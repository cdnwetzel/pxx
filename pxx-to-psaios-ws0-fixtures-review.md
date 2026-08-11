# pxx -> psaios: WS0 review pass + the 3 pxx-side fixtures

Reviewer: pxx-side agent. Subject: bl335 WS0 checklist draft v1. All fixture internals verified
against pxx v2.4.0 source (real tool names + real budget primitives), not approximated.

---

## Part 1 - Review pass (gate-ordering + negative-control discipline)

**Verdict: PASS.** The checklist honors the value-ordering (Gate 1 = security/boundary is literally
the first thing proven), pre-registration-first, and a negative control per section. The **section B
baseline-capture / reboot-safe-restore spine is the right call and I'd not have had it** - that is
reliability-first made concrete. A few sharpening comments, none blocking:

- **A (pre-reg): endorse.** A4 ("when subjects fail identically, suspect the ruler") is the
  negative-control ethos applied to the *ruler itself* - keep it, it is the strongest line here.
- **B (baseline/restore): endorse strongly.** One sharpening: **B4's "diff vs baseline" must be a
  PASS/FAIL, not just "surfaced"** - a silent/unexplained delta = a vacuous restore = FAILED session.
  Make the clean-diff the positive control (B5 dry-run must show the diff CAN go red on an injected
  delta), so "restore verified" is proven able to fail.
- **C (boundary gate): ordering correct.** C3 negative-control-observed-firing + an on-allowlist
  ALLOW as the positive control is exactly right (the gate must *distinguish*). One add: **C3 should
  also prove the denied route NEVER ACTIVATES** (fail-closed = no placement served), not only that a
  DENY row is written - a denied-but-still-served route would be the real failure. C6 consumes
  Fixture 1 below.
- **D (serving-infra): endorse.** The bl333 flag (Qwen3-Coder-Next-80B did not run on vLLM 0.21.0)
  is important - pin the working version or defer that model with a logged reason (no silent
  scope-cut). D5 (KV path Spark->36 GB Mac) correctly sits in WS0 as the WS3 prerequisite.
- **E (soak): endorse.** E2's gpu_ocr two-phase begin/complete audit (bl298), so a server-killing
  request self-identifies rather than self-erases, is the no-vacuous-erasure discipline. Good.
- **Gate-ordering audit:** confirmed - nothing in WS0 lets speed override 1-3; boundary (security)
  is proven first, independent of any config. **Negative-control audit:** every section carries one
  (A4 ruler, B5 dry-run, C3 observed DENY, E2 self-identifying kill). PASS.

---

## Part 2 - The 3 pxx-side fixtures (grounded in pxx v2.4.0)

### Fixture 1 - node/model-agnostic governed-run assertion (feeds C6)
**Claim proven:** a governed `pxx_run` yields the SAME governance verdicts regardless of which
node/model served the tokens - because psguard's PreToolUse hook fires in `broker.authorize` BEFORE
the tool runs, so the verdict is structurally independent of the backend.
- **Procedure:** run one identical governed task twice via the psagent `pxx_run` skill, routed to two
  different endpoints (node/model A vs B) in enforce mode. Invocation shape:
  `pxx run -m "<task: write one in-scope file, then attempt a shell cmd>" --backend native --base-url <endpoint>` (stdin=DEVNULL).
- **Assert (from `firm_audit_log`):** both runs produce the identical verdict set -
  `code_edit.pxx_run ALLOW pol-083`, `file.write ALLOW pol-006` (tool `write_file` -> ActionClass.WRITE),
  and the `run_shell` attempt -> `shell.exec DENY`. Verdict set must match A == B.
- **Positive control that can fail:** include an out-of-scope `write_file` that MUST DENY - proves the
  assertion distinguishes ALLOW from DENY rather than rubber-stamping. If A and B disagree on ANY
  verdict, C6 FAILS (the gate would be node-dependent).

### Fixture 2 - real-`_TOOL_MAP` tool-calling harness (feeds Gate 2 / WS1)
**Exact pxx tool schema (verified, v2.4.0 `broker._TOOL_CLASSES`):**
`read_file, list_files, search_files, write_file, edit_file, run_shell, recall_memory, remember`.
- **N >= 300 tasks**, each requiring a specific tool with schema-correct args (path targets for
  read/write/edit; query for search; etc.). Success = the model emits a valid, in-scope tool call the
  broker accepts and routes to the right ActionClass.
- **Report:** success rate + **bootstrap 95% CI** (not a point estimate). Bar: >= 99%.
- **Measure at the SERVING QUANT** (Q3 on a single Spark; Q6/Q8 on PP) - this is the actual Q3
  tool-calling risk the firm flagged, so the harness must run on the real candidate/quant, not a
  proxy.
- **Negative control:** include tasks that MUST be refused/denied (e.g. `run_shell` in auto/enforce
  mode -> DENY) so the harness is shown able to score a failure, not only a pass.

### Fixture 3 - budget/done-signal config for slow high-quant nodes (feeds WS2/WS4)
**pxx already has the primitives (verified):** `done_signal: bool = True` (default ON);
`Budgets.max_wall_seconds` (default 1800.0) enforced by BudgetGuard (`deadline`/`remaining_seconds`).
On a local provider, `effective_budgets()` raises only the token budget and leaves **wall-clock,
rounds, cost untouched** - so on a slow PP node the **wall-clock is the real guardrail** and
done-signal stops over-work.
- **Quality-lane recipe (pxx.toml / env):**
  ```toml
  done_signal = true            # default; explicit on the quality lane - exits at first
                                # objectively-verified edit (scope+diff-cap+lint+tests), not the cap
  [budgets]
  max_wall_seconds = <tuned>    # THE guardrail on slow high-quant nodes (token budget is
                                # meaningless at low tok/s); raise from 1800 only if a legit slow PP
                                # run needs it, but keep bounded
  max_rounds = <tuned>          # keep bounded; done_signal exits earlier on success
  ```
- **Rationale:** slow PP3/Q8 nodes decode at few tok/s, so token budgets don't bind; `max_wall_seconds`
  prevents a runaway and `done_signal` prevents burning rounds after the objective gates already pass.
- **Negative control:** a never-converging task must hit `max_wall_seconds` and report BUDGET/COMPLETED
  cleanly (not hang) - proving the guardrail fires under load.

---

**Handoff:** fixtures are grounded and ready. On your WS0 execution, C6 consumes Fixture 1; Gate 2 /
WS1 consumes Fixture 2; WS2/WS4 consume Fixture 3. Review verdict is PASS with the sharpening notes
above (B4 diff pass/fail, C3 denied-route-never-activates). Green light to build + prove the boundary
gate on the QSFP56 triangle; ping on C3 negative-control observed firing and we start 2+1.
