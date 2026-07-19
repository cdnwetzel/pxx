# Open items & remediation plan — post-dogfood sweep
> Backlog ID: 009

> Status: done — all decisions D1–D5 made and executed (D1/D2/D3 on
> 2026-07-16; D4 parked-on-trigger and D5 resolved 2026-07-17). Remaining
> follow-through lives in its own homes: the §6 A/B run (plan 006) and the
> release-workflow scanner step (user-gated, roadmap Phase 0).

## Scoreboard

| # | Item | Outcome |
|---|------|---------|
| 1 | Push 6 local commits | **DECISION D1+D2** (privacy finding gates the push) |
| 2 | Independent reviewer model | **CLOSED** — local 7b pulled, stock defaults restored, preflight-verified |
| 3 | 9.4 cross-session capture + privacy check | **CLOSED** — implemented + privacy check done (found D1); plan 004 → `done` |
| 4 | aider `--no-verify` scope-gate audit | **CLOSED** — exposure confirmed empirically; loop-level scope guard shipped |
| 5 | docs-sme §6 model A/B | **DECISION D5** — blocked on candidate deployment + gpu-node-1 access |
| 6 | PyPI v1.1.0 | **PREPARED** — changelog written; execution is **DECISION D3** |
| 7 | Fleet hygiene | Ollama models **CLOSED** (via item 2); gpu-node-1 → **DECISION D5** |
| 8 | Backlog hygiene 001/002 | **CLOSED** — audited vs code; 001 → `done`, 002 → `planned`; direction is **DECISION D4** |

## What shipped in this sweep (commits eda8098, 4c8415b, + this one)

- Review leg fails closed on empty output; review-backend preflight
  (unreachable endpoint or missing model refuses the loop at start —
  including Ollama's `"data": null` zero-models shape).
- `_capture_loop_summary`: terminal review verdicts (APPROVE/REJECT/
  NO_REVIEW) store diff observations + a loop-summary observation;
  best-effort; content repo-relative only (test-pinned).
- `_out_of_scope_changes`: loop-level scope enforcement, verdict
  `OUT_OF_SCOPE`, fail closed — because aider commits with `--no-verify`
  (demonstrated live: an off-scope commit sails past the hook).
- Independent reviewer restored: `qwen2.5:7b-instruct` pulled locally;
  `~/.config/pxx/env` back to stock review defaults (editor=Qwen3-Coder
  on vllm-host-1, reviewer=local 7b — genuinely independent).
- `.gitignore` narrowed `/review/` → `/review/claude/` (codex/copilot/
  gemini review files are tracked content).
- CHANGELOG.md `[1.1.0]` section written (release itself: D3).
- Plans 004 → done, 001 → done (Tier 1 verified in code), 002 → planned.

---

## Decisions needed

### D1 — Public-repo privacy breach (decide FIRST; gates D2/D3)

**Finding:** github.com/cdnwetzel/pxx is **public**, and pushed `main`
carries internal infrastructure details that the a256a04 de-identification
sweep existed to prevent: `CLAUDE.md` (fleet hostnames, LAN IPs like
office LAN IPs, the office search domain, and the explicit
"nothing on the fleet has request-level auth; the network boundary is the
auth layer" posture), plus ~19 more files (plans/, docs/, deploy/README,
review/) matching the fleet identifier set (see D1 notes). The
6 unpushed commits add 3 more such files. No credentials are exposed —
this is topology + posture, all RFC1918/behind NAT.

**Options:**
- **(a) Scrub forward — RECOMMENDED.** One commit replacing internal
  identifiers in tracked files with placeholders (`<lan-vllm-host>`,
  `your-office-domain`), moving machine truth into `~/.config/pxx/env`
  comments and Claude's session memory (already there). History still
  contains the old text, but the live tree — what visitors and search
  indexes actually read — goes clean. Low effort, no disruption.
- **(b) Scrub + history rewrite.** Adds `git filter-repo` + force-push;
  invalidates clones/PRs and the v1.0.0 tag lineage. Only worth it if you
  consider the exposure material; GitHub caches force-pushed commits
  anyway unless support purges them.
- **(c) Flip the repo private.** Kills the public-portfolio purpose and
  PyPI source links.
- **(d) Accept as-is.** Defensible (internal addresses, no secrets), but
  it normalizes drift against your own a256a04 contract.

**Recommendation: (a)**, executed before any push. Say the word and I do
the scrub commit; the only judgment calls are CLAUDE.md wording (I'd keep
roles — "priority LAN vLLM node" — and drop names/IPs) and whether
`review/codex|copilot|gemini` files (other agents' namespaces, which I
don't edit) get scrubbed by you or left as historical.

### D2 — Push & squash (after D1)

9 commits are local: 4 `[autonomous]` + 5 session commits. Options:
push as-is, or first squash the three run-B autonomous commits
(`b5123f8`/`98c54ca`/`76492c2` — one logical change) via interactive
rebase. **Recommendation:** squash-then-push for a cleaner public
history; entirely cosmetic, skip if you don't care.

### D3 — PyPI v1.1.0 release (after D1+D2)

Everything is prepared: `CHANGELOG.md [1.1.0]` covers the loop, local
review, multi-endpoint chains, headless hardening, and today's safety
work. Remaining steps are yours (guardrails + credentials):
1. Bump `version = "1.1.0"` in `pyproject.toml` (guardrailed file).
2. `uv build`, publish via the same trusted-publishing workflow as 1.0.0
   (tag `v1.1.0`, push tag — the CI workflow publishes).
3. Post-publish smoke on a machine without the repo:
   `uv tool install pxx-orchestrator==1.1.0`, run `pxx --help`, one ask
   session; litellm metadata warning is acceptable there (documented).

**Recommendation:** ship it this week — the gap between repo and PyPI is
6 weeks of user-facing features, and Task 1's edit-mode validation (the
release gate you set) passed today.

### D4 — Phase 8 Tier 2/3 direction — DECIDED 2026-07-17

**PARKED, revisit on trigger** (user decision): plan 002 stays `planned`;
the revival triggers are a second daily-driver machine or a teammate
needing shared observation memory. Roadmap Phase 20 supersedes the old
8.5/8.7 intelligence-layer design regardless.

### D5 — gpu-node-1 + mirror + A/B scope — RESOLVED 2026-07-17

- **gpu-node-1 access from this MacBook: DONE.** SSH alias installed
  (machine-local config), persistent tunnel agent loaded
  (`local.pxx.gpu-node-1-vllm-tunnel`), verified: `127.0.0.1:8003` serves
  `qwen2.5-coder-14b-coder-lora` (16k ctx). Tier-2 fallback restored; pxx
  detection order unchanged (vllm-host-1 first). Bonus measurement unlocked
  and taken: the 14b calibrates at recall 0.25 / fp 0.00 under the v2
  prompt — strictly dominates the 7b (0.00/0.00) as a quiet reviewer,
  but switching production would add a tunnel-availability dependency
  to every loop preflight for +0.25 recall; recommendation: keep the
  always-local 7b default, 14b recorded as the measured alternative
  (`evals/baselines/reviewer-qwen2.5-coder-14b.json`).
- **Private mirror: resolved by clarification** — pxx has no standalone
  mirror there (it ships integrated inside another tree); doctor now
  reports expected-but-unconfigured mirrors as informational
  ("not configured on this machine") instead of "unreachable".
- **§6 A/B: unblocked** — the incumbent arm is reachable through the
  restored tunnel; contender set per the standing recommendation
  (incumbent vs Qwen3-Coder through the SME proxy). Execution is
  scheduling, not decision; plan 006 closes when it runs.
