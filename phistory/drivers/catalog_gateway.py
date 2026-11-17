from __future__ import annotations

import hashlib
import http.client
import json
import platform
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class PublicCatalogGateway:
    """Relay public model metadata directly and model POSTs only to the owned tap."""

    def __init__(self, proxy_port: int, archive_dir: Path):
        self.proxy_port = proxy_port
        self.archive_dir = archive_dir
        self.catalog_url = "https://openrouter.ai/api/v1/models"
        self.model_base = "https://openrouter.ai/api"
        self.lock = threading.Lock()
        self.records = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/v1/models":
                    self.send_error(404)
                    return
                try:
                    request = urllib.request.Request(gateway.catalog_url, headers={"Accept": "application/json"})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        request, timeout=45
                    ) as response:
                        body = response.read(16 * 1024 * 1024 + 1)
                        if len(body) > 16 * 1024 * 1024:
                            raise ValueError("public model catalogue exceeded its size bound")
                        json.loads(body)
                        gateway.archive(body)
                        self.reply(response.status, response.headers.get("Content-Type", "application/json"), body)
                except Exception as error:
                    self.send_error(502, str(error))

            def do_POST(self):
                if self.path not in ("/v1/chat/completions", "/v1/responses"):
                    self.send_error(404)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in ("host", "connection", "transfer-encoding", "content-length")
                }
                connection = http.client.HTTPConnection("127.0.0.1", gateway.proxy_port, timeout=60)
                try:
                    connection.request("POST", gateway.model_base + self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    self.reply(response.status, response.getheader("Content-Type", "application/json"), response.read())
                except Exception as error:
                    self.send_error(502, str(error))
                finally:
                    connection.close()

            def reply(self, status: int, content_type: str, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def archive(self, body: bytes) -> None:
        digest = hashlib.sha256(body).hexdigest()
        with self.lock:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            (self.archive_dir / f"{digest}.json").write_bytes(body)
            self.records.append(
                {
                    "url": self.catalog_url,
                    "sha256": digest,
                    "file": f"{digest}.json",
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "capture_host": platform.system(),
                }
            )
            (self.archive_dir / "manifest.json").write_text(
                json.dumps({"kind": "actual public model metadata", "requests": self.records}, indent=2) + "\n"
            )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
