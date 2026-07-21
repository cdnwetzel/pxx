"""Phase 11: agent manifests and run directories.

An :class:`AgentManifest` captures everything that defines *which agent* ran:
pxx version, backend, model, prompt hashes, budgets, review mode. Its
:attr:`AgentManifest.agent_version_id` is a stable content hash — the same
configuration always yields the same id, and any behavioral change yields a
new one. The canonical form never contains URLs, paths, or secrets.

Run directories (``state_dir/runs/<run_id>/``) hold the experience-plane
record of one run: ``manifest.json``, ``task.json``, ``events.jsonl``
(metadata-only), ``outcome.json``, and optionally ``diff.patch``. All writes
are best-effort telemetry: failures are logged, never raised.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__
from .config import ModelRef, Settings
from .safety import Budgets

log = logging.getLogger("pxx.manifest")


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


#: Memory-injection policy version: bump when inject.build_context's
#: selection/ranking/rendering policy changes (context drift sentinel).
MEMORY_POLICY_VERSION = "1"


@dataclass(frozen=True)
class ModelFingerprint:
    """The SERVED model's identity, not just its name (Phase 11 amend).

    An Ollama tag re-pulled to different bytes is a DIFFERENT agent under the
    same name — the digest catches it. ``hash`` feeds agent_version_id.
    """

    provider: str
    requested_model: str
    resolved_id: str = ""
    digest: str = ""
    modified_at: str = ""
    detail: str = ""

    @property
    def hash(self) -> str:
        key = self.digest or self.resolved_id
        return _sha16(key) if key else ""

    @classmethod
    def empty(cls, model: ModelRef) -> ModelFingerprint:
        return cls(provider=model.provider, requested_model=model.model)


async def probe_model_fingerprint(
    model: ModelRef,
    *,
    transport: Any = None,
    timeout: float = 2.0,  # noqa: ASYNC109 - probe deadline, not asyncio scope
) -> ModelFingerprint:
    """Best-effort probe of the served model's fingerprint.

    Ollama: ``GET /api/tags`` -> digest + modified_at for the named model.
    OpenAI-compatible: ``GET /v1/models`` -> the resolved id (+ created /
    owned_by when present). Any failure degrades to an empty fingerprint —
    identity is telemetry and never gates a run.
    """
    try:
        import httpx
    except ImportError:
        return ModelFingerprint.empty(model)
    base = (model.base_url or "").rstrip("/")
    if not base:
        return ModelFingerprint.empty(model)
    try:
        async with httpx.AsyncClient(base_url=base, timeout=timeout, transport=transport) as client:
            if model.provider == "ollama":
                resp = await client.get("/api/tags")
                resp.raise_for_status()
                for entry in resp.json().get("models", []):
                    name = entry.get("name", "")
                    if name == model.model or name.split(":")[0] == model.model.split(":")[0]:
                        return ModelFingerprint(
                            provider=model.provider,
                            requested_model=model.model,
                            resolved_id=name,
                            digest=entry.get("digest", ""),
                            modified_at=entry.get("modified_at", ""),
                        )
                return ModelFingerprint.empty(model)
            resp = await client.get("/v1/models")
            resp.raise_for_status()
            for entry in resp.json().get("data", []):
                if entry.get("id") == model.model:
                    detail = str(entry.get("created") or entry.get("owned_by") or "")
                    return ModelFingerprint(
                        provider=model.provider,
                        requested_model=model.model,
                        resolved_id=str(entry.get("id")),
                        detail=detail,
                    )
    except Exception:
        log.debug("model fingerprint probe failed for %s", model.model, exc_info=True)
    return ModelFingerprint.empty(model)


def aci_hash(workflow_hash: str = "") -> str:
    """Hash the agent-computer interface: registered tool specs + the
    WORKFLOW.md contract. An interface change is a detectable drift."""
    try:
        from .tools import default_registry

        specs = default_registry().specs()
    except Exception:
        log.debug("ACI spec collection failed; hashing empty tool set", exc_info=True)
        specs = []
    blob = json.dumps({"tools": specs, "workflow": workflow_hash}, sort_keys=True, default=str)
    return _sha16(blob)


def context_hash(settings: Settings, prompt_hashes: dict[str, str]) -> str:
    """Hash the context assembly: system prompts + memory-injection policy."""
    blob = json.dumps(
        {
            "prompts": dict(sorted(prompt_hashes.items())),
            "memory_enabled": settings.memory_enabled,
            "memory_policy": MEMORY_POLICY_VERSION,
        },
        sort_keys=True,
    )
    return _sha16(blob)


def hash_prompts(prompts_dir: Path | None = None) -> dict[str, str]:
    """sha256[:16] of each ``*.md`` prompt file, keyed by file name, sorted.

    Defaults to the packaged ``pxx/prompts`` resources. Missing/unreadable
    prompt dirs degrade to an empty mapping (identity stays deterministic).
    """
    try:
        if prompts_dir is None:
            root = resources.files("pxx") / "prompts"
            entries = [
                (entry.name, entry.read_bytes())
                for entry in root.iterdir()
                if entry.name.endswith(".md")
            ]
        else:
            entries = [(p.name, p.read_bytes()) for p in Path(prompts_dir).glob("*.md")]
    except Exception:
        log.exception("prompt hashing failed; using empty prompt set")
        return {}
    return {name: hashlib.sha256(blob).hexdigest()[:16] for name, blob in sorted(entries)}


def _settings_hash(settings: Settings) -> str:
    """Behavior-relevant settings fingerprint (secrets/URLs/paths excluded)."""
    data = {
        "permission": str(settings.permission),
        "scope": sorted(settings.scope),
        "memory_enabled": settings.memory_enabled,
        "sandbox_shell": settings.sandbox_shell,
        "test_command": settings.test_command,
        "hooks": [f"{h.event}:{h.command}" for h in settings.hooks],
        "fallback_models": [f"{m.provider}:{m.model}" for m in settings.fallback_models],
        "mcp_servers": [s.name for s in settings.mcp_servers],
    }
    return _sha16(json.dumps(data, sort_keys=True))


@dataclass(frozen=True)
class AgentManifest:
    """Identity of the agent that produced a run."""

    pxx_version: str
    backend: str
    provider: str
    model: str
    python_version: str
    prompt_hashes: dict[str, str]  # prompt file name -> sha256[:16]
    settings_hash: str
    budgets: Budgets
    review_mode: str
    workflow_hash: str = ""  # sha256[:16] of WORKFLOW.md; "" when absent
    protected_paths_hash: str = ""  # sha256[:16] of the sorted guardrail list
    model_fingerprint: str = ""  # served-model digest hash; "" when unknown
    aci_hash: str = ""  # agent-computer interface hash (tools + workflow)
    context_hash: str = ""  # context assembly hash (prompts + memory policy)

    def canonical(self) -> dict[str, Any]:
        """JSON-able identity form. Never contains URLs, paths, or secrets."""
        return {
            "pxx_version": self.pxx_version,
            "backend": self.backend,
            "provider": self.provider,
            "model": self.model,
            "python_version": self.python_version,
            "prompt_hashes": dict(sorted(self.prompt_hashes.items())),
            "settings_hash": self.settings_hash,
            "budgets": asdict(self.budgets),
            "review_mode": self.review_mode,
            "workflow_hash": self.workflow_hash,
            "protected_paths_hash": self.protected_paths_hash,
            "model_fingerprint": self.model_fingerprint,
            "aci_hash": self.aci_hash,
            "context_hash": self.context_hash,
        }

    @property
    def agent_version_id(self) -> str:
        """sha256(canonical json, sort_keys)[:16] — same config, same id."""
        return _sha16(json.dumps(self.canonical(), sort_keys=True))


def protected_paths_hash() -> str:
    """sha256[:16] of the sorted guardrail list (pxx.protected_paths)."""
    from .protected_paths import PROTECTED_PREFIXES

    return _sha16("\n".join(sorted(PROTECTED_PREFIXES)))


def build_manifest(
    settings: Settings,
    backend_name: str,
    prompts_dir: Path | None = None,
    review_mode: str = "blocking",
    workflow_path: Path | None = None,
    fingerprint: ModelFingerprint | None = None,
) -> AgentManifest:
    """Build the manifest for a run from resolved settings.

    ``workflow_path`` (usually ``<repo>/WORKFLOW.md``) is hashed into the
    identity when present; unreadable files degrade to an empty hash so
    identity never gates a run. ``fingerprint`` (from
    :func:`probe_model_fingerprint`) binds the SERVED model bytes.
    """
    wf_hash = ""
    if workflow_path is not None and Path(workflow_path).is_file():
        try:
            wf_hash = _sha16(Path(workflow_path).read_text(encoding="utf-8"))
        except Exception:
            log.exception("workflow hashing failed; using empty workflow hash")
    prompts = hash_prompts(prompts_dir)
    return AgentManifest(
        pxx_version=__version__,
        backend=backend_name,
        provider=settings.model.provider,
        model=settings.model.model,
        python_version=platform.python_version(),
        prompt_hashes=prompts,
        settings_hash=_settings_hash(settings),
        budgets=settings.budgets,
        review_mode=str(review_mode),
        workflow_hash=wf_hash,
        protected_paths_hash=protected_paths_hash(),
        model_fingerprint=fingerprint.hash if fingerprint else "",
        aci_hash=aci_hash(wf_hash),
        context_hash=context_hash(settings, prompts),
    )


class RunDirWriter:
    """Best-effort writer for ``state_dir/runs/<run_id>/`` telemetry.

    Every write method swallows and logs failures — like the audit log, run
    telemetry must never crash a session.
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @classmethod
    def open(cls, state_dir: Path, run_id: str) -> RunDirWriter:
        """Create (best-effort) and return the writer for ``runs/<run_id>/``."""
        writer = cls(Path(state_dir) / "runs" / run_id)
        try:
            writer.run_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.exception("run dir creation failed (best-effort): %s", writer.run_dir)
        return writer

    def _write_json(self, name: str, payload: Any) -> None:
        try:
            text = json.dumps(payload, sort_keys=True, default=str)
            (self.run_dir / name).write_text(text + "\n")
        except Exception:
            log.exception("run telemetry write failed (best-effort): %s", name)

    def write_manifest(self, manifest: AgentManifest) -> None:
        payload = {"agent_version_id": manifest.agent_version_id, **manifest.canonical()}
        self._write_json("manifest.json", payload)

    def write_task(self, task: dict[str, Any]) -> None:
        self._write_json("task.json", task)

    def append_event(self, event: dict[str, Any]) -> None:
        try:
            with (self.run_dir / "events.jsonl").open("a") as fh:
                fh.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        except Exception:
            log.exception("run event append failed (best-effort)")

    def write_outcome(self, outcome: dict[str, Any]) -> None:
        self._write_json("outcome.json", outcome)

    def write_diff(self, patch_text: str) -> None:
        try:
            (self.run_dir / "diff.patch").write_text(patch_text)
        except Exception:
            log.exception("run diff write failed (best-effort)")
