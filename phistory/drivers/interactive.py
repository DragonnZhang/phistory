from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import tap_command
from phistory.drivers.first_request import PROMPT
from phistory.drivers.local_server import has_prompt_request, stop_process
from phistory.models import CommandResult

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def run_interactive_pty(context: CaptureRunContext) -> CaptureExecution:
    ready = context.target.variant.pty_ready_text
    if not ready:
        raise ValueError("interactive captures require a native ready marker")
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir, context.env)
    context.tap_output_dir.mkdir(parents=True, exist_ok=True)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    process = None
    output = bytearray()
    failure = None
    submitted = False
    agent = context.target.agent
    try:
        process = subprocess.Popen(
            argv,
            cwd=context.work_dir,
            env={**context.env, "TERM": "xterm-256color"},
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + agent.capture_timeout_seconds
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    output.extend(chunk)
            if not submitted and ready in ANSI.sub("", output.decode("utf-8", errors="replace")):
                os.write(master, (PROMPT + "\r").encode())
                submitted = True
            if submitted and has_prompt_request(
                context.tap_output_dir,
                PROMPT,
                minimum_tools=agent.capture_min_tools,
                required_system_text=agent.required_system_text,
                required_user_text=agent.required_user_text,
            ):
                break
            if process.poll() is not None:
                raise RuntimeError(f"native interactive client exited before its task request ({process.returncode})")
        else:
            raise TimeoutError("native interactive task did not emit its primary request")
    except Exception as exc:
        failure = exc
    finally:
        if process is not None:
            stop_process(process)
        os.close(master)
        if slave >= 0:
            os.close(slave)
    text = output.decode("utf-8", errors="replace")
    (context.tap_output_dir / "client.log").write_text(text, encoding="utf-8")
    if failure is not None:
        raise RuntimeError(f"{failure}\nNative interactive log:\n{text[-6000:]}") from failure
    return CaptureExecution(tuple(argv), CommandResult(tuple(argv), process.returncode or 0, text, ""))
