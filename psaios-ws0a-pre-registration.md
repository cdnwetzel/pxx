# bl335 WS0-A — measurement-gate PRE-REGISTRATION (locked before WS1's first run)

**Owner:** psaios (CC CLI, 7960). **Reviewer:** pxx (coord `coord/pxx-psaios`).
**Status:** DRAFT for pxx review (checklist §A). **Binding once pxx PASSes + Chris acks — no
post-hoc bar changes except under §A4.**

**Why this file exists (firm doctrine, `docs/measurement-gate-protocol.md`, adopted 2026-08-03):**
the concrete bars + rubric + GO/NO-GO thresholds + hidden acceptance tests are committed **BEFORE**
the first measured run, so a green board proves capability, not a tuned ruler. This locks the
"set concretely in WS1" placeholders in `docs/bl335-multinode-inference-readiness.md` §3 in advance.
Reference implementation of the run≠score split: `tools/pxx/a2/` (`run_arm.py` runs, `score.py`
scores, hidden `tasks.json`/scores).

---

## 0. Subjects, tasks, and the run/score split

**Subjects (each is one serving config, scored independently):**
- `S-spark1`, `S-spark2`, `S-spark3` — single-node baselines (WS1), the reference floor.
- `S-mac36` — single-node 36 GB M4 Max baseline (WS1).
- `C-2plus1` — 2 Sparks PP + (1 Spark prefill / 36 GB Mac decode) over TB5 (WS3).
- `C-3plus0` — PP3 across 3 Sparks, and 3-independent-replica scale-out (WS2).

**Protocol invariants (locked, all subjects):**
1. **Running and scoring are SEPARATE programs.** The runner emits raw per-task artifacts; a
   separate scorer reads them. Neither the runner nor the model sees the scoring key.
2. **Hidden acceptance tests live OUTSIDE the repo until scoring** — under `~/bl335-hidden/`
   on the 7960 (NOT tracked; `gate2_toolcalls/`, `gate3_accuracy/`, `gate3_leakage/`), revealed
   into a scoring run only. The repo carries the *bars*, never the answer key.
3. **Fresh isolated workspace per subject per task** — no cross-task state; a task never sees a
   prior task's edits.
4. **Never gate on a component's self-report** (exit code / health field / "tests passed"): the
   scorer verifies out of band (A2 caught a tool reporting failure on correct runs).
5. **Negative control mandatory on every gate** — a case that SHOULD fail, proven to fail, in the
   same run — so a green line proves the harness can distinguish, not that it is broken.

---

## 1. Gate 1 — governance / audit  (HARD PASS/FAIL, no waiver, ranks nothing)

**Bar:** a governed `pxx_run` on the config yields, as a **live step5-style assertion against the
real enforce-mode daemon** (hash `8418963122bd7e58`, 56/21):
- `code_edit.pxx_run` → **ALLOW pol-083**
- in-scope `file.write` → **ALLOW pol-006**
- `run_shell` (auto) → **shell.exec DENY** (minted identity has no shell class)
- **node/model-agnostic:** identical verdicts regardless of which Spark node / model served the
  tokens (this is C6, generalized to every config).
- **inter-node negative control OBSERVED firing:** a placement naming an off-fabric / wrong-port /
  mixed endpoint → **DENY inv-036 sev-5**, seen in `firm_audit_log` (not assumed), and the route
  **never activates** (C3b — no tokens served on the denied path).
- every request + KV transfer recorded in `firm_audit_log`, hash-chain intact, `.head` advances.

**GO/NO-GO:** ALL of the above hold → PASS. ANY deviation → **FAIL, no speed waiver**; the config is
ineligible regardless of Gates 2-4. (Positive-control-that-can-fail: the on-fabric ALLOW + the
off-fabric DENY must both be observed, or the assertion is vacuous — bl331 Layer-3 ethos.)

## 2. Gate 2 — reliability  (PASS/FAIL to be eligible)

**Bar:** tool-calling success on pxx's **real `_TOOL_MAP` schema**
(`write_file`/`edit_file`/`read_file`/`list_files`/`search_files`/`run_shell`), NOT a generic
function-calling probe. **N = 500 (LOCKED**, over the 300 floor — tighter bootstrap CI). **Statistic
(LOCKED, pre-registered so it can't be chosen after the data):** gate on **point ≥ 99% AND the
bootstrap 95% lower bound ≥ 98%** — the point estimate alone is too brittle at this N. **Serving quant
(method LOCKED):** each subject is measured at the quant it will ACTUALLY serve at per the WS0-D pin
manifest per lane (Q3 single-Spark; Q6/Q8 PP); the specific per-subject value locks when the WS0-D
pins land — still before WS1's first run, so pre-registration holds. Task set: pxx Fixture 2 generates
the 500 schema-correct tasks (in/out-of-scope mix incl. the run_shell-in-auto negative control);
psaios places them in `~/bl335-hidden/gate2_toolcalls/`.

**Stability = a SOAK, not a 1h smoke** (the bl283 lesson): a **24 h run overlapping a real bl286 OCR
batch** on the shared GB10 pool, instrumenting free-VRAM headroom and the bl288 admission-threshold
interplay (the spark2 free-VRAM→0 event was a 24-48 h co-tenancy failure invisible at 1 h). See §5 (E3).

**Failover sub-bar:** kill one PP stage / replica / decode node → **defined graceful behavior +
deterministic restart** (no silent wedge, no data loss), proven once per multi-node config.

**Negative control (enumerated):** the harness includes a **run_shell-in-auto** case that MUST be
counted as a **DENY/FAILURE** (the governed deny path is exercised, so a green board proves the
counter can register the deny — not that the deny path was never hit); plus a deliberately malformed
tool-schema call that must score as a FAILURE. Either failing to register → the harness is vacuous.

**GO/NO-GO:** point ≥ 99% **AND** bootstrap 95% lower bound ≥ 98% **AND** soak = 0 crashes / 0
unrecovered headroom events **AND** failover graceful **AND** both negative controls register → PASS.
Else FAIL (ineligible).

## 3. Gate 3 — accuracy  (PASS/FAIL to be eligible)

**Bar:** coding/task quality **≥ the single-node Q3 baseline** for the same model family (no
regression from sharding or a quant change). Plus a **held-out / leakage probe** (the modeleval
lesson — a model can win in-domain and fail leakage): a config that beats the baseline in-domain but
fails the held-out probe **does not PASS**. Drop invalid/errored rows BEFORE aggregating (disclosed
count).

**Baseline source:** `S-spark*`/`S-mac36` WS1 scores become the Q3 floor each multi-node config is
measured against (so Gate 3 is defined only after WS1 baselines exist — locked here as the *rule*,
the numeric floor is the WS1 output).

**Negative controls (enumerated):** (1) a known-wrong reference answer in the scoring set must score
FAIL (the accuracy scorer isn't rubber-stamping); (2) **leakage-probe control** — a deliberately
poisoned/leaked item the model MUST get **wrong-or-flagged**, distinct from the held-out item it
should pass, so the leakage probe can actually go RED (a config that "passes" a leakage probe with no
item able to fail it has proven nothing).

**GO/NO-GO:** in-domain ≥ baseline **AND** held-out/leakage ≥ baseline **AND** both negative controls
register → PASS. Else FAIL (ineligible).

## 4. Gate 4 — speed  (RANKED ONLY — never rescues a Gate 1-3 failure)

Reported metrics among Gate 1-3 passers only: decode tok/s, TTFT, inter-token latency p50/p95,
concurrent throughput. **Ranking, not a gate** — speed can never make an ineligible config eligible.
No pre-registered threshold; the *ratio selection* (PP vs scale-out) reads off this ranking.

## 5. E3 — 24 h co-tenancy soak PASS bar (Gate-2 pre-condition)

Over a 24 h window overlapping a real bl286 OCR batch on the shared GB10 pool:
**0 serving crashes / 0 KV-transfer failures / 0 unrecovered free-VRAM-headroom events.**
gpu_ocr two-phase begin/complete audit (bl298) must be on so a server-killing request self-identifies
rather than self-erasing. Any one of the three > 0 → soak FAIL → Gate 2 FAIL.

## 6. §A4 — "when subjects fail identically, suspect the ruler"

Any correction to a task, rubric, or bar after this file is binding must be: provably spec-wrong (not
just inconvenient), applied to **every** subject, **dropped-not-inverted**, and disclosed in the
scoring writeup. A correction that helps one subject and not others is presumed a ruler error.

---

## 7. Lock status (pxx review PASS, coord seq-17)
- **LOCKED:** Gate-1 governance bar (reuses the live boundary gate); the run≠score split; hidden-test
  location + fresh-workspace rule; **each gate's negative control enumerated** (Gate-1 off-fabric DENY
  OBSERVED; Gate-2 run_shell-in-auto DENY + malformed-schema FAIL; Gate-3 poisoned/leaked item MUST
  fail); **Gate-2 N = 500** + **statistic = point ≥99% AND bootstrap 95% lower bound ≥98%**; Gate-3
  (≥ baseline + leakage); Gate-4 ranked-only; E3 soak bar; §A4. pxx F2 confirmed N + statistic.
- **METHOD-LOCKED, value at WS0-D (still pre-registration, before WS1):** the **per-subject serving
  quant** — measured at the quant each subject actually serves at per the WS0-D pin manifest per lane
  (Q3 single-Spark; Q6/Q8 PP). The rule is locked now; the numeric value can't precede the WS0-D pins,
  and lands before WS1's first run. pxx Fixture 2 generates the hidden Gate-2 task set at that point.

**Sign-off chain:** psaios draft → **pxx review PASS (coord seq-17, 1 sharpening folded here)** →
Chris ack → binding. Full-text artifact attached to `coord/pxx-psaios` at seq-18 for pxx's record.
