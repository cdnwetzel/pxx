# pxx 2.0 — local-first AI coding agent runtime

[![CI](https://github.com/cdnwetzel/pxx/actions/workflows/ci.yml/badge.svg?branch=v2)](https://github.com/cdnwetzel/pxx/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pxx-orchestrator)](https://pypi.org/project/pxx-orchestrator/)
[![Python](https://img.shields.io/pypi/pyversions/pxx-orchestrator)](https://pypi.org/project/pxx-orchestrator/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

`pxx` is an async, event-sourced coding-agent runtime that runs against **your own
inference** (Ollama, vLLM, or any OpenAI-compatible endpoint) — no cloud dependency,
no telemetry, no API keys required. It pairs a native tool-calling agent loop with
persistent cross-session memory, deterministic safety gates, and MCP interop, and
can delegate to [aider](https://github.com/Aider-AI/aider) as an optional edit engine.

> **New here? → [Hands-on tutorial](https://github.com/cdnwetzel/pxx/blob/v2/docs/TUTORIAL.md)**
> Every claim this project makes is evidence-gated — dated records, reproducible
> procedures, and explicit non-claims live in
> [docs/RECEIPTS.md](https://github.com/cdnwetzel/pxx/blob/v2/docs/RECEIPTS.md).

> pxx 2.0 is a ground-up rewrite of pxx 1.x. The 1.x control-plane semantics
> (fail-closed gates, scope limits, bounded loops, audit) are preserved; the
> execution layer (`os.execv` into aider, argv scanning, sidecar services) is
> replaced by an async runtime where **pxx owns the agent loop**. See
> [DESIGN.md](DESIGN.md) for the architecture contract and
> [docs/MIGRATION.md](https://github.com/cdnwetzel/pxx/blob/v2/docs/MIGRATION.md) for 1.x → 2.0 changes.

## Why pxx

- **Offline-capable**: all inference stays on your machine or your LAN.
- **pxx owns the runtime**: every model/tool event flows through pxx's event bus.
  Backends cannot bypass policy — scope, permissions, budgets, and hooks are
  enforced by the host, never by the model.
- **Persistent memory**: observations from previous sessions (files changed, tool
  outcomes, your own `remember` notes) are stored in a local SQLite database with
  hybrid BM25 + vector search and injected deterministically at session start.
- **Fail-closed safety**: read-only by default. Writes require `edit`/`run` mode,
  stay inside a canonicalized scope (symlinks resolved), shell commands are gated
  in every write-capable mode (a hook, sandbox, or explicit opt-in — even under
  unattended `run`), and every run ends with a machine-readable terminal code in
  a hash-chained audit log.
- **Interop**: consumes MCP servers as tools, and exposes its own memory as an MCP
  server for other agents (Claude Code, goose, opencode, …).

## Where pxx fits

This is a fit guide, not a sales pitch — pxx isn't competing on raw capability. It
exists because one distinction is **architectural, not marketing:** a hosted SaaS
coding agent *cannot* be self-hosted. Your source and your inference run on the
vendor's infrastructure by construction. Where that's permitted you have many good
options. Where it isn't — regulated, air-gapped, IP-sensitive, sovereignty-required —
that entire category is off the table before capabilities are even discussed, because
those environments demand a higher standard than "the vendor is trustworthy": the code
and the inference must stay inside controls the operator owns, and every action must be
auditable.

pxx is built to that sovereign standard:

- **Self-hosted, always.** Code and inference stay on hardware *you* own — local,
  on-prem, or fully air-gapped; your models, your network, your key management, your
  audit boundary. No vendor in the loop, no telemetry, no data egress.
- **Host-enforced, not model-trusted.** Scope, permissions, budgets, and hooks are
  enforced by the host — a jailbroken or confused model still can't leave its lane —
  and every action and approval lands in a hash-chained, tamper-evident audit log.
- **Open and inspectable — no unknowns.** MIT, under the same community model that
  made Linux and the tools you already rely on — not "source-available," not open-core
  with the real logic hidden behind a service. You can read the code and the docs and
  follow every gate, tool, and decision end to end; there are no hidden functions and
  no telemetry.
- **Evidence-gated — a step beyond typical open source.** Open source hands you the
  source; pxx also keeps the *receipts*. Every capability claim maps to a dated,
  reproducible record in
  [docs/RECEIPTS.md](https://github.com/cdnwetzel/pxx/blob/v2/docs/RECEIPTS.md) with an
  explicit statement of what is *not* claimed — evidence, not just availability.

The through-line is **verify, don't trust:** no vendor to trust (there isn't one), no
binary to trust (read it), no claims to trust (check the receipts).

**Use pxx when** self-hosting, sovereignty, and provable audit are *requirements* — when
the work simply cannot leave your perimeter and "trust the vendor" isn't a sufficient
control.

pxx is deliberately **not** trying to be the tool for every environment. If your
environment permits SaaS and you want maximum capability on hard or greenfield work, a
hosted frontier agent will do more; if you want turn-by-turn pair editing, a local
assistant such as [aider](https://github.com/Aider-AI/aider) (which pxx can delegate to)
may fit better. pxx has no browser yet and leans on smaller local models — it trades
breadth for sovereignty and auditability, on purpose. It's early (2.x) and
solo-maintained; the receipts, not the prose, are the source of truth.

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
with HTTP 400 (`"auto" tool choice requires …`). `pxx doctor` probes for this
under a realistic agent context and reports if a model accepts `tools` but
answers in prose (some small models tool-call on a toy probe yet degrade under
a real loop prompt on constrained hardware).
`ask`/`edit` (and `run` with `--backend aider`) can sidestep via the aider
backend; `pxx loop` is native-only and cannot.

## Quick start

```sh
pxx doctor                          # check your setup

pxx ask -m "Explain main.py"        # read-only (default): no writes, no shell
pxx edit -m "Add error handling to main.py"   # writes allowed, in scope
pxx edit --commit -m "…"            # + commit the change on COMPLETED (opt-in)
pxx run  -m "Add tests for utils.py"          # unattended, budget-capped
pxx loop -m "Fix the failing tests" --scope src  # bounded edit→test→review loop
pxx chat                            # interactive session
```

Edits land **uncommitted** by default so you can review them first — pass
`--commit` (or `PXX_AUTO_COMMIT=1` / `auto_commit = true`) to have a COMPLETED
run commit its work. The `pxx-pre/<ts>` safety tag always points at the
pre-session HEAD, so undo is `git reset --hard <tag>` either way.

Permission modes: **ask** (read-only) → **plan** (plan only) → **edit** (writes in
scope, shell via hooks) → **auto** (unattended, budgets enforced). Every run is
bounded: max rounds/tokens/cost/wall-clock/diff-lines, all configurable.

> **New to pxx?** The [**hands-on tutorial**](https://github.com/cdnwetzel/pxx/blob/v2/docs/TUTORIAL.md) takes you from install to building
> (and safely undoing) a small tool in ~25 minutes — the fastest way to get the mental model:
> read-only by default, scoped edits, and the safety tag that nets your work.

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
pxx review [--staged|--since SHA]  # read-only review of the current diff (exit 2 on REVISE)
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
pxx improve triage list|qualify|reject   # durable human verdicts on proposals
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
run (see [docs/MIGRATION.md](https://github.com/cdnwetzel/pxx/blob/v2/docs/MIGRATION.md)).

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

Pull requests are reviewed automatically by
[CodeRabbit](https://coderabbit.ai) (config in `.coderabbit.yaml`) in
addition to human review.

## License

MIT — see [LICENSE](LICENSE).
