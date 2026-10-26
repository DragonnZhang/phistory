import json
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

from phistory.models import AgentSpec
from phistory.packages import install_agent


def _wheel(directory, name, version, *, dependencies=(), cli=False):
    path = directory / f"{name}-{version}-py3-none-any.whl"
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f"VERSION = {version!r}\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {item}\n" for item in dependencies),
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if cli:
        files[f"{name}/__init__.py"] = """import importlib.util
import json
import sys
import phistory_test_leaf

def main():
    print(json.dumps({
        'version': phistory_test_leaf.VERSION,
        'prefix': sys.prefix,
        'dev_installed': importlib.util.find_spec('phistory_test_dev') is not None,
    }))
"""
        files[f"{dist}/entry_points.txt"] = f"[console_scripts]\nhermes = {name}:main\n"
    files[f"{dist}/RECORD"] = "".join(f"{name},,\n" for name in files) + f"{dist}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return path


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for the offline installer integration")
def test_locked_release_uses_upstream_resolution_in_its_own_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    caller_environment = tmp_path / "caller-environment"
    caller_environment.mkdir()
    (caller_environment / "untouched").write_text("caller")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(caller_environment))
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    sources = {
        "phistory-test-cli": _wheel(
            wheels, "phistory_test_cli", "1.0", dependencies=["phistory-test-leaf==1.0"], cli=True
        ),
        "phistory-test-leaf": _wheel(wheels, "phistory_test_leaf", "2.0"),
        "phistory-test-dev": _wheel(wheels, "phistory_test_dev", "1.0"),
    }
    release = tmp_path / "release"
    release.mkdir()
    (release / ".python-version").write_text(f"{sys.version_info.major}.{sys.version_info.minor}\n")
    (release / "pyproject.toml").write_text(
        """[project]
name = "fixture-release"
version = "1.0"
requires-python = ">=3.11"
dependencies = ["phistory-test-cli==1.0", "phistory-test-leaf==2.0"]
[dependency-groups]
dev = ["phistory-test-dev==1.0"]
[tool.uv]
package = false
override-dependencies = ["phistory-test-leaf==2.0"]
[tool.uv.sources]
"""
        + "".join(f"{name} = {{ path = {json.dumps(str(path))} }}\n" for name, path in sources.items())
    )
    subprocess.run(["uv", "lock", "--project", str(release)], check=True, capture_output=True, text=True)
    lock_bytes = (release / "uv.lock").read_bytes()

    def fake_download(_url, output):
        with tarfile.open(output, "w:gz") as archive:
            archive.add(release, arcname="fixture-release")

    monkeypatch.setattr("phistory.packages._download", fake_download)
    agent = AgentSpec(
        id="hermes",
        display_name="Fake locked CLI",
        package="example/fixture",
        source="github-release",
        github_release_install="editable",
        tap_client="hermes",
        fake_env={},
    )
    install_dir = tmp_path / "isolated-install"
    bin_dir = install_agent(agent, "v1", install_dir)
    result = subprocess.run([str(bin_dir / "hermes")], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == {"version": "2.0", "prefix": str(install_dir), "dev_installed": False}
    assert (install_dir / "source/fixture-release/uv.lock").read_bytes() == lock_bytes
    assert sorted(path.name for path in caller_environment.iterdir()) == ["untouched"]
