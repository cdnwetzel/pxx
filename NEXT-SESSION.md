# Next session — working plan

_Handoff pointer, rewritten 2026-10-07. Durable priorities stay in `docs/ROADMAP.md`;
release history in `CHANGELOG.md`. This file says only where things stand._

## State (2026-10-07)

- `v2` is the release branch. **2.6.5 is published** (tag `v2.6.5`, PyPI):
  `Settings.chat_template_kwargs` — a config-only passthrough for
  serving-layer chat template switches (strict JSON-safe TOML table, unset =
  key not sent). The motivating case, Nemotron's `enable_thinking = false`,
  was exercised same-day by ACP's reviewer probes. 2.6.4 carried
  `Settings.temperature` (deterministic generation surface); 2.6.3 the
  bare-mode docs + prompt templates; 2.6.2 `bare = true` itself. Read
  `docs/bare-mode/README.md` before citing the conformance numbers — the
  2.6.2-era claim was narrowed by the 2026-10-05 follow-up arms, and the
  templates' generalized forms are UNVALIDATED.
- ACP (the first bare-mode consumer) resolved its contract-reliability arc:
  `temperature = 0` + bare is the locked surface for contract-bound phases
  (temp-1.0 produced repeated byte-level formatting defects, caught
  fail-closed). Its witnessed human VALIDATE was approved 2026-10-07,
  scope-limited to the drill procedure. If ACP asks for serving-layer
  structured output next, that's a new design conversation, not a bugfix.
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
