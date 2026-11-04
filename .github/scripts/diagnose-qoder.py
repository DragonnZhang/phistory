from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from phistory import capture, packages
from phistory.drivers import CaptureExecution
from phistory.drivers.common import tap_command
from phistory.drivers.oneshot import _run
from phistory.models import CaptureTarget
from phistory.registry import get_agent


def safe_text(text: str) -> str:
    text = capture._redact_secrets(text, (os.environ.get("QODER_PERSONAL_ACCESS_TOKEN", ""),))
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", text)
    text = re.sub(
        r"(?i)((?:token|password|secret|api[_-]?key|authorization)[\w-]*\s*[:=]\s*)[^\s,;]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(r"https?://[^\s\"'<>]+", lambda m: urlsplit(m[0]).hostname or "<url>", text)
    text = re.sub(r"[A-Za-z0-9_+/=-]{28,}", "<opaque>", text)
    return text[:1200]


def diagnose(context):
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir)
    result = _run(argv, context, context.env)
    print(json.dumps({"version": context.target.version.version, "exit_code": result.returncode}), flush=True)
    logs = sorted((Path(context.env["HOME"]) / ".qoder/logs").glob("runs/*/qodercli.log"))
    print(json.dumps({"internal_log_count": len(logs)}), flush=True)
    relevant = re.compile(
        r"ERROR|WARN|\[qoder-server-request\]|\[httpdns|\[API |models\.list|process\.exiting|"
        r"Error:|Error\b|QoderClient|model\.resolve|model\.selected|startup\.diagnostic"
    )
    for log in logs:
        lines = log.read_text(errors="replace").splitlines()
        selected = [line for line in lines if relevant.search(line)]
        print(json.dumps({"log_name": log.name, "lines": len(lines), "selected": len(selected)}), flush=True)
        for line in selected[-70:]:
            print(safe_text(line), flush=True)
    for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        for line in text.splitlines()[-30:]:
            print(stream + ": " + safe_text(line), flush=True)
    return CaptureExecution(tuple(argv), result)


def main() -> int:
    if platform.system() != "Linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Qoder diagnostics run only in Linux GitHub Actions")
    capture.run_capture = diagnose
    agent = get_agent("qoder")
    variant = replace(
        agent.default_variant,
        run_args=(*agent.default_variant.run_args[:-1], "--debug", agent.default_variant.run_args[-1]),
    )
    agent = replace(
        agent,
        default_variant=variant,
        extra_env={**agent.extra_env, "QODER_STARTUP_TRACE": "1", "QODER_E2E_MODEL_DIAGNOSTICS": "1"},
    )
    directory = Path(os.environ["RUNNER_TEMP"]) / "qoder-diagnostics"
    results = []
    for version in packages.versions_between(agent, "1.1.81", "1.1.82"):
        print(f"Diagnosing qoder {version.version}", flush=True)
        result = capture.capture_target(
            CaptureTarget(agent, version, variant, directory / "captures"),
            cache_dir=directory / "cache",
            force=True,
            keep_tap=True,
            extract_static=False,
        )
        results.append({"version": version.version, "status": result.status})
        print(json.dumps(results[-1]), flush=True)
    print(json.dumps({"results": results}), flush=True)
    return int(any(result["status"] != "captured" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
