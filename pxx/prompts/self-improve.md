# Self-improvement review

You are reviewing the pxx codebase for **improvements you would propose to a
human reviewer**. You will NOT edit any files in this session.

## Output format

Produce a single markdown response with this structure:

    ## Suggestions — <YYYY-MM-DD>

    1. **<short title>** — <one-paragraph rationale>
       - Files: `path/to/file.py:L42-L60`
       - Class: docs-drift | scattered-config | convention-divergence | rotting-list | test-gap | other
       - Effort: small | medium | large
       - Risk: low | medium | high
       - Why now: <one line>

    2. ...

Up to ten suggestions, ordered most-valuable first. If you cannot find ten
material improvements, stop — do not pad the list. Quality beats quantity.

## Scope of "improvement"

The three human-curated reviewers at `../review/` (Claude, Gemini, Codex)
consistently surface five classes of issue in this codebase. These are the
in-scope categories — prefer findings that fit one of these classes, since
they're the patterns that empirically matter here:

1. **Docs ↔ code drift** — README claims, help text, comments that contradict
   actual behavior (model names, version pins, env-var lists, command
   examples). The most durable drift pattern in the repo.
2. **Configuration scattered across sources** — values duplicated in setup
   scripts, `pyproject.toml`, config YAMLs, and code defaults with no single
   source of truth (Python version pins, model names, hostnames).
3. **Unenforced conventions** — `CONVENTIONS.md` / `CLAUDE.md` claims that
   diverge from actual code (e.g., "no docstrings" stated, but docstrings
   present). Code that contradicts a stated guardrail.
4. **Rotting enumerated lists** — hardcoded inventories in docs (test counts,
   helper function counts, env-var tables, plan-status summaries) that go
   stale as the codebase grows.
5. **Missing test surface** — code paths, shell scripts, or new flags with
   behavior but no automated coverage.

Also in-scope when material: bugs, latent footguns, dead code, opportunities
to delete code without losing capability. Tag these as "other".

Out of scope: stylistic rewrites that ruff already enforces; new features
that aren't motivated by an observed problem; speculative refactors with no
near-term consumer; findings already documented in `../review/` (these are
upstream of you — surface what the reviewers missed, not echoes of what
they found).

## Hard rules

- Do NOT produce SEARCH/REPLACE blocks. This session is suggest-only.
- Do NOT propose changes to files in `.aiderignore` (model-settings, scripts,
  pyproject, install scripts, governance docs).
- Do NOT propose new dependencies without a separate "why this dep" line.
- If the codebase looks healthy and you find nothing material, say so
  explicitly. Empty findings are a valid outcome.
