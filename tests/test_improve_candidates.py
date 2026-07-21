"""Phase 16 tests: declarative candidates, tighten-only budgets, integrity."""

from __future__ import annotations

import dataclasses
import json

import pytest

from pxx.errors import CandidateInvalid
from pxx.improve import candidates
from pxx.improve.candidates import (
    Candidate,
    CandidateClass,
    compute_content_hash,
    content_path,
    make_candidate,
    read_candidate,
    validate_candidate,
    write_candidate,
)
from pxx.protected_paths import is_protected_path
from pxx.safety import Budgets

BASELINE = Budgets()


def settings_candidate(target="review_mode", value="advisory", **kw):
    return make_candidate(
        kw.pop("candidate_id", "c-001"),
        CandidateClass.SETTINGS,
        target,
        value,
        kw.pop("rationale", "correlated with fewer blocked runs"),
        kw.pop("evidence", ("r1", "r2", "r3")),
        **kw,
    )


def content_candidate(target="pxx/prompts/native_system.md", value="# new prompt\ntext", **kw):
    return make_candidate(
        kw.pop("candidate_id", "c-002"),
        CandidateClass.CONTENT,
        target,
        value,
        kw.pop("rationale", "round-cap failures correlate with wording"),
        kw.pop("evidence", ("r4",)),
        **kw,
    )


# --- happy paths --------------------------------------------------------------


def test_valid_settings_candidate_validates():
    validate_candidate(settings_candidate())


def test_valid_content_candidate_validates():
    validate_candidate(content_candidate())


def test_model_and_fallbacks_and_memory_limit_targets_allowed():
    validate_candidate(settings_candidate(target="model", value="qwen2.5-coder:14b"))
    validate_candidate(
        settings_candidate(target="model", value={"provider": "ollama", "model": "m"})
    )
    validate_candidate(settings_candidate(target="fallback_models", value=["a", {"model": "b"}]))
    validate_candidate(settings_candidate(target="memory_retrieval_limit", value=3))


def test_budget_tightening_allowed_via_baseline_comparison():
    baseline = dataclasses.replace(BASELINE, max_rounds=25)
    c = settings_candidate(
        target="budgets",
        value={"max_rounds": 10},
        baseline_budgets=baseline,
    )
    validate_candidate(c)


def test_budget_equal_to_baseline_allowed():
    c = settings_candidate(
        target="budgets",
        value={"max_tokens": BASELINE.max_tokens},
        baseline_budgets=dataclasses.asdict(BASELINE),
    )
    validate_candidate(c)


# --- rejection classes --------------------------------------------------------


def test_non_allowlisted_target_rejected():
    with pytest.raises(CandidateInvalid, match="non-allowlisted"):
        validate_candidate(settings_candidate(target="permission", value="auto"))
    with pytest.raises(CandidateInvalid, match="non-allowlisted"):
        validate_candidate(settings_candidate(target="scope", value=["."]))
    with pytest.raises(CandidateInvalid, match="non-allowlisted"):
        validate_candidate(settings_candidate(target="dependencies", value=["numpy"]))


def test_protected_paths_rejected():
    for path in (
        "pxx/safety.py",
        "pxx/eval/harness.py",
        "pxx/improve/promotion.py",
        "evals/micro/x.toml",
        ".github/workflows/ci.yml",
        "docs/TRUST_BOUNDARY.md",
    ):
        assert is_protected_path(path)
        with pytest.raises(CandidateInvalid, match="protected"):
            validate_candidate(content_candidate(target=path))


def test_unclassifiable_paths_fail_closed():
    for bad in ("", "  ", "/abs/path.md", "~/x.md", "../escape.md", "pxx/../escape.md"):
        with pytest.raises(CandidateInvalid):
            validate_candidate(content_candidate(target=bad))


def test_non_prompt_content_target_rejected():
    with pytest.raises(CandidateInvalid, match="pxx/prompts"):
        validate_candidate(content_candidate(target="pxx/prompts/evil.py"))
    with pytest.raises(CandidateInvalid, match="pxx/prompts"):
        validate_candidate(content_candidate(target="src/notes.md"))


def test_budget_increase_rejected_fieldwise():
    with pytest.raises(CandidateInvalid, match="tighten-only"):
        validate_candidate(
            settings_candidate(
                target="budgets",
                value={"max_rounds": BASELINE.max_rounds + 1},
                baseline_budgets=dataclasses.asdict(BASELINE),
            )
        )
    with pytest.raises(CandidateInvalid, match="tighten-only"):
        validate_candidate(
            settings_candidate(
                target="budgets",
                value={"max_cost_usd": BASELINE.max_cost_usd + 0.01},
                baseline_budgets=BASELINE,
            )
        )


def test_budget_without_baseline_reference_rejected():
    with pytest.raises(CandidateInvalid, match="baseline"):
        validate_candidate(settings_candidate(target="budgets", value={"max_rounds": 5}))


def test_multi_field_budget_candidate_rejected_one_variable_only():
    with pytest.raises(CandidateInvalid, match="one behavioral variable"):
        validate_candidate(
            settings_candidate(
                target="budgets",
                value={"max_rounds": 5, "max_tokens": 100},
                baseline_budgets=BASELINE,
            )
        )


def test_unknown_budget_field_rejected():
    with pytest.raises(CandidateInvalid, match="unknown budget field"):
        validate_candidate(
            settings_candidate(
                target="budgets",
                value={"max_vibes": 1},
                baseline_budgets=BASELINE,
            )
        )


def test_missing_rationale_rejected():
    with pytest.raises(CandidateInvalid, match="rationale"):
        validate_candidate(settings_candidate(rationale="   "))


def test_missing_evidence_rejected():
    with pytest.raises(CandidateInvalid, match="evidence"):
        validate_candidate(settings_candidate(evidence=()))


def test_content_hash_mismatch_rejected():
    c = settings_candidate()
    tampered = dataclasses.replace(c, value="blocking")
    with pytest.raises(CandidateInvalid, match="content_hash"):
        validate_candidate(tampered)


def test_hash_covers_class_target_and_value():
    h = compute_content_hash("settings", "review_mode", "advisory")
    assert h != compute_content_hash("settings", "review_mode", "blocking")
    assert h != compute_content_hash("settings", "model", "advisory")
    assert h != compute_content_hash("content", "review_mode", "advisory")


def test_unsafe_candidate_id_rejected():
    with pytest.raises(CandidateInvalid, match="id"):
        validate_candidate(settings_candidate(candidate_id="../evil"))
    with pytest.raises(CandidateInvalid, match="id"):
        validate_candidate(settings_candidate(candidate_id="a/b"))


def test_invalid_review_mode_rejected():
    with pytest.raises(CandidateInvalid, match="review_mode"):
        validate_candidate(settings_candidate(target="review_mode", value="yolo"))


def test_invalid_memory_retrieval_limit_rejected():
    for bad in (0, -1, "3", True, None):
        with pytest.raises(CandidateInvalid):
            validate_candidate(settings_candidate(target="memory_retrieval_limit", value=bad))


def test_unknown_candidate_class_rejected():
    c = make_candidate("c-x", "settings", "review_mode", "advisory", "r", ("r1",))
    rogue = dataclasses.replace(
        c,
        change_class="evaluator",
        content_hash=compute_content_hash("evaluator", "review_mode", "advisory"),
    )
    with pytest.raises(CandidateInvalid, match="unknown candidate class"):
        validate_candidate(rogue)


# --- persistence + validate/write path equivalence -----------------------------


def test_write_candidate_persists_json(tmp_path):
    c = settings_candidate()
    path = write_candidate(c, tmp_path / ".pxx")
    assert path == tmp_path / ".pxx" / "candidates" / "c-001" / "candidate.json"
    data = json.loads(path.read_text())
    assert data["id"] == "c-001"
    assert data["class"] == "settings"
    assert data["evidence"] == ["r1", "r2", "r3"]
    assert read_candidate(path.parent).id == "c-001"


def test_write_candidate_validates_first(tmp_path):
    bad = settings_candidate(target="permission", value="auto")
    with pytest.raises(CandidateInvalid):
        write_candidate(bad, tmp_path)
    assert not (tmp_path / "candidates").exists() or not list(
        (tmp_path / "candidates").rglob("candidate.json")
    )


def test_candidate_immutable_once_written(tmp_path):
    c = settings_candidate()
    write_candidate(c, tmp_path)
    with pytest.raises(CandidateInvalid, match="immutable"):
        write_candidate(c, tmp_path)


def test_validate_and_write_use_the_same_derived_content_path(tmp_path, monkeypatch):
    """Path equivalence: content_path is derived ONCE; validation and
    persistence must observe the exact same value."""
    seen: list[str] = []
    real = candidates.content_path

    def spy(candidate: Candidate) -> str:
        derived = real(candidate)
        seen.append(derived)
        return derived

    monkeypatch.setattr(candidates, "content_path", spy)
    c = content_candidate(target="./pxx/prompts/native_system.md")
    validate_candidate(c)
    path = write_candidate(c, tmp_path)
    assert len(seen) >= 2
    assert len(set(seen)) == 1, f"validate/write derived different paths: {seen}"
    persisted = json.loads(path.read_text())
    assert persisted["target"] == seen[0] == "pxx/prompts/native_system.md"
    assert persisted["target"] == content_path(c)


def test_content_write_rejects_when_derived_path_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(candidates, "content_path", lambda c: "pxx/safety.py")
    with pytest.raises(CandidateInvalid, match="protected"):
        write_candidate(content_candidate(), tmp_path)


# --- B4.3: broader candidate change classes -----------------------------------------


def _content_candidate(cid, cls, target, value):
    from pxx.improve.candidates import make_candidate

    return make_candidate(cid, cls, target, value, "rationale", ("run-1",))


def test_skill_candidate_validates():
    from pxx.improve.candidates import CandidateClass, validate_candidate

    c = _content_candidate("s1", CandidateClass.SKILL, ".pxx/skills/rebase.md", "# skill\n")
    validate_candidate(c)  # must not raise


def test_fewshot_and_playbook_validate():
    from pxx.improve.candidates import CandidateClass, validate_candidate

    validate_candidate(
        _content_candidate("f1", CandidateClass.FEWSHOT, ".pxx/fewshot/ex1.md", "example\n")
    )
    validate_candidate(
        _content_candidate("p1", CandidateClass.PLAYBOOK, ".pxx/playbooks/ship.md", "steps\n")
    )


def test_demonstration_requires_contrastive_poles():
    import pytest

    from pxx.errors import CandidateInvalid
    from pxx.improve.candidates import CandidateClass, validate_candidate

    good = _content_candidate(
        "d1",
        CandidateClass.DEMONSTRATION,
        ".pxx/demonstrations/ex1.md",
        "task: fix x\nbad: deleted the test\npreferred: fix the code",
    )
    validate_candidate(good)
    one_sided = _content_candidate(
        "d2",
        CandidateClass.DEMONSTRATION,
        ".pxx/demonstrations/ex2.md",
        "task: fix x\nbad: deleted the test",
    )
    with pytest.raises(CandidateInvalid, match="contrastive"):
        validate_candidate(one_sided)


def test_new_classes_reject_wrong_surface_and_protected():
    import pytest

    from pxx.errors import CandidateInvalid
    from pxx.improve.candidates import CandidateClass, validate_candidate

    # skill content must live under .pxx/skills/, not prompts
    with pytest.raises(CandidateInvalid, match="target must match"):
        validate_candidate(_content_candidate("s2", CandidateClass.SKILL, "pxx/prompts/x.md", "x"))
    # no class may touch a protected path
    with pytest.raises(CandidateInvalid, match="protected"):
        validate_candidate(_content_candidate("s3", CandidateClass.SKILL, "pxx/safety.py", "x"))
    # traversal fails closed
    with pytest.raises(CandidateInvalid):
        validate_candidate(
            _content_candidate("s4", CandidateClass.FEWSHOT, ".pxx/fewshot/../evil.md", "x")
        )
