# Split `cli.py` into modules

> Backlog ID: **013**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **in-progress**. Blocks: `—`. Blocked by: `—`.
>
> Stub drafted in response to Claude review finding **F-008** (sixth pass,
> 2026-05-13). The cli.py docstring still says *"detect endpoint, pick model,
> exec aider"* but the file has grown to ~750 lines spanning eight distinct
> responsibilities.

## Context

`pxx/cli.py` started as a thin orchestrator over aider. After #002 (safety
foundation), #003 (scoping & dry-run), #004 (audit log), and the three
dogfooding tiers #010/#011/#012, the file now contains:

1. Endpoint + model selection (the original)
2. Aider arg construction (`_build_aider_args`)
3. Safety foundation (`_create_safety_tag`, `_prune_old_safety_tags`, `_self_sanity_check`, `_git_dirty`, `_has_commits`)
4. Scope handling glue (`_write_scope_context`, `_git_repo_root`, `_git_head_sha`)
5. Self-modes (`_self_test`, `_self_lint`, `_self_improve_setup`-implicit, `_self_fix` extraction + diff cap)
6. Audit log composition (record-building before `os.execv`)
7. Trusted-paths gate
8. Command-listing helpers (`_write_commands_context`, `_print_command_listing`)

The file's opening docstring (*"detect endpoint, pick model, exec aider"*)
and CLAUDE.md's *"thin Python CLI wrapper"* framing both undersell what's
actually there. Reviewers (Claude pass 6, Codex pass 8) note this as
recurring drift. The longer the framing-vs-reality gap, the harder it gets
to introduce the next mode without piling onto the same file.

## The N mechanisms

### M1 — Extract `pxx/safety.py`

Move the #002 surface into its own module:
`_create_safety_tag`, `_prune_old_safety_tags`, `_self_sanity_check`,
`_git_dirty`, `_has_commits`, `SAFETY_TAG_PREFIX`,
`SAFETY_TAG_RETENTION_DAYS`. `cli.py` imports and composes.

### M2 — Extract `pxx/self_modes.py`

Move the dogfooding tier surface (`_self_test`, `_self_lint`,
`_extract_self_fix_task`, `_determine_session_class`,
`SELF_FIX_DIFF_CAP`, the `_self_improve` prompt-loading glue) into one
module. `cli.py` imports and dispatches.

### M3 — Update framing

After the moves, rewrite `cli.py`'s top docstring + the Architecture
section of `CLAUDE.md` to reflect the actual composition (orchestrator +
3-4 sibling modules: endpoints, scope, safety, self_modes, audit). One
truthful description, not two stale ones.

## Verification

| Scenario                                  | Expected outcome                          |
| ----------------------------------------- | ----------------------------------------- |
| All 252 existing tests still pass         | No behavior change                        |
| `pxx --self-test` / `--self-lint` / `--self-improve` / `--self-fix` still work | Tier 1/2/3 surface intact |
| `pxx` (ask) and `pxx --edit` still work   | Original surface intact                   |
| `cli.py` LOC drops to under 300           | Refactor actually trimmed it              |
| New module docstrings explain WHY they're separate | Not just relocated text          |

## Non-goals

- **No behavior changes.** Same flags, same outputs, same exit codes.
- **No public API renames.** `from pxx.cli import _self_test` should still
  work if any caller relied on it; re-export from `cli.py` if needed.
- **Not a rewrite of any individual function.** Pure moves.

## Open questions

1. **Where do shared git helpers go?** `_git_dirty`, `_git_repo_root`,
   `_git_head_sha`, `_has_commits` are used by both safety.py and audit.py.
   Options: keep in `cli.py`, hoist to a new `pxx/_git.py`, or duplicate.
   Recommend: `pxx/_git.py`.
2. **`_build_aider_args` placement.** Stays in `cli.py` as the core
   orchestrator function, or moves to a new `pxx/aider_args.py`?
   Recommend: stays in `cli.py` — it's the synthesis point.
3. **Should this wait?** If #014 (docstring convention) lands first, the
   move can apply the new convention as it goes — one less drift class to
   introduce.

## Status updates needed in `backlog.md` when this completes

- `#013` status: `proposed` → `planned` → `in-progress` → `done`
- No other plans' "Blocks"/"Blocked by" columns change.
