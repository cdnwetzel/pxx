#!/usr/bin/env bash
# Wrapper for the keep-alive launchd service. Env (DOCS_SME_UPSTREAM, _PORT,
# _RETRIEVAL, _RERANK) comes from the plist's EnvironmentVariables.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/bin:$PATH"
exec uv run docs-sme
