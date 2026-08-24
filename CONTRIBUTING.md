# Contributing to pxx

pxx is a fail-closed agent orchestrator. Its value is that its gates cannot be
bypassed, so the process that ships it is held to the same standard as the code.

## The merge gate

**CodeRabbit must PASS before any PR is merged.** Not advisory, and not waived for
docs-only or pre-approved changes. CI (tests on 3.11/3.12/3.13, `ruff check`,
`ruff format --check`, `pxx check`) must be green as well.

### The one manual step, and why it exists

`auto_incremental_review` is **off** in `.coderabbit.yaml`. CodeRabbit reviews a PR
when it opens and **not** on subsequent pushes.

That is deliberate — review limits are enforced **per developer per hour** (Pro: 5
PR reviews/hour), not per repository, and a review is spent per *push*. A typical
fix loop here is 3–4 pushes, so a single PR could consume the entire hourly budget
and stall the next one for 25 minutes.

It also creates a hazard that has to be closed by hand:

> After the opening review, the status check stays green while you push fixes.
> A PR merged in that state passed a gate that never read its final code.

So, non-negotiably:

> **Before merging, comment `@coderabbitai review` on the PR and wait for the
> review of the final state.**

A green check from the opening commit is not evidence about what you are merging.
If the diff changed after the last review, the gate is stale and the merge is
ungated regardless of what the badge says.

**Exception:** if nothing has been pushed since CodeRabbit's most recent review,
the existing review already covers the final state and no re-trigger is needed.

### When CodeRabbit is unavailable

Rate limits and outages happen, and they happen most during release waves — that
is, exactly when throughput matters. Waiting is always acceptable. If you choose
not to wait, the substitution must be **recorded, not silent**: state in the PR
that the gate was substituted, by what, why, and who approved it. An unwritten
exception leaves no evidence; a written one does.

Do **not** enable CodeRabbit's usage-based reviews ($0.25/file) to work around a
limit. The subscription is already paid; the limit is an hourly rate, not a
quota you have exhausted. Spending per-file on top of it converts a predictable
bill into an unbounded one, and agentic workflows produce exactly the large,
frequent diffs that make that expensive. Reducing consumption per PR — which is
what this configuration does — recovers the capacity without spending more.

## Greptile (advisory)

Greptile runs as a **second, independent reviewer** and is configured in
`greptile.json` with `statusCheck: false` — it does not gate merges. It reviews
once per PR on open, for the same quota reason (Starter tier: 50 credits/month).

Its value is disagreement. A finding one reviewer raises and the other misses is
worth more than either reviewer's aggregate score, because a single reviewer's
hit-rate has no denominator: you cannot see what it missed.

Verify every finding from either reviewer against the code before acting. A
confident wrong answer is still wrong, and both of these tools produce them.

## Standards that apply to every change

- **Fail closed.** Unknown, missing, malformed, or unreadable means deny. A branch
  where an error could produce an allow is a defect.
- **Every gate ships with a negative control.** A check that cannot fail is not a
  passing check; it is a defect. Prove it fires on the bad case — ideally by
  breaking the thing under test and watching the test fail.
- **Evidence beats assertion.** "Tests pass" is not evidence when the output could
  be pasted. Claims in docs, CHANGELOG, and PR descriptions must match what the
  code and tests actually do.
- **Protected paths** (`PROTECTED_PREFIXES` in `pxx/protected_paths.py`) are the
  trusted control plane. Changes there need human review and cannot be made
  autonomously.
- **No secrets or private identifiers in committed content** — no absolute home
  paths, LAN IPs, internal hostnames, or organisation-internal project names.
  `pxx check` enforces this and has caught real leaks.

## Before opening a PR

```bash
uv run python -m pytest          # note: `python -m`, so cwd is on sys.path
uv run ruff check pxx tests
uv run ruff format --check pxx tests
uv run pxx check                 # governance scan
```

## Releases

Every shipped change bumps the version in `pyproject.toml`, `pxx/__init__.py`, and
`uv.lock`, adds a `CHANGELOG.md` entry, and updates `docs/ROADMAP.md` where the
roadmap made a claim about it. Fetch and rebase onto `origin/v2` first — a local
branch can trail the remote, and the bump must come from the true latest.

Changes that do not ship in the wheel (`docs/`, `prototypes/`, examples) do not
get a version bump: merged is delivered.

Behavioural claims land with a receipt in `docs/RECEIPTS.md` — dated, graded
(Attested or Reproducible), with an explicit "Boundary — explicitly not claimed"
section. A claim without a receipt is not a claim pxx makes.
