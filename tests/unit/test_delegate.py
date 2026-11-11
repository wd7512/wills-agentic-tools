"""Tests for wills_agentic_tools.delegate - fake workers and gate executables.

Every lane lifecycle test runs in a real ``git`` repository under tmp, a
fake ``opencode`` on PATH that writes canned JSON events, and a stateful
fake gate whose rc comes from an env-pointed counter file, so
start/resume/review/status/gate run end-to-end without the network or
the real toolchain.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from wills_agentic_tools import delegate


def _fake_agent_command(
    cmd: list[str], *, cwd: Path, env: dict[str, str], stdout, timeout: float, **kwargs
) -> subprocess.CompletedProcess[str]:
    argv = cmd[1:]
    backend = cmd[0]
    if pwd_file := env.get("FAKE_PWD_FILE"):
        Path(pwd_file).write_text(env.get("PWD", ""))
    if log := env.get("FAKE_LOG"):
        with Path(log).open("a") as handle:
            handle.write("---CALL---" + "\n" + "\n".join(argv) + "\n")
    calls = env.get("FAKE_CALLS")
    call_index = 1
    if calls:
        path = Path(calls)
        call_index = (len(path.read_text().splitlines()) if path.exists() else 0) + 1
        with path.open("a") as handle:
            handle.write("\n")
    if log := env.get("FAKE_CODEX_LOG"):
        with Path(log).open("a") as handle:
            handle.write(json.dumps(argv) + "\n")
    if env.get("FAKE_MODE") == "timeout":
        raise subprocess.TimeoutExpired(cmd, timeout)
    events: list[dict] = []
    returncode = 0
    if backend == "codex":
        if env.get("FAKE_CODEX_FAILED"):
            events.append({"type": "turn.failed", "error": {"message": "worker failed"}})
        else:
            events.extend(
                [
                    {"type": "thread.started", "thread_id": "thread-codex-1"},
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "codex worker done"},
                    },
                ]
            )
    else:
        mode = env.get("FAKE_MODE", "ok")
        is_reviewer = any("You are the reviewer" in arg for arg in argv)
        events.append({"type": "step_start", "sessionID": "ses_fake1"})
        if mode == "provider-error" and call_index <= 1:
            events.append({"type": "error", "message": "provider exploded"})
        elif mode == "auto-reject":
            events.append(
                {
                    "type": "tool_use",
                    "sessionID": "ses_fake1",
                    "part": {
                        "type": "tool_use",
                        "state": {
                            "status": "error",
                            "error": "auto-rejecting tool call (offline)",
                        },
                    },
                }
            )
        else:
            if is_reviewer:
                (cwd / ".agent" / "review.md").write_text(
                    "## Review\nHigh: 0\nMedium: 1\nVERDICT: APPROVED\n"
                    "Strongest datum against the verdict: none noted.\n"
                )
            text = "reviewer done" if is_reviewer else "worker done"
            events.append(
                {
                    "type": "text",
                    "sessionID": "ses_fake1",
                    "part": {"type": "text", "text": text},
                }
            )
            if mode == "fail-rc":
                stdout.write("boom\n")
                returncode = 4
    stdout.write("".join(json.dumps(event) + "\n" for event in events))
    return subprocess.CompletedProcess(cmd, returncode)


def _fake_gate_command(cmd: list[str], ctx: delegate.LaneCtx, stdout):
    fails_file = os.environ.get("OC_GATE_FAILS")
    remaining = (
        int(Path(fails_file).read_text()) if fails_file and Path(fails_file).is_file() else 0
    )
    if remaining:
        Path(fails_file).write_text(str(remaining - 1))
        stdout.write("gate failed\n")
        return subprocess.CompletedProcess(cmd, 1)
    stdout.write("gate ok\n")
    return subprocess.CompletedProcess(cmd, 0)


@pytest.fixture
def lane_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("repo\n")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(delegate, "_detect_repo_root", lambda _cwd: repo)
    monkeypatch.setattr(delegate, "_lane_ctx", lambda name, cwd=None: delegate.LaneCtx(repo, name))
    monkeypatch.setattr(
        delegate,
        "_ensure_worktree",
        lambda ctx, base: ctx.wt.mkdir(parents=True, exist_ok=True) or True,
    )
    monkeypatch.setattr(delegate, "_git_head", lambda _ctx: "a" * 40)
    monkeypatch.setattr(delegate, "_run_uv_sync", lambda _ctx: None)
    monkeypatch.setattr(delegate.CodexBackend, "_cache_dir", lambda: str(tmp_path / "uv-cache"))
    monkeypatch.setattr(delegate, "_run_agent_command", _fake_agent_command)
    monkeypatch.setattr(delegate, "_run_gate_command", _fake_gate_command)
    return SimpleNamespace(lane=delegate, repo=repo, tmp=tmp_path, gate_cmd="fake-gate")


def _brief(env: SimpleNamespace) -> Path:
    brief = env.tmp / "brief.md"
    brief.write_text("implement the thing" + chr(10))
    return brief


def _start_argv(env: SimpleNamespace, extra: list[str] | None = None) -> list[str]:
    argv = [
        "start",
        "l1",
        "--brief",
        str(_brief(env)),
        "--base",
        "main",
        "--gate-cmd",
        env.gate_cmd,
        "--model",
        "opencode/glm-5.3-flash-free",
    ]
    return argv + (extra or [])


def _run(env: SimpleNamespace, argv: list[str]) -> int:
    return env.lane.main(argv)


def _lane(env: SimpleNamespace, name: str = "l1") -> dict:
    path = env.repo / ".worktrees" / f"w-{name}" / ".agent" / "lane.json"
    return json.loads(path.read_text())


def _wt(env: SimpleNamespace, name: str = "l1") -> Path:
    return env.repo / ".worktrees" / f"w-{name}"


def _fake_calls(log_path: Path) -> list[list[str]]:
    return [block.strip().splitlines() for block in log_path.read_text().split("---CALL---")[1:]]


def test_start_green(lane_env, capsys, monkeypatch):
    env = lane_env
    fake_log = env.tmp / "fake-opencode.log"
    monkeypatch.setenv("FAKE_LOG", str(fake_log))
    rc = _run(env, _start_argv(env))
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["state"] == "gate-green"
    assert lane_state["gate_rc"] == 0
    assert lane_state["sessionID"] == "ses_fake1"
    assert lane_state["model"] == "opencode/glm-5.3-flash-free"
    assert lane_state["rounds"] == 1
    assert len(lane_state["head"]) == 40
    assert (_wt(env) / ".agent" / "brief.md").is_file()
    assert (_wt(env) / ".agent" / "gate.log").is_file()
    jsonl = (_wt(env) / ".agent" / "oc-l1-r0.jsonl").read_text()
    assert '"sessionID"' in jsonl
    assert "worker done" in jsonl
    call = _fake_calls(fake_log)[0]
    assert call[:5] == ["run", "--format", "json", "--title", "l1"]
    assert call[call.index("-m") + 1] == "opencode/glm-5.3-flash-free"
    assert "--variant" not in call
    fields = [f.strip() for f in capsys.readouterr().out.strip().split(" | ")]
    assert fields[0] == "l1"
    assert fields[1] == "gate-green"
    assert fields[2] == lane_state["head"][:8]
    assert fields[3] == "0"
    assert fields[4] == "- -/-"
    assert fields[5] == "opencode/glm-5.3-flash-free"
    assert fields[6] == "1"


def test_start_passes_variant_after_model(lane_env, monkeypatch):
    env = lane_env
    fake_log = env.tmp / "fake-opencode.log"
    monkeypatch.setenv("FAKE_LOG", str(fake_log))
    rc = _run(env, _start_argv(env, extra=["--variant", "xhigh"]))
    assert rc == 0
    call = _fake_calls(fake_log)[0]
    assert call[call.index("-m") + 1 : call.index("-m") + 4] == [
        "opencode/glm-5.3-flash-free",
        "--variant",
        "xhigh",
    ]


def test_codex_start_and_resume_use_recorded_thread_id(lane_env, monkeypatch):
    env = lane_env
    codex_log = env.tmp / "fake-codex.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(codex_log))
    rc = _run(env, _start_argv(env, extra=["--model", "codex:gpt-6-luna"]))
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["backend"] == "codex"
    assert lane_state["model"] == "codex:gpt-6-luna"
    assert lane_state["sessionID"] == "thread-codex-1"
    event_log = (_wt(env) / ".agent" / "l1-r0.jsonl").read_text()
    assert '"text": "codex worker done"' in event_log
    start_call = json.loads(codex_log.read_text().splitlines()[0])
    assert start_call[:4] == ["exec", "--json", "-s", "workspace-write"]
    assert "--add-dir" in start_call
    assert "--ephemeral" not in start_call
    assert "gpt-6-luna" in start_call

    decisions = env.tmp / "decisions.md"
    decisions.write_text("continue" + "\n")
    resume_rc = _run(env, ["resume", "l1", "--file", str(decisions), "--gate-cmd", env.gate_cmd])
    assert resume_rc == 0
    assert _lane(env)["state"] == "gate-green"
    resume_call = json.loads(codex_log.read_text().splitlines()[1])
    assert resume_call[:4] == ["exec", "resume", "thread-codex-1", "--json"]
    assert "thread-codex-1" in resume_call


def test_codex_turn_failed_marks_worker_failed(lane_env, monkeypatch):
    env = lane_env
    monkeypatch.setenv("FAKE_CODEX_FAILED", "1")
    rc = _run(env, _start_argv(env, extra=["--model", "codex:gpt-6-luna"]))
    assert rc == 1
    assert _lane(env)["state"] == "worker-failed"
    assert not (_wt(env) / ".agent" / "gate.log").exists()


def test_backend_selection_routes_codex_prefix_only():
    assert delegate._backend_for("codex:gpt-6-luna").name == "codex"
    assert delegate._backend_for("opencode/glm-5.3-flash-free").name == "opencode"


def test_start_provider_error_does_not_invoke_second_model(lane_env, monkeypatch):
    env = lane_env
    calls = env.tmp / "fake-calls"
    monkeypatch.setenv("FAKE_MODE", "provider-error")
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    rc = _run(env, _start_argv(env))
    assert rc == 1
    lane_state = _lane(env)
    assert lane_state["state"] == "worker-failed"
    assert lane_state["model"] == "opencode/glm-5.3-flash-free"
    assert lane_state["rounds"] == 1
    assert not (_wt(env) / ".agent" / "gate.log").is_file()
    # only one opencode call happened
    assert calls.read_text().count(chr(10)) <= 2


def test_failed_tool_use_without_auto_reject_does_not_fail_run(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {"type": "tool_use", "part": {"state": {"status": "error", "error": "bash exited 1"}}}
        )
        + "\n"
        + json.dumps({"type": "text", "part": {"text": "I fixed the command."}})
        + "\n"
    )
    result = delegate._analyze_run(events, 0, False, head_moved=False)
    assert not result.failed


def test_resume_model_records_model(lane_env, capsys, monkeypatch):
    env = lane_env
    _run(env, _start_argv(env))
    capsys.readouterr()
    decisions = env.tmp / "decisions.md"
    decisions.write_text("use approach B" + chr(10))
    rc = _run(
        env,
        [
            "resume",
            "l1",
            "--file",
            str(decisions),
            "--gate-cmd",
            env.gate_cmd,
            "--model",
            "opencode/deepseek-v4.1-flash-free",
        ],
    )
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["state"] == "gate-green"
    assert lane_state["model"] == "opencode/deepseek-v4.1-flash-free"


def test_start_auto_reject_records_worker_failed_and_skips_gate(lane_env, capsys, monkeypatch):
    env = lane_env
    monkeypatch.setenv("FAKE_MODE", "auto-reject")
    rc = _run(env, _start_argv(env))
    assert rc == 1
    lane_state = _lane(env)
    assert lane_state["state"] == "worker-failed"
    assert not (_wt(env) / ".agent" / "gate.log").is_file()
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    assert out.startswith("l1 | worker-failed | ")


def test_start_gate_failure_autofixes_to_green(lane_env, monkeypatch):
    env = lane_env
    fails = env.tmp / "gate-fails"
    fails.write_text("1")
    fake_log = env.tmp / "fake-opencode.log"
    monkeypatch.setenv("FAKE_LOG", str(fake_log))
    monkeypatch.setenv("OC_GATE_FAILS", str(fails))
    rc = _run(env, _start_argv(env, ["--variant", "xhigh"]))
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["state"] == "gate-green"
    assert lane_state["gate_rc"] == 0
    assert lane_state["fix_rounds"] == 1
    assert lane_state["rounds"] == 2
    resumed = [call for call in _fake_calls(fake_log) if "-s" in call]
    assert len(resumed) == 1
    assert resumed[0][resumed[0].index("-s") + 1] == "ses_fake1"
    assert resumed[0][-2:] == ["--variant", "xhigh"]
    assert "The full gate failed (exit 1)" in " ".join(resumed[0])


def test_gate_fix_round_reuses_the_stored_variant(lane_env, monkeypatch):
    env = lane_env
    fails = env.tmp / "gate-fails"
    fails.write_text("1")
    codex_log = env.tmp / "fake-codex.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(codex_log))
    monkeypatch.setenv("OC_GATE_FAILS", str(fails))
    rc = _run(
        env,
        _start_argv(env, ["--model", "codex:gpt-6-luna", "--variant", "xhigh"]),
    )
    assert rc == 0
    calls = [json.loads(line) for line in codex_log.read_text().splitlines()]
    fix_call = calls[1]
    variant_index = fix_call.index('model_reasoning_effort="xhigh"')
    assert fix_call[variant_index - 1] == "-c"


def test_start_gate_failure_respects_max_fix(lane_env, monkeypatch, capsys):
    env = lane_env
    fails = env.tmp / "gate-fails"
    fails.write_text("5")
    fake_log = env.tmp / "fake-opencode.log"
    monkeypatch.setenv("FAKE_LOG", str(fake_log))
    monkeypatch.setenv("OC_GATE_FAILS", str(fails))
    rc = _run(env, _start_argv(env))
    assert rc == 1
    lane_state = _lane(env)
    assert lane_state["state"] == "gate-red"
    assert lane_state["gate_rc"] == 1
    assert lane_state["fix_rounds"] == 2
    assert lane_state["rounds"] == 3
    assert len([call for call in _fake_calls(fake_log) if "-s" in call]) == 2
    out = capsys.readouterr().out.strip()
    assert out.startswith("l1 | gate-red | ")


def test_start_timeout_records_state_then_resume_recovers(lane_env, monkeypatch, capsys):
    env = lane_env
    monkeypatch.setenv("FAKE_MODE", "timeout")
    rc = _run(env, [*_start_argv(env), "--timeout", "1"])
    assert rc == 1
    lane_state = _lane(env)
    assert lane_state["state"] == "timeout"
    assert capsys.readouterr().out.strip().startswith("l1 | timeout | ")

    decisions = env.tmp / "decisions.md"
    decisions.write_text("keep going with approach A" + chr(10))
    monkeypatch.delenv("FAKE_MODE")
    rc = _run(env, ["resume", "l1", "--file", str(decisions), "--gate-cmd", env.gate_cmd])
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["state"] == "gate-green"
    assert (_wt(env) / ".agent" / "decisions-r1.md").read_text() == decisions.read_text()


def test_resume_recorded_session_gets_decisions_file(lane_env, capsys, monkeypatch):
    env = lane_env
    calls = env.tmp / "fake-calls"
    monkeypatch.setenv("FAKE_MODE", "provider-error")
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    _run(env, _start_argv(env, extra=["--model", "opencode-go/muse-spark-1.3-contributor"]))
    assert _lane(env)["state"] == "worker-failed"
    capsys.readouterr()

    monkeypatch.delenv("FAKE_MODE")
    fake_log = env.tmp / "fake-opencode.log"
    monkeypatch.setenv("FAKE_LOG", str(fake_log))
    decisions = env.tmp / "opts.md"
    decisions.write_text("use approach B" + chr(10))
    rc = _run(env, ["resume", "l1", "--file", str(decisions), "--gate-cmd", env.gate_cmd])
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["state"] == "gate-green"
    assert (_wt(env) / ".agent" / "decisions-r1.md").read_text() == decisions.read_text()
    resume_call = [call for call in _fake_calls(fake_log) if "-s" in call][0]
    assert resume_call[resume_call.index("-s") + 1] == "ses_fake1"
    assert any("decisions-r1.md" in line for line in resume_call)
    assert any("implement it" in line for line in resume_call)
    assert capsys.readouterr().out.strip().startswith("l1 | gate-green | ")


def test_review_parses_verdict_and_counts(lane_env, capsys):
    env = lane_env
    _run(env, _start_argv(env))
    capsys.readouterr()
    rc = _run(env, ["review", "l1", "--model", "opencode-go/deepseek-v4.1-flash"])
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["state"] == "reviewed"
    assert lane_state["review"]["verdict"] == "APPROVED"
    assert lane_state["review"]["high"] == 0
    assert lane_state["review"]["medium"] == 1
    assert lane_state["review"]["model"] == "opencode-go/deepseek-v4.1-flash"
    assert (_wt(env) / ".agent" / "review.md").is_file()
    assert (_wt(env) / ".agent" / "review-l1-r0.jsonl").is_file()
    out = capsys.readouterr().out.strip()
    assert out.startswith("l1 | reviewed | ")
    assert "APPROVED 0/1" in out


def test_review_archives_previous_review(lane_env):
    env = lane_env
    _run(env, _start_argv(env))
    _run(env, ["review", "l1", "--model", "opencode-go/deepseek-v4.1-flash"])
    rc = _run(env, ["review", "l1", "--model", "opencode-go/deepseek-v4.1-flash"])
    assert rc == 0
    agent = _wt(env) / ".agent"
    archived = (agent / "review-r0.md").read_text()
    assert "VERDICT: APPROVED" in archived
    assert (agent / "review.md").is_file()
    assert (agent / "review-l1-r1.jsonl").is_file()


def test_review_warns_when_model_matches_worker(lane_env, caplog):
    env = lane_env
    _run(env, _start_argv(env))
    with caplog.at_level(logging.WARNING, logger="wills_agentic_tools.delegate"):
        rc = _run(env, ["review", "l1", "--model", "opencode/glm-5.3-flash-free"])
    assert rc == 0
    assert any("not cross-provider" in rec.message for rec in caplog.records)


def test_status_one_line_per_lane(lane_env, capsys):
    env = lane_env
    rc = _run(env, _start_argv(env))
    assert rc == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 1

    rc = _run(env, ["status", "l1"])
    assert rc == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("l1 | gate-green | ")
    assert "opencode/glm-5.3-flash-free" in line

    rc = _run(env, ["status", "nope"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""
    rc = _run(env, ["status"])
    assert rc == 0
    line = capsys.readouterr().out.strip()
    assert len(line.splitlines()) == 1
    assert line.startswith("l1 | gate-green | ")


def test_gate_only_reruns_and_records(lane_env, monkeypatch):
    env = lane_env
    _run(env, _start_argv(env))
    fails = env.tmp / "gate-fails"
    fails.write_text("1")
    monkeypatch.setenv("OC_GATE_FAILS", str(fails))
    rc = _run(env, ["gate", "l1", "--gate-cmd", env.gate_cmd])
    assert rc == 1
    lane_state = _lane(env)
    assert lane_state["gate_rc"] == 1
    assert lane_state["state"] == "gate-red"

    monkeypatch.delenv("OC_GATE_FAILS")
    rc = _run(env, ["gate", "l1", "--gate-cmd", env.gate_cmd])
    assert rc == 0
    lane_state = _lane(env)
    assert lane_state["gate_rc"] == 0
    assert lane_state["state"] == "gate-green"


def test_worker_runs_with_pwd_set_to_the_lane_worktree(lane_env, monkeypatch):
    env = lane_env
    pwd_file = env.tmp / "pwd.txt"
    monkeypatch.setenv("FAKE_PWD_FILE", str(pwd_file))
    monkeypatch.setenv("PWD", str(env.tmp))
    _run(env, _start_argv(env))
    assert pwd_file.read_text().strip() == str(_wt(env))


def test_delegate_has_no_public_profile_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "public"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "sample"\n[tool.devtools]\nprofile = "public"\n'
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(delegate, "_detect_repo_root", lambda _cwd: repo)
    assert delegate.main(["status"]) == 0


def test_codex_start_argv_with_variant(lane_env, monkeypatch):
    ctx = delegate.LaneCtx(lane_env.repo, "l1")
    backend = delegate.CodexBackend()
    monkeypatch.setattr(delegate.CodexBackend, "_cache_dir", lambda: "/cache")
    start = backend.build_start_argv(ctx, "prompt", "codex:gpt", None, "xhigh")
    assert start[start.index("-c") + 1] == 'model_reasoning_effort="xhigh"'
    assert start[start.index("-c") + 2] == "-m"


def test_codex_start_argv_without_variant(lane_env, monkeypatch):
    ctx = delegate.LaneCtx(lane_env.repo, "l1")
    backend = delegate.CodexBackend()
    monkeypatch.setattr(delegate.CodexBackend, "_cache_dir", lambda: "/cache")
    plain = backend.build_start_argv(ctx, "prompt", "codex:gpt", None, None)
    assert "model_reasoning_effort" not in " ".join(plain)


def test_codex_resume_argv_with_variant(lane_env, monkeypatch):
    ctx = delegate.LaneCtx(lane_env.repo, "l1")
    backend = delegate.CodexBackend()
    monkeypatch.setattr(delegate.CodexBackend, "_cache_dir", lambda: "/cache")
    resume = backend.build_resume_argv(ctx, "prompt", "codex:gpt", "sid", "xhigh")
    index = resume.index('model_reasoning_effort="xhigh"')
    assert resume[index + 1] == "-m"


def test_opencode_resume_argv_with_variant(lane_env):
    ctx = delegate.LaneCtx(lane_env.repo, "l1")
    open_code = delegate.OpenCodeBackend().build_resume_argv(ctx, "p", "opencode/x", "sid", "high")
    assert open_code[-2:] == ["--variant", "high"]


def test_start_requires_model(lane_env, capsys):
    argv = _start_argv(lane_env)
    argv.remove("--model")
    argv.remove("opencode/glm-5.3-flash-free")
    with pytest.raises(SystemExit) as exc:
        delegate.main(argv)
    assert exc.value.code == 2


def _configure_delegate_repo(env):
    (env.repo / "pyproject.toml").write_text(
        '[tool.devtools.delegate]\ngate_command = "from-config"\nbase = "config-base"\n'
        'extra_rules = ["Add one changelog fragment under changes/."]\n'
    )


def test_config_values_used_when_flags_absent(lane_env, monkeypatch):
    env = lane_env
    _configure_delegate_repo(env)
    captured = {}
    original = delegate._ensure_worktree
    gate_calls = []

    def ensure(ctx, base):
        captured["base"] = base
        return original(ctx, base)

    monkeypatch.setattr(delegate, "_ensure_worktree", ensure)
    original_gate = delegate._run_gate_command

    def record_gate(cmd, ctx, stdout):
        gate_calls.append(cmd)
        return original_gate(cmd, ctx, stdout)

    monkeypatch.setattr(delegate, "_run_agent_command", _fake_agent_command)
    monkeypatch.setattr(delegate, "_run_gate_command", record_gate)
    log = env.tmp / "prompt.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    rc = _run(env, ["start", "l1", "--brief", str(_brief(env)), "--model", "opencode/x"])
    assert rc == 0
    assert captured["base"] == "config-base"
    assert _lane(env)["base"] == "config-base"
    assert gate_calls == [["from-config"]]


def test_flags_override_delegate_config(lane_env, monkeypatch):
    env = lane_env
    _configure_delegate_repo(env)
    captured = {}
    original = delegate._ensure_worktree

    def ensure(ctx, base):
        captured["base"] = base
        return original(ctx, base)

    monkeypatch.setattr(delegate, "_ensure_worktree", ensure)
    monkeypatch.setattr(delegate, "_run_agent_command", _fake_agent_command)
    monkeypatch.setattr(delegate, "_run_gate_command", _fake_gate_command)
    rc = _run(
        env,
        [
            "start",
            "l2",
            "--brief",
            str(_brief(env)),
            "--model",
            "opencode/x",
            "--base",
            "explicit-base",
            "--gate-cmd",
            "explicit-gate",
        ],
    )
    assert rc == 0
    assert captured["base"] == "explicit-base"


def test_extra_rules_appear_in_start_prompt(lane_env, monkeypatch):
    env = lane_env
    _configure_delegate_repo(env)
    monkeypatch.setattr(delegate, "_run_agent_command", _fake_agent_command)
    monkeypatch.setattr(delegate, "_run_gate_command", _fake_gate_command)
    log = env.tmp / "prompt.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    assert _run(env, ["start", "l1", "--brief", str(_brief(env)), "--model", "opencode/x"]) == 0
    prompt = "\n".join(_fake_calls(log)[0])
    assert "- Add one changelog fragment under changes/." in prompt
    assert "Never touch `runs/`" not in prompt


def test_variant_persisted_and_resume_replaces(lane_env, monkeypatch):
    env = lane_env
    assert _run(env, _start_argv(env, ["--variant", "xhigh"])) == 0
    assert _lane(env)["variant"] == "xhigh"
    decisions = env.tmp / "decisions.md"
    decisions.write_text("continue\n")
    assert _run(env, ["resume", "l1", "--file", str(decisions), "--variant", "high"]) == 0
    assert _lane(env)["variant"] == "high"
