import json
from pathlib import Path

import pytest

from phistory import capture
from phistory.models import CaptureTarget, CommandResult, VersionInfo
from phistory.registry import get_agent

SECRET = "qoder-test-credential"


@pytest.fixture
def target(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", SECRET)
    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args: tmp_path / "bin")
    monkeypatch.setattr("phistory.capture._binary_version", lambda *_args, **_kwargs: "qoder 1.1.82")
    monkeypatch.setattr("phistory.drivers.oneshot.time.sleep", lambda _seconds: None)
    agent = get_agent("qoder")
    return CaptureTarget(agent, VersionInfo("1.1.82"), agent.default_variant, tmp_path / "captures")


def _native_log(env):
    path = Path(env["HOME"]) / ".qoder/logs/runs/test-session/qodercli.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _model_error(model="auto"):
    return (
        "2026-11-04T06:24:33.509Z ERROR debug.message [session-runner] query failed "
        f'Ou [ModelNotFoundError]: main model "{model}" is unavailable.\n'
    )


def _write_prompt(argv):
    prompt = Path(argv[argv.index("--export-prompt") + 1])
    prompt.write_text("# Fake prompt\n", encoding="utf-8")
    trace = {"request": {"body": {"system": "Fake prompt", "messages": [{"role": "user", "content": "Hi"}]}}}
    prompt.with_name("trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")


@pytest.mark.parametrize("model", ["auto", SECRET])
def test_model_unavailable_fails_once_with_safe_reason_and_preserves_archive(target, tmp_path, monkeypatch, model):
    target.variant_dir.mkdir(parents=True)
    previous = {path: f"original {path.name}\n" for path in (target.prompt_path, target.trace_path, target.meta_path)}
    for path, text in previous.items():
        path.write_text(text, encoding="utf-8")
    attempts = []
    homes = []

    def fake_run(argv, *, env, **_kwargs):
        attempts.append(argv)
        homes.append(Path(env["HOME"]))
        _native_log(env).write_text(
            "debug padding\n" * 10000
            + _model_error(model)
            + f"INFO authorization: Bearer {SECRET}\nINFO access_token: exchanged-credential\n",
            encoding="utf-8",
        )
        return CommandResult(tuple(argv), 1, "", "Error: no valid records found in trace file")

    monkeypatch.setattr("phistory.drivers.oneshot.run", fake_run)
    result = capture.capture_target(target, cache_dir=tmp_path / "cache", force=True)

    assert result.status == "failed"
    assert len(attempts) == 1
    assert "--no-session-persistence" in attempts[0]
    assert "--model" not in attempts[0]
    assert 'main model "' + ("<redacted>" if model == SECRET else model) + '" is unavailable.' in result.error
    assert SECRET not in result.error
    assert "exchanged-credential" not in result.error
    assert "debug padding" not in result.error
    assert len(result.error) < 1000
    assert all(not home.exists() for home in homes)
    assert all(path.read_text(encoding="utf-8") == text for path, text in previous.items())
    assert not list(target.root.glob(".phistory-capture-*"))


def test_transient_failure_retries_without_reusing_a_stale_native_error(target, tmp_path, monkeypatch):
    make_env = capture._capture_env

    def seeded_env(*args, **kwargs):
        env = make_env(*args, **kwargs)
        _native_log(env).write_text(_model_error(), encoding="utf-8")
        return env

    monkeypatch.setattr("phistory.capture._capture_env", seeded_env)
    attempts = []

    def fake_run(argv, *, env, **_kwargs):
        attempts.append(argv)
        if len(attempts) == 1:
            with _native_log(env).open("a", encoding="utf-8") as log:
                log.write("WARN network connection reset\n")
            return CommandResult(tuple(argv), 1, "", "no valid records found in trace file")
        _write_prompt(argv)
        return CommandResult(tuple(argv), 0, "", "")

    monkeypatch.setattr("phistory.drivers.oneshot.run", fake_run)
    result = capture.capture_target(target, cache_dir=tmp_path / "cache")

    assert result.status == "captured"
    assert len(attempts) == 2
    assert target.prompt_path.read_text(encoding="utf-8") == "# Fake prompt\n"


def test_valid_prompt_survives_a_later_native_model_error(target, tmp_path, monkeypatch):
    def fake_run(argv, *, env, **_kwargs):
        _write_prompt(argv)
        _native_log(env).write_text(_model_error(), encoding="utf-8")
        return CommandResult(tuple(argv), 1, "", "")

    monkeypatch.setattr("phistory.drivers.oneshot.run", fake_run)
    result = capture.capture_target(target, cache_dir=tmp_path / "cache")

    assert result.status == "captured"
    assert json.loads(target.meta_path.read_text(encoding="utf-8"))["client_exit_code"] == 1
