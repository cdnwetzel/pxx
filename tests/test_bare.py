"""Tests for Settings.bare — payload-only reduction for contract-gated callers.

Bare mode (``bare = true``, default OFF) strips the native backend's
chat-completions request down to the task itself: no system message, no
``tools`` key, and no tool schemas are even constructed. It exists for
single-shot, no-tool, contract-gated callers (e.g. ACP's isolated drill
workers) whose own fail-closed validation makes the coding-agent scaffolding
a measured degradant. Everything else — clarity, scope, hooks, budgets,
permissions, transport retry/fallback, advisory truthfulness, config
validation, audit — is unchanged.

Tool-mediated gates (scope/hook checks inside ``ToolRegistry.call``) are
STRUCTURALLY UNREACHABLE in bare mode, by construction: no tools are
advertised, so the model cannot make a tool call, so ``ctx.tools.call`` can
never be invoked. Their enforcement is preserved, not weakened — there is no
tool surface left to gate. (Should a misbehaving serving layer return
tool_calls anyway, the loop still routes them through the gated registry.)

No network, no Ollama, no git repo: the backend is driven through
httpx.MockTransport; session-level runs use MockBackend or a stubbed
model-fingerprint probe.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

import pxx.cli as cli
from pxx.backends.base import SessionContext
from pxx.backends.mock import MockBackend
from pxx.backends.native import NativeBackend
from pxx.config import ModelRef, Settings
from pxx.errors import BudgetExceeded, ConfigError
from pxx.events import EventBus
from pxx.outcome import RunOutcome, TerminalCode
from pxx.safety import BudgetGuard, Budgets, HookRunner, PermissionMode, ScopeGate
from pxx.session import Session
from pxx.tools import default_registry


class SpyRegistry:
    """ToolRegistry stand-in that records specs() calls."""

    def __init__(self) -> None:
        self.specs_calls = 0

    def specs(self) -> list[dict]:
        self.specs_calls += 1
        return [
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
            }
        ]

    async def call(self, name: str, args: dict, ctx) -> str:  # pragma: no cover
        raise AssertionError("no tool call may execute in these tests")


def make_ctx(
    tmp_path: Path,
    *,
    bare: bool,
    tools=None,
    settings: Settings | None = None,
    budgets: Budgets | None = None,
    memory_context: str = "",
) -> SessionContext:
    settings = settings or Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        bare=bare,
    )
    return SessionContext(
        settings=settings,
        bus=EventBus(),
        scope=ScopeGate(tmp_path),
        hooks=HookRunner(),
        budgets=BudgetGuard(budgets or settings.budgets),
        tools=tools or SpyRegistry(),
        memory=None,
        session_id="test",
        project=tmp_path.name,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        memory_context=memory_context,
    )


def completion(content: str, *, total_tokens: int = 10) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": total_tokens - 4,
            "completion_tokens": 4,
            "total_tokens": total_tokens,
        },
    }


def make_backend(handler) -> NativeBackend:
    return NativeBackend(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def capture_handler(captured: list[dict], response: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response or completion("done"))

    return handler


# --- bare payload shape (negative space) -------------------------------------


def test_bare_payload_is_a_single_user_message(tmp_path: Path) -> None:
    captured: list[dict] = []
    ctx = make_ctx(tmp_path, bare=True)
    outcome = asyncio.run(make_backend(capture_handler(captured)).run("the task text", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert len(captured) == 1
    payload = captured[0]
    # Exactly one message, and it is the task — no system message at all.
    assert payload["messages"] == [{"role": "user", "content": "the task text"}]
    # No tools key AT ALL (not even an empty list).
    assert "tools" not in payload


def test_bare_payload_omits_memory_context_and_system_prompt(tmp_path: Path) -> None:
    """memory_context is built by the session but must NOT reach a bare
    payload; nor may the fallback system prompt appear."""
    captured: list[dict] = []
    ctx = make_ctx(tmp_path, bare=True, memory_context="memory-marker-xyzzy")
    asyncio.run(make_backend(capture_handler(captured)).run("task", ctx))
    payload = captured[0]
    assert not [m for m in payload["messages"] if m["role"] == "system"]
    blob = json.dumps(payload)
    assert "memory-marker-xyzzy" not in blob
    assert "local-first coding agent" not in blob  # _FALLBACK_SYSTEM_PROMPT
    assert payload["messages"][0]["content"] == "task"


def test_bare_never_constructs_tool_specs(tmp_path: Path) -> None:
    """No tool schemas (built-in or MCP) are constructed in bare mode."""
    spy = SpyRegistry()
    ctx = make_ctx(tmp_path, bare=True, tools=spy)
    asyncio.run(make_backend(capture_handler([])).run("task", ctx))
    assert spy.specs_calls == 0


def test_default_mode_constructs_tool_specs(tmp_path: Path) -> None:
    """Control: default mode builds the tool specs exactly once."""
    spy = SpyRegistry()
    ctx = make_ctx(tmp_path, bare=False, tools=spy)
    asyncio.run(make_backend(capture_handler([])).run("task", ctx))
    assert spy.specs_calls == 1


# --- default payload: positive shape + golden fixture -------------------------


def _golden_ctx() -> SessionContext:
    """Fixed-everything context for the golden payload: fixed model, task,
    cwd/scope and empty memory context, so the capture is reproducible."""
    settings = Settings(
        model=ModelRef(provider="ollama", model="golden-model", base_url="http://test.local")
    )
    cwd = Path("/pxx-golden-cwd")
    return SessionContext(
        settings=settings,
        bus=EventBus(),
        scope=ScopeGate(cwd),
        hooks=HookRunner(),
        budgets=BudgetGuard(settings.budgets),
        tools=default_registry(),
        memory=None,
        session_id="golden",
        project="golden",
        cwd=cwd,
        cancel_event=asyncio.Event(),
        memory_context="",
    )


def test_default_payload_positive_shape(tmp_path: Path) -> None:
    captured: list[dict] = []
    ctx = make_ctx(tmp_path, bare=False, tools=default_registry())
    asyncio.run(make_backend(capture_handler(captured)).run("task", ctx))
    payload = captured[0]
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1] == {"role": "user", "content": "task"}
    # Tool count is asserted against the registry itself, not a literal.
    assert len(payload["tools"]) == len(default_registry().specs())


# Canonical default payload, captured from the fixed context above. After an
# INTENTIONAL change to the system prompt or tool registry, regenerate with:
#   uv run python -c "
#   import asyncio, json, sys; sys.path.insert(0, 'tests')
#   from test_bare import _golden_ctx, capture_handler, completion, make_backend
#   captured = []
#   asyncio.run(make_backend(capture_handler(captured)).run('golden task', _golden_ctx()))
#   print(json.dumps(captured[0], sort_keys=True, separators=(',', ':')))"
# and paste the output over the fixture below.
_GOLDEN_DEFAULT_PAYLOAD = r"""{"messages":[{"content":"# pxx \u2014 local coding agent\n\nYou are pxx, a coding agent running locally against the user's repository. You\ncomplete tasks by calling the provided tools and then reporting back concisely.\n\n## Tool discipline\n\n- Use the tools to inspect before you act: never guess file contents \u2014 read\n  the file first.\n- Make the smallest change that satisfies the task. Do not refactor, reformat,\n  or \"improve\" unrelated code.\n- Only call tools that exist in your tool list. If a capability is missing,\n  say so instead of improvising around it.\n\n## Scope and safety (absolute)\n\n- The declared scope is a hard trust boundary: never attempt to read, write,\n  or execute anything outside it. Scope, permission, hook, and budget gates\n  are deterministic and cannot be argued with \u2014 a denial is final, adjust\n  your approach instead of retrying the same denied action.\n- Respect the permission mode: in read-only modes (ask/plan) do not call\n  mutating tools at all. In plan mode, output a concrete, ordered\n  implementation plan and stop.\n\n## Editing files\n\n- Prefer `edit_file` for changes to existing files. Its `old_string` must\n  match the file **exactly once**, byte for byte including whitespace and\n  indentation \u2014 read the file first and copy the snippet verbatim.\n- Use `write_file` only for new files or full rewrites you can justify.\n- After editing, re-read or test the affected code when the tools allow it.\n\n## When to stop\n\nStop calling tools and reply with a final summary when:\n- the task is complete \u2014 summarize what changed, where, and anything the user\n  should verify; or\n- you are blocked (missing information, denied gate, repeated failures) \u2014\n  explain the blocker and what you need.\nDo not keep looping once there is nothing useful left to do.\n\n\n## Scope\nYou may only read and write paths inside: /pxx-golden-cwd\nNever attempt paths outside this scope; scope gates are absolute.\n\n## Permission mode: ask\n\nAsk mode: you are read-only. Inspect and answer; do not attempt writes.","role":"system"},{"content":"golden task","role":"user"}],"model":"golden-model","tools":[{"function":{"description":"Read a file with line numbers. Use offset/limit to page through large files (at most 2000 lines per call).","name":"read_file","parameters":{"properties":{"limit":{"description":"Max lines to return (default and hard cap 2000).","type":"integer"},"offset":{"default":1,"description":"1-based line number to start at (default 1).","type":"integer"},"path":{"description":"File path (relative to project root).","type":"string"}},"required":["path"],"type":"object"}},"type":"function"},{"function":{"description":"Write content to a file, creating parent directories. Overwrites existing files.","name":"write_file","parameters":{"properties":{"content":{"description":"Full file content to write.","type":"string"},"path":{"description":"File path (relative to project root).","type":"string"}},"required":["path","content"],"type":"object"}},"type":"function"},{"function":{"description":"Replace an exact string in a file. old_string must match exactly one location; include enough context to make it unique.","name":"edit_file","parameters":{"properties":{"new_string":{"description":"Replacement text.","type":"string"},"old_string":{"description":"Exact text to replace.","type":"string"},"path":{"description":"File path (relative to project root).","type":"string"}},"required":["path","old_string","new_string"],"type":"object"}},"type":"function"},{"function":{"description":"Remove a file from the project by staging it: the file is moved to the operator's delete staging area (kept, recoverable), never deleted outright. Use when a file should no longer exist in the project (moved, obsolete, superseded). Directories are not removed.","name":"delete_file","parameters":{"properties":{"path":{"description":"File path (relative to project root).","type":"string"},"reason":{"description":"One line: why the file should go.","type":"string"}},"required":["path"],"type":"object"}},"type":"function"},{"function":{"description":"List files under the project matching a glob pattern (default '**/*'). Skips .git, __pycache__ and node_modules.","name":"list_files","parameters":{"properties":{"limit":{"default":200,"description":"Max entries to return (default 200).","type":"integer"},"pattern":{"default":"**/*","description":"Glob pattern relative to the project root.","type":"string"}},"type":"object"}},"type":"function"},{"function":{"description":"Search file contents with a regex. Uses ripgrep when available, otherwise a pure-python fallback. Returns 'path:line: match'.","name":"search_files","parameters":{"properties":{"limit":{"default":50,"description":"Max matches to return (default 50).","type":"integer"},"path":{"default":".","description":"File or directory to search (default: project root).","type":"string"},"pattern":{"description":"Regular expression to search for.","type":"string"}},"required":["pattern"],"type":"object"}},"type":"function"},{"function":{"description":"Run a shell command via /bin/sh -c. Combined stdout+stderr is returned (capped at 32 KiB) with the exit code. Availability depends on the session permission mode.","name":"run_shell","parameters":{"properties":{"command":{"description":"Shell command to run.","type":"string"},"timeout":{"default":60,"description":"Timeout in seconds (default 60).","type":"integer"}},"required":["command"],"type":"object"}},"type":"function"},{"function":{"description":"Search long-term memory for observations relevant to a query.","name":"recall_memory","parameters":{"properties":{"k":{"default":5,"description":"Max observations to return (default 5).","type":"integer"},"query":{"description":"What to recall.","type":"string"}},"required":["query"],"type":"object"}},"type":"function"},{"function":{"description":"Store a fact/decision/gotcha in long-term memory for future sessions. Available in every permission mode (memory is telemetry, not a file write).","name":"remember","parameters":{"properties":{"content":{"description":"What to remember.","type":"string"},"tags":{"default":"","description":"Optional comma-separated tags.","type":"string"}},"required":["content"],"type":"object"}},"type":"function"}]}"""


def test_default_payload_golden_fixture() -> None:
    """Pins the current default payload (9-tool registry + system prompt) so
    an intentional change requires a deliberate fixture update."""
    captured: list[dict] = []
    ctx = _golden_ctx()
    asyncio.run(make_backend(capture_handler(captured)).run("golden task", ctx))
    assert len(captured) == 1
    canonical = json.dumps(captured[0], sort_keys=True, separators=(",", ":"))
    assert canonical == _GOLDEN_DEFAULT_PAYLOAD


# --- audit metadata (body-free, both modes) -----------------------------------


def test_bare_event_metadata_fields(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, bare=True)
    asyncio.run(make_backend(capture_handler([])).run("task", ctx))
    rendered = next(e for e in ctx.bus.history if e.kind == "prompt_rendered")
    assert rendered.data["payload_mode"] == "bare"
    assert rendered.data["system_message_present"] is False
    assert rendered.data["tools_key_present"] is False
    assert rendered.data["tool_count"] == 0
    request = next(e for e in ctx.bus.history if e.kind == "model_request")
    assert request.data["payload_mode"] == "bare"
    assert request.data["system_message_present"] is False
    assert request.data["tools_key_present"] is False
    assert request.data["tool_count"] == 0


def test_default_event_metadata_fields(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, bare=False, tools=default_registry())
    asyncio.run(make_backend(capture_handler([])).run("task", ctx))
    rendered = next(e for e in ctx.bus.history if e.kind == "prompt_rendered")
    assert rendered.data["payload_mode"] == "default"
    assert rendered.data["system_message_present"] is True
    assert rendered.data["tools_key_present"] is True
    assert rendered.data["tool_count"] == len(default_registry().specs())
    request = next(e for e in ctx.bus.history if e.kind == "model_request")
    assert request.data["payload_mode"] == "default"
    assert request.data["system_message_present"] is True
    assert request.data["tools_key_present"] is True
    assert request.data["tool_count"] == len(default_registry().specs())


def test_bare_audit_events_carry_no_prompt_bodies(tmp_path: Path) -> None:
    """Audit redaction is unchanged: no backend event carries the task text
    or any prompt body (repo hard rule 3 — audit is metadata-only)."""
    ctx = make_ctx(tmp_path, bare=True, memory_context="memory-marker-xyzzy")
    asyncio.run(make_backend(capture_handler([])).run("task-body-marker", ctx))
    for event in ctx.bus.history:
        blob = json.dumps(event.data)
        assert "task-body-marker" not in blob, event.kind
        assert "memory-marker-xyzzy" not in blob, event.kind


# --- interaction matrix --------------------------------------------------------


def test_bare_keeps_clarity_gate(tmp_path: Path) -> None:
    """bare + clarity_gate=True (the default) + an ambiguous task still stops
    with CLARIFICATION_REQUIRED: bare never weakens the clarity gate."""
    settings = Settings(
        model=ModelRef(provider="ollama", model="test-model"),
        permission=PermissionMode.ASK,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
        bare=True,
    )
    backend = MockBackend([{"done": "ok"}])
    session = Session(settings, backend, cwd=tmp_path)
    outcome = asyncio.run(session.run("fix the bug in src/missing.py"))
    assert outcome.code is TerminalCode.CLARIFICATION_REQUIRED


def test_bare_keeps_budget_enforcement(tmp_path: Path) -> None:
    """A bare run still burns budget: the token guard trips on the first round."""
    settings = Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        bare=True,
    )
    ctx = make_ctx(tmp_path, bare=True, settings=settings, budgets=Budgets(max_tokens=5))
    with pytest.raises(BudgetExceeded):
        asyncio.run(make_backend(capture_handler([])).run("task", ctx))


def test_bare_does_not_expand_fallback(tmp_path: Path) -> None:
    """Fallback routing is unchanged: the payload names settings.model, and a
    successful primary means exactly one request — no extra models contacted."""
    settings = Settings(
        model=ModelRef(provider="ollama", model="primary-model", base_url="http://test.local"),
        fallback_models=(
            ModelRef(provider="ollama", model="spare-model", base_url="http://spare.local"),
        ),
        bare=True,
    )
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("http://test.local/")
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion("done"))

    ctx = make_ctx(tmp_path, bare=True, settings=settings)
    outcome = asyncio.run(make_backend(handler).run("task", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert len(captured) == 1
    assert captured[0]["model"] == "primary-model"


def test_bare_skips_prose_tool_call_nudge(tmp_path: Path) -> None:
    """There is no tools API to nudge toward in bare mode: a <tool_call> block
    in the answer is the final answer, not a nudge trigger."""
    prose = '<tool_call>{"name": "read_file", "arguments": {"path": "x.py"}}</tool_call>'
    captured: list[dict] = []
    ctx = make_ctx(tmp_path, bare=True)
    outcome = asyncio.run(
        make_backend(capture_handler(captured, completion(prose))).run("task", ctx)
    )
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == prose
    assert len(captured) == 1  # no nudge round
    assert not [e for e in ctx.bus.history if e.kind == "tool_call_prose"]


def test_bare_contract_json_answer_completes(tmp_path: Path) -> None:
    """The whole point: a contract caller's JSON answer must not false-positive
    the prose-tool-call detector (default mode would nudge on this shape)."""
    contract = '{"name": "ops", "arguments": {"a": 1}, "result": "ok"}'
    ctx = make_ctx(tmp_path, bare=True)
    outcome = asyncio.run(make_backend(capture_handler([], completion(contract))).run("task", ctx))
    assert outcome.code is TerminalCode.COMPLETED
    assert outcome.summary == contract
    assert not [e for e in ctx.bus.history if e.kind == "tool_call_prose"]


# --- mode allowlist -------------------------------------------------------------


def _session_settings(tmp_path: Path, mode: PermissionMode) -> Settings:
    return Settings(
        model=ModelRef(provider="ollama", model="test-model", base_url="http://test.local"),
        permission=mode,
        memory_enabled=False,
        memory_dir=tmp_path / "mem",
        state_dir=tmp_path / "state",
        bare=True,
    )


@pytest.mark.parametrize("mode", [PermissionMode.ASK, PermissionMode.PLAN])
def test_bare_read_only_modes_run(tmp_path: Path, monkeypatch, mode: PermissionMode) -> None:
    """The allowlisted read-only modes (ask, plan) run bare end-to-end."""

    async def no_probe(model, **kwargs) -> None:
        return None

    monkeypatch.setattr("pxx.manifest.probe_model_fingerprint", no_probe)
    captured: list[dict] = []
    backend = make_backend(capture_handler(captured))
    session = Session(_session_settings(tmp_path, mode), backend, cwd=tmp_path)
    outcome = asyncio.run(session.run("say hello"))
    assert outcome.code is TerminalCode.COMPLETED
    assert len(captured) == 1
    assert "tools" not in captured[0]


@pytest.mark.parametrize("mode", [PermissionMode.EDIT, PermissionMode.AUTO])
def test_bare_write_modes_refused_before_any_request(tmp_path: Path, mode: PermissionMode) -> None:
    """bare + a write-capable mode fails loudly BEFORE any model request —
    one check at Session.run start covers CLI, serve, and library callers."""
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        called.append(request)
        return httpx.Response(200, json=completion("done"))

    backend = make_backend(handler)
    session = Session(_session_settings(tmp_path, mode), backend, cwd=tmp_path)
    with pytest.raises(ConfigError, match="bare"):
        asyncio.run(session.run("say hello"))
    assert called == []  # no HTTP request was ever attempted


def test_bare_refusal_message_names_the_mode(tmp_path: Path) -> None:
    session = Session(
        _session_settings(tmp_path, PermissionMode.AUTO), MockBackend([]), cwd=tmp_path
    )
    with pytest.raises(ConfigError, match="auto"):
        asyncio.run(session.run("say hello"))


def test_cli_bare_with_edit_mode_exits_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    """End to end through the CLI: `pxx edit` with `bare = true` surfaces the
    ConfigError like any other config refusal (exit 1, message on stderr)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pxx.toml").write_text("bare = true\n")
    rc = cli.main(["edit", "-m", "change something"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "bare" in err and "edit" in err


# --- CLI observability: the outcome line names the session ---------------------


class _FakeBackend:
    name = "fake"

    async def run(self, task, ctx):  # pragma: no cover
        raise NotImplementedError

    async def cancel(self):  # pragma: no cover
        pass


class _FakeSession:
    """Stands in for pxx.session.Session (mirrors tests/test_cli.py)."""

    outcome = RunOutcome(
        code=TerminalCode.COMPLETED, summary="done", rounds=1, tokens=10, session_id="abc123"
    )

    def __init__(self, settings, backend, *, cwd=None, bus=None):
        self.session_id = "abc123"
        self.bus = bus or EventBus()

    async def run(self, task: str) -> RunOutcome:
        return self.outcome


def test_cli_outcome_line_includes_session_id(tmp_path: Path, monkeypatch, capsys) -> None:
    """`_cmd_run_like`'s outcome line carries session=<id> so a caller can
    locate the run's audit events in state_dir/audit/<date>.jsonl."""
    monkeypatch.setattr(cli, "Session", _FakeSession)
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda cwd=None, overrides=None: Settings(
            memory_dir=tmp_path / "mem", state_dir=tmp_path / "state"
        ),
    )
    monkeypatch.setattr(cli, "_make_backend", lambda name, settings: _FakeBackend())
    monkeypatch.setattr(cli, "_resolve_backend_name", lambda cmd, req, settings: "native")
    assert cli.main(["ask", "-m", "x"]) == 0
    out = capsys.readouterr().out
    assert "session=abc123" in out
