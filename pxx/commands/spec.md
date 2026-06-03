# /spec — Specification and requirements gathering

Gather and clarify requirements before any design or implementation.

## What to do

- **Ask clarifying questions** if the request is vague (scope, constraints, success criteria)
- **Break down the requirement** into discrete, testable user stories
- **Document assumptions** (performance targets, storage limits, concurrency model)
- **Identify constraints**: timeline, dependencies, backwards compatibility, resource limits
- **List success metrics** (quantitative and qualitative)
- **Flag unknowns**: third-party integrations, data sources, deployment model

## Format

Use a markdown outline with sections:
- User stories (numbered, GIVEN/WHEN/THEN or similar)
- Non-functional requirements (perf, scale, security, availability)
- Dependencies and blockers
- Out of scope (what we won't do)
- Success criteria (how we know it's done)

## Example

```
## Requirement: User authentication for web dashboard

### User Stories
1. GIVEN a user with valid credentials, WHEN they submit the login form, THEN they are authenticated and redirected to the dashboard
2. GIVEN a user with invalid credentials, WHEN they submit the login form, THEN they see an error message
3. GIVEN an authenticated user, WHEN they click logout, THEN their session ends

### Non-functional Requirements
- Login response time: < 500ms
- Support 1000 concurrent users
- Password must be hashed with bcrypt (min cost 12)

### Out of Scope
- OAuth/SSO integration (Phase 2)
- Password reset flow (Phase 2)

### Success Criteria
- All user stories pass
- Passwords stored securely (no plaintext)
- Session cookies are httponly + secure
```
