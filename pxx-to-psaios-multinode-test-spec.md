# Test spec v2: PP + disaggregation proof (configs 2+1 and 3+0) on current hardware

**Companion to** `multinode-inference-readiness.md`. **Ethos:** plan -> research -> execute ->
prove; receipts only, no shortcuts, no unbacked (aspirational) rules. **Runnable now** on 3 Sparks
+ the 36 GB Mac. **v2 folds in psaios seq-8 corrections** (honest boundary scope, pre-registration,
statistical + soak + leakage tunings).

**Acceptance is VALUE-ORDERED (security > reliability > accuracy > speed).** A config must PASS
Gates 1-3 to be *eligible*; speed (Gate 4) only *ranks* eligible configs and can NEVER rescue a
config that failed 1-3. This ordering is the anti-shortcut guarantee, applied to EVERY config.

**Execution order (psaios seq-8):** (0) boundary gate NOW on the live QSFP56 triangle (in WS0,
independent, no new hardware) -> (1) config **2+1** (disagg is highest integration risk; 36 GB
dress-rehearsal) -> (2) config **3+0** (pure throughput; only selects the production ratio, not
needed until the 4th Spark ~Feb 2027).

---

## Value-ordered acceptance gates (apply to every config, in order)

### Gate 1 - Security / Governance / Auditability (hard PASS/FAIL)
- Governed `pxx_run` routed to the config yields `code_edit.pxx_run ALLOW pol-083` +
  `file.write ALLOW pol-006` + `shell.exec DENY`, **node/model-agnostic** - a LIVE step5-style
  assertion against the real enforce-mode daemon, not a mock.
- **Inter-node boundary (honest split - psguard is NOT in the RDMA/NCCL packet path):**
  - *Control plane (psguard ENFORCES):* psrouter emits a new `inference.node_placement` action on
    every multi-node assignment; psguard checks each node against the firm private-fabric allowlist
    (**inv-036** + a **pol-026-family** policy: QSFP56 192.168.100.0/24 switchless triangle, TB5
    point-to-point, mgmt 192.168.111.0/24). Off-allowlist -> **fail-closed DENY sev-5** at
    route-config load AND per-placement. The placement/route decision is hash-chained into
    `firm_audit_log`.
  - *Data plane (topology + firewall backstop, NOT psguard):* inter-node transport bound to the
    private-fabric interfaces (switchless QSFP56 = no gateway = no route off-subnet; TB5
    point-to-point) + a network egress-deny backstop.
  - **No per-KV-packet audit is claimed.** No-egress is proven by: allowlist gate (fails closed) +
    private-interface binding + firewall egress-deny + audited placement decisions. Provable, not
    asserted. (We do not ship an aspirational rule.)
- **Negative control OBSERVED firing:** a deliberately mis-routed off-subnet target must be *seen*
  to DENY (captured, not assumed) - the positive-control-that-can-fail discipline (cf. bl331 L3).
- **FAIL here = disqualified, regardless of any quality or speed. No exceptions, no waiver.**

### Gate 2 - Reliability (PASS/FAIL)
- **Tool-calling >= 99% over N >= 300** (or report a bootstrap CI; N=100 is too thin - 1 failure =
  99%, cannot resolve 99 from 96), measured on **pxx's REAL `_TOOL_MAP` schema** (write_file /
  edit_file / run_shell / ...), NOT a generic function-calling probe - else it is not testing the
  actual Q3 risk.
- **Stability = SOAK, not smoke:** a **24 h soak that OVERLAPS a real OCR batch** (bl286 corpus).
  The GB10 nodes are co-tenant (vLLM + OCR + Whisper on shared unified memory); the failure that
  bit spark2 (bl283) was a free-VRAM->0 headroom event at 24-48 h, invisible at 1 h. Watch the
  admission-threshold interplay (bl288). Require: 0 crashes, 0 stage hangs, 0 KV-transfer failures,
  0 OOM/headroom events across the window.
- **Failover:** kill a PP stage / a replica / the decode node mid-run -> defined graceful behavior,
  no corruption, deterministic recovery on restart with no manual surgery.

### Gate 3 - Accuracy (PASS/FAIL)
- **Coding/task quality >= single-node Q3 baseline** on the graded eval set (no regression from
  sharding or quant change).
- **Held-out / leakage probe (required):** a higher-quant PP model must not just match in-domain,
  it must NOT regress on a held-out set (the modeleval lesson: CM-SME won in-domain but failed the
  leakage probe). In-domain-only wins are not accepted.
- **Faithfulness** (any RAG/answering role) not worse than baseline (reuse the verifier method;
  drop invalid/errored rows before aggregating).
- A higher-quant config (PP Q6/Q8) must **meet or beat** baseline to justify its cost.

### Gate 4 - Speed (RANKING only, among Gate 1-3 passers)
- Decode tok/s, TTFT (prefill), inter-token latency p50/p95, concurrent throughput.
- Reported and ranked. **Never used to pass a config that failed 1-3.**

---

## Shared apparatus
- **Pre-registration (firm measurement-gate protocol):** the concrete bars, rubric, GO/NO-GO
  thresholds, and **hidden acceptance tests** are committed **OUTSIDE the repo BEFORE WS1's first
  run** (not "set during WS1"); **running and scoring are SEPARATE programs**; **fresh workspace
  per subject per task**. Goalposts are locked before any data exists.
- **Prompt sets:** (a) governance probe (edits hitting pol-083/pol-006 + a shell attempt that must
  DENY); (b) tool-calling on the real `_TOOL_MAP`, N >= 300; (c) coding-quality graded set +
  held-out leakage set; (d) faithfulness (if a RAG role); (e) load/concurrency ramp; (f) 24 h soak
  co-scheduled with the bl286 OCR batch.
- **Metrics (metadata only, `firm_audit_log`-convergent):** TTFT, inter-token latency, tok/s,
  per-node GPU util + mem-BW + free-VRAM headroom over time, interconnect utilization, per-stage
  pipeline-bubble fraction.
- **Negative controls MANDATORY on every gate** (a case that SHOULD fail, proven to fail) - a green
  board proves capability, not a broken harness.

---

## WS0 - Research / feasibility spike (BEFORE any benchmarking) [~1 week]
**Ownership: psaios drafts this checklist** (owns the boundary gate + serving-infra on the actual
GB10/CUDA 13 nodes + the co-tenancy soak conditions - bl283/bl286/bl288 + the measurement-gate
protocol). **pxx supplies the pxx-side items** (governed-run node/model-agnostic assertion, the
real-`_TOOL_MAP` tool-calling harness, budget/done-signal config) and **reviews** the whole for
gate-ordering + negative-control discipline.
- **Boundary gate FIRST, standable-up NOW** on the live switchless QSFP56 triangle (bl326),
  independent of any full config: prove the placement allowlist + fail-closed DENY sev-5 + audited
  `inference.node_placement`, with the **negative control OBSERVED firing**. Smallest boundary, no
  new hardware.
- **Confirm + pin each serving stack on THIS hardware:** vLLM PP + disaggregated prefill/decode on
  GB10 / CUDA 13; MLX distributed decode on M4 Max; llama.cpp-RPC as the PP fallback; EXO later.
- **Prove the KV-transfer path at toy scale** (1 request: Spark prefill -> 36 GB Mac decode).
- **Exit criteria:** each topology has a proven-runnable pinned path OR is deferred with a logged
  reason. No silent scope-cut.

---

## WS2 spec - PP2 -> PP3 (config 3+0) [runs AFTER 2+1]
1. **Baseline:** single Spark, 284B Q3 (fits 118 GB) - reference Gates 1-4.
2. **PP2:** shard 284B at Q6/Q8 across 2 Sparks over the QSFP56 fabric. Gate 1 (placement allowlist
   + audit), Gate 2, Gate 3; then Gate 4 (tok/s, inter-token latency, per-stage util, bubble frac).
3. **PP3:** repeat across 3 Sparks.
4. **Scale-out control:** 3 independent Q3 replicas - Gate 2/4 throughput + failover comparison.
5. **TP2 once:** document the ceiling (expected poor over ~25 GB/s) - hard number, not an assertion.
6. **Verdict:** among Gate 1-3 passers, does higher-quant PP quality justify its latency vs
   scale-out? Data decides; record the receipt. (Selects the production RATIO; not needed until the
   4th Spark.)

## WS3 spec - Disaggregation (config 2+1, on the 36 GB Mac) [FIRST full config]
1. **Baseline:** single-Spark decode (273 GB/s) - reference decode tok/s + latency.
2. **Disagg:** Spark prefill -> 36 GB Mac decode over TB5 RDMA. **Gate 1 focus:** the placement
   allowlist must include the TB5 link; prove fail-closed off-subnet + audited placement. Gate 2
   (handoff stability, decode-node failover), Gate 3.
3. **Hypothesis (Gate 4, among passers):** Mac decode (~400-546 GB/s) beats single-Spark decode
   *net of the KV-transfer cost*. Measure; if net-negative, disagg is killed for now (logged).
4. **De-risk the swap:** document the KV path + serialization so the 64 GB unit is a capacity swap,
   not an integration project.

---

## Receipts produced (per config)
1. Gate 1: node/model-agnostic ALLOW/DENY live-assertion trace + placement-allowlist fail-closed
   negative control OBSERVED firing + `firm_audit_log` placement-chain proof + private-interface /
   firewall no-egress evidence.
2. Gate 2: tool-calling rate (N>=300, real schema) + 24 h co-tenant soak log (headroom over time) +
   failover behavior.
3. Gate 3: graded-quality delta vs baseline + held-out leakage-probe delta + faithfulness delta.
4. Gate 4: speed table (eligible configs only).
5. **Decision memo:** PP viable? disagg viable? which production shape (3+1 vs 4), with the
   node-role ratio the data supports.
