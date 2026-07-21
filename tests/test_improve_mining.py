"""Phase 15 tests: deterministic outcome clustering + propose-only mining."""

from __future__ import annotations

import dataclasses
import json

from pxx.improve.mining import (
    CORRELATION_LABEL,
    Cluster,
    MinedRun,
    Proposal,
    cluster_outcomes,
    propose_from_clusters,
)
from pxx.outcome import TerminalCode


def run(run_id, code, backend="native", model="qwen", rounds=5, memory=True, **kw):
    return MinedRun(
        run_id=run_id,
        terminal_code=str(code),
        backend=backend,
        model=model,
        rounds=rounds,
        memory_used=memory,
        **kw,
    )


# --- clustering ---------------------------------------------------------------


def test_clusters_are_deterministic_regardless_of_input_order():
    runs_a = [
        run("r3", "REVIEW_UNPARSEABLE", rounds=2),
        run("r1", "REVIEW_UNPARSEABLE", rounds=2),
        run("r2", "COMPLETED", rounds=8),
    ]
    runs_b = list(reversed(runs_a))
    assert cluster_outcomes(runs_a) == cluster_outcomes(runs_b)


def test_grouping_dimensions_split_clusters():
    runs = [
        run("a", "REVIEW_UNPARSEABLE", backend="native"),
        run("b", "REVIEW_UNPARSEABLE", backend="aider"),
        run("c", "REVIEW_UNPARSEABLE", model="other"),
        run("d", "REVIEW_UNPARSEABLE", memory=False),
        run("e", "REVIEW_UNPARSEABLE", rounds=50),  # different rounds bucket
        run("f", "COMPLETED"),
    ]
    clusters = cluster_outcomes(runs)
    assert len(clusters) == 6
    assert all(c.size == 1 for c in clusters)


def test_rounds_buckets():
    runs = [
        run("z", "COMPLETED", rounds=0),
        run("o", "COMPLETED", rounds=3),
        run("m", "COMPLETED", rounds=10),
        run("h", "COMPLETED", rounds=11),
    ]
    buckets = {c.run_ids[0]: c.rounds_bucket for c in cluster_outcomes(runs)}
    assert buckets == {"z": "0", "o": "1-3", "m": "4-10", "h": "11+"}


def test_every_cluster_labeled_correlation_never_causation():
    runs = [run(f"r{i}", "ROUND_CAP") for i in range(4)]
    clusters = cluster_outcomes(runs)
    assert len(clusters) == 1
    for cluster in clusters:
        assert cluster.label == CORRELATION_LABEL == "correlation"
        assert cluster.label != "causation"


def test_cluster_cites_sorted_run_ids():
    runs = [
        run("r9", "REVIEW_UNPARSEABLE"),
        run("r1", "REVIEW_UNPARSEABLE"),
        run("r5", "REVIEW_UNPARSEABLE"),
    ]
    (cluster,) = cluster_outcomes(runs)
    assert cluster.run_ids == ("r1", "r5", "r9")


def test_input_duck_typed_mapping_and_dataclass():
    @dataclasses.dataclass(frozen=True)
    class ForeignRun:  # pxx.runs-shaped stand-in with extra fields
        run_id: str
        terminal_code: TerminalCode
        backend: str
        model: str
        rounds: int
        memory_used: bool
        extra_field: str = "ignored"

    foreign = ForeignRun("d1", TerminalCode.REVIEW_UNPARSEABLE, "native", "qwen", 3, True)
    mapping = {
        "run_id": "m1",
        "terminal_code": "REVIEW_UNPARSEABLE",
        "backend": "native",
        "model": "qwen",
        "rounds": 3,
        "memory_used": True,
        "unrelated": 123,
    }
    clusters = cluster_outcomes([foreign, mapping])
    assert len(clusters) == 1
    assert clusters[0].run_ids == ("d1", "m1")


# --- proposals ----------------------------------------------------------------


def test_proposals_reference_their_evidence_run_ids():
    runs = [run(f"r{i}", "BUDGET_EXCEEDED") for i in range(5)]
    clusters = cluster_outcomes(runs)
    proposals = propose_from_clusters(clusters)
    assert proposals
    cluster_ids = {rid for c in clusters for rid in c.run_ids}
    for p in proposals:
        assert isinstance(p, Proposal)
        assert p.evidence, "proposal must cite evidence"
        assert set(p.evidence) <= cluster_ids
        assert p.basis == CORRELATION_LABEL


def test_proposals_are_proposals_only_and_jsonable():
    runs = [run("r1", "ROUND_CAP", rounds=30), run("r2", "ROUND_CAP", rounds=30)]
    proposals = propose_from_clusters(cluster_outcomes(runs))
    assert len(proposals) == 1
    p = proposals[0]
    assert p.operation == "adjust_prompt"
    assert p.target == "pxx/prompts/native_system.md"
    # JSON-able
    encoded = json.dumps(p.to_dict())
    assert json.loads(encoded)["basis"] == "correlation"


def test_completed_clusters_yield_no_proposals():
    runs = [run(f"r{i}", "COMPLETED") for i in range(3)]
    assert propose_from_clusters(cluster_outcomes(runs)) == []


def test_failure_without_memory_yields_memory_proposal():
    runs = [run(f"r{i}", "REVIEW_UNPARSEABLE", memory=False) for i in range(3)]
    proposals = propose_from_clusters(cluster_outcomes(runs))
    targets = {(p.target, p.operation) for p in proposals}
    assert ("memory_retrieval_limit", "adjust_memory") in targets
    assert ("pxx/prompts/review.md", "adjust_prompt") in targets


def test_proposals_merge_across_clusters_and_are_deterministic():
    runs = [
        run("a1", "MODEL_UNAVAILABLE", backend="native"),
        run("b1", "MODEL_UNAVAILABLE", backend="aider"),
        run("b2", "MODEL_UNAVAILABLE", backend="aider"),
    ]
    first = propose_from_clusters(cluster_outcomes(runs))
    second = propose_from_clusters(cluster_outcomes(list(reversed(runs))))
    assert first == second
    (p,) = first
    assert p.target == "model"
    assert p.evidence == ("a1", "b1", "b2")
    assert 0.0 < p.confidence <= 0.9


def test_confidence_grows_with_evidence_but_never_reaches_one():
    small = propose_from_clusters(cluster_outcomes([run("r1", "BUDGET_EXCEEDED")]))
    big = propose_from_clusters(
        cluster_outcomes([run(f"r{i}", "BUDGET_EXCEEDED") for i in range(20)])
    )
    assert small[0].confidence < big[0].confidence
    assert big[0].confidence < 1.0


def test_cluster_key_is_stable():
    c = Cluster(
        terminal_code="REVIEW_UNPARSEABLE",
        backend="native",
        model="qwen",
        memory_used=True,
        rounds_bucket="4-10",
        run_ids=("r1",),
    )
    assert c.key == (
        "REVIEW_UNPARSEABLE",
        "native",
        "qwen",
        True,
        "4-10",
        "",
        "",
        "",
        "",
        False,
    )
    assert c.size == 1


# --- B4.1: richer dims, pattern detectors, root-cause proposals --------------------


def test_new_dims_split_clusters():
    from pxx.improve.mining import cluster_outcomes

    base = dict(run_id="r", terminal_code="ROUND_CAP")
    runs = [
        {**base, "run_id": "r1", "stage": "edit"},
        {**base, "run_id": "r2", "stage": "review"},
        {**base, "run_id": "r3", "stage": "edit", "retried": True},
        {**base, "run_id": "r4", "stage": "edit", "scope_type": "scoped"},
    ]
    clusters = cluster_outcomes(runs)
    assert len(clusters) == 4  # stage, retry, and scope-type all split


def test_detectors_fire_on_crafted_streams():
    from pxx.improve.mining import detect_patterns

    runs = [
        {"run_id": "u1", "terminal_code": "REVIEW_UNPARSEABLE"},
        {
            "run_id": "u2",
            "terminal_code": "ROUND_CAP",
            "contributing_codes": ["REVIEW_UNPARSEABLE"],
        },
        {"run_id": "t1", "terminal_code": "EDIT_TIMEOUT"},
        {"run_id": "t2", "terminal_code": "BUDGET_EXCEEDED"},
        {"run_id": "l1", "terminal_code": "LINT_BLOCKED"},
        {"run_id": "m1", "terminal_code": "ROUND_CAP", "memory_used": True, "files_changed": 10},
        {"run_id": "m2", "terminal_code": "ROUND_CAP", "memory_used": True, "files_changed": 8},
        {"run_id": "n1", "terminal_code": "ROUND_CAP", "memory_used": False, "files_changed": 1},
        {"run_id": "n2", "terminal_code": "COMPLETED", "memory_used": False, "files_changed": 2},
        {"run_id": "f1", "terminal_code": "MODEL_UNAVAILABLE", "model": "bad-model"},
        {"run_id": "f2", "terminal_code": "MODEL_UNAVAILABLE", "model": "bad-model"},
        {"run_id": "p1", "terminal_code": "COMPLETED", "model": "good-model"},
    ]
    names = {p.name for p in detect_patterns(runs)}
    assert "unparseable_review" in names
    assert "timeout_cluster" in names
    assert "lint_blocked" in names
    assert "memory_diff_size_correlation" in names
    assert "model_failure_disparity" in names
    # every pattern cites its evidence and is correlation-labeled
    for p in detect_patterns(runs):
        assert p.run_ids and p.label == "correlation"


def test_detectors_quiet_on_healthy_stream():
    from pxx.improve.mining import detect_patterns

    runs = [
        {"run_id": f"r{i}", "terminal_code": "COMPLETED", "model": "m", "files_changed": 2}
        for i in range(5)
    ]
    assert detect_patterns(runs) == []


def test_model_capability_is_not_proposed_as_prompt_change():
    """B4.1 acceptance: a MODEL_CAPABILITY failure proposes a model lever,
    never a prompt tweak."""
    from pxx.improve.mining import RootCause, cluster_outcomes, propose_from_clusters

    runs = [
        {"run_id": f"r{i}", "terminal_code": "NO_TEST_PROGRESS", "memory_used": True}
        for i in range(3)
    ]
    proposals = propose_from_clusters(cluster_outcomes(runs))
    assert proposals
    for p in proposals:
        assert p.root_cause == str(RootCause.MODEL_CAPABILITY)
        assert p.target == "model" and p.operation == "switch_model"
        assert "prompt" not in p.target
        assert p.reason_prompt_change_is_insufficient
        assert p.evidence


def test_proposals_carry_root_cause_and_reason():
    from pxx.improve.mining import RootCause, cluster_outcomes, propose_from_clusters

    runs = [{"run_id": "r1", "terminal_code": "BUDGET_EXCEEDED", "memory_used": False}]
    proposals = propose_from_clusters(cluster_outcomes(runs))
    by_op = {p.operation: p for p in proposals}
    assert by_op["tighten_budget"].root_cause == str(RootCause.TOOLING)
    assert by_op["adjust_memory"].root_cause == str(RootCause.CONTEXT_MISSING)
    assert by_op["adjust_memory"].reason_prompt_change_is_insufficient


def test_mining_determinism_with_new_dims():
    from pxx.improve.mining import cluster_outcomes, propose_from_clusters

    runs = [
        {"run_id": "r3", "terminal_code": "ROUND_CAP", "stage": "edit"},
        {"run_id": "r1", "terminal_code": "ROUND_CAP", "stage": "edit"},
        {"run_id": "r2", "terminal_code": "ROUND_CAP", "stage": "edit"},
    ]
    first = propose_from_clusters(cluster_outcomes(runs))
    second = propose_from_clusters(cluster_outcomes(list(reversed(runs))))
    assert [p.to_dict() for p in first] == [p.to_dict() for p in second]
