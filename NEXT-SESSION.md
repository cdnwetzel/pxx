# Next session — working plan

_Handoff note, updated 2026-08-02 (end of a long two-session day). Durable
priorities in `docs/ROADMAP.md`; this is the "start here next time" pointer._

## State at handoff (2026-08-02 stand-down)
- On `v2` (clean, CI green). **2.3.1 published** to PyPI (the portable-box
  degrade batch: BUG A/B, DF-02, tokens=0, PXX_BACKEND — PRs #17–#21).
- **UNRELEASED on `v2` (past 2.3.1)** — neo's three R-023 findings, all merged:
  - **#24 F1** — safety net restores the tree (incl. UNTRACKED) on abort (was
    data loss). CodeRabbit caught a privacy leak in the first cut (restore ran
    before the run artifact was written) — fixed.
  - **#25 F3** — a 404 / model-not-found advances the `[[fallback_models]]`
    chain instead of hard-failing (closes R-023 boundary b).
  - **#26 F5** — read/write scope split: reads span the repo root, writes stay
    in `--scope`. Fixes single-file scope + the dogfood OUT_OF_SCOPE failures.
    CodeRabbit caught a symlink escape in the search fallback — fixed.
- **NEXT UP (approved sequence):** DF-03 review toggle (`PXX_LOOP_REVIEW` /
  add `--no-review`; keep opt-in default) + provider-aware token budget (local
  ollama/vllm get a high/None max_tokens; paid keeps the cost cap) → then cut
  **2.3.2** → `uv tool upgrade` neo → neo runs the **auto-lane degrade sequel**
  (PXX_BACKEND pinned) → author its receipt.
- **Follow-ups logged (not yet done):** `__pycache__/*.pyc` counted as
  OUT_OF_SCOPE by the changed-path guard (should ignore gitignored/build
  detritus); F2 (`pxx doctor` should probe tool-calling under a REALISTIC
  context — a toy probe lies; also fix `scratchpad/pxx-portable-setup.sh` which
  uses a toy probe + recommends qwen2.5-coder/instruct that fails on 8GB — use
  `qwen3:4b-instruct-8k`); F6 (stale user-config model id).
- **CLI gotcha (still true):** PATH `pxx` = uv-tool `pxx-orchestrator` (a
  published wheel, currently 2.3.1) — NOT repo source. `uv tool upgrade` after a
  release; `uv run --extra dev` to exercise the branch.

## NEW strategic thread (awaits the human — do NOT auto-start)
A third agent (Kimi) drafted **"VIOLOOP vs pxx-dispatch"** + a
**"screen-vision roadmap"**; neo VERIFIED the assessment (~22 claims vs tree,
3 errata: 23 not 21 terminal codes; PXX_BACKEND exists as of 2.3.1;
vision-text-injection posture needs a probe receipt). Source + neo's verified
assessment are in `/tmp/pxx/neo/` (see `00-INDEX.md`). Phase 0 spike is
bridge-side only (no collision with pxx-core). **Awaiting the human's word.**

## Two-session workflow (mini + neo)
- **mini** (Mac Mini, this session) = integrator/lead: owns `v2` merges, decides
  order, hand-codes/dogfoods, authors receipts, waits for CodeRabbit before
  merging (it caught 2 real security bugs today).
- **neo** (8GB MacBook, Fable 5) = testing/degradation/hardware lane. Coms are
  FILE-based: neo→mini in `/tmp/pxx/neo/`, mini→neo in `/tmp/pxx/mini/`. neo is
  push-frozen on `v2` (feeds receipts/branches only). neo wheel = 2.3.1.

## Feedback from the portable box (2026-08-02)

Exercised the "Portable / single-box degrade" pattern (docs/CONFIG.md) for real
on the 8GB portable box with the GPU-box primary unreachable. Receipts:

- **BUG — backend auto-pick voids the degrade pattern.** `_resolve_backend_name`
  (cli.py, `ask`/`edit`/`plan`/`chat` lane) picks aider whenever the binary
  passes `--version` — a health check that proves nothing about the endpoint
  chain. The aider backend never consults `[[fallback_models]]`: with the
  primary dead it sat in litellm 32s retries (83s wall, no fallback), while
  `--backend native` on the identical config degraded cleanly (one "endpoint
  unreachable; falling back" line, local model answered). `run`/`loop` already
  force native; the auto lane should prefer native too, or the aider lane must
  honor the fallback chain.
- **BUG — truthfulness: the failed aider run exited `[COMPLETED]`** with
  summary "The API provider's servers are down or overloaded." and tokens=0.
  Same family as the 2.1.7 phantom upgrade claim; should map to
  MODEL_UNAVAILABLE (or similar), never COMPLETED.
- **Gap:** backend is only selectable per-invocation (`--backend`); no config
  key exists, so a box can't declare a durable backend posture.
- Repro for both bugs: install the `aider` extra, point the primary model at an
  unreachable endpoint, run `pxx ask`.
- Local mitigations applied on the portable box (not code): aider extra pruned
  from the repo venv (`uv sync --exact --extra dev`), uv-tool wheel upgraded
  2.1.7 → 2.3.0. Repo-local `.pxx/config.toml` now carries the documented
  degrade config (GPU-box primary, on-device fallback).
- DF-01 (WORKFLOW `[commands] test` → `run_loop`) survived the ff to f592975 —
  re-merged into the new `--review` loop_kwargs, cli+loop suites green, still
  awaiting human commit on `dogfood/phase0`.

## Transferred DF work orders (2026-08-02)

Ownership of the remaining Phase 0 dogfood orders moves to the primary
session (they live in the portable box's gitignored `.pxx/review/`, so full
text is relayed here). DF-01 landed in 0b31b4d (see feedback section above).

### DF-02 — `--budget-rounds` can raise the loop's round cap (OPEN, valid)

- base_sha: 0b31b4d (DF-01 merged). Scope: `pxx/cli.py` + `tests/test_cli.py`;
  `pxx/loop.py` unchanged (default stays 3). Diff ≤ 80 lines.
- **Bug [P1]:** `run_loop` defaults `max_rounds=3` and `_cmd_loop` never
  passes it. `--budget-rounds N` only tightens BudgetGuard, so a loop needing
  4+ genuine heal rounds always exits ROUND_CAP at 3.
- **Fix:** in `_cmd_loop`, pass `max_rounds=args.budget_rounds` to `run_loop`
  when the flag was provided; otherwise omit it. `WORKFLOW.md`
  `budgets.max_rounds` still clamps via BudgetGuard — no change there.
- **Test:** monkeypatch `pxx.loop.run_loop`, capture kwargs:
  `pxx loop --budget-rounds 8` passes `max_rounds=8`; without the flag,
  `max_rounds` absent.

### DF-03 — reviewer leg on the loop CLI path (NEEDS RECONCILIATION)

- Original order [P1 FAIL-OPEN]: `_cmd_loop` passes `reviewer=None`, so
  `pxx loop` completes on green tests alone; fix was reviewer ON by default
  (`NativeReviewer(settings.model)`, BLOCKING) with a loud `--no-review`
  opt-out.
- **Superseded in part by 2.3.0:** the loop parser now has `--review` /
  `--review-mode` (opt-IN, `settings.effective_review_model`, blocking
  default, #14/f175434). The reviewer leg is reachable but the default
  remains review-off — the opposite posture from the order's spec.
- Owner's decision: keep opt-in (close DF-03 as redesigned, mirroring the
  py313 closure pattern) or flip the default to review-on + `--no-review`
  per the original fail-open argument. Don't implement against the stale
  spec (`settings.model` is also superseded by `effective_review_model`).

## Recommended order next time
1. **Earn the bars.** real_runs needs 100 genuine `pxx` agent runs (the daemon
   does NOT move it — it only writes proposals); human_approved_promotions needs
   3. Triage the daemon's inbox: `pxx improve status` → `inbox human-review-required`,
   then promote the good ones. This is the last thing between the platform and
   READY.
2. **`pxx improve defects init`** — small first-class idempotent command to
   create/verify the empty critical-defects ledger (direct-written this session).
   Ship with a test + a receipt; land in a 2.3.1.
3. **Clean up stale `PXX_*` env vars** in the user's shell profile
   (`PXX_REVIEW_URL`, `PXX_REVIEW_BACKEND`, `PXX_VLLM_URL`, `PXX_VLLM_MODEL`,
   `PXX_REPORT_*`, `PXX_CLAUDE_BIN`, `PXX_OPENCLAW_BIN`) — pxx consumes none
   (warns on each), so the intended two-box review routing likely isn't applied.
   Correct keys: `PXX_REVIEW_MODEL` / `PXX_REVIEW_BASE_URL`.
4. **Live eval arms** (ArmRunner seam, `improve/candidate_eval.py`) on the real
   two-box endpoints; **Camelid/NanoCamelid** reference-inference integration
   (design notes already in ROADMAP/DESIGN).

## Standing constraints
- **Standard:** the timtoole02 (Camelid/NanoCamelid) receipts bar — exact-config
  claims (nothing inherits), negative results as first-class receipts,
  reproduction paths, results ledgers. Keep RECEIPTS.md append-only + dated.
- **Before any version bump/branch:** `git fetch` + rebase onto `origin/v2`
  first (local can trail remote), then bump from the true latest.
- **Pre-flight lint:** run BOTH `ruff check` and `ruff format --check` — the
  release `verify` gate runs both (PR CI now does too, since #9).
- Every fix ships with tests + a receipt; validate on real hardware where the
  claim is behavioural.
