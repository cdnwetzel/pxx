# /test — Write parametrized pytest tests

Write pytest tests for the target function(s).

- Parametrize edge cases: empty, single, many, boundary values, error inputs
- One logical assertion per test (multiple `assert`s on the same return value is fine)
- Use fixtures only when setup is shared across 3+ tests
- No mocking unless I explicitly say to; prefer real objects and temp files (`tmp_path`)
- Test file mirrors source path: `src/x/y.py` → `tests/x/test_y.py`
- Use `pytest.raises` with `match=` for expected exceptions
- Name tests `test_<behavior>`, not `test_<function>`
