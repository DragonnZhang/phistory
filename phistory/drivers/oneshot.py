from __future__ import annotations

import time

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import tap_command
from phistory.storage import remove_if_exists
from phistory.subprocesses import run

CAPTURE_TIMEOUT_SECONDS = 1800


def run_oneshot(context: CaptureRunContext) -> CaptureExecution:
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir)
    env = context.env
    result = _run(argv, context, env)
    if _needs_claude_session_persistence_retry(context, result):
        _reset_output(context)
        argv = _without_arg(argv, "--no-session-persistence")
        result = _run(argv, context, env)
    if _needs_codex_api_key_retry(context, result):
        _reset_output(context)
        env = {**env, "OPENAI_API_KEY": "phistory-fake-api-key"}
        result = _run(argv, context, env)
    if _needs_antigravity_model_retry(context, result):
        _reset_output(context)
        argv = _without_arg_and_value(argv, "--model")
        result = _run(argv, context, env)
    for _ in range(2):
        if not _needs_prompt_retry(result, context.prompt_path):
            break
        _reset_output(context)
        time.sleep(1)
        result = _run(argv, context, env)
    return CaptureExecution(tuple(argv), result)


def _run(argv: list[str], context: CaptureRunContext, env: dict[str, str]):
    return run(
        argv,
        cwd=context.work_dir,
        env=env,
        timeout=CAPTURE_TIMEOUT_SECONDS,
        check=False,
    )


def _reset_output(context: CaptureRunContext) -> None:
    remove_if_exists(context.tap_output_dir)
    context.prompt_path.unlink(missing_ok=True)


def _needs_claude_session_persistence_retry(context: CaptureRunContext, result) -> bool:
    if context.target.agent.id != "claude-code" or result.returncode == 0:
        return False
    output = f"{result.stderr}\n{result.stdout}"
    return "unknown option '--no-session-persistence'" in output


def _needs_codex_api_key_retry(context: CaptureRunContext, result) -> bool:
    if context.target.agent.id != "codex" or result.returncode == 0:
        return False
    output = f"{result.stderr}\n{result.stdout}"
    return "Missing OpenAI API key" in output


def _needs_antigravity_model_retry(context: CaptureRunContext, result) -> bool:
    if context.target.agent.id != "antigravity" or result.returncode == 0:
        return False
    output = f"{result.stderr}\n{result.stdout}"
    return "flags provided but not defined: -model" in output


def _needs_prompt_retry(result, prompt_path) -> bool:
    if prompt_path.exists():
        return False
    output = f"{result.stderr}\n{result.stdout}"
    return any(
        message in output
        for message in (
            "no prompt-bearing request found in trace",
            "no valid records found in trace file",
        )
    )


def _without_arg(argv: list[str], value: str) -> list[str]:
    return [arg for arg in argv if arg != value]


def _without_arg_and_value(argv: list[str], value: str) -> list[str]:
    out = []
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg == value:
            skip_next = True
            continue
        out.append(arg)
    return out
