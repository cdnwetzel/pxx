# Prototype sketch: Context Paging for small-context models (v0)

**Goal.** Prove a small-context local model (4B / 8K window) can complete a real repo task by
assembling a fresh, hard-capped **capsule** per action — no transcript replay — with host-run
verification, and earn a receipt on the 8 GB Neo. Reference: Camelid's Context Paging Runtime
(timtoole02, 2026-08); this is the pxx-native, receipted evaluation of that idea.

**What pxx already provides (reuse, don't rebuild).** Host-enforced scope + tool gating
(`broker.authorize`), the terminal taxonomy (`BLOCKED != COMPLETED`), host-run verification (the
model can't grade its own work, R-014), fresh-context-per-round, and stale-edit rejection
(`edit_file` exact old_string match). v0 adds only the *paging* layer on top.

## v0 components (smallest thing that proves the mechanism)
1. **Persistent state (host-owned, survives restart):**
   - **Task ledger** (JSON on disk): objective, acceptance (the test command), invariants,
     decisions, failed attempts, verification state, revision. The task lives here, not in a
     transcript.
   - **Source pages:** repo files, each addressed by its `sha-256` — the single authority.
     (v0: file-level pages; symbol cards come in v1.)
   - **Artifact store:** full test/terminal logs on disk; the model gets a bounded summary + a
     reference ID.
2. **Capsule builder (per action):** fixed agent kernel + task contract (from the ledger) + the
   EXACT target source verbatim (never summarized, never evicted) + a compact diagnostic (last
   failure summary + ref) + this phase's tool list. Enforce a **hard input cap** (start 5,500
   tokens) measured with the REAL tokenizer; over budget -> evict in order (history -> dep pages
   -> repo map), **never the target source**.
3. **Typed actions (model replies with exactly ONE, validated before execute):**
   `NEED_CONTEXT(path|symbol)` (a page fault), `PATCH(path, expected_sha, diff)`,
   `SEARCH | RUN_TEST | INSPECT` (results bounded, by reference), `COMPLETE` (accepted only after
   host verification), `BLOCKED` (an honest stop, never recorded as done).
4. **Host executor + verifier:** apply a `PATCH` only if `expected_sha` == the page's current
   `sha-256`, else REJECT + page in fresh source (never apply blind); run tools sandboxed; run the
   acceptance test command as verification (`COMPLETE` requires host tests green); reindex hashes;
   update the ledger. No transcript replay.
5. **Loop:** build capsule -> model -> one typed action -> host executes/verifies -> update ledger
   -> repeat until COMPLETE / BLOCKED / budget.

## Prove on Neo (8 GB) — the receipt
- **Hardware:** Neo (8 GB MacBook), a real 4B/8K model via Ollama (e.g. `qwen3:4b`).
- **Task:** a small, real single-file bug fix whose failing test must pass.
- **PASS receipt (all captured):** every capsule <= the token cap (measured with the real
  tokenizer); >= 1 `NEED_CONTEXT` page-fault serviced; a hash-checked `PATCH` applied; host-run
  tests green -> `COMPLETE`.
- **Negative controls (the discipline — each MUST fire, so the mechanism is proven able to fail):**
  1. a `PATCH` with a **stale sha** is REJECTED (not applied blind);
  2. **kill the process mid-task, restart** -> resumes from the ledger with **zero transcript replay**;
  3. a `BLOCKED` is **never** recorded as `COMPLETE`;
  4. an over-budget capsule **evicts** and **never drops the target source**.
- Mac Mini (16 GB) = the gentler first pass before Neo.

## Scope discipline
- **v0** = file-level pages, single-file task, one model on Neo, the core loop + the 4 negative
  controls. Prove the MECHANISM; earn one receipt.
- **v1** = symbol cards + a tiny repo map (structural memory, hash-invalidated) + multi-file tasks.
- **Integration decision (deferred):** build as a native pxx context-assembly mode vs. compose on
  Camelid's runtime (they interoperate today via `provider=openai-compatible`). Revive the parked
  timtoole02 collaboration RFC rather than duplicate a kindred project.
