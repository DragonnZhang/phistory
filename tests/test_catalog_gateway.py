import hashlib
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from phistory.drivers.catalog_gateway import PublicCatalogGateway


@contextmanager
def serve(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_catalog_gateway_preserves_actual_metadata_and_model_bytes_and_rejects_other_routes(tmp_path):
    metadata = b'{ "data": [{"id": "native/model", "name": "Native model"}] }\n'
    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/public/models"
            calls.append(("metadata", self.path))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(metadata)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(("tap", self.path, body, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"captured":true}\n\ndata: [DONE]\n\n')

        def log_message(self, *args):
            pass

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with serve(Upstream) as upstream, PublicCatalogGateway(upstream.server_port, tmp_path / "catalog") as gateway:
        gateway.catalog_url = f"http://127.0.0.1:{upstream.server_port}/public/models"
        with opener.open(gateway.base_url + "/models") as response:
            assert response.read() == metadata
        raw = b'{ "model":"native/model", "messages":[{"role":"system","content":"Actual system"}], "tools":[] }'
        request = urllib.request.Request(
            gateway.base_url + "/chat/completions",
            data=raw,
            headers={"Authorization": "Bearer owned-fake-key", "Content-Type": "application/json"},
        )
        with opener.open(request) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            assert response.read() == b'data: {"captured":true}\n\ndata: [DONE]\n\n'
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(urllib.request.Request(gateway.base_url + "/embeddings", data=b"{}"))
        assert error.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(gateway.base_url + "/chat/completions")
        assert error.value.code == 404
    assert calls == [
        ("metadata", "/public/models"),
        ("tap", "https://openrouter.ai/api/v1/chat/completions", raw, "Bearer owned-fake-key"),
    ]
    manifest = json.loads((tmp_path / "catalog/manifest.json").read_text())
    record = manifest["requests"][0]
    assert record["sha256"] == hashlib.sha256(metadata).hexdigest()
    assert (tmp_path / "catalog" / record["file"]).read_bytes() == metadata
