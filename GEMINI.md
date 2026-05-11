# Gemini CLI — Project Instructions

These rules apply when using Gemini CLI to develop `pxx`.

## Workflow Mandates

1.  **Always write a plan file**: For any change involving more than a simple fix, create a new plan in `plans/` using `plans/_template.md`.
    -   Register the plan in `plans/backlog.md` by picking the next free ID.
    -   Keep the plan status updated (`in-progress`, `done`, etc.).
2.  **Autonomous Execution**: Gemini CLI is pre-approved to proceed with implementation once a plan is established and "planned" (or "in-progress"). No separate "Directive" turn is required if the plan is clear.
3.  **Commit and Push**: After verifying changes (tests, linting), commit with a descriptive message and push to the remote repository.

## Engineering Standards

-   **Follow existing conventions**: Refer to `CONVENTIONS.md` and `CLAUDE.md`.
-   **Python Style**: Python 3.11+, type hints on every signature, no `Any`.
-   **Tooling**: Use `uv` for dependency management and running tests/lint.
    -   Lint: `uv run ruff check --fix` and `uv run ruff format`
    -   Test: `uv run pytest`

## Hard Guardrails

Refer to the "Hard guardrails" in `CONVENTIONS.md`. Do not modify configuration files, setup scripts, or metadata files without explicit confirmation.
