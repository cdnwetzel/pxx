# Dogfooding Tier 3 — bounded autonomous edits

> Backlog ID: **012**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **planned**. Blocks: `—`. Blocked by: `—`. Parent: `#001`. Siblings: `#010` (Tier 1, done), `#011` (Tier 2, done).

## Context

`#001 Dogfooding` walks five tiers from "observation only" to "bounded
autonomy." Tier 1 (#010, done) added portable health checks. Tier 2
(#011, done) added a suggest-only review session. Tier 3 is the first
tier where **pxx makes changes to pxx with minimal supervision** — small,
reversible, gated edits inside a single module.

The umbrella plan was explicit that Tier 3 reuses #002's and #003's
mechanisms rather than reinventing them:

- **#002** provides the pre-session safety tag, the pre-commit hook
  (ruff + pytest + 100-line diff cap), and the self-sanity import check.
- **#003** provides `--scope <path>` (prompt directive + `PXX_SCOPE` env
  read by the hook) and the `--anywhere`-gated trusted-paths config.

What Tier 3 itself adds is a *workflow* on top: a one-flag entry-point
that opens an `--edit --scope` session pre-configured for a single
self-improvement task, plus an `[autonomous]` commit-message prefix so
self-driven commits are filterable from manual ones.

**Why now:** with #002 and #003 actually done (both code-and-status, after
today's S3 recovery), every prerequisite mechanism is in place. Tier 3
ships as composition, not new machinery.

## The three additions

### M1 — `pxx --self-fix "<task>" --scope <path>` subcommand

The headline mechanism. A single command that opens a fully-configured
autonomous session targeting one module:

1. Validates required args: `"<task>"` (positional or via `--message`)
   and at least one `--scope <path>` (refuses to run without scope).
2. `cd`s into `REPO_ROOT` (like `--self-improve` and `--self-test`).
3. Sets `PXX_AUTONOMOUS=1` in the env so the commit-msg hook (M2) tags
   the resulting commit.
4. Sets the diff cap to a tighter Tier-3 value (proposal: 60 lines vs
   the default 100) by exporting `PXX_DIFF_CAP=60`. Users can still
   `--big` to bypass.
5. Launches aider in `--edit` mode with the `--scope` enforcement
   already in place from #003. Aider runs one round-trip, commits, exits.
6. After the aider process exits, pxx prints a one-line summary
   (commit SHA + diff stats) and any pre-commit hook output.

**Composition, not re-invention:** every safety layer (safety tag,
diff cap, ruff+pytest gate, scope enforcement, trusted-paths gate) is
already implemented in #002/#003 and fires naturally because we're using
`--edit --scope` underneath. M1 is essentially "`--edit --scope` with a
specific task string, the autonomous env var set, and a tighter cap."

### M2 — `[autonomous]` commit-msg prefix

When `PXX_AUTONOMOUS=1` is set, commits generated during the session get
`[autonomous]` prepended to their first line. Implementation goes in the
existing pre-commit hook (#002) — it's the most reliable place because:

- It fires on every commit attempt in the pxx repo.
- It already reads pxx env vars (`PXX_ALLOW_BIG_DIFF`, `PXX_SCOPE`,
  `PXX_DIFF_CAP`).
- It can modify the commit message via a sibling `prepare-commit-msg`
  hook installed by the same installer script.

Practical mechanism: install a `prepare-commit-msg` hook alongside the
existing `pre-commit` hook. It checks for `PXX_AUTONOMOUS=1` and, if set
and the message doesn't already start with `[autonomous]`, prepends it.
Idempotent — re-running on an already-tagged message is a no-op.

**Why a prefix not a trailer:** Trailers (`Autonomous: true`) are
filterable too, but a one-line `git log --oneline` should show the tag
at a glance. Aider users routinely scan log with `--oneline`; the prefix
is what they'll see.

### M3 — No-push convention (passive)

Tier 3 commits stay on the local branch. `git deliver` / `git push`
remain explicit human actions. This is enforced by **convention, not
code**:

- pxx never runs `git push` autonomously.
- The workflow doc (this plan, and a new README subsection) is explicit
  about the boundary.
- If active enforcement is needed later, a `pre-push` hook can reject
  pushes when HEAD's latest commit has the `[autonomous]` prefix — but
  that's deferred to a follow-up plan, because false positives (user
  manually pushing after reviewing) would be annoying.

## Design decisions (locked unless flagged below)

| Choice                          | Decision                                                                         |
| ------------------------------- | -------------------------------------------------------------------------------- |
| Mode                            | `--edit` always; `--self-fix` is a thin pre-config over `pxx --edit`             |
| cwd                             | Always `REPO_ROOT`                                                               |
| Scope                           | **Required** — `--self-fix` without `--scope` exits 2 with a clear message       |
| Diff cap                        | Tighter than default — 60 lines per commit; `--big` bypass still honored          |
| Commit-msg mechanism            | `prepare-commit-msg` hook (sibling of existing pre-commit hook), env-var gated   |
| Trusted-paths gate              | Honored — `--self-fix` outside trusted prefixes needs `--anywhere`               |
| Safety tag                      | Fires automatically (pxx repo has commits; #002 M1 path applies)                  |
| Push                            | Never autonomous; convention only in v1                                          |
| Task input                      | Positional arg OR `--message` (existing aider flag)                              |

## Open design choices (need user input)

Three real choices; the rest is independent of them.

1. **Commit-message tag format.** Pick one — and it's hard to change later
   without rewriting commit history:
   - **(A) `[autonomous]`** — explicit, unambiguous, slightly verbose
   - **(B) `[self]`** — shorter, but ambiguous ("self" what? self-test?)
   - **(C) `[pxx]`** — short and tool-named, but collides with the
     common convention of prefixing commits with the tool/component name
     (people manually write `pxx: fix X` already)

   **Recommendation:** **A**. Verbose-but-clear beats short-but-ambiguous,
   and `git log --grep '^\[autonomous\]'` is the obvious filter.

2. **Does v1 ship `--self-fix`, or just M2 (commit tag) + a workflow doc?**
   - **(A) Ship `--self-fix` in v1** — full headline feature, ~50 LOC in
     `cli.py` + tests. Worth it because the workflow doc alone is just
     paperwork; the flag is what makes the workflow ergonomic.
   - **(B) Ship M2 + workflow doc only** — leave `--self-fix` as a
     follow-up. The user runs `PXX_AUTONOMOUS=1 PXX_DIFF_CAP=60 pxx --edit
     --scope <path> --message "<task>"` manually until the convention proves
     itself worth a flag.

   **Recommendation:** **A**. The reason for tiers is that each one should
   be ergonomic enough to actually use. A workflow doc without a flag
   means the user types a 70-char incantation every time. The flag is the
   ergonomics.

3. **Diff cap default for autonomous mode.**
   - **(A) 60 lines** — significantly tighter than the 100-line default,
     reflecting "one focused change."
   - **(B) Same as default (100)** — autonomous mode inherits the same
     cap; if you want tighter, set `PXX_DIFF_CAP=60` yourself.
   - **(C) 40 lines** — even tighter; some autonomous-agent literature
     uses 50-line caps as a sanity ceiling.

   **Recommendation:** **A** (60). The point of tier 3 is small focused
   changes; tighter than 100 enforces the intent without being so tight
   that legitimate small features (like a 1-method-+-test addition)
   trip it. `--big` bypass still works.

## Files to modify

| Path                                    | Change                                                                                                  |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `pxx/cli.py`                            | Add `_self_fix_setup()` helper + a short-circuit branch in `main()`. Validates `--scope`, sets env vars, falls through to the existing `--edit --scope` path. ~50 LOC. |
| `scripts/.git-hooks/prepare-commit-msg` *(new)* | Read `PXX_AUTONOMOUS` env var; prepend `[autonomous]` to the first line of `$1` (commit message file). Idempotent.                                                |
| `scripts/install-precommit-hook.sh`     | Install the new `prepare-commit-msg` hook alongside the existing `pre-commit` hook. Symmetric --uninstall.                                                              |
| `scripts/setup-neo.sh`, `setup-studio.sh` | Run `--install-hook` so both hooks land on fresh installs (already done for pre-commit; just needs to cover the new sibling).                                          |
| `tests/test_cli.py`                     | New `TestSelfFixFlag` class — argv detection, required-scope enforcement, env vars set, exits 2 on missing scope, --big bypass interaction. ~70 LOC.                    |
| `tests/test_install_hook.py` *(extend)* | Verify the installer drops both hooks; verify `[autonomous]` prefix is prepended when `PXX_AUTONOMOUS=1` is set; verify idempotence on re-run.                          |
| `README.md`                             | Extend "Self-modes" subsection with the `--self-fix` paragraph + the no-push convention note.                                                                            |
| `CLAUDE.md`                             | One line under "Using pxx" naming the flag and pointing at the autonomous tag convention.                                                                                |

**Existing primitives to reuse (do NOT duplicate):**

- All of #002's safety mechanisms — they fire automatically when
  `--edit` is invoked in a git repo with commits.
- All of #003's scope mechanisms — `_write_scope_context()`,
  `resolve_scopes()`, `format_for_env()`, `extract_scope_args()`,
  the `PXX_SCOPE` env var. `--self-fix` calls these the same way
  `pxx --edit --scope` already does.
- The trusted-paths gate from #003 S3 — `--self-fix` is `--edit` under
  the hood, so the gate fires naturally.
- `_build_aider_args()` — accepts `extra_reads`; `--self-fix` can pass a
  task-context tempfile if needed (probably not — the task string goes
  via `--message`).

## Implementation order

Three commits, smallest first, so each is independently testable:

1. **M2 + installer changes** (~80 LOC + tests). Land the
   `prepare-commit-msg` hook and the installer wiring, with tests proving
   the `[autonomous]` prefix appears when the env var is set and not
   otherwise. No `--self-fix` flag yet — set `PXX_AUTONOMOUS=1` manually
   to test. Lowest-risk commit; the rest builds on it.
2. **M1 — `--self-fix` flag** (~70 LOC + tests). Wire the flag in
   `cli.py`, validate `--scope`, set the env vars, fall through to the
   existing `--edit --scope` path. Tests cover the env-var setting,
   scope-required error, and message passthrough.
3. **Docs + workflow note + backlog cascade** (~30 LOC). README + CLAUDE.md
   subsections; #012 → done in the same commit.

## Verification

| Scenario                                                                              | Expected outcome                                                                              |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `pxx --self-fix "fix typo in cli.py" --scope pxx/cli.py`                              | aider opens, banner shows `mode=edit` + scope context, session makes ≤60-line edit, commit message starts with `[autonomous]` |
| `pxx --self-fix "fix typo"` (no `--scope`)                                            | exit 2 with "pxx: --self-fix requires --scope <path>; refusing to run without explicit scope" |
| `pxx --self-fix "..." --scope pxx/cli.py --big`                                       | diff cap bypassed; commit still tagged `[autonomous]`                                         |
| Manual commit after `--self-fix` session ends, in same shell                          | `PXX_AUTONOMOUS` is unset by then (set only in the child env); manual commit gets NO tag      |
| `PXX_AUTONOMOUS=1 git commit -m "manual test"`                                        | commit gets `[autonomous]` prefix (proves the hook works independently of pxx)                |
| Re-running with an already-tagged message (`[autonomous] foo`)                        | hook is idempotent — single prefix only                                                       |
| `pxx --self-fix "..." --scope tests/` in `/tmp` with trusted-paths config             | trusted-paths gate fires unless `--anywhere` (since `--self-fix` is `--edit` underneath)      |
| Installer drops both `pre-commit` and `prepare-commit-msg` hooks                      | unit test in `tests/test_install_hook.py` confirms both files exist after install             |
| `pxx --install-hook --uninstall`                                                      | removes both hooks symmetrically                                                              |
| `pxx --self-fix "..." --scope pxx/cli.py` — model's edit fails ruff or pytest         | pre-commit hook rejects; no commit lands; no safety-tag pollution                             |

## Non-goals

- **No looping.** `--self-fix` runs **one** session and exits. No `while
  true; pxx --self-fix`. The dogfooding plan is explicit on this.
- **No auto-push.** `git deliver` / `git push` remain user-only actions.
- **No multi-task batching.** One task string per session; for a queue
  of tasks, the user runs multiple sessions explicitly.
- **No cross-repo support.** `--self-fix` targets the pxx repo only;
  generalizing is a different plan.
- **No active no-push enforcement** (e.g., `pre-push` hook) in v1.
- **No state outside git** — task strings live in commit messages and the
  user's shell history; no `~/.pxx/tasks.log` or similar.
- **No modifying governance files.** The existing `.aiderignore` + the
  CONVENTIONS.md rule already prevent edits to `aider.conf.yml`,
  `model-settings.yml`, install scripts, etc.

## Coordination with other plans

- **Parent: #001** (Dogfooding umbrella). Stays `in-progress` after
  this lands; Tier 4 (`learnings.md`) is the next tier, materially
  improved by #004 (Session audit log) which is still planned.
- **Hard deps (now satisfied):** #002 (Safety foundation, done) and
  #003 (Scoping & dry-run, done — including S3 trusted-paths).
- **Cross-cuts #002:** the installer script grows a second hook; the
  `--install-hook` flag now installs both. Symmetric `--uninstall`.
- **Future #004 (Session audit log)** will likely capture `--self-fix`
  sessions as a first-class log type — the `PXX_AUTONOMOUS=1` env var
  is exactly the marker #004 needs to differentiate session classes.

## Risks and mitigations

| Risk                                                                  | Mitigation                                                                              |
| --------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Aider commit messages bypass the hook (e.g., empty msg, hook misfires) | `prepare-commit-msg` hook fires on EVERY commit path including `--amend`. Unit-test the empty-msg case; refuse to prepend if msg is empty (let the commit fail naturally for empty messages, as git already does). |
| The 60-line cap rejects legitimate Tier-3 additions                   | `--big` bypass still works. Tighten or loosen via `PXX_DIFF_CAP=<n>` per session.       |
| Autonomous session produces broken code that passes hook              | pxx tests are the safety net (#002 gate). If they're inadequate, `--self-test` after the session catches it; #010 covers this.  |
| `PXX_AUTONOMOUS` leaks into a subsequent manual session in same shell  | Not a real risk in practice (each pxx invocation sets it explicitly via `--self-fix`); document the env var so users know to `unset` it if they exported it manually for testing. |
| Bad autonomous commit reaches the remote                              | No-push convention. If it does, `git revert` and a `[autonomous-revert]` follow-up commit. The reflog + safety tag still provide local recovery. |
| The prefix tag pollutes `git log --oneline` visual density            | Acceptable trade-off: filterability > visual density. Users who want clean log can `git log --invert-grep --grep '^\[autonomous\]'`.            |

## Open lessons from the umbrella plan (Tier 5 still out of scope)

The umbrella plan explicitly puts Tier 5 (full self-evolution) out of
scope; Tier 3 doesn't change that. Tier 3 is "bounded autonomy" — bounded
by scope, diff cap, hook gate, and the single-session non-loop rule.
A Tier-3 session that wants to do more than one thing must be run more
than once, explicitly, by the user.

## Status updates needed in `backlog.md` when this completes

- `#012` status: `planned` → `in-progress` → `done` (across the three
  implementation commits above).
- `#001` status: stays `in-progress` (Tier 4 still pending; #004 not yet
  done so Tier 4 quality is reduced anyway).
- `Next free ID`: bump from `012` to `013`.
- `#004 Session audit log` description gets a one-line cross-reference:
  "consumes `PXX_AUTONOMOUS=1` as a session-class marker."
