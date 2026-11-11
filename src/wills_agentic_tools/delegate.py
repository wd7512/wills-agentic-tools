"""Run worker sessions in repository worktrees and gate their changes.

The delegate launches opencode or Codex sessions, records their events and
session ids, runs the configured gate, and resumes the same session for
gate fixes. The orchestrator selects each model explicitly.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from wills_agentic_tools.config import ConfigError, load_tool_config

logger = logging.getLogger("wills_agentic_tools.delegate")

DEFAULT_BASE = "origin/dev"
DEFAULT_TIMEOUT = 1800.0
DEFAULT_MAX_FIX = 2
DEFAULT_GATE_CMD = "uv run gate"

WORKER_RULES = """- Work only inside the worktree; scratch goes in `.agent/`, never `/tmp`.
- Never `git push` and never use `gh`.
- One commit for the change; amend on later rounds.
- Don't repeat the same search. Never signal a pid you didn't spawn; no `pkill` or `killall`.
- Behaviour changes are red first: quote each failing test before fixing.
- Run only targeted pytest for the files you change; the delegate tool runs the full gate after you finish and sends you the failures."""


def _worker_rules(repo_root: Path) -> str:
    extra = _delegate_config(repo_root)["extra_rules"]
    return WORKER_RULES + "".join(f"\n- {rule}" for rule in extra)


def _delegate_config(repo_root: Path) -> dict[str, Any]:
    try:
        config = load_tool_config(repo_root, "delegate")
    except ConfigError as exc:
        raise LaneError(str(exc)) from exc
    for key, default, expected in (
        ("gate_command", DEFAULT_GATE_CMD, str),
        ("base", DEFAULT_BASE, str),
        ("extra_rules", [], list),
    ):
        value = config.get(key, default)
        if not isinstance(value, expected) or (
            key == "extra_rules" and not all(isinstance(x, str) for x in value)
        ):
            raise LaneError(f"[tool.devtools.delegate].{key} has the wrong type")
        config[key] = value
    return config


START_PROMPT = """\
You are the worker on lane {name} in this worktree. Read .agent/brief.md and implement it.

Follow the brief's worker rules and these lane rules:
{rules}

Amend the single commit on later rounds. Your final assistant message states what changed and reports targeted pytest; delegate runs the full gate after you finish."""

RESUME_PROMPT = """\
Read .agent/decisions-r{round}.md and implement it.

Follow the lane rules:
{rules}"""

FIX_PROMPT = """\
The full gate failed (exit {exit}). Read .agent/gate.log (the end of the file has the failures), fix them, keep the single commit, and run only targeted pytest for the files you change. Delegate reruns the full gate after you finish.

Follow the lane rules:
{rules}"""

REVIEW_PROMPT = """\
You are the reviewer for lane {name}. This is a read-only review: do not modify any file except .agent/review.md.

Read .agent/brief.md (the brief; the reviewer gets the same brief).{decisions}{focus}\
Run `git diff {base}...HEAD` and review the diff against the brief.

Apply the High/Medium floor: correctness, evidence integrity, gates. Real High and Medium findings are fixed, not deferred.

Write .agent/review.md containing:
- the finding counts on separate lines: `High: N` and `Medium: N`
- each finding with its evidence,
- the verdict line in one of the forms: `VERDICT: APPROVED` or `VERDICT: CHANGES-REQUESTED`
- the strongest datum against your own verdict."""


class LaneError(Exception):
    """A lane precondition failed; the runner exits 2."""


@dataclass
class RunResult:
    rc: int
    sid: str | None
    text: str
    auto_reject: bool
    provider_error: bool
    timed_out: bool = False
    head_moved: bool = False

    @property
    def failed(self) -> bool:
        empty = (not self.text) and not self.head_moved
        return bool(self.auto_reject or empty or self.provider_error or self.rc != 0)


@dataclass
class LaneCtx:
    root: Path
    name: str

    @property
    def wt(self) -> Path:
        return self.root / ".worktrees" / f"w-{self.name}"

    @property
    def agent(self) -> Path:
        return self.wt / ".agent"

    @property
    def lane_path(self) -> Path:
        return self.agent / "lane.json"


def _detect_repo_root(cwd: Path) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise LaneError(f"not a git repository: {cwd}")
    return Path(proc.stdout.strip()).parent


def _lane_ctx(name: str, cwd: Path | None = None) -> LaneCtx:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise LaneError(f"invalid lane name: {name!r}")
    return LaneCtx(root=_detect_repo_root(cwd or Path.cwd()), name=name)


def _git(ctx: LaneCtx, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=ctx.wt, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise LaneError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _git_head(ctx: LaneCtx) -> str:
    return _git(ctx, "rev-parse", "HEAD", check=False)


def _ensure_agent_dir(ctx: LaneCtx) -> None:
    ctx.agent.mkdir(parents=True, exist_ok=True)


def _save_lane(ctx: LaneCtx, lane: dict[str, Any]) -> None:
    _ensure_agent_dir(ctx)
    tmp = ctx.agent / "lane.json.tmp"
    tmp.write_text(json.dumps(lane, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(ctx.lane_path)


def _load_lane(ctx: LaneCtx) -> dict[str, Any]:
    if not ctx.lane_path.is_file():
        raise LaneError(f"no lane.json at {ctx.lane_path}; run 'start' first")
    return json.loads(ctx.lane_path.read_text(encoding="utf-8"))


def _ensure_worktree(ctx: LaneCtx, base: str) -> bool:
    if ctx.wt.is_dir():
        if not (ctx.wt / ".git").exists():
            raise LaneError(f"{ctx.wt} exists but is not a git worktree")
        logger.info("lane %s: reusing worktree %s", ctx.name, ctx.wt)
        return False
    ctx.root.joinpath(".worktrees").mkdir(exist_ok=True)
    branch = f"lane/{ctx.name}"
    proc = subprocess.run(
        ["git", "-C", str(ctx.root), "worktree", "add", "-b", branch, str(ctx.wt), base],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["git", "-C", str(ctx.root), "worktree", "add", str(ctx.wt), base],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise LaneError(f"git worktree add failed: {proc.stderr.strip()}")
    logger.info("lane %s: created worktree %s on branch %s from %s", ctx.name, ctx.wt, branch, base)
    return True


def _run_uv_sync(ctx: LaneCtx) -> None:
    proc = subprocess.run(
        ["uv", "sync", "--all-extras", "--locked"],
        cwd=ctx.wt,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-200:]
        logger.warning("lane %s: uv sync failed (exit %d): %s", ctx.name, proc.returncode, tail)


def _run_agent_command(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: int,
    stdout: Any,
    stderr: int,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        timeout=timeout,
    )


def _run_gate_command(
    cmd: list[str], ctx: LaneCtx, stdout: Any
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, cwd=ctx.wt, stdout=stdout, stderr=subprocess.STDOUT)


def _read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _analyze_run(
    path: Path,
    rc: int,
    timed_out: bool,
    head_moved: bool,
) -> RunResult:
    sid: str | None = None
    texts: list[str] = []
    provider_error = False
    auto_reject = False
    for event in _read_events(path):
        event_sid = event.get("sessionID")
        if isinstance(event_sid, str) and event_sid:
            sid = event_sid
        if event.get("type") == "text":
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        if event.get("type") == "error":
            provider_error = True
            if "auto-reject" in json.dumps(event):
                auto_reject = True
        if event.get("type") == "tool_use":
            part = event.get("part")
            state = part.get("state") if isinstance(part, dict) else None
            if (
                isinstance(state, dict)
                and state.get("status") == "error"
                and "auto-reject" in json.dumps(state.get("error", ""))
            ):
                auto_reject = True
    return RunResult(
        rc=rc,
        sid=sid,
        text="\n".join(texts).strip(),
        auto_reject=auto_reject,
        provider_error=provider_error,
        timed_out=timed_out,
        head_moved=head_moved,
    )


def _analyze_codex_run(path: Path, rc: int, timed_out: bool, head_moved: bool) -> RunResult:
    sid = None
    text = ""
    provider_error = False
    for event in _read_events(path):
        if (
            sid is None
            and event.get("type") == "thread.started"
            and isinstance(event.get("thread_id"), str)
        ):
            sid = event["thread_id"]
        if event.get("type") == "item.completed":
            item = event.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                text = item["text"]
        if event.get("type") in {"error", "turn.failed"}:
            provider_error = True
    return RunResult(
        rc=rc,
        sid=sid,
        text=text.strip(),
        auto_reject=False,
        provider_error=provider_error,
        timed_out=timed_out,
        head_moved=head_moved,
    )


class Backend:
    name = ""

    def build_start_argv(
        self,
        ctx: LaneCtx,
        prompt: str,
        model: str,
        title: str | None,
        variant: str | None,
        *,
        review: bool = False,
    ) -> list[str]:
        raise NotImplementedError

    def build_resume_argv(
        self, ctx: LaneCtx, prompt: str, model: str, sid: str, variant: str | None
    ) -> list[str]:
        raise NotImplementedError

    def analyze_run(self, path: Path, rc: int, timed_out: bool, head_moved: bool) -> RunResult:
        raise NotImplementedError


class OpenCodeBackend(Backend):
    name = "opencode"

    def build_start_argv(
        self,
        ctx: LaneCtx,
        prompt: str,
        model: str,
        title: str | None,
        variant: str | None,
        *,
        review: bool = False,
    ) -> list[str]:
        cmd = ["opencode", "run", "--format", "json"]
        if title:
            cmd += ["--title", title]
        cmd += [prompt, "-m", model]
        if variant:
            cmd += ["--variant", variant]
        return cmd

    def build_resume_argv(
        self, ctx: LaneCtx, prompt: str, model: str, sid: str, variant: str | None
    ) -> list[str]:
        cmd = ["opencode", "run", "--format", "json", "-s", sid, prompt, "-m", model]
        if variant:
            cmd += ["--variant", variant]
        return cmd

    def analyze_run(self, path: Path, rc: int, timed_out: bool, head_moved: bool) -> RunResult:
        return _analyze_run(path, rc, timed_out, head_moved)


class CodexBackend(Backend):
    name = "codex"

    @staticmethod
    @lru_cache(maxsize=1)
    def _cache_dir() -> str:
        return subprocess.run(
            ["uv", "cache", "dir"], capture_output=True, text=True, check=True
        ).stdout.strip()

    @staticmethod
    def _add_dirs(ctx: LaneCtx) -> list[str]:
        return ["--add-dir", str(ctx.root / ".git"), "--add-dir", CodexBackend._cache_dir()]

    @staticmethod
    def _model(model: str) -> str:
        return model.removeprefix("codex:")

    def build_start_argv(
        self,
        ctx: LaneCtx,
        prompt: str,
        model: str,
        title: str | None,
        variant: str | None,
        *,
        review: bool = False,
    ) -> list[str]:
        sandbox = "read-only" if review else "workspace-write"
        cmd = ["codex", "exec", "--json", "-s", sandbox]
        if not review:
            cmd += self._add_dirs(ctx)
        cmd += ["-C", str(ctx.wt)]
        if variant:
            cmd += ["-c", f'model_reasoning_effort="{variant}"']
        cmd += ["-m", self._model(model)]
        if review:
            cmd += ["-o", str(ctx.agent / "review.md")]
        cmd.append(prompt)
        return cmd

    def build_resume_argv(
        self, ctx: LaneCtx, prompt: str, model: str, sid: str, variant: str | None
    ) -> list[str]:
        roots = json.dumps([str(ctx.root / ".git"), CodexBackend._cache_dir()])
        cmd = [
            "codex",
            "exec",
            "resume",
            sid,
            "--json",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            f"sandbox_workspace_write.writable_roots={roots}",
        ]
        if variant:
            cmd += ["-c", f'model_reasoning_effort="{variant}"']
        cmd += [
            "-m",
            self._model(model),
            prompt,
        ]
        return cmd

    def analyze_run(self, path: Path, rc: int, timed_out: bool, head_moved: bool) -> RunResult:
        return _analyze_codex_run(path, rc, timed_out, head_moved)


def _backend_for(model: str) -> Backend:
    return CodexBackend() if model.startswith("codex:") else OpenCodeBackend()


def _opencode_call(
    ctx: LaneCtx,
    out_path: Path,
    prompt: str,
    model: str,
    ns: argparse.Namespace,
    *,
    sid: str | None = None,
    title: str | None = None,
    variant: str | None = None,
    review: bool = False,
) -> RunResult:
    _ensure_agent_dir(ctx)
    before_head = _git_head(ctx)
    backend = _backend_for(model)
    if sid:
        cmd = backend.build_resume_argv(ctx, prompt, model, sid, variant)
    else:
        cmd = backend.build_start_argv(ctx, prompt, model, title, variant, review=review)
    label = f"{out_path.stem} (model {model})"
    logger.info("lane %s: %s %s", ctx.name, backend.name, label)
    timed_out = False
    rc = -1
    with out_path.open("w", encoding="utf-8") as fh:
        try:
            proc = _run_agent_command(
                cmd,
                cwd=ctx.wt,
                env={**os.environ, "PWD": str(ctx.wt)},
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=ns.timeout,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning(
                "lane %s: %s %s timed out after %ss", ctx.name, backend.name, label, ns.timeout
            )
    after_head = _git_head(ctx)
    result = backend.analyze_run(
        out_path,
        rc,
        timed_out,
        before_head != after_head and bool(before_head) and bool(after_head),
    )
    if result.timed_out and not result.text:
        logger.warning(
            "lane %s: no assistant text before the timeout; resume to continue", ctx.name
        )
    return result


def _worker_round(
    ctx: LaneCtx,
    lane: dict[str, Any],
    prompt: str,
    ns: argparse.Namespace,
    *,
    sid: str | None = None,
    variant: str | None = None,
) -> RunResult:
    idx = lane["r"]
    backend = _backend_for(lane["model"])
    filename = (
        f"{ctx.name}-r{idx}.jsonl" if backend.name == "codex" else f"oc-{ctx.name}-r{idx}.jsonl"
    )
    out_path = ctx.agent / filename
    result = _opencode_call(
        ctx,
        out_path,
        prompt,
        lane["model"],
        ns,
        sid=sid,
        title=None if sid else ctx.name,
        variant=variant,
    )
    lane["r"] = idx + 1
    lane["rounds"] = lane["r"]
    if not result.timed_out:
        lane["head"] = _git_head(ctx) or lane.get("head")
    if result.sid:
        lane["sessionID"] = result.sid
    if result.timed_out:
        lane["state"] = "timeout"
    elif result.failed:
        lane["state"] = "worker-failed"
    else:
        lane["state"] = "running"
    _save_lane(ctx, lane)
    if lane["state"] in ("worker-failed", "timeout"):
        logger.warning("lane %s: rerun with --model <other>", ctx.name)
    return result


@contextlib.contextmanager
def _gate_lock(ctx: LaneCtx) -> Iterator[None]:
    """Hold the repo-wide gate lock so lanes' gates run one at a time.

    Two parallel gates each fan pytest out over every core and the
    signal-timing tests fail under that load, so gates queue instead.
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    lock_path = ctx.root / ".worktrees" / ".delegate-gate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("lane %s: waiting for another lane's gate to finish", ctx.name)
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _run_gate(ctx: LaneCtx, ns: argparse.Namespace) -> int:
    _ensure_agent_dir(ctx)
    log_path = ctx.agent / "gate.log"
    cmd = shlex.split(ns.gate_cmd)
    with _gate_lock(ctx), log_path.open("w", encoding="utf-8") as fh:
        logger.info("lane %s: running gate: %s", ctx.name, ns.gate_cmd)
        proc = _run_gate_command(cmd, ctx, fh)
    if proc.returncode != 0:
        logger.warning(
            "lane %s: gate failed (exit %d); log: %s", ctx.name, proc.returncode, log_path
        )
    else:
        logger.info("lane %s: gate green", ctx.name)
    return proc.returncode


def _gate_loop(ctx: LaneCtx, lane: dict[str, Any], ns: argparse.Namespace) -> int:
    rc = _run_gate(ctx, ns)
    while rc != 0 and lane.get("sessionID") and lane.get("fix_rounds", 0) < ns.max_fix:
        lane["fix_rounds"] = lane.get("fix_rounds", 0) + 1
        _save_lane(ctx, lane)
        prompt = FIX_PROMPT.format(exit=rc, rules=_worker_rules(ctx.root))
        result = _worker_round(
            ctx, lane, prompt, ns, sid=lane["sessionID"], variant=lane.get("variant")
        )
        if result.timed_out or result.failed:
            logger.warning("lane %s: auto-fix round failed to run; leaving the gate red", ctx.name)
            break
        rc = _run_gate(ctx, ns)
    return rc


def _format_status(lane: dict[str, Any]) -> str:
    review = lane.get("review") or {}
    verdict = review.get("verdict") or "-"
    counts = f"{review.get('high', '-')}/{review.get('medium', '-')}"
    head = (lane.get("head") or "")[:8] or "-"
    gate_rc = "-" if lane.get("gate_rc") is None else lane["gate_rc"]
    return " | ".join(
        [
            lane["name"],
            lane.get("state") or "-",
            head,
            str(gate_rc),
            f"{verdict} {counts}",
            lane.get("model") or "-",
            str(lane.get("rounds", 0)),
        ]
    )


def _finish(ctx: LaneCtx, lane: dict[str, Any]) -> None:
    _save_lane(ctx, lane)
    print(_format_status(lane))


def _post_run(
    ctx: LaneCtx,
    lane: dict[str, Any],
    ns: argparse.Namespace,
    prompt: str,
    *,
    sid: str | None = None,
    variant: str | None = None,
) -> int:
    _worker_round(ctx, lane, prompt, ns, sid=sid, variant=variant)
    if lane.get("state") in ("worker-failed", "timeout"):
        _finish(ctx, lane)
        return 1
    rc = _gate_loop(ctx, lane, ns)
    lane["gate_rc"] = rc
    if lane.get("state") != "timeout":
        lane["state"] = "gate-green" if rc == 0 else "gate-red"
    _finish(ctx, lane)
    return 0 if rc == 0 else 1


def cmd_start(ns: argparse.Namespace) -> int:
    ctx = _lane_ctx(ns.name)
    config = _delegate_config(ctx.root)
    ns.gate_cmd = ns.gate_cmd or config["gate_command"]
    ns.base = ns.base or config["base"]
    if not ns.brief or not Path(ns.brief).is_file():
        raise LaneError(f"--brief file not found: {ns.brief}")
    _ensure_worktree(ctx, ns.base)
    _run_uv_sync(ctx)
    _ensure_agent_dir(ctx)
    shutil.copyfile(ns.brief, ctx.agent / "brief.md")
    lane: dict[str, Any] = {
        "name": ctx.name,
        "worktree": str(Path(".worktrees") / f"w-{ctx.name}"),
        "base": ns.base,
        "state": "running",
        "sessionID": None,
        "backend": _backend_for(ns.model).name,
        "model": ns.model,
        "variant": ns.variant,
        "r": 0,
        "rounds": 0,
        "fix_rounds": 0,
        "gate_rc": None,
        "head": _git_head(ctx),
        "artifacts": {
            "brief": ".agent/brief.md",
            "gate_log": ".agent/gate.log",
        },
    }
    _save_lane(ctx, lane)
    prompt = START_PROMPT.format(name=ctx.name, rules=_worker_rules(ctx.root))
    return _post_run(ctx, lane, ns, prompt, variant=ns.variant)


def cmd_resume(ns: argparse.Namespace) -> int:
    ctx = _lane_ctx(ns.name)
    config = _delegate_config(ctx.root)
    lane = _load_lane(ctx)
    sid = lane.get("sessionID")
    if not sid:
        logger.warning(
            "lane %s: no recorded sessionID; starting a fresh session for the decisions turn",
            ctx.name,
        )
    else:
        logger.info("lane %s: resuming session %s", ctx.name, sid)
    if not ns.file or not Path(ns.file).is_file():
        raise LaneError(f"--file decisions file not found: {ns.file}")
    if getattr(ns, "model", None):
        lane["model"] = ns.model
    if getattr(ns, "variant", None) is not None:
        lane["variant"] = ns.variant
    lane["backend"] = _backend_for(lane["model"]).name
    _ensure_agent_dir(ctx)
    round_idx = lane["r"]
    shutil.copyfile(ns.file, ctx.agent / f"decisions-r{round_idx}.md")
    lane["state"] = "running"
    _save_lane(ctx, lane)
    prompt = RESUME_PROMPT.format(round=round_idx, rules=_worker_rules(ctx.root))
    ns.gate_cmd = ns.gate_cmd or config["gate_command"]
    return _post_run(ctx, lane, ns, prompt, sid=sid, variant=lane.get("variant"))


def _archive_review(ctx: LaneCtx) -> int:
    existing = ctx.agent / "review.md"
    if not existing.is_file():
        return 0
    index = 0
    while (ctx.agent / f"review-r{index}.md").is_file():
        index += 1
    existing.replace(ctx.agent / f"review-r{index}.md")
    return index + 1


def _parse_review(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    verdict = re.search(r"VERDICT:\s*(\S+)", text)
    if not verdict:
        return None
    high = re.search(r"High:\s*(\d+)", text)
    medium = re.search(r"Medium:\s*(\d+)", text)
    return {
        "verdict": verdict.group(1).strip(),
        "high": int(high.group(1)) if high else None,
        "medium": int(medium.group(1)) if medium else None,
    }


def cmd_review(ns: argparse.Namespace) -> int:
    ctx = _lane_ctx(ns.name)
    lane = _load_lane(ctx)
    _ensure_agent_dir(ctx)
    review_index = _archive_review(ctx)
    model = ns.model
    if model == lane.get("model"):
        logger.warning(
            "reviewer model equals the worker model (%s); the review is not cross-provider", model
        )
    decisions = sorted(p.name for p in ctx.agent.glob("decisions-r*.md"))
    decisions_txt = f"\nRead the decision files: {', '.join(decisions)}.\n" if decisions else "\n"
    focus_txt = ""
    if ns.focus:
        if not Path(ns.focus).is_file():
            raise LaneError(f"--focus file not found: {ns.focus}")
        shutil.copyfile(ns.focus, ctx.agent / "focus.md")
        focus_txt = "\nPay special attention to .agent/focus.md.\n"
    prompt = REVIEW_PROMPT.format(
        name=ctx.name,
        decisions=decisions_txt,
        focus=focus_txt,
        base=lane.get("base", DEFAULT_BASE),
    )
    out_path = ctx.agent / f"review-{ctx.name}-r{review_index}.jsonl"
    result = _opencode_call(
        ctx, out_path, prompt, model, ns, title=f"{ctx.name}-review-r{review_index}", review=True
    )
    if result.failed:
        logger.warning(
            "lane %s: reviewer run failed (exit %s, text %s)",
            ctx.name,
            result.rc,
            bool(result.text),
        )
        _finish(ctx, lane)
        return 1
    parsed = _parse_review(ctx.agent / "review.md")
    if parsed is None:
        logger.error("lane %s: reviewer wrote no parsable review.md", ctx.name)
        _finish(ctx, lane)
        return 1
    parsed["model"] = model
    lane["review"] = parsed
    lane["state"] = "reviewed"
    _finish(ctx, lane)
    return 0


def cmd_gate(ns: argparse.Namespace) -> int:
    ctx = _lane_ctx(ns.name)
    config = _delegate_config(ctx.root)
    ns.gate_cmd = ns.gate_cmd or config["gate_command"]
    lane = _load_lane(ctx)
    rc = _run_gate(ctx, ns)
    lane["gate_rc"] = rc
    lane["head"] = _git_head(ctx) or lane.get("head")
    if lane.get("state") in ("running", "gate-green", "gate-red"):
        lane["state"] = "gate-green" if rc == 0 else "gate-red"
    _finish(ctx, lane)
    return 0 if rc == 0 else 1


def cmd_status(ns: argparse.Namespace) -> int:
    root = _detect_repo_root(Path.cwd())
    if ns.names:
        paths = [root / ".worktrees" / f"w-{n}" / ".agent" / "lane.json" for n in ns.names]
    else:
        worktrees = root / ".worktrees"
        paths = sorted(worktrees.glob("w-*/.agent/lane.json")) if worktrees.is_dir() else []
    for path in paths:
        if not path.is_file():
            logger.warning("no lane at %s", path)
            continue
        print(_format_status(json.loads(path.read_text(encoding="utf-8"))))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    config = _delegate_config(_detect_repo_root(Path.cwd()))
    parser = argparse.ArgumentParser(
        prog="delegate",
        description="private deterministic subagent delegation runner",
    )
    parser.add_argument(
        "--gate-cmd", default=config["gate_command"], help="gate command to run in the worktree"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="per-worker-call timeout in seconds",
    )
    parser.add_argument(
        "--max-fix",
        type=int,
        default=DEFAULT_MAX_FIX,
        help="gate-failure auto-fix rounds per post-run",
    )
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--gate-cmd", default=argparse.SUPPRESS)
    shared.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    shared.add_argument("--max-fix", type=int, default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser(
        "start",
        parents=[shared],
        help="create the worktree, sync, copy the brief, run the worker, post-run",
    )
    p_start.add_argument("name")
    p_start.add_argument("--brief", required=True)
    p_start.add_argument("--model", required=True)
    p_start.add_argument("--variant", default=None)
    p_start.add_argument("--base", default=config["base"])
    p_start.set_defaults(func=cmd_start)

    p_resume = sub.add_parser(
        "resume",
        parents=[shared],
        help="resume the recorded session with a decisions file, then post-run",
    )
    p_resume.add_argument("name")
    p_resume.add_argument("--file", required=True)
    p_resume.add_argument("--model", default=None)
    p_resume.add_argument("--variant", default=None)
    p_resume.set_defaults(func=cmd_resume)

    p_review = sub.add_parser(
        "review",
        parents=[shared],
        help="run a fresh cross-provider reviewer session over the lane diff",
    )
    p_review.add_argument("name")
    p_review.add_argument("--focus", default=None)
    p_review.add_argument("--model", required=True)
    p_review.set_defaults(func=cmd_review)

    p_status = sub.add_parser("status", help="one status line per lane")
    p_status.add_argument("names", nargs="*")
    p_status.set_defaults(func=cmd_status)

    p_gate = sub.add_parser("gate", parents=[shared], help="run the gate only, then record it")
    p_gate.add_argument("name")
    p_gate.set_defaults(func=cmd_gate)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        parser = _build_parser()
        ns = parser.parse_args(argv)
        return int(ns.func(ns))
    except LaneError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
