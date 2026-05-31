# Split `cli.py` into modules

> Backlog ID: **013**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: `—`. Blocked by: `—`.

## Context

`pxx/cli.py` has grown to over 850 lines, carrying multiple distinct
responsibilities (Safety, Scoping, Self-modes, Audit, etc.). This refactor
extracts these into focused modules to improve maintainability and testability.

While the original target was <300 lines, the current modularization (M1-M3)
reaches ~520 lines. This is because significant orchestration logic
(_build_aider_args, audit composition, command-listing) remains in `cli.py`
to avoid premature over-abstraction. Further extractions (e.g. to
`pxx/aider_args.py` or `pxx/command_ui.py`) are deferred to follow-up plans.

## The N mechanisms

### M1 — Extract `pxx/safety.py`

Move the #002 surface into its own module:
`create_tag`, `prune_old_tags`, `sanity_check`. `cli.py` re-exports for compatibility.

### M2 — Extract `pxx/self_modes.py`

Move the dogfooding tier surface (`self_test`, `self_lint`,
`extract_self_fix_task`, `determine_session_class`,
`SELF_FIX_DIFF_CAP`) into one module. `cli.py` re-exports for compatibility.

### M3 — Extract `pxx/_git.py`

Centralize git CLI interactions to ensure consistent behavior (timeouts, 
error handling) and provide a stable internal API.

### M4 — Update framing

Update `cli.py`'s top docstring + the Architecture section of `CLAUDE.md` 
to reflect the actual modular composition.

## Verification

| Done? | Scenario | Expected outcome | Result |
| :---: | :--- | :--- | :--- |
| [x] | All tests pass | ✓ 273 green | @ea53a0c |
| [x] | Dogfooding surface intact | `--self-test` etc. work | @ea53a0c |
| [x] | Original surface intact | `pxx` (ask) and `--edit` work | @ea53a0c |
| [x] | Re-exports valid | `from pxx.cli import _self_test` works | @ea53a0c |
| [x] | Rationale documented | Plan explains 520 LOC vs 300 target | @current |
| [x] | Docstrings explain WHY | Modules have high-signal docstrings | @current |
| [x] | Clean lint | `ruff check pxx/cli.py` is green | @current |

## Non-goals

- **No behavior changes.** Same flags, same outputs, same exit codes.
- **Not a rewrite of any individual function.** Pure moves.

## Status updates needed in `backlog.md` when this completes

- `#013` status: `in-progress` → `done`
