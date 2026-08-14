from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import tap_command
from phistory.models import CommandResult

SERVER_TIMEOUT_SECONDS = 60
CAPTURE_TIMEOUT_SECONDS = 90
PROMPT = "Reply with one short sentence."


def run_dsh_web(context: CaptureRunContext) -> CaptureExecution:
    port = _free_port()
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir)
    argv.extend(("--host", "127.0.0.1", "--port", str(port)))
    context.tap_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = context.tap_output_dir / "client.log"
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            argv,
            cwd=context.work_dir,
            env=context.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            _create_and_prompt_session(context, port, process)
            _wait_for_prompt_trace(context.tap_output_dir, process)
        finally:
            _stop_process(process)
    stdout = log_path.read_text(encoding="utf-8", errors="replace")
    if time.monotonic() - started > CAPTURE_TIMEOUT_SECONDS:
        stdout += "\nDSH Web capture exceeded its timeout."
    result = CommandResult(tuple(argv), process.returncode or 0, stdout, "")
    return CaptureExecution(tuple(argv), result)


def _create_and_prompt_session(context: CaptureRunContext, port: int, process: subprocess.Popen) -> None:
    payload: dict[str, object] = {
        "sessionId": f"phistory-{context.target.variant.id}",
        "cwd": str(context.work_dir),
    }
    mode = context.target.variant.dimensions.get("mode")
    if mode:
        payload["agentPreset"] = mode
    created = _rpc_when_ready(port, "session.create", payload, process)
    session_id = str(created["sessionId"])
    _rpc(
        port,
        "session.prompt",
        {
            "sessionId": session_id,
            "mode": "queue",
            "content": [{"type": "text", "text": PROMPT}],
        },
    )


def _rpc_when_ready(
    port: int,
    method: str,
    payload: dict[str, object],
    process: subprocess.Popen,
) -> dict[str, object]:
    deadline = time.monotonic() + SERVER_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"DSH Web exited before becoming ready ({process.returncode})")
        try:
            return _rpc(port, method, payload)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"DSH Web did not become ready: {last_error}")


def _rpc(port: int, method: str, payload: dict[str, object]) -> dict[str, object]:
    envelope = {
        "type": "client-request",
        "rpcId": f"phistory-{method}",
        "method": method,
        "payload": payload,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/{method}",
        data=json.dumps(envelope).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.loads(response.read())
    result = body.get("result") or {}
    if not result.get("ok"):
        raise RuntimeError(f"DSH {method} failed: {result.get('error')}")
    return result.get("value") or {}


def _wait_for_prompt_trace(tap_output_dir: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _has_prompt_request(tap_output_dir):
            return
        if process.poll() is not None:
            raise RuntimeError(f"DSH Web exited before sending a prompt request ({process.returncode})")
        time.sleep(0.25)
    raise RuntimeError("DSH Web did not emit a prompt-bearing request")


def _has_prompt_request(tap_output_dir: Path) -> bool:
    for trace_path in tap_output_dir.glob("*/trace_*.jsonl"):
        try:
            lines = trace_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = (record.get("request") or {}).get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    continue
            if (
                isinstance(body, dict)
                and any(key in body for key in ("messages", "input", "system", "instructions"))
                and PROMPT in json.dumps(body, ensure_ascii=False)
            ):
                return True
    return False


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=20)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
