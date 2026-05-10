# Session Audit Log

> Backlog ID: **004**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **proposed** (stub). Blocks: —. Blocked by: —.

## Problem

When a pxx session does something unexpected — wrong file edited, weird
commit, runaway diff — the only forensic evidence is git history plus
aider's chat transcript. That's enough to *eventually* figure out what
happened, but it's slow. A structured, per-session log makes post-mortems
fast and unblocks the Tier 4 "learnings loop" from the dogfooding plan
(#001).

This is parallel to #001 — it doesn't block dogfooding, but it makes
dogfooding much safer to operate in practice.

## Capabilities to design

- **Per-session JSON-lines log**: one file per session at
  `~/.pxx/sessions/<timestamp>-<endpoint>.jsonl`.
- **Lines record**:
  - session start (mode, model, endpoint, cwd, git head, pxx version)
  - files read (path + size, no content)
  - files edited (path + diff stats — added/removed lines)
  - commands aider ran (cmd + exit code + duration)
  - commits created (SHA + message subject)
  - session end (clean exit | aider error | user ctrl-c)
- **Privacy default**: log paths and sizes, **not file contents**. Diff
  stats only. The content is in git already.

## Open questions

1. Where exactly does it live — `~/.pxx/sessions/` (matches dry-run plan
   #003) or XDG `~/.local/state/pxx/sessions/`? Pick one root for all pxx
   state and be consistent.
2. Retention policy — keep forever, or rotate after 90 days?
   Compression for old logs?
3. Should the log be append-only by design (no future "edit-log" command
   to redact)?
4. How is the log produced — does pxx itself write it, or does it shim
   aider's existing event/tool hooks? Shimming aider is fragile; pxx
   wrapping the launch and tailing aider's chat history is simpler.
5. Should we expose a `pxx --log <session-id>` reader command, or just
   document `cat ~/.pxx/sessions/<file>` ? Reader is nicer UX but more code.

## Non-goals

- Trace replay (re-execute a session from the log). Out of scope.
- Centralized log aggregation across machines. Per-machine is fine for a
  solo developer; the dual-remote git already covers cross-machine state.
- Capturing file contents — git already has them; logging them twice
  wastes disk and risks leaking sensitive content.

## Verification

- Run a session that edits two files and makes one commit. The session log
  must contain entries for both files (with diff stats) and the commit SHA.
- Crash a session mid-edit (`kill -9`). The log must show session-start
  but no session-end record, making the abnormal exit obvious.
- Confirm no file contents appear in the log under any path.
