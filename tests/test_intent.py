"""Run-intent marker tests (RUN-INTENT-SPEC-20261012 v3, pxx half).

Contract under test:

- One ``(run_intent, run_intent_provenance)`` pair is computed at session
  construction and recorded IDENTICALLY in ``task.json`` and the
  ``session_start`` event data. ``run_intent`` is one of operational / probe /
  eval / drill / unknown (default unknown when nothing declares);
  ``run_intent_provenance`` is first-party | declared, absent when unknown.
- Provenance is assigned by pxx from the call site: CLI ``--intent`` and the
  ``run_intent`` config key record ``declared``; only pxx-internal call sites
  (the ``Session(run_intent=...)`` kwarg) record ``first-party``. Callers never
  supply provenance: a provenance-claiming CLI flag is a usage error and a
  provenance-claiming config key is a ConfigError (spec A1).
- T7: intent is metadata-only. For every session verb (ask, edit, plan, run,
  chat, loop) a run under each of the five intent values has EQUIVALENT
  EXECUTION SEMANTICS — same gates consulted, same outcome code, same side
  effects — compared semantically, not byte-for-byte. Intent appears only in
  the recorded metadata.

No network, no Ollama, no aider, no git repo required.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pxx import cli
from pxx.backends.mock import MockBackend
from pxx.config import Settings, load_settings
from pxx.errors import ConfigError
from pxx.intent import RunIntent, resolve_run_intent
from pxx.safety import PermissionMode
from pxx.session import Session

INTENT_VALUES = ("operational", "probe", "eval", "drill")
SESSION_VERBS = ("ask", "edit", "plan", "run", "chat", "loop")


# ---------------------------------------------------------------------------
# resolver (pxx.intent.resolve_run_intent)


def test_resolve_internal_call_site_is_first_party():
    assert resolve_run_intent(None, RunIntent.EVAL) == ("eval", "first-party")
    assert resolve_run_intent(None, RunIntent.PROBE) == ("probe", "first-party")


def test_resolve_internal_beats_config_declaration():
    # A pxx-internal call site inside a declared run keeps its own identity:
    # the internal value wins and the provenance is first-party.
    assert resolve_run_intent("drill", RunIntent.EVAL) == ("eval", "first-party")


def test_resolve_config_declaration_is_declared():
    for value in INTENT_VALUES:
        assert resolve_run_intent(value, None) == (value, "declared")


def test_resolve_absent_everywhere_is_unknown_without_provenance():
    assert resolve_run_intent(None, None) == ("unknown", None)


def test_resolve_unrecognized_declaration_reads_unknown():
    # Fail-safe, forward-compatible (spec §1): an unrecognized recorded value
    # reads as unknown — never guessed, never operational.
    assert resolve_run_intent("bogus", None) == ("unknown", None)
    assert resolve_run_intent("unknown", None) == ("unknown", None)
    assert resolve_run_intent("", None) == ("unknown", None)


# ---------------------------------------------------------------------------
# config: run_intent key honored, strict validation, provenance rejected (A1)


def _write_user_config(monkeypatch, tmp_path: Path, text: str) -> None:
    config = tmp_path / "user-config.toml"
    config.write_text(text)
    monkeypatch.setattr("pxx.config._USER_CONFIG", config)


def test_config_run_intent_honored_from_project_toml(tmp_path, monkeypatch):
    (tmp_path / "pxx.toml").write_text('run_intent = "drill"\n')
    monkeypatch.chdir(tmp_path)
    settings = load_settings(tmp_path)
    # Not an exec surface: honored from the TOML layers including repo-local
    # (no PXX_RUN_INTENT env var by design).
    assert settings.run_intent == "drill"


def test_config_run_intent_honored_from_user_toml(tmp_path, monkeypatch):
    _write_user_config(monkeypatch, tmp_path, 'run_intent = "probe"\n')
    settings = load_settings(tmp_path / "cwd")
    assert settings.run_intent == "probe"


def test_config_precedence_project_over_user(tmp_path, monkeypatch):
    _write_user_config(monkeypatch, tmp_path, 'run_intent = "drill"\n')
    (tmp_path / "pxx.toml").write_text('run_intent = "probe"\n')
    settings = load_settings(tmp_path)
    assert settings.run_intent == "probe"


def test_config_precedence_cli_over_project(tmp_path, monkeypatch):
    (tmp_path / "pxx.toml").write_text('run_intent = "probe"\n')
    settings = load_settings(tmp_path, {"run_intent": "eval"})
    assert settings.run_intent == "eval"


def test_config_run_intent_rejects_unrecognized_value(tmp_path):
    (tmp_path / "pxx.toml").write_text('run_intent = "bogus"\n')
    with pytest.raises(ConfigError, match="run_intent"):
        load_settings(tmp_path)


def test_config_run_intent_rejects_non_string(tmp_path):
    (tmp_path / "pxx.toml").write_text("run_intent = 3\n")
    with pytest.raises(ConfigError, match="run_intent"):
        load_settings(tmp_path)


def test_config_run_intent_rejects_explicit_unknown(tmp_path):
    # "unknown" is the absence of a declaration, not a declarable value.
    (tmp_path / "pxx.toml").write_text('run_intent = "unknown"\n')
    with pytest.raises(ConfigError, match="run_intent"):
        load_settings(tmp_path)


def test_config_provenance_key_is_config_error(tmp_path):
    """A1: callers never supply provenance — a config key attempting to set
    it fails closed, from any layer."""
    (tmp_path / "pxx.toml").write_text('run_intent_provenance = "first-party"\n')
    with pytest.raises(ConfigError, match="run_intent_provenance"):
        load_settings(tmp_path)


def test_config_provenance_key_via_overrides_is_config_error(tmp_path):
    """A1: the library/override path cannot smuggle provenance either."""
    with pytest.raises(ConfigError, match="run_intent_provenance"):
        load_settings(tmp_path, {"run_intent_provenance": "first-party"})


# ---------------------------------------------------------------------------
# CLI: --intent on every session verb, strict values, provenance flags (A1)


def test_intent_flag_parsed_on_every_session_verb():
    parser = cli._build_parser()
    for verb in SESSION_VERBS:
        args = parser.parse_args([verb, "-m", "x", "--intent", "eval"])
        assert args.intent == "eval", verb


def test_intent_flag_absent_defaults_to_none():
    parser = cli._build_parser()
    for verb in SESSION_VERBS:
        args = parser.parse_args([verb, "-m", "x"])
        assert args.intent is None, verb


def test_intent_flag_rejects_unknown_value():
    parser = cli._build_parser()
    for verb in SESSION_VERBS:
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args([verb, "-m", "x", "--intent", "bogus"])
        # argparse usage error (same behavior as --provider/--backend choices).
        assert excinfo.value.code == 2, verb


def test_cli_overrides_map_intent_to_run_intent():
    parser = cli._build_parser()
    args = parser.parse_args(["run", "-m", "x", "--intent", "drill"])
    overrides = cli._cli_overrides(args, PermissionMode.AUTO)
    assert overrides["run_intent"] == "drill"


@pytest.mark.parametrize(
    "flag",
    ["--run-intent-provenance", "--intent-provenance", "--provenance"],
)
def test_provenance_claiming_cli_flag_is_usage_error(flag):
    """A1: an intent-adjacent CLI argument claiming provenance never reaches
    a session — it is a hard usage error (64), not a warn-and-ignore flag."""
    for form in (flag, f"{flag}=first-party"):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["ask", "-m", "x", form])
        assert excinfo.value.code == cli.EXIT_USAGE


# ---------------------------------------------------------------------------
# recording: one pair, identical in task.json and session_start


def _settings(tmp_path: Path, **overrides) -> Settings:
    from dataclasses import replace

    base = Settings(
        permission=PermissionMode.AUTO,
        memory_enabled=False,
        safety_net=False,
        state_dir=tmp_path / "state",
    )
    return replace(base, **overrides) if overrides else base


def _run_dirs(state_dir: Path) -> list[Path]:
    return sorted((state_dir / "runs").iterdir())


def _read_task(run_dir: Path) -> dict:
    return json.loads((run_dir / "task.json").read_text())


def _read_events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _session_start(run_dir: Path) -> dict:
    starts = [rec["data"] for rec in _read_events(run_dir) if rec["kind"] == "session_start"]
    assert len(starts) == 1
    return starts[0]


def _intent_pair(mapping: dict) -> tuple[str, str | None]:
    return (mapping["run_intent"], mapping.get("run_intent_provenance"))


def _run_session(tmp_path: Path, settings: Settings, **session_kwargs) -> Path:
    session = Session(
        settings, MockBackend([{"say": "hi"}, {"done": "ok"}]), cwd=tmp_path, **session_kwargs
    )
    outcome = asyncio.run(session.run("do it"))
    assert outcome.code.name == "COMPLETED"
    dirs = _run_dirs(settings.state_dir)
    assert len(dirs) == 1
    return dirs[0]


def test_recorded_unknown_when_nothing_declares(tmp_path):
    run_dir = _run_session(tmp_path, _settings(tmp_path))
    task = _read_task(run_dir)
    start = _session_start(run_dir)
    assert task["run_intent"] == "unknown"
    assert "run_intent_provenance" not in task
    assert start["run_intent"] == "unknown"
    assert "run_intent_provenance" not in start


def test_recorded_declared_from_config(tmp_path):
    for value in INTENT_VALUES:
        settings = _settings(tmp_path / value, run_intent=value)
        (tmp_path / value).mkdir(exist_ok=True)
        run_dir = _run_session(tmp_path / value, settings)
        assert _intent_pair(_read_task(run_dir)) == (value, "declared")
        assert _intent_pair(_session_start(run_dir)) == (value, "declared")


def test_recorded_first_party_from_internal_callsite(tmp_path):
    run_dir = _run_session(tmp_path, _settings(tmp_path), run_intent=RunIntent.EVAL)
    assert _intent_pair(_read_task(run_dir)) == ("eval", "first-party")
    assert _intent_pair(_session_start(run_dir)) == ("eval", "first-party")


def test_recorded_internal_beats_config(tmp_path):
    run_dir = _run_session(
        tmp_path, _settings(tmp_path, run_intent="drill"), run_intent=RunIntent.PROBE
    )
    assert _intent_pair(_read_task(run_dir)) == ("probe", "first-party")
    assert _intent_pair(_session_start(run_dir)) == ("probe", "first-party")


def test_recorded_unrecognized_settings_value_is_unknown(tmp_path):
    # A library-constructed Settings bypassing config validation still reads
    # fail-safe: unrecognized declarations record as unknown (spec §1).
    run_dir = _run_session(tmp_path, _settings(tmp_path, run_intent="bogus"))
    assert _intent_pair(_read_task(run_dir)) == ("unknown", None)
    assert _intent_pair(_session_start(run_dir)) == ("unknown", None)


def test_task_json_and_session_start_agree(tmp_path):
    """Spec §3: one value, written identically; divergence means hand-edit."""
    for kwargs, settings_kwargs in (
        ({}, {}),
        ({}, {"run_intent": "operational"}),
        ({"run_intent": RunIntent.EVAL}, {}),
        ({"run_intent": RunIntent.DRILL}, {"run_intent": "probe"}),
    ):
        case_dir = tmp_path / f"case{len(list(tmp_path.iterdir()))}"
        case_dir.mkdir()
        run_dir = _run_session(case_dir, _settings(case_dir, **settings_kwargs), **kwargs)
        assert _intent_pair(_read_task(run_dir)) == _intent_pair(_session_start(run_dir))


def test_settings_declaration_never_records_first_party(tmp_path):
    """A1: no config/CLI-declared value can produce first-party provenance."""
    for value in INTENT_VALUES:
        case_dir = tmp_path / value
        case_dir.mkdir()
        run_dir = _run_session(case_dir, _settings(case_dir, run_intent=value))
        assert _intent_pair(_read_task(run_dir))[1] == "declared"


def test_no_first_party_callsite_exists_yet():
    """Investigation pin (2026-10-12): the first-party mechanism is the
    internal ``Session(run_intent=...)`` kwarg. Neither the eval harness
    (pxx/eval/harness.py — patch+check arms in tmp git repos, no Session) nor
    the doctor/smoke/connectivity probes (raw httpx, no Session) produce
    session run records, so no pxx module passes the kwarg today. When a
    first-party call site is added (a live eval arm declares RunIntent.EVAL;
    a session-producing probe declares RunIntent.PROBE), update this pin."""
    import pxx

    root = Path(pxx.__file__).parent
    callers = [
        path
        for path in root.rglob("*.py")
        if "run_intent=RunIntent" in path.read_text(encoding="utf-8")
    ]
    assert callers == []


# ---------------------------------------------------------------------------
# T7: equivalent execution semantics across intents, per session verb


#: Event-data keys that legitimately differ between otherwise-identical runs
#: (timestamps, session ids, run ids, identity hashes, wall-clock budget
#: snapshot, environment-derived absolute paths) plus the intent metadata
#: itself, which is asserted separately.
_VOLATILE_EVENT_DATA = {
    "run_id",
    "agent_version_id",
    "project",
    "task_id",
    "repository_fingerprint",
    "starting_commit",
    "budgets",
    "scope",
    "run_intent",
    "run_intent_provenance",
}

#: outcome.json fields compared for execution equivalence (identity,
#: timestamps, and wall-clock durations legitimately differ).
_OUTCOME_SEMANTIC_KEYS = (
    "code",
    "summary",
    "rounds",
    "tokens",
    "diff_lines",
    "files_changed",
    "lint_errors",
    "baseline_failures",
    "introduced_failures",
    "terminal_failures",
    "unparseable_review_count",
)

_SAY_SCRIPT = [{"say": "hi"}, {"done": "ok"}]
_WRITE_SCRIPT = [
    {"tool": "write_file", "args": {"path": "out.txt", "content": "x\n"}},
    {"done": "wrote"},
]
_SCRIPT_BY_VERB = {
    "ask": _SAY_SCRIPT,
    "plan": _SAY_SCRIPT,
    "edit": _WRITE_SCRIPT,
    "run": _WRITE_SCRIPT,
    "chat": _WRITE_SCRIPT,
    "loop": _WRITE_SCRIPT,
}


def _drive_verb(monkeypatch, tmp_path: Path, verb: str, intent: str | None, script=None):
    """Run one session verb end-to-end through the real CLI + real Session +
    MockBackend, in a fresh cwd with its own state dir. Returns
    (exit_code, work_dir, state_dir)."""
    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    (work / "pxx.toml").write_text(
        f'state_dir = "{state}"\nmemory_enabled = false\nsafety_net = false\n'
    )
    monkeypatch.chdir(work)
    script = _SCRIPT_BY_VERB[verb] if script is None else script
    monkeypatch.setattr(cli, "_make_backend", lambda name, settings: MockBackend(script))
    monkeypatch.setattr(cli, "_resolve_backend_name", lambda cmd, req, settings: "native")
    argv = [verb]
    if intent is not None:
        argv += ["--intent", intent]
    if verb == "chat":
        lines = iter(["do it"])

        def _input(prompt: str = "") -> str:
            try:
                return next(lines)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr("builtins.input", _input)
    elif verb == "loop":
        monkeypatch.setattr(
            "pxx.loop._default_factory",
            lambda settings: lambda: MockBackend(script),
        )
        argv += ["-m", "do it"]
    else:
        argv += ["-m", "do it"]
    exit_code = cli.main(argv)
    return exit_code, work, state


def _normalize(value, work: Path):
    """Replace the per-case absolute work path with a stable token: absolute
    tmp paths legitimately differ between runs (they are environment, not
    execution semantics)."""
    if isinstance(value, str):
        return value.replace(str(work), "<work>")
    if isinstance(value, dict):
        return {k: _normalize(v, work) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v, work) for v in value]
    return value


def _events_semantic(run_dir: Path, work: Path) -> list:
    out = []
    for rec in _read_events(run_dir):
        data = {k: v for k, v in rec["data"].items() if k not in _VOLATILE_EVENT_DATA}
        out.append((rec["kind"], _normalize(data, work)))
    return out


def _outcome_semantic(run_dir: Path) -> dict:
    outcome = json.loads((run_dir / "outcome.json").read_text())
    return {key: outcome[key] for key in _OUTCOME_SEMANTIC_KEYS}


def _worktree(root: Path) -> dict[str, bytes]:
    # pxx.toml is excluded: it is the test harness's own identical declaration
    # modulo the per-case absolute state_dir path, not a run side effect.
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "pxx.toml"
    }


@pytest.mark.parametrize("verb", SESSION_VERBS)
def test_t7_equivalent_execution_semantics_across_intents(verb, monkeypatch, tmp_path):
    """For one verb, a run under each of the five intent values (operational,
    probe, eval, drill, and undeclared/unknown) consults the same gates,
    produces the same outcome code and side effects, and differs ONLY in the
    recorded intent metadata."""
    signatures = []
    for intent in (None, *INTENT_VALUES):
        case_dir = tmp_path / (intent or "undeclared")
        case_dir.mkdir()
        exit_code, work, state = _drive_verb(monkeypatch, case_dir, verb, intent)
        # The loop verb writes a loop-level run record (mode: "loop") beside
        # the per-round session run dir; both carry the marker, and the
        # semantic comparison runs on the session dir.
        run_dirs = _run_dirs(state)
        session_dirs = [d for d in run_dirs if "task_id" in _read_task(d)]
        assert len(session_dirs) == 1, f"{verb}/{intent}: expected one session run dir"
        run_dir = session_dirs[0]

        # The recorded marker: identical in task.json and session_start.
        expected = ("unknown", None) if intent is None else (intent, "declared")
        assert _intent_pair(_read_task(run_dir)) == expected, f"{verb}/{intent}"
        assert _intent_pair(_session_start(run_dir)) == expected, f"{verb}/{intent}"
        for other in run_dirs:
            assert _intent_pair(_read_task(other)) == expected, f"{verb}/{intent}"

        signatures.append(
            (
                exit_code,
                _events_semantic(run_dir, work),
                _outcome_semantic(run_dir),
                _worktree(work),
            )
        )

    baseline = signatures[0]
    for intent, signature in zip((None, *INTENT_VALUES), signatures, strict=True):
        assert signature[0] == baseline[0], f"{verb}/{intent}: exit code differs"
        assert signature[1] == baseline[1], f"{verb}/{intent}: event semantics differ"
        assert signature[2] == baseline[2], f"{verb}/{intent}: outcome differs"
        assert signature[3] == baseline[3], f"{verb}/{intent}: side effects differ"


def test_t7_gate_denial_identical_across_intents(monkeypatch, tmp_path):
    """A gate that FIRES fires identically whatever the intent: a write
    attempted in read-only ask mode is denied the same way under every
    intent value — a run cannot alter its gating by declaring an intent."""
    signatures = []
    for intent in (None, *INTENT_VALUES):
        case_dir = tmp_path / (intent or "undeclared")
        case_dir.mkdir()
        exit_code, work, state = _drive_verb(
            monkeypatch, case_dir, "ask", intent, script=_WRITE_SCRIPT
        )
        run_dir = _run_dirs(state)[0]
        expected = ("unknown", None) if intent is None else (intent, "declared")
        assert _intent_pair(_read_task(run_dir)) == expected
        signatures.append((exit_code, _outcome_semantic(run_dir), _worktree(work)))
    baseline = signatures[0]
    assert baseline[1]["code"] != "COMPLETED"  # the gate really fired
    for signature in signatures:
        assert signature == baseline


def test_config_declared_intent_flows_to_record(monkeypatch, tmp_path):
    """Config-layer declaration records declared intent; CLI still wins."""
    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    (work / "pxx.toml").write_text(
        f'state_dir = "{state}"\nmemory_enabled = false\nsafety_net = false\nrun_intent = "probe"\n'
    )
    monkeypatch.chdir(work)
    monkeypatch.setattr(cli, "_make_backend", lambda name, settings: MockBackend(_SAY_SCRIPT))
    monkeypatch.setattr(cli, "_resolve_backend_name", lambda cmd, req, settings: "native")

    assert cli.main(["ask", "-m", "do it"]) == 0
    assert cli.main(["ask", "-m", "do it", "--intent", "drill"]) == 0
    # Two runs in the same state dir: run ids are timestamp+random, so match
    # by recorded intent rather than position.
    recorded = {_intent_pair(_read_task(d)) for d in _run_dirs(state)}
    assert recorded == {("probe", "declared"), ("drill", "declared")}
    for d in _run_dirs(state):
        assert _intent_pair(_session_start(d)) == _intent_pair(_read_task(d))
