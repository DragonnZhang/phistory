from pathlib import Path

from phistory.capture import _capture_env
from phistory.drivers.common import tap_command
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import AGENT_ORDER, get_agent, parse_agent_ids


def test_display_order_starts_with_dsh_in_third_position():
    assert AGENT_ORDER[:3] == ("claude-code", "codex", "dsh")


def test_parse_default_agents():
    assert parse_agent_ids(None) == [
        "claude-code",
        "codex",
        "dsh",
        "antigravity",
        "grok",
        "minimax-code",
        "kimi-code",
        "qwen-code",
        "qoder",
        "mimo",
        "openclaw",
        "hermes",
        "kimi",
        "opencode",
        "pi",
        "omp",
    ]


def test_get_agent_has_capture_contract():
    agent = get_agent("codex")

    assert agent.package == "@openai/codex"
    assert agent.tap_client == "codex"
    assert agent.fake_chatgpt_auth
    assert agent.hidden_capture_variants == ("gpt-5.6",)
    assert agent.default_variant.label == "Default"
    assert "--" in agent.default_variant.run_args
    assert "--model" not in agent.default_variant.run_args
    assert [(variant.id, variant.dimensions) for variant in agent.variants] == [
        ("gpt-5.6-sol", {"model": "gpt-5.6-sol"}),
        ("gpt-5.6-terra", {"model": "gpt-5.6-terra"}),
        ("gpt-5.6-luna", {"model": "gpt-5.6-luna"}),
        ("gpt-5.5", {"model": "gpt-5.5"}),
    ]
    for variant in agent.variants:
        model_index = variant.run_args.index("--model")
        assert variant.run_args[model_index + 1] == variant.dimensions["model"]


def test_claude_code_uses_full_prompt_surface_with_isolated_sessions():
    agent = get_agent("claude-code")

    assert agent.default_variant.label == "Non-official API"
    assert agent.default_variant.dimensions == {"api": "non-official"}
    assert agent.default_variant.tap_mode is None
    assert "--no-session-persistence" in agent.default_variant.run_args
    assert "--bare" not in agent.default_variant.run_args
    assert "--exclude-dynamic-system-prompt-sections" not in agent.default_variant.run_args
    assert [(variant.id, variant.label, variant.dimensions) for variant in agent.variants] == [
        ("official", "Official API · Sonnet 5", {"api": "official", "model": "claude-sonnet-5"}),
        (
            "official-opus",
            "Official API · Opus 5 1M",
            {"api": "official", "model": "claude-opus-5[1m]"},
        ),
        (
            "official-opus-4-8",
            "Official API · Opus 4.8 1M",
            {"api": "official", "model": "claude-opus-4-8[1m]"},
        ),
        (
            "official-opus-4-7",
            "Official API · Opus 4.7 1M",
            {"api": "official", "model": "claude-opus-4-7[1m]"},
        ),
        (
            "official-fable",
            "Official API · Fable 5",
            {"api": "official", "model": "claude-fable-5"},
        ),
    ]


def test_claude_code_official_variants_use_forward_capture_without_changing_default_mode(tmp_path: Path):
    agent = get_agent("claude-code")
    default = CaptureTarget(agent, VersionInfo("1.0.0"), agent.default_variant, tmp_path / "captures")
    default_command = tap_command(default, default.prompt_path, default.variant_dir / ".tap")

    assert agent.tap_mode == "auto"
    assert "--mode" not in default_command
    assert "--export-prompt" in default_command

    for variant_id, model in (
        ("official", "claude-sonnet-5"),
        ("official-opus", "claude-opus-5[1m]"),
        ("official-opus-4-8", "claude-opus-4-8[1m]"),
        ("official-opus-4-7", "claude-opus-4-7[1m]"),
        ("official-fable", "claude-fable-5"),
    ):
        target = CaptureTarget(agent, VersionInfo("1.0.0"), agent.variant(variant_id), tmp_path / "captures")
        command = tap_command(target, target.prompt_path, target.variant_dir / ".tap")

        assert "--mode" in command
        assert command[command.index("--mode") + 1] == "forward"
        assert "--export-prompt" in command
        assert target.variant.tap_mode == "forward"
        assert target.variant.run_args[target.variant.run_args.index("--model") + 1] == model


def test_claude_code_uses_deterministic_capture_environment(tmp_path: Path):
    agent = get_agent("claude-code")
    target = CaptureTarget(agent, VersionInfo("1.0.0"), agent.default_variant, tmp_path / "captures")

    assert agent.extra_env["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] == "off"
    assert agent.extra_env["DISABLE_GROWTHBOOK"] == "1"
    assert agent.extra_env["DISABLE_TELEMETRY"] == "1"
    assert agent.recorded_env == (
        "CLAUDE_CODE_TOTAL_TOKENS_REMINDER",
        "DISABLE_GROWTHBOOK",
        "DISABLE_TELEMETRY",
    )

    env = _capture_env(target, tmp_path / "bin", tmp_path / "home")

    assert env["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] == "off"
    assert env["DISABLE_GROWTHBOOK"] == "1"
    assert env["DISABLE_TELEMETRY"] == "1"


def test_new_agents_define_install_and_capture_profiles():
    antigravity = get_agent("antigravity")
    dsh = get_agent("dsh")
    grok = get_agent("grok")
    minimax_code = get_agent("minimax-code")
    kimi_code = get_agent("kimi-code")
    qwen_code = get_agent("qwen-code")
    qoder = get_agent("qoder")
    mimo = get_agent("mimo")
    openclaw = get_agent("openclaw")
    hermes = get_agent("hermes")
    kimi = get_agent("kimi")
    opencode = get_agent("opencode")
    pi = get_agent("pi")
    omp = get_agent("omp")

    assert antigravity.source == "github-release-asset"
    assert antigravity.package == "google-antigravity/antigravity-cli"
    assert antigravity.release_asset == "agy_cli_linux_x64.tar.gz"
    assert antigravity.release_asset_binary == "antigravity"
    assert antigravity.release_manifest_url
    assert antigravity.tap_client == "agy"
    assert antigravity.home_profile == "antigravity"
    assert antigravity.tap_mode == "forward"
    assert "--print" in antigravity.default_variant.run_args

    assert dsh.source == "npm"
    assert dsh.package == "@deepseek-ai/dsh"
    assert dsh.tap_client == "dsh"
    assert dsh.home_profile == "dsh"
    assert dsh.tap_mode == "forward"
    assert dsh.default_variant.id == "default"
    assert dsh.default_variant.driver == "dsh-web"
    assert dsh.default_variant.dimensions == {"surface": "web"}
    assert [variant.id for variant in dsh.variants] == ["headless", "standard", "code", "minimal", "cordis"]

    assert grok.source == "npm"
    assert grok.package == "@xai-official/grok"
    assert grok.tap_client == "grok"
    assert grok.fake_env == {
        "XAI_API_KEY": "phistory-fake-api-key",
        "GROK_CODE_XAI_API_KEY": "phistory-fake-api-key",
    }
    assert grok.home_profile == "grok"
    assert "--no-auto-update" in grok.default_variant.run_args
    assert "--single" in grok.default_variant.run_args

    assert minimax_code.source == "minimax-code"
    assert minimax_code.package == "MiniMax Code desktop app"
    assert minimax_code.tap_client == "minimax-code"
    assert minimax_code.tap_mode == "reverse"
    assert minimax_code.default_variant.run_args == ()

    assert kimi_code.source == "npm"
    assert kimi_code.package == "@moonshot-ai/kimi-code"
    assert kimi_code.tap_client == "kimi-code"
    assert kimi_code.executable == "kimi"
    assert kimi_code.home_profile == "kimi-code"
    assert "--prompt" in kimi_code.default_variant.run_args

    assert qwen_code.source == "npm"
    assert qwen_code.node_runtime == "node@22"
    assert qwen_code.home_profile == "qwen"
    assert qwen_code.package == "@qwen-code/qwen-code"
    assert qwen_code.tap_client == "qwen"
    assert qwen_code.tap_mode == "reverse"
    assert qwen_code.fake_env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert qwen_code.fake_env["OPENAI_MODEL"] == "qwen3.8-max"
    assert qwen_code.default_variant.run_args[0] == "--yolo"
    assert "--prompt" in qwen_code.default_variant.run_args

    assert qoder.source == "npm"
    assert qoder.package == "@qoder-ai/qodercli"
    assert qoder.tap_client == "qoder"
    assert qoder.tap_mode == "forward"
    assert qoder.inherited_env == {
        "QODER_PERSONAL_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN",
        "QODER_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN",
    }
    assert "--print" in qoder.default_variant.run_args
    assert "--no-session-persistence" in qoder.default_variant.run_args

    assert mimo.source == "npm"
    assert mimo.package == "@mimo-ai/cli"
    assert mimo.tap_client == "mimo"
    assert mimo.home_profile == "mimo"
    assert mimo.tap_mode == "reverse"
    assert "run" in mimo.default_variant.run_args
    assert "--dangerously-skip-permissions" in mimo.default_variant.run_args

    assert openclaw.source == "npm"
    assert openclaw.home_profile == "openclaw"
    assert openclaw.node_runtime == "node@24"
    assert "agent" in openclaw.default_variant.run_args

    assert hermes.source == "github-release"
    assert hermes.package == "NousResearch/hermes-agent"
    assert hermes.home_profile == "hermes"
    assert "chat" in hermes.default_variant.run_args
    assert "-q" in hermes.default_variant.run_args
    assert "openrouter" in hermes.default_variant.run_args

    assert kimi.source == "github-release"
    assert kimi.package == "MoonshotAI/kimi-cli"
    assert kimi.home_profile == "kimi"
    assert "--print" in kimi.default_variant.run_args

    assert opencode.source == "npm"
    assert opencode.package == "opencode-ai"
    assert opencode.home_profile == "opencode"
    assert opencode.tap_mode == "reverse"
    assert "run" in opencode.default_variant.run_args
    assert "--dir" in opencode.default_variant.run_args

    assert pi.source == "npm"
    assert pi.package == "@earendil-works/pi-coding-agent"
    assert pi.home_profile == "pi"
    assert pi.node_runtime is None

    assert omp.source == "npm"
    assert omp.package == "@oh-my-pi/pi-coding-agent"
    assert omp.tap_client == "omp"
    assert omp.executable == "omp"
    assert omp.home_profile == "omp"
    assert omp.binary_release_repo == "can1357/oh-my-pi"
    assert omp.binary_release_asset == "omp-linux-x64"
    assert omp.binary_release_tag == "v{version}"
    assert "--print" in omp.default_variant.run_args
