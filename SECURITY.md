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
