"""Tests for ``package/devtools/test_names.py`` — duplicate detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from wills_agentic_tools.checks.test_names import check_test_names


def _write_tests(root: Path, files: tuple[tuple[str, str], ...]) -> Path:
    d = root / "tests"
    d.mkdir()
    for rel, content in files:
        path = d / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return d


class TestCheckTestNames:
    @pytest.mark.parametrize(
        "files",
        [
            (
                ("test_a.py", "def test_foo(): pass\n"),
                ("test_b.py", "def test_bar(): pass\n"),
            ),
            (
                ("test_a.py", "def helper(): pass\ndef test_foo(): pass\n"),
                ("test_b.py", "def helper(): pass\ndef test_bar(): pass\n"),
            ),
        ],
        ids=["no-duplicates", "non-test-functions-ignored"],
    )
    def test_clean_trees_no_violations(
        self, tmp_path: Path, files: tuple[tuple[str, str], ...]
    ) -> None:
        d = _write_tests(tmp_path, files)
        assert check_test_names(d) == []

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            (
                (
                    ("test_a.py", "def test_foo(): pass\n"),
                    ("test_b.py", "def test_foo(): pass\n"),
                ),
                ("test_foo", "2 files"),
            ),
            (
                (
                    ("unit/test_a.py", "def test_foo(): pass\n"),
                    ("test_b.py", "def test_foo(): pass\n"),
                ),
                ("test_foo",),
            ),
            (
                (
                    ("test_a.py", "def test_dup(): pass\n"),
                    ("test_b.py", "def test_dup(): pass\n"),
                    ("test_c.py", "def test_dup(): pass\n"),
                ),
                ("test_dup", "3 files"),
            ),
        ],
        ids=["duplicate-across-two-files", "nested-subdirectories", "duplicate-in-three-files"],
    )
    def test_duplicates_flag_expected(
        self,
        tmp_path: Path,
        files: tuple[tuple[str, str], ...],
        expected: tuple[str, ...],
    ) -> None:
        d = _write_tests(tmp_path, files)
        violations = check_test_names(d)
        assert len(violations) == 1
        assert all(s in violations[0] for s in expected)

    def test_unparseable_file(self, tmp_path: Path) -> None:
        d = _write_tests(tmp_path, (("test_broken.py", "def foo(:\n"),))
        violations = check_test_names(d)
        assert len(violations) == 1
        assert "unparseable" in violations[0]
        assert "test_broken.py" in violations[0]

    def test_invalid_utf8_file_flagged(self, tmp_path: Path) -> None:
        d = tmp_path / "tests"
        d.mkdir()
        (d / "test_bytes.py").write_bytes(b"def test_x():\n    x = '\xff\xfe'\n")
        violations = check_test_names(d)
        assert any("test_bytes.py" in v for v in violations)
        assert any("UnicodeDecodeError" in v for v in violations)
