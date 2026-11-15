#!/usr/bin/env bash
# Managed by deploy.vps. Starts a Cloudflare Quick Tunnel and synchronizes its
# generated HTTPS URL into .env and MongoDB so the bot uses it after restarts.
set -Eeuo pipefail

SCRIPT_DIR="${FILE2LINK_DIR:-$(pwd)}"
if [[ ! -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fi
cd "$SCRIPT_DIR"
PORT_VALUE="${PORT:-8000}"
LOG_FILE="$SCRIPT_DIR/cloudflared-quick-tunnel.log"
COMPOSE=(docker compose --project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml" --env-file "$SCRIPT_DIR/.env")

log() { printf '[File2link Cloudflare] %s\n' "$*"; }

if [[ ! -x /usr/local/bin/cloudflared ]]; then
  log 'cloudflared binary is missing; the deployment installer must install it first.'
  exit 1
fi

# Wait until the web service is available before exposing it publicly.
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${PORT_VALUE}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "http://127.0.0.1:${PORT_VALUE}/health" >/dev/null || {
  log 'File2link health endpoint is not ready; refusing to start the public tunnel.'
  exit 1
}

: > "$LOG_FILE"
log "Starting Quick Tunnel to http://127.0.0.1:${PORT_VALUE}"
/usr/local/bin/cloudflared tunnel --no-autoupdate --loglevel info --url "http://127.0.0.1:${PORT_VALUE}" >"$LOG_FILE" 2>&1 &
CF_PID=$!
cleanup() {
  if kill -0 "$CF_PID" 2>/dev/null; then kill "$CF_PID" 2>/dev/null || true; fi
  wait "$CF_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

TUNNEL_URL=''
for _ in $(seq 1 90); do
  if ! kill -0 "$CF_PID" 2>/dev/null; then
    log 'cloudflared exited before creating a tunnel.'
    tail -n 80 "$LOG_FILE" || true
    exit 1
  fi
  TUNNEL_URL="$(grep -Eo 'https://[[:alnum:]-]+\.trycloudflare\.com' "$LOG_FILE" | tail -n 1 || true)"
  if [[ -n "$TUNNEL_URL" ]]; then break; fi
  sleep 2
done

if [[ -z "$TUNNEL_URL" ]]; then
  log 'Timed out waiting for the trycloudflare.com URL. Recent cloudflared output:'
  tail -n 100 "$LOG_FILE" || true
  exit 1
fi

log "Generated public URL: $TUNNEL_URL"

# Update .env without sourcing it as shell code; preserve all other settings.
python3 - "$SCRIPT_DIR/.env" "$TUNNEL_URL" <<'PY'
import json, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
url = sys.argv[2]
lines = path.read_text().splitlines() if path.exists() else []
new_lines = []
found = False
for line in lines:
    if re.match(r'^\s*BASE_URL\s*=', line):
        if not found:
            new_lines.append('BASE_URL=' + json.dumps(url))
            found = True
    else:
        new_lines.append(line)
if not found:
    new_lines.append('BASE_URL=' + json.dumps(url))
path.write_text('\n'.join(new_lines) + '\n')
path.chmod(0o600)
PY

# Persist the URL in MongoDB too: app startup loads this setting and gives it
# priority over environment defaults. This makes the bot use the new URL.
if ! "${COMPOSE[@]}" exec -T web python -c '
import asyncio, datetime, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
    uri = os.environ.get("MONGODB_URI") or os.environ.get("DATABASE_URL")
    if not uri:
        raise RuntimeError("MONGODB_URI/DATABASE_URL is missing inside web container")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=15000)
    await client.admin.command("ping")
    db = client[os.environ.get("DATABASE_NAME", "file2link")]
    await db.settings.update_one(
        {"key": "base_url"},
        {"$set": {"value": sys.argv[1], "updated_by": "deployment", "updated_at": datetime.datetime.utcnow()}},
        upsert=True,
    )
    client.close()
asyncio.run(main())
' "$TUNNEL_URL"; then
  log 'Could not persist BASE_URL in MongoDB. Check MongoDB connectivity; refusing to report deployment as fully configured.'
  exit 1
fi

# Recreate the web container so its environment also reflects BASE_URL.
"${COMPOSE[@]}" up -d --force-recreate web
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT_VALUE}/health" >/dev/null 2>&1; then
    log 'Web service is healthy with the generated Cloudflare URL.'
    break
  fi
  sleep 2
done
curl -fsS "http://127.0.0.1:${PORT_VALUE}/health" >/dev/null || {
  log 'Web service did not recover after applying BASE_URL.'
  "${COMPOSE[@]}" logs --tail=100 web || true
  exit 1
}

log "BASE_URL saved to .env and MongoDB: $TUNNEL_URL"
log 'Tunnel is running. Keep this systemd service enabled; it will regenerate and resynchronize the URL after a VPS reboot.'
wait "$CF_PID"
