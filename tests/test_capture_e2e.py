import json
import os
import stat
import sys
from pathlib import Path

from phistory.capture import (
    _binary_version,
    _capture_env,
    _capture_host,
    _sanitize_text,
    _sanitize_trace,
    _without_parent_env,
    capture_target,
)
from phistory.drivers import CaptureRunContext
from phistory.drivers.oneshot import (
    _capture_timeout,
    _needs_antigravity_model_retry,
    _needs_prompt_retry,
    _needs_qoder_session_persistence_retry,
    _needs_qwen_output_format_retry,
    _without_arg,
    _without_arg_and_value,
)
from phistory.models import AgentSpec, CaptureTarget, CaptureVariant, VersionInfo
from phistory.subprocesses import run as run_subprocess


def _target(agent: AgentSpec, version: VersionInfo, root: Path) -> CaptureTarget:
    return CaptureTarget(agent, version, agent.default_variant, root)


def test_capture_target_runs_local_cli_through_tap(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(_FAKE_CODEX, encoding="utf-8")
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args, **_kwargs: bin_dir)
    static_extractions = []
    monkeypatch.setattr(
        "phistory.capture._extract_static_best_effort",
        lambda target, install_dir: static_extractions.append((target, install_dir)),
    )

    agent = AgentSpec(
        id="fake-codex",
        display_name="Fake Codex",
        package="fake-codex",
        tap_client="codex",
        fake_env={"OPENAI_API_KEY": "fake"},
        extra_env={"TEST_CAPTURE_BASELINE": "code-defaults"},
        recorded_env=("TEST_CAPTURE_BASELINE",),
        default_variant=CaptureVariant("default", "Default", ("--no-yolo", "--", "exec", "hello", "--json")),
    )
    target = _target(agent, VersionInfo("1.0.0", "2026-05-22T00:00:00Z"), tmp_path / "captures")
    target.variant_dir.mkdir(parents=True)
    target.prompt_path.write_text("old prompt\n", encoding="utf-8")
    target.trace_path.write_text("old trace\n", encoding="utf-8")
    target.meta_path.write_text("old meta\n", encoding="utf-8")

    result = capture_target(target, cache_dir=tmp_path / "cache", force=True, extract_static=False)

    assert result.status == "captured"
    assert target.prompt_path.exists()
    assert target.trace_path.exists()
    assert target.meta_path.exists()
    assert not (target.version_dir / ".home").exists()
    assert static_extractions == []

    meta = json.loads(target.meta_path.read_text(encoding="utf-8"))
    assert meta["binary_version"] == "fake-codex 1.0.0"
    assert meta["target"] == "claude-tap capture-only"
    assert meta["observed"] == {"model": "fake-model", "tool_count": 1}
    assert meta["capture_environment"] == {"TEST_CAPTURE_BASELINE": "code-defaults"}
    assert meta["capture_host"]["runner"] == "local"
    assert "run_url" not in meta["capture_host"]
    assert "-t" not in meta["command"]

    trace_records = [json.loads(line) for line in target.trace_path.read_text(encoding="utf-8").splitlines()]
    assert trace_records
    assert {record["response"]["status"] for record in trace_records} == {200}
    assert all(record["response"]["body"]["id"].startswith("resp_claude_tap_capture") for record in trace_records)

    prompt = target.prompt_path.read_text(encoding="utf-8")
    assert "Fake system prompt" in prompt
    assert str(tmp_path) not in prompt


def test_capture_host_records_actual_github_runner(monkeypatch):
    monkeypatch.setattr("phistory.capture.platform.system", lambda: "Linux")
    monkeypatch.setattr("phistory.capture.platform.machine", lambda: "x86_64")
    for key, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "owner/phistory",
        "GITHUB_WORKFLOW": "Backfill prompt history",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_JOB": "backfill",
    }.items():
        monkeypatch.setenv(key, value)

    assert _capture_host() == {
        "platform": "Linux",
        "architecture": "x86_64",
        "runner": "github-actions",
        "workflow": "Backfill prompt history",
        "run_id": "123",
        "run_attempt": "2",
        "job": "backfill",
        "run_url": "https://github.com/owner/phistory/actions/runs/123",
    }


def test_sanitize_text_normalizes_volatile_claude_headers():
    text = (
        "x-anthropic-billing-header: cc_version=2.1.146.6c9; cc_entrypoint=sdk-cli; cch=abc123;\n"
        " - OS Version: Linux 6.17.0-1013-azure\n"
        "OS Version: Linux 5.15.0-1092-azure\n"
        "Line with trailing whitespace. \t\n"
        "Today's date is 2026-05-21.\n"
        "Today's date is 5/21/2026.\n"
        "Today's date: 2026-05-21\n"
        "Today's date: 5/21/2026\n"
        "Web UI: http://127.0.0.1:23013\n"
        "The current date and time in ISO format is `2026-05-23T07:26:17.532901+00:00`.\n"
        "The current local time is: 2026-06-27T13:47:31+08:00.\n"
        "Conversation started: Friday, June 05, 2026 08:07 PM\n"
        "Conversation ID: d6609428-853a-4f4d-80e5-229becf1fff5\n"
        "  YOUR SESSION ID: mvs_f929f84029b14d2f9d430ed7c07ec713\n"
        "  YOUR SCRATCHPAD: $PHISTORY_HOME/.minimax-code/scratchpads/mvs_b8f1288efdef4a5ebf93877c7cb835a4/scratchpad.md\n"
        "  daemonPort: 23425\n"
        "  date: Mon Aug 03 2026 15:16:57 GMT+0000 (Coordinated Universal Time)\n"
        "  date: 2026-08-03 15:39:33 (UTC, UTC+0)\n"
        "  date: keep this documentation example\n"
        "<current_date>2026-05-21</current_date>\n"
        "<timezone>Etc/UTC</timezone>\n"
        "$PHISTORY_HOME/.gemini/antigravity-cli/brain/d6609428-853a-4f4d-80e5-229becf1fff5\n"
        "$PHISTORY_HOME/.claude/projects/-tmp-phistory-work-abc123/memory/\n"
        "$PHISTORY_HOME/.qwen/projects/-private-var-folders--6-1prb7x252-j-m70ky5t2h7hw0000gn-T-phistory-work-uw4e-thx/memory/MEMORY.md\n"
        "$PHISTORY_HOME/.local/share/mimocode/memory/sessions/ses_0e24f5112ffejtR0N23CyYMtYt/notes.md\n"
        "Authorization: Bearer phistory-fake-access-token\n"
        "\n"
        "\n"
        "```json"
    )

    assert _sanitize_text(text, {}) == (
        "x-anthropic-billing-header: cc_version=2.1.146.6c9; cc_entrypoint=sdk-cli; cch=<normalized>;\n"
        " - OS Version: $PHISTORY_OS_VERSION\n"
        "OS Version: $PHISTORY_OS_VERSION\n"
        "Line with trailing whitespace.\n"
        "Today's date is $PHISTORY_DATE.\n"
        "Today's date is $PHISTORY_DATE.\n"
        "Today's date: $PHISTORY_DATE\n"
        "Today's date: $PHISTORY_DATE\n"
        "Web UI: http://127.0.0.1:$PHISTORY_PORT\n"
        "The current date and time in ISO format is `$PHISTORY_DATETIME`.\n"
        "The current local time is: $PHISTORY_DATETIME.\n"
        "Conversation started: $PHISTORY_DATETIME\n"
        "Conversation ID: $PHISTORY_CONVERSATION\n"
        "  YOUR SESSION ID: $PHISTORY_SESSION\n"
        "  YOUR SCRATCHPAD: $PHISTORY_HOME/.minimax-code/scratchpads/$PHISTORY_SESSION/scratchpad.md\n"
        "  daemonPort: $PHISTORY_PORT\n"
        "  date: $PHISTORY_DATETIME\n"
        "  date: $PHISTORY_DATETIME\n"
        "  date: keep this documentation example\n"
        "<current_date>$PHISTORY_DATE</current_date>\n"
        "<timezone>$PHISTORY_TIMEZONE</timezone>\n"
        "$PHISTORY_HOME/.gemini/antigravity-cli/brain/$PHISTORY_CONVERSATION\n"
        "$PHISTORY_HOME/.claude/projects/$PHISTORY_PROJECT/memory/\n"
        "$PHISTORY_HOME/.qwen/projects/$PHISTORY_PROJECT/memory/MEMORY.md\n"
        "$PHISTORY_HOME/.local/share/mimocode/memory/sessions/$PHISTORY_SESSION/notes.md\n"
        "Authorization: Bearer <redacted>\n"
        "\n"
        "```json"
    )


def test_capture_env_writes_fake_chatgpt_auth(tmp_path: Path):
    agent = AgentSpec(
        id="codex",
        display_name="Codex",
        package="@openai/codex",
        tap_client="codex",
        fake_env={},
        fake_chatgpt_auth=True,
    )
    target = _target(agent, VersionInfo("1.0.0"), tmp_path / "captures")

    env = _capture_env(target, tmp_path / "bin", tmp_path / "home")

    auth = json.loads((tmp_path / "home" / ".codex" / "auth.json").read_text(encoding="utf-8"))
    assert auth["auth_mode"] == "chatgpt"
    assert auth["tokens"]["access_token"] == "phistory-fake-access-token"
    assert env["OPENAI_API_KEY"] == ""
    assert env["CI"] == "true"
    assert env["GITHUB_ACTIONS"] == "true"
    assert env["TZ"] == "Etc/UTC"


def test_capture_env_only_inherits_explicitly_mapped_values(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "qoder-secret-value")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    agent = AgentSpec(
        id="qoder",
        display_name="Qoder CLI",
        package="@qoder-ai/qodercli",
        tap_client="qoder",
        fake_env={},
        inherited_env={
            "QODER_PERSONAL_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN",
            "QODER_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN",
        },
    )

    env = _capture_env(_target(agent, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "home")

    assert env["QODER_PERSONAL_ACCESS_TOKEN"] == "qoder-secret-value"
    assert env["QODER_ACCESS_TOKEN"] == "qoder-secret-value"
    assert env["CI"] == "true"
    assert "GITHUB_ACTIONS" not in env
    assert "UNRELATED_SECRET" not in env


def test_capture_env_reports_missing_required_value_without_exposing_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("QODER_PERSONAL_ACCESS_TOKEN", raising=False)
    agent = AgentSpec(
        id="qoder",
        display_name="Qoder CLI",
        package="@qoder-ai/qodercli",
        tap_client="qoder",
        fake_env={},
        inherited_env={"QODER_PERSONAL_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN"},
    )

    try:
        _capture_env(_target(agent, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "home")
    except RuntimeError as exc:
        assert str(exc) == "qoder: missing required environment variable(s): QODER_PERSONAL_ACCESS_TOKEN"
    else:
        raise AssertionError("missing required environment variable was accepted")


def test_capture_failure_redacts_inherited_secret_from_error(tmp_path: Path, monkeypatch):
    secret = "qoder-secret-value"
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", secret)
    agent = AgentSpec(
        id="qoder",
        display_name="Qoder CLI",
        package="@qoder-ai/qodercli",
        tap_client="qoder",
        fake_env={},
        inherited_env={"QODER_PERSONAL_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN"},
    )

    def fail_install(*_args, **_kwargs):
        raise RuntimeError(f"server rejected {secret}")

    monkeypatch.setattr("phistory.packages.install_agent", fail_install)

    result = capture_target(
        _target(agent, VersionInfo("1.0.0"), tmp_path / "captures"),
        cache_dir=tmp_path / "cache",
    )

    assert result.status == "failed"
    assert secret not in (result.error or "")
    assert result.error == "server rejected <redacted>"


def test_subprocess_can_run_without_inheriting_parent_environment(monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")

    result = run_subprocess(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('UNRELATED_SECRET', 'missing')); print(os.environ['SAFE_VALUE'])",
        ],
        env={"SAFE_VALUE": "present"},
        inherit_env=False,
    )

    assert result.stdout.splitlines() == ["missing", "present"]


def test_capture_env_shares_npx_runtime_cache_across_versions(tmp_path: Path):
    agent = AgentSpec(
        id="runtime-agent",
        display_name="Runtime Agent",
        package="runtime-agent",
        tap_client="runtime-agent",
        fake_env={},
        node_runtime="node@22",
    )
    bin_dir = tmp_path / "cache" / "installs" / agent.id / "1.0.0" / "node_modules" / ".bin"

    env = _capture_env(_target(agent, VersionInfo("1.0.0"), tmp_path), bin_dir, tmp_path / "home")

    assert env["NPM_CONFIG_CACHE"] == str(tmp_path / "cache" / "installs" / agent.id / ".npm-runtime-cache")


def test_without_parent_env_restores_the_original_environment(monkeypatch):
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "original-secret")

    with _without_parent_env(("QODER_PERSONAL_ACCESS_TOKEN",)):
        assert "QODER_PERSONAL_ACCESS_TOKEN" not in os.environ
        os.environ["QODER_PERSONAL_ACCESS_TOKEN"] = "changed-inside-context"

    assert os.environ["QODER_PERSONAL_ACCESS_TOKEN"] == "original-secret"


def test_sanitize_trace_fully_redacts_headers_and_secret_values(tmp_path: Path):
    secret = "qoder-secret-value"
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "request": {
                    "headers": {
                        "authorization": "Bearer qoder...",
                        "Cosy-Key": "qoder-cosy-key",
                        "Cosy-MachineHostname": "developer-macbook",
                        "Cosy-MachineId": "qoder-machine-id",
                        "Cosy-MachineToken": "qoder-machine-token",
                        "Cosy-User": "qoder-user-id",
                        "x-api-key": "qoder...",
                        "X-Model-Key": "qoder-model-key",
                        "content-type": "application/json",
                    },
                    "body": {"credential": secret},
                },
                "response": {"headers": {"Authorization": "Bearer response-secret"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _sanitize_trace(trace, (secret,))

    text = trace.read_text(encoding="utf-8")
    record = json.loads(text)
    assert secret not in text
    assert record["request"]["headers"] == {
        "authorization": "***",
        "Cosy-Key": "***",
        "Cosy-MachineHostname": "***",
        "Cosy-MachineId": "***",
        "Cosy-MachineToken": "***",
        "Cosy-User": "***",
        "x-api-key": "***",
        "X-Model-Key": "***",
        "content-type": "application/json",
    }
    assert record["request"]["body"]["credential"] == "<redacted>"
    assert record["response"]["headers"]["Authorization"] == "***"


def test_capture_env_writes_agent_profile_configs(tmp_path: Path):
    antigravity = AgentSpec(
        id="antigravity",
        display_name="Antigravity",
        package="antigravity",
        tap_client="agy",
        fake_env={},
        home_profile="antigravity",
    )
    dsh = AgentSpec(
        id="dsh",
        display_name="DeepSeek Harness",
        package="@deepseek-ai/dsh",
        tap_client="dsh",
        fake_env={"DEEPSEEK_API_KEY": "fake"},
        home_profile="dsh",
    )
    openclaw = AgentSpec(
        id="openclaw",
        display_name="OpenClaw",
        package="openclaw",
        tap_client="openclaw",
        fake_env={},
        home_profile="openclaw",
    )
    hermes = AgentSpec(
        id="hermes",
        display_name="Hermes",
        package="hermes-agent",
        tap_client="hermes",
        fake_env={},
        home_profile="hermes",
    )
    grok = AgentSpec(
        id="grok",
        display_name="Grok Build",
        package="@xai-official/grok",
        tap_client="grok",
        fake_env={"XAI_API_KEY": "fake"},
        home_profile="grok",
    )
    kimi = AgentSpec(
        id="kimi",
        display_name="Kimi",
        package="kimi-cli",
        tap_client="kimi",
        fake_env={},
        home_profile="kimi",
    )
    kimi_code = AgentSpec(
        id="kimi-code",
        display_name="Kimi Code",
        package="kimi-code",
        tap_client="kimi-code",
        fake_env={},
        home_profile="kimi-code",
    )
    mimo = AgentSpec(
        id="mimo",
        display_name="MiMo Code",
        package="mimo",
        tap_client="mimo",
        fake_env={},
        home_profile="mimo",
    )
    opencode = AgentSpec(
        id="opencode",
        display_name="opencode",
        package="opencode-ai",
        tap_client="opencode",
        fake_env={},
        home_profile="opencode",
    )
    omp = AgentSpec(
        id="omp",
        display_name="Oh My Pi",
        package="omp",
        tap_client="omp",
        fake_env={},
        home_profile="omp",
    )
    pi = AgentSpec(
        id="pi",
        display_name="Pi",
        package="pi",
        tap_client="pi",
        fake_env={},
        home_profile="pi",
    )
    qwen = AgentSpec(
        id="qwen-code",
        display_name="Qwen Code",
        package="@qwen-code/qwen-code",
        tap_client="qwen",
        fake_env={},
        home_profile="qwen",
    )

    antigravity_env = _capture_env(
        _target(antigravity, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "ag"
    )
    dsh_env = _capture_env(_target(dsh, VersionInfo("0.0.1-rc.2"), tmp_path), tmp_path / "bin", tmp_path / "dsh")
    openclaw_env = _capture_env(_target(openclaw, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "oc")
    hermes_env = _capture_env(_target(hermes, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "hm")
    grok_env = _capture_env(_target(grok, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "grok")
    kimi_env = _capture_env(_target(kimi, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "km")
    kimi_code_env = _capture_env(_target(kimi_code, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "kc")
    mimo_env = _capture_env(_target(mimo, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "mm")
    opencode_env = _capture_env(_target(opencode, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "op")
    omp_env = _capture_env(_target(omp, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "omp")
    pi_env = _capture_env(_target(pi, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "pi")
    qwen_env = _capture_env(_target(qwen, VersionInfo("1.0.0"), tmp_path), tmp_path / "bin", tmp_path / "qwen")

    agy_token = json.loads(
        (Path(antigravity_env["HOME"]) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").read_text(
            encoding="utf-8"
        )
    )
    openclaw_config = json.loads(Path(openclaw_env["OPENCLAW_CONFIG_PATH"]).read_text(encoding="utf-8"))
    kimi_config = (Path(kimi_env["KIMI_SHARE_DIR"]) / "config.toml").read_text(encoding="utf-8")
    kimi_code_config = (Path(kimi_code_env["KIMI_CODE_HOME"]) / "config.toml").read_text(encoding="utf-8")
    mimo_config = json.loads(Path(mimo_env["MIMOCODE_CONFIG"]).read_text(encoding="utf-8"))
    opencode_config = json.loads(Path(opencode_env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
    omp_models = json.loads((Path(omp_env["PI_CODING_AGENT_DIR"]) / "models.json").read_text(encoding="utf-8"))
    pi_models = json.loads((Path(pi_env["PI_CODING_AGENT_DIR"]) / "models.json").read_text(encoding="utf-8"))
    qwen_settings = json.loads((Path(qwen_env["HOME"]) / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    assert agy_token["auth_method"] == "consumer"
    assert qwen_settings == {
        "selectedAuthType": "openai",
        "security": {"auth": {"selectedType": "openai"}},
    }
    assert agy_token["token"]["access_token"] == "phistory-fake-access-token"
    assert dsh_env["DSH_HOME"].endswith("/.dsh")
    assert dsh_env["DEEPSEEK_API_KEY"] == "fake"
    assert openclaw_config["models"]["providers"]["phistory"]["api"] == "openai-responses"
    assert (Path(hermes_env["HERMES_HOME"]) / "config.yaml").read_text(encoding="utf-8").startswith("model:")
    assert grok_env["GROK_HOME"].endswith(".grok")
    assert 'type = "openai_responses"' in kimi_config
    assert 'default_model = "kimi-code/kimi-for-coding"' in kimi_code_config
    assert kimi_code_env["KIMI_CODE_HOME"].endswith(".kimi-code")
    assert mimo_config["model"] == "openai/gpt-4.1"
    assert mimo_env["MIMOCODE_MIMO_ONLY"] == "false"
    assert opencode_config["model"] == "openai/gpt-4.1"
    assert omp_env["PI_CODING_AGENT_DIR"].endswith(".omp/agent")
    assert omp_models["providers"]["phistory"]["api"] == "openai-responses"
    assert pi_models["providers"]["phistory"]["api"] == "openai-responses"


def test_binary_version_falls_back_to_package_version(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "openclaw"
    executable.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    def fail_version(*_args, **_kwargs):
        raise TimeoutError("version timed out")

    monkeypatch.setattr("phistory.capture.run", fail_version)
    agent = AgentSpec(
        id="openclaw",
        display_name="OpenClaw",
        package="openclaw",
        tap_client="openclaw",
        fake_env={},
    )
    target = _target(agent, VersionInfo("2026.6.11"), tmp_path)

    assert _binary_version(target, bin_dir) == "2026.6.11"


def test_binary_version_rejects_unrelated_success_output(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "qwen"
    executable.write_text("#!/bin/sh\nprintf 'OpenAI Log Viewer\\n0.0.1\\n'\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    agent = AgentSpec(
        id="qwen-code",
        display_name="Qwen Code",
        package="@qwen-code/qwen-code",
        tap_client="qwen",
        fake_env={},
    )
    target = _target(agent, VersionInfo("0.0.1"), tmp_path)

    assert _binary_version(target, bin_dir) == "0.0.1"


def test_antigravity_model_flag_retry_removes_model_value():
    agent = AgentSpec(
        id="antigravity",
        display_name="Antigravity",
        package="antigravity",
        tap_client="agy",
        fake_env={},
    )
    target = _target(agent, VersionInfo("1.0.4"), Path("captures"))
    result = type("Result", (), {"returncode": 1, "stderr": "flags provided but not defined: -model", "stdout": ""})()

    context = CaptureRunContext(target, target.prompt_path, target.variant_dir / ".tap", Path("workspace"), {})
    assert _needs_antigravity_model_retry(context, result)
    assert _without_arg_and_value(["agy", "--print", "hello", "--model", "flash"], "--model") == [
        "agy",
        "--print",
        "hello",
    ]


def test_qwen_output_format_retry_removes_unsupported_flag():
    agent = AgentSpec(
        id="qwen-code",
        display_name="Qwen Code",
        package="@qwen-code/qwen-code",
        tap_client="qwen",
        fake_env={},
    )
    target = _target(agent, VersionInfo("0.0.1"), Path("captures"))
    result = type(
        "Result",
        (),
        {"returncode": 1, "stderr": "Unknown arguments: output-format, outputFormat", "stdout": ""},
    )()
    context = CaptureRunContext(target, target.prompt_path, target.variant_dir / ".tap", Path("workspace"), {})

    assert _needs_qwen_output_format_retry(context, result)
    assert _without_arg_and_value(["qwen", "--prompt", "hello", "--output-format", "text"], "--output-format") == [
        "qwen",
        "--prompt",
        "hello",
    ]


def test_qoder_session_persistence_retry_removes_unsupported_flag(tmp_path: Path):
    agent = AgentSpec(
        id="qoder",
        display_name="Qoder CLI",
        package="@qoder-ai/qodercli",
        tap_client="qoder",
        fake_env={},
    )
    target = _target(agent, VersionInfo("1.0.47"), tmp_path / "captures")
    result = type(
        "Result",
        (),
        {"returncode": 1, "stderr": "no valid records found in trace file", "stdout": ""},
    )()
    context = CaptureRunContext(target, target.prompt_path, target.variant_dir / ".tap", Path("workspace"), {})

    assert _capture_timeout(context) == 180
    assert _needs_qoder_session_persistence_retry(context, result)
    assert _without_arg(
        ["qoder", "--no-yolo", "--", "--print", "--no-session-persistence", "hello"],
        "--no-session-persistence",
    ) == ["qoder", "--no-yolo", "--", "--print", "hello"]


def test_no_prompt_retry_handles_claude_tap_export_failures(tmp_path: Path):
    result = type(
        "Result", (), {"returncode": 1, "stderr": "Error: no prompt-bearing request found in trace", "stdout": ""}
    )()

    assert _needs_prompt_retry(result, tmp_path / "missing.md")
    invalid_trace = type(
        "Result", (), {"returncode": 1, "stderr": "no valid records found in trace file", "stdout": ""}
    )()
    assert _needs_prompt_retry(invalid_trace, tmp_path / "missing.md")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Prompt\n", encoding="utf-8")
    assert not _needs_prompt_retry(result, prompt)


def test_capture_target_retries_transient_empty_trace(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "agent"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args, **_kwargs: bin_dir)
    monkeypatch.setattr("phistory.drivers.oneshot.time.sleep", lambda _seconds: None)

    agent = AgentSpec(
        id="agent",
        display_name="Agent",
        package="agent",
        tap_client="agent",
        fake_env={},
    )
    target = _target(agent, VersionInfo("1.0.0"), tmp_path / "captures")
    capture_attempts = 0

    def fake_run(argv, **_kwargs):
        nonlocal capture_attempts
        if argv == [str(executable), "--version"]:
            return type("Result", (), {"returncode": 0, "stdout": "agent 1.0.0\n", "stderr": ""})()
        capture_attempts += 1
        if capture_attempts == 1:
            return type(
                "Result", (), {"returncode": 1, "stdout": "", "stderr": "no valid records found in trace file"}
            )()
        target.prompt_path.write_text("# Prompt\n", encoding="utf-8")
        target.trace_path.write_text("{}\n", encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("phistory.capture.run", fake_run)
    monkeypatch.setattr("phistory.drivers.oneshot.run", fake_run)

    result = capture_target(target, cache_dir=tmp_path / "cache", force=True)

    assert result.status == "captured"
    assert capture_attempts == 2


def test_capture_failure_removes_partial_version_dir(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args, **_kwargs: bin_dir)

    agent = AgentSpec(
        id="broken-codex",
        display_name="Broken Codex",
        package="broken-codex",
        tap_client="codex",
        fake_env={"OPENAI_API_KEY": "fake"},
        default_variant=CaptureVariant("default", "Default", ("--no-yolo", "--", "exec", "hello", "--json")),
    )
    target = _target(agent, VersionInfo("1.0.0"), tmp_path / "captures")

    result = capture_target(target, cache_dir=tmp_path / "cache", force=True)

    assert result.status == "failed"
    assert not target.version_dir.exists()


def test_forced_capture_failure_preserves_existing_archive(tmp_path: Path, monkeypatch):
    agent = AgentSpec(
        id="agent",
        display_name="Agent",
        package="agent",
        tap_client="agent",
        fake_env={},
    )
    target = _target(agent, VersionInfo("1.0.0"), tmp_path / "captures")
    target.variant_dir.mkdir(parents=True)
    target.prompt_path.write_text("original prompt\n", encoding="utf-8")
    target.trace_path.write_text("original trace\n", encoding="utf-8")
    target.meta_path.write_text("original meta\n", encoding="utf-8")

    def fail_install(*_args, **_kwargs):
        raise RuntimeError("package install failed")

    monkeypatch.setattr("phistory.packages.install_agent", fail_install)

    result = capture_target(target, cache_dir=tmp_path / "cache", force=True)

    assert result.status == "failed"
    assert result.error == "package install failed"
    assert target.prompt_path.read_text(encoding="utf-8") == "original prompt\n"
    assert target.trace_path.read_text(encoding="utf-8") == "original trace\n"
    assert target.meta_path.read_text(encoding="utf-8") == "original meta\n"
    assert not list(target.root.glob(".phistory-capture-*"))


def test_capture_retries_old_claude_without_session_persistence(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(_FAKE_OLD_CLAUDE, encoding="utf-8")
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args, **_kwargs: bin_dir)

    agent = AgentSpec(
        id="claude-code",
        display_name="Claude Code",
        package="@anthropic-ai/claude-code",
        tap_client="claude",
        fake_env={"ANTHROPIC_API_KEY": "fake"},
        default_variant=CaptureVariant(
            "default", "Default", ("--no-yolo", "--", "--no-session-persistence", "-p", "hello")
        ),
    )
    target = _target(agent, VersionInfo("0.2.9"), tmp_path / "captures")

    result = capture_target(target, cache_dir=tmp_path / "cache", force=True)

    assert result.status == "captured"
    meta = json.loads(target.meta_path.read_text(encoding="utf-8"))
    assert "--no-session-persistence" not in meta["command"]


def test_capture_retries_old_codex_with_api_key(tmp_path: Path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(_FAKE_OLD_CODEX, encoding="utf-8")
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr("phistory.packages.install_agent", lambda *_args, **_kwargs: bin_dir)

    agent = AgentSpec(
        id="codex",
        display_name="Codex",
        package="@openai/codex",
        tap_client="codex",
        fake_env={},
        default_variant=CaptureVariant("default", "Default", ("--no-yolo", "--", "exec", "hello", "--json")),
        fake_chatgpt_auth=True,
    )
    target = _target(agent, VersionInfo("0.1.0"), tmp_path / "captures")

    result = capture_target(target, cache_dir=tmp_path / "cache", force=True)

    assert result.status == "captured"
    assert "Old Codex system prompt" in target.prompt_path.read_text(encoding="utf-8")


_FAKE_CODEX = """#!/usr/bin/env python3
import json
import re
import sys
import urllib.request

if "--version" in sys.argv:
    print("fake-codex 1.0.0")
    raise SystemExit(0)

base_url = None
for arg in sys.argv:
    match = re.search(r'base_url="([^"]+)"', arg)
    if match:
        base_url = match.group(1)
        break

if not base_url:
    print("missing base_url override", file=sys.stderr)
    raise SystemExit(2)

payload = {
    "model": "fake-model",
    "instructions": "Fake system prompt",
    "input": "hello",
    "tools": [
        {
            "type": "function",
            "name": "shell",
            "description": "run a command",
            "parameters": {"type": "object", "properties": {}},
        }
    ],
}
request = urllib.request.Request(
    base_url.rstrip("/") + "/responses",
    data=json.dumps(payload).encode("utf-8"),
    headers={"content-type": "application/json", "authorization": "Bearer fake"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    response.read()
"""


_FAKE_OLD_CODEX = """#!/usr/bin/env python3
import json
import os
import re
import sys
import urllib.request

if "--version" in sys.argv:
    print("codex-cli 0.1.0")
    raise SystemExit(0)

if not os.environ.get("OPENAI_API_KEY"):
    print("Missing OpenAI API key.", file=sys.stderr)
    raise SystemExit(1)

base_url = None
for arg in sys.argv:
    match = re.search(r'base_url="([^"]+)"', arg)
    if match:
        base_url = match.group(1)
        break

if not base_url:
    print("missing base_url override", file=sys.stderr)
    raise SystemExit(2)

payload = {"model": "fake-model", "instructions": "Old Codex system prompt", "input": "hello", "tools": []}
request = urllib.request.Request(
    base_url.rstrip("/") + "/responses",
    data=json.dumps(payload).encode("utf-8"),
    headers={"content-type": "application/json", "authorization": "Bearer fake"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    response.read()
"""


_FAKE_OLD_CLAUDE = """#!/usr/bin/env python3
import json
import os
import sys
import urllib.request

if "--version" in sys.argv:
    print("0.2.9 (Claude Code)")
    raise SystemExit(0)

if "--no-session-persistence" in sys.argv:
    print("error: unknown option '--no-session-persistence'", file=sys.stderr)
    raise SystemExit(1)

base_url = os.environ.get("ANTHROPIC_BASE_URL")
if not base_url:
    print("missing ANTHROPIC_BASE_URL", file=sys.stderr)
    raise SystemExit(2)

payload = {
    "model": "fake-claude",
    "messages": [{"role": "user", "content": "hello"}],
    "system": [{"type": "text", "text": "Old Claude system prompt"}],
    "tools": [],
}
request = urllib.request.Request(
    base_url.rstrip("/") + "/v1/messages",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json", "x-api-key": "fake", "anthropic-version": "2023-06-01"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    response.read()
"""
