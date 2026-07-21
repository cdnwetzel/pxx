"""Phase 16: declarative improvement candidates + integrity validation.

A :class:`Candidate` is a *declarative*, JSON-persisted proposal for one
behavioral change, stored at ``.pxx/candidates/<id>/candidate.json`` and
immutable once written. Candidates never touch the trusted control plane:
any protected path (see :mod:`pxx.protected_paths`), evaluator logic,
permissions, budget increases, or new dependencies are human-only and are
rejected here by :func:`validate_candidate` raising
:class:`pxx.errors.CandidateInvalid`.

Permitted change classes:

- ``settings`` overlays: ``review_mode``, ``budgets`` (TIGHTEN-ONLY,
  compared field-wise against the baseline Budgets the candidate
  references), ``model``, ``fallback_models``, ``memory_retrieval_limit``.
- ``content`` targets: ``pxx/prompts/*.md`` text.

One behavioral variable per candidate: a budgets overlay mapping with more
than one field is rejected. Content candidates derive their repo-relative
path ONCE (:func:`content_path`) and the exact same derived value is both
validated and written — a test pins that equivalence.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..errors import CandidateInvalid
from ..protected_paths import is_protected_path
from ..review import ReviewMode
from ..safety import Budgets


class CandidateClass(StrEnum):
    SETTINGS = "settings"
    CONTENT = "content"
    SKILL = "skill"
    FEWSHOT = "fewshot"
    PLAYBOOK = "playbook"
    DEMONSTRATION = "demonstration"


#: Allowlisted settings-overlay targets. Anything else (permissions, scope,
#: hooks, evaluator knobs, dependencies) is human-only.
SETTINGS_TARGETS: tuple[str, ...] = (
    "review_mode",
    "budgets",
    "model",
    "fallback_models",
    "memory_retrieval_limit",
)

_BUDGET_FIELDS = frozenset(f.name for f in dataclasses.fields(Budgets))

#: Content candidates may only target prompt markdown files.
_CONTENT_TARGET_RE = re.compile(r"^pxx/prompts/[A-Za-z0-9][A-Za-z0-9._-]*\.md$")

#: Allowlisted target surfaces per content-like candidate class. Each class
#: writes exactly ONE file under its own declarative root.
_CLASS_TARGET_RES: dict[str, re.Pattern[str]] = {
    str(CandidateClass.CONTENT): _CONTENT_TARGET_RE,
    str(CandidateClass.SKILL): re.compile(r"^\.pxx/skills/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"),
    str(CandidateClass.FEWSHOT): re.compile(r"^\.pxx/fewshot/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"),
    str(CandidateClass.PLAYBOOK): re.compile(r"^\.pxx/playbooks/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"),
    str(CandidateClass.DEMONSTRATION): re.compile(
        r"^\.pxx/demonstrations/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"
    ),
}

#: Candidate ids are single safe path segments.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Candidate:
    """One declarative behavioral change. JSON-able throughout."""

    id: str
    change_class: str  # CandidateClass value
    target: str
    value: Any  # settings overlay value, or prompt markdown text
    rationale: str
    evidence: tuple[str, ...]  # run_ids backing this candidate
    content_hash: str  # sha256 of canonical {"change_class","target","value"}
    baseline_budgets: dict[str, float] | None = None  # required for "budgets"


def compute_content_hash(change_class: str, target: str, value: Any) -> str:
    """Content-address the change itself so the persisted JSON is immutable."""
    canonical = json.dumps(
        {"change_class": str(change_class), "target": target, "value": value},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_candidate(
    candidate_id: str,
    change_class: CandidateClass | str,
    target: str,
    value: Any,
    rationale: str,
    evidence: tuple[str, ...] | list[str],
    *,
    baseline_budgets: Budgets | dict[str, float] | None = None,
) -> Candidate:
    """Build a Candidate with a correct content_hash (still unvalidated)."""
    cls = str(change_class)
    baseline: dict[str, float] | None
    if isinstance(baseline_budgets, Budgets):
        baseline = dataclasses.asdict(baseline_budgets)
    else:
        baseline = dict(baseline_budgets) if baseline_budgets is not None else None
    return Candidate(
        id=candidate_id,
        change_class=cls,
        target=target,
        value=value,
        rationale=rationale,
        evidence=tuple(evidence),
        content_hash=compute_content_hash(cls, target, value),
        baseline_budgets=baseline,
    )


def content_path(candidate: Candidate) -> str:
    """Derive the repo-relative path for a content candidate.

    THE single derivation: both :func:`validate_candidate` and
    :func:`write_candidate` call this, so the validated path and the
    persisted path can never diverge.
    """
    raw = str(candidate.target).replace("\\", "/")
    return posixpath.normpath(raw.removeprefix("./"))


def _validate_budgets(candidate: Candidate) -> None:
    value = candidate.value
    if not isinstance(value, dict) or not value:
        raise CandidateInvalid("budgets candidate value must be a non-empty mapping")
    if len(value) > 1:
        raise CandidateInvalid(
            f"one behavioral variable per candidate: budgets changes {sorted(value)} at once"
        )
    (field_name,) = value.keys()
    if field_name not in _BUDGET_FIELDS:
        raise CandidateInvalid(f"unknown budget field: {field_name!r}")
    new = value[field_name]
    if isinstance(new, bool) or not isinstance(new, (int, float)):
        raise CandidateInvalid(f"budget {field_name} must be numeric, got {new!r}")
    baseline = candidate.baseline_budgets
    if not baseline or field_name not in baseline:
        raise CandidateInvalid(
            f"budgets candidate must reference a baseline Budgets containing {field_name!r}"
        )
    base = baseline[field_name]
    if isinstance(base, bool) or not isinstance(base, (int, float)):
        raise CandidateInvalid(f"baseline budget {field_name} is not numeric: {base!r}")
    if new > base:
        raise CandidateInvalid(
            f"budget increase rejected (tighten-only): {field_name} {new} > baseline {base}"
        )


def _validate_settings(candidate: Candidate) -> None:
    if candidate.target not in SETTINGS_TARGETS:
        raise CandidateInvalid(
            f"non-allowlisted settings target: {candidate.target!r} "
            f"(allowed: {list(SETTINGS_TARGETS)})"
        )
    if candidate.target == "budgets":
        _validate_budgets(candidate)
    elif candidate.target == "review_mode":
        try:
            ReviewMode(str(candidate.value))
        except ValueError as exc:
            raise CandidateInvalid(f"invalid review_mode: {candidate.value!r}") from exc
    elif candidate.target == "model":
        v = candidate.value
        ok = (isinstance(v, str) and bool(v.strip())) or (
            isinstance(v, dict) and isinstance(v.get("model"), str) and v["model"].strip()
        )
        if not ok:
            raise CandidateInvalid("model candidate must be a model name or ModelRef mapping")
    elif candidate.target == "fallback_models":
        v = candidate.value
        if not isinstance(v, list) or not all(
            (isinstance(m, str) and m.strip())
            or (isinstance(m, dict) and isinstance(m.get("model"), str) and m["model"].strip())
            for m in v
        ):
            raise CandidateInvalid("fallback_models must be a list of model names/mappings")
    elif candidate.target == "memory_retrieval_limit":
        v = candidate.value
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise CandidateInvalid("memory_retrieval_limit must be a positive integer")


def _validate_content(candidate: Candidate) -> None:
    cls = str(candidate.change_class)
    target_re = _CLASS_TARGET_RES.get(cls)
    if target_re is None:
        raise CandidateInvalid(f"unknown candidate class: {cls!r}")
    path = content_path(candidate)  # derived ONCE; same value write_candidate uses
    if is_protected_path(path):  # also fail-closed for unclassifiable paths
        raise CandidateInvalid(f"protected or unclassifiable path: {candidate.target!r}")
    if not target_re.match(path):
        raise CandidateInvalid(f"{cls} target must match {target_re.pattern}, got {path!r}")
    if not isinstance(candidate.value, str) or not candidate.value.strip():
        raise CandidateInvalid(f"{cls} candidate value must be non-empty text")
    if cls == str(CandidateClass.DEMONSTRATION):
        # Contrastive (anti-demonstration) format: task, bad action, preferred
        # action — the negative-learning corpus requires both poles.
        lowered = candidate.value.lower()
        if "bad" not in lowered or "preferred" not in lowered:
            raise CandidateInvalid(
                "demonstration candidates must be contrastive: the value must "
                "name the bad action AND the preferred action"
            )


def validate_candidate(candidate: Candidate) -> None:
    """Raise :class:`CandidateInvalid` on any integrity/policy violation."""
    if not _ID_RE.match(candidate.id) or ".." in candidate.id:
        raise CandidateInvalid(f"unsafe candidate id: {candidate.id!r}")
    try:
        cls = CandidateClass(str(candidate.change_class))
    except ValueError as exc:
        raise CandidateInvalid(f"unknown candidate class: {candidate.change_class!r}") from exc
    if not candidate.rationale or not candidate.rationale.strip():
        raise CandidateInvalid("candidate is missing a rationale")
    if not candidate.evidence:
        raise CandidateInvalid("candidate is missing evidence run_ids")
    expected = compute_content_hash(candidate.change_class, candidate.target, candidate.value)
    if candidate.content_hash != expected:
        raise CandidateInvalid("content_hash mismatch: candidate payload was tampered with")
    if cls is CandidateClass.SETTINGS:
        _validate_settings(candidate)
    else:
        _validate_content(candidate)


def candidate_to_dict(candidate: Candidate) -> dict[str, Any]:
    target = (
        content_path(candidate)
        if str(candidate.change_class) in _CLASS_TARGET_RES
        else candidate.target
    )
    return {
        "id": candidate.id,
        "class": str(candidate.change_class),
        "target": target,
        "value": candidate.value,
        "rationale": candidate.rationale,
        "evidence": list(candidate.evidence),
        "content_hash": candidate.content_hash,
        "baseline_budgets": candidate.baseline_budgets,
    }


def candidate_from_dict(data: dict[str, Any]) -> Candidate:
    return Candidate(
        id=str(data["id"]),
        change_class=str(data["class"]),
        target=str(data["target"]),
        value=data["value"],
        rationale=str(data["rationale"]),
        evidence=tuple(str(e) for e in data["evidence"]),
        content_hash=str(data["content_hash"]),
        baseline_budgets=data.get("baseline_budgets"),
    )


def write_candidate(candidate: Candidate, base_dir: Path | str) -> Path:
    """Validate then persist to ``<base_dir>/candidates/<id>/candidate.json``.

    Immutable once written: an existing record is never overwritten.
    Content candidates persist the exact path that was validated (both come
    from :func:`content_path`).
    """
    validate_candidate(candidate)
    dest = Path(base_dir) / "candidates" / candidate.id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "candidate.json"
    if path.exists():
        raise CandidateInvalid(f"candidate already exists and is immutable: {candidate.id!r}")
    path.write_text(json.dumps(candidate_to_dict(candidate), indent=2, sort_keys=True) + "\n")
    return path


def read_candidate(candidate_dir: Path | str) -> Candidate:
    """Load a persisted candidate directory (``.../candidates/<id>``)."""
    data = json.loads((Path(candidate_dir) / "candidate.json").read_text())
    return candidate_from_dict(data)
