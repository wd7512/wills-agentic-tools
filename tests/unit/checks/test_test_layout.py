from pathlib import Path

from wills_agentic_tools.checks.test_layout import check_test_layout


def _check(
    root: Path, source: str | None = "src/pkg", exceptions: list[str] | None = None
) -> list[str]:
    return check_test_layout(root, source, "tests/unit", "tests", exceptions or [])


def test_unmirrored_test_fails(tmp_path: Path) -> None:
    source = tmp_path / "src" / "pkg"
    source.mkdir(parents=True)
    unit = tmp_path / "tests" / "unit"
    unit.mkdir(parents=True)
    test = unit / "test_missing.py"
    test.write_text("", encoding="utf-8")
    assert _check(tmp_path)


def test_mirror_exception_skips_test(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_missing.py").touch()
    assert _check(tmp_path, exceptions=["tests/unit/test_missing.py"]) == []


def test_package_init_mirror_is_clean(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg" / "subpkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "subpkg" / "__init__.py").touch()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_subpkg.py").touch()
    assert _check(tmp_path) == []


def test_missing_source_package_setting_is_violation(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    assert _check(tmp_path, source=None)


def test_only_two_mirror_forms_are_accepted(tmp_path: Path) -> None:
    source = tmp_path / "src" / "pkg" / "module"
    source.mkdir(parents=True)
    (source / "__init__.py").touch()
    unit = tmp_path / "tests" / "unit" / "module"
    unit.mkdir(parents=True)
    (unit / "test_module.py").touch()
    result = _check(tmp_path)
    assert any("no mirror source" in item for item in result)


def test_clean_layouts_no_violations(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg" / "gateway").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "foo.py").touch()
    (tmp_path / "src" / "pkg" / "gateway" / "server.py").touch()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").touch()
    (tmp_path / "tests" / "conftest.py").touch()
    (tmp_path / "tests" / "unit" / "gateway").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_foo.py").touch()
    (tmp_path / "tests" / "unit" / "gateway" / "test_server.py").touch()
    (tmp_path / "tests" / "unit" / "conftest.py").touch()
    assert _check(tmp_path) == []


def test_violation_layouts_flag_expected(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_loose.py").touch()
    (tmp_path / "tests" / "unit").mkdir()
    (tmp_path / "tests" / "unit" / "test_orphan.py").touch()
    (tmp_path / "tests" / "unit" / "helper.py").touch()
    violations = _check(tmp_path)
    assert any("test_orphan.py" in value and "no mirror source" in value for value in violations)
    assert any("helper.py" in value for value in violations)
    assert any("test_loose.py" in value for value in violations)


def test_source_module_without_mirrored_test_is_reported(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg" / "gateway").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "gateway" / "server.py").touch()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    violations = _check(tmp_path)
    assert any(
        "src/pkg/gateway/server.py" in value and "tests/unit/gateway/test_server.py" in value
        for value in violations
    )


def test_source_module_with_mirrored_test_is_clean(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg" / "gateway").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "gateway" / "server.py").touch()
    (tmp_path / "tests" / "unit" / "gateway").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "gateway" / "test_server.py").touch()
    assert _check(tmp_path) == []


def test_excepted_source_module_is_clean(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "cli.py").touch()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    assert _check(tmp_path, exceptions=["src/pkg/cli.py"]) == []


def test_entry_points_require_an_explicit_exception(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "cli.py").touch()
    (tmp_path / "src" / "pkg" / "__main__.py").touch()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    assert any("src/pkg/cli.py" in value for value in _check(tmp_path))
    assert any("src/pkg/__main__.py" in value for value in _check(tmp_path))
    assert _check(tmp_path, exceptions=["src/pkg/cli.py", "src/pkg/__main__.py"]) == []


def test_test_path_exception_does_not_hide_missing_source_mirror(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "module.py").touch()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_other.py").touch()
    violations = _check(tmp_path, exceptions=["tests/unit/test_other.py"])
    assert any(
        "src/pkg/module.py" in value and "tests/unit/test_module.py" in value
        for value in violations
    )


def test_package_init_without_unit_mirror_is_clean(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg" / "subpkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "subpkg" / "__init__.py").touch()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    assert _check(tmp_path) == []


def test_missing_required_roots_are_flagged(tmp_path: Path) -> None:
    violations = _check(tmp_path)
    assert any("source_package directory is missing" in value for value in violations)
    assert any("unit_tests directory is missing" in value for value in violations)


def test_present_required_roots_are_clean(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    assert _check(tmp_path) == []


def test_exception_layout_is_clean(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_architecture.py").touch()
    assert (
        check_test_layout(
            tmp_path,
            "src/pkg",
            "tests/unit",
            "tests",
            ["tests/unit/test_architecture.py"],
        )
        == []
    )
