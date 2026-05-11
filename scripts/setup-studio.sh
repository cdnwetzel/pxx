#!/usr/bin/env bash
# One-shot setup for the M4 Max Mac Studio (the heavy backend).
# Run this on the Studio itself.
#
# Access model: the Neo reaches this Studio either over the office LAN
# or over your existing SSL VPN. No new ports exposed publicly.

set -euo pipefail

# shellcheck source=./_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

echo "==> Checking Homebrew..."
if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh"
  exit 1
fi

echo "==> Installing Ollama..."
if ! command -v ollama >/dev/null; then
  _with_check "brew install ollama" brew install ollama
fi

echo "==> Configuring Ollama service..."
# Bind to all interfaces so the Neo can reach it both on-LAN
# and when connected through the SSL VPN.
#
# SECURITY POSTURE: 0.0.0.0:11434 means Ollama listens on every network
# interface on this host. Ollama itself has no authentication. This is
# only safe because the network boundary controls who can reach this port:
#   - LAN: trusted office network (physical access required)
#   - Remote: SSL VPN required (authenticated tunnel)
#   - Public internet: NOT exposed (no port forward on the router)
# If you ever take this Studio off a trusted network — e.g. a coffee-shop
# wifi — change OLLAMA_HOST to 127.0.0.1:11434 first.
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
launchctl setenv OLLAMA_KEEP_ALIVE "30m"
launchctl setenv OLLAMA_MAX_LOADED_MODELS "1"
launchctl setenv OLLAMA_NUM_PARALLEL "2"
launchctl setenv OLLAMA_FLASH_ATTENTION "1"

brew services restart ollama 2>/dev/null || brew services start ollama
sleep 3

echo "==> Pulling models..."
# 14GB — primary, agentic editing
_with_check "ollama pull devstral:24b" ollama pull devstral:24b
# generalist alternate
_with_check "ollama pull qwen3:14b"    ollama pull qwen3:14b
# smaller fallback if memory pressure
_with_check "ollama pull qwen3:8b"     ollama pull qwen3:8b

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
