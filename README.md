# pxx 2.0 — local-first AI coding agent runtime

`pxx` is an async, event-sourced coding-agent runtime that runs against **your own
inference** (Ollama, vLLM, or any OpenAI-compatible endpoint) — no cloud dependency,
no telemetry, no API keys required. It pairs a native tool-calling agent loop with
persistent cross-session memory, deterministic safety gates, and MCP interop, and
can delegate to [aider](https://github.com/Aider-AI/aider) as an optional edit engine.

> pxx 2.0 is a ground-up rewrite of pxx 1.x. The 1.x control-plane semantics
> (fail-closed gates, scope limits, bounded loops, audit) are preserved; the
> execution layer (`os.execv` into aider, argv scanning, sidecar services) is
> replaced by an async runtime where **pxx owns the agent loop**. See
> [DESIGN.md](DESIGN.md) for the architecture contract and
> [docs/MIGRATION.md](docs/MIGRATION.md) for 1.x → 2.0 changes.

## Why pxx

- **Offline-capable**: all inference stays on your machine or your LAN.
- **pxx owns the runtime**: every model/tool event flows through pxx's event bus.
  Backends cannot bypass policy — scope, permissions, budgets, and hooks are
  enforced by the host, never by the model.
- **Persistent memory**: observations from previous sessions (files changed, tool
  outcomes, your own `remember` notes) are stored in a local SQLite database with
  hybrid BM25 + vector search and injected deterministically at session start.
- **Fail-closed safety**: read-only by default. Writes require `edit`/`run` mode,
  stay inside a canonicalized scope (symlinks resolved), shell commands are gated,
  and every run ends with a machine-readable terminal code in a hash-chained audit log.
- **Interop**: consumes MCP servers as tools, and exposes its own memory as an MCP
  server for other agents (Claude Code, goose, opencode, …).

## Install

```sh
pip install pxx-orchestrator          # the command is `pxx`
# optional extras:
pip install "pxx-orchestrator[aider]"   # aider delegation backend (Python < 3.13)
pip install "pxx-orchestrator[server]"  # headless HTTP API (pxx serve)
```

Prerequisites: Python 3.11+, and a reachable model endpoint —
[Ollama](https://ollama.com) by default (`ollama pull qwen2.5-coder:7b`).

## Backends

`pxx` runs tasks on one of two execution backends, selected per run:

- **native** (the tool-calling agent loop this README describes): `pxx` drives
  the model directly through its own tool surface and gates.
- **aider** (delegation): edits are handed to [aider](https://github.com/Aider-AI/aider)
  as the edit engine, still under pxx's scope/budget/hook gates.

Default selection: **aider when the `aider` binary is on `PATH`, else native**
(`run`/`loop` always default to native). Force one with
`--backend native|aider` on any run verb.

**Tool calling.** The native backend needs an endpoint that accepts tool calls
(`tools` in the chat-completions request). Ollama supports tool calling out of
the box. vLLM must be launched with `--enable-auto-tool-choice
--tool-call-parser <parser>` — without those flags every native round fails
with HTTP 400 (`"auto" tool choice requires …`). `pxx doctor` probes for this.
`ask`/`edit` (and `run` with `--backend aider`) can sidestep via the aider
backend; `pxx loop` is native-only and cannot.

## Quick start

```sh
pxx doctor                          # check your setup

pxx ask -m "Explain main.py"        # read-only (default): no writes, no shell
pxx edit -m "Add error handling to main.py"   # writes allowed, in scope
pxx run  -m "Add tests for utils.py"          # unattended, budget-capped
pxx loop -m "Fix the failing tests" --scope src  # bounded edit→test→review loop
pxx chat                            # interactive session
```

Permission modes: **ask** (read-only) → **plan** (plan only) → **edit** (writes in
scope, shell via hooks) → **auto** (unattended, budgets enforced). Every run is
bounded: max rounds/tokens/cost/wall-clock/diff-lines, all configurable.

## Memory

```sh
pxx memory add "we use ruff, not black" --tags conventions
pxx memory search "linting"
pxx memory list
```

Memory is hybrid-retrieved (FTS5 BM25 0.4 + embedding cosine 0.6). Embeddings come
from a local Ollama model when reachable, else a deterministic hash embedder —
search always works offline. TTL'd observations archive to JSONL monthly.
Memory is **context, never policy**.

Memory is **project-scoped by working directory**: `search`/`list` see only the
current directory's project (its directory name) — run them from the directory
the memory was added in. Keyword search matches whole tokens exactly (no
stemming): searching `round` will not match `rounding`.

Expose it to other agents over MCP:

```sh
pxx mcp            # stdio MCP server: memory_search / memory_add / memory_list
```

## Configuration

Layered, highest precedence wins: CLI flags → `PXX_*` env → `./pxx.toml` (or
`.pxx/config.toml`) → `~/.config/pxx/config.toml` → defaults. Unknown keys are
rejected (fail-closed, no silent typos). Example `pxx.toml`:

```toml
model = "qwen2.5-coder:14b"
provider = "ollama"
permission = "edit"
scope = ["src", "tests"]
test_command = "pytest -q"

[budgets]
max_rounds = 20
max_cost_usd = 2.0

[[fallback_models]]
model = "served-model"
provider = "vllm"
base_url = "http://gpu-box:8000"

[[hooks]]
event = "PreToolUse"
command = "/usr/local/bin/my-gate"   # exit 0 allow / 2 deny — deterministic

[[mcp_servers]]
name = "filesystem"
command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
```

1.x `PXX_OLLAMA_BASE` / `PXX_OLLAMA_MODEL` env vars and `~/.config/pxx/env`
still work.

## Headless API

```sh
pxx serve --port 8400     # FastAPI: POST /v1/sessions, SSE event stream,
                          # cancel, memory proxy. Loopback-only by default.
```

## Operator commands

Beyond the everyday verbs (`ask`/`edit`/`plan`/`run`/`loop`/`chat`):

**Safety & release**

```sh
pxx check [--all-files]   # secret/PII scan — staged files, or all tracked files
pxx upgrade               # upgrade the pxx install in place
pxx doctor                # diagnose setup (endpoints, backend, memory, config)
pxx audit verify <path>   # verify a hash-chained audit log
```

**Run evidence** (every run is recorded with an immutable agent manifest)

```sh
pxx runs list|show|export         # recorded runs, per-agent projections
pxx runs resume <run-id>          # resume a run from its checkpoint
pxx agents list|show              # agent versions + success rates (drift quarantine)
pxx verify [run-id]               # verification packet for a run (gates fired)
pxx metrics summary|failures|memory-impact|export|compare
```

**Evaluation & improvement** (the self-improvement platform)

```sh
pxx eval run|self-check|report [--partition held-out]
pxx calibrate                     # reviewer calibration (recall/fp/agreement)
pxx improve analyze|clusters|proposals|cycle|status|daemon|pause|resume
pxx improve evaluate-candidate <id>   # held-out, both arms
pxx improve readiness|auto-promote|principles
pxx propose                       # create a constrained improvement candidate
pxx compare <baseline> <candidate>    # promotion verdict (held-out, multi-metric)
pxx promote <candidate-id>        # human-gated promotion (needs a real scorecard)
pxx agent activate|rollback|history|channels|canary
pxx goal -m "<goal>"              # goal -> task DAG -> isolated per-node loops
```

**Legibility** (docs/workflow contracts)

```sh
pxx workflow validate             # validate this repo's WORKFLOW.md
pxx context audit                 # docs present + trust mirrors in sync
pxx docs check                    # every documented verb exists
```

Every verb self-describes: append `--help` (e.g. `pxx check --help`).

## Safety model (short version)

- Edit-capable sessions (`edit`/`run`/`loop`/`goal`, in a git repo) tie a safety net
  before anything can write: uncommitted work is stashed
  (`--include-untracked`, message carries the run id) and HEAD is tagged
  `pxx-pre/<ts>`. Restore with `git reset --hard <tag>` + `git stash pop` —
  pop is your move, never pxx's. Disable with `safety_net = false`.
- Paths are canonicalized with symlinks resolved before any gate decision —
  model output never defines the trust boundary.
- Hooks are deterministic gates (like Claude Code's PreToolUse): they cannot be
  overridden by model judgment.
- The audit log (`~/.local/state/pxx/audit/YYYY-MM-DD.jsonl`) is hash-chained and
  metadata-only — no prompts, file contents, or secrets. Verify with
  `pxx audit verify <path>`.
- Bounded loops stop on: round cap, diff cap, budget, scope violation,
  non-monotonic test progress (`NO_TEST_PROGRESS`), a detected oscillation
  (`LOOP_DETECTED`), or a blocking review verdict.

## Upgrading

With 2.0 on PyPI:

- **uv tool**: `uv tool upgrade pxx-orchestrator`
- **pipx**: `pipx upgrade pxx-orchestrator`
- **pip**: `pip install -U pxx-orchestrator`
- **from source**: `git pull && uv sync --extra dev --extra server`
- **in-place**: `pxx upgrade` — upgrades the pxx install (detects uv tool /
  pipx / pip automatically)

Settings, memory, and audit state carry forward — 2.0 migrates them on first
run (see [docs/MIGRATION.md](docs/MIGRATION.md)).

## Development

```sh
git clone https://github.com/cdnwetzel/pxx && cd pxx   # the 2.0 tree (branch v2)
uv sync --extra dev --extra server
uv run pytest          # 870+ tests, no network/Ollama/aider required
uv run ruff check
```

> 2.0 lives on [`cdnwetzel/pxx`](https://github.com/cdnwetzel/pxx) (this repo);
> the 1.x line continues on its `v1.x` branch. The public history is a
> curated series — the full development history stays private.

## License

MIT — see [LICENSE](LICENSE).
