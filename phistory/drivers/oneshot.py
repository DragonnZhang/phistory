from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import effective_tap_mode, tap_command
from phistory.packages import agent_executable
from phistory.storage import remove_if_exists
from phistory.subprocesses import run

CAPTURE_TIMEOUT_SECONDS = 1800
QODER_CAPTURE_TIMEOUT_SECONDS = 180
LEGACY_CLAUDE_FORWARD_PATCH = (
    "Claude Code does not accept inline --settings JSON; stripped claude-tap's injected forward-proxy settings "
    "argument and relied on the equivalent proxy and CA environment."
)


def run_oneshot(context: CaptureRunContext) -> CaptureExecution:
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir)
    env, compatibility_patches = _prepare_claude_forward_compat(context, context.env)
    result = _run(argv, context, env)
    if _needs_claude_session_persistence_retry(context, result):
        _reset_output(context)
        argv = _without_arg(argv, "--no-session-persistence")
        result = _run(argv, context, env)
    if _needs_qoder_session_persistence_retry(context, result):
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
    if _needs_qwen_output_format_retry(context, result):
        _reset_output(context)
        argv = _without_arg_and_value(argv, "--output-format")
        result = _run(argv, context, env)
    for _ in range(2):
        if not _needs_prompt_retry(result, context.prompt_path):
            break
        _reset_output(context)
        time.sleep(1)
        result = _run(argv, context, env)
    return CaptureExecution(tuple(argv), result, compatibility_patches)


def _prepare_claude_forward_compat(
    context: CaptureRunContext,
    env: dict[str, str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    target = context.target
    if target.agent.id != "claude-code" or effective_tap_mode(target) != "forward":
        return env, ()
    executable = shutil.which(agent_executable(target.agent), path=env.get("PATH"))
    if executable is None or _claude_supports_inline_settings(Path(executable), context, env):
        return env, ()

    wrapper_dir = context.work_dir / ".phistory-forward-bin"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = wrapper_dir / agent_executable(target.agent)
    wrapper.write_text(_legacy_claude_wrapper(Path(executable)), encoding="utf-8")
    wrapper.chmod(0o755)
    return (
        {**env, "PATH": f"{wrapper_dir}{os.pathsep}{env.get('PATH', '')}"},
        (LEGACY_CLAUDE_FORWARD_PATCH,),
    )


def _claude_supports_inline_settings(executable: Path, context: CaptureRunContext, env: dict[str, str]) -> bool:
    result = run(
        [str(executable), "--help"],
        cwd=context.work_dir,
        env=env,
        timeout=30,
        check=False,
        inherit_env=False,
    )
    help_text = f"{result.stdout}\n{result.stderr}"
    return "--settings <file-or-json>" in help_text


def _legacy_claude_wrapper(executable: Path) -> str:
    return f"""#!{sys.executable}
from __future__ import annotations

import os
import sys

executable = {str(executable)!r}
args: list[str] = []
skip_next = False
for arg in sys.argv[1:]:
    if skip_next:
        skip_next = False
        continue
    if arg == "--settings":
        skip_next = True
        continue
    if arg.startswith("--settings="):
        continue
    args.append(arg)
os.execv(executable, [executable, *args])
"""


def _run(argv: list[str], context: CaptureRunContext, env: dict[str, str]):
    return run(
        argv,
        cwd=context.work_dir,
        env=env,
        timeout=_capture_timeout(context),
        check=False,
        inherit_env=False,
    )


def _capture_timeout(context: CaptureRunContext) -> int:
    if context.target.agent.id == "qoder":
        return QODER_CAPTURE_TIMEOUT_SECONDS
    return CAPTURE_TIMEOUT_SECONDS


def _reset_output(context: CaptureRunContext) -> None:
    remove_if_exists(context.tap_output_dir)
    context.prompt_path.unlink(missing_ok=True)


def _needs_claude_session_persistence_retry(context: CaptureRunContext, result) -> bool:
    if context.target.agent.id != "claude-code" or result.returncode == 0:
        return False
    output = f"{result.stderr}\n{result.stdout}"
    return "unknown option '--no-session-persistence'" in output


def _needs_qoder_session_persistence_retry(context: CaptureRunContext, result) -> bool:
    if context.target.agent.id != "qoder" or result.returncode == 0:
        return False
    return _needs_prompt_retry(result, context.prompt_path)


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


def _needs_qwen_output_format_retry(context: CaptureRunContext, result) -> bool:
    if context.target.agent.id != "qwen-code" or result.returncode == 0:
        return False
    output = f"{result.stderr}\n{result.stdout}"
    return "Unknown arguments: output-format, outputFormat" in output


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
