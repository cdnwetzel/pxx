#!/usr/bin/env bash
# One-shot setup for the 8GB MacBook Neo (thin client + offline fallback).
# Run this on the Neo itself.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Checking Homebrew..."
if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh"
  exit 1
fi

echo "==> Installing Ollama (offline fallback)..."
if ! command -v ollama >/dev/null; then
  brew install ollama
fi

echo "==> Tuning Ollama for 8GB..."
launchctl setenv OLLAMA_KEEP_ALIVE "5m"
launchctl setenv OLLAMA_MAX_LOADED_MODELS "1"
launchctl setenv OLLAMA_NUM_PARALLEL "1"
launchctl setenv OLLAMA_FLASH_ATTENTION "1"

brew services restart ollama 2>/dev/null || brew services start ollama
sleep 3

echo "==> Pulling offline fallback model..."
ollama pull qwen3:4b

echo "==> Installing uv (Python env manager)..."
if ! command -v uv >/dev/null; then
  brew install uv
fi

echo "==> Installing Python 3.12 (aider deps don't yet support 3.13+)..."
uv python install 3.12

echo "==> Installing pxx..."
cd "$REPO_DIR"
uv tool install --editable . --python 3.12

echo "==> Verifying..."
if command -v pxx >/dev/null; then
  echo "    pxx installed at: $(which pxx)"
else
  echo "    pxx not on PATH — run: uv tool update-shell  (then restart shell)"
fi

echo ""
echo "==> Neo setup complete."
echo ""
echo "    Add to ~/.zshrc (fill in your work-internal Studio hostname):"
echo "      export PXX_STUDIO_LAN_URL=http://workstation:11434"
echo "      export PXX_STUDIO_REMOTE_URL=http://workstation:11434"
echo ""
echo "    Daily flow:"
echo "      Office (LAN):    pxx                       (auto-detects Studio)"
echo "      Remote:          (bring up SSL VPN)        pxx"
echo "      Offline:         pxx                       (falls back to qwen3:4b)"
