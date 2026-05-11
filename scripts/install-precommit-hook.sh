#!/usr/bin/env bash
# Install pxx's pre-commit hook into the current git repo.
#
# Usage:
#   bash scripts/install-precommit-hook.sh             # install / refresh
#   bash scripts/install-precommit-hook.sh --force     # overwrite non-pxx hook
#   bash scripts/install-precommit-hook.sh --uninstall # remove pxx hook
#
# The hook content lives in scripts/pre-commit-template. This installer
# copies it (with a marker line) into .git/hooks/pre-commit and makes
# it executable. Idempotent: re-running refreshes the hook.
#
# Refuses to overwrite a pre-existing non-pxx pre-commit hook unless
# --force is passed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/pre-commit-template"
MARKER="# pxx-managed pre-commit hook"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: not inside a git repository (cwd: $(pwd))" >&2
    exit 1
fi

GIT_DIR=$(git rev-parse --git-dir)
HOOKS_DIR="$GIT_DIR/hooks"
HOOK="$HOOKS_DIR/pre-commit"

# Uninstall path.
if [ "${1:-}" = "--uninstall" ]; then
    if [ ! -f "$HOOK" ]; then
        echo "No pre-commit hook found at $HOOK; nothing to do."
        exit 0
    fi
    if ! grep -q "$MARKER" "$HOOK" 2>/dev/null; then
        echo "ERROR: $HOOK is not pxx-managed; refusing to remove." >&2
        echo "  Remove it manually if you want it gone." >&2
        exit 1
    fi
    rm -f "$HOOK"
    echo "Removed pxx pre-commit hook at $HOOK"
    exit 0
fi

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: hook template not found at $TEMPLATE" >&2
    exit 1
fi

# Refuse to overwrite a non-pxx hook unless --force.
if [ -f "$HOOK" ]; then
    if grep -q "$MARKER" "$HOOK" 2>/dev/null; then
        echo "pxx pre-commit hook already installed at $HOOK; refreshing..."
    elif [ "${1:-}" = "--force" ]; then
        echo "Existing pre-commit hook is NOT pxx-managed; --force given, overwriting..."
    else
        echo "ERROR: $HOOK exists and is not pxx-managed." >&2
        echo "  Use '$0 --force' to overwrite, or merge by hand." >&2
        exit 1
    fi
fi

mkdir -p "$HOOKS_DIR"
{
    printf '%s\n' "$MARKER"
    cat "$TEMPLATE"
} > "$HOOK"
chmod +x "$HOOK"
echo "Installed pxx pre-commit hook at $HOOK"
