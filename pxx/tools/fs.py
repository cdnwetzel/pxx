"""Filesystem tools: read_file, write_file, edit_file, list_files, search_files.

Every path from the model is untrusted input: all of them go through
``ctx.scope.check`` / ``check_write`` (canonicalized, symlink-resolved)
before any I/O. Expected failures (missing file, ambiguous edit, bad regex)
are returned as error strings for the model; gate errors propagate.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from . import ToolContext, ToolSpec, tool_schema

#: Hard cap on lines returned by read_file.
MAX_READ_LINES = 2000
#: Default cap for list_files results.
MAX_LIST_ENTRIES = 200
#: Default cap for search_files matches.
MAX_SEARCH_MATCHES = 50
#: Directories never descended into by list/search.
SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules"})


def _err(msg: str) -> str:
    return f"error: {msg}"


def _numbered(text_lines: list[str], start: int) -> str:
    return "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(text_lines, start=start))


class ReadFile:
    spec = ToolSpec(
        name="read_file",
        description=(
            "Read a file with line numbers. Use offset/limit to page through "
            "large files (at most 2000 lines per call)."
        ),
        parameters=tool_schema(
            {
                "path": {"type": "string", "description": "File path (relative to project root)."},
                "offset": {
                    "type": "integer",
                    "description": "1-based line number to start at (default 1).",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return (default and hard cap 2000).",
                },
            },
            required=["path"],
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.scope.check(str(args.get("path", "")))
        if not path.is_file():
            return _err(f"not a file: {path}")
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            return _err(f"binary file, cannot display: {path}")
        lines = raw.decode(errors="replace").splitlines()
        offset = max(1, int(args.get("offset") or 1))
        if offset > len(lines):
            return _err(f"offset {offset} past end of file ({len(lines)} lines)")
        limit = int(args.get("limit") or MAX_READ_LINES)
        limit = min(limit, MAX_READ_LINES)
        window = lines[offset - 1 : offset - 1 + limit]
        header = f"{path} ({len(lines)} lines total)"
        if offset > 1 or offset - 1 + limit < len(lines):
            header += f" — showing lines {offset}-{offset - 1 + len(window)}"
        return header + "\n" + _numbered(window, offset)


class WriteFile:
    spec = ToolSpec(
        name="write_file",
        description="Write content to a file, creating parent directories. Overwrites existing files.",
        parameters=tool_schema(
            {
                "path": {"type": "string", "description": "File path (relative to project root)."},
                "content": {"type": "string", "description": "Full file content to write."},
            },
            required=["path", "content"],
        ),
        mutating=True,
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.scope.check_write(str(args.get("path", "")), ctx.permission)
        content = str(args.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content)
        await ctx.bus.emit(
            "file_changed",
            {
                "path": str(path),
                "tool": "write_file",
                "action": "modified" if existed else "created",
                "bytes": len(content.encode()),
            },
            session_id=ctx.session_id,
        )
        return f"wrote {path} ({len(content.splitlines())} lines)"


class EditFile:
    spec = ToolSpec(
        name="edit_file",
        description=(
            "Replace an exact string in a file. old_string must match exactly "
            "one location; include enough context to make it unique."
        ),
        parameters=tool_schema(
            {
                "path": {"type": "string", "description": "File path (relative to project root)."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            required=["path", "old_string", "new_string"],
        ),
        mutating=True,
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.scope.check_write(str(args.get("path", "")), ctx.permission)
        old = str(args.get("old_string", ""))
        new = str(args.get("new_string", ""))
        if not old:
            return _err("old_string must not be empty")
        if not path.is_file():
            return _err(f"not a file: {path}")
        text = path.read_text()
        count = text.count(old)
        if count == 0:
            return _err(f"old_string not found in {path}")
        if count > 1:
            return _err(
                f"old_string matches {count} locations in {path}; "
                "add more surrounding context to make it unique"
            )
        path.write_text(text.replace(old, new, 1))
        diff_lines = len(old.splitlines()) + len(new.splitlines())
        await ctx.bus.emit(
            "file_changed",
            {
                "path": str(path),
                "tool": "edit_file",
                "action": "modified",
                "diff_lines": diff_lines,
            },
            session_id=ctx.session_id,
        )
        return f"edited {path} (~{diff_lines} diff lines)"


class ListFiles:
    spec = ToolSpec(
        name="list_files",
        description=(
            "List files under the project matching a glob pattern "
            "(default '**/*'). Skips .git, __pycache__ and node_modules."
        ),
        parameters=tool_schema(
            {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern relative to the project root.",
                    "default": "**/*",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 200).",
                    "default": MAX_LIST_ENTRIES,
                },
            }
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        base = ctx.scope.check(ctx.cwd)
        pattern = str(args.get("pattern") or "**/*")
        limit = int(args.get("limit") or MAX_LIST_ENTRIES)
        try:
            candidates = base.glob(pattern)
            matches: list[str] = []
            truncated = False
            for path in candidates:
                rel_parts = path.relative_to(base).parts
                if any(part in SKIP_DIRS for part in rel_parts):
                    continue
                if not ctx.scope.in_scope(path):
                    continue
                if len(matches) >= limit:
                    truncated = True
                    break
                matches.append(str(Path(*rel_parts)) + ("/" if path.is_dir() else ""))
        except (NotImplementedError, ValueError, OSError) as exc:
            return _err(f"bad pattern {pattern!r}: {exc}")
        matches.sort()
        if not matches:
            return f"no files matching {pattern!r} under {base}"
        out = "\n".join(matches)
        if truncated:
            out += f"\n… truncated at {limit} entries"
        return out


class SearchFiles:
    spec = ToolSpec(
        name="search_files",
        description=(
            "Search file contents with a regex. Uses ripgrep when available, "
            "otherwise a pure-python fallback. Returns 'path:line: match'."
        ),
        parameters=tool_schema(
            {
                "pattern": {"type": "string", "description": "Regular expression to search for."},
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: project root).",
                    "default": ".",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max matches to return (default 50).",
                    "default": MAX_SEARCH_MATCHES,
                },
            },
            required=["pattern"],
        ),
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        pattern = str(args.get("pattern", ""))
        base = ctx.scope.check(str(args.get("path") or "."))
        limit = int(args.get("limit") or MAX_SEARCH_MATCHES)
        if shutil.which("rg"):
            return await self._rg(pattern, base, limit)
        return await self._py(pattern, base, limit)

    async def _rg(self, pattern: str, base: Path, limit: int) -> str:
        proc = await asyncio.create_subprocess_exec(
            "rg",
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--max-count",
            str(limit),
            "--glob",
            "!.git",
            "--glob",
            "!__pycache__",
            "--glob",
            "!node_modules",
            "--",
            pattern,
            str(base),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            return _err("search timed out after 30s")
        if proc.returncode not in (0, 1):  # 1 = no matches
            detail = stderr.decode(errors="replace").strip()[:300]
            return _err(f"rg failed (exit {proc.returncode}): {detail}")
        lines = stdout.decode(errors="replace").splitlines()
        if not lines:
            return f"no matches for {pattern!r}"
        truncated = len(lines) > limit
        out = "\n".join(lines[:limit])
        if truncated:
            out += f"\n… truncated at {limit} matches"
        return out

    async def _py(self, pattern: str, base: Path, limit: int) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return _err(f"invalid regex: {exc}")
        roots = [base] if base.is_file() else sorted(base.rglob("*"))  # noqa: ASYNC240
        matches: list[str] = []
        for path in roots:
            if len(matches) >= limit:
                break
            try:
                rel_parts = path.relative_to(base).parts
            except ValueError:
                rel_parts = path.parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if "\0" in text[:8192]:
                continue  # binary
            for n, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{path}:{n}: {line.strip()[:200]}")
                    if len(matches) >= limit:
                        break
        if not matches:
            return f"no matches for {pattern!r}"
        out = "\n".join(matches)
        if len(matches) >= limit:
            out += f"\n… truncated at {limit} matches"
        return out
