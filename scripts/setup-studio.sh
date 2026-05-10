#!/usr/bin/env bash
# One-shot setup for the M4 Max Mac Studio (the heavy backend).
# Run this on the Studio itself.
#
# Access model: the Neo reaches this Studio either over the office LAN
# or over your existing SSL VPN. No new ports exposed publicly.

set -euo pipefail

echo "==> Checking Homebrew..."
if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh"
  exit 1
fi

echo "==> Installing Ollama..."
if ! command -v ollama >/dev/null; then
  brew install ollama
fi

echo "==> Configuring Ollama service..."
# Bind to all interfaces so the Neo can reach it both on-LAN
# and when connected through the SSL VPN.
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
launchctl setenv OLLAMA_KEEP_ALIVE "30m"
launchctl setenv OLLAMA_MAX_LOADED_MODELS "1"
launchctl setenv OLLAMA_NUM_PARALLEL "2"
launchctl setenv OLLAMA_FLASH_ATTENTION "1"

brew services restart ollama 2>/dev/null || brew services start ollama
sleep 3

echo "==> Pulling models..."
ollama pull devstral:24b   # 14GB — primary, agentic editing
ollama pull qwen3:14b      # generalist alternate
ollama pull qwen3:8b       # smaller fallback if memory pressure

echo "==> Verifying Ollama API..."
curl -sS http://localhost:11434/api/tags >/dev/null && echo "    Ollama API: OK" || echo "    Ollama API: FAIL"

LAN_HOST="$(hostname).local"
echo ""
echo "==> Studio setup complete."
echo "    LAN URL (office):  http://${LAN_HOST}:11434"
echo "    VPN URL (remote):  use your work-internal DNS name or IP for this machine"
echo ""
echo "    On the Neo, set in ~/.zshrc:"
echo "      export PXX_STUDIO_LAN_URL=http://${LAN_HOST}:11434"
echo "      export PXX_STUDIO_REMOTE_URL=http://<work-internal-hostname-or-ip>:11434"
