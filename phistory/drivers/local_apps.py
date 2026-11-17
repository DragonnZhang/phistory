from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import ExitStack
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.catalog_gateway import PublicCatalogGateway
from phistory.drivers.common import tap_command
from phistory.drivers.docker_runtime import DOCKER_APPS, cleanup_docker_app, prepare_docker_env, wait_docker_ready
from phistory.drivers.local_server import has_prompt_request, stop_process, wait_for_prompt
from phistory.models import CommandResult

PROMPT = "Reply with one short sentence."


class LocalApi:
    def __init__(self, port: int, deadline: float):
        self.base = f"http://127.0.0.1:{port}"
        self.deadline = deadline
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.headers = {"Content-Type": "application/json"}

    def remaining(self, limit: float) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native application capture exceeded its time budget")
        return min(limit, remaining)

    def open(self, path: str, body: dict | None = None, *, method: str | None = None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self.headers,
            method=method or ("POST" if body is not None else "GET"),
        )
        try:
            return self.opener.open(request, timeout=self.remaining(90))
        except urllib.error.HTTPError as error:
            detail = error.read(4000).decode("utf-8", errors="replace")
            raise RuntimeError(f"native API {request.method} {path} failed ({error.code}): {detail}") from error

    def request(self, path: str, body: dict | None = None, *, method: str | None = None):
        with self.open(path, body, method=method) as response:
            payload = response.read()
            try:
                return json.loads(payload)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"native API {path} returned non-JSON ({response.status}, {response.headers.get('Content-Type')}): {payload[:1000]!r}"
                ) from error

    def request_bytes(self, path: str, body: dict | None = None):
        with self.open(path, body) as response:
            return response.read()

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
    env["PHISTORY_CAPTURE_ARTIFACT_DIR"] = str(context.prompt_path.parent)
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
        "required_tool_name_fragments": context.target.agent.required_tool_name_fragments,
    }
    proxy_port = 0
    if context.target.agent.id in {"hivekeep", "anything-llm", "librechat", "vibesdk"} | DOCKER_APPS:
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            proxy_port = check.getsockname()[1]
        env["PHISTORY_TAP_PORT"] = str(proxy_port)
    proxy_host = prepare_docker_env(env) if context.target.agent.id in DOCKER_APPS else None
    argv = tap_command(
        context.target,
        context.prompt_path,
        context.tap_output_dir,
        env,
        **({"proxy_port": proxy_port} if proxy_port else {}),
        **({"proxy_host": proxy_host} if proxy_host else {}),
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
            if context.target.agent.id == "hivekeep"
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
            if context.target.agent.id == "vibesdk":
                api.base = f"http://localhost:{port}"
            if context.target.agent.id in DOCKER_APPS:
                api.base = f"http://localhost:{port}"
                wait_docker_ready(env, process, deadline)
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
            readiness = {
                "web3gpt": "/api/chat",
                "hivekeep": "/api/onboarding/status",
                "librechat": "/readyz",
                "vibesdk": "/api/auth/csrf-token",
            }
            if context.target.agent.id != "dark-moon":
                api.wait_ready(process, path=readiness.get(context.target.agent.id, "/"))
            if context.target.agent.id == "ouroboros":
                api.wait_managed_tasks(process)
            try:
                _submit_task(
                    replace(context, env=env),
                    api,
                    log,
                    **({"catalog_gateway_url": gateway.base_url} if gateway else {}),
                )
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
            if context.target.agent.id in DOCKER_APPS:
                cleanup_docker_app(context.target.agent.id, env, log)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if failure is not None:
        raise RuntimeError(f"{failure}\nNative application log:\n{output[-6000:]}") from failure
    return CaptureExecution(tuple(argv), CommandResult(tuple(argv), process.returncode or 0, output, ""))


def _submit_task(context: CaptureRunContext, api: LocalApi, log, *, catalog_gateway_url: str | None = None) -> None:
    match context.target.agent.id:
        case "dark-moon":
            subprocess.run(
                ["bash", "darkmoon.sh", PROMPT, "--agent", "pentest", "--format", "json"],
                cwd=context.env["PHISTORY_SOURCE_ROOT"],
                env=context.env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=api.remaining(180),
            )
        case "qm":
            output = subprocess.check_output(
                ["qm", "admin-login", "--email", "phistory@example.com"],
                cwd=Path(context.env["PHISTORY_HOME"]) / "qm-deployment",
                env=context.env,
                text=True,
                timeout=api.remaining(90),
            )
            links = [word for word in output.split() if "/auth/admin-login#token=" in word]
            if len(links) != 1:
                raise RuntimeError("native QM CLI did not issue exactly one admin login link")
            token = parse_qs(urlsplit(links[0]).fragment)["token"][0]
            request = urllib.request.Request(
                api.base + "/auth/admin-login",
                data=urlencode({"token": token}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": api.base},
            )
            with api.opener.open(request, timeout=api.remaining(90)):
                pass
            api.headers["Origin"] = api.base
            with api.open("/api/turn", {"text": PROMPT}) as response:
                response.read(1)
        case "autogpt":
            api.headers["Origin"] = api.base
            api.request(
                "/api/auth/sign-up/email",
                {"email": "phistory@example.com", "password": secrets.token_urlsafe(24) + "Aa9!", "name": "Phistory"},
            )
            token = api.request("/api/auth/token")["token"]
            api.headers["Authorization"] = "Bearer " + token
            session = api.request("/_agpt/api/chat/sessions", {})
            with api.open(
                f"/_agpt/api/chat/sessions/{quote(session['id'], safe='')}/stream", {"message": PROMPT}
            ) as stream:
                while api.remaining(90):
                    if has_prompt_request(
                        context.tap_output_dir,
                        PROMPT,
                        minimum_tools=context.target.agent.capture_min_tools,
                        required_system_text=context.target.agent.required_system_text,
                    ):
                        return
                    if not stream.readline():
                        return
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
        case "librechat":
            password = secrets.token_urlsafe(24) + "Aa9!"
            api.headers["Origin"] = api.base
            api.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0.0.0 PhistoryCapture/0.1"
            api.request(
                "/api/auth/register",
                {
                    "name": "Phistory",
                    "email": "phistory@example.com",
                    "password": password,
                    "confirm_password": password,
                },
            )
            login = api.request("/api/auth/login", {"email": "phistory@example.com", "password": password})
            api.headers["Authorization"] = "Bearer " + login["token"]
            catalog = api.request("/api/agents/tools")
            if '"calculator"' not in json.dumps(catalog):
                raise RuntimeError("native LibreChat catalogue did not expose its calculator")
            agent = api.request(
                "/api/agents",
                {"name": "Phistory", "provider": "openAI", "model": "gpt-6.1-sol", "tools": ["calculator"]},
            )
            with api.open(
                "/api/agents/chat",
                {
                    "text": PROMPT,
                    "endpoint": "agents",
                    "agent_id": agent["id"],
                    "conversationId": "new",
                    "parentMessageId": "00000000-0000-0000-0000-000000000000",
                    "messageId": str(uuid.uuid4()),
                },
            ) as stream:
                while stream.readline():
                    if has_prompt_request(
                        context.tap_output_dir,
                        PROMPT,
                        minimum_tools=context.target.agent.capture_min_tools,
                        required_tool_name_fragments=context.target.agent.required_tool_name_fragments,
                    ):
                        break
        case "vibesdk":
            asyncio.run(_vibesdk_task(context, api))
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


async def _vibesdk_task(context: CaptureRunContext, api: LocalApi) -> None:
    import aiohttp

    api.headers["Origin"] = "http://localhost:5173"
    csrf = api.request("/api/auth/csrf-token")["data"]["token"]
    api.headers["X-CSRF-Token"] = csrf
    api.headers["Cookie"] = "; ".join(f"{cookie.name}={cookie.value}" for cookie in api.cookies)
    api.request(
        "/api/auth/register",
        {"email": "phistory@example.com", "password": secrets.token_urlsafe(24) + "Aa9!", "name": "Phistory"},
    )
    token = next((cookie.value for cookie in api.cookies if cookie.name == "accessToken"), None)
    if not token:
        raise RuntimeError("native VibeSDK registration did not issue its accessToken")
    api.headers.pop("Cookie", None)
    api.headers.pop("X-CSRF-Token", None)
    api.headers["Authorization"] = "Bearer " + token
    payload = api.request_bytes("/api/agent", {"query": PROMPT, "projectType": "general"})
    records = [json.loads(line) for line in payload.decode().splitlines() if line.strip()]
    created = next((record for record in records if record.get("agentId")), None)
    if created is None:
        raise RuntimeError(f"native VibeSDK creation did not return its agent ID: {payload[-2500:]!r}")
    if created.get("behaviorType") != "agentic":
        raise RuntimeError("native General project did not select its documented agentic behavior")
    url = f"ws://{urlsplit(api.base).netloc}/api/agent/{quote(created['agentId'], safe='')}/ws"
    headers = {"Authorization": "Bearer " + token, "Origin": api.headers["Origin"]}
    async with aiohttp.ClientSession() as session, session.ws_connect(url, headers=headers) as ws:
        started = False
        while api.remaining(90):
            if started and has_prompt_request(
                context.tap_output_dir,
                PROMPT,
                minimum_tools=context.target.agent.capture_min_tools,
                required_system_text=context.target.agent.required_system_text,
                required_user_text=context.target.agent.required_user_text,
                required_tool_name_fragments=context.target.agent.required_tool_name_fragments,
            ):
                return
            try:
                message = await ws.receive(timeout=1)
            except asyncio.TimeoutError:
                continue
            if message.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(message.data)
                if data.get("type") == "agent_connected" and not started:
                    await ws.send_json({"type": "generate_all"})
                    started = True
                elif data.get("type") == "error":
                    raise RuntimeError(f"native VibeSDK control error: {json.dumps(data)}")
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                raise RuntimeError("native VibeSDK control WebSocket closed before its primary request")


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
