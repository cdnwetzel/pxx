# Dogfooding pxx: A Plan for Self-Improvement

> Backlog ID: **001**. See [plans/backlog.md](backlog.md) for the inventory.
>
> Status: **in-progress**. Blocks: `—`. Blocked by: `—`. Sub-plans: `#010` (Tier 1, done), `#011` (Tier 2, done), `#012` (Tier 3, planned).
>
> Three sibling plans were extracted from this document so each could be
> reasoned about independently — see [Coordination with other plans](#coordination-with-other-plans).
> No code in this document is being executed. It exists to capture the design
> intent for using pxx to improve pxx; specific *mechanisms* live in the
> sibling plans.

## Context

pxx is a thin wrapper around aider for offline Python coding. As of the current
commit it's functional: 14 tests pass on both Neo and Studio, ruff is clean,
both GitHub repos are in sync, hard guardrails are enforced by `.aiderignore`
and `CONVENTIONS.md`.

The natural next question: **can we use pxx to improve pxx?**

This document says yes, with constraints, and lays out a phased path from
"observation only" to "bounded autonomy" — explicitly stopping short of full
self-modification. The intent is to take advantage of the same dogfooding loop
that drives Claude Code itself (Anthropic reports 70–80% internal daily use and
90% of Claude's code is written by Claude) while applying it at the scale of a
solo personal tool.

## What dogfooding means here

In this context, dogfooding means **using `pxx` (the running CLI) to maintain
and evolve `pxx` (the codebase)**. Concrete shapes this takes:

- **The tool builds its own features.** *"Add a `--dry-run` flag that shows
  planned changes without executing them."* pxx analyzes its own codebase,
  writes the implementation, runs the test gate, fixes any failures.
- **Self-debug on failure.** When pxx crashes or behaves oddly, the next
  session opens with the transcript: *"You just failed when trying to refactor
  endpoints.py. Identify the bug, propose a fix."*
- **UX validation through daily use.** If pxx is annoying (too verbose,
  unnecessary confirmations, slow startup), the user feels the pain first and
  immediately tells pxx to fix it: *"Modify your default response format to be
  more concise."*
- **Scale and edge-case testing.** Tasking pxx with a non-trivial refactor of
  its own architecture exercises long contexts, multi-file edits, and safe-edit
  decisions far more realistically than synthetic benchmarks.
- **Recursive improvement loop.** Fix a bug once → tell pxx to find similar
  bugs in the rest of the codebase → it generalizes the pattern.

What does **not** count: pxx autonomously deciding what to improve and running
unattended. That's a separate research direction (Darwin Gödel Machine, SICA,
HyperAgents — see References) and is explicitly out of scope.

## Why this matters for pxx specifically

Four reasons dogfooding is uniquely valuable for an agentic CLI like pxx:

1. **Trust building** — if pxx can reliably modify its own code (the most
   sensitive codebase the user owns), it earns trust for production work
   elsewhere.
2. **Dogfooding as the test suite** — if an update breaks pxx's ability to
   edit its own handler, the user discovers that *during the very next use*,
   not via CI or a downstream user report. Unit tests can't catch "the agent
   handles large messy codebases badly."
3. **Autonomy calibration** — the user discovers where the agent needs more or
   less autonomy by watching how comfortably it modifies its own critical
   paths. Areas where it stumbles get tighter guardrails; areas where it
   excels can be loosened.
4. **The 20/80 economics** — build the first 20% manually (which we've now
   done: scaffold, endpoint detection, system prompt, guardrails, tests). The
   next 80% (features, polish, lint cleanups, prompt tuning) is built by the
   tool itself. The first commit was hand-written; commit number N can be
   asked for and reviewed.

## Coordination with other plans

This plan is the **umbrella** for self-improvement. After the initial draft,
three sibling plans were extracted so each could own a well-scoped piece of
machinery. The tier path below relies on them rather than redefining their
mechanisms:

- **#002 (Safety foundation)** owns the pre-session safety tag, the
  pre-commit hook (test + lint gate + diff cap), and the self-sanity import
  check. Tier 3 here *uses* #002's mechanisms; it does not redefine them.
- **#003 (Scoping & dry-run)** owns the `--scope` flag, the `--dry-run`
  pass-through, and the trusted-paths config. Tier 2 here benefits from
  `--dry-run`; Tier 3 uses `--scope` to bound each autonomous session to a
  single module.
- **#004 (Session audit log)** owns the per-session JSONL log under
  `$XDG_STATE_HOME/pxx/sessions/`. Tier 4 here consumes that log as the raw
  data source for `learnings.md`.

**Status implication:** this plan stays `blocked` until both #002 and #003
reach `done`. #004 is not a hard blocker but materially improves the quality
of Tier 4.

**What this plan still owns (not delegated):**

- The **tier path itself** — the staged progression from observation to
  bounded autonomy, with the criteria that gate each transition.
- The **philosophy** — why dogfooding is uniquely valuable here, why we stop
  short of Tier 5, what 20/80 means for a personal tool.
- The **success metrics** below — how we'll know dogfooding is working.

## The bootstrap problem (and why it's mostly solved)

The meta-challenge: **you must already have a minimally functional agentic CLI
before you can dogfood effectively.** The first 20% — file I/O, command
execution, endpoint detection, prompt loading, test harness, guardrails — has
to be hand-built. Everything past that the tool can help build, *as long as*
its baseline is reliable enough that its self-improvements don't compound bugs.

pxx already meets that baseline:

| Prerequisite                       | Current status                                       |
| ---                                | ---                                                  |
| Test suite covers core logic       | 14 tests on pure helpers (`tests/test_*.py`)         |
| Lint clean                         | `uv run ruff check .` passes                         |
| Both machines run identical code   | `rsync` + `git deliver` to two remotes               |
| Bad commits are revertable         | git on both Neo and Studio                           |
| Hard files are off-limits to aider | `.aiderignore` (model-settings, scripts, pyproject)  |
| Model knows the rules              | `CONVENTIONS.md`, `CLAUDE.md`, `.aider.conf.yml`     |
| Bad changes detected fast          | `uv run pytest -q`, `uv run ruff check`              |
| Editor-process separation          | pxx wrapper edits live; running session loads stale  |

The bootstrap risk is mitigated because:

1. **Reversibility** — every aider edit becomes a git commit; bad changes are
   `git reset` or `git revert` away.
2. **Test gate** — tests catch the most likely regression class (broken
   `cli.py` / `endpoints.py`).
3. **Guardrails** — `.aiderignore` prevents aider from touching the most
   dangerous files (`model-settings.yml`, install scripts).
4. **Two-machine separation** — changes are made on Neo, validated, then
   propagated. The Studio's working installation stays intact until rsync or
   `git collect` pulls them in.

## Phased path

### Tier 1 — Observation only  *(largely already live)*

**Goal:** pxx can see itself. No edits.

**Reviewer-first default (commit `957e4d0`) already makes Tier 1 mostly free:**
running `pxx` in the pxx repo without `--edit` loads the system prompt,
endpoints, and (when #003 lands) optional `--scope` — all read-only. The
`--self-audit` flag from the original sketch is therefore redundant; the
equivalent is `cd <pxx-repo> && pxx`.

What's still worth adding here:

- `pxx --self-test` → runs `uv run pytest -q` against the pxx repo regardless
  of cwd. Useful as a portable health check from any project.
- `pxx --self-lint` → same for `uv run ruff check .` and `uv run ruff format --check .`.

**Implementation cost:** ~30 lines in `cli.py`. No new deps.

**Risk:** zero.

**Success criterion:** the ad-hoc ritual `cd ~/ai/code_pro/pxx && uv run pytest -q`
is replaced by a one-word command runnable from anywhere on either machine.

### Tier 2 — Suggested changes, human-approved

**Goal:** pxx proposes improvements; the human decides which to apply.

- `pxx --self-improve` opens a normal aider session in the pxx repo with a
  structured opening prompt: *"Review the codebase for improvements. Output a
  numbered list of suggested changes with file:line references. Do not edit any
  files."*
- The session writes its suggestions to `plans/suggestions-YYYY-MM-DD.md`
  (or to chat output the user can copy).
- The user reviews, picks one or two items, and runs a regular `pxx` session
  to implement them — same review/commit/test flow as any other change.

Already supported by the existing aider workflow plus the `/audit` slash
command. Tier 2 just formalizes the convention and gives it a flag.

**Implementation cost:** documentation + one CLI flag.

**Risk:** low. Aider in the pxx repo respects `.aiderignore` and `CLAUDE.md`
already; this just constrains the opening prompt.

### Tier 3 — Bounded autonomous edits

**Goal:** pxx can make small, reversible improvements with minimal supervision.

**Requires #002 (Safety foundation) and #003 (Scoping & dry-run) to be `done`.**
Tier 3 doesn't reinvent their mechanisms — it composes them into a workflow:

- **From #002**: pre-session safety tag, pre-commit hook (test + lint gate),
  per-session diff cap, self-sanity check on launch.
- **From #003**: `--scope` to bound each session to a specific module/dir.

What Tier 3 itself adds on top:

- A **workflow convention**: each session opens as
  `pxx --edit --scope <path>` targeting one module, makes **one focused
  change**, lets the pre-commit hook gate the commit, then stops.
- An **`[autonomous]` commit-message tag** so self-driven commits are
  filterable from manual ones.
- An optional **`pxx --self-fix "<task description>"`** subcommand that
  automates the open-session + close-session loop for unattended runs.
- A **no-push rule**: Tier 3 commits stay on the local branch. `git deliver`
  remains a deliberate human action.

Realistic Tier-3 tasks once #002 and #003 land:

- *"Add a `--rollback` that runs `git reset --hard pxx-pre/<latest>` after
  a confirmation prompt."* — uses #002's tags directly.
- *"You just crashed in `_in_git_repo` when run from a submodule. Reproduce,
  fix, add a regression test."* — the self-debug case from the why-section.
- *"Refactor `endpoints.py` to extract `_candidates()` so it's testable
  independently."* — `--scope pxx/endpoints.py` keeps the change surgical.

**Implementation cost:** small once #002 and #003 are in place — mostly a
workflow doc plus the optional `--self-fix` subcommand.

**Risk:** medium. Bad commits are local-only, easy to revert via #002's
safety tag. Don't propagate to Studio or GitHub without user action.

### Tier 4 — Learnings loop

**Goal:** pxx accumulates lessons across sessions and feeds them back into its
own system prompt.

**Materially improved by #004 (Session audit log).** Without #004, Tier 4
relies on memory; with #004, the JSONL log is the raw data source.

Pattern:

- Keep a `pxx/prompts/learnings.md` file with one-line behavioral lessons:
  - *"model often forgets that `.aider.conf.yml` is in `.aiderignore`"*
  - *"model proposes new deps without justification — reinforce the rule"*
  - *"`/refocus` should mention 32K context window explicitly"*
- On startup, `cli.py` adds `learnings.md` to aider's `--read` context list
  so every session sees the accumulated lessons.
- **#004's JSONL log is the input**: periodically (manually at first), grep
  the log for patterns — repeated revert+retry cycles, files that always
  trigger circuit-breaker trips (#002), sessions that ended with the same
  kind of failure. Distill those observations into one-line entries in
  `learnings.md`.

This is **not** model fine-tuning — it's context-engineering. Durable
behavioral steering through plain text files, version-controlled like
everything else.

**Implementation cost (assuming #004 is `done`):** create `learnings.md`,
add a `--read` line in `cli.py`, document the append-from-#004-log convention.

**Implementation cost (without #004):** same code, but `learnings.md` has to
be hand-maintained from memory — much lower signal.

**Risk:** low, but watch for the file growing unbounded. Periodic compaction
is itself a dogfoodable task ("read `learnings.md` and merge duplicates").

### Tier 5 — Full self-evolution

**Out of scope.** A personal tool is the wrong venue for Darwin-Gödel-style
recursive self-modification. If we ever want to explore this, fork to a
separate research project.

## Safety boundaries

Hard limits enforced by code/config, not just prompts:

1. **`.aiderignore` is sacred.** Adding entries is fine; removing them is a
   manual operation. Self-modes that *remove* guardrails are blocked.
2. **Tests must pass to commit** in Tier 3. Pre-commit hook enforces this, not
   vibes.
3. **No self-mode pushes.** `git deliver` / `git push` are explicit user
   actions only.
4. **Self-modes do not touch the Studio.** They operate on the local working
   tree of the machine they're run on. Studio receives changes via explicit
   `rsync` or `git collect`.
5. **No self-modification of governance files** even in Tier 3 — `.aiderignore`,
   `CONVENTIONS.md`, `CLAUDE.md`, `model-settings.yml`, `config/aider.conf.yml`,
   `.aider.conf.yml`, `pyproject.toml`, install scripts. Humans change these.
6. **Per-session diff cap** in Tier 3. Refuse to commit if the diff exceeds N
   lines (e.g. 100) without an explicit override flag.
7. **No environment writes.** Self-modes do not touch `~/.zshrc`, the tool
   venv, `.venv/`, or any install state. Code only.

## What NOT to do

- Don't add `pxx --self-improve --apply` that combines suggest + apply. Keep
  them separate so the user can review.
- Don't run pxx self-modes in a loop. No `while true; pxx --self-fix`. The
  session must end and re-launch.
- Don't store dogfooding state in a database. Plain files only.
- Don't let pxx modify its install environments. Code changes only.
- Don't let pxx silently rewrite `learnings.md` without diff review (at least
  until Tier 4 is stable).

## Triggers and invocation

- **Manual flags** (`--self-test`, `--self-lint`, `--self-audit`,
  `--self-improve`, `--self-fix`) — start here.
- **Pre-commit hook** in the pxx repo to enforce the test gate. Set up via
  `setup-*.sh`.
- **Scheduled** (cron/launchd) — defer indefinitely. Solo developer; no need.
- **CI** — none planned. The two-machine + dual-remote setup is the substitute.

## Success metrics

After 4 weeks of routine dogfooding:

- Test count grew without coverage gaps
- Ruff issues stayed at zero
- No README/code drift like the `qwen2.5` vs `devstral` mismatch caught in
  `review/04-observations.md`
- The `review/` directory has fewer observations needing follow-up
- pxx ran without crashing after every self-edit cycle
- `learnings.md` (if Tier 4 reached) has under 50 lines and no duplicates
- At least one substantive feature (`--dry-run`, `--rollback`, response-format
  tightening, etc.) was added by pxx itself — i.e. **the 20/80 ratio actually
  shifted toward 80%**

If any of these fails, regress one tier and reassess.

**The honest test:** if pxx is not good enough to handle its own development,
it's not ready for anyone else's. Each tier should pass that bar before being
treated as "done."

## Open questions

1. **Exit code semantics** for `pxx --self-test`: Unix-style (non-zero on
   failure) or informational (always 0)? Recommended: Unix-style.
2. **Location of `learnings.md`**: `pxx/prompts/learnings.md` (auto-loaded with
   the system prompt) or repo root (more discoverable)? Recommended:
   `pxx/prompts/`.
3. **Commit-message tag** for Tier 3 autonomous commits: `[autonomous]`,
   `[self]`, `[pxx]`? Pick before Tier 3 starts.
4. **Cross-machine question**: if the Studio runs `pxx --self-improve` and the
   Neo doesn't see changes until `git collect`, is that fine? Probably yes —
   the Neo's user-facing role is the development node anyway; the Studio is
   the model host. Self-edits should happen on the Neo by default.

## Critical files for implementation reference

When Tier 1 starts, these files will need to be touched:

- `pxx/cli.py` — add flag parsing + the three `--self-*` paths (Tier 1)
- `CLAUDE.md` — document the self-modes once they exist
- `README.md` — same
- `.aider.conf.yml` *(project)* — may benefit from a Tier-2-specific config
- `scripts/setup-neo.sh` and `scripts/setup-studio.sh` — install the
  pre-commit hook when Tier 3 starts
- `pxx/prompts/learnings.md` *(new)* — Tier 4

Existing primitives to **reuse**, not duplicate:

- `pxx/cli.py:_in_git_repo()` — already a clean check for cwd-is-a-repo
- `pxx/cli.py:_find_aider()` — already resolves the aider binary path
- `pxx/commands/audit.md` — the read-only-review slash command, ready to be
  loaded by `--self-audit`
- `config/aider.conf.yml` lint/test command wiring — already routes through
  `uv run`

## Verification plan (for when implementation starts)

Each tier is verified independently before the next is started.

**Tier 1:**
- Run `pxx --self-test` from `/tmp/some-empty-dir` — output should match
  `cd ~/ai/code_pro/pxx && uv run pytest -q`.
- Run `pxx --self-lint` likewise.
- Run `pxx --self-audit` — confirm aider opens, system prompt loads, audit
  prompt loads, no file edits proposed.

**Tier 2:**
- Run `pxx --self-improve` — confirm aider opens with the constrained prompt
  and produces a markdown list of suggestions without editing any files.

**Tier 3:**
- Make the pre-commit hook reject a deliberate test break.
- Confirm the diff-size guard blocks a >100-line change.
- Confirm `git deliver` still requires explicit user action.

**Tier 4:**
- Add a one-line lesson to `learnings.md`, run `pxx` in another project, and
  verify the lesson is in the system prompt via aider's transcript.

## Execution after approval (out of scope of this plan)

This plan, once approved, should land at `plans/dogfooding.md` inside the pxx
repo — committed with a high-quality message capturing the intent, and pushed
to both `cdnwetzel/pxx` and `mirror/pxx` via `git deliver`. Rsync to Studio per
the standard workflow.

Adopting this plan does **not** approve any of the tier implementations. Each
tier is a separate decision that requires its own approval. This document is
the design note; implementation lives in future PRs/commits.

## References

- [Recursive Self-Improvement: Building a Self-Improving Agent with Claude Code (David Oliver, 2026)](https://medium.com/@davidroliver/recursive-self-improvement-building-a-self-improving-agent-with-claude-code-d2d2ae941282)
- [SSI-FM: A Self-Improving Coding Agent (ICLR 2025)](https://openreview.net/pdf?id=rShJCyLsOr)
- [Darwin Gödel Machine — Sakana AI](https://sakana.ai/dgm/)
- [Dogfooding with Rapid Iteration for Agent Improvement (Agentic Patterns)](https://www.agentic-patterns.com/patterns/dogfooding-with-rapid-iteration-for-agent-improvement/)
- [Self-Improving Coding Agents (Addy Osmani)](https://addyosmani.com/blog/self-improving-agents/)
- [How to Build a Self-Improving AI System with Claude Code (Product Compass)](https://www.productcompass.pm/p/self-improving-claude-system)
- [A Self-Improving Coding Agent (arxiv 2504.15228)](https://arxiv.org/html/2504.15228v2)
