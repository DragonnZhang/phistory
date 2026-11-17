from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import tap_command
from phistory.drivers.local_server import has_prompt_request, stop_process, wait_for_prompt
from phistory.models import CommandResult

PROMPT = "Reply with one short sentence."


class LocalApi:
    def __init__(self, port: int, deadline: float):
        self.base = f"http://127.0.0.1:{port}"
        self.deadline = deadline
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.headers = {"Content-Type": "application/json"}

    def remaining(self, limit: float) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native application capture exceeded its time budget")
        return min(limit, remaining)

    def request(self, path: str, body: dict | None = None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self.headers,
            method="POST" if body is not None else "GET",
        )
        with self.opener.open(request, timeout=self.remaining(90)) as response:
            return json.load(response)

    def wait_ready(self, process: subprocess.Popen) -> None:
        deadline = time.monotonic() + self.remaining(90)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"native application exited during startup ({process.returncode})")
            try:
                with self.opener.open(self.base + "/", timeout=2):
                    return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        raise RuntimeError("native application did not become ready")


def run_local_task_api(context: CaptureRunContext) -> CaptureExecution:
    deadline = time.monotonic() + context.target.agent.capture_timeout_seconds
    with socket.socket() as check:
        check.bind(("127.0.0.1", 0))
        port = check.getsockname()[1]
    env = {**context.env, "PHISTORY_SERVER_PORT": str(port)}
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir, env)
    context.tap_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = context.tap_output_dir / "client.log"
    failure = None
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            argv,
            cwd=context.work_dir,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            api = LocalApi(port, deadline)
            api.wait_ready(process)
            try:
                _submit_task(context, api, log)
            except Exception as exc:
                if not has_prompt_request(context.tap_output_dir, PROMPT):
                    raise
                print(f"Native task returned after its captured request: {exc}", file=log, flush=True)
            if not has_prompt_request(context.tap_output_dir, PROMPT):
                wait_for_prompt(context.tap_output_dir, process, PROMPT, timeout=max(1, int(api.remaining(90))))
        except Exception as exc:
            failure = exc
        finally:
            stop_process(process)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if failure is not None:
        raise RuntimeError(f"{failure}\nNative application log:\n{output[-6000:]}") from failure
    return CaptureExecution(tuple(argv), CommandResult(tuple(argv), process.returncode or 0, output, ""))


def _submit_task(context: CaptureRunContext, api: LocalApi, log) -> None:
    match context.target.agent.id:
        case "hive":
            api.request("/api/sessions", {"initial_phase": "independent", "initial_prompt": PROMPT})
        case "intentkit":
            chat = api.request("/lead/chats", {})
            api.request(f"/lead/chats/{chat['id']}/messages", {"message": PROMPT, "stream": False})
        case "mirix":
            _mirix_task(context, api)
        case "ouroboros":
            executable = shutil.which("ouroboros", path=context.env["PATH"])
            if executable is None:
                raise RuntimeError("installed Ouroboros CLI was not found")
            result = subprocess.run(
                [
                    executable,
                    "--url",
                    api.base,
                    "run",
                    "--workspace",
                    str(context.work_dir),
                    "--jsonl",
                    "--timeout",
                    "60",
                    "--execution-timeout",
                    "60",
                    PROMPT,
                ],
                cwd=context.work_dir,
                env=context.env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=api.remaining(75),
            )
            if result.returncode:
                raise RuntimeError(f"native Ouroboros task client exited {result.returncode}")
        case _:
            raise ValueError(f"no native task API for {context.target.agent.id}")


def _mirix_task(context: CaptureRunContext, api: LocalApi) -> None:
    account = api.request(
        "/admin/auth/register",
        {
            "name": "phistory",
            "email": "phistory@example.test",
            "password": "phistory-local-password-123",
        },
    )
    api.headers["Authorization"] = "Bearer " + account["access_token"]
    executable = shutil.which("python", path=context.env["PATH"])
    if executable is None:
        raise RuntimeError("installed MIRIX Python was not found")
    config_path = Path(context.env["PHISTORY_SOURCE_ROOT"]) / "mirix/configs/mirix.yaml"
    config = json.loads(
        subprocess.check_output(
            [
                executable,
                "-c",
                "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
                str(config_path),
            ],
            env=context.env,
            text=True,
            timeout=api.remaining(30),
        )
    )
    api.request("/agents/meta/initialize", {"config": config})
    agents = api.request("/agents")
    agent = next(
        (
            item
            for item in agents
            if "episodic" in (str(item.get("agent_type", "")) + str(item.get("name", ""))).lower()
        ),
        None,
    )
    if agent is None:
        raise RuntimeError("native MIRIX initialization did not return its episodic agent")
    api.request(f"/agents/{agent['id']}/messages", {"message": PROMPT, "role": "user"})
