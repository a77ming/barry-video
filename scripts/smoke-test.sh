#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_CLI="${BARRY_VIDEO_BACKEND:-$ROOT_DIR/backend/inbeidou_cli.py}"

python3 "$BACKEND_CLI" user --json >/dev/null
python3 "$BACKEND_CLI" credit --json >/dev/null
python3 "$BACKEND_CLI" products --json >/dev/null
python3 "$BACKEND_CLI" list --platform dramabox --size 2 --json >/dev/null
python3 "$BACKEND_CLI" uploads list --size 2 --json >/dev/null
python3 "$BACKEND_CLI" publish accounts --platform FACEBOOK --json >/dev/null

echo "Barry Video smoke test passed."
