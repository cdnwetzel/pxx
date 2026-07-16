# Open items & remediation plan — post-dogfood sweep
> Backlog ID: 009

> Status: planned
> Type: remediation inventory — everything open across the repo, fleet, and
> backlog as of 2026-07-16 (after live dogfood #2 and the same-day loop
> hardening, commit eda8098). Ordered by recommended execution. Items that
> belong to existing plans reference them rather than duplicating scope;
> this plan closes when every item is either done or explicitly re-homed.

## Already fixed today (context, no action)

- Empty reviewer output → APPROVE laundering: now fails closed (eda8098).
- No review-backend preflight: loop now refuses to start against an
  unreachable backend or one not serving the configured model (eda8098).
- `review/` residue blocking consecutive loops: gitignored (34e6b52).
- Review default unusable on this MacBook: `~/.config/pxx/env` now points
  review at vllm-host-1 (machine-local, not a repo change).

## 1. Push decision — 6 local commits (user call)

`main` is 6 ahead of origin: four `[autonomous]` (a6a0c97, b5123f8,
98c54ca, 76492c2) + two session commits (34e6b52, eda8098). The loop's
contract is "push is yours."

**Remediation:** user reviews and pushes. Optional first: squash run B's
three commits (b5123f8/98c54ca/76492c2 are one logical change — the
test file); history is local so an interactive rebase is still safe.
**Effort:** minutes. **Blocks:** item 6 (PyPI ships only pushed work).

## 2. Independent reviewer model (self-review trade-off)

Editor and reviewer are currently the same model (Qwen3-Coder reviews its
own edits) — calibrated as non-rubber-stamping today, but a shared blind
spot stays shared. The sovereign default (`qwen2.5:7b-instruct` on local
Ollama) is unusable because this MacBook's Ollama has zero models.

**Remediation:** `ollama pull qwen2.5:7b-instruct` (~4.7 GB) on the
MacBook, then drop the `PXX_REVIEW_URL`/`PXX_REVIEW_MODEL` overrides from
`~/.config/pxx/env` — restoring the stock default, which the new
preflight now verifies at loop start. Keep the vllm-host-1 override documented
as the fallback for disk-constrained machines.
**Effort:** one pull + env edit + one loop smoke. **Risk:** none.

## 3. Plan 008 Task 2 — 9.4 cross-session capture + privacy check

The remaining substance of plan 004: on terminal verdict, capture the
loop summary via `tool_capture.capture_session_tools()`; verify loop
audit/memory records honor the de-identification contract (a256a04) —
no machine paths/hostnames reachable from public artifacts.

**Remediation:** per `plans/session-2026-07-16-loop-dogfood.md` Task 2.
Good loop-fodder tasks for the capture dogfood: the still-untested
helpers in `pxx/safety.py` (`sanity_check`, `create_tag`,
`prune_old_tags`, `_has_unmerged_autonomous_commits`) and
`loop._healing_message` — genuine gaps, single-file, objective verifier.
**Closes:** plan 004 (flip backlog in the same commit as the last step).

## 4. aider commits bypass the pre-commit hook (scope gate audit)

Observed in run B: aider commits with `--no-verify` by default, so the
pre-commit hook (ruff/pytest/diff-cap/**scope gate**) does not run on
aider's own commits. The loop's internal gates cover lint/tests/diff
budget independently, but the *scope* prefix gate may only be enforced
on non-aider commits — meaning an off-scope aider edit could land in a
tagged commit and only be caught later (or not at all).

**Remediation:** (a) confirm the exposure with a deliberate off-scope
edit in a throwaway branch; (b) if real, pass `--git-commit-verify` to
aider in `--self-fix`/`--loop` modes (fixed args in `_build_aider_args`
gated on autonomous mode), and verify the loop's heal path still works
when a hook rejection bounces an aider commit; (c) record the decision
in plan 004's Decisions section either way.
**Effort:** half-day incl. tests. **Risk:** medium — touches the loop's
commit flow; do after item 3 so capture work isn't entangled.

## 5. Plan 008 Task 3 — docs-sme §6 model A/B

Unchanged from `session-2026-07-16-loop-dogfood.md` Task 3: rerun §6
with Qwen3-Coder as a contender, reusing the 2026-07-15 harness
(temperature 0, warmup excluded, TTFT/tok-s, hand-graded outputs).
**Closes:** plan 006.

## 6. Plan 008 Task 4 — PyPI v1.1.0

Now unblocked: Task 1's dogfood passed. Since the plan was written,
today's loop hardening (preflight, fail-closed review) is also
user-facing — include it in the changelog. Steps as written in plan 008
(changelog from `git log v1.0.0..main`, guardrailed pyproject bump user-
approved, publish, tag, pip-install smoke on a machine without the repo).
**Blocked by:** item 1 (push first).

## 7. Fleet hygiene (machine/infra, not repo code)

- **T5810 tunnel dead on this MacBook** — tier-2 fallback currently
  unavailable; `pxx` would fall through to a model-less local Ollama if
  vllm-host-1 dropped. Either install/load the launchd tunnel plist here
  (`deploy/launchd/local.pxx.gpu-node-1-vllm-tunnel.plist`) or consciously
  accept vllm-host-1-only and note it in `~/.config/pxx/env`.
- **`mirror` mirror unreachable** in `pxx --doctor` from the office LAN.
  Determine expected-vs-broken (host asleep? route gone?); if expected
  on this network, doctor's output is correct and no action; document in
  the env file comment either way.
- **MacBook Ollama runs with zero models** — item 2 resolves this by
  giving it the reviewer model; alternatively stop the service.

## 8. Backlog status hygiene — stale in-progress plans

Plans 001 (Phase 8) and 002 (Phase 8 Tier 2/3) have shown `in-progress`
since before the July fleet work with no recent commits against them.

**Remediation:** audit each against reality; either finish the genuinely
in-flight remainder, re-scope to what still matters post-vllm-host-1, or flip
to `done`/`blocked` with a dated note. A backlog whose statuses lag
deceives — same rule that motivated creating backlog.md.

## Suggested order

1 (push, minutes) → 2 (reviewer pull, background) → 3 (capture, ~1 day)
→ 4 (scope-gate audit) → 5 (A/B) → 6 (PyPI) → 7–8 (hygiene, fill-in).
Items 3 and 5 are independent; 7–8 can interleave anywhere.
