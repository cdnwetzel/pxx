# Next session — working plan

_Handoff note, 2026-08-01. Durable priorities live in `docs/ROADMAP.md`; this is
the short "start here next time" pointer. Delete/rewrite freely._

## State at handoff
- On `v2` (clean). **2.3.0 published** to PyPI. This session merged **PRs #8–#16**
  and added receipts **R-008…R-022**. The dogfood hardening batch (#11–#16) is
  released; every dogfood finding is fixed.
- **Improve daemon is LIVE** — LaunchAgent `local.pxx.improve-daemon` runs
  `pxx improve daemon --once` hourly (propose-only, non-mutating; see
  `docs/ops/` + R-022). It accrues *proposals for triage*, not earned-enablement
  counts. `evaluator-defects.json` re-established (empty). `pxx improve readiness`
  is honest → **NOT-READY** (real_runs + human_promotions legitimately unmet).
- **CLI install gotcha:** the PATH `pxx` is the uv-tool `pxx-orchestrator`, a
  published wheel — NOT the repo source. After a release, run
  `uv tool upgrade pxx-orchestrator` or the CLI stays on the old version. Use
  `uv run --extra dev` to exercise repo source directly.
- Two-box rig for real runs: coder `qwen3-coder:30b` on asrock via
  `ssh -L 11435:127.0.0.1:11434 chris@asrock`; judge on the Mac's own Ollama at
  `:11666`. Use the OFFICIAL qwen3-coder (the Unsloth Q3 GGUF won't return
  structured `tool_calls`).

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
