from __future__ import annotations

import hashlib
import json

import pytest

from phistory import source_archive
from phistory.registry import AGENTS


def project(content: bytes = b"You are a test agent.\n") -> dict:
    return {
        "id": "example",
        "name": "Example",
        "repository": "example/agent",
        "existing_agent": None,
        "prompt_scope": "Prompt template, not runtime interpolation.",
        "tools_scope": "Tool registration code, not runtime schema.",
        "files": [
            {
                "commit": "a" * 40,
                "path": "prompts/system.md",
                "sha256": hashlib.sha256(content).hexdigest(),
                "roles": ["prompt", "tools"],
            }
        ],
    }


def test_survey_catalog_preserves_all_95_and_skips_existing_integrations():
    catalog = source_archive.read_catalog()
    assert len(catalog["records"]) == 95
    existing = [item for item in catalog["records"] if item.get("existing_agent")]
    missing = [item for item in catalog["records"] if not item.get("existing_agent")]
    assert len(existing) == 12
    assert len(missing) == 83
    assert all(item["existing_agent"] in AGENTS for item in existing)
    assert all(item["files"] for item in missing)


def test_archive_preserves_bytes_provenance_and_distinct_surfaces(tmp_path, monkeypatch):
    content = b"You are an agent.\r\n```tool\nread_file\n```\r\n"
    record = project(content)
    calls = []

    def get(url):
        calls.append(url)
        return content

    monkeypatch.setattr(source_archive, "_get", get)
    result = source_archive.archive_project(record, tmp_path)
    snapshot = tmp_path / "example" / result["snapshot"]
    manifest = json.loads((snapshot / "manifest.json").read_text())
    saved = snapshot / manifest["files"][0]["archive_path"]
    assert saved.read_bytes() == content
    assert manifest["archive_kind"] == "source-files"
    assert manifest["selection"] == "survey-pinned"
    assert manifest["archive_host"]["system"]
    assert "not assembled system messages" in manifest["scope"]
    assert "````" in (snapshot / "prompts.md").read_text()
    assert not list(snapshot.glob("trace*"))
    assert len(calls) == 1
    assert source_archive.archive_project(record, tmp_path)["status"] == "skipped"
    assert len(calls) == 1
    saved.write_bytes(b"damaged")
    assert source_archive.archive_project(record, tmp_path)["status"] == "archived"
    assert saved.read_bytes() == content


def test_hash_mismatch_does_not_publish_partial_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(source_archive, "_get", lambda url: b"unexpected upstream content")
    with pytest.raises(ValueError, match="SHA-256 differs"):
        source_archive.archive_project(project(), tmp_path)
    assert not list(tmp_path.rglob("manifest.json"))


def test_existing_integrations_are_not_downloaded_or_modified(tmp_path, monkeypatch):
    def fail(url):
        pytest.fail("existing integration was downloaded")

    monkeypatch.setattr(source_archive, "_get", fail)
    record = {"id": "codex", "existing_agent": "codex"}
    assert source_archive.archive_project(record, tmp_path)["status"] == "existing"
    assert list(tmp_path.iterdir()) == []


def test_latest_pins_head_once_and_preserves_new_content(tmp_path, monkeypatch):
    calls = []
    content = b"Current upstream prompt\n"

    def get(url, *, github_api=False):
        calls.append(url)
        if github_api:
            return json.dumps({"sha": "b" * 40}).encode()
        assert "/" + "b" * 40 + "/" in url
        return content

    monkeypatch.setattr(source_archive, "_get", get)
    result = source_archive.archive_project(project(), tmp_path, latest=True)
    manifest = json.loads((tmp_path / "example" / result["snapshot"] / "manifest.json").read_text())
    assert manifest["selection"] == "latest-default-branch"
    assert manifest["files"][0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert sum("/commits/HEAD" in url for url in calls) == 1


def test_failed_project_is_reported_without_losing_other_archives(tmp_path, monkeypatch):
    good = project()
    bad = {**good, "id": "broken", "repository": "example/broken"}
    monkeypatch.setattr(source_archive, "read_catalog", lambda: {"records": [good, bad]})

    def get(url):
        if "/broken/" in url:
            raise OSError("upstream unavailable")
        return b"You are a test agent.\n"

    monkeypatch.setattr(source_archive, "_get", get)
    results = source_archive.archive_sources(tmp_path)
    assert [(item["id"], item["status"]) for item in results] == [("broken", "failed"), ("example", "archived")]
    assert len(list(tmp_path.rglob("manifest.json"))) == 1


@pytest.mark.parametrize("path", ["../escape", "/absolute", "nested/../../escape"])
def test_catalog_rejects_unsafe_paths(tmp_path, path):
    record = project()
    record["files"][0]["path"] = path
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"records": [record]}))
    with pytest.raises(ValueError, match="unsafe source path"):
        source_archive.read_catalog(catalog)


def test_source_view_links_originals_and_explains_unassembled_scope(tmp_path, monkeypatch):
    record = project()
    monkeypatch.setattr(source_archive, "_get", lambda url: b"You are a test agent.\n")
    monkeypatch.setattr(
        source_archive,
        "read_catalog",
        lambda: {
            "records": [
                record,
                {"id": "codex", "name": "Codex", "repository": "openai/codex", "existing_agent": "codex"},
            ]
        },
    )
    source_archive.archive_project(record, tmp_path / "sources")
    output = tmp_path / "sources.html"
    source_archive.render_source_index(tmp_path / "sources", output)
    text = output.read_text()
    assert "2 surveyed agents · 1 already integrated · 1 additional source archives" in text
    assert "not assembled system messages" in text
    assert "prompts.md" in text and "tools.md" in text
    assert "index.html?agent=codex" in text
    assert len(json.loads((tmp_path / "sources/index.json").read_text())["records"]) == 2
