# ARCHITECTURE.md — pxx 2.0 module map

The authoritative contracts are `DESIGN.md` (core) and `DESIGN-ROADMAP.md`
(self-improvement platform). This file is the map, not the manual.

## Planes

```
TRUSTED CONTROL PLANE   safety.py (scope/hooks/budgets/permissions)
                        broker.py (action broker: per-action-class authorization)
                        workflow.py + WORKFLOW.md (repo-owned machine contract)
                        governance.py (secrets/public-content scanner)
                        protected_paths.py (THE optimizer-protected set)
                        clarify.py (ambiguity gate)
                        eval/ (cases, harness, report) · calibration.py
                        improve/promotion.py · improve/candidates.py
PRODUCTION RUNTIME      session.py · loop.py · review.py · router.py
                        backends/ (native, aider, mock) · tools/ (fs, shell, memory)
                        goal.py (goal -> task DAG -> bounded loops)
EXPERIENCE PLANE        events.py (bus + hash-chained audit) · manifest.py
                        runs.py · verify.py · cost.py · memory/ (SQLite store)
OPTIMIZER PLANE         improve/mining.py (clusters, proposals)
EVALUATION PLANE        eval/harness.py (disposable repos, hidden checks)
PROMOTION PLANE         improve/channels.py (stable/candidate/shadow/retired)
                        improve/cycle.py (propose-only cycle) · improve/autopromote.py
EDGES                   cli.py · server.py (HTTP API) · mcp/ (client + server)
                        doctor.py · upgrade.py
```

## Invariants (enforced by tests)

1. pxx owns the runtime: every model/tool event flows through the event bus;
   backends cannot bypass policy.
2. Gates raise and propagate; telemetry (audit, memory, run dirs) is
   best-effort and never crashes a session.
3. One authorization authority: every tool call goes through
   `ToolRegistry.call` → `ActionBroker.authorize` — no parallel path.
4. Paths are untrusted input: canonicalized before any gate decision.
5. Audit is metadata-only: no prompt bodies, file contents, diffs, secrets.
6. The optimizer never writes the trusted control plane
   (`PROTECTED_PREFIXES` ⇔ `docs/TRUST_BOUNDARY.md` ⇔ `WORKFLOW.md`
   `[protected_paths]` — all three pinned in sync).
7. Every run is attributable: `run_id` + `agent_version_id` (manifest hashes
   prompts, settings, budgets, WORKFLOW.md, and the protected-paths list).
