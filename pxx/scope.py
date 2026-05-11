"""Path-prefix scope handling for the pxx --scope flag (#003 S1).

A "scope" is a set of repo-relative directory or file prefixes. When the
user passes ``--scope <path>``, the session is restricted to edits under
those prefixes. Enforcement happens in three layers:

1. Prompt directive (always on) — injected as a ``--read`` context file.
2. Banner output (always on) — clear visibility of what's restricted.
3. Pre-commit hook gate (when installed) — reads ``PXX_SCOPE`` env var
   and rejects commits touching out-of-scope files.

This module is pure / testable: no I/O on cwd or git beyond what's
passed in. The cli.py module composes it with real git state.
"""

from __future__ import annotations

import sys
from pathlib import Path


def extract_scope_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Consume ``--scope <path>`` and ``--scope=<path>`` from argv.

    Returns ``(scope_values, remaining_argv)``. Malformed ``--scope`` at
    end of argv with no value is dropped silently.
    """
    scopes: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--scope":
            if i + 1 < len(argv):
                scopes.append(argv[i + 1])
                i += 2
            else:
                # malformed --scope with no value; drop the flag
                i += 1
        elif a.startswith("--scope="):
            scopes.append(a[len("--scope=") :])
            i += 1
        else:
            remaining.append(a)
            i += 1
    return scopes, remaining


def resolve_scopes(
    scope_paths: list[str],
    repo_root: Path,
    cwd: Path | None = None,
) -> list[str]:
    """Normalize user-supplied scope paths to repo-relative posix strings.

    - Absolute paths must be under ``repo_root``.
    - Relative paths are resolved against ``cwd`` (defaults to
      ``Path.cwd()``).
    - Trailing slash on the input is preserved as a directory hint
      (matters for ``is_in_scope`` matching).
    - Scope = repo root (``""`` after resolution) means "everything"
      and is kept as ``""``.

    Raises ``ValueError`` if any path resolves outside ``repo_root``.
    """
    cwd = cwd or Path.cwd()
    repo_root = repo_root.resolve()
    out: list[str] = []
    for raw in scope_paths:
        p = Path(raw)
        abs_p = p.resolve() if p.is_absolute() else (cwd / p).resolve()
        try:
            rel = abs_p.relative_to(repo_root)
        except ValueError as e:
            msg = f"scope path outside repo: {raw} (resolved to {abs_p})"
            raise ValueError(msg) from e
        relstr = rel.as_posix()
        if relstr == ".":
            relstr = ""
        if raw.endswith("/") and relstr and not relstr.endswith("/"):
            relstr += "/"
        out.append(relstr)
    return out


def is_in_scope(filepath: str, scope_prefixes: list[str]) -> bool:
    """True iff ``filepath`` (repo-relative posix) is under any scope prefix.

    Empty ``scope_prefixes`` means "no restriction" → always True.
    Empty string in ``scope_prefixes`` means "repo root" → always True.
    """
    if not scope_prefixes:
        return True
    fp = filepath.lstrip("/")
    for prefix in scope_prefixes:
        p = prefix.lstrip("/")
        if p == "":
            return True  # repo-root scope matches everything
        if p.endswith("/"):
            if fp.startswith(p):
                return True
            if fp == p.rstrip("/"):
                return True
        else:
            if fp == p:
                return True
            if fp.startswith(p + "/"):
                return True
    return False


def format_for_env(scope_paths: list[str]) -> str:
    """Format scope paths as colon-separated for the PXX_SCOPE env var."""
    return ":".join(scope_paths)


def scope_check_main(argv: list[str] | None = None) -> int:
    """CLI entry for the pre-commit hook.

    Exposed as the ``pxx-scope-check`` console script (see
    ``pyproject.toml``) so the hook can invoke it from any working
    directory regardless of whether the host project has pxx in its
    venv — uv tool installs put the script on PATH globally.

    Reads ``PXX_SCOPE`` from env and a list of filenames (one per line)
    from stdin. Prints any out-of-scope files (one per line) to stdout.
    Always exits 0 on valid invocation — the caller (hook) decides
    whether non-empty output should abort the commit.

    Invoke from the hook as:

        git diff --cached --name-only | pxx-scope-check check
    """
    import os

    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] != "check":
        print("usage: python3 -m pxx.scope check  < stdin-list-of-files", file=sys.stderr)
        return 2
    scope_env = os.environ.get("PXX_SCOPE", "")
    if not scope_env:
        return 0
    prefixes = [p for p in scope_env.split(":") if p]
    files = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    out_of_scope = [f for f in files if not is_in_scope(f, prefixes)]
    for f in out_of_scope:
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(scope_check_main())
