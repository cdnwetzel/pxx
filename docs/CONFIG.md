# Configuration reference

Precedence (highest wins): CLI flags → `PXX_*` env vars → `./pxx.toml` or
`./.pxx/config.toml` → `~/.config/pxx/config.toml` → defaults. Additionally,
`~/.config/pxx/env` (KEY=VALUE lines) is loaded into the environment with
`setdefault` semantics. Unknown TOML keys raise `ConfigError`.

## Keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `model` | string | `qwen2.5-coder:7b` | model id |
| `provider` | string | `ollama` | `ollama` / `openai` / `vllm` / `openai-compatible` |
| `base_url` | string | per provider | endpoint base URL |
| `api_key` | string | — | bearer token for OpenAI-compatible endpoints |
| `permission` | string | `ask` | `ask` / `plan` / `edit` / `auto` |
| `scope` | list[str] | `[]` (repo root) | root-relative writable prefixes |
| `trusted_paths` | list[str] | `[]` | extra absolute roots allowed in scope |
| `memory_enabled` | bool | `true` | persistent memory |
| `memory_dir` | path | `~/.pxx` | memory db + archives |
| `state_dir` | path | `$XDG_STATE_HOME/pxx` | audit logs |
| `test_command` | string | — | used by `pxx loop` |
| `sandbox_shell` | bool | `false` | wrap `run_shell` in sandbox-exec/bubblewrap |
| `safety_net` | bool | `true` | stash + `pxx-pre/<ts>` tag on edit-capable session starts (git repos) |

**Tool calling.** The native backend (and therefore every `pxx loop` run, and
`pxx run` by default) needs an endpoint that accepts tool calls. Ollama
supports tool calling out of the box. A vLLM server must be launched with
`--enable-auto-tool-choice --tool-call-parser <parser>`; without those flags
every native round fails with HTTP 400 (`"auto" tool choice requires …`).
`pxx doctor` probes the configured endpoints for this. The aider backend
(`ask`/`edit`, or `run --backend aider`) does not need endpoint tool calling.

## `[budgets]`

`max_rounds` (25), `max_tokens` (200000), `max_cost_usd` (5.0),
`max_wall_seconds` (1800), `max_diff_lines` (400). Tripping any budget stops
the run with `BUDGET_EXCEEDED`.

## `[[fallback_models]]`

Ordered fallback chain, each entry: `model` (required), `provider`,
`base_url`, `api_key`. On connection failure the native backend tries the
next entry; `pxx.router.resolve_model` picks the first reachable.

## `[roles.review]`

Optional per-role model overlay for the reviewer/judge. When absent the
reviewer runs on the coder `model` — a run is byte-identical to before this
key existed. When present it accepts the same fields as a model (`provider`,
`model`, `base_url`, `api_key`); unspecified fields inherit the coder model,
so a lone `base_url` reuses the same model on a different endpoint. This lets
the coder and the judge run on different hardware — e.g. a GPU-box coder and a
Mac judge:

```toml
model = "qwen3-coder:30b"
base_url = "http://gpu-box:11434"   # coder on the RTX box

[roles.review]
model = "qwen3.5:9b"
base_url = "http://localhost:11434" # judge on the Mac (e.g. via SSH tunnel)
```

Only `review` is recognised today (an unknown role name is a fail-closed
error). Precedence follows the normal layering; env `PXX_REVIEW_*` overlays
the TOML. Consumed by `pxx review`, `pxx calibrate`, and the opt-in
`pxx loop --review` gate (`--review-mode blocking|advisory`).

## `[[hooks]]`

Deterministic gates: `event` (`PreToolUse` / `PostToolUse`), `command`
(shell), `timeout` (10s), `matcher` (optional tool-name substring). The hook
receives JSON on stdin; exit 0 allows, anything else denies (fail-closed).

## `[[mcp_servers]]`

`name` + `command` (argv list). pxx spawns the server over stdio and mounts
its tools as `mcp__<name>__<tool>`.

## Environment variables

`PXX_MODEL`, `PXX_PROVIDER`, `PXX_BASE_URL`, `PXX_API_KEY`, `PXX_PERMISSION`,
`PXX_TEST_COMMAND`, `PXX_SANDBOX_SHELL`, `PXX_MEMORY_ENABLED`, `PXX_MEMORY_DIR`,
`PXX_SCOPE` (comma list), `PXX_SERVER_TOKEN` (auth for `pxx serve`).
Reviewer role overlay (see `[roles.review]`): `PXX_REVIEW_MODEL`,
`PXX_REVIEW_PROVIDER`, `PXX_REVIEW_BASE_URL`, `PXX_REVIEW_API_KEY`.
Legacy: `PXX_OLLAMA_BASE`, `PXX_OLLAMA_MODEL`.
