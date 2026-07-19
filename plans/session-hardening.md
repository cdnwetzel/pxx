# vllm-host-1 session hardening — model config, headless robustness, read-only integrity
> Backlog ID: 007

> Status: done — all five items landed 2026-07-15 (commits 6b327c5, e920bf1,
> 9364d10, acb4ccd, + item 5); acceptance verified live: no litellm metadata
> warning, no .gitignore mutation, clean headless one-shot without --yes.
> Source: 2026-07-15 vllm-host-1 smoke test + `--self-improve` dogfood session
> (first real session through the new multi-candidate vLLM chain, commit
> 6bb7eed). Every item below was observed live, not speculated.

## Context

pxx now routes to vllm-host-1's vLLM (`Qwen3-Coder`, 30B-A3B FP8, 32k ctx) as the
priority endpoint. The first end-to-end session worked, but surfaced four
gaps. Recommended implementation order below is by risk-adjusted value:
items 1–2 are trivial pure-code changes with immediate protection, item 3 is
a small design decision, item 4 is the biggest quality win but touches
guardrailed files so it needs the user in the loop, and item 5 is optional
polish. Items are independent — nothing blocks anything — so each lands as
its own commit with its own tests.

## 1. Warn on PXX_VLLM_URL / PXX_VLLM_MODEL length mismatch

**Observed:** a URL with no positionally-paired model silently falls back to
`VLLM_DEFAULT`. With two candidates serving different model ids
(vllm-host-1 `Qwen3-Coder` vs gpu-node-1 `qwen2.5-coder-14b-coder-lora`), a forgotten
model entry routes requests to a server with the *wrong model id* — vLLM
rejects unknown ids, so the session dies mid-flight instead of at launch.
(Also the top suggestion from the `--self-improve` dogfood review.)

**Change:** in `pxx/endpoints.py::_vllm_candidates()`, when
`PXX_VLLM_MODEL` is set and its non-empty entry count differs from the URL
count, print a one-line `pxx:` warning to stderr naming both counts and
which URLs got the default. Keep the existing fallback behavior — warn,
don't refuse (a single shared model across URLs is a legitimate config).

**Tests:** capsys assertion on the warning for 2-URLs/1-model; no warning
for matched lists or unset `PXX_VLLM_MODEL` (the single-URL default path
must stay silent).

**Effort:** ~10 lines + 2 tests. **Risk:** none (warning only).

## 2. Pass `--no-gitignore` to aider

**Observed:** an ask-mode (read-only) session launched with `--yes` silently
appended `.aider*` to `.gitignore` — a working-tree mutation from a session
whose banner promised "read-only". Contradicts pxx's reviewer-first
contract, and multiple concurrent review agents could trip over the dirty
file.

**Change:** add `--no-gitignore` to the fixed args in
`pxx/cli.py::_build_aider_args()` — unconditionally, both modes. Rationale:
`.gitignore` hygiene is a repo decision, not a per-session one; if `.aider*`
belongs in an ignore file, commit it once by hand. Users can still override
per-session because user args are appended after the fixed args.

**Tests:** extend the existing `_build_aider_args` tests to assert
`--no-gitignore` is present in ask and edit mode.

**Effort:** 1 line + test edits. **Risk:** none.

## 3. Headless (non-TTY) hardening

**Observed:** `pxx --self-improve --message "..."` *without* `--yes` crashed
with a raw prompt_toolkit `OSError: [Errno 22]` traceback when aider raised
an interactive confirm on a non-TTY stdin. Scripted one-shot runs — the
exact shape `--loop`, cron jobs, and CI smoke tests use — currently work
only if the caller remembers `--yes`.

**Change:** in `pxx/cli.py::main()`, just before the exec: if
`not sys.stdin.isatty()` and the user args contain none of `--yes`,
`--yes-always`, or `--no` — append `--yes` and print a one-line
`pxx: non-TTY stdin — passing --yes to aider` notice to stderr.

**Decision made (alternative rejected):** hard-failing with a clear error
was considered, but pxx's own self-modes are the primary headless callers
and they always want `--yes` semantics; failing would just push boilerplate
onto every script. The stderr notice keeps it observable.

**Safety note:** `--yes` in *edit* mode auto-approves aider actions. That is
acceptable because headless edit sessions already require the #002 safety
tag and `--scope` gates; document the interaction in the "Modes" section of
CLAUDE.md as part of this item.

**Tests:** unit-test the arg-injection helper (extract it as a pure function
taking `(isatty: bool, user_args: list[str])` so no TTY mocking is needed):
injects on non-TTY without consent flags; no-ops on TTY, and on non-TTY when
`--yes`/`--yes-always`/`--no` already present.

**Effort:** ~15 lines + 3 tests + CLAUDE.md paragraph. **Risk:** low.

## 4. Register `openai/Qwen3-Coder` in the model config files

**Observed:** aider warns `Unknown context window size and costs, using sane
defaults` — litellm has no metadata for `openai/Qwen3-Coder`, so aider
guesses at the context budget instead of using the server's actual 32,768
(`max_model_len` confirmed via `/v1/models` on vllm-host-1). Repo-map sizing and
history truncation are running blind on the fleet's *primary* endpoint.

**Change — GUARDRAILED, user executes or explicitly approves each edit**
(per CONVENTIONS.md both files are refuse-and-ask):

- `config/model-metadata.json` — add key `"openai/Qwen3-Coder"` mirroring
  the existing `qwen2.5-coder-14b` entry's shape:
  `max_input_tokens: 28672`, `max_tokens/max_output_tokens: 4096` (28k + 4k
  fits the 32k window with headroom), zero costs,
  `litellm_provider: "openai"`, `mode: "chat"`.
- `config/model-settings.yml` — add `- name: openai/Qwen3-Coder` with
  `edit_format: diff` (the A/B outputs showed clean, instruction-following
  diffs; no reason to deviate from the repo's standard). No `num_ctx` —
  that knob is Ollama-only per the file's own header comment.

**Verification:** relaunch `pxx --message "..."` and confirm the litellm
warning is gone and the banner/aider header reports the 28k context; run
one real `--edit --scope pxx/` session to confirm diff edit format applies
cleanly.

**Effort:** two small config stanzas. **Risk:** low-medium (wrong values
degrade sessions — hence the guardrail; values above are derived from the
live server, not guessed).

## 5. (Optional) Debug logging on endpoint probe failures

**Observed:** when all endpoints are down, `detect_endpoint()` raises one
generic RuntimeError; which candidates were tried and why each failed is
invisible. (Second suggestion from the dogfood review.)

**Change:** emit a per-candidate `pxx: probe failed <name> <url>` line to
stderr behind a `PXX_DEBUG=1` env check — pxx has no logging framework and
should not grow one for this. Include the tried-candidate list in the
RuntimeError message.

**Effort:** ~8 lines. **Risk:** none. Do last; skip if it feels like noise.

## Explicitly not planned

- Extracting `_vllm_candidates()` validation into a helper (dogfood
  suggestion 3): the function is ~20 lines; a split adds indirection
  without testability gains — the list tests in `test_endpoints.py` already
  cover it.
- Rebinding the Studio's Ollama to `0.0.0.0`: deliberate localhost-only
  posture per the Studio's migration runbook; out of scope here.

## Acceptance

Plan closes when items 1–4 are landed (5 optional), each with tests green
(`uv run pytest -q`) and scoped ruff clean, and a follow-up smoke session
against vllm-host-1 shows: no litellm metadata warning, no `.gitignore`
mutation, and a clean headless one-shot without `--yes` supplied manually.
