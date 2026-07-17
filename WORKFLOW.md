# WORKFLOW.md — the executable workflow contract (roadmap Phase 10.5)

Repository-owned policy, separated from the orchestrator that executes it.
The TOML block below is machine-readable; `tests/test_workflow_contract.py`
asserts every field against the code it describes, so this file **cannot
drift silently** — a mismatch fails CI. The agent manifest hashes this file:
editing it changes the `agent_version_id` of every subsequent run.

```toml
schema_version = 1

[states]
initial = "idle"
phases = ["idle", "generating", "review_pending", "approved", "rejected"]
# Terminal outcome codes are pxx/outcomes.py::FAILURE_CODES (19 canonical
# codes); no terminal condition is ever parsed from message text.

[budgets]
max_rounds = 3
max_seconds = 1800.0
max_diff_lines = 150

[commands]
test = ["uv", "run", "pytest", "-q"]
lint = ["uv", "run", "ruff", "check"]
format_check = ["uv", "run", "ruff", "format", "--check"]

[review]
# blocking = the reviewer's verdict gates (REVISE heals, REJECT/NO_REVIEW
# stop). advisory = findings are recorded and surfaced but never block a run
# whose deterministic gates are green (calibration showed no local reviewer
# both catches defects and stays quiet). Set via PXX_REVIEW_MODE; part of the
# agent_version_id.
mode = "blocking"

[permissions]
filesystem = "workspace-write"      # scoped by --scope + trusted-paths (#003)
network = "llm-endpoints-only"      # model calls only; no other egress
protected_paths = [
    "pxx/safety.py",
    "pxx/scope.py",
    "pxx/governance.py",
    "pxx/review_gate.py",
    "pxx/loop.py",
    "pxx/evaluation.py",
    "pxx/calibration.py",
    "pxx/promotion.py",
    "evals/",
    ".github/workflows/",
]
```

## Workflow (one bounded loop run)

1. Preflight: hooks installed, review backend usable, clean tree, scope set.
2. Measure the baseline failing-test set.
3. Round: edit (scoped, auto-committed, `[autonomous]`-tagged) → format →
   tests → scoped lint → diff budget → out-of-scope guard → review → verdict.
4. APPROVE terminates only with: review approval AND zero baseline failures
   AND zero introduced regressions AND clean scoped lint.
5. Every exit writes a machine-readable terminal record; the run is
   attributable via `run_id` + `agent_version_id`.
6. Stop at APPROVE — commits stay local; **pushing is always a human act**.

`protected_paths` above is the machine projection of docs/TRUST_BOUNDARY.md:
automated improvement may propose changes to those paths, never apply them.
