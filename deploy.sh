#!/usr/bin/env bash
# Compatibility entry point; all deployment logic lives in deploy.vps.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec ./deploy.vps
