# /build — Implementation and coding

Write the code following the plan.

## What to do

- **Follow the architecture** from /plan exactly
- **Write minimal, correct code** — no over-engineering, no premature optimization
- **Use stdlib and existing dependencies first** — new deps need justification
- **Type hints on all public functions** (no `Any` without reason)
- **No comments unless the WHY is non-obvious** — let code be self-documenting
- **Prefer simple over clever** — one if/else is better than a ternary
- **Batch related changes** into a single commit with clear message
- **Test as you go** — write tests before or alongside implementation

## Style rules

- Python: 3.11+ modern syntax (match, | unions, dataclass)
- Type hints are required on public signatures
- Docstrings only on public functions when the WHY is non-trivial
- No defensive code for impossible states
- Prefer pathlib.Path over os.path
- Use ruff format (don't fight the formatter)

## Example workflow

```
1. Create new module with stub functions
2. Write basic tests (red)
3. Implement function (green)
4. Refactor if needed (refactor)
5. Commit with message: "feat(module): add description"
6. Move to next function
```

## Avoid

- Premature abstraction (wait until you see the pattern 3 times)
- Trying to make code "future-proof" (YAGNI)
- Mocking internal code (test behavior, not structure)
- Over-commenting (code should be self-explanatory)
- Defensive try/except for control flow
