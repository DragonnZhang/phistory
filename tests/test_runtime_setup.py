from phistory.models import CommandResult
from phistory.runtime_setup import prepare_octomind_tap


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
