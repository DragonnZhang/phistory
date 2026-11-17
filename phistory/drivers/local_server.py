from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path


def has_prompt_request(tap_output_dir: Path, prompt: str) -> bool:
    for trace in tap_output_dir.glob("*/trace_*.jsonl"):
        try:
            lines = trace.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                body = (json.loads(line).get("request") or {}).get("body")
                if isinstance(body, str):
                    body = json.loads(body)
            except (json.JSONDecodeError, AttributeError):
                continue
            if (
                isinstance(body, dict)
                and any(key in body for key in ("messages", "input", "system", "instructions"))
                and prompt in json.dumps(body, ensure_ascii=False)
            ):
                return True
    return False


def wait_for_prompt(tap_output_dir: Path, process: subprocess.Popen, prompt: str, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if has_prompt_request(tap_output_dir, prompt):
            return
        if process.poll() is not None:
            raise RuntimeError(f"local server exited before sending a model request ({process.returncode})")
        time.sleep(0.25)
    raise RuntimeError("local server did not emit a prompt-bearing model request")


def stop_process(process: subprocess.Popen) -> None:
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
