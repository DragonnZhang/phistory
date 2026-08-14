from __future__ import annotations

import sys
from pathlib import Path

from phistory.models import CaptureTarget


def tap_command(target: CaptureTarget, prompt_path: Path, tap_output_dir: Path) -> list[str]:
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
        "--",
        *upstream_client_args(target.variant.run_args),
    ]


def upstream_client_args(run_args: tuple[str, ...]) -> list[str]:
    args = list(run_args)
    if args and args[0] == "--no-yolo":
        args.pop(0)
    if args and args[0] == "--":
        args.pop(0)
    return args


def _tap_mode_args(target: CaptureTarget) -> list[str]:
    if target.agent.tap_mode == "auto":
        return []
    return ["--mode", target.agent.tap_mode]


def _tap_yolo_args(target: CaptureTarget) -> list[str]:
    if target.variant.run_args[:1] == ("--no-yolo",):
        return ["--no-yolo"]
    return []
