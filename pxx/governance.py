"""Public-content governance scanner (Phase 0.1).

Deterministic, offline scanning for content that must never leave the
machine: secrets (API keys, tokens, private keys), private IPv4 addresses,
absolute home paths, and denylisted internal hostnames. Used by ``pxx check``
(exit 2 on findings) and by any pre-publish/pre-share gate.

The core scanner (:func:`scan_text`) is pure: it takes content in and returns
:class:`Finding` objects. :func:`scan_content` adds only the I/O of reading
the paths it is handed; :func:`scan_staged` is a thin git edge. Lockfiles and
binary-ish files are skipped; a ``pxx: allow <rule>`` line pragma suppresses
that rule on that line only.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import PxxError

log = logging.getLogger("pxx.governance")

#: Lockfiles are machine-generated and notoriously false-positive heavy.
LOCKFILE_NAMES: frozenset[str] = frozenset({"uv.lock", "package-lock.json", "poetry.lock"})

#: (rule, pattern) pairs for secret-shaped content. Rule names are stable —
#: they are what ``pxx: allow <rule>`` pragmas reference.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "secret-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|token|secret|password)\b"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9/+=._-]{16,}"
        ),
    ),
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "github-token",
        re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{16,}|ghp_[A-Za-z0-9]{16,})\b"),
    ),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
    ),
)

#: Private / link-local IPv4 ranges: 10/8, 192.168/16, 172.16-31/12, 169.254/16.
PRIVATE_IP_PATTERN = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3})\b"
)

#: Absolute home-directory paths ("/Users/<name>", "/home/<name>").
HOME_PATH_PATTERN = re.compile(r"(?<![\w./-])/(?:Users|home)/[A-Za-z0-9._-]+")

#: Line pragma suppressing one rule on one line: ``pxx: allow <rule>``.
PRAGMA_PREFIX = "pxx: allow "

_MAX_PREVIEW = 80


@dataclass(frozen=True)
class Finding:
    """One governance hit. ``line`` is 1-based; ``preview`` is truncated."""

    rule: str
    path: str
    line: int
    preview: str


def _preview(text: str) -> str:
    return text.strip()[:_MAX_PREVIEW]


def _allowed_rules(line: str) -> frozenset[str]:
    """Rules suppressed on this line by a ``pxx: allow <rule>`` pragma."""
    idx = line.find(PRAGMA_PREFIX)
    if idx < 0:
        return frozenset()
    tail = line[idx + len(PRAGMA_PREFIX) :].strip()
    # Pragma runs to end of line; take the first token as the rule name.
    rule = tail.split()[0] if tail else ""
    return frozenset({rule}) if rule else frozenset()


def scan_text(
    text: str, path: str = "<memory>", *, denylist: tuple[str, ...] = ()
) -> list[Finding]:
    """Scan ``text`` for secrets, private IPs, home paths, denylist hosts.

    Pure function: no I/O. ``path`` is only a label carried into findings.
    Denylist entries match as exact words: ``corp.internal`` does not match
    inside ``notcorp.internal`` or ``corp.internal.evil``.
    """
    findings: list[Finding] = []
    deny_res = [
        (host, re.compile(r"(?<![\w.-])" + re.escape(host) + r"(?![\w.-])"))
        for host in denylist
        if host
    ]
    for lineno, line in enumerate(text.splitlines(), start=1):
        allowed = _allowed_rules(line)
        checks: list[tuple[str, re.Pattern[str]]] = [
            *SECRET_PATTERNS,
            ("private-ip", PRIVATE_IP_PATTERN),
            ("home-path", HOME_PATH_PATTERN),
            *(("denylist-host", rx) for _, rx in deny_res),
        ]
        for rule, pattern in checks:
            if rule in allowed:
                continue
            m = pattern.search(line)
            if m:
                findings.append(
                    Finding(rule=rule, path=path, line=lineno, preview=_preview(m.group(0)))
                )
    return findings


def _is_binaryish(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def scan_content(
    paths: tuple[str | Path, ...] | list[str | Path],
    *,
    denylist: tuple[str, ...] = (),
) -> list[Finding]:
    """Read and scan each path. Lockfiles, binary-ish, and unreadable files
    are skipped (logged). No I/O beyond reading the paths passed in."""
    findings: list[Finding] = []
    for p in paths:
        path = Path(p)
        if path.name in LOCKFILE_NAMES:
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            log.warning("governance: skipping unreadable file %s: %s", path, exc)
            continue
        if _is_binaryish(data):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            log.debug("governance: skipping non-utf-8 file %s", path)
            continue
        findings.extend(scan_text(text, str(path), denylist=denylist))
    return findings


def load_denylist(path: Path | None = None) -> tuple[str, ...]:
    """Load internal hostnames from ``~/.config/pxx/public-denylist``
    (one per line, ``#`` comments allowed). Missing/unreadable -> ``()``.
    Telemetry-style helper: best-effort, never raises."""
    target = path or (Path.home() / ".config" / "pxx" / "public-denylist")
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    return tuple(
        stripped for raw in lines if (stripped := raw.strip()) and not stripped.startswith("#")
    )


def scan_staged(*, cwd: Path | None = None, denylist: tuple[str, ...] = ()) -> list[Finding]:
    """Scan files staged in git (``git diff --cached --name-only``).

    FAIL-CLOSED: raises :class:`PxxError` when the staged fileset cannot be
    determined (not a git repository, git missing, index/lock error) — a
    scan that cannot run is NOT a clean scan, and must never read as one.
    Rename detection is disabled so a staged rename can't hide its source.
    Staged files that no longer exist on disk are skipped by
    :func:`scan_content`'s unreadable-file handling."""
    root = cwd or Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--no-renames"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PxxError(f"governance: cannot scan staged files: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        reason = detail[0] if detail else f"git exited {proc.returncode}"
        raise PxxError(f"governance: cannot scan staged files: {reason}")
    names = [n for n in proc.stdout.splitlines() if n.strip()]
    paths = [root / name for name in names]
    return scan_content(paths, denylist=denylist)
