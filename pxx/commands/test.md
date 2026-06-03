# /test — Write parametrized pytest tests

Write pytest tests that cover edge cases, error conditions, and success paths. Parametrize tests when appropriate. Do not mock unless explicitly asked. Aim for high coverage of untested paths.

## Guidelines
- Use `pytest.mark.parametrize` for multiple inputs
- Test both happy path and error cases
- Import only what's needed; avoid overmocking
- Assert on actual behavior, not implementation details
