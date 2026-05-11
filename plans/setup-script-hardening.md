# Setup-Script Hardening

> Backlog ID: **005**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: `—`. Blocked by: `—`.

## Context

`scripts/setup-neo.sh`, `scripts/setup-studio.sh`, and `scripts/doctor.sh`
were written to work the first time on a clean machine. They do, but they
have three real rough edges in re-run / failure scenarios:

- **Partial failures don't fail fast.** If `uv tool install` or `ollama pull`
  fails mid-script, the script continues and produces a cascade of follow-on
  errors. The user has to scroll back to find the original failure.
- **Some sections aren't idempotent.** Most ops are, but the `~/.zshrc`
  env-var append step initially appended duplicates on re-run (caught and
  fixed earlier this session by hand). The general pattern of marker-based
  idempotence isn't enforced.
- **`doctor.sh` calls `/api/tags` twice per reachable endpoint** — once
  to probe, once to extract model names. Halves the calls saves nothing
  material, but the duplication signals carelessness.

Flagged in `../review/00-init-codebase-notes.md` as "Review scripts for
idempotence, failure modes, and Mac-specific assumptions." Mac-specific
assumptions stay deliberately (this is a Mac-only tool); the other two
get hardened.

This is **quality work, not correctness work** — the scripts function
today. The payoff is fewer 3am debugging sessions when something fails
mid-setup on a new machine.

## The mechanisms

### M1 — Idempotence markers

Every section of every setup script that creates persistent state outside
the script's own directory uses a marker-comment convention:

```bash
# pxx-managed:<section-name>:start
... code that writes external state ...
# pxx-managed:<section-name>:end
```

Sections that need this:

- `~/.zshrc` env-var append (`pxx-managed:zshrc-env`)
- `~/.zshrc` PATH addition if any (`pxx-managed:zshrc-path`)
- launchctl `setenv` calls — these are already idempotent (set replaces),
  so no marker needed
- `.git/hooks/pre-commit` install (will be added by #002 — share the
  marker convention)

Re-run logic: before each section, grep for the start marker in the
target file. If present, skip the section (or replace its content
between the markers).

### M2 — Fail-fast wrappers for slow ops

Wrap each `ollama pull`, `brew install`, and `uv tool install` with an
explicit error path:

```bash
_with_check() {
    local label="$1"; shift
    if ! "$@"; then
        echo "ERROR ($label): command failed: $*" >&2
        echo "  Aborting setup; fix the above and re-run." >&2
        exit 1
    fi
}

# usage
_with_check "ollama pull devstral:24b" ollama pull devstral:24b
_with_check "brew install uv"          brew install uv
```

`set -euo pipefail` already at the top of each script gives most of this
for free, but the explicit label + clear "Aborting setup" message
shortens the debugging path when something fails.

### M3 — `doctor.sh` API-call deduplication

Refactor `probe()` to call `/api/tags` exactly once per endpoint:

```bash
probe() {
    local url="$1"
    if [[ -z "$url" ]]; then echo "(not set)"; return; fi
    local resp
    if resp=$(curl -sS --max-time 1 "$url/api/tags" 2>/dev/null); then
        local models
        models=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models', [])) or 'none loaded')" 2>/dev/null || echo "?")
        echo "OK — models: $models"
    else
        echo "unreachable"
    fi
}
```

Same output, half the API calls.

### M4 — Shared bash helper file

Extract the marker management (M1) and `_with_check` (M2) into
`scripts/_lib.sh` so setup-neo.sh and setup-studio.sh both source it.
Conventional underscore-prefix signals "infrastructure, not a runnable
script."

## Files to modify

| Path                                  | Change                                                                                                  |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `scripts/_lib.sh` *(new)*             | Bash functions: `_with_check`, `_marker_present`, `_replace_between_markers`. Sourced by both scripts.  |
| `scripts/setup-neo.sh`                | Source `_lib.sh`; wrap slow ops with `_with_check`; replace ad-hoc env-var append with marker-aware version. |
| `scripts/setup-studio.sh`             | Same.                                                                                                   |
| `scripts/doctor.sh`                   | Apply M3 (one API call per endpoint).                                                                   |
| `scripts/install-precommit-hook.sh` *(when #002 lands)* | Use the same marker convention so hook re-install is idempotent.                        |
| `README.md`                           | One line under "Setup" noting that re-running setup scripts is safe.                                    |

**Existing primitives to reuse:**

- `set -euo pipefail` — already present; keep.
- The current `probe()` function in doctor.sh — refactor in place; the
  call sites stay identical.

## Implementation order

Three commits, each independently verifiable:

1. **`scripts/doctor.sh` dedup** — smallest, isolated change. Output
   should be byte-identical to today; the only difference is one
   fewer HTTP request per probe. Easy to land and ship.
2. **`scripts/_lib.sh` + `_with_check` adoption** — introduce the
   helper file; convert one slow op (e.g., the `ollama pull devstral:24b`
   in `setup-studio.sh`) as proof-of-concept; verify error path by
   deliberately running with `ollama` absent.
3. **Marker-based idempotence pass** — apply `_marker_present` to the
   `~/.zshrc` append in `setup-neo.sh`. Verify by running setup twice
   in a row on a test machine (or in a tmpdir HOME).

## Verification

| Scenario                                                                  | Expected outcome                                                                        |
| ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Run `setup-neo.sh` twice in a row on the same machine                     | Second run does not duplicate `~/.zshrc` env-var entries; no other state changes        |
| Simulate `uv tool install` failure (e.g., bad pyproject)                  | Script exits non-zero immediately with `ERROR (uv tool install ...)` line                |
| Run `doctor.sh` against a reachable endpoint with two models loaded       | Output unchanged from today; only one `/api/tags` call in `tcpdump`/`-v` curl logging   |
| Run `doctor.sh` against an unreachable endpoint                           | Prints `unreachable`; no error                                                          |
| Run `setup-studio.sh` when `devstral:24b` is already pulled               | `ollama pull` is idempotent (no-op on already-present model); script doesn't re-fetch   |
| Run `setup-neo.sh` on a fresh machine                                     | Works exactly as before — backward compatibility preserved                              |

## Coordination notes

- **#002 (Safety foundation)** owns the pre-commit hook installer. When
  it ships, the hook installer adopts the same `_lib.sh` marker
  convention so re-running setup is safe.

## Non-goals

- **Cross-platform support** (Linux/Windows). Mac assumptions are
  deliberate.
- **Bash → something-else rewrite.** Bash is fine for ~50-line setup
  scripts.
- **A `pxx setup` Python subcommand that supersedes the shell scripts.**
  Adds complexity for no real gain; bash works.
- **Self-update mechanism (`pxx update`).** Separate concern.

## Status updates needed in `backlog.md` when this completes

- `#005` status: `planned` → `in-progress` → `done`
- No cross-plan column changes; if `#002` lands after this and adopts
  the marker convention from `_lib.sh`, note the reuse but no status
  change needed.
