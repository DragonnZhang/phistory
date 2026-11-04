from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from phistory import capture, packages
from phistory.drivers import CaptureExecution
from phistory.drivers.common import tap_command
from phistory.drivers.oneshot import _run
from phistory.models import CaptureTarget
from phistory.registry import get_agent
from phistory.subprocesses import run

INSTALL_AGENT = packages.install_agent


def diagnostic_install(agent, version, install_dir):
    bin_dir = INSTALL_AGENT(agent, version, install_dir)
    bundle = install_dir / "node_modules/@qoder-ai/qodercli/bundle/qodercli.js"
    source = bundle.read_text()
    marker = "PHISTORY_MODEL_DIAGNOSTIC"
    if marker not in source:
        anchor = "if(!t||t.source!==e.model.source||!1===t.enabled)throw new"
        assert source.count(anchor) == 1
        observation = (
            'process.stderr.write("PHISTORY_MODEL_DIAGNOSTIC " + JSON.stringify({'
            "purpose:A.purpose,requestedKey:e.model.key,requestedSource:e.model.source,"
            "found:!!t,actualKey:t?.key,actualSource:t?.source,enabled:t?.enabled}) + "
            '"\\n");'
        )
        bundle.write_text(source.replace(anchor, observation + anchor))
    return bin_dir


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
        r"Error:|Error\b|QoderClient|model\.resolve|model\.selected|\[qoder-model-resolver\]|"
        r"\[auth\] event=auth\.init|\[e2e-lite-catalog\]"
    )
    for log in logs:
        lines = log.read_text(errors="replace").splitlines()
        selected = [line for line in lines if relevant.search(line)]
        print(json.dumps({"log_name": log.name, "lines": len(lines), "selected": len(selected)}), flush=True)
        for line in selected[-140:]:
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
        run_args=(
            *agent.default_variant.run_args[:-1],
            "--output-format", "json",
            agent.default_variant.run_args[-1],
        ),
    )
    directory = Path(os.environ["RUNNER_TEMP"]) / "qoder-diagnostics"
    version = packages.versions_between(agent, "1.1.82", "1.1.82")[0]
    target = CaptureTarget(agent, version, variant, directory / "captures")
    inherited = capture._resolve_inherited_env(agent.id, agent.inherited_env)
    with capture._without_parent_env(agent.inherited_env.values()):
        bin_dir = packages.install_agent(
            agent, version.version,
            directory / "cache" / "installs" / agent.id / version.version,
        )
    with TemporaryDirectory(prefix="qoder-model-list-") as temporary:
        home = Path(temporary) / "home"
        work = Path(temporary) / "work"
        work.mkdir()
        env = capture._capture_env(target, bin_dir, home, inherited_env=inherited)
        listing = run(
            [str(bin_dir / packages.agent_executable(agent)), "--list-models"],
            cwd=work, env=env, timeout=120, check=False, inherit_env=False,
        )
        print(json.dumps({"probe": "native --list-models (no prompt)", "exit_code": listing.returncode}), flush=True)
        for stream, output in (("stdout", listing.stdout), ("stderr", listing.stderr)):
            for line in output.splitlines()[-120:]:
                print(stream + ": " + safe_text(line), flush=True)
    print(f"Diagnosing qoder {version.version} with unmodified CLI and JSON output", flush=True)
    result = capture.capture_target(
        target, cache_dir=directory / "cache", force=True,
        keep_tap=True, extract_static=False,
    )
    print(json.dumps({"version": version.version, "status": result.status}), flush=True)
    return int(result.status != "captured")


if __name__ == "__main__":
    raise SystemExit(main())
