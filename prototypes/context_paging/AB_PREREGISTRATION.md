# Pre-registration — Context Paging v0 hardware A/B/C (8 GB, on-device)

Written **before** running, so the result can't be reverse-justified. This measures the **test
bed**, not the mechanism (the mechanism is hardware-independent and already proven by the offline
suite). Per pxx's value order, speed ranks **last** and is not a gate — this is a secondary
performance/capacity receipt plus a build-in-public data point.

## Candidates (all 8 GB unified memory, same task, same model)

| | Box | SoC | Bandwidth | Cooling |
|---|---|---|---|---|
| **A** | 2020 MacBook Pro | **M1** (Mac-class, 2020) | ~68 GB/s | active (fan) |
| **B** | "Neo" MacBook | **A18 Pro** (2024/26) | ~60 GB/s | likely fanless |
| **C** | iPhone 16 Pro Max | **A18 Pro** (same as B) | ~60 GB/s | fanless (phone) |

**Design:** B and C are the **same silicon** in different chassis → *B vs C isolates
cooling/chassis*. A vs {B,C} → *isolates chip generation*. This is the whole reason C is worth
adding: it turns a two-box drag race into a controlled experiment.

## Hypothesis (pre-registered)

> On this workload, the **2020 M1 (A)** will **match or beat** the A18 Pro boxes (B, C) on
> **sustained wall-clock-to-receipt**, because local-LLM work here loads the chip continuously and
> the fanless A18 Pro throttles (observed ~37→22 tok/s on a 3B). The A18 Pro may win **burst
> prefill (TTFT)** on early actions. Among the A18 Pro pair, **C (iPhone, larger thermal mass than
> a thin fanless laptop) may sustain as well as or better than B**. Net thesis: *newer silicon ≠
> faster for sustained memory-bound local inference on 8 GB; cooling + swap dominate.*

Falsifier: if B/C beat A on sustained wall-clock with no swap and no throttle knee, the thesis is
wrong and we say so.

## Why this workload is unusual (and what it really probes)

Context paging **re-prefills a fresh ~5,500-token capsule every action** but generates a **tiny**
typed action (~50–150 tokens). It is **prefill-dominated** (big-in/tiny-out), the inverse of a
chatbot. So this A/B/C is really a **repeated-prefill throughput probe** — and if re-prefilling
every action is the cost driver, that motivates a **v1 KV-reuse optimization** (share the cache
across capsules with a common prefix). The A/B/C is thus roadmap signal, not just a ranking.

## Controlled variables (pin ALL of these)

- **Same model + quant + tokenizer:** `qwen3:4b` at the **same GGUF quant** on every candidate,
  `--hf-tokenizer Qwen/Qwen3-4B`. **This is the make-or-break control for C** — if the iPhone can
  only serve a different model, C is a model+hardware comparison and must be labeled as such.
- **Same inference engine** where possible (llama.cpp on all three; note if C's app differs — that
  is a secondary confound between B and C).
- **Same host:** run the pxx Python host on **one fixed control Mac**; only the `--base-url` moves
  between A / B / C. Host overhead is then constant, so any delta is inference. (`--transport lan`
  for remote endpoints incl. the phone; `--transport local` if co-located.)
- **Same run params:** identical `--cap`, `--max-actions`, task, and `--stream` on every run.
- **Same thermal + power state:** plugged into wall power (not battery), thermally settled before
  the first run, ambient temperature logged, identical between candidates.

## Procedure

1. **Negative control first (proves the harness, not the hardware):** run
   `tests/test_context_paging.py` on the control host — it must pass identically regardless of which
   endpoint is used. A green offline suite means any A/B/C delta is hardware, not code.
2. **N ≥ 3 runs per candidate**, back to back, after thermal settling. Record every `receipt.json`.
3. Keep the phone (C) **foreground + screen-on + plugged in** for the whole run.

## Metrics (report all; the first two decide it)

1. **Swap delta (`performance.swap_used_delta_mb`)** — the true 8 GB wall. If a box swaps, that
   dominates; report it first. (Host-side on Macs; for the phone read memory on-device — Xcode
   Instruments / the app's memory readout — and note it.)
2. **Sustained wall-clock-to-receipt (`performance.wall_clock_s`)** — median over N runs.
3. **TTFT median (`performance.ttft_median_s`, `--stream`)** — prefill proxy; separates the
   burst-prefill story from sustained.
4. Prompt/decode throughput (`prompt_tokens_per_s`, `completion_tokens_per_s`), model-time,
   per-call latency median/p90.
5. **Throttle knee:** does per-action latency *rise* across the run? Plot latency vs action seq —
   a rising curve on B/C but flat on A is the thermal signature.

Report **median + spread** (not a single run). A candidate that BLOCKs (a 4B may not one-shot the
protocol) still yields an honest receipt — record it; do not cherry-pick a COMPLETED run.

## What a clean result looks like

A short table per candidate: `{swap_delta_mb, wall_clock_s (median±), ttft_median_s, prompt_tok/s,
completed?, throttle_knee?}`, plus the raw `receipt.json`s committed under `receipts/`, and a
one-paragraph verdict that either confirms or falsifies the pre-registered hypothesis. Then the
RECEIPTS.md R-line.

## Threats to validity (name them, don't hide them)

- **Model parity (C):** the biggest risk — see controls. If unmet, C is labeled model+hardware.
- **Engine parity (B vs C):** different llama.cpp builds/Metal kernels; note it.
- **LAN transport:** adds ~ms per call, negligible vs multi-second prefill, but report `transport`.
- **"Neo" chassis unknown:** if B has a fan, the B-vs-C cooling contrast weakens — record each box's
  cooling. The receipts + this doc make every one of these auditable.
