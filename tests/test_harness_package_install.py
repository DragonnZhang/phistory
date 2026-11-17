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


def test_raw_release_binary_is_installed_without_archive_extraction(tmp_path, monkeypatch):
    agent = get_agent("openagent")
    monkeypatch.setattr(
        packages,
        "_github_release_asset_info",
        lambda *_: {"name": "openagent_linux_x86_64", "url": "https://example.invalid/openagent"},
    )
    monkeypatch.setattr(packages, "_download", lambda url, output: output.write_bytes(b"fake ELF"))
    result = packages.install_agent(agent, "v2.89.2", tmp_path / "install")
    assert (result / "openagent").read_bytes() == b"fake ELF"
    assert (result / "openagent").stat().st_mode & 0o111


def test_release_channels_do_not_substitute_legacy_or_other_product_tags(monkeypatch):
    monkeypatch.setattr(
        packages,
        "_github_releases",
        lambda _: [
            {
                "tag_name": "v1.9.0",
                "published_at": "2026-11-18",
                "tarball_url": "https://example.invalid/legacy",
                "assets": [],
            },
            {
                "tag_name": "studio-v2.61.0",
                "published_at": "2026-11-17",
                "tarball_url": "https://example.invalid/studio",
                "assets": [
                    {"name": "reasonix-linux-amd64.tar.gz", "browser_download_url": "https://example.invalid/reasonix"}
                ],
            },
        ],
    )
    agent = get_agent("deepseek-reasonix")
    assert packages.latest_version(agent).version == "studio-v2.61.0"
    assert [version.version for version in packages.all_versions(agent)] == ["studio-v2.61.0"]


def test_python_api_install_does_not_treat_bare_venv_as_installed_package(tmp_path, monkeypatch):
    agent = get_agent("quorum-cli")
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    (install / "bin/python").touch()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["uv", "venv"]:
            (install / "bin").mkdir(parents=True)
            (install / "bin/python").touch()

    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "1.5.1", install)
    assert commands[-1][-1] == "quorum-cli==1.5.1"
    assert (install / ".phistory-python-package-installed").exists()
    assert packages.install_agent(agent, "1.5.1", install) == result
    assert len(commands) == 2


def test_source_script_install_keeps_sibling_templates_and_resolves_immutable_revision(tmp_path, monkeypatch):
    from phistory.models import CommandResult

    agent = get_agent("headlong")
    revision = "d" * 40
    monkeypatch.setattr(packages, "run", lambda argv, **_: CommandResult(tuple(argv), 0, revision + "\tHEAD\n", ""))
    assert packages.latest_version(agent).version == revision
    urls = []

    def download(url, output):
        urls.append(url)
        with tarfile.open(output, "w:gz") as archive:
            for name in ("bin/thinkers", "tools/identity", "skills/mem/SKILL.md"):
                payload = name.encode()
                member = tarfile.TarInfo(f"headlong-{revision}/{name}")
                member.mode = 0o755 if name.startswith(("bin/", "tools/")) else 0o644
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(packages, "_download", download)
    result = packages.install_agent(agent, revision, tmp_path / "install")
    assert (result / "thinkers").is_symlink()
    identity = (result / "identity").resolve()
    assert (identity.parent.parent / "skills/mem/SKILL.md").read_text() == "skills/mem/SKILL.md"
    assert (result / "bash").is_file()
    assert packages.install_agent(agent, revision, tmp_path / "install") == result
    assert urls == [f"https://github.com/laude-institute/headlong/archive/{revision}.tar.gz"]
    with pytest.raises(ValueError, match="immutable"):
        packages.install_agent(agent, "main", tmp_path / "bad")


def test_source_requirements_use_declared_python_and_retain_importable_source(tmp_path, monkeypatch):
    agent = get_agent("mini-agi")
    install = tmp_path / "install"
    commands = []

    def download(url, output):
        with tarfile.open(output, "w:gz") as archive:
            for name, content in {"requirements.txt": b"thinkgpt==0.0.6", "miniagi.py": b"native source"}.items():
                member = tarfile.TarInfo("source-root/" + name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))

    def run(argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ["uv", "venv"]:
            (install / "bin/python").touch()

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "e" * 40, install)
    assert commands[0][-2:] == ["--python", "3.11"]
    assert commands[1][-2:] == ["-r", str(install / "source/source-root/requirements.txt")]
    assert (packages.installed_source_root(result) / "miniagi.py").read_bytes() == b"native source"
    assert packages.install_agent(agent, "e" * 40, install) == result
    assert len(commands) == 2
