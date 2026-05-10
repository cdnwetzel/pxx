# /docstring

Add docstrings to the selected function(s). Only what I asked for.

- One-line summary in imperative mood ("Parse the config", not "Parses the config")
- `Args:` and `Returns:` only when the types alone don't make it obvious
- `Raises:` only for exceptions a caller would reasonably handle
- No `Example:` unless the function is a public API entry point
- Do not add docstrings to private helpers (`_foo`)
- Do not document `self` or `cls`
- Use Google style (matches what ruff/pydocstyle expect)
