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
# Behavior:
#   - If the marker block already exists in <file>, its content is
#     REPLACED with the new stdin. Otherwise the block is appended at end.
#   - Before the marker-block write, any pre-existing exact-match lines
#     OUTSIDE any `pxx-managed:*` block are stripped. This avoids
#     duplicates from earlier hand-edits or other tools. Only full-line
#     exact matches are removed; partial / similar lines are left alone.
#   - Idempotent: running twice with the same stdin yields the same file
#     byte-for-byte.
#   - Uses awk for portability — no GNU-sed dependency.
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

    # Build two views of the stdin content as temp files:
    #   content_full: for the marker-block body (preserves blank lines)
    #   content_skip: for the dup-strip skip set (blank lines removed
    #                 so we don't accidentally delete blank lines
    #                 elsewhere in the file)
    local content_full content_skip file_tmp
    content_full=$(mktemp)
    content_skip=$(mktemp)
    printf '%s\n' "$content" > "$content_full"
    grep -v '^$' "$content_full" > "$content_skip" || true

    # Dup-strip pass: remove any lines OUTSIDE any pxx-managed:* block
    # that exactly match a line in the new stdin. Skipped when stdin
    # has no non-blank lines (nothing to match against).
    if [ -s "$content_skip" ] && grep -F -x -q -f "$content_skip" "$file" 2>/dev/null; then
        file_tmp=$(mktemp)
        awk -v cf="$content_skip" '
            BEGIN { while ((getline line < cf) > 0) skip[line] = 1; close(cf) }
            /^# pxx-managed:.*:start$/ { in_any_block = 1; print; next }
            /^# pxx-managed:.*:end$/   { in_any_block = 0; print; next }
            in_any_block               { print; next }
            !($0 in skip)              { print }
        ' "$file" > "$file_tmp"
        mv "$file_tmp" "$file"
    fi

    if _marker_present "$file" "$marker"; then
        # Replace our block's content with the new stdin.
        file_tmp=$(mktemp)
        awk -v start="$start_line" -v end="$end_line" -v cf="$content_full" '
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
    else
        # No existing block — append one at end.
        printf '\n%s\n%s\n%s\n' "$start_line" "$content" "$end_line" >> "$file"
    fi

    rm -f "$content_full" "$content_skip"
}
