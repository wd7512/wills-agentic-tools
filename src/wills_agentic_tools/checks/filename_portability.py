"""Windows filename-portability check — no NTFS-illegal tracked names.

Pure and stdlib-only; ``check_filename_portability`` returns violation
strings (empty list = clean). Added after four ``model_defaults`` entries
carried ``:`` in their filenames (OpenRouter/Ollama ``vendor/model:tag``
slugs mapped verbatim to paths), which ``core.protectNTFS`` refuses to
check out — every Windows clone of the repo broke. The content scanners
never saw it: the bytes inside were clean, the names were not.

The check enumerates ``git ls-files -z`` and fails closed when git cannot
answer, so untracked scratch never trips it and a blind repo never passes
it. It enforces the full Windows set, not just the colon that bit us:
``< > : " / \\ | ? *``, C0 controls in names, per-segment reserved device
names (CON PRN AUX NUL COM1-9 LPT1-9, any extension, any case), and
segments ending in space or dot.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

_ILLEGAL_CHARS: frozenset[str] = frozenset('<>:"/\\|?*')

_RESERVED_STEMS: frozenset[str] = (
    frozenset({"CON", "PRN", "AUX", "NUL"})
    | frozenset(f"COM{i}" for i in range(1, 10))
    | frozenset(f"LPT{i}" for i in range(1, 10))
)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def _violations_for(rel: str) -> list[str]:
    """Return one violation per Windows-illegal property of a tracked path."""
    reasons: list[str] = []
    for segment in PurePosixPath(rel).parts:
        for char in sorted({ch for ch in segment if ch in _ILLEGAL_CHARS or ord(ch) < 32}):
            reasons.append(f"illegal character U+{ord(char):04X} in segment {segment!r}")
        if segment.split(".")[0].upper() in _RESERVED_STEMS:
            reasons.append(f"reserved Windows device name in segment {segment!r}")
        if segment.endswith((" ", ".")):
            reasons.append(f"segment {segment!r} ends in space or dot")
    return [f"{rel}: Windows-unsafe filename ({reason})" for reason in reasons]


def check_filename_portability(repo_root: Path) -> list[str]:
    """Return violation strings for tracked paths under *repo_root*.

    Every ``git ls-files`` entry must survive a Windows checkout. A repo
    git cannot enumerate fails closed: no answer is a violation, not a skip.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return [f"cannot verify filename portability: git unavailable ({exc})"]
    if result.returncode != 0:
        detail = _decode(result.stderr).strip()
        return [
            f"cannot verify filename portability: git ls-files rc={result.returncode} ({detail})"
        ]
    violations: list[str] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        violations.extend(_violations_for(_decode(raw_path)))
    return violations
