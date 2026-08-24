#!/usr/bin/env python3
"""Review bench — score an external PR reviewer against evals/calibration/.

Three verbs:

  scaffold  reconstruct the corpus as a git tree (base commit + one branch per
            case) so each case can be opened as a pull request whose diff is
            byte-identical to the one the sovereign reviewer was given.
  harvest   read a bench repo's PRs and record what a reviewer said, verbatim.
  score     replay the captures through pxx's PRODUCTION calibration path.

The mapping from prose comments to a VERDICT line is mechanical and fixed before
any data is collected — see README.md. The person running this is usually the
author of the code under review, so they must have no discretion over the score.

Non-shipped: prototypes/ is excluded from the wheel.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

CORPUS = REPO_ROOT / "evals" / "calibration"

# --- diff reconstruction --------------------------------------------------------------


@dataclass(frozen=True)
class Reconstructed:
    path: str
    before: str
    after: str


def reconstruct(diff: str) -> list[Reconstructed]:
    """Rebuild the before/after text of each file's hunk region from a unified diff.

    Handles MULTI-FILE diffs (one entry per `+++ b/` header). Only hunk regions
    are reconstructed, which is all a review needs. Returns an empty list for a
    diff with no hunks (the empty-diff case), which the caller reports rather
    than silently skipping.
    """
    files: list[Reconstructed] = []
    path: str | None = None
    before: list[str] = []
    after: list[str] = []

    def flush() -> None:
        nonlocal path, before, after
        if path and (before or after):
            files.append(
                Reconstructed(
                    path=path,
                    before="\n".join(before) + "\n" if before else "",
                    after="\n".join(after) + "\n" if after else "",
                )
            )
        path, before, after = None, [], []

    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("--- "):
            flush()
            in_hunk = False
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            after.append(line[1:])
        elif line.startswith("-"):
            before.append(line[1:])
        elif line.startswith(" "):
            before.append(line[1:])
            after.append(line[1:])
        # any other prefix (e.g. "\\ No newline at end of file") is not content
    flush()
    return files


def cmd_scaffold(args) -> int:
    from pxx.calibration import load_cases

    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"refusing to scaffold into non-empty {out}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    def git(*a, **kw):
        return subprocess.run(["git", "-C", str(out), *a], check=True, capture_output=True, text=True, **kw)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "bench@localhost")
    git("config", "user.name", "review-bench")

    cases = load_cases(CORPUS)
    built, skipped = [], []

    # Base commit: every case's BEFORE state, namespaced under cases/<id>/ so two
    # cases touching the same filename (critical-removed-validation and
    # critical-sql-injection both edit app/users.py) cannot overwrite each other's
    # pre-image. Only the path prefix differs from the corpus; the +/- content
    # lines the reviewer sees are identical.
    for case in cases:
        files = reconstruct(case.diff)
        if not files:
            skipped.append((case.id, "no reconstructable hunk"))
            continue
        for r in files:
            target = out / "cases" / case.id / r.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(r.before, encoding="utf-8")
    (out / "README.md").write_text(
        "# pxx review bench\n\nSynthetic corpus from `evals/calibration/`. Each PR carries one\n"
        "labelled case. Not a real project.\n",
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-q", "-m", "base: pre-image of every calibration case")

    for case in cases:
        files = reconstruct(case.diff)
        if not files:
            continue
        branch = f"case/{case.id}"
        git("checkout", "-q", "main")
        git("checkout", "-q", "-b", branch)
        for r in files:
            (out / "cases" / case.id / r.path).write_text(r.after, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", f"{case.id}: {case.task}")
        # verify the PR diff will match the corpus diff in CONTENT (+/- lines),
        # ignoring hunk headers and index lines which git regenerates.
        # The file STATES are correct by construction (both sides derive from the
        # same diff). This compares how git RENDERS the change: git computes a
        # minimal diff, while a hand-written corpus diff may not be minimal — an
        # import reorder is the usual case. A rendering difference is recorded,
        # not treated as a failure, because the reviewer still sees the same
        # before/after. It is surfaced because it means that case's PR is not
        # byte-identical to what the sovereign reviewer was shown.
        produced = git("diff", "main", "--unified=3").stdout
        rendering_differs = not _content_matches(case.diff, produced)
        built.append((case.id, branch, case.task, rendering_differs))

    git("checkout", "-q", "main")

    manifest = {
        "corpus": str(CORPUS.relative_to(REPO_ROOT)),
        "cases_total": len(cases),
        "branches": [
            {"case_id": c, "branch": b, "task": t, "rendering_differs": d}
            for c, b, t, d in built
        ],
        "skipped": [{"case_id": c, "reason": r} for c, r in skipped],
    }
    (out / "bench-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    differing = [c for c, _, _, d in built if d]
    print(f"scaffolded {len(built)}/{len(cases)} cases into {out}")
    for _cid, branch, _task, d in built:
        print(f"  {branch}{'   [git renders this diff differently]' if d else ''}")
    if differing:
        print(
            f"\n{len(differing)} case(s) render differently than the corpus diff "
            "(same before/after state; git minimises where the corpus diff did not). "
            "Their PRs are NOT byte-identical to what the sovereign reviewer saw."
        )
    if skipped:
        print(f"\nSKIPPED {len(skipped)} (reported, never silently dropped):")
        for cid, reason in skipped:
            print(f"  {cid}: {reason}")
    print("\nNext: create a bench repo, push all branches, open one PR per branch.")
    return 0


def _content_matches(corpus_diff: str, produced_diff: str) -> bool:
    """Compare only the +/- content lines; hunk headers and index lines differ."""

    def content(d: str) -> list[str]:
        return [
            ln
            for ln in d.splitlines()
            if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
        ]

    return content(corpus_diff) == content(produced_diff)


# --- harvest --------------------------------------------------------------------------

#: Mechanical severity mapping. Fixed before data collection (see README).
_CR_SEVERITY = [
    (re.compile(r"🔴|\bCritical\b"), "high"),
    (re.compile(r"🟠|\bMajor\b"), "high"),
    (re.compile(r"🟡|\bMinor\b"), "medium"),
    (re.compile(r"🔵|\bTrivial\b"), "low"),
]
_ACTIONABLE = re.compile(r"Actionable comments posted:\s*(\d+)", re.IGNORECASE)
#: CodeRabbit renders a clean review as "No actionable comments", not as
#: "Actionable comments posted: 0". Treating only the numeric form as a completed
#: review made every clean verdict look like a non-review.
_NO_ACTIONABLE = re.compile(r"No actionable comments", re.IGNORECASE)

#: A reviewer can post a comment that is NOT a review — a rate-limit notice, an
#: "in progress" placeholder, an error. Captured naively these parse to zero
#: findings and therefore to APPROVE, which is silence-as-approval through a
#: side door: the tool never judged the diff, and would be scored as having
#: cleared it. Any capture matching these is dropped at harvest so it reaches the
#: scorer as MISSING, i.e. unavailable and flagged.
_NON_REVIEW = re.compile(
    r"Review limit reached|rate limited by|Currently processing new changes|"
    r"review is currently in progress|We are unable to review|"
    r"review_stack_entry_start|Review Change Stack",
    re.IGNORECASE,
)

#: A reviewer can post under MORE THAN ONE login. Copilot posts inline comments
#: as "Copilot" and review bodies as "copilot-pull-request-reviewer[bot]";
#: filtering on a single login silently dropped every inline finding and scored
#: it at recall 0.000 — a number that was pure harness artifact.
_REVIEWER_LOGINS = {
    "coderabbit": ("coderabbitai[bot]",),
    "greptile": ("greptile-apps[bot]",),
    "copilot": ("copilot-pull-request-reviewer[bot]", "Copilot"),
}


def _gh_json(args: list[str]) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300])
    return json.loads(out.stdout or "[]")


def cmd_harvest(args) -> int:
    logins = _REVIEWER_LOGINS.get(args.reviewer, (args.reviewer,))
    prs = _gh_json(["pr", "list", "--repo", args.repo, "--state", "all", "--limit", "100",
                    "--json", "number,headRefName"])
    captures: dict = {}
    non_reviews: list[str] = []
    for pr in prs:  # type: ignore[union-attr]
        branch = pr["headRefName"]
        if not branch.startswith("case/"):
            continue
        case_id = branch[len("case/"):]
        num = pr["number"]
        inline = _gh_json(["api", f"repos/{args.repo}/pulls/{num}/comments", "--paginate"])
        issue_comments = _gh_json(["api", f"repos/{args.repo}/issues/{num}/comments", "--paginate"])
        # PR REVIEW bodies live at /pulls/{n}/reviews, NOT in issue comments. A
        # reviewer that submits a formal review (COMMENTED / CHANGES_REQUESTED)
        # with its summary in the review body is invisible to the issue-comments
        # endpoint — Copilot does exactly this. Missing them meant scoring on
        # partial input while believing it was complete.
        reviews = _gh_json(["api", f"repos/{args.repo}/pulls/{num}/reviews", "--paginate"])
        mine_inline = [c for c in inline if c["user"]["login"] in logins]  # type: ignore[index]
        mine_summary = [
            c["body"] for c in issue_comments if c["user"]["login"] in logins  # type: ignore[index]
        ]
        mine_summary += [
            r["body"] for r in reviews  # type: ignore[index]
            if r["user"]["login"] in logins and (r.get("body") or "").strip()
        ]
        if not mine_inline and not mine_summary:
            # No response recorded. Deliberately NOT written as an approval —
            # a missing capture must reach the scorer as unavailable.
            continue
        blob = "\n".join(mine_summary)
        if not mine_inline and _NON_REVIEW.search(blob):
            # Present but not a review (rate-limited / still processing). Drop it
            # so the scorer sees an absent capture rather than a clean bill.
            non_reviews.append(case_id)
            continue
        captures[case_id] = {
            "pr": num,
            "inline": [{"path": c.get("path"), "body": c["body"]} for c in mine_inline],  # type: ignore[index]
            "summary": mine_summary,
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"reviewer": args.reviewer, "repo": args.repo,
                               "captures": captures}, indent=2) + "\n", encoding="utf-8")
    print(f"captured {len(captures)} case responses from {'/'.join(logins)} -> {out}")
    if non_reviews:
        print(
            f"DROPPED {len(non_reviews)} non-review comment(s) (rate-limit notice or "
            f"still processing) — these score as UNAVAILABLE, never as approval:"
        )
        for cid in non_reviews:
            print(f"  {cid}")
    return 0


# --- translate + score ----------------------------------------------------------------


def translate(reviewer: str, capture: dict, *, lenient_severity: str | None) -> str:
    """Prose -> VERDICT text. Mechanical; see README for the fixed rule."""
    inline = capture.get("inline", [])
    summary_text = "\n".join(capture.get("summary", []))

    if reviewer == "coderabbit":
        m = _ACTIONABLE.search(summary_text)
        if m is None and _NO_ACTIONABLE.search(summary_text):
            return "VERDICT: APPROVE"
        if m is None:
            # The pre-registered rule makes the summary count authoritative for
            # CodeRabbit. Falling back to len(inline) would be undeclared
            # discretion that can change an outcome — and a capture with no
            # count line is a non-review that harvest should already have
            # dropped. Refuse rather than guess.
            raise ValueError(
                "coderabbit capture has no 'Actionable comments posted' line; "
                "it is not a completed review (re-harvest, or the response is a "
                "rate-limit/processing notice)"
            )
        n = int(m.group(1))
    else:
        n = len(inline)

    if n == 0:
        return "VERDICT: APPROVE"

    if lenient_severity:
        severity = lenient_severity
    else:
        severity = "low"
        blob = summary_text + "\n" + "\n".join(c.get("body", "") for c in inline)
        rank = {"low": 0, "medium": 1, "high": 2}
        for pattern, sev in _CR_SEVERITY:
            if pattern.search(blob) and rank[sev] > rank[severity]:
                severity = sev
    lines = [f"F-{i + 1:03d} [{severity}] bench.py:1 external finding" for i in range(n)]
    return "VERDICT: REVISE\n" + "\n".join(lines)


def cmd_score(args) -> int:
    import asyncio

    from pxx.calibration import RecordedReviewer, breaches, load_cases, run_calibration

    data = json.loads(Path(args.captures).read_text(encoding="utf-8"))
    reviewer_name = data["reviewer"]
    captures = data["captures"]
    cases = load_cases(CORPUS)
    by_id = {c.id: c for c in cases}

    # Do not quietly drop captures the corpus does not know about: that bypasses
    # RecordedReviewer.from_cases' divergence check and inflates the reported
    # capture count, so a stale capture file could produce a confident score.
    unknown = sorted(set(captures) - set(by_id))
    if unknown:
        print(
            f"capture file references {len(unknown)} case id(s) absent from the corpus: "
            f"{unknown}\nThe bench and the corpus have diverged; re-scaffold and re-harvest.",
            file=sys.stderr,
        )
        return 2

    rows = []
    for mode in ("strict", "lenient"):
        responses = {}
        for case_id, cap in captures.items():
            case = by_id[case_id]
            lenient = (case.min_severity or "low") if mode == "lenient" else None
            responses[case_id] = translate(reviewer_name, cap, lenient_severity=lenient)
        # Score over CAPTURED cases only. run_calibration treats an absent
        # response as flagged, which is right for a live gate (unavailable must
        # block) and WRONG for a benchmark: an uncaptured clean case would count
        # as a false positive the reviewer never committed. Observed for real —
        # CodeRabbit was rate-limited on 5 clean cases and scored fp_rate 1.000,
        # a number produced entirely by missing data. Coverage is reported
        # instead, so a partial run is visibly partial rather than quietly wrong.
        scored = [c for c in cases if c.id in responses]
        report = asyncio.run(
            run_calibration(RecordedReviewer.from_cases(cases, responses), scored)
        )
        rows.append((mode, report))

    covered = len([c for c in cases if c.id in captures])
    pct = 100.0 * covered / len(cases) if cases else 0.0
    print(f"\nreviewer: {reviewer_name}   scored on {covered}/{len(cases)} cases "
          f"({pct:.0f}% coverage)")
    if covered < len(cases):
        print("  PARTIAL RUN — uncaptured cases are excluded, not counted against "
              "the reviewer. Compare across reviewers only at equal coverage.")
    print(f"{'mode':<9}{'recall':>8}{'fp_rate':>9}{'agreement':>11}  verdict")
    for mode, r in rows:
        b = breaches(r)
        # Only recall / fp / agreement are meaningful for an external reviewer;
        # format_compliance and availability describe this harness (README §2).
        meaningful = [x for x in b if x.split()[0] in ("recall", "fp_rate", "agreement")]
        print(f"{mode:<9}{r.recall:>8.3f}{r.fp_rate:>9.3f}{r.agreement:>11.3f}  "
              f"{'PASS' if not meaningful else 'FAIL: ' + '; '.join(meaningful)}")

    missing = [c.id for c in cases if c.id not in captures]
    if missing:
        print(f"\nNO RESPONSE CAPTURED for {len(missing)} case(s) — scored as unavailable, "
              f"never as approval:")
        for m in missing:
            print(f"  {m}")
    print("\nSovereign baseline to beat: recall 0.857 / fp 0.143 (qwen2.5-coder:32b)")
    print("Thresholds: MIN_RECALL 0.75, MAX_FP_RATE 0.25, MIN_AGREEMENT 0.75")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scaffold", help="materialise the corpus as a git tree")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_scaffold)

    h = sub.add_parser("harvest", help="record a reviewer's PR comments")
    h.add_argument("--repo", required=True, help="owner/repo of the bench repository")
    h.add_argument("--reviewer", required=True,
                   choices=[*sorted(_REVIEWER_LOGINS), "other"])
    h.add_argument("--out", required=True)
    h.set_defaults(fn=cmd_harvest)

    c = sub.add_parser("score", help="score captures via the production path")
    c.add_argument("--captures", required=True)
    c.set_defaults(fn=cmd_score)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
