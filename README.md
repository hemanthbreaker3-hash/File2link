# ANIZONEFLIX

> Fast, clean Telegram media streaming and direct-link service.

Official branding:
- **ANIZONEFLIX**
- **https://t.me/anizoneflix**
- **@anizoneflix**

## Features

- Direct Telegram media streaming with HTTP range support.
- Browser video playback with a lightweight, mobile-first UI.
- Download and watch links.
- Audio-track discovery with cached probe results.
- FFmpeg stream-copy remux for selecting another audio track.
- External-player menus separated into **Mobile, Desktop, Tablet and TV**.
- Android `VIEW` intents with explicit package names for supported players.
- Desktop shortcuts for VLC and PotPlayer plus a universal Open URL option.
- Copy-stream-URL fallback for every platform.
- Low-end friendly UI: no Tailwind runtime, no Font Awesome runtime, no glassmorphism, no unnecessary animations.
- Telegram bot with user registration, force-subscription checks, rate limiting and admin tools.
- MongoDB persistence.
- Redis rate-limit acceleration with graceful in-memory fallback.
- Multi-session Telethon streaming support.
- Docker-first deployment.
- Works with Docker, Docker Compose, VPS, Render, Koyeb and Heroku container deployments.

## Important deployment note

No application can honestly guarantee a **100% successful deployment on every provider** because provider networking, credentials, external databases, Telegram limits and platform policies are outside the Docker image.

This project is designed so the **same Dockerfile** is used on VPS, Render, Koyeb, Heroku Container Registry and other Docker-capable hosts. The application listens on the provider's `$PORT`.

For cloud platforms that provide only one web container, use an **external MongoDB** service. Redis is recommended but not mandatory because the rate limiter has a memory fallback.

---

# 1. Configuration

Copy the template:

```bash
cp .env.example .env
```

Required:

```env
API_ID=YOUR_API_ID
API_HASH=YOUR_API_HASH
BOT_TOKEN=YOUR_BOT_TOKEN
OWNER_ID=YOUR_TELEGRAM_USER_ID
```

Recommended:

```env
MONGODB_URI=mongodb+srv://...
REDIS_URL=redis://...
BASE_URL=https://your-public-domain.example
DEFAULT_EXPIRY=24
SESSIONS=
```

### BASE_URL

Set this to the public HTTPS URL that users will open.

Example:

```env
BASE_URL=https://stream.example.com
```

Do not put a private VPS address in a public frontend configuration.

### Security

Never commit `.env`, Telegram bot tokens, API hashes, MongoDB credentials or Telethon sessions.

If credentials were ever committed to a repository or shared publicly, rotate them before production use.

---

# 2. Local Docker Compose

Requirements:

- Docker
- Docker Compose

Start:

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
```

Open:

```text
http://SERVER_IP:8000
```

Check:

```bash
docker compose ps
docker compose logs -f web
```

Stop:

```bash
docker compose down
```

Data is stored in Docker volumes for MongoDB and Redis.

---

# 3. VPS Deployment

Ubuntu/Debian VPS:

```bash
git clone YOUR_REPOSITORY_URL
cd YOUR_REPOSITORY_DIRECTORY
cp .env.example .env
nano .env
chmod +x deploy.vps
./deploy.vps
```

The deployer:

1. Installs Docker if required.
2. Starts Docker.
3. Detects `docker compose` or `docker-compose`.
4. Checks required Telegram variables.
5. Uses Docker service names for MongoDB and Redis.
6. Builds the application.
7. Starts MongoDB, Redis and ANIZONEFLIX.
8. Waits for `/health`.
9. Prints container status and logs on failure.

Useful commands:

```bash
docker compose ps
docker compose logs -f web
docker compose restart
docker compose up -d --build
docker compose down
```

### Reverse proxy / HTTPS

For production, put ANIZONEFLIX behind Nginx, Caddy or Cloudflare Tunnel and set:

```env
BASE_URL=https://your-public-domain.example
```

Keep the internal application port private when possible.

---

# 4. Render

Render can build this repository directly from the root `Dockerfile`.

### Steps

1. Create a new **Web Service**.
2. Connect the repository.
3. Select **Docker** as the runtime.
4. Keep the Dockerfile path as:

```text
./Dockerfile
```

5. Add environment variables:

```text
API_ID
API_HASH
BOT_TOKEN
OWNER_ID
MONGODB_URI
REDIS_URL
BASE_URL
DEFAULT_EXPIRY
SESSIONS
```

6. Deploy.

Render supplies `PORT`; the Docker entrypoint automatically uses it.

### Database

Do not run MongoDB inside the Render web container. Use MongoDB Atlas or another reachable MongoDB service.

Redis is optional. If Redis is unavailable, rate limiting falls back to process memory.

### BASE_URL

After Render gives you an HTTPS URL, set:

```env
BASE_URL=https://your-service.onrender.com
```

Redeploy after changing environment variables.

---

# 5. Koyeb

Use the same root Dockerfile.

### Steps

1. Create a Koyeb App.
2. Choose deployment from GitHub or container.
3. Select the repository.
4. Use the root `Dockerfile`.
5. Add the required environment variables.
6. Set the service port to the same port exposed by the service (normally `8000`; Koyeb can also provide its own `PORT`).
7. Deploy.

Environment:

```text
API_ID=...
API_HASH=...
BOT_TOKEN=...
OWNER_ID=...
MONGODB_URI=...
REDIS_URL=...
BASE_URL=https://your-koyeb-domain.example
DEFAULT_EXPIRY=24
SESSIONS=
```

The Docker command reads `$PORT`, so the image does not require a hard-coded cloud port.

---

# 6. Heroku

Heroku supports container deployment. A Heroku web dyno cannot run the complete Docker Compose stack, so use external MongoDB and Redis.

## Option A — Heroku Container Registry

Install and authenticate the Heroku CLI, then:

```bash
heroku login
heroku container:login
heroku create YOUR-APP-NAME
```

Set configuration:

```bash
heroku config:set   API_ID="YOUR_API_ID"   API_HASH="YOUR_API_HASH"   BOT_TOKEN="YOUR_BOT_TOKEN"   OWNER_ID="YOUR_TELEGRAM_USER_ID"   MONGODB_URI="YOUR_MONGODB_URI"   REDIS_URL="YOUR_REDIS_URL"   BASE_URL="https://YOUR-APP-NAME.herokuapp.com"   DEFAULT_EXPIRY="24"   --app YOUR-APP-NAME
```

Build and release:

```bash
heroku container:push web --app YOUR-APP-NAME
heroku container:release web --app YOUR-APP-NAME
heroku logs --tail --app YOUR-APP-NAME
```

Heroku provides `$PORT`; the container uses it automatically.

## Option B — Heroku `heroku.yml`

This repository includes `heroku.yml` for Docker-based Heroku workflows.

Use the container stack and deploy according to the current Heroku CLI flow for your account.

---

# 7. Any Docker Host

The root `Dockerfile` is the canonical production image.

Build:

```bash
docker build -t anizoneflix .
```

Run:

```bash
docker run -d   --name anizoneflix   -p 8000:8000   --env-file .env   -e PORT=8000   --restart unless-stopped   anizoneflix
```

Check:

```bash
curl http://127.0.0.1:8000/health
docker logs -f anizoneflix
```

For a platform that injects `$PORT`, expose and publish the port required by that platform.

---

# 8. Player System

The watch page has four lightweight menus:

### Mobile

- VLC
- MX Player
- Just Player
- mpv
- Kodi
- Copy URL

### Tablet

- VLC
- MX Player
- Just Player
- Kodi

### Desktop

- VLC
- PotPlayer
- Open URL
- Copy URL

### TV

- VLC
- Kodi
- Just Player
- Open URL

Android buttons use:

```text
android.intent.action.VIEW
```

with explicit application packages and:

```text
type=video/*
```

If a player has changed its Android package, is not installed, or does not accept external HTTP streams, the universal **Copy URL** option remains available.

For iOS/macOS applications such as Infuse, use the copied stream URL or the application's Network/Open URL feature. Custom URL schemes vary by app version and are intentionally not used as the only playback path.

---

# 9. Browser Player

The browser player uses the native HTML5 `<video>` element.

This keeps the watch page small and fast on low-end devices.

The stream endpoint supports HTTP byte ranges so compatible browsers can seek without downloading the complete file first.

The download endpoint uses:

```text
/download-style attachment
```

while browser streaming uses:

```text
/stream-style inline
```

---

# 10. Audio Tracks

The server uses FFprobe to discover:

- video tracks
- audio tracks
- subtitle tracks
- language
- codec
- channel layout

Track information is cached in MongoDB after a successful probe.

Selecting another audio track uses FFmpeg stream-copy remuxing:

```text
Telegram → FFmpeg → fragmented MP4 → browser
```

No video transcoding is performed.

Because remuxing reads the source sequentially, seeking may be more limited than direct playback.

---

# 11. Telegram Sessions

A bot session is used automatically when no user sessions are configured.

For higher throughput, add Telethon StringSessions:

```env
SESSIONS=session_string_1,session_string_2
```

Do not publish these values.

Generate a session with:

```bash
python gen_session.py
```

The machine must have the required Telegram API credentials.

---

# 12. Health Check

The application exposes:

```text
GET /health
```

Example:

```json
{"status":"ok","service":"anizoneflix"}
```

Docker uses this endpoint for its health check.

---

# 13. Troubleshooting

### Container starts then stops

```bash
docker compose logs --tail=150 web
```

Check:

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`
- `MONGODB_URI`
- Telegram network access

### MongoDB connection error

For Docker Compose:

```env
MONGODB_URI=mongodb://mongodb:27017/tg_media_bot
```

For Render/Koyeb/Heroku, use an external MongoDB URI.

Do not use `localhost` for MongoDB from inside the web container unless MongoDB is running in that same container.

### Redis connection error

Docker Compose:

```env
REDIS_URL=redis://redis:6379/0
```

Cloud:

```env
REDIS_URL=your-external-redis-url
```

The application can continue with its in-memory rate-limit fallback when Redis is unavailable.

### Browser cannot play a file

Some containers/codecs are not browser-compatible. Try:

1. Open the stream in VLC/Kodi.
2. Use the Download button.
3. Select another audio track if available.
4. Use Copy URL and paste it into a player network-stream dialog.

### External player does nothing

Make sure the application is installed and supports HTTP/HTTPS network playback. Android intent behavior is controlled by Android and the installed application; no website can force an app to accept a URL.

---

# 14. Updating

VPS:

```bash
git pull
./deploy.vps
```

Generic Docker:

```bash
git pull
docker build -t anizoneflix .
docker compose up -d --build
```

---

# 15. Project Layout

```text
.
├── app/
│   ├── admin/
│   ├── bot/
│   ├── cache/
│   ├── database/
│   ├── models/
│   ├── streamer/
│   ├── templates/
│   └── utils/
├── docker/
├── netlify/
├── vercel/
├── workers/
├── Dockerfile
├── docker-compose.yml
├── deploy.vps
├── heroku.yml
├── Procfile
├── requirements.txt
└── .env.example
```

---

# 16. Branding

The web interface uses only:

**ANIZONEFLIX**

**https://t.me/anizoneflix**

**@anizoneflix**

No unrelated project names, logos or branding are used by the main UI.

---

## License

Use and modify this project according to the license included with your distribution.
