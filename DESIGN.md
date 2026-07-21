# pxx 2.0 ("pxx_ng") — Architecture & Contracts

**This file is the authoritative contract for every module. Implement against it exactly.**
If a contract must change, change it here first.

pxx 2.0 is a ground-up rewrite of pxx 1.x (aider orchestrator). The 1.x trusted
control plane semantics (fail-closed gates, scope, audit, bounded loops) are
preserved; the execution layer (execv-into-aider, argv scanning, dead memory
pipeline) is replaced by an async, event-sourced runtime where pxx owns the
agent loop and aider is one pluggable backend.

## Principles

1. **pxx owns the runtime.** Every model/tool event flows through pxx's event
   bus. Backends cannot bypass policy (scope, permissions, budgets, hooks).
2. **Local-first, offline-capable.** Core works with zero network except a
   reachable OpenAI-compatible / Ollama endpoint. No mandatory cloud deps.
3. **Fail-closed gates, best-effort telemetry.** Safety/scope/budget/hook
   denials stop the action. Audit and memory writes never crash a session
   (wrapped, logged to stderr).
4. **Pure core, thin I/O edges.** Modules that make policy decisions take
   data in and return decisions; subprocess/network/FS I/O is injected or
   isolated in small edge modules. Everything testable without network,
   without Ollama, without aider, without a git repo.
5. **Async core.** All long-running operations are `async`. Sync shims only
   at the CLI boundary (`asyncio.run`).
6. **Paths are untrusted input.** Every path from a model or user is
   canonicalized (`os.path.realpath`, symlinks resolved) before any gate
   decision. Model output never defines the trust boundary.

## Package layout

```
pxx/
  __init__.py          # __version__ = "2.0.0"  (no side effects at import!)
  config.py            # Settings, layered config resolution
  events.py            # Event model, EventBus, AuditLog (hash-chained JSONL)
  outcome.py           # TerminalCode enum, RunOutcome
  safety.py            # PermissionMode, ScopeGate, HookRunner, BudgetGuard
  broker.py            # ActionBroker: per-action-class authorization (single authority)
  workflow.py          # WORKFLOW.md machine contract (load/validate, fail closed)
  clarify.py           # ambiguity gate: ready_to_act -> ReadyDecision
  audit_sampling.py    # deterministic human audit sampling (14.5)
  router.py            # Endpoint probing, ModelRegistry, fallback chains
  session.py           # Session: wires config+backend+memory+safety+events
  loop.py              # bounded autonomous loop (edit -> test -> review)
  review.py            # review gate: Finding, Verdict, backends, policy
  memory/
    __init__.py        # re-exports MemoryStore, capture, inject
    store.py           # SQLite + FTS5 + vector search, retention/archive
    embeddings.py      # Embedder protocol; OllamaEmbedder; HashEmbedder
    capture.py         # events/git-diff -> observations (post-session)
    inject.py          # deterministic session-start context builder
  tools/
    __init__.py        # Tool protocol, ToolRegistry, ToolContext
    fs.py              # read_file, write_file, edit_file, list_files, search_files
    shell.py           # run_shell (scope-gated, optional sandbox-exec)
    memory_tools.py    # recall_memory, remember
  backends/
    __init__.py
    base.py            # AgentBackend protocol, BackendCapabilities
    native.py          # pxx's own OpenAI-compatible tool-calling agent loop
    aider.py           # aider subprocess backend (headless stdio, optional dep)
    mock.py            # scripted backend for tests (no I/O)
  mcp/
    __init__.py
    client.py          # minimal MCP stdio client (JSON-RPC, tools/list+call)
    server.py          # exposes memory store as an MCP stdio server
  server.py            # `pxx serve` headless HTTP API (fastapi extra)
  doctor.py            # health checks, config/endpoint report
  upgrade.py           # self-update (pip/pipx/uv tool detection)
  cli.py               # argparse CLI, 1.x flag compat shim
  prompts/
    native_system.md   # system prompt for the native backend
    review.md          # reviewer prompt
```

## Core contracts

### config.py
```python
@dataclass(frozen=True)
class ModelRef:
    provider: str          # "ollama" | "openai" | "vllm" | "openai-compatible"
    model: str             # e.g. "qwen2.5-coder:7b"
    base_url: str | None = None
    api_key: str | None = None

@dataclass(frozen=True)
class Budgets:
    max_rounds: int = 25
    max_tokens: int = 200_000
    max_cost_usd: float = 5.0
    max_wall_seconds: float = 1800.0
    max_diff_lines: int = 400

@dataclass(frozen=True)
class Settings:
    model: ModelRef
    fallback_models: tuple[ModelRef, ...] = ()
    permission: PermissionMode = PermissionMode.ASK   # default read-only
    scope: tuple[str, ...] = ()                        # repo-relative prefixes; () = repo root
    trusted_paths: tuple[str, ...] = ()
    budgets: Budgets = Budgets()
    memory_enabled: bool = True
    memory_dir: Path = ~/.pxx (XDG-aware)
    state_dir: Path = ~/.local/state/pxx (XDG_STATE_HOME-aware)
    hooks: tuple[Hook, ...] = ()
    test_command: str | None = None
    sandbox_shell: bool = False
    mcp_servers: tuple[McpServerSpec, ...] = ()
```
- `load_settings(cwd, cli_overrides) -> Settings`: precedence =
  CLI flags > `PXX_*` env vars > `./pxx.toml` / `./.pxx/config.toml` >
  `~/.config/pxx/config.toml` > built-in defaults. TOML parsed with stdlib
  `tomllib`. Unknown keys raise `ConfigError` listing the key (fail-closed
  config, no silent typos).
- Env file `~/.config/pxx/env` (KEY=VALUE) still honored, loaded via
  `setdefault` **by `load_settings`**, never at import time.

### events.py
```python
@dataclass(frozen=True)
class Event:
    kind: str            # "session_start"|"model_request"|"model_response"|
                         # "tool_call"|"tool_result"|"file_changed"|
                         # "gate_decision"|"observation"|"budget"|"error"|"session_end"
    data: dict[str, Any]
    session_id: str
    ts: float = time.time()
    seq: int = 0         # assigned by EventBus

class EventBus:        # async pub/sub; subscribers are async callables
    def subscribe(self, fn) -> None
    async def emit(self, kind: str, data: dict) -> Event

class AuditLog:        # hash-chained JSONL, best-effort
    def __init__(self, state_dir: Path)
    async def record(self, event: Event) -> None
    # each line: {..., "prev_hash": str, "hash": str}; hash = sha256(canonical_json(prev_hash + event))
    @staticmethod
    def verify(path: Path) -> bool   # chain integrity check
```
- Audit records **metadata only**: no prompt bodies, no file contents, no
  diffs, no secrets. URLs are credential-scrubbed before recording.

### outcome.py
```python
class TerminalCode(StrEnum):
    COMPLETED | INTERRUPTED | BUDGET_EXCEEDED | ROUND_CAP | DIFF_CAP
    CLARIFICATION_REQUIRED
    EDIT_FAILED | EDIT_TIMEOUT
    TEST_RUN_FAILED | TEST_REGRESSION | NO_TEST_PROGRESS | LINT_BLOCKED
    REVIEW_REJECTED | REVIEW_UNAVAILABLE | REVIEW_EMPTY | REVIEW_UNPARSEABLE
    OUT_OF_SCOPE | HOOK_DENIED | HOOKS_MISSING
    MODEL_UNAVAILABLE | CONFIGURATION_INVALID
@dataclass(frozen=True)
class RunOutcome:
    code: TerminalCode
    summary: str
    rounds: int
    tokens: int
    diff_lines: int
    cost_usd: float | None = None        # None = unpriced; never fabricated
    findings: tuple[dict, ...] = ()
    contributing_codes: tuple[str, ...] = ()  # one terminal + contributing
    edit_seconds: float; test_seconds: float; review_seconds: float
    files_changed: int
    baseline_failures: int; introduced_failures: int; terminal_failures: int
    lint_errors: int
    findings_by_severity: dict[str, int]
    unparseable_review_count: int
    injected_observation_ids: tuple[str, ...]
```
The 18 canonical codes map to 12.2 as: COMPLETED≡APPROVED,
DIFF_CAP≡DIFF_BUDGET_EXCEEDED, ROUND_CAP≡ROUND_CAP_EXCEEDED,
BUDGET_EXCEEDED≡TIME_BUDGET_EXCEEDED; INTERRUPTED/CLARIFICATION_REQUIRED/
HOOK_DENIED are repo additions.

### safety.py
```python
class PermissionMode(StrEnum):
    ASK = "ask"        # read-only: no writes, no mutating shell
    PLAN = "plan"      # like ask, model asked to produce a plan only
    EDIT = "edit"      # writes allowed within scope; shell gated by hooks
    AUTO = "auto"      # unattended: writes+shell allowed within scope/budgets

class ScopeGate:
    def __init__(self, root: Path, scope: tuple[str, ...], trusted: tuple[str, ...])
    def check(self, path: str | Path) -> Path   # -> canonical Path or raises ScopeViolation
    def is_write_allowed(self, path) -> bool    # permission-aware
class HookRunner:
    # hooks fire PreToolUse / PostToolUse; JSON on stdin, exit 0 = allow,
    # exit 2 = deny (deterministic, cannot be overridden by the model)
    async def run_pre(self, tool_name: str, args: dict) -> None  # raises HookDenied
    async def run_post(self, tool_name: str, args: dict, result: str) -> None
class BudgetGuard:
    def consume(self, *, rounds=0, tokens=0, cost=0.0, diff_lines=0) -> None
    # raises BudgetExceeded with the specific budget that tripped; wall clock
    # enforced by the session via deadline comparison
```

### router.py
- `async probe_endpoints(specs) -> list[Endpoint]`: async httpx probes with
  1s timeout; Ollama native (`/api/tags`) and OpenAI-compatible (`/v1/models`).
- `ModelRegistry`: known context windows for common local models
  (qwen2.5-coder, devstral, llama3.1, gemma, deepseek-coder...), probed
  metadata wins over table, table wins over 8192 default.
- Priority: explicit `--model`/`PXX_MODEL` > vLLM endpoints > Ollama.
  First reachable wins. Returns `ModelRef`.

### memory/
- `store.py`: `MemoryStore(path)` — SQLite, WAL mode.
  Tables: `observations(id INTEGER PK, project TEXT, kind TEXT, content TEXT,
  tags TEXT(json), source TEXT, session_id TEXT, confidence REAL,
  created_at REAL, expires_at REAL NULL, embedding BLOB NULL, archived INT)`,
  plus `observations_fts` FTS5(content, tags, content=observations).
  API: `add(project, kind, content, *, tags, source, session_id, confidence,
  ttl_days) -> id` (dedupe via UNIQUE(project, content) hash), `search(project,
  query, *, k=8) -> list[Observation]` (hybrid: FTS5 bm25 rank 0.4 + cosine
  0.6, pure-python cosine over float32 blobs, no numpy required),
  `forget(id)`, `archive_expired() -> int` (moves to
  `memory-archive/YYYY-MM.jsonl`), `list(project)`, `stats()`.
- `embeddings.py`: `Embedder` protocol (`embed(texts) -> list[bytes]` of
  float32). `OllamaEmbedder(base_url, model="nomic-embed-text")` async via
  httpx; `HashEmbedder(dim=256)` deterministic token-hash embedding — the
  offline default and test embedder. Store picks Ollama if reachable at
  session start, else HashEmbedder (decision recorded in audit).
- `capture.py`: `observations_from_events(events) -> list[NewObservation]`
  (tool_result/file_changed rollups) and `observations_from_git(pre_sha,
  root)` (post-session diff rollup; stdlib subprocess, works without repo).
- `inject.py`: `async build_context(store, project, task_hint, budget_tokens=1500)
  -> str` — deterministic session-start memory context: curated pinned
  observations first, then hybrid search hits; markdown, hard token budget.
  Memory is **context, never policy**.

### tools/
```python
@dataclass(frozen=True)
class ToolSpec:      # name, description, JSON-schema dict, mutating: bool
class Tool(Protocol):
    spec: ToolSpec
    async def run(self, args: dict, ctx: ToolContext) -> str
@dataclass
class ToolContext:   # scope: ScopeGate, hooks: HookRunner, permission, bus,
                     # memory: MemoryStore|None, cwd: Path
class ToolRegistry:  # register(tool), specs() -> [openai tool schema],
                     # async call(name, args_json) -> str
```
- `fs.py`: read_file (offset/limit), write_file (permission>=EDIT, in-scope),
  edit_file (exact old/new string replace, unique match), list_files (glob),
  search_files (ripgrep if on PATH else pure-python fallback).
- `broker.py`: the single authorization authority. `ToolRegistry.call`
  classifies every call into a `ToolAction` (action class READ/WRITE/DELETE/
  SHELL/NETWORK/MEMORY, risk tier, canonicalized targets) and routes it
  through `ActionBroker.authorize` — per-action-class authorization against
  the active `PermissionProfile` (from WORKFLOW.md `[permissions]`, else
  built-in defaults), scope enforcement, PreToolUse hooks as the deny
  substrate, and `tool_action_proposed` + `policy_decision` events on every
  call. Fail closed: unclassifiable action or no profile entry -> denied.
- `workflow.py` + repo-root `WORKFLOW.md`: the repository-owned machine
  contract (states, budgets, commands, permission profiles, hooks,
  protected-paths mirror). `load_workflow` fails closed on unknown keys /
  missing sections / bad types. Hashed into every agent manifest (with the
  protected-paths list) so contract edits mint new `agent_version_id`s.
- `clarify.py`: the ambiguity gate. `ready_to_act` runs before the first
  backend round (session entry + loop round 1); ambiguous tasks stop with
  `CLARIFICATION_REQUIRED` and a surfaced question, without editing.
- `audit_sampling.py`: deterministic human-audit flags (100% promotions /
  high-risk, ~20% ordinary, sha256 of run_id — no RNG).
- `shell.py`: run_shell — allowed in AUTO; in EDIT only if a PreToolUse hook
  allows it (fail-closed); never in ASK/PLAN. `sandbox_shell=True` wraps in
  `sandbox-exec -f <generated profile>` on macOS / `bubblewrap` on Linux when
  available. Timeout 60s default, output capped at 32 KiB.
- Tool surface is deliberately ~8 tools (small local models degrade past ~10).

### backends/
```python
class BackendCapabilities(NamedTuple):
    streaming: bool; tools: bool; interactive: bool; headless: bool
class AgentBackend(Protocol):
    name: str
    capabilities: BackendCapabilities
    async def run(self, task: str, ctx: SessionContext) -> RunOutcome
    async def cancel(self) -> None
```
- `native.py`: pxx's own loop — OpenAI-compatible `/v1/chat/completions`
  with `tools=[registry.specs()]`, non-streaming JSON per round (streaming
  optional later), executes tool calls through `ToolRegistry` (which enforces
  gates/hooks/budgets), appends results, repeats until stop or budget.
  Emits all events. Fallback chain: on connection error, try next ModelRef.
- `aider.py`: runs `aider --message <task> --yes-always --no-stream
  [--no-git]` as an async subprocess; maps permission ASK->`--chat-mode ask`,
  EDIT->`--chat-mode diff`-ish defaults; passes scope context via a temp
  `--read` file; captures file changes via pre/post `git rev-parse HEAD`
  diff; streams stdout lines as `model_response` events. Import/launch
  guarded: `shutil.which("aider")` or importable `aider`; else
  `BackendUnavailable` with install hint. aider is an **optional** extra.
- `mock.py`: `MockBackend(script: list[Step])` — scripted tool calls /
  responses for tests; full determinism, no I/O.

### mcp/
- `client.py`: stdio JSON-RPC client — `initialize`, `tools/list`,
  `tools/call` only (spec 2025-11-25 subset). Spawns the server subprocess
  and frames messages as newline-delimited JSON (MCP stdio framing).
  Surfaces remote tools through the ToolRegistry with namespaced names
  `mcp__<server>__<tool>`.
- `server.py`: `pxx mcp` — exposes memory tools (`memory_search`,
  `memory_add`, `memory_list`) over stdio for other agents (Claude Code,
  goose, opencode). Newline-delimited JSON-RPC.

### session.py
`Session(settings, backend, bus)` orchestrates one run: creates AuditLog,
opens MemoryStore, builds ToolRegistry (+ MCP client tools when configured),
injects memory context into the task prompt, installs SIGINT handling
(-> TerminalCode.INTERRUPTED), enforces wall-clock deadline, runs post-session
memory capture, writes terminal audit record, returns `RunOutcome`.

### loop.py / review.py
- `run_loop(task, settings, ...) -> RunOutcome`: bounded rounds
  (default 3): native/aider backend edit round -> run `test_command` ->
  review gate -> heal with reviewer findings. Same guards as 1.x: round cap,
  diff budget, monotonic failing-set progress (NO_PROGRESS), scope re-check
  after each round (aider commits can bypass hooks), lint gate.
- `review.py`: `Finding(id, severity, file, line, message)`,
  `Verdict(StrEnum): APPROVE|REVISE|NO_REVIEW`; fail-closed: unknown output
  -> REVISE, no evidence -> NO_REVIEW; `mode` blocking|advisory (advisory
  never blocks). Reviewer backend = native backend with `prompts/review.md`.

### server.py (extra: `pxx[server]`)
FastAPI app: `POST /v1/sessions` (start run, returns session_id),
`GET /v1/sessions/{id}/events` (SSE stream from EventBus),
`POST /v1/sessions/{id}/cancel`, `GET /v1/health`,
`GET/POST /v1/memory/*` proxy to MemoryStore. Binds 127.0.0.1 by default;
token auth via `PXX_SERVER_TOKEN` when bound to non-loopback.

### cli.py
argparse. Subcommands:
`ask` (default), `edit`, `plan`, `run` (native, unattended within budgets),
`loop`, `chat`, `memory {search,add,list,forget}`, `mcp`, `serve`, `doctor`,
`upgrade`, `audit {verify, tail}`. Global: `--model`, `--scope`, `--budget-*`,
`--no-memory`, `--sandbox`, `-m/--message`, files as positionals.
Self-improvement verbs (see DESIGN-ROADMAP.md "### CLI additions"):
`runs {list,show,export}`, `agents {list,show}`, `verify [run-id]`,
`metrics {summary,failures,memory-impact,export}`, `eval {run,self-check,report}`,
`calibrate`, `improve {analyze,clusters,proposals,cycle}`, `propose`,
`compare`, `agent {activate,rollback,history,channels}`, `promote`,
`check [--all-files]`, `goal`, `workflow {validate}`, `context {audit}`,
`docs {check}`. Fail-closed verdicts exit 2; usage errors
(unknown command, empty task, invalid candidate, missing evidence/approver,
empty corpus) exit 64 — CI keying "2 = a gate fired" can rely on the split.
**1.x compat shim**: bare `pxx` == `pxx ask`; `--edit` == `pxx edit`;
`--with-memory` == default-on now (no service needed); unknown flags forward
to aider **only** when the aider backend is selected, with a deprecation
warning. `--self-test/--self-lint/--doctor` map to new subcommands.

## Conventions

- Python >= 3.11, no upper bound. Deps (core): `httpx>=0.27` only.
  Extras: `aider` (aider-chat, python <3.13 only), `server` (fastapi+uvicorn),
  `dev` (pytest, ruff).
- Style: ruff defaults (E/F/W), target py311, `from __future__ import
  annotations` everywhere, dataclasses over dicts, StrEnum for enums, no
  print() outside cli/doctor (use `logging.getLogger("pxx")`).
- Errors: `pxx.errors` — `PxxError` base; `ScopeViolation`, `HookDenied`,
  `BudgetExceeded`, `BackendUnavailable`, `ConfigError`, `GateFailed`.
  Gates raise; telemetry suppresses.
- Tests: pytest, no network, no Ollama, no aider, no git required. Async
  tested via `asyncio.run()` in sync test functions (no pytest-asyncio dep).
  Mock backend + HashEmbedder + tmp_path everywhere. Every module ships with
  tests in `tests/test_<module>.py`.
- The legacy 1.x code lives in git history (tag/HEAD before rewrite); it is
  NOT imported by 2.0.
