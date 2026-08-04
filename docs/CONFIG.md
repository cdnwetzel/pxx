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
| `allow_ungated_shell` | bool | `false` | explicitly permit `run_shell` with no hook/sandbox (see `[[hooks]]`) |
| `safety_net` | bool | `true` | stash + `pxx-pre/<ts>` tag on edit-capable session starts (git repos) |
| `loop_review` | bool | `false` | per-box default for the `pxx loop` model-backed review gate (see below) |

**Tool calling.** The native backend (and therefore every `pxx loop` run, and
`pxx run` by default) needs an endpoint that accepts tool calls. Ollama
supports tool calling out of the box. A vLLM server must be launched with
`--enable-auto-tool-choice --tool-call-parser <parser>`; without those flags
every native round fails with HTTP 400 (`"auto" tool choice requires …`).
`pxx doctor` probes the configured endpoints for this — under the real agent
system prompt, not a toy call — and warns (F2) when a model accepts `tools` but
answers in prose, which strands `pxx loop`. Some small instruct models pass a
one-line probe yet degrade under a real loop context on constrained hardware, so
the probe requires an actual tool call and runs for ollama too. The aider
backend (`ask`/`edit`, or `run --backend aider`) does not need endpoint tool
calling.

**`pxx loop` review gate.** The model-backed review gate is opt-in per run via
`--review` (and `--review-mode blocking|advisory`). A box that always wants it
can flip the default with `loop_review = true` (or `PXX_LOOP_REVIEW=1`); the
shipped default stays off. Precedence is the usual layering — an explicit
`--review` / `--no-review` on the command always wins over `loop_review`, so
`--no-review` turns the gate off for a single run even when the setting is on.

## `[budgets]`

`max_rounds` (25), `max_tokens` (200000), `max_cost_usd` (5.0),
`max_wall_seconds` (1800), `max_diff_lines` (400). Tripping any budget stops
the run with `BUDGET_EXCEEDED`.

**Provider-aware token ceiling.** On a local provider (`ollama`/`vllm`) a token
has no marginal cost, so the default `max_tokens` ceiling is pure friction — a
real task on a small box can legitimately burn 200k tokens. When the coder runs
on a local provider **and** `max_tokens` is still at its `200000` default, pxx
lifts it to a high finite backstop so local runs aren't cut short mid-work.
Runaways stay bounded by `max_rounds` and `max_wall_seconds`, and paid spend by
`max_cost_usd` — none of which are touched. To opt out, set `max_tokens` to any
value **other than** `200000` and you get exactly the cap you name (the lift is
detected by comparison to the default, so an explicit `200000` reads as
unchanged and is still lifted — pick `199999`/`200001` for a hard local cap
there). Paid providers (`openai`, `openai-compatible`) always keep the
configured cap.

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
the TOML, and the overlay is resolved against the final coder model (a later
`PXX_MODEL`/`PXX_API_KEY` still reaches the reviewer). Consumed by `pxx review`,
`pxx calibrate`, and the opt-in `pxx loop --review` gate (`--review-mode
blocking|advisory`).

**Trust boundary.** Reviewer routing is a data-egress surface — the diff (and
any bearer token) is sent to `base_url`. Like `[[hooks]]` and `[[mcp_servers]]`,
`[roles.review]` is honoured **only from user config, env, or CLI**, never from
a repo-local `pxx.toml` / `.pxx/config.toml` (a checked-in file trying to set it
is ignored with a warning).

## Portable / single-box degrade

pxx runs on one box by default: set only `model` (or `PXX_MODEL`) and the
reviewer inherits it — no second endpoint, no `--review` needed. An 8GB laptop
with a small local coder (e.g. `qwen2.5-coder:3b` on Ollama) is a complete
setup, and `pxx improve` runs its cycle offline (deterministic, no model).

For a machine that is sometimes docked to a GPU box and sometimes portable, make
the on-device model a **fallback** so a single config degrades automatically.
pxx is local-first: it probes `model` then each `[[fallback_models]]` entry at
session start and uses the first reachable — an unreachable endpoint is data,
not an error, so there is nothing to switch by hand.

```toml
# primary: the GPU box (reachable only when docked / tunnelled)
model = "Qwen3-Coder"
provider = "vllm"
base_url = "http://127.0.0.1:8001"

# fallback: an on-device model, always reachable
[[fallback_models]]
provider = "ollama"
model = "qwen2.5-coder:3b"
base_url = "http://127.0.0.1:11434"
```

Docked → the GPU coder; on the road → the local one, same config. The reviewer
has no separate fallback chain: when portable, either drop `--review` (the coder
and your `test_command` still gate the run) or point `[roles.review]` at a local
endpoint.

When a `[[fallback_models]]` chain is set, the **auto** backend lane
(`ask`/`edit`/`plan`/`chat`) prefers the native backend — the aider backend does
not consult the chain, so picking it would silently void the degrade config.
`run`/`loop` are always native. To fix a backend for a box regardless, set the
`backend` key (`native` | `aider` | `auto`) or `PXX_BACKEND`; an explicit
`--backend` flag still wins.

## `[[hooks]]`

Deterministic gates: `event` (`PreToolUse` / `PostToolUse`), `command`
(shell), `timeout` (10s), `matcher` (optional tool-name substring). The hook
receives JSON on stdin; exit 0 allows, anything else denies (fail-closed).

**Payload.** pxx writes one JSON object to the hook's stdin —
`{"tool": "<name>", "args": {...}}` for `PreToolUse` (`PostToolUse` adds
`"result_preview"`). The hook is spawned with pxx's working directory (**the
project root**) and environment. The verdict is the exit code: `0` allow,
non-zero deny (fail-closed — a timeout or crash denies).

**Path contract — read this before writing a scope/boundary hook.** The
filesystem tools (`read_file` / `write_file` / `edit_file` / `list_files` /
`search_files`) put their target in `args["path"]` **relative to the project
root**, not as an absolute path. A hook that enforces a path boundary MUST:

1. **Resolve it itself, against a trusted root.** Anchor the relative path to the
   hook's own working directory (pxx sets it to the project root) to get an
   absolute path. Do *not* trust any pre-resolved path handed in by the run —
   the run is the thing being governed, so its guard must derive the target
   independently (no confused deputy). pxx sends the raw relative path on
   purpose, for exactly this reason.
2. **Canonicalize with `realpath`, never lexically first.** Follow symlinks
   (`os.path.realpath` / `Path.resolve()`) before the in/out-of-scope check, and
   do **not** `os.path.normpath` (or otherwise collapse `..`) beforehand: lexical
   `..` collapse happens *before* symlinks are followed, so `link/../secret` (an
   in-scope symlink `link` that points elsewhere, then `..`) becomes `secret` —
   the symlink is erased and the escape is masked (**fail-open**). Join with `..`
   intact and let a single `realpath` pass resolve symlinks and `..` together.
3. **Boundary-anchor the prefix check.** Compare with `target == root` or
   `target.startswith(root.rstrip("/") + "/")` so a sibling like `/repo-evil`
   does not match the scope `/repo`.

`run_shell`'s target is a command string, not a path — don't anchor it.

**`run_shell` is fail-closed in write-capable modes.** `scope` confines only the
**file** tools (it checks path targets); a shell command has no path target, so
`scope` does **not** restrict what `run_shell` can touch. Because of that,
`run_shell` in `edit` or `auto` mode (including unattended `pxx run`) requires an
explicit shell safeguard, or it is denied with `HOOKS_MISSING`. Provide exactly
one:

- a `PreToolUse` hook with `matcher = "run_shell"` (deterministic allow/deny per
  command), **or**
- `sandbox_shell = true` (contain it in sandbox-exec / bubblewrap), **or**
- `allow_ungated_shell = true` (or `PXX_ALLOW_UNGATED_SHELL=1`) to accept an
  unhooked, unsandboxed shell **explicitly** — off by default, since it lets a
  model-authored command run unconfined.

`ask`/`plan` never permit `run_shell` at all.

## `[[mcp_servers]]`

`name` + `command` (argv list). pxx spawns the server over stdio and mounts
its tools as `mcp__<name>__<tool>`.

## Environment variables

`PXX_MODEL`, `PXX_PROVIDER`, `PXX_BASE_URL`, `PXX_API_KEY`, `PXX_PERMISSION`,
`PXX_BACKEND` (`native`/`aider`/`auto`),
`PXX_TEST_COMMAND`, `PXX_SANDBOX_SHELL`, `PXX_ALLOW_UNGATED_SHELL`,
`PXX_LOOP_REVIEW` (default the `pxx loop`
review gate on for this box), `PXX_MEMORY_ENABLED`, `PXX_MEMORY_DIR`,
`PXX_SCOPE` (comma list), `PXX_SERVER_TOKEN` (auth for `pxx serve`).
Reviewer role overlay (see `[roles.review]`): `PXX_REVIEW_MODEL`,
`PXX_REVIEW_PROVIDER`, `PXX_REVIEW_BASE_URL`, `PXX_REVIEW_API_KEY`.
Legacy: `PXX_OLLAMA_BASE`, `PXX_OLLAMA_MODEL`.
