# Security advisory dispositions — aider 0.86.2 (2026-07-21)

Standing record of how pxx dispositions the two live advisories against its
pinned dependency `aider-chat==0.86.2`.

**Status: confirmed by the owner, 2026-07-21.**
Revisit trigger for both rows: an aider release that fixes the advisory. The
aider-upgrade discipline in CLAUDE.md already forces a touch-point review on
every pin bump; re-running the scanner against the new pin is part of it.

Source: OSV/PyPA advisory DB, verified 2026-07-21. Both advisories list
`last_affected: 0.86.2`; the latest PyPI release is 0.86.2, so **no fixed
release exists for either.**

## Disposition options

- **P** — patch: carry an upstream fix (cherry-pick/fork) ahead of release
- **D** — deny: pxx closes the reachable path in its own code
- **T** — tolerate: accept temporarily, documented, with an expiry/trigger
- **G** — guard: compensating control outside pxx's code (operator-applied)
- **W** — wait: hold the release until upstream ships a fix

## Decision table

| Advisory | Reachable under pxx? | Disposition | Compensating control | Revisit trigger |
|---|---|---|---|---|
| [PYSEC-2026-2335](https://github.com/advisories/GHSA-7w7m-v5vp-w699) (CVE-2026-10175) — code injection via architect mode's auto-applied editor stage (`editor_coder.run`) | Only user-forwarded architect flags (`--architect` / `--chat-mode` / `--edit-format architect`, incl. abbreviations), or the user's own aider config | **D + T** | pxx refuses architect flags at launch (`cli._architect_mode_refusal`, exit 2); SECURITY.md: don't enable architect in your own aider config until fixed | aider release fixing it |
| [PYSEC-2026-2336](https://github.com/advisories/GHSA-hchg-qm84-cj9p) (CVE-2026-10177) — SSRF to the EC2 metadata endpoint via the docs fetcher (`api_docs.py` `requests.get`, i.e. `/web`) | Only the user's interactive `/web` fetch inside aider | **T + G** documented (→ **P-minimal** in credential-bearing deploys) | SECURITY.md egress guidance: block 169.254.169.254 / link-local / RFC1918 | aider release fixing it |

## 2335 — why D + T

Reachability: pxx never engages architect mode — it injects `--chat-mode ask`
in ask mode and ships `chat-mode: ask` + `edit-format: diff` in
`config/aider.conf.yml`. The only pxx-created path is forwarding a user's own
flags, and D closes it (`_architect_mode_refusal` in `pxx/cli.py`: pxx exits
with a clear message instead of exec'ing aider). The gate covers every
spelling aider's parser resolves to architect mode — `--architect`,
`--chat-mode`/`--edit-format` with value `architect`, and their unambiguous
argparse abbreviations down to `--ar` / `--chat-m` / `--edit-` (floors
verified against the pinned 0.86.2 parser; shorter prefixes are ambiguous
and aider rejects them itself).

Residual (T): a user who sets architect mode in their own aider config
(e.g. `~/.aider.conf.yml`). pxx cannot sandbox a user's configuration of its
own dependency any more than a shell can sandbox what you type. After D,
triggering 2335 requires four deliberate user choices — set architect in your
aider config, against pxx's documented warning, with attacker-influenced
content in context, with auto-accept on — none of which is pxx's shipped
behavior. Documented in SECURITY.md with an expiry tied to the upstream fix.

Rejected: **P** — upstream has not responded to the report
([aider#5058](https://github.com/Aider-AI/aider/issues/5058)); there is no
fix to cherry-pick, and a fork is unjustified maintenance for a path D makes
unreachable through pxx. **W** — holding the release for a self-inflicted,
off-by-default path is disproportionate.

## 2336 — why T + G, calibrated to deployment

`/web` is entirely user-initiated inside aider's interactive session; pxx
can't intercept it (pxx `exec`s into aider and is out of the picture) and
aider has no disable switch — there is nothing to close, only to bound and
mitigate. In pxx's supported posture (local developer machine, endpoints you
control, no cloud credentials) the metadata-endpoint blast radius is largely
inert: there is no IMDS on a laptop. T bounds the claim to that posture
honestly; G gives anyone outside it a real compensating control without pxx
hard-wiring a fragile, platform-specific network policy.

Escalation: in a credential-bearing deployment (cloud VM, CI runner with
IMDS), the disposition upgrades to **P-minimal** — cherry-pick the pending
upstream fix ([aider#5137](https://github.com/Aider-AI/aider/pull/5137)) —
and G is mandatory, not advisory.

Rejected: **W** — ships nothing, helps no one, and isn't warranted for a
user-initiated, network-bounded path. Enforcing G inside pxx (e.g. spawning
aider in a restricted netns) — platform-specific and fragile, and it breaks
legitimate `/web` fetches of trusted docs plus the normal endpoint call;
egress policy is an operator's control to document, not a library's to
hard-wire.

## Why not "wait" overall

Zero-advisories-ever is impossible with a real dependency. The credible
standard is: every advisory has a reasoned, reachability-bounded disposition
with a compensating control and a revisit trigger — a stronger public story
than a green scanner badge hiding an accepted risk. Both fixes are unreleased
upstream, so W ships nothing; meanwhile the paths are opt-in (2335, closed by
D) or user-initiated and network-bounded (2336, mitigated by G).
