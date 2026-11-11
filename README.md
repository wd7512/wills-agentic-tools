# wills-agentic-tools

Development tools for repositories that are built by coding agents under one human orchestrator.

| Command | What it does |
|---|---|
| `uv run gate` | Runs format, lint, type check, tests and lock check. Every part runs even after one fails; the exit code is 0 only when all pass. |
| `uv run check` | Repository hygiene checks: test layout, test names, portable file names, secrets, untracked paths, changelog fragment. |
| `uv run delegate` | Starts a worker model in its own git worktree with a brief, runs the gate when it finishes, and sends failures back to the same session. |

Each command reads its settings from `[tool.devtools]` in the calling repository's `pyproject.toml`.

## Use it in another repository

Add it as a development dependency, pinned to a tag:

```toml
[dependency-groups]
dev = ["wills-agentic-tools"]

[tool.uv.sources]
wills-agentic-tools = { git = "https://github.com/wd7512/wills-agentic-tools", tag = "v0.1.0" }
```

## Develop

```sh
uv sync --all-extras --locked
uv run pytest
```

Licensed under Apache-2.0.
