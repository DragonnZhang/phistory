import json

import pytest

from phistory.capture import _primary_snapshot
from phistory.drivers.local_server import has_prompt_request


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_required_tool_names_reject_placeholders_and_text_mentions(tmp_path, protocol):
    def record(name):
        tool = {
            "name": name,
            "description": "agentlas_tool_search is mentioned here",
            "input_schema": {"type": "object"},
        }
        if protocol == "openai":
            tool = {
                "type": "function",
                "function": {"name": name, "description": tool["description"], "parameters": tool["input_schema"]},
            }
        return {
            "request": {
                "path": "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions",
                "body": {
                    "model": "fake",
                    "messages": [{"role": "user", "content": "Native task includes agentlas_tool_search"}],
                    "tools": [tool],
                },
            }
        }

    path = tmp_path / "session/trace_fake.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps(record("DeferredToolPlaceholder")) + "\n")
    criteria = {"required_tool_name_fragments": ("agentlas_tool_search",)}
    assert not has_prompt_request(tmp_path, "Native task", **criteria)
    with pytest.raises(ValueError):
        _primary_snapshot(path, **criteria)
    with path.open("a") as output:
        output.write(json.dumps(record("mcp__native__agentlas_tool_search")) + "\n")
    assert has_prompt_request(tmp_path, "Native task", **criteria)
    snapshot = _primary_snapshot(path, **criteria)
    assert snapshot.tools[0].name == "mcp__native__agentlas_tool_search"
