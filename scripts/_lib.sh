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

# _marker_present <file> <marker-name>
#
# Return 0 if a `# pxx-managed:<marker-name>:start` line exists in <file>.
# Returns 1 if file doesn't exist or marker absent.
#
# Usage:
#   if _marker_present "$HOME/.zshrc" "pxx-env"; then ...; fi
_marker_present() {
    local file="$1"
    local marker="$2"
    [[ -f "$file" ]] || return 1
    grep -q "^# pxx-managed:${marker}:start\$" "$file"
}

# _append_with_markers <file> <marker-name>
#
# Append stdin content to <file>, wrapped in marker comments:
#   # pxx-managed:<marker-name>:start
#   <stdin content>
#   # pxx-managed:<marker-name>:end
#
# If the marker block already exists in <file>, the content between the
# start/end markers is REPLACED. Otherwise the block is appended at end.
# Idempotent: running twice with the same content yields the same file.
# Uses awk for portability — no GNU-sed dependency.
#
# Usage:
#   _append_with_markers "$HOME/.zshrc" "pxx-env" <<'EOF'
#   export PXX_STUDIO_LAN_URL=http://workstation:11434
#   EOF
_append_with_markers() {
    local file="$1"
    local marker="$2"
    local content
    content=$(cat)
    local start_line="# pxx-managed:${marker}:start"
    local end_line="# pxx-managed:${marker}:end"

    touch "$file"

    if _marker_present "$file" "$marker"; then
        # Replace block. Pass content via a temp file (not -v) so awk
        # does not choke on multi-line strings with embedded newlines.
        local content_tmp file_tmp
        content_tmp=$(mktemp)
        printf '%s\n' "$content" > "$content_tmp"
        file_tmp=$(mktemp)
        awk -v start="$start_line" -v end="$end_line" -v cf="$content_tmp" '
            $0 == start {
                in_block = 1
                print start
                while ((getline line < cf) > 0) print line
                close(cf)
                next
            }
            $0 == end   { in_block = 0; print end; next }
            !in_block   { print }
        ' "$file" > "$file_tmp"
        mv "$file_tmp" "$file"
        rm -f "$content_tmp"
    else
        printf '\n%s\n%s\n%s\n' "$start_line" "$content" "$end_line" >> "$file"
    fi
}
