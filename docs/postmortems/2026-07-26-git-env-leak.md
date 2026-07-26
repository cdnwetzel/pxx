# Postmortem: the day my pre-commit hook nearly staged the deletion of my own repo

*2026-07-26 · pxx v2.1.0 → v2.1.1 · receipts: [R-002](../RECEIPTS.md)*

**TL;DR.** Running pxx's test suite from inside a git pre-commit hook made
test scaffolds operate on the *real* repository instead of their tmp
directories — culminating in the real index staging the deletion of all
192 tracked files. Root cause: git exports `GIT_DIR`, `GIT_INDEX_FILE`,
and `GIT_AUTHOR_*` into hooks, and every subprocess down the chain
inherits them. The fix (shipped same day in 2.1.1) scrubs those variables
at every git spawn site pxx owns. An independent adversarial review then
proved the same hole existed in a site the patch had missed — by
exploiting it live. No data was lost at any point; the working tree was
never touched.

## What happened

A routine hygiene commit (`.gitignore` additions) ran through this repo's
pre-commit hook, which executes the full test suite. Four goal/safety-net
tests failed — tests that had passed minutes earlier in a standalone run.
A retry failed nine. All thirteen passed in isolation, every time.

Then the commit aborted at the hook's diff-cap gate with a number that
made no sense: **39,980 staged lines** for a ~45-line change. `git status`
showed every tracked file staged for deletion, plus one stray `a.py`.

## The wrong hypothesis first

Another agent session was editing the working tree concurrently, and file
churn mid-run was the obvious suspect — I recorded that hypothesis as
likely. It was wrong, and worth admitting: the churn only varied *which*
tests failed between runs. Deterministic reproduction, not correlation,
found the cause.

## The repro

```sh
GIT_DIR="$PWD/.git" GIT_INDEX_FILE="$PWD/.git/index" \
  uv run pytest tests/test_goal.py tests/test_safety_net.py
```

Fails on v2.1.0 in a clean shell. Green without the variables. That's the
whole bug: `git commit` exports repo-targeting and identity variables
into hooks. A test helper's `git init` + `git add -A` in a tmp directory,
with `GIT_DIR` pointing at the real repo, computes "tracked files vs a
worktree containing one file" — and stages 192 deletions plus that one
file into the **real index**. The stray `a.py` even fingerprinted the
culprit: its staged blob matched a test fixture byte-for-byte.

Identity leaks the same way: `GIT_AUTHOR_NAME` alone flips a test that
asserts commits use the target repo's configured identity — author env
beats repo config in git.

Recovery, for completeness: the working tree was intact the entire time
(only the index was rewritten), so recovery was a mixed `git reset` and
nothing else.

## The fix

`pxx/gitenv.py`: one sanctioned function, `git_env()`, returning the
process environment minus the repo-targeting set (`GIT_DIR`,
`GIT_INDEX_FILE`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
`GIT_NAMESPACE`, `GIT_PREFIX`) and the identity overrides
(`GIT_AUTHOR_*`, `GIT_COMMITTER_*`). Threaded through every git
subprocess pxx spawns, the aider process environment (aider commits via
its own git calls), and the agent's `run_shell` tool. Transport and
config variables (`GIT_SSH*`, `GIT_CONFIG_*`) are deliberately kept —
removing those breaks user setups without any wrong-repo risk. The test
suite scrubs the same set via an autouse fixture, and poisoned-environment
regression tests (`tests/test_gitenv.py`) pin the incident shapes.

## The review caught what I missed

The patch went to an independent adversarial review with instructions to
sweep for unscrubbed spawn sites. It found one — the eval harness — and
did not stop at flagging it: it set `GIT_DIR` to a victim repo, called
the harness's git helper against a scaffold, and showed the victim's
index staging a deletion. The exact incident, reproduced at a site the
patch claimed to cover. It also made the sharpest process observation of
the day: the suite-wide scrub fixture *masks* unscrubbed sites — only
tests that re-poison the environment themselves can catch a missed one.
That's why every regression test in `tests/test_gitenv.py` sets its own
poison.

Round two of the review re-ran the exploit against the fix (victim
clean), then caught one last thing before the release: a version-string
sync miss that would have failed the package smoke gate. Ship, verified.

## Lessons

1. **Hooks are a hostile environment for subprocess trees.** Any tool
   that shells out to git and can be invoked from a hook, CI step, or
   another tool's hook inherits a loaded gun. Scrub at the boundary you
   own.
2. **A wrong hypothesis recorded is cheap; a wrong hypothesis asserted is
   expensive.** The concurrency theory was plausible and fit the timing.
   The deterministic repro cost ten minutes and replaced it.
3. **Blanket test-environment hygiene can hide the defect it compensates
   for.** The autouse scrub makes the suite honest under hooks — and
   silently protects unfixed call sites. Pair every global mitigation
   with tests that bypass it on purpose.
4. **Adversarial review with verify-by-execution earns its cost.** The
   strongest item in 2.1.1 wasn't in the original patch plan; the review
   process put it there and then proved the fix.

*This document names no private infrastructure by design; environment
details are role-described. The receipts entry (R-002) carries the
reproduction procedure and boundaries.*
