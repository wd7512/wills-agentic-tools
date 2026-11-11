"""Command line entry point for repository checks."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wills_agentic_tools.checks.changelog_fragment import check_changelog_fragment
from wills_agentic_tools.checks.filename_portability import check_filename_portability
from wills_agentic_tools.checks.secrets import check_secrets
from wills_agentic_tools.checks.test_layout import check_test_layout
from wills_agentic_tools.checks.test_names import check_test_names
from wills_agentic_tools.checks.untracked_paths import check_untracked_paths
from wills_agentic_tools.config import ConfigError, load_tool_config

logger = logging.getLogger(__name__)


def _check_config(repo_root: Path) -> dict[str, Any]:
    raw = load_tool_config(repo_root, "check")
    defaults: dict[str, Any] = {
        "source_package": None,
        "unit_tests": "tests/unit",
        "tests_dir": "tests",
        "mirror_exceptions": [],
        "untracked_paths": [],
        "changelog_dir": "changes",
        "changelog_exempt": ["docs/"],
        "base": "origin/dev",
    }
    expected: dict[str, type | tuple[type, ...]] = {
        "source_package": (str, type(None)),
        "unit_tests": str,
        "tests_dir": str,
        "mirror_exceptions": list,
        "untracked_paths": list,
        "changelog_dir": str,
        "changelog_exempt": list,
        "base": str,
    }
    values = defaults | raw
    for key, accepted in expected.items():
        value = values[key]
        if not isinstance(value, accepted) or (
            isinstance(value, list) and any(not isinstance(item, str) for item in value)
        ):
            label = (
                accepted.__name__
                if isinstance(accepted, type)
                else " or ".join(t.__name__ for t in accepted)
            )
            raise ConfigError(f"[tool.devtools.check].{key} must be {label}")
    return values


def _run_test_names(root: Path, config: dict[str, Any]) -> list[str]:
    return check_test_names(root / config["tests_dir"])


def _run_filename_portability(root: Path, config: dict[str, Any]) -> list[str]:
    return check_filename_portability(root)


def _run_secrets(root: Path, config: dict[str, Any]) -> list[str]:
    return check_secrets(root)


def _run_untracked_paths(root: Path, config: dict[str, Any]) -> list[str]:
    return check_untracked_paths(root, config["untracked_paths"])


def _run_test_layout(root: Path, config: dict[str, Any]) -> list[str]:
    return check_test_layout(
        root,
        config["source_package"],
        config["unit_tests"],
        config["tests_dir"],
        config["mirror_exceptions"],
    )


def _run_changelog_fragment(root: Path, config: dict[str, Any]) -> list[str]:
    return check_changelog_fragment(
        root, config["changelog_dir"], config["changelog_exempt"], config["base"]
    )


_CHECKS: dict[str, Callable[[Path, dict[str, Any]], list[str]]] = {
    "test-names": _run_test_names,
    "filename-portability": _run_filename_portability,
    "secrets": _run_secrets,
    "untracked-paths": _run_untracked_paths,
    "test-layout": _run_test_layout,
    "changelog-fragment": _run_changelog_fragment,
}


def run_checks(repo_root: Path, checks: str) -> int:
    """Run selected checks and return 0, 1, or 2 for clean, violations, or usage/config errors."""
    selected = [name.strip() for name in checks.split(",")]
    invalid = [name for name in selected if not name or name not in _CHECKS]
    if invalid:
        logger.error("invalid --checks value(s): %s", ", ".join(invalid))
        return 2
    root = repo_root.resolve()
    try:
        config = _check_config(root)
        violations = [v for name in selected for v in _CHECKS[name](root, config)]
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    for violation in violations:
        logger.error("%s", violation)
    if violations:
        return 1
    logger.info("all checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the check command."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="check")
    parser.add_argument("--checks", default=",".join(_CHECKS))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    return run_checks(args.repo_root, args.checks)
