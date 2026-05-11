# /typecheck — Tighten type hints toward mypy --strict

Add or tighten type hints in the selected code. Treat `mypy --strict` as the bar.

- Replace `Any` with concrete types where possible
- Flag arguments that should be `Protocol`, `TypedDict`, or `Literal[...]`
- Avoid `Optional[X]` unless `None` is genuinely a domain value (not just "missing")
- Use `Self` for fluent builders and copy methods
- Use `TypeAlias` for repeated complex types
- Report anything that would still fail `mypy --strict` and explain why
- Do not change runtime behavior
