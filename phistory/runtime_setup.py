from __future__ import annotations

import re
from pathlib import Path

from phistory.subprocesses import run


def prepare_octomind_tap(state: Path, repository: str) -> str:
    tap = state / "taps" / "muvon" / "octomind-tap"
    tap.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", repository, str(tap)], timeout=120)
    revision = run(["git", "-C", str(tap), "rev-parse", "HEAD"]).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise RuntimeError("Octomind's default tap did not resolve to an immutable Git commit")
    return revision
