# /audit — Read-only review for bugs, unsafe patterns, and perf footguns

Audit the code for correctness, safety, and performance without making edits. Look for:
- Logic errors and off-by-one bugs
- Unsafe patterns (bare except, mutable defaults, race conditions)
- Performance footguns (N+1 queries, unbounded growth, unnecessary copies)
- Security issues (injection vectors, unvalidated input, hardcoded secrets)

Report findings organized by severity. Suggest fixes but don't implement them unless asked.
