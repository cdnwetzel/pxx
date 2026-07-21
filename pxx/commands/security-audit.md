# /security-audit — Security analysis and threat modeling

Audit code and systems for security vulnerabilities and risks.

## What to do

- **Identify trust boundaries** (where data crosses from untrusted to trusted)
- **Look for OWASP Top 10**: injection, broken auth, XSS, CSRF, insecure deserialization, etc.
- **Check input validation** on all external inputs (HTTP, files, API responses)
- **Audit secrets handling** (no secrets in logs, env vars, code, .git history)
- **Verify authentication/authorization** (is access control enforced everywhere?)
- **Check cryptography**: are secure algorithms used? Key rotation? TLS?
- **Look for timing attacks**: are comparisons constant-time where needed?
- **Check dependencies**: are they maintained? Any known CVEs?

## Format

Produce a security assessment with sections:

- Threat model (trust boundaries, data flows, assumptions)
- OWASP checklist (which top 10 apply? what's the risk?)
- Input validation audit (where is external data handled?)
- Secrets handling (what secrets exist? how are they stored?)
- Authentication/authorization (is every protected endpoint guarded?)
- Cryptography (secure algorithms? Keys properly managed?)
- Findings (Critical, High, Medium, Low severity)

## Severity levels

- **Critical**: Allows complete compromise (RCE, auth bypass, data breach)
- **High**: Significant risk (unvalidated input, weak crypto)
- **Medium**: Partial risk, defense-in-depth issue (missing logging, timeout)
- **Low**: Best practice gap, minimal immediate risk (weak password policy)

## Example

```
## Security Audit: Session Management

### Trust Boundaries
- [Trusted] Server-side session store
- [Untrusted] Client-submitted session token
- [Untrusted] HTTP headers (User-Agent, etc.)

### OWASP Checklist
- ✅ A01:2021 Broken Access Control: sessions checked on every request
- ⚠️  A02:2021 Cryptographic Failures: tokens use HMAC-SHA256 (acceptable but upgrade to Ed25519 recommended)
- ❌ A03:2021 Injection: prepared statements used (not vulnerable)
- ⚠️  A05:2021 Broken Access Control: no CSRF token on POST (high risk if not httponly)

### Findings

**CRITICAL:** Session tokens stored in localStorage
- Risk: XSS attack can steal session
- Fix: Move to httponly + secure cookie
- Timeline: Fix before next release

**HIGH:** No rate limiting on login endpoint
- Risk: Brute force password guessing
- Fix: Add rate limit (e.g., 5 tries per minute per IP)
- Timeline: 1 week

**MEDIUM:** Weak password policy (6 chars, no complexity)
- Risk: Users choose guessable passwords
- Fix: Enforce 12+ chars, require uppercase/number/symbol
- Timeline: Next release
```

## Output

List all findings with severity, risk description, and recommended fix.
