import json
from pathlib import Path

import pytest

from phistory.drivers import CaptureRunContext
from phistory.drivers.dsh_web import PROMPT, _create_and_prompt_session, _has_prompt_request
from phistory.models import AgentSpec, CaptureTarget, CaptureVariant, VersionInfo
from phistory.registry import AGENTS, get_agent
from phistory.site import _build_manifest
from phistory.storage import write_meta
from phistory.workflow import capture_latest


def test_capture_target_keeps_variants_isolated_under_a_version(tmp_path: Path):
    agent = AgentSpec(
        id="agent",
        display_name="Agent",
        package="agent",
        tap_client="agent",
        fake_env={},
        variants=(CaptureVariant("alternate", "Alternate", dimensions={"model": "alternate"}),),
    )

    default = CaptureTarget(agent, VersionInfo("1.0.0"), agent.default_variant, tmp_path)
    alternate = CaptureTarget(agent, VersionInfo("1.0.0"), agent.variant("alternate"), tmp_path)

    assert default.variant_dir == tmp_path / "agent/1.0.0/variants/default"
    assert alternate.variant_dir == tmp_path / "agent/1.0.0/variants/alternate"
    assert default.static_dir == alternate.static_dir == tmp_path / "agent/1.0.0/static"


def test_agent_rejects_duplicate_variant_ids():
    with pytest.raises(ValueError, match="unique"):
        AgentSpec(
            id="agent",
            display_name="Agent",
            package="agent",
            tap_client="agent",
            fake_env={},
            variants=(CaptureVariant("default", "Duplicate"),),
        )


@pytest.mark.parametrize("variant_id", ["Not/Safe", "..", ".hidden", "trailing."])
def test_agent_rejects_invalid_variant_ids(variant_id: str):
    with pytest.raises(ValueError, match="invalid"):
        AgentSpec(
            id="agent",
            display_name="Agent",
            package="agent",
            tap_client="agent",
            fake_env={},
            variants=(CaptureVariant(variant_id, "Unsafe"),),
        )


def test_capture_latest_runs_default_and_every_configured_variant(monkeypatch, tmp_path: Path):
    agent = AgentSpec(
        id="agent",
        display_name="Agent",
        package="agent",
        tap_client="agent",
        fake_env={},
        variants=(
            CaptureVariant("one", "One"),
            CaptureVariant("two", "Two"),
        ),
    )
    captured = []
    monkeypatch.setattr("phistory.workflow.get_agent", lambda _agent_id: agent)
    monkeypatch.setattr("phistory.workflow.packages.latest_version", lambda _agent: VersionInfo("1.0.0"))

    def fake_capture(target, **_kwargs):
        captured.append(target)
        return target.variant.id

    monkeypatch.setattr("phistory.workflow.capture_target", fake_capture)

    results = capture_latest(["agent"], root=tmp_path / "captures", cache_dir=tmp_path / "cache")

    assert results == ["default", "one", "two"]
    assert [target.variant.id for target in captured] == ["default", "one", "two"]
    assert {target.version.version for target in captured} == {"1.0.0"}


def test_dsh_web_default_does_not_override_mode(monkeypatch, tmp_path: Path):
    agent = get_agent("dsh")
    payloads = []
    monkeypatch.setattr(
        "phistory.drivers.dsh_web._rpc_when_ready",
        lambda _port, _method, payload, _process: payloads.append(payload) or {"sessionId": "session"},
    )
    monkeypatch.setattr("phistory.drivers.dsh_web._rpc", lambda *_args: {})

    for variant_id in ("default", "code"):
        target = CaptureTarget(agent, VersionInfo("1.0.0"), agent.variant(variant_id), tmp_path)
        context = CaptureRunContext(target, target.prompt_path, target.variant_dir / ".tap", tmp_path, {})
        _create_and_prompt_session(context, 1234, object())

    assert "agentPreset" not in payloads[0]
    assert payloads[1]["agentPreset"] == "code"


def test_dsh_web_accepts_a_prompt_request_without_tools(tmp_path: Path):
    trace_dir = tmp_path / "session"
    trace_dir.mkdir()
    (trace_dir / "trace_1.jsonl").write_text(
        json.dumps({"request": {"body": {"messages": [{"role": "user", "content": PROMPT}]}}}) + "\n",
        encoding="utf-8",
    )

    assert _has_prompt_request(tmp_path)


def test_site_manifest_builds_independent_variant_version_lanes(tmp_path: Path):
    agent = AgentSpec(
        id="agent",
        display_name="Agent",
        package="agent",
        tap_client="agent",
        fake_env={},
        variants=(CaptureVariant("model-b", "Model B", dimensions={"model": "model-b"}),),
    )
    captures = (
        ("1.0.0", "default", "old default"),
        ("1.1.0", "default", "new default"),
        ("1.0.0", "model-b", "only model b"),
    )
    for version, variant_id, prompt in captures:
        variant = agent.variant(variant_id)
        target = CaptureTarget(agent, VersionInfo(version), variant, tmp_path)
        target.variant_dir.mkdir(parents=True)
        target.prompt_path.write_text(prompt + "\n", encoding="utf-8")
        target.trace_path.write_text("{}\n", encoding="utf-8")
        write_meta(
            target,
            {
                "agent_id": agent.id,
                "agent": agent.display_name,
                "version": version,
                "variant": {
                    "id": variant.id,
                    "label": variant.label,
                    "dimensions": variant.dimensions,
                },
            },
        )

    manifest = _build_manifest(tmp_path)
    site_agent = manifest["agents"][0]
    lanes = {variant["id"]: variant for variant in site_agent["variants"]}

    assert site_agent["default_variant"] == "default"
    assert [variant["id"] for variant in site_agent["variants"]] == ["default", "model-b"]
    assert [item["version"] for item in lanes["default"]["versions"]] == ["1.1.0", "1.0.0"]
    assert lanes["default"]["versions"][0]["change"]["previous_version"] == "1.0.0"
    assert lanes["model-b"]["versions"][0]["change"]["previous_version"] is None
    assert lanes["model-b"]["versions"][0]["variant_dimensions"] == {"model": "model-b"}


def test_site_manifest_prefers_registered_lane_label_over_captured_version_label(monkeypatch, tmp_path: Path):
    agent = AgentSpec(
        id="renamed-agent",
        display_name="Renamed Agent",
        package="agent",
        tap_client="agent",
        fake_env={},
        default_variant=CaptureVariant("default", "Non-official API"),
    )
    monkeypatch.setitem(AGENTS, agent.id, agent)
    target = CaptureTarget(agent, VersionInfo("1.0.0"), agent.default_variant, tmp_path)
    target.variant_dir.mkdir(parents=True)
    target.prompt_path.write_text("prompt\n", encoding="utf-8")
    target.trace_path.write_text("{}\n", encoding="utf-8")
    write_meta(
        target,
        {
            "agent_id": agent.id,
            "agent": agent.display_name,
            "version": target.version.version,
            "variant": {"id": "default", "label": "Default", "dimensions": {}},
        },
    )
    retired_variant = CaptureVariant("retired", "Retired Snapshot")
    retired_target = CaptureTarget(agent, VersionInfo("1.0.0"), retired_variant, tmp_path)
    retired_target.variant_dir.mkdir(parents=True)
    retired_target.prompt_path.write_text("retired prompt\n", encoding="utf-8")
    retired_target.trace_path.write_text("{}\n", encoding="utf-8")
    write_meta(
        retired_target,
        {
            "agent_id": agent.id,
            "agent": agent.display_name,
            "version": retired_target.version.version,
            "variant": {"id": "retired", "label": "Captured Retired", "dimensions": {}},
        },
    )

    lanes = {lane["id"]: lane for lane in _build_manifest(tmp_path)["agents"][0]["variants"]}

    assert lanes["default"]["label"] == "Non-official API"
    assert lanes["default"]["versions"][0]["variant_label"] == "Default"
    assert lanes["retired"]["label"] == "Captured Retired"


def test_site_does_not_read_the_removed_flat_capture_layout(tmp_path: Path):
    version_dir = tmp_path / "agent/1.0.0"
    version_dir.mkdir(parents=True)
    (version_dir / "prompt.md").write_text("legacy\n", encoding="utf-8")
    (version_dir / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    (version_dir / "meta.json").write_text(json.dumps({"agent_id": "agent", "version": "1.0.0"}), encoding="utf-8")

    assert _build_manifest(tmp_path) == {"agents": [], "count": 0}


def test_capture_workflow_commits_successful_outputs_before_reporting_failures():
    workflow = Path(".github/workflows/capture.yml").read_text(encoding="utf-8")

    assert "group: capture-prompts-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert workflow.index("git add README.md") < workflow.index("git diff --cached --quiet")
    assert workflow.index("name: Commit capture updates") < workflow.index("name: Report capture failures")
