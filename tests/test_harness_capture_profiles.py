import json

from phistory.capture import _capture_env
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent, parse_agent_ids


def target(agent_id, tmp_path):
    agent = get_agent(agent_id)
    return CaptureTarget(agent, VersionInfo("test"), agent.default_variant, tmp_path / "captures")


def test_unverified_profiles_require_explicit_selection():
    candidates = {"goose", "crush", "mini-swe-agent", "nanobot", "bub", "jcode"}
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
