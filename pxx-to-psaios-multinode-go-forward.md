# Multinode inference - go-forward plan (post Gate-1-live)

**As of 2026-08-11.** Milestone LANDED: the inter-node governance boundary (Gate 1) is **LIVE +
GREEN + enforced on the fleet**. inv-036 + pol-084 deployed, 8/8 at hash `8418963122bd7e58`
(56 pol / 21 inv), live enforce-mode C3/C3b observed against the deployed daemon. Value-order held:
the security gate was proven live BEFORE any serving experiment. Nothing emits
`inference.node_placement` in production yet, so zero traffic impact; the gate is permanent and
holding underneath everything that follows.

This plan is the remaining serving path. **Chris opens the serving window; no rush.**

## Sequence (each step gates the next)

0. **WS0-A pre-registration (FIRST, before any measured run):** lock the WS1 / 2+1 gate bars +
   rubric + hidden acceptance tests OUTSIDE the repo; running and scoring as separate programs;
   fresh workspace per subject per task. The baselines are the reference the gates compare against,
   so pre-registration precedes the first baseline run. (Firm measurement-gate protocol.)

1. **WS0-D serving-infra feasibility (psaios-owned, toy scale) + PIN:** confirm + pin vLLM PP +
   disaggregated prefill/decode on GB10 / CUDA 13 (re-check the bl333 Qwen3-Coder-Next-80B /
   vLLM-0.21 flag), MLX distributed decode on the M4 Max, llama.cpp-RPC PP fallback. EXO deferred
   (gated on the 64 GB Mac). Record every pinned version in the WS1 manifest.

2. **D5 KV-path (Spark -> 36 GB Mac, 1 request):** prove the disagg handoff at toy scale (the WS3
   prerequisite). Needs the TB5 RDMA link -> a section-B after-hours fabric window (netplan backed
   up; baseline-capture -> change -> reboot-safe-return).

3. **WS1 single-node baselines:** each Spark + the 36 GB Mac, on the candidate models - capture the
   Gate 1-4 reference metrics (TTFT, inter-token latency, tok/s, free-VRAM headroom). The numbers
   every multinode config must beat. Uses **pxx Fixture 2** (real `_TOOL_MAP`, N >= 300, bootstrap
   CI, at serving quant).

4. **Config 2+1 (first full config):** 2 Sparks PP + 1 Spark-prefill / 36 GB-Mac-decode disagg
   pair. Value-ordered gates (security > reliability > accuracy > speed; speed ranks only).
   **pxx Fixture 1** (node/model-agnostic governed-run -> C6) + **Fixture 2** wired in.

5. **Config 3+0 (then):** PP3 vs 3-independent - pure throughput; selects the production ratio.
   Not needed until the 4th Spark (~2027-02).

## Optional now (no serving stack needed)
Run **Fixture 1** against two LIVE Spark endpoints to close **C6** (node/model-agnostic governance
on the enforce fleet) independent of the disagg stack - locks that governance proof early.

## Open decision gates (Chris)
- **(a)** When to open the serving window (WS0-D + WS1).
- **(b)** The TB5 fabric window for D5 (section-B, after-hours).

## pxx owes when the window opens
F1 (runnable node/model-agnostic assertion), F2 (real `_TOOL_MAP` harness), F3 (done_signal +
`max_wall_seconds` config recipe) - all specified + accepted; wire in at WS1 / 2+1.

## Loose ends (filed, not blocking)
- `bl335-boundary-gate` branch may be deleted whenever (merged + deployed).
- **bl336** (enforce-daemon-follows-working-tree hardening) filed.
- restart-fleet stale accept-log hardening (bl336-adjacent) filed.

## Roadmap anchor (WS4, both sides)
pxx per-role model routing (`review_model`/`roles` overlay: coder -> quality lane, judge -> fast
lane, unchanged gates) <-> psaios psrouter lane/health/load placement. The keystone that lets a
single governed run use the whole heterogeneous fleet under the now-live gate.
