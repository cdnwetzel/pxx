# Reference fleet: the roster that drives pxx dogfooding

This is the model roster the author runs pxx against, settled by A/B
side-by-sides and live measurement (2026-08). It is documented here as a
*reference deployment* — pxx itself only needs one OpenAI-compatible or
Ollama endpoint (see `docs/CONFIG.md`); this shows one working shape of the
full multi-role loop.

Host identities are placeholders by policy (see `docs/TRUST_BOUNDARY.md`);
the mapping to real machines lives outside the repo.

## Role → model → endpoint

| Role | Model | Quant / dtype | On disk | Endpoint |
|---|---|---|---|---|
| Writer / Coder | qwen3.8-27b (Qwen3.8-27B-FP8, 27B dense hybrid) | FP8 e4m3 (Marlin, sm_86) | 29 GB | `<writer-host>:8007` — vLLM 0.27.1 |
| Planner | Writer self-plan (same endpoint) | — | — | `<writer-host>:8007` |
| Reviewer | gpt-oss:20b (20.9B) | MXFP4 | 13 GB | `<review-host>:11434` — Ollama |
| Verifier | executed gates — pytest + `ui_acceptance.py` (no model) | — | — | runs on the loop host |
| Operator / VisionJudge | qwen2.5vl:3b (3.8B) | Q4_K_M | 3.2 GB | `<vision-host>:11434` — Ollama |
| Generalist / Scribe | qwen2.5:14b-instruct (14.8B) | Q4_K_M | 9.0 GB | `<scribe-host>:11434` — Ollama |
| RAG LLM (portfolio site) | same instance as Writer | FP8 | — | via router `:8004` |
| Embedder | BAAI/bge-base-en-v1.5 (110M, fp32, CPU) | — | — | `<writer-host>` loopback `:8005` |
| Vector store | Qdrant | — | — | `<writer-host>:6333` |
| Router | labrouter → default_backend qwen3.8-27b | — | — | `<writer-host>` loopback `:8004` |

Coder escalation lanes (one Ollama endpoint on `<review-host>`):

| Lane | Model | Params | Quant | Size |
|---|---|---|---|---|
| small | qwen2.5:14b | 14.8B | Q4_K_M | 9.0 GB |
| standard | gpt-oss:20b | 20.9B | MXFP4 | 13 GB |
| deep | qwen2.5-coder:32b | 32.8B | Q4_K_M | 19 GB |

## Why this shape

- **Reviewer is a different model family than the Writer.** A same-family
  reviewer measurably over-flags its own family's diffs (a Qwen reviewer on
  Qwen diffs regressed the live eval 13/15 → 4/15 with hallucinated P0s,
  2026-07-17). Cross-family review decorrelates that failure.
- **The Verifier is not a model.** Enforcement stays with deterministic,
  executed gates (tests, lint, scope, regression); model review is advisory.
  A small vision model judges GUI acceptance, but the gate that blocks is
  still an executed script.
- **One vLLM instance serves Writer, Planner, and RAG.** Continuous batching
  absorbs the mixed traffic; FP8 puts a 27B on paired 20 GB Ampere cards
  (Marlin kernels — Ampere has no native FP8) with headroom for a 32k
  context at `--max-num-seqs 4`.
- **Escalation lanes share one endpoint.** Ollama keeps one lane resident at
  a time; escalation to `deep` pays a model swap, which is acceptable for a
  rare lane and keeps the box simple.
