"""Checks that configured generated paths are ignored and untracked."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, check=False)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def check_untracked_paths(repo_root: Path, paths: list[str]) -> list[str]:
    """Return violations for tracked or non-ignored configured paths."""
    violations: list[str] = []
    for raw in paths:
        entry = raw.rstrip("/") + "/"
        try:
            tracked = _git(repo_root, "ls-files", "-z", "--", entry)
            ignored = _git(repo_root, "check-ignore", "-q", "--no-index", entry)
        except OSError as exc:
            violations.append(f"cannot verify {entry}: git unavailable ({exc})")
            continue
        if tracked.returncode:
            detail = _decode(tracked.stderr).strip()
            violations.append(
                f"cannot verify {entry}: git ls-files rc={tracked.returncode} ({detail})"
            )
            continue
        if ignored.returncode == 1:
            violations.append(f"{entry} is not gitignored")
        elif ignored.returncode:
            detail = _decode(ignored.stderr).strip()
            violations.append(
                f"cannot verify {entry} is ignored: git check-ignore rc={ignored.returncode} ({detail})"
            )
        violations.extend(
            f"{p}: tracked file under {entry}"
            for p in (_decode(x) for x in tracked.stdout.split(b"\0"))
            if p
        )
    return violations
