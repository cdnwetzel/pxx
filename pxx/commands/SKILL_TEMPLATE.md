# Skill Template

Use this template to create custom skills for pxx workflows.

## File structure

Place custom skills in `pxx/commands/` with the naming convention `<skillname>.md`.

## Anatomy

```markdown
# /skillname — Short description

One-paragraph summary of what this skill does and when to use it.

## What to do

- Numbered or bullet points describing the workflow
- Each step should be concrete and actionable
- Include examples where helpful

## Format

Describe the expected input/output format:
- Input: markdown blocks, code snippets, natural language
- Output: code, analysis, checklist, implementation

## Example

Show a concrete before/after or worked example.
```

## Best practices

1. **Keep it focused**: one skill = one clear workflow step
2. **Make it actionable**: steps should be concrete, not vague
3. **Include examples**: show what success looks like
4. **No multi-file dependencies**: each skill should work standalone
5. **Assume context**: user is already in aider with the codebase loaded

## Built-in skills

The following skills are included in pxx:

- `/spec` — Gather requirements and write user stories
- `/plan` — Design architecture and data flows
- `/build` — Implement code following the plan
- `/test` — Write parametrized pytest tests
- `/review` — Code review and quality gates
- `/ship` — Release and deployment preparation
- `/security-audit` — Threat modeling and vulnerability audit
- `/simplify` — Code simplification and refactoring

## Usage in aider

Load a skill at the start of an aider session or mid-session:

```bash
# At launch
pxx -- --read /path/to/codebase /load pxx/commands/spec.md

# Mid-session (in aider prompt)
/load pxx/commands/plan.md
```

Aider expands `/load` into the file content, making the skill available to the AI.

## Custom skills

To create your own skill, use this template and place it in `pxx/commands/your-skill.md`.

Example: Building a `/integration-test` skill for setting up integration tests:

```markdown
# /integration-test — Integration test workflow

Write integration tests that verify multiple components working together.

## What to do

1. Identify component boundaries (what components interact?)
2. Set up shared fixtures (test database, mock APIs, temp files)
3. Write test scenarios covering the happy path and error cases
4. Assert on behavior, not internals (no mocking component internals)
5. Clean up resources after each test

## Example

Test that a user can log in and then post a message:

- Setup: create test user, start test database
- Scenario 1: valid login, then post message → success
- Scenario 2: invalid login → error
- Teardown: delete test user, close database
```

## Discovery

List all available skills:

```bash
pxx --list-skills
```

Output:

```
Available skills:
  /spec                  — Specification and requirements gathering
  /plan                  — Architecture and implementation planning
  /build                 — Implementation and coding
  /test                  — Write parametrized pytest tests
  /review                — Code review and quality gates
  /ship                  — Release and deployment preparation
  /security-audit        — Security analysis and threat modeling
  /simplify              — Code simplification and refactoring
```
