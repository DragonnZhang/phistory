from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


def compatibility_metadata(install_dir: Path, version: str) -> dict[str, str]:
    cached = install_dir / "interpreter-compatibility.json"
    if cached.exists():
        result = json.loads(cached.read_text())
        if result.get("release") == version and result.get("compatibility_version"):
            return result
    tag = urllib.parse.quote(version, safe="")
    url = f"https://raw.githubusercontent.com/openinterpreter/openinterpreter/{tag}/codex-rs/product-info/src/lib.rs"
    request = urllib.request.Request(url, headers={"User-Agent": "phistory"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    matches = re.findall(rb'pub const OPEN_INTERPRETER_CODEX_COMPATIBILITY_VERSION:\s*&str\s*=\s*"([^"]+)";', content)
    if len(matches) != 1:
        raise RuntimeError(f"cannot derive Interpreter compatibility version from {url}")
    result = {
        "release": version,
        "source": url,
        "sha256": hashlib.sha256(content).hexdigest(),
        "compatibility_version": matches[0].decode("utf-8"),
    }
    cached.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
