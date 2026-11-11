"""Tests for ``package/devtools/filename_portability.py``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wills_agentic_tools.checks import filename_portability
from wills_agentic_tools.checks.filename_portability import (
    _violations_for,
    check_filename_portability,
)


class TestViolationsFor:
    @pytest.mark.parametrize(
        "rel",
        [
            "src/mod.py",
            "model_defaults/ollama/beam__colon__cloud.json",
            "openrouter/nvidia/nemotron-3-ultra-550b-a55b__colon__free.json",
            "dir.with.dots/file.name.json",
            "a-b_c+d=e,f%g.json",
            "CONtrol.py",
            "null.json",
            "space inside.py",
        ],
        ids=[
            "plain",
            "encoded-colon",
            "encoded-colon-nested",
            "inner-dots",
            "punctuation",
            "reserved-prefix",
            "reserved-substring",
            "inner-space",
        ],
    )
    def test_clean_names_no_violations(self, rel: str) -> None:
        assert _violations_for(rel) == []

    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("model_defaults/ollama/beam:cloud.json", "U+003A"),
            ("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free.json", "U+003A"),
            ("openrouter/minimax/minimax-m3.1-flash:free.json", "U+003A"),
            ("openrouter/nvidia/nemotron-3.5-super-vl:free.json", "U+003A"),
            ("a<b.json", "U+003C"),
            ("a>b.json", "U+003E"),
            ('a"b.json', "U+0022"),
            ("a|b.json", "U+007C"),
            ("a?b.json", "U+003F"),
            ("a*b.json", "U+002A"),
            ("a\\b.json", "U+005C"),
            ("src/NUL.json", "reserved Windows device name"),
            ("src/nul", "reserved Windows device name"),
            ("src/COM1.json", "reserved Windows device name"),
            ("src/com9.txt", "reserved Windows device name"),
            ("src/LPT1", "reserved Windows device name"),
            ("src/lpt9.json", "reserved Windows device name"),
            ("src/AUX.json", "reserved Windows device name"),
            ("src/PRN.json", "reserved Windows device name"),
            ("src/CON.json", "reserved Windows device name"),
            ("trailingdot./x.py", "ends in space or dot"),
            ("trailspace /x.py", "ends in space or dot"),
            ("x.py ", "ends in space or dot"),
            ("a\x01b.json", "U+0001"),
            ("a\x1fb.json", "U+001F"),
        ],
        ids=[
            "beam-colon",
            "nemotron-ultra-colon",
            "minimax-colon",
            "nemotron-vl-colon",
            "less-than",
            "greater-than",
            "double-quote",
            "pipe",
            "question",
            "star",
            "backslash",
            "nul-upper-ext",
            "nul-bare-lower",
            "com1-upper",
            "com9-lower",
            "lpt1-upper",
            "lpt9-lower",
            "aux-upper",
            "prn-upper",
            "con-upper",
            "trailing-dot-dir",
            "trailing-space-dir",
            "trailing-space-file",
            "control-0x01",
            "control-0x1f",
        ],
    )
    def test_unsafe_names_flag_expected(self, rel: str, expected: str) -> None:
        violations = _violations_for(rel)
        assert violations, rel
        assert any(expected in v and v.startswith(f"{rel}:") for v in violations), violations

    def test_multiple_reasons_all_reported(self) -> None:
        violations = _violations_for("NUL./x.py")
        assert len(violations) >= 2


class TestCheckFilenamePortability:
    def test_real_colon_shape_reports_each_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout = (
            b"src/package/gateway/model_defaults/ollama/beam:cloud.json\x00"
            b"src/package/gateway/model_defaults/openrouter/minimax/minimax-m3.1-flash:free.json\x00"
            b"src/package/gateway/model_defaults/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free.json\x00"
            b"src/package/gateway/model_defaults/openrouter/nvidia/nemotron-3.5-super-vl:free.json\x00"
        )

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args[0], 0, stdout, b"")

        monkeypatch.setattr(filename_portability.subprocess, "run", fake_run)
        violations = check_filename_portability(Path("."))
        assert len(violations) == 4
        assert all("U+003A" in v for v in violations)

    def test_portable_tree_reports_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args[0], 0, b"src/mod.py\x00docs/a.md\x00", b"")

        monkeypatch.setattr(filename_portability.subprocess, "run", fake_run)
        assert check_filename_portability(Path(".")) == []

    def test_git_failure_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args[0], 1, b"", b"not a repo")

        monkeypatch.setattr(filename_portability.subprocess, "run", fake_run)
        violations = check_filename_portability(Path("."))
        assert len(violations) == 1
        assert "cannot verify filename portability" in violations[0]

    def test_git_unavailable_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            raise OSError("no git")

        monkeypatch.setattr(filename_portability.subprocess, "run", fake_run)
        violations = check_filename_portability(Path("."))
        assert len(violations) == 1
        assert "cannot verify filename portability" in violations[0]
