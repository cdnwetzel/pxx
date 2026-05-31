# Gemini CLI — Project Instructions

These rules apply when using Gemini CLI to develop `pxx`.

## Workflow Mandates

1.  **Always write a plan file for pxx development changes**: For any change involving more than a simple fix to pxx's code/tests/configs/scripts/prompts/docs, create a new plan in `plans/` using `plans/_template.md`.
    -   Register the plan in `plans/backlog.md` by picking the next free ID.
    -   Keep the plan status updated (`in-progress`, `done`, etc.).
    -   **Scope:** `plans/backlog.md` is for **pxx development only**. Do NOT register meta-tooling work there — i.e., changes to `GEMINI.md` itself, to your own Gemini workflow, or to `../review/*`. Those evolve outside the backlog. If unsure whether your change is "pxx development" vs. "meta-tooling for yourself", ask the user first.
2.  **Autonomous Execution**: Gemini CLI is pre-approved to proceed with implementation once a plan is established and "planned" (or "in-progress"). No separate "Directive" turn is required if the plan is clear.
3.  **Commit and Push**: After verifying changes (tests, linting), commit with a descriptive message and push to the remote repository.
4.  **Review-folder scope**: per `../review/inventory.md`, you own a specific set of files in `../review/`. Refreshes to those files happen there directly and do NOT need a plan in `plans/backlog.md`.

## Engineering Standards

-   **Follow existing conventions**: Refer to `CONVENTIONS.md` and `CLAUDE.md`.
-   **Python Style**: Python 3.11+, type hints on every signature, no `Any`.
-   **Tooling**: Use `uv` for dependency management and running tests/lint.
    -   Lint: `uv run ruff check --fix` and `uv run ruff format`
    -   Test: `uv run pytest`

## Hard Guardrails

Refer to the "Hard guardrails" in `CONVENTIONS.md`. Do not modify configuration files, setup scripts, or metadata files without explicit confirmation.
