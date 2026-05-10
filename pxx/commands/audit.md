# /audit

Read-only review of the selected code. Do not edit anything. Report findings as a numbered list with `file:line` references.

Look for:

**Bugs**
- Off-by-one, fencepost errors
- Mutable default arguments
- Race conditions in async or threaded code
- Resource leaks (files, sockets, db connections, subprocess pipes)
- `==` where `is` is meant (or vice versa) for None/True/False

**Unsafe patterns**
- SQL string concatenation; non-parameterized queries
- `shell=True` on subprocess with user input
- `eval`, `exec`, `pickle.loads` on untrusted data
- Path traversal: user input passed to `open`/`Path` without validation
- Credentials in code or logs

**Performance footguns**
- N+1 queries
- Accidental O(n²): nested loops over the same collection, repeated `list.index`
- Re-opening files inside loops
- Synchronous I/O in async functions

Wait for me to pick which findings to address. Do not propose fixes yet.
