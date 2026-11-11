"""Read a tool's settings from the calling repository's ``pyproject.toml``."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """The calling repository's ``pyproject.toml`` is unreadable or malformed."""


def load_tool_config(repo_root: Path, section: str) -> dict[str, Any]:
    """Return the ``[tool.devtools.<section>]`` table, or ``{}`` when it is absent.

    A missing ``pyproject.toml`` or a missing table is not an error: every
    setting has a default. A file that does not parse, or a ``devtools``
    entry that is not a table, raises ``ConfigError``.
    """
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    table: Any = data
    for key in ("tool", "devtools", section):
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: [tool.devtools.{section}] is not a table")
        table = table.get(key, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [tool.devtools.{section}] is not a table")
    return table
