import json
import tomllib
from pathlib import Path

from phistory.capture import _capture_env
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent, parse_agent_ids


def target(agent_id, tmp_path):
    agent = get_agent(agent_id)
    return CaptureTarget(agent, VersionInfo("test"), agent.default_variant, tmp_path / "captures")


def test_unverified_profiles_require_explicit_selection():
    candidates = {
        "devopsgpt",
        "critic-training",
        "empryo",
        "web3gpt",
        "atomic-agent",
        "self-operating-computer",
        "hive",
        "intentkit",
        "mirix",
        "ouroboros",
        "agent-zero",
    }
    assert not candidates.intersection(parse_agent_ids(None))
    assert set(parse_agent_ids(",".join(sorted(candidates)))) == candidates


def test_goose_uses_platform_developer_and_provider_default_model(tmp_path):
    home = tmp_path / "home"
    item = target("goose", tmp_path)
    env = _capture_env(item, tmp_path / "bin", home)
    config = json.loads((home / ".goose/config/config.yaml").read_text())
    assert config["extensions"]["developer"]["type"] == "platform"
    assert config["extensions"]["developer"]["enabled"]
    assert "GOOSE_MODEL" not in config and "GOOSE_MODEL" not in env
    assert "--model" not in item.variant.run_args
    assert env["GOOSE_PATH_ROOT"] == str(home / ".goose")
    assert env["GOOSE_CA_CERT_PATH"] == str(home / ".local/share/claude-tap/ca.pem")


def test_crush_global_config_is_directory_with_explicit_model_selection(tmp_path):
    item = target("crush", tmp_path)
    env = _capture_env(item, tmp_path / "bin", tmp_path / "home")
    from pathlib import Path

    config = json.loads((Path(env["CRUSH_GLOBAL_CONFIG"]) / "crush.json").read_text())
    assert config["models"]["large"] == {"provider": "openai", "model": "gpt-4.1"}
    assert config["models"]["small"] == config["models"]["large"]
    assert config["options"]["disable_provider_auto_update"]
    assert item.variant.dimensions["model"] == config["models"]["large"]["model"]


def test_nanobot_preserves_builtin_model_and_bub_installs_app_extra(tmp_path):
    home = tmp_path / "home"
    _capture_env(target("nanobot", tmp_path), tmp_path / "bin", home)
    config = json.loads((home / ".nanobot/config.json").read_text())
    assert "agents" not in config
    assert config["providers"]["anthropic"]["apiKey"] == "phistory-fake-api-key"
    bub = get_agent("bub")
    assert bub.package == "bub" and bub.pypi_extras == ("app",)
    assert "BUB_MODEL" not in bub.extra_env


def test_mini_configures_required_model_and_stops_dummy_format_retries(tmp_path):
    item = target("mini-swe-agent", tmp_path)
    env = _capture_env(item, tmp_path / "bin", tmp_path / "home")
    assert env["MSWEA_CONFIGURED"] == "true"
    assert env["MSWEA_MODEL_NAME"] == "openai/gpt-4.1"
    assert "agent.step_limit=1" in item.variant.run_args
    assert item.variant.dimensions["step_limit"] == "1"
    assert item.agent.capture_timeout_seconds <= 300


def test_jcode_does_not_disable_default_selfdev_tool_surface(tmp_path):
    item = target("jcode", tmp_path)
    env = _capture_env(item, tmp_path / "bin", tmp_path / "home")
    assert "--no-selfdev" not in item.variant.run_args
    assert "--model" not in item.variant.run_args
    assert env["JCODE_NON_INTERACTIVE"] == "1"
    assert env["ANTHROPIC_API_KEY"] == "phistory-fake-api-key"


def test_codewhale_keeps_toolless_default_and_separate_agent_mode(tmp_path):
    item = target("codewhale", tmp_path)
    env = _capture_env(item, tmp_path / "bin", tmp_path / "home")
    assert "--auto" not in item.variant.run_args
    assert "--auto" in item.agent.variant("auto").run_args
    assert "--model" not in item.variant.run_args
    assert tomllib.loads((Path(env["CODEWHALE_HOME"]) / "config.toml").read_text()) == {
        "update": {"check_for_updates": False}
    }


def test_cline_variant_redirects_provider_state_without_changing_gateway_default(tmp_path):
    agent = get_agent("cline")
    home = tmp_path / "home"
    native = CaptureTarget(agent, VersionInfo("3.0.74"), agent.default_variant, tmp_path)
    env = _capture_env(native, tmp_path / "bin", home)
    path = Path(env["CLINE_PROVIDER_SETTINGS_PATH"])
    assert path == Path(env["CLINE_DATA_DIR"]) / "settings/providers.json"
    assert not path.exists()
    configured = CaptureTarget(agent, VersionInfo("3.0.74"), agent.variant("anthropic"), tmp_path)
    _capture_env(configured, tmp_path / "bin", home)
    settings = json.loads(path.read_text())
    assert settings["lastUsedProvider"] == "anthropic"
    assert settings["providers"]["anthropic"]["settings"]["model"] == "claude-sonnet-5-5"


def test_letta_local_auth_does_not_leak_into_cloud_default(tmp_path):
    agent = get_agent("letta-code")
    cloud = _capture_env(target("letta-code", tmp_path), tmp_path / "bin", tmp_path / "cloud")
    local = CaptureTarget(agent, VersionInfo("test"), agent.variant("local"), tmp_path)
    env = _capture_env(local, tmp_path / "bin", tmp_path / "local")
    assert "ANTHROPIC_API_KEY" not in cloud and "LETTA_API_KEY" not in cloud
    assert env["ANTHROPIC_API_KEY"] == "phistory-fake-api-key"
    assert agent.latest_variant_ids == ("local",)
    assert agent.default_variant.capture_unavailable


def test_interpreter_uses_native_fallback_without_overriding_reserved_provider(tmp_path):
    agent = get_agent("openinterpreter")
    item = CaptureTarget(agent, VersionInfo("rust-v0.0.61"), agent.default_variant, tmp_path)
    env = _capture_env(item, tmp_path / "install/bin", tmp_path / "home")
    assert Path(env["INTERPRETER_HOME"]).is_dir()
    assert not (Path(env["INTERPRETER_HOME"]) / "config.toml").exists()
    assert "--model" not in item.variant.run_args and "--config" not in item.variant.run_args
    assert not agent.default_variant.capture_unavailable


def test_prime_preserves_bundle_and_bootstraps_kernel_before_offline_capture(tmp_path):
    agent = get_agent("prime-agent")
    env = _capture_env(target("prime-agent", tmp_path), tmp_path / "bin", tmp_path / "home")
    assert agent.release_asset_layout == "bundle"
    assert agent.bootstrap_args == ("--prime-agent-bootstrap",)
    assert agent.bootstrap_env == {"PI_OFFLINE": "0"}
    assert env["PI_OFFLINE"] == "1"
    assert env["PI_PACKAGE_DIR"] == str((tmp_path / "bin").resolve())
    assert Path(env["PRIME_AGENT_KERNEL_VENV"]).is_relative_to(tmp_path / "home")


def test_octomind_preserves_native_role_and_records_independent_tap_revision(tmp_path, monkeypatch):
    revision = "b" * 40
    monkeypatch.setattr("phistory.runtime_setup.prepare_octomind_tap", lambda *_: revision)
    monkeypatch.setattr(
        "phistory.runtime_setup.prepare_octobrain", lambda *_: {"version": "octobrain 0.15.2", "sha256": "d" * 64}
    )
    agent = get_agent("octomind")
    variant = agent.variant("http")
    item = CaptureTarget(agent, VersionInfo("0.72.0"), variant, tmp_path)
    env = _capture_env(item, tmp_path / "bin", tmp_path / "home")
    assert variant.stdin_text == "Reply with one short sentence.\n"
    assert "model.request_transport=http" in variant.run_args
    assert "--model" not in variant.run_args
    assert "assistant:chief" not in variant.run_args
    assert env["PHISTORY_OCTOMIND_TAP_COMMIT"] == revision
    assert json.loads(env["PHISTORY_OCTOMIND_DEPENDENCIES"])["octobrain"]["version"] == "octobrain 0.15.2"
    assert env["OCTOMIND_DATA_DIR"] == str(tmp_path / "home/.octomind")


def test_opendev_setup_marker_preserves_all_native_defaults(tmp_path):
    env = _capture_env(target("opendev", tmp_path), tmp_path / "bin", tmp_path / "home")
    assert json.loads((Path(env["OPENDEV_DIR"]) / "settings.json").read_text()) == {}
    assert env["FIREWORKS_API_KEY"] == "phistory-fake-api-key"
    assert "OPENDEV_MODEL" not in env and "OPENDEV_PROVIDER" not in env


def test_reasonix_stored_key_preflight_uses_isolated_dotenv(tmp_path):
    env = _capture_env(target("deepseek-reasonix", tmp_path), tmp_path / "bin", tmp_path / "home")
    assert (Path(env["REASONIX_HOME"]) / ".env").read_text() == "DEEPSEEK_API_KEY=phistory-fake-api-key\n"


def test_cheetah_native_endpoint_override_preserves_fresh_model_and_no_auth(tmp_path):
    item = target("cheetahclaws", tmp_path)
    home = tmp_path / "home"
    env = _capture_env(item, tmp_path / "bin", home)
    assert env["OLLAMA_BASE_URL"] == "http://ollama.phistory.invalid"
    assert not (home / ".cheetahclaws/config.json").exists()
    assert not any(key.endswith("API_KEY") for key in env)
    assert "--model" not in item.variant.run_args


def test_openmanus_config_and_workspace_are_copied_per_capture(tmp_path):
    install = tmp_path / "install"
    source = install / "source/openmanus"
    (source / "config").mkdir(parents=True)
    original = source / "config/config.toml"
    original.write_text("native original")
    item = target("openmanus", tmp_path)
    first = _capture_env(item, install / "bin", tmp_path / "first")
    second = _capture_env(item, install / "bin", tmp_path / "second")
    assert first["PHISTORY_SOURCE_ROOT"] != second["PHISTORY_SOURCE_ROOT"]
    assert first["PYTHONPATH"] == first["PHISTORY_SOURCE_ROOT"]
    assert original.read_text() == "native original"
    config = tomllib.loads((Path(first["PHISTORY_SOURCE_ROOT"]) / "config/config.toml").read_text())
    assert config["llm"]["model"] == "gpt-4o"
    assert config["llm"]["base_url"] == "https://api.openai.com/v1"
    assert config["daytona"]["daytona_api_key"] == "phistory-fake-daytona-key"
