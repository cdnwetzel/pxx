# Engine capability interlock — design

**Status:** design, not implemented.
**Scope:** pxx reads an inference engine's own machine-readable capability declaration and
refuses, before spending budget, a run the engine says cannot work. Reference engine:
Camelid (timtoole02).

---

## 1. Problem

pxx's native backend cannot function without tool calling. Today pxx discovers that an
endpoint cannot tool-call **empirically and late**:

- **R-007** recorded that Camelid's OpenAI `tools` surface accepted a `tools` array and
  returned HTTP 200 while not executing tools (v0.4.4). We found that by watching the model
  answer in prose.
- `doctor`'s F2 probe (`_tool_calling_check`) exists precisely because *accepts `tools`* is
  not *executes tools*. It is a real generation against the endpoint, and it is **opt-in** —
  a user who never runs `pxx doctor` gets no warning at all.
- The cost of finding out late is the whole round budget, and a terminal code
  (`LOOP_DETECTED` / `NO_TEST_PROGRESS`) that names a *symptom* rather than the cause.

Camelid now **declares** this per model row, machine-readably, before a token is generated:
`tool_capable` appears on all 65 ledger rows (6 true, 59 false), is served at runtime, and is
enforced internally at `src/chat/session.rs::active_tool_capable()` with
`tool_capable_rows()` available to list working alternatives.

**We are ignoring a machine-readable declaration and rediscovering it by burning budget.**

## 2. Non-goals, and one anti-goal

- **pxx does not build a compatibility ledger.** Camelid owns engine truth — parity, quant,
  backend, context. A second, weaker opinion on the same facts is worse than none: it will
  eventually disagree with the authority and users will not know which to trust.
- **pxx does not certify models.** It reads and acts on the engine's own declaration.
- **No behaviour change against engines that declare nothing.** Ollama, vLLM, OpenAI, and any
  Camelid predating the capabilities surface must behave byte-identically to today.
- **ANTI-GOAL: this is not a security control.** See §4.6. It is footgun avoidance, and an
  operator who wants past it has several legitimate routes. Anything relying on it to *stop*
  a determined caller is misreading it.

## 3. Division of evidence

| Question | Answered by |
| --- | --- |
| Is this model correct on this hardware? | Camelid (its ledger + sealed bundles) |
| Was this run governed — scope, gates, review, HITL, budgets? | pxx |
| **Did the run's claim exceed the engine's declared support?** | **pxx, acting on Camelid's declaration** |

The third row is the join. Camelid publishes it; pxx is a policy runtime, so acting on it is
pxx-shaped work over Camelid-shaped evidence. Neither project can do it alone.

## 4. Design

### 4.1 Probe — fold into the existing session-start probe

`pxx/manifest.py::probe_model_fingerprint` already runs at session start
(`session.py:193`), is best-effort, degrades to empty on any failure, and feeds run
telemetry. The capability probe runs **on the session path**, under one shared session-start
budget. It is a single cheap GET; it is not the expensive F2 generation probe (§4.4).

**This requires a contract change to the existing probe.** `probe_model_fingerprint` today
constructs and closes its own client (`async with httpx.AsyncClient(...)` in
`manifest.py`), so "reuse the client" is not implementable against it as written. Both
probes must instead accept a **caller-owned `httpx.AsyncClient`** created once by the
session and passed in. A test should assert both probes received the same client object,
since nothing else would catch a regression to two clients.

Precisely: what is shared is the **connection pool**, not a socket. Two requests through one
`AsyncClient` may still use different sockets, particularly if issued concurrently. The
benefit claimed here is avoiding a second client construction and TLS/connection setup, not
literal single-socket reuse — the earlier wording said "same connection" and overstated it.

```python
async def probe_engine_capabilities(
    model: ModelRef, *, client: httpx.AsyncClient, deadline: Deadline
) -> EngineCapabilities:
    """Best-effort probe of an engine's self-declared capabilities.

    Camelid serves this at GET {root}/api/capabilities — NOT under /v1/ (§6.1).
    `root` is `ModelRef.endpoint` with a trailing `/v1` REMOVED: an
    OpenAI-compatible base_url conventionally ends in /v1 (the fleet's own
    coder endpoint does), and naive joining would probe /v1/api/capabilities,
    which 404s forever and silently disables the interlock. Both endpoint forms
    are tested.

    `client` is CALLER-OWNED and shared with probe_model_fingerprint (which
    must be refactored to accept one — it constructs its own today). The probe
    neither creates nor closes it. Tests inject a MockTransport through this
    client, which is why there is no separate `transport` argument.

    LIFECYCLE: the SESSION owns the client and closes it exactly once, after
    every probe task has settled — including on probe error, deadline expiry,
    and cancellation. `async with httpx.AsyncClient(...)` around the probe
    block, not a bare construct-and-forget: "neither probe closes it" is only
    safe if something else definitely does, and today's code closes per-probe
    precisely because each probe owns its own. Moving ownership up without
    moving the close would leak a connection pool per session — the class of
    defect this repo already fixed once in the git helpers (2.3.6/R-030).

    `deadline` is the SHARED session-start budget, not a fresh per-probe
    timeout: this runs alongside probe_model_fingerprint, and two independent
    2s timeouts would let startup spend 4s while claiming a 2s budget. The
    remaining time is passed down; an exhausted deadline yields failed().
    """

# and, correspondingly, the existing probe's contract changes to:
#   async def probe_model_fingerprint(
#       model: ModelRef, *, client: httpx.AsyncClient, deadline: Deadline
#   ) -> ModelFingerprint
```

```python
@dataclass(frozen=True)
class EngineCapabilities:
    engine: str = ""                      # "camelid" | "" when unknown
    row_id: str = ""                      # execution_plan.exact_model_row
    support_level: str = ""               # execution_plan.support_level
    selected_backend: str = ""
    tool_capable: bool | None = None      # None = NOT DECLARED (never False by default)
    tool_capable_rows: tuple[str, ...] = ()
    #: Canonical JSON of the served payload, NOT a dict. `frozen=True` stops field
    #: rebinding but not mutation of a nested mapping, so a dict here would let a
    #: caller edit the declaration after probing and desynchronise it from what was
    #: recorded. A string is deeply immutable by construction and still verbatim
    #: enough to re-parse for the receipt.
    raw_json: str = ""
    #: Why there is no declaration. "declared" | "undeclared" | "probe_failed".
    #: These MUST NOT be collapsed: an engine that answered and said nothing
    #: about tools is a normal Ollama box (record `tool_capable: null`), while a
    #: probe that could not complete means the gate did not run at all (record
    #: `capabilities_probe_failed`, §4.6). Both proceed, and both must remain
    #: distinguishable afterwards — otherwise a skipped gate is indistinguishable
    #: from a passed one, which is the failure mode this repo keeps finding.
    status: str = "undeclared"

    @classmethod
    def undeclared(cls) -> EngineCapabilities:
        """The engine answered but declared nothing recognisable."""
        return cls(status="undeclared")

    @classmethod
    def failed(cls) -> EngineCapabilities:
        """The probe could not complete — unreachable, 401/404, timeout, bad shape."""
        return cls(status="probe_failed")

    @property
    def declared(self) -> bool:
        return self.tool_capable is not None
```

**`tool_capable: bool | None` is load-bearing.** Collapsing "not declared" into `False`
fails closed against every Ollama box on the planet; collapsing it into `True` discards the
signal. `None` means *the engine did not say*, is stored as JSON `null`, and **stays
tri-state everywhere** — nothing downstream may coerce it to a boolean.

### 4.2 Which backends the gate applies to

The criterion is **"will this backend issue tool calls to the configured endpoint?"** That
is *not* the same as `backend.capabilities.tools`, and reading it that way is a live trap —
the actual values in the tree are:

| Backend | `capabilities.tools` | Drives the configured endpoint? | Gate applies |
| --- | --- | --- | --- |
| `native` | `True` | yes | **yes** |
| `aider` | `False` | yes | no — issues no tool calls |
| `mock` | `True` | **no** — scripted steps | no |
| `replay` | `True` | **no** — replays recorded sessions | no |

`mock` and `replay` declare `tools=True` yet never touch the live endpoint, so gating on
`capabilities.tools` alone would fire the interlock in the test backends and not in `aider`.
An earlier draft of this document asserted `mock` and `replay` were `tools=False`; they are
not. The implementation must test the composite criterion, not the flag.

### 4.3 Gate — the declaration is a PRE-CHECK, not the authority

The declaration should not be authoritative, for a reason visible in Camelid's own data:
**59 of 65 rows are `tool_capable: false`, and some are false because *unproven there*, not
incapable**. Meanwhile pxx's F2 probe is *ground truth* — it actually attempts a tool call.

A declaration must not outrank empirical evidence. If it did, a conservative `false` would
block a run that demonstrably works, and the user's only recourse would be switching off a
safety flag — which trains people to switch off safety flags.

| Engine declares | Fresh local F2 evidence (§4.4) | pxx does |
| --- | --- | --- |
| `false` | none | **Refuse to start.** Typed error naming the row and the engine's own `tool_capable_rows`. |
| `false` | a fresh probe **did** tool-call | **Proceed**, and record an `engine_under_claim` finding for upstream. |
| `true` | any | Proceed. Record the declaration. |
| not declared (`status="undeclared"`) | any | Proceed, unchanged. Record `tool_capable: null` — **not** `false`. |
| probe failed (`status="probe_failed"`) | any | Proceed, unchanged. Record `capabilities_probe_failed`. **Distinct from the row above** — the gate did not run, rather than ran and found nothing. See §4.6. |

**We fail closed on a declaration of incapability, not on the absence of one.** Absence is
the norm rather than a risk signal; a gate that fired on absence would fire on almost every
user, which is how a good gate gets configured off permanently.

This deliberately departs from `probe_model_fingerprint`'s stated contract ("identity is
telemetry and never gates a run"). Justification: a fingerprint mismatch is *ambiguous*
evidence about identity, whereas `tool_capable: false` with no contradicting probe is the
engine's own unambiguous statement that the thing pxx is about to attempt will not work.
Different claims, different handling — flagged rather than slipped past in review.

### 4.4 The F2 override record — freshness and invalidation

The contradicting evidence in row 2 comes from `doctor`'s existing F2 generation probe, which
is too expensive to run per session. It is therefore cached, and **a cache that can outlive
what it describes is a way to override a current declaration with a stale observation.** So:

```json
{ "schema": 1,
  "endpoint": "http://…", "model": "…",
  "served_fingerprint": "sha256:…",
  "tool_called": true,
  "observed_utc": "…Z" }
```

Stored under `state_dir` (never the repo). A record is used **only** when all hold:

0. `tool_called` is present and exactly `true`. A record with `tool_called: false`,
   a missing field, or a malformed body is **not evidence** and must not satisfy the
   override. Omitting this check was a fail-open introduced by an earlier revision of
   this section: every other condition below is about *freshness*, and a stale-but-fresh
   negative record would have passed all of them and unblocked the very run the
   declaration was refusing;
1. `endpoint` and `model` match the resolved `ModelRef` exactly;
2. `served_fingerprint` matches the fingerprint `probe_model_fingerprint` returns **for this
   session** — so a model swap behind an unchanged name invalidates it, which is the case
   this repo already knows bites (`runs.py:292`);
3. `observed_utc` is within a TTL (proposed: 7 days).

Any miss means *no evidence*, which lands in row 1 and refuses. **Absent evidence must never
be read as passing evidence** — the failure direction here is the opposite of §4.3's, and
deliberately so: this cache exists only to *unblock*, so a stale entry must lose its power
rather than keep granting it.

### 4.5 Terminal code

`CONFIGURATION_INVALID` + `contributing_codes=("ENGINE_TOOLS_UNCERTIFIED",)`.

It *is* a configuration error: pxx was pointed at a model row the engine says cannot do
tools. `contributing_codes` is a free-form `tuple[str, ...]` (`outcome.py:69`, no enforced
vocabulary), so this is non-breaking. **Recommend against a new `TerminalCode`** — the
23-code taxonomy is load-bearing in projection and runs, and one avoidable addition is one
avoidable migration.

**Do not map a capabilities-route failure to `MODEL_UNAVAILABLE`.** A 404, 401, or timeout
on `/api/capabilities` says nothing about `/v1/chat/completions`; the route is optional and
most engines do not serve it at all. `MODEL_UNAVAILABLE` means the generation endpoint is
unusable, and widening it here would make a real terminal code ambiguous.

### 4.6 Stated limitation: the interlock is bypassable

If `/api/capabilities` is unreachable, slow, or auth-gated while generation still works, the
gate does not fire and the run proceeds. That is a **fail-open on the probe path**, and it is
a deliberate consequence of §4.3's rule that absence of a declaration is not a risk signal —
but it means the interlock cannot be relied on to *stop* anything. An operator who wants past
it can firewall one route, or set `on_declared_incapable = "warn"`.

Mitigations, none of which change that conclusion:
- the outcome records `capabilities_probe_failed` (distinct from `undeclared`), so a run that skipped the gate is
  identifiable after the fact rather than silently indistinguishable from a gated one;
- `doctor` reports when an endpoint that previously declared capabilities has stopped doing
  so, which is the signal that something changed.

Recorded here rather than left implicit: a reader who assumed this was a hard gate would be
wrong, and the earlier draft of this document did not say so.

### 4.7 Config

```toml
[engine]
on_declared_incapable = "refuse"   # "refuse" (default) | "warn"
```

**One key, not two.** An earlier revision had `refuse_declared_incapable` *and*
`allow_uncertified_tools`, which express the same decision twice and leave their interaction
undefined — what does `refuse_declared_incapable=false` plus `allow_uncertified_tools=false`
mean? A single enumerated setting removes the question rather than documenting an answer to
it. `"warn"` is the risk-acceptance value: the run proceeds and the finding is logged.

The key was originally named `require_declared_tools`, which inverted its own meaning — the
name reads as *a declaration is required*, while the behaviour is *absence proceeds, only an
explicit false refuses*. An implementer working from the old name would have blocked every
undeclared engine, i.e. every Ollama user: the exact tri-state collapse §4.1 forbids.

Honoured only from user config, env, or CLI — **never repo-local**, the same A0b treatment
as `allow_ungated_shell` and the `[roles]` lanes, because a checked-in file must not be able
to switch off a gate.

### 4.8 Doctor — declaration vs probe as a cross-check

`doctor` empirically probes what Camelid declares: two independent sources for one fact.
Report agreement, not just the probe.

| Declared | F2 probe | Report |
| --- | --- | --- |
| true | tool-calls | ✅ declaration confirmed by probe |
| false | prose | ✅ declaration confirmed (and the gate will refuse) |
| **true** | **prose** | ⚠️ **engine over-claims** — send upstream |
| **false** | **tool-calls** | ⚠️ **engine under-claims** — send upstream |
| not declared | either | current behaviour, unchanged |

The two disagreement rows are a **cross-project negative control**: each project's claim
checked by the other's independent method. Neither of us can build that alone, and it is the
most defensible part of this design.

### 4.9 Where the declaration is recorded

**Not in `manifest.json`.** That file is the agent *identity*, hashed into
`agent_version_id`, and `manifest.py:7` states its canonical form "never contains URLs,
paths, or secrets". An engine declaration is per-run runtime data carrying an endpoint, so
putting it there would both break the documented contract and churn `agent_version_id` on
every endpoint change — defeating the drift sentinel it exists to be.

It belongs in the **run record** (run-dir telemetry / outcome), alongside the served-model
fingerprint:

```json
"engine_declaration": {
  "engine": "camelid",
  "row_id": "qwen3_4b_instruct_q8_0",
  "support_level": "supported_exact_row_smoke",
  "tool_capable": true,
  "captured_utc": "…Z",
  "raw_json": "{…}"
}
```

`raw_json` is stored alongside the parsed fields, not instead of them. The parsed fields are
what the gate acted on; `raw_json` is the payload exactly as served, so a later reader can
re-derive them and check that pxx read the declaration correctly. Storing only the parsed
view would make the receipt unfalsifiable against the engine's actual response — which is the
whole reason §4.1 keeps it immutable.

## 5. Tests

Behavioural, `httpx.MockTransport`, no network — matching `tests/test_doctor.py`.

**Positive**
1. declared `true` → session starts; declaration recorded in the run record.
2. not declared → session starts; **byte-identical to today**, and the record carries
   `tool_capable: null`, not `false` (the regression guard for every non-Camelid user).
3. capabilities probe raises → session starts; `capabilities_probe_failed` recorded; no
   crash, and **no `MODEL_UNAVAILABLE`**.
4. unrecognised payload shape → `status="probe_failed"`, never an exception.
5. **A probe failure and an undeclared engine are distinguishable in the record**: the first
   yields `status="probe_failed"`, the second `status="undeclared"` with `tool_capable: null`.
   Both proceed; asserting only "both proceed" would let the two collapse.
6. **URL normalisation, both endpoint forms.** `base_url` with and without a trailing `/v1`
   must both probe `{root}/api/capabilities`. A `/v1`-suffixed endpoint (the common
   OpenAI-compatible form) must NOT produce `/v1/api/capabilities`.
7. **Shared deadline.** With both probes stubbed to hang, total session-start probe time
   stays within the single budget rather than 2x it.
7b. **Shared client.** Both probes receive the *same* `httpx.AsyncClient` instance — assert
    object identity. Nothing else catches a regression to two clients, since two clients
    behave identically apart from setup cost.
7c. **Close lifecycle, parameterised over outcomes.** The session closes the client exactly
    once — asserted via `is_closed` (or a close-counting spy) — on each of: both probes
    succeeding, a probe raising, the deadline expiring, and the session being cancelled
    mid-probe. Testing only the success path would leave the leak paths uncovered, which is
    where a leak actually lives.

**Negative controls**
8. declared `false`, no F2 record, native backend → refuse; `CONFIGURATION_INVALID` +
   `ENGINE_TOOLS_UNCERTIFIED`; error names the row **and** the alternatives.
9. declared `false` + **fresh** F2 record showing a tool call → **proceeds**, under-claim
   finding recorded. (Guards §4.3: a conservative declaration must not block a working setup.)
10. declared `false` + F2 record whose `served_fingerprint` no longer matches → **refuses**.
11. declared `false` + F2 record older than the TTL → **refuses**.
12. declared `false` + `on_declared_incapable="warn"` → proceeds with a warning.
13. **Parameterised over every non-`native` backend** (`aider`, `mock`, `replay`) with
    declared `false` → all **proceed**. Guards §4.2: `mock` and `replay` report
    `capabilities.tools=True` yet must not be gated.
14. `on_declared_incapable` from a repo-local `pxx.toml` → **ignored, with a warning**
    (mirrors the existing A0b tests).
15. declared `false` + F2 record with **`tool_called: false`** (fresh, matching fingerprint,
    within TTL) → **refuses**. Guards the §4.4 fail-open directly: every freshness condition
    passes and the record still is not evidence.
15b. same with `tool_called` absent or the body malformed → **refuses**.
15c. **Mutation:** stub the gate to always allow → tests 8, 10, 11, 14 and 15 fail.

**Doctor**
16. declared true + prose probe → over-claim warning fires.
17. declared false + tool-calling probe → under-claim warning fires.

## 6. Risks and open questions

1. **Route resolved, not yet live-verified.** `GET /api/capabilities` is documented in
   Camelid's README as "the machine-readable route and feature inventory", default port 8181,
   and is **not** under `/v1/`. The payload shape is confirmed from a sealed evidence bundle
   (`engine`, `execution_plan`, `model_compatibility`, `support_contract`, `api_features`, …).
   Unconfirmed against a **running instance**: auth requirements, and whether the served
   payload matches the bundled one. Verify during implementation; do not skip it.
2. **Schema stability.** `execution_plan` / `model_compatibility` are not a published contract
   for third parties. Parsing must be defensive and degrade to `undeclared()` / `failed()` on any
   shape surprise, never raise. Worth asking whether Tim would version it.
3. **Under-claim friction.** 59/65 rows are `false`, some conservatively. §4.3 exists to stop
   that becoming a hard block, but a user with no F2 record still gets refused. The doctor
   under-claim row converts the friction into an upstream signal; watch for complaints.
4. **The gate is bypassable (§4.6)** and must not be described as a hard guarantee anywhere
   in user-facing docs.

## 7. What this deliberately does not copy from Camelid

Recorded because "adopt their whole approach" was considered and rejected in part:

- **Their derived-ledger + drift-check model.** Camelid's ledger is generated *from code* and
  re-derived in CI, which works because the claim *is* a declarative table. pxx's equivalent
  already exists where it fits — `pxx context audit` verifying the `WORKFLOW.md` ↔
  `protected_paths.py` mirror. No further adoption needed.
- **Their granular status vocabulary** (`supported_exact_row_smoke_chatml`,
  `blocked_pending_broader_encoder_evidence`). Right for a 65-row compatibility matrix,
  over-engineering for pxx's narrative claims.
- **Prose stuffed into JSON fields.** Camelid's `full_support_blockers` runs to ~500 words
  inside a single JSON string. It validates and is close to unusable — not queryable, not
  diffable. pxx should not imitate it; prose belongs in markdown.
- **Bulk evidence committed to the main repo.** Camelid is 374 MB. pxx ships to PyPI and
  wants a fast clone.

## 8. Definition of done

- [ ] `/api/capabilities` verified against a running Camelid (auth, payload) — risk 1
- [ ] `probe_engine_capabilities` + `EngineCapabilities` with tri-state `tool_capable`,
      the three-valued `status`, and immutable `raw_json`
- [ ] Session-start gate on the composite backend criterion (§4.2), not `capabilities.tools`
- [ ] Root-path URL derivation stripping a trailing `/v1`; one shared probe deadline;
      and `probe_model_fingerprint` refactored to accept a caller-owned client (contract
      change — it constructs its own today)
- [ ] Session owns and closes that client exactly once after all probes settle, including
      on error, deadline expiry, and cancellation (§4.1) — with the four lifecycle tests
- [ ] F2 override record requiring `tool_called: true`, plus fingerprint + TTL
      invalidation (§4.4)
- [ ] `on_declared_incapable` ("refuse"|"warn"), single key, non-repo-local
- [ ] Doctor declaration-vs-probe cross-check
- [ ] 17+ tests incl. the mutation control, the all-backends parameterisation, the
      probe-failed-vs-undeclared distinction, and the `tool_called: false` control
- [ ] Declaration recorded in the run record — **not** `manifest.json` (§4.9)
- [ ] `docs/CONFIG.md` `[engine]` section, stating the §4.6 limitation
- [ ] A receipt whose boundary states what was verified live and what was not
