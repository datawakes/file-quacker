# Coding Conventions

These conventions are meant to keep contributions consistent without making the project feel heavy. When in doubt, match the surrounding code and keep changes small.

## General Guidelines

- Keep changes focused. Fix the thing you came to fix; avoid unrelated cleanup or refactoring in the same PR.
- Prefer simple, readable code over clever abstractions.
- Ask or open an issue when behavior, schema, or scope is unclear.
- Read the existing code before adding new patterns.
- Add comments only when they explain a non-obvious reason. Do not comment what the code already says clearly.
- Remove unused imports, dead helpers, and temporary debug code created by your change.
- Include tests or a clear manual verification note when practical.

## Python

- Follow the structure already used in the project.
- Keep functions small and direct.
- Use descriptive names instead of explanatory comments.
- Use existing error types and logging patterns where they apply.
- Avoid broad exception handling unless there is a clear reason.
- Do not add new dependencies casually. If a dependency is needed, explain why in the PR.

## SQL

- Use lowercase SQL keywords and identifiers.
- Use 4 spaces for indentation.
- Use leading commas in `select` lists.
- Schema-qualify table names.
- Use `?` parameters instead of string interpolation.
- Python t-strings are also acceptable when used through a library/helper that safely converts interpolated values into bound parameters. The project currently targets Python 3.11, so we are not using them yet.
- End statements with a semicolon.
- Use `cast()` for type conversions.
- Do not guess column names. Check the table definition first.
- `top` queries must include an `order by` when row choice matters.

## Frontend

- Match the existing Vue and component patterns.
- Buttons inside forms should use `type="button"` unless they submit the form.
- Keep dialogs predictable: one primary Save action should save the whole dialog.
- Do not hide meaningful errors from users.
- Avoid UI behavior that refreshes or blanks a table unnecessarily during small inline updates.

## Configuration

- Do not hard-code values that are likely to vary by environment.
- Do not commit secrets, local paths, credentials, tokens, or personal test files.

## Logging

- Log important state changes, external calls, and failures.
- Keep noisy per-row or per-step details at debug level.
- Use stack traces only when they help diagnose the problem.

## Pull Requests

A good PR should include:

- What changed.
- Why it changed.
- How it was tested.
- Any follow-up work or known limitations.

Small, boring PRs are preferred. They are easier to review and less likely to break unrelated behavior.