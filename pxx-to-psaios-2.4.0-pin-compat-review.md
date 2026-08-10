# pxx 2.4.0 -> PSAIOS pin-compatibility review

**Verdict: PIN 2.4.0.** The governed edit surface is byte-identical to the 2.3.6 you
already run + verified (bl330). There is one net-new *config* surface, and it is locked
by the same A0b invariant you already trust for shell. Your two done-signal questions are
answered inline (Q1/Q2).

Diff basis: `git log/diff v2.3.6..v2.4.0`.

## Governed surface: byte-identical 2.3.6 -> 2.4.0 (verified)
- **broker.py: 0 commits** -> `_TOOL_CLASSES` unchanged (read/list/search/write/edit/
  run_shell + recall_memory/remember). pol-083 / pol-006 + your hook matchers need no change.
- **tools/__init__.py: 0 commits** -> tool-dispatch / PreToolUse fire-point unchanged.
  Your hook still fires (broker.authorize) BEFORE the tool; the run_shell -> shell.exec
  DENY still precedes pxx's own gate.
- **safety.py: 0 commits** -> the run_shell auto-mode A0b gate is unchanged.
- **events.py (AuditLog): 0 commits** -> hash-chain canonicalization unchanged.
  **bl305 coupling: no per-version audit re-verify needed for this pin.**

## Q1 - does done-signal (2.3.7, #38) change the governed tool surface / need policy or hook re-review?
**No.** done-signal early-exit is an *injected oracle* (`SessionContext.done_check`,
consulted by the native backend after edit turns). **No model-visible tool was added;
`broker._TOOL_CLASSES` is unchanged.** The commit states it directly: "a governed
integrator that pins pxx by tool surface needs no re-review." Every edit still passes
through broker.authorize + your PreToolUse hook identically.

## Q2 - does it affect the governed `pxx run` invocation psagent uses?
**No - the governed single-run path is byte-identical.** From the commit: "Only fires
inside `run_loop` with a test command; single-shot `pxx run` is byte-identical." psagent
invokes `pxx run -m task` (single-shot), so done-signal never engages. It also *cannot*
cause an unverified edit to report success: early-exit fires only when the on-disk diff
already passes the objective gates (scope + diff-cap + lint + tests), and the loop's own
review gate still runs on the COMPLETED result. It stops over-work - stricter, not looser.
If you ever move to `pxx loop` with a test command, `done_signal` is default-on and
disable-able via `PXX_DONE_SIGNAL` / TOML strict-bool.

## One net-new governance surface: `memory_capture_successes`
2.4.0 adds opt-in success-exemplar memory capture (records tool `result_preview`).
**Default False.** More important: it is added to the A0b project-config-IGNORED set
alongside `allow_ungated_shell` - a checked-in `pxx.toml` inside the edited repo **cannot**
enable it (honoured only from user config, with a loud warning). In-code rationale: "a file
inside the edit surface must not be able to define - or DISABLE - the gate that guards the
edit surface." So the only new data-persistence vector is locked by the same invariant that
already protects shell.

## Other 2.4.0 deltas (benign to the governed path)
- W1.2: `git worktree add` bounded `timeout=30` (adds to 2.3.6's git-timeout second wall).
- W1.3: fail-closed secrets gate on **auto-commit** - improvement-loop path only, NOT the
  governed `pxx run` tool dispatch.
- W2/W3 memory: real `memory_retrieval_limit`, result_preview capture - all under the
  memory tools already in your pinned set; gated by `memory_capture_successes` above.

## Recommended adoption
`PINNED_PXX_VERSIONS += "2.4.0"` (keep 2.3.6 running/rollback until verified; 2.4.0 is a
strict superset, promote after). Live-verify = your bl330 step5 as-is **plus** one net-new
assertion: a checked-in `memory_capture_successes = true` in a repo-local pxx.toml under the
edit surface is IGNORED (A0b) -> a governed run cannot enable persistent memory writes.

## pol-017/pol-020 over-match (your note)
Acknowledged; not pxx-side, not blocking. Same class as the inv-016b/bl324 phantom-key
story: if those rules are meant to *enforce*, they need their condition predicate
implemented (else the whole strict rule is inert / over-matching); if aspirational, a
loud-but-benign log is fine. No pxx action.
