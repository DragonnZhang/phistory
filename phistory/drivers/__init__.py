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
    from phistory.drivers.first_request import run_first_request
    from phistory.drivers.interactive import run_interactive_pty
    from phistory.drivers.local_apps import run_local_task_api
    from phistory.drivers.oneshot import run_oneshot
    from phistory.drivers.openagent import run_openagent_store

    runners: dict[str, CaptureRunner] = {
        "oneshot": run_oneshot,
        "dsh-web": run_dsh_web,
        "openagent-store": run_openagent_store,
        "local-task-api": run_local_task_api,
        "first-request": run_first_request,
        "interactive-pty": run_interactive_pty,
    }
    return runners[context.target.variant.driver](context)
