# bl335 WS0 — research/feasibility spike checklist (psaios v1.1)

**Owner:** psaios (CC CLI, 7960). **Status:** **pxx review PASS (coord seq-11)** on gate-ordering +
negative-control discipline; **CLEARED to execute the boundary gate** on the live QSFP56 triangle.
v1.1 folds pxx's two sharpening notes: **B4/B5** (baseline diff = hard PASS/FAIL + injected-delta
positive control) and **C3b** (denied route must never activate, not just log a DENY), plus the
**D1** no-silent-scope-cut tightening.

**Purpose (firm doctrine):** prove the tooling AND the governance boundary work at toy scale BEFORE
committing the benchmark matrix — no building on assumptions, no bandaid→debt. WS0 is the gate that
lets WS1+ start.

**Exit criteria (all must hold, or the item is logged-deferred with a reason — no silent scope-cut):**
each intended topology has a proven-runnable *pinned* path or a logged deferral; the boundary gate
is GREEN with its **negative control OBSERVED firing**; the baseline-capture/restore harness passes
a dry-run; and the pre-registration artifact is committed. Every check ships a **negative control**
(a case that SHOULD fail, proven to fail) so a green board proves capability, not a broken harness.

---

## A. Pre-registration (measurement-gate protocol) — DO FIRST, before any run

- [ ] **A1** Commit the concrete Gate 1-4 bars + rubric + GO/NO-GO thresholds to the repo BEFORE
  WS1's first measured run (locks the spec's "set in WS1" placeholders in advance).
- [ ] **A2** Hidden acceptance tests live OUTSIDE the repo until scoring; **running and scoring are
  separate programs**; fresh isolated workspace per subject per task.
- [ ] **A3** Pre-register the WS0 exit gates themselves (this file's checkboxes are the contract).
- [ ] **A4** "When subjects fail identically, suspect the ruler" — any correction to a task/rubric
  must be provably spec-wrong, applied to every subject, dropped-not-inverted, disclosed.
- **Ref:** `docs/measurement-gate-protocol.md` (firm-wide since 2026-08-03); reference impl
  `tools/pxx/a2/`.

## B. Baseline-capture / reboot-safe-restore harness (Chris §1 safety spine) — MUST exist before any fabric change

- [ ] **B1** `baseline-capture` script: snapshot `/etc/netplan/*.yaml` + interface/IP/MTU + RoCE/NCCL
  env on each Spark + Mac network config, into a timestamped baseline bundle.
- [ ] **B2** Snapshot serving state (vLLM/Ollama/MLX/OCR up? model+quant+bind+util) via the
  fleet-inference-inventory §4 re-verify commands + `restart-fleet.sh --verify-only`.
- [ ] **B3** Snapshot psguard state: canonical policy hash (`get-fleet-hash.sh`), enforce mode,
  `kernel_state: OK` on all in-scope nodes.
- [ ] **B4** `return-to-baseline` script: stop experimental services → restore netplan from B1 →
  **reboot touched nodes** (clean auto-start to baseline) → re-run B1-B3 sweep → **diff vs the
  captured baseline**. The diff is a **hard PASS/FAIL**, not a "surfaced" note: a silent or
  unexplained delta = a vacuous restore = **FAILED session** (pxx seq-11 sharpening). Every delta is
  either explained-and-accepted or the session fails.
- [ ] **B5** **Dry-run both scripts** on one Spark (no real change) — prove capture→restore→diff is
  clean and idempotent, AND **inject a deliberate delta and prove the diff goes RED** (positive
  control: "restore verified" must be provably *able to fail*, or B4's PASS is vacuous). A session
  that cannot restore baseline is a FAILED session.
- [ ] **B6** Back-up-netplan step is wired as a hard precondition to any fabric edit (trivial now;
  mandatory). After-hours/weekend execution only.

## C. Boundary gate (the net-new psguard surface) — build + prove at toy scale

*Independent of either full config; runs NOW on the already-live switchless QSFP56 triangle (bl326).*

- [ ] **C1** Define CanonicalAction **`inference.node_placement`** + **inv-036** (+ policy in the
  **pol-026 target-allowlist family**), with the firm **private-fabric allowlist**: QSFP56
  `192.168.100.0/24` (switchless triangle), TB5 point-to-point link, mgmt `192.168.111.0/24`.
- [ ] **C2** psrouter emits `inference.node_placement` on any multi-node placement (PP group /
  disagg pair) → authorized by psguard → **audited into `firm_audit_log`** (hash-chain intact,
  `.head` advances, no gaps). Prove one placement writes one clean row.
- [ ] **C3** **Fail-closed enforcement, NEGATIVE CONTROL OBSERVED FIRING:** a placement/route-config
  naming an off-allowlist endpoint is **DENY sev-5 at BOTH route-config load AND per-placement** —
  and this DENY is *observed* in the audit log, not assumed. (Positive-control-that-can-fail ethos,
  like bl331 Layer-3: an on-allowlist placement must ALLOW, so the gate can distinguish.)
- [ ] **C3b** **Prove the denied route NEVER ACTIVATES** (pxx seq-11 sharpening): fail-closed means
  **no placement is served** on a DENY — not merely that a DENY row is written. A denied-but-still-served
  route is the real failure mode. Assert psrouter does not forward the request and no tokens are
  served on the off-allowlist path (check the serving side, not only the audit side).
- [ ] **C4** **Private-interface binding proof:** inter-node transport binds only to the QSFP56
  (`192.168.100.x`) / TB5 interfaces, never a routable/mgmt NIC — verified by actual socket/interface
  inspection, not config assertion.
- [ ] **C5** **Firewall egress-deny backstop** verified on the in-scope nodes (extends
  `docs/fleet-firewall-posture-decision.md`); prove an off-subnet packet from the inference path is
  dropped at the network layer independent of psguard.
- [ ] **C6** **Node/model-agnostic** proof (uses pxx fixture #1): a governed `pxx_run` yields
  `code_edit.pxx_run ALLOW pol-083` / `file.write ALLOW pol-006` / `shell.exec DENY` regardless of
  which node/model served the tokens — as a live enforce-mode assertion.
- **Deliverable:** boundary gate GREEN = C3 negative control observed firing + C2 audit row + C4
  binding + C5 backstop + C6 node-agnostic. This is the green light for config (1) 2+1.

## D. GB10 serving-infra feasibility (toy scale) — prove + PIN before the matrix

- [ ] **D1** vLLM PP + disaggregated prefill/decode confirmed runnable on GB10 / CUDA 13; **pin the
  exact working version OR defer the affected model with a logged reason — no silent scope-cut**
  (pxx seq-11). Flag from bl333: Qwen3-Coder-Next-80B did NOT run on vLLM 0.21.0 as of 2026-08-03 —
  either pin the version that runs it or log it deferred.
- [ ] **D2** MLX distributed decode confirmed on the M4 Max; pin version.
- [ ] **D3** llama.cpp-RPC PP **fallback** confirmed; pin version.
- [ ] **D4** EXO heterogeneous — **defer with a logged reason** (bleeding-edge; gated on the 64 GB
  Mac / bl332), or confirm a toy path if trivial.
- [ ] **D5** **KV-transfer path Spark → 36 GB Mac at 1-request scale** proven (the WS3 prerequisite;
  de-risks the fiddly serialization before the disagg benchmark).
- [ ] **D6** Record every pinned version per lane into the WS1 harness's version manifest.

## E. Co-tenancy soak conditions (Gate-2 pre-condition; the bl283/bl288/bl298 lesson) — SPEC, not run yet

- [ ] **E1** Define the **24h soak overlapping a real bl286 OCR batch** on the shared GB10 pool —
  the co-tenancy/headroom interaction a 1h smoke misses (spark2 free-VRAM→0 was a 24-48h event).
- [ ] **E2** Instrument free-VRAM headroom on the shared vLLM + gpu-ocr (+Whisper) pool; record the
  bl288 admission-threshold interplay; confirm the gpu_ocr two-phase begin/complete audit (bl298) so
  a server-killing request self-identifies rather than self-erasing.
- [ ] **E3** Pre-register the soak PASS bar (0 crashes / 0 KV-transfer failures / 0 unrecovered
  headroom events over the window) as part of A1.

---

## Division of labor (coord seq-9)

**psaios OWNS (this draft):** A (pre-registration), B (baseline/restore safety spine), C (boundary
gate), D1/D3/D4/D5 (serving-infra feasibility), E (co-tenancy soak conditions). psrouter lane/health/
load placement (WS4 counterpart).

**pxx SUPPLIES (fixtures, on draft landing):**
1. node/model-agnostic governed-run assertion (ALLOW pol-083 / pol-006 / shell DENY regardless of
   node) as a live step5-style check → feeds **C6**.
2. real-`_TOOL_MAP` tool-calling harness (write_file/edit_file/run_shell, N≥300) → feeds Gate 2 / WS1.
3. budget/done-signal config for slow high-quant nodes (done-signal early-exit default-on on the
   quality lane; wall-clock-aware budget) → feeds WS2/WS4.

**pxx REVIEWS:** this draft for value-ordered-gate ordering + negative-control discipline BEFORE WS0
executes.

**Shared WS4 anchor:** pxx per-role model routing (review_model/roles overlay: coder→quality lane,
judge→fast lane, unchanged gates) ↔ psaios psrouter lane placement.

## Next
Send this draft to pxx (coord attachment) → pxx returns the 3 fixtures + a review pass → on review
PASS, execute WS0 (build + prove the boundary gate; C3 negative control observed firing) → then
config (1) 2+1. Nothing persists to the fabric outside an after-hours/weekend window under §B.
