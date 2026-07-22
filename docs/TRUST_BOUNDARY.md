# Trust Boundary — the optimizer-protected set

pxx 2.0 is a self-improvement platform: an optimizer plane mines run outcomes
and proposes candidate changes, an evaluation plane scores them, and a
promotion plane ships them. For that loop to be safe, one part of the
repository must be **forever off-limits to the optimizer**: the trusted
control plane. Only humans change the files below. This is not a preference;
it is enforced by `pxx/protected_paths.py` (fail-closed) and pinned by tests
against this document in both directions.

The machine-readable source of truth is `PROTECTED_PREFIXES` in
`pxx/protected_paths.py`. Entries ending in `/` are directory prefixes;
everything beneath them is protected. All other entries are exact files.

## Protected paths

```
- pxx/safety.py
- pxx/errors.py
- pxx/broker.py
- pxx/workflow.py
- pxx/clarify.py
- pxx/governance.py
- pxx/protected_paths.py
- WORKFLOW.md
- pxx.toml
- .pxx/config.toml
- .pxx/promotions/
- .pxx/candidates/
- .pxx/channels.json
- .pxx/cycle-state.json
- .pxx/cycle.lock
- .pxx/cycle-report.json
- .pxx/daemon-control.json
- .pxx/daemon-status.json
- .pxx/tasks.json
- .pxx/inbox/
- pxx/eval/
- pxx/improve/
- evals/
- .github/
- docs/TRUST_BOUNDARY.md
- tests/test_safety.py
- tests/test_governance.py
- tests/test_protected_paths.py
- tests/test_broker.py
- tests/test_workflow.py
- tests/test_clarify.py
- scripts/smoke-package.sh
```

## Why each class is protected

- **Gates** (`pxx/safety.py`, `pxx/errors.py`): permission modes, scope,
  hooks, and budgets are deterministic, fail-closed, and cannot be overruled
  by model judgment. If the optimizer could edit the gates, every other
  guarantee in the system collapses — it could simply grant itself scope.
- **Governance** (`pxx/governance.py`): the secrets / public-content scanner
  decides what may leave the machine. Letting the optimizer weaken its own
  exfiltration checks is an obvious escape hatch, so the scanner is part of
  the control plane.
- **The boundary itself** (`pxx/protected_paths.py`, this document): the set
  that defines what is protected cannot be optimizer-writable, or protection
  would be self-deleting.
- **Evaluators** (`pxx/eval/`): case loading, the harness, hidden checks, and
  scoring decide whether a candidate is good. An optimizer that edits its own
  graders can manufacture wins; evaluation must be an adversary it cannot
  influence.
- **Improvement-plane policy** (`pxx/improve/`): candidate validation,
  promotion gates, channels/rollback, and autopromotion risk classes. The
  optimizer may *propose* (candidates are data, written under `.pxx/`), but
  the rules that validate, compare, promote, and roll back are policy — and
  policy never comes from model output.
- **Hidden cases and corpus** (`evals/`): the TOML eval corpus, including
  adversarial and hidden-check cases. If candidates could touch the corpus,
  they could delete the tests that would catch them. Candidates are also
  forbidden from seeing hidden checks, let alone writing them.
- **Promotion-policy tests** (`tests/test_safety.py`,
  `tests/test_governance.py`, `tests/test_protected_paths.py`): the tests
  that pin the control plane. An optimizer that can weaken the tests can
  weaken the gates they pin.
- **CI and release** (`.github/`, `scripts/smoke-package.sh`): workflows and
  the package smoke gate control what ships. Release automation is
  human-operated; the optimizer never writes it.

Anything not on this list is fair game for *proposals* — but even then only
through the declared candidate mechanism (`.pxx/candidates/`), one behavioral
variable at a time, with evidence. Proposals are data; this document is law.
