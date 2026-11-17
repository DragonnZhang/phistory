from __future__ import annotations

import socket
import subprocess
from pathlib import Path

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import tap_command
from phistory.drivers.local_server import stop_process, wait_for_prompt
from phistory.models import CommandResult

PROMPT = "Reply with one short sentence."


def run_first_request(context: CaptureRunContext) -> CaptureExecution:
    env = {**context.env, "PHISTORY_CAPTURE_ARTIFACT_DIR": str(context.prompt_path.parent)}
    proxy_port = 0
    if context.target.agent.id == "orkas":
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            proxy_port = check.getsockname()[1]
        env["PHISTORY_TAP_PORT"] = str(proxy_port)
        env["PHISTORY_ELECTRON_DRIVER"] = str(Path(__file__).with_name("orkas_task.cjs"))
    argv = tap_command(
        context.target,
        context.prompt_path,
        context.tap_output_dir,
        env,
        **({"proxy_port": proxy_port} if proxy_port else {}),
    )
    context.tap_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = context.tap_output_dir / "client.log"
    failure = None
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            argv,
            cwd=context.work_dir,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if context.target.variant.stdin_text is not None else subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        try:
            if process.stdin is not None:
                process.stdin.write(context.target.variant.stdin_text)
                process.stdin.close()
            wait_for_prompt(
                context.tap_output_dir,
                process,
                next(
                    iter(context.target.agent.required_user_text or context.target.agent.required_system_text), PROMPT
                ),
                timeout=context.target.agent.capture_timeout_seconds,
                minimum_tools=context.target.agent.capture_min_tools,
                required_system_text=context.target.agent.required_system_text,
                required_user_text=context.target.agent.required_user_text,
                required_tool_name_fragments=context.target.agent.required_tool_name_fragments,
            )
        except Exception as exc:
            failure = exc
        finally:
            stop_process(process)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if failure is not None:
        raise RuntimeError(f"{failure}\nNative task log:\n{output[-6000:]}") from failure
    return CaptureExecution(tuple(argv), CommandResult(tuple(argv), process.returncode or 0, output, ""))
