# wills-agentic-tools

Development tools for repositories that are built by coding agents under one human orchestrator.

| Command | What it does |
|---|---|
| `uv run gate` | Runs format, lint, type check, tests and lock check. Every part runs even after one fails; the exit code is 0 only when all pass. |
| `uv run check` | Repository hygiene checks: test layout, test names, portable file names, secrets, untracked paths, changelog fragment. |
| `uv run delegate` | Starts a worker model in its own git worktree with a brief, runs the gate when it finishes, and sends failures back to the same session. |

Each command reads its settings from `[tool.devtools]` in the calling repository's `pyproject.toml`.

## Test layout

`uv run check --checks test-layout` reads `[tool.devtools.check]` and enforces a mirrored test tree in both directions:

- every source module such as `src/demo/sub/module.py` needs `tests/unit/sub/test_module.py`;
- every unit test such as `tests/unit/sub/test_module.py` needs `src/demo/sub/module.py` (or the package form `src/demo/sub/module/__init__.py`);
- `__init__.py` files under `source_package` are not required to have a unit mirror.

```toml
[tool.devtools.check]
source_package = "src/demo"
unit_tests = "tests/unit"
mirror_exceptions = ["src/demo/cli.py", "tests/unit/test_architecture.py"]
```

`mirror_exceptions` entries are repository-relative paths in one of two forms:

- a source module path, such as `src/demo/cli.py`, which exempts that module from needing a mirrored test;
- a unit-test path, such as `tests/unit/test_architecture.py`, which exempts that test from needing a mirrored source.

A test-path exception only exempts that test. It does not exempt source modules, so entry points such as `cli.py` or `__main__.py` must be listed by their source path.

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
