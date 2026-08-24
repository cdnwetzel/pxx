# Review bench — holding external reviewers to the sovereign bar

Non-shipped (`prototypes/`, excluded from the wheel). Merged is delivered; no version bump.

## The question

pxx already grades a reviewer. `evals/calibration/` is 14 labelled cases (7 `expect=flag`,
7 `expect=clean`) and `pxx/calibration.py` publishes the thresholds:

```text
MIN_RECALL 0.75 · MAX_FP_RATE 0.25 · MIN_FORMAT_COMPLIANCE 0.9
MIN_AVAILABILITY 0.75 · MIN_AGREEMENT 0.75
```

The sovereign reviewer has been measured against exactly this:

| Reviewer | Recall | FP | Outcome |
| --- | --- | --- | --- |
| `qwen2.5:14b-instruct-q4_k_m` | 0.857 | 0.286 | breaches `MAX_FP_RATE` → advisory only |
| `qwen2.5-coder:32b` | 0.857 | 0.143 | passes → blocking-grade (needs >16 GB) |

So an external reviewer earns a place here only by clearing the same bar. **Pre-registered
before running anything: recall ≥ 0.857 AND fp ≤ 0.143 beats the best sovereign result.
Below 0.75 / above 0.25 fails calibration outright.**

## Why a replay adapter instead of a live one

CodeRabbit, Greptile, and Copilot review *pull requests*. They cannot be handed a diff and
asked for a verdict, so they cannot implement the `Reviewer` protocol live. The bench
therefore captures what each said about each case and replays it through
`RecordedReviewer` — which feeds the **same** `run_calibration` → `parse_review` →
threshold path used for a local model.

That is deliberate: there is no second scoring implementation that could drift from the
runtime, or be tuned until a tool passes.

## The mapping rule — mechanical, and fixed before any data was collected

These tools emit prose, not `VERDICT:` lines. The translation is mechanical so that the
person running the bench (who is often the author of the code under review) exercises no
discretion over the outcome:

| Reviewer | Flagged when | Severity from |
| --- | --- | --- |
| CodeRabbit | its summary says `Actionable comments posted: N` with **N ≥ 1** | `🔴 Critical`/`🟠 Major` → high, `🟡 Minor` → medium, `🔵 Trivial` → low; take the max |
| Greptile | ≥ 1 inline review comment on the PR | its own severity label when present |
| Copilot | ≥ 1 inline review comment on the PR | none emitted → see dual scoring |

Absence of any comment = APPROVE.

On nitpicks: CodeRabbit's *actionable* count already excludes them, so for CodeRabbit the
rule inherits that exclusion. Reviewers scored by inline-comment count have no nitpick
distinction to inherit, and the harness applies **no filtering of its own** — stated plainly
because an earlier draft of this file claimed filtering the translator does not do.

### Dual scoring, because severity vocabulary is not the thing being measured

Five flag cases carry a `min_severity` (2 `high`, 3 `medium`); the two `edge-*` flag cases carry none. A tool with no severity
vocabulary would fail those on format rather than on substance, which measures the wrong
thing. So every run reports **both**:

- **strict** — severity exactly as the reviewer marked it; unmarked findings count as `low`
- **lenient** — a flagged case is credited at the case's `min_severity`

Neither is "the" number. Report both. A tool that only passes leniently has caught the
defect but cannot express urgency, which is worth knowing rather than hiding behind one
figure.

## What this bench does NOT measure

Stated up front, because a benchmark that oversells itself is worse than none:

1. **`format_compliance` and `availability` are artifacts of the harness, not properties of
   the tool.** The translation supplies the verdict line, so those two dimensions measure
   the translator. **Only `recall`, `fp_rate`, and `agreement` are meaningful here.**
2. **The corpus understates codebase-aware reviewers.** These are small, synthetic,
   context-free diffs. CodeRabbit's and Greptile's stated advantage is whole-repo context,
   and this bench denies them it. It is a fair comparison of the *shared* capability — can
   you spot a defect in a diff — and an unfair one for their differentiator. That is why
   the bench is paired with the running unique-find ledger on real PRs, which has the
   opposite bias (high external validity, no ground truth).
3. **n = 14.** Each case is worth ~0.14 recall. Two cases' difference is inside the noise.
   Treat a gap under ~0.15 as "not distinguished by this bench".
4. **One shot per case.** No variance estimate. A model reviewer is temperature-pinned;
   these tools are not, and may not be deterministic.

## Running it

```bash
# 1. materialise the corpus as a git tree: base commit + one branch per case
python3 prototypes/review_bench/bench.py scaffold --out /tmp/pxx-review-bench

# 2. push, open one PR per case, let the reviewers run  (see "PR hygiene" below)

# 3. harvest what they said
python3 prototypes/review_bench/bench.py harvest --repo <owner>/<bench-repo> \
    --out prototypes/review_bench/captures/coderabbit.json --reviewer coderabbit

# 4. score through the production path
python3 prototypes/review_bench/bench.py score \
    --captures prototypes/review_bench/captures/coderabbit.json
```

`scaffold` reconstructs each case's before/after file state from the unified diff. It then
compares how git renders that change against the corpus diff. A case whose rendering
differs is still emitted — the before/after state is correct, git simply minimises where a
hand-written diff did not — but it is flagged in the output and recorded per case as
`rendering_differs` in `bench-manifest.json`, because that case's PR is **not**
byte-identical to what the sovereign reviewer saw. A case with no reconstructable hunk
(`edge-empty-diff`, which cannot be a PR at all) is reported as skipped.

## PR hygiene

Open these against a **separate bench repository**, not `cdnwetzel/pxx`. Fourteen synthetic
PRs would pollute the history of a public repo that is part of the project's
evaluation-readiness story, and an external evaluator reading that history should see real
work.

Pace the PRs: CodeRabbit limits are per developer per hour (Pro = 5), so 14 PRs is roughly
three hours, or open them and let the queue drain. Greptile spends 14 of its 50 monthly
credits.

## Captures are evidence

`captures/*.json` stores each reviewer's response **verbatim** — inline comments, issue
comments, and PR review bodies. The derived verdict is **not** persisted; it is recomputed
from the raw text on every `score` run. That is deliberate: the translation rule stays the
single source of truth, so changing it cannot leave stale verdicts behind. A disputed score
is re-derivable from the captures without re-running the reviewers.
