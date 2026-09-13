import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/verify-qoder-secret.sh"
SECRET = "fake-qoder-secret.[literal]"


def run_scan(root: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        cwd=root,
        env={**os.environ, "QODER_PERSONAL_ACCESS_TOKEN": SECRET, **env},
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("leaked", [False, True])
def test_secret_scan_without_ripgrep(tmp_path: Path, leaked: bool):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "grep").symlink_to(shutil.which("grep"))
    capture_dir = tmp_path / "captures/qoder/1.0.0/variants/default"
    capture_dir.mkdir(parents=True)
    trace = capture_dir / ".trace.jsonl"
    value = SECRET if leaked else "<redacted>"
    trace.write_bytes(b"\x00" + value.encode())

    result = run_scan(tmp_path, PATH=str(bin_dir))

    assert result.returncode == (1 if leaked else 0)
    assert SECRET not in result.stdout + result.stderr
    if leaked:
        assert str(trace.relative_to(tmp_path)) in result.stdout
        assert "Qoder credential leak detected" in result.stdout


@pytest.mark.parametrize("missing_command", [False, True])
def test_secret_scan_fails_when_scanner_errors(tmp_path: Path, missing_command: bool):
    (tmp_path / "captures/qoder").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if not missing_command:
        grep = bin_dir / "grep"
        grep.write_text("#!/bin/sh\nexit 2\n")
        grep.chmod(0o755)

    result = run_scan(tmp_path, PATH=str(bin_dir))

    assert result.returncode == (127 if missing_command else 2)
    assert "Qoder credential scan failed" in result.stdout
    assert SECRET not in result.stdout + result.stderr


@pytest.mark.parametrize("secret", ["", SECRET])
def test_secret_scan_skips_when_not_applicable(tmp_path: Path, secret: str):
    if not secret:
        (tmp_path / "captures/qoder").mkdir(parents=True)

    result = run_scan(tmp_path, QODER_PERSONAL_ACCESS_TOKEN=secret, PATH="")

    assert result.returncode == 0
