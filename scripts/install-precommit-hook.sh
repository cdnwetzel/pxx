#!/usr/bin/env bash
# Install pxx's git hooks into the current git repo.
#
# Usage:
#   bash scripts/install-precommit-hook.sh             # install / refresh
#   bash scripts/install-precommit-hook.sh --force     # overwrite non-pxx hooks
#   bash scripts/install-precommit-hook.sh --uninstall # remove pxx hooks
#
# Four hooks are installed:
#   pre-commit          (#002 M2)   ruff + pytest + diff cap + scope gate
#   prepare-commit-msg  (#012 M2)   prepends [autonomous] when PXX_AUTONOMOUS=1
#   post-commit         (#008 M1)   restart hint when a pxx core file changes
#   pre-push            (#015 M1)   gate [autonomous] commits behind env var
#
# Templates live in scripts/<hook>-template. This installer copies each
# (with a marker line) into .git/hooks/<hook> and makes it executable.
# Idempotent: re-running refreshes both hooks.
#
# Refuses to overwrite a pre-existing non-pxx hook unless --force is passed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="# pxx-managed pre-commit hook"
# Single marker string covers both hooks — it's a generic "this is
# pxx-managed" sentinel; the historical name reflects that the
# pre-commit hook was the first one installed.

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: not inside a git repository (cwd: $(pwd))" >&2
    exit 1
fi

GIT_DIR=$(git rev-parse --absolute-git-dir)
WORK_TREE=$(git rev-parse --show-toplevel)
HOOKS_DIR="$GIT_DIR/hooks"

# Hook spec: (hook-name, template-filename) pairs. Add more here as new
# hooks are added; the install/uninstall paths below iterate over them.
HOOKS=(
    "pre-commit:pre-commit-template"
    "prepare-commit-msg:prepare-commit-msg-template"
    "post-commit:post-commit-template"
    "pre-push:pre-push-template"
)

FORCE=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --uninstall) UNINSTALL=1 ;;
        *)
            echo "ERROR: unknown arg: $arg" >&2
            echo "  Usage: $0 [--force] [--uninstall]" >&2
            exit 2
            ;;
    esac
done

install_one_hook() {
    local hook_name="$1" template_name="$2"
    local hook="$HOOKS_DIR/$hook_name"
    local template="$SCRIPT_DIR/$template_name"

    if [ ! -f "$template" ]; then
        echo "ERROR: hook template not found at $template" >&2
        exit 1
    fi

    if [ -f "$hook" ]; then
        if grep -q "$MARKER" "$hook" 2>/dev/null; then
            echo "pxx $hook_name hook already installed at $hook; refreshing..."
        elif [ "$FORCE" = "1" ]; then
            echo "Existing $hook_name hook is NOT pxx-managed; --force given, overwriting..."
        else
            echo "ERROR: $hook exists and is not pxx-managed." >&2
            echo "  Use '$0 --force' to overwrite, or merge by hand." >&2
            exit 1
        fi
    fi

    mkdir -p "$HOOKS_DIR"
    # The template's shebang MUST stay line 1, or git ignores it and runs the
    # hook under /bin/sh — which is bash on macOS (pipefail works) but dash on
    # Ubuntu (pipefail is illegal). Inject the pxx-managed marker AFTER the
    # shebang, not before it. (CI caught this on its first run, 2026-07-17.)
    {
        head -n 1 "$template"          # shebang, verbatim, line 1
        printf '%s\n' "$MARKER"
        tail -n +2 "$template"         # the rest of the template
    } > "$hook"
    chmod +x "$hook"
    echo "Installed pxx $hook_name hook at $hook"
}

uninstall_one_hook() {
    local hook_name="$1"
    local hook="$HOOKS_DIR/$hook_name"

    if [ ! -f "$hook" ]; then
        echo "No $hook_name hook found at $hook; nothing to do."
        return 0
    fi
    if ! grep -q "$MARKER" "$hook" 2>/dev/null; then
        echo "WARN: $hook is not pxx-managed; skipping." >&2
        return 0
    fi
    rm -f "$hook"
    echo "Removed pxx $hook_name hook at $hook"
}

if [ "$UNINSTALL" = "1" ]; then
    for spec in "${HOOKS[@]}"; do
        hook_name="${spec%%:*}"
        uninstall_one_hook "$hook_name"
    done
    echo "  (repo: $WORK_TREE)"
    exit 0
fi

for spec in "${HOOKS[@]}"; do
    hook_name="${spec%%:*}"
    template_name="${spec##*:}"
    install_one_hook "$hook_name" "$template_name"
done
echo "  (repo: $WORK_TREE)"
