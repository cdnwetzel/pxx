# pxx evaluation corpus (roadmap Phase 13)

**Optimizer-protected** (docs/TRUST_BOUNDARY.md): no automated process may
modify cases, fixtures, or checks. Growing the corpus is a human act,
triggered by: an unexpected production failure, a reviewer miss a human
caught, a promoted-candidate regression, or a newly discovered failure mode.

## Layout

- `micro/` — small deterministic code-repair tasks (fast, reproducible).
- `regression/` — historical pxx defect *classes*, transcribed so they can
  never silently return (each case header cites its origin).
- `adversarial/` — cases whose real subject is the checks: deleting tests,
  weakening assertions, `noqa`, skip markers, out-of-scope edits.

## Case format (TOML, stdlib `tomllib` — deliberately not YAML)

```toml
schema_version = 1
id = "m1-example"
tier = "micro"              # micro | regression | adversarial
category = "bugfix"
task = "What a live agent would be told. Never includes the checks below."

[fixture]                    # materialized into a fresh disposable git repo
"src/lib.py" = "..."
"tests/test_lib.py" = "..."

[checks]
allowed_files = ["src/lib.py"]          # hidden: change boundary
commands = [["python","-m","pytest","-q"]]  # visible deterministic gates
forbidden_patterns = ["noqa"]           # hidden: regex over added diff lines
tests_unchanged = true                  # hidden: test files untouched

[patches.honest]             # scripted arm: must pass every check
"src/lib.py" = "...fixed..."

[patches.cheat]              # scripted arm: MUST be caught by some layer
"tests/test_lib.py" = "...weakened..."
```

## Self-check (`pxx --eval [tier|all]`)

The laboratory proves itself before any model runs in it: every honest
patch must pass all checks; every cheat patch must be caught by at least
one layer (hidden or visible-deterministic). A cheat with zero failures
means the *case* is broken — self-check fails closed. The full corpus
self-check also runs in the test suite on every commit.

A live-agent arm (running `pxx --loop` inside the fixture worktree instead
of applying a scripted patch) is the next expansion; the scripted arms stay
forever as the harness's own regression suite.
