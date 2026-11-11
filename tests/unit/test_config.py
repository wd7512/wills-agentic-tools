from pathlib import Path

import pytest

from wills_agentic_tools.config import ConfigError, load_tool_config


def _write(root: Path, text: str) -> None:
    (root / "pyproject.toml").write_text(text, encoding="utf-8")


def test_section_table_is_returned(tmp_path: Path):
    _write(tmp_path, '[tool.devtools.gate]\nsource = "src/"\ntests = ["tests/unit"]\n')
    assert load_tool_config(tmp_path, "gate") == {"source": "src/", "tests": ["tests/unit"]}


def test_missing_pyproject_gives_empty_config(tmp_path: Path):
    assert load_tool_config(tmp_path, "gate") == {}


def test_missing_section_gives_empty_config(tmp_path: Path):
    _write(tmp_path, '[project]\nname = "x"\n')
    assert load_tool_config(tmp_path, "gate") == {}


def test_other_sections_are_not_returned(tmp_path: Path):
    _write(tmp_path, '[tool.devtools.check]\nbase = "origin/dev"\n')
    assert load_tool_config(tmp_path, "gate") == {}


def test_unparseable_pyproject_is_an_error(tmp_path: Path):
    _write(tmp_path, "[tool.devtools.gate\n")
    with pytest.raises(ConfigError, match="cannot read"):
        load_tool_config(tmp_path, "gate")


def test_section_that_is_not_a_table_is_an_error(tmp_path: Path):
    _write(tmp_path, '[tool.devtools]\ngate = "uv run pytest"\n')
    with pytest.raises(ConfigError, match="not a table"):
        load_tool_config(tmp_path, "gate")
