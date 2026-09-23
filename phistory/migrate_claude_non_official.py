"""Reclassify archived Claude Code custom-API snapshots without recapturing them."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


def migrate(root: Path) -> int:
    pending: list[tuple[Path, Path, dict]] = []
    for old in sorted((root / "claude-code").glob("*/variants/default")):
        new = old.with_name("non-official")
        meta_path = old / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if new.exists() or not all((old / name).is_file() for name in ("prompt.md", "trace.jsonl")):
            raise ValueError(f"conflicting or incomplete Claude Code capture: {old}")
        legacy = meta.get("variant") == {"id": "default", "label": "Default", "dimensions": {}}
        recent = meta.get("variant") == {
            "id": "default",
            "label": "Non-official API",
            "dimensions": {"api": "non-official"},
        }
        if (
            meta.get("agent_id") != "claude-code"
            or meta.get("version") != old.parent.parent.name
            or not (
                (legacy and meta.get("requested") == {})
                or (recent and meta.get("requested") == {"api": "non-official"})
            )
            or "--mode" in meta.get("command", [])
        ):
            raise ValueError(f"not a historical custom-API default: {meta_path}")
        pending.append((old, new, meta))

    for old, new, meta in pending:
        old.rename(new)
        meta["reclassified_from"] = {"variant": deepcopy(meta["variant"]), "requested": deepcopy(meta["requested"])}
        meta["variant"] = {"id": "non-official", "label": "Non-official API", "dimensions": {"api": "non-official"}}
        (new / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(pending)


if __name__ == "__main__":
    print(f"migrated {migrate(Path('captures'))} Claude Code custom-API snapshots")
