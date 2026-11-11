from __future__ import annotations

import logging
import tracemalloc
from pathlib import Path

import pytest

from wills_agentic_tools.checks.secrets import check_secrets

# Built from fragments on purpose: this file is scanned by the very gate it
# tests, so a contiguous literal would fail ``dev-tools check`` forever.
_SK = "sk-"
_TAIL_A = "n0j2ygOKbTddn7FSBFmDembpnZbLnYX0YYyaDkGyKnUTKS7aTtThng3m"
_TAIL_B = "Lpx8gmax"
LIVE_KEY = _SK + _TAIL_A + _TAIL_B

_JWT = (
    "eyJhbGciOiJIUzI1NiJ9"
    + "."
    + "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    + "."
    + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
_PEM = "-----BEGIN RSA " + "PRIVATE KEY-----"
_SLACK = "xoxb-" + "1234567890-abcdefghijkl"
_GHP_EXAMPLE = "ghp_" + "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def test_clean_tree_has_no_violations(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"notes.md": "nothing secret here\n"})
    assert check_secrets(root) == []


def test_a_live_key_is_reported_with_its_location(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {"workers/turns.txt": f"first line\nthe new key : {LIVE_KEY} : go\n"},
    )
    violations = check_secrets(root)
    assert len(violations) == 1
    assert "workers/turns.txt:2" in violations[0]


def test_the_report_never_echoes_the_whole_secret(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.txt": f"key={LIVE_KEY}\n"})
    (violation,) = check_secrets(root)
    assert LIVE_KEY not in violation
    assert f"len={len(LIVE_KEY)}" in violation


def test_a_run_directory_naming_a_failure_is_not_a_credential(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {"WORKLOG.md": "runs/20260910-reduced-grid-9v10task-FAILED-extract-timeout\n"},
    )
    assert check_secrets(root) == []


@pytest.mark.parametrize(
    "body",
    ["AKIA1234567890123456", "AKIAIOSFODNN7EXAMPLE", _GHP_EXAMPLE],
)
def test_known_placeholder_keys_stay_committable(tmp_path: Path, body: str) -> None:
    root = _tree(tmp_path, {"example.env": f"{body}\n"})
    assert check_secrets(root) == []


def test_example_pat_in_an_evidence_transcript_is_not_a_violation(tmp_path: Path) -> None:
    """A trial agent grepped with the GitHub docs example PAT and the turn was
    captured verbatim into a committed bundle (); the value is the
    allowlisted placeholder, so the gate must not fail on it."""
    line = '{"i":16,"obs":"' + _GHP_EXAMPLE + ' -> 0 occurrences\\n"}\n'
    root = _tree(
        tmp_path,
        {"evidence/bundle/k3-step5/cells.jsonl": line, "evidence/bundle/sources/harbor.json": line},
    )
    assert check_secrets(root) == []


def test_allowlist_is_value_scoped_not_pattern_scoped(tmp_path: Path) -> None:
    """Allowlisting the example PAT must not blanket-exempt the ghp_ shape:
    any other token of that shape in the same evidence bundle still fails."""
    body = f"needle {_GHP_EXAMPLE}\nleaked ghp_{'a' * 36}\n"
    root = _tree(tmp_path, {"evidence/bundle/cells.jsonl": body})
    violations = check_secrets(root)
    assert len(violations) == 1
    assert "evidence/bundle/cells.jsonl:2" in violations[0]


@pytest.mark.parametrize(
    "body",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 60,
        "AKIA" + "Z" * 16,
        "AIza" + "c" * 35,
        _SLACK,
        "sk-ant-" + "d" * 40,
        "ahe_" + "e" * 32,
        _JWT,
        _PEM,
    ],
)
def test_other_provider_shapes_are_caught(tmp_path: Path, body: str) -> None:
    root = _tree(tmp_path, {"creds.txt": f"{body}\n"})
    assert len(check_secrets(root)) == 1


def test_generated_and_vendored_directories_are_skipped(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "runs/k3-x/provenance.json": f'{{"k":"{LIVE_KEY}"}}\n',
            ".venv/lib/thing.py": f'KEY = "{LIVE_KEY}"\n',
        },
    )
    assert check_secrets(root) == []


def test_evidence_provider_credential_is_caught(tmp_path: Path) -> None:
    """Evidence bundles are committed; a real key landing in one must fail ()."""
    root = _tree(tmp_path, {"evidence/bundle/cells.jsonl": f'{{"k":"{LIVE_KEY}"}}\n'})
    violations = check_secrets(root)
    assert len(violations) == 1
    assert "evidence/bundle/cells.jsonl:1" in violations[0]


def test_evidence_private_key_block_is_exempt_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A trial-generated keypair is not a repo credential; the skip is logged ()."""
    root = _tree(tmp_path, {"evidence/bundle/sources/harbor.json": f'{{"obs":"{_PEM}"}}\n'})
    with caplog.at_level(logging.WARNING):
        assert check_secrets(root) == []
    assert "skipping 1 private key block match(es) under evidence/" in caplog.text


def test_private_key_block_outside_evidence_still_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"workers/rekey.txt": f"{_PEM}\n"})
    assert len(check_secrets(root)) == 1


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "x.png").write_bytes(LIVE_KEY.encode())
    assert check_secrets(root) == []


def test_file_over_two_mib_with_key_past_the_mark_is_reported(tmp_path: Path) -> None:
    """Large in-scope files are streamed, never silently skipped ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    pad = "q" * (MAX_BYTES + 64)
    root = _tree(tmp_path, {"notes/big.txt": f"{pad}\nplanted {LIVE_KEY} here\n"})
    violations = check_secrets(root)
    assert len(violations) == 1
    assert "notes/big.txt:2" in violations[0]


def test_large_file_violations_keep_pattern_major_order(tmp_path: Path) -> None:
    """Over-limit ordering matches whole-text order: pattern-major ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    ghp_key = "ghp_" + "z" * 36
    head = "q" * 1000 + ghp_key + "\n"
    middle = "q" * (512 * 1024)
    tail_len = MAX_BYTES - len(head) - len(middle) - len(LIVE_KEY) - 8
    body = head + middle + "planted " + LIVE_KEY + " here\n" + "q" * tail_len + "\n"
    root = _tree(tmp_path, {"notes/order.txt": body})
    violations = check_secrets(root)
    assert len(violations) == 2
    assert "possible openai-style key" in violations[0]
    assert "possible github PAT" in violations[1]


def test_large_multiline_file_reports_exact_lines_across_chunks(tmp_path: Path) -> None:
    """Running line numbers hold across consumed chunks on JSONL shapes ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    def record(i: int, content: str) -> str:
        line = f'{{"i":{i:06d},"obs":"{content}"}}'
        return line + "q" * (127 - len(line)) + "\n"

    line_b = 2049
    line_a = 17000
    total = 17500
    assert total * 128 > MAX_BYTES
    lines = []
    for i in range(1, total + 1):
        if i in (line_a, line_b):
            lines.append(record(i, "planted " + LIVE_KEY + " here"))
        else:
            lines.append(record(i, "filler"))
    root = _tree(tmp_path, {"notes/many.jsonl": "".join(lines)})
    violations = check_secrets(root)
    assert len(violations) == 2
    assert f"notes/many.jsonl:{line_b}:" in violations[0]
    assert f"notes/many.jsonl:{line_a}:" in violations[1]


def test_large_file_undecodable_tail_does_not_hide_key(tmp_path: Path) -> None:
    """Bytes scanning reports the key despite undecodable bytes elsewhere ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    head = ("planted " + LIVE_KEY + " here\n").encode()
    pad_len = MAX_BYTES + 64 - len(head) - 1
    target = tmp_path / "notes" / "broken.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(head + b"q" * pad_len + b"\xff" + b"\n")
    violations = check_secrets(tmp_path)
    assert len(violations) == 1
    assert "notes/broken.txt:1" in violations[0]


def test_agent_scratch_files_are_not_scanned(tmp_path: Path) -> None:
    """Worktree scratch under .agent/ is never committed, so not scanned ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    pad = "q" * (MAX_BYTES + 64)
    root = _tree(tmp_path, {".agent/fuzz/big.txt": f"{pad}\nplanted {LIVE_KEY} here\n"})
    assert check_secrets(root) == []


def test_large_file_long_jwt_straddling_boundary_reports_true_length(tmp_path: Path) -> None:
    """An unbounded multi-segment token crossing a boundary is caught whole ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    token = "eyJhbGciOiJIUzI1NiJ9" + "." + "b" * 8160 + "." + "c" * 30
    assert len(token) > 8192
    limit = 256 * 1024 - 4096
    straddling = "q" * (limit - 4) + token
    control = "q" * 1000 + token
    rest = MAX_BYTES - len(straddling) - len(control) + 64
    body = straddling + "\n" + control + "\n" + "q" * rest + "\n"
    root = _tree(tmp_path, {"notes/jwt.txt": body})
    violations = check_secrets(root)
    assert len(violations) == 2
    assert f"notes/jwt.txt:1: possible jwt: eyJhbGci...cc (len={len(token)})" in violations[0]
    assert f"notes/jwt.txt:2: possible jwt: eyJhbGci...cc (len={len(token)})" in violations[1]


def test_large_file_single_long_token_is_reported_once(tmp_path: Path) -> None:
    """A 1.5 MiB key-shaped token is reported once with its true length ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    token = "sk-" + "A" * (1572864 - 3)
    assert len(token) == 1572864
    body = token + "\n" + "q" * (MAX_BYTES - len(token)) + "\n"
    root = _tree(tmp_path, {"notes/long.txt": body})
    violations = check_secrets(root)
    assert len(violations) == 1
    assert (
        "notes/long.txt:1: possible openai-style key: sk-AAAAA...AA (len=1572864)" in violations[0]
    )


def test_large_file_prose_run_without_secrets_is_clean(tmp_path: Path) -> None:
    """A 3 MiB file of repeated prose with key-like fragments stays clean ()."""
    unit = "risk-assessment "
    body = unit * (3 * 1024 * 1024 // len(unit) + 64) + "\n"
    assert len(body) > 3 * 1024 * 1024
    root = _tree(tmp_path, {"notes/prose.txt": body})
    assert check_secrets(root) == []


def test_large_file_scan_heap_stays_bounded(tmp_path: Path) -> None:
    """Newline counting never materialises the mapped file on the heap ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    size = 10 * 1024 * 1024
    token = "sk-" + "A" * 64
    body = "q" * (size - len(token) - 1) + token + "\n"
    assert len(body) > MAX_BYTES
    target = tmp_path / "notes" / "wide.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body.encode())
    tracemalloc.start()
    try:
        violations = check_secrets(tmp_path)
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert len(violations) == 1
    assert (
        f"notes/wide.txt:1: possible openai-style key: sk-AAAAA...AA (len={len(token)})"
        in (violations[0])
    )
    assert peak < 2 * 1024 * 1024


def test_byte_patterns_preserve_provider_flags() -> None:
    """Bytes patterns carry every str flag except UNICODE ()."""
    import re

    from wills_agentic_tools.checks import secrets as scan

    assert len(scan._BYTE_PATTERNS) == len(scan._PATTERNS)
    probes = ("sk-" + "a" * 40, "SK-" + "A" * 40, "ghp_" + "b" * 36, "GHP_" + "B" * 36)
    for (label, pattern), (blabel, bpattern) in zip(
        scan._PATTERNS, scan._BYTE_PATTERNS, strict=True
    ):
        assert label == blabel
        assert bpattern.flags == pattern.flags & ~re.UNICODE
        for probe in probes:
            assert bool(pattern.search(probe)) == bool(bpattern.search(probe.encode("ascii")))


def test_large_evidence_private_key_block_is_exempt_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Trial keypairs in over-limit evidence stay exempt and logged ()."""
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    pad = "q" * (MAX_BYTES + 64)
    body = f"{pad}\n" + '{"obs":"' + _PEM + '"}\n'
    root = _tree(tmp_path, {"evidence/bundle/sources/harbor.json": body})
    with caplog.at_level(logging.WARNING):
        assert check_secrets(root) == []
    assert "skipping 1 private key block match(es) under evidence/" in caplog.text


def test_large_file_empty_file_yields_nothing(tmp_path: Path) -> None:
    """A 0-byte file cannot be mapped and yields no violations ()."""
    from wills_agentic_tools.checks.secrets import _scan_large_file

    target = tmp_path / "empty.txt"
    target.write_bytes(b"")
    assert _scan_large_file(target, tmp_path) == []


def test_large_file_unreadable_file_yields_nothing(tmp_path: Path) -> None:
    """A permission-denied file is skipped without violations ()."""
    import os

    from wills_agentic_tools.checks.secrets import _scan_large_file

    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    target = tmp_path / "notes" / "denied.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(f"planted {LIVE_KEY} here\n".encode())
    target.chmod(0o000)
    assert _scan_large_file(target, tmp_path) == []


def test_large_file_scan_error_returns_partial_results_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-scan failure keeps already-found violations and logs ()."""
    import types

    from wills_agentic_tools.checks import secrets as scan
    from wills_agentic_tools.checks.secrets import MAX_BYTES

    head = ("planted " + LIVE_KEY + " here\n").encode()
    pad_len = MAX_BYTES + 64 - len(head)
    target = tmp_path / "notes" / "flaky.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(head + b"q" * pad_len + b"\n")
    real = scan._BYTE_PATTERNS[0][1]

    def flaky_finditer(data):
        yield from real.finditer(data)
        raise OSError("injected fault")

    flaky = types.SimpleNamespace(finditer=flaky_finditer)
    monkeypatch.setattr(
        scan, "_BYTE_PATTERNS", ((scan._BYTE_PATTERNS[0][0], flaky),) + scan._BYTE_PATTERNS[1:]
    )
    with caplog.at_level(logging.WARNING):
        violations = scan._scan_large_file(target, tmp_path)
    assert len(violations) == 1
    assert "notes/flaky.txt:1" in violations[0]
    assert "notes/flaky.txt" in caplog.text
