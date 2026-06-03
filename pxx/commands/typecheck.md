# /typecheck — Tighten type hints toward mypy --strict

Add or refine type hints to make the code pass `mypy --strict`. Focus on:
- Function signatures (all parameters and return types)
- Class attributes and properties
- Complex data structures (use TypedDict, dataclass, or generic types)
- Exception handling (specify exception types)

Use modern syntax: `X | Y` over `Union[X, Y]`, `Self` for returning instances, generics where appropriate.
