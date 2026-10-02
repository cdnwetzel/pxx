# Next session — working plan

_Handoff pointer, rewritten 2026-10-02. Durable priorities stay in `docs/ROADMAP.md`;
release history in `CHANGELOG.md`. This file says only where things stand._

## State (2026-10-02)

- `v2` is the release branch. **2.6.1 is published** (tag `v2.6.1`, PyPI): `scope_violation =
  "refuse_tool"` (#86), `delete_file` as a staged move (#87), the test suite isolated from the
  operator's config (#88), greptile removed (#89). 2.6.0 (2026-09-23) carried the sandboxed loop
  test, the loop's own run record, `hook_denial = "refuse_tool"` and bounded transport retry.
- Both gate-softening settings (`hook_denial`, `scope_violation`) are honoured from the user
  config only, never from a repo-local file; `delete_staging` likewise. `DESIGN.md` states the
  contracts.
- CI: ruff, `ruff format --check`, pytest on 3.11/3.12/3.13, package smoke, governance scan.
  CodeRabbit reviews every PR. Squash-merge with the `(#NN)` suffix; annotated `vX.Y.Z` tag on
  the merge commit; `release.yml` verifies, publishes and makes the GitHub release.

## Open

- Issues #32 (`pxx run` never executes `test_command`, filed on 2.3.2) and #33 (BUDGET_EXCEEDED
  mislabel): both open, neither re-tested on 2.6.x. `pxx loop` does run the gate.
- The earlier F7 question (a COMPLETED run over a dirty tree leaves the user's WIP in a stash)
  was never closed here; check `pxx/safety_net.py` before assuming either way.
- Suite-wide isolation now covers `HOME`, `XDG_*`, `PXX_*` and the user config/env paths; any new
  code path that reads the operator's files should be added to `tests/conftest.py`'s fixture.

## Start here

1. `uv run pytest` and `uv run ruff check pxx tests` on a clean checkout of `v2`.
2. Read `CHANGELOG.md` `[2.6.1]` and `DESIGN.md` §tools before touching the broker, the tools or
   `config.py`.
3. Branch from `origin/v2`; one concern per PR; contract changes in the same commit as the code.
