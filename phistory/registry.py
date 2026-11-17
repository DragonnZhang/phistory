from __future__ import annotations

from phistory.models import AgentSpec, CaptureDriver, CaptureVariant, HomeProfile, TapMode
from phistory.task_entries import CTXAGENT_PREFETCH, CTXAGENT_TASK, QUORUM_TASK


def _default(
    run_args: tuple[str, ...] = (),
    *,
    label: str = "Default",
    driver: CaptureDriver = "oneshot",
    dimensions: dict[str, str] | None = None,
    tap_mode: TapMode | None = None,
    capture_unavailable: str | None = None,
) -> CaptureVariant:
    return CaptureVariant(
        id="default",
        label=label,
        run_args=run_args,
        driver=driver,
        dimensions=dimensions or {},
        tap_mode=tap_mode,
        capture_unavailable=capture_unavailable,
    )


def _variant(
    variant_id: str,
    label: str,
    run_args: tuple[str, ...],
    *,
    driver: CaptureDriver = "oneshot",
    dimensions: dict[str, str] | None = None,
    tap_mode: TapMode | None = None,
    min_version: str | None = None,
    home_profile: HomeProfile | None = None,
    extra_env: dict[str, str] | None = None,
    stdin_text: str | None = None,
) -> CaptureVariant:
    return CaptureVariant(
        id=variant_id,
        label=label,
        run_args=run_args,
        driver=driver,
        dimensions=dimensions or {},
        tap_mode=tap_mode,
        min_version=min_version,
        home_profile=home_profile,
        extra_env=extra_env or {},
        stdin_text=stdin_text,
    )


CLAUDE_CODE = AgentSpec(
    id="claude-code",
    display_name="Claude Code",
    package="@anthropic-ai/claude-code",
    tap_client="claude",
    fake_env={"ANTHROPIC_API_KEY": "fake"},
    extra_env={
        "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_GROWTHBOOK": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    recorded_env=(
        "CLAUDE_CODE_TOTAL_TOKENS_REMINDER",
        "DISABLE_GROWTHBOOK",
        "DISABLE_TELEMETRY",
    ),
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--no-session-persistence",
            "-p",
            "Reply with one short sentence.",
        ),
        tap_mode="forward",
    ),
    variants=(
        _variant(
            "non-official",
            "Non-official API",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "non-official"},
        ),
        _variant(
            "official-fable-5-2",
            "Official API · Fable 5.2",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-fable-5-2",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-fable-5-2"},
            tap_mode="forward",
            min_version="2.1.321",
        ),
        _variant(
            "official-fable-5-1",
            "Official API · Fable 5.1",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-fable-5-1",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-fable-5-1"},
            tap_mode="forward",
        ),
        _variant(
            "official-fable",
            "Official API · Fable 5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-fable-5",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-fable-5"},
            tap_mode="forward",
        ),
        _variant(
            "official-opus-5-5",
            "Official API · Opus 5.5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-opus-5-5[1m]",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-opus-5-5[1m]"},
            tap_mode="forward",
        ),
        _variant(
            "official-opus",
            "Official API · Opus 5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-opus-5[1m]",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-opus-5[1m]"},
            tap_mode="forward",
        ),
        _variant(
            "official-opus-4-8",
            "Official API · Opus 4.8",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-opus-4-8[1m]",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-opus-4-8[1m]"},
            tap_mode="forward",
        ),
        _variant(
            "official-opus-4-7",
            "Official API · Opus 4.7",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-opus-4-7[1m]",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-opus-4-7[1m]"},
            tap_mode="forward",
        ),
        _variant(
            "official-sonnet-5-5",
            "Official API · Sonnet 5.5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-sonnet-5-5",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-sonnet-5-5"},
            tap_mode="forward",
            min_version="2.1.284",
        ),
        _variant(
            "official",
            "Official API · Sonnet 5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-sonnet-5",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-sonnet-5"},
            tap_mode="forward",
        ),
        _variant(
            "official-haiku-5-5",
            "Official API · Haiku 5.5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-haiku-5-5",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-haiku-5-5"},
            tap_mode="forward",
            min_version="2.1.293",
        ),
        _variant(
            "official-haiku",
            "Official API · Haiku 4.5",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-haiku-4-5",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-haiku-4-5"},
            tap_mode="forward",
        ),
        _variant(
            "official-mythos-5-2",
            "Official API · Mythos 5.2 (verification required)",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-mythos-5-2",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-mythos-5-2", "availability": "verification-required"},
            tap_mode="forward",
            min_version="2.1.321",
        ),
        _variant(
            "official-mythos-5-1",
            "Official API · Mythos 5.1 (verification required)",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-mythos-5-1",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-mythos-5-1", "availability": "verification-required"},
            tap_mode="forward",
            min_version="2.1.296",
        ),
        _variant(
            "official-mythos",
            "Official API · Mythos 5 (verification required)",
            (
                "--no-yolo",
                "--",
                "--no-session-persistence",
                "--model",
                "claude-mythos-5",
                "-p",
                "Reply with one short sentence.",
            ),
            dimensions={"api": "official", "model": "claude-mythos-5", "availability": "verification-required"},
            tap_mode="forward",
            min_version="2.1.296",
        ),
    ),
)

CODEX = AgentSpec(
    id="codex",
    display_name="Codex CLI",
    package="@openai/codex",
    tap_client="codex",
    fake_env={},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    fake_chatgpt_auth=True,
    hidden_capture_variants=("gpt-5.6",),
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "exec",
            "Reply with one short sentence.",
            "--skip-git-repo-check",
            "--json",
        ),
    ),
    variants=(
        _variant(
            "non-official",
            "Non-official · Qwen3 Coder Plus",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "qwen3-coder-plus",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "qwen3-coder-plus", "model_family": "non-official"},
        ),
        _variant(
            "gpt-6.1-sol",
            "GPT-6.1 Sol",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-6.1-sol",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-6.1-sol"},
            min_version="0.159.1",
        ),
        _variant(
            "gpt-6.1-terra",
            "GPT-6.1 Terra",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-6.1-terra",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-6.1-terra"},
            min_version="0.165.0",
        ),
        _variant(
            "gpt-6-astra",
            "GPT-6 Astra",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-6-astra",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-6-astra"},
            min_version="0.153.1",
        ),
        _variant(
            "gpt-6-sol",
            "GPT-6 Sol",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-6-sol",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-6-sol"},
            min_version="0.157.0-alpha.10",
        ),
        _variant(
            "gpt-6-luna",
            "GPT-6 Luna",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-6-luna",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-6-luna"},
            min_version="0.157.0-alpha.10",
        ),
        _variant(
            "gpt-5.6-sol",
            "GPT-5.6 Sol",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-5.6-sol",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-5.6-sol"},
            min_version="0.144.0",
        ),
        _variant(
            "gpt-5.6-terra",
            "GPT-5.6 Terra",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-5.6-terra",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-5.6-terra"},
            min_version="0.144.0",
        ),
        _variant(
            "gpt-5.6-luna",
            "GPT-5.6 Luna",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-5.6-luna",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-5.6-luna"},
            min_version="0.144.0",
        ),
        _variant(
            "gpt-5.5",
            "GPT-5.5",
            (
                "--no-yolo",
                "--",
                "exec",
                "Reply with one short sentence.",
                "--model",
                "gpt-5.5",
                "--skip-git-repo-check",
                "--json",
            ),
            dimensions={"model": "gpt-5.5"},
            min_version="0.125.0",
        ),
    ),
)

ANTIGRAVITY = AgentSpec(
    id="antigravity",
    display_name="Antigravity CLI",
    package="google-antigravity/antigravity-cli",
    source="github-release-asset",
    release_asset="agy_cli_linux_x64.tar.gz",
    release_asset_binary="antigravity",
    release_manifest_url="https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json",
    tap_client="agy",
    fake_env={},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    home_profile="antigravity",
    tap_mode="forward",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--print",
            "Reply with one short sentence.",
            "--print-timeout",
            "1s",
            "--dangerously-skip-permissions",
            "--model",
            "MODEL_GOOGLE_GEMINI_2_5_FLASH",
        )
    ),
)

DSH = AgentSpec(
    id="dsh",
    display_name="DeepSeek Harness",
    package="@deepseek-ai/dsh",
    tap_client="dsh",
    fake_env={"DEEPSEEK_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "DSH_TELEMETRY_DISABLED": "1",
        "CI": "1",
    },
    home_profile="dsh",
    tap_mode="forward",
    default_variant=_default(
        ("--no-yolo", "--", "web"),
        driver="dsh-web",
        dimensions={"surface": "web"},
    ),
    variants=(
        _variant(
            "headless",
            "Headless",
            ("--no-yolo", "--", "--profile", "headless", "Reply with one short sentence."),
            dimensions={"surface": "headless"},
        ),
        _variant(
            "standard",
            "Standard",
            ("--no-yolo", "--", "web"),
            driver="dsh-web",
            dimensions={"surface": "web", "mode": "standard"},
        ),
        _variant(
            "code",
            "PTC",
            ("--no-yolo", "--", "web"),
            driver="dsh-web",
            dimensions={"surface": "web", "mode": "ptc"},
        ),
        _variant(
            "minimal",
            "Minimal",
            ("--no-yolo", "--", "web"),
            driver="dsh-web",
            dimensions={"surface": "web", "mode": "minimal"},
        ),
        _variant(
            "cordis",
            "Creator",
            ("--no-yolo", "--", "web"),
            driver="dsh-web",
            dimensions={"surface": "web", "mode": "cordis"},
        ),
    ),
)

GROK = AgentSpec(
    id="grok",
    display_name="Grok Build",
    package="@xai-official/grok",
    tap_client="grok",
    fake_env={
        "XAI_API_KEY": "phistory-fake-api-key",
        "GROK_CODE_XAI_API_KEY": "phistory-fake-api-key",
    },
    extra_env={
        "GROK_DISABLE_AUTOUPDATER": "1",
        "GROK_TELEMETRY_ENABLED": "false",
        "GROK_FEEDBACK_ENABLED": "false",
        "GROK_TRACE_UPLOAD": "false",
        "GROK_INSTRUMENTATION": "disabled",
        "CI": "1",
    },
    home_profile="grok",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--no-auto-update",
            "--single",
            "Reply with one short sentence.",
        )
    ),
)

MINIMAX_CODE = AgentSpec(
    id="minimax-code",
    display_name="MiniMax Code",
    package="MiniMax Code desktop app",
    source="minimax-code",
    tap_client="minimax-code",
    fake_env={},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "LOG_LEVEL": "fatal",
        "CI": "1",
    },
    tap_mode="reverse",
    default_variant=_default(),
)

KIMI_CODE = AgentSpec(
    id="kimi-code",
    display_name="Kimi Code",
    package="@moonshot-ai/kimi-code",
    tap_client="kimi-code",
    executable="kimi",
    fake_env={
        "KIMI_API_KEY": "phistory-fake-api-key",
        "MOONSHOT_API_KEY": "phistory-fake-api-key",
    },
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "KIMI_TELEMETRY_DISABLED": "1",
        "CI": "1",
    },
    home_profile="kimi-code",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--prompt",
            "Reply with one short sentence.",
            "--output-format",
            "text",
        )
    ),
)

QWEN_CODE = AgentSpec(
    id="qwen-code",
    display_name="Qwen Code",
    package="@qwen-code/qwen-code",
    tap_client="qwen",
    node_runtime="node@22",
    home_profile="qwen",
    fake_env={
        "OPENAI_API_KEY": "phistory-fake-api-key",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "OPENAI_MODEL": "qwen3.8-max",
    },
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "QWEN_CODE_SKIP_UPDATE_CHECK_ONCE": "true",
        "CI": "1",
    },
    tap_mode="reverse",
    default_variant=_default(
        (
            "--yolo",
            "--prompt",
            "Reply with one short sentence.",
            "--output-format",
            "text",
        )
    ),
)

QODER = AgentSpec(
    id="qoder",
    display_name="Qoder CLI",
    package="@qoder-ai/qodercli",
    tap_client="qoder",
    fake_env={},
    inherited_env={
        "QODER_PERSONAL_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN",
        "QODER_ACCESS_TOKEN": "QODER_PERSONAL_ACCESS_TOKEN",
    },
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
        "PHISTORY_QODER_CAPTURE_BOOTSTRAP": "model-availability",
    },
    recorded_env=("PHISTORY_QODER_CAPTURE_BOOTSTRAP",),
    tap_mode="forward",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--print",
            "--no-session-persistence",
            "Reply with one short sentence.",
        )
    ),
)

MIMO = AgentSpec(
    id="mimo",
    display_name="MiMo Code",
    package="@mimo-ai/cli",
    tap_client="mimo",
    fake_env={
        "OPENAI_API_KEY": "phistory-fake-api-key",
        "ANTHROPIC_API_KEY": "phistory-fake-api-key",
    },
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    home_profile="mimo",
    tap_mode="reverse",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "run",
            "Reply with one short sentence.",
            "--model",
            "openai/gpt-4.1",
            "--format",
            "json",
            "--dir",
            ".",
            "--dangerously-skip-permissions",
        )
    ),
)

OPENCLAW = AgentSpec(
    id="openclaw",
    display_name="OpenClaw",
    package="openclaw",
    tap_client="openclaw",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    node_runtime="node@24",
    home_profile="openclaw",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "agent",
            "--local",
            "--agent",
            "main",
            "--message",
            "Reply with one short sentence.",
            "--json",
            "--timeout",
            "20",
        )
    ),
)

HERMES = AgentSpec(
    id="hermes",
    display_name="Hermes Agent",
    package="NousResearch/hermes-agent",
    source="github-release",
    github_release_install="editable",
    tap_client="hermes",
    fake_env={
        "OPENAI_API_KEY": "phistory-fake-api-key",
        "OPENROUTER_API_KEY": "phistory-fake-api-key",
    },
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "HERMES_ACCEPT_HOOKS": "1",
    },
    home_profile="hermes",
    tap_mode="reverse",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "chat",
            "-q",
            "Reply with one short sentence.",
            "--yolo",
            "-Q",
            "--provider",
            "openrouter",
            "--model",
            "phistory-dummy",
        )
    ),
)

KIMI = AgentSpec(
    id="kimi",
    display_name="Kimi CLI",
    package="MoonshotAI/kimi-cli",
    source="github-release",
    # 1.52.0 replaced the CLI with a migration notice: MoonshotAI/kimi-cli#2666.
    track_latest=False,
    tap_client="kimi",
    fake_env={
        "OPENAI_API_KEY": "phistory-fake-api-key",
        "KIMI_API_KEY": "phistory-fake-api-key",
        "MOONSHOT_API_KEY": "phistory-fake-api-key",
    },
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "KIMI_TELEMETRY_DISABLED": "1",
    },
    home_profile="kimi",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--print",
            "--prompt",
            "Reply with one short sentence.",
            "--model",
            "phistory-dummy",
            "--output-format",
            "text",
        )
    ),
)

OPENCODE = AgentSpec(
    id="opencode",
    display_name="opencode",
    package="opencode-ai",
    tap_client="opencode",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    home_profile="opencode",
    tap_mode="reverse",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "run",
            "Reply with one short sentence.",
            "--model",
            "openai/gpt-4.1",
            "--format",
            "json",
            "--dir",
            ".",
        )
    ),
)

PI = AgentSpec(
    id="pi",
    display_name="Pi",
    package="@earendil-works/pi-coding-agent",
    tap_client="pi",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    home_profile="pi",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--provider",
            "phistory",
            "--model",
            "gpt-4.1",
            "--print",
            "--mode",
            "text",
            "--no-session",
            "Reply with one short sentence.",
        )
    ),
)

OMP = AgentSpec(
    id="omp",
    display_name="Oh My Pi",
    package="@oh-my-pi/pi-coding-agent",
    tap_client="omp",
    executable="omp",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_UPDATES": "1",
        "CI": "1",
    },
    binary_release_repo="can1357/oh-my-pi",
    binary_release_asset="omp-linux-x64",
    binary_release_tag="v{version}",
    home_profile="omp",
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--print",
            "--mode",
            "text",
            "--no-session",
            "--approval-mode",
            "yolo",
            "--model",
            "phistory/gpt-4.1",
            "Reply with one short sentence.",
        )
    ),
)

GEMINI_CLI = AgentSpec(
    id="gemini-cli",
    display_name="Gemini CLI",
    package="@google/gemini-cli",
    tap_client="gemini",
    executable="gemini",
    fake_env={"GEMINI_API_KEY": "phistory-fake-api-key"},
    tap_mode="forward",
    extra_env={"CI": "1", "GEMINI_TELEMETRY_ENABLED": "false", "GEMINI_CLI_TRUST_WORKSPACE": "true"},
    recorded_env=("GEMINI_TELEMETRY_ENABLED", "GEMINI_CLI_TRUST_WORKSPACE"),
    capture_timeout_seconds=300,
    default_variant=_default(("--no-yolo", "--", "-p", "Reply with one short sentence.")),
)


GOOSE = AgentSpec(
    id="goose",
    display_name="Goose",
    package="aaif-goose/goose",
    source="github-release-asset",
    release_asset="goose-x86_64-unknown-linux-gnu.tar.gz",
    release_asset_binary="goose",
    tap_client="goose",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={"GOOSE_DISABLE_KEYRING": "1"},
    recorded_env=("GOOSE_DISABLE_KEYRING",),
    home_profile="goose",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(
        ("--no-yolo", "--", "run", "--provider", "openai", "--text", "Reply with one short sentence.", "--no-session"),
        label="Default · OpenAI provider",
        dimensions={"provider": "openai", "extensions": "platform developer"},
    ),
)

CRUSH = AgentSpec(
    id="crush",
    display_name="Crush",
    package="@charmland/crush",
    tap_client="crush",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={"CRUSH_DISABLE_PROVIDER_AUTO_UPDATE": "1", "CRUSH_DISABLE_METRICS": "1"},
    recorded_env=("CRUSH_DISABLE_PROVIDER_AUTO_UPDATE", "CRUSH_DISABLE_METRICS"),
    home_profile="crush",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(
        ("--no-yolo", "--", "run", "Reply with one short sentence."),
        label="Default · configured OpenAI",
        dimensions={"provider": "openai", "model": "gpt-4.1"},
    ),
)

MINI_SWE_AGENT = AgentSpec(
    id="mini-swe-agent",
    display_name="mini-swe-agent",
    package="mini-swe-agent",
    source="pypi",
    tap_client="mini",
    executable="mini",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "MSWEA_CONFIGURED": "true",
        "MSWEA_MODEL_NAME": "openai/gpt-4.1",
        "MSWEA_COST_TRACKING": "ignore_errors",
    },
    recorded_env=("MSWEA_CONFIGURED", "MSWEA_MODEL_NAME", "MSWEA_COST_TRACKING"),
    tap_mode="forward",
    capture_timeout_seconds=300,
    default_variant=_default(
        (
            "--no-yolo",
            "--",
            "--task",
            "Reply with one short sentence.",
            "--yolo",
            "--exit-immediately",
            "-c",
            "mini.yaml",
            "-c",
            "agent.step_limit=1",
        ),
        label="Default · configured OpenAI",
        dimensions={"provider": "openai", "model": "gpt-4.1", "config": "mini.yaml", "step_limit": "1"},
    ),
)

NANOBOT = AgentSpec(
    id="nanobot",
    display_name="nanobot",
    package="nanobot-ai",
    source="pypi",
    tap_client="nanobot",
    fake_env={"ANTHROPIC_API_KEY": "phistory-fake-api-key"},
    home_profile="nanobot",
    tap_mode="forward",
    capture_timeout_seconds=180,
    default_variant=_default(("--no-yolo", "--", "agent", "--message", "Reply with one short sentence.")),
)

BUB = AgentSpec(
    id="bub",
    display_name="Bub",
    package="bub",
    source="pypi",
    pypi_extras=("app",),
    tap_client="bub",
    fake_env={"BUB_API_KEY": "phistory-fake-api-key", "BUB_OPENROUTER_API_KEY": "phistory-fake-api-key"},
    extra_env={"BUB_MAX_STEPS": "1"},
    recorded_env=("BUB_MAX_STEPS",),
    home_profile="bub",
    tap_mode="forward",
    capture_timeout_seconds=180,
    default_variant=_default(("--no-yolo", "--", "run", "Reply with one short sentence.")),
)

JCODE = AgentSpec(
    id="jcode",
    display_name="Jcode",
    package="1jehuang/jcode",
    source="github-release-asset",
    release_asset="jcode-linux-x86_64.tar.gz",
    release_asset_binary="jcode-linux-x86_64",
    release_asset_layout="bundle",
    tap_client="jcode",
    fake_env={"ANTHROPIC_API_KEY": "phistory-fake-api-key"},
    extra_env={"JCODE_NON_INTERACTIVE": "1", "JCODE_RUN_AUTO_POKE": "0"},
    recorded_env=("JCODE_NON_INTERACTIVE", "JCODE_RUN_AUTO_POKE"),
    home_profile="jcode",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(("--no-yolo", "--", "--no-update", "run", "--json", "Reply with one short sentence.")),
)


PI_RUST = AgentSpec(
    id="pi-rust",
    display_name="pi-rust",
    package="@bigfishnpm/rpi",
    tap_client="rpi",
    fake_env={"ANTHROPIC_API_KEY": "phistory-fake-api-key"},
    extra_env={"RPI_OFFLINE": "1", "ANTHROPIC_BASE_URL": "http://api.anthropic.com"},
    recorded_env=("RPI_OFFLINE", "ANTHROPIC_BASE_URL"),
    home_profile="pi-rust",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(
        ("--no-yolo", "--", "-p", "Reply with one short sentence.", "--no-session"),
        dimensions={"transport": "HTTP capture-only proxy; native Anthropic provider/model"},
    ),
)

CODEWHALE = AgentSpec(
    id="codewhale",
    display_name="Codewhale",
    package="codewhale",
    tap_client="codewhale",
    fake_env={"DEEPSEEK_API_KEY": "phistory-fake-api-key"},
    home_profile="codewhale",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(
        ("--no-yolo", "--", "exec", "--output-format", "stream-json", "Reply with one short sentence."),
        label="Default · exec without tools",
        dimensions={"mode": "exec", "tools": "none"},
    ),
    variants=(
        _variant(
            "auto",
            "Auto · agent tools",
            ("--no-yolo", "--", "exec", "--auto", "--output-format", "stream-json", "Reply with one short sentence."),
            dimensions={"mode": "auto"},
        ),
    ),
)

OPENDEV = AgentSpec(
    id="opendev",
    display_name="OpenDev",
    package="opendev-to/opendev",
    source="github-release-asset",
    release_asset="opendev-cli-x86_64-unknown-linux-gnu.tar.xz",
    release_asset_binary="opendev",
    tap_client="opendev",
    fake_env={"FIREWORKS_API_KEY": "phistory-fake-api-key"},
    extra_env={"OPENDEV_DISABLE_REMOTE_MODELS": "1"},
    recorded_env=("OPENDEV_DISABLE_REMOTE_MODELS",),
    home_profile="opendev",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(("--no-yolo", "--", "-p", "Reply with one short sentence.")),
)

KILOCODE = AgentSpec(
    id="kilocode",
    display_name="Kilo Code",
    package="@kilocode/cli",
    tap_client="kilo",
    fake_env={},
    extra_env={"KILO_DISABLE_MODELS_FETCH": "1", "KILO_DISABLE_AUTOUPDATE": "1"},
    recorded_env=("KILO_DISABLE_MODELS_FETCH", "KILO_DISABLE_AUTOUPDATE"),
    home_profile="kilocode",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(
        ("--no-yolo", "--", "run", "--format", "json", "Reply with one short sentence."),
        label="Default · configured compatible provider",
        dimensions={"provider": "OpenAI-compatible", "model": "gpt-4.1"},
    ),
)

CLINE = AgentSpec(
    id="cline",
    display_name="Cline",
    package="cline",
    tap_client="cline",
    fake_env={},
    home_profile="cline",
    tap_mode="forward",
    capture_timeout_seconds=240,
    default_variant=_default(
        ("--no-yolo", "--", "--key", "phistory-fake-api-key", "--json", "Reply with one short sentence.")
    ),
    variants=(
        _variant(
            "anthropic",
            "Configured Anthropic",
            ("--no-yolo", "--", "--json", "Reply with one short sentence."),
            home_profile="cline-anthropic",
            dimensions={"provider": "anthropic", "model": "claude-sonnet-5-5"},
        ),
    ),
)

LETTA_CODE = AgentSpec(
    id="letta-code",
    display_name="Letta Code",
    package="@letta-ai/letta-code",
    tap_client="letta",
    fake_env={},
    home_profile="letta-code",
    tap_mode="forward",
    capture_timeout_seconds=240,
    latest_variant_ids=("local",),
    default_variant=_default(
        ("--no-yolo", "--", "-p", "Reply with one short sentence."),
        capture_unavailable="Cloud backend constructs its model request on Letta's server; local HTTP interception cannot archive the complete model system prompt and tools.",
    ),
    variants=(
        _variant(
            "local",
            "Local backend",
            ("--no-yolo", "--", "--backend", "local", "-p", "Reply with one short sentence."),
            extra_env={"ANTHROPIC_API_KEY": "phistory-fake-api-key"},
            dimensions={"backend": "local", "provider": "anthropic"},
        ),
    ),
)

PRIME_AGENT = AgentSpec(
    id="prime-agent",
    display_name="Prime Agent",
    package="PrimeIntellect-ai/prime-agent",
    source="github-release-asset",
    release_asset="prime-agent-{version_number}-linux-x64.tar.gz",
    release_asset_binary="prime-agent",
    release_asset_layout="bundle",
    tap_client="prime-agent",
    fake_env={"ANTHROPIC_API_KEY": "phistory-fake-api-key"},
    extra_env={"PI_OFFLINE": "1"},
    recorded_env=("PI_OFFLINE",),
    home_profile="prime-agent",
    bootstrap_args=("--prime-agent-bootstrap",),
    bootstrap_env={"PI_OFFLINE": "0"},
    tap_mode="forward",
    capture_timeout_seconds=300,
    default_variant=_default(("--no-yolo", "--", "--print", "--no-session", "Reply with one short sentence.")),
)

OPENINTERPRETER = AgentSpec(
    id="openinterpreter",
    display_name="OpenInterpreter",
    package="openinterpreter/openinterpreter",
    source="github-release-asset",
    release_asset="open-interpreter-package-x86_64-unknown-linux-musl.tar.gz",
    release_asset_binary="interpreter",
    release_asset_layout="bundle",
    release_tag_prefix="rust-v",
    tap_client="interpreter",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={"OPEN_INTERPRETER_NONINTERACTIVE": "1"},
    recorded_env=("OPEN_INTERPRETER_NONINTERACTIVE",),
    home_profile="interpreter",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=300,
    default_variant=_default(
        ("--no-yolo", "--", "exec", "--skip-git-repo-check", "--ephemeral", "--json", "Reply with one short sentence."),
        dimensions={"transport": "native HTTP fallback after capture-only proxy refuses WebSocket with HTTP 426"},
    ),
)


FIBONA = AgentSpec(
    id="fibona-agent",
    display_name="Fibona · Python",
    package="FibonaAI/fibona-agent",
    source="github-release",
    github_release_install="editable",
    tap_client="fibona",
    fake_env={"FIBONA_API_KEY": "phistory-fake-api-key"},
    home_profile="fibona",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=240,
    latest_variant_ids=("minimal", "terminal"),
    default_variant=_default(
        ("--no-yolo", "--", "Reply with one short sentence."),
        capture_unavailable="The native Python CLI requires an explicit boot-mode; capture the named minimal or terminal bootstrap surface.",
    ),
    variants=tuple(
        _variant(
            mode,
            f"Python · {mode} bootstrap",
            ("--no-yolo", "--", "--boot-mode", mode, "Reply with one short sentence."),
            dimensions={"runtime": "Python source package", "bootstrap": mode},
        )
        for mode in ("minimal", "terminal")
    ),
)

CTXAGENT = AgentSpec(
    id="ctxagent",
    display_name="CTXAgent · paper runtime",
    package="MatthiasHBusch/CTXAgent",
    source="github-release",
    github_release_install="editable",
    tap_client="python",
    fake_env={"OPENROUTER_API_KEY": "phistory-fake-api-key"},
    home_profile="ctxagent",
    bootstrap_args=("-c", CTXAGENT_PREFETCH),
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=240,
    latest_variant_ids=("paper-api",),
    default_variant=_default(
        capture_unavailable="The native GUI/server needs an interactive driver; use the named paper-api task entry for the root Python runtime.",
    ),
    variants=(
        _variant(
            "paper-api",
            "Paper runtime · AgentSession API",
            ("--no-yolo", "--", "-c", CTXAGENT_TASK),
            dimensions={
                "runtime": "root Python paper package",
                "entry": "AgentSession.run_turn",
                "provider_policy": "eu_us",
                "optional_readers": "not installed",
            },
        ),
    ),
)

REASONIX = AgentSpec(
    id="deepseek-reasonix",
    display_name="DeepSeek Reasonix · Studio",
    package="esengine/DeepSeek-Reasonix",
    source="github-release-asset",
    release_tag_prefix="studio-v",
    release_asset="reasonix-linux-amd64.tar.gz",
    release_asset_binary="reasonix",
    tap_client="reasonix",
    fake_env={"DEEPSEEK_API_KEY": "phistory-fake-api-key"},
    home_profile="reasonix",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=240,
    default_variant=_default(("--no-yolo", "--", "-p", "Reply with one short sentence.", "--output-format", "json")),
)

QUORUM = AgentSpec(
    id="quorum-cli",
    display_name="Quorum CLI",
    package="quorum-cli",
    source="pypi",
    tap_client="python",
    fake_env={"OPENAI_API_KEY": "phistory-fake-api-key"},
    extra_env={"OPENAI_MODELS": "gpt-4.1,gpt-4.1-mini", "QUORUM_ROUNDS_PER_AGENT": "1"},
    recorded_env=("OPENAI_MODELS", "QUORUM_ROUNDS_PER_AGENT"),
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=300,
    latest_variant_ids=("standard-api",),
    default_variant=_default(
        capture_unavailable="The native terminal UI requires configured participants and an interactive driver; use the named standard-api discussion surface."
    ),
    variants=(
        _variant(
            "standard-api",
            "Standard discussion · configured OpenAI API participants",
            ("--no-yolo", "--", "-c", QUORUM_TASK),
            dimensions={
                "entry": "FourPhaseConsensusTeam.run_stream",
                "method": "standard",
                "participants": "openai:gpt-4.1,openai:gpt-4.1-mini",
                "rounds_per_agent": "1",
                "tools": "none for API participants",
            },
        ),
    ),
)

TIANSHU = AgentSpec(
    id="tianshu-harness",
    display_name="Tianshu Harness",
    package="tianshu-harness",
    tap_client="tianshu",
    fake_env={"DEEPSEEK_API_KEY": "phistory-fake-api-key"},
    extra_env={"RIVET_NO_UPDATE_CHECK": "1"},
    recorded_env=("RIVET_NO_UPDATE_CHECK",),
    home_profile="tianshu",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=240,
    default_variant=_default(("--no-yolo", "--", "-p", "Reply with one short sentence.", "--json")),
    variants=(
        _variant(
            "full-tools",
            "Full tool preset",
            ("--no-yolo", "--", "-p", "Reply with one short sentence.", "--json"),
            extra_env={"RIVET_TOOL_PRESET": "full"},
            dimensions={"tool_preset": "full"},
        ),
    ),
)


OCTOMIND = AgentSpec(
    id="octomind",
    display_name="Octomind",
    package="Muvon/octomind",
    source="github-release-asset",
    release_asset="octomind-{version_number}-x86_64-unknown-linux-musl.tar.gz",
    release_asset_binary="octomind",
    tap_client="octomind",
    fake_env={"OCTOHUB_API_KEY": "phistory-fake-api-key"},
    extra_env={
        "OCTOMIND_TELEMETRY": "0",
        "PHISTORY_OCTOMIND_TAP_COMMIT": "",
        "PHISTORY_OCTOMIND_TAP_REPOSITORY": "https://github.com/muvon/octomind-tap",
    },
    recorded_env=("OCTOMIND_TELEMETRY", "PHISTORY_OCTOMIND_TAP_COMMIT", "PHISTORY_OCTOMIND_TAP_REPOSITORY"),
    home_profile="octomind",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=300,
    latest_variant_ids=("http",),
    default_variant=_default(
        ("--no-yolo", "--", "run", "--format", "jsonl"),
        capture_unavailable="Native OctoHub uses WebSocket transport; capture-only tap requires the named HTTP transport variant.",
    ),
    variants=(
        _variant(
            "http",
            "Native assistant role · HTTP transport",
            ("--no-yolo", "--", "run", "--format", "jsonl", "-c", "model.request_transport=http"),
            stdin_text="Reply with one short sentence.\n",
            dimensions={"transport": "HTTP", "role": "native assistant:chief", "model": "native octohub:auto"},
        ),
    ),
)


HEADLONG = AgentSpec(
    id="headlong",
    display_name="Headlong",
    package="laude-institute/headlong",
    source="github-source",
    github_source_install="scripts",
    tap_client="bash",
    fake_env={"ANTHROPIC_API_KEY": "phistory-fake-api-key"},
    extra_env={"SHELLM_THINKER_ENV": "local", "SHELLM_MAX_ITERATIONS": "1"},
    recorded_env=("SHELLM_THINKER_ENV", "SHELLM_MAX_ITERATIONS"),
    home_profile="headlong",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=300,
    default_variant=_default(
        ("--no-yolo", "--", "-c", '. "$HEADLONG_HOME/identities/phistory/activate"; thinkers step monolith'),
        label="Native monolith · synchronous manual wake",
        dimensions={
            "entry": "thinkers step monolith",
            "environment": "local",
            "max_iterations": "1",
            "action_protocol": "fenced Bash embedded in prompt",
        },
    ),
)


OPENAGENT = AgentSpec(
    id="openagent",
    display_name="OpenAgent",
    package="the-open-agent/openagent",
    source="github-release-asset",
    release_asset="openagent_linux_x86_64",
    release_asset_binary="openagent_linux_x86_64",
    tap_client="openagent",
    fake_env={},
    home_profile="openagent",
    tap_mode="forward",
    track_latest=False,
    capture_timeout_seconds=240,
    latest_variant_ids=("store-api",),
    default_variant=_default(
        ("--no-yolo", "--", "serve"),
        capture_unavailable="The native server has no configured model provider; use the named configured local Store API surface.",
    ),
    variants=(
        _variant(
            "store-api",
            "Local Store API · configured OpenAI",
            ("--no-yolo", "--", "serve"),
            driver="openagent-store",
            dimensions={"entry": "native built-in Store completion API", "provider": "OpenAI", "model": "gpt-4.1"},
        ),
    ),
)


AGENTS: dict[str, AgentSpec] = {
    agent.id: agent
    for agent in (
        CLAUDE_CODE,
        CODEX,
        DSH,
        ANTIGRAVITY,
        GROK,
        MINIMAX_CODE,
        KIMI_CODE,
        QWEN_CODE,
        QODER,
        MIMO,
        OPENCLAW,
        HERMES,
        KIMI,
        OPENCODE,
        PI,
        OMP,
        GEMINI_CLI,
        GOOSE,
        CRUSH,
        MINI_SWE_AGENT,
        NANOBOT,
        BUB,
        JCODE,
        PI_RUST,
        CODEWHALE,
        OPENDEV,
        KILOCODE,
        CLINE,
        LETTA_CODE,
        PRIME_AGENT,
        OPENINTERPRETER,
        FIBONA,
        CTXAGENT,
        REASONIX,
        QUORUM,
        TIANSHU,
        OCTOMIND,
        HEADLONG,
        OPENAGENT,
    )
}
AGENT_ORDER = tuple(AGENTS)
LATEST_AGENT_IDS = tuple(agent.id for agent in AGENTS.values() if agent.track_latest)


def agent_sort_key(agent_id: str) -> tuple[int, str]:
    try:
        return (AGENT_ORDER.index(agent_id), "")
    except ValueError:
        return (len(AGENT_ORDER), agent_id)


def get_agent(agent_id: str) -> AgentSpec:
    try:
        return AGENTS[agent_id]
    except KeyError as exc:
        known = ", ".join(sorted(AGENTS))
        raise ValueError(f"unknown agent {agent_id!r}; known agents: {known}") from exc


def parse_agent_ids(value: str | None) -> list[str]:
    if not value:
        return list(LATEST_AGENT_IDS)
    ids = [item.strip() for item in value.split(",") if item.strip()]
    for agent_id in ids:
        get_agent(agent_id)
    return ids
