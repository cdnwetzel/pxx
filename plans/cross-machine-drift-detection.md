# Cross-Machine Drift Detection

> Backlog ID: **006**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **done**. Blocks: `—`. Blocked by: `—`.

## Context

pxx is installed `--editable` on both Neo and Studio. Both machines run
their own copy of the code from `/Users/you/ai/code_pro/pxx/`. The
dual-remote git pattern (`git deliver`) and the rsync-from-Neo memory rule
keep them in sync, but those are **operational discipline**, not mechanical
guarantees.

Three failure modes are real today:

- After a `pxx --edit` session on Neo, the user forgets to rsync /
  `git deliver` → Studio runs stale code on the next remote launch.
- During a long aider session that ran `pxx` over the network to the
  Studio, the local Neo install was upgraded (rsync from another node) →
  the Studio is now behind the Neo, and the LAN/VPN endpoint detection
  picks the wrong code path.
- The user makes a quick fix directly on the Studio (rare, but real for
  hot-fixes) and forgets to `git deliver` back to the Neo → Neo is behind.

When #001 dogfooding starts running real self-edits, drift becomes
*invisibly* dangerous: an autonomous edit on Neo that never reached
Studio is broken in a way no test catches (tests pass on the source of
the edit; behavior is wrong on the remote that's still running old code).

This plan adds a one-command check + an optional pre-edit auto-check so
drift becomes visible in seconds rather than after the user notices
something behaving wrong.

## The mechanisms

### M1 — `pxx --check-sync`

A `pxx --check-sync` flag (or `pxx check-sync` subcommand — see open
questions) does:

1. Find the configured remote (`PXX_STUDIO_LAN_URL` first; if it
   resolves but isn't workstation, fall back to a configurable SSH
   target, default `cwetzel@workstation`).
2. Run `ssh <target> "git -C /Users/you/ai/code_pro/pxx rev-parse HEAD"`
   with a 5-second timeout.
3. Compare to local `git rev-parse HEAD`.
4. Print a one-line report.

**Output examples:**

```
✓ Neo and Studio at 4cffc37 (main)
```

```
✗ drift detected:
    Neo:    4cffc37 main
    Studio: 70abb57 main
  From Neo: git deliver && rsync ...
  Or from Studio: cd ~/ai/code_pro/pxx && git pull origin main
```

```
? Studio unreachable (SSH timeout after 5s); skipping drift check
```

Exit codes:

- `0` — in sync, or Studio unreachable (intentional: drift check is
  diagnostic, not gating)
- `1` — drift detected

### M2 — Optional pre-edit auto-check

When `pxx --edit` runs, optionally invoke M1 first. Controlled by:

- **Off by default** in v1 — adds ~200–500ms to every `--edit` launch;
  not always worth it.
- `PXX_AUTOCHECK_DRIFT=1` in `~/.zshrc` opts in.
- `pxx --edit --no-check-sync` forces skip even when env var is set.

If drift is detected:
- **Warn, don't block.** Print the drift message, then continue
  launching aider. The user can choose to abort.

The rationale for warn-not-block: the drift check is informational. A
hard block would create a workflow trap where a flaky VPN prevents
local-only edits.

### M3 — `doctor.sh` integration

Add a "Drift" section to `doctor.sh` so the pre-flight check naturally
includes this signal:

```
=== Drift ===
  Neo HEAD:    4cffc37 main
  Studio HEAD: 4cffc37 main  ✓
```

Reuses `M1`'s logic via a Python entry point that doctor.sh calls.

## Files to modify

| Path                                  | Change                                                                                                  |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `pxx/drift.py` *(new)*                | Pure module: `local_head() -> str`, `remote_head(ssh_target: str, timeout: float) -> str \| None`, `compare(local, remote) -> DriftResult`, `print_report(result)`. |
| `pxx/cli.py`                          | Parse `--check-sync` / `--no-check-sync`. Route `--check-sync` to drift module and exit; optional pre-`--edit` invocation when `PXX_AUTOCHECK_DRIFT=1`. |
| `tests/test_drift.py` *(new)*         | Mocked-subprocess tests for `local_head`, `remote_head` (success + timeout + ssh failure), `compare`, `print_report`. |
| `scripts/doctor.sh`                   | Add a "Drift" section that shells out to `pxx --check-sync --format=brief` (new mini-flag) or equivalent. |
| `README.md`                           | Document `--check-sync` and the optional auto-check env var.                                            |
| `CLAUDE.md`                           | Document in the two-machine section so future agents know to suggest the check after self-edits.        |

**Existing primitives to reuse:**

- `pxx/cli.py:_in_git_repo()` — already runs `git rev-parse`. Generalize
  to `_git_head(cwd=None) -> str | None` and reuse from `drift.py`.
- Subprocess pattern from `_in_git_repo()` — copy the timeout-and-catch
  shape for the SSH call.

## Implementation order

Three commits:

1. **`pxx/drift.py` + tests** — pure module with mocked subprocess; no
   caller wired up yet. Verify all the corner cases (timeout, missing
   ssh key, repo not at expected path on remote, stale local HEAD).
2. **`--check-sync` flag in `cli.py`** — wires the printer; pxx exits
   after printing. End-to-end smoke test against the real Studio.
3. **Optional auto-check + doctor.sh integration** — only after M1 +
   tests are stable. The auto-check is opt-in; doctor.sh integration
   is universal (every doctor run reports drift).

## Coordination notes

- **#001 (Dogfooding) Tier 3** is the strongest motivation for this
  plan. When Tier 3 implementation starts, evaluate whether
  drift-detection-before-each-autonomous-session should be made a
  precondition (probably yes for Tier 3 specifically; still opt-in
  for human-driven `pxx --edit`).
- **#002 (Safety foundation)** pairs naturally — pre-session safety
  tags are created locally on Neo; a drift check confirms the Studio
  knows about commits made under those tags.
- **#004 (Session audit log) v2** could record drift results in the
  session_start entry. Out of scope for #006 itself; flagged for
  later integration.
- **SSH config dependency**: this plan assumes a working SSH path
  from Neo → Studio (key-based auth). Today's setup satisfies this,
  but the plan should fail gracefully if the user's SSH config changes.

## Verification

| Scenario                                                          | Expected outcome                                                                                   |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Both machines at the same SHA                                     | `pxx --check-sync` exits 0 with `✓ ... at <sha>` line                                              |
| Neo has a commit Studio doesn't have                              | Exits 1 with both SHAs and the recommended `git deliver` command                                   |
| Studio has a commit Neo doesn't have                              | Exits 1 with both SHAs and the recommended `git pull origin main` command                          |
| Studio's pxx repo doesn't exist (fresh machine before setup)      | Exits 0 with a clear "Studio's pxx repo not found at <path>; run setup-studio.sh there" message    |
| Studio unreachable (off-network / SSH timeout)                    | Exits 0 with `Studio unreachable; skipping drift check`                                            |
| `PXX_AUTOCHECK_DRIFT=1` then `pxx --edit` from a synced state     | Drift check runs silently; aider launches normally                                                 |
| `PXX_AUTOCHECK_DRIFT=1` then `pxx --edit` from a drifted state    | Drift warning printed; aider launches anyway (warn, don't block)                                   |
| `pxx --edit --no-check-sync` with `PXX_AUTOCHECK_DRIFT=1` set     | Drift check skipped; aider launches normally                                                       |
| Drift check from the Studio (looking at the Neo)                  | Inverse direction works — Studio can also check on Neo (target is configurable)                    |

## Non-goals

- **Two-way sync.** We have git for that. Drift detection only
  *detects*; the user decides what to do.
- **Conflict resolution.** Humans handle merges.
- **Real-time monitoring / daemon.** This is a pre/post-session check,
  not a watcher.
- **More than two machines.** Neo + Studio is the architecture. If a
  third machine joins, generalize then.
- **Detecting working-tree drift** (uncommitted changes on either
  side). HEAD-only is simpler; working-tree drift is rarer and
  partially handled by #002's pre-session stash.

## Status updates needed in `backlog.md` when this completes

- `#006` status: `planned` → `in-progress` → `done`
- When `done`, add a sentence to `#001`'s Tier 3 description
  recommending `pxx --check-sync` before each autonomous session.
- Optionally, if `PXX_AUTOCHECK_DRIFT=1` becomes the recommended
  default, note that in the README's "Modes" section.
