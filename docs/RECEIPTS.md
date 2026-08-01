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

*Convention: entries are append-only and dated; superseded claims are
struck through with a pointer to the superseding entry, never deleted.*
