import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("contains_secret", [False, True])
def test_qoder_guard_scans_explicit_smoke_root_without_printing_secret(tmp_path, contains_secret):
    capture_root = tmp_path / "smoke root" / "qoder"
    capture_root.mkdir(parents=True)
    token = "fake-smoke-secret"
    (capture_root / "trace.jsonl").write_text(token if contains_secret else "dummy request")
    script = Path(__file__).parents[1] / ".github/scripts/verify-qoder-secret.sh"
    result = subprocess.run(
        ["bash", str(script), str(capture_root)],
        cwd=tmp_path,
        env={**os.environ, "QODER_PERSONAL_ACCESS_TOKEN": token},
        text=True,
        capture_output=True,
    )
    assert result.returncode == (1 if contains_secret else 0)
    assert token not in result.stdout + result.stderr
