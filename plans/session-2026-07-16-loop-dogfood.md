# 2026-07-16 session plan — close Phase 9 via vllm-host-1, then docs-sme A/B
> Backlog ID: 008

> Status: done — Task 1 (live loop dogfood, APPROVE) and Task 2 (9.4 capture +
> privacy check) closed plan 004 on 2026-07-16; Task 4 shipped as
> pxx-orchestrator 1.1.0 on PyPI the same night (trusted publishing fixed and
> verified, tags v1.1.0 + learning-baseline-1, clean-env smoke passed).
> Task 3 (docs-sme §6 A/B) is tracked solely by plan 006 (re-scope decision
> D5 pending in plan 009) — no remaining scope here.
> Type: session work-order — sequences the remaining work of plans 004 and
> 006; closes when its tasks land (which should take 004, and possibly 006,
> to `done`). No new scope of its own.
> Context: written 2026-07-15, immediately after plan 007 shipped — vllm-host-1
> (`Qwen3-Coder`, 30B-A3B FP8) is now the priority endpoint, verified live
> end-to-end in ask mode. Edit mode has NOT yet run against vllm-host-1.

## Why this order

Phase 9's `--loop` was built and hardened against the gpu-node-1's 14B, whose
malformed SEARCH/REPLACE edits forced retry machinery (9ca1a22). The loop
now inherits vllm-host-1's Qwen3-Coder, which went 4/4 on edit-shaped tasks in
the 2026-07-15 A/B — the live dogfood that has been Phase 9's blocking
validation is suddenly much more likely to pass, and it doubles as the
first real edit-mode exercise of the new endpoint. docs-sme's single
remaining item (§6 model A/B) is the lighter second task and can reuse
the A/B methodology from 2026-07-15.

## Task 1 — live `pxx --loop` dogfood on vllm-host-1 (plan 004)

Preconditions (hard):
- Clean working tree — `--loop` implies edit mode; the #002 safety tag
  stashes uncommitted work.
- No concurrent review agents (reviewer-runtime rules in CLAUDE.md).
- `pxx --doctor` and a `pxx --self-test` pass first.

Steps:
1. Pick a real, bounded, single-file task in the pxx repo (Phase 9's
   "one file per round" constraint) — ideal shape: a small refactor or
   test-gap fix with an objective verifier.
2. Run `pxx --loop "<task>" --scope <file>` and observe rounds: edit →
   lint gate → review → verdict. Note whether the 9ca1a22 retry path
   ever fires (it shouldn't with Qwen3-Coder — that's the hypothesis).
3. Record outcomes in the 004 plan file: rounds used, verdicts,
   edit-format failures if any, wall-clock vs the 1800s budget.

Acceptance: one loop reaches APPROVE on a genuine task without manual
intervention. If Qwen3-Coder still produces malformed edits, capture the
transcript — that decides whether the retry machinery stays load-bearing
or becomes gpu-node-1-legacy.

## Task 2 — 9.4 cross-session capture (plan 004, ~1 day est.)

Per the 004 plan: on terminal verdict, `tool_capture.capture_session_tools()`
stores the loop summary for cross-session recall; then the privacy check —
loop audit/memory records must honor the de-identification contract
(a256a04): no machine paths/hostnames in anything that could reach a
public artifact. The deferred `--rollback` opt-in stays deferred unless
Task 1 shows a concrete need.

Closing Task 1 + Task 2 flips plan 004 to `done` (update backlog row in
the same commit as the last verification step).

## Task 3 — docs-sme model A/B (§6 of plan 006)

The only remaining item of 006. Two changes to §6's original framing:
- Add vllm-host-1's Qwen3-Coder (through the SME) as a contender — the section
  predates vllm-host-1's existence, and the fleet's primary model should be in
  the comparison.
- Reuse the 2026-07-15 A/B harness shape: identical prompts, temperature
  0, warmup excluded, TTFT/tok-s measured, outputs saved and hand-graded.
  Watch for the qwen3:30b-a3b overthinking failure mode (DNF on 2/4 with
  a 3000-token budget) if it's included.

Closing Task 3 flips plan 006 to `done`.

## Task 4 — PyPI release checkpoint (v1.1.0)

PyPI (`pxx-orchestrator`) is still at 1.0.0 (2026-06-04) while main has
accumulated user-facing features since: multi-candidate vLLM lists with
per-endpoint models, `--no-gitignore`, non-TTY `--yes` injection,
`PXX_DEBUG` probe logging, plus the loop/review/endpoint fixes of June.
The repo staying current while PyPI ages is its own drift class — adopt
the rule: **any session that closes a plan touching user-facing behavior
ends by asking "does PyPI need a bump?"**

Release when, not before:
- Task 1's dogfood passes — ship edit-mode-validated features, not
  ask-mode-only ones.
- `uv run pytest -q` and repo-wide ruff green (already true today).

Steps (semver: minor bump — new features, no breaking changes):
1. CHANGELOG.md: add `[1.1.0]` section covering everything since 1.0.0
   (git log v1.0.0..main is the source of truth).
2. Bump `version` in pyproject.toml — **guardrailed file: user executes
   or explicitly approves.**
3. Build + publish (`uv build`, then the same publish path used for
   1.0.0), tag `v1.1.0`, push tag.
4. Post-publish smoke: `uv tool install pxx-orchestrator==1.1.0` in a
   temp env on a machine WITHOUT the repo checkout — 1.0.0's packaging
   note applies: config/ files ship only with a checkout, so verify the
   pip-installed fallback paths still behave (no `--config`, litellm
   metadata warning acceptable there or documented).

If Task 1 fails, still consider shipping 1.1.0 minus the loop claims —
the endpoint/headless features are validated independently.

## Explicitly out of scope tomorrow

- Phase 8.5 confidence scoring and the phase-8 tier-2/3 feature list
  (001/002/003): explicitly off the loop's critical path per plan 004;
  nothing tomorrow blocks on them.
- Any change to the Studio's Ollama daemon or binding (settled 2026-07-15,
  localhost-only by design).

## Stop conditions

If the Task 1 dogfood fails twice on distinct tasks, stop and diagnose
rather than iterating the loop harness blind — the failure transcript is
the deliverable in that case, and Tasks 2–3 still proceed independently.
