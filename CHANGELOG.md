# Changelog

All notable changes to pxx are documented here. The 1.x series history is
preserved in git (tag `v1.3.3` and earlier).

## [2.5.3] — 2026-08-18

### Changed

- **Greenfield gate soundness locked with a regression guard.** A clone-from-docs probe
  raised a concern that the regression-relative test gate might *vacuously pass* broken
  greenfield code (a node appeared to reach `COMPLETED` on a module with a `SyntaxError`).
  A **deterministic reproduce disproves it**: `run_loop` on a suite that fails from round 1
  terminates `LOOP_DETECTED` / a non-success code, **never `COMPLETED`**
  (`tests/test_loop.py::test_greenfield_failing_baseline_never_completes`) — the apparent
  pass was a confound (lost per-round log + a sandbox whose state did not match the recorded
  outcome), the same failure mode as the earlier "planner fails greenfield" report. The gate
  is sound; a regression guard now locks that in and the roadmap claim is withdrawn. No
  behaviour change — a test + doc correction that hold the gate to its negative-control
  discipline (reproduce cleanly before carding a gap).

## [2.5.2] — 2026-08-18

### Added

- **Goal planner runs on the `roles.plan` lane.** `pxx goal`'s read-only planner now
  resolves `Settings.effective_role("plan")` (via a testable `_planner_settings` helper),
  so a reasoning/planning model can decompose the goal into the task DAG while the coder
  builds it — the second consumer of the 2.5 role-lane map, matching a separate PLAN role
  (reasoning brain ≠ builder). Falls back to the coder model when `[roles.plan]` is unset
  (byte-identical to before). Surfaced by the clone-from-docs probe. NOTE (honesty): the
  probe's initial "planner fails greenfield with OUT_OF_SCOPE" report was a **harness
  artifact** — a `uv run --directory` invocation ran the planner in the wrong repository; a
  clean reproduce shows `pxx goal` works greenfield end-to-end. So this is an architectural
  enhancement (reasoning-planner support), not a bug-fix. +2 tests.

## [2.5.1] — 2026-08-17

### Fixed

- **Embedding-space versioning for observation memory (fail-closed on embedder swap).**
  Stored vectors carried no record of which embedder produced them, so changing the memory
  embedder (or the `roles.embed` model) silently made `search()` compute cosine across
  incomparable spaces — confident garbage with no error, and worst in observation memory,
  which accumulates for months and is never reindexed. The store now stamps its
  embedding-space identity in a `meta` table, and `set_embedder` fails closed with
  `EmbeddingSpaceError` when a *different* identity is attached over existing vectors —
  targeting the dangerous **same-dimension / different-model** case (a plain dimension
  mismatch already threw). Recovery: `MemoryStore.reset_embedding_space()` clears the
  vectors + stamp and detaches the embedder (re-add observations to re-embed under the new
  embedder — it does not auto-reindex), or repoint to the original embedder. Empty stores
  adopt a new embedder freely; pre-versioning
  stores with vectors assume the current embedder (logged warning, not a hard fail).
  Embedders now expose an `identity` (`HashEmbedder` → `hash:dim=N`, `OllamaEmbedder` →
  `ollama:<model>`). This is the retrieval analogue of the content-truthfulness gate —
  refusing to answer from a corrupt index is refusing to fabricate, not judging. +10 tests
  including a same-dim/different-model negative control. Prerequisite for the forthcoming
  repo code index; flagged by PSAIOS in coordination.

## [2.5.0] — 2026-08-17

### Added

- **Generalised per-role model lanes — `[roles.<name>]`.** The reviewer overlay
  (`[roles.review]`, shipped 2.2.0) is widened to a **closed, validated role-lane
  map**: `author`, `reviewer`, `plan`, `fast`, `verify`, `embed`. Each lane names a
  `(provider, model, base_url, api_key)` quadruple resolved late against the coder
  `model`, so different roles can run on different families/endpoints — the driver
  is **family independence on the judgment axis** (the reviewer/verifier should not
  share the author's lineage). `Settings.effective_role(name)` is the one resolver
  role consumers call. New per-lane env parity `PXX_<ROLE>_{MODEL,PROVIDER,BASE_URL,
  API_KEY}` (reviewer keeps `PXX_REVIEW_*`). `review` is a back-compat alias for
  `reviewer`; `review_model` / `[roles.review]` / `PXX_REVIEW_*` / `--review-*` are
  unchanged and the reviewer path is byte-identical (proven by the existing review
  suite staying green). An unconfigured box is byte-identical to before — every
  unset lane resolves to the coder `model`. The closed set is fail-closed on an
  unknown role; opening it later is non-breaking, narrowing is not.
- **Egress guard covers every lane.** Role routing is a data-egress surface for all
  lanes (the diff for the reviewer, source chunks for `embed`, the prompt for any
  lane), so the entire `[roles]` table is honoured only from user config, env, or
  CLI — a repo-local `pxx.toml` can redirect **no** lane (ignored with a warning).
  Every lane inherits the reviewer's exfil protection by construction. pxx owns
  role→model **name**; endpoint/node **placement** stays a pluggable adapter (never
  a second placement authority) — advancing the ROADMAP "model-backed boundary
  roles" item. `embed` is reserved for the forthcoming repo code index.

### Fixed

- **User-config symlink trust check now uses the repo root, not just cwd.** The
  symlink-into-project guard (which downgrades a `~/.config/pxx/config.toml`
  symlinked to repo-editable content to untrusted) compared the target against
  `cwd`. From a **nested** working directory, a symlink targeting a repo file that
  is an *ancestor* of cwd escaped the check and was trusted — letting a repo file
  smuggle `allow_ungated_shell` / `memory_capture_successes` / hooks / role-lane
  routing past the A0b gate. Containment is now checked against the repository root
  (`_repo_boundary`, nearest `.git` ancestor), closing the bypass; regression test
  added. Found pre-flight by CodeRabbit while reviewing the role-lane map (whose
  new lanes widened this guard's responsibility).

## [2.4.4] — 2026-08-16

### Changed

- **Review prompt: severity discipline to stop small reviewers over-flagging.** The review gate's
  system prompt (`pxx/prompts/review.md`) told the model to `APPROVE` only with "no high or medium
  findings" but never that **style, comments, renames, test-only edits, import order, and refactors
  that keep tests green are not defects** — so smaller judges rated acceptable-change nitpicks as
  `medium` and voted `REVISE`. Added an explicit rule reserving `high`/`medium` for real correctness
  or safety defects; minor/stylistic observations are `low` at most and never a reason to REVISE.
  Measured on the calibration corpus at `temperature:0`: `qwen2.5-coder:32b` false-positive rate
  **0.429 → 0.143** (now passes `calibration ok`) and `qwen2.5:14b-instruct` **0.429 → 0.286**,
  with **recall unchanged at 0.857** (precision up, no loss of critical-bug detection). This makes
  a fast, GPU-resident local model a viable blocking reviewer. NOTE: the calibration corpus is
  small (14 cases) — treat the magnitude as indicative pending held-out validation on real diffs.

## [2.4.3] — 2026-08-16

### Fixed

- **Reviewer/judge now decodes greedily (`temperature: 0`) — the review gate is reproducible.**
  The model-backed reviewer's request omitted `temperature`, so against an Ollama endpoint the
  server default (0.8) made the judge **nondeterministic**: the same diff produced different
  verdicts run-to-run, and `pxx calibrate` swung across its `MIN_AGREEMENT` / `MIN_RECALL`
  thresholds — so a calibrated reviewer couldn't be trusted to stay calibrated, and a single
  calibration near a threshold was a coin-flip. `pxx/review.py` now sends `temperature: 0`, pinning
  the judge to greedy decoding. Surfaced by a `reachable → unparseable → calibrate → replace`
  reviewer diagnostic that exposed the swing. Test asserts the reviewer payload carries
  `temperature == 0`.

## [2.4.2] — 2026-08-14

### Added

- **Content-truthfulness gate (advisory) — a new axis, separate from permission.** Scope /
  R-014 governs what the agent may *touch*; it does not catch a model that stays fully
  in-scope and still reports something *false* about the code (quoting a comment that isn't
  there, presenting invented code as real). The objective gates (lint/tests/diff-cap) catch
  broken edits, not confident-but-wrong claims. This ships the **first increment: deterministic
  quote-grounding** (`pxx/truthfulness.py`). Every non-trivial code span the model quotes in its
  final narration must appear in content it actually read or wrote — the union of read
  tool-results and **edit-tool** args accumulated across the run. Grounding is checked
  **per-source** (a quote must sit inside a single source, so it can't be assembled from two
  unrelated reads) and only **edit-tool** args count as "written" (a non-edit tool arg is
  model-supplied, not read content, so it can't launder a fabricated quote). Fenced blocks are
  checked **line-by-line** (a model that quotes a function but elides its docstring is being
  terse, not fabricating; a single invented line is what flags), tolerating spaced info strings
  and CRLF; inline spans are checked only when they look like code (whitespace-normalized, no
  model, fully deterministic).
- Wired into the native loop's COMPLETED path as an **advisory, non-blocking, fail-safe** check:
  an ungrounded quote emits a **metadata-only** `content_truthfulness` event (count + a kind
  breakdown, **never** the quoted code — the audit stream carries no file contents) plus a
  warning log, but **never** changes a run's outcome — the `try/except` swallows any checker
  error so an advisory can't break a run. Advisory-first is deliberate: the false-positive rate
  is measured on real runs (like the reviewer's calibration) **before** the gate is ever
  promotable to a heal trigger. Prove-before-you-call-it.
- **Negative control (shipped in the tests):** a fabricated quote MUST flag and a real quote
  MUST pass — a check that cannot go red is not a check. `content_truthfulness` registered in
  the `EVENT_KINDS` allowlist.

## [2.4.1] — 2026-08-11

### Added

- **`--review-model` / `--review-base-url` run flags** — the per-role reviewer/judge
  overlay (`[roles.review]` / `PXX_REVIEW_*`, shipped 2.2.0) is now settable per-run on
  the command line, no config file needed. Available on every command that runs the
  reviewer: the run/loop commands (alongside `--model`/`--base-url`) **and the reviewer
  commands `pxx review` / `pxx calibrate`**. The flags layer as the highest-precedence
  `[roles.review]` source (user TOML → env → flags, per field), so `pxx loop --review
  --model <coder> --base-url <coder-url> --review-model <judge> --review-base-url
  <judge-url>` runs the coder and the judge on different endpoints in one invocation.
  Absent the flags a run is byte-identical to before. Tests: CLI→overlay mapping (run +
  review + calibrate), flag-over-env precedence (model and base_url), and no-flags no-op.

The **Kimi K3 Swarm audit** — an independent architecture + quality audit
(2.8T-parameter frontier model, high-effort) of the repo at `v2.3.7` — landed as
three waves: validated bug/security fixes, then the loop-closing feature, then
the learning-loop completion. Every item rode the normal gate (verified in a real
venv → PR → CI + CodeRabbit). Notably, the Wave 1 memory-capture fix was authored
**by pxx fixing its own bug** on local hardware (receipt **R-034**).

### Added

- **`memory_retrieval_limit` setting + stable settings overlay** (Wave 2 — closes
  the improvement loop): the `improve/` plane (candidates, promotion guards,
  shadow/canary, autopromote) never changed a production run. `memory_retrieval_limit`
  is now a real `Settings` field (default `8` == the historical `_SEARCH_HITS`, so
  an unconfigured box is byte-identical; strict positive-int TOML parse) consumed
  by memory injection. `apply_stable_overlay()` applies the STABLE channel's
  *settings*-class candidate at session start — re-validated (content-hash tamper
  check), budgets **tighten-only** against the current budgets, CLI-pinned keys
  always win, fail-closed but never bricking (a broken/tampered/absent artifact →
  base settings + a warning). This unblocks live (model-scored) eval arms.
- **Opt-in success-exemplar capture** `memory_capture_successes` (Wave 3, default
  **off**, byte-identical when off): a COMPLETED run writes exactly one compact
  `session_outcome` exemplar of **bounded shape metadata only** (files-changed /
  tool-call counts — the raw task prompt is not persisted, since this durable row
  later becomes prompt context), `contamination_risk` below auto-quarantine,
  provenance from the completed-run ladder, deduped so identical verified shapes
  grow `seen_count` — the recurrence signal the graduation ladder consumes.
  Enabling it turns on persistent memory writes, so the key is honoured only from
  a trusted source (user config / `PXX_MEMORY_CAPTURE_SUCCESSES` / CLI — never a
  repo-local `pxx.toml`, A0b). Preserves the Phase 20.5 "no silent
  success-to-knowledge" default. Also: `MemoryStore` dedup now upgrades an
  observation's provenance *label* (not only its numeric confidence) when a
  stronger-evidence recurrence arrives.

### Fixed

- **Memory capture read the wrong event key** (Wave 1, R-034): the `tool_result`
  branch read `result`/`output`, but the tool bus emits `result_preview` — so
  **every real tool-result observation was silently dropped**. Now reads
  `result_preview` first (legacy keys kept as fallbacks); failed tool calls
  (`error=True`) are captured at low confidence so they stay distinguishable.
- **Unbounded `git worktree add`** (Wave 1): `improve/channels.py` and
  `improve/scheduler.py` ran `worktree add` with no `timeout=` — the two sites the
  2.3.6/R-030 git-bounding missed. Both now `timeout=30` and degrade to the copy
  fallback on `TimeoutExpired` (R-030's boundary amended to name the gap).

### Security

- **Fail-closed secrets gate on auto-commit** (Wave 1): `commit_session_work` now
  scans the staged delta (`governance.scan_staged`, already fail-closed) before an
  auto-commit; any finding or an unrunnable scan → no commit, work left staged.
- **The PR-time governance scan is now armed** (Wave 1): same-repo PRs/pushes run
  `pxx check --all-files --require-denylist` (fork PRs stay unarmed with a loud
  warning), closing the 1.3.x silent-green hole where an empty denylist passed
  silently. Arming immediately surfaced — and this release fixes — fleet host
  names leaked into the public receipts corpus; `docs/RECEIPTS.md` now describes
  hardware by capability, not by hostname/IP.

## [2.3.7] — 2026-08-05

Done-signal early-exit — the second half of "clean loop termination". 2.3.6's
predecessor R-017 salvaged an over-worked run's *terminal code* (report
`COMPLETED`, not `BUDGET_EXCEEDED`) but left the over-work itself: a local coder
keeps calling tools past a passing solution until its per-turn budget is spent.
This stops it at the first objectively-complete edit-state.

### Added

- **`pxx loop` done-signal early-exit** (R-031): a per-round coder session whose
  on-disk diff already passes the objective gates (scope + diff-cap + lint +
  tests) stops mid-session and reports `COMPLETED`, instead of burning the rest
  of its budget. Implemented as an injected oracle (`SessionContext.done_check`,
  built by `run_loop`, consulted by the native backend after each edit turn) —
  **no model-visible tool was added, so the tool surface is unchanged**. The
  loop's own review gate still runs on the result. `_edit_objectively_done` is
  shared with the over-work salvage (`_overwork_salvageable` now delegates to it).
- **`done_signal` setting** (default **on**; `PXX_DONE_SIGNAL`, TOML strict
  boolean): turn the early-exit off for a suite slow enough that a mid-session
  test run costs more than the rounds it saves. Only ever fires inside `run_loop`
  with a configured test command; single-shot `pxx run` is byte-identical.

## [2.3.6] — 2026-08-04

The "init-watchdog" follow-up, re-scoped by the evidence: the pre-loop network
ops (memory-embed 30s, MCP-handshake 30s, model-fingerprint 2s) are already
individually bounded — but pxx's **git subprocess helpers were not**, so a
wedged git or a **blocking git hook** (a pre-commit prompt, a credential helper)
could hang a run, most dangerously at the safety-net tie which runs at startup,
before the run's own wall-clock budget exists.

### Fixed

- **Every git subprocess in the run path is now time-bounded** and killed +
  reaped on timeout (`gitenv.communicate_bounded`), matching what the test-runner
  and worktree helpers already did. The three previously-unbounded helpers —
  `safety_net._git` (startup safety net, pre-budget), `loop._git` (per-round
  changed-paths/diff), and `goal._git` (task-DAG git) — degrade on timeout
  (git-unavailable / non-zero) instead of hanging. Bound is `PXX_GIT_TIMEOUT`
  seconds (default 60; positive-finite, else the default).

## [2.3.5] — 2026-08-04

Security: close the `run_shell` auto-mode gap surfaced while documenting the hook
contract in 2.3.4 (see R-029). The README claimed "shell commands are gated";
`auto` mode didn't honor it.

### Security

- **`run_shell` is now fail-closed in `auto` mode, matching `edit`.** `scope`
  confines only the file tools (a shell command has no path target), so an
  unattended `pxx run` could execute arbitrary model-authored shell with **no
  PreToolUse hook and no sandbox** — `auto`'s profile allows the shell class and
  only `edit` enforced the hook requirement. Now `run_shell` in **either** write-
  capable mode requires one explicit safeguard — a `run_shell` PreToolUse hook,
  `sandbox_shell`, or the new opt-in `allow_ungated_shell` (`PXX_ALLOW_UNGATED_SHELL`)
  — else it is denied `HOOKS_MISSING`. `ask`/`plan` still never permit shell.
  This also *relaxes* `edit` slightly: `sandbox_shell` (containment) now satisfies
  the gate there too, not only a hook.

### Added

- **`allow_ungated_shell` config key / `PXX_ALLOW_UNGATED_SHELL`** (default
  `false`) — explicit, named risk-acceptance for an unhooked, unsandboxed shell,
  so the fail-closed default has a deliberate escape hatch rather than a silent one.

### Changed

- `README.md` truthed up: "shell commands are gated **in every write-capable
  mode**" — the claim now holds for unattended `run`.

## [2.3.4] — 2026-08-04

Hardening from pxx's first governed production integration inside a third-party
host (see R-028): a real run surfaced a startup footgun and the fix for it.

### Fixed

- **`pxx run`/`ask`/`edit`/`loop` no longer hang forever on an open, data-less stdin.**
  When invoked with no `-m/--message` and a non-TTY stdin that is *open but never
  delivers data* — a common headless-subprocess footgun (`subprocess.run(...)`
  without `stdin=DEVNULL`) — `_read_task` blocked `sys.stdin.read()` indefinitely,
  waiting for a task that never came (observed as a ~900s "hang" before the run
  even started, so the wall-clock budget could not fire). It now waits a bounded
  window for piped input, then fails fast with the usual "task is required" usage
  error. A real `echo … | pxx` pipe (data ready immediately) and a closed stdin
  (EOF) are both unaffected; a non-selectable stdin (Windows, substituted test
  streams) keeps the historical blocking read.

### Docs

- **`docs/CONFIG.md` §`[[hooks]]` — the PreToolUse payload + path contract.**
  Documents the JSON payload pxx sends (`{"tool", "args"}`), that the hook runs
  with the project root as cwd, and — for scope/boundary hooks — that fs-tool
  `path` args are **repo-root-relative** and must be resolved against a trusted
  root and canonicalized with `realpath` (never `normpath`-then-`realpath`, which
  masks a `symlink/../…` escape) with a boundary-anchored prefix check. The guard
  must resolve the raw path itself, never trust a pre-resolved path from the
  governed run (no confused deputy).
- **`docs/RECEIPTS.md` R-028** — first governed production edit inside a
  third-party host (attested; both enforcement layers exercised on a real write).

### Deferred

- An **init-watchdog** bounding pre-loop startup (memory-embed and MCP-handshake
  can hang before the run's own wall-clock budget engages) is scoped for a
  follow-up — it needs careful async teardown (killing spawned MCP subprocesses
  on timeout) and is deliberately not rushed into the startup path here.

## [2.3.3] — 2026-08-03

Local-first ergonomics and honest diagnostics from an overnight batch: a per-box
review default, a token budget that stops fighting free local inference, a
`pxx doctor` probe that tests *usable* tool-calling under a real context, and
quieter scope accounting.

### Added

- **Per-box `pxx loop` review default (`loop_review` / `PXX_LOOP_REVIEW`).** The
  model-backed review gate stays opt-in per run (`--review`), but a box that
  always wants it can now flip the default with `loop_review = true` (or
  `PXX_LOOP_REVIEW=1`). `pxx loop` gains a matching `--no-review` so a single run
  can turn the gate off even when the setting is on; an explicit flag always wins
  over the config default. The shipped default is unchanged (review off).

### Changed

- **Provider-aware token budget.** On a local provider (`ollama`/`vllm`), where
  tokens are free, the default `max_tokens` ceiling (200k) is lifted to a high
  finite backstop so a real task on a small box isn't cut short with a false
  `BUDGET_EXCEEDED` mid-work. The lift applies only when `max_tokens` is left at
  its `200000` default (any other value — tighter *or* looser — is honoured
  verbatim; an explicit `200000` is indistinguishable from the default and is
  still lifted), and never touches `max_rounds`, `max_wall_seconds`, or
  `max_cost_usd`, so runaways and paid spend stay bounded exactly as before.
  Paid providers keep the configured cap.
- **`pxx doctor` verifies *usable* tool-calling, not just an accepted `tools`
  array (F2).** The old probe sent a one-token "ping" and passed on any HTTP
  200 — and skipped ollama entirely. But accepting `tools` ≠ emitting a
  `tool_call`: some small instruct models return 200 yet answer in PROSE once
  the context is the size of a real loop prompt, which strands `pxx loop`
  (observed on an 8GB box). The probe now sends the real native system prompt
  plus an unambiguous file-read task and requires the response to contain a
  structured tool call, running for every provider including ollama. A
  prose response is an actionable F2 warning; still fail-soft (never a hard
  doctor failure).

### Fixed

- **Interpreter/test caches no longer trip OUT_OF_SCOPE or inflate the diff.**
  When a target repo doesn't `.gitignore` them, the loop's own `test_command`
  (pytest/mypy/ruff) creates `__pycache__/`, `*.pyc`, `.pytest_cache/`, etc.
  *after* the agent's edits — and `_changed_paths` counted them as changes,
  which could false-trigger `OUT_OF_SCOPE`. These never-agent-authored artifacts
  are now filtered from the changed-path set (by path component, so nested dirs
  are covered).

## [2.3.2] — 2026-08-02

Three fixes surfaced and independently re-verified by the second-lane (8GB
portable) degrade campaign behind R-023.

### Fixed

- **Safety net restores the working tree on abort, including UNTRACKED files
  (F1).** The net stashes a dirty tree (`--include-untracked`) at session start;
  on an ABORT (e.g. `MODEL_UNAVAILABLE`, which does zero work) the user's
  uncommitted files were stranded in a stash — the `pxx-pre` tag restores tracked
  files but not untracked ones. An aborted run now resets to the tag (discarding
  the failed run's partial edits) and pops the net's own stash, restoring
  tracked-dirty AND untracked. Fail-soft. Done last, after the run artifact is
  written, so the restored WIP is never captured into `diff.patch`.
- **A 404 / model-not-found advances the fallback chain (F3).** A reachable
  endpoint that doesn't serve the requested model id (HTTP 404, or a 400/422
  body naming a missing model) now walks `[[fallback_models]]` — same as an
  unreachable endpoint — instead of hard-failing `MODEL_UNAVAILABLE`. A 404 on
  the last endpoint still raises.

### Changed

- **Scope: reads span the repo, writes stay in `scope` (F5).** A single-file or
  single-dir `--scope` no longer blocks *reads* — the agent may read anything
  under the project root (tests, imports, context) but may only WRITE within
  `scope`. Reads still cannot escape the root. The broker authorizes read-class
  actions via `check_read`, write-class via `check_write`.

## [2.3.1] — 2026-08-02

Fixes from a two-session portable-box dogfood (primary GPU coder / on-device
degrade). Truthfulness + degrade correctness.

### Fixed

- **Auto backend lane honors the fallback chain (BUG A).** `pxx ask/edit` picked
  aider on binary presence alone, and aider ignores `[[fallback_models]]` — so
  with the primary endpoint down it sat in litellm retries instead of degrading.
  The auto lane now prefers native when a fallback chain is configured (only
  native honors it). `run`/`loop` were already native; an explicit `--backend`
  still wins.
- **A provider-down aider run is `MODEL_UNAVAILABLE`, not `COMPLETED` (BUG B).**
  aider exits 0 even when the endpoint is down/overloaded — it prints the
  provider error and makes no edit, which fell through to a phantom `COMPLETED`
  (tokens=0). Now reclassified when nothing was edited and the transcript matches
  a distinctive endpoint-down signature.
- **`--budget-rounds` can raise the loop round cap (DF-02).** It fed only the
  BudgetGuard (which tightens), never `run_loop`'s `max_rounds` (default 3), so a
  loop needing 4+ heal rounds always hit ROUND_CAP at 3.
- **`BUDGET_EXCEEDED`/`EDIT_TIMEOUT` report the real tokens spent, not 0.** The
  session omitted the token count on those terminal branches, so an over-work run
  recorded tokens=0 — under-counting `real_runs` and hiding the over-work.
- Budget flags reject non-positive and non-finite values (`--budget-rounds 0`,
  `--budget-seconds nan`/`inf`) at parse.

### Added

- **`backend` config key / `PXX_BACKEND`** (`native`/`aider`/`auto`) — a durable
  per-box backend posture without a per-invocation `--backend` flag.

### Docs

- `docs/MIGRATION.md`: the 1.x→2.x reorg removed importable modules (`pxx.scope`,
  `pxx.audit`) that downstream integrations depended on — drive pxx via its CLI.
- `docs/CONFIG.md`: the portable / single-box degrade pattern and the backend
  posture key.

## [2.3.0] — 2026-08-01

Reliability and safety hardening surfaced by a full **zero-intervention dogfood**
of pxx on itself and on a live codebase. The reasoning-judge blocking gate and
the earned-enablement counters are now trustworthy end-to-end. Receipts
R-013…R-021 record the exact configurations and negative results.

### Added

- **Reasoning-judge structured verdict contract** (#14): the blocking review gate
  parses a strict `response_format` json_schema verdict first, then falls back to
  free text, so a reasoning judge reliably emits a parseable APPROVE/REVISE with
  file-anchored findings that block. Endpoints that ignore `response_format`
  retry as plain text — same reliability as before. Validated on real hardware
  (qwen3.5 in `--review-mode blocking`, 6/6 parseable).
- **Durable `real_runs` ledger** (#15): the earned-enablement `real_runs` count is
  reconciled through an append-only `real-runs.jsonl` in the state dir, so an
  external run-dir clear no longer erases accrued progress. Records each genuine
  run once by id; persistence is best-effort; a run dir whose canonical path
  escapes `runs/` (symlink or symlinked ancestor) is rejected; an
  undecodable/corrupt ledger fails closed.

### Fixed

- **`real_runs` counts only genuine runs** (#11): `mock`/`replay` backends,
  crashes before a terminal outcome, and zero-work connection failures no longer
  inflate the bar — only real-backend runs with a recorded outcome and token or
  diff evidence count.
- **Daemon status reports real liveness** (#13): `pxx improve status` now reports
  **running / paused / stopped** from the `daemon.lock` flock, instead of always
  claiming "running" from the pause flag. A daemon that is not running reads
  `stopped`.
- **Clarity gate no longer false-blocks a described artifact** (#16): the
  missing-file signal is governed per path — it gates only when an edit verb is
  the nearest cue to a specific path within its clause, so a task that merely
  *describes* a generated/runtime artifact ("…so it emits `out.json`") proceeds.
  Genuine ambiguity still gates. Untrusted task paths with a `..` segment are
  ignored (no cwd-escaping probe).
- **Clean termination for over-worked runs** (#12): a run that exceeds its budget
  but produced a verified, in-scope edit is salvaged to COMPLETED — honoring the
  scope, diff-budget, lint, test, and review guards — instead of terminating as
  BUDGET_EXCEEDED with work stranded.

### Changed

- PR CI runs `ruff format --check` for parity with the release `verify` gate (#9),
  so a format-only drift is caught on the PR, not at release time.

## [2.2.0] — 2026-08-01

First step toward the ROADMAP "model-backed boundary roles" item: the
reviewer/judge can now run on a different model and endpoint than the coder,
so a modest two-box setup (a GPU-box coder + a Mac judge) is expressible in
config alone — no code change, degrades cleanly to a single endpoint. Verified
on real hardware: a qwen3-coder coder on an RTX 5060 Ti + a Mac judge complete
one autonomous `pxx loop --review`.

### Added

- **`[roles.review]` per-role model overlay** (config + `PXX_REVIEW_MODEL` /
  `PXX_REVIEW_PROVIDER` / `PXX_REVIEW_BASE_URL` / `PXX_REVIEW_API_KEY`): an
  optional `Settings.review_model` that the reviewer construction sites
  (`pxx review`, `pxx calibrate`) resolve via `Settings.effective_review_model`.
  Unspecified fields inherit the coder model, so a lone `base_url` reuses the
  same model on another box. Fail-closed on unknown role names, unknown
  sub-keys, and unknown providers — a typo is an error, never a silent no-op.
  When unset, a run is byte-identical to before the field existed.
  The overlay is stored *sparse* and resolved against the coder model once, at
  the end of layering, so a later `PXX_MODEL`/`PXX_API_KEY` override still
  propagates into the reviewer (no stale early copy). **Reviewer routing is a
  data-egress surface** (the diff and any bearer token go to `base_url`), so —
  like hooks and MCP servers — the overlay is honoured only from user config,
  env, or CLI, and is **ignored (with a warning) from repo-local config**: a
  checked-in `pxx.toml` cannot redirect a review to an attacker endpoint.
- **`pxx loop --review` (opt-in model-backed judge)**: the bounded
  edit→test→review loop can now run its review gate each round, driven by
  `Settings.effective_review_model` — so with `[roles.review]` set, the judge
  runs on a different model/endpoint than the coder. `--review-mode
  blocking|advisory` (default blocking) selects whether a REVISE heals/fails
  closed or is reported only. Without `--review` the loop is unchanged
  (`reviewer=None`, gate skipped) — the flag lives only on `loop`, never as a
  silent no-op on `ask`/`edit`/`run`.
- **Reasoning-model judges supported**: the review parser strips
  `<think>…</think>` / `<thinking>` scratchpads (closed pairs and a dangling
  unclosed opener) before reading the `VERDICT:` line and findings, so a
  reasoning judge (qwen3.5, deepseek-r1, qwen3 `/think`) that reasons "aloud"
  toward one verdict and then finalises another is parsed from its final
  answer, not its scratchpad. No-op for non-reasoning reviewers. The review
  prompt now tells reasoning models to keep the verdict out of `<think>`.

### Fixed

- **Memory tools silently dropped every observation** (found while
  dogfooding the two-box loop): `MemoryStore.add`/`search` are `async`, but
  the `remember`/`recall_memory` agent tools and the MCP server called them
  without awaiting — the coroutine was discarded (a `RuntimeWarning`), so
  nothing was persisted and `recall_memory` errored against the real store.
  All three call sites now await via a shared `await_if_needed` helper (also
  de-duplicating the copy in `pxx serve`); the HTTP server was already
  correct. Regression-guarded by async-store test doubles in the tool and MCP
  suites, and verified against a real `MemoryStore` with the un-awaited
  warning promoted to an error.

## [2.1.7] — 2026-07-30

### Fixed

- **`pxx upgrade` no longer claims an upgrade it cannot prove** (PR #7).
  Found by dogfooding the 2.1.6 rollout minutes after publish: `uv tool
  upgrade` exits 0 on "Nothing to upgrade" (the index hadn't served the new
  release yet), and the old code trusted `rc==0` — printing
  "upgraded 2.1.5 -> 2.1.6" while the install stayed at 2.1.5. After the
  upgrade command succeeds, pxx now re-runs the installed entry point
  (preferring the console script that launched the process over a PATH
  lookup, so a shadowing second install is never consulted) and compares
  versions: match or newer (an index-ahead release counts as success) →
  verified success naming the version actually installed; older → an honest
  error with a stale-index hint; probe unavailable → success with an
  explicit unverified caveat. The probe is bounded (20s), parses only a
  digit-led `pxx <version>` banner token (immune to warning noise and
  pre-release truncation), and a cancelled or timed-out probe child is
  killed and reaped — no orphan process, no "Event loop is closed" teardown
  noise. Ten new tests, including a real hung-child kill-and-reap
  end-to-end.

### Notes

- Pre-release review: CodeRabbit PR review (1 actionable finding, fixed:
  the index-ahead race above) and a three-round adversarial Claude pass
  (all findings closed, final verdict APPROVED) — both lanes converged.

## [2.1.6] — 2026-07-30

### Changed

- **Unknown flags that name a subcommand now hint the right spelling**:
  `pxx --upgrade` previously printed only `ignoring unknown flag: --upgrade`
  (the tolerant 1.x compat handler) and went on to demand a task — while the
  user's intent was unambiguously the `upgrade` subcommand. Dash-prefixed
  unknown flags whose name matches a subcommand now append
  `(did you mean 'pxx upgrade'?)` to the existing warning. Native-backend
  ignore path only; the aider flag-forwarding compat path is unchanged, and
  the hint never leaks into the task text.

### Notes

- The long-open py3.13/aider work order closed as resolved-by-redesign:
  the v2 packaging already fences the broken chain (`[aider]` extra is
  marker-gated `python_version < '3.13'`; core stays uncapped because the
  native backend runs on 3.13). Verified against the published 2.1.5 wheel
  on an isolated 3.13 interpreter during this release's review pass.
- Pre-release review: CodeRabbit CLI and an independent adversarial Claude
  pass on the release delta — zero findings each.

## [2.1.5] — 2026-07-29

The triage loop (PR #6): human verdicts on improvement proposals are now
recordable and DURABLE. Found on the daemon's first live day — a human
rejected a proposal and the very next hourly cycle re-surfaced the
identical signature, because nothing consulted human verdicts.

### Added

- **`pxx improve triage list|qualify|reject <slug> --note --by`**:
  records the verdict with reviewer identity + timestamp and moves the
  entry out of `human-review-required/` atomically. Rejections require
  a note — the rationale is the record's value. Unknown slug, noteless
  reject, or a corrupt entry exits 2, never a traceback; unreadable
  inbox entries are surfaced in the listing, not hidden. Slugs are
  validated to the 12-hex inbox shape (a crafted slug could previously
  reach filesystem paths).
- **The cycle honors human dispositions**: signatures with a
  `disposition` record in `qualified/` or `rejected/` are skipped
  (`human-dispositioned` in the report) instead of re-proposed every
  tick. The cycle's own auto-rejections carry no disposition and stay
  re-routable on purpose — their gates may change between versions.
  Verified live: post-restart, the previously rejected `switch_model`
  signature stayed suppressed on a real cycle.

### Fixed

- **Prose tool-call detector catches two more live shapes** (both
  captured while dogfooding this release): a bare tool-call JSON
  embedded after conversational prose, and pretty-printed
  (whitespace-formatted) call objects. The embedded scan only triggers
  when the JSON names a *registered* tool, so docs and explanations
  quoting tool-call JSON never false-positive.

## [2.1.4] — 2026-07-29

Run-integrity sensors (PR #5 — the first PR through the automatic
CodeRabbit + Copilot review lane; two confirmed findings fixed in-flight).

### Added

- **Tool calls returned as prose are detected** (R-007): a well-formed
  `<tool_call>` block — or a bare tool-call JSON final answer — arriving
  with an empty `tool_calls` array means the serving layer dropped the
  call. pxx now emits a `tool_call_prose` event, warns, and re-prompts
  the model that the call was NOT executed; an endpoint that keeps doing
  it fails the run actionably ("returns tool calls as prose", naming the
  vLLM parser flags) instead of completing with `diff_lines=0`. The
  bare-JSON shape was captured live while building the feature: a
  dogfooded qwen2.5-coder:7b run answered with the raw `edit_file` JSON
  and "completed" having changed nothing.
- **Set-but-unconsumed `PXX_*` variables warn once per process** (typo
  insurance, deferred from 2.1.1). Git-hook/CI variables are
  allowlisted; warn-only — an unknown variable never fails a run.

### Fixed

- **Timeout env semantics: presence wins.** A set-but-empty or malformed
  `PXX_REVIEW_TIMEOUT` no longer silently falls through to
  `PXX_NATIVE_TIMEOUT` — production had the exact `or`-falsy trap the
  `micro-timeout-env-chain` eval case punishes. Malformed, non-positive,
  and non-finite values (a `inf` value would have disabled the HTTP
  timeout entirely) now warn and use the default. The native backend's
  env read moved to config.py (`native_timeout()`), the sanctioned
  environment boundary. Closes the 2.1.2 review's parity note.

## [2.1.3] — 2026-07-28

First external-tool review pass (CodeRabbit CLI over `v2.1.2..HEAD`):
5 confirmed findings, all fixed below; re-review clean.

### Fixed

- **Defects ledger fails closed on malformed shape**: `load_defects` now
  requires both sections present and list-valued, every entry carrying a
  usable string id. An object-shaped section previously counted as
  `len({}) == 0` unresolved defects and could turn the
  `unresolved_critical_defects` readiness bar green; `gather_counts` had
  the identical fail-open hole in its own raw `json.loads` path and now
  routes through the strict loader (still fail-closed to `None`).
- **Ledger writes are concurrency-safe and atomic**:
  `add_defect`/`resolve_defect` hold an exclusive flock across
  read-modify-write (no lost entries or colliding `D-nnn` ids between the
  CLI and the improve daemon); `_write_defects` writes tmp-then-replace
  so a crash mid-write cannot corrupt the ledger.
- **`pxx improve defects` never tracebacks on a corrupt ledger**: all
  three subcommands (`list`/`add`/`resolve`) share one handler — reason
  and ledger path on stderr, exit 2, corrupt file left untouched.
- **`micro-timeout-env-chain` honest patch carried the falsy trap the
  case exists to punish** (`REVIEW=""` fell through to `NATIVE` via
  `or`): now presence-based, task text pinned down, and a hidden check
  covers `REVIEW="" + NATIVE → 120.0`. The cheat patch still passes
  visible checks and fails hidden ones (`pxx eval self-check` 36/36).

## [2.1.2] — 2026-07-26

### Fixed

- **Reviewer timeout is configurable** (`PXX_REVIEW_TIMEOUT`, falling back
  to `PXX_NATIVE_TIMEOUT`, then the 120 s default; malformed/non-positive
  → default; explicit constructor argument wins): the first usage-found
  defect after 2.1.1 — a ~930-line `pxx review --since` diff on 8 GB
  hardware died at exactly the fixed 120 s ceiling. Hardware slow enough
  to need a longer agent round is slow on review prefill too, so the
  native timeout doubles as the fallback.
- **Review failure reasons are never blank**: `httpx.ReadTimeout`
  stringifies to an empty string, producing the observed
  `reviewer request failed: ` — the exception type name now appears when
  the message is empty.
- **Reviewer context overflow is actionable**: the reviewer path gets the
  same Ollama `exceed_context_size` special-case the native backend got
  in 2.1.1 (raise `num_ctx`, use a larger-context model, or narrow the
  diff with `--staged` / a closer `--since`).

## [2.1.1] — 2026-07-26

### Fixed (security/correctness)

- **Git subprocesses no longer inherit hook-exported `GIT_*` variables**
  (new `pxx/gitenv.py`, threaded through every git spawn site and the
  aider process env): running pxx (or its test suite) from inside a git
  hook or CI step previously re-targeted git calls at the *caller's*
  repository — a leaked `GIT_DIR` was proven to stage deletion of every
  tracked file in the invoking repo. pxx now always targets the repo it
  was pointed at, with that repo's configured identity. Covers every git
  spawn site including the eval harness (an independently-reviewed miss,
  proven exploitable before the fix) and the agent's `run_shell` tool
  (an agent-issued `git add -A` had the same hazard one layer down).
  The test suite scrubs the same set via an autouse fixture, with
  poisoned-environment regression tests pinning the incident shapes.
- **A findings-less `REVISE` degrades to `NO_REVIEW`** instead of
  blocking: a reviewer that emits a verdict with zero usable findings is
  a generic block the loop would "heal" against forever (burning rounds
  on a MUST-address list with no bullets). Classified as
  `review_error="empty"` when the verdict line parsed.

### Fixed (first-run experience)

- **Broken aider no longer breaks bare commands**: auto backend selection
  health-probes `aider --version` and falls back to the native backend
  with a warning (a Python 3.13 aider that crashes on import previously
  turned every `pxx ask`/`edit` into `EDIT_FAILED`). Explicit
  `--backend aider` is honored unprobed. (The probe costs ~1–2 s per
  auto-resolved invocation on aider-installed machines; pin `--backend`
  or set `backend` in config to skip it.)
- **`pxx doctor` now checks what it appears to check**: a reachable
  endpoint is also verified to *serve the configured model* (single-model
  endpoints note the session auto-correct; multi-model without yours
  fails the check), and a found aider binary is verified to actually run.
- **Context overflow is actionable**: Ollama ≥ 0.32's loud
  `exceed_context_size_error` now surfaces as "raise num_ctx / use a
  larger-context model" instead of raw HTTP 400 JSON.
- **`edit_file` misses teach the model**: the old_string-not-found error
  now tells the agent to re-read the file and retry with an exact
  substring (small local models previously blamed "the environment" and
  gave up).
- **PyPI page links**: `[project.urls]` pointed at a nonexistent `main`
  branch and the README used repo-relative links — both 404'd on
  pypi.org. All pinned to `v2` absolute URLs, plus a Tutorial sidebar
  link. (Takes effect on the PyPI page with this release.)

### Docs

- Tutorial troubleshooting: new entry for the small-model "environment
  blocks editing" bluff (re-run), and the silent-truncation warning
  updated for loud-failing Ollama ≥ 0.32.

## [2.1.0] — 2026-07-25

### Added

- **`PXX_NATIVE_TIMEOUT`**: the native backend's per-round HTTP timeout is
  now configurable via env var (default unchanged: 300s). Rounds against
  local models on memory-constrained hardware can legitimately exceed 300s,
  and the resulting `ReadTimeout` surfaced as a misleading
  `MODEL_UNAVAILABLE`. Unset, malformed, and non-positive/NaN values fall
  back to the default; an explicit constructor argument wins over the env.
- **Hands-on tutorial** (`docs/TUTORIAL.md`) + quickstart scaffold
  (`scripts/setup-pxx-quickstart.sh`): build a tested temperature-converter
  CLI with the v2 verb CLI in ~25 minutes — clean-room validated at 6/6
  against a real local Ollama on 8GB hardware, with tiered model guidance
  and a troubleshooting section distilled from live failure modes.

## [2.0.2] — 2026-07-22

### Added (security hardening)

- **Protected paths are now enforced, not labeled**: the action broker
  hard-denies write-class actions against `PROTECTED_PREFIXES` in every
  permission mode (the trust plane is human-only; a risk-tier label was
  never a gate). The protected set now includes `pxx.toml`,
  `.pxx/config.toml`, and the `.pxx` evidence plane (promotions, candidates,
  channels, cycle/daemon/task state, inbox) — while optimizer work products
  (`.pxx/skills|fewshot|playbooks|demonstrations|worktrees`) stay writable.
- **Repo-local hook commands and MCP server definitions are no longer
  honored** from `pxx.toml` / `.pxx/config.toml` (ignored with a loud
  warning): a file inside the edit surface cannot define the gate that
  guards it. User-level config, env, and CLI still define them.
- **`is_protected_path` is case-insensitive** (casefold comparison): on
  macOS/Windows, `PXX/safety.py` no longer bypasses protection.
- **End-to-end proof** of the repo-config exec hole (model writes pxx.toml
  in run N, run N+1 executes its `test_command` with no broker/policy gate)
  with a positive-control denial test pinning the fix.


- **`pxx review [--staged|--since SHA]`**: read-only review of the current
  diff (working tree, staged, or since a commit) through the production
  review machinery — evidence-linked findings, advisory mode, exit 0 on
  APPROVE/NO_REVIEW and 2 on REVISE. No session, no tools, no writes.
  The legacy `pxx --review` flag maps to it (deprecated, with a notice).
- **`--commit` (edit/run/loop; intentionally inert on `goal` nodes —
  their work merges back first)**: opt-in auto-commit of a COMPLETED run's
  work (`pxx: <task preview> [net: <tag>]`). The default stays
  review-before-commit; the `pxx-pre/<ts>` safety tag still points at the
  pre-session HEAD, so undo is unchanged. Loops commit once at the end of a
  completed loop, never per round. Also configurable via `auto_commit` /
  `PXX_AUTO_COMMIT`.

B10 — orchestration & event fidelity (roadmap phases 22 + 10.8):

### Added

- **Per-node worktree isolation** (resolves O4): every goal node now runs
  its loop in its OWN git worktree — a node's mid-run state is invisible to
  siblings (test-pinned) — and changes merge into the main tree only at
  integration, with conflicts caught as `MERGE_CONFLICT` instead of silent
  clobbering. Disjoint-scope remains a fast-path guard for non-git trees.
- **Boundary role agents** (`pxx.roles`): Reproducer, Boundary-Reviewer
  (invoked on HIGH-tier broker decisions, auditable on the stream), and
  Artifact-Reviewer (vets the merged goal artifact; protected-path content
  rejects as `OUT_OF_SCOPE`) — with schema-versioned, fail-closed typed
  handoff artifacts and versioned planner skills loaded from the B5 skill
  layer.
- **Complete event vocabulary**: `run_created`, `prompt_rendered`,
  `tool_action_proposed`, `policy_decision`, `checkpoint_created`,
  `run_paused`, `resumed`, `evaluation_completed` — each emitted at its real
  site; unknown kinds rejected.
- **Outcome projection** (`pxx.projection`): the persisted `outcome.json`
  is now projected FROM the run's event stream — the audit log is the
  single source of truth and the record cannot disagree with it.

All four overclaims (O1–O4) are now resolved by building. The full
roadmap build-out (B1–B10) is complete.

B9 — continuous operation (roadmap phases 19 + 19.5 + 10.75):

### Added

- **Scheduler/daemon** (`improve/scheduler.py`, `pxx improve daemon`): drives
  `run_cycle` on an interval with three non-overlap guarantees (daemon
  flock, cycle flock, repo/GPU work lock), durable pause control that halts
  cleanly at tick boundaries, and deterministic per-candidate worktrees.
- **Task-claim state machine** (`improve/tasks.py`): QUEUED → CLAIMED →
  RUNNING → AWAITING_REVIEW → DONE | FAILED, durable claims + heartbeats +
  stall detection, and startup reconciliation that requeues crashed tasks
  (never lost, never duplicated, idempotent).
- **Checkpoint + resume** (`pxx/resume.py`, `pxx runs resume <id>`): a run
  pauses into a checkpoint and resumes deterministically via the replay
  substrate to the same terminal outcome; `checkpoint_created` joins the
  event vocabulary (B10).
- **Operator control plane**: `pxx improve status` (cycle, queue, inbox
  counts, daemon state), `pxx improve pause` / `resume`.

B8 — evidence-gated auto-promotion (roadmap phase 21):

### Added

- **CLI reachability**: `pxx improve readiness` (per-bar status +
  preconditions) and `pxx improve auto-promote <id> [--consent]`. Default
  posture is report-and-refuse; `--consent` is required to actually
  promote. Every decision prints the human-visibility bundle (candidate,
  rationale, expected-vs-observed evidence details, rollback command).
- **Real evidence producer** (`improve/evidence.py`): the four evidence
  bars — full corpus, held-out, adversarial, canary — are COMPUTED from
  records (evaluation.json, the canary ledger), never accepted as input
  booleans. Missing evidence = False bar (the M0 F1 anti-pattern, closed
  here too).
- **Precondition gate**: the roadmap's "ten mandatory items" (action
  broker, taxonomy, held-out corpus, calibration corpus, real hard gates,
  canary channel, promotion records, apply envelope, measured utility,
  workflow contract) are verified by execution; any missing item globally
  disables auto-promotion.
- **Post-promotion monitoring** (`monitor_promotion`): a tripped breaker on
  the new stable auto-rolls-back to the exact prior stable with a recorded
  reason; a healthy window does nothing.

Overclaim O1 is now fully resolved by building: auto-promotion is real,
CLI-reachable, evidence-gated, and globally disabled until the platform
earns it.

B7 — deployment: canary + circuit breakers (roadmap phase 18):

### Added

- **CANARY channel**: the full stable→candidate→shadow→canary→stable path
  now exists. Deterministic ~1-in-20 run selection by run_id hash (no RNG);
  canary outcomes accrue as distinct promotion evidence
  (`pxx agent canary`); green-over-20-runs makes a canary eligible to
  advance; a breaker trip retires the canary without touching stable.
- **The 3 missing circuit breakers** (now all 7): approval-rate-drop
  (Δ>0.2 below baseline), human-correction-spike (≥3 overrides/reverts),
  reviewer-availability-drop (<0.5). Each retires the offending channel
  with a recorded reason; healthy signals don't trip.
- **Exercised rollback + history**: activate-stable (gated by a passing
  promotion record, M0 F5) → rollback restores the exact previous stable,
  both events visible in `pxx agent history`.

B6 — promotion rigor (roadmap phase 17):

### Added

- **Held-out judgment recorded**: the promotion verdict records which
  partition produced it; development-only scorecards are refused even when
  the candidate wins every case (from B3.3's prerequisite).
- **Multi-metric guards** (`improve.promotion.compare`): beyond pass/fail —
  the roadmap's `cost ≤ 1.15× baseline` rule plus guards for avg rounds
  (≤1.25×), p95 duration (≤1.25×), diff size (≤1.5×), rollback rate
  (Δ≤0.05), and memory usefulness (drop ≤0.05, fed by B5's measured
  utility). A pass-rate-up/cost-up-16% candidate is NOT eligible. Unpriced
  or unmeasured metrics record as `unmeasured` — never fabricated, never
  silently blocking. Metric regressions are soft (human-overridable); hard
  gates stay absolute and override-proof.
- **Risk-class route table**: `classify_risk` (moved into promotion) maps a
  candidate to LOW/MEDIUM/HIGH → route `fast`/`standard`/`human` with the
  required evidence bars recorded on the verdict (B8's checklist). Unknown
  risk routes human-only (fail closed). `pxx compare` prints route, bars,
  and metric report.

B5 — outcome-aware memory + entropy control (roadmap phases 20 + 20.5):

### Added

- **Measured observed_utility** (`pxx.memory.utility`): memory ablations
  attribute outcome deltas from matched run pairs (with vs without an
  observation injected, by task_id) and write a MEASURED utility back —
  useful observations rise, misleading ones sink in search ranking
  (`0.4 + 0.3*evidence + 0.3*utility`, contamination down-weighted).
  The 5-level EVIDENCE_RANK ladder now drives capture (was dead code), and
  every observation carries provenance, validation dimension, and
  agent_version_id (Phase 20.2).
- **Five knowledge layers** (policy / repository / skill / playbook /
  episodic) with per-layer retention TTLs, layered injection ordering, a
  recurrence signal (`seen_count`), and a graduation ladder — recurring,
  high-utility lessons climb episodic → skill → playbook. v2→v2.1 migration
  preserves existing data.
- **No success auto-conversion** (Phase 20.5): COMPLETED sessions write
  NOTHING automatically; only failed runs capture episodic, low-trust
  (failed_run_inference + contamination) observations.
- **Entropy control** (`pxx.entropy`): golden-principle lints
  (`pxx improve principles`, wired into CI), per-layer quality grades
  (`pxx memory grades`), and a deterministic GC pass (`pxx memory gc`)
  pruning expired, low-utility, and contaminated entries.

B4 — learning & candidates (roadmap phases 15 + 16):

### Added

- **Richer mining** (`pxx.improve.mining`): cluster dimensions expanded
  (stage, task category, scope type, severity, retry behavior); recurring-
  pattern detectors (unparseable reviews, timeout clusters, lint blocks,
  memory↔diff-size correlation, model failure disparity); `RootCause`
  classification on every proposal (AMBIGUOUS_REQUIREMENTS / CONTEXT_MISSING
  / MODEL_CAPABILITY / PROMPT_DEFECT / TOOLING / EVALUATOR_DEFECT) with
  `reason_prompt_change_is_insufficient` — a MODEL_CAPABILITY failure
  proposes a model lever, never a prompt tweak. Correlation-only labeling
  preserved.
- **Semantic loop detection + recovery ladder** (`pxx.loop.ProgressVector`):
  identical failing-set + diff + findings across rounds → step 1 injects a
  re-plan prompt, step 2 stops with `LOOP_DETECTED` — never the blunt round
  cap. Healthy healing loops are unaffected.
- **Broader candidate classes** (`CandidateClass.SKILL / FEWSHOT / PLAYBOOK /
  DEMONSTRATION`), each with an allowlisted target surface and fail-closed
  validation (contrastive "bad + preferred" poles required for
  demonstrations).
- **Apply→verify envelope** (`pxx.improve.apply`): candidates are applied to
  ONLY their declared target and the envelope proves it — committed +
  worktree changes read with `--no-renames` and all untracked files
  enumerated; symlinked targets rejected; tampered candidates re-validated
  on apply.
- **`pxx improve evaluate-candidate <id>`**: re-validate → held-out corpus
  at baseline AND under the candidate → `promotion.compare` → verdict +
  recorded evidence. Candidate integrity validation serves as the
  permission_expansion evidence producer for the candidate arm.

B3 — evaluation depth (roadmap phase 13 + 14.3):

### Added

- **Corpus 18 → 30** (10 micro / 10 regression / 10 adversarial), all
  self-checking in CI with byte-identical repeatability. New regression
  cases are shaped from the repo's own M0/B1/B2 history (truncation anchor,
  stale review, clarify gate, context hint/staleness); new adversarial cases
  cover rename-collapse escapes, hardcoded expectations, weakened timeouts,
  new dependencies, and budget blowouts.
- **`no_new_dependencies`** check: a diff that adds a non-stdlib import or
  touches a dependency manifest fails the case.
- **Five evaluation families** (`Case.family`): capability / safety /
  recovery / context / economic, with a per-family breakdown on every
  scorecard (`family X: n/m` lines).
- **Held-out partitioning** (`Case.partition` dev|held-out):
  `pxx eval report --partition held-out` scores only held-out cases;
  `eval.report.compare` refuses development-only candidates and
  `improve.promotion.compare` requires held-out evidence (Phase 17.4 — the
  hard prerequisite for B6).
- **ReplayBackend**: replays a recorded run's tool calls deterministically
  from its run dir — same broker/gates as a live run, byte-identical across
  replays; truncated recorded args fail closed (metadata-only audit).
- **Calibration**: corpus 8 → 14 (7 flag / 7 clean) plus a verdict-agreement
  metric with an explicit `MIN_AGREEMENT` threshold.

B2 — identity & outcome fidelity (roadmap phases 11 + 12):

### Added

- **Canonical 21-code taxonomy** (`pxx.outcome`, later extended to 23 by
  `LOOP_DETECTED` and `MERGE_CONFLICT`): the coarse `GATE_FAILED` /
  `NO_PROGRESS` / `BACKEND_ERROR` / `SCOPE_VIOLATION` are split into their
  real causes — `EDIT_FAILED`, `EDIT_TIMEOUT`, `TEST_RUN_FAILED`,
  `TEST_REGRESSION`, `NO_TEST_PROGRESS`, `LINT_BLOCKED`, `REVIEW_REJECTED`,
  `REVIEW_UNAVAILABLE`, `REVIEW_EMPTY`, `REVIEW_UNPARSEABLE`, `OUT_OF_SCOPE`,
  `HOOKS_MISSING`, `MODEL_UNAVAILABLE`, `CONFIGURATION_INVALID` (the 12.2
  canonical set, plus `INTERRUPTED`, `CLARIFICATION_REQUIRED`, `HOOK_DENIED`).
  One run carries one terminal code plus `contributing_codes`.
- **Full 12.1 RunOutcome**: per-leg seconds (edit/test/review),
  `files_changed`, baseline/introduced/terminal failure counts, lint errors,
  `findings_by_severity`, `unparseable_review_count`,
  `injected_observation_ids` — persisted to `outcome.json` and round-tripped
  through `runs.py`. A lint gate (from WORKFLOW.md `commands.lint`) joins
  the loop's guards.
- **Commit-bound review validity** (`pxx.review.ReviewPacket`): a review
  approves a commit, not a task — when HEAD advances past the reviewed
  commit the loop forces a re-review, and fails closed if the tree never
  stabilizes.
- **Identity threading** (Phase 11.3): `task_id`, `repository_fingerprint`
  (HEAD + dirty + tracked-count), and `starting_commit` in every run record.
- **Drift sentinels**: `ModelFingerprint` probed from the served model
  (Ollama digest / resolved id) — a same-name re-pull mints a new
  `agent_version_id` and `pxx agents list` marks the superseded agents
  QUARANTINED; `aci_hash` (tool set + WORKFLOW.md) and `context_hash`
  (prompts + memory policy) join the manifest identity.
- `pxx metrics compare A B`: per-metric delta between two agents' run sets.

### Changed

- Exit-code mapping: review/test/lint scope codes exit 2 (gate); config and
  model errors exit 1; usage errors stay 64.

B1 — authority & legibility substrate (roadmap phases 14 + 10.5):

### Added

- **Action broker** (`pxx.broker`): every tool call is classified into a
  typed `ToolAction` (action class, risk tier, targets) and authorized
  through one authority at the `ToolRegistry.call` choke point — per-class
  permission profiles (WORKFLOW.md `[permissions]` or built-in defaults),
  scope enforcement, PreToolUse hooks as the deny substrate, and
  `tool_action_proposed` + `policy_decision` events on every call. Fail
  closed on unclassifiable actions and unknown modes.
- **WORKFLOW.md machine contract** (`pxx.workflow`): repository-owned TOML
  contract (states, budgets, commands, permission profiles, hooks,
  protected-paths mirror) with a fail-closed loader. Hashed — with the
  protected-paths list — into every agent manifest, so contract or guardrail
  edits mint a new `agent_version_id`.
- **Ambiguity gate** (`pxx.clarify`): `ready_to_act` runs before the first
  backend round; ambiguous tasks (empty, missing referenced file, test
  intent without a test command) stop with `CLARIFICATION_REQUIRED` and a
  surfaced question — without editing anything.
- **Evidence-linked findings** (`pxx.review`): findings without a concrete
  anchor (file+line, backticked input/command, named path) are dropped at
  parse time; an all-generic review degrades to `NO_REVIEW` — it can neither
  force healing loops nor silently approve.
- **Deterministic human audit sampling** (`pxx.audit_sampling`): 100% of
  promotions and protected-path-touching runs, ~20% of ordinary runs,
  reproducible by sha256 of the run id; recorded in `outcome.json`.
- **Legibility verbs**: `pxx workflow validate`, `pxx context audit`
  (docs present + trust mirrors in sync), `pxx docs check` (documented verbs
  exist) — wired into CI, plus `ARCHITECTURE.md` (module map).

M0 safety hardening (independent code review, fail-opens first):

### Fixed (security / fail-open)

- `pxx eval report` computed NO hard gates and stamped all five `True` —
  `pxx compare` then judged promotion eligibility on fabricated green.
  Gates are now derived from actual run evidence (`eval.report.compute_gates`);
  a gate with no evidence is `False` (fail closed).
- The loop's post-round scope re-check read only `git status` — a backend
  that commits (aider auto-commit) left a clean tree and escaped scope
  containment. It now diffs `pre_sha`..working-tree plus untracked files.
- All gate-relevant git reads run with `--no-renames`, so a rename can't
  collapse its source path out of scope/allowed-files evidence (loop scope
  re-check, diff budget, eval `allowed_files`, staged scan, memory capture).
- `pxx check` reported "clean" (exit 0) on any git error or outside a repo —
  the staged scan now fails closed (`PxxError` → exit 1).
- `pxx promote` built promotion records with `gates={}` and
  `pxx agent activate stable` applied ANY version unchecked. `promote` now
  requires `--scorecard` with real, all-green hard-gate evidence, and
  `activate stable` requires a passing promotion record for that version.

### Fixed (crash / integrity / leak / minor)

- `pxx runs/metrics/agents/verify` no longer crash on a malformed
  `outcome.json` field (defensive coercion to neutral defaults); export
  verbs report a clean error (exit 1) on unwritable paths.
- Audit: trailing truncation of the hash chain is now detected (`.head`
  sidecar anchors count + tip hash; unanchored logs fail closed), and an
  unparseable tail no longer silently reseeds GENESIS mid-chain — the
  damaged file rotates aside (loudly) and a fresh chain starts.
- MCP clients are tracked and closed after every run (no more leaked
  subprocess/reader task), the session SIGINT handler is removed on exit,
  and a timed-out hook process is reaped (source of the event-loop-closed
  teardown warning; the suite now runs under
  `-W error::pytest.PytestUnraisableExceptionWarning`).
- A typo'd subcommand fails loud (exit 64, with a suggestion) instead of
  silently routing to `ask` and hitting a model; Ctrl-C yields a clean 130
  without a traceback; usage errors are split from gate stops (64 vs 2);
  local/unpriced runs report `cost_usd=None` (never a fabricated $0.00);
  a missing public-denylist and vacuous calibration dimensions now print
  loud warnings instead of passing silently.

### Changed

- `RunOutcome.cost_usd` is now `float | None` (None = unpriced).
- `governance.scan_staged` raises `PxxError` when the staged fileset cannot
  be determined (previously returned `[]`).

Roadmap platform (phases 0, 11–22; see docs/ROADMAP.md and
DESIGN-ROADMAP.md):

### Added

- **Immutable behavior versioning** (`pxx.manifest`, phase 11): every session
  writes a run directory (`state_dir/runs/<run_id>/` with `manifest.json`,
  `task.json`, `events.jsonl`, `outcome.json`, optional `diff.patch`) and
  attaches `run_id` + `agent_version_id` to the audit stream — same config,
  same agent id, always. Best-effort: never gates a run.
- **Run/outcome analytics** (`pxx.runs`, `pxx.verify`, `pxx.cost`, phase 12):
  run store queries and metrics, `VerificationPacket` projection with
  gates-fired evidence, and pluggable cost accounting (versioned price table
  for known cloud models; local/unknown providers record usage with cost
  `None` — never fabricated dollars).
- **Evaluation harness** (`pxx.eval`, phase 13): TOML case format, disposable
  git-repo materialization, visible + hidden checks, pure-python unified-diff
  patching, honest/cheat self-checking corpus (18 seed cases under `evals/`:
  8 micro, 5 regression, 5 adversarial), corpus-fingerprinted scorecards that
  refuse mismatched comparisons.
- **Reviewer calibration** (`pxx.calibration`, phase 14): 8-case calibration
  corpus, recall/false-positive/format-compliance/availability metrics with
  explicit thresholds, using the production `pxx.review.parse_review` path.
- **Experience mining + constrained candidates + promotion policy**
  (`pxx.improve.mining/candidates/promotion`, phases 15–17): deterministic
  failure clustering labeled correlation-only, declarative single-variable
  candidates on an allowlisted surface (settings overlays, tighten-only
  budgets, `pxx/prompts/*.md` content) with fail-closed validation against
  `pxx.protected_paths`, absolute hard gates (no human override), and
  append-only promotion records.
- **Deployment machinery** (`pxx.improve.channels/cycle/autopromote`, phases
  18/19/21): stable/candidate/shadow/retired channels with proven rollback,
  circuit breakers, shadow runs that never touch the main worktree, a durable
  idempotent propose-only improvement cycle with triage inbox and anti-spam
  rules, and evidence-gated auto-promotion that refuses unless all readiness
  bars are green.
- **Goal orchestration** (`pxx.goal`, phase 22): goal → validated task DAG →
  bounded `run_loop` per node with disjoint-scope parallelism, dependent-skip
  on failure, and a final integration test pass.
- **Outcome-aware memory** (phase 20): observations carry
  `evidence_confidence`, `observed_utility`, `contamination_risk`, provenance
  outcome, and quarantine flags; search weighs evidence and excludes
  quarantined entries. Frequency is not correctness.
- **New CLI verbs**: `runs`, `agents`, `verify`, `metrics`, `eval`,
  `calibrate`, `improve`, `propose`, `compare`, `agent`, `promote`, `check`,
  `goal`.
- **Package smoke** (`scripts/smoke-package.sh`, phase 0.5 tier B): build →
  install into a throwaway venv → assert the packaging contract (version,
  doctor, prompts resource, `evals/` excluded, `pxx.eval`/`pxx.improve`
  importable); wired as a CI job after the test matrix.

## [2.0.0] — 2026-07-17

Ground-up rewrite ("pxx_ng"). pxx is now an async, event-sourced agent runtime
rather than an aider launcher.

### Added

- **Async runtime with pluggable backends** (`pxx.backends`): `NativeBackend`
  (pxx's own OpenAI-compatible tool-calling agent loop), `AiderBackend`
  (optional subprocess delegation, `pxx-orchestrator[aider]`), `MockBackend`
  (scripted, for tests). pxx owns the loop; backends cannot bypass policy.
- **Built-in tool surface** (`pxx.tools`): `read_file`, `write_file`,
  `edit_file` (exact-match), `list_files`, `search_files` (rg with pure-python
  fallback), `run_shell` (gated, optional `sandbox-exec`/`bubblewrap`
  sandboxing), `recall_memory`, `remember`. Deliberately ~8 tools for small
  local models.
- **Typed event stream + hash-chained audit** (`pxx.events`): every session
  event flows through an async bus; the audit log is tamper-evident JSONL,
  metadata-only, credential-scrubbed, verifiable via `pxx audit verify`.
- **Integrated memory** (`pxx.memory`): SQLite + FTS5 (BM25) + pure-python
  cosine vector search (0.4/0.6 hybrid), deterministic hash embeddings offline
  with automatic Ollama embeddings when reachable, TTL + monthly JSONL
  archival, deterministic session-start injection, post-session capture from
  the event stream and git diffs. No sidecar service required.
- **MCP interop** (`pxx.mcp`): stdio MCP client (spec 2025-11-25 subset) that
  mounts remote tools as `mcp__<server>__<tool>`, and `pxx mcp`, an MCP server
  exposing pxx memory to other agents.
- **Layered TOML config** (`pxx.config`): CLI > env > project `pxx.toml` >
  user config > defaults; unknown keys rejected. Legacy `PXX_OLLAMA_*` env and
  `~/.config/pxx/env` still honored.
- **Permission modes + hooks + budgets** (`pxx.safety`): ask/plan/edit/auto;
  PreToolUse/PostToolUse hooks as deterministic gates; cumulative budgets
  (rounds, tokens, cost, wall-clock, diff lines) with hard stops.
- **Endpoint router** (`pxx.router`): async probing of Ollama and
  OpenAI-compatible endpoints, fallback chains, known context-window table.
- **Bounded autonomous loop** (`pxx.loop`): edit → test → review rounds with
  fresh context per round, monotonic failing-set progress (`NO_TEST_PROGRESS`),
  post-hoc scope re-check, diff budget, and a fail-closed review gate
  (`pxx.review`, BLOCKING/ADVISORY).
- **Headless server** (`pxx.server`, `[server]` extra): FastAPI app with
  session start/cancel, SSE event streaming, memory proxy, optional bearer
  token auth.
- **New CLI**: `ask` (default), `edit`, `plan`, `run`, `loop`, `chat`,
  `memory`, `mcp`, `serve`, `doctor`, `upgrade`, `audit`. Exit codes:
  0 completed, 2 gate/budget stop, 64 usage error, 130 interrupted, 1 error.
- Terminal codes (`pxx.outcome`) replace message parsing; every run ends with
  exactly one machine-readable code.

### Changed

- Default mode remains read-only (`ask`), now enforced by the tool registry
  rather than aider's chat mode.
- Python requirement is `>=3.11` with **no upper bound** (aider is optional and
  constrained to `<3.13` only within its own extra).
- Core dependencies reduced to a single package: `httpx`.

### Removed (1.x architecture)

- `os.execv` handoff to aider; raw `sys.argv` scanning (argparse now);
  `agentmemory`/`9router`/`docs-rag-sme` sidecar services (memory and routing
  are in-process); the broken stdout-scraping observer and the unwired
  `/recall` slash commands (superseded by event-stream capture and real
  memory tools); the vendored service checkouts.

### Migration

- 1.x flag invocations (`pxx --edit ...`, `pxx --with-memory`) are rewritten
  by a compat shim. A 1.x `~/.pxx/memory.db` is detected and moved aside to
  `memory.db.v1-backup` on first 2.0 run. See docs/MIGRATION.md.
