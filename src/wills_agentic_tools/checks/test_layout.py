"""Checks test tree placement and source mirroring."""

from __future__ import annotations

from pathlib import Path

_SPECIAL = {"__init__.py", "conftest.py"}


def check_test_layout(
    repo_root: Path,
    source_package: str | None,
    unit_tests: str,
    tests_dir: str,
    mirror_exceptions: list[str],
) -> list[str]:
    """Return configured test layout violations."""
    tests = repo_root / tests_dir
    unit = repo_root / unit_tests
    violations: list[str] = []
    if source_package is None:
        violations.append("source_package is required for test-layout")
        source = repo_root / "__missing_source_package__"
    else:
        source = repo_root / source_package
    for label, path in (("source_package", source), ("unit_tests", unit)):
        if not path.is_dir():
            violations.append(f"{label} directory is missing: {path.relative_to(repo_root)}")
    for path in sorted(tests.glob("*.py")):
        if path.name not in _SPECIAL:
            violations.append(
                f"{path.relative_to(repo_root)}: only __init__.py / conftest.py may live directly under tests/"
            )
    exceptions = set(mirror_exceptions)
    if unit.is_dir():
        for path in sorted(unit.rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if path.name in _SPECIAL:
                continue
            if rel in exceptions:
                continue
            if not path.name.startswith("test_") or not path.name.endswith(".py"):
                violations.append(
                    f"{rel}: only test_<module>.py, __init__.py, or conftest.py may live under unit_tests"
                )
                continue
            stem = path.stem[5:]
            rel_dir = path.relative_to(unit).parent
            if not (
                (source / rel_dir / f"{stem}.py").is_file()
                or (source / rel_dir / stem / "__init__.py").is_file()
            ):
                expected = (source / rel_dir / f"{stem}.py").relative_to(repo_root)
                violations.append(f"{rel}: no mirror source ({expected})")
    return violations
