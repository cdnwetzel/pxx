"""Session audit log for pxx (#004).

Append-only JSONL log of every pxx session's metadata, written before
``os.execv`` hands control to aider. The log is the input to:

- Post-mortem investigation ("what session edited this file?")
- Tier 4 of #001 dogfooding (learnings.md distillation)
- Mode-by-mode filtering (``jq '.session_class == "self-fix"'``)

This module is pure: no subprocess calls, no git invocations. The caller
(``pxx.cli``) composes the record from its own state and passes it in.

**Privacy note:** Log records include absolute file paths (``git_repo_root``,
``cwd``) and may reveal project structure to anyone with access to the log
directory (``~/.local/state/pxx/sessions/``). Consider this when sharing
session logs or if the machine is multi-user.

**Privacy contract** (shared responsibility):

- No prompts or model responses (aider's history files own those)
- No file contents or diffs (git owns those)
- No env vars matching ``*TOKEN*`` / ``*KEY*`` / ``*SECRET*`` / ``*PASSWORD*``
  (callers must validate their record construction; use :func:`is_sensitive_env`
  to test whether a string matches sensitive patterns, e.g. in unit tests)

Retention policy:

- Files older than ``PXX_LOG_RETENTION_DAYS`` (default 90) are deleted
- Files older than 30 days are gzipped (``.jsonl`` → ``.jsonl.gz``)
- Both passes are idempotent and cheap (one directory scan)
"""

from __future__ import annotations

import gzip
import json
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

GZIP_AFTER_DAYS = 30
DEFAULT_RETENTION_DAYS = 90
SENSITIVE_ENV_PATTERNS = ("TOKEN", "KEY", "SECRET", "PASSWORD")


def log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "pxx" / "sessions"


def todays_log_file(directory: Path | None = None) -> Path:
    directory = directory or log_dir()
    return directory / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def make_session_id() -> str:
    # Time-prefixed so a lexical sort matches a chronological sort. The 4-hex
    # suffix disambiguates same-second invocations (cron, scripted runs).
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{secrets.token_hex(2)}"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def is_sensitive_env(name: str) -> bool:
    """True if an env var name matches any sensitive pattern (case-insensitive).

    Exposed for callers that want to audit their own record-construction
    code, e.g., in unit tests asserting no sensitive keys are passed in.
    Not called internally; privacy contract enforced at the record-construction
    site in write_session_start() (callers must not pass sensitive keys).
    """
    upper = name.upper()
    return any(p in upper for p in SENSITIVE_ENV_PATTERNS)


def _scrub_url(url: str) -> str:
    """Remove credentials from a URL (user:password@host -> host).

    Handles Basic auth (user:password) in URLs. Bearer tokens should never
    appear in URLs (they belong in headers); if they do, a different scrubbing
    approach would be needed. This function covers Ollama/vLLM endpoints which
    use header-based auth, not query-param auth.
    """
    if not url:
        return url
    # Match scheme://[user:password@]host[:port][/path]
    match = re.match(r"^([a-z]+://)((?:[^@]+@)?)(.+)$", url, re.IGNORECASE)
    if match:
        scheme, credentials, host_part = match.groups()
        return scheme + host_part
    return url


def write_session_start(record: dict, log_path: Path | None = None) -> Path:
    """Append a ``session_start`` record to today's log file.

    Creates the log directory and file if needed. Fills in defaults for
    ``event``, ``ts``, and ``session_id`` if the caller didn't.
    Scrubs credentials from endpoint_url before logging.

    Returns the path written to. Raises on filesystem errors — callers
    should suppress (the audit log failing must not abort pxx startup).
    Respects PXX_AUDIT_DISABLE=1 to skip logging on shared machines.
    """
    # Skip audit logging if explicitly disabled (e.g., shared machines, privacy)
    if os.environ.get("PXX_AUDIT_DISABLE") == "1":
        return Path()

    path = log_path or todays_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    record.setdefault("event", "session_start")
    record.setdefault("ts", now_iso())
    record.setdefault("session_id", make_session_id())
    # Scrub credentials from endpoint_url to prevent leakage
    if "endpoint_url" in record:
        record["endpoint_url"] = _scrub_url(record["endpoint_url"])
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    return path


def last_session_head_for(
    repo_root: str,
    directory: Path | None = None,
) -> str | None:
    """Return ``git_head_sha`` from the most recent ``session_start`` record
    whose ``git_repo_root`` matches ``repo_root`` (#008 M2).

    Scans ``directory`` (default: :func:`log_dir`) in reverse chronological
    order, reading uncompressed ``.jsonl`` files only. Stops at the first
    matching record with a non-null ``git_head_sha``. Returns ``None`` when
    the directory is missing, no record matches, or any line is unreadable
    (best-effort — the banner check that calls this must never abort startup).
    """
    directory = directory or log_dir()
    if not directory.exists():
        return None
    files = sorted(
        (f for f in directory.iterdir() if f.is_file() and f.suffix == ".jsonl"),
        reverse=True,
    )
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") != "session_start":
                continue
            if rec.get("git_repo_root") != repo_root:
                continue
            sha = rec.get("git_head_sha")
            if sha:
                return sha
    return None


def prune_old_logs(
    retention_days: int | None = None,
    gzip_after_days: int = GZIP_AFTER_DAYS,
    directory: Path | None = None,
) -> tuple[int, int]:
    """Prune old log files: gzip ones older than ``gzip_after_days``,
    delete ones older than ``retention_days``.

    Defaults: ``retention_days`` reads from ``PXX_LOG_RETENTION_DAYS`` env
    (or 90 if absent); ``directory`` reads from ``log_dir()``.

    Idempotent — running twice on the same state is a no-op the second
    time. Returns ``(gzipped_count, deleted_count)`` for testability.
    Silently no-ops if the directory doesn't exist (first run).
    """
    if retention_days is None:
        retention_days = int(
            os.environ.get("PXX_LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
        )
    directory = directory or log_dir()
    if not directory.exists():
        return (0, 0)

    now = time.time()
    gzipped = 0
    deleted = 0

    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        age_days = (now - entry.stat().st_mtime) / 86400
        if age_days > retention_days:
            entry.unlink()
            deleted += 1
        elif age_days > gzip_after_days and entry.suffix == ".jsonl":
            gz_path = entry.with_suffix(".jsonl.gz")
            with entry.open("rb") as src, gzip.open(gz_path, "wb") as dst:
                dst.write(src.read())
            entry.unlink()
            gzipped += 1

    return (gzipped, deleted)


def log_router_event(
    event_type: str,
    status: bool = True,
    usage: dict | None = None,
    log_path: Path | None = None,
) -> None:
    """Log 9router event (start, stop, status) with usage stats.

    Args:
        event_type: 'router_start', 'router_stop', 'router_status'
        status: True if operation succeeded
        usage: Dict with 'total_tokens' and 'total_cost' from /v1/usage
        log_path: Custom log path (default: today's log)
    """
    if os.environ.get("PXX_AUDIT_DISABLE") == "1":
        return

    path = log_path or todays_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "event": event_type,
        "ts": now_iso(),
        "status": status,
    }
    if usage:
        record["usage"] = usage

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def log_memory_event(
    event_type: str,
    status: bool = True,
    data: dict | None = None,
    log_path: Path | None = None,
) -> None:
    """Log agentmemory event (start, stop, observe, inject).

    Args:
        event_type: 'memory_start', 'memory_stop', 'memory_observe', 'memory_inject'
        status: True if operation succeeded
        data: Dict with context-specific data (e.g., observations_count, tokens_used)
        log_path: Custom log path (default: today's log)
    """
    if os.environ.get("PXX_AUDIT_DISABLE") == "1":
        return

    path = log_path or todays_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "event": event_type,
        "ts": now_iso(),
        "status": status,
    }
    if data:
        record.update(data)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
