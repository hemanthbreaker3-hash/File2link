#!/usr/bin/env bash
# Run this pasted script on the VPS. It asks for the repo URL and secrets safely,
# clones a clean checkout, exports the user's current File2link settings, then deploys.
set -Eeuo pipefail

read -r -p 'GitHub repository URL for File2link: ' REPO_URL
[[ "$REPO_URL" =~ ^https://github\.com/[^/]+/[^/]+([.]git)?$ ]] || { echo 'Enter a valid https://github.com/OWNER/REPO[.git] URL.' >&2; exit 2; }
read -r -s -p 'API_HASH (paste rotated/current hash): ' API_HASH; echo
read -r -s -p 'BOT_TOKEN (paste rotated/current token): ' BOT_TOKEN; echo
read -r -s -p 'MONGODB_URI (paste rotated/current Atlas URI): ' MONGODB_URI; echo
[[ -n "$API_HASH" && -n "$BOT_TOKEN" && "$MONGODB_URI" == mongodb* ]] || { echo 'API_HASH, BOT_TOKEN and a valid MongoDB URI are required.' >&2; exit 2; }

APP_DIR="$HOME/file2link"
cd "$HOME"
rm -rf -- "$APP_DIR"
git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"

export API_ID='22266643'
export API_HASH BOT_TOKEN
export OWNER_ID='8663988850'
export ADMINS='5778067019'
export CHANNEL_ID='-1003553348627'
export FORCE_SUB_CHANNELS='-1004306981801'
export MONGODB_URI
export DATABASE_NAME='file2link'
export REDIS_URL='redis://redis:6379/0'
export PORT='8000'

[[ -f deploy.vps ]] || { echo 'Repository root does not contain deploy.vps.' >&2; exit 1; }
chmod +x deploy.vps
./deploy.vps
