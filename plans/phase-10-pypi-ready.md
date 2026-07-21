# Phase 10: PyPI-Ready — professional, clean, publishable
> Backlog ID: 005

## Overview

**Goal:** Make pxx a final product clean and professional enough to publish on
PyPI — installable and usable by someone who is not the author, on a machine
that is not the Studio.

**Status:** `done` — **SHIPPED 2026-06-10**: `pxx-orchestrator 1.0.0` is live
on PyPI (https://pypi.org/project/pxx-orchestrator/) and on TestPyPI, both
verified installable (fresh-venv install + smoke from each index). Published
as `pxx-orchestrator` because the `pxx` name is squatted by an unrelated 2023
project; the console script and import package remain `pxx`. Option B (core on
PyPI; services are a repo-clone feature).

**Today's reality (cited):** pxx is a personal/offline tool with vestigial
publish scaffolding. It's on PyPI at **0.0.9** while local is **1.0.0** (the
`c79b276 "Prepare for v1.0.0 public release"` never shipped). The core
ask/edit-against-Ollama flow is largely portable (endpoints are env-overridable
— `pxx/endpoints.py:91,107`), but the optional services and several defaults are
machine-specific.

**Blocked by:** nothing technical; gated by one architectural decision (below).

---

## The crux decision: how do the optional services ship?

`services/9router`, `services/agentmemory`, `services/docs-rag-sme` are separate
installable packages (own `pyproject.toml`, version, console scripts). A
`pip install pxx` from PyPI does **not** include `services/`, so
`--with-router` / `--with-memory` / `--with-docs` can't work from a wheel unless
the services ship too. `NineRouterManager.start()` already prefers the installed
console script (`pxx/router.py:46` Try 1 = `nine-router`); the dir path is only
the dev fallback (now relative — `pxx/router.py:_SERVICE_DIR`).

- **Option A — separate published packages + extras.** Publish `pxx-router`,
  `pxx-memory` (rename: PyPI names can't be bare `9router`/`agentmemory`); pxx
  declares them as optional extras (`pip install pxx[memory,router]`) and
  invokes their console scripts. Cleanest product; most packaging work.
- **Option B — core-only on PyPI; services are "clone the repo."** Publish only
  the portable core (ask/edit + memory *client* against any Ollama). The
  supervisor services stay a repo-clone advanced feature, clearly documented.
  Ships fastest; honest about scope. **Recommended starting point.**
- **Option C — bundle services into the pxx wheel.** Rejected: they have their
  own deps/venvs; `package_data` bundling is fragile.

**This decision sizes Phases 10.2/10.4 and must be made first.**

---

## Phase 10.1: De-personalize the package

**What:** Remove machine-specific assumptions so a stranger's install works.

**Tasks:**
- [x] `pxx/router.py:68`, `pxx/memory.py:89` — relative `_SERVICE_DIR` + env
      overrides (done, commit `cdac9fa`).
- [ ] `pxx/drift.py:42-43` — `<user>@workstation` / personal home-path defaults are
      not env-overridable. Add `PXX_DRIFT_SSH_TARGET` / `PXX_DRIFT_REMOTE_PATH`,
      and skip the drift feature cleanly when unset (it's inherently personal —
      cross-machine sync between *specific* hosts).
- [ ] `pxx/endpoints.py:29` — default `workstation` →
      a generic default (`localhost:11434`) or none; document the env overrides.
- [ ] `pxx/_core_files.py:49` — cosmetic `/Users/...` docstring example → generic.

**Effort:** ~0.5 day. **Status:** `done` (commit caea71f)

## Phase 10.2: Services packaging (per the crux decision)

**What:** Make the optional features installable per Option A or B.

**If Option B (recommended):** document services as a repo-clone feature; ensure
the core degrades gracefully when a service/console-script is absent (it already
raises RuntimeError — verify the user message is friendly). Minimal.

**If Option A:** rename the service dist packages (PyPI-legal, namespaced under
`pxx-`), publish them, add `[project.optional-dependencies]` extras, and wire
pxx to their console scripts only.

**Effort:** B ≈ 0.5 day. **Status:** `done` (Option B; commits f2f0c9c, 88a0375)

## Phase 10.3: Metadata, license, version hygiene

**What:** Make the distribution metadata real.

**Tasks:**
- [ ] `pyproject.toml:12` — `authors = [{name = "pxx"}]` is a placeholder; set
      real author name + email.
- [ ] **No `LICENSE` file exists** though `pyproject.toml:11` declares MIT. Add a
      standard MIT `LICENSE` with the author's name/year.
- [ ] Reconcile version: PyPI is at 0.0.9, local at 1.0.0. Pick the next real
      release version and confirm the author owns the `pxx` name on PyPI.
- [ ] Re-check classifiers: `Python :: 3.13` is claimed but install is pinned to
      3.12 (`CLAUDE.md`); validate or drop.

**Effort:** ~0.5 day. **Status:** `done` (commit 82eb15e; packaged the .md assets)

## Phase 10.4: User-facing docs

**What:** A README that stands alone for a stranger.

**Tasks:**
- [ ] README (`README.md`, 219 lines): ensure Install / Quickstart / Configuration
      sections that don't assume the Studio fleet. The env-var table from
      `CLAUDE.md` is the config contract — surface it for users.
- [ ] Keep `CLAUDE.md` as the contributor/dev doc (fleet-specific) — but the
      README must not depend on it.
- [ ] Document the services story per the crux decision.

**Effort:** ~1 day. **Status:** `done` (commit 82eac22)

## Phase 10.5: Build + publish dry-run

**What:** Prove it installs clean off a built artifact, not the repo.

**Tasks:**
- [ ] `python -m build` (sdist + wheel); `twine check dist/*`.
- [ ] Install the wheel into a fresh venv on a path that is NOT the repo; run
      `pxx --doctor`, an ask-mode session against a local Ollama, and confirm the
      optional features degrade gracefully (or work, per Option A).
- [ ] TestPyPI upload + install before the real PyPI release.

**Effort:** ~0.5 day. **Status:** `done` (commit 82eb15e; packaged the .md assets)

---

## Success Criteria

- [x] `pip install pxx` in a clean venv (not the repo) → `pxx` runs (verified:
      `--list-commands`, `--doctor`, packaged `system.md` resolves, degrades
      without Ollama/services).
- [x] No hardcoded `/Users/...`, office hostnames, or personal SSH targets on a
      default code path (`grep` clean across `pxx/*.py` and `README.md`).
- [x] `twine check` clean on sdist + wheel; LICENSE present; metadata real.
- [x] README stands alone for a stranger (no fleet refs; pip-core + repo-services).
- [x] Optional services degrade gracefully with a clear "clone the repo" message,
      fast (~0.2s, no retry spam).

## Decisions (resolved)

1. **Services packaging → Option B** (core on PyPI; services are a repo-clone
   feature). A is a future option if the services warrant their own releases.
2. **Published product scope → core orchestrator** (ask/edit + memory client);
   supervisor/loop are advanced repo features.
3. **drift.py → kept**, but de-personalized: no default host, env-configured,
   skips cleanly when unset.

## Dependencies

**Blocked by:** nothing. **Relates to:** Phase 9 (the loop) — both want the
de-personalization in 10.1; ship 10.1 regardless of the loop.
