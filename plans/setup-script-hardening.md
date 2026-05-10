# Setup-Script Hardening

> Backlog ID: **005**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed** (stub). Blocks: `—`. Blocked by: `—`.

## Context

`scripts/setup-neo.sh`, `scripts/setup-studio.sh`, and `scripts/doctor.sh`
were written to work, not to be defensively rerunnable. Two real problems
surface in practice:

- **Partial failures**: if `uv tool install` fails mid-run, the script
  continues and produces a cascade of follow-on errors. The user has to
  read carefully to spot the original failure.
- **Idempotence gaps**: rerunning a setup script on an already-set-up
  machine produces slightly different output paths and a few non-idempotent
  ops (e.g., appending env vars to `~/.zshrc` without a marker check —
  caught and fixed for the pxx env vars earlier this session, but the
  general pattern isn't enforced).

Flagged in `../review/00-init-codebase-notes.md` as a deferred review pass.
It's a quality improvement, not a correctness fix — the scripts work today
on clean machines.

## The mechanisms (sketch — to be expanded when fleshed out)

- **Idempotence pass**: every section of every setup script should be safe
  to run twice. Use marker comments (`# pxx: added by setup-*.sh`) for
  appended sections so reruns detect and skip. The `~/.zshrc` append step
  is the most obvious offender.
- **Fail-fast posture**: top of each script already has `set -euo pipefail`,
  but loops over `ollama pull <model>` can mask individual pull failures.
  Wrap each with explicit error reporting and exit-on-first-failure.
- **`doctor.sh` deduplication**: currently calls `/api/tags` twice per
  endpoint when reachable (probe + model list). One call would do.

## Open questions

1. Should setup scripts write a `# pxx version: X.Y.Z` marker so a future
   pxx version can detect a stale install and prompt for re-setup?
2. Should the env-var append detect existing pxx entries in `~/.zshrc` and
   skip silently, vs. fail loudly with instructions to remove them first?
3. Is `doctor.sh`'s "currently loaded models" line worth its complexity,
   given it only ever reflects local Ollama (not the Studio when running
   from the Neo)?
4. Should setup scripts validate Homebrew is up to date, or accept any
   working brew?

## Verification (placeholders — to be expanded when fleshed out)

| Scenario                                                                  | Expected outcome                                  |
| ------------------------------------------------------------------------- | ------------------------------------------------- |
| Run `setup-neo.sh` twice in a row on the same machine                     | Second run does not duplicate env vars or hooks   |
| Simulate `uv tool install` failure mid-script                             | Script exits non-zero immediately with clear error |
| Run `doctor.sh` against a machine with Ollama installed but no models     | Reports "no models loaded" cleanly                |
| Run `setup-studio.sh` when `devstral:24b` is already pulled               | Skips re-pull cleanly                             |

## Non-goals

- **Cross-platform support** (Linux/Windows). Mac assumptions are deliberate
  for the current hardware. Out of scope for this plan.
- **Bash → something else rewrite** (Python, Just, Make). Bash is fine for
  20-line setup scripts; rewriting would burn complexity budget for no
  user-visible win.
- **Self-update mechanism** (`pxx update`). Separate concern; out of scope.

## Status updates needed in `backlog.md` when this completes

- `#005` status: `proposed` → `planned` → `in-progress` → `done`
- No cross-plan effects expected. If the implementation pass surfaces a
  meaningful interaction with another plan (e.g., #002's hook installer
  shares the same marker convention), update both at that time.
