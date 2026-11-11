"""Duplicate-test-name check — one ``test_*`` name defined at most once.

Pure and stdlib-only; ``check_test_names`` returns one violation per name
defined in more than one file (empty list = clean).
"""

from __future__ import annotations

import ast
from pathlib import Path


def _collect_test_names(
    tests_dir: Path,
) -> tuple[dict[str, set[str]], list[str]]:
    """Walk *tests_dir* and collect test function definitions.

    Returns ``(name_to_files, parse_violations)``.
    """
    name_files: dict[str, set[str]] = {}
    violations: list[str] = []

    for py_path in sorted(tests_dir.rglob("*.py")):
        name_files, violations = _scan_file(py_path, tests_dir, name_files, violations)

    return name_files, violations


def _scan_file(
    py_path: Path,
    tests_dir: Path,
    name_files: dict[str, set[str]],
    violations: list[str],
) -> tuple[dict[str, set[str]], list[str]]:
    """Parse one ``.py`` file and collect test function names."""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        violations.append(f"unreadable or unparseable file: {py_path} ({type(exc).__name__})")
        return name_files, violations

    try:
        rel = str(py_path.relative_to(tests_dir.parent.parent))
    except ValueError:
        rel = str(py_path)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            name_files.setdefault(node.name, set()).add(rel)

    return name_files, violations


def check_test_names(tests_dir: Path) -> list[str]:
    """Return one violation per ``test_*`` name defined more than once.

    A ``.py`` file that fails to parse yields exactly one violation naming
    the file.  Non-``test_*`` functions are ignored.
    """
    name_files, violations = _collect_test_names(tests_dir)

    for name, files in sorted(name_files.items()):
        if len(files) > 1:
            violations.append(
                f"duplicate test name '{name}' in {len(files)} files: " + ", ".join(sorted(files))
            )

    return violations
