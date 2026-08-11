# Plan: Governed multi-node inference readiness (2-month runway to the 64 GB Mac)

**Owner:** firm / PSAIOS (work fleet, NOT the home fleet)
**Window:** 2026-08-10 -> ~2026-10 (64 GB M4 Max arrival). 4th DGX Spark planned ~2027-02.
**Ethos:** prove before we call it. Every claim is a measured receipt, not a spec-sheet number.
**One-line thesis:** the multi-node work upgrades the *model tier*; it does NOT change pxx's
governance surface. Scale the brain, governance stays byte-identical.

---

## 0. Where this fits in enterprise pxx governed by PSAIOS (the spine)

The inference tier sits at **T1, behind psrouter**. pxx never touches a GPU or a node directly:
`pxx run -> psagent skill -> psrouter -> {Spark node / PP group / disagg pair}`. Consequences
that make this whole plan low-risk from a governance standpoint:

- **Model swaps are transparent to governance.** pxx is pinned by *tool surface*
  (`broker._TOOL_CLASSES`), not by the model. As bl330/bl331 proved, changing the model behind
  psrouter needs **no hook/policy/audit re-review**. So PP3, disagg, a 284B frontier model, or a
  fast MoE all light up under the *same* psguard gates (pol-083 `code_edit`, pol-006 `file.write`,
  fail-closed shell) with no pxx re-pin.
- **The one genuinely new governance surface is the inter-node boundary - governed honestly, as a
  control-plane gate + a data-plane backstop (psaios seq-8 correction).** psguard is NOT in the
  RDMA/NCCL packet path, so we do NOT claim per-KV-packet audit (that would be an aspirational
  rule). Instead: (control plane, psguard ENFORCES) psrouter emits a new `inference.node_placement`
  action; psguard checks each node against the firm private-fabric allowlist (**inv-036** +
  **pol-026-family** policy: QSFP56 switchless triangle, TB5 point-to-point, mgmt subnet) and
  **fails closed DENY sev-5** off-allowlist, hash-chaining the placement into `firm_audit_log`;
  (data plane, topology + firewall, NOT psguard) transport is bound to the private-fabric
  interfaces (switchless QSFP56 = no gateway = no off-subnet route; TB5 point-to-point) + a
  firewall egress-deny backstop. No-egress is thus **provable** (allowlist gate + interface binding
  + firewall + audited placement), not asserted. psaios owns this gate (bl335). Close before
  production (WS4).
- **Audit unchanged in shape.** Routed inference + governed pxx runs still land in
  `firm_audit_log` (bl305 coupling; audit writer = pxx `events.py` AuditLog). Multi-node adds
  *placement/routing* decisions to the audited record.

**Net:** we are standing up a bigger, faster, or more-parallel model tier under an unchanged
governance kernel, plus one new boundary (inter-node data) to bring under psguard.

---

## 1. Target configurations (from the 2+1 / 3 / 3+1 / 4 enumeration)

"+1" = the disaggregated **Spark-prefill / Mac-decode** pair as one logical unit.

| Config | Sparks | Mac | Feasible | Purpose |
|---|---|---|---|---|
| **2+1** | 3 (2 + 1 in pair) | 1 | now (3 Sparks) | prove PP2 *and* disagg together |
| **3**   | 3 | - | now | max single-model capacity (PP3) *or* 3x scale-out |
| **3+1** | 4 (3 + 1 in pair) | 1 | needs 4th Spark (~Feb 2027) | production: big-model tier + latency lane |
| **4**   | 4 | - | needs 4th Spark | biggest model (PP4) *or* 4x scale-out |

Every node count has two readings, answering different questions:
- **PP (shard one model across the group):** only justified when the model exceeds one node's
  120 GB (e.g. 284B at Q6/Q8 ~ 200-300 GB). This is the *experiment*, unproven over 200 GbE.
- **Scale-out (each node runs its own full model):** better throughput + fault isolation; the
  *safe fallback* if PP throughput disappoints. Same node count, zero interconnect risk.

The disagg "+1" is orthogonal (a latency play) and bolts onto any group.

**Sequencing:** the two feasible-now configs are the *experiments* and we run them BEFORE the
64 GB arrives, on the **36 GB Mac in hand**:
- **2+1** = 2 Sparks + (1 Spark prefill / 36 GB Mac decode). Proves PP2 *and* disagg together. The
  36 GB caps the decode-side KV budget, but proves the mechanism so October is a capacity swap.
- **3+0** = 3 Sparks, no Mac. PP3 vs 3-independent, head to head.
The two post-4th-Spark configs (**3+1**, **4**) are the *production candidates the data selects
between* (~2027-02).

---

## 1a. Which config serves pxx vs psaios (they differ; routing reconciles)

The "best config" is not one topology, because pxx and psaios stress the fleet differently:

- **pxx (governed autonomous coding)** is **quality- and tool-calling-sensitive, not primarily
  speed-sensitive.** done-signal early-exit + healing loops mean a *better* model yields *fewer*
  rounds = cheaper net, even at lower tok/s. So pxx wants a **high-quality lane**: the frontier
  model at the best quant it can get. If Q3 tool-calling holds, a single Spark; if not, **PP2/PP3
  to reach Q6/Q8**. pxx also wants **per-role routing** so its cheap judge/reviewer role does not
  sit on the expensive quality lane.
- **psaios (multi-role firm serving)** is **concurrency- and resilience-sensitive** (no backups).
  It wants **scale-out (3+0 as 3 independent replicas)** for throughput + fault isolation + per-role
  placement, plus the **disagg "+1"** as a low-latency lane for interactive roles (chat, triage).

**They do not actually conflict, because psrouter routes by role.** The same governed run sends its
coder role to the quality/PP lane and its judge role to a fast replica; throughput roles land on
scale-out; interactive roles on disagg. So the production shape (3+1 or 4) is **heterogeneous by
design**, and the experiments tell you the *ratio* (how many nodes as a quality PP group vs
scale-out replicas vs the disagg pair). That is the real deliverable of the "now" tests.

---

## 2. The 2-month workstreams

**Acceptance is value-ordered (security > reliability > accuracy > speed).** See the companion
`multinode-test-spec.md`: every config must pass Gate 1 (security/governance/audit), Gate 2
(reliability), Gate 3 (accuracy) to be *eligible*; speed (Gate 4) only ranks eligible configs and
can never rescue a config that failed 1-3. Auditability + security stay intact through every
iteration by design.

### WS0 - Research / feasibility spike (week 0, BEFORE benchmarking)
**psaios drafts this checklist** (owns the boundary gate + GB10 serving-infra + co-tenancy soak
conditions); pxx supplies the pxx-side items (governed-run assertion, real-`_TOOL_MAP` harness,
budget/done-signal) and reviews for gate-ordering + negative-control discipline. Prove tooling +
the governance boundary at toy scale before committing the matrix (avoids bandaid -> debt):
**boundary gate FIRST, standable-up now on the live QSFP56 triangle** (placement allowlist +
fail-closed + audited placement, negative control OBSERVED firing); confirm + pin the serving
stacks (vLLM PP + disagg; MLX distributed decode; llama.cpp-RPC fallback; EXO later); prove the KV
path Spark->36 GB Mac at 1-request scale. Exit: each topology has a proven-runnable pinned path or
is deferred with a logged reason. Detail in the test spec.

### WS1 - Baseline + harness (weeks 1-2)
- **Single-node baselines** on each of the 3 Sparks and the 36 GB Mac, on the real candidate
  models (DeepSeek-V4-Flash 284B Q3; gpt-oss-120b Q6; the pscode 8-role code model). Capture:
  TTFT, inter-token latency, tok/s, GPU/mem-BW utilization. These are the numbers every
  multi-node config must *beat* to be worth its complexity.
- **Reusable benchmark harness** that records to a store convergent with `firm_audit_log`
  (metadata only, no prompt bodies): per-run metrics + node placement + interconnect utilization.
  Bake in a **negative control** on every metric (a run that *should* regress, to prove the
  harness can show a loss - no vacuous green).
- **Pin the serving stack per lane** and record versions: Sparks (CUDA) = vLLM (PP + disagg
  support) with llama.cpp-RPC as the simple PP fallback; Mac lane = MLX; heterogeneous = EXO.
  Confirm each supports the topology we intend *before* week 3.

### WS2 - Prove PP2 -> PP3 on the Sparks (weeks 3-5)
- **PP2:** shard a >120 GB model (284B at Q6/Q8) across 2 Sparks over 200 GbE. Measure tok/s,
  inter-token latency, per-stage utilization, pipeline-bubble fraction. Compare to single-node Q3.
- **PP3:** same across 3 Sparks.
- **Scale-out control:** 3 independent replicas, for throughput + redundancy comparison.
- **TP2 once:** benchmark tensor-parallel a single time to *document the ceiling* (expected poor
  over 25 GB/s). Get the hard number instead of asserting it.
- **Pass/fail bars (set concretely in WS1):** e.g. interactive lane needs >= N tok/s and
  <= M ms inter-token; the quant/quality gain from sharding must justify the latency cost, else
  PP is killed in favor of scale-out. Decision is data-driven.

### WS3 - Prove disaggregation on the CURRENT 36 GB Mac (weeks 4-6, overlaps WS2)
This is the **dress rehearsal for the 64 GB Mac** - prove the mechanism now so October is plug-in.
- **Spark prefill -> 36 GB Mac decode** across the network. The 36 GB caps the decode-side KV
  budget (smaller max context/model), but it exercises the full path: KV-cache transfer, format,
  MLX decode, the handoff.
- **Hypothesis to prove:** Mac decode (BW ~400-546 GB/s) beats single-Spark decode (273 GB/s) on
  the BW-bound decode phase, net of the KV-transfer cost. Measure; kill if net-negative.
- **De-risk the fiddly bit:** nail the KV-transfer path + serialization on 36 GB so the 64 GB unit
  is a capacity swap, not an integration project.

### WS4 - Governance integration (weeks 5-8, continuous)
- **Register new routes in psrouter** as governed endpoints: prefill node, decode node, PP group.
  Placement/routing decisions become audited events.
- **Close the inter-node boundary (psaios-owned, bl335) - control-plane gate + data-plane
  backstop:** psrouter emits `inference.node_placement`; psguard enforces the private-fabric
  allowlist (inv-036 + pol-026-family), fail-closed DENY sev-5 off-allowlist, at route-config load
  AND per-placement, audited into `firm_audit_log`. Data plane: bind transport to private-fabric
  interfaces (switchless QSFP56, TB5 point-to-point) + firewall egress-deny. **No per-KV-packet
  audit claimed** - no-egress is topology + firewall + allowlist-gate + audited placement. The
  negative control (mis-routed off-subnet target) must be **observed** to DENY. Green before prod.
- **Reconfirm pxx pin-compat holds on the new tier:** a governed `pxx_run` routed through PP3 /
  disagg still yields `code_edit.pxx_run ALLOW pol-083` + `file.write ALLOW pol-006` +
  `shell.exec DENY`, regardless of which node/model served the tokens. Add a step5-style
  assertion that proves the gate is node/model-agnostic.
- **End-to-end governed proof:** one real governed code edit, routed to the new tier, gated by
  psguard, landing in `firm_audit_log`. That is the receipt that the model tier changed and
  governance did not.

### WS5 - 64 GB Mac readiness checklist (week 8 / on arrival)
So the unit is plug-and-play, all of the following must already be true and measured:
- MLX + EXO installed and **version-pinned**; TB5 RDMA link validated (dual-TB5 bonded, RDMA
  path confirmed, ~20 GB/s measured).
- Disagg KV path **already proven on the 36 GB Mac** (WS3).
- psrouter decode-node route **templated**; psguard inter-node boundary policy in place (WS4);
  audit wired.
- Documented what the 64 GB unlocks: (a) the 36+64 MLX **shard pair** for a model too big for
  either Mac alone; (b) a larger decode-side KV budget for disagg (longer context / bigger model
  on the decode side).

---

## 3. What arrival unlocks, mapped to the timeline
- **Now (3 Sparks + 36 GB Mac):** prove **2+1** (PP2 + disagg-on-36 GB) and **3** (PP3 vs
  3-independent). Governance integrated (WS4). Two measured receipts in hand.
- **~Oct (64 GB Mac):** disagg decode scales to 64 GB; the 36+64 Mac pair becomes an MLX shard
  lane; move toward the **3+1 / 4** production shape.
- **~Feb 2027 (4th Spark):** select the production config (**3+1** or **4**) from the data the
  "now" experiments produced - not a spec sheet.

## 3a. Roadmap features to get "over the line" (the leverage)

Hardware alone does not leverage the stack; pxx and psaios each need code to exploit it. The
**keystone on both sides is role/lane-aware routing** - it is what lets a single governed run use
the whole heterogeneous fleet transparently, under unchanged psguard gates.

**pxx features (agent runtime):**
1. **Per-role model routing** - ALREADY on pxx's roadmap (`review_model` / `roles` overlay ->
   resolve each role to its own endpoint). The single highest-leverage feature: one governed run
   splits coder (quality lane) + judge (fast lane). *Bring this over the line first.*
2. **Task-class -> lane hint** passed to psrouter ("heavy refactor" -> quality; "lint fix" ->
   fast; "draft" -> spec-decode). Lets pxx ask for the right lane without knowing the topology.
3. **Parallel governed runs across replicas** - per-goal-DAG-node endpoint assignment so
   independent sub-tasks fan out to the 3 scale-out nodes = ~3x throughput.
4. **Time-aware / provider-aware token budget** (revive the deferred 2.3.2 item) - budget by
   wall-clock, not just tokens, so a slow high-quant PP node cannot blow the run's time budget.
5. **done-signal early-exit** - ALREADY shipped (2.3.7); keep default-on for the quality lane,
   where it saves the most (fewer expensive slow-node rounds). No new work, just leverage.
6. **Disagg-latency tolerance** - pxx timeouts/streaming must expect the disagg profile (higher
   TTFT from the prefill handoff, then fast decode). Mostly config, but verify.

**psaios features (governance + routing):**
1. **psrouter role/lane/health/load routing** (keystone counterpart) - know the topology
   (prefill / decode / PP-group / replicas), route by role + latency-class + load + health,
   present a PP group as ONE logical endpoint, load-balance replicas, fail over on node death,
   orchestrate the disagg prefill->decode handoff.
2. **psguard inter-node data-plane governance (NEW surface)** - a policy/invariant class for
   "activations/KV cross a node boundary": fail-closed if a route would egress the firm subnet,
   audit every transfer. This is the net-new governance code the stack requires.
3. **firm_audit_log placement records** - record node/lane/model per request + inter-node
   transfers, so the no-egress guarantee is provable, not asserted.
4. **Per-node model/version pinning** extended to the multi-node tier (a PP group's model must be
   consistent across its nodes; bl327/bl331 pin model applies per node).
5. **Fleet observability** - per-node/lane metrics (tok/s, latency, util, interconnect) feeding
   both routing decisions and the WS1 benchmark harness.
6. **Placement config** (static now, dynamic later) - which model on which node/lane.

**"Over the line" (the milestone all of the above converge on):** a *single governed pxx run*
can, transparently and under unchanged psguard gates, route its coder role to the frontier quality
lane and its judge role to a fast lane; psrouter places each on the right node; the inter-node data
plane is proven fail-closed; and every hop lands in firm_audit_log. Feature #1 on each side
(pxx per-role routing + psrouter lane placement) is the pair that unlocks the most and should lead.

---

## 4. Risks, unknowns, kill-criteria
- **PP over 200 GbE may not be throughput-viable** -> fallback is scale-out independent (already
  the safe default; no schedule risk).
- **Disagg KV-transfer latency may eat the Mac-decode BW win** -> WS3 measures it; kill if net-neg.
- **Serving-stack maturity:** multi-node vLLM over RoCE is fussy; MLX-distributed is young; EXO
  heterogeneous is bleeding-edge -> pin versions, keep llama.cpp-RPC as the PP fallback.
- **Governance:** inter-node data plane is a new egress surface -> WS4 must close it fail-closed
  before production.
- **Vacuous-benchmark risk:** every metric ships with a negative control (a run that should
  regress), so a green board proves capability, not a broken harness.

## 5. Deliverables (receipts)
1. Single-node baseline receipt (WS1).
2. PP2/PP3/scale-out + TP2-ceiling benchmark receipts (WS2).
3. Disagg prefill/decode receipt on 36 GB, with the Mac-decode-beats-Spark verdict (WS3).
4. Governance receipts: psrouter routes, inter-node fail-closed boundary proof, node/model-agnostic
   pin-compat assertion, one end-to-end governed pxx_run on the new tier (WS4).
5. 64 GB readiness checklist, signed off (WS5).
6. Decision memo: PP viable? disagg viable? which production config (3+1 vs 4)?

---

## Appendix - hardware context (work fleet)
- **Ingest/preprocess:** Dell Precision 7960 (RHEL, psadmin; psrouter + psaios coord-agent host),
  2x 16 GB (RTX 5080 + RTX 5060 Ti OC) -> OCR + Whisper.
- **Inference:** 3x DGX Spark 128 GB (GB10, 273 GB/s, ConnectX ~200 GbE = ~25 GB/s inter-node;
  3-node ring = full K3 mesh). 4th planned ~2027-02.
- **MLX/experimental:** M4 Max Mac Studio 36 GB now, 64 GB ~Oct 2026; paired over dual TB5 RDMA
  (~20 GB/s, RDMA zero-copy/low-latency; decode BW ~400-546 GB/s > GB10 273).
- Home fleet (T5810 / asrock / Mac mini) is SEPARATE and out of scope here.
