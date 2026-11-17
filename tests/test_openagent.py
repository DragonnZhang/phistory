import io
import json

import pytest

from phistory.drivers.openagent import PROMPT, _api, _configure_and_prompt


def test_store_driver_preserves_native_prompt_and_tools_instead_of_using_provider_bypass(monkeypatch):
    native = {
        "owner": "admin",
        "name": "store-built-in",
        "systemPrompt": "native prompt",
        "tools": ["native-tool"],
        "otherConfig": {"unchanged": True},
    }
    calls = []

    def api(opener, path, payload=None, **kwargs):
        calls.append((path, payload, kwargs))
        return native if path.startswith("/api/get-store") else True

    monkeypatch.setattr("phistory.drivers.openagent._api", api)
    _configure_and_prompt(object())
    updated = calls[2][1]
    assert updated == {**native, "modelProvider": "phistory", "externalApiKey": "phistory-local-store-key"}
    assert native["tools"] == ["native-tool"] and "modelProvider" not in native
    assert calls[-1][0] == "/api/v1/chat/completions"
    assert calls[-1][2] == {"bearer": "phistory-local-store-key"}
    assert calls[-1][1]["messages"] == [{"role": "user", "content": PROMPT}]
    assert not any("test-provider" in call[0] for call in calls)


def test_local_api_checks_error_envelope_and_sends_store_bearer():
    class Opener:
        def __init__(self, body):
            self.body = body
            self.requests = []

        def open(self, request, **kwargs):
            self.requests.append(request)
            return io.BytesIO(json.dumps(self.body).encode())

    opener = Opener({"status": "ok", "data": {"value": 1}})
    assert _api(opener, "/api/example", {"x": 1}, bearer="local-key") == {"value": 1}
    request = opener.requests[0]
    assert request.get_header("Authorization") == "Bearer local-key"
    assert json.loads(request.data) == {"x": 1}
    assert request.full_url == "http://127.0.0.1:14000/api/example"
    with pytest.raises(RuntimeError, match="rejected"):
        _api(Opener({"status": "error", "msg": "rejected"}), "/api/example")
