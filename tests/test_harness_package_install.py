from pathlib import Path

from phistory import packages
from phistory.registry import get_agent


def test_pypi_extra_is_installed_without_changing_release_identity(tmp_path, monkeypatch):
    agent = get_agent("bub")
    looked_up = []
    commands = []

    def metadata(package):
        looked_up.append(package)
        return {"info": {"version": "0.6.1"}, "releases": {"0.6.1": [{"packagetype": "bdist_wheel"}]}}

    def run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["uv", "pip", "install"]:
            executable = Path(command[command.index("--python") + 1]).with_name("bub")
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("fake CLI")

    monkeypatch.setattr(packages, "_pypi_json", metadata)
    monkeypatch.setattr(packages, "run", run)
    assert packages.latest_version(agent).version == "0.6.1"
    assert looked_up == ["bub"]
    result = packages.install_agent(agent, "0.6.1", tmp_path / "install")
    assert (result / "bub").exists()
    assert commands[-1][-1] == "bub[app]==0.6.1"
