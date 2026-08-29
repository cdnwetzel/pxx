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
before running anything: recall ≥ 0.857 AND fp ≤ 0.143 MATCHES OR EXCEEDS the best
sovereign result** (equality at this precision is a tie, not a win).
Below 0.75 / above 0.25 fails calibration outright.

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

A capture counts as a completed review only when it carries a completion marker —
for CodeRabbit, `Actionable comments posted: N` or `No actionable comments`. A capture
without one is a non-review (rate-limit notice, in-progress placeholder, or the "Review
Change Stack" banner) and is dropped at harvest so it reaches the scorer as MISSING, not as
a clean bill. A completed review reporting zero findings = APPROVE.

For reviewers scored by inline count, a **formal review state** outranks the count:
`CHANGES_REQUESTED` flags regardless of inline comments, `APPROVED` with no inline comments
approves, and `COMMENTED` falls through to the count. Without that rule a review carrying
its findings in the body and none inline would translate to APPROVE.

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

## Partial runs: coverage, not imputation

`run_calibration` treats an absent response as *flagged*. That is right for a live gate —
an unavailable reviewer must block — and **wrong for a benchmark**, where an uncaptured
case is missing data, not a judgement the reviewer made.

Observed for real on the first run: CodeRabbit was rate-limited on five `clean` cases and
scored `fp_rate 1.000`, a figure produced entirely by absent captures. So `score` runs only
over captured cases and prints coverage. A partial run is visibly partial.

**Compare reviewers only at equal coverage.** A score at 57% coverage is not comparable to
one at 93%, and neither is comparable to the sovereign baseline, which ran the full corpus.

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
3. **The scaffolded files are TRUNCATED.** A case is a diff, and the corpus holds no
   full-file source (`id`, `kind`, `diff`, `task`, `expect`, `min_severity` — nothing else).
   Reconstruction can therefore only materialise the hunk region: the PR's file contains the
   changed lines and their context and *nothing else* — no imports, no surrounding
   definitions. Inventing that context would be fabricating evidence, so the bench does not.
   The consequence is real and cuts one way: a codebase-aware reviewer may flag a truncated
   file for problems the case never intended (an unresolved name, a missing import), which
   **inflates the false-positive rate** for exactly the reviewers this corpus already
   disadvantages. Treat any fp figure here as an upper bound. Raised by Greptile reviewing
   this harness (PR #81).

4. **n = 14, but the metrics have SEPARATE class denominators of 7 each.** Recall runs over
   the seven `expect=flag` cases and the false-positive rate over the seven `expect=clean`
   cases — a clean case cannot move recall and a flagged case cannot move fp. So one case
   is worth ~0.143 on whichever metric it belongs to, and at partial coverage the fp step
   is larger still (CodeRabbit returned 4 clean captures, so one case moves its fp by
   0.250). Treat any gap smaller than those steps as not distinguished by this bench.
5. **One shot per case.** No variance estimate. A model reviewer is temperature-pinned;
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
three hours, or open them and let the queue drain.

Greptile billing inputs, since the estimate depends on them: **Starter plan, 50 credits per
month, standard review mode**. A standard review costs 1 credit, so 13 PRs cost 13 credits.
TREX reviews cost 3 credits each — the same run in TREX mode would cost 39, most of a month.

## Captures are evidence

`captures/*.json` stores each reviewer's response **verbatim** — inline comments, issue
comments, and PR review bodies. The derived verdict is **not** persisted; it is recomputed
from the raw text on every `score` run. That is deliberate: the translation rule stays the
single source of truth, so changing it cannot leave stale verdicts behind. A disputed score
is re-derivable from the captures without re-running the reviewers.
