#!/usr/bin/env bash
# Scaffold the pxx-quickstart sandbox: a tiny temperature-converter you'll FINISH with pxx.
# Safe to run anywhere — it makes a throwaway git repo in ./pxx-quickstart.
#
# CHANGES vs the original:
#   1. converter.py now ships the `if __name__ == "__main__": main(sys.argv)` entry
#      point (P0 fix) — Level 6's `python converter.py 100 C F` could never print
#      without it. It's boilerplate the learner shouldn't have to write.
#   2. Added test_cli_c_to_f() so the CLI climax is actually test-covered — the
#      scoreboard is now /6 and "all green" genuinely guarantees the CLI works.
#   3. Added .gitignore for aider's droppings so `git status` stays clean (Level 1
#      checkpoint claim becomes true).
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
    raise NotImplementedError        # Level 5: dispatch between units


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
def test_convert_c_to_f(): assert convert(100, "C", "F") == 212      # Level 5 adds
def test_convert_f_to_c(): assert convert(32, "F", "C") == 0         # Level 5 adds


def test_cli_c_to_f():                                               # Level 6 adds (the CLI itself)
    """`python converter.py 100 C F` must print 212.0 — covers main() + the entry point."""
    out = subprocess.run(
        [sys.executable, "converter.py", "100", "C", "F"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert out == "212.0"
PY

cat > .gitignore <<'GI'
# aider writes these into your working tree during a session; ignoring them keeps
# `git status` clean so you can trust it as your "did the agent change anything?" signal
.aider*
__pycache__/
.pytest_cache/
GI

cat > README.md <<'MD'
# pxx-quickstart

A throwaway converter with a bug and two unfinished functions. You'll finish it with pxx.

    pytest -q        # 6 failing tests — your starting line
    # Goal: all 6 green, and `python converter.py 100 C F` prints 212.0

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
