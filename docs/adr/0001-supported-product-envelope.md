# ADR-0001: Supported product envelope

- **Status:** Accepted _(owner, 2026-07-21)_
- **Date:** 2026-07-20
- **Deciders:** repo owner
- **Supersedes / relates to:** the 1.3.4 public-readiness work
  (`pxx-1.3.4-release/`), branch `release/1.3.4`

## Context

pxx has a genuinely solid, tested **core** (endpoint detection, ask/edit with
explicit consent, git safety tags, trusted paths, scope enforcement, audit
records, aider dispatch, bounded self-dev/review). It also carries several
**optional services** that are advertised as working but are, on inspection
(convergent findings from multiple independent audits *and* this project's own
capstone), only partially wired:

- agentmemory automatic capture is not wired (the stdout observer returns
  immediately) and cross-session injection is not called from the CLI; "capture"
  is a post-session git-diff summary only;
- 9router's fallback chains and token/cost metrics modules exist but are not used
  by the production app (single upstream, three routes);
- HNSW "100×/5ms/<10% recall" is unproven — the shared index stays size-zero on
  the production storage path;
- docs-RAG and live continuous-self-improvement require private local models.

Publishing docs that promise these as stable undermines a build-in-public posture.

## Decision

**Envelope A — ship a hardened core; treat the unfinished services as
experimental/source-only; retract unproven claims.**

### Supported (stable) — the public product
Endpoint detection + model selection · ask-by-default + explicit `--edit` consent
· git safety tags · trusted paths · `--scope` enforcement · audit records · aider
dispatch · the bounded self-dev/review surfaces that are packaged and tested.

### Experimental / source-only (not the supported contract)
agentmemory + automatic cross-session memory · 9router fallback/metrics/middleware
· docs-RAG · HNSW scaling/performance · live continuous-self-improvement.

### Install boundary
The **PyPI wheel is the core only.** The services live in `services/`, require a
repo checkout, and carry explicit "experimental — source checkout required"
banners and a promotion checklist. A PyPI wheel must never imply a feature it does
not contain.

### Definition of "offline"
pxx performs **no cloud inference** — every LLM call goes to an endpoint *you*
control (localhost or a network you trust). It is **not** "no network" (it talks
to your endpoints), and pxx does **not** run inference itself. Public copy uses
exactly this framing.

### Claims withdrawn (until separately proven)
"persistent observation memory" (tagline/PyPI summary) · "automatic capture via
tool calls" · automatic cross-session injection · 9router "fallback chains" /
"token tracking" / `/v1/status` / `/v1/usage` · HNSW "100×" / "5ms" /
"<10% recall loss" · any compliance/archival guarantee not backed by shipped,
tested behavior.

### Promotion criteria (experimental → stable), one component at a time
1. Production-path integration tests proving the feature actually runs (not just
   unit tests on an unwired component);
2. reproducible, checked-in benchmark evidence for any numeric claim;
3. a resolved install/packaging contract (a uniquely-named distribution or a
   documented source-install path);
4. its own ADR + a claim-ledger entry + passing release gates.

## Consequences

- A smaller supported surface with an **honest, defensible public contract** and
  the strongest near-term narrative ("I shipped a hardened core and labeled the
  experiments, promoting only on proof").
- Service code stays public and CI-tested as an **engineering lab** with stated
  limitations — not deleted, just not claimed.
- Support window: 1.3.x on Python 3.11–3.12.

## Alternatives considered

- **Envelope B (supported full stack):** complete the memory + 9router
  integration, prove HNSW, and decide a service-distribution story before release.
  Rejected for the first release — it blocks shipping an honest core on large,
  open-ended work. Revisit per-component via Milestone 6 and the promotion
  criteria above.
