"""Gate runner — every repo gate, one command, one exit code.

The gates are otherwise five separate commands a human has to remember,
which makes "did they all pass?" a memory test. That test has already
failed twice: a `pytest | tail` pipe swallowed a non-zero exit and let a
commit through with a failing test, and the same mistake recurred when a
*new* gate command was piped to `tail` and reported exit 0 on a crashed
run. A gate whose result depends on who piped it is not a gate.

Two properties are load-bearing and are the reason this is a module
rather than a shell alias:

* Every gate runs even after one fails. Stopping at the first failure
  reports a half-truth, and finding out what else broke then means
  re-running the same commands by hand -- the exact memory test this
  exists to remove.
* A gate that cannot *start* (no `uv` on PATH, a missing interpreter) is
  recorded as a failure at the conventional rc 127, never swallowed.
  One broken environment must not hide the other five results.

Subprocess output is inherited, not captured, so a failing gate shows
its own diagnostics; only the final summary table is printed. The env
handed to each gate is ``{**os.environ, **overrides}`` — a draft that
passed just the override dict wiped PATH, and then `uv` itself could not
be found.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from wills_agentic_tools.config import ConfigError, load_tool_config

logger = logging.getLogger(__name__)

CANNOT_START = 127


@dataclass(frozen=True)
class Gate:
    """One named gate: a display name and the argv to run it."""

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class GateResult:
    """The outcome of one gate: its exit status and how long it took."""

    name: str
    returncode: int
    seconds: float

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def build_gates(config: dict) -> tuple[Gate, ...]:
    source = config.get("source", "src/")
    tests = config.get("tests", ["tests"])
    e2e = config.get("e2e", [])
    if (
        not isinstance(source, str)
        or not isinstance(tests, list)
        or not all(isinstance(v, str) for v in tests)
        or not isinstance(e2e, list)
        or not all(isinstance(v, str) for v in e2e)
    ):
        raise ConfigError(
            "gate config source must be a string and tests/e2e must be lists of strings"
        )
    gates = [
        Gate("format", ("uv", "run", "ruff", "format", "--check")),
        Gate("lint", ("uv", "run", "ruff", "check")),
        Gate("types", ("uv", "run", "ty", "check", source)),
        Gate(
            "tests",
            (
                "uv",
                "run",
                "pytest",
                *tests,
                "-n",
                "auto",
            ),
        ),
        Gate("lock", ("uv", "lock", "--check")),
    ]
    if e2e:
        gates.append(Gate("e2e", ("uv", "run", "pytest", *e2e)))
    return tuple(gates)


CommandRunner = Callable[[list[str], Path, Mapping[str, str]], int]


def gate_env(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the subprocess env for a gate: inherited, plus overrides.

    The inherited half is not optional. Passing only the override dict
    produces an env with no PATH, so the gate cannot find `uv` at all
    and every gate fails identically for the wrong reason.
    """
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if overrides:
        env.update(overrides)
    return env


def _default_runner(command: list[str], cwd: Path, env: Mapping[str, str]) -> int:
    return subprocess.run(command, cwd=cwd, env=dict(env)).returncode


def _parse_names(value: str, flag: str, valid_names: tuple[str, ...]) -> list[str] | None:
    """Split and validate a comma-separated gate-name list.

    Returns the names, or ``None`` if any is unknown (error already
    logged). An unknown name is a usage error, not a silent no-op: a
    typo'd ``--only lintz`` that ran the full suite would look like a
    passing lint gate.
    """
    names = [part.strip() for part in value.split(",") if part.strip()]
    invalid = [name for name in names if name not in valid_names]
    if invalid:
        logger.error(
            "unknown %s gate name(s): %s (valid: %s)",
            flag,
            ", ".join(invalid),
            ",".join(valid_names),
        )
        return None
    return names


def select_gates(gates: tuple[Gate, ...], only: str = "", skip: str = "") -> list[Gate] | None:
    """Resolve ``--only``/``--skip`` into the gates to run, in shipped order.

    Returns ``None`` on a usage error — an unknown name, or a selection
    that leaves nothing to run, since a gate run of zero gates is a run
    that reports nothing about the repo.
    """
    names = tuple(gate.name for gate in gates)
    only_names = _parse_names(only, "--only", names)
    if only_names is None:
        return None
    skip_names = _parse_names(skip, "--skip", names)
    if skip_names is None:
        return None

    selected = [
        gate for gate in gates if (gate.name in only_names if only_names else gate.name != "e2e")
    ]
    selected = [gate for gate in selected if gate.name not in skip_names]
    if not selected:
        logger.error("no gates selected — nothing to run")
        return None
    return selected


def run_gate(gate: Gate, repo_root: Path, runner: CommandRunner | None = None) -> GateResult:
    """Run one gate and return its result; a start failure is rc 127, not raised."""
    run = runner or _default_runner
    env = gate_env()
    started = time.monotonic()
    try:
        returncode = run(list(gate.command), repo_root, env)
    except OSError as exc:
        logger.error("gate %r could not start: %s", gate.name, exc)
        returncode = CANNOT_START
    seconds = time.monotonic() - started
    logger.info(
        "gate %r -> %s in %.2fs", gate.name, "pass" if returncode == 0 else returncode, seconds
    )
    return GateResult(name=gate.name, returncode=returncode, seconds=seconds)


def _print_summary(results: list[GateResult]) -> None:
    """Print the reconciled per-gate table (the reporting convention).

    The verdict is the final stdout line on purpose. A caller who pipes this
    output through a pipe or log scraper still sees the last line printed,
    so a failing gate command cannot look like a clean run. The error line
    goes to the logging handler only, because a stderr diagnostic is exactly
    what gets swallowed by such a pipe.
    """
    print(f"{'gate':<10} {'result':<10} {'seconds':>7}")
    for result in results:
        status = "pass" if result.passed else f"FAIL({result.returncode})"
        print(f"{result.name:<10} {status:<10} {result.seconds:7.2f}")

    failed = [result.name for result in results if not result.passed]
    if failed:
        print(
            f"VERDICT: FAIL ({len(failed)} of {len(results)} gate(s) failed: {', '.join(failed)})"
        )
    else:
        print(f"VERDICT: PASS ({len(results)} of {len(results)} gate(s) passed)")


def run_gates(
    gates: tuple[Gate, ...],
    repo_root: Path,
    only: str = "",
    skip: str = "",
    runner: CommandRunner | None = None,
) -> int:
    """Run the selected gates and return 0 only if every one passed.

    Every selected gate runs, whatever the others did: the first
    failure does not stop the run. Returns 0 all-pass, 1 any-failure,
    2 on a usage error.
    """
    selected = select_gates(gates, only=only, skip=skip)
    if selected is None:
        return 2

    results = [run_gate(gate, repo_root, runner) for gate in selected]
    _print_summary(results)

    failed = [result.name for result in results if not result.passed]
    if failed:
        logger.error("%d of %d gate(s) failed: %s", len(failed), len(results), ", ".join(failed))
        return 1
    logger.info("all %d gate(s) passed", len(results))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate", description="run repository gates")
    parser.add_argument("--only", default="")
    parser.add_argument("--skip", default="")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = load_tool_config(args.repo_root, "gate")
        gates = build_gates(config)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    return run_gates(gates, args.repo_root, only=args.only, skip=args.skip)
