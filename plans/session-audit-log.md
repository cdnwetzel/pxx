# Session Audit Log

> Backlog ID: **004**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **planned**. Blocks: `—`. Blocked by: `—`.

## Context

When a pxx session does something unexpected — wrong file edited, weird
commit, runaway diff, mysterious crash — the only forensic evidence today
is git history plus aider's per-project chat history (`.aider.chat.history.md`,
`.aider.input.history`, `.aider.llm.history`). Those are eventually enough,
but:

- They're per-project, not centralized — you have to know which repo to
  look in.
- They mix user prompts and model output, so finding "what files did this
  session edit?" requires reading prose.
- They contain no structured metadata (endpoint, mode, scope, model version,
  git HEAD at session start).

This plan adds a thin, structured audit log that lives outside any repo, is
the same shape on both machines, and is cheap to query. It does **not**
duplicate aider's conversational records — it indexes into them.

This plan does **not** block #001 (Dogfooding) directly, but it materially
improves the post-mortem experience once dogfooding starts. It also feeds
Tier 4 of #001 (the "learnings loop" — observations grepped from past
sessions and fed back into the system prompt).

## The two mechanisms

### M1 — Session manifest (JSON-lines)

Every `pxx` invocation, before exec'ing aider, appends one JSON-lines
record to:

```
${XDG_STATE_HOME:-$HOME/.local/state}/pxx/sessions/YYYY-MM-DD.jsonl
```

One file per day; daily rotation is automatic. Each session record is a
single JSON object:

```json
{
  "event": "session_start",
  "ts": "2026-05-10T18:42:09.123-04:00",
  "session_id": "20260510T184209-9b3a",
  "pxx_version": "0.1.0",
  "mode": "ask",
  "model": "ollama_chat/devstral:24b",
  "endpoint_name": "studio_lan",
  "endpoint_url": "http://workstation:11434",
  "cwd": "/Users/you/ai/code_pro/pxx",
  "git_repo_root": "/Users/you/ai/code_pro/pxx",
  "git_head_sha": "70abb57...",
  "git_dirty": false,
  "scope": [],
  "edit_mode": false,
  "dry_run": false,
  "big": false,
  "aider_history_path": ".aider.chat.history.md"
}
```

**What is logged:**

- All pxx-level state at launch (mode, model, endpoint, flags)
- Repo identity (root path + HEAD SHA + dirty flag)
- The path to aider's per-project history file, so a reader can jump
  from the audit log into the conversation when needed
- Scope (from #003) if set
- `big` (from #002) if set

**What is NOT logged, ever:**

- File contents (git already has them; logging twice wastes disk and
  risks leaking sensitive content)
- Model prompts or responses (aider's history files own that)
- Bearer tokens, API keys, or anything from env vars matching
  `*TOKEN*`, `*KEY*`, `*SECRET*`, `*PASSWORD*`
- File diffs (those are in git via the session_start `git_head_sha`)

**Session end is intentionally implicit in v1.** pxx uses `os.execv` so
there's no parent process to write a session_end record. The next pxx
invocation can compare current `git_head_sha` to the previous session's
HEAD to *infer* what changed. A future v2 may switch from `execv` to a
supervisor process (`subprocess.run`) to capture exact end timestamps and
exit status — see "Open design notes."

### M2 — Retention and rotation

- **Files older than 90 days** (configurable via `PXX_LOG_RETENTION_DAYS`)
  are deleted at session start.
- **Files older than 30 days** are gzipped at session start.
- Both passes are cheap (one directory scan per launch) and idempotent.

No reader command in v1 — `cat`, `jq`, and `grep` are good enough for the
post-mortem use case. A `pxx --log [N]` reader is deferred until the user
notices they're typing the same `jq` query repeatedly.

## Files to modify

| Path                    | Change                                                                                                          |
| ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| `pxx/cli.py`            | Call `audit.write_session_start()` after endpoint detection + arg building, before `os.execv`. ~5 lines.        |
| `pxx/audit.py` *(new)*  | Pure module: `write_session_start(record: dict)`, `prune_old_logs()`, `_log_dir()`, `_redact_env()`. ~60 lines. |
| `tests/test_audit.py` *(new)* | Tests for path resolution (XDG fallback), record shape, retention math, env redaction.                    |
| `CLAUDE.md`             | Document the log location, what is and is not logged, retention.                                                |
| `README.md`             | One-line pointer under "Pre-flight check" so users know the log exists.                                          |

**Existing primitives to reuse:**

- `pxx/cli.py:_in_git_repo()`, expand to a small `_repo_root()` helper —
  needed for `git_repo_root` in the record. Likely same shape as a
  `git rev-parse --show-toplevel` call.
- `pxx/endpoints.py:Endpoint` — already carries `name` and `url`.
- `pxx/cli.py:_build_aider_args()` arg-parsing pattern — copy for `dry_run`
  / `scope` / `big` once those exist (don't pre-build it here; wire the
  record fields when the relevant plans land).

## Implementation order

Three commits, smallest first:

1. **`pxx/audit.py` + tests** — pure module with no caller. Land it, prove
   the path resolution and record-shape logic are right, before anyone
   imports it.
2. **Wire into `cli.py`** — call `write_session_start()` after arg
   construction, before `os.execv`. Add fields the record cares about
   (mode, endpoint, etc.). Smoke-test by running `pxx --help` and
   inspecting the new JSONL entry.
3. **Retention pass** — add `prune_old_logs()` and call it from `cli.py`.
   Verify the gzipping is idempotent and the deletion threshold is
   correctly applied to a synthetic old file in tests.

## Coordination with other plans

- **#001 (Dogfooding) — Tier 4 learnings loop.** The audit log is the
  natural input to `learnings.md`. When Tier 4 lands, it will grep this
  log for patterns ("model frequently forgets X", "circuit breaker trips
  most often on file Y") and propose distilled lessons.
- **#002 (Safety foundation) — circuit breaker.** When the pre-commit
  hook rejects a commit, that fact isn't directly captured here (the
  rejection happens in git, not pxx). But the next session_start record
  shows whether a commit was actually made, so a "circuit breaker
  tripped → next session" pattern is visible by comparing consecutive
  records' `git_head_sha`.
- **#003 (Scoping & dry-run) — record enrichment.** The `scope`,
  `dry_run` flags will be live in the record only after #003 lands. Until
  then they're recorded as `[]` and `false` respectively (the values pxx
  has anyway).

None of these are hard dependencies — #004 ships fine standalone with
empty/false defaults for fields owned by future plans.

## Verification

| Scenario                                                              | Expected outcome                                                                                                         |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| First-ever `pxx` invocation on a machine                              | Log dir created at `$XDG_STATE_HOME/pxx/sessions/`; today's `.jsonl` file created with one `session_start` record         |
| Run `pxx` twice in same day                                           | Same `.jsonl` file gains a second line; no new file created                                                              |
| Run `pxx` across midnight UTC                                         | Second invocation writes to next day's file (date-rolled filename)                                                       |
| `pxx --edit --scope tests/ --big` (after #002 + #003 land)            | Record contains `"edit_mode": true`, `"scope": ["tests/"]`, `"big": true`                                                |
| Inspect a record — does it ever contain file content or LLM output?   | No. Only metadata, paths, sizes, SHAs. Grep confirms.                                                                    |
| Env contains `OPENAI_API_KEY=secret`; run `pxx`                       | Record contains no value from any `*KEY*`/`*TOKEN*`/`*SECRET*` env var                                                   |
| Synthetic 100-day-old log file present at launch                      | File deleted on next session start                                                                                       |
| Synthetic 45-day-old log file present at launch                       | File gzipped (`.jsonl` → `.jsonl.gz`) on next session start                                                               |
| Synthetic 5-day-old log file present at launch                        | File untouched                                                                                                           |
| `pxx` invocation crashes during endpoint detection                    | session_start record is **not** written (write happens after detection succeeds); abnormal exit visible in `pxx`'s stderr |
| `pxx` in a non-git directory                                          | Record has `git_repo_root: null`, `git_head_sha: null`, `git_dirty: null`                                                 |

## Open design notes (deferred)

- **`session_end` record + exit status** — requires switching `cli.py`
  from `os.execv` to a supervisor process (`subprocess.run`). Real
  upside (exact end timestamps, exit codes, ability to write
  post-session work like the auto-revert in #002). Real cost
  (architecture change, signal-handling correctness, larger blast
  radius). Defer until v2; revisit if "did this session crash?"
  questions start coming up in real post-mortems.
- **`pxx --log` reader command** — `cat | jq | grep` is fine for now.
  Build a reader when the same query gets typed three times.
- **Cross-machine aggregation** — single-developer tool; per-machine logs
  are enough. The dual-remote git setup is the only "sync" we need.
- **Project-local log mirror** — could write a slim per-repo log into
  `.git/pxx-sessions.jsonl` for projects where the user wants per-repo
  audit. Speculative; deferred.
- **Schema versioning** — every record could carry `"schema": 1`. Skipped
  in v1 because there's nothing to be compatible with yet; add when v2
  introduces a breaking change.

## Non-goals

- **Replay** — re-executing a session from the log. Out of scope.
- **Conversation logging** — aider's `.aider.chat.history.md` owns this;
  we link to it via `aider_history_path`, we don't duplicate it.
- **File content** — git owns this. Logging it twice wastes disk and
  risks leaking sensitive content.
- **Real-time observability** — no dashboard, no metrics endpoint. This
  is a forensic log, not a monitoring system.

## Status updates needed in `backlog.md` when this completes

- `#004` status: `planned` → `in-progress` → `done`
- No other plans' "Blocks" or "Blocked by" columns change — #004 is
  parallel.
- If #001 (Dogfooding) Tier 4 later cites this plan as a prerequisite,
  add #004 → Tier-4 dependency at that time (probably as an internal
  note on #001 rather than a column change).
