# External review triage — doc-hygiene pass (2026-08-26)

Disposition record for a third-party doc-hygiene review of pxx received
2026-08-26. Every item was verified against the code at `2f436dd` before being
dispositioned: claims were checked by reading the cited code, by building the
sdist/wheel, and — for the stash claim — by executing a repro in a scratch
repo. Nothing here is dispositioned from the review's own reasoning.

Two findings the external pass did not raise were found during verification and
are recorded in §4; both are proven, and both are more consequential than most
of the submitted list.

## Disposition codes

- **C** — credible: a real defect or drift; fix it
- **N** — nit: real but low-value, or a one-line doc clarification
- **M** — misunderstanding: the premise does not hold against current code
- **A** — already landed: the requested change is present in the tree today

## 1. Scoreboard

| # | Submitted item | Disposition | Evidence |
|---|---|---|---|
| 1 | Memory truth: docs imply live observation works | **C (narrow)** | Only `CLAUDE.md:70` survives; README + ADR-0001 already correct |
| 2 | Memory truth: README carries stale capture claims | **A** | `README.md:110-112` already states the git-diff truth verbatim |
| 3 | Memory truth: strike changelog entry implying capture is absent | **M** | No such entry; `CHANGELOG.md:36` already states it accurately |
| 4 | SECURITY.md: document `.pxx/` as trusted state | **C (reframed)** | Real issue is a fail-open gate, not the stated exposure — see §2.2 |
| 5 | SECURITY.md: `.pxx/` swept by `--include-untracked` stash | **M** | `.pxx/` is gitignored; `stash -u` skips ignored files (repro in §3.2) |
| 6 | `review_gate.py` parse target disagrees with on-disk `CR-` paths | **M** | Two unrelated mechanisms (§3.1) |
| 7 | Scope case-sensitivity asymmetry | **N** | Real, but fails closed |
| 8 | Symlink escape on prefix-based staged-path checking | **N** | Theoretical; already acknowledged |
| 9 | WORKFLOW.md TOML missing `.pxx/`, `docs/TRUST_BOUNDARY.md` | **M** | Neither is in the canonical list; adding them is policy, not parity |
| 10 | WORKFLOW.md TOML projection is incomplete / no parity test | **C** | 10 of 18 entries present; 8 missing (§2.1) |
| 11 | README env-var table refresh | **N** | 6 of 44 documented, but that is the ADR-0001 supported envelope |
| 12 | CHANGELOG: note observer demotion was partial | **A** | `CHANGELOG.md:36` already scopes it correctly |
| 13 | Relocate/rename root-level `test_seed.py` | **C (trivial)** | Tracked leftover, outside `testpaths`, does not ship |
| 14 | Verify sdist/wheel excludes | **M** | Both artifacts built and inspected — clean (§3.3) |
| 15 | Release denylist scan enumeration vs. what ships | **C** | `_SHIPPED_PREFIXES` is stale (§2.3) |
| 16 | Decide and document `services/**` status | **A** | Decided in ADR-0001; tracked, source-only, absent from both artifacts |

Net: 5 credible (one of them a single line), 3 nits, 5 misunderstandings,
3 already landed.

## 2. Credible findings

### 2.1 `WORKFLOW.md` protected-path projection is incomplete, and untested

`pxx/protected_paths.py::PROTECTED_PREFIXES` carries 18 entries. The
machine-readable projection in `WORKFLOW.md` carries 10. Missing:

```
.aiderignore              pxx/candidate_eval.py     pxx/improvement.py
config/                   pxx/candidates.py         pxx/protected_paths.py
pyproject.toml            pxx/content_candidates.py
```

`tests/test_workflow_contract.py:52` asserts only five hardcoded names are
present, so the projection can drift by eight entries and stay green.

`WORKFLOW.md` states that `protected_paths` "is the machine projection of
docs/TRUST_BOUNDARY.md". It is not currently a faithful one. The fix is a
parity test against `PROTECTED_PREFIXES` — set equality, not membership of a
hardcoded subset — plus a statement that `protected_paths.py` is authoritative
for new prefixes.

### 2.2 `.pxx/workflow_state.json` gates review and fails open when absent

`governance.check_review_verdict()` (`pxx/governance.py:466`) reads
`.pxx/workflow_state.json` and raises a `warning` on `review_pending` and an
`error` on `rejected`. When `workflow.load_state()` returns `None` it returns
no violations at all. Deleting the file therefore clears a pending or rejected
review gate silently.

This is the substantive form of the external item. The file is local,
gitignored, agent-writable state that participates in gating; that combination
belongs in SECURITY.md. The exposure story the review attached to it (stash
sweep) does not hold — see §3.2.

### 2.3 `_SHIPPED_PREFIXES` no longer describes what ships

`pxx/governance.py:248`:

```python
_SHIPPED_PREFIXES = ("pxx/", "tests/", "README.md", "pyproject.toml")
```

with the comment "verified against the built 1.1.0 artifact". Since 1.3.4,
`MANIFEST.in` carries `prune tests`, and the built sdist contains no `tests/`
tree. The release gate (`pxx --check --shipped`) therefore scans a path that
cannot reach PyPI.

The drift is in the fail-closed direction — it over-scans, so a hostname in a
test file would block a publish it could not have affected. Not a hole, but the
comment asserts a verification that is two releases stale, and the list should
be re-derived from the current artifact.

### 2.4 Trivial hygiene

- `test_seed.py` at the repo root is a tracked loop-dogfood leftover. It is
  outside `[tool.pytest.ini_options] testpaths` and absent from both build
  artifacts, so it is cosmetic — but it reads as a broken test module.
- `.DS_Store` is not in `.gitignore`; two are untracked in the tree today.

## 3. Misunderstandings — verification detail

### 3.1 `review/claude/` and `.pxx/review/` are different mechanisms

The review asserts that "review_gate.py's parse target and on-disk reality
currently disagree" and asks for a reconciled `CR-` path convention.

- `pxx/review_gate.py` writes `review/claude/claude-findings.md` and globs
  `review/claude/claude-*.md` (`review_gate.py:249`, `:418`). `.gitignore`
  carries `/review/claude/` for exactly this. The directory does not exist in a
  clean tree because it is per-run output.
- `.pxx/review/CR-*.md` is the Claude-to-Claude work-order channel documented
  in `.pxx/review/PROTOCOL.md` — human-relayed review orders with an
  `OPEN` → `CODED` → `APPROVED` lifecycle, moved to `.pxx/review/DONE/` on
  close.

Neither reads the other's files. There is no convention to reconcile.

### 3.2 `git stash --include-untracked` does not sweep `.pxx/`

`pxx/safety.py:104-106` stashes with `--include-untracked`. `-u` stashes
untracked files; it does not touch **ignored** files, which require `-a`.
`.pxx/` is ignored (`.gitignore`). Repro in a scratch repo — a gitignored
`.pxx/workflow_state.json` and a plain untracked `loose.txt`, then
`git stash push --include-untracked`:

```
workflow_state.json present? YES
loose.txt present? NO
```

The state file survives. The stated data-loss interaction does not exist.

### 3.3 The build artifacts are clean

Both artifacts were built (`uv build`) and inspected rather than inferred from
worktree contents. Neither the sdist (74 files) nor the wheel contains
`private/`, `.aider.chat.history.md`, `.aider.input.history`, `services/`,
`.DS_Store`, `__pycache__`, `tests/`, or `test_seed.py`. Sdist top level is
`LICENSE`, `MANIFEST.in`, `PKG-INFO`, `README.md`, `pyproject.toml`,
`setup.cfg`. The wheel contains `pxx/` and `.dist-info/` only.

The one flagged match is `pxx_orchestrator.egg-info/` inside the sdist, which
is standard setuptools sdist metadata, not leakage.

The review's packaging checklist appears to have been derived from what is
present in the working tree, which does carry ignored build junk, rather than
from what the packaging config actually emits.

### 3.4 Scope case-sensitivity fails closed

`scope.is_in_scope()` (`pxx/scope.py:90`) compares case-sensitively;
`protected_paths.is_protected_path()` casefolds both sides deliberately (its
docstring explains why). The asymmetry is real. Its direction is safe: on a
case-insensitive filesystem a case-variant path reads as *out* of scope, and
`loop._out_of_scope_changes()` (`pxx/loop.py:393`) terminates the round
`OUT_OF_SCOPE`. Over-rejection, not under-protection. A one-line note is the
whole remedy.

## 4. Found during verification, not submitted

### 4.1 The `.aiderignore` mirror test passes by substring, hiding a gap

`tests/test_protected_paths.py:93-96` enforces mirror parity with:

```python
assert p in ignore
```

a substring test against the whole file. `PROTECTED_PREFIXES` contains
`config/`, and `.aiderignore` lists only `config/model-settings.yml` and
`config/aider.conf.yml` — so `"config/"` matches as a substring and the test
greens. But `config/model-metadata.json` is protected by `is_protected_path()`
and is **not** covered by `.aiderignore`:

```
is_protected_path(config/model-metadata.json) = True
listed verbatim in .aiderignore?              False
```

The editor-level backstop that `docs/TRUST_BOUNDARY.md` claims ("aider refuses
to edit its own gates") has a hole for that file, and the parity test that
exists to catch exactly this cannot see it. The `TRUST_BOUNDARY.md` mirror
test immediately below it has the same substring weakness.

Fix: match on parsed lines, not substrings, in both mirror tests.

### 4.2 See §2.2

`check_review_verdict()`'s fail-open on missing state was found while checking
the external `.pxx/` item, and is not what that item described.

## 5. Residual

The external pass declined to deep-review `loop.py`, the services internals,
and the prompt markdown, and named `loop.py` as the one remaining candidate.
That reasoning holds. One correction to its framing: it lists
"whether resume-after-interrupt corrupts workflow state" as unexecutable
residual risk. Given §2.2 that is testable, and `.pxx/workflow_state.json`
lifecycle — not `loop.py` orchestration polish — is where a further pass would
pay.
