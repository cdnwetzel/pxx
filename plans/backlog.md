# Backlog

Master inventory of pxx planning docs. Each plan gets a stable numeric ID that
is **never reused**, even if a plan is cancelled.

## Workflow for adding a new plan

1. Pick the next free ID from **"Next free ID"** at the bottom of this file.
2. Scan the table below to make sure an existing plan doesn't already cover
   the idea. If one does, expand that plan instead of creating a duplicate.
3. Copy [`_template.md`](_template.md) to `plans/<slug>.md` (filename is
   just the slug; the ID lives in the header block).
4. Fill in the title, ID, status (`proposed` for new stubs, `planned` once
   fleshed out), and the dependency columns.
5. Add a row to the Plans table below with the same ID, title, file link,
   status, and dependencies.
6. Bump the "Next free ID" line.
7. If this plan blocks another, or is blocked by one, fill in the dependency
   columns on both ends so the graph stays consistent.

The template documents which sections are required at "proposed" vs
"planned" stages and which are optional. Following it keeps the plans
comparable and scannable.

## Plans

| ID  | Title                                | File                                              | Status   | Blocks   | Blocked by |
| --- | ------------------------------------ | ------------------------------------------------- | -------- | -------- | ---------- |
| 001 | Dogfooding pxx (self-improvement)    | [dogfooding.md](dogfooding.md)                    | blocked  | —        | 002, 003   |
| 002 | Safety foundation                    | [safety-foundation.md](safety-foundation.md)      | planned  | 001      | —          |
| 003 | Scoping & dry-run                    | [scoping-and-dry-run.md](scoping-and-dry-run.md)  | planned  | 001      | —          |
| 004 | Session audit log                    | [session-audit-log.md](session-audit-log.md)      | proposed | —        | —          |

## Status legend

- **proposed** — idea captured; not yet committed to implementation
- **planned** — details locked; awaiting kickoff
- **in-progress** — implementation underway
- **blocked** — cannot proceed; see "Blocked by" column
- **done** — implementation complete and verified
- **cancelled** — dropped; kept here for traceability so the ID isn't reused

## Dependency rules

- "Blocks" and "Blocked by" must be mutually consistent. If plan B depends on
  plan A, A's "Blocks" row contains B and B's "Blocked by" row contains A.
- A plan in **blocked** state has at least one non-**done** ID in its
  "Blocked by" column. Once all blockers move to **done**, the status can
  advance to **planned** or **in-progress**.
- A plan cannot block itself. Cycles are bugs; surface them in PRs.

## Next free ID

`005`
