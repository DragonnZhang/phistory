from __future__ import annotations

import http.cookiejar
import json
import os
import re
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
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    created = _rpc_when_ready(port, "session.create", payload, process, opener, context.tap_output_dir / "client.log")
    session_id = str(created["sessionId"])
    _rpc(
        port,
        "session.prompt",
        {
            "sessionId": session_id,
            "mode": "queue",
            "content": [{"type": "text", "text": PROMPT}],
        },
        opener,
    )


def _rpc_when_ready(
    port: int,
    method: str,
    payload: dict[str, object],
    process: subprocess.Popen,
    opener: urllib.request.OpenerDirector,
    log_path: Path,
) -> dict[str, object]:
    deadline = time.monotonic() + SERVER_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"DSH Web exited before becoming ready ({process.returncode})")
        try:
            return _rpc(port, method, payload, opener)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
                _authenticate(port, opener, log_path)
            time.sleep(0.25)
    raise RuntimeError(f"DSH Web did not become ready: {last_error}")


def _authenticate(port: int, opener: urllib.request.OpenerDirector, log_path: Path) -> None:
    if not log_path.exists():
        return
    log = log_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"dsh web: (http://127\.0\.0\.1:{port}/\?token=[A-Za-z0-9_-]+)(?=\s|$)", log)
    if match:
        with opener.open(match[1], timeout=5) as response:
            response.read()


def _rpc(
    port: int,
    method: str,
    payload: dict[str, object],
    opener: urllib.request.OpenerDirector,
) -> dict[str, object]:
    endpoints = (
        (method.replace(".", "/"), {"args": {"request": payload}}),
        (method, payload),
    )
    for index, (endpoint, wire_payload) in enumerate(endpoints):
        envelope = {
            "type": "client-request",
            "rpcId": f"phistory-{method}",
            "method": endpoint,
            "payload": wire_payload,
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/{endpoint}",
            data=json.dumps(envelope).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with opener.open(request, timeout=5) as response:
                body = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or index == len(endpoints) - 1:
                raise
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
