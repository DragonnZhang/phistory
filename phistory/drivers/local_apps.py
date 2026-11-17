from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import quote

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.catalog_gateway import PublicCatalogGateway
from phistory.drivers.common import tap_command
from phistory.drivers.local_server import has_prompt_request, stop_process, wait_for_prompt
from phistory.models import CommandResult

PROMPT = "Reply with one short sentence."


class LocalApi:
    def __init__(self, port: int, deadline: float):
        self.base = f"http://127.0.0.1:{port}"
        self.deadline = deadline
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.headers = {"Content-Type": "application/json"}

    def remaining(self, limit: float) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native application capture exceeded its time budget")
        return min(limit, remaining)

    def request(self, path: str, body: dict | None = None, *, method: str | None = None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self.headers,
            method=method or ("POST" if body is not None else "GET"),
        )
        try:
            with self.opener.open(request, timeout=self.remaining(90)) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(4000).decode("utf-8", errors="replace")
            raise RuntimeError(f"native API {request.method} {path} failed ({error.code}): {detail}") from error

    def wait_ready(self, process: subprocess.Popen, *, path: str = "/") -> None:
        deadline = time.monotonic() + self.remaining(90)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"native application exited during startup ({process.returncode})")
            try:
                with self.opener.open(self.base + path, timeout=2):
                    return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        raise RuntimeError("native application did not become ready")

    def wait_managed_tasks(self, process: subprocess.Popen) -> None:
        deadline = time.monotonic() + self.remaining(180)
        last_health = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"native supervisor exited during startup ({process.returncode})")
            try:
                last_health = self.request("/api/health")
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
                continue
            boot = last_health.get("boot") or {}
            if boot.get("phase") == "failed":
                raise RuntimeError(f"native supervisor boot failed: {json.dumps(last_health)}")
            if boot.get("tasks_ready") is True:
                return
            time.sleep(0.5)
        raise RuntimeError(f"native supervisor managed tasks did not become ready: {json.dumps(last_health)}")


def run_local_task_api(context: CaptureRunContext) -> CaptureExecution:
    deadline = time.monotonic() + context.target.agent.capture_timeout_seconds
    with socket.socket() as check:
        check.bind(("127.0.0.1", 0))
        port = check.getsockname()[1]
    env = {**context.env, "PHISTORY_SERVER_PORT": str(port)}
    if context.target.agent.id == "anything-llm":
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            env["PHISTORY_COLLECTOR_PORT"] = str(check.getsockname()[1])
    if context.target.agent.id == "looptroop":
        home = Path(env["PHISTORY_HOME"])
        checkout = home / "task-repository"
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/DragonnZhang/claude-tap.git", str(checkout)],
            check=True,
            timeout=90,
            stdout=subprocess.DEVNULL,
        )
        models = subprocess.run(
            ["opencode", "models"], cwd=checkout, env=env, capture_output=True, text=True, timeout=120
        )
        if models.returncode:
            raise RuntimeError(
                f"native OpenCode model catalogue bootstrap failed: {(models.stderr or models.stdout)[-4000:]}"
            )
        (context.prompt_path.parent / "native-host-models.log").write_text(models.stdout, encoding="utf-8")
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            opencode_port = check.getsockname()[1]
        env.update(
            LOOPTROOP_CONFIG_DIR=str(home / ".config/looptroop"),
            LOOPTROOP_OPENCODE_MODE="live",
            LOOPTROOP_OPENCODE_BASE_URL=f"http://127.0.0.1:{opencode_port}",
        )
    if context.target.agent.id == "markus":
        path = Path(env["PHISTORY_HOME"]) / ".markus/markus.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            web_port = check.getsockname()[1]
        config = {
            "server": {"apiPort": port, "webPort": web_port},
            "llm": {
                "defaultProvider": "openai",
                "defaultModel": "gpt-4.1",
                "providers": {
                    "openai": {
                        "apiKeyEnv": "OPENAI_API_KEY",
                        "model": "gpt-4.1",
                        "baseUrl": "https://api.openai.com/v1",
                    }
                },
            },
        }
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        path.chmod(0o600)
    requirements = {
        "minimum_tools": context.target.agent.capture_min_tools,
        "required_system_text": context.target.agent.required_system_text,
        "required_user_text": context.target.agent.required_user_text,
    }
    proxy_port = 0
    if context.target.agent.id == "hivekeep":
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            proxy_port = check.getsockname()[1]
    argv = tap_command(
        context.target,
        context.prompt_path,
        context.tap_output_dir,
        env,
        **({"proxy_port": proxy_port} if proxy_port else {}),
    )
    context.tap_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = context.tap_output_dir / "client.log"
    failure = None
    task_marker = next(iter(context.target.agent.required_user_text), PROMPT)
    with log_path.open("w", encoding="utf-8") as log, ExitStack() as resources:
        gateway = (
            resources.enter_context(
                PublicCatalogGateway(proxy_port, context.prompt_path.parent / "public-model-catalog")
            )
            if proxy_port
            else None
        )
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
            if context.target.agent.id == "looptroop":
                state_path = Path(env["LOOPTROOP_CONFIG_DIR"]) / "daemon.json"
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("native LoopTroop daemon exited before registration")
                    if state_path.exists():
                        state = json.loads(state_path.read_text())
                        if state.get("opencode", {}).get("status") in ("managed", "adopted"):
                            api.headers["Authorization"] = "Bearer " + state["apiToken"]
                            break
                    time.sleep(0.25)
                else:
                    raise TimeoutError("native LoopTroop OpenCode supervision did not become ready")
            readiness = {"web3gpt": "/api/chat", "hivekeep": "/api/onboarding/status"}
            api.wait_ready(process, path=readiness.get(context.target.agent.id, "/"))
            if context.target.agent.id == "ouroboros":
                api.wait_managed_tasks(process)
            try:
                _submit_task(context, api, log, **({"catalog_gateway_url": gateway.base_url} if gateway else {}))
            except Exception as exc:
                if not has_prompt_request(context.tap_output_dir, task_marker, **requirements):
                    raise
                print(f"Native task returned after its captured request: {exc}", file=log, flush=True)
            if not has_prompt_request(context.tap_output_dir, task_marker, **requirements):
                wait_for_prompt(
                    context.tap_output_dir, process, task_marker, timeout=max(1, int(api.remaining(90))), **requirements
                )
        except Exception as exc:
            failure = exc
        finally:
            stop_process(process)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if failure is not None:
        raise RuntimeError(f"{failure}\nNative application log:\n{output[-6000:]}") from failure
    return CaptureExecution(tuple(argv), CommandResult(tuple(argv), process.returncode or 0, output, ""))


def _submit_task(context: CaptureRunContext, api: LocalApi, log, *, catalog_gateway_url: str | None = None) -> None:
    match context.target.agent.id:
        case "markus":
            api.request("/api/auth/login", {"email": "admin@markus.local", "password": context.env["ADMIN_PASSWORD"]})
            agents = api.request("/api/agents?lean=1")["agents"]
            secretary = next((agent for agent in agents if agent.get("isOrgSecretary") is True), None)
            if secretary is None:
                raise RuntimeError("native Markus server did not seed its organization Secretary")
            api.request(f"/api/agents/{secretary['id']}/message", {"text": PROMPT})
        case "web3gpt":
            api.request(
                "/api/chat",
                {
                    "agentId": "agent_web3gpt",
                    "messages": [{"id": "phistory-user", "role": "user", "parts": [{"type": "text", "text": PROMPT}]}],
                },
            )
        case "looptroop":
            profile = api.request("/api/profile")
            settings = {
                "mainImplementer": "openai/gpt-4.1",
                "councilMembers": json.dumps(["openai/gpt-4.1", "openai/gpt-4.1-mini"]),
                "minCouncilQuorum": 2,
            }
            profile = api.request("/api/profile", settings, method="PATCH" if profile else "POST")
            project = api.request(
                "/api/projects",
                {
                    "name": "Phistory",
                    "shortname": "PHY",
                    "folderPath": str(Path(context.env["PHISTORY_HOME"]) / "task-repository"),
                    "profileId": profile["id"],
                },
            )
            ticket = api.request("/api/tickets", {"projectId": project["id"], "title": PROMPT})
            api.request(f"/api/tickets/{quote(str(ticket['id']), safe='')}/start", method="POST")
        case "anything-llm":
            key = api.request("/api/system/generate-api-key", {"name": "Phistory"})
            api.headers["Authorization"] = "Bearer " + key["apiKey"]["secret"]
            workspace = api.request("/api/v1/workspace/new", {"name": "Phistory", "chatMode": "chat"})
            slug = quote(workspace["workspace"]["slug"], safe="")
            api.request(f"/api/v1/workspace/{slug}/chat", {"message": "@agent " + PROMPT, "mode": "chat"})
        case "hivekeep":
            if catalog_gateway_url is None:
                raise ValueError("Hivekeep requires its explicit public-catalogue transport")
            api.headers["Origin"] = api.base
            api.request(
                "/api/auth/sign-up/email",
                {
                    "email": "phistory@example.com",
                    "password": context.env["PHISTORY_HIVEKEEP_PASSWORD"],
                    "name": "Phistory",
                },
            )
            api.request("/api/onboarding/profile", {"firstName": "Phistory", "pseudonym": "phistory", "language": "en"})
            provider = api.request(
                "/api/providers",
                {
                    "name": "Phistory compatible OpenRouter",
                    "type": "openai-compatible",
                    "config": {"baseUrl": catalog_gateway_url, "apiKey": context.env["OPENAI_API_KEY"]},
                    "families": ["llm"],
                },
            )["provider"]
            if provider.get("isValid") is not True:
                raise RuntimeError("native Hivekeep provider did not authenticate against the actual public catalogue")
            agent = api.request("/api/onboarding/configurator", {"providerId": provider["id"]})["agent"]
            if agent.get("name") != "Queenie" or agent.get("kind") != "configurator":
                raise RuntimeError("native Hivekeep onboarding did not seed its real Queenie configurator")
        case "hive":
            session = api.request("/api/sessions", {"initial_phase": "independent", "initial_prompt": PROMPT})
            api.request(f"/api/sessions/{session['session_id']}/chat", {"message": PROMPT})
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
    meta = api.request("/agents/meta/initialize", {"config": config})
    if not isinstance(meta, dict) or not meta.get("id"):
        raise RuntimeError("native MIRIX initialization did not return its meta agent")
    agents = api.request(f"/agents?parent_id={meta['id']}")
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
