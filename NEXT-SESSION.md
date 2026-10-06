# Next session — working plan

_Handoff pointer, rewritten 2026-10-06. Durable priorities stay in `docs/ROADMAP.md`;
release history in `CHANGELOG.md`. This file says only where things stand._

## State (2026-10-06)

- `v2` is the release branch. **2.6.4 is published** (tag `v2.6.4`, PyPI):
  `Settings.temperature` — a config-only deterministic generation surface
  (strict [0.0, 2.0], unset = key not sent). 2.6.3 carried the bare-mode
  docs + prompt templates (`docs/bare-mode/`) with the restated,
  bounded measurement claim; 2.6.2 carried `bare = true` itself (payload-only
  mode for contract-gated `ask`/`plan`). Read `docs/bare-mode/README.md`
  before citing the conformance numbers — the 2.6.2-era claim was narrowed
  by the 2026-10-05 follow-up arms, and the templates' generalized forms
  are UNVALIDATED.
- ACP (the first bare-mode consumer) is mid temperature experiment: temp-1.0
  strict textual contracts produced repeated byte-level formatting defects
  (casing slips, missing table pipes) caught fail-closed by ACP's gates;
  `temperature = 0` + bare is the candidate deterministic surface. If ACP
  asks for serving-layer structured output next, that's a new design
  conversation, not a bugfix.
- Both gate-softening settings (`hook_denial`, `scope_violation`) are honoured from the user
  config only, never from a repo-local file; `delete_staging` likewise. `bare`,
  `clarity_gate`, and `temperature` are honoured from every layer (they are
  not exec surfaces). `DESIGN.md` states the contracts.
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
- `docs/RECEIPTS.md`'s dated `--extra dev` install commands are stale
  (dependency-groups era) — historical receipts; owner's call whether to
  refresh or annotate.

## Start here

1. `uv run pytest` and `uv run ruff check pxx tests` on a clean checkout of `v2`.
2. Read `CHANGELOG.md` `[2.6.4]` and `DESIGN.md` §tools before touching the broker, the tools or
   `config.py`.
3. Branch from `origin/v2`; one concern per PR; contract changes in the same commit as the code.
