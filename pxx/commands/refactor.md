# /refactor — Refactor for clarity; keep behavior identical

Refactor the selected code for clarity. Behavior must stay identical.

- Add or tighten type hints
- Replace dict-of-anything with `dataclass` or `TypedDict` where it clarifies intent
- Inline single-use helpers; extract when a block is used 2+ times
- Remove dead code and unreachable branches
- Do not add comments or docstrings unless I asked for them
- Do not introduce new dependencies
- Do not change public signatures unless I asked
- Show the diff, then a one-line note for each non-trivial change
