# Anizoneflix

A lightweight media-link and streaming service with a Telegram bot, browser player, multi-session streaming, audio-track remuxing, MongoDB and optional Redis.

**Official:** https://t.me/anizoneflix  
**Handle:** @anizoneflix

## What changed

- Clean, dependency-light web UI for low-end phones.
- Removed Tailwind, Font Awesome, Google Fonts, HTMX and other heavy frontend dependencies.
- Native HTML5 video controls instead of a large player framework.
- Added `/health` for Render, Koyeb, Heroku and container health checks.
- Added expiry enforcement, cached track discovery, access counting and safer HTTP range handling.
- Fixed clean shutdown for all active clients.
- Docker image uses Python 3.12 slim, FFmpeg and a non-root runtime user.
- Docker listens on the platform-provided `PORT`.
- VPS Compose uses internal MongoDB and Redis networking.
- Admin panel now requires HTTP Basic Authentication.
- Removed credentials and secrets from the example environment file.
- Stable container tags and persistent database volumes.
- Safer `.dockerignore`.
- Rewritten deployment documentation.

> No deployment system can honestly guarantee 100% success with invalid credentials, unavailable databases, provider restrictions or account limits. This project uses one portable Docker image and documents the required environment for each provider.

## Required environment

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
OWNER_ID=123456789

BASE_URL=https://your-public-domain.example
PORT=8000

MONGODB_URI=mongodb+srv://...
REDIS_URL=redis://...
SESSIONS=

ADMIN_USERNAME=admin
ADMIN_PASSWORD=use-a-long-random-password

ADMINS=
FORCE_SUB_CHANNELS=
CHANNEL_ID=
DEFAULT_EXPIRY=24
DEBUG=false
```

Never commit `.env` or real credentials.

## Docker: local / VPS / any Docker host

Build:

```bash
docker build -t anizoneflix .
```

Run:

```bash
docker run -d   --name anizoneflix   --restart unless-stopped   -p 8000:8000   --env-file .env   anizoneflix
```

Check:

```bash
curl http://127.0.0.1:8000/health
docker logs -f anizoneflix
```

The application listens on `0.0.0.0:${PORT}`. Docker hosts can therefore inject their own port.

## VPS with MongoDB + Redis

Install Docker and Docker Compose, then:

```bash
cp .env.example .env
nano .env
docker compose up -d --build
docker compose ps
docker compose logs -f web
```

Open:

```text
http://YOUR_SERVER_IP:8000
```

For a reverse proxy, point your HTTPS domain to port `8000`.

### Update

```bash
git pull
docker compose up -d --build
docker image prune -f
```

### Stop

```bash
docker compose down
```

Data is stored in the named MongoDB and Redis volumes.

## Render

Create a **Web Service** from the repository.

- Runtime: Docker
- Dockerfile: `Dockerfile`
- No fixed port is required; the app reads `PORT`.
- Add all required environment variables from `.env.example`.
- Use a managed MongoDB-compatible database and Redis-compatible service.
- Set `BASE_URL` to the public Render URL after deployment if needed.

Health check path:

```text
/health
```

Do not use the VPS `docker-compose.yml` as the Render deployment definition.

## Koyeb

Create an App/Service from the repository and select **Dockerfile**.

- Dockerfile: `Dockerfile`
- HTTP port: `8000` or the provider's configured container port.
- Add the required environment variables.
- Use external MongoDB and Redis.
- Health endpoint: `/health`.

The container automatically honors the `PORT` environment variable.

## Heroku Container Registry

Heroku does not run `docker-compose.yml` as the application runtime. Use the Docker image as the web process.

```bash
heroku login
heroku create your-app-name

heroku container:login
docker build -t registry.heroku.com/YOUR_APP_NAME/web .
docker tag registry.heroku.com/YOUR_APP_NAME/web:latest registry.heroku.com/YOUR_APP_NAME/web
docker push registry.heroku.com/YOUR_APP_NAME/web

heroku container:release web -a YOUR_APP_NAME
```

Set configuration:

```bash
heroku config:set API_ID=... API_HASH=... BOT_TOKEN=... OWNER_ID=... -a YOUR_APP_NAME
heroku config:set MONGODB_URI=... REDIS_URL=... ADMIN_USERNAME=admin ADMIN_PASSWORD=... -a YOUR_APP_NAME
```

Heroku supplies `PORT`; the Docker command already uses it.

## Other Docker platforms

The same `Dockerfile` is intended for platforms that accept OCI/Docker images, including VPS Docker hosts and managed container services.

General requirements:

1. Build from `Dockerfile`.
2. Provide required environment variables.
3. Provide reachable MongoDB.
4. Provide Redis if rate limiting/cache needs shared state.
5. Expose the HTTP service on the platform-provided port.
6. Set `BASE_URL` to the public HTTPS URL.

## Health and diagnostics

```bash
curl -fsS http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok","service":"anizoneflix"}
```

If the health endpoint fails, inspect:

```bash
docker logs --tail 200 anizoneflix
```

For Compose:

```bash
docker compose logs --tail 200 web
docker compose ps
```

Common causes:

- Invalid `API_ID`, `API_HASH` or `BOT_TOKEN`.
- MongoDB URI is unreachable.
- Redis URI is unreachable.
- `BASE_URL` is wrong.
- Provider blocks long-running connections or outbound access.
- Required bot permissions are missing.

## Security

- Keep `.env` private.
- Use a long random `ADMIN_PASSWORD`.
- Do not expose MongoDB or Redis ports publicly.
- Put the service behind HTTPS in production.
- Rotate any credential that was previously committed or shared.

## License

MIT.
