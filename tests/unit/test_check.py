from pathlib import Path

from wills_agentic_tools.check import main


def test_unknown_check_returns_usage_error() -> None:
    assert main(["--checks", "unknown"]) == 2


def test_failing_check_does_not_stop_later_checks(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_one.py").write_text(
        "def test_duplicate(): pass\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "nested").mkdir()
    (tmp_path / "tests" / "nested" / "test_two.py").write_text(
        "def test_duplicate(): pass\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.devtools.check]\ntests_dir = "tests"\n', encoding="utf-8"
    )
    assert main(["--repo-root", str(tmp_path), "--checks", "test-names,filename-portability"]) == 1


def test_invalid_config_types_return_two(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.devtools.check]\nsource_package = 9\nunit_tests = "tests/unit"\n', encoding="utf-8"
    )
    assert main(["--repo-root", str(tmp_path), "--checks", "test-layout"]) == 2


def test_empty_check_name_returns_two() -> None:
    assert main(["--checks", "test-names,"]) == 2


def test_later_checks_run_after_git_failure(tmp_path: Path) -> None:
    from wills_agentic_tools.checks.untracked_paths import check_untracked_paths

    (tmp_path / "not-repo").mkdir()
    violations = check_untracked_paths(tmp_path / "not-repo", ["first/", "second/"])
    assert len(violations) == 2
    assert "first/" in violations[0]
    assert "second/" in violations[1]


def test_clean_returns_zero(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert main(["--repo-root", str(tmp_path), "--checks", "test-names"]) == 0


def test_violations_return_one(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "one.py").write_text("def test_dup(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "two.py").write_text("def test_dup(): pass\n", encoding="utf-8")
    assert main(["--repo-root", str(tmp_path), "--checks", "test-names"]) == 1


def test_invalid_checks_return_two(tmp_path: Path) -> None:
    assert main(["--repo-root", str(tmp_path), "--checks", "bogus"]) == 2


def test_subset_filter_ignores_unselected(tmp_path: Path) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_orphan.py").write_text("", encoding="utf-8")
    assert main(["--repo-root", str(tmp_path), "--checks", "secrets"]) == 0


def test_clean_logs_info(tmp_path: Path, caplog) -> None:
    import logging

    (tmp_path / "tests").mkdir()
    with caplog.at_level(logging.INFO):
        assert main(["--repo-root", str(tmp_path), "--checks", "test-names"]) == 0
    assert "all checks passed" in caplog.text


def test_violations_logged_as_errors(tmp_path: Path, caplog) -> None:
    import logging

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "one.py").write_text("def test_dup(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "two.py").write_text("def test_dup(): pass\n", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert main(["--repo-root", str(tmp_path), "--checks", "test-names"]) == 1
    assert "duplicate test name" in caplog.text


def test_wrong_types_for_each_config_key_return_two(tmp_path: Path) -> None:
    invalid_values = {
        "source_package": "true",
        "unit_tests": "true",
        "tests_dir": "true",
        "mirror_exceptions": "true",
        "untracked_paths": "true",
        "changelog_dir": "true",
        "changelog_exempt": "true",
        "base": "true",
    }
    for key, value in invalid_values.items():
        (tmp_path / "pyproject.toml").write_text(
            f"[tool.devtools.check]\n{key} = {value}\n", encoding="utf-8"
        )
        assert main(["--repo-root", str(tmp_path), "--checks", "test-names"]) == 2


def test_non_string_config_list_item_returns_two(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.devtools.check]\nuntracked_paths = ["runs/", 3]\n', encoding="utf-8"
    )
    assert main(["--repo-root", str(tmp_path), "--checks", "untracked-paths"]) == 2


def test_default_checks_include_the_six_supported_names() -> None:
    from wills_agentic_tools.check import _CHECKS

    assert set(_CHECKS) == {
        "test-names",
        "filename-portability",
        "secrets",
        "untracked-paths",
        "test-layout",
        "changelog-fragment",
    }


def test_main_dispatches_check(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.devtools.check]\nsource_package = "src/pkg"\n', encoding="utf-8"
    )
    assert main(["--repo-root", str(tmp_path), "--checks", "test-names,test-layout"]) == 0


def test_validate_checks_valid(tmp_path: Path) -> None:
    assert main(["--repo-root", str(tmp_path), "--checks", "test-names, secrets"]) == 0


def test_validate_checks_invalid(tmp_path: Path) -> None:
    assert main(["--repo-root", str(tmp_path), "--checks", "bogus"]) == 2


def test_default_checks_carry_the_layout_gate() -> None:
    from wills_agentic_tools.check import _CHECKS

    assert "test-layout" in _CHECKS


def test_default_checks_carry_the_portability_gate() -> None:
    from wills_agentic_tools.check import _CHECKS

    assert "filename-portability" in _CHECKS
