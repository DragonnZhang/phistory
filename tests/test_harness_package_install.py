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


def test_pypi_compatibility_requirements_share_resolution_and_invalidate_stale_installs(tmp_path, monkeypatch):
    agent = get_agent("self-operating-computer")
    install = tmp_path / "install"
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["uv", "venv"]:
            (install / "bin").mkdir(parents=True)
            (install / "bin/python").touch()

    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "1.5.8", install)
    assert commands[-1][-3:] == ["self-operating-computer==1.5.8", "httpx==0.25.2", "setuptools==80.9.0"]
    assert packages.install_agent(agent, "1.5.8", install) == result
    assert len(commands) == 2
    changed = replace(agent, pypi_extra_requirements=("httpx==0.27.1", "setuptools==80.9.0"))
    packages.install_agent(changed, "1.5.8", install)
    assert len(commands) == 4 and commands[-1][-2] == "httpx==0.27.1"


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


def test_legacy_root_project_takes_precedence_over_nested_modern_packages(tmp_path):
    root = tmp_path / "source"
    nested = root / "experimental"
    nested.mkdir(parents=True)
    (root / "setup.py").write_text("from setuptools import setup; setup(name='native-root')")
    (nested / "pyproject.toml").write_text("[project]\nname='experimental'\nversion='1'")
    assert packages._find_source_root(tmp_path) == root


def test_source_workspace_uses_upstream_lock_and_selected_application_package(tmp_path, monkeypatch):
    agent = get_agent("db-gpt")
    install = tmp_path / "install"
    commands = []
    files = {
        "pyproject.toml": b"[project]\nname='empty-workspace'\nversion='1'",
        "uv.lock": b"original lock",
        "packages/dbgpt-core/pyproject.toml": b"native core",
        "packages/dbgpt-ext/pyproject.toml": b"native extension",
    }

    def download(url, output):
        with tarfile.open(output, "w:gz") as archive:
            for name, payload in files.items():
                member = tarfile.TarInfo("source-root/" + name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

    def run(argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ["uv", "venv"]:
            (install / "bin").mkdir()
            (install / "bin/python").touch()

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "f" * 40, install)
    source = packages.installed_source_root(result)
    assert commands[1][:2] == ["uv", "sync"]
    assert "--frozen" in commands[1] and "--no-dev" in commands[1]
    assert commands[1][commands[1].index("--package") + 1] == "dbgpt-app"
    assert commands[1][commands[1].index("--project") + 1] == str(source)
    assert all((source / name).read_bytes() == payload for name, payload in files.items())


def test_source_script_install_keeps_sibling_templates_and_resolves_immutable_revision(tmp_path, monkeypatch):
    from phistory.models import CommandResult

    agent = get_agent("headlong")
    revision = "d" * 40
    refs = "a" * 40 + "\trefs/remotes/origin/HEAD\n" + revision + "\tHEAD\n" + "e" * 40 + "\trefs/heads/HEAD\n"
    monkeypatch.setattr(packages, "run", lambda argv, **_: CommandResult(tuple(argv), 0, refs, ""))
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
    monkeypatch.setattr(
        packages, "run", lambda argv, **_: CommandResult(tuple(argv), 0, "a" * 40 + "\trefs/remotes/origin/HEAD\n", "")
    )
    with pytest.raises(RuntimeError, match="cannot resolve"):
        packages.latest_version(agent)


@pytest.mark.parametrize("portable", [False, True])
def test_source_requirements_use_declared_python_and_retain_importable_source(tmp_path, monkeypatch, portable):
    agent = get_agent("mini-agi")
    if portable:
        agent = replace(
            agent, source_requirement_files=(), source_python_dependencies=("openai>=2.45.0", "requests>=2.34.2")
        )
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
    expected = (
        ["openai>=2.45.0", "requests>=2.34.2"]
        if portable
        else ["-r", str(install / "source/source-root/requirements.txt")]
    )
    assert commands[1][-2:] == expected
    assert (packages.installed_source_root(result) / "miniagi.py").read_bytes() == b"native source"
    assert (packages.installed_source_root(result) / "requirements.txt").read_bytes() == b"thinkgpt==0.0.6"
    assert packages.install_agent(agent, "e" * 40, install) == result
    assert len(commands) == 2


@pytest.mark.parametrize("nested", [False, True])
def test_editable_source_keeps_repository_and_installs_selected_workspace_and_extra_requirements(
    tmp_path, monkeypatch, nested
):
    agent = replace(
        get_agent("gpt-researcher"),
        source_project="backend" if nested else ".",
        source_extra_requirements=("deep_agents/requirements.txt",),
    )
    install = tmp_path / "install"
    commands = []
    project = "repo/backend" if nested else "repo"

    def download(url, output):
        assert "/archive/" + "f" * 40 in url
        with tarfile.open(output, "w:gz") as archive:
            for name, content in {
                f"{project}/pyproject.toml": b"[project]\nname='test'\nversion='1'\n",
                f"{project}/deep_agents/requirements.txt": b"native-extra",
                "repo/prompts/system.md": b"native resource",
            }.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["uv", "venv"]:
            (install / "bin").mkdir()
            (install / "bin/python").touch()

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "f" * 40, install)
    editable = next(command for command in commands if "--editable" in command)
    assert Path(editable[-1]) == install / "source" / project
    assert commands[-1][-1] == str(install / "source" / project / "deep_agents/requirements.txt")
    assert (packages.installed_source_root(result) / "prompts/system.md").read_text() == "native resource"
    assert packages.install_agent(agent, "f" * 40, install) == result
    assert len(commands) == 3


def test_official_release_wheel_keeps_constraints_plugins_and_package_resources(tmp_path, monkeypatch):
    agent = get_agent("raven")
    install = tmp_path / "install"
    commands = []
    downloads = []
    asset = {"name": "raven-0.2.7-py3-none-any.whl", "url": "https://example.invalid/host.whl"}
    monkeypatch.setattr(packages, "_github_release_asset_info", lambda *_: asset)
    monkeypatch.setattr(
        packages,
        "_github_release_by_tag",
        lambda *_: {
            "assets": [
                {"name": name, "browser_download_url": "https://example.invalid/" + name}
                for name in ("raven-plugins.txt", "raven-constraints.txt")
            ]
        },
    )

    def download(url, path):
        downloads.append(url)
        path.write_text("native package asset")

    def run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["uv", "pip", "install"]:
            (install / "bin/raven").touch()
            resource = install / "lib/raven/agents/system.md"
            resource.parent.mkdir(parents=True)
            resource.write_text("wheel-owned native agents")

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "v0.2.7", install)
    assert len(downloads) == 3
    assert commands[0][-2:] == ["--python", "3.12"]
    command = commands[-1]
    assert command[command.index("-r") + 1] == str(install / "raven-plugins.txt")
    assert command[command.index("-c") + 1] == str(install / "raven-constraints.txt")
    assert command[-1] == str(install / asset["name"])
    assert (install / "lib/raven/agents/system.md").read_text() == "wheel-owned native agents"
    assert packages.install_agent(agent, "v0.2.7", install) == result
    assert len(commands) == 2


def test_source_build_runs_in_retained_repository_before_caching(tmp_path, monkeypatch):
    agent = get_agent("zcode")
    install = tmp_path / "install"
    commands = []

    def download(url, output):
        with tarfile.open(output, "w:gz") as archive:
            payload = b"native source and resources"
            member = tarfile.TarInfo("repo/package.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    def run(command, **kwargs):
        commands.append((tuple(command), kwargs["cwd"]))

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(packages, "run", run)
    result = packages.install_agent(agent, "a" * 40, install)
    assert commands == [(command, install / "source/repo") for command in agent.source_build_commands]
    assert (result / "python").is_file()
    assert (packages.installed_source_root(result) / "package.json").read_text() == "native source and resources"
    assert packages.install_agent(agent, "a" * 40, install) == result
    assert len(commands) == 2


def test_locked_release_honors_declared_uv_version_and_production_dependency_group(tmp_path, monkeypatch):
    agent = replace(get_agent("ouroboros"), source_dependency_groups=("app",))
    install = tmp_path / "install"
    commands = []
    manifest = (
        b"[project]\nname='native'\nversion='1'\n[tool.uv]\nrequired-version='==0.12.1'\n[dependency-groups]\napp=[]\n"
    )

    def download(url, output):
        with tarfile.open(output, "w:gz") as archive:
            for name, content in {"pyproject.toml": manifest, "uv.lock": b"native frozen resolution"}.items():
                member = tarfile.TarInfo("repo/" + name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))

    def run(command, **kwargs):
        commands.append((command, kwargs))
        if command[:2] == ["uv", "venv"]:
            (install / "bin").mkdir()
            (install / "bin/python").touch()

    monkeypatch.setattr(packages, "_download", download)
    monkeypatch.setattr(packages, "run", run)
    packages.install_agent(agent, "v1", install)
    command, kwargs = commands[-1]
    assert command[:5] == ["uvx", "--from", "uv==0.12.1", "uv", "sync"]
    assert "--frozen" in command and "--no-default-groups" in command
    assert command[command.index("--group") + 1] == "app"
    assert kwargs["env"] == {"UV_PROJECT_ENVIRONMENT": str(install.resolve())}
    assert (install / "source/repo/uv.lock").read_bytes() == b"native frozen resolution"
