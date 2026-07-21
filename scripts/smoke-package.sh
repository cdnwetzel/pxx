#!/usr/bin/env bash
# scripts/smoke-package.sh — Phase 0.5 Tier B: package smoke test.
#
# Build the wheel, install it into a throwaway venv, and assert:
#   (a) `pxx --version` works
#   (b) `pxx doctor` runs (offline: endpoint failures are soft warnings,
#       so a zero exit is the designed behavior — verified against doctor.py)
#   (c) the bundled prompts resource loads via importlib.resources
#   (d) evals/, tests/, docs/ are NOT in the wheel
#   (e) pxx.eval and pxx.improve ARE importable from the installed wheel
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail() { echo "smoke-package: FAIL: $*" >&2; exit 1; }
note() { echo "smoke-package: $*"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK" "$REPO_ROOT/build" "$REPO_ROOT/dist" "$REPO_ROOT"/pxx_orchestrator.egg-info' EXIT

# --- build the wheel -------------------------------------------------------
DIST="$WORK/dist"
mkdir -p "$DIST"
if command -v uv >/dev/null 2>&1; then
    note "building wheel with uv build"
    uv build --wheel --out-dir "$DIST"
else
    note "uv not found; building wheel with python -m build"
    python3 -m build --wheel --outdir "$DIST" || \
        fail "neither 'uv build' nor a working 'python -m build' available"
fi

WHEEL="$(ls "$DIST"/*.whl 2>/dev/null | head -n 1)"
[ -n "$WHEEL" ] || fail "no wheel produced in $DIST"
note "built $(basename "$WHEEL")"

# --- inspect wheel contents ------------------------------------------------
LISTING="$(python3 -c "import zipfile,sys; [print(n) for n in zipfile.ZipFile(sys.argv[1]).namelist()]" "$WHEEL")"

for excluded in evals/ tests/ docs/; do
    if grep -q "^${excluded}" <<<"$LISTING"; then
        fail "wheel must not contain '${excluded}'"
    fi
done
note "wheel excludes evals/, tests/, docs/"

grep -q "^pxx/prompts/native_system\.md$" <<<"$LISTING" || \
    fail "wheel is missing pxx/prompts/native_system.md"
grep -q "^pxx/eval/__init__\.py$" <<<"$LISTING" || fail "wheel is missing pxx/eval/"
grep -q "^pxx/improve/__init__\.py$" <<<"$LISTING" || fail "wheel is missing pxx/improve/"

# --- install into a throwaway venv -----------------------------------------
VENV="$WORK/venv"
python3 -m venv "$VENV"
PY="$VENV/bin/python"
PXX="$VENV/bin/pxx"
"$PY" -m pip install --quiet "$WHEEL" || fail "pip install of wheel failed"

# --- (a) pxx --version -----------------------------------------------------
VERSION_OUT="$("$PXX" --version)" || fail "'pxx --version' failed"
grep -q "2\.0\.0" <<<"$VERSION_OUT" || fail "unexpected version output: $VERSION_OUT"
note "pxx --version: $VERSION_OUT"

# --- (b) pxx doctor --------------------------------------------------------
"$PXX" doctor >/dev/null || fail "'pxx doctor' exited non-zero"
note "pxx doctor: ok (endpoint warnings are expected offline)"

# --- (c) prompts resource loads --------------------------------------------
"$PY" -c "from importlib.resources import files; files('pxx').joinpath('prompts/native_system.md').read_text()" \
    || fail "prompts resource failed to load from installed wheel"
note "prompts resource loads"

# --- (e) pxx.eval / pxx.improve importable ----------------------------------
"$PY" -c "import pxx.eval, pxx.improve" || fail "pxx.eval/pxx.improve not importable from wheel"
note "pxx.eval and pxx.improve import from installed wheel"

note "PASS"
