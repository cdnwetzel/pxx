#!/usr/bin/env bash
# Scaffold the pxx-quickstart sandbox: a tiny temperature-converter you'll FINISH with pxx.
# Safe to run anywhere — it makes a throwaway git repo in ./pxx-quickstart.
#
# Dual-flavor: works for BOTH pxx 1.3.x (stable) and pxx 2.0.
# CHANGES vs the 1.x-only scaffold:
#   1. Emits pxx.toml with `test_command` — pxx 2.0 reads it (enables `pxx loop`
#      in Level 6½); pxx 1.3.x ignores the file entirely. Harmless on both.
#   2. .gitignore also ignores `.pxx/` — 2.0 writes project-local state there
#      during loop runs; keeping it out of git preserves the Level 1 promise
#      that `git status` is your "did the agent change anything?" signal.
# Carried over from the validated 1.x scaffold:
#   - `if __name__ == "__main__"` entry point ships pre-wired (P0 fix).
#   - test_cli_c_to_f() covers the CLI climax — the scoreboard is /6.
#   - .gitignore for aider droppings (2.0's aider backend drops them too).
set -euo pipefail

dir="${1:-pxx-quickstart}"
if [ -e "$dir" ]; then echo "refusing: $dir already exists"; exit 1; fi
mkdir -p "$dir"; cd "$dir"

cat > converter.py <<'PY'
"""A tiny temperature converter — you'll finish building it with pxx."""

import sys


def celsius_to_fahrenheit(c):
    return c * 9 / 5                 # BUG (Level 3): forgot the + 32


def fahrenheit_to_celsius(f):
    raise NotImplementedError        # Level 4: you'll add this


def convert(value, unit_from, unit_to):
    raise NotImplementedError        # Level 6: dispatch between units


def main(argv):
    # Level 6: make `python converter.py 100 C F` print 212.0
    raise NotImplementedError


if __name__ == "__main__":           # entry point — already wired for you;
    main(sys.argv)                   # implement main() in Level 6 and this runs it
PY

cat > test_converter.py <<'PY'
import subprocess
import sys

from converter import celsius_to_fahrenheit, fahrenheit_to_celsius, convert


def test_c2f_freezing():   assert celsius_to_fahrenheit(0) == 32     # Level 3 fixes
def test_c2f_boiling():    assert celsius_to_fahrenheit(100) == 212  # Level 3 fixes
def test_f2c():            assert fahrenheit_to_celsius(212) == 100  # Level 4 adds
def test_convert_c_to_f(): assert convert(100, "C", "F") == 212      # Level 6 adds
def test_convert_f_to_c(): assert convert(32, "F", "C") == 0         # Level 6 adds


def test_cli_c_to_f():                                               # Level 6 adds (the CLI itself)
    """`python converter.py 100 C F` must print 212.0 — covers main() + the entry point."""
    out = subprocess.run(
        [sys.executable, "converter.py", "100", "C", "F"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert out == "212.0"
PY

cat > pxx.toml <<'TOML'
# Read by pxx 2.0 only (project config); pxx 1.3.x ignores this file.
# Lets `pxx loop` score itself with the tutorial's scoreboard (Level 6½).
test_command = "pytest -q"

# 2.0's native backend fail-closes shell-in-edit-mode behind a PreToolUse hook.
# Without one, the model's post-edit test run dies with HOOKS_MISSING (after
# its file writes already landed). This narrow hook allows only pytest.
[[hooks]]
event = "PreToolUse"
matcher = "run_shell"
command = "grep -qE 'pytest|converter'"   # tests + the tutorial's own CLI
TOML

cat > .gitignore <<'GI'
# aider writes these into your working tree during a session (1.x always,
# 2.0 when its aider backend is active); ignoring them keeps `git status`
# clean so you can trust it as your "did the agent change anything?" signal
.aider*
# pxx 2.0 project-local state (loop runs, candidates)
.pxx/
__pycache__/
.pytest_cache/
GI

cat > README.md <<'MD'
# pxx-quickstart

A throwaway converter with a bug and two unfinished functions. You'll finish it with pxx.

    pytest -q        # 6 failing tests — your starting line
    # Goal: all 6 green, and `python converter.py 100 C F` prints 212.0

Works with pxx 1.3.x and pxx 2.0 (pxx.toml is 2.0-only sugar; 1.x ignores it).
Nothing here matters — break it, undo it, re-break it. That's the point.
MD

git init -q
git config user.email you@example.com
git config user.name "You"
git add -A
git commit -q -m "start: a converter to finish with pxx"

echo "✓ pxx-quickstart ready in ./$dir"
echo "  need pytest?  →  uv tool install pytest   (or:  pip install pytest)"
echo "  cd $dir && pytest -q     # you should see 6 failing tests (that's the start line)"
