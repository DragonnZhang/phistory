import json
import os
import sys
from dataclasses import replace

import pytest

from phistory.drivers import CaptureRunContext
from phistory.drivers.first_request import PROMPT, run_first_request
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent


@pytest.mark.skipif(os.name != "posix", reason="native capture uses POSIX process groups")
def test_first_request_forwards_native_stdin_and_stops_owned_process_after_generation(tmp_path, monkeypatch):
    script = tmp_path / "fake_native.py"
    script.write_text("""
import json, os, sys, time
from pathlib import Path
print("owned_pid=" + str(os.getpid()), flush=True)
task = sys.stdin.readline().strip()
trace = Path(sys.argv[1])
trace.parent.mkdir(parents=True, exist_ok=True)
with trace.open("w") as output:
    output.write(json.dumps({"request": {"path": "/v1/embeddings", "body": {"input": task}}}) + "\\n")
    output.flush()
    time.sleep(0.05)
    output.write(json.dumps({"request": {"path": "/v1/chat/completions", "body": {"model": "fake", "messages": [{"role": "user", "content": task}]}}}) + "\\n")
    output.flush()
    time.sleep(60)
""")
    agent = replace(get_agent("atomic-agent"), capture_timeout_seconds=5, capture_min_tools=0)
    target = CaptureTarget(agent, VersionInfo("test"), agent.variant("cloud-openai"), tmp_path)
    tap = tmp_path / ".tap"
    trace = tap / "session/trace_fake.jsonl"
    monkeypatch.setattr(
        "phistory.drivers.first_request.tap_command", lambda *args: [sys.executable, str(script), str(trace)]
    )
    context = CaptureRunContext(target, tmp_path / "prompt.md", tap, tmp_path, {"PATH": os.environ["PATH"]})
    result = run_first_request(context)
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(records) == 2
    assert records[-1]["request"]["body"]["messages"][0]["content"] == PROMPT
    pid = int(
        next(line.split("=", 1)[1] for line in result.result.stdout.splitlines() if line.startswith("owned_pid="))
    )
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
