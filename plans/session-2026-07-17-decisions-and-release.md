# 2026-07-17 session plan — decide, scrub, push, release
> Backlog ID: 010

> Status: planned
> Type: session work-order — executes the D1–D5 decisions from plan 009 and
> ships v1.1.0. Closes when the release is live (or explicitly deferred) and
> plan 009 flips to `done`. No new scope of its own.
> Context: written 2026-07-16 end of day. Phase 9 is done and hardened; the
> whole remaining stack is gated on decisions, not code. A second CC session
> is adding Mini-tunnel deploy files in parallel — reconcile before looping.

## Why this order

D1 (privacy) gates the push; the push gates PyPI. Everything else slots
around that spine. The loop work is optional dessert: the new guards
(preflight, scope, capture) have unit coverage but no live exercise yet.

## Task 0 — reconcile the parallel session's deploy/ work

The other CC CLI owns `deploy/README.md` (modified), 
`deploy/launchd/local.pxx.mini-vllm-tunnel.plist`, and `deploy/systemd/`
(untracked). Before anything else:
1. Confirm that session has committed (or the user commits) its files —
   this session does not touch them.
2. Privacy pass on those files (they're tunnel configs — hostnames/IPs
   likely): they get the same D1 treatment before push.
3. Verify `git status` is clean afterward — `--loop` and the #002 safety
   tag both need it.

## Task 1 — execute D1: privacy scrub (user decision, then ~1 hour)

On the user's go for option (a) scrub-forward (recommended in 009):
1. Sweep tracked files for the fleet identifier set (fleet hostnames,
   office/home LAN ranges, search domain) — CLAUDE.md, plans/, docs/,
   deploy/, config comments. Replace with role placeholders
   (`<lan-vllm-host>`, `<office-domain>`); keep roles and architecture
   intact so the docs stay useful. The literal identifier list is
   deliberately NOT written here: this repo is public.
2. Machine truth lives on in `~/.config/pxx/env` comments and Claude's
   session memory — verify both are current before deleting from docs.
3. `review/codex|copilot|gemini` files are other agents' namespaces:
   user scrubs by hand or explicitly delegates; default is leave + note.
4. Gate: `git grep` for the pattern list returns only deliberate
   placeholders; full tests + ruff still green.
If the user instead picks (b) history rewrite / (c) private / (d) accept,
record the decision in 009 and adjust: (b) adds filter-repo + force-push
coordination; (c)/(d) skip the scrub and unblock D2 immediately.

## Task 2 — execute D2: squash + push

1. Optional squash: `b5123f8`/`98c54ca`/`76492c2` → one commit
   (interactive rebase; all local, safe until pushed).
2. Push main to origin (fans to configured remotes; the private mirror is a
   D5 item and absent on this machine — doctor's warning is expected).
3. `pxx --doctor` after: origin in sync.

## Task 3 — execute D3: PyPI v1.1.0

Preconditions: Tasks 1–2 done; `uv run pytest -q` green.
1. User bumps `version = "1.1.0"` in pyproject.toml (guardrailed) — or
   explicitly approves the edit in-session.
2. Re-check CHANGELOG.md `[1.1.0]`: date it, fold in anything Tasks 0–2
   changed (e.g. scrub commit is repo-only, no changelog entry needed).
3. `uv build`; tag `v1.1.0`; push tag → trusted-publishing workflow
   publishes (same path as 1.0.0).
4. Post-publish smoke on a machine WITHOUT the repo checkout:
   `uv tool install pxx-orchestrator==1.1.0`, `pxx --help`, one ask-mode
   session. Litellm metadata warning there is acceptable/documented.
5. Update plan 008 Task 4 → done; if 008's remaining task (docs-sme A/B)
   moves to 006/D5 scope, flip 008 → done in the same commit.

## Task 4 — D5 infrastructure (needs user inputs)

- gpu-node-1 from this MacBook (recommended (i) in 009): user supplies the SSH
  host details → add `~/.ssh/config` alias, install
  `deploy/launchd/local.pxx.gpu-node-1-vllm-tunnel.plist`, verify
  `curl 127.0.0.1:8003/v1/models`, confirm pxx detection order
  vllm-host-1 → tunnel → local.
- Private mirror: user supplies mirror URL → `git remote add <mirror> <url>`,
  re-run `pxx --doctor` (mirrors in sync).
- §6 A/B re-scope: record the chosen contender set in plan 006; if
  gpu-node-1 is restored and time remains, run the incumbent-vs-Qwen3-Coder
  arms through the SME proxy (2026-07-15 harness: temp 0, warmup
  excluded, TTFT/tok-s, hand-graded). Closing §6 flips 006 → done.

## Task 5 (optional) — live exercise of the new loop guards

First live run since preflight/scope-guard/capture landed. Pick one
safety.py test gap (`sanity_check`, `create_tag`, `prune_old_tags`, or
`_has_unmerged_autonomous_commits`) as loop fodder:
1. Start agentmemory locally so 9.4 capture has a live target; note the
   observation landing (`/observations` or `/metrics`).
2. `pxx --loop "<task>" --scope tests/test_safety.py` — expect: preflight
   passes against the local 7b reviewer (first loop with the independent
   reviewer), APPROVE, capture posted.
3. Record in 004's dogfood section: reviewer independence changed the
   verdict dynamics or not.

## Bookkeeping on close

- Plan 009 → `done` once D1–D5 are all decided and executed/recorded.
- Backlog rows updated in the same commits as the work (status hygiene).
- End-of-session: does PyPI need anything beyond 1.1.0? (standing rule
  from plan 008).

## Stop conditions

- If D1 stalls (user unavailable/undecided), Tasks 2–3 stay blocked —
  do NOT push or publish around it. Tasks 4–5 proceed independently.
- If the parallel session's deploy/ work is still in flight, skip Task 5
  (dirty tree) rather than stashing someone else's changes.
