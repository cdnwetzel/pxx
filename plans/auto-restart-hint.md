# Auto-Restart Hint After Self-Edits

> Backlog ID: **008**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **planned**. Blocks: `—`. Blocked by: `—`.
>
> Plan content is design-complete; implementation is now queued only
> behind #004 (which owns the last-seen HEAD record) — #002 (the hook
> installer) landed in commits 22bac57/94da2d0/953b7b6. Standalone
> implementation against today's `#002` machinery is possible if #004
> stays deferred — see "Coordination notes" below for the trade-off.

## Context

`../review/conventions.md` flagged: *"Manual verification is required for
core logic changes (cli.py, endpoints.py) by restarting the session."*

Because pxx is installed `--editable`, edits to `pxx/cli.py` or
`pxx/endpoints.py` change the source immediately but the currently-running
aider session keeps the **old** module objects in memory. The user can
"verify" a change and silently get the previous behavior — a misleading
no-op, not a loud regression. The agent gets fooled too: it can edit
core files, "test" them in the same session, and decide success even
though nothing it just did is loaded.

This is a small papercut today and a much bigger one once #001 dogfooding
starts running autonomous self-edits. The fix is **not** true hot reload
(which would require fragile process introspection) — it's a clear
**prompt at the right moment** that the running session is stale.

## The mechanisms

### M1 — Post-commit notice (in-session)

A git `post-commit` hook (installed by the same installer #002 uses for
the pre-commit hook) checks whether the just-landed commit touched any
"core" file. If yes, write one line to stderr:

```
pxx: this commit modified core pxx modules (cli.py and/or endpoints.py).
     Restart pxx before relying on this session — current process is
     running the old code.
```

"Core" is precisely: `pxx/cli.py`, `pxx/endpoints.py`, and any future
file the maintainer adds to a small constant list. Prompt files
(`pxx/prompts/*.md`) and command files (`pxx/commands/*.md`) are NOT
core — they take effect on aider's next `/clear` or session, not on
process restart. They get a different (softer) hint if any.

### M2 — Next-launch banner

When pxx launches, compare the current `HEAD` to the previous session's
`git_head_sha` (read from #004's audit log). If a core file changed
between them, the launch banner gets a confirming one-liner:

```
pxx: loaded freshly-edited cli.py (commit 957e4d0)
```

This closes the loop: the user gets a "your in-progress session is
stale" warning when the commit lands, and a "you're now on the new
code" confirmation when they restart.

### M3 — Core-file constant

Single source of truth for what counts as "core". Lives in
`pxx/_core_files.py` as a tuple of relative paths. Both M1 (post-commit
hook) and M2 (cli.py banner check) import this list — drift between the
two is impossible.

## Files to modify

| Path                                  | Change                                                                                                                |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `pxx/_core_files.py` *(new)*          | `CORE_FILES = ("pxx/cli.py", "pxx/endpoints.py")` and `is_core(path: str) -> bool`. Pure, no deps.                    |
| `pxx/cli.py`                          | After `_self_sanity_check`, read previous session's `git_head_sha` from #004's log; compare; emit banner if core changed. |
| `.git-hooks/post-commit-template` *(new, in repo)* | Bash template the installer copies into `.git/hooks/post-commit`. Imports `_core_files.py` constants via a small Python invocation. |
| `scripts/install-precommit-hook.sh` *(owned by #002)* | Extended to also install the post-commit template. Runs the same idempotence/marker check.                |
| `tests/test_core_files.py` *(new)*    | Tests for `is_core()` — positive (cli.py, endpoints.py), negative (README.md, prompts/system.md, commands/*.md), edge (absolute paths, relative paths, trailing slashes). |
| `tests/test_cli.py`                   | Test the banner-emit logic: mock previous-session log entry, mock current HEAD, verify banner string.                  |
| `README.md`, `CLAUDE.md`              | Document M1 and M2 behavior.                                                                                          |

**Existing primitives to reuse:**

- `pxx/audit.py` *(from #004)* — provides the previous session's
  `git_head_sha`. Don't duplicate the log read.
- `scripts/install-precommit-hook.sh` *(from #002)* — extend; don't
  fork a second installer.
- Subprocess pattern from `pxx/cli.py:_in_git_repo()` — copy for the
  HEAD-resolution call.

## Implementation order

Two commits, both gated on #002 and #004 being `done`:

1. **`pxx/_core_files.py` + `tests/test_core_files.py`** — pure, no
   caller. Establishes the constant. Independently testable.
2. **Hook + banner wiring** — extend #002's installer with the
   post-commit template; add the banner-emit logic in `cli.py`
   reading from #004's audit log.

## Coordination notes

- **#002 (Safety foundation)** owns the hook installer. #008's
  post-commit hook ships in the same installer to avoid two install
  paths. *Could* be implemented standalone (own minimal installer) but
  doing so duplicates the marker-check logic #002 will have. Cleanest:
  wait.
- **#004 (Session audit log)** owns the previous-session `git_head_sha`.
  M2's banner reads from #004's log. *Could* be implemented standalone
  (one-off `~/.pxx/last-head` file) but that creates a precedent that
  #004 will then have to subsume. Cleanest: wait.

If the user later decides the standalone implementation is acceptable
to ship #008 ahead of #002 / #004, this plan can be revised. The
trade-off is technical debt for time-to-ship.

## Verification

| Scenario                                                                              | Expected outcome                                                                            |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| In an aider session, edit `pxx/cli.py` and commit                                     | Post-commit notice printed; the running session is unaffected mechanically but warned       |
| In an aider session, edit `README.md` and commit                                      | No notice (not a core file)                                                                 |
| In an aider session, edit `pxx/prompts/system.md` and commit                          | No core-file notice; (optional) softer note that prompt changes apply on next session       |
| Edit + commit `pxx/cli.py`; launch a new `pxx` session                                | Banner reads `pxx: loaded freshly-edited cli.py (commit <sha>)`                             |
| Edit + commit a non-core file in session A; launch session B                          | Banner does NOT mention freshly-edited core code                                            |
| Run `pxx` without #004's audit log file present (first invocation ever)               | Banner mechanism silently skips; no error                                                   |
| `is_core("pxx/cli.py")`                                                               | True                                                                                        |
| `is_core("/absolute/path/to/pxx/cli.py")`                                             | True (normalized)                                                                           |
| `is_core("pxx/prompts/system.md")`                                                    | False                                                                                       |

## Non-goals

- True hot reload of pxx's Python code mid-session
- Forcible auto-restart of aider
- Tracking core-file edits across remote (Studio-side) sessions —
  single-session, single-machine focus
- Detecting third-party-package edits as "core" — only pxx's own
  modules count

## Status updates needed in `backlog.md` when this completes

- `#008` status: `blocked` → `planned` → `in-progress` → `done`
  (transition `blocked` → `planned` happens when 002 and 004 are both
  `done`; until then #008 stays `blocked`).
- When `done`, `#001`'s Tier 3 description can mention that the
  auto-restart hint provides additional safety for autonomous self-edits.
