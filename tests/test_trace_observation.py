import json

from phistory.capture import _trace_observation


def test_gemini_counts_declarations_and_reads_model_from_native_path(tmp_path):
    path = tmp_path / "trace.jsonl"
    record = {
        "request": {
            "path": "/v1beta/models/gemini-test:streamGenerateContent?alt=sse",
            "body": {
                "systemInstruction": {"parts": [{"text": "System instructions"}]},
                "contents": [{"role": "user", "parts": [{"text": "Task"}]}],
                "tools": [
                    {"functionDeclarations": [{"name": "read", "parameters": {}}, {"name": "write", "parameters": {}}]}
                ],
            },
        }
    }
    path.write_text(json.dumps(record) + "\n")
    assert _trace_observation(path) == {"model": "gemini-test", "tool_count": 2}


def test_observation_preserves_openai_deferred_tools_and_string_body(tmp_path):
    path = tmp_path / "trace.jsonl"
    body = {
        "model": "test-model",
        "instructions": "System instructions",
        "tools": [{"type": "function", "name": "read", "parameters": {}}],
        "input": [{"type": "additional_tools", "tools": [{"type": "function", "name": "write", "parameters": {}}]}],
    }
    path.write_text(json.dumps({"request": {"path": "/v1/responses", "body": json.dumps(body)}}) + "\n")
    assert _trace_observation(path) == {"model": "test-model", "tool_count": 2}


def test_observation_selects_prompt_over_bootstrap_and_handles_broken_lines(tmp_path):
    path = tmp_path / "trace.jsonl"
    records = [
        {"request": {"path": "/v1/models", "body": {"model": "bootstrap"}}},
        {
            "request": {
                "path": "/v1/messages",
                "body": {"model": "claude-test", "system": "System", "tools": [{"name": "bash", "input_schema": {}}]},
            }
        },
    ]
    path.write_text("invalid json\n" + "\n".join(json.dumps(item) for item in records) + "\n")
    assert _trace_observation(path) == {"model": "claude-test", "tool_count": 1}


def test_openai_namespaces_count_callable_tools(tmp_path):
    path = tmp_path / "trace.jsonl"
    body = {
        "model": "test-model",
        "instructions": "System",
        "tools": [
            {
                "type": "namespace",
                "name": "functions",
                "tools": [
                    {"type": "function", "name": "read", "parameters": {}},
                    {"type": "function", "name": "write", "parameters": {}},
                ],
            }
        ],
    }
    path.write_text(json.dumps({"request": {"path": "/v1/responses", "body": body}}) + "\n")
    assert _trace_observation(path) == {"model": "test-model", "tool_count": 2}
