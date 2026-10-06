# Bare mode (`bare = true`)

Payload-only reduction of the native backend's chat-completions request for
single-shot, no-tool, contract-gated callers.

```toml
# pxx.toml (or any config layer — bare is not an exec surface)
bare = true
```

With `bare = true`, an `pxx ask` / `pxx plan` request carries exactly one
message — the task itself as a single `user` message. There is no system
message and no `tools` key, and `ToolRegistry.specs()` is never called, so no
tool schemas (built-in or MCP) are even constructed. Everything else is
unchanged: clarity, scope, permissions, hooks, budgets, routing, memory
policy, and audit all behave exactly as in default mode. Bare is valid only
for the read-only modes ASK and PLAN; any other mode fails with `ConfigError`
at `Session.run` entry, before the first model request. Default OFF, strict
boolean, config-only (no env var, no CLI flag).

Every `prompt_rendered` and `model_request` audit event carries body-free
payload receipt fields — `payload_mode` (`"bare"`/`"default"`),
`system_message_present`, `tools_key_present`, `tool_count` — so a caller can
reconcile per invocation that the payload actually sent was the payload
intended, without the audit stream carrying prompt content.

## When to use it

Use bare mode when the caller:

- issues a single-shot request (no agentic loop, no tool use),
- supplies its own output contract and its own fail-closed validation, and
- treats pxx as a governed transport, not as the prompt author.

Do not use bare mode for `pxx run` / `pxx loop` / `pxx chat` / `pxx edit` —
those modes are tool-mediated by design, and bare rejects them.

## Measured evidence

Two measurement windows, same model (`Qwen3.8-27B-FP8` on a local vLLM
server, temperature 1.0), same task (the ACP PLAN contract prompt, sha256
`4b504957…`). The caller's production contract path (strict JSON extraction +
schema validation + a mechanical 1:1 task/criterion check) scored every
sample; malformed outputs were caught fail-closed by that path, not by hand.

**2026-10-04 — diagnosis (pre-bare, direct HTTP probes):**

| Arm | Conformant |
|---|---|
| bare-shaped prompt (single user message) | 12/12 |
| pxx system-prompt path | 2/6 (primary isolation table); 7/10 under corrected rescoring |

**2026-10-05 — isolation arms through pxx 2.6.2 (12 samples per arm, interleaved):**

| Arm | Conformant | Defects |
|---|---|---|
| bare | 11/12 | one malformed JSON (output stopped one closing brace early; `finish_reason=stop`, not truncation) |
| default | 10/12 | one malformed JSON (same class), one invented/missing criterion |
| receipt cross-check | 24/24 | every audit receipt matched the intended payload mode |

**Caveats that bound the claim:**

- The same vLLM server process served both windows — no restart, no config
  change. The default path's recovery from 2/6 (10-04) to 10/12 (10-05) is
  therefore unexplained except by small-sample variance at temperature 1.0.
- 12 samples per arm is too small to establish causality or statistically
  significant superiority.

**The claim we stand behind:**

> Bare mode is live-verified and suitable for contract-gated callers. The
> measurements support the correction operationally but do not prove that
> scaffolding was the sole cause of the earlier contract defects.

**Independent merits** (not sample-dependent):

- **Payload integrity** — the request contains exactly what the caller meant
  to send; nothing is injected between the contract and the model.
- **Size** — for this prompt class, ~85 prompt tokens of task versus ~2000
  with the agent system prompt and 9 tool schemas attached.
- **Receipts** — body-free per-request payload metadata makes "what was
  actually sent" an auditable fact rather than an assumption.

## Prompt templates

Starter templates for common bare-mode call shapes, under `templates/`:

| Template | Shape | Status |
|---|---|---|
| `templates/json-contract.md` | single JSON object against a caller schema | source prompt measured (bare 11/12, 2026-10-05); generalized form UNVALIDATED |
| `templates/bounded-codegen.md` | one bounded source file, no prose | UNVALIDATED |
| `templates/extraction.md` | field extraction / classification to JSON | UNVALIDATED |

UNVALIDATED means: the shape is plausible and follows the measured pattern,
but no measured conformance run exists for that template yet. Each template
carries its own header with the model, endpoint class, and temperature any
measurement was taken against. Treat templates as starting points — measure
against your own model and contract before relying on one in a gated pipeline.
