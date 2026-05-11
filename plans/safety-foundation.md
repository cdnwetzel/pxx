# Safety Foundation

> Backlog ID: **002**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: 001. Blocked by: —.

## Context

Today `pxx --edit` gives aider full edit authority within `.aiderignore`
bounds. The only safety nets are:

- aider's per-edit confirmations (skippable with `--yes-always`)
- git auto-commits in code mode (recoverable, but only with manual `git`
  surgery)
- no automated rollback when tests or lint fail post-edit
- no per-session "undo point" — to revert a whole session the user has to
  remember the SHA from before they started
- no protection against a self-edit that breaks pxx's own ability to launch

The cost of a single bad session can be tens or hundreds of lines of damage
across multiple files, or a broken pxx that won't start. This plan adds four
mechanisms that together make every `--edit` session **safely reversible** and
**defensible against runaway changes**.

This plan blocks #001 (Dogfooding) because without these safety mechanisms,
running pxx against its own code without supervision is irresponsible.

## The four mechanisms

### M1 — Pre-session safety tag

When `pxx --edit` is invoked inside a git repo, BEFORE exec'ing aider:

1. Create a local-only tag `pxx-pre/<unix-ts>` pointing at the current `HEAD`.
2. If the working tree is dirty, run
   `git stash push --include-untracked --message "pxx-pre/<ts>: working state at session start"`.
   The stash is referenced explicitly by name; the user can recover with
   `git stash list` and `git stash show stash@{0}`.
3. Banner adds: `pxx: safety tag pxx-pre/<ts> — undo session with: git reset --hard pxx-pre/<ts>`
4. At session start, prune all `pxx-pre/*` tags older than 30 days (quiet).

If not in a git repo: skip silently (the existing `--no-git` path already
handles this case).

**Namespace `pxx-pre/<ts>` is chosen deliberately:**

- Visually distinct from feature / release tags
- `git deliver` only pushes `main` — tags are never pushed by either remote
  (no `--tags` flag in the alias)
- Easy to filter (`git tag --list 'pxx-pre/*'`) and clean up manually

### M2 — Pre-commit hook

`scripts/install-precommit-hook.sh` writes `.git/hooks/pre-commit` for the
current repo. The hook:

1. Runs `uv run ruff check` — fails fast on lint and syntax errors
2. Runs `uv run pytest -q` — catches behavior regressions
3. Enforces the per-session diff cap (M4 below)

Bypass: `PXX_PRECOMMIT_SKIP=1` for emergencies (documented as a last resort,
not a normal workflow).

**Installation:**

- `pxx --install-hook` — manual, opt-in, per-repo. Refuses to overwrite a
  non-pxx pre-commit hook unless `--force` is passed.
- `scripts/setup-neo.sh` and `scripts/setup-studio.sh` automatically install
  the hook in the pxx repo.

**Assumption:** the target repo uses uv + pyproject.toml + pytest + ruff. The
hook is opinionated. For repos that don't use uv, the user should not run
`pxx --install-hook` — they can write their own hook.

### M3 — Pre-launch self-sanity check

Every `pxx` invocation does, before endpoint detection or anything else:

```python
def _self_sanity_check() -> None:
    """Refuse to launch if pxx's own modules can't be imported.

    Protects against self-modification that left pxx in a broken state.
    Without this, a bad self-edit causes a confusing crash mid-startup.
    """
    try:
        from pxx import endpoints  # noqa: F401
    except Exception as e:
        print(
            f"pxx: own module failed to import: {e}\n"
            "  pxx may have been broken by a self-edit.\n"
            "  Recover with one of:\n"
            "    git -C <pxx-repo> reflog\n"
            "    git -C <pxx-repo> reset --hard <last-known-good>\n"
            "    git -C <pxx-repo> reset --hard pxx-pre/<ts>",
            file=sys.stderr,
        )
        sys.exit(2)
```

The protection is partial: if `cli.py` itself is so broken that the import of
`cli` fails, even this check won't fire. The surface area covered (importing
`endpoints` cleanly) catches the most likely breakage class for self-edits to
the package.

A stronger version (a subprocess sanity check that imports the package from a
clean Python instance) is deferred until v1 proves insufficient.

### M4 — Per-session diff cap

The pre-commit hook (M2) refuses commits where
`git diff --cached --numstat` totals more than `PXX_DIFF_CAP` lines added +
removed.

- Default cap: **100 lines**
- Override: `PXX_DIFF_CAP` env var (e.g., `PXX_DIFF_CAP=300`)
- Bypass: `pxx --edit --big` sets `PXX_ALLOW_BIG_DIFF=1` in the env. The hook
  honors this for the duration of that aider session only.

The cap is the most important guardrail against a runaway session. Even if
the model proposes correct edits, if it proposes *hundreds of lines* of them,
human review is the only way to catch subtle mistakes. The cap forces a
deliberate `--big` opt-in.

## Files to modify

| Path                                          | Change                                                 |
| --------------------------------------------- | ------------------------------------------------------ |
| `pxx/cli.py`                                  | Add `_self_sanity_check`, `_create_safety_tag`, `_prune_old_safety_tags`. Parse `--big`. Update banner. |
| `scripts/install-precommit-hook.sh` *(new)*   | Idempotent installer for `.git/hooks/pre-commit`.       |
| `.git/hooks/pre-commit-template` *(new, in repo)* | Template the installer copies into `.git/hooks/pre-commit`. Tracked so it's reviewable. |
| `scripts/setup-neo.sh`                        | Append `bash scripts/install-precommit-hook.sh` at end. |
| `scripts/setup-studio.sh`                     | Append `bash scripts/install-precommit-hook.sh` at end. |
| `tests/test_cli.py`                           | Tests for `_create_safety_tag` (mocked git), `_prune_old_safety_tags`, `_self_sanity_check` failure path, `--big` flag in `_build_aider_args`. |
| `CLAUDE.md`                                   | Document M1–M4 under a new "Safety net" section.        |
| `README.md`                                   | Add a "Safety net" subsection under "Modes".            |

Existing primitives to reuse:

- `pxx/cli.py:_in_git_repo()` — already detects whether cwd is a git repo
- `pxx/cli.py:_build_aider_args()` — extend to take `--big` and propagate
- `subprocess.run` patterns already in `_in_git_repo` — copy for tag creation

## Implementation order

Three commits, each independently testable:

1. **M1 + M3** (safety tag + self-sanity) — pure pxx-side, no hook dependency. Smallest commit.
2. **M2 + M4** (pre-commit hook + diff cap) — installs `.git/hooks/pre-commit`, adds `--big`, runs the hook against the pxx repo as the verification.
3. **Setup script integration** — make `setup-neo.sh` and `setup-studio.sh` auto-install the hook so fresh machines get it.

After all three land, run pxx through ≥1 week of normal `--edit` use before
considering #001 (dogfooding) unblocked. The goal of that week: catch any
operational pain (annoying prompts, false-positive blocks, slow pre-commit
runs) and tune defaults.

## Verification

| Scenario                                                                | Expected outcome                                                                                            |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `pxx --edit` in a git repo with clean tree                              | Tag `pxx-pre/<ts>` created at HEAD; banner mentions it; aider launches                                      |
| `pxx --edit` with uncommitted changes                                   | Stash created (`pxx-pre/<ts>: ...`); tag still created; banner mentions both                                |
| `pxx --edit` outside a git repo                                         | No tag; banner says `--no-git`; aider launches                                                              |
| Make a commit that breaks `uv run pytest -q`                            | Pre-commit hook rejects; commit not landed                                                                  |
| Make a commit that fails `uv run ruff check`                            | Pre-commit hook rejects; commit not landed                                                                  |
| Make a commit > 100 lines without `--big`                               | Pre-commit hook rejects with explicit message naming `--big`                                                |
| `pxx --edit --big` and commit 200 lines that pass tests + lint          | Allowed                                                                                                     |
| `PXX_PRECOMMIT_SKIP=1` and a failing-test commit                        | Allowed (bypass works — documented as emergency-only)                                                       |
| Manually corrupt `pxx/endpoints.py` (delete a function); run `pxx`      | Sanity check exits 2 with reflog hint                                                                       |
| Run `pxx --edit` 30+ days after a previous session                      | Old `pxx-pre/<ts>` tags from > 30 days ago are pruned silently; new tag created                             |
| `git deliver` after a session                                           | Only `main` is pushed; `pxx-pre/<ts>` tags do not appear on `cdnwetzel/pxx` or `mirror/pxx`                  |

## Open design notes (deferred, but flagged)

- **Auto-revert on circuit-breaker trip** — v1 halts and reports. v2 might
  auto-revert the last commit on syntax error. Defer until we see how often
  the manual revert is annoying.
- **Hook for non-uv projects** — when this plan is generalized to install
  hooks in repos that don't use uv, the hook will need to be parameterized.
  Out of scope for v1; `pxx --install-hook` is opt-in and explicitly
  uv-flavored.
- **Tag pruning to a configurable horizon** — 30 days is hard-coded.
  Could be `PXX_TAG_RETENTION_DAYS` if it ever matters. Defer until it does.

## Status updates needed in `backlog.md` when this completes

- `#002` status: `planned` → `in-progress` → `done`
- `#001` "Blocked by": `002, 003` → `003` (when 002 lands)
- If `#003` also lands first: `#001` "Blocked by": `002, 003` → `002`
- When both 002 and 003 done: `#001` status: `blocked` → `proposed` (ready
  to plan implementation kickoff)
