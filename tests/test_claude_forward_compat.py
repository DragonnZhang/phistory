import json
import os
import sys
from pathlib import Path

import pytest

from phistory.drivers import CaptureRunContext
from phistory.drivers.oneshot import LEGACY_CLAUDE_FORWARD_PATCH, _prepare_claude_forward_compat
from phistory.models import CaptureTarget, VersionInfo
from phistory.registry import get_agent
from phistory.subprocesses import run


def _context(tmp_path: Path, help_text: str) -> tuple[CaptureRunContext, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "claude"
    executable.write_text(
        f"""#!{sys.executable}
import json
import sys

if "--help" in sys.argv:
    print({help_text!r})
else:
    print(json.dumps(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    agent = get_agent("claude-code")
    target = CaptureTarget(agent, VersionInfo("1.0.0"), agent.variant("official"), tmp_path / "captures")
    context = CaptureRunContext(
        target,
        target.prompt_path,
        target.variant_dir / ".tap",
        work_dir,
        {"PATH": str(bin_dir)},
    )
    return context, context.env


def test_claude_forward_keeps_supported_settings_argument_path(tmp_path: Path):
    context, env = _context(tmp_path, "--settings <file-or-json>")

    prepared, patches = _prepare_claude_forward_compat(context, env)

    assert prepared is env
    assert patches == ()


def test_claude_forward_wraps_file_only_settings_option(tmp_path: Path):
    context, env = _context(tmp_path, "--settings <file> Path to a settings JSON file to load")

    prepared, patches = _prepare_claude_forward_compat(context, env)

    assert prepared is not env
    assert patches == (LEGACY_CLAUDE_FORWARD_PATCH,)


@pytest.mark.parametrize("settings_arg", [("--settings", "{}"), ("--settings={}",)])
def test_claude_forward_wrapper_strips_unsupported_settings_argument(tmp_path: Path, settings_arg: tuple[str, ...]):
    context, env = _context(tmp_path, "legacy help without the option")

    prepared, patches = _prepare_claude_forward_compat(context, env)
    wrapper = Path(prepared["PATH"].split(os.pathsep)[0]) / "claude"
    result = run(
        [str(wrapper), *settings_arg, "--model", "claude-sonnet-5"],
        cwd=context.work_dir,
        env=prepared,
        check=False,
        inherit_env=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == ["--model", "claude-sonnet-5"]
    assert patches == (LEGACY_CLAUDE_FORWARD_PATCH,)
