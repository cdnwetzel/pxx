#!/usr/bin/env bash
# Shared helpers for pxx setup / diagnostic scripts.
#
# Sourced by setup-neo.sh, setup-studio.sh. Not intended to run standalone.
# Leading underscore on the filename signals "infrastructure, not a runnable
# script."

# _with_check <label> <command...>
#
# Run a command and exit the calling script with a clear, labeled error if
# it fails. Useful for slow / external operations (brew install, uv tool
# install, ollama pull) where the cascade of follow-on errors from
# `set -euo pipefail` would otherwise obscure the original failure.
#
# Usage:
#   _with_check "ollama pull devstral:24b" ollama pull devstral:24b
_with_check() {
    local label="$1"
    shift
    if ! "$@"; then
        echo >&2
        echo "ERROR (${label}): command failed:" >&2
        echo "  $*" >&2
        echo "Aborting setup; fix the above and re-run." >&2
        exit 1
    fi
}
