# /ship — Release and deployment preparation

Prepare code for production release.

## What to do

- **Update version** in package metadata (version bump follows semver)
- **Update CHANGELOG** with user-facing summary of changes
- **Update README** if behavior or defaults changed
- **Run final tests** — linting, unit tests, integration tests
- **Document breaking changes** (if any) with migration guide
- **Tag the release** in git (v1.2.3 format)
- **Verify CI/CD** passes on the release branch
- **Draft release notes** for users/customers

## Checklist

- [ ] Version bumped (major.minor.patch)
- [ ] All tests passing (unit + integration)
- [ ] Linting clean (ruff, type checking)
- [ ] CHANGELOG updated with this release
- [ ] README up-to-date (commands, examples, defaults)
- [ ] No debug logging or print statements left
- [ ] Breaking changes documented with migration
- [ ] Git tag created (v1.2.3)
- [ ] Release notes drafted

## Breaking Change Template

```
## Breaking Change: Session API

**What changed:** Session tokens are now JWT instead of opaque strings.

**Why:** JWT allows offline verification without DB roundtrip.

**Migration:**
1. Old tokens: any bearer token will still work (via DB fallback)
2. New tokens: generated on next login
3. No action required from users (transparent upgrade)

**Deprecation timeline:** Old token format supported until 2026-12-31
```

## Example release notes

```
## v2.1.0 - 2026-06-02

### Features
- ✨ Offline session verification with JWT tokens
- ✨ New /recall slash command for memory queries

### Fixes
- 🐛 Fixed race condition in cache invalidation
- 🐛 Fixed memory leak in observer thread

### Security
- 🔒 Passwords now hashed with bcrypt cost 14 (up from 12)

### Breaking Changes
- ⚠️  `/session` endpoint now requires bearer token format (JWT)
```
