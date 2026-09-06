#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="$ROOT_DIR/extension"
OUT_PATH="${1:-$ROOT_DIR/dist/flow-provider-extension.zip}"

mkdir -p "$(dirname "$OUT_PATH")"
rm -f "$OUT_PATH"
OUT_ABS="$(cd "$(dirname "$OUT_PATH")" && pwd)/$(basename "$OUT_PATH")"

(
  cd "$EXT_DIR"
  zip -qr "$OUT_ABS" \
    manifest.json \
    config.js \
    background \
    popup \
    providers \
    -x '*/tests/*' \
    -x 'config.local.js' \
    -x 'config.local.example.js' \
    -x '*.DS_Store'
)

python3 - "$OUT_ABS" <<'PY'
import json
import sys
import zipfile

path = sys.argv[1]
with zipfile.ZipFile(path) as archive:
    names = set(archive.namelist())
    required = {
        "manifest.json",
        "config.js",
        "background/background.js",
        "background/offscreen.html",
        "background/offscreen.js",
        "popup/popup.html",
        "popup/popup.js",
        "popup/popup.css",
        "providers/flow/browser-transport.js",
        "providers/flow/session-bridge.js",
        "providers/flow/flow-frame-bridge.js",
    }
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"extension package missing required files: {missing}")
    forbidden = [name for name in names if "tests/" in name or name.endswith("config.local.js")]
    if forbidden:
        raise SystemExit(f"extension package contains forbidden files: {forbidden}")
    manifest = json.loads(archive.read("manifest.json"))
    version = str(manifest.get("version") or "").strip()
    if not version:
        raise SystemExit("extension manifest version is missing")
    print(f"Packaged Flow Provider Connector v{version}: {path}")
PY
