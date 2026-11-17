from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tomllib
import urllib.request
import zipfile
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError

from phistory.models import AgentSpec, VersionInfo
from phistory.subprocesses import run

_PLATFORM_VERSION_RE = re.compile(r"-(darwin|linux|win32)-(x64|arm64)$")
_PYPI_PRERELEASE_RE = re.compile(r"(?:a|b|rc|dev)\d*$", re.IGNORECASE)
_QODER_1_0_15_CYCLE_SIGNATURE = (
    "var Po,yWA,NWA,vYe,HYe=p(async()=>{await he(),Ye(),await yV(),NA(),await PV(),await TWA(),await xWA(),await NYe()"
)
_QODER_1_0_15_PATCHED_SIGNATURE = (
    "var Po,yWA,NWA,vYe,HYe=p(async()=>{await he(),Ye(),await yV(),NA(),await PV(),await TWA(),await NYe()"
)
_QODER_1_0_15_PATCH_NOTE = (
    "After installing the exact official @qoder-ai/qodercli@1.0.15 package, removed the redundant HYe-to-xWA "
    "initializer await that deadlocks with xWA-to-HYe; all other published bundle code is unchanged."
)
INSTALL_TIMEOUT_SECONDS = 1800


def latest_version(agent: AgentSpec) -> VersionInfo:
    if agent.source == "minimax-code":
        from phistory.minimax_code import latest_version as minimax_code_latest_version

        return minimax_code_latest_version()
    if agent.source == "npm":
        return _npm_latest(agent)
    if agent.source == "pypi":
        return _pypi_latest(agent)
    if agent.source == "github-release":
        return _github_release_latest(agent)
    if agent.source == "github-release-asset":
        return _github_release_asset_latest(agent)
    if agent.source == "github-source":
        return _github_source_latest(agent)
    raise ValueError(f"unsupported package source: {agent.source}")


def all_versions(agent: AgentSpec, *, include_prerelease: bool = False) -> list[VersionInfo]:
    if agent.source == "minimax-code":
        from phistory.minimax_code import all_versions as minimax_code_versions

        return minimax_code_versions()
    if agent.source == "npm":
        return _npm_versions(agent, include_prerelease=include_prerelease)
    if agent.source == "pypi":
        return _pypi_versions(agent, include_prerelease=include_prerelease)
    if agent.source == "github-release":
        return _github_release_versions(agent, include_prerelease=include_prerelease)
    if agent.source == "github-release-asset":
        return _github_release_asset_versions(agent, include_prerelease=include_prerelease)
    if agent.source == "github-source":
        return [_github_source_latest(agent)]
    raise ValueError(f"unsupported package source: {agent.source}")


def versions_between(agent: AgentSpec, start: str, end: str, *, include_prerelease: bool = False) -> list[VersionInfo]:
    versions = all_versions(agent, include_prerelease=include_prerelease)
    if end == "latest":
        end = versions[-1].version
    start_idx = _index_of(versions, start)
    end_idx = _index_of(versions, end)
    if start_idx > end_idx:
        raise ValueError(f"--from {start} is newer than --to {end}")
    return versions[start_idx : end_idx + 1]


def version_info(agent: AgentSpec, version: str) -> VersionInfo:
    for item in all_versions(agent, include_prerelease=True):
        if item.version == version:
            return item
    return VersionInfo(version=version)


def install_agent(agent: AgentSpec, version: str, install_dir: Path) -> Path:
    if agent.source == "minimax-code":
        from phistory.minimax_code import install as install_minimax_code

        return install_minimax_code(version, install_dir)
    if agent.binary_release_repo:
        return _install_binary_release(agent, version, install_dir)
    if agent.source == "npm":
        return _install_npm(agent, version, install_dir)
    if agent.source == "pypi":
        return _install_pypi(agent, version, install_dir)
    if agent.source == "github-release":
        return _install_github_release(agent, version, install_dir)
    if agent.source == "github-release-asset":
        return _install_github_release_asset(agent, version, install_dir)
    if agent.source == "github-source":
        return _install_github_source(agent, version, install_dir)
    raise ValueError(f"unsupported package source: {agent.source}")


def agent_executable(agent: AgentSpec) -> str:
    return agent.executable or agent.tap_client


def compatibility_patches(agent: AgentSpec, version: str) -> tuple[str, ...]:
    if agent.id == "qoder" and agent.package == "@qoder-ai/qodercli" and version == "1.0.15":
        return (_QODER_1_0_15_PATCH_NOTE,)
    return ()


def npm_view(package: str, *fields: str) -> object:
    args = ["npm", "view", package, *fields, "--json"]
    result = run(args, timeout=120)
    text = result.stdout.strip()
    data = json.loads(text) if text else None
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return data


def _npm_latest(agent: AgentSpec) -> VersionInfo:
    data = npm_view(agent.package, "version", "time", "dist.tarball")
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected npm view shape for {agent.package}: {data!r}")
    version = str(data["version"])
    times = data.get("time") if isinstance(data.get("time"), dict) else {}
    return VersionInfo(
        version=version,
        published_at=str(times.get(version)) if times.get(version) else None,
        tarball_url=str(data.get("dist.tarball")) if data.get("dist.tarball") else None,
    )


def _npm_versions(agent: AgentSpec, *, include_prerelease: bool) -> list[VersionInfo]:
    data = npm_view(agent.package, "versions", "time")
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
        raise RuntimeError(f"unexpected npm versions shape for {agent.package}: {data!r}")
    times = data.get("time") if isinstance(data.get("time"), dict) else {}
    versions = [
        VersionInfo(version=str(version), published_at=str(times.get(version)) if times.get(version) else None)
        for version in data["versions"]
    ]
    return [item for item in versions if _is_archivable_version(item.version, include_prerelease=include_prerelease)]


def _install_npm(agent: AgentSpec, version: str, install_dir: Path) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    package_ref = f"{agent.package}@{version}"
    run([*agent.install_command, "--prefix", str(install_dir), package_ref], timeout=INSTALL_TIMEOUT_SECONDS)
    _apply_npm_compatibility_patches(agent, version, install_dir)
    bin_dir = install_dir / "node_modules" / ".bin"
    if not bin_dir.exists():
        raise RuntimeError(f"npm install did not create bin dir: {bin_dir}")
    if agent.node_runtime:
        _wrap_runtime_bin(bin_dir / agent_executable(agent), agent.node_runtime, "NODE")
    return bin_dir


def _apply_npm_compatibility_patches(agent: AgentSpec, version: str, install_dir: Path) -> None:
    if not compatibility_patches(agent, version):
        return
    bundle = install_dir / "node_modules" / "@qoder-ai" / "qodercli" / "bundle" / "qodercli.js"
    if not bundle.is_file():
        raise RuntimeError(f"Qoder CLI 1.0.15 compatibility patch target is missing: {bundle}")
    source = bundle.read_text(encoding="utf-8")
    if _QODER_1_0_15_PATCHED_SIGNATURE in source and _QODER_1_0_15_CYCLE_SIGNATURE not in source:
        return
    if source.count(_QODER_1_0_15_CYCLE_SIGNATURE) != 1:
        raise RuntimeError("Qoder CLI 1.0.15 compatibility patch signature mismatch")
    bundle.write_text(
        source.replace(_QODER_1_0_15_CYCLE_SIGNATURE, _QODER_1_0_15_PATCHED_SIGNATURE),
        encoding="utf-8",
    )


def _install_binary_release(agent: AgentSpec, version: str, install_dir: Path) -> Path:
    if not agent.binary_release_asset:
        raise RuntimeError(f"{agent.id} defines binary_release_repo without binary_release_asset")
    bin_dir = install_dir / "bin"
    executable = bin_dir / agent_executable(agent)
    if executable.exists():
        return bin_dir
    if install_dir.exists():
        shutil.rmtree(install_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    tag = agent.binary_release_tag.format(version=version)
    release = _github_release_by_tag(agent.binary_release_repo, tag)
    expected = agent.binary_release_asset.format(version=version)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError(f"GitHub release without assets for {agent.binary_release_repo}@{tag}: {release!r}")
    asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == expected), None)
    if not isinstance(asset, dict):
        available = ", ".join(str(item.get("name")) for item in assets if isinstance(item, dict))
        raise RuntimeError(
            f"asset {expected!r} not found for {agent.binary_release_repo}@{tag}; available: {available}"
        )
    url = asset.get("browser_download_url")
    if not url:
        raise RuntimeError(f"GitHub release asset missing download URL: {asset!r}")
    _download(str(url), executable)
    executable.chmod(executable.stat().st_mode | 0o755)
    return bin_dir


def _pypi_latest(agent: AgentSpec) -> VersionInfo:
    data = _pypi_json(agent.package)
    info = data.get("info")
    if not isinstance(info, dict) or not info.get("version"):
        raise RuntimeError(f"unexpected PyPI package shape for {agent.package}: {data!r}")
    version = str(info["version"])
    return _pypi_version_from_release(version, data.get("releases", {}).get(version))


def _pypi_versions(agent: AgentSpec, *, include_prerelease: bool) -> list[VersionInfo]:
    data = _pypi_json(agent.package)
    releases = data.get("releases")
    if not isinstance(releases, dict):
        raise RuntimeError(f"unexpected PyPI releases shape for {agent.package}: {data!r}")
    versions = [
        _pypi_version_from_release(str(version), files)
        for version, files in releases.items()
        if _is_archivable_version(str(version), include_prerelease=include_prerelease)
    ]
    return sorted(versions, key=lambda item: (item.published_at or "", item.version))


def _install_pypi(agent: AgentSpec, version: str, install_dir: Path) -> Path:
    bin_dir = install_dir / "bin"
    executable = bin_dir / agent_executable(agent)
    complete = install_dir / ".phistory-python-package-installed"
    if executable.exists() and (executable.name != "python" or complete.exists()):
        return bin_dir
    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    run(
        ["uv", "venv", str(install_dir), *(["--python", agent.python_runtime] if agent.python_runtime else [])],
        timeout=120,
    )
    extras = f"[{','.join(agent.pypi_extras)}]" if agent.pypi_extras else ""
    run(
        ["uv", "pip", "install", "--python", str(bin_dir / "python"), f"{agent.package}{extras}=={version}"],
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if not executable.exists():
        raise RuntimeError(f"PyPI install did not create executable: {executable}")
    complete.touch()
    return bin_dir


def _github_release_latest(agent: AgentSpec) -> VersionInfo:
    releases = _agent_releases(agent)
    for release in releases:
        if not release.get("draft") and not release.get("prerelease"):
            return _github_release_version(release)
    raise RuntimeError(f"no stable GitHub releases found for {agent.package}")


def _github_release_versions(agent: AgentSpec, *, include_prerelease: bool) -> list[VersionInfo]:
    releases = [
        _github_release_version(release)
        for release in _agent_releases(agent)
        if not release.get("draft") and (include_prerelease or not release.get("prerelease"))
    ]
    return sorted(releases, key=lambda item: (item.published_at or "", item.version))


def _github_release_asset_latest(agent: AgentSpec) -> VersionInfo:
    releases = _agent_releases(agent)
    for release in releases:
        if not release.get("draft") and not release.get("prerelease"):
            return _github_release_asset_version(agent, release)
    raise RuntimeError(f"no stable GitHub releases found for {agent.package}")


def _github_release_asset_versions(agent: AgentSpec, *, include_prerelease: bool) -> list[VersionInfo]:
    releases = [
        _github_release_asset_version(agent, release)
        for release in _agent_releases(agent)
        if not release.get("draft") and (include_prerelease or not release.get("prerelease"))
    ]
    return sorted(releases, key=lambda item: (item.published_at or "", item.version))


def _agent_releases(agent: AgentSpec) -> list[dict]:
    releases = _github_releases(agent.package)
    if agent.release_tag_prefix:
        return [
            release for release in releases if str(release.get("tag_name", "")).startswith(agent.release_tag_prefix)
        ]
    return releases


def _github_source_latest(agent: AgentSpec) -> VersionInfo:
    result = run(["git", "ls-remote", f"https://github.com/{agent.package}.git", "HEAD"], timeout=120)
    revision = result.stdout.split()[0] if result.stdout.strip() else ""
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise RuntimeError(f"cannot resolve GitHub source HEAD for {agent.package}")
    return VersionInfo(revision, tarball_url=f"https://github.com/{agent.package}/archive/{revision}.tar.gz")


def _install_github_source(agent: AgentSpec, revision: str, install_dir: Path) -> Path:
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("GitHub source installations require a full immutable commit SHA")
    url = f"https://github.com/{agent.package}/archive/{revision}.tar.gz"
    if agent.github_source_install == "editable":
        return _install_github_release(agent, revision, install_dir, archive_url=url, editable=True)
    bin_dir = install_dir / "bin"
    complete = install_dir / ".phistory-source-installed"
    if complete.exists() and (bin_dir / agent_executable(agent)).exists():
        return bin_dir
    if install_dir.exists():
        shutil.rmtree(install_dir)
    bin_dir.mkdir(parents=True)
    archive = install_dir / "source.tar.gz"
    _download(url, archive)
    source = install_dir / "source"
    source.mkdir()
    _extract_archive(archive, source)
    roots = list(source.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise RuntimeError("source archive must contain exactly one repository root")
    for command in agent.source_build_commands:
        run(list(command), cwd=roots[0], timeout=INSTALL_TIMEOUT_SECONDS)
    if agent.github_source_install == "requirements":
        run(
            [
                "uv",
                "venv",
                str(install_dir),
                "--allow-existing",
                *(["--python", agent.python_runtime] if agent.python_runtime else []),
            ],
            timeout=120,
        )
        run(
            ["uv", "pip", "install", "--python", str(bin_dir / "python"), "-r", str(roots[0] / "requirements.txt")],
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
        if not (bin_dir / agent_executable(agent)).exists():
            raise RuntimeError("source requirements installation did not provide the runtime executable")
        complete.touch()
        return bin_dir
    for directory in (roots[0] / "bin", roots[0] / "tools"):
        if directory.is_dir():
            for command in directory.iterdir():
                if command.is_file() and os.access(command, os.X_OK):
                    link = bin_dir / command.name
                    if not link.exists():
                        link.symlink_to(command.resolve())
    executable = bin_dir / agent_executable(agent)
    if not executable.exists():
        system = shutil.which(agent_executable(agent))
        if system is None:
            raise RuntimeError(f"source runtime executable is unavailable: {agent_executable(agent)}")
        executable.symlink_to(system)
    complete.touch()
    return bin_dir


def installed_source_root(bin_dir: Path) -> Path:
    source = bin_dir.parent / "source"
    roots = [path for path in source.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"expected one installed source root in {source}")
    return roots[0]


def _install_github_release(
    agent: AgentSpec, version: str, install_dir: Path, *, archive_url: str | None = None, editable: bool = False
) -> Path:
    bin_dir = install_dir / "bin"
    executable = bin_dir / agent_executable(agent)
    complete = install_dir / ".phistory-python-package-installed"
    if executable.exists() and (executable.name != "python" or complete.exists()):
        return bin_dir
    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    package_ref = archive_url or f"https://github.com/{agent.package}/archive/refs/tags/{version}.tar.gz"
    if editable or agent.github_release_install == "editable":
        archive_path = install_dir / "source.tar.gz"
        source_dir = install_dir / "source"
        _download(package_ref, archive_path)
        source_dir.mkdir()
        _extract_archive(archive_path, source_dir)
        if agent.source_project != ".":
            roots = list(source_dir.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise RuntimeError("source archive must contain exactly one repository root")
            source_root = roots[0] / agent.source_project
        else:
            source_root = _find_source_root(source_dir)
        venv_args = ["uv", "venv", str(install_dir), "--allow-existing"]
        python_version = source_root / ".python-version"
        if agent.python_runtime:
            venv_args.extend(["--python", agent.python_runtime])
        elif python_version.is_file():
            venv_args.extend(["--python", python_version.read_text(encoding="utf-8").strip()])
        run(venv_args, timeout=120)
        if (source_root / "uv.lock").is_file():
            manifest = tomllib.loads((source_root / "pyproject.toml").read_text(encoding="utf-8"))
            required_uv = manifest.get("tool", {}).get("uv", {}).get("required-version", "")
            uv_command = (
                ["uvx", "--from", f"uv{required_uv}", "uv"]
                if re.fullmatch(r"==[0-9]+\.[0-9]+\.[0-9]+", required_uv)
                else ["uv"]
            )
            run(
                [
                    *uv_command,
                    "sync",
                    "--project",
                    str(source_root),
                    "--frozen",
                    "--no-default-groups",
                    *[argument for group in agent.source_dependency_groups for argument in ("--group", group)],
                    "--python",
                    str(bin_dir / "python"),
                ],
                env={"UV_PROJECT_ENVIRONMENT": str(install_dir.resolve())},
                timeout=INSTALL_TIMEOUT_SECONDS,
            )
        else:
            run(
                ["uv", "pip", "install", "--python", str(bin_dir / "python"), "--editable", str(source_root)],
                timeout=INSTALL_TIMEOUT_SECONDS,
            )
        for requirement in agent.source_extra_requirements:
            run(
                ["uv", "pip", "install", "--python", str(bin_dir / "python"), "-r", str(source_root / requirement)],
                timeout=INSTALL_TIMEOUT_SECONDS,
            )
    else:
        run(
            ["uv", "venv", str(install_dir), *(["--python", agent.python_runtime] if agent.python_runtime else [])],
            timeout=120,
        )
        run(
            ["uv", "pip", "install", "--python", str(bin_dir / "python"), package_ref],
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
    if not executable.exists():
        raise RuntimeError(f"GitHub release install did not create executable: {executable}")
    complete.touch()
    return bin_dir


def _find_source_root(extract_dir: Path) -> Path:
    projects = {path.parent for path in extract_dir.rglob("pyproject.toml")}
    roots = sorted(path for path in projects if not any(parent in projects for parent in path.parents))
    if len(roots) != 1:
        found = ", ".join(str(path) for path in roots) or "none"
        raise RuntimeError(f"expected one Python project in release archive, found: {found}")
    return roots[0]


def _install_github_release_asset(agent: AgentSpec, version: str, install_dir: Path) -> Path:
    bin_dir = install_dir / "bin"
    executable = bin_dir / agent_executable(agent)
    if executable.exists():
        return bin_dir
    if install_dir.exists():
        shutil.rmtree(install_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    asset = _github_release_asset_info(agent, version)
    archive_path = install_dir / asset["name"]
    _download(asset["url"], archive_path)
    if asset.get("sha512"):
        _verify_sha512(archive_path, asset["sha512"])
    if agent.release_asset_layout == "wheel":
        run(
            [
                "uv",
                "venv",
                str(install_dir),
                "--allow-existing",
                *(["--python", agent.python_runtime] if agent.python_runtime else []),
            ],
            timeout=120,
        )
        arguments = ["uv", "pip", "install", "--python", str(bin_dir / "python")]
        release = _github_release_by_tag(agent.package, version)
        dependencies = [(name, "-r") for name in agent.release_requirement_assets]
        if agent.release_constraint_asset:
            dependencies.append((agent.release_constraint_asset, "-c"))
        for name, flag in dependencies:
            matches = [item for item in release.get("assets", []) if item.get("name") == name]
            if len(matches) != 1 or not matches[0].get("browser_download_url"):
                raise RuntimeError(f"missing required release dependency asset: {name}")
            path = install_dir / name
            _download(matches[0]["browser_download_url"], path)
            arguments.extend([flag, str(path)])
        run([*arguments, str(archive_path)], timeout=INSTALL_TIMEOUT_SECONDS)
        if not executable.exists():
            raise RuntimeError(f"release wheel did not provide executable: {executable}")
        return bin_dir
    binary_name = agent.release_asset_binary or agent.tap_client
    if asset["name"] == binary_name:
        source = archive_path
    else:
        extract_dir = install_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _extract_archive(archive_path, extract_dir)
        source = _find_extracted_binary(extract_dir, binary_name)
    if agent.release_asset_layout == "bundle":
        source.chmod(source.stat().st_mode | 0o755)
        command = source.parent / agent_executable(agent)
        if command != source:
            if command.exists():
                raise RuntimeError(f"release bundle already contains a different command: {command}")
            command.symlink_to(source.name)
        bin_dir.rmdir()
        bin_dir.symlink_to(source.parent.resolve(), target_is_directory=True)
        return bin_dir
    shutil.copy2(source, executable)
    executable.chmod(executable.stat().st_mode | 0o755)
    return bin_dir


def _pypi_json(package: str) -> dict:
    url = f"https://pypi.org/pypi/{package}/json"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected PyPI response for {package}: {data!r}")
    return data


def _github_releases(repo: str) -> list[dict]:
    page = 1
    releases: list[dict] = []
    use_auth = _github_token() is not None
    while True:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        request = urllib.request.Request(url, headers=_github_headers(use_auth=use_auth))
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.load(response)
        except (HTTPError, IncompleteRead, RemoteDisconnected, URLError) as exc:
            if isinstance(exc, HTTPError) and exc.code == 401 and use_auth:
                use_auth = False
                continue
            if not isinstance(exc, HTTPError) or exc.code == 403:
                return _github_releases_via_gh(repo)
            raise
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected GitHub releases response for {repo}: {data!r}")
        if not data:
            return releases
        releases.extend(item for item in data if isinstance(item, dict))
        page += 1


def _github_headers(*, use_auth: bool = True) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "phistory",
    }
    token = _github_token()
    if use_auth and token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _github_releases_via_gh(repo: str) -> list[dict]:
    result = run(["gh", "api", f"repos/{repo}/releases", "--paginate", "--slurp"], timeout=120)
    pages = json.loads(result.stdout)
    if not isinstance(pages, list):
        raise RuntimeError(f"unexpected gh releases response for {repo}: {pages!r}")
    releases: list[dict] = []
    for page in pages:
        if isinstance(page, list):
            releases.extend(item for item in page if isinstance(item, dict))
    return releases


def _github_release_version(release: dict) -> VersionInfo:
    tag = release.get("tag_name")
    if not tag:
        raise RuntimeError(f"GitHub release without tag_name: {release!r}")
    return VersionInfo(
        version=str(tag),
        published_at=str(release.get("published_at")) if release.get("published_at") else None,
        tarball_url=str(release.get("html_url")) if release.get("html_url") else None,
    )


def _github_release_asset_version(agent: AgentSpec, release: dict) -> VersionInfo:
    version = _github_release_version(release)
    asset = _select_release_asset(agent, version.version, release)
    return VersionInfo(
        version=version.version,
        published_at=version.published_at,
        tarball_url=str(asset.get("browser_download_url"))
        if asset.get("browser_download_url")
        else version.tarball_url,
    )


def _github_release_asset_info(agent: AgentSpec, version: str) -> dict[str, str]:
    manifest_asset = _github_release_asset_from_manifest(agent, version)
    if manifest_asset:
        return manifest_asset
    release = _github_release_by_tag(agent.package, version)
    asset = _select_release_asset(agent, version, release)
    url = asset.get("browser_download_url")
    name = asset.get("name")
    if not url or not name:
        raise RuntimeError(f"GitHub release asset missing name or download URL: {asset!r}")
    return {"name": str(name), "url": str(url)}


def _github_release_asset_from_manifest(agent: AgentSpec, version: str) -> dict[str, str] | None:
    if not agent.release_manifest_url:
        return None
    try:
        with urllib.request.urlopen(agent.release_manifest_url, timeout=120) as response:
            data = json.load(response)
    except OSError:
        return None
    if not isinstance(data, dict) or str(data.get("version")) != version or not data.get("url"):
        return None
    name = agent.release_asset or Path(str(data["url"])).name
    asset = {"name": name, "url": str(data["url"])}
    if data.get("sha512"):
        asset["sha512"] = str(data["sha512"])
    return asset


def _github_release_by_tag(repo: str, tag: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    use_auth = _github_token() is not None
    while True:
        request = urllib.request.Request(url, headers=_github_headers(use_auth=use_auth))
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.load(response)
        except HTTPError as exc:
            if exc.code == 401 and use_auth:
                use_auth = False
                continue
            if exc.code == 403:
                return _github_release_by_tag_via_gh(repo, tag)
            raise
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected GitHub release response for {repo}@{tag}: {data!r}")
        return data


def _github_release_by_tag_via_gh(repo: str, tag: str) -> dict:
    result = run(["gh", "api", f"repos/{repo}/releases/tags/{tag}"], timeout=120)
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected gh release response for {repo}@{tag}: {data!r}")
    return data


def _select_release_asset(agent: AgentSpec, version: str, release: dict) -> dict:
    if not agent.release_asset:
        raise RuntimeError(f"{agent.id} does not define release_asset")
    expected = agent.release_asset.format(version=version, version_number=version.removeprefix("v"))
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError(f"GitHub release without assets for {agent.package}@{version}: {release!r}")
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == expected:
            return asset
    available = ", ".join(str(asset.get("name")) for asset in assets if isinstance(asset, dict))
    raise RuntimeError(f"asset {expected!r} not found for {agent.package}@{version}; available: {available}")


def _download(url: str, output: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "phistory"})
    with urllib.request.urlopen(request, timeout=300) as response, output.open("wb") as file:
        shutil.copyfileobj(response, file)


def _verify_sha512(path: Path, expected: str) -> None:
    digest = hashlib.sha512(path.read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise RuntimeError(f"sha512 mismatch for {path.name}")


def _extract_archive(archive: Path, output: Path) -> None:
    suffixes = "".join(archive.suffixes)
    if suffixes.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tar")):
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                target = (output / member.name).resolve()
                if not target.is_relative_to(output.resolve()):
                    raise RuntimeError(f"unsafe archive path: {member.name}")
            try:
                tar.extractall(output, filter="data")
            except TypeError:
                tar.extractall(output)
        return
    if suffixes.endswith(".zip"):
        with zipfile.ZipFile(archive) as zip_file:
            for member in zip_file.namelist():
                target = (output / member).resolve()
                if not target.is_relative_to(output.resolve()):
                    raise RuntimeError(f"unsafe archive path: {member}")
            zip_file.extractall(output)
        return
    raise RuntimeError(f"unsupported release asset archive: {archive.name}")


def _find_extracted_binary(root: Path, name: str) -> Path:
    direct = root / name
    if direct.exists() and direct.is_file():
        return direct
    matches = [path for path in root.rglob(name) if path.is_file()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(f"release asset did not contain executable: {name}")
    raise RuntimeError(f"release asset contained multiple {name!r} executables: {matches}")


def _pypi_version_from_release(version: str, files: object) -> VersionInfo:
    release_files = files if isinstance(files, list) else []
    preferred = next((item for item in release_files if item.get("packagetype") == "bdist_wheel"), None)
    preferred = preferred or next((item for item in release_files if isinstance(item, dict)), None)
    return VersionInfo(
        version=version,
        published_at=str(preferred.get("upload_time_iso_8601"))
        if preferred and preferred.get("upload_time_iso_8601")
        else None,
        tarball_url=str(preferred.get("url")) if preferred and preferred.get("url") else None,
    )


def _index_of(versions: list[VersionInfo], version: str) -> int:
    for idx, item in enumerate(versions):
        if item.version == version:
            return idx
    raise ValueError(f"version not found: {version}")


def _is_archivable_version(version: str, *, include_prerelease: bool) -> bool:
    if _PLATFORM_VERSION_RE.search(version):
        return False
    if include_prerelease:
        return True
    return "-" not in version and _PYPI_PRERELEASE_RE.search(version) is None


def _wrap_node_bin(executable: Path, node_runtime: str) -> None:
    _wrap_runtime_bin(executable, node_runtime, "NODE")


def _wrap_runtime_bin(executable: Path, runtime: str, marker_name: str) -> None:
    if not executable.exists():
        raise RuntimeError(f"cannot wrap missing executable: {executable}")
    marker = f"PHISTORY_{marker_name}_RUNTIME_WRAPPER"
    if not executable.is_symlink():
        try:
            if marker in executable.read_text(encoding="utf-8", errors="ignore"):
                return
        except OSError:
            pass
    if executable.is_symlink():
        real_executable = executable.resolve()
        executable.unlink()
    else:
        real_executable = executable.with_name(f"{executable.name}.real")
        if not real_executable.exists():
            executable.rename(real_executable)
    executable.write_text(
        f'#!/bin/sh\n# {marker}\nexec npx -y "{runtime}" "{real_executable}" "$@"\n',
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | 0o755)
