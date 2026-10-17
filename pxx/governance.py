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
from .gitenv import INTERNAL_GIT, git_env

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
    """Scan what is STAGED in git -- each changed index entry's blob, i.e. the
    exact bytes a commit of the index would record.

    FAIL-CLOSED: raises :class:`PxxError` when the staged fileset cannot be
    determined (not a git repository, git missing, index/lock error) or a
    staged blob cannot be read -- a scan that cannot run is NOT a clean scan,
    and must never read as one. Rename detection is disabled so a staged
    rename can't hide its source; deletions have no blob and are not read.

    Entries come from ``git diff-index --cached -z`` -- NUL-separated names
    (never quoted, never stripped) with each entry's blob SHA -- and blobs
    are read BY SHA with replace refs ignored (2.9.1 reviews): a quoted name
    was "unreadable" and skipped, a clean filter or a `git replace` could
    show the scan different bytes from the commit, and a name like
    ``0:notes.txt`` parsed as an index-stage revision of another file."""
    root = cwd or Path.cwd()
    head = _git_bytes_or_none(root, "rev-parse", "--verify", "-q", "HEAD")
    entries: list[tuple[str, str]] = []
    if head:
        raw = _git_bytes(root, "diff-index", "--cached", "-z", "--no-renames", "HEAD")
        fields = raw.decode("utf-8", "surrogateescape").split("\0")
        # ":<old mode> <new mode> <old sha> <new sha> <status>" NUL "<path>" NUL
        for meta, name in zip(fields[0::2], fields[1::2], strict=False):
            parts = meta.lstrip(":").split()
            if len(parts) == 5 and parts[4] != "D":
                entries.append((name, parts[1] + " " + parts[3]))
    else:  # no commit yet: everything in the index is staged
        raw = _git_bytes(root, "ls-files", "-s", "-z")
        for record in raw.decode("utf-8", "surrogateescape").split("\0"):
            meta, _, name = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and name:
                entries.append((name, parts[0] + " " + parts[1]))
    return scan_blobs(
        root,
        [(name, ms.split()[1]) for name, ms in entries if not ms.startswith("160000")],
        denylist=denylist,
    )


def scan_blobs(
    root: Path, blobs: list[tuple[str, str]], *, denylist: tuple[str, ...] = ()
) -> list[Finding]:
    """Scan git blobs BY SHA -- ``[(repo-relative name, blob sha)]`` -- with
    replace refs ignored, so the bytes scanned are the bytes a commit built
    from those SHAs records. Lockfiles, binary-ish and non-UTF-8 blobs are
    skipped (as for files); an unreadable blob raises :class:`PxxError`
    (fail-closed). The name is only a label and an attribute key -- never
    parsed as a revision."""
    findings: list[Finding] = []
    for name, sha in blobs:
        if Path(name).name in LOCKFILE_NAMES:
            continue
        data = _git_bytes(root, "cat-file", "blob", sha)
        if _is_binaryish(data):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            log.debug("governance: skipping non-utf-8 blob %s", name)
            continue
        findings.extend(scan_text(text, str(root / name), denylist=denylist))
    return findings


def _git_bytes_or_none(root: Path, *args: str) -> bytes | None:
    try:
        return _git_bytes(root, *args)
    except PxxError:
        return None


def _git_bytes(root: Path, *args: str) -> bytes:
    """Run git; return raw stdout. Raises PxxError on any failure (fail-closed)."""
    try:
        proc = subprocess.run(
            # INTERNAL_GIT: the scan must see the real objects, and must not
            # run repository-controlled code
            [*INTERNAL_GIT, *args],
            cwd=root,
            capture_output=True,
            timeout=15,
            env=git_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PxxError(f"governance: cannot scan staged files: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        reason = detail[0] if detail else f"git exited {proc.returncode}"
        raise PxxError(f"governance: cannot scan staged files: {reason}")
    return proc.stdout
