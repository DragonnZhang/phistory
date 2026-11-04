import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from phistory import capture
from phistory.drivers import CaptureRunContext, qoder
from phistory.models import CaptureTarget, CommandResult, VersionInfo
from phistory.registry import get_agent

HOOK = Path(qoder.__file__).with_name("qoder_bootstrap.cjs")
FIXTURE = """
const fs = require('node:fs');
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
const ordinary = JSON.parse('{"n":2}', (key, val) => key === 'n' ? val * 2 : val);
let invalidJsonRejected = false;
try { JSON.parse('{'); } catch (error) { invalidJsonRejected = error instanceof SyntaxError; }
process.stdout.write(JSON.stringify({ value, ordinary, invalidJsonRejected }));
"""


def _parse(tmp_path, value, *, mode="model-availability", capture_enabled=True, report_enabled=True):
    node = shutil.which("node")
    assert node, "Node is required for the fake Qoder bootstrap tests"
    report = tmp_path / "bootstrap.json"
    env = {"PATH": os.environ.get("PATH", ""), "PHISTORY_QODER_CAPTURE_BOOTSTRAP": mode}
    if report_enabled:
        env["PHISTORY_QODER_CAPTURE_BOOTSTRAP_REPORT"] = str(report)
    if capture_enabled:
        env["CLAUDE_TAP_QODER_PROMPT_PATH"] = str(tmp_path / "fake-prompt.json")
    result = subprocess.run(
        [node, "--require", str(HOOK), "-e", FIXTURE],
        input=json.dumps(value),
        text=True,
        capture_output=True,
        env=env,
        check=True,
        timeout=10,
    )
    parsed = json.loads(result.stdout)
    assert parsed["ordinary"] == {"n": 4}
    assert parsed["invalidJsonRejected"]
    return parsed["value"], json.loads(report.read_text()) if report.exists() else None


def _catalog(enable=False):
    return {
        "assistant": [
            {
                "key": "auto",
                "display_name": "Auto",
                "enable": enable,
                "is_default": True,
                "max_input_tokens": 200000,
                "context_config": {"200K": {"token_count": 200000, "is_default": True}},
            },
            {"key": "other", "display_name": "Other", "enable": False},
        ],
        "quest": [{"key": "auto", "display_name": "Auto", "enable": False}],
    }


@pytest.mark.parametrize("enable", [False, 0, "0"])
def test_capture_mock_changes_only_existing_auto_availability(tmp_path, enable):
    value = _catalog(enable)
    expected = _catalog(True)
    actual, report = _parse(tmp_path, value)

    assert actual == expected
    assert report == {"mode": "model-availability", "model": "auto", "original_enable": enable, "enable": True}


@pytest.mark.parametrize("options", [{"mode": ""}, {"capture_enabled": False}, {"report_enabled": False}])
def test_bootstrap_requires_capture_only_mode_and_provenance_report(tmp_path, options):
    value = _catalog()
    actual, report = _parse(tmp_path, value, **options)
    assert actual == value
    assert report is None


@pytest.mark.parametrize("enable", [True, 1, "1", None])
def test_bootstrap_leaves_available_or_unspecified_models_unchanged(tmp_path, enable):
    value = _catalog(enable)
    actual, report = _parse(tmp_path, value)
    assert actual == value
    assert report is None


def test_bootstrap_never_invents_an_absent_model_or_rewrites_a_request(tmp_path):
    value = {
        "assistant": [{"key": "other", "display_name": "Other", "enable": False}],
        "messages": [{"role": "user", "content": "Reply with one short sentence."}],
        "system": "Original system prompt",
        "tools": [{"name": "Read", "description": "Original tool"}],
    }
    actual, report = _parse(tmp_path, value)
    assert actual == value
    assert report is None


def test_node_preload_works_with_spaced_paths_and_existing_options(tmp_path, monkeypatch):
    directory = tmp_path / "path with spaces"
    directory.mkdir()
    shutil.copy2(HOOK, directory / HOOK.name)
    monkeypatch.setattr(qoder, "__file__", str(directory / "qoder.py"))
    agent = get_agent("qoder")
    target = CaptureTarget(agent, VersionInfo("1.1.82"), agent.default_variant, tmp_path / "captures")
    context = CaptureRunContext(target, target.prompt_path, tmp_path / "tap", tmp_path, {})
    context.tap_output_dir.mkdir()
    env = qoder.prepare_qoder_env(
        context,
        {"PHISTORY_QODER_CAPTURE_BOOTSTRAP": "model-availability", "NODE_OPTIONS": "--stack-trace-limit=20"},
    )
    env["CLAUDE_TAP_QODER_PROMPT_PATH"] = str(tmp_path / "fake-prompt.json")
    assert env["NODE_OPTIONS"].startswith("--stack-trace-limit=20 ")
    result = subprocess.run(
        [shutil.which("node"), "-e", FIXTURE],
        input=json.dumps(_catalog()),
        text=True,
        capture_output=True,
        env=env,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout)["value"] == _catalog(True)


def test_capture_records_bootstrap_provenance_without_changing_default_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "test-credential")
    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args: tmp_path / "bin")
    monkeypatch.setattr("phistory.capture._binary_version", lambda *_args, **_kwargs: "fake-qoder")
    agent = get_agent("qoder")
    target = CaptureTarget(agent, VersionInfo("1.1.82"), agent.default_variant, tmp_path / "captures")

    def fake_run(argv, *, env, **_kwargs):
        assert str(HOOK) in env["NODE_OPTIONS"]
        assert "--model" not in argv
        assert env["PHISTORY_QODER_CAPTURE_BOOTSTRAP"] == "model-availability"
        report = Path(env["PHISTORY_QODER_CAPTURE_BOOTSTRAP_REPORT"])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"mode": "model-availability", "model": "auto", "enable": True}))
        target.prompt_path.write_text("# Fake system prompt\n")
        target.trace_path.write_text('{"request":{"body":{"model":"auto","system":"Fake system prompt"}}}\n')
        return CommandResult(tuple(argv), 0, "", "")

    monkeypatch.setattr("phistory.drivers.oneshot.run", fake_run)
    result = capture.capture_target(target, cache_dir=tmp_path / "cache", extract_static=False)

    assert result.status == "captured"
    meta = json.loads(target.meta_path.read_text())
    assert meta["variant"]["id"] == "default"
    assert meta["requested"] == {}
    assert meta["observed"]["model"] == "auto"
    assert meta["capture_environment"] == {"PHISTORY_QODER_CAPTURE_BOOTSTRAP": "model-availability"}
    assert any("Capture-only mock" in patch for patch in meta["compatibility_patches"])
