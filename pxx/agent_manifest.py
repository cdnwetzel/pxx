"""Immutable behavior identity — roadmap Phase 11, minimum slice.

An AgentManifest captures every behavior-defining fact about how pxx will
run a session: versions, models, prompt hashes, and budgets. Hashing its
canonical form yields ``agent_version_id`` — the same configuration always
produces the same id, so run outcomes can be grouped and compared by the
behavior that produced them.

Privacy (a256a04): the manifest carries model ids and version strings only —
never endpoint URLs, hostnames, or filesystem paths.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import subprocess
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

import pxx
from pxx import review_gate

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class AgentManifest:
    manifest_version: int
    workflow_hash: str  # WORKFLOW.md contract (10.5) — editing it is a behavior change
    pxx_version: str
    pxx_commit: str
    aider_version: str
    python_version: str
    editor_backend: str  # "ollama" | "vllm"
    editor_model: str
    reviewer_backend: str  # "local" | "claude"
    reviewer_model: str
    reviewer_mode: str  # "blocking" | "advisory" — whether the reviewer gates
    edit_prompt_hash: str
    healing_prompt_hash: str
    review_prompt_hash: str
    max_rounds: int
    max_seconds: float
    diff_budget: int


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _pxx_commit() -> str:
    """Short HEAD of the installed pxx source tree, or 'release' for a
    non-checkout install — behavior can change with code, so the commit is
    a behavior-defining field, not a runtime one."""
    pkg_root = Path(pxx.__file__).resolve().parent.parent
    try:
        r = subprocess.run(
            ["git", "-C", str(pkg_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "release"
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else "release"


def _aider_version() -> str:
    try:
        return metadata.version("aider-chat")
    except metadata.PackageNotFoundError:
        return "unknown"


def _workflow_hash() -> str:
    path = Path(pxx.__file__).resolve().parent.parent / "WORKFLOW.md"
    try:
        return _sha(path.read_text(encoding="utf-8"))
    except OSError:
        return "missing"


def _edit_prompt_hash() -> str:
    path = Path(pxx.__file__).resolve().parent / "prompts" / "system.md"
    try:
        return _sha(path.read_text(encoding="utf-8"))
    except OSError:
        return "missing"


def current_manifest(
    editor_backend: str,
    editor_model: str,
    max_rounds: int,
    max_seconds: float,
    diff_budget: int,
) -> AgentManifest:
    """Snapshot the behavior-defining configuration of this process.

    The healing prompt is built in code, so its hash covers the builder's
    source (``inspect.getsource``) — a template change shows up even in
    release installs where ``pxx_commit`` is opaque.
    """
    return AgentManifest(
        manifest_version=MANIFEST_VERSION,
        workflow_hash=_workflow_hash(),
        pxx_version=getattr(pxx, "__version__", "unknown"),
        pxx_commit=_pxx_commit(),
        aider_version=_aider_version(),
        python_version=platform.python_version(),
        editor_backend=editor_backend,
        editor_model=editor_model,
        reviewer_backend=review_gate._review_backend(),
        reviewer_model=review_gate._review_model(),
        reviewer_mode=review_gate.review_mode(),
        edit_prompt_hash=_edit_prompt_hash(),
        healing_prompt_hash=_sha(inspect.getsource(review_gate.build_healing_prompt)),
        review_prompt_hash=_sha(
            review_gate.LOCAL_REVIEW_INSTRUCTIONS + review_gate._TASK_CONTEXT_TEMPLATE
        ),
        max_rounds=max_rounds,
        max_seconds=max_seconds,
        diff_budget=diff_budget,
    )


def agent_version_id(manifest: AgentManifest) -> str:
    """Stable id: canonical JSON of the manifest, hashed. Same config ⇒ same id."""
    canon = json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))
    return "agent-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]
