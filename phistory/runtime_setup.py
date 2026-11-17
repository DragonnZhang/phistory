from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
from pathlib import Path

from phistory import packages
from phistory.models import AgentSpec
from phistory.subprocesses import run


def prepare_npm_host(cache: Path, package: str, executable: str, env: dict[str, str]) -> tuple[Path, dict[str, str]]:
    host = AgentSpec("native-host", executable, package, executable, {})
    version = packages.latest_version(host).version
    bin_dir = packages.install_agent(host, version, cache / executable / version)
    version_output = run(
        [str(bin_dir / executable), "--version"], env=env, inherit_env=False, timeout=30
    ).stdout.strip()
    return bin_dir, {"package": package, "version": version, "executable": executable, "version_output": version_output}


def prepare_octomind_tap(state: Path, repository: str) -> str:
    tap = state / "taps" / "muvon" / "octomind-tap"
    tap.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", repository, str(tap)], timeout=120)
    revision = run(["git", "-C", str(tap), "rev-parse", "HEAD"]).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise RuntimeError("Octomind's default tap did not resolve to an immutable Git commit")
    return revision


def prepare_cuga_example(home: Path, source: str) -> tuple[Path, str]:
    request = urllib.request.Request(source, headers={"User-Agent": "phistory"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    path = home / "cuga-hello-tools.py"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def prepare_octobrain(state: Path, env: dict[str, str]) -> dict[str, str]:
    script = state / "taps/muvon/octomind-tap/deps/muvon/octobrain.sh"
    run(["bash", str(script)], env=env, inherit_env=False, timeout=300)
    executable = shutil.which("octobrain", path=env["PATH"])
    if executable is None:
        raise RuntimeError("the official Octomind dependency script did not install octobrain")
    version = run([executable, "--version"], env=env, inherit_env=False, timeout=30).stdout.strip()
    return {"version": version, "sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()}
