# Template: bounded single-file codegen

- **purpose:** produce one complete source file (or one complete patch unit)
  implementing a fixed contract — nothing else in the output.
- **output contract:** exactly one fenced code block in the target language,
  parseable/compilable as-is, satisfying the stated contract and constraints.
- **validated against:** no measured run yet.
- **measured result:** none — shape follows the measured JSON-contract
  pattern (explicit role, exact-output statement, testable rules, delimited
  input).
- **status:** UNVALIDATED.

## Prompt shape

```text
You are the CODE worker. Implement <contract statement — behavior, edge
cases, error handling> in <language>, as the single file <filename>.
DO NOT call any tools — no shell, no file reads. Tool use terminates the task.
Output EXACTLY ONE <language> code block and nothing else — no prose before
or after.
Rules: <public API names and signatures are fixed; dependencies limited to
<list>; no I/O / no globals / no network — whatever the caller enforces>.
--- CONTEXT ---
<any interfaces, existing code, or fixtures the implementation must fit —
complete and self-contained>
--- END CONTEXT ---
```

```json
{
  "caller_checks": [
    "single fenced block, language tag matches",
    "file parses/compiles",
    "public API names present with fixed signatures",
    "contract tests (caller-owned) pass",
    "no forbidden constructs (caller lint)"
  ]
}
```

## Notes

- Keep the scope to one file. Multi-file generation under a single-shot
  contract is where models invent files the caller cannot verify.
- The caller's checks — not the prompt — are the real gate. The prompt's job
  is to make the desired output shape the path of least resistance.
