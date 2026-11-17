from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from pathlib import Path

from phistory.models import CaptureTarget, TapMode


def tap_command(
    target: CaptureTarget,
    prompt_path: Path,
    tap_output_dir: Path,
    run_env: Mapping[str, str] | None = None,
    *,
    proxy_port: int = 0,
    proxy_host: str | None = None,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "claude_tap",
        "run",
        target.agent.tap_client,
        *_tap_yolo_args(target),
        "--export-prompt",
        str(prompt_path),
        "--no-live",
        "--no-open",
        "--no-update-check",
        "--output-dir",
        str(tap_output_dir),
        *_tap_mode_args(target),
        *(["--target", target.agent.tap_target] if target.agent.tap_target else []),
        *(["--port", str(proxy_port)] if proxy_port else []),
        *(["--host", proxy_host] if proxy_host else []),
        "--",
        *[_expand_capture_path(arg, run_env) for arg in upstream_client_args(target.variant.run_args)],
    ]


def _expand_capture_path(arg: str, env: Mapping[str, str] | None) -> str:
    match = re.match(r"^\$(PHISTORY_[A-Z_]+)(?=/|$)", arg)
    if not match:
        return arg
    key = match[1]
    if env is None or key not in env:
        raise ValueError(f"capture command requires isolated path {key}")
    return env[key] + arg[match.end() :]


def upstream_client_args(run_args: tuple[str, ...]) -> list[str]:
    args = list(run_args)
    if args and args[0] == "--no-yolo":
        args.pop(0)
    if args and args[0] == "--":
        args.pop(0)
    return args


def _tap_mode_args(target: CaptureTarget) -> list[str]:
    tap_mode = effective_tap_mode(target)
    if tap_mode == "auto":
        return []
    return ["--mode", tap_mode]


def effective_tap_mode(target: CaptureTarget) -> TapMode:
    return target.variant.tap_mode or target.agent.tap_mode


def _tap_yolo_args(target: CaptureTarget) -> list[str]:
    if target.variant.run_args[:1] == ("--no-yolo",):
        return ["--no-yolo"]
    return []
