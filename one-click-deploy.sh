#!/usr/bin/env bash
# Compatibility entry point for the standard VPS deployment.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec ./deploy.vps
