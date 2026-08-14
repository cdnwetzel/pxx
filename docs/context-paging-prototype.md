# Prototype sketch: Context Paging for small-context models (v0)

**Goal.** Prove a small-context local model (4B / 8K window) can complete a real repo task by
assembling a fresh, hard-capped **capsule** per action — no transcript replay — with host-run
verification, and earn a receipt on the 8 GB Neo. Reference: Camelid's Context Paging Runtime
(timtoole02, 2026-08); this is the pxx-native, receipted evaluation of that idea.

**Scope of v0 (deliberately narrow).** File-level pages only. `NEED_CONTEXT(path)` and file-level
eviction. **Symbol cards, a repo map, and symbol-level requests are v1** — v0 describes only what
the v0 host can do deterministically.

**What pxx already provides (reuse, don't rebuild).** Host-enforced scope + tool gating
(`broker.authorize`), the terminal taxonomy (`BLOCKED != COMPLETED`), host-run verification (the
model can't grade its own work, R-014), fresh-context-per-round, and stale-edit rejection
(`edit_file` exact old_string match). v0 adds only the *paging* layer on top.

## Source-page hashing contract (the single authority)

A page's `sha-256` is over the **raw file bytes** (no normalization, no newline munging), addressed
by the **canonicalized, symlink-resolved** path. The **same** rule is used everywhere it matters —
the patcher's `expected_sha` check, the reindexer after a write, and the restart reconciliation —
so a hash means one thing across the whole system (mirrors pxx's canonical hashing in `events.py`).

## v0 components (smallest thing that proves the mechanism)

1. **Persistent state (host-owned, survives restart):**

   - **Task ledger** (JSON on disk): objective, acceptance (**the test command lives here**),
     invariants, decisions, failed attempts, verification state, revision. The task lives here, not
     in a transcript.
   - **Source pages:** repo files, each addressed by its `sha-256` (above) — the single authority.
   - **Artifact store:** full test/terminal logs on disk; the model gets a bounded summary + a
     reference ID — the summary is **scrubbed of secrets** (credentials never reach the model).

2. **Capsule builder (per action):** fixed agent kernel + task contract (from the ledger) + the
   EXACT target source verbatim (never summarized, never evicted) + a compact diagnostic (last
   failure summary + ref) + this phase's tool list. Enforce a **hard input cap** (start 5,500
   tokens) measured with the REAL tokenizer; over budget → evict in order **history → dependency
   pages** (never the target source).

   - **Overflow path (kernel + contract + target source alone > cap after all evictions):** the
     host returns a preflight **`BLOCKED(reason="target_source_exceeds_capsule")`** — a legal,
     deterministic action — rather than ever evicting or summarizing the target source. (v1:
     windowed source pages + windowed patch semantics; out of v0 scope.)

3. **Typed actions (model replies with exactly ONE; host validates shape + fields before execute):**

   - `NEED_CONTEXT(path)` — a page fault; host pages the exact file in.
   - `PATCH(path, expected_sha, diff)` — applied only if `expected_sha` matches the page's current
     hash, else REJECT + page fresh source (never apply blind).
   - `RUN_TEST` — runs **the ledger's acceptance command only** (host-owned); a model-selected
     command is refused (else the model could grade its own work). Runs in the sandbox under a
     host **timeout + resource bound** (a runaway test can't hang the loop).
   - `SEARCH | INSPECT` — bounded results, returned by reference.
   - `COMPLETE` — accepted **only** after a host `RUN_TEST` of the ledger command passes.
   - `BLOCKED` — an honest stop; **never** recorded as `COMPLETE`.

4. **Host executor + verifier (crash-safe):** before mutating, persist an **in-flight action record
   with an idempotency key**; apply the patch, reindex the hash, and bump the ledger revision under
   an **atomic commit** (tmp-then-replace, ordered so a crash leaves a recoverable state); on
   startup **reconcile** the in-flight record (already-applied → skip; not-applied → discard;
   **ambiguous → fail closed**), so an interrupted action never double-applies or leaves source /
   page-index / ledger inconsistent. Tools run sandboxed. No transcript replay.

5. **Loop:** build capsule → model → one typed action → host executes/verifies → update ledger →
   repeat until COMPLETE / BLOCKED / budget.

## Prove on Neo (8 GB) — the receipt

- **Hardware:** Neo (8 GB MacBook), a real 4B/8K model via Ollama (e.g. `qwen3:4b`).
- **Task:** a small, real single-file bug fix whose failing test must pass.
- **PASS receipt (schema — recorded to disk):**

  ```json
  {
    "model_id": "...", "tokenizer_id": "...",
    "capsules": [{"action_seq": 1, "input_tokens": 4123, "under_cap": true}, ...],
    "actions": [{"seq": 1, "type": "NEED_CONTEXT", "path": "..."},
                {"seq": 2, "type": "PATCH", "path": "...", "expected_sha": "...", "applied": true}, ...],
    "negative_controls": {"stale_sha_rejected": true, "restart_resumed_no_replay": true,
                          "blocked_not_completed": true, "overflow_never_dropped_target": true},
    "verification": {"command": "<ledger acceptance cmd>", "host_run": true, "passed": true},
    "terminal": "COMPLETED"
  }
  ```

- **Negative controls (each MUST fire — the mechanism is proven able to fail):**

  1. a `PATCH` with a **stale sha** is REJECTED (not applied blind);
  2. **kill the process mid-action, restart** → the in-flight record reconciles, resumes from the
     ledger with **zero transcript replay**, no double-apply;
  3. a `BLOCKED` is **never** recorded as `COMPLETE`;
  4. an over-budget capsule **evicts** and **never drops the target source** (and the target-alone
     overflow returns the preflight `BLOCKED`).

- Mac Mini (16 GB) = the gentler first pass before Neo.

## Scope discipline

- **v0** = file-level pages, single-file task, one model on Neo, the core loop + the 4 negative
  controls + the receipt schema. Prove the MECHANISM; earn one receipt.
- **v1** = symbol cards + a tiny repo map (structural memory, hash-invalidated) + symbol-level
  `NEED_CONTEXT` + windowed source pages + multi-file tasks.
- **Integration decision (deferred):** build as a native pxx context-assembly mode vs. compose on
  Camelid's runtime (they interoperate today via `provider=openai-compatible`). Revive the parked
  timtoole02 collaboration RFC rather than duplicate a kindred project.
