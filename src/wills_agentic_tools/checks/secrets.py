"""Committed-secret hygiene check.

Pure and stdlib-only; ``check_secrets`` returns violation strings (empty
list = clean).  This check catches provider-shaped credentials that may have been committed: a user
pasted it into a chat session, the phase-4 signal sweep captured the turn
verbatim, and the artifact was committed.  Nothing in the toolchain
objected.  This gate is the objection.

Patterns are provider-anchored and deliberately require a long unbroken
alphanumeric run, because a loose ``sk-`` prefix matches innocent text:
``runs/20260910-reduced-grid-9v10task-FAILED-extract-timeout`` is a run
directory name, not a credential.

Known placeholder values are allowlisted so documentation that *shows* a
key shape stays committable.

Scope: files under the repo tree, excluding generated/vendored directories,
worktree scratch, and binary suffixes. Files over ``MAX_BYTES`` are scanned
through a read-only memory mapping instead of being skipped, so no file is
skipped solely for its size.
Evidence bundles are scanned too:
operator/provider credentials leaking into a committed trajectory are
worth catching. The one scope-based exemption is the generic
``-----BEGIN ... PRIVATE KEY-----`` header under ``evidence/`` — agents
routinely run ``openssl``/``ssh-keygen`` inside a trial, so that pattern
matches ephemeral task keypairs rather than a repo credential. Every skip
is logged, never silent; the pattern stays live everywhere outside
``evidence/``.
"""

from __future__ import annotations

import logging
import mmap
import os
import re
from collections.abc import Iterator
from pathlib import Path

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai-style key", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("github PAT", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github fine-grained PAT", re.compile(r"github_pat_[A-Za-z0-9_]{50,}")),
    ("aws access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("anthropic-style key", re.compile(r"sk-ant-[A-Za-z0-9_-]{24,}")),
    ("ahe gateway key", re.compile(r"ahe_[A-Za-z0-9]{24,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
_PLACEHOLDERS = frozenset(
    {"AKIA1234567890123456", "AKIAIOSFODNN7EXAMPLE", "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"}
)
_EVIDENCE_EXEMPT_LABELS = frozenset({"private key block"})

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".agent",
        ".cache",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".ty_cache",
        ".venv",
        ".worktrees",
        "__pycache__",
        "node_modules",
        "runs",
    }
)

MAX_BYTES = 2 * 1024 * 1024

_BYTE_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = tuple(
    (label, re.compile(pattern.pattern.encode("ascii"), pattern.flags & ~re.UNICODE))
    for label, pattern in _PATTERNS
)

_BYTE_PLACEHOLDERS: frozenset[bytes] = frozenset(value.encode("ascii") for value in _PLACEHOLDERS)

_BINARY_SUFFIXES: frozenset[str] = frozenset(
    {".7z", ".gz", ".jpg", ".jpeg", ".png", ".tar", ".webp", ".woff2", ".zst"}
)


def _should_skip(path: Path, exclude_dirs: frozenset[str], repo_root: Path) -> bool:
    """Decide exclusion from path shape alone; size never excludes."""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    if any(part in exclude_dirs for part in rel.parts[:-1]):
        return True
    return path.suffix.lower() in _BINARY_SUFFIXES


def _iter_repo_files(repo_root: Path, exclude_dirs: frozenset[str]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(repo_root):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in exclude_dirs)
        for name in sorted(filenames):
            candidate = here / name
            if not _should_skip(candidate, exclude_dirs, repo_root):
                yield candidate


def _is_evidence_path(path: Path, repo_root: Path) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == "evidence"


def _redact(match: str) -> str:
    """Keep enough of the shape to locate the value, drop the rest."""
    if len(match) <= 12:
        return "<redacted>"
    return f"{match[:8]}...{match[-2:]} (len={len(match)})"


def _scan_text(text: str, path: Path, repo_root: Path, *, in_evidence: bool = False) -> list[str]:
    found: list[str] = []
    try:
        shown = path.relative_to(repo_root)
    except ValueError:
        shown = path
    for label, pattern in _PATTERNS:
        if in_evidence and label in _EVIDENCE_EXEMPT_LABELS:
            skipped = sum(1 for _ in pattern.finditer(text))
            if skipped:
                logger.warning(
                    "secret scan: skipping %d %s match(es) under evidence/ "
                    "(trial-generated keypairs): %s",
                    skipped,
                    label,
                    shown,
                )
            continue
        for match in pattern.finditer(text):
            if match.group(0) in _PLACEHOLDERS:
                continue
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{shown}:{line}: possible {label}: {_redact(match.group(0))}")
    return found


def _scan_large_file(path: Path, repo_root: Path, *, in_evidence: bool = False) -> list[str]:
    """Scan one over-limit file through a read-only memory mapping.

    Returns the same violation strings ``_scan_text`` would return for the
    whole text, in the same pattern-major order: byte versions of the
    provider patterns run directly over the mapping, so a match of any
    length anywhere is found exactly once, with no chunks, no overlap, no
    carry, and no cap. Memory stays bounded because the operating system
    pages the mapping instead of holding the file in the heap. Line numbers
    count newlines incrementally between successive match offsets. Only
    each matched span is decoded (with replacement) for redaction, so
    undecodable bytes elsewhere can neither hide a match nor fail the
    scan. An empty or unreadable file yields nothing, matching the small
    undecodable-file behaviour of yielding no violations. A failure
    mid-scan logs the path and reason and returns the violations found so
    far. A file truncated after the mapping is created can raise SIGBUS
    instead of a catchable error; no signal handler is installed, so
    concurrent mutation of scanned trees stays out of scope.
    """
    try:
        shown = path.relative_to(repo_root)
    except ValueError:
        shown = path
    found: list[tuple[int, int, str]] = []
    skipped: dict[str, int] = {}
    try:
        with path.open("rb") as handle:
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                hits: list[tuple[int, int, bytes]] = []
                try:
                    for pattern_index, (label, pattern) in enumerate(_BYTE_PATTERNS):
                        if in_evidence and label in _EVIDENCE_EXEMPT_LABELS:
                            count = sum(1 for _ in pattern.finditer(data))
                            if count:
                                skipped[label] = skipped.get(label, 0) + count
                            continue
                        for match in pattern.finditer(data):
                            if match.group(0) in _BYTE_PLACEHOLDERS:
                                continue
                            hits.append((pattern_index, match.start(), match.group(0)))
                except (OSError, ValueError) as exc:
                    logger.warning(
                        "secret scan: large file scan failed, reporting partial results: %s: %s",
                        shown,
                        exc,
                    )
                line_by_offset: dict[int, int] = {}
                line = 1
                prev = 0
                for _, offset, _ in sorted(hits, key=lambda hit: hit[1]):
                    pos = prev
                    while (nl := data.find(b"\n", pos, offset)) != -1:
                        line += 1
                        pos = nl + 1
                    prev = offset
                    line_by_offset[offset] = line
                for pattern_index, offset, raw in hits:
                    label = _BYTE_PATTERNS[pattern_index][0]
                    text = raw.decode("utf-8", errors="replace")
                    found.append(
                        (
                            pattern_index,
                            offset,
                            f"{shown}:{line_by_offset[offset]}: possible {label}: {_redact(text)}",
                        )
                    )
    except (OSError, ValueError):
        return []
    for label, count in skipped.items():
        logger.warning(
            "secret scan: skipping %d %s match(es) under evidence/ (trial-generated keypairs): %s",
            count,
            label,
            shown,
        )
    found.sort()
    return [text for _, _, text in found]


def check_secrets(repo_root: Path) -> list[str]:
    """Return one violation string per suspected credential; empty = clean."""
    violations: list[str] = []
    for path in _iter_repo_files(repo_root, DEFAULT_EXCLUDE_DIRS):
        try:
            oversized = path.stat().st_size > MAX_BYTES
        except OSError:
            continue
        if oversized:
            violations.extend(
                _scan_large_file(
                    path,
                    repo_root,
                    in_evidence=_is_evidence_path(path, repo_root),
                )
            )
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        violations.extend(
            _scan_text(
                text,
                path,
                repo_root,
                in_evidence=_is_evidence_path(path, repo_root),
            )
        )
    return violations
