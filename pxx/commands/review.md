# /review — Code review and quality gates

Review code for correctness, style, and maintainability.

## What to do

- **Check against spec**: does it implement all user stories?
- **Check against plan**: does it follow the architecture?
- **Check types and APIs**: are all public functions typed? Do they make sense?
- **Check error handling**: are failures logged? Can we recover?
- **Check tests**: are edge cases covered? Do tests use realistic data?
- **Check style**: ruff clean? Line length ok? Names clear?
- **Check performance**: any obvious O(n²) algorithms? Large allocations?
- **Check security**: any input validation missing? Secrets in logs?

## Format

Produce a review checklist with sections:

- Spec alignment (all requirements met?)
- Architecture adherence (follows plan?)
- Code quality (types, style, readability)
- Test coverage (critical paths tested?)
- Performance & scale (no obvious bottlenecks?)
- Security (auth, validation, secrets)
- Documentation (README, docstrings, comments where needed?)

## Example

```
## Review: User Authentication Module

### Spec Alignment
- ✅ Login with valid credentials works
- ✅ Login with invalid credentials shows error
- ✅ Session is httponly + secure
- ⚠️  Password reset flow not in this PR (noted as Phase 2)

### Code Quality
- ✅ All functions typed
- ⚠️  `hash_password()` function is 8 lines; consider extracting constant for bcrypt cost
- ✅ No hardcoded secrets
- ✅ Follows ruff style

### Test Coverage
- ✅ Happy path (valid login)
- ✅ Error path (invalid credentials)
- ✅ Session timeout edge case
- ❌ MISSING: Test invalid email format (should fail at validation)

### Security
- ✅ Passwords hashed with bcrypt cost 12
- ✅ No password logged
- ⚠️  SQL injection risk: check prepared statements are used
```

## Output

List blockers (❌ MUST FIX), issues (⚠️  should fix), and passes (✅ good).
