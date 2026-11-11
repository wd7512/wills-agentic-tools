"""Tests for configured untracked output paths."""

from __future__ import annotations

import subprocess
from pathlib import Path

from wills_agentic_tools.checks.untracked_paths import check_untracked_paths


def _repo(path: Path, *, ignored: bool = True) -> Path:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "A"], check=True)
    if ignored:
        (path / ".gitignore").write_text("runs/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
        subprocess.run(
            ["git", "-C", str(path), "commit", "-m", "ignore"], check=True, capture_output=True
        )
    return path


def test_clean_repo_is_clean(tmp_path: Path) -> None:
    assert check_untracked_paths(_repo(tmp_path), ["runs/"]) == []


def test_staged_force_added_file_is_violation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo / "runs" / "run-1" / "provenance.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "runs/run-1/provenance.json"], check=True)
    result = check_untracked_paths(repo, ["runs/"])
    assert len(result) == 1
    assert "runs/run-1/provenance.json" in result[0]


def test_untracked_but_unignored_runs_is_violation(tmp_path: Path) -> None:
    result = check_untracked_paths(_repo(tmp_path, ignored=False), ["runs/"])
    assert len(result) == 1
    assert "not gitignored" in result[0]


def test_non_git_dir_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "not-repo").mkdir()
    result = check_untracked_paths(tmp_path / "not-repo", ["runs/"])
    assert len(result) == 1
    assert "cannot verify" in result[0]


def test_multiple_configured_paths_report_tracked_and_not_ignored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("runs/\ntracked/\n", encoding="utf-8")
    (repo / "tracked").mkdir()
    (repo / "tracked" / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "tracked/x.txt", ".gitignore"], check=True)
    result = check_untracked_paths(repo, ["tracked/", "runs/", "unignored/"])
    assert any("tracked/x.txt" in v for v in result)
    assert any("unignored/" in v for v in result)


def test_empty_paths_are_clean() -> None:
    assert check_untracked_paths(Path("."), []) == []


def test_dispatch_clean_is_zero(tmp_path: Path) -> None:
    from wills_agentic_tools.check import run_checks

    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[tool.devtools.check]\nuntracked_paths = ["runs/"]\n', encoding="utf-8"
    )
    assert run_checks(repo, "untracked-paths") == 0


def test_dispatch_violation_is_one(tmp_path: Path) -> None:
    from wills_agentic_tools.check import run_checks

    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    (repo / "runs" / "x.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "runs/x.json"], check=True)
    (repo / "pyproject.toml").write_text(
        '[tool.devtools.check]\nuntracked_paths = ["runs/"]\n', encoding="utf-8"
    )
    assert run_checks(repo, "untracked-paths") == 1


def test_two_configured_paths_report_only_tracked_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("tracked/\nclean/\n", encoding="utf-8")
    (repo / "tracked").mkdir()
    (repo / "tracked" / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "tracked/x.txt", ".gitignore"], check=True)
    violations = check_untracked_paths(repo, ["tracked/", "clean/"])
    assert len(violations) == 1
    assert "tracked/x.txt" in violations[0]
