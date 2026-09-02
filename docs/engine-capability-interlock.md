# Engine capability interlock — design

**Status:** design, not implemented. Supersedes the 2026-08-22 scratch draft.
**Scope:** pxx reads an inference engine's own machine-readable capability declaration and
uses it to fail *before* a run that cannot work. Reference engine: Camelid (timtoole02).

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

## 2. Non-goals

- **pxx does not build a compatibility ledger.** Camelid owns engine truth — parity, quant,
  backend, context. A second, weaker opinion on the same facts is worse than none: it will
  eventually disagree with the authority and users will not know which to trust.
- **pxx does not certify models.** It reads and acts on the engine's own declaration.
- **No behaviour change against engines that declare nothing.** Ollama, vLLM, OpenAI, and any
  Camelid predating the capabilities surface must behave byte-identically to today.

## 3. Division of evidence

| Question | Answered by |
| --- | --- |
| Is this model correct on this hardware? | Camelid (its ledger + sealed bundles) |
| Was this run governed — scope, gates, review, HITL, budgets? | pxx |
| **Did the run's claim exceed the engine's declared support?** | **pxx, acting on Camelid's declaration** |

The third row is the join. Camelid publishes it; pxx is a policy runtime, so acting on it is
pxx-shaped work over Camelid-shaped evidence. Neither project can do it alone.

## 4. Design

### 4.1 Probe — extend the existing seam

`pxx/manifest.py::probe_model_fingerprint` already runs at session start
(`session.py:193`), is best-effort, degrades to empty on any failure, and feeds the run
manifest. Add a sibling with the same contract:

```python
async def probe_engine_capabilities(
    model: ModelRef, *, transport=None, timeout: float = 2.0
) -> EngineCapabilities:
    """Best-effort probe of an engine's self-declared capabilities.

    Camelid serves this at GET /api/capabilities (NOT under /v1/) — see §6.1.
    Engines serving nothing recognisable return EngineCapabilities.unknown();
    that is the common case and must stay silent.
    """
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
    raw: Mapping[str, Any] = field(default_factory=dict)   # verbatim, for the receipt

    @property
    def declared(self) -> bool:
        return self.tool_capable is not None
```

**`tool_capable: bool | None` is load-bearing.** Collapsing "not declared" into `False`
fails closed against every Ollama box on the planet; collapsing it into `True` discards the
signal. `None` means *the engine did not say*, and pxx must then behave exactly as today.

### 4.2 Gate — the declaration is a PRE-CHECK, not the authority

This is the substantive change from the first draft, which made the declaration
authoritative. It should not be, for a reason visible in Camelid's own data: **59 of 65 rows
are `tool_capable: false`, and some are false because *unproven here*, not *incapable***.
Meanwhile pxx's F2 probe is *ground truth* — it actually attempts a tool call.

A declaration must not outrank empirical evidence. If it did, a conservative `false` would
block a run that demonstrably works, and the user's only recourse would be switching off a
safety flag — which trains people to switch off safety flags.

At session start, **only** when the backend requires tool calling (native yes; aider, mock,
replay no):

| Engine declares | Local probe evidence for this endpoint+model | pxx does |
| --- | --- | --- |
| `false` | none recorded | **Refuse to start.** Typed error naming the row and the engine's own `tool_capable_rows`. |
| `false` | a recorded probe **did** tool-call | **Proceed**, and record an `engine_under_claim` finding for upstream. |
| `true` | any | Proceed. Record the declaration in the manifest. |
| not declared (`None`) | any | Proceed, unchanged. Record `declared=false` in the manifest. |
| probe of the capabilities route failed | any | Proceed, unchanged. A dead endpoint is already `MODEL_UNAVAILABLE`. |

The probe evidence comes from `doctor`'s existing F2 check, which writes a small record keyed
by `(endpoint, model)` under `state_dir`. No new network call on the session path.

**We fail closed on a declaration of incapability, not on the absence of one.** Absence is
the norm rather than a risk signal; a gate that fired on absence would fire on almost every
user, which is how a good gate gets configured off permanently.

This deliberately departs from `probe_model_fingerprint`'s stated contract ("identity is
telemetry and never gates a run"). The justification: a fingerprint mismatch is *ambiguous*
evidence about identity, whereas `tool_capable: false` with no contradicting probe is the
engine's own unambiguous statement that the thing pxx is about to attempt will not work.
Different claims, different handling — flagged here rather than slipped past in review.

### 4.3 Terminal code

`CONFIGURATION_INVALID` + `contributing_codes=("ENGINE_TOOLS_UNCERTIFIED",)`.

It *is* a configuration error: pxx was pointed at a model row the engine says cannot do
tools. `contributing_codes` is a free-form `tuple[str, ...]` (`outcome.py:69`, no enforced
vocabulary), so this is non-breaking. **Recommend against a new `TerminalCode`** — the
23-code taxonomy is load-bearing in projection and runs, and one avoidable addition is one
avoidable migration.

### 4.4 Config

```toml
[engine]
require_declared_tools = true     # default; gate on an affirmative false
allow_uncertified_tools = false   # explicit risk-acceptance escape hatch
```

`allow_uncertified_tools` downgrades the refusal to a warning. Honoured only from user
config, env, or CLI — **never repo-local**, the same A0b treatment as `allow_ungated_shell`
and the `[roles]` lanes, because a checked-in file must not be able to switch off a gate.

### 4.5 Doctor — declaration vs probe as a cross-check

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

### 4.6 Receipt surface

`EngineCapabilities.raw` is captured verbatim into the run manifest, so a pxx governance
receipt can cite the engine's declaration as-served:

```json
"engine_declaration": {
  "engine": "camelid",
  "row_id": "qwen3_4b_instruct_q8_0",
  "support_level": "supported_exact_row_smoke",
  "tool_capable": true,
  "captured_utc": "2026…Z"
}
```

## 5. Tests

Behavioural, `httpx.MockTransport`, no network — matching `tests/test_doctor.py`.

**Positive**
1. declared `true` → session starts; manifest records the declaration.
2. not declared → session starts; **byte-identical to today** (the regression guard for every
   non-Camelid user).
3. capabilities probe raises → session starts; no crash (best-effort contract preserved).

**Negative controls**
4. declared `false`, no probe record, native backend → refuse; `CONFIGURATION_INVALID` +
   `ENGINE_TOOLS_UNCERTIFIED`; error names the row **and** the alternatives.
5. declared `false` **but** probe record shows a tool call → **proceeds**, under-claim
   finding recorded. (Guards the §4.2 restructure: a stale authority must not block a working
   configuration.)
6. declared `false` + `allow_uncertified_tools=true` → proceeds with a warning.
7. declared `false` + non-tool backend (aider/mock) → **proceeds**. The gate must not fire
   where tools are irrelevant.
8. `allow_uncertified_tools` set from a repo-local `pxx.toml` → **ignored, with a warning**
   (mirrors the existing A0b tests).
9. **Mutation:** stub the gate to always allow → tests 4 and 8 fail.

**Doctor**
10. declared true + prose probe → over-claim warning fires.
11. declared false + tool-calling probe → under-claim warning fires.

## 6. Risks and open questions

1. **Route resolved, not yet live-verified.** `GET /api/capabilities` is documented in
   Camelid's README as "the machine-readable route and feature inventory", default port 8181,
   and is **not** under `/v1/`. The payload shape is confirmed from a sealed evidence bundle
   (`engine`, `execution_plan`, `model_compatibility`, `support_contract`, `api_features`, …).
   What remains unconfirmed is behaviour against a **running instance**: auth requirements,
   and whether the served payload matches the bundled one. Downgraded from blocking to
   *verify during implementation* — but do not skip it.
2. **Schema stability.** `execution_plan` / `model_compatibility` are not a published contract
   for third parties. Parsing must be defensive and degrade to `unknown()` on any shape
   surprise, never raise. Worth asking whether Tim would version it.
3. **Latency.** One more 2s-timeout probe at session start. Fold it into the same `httpx`
   client as the fingerprint probe rather than opening a second connection.
4. **Under-claim friction.** 59/65 rows are `false`, some conservatively. §4.2 exists to stop
   that becoming a hard block, but a user with no probe record still gets refused. The doctor
   under-claim row converts the friction into an upstream signal; watch for complaints.

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
- [ ] `probe_engine_capabilities` + `EngineCapabilities` with tri-state `tool_capable`
- [ ] Session-start gate, tool-requiring backends only, probe-record override per §4.2
- [ ] `allow_uncertified_tools`, non-repo-local
- [ ] Doctor declaration-vs-probe cross-check
- [ ] 11 tests incl. the mutation control
- [ ] Declaration captured verbatim in the run manifest
- [ ] `docs/CONFIG.md` `[engine]` section
- [ ] A receipt, with the boundary stating what was verified live and what was not
