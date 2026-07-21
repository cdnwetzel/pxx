# Security Policy

## Supported versions
pxx ships as `pxx-orchestrator` on PyPI. Security fixes land on the latest
release line; run a supported version (1.3.x on Python 3.11–3.12).

## Reporting a vulnerability
Report suspected vulnerabilities **privately** via GitHub Security Advisories:
<https://github.com/cdnwetzel/pxx/security/advisories/new>. Please don't open a
public issue for a security report. We aim to acknowledge within a few days and
will coordinate a fix and disclosure timeline with you.

## Scope & posture
pxx orchestrates a local coding agent (aider) against LLM endpoints you control.
It sends no credentials to those endpoints and is designed to run against
localhost or a network you trust — not the open internet. The network boundary
is the trust boundary (see `docs/TRUST_BOUNDARY.md`). Exposing an endpoint to
the public internet is out of scope — don't do that.

The supported deployment is a developer machine or LAN-trusted fleet **without
cloud instance credentials**. Credential-bearing environments (cloud VMs, CI
runners with IMDS) are out of posture unless the compensating control under
PYSEC-2026-2336 below is applied.

## Active dependency advisories (`aider-chat==0.86.2`)

Full analysis, options considered, and revisit triggers:
[docs/security-advisory-dispositions.md](docs/security-advisory-dispositions.md).
Both rows are re-dispositioned on every aider upgrade; both expire when a
fixed aider release ships (none exists as of 2026-07-21 — 0.86.2 is latest).

### PYSEC-2026-2335 (CVE-2026-10175) — code injection via architect mode

In architect mode aider auto-applies its editor stage, so prompt-injected
content can become committed code. pxx never engages architect mode (it runs
ask/diff only) and **refuses to launch** if you pass `--architect`,
`--chat-mode architect`, or `--edit-format architect` — including aider's
unambiguous argparse abbreviations (`--ar`, `--chat-m`, `--edit-f`, …) —
the one path pxx itself created.

**Residual pxx cannot close:** architect mode set in your *own* aider config
(e.g. `~/.aider.conf.yml`), which pxx does not govern. **Do not enable
architect mode until aider ships a fix.** This warning expires when the
pinned aider carries the fix.

### PYSEC-2026-2336 (CVE-2026-10177) — SSRF via `/web`

Aider's interactive `/web <url>` fetches arbitrary URLs, including the EC2
metadata endpoint (`169.254.169.254`). pxx cannot intercept it (pxx execs
into aider and is gone) and aider has no disable switch.

- **Supported local posture** (no cloud credentials on the machine): risk
  accepted — there is no instance metadata to steal. Still, only `/web` URLs
  you trust.
- **Credential-bearing environments** (cloud VMs, CI runners with IMDS):
  **not supported without a compensating control.** Block egress to
  `169.254.169.254` and link-local `169.254.0.0/16` — and RFC1918 ranges
  where policy allows — at the host firewall or network egress layer, and
  apply the fix path (cherry-pick the pending upstream PR) described in the
  disposition doc before running pxx there.
