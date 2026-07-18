> Backlog ID: 013

# pxx 1.3.1 — Install & Upgrade UX

**Status:** done — shipped in 1.3.1 (all workstreams A–F implemented; reviewer
wheel-verification is the post-publish delivery gate)
**Type:** patch release (metadata + docs + one new CLI verb + one safety-spine
fix; **no change to the orchestration/edit behavior of 1.3.0**)

**Reviewer-decision resolutions (2026-07-17, user-directed same-day ship):**
(1) services publishing — **source-install only** for 1.3.1; docs corrected to
match, PyPI publishing of 9router/agentmemory deferred. (2) verb spelling —
**`--upgrade` primary, `--update` alias**. (3) ceiling — **hard `<3.13`**
(mirrors aider-chat). Also folded in: the P5 rename-collapse safety fix from
`.pxx/review/OPEN-content-rename-escape.md`.
**Motivating incident (2026-07-17):** two faces of the same "too-new Python"
gap. (1) A first-user `uv tool install pxx-orchestrator@latest` selected
CPython **3.13**; pxx's own banner ran, then aider crashed at import —
**PEP 594 removed the `audioop` stdlib module in 3.13**, and aider → `pydub`
imports it (`ModuleNotFoundError: pyaudioop`). The release was
installable-but-broken on the default interpreter. (2) A lab `pip install` on
**3.14** instead failed at resolve time with `ResolutionImpossible` (aider-chat
has no 3.14 distribution). Both have the same one-line cause and cure: an
over-broad `requires-python`. The package itself is fine on 3.11/3.12.

The theme of 1.3.1 is: **installing or upgrading pxx should either just work,
or fail with a message that tells you exactly what to do — never a stack
trace.**

---

## Verified facts this roadmap is built on

All confirmed against the **live PyPI artifact** and the source tree, not
assumed:

1. The published `pxx-orchestrator` 1.3.0 wheel declares `Requires-Python:
   >=3.11` (no upper bound) and depends on `aider-chat==0.86.2`.
2. `aider-chat==0.86.2` itself declares `Requires-Python: >=3.10,<3.13`. So the
   *true* supported range for pxx is **3.11–3.12**, and 3.13/3.14 cannot
   resolve `aider-chat` at all → `ResolutionImpossible` on the whole solve.
3. The wheel exposes exactly one extra: `dev`. There is **no** `all`, `memory`,
   or `router` extra.
4. On PyPI: `pxx` is an unrelated 2023 project (not this one); `9router` does
   **not** exist (HTTP 404); `agentmemory` exists but is a **different**
   author's package (Moon / AutonomousResearchGroup), not this repo's service.
5. The classifiers in `pyproject.toml` are already correct (list 3.11 and 3.12
   only) — they just aren't enforced without the `requires-python` ceiling.
6. Non-bug ruled out: a local `importlib.metadata.version()` read of `1.0.0` was
   a **stale `pxx_orchestrator.egg-info` (v1.0.0) shadowing site-packages from
   the source CWD**, not a shipped version drift. The wheel METADATA and
   `pxx.__version__` both correctly read 1.3.0.

---

## Scope

**In:** the `requires-python` ceiling; truthful install docs; a documented
upgrade path for machines still on 1.2.x; a `pxx --upgrade` CLI verb; the 1.3.1
release mechanics; egg-info hygiene.

**Out:** publishing the `9router` / `agentmemory` services to PyPI (a separate
product decision — see Open Questions); any change to endpoint detection,
safety gates, or the content change-class feature shipped in 1.3.0.

---

## Workstream A — the install-blocker fix (`requires-python` ceiling) ✅ IMPLEMENTED (awaiting reviewer wheel verification)

Landed ahead of the rest of 1.3.1 via the user-authorized order
`.pxx/review/OPEN-py313-requires-python.md` (status CODED). The remaining
workstreams (B–F) still ship together in the 1.3.1 release.

**A1.** ✅ `pyproject.toml`: `requires-python = ">=3.11"` → `">=3.11,<3.13"`.
On 3.13 uv/pip no longer auto-select the interpreter (so the audioop crash is
unreachable); on 3.14 pip prints a clear *"requires a different Python
(>=3.11,<3.13)"* instead of a resolver trace. The ceiling is tied to
`aider-chat`'s own `<3.13` and gets revisited under the existing **aider
upgrade discipline** (CLAUDE.md) whenever the aider pin moves to a 3.13-capable
release. **Do not** chase dropping pydub via an aider bump as the near-term fix
— the cap is the honest bound on what actually works.

**A2.** ✅ Test `tests/test_packaging.py` pins the invariant: the shipped
`requires-python` `SpecifierSet` contains 3.11/3.12 and excludes 3.13/3.14, and
keeps the 3.11 floor. Reviewer's delivery check is against the next published
wheel (no `--python` pin → resolves to 3.12, aider imports).

**A3.** ✅ Classifiers already correct (3.11, 3.12 only) — verified, no change.

**A4.** Guardrail note: `pyproject.toml` is a hard-guardrail file; the edit was
made under the order's explicit user authorization (cap only, floor untouched),
committed as CODED, not self-approved.

---

## Workstream B — documentation truthfulness (install won't frustrate)

The failure a user actually hits is only half the problem; the docs currently
point at wrong package names and non-existent extras. Fix every user-facing
install surface to match the published reality (fact #3, #4 above).

**B1.** Canonical install name everywhere user-facing is
`pip install pxx-orchestrator` (README is already correct; `docs/INSTALL.md`
and `docs/DEPLOY.md` say `pip install pxx` / `pxx[all]` and must be corrected).
The console command and import package remain `pxx`; call that out so the
name split doesn't confuse.

**B2.** State the supported interpreter range explicitly wherever a version is
mentioned: **"Python 3.11 or 3.12 (3.13+ not yet supported — the pinned
aider-chat requires `<3.13`)."** Surfaces: `README.md:24,242`,
`docs/INSTALL.md:8,39`. (`docs/DEPLOY.md`'s Dockerfile already uses
`python:3.12-slim` — correct, leave it.)

**B3.** Remove/repair false install commands: drop `pip install pxx[all|memory|
router]` (no such extras), `pip install 9router` (404), and `pip install
agentmemory` (a stranger's package). Where the services are genuinely needed,
document the **source install** (`pip install -e services/agentmemory`) until a
publishing decision is made — never a bare PyPI name that resolves to someone
else's code.

**B4.** New `docs/INSTALL.md` troubleshooting entry mapping the exact symptoms
to the cause and fix:
- `ERROR: ResolutionImpossible` mentioning `aider-chat`, **or**
- `Cannot import 'setuptools.build_meta'` while building numpy, **or**
- `no matching distribution ... aider-chat`
→ "Your interpreter is newer than 3.12. Create the venv with Python 3.11 or
3.12: `python3.12 -m venv .venv` (or `uv venv --python 3.12`)."

**B5.** Recommend `uv` as the primary path (it's already pxx's own tool) with a
plain-`pip` fallback, and prefer `.venv` naming to avoid the
`pxx-sandbox/pxx-sandbox/…` nesting seen in the incident.

---

## Workstream C — upgrade path for existing 1.2.x installs

Machines provisioned earlier (e.g. a laptop still on a 1.2.x line) need an
explicit, documented upgrade story — today there is none. Add an "Upgrading"
section to `README.md` and `docs/INSTALL.md` covering each install method:

| Installed via | Upgrade command |
|---|---|
| `uv tool install` | `uv tool upgrade pxx-orchestrator` |
| `pipx` | `pipx upgrade pxx-orchestrator` |
| plain `pip` in a venv | `pip install -U pxx-orchestrator` |
| editable dev checkout | `git pull && uv sync --extra dev` (do **not** pip-upgrade an editable install) |

Notes to include:
- **C1.** 1.2.x installs already sit on Python ≤3.12 (1.2.x also pinned
  aider `<3.13`), so in-place upgrade to 1.3.1 is safe; the ceiling only bites
  *fresh* installs on a too-new interpreter.
- **C2.** Because pxx pins `aider-chat` exactly, upgrading pxx may move aider —
  intended, and covered by the aider upgrade discipline.
- **C3.** After upgrade, verify with `pxx --doctor` (health) and the version
  banner.

---

## Workstream D — `pxx --upgrade` CLI verb (design)

The user asked for a first-class `/update` or `/upgrade` verb. Design decision:
it must live at the **pxx CLI layer, not as an aider slash command** — pxx
`os.execv`s into aider, so an in-session slash command can never upgrade pxx
itself. Model it on the existing exit-early verbs (`--doctor`, `--self-test`):
it runs, reports, and exits without launching a session.

**D1. Spelling:** primary `--upgrade`; accept `--update` as an alias (the user
named both). Document one, alias the other.

**D2. Behavior:**
1. Detect the install method — `uv tool` vs `pipx` vs plain `pip` vs
   **editable/repo checkout**.
2. Query the latest version (PyPI JSON) and print `current → latest`.
3. On an editable/source checkout: **refuse** with guidance
   (`git pull` / `uv sync`) — never pip-upgrade over a dev tree.
4. Otherwise print `current → latest via <cmd>` and exec the correct upgrade
   command for the detected method. **Invoking `--upgrade` is itself the
   consent** — it is a deliberate top-level verb like `--doctor` / `--self-*`,
   not a flag on an editing session, so it does not add a separate confirm or
   `--yes`/`--no` step (there is nothing destructive to a user's repo to gate;
   the editable-checkout case already refuses). *(Amended 2026-07-18 to match
   the shipped code — the earlier "confirm, then exec" clause was dropped;
   review order CR-2026-07-18 [P4].)*

**D3. Offline posture:** pxx is offline-capable; `--upgrade` needs the network.
If PyPI is unreachable, degrade gracefully — say so and exit non-zero, don't
hang or trace out. (Reuse the endpoint-probe timeout style.)

**D4. Safety:** the verb touches the *environment*, not the user's repo — it
never triggers the #002 safety tag and does no edits. It should be inert (clear
message, clean exit) when run from inside an editable checkout so it can't
clobber a contributor's working copy.

**D5. Tests:** pure-function coverage for install-method detection and the
current-vs-latest comparison (mock the PyPI response); no live network in the
suite, matching the existing endpoint-probe test style.

---

## Workstream E — release mechanics (1.3.1)

**E1.** Version bump in **lockstep** (per the REL-1.3.0 order's lockstep rule —
`agent_manifest.py` reads `__version__` into agent identity; drift corrupts
`agent_version_id`):
- `pyproject.toml` → `version = "1.3.1"`
- `pxx/__init__.py` → `__version__ = "1.3.1"`
**E2.** `uv lock` so `uv.lock` records 1.3.1 (stale-lock = dirty-tree/CI risk).
**E3.** `CHANGELOG.md` → `## [1.3.1] — <date>`: Fixed (requires-python ceiling
so 3.13+ fails legibly); Docs (correct install name, real extras, upgrade
section, troubleshooting); Added (`pxx --upgrade` verb).
**E4.** Pre-flight locally before tagging: `pxx --check --shipped` clean,
`uv run pytest -q` green, `git status` clean.
**E5.** Commit `chore(release): v1.3.1`, tag `v1.3.1`, push tag → release CI
gate→build→publish (trusted publisher). PyPI is immutable, so the fix reaches
users only as a new version — 1.3.0 cannot be repaired in place.
**E6.** Reviewer delivery gate: verify the **published 1.3.1 wheel** declares
`Requires-Python: >=3.11,<3.13`, that a 3.14 install now fails with the clean
message, and that a 3.12 install still works — before marking DELIVERED.

---

## Workstream F — hygiene

**F1.** The stale `pxx_orchestrator.egg-info` (v1.0.0) in the source tree
shadows metadata reads from the repo CWD. It's gitignored (not shipped), but
regenerate/remove it so local `importlib.metadata` reads aren't misleading
during future release verification.

---

## Acceptance criteria

- A fresh `pip install pxx-orchestrator` on Python 3.13 or 3.14 fails with a
  clear *"requires a different Python (>=3.11,<3.13)"* message — no resolver
  trace, no numpy build.
- A fresh install on 3.11/3.12 still succeeds and `pxx --doctor` passes.
- Every user-facing doc install command resolves to *this* project (no `pxx`,
  no bogus extras, no `9router`/`agentmemory` PyPI names).
- README + INSTALL document upgrading for each install method.
- `pxx --upgrade` detects install method, reports current→latest, refuses on an
  editable checkout, and has pure-function tests.
- Published 1.3.1 wheel metadata verified by the reviewer against the above.

---

## Open questions for the reviewer

1. **Services publishing:** do `9router` / this repo's `agentmemory` get
   real PyPI names (they collide/404 today), or do the docs commit to
   source-install only for 1.3.1? Workstream B assumes source-install until
   decided.
2. **Verb spelling:** ship `--upgrade` with `--update` as the alias (this
   roadmap's choice), or the reverse?
3. **Ceiling style:** hard `<3.13` (chosen — mirrors aider) vs a looser
   `<3.14` with a warning. Hard cap is safer and self-documents; confirm.
