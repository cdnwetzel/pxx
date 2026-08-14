# Context Paging Runtime — v0 prototype

Virtual memory for small-context models. A 4B model with an 8K window completes a real repo
task by assembling a fresh, hard-capped **capsule** per action — no transcript replay — with
**host-run verification**, and earns a receipt on an 8 GB MacBook (Neo).

Reference: Camelid's Context Paging Runtime (timtoole02, 2026-08). This is the pxx-native,
**receipted** evaluation of that idea. Design contract: [`docs/context-paging-prototype.md`](../../docs/context-paging-prototype.md).

> **Not shipped.** This package lives outside the packaged `pxx` namespace on purpose — it is a
> v0 prototype and the *build-native vs. compose-on-Camelid* integration decision is deliberately
> deferred. It is not part of the pxx wheel (`[tool.setuptools] packages` in `pyproject.toml`).

## What v0 proves

The mechanism, not the model: that a bounded capsule + typed actions + a host verifier the model
cannot fake is enough to drive a real edit to a **host-verified** COMPLETE — and that it **fails
honestly** on the bad cases. v0 is **file-level pages only**; symbol cards + a repo map are v1.

## Module map

| File | Responsibility |
|------|----------------|
| `pages.py` | Source pages — `sha-256` over raw bytes, canonical + symlink-resolved path (the one hashing authority); atomic writes. |
| `ledger.py` | Task ledger — durable task state + monotonic revision; atomic JSON. The task lives here, not in a transcript. |
| `artifacts.py` | Full logs on disk; a bounded, **secret-scrubbed** summary + ref to the model. |
| `capsule.py` | Capsule builder — hard input-token cap; evict `history → dependency pages` (oldest first); **never** the target source; floor-too-big → `CapsuleOverflow`. |
| `actions.py` | Typed actions (`NEED_CONTEXT` / `PATCH` / `RUN_TEST` / `SEARCH` / `INSPECT` / `COMPLETE` / `BLOCKED`); shape-validated. |
| `executor.py` | Crash-safe executor + verifier — in-flight idempotency record, exact-match `PATCH` under an `expected_sha` guard, atomic commit, startup **reconcile** (applied / discard / **ambiguous → fail-closed**). `RUN_TEST` runs the ledger command only, under a host timeout. |
| `runtime.py` | The loop: build capsule → model → one typed action → host executes/verifies → update ledger → repeat until COMPLETE / BLOCKED / budget. |
| `model.py` | `ScriptedModel` (offline, for the mechanism tests) + `OpenAICompatibleModel` (the live run). |
| `receipt.py` | The PASS-receipt schema, written to disk. |
| `run_neo.py` | The live driver — earns the 8 GB Neo receipt with a real 4B model. |

## The four negative controls (a check that cannot fail is not a check)

Proven deterministically, offline, in [`tests/test_context_paging.py`](../../tests/test_context_paging.py):

1. **stale-sha rejected** — a `PATCH` whose `expected_sha` no longer matches is refused (fresh
   source paged back), never applied blind.
2. **kill-restart resume, no replay, no double-apply** — a crash between the source write and the
   ledger bump is reconciled from the in-flight record; the run resumes from the **ledger alone**
   (the model script has *no* patch action) and the edit lands exactly once. Ambiguous state
   fails closed.
3. **BLOCKED is never COMPLETED** — an honest stop is recorded under its own terminal code; a
   premature `COMPLETE` without a passing host `RUN_TEST` is rejected.
4. **over-budget eviction never drops the target** — under cap pressure, dependency pages and
   history evict but the target source never does; if the floor alone exceeds the cap the host
   returns a preflight `BLOCKED(target_source_exceeds_capsule)` rather than evict/summarize it.

```
uv run --extra dev python -m pytest tests/test_context_paging.py -q
```

## Earn the live receipt (on Neo)

```
ollama serve &            # on the 8 GB MacBook
ollama pull qwen3:4b
python -m prototypes.context_paging.run_neo \
    --base-url http://localhost:11434/v1 --model qwen3:4b --cap 5500
# optional, for a real token count: --hf-tokenizer Qwen/Qwen3-4B  (needs `transformers`)
```

The run writes `state/receipt.json` (per-capsule token accounting, the action trace, the
negative-control flags observed, the host verification verdict, the terminal code). The
`tokenizer_id` field records whether a real or approximate tokenizer measured the cap — the
receipt never overstates its fidelity.

## Deferred: integration decision

Build as a native pxx context-assembly mode vs. compose on Camelid's runtime (they interoperate
today via `provider=openai-compatible`). v0 reuses pxx's spine conceptually — host-enforced
scope, terminal taxonomy, host-run verification (R-014), fresh-context-per-round, exact-match
edits — and adds only the paging layer. Revive the parked timtoole02 collaboration RFC rather
than duplicate a kindred project.
