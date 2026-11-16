# HITLER YT — Telegram Media Bot & OTT Website

A Telegram media-link bot and streaming backend with a blue, responsive HITLER YT website, MongoDB-backed catalog, TMDB publishing, and a Vercel gateway.

> **Deployment architecture:** Vercel hosts the public website domain/gateway. The Telegram bot, MongoDB access, and high-bandwidth streaming engine require a persistent Python backend (recommended: VPS/Docker). Vercel serverless functions are not a replacement for a continuously running Telegram bot or large media streams. Deploying the repository root to Vercel is supported, but `BACKEND_URL` must point to the running backend.

## Features

- Telegram bot media link generation, force-subscription checks, rate limits, media information, screenshots, sample video, deletion, admin stats and broadcast tools.
- FastAPI backend with Telegram media streaming, byte-range support, track discovery and remux endpoints.
- Responsive blue HITLER YT website and watch page.
- MongoDB-backed runtime settings and title catalog.
- Basic-auth protected `/panel` dashboard and `/admin` dashboard.
- Download, streaming, built-in player and best-effort Android VLC handoff settings.
- All-users/admins-only/owners-only access mode setting.
- TMDB search and one-click page publishing from Telegram `/search` and the admin dashboard.
- `/categories`, `/edit`, and `/panel` admin commands link to the secure web CMS.
- Default Movies and Web Series categories; custom categories can be created and deleted (default categories are protected).
- Movie/series pages support metadata, categories, groups and multiple buttons per group through the catalog API.

## 1. Configure the backend

Copy `.env.example` to `.env` and configure at minimum:

```env
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_bot_token
OWNER_ID=your_telegram_user_id
ADMINS=123456789,987654321
MONGODB_URI=mongodb+srv://...
DATABASE_NAME=tg_media_bot
BASE_URL=https://your-project.vercel.app
ADMIN_PANEL_TOKEN=use-a-long-random-secret
TMDB_API_KEY=your_tmdb_v3_api_key
```

`ADMIN_PANEL_TOKEN` protects the website dashboard using HTTP Basic authentication (username: `admin`). The VPS deploy script generates a strong token if one is not set; retrieve it from the private backend `.env` file. Never commit `.env` or expose the token in client-side code. TMDB search is disabled until `TMDB_API_KEY` is configured.

Optional settings are documented in `.env.example`. MongoDB must be reachable from the backend host. Redis is optional for deployments that do not use the cache integration.

## 2. Deploy the backend (VPS / Docker)

The bot and streaming backend must remain online. On a Linux VPS with Docker and Docker Compose installed:

```bash
cp .env.example .env
nano .env

docker compose up -d --build
```

Check the backend:

```bash
curl http://127.0.0.1:8000/health
```

Put the backend behind HTTPS using a reverse proxy or Cloudflare Tunnel. Set the resulting public backend URL as `BACKEND_URL` in Vercel. Ensure the reverse proxy permits streaming responses and HTTP Range requests.

Alternatively, install Python dependencies and run `uvicorn app.main:app --host 0.0.0.0 --port 8000`. This is suitable for development; use a process supervisor for production.

## 3. Deploy the website gateway to Vercel (repository root)

1. Import this repository at [Vercel](https://vercel.com/new).
2. Set **Root Directory** to the repository root (do not select `vercel/`).
3. Add the environment variable `BACKEND_URL`, e.g. `https://api.your-domain.com`.
4. Deploy. No custom build command is required; the root `vercel.json` routes requests through the Node.js proxy in `api/[...path].js`.
5. Set the bot's public URL to the Vercel domain using `BASE_URL` or `/baseurl https://your-project.vercel.app`.

The Vercel gateway redirects `/stream`, `/dl`, and `/remux` traffic to the backend so media bytes do not pass through a serverless function. Other requests (home page, watch page, static files, catalog and admin API) are proxied to the backend.

If `BACKEND_URL` is missing or unreachable, the Vercel deployment will return a clear configuration/502 error. This is expected until the backend is deployed and reachable.

## 4. Admin dashboard and commands

Open `https://your-project.vercel.app/panel` and authenticate with username `admin` and the `ADMIN_PANEL_TOKEN` password.

- Toggle downloads and streaming independently.
- Toggle the built-in watch-page player.
- Toggle best-effort Android VLC auto handoff.
- Select the configured access mode.
- Create/delete custom categories.
- Search TMDB and publish movie/series pages. Movie pages are assigned to Movies; TV pages to Web Series.

Telegram admin commands:

- `/search title` — search TMDB and select a result to publish.
- `/categories` — open category management guidance.
- `/edit` — open page editor guidance.
- `/panel` — dashboard link.
- `/baseurl https://domain` — update generated public links.

Only Telegram IDs in `ADMINS` or the `OWNER_ID` can use these CMS commands. The web dashboard uses its separate Basic-auth secret.

## 5. Catalog API

- `GET /api/catalog` — published catalog.
- `GET /api/catalog?q=salaar` — title search.
- `GET /api/catalog?category=movies` — category filter.
- `GET /title/{slug}` — dedicated title page.
- `GET /api/site-settings` — public playback settings.
- `GET /api/admin/categories` — protected category list.
- `POST /api/admin/categories` — protected category creation.
- `DELETE /api/admin/categories/{slug}` — protected category deletion.
- `GET /api/admin/tmdb-search?q=title` — protected TMDB search.
- `POST /api/admin/pages` and `PUT /api/admin/pages/{slug}` — protected page publishing/editing.
- `PUT /api/admin/settings` — protected runtime settings.

All CMS writes are stored in MongoDB and survive process restarts. The page data model includes `groups: [{name, buttons: [{name, url}]}]`; keep button URLs absolute HTTP(S) URLs or relative same-site paths. Internal absolute links are rendered against the current forwarded website domain, so changing the Vercel domain does not require rewriting every internal button. Do not enter JavaScript URLs.

## 6. Player behavior and limitations

The built-in player can be hidden while external-player options remain on the Watch Online page. Auto handoff is best-effort: browsers do not expose a reliable API for enumerating installed external players. The current implementation attempts Android's VLC intent and uses the normal page as browser fallback. On iOS and desktop it leaves the normal page in place rather than claiming to detect an installed app it cannot verify.

## 7. Development and tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

For a local backend, set the environment variables above before starting the app. `/health` is the health-check endpoint.

## Security notes

- Keep bot tokens, API hashes, database URIs, TMDB keys and admin secrets in environment variables.
- Use HTTPS for both Vercel and the backend.
- Restrict backend ingress to trusted infrastructure where possible.
- Use a strong `ADMIN_PANEL_TOKEN`; rotate it if it has ever been shared.
- Configure `ADMINS` and `OWNER_ID` with numeric Telegram IDs.
- TMDB metadata is provided by TMDB; attribution and applicable TMDB terms should be followed.
