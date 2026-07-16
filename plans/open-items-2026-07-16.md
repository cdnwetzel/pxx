# Open items & remediation plan — post-dogfood sweep
> Backlog ID: 009

> Status: in-progress — execution sweep run 2026-07-16 (same day). Every item
> below is CLOSED or carries a **DECISION** block for the user. This plan
> closes when the D1–D5 decisions are made and their follow-through lands.

## Scoreboard

| # | Item | Outcome |
|---|------|---------|
| 1 | Push 6 local commits | **DECISION D1+D2** (privacy finding gates the push) |
| 2 | Independent reviewer model | **CLOSED** — local 7b pulled, stock defaults restored, preflight-verified |
| 3 | 9.4 cross-session capture + privacy check | **CLOSED** — implemented + privacy check done (found D1); plan 004 → `done` |
| 4 | aider `--no-verify` scope-gate audit | **CLOSED** — exposure confirmed empirically; loop-level scope guard shipped |
| 5 | docs-sme §6 model A/B | **DECISION D5** — blocked on candidate deployment + T5810 access |
| 6 | PyPI v1.1.0 | **PREPARED** — changelog written; execution is **DECISION D3** |
| 7 | Fleet hygiene | Ollama models **CLOSED** (via item 2); T5810 + mirror → **DECISION D5** |
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
`192.168.111.12`, the `splawoffice.local` search domain, and the explicit
"nothing on the fleet has request-level auth; the network boundary is the
auth layer" posture), plus ~19 more files (plans/, docs/, deploy/README,
review/) matching `192.168.111|splawoffice|vllm-host-1|workstation`. The
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

### D4 — Phase 8 Tier 2/3 direction (no urgency)

8.6–8.8 (multi-machine sync, team features, self-optimizing memory) are
untouched since June and target a multi-user scale you don't currently
operate at. **Recommendation: leave 002 parked at `planned`** and spend
the effort on loop-adjacent work (e.g. plan 003's confidence scoring
feeding review quality) — revisit only if a second daily-driver machine
or a teammate materializes. Deciding "parked indefinitely" vs "queued
next" is yours.

### D5 — T5810 access from this MacBook + mirror mirror + §6 A/B scope

Three intertwined infra facts found today: `gpu-node-1` doesn't resolve on
the office LAN from this MacBook (no `~/.ssh/config` alias, no tunnel
plist installed here — that setup lives on the Studio); the `mirror`
mirror remote that `pxx --doctor` checks isn't configured on this
machine (and `mirror/pxx` isn't visible to your gh auth — different
name, or private to another account); and docs-sme §6's A/B needs the
T5810 incumbent plus candidates (Qwen3-Coder-Next, Gemma 4) that are
deployed nowhere in the fleet.

**Decisions:**
- T5810 from this MacBook: (i) give me the SSH alias/host details and I
  install the tunnel plist here (restores tier-2 fallback + the §6
  incumbent arm), or (ii) accept vllm-host-1-only on this machine (env file
  already documents the posture). **Recommendation: (i)** — one-time,
  small, and the loop's review/endpoint fallbacks get real again.
- mirror: give me the mirror URL to `git remote add mirror <url>` here,
  or accept the doctor warning on this machine as expected output.
- §6 A/B scope: **recommendation — re-scope to incumbent
  (qwen2.5-coder-14b-coder-lora) vs vllm-host-1 Qwen3-Coder through the SME
  proxy**, both already deployed (needs only T5810 access). Defer
  Qwen3-Coder-Next/Gemma until you decide to deploy them (Gemma 4 26B
  fits 2×A4500 quantized; Qwen3-Coder-Next likely doesn't fit 40GB).
  Plan 006 stays `in-progress` until you pick.
