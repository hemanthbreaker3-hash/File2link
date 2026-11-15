#!/usr/bin/env bash
# Clone a File2link repository and run its deploy.vps script.
set -Eeuo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <git-repository-url> [directory-name]" >&2
  exit 2
fi
REPO_URL="$1"
TARGET_DIR="${2:-file2link}"
if [ -e "$TARGET_DIR" ]; then
  echo "[ERROR] '$TARGET_DIR' already exists. Remove or rename it first." >&2
  exit 1
fi
command -v git >/dev/null 2>&1 || { echo "[ERROR] git is required." >&2; exit 1; }
git clone "$REPO_URL" "$TARGET_DIR"
cd "$TARGET_DIR"
[ -f deploy.vps ] || { echo "[ERROR] deploy.vps was not found in the cloned repository root." >&2; exit 1; }
chmod +x deploy.vps
./deploy.vps
