from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from claude_tap.prompt_snapshot import PromptSnapshot, render_prompt_markdown, snapshot_from_records

from phistory import packages
from phistory.drivers import CaptureRunContext, run_capture
from phistory.models import CaptureResult, CaptureTarget
from phistory.packages import agent_executable
from phistory.request_selection import matches_prompt_surface
from phistory.static_prompts.extract import extract_static_prompts
from phistory.storage import copy_trace, is_captured, latest_trace, prepare_version_dir, remove_if_exists, write_meta
from phistory.subprocesses import run

_VOLATILE_TEXT_PATTERNS = (
    (re.compile(r"\bcch=[^;\s]+"), "cch=<normalized>"),
    (re.compile(r"(?m)^ - OS Version: .+$"), " - OS Version: $PHISTORY_OS_VERSION"),
    (re.compile(r"(?m)^OS Version: .+$"), "OS Version: $PHISTORY_OS_VERSION"),
    (re.compile(r" - OS Version: [^\\\n]*(?=\\n)"), " - OS Version: $PHISTORY_OS_VERSION"),
    (
        re.compile(r"Today's date is (?:\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}/\d{1,2}/\d{4})\."),
        "Today's date is $PHISTORY_DATE.",
    ),
    (
        re.compile(r"Today's date: (?:\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}/\d{1,2}/\d{4})"),
        "Today's date: $PHISTORY_DATE",
    ),
    (re.compile(r"http://(?:127\.0\.0\.1|localhost):\d+"), "http://127.0.0.1:$PHISTORY_PORT"),
    (
        re.compile(r"The current date and time in ISO format is `[^`]+`\."),
        "The current date and time in ISO format is `$PHISTORY_DATETIME`.",
    ),
    (re.compile(r"The current local time is: [^\n]+"), "The current local time is: $PHISTORY_DATETIME."),
    (re.compile(r"(?m)^Conversation started: .+$"), "Conversation started: $PHISTORY_DATETIME"),
    (re.compile(r"Conversation ID: [0-9a-f-]{36}"), "Conversation ID: $PHISTORY_CONVERSATION"),
    (re.compile(r"(?m)^(  YOUR SESSION ID:) mvs_[A-Za-z0-9_]+$"), r"\1 $PHISTORY_SESSION"),
    (
        re.compile(r"(?m)^(  YOUR SCRATCHPAD: .*/scratchpads/)mvs_[A-Za-z0-9_]+(/scratchpad\.md)$"),
        r"\1$PHISTORY_SESSION\2",
    ),
    (re.compile(r"(?m)^(  daemonPort:) \d+$"), r"\1 $PHISTORY_PORT"),
    (
        re.compile(
            r"(?m)^(  date:) (?:\d{4}-\d{2}-\d{2} .+ \(UTC, UTC[+-]\d+(?::\d+)?\)|"
            r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) .+ GMT[+-]\d{4} \([^)]+\))$"
        ),
        r"\1 $PHISTORY_DATETIME",
    ),
    (re.compile(r"<current_date>\d{4}-\d{2}-\d{2}</current_date>"), "<current_date>$PHISTORY_DATE</current_date>"),
    (re.compile(r"<timezone>[^<]+</timezone>"), "<timezone>$PHISTORY_TIMEZONE</timezone>"),
    (
        re.compile(r"\$PHISTORY_HOME/\.gemini/antigravity-cli/brain/[0-9a-f-]{36}"),
        "$PHISTORY_HOME/.gemini/antigravity-cli/brain/$PHISTORY_CONVERSATION",
    ),
    (
        re.compile(r"\$PHISTORY_HOME/\.claude/projects/-tmp-phistory-work-[^/\s]+"),
        "$PHISTORY_HOME/.claude/projects/$PHISTORY_PROJECT",
    ),
    (
        re.compile(r"\$PHISTORY_HOME/\.qwen/projects/[A-Za-z0-9-]+"),
        "$PHISTORY_HOME/.qwen/projects/$PHISTORY_PROJECT",
    ),
    (
        re.compile(r"\$PHISTORY_HOME/\.local/share/mimocode/memory/sessions/ses_[A-Za-z0-9_]+"),
        "$PHISTORY_HOME/.local/share/mimocode/memory/sessions/$PHISTORY_SESSION",
    ),
    (re.compile(r"Bearer phistory-[A-Za-z0-9_-]+"), "Bearer <redacted>"),
)
_SENSITIVE_TRACE_HEADERS = frozenset(
    {
        "api-key",
        "authorization",
        "cosy-key",
        "cosy-machinehostname",
        "cosy-machineid",
        "cosy-machinetoken",
        "cosy-user",
        "proxy-authorization",
        "x-api-key",
        "x-goog-api-key",
        "x-model-key",
    }
)


def capture_target(
    target: CaptureTarget,
    *,
    cache_dir: Path,
    force: bool = False,
    keep_tap: bool = False,
    extract_static: bool = True,
) -> CaptureResult:
    if is_captured(target) and not force:
        return CaptureResult(
            target.agent.id,
            target.version.version,
            target.variant.id,
            "skipped",
            target.prompt_path,
            target.trace_path,
            target.meta_path,
        )
    if target.variant.capture_unavailable:
        return CaptureResult(
            target.agent.id,
            target.version.version,
            target.variant.id,
            "failed",
            error=target.variant.capture_unavailable,
        )

    staging_root: Path | None = None
    working_target = target
    if force and target.variant_dir.exists():
        target.root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(mkdtemp(prefix=".phistory-capture-", dir=target.root))
        working_target = CaptureTarget(target.agent, target.version, target.variant, staging_root)

    started = time.time()
    prepare_version_dir(working_target)
    install_dir = (cache_dir / "installs" / target.agent.id / target.version.version).resolve()
    variant_dir = working_target.variant_dir.resolve()
    prompt_path = working_target.prompt_path.resolve()
    tap_output_dir = (variant_dir / ".tap").resolve()
    inherited_env: dict[str, str] = {}

    try:
        inherited_env = _resolve_inherited_env(target.agent.id, target.agent.inherited_env)
        with _without_parent_env(target.agent.inherited_env.values()):
            bin_dir = packages.install_agent(target.agent, target.version.version, install_dir)
            binary_version = _binary_version(target, bin_dir, inherited_env=inherited_env)
        with (
            TemporaryDirectory(prefix="phistory-home-", ignore_cleanup_errors=True) as home_dir,
            TemporaryDirectory(prefix="phistory-work-", ignore_cleanup_errors=True) as work_dir,
        ):
            env = _capture_env(working_target, bin_dir, Path(home_dir), inherited_env=inherited_env)
            env["PWD"] = str(Path(work_dir))
            if target.agent.bootstrap_args:
                run(
                    [str(bin_dir / agent_executable(target.agent)), *target.agent.bootstrap_args],
                    cwd=Path(work_dir),
                    env={**env, **target.agent.bootstrap_env},
                    timeout=600,
                    inherit_env=False,
                )
            execution = run_capture(
                CaptureRunContext(
                    target=working_target,
                    prompt_path=prompt_path,
                    tap_output_dir=tap_output_dir,
                    work_dir=Path(work_dir),
                    env=env,
                )
            )
            argv = list(execution.command)
            result = execution.result
            compatibility_patches = (
                *packages.compatibility_patches(target.agent, target.version.version),
                *execution.compatibility_patches,
            )
        if not prompt_path.exists():
            detail = "\n".join(
                f"{name}:\n{output.strip()[-4000:]}"
                for name, output in (("stdout", result.stdout), ("stderr", result.stderr))
                if output.strip()
            )
            raise RuntimeError(f"capture command failed ({result.returncode})\n{detail}")

        client_log = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        (working_target.variant_dir / "client.log").write_text(
            _redact_secrets(client_log, tuple(inherited_env.values())), encoding="utf-8"
        )

        if not working_target.trace_path.exists():
            trace = latest_trace(tap_output_dir)
            if inherited_env:
                _sanitize_trace(trace, inherited_env.values())
            copy_trace(trace, working_target)
        if inherited_env:
            _sanitize_trace(working_target.trace_path, inherited_env.values())
        selected_snapshot = None
        if (
            target.agent.capture_min_tools
            or target.agent.required_system_text
            or target.agent.required_user_text
            or target.agent.required_tool_name_fragments
        ):
            try:
                selected_snapshot = _primary_snapshot(
                    working_target.trace_path,
                    minimum_tools=target.agent.capture_min_tools,
                    required_system_text=target.agent.required_system_text,
                    required_user_text=target.agent.required_user_text,
                    required_tool_name_fragments=target.agent.required_tool_name_fragments,
                )
            except ValueError as exc:
                raise RuntimeError(
                    f"no request matched the required primary prompt/tool surface\n{client_log[-6000:]}"
                ) from exc
            prompt_path.write_text(render_prompt_markdown(selected_snapshot), encoding="utf-8")
        replacements = {
            str(install_dir): "$PHISTORY_INSTALL",
            str(home_dir): "$PHISTORY_HOME",
            str(work_dir): "$PHISTORY_WORKSPACE",
            **{secret: "<redacted>" for secret in inherited_env.values()},
        }
        _sanitize_file(prompt_path, replacements)
        write_meta(
            working_target,
            {
                "agent_id": target.agent.id,
                "agent": target.agent.display_name,
                "package": target.agent.package,
                **({"python_runtime": target.agent.python_runtime} if target.agent.python_runtime else {}),
                **({"package_extras": list(target.agent.pypi_extras)} if target.agent.pypi_extras else {}),
                **(
                    {"package_runtime_requirements": list(target.agent.pypi_extra_requirements)}
                    if target.agent.pypi_extra_requirements
                    else {}
                ),
                **(
                    {"package_layout": target.agent.release_asset_layout}
                    if target.agent.release_asset_layout != "binary"
                    else {}
                ),
                **(
                    {
                        "source_runtime": {
                            "project": target.agent.source_project,
                            "state": target.agent.source_state,
                            "extra_requirements": list(target.agent.source_extra_requirements),
                            "dependency_groups": list(target.agent.source_dependency_groups),
                            "requirement_files": list(target.agent.source_requirement_files)
                            if target.agent.github_source_install == "requirements"
                            else [],
                            "python_dependencies": list(target.agent.source_python_dependencies),
                            "workspace_package": target.agent.source_sync_package,
                            "build_commands": [list(command) for command in target.agent.source_build_commands],
                        }
                    }
                    if target.agent.source == "github-source"
                    else {}
                ),
                **(
                    {
                        "bootstrap": {
                            "arguments": list(target.agent.bootstrap_args),
                            "environment": target.agent.bootstrap_env,
                        }
                    }
                    if target.agent.bootstrap_args
                    else {}
                ),
                "version": target.version.version,
                "variant": {
                    "id": target.variant.id,
                    "label": target.variant.label,
                    "dimensions": target.variant.dimensions,
                },
                "requested": target.variant.dimensions,
                "observed": _trace_observation(working_target.trace_path, snapshot=selected_snapshot),
                **(
                    {"host_runtime": json.loads(env["PHISTORY_NATIVE_HOST"])} if env.get("PHISTORY_NATIVE_HOST") else {}
                ),
                **(
                    {
                        "capture_selection": {
                            "minimum_tools": target.agent.capture_min_tools,
                            "required_system_text": list(target.agent.required_system_text),
                            "required_user_text": list(target.agent.required_user_text),
                            "required_tool_name_fragments": list(target.agent.required_tool_name_fragments),
                        }
                    }
                    if selected_snapshot is not None
                    else {}
                ),
                "published_at": target.version.published_at,
                "tarball_url": target.version.tarball_url,
                "binary_version": _replace_many(binary_version, replacements) if binary_version else None,
                "captured_at": _iso_now(),
                "capture_host": _capture_host(),
                "tap_client": target.agent.tap_client,
                "target": "claude-tap capture-only",
                "client_exit_code": result.returncode,
                "client_log": "client.log",
                "duration_seconds": round(time.time() - started, 3),
                "command": [_replace_many(part, replacements) for part in _portable_command(argv, variant_dir)],
                **({"stdin": target.variant.stdin_text} if target.variant.stdin_text is not None else {}),
                **(
                    {"capture_environment": {key: env[key] for key in target.agent.recorded_env}}
                    if target.agent.recorded_env
                    else {}
                ),
                **({"compatibility_patches": list(patches)} if (patches := compatibility_patches) else {}),
            },
        )
        if not keep_tap:
            remove_if_exists(tap_output_dir)
        if staging_root is not None:
            _promote_staged_capture(working_target.variant_dir, target.variant_dir, staging_root)
        if extract_static and target.variant.id == "default":
            _extract_static_best_effort(target, install_dir)
        return CaptureResult(
            target.agent.id,
            target.version.version,
            target.variant.id,
            "captured",
            target.prompt_path,
            target.trace_path,
            target.meta_path,
        )
    except Exception as exc:
        if not keep_tap:
            remove_if_exists(working_target.variant_dir)
            if staging_root is not None:
                remove_if_exists(staging_root)
            else:
                _remove_empty_capture_parents(working_target)
        elif staging_root is not None:
            exc = RuntimeError(f"{exc}\nfailed capture kept under {staging_root}")
        error = _redact_secrets(str(exc), tuple(inherited_env.values()))
        return CaptureResult(target.agent.id, target.version.version, target.variant.id, "failed", error=error)


def _capture_host() -> dict[str, str]:
    # Read the parent environment: _capture_env sets CI flags even for local probes.
    host = {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "runner": "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
    }
    if host["runner"] == "github-actions":
        for name in ("GITHUB_WORKFLOW", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB"):
            if value := os.environ.get(name):
                host[name.removeprefix("GITHUB_").lower()] = value
        if (repository := os.environ.get("GITHUB_REPOSITORY")) and (run_id := host.get("run_id")):
            server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
            host["run_url"] = f"{server}/{repository}/actions/runs/{run_id}"
    return host


def _remove_empty_capture_parents(target: CaptureTarget) -> None:
    for path in (target.variant_dir.parent, target.version_dir, target.version_dir.parent):
        try:
            path.rmdir()
        except OSError:
            break


def _promote_staged_capture(staged: Path, destination: Path, staging_root: Path) -> None:
    backup = staging_root / "previous"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.rename(backup)
    try:
        staged.rename(destination)
    except Exception:
        if backup.exists():
            backup.rename(destination)
        raise
    remove_if_exists(backup)
    remove_if_exists(staging_root)


def _capture_env(
    target: CaptureTarget,
    bin_dir: Path,
    home_dir: Path | None = None,
    *,
    inherited_env: Mapping[str, str] | None = None,
    prepare_runtime: bool = True,
) -> dict[str, str]:
    home = home_dir or target.version_dir / ".home"
    profile = target.variant.home_profile or target.agent.home_profile
    for path in (home, home / ".config", home / ".cache", home / ".local" / "share", home / ".codex", home / ".claude"):
        path.mkdir(parents=True, exist_ok=True)
    if target.agent.fake_chatgpt_auth:
        _write_fake_chatgpt_auth(home)
    if profile == "antigravity":
        _write_antigravity_config(home)
    if profile == "hermes":
        _write_hermes_config(home)
    if profile == "kimi":
        _write_kimi_config(home)
    if profile == "kimi-code":
        _write_kimi_code_config(home)
    if profile == "mimo":
        _write_mimo_config(home)
    if profile == "omp":
        _write_omp_config(home)
    if profile == "openclaw":
        _write_openclaw_config(home)
    if profile == "opencode":
        _write_opencode_config(home)
    if profile == "pi":
        _write_pi_config(home)
    if profile == "qwen":
        _write_qwen_config(home)
    if profile == "goose":
        _write_goose_config(home)
    if profile == "crush":
        _write_crush_config(home)
    if profile == "nanobot":
        _write_nanobot_config(home)
    if profile == "codewhale":
        config_dir = home / ".codewhale"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text("[update]\ncheck_for_updates = false\n", encoding="utf-8")
    if profile == "opendev":
        config_dir = home / ".opendev"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "settings.json").write_text("{}\n", encoding="utf-8")
    if profile == "reasonix":
        config_dir = home / ".reasonix"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("DEEPSEEK_API_KEY=phistory-fake-api-key\n", encoding="utf-8")
        (config_dir / ".env").chmod(0o600)
    if profile == "cline-anthropic":
        _write_cline_anthropic_config(home)
    inherited = (
        dict(inherited_env)
        if inherited_env is not None
        else _resolve_inherited_env(target.agent.id, target.agent.inherited_env)
    )
    env = {
        **target.agent.fake_env,
        **target.agent.extra_env,
        **target.variant.extra_env,
        **inherited,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "PHISTORY_HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "TZ": "Etc/UTC",
    }
    if target.agent.id == "qoder":
        # Older releases interpret this as a paid workflow mode instead of ordinary headless CLI execution.
        env.pop("GITHUB_ACTIONS")
    if target.agent.native_host and prepare_runtime:
        from phistory.runtime_setup import prepare_npm_host

        host_bin, provenance = prepare_npm_host(bin_dir.parent / "native-host", *target.agent.native_host, env)
        env["PATH"] = f"{bin_dir}{os.pathsep}{host_bin}{os.pathsep}{os.environ.get('PATH', '')}"
        env["PHISTORY_NATIVE_HOST"] = json.dumps(provenance)
    if profile == "hermes":
        env["HERMES_HOME"] = str(home / ".hermes")
    if profile == "dsh":
        env["DSH_HOME"] = str(home / ".dsh")
    if profile == "grok":
        grok_home = home / ".grok"
        grok_home.mkdir(parents=True, exist_ok=True)
        env["GROK_HOME"] = str(grok_home)
    if profile == "kimi":
        env["KIMI_SHARE_DIR"] = str(home / ".kimi")
    if profile == "kimi-code":
        env["KIMI_CODE_HOME"] = str(home / ".kimi-code")
    if profile == "mimo":
        env.update(
            {
                "MIMOCODE_CONFIG": str(home / ".config" / "mimocode" / "mimocode.json"),
                "MIMOCODE_MIMO_ONLY": "false",
            }
        )
    if profile == "openclaw":
        env.update(
            {
                "OPENCLAW_STATE_DIR": str(home / ".openclaw"),
                "OPENCLAW_CONFIG_PATH": str(home / ".openclaw" / "openclaw.json"),
            }
        )
    if profile == "opencode":
        env["OPENCODE_CONFIG"] = str(home / ".config" / "opencode" / "opencode.json")
    if profile == "pi":
        env["PI_CODING_AGENT_DIR"] = str(home / ".pi" / "agent")
    if profile == "omp":
        env["PI_CODING_AGENT_DIR"] = str(home / ".omp" / "agent")
    if profile == "goose":
        env["GOOSE_PATH_ROOT"] = str(home / ".goose")
        env["GOOSE_CA_CERT_PATH"] = str(home / ".local" / "share" / "claude-tap" / "ca.pem")
    if profile == "crush":
        env["CRUSH_GLOBAL_CONFIG"] = str(home / ".config" / "crush")
        env["CRUSH_DATA_DIR"] = str(home / ".local" / "share" / "crush")
    if profile == "bub":
        (home / ".bub").mkdir(parents=True, exist_ok=True)
        env["BUB_HOME"] = str(home / ".bub")
    if profile == "jcode":
        (home / ".jcode").mkdir(parents=True, exist_ok=True)
        env["JCODE_HOME"] = str(home / ".jcode")
    if profile == "pi-rust":
        (home / ".rpi" / "agent").mkdir(parents=True, exist_ok=True)
        env["RPI_CODING_AGENT_DIR"] = str(home / ".rpi" / "agent")
    if profile == "codewhale":
        env["CODEWHALE_HOME"] = str(home / ".codewhale")
    if profile == "opendev":
        env["OPENDEV_DIR"] = str(home / ".opendev")
    if profile == "kilocode":
        env["KILO_CONFIG_CONTENT"] = json.dumps(
            {
                "model": "phistory/gpt-4.1",
                "enabled_providers": ["phistory"],
                "provider": {
                    "phistory": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Phistory",
                        "options": {"baseURL": "https://api.openai.com/v1", "apiKey": "phistory-fake-api-key"},
                        "models": {"gpt-4.1": {"name": "GPT-4.1"}},
                    }
                },
            }
        )
    if profile in ("cline", "cline-anthropic"):
        state = home / ".cline"
        state.mkdir(parents=True, exist_ok=True)
        env["CLINE_DATA_DIR"] = str(state)
        env["CLINE_PROVIDER_SETTINGS_PATH"] = str(state / "settings" / "providers.json")
    if profile == "letta-code":
        state = home / ".letta-local"
        state.mkdir(parents=True, exist_ok=True)
        env["LETTA_LOCAL_BACKEND_DIR"] = str(state)
    if profile == "prime-agent":
        state = home / ".prime-agent"
        state.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "PRIME_AGENT_CODING_AGENT_DIR": str(state),
                "PRIME_AGENT_SESSION_DIR": str(state / "sessions"),
                "PRIME_AGENT_DAEMON_SOCKET": str(state / "daemon.sock"),
                "PRIME_AGENT_KERNEL_VENV": str(state / "kernel-venv"),
                "PI_PACKAGE_DIR": str(bin_dir.resolve()),
            }
        )
    if profile == "interpreter":
        state = home / ".openinterpreter"
        state.mkdir(parents=True, exist_ok=True)
        env["INTERPRETER_HOME"] = str(state)
    if profile == "fibona":
        env["FIBONA_LOG_DIR"] = str(home / ".fibona" / "logs")
    if profile == "ctxagent":
        env["CTXAGENT_DATA"] = str(home / ".ctxagent")
    if profile == "reasonix":
        env["REASONIX_HOME"] = str(home / ".reasonix")
    if profile == "tianshu":
        env["RIVET_HOME"] = str(home / ".tianshu")
    if profile == "octomind" and prepare_runtime:
        from phistory.runtime_setup import prepare_octobrain, prepare_octomind_tap

        state = home / ".octomind"
        env["PATH"] = f"{home / '.local/bin'}{os.pathsep}{env['PATH']}"
        env["OCTOMIND_DATA_DIR"] = str(state)
        env["PHISTORY_OCTOMIND_TAP_COMMIT"] = prepare_octomind_tap(state, env["PHISTORY_OCTOMIND_TAP_REPOSITORY"])
        env["PHISTORY_OCTOMIND_DEPENDENCIES"] = json.dumps({"octobrain": prepare_octobrain(state, env)}, sort_keys=True)
    if profile == "headlong" and prepare_runtime:
        state = home / ".headlong"
        state.mkdir(parents=True, exist_ok=True)
        env["HEADLONG_HOME"] = str(state)
        run(
            [str(bin_dir / "identity"), "--dir", str(state / "identities"), "new", "phistory"],
            env=env,
            inherit_env=False,
            timeout=120,
        )
    if profile == "openagent":
        env["driverName"] = "sqlite3"
        env["dataSourceName"] = str(home / "openagent.db")
    if profile == "cheetah-anthropic":
        state = home / ".cheetahclaws"
        state.mkdir(parents=True, exist_ok=True)
        (state / "config.json").write_text(json.dumps({"model": "claude-sonnet-4-6"}) + "\n", encoding="utf-8")
    if profile == "cheetah-ollama":
        (home / ".cheetahclaws").mkdir(parents=True, exist_ok=True)
        env["OLLAMA_BASE_URL"] = "http://ollama.phistory.invalid"
    if target.agent.source_state != "none" or profile in ("trashclaw", "mini-agi", "openmanus", "researchclaw"):
        source = packages.installed_source_root(bin_dir)
        if (target.agent.source_state == "isolated" or profile in ("openmanus", "researchclaw")) and prepare_runtime:
            isolated = home / "source"
            shutil.copytree(source, isolated, symlinks=True)
            source = isolated
        env["PHISTORY_SOURCE_ROOT"] = str(source)
        env["PYTHONPATH"] = str(source)
        if (source / "src").is_dir():
            env["PYTHONPATH"] += os.pathsep + str(source / "src")
        if profile == "openmanus" and prepare_runtime:
            config = source / "config" / "config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                '[llm]\nmodel = "gpt-4o"\nbase_url = "https://api.openai.com/v1"\napi_key = "phistory-fake-api-key"\nmax_tokens = 8192\ntemperature = 0.0\n\n[daytona]\ndaytona_api_key = "phistory-fake-daytona-key"\n',
                encoding="utf-8",
            )
    if profile == "cuga":
        env["CUGA_DBS_DIR"] = str(home / ".cuga" / "db")
        env["CUGA_LOGGING_DIR"] = str(home / ".cuga" / "logs")
        if prepare_runtime:
            from phistory.runtime_setup import prepare_cuga_example

            path, digest = prepare_cuga_example(home, env["PHISTORY_CUGA_EXAMPLE_SOURCE"])
            env["PHISTORY_CUGA_EXAMPLE"] = str(path)
            env["PHISTORY_CUGA_EXAMPLE_SHA256"] = digest
    if profile == "deerflow":
        env["DEER_FLOW_HOME"] = str(home / "deerflow")
    if profile == "hive":
        state = home / "hive"
        state.mkdir(parents=True, exist_ok=True)
        env["HIVE_HOME"] = str(state)
        path = state / "configuration.json"
        path.write_text(json.dumps({"llm": {"api_key": "phistory-fake-api-key"}}) + "\n", encoding="utf-8")
        path.chmod(0o600)
    if profile == "atomic-cloud":
        state = home / "atomic-agent"
        state.mkdir(parents=True, exist_ok=True)
        (home / "work").mkdir(parents=True, exist_ok=True)
        env["ATOMIC_AGENT_STATE_DIR"] = str(state)
        config = {
            "llm": {
                "activeTextProvider": "phistory",
                "activeEmbeddingProvider": "phistory",
                "providers": [
                    {
                        "id": "phistory",
                        "kind": "openai-compatible",
                        "baseUrl": "https://api.openai.com/v1",
                        "apiKeyEnvVar": "OPENAI_API_KEY",
                        "defaultChatModel": "gpt-4.1",
                        "defaultEmbeddingModel": "text-embedding-3-small",
                    }
                ],
            }
        }
        path = state / "config.json"
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        path.chmod(0o600)
    if profile == "mirix":
        env["MIRIX_DIR"] = str(home / "mirix")
        env["TIKTOKEN_CACHE_DIR"] = str(home / "mirix" / "tiktoken")
    if profile == "intentkit":
        env["DB_NAME"] = "phistory_" + re.sub(r"[^a-zA-Z0-9]", "_", home.name)[-48:]
    if profile == "ouroboros":
        env["OUROBOROS_DATA_DIR"] = str(home / "ouroboros")
        env["OUROBOROS_APP_ROOT"] = env["PHISTORY_SOURCE_ROOT"]
    if profile == "raven":
        state = home / "raven"
        state.mkdir(parents=True, exist_ok=True)
        env["RAVEN_HOME"] = str(state)
        config = {
            "agents": {"defaults": {"provider": "anthropic"}},
            "providers": {"anthropic": {"apiKey": "phistory-fake-api-key"}},
        }
        path = state / "config.json"
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        path.chmod(0o600)
    if profile == "zcode":
        state = home / "zcode"
        state.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "ZCODE_DATA_BASE_DIR": str(state),
                "ZCODE_STORAGE_DIR": str(state / "storage"),
                "ZCODE_SESSION_DB_PATH": str(state / "sessions.db"),
                "ZCODE_LOG_DIR": str(state / "logs"),
                "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(state / "providers.json"),
            }
        )
        config = {
            "schemaVersion": 1,
            "config": {
                "providerConfigRules": {
                    "providerRules": [
                        {
                            "providerId": "phistory",
                            "providerName": "Phistory API",
                            "config": {
                                "group": "standard-personal",
                                "access": {"type": "api-key", "apiKey": "phistory-fake-api-key"},
                                "api": {"type": "openai-chat-completions", "baseUrl": "https://api.openai.com/v1"},
                                "personalModelIds": ["gpt-4.1"],
                            },
                        }
                    ]
                },
                "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
                "defaultModelSelection": {"providerId": "phistory", "modelId": "gpt-4.1"},
            },
        }
        path = Path(env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"])
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        path.chmod(0o600)
    if profile == "nanomuse":
        env["NANOMUSE_DATA_DIR"] = str(home / "nanomuse")
        env["NANOMUSE_WORKSPACE"] = str(home / "work")
    if profile == "localgpt":
        env["LANCEDB_PATH"] = str(home / "lancedb")
    if profile == "bambooai":
        env["BAMBOO_DATA_DIR"] = str(home / "bambooai" / "data")
        env["BAMBOO_MEMORY_DIR"] = str(home / "bambooai" / "memory")
    if profile == "cowagent":
        state = home / "cowagent"
        state.mkdir(parents=True, exist_ok=True)
        env["COW_DATA_DIR"] = str(state)
        if prepare_runtime:
            config = json.loads((Path(env["PHISTORY_SOURCE_ROOT"]) / "config-template.json").read_text())
            config["deepseek_api_key"] = "phistory-fake-api-key"
            (state / "config.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
    if profile == "pr-agent":
        (home / "input.diff").write_text(
            "diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n",
            encoding="utf-8",
        )
    if target.agent.node_runtime:
        env["NPM_CONFIG_CACHE"] = str(bin_dir.parents[2] / ".npm-runtime-cache")
    if target.agent.fake_chatgpt_auth:
        env.update({"OPENAI_API_KEY": "", "CODEX_API_KEY": "", "CODEX_ACCESS_TOKEN": ""})
    return env


def _extract_static_best_effort(target: CaptureTarget, install_dir: Path) -> None:
    if target.agent.id != "claude-code":
        return
    target.static_dir.mkdir(parents=True, exist_ok=True)
    try:
        extract_static_prompts(target, install_dir)
    except Exception:
        return


def _trace_records(trace_path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        request = record.get("request")
        if not isinstance(request, dict):
            continue
        body = request.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                continue
        if not isinstance(body, dict):
            continue
        records.append({**record, "request": {**request, "body": body}})
    return records


def _primary_snapshot(
    trace_path: Path,
    *,
    minimum_tools: int = 0,
    required_system_text: tuple[str, ...] = (),
    required_user_text: tuple[str, ...] = (),
    required_tool_name_fragments: tuple[str, ...] = (),
) -> PromptSnapshot:
    candidates = []
    for record in _trace_records(trace_path):
        try:
            snapshot = snapshot_from_records([record])
        except ValueError:
            continue
        if matches_prompt_surface(
            snapshot,
            minimum_tools=minimum_tools,
            required_system_text=required_system_text,
            required_user_text=required_user_text,
            required_tool_name_fragments=required_tool_name_fragments,
        ):
            candidates.append(record)
    return snapshot_from_records(candidates)


def _trace_observation(trace_path: Path, *, snapshot: PromptSnapshot | None = None) -> dict[str, object]:
    try:
        snapshot = snapshot or snapshot_from_records(_trace_records(trace_path))
    except ValueError:
        return {}
    observed: dict[str, object] = {}
    if snapshot.model:
        observed["model"] = snapshot.model
    if snapshot.tools:
        observed["tool_count"] = len(snapshot.tools)
    return observed


def _write_fake_chatgpt_auth(home: Path) -> None:
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    auth = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": _fake_chatgpt_jwt(),
            "access_token": "phistory-fake-access-token",
            "refresh_token": "phistory-fake-refresh-token",
            "account_id": "phistory-account",
        },
        "last_refresh": "2026-01-01T00:00:00Z",
    }
    (codex_home / "auth.json").write_text(json.dumps(auth, separators=(",", ":")), encoding="utf-8")


def _write_openclaw_config(home: Path) -> None:
    state_dir = home / ".openclaw"
    state_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "agents": {
            "defaults": {
                "workspace": str(state_dir / "workspace"),
                "model": {"primary": "phistory/phistory-dummy"},
            }
        },
        "models": {
            "providers": {
                "phistory": {
                    "api": "openai-responses",
                    "baseUrl": "http://127.0.0.1:9/v1",
                    "apiKey": "phistory-fake-api-key",
                    "models": [{"id": "phistory-dummy", "name": "Phistory Dummy"}],
                }
            }
        },
    }
    (state_dir / "openclaw.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def _write_antigravity_config(home: Path) -> None:
    agy_home = home / ".gemini" / "antigravity-cli"
    agy_home.mkdir(parents=True, exist_ok=True)
    token = {
        "auth_method": "consumer",
        "token": {
            "access_token": "phistory-fake-access-token",
            "token_type": "Bearer",
            "refresh_token": "phistory-fake-refresh-token",
            "expiry": "2099-01-01T00:00:00Z",
            "is_gcp_tos": False,
        },
    }
    (agy_home / "antigravity-oauth-token").write_text(json.dumps(token, separators=(",", ":")), encoding="utf-8")


def _write_hermes_config(home: Path) -> None:
    hermes_home = home / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(
        "\n".join(
            [
                "model:",
                "  provider: openrouter",
                "  default: phistory-dummy",
                "agent:",
                "  max_turns: 1",
                "display:",
                "  streaming: false",
                "  persistent_output: false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_goose_config(home: Path) -> None:
    config_dir = home / ".goose" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "extensions": {
            "developer": {
                "enabled": True,
                "type": "platform",
                "name": "developer",
                "display_name": "Developer",
                "bundled": True,
            }
        }
    }
    (config_dir / "config.yaml").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _write_crush_config(home: Path) -> None:
    config_dir = home / ".config" / "crush"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "models": {
            "large": {"model": "gpt-4.1", "provider": "openai"},
            "small": {"model": "gpt-4.1", "provider": "openai"},
        },
        "providers": {"openai": {"api_key": "phistory-fake-api-key"}},
        "options": {"disable_provider_auto_update": True, "disable_metrics": True},
    }
    (config_dir / "crush.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _write_nanobot_config(home: Path) -> None:
    config_dir = home / ".nanobot"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {"providers": {"anthropic": {"apiKey": "phistory-fake-api-key"}}}
    (config_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _write_cline_anthropic_config(home: Path) -> None:
    config_dir = home / ".cline" / "settings"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "version": 1,
        "lastUsedProvider": "anthropic",
        "providers": {
            "anthropic": {
                "settings": {"provider": "anthropic", "apiKey": "phistory-fake-api-key", "model": "claude-sonnet-5-5"},
                "updatedAt": _iso_now(),
            }
        },
    }
    (config_dir / "providers.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _write_kimi_config(home: Path) -> None:
    kimi_home = home / ".kimi"
    kimi_home.mkdir(parents=True, exist_ok=True)
    (kimi_home / "config.toml").write_text(
        "\n".join(
            [
                'default_model = "phistory-dummy"',
                "default_yolo = true",
                "skip_afk_prompt_injection = true",
                "",
                "[providers.phistory]",
                'type = "openai_responses"',
                'base_url = "https://api.openai.com/v1"',
                'api_key = "phistory-fake-api-key"',
                "",
                "[models.phistory-dummy]",
                'provider = "phistory"',
                'model = "gpt-4.1"',
                "max_context_size = 200000",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_kimi_code_config(home: Path) -> None:
    kimi_home = home / ".kimi-code"
    kimi_home.mkdir(parents=True, exist_ok=True)
    (kimi_home / ".skip-migration-from-kimi-cli").write_text("", encoding="utf-8")
    (kimi_home / "config.toml").write_text(
        "\n".join(
            [
                'default_model = "kimi-code/kimi-for-coding"',
                "",
                '[providers."phistory"]',
                'type = "kimi"',
                'base_url = "https://api.kimi.com/coding/v1"',
                'api_key = "phistory-fake-api-key"',
                "",
                '[models."kimi-code/kimi-for-coding"]',
                'provider = "phistory"',
                'model = "kimi-for-coding"',
                "max_context_size = 262144",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_mimo_config(home: Path) -> None:
    config_dir = home / ".config" / "mimocode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "$schema": "https://mimo.xiaomi.com/mimocode/config.json",
        "model": "openai/gpt-4.1",
        "provider": {
            "openai": {
                "options": {
                    "apiKey": "phistory-fake-api-key",
                }
            }
        },
    }
    (config_dir / "mimocode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def _write_qwen_config(home: Path) -> None:
    qwen_home = home / ".qwen"
    qwen_home.mkdir(parents=True, exist_ok=True)
    config = {
        "selectedAuthType": "openai",
        "security": {"auth": {"selectedType": "openai"}},
    }
    (qwen_home / "settings.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def _write_opencode_config(home: Path) -> None:
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": "openai/gpt-4.1",
        "provider": {
            "openai": {
                "options": {
                    "apiKey": "phistory-fake-api-key",
                }
            }
        },
    }
    (config_dir / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def _write_pi_config(home: Path) -> None:
    pi_home = home / ".pi" / "agent"
    pi_home.mkdir(parents=True, exist_ok=True)
    models = {
        "providers": {
            "phistory": {
                "api": "openai-responses",
                "baseUrl": "https://api.openai.com/v1",
                "apiKey": "phistory-fake-api-key",
                "models": [{"id": "gpt-4.1", "name": "gpt-4.1"}],
            }
        }
    }
    (pi_home / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")
    (pi_home / "settings.json").write_text(
        json.dumps({"defaultProvider": "phistory"}, indent=2),
        encoding="utf-8",
    )


def _write_omp_config(home: Path) -> None:
    omp_home = home / ".omp" / "agent"
    omp_home.mkdir(parents=True, exist_ok=True)
    models = {
        "providers": {
            "phistory": {
                "api": "openai-responses",
                "baseUrl": "https://api.openai.com/v1",
                "apiKey": "phistory-fake-api-key",
                "models": [
                    {
                        "id": "gpt-4.1",
                        "name": "gpt-4.1",
                        "contextWindow": 200000,
                        "maxTokens": 32768,
                    }
                ],
            }
        }
    }
    (omp_home / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")
    (omp_home / "settings.json").write_text(
        json.dumps({"defaultProvider": "phistory", "defaultModelId": "gpt-4.1"}, indent=2),
        encoding="utf-8",
    )


def _fake_chatgpt_jwt() -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "exp": 4102444800,
        "email": "phistory@example.invalid",
        "https://api.openai.com/auth": {
            "chatgpt_plan_type": "plus",
            "chatgpt_user_id": "phistory-user",
            "chatgpt_account_id": "phistory-account",
            "chatgpt_account_is_fedramp": False,
        },
    }
    return f"{_b64url_json(header)}.{_b64url_json(payload)}.phistory-signature"


def _b64url_json(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _binary_version(
    target: CaptureTarget,
    bin_dir: Path,
    *,
    inherited_env: Mapping[str, str] | None = None,
) -> str | None:
    executable = bin_dir / agent_executable(target.agent)
    if not executable.exists():
        return None
    with TemporaryDirectory(prefix="phistory-version-home-") as home_dir:
        try:
            env = _capture_env(target, bin_dir, Path(home_dir), inherited_env=inherited_env, prepare_runtime=False)
            result = run(
                [str(executable), "--version"],
                cwd=Path(home_dir),
                env=env,
                timeout=30,
                check=False,
                inherit_env=False,
            )
        except Exception:
            return target.version.version
    text = (result.stdout or result.stderr).strip()
    if "\n" in text or target.version.version not in text:
        return target.version.version
    return text


def _portable_command(argv: list[str], version_dir: Path) -> list[str]:
    out: list[str] = []
    for index, arg in enumerate(argv):
        if index == 0:
            out.append("python")
            continue
        path = Path(arg)
        if path.is_absolute():
            try:
                out.append(path.relative_to(version_dir).as_posix())
                continue
            except ValueError:
                pass
        out.append(arg)
    return out


def _sanitize_file(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(_sanitize_text(text, replacements), encoding="utf-8")


def _sanitize_trace(path: Path, secret_values: Iterable[str]) -> None:
    secrets = tuple(dict.fromkeys(value for value in secret_values if value))
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            lines.append(_redact_secrets(line, secrets))
            continue
        _redact_trace_headers(record)
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        lines.append(_redact_secrets(serialized, secrets))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _redact_trace_headers(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _redact_trace_headers(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key.lower() == "headers" and isinstance(item, dict):
            for header in item:
                if header.lower() in _SENSITIVE_TRACE_HEADERS:
                    item[header] = "***"
        _redact_trace_headers(item)


def _redact_secrets(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, "<redacted>")
    return text


def _resolve_inherited_env(agent_id: str, mapping: Mapping[str, str]) -> dict[str, str]:
    missing = sorted({source for source in mapping.values() if not os.environ.get(source)})
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"{agent_id}: missing required environment variable(s): {names}")
    return {child: os.environ[source] for child, source in mapping.items()}


@contextmanager
def _without_parent_env(names: Iterable[str]) -> Iterator[None]:
    unique_names = tuple(dict.fromkeys(names))
    saved = {name: os.environ[name] for name in unique_names if name in os.environ}
    for name in unique_names:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name in unique_names:
            os.environ.pop(name, None)
        os.environ.update(saved)


def _sanitize_text(text: str, replacements: dict[str, str]) -> str:
    text = _replace_many(text, replacements)
    for pattern, replacement in _VOLATILE_TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}(```json)", r"\n\n\1", text)
    return text


def _replace_many(text: str, replacements: dict[str, str]) -> str:
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return text


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
