# Receipts

Every public claim pxx makes, tied to a dated record and a procedure a
stranger can run. Claims are **evidence-gated**: anything not listed here
with a receipt is not claimed. Boundaries — what is explicitly *not*
claimed — are part of each entry.

Two provenance grades are used, and marked on every entry:

- **Reproducible** — you can run the procedure yourself against the
  public repo/package and get the same class of result.
- **Attested** — verified by a maintainer on dated hardware with local
  run records (paths given); the procedure to re-derive it yourself is
  included, but the original artifacts are local telemetry.

---

## R-001 — Tutorial completes 6/6 on 8 GB consumer hardware (local Ollama)

**Claim.** `docs/TUTORIAL.md` — from the scaffolded sandbox at 0/6
failing tests, the documented `pxx` commands reach 6/6 passing plus a
working CLI, on an 8 GB machine with the tutorial's recommended model.

**Grade.** Attested (2026-07-26) + Reproducible (the procedure *is* the
tutorial).

**Environment.** 8 GB Apple-silicon MacBook, macOS; Ollama 0.32.3;
model `qwen3:4b-instruct-8k` (the tutorial's 8 GB path: qwen3
4B-instruct, `num_ctx 8192`, temperature 0.2); pxx 2.1.0 installed from
PyPI via `uv tool install`.

**Record.** Level-by-level, session summary lines as printed:

| Level | Command | Result | Session line |
|---|---|---|---|
| 0 | scaffold + `pytest -q` | 6 failed (exact match to tutorial text) | — |
| 1 | `pxx ask` | correct analysis, tree clean | `rounds=3 tokens=5545 diff_lines=0` |
| 3 | `pxx edit --commit` | 2/6 | `rounds=5 tokens=10239 diff_lines=2` |
| 4 | `pxx edit --commit --scope .` | **first attempt failed** (see boundary), retry → 3/6 | retry: `rounds=4 tokens=7851 diff_lines=2` |
| 5 | `pxx edit` (no commit) + `pxx review` | 5/6; review exit code 2 on REVISE | `rounds=4 tokens=8351 diff_lines=10` |
| 6 | `pxx edit --commit --scope .` | 6/6; `python converter.py 100 C F` → `212.0` | `rounds=4 tokens=9090 diff_lines=11` |

Safety tags (`pxx-pre/<ts>`) were created on every edit session. Local
run records: `~/.local/state/pxx/runs/20260726T15*` (per-round
`events.jsonl`, outcomes).

**Boundary — explicitly not claimed.** Completion is not
single-attempt-deterministic at this model size: Level 4's first session
failed (the model missed its `edit_file` match twice, then wrongly
blamed the environment; `diff_lines=0`, no tree damage) and succeeded on
an identical re-run. 6/6 cost 5 edit sessions, not 4. This failure mode
is documented in the tutorial's troubleshooting and drove two 2.1.1
fixes (`edit_file` retry guidance; tutorial item 3). Models below the
tutorial's recommendations are explicitly outside the claim.

---

## R-002 — pxx invoked from a git hook targets the right repository

**Claim.** As of 2.1.1, every pxx git subprocess (and the agent's
`run_shell` tool, the aider process environment, and the eval harness)
scrubs inherited `GIT_DIR` / `GIT_INDEX_FILE` / `GIT_WORK_TREE` /
`GIT_AUTHOR_*` / `GIT_COMMITTER_*`, so pxx running inside a git hook or
CI step operates on the repository it was pointed at — never the hook
caller's.

**Grade.** Reproducible.

**Why this exists (incident, 2026-07-26).** Before the fix, running the
test suite under a pre-commit hook leaked the caller's `GIT_DIR` into
test scaffolds: a tmp-dir `git init` + `git add -A` re-targeted the
*real* repository and staged deletion of all 192 tracked files (the
working tree survived; recovery was a mixed `git reset`). An independent
adversarial review then proved the same class held for the eval harness
(`pxx eval` path) by live demonstration against a victim repo — that
finding post-dated the original patch plan and is why the harness is in
the fix list.

**Procedure.** On v2.1.1+: run the poisoned-environment regression tests
that pin both incident shapes —

```sh
uv run pytest tests/test_gitenv.py -q
```

To reproduce the original defect, check out tag `v2.1.0` and run the
suite with hook-style env (`GIT_DIR="$PWD/.git"
GIT_INDEX_FILE="$PWD/.git/index" uv run pytest tests/test_goal.py
tests/test_safety_net.py`) — multiple failures; on `v2.1.1` the same
command is green.

**Boundary.** User-configured commands (hooks from user config,
`PXX_TEST_COMMAND`, goal integration commands) and MCP server processes
deliberately inherit the caller's environment — users may legitimately
depend on variables they set themselves. Only pxx-owned git calls and
the model-controlled `run_shell` are scrubbed.

**Write-up.** Full incident narrative:
[postmortems/2026-07-26-git-env-leak.md](postmortems/2026-07-26-git-env-leak.md).

---

## R-003 — Review contract: exit codes and no vacuous verdicts

**Claim.** `pxx review` exits 0 on APPROVE/NO_REVIEW and 2 on REVISE
(scriptable); a REVISE carrying zero evidence-linked findings is
degraded to NO_REVIEW (`review_error="empty"`) rather than blocking —
"never a vacuous APPROVE, never a generic block."

**Grade.** Reproducible.

**Procedure.** `uv run pytest tests/test_review.py -q` (see
`test_parse_findings_less_revise_degrades_to_no_review` and the
exit-code tests). Observed live 2026-07-26: a 4B reviewer emitted a bare
`verdict: REVISE` with no findings on the tutorial sandbox — the exact
input the degrade now handles (pre-2.1.1 this printed an unactionable
verdict; in loop mode it burned healing rounds).

**Boundary.** Verdict *quality* is a property of the configured reviewer
model, not of pxx; small local models produce noisy verdicts. pxx claims
the contract around the verdict, not the verdict itself.

---

## R-004 — Self-improvement is report-and-refuse until earned (visible non-claim)

**Claim.** Auto-promotion of self-improvement candidates is **disabled
by the platform's own readiness bars** and currently refuses:
~~as of 2026-07-26, all four bars unmet — eval_cases 44/50, real_runs
47/100, human_approved_promotions 0/3, unresolved_critical_defects
untracked~~ **updated 2026-07-27**: `pxx improve readiness` reports all
ten preconditions ok, **eval_cases green (50/50)** and
**unresolved_critical_defects green** (ledger established via
`pxx improve defects`; D-001 and D-002 recorded and resolved with
receipt pointers — see R-002, R-006), while **real_runs (~48/100)** and
**human_approved_promotions (0/3)** remain unmet — overall **NOT-READY**.
The two green bars were the last desk-completable ones; the remaining
two fill only through recorded real usage and human promotion decisions.

**Grade.** Reproducible (`pxx improve readiness` on any install; your
counts will reflect your own evidence plane).

**Boundary — explicitly not claimed.** pxx does **not** claim
production-ready autonomous self-improvement. The bars fill only through
recorded real runs and human promotion decisions; until then the
improvement machinery proposes and refuses to self-apply. This section
will be updated when the bars move — the update itself is the receipt.

---

## R-005 — Release gates

**Claim.** Every release passes, in CI on Python 3.11/3.12/3.13: ruff
lint + format, the full test suite, a docs-consistency gate (docs may
not document CLI verbs that don't exist), a secrets/PII scan
(`pxx check --all-files`), and a package smoke gate
(`scripts/smoke-package.sh`: build wheel → install into a throwaway venv
→ assert version, doctor, prompt resources, and that `evals/` is not in
the wheel). Production PyPI publishes are tag-push-only.

**Grade.** Reproducible — the workflows and scripts are in the repo
(`.github/workflows/ci.yml`, `release.yml`); public run history is on
the repository's Actions page. 2.1.1's smoke gate additionally caught a
version-sync miss during independent review (round 2, P1) before the
release was cut.

---

## R-006 — 2.1.2 fixes verified against the original failing sequence

**Claim.** The 2.1.2 reviewer fixes (configurable timeout, never-blank
failure reasons, actionable context-overflow message) resolve the defect
they were cut for — verified 2026-07-27 by re-running the *exact*
sequence that failed on 2.1.1: `pxx review --since <sha>` over a
~930-line security diff on a real multi-tenant FastAPI repository,
8 GB Apple-silicon hardware, local Ollama, qwen3-4B models, pxx
installed from the published PyPI wheel.

**Grade.** Attested (the target repository is private); the procedure
reproduces on any comparable repo and hardware.

**Record — full matrix, same diff and machine as the original failure:**

| Scenario | 2.1.1 behavior | 2.1.2 observed |
|---|---|---|
| default timeout, ~930-line diff | died at 120.36 s, reason **blank** | dies at ~120 s, reason **`ReadTimeout`** |
| 8k-context model, same diff | raw failure | *"review diff exceeds the model's context window — raise num_ctx …"* |
| `PXX_REVIEW_TIMEOUT=900`, same diff | impossible (fixed ceiling) | request **completes at 175.28 s**; 4B model's output unparseable → honest advisory degrade |
| `PXX_REVIEW_TIMEOUT=900`, 47-line diff | would die at 120 s | **`verdict: APPROVE` at 124.15 s** |

The last row is the decisive one twice over: a real usable verdict on
the real repo, from a run that itself exceeded the old ceiling — on this
hardware class even the small-diff happy path needs more than 120 s
(model cold-load + prefill), so the fixed ceiling made `pxx review`
effectively unusable here, and the env override restores it end-to-end.

**Boundary — explicitly not claimed.** A 4B reviewer model does not
produce a parseable verdict on ~930-line diffs (row 3); that is the
reviewer-model-quality boundary already declared in R-003, handled by
the honest degrade, and remediated by narrowing the diff — exactly what
the new overflow/timeout messages advise. Warm-model latencies are
lower; the numbers above include cold loads under memory pressure,
deliberately: that is the hardware class the tutorial targets.

---

## R-007 — The 8 GB standalone coding workstation, and the pxx ⇄ Camelid lane map

**Primary claim.** An 8 GB unified-memory Apple-silicon laptop is a
self-sufficient pxx coding workstation **today** with the verified
configuration: pxx 2.1.2 + Ollama + a qwen3-4B-class model at 8–12k
context — full tutorial 6/6 (R-001), edits and asks end-to-end, reviews
via `PXX_REVIEW_TIMEOUT` on narrowed diffs (R-006). Nothing below
changes that; this entry maps a *second* inference lane explored on the
same hardware class.

**Secondary record — pxx over Camelid v0.4.4** (2026-07-27/28;
Camelid is an independent Rust-native GGUF engine with an
OpenAI-compatible serve; row `qwen3_4b_q4_k_m`, the one its
compatibility ledger marks `tool_capable`; tarball SHA-verified from the
v0.4.4 release). Tested on two machines: the 8 GB laptop (macOS 25.x)
and a 16 GB M4 Mac mini (macOS 26.4.1). Three findings, each isolated:

1. **Default serve panics on macOS ARM64 for this row** —
   `src/inference/metal_resident.rs:27:41: called Option::unwrap() on a
   None value` on the first completion request; deterministic; **identical
   on 8 GB and 16 GB machines** (so not memory-conditioned); the engine
   worker does not recover (subsequent requests 503 in milliseconds).
   Camelid's ledger claims no Metal-resident lane for this row — the
   panic is the "auto-select the safest validated execution plan" path
   routing into an unvalidated lane and unwrapping instead of refusing
   with a typed error.
2. **The deterministic CPU lane works end-to-end with pxx**: a read-only
   pxx session completed the full round trip on both machines
   (~1.2 tok/s on the 8 GB laptop; ~8.7 tok/s on the M4 — parity-lane
   speeds, per Camelid's own docs not a performance lane).
3. **The OpenAI `tools` surface does not execute tools in v0.4.4**:
   the parameter is accepted, the model emits a correct Qwen-native
   `<tool_call>{"name": …}</tool_call>` block — and the serve layer
   returns it verbatim as `content`, with `tool_calls` always null
   (isolated with a direct curl carrying an explicit tools array).
   Agent loops driving `/v1/chat/completions` therefore cannot execute
   tools against this build. **This does not contradict Camelid's
   `tool_capable` receipt**, which was earned through its own agent
   lane and parser; the OpenAI tool surface is simply an unclaimed lane,
   now mapped.

**Boundary — explicitly not claimed.** No Camelid parity or quality
claims (its ledger owns those); pxx tutorial 6/6 *over Camelid* was not
achieved — blocked at finding 3, not at the model or the hardware; the
8 GB standalone claim is exactly the R-001 configuration, nothing
broader. Findings 1 and 3 are reported upstream; this entry updates
when the lanes change.

**Grade.** Attested (two role-described machines); every probe command
is reproducible against the public Camelid v0.4.4 release.

---

*Convention: entries are append-only and dated; superseded claims are
struck through with a pointer to the superseding entry, never deleted.*
