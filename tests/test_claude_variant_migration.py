import json
from pathlib import Path

import pytest

from phistory.migrate_claude_non_official import migrate


def _capture(root: Path, version: str, *, label: str = "Non-official API") -> Path:
    old = root / "claude-code" / version / "variants" / "default"
    old.mkdir(parents=True)
    (old / "prompt.md").write_text("original prompt\n", encoding="utf-8")
    (old / "trace.jsonl").write_bytes(b'{"raw":true}\n')
    (old / "meta.json").write_text(
        json.dumps(
            {
                "agent_id": "claude-code",
                "version": version,
                "variant": {
                    "id": "default",
                    "label": label,
                    "dimensions": {} if label == "Default" else {"api": "non-official"},
                },
                "requested": {} if label == "Default" else {"api": "non-official"},
                "command": ["claude_tap", "run", "claude"],
                "capture_host": {"platform": "Linux", "run_url": "https://example.test/actions/1"},
            }
        ),
        encoding="utf-8",
    )
    return old


def test_reclassifies_old_default_without_changing_raw_evidence(tmp_path: Path):
    old = _capture(tmp_path, "2.1.0")
    trace = (old / "trace.jsonl").read_bytes()
    prompt = (old / "prompt.md").read_bytes()

    assert migrate(tmp_path) == 1

    new = old.with_name("non-official")
    assert not old.exists()
    assert (new / "trace.jsonl").read_bytes() == trace
    assert (new / "prompt.md").read_bytes() == prompt
    meta = json.loads((new / "meta.json").read_text(encoding="utf-8"))
    assert meta["variant"] == {"id": "non-official", "label": "Non-official API", "dimensions": {"api": "non-official"}}
    assert meta["capture_host"] == {"platform": "Linux", "run_url": "https://example.test/actions/1"}
    assert meta["reclassified_from"]["variant"]["id"] == "default"


def test_reclassifies_legacy_unlabelled_default_without_inventing_requested_dimensions(tmp_path: Path):
    old = _capture(tmp_path, "1.0.0", label="Default")
    assert migrate(tmp_path) == 1
    meta = json.loads((old.with_name("non-official") / "meta.json").read_text(encoding="utf-8"))
    assert meta["variant"]["dimensions"] == {"api": "non-official"}
    assert meta["requested"] == {}
    assert meta["reclassified_from"] == {
        "variant": {"id": "default", "label": "Default", "dimensions": {}},
        "requested": {},
    }


def test_migration_validates_all_captures_before_moving_any(tmp_path: Path):
    valid = _capture(tmp_path, "1.0.0")
    invalid = _capture(tmp_path, "2.0.0", label="Default")
    meta_path = invalid / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["command"] = ["--mode", "forward"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ValueError, match="not a historical custom-API default"):
        migrate(tmp_path)
    assert valid.exists()
