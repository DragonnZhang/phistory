from phistory.models import CommandResult, VersionInfo
from phistory.runtime_setup import prepare_npm_host, prepare_octomind_tap


def test_native_host_is_installed_at_resolved_release_and_records_real_version_without_parent_environment(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr("phistory.runtime_setup.packages.latest_version", lambda _: VersionInfo("1.2.3"))

    def install(agent, version, directory):
        calls.append((agent.package, version, directory))
        return directory / "bin"

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CommandResult(tuple(argv), 0, "native-host 1.2.3\n", "")

    monkeypatch.setattr("phistory.runtime_setup.packages.install_agent", install)
    monkeypatch.setattr("phistory.runtime_setup.run", run)
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin"}
    binary, provenance = prepare_npm_host(tmp_path / "cache", "@example/native-host", "native-host", env)
    assert calls[0] == ("@example/native-host", "1.2.3", tmp_path / "cache/native-host/1.2.3")
    assert calls[1][0] == [str(binary / "native-host"), "--version"]
    assert calls[1][1]["env"] == env and calls[1][1]["inherit_env"] is False
    assert provenance == {
        "package": "@example/native-host",
        "version": "1.2.3",
        "executable": "native-host",
        "version_output": "native-host 1.2.3",
    }


def test_default_tap_bootstrap_records_real_revision_without_running_agent(tmp_path, monkeypatch):
    commands = []
    revision = "c" * 40

    def run(argv, **kwargs):
        commands.append(argv)
        return CommandResult(tuple(argv), 0, revision + "\n" if "rev-parse" in argv else "", "")

    monkeypatch.setattr("phistory.runtime_setup.run", run)
    assert prepare_octomind_tap(tmp_path, "https://example.invalid/tap") == revision
    assert commands[0] == [
        "git",
        "clone",
        "--depth",
        "1",
        "https://example.invalid/tap",
        str(tmp_path / "taps/muvon/octomind-tap"),
    ]
    assert all("octomind" != argv[0] for argv in commands)


def test_official_sdk_example_is_preserved_with_content_hash(tmp_path, monkeypatch):
    import hashlib
    import io

    from phistory.runtime_setup import prepare_cuga_example

    source = b"# unchanged official example\n"
    monkeypatch.setattr("phistory.runtime_setup.urllib.request.urlopen", lambda *_args, **_kwargs: io.BytesIO(source))
    path, digest = prepare_cuga_example(tmp_path, "https://example.invalid/exact-tag/main.py")
    assert path.read_bytes() == source
    assert digest == hashlib.sha256(source).hexdigest()


def test_dependency_prewarm_runs_official_installer_with_isolated_environment(tmp_path, monkeypatch):
    import hashlib

    from phistory.runtime_setup import prepare_octobrain

    executable = tmp_path / "octobrain"
    executable.write_bytes(b"native binary")
    env = {"HOME": str(tmp_path / "home"), "PATH": str(tmp_path)}
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CommandResult(tuple(argv), 0, "octobrain 0.15.2\n", "")

    monkeypatch.setattr("phistory.runtime_setup.run", run)
    monkeypatch.setattr("phistory.runtime_setup.shutil.which", lambda *_args, **_kwargs: str(executable))
    result = prepare_octobrain(tmp_path, env)
    assert calls[0][0] == ["bash", str(tmp_path / "taps/muvon/octomind-tap/deps/muvon/octobrain.sh")]
    assert all(call[1]["env"] == env and call[1]["inherit_env"] is False for call in calls)
    assert result == {"version": "octobrain 0.15.2", "sha256": hashlib.sha256(b"native binary").hexdigest()}
