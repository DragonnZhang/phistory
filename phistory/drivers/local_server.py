from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from claude_tap.prompt_snapshot import snapshot_from_records

from phistory.request_selection import matches_prompt_surface


def has_prompt_request(
    tap_output_dir: Path,
    prompt: str,
    *,
    minimum_tools: int = 0,
    required_system_text: tuple[str, ...] = (),
    required_user_text: tuple[str, ...] = (),
) -> bool:
    for trace in tap_output_dir.glob("*/trace_*.jsonl"):
        try:
            lines = trace.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
                body = (record.get("request") or {}).get("body")
                if isinstance(body, str):
                    body = json.loads(body)
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(body, dict) and prompt in json.dumps(body, ensure_ascii=False):
                try:
                    snapshot = snapshot_from_records([record])
                except ValueError:
                    continue
                if not matches_prompt_surface(
                    snapshot,
                    minimum_tools=minimum_tools,
                    required_system_text=required_system_text,
                    required_user_text=required_user_text,
                ):
                    continue
                if snapshot.system_prompt or snapshot.developer_prompt or snapshot.user_message:
                    return True
    return False


def wait_for_prompt(
    tap_output_dir: Path,
    process: subprocess.Popen,
    prompt: str,
    timeout: int = 90,
    *,
    minimum_tools: int = 0,
    required_system_text: tuple[str, ...] = (),
    required_user_text: tuple[str, ...] = (),
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if has_prompt_request(
            tap_output_dir,
            prompt,
            minimum_tools=minimum_tools,
            required_system_text=required_system_text,
            required_user_text=required_user_text,
        ):
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
