#!/usr/bin/env bash
# One-shot setup for the 8GB MacBook Neo (thin client + offline fallback).
# Run this on the Neo itself.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# shellcheck source=./_lib.sh
. "$SCRIPT_DIR/_lib.sh"

echo "==> Checking Homebrew..."
if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh"
  exit 1
fi

echo "==> Installing Ollama (offline fallback)..."
if ! command -v ollama >/dev/null; then
  _with_check "brew install ollama" brew install ollama
fi

echo "==> Tuning Ollama for 8GB..."
launchctl setenv OLLAMA_KEEP_ALIVE "5m"
launchctl setenv OLLAMA_MAX_LOADED_MODELS "1"
launchctl setenv OLLAMA_NUM_PARALLEL "1"
launchctl setenv OLLAMA_FLASH_ATTENTION "1"

brew services restart ollama 2>/dev/null || brew services start ollama
sleep 3

echo "==> Pulling offline fallback model..."
_with_check "ollama pull qwen3:4b" ollama pull qwen3:4b

echo "==> Installing uv (Python env manager)..."
if ! command -v uv >/dev/null; then
  _with_check "brew install uv" brew install uv
fi

echo "==> Installing Python 3.12 (aider deps don't yet support 3.13+)..."
_with_check "uv python install 3.12" uv python install 3.12

echo "==> Installing pxx..."
cd "$REPO_DIR"
_with_check "uv tool install pxx" uv tool install --editable . --python 3.12

echo "==> Verifying..."
if command -v pxx >/dev/null; then
  echo "    pxx installed at: $(which pxx)"
else
  echo "    pxx not on PATH — run: uv tool update-shell  (then restart shell)"
fi

echo "==> Configuring ~/.zshrc with pxx env vars (marker-managed; idempotent)..."
# This block is rewritten in place on every setup run.  If you previously
# added these exports by hand, remove the hand-added copies once; the
# marker block will manage them from now on.
_append_with_markers "$HOME/.zshrc" "pxx-env" <<'EOF'
export PXX_STUDIO_LAN_URL=http://workstation:11434
export PXX_STUDIO_REMOTE_URL=http://workstation:11434
EOF
echo "    ~/.zshrc updated.  Open a new shell or run: source ~/.zshrc"

echo ""
echo "==> Neo setup complete."
echo ""
echo "    Daily flow:"
echo "      Office (LAN):    pxx                       (auto-detects Studio)"
echo "      Remote:          (bring up SSL VPN)        pxx"
echo "      Offline:         pxx                       (falls back to qwen3:4b)"
