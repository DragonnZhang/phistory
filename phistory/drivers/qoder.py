from __future__ import annotations

import json
import re
from pathlib import Path

from phistory.drivers import CaptureRunContext

_MODEL_FAILURE = re.compile(
    r'\bERROR[^\r\n]*\[ModelNotFoundError\]: ([a-z_]+ model "[^"\r\n]{1,160}" is unavailable\.)'
)
_BOOTSTRAP_MODE = "model-availability"


def prepare_qoder_env(context: CaptureRunContext, env: dict[str, str]) -> dict[str, str]:
    if context.target.agent.id != "qoder" or env.get("PHISTORY_QODER_CAPTURE_BOOTSTRAP") != _BOOTSTRAP_MODE:
        return env
    hook = Path(__file__).with_name("qoder_bootstrap.cjs")
    return {
        **env,
        "NODE_OPTIONS": f"{env.get('NODE_OPTIONS', '')} --require={json.dumps(str(hook))}".strip(),
        "PHISTORY_QODER_CAPTURE_BOOTSTRAP_REPORT": str(_bootstrap_report(context)),
    }


def qoder_bootstrap_patches(context: CaptureRunContext) -> tuple[str, ...]:
    if context.target.agent.id != "qoder":
        return ()
    try:
        report = json.loads(_bootstrap_report(context).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if (
        not isinstance(report, dict)
        or report.get("mode") != _BOOTSTRAP_MODE
        or report.get("model") != "auto"
        or report.get("enable") is not True
    ):
        return ()
    return (
        "Capture-only mock: enabled the existing Qoder Auto catalog entry for request construction. "
        "The server's other model metadata and the CLI's default model selection were preserved; "
        "this does not verify real model access.",
    )


def _bootstrap_report(context: CaptureRunContext) -> Path:
    return context.tap_output_dir / "qoder-bootstrap.json"


def qoder_log_offsets(context: CaptureRunContext) -> dict[Path, int]:
    if context.target.agent.id != "qoder" or not (home := context.env.get("HOME")):
        return {}
    offsets = {}
    for path in (Path(home) / ".qoder/logs/runs").glob("*/qodercli.log"):
        try:
            if path.is_file() and not path.is_symlink():
                offsets[path] = path.stat().st_size
        except OSError:
            continue
    return offsets


def qoder_model_failure(context: CaptureRunContext, offsets: dict[Path, int]) -> str | None:
    for path, size in reversed(list(qoder_log_offsets(context).items())):
        start = offsets.get(path, 0)
        if size == start:
            continue
        if size < start:
            start = 0
        try:
            with path.open("rb") as log:
                log.seek(max(start, size - 65536))
                tail = log.read(65536).decode("utf-8", errors="replace")
        except OSError:
            continue
        failures = _MODEL_FAILURE.findall(tail)
        if failures:
            # Native logs may contain credentials; expose only the recognized model error.
            return f"Qoder CLI model selection failed: {failures[-1]}"
    return None
