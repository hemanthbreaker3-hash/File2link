# HITLER — Telegram Media Link Service

Telegram bot and FastAPI web application for creating direct download and browser-streaming links for media messages.

**Telegram:** https://t.me/HiTLERYT  
**Username:** @hitleryt

## Features

- Telegram bot file intake and generated download/watch links.
- HTTP byte-range streaming for browser seeking and downloads.
- Optional FFmpeg stream-copy remux for audio-track selection.
- Android external-player intents for VLC, MX Player, Just Player, mpv and Kodi, plus a copyable stream URL.
- Browser-only Web Audio volume amplification up to 300% and touch controls (right side: volume; left side: brightness).
- Persistent `/baseurl` command for the bot owner and configured admins.
- MongoDB-backed file metadata and settings.
- Docker Compose VPS deployment with MongoDB, Redis, health checks and restart policies.

> Volume amplification and brightness adjustments apply to the built-in browser player. External players use their own volume and display controls. Device-level brightness APIs are not consistently exposed to mobile browsers.

## Requirements

- Linux VPS with Docker Engine and Docker Compose plugin.
- Public HTTPS domain pointing to the VPS (recommended for reliable Android intents and browser playback).
- Telegram API ID/hash from `my.telegram.org`, bot token from BotFather, and the Telegram numeric owner ID.

## VPS deployment

### One-command style (Git clone + exported credentials)

Clone the repository, enter its directory, export your own File2link credentials, then run `deploy.vps`. The script automatically writes the exported values into a private `.env` file, validates them, builds the Docker image, starts the stack, and waits for the health endpoint.

```bash
rm -rf file2link
git clone YOUR_FILE2LINK_GITHUB_REPO file2link
cd file2link

export API_ID="YOUR_API_ID"
export API_HASH="YOUR_API_HASH"
export BOT_TOKEN="YOUR_BOT_TOKEN"
export OWNER_ID="YOUR_TELEGRAM_USER_ID"
export ADMINS=""
export CHANNEL_ID=""
export FORCE_SUB_CHANNELS=""
export MONGODB_URI="YOUR_MONGODB_URI"
export DATABASE_NAME="file2link"

ls deploy.vps
chmod +x deploy.vps
./deploy.vps
```

Replace the placeholders with credentials for this bot and the actual GitHub URL. Do not reuse credentials from another bot. Never commit `.env` or publish live tokens/passwords. The deployment script supports exported variables, `config.env`, or `.env` (precedence: exported shell variables, then `config.env`, then existing `.env`). It also accepts `APP_ID` as an alias for `API_ID`, `DATABASE_URI`/`DATABASE_URL` for `MONGODB_URI`, and `LOG_CHANNEL` for `CHANNEL_ID`.

Alternatively, after exporting the same variables in your current shell, use the included helper:

```bash
chmod +x deploy-from-git.sh
./deploy-from-git.sh YOUR_FILE2LINK_GITHUB_REPO file2link
```

The helper refuses to overwrite an existing directory. If you intentionally want a clean clone, remove that directory first.

The script requires Docker Engine and Docker Compose (it installs Docker on supported Linux VPS systems when missing). By default, Compose starts local MongoDB and Redis containers. If `MONGODB_URI` points to MongoDB Atlas, the app uses that external database URI; the bundled MongoDB service remains available but is not the configured application database. Verify the deployment with:

```bash
docker compose ps
docker compose logs --tail=100 web
curl -fsS http://127.0.0.1:8000/health
```

The health check confirms the web process is responding; it does not prove Telegram authorization, Atlas network access, DNS, firewall, TLS, or end-to-end playback.

## Configuration

| Variable | Required | Description |
|---|---:|---|
| `API_ID` | Yes | Telegram API ID |
| `API_HASH` | Yes | Telegram API hash |
| `BOT_TOKEN` | Yes | Telegram bot token |
| `OWNER_ID` | Yes | Numeric Telegram owner ID |
| `ADMINS` | No | Comma-separated admin Telegram IDs |
| `MONGODB_URI` | Yes | MongoDB connection string |
| `DATABASE_NAME` | No | MongoDB database name; defaults to `tg_media_bot` |
| `REDIS_URL` | No | Redis URL; Compose default is `redis://redis:6379/0` |
| `BASE_URL` | Yes in production | Public base URL, including `https://` |
| `DEFAULT_EXPIRY` | No | Link lifetime in hours; `0` disables expiry |
| `SESSIONS` | No | Comma-separated Telethon StringSession values |
| `ADMIN_PANEL_TOKEN` | No | Enables `/admin` HTTP Basic auth; username is `admin` |
| `STREAM_CHUNK_SIZE` | No | Stream read size from 64 KiB to 1 MiB |

The admin panel is disabled unless `ADMIN_PANEL_TOKEN` is set. Do not expose an unprotected admin dashboard to the public internet.

## Bot commands

- `/start` — start the bot.
- `/baseurl` — show the current public base URL.
- `/baseurl https://your-domain.example` — validate, persist and activate a new base URL.
- `/stats` — show basic bot statistics (owner/admin only).

The base URL is stored in MongoDB and loaded at application startup. Newly generated links use the updated value without editing `.env` or restarting the service.

## Streaming and player notes

- Browser streaming supports standard single byte ranges and returns `206 Partial Content` for valid range requests; invalid ranges return `416`.
- The streaming engine uses ordered, bounded-memory reads to avoid out-of-order/corrupted media. Multiple Telegram sessions are supported for session selection, but the media reference is downloaded through the same session that fetched it.
- The `/remux/{short_code}?audio=N` endpoint requires FFmpeg and performs stream-copy remuxing; seeking is not available in remux mode.
- Android external-player buttons use Android VIEW intents with the media MIME type. If a package is missing or an intent is blocked, copy the stream URL into the installed player's network-stream dialog.
- Browser volume boost uses Web Audio and may be unavailable in browsers that block audio processing. High gain can cause distortion; keep the level at the lowest useful setting.

## Reverse proxy

Put the app behind Nginx, Caddy or another TLS reverse proxy. Forward requests to `127.0.0.1:8000`, preserve the `Range` and `Host` headers, and allow long-lived streaming responses. Do not buffer large streaming responses at the proxy.

## Automatic public HTTPS URL with Cloudflare Quick Tunnel

`./deploy.vps` now installs `cloudflared` automatically after the web health check and creates a systemd service named `file2link-cloudflared`. The deployment waits for Cloudflare to issue a `https://*.trycloudflare.com` URL, writes it to `.env`, stores it in MongoDB (`settings` collection, key `base_url`), and recreates the web container so the bot immediately uses it for newly generated download and watch buttons.

The service starts again after VPS reboot. If Cloudflare issues a different Quick Tunnel URL after a reboot or service restart, the helper automatically updates `.env` and MongoDB and restarts the web container to apply the new URL.

Useful commands:

```bash
# Show the current generated URL
sudo grep '^BASE_URL=' .env

# Follow tunnel logs / troubleshoot URL generation
sudo journalctl -u file2link-cloudflared -f

# Restart the tunnel (a new temporary URL may be generated)
sudo systemctl restart file2link-cloudflared

# Check service status
sudo systemctl status file2link-cloudflared --no-pager
```

Quick Tunnels are temporary and intended for testing: their URL can change after a restart, and Cloudflare does not provide an uptime guarantee for them. The File2link service automatically synchronizes the new URL, but links already shared with users using an old URL will not be rewritten. For production, use a named Cloudflare Tunnel with a hostname on your own domain.

## Maintenance

```bash
docker compose logs -f web
docker compose restart web
docker compose pull mongodb redis
docker compose up -d --build
```

MongoDB data is stored in the `mongodb_data` Docker volume. Back it up before upgrades or migrations.

## Testing and verification

Run locally in an environment with dependencies installed:

```bash
python -m compileall -q app workers gen_session.py
pytest -q
```

For a production acceptance test, additionally verify bot authentication, uploading a test file, generated link opening, HTTP range seeking, full download, Android player launches, audio-track remuxing, `/baseurl` persistence after restart, and concurrent playback from separate devices. A successful image build or `/health` response alone does not prove every Telegram or client-device feature works.

## Public Base URL and Telegram file buttons

Set `BASE_URL` to a publicly reachable URL before users upload files. For Cloudflare Quick Tunnel, run `cloudflared tunnel --url http://127.0.0.1:8000`, then send the generated URL to the bot with `/baseurl https://YOUR-NAME.trycloudflare.com`. The bot validates the URL and uses it for the Direct Download (`/dl/`) and Watch Online (`/watch/`) buttons attached to the uploaded file message. `localhost` and private IP addresses are intentionally rejected because Telegram users cannot reach them.

The update-channel buttons point to `https://t.me/HiTLERYT`. `CHANNEL_ID` remains a numeric Telegram chat ID for private logging/file mirroring; it must not be replaced with a public URL.
