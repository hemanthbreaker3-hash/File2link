#!/usr/bin/env bash
# Run this pasted script on the VPS. It asks for the repo URL and secrets safely,
# clones a clean checkout, exports the user's current File2link settings, then deploys.
set -Eeuo pipefail

read -r -p 'GitHub repository URL for File2link: ' REPO_URL
[[ "$REPO_URL" =~ ^https://github\.com/[^/]+/[^/]+([.]git)?$ ]] || { echo 'Enter a valid https://github.com/OWNER/REPO[.git] URL.' >&2; exit 2; }
read -r -p 'Telegram API_ID: ' API_ID
read -r -s -p 'API_HASH (paste rotated/current hash): ' API_HASH; echo
read -r -p 'Telegram OWNER_ID: ' OWNER_ID
read -r -p 'ADMINS (comma-separated IDs, optional): ' ADMINS
read -r -p 'CHANNEL_ID (optional): ' CHANNEL_ID
read -r -p 'FORCE_SUB_CHANNELS (comma-separated IDs, optional): ' FORCE_SUB_CHANNELS
read -r -s -p 'TMDB_API_KEY (optional): ' TMDB_API_KEY; echo
read -r -s -p 'BOT_TOKEN (paste rotated/current token): ' BOT_TOKEN; echo
read -r -s -p 'MONGODB_URI (paste rotated/current Atlas URI): ' MONGODB_URI; echo
[[ "$API_ID" =~ ^[0-9]+$ && "$OWNER_ID" =~ ^[0-9]+$ && -n "$API_HASH" && -n "$BOT_TOKEN" && "$MONGODB_URI" == mongodb* ]] || { echo 'API_HASH, BOT_TOKEN and a valid MongoDB URI are required.' >&2; exit 2; }

APP_DIR="$HOME/file2link"
cd "$HOME"
rm -rf -- "$APP_DIR"
git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"

export API_ID API_HASH BOT_TOKEN OWNER_ID ADMINS CHANNEL_ID FORCE_SUB_CHANNELS TMDB_API_KEY
export MONGODB_URI
export DATABASE_NAME='file2link'
export REDIS_URL='redis://redis:6379/0'
export PORT='8000'

[[ -f deploy.vps ]] || { echo 'Repository root does not contain deploy.vps.' >&2; exit 1; }
chmod +x deploy.vps
./deploy.vps
