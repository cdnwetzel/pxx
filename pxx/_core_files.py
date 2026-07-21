"""Core-file registry for the auto-restart hint (#008).

"Core" means a pxx module whose source code is loaded into the
running aider/pxx process at startup. Edits to these files do not
take effect until the user exits and re-launches — the post-commit
hook (M1) warns at commit time, and ``cli.py``'s launch banner (M2)
confirms after restart.

This is the single source of truth shared by M1 (the bash post-commit
hook) and M2 (the in-Python banner check). M1 imports ``CORE_FILES``
via a small ``python3 -c`` invocation; drift between the two
mechanisms is therefore impossible.

Files that are **not** core (and therefore don't appear here):

- ``pxx/prompts/*.md``     — loaded by aider on next session, not on restart
- ``pxx/commands/*.md``    — same
- ``config/*.yml``         — read by aider, not by pxx itself
- shell scripts, docs      — never imported into the process

Add a new path only when it's a Python module imported by ``pxx.cli``
at startup. Keep paths repo-relative and POSIX (no leading ``./``).
"""

from __future__ import annotations

from pathlib import PurePosixPath

CORE_FILES: tuple[str, ...] = (
    "pxx/_core_files.py",
    "pxx/_git.py",
    "pxx/agent_manifest.py",
    "pxx/audit.py",
    "pxx/cli.py",
    "pxx/commands_index.py",
    "pxx/drift.py",
    "pxx/endpoints.py",
    "pxx/governance.py",
    "pxx/review_gate.py",
    "pxx/safety.py",
    "pxx/scope.py",
    "pxx/self_modes.py",
)


def is_core(path: str) -> bool:
    """True if ``path`` refers to one of the registered core pxx modules.

    Accepts repo-relative paths (``pxx/cli.py``), absolute paths
    (``/path/to/pxx/cli.py``), and paths with trailing slashes
    (``pxx/cli.py/``) — all are normalized before comparison. Comparison
    is suffix-based: an absolute path matches if it *ends with* a core
    path, treating ``/`` as the separator.
    """
    if not path:
        return False
    normalized = PurePosixPath(path.rstrip("/").replace("\\", "/")).as_posix()
    if normalized in CORE_FILES:
        return True
    return any(normalized.endswith("/" + core) for core in CORE_FILES)
