"""Requires a changelog fragment for non-exempt branch changes."""

from __future__ import annotations

import subprocess
from pathlib import Path


def check_changelog_fragment(
    repo_root: Path, changelog_dir: str, changelog_exempt: list[str], base: str
) -> list[str]:
    """Return a violation when branch changes lack a new changelog fragment."""
    directory = changelog_dir.strip("/")
    try:
        added = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "diff",
                "--name-only",
                "--diff-filter=A",
                f"{base}...HEAD",
            ],
            capture_output=True,
            check=False,
        )
        changed = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return [f"cannot verify changelog fragment: git unavailable ({exc})"]
    for result in (added, changed):
        if result.returncode:
            return [f"cannot verify changelog fragment: {_decode(result.stderr)}"]
    added_paths = _decode(added.stdout).splitlines()
    changed_paths = _decode(changed.stdout).splitlines()
    if not changed_paths or any(
        p.startswith(f"{directory}/") and "/" not in p[len(directory) + 1 :] for p in added_paths
    ):
        return []
    prefixes = tuple(changelog_exempt)
    if all(any(p.startswith(prefix) for prefix in prefixes) for p in changed_paths):
        return []
    return [f"branch changes require a new fragment under {directory}/"]


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape").strip()
