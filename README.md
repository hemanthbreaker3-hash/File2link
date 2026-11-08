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

1. Extract this project on the VPS and enter the project directory.
2. Fill in your credentials in `config.env` (API_ID, API_HASH, BOT_TOKEN, OWNER_ID, BASE_URL, etc.):

   ```bash
   nano config.env
   ```

   `config.env` is a short, dedicated place to fill in just your credentials. You don't need to touch `.env` by hand — `./deploy.vps` reads `config.env` and writes any filled-in values into `.env` for you each time it runs (creating `.env` from `.env.example` first if it doesn't exist yet). Leaving a field blank in `config.env` leaves the existing `.env` value untouched. If you'd rather edit `.env` directly instead, that still works too: `cp .env.example .env && nano .env`.

   If you don't have `BASE_URL` yet, leave it blank for now — see [Get a public HTTPS URL with Cloudflare Tunnel](#get-a-public-https-url-with-cloudflare-tunnel) below, or set it later with the bot's `/baseurl` command once it's running.

3. Keep `MONGODB_URI=mongodb://mongodb:27017` and `DATABASE_NAME=tg_media_bot` when using the bundled Compose database. Configure `ADMINS` as comma-separated Telegram numeric IDs if needed.
4. Start the stack:

   ```bash
   chmod +x deploy.vps
   ./deploy.vps
   ```

5. Verify the service:

   ```bash
   docker compose ps
   docker compose logs --tail=100 web
   curl -fsS http://127.0.0.1:8000/health
   ```

   A healthy response is `{"status":"ok","service":"HITLER"}`. Use `PORT` if you configured a different port.

The deployment script checks required Telegram settings, builds the image, starts the services, and waits for the health endpoint. It cannot verify your Telegram credentials, DNS, firewall, TLS proxy, or actual playback from a remote Android device; test those separately.

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

## Get a public HTTPS URL with Cloudflare Tunnel

If you don't have a domain yet, or just want a quick HTTPS URL to put in `BASE_URL` for testing, [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (`cloudflared`) can expose the local service without any DNS setup or open ports.

**Quick tunnel (fastest, temporary URL):**

1. Install `cloudflared` on the VPS:

   ```bash
   curl -fsSL https://pkg.cloudflare.com/cloudflared-ascii.sh | sudo bash 2>/dev/null; \
   sudo apt-get update -qq && sudo apt-get install -y cloudflared
   ```

   (See [Cloudflare's install docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) if your distro isn't Debian/Ubuntu-based.)

2. With the stack already running (`./deploy.vps`), start a quick tunnel pointed at the app's port:

   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```

3. `cloudflared` prints a random URL that looks like `https://random-words-1234.trycloudflare.com`. That's your public HTTPS URL.
4. Set it as `BASE_URL` — either update `config.env`/`.env` and rerun `./deploy.vps`, or just send `/baseurl https://random-words-1234.trycloudflare.com` to the bot (no restart needed; see [Bot commands](#bot-commands)).

Quick tunnels are meant for testing: the URL changes every time you restart `cloudflared`, and there's no uptime guarantee. Keep the `cloudflared tunnel --url ...` command running (e.g. in `tmux`, `screen`, or as a `systemd` service) for as long as you want the URL to stay valid.

**Named tunnel (stable URL tied to your own domain):**

If you own a domain on Cloudflare and want a URL that doesn't change:

1. Authenticate and create a named tunnel:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create file2link
   ```

2. Route a hostname on your domain to it:

   ```bash
   cloudflared tunnel route dns file2link stream.yourdomain.com
   ```

3. Run it pointed at the app:

   ```bash
   cloudflared tunnel --url http://localhost:8000 run file2link
   ```

4. Set `BASE_URL=https://stream.yourdomain.com` and rerun `./deploy.vps` (or set it via `/baseurl`).

Either way, Cloudflare terminates TLS and proxies to the VPS, so you don't need Nginx/Caddy or a separate certificate for this path — only use the [Reverse proxy](#reverse-proxy) section above if you're not using Cloudflare Tunnel.

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
