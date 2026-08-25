from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from phistory.models import CaptureTarget, CommandResult


@dataclass(frozen=True)
class CaptureRunContext:
    target: CaptureTarget
    prompt_path: Path
    tap_output_dir: Path
    work_dir: Path
    env: dict[str, str]


@dataclass(frozen=True)
class CaptureExecution:
    command: tuple[str, ...]
    result: CommandResult
    compatibility_patches: tuple[str, ...] = ()


CaptureRunner = Callable[[CaptureRunContext], CaptureExecution]


def run_capture(context: CaptureRunContext) -> CaptureExecution:
    from phistory.drivers.dsh_web import run_dsh_web
    from phistory.drivers.oneshot import run_oneshot

    runners: dict[str, CaptureRunner] = {
        "oneshot": run_oneshot,
        "dsh-web": run_dsh_web,
    }
    return runners[context.target.variant.driver](context)
