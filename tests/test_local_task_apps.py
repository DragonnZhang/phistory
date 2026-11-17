from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from phistory.drivers import CaptureRunContext
from phistory.drivers.local_apps import PROMPT, LocalApi, _mirix_task, _submit_task
from phistory.drivers.local_server import has_prompt_request
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent


def context(agent_id: str, tmp_path: Path) -> CaptureRunContext:
    agent = get_agent(agent_id)
    target = CaptureTarget(agent, VersionInfo("test"), agent.variant(agent.latest_variant_ids[0]), tmp_path)
    return CaptureRunContext(
        target,
        tmp_path / "prompt.md",
        tmp_path / ".tap",
        tmp_path,
        {
            "PATH": "/runtime/bin",
            "PHISTORY_SOURCE_ROOT": str(tmp_path / "source"),
        },
    )


def test_local_task_uses_native_chat_id_and_single_user_endpoint(tmp_path):
    calls = []

    class Api:
        def request(self, path, body=None):
            calls.append((path, body))
            return {"id": "native-chat-id"}

    _submit_task(context("intentkit", tmp_path), Api(), None)
    assert calls == [
        ("/lead/chats", {}),
        ("/lead/chats/native-chat-id/messages", {"message": PROMPT, "stream": False}),
    ]


def test_mirix_registers_native_principal_and_preserves_complete_config(tmp_path, monkeypatch):
    calls = []
    config = {"llm_config": {"model": "native"}, "meta_agent_config": {"agents": ["episodic", "semantic"]}}

    class Api:
        headers = {}

        def remaining(self, limit):
            return limit

        def request(self, path, body=None):
            calls.append((path, body))
            if path == "/admin/auth/register":
                return {"access_token": "native-jwt"}
            assert self.headers["Authorization"] == "Bearer native-jwt"
            if path == "/agents/meta/initialize":
                return {"id": "native-meta"}
            if path == "/agents?parent_id=native-meta":
                return [
                    {"id": "meta", "name": "meta_memory_agent"},
                    {"id": "native-episode", "name": "episodic_memory_agent"},
                ]

    monkeypatch.setattr("phistory.drivers.local_apps.shutil.which", lambda *a, **kw: "/runtime/bin/python")
    monkeypatch.setattr("phistory.drivers.local_apps.subprocess.check_output", lambda *a, **kw: json.dumps(config))
    _mirix_task(context("mirix", tmp_path), Api())
    assert calls[1] == ("/agents/meta/initialize", {"config": config})
    assert calls[-1] == ("/agents/native-episode/messages", {"message": PROMPT, "role": "user"})


def test_prompt_wait_ignores_embeddings_and_accepts_native_chat_google_and_flat_ollama(tmp_path):
    directory = tmp_path / "2026-11-17"
    directory.mkdir()
    trace = directory / "trace_test.jsonl"
    embedding = {"request": {"path": "/v1/embeddings", "body": {"input": PROMPT}}}
    trace.write_text(json.dumps(embedding) + "\n")
    assert not has_prompt_request(tmp_path, PROMPT)
    for path, body in [
        ("/api/generate", {"model": "native", "prompt": PROMPT}),
        ("/v1/chat/completions", {"model": "native", "messages": [{"role": "user", "content": PROMPT}]}),
        ("/v1beta/models/native:generateContent", {"contents": [{"role": "user", "parts": [{"text": PROMPT}]}]}),
    ]:
        trace.write_text(json.dumps({"request": {"path": path, "body": body}}) + "\n")
        assert has_prompt_request(tmp_path, PROMPT)


def test_supervisor_wait_requires_native_task_admission_and_reports_boot_failure(monkeypatch):
    class Process:
        def poll(self):
            return None

    api = LocalApi(1, time.monotonic() + 10)
    health = iter(
        [{"boot": {"phase": "ready", "dialogue_ready": True, "tasks_ready": False}}, {"boot": {"tasks_ready": True}}]
    )
    paths = []

    def request(path):
        paths.append(path)
        return next(health)

    monkeypatch.setattr(api, "request", request)
    monkeypatch.setattr("phistory.drivers.local_apps.time.sleep", lambda _: None)
    api.wait_managed_tasks(Process())
    assert paths == ["/api/health", "/api/health"]
    monkeypatch.setattr(api, "request", lambda _: {"boot": {"phase": "failed", "error": "native failure"}})
    with pytest.raises(RuntimeError, match="native failure"):
        api.wait_managed_tasks(Process())


def test_local_api_preserves_native_login_cookie_for_owned_application_requests():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Set-Cookie", "markus_token=native-session; HttpOnly; Path=/; SameSite=Strict")
            self.end_headers()
            self.wfile.write(b'{"user":{"id":"native-user"}}')

        def do_GET(self):
            seen.append(self.headers.get("Cookie"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"agents":[]}')

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            api = LocalApi(server.server_port, time.monotonic() + 10)
            api.request("/api/auth/login", {"email": "owned@example.test", "password": "local-test"})
            assert api.request("/api/agents") == {"agents": []}
            assert seen == ["markus_token=native-session"]
        finally:
            server.shutdown()
            thread.join(timeout=5)
