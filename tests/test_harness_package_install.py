import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("suffix,mode", [("tar.gz", "w:gz"), ("tar.xz", "w:xz")])
def test_release_bundle_preserves_sidecars_launcher_name_and_cache(tmp_path, monkeypatch, suffix, mode):
    agent = replace(get_agent("jcode"), release_asset=f"jcode-linux-x86_64.{suffix}")
    downloads = []

    def download(url, output):
        downloads.append(url)
        with tarfile.open(output, mode) as archive:
            for name, payload in {
                "jcode-linux-x86_64": b"launcher resolves its own name",
                "jcode-linux-x86_64.bin": b"native sidecar",
                "resources/prompt.txt": b"bundled prompt",
            }.items():
                member = tarfile.TarInfo(f"package/{name}")
                member.size = len(payload)
                member.mode = 0o755 if "/" not in name else 0o644
                archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(
        packages,
        "_github_release_asset_info",
        lambda *_: {"name": agent.release_asset, "url": "https://example.invalid/bundle"},
    )
    bin_dir = packages.install_agent(agent, "v1", tmp_path / "install")
    assert bin_dir.is_symlink()
    assert (bin_dir / "jcode").is_symlink()
    assert (bin_dir / "jcode").resolve().name == "jcode-linux-x86_64"
    assert (bin_dir / "jcode-linux-x86_64.bin").read_text() == "native sidecar"
    assert (bin_dir / "resources/prompt.txt").read_text() == "bundled prompt"
    assert packages.install_agent(agent, "v1", tmp_path / "install") == bin_dir
    assert len(downloads) == 1


def test_release_asset_can_use_numeric_version_without_changing_tag():
    agent = get_agent("prime-agent")
    asset = {"name": "prime-agent-0.10.3-linux-x64.tar.gz", "browser_download_url": "https://example.invalid/prime"}
    assert packages._select_release_asset(agent, "v0.10.3", {"assets": [asset]}) == asset
