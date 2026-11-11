import subprocess
from pathlib import Path

from wills_agentic_tools.checks.changelog_fragment import check_changelog_fragment


def _check(root: Path, base: str = "dev") -> list[str]:
    return check_changelog_fragment(root, "changes", ["docs/"], base)


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "dev", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "A"], check=True)
    (path / "src").mkdir()
    (path / "src" / "x.py").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "base"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "checkout", "-b", "work"], check=True, capture_output=True
    )


def test_added_fragment_is_clean(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "12.feature.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "fragment"], check=True, capture_output=True
    )
    assert _check(tmp_path) == []


def test_source_only_change_requires_fragment(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "src" / "x.py").write_text("changed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-am", "source"], check=True, capture_output=True
    )
    assert _check(tmp_path)


def test_docs_only_change_is_exempt(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("docs\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "docs"], check=True, capture_output=True
    )
    assert _check(tmp_path) == []


def test_existing_fragment_change_does_not_count_as_added(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "dev", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "A"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "old.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "work"], check=True, capture_output=True
    )
    (tmp_path / "changes" / "old.md").write_text("changed\n", encoding="utf-8")
    (tmp_path / "src" / "x.py").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "change"], check=True, capture_output=True
    )
    assert _check(tmp_path)


def test_unknown_base_returns_one_violation(tmp_path: Path) -> None:
    assert len(_check(tmp_path, "missing-base")) == 1
