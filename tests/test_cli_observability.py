from pathlib import Path
from types import SimpleNamespace

import pytest

from phistory import cli
from phistory.cli import _print_results
from phistory.models import AgentSpec, CaptureResult
from phistory.static_prompts.extract import StaticSourceUnavailable
from phistory.workflow import capture_latest


def test_print_results_writes_github_summary_and_annotations(tmp_path: Path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    code = _print_results(
        [
            CaptureResult("codex", "1.0.0", "default", "captured", tmp_path / "prompt.md", tmp_path / "trace.jsonl"),
            CaptureResult("claude-code", "unknown", "default", "failed", error="first line\nsecond line"),
        ],
        "Observed capture",
    )

    assert code == 1
    text = summary.read_text(encoding="utf-8")
    assert "## Observed capture" in text
    assert "Captured: **1** · Skipped: **0** · Failed: **1**" in text
    assert "`codex`" in text
    assert "`claude-code`" in text
    assert "first line second line" in text
    assert "::error title=claude-code unknown default capture failed::first line second line" in capsys.readouterr().err


def test_capture_latest_reports_version_lookup_failure(monkeypatch, tmp_path: Path):
    agent = AgentSpec(
        id="broken",
        display_name="Broken",
        package="broken",
        tap_client="broken",
        fake_env={},
    )
    monkeypatch.setattr("phistory.workflow.get_agent", lambda _agent_id: agent)
    monkeypatch.setattr(
        "phistory.workflow.packages.latest_version", lambda _agent: (_ for _ in ()).throw(RuntimeError("registry down"))
    )

    results = capture_latest(["broken"], root=tmp_path / "captures", cache_dir=tmp_path / "cache")

    assert len(results) == 1
    assert results[0].agent_id == "broken"
    assert results[0].version == "unknown"
    assert results[0].variant_id == "default"
    assert results[0].status == "failed"
    assert results[0].error == "registry down"


def test_extract_static_treats_unavailable_packaged_source_as_skip(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(cli.packages, "version_info", lambda agent, version: SimpleNamespace(version=version))
    monkeypatch.setattr(cli.packages, "install_agent", lambda agent, version, install_dir: None)
    monkeypatch.setattr(
        cli,
        "extract_static_prompts",
        lambda target, install_dir: (_ for _ in ()).throw(StaticSourceUnavailable("binary-only package")),
    )

    status = cli._extract_static(
        "claude-code",
        ["1.2.3"],
        root=tmp_path / "captures",
        cache_dir=tmp_path / "cache",
    )

    assert status == 0
    assert "skipped static extraction: binary-only package" in capsys.readouterr().out


def test_backfill_cli_forwards_resource_and_shard_options(monkeypatch, tmp_path: Path):
    observed = {}

    def fake_iter_backfill(agent_id, **kwargs):
        observed["agent_id"] = agent_id
        observed.update(kwargs)
        return iter(())

    monkeypatch.setattr(cli, "iter_backfill", fake_iter_backfill)

    status = cli.main(
        [
            "--root",
            str(tmp_path / "captures"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "backfill",
            "claude-code",
            "--from",
            "1.0.0",
            "--to",
            "2.0.0",
            "--skip-static",
            "--prune-installs",
            "--shard-index",
            "1",
            "--shard-count",
            "4",
        ]
    )

    assert status == 0
    assert observed["agent_id"] == "claude-code"
    assert observed["extract_static"] is False
    assert observed["prune_installs"] is True
    assert observed["shard_index"] == 1
    assert observed["shard_count"] == 4


@pytest.mark.parametrize(
    "args",
    [
        ["--shard-index", "0"],
        ["--shard-count", "2"],
        ["--shard-index", "0", "--shard-count", "0"],
        ["--shard-index", "2", "--shard-count", "2"],
    ],
)
def test_backfill_cli_rejects_invalid_shard_options(args):
    with pytest.raises(SystemExit) as exc:
        cli.main(["backfill", "claude-code", "--from", "1.0.0", *args])

    assert exc.value.code == 2
