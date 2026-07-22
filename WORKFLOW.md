# WORKFLOW.md — pxx agent workflow contract

Machine-readable contract for agents operating in this repository
(Phase 10.5: agent-legible workflow). The fenced TOML block below is parsed
by `pxx.workflow.load_workflow`; unknown keys, missing sections, and type
errors fail closed (`pxx workflow validate` exits 2).

This file is part of the **trusted control plane**: it is hashed into every
agent manifest (`agent_version_id` changes when it changes) and listed in
`PROTECTED_PREFIXES` — the optimizer may never modify it autonomously.

```toml
schema_version = 1

# Deterministic lifecycle hooks (top-level array of tables). event:
# PreToolUse | PostToolUse. exit 0 = allow, exit 2 = deny (fail closed).
# None configured by default.
hooks = []

[states]
initial = "idle"
names = ["idle", "planning", "executing", "verifying", "reviewing", "completed", "failed"]
terminal = ["completed", "failed"]

[budgets]
max_rounds = 25
max_tokens = 200000
max_cost_usd = 5.0
max_wall_seconds = 1800.0
max_diff_lines = 400

[commands]
test = "uv run pytest"
lint = "uv run ruff check pxx tests"

# permission mode -> allowed action classes (consumed by pxx.broker).
# Valid classes: read, write, delete, shell, network, memory.
[permissions]
ask = ["read", "memory"]
plan = ["read", "memory"]
edit = ["read", "write", "memory", "shell"]
auto = ["read", "write", "delete", "shell", "network", "memory"]

[protected_paths]
# Mirror of pxx/protected_paths.py PROTECTED_PREFIXES. The code is the
# single source of truth; `pxx context audit` verifies this mirror.
paths = [
    "pxx/safety.py",
    "pxx/errors.py",
    "pxx/governance.py",
    "pxx/protected_paths.py",
    "pxx/broker.py",
    "pxx/workflow.py",
    "pxx/clarify.py",
    "pxx/eval/",
    "pxx/improve/",
    "evals/",
    ".github/",
    "WORKFLOW.md",
    "pxx.toml",
    ".pxx/config.toml",
    # The .pxx EVIDENCE plane — records the machinery trusts (promotion/activation
    # gates read these): forgeable only by the machinery, never by the model.
    ".pxx/promotions/",
    ".pxx/candidates/",
    ".pxx/channels.json",
    ".pxx/cycle-state.json",
    ".pxx/cycle.lock",
    ".pxx/cycle-report.json",
    ".pxx/daemon-control.json",
    ".pxx/daemon-status.json",
    ".pxx/tasks.json",
    ".pxx/inbox/",
    "docs/TRUST_BOUNDARY.md",
    "tests/test_safety.py",
    "tests/test_governance.py",
    "tests/test_protected_paths.py",
    "tests/test_broker.py",
    "tests/test_workflow.py",
    "tests/test_clarify.py",
    "scripts/smoke-package.sh",
]
```

## Workflow (prose)

1. Validate the baseline: run the `test`/`lint` commands before changing
   anything; record the pre-existing state.
2. Reproduce the problem; record evidence (failing command + output).
3. Make the smallest scoped change that resolves the evidence.
4. Verify deterministically: `test` and `lint` must both pass.
5. Stop at ready-for-review: a bounded loop never self-approves; the review
   gate (blocking or advisory) judges the diff.
