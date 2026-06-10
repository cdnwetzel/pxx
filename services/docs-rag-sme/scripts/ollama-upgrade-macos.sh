#!/usr/bin/env bash
# Terminal-only, reversible upgrade of Ollama.app on macOS (no GUI needed).
# Backs up the current app so rollback is one command. Run as your normal user;
# it uses sudo only for the /Applications replace + the CLI symlink, which will
# prompt for your password in this terminal.
#
#   bash scripts/ollama-upgrade-macos.sh
#
# This swaps the inference engine the whole pxx Studio fleet uses. After it
# finishes, run scripts/ollama-upgrade-verify.sh (embedding-drift + fleet smoke).
set -euo pipefail

URL="https://ollama.com/download/Ollama-darwin.zip"
APP="/Applications/Ollama.app"
TMP="$(mktemp -d)"
TS="$(date -u +%Y%m%d-%H%M%S)"
BAK="${APP}.bak-${TS}"

echo "current: $(ollama --version 2>&1 | head -1)"

echo "1/6 quitting Ollama (server + menu-bar app)…"
osascript -e 'tell application "Ollama" to quit' 2>/dev/null || true
pkill -x Ollama 2>/dev/null || true
sleep 2

echo "2/6 downloading latest Ollama.app…"
curl -fSL "$URL" -o "$TMP/Ollama.zip"

echo "3/6 unpacking…"
unzip -q -o "$TMP/Ollama.zip" -d "$TMP"
test -d "$TMP/Ollama.app" || { echo "download did not contain Ollama.app"; exit 1; }

echo "4/6 backing up current app -> $BAK (sudo)…"
sudo mv "$APP" "$BAK"

echo "5/6 installing new app + refreshing CLI symlink (sudo)…"
sudo mv "$TMP/Ollama.app" "$APP"
sudo ln -sf "$APP/Contents/Resources/ollama" /usr/local/bin/ollama

echo "6/6 relaunching Ollama as your user…"
open -a Ollama
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done

echo "----"
echo "new: $(ollama --version 2>&1 | head -1)"
echo "models: $(curl -s http://127.0.0.1:11434/api/tags | python3 -c 'import sys,json;print([m["name"] for m in json.load(sys.stdin)["models"]])')"
echo
echo "ROLLBACK (if anything looks wrong):"
echo "  osascript -e 'tell application \"Ollama\" to quit'; sudo rm -rf $APP && sudo mv $BAK $APP && open -a Ollama"
echo
echo "NEXT: bash scripts/ollama-upgrade-verify.sh"
