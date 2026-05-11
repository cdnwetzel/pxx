# Slash-Command Discoverability

> Backlog ID: **007**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: `—`. Blocked by: `—`.

## Context

pxx ships six slash-command prompt fragments at `pxx/commands/*.md`
(`audit`, `docstring`, `refactor`, `refocus`, `test`, `typecheck`). The
only way to find them today is `ls pxx/commands/` — there's no in-session
listing, no `pxx commands` subcommand, and aider's `/help` doesn't know
about them.

Small daily friction tax: every new session the user either remembers
the exact filename or context-switches to a shell to list them. Surfaced
in `../review/pxx_commands.md` and in observed daily use.

## The mechanisms

### M1 — `pxx --list-commands`

`pxx --list-commands` prints a table of available commands and exits
without launching aider. Each row: name, full path, one-line description
extracted from the first markdown heading (`# /<name> — <desc>`).

Output is filesystem-driven: any new `pxx/commands/*.md` file is picked
up automatically — no code change needed when adding commands.

Format:

```
Available commands (load inside aider via /load <path>):

  /audit       — Read-only review for bugs, unsafe patterns, perf footguns
  /docstring   — Add a concise docstring (only when asked)
  /refactor    — Clarify code; keep behavior identical
  /refocus     — Digest the conversation in a fixed format before /clear
  /test        — Write parametrized pytest tests
  /typecheck   — Tighten type hints toward mypy --strict

Full paths:
  /Users/you/.local/share/uv/tools/pxx/lib/.../pxx/commands/audit.md
  ...
```

### M2 — In-session prompt injection

The same listing is added to aider's read-only context via `--read` of
a generated tempfile so the model can suggest the right `/load` when
the user describes a task. Token cost ≈ 50 per session; negligible.

The tempfile lives at `${TMPDIR}/pxx-commands-<pid>.md` and is removed
on session end.

## Files to modify

| Path                                  | Change                                                                                       |
| ------------------------------------- | -------------------------------------------------------------------------------------------- |
| `pxx/commands_index.py` *(new)*       | Pure module: `list_commands() -> list[CommandInfo]` (name, abs path, description). Uses `dataclass` for `CommandInfo`. |
| `pxx/cli.py`                          | Parse `--list-commands`; route to `commands_index.print_listing()` then `sys.exit(0)`. For normal launches, generate the tempfile and inject it as `--read`. |
| `tests/test_commands_index.py` *(new)* | Tests: extraction from existing 6 files, first-line parsing (heading vs prose vs empty), empty-directory edge case, missing-file edge case. |
| `README.md`                           | One-paragraph "Discovering commands" section under "Slash commands".                          |
| `CLAUDE.md`                           | Update the slash-commands section to mention `--list-commands` and the auto-injection.        |

**Existing primitives to reuse:**

- `pxx/cli.py:PKG_DIR / "commands"` — known location pattern, copy it.
- `pxx/cli.py:_build_aider_args()` — extend to add a `--read <tempfile>`
  entry; the tempfile is created just before exec'ing aider.
- `tempfile` stdlib — for the per-session command-listing file.

## Implementation order

Three commits, smallest first:

1. **`pxx/commands_index.py` + tests** — pure module, no caller yet.
   Verify the extraction works against the existing six commands and
   the heading-format assumption holds.
2. **`--list-commands` flag in `cli.py`** — wires the printer; pxx exits
   after printing. Small change, easy to verify by eye.
3. **In-session prompt injection** — generate the tempfile, add to
   `_build_aider_args` output. Lower priority; ship M1 first to validate
   the discovery primitive before adding token cost to every session.

## Verification

| Scenario                                                                    | Expected outcome                                                                       |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `pxx --list-commands` from any directory                                    | Table of 6 rows; one per existing command; abs paths printed                           |
| `pxx --list-commands` after adding `pxx/commands/foo.md` with `# /foo — bar` | Seventh row appears with description "bar"; no code change required                    |
| Inside aider after a normal launch, ask "what commands can I load?"         | Model lists them with paste-ready `/load <abs-path>` lines                             |
| `pxx --list-commands` in a non-git directory                                | Works identically — no git dependency                                                  |
| `pxx --list-commands` with `pxx/commands/` containing a malformed file (no heading) | File still listed; description shows `(no description)` and a non-zero count of warnings, no crash |

## Non-goals

- Plugin / extension system for user-added commands. Filesystem-based
  discovery is enough; users add files directly.
- Modifying aider's internal `/help` output or fork-and-add a real
  subcommand mechanism — `/load <path>` is the contract.
- Renaming or restructuring the existing six commands.

## Status updates needed in `backlog.md` when this completes

- `#007` status: `planned` → `in-progress` → `done`
- No cross-plan effects expected.
