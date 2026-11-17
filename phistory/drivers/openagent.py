from __future__ import annotations

import base64
import http.cookiejar
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

from phistory.drivers import CaptureExecution, CaptureRunContext
from phistory.drivers.common import tap_command
from phistory.drivers.local_server import stop_process, wait_for_prompt
from phistory.models import CommandResult

PORT = 14000
PROMPT = "Reply with one short sentence."


def run_openagent_store(context: CaptureRunContext) -> CaptureExecution:
    with socket.socket() as check:
        check.bind(("127.0.0.1", PORT))
    argv = tap_command(context.target, context.prompt_path, context.tap_output_dir)
    context.tap_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = context.tap_output_dir / "client.log"
    failure = None
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            argv,
            cwd=context.work_dir,
            env=context.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({})
            )
            credentials = (context.env["clientId"], context.env["clientSecret"])
            _application_auth_when_ready(opener, process, credentials)
            _configure_and_prompt(opener, credentials)
            wait_for_prompt(context.tap_output_dir, process, PROMPT)
        except Exception as exc:
            failure = exc
        finally:
            stop_process(process)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if failure is not None:
        raise RuntimeError(f"{failure}\nOpenAgent client log:\n{output[-4000:]}") from failure
    result = CommandResult(tuple(argv), process.returncode or 0, output, "")
    return CaptureExecution(tuple(argv), result)


def _application_auth_when_ready(opener, process, credentials: tuple[str, str]) -> None:
    deadline = time.monotonic() + 60
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"OpenAgent exited before local application auth ({process.returncode})")
        try:
            _api(opener, "/api/get-store?id=admin/store-built-in", basic_auth=credentials)
            return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"OpenAgent local server did not become ready: {last_error}")


def _configure_and_prompt(opener, credentials: tuple[str, str]) -> None:
    _api(
        opener,
        "/api/add-provider",
        {
            "name": "phistory",
            "category": "Model",
            "type": "OpenAI",
            "subType": "gpt-4.1",
            "clientSecret": "phistory-fake-api-key",
            "providerUrl": "https://api.openai.com",
            "state": "Active",
            "isDefault": True,
        },
        basic_auth=credentials,
    )
    path = "/api/get-store?id=admin/store-built-in"
    store = _api(opener, path, basic_auth=credentials)
    if not isinstance(store, dict):
        raise RuntimeError("OpenAgent's native built-in Store was not returned")
    store = {
        **store,
        "owner": "admin",
        "name": "phistory-capture",
        "isDefault": False,
        "modelProvider": "phistory",
        "externalApiKey": "phistory-local-store-key",
    }
    store.pop("fileTree", None)
    store.pop("propertiesMap", None)
    _api(opener, "/api/add-store", store, basic_auth=credentials)
    created = _api(opener, "/api/get-store?id=admin/phistory-capture", basic_auth=credentials)
    if not isinstance(created, dict) or any(
        created.get(key) != store.get(key) for key in ("prompt", "tools", "modelProvider")
    ):
        raise RuntimeError("OpenAgent's configured Store did not preserve the native prompt/tool selection")
    _api(
        opener,
        "/api/v1/chat/completions",
        {"model": "gpt-4.1", "stream": False, "messages": [{"role": "user", "content": PROMPT}]},
        bearer="phistory-local-store-key",
    )


def _api(
    opener,
    path: str,
    payload: dict | None = None,
    *,
    bearer: str | None = None,
    basic_auth: tuple[str, str] | None = None,
):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif basic_auth:
        credentials = base64.b64encode(":".join(basic_auth).encode()).decode("ascii")
        headers["Authorization"] = f"Basic {credentials}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    with opener.open(request, timeout=60) as response:
        body = json.load(response)
    if isinstance(body, dict) and "status" in body:
        if body["status"] != "ok":
            raise RuntimeError(f"OpenAgent {path} failed: {body.get('msg', body)}")
        return body.get("data")
    return body
