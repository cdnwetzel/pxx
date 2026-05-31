#!/usr/bin/env bash
# Smoke tests for scripts/_lib.sh helpers.
#
# Run via:  bash scripts/test_lib.sh
#
# Plain bash; no framework. Each test is a labeled assertion. Exits
# non-zero on any failure and prints a pass/fail summary. Intended as
# the regression net for #005's marker + with_check helpers.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "$SCRIPT_DIR/_lib.sh"

PASS=0
FAIL=0

_assert_eq() {
    local label="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        PASS=$((PASS + 1))
        echo "  ✓ $label"
    else
        FAIL=$((FAIL + 1))
        echo "  ✗ $label" >&2
        echo "    expected: $(printf '%q' "$expected")" >&2
        echo "    actual:   $(printf '%q' "$actual")" >&2
    fi
}

_assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -q -- "$needle"; then
        PASS=$((PASS + 1))
        echo "  ✓ $label"
    else
        FAIL=$((FAIL + 1))
        echo "  ✗ $label" >&2
        echo "    expected to find: $needle" >&2
    fi
}

_assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -q -- "$needle"; then
        FAIL=$((FAIL + 1))
        echo "  ✗ $label" >&2
        echo "    should NOT contain: $needle" >&2
    else
        PASS=$((PASS + 1))
        echo "  ✓ $label"
    fi
}

_md5_of() {
    if command -v md5 >/dev/null; then
        md5 -q "$1"
    else
        md5sum "$1" | awk '{print $1}'
    fi
}

TF=$(mktemp)
trap 'rm -f "$TF"' EXIT

echo "=== _marker_present ==="

# Marker absent in a non-empty file.
echo "# existing content" > "$TF"
if _marker_present "$TF" "test-marker"; then RESULT=found; else RESULT=absent; fi
_assert_eq "absent in clean file" "$RESULT" "absent"

# Marker absent when file does not exist.
rm -f "$TF"
if _marker_present "$TF" "test-marker"; then RESULT=found; else RESULT=absent; fi
_assert_eq "absent when file missing" "$RESULT" "absent"
touch "$TF"

echo
echo "=== _append_with_markers — first append ==="

echo "# existing line" > "$TF"
_append_with_markers "$TF" "test-marker" <<'EOF'
line one
line two
EOF
CONTENT=$(cat "$TF")
_assert_contains "start marker present" "$CONTENT" "^# pxx-managed:test-marker:start\$"
_assert_contains "end marker present"   "$CONTENT" "^# pxx-managed:test-marker:end\$"
_assert_contains "stdin line 1 present" "$CONTENT" "^line one\$"
_assert_contains "stdin line 2 present" "$CONTENT" "^line two\$"
_assert_contains "pre-existing line preserved" "$CONTENT" "^# existing line\$"

# Marker should now be detected.
if _marker_present "$TF" "test-marker"; then RESULT=found; else RESULT=absent; fi
_assert_eq "marker present after append" "$RESULT" "found"

echo
echo "=== _append_with_markers — idempotent re-run ==="

BEFORE_HASH=$(_md5_of "$TF")
_append_with_markers "$TF" "test-marker" <<'EOF'
line one
line two
EOF
AFTER_HASH=$(_md5_of "$TF")
_assert_eq "byte-identical file after same-content re-run" "$AFTER_HASH" "$BEFORE_HASH"

echo
echo "=== _append_with_markers — replace with different content ==="

_append_with_markers "$TF" "test-marker" <<'EOF'
new only
EOF
CONTENT=$(cat "$TF")
_assert_contains "new content present"          "$CONTENT" "^new only\$"
_assert_not_contains "old line one removed"     "$CONTENT" "^line one\$"
_assert_not_contains "old line two removed"     "$CONTENT" "^line two\$"
_assert_contains "outside-marker content kept"  "$CONTENT" "^# existing line\$"

echo
echo "=== _append_with_markers — dup stripping ==="

# Pre-existing exact duplicate OUTSIDE any marker block (e.g., user
# hand-added the same export earlier) is removed before the marker
# block is added. Only the exact line is removed — surrounding
# comments and unrelated lines stay.

# Test: hand-added exact dup, first append (no prior marker block)
cat > "$TF" <<'EOF'
# user pre-existing top comment
export DUP=once
# user mid comment
EOF
_append_with_markers "$TF" "dup-test" <<'EOF'
export DUP=once
export NEW=value
EOF
CONTENT=$(cat "$TF")
# The pre-existing "export DUP=once" line outside the marker should be GONE.
# But it should appear EXACTLY once inside the marker block.
DUP_COUNT=$(grep -c '^export DUP=once$' "$TF")
_assert_eq "exact dup stripped (count is 1)" "$DUP_COUNT" "1"
_assert_contains "marker block exists"   "$CONTENT" "^# pxx-managed:dup-test:start\$"
_assert_contains "new line present"       "$CONTENT" "^export NEW=value\$"
_assert_contains "unrelated top comment kept" "$CONTENT" "^# user pre-existing top comment\$"
_assert_contains "unrelated mid comment kept" "$CONTENT" "^# user mid comment\$"

# Test: similar-but-not-exact line preserved (different value).
cat > "$TF" <<'EOF'
export ALMOST=different
EOF
_append_with_markers "$TF" "near-test" <<'EOF'
export ALMOST=same
EOF
CONTENT=$(cat "$TF")
_assert_contains "different-value line preserved" "$CONTENT" "^export ALMOST=different\$"
_assert_contains "new-value line present"         "$CONTENT" "^export ALMOST=same\$"

# Test: lines inside OTHER marker blocks are NOT touched, even if they
# match.  Only outside-any-block matches get stripped.
cat > "$TF" <<'EOF'
# pxx-managed:other:start
export SHARED=value
# pxx-managed:other:end
export SHARED=value
EOF
_append_with_markers "$TF" "another" <<'EOF'
export SHARED=value
EOF
INSIDE_OTHER=$(awk '/^# pxx-managed:other:start$/,/^# pxx-managed:other:end$/' "$TF" | grep -c '^export SHARED=value$')
_assert_eq "line inside other marker block preserved" "$INSIDE_OTHER" "1"
SHARED_COUNT=$(grep -c '^export SHARED=value$' "$TF")
# One inside `other` block + one inside `another` block = 2 total occurrences
_assert_eq "outside-block dup removed; total = 2" "$SHARED_COUNT" "2"

echo
echo "=== _with_check ==="

if _with_check "smoke success" true >/dev/null 2>&1; then RESULT=success; else RESULT=fail; fi
_assert_eq "success path returns 0" "$RESULT" "success"

# Failure path calls `exit 1`, so run it in a subshell so the test
# script itself isn't terminated.
if (_with_check "smoke failure" false) >/dev/null 2>&1; then RESULT=success; else RESULT=fail; fi
_assert_eq "failure path exits non-zero" "$RESULT" "fail"

echo
echo "=== summary ==="
echo "  Pass: $PASS  Fail: $FAIL"
[ "$FAIL" -eq 0 ]
