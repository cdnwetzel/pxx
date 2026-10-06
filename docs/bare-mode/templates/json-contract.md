# Template: single JSON object against a caller schema

- **purpose:** produce exactly one JSON object conforming to a caller-supplied
  schema — decomposition, planning, structured answers — with no prose and no
  markdown fences.
- **output contract:** one JSON object, byte-parseable, validated by the
  caller against the schema below (shape shown; substitute your own).
- **validated against:** the ACP PLAN contract prompt this generalizes was
  measured on `Qwen3.8-27B-FP8` (local vLLM, temperature 1.0).
- **measured result:** source prompt — bare 11/12 conformant through pxx
  2.6.2 (2026-10-05); default payload 10/12 in the same arms.
- **status:** UNVALIDATED as generalized — the measured artifact is the
  specific ACP prompt, not this template. Measure before gating on it.

## Prompt shape

```text
You are the <ROLE> worker. <One-sentence task statement>. DO NOT call any
tools — no shell, no file reads. Tool use terminates the task.
Output EXACTLY ONE JSON object (no prose, no markdown fences):
<schema as a compact literal, e.g. {"items": [{"id": "ITEM-1", ...}]}>.
Rules: <numbered, testable constraints — counts, verbatim-quotation
requirements, exclusion rules, ordering rules>.
--- INPUT ---
<the complete, self-contained input>
--- END INPUT ---
```

```json
{
  "items": [
    {
      "id": "ITEM-1",
      "field": "<constrained value>",
      "deps": []
    }
  ]
}
```

## Why each element exists

- **"DO NOT call any tools"** — bare mode advertises none; stating it keeps
  the contract explicit and makes a tool attempt a visible violation.
- **"EXACTLY ONE JSON object (no prose, no markdown fences)"** — the caller's
  extractor is strict; fences or commentary fail closed.
- **Schema as an inline literal** — the model copies shapes better than it
  follows descriptions.
- **Numbered, testable rules** — every rule should map to a check the caller
  actually runs; unenforced rules get dropped by the model under variance.
- **Delimited, complete input** — bare mode injects nothing; anything the
  model needs must be inside the markers.
