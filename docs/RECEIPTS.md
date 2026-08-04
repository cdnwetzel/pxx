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
~~**real_runs (~48/100)**~~ ~~**unresolved_critical_defects green**~~
**updated 2026-08-01 (dogfood R-013/R-014):** the state dir was cleared
out-of-band since — `real_runs` (a live subdir count of `runs/`) was
**16/100** at the R-013/R-014 snapshot (it then moved to **17/100** after the
R-015 loop run — the count is live and monotone within a session), and
`evaluator-defects.json` is absent so
**unresolved_critical_defects is UNMET** (fails closed) again. eval_cases
stays green (50/50); human_approved_promotions 0/3. Overall still NOT-READY.
The `real_runs` counter is a live `iterdir()` with no durability or guards —
see the finding in R-014.

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

## R-008 — The reviewer/judge can run on a different endpoint than the coder

**Claim.** With `[roles.review]` (or `PXX_REVIEW_*`) set, the reviewer/judge
issues its `/v1/chat/completions` request to the review endpoint while the
coder keeps its own; with the overlay unset, the reviewer uses the coder
endpoint — a run is byte-identical to before the field existed. This is the
config seam under the ROADMAP "model-backed boundary roles" item: a two-box
setup (e.g. a GPU-box coder + a Mac judge) is expressible in config alone.

**Grade.** Reproducible (procedure below, no external services or models).

**Procedure.** Run two stub OpenAI-compatible servers that log which port a
POST hits — port A ("coder") and port B ("reviewer") — each returning
`VERDICT: APPROVE`. In a repo with an uncommitted diff:

1. `PXX_PROVIDER=openai-compatible PXX_BASE_URL=http://127.0.0.1:A
   PXX_REVIEW_BASE_URL=http://127.0.0.1:B pxx review` → the **reviewer** port
   (B) receives the request; `verdict: APPROVE` is printed (full round trip).
2. `PXX_PROVIDER=openai-compatible PXX_BASE_URL=http://127.0.0.1:A pxx review`
   (no overlay) → the **coder** port (A) receives the request.

**Observed (2026-08-01).** Exactly one hit per run: run 1 → `HIT reviewer`,
run 2 → `HIT coder`; both parsed `verdict: APPROVE`. Config resolution is
pinned by nine tests in `tests/test_config.py` (`test_roles_review_*`,
`test_review_model_defaults_to_none_and_effective_falls_back`,
`test_unknown_role_rejected`, …); full suite 995 passed / 2 skipped.

**Boundary — explicitly not claimed.** ~~No claim yet of a live two-box run
with production models over the network (that lane, incl. the SSH-tunnelled
GPU coder + Mac judge, is future work)~~ — **superseded: the live two-box run
is recorded in R-011.** The R-007 Camelid lanes are unchanged; only the
reviewer role is wired through the runtime so far (coder/planner role overlays
are not yet claimed); no quality/speed claim about any model.

---

## R-009 — `pxx loop --review` wires a model-backed judge into the edit loop

**Claim.** `pxx loop --review` enables the review gate the bounded loop
otherwise skips, constructing the reviewer from `effective_review_model` —
so the judge can run on a different local model/endpoint than the coder. A
real local model serves as that judge and returns a structured verdict.

**Grade.** Reproducible (wiring + config) + Attested (the real-hardware judge
verdict, on dated tunnelled hardware).

**Wiring — Reproducible.** Four tests in `tests/test_cli.py`
(`test_loop_review_flag_wires_reviewer_with_effective_model`,
`test_loop_review_advisory_mode`,
`test_loop_without_review_flag_passes_no_reviewer`,
`test_loop_review_falls_back_to_coder_model_when_no_overlay`) pin that
`--review` hands `run_loop` a `NativeReviewer` built from
`effective_review_model` with the selected `ReviewMode`, and that omitting the
flag hands it no reviewer (the gate stays skipped — unchanged default).

**Real-hardware judge — Attested (2026-08-01).** Over an SSH tunnel
(`ssh -L 11435:127.0.0.1:11434 asrock`) to a Ryzen 9 5950X + RTX 5060 Ti
16 GB box (Gentoo, Ollama 0.30.5), `pxx review` with
`PXX_REVIEW_MODEL=gemma2:9b` (`PXX_BASE_URL` on the tunnel) reviewed a real
4-line diff and returned `verdict: APPROVE` — a real local model driving the
same `NativeReviewer` the loop wires. The coder endpoint answered a live
`qwen2.5:14b` completion over the same tunnel.

**Autonomous lane — Attested (2026-08-01).** Initially the review gate (which
fires only after a successful edit round) was unreachable because the *instruct*
models on the box (`qwen2.5:14b/7b-instruct`, `gemma2:9b`) did not drive pxx's
native tool-call loop to an edit (a run hit `BUDGET_EXCEEDED` with an empty
diff). Resolved once a **coder-tuned** model was installed: with
`PXX_MODEL=qwen3-coder:30b` (official Ollama build) as the coder and a review
endpoint as the judge, `pxx loop --review --review-mode blocking` on the
`add(a,b)` sandbox task **edited `calc.py` (+3), the review gate fired in-loop,
and the run returned `[COMPLETED] … (verdict APPROVE)` in one round** — the full
edit→judge→complete lane, unattended. See R-010 for why the coder model choice
(and its serving template) is load-bearing here.

**Boundary — explicitly not claimed.** No model quality/speed claim; the
sandbox task is trivial (one function). qwen3-coder Q4 runs partially on CPU on
this 16 GB card (see R-010) — a speed characterisation, not a correctness one.

---

## R-010 — Coder-role model must tool-call through the OpenAI API; GGUF template decides it

**Claim.** For the pxx coder role over an OpenAI-compatible Ollama endpoint,
what matters first is not size or speed but whether the served model returns
**structured `tool_calls`** — and that depends on the GGUF's chat/tool
template, not the weights. The official `qwen3-coder:30b` does; the Unsloth
`UD-Q3_K_XL` GGUF of the same model does not (it returns the tool call as a
`<tools>…</tools>` text block with `tool_calls: null`), so pxx never executes
it and the loop no-op-completes.

**Grade.** Attested + Reproducible (2026-08-01; Ryzen 9 5950X + RTX 5060 Ti
16 GB, Gentoo, Ollama 0.30.5; probes are plain `curl` to
`/v1/chat/completions` with a one-tool array).

**Measured (same box, via the SSH tunnel).**

| Model / quant | Size | tok/s | GPU placement | `tool_calls`? |
|---|---|---|---|---|
| `hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q3_K_XL` | 13 GB | 63.8 | 100% GPU | **no** (`<tools>` as content) |
| `qwen3-coder:30b` (official Q4_K_M) | 18 GB | 40.6 | 23%/77% CPU/GPU | **yes** (`finish_reason: tool_calls`) |
| `qwen2.5:7b-instruct` (control) | 4.7 GB | 51.6 | 100% GPU | **yes** |

The control (`qwen2.5:7b`) proves Ollama's OpenAI tool bridge itself works on
this version — so the Unsloth-GGUF failure is a template/parser gap, not an
engine bug. This is the same *class* as R-007 finding 3 (a correct tool call
emitted as prose the serving layer doesn't lift into the structured field),
here reproduced for Ollama + an Unsloth GGUF.

**Consequence for pxx.** The faster, fully-resident Q3 is unusable as the
agentic coder; the official Q4 is the coder despite partial CPU offload (its
MoE 3B-active keeps it at ~40 tok/s). pxx's prose-tool-call detector
(`native.py`) catches `<tool_call>`/bare-JSON shapes but not `<tools>`; teaching
it that shape (parse-and-nudge, never execute) is the pxx-native mitigation if a
non-official GGUF must be used.

**Boundary — explicitly not claimed.** No coding-quality ranking between quants;
tok/s are single-prompt samples, not a throughput benchmark; the Q3's `<tools>`
behaviour is specific to that GGUF + Ollama 0.30.5 and may change with either.

---

## R-011 — True two-box run: GPU-box coder + Mac judge, one autonomous loop

**Claim.** A single `pxx loop --review` runs the coder and the judge on two
**physically distinct machines** — the coder-tuned model on an Nvidia GPU box,
the review/judge model on an Apple-Silicon Mac — and completes the
edit→judge→approve lane unattended, fully local (no cloud). This is the
per-role routing of R-008 exercised across real hardware.

**Grade.** Attested (2026-08-01, two role-described machines).

**Setup.** Coder = `qwen3-coder:30b` (official Ollama) on the Ryzen 9 5950X +
RTX 5060 Ti 16 GB box, reached from the Mac over an SSH tunnel
(`PXX_BASE_URL=http://localhost:11435`). Judge = `gemma2:9b` on the M4 Mac
mini's **own** Ollama (`PXX_REVIEW_BASE_URL=http://localhost:11666`, a distinct
local instance serving only that model — not the tunnel). One command:
`pxx loop --review --review-mode blocking` on the `add(a,b)` sandbox task.

**Observed.** `[COMPLETED] completed in 1 round(s) (verdict APPROVE)`;
`calc.py` edited (+5, coder added a docstring unprompted). The Mac Ollama
access log recorded the judge call — `POST "/v1/chat/completions" 200` in
8.4 s, prompt eval 510 tokens @ ~170 tok/s, 12 tokens generated — with
`gemma2:9b` resident **100% GPU (Metal), 6.3 GB**. The coder generations were
served by the GPU box over the tunnel. Two machines, ~$1,200 of consumer
hardware total, no cloud dependency.

**Reasoning judge variant — Attested (2026-08-01).** The same two-box loop was
re-run with a **reasoning** judge on the Mac (`qwen3.5:9b`, resident 100% Metal,
5.6 GB) instead of gemma2. The Mac served the review in 41.0 s generating **614
tokens** (vs gemma2's 12) — i.e. it reasoned at length — and pxx parsed
`verdict APPROVE` from the final answer via the new `<think>`-stripping parser;
the loop `[COMPLETED]` in one round. Confirms reasoning judges work end-to-end,
at a real latency cost (thorough but ~40 s/review here vs ~8 s).

**Boundary — explicitly not claimed.** Trivial task (one function); no
quality/latency claim (the cross-machine review adds a network hop and a Mac
model load); the coder still runs partially on CPU on the 16 GB card (R-010).
The point proven is *topological* — role routing places coder and judge on
separate local machines in one loop — not a performance result.

---

## R-012 — Clean autonomous completion with qwen3-coder: the tuning that matters

**Claim.** A qwen3-coder `pxx loop` completes cleanly (one round, `COMPLETED`)
when given (a) an adequate round budget, (b) a test command as the done-signal,
and (c) bytecode hygiene — and a *format-reliable* judge for blocking review.
The earlier `BUDGET_EXCEEDED` runs were a starved budget, not a stuck coder.

**Grade.** Attested (2026-08-01, two-box: RTX 5060 Ti coder + M4 Mac judge).

**Root cause (from the run record).** The `rounds` budget is consumed **per
native model turn**, not per outer loop iteration. qwen3-coder verifies its own
edit with extra tool calls — one trivial `add()` task took **6 model turns**
(5 tool-calls, then a natural `finish_reason: stop`). `--budget-rounds 5` cut it
off at turn 6, recording `BUDGET_EXCEEDED rounds=0`. It stops on its own given
room; the default budget (25) is ample.

**Two more gotchas found.** (1) Running `python3 check.py` writes
`__pycache__/*.pyc`, which churns the diff/scope each round — suppress with
`PYTHONDONTWRITEBYTECODE=1` and a `.gitignore`. (2) A **reasoning** judge
(`qwen3.5:9b`) is intermittently format-non-compliant — one run blocked with
`REVIEW_UNPARSEABLE` (no parseable `VERDICT:` line; not a `<think>` case the
parser handles). For a *blocking* gate use a format-reliable judge (gemma2 here
returned `VERDICT: APPROVE` deterministically) or run the reasoning judge in
`--review-mode advisory`.

**Observed clean run.** `PXX_MODEL=qwen3-coder:30b` (coder, tunnelled GPU),
`PXX_REVIEW_MODEL=gemma2:9b` (judge, Mac), `PXX_TEST_COMMAND="python3 check.py"`,
default budget, `--review-mode blocking` → `[COMPLETED] … (verdict APPROVE)` in
one round, `calc.py` +4, no stray files.

**Boundary — explicitly not claimed.** Trivial task; reasoning-judge
non-compliance is intermittent (not every run) and model/version specific. The
`MemoryStore.add` "coroutine never awaited" `RuntimeWarning` was observed
during these runs (a separate pre-existing defect) — **since fixed in 2.2.0**
(the un-awaited `add`/`search` in the memory tools and MCP server; see
`CHANGELOG.md`).

---

## R-013 — `pxx improve cycle` (v2.2.0) mines the local run store deterministically and stops before promotion

**Claim.** `pxx improve cycle` (v2.2.0) reads the local run store, clusters
outcomes, routes correlation-only proposals to the human-review inbox, and
**never promotes** (`stopped_before_promotion=true`); run twice on identical
input it is byte-identical (same `cycle_id`). No model or network is used.

**Grade.** Reproducible (deterministic mining — no model, no clock, no
randomness) + Attested (2026-08-01, the maintainer's local run store).

**Exact configuration (nothing inherits).** pxx 2.2.0 (uv-tool install,
`~/.local/bin/pxx`); no model/endpoint (the cycle is pure filesystem mining);
`state_dir=~/.local/state/pxx`; pxx repo at commit e78dcd2; daemon paused for
isolation. A different run store yields different clusters.

**Procedure (reproduction path).**

```sh
pxx improve pause
pxx improve cycle    # run 1
pxx improve cycle    # run 2 — same cycle-<id> proves idempotency
python3 -c "import json;print(json.load(open('$HOME/.local/state/pxx/cycle-report.json'))['stopped_before_promotion'])"
```

**Results ledger.**

| field | value |
|---|---|
| cycle_id (run 1 == run 2) | `cycle-745010b191e5` |
| mode | propose-only |
| runs_collected | 15 |
| clusters | 7 |
| proposals | 1 |
| candidates (auto-derived) | 0 |
| human_review | `["budgets:tighten_budget"]` |
| stopped_before_promotion | **true** |
| real_runs before/after | 15 / 15 (unchanged — read-only) |

**Observed (2026-08-01).** Both runs printed `cycle cycle-745010b191e5
(mode=propose-only) … runs=15 clusters=7 proposals=1` and `stopped before
promotion (propose-only)`; the second run's `cycle_id` was identical. The
proposal is correlation-disciplined: `target=budgets, operation=tighten_budget,
risk=low, basis=correlation, root_cause=TOOLING, confidence=0.5`, evidence = 3
real run_ids — the same BUDGET_EXCEEDED runs independently diagnosed by hand in
R-012. Local records: `~/.local/state/pxx/cycle-report.json`, `cycle-state.json`,
`inbox/human-review-required/*.json`.

**Boundary — explicitly not claimed.** The cycle only *proposes* — promotes
nothing, applies nothing (the proposal sits in the human inbox, unactioned). The
proposal is a CORRELATION hypothesis (`confidence=0.5`), NOT a validated fix.
Determinism claimed only for identical run-store input. Exact-config only.

## R-014 — Dogfood: the autonomous loop cannot self-modify pxx's protected control plane (fail-closed, as designed)

**Claim.** An autonomous `pxx loop` (v2.2.0, real qwen3-coder backend) tasked to
change pxx's own control-plane code is refused — first by the deterministic
clarity gate on ambiguous file references, and (when that is cleared) by the
protected-path gate, in every permission mode. The self-improvement targets that
surfaced during live dogfooding all live in pxx's protected control plane, so
autonomous self-modification is architecturally forbidden and must go through
human review.

**Grade.** Attested (2026-08-01, two-box hardware).

**Exact configuration (nothing inherits).** pxx 2.2.0 (uv-tool install); coder
`qwen3-coder:30b` (official Ollama Q4_K_M) on a Ryzen 9 5950X + RTX 5060 Ti 16GB
(Gentoo, Ollama 0.30.5) reached over an SSH tunnel (`PXX_BASE_URL=
http://localhost:11435`); advisory judge `gemma2:9b` on the M4 Mac
(`PXX_REVIEW_BASE_URL=http://localhost:11666`); pxx repo at e78dcd2;
`--review --review-mode advisory --budget-rounds 10`.

**Observed (2026-08-01) — three attempts, negative results recorded verbatim.**
1. Task: harden the `real_runs` counter (in `autopromote.py`). →
   `[CLARIFICATION_REQUIRED]` on round 1: the task named `outcome.json` (a
   runtime artifact, not a repo file) and `ready_to_act` (`clarify.py:76-90`)
   refused — "references 'outcome.json', which does not exist under …". Zero
   edits; `real_runs` unchanged (a clarity-refused run leaves no counted dir).
2. Re-phrased with explicit "this is a runtime file, not in the source tree." →
   **identical `[CLARIFICATION_REQUIRED]`** (same instant, no model round): the
   gate is a pure regex scan and context cannot override it.
3. Pivot: fix the clarity-gate false-positive itself (referencing only existing
   files). → cleared the clarity gate, opened a run (`real_runs` 15→16), ran
   **~68 s of real qwen3-coder work**, planned an edit — then
   `[OUT_OF_SCOPE] protected path (human-only control plane, denied in every
   permission mode): pxx/clarify.py`. Zero edits landed; tree clean.

Confirmed: `is_protected_path` is **True** for `pxx/clarify.py`,
`pxx/improve/autopromote.py`, and `pxx/improve/cycle.py`; **False** for
`session.py`/`review.py`/`config.py`/`loop.py`. Logs:
`scratchpad/dogfood-logs/01b-loop*.log`.

**Findings surfaced (all real, reproducible):**
- **F-1 — guardless `real_runs` counter.** `gather_counts`
  (`autopromote.py:115-116`) counts every subdir of `runs/` with no filter:
  mock/replay backends, crashed pre-outcome runs, and self-referential runs all
  count; no dedup; not durable (a live `iterdir()`). Demonstrated in-session: an
  accidental failed `MODEL_UNAVAILABLE` probe (zero work) bumped `real_runs`
  14→15. The earned-enablement bar is honest only by convention.
- **F-2 — clarity-gate false-positive.** `ready_to_act` refuses any task with an
  edit verb that mentions a `*.ext` token absent under cwd, even when the file
  is a runtime/generated artifact merely described (not edited). Uncontestable by
  context.
- **F-3 (safety WIN) — control-plane protection holds.** pxx's protected-path
  gate refuses its own agent's edits to `clarify.py`/`autopromote.py`/`cycle.py`
  in every permission mode — the fail-closed "backends cannot modify the gates
  that guard them" invariant, demonstrated live after real model work.

**Boundary — explicitly not claimed.** The loop never LANDED an edit here — by
design, because every target was protected control plane; this is NOT a claim
about the loop's ability to complete edits on ordinary code (see Phase 2 /
future). F-1 and F-2 are reported, NOT fixed (their files are human-gated; fixes
must be human-reviewed). No model quality/latency claim. Exact-config only.
`real_runs` net +2 this session (14→16) is genuine `Session.run` invocations, not
padding.

---

## R-015 — Dogfood: the autonomous loop lands a correct, tested change on a real live mature codebase

**Claim.** An autonomous `pxx loop` (v2.2.0, real qwen3-coder backend) completed
the full edit→test→review cycle on a **live, mature, multi-tenant FastAPI SaaS
backend** (not the pxx repo), producing a correct, idiomatic, read-only helper
plus four passing tests — with **zero human intervention** during the run. The
one blemish, `BUDGET_EXCEEDED` (over-work past a working solution), independently
reproduces the R-012 budget finding and the R-013 `tighten_budget` proposal on a
second, unrelated codebase.

**Grade.** Attested (2026-08-01, two-box hardware; the target is a private repo,
so the diff/results are cited, the repo is not published).

**Exact configuration (nothing inherits).** pxx 2.2.0 (uv-tool install); coder
`qwen3-coder:30b` (official Ollama Q4_K_M) on Ryzen 9 5950X + RTX 5060 Ti 16GB
over SSH tunnel (`PXX_BASE_URL=http://localhost:11435`); advisory judge
`gemma2:9b` on the M4 Mac (`PXX_REVIEW_BASE_URL=http://localhost:11666`); target
repo `workorder_wizard` at commit `a4fc2f3` (working tree clean before and
after); `cwd=src/backend`; test command run in an out-of-repo scratch venv
(`requirements.txt`, pytest 9.1.1) so the live tree stayed pristine;
`--review --review-mode advisory --budget-rounds 10`.

**Procedure.**

```sh
# scratch venv outside the repo, deps from requirements.txt
pxx loop -m "In utils/plan_limits.py, add get_plan_limits_summary(db, tenant)
  composing the existing getters (read-only, no behaviour change); add a test to
  tests/test_plan_limits.py." --review --review-mode advisory --budget-rounds 10
# (PXX_MODEL=qwen3-coder:30b @11435, PXX_REVIEW_MODEL=gemma2:9b @11666,
#  PXX_TEST_COMMAND=<scratch-venv>/bin/python -m pytest tests/test_plan_limits.py)
```

**Results ledger.**

| field | value |
|---|---|
| task | add read-only `get_plan_limits_summary` + test |
| terminal_code | `BUDGET_EXCEEDED` (10 rounds — over-work, not a failure to produce) |
| files changed | `utils/plan_limits.py` +13, `tests/test_plan_limits.py` +62 |
| tests | 9 → **13, all pass** (2.95 s) |
| change correctness | composes existing getters, read-only, no behaviour change (as tasked) |
| real_runs before/after | 16 → 17 |
| disposition | diff reviewed, **RESET to `a4fc2f3`** (observation, not landed) |

**Observed (2026-08-01).** The loop added `get_plan_limits_summary(db, tenant)`
returning `{"max_users": get_max_users(...), "max_work_orders_per_month":
get_max_work_orders_per_month(...)}` with a correct docstring, and a
`TestPlanLimitsSummary` class of four tests (free / starter / trial / override
plan scenarios) with full-dict assertions on the existing fixtures. The scoped
suite went 9→13, all green. It ran 10 rounds and stopped at `BUDGET_EXCEEDED`
(the coder kept working past a passing solution — same over-work pattern R-012
tuned and R-013's cycle independently flagged). The change was reviewed and the
live tree reset to its exact prior commit; nothing was committed to the target
repo. Log: `scratchpad/dogfood-logs/02-workorder-*.log`.

**Boundary — explicitly not claimed.** The change was NOT landed (dogfood =
observe; reset to `a4fc2f3`) — no claim that this code shipped or was adopted.
`BUDGET_EXCEEDED` (not `COMPLETED`) means the loop over-worked; this is the
R-012 budget finding reproduced, not a clean-completion claim. One task, one
small read-only helper — no claim about larger/multi-file/behaviour-changing
tasks. The scratch venv (`requirements.txt`) is not the project's CI environment
and may differ from it. Exact-config only; a different task/model/repo is not
covered.

---

## R-016 — `real_runs` counts only genuine runs (F-1 fix)

**Claim.** The earned-enablement `real_runs` bar now counts a `runs/<id>/`
directory only if it did genuine work: a **real backend** (`native`/`aider`, not
the `mock`/`replay` test doubles), a recorded **terminal outcome**
(`outcome.json` present — a crash before the outcome never counts), and
**evidence the model ran** (tokens spent OR a diff produced). Fail-closed: any
unreadable/absent record does not count. This closes the F-1 gaming dogfooded in
R-014 (the bar was a raw subdirectory count, so a `mock` run, a crash, or a
zero-work `MODEL_UNAVAILABLE` probe all inflated it).

**Grade.** Reproducible (the filter + tests) + Attested (2026-08-01, the effect
on the maintainer's live run store).

**Exact configuration (nothing inherits).** pxx v2 (`fix/f1-real-runs-counter`);
`gather_counts` / `counts_as_real_run` in `pxx/improve/autopromote.py`. Only the
`real_runs` bar's *counting* changes; the other bars and thresholds are
unchanged.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_improve_autopromote.py -q
# and against a real store:
uv run --extra dev python -c "from pxx.improve.autopromote import gather_counts; \
from pathlib import Path; print(gather_counts(Path.home()/'.local/state/pxx').real_runs)"
```

**Observed (2026-08-01).** On the maintainer's 17-directory store the count went
**17 (raw) → 13 (genuine)**: excluded were 2 `MODEL_UNAVAILABLE` probes (0
tokens, 0 diff), 1 `OUT_OF_SCOPE` and 1 `BUDGET_EXCEEDED` with no recorded work;
kept were 9 `COMPLETED` + 4 `BUDGET_EXCEEDED` that spent tokens or edited files.
Pinned by `tests/test_improve_autopromote.py`
(`test_real_runs_counts_only_genuine_runs`, `…_zero_when_no_runs_dir`,
`test_counts_as_real_run_fail_closed_on_bad_records`); full suite 1030 passed.

**Boundary — explicitly not claimed.** This is the **filter only** — it stops
junk from inflating the bar. ~~It does NOT make the counter durable: `real_runs`
is still a live `iterdir()`, so an external run-dir clear still regresses it (the
~48→17 drop of R-014 would still happen).~~ **[Superseded by R-020 —
`real_runs` is now reconciled through a durable append-only ledger, so a
run-dir clear no longer regresses persisted ids.]** Self-referential (pxx-repo)
genuine runs still count. "Genuine work" is proxied by tokens/diff, not a
semantic judgment of value. ~~Durability (an evidenced ledger)~~ **(shipped in
R-020)** and the fact that the accumulation daemon is not currently running are
tracked ROADMAP follow-ups. No claim that the `real_runs` bar is now trustworthy
end-to-end — only that mock/crash/zero-work runs no longer inflate it.

---

## R-017 — Clean loop termination: an over-worked run with a verified edit reports COMPLETED

**Claim.** When a coder exhausts its per-turn round budget (session-level
`BUDGET_EXCEEDED`) but has already produced a correct edit, `run_loop` now
verifies that edit once — if a configured test command passes (and a configured
review gate does not block) — and reports `COMPLETED` instead of the over-work
terminal. Fail-closed: without a test command, or if tests fail / the gate
blocks, the original terminal stands. It never heals (the budget is spent) and
never rescues a run the guards would fail.

**Grade.** Reproducible (six unit tests) + Attested (2026-08-01, a real two-box
before/after on a live SaaS repo).

**Exact configuration (nothing inherits).** pxx v2 (`fix/clean-loop-termination`);
`_overwork_verified` + the salvage block in `pxx/loop.py`. Coder
`qwen3-coder:30b` over the SSH tunnel (`11435`), advisory judge `gemma2:9b`
(`11666`), target `workorder_wizard` at `a4fc2f3`, scratch-venv test command.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_loop.py -q -k overwork
# real two-box: re-run the R-015 task with the fixed loop and observe the terminal
```

**Observed (2026-08-01).** The **same task/setup that reported `BUDGET_EXCEEDED`
in R-015** now reports:
`[COMPLETED] completed in 1 round(s) (over-work salvaged: budget spent
mid-round, edit verified)`. The coder over-worked identically (same
`get_plan_limits_summary` edit, `+69` lines) and the scoped suite went 9→13 all
passing (2.92 s) — the terminal now reflects reality. A `gate_decision`
`overwork_salvage` event is emitted. Six tests pin the branches
(`tests/test_loop.py::test_overwork_*`): salvaged when tests pass / under
advisory review; NOT salvaged when tests fail, no test command, no diff, or a
blocking review REVISEs.

**Boundary — explicitly not claimed.** This corrects the *terminal code* of an
over-worked-but-successful run; it does **not** reduce the over-work itself (the
coder still burns rounds — a separate efficiency item / the roadmap
`tighten_budget` and done-signal work). It fires only for session-level
`BUDGET_EXCEEDED` with a diff, not the outer `ROUND_CAP` or other terminals. It
requires an objective test signal — a run with no test command is never
salvaged. No model quality claim; exact-config only.

---

## R-018 — `pxx improve status` reports real daemon liveness (running / paused / stopped)

**Claim.** `pxx improve status` now reports the daemon as **running**,
**paused**, or **stopped** from actual process liveness — a live daemon holds an
exclusive flock on `daemon.lock` (released by the OS even on a crash), probed by
`scheduler.is_running`. Previously it printed "running" whenever the pause flag
was unset, so a daemon that was not running still read as "running" — the
misleading claim the dogfood surfaced (R-014, and the ROADMAP earned-enablement
correction).

**Grade.** Reproducible (four unit tests) + Attested (2026-08-01, the live
workstation now reads `daemon: stopped`).

**Exact configuration (nothing inherits).** pxx v2 (`fix/daemon-liveness`);
`is_running` in `pxx/improve/scheduler.py`, wired into the `pxx improve status`
CLI. Liveness = the `daemon.lock` flock; the durable pause flag
(`daemon-control.json`) is orthogonal.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_improve_scheduler.py -q -k is_running
pxx improve status   # 'daemon: stopped' when no daemon holds the lock
```

**Observed (2026-08-01).** `pxx improve status` on the workstation prints
`daemon: stopped` (no daemon process — matching reality). Unit probe: no
lockfile → `False`; a free (stale) lockfile → `False`; a held lockfile → `True`;
released → `False`; a pause flag with no live daemon → `is_running False`.

**Boundary — explicitly not claimed.** This makes the status *honest*; it does
NOT stand the daemon up. No launchd job runs the improvement daemon yet, so
earned-enablement counts still accrue only from manual runs — running it under
launchd (`pxx improve daemon`, hourly `--once` or a KeepAlive job) is the
remaining, environment-specific step, tracked in the ROADMAP. Liveness is
detected via the local `daemon.lock`; a daemon on a different host/state-dir is
not observed.

---

## R-019 — Reasoning judges gate the blocking review deterministically (structured verdict)

**Claim.** The reviewer requests a **grammar-constrained** verdict
(`response_format` json_schema forcing `{verdict, findings}`), so a reasoning
judge (qwen3.5-class) always emits a parseable verdict — fixing R-012, where
qwen3.5 intermittently produced no `VERDICT:` line and so was unusable for a
`--review-mode blocking` gate. The parser reads structured JSON first and falls
back to free text; endpoints that reject `response_format` retry plain.

**Grade.** Reproducible (parser + request tests) + Attested (2026-08-01, real
qwen3.5 judge over the Mac Ollama).

**Exact configuration (nothing inherits).** pxx v2 (`feat/reasoning-judges`);
`_VERDICT_SCHEMA` / `_parse_structured_verdict` in `pxx/review.py`. Judge
`qwen3.5:9b` on the Mac's own Ollama (`http://localhost:11666`), via
`NativeReviewer` + `review_changes`.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_review.py -q -k structured
# real judge: NativeReviewer(qwen3.5 @ :11666), review_changes(diff, task, …, BLOCKING)
```

**Observed (2026-08-01).** Direct endpoint probe: `response_format` json_schema
→ clean `{"verdict": …}` (plain `json_object` and Ollama `format` returned
arbitrary keys). Through pxx's path, BLOCKING mode, 3 runs each: a correct diff
→ `APPROVE` (not blocked); a buggy diff (`return a - b`) → `REVISE`, `blocked`,
with a `calc.py`-anchored finding — **6/6 parseable, 0 `unparseable`** (vs R-012's
intermittent failures). Models fold the line into the `file` field
(`"calc.py:2"`); the structured parser splits an embedded `:line` and treats a
named file as the finding's anchor. Ten tests
(`tests/test_review.py::test_parse_structured_*`,
`test_native_reviewer_requests_structured_verdict`,
`…_falls_back_when_response_format_rejected`,
`test_structured_revise_blocks_in_blocking_mode`); full suite 1055 passed.

**Boundary — explicitly not claimed.** No model quality claim beyond
parseability + correct verdict on these small diffs; a reasoning judge's
*judgement* is only as good as the model. Structured output depends on the
endpoint supporting `response_format` json_schema (Ollama/vLLM/OpenAI do; others
fall back to free text — same reliability as before). Exact-config only.

---

## R-020 — `real_runs` is durable: an evidenced ledger survives run-dir clears

**Claim.** `real_runs` is reconciled through a durable, append-only ledger
(`real-runs.jsonl` in the state dir): every genuine run (R-016 criteria) is
recorded once by run id (once its ledger append succeeds), and the count is the
number of DISTINCT recorded ids — which, for successfully-persisted ids,
**never shrinks when run dirs are rotated or cleared**. This closes the F-1
durability gap: the count was a live `iterdir()`, so an external state-dir clear
silently erased earned progress (observed live ~48 → 17, R-014/R-016).

**Grade.** Reproducible (three unit tests + a durability smoke).

**Exact configuration (nothing inherits).** pxx v2 (`fix/f1-durable-ledger`);
`reconcile_real_runs` / `_ledger_run_ids` / `_genuine_run_meta` in
`pxx/improve/autopromote.py`; `gather_counts` counts via the ledger. Only the
`real_runs` bar changes.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_improve_autopromote.py -q -k ledger
```

**Observed (2026-08-01).** Smoke: 5 genuine runs (a `mock` run excluded) →
`real_runs=5`, 5 ledger lines; **`shutil.rmtree(runs/)` then recount →
`real_runs=5`** (durable); a new genuine run → 6. Tests pin: durability across a
clear; idempotent reconcile (no double-count); a corrupt ledger line skipped
while valid entries still count. Full suite 1059 passed.

**Boundary — explicitly not claimed.** A run is captured into the durable ledger
when `gather_counts`/`reconcile_real_runs` next runs (readiness check or daemon
tick) — a genuine run whose dir is cleared BEFORE any reconcile is not captured
(a periodic daemon, once stood up, only *reduces* this window until its next
tick; a run removed before the next reconcile stays uncaptured — it does not
close the window). Persistence is best-effort: reconcile can return an id whose
ledger append failed, and that id can be lost on a later run-dir clear; duplicate
lines from concurrent writers are deduplicated on read, not prevented on write.
The ledger records `run_id`/`recorded_at`/`backend`/`code`/work counts, not the
full run evidence (the run dir holds that while it exists). No change to the
genuine-run criteria (R-016). A run dir whose canonical path escapes `runs/` (a
symlink, or a symlinked ancestor) is rejected.

---

## R-021 — F-2 clarity gate: a described artifact no longer false-blocks

**Claim.** The clarity gate's missing-file signal is now *governed* per path, not
global. It gates only when an edit verb is the NEAREST cue to a specific path
within its clause; a path introduced by a creation/generation cue (`emits
out.json`, `such as build/x.json`, `a new foo.py`) is not treated as an edit
target, so an edit-verb task that merely *describes* a generated/runtime artifact
no longer stops with a spurious question. Genuine ambiguity ("fix the bug in
`src/nope.py`" with no such cue) still gates.

**Grade.** Reproducible (18 unit tests, 8 new for the governance boundary).

**Exact configuration (nothing inherits).** pxx v2; `ready_to_act` in
`pxx/clarify.py` — per-path clause window (`_CLAUSE_BREAK`), nearest-cue
comparison of `_EXISTING_FILE_VERBS` vs `_NON_EDIT_TARGET_CUE`. The suppression
cue set is deliberately tight — creation/generation verbs (incl. past
participles like `generated`/`written`) plus explicit exemplifiers (`such as`,
`for example`, `e.g.`); generic nouns/adjectives that also sit near real edit
targets (`runtime`, `artifact`, the file `called`/`named` X) are excluded so
they can't false-suppress genuine ambiguity (CodeRabbit, PR #16). Untrusted task
paths with a `..` segment are ignored (no cwd-escaping probe). The old logic
gated on *any* edit verb anywhere + *any* missing path. Only the missing-file
branch changes; empty-task and test-intent branches untouched.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_clarify.py -q
```

**Observed (2026-08-01).** The dogfooded false positive — "improve the
run-integrity detector so it emits `prose-tool-call.json`" — now returns
`READY_TO_EXECUTE` (was `INSUFFICIENT_CONTEXT`). Preserved: missing edit target
still gates ("...then fix the bug in `src/detector.py`" → gated); a creation cue
in a PRIOR clause does not suppress the next clause's edit target
("generate a schema. then edit `src/missing.py`" → gated). Post-review
hardening pins: `fix runtime crash in src/nope.py` and `...the file called
src/nope.py` still gate (generic words don't suppress); `a/../../outside.py` is
ignored (no cwd escape). Full suite 1069 passed.

**Boundary — explicitly not claimed.** This is still a deterministic surface
heuristic on task prose, not parsing. It reduces false positives; it does not
guarantee zero. The "nearest cue wins within a clause" rule can misjudge unusual
phrasings (an edit target that happens to sit right after a creation word in the
same clause is suppressed; a described artifact with no cue and an upstream edit
verb still gates). It reads task text only — never the diff or model intent.
`clarify.py` is protected control-plane code; this change is human-authored.

---

## R-022 — the improve daemon is stood up (hourly, propose-only, non-mutating)

**Claim.** A macOS LaunchAgent runs `pxx improve daemon --once` hourly. Each tick
runs ONE propose-only improvement cycle (mine terminal run records → cluster →
write proposals to the human-review inbox). It never edits the working tree, runs
the agent, or promotes anything. Operator controls: `pxx improve pause` durably
halts the cycle at the next tick, `resume` clears it, `launchctl bootout`
uninstalls.

**Grade.** Reproduced live on the Mac mini (2026-08-01).

**Exact configuration (nothing inherits).** pxx **2.3.0** (uv-tool
`pxx-orchestrator`, PATH `~/.local/bin/pxx`). LaunchAgent
`~/Library/LaunchAgents/local.pxx.improve-daemon.plist` (committed to the repo at
`docs/ops/local.pxx.improve-daemon.plist`): `ProgramArguments` = `pxx improve
daemon --once`; `WorkingDirectory` = `~/ai/pxx`; `StartCalendarInterval` Minute 0
(hourly); `RunAtLoad` false; `ProcessType` Background, `Nice` 5; logs to
`~/Library/Logs/pxx-improve.log`. State dir `~/.local/state/pxx`. The cycle is
deterministic/offline (`cycle.py`/`mining.py` reference "model" only as a run-record
field — no network/model calls), so it has NO dependency on the two-box rig.

**Procedure (reproduction path).**

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.pxx.improve-daemon.plist
launchctl kickstart -k gui/$(id -u)/local.pxx.improve-daemon
tail -1 ~/Library/Logs/pxx-improve.log
```

**Observed (2026-08-01).** Kickstart via launchd → `daemon: ticks=1 cycles=1
paused-skips=0`, last exit code 0, working tree clean (no mutation), 2 proposals
in `inbox/human-review-required`. Pause kill-switch: after `pxx improve pause`, a
kickstart logs `ticks=1 cycles=0 paused-skips=1` (cycle skipped); `resume` clears
it. `pxx improve readiness` reads eval_cases + unresolved_critical_defects green,
real_runs + human_approved_promotions unmet → **NOT-READY** (auto-promotion still
refuses).

**Boundary — explicitly not claimed.** The daemon accrues *proposals for human
triage*, NOT earned-enablement counts: `real_runs` moves only from genuine `pxx`
agent runs, not daemon cycles — so standing it up does not advance the
`real_runs`/`human_approved_promotions` bars. Because it runs `--once`, `pxx
improve status` reads `daemon: stopped` between ticks by design (the flock is only
held during a tick) — a long-lived `KeepAlive` variant would read `running`.
Nothing is auto-promoted (cycle is propose-only, `stopped_before_promotion` pinned
True). The daemon holds `work.lock` only for its brief tick; the cycle is
read-analyze-propose only, so a concurrent manual run is safe. `autopromote.py`
and the LaunchAgent are human-authored control-plane, not autonomously editable.

---

## R-023 — portable / single-box degrade, verified on one box across three states

**Claim.** pxx's local-first degrade — the router probes `model` then each
`[[fallback_models]]` entry and prefers the first *reachable* one — works
end-to-end across three deployment states (a remote GPU **primary-up**,
**primary-down** falling to an on-device model, and **local-only**) on BOTH the
native lane and the auto lane (after BUG A, #21). Every run completes; nothing
phantoms. Single box, three states — not a multi-box "fleet" claim. (Reachability
is *not* sufficiency: a reachable endpoint serving a different model id is a
known un-handled gap — Boundary (b), not claimed here.)

**Grade.** Reproduced live on one 8GB portable box — 8 runs, zero failures.

**Exact configuration (nothing inherits).** pxx **2.3.1** (PyPI wheel carrying
PRs 17 BUG B, 18 DF-02, 20 token truthfulness, 21 BUG A). Hermetic scratch repo
(a buggy function + a failing test), identical coder task across the loop runs.
Coder =
a remote GPU model as primary with a `[[fallback_models]]` chain → an on-device
instruct model. Reviewer pinned to a local endpoint via `PXX_REVIEW_*` for the
standard runs (repo-local `[roles.review]` is ignored by design — data-egress
boundary). `pxx loop --review --review-mode advisory` (native) and `pxx ask`
(auto) per state.

**Procedure (reproduction path).** Configure a fallback chain (remote primary +
on-device fallback); for each of {primary-up, primary-down (dead port),
local-only} run both the native loop and the auto `ask` (the 6 base runs), plus
two variants in primary-down: the auto lane with `aider` present (the BUG A
case) and a native run with the reviewer endpoint dead (the NO_REVIEW bonus) —
8 runs total.

**Observed (2026-08-02).** All 8 runs COMPLETED:

| State | Lane | Result |
|---|---|---|
| local-only | native loop / auto ask | COMPLETED (on-device) |
| primary-down | native loop | COMPLETED — one clean "endpoint unreachable; falling back" line |
| primary-down | auto ask (aider absent / **present**) | COMPLETED — **native preferred, clean fallback** (BUG A) |
| primary-up | native loop / auto ask | COMPLETED (GPU primary, no fallback line) |
| primary-down (bonus) | native, reviewer→dead endpoint | COMPLETED, verdict **NO_REVIEW** + loud "reviewer unavailable" |

- **BUG A before/after:** same setup (aider on PATH, dead primary, chain set) on
  pre-fix source picks aider → ~32s litellm retries → ~97s wall → phantom
  COMPLETED (tokens=0); on 2.3.1 → native preferred → **0.9s clean COMPLETED**.
- **Fallback overhead ≈ zero** (65s primary-down vs 62s local-only for the loop —
  the dead-port probe fails instantly, the chain advances silently-but-logged).
- `real_runs` accrued 63 → 77 over the campaign (includes aborted setup runs —
  an honest ledger).

**Boundary — explicitly not claimed.** (a) **Tool-call capability is
context-dependent** — a model that returns structured `tool_calls` on a toy
probe can degrade to *prose* under pxx's real loop prompt (the detector then
correctly terminates MODEL_UNAVAILABLE). Validate an on-device fallback coder
under a realistic-size context, not a one-line probe. (b) A reachable primary
serving a *different model id* hard-fails MODEL_UNAVAILABLE (404) without
advancing the chain. **[Fixed post-2.3.1 — F3, PR #25: a 404 / "model not found"
from a reachable endpoint now advances the `[[fallback_models]]` chain.]** (c)
The **reviewer has no fallback chain**: when its endpoint is down it is honestly
absent (NO_REVIEW), never a phantom pass. (d) The safety-net stash does not
currently restore *untracked* files on a mid-run abort. **[Fixed post-2.3.1 —
F1, PR #24: an aborted run restores the pre-run tree, incl. untracked.]** Evidence is from one
8GB box; per-run rows live in that box's local state dir.

---

## R-024 — the safety net restores the working tree on abort, incl. UNTRACKED

**Claim.** When an edit-capable run ABORTS (any non-COMPLETED outcome), the
safety net restores the user's pre-run working tree — tracked-dirty AND
untracked files — instead of stranding it in a stash pxx never pops. Fixes F1:
the `pxx-pre` tag restores tracked files (they are at HEAD) but cannot restore
untracked ones (they are in no commit), so an aborted run over a dirty tree
silently lost the user's untracked files.

**Grade.** Reproducible (unit tests) + Attested (mini 2026-08-02, and
independently re-verified on the 8GB portable box by re-running the original
repro).

**Exact configuration (nothing inherits).** pxx **2.3.2**; `restore_safety_net`
+ `_stash_ref` in `pxx/safety_net.py`; wired into the loop and session teardown
(runs LAST, after the run artifact is written, so restored WIP is never captured
into `diff.patch`). `reset --hard` to the tag then pop the net's OWN stash
(matched on the exact message). Fail-soft.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_safety_net.py -q
# e2e: in a git repo with a tracked-dirty file + an untracked file, run
#   pxx loop -m "…" --scope . --base-url http://127.0.0.1:9999   # dead endpoint
# (no [[fallback_models]]) -> MODEL_UNAVAILABLE abort.
```

**Observed (2026-08-02).** On released 2.3.1 a dead-endpoint `MODEL_UNAVAILABLE`
loop LOSES an untracked `notes.txt` (stranded in a `pxx safety net loop-*`
stash). On 2.3.2 the same abort restores `notes.txt` and the tracked-dirty file,
discards the failed run's partial edit, leaves no stash; a pre-run `SECRET_KEY`
never appears in any run `diff.patch`. Full suite 1088+ passed.

**Boundary — explicitly not claimed.** Restore fires on ABORT only. On a
COMPLETED run over a dirty tree the net still stashes the user's pre-run WIP and
does not pop it (pop is the user's move) — an open follow-up (F7). Reads that
canonicalize outside the repo, and any git failure, leave tag/stash intact for
manual recovery (never crashes).

---

## R-025 — a 404 / model-not-found advances the fallback chain

**Claim.** A REACHABLE endpoint that does not serve the requested model id
(HTTP 404, or a 400/422 body naming a missing model) advances the
`[[fallback_models]]` chain — the same path as an unreachable endpoint — instead
of hard-failing `MODEL_UNAVAILABLE`. Fixes F3: only connection/timeout errors
advanced the chain, so a reachable-but-wrong-model primary hard-failed.

**Grade.** Reproducible (unit tests) + Attested (mini 2026-08-02; F3 was
surfaced and re-verified end-to-end on the portable box with an auditable
`gate_decision` fallback event).

**Exact configuration (nothing inherits).** pxx **2.3.2**; `_model_not_found`
+ the advance branch in `pxx/backends/native.py`. 404 always; 400/422 only when
the body names a missing model; 5xx and generic 400s are NOT correctable.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_backends_native.py -q -k "model_not_found or fallback"
```

**Observed (2026-08-02).** A primary returning 404 + a healthy fallback → the
run COMPLETES on the fallback, emitting one `fallback` gate event to the next
model. A 404 on the LAST endpoint still raises `BackendError` (never silently
swallowed). Parametrized detection pins 404/400-model-not-found/422 as
correctable and exceed-context / generic-400 / 5xx as not.

**Boundary — explicitly not claimed.** This ADVANCES the chain; it does not
auto-correct the model id on a single-model endpoint (the router has that seam;
the native loop path only advances). Bare reachability is still not sufficiency.

---

## R-026 — scope split: reads span the repo, writes stay in `scope`

**Claim.** A single-file or single-dir `--scope` no longer blocks READS: the
agent may read anything under the project root (tests, imports, context) but may
only WRITE within `scope`. Reads still cannot escape the root. Fixes F5, the
root cause of the dogfood `OUT_OF_SCOPE` failures — the agent could not read the
test it needed to edit correctly.

**Grade.** Reproducible (unit + tool tests) + Attested (mini 2026-08-02; the
morning's exact single-file-scope failure now reads the sibling test with no
violation).

**Exact configuration (nothing inherits).** pxx **2.3.2**; `ScopeGate.check_read`
(repo-root or trusted) vs `check_write` (scope) in `pxx/safety.py`; the broker
(`pxx/broker.py`) authorizes read-class actions via `check_read`, write-class via
`check_write`; `read_file`/`list_files`/`search_files` re-gate through the read
scope (the search fallback re-checks each symlink candidate).

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_safety.py tests/test_tools.py -q -k "scope"
```

**Observed (2026-08-02).** Under `--scope src`, `read_file docs/readme.md`
succeeds while `write_file docs/readme.md` is denied; a read of `/etc/passwd` or
`../outside.txt` still raises; the Python search fallback returns "no matches"
for a symlink pointing at an out-of-root secret (content not exposed). Full suite
1100 passed.

**Boundary — explicitly not claimed.** Reads are confined to the project root —
a user who needs to prevent the agent from *reading* an in-repo file should
gitignore/vault it (scope no longer gates reads). The write boundary and the
loop's changed-path guard are unchanged. Test-generated build artifacts
(`__pycache__/*.pyc`) counted by the changed-path guard as OUT_OF_SCOPE are a
separate open follow-up.

---

## R-027 — Fresh PyPI install → doctor → first real task (~5 min)

**Claim.** A new user can `pip install pxx-orchestrator`, run `pxx doctor`, and
complete a real read-only task against a local model — the install-and-first-task
path works end to end on the shipped 2.3.2 wheel, and a read-only run leaves the
working tree untouched.

**Grade.** Reproducible — run live on the 16 GB Mac mini (2026-08-03) against a
local Ollama endpoint. Complements R-001 (the full 6/6 tutorial on the 8 GB path).

**Exact configuration (nothing inherits).** pxx **2.3.2** installed from PyPI
into a fresh `python -m venv` (CPython 3.14.6) via `pip install
pxx-orchestrator`; macOS, Apple-silicon Mac mini (16 GB); coder model
`qwen2.5:7b-instruct` served by Ollama at `http://127.0.0.1:11666`
(`PXX_PROVIDER`/`PXX_MODEL`/`PXX_BASE_URL`); a second, deliberately-unreachable
endpoint (`Qwen3-Coder` @ `:8001`) present to show reachability reporting.

**Procedure (reproduction path).**

```sh
python -m venv venv && ./venv/bin/pip install pxx-orchestrator
./venv/bin/pxx --version                      # reports version 2.3.2
./venv/bin/pxx doctor                         # setup diagnosis
mkdir demo && cd demo && printf 'def add(a, b):\n    return a - b\n' > calc.py
git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm init
PXX_PROVIDER=ollama PXX_MODEL=qwen2.5:7b-instruct PXX_BASE_URL=http://127.0.0.1:11666 \
  ../venv/bin/pxx ask -m "Does the add function in calc.py have a bug? ..."
```

**Observed (2026-08-03).** `pip install` completed in ~1 s (cached wheels;
first-ever install with a cold cache is longer). `pxx --version` reported `2.3.2`.
`pxx doctor`: `python 3.14.6`, `config`, `memory_dir`, `state_dir`, `hooks`,
`git`, `rg` all ✅; the reachable endpoint reported `reachable
(http://127.0.0.1:11666)` and the down one `unreachable (…:8001)` (⚠, not an
error — the fallback semantics). The read-only `pxx ask` terminated `COMPLETED`
with a genuine model round (`rounds=3 tokens=4295 diff_lines=0`) and `git status`
stayed empty — read-only mode wrote nothing.

**Boundary — explicitly not claimed.** This attests the install → CLI → runtime
→ read-only-safety path, **not** model answer-correctness and **not** the full
6/6 tutorial completion (that is R-001, on the recommended 8 GB `qwen3:4b-instruct-8k`
path). The `qwen2.5:7b-instruct` judge model *hedged* rather than definitively
naming the `a - b` bug — no capability claim is made about that model here.
Timing and endpoint reachability depend on a local Ollama being up; a cold-cache
`pip install` is not ~1 s.

---

## R-028 — first governed production edit inside a third-party host

**Claim.** `pxx run --backend native` produced a real, test-verified code edit
while running as a governed producer inside an external autonomous-agent host:
every model tool call passed through the host's `PreToolUse` hook, which enforced
a separate policy kernel's veto (fail-closed) against a scoped machine identity
limited to file read/write. The run's scope held — the in-scope write was
allowed and every out-of-scope / symlink / `..`-traversal attempt was denied by
the host — and the edit passed the host's own test gate.

**Grade.** **Attested (2026-08-04)** — evidence relayed from the host operator's
live production run. **Not reproduced in this repo**: the enforcement path
requires the host's policy kernel and skill layer, a confidential third-party
deployment, so this is not a self-contained procedure like R-001/R-027. What is
pxx's to attest is that pxx behaved correctly *as the governed producer*.

**Exact configuration (as reported).** pxx **2.3.2**, `pxx run --backend native`,
`--provider vllm` through the host's inference router to a ~30B local coder
model; a per-run machine identity scoped to one dev repo with action classes
`file.read` + `file.write` (no shell); the host's `PreToolUse` hook wired via
`[[hooks]]`, spawned per tool call, fail-closed. Budgets passed as flags
(`--budget-rounds/-seconds/-diff-lines`).

**Procedure (host-side; not runnable here).** The host's skill invokes
`pxx run -m "<task>" --scope . --backend native --provider vllm --base-url
<router> --model <coder> --budget-…`; pxx drives the model, and each fs tool call
is vetoed by the hook before it executes.

**Observed (as reported).** Task = implement `subtract(a, b)` in `calc.py`.
Result: the function was written; `SkillResult success=True`, terminal
`COMPLETED`, `files_changed=1`, `rounds=3`, `0` introduced failures; the edit
passed the host's `assert subtract(5,3)==2` gate. The audit recorded the outer
run-admission ALLOW and the inner `file.write` ALLOW under the minted identity;
the boundary was independently exercised — out-of-scope write, absolute-symlink,
relative-`..`, relative-symlink, and in-scope-symlink+`..` targets **all denied**.

**Boundary — explicitly not claimed.** (1) This is host-side attestation, not a
reproducible pxx procedure — the governance layers are the host's, not pxx's.
(2) The task is the well-specified, self-contained class (cf. pxx tied an
external agent 10/10 on this class in the host's pre-registered eval); it is *not*
evidence about open-ended refactors. (3) It ran on **2.3.2**. The integration
surfaced one pxx-side footgun — `pxx run` with no `-m` and an open, data-less
stdin blocked indefinitely — **fixed in 2.3.4** (`_read_task` bounded wait →
fail-fast usage error); the other findings were host-side (its skill's outcome
schema and its hook's path resolution). (4) Firm-identifying details of the host
are deliberately omitted.

---

## R-029 — `run_shell` is fail-closed in `auto` mode (closes an unguarded shell)

**Claim.** `run_shell` in the unattended `auto` mode no longer executes
model-authored shell without a safeguard. In a write-capable mode (`edit` or
`auto`) it now requires a `run_shell` PreToolUse hook, `sandbox_shell`, or the
explicit `allow_ungated_shell` opt-in, else it is denied `HOOKS_MISSING`.
Previously `edit` enforced this but `auto` did not — and `scope` never confined
shell (a command has no path target), so an unattended `pxx run` could run
arbitrary shell unhooked and unsandboxed.

**Grade.** Reproducible (unit tests) — the deny path, all three satisfy-the-gate
paths (hook / sandbox / opt-in), and the config plumbing are covered.

**Exact configuration (nothing inherits).** pxx **2.3.5**; `pxx/tools/shell.py`
`run()` gate — `mode in (EDIT, AUTO) and not (has_pre_hooks or sandbox_shell or
allow_ungated_shell)` → `HooksMissing`; `Settings.allow_ungated_shell` (default
`False`) + `PXX_ALLOW_UNGATED_SHELL`, plumbed to `ToolContext` via
`make_tool_context`. `PermissionProfile.defaults()` allows the `shell` class in
`auto` (`broker.py`), and `run_shell` carries no path target (`broker.py`), so
the tool-level gate is the enforcement point.

**Procedure (reproduction path).**

```sh
uv run --extra dev python -m pytest tests/test_tools.py -k run_shell -q
uv run --extra dev python -m pytest tests/test_config.py -k allow_ungated_shell -q
```

**Observed (2026-08-04).** In `auto` with no hook/sandbox/opt-in,
`run_shell {"command": "echo hi"}` raises `HooksMissing` naming the three
safeguards. With any one of a `run_shell` hook, `sandbox_shell=true`, or
`allow_ungated_shell=true`, the command runs. `ask`/`plan` still raise
`ScopeViolation`. Config: `allow_ungated_shell` defaults `False`, reads from TOML
and `PXX_ALLOW_UNGATED_SHELL`, env overrides TOML.

**Boundary — explicitly not claimed.** (1) This gates *whether* shell runs, not
*what* it can do once a safeguard is chosen — `allow_ungated_shell` deliberately
runs it unconfined (named risk-acceptance), and `sandbox_shell` degrades to plain
`/bin/sh` when no `sandbox-exec`/`bwrap` binary is present (containment silently
absent — a separate follow-up). (2) The fix relaxes `edit` too: `sandbox_shell`
now satisfies the gate there, where before only a hook did. (3) The
firm-governed host of R-028 was never exposed by the old gap (its minted identity
carried no shell class); this closes it for pxx-standalone unattended users.

---

*Convention: entries are append-only and dated; superseded claims are
struck through with a pointer to the superseding entry, never deleted.*
