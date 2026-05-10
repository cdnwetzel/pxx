# Safety Foundation

> Backlog ID: **002**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed** (stub). Blocks: 001. Blocked by: —.

## Problem

Before pxx can safely edit its own source (dogfooding, ID 001) or any valuable
real codebase in `--edit` mode, we need a safety net that doesn't depend on the
user remembering to do the right thing. Today:

- aider auto-commits in code mode, but there's no automated revert when tests
  fail or syntax breaks after a commit
- there's no per-session tag for one-command rollback to the pre-session state
- aider's `lint-cmd` / `test-cmd` are run *after* edits but don't block commits
  on failure
- there's no per-session diff cap, so a runaway change can land 1000 lines of
  damage before anyone notices

The goal: every `pxx --edit` session should be **completely reversible** to the
state at session start, and obviously-broken edits should be blocked before
they commit.

## Capabilities to design

- **Pre-session git tag**: `pxx --edit` creates `pxx-pre-<unix-ts>` tag at the
  current HEAD before launching aider. If uncommitted changes exist, stash
  them under a labeled stash too. Document the one-command undo.
- **Pre-commit hook**: installed by `setup-*.sh`; runs
  `uv run pytest -q && uv run ruff check`. Failing test or lint blocks the
  commit. Use the project's pinned versions, not whatever's on PATH.
- **Circuit breaker**: after each aider commit, run `python -m compileall .`
  (or equivalent for non-Python files in scope) on edited files. Syntax
  break → loud warning; default = halt session, optional auto-revert of the
  last commit only.
- **Per-session diff cap**: refuse to commit a single change >N lines without
  `--big`. Default N = 100.

## Open questions

1. Where does the pre-commit hook live — per-project or copied by setup
   scripts to every repo where pxx is used? Probably per-project, opt-in via
   `pxx --install-hook` or similar.
2. Diff cap default — 100 lines? Configurable via env var?
3. Circuit breaker behavior — auto-revert or just halt-and-report?
   Auto-revert is bolder; halt-and-report is more conservative for a first
   implementation.
4. Pre-session tag retention — keep all of them, or garbage-collect tags
   older than N days?
5. Interaction with the dual-remote workflow — pre-session tags should NOT
   be pushed to `origin`/`PS`; they're local recovery markers. Use a
   `pxx-pre/` namespace and document.

## Non-goals

- Sandboxed bash execution (over-engineered for a personal tool).
- Replacing git as the source of truth for history — we wrap it, not
  replace it.
- Multi-user / concurrent-session locking (single-developer tool).

## Verification (when implementation starts)

- Break a test deliberately on a branch; pre-commit hook must reject the
  commit.
- Run a session that ends with a syntax error; circuit breaker must halt
  before the next edit.
- After a session, `git reset --hard pxx-pre-<ts>` must restore the exact
  pre-session state (working tree + index).
- Confirm pxx-pre/* tags are not pushed by `git deliver`.
