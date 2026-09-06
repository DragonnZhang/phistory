import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from phistory.drivers import CaptureRunContext
from phistory.drivers.dsh_web import _create_and_prompt_session
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent


@pytest.mark.parametrize("requires_auth", [False, True])
@pytest.mark.parametrize("remote_api", [False, True])
def test_web_session_authenticates_and_reuses_cookie(tmp_path: Path, requires_auth: bool, remote_api: bool):
    methods = []
    exchanges = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/?token=test-launch-token":
                exchanges.append(self.path)
                self.send_response(303)
                self.send_header("Set-Cookie", "dsh-session=test-session; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Location", "/")
                self.end_headers()
            else:
                assert self.path == "/"
                assert self.headers.get("Cookie") == "dsh-session=test-session"
                self.send_response(200)
                self.end_headers()

        def do_POST(self):
            envelope = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if requires_auth and self.headers.get("Cookie") != "dsh-session=test-session":
                self.send_response(401)
                self.end_headers()
                return
            endpoint = envelope["method"]
            assert self.path == f"/api/{endpoint}"
            if ("/" in endpoint) != remote_api:
                self.send_response(404)
                self.end_headers()
                return
            payload = envelope["payload"]
            if remote_api:
                assert set(payload) == {"args"}
                assert set(payload["args"]) == {"request"}
                payload = payload["args"]["request"]
            assert "sessionId" in payload
            methods.append(endpoint.replace("/", "."))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"result": {"ok": True, "value": {"sessionId": "session"}}}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    agent = get_agent("dsh")
    target = CaptureTarget(agent, VersionInfo("0.1.2-rc.1"), agent.default_variant, tmp_path)
    context = CaptureRunContext(target, target.prompt_path, tmp_path, tmp_path, {})
    (tmp_path / "client.log").write_text(f"dsh web: http://127.0.0.1:{port}/?token=test-launch-token\n")
    try:
        _create_and_prompt_session(context, port, SimpleNamespace(poll=lambda: None))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert methods == ["session.create", "session.prompt"]
    assert len(exchanges) == int(requires_auth)
