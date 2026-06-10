#!/usr/bin/env bash
# Wrapper for the launchd/systemd timer: run the delta-refresh and append the
# JSON-Lines output to a dated log under .setup/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.setup"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/refresh-$(date -u +%Y%m%d).jsonl"

cd "$ROOT"
# uv must be on PATH; Homebrew installs to /opt/homebrew/bin.
export PATH="/opt/homebrew/bin:$PATH"
exec uv run docs-sme-refresh "$@" >>"$LOG" 2>&1
