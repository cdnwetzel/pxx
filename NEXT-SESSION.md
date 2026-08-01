# Next session — working plan

_Handoff note, 2026-08-01. Durable priorities live in `docs/ROADMAP.md`; this is
the short "start here next time" pointer. Delete/rewrite freely._

## State at handoff
- On `v2` (clean). **2.2.0 published** to PyPI. This session merged **PRs #8–#13**
  and added receipts **R-008…R-018**.
- Full zero-intervention dogfood done (pxx on itself + the live SaaS
  `../workorder_wizard/`). Every finding is fixed or honestly tracked.
- Two-box rig for real runs: coder `qwen3-coder:30b` on asrock via
  `ssh -L 11435:127.0.0.1:11434 chris@asrock`; judge on the Mac's own Ollama at
  `:11666` (gemma2:9b / qwen3.5:9b). Use the OFFICIAL qwen3-coder (the Unsloth
  Q3 GGUF won't return structured `tool_calls`).

## Recommended order next time
1. **Reliable reasoning judges (blocking gate).** A structured / grammar-
   constrained verdict contract so a reasoning judge (qwen3.5-class) can gate a
   `--review-mode blocking` loop deterministically. R-012/R-015 evidence:
   qwen3.5 intermittently emits no parseable `VERDICT:` line → today it's only
   trustworthy in `advisory`. Test on the two-box rig.
2. **F-1 durability (evidenced ledger).** `real_runs` is still a live
   `iterdir()`; an external state-dir clear regressed it ~48→17. Persist an
   append-only, evidenced ledger of genuine run IDs so earned progress survives
   run-dir rotation. `autopromote.py` is protected control plane → human-gated.
3. **F-2 clarity-gate false-positive.** `clarify.ready_to_act` refuses any
   edit-verb task naming a `*.ext` token absent under cwd, even when it's a
   runtime/generated file only described. Distinguish "edit THIS existing file"
   from a descriptive / to-be-created reference. Protected → human-gated.
4. **Stand up the improve daemon** (ops, needs approval). Status now honestly
   reads `daemon: stopped`; nothing accrues earned-enablement until it runs.
   An hourly `pxx improve daemon --once` launchd job (propose-only, safe). Then
   `unresolved_critical_defects` also needs its `evaluator-defects.json` ledger
   re-established (it was cleared out-of-band).

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
