# Dogfooding Tier 1 — `--self-test` and `--self-lint`

> Backlog ID: **010**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: `—`. Blocked by: `—`. Parent: `#001`.

## Context

`#001 Dogfooding` is the umbrella plan for using pxx to maintain pxx. With
`#002 Safety foundation` and `#003 Scoping & dry-run` both `done`, the
umbrella is unblocked and its phased tier path can start.

Tier 1 in the dogfooding plan is "Observation only — pxx can see itself.
No edits." The plan notes it's "largely already live" because the
reviewer-first default (`cd <pxx-repo> && pxx` in ask mode) already lets a
user read pxx with pxx. What was still missing: **portable health-check
commands** that run the pxx test/lint gate from any cwd on either machine,
without paying for endpoint detection or aider startup.

This plan ships those two commands: `--self-test` and `--self-lint`.

## The two flags

### F1 — `pxx --self-test`

Runs `uv run pytest -q` against the pxx repo regardless of where it's invoked
from. Returns the pytest exit code (Unix-style). Banner + status line go to
stderr so the pytest output on stdout stays clean for piping.

### F2 — `pxx --self-lint`

Runs **both** ruff sub-commands in sequence against the pxx repo:

1. `uv run ruff check .`
2. `uv run ruff format --check .`

Both run every time (no short-circuit on first failure) so every violation
shows up in one pass. Combined exit code is the bitwise OR of the two —
non-zero iff either failed.

### Design decisions

| Choice                | Decision                                                                |
| --------------------- | ----------------------------------------------------------------------- |
| Invocation            | `subprocess.run` with a banner before and a status line after           |
| cwd                   | Always `REPO_ROOT` — these flags mean "test/lint pxx itself"            |
| Exit codes            | Unix-style; propagated cleanly so `||` chains and CI can switch on them |
| Short-circuit point   | Same level as `--list-commands` — before sanity check and endpoint probe |
| Output stream         | Banners on stderr; child stdout/stderr untouched                        |

## Files to modify

| Path                  | Change                                                              |
| --------------------- | ------------------------------------------------------------------- |
| `pxx/cli.py`          | Add `_self_test()`, `_self_lint()`, two short-circuit branches.     |
| `tests/test_cli.py`   | New `TestSelfTest` + `TestSelfLint` classes.                        |
| `README.md`           | New "Self-modes — portable health checks" subsection.               |
| `CLAUDE.md`           | One-line reference under "Using pxx" so future sessions see them.   |

**Existing primitives reused:**

- `pxx/cli.py:REPO_ROOT` — `Path(__file__).parent.parent`, already resolves
  to the pxx repo top-level.
- `--list-commands` / `--install-hook` short-circuit pattern in `main()` —
  mirror it for the new flags.
- `TestListCommandsFlag.test_list_commands_flag_short_circuits_endpoint_detection`
  — same `fake_detect` / `calls` pattern reused for both new short-circuits.

## Verification

| Scenario                                                       | Expected outcome                                            |
| -------------------------------------------------------------- | ----------------------------------------------------------- |
| `cd /tmp && pxx --self-test` (pxx repo green)                  | pytest summary, exit 0                                      |
| `cd /tmp && pxx --self-lint` (pxx repo clean)                  | both ruff sub-commands, exit 0                              |
| `cd /tmp && pxx --self-test` with a broken pxx test            | pytest failures, exit 1 (Unix-style)                        |
| `cd /tmp && pxx --self-lint` with a formatting violation       | combined non-zero; `format=` line distinguishes which failed |
| Short-circuit: `pxx --self-test` does not call `detect_endpoint` | unit test asserts `detect_endpoint` is never invoked       |
| Banners land on stderr, not stdout                             | unit test confirms by capsys split                           |

## Non-goals

- **No `--self-audit`.** The dogfooding plan itself observed `cd <pxx-repo> && pxx`
  already does this via the reviewer-first default; adding a flag would
  duplicate behavior under a new name.
- **No `--self-fix` / autonomous edit loop.** That's Tier 3 (see #001).
- **No `--self-improve` suggestion session.** That's Tier 2.
- **No flag for "test/lint cwd's project".** `--self-` deliberately means
  "the pxx repo." A generic test/lint runner is a different tool.

## Coordination with other plans

- **#001** is the umbrella; this plan is the first concrete tier under it.
  When this lands `done`, #001 stays `in-progress` because Tiers 2/3/4 are
  still pending.
- **Future Tier 2 plan** will add `--self-improve` and inherits the same
  short-circuit pattern.
- **Future Tier 3 plan** uses `#003`'s `--scope` flag and `#002`'s pre-commit
  hook; nothing in Tier 1 constrains those decisions.

## Status updates needed in `backlog.md` when this completes

- `#010` status: born `done` in the same commit as the implementation.
- `#001` status: `planned` → `in-progress` in the same commit (first concrete
  tier of the umbrella has landed; will remain `in-progress` until Tiers 2/3/4
  also land).
- `Next free ID`: bump from `010` to `011`.
