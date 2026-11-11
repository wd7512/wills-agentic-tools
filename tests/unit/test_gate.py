from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from wills_agentic_tools.config import ConfigError
from wills_agentic_tools.gate import (
    CANNOT_START,
    Gate,
    GateResult,
    _print_summary,
    build_gates,
    gate_env,
    main,
    run_gate,
    run_gates,
    select_gates,
)

REPO = Path(".")
GATES = build_gates({})
GATE_NAMES = tuple(g.name for g in GATES)


class FakeRunner:
    def __init__(self, returncode=0, fail_on=None):
        self.returncode, self.fail_on = returncode, fail_on
        self.commands, self.envs, self.cwds = [], [], []

    def __call__(self, command, cwd, env):
        self.commands.append(command)
        self.envs.append(env)
        self.cwds.append(cwd)
        return 1 if self.fail_on is not None and self.fail_on in command else self.returncode


def _unavailable(command, cwd, env):
    raise OSError("uv not found on PATH")


class TestExitCodes:
    def test_all_passing_exits_zero(self):
        assert run_gates(GATES, REPO, runner=FakeRunner(0)) == 0

    def test_one_failing_gate_fails_the_whole_run(self):
        assert run_gates(GATES, REPO, runner=FakeRunner(fail_on="pytest")) == 1

    def test_a_runner_that_raises_oserror_is_not_propagated(self):
        assert run_gates(GATES, REPO, runner=_unavailable) == 1

    def test_a_gate_that_cannot_start_is_recorded_as_127(self):
        result = run_gate(GATES[0], REPO, _unavailable)
        assert result.returncode == CANNOT_START == 127
        assert not result.passed

    def test_a_gate_result_passes_only_on_zero(self):
        assert GateResult("lint", 0, 0.1).passed
        assert not GateResult("lint", 1, 0.1).passed


class TestEveryGateRuns:
    def test_every_gate_runs_even_after_one_fails(self):
        runner = FakeRunner(fail_on="pytest")
        assert run_gates(GATES, REPO, runner=runner) == 1
        assert runner.commands == [list(g.command) for g in GATES]

    def test_a_broken_environment_does_not_hide_the_other_gates(self):
        seen = []

        def failing_second(command, cwd, env):
            seen.append(command)
            if len(seen) == 2:
                raise OSError("uv unavailable")
            return 0

        assert run_gates(GATES, REPO, runner=failing_second) == 1
        assert len(seen) == len(GATES)

    def test_the_failing_gate_is_named_in_the_log(self, caplog):
        with caplog.at_level(logging.ERROR):
            run_gates(GATES, REPO, runner=FakeRunner(fail_on="pytest"))
        assert "tests" in caplog.text

    def test_gates_run_in_the_shipped_order(self):
        runner = FakeRunner()
        run_gates(GATES, REPO, runner=runner)
        assert runner.commands == [list(g.command) for g in GATES]


class TestSubprocessEnv:
    def test_gate_env_inherits_the_whole_environment(self):
        env = gate_env()
        for key, value in os.environ.items():
            assert env[key] == value

    def test_gate_env_disables_bytecode_writes(self):
        assert gate_env()["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_gate_env_applies_overrides_without_dropping_path(self):
        env = gate_env({"CUSTOM_GATE": "1"})
        assert env["CUSTOM_GATE"] == "1" and "PATH" in env

    def test_the_runner_receives_a_superset_of_os_environ(self):
        runner = FakeRunner()
        run_gates(GATES, REPO, runner=runner)
        for env in runner.envs:
            assert all(key in env for key in os.environ)
            assert env["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_gates_run_with_the_repo_root_as_cwd(self):
        runner = FakeRunner()
        root = Path("some-repo")
        run_gates(GATES, root, runner=runner)
        assert runner.cwds == [root] * len(GATES)


class TestSelection:
    def test_only_selects_a_subset(self):
        runner = FakeRunner()
        assert run_gates(GATES, REPO, only="lint,types", runner=runner) == 0
        assert runner.commands == [list(GATES[1].command), list(GATES[2].command)]

    def test_skip_drops_one_gate(self):
        runner = FakeRunner()
        assert run_gates(GATES, REPO, skip="tests", runner=runner) == 0
        assert len(runner.commands) == len(GATES) - 1
        assert not any("pytest" in c for c in runner.commands)

    def test_only_and_skip_compose(self):
        runner = FakeRunner()
        assert run_gates(GATES, REPO, only="lint,types", skip="types", runner=runner) == 0
        assert runner.commands == [list(GATES[1].command)]

    def test_an_unknown_only_name_is_a_usage_error_not_a_pass(self):
        runner = FakeRunner()
        assert run_gates(GATES, REPO, only="lintz", runner=runner) == 2
        assert runner.commands == []

    def test_an_unknown_skip_name_is_a_usage_error(self):
        assert run_gates(GATES, REPO, skip="lintz", runner=FakeRunner()) == 2

    def test_skipping_everything_is_a_usage_error(self):
        runner = FakeRunner()
        assert run_gates(GATES, REPO, skip=",".join(GATE_NAMES), runner=runner) == 2
        assert runner.commands == []

    def test_select_gates_returns_none_on_a_usage_error(self):
        assert select_gates(GATES, only="nope") is None
        assert select_gates(GATES, skip=",".join(GATE_NAMES)) is None

    def test_select_gates_preserves_shipped_order(self):
        selected = select_gates(GATES, only="lock,format")
        assert selected is not None
        assert [g.name for g in selected] == ["format", "lock"]

    def test_blank_values_select_everything(self):
        selected = select_gates(GATES, only="", skip="  ,  ")
        assert selected is not None and len(selected) == len(GATES)


class TestShippedGates:
    def test_all_five_gates_are_shipped(self):
        assert GATE_NAMES == ("format", "lint", "types", "tests", "lock")

    def test_pytest_runs_only_the_core_tests(self):
        assert "tests" in GATES[3].command

    def test_pytest_runs_on_all_cores(self):
        command = GATES[3].command
        assert command[command.index("-n") + 1] == "auto"

    def test_every_gate_goes_through_uv_except_the_bare_lock_check(self):
        for gate in GATES:
            assert gate.command == (
                ("uv", "lock", "--check") if gate.name == "lock" else gate.command
            )
            if gate.name != "lock":
                assert gate.command[:2] == ("uv", "run")

    def test_gate_names_are_unique(self):
        assert len(set(GATE_NAMES)) == len(GATE_NAMES)


class TestCliWiring:
    def test_main_dispatches_gate_with_only_and_skip(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("")
        seen = []

        def fake_run(gates, repo_root, only="", skip="", runner=None):
            seen.append((repo_root, only, skip))
            return 0

        monkeypatch.setattr("wills_agentic_tools.gate.run_gates", fake_run)
        assert main(["--only", "lint,types", "--skip", "types", "--repo-root", str(tmp_path)]) == 0
        assert seen == [(tmp_path, "lint,types", "types")]

    def test_gate_usage_error_exits_two(self, tmp_path):
        assert main(["--only", "bogus-gate", "--repo-root", str(tmp_path)]) == 2

    def test_gate_is_offered_alongside_check(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        assert "gate" in capsys.readouterr().out


class TestVerdictIsTheLastStdoutLine:
    def test_failing_gate_prints_verdict_as_final_stdout_line(self, capsys):
        _print_summary([GateResult("lint", 0, 0.1), GateResult("types", 1, 0.2)])
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[-1].startswith("VERDICT: FAIL") and "types" in lines[-1]

    def test_passing_gates_print_a_pass_verdict_last(self, capsys):
        _print_summary([GateResult("lint", 0, 0.1), GateResult("types", 0, 0.2)])
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[-1].startswith("VERDICT: PASS") and "FAIL" not in lines[-1]

    def test_verdict_reports_how_many_gates_failed(self, capsys):
        _print_summary(
            [GateResult("lint", 1, 0.1), GateResult("types", 2, 0.2), GateResult("lock", 0, 0.3)]
        )
        verdict = capsys.readouterr().out.strip().splitlines()[-1]
        assert "2 of 3" in verdict and "lint, types" in verdict


def test_default_config_builds_five_exact_parts():
    assert build_gates({}) == (
        Gate("format", ("uv", "run", "ruff", "format", "--check")),
        Gate("lint", ("uv", "run", "ruff", "check")),
        Gate("types", ("uv", "run", "ty", "check", "src/")),
        Gate("tests", ("uv", "run", "pytest", "tests", "-n", "auto")),
        Gate("lock", ("uv", "lock", "--check")),
    )


def test_two_test_paths_both_appear_in_tests_command():
    gate = build_gates({"tests": ["tests/unit", "tests/integration"]})[3]
    assert gate.command == ("uv", "run", "pytest", "tests/unit", "tests/integration", "-n", "auto")


def test_configured_e2e_is_skipped_on_plain_run():
    gates = build_gates({"e2e": ["tests/e2e"]})
    runner = FakeRunner()
    assert run_gates(gates, REPO, runner=runner) == 0
    assert len(runner.commands) == 5


def test_only_e2e_runs_only_e2e():
    gates = build_gates({"e2e": ["tests/e2e"]})
    runner = FakeRunner()
    assert run_gates(gates, REPO, only="e2e", runner=runner) == 0
    assert runner.commands == [list(gates[-1].command)]


def test_only_tests_and_e2e_runs_in_part_order():
    gates = build_gates({"e2e": ["tests/e2e"]})
    runner = FakeRunner()
    assert run_gates(gates, REPO, only="tests,e2e", runner=runner) == 0
    assert runner.commands == [list(gates[3].command), list(gates[5].command)]


def test_only_e2e_without_configuration_returns_two(tmp_path):
    assert main(["--only", "e2e", "--repo-root", str(tmp_path)]) == 2


def test_skip_e2e_is_accepted_when_configured():
    gates = build_gates({"e2e": ["tests/e2e"]})
    assert run_gates(gates, REPO, skip="e2e", runner=FakeRunner()) == 0


def test_wrong_tests_config_type_returns_two(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.devtools.gate]\ntests = "tests"\n')
    assert main(["--repo-root", str(tmp_path)]) == 2


def test_oserror_is_reported_as_127_and_later_parts_run(capsys):
    gate = Gate("first", ("x",))
    result = run_gate(gate, REPO, _unavailable)
    assert result.returncode == 127
    gates = (gate, Gate("later", ("y",)))
    seen = []

    def runner(command, cwd, env):
        seen.extend(command)
        if command == ["x"]:
            raise OSError("cannot start")
        return 0

    assert run_gates(gates, REPO, runner=runner) == 1
    assert "y" in seen
    assert "FAIL(127)" in capsys.readouterr().out


def test_build_gates_raises_config_error_for_wrong_types():
    with pytest.raises(ConfigError):
        build_gates({"tests": "tests"})
