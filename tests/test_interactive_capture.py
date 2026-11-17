import json
import os
import sys
from dataclasses import replace

import pytest

from phistory.drivers import CaptureRunContext
from phistory.drivers.first_request import PROMPT
from phistory.drivers.interactive import run_interactive_pty
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent


@pytest.mark.skipif(os.name != "posix", reason="native terminal capture requires POSIX PTY")
def test_interactive_capture_uses_real_terminal_waits_for_ready_and_reaps_child(tmp_path, monkeypatch):
    script = tmp_path / "fake_terminal.py"
    script.write_text("""
import json, os, signal, sys, termios, time, tty
from pathlib import Path
assert sys.stdin.isatty() and sys.stdout.isatty()
assert tuple(os.get_terminal_size()) == (100, 30)
assert os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
tty.setraw(sys.stdin.fileno())
def shutdown(signum, frame):
    print("native UI restoration " * 15000, flush=True)
    Path(sys.argv[2]).write_text("exported after draining native terminal output")
    sys.exit(0)
signal.signal(signal.SIGINT, shutdown)
print("owned_pid=" + str(os.getpid()), flush=True)
print("\\x1b[36mReady for task\\x1b[0m", flush=True)
task = b""
while not task.endswith(b"\\r"):
    task += os.read(sys.stdin.fileno(), 1)
    if task == b"Reply with one short sentence.":
        print(task.decode(), flush=True)
assert task == b"Reply with one short sentence.\\r"
trace = Path(sys.argv[1]); trace.parent.mkdir(parents=True, exist_ok=True)
trace.write_text(json.dumps({"request":{"path":"/v1/embeddings","body":{"input": task.decode()}}}) + "\\n")
time.sleep(0.1)
with trace.open("a") as output:
    output.write(json.dumps({"request":{"path":"/v1/chat/completions","body":{"model":"fake","messages":[{"role":"user","content":task.decode().strip()}],"tools":[{"type":"function","function":{"name":"shell","parameters":{"type":"object"}}}]}}}) + "\\n")
assert os.read(sys.stdin.fileno(), 1) == b"\\x03"
print("native cancellation acknowledged", flush=True)
assert os.read(sys.stdin.fileno(), 1) == b"\\x03"
shutdown(None, None)
""")
    agent = replace(get_agent("agentlink"), capture_timeout_seconds=5)
    variant = replace(agent.variant("native-tui-openai"), pty_ready_text="Ready for task")
    target = CaptureTarget(agent, VersionInfo("test"), variant, tmp_path)
    trace = tmp_path / ".tap/session/trace_fake.jsonl"
    monkeypatch.setattr(
        "phistory.drivers.interactive.tap_command",
        lambda *args: [sys.executable, str(script), str(trace), str(tmp_path / "prompt.md")],
    )
    context = CaptureRunContext(
        target, tmp_path / "prompt.md", tmp_path / ".tap", tmp_path, {"PATH": os.environ["PATH"]}
    )
    result = run_interactive_pty(context)
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(records) == 2 and records[1]["request"]["body"]["messages"][0]["content"] == PROMPT
    assert (tmp_path / "prompt.md").read_text() == "exported after draining native terminal output"
    assert result.result.returncode == 0
    pid = int(result.result.stdout.split("owned_pid=", 1)[1].split()[0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
