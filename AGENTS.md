# Agent instructions

- Use `uv` for everything: `uv sync --all-extras --locked`, `uv run pytest`, `uv run ruff format`, `uv run ruff check`, `uv run ty check src/`. No pip, no direct `python`.
- Work only in your worktree. One change, one commit. Do not push and do not use `gh`.
- Tests first: write the test, see it fail for the expected reason, then write the code.
- Assert on outputs (return values, files, exit codes), not on which internal functions were called.
- No comments in code unless asked. Docstrings on public functions are fine. Log with `logging`; `print` only for a command's final output.
- Do not add a dependency unless the brief names it.
- Never pipe a gate or test command through `tail` or `head`; report the real exit code.
