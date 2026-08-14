"""Mechanism proof for the Context Paging Runtime v0 prototype.

Deterministic + offline: a ScriptedModel and a fake host test-runner stand in for the live 4B
model and a real test command, so these run in CI with no hardware and no network. The four
NEGATIVE CONTROLS each force the bad case and assert the mechanism catches it — a mechanism
that cannot fail is not proven. The live 8 GB Neo receipt (real qwen3:4b) is earned separately
with ``prototypes/context_paging/run_neo.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prototypes.context_paging import (
    CapsuleBuilder,
    CapsuleOverflow,
    Ledger,
    PageStore,
    Runtime,
    page_hash,
)
from prototypes.context_paging.artifacts import ArtifactStore, scrub_secrets
from prototypes.context_paging.capsule import approx_token_counter
from prototypes.context_paging.model import ScriptedModel
from prototypes.context_paging.pages import Page

_BUG = "def add(a, b):\n    return a - b  # BUG\n"
_FIX = "def add(a, b):\n    return a + b\n"


def _make_repo(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "bug.py").write_text(_BUG)
    return tmp_path, page_hash(_BUG.encode())


def _host_test(root: Path):
    """A host verifier the model cannot fake: passes iff bug.py actually contains the fix."""

    def run(argv, cwd, timeout):
        text = (root / "bug.py").read_text()
        return (0, "1 passed") if "return a + b" in text else (1, "1 failed: add(2,3)!=5")

    return run


def _runtime(root: Path, state_dir: Path, actions: list[dict], **kw) -> Runtime:
    ledger = Ledger(objective="fix add()", acceptance_cmd=["pytest", "-q"], target_path="bug.py")
    return Runtime(
        root=root,
        state_dir=state_dir,
        model=ScriptedModel(actions),
        ledger=ledger,
        test_runner=_host_test(root),
        **kw,
    )


# --------------------------------------------------------------------------- happy path
def test_happy_path_completes_with_host_verification(tmp_path):
    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    rt = _runtime(
        root,
        state,
        [
            {
                "type": "PATCH",
                "path": "bug.py",
                "expected_sha": sha0,
                "old_string": "return a - b  # BUG",
                "new_string": "return a + b",
            },
            {"type": "RUN_TEST"},
            {"type": "COMPLETE"},
        ],
    )
    terminal, receipt = rt.run()
    assert terminal.code == "COMPLETED"
    assert (root / "bug.py").read_text() == _FIX  # applied exactly once
    assert receipt.verification == {"command": "pytest -q", "host_run": True, "passed": True}
    assert receipt.terminal == "COMPLETED"
    # every capsule carried the target source and stayed under cap
    assert receipt.capsules and all(c["under_cap"] for c in receipt.capsules)
    assert rt.ledger.revision == 1  # one committed mutation


def test_complete_is_rejected_without_a_passing_host_test(tmp_path):
    # COMPLETE is a request; the host records COMPLETED only after its OWN test passes.
    root, _ = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    rt = _runtime(
        root,
        state,
        [
            {"type": "COMPLETE"},  # premature: no RUN_TEST yet -> rejected, not terminal
            {"type": "BLOCKED", "reason": "gave_up"},
        ],
    )
    terminal, receipt = rt.run()
    assert terminal.code == "BLOCKED"  # the premature COMPLETE did NOT finish the run
    assert receipt.actions[0] == {"seq": 1, "type": "Complete"}  # recorded, but not terminal
    assert receipt.verification["passed"] is False


# ------------------------------------------------- negative control 1: stale-sha rejected
def test_nc1_stale_sha_patch_is_rejected_then_fresh_succeeds(tmp_path):
    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    stale = "0" * 64
    rt = _runtime(
        root,
        state,
        [
            {
                "type": "PATCH",
                "path": "bug.py",
                "expected_sha": stale,
                "old_string": "return a - b  # BUG",
                "new_string": "return a + b",
            },  # REJECTED
            {
                "type": "PATCH",
                "path": "bug.py",
                "expected_sha": sha0,
                "old_string": "return a - b  # BUG",
                "new_string": "return a + b",
            },  # applied
            {"type": "RUN_TEST"},
            {"type": "COMPLETE"},
        ],
    )
    terminal, receipt = rt.run()
    assert receipt.negative_controls["stale_sha_rejected"] is True
    assert terminal.code == "COMPLETED"
    assert rt.ledger.revision == 1  # the stale patch applied nothing; only the fresh one did


def test_nc1_executor_never_applies_a_stale_patch(tmp_path):
    # focused: a stale PATCH leaves the file byte-for-byte unchanged
    from prototypes.context_paging.actions import Patch
    from prototypes.context_paging.executor import Executor

    root, _ = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    ledger = Ledger(objective="x", acceptance_cmd=["true"], target_path="bug.py")
    ex = Executor(
        root=root,
        state_dir=state,
        pages=PageStore(root),
        ledger=ledger,
        artifacts=ArtifactStore(state),
    )
    res = ex.execute(
        Patch(
            path="bug.py",
            expected_sha="deadbeef",
            old_string="return a - b  # BUG",
            new_string="return a + b",
        )
    )
    assert res.detail["rejected"] == "stale_sha"
    assert (root / "bug.py").read_text() == _BUG  # untouched


# ---------------------------------------- negative control 2: kill-restart resume, no replay
def test_nc2_restart_resumes_from_ledger_no_replay_no_double_apply(tmp_path):
    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    pages = PageStore(root)

    # --- simulate a CRASH after the source write but before the ledger bump ---
    from prototypes.context_paging.executor import Executor

    ledger = Ledger(objective="fix add()", acceptance_cmd=["pytest", "-q"], target_path="bug.py")
    ledger.save(state)
    ex = Executor(
        root=root, state_dir=state, pages=pages, ledger=ledger, artifacts=ArtifactStore(state)
    )
    planned = page_hash(_FIX.encode())
    ex._write_inflight(
        {
            "key": "k",
            "path": "bug.py",
            "expected_sha": sha0,
            "planned_sha": planned,
            "pre_revision": 0,
        }
    )
    pages.write("bug.py", _FIX)  # applied to disk...
    # ...process dies here: no ledger bump, inflight record still present.
    assert (state / "inflight.json").is_file()
    assert ledger.revision == 0

    # --- fresh process: a NEW runtime with the SAME state_dir. The model script has NO patch
    #     action — if resume required replaying the patch, this run could not finish. ---
    reloaded = Ledger.load(state)
    rt = Runtime(
        root=root,
        state_dir=state,
        model=ScriptedModel([{"type": "RUN_TEST"}, {"type": "COMPLETE"}]),
        ledger=reloaded,
        test_runner=_host_test(root),
    )
    terminal, receipt = rt.run()

    assert receipt.negative_controls["restart_resumed_no_replay"] is True
    assert terminal.code == "COMPLETED"
    assert (root / "bug.py").read_text() == _FIX  # applied exactly ONCE (not doubled)
    assert rt.ledger.revision == 1  # the crashed bump was completed exactly once
    assert not (state / "inflight.json").exists()  # record cleared


def test_nc2_reconcile_fails_closed_on_ambiguous_state(tmp_path):
    # if the file matches NEITHER expected nor planned, reconcile refuses to guess
    from prototypes.context_paging.executor import Executor

    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    ledger = Ledger(objective="x", acceptance_cmd=["true"], target_path="bug.py")
    ex = Executor(
        root=root,
        state_dir=state,
        pages=PageStore(root),
        ledger=ledger,
        artifacts=ArtifactStore(state),
    )
    ex._write_inflight(
        {
            "key": "k",
            "path": "bug.py",
            "expected_sha": sha0,
            "planned_sha": "1" * 64,
            "pre_revision": 0,
        }
    )
    (root / "bug.py").write_text("def add(a, b):\n    return a * b  # third party edit\n")
    with pytest.raises(RuntimeError, match="ambiguous"):
        ex.reconcile()


# ------------------------------------------ negative control 3: BLOCKED is never COMPLETED
def test_nc3_blocked_is_never_recorded_as_completed(tmp_path):
    root, _ = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    rt = _runtime(root, state, [{"type": "BLOCKED", "reason": "unsolvable"}])
    terminal, receipt = rt.run()
    assert terminal.code == "BLOCKED" and terminal.code != "COMPLETED"
    assert receipt.terminal == "BLOCKED:unsolvable"
    assert receipt.negative_controls["blocked_not_completed"] is True
    assert receipt.verification["passed"] is False
    assert (root / "bug.py").read_text() == _BUG  # nothing was changed


# ----------------------- negative control 4: eviction never drops target; overflow -> BLOCKED
def test_nc4a_over_budget_eviction_keeps_target_drops_deps(tmp_path):
    target = Page(path="bug.py", text=_BUG, sha=page_hash(_BUG.encode()))
    deps = [Page(path=f"dep{i}.py", text="x = 1\n" * 60, sha="s") for i in range(8)]
    history = ["h" * 400 for _ in range(8)]
    # cap admits the floor + a little, forcing most deps/history to evict
    floor = sum(
        approx_token_counter(t)
        for t in ["kernel", "contract", f"# path: bug.py\n# sha256: x\n{_BUG}", "tools"]
    )
    builder = CapsuleBuilder(cap_tokens=floor + 120, count_tokens=approx_token_counter)
    cap = builder.build(
        kernel="kernel",
        contract="contract",
        target=target,
        tools="tools",
        dependency_pages=deps,
        history=history,
    )
    assert "target:bug.py" in cap.included  # target NEVER evicted
    assert cap.evicted  # something was evicted under pressure
    assert cap.under_cap


def test_nc4b_target_too_big_overflows_to_preflight_blocked(tmp_path):
    root, _ = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    # a cap so small even the floor cannot fit -> the builder must BLOCK, never evict the target
    rt = _runtime(root, state, [{"type": "RUN_TEST"}], cap_tokens=3)
    terminal, receipt = rt.run()
    assert terminal.code == "BLOCKED"
    assert terminal.reason == "target_source_exceeds_capsule"
    assert (
        receipt.negative_controls["overflow_never_dropped_target"] is True
    )  # BLOCKED, not dropped


def test_nc4b_builder_raises_overflow_directly(tmp_path):
    target = Page(path="big.py", text="z = 0\n" * 5000, sha="s")
    builder = CapsuleBuilder(cap_tokens=50, count_tokens=approx_token_counter)
    with pytest.raises(CapsuleOverflow):
        builder.build(kernel="k", contract="c", target=target, tools="t")


# ----------------------------------------------------------------- supporting invariants
def test_page_hash_is_over_raw_bytes_and_deterministic():
    assert page_hash(b"abc") == page_hash(b"abc")
    assert page_hash(b"abc") != page_hash(b"abd")


def test_patch_requires_a_unique_anchor(tmp_path):
    from prototypes.context_paging.actions import Patch
    from prototypes.context_paging.executor import Executor

    root = tmp_path
    (root / "dup.py").write_text("x=1\nx=1\n")
    state = tmp_path / ".pxx-paging"
    ledger = Ledger(objective="x", acceptance_cmd=["true"], target_path="dup.py")
    ex = Executor(
        root=root,
        state_dir=state,
        pages=PageStore(root),
        ledger=ledger,
        artifacts=ArtifactStore(state),
    )
    res = ex.execute(
        Patch(
            path="dup.py", expected_sha=page_hash(b"x=1\nx=1\n"), old_string="x=1", new_string="x=2"
        )
    )
    assert res.detail["rejected"] == "non_unique_match"  # ambiguous -> refused, no fuzz


def test_artifact_summary_is_secret_scrubbed(tmp_path):
    state = tmp_path / ".pxx-paging"
    store = ArtifactStore(state)
    leak = (
        "Traceback\nAUTHORIZATION: Bearer sk-abcdefghijkl123456\npassword=hunter2\nhttps://u:pw@h/x"
    )
    ref = store.put("runtest", leak)
    assert "hunter2" not in ref.summary and "sk-abcdefghijkl123456" not in ref.summary
    assert "u:pw@h" not in ref.summary
    # the on-disk artifact is scrubbed too (not just the summary)
    assert "hunter2" not in (store.get(ref.ref_id) or "")


def test_scrub_secrets_is_deterministic_and_broad():
    assert scrub_secrets("api_key=SECRETVALUE") == "api_key=***"
    assert scrub_secrets("nothing to see") == "nothing to see"


def test_ledger_round_trips_on_disk(tmp_path):
    state = tmp_path / ".pxx-paging"
    led = Ledger(objective="o", acceptance_cmd=["pytest"], target_path="a.py", revision=3)
    led.save(state)
    back = Ledger.load(state)
    assert back.revision == 3 and back.target_path == "a.py" and back.acceptance_cmd == ["pytest"]


# ------------------------------------------------ performance instrumentation (A/B/C receipts)
def test_deterministic_run_leaves_performance_empty(tmp_path):
    # the offline mechanism must NEVER populate performance (it is a hardware measurement) — so
    # the receipt stays reproducible byte-for-byte across machines.
    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    rt = _runtime(
        root,
        state,
        [
            {
                "type": "PATCH",
                "path": "bug.py",
                "expected_sha": sha0,
                "old_string": "return a - b  # BUG",
                "new_string": "return a + b",
            },
            {"type": "RUN_TEST"},
            {"type": "COMPLETE"},
        ],
    )
    _, receipt = rt.run()
    assert receipt.performance == {}


def test_build_performance_aggregates_stats():
    from prototypes.context_paging.run_neo import build_performance

    stats = [
        {"latency_s": 2.0, "ttft_s": 1.5, "prompt_tokens": 5000, "completion_tokens": 50},
        {"latency_s": 4.0, "ttft_s": 3.5, "prompt_tokens": 5200, "completion_tokens": 40},
    ]
    p = build_performance(
        stats, wall_clock_s=7.0, swap_delta_mb=12.5, transport="lan", streamed=True
    )
    assert p["model_calls"] == 2
    assert p["model_time_s"] == 6.0
    assert p["total_prompt_tokens"] == 10200 and p["total_completion_tokens"] == 90
    assert p["prompt_tokens_per_s"] == round(10200 / 6.0, 2)  # prefill-inclusive throughput
    assert p["ttft_median_s"] == 2.5  # median(1.5, 3.5)
    assert p["swap_used_delta_mb"] == 12.5 and p["transport"] == "lan" and p["streamed"] is True


def test_build_performance_handles_endpoints_without_usage():
    # an endpoint that doesn't report token usage -> totals/rates are None, never fabricated
    from prototypes.context_paging.run_neo import build_performance

    stats = [{"latency_s": 1.0, "ttft_s": None, "prompt_tokens": None, "completion_tokens": None}]
    p = build_performance(
        stats, wall_clock_s=1.2, swap_delta_mb=None, transport="local", streamed=False
    )
    assert p["total_prompt_tokens"] is None and p["prompt_tokens_per_s"] is None
    assert p["ttft_median_s"] is None and p["swap_used_delta_mb"] is None
    assert p["model_calls"] == 1 and p["wall_clock_s"] == 1.2


# ------------------------------------------------ review-hardening negative controls (PR #69)
def test_patch_invalidates_a_prior_passing_verification(tmp_path):
    # CRITICAL: a passing RUN_TEST is only valid for the source it ran on. After a green test, a
    # further PATCH must reset verified -> a COMPLETE cannot ride stale verification.
    from prototypes.context_paging.actions import Patch
    from prototypes.context_paging.executor import Executor

    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    ledger = Ledger(objective="x", acceptance_cmd=["true"], target_path="bug.py", verified=True)
    ex = Executor(
        root=root,
        state_dir=state,
        pages=PageStore(root),
        ledger=ledger,
        artifacts=ArtifactStore(state),
    )
    res = ex.execute(
        Patch(
            path="bug.py",
            expected_sha=sha0,
            old_string="return a - b  # BUG",
            new_string="return a + b",
        )
    )
    assert res.detail["applied"] is True
    assert ledger.verified is False  # a source change dropped the stale verification
    assert Ledger.load(state).verified is False  # and it was persisted, not in-memory only


def test_stale_verification_cannot_complete_end_to_end(tmp_path):
    # end-to-end: PATCH -> RUN_TEST(pass) -> PATCH again -> COMPLETE must be REJECTED (verified
    # was invalidated by the 2nd patch), then RUN_TEST -> COMPLETE succeeds.
    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    fixed_sha = page_hash(_FIX.encode())
    rt = _runtime(
        root,
        state,
        [
            {
                "type": "PATCH",
                "path": "bug.py",
                "expected_sha": sha0,
                "old_string": "return a - b  # BUG",
                "new_string": "return a + b",
            },
            {"type": "RUN_TEST"},  # passes
            {
                "type": "PATCH",
                "path": "bug.py",
                "expected_sha": fixed_sha,
                "old_string": "return a + b",
                "new_string": "return a + b  # touched",
            },
            {"type": "COMPLETE"},  # must be REJECTED: verified was reset by the 2nd patch
            {"type": "RUN_TEST"},  # passes again (still returns a+b)
            {"type": "COMPLETE"},  # now allowed
        ],
    )
    terminal, receipt = rt.run()
    assert terminal.code == "COMPLETED"
    completes = [a for a in receipt.actions if a["type"] == "Complete"]
    assert len(completes) == 2  # the first COMPLETE was rejected, not terminal


def test_reconcile_invalidates_stale_verification_after_crash(tmp_path):
    # CRITICAL fault injection: crash AFTER the source write but BEFORE verified=False was
    # persisted, with verified=True on disk. reconcile must clear verified (else COMPLETE could
    # ride a test that ran on the OLD source).
    from prototypes.context_paging.executor import Executor

    root, sha0 = _make_repo(tmp_path)
    state = tmp_path / ".pxx-paging"
    pages = PageStore(root)
    ledger = Ledger(objective="x", acceptance_cmd=["true"], target_path="bug.py", verified=True)
    ledger.save(state)
    ex = Executor(
        root=root, state_dir=state, pages=pages, ledger=ledger, artifacts=ArtifactStore(state)
    )
    planned = page_hash(_FIX.encode())
    ex._write_inflight(
        {
            "key": "k",
            "path": "bug.py",
            "expected_sha": sha0,
            "planned_sha": planned,
            "pre_revision": 0,
        }
    )
    pages.write("bug.py", _FIX)  # source applied; ledger on disk still has verified=True, rev 0

    # fresh process reconciles
    reloaded = Ledger.load(state)
    assert reloaded.verified is True  # the stale value is on disk before reconcile
    ex2 = Executor(
        root=root, state_dir=state, pages=pages, ledger=reloaded, artifacts=ArtifactStore(state)
    )
    assert ex2.reconcile() == "applied"
    assert reloaded.verified is False and reloaded.revision == 1
    on_disk = Ledger.load(state)  # BOTH the cleared verification and the bumped revision persisted
    assert on_disk.verified is False and on_disk.revision == 1  # exactly-once recovery is durable


def test_artifact_get_refuses_path_traversal(tmp_path):
    # CRITICAL: ref_id comes from the model; get() must never read outside the artifact dir.
    state = tmp_path / ".pxx-paging"
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    store = ArtifactStore(state)
    assert store.get("../../secret") is None
    assert store.get("../secret.txt") is None
    assert store.get("/etc/hosts") is None
    ref = store.put("runtest", "ordinary log")  # a legit ref still works
    assert store.get(ref.ref_id) == "ordinary log"


def test_authorization_header_is_fully_scrubbed():
    # a deliberately realistic JWT fixture to exercise the scrubber; suppress the governance
    # secret-scanner on this one line (it is a test fixture, not a real credential).
    jwt = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123signature"  # pxx: allow jwt
    out = scrub_secrets(jwt)
    assert "eyJhbGciOiJIUzI1NiJ9" not in out and "signature" not in out


def test_capsule_under_cap_holds_for_a_nonadditive_tokenizer(tmp_path):
    # a pathological counter where count(a+b) > count(a)+count(b): admission must still measure
    # the ACTUAL joined prompt, so under_cap is a hard guarantee (not a per-section-sum estimate).
    def spiky(text: str) -> int:
        return len(text) + text.count("\n") * 100  # newlines cost a lot when joined

    target = Page(path="bug.py", text=_BUG, sha=page_hash(_BUG.encode()))
    deps = [Page(path=f"d{i}.py", text="a=1\n" * 20, sha="s") for i in range(6)]
    floor_only = CapsuleBuilder(cap_tokens=10_000_000, count_tokens=spiky).build(
        kernel="k", contract="c", target=target, tools="t"
    )
    builder = CapsuleBuilder(cap_tokens=floor_only.input_tokens + 50, count_tokens=spiky)
    cap = builder.build(
        kernel="k",
        contract="c",
        target=target,
        tools="t",
        dependency_pages=deps,
        history=["h\n" * 10 for _ in range(6)],
    )
    assert cap.under_cap  # measured on the real joined prompt, so this cannot be violated
    assert "target:bug.py" in cap.included


def test_non_utf8_target_blocks_fail_closed(tmp_path):
    root = tmp_path
    (root / "bin.py").write_bytes(b"\xff\xfe not valid utf-8 \x00")
    state = tmp_path / ".pxx-paging"
    ledger = Ledger(objective="x", acceptance_cmd=["true"], target_path="bin.py")
    rt = Runtime(
        root=root,
        state_dir=state,
        model=ScriptedModel([{"type": "RUN_TEST"}]),
        ledger=ledger,
        test_runner=_host_test(root),
    )
    terminal, _ = rt.run()
    assert terminal.code == "BLOCKED" and terminal.reason == "target_not_utf8"
