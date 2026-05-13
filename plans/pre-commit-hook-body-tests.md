# Pre-commit hook body test coverage

> Backlog ID: **016**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed**. Blocks: `—`. Blocked by: `—`.
>
> Stub drafted in response to Claude review finding **F-016** (sixth pass,
> 2026-05-13). Partial-resolution of older **F-010** — the installer + the
> prepare-commit-msg hook now have integration tests via
> `tests/test_install_hook.py`, but the pre-commit hook's actual
> validation branches don't.

## Context

After `#012` (Tier 3 autonomous edits), `tests/test_install_hook.py`
covers the hook installer mechanics and the prepare-commit-msg hook's
`[autonomous]` prefix behavior end-to-end via subprocess + real git
commits. Good progress.

What's still uncovered: the **body** of `scripts/pre-commit-template`.
The hook has five distinct branches:

1. `PXX_PRECOMMIT_SKIP=1` emergency bypass
2. `uv run ruff check` gate
3. `uv run pytest -q` gate
4. Diff cap (`PXX_DIFF_CAP`, `PXX_ALLOW_BIG_DIFF`)
5. Scope gate (`PXX_SCOPE` + `pxx-scope-check`)

Each branch is real logic, with non-trivial bash arithmetic (the
`awk`-driven diff size sum), and each one has an "abort with helpful
error" path. None are covered. The next time #002's hook changes —
because the next plan adds another gate, or because the diff cap formula
is tightened — there's no safety net.

The same pattern will recur with every future shell-hook plan, so
landing the test infrastructure now pays off across `#004`, `#015`
(pre-push), and beyond.

## The N mechanisms

### M1 — Subprocess-driven integration tests in pytest

Extend `tests/test_install_hook.py` (or split out to a new
`tests/test_pre_commit_hook.py`) with one test per branch:

- **Emergency bypass:** `PXX_PRECOMMIT_SKIP=1 git commit` exits 0 even
  when pytest would fail. Build a tmp git repo with a broken test file;
  confirm the bypass works.
- **Ruff gate:** introduce a transient ruff violation, attempt commit,
  expect hook exit non-zero with the documented error message.
- **Pytest gate:** introduce a transient failing test, attempt commit,
  expect hook exit non-zero.
- **Diff cap:** stage a >100-line diff with the default cap, attempt
  commit, expect hook abort; then try with `PXX_ALLOW_BIG_DIFF=1`,
  expect pass; then try with `PXX_DIFF_CAP=200`, expect pass.
- **Scope gate:** export `PXX_SCOPE=tests/`, stage a file outside
  `tests/`, attempt commit, expect hook abort.

All tests use a fresh tmp git repo + `_init_repo` helper from the
existing file. Mock `pxx-scope-check` is on PATH (or install pxx via the
dev venv).

### M2 — Decide infrastructure

Two paths:

- **(A) Stay in pytest** — extend `test_install_hook.py` with subprocess
  calls. Pro: one test runner, one test report. Con: each test sets up a
  full tmp repo with pyproject + tests, which is slow (~1-2s per test).
- **(B) Bats fixture in a new `tests/hooks.bats`** — bats is the native
  bash test framework. Pro: idiomatic for shell tests. Con: adds a new
  test runner + dev dep + CI surface; `bash scripts/test_lib.sh` is the
  precedent for shell tests but not pytest-integrated.

**Recommendation:** **(A)**. The existing `test_install_hook.py`
already does subprocess + real-git work; extending it keeps everything
in one runner. The setup overhead is mitigated by a pytest fixture that
yields a pre-initialized repo.

## Verification

| Scenario                                                                | Expected outcome                                                  |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------- |
| All 5 hook branches have at least one test                              | Coverage gap closed                                               |
| Test for "ruff fail" introduces a real lint error and observes abort    | End-to-end, not mocked                                            |
| `PXX_ALLOW_BIG_DIFF=1` test confirms the override actually works        | Documents bypass behavior                                         |
| `PXX_PRECOMMIT_SKIP=1` test confirms the emergency-bypass path          | Mid-incident escape hatch verified                                |
| Scope-gate test exercises the `pxx-scope-check` integration             | Hook ↔ Python helper boundary tested                              |
| Tests are deterministic (no flakes from git timestamps, mtime, etc.)    | Stable in CI / repeated local runs                                |

## Non-goals

- **Not a redesign of the pre-commit hook.** Pure test coverage.
- **Not a coverage tool.** No `bash-coverage` or similar; line-by-line
  bash coverage is not the goal. Branch coverage by behavior is.
- **Not a CI integration.** Tests run via `uv run pytest -q` like
  everything else.

## Open questions

1. **Pytest or bats?** Per M2 recommendation, pytest. Confirm before
   implementing.
2. **Should this be split into a separate `test_pre_commit_hook.py` file?**
   Or stay in `test_install_hook.py`? The file is already 200+ lines.
   Recommend: **separate file** when the pre-commit-body tests would
   roughly double its size.
3. **Coordinate with `#015`?** If `#015` (pre-push hook) lands, the same
   test pattern applies; consider a shared `tests/hooks_helpers.py`
   module instead of duplicating fixtures.

## Status updates needed in `backlog.md` when this completes

- `#016` status: `proposed` → `planned` → `in-progress` → `done`
- No other plans' "Blocks"/"Blocked by" columns change.
