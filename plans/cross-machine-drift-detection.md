# Cross-Machine Drift Detection

> Backlog ID: **006**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed** (stub). Blocks: `—`. Blocked by: `—`.

## Context

pxx is installed `--editable` on both Neo and Studio. Both machines run
their own copy of the code from `/Users/you/ai/code_pro/pxx/`. The
dual-remote git pattern (`git deliver`) and the rsync-from-Neo memory rule
keep them in sync, but those are **operational discipline**, not
mechanical guarantees. After a `pxx --edit` session on Neo, if the user
forgets to rsync or `git deliver`, the Studio runs stale code until next
manual sync.

This becomes operationally critical when dogfooding (#001) starts running
real self-edits. A "successful" autonomous edit on Neo that never reached
Studio is invisibly broken: Neo's tests pass, Neo's behavior changes, but
the Studio's `pxx` install (when called via SSH from the Neo) is still
the old version. Same problem in reverse if the Studio is ever used
directly.

This plan does **not** block #001 — the current rsync discipline + memory
rule cover the gap for human-driven sessions. It materially improves the
safety of #001 Tier 3 (bounded autonomous edits) where the human isn't
actively supervising each session.

## The mechanisms (sketch — to be expanded when fleshed out)

- **`pxx --check-sync`** (standalone subcommand, or new line in
  `doctor.sh`): SSH-runs `git rev-parse HEAD` on the Studio's pxx repo
  and compares to the Neo's local HEAD. One-line report:
  - `✓ Neo and Studio at <sha>`
  - `✗ drift: Neo=<a> Studio=<b>; from Neo run: git deliver && rsync ...`
- **Pre-edit drift check** (optional, opt-in via flag or default-on):
  `pxx --edit` warns when local HEAD doesn't match the Studio's HEAD
  before opening aider, since the about-to-be-proposed edits will land
  on Neo only.
- **Post-session reminder**: when a `pxx --edit` session has made
  commits, print a "git deliver + rsync" reminder at exit. This requires
  the supervisor-process change discussed in #004's deferred items — pxx
  still execs into aider today and has no parent to print at exit.

## Open questions

1. Implicit (auto-run on every `pxx --edit`) or explicit
   (`pxx --check-sync`)? Implicit is safer but adds ~200ms SSH overhead
   per launch. Probably: implicit when on the home network (fast SSH);
   explicit when on VPN (slower); detect via tunable timeout.
2. What's the SSH cost on the Studio side? `git rev-parse HEAD` is
   sub-millisecond once the SSH handshake completes. Connection reuse
   via `ControlMaster` would amortize across a session.
3. Should drift detection check the working-tree state too (uncommitted
   changes on either side), or just HEAD? HEAD-only is simpler; working-
   tree drift is rarer and already partially handled by the
   pre-session-tag/stash from #002.
4. Where does the post-session reminder go if pxx still uses `os.execv`?
   Could be a separate exit-trap script. Or wait for #004 v2's supervisor.

## Verification (placeholders — to be expanded when fleshed out)

| Scenario                                                       | Expected outcome                                                      |
| -------------------------------------------------------------- | --------------------------------------------------------------------- |
| Both machines at the same SHA                                  | Drift check reports OK in one line                                    |
| Neo has a commit the Studio doesn't                            | Reports both SHAs and recommends `git deliver`                        |
| Studio unreachable (off-network)                               | Drift check skips silently with a one-line note; pxx still launches   |
| Studio's pxx repo doesn't exist (fresh machine)                | Reports clearly; suggests running `setup-studio.sh` there             |

## Coordination notes

- **#001 Tier 3 (autonomous edits)** is the strongest motivation for this
  plan. When #001's Tier 3 implementation starts, evaluate whether
  drift-detection-before-each-session should be made a hard precondition.
- **#004 v2 (supervisor process)** unlocks the post-session reminder.
  Without it, the reminder mechanism is awkward.

## Non-goals

- **Two-way sync** — we have git for that.
- **Conflict resolution** — humans handle merges.
- **Real-time monitoring** — this is a pre-/post-session check, not a daemon.
- **More than two machines** — Neo + Studio is the architecture. If a
  third machine joins, generalize then.

## Status updates needed in `backlog.md` when this completes

- `#006` status: `proposed` → `planned` → `in-progress` → `done`
- When `done`, add a note to `#001`'s Tier 3 description recommending
  drift detection before each autonomous session. (Or, if Tier 3 isn't
  yet implemented, fold the recommendation into the workflow doc that
  ships with Tier 3.)
