# Pre-push hook gating `[autonomous]` commits

> Backlog ID: **015**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed**. Blocks: `—`. Blocked by: `—`.
>
> Stub drafted in response to Claude review finding **F-014** (sixth pass,
> 2026-05-13). New drift class: push-time gate gap.

## Context

The pxx repo is currently 12 commits ahead of `origin/main`. Some of those
commits carry the `[autonomous]` prefix from `#012`'s prepare-commit-msg
hook (the autonomous-mode marker). No hook fires at push time, so an
autonomous commit can reach a remote without an explicit review beat.

For a single-developer setup this is intentional — `git deliver` /
`git push` are deliberate user actions, and the user reviews on the way.
But as autonomous sessions become more frequent (and the bar to start one
is now `pxx --self-fix "..." --scope x` per #012), the per-push gap could
matter: a user who batches several `--self-fix` runs may push without
re-reading each one. A pre-push hook would be the belt-and-suspenders
layer — same pattern as the pre-commit hook from `#002`.

The counter-argument worth raising in design: at pxx's scale, manual
discipline may already cover this. The plan should explicitly compare
"add the hook" vs "tighten the manual `git deliver` workflow doc" before
committing to code.

## The N mechanisms

### M1 — Pre-push hook detecting `[autonomous]` commits

A `pre-push` hook installed alongside the existing `pre-commit` and
`prepare-commit-msg` hooks. On `git push`, it walks `HEAD..<remote>/<branch>`
looking for commits whose subject line starts with `[autonomous]`. If any
are found:

- Print a summary (count + first-line of each tagged commit).
- Require an explicit confirmation step before allowing the push.

### M2 — Confirmation mechanism

Two design options:

- **(A) Env-var gated** — refuse unless `PXX_PUSH_AUTONOMOUS=1` is set
  for the push (same idiom as `PXX_ALLOW_BIG_DIFF`). User must `export`
  or one-shot the env to push.
- **(B) Interactive prompt** — read from `/dev/tty` and require "yes" /
  "no". Won't work in non-interactive contexts (CI, scripts) but pxx is
  CLI-only.

**Recommendation:** **(A)** — matches the existing pxx pattern and is
scriptable. Less friction for "yes I reviewed, push them" workflows.

### M3 — Installer integration

Extend `scripts/install-precommit-hook.sh` to drop a third hook
(`pre-push-template` → `.git/hooks/pre-push`). The installer's `HOOKS=`
array already supports adding entries; add one row.

## Verification

| Scenario                                                                  | Expected outcome                                               |
| ------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `git push` with no `[autonomous]` commits in `HEAD..origin/main`          | Push proceeds normally                                         |
| `git push` with one `[autonomous]` commit, no env var                     | Push aborted; message lists the tagged commit; instructs how to override |
| `git push` with `PXX_PUSH_AUTONOMOUS=1`                                   | Push proceeds                                                  |
| `git push --no-verify`                                                    | Hook bypassed (git semantics); document this in the README     |
| `--uninstall` removes the pre-push hook alongside the other two           | Symmetric removal                                              |
| Hook is installed when `setup-neo.sh` / `setup-studio.sh` run             | Default install picks up the third hook                        |

## Non-goals

- **Not a force-push protector.** `git push --force` is a separate
  concern; CLAUDE.md already says don't.
- **Not a remote-specific gate.** The hook fires for any push; design
  doesn't try to differentiate `origin` vs `PS`.
- **Not a `git deliver` change.** `git deliver` is the user's wrapper
  alias; this plan touches the underlying `git push` hook, which
  `deliver` invokes.

## Open questions

1. **Is this worth doing at all?** pxx is single-developer; the user
   already reviews on the way to push. Counter-argument is that autonomous
   commits are exactly the case where the user *might not* review
   carefully. User decision.
2. **What about merge commits with `[autonomous]` parents?** A merge of
   a branch that has tagged commits will have those commits in
   `HEAD..remote`. Probably fine — the hook lists them.
3. **Pair with a `PXX_AUTONOMOUS_PUSHED` audit-log field?** Out of scope
   for v1; would require coordination with `#004`.

## Status updates needed in `backlog.md` when this completes

- `#015` status: `proposed` → `planned` → `in-progress` → `done`
- No other plans' "Blocks"/"Blocked by" columns change.
