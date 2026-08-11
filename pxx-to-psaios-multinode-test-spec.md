# Test spec: PP + disaggregation proof (configs 2+1 and 3+0) on current hardware

**Companion to** `multinode-inference-readiness.md`. **Ethos:** plan -> research -> execute ->
prove; receipts only, no shortcuts. **Runnable now** on 3 Sparks + the 36 GB Mac.

**Acceptance is VALUE-ORDERED (security > reliability > accuracy > speed).** A config must PASS
Gates 1-3 to be *eligible*; speed (Gate 4) only *ranks* eligible configs and can NEVER rescue a
config that failed 1-3. This ordering is the anti-shortcut guarantee, applied to EVERY config.

---

## Value-ordered acceptance gates (apply to every config, in order)

### Gate 1 - Security / Governance / Auditability (hard PASS/FAIL)
- A governed `pxx_run` routed to the config yields `code_edit.pxx_run ALLOW pol-083` +
  `file.write ALLOW pol-006` + `shell.exec DENY`, **node/model-agnostic** (proves the gate does
  not depend on which node/model served the tokens).
- **Inter-node data plane:** capture boundary traffic; confirm **no data egresses the firm
  subnet**. Negative control: a deliberately mis-routed off-subnet target must **fail closed**.
- Every request + every inter-node KV/activation transfer is recorded in `firm_audit_log`
  (hash-chain intact, `.head` advances, no gaps).
- **FAIL here = disqualified, regardless of any quality or speed. No exceptions, no waiver.**

### Gate 2 - Reliability (PASS/FAIL)
- **Tool-calling success >= 99%** over N >= 100 structured-output tasks (directly tests the Q3
  tool-calling risk).
- **Stability:** sustained run (>= 1 h or >= X requests) with 0 crashes, 0 pipeline-stage hangs,
  0 KV-transfer failures.
- **Failover:** kill a PP stage / a replica / the decode node mid-run -> defined graceful behavior,
  no corruption, deterministic recovery on restart with no manual surgery.

### Gate 3 - Accuracy (PASS/FAIL)
- **Coding/task quality >= single-node Q3 baseline** on the graded eval set (no regression from
  sharding or quant change).
- **Faithfulness** (for any RAG/answering role) not worse than baseline (reuse the verifier
  method; drop invalid/errored rows before aggregating).
- A higher-quant config (PP Q6/Q8) must **meet or beat** baseline quality to justify its cost.

### Gate 4 - Speed (RANKING only, among Gate 1-3 passers)
- Decode tok/s, TTFT (prefill), inter-token latency p50/p95, concurrent throughput.
- Reported and ranked. **Never used to pass a config that failed 1-3.**

---

## Shared apparatus
- **Prompt sets:** (a) *governance probe* - edits that must hit pol-083/pol-006 + a shell attempt
  that must DENY; (b) *tool-calling* - structured outputs, N >= 100; (c) *coding-quality graded* -
  the deterministic grader / real_runs-style tasks; (d) *faithfulness* - if a RAG role is under
  test; (e) *load* - concurrency ramp.
- **Metrics capture (metadata only, `firm_audit_log`-convergent):** per-request TTFT, inter-token
  latency, tok/s, per-node GPU util + mem-BW, interconnect utilization, per-stage pipeline-bubble
  fraction.
- **Negative controls are MANDATORY on every gate** (a case that SHOULD fail, proven to fail) so a
  green board proves capability, not a broken harness. See the negative-control rule.

---

## WS0 - Research / feasibility spike (BEFORE any benchmarking) [~1 week]
De-risk tooling + governance-enforceability so the matrix is not built on assumptions:
- **Confirm each topology is actually supported on THIS hardware** and pin exact versions: vLLM
  PP + disaggregated prefill/decode on GB10 / CUDA 13; MLX distributed decode on M4 Max;
  llama.cpp-RPC as the PP fallback; EXO heterogeneous (later phase).
- **Prove the KV-transfer path at toy scale** (1 request: Spark prefill -> 36 GB Mac decode)
  before the full disagg benchmark.
- **Prove the inter-node boundary is ENFORCEABLE now:** can psguard + network policy fail-close an
  off-subnet route? Build that control before Gate 1 depends on it.
- **Exit criteria:** each intended topology has a proven-runnable path + a pinned stack, OR is
  explicitly deferred with a logged reason. No silent scope-cut (log what was dropped).

---

## WS2 spec - PP2 -> PP3 (config 3+0)
1. **Baseline:** single Spark, 284B Q3 (fits 118 GB) - record Gates 1-4 as the reference.
2. **PP2:** shard 284B at Q6/Q8 (>120 GB) across 2 Sparks over 200 GbE. Run Gate 1 (governance +
   inter-node boundary, since activations cross Spark<->Spark), Gate 2, Gate 3. Then Gate 4:
   tok/s, inter-token latency, per-stage util, pipeline-bubble fraction.
3. **PP3:** repeat across 3 Sparks.
4. **Scale-out control:** 3 independent Q3 replicas - Gate 2/4 throughput + failover comparison.
5. **TP2 once:** document the ceiling (expected poor over ~25 GB/s) - get the hard number.
6. **Verdict:** among Gate 1-3 passers, does higher-quant PP quality justify its latency vs
   scale-out? Data decides; record the receipt.

## WS3 spec - Disaggregation (config 2+1, on the 36 GB Mac)
1. **Baseline:** single-Spark decode (273 GB/s) - reference decode tok/s + latency.
2. **Disagg:** Spark prefill -> 36 GB Mac decode over TB5 RDMA. **Gate 1 is the focus** (KV crosses
   the Spark->Mac boundary; prove no egress + fail-closed off-subnet). Then Gate 2 (stability of the
   handoff, failover if the decode node drops), Gate 3.
3. **Hypothesis (Gate 4, among passers):** Mac decode (~400-546 GB/s) beats single-Spark decode
   *net of the KV-transfer cost*. Measure; if net-negative, disagg is killed for now (logged).
4. **De-risk the swap:** document the KV path + serialization so the 64 GB unit is a capacity swap,
   not an integration project.

---

## Receipts produced (per config)
1. Gate 1 evidence: governance ALLOW/DENY trace + inter-node no-egress capture + fail-closed
   negative control + `firm_audit_log` chain proof.
2. Gate 2 evidence: tool-calling rate, stability log, failover behavior.
3. Gate 3 evidence: graded-quality delta vs baseline, faithfulness delta.
4. Gate 4 ranking: speed table (eligible configs only).
5. **Decision memo:** PP viable? disagg viable? which production shape (3+1 vs 4), with the ratio
   of quality-PP vs scale-out vs disagg nodes the data supports.
