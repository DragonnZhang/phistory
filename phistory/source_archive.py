from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

CATALOG_PATH = Path(__file__).with_name("source_catalog.json")
SOURCE_SCOPE = (
    "Complete copies of the selected upstream source files, including prompt templates and tool registration code. "
    "These are source archives, not assembled system messages or captured API tool schemas. "
    "No CLI or model request was executed."
)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")


def read_catalog(path: Path = CATALOG_PATH) -> dict:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    repositories: set[str] = set()
    for record in catalog["records"]:
        agent_id, repository = record["id"], record["repository"]
        if not _ID.fullmatch(agent_id) or agent_id in seen:
            raise ValueError(f"invalid or duplicate source agent id: {agent_id}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or repository in repositories:
            raise ValueError(f"invalid or duplicate repository: {repository}")
        seen.add(agent_id)
        repositories.add(repository)
        if record.get("existing_agent"):
            continue
        if not record.get("files"):
            raise ValueError(f"{agent_id}: no pinned source files")
        for source in record["files"]:
            path = PurePosixPath(source["path"])
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"{agent_id}: unsafe source path: {path}")
            if not _COMMIT.fullmatch(source["commit"]):
                raise ValueError(f"{agent_id}: unpinned source commit")
            if record.get("source_commits") and source["commit"] not in record["source_commits"]:
                raise ValueError(f"{agent_id}: source commit belongs to a different survey repository")
            if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
                raise ValueError(f"{agent_id}: invalid source SHA-256")
            if not source["roles"] or set(source["roles"]) - {"prompt", "tools"}:
                raise ValueError(f"{agent_id}: invalid source roles")
    return catalog


def _get(url: str, *, github_api: bool = False) -> bytes:
    headers = {"User-Agent": "Phistory-source-archive", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if github_api and token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read(8 * 1024 * 1024 + 1)
            if len(content) > 8 * 1024 * 1024:
                raise ValueError(f"source file exceeds 8 MiB: {url}")
            return content
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def _head_commit(repository: str) -> str:
    metadata = json.loads(_get(f"https://api.github.com/repos/{repository}/commits/HEAD", github_api=True))
    commit = metadata["sha"]
    if not _COMMIT.fullmatch(commit):
        raise ValueError(f"{repository}: invalid GitHub commit")
    return commit


def _source_url(repository: str, source: dict) -> str:
    path = urllib.parse.quote(source["path"], safe="/")
    return f"https://raw.githubusercontent.com/{repository}/{source['commit']}/{path}"


def _download(repository: str, source: dict, *, verify_seed: bool) -> tuple[dict, bytes]:
    content = _get(_source_url(repository, source))
    digest = hashlib.sha256(content).hexdigest()
    if verify_seed and digest != source["sha256"]:
        raise ValueError(f"{repository}/{source['path']}: SHA-256 differs from the pinned survey evidence")
    content.decode("utf-8")
    return {**source, "sha256": digest}, content


def _snapshot_id(files: list[dict]) -> str:
    identity = [{key: item[key] for key in ("commit", "path", "sha256", "roles")} for item in files]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]


def archive_project(record: dict, root: Path, *, latest: bool = False, force: bool = False) -> dict:
    if record.get("existing_agent"):
        return {"id": record["id"], "status": "existing", "agent": record["existing_agent"]}
    repository = record["repository"]
    files = record["files"]
    if latest:
        commit = _head_commit(repository)
        files = [{**item, "commit": commit} for item in files]
        merged = {}
        for item in files:
            if item["path"] in merged:
                item = {**item, "roles": sorted(set(item["roles"]) | set(merged[item["path"]]["roles"]))}
            merged[item["path"]] = item
        files = list(merged.values())
    elif not force:
        snapshot = _snapshot_id(files)
        if _is_archived(root / record["id"] / snapshot, files):
            return {"id": record["id"], "snapshot": snapshot, "status": "skipped"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(lambda item: _download(repository, item, verify_seed=not latest), files))
    sources = [item for item, _ in downloaded]
    snapshot = _snapshot_id(sources)
    destination = root / record["id"] / snapshot
    if not force and _is_archived(destination, sources):
        return {"id": record["id"], "snapshot": snapshot, "status": "skipped"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".source-", dir=destination.parent))
    try:
        for item, content in downloaded:
            relative = Path("files") / item["commit"] / item["path"]
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            item["archive_path"] = relative.as_posix()
            item["url"] = f"https://github.com/{repository}/blob/{item['commit']}/{item['path']}"
        for role, output in [("prompt", "prompts.md"), ("tools", "tools.md")]:
            (staging / output).write_text(_source_markdown(record, downloaded, role), encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "archive_kind": "source-files",
            "scope": SOURCE_SCOPE,
            "agent_id": record["id"],
            "agent": record["name"],
            "repository": repository,
            "snapshot": snapshot,
            "selection": "latest-default-branch" if latest else "survey-pinned",
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "archive_host": _archive_host(),
            "files": sources,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"id": record["id"], "snapshot": snapshot, "status": "archived", "files": len(sources)}


def _archive_host() -> dict:
    import platform

    host = {"system": platform.system(), "machine": platform.machine()}
    if os.environ.get("GITHUB_ACTIONS") == "true":
        host["github_actions"] = {
            "run_url": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
            "sha": os.environ.get("GITHUB_SHA", ""),
        }
    return host


def _is_archived(path: Path, expected: list[dict]) -> bool:
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest["snapshot"] != _snapshot_id(expected):
            return False
        if not (path / "prompts.md").is_file() or not (path / "tools.md").is_file():
            return False
        archived = manifest["files"]
        if len(archived) != len(expected):
            return False
        for item, source in zip(archived, expected):
            if any(item[key] != source[key] for key in ("path", "commit", "roles", "sha256")):
                return False
            if hashlib.sha256((path / item["archive_path"]).read_bytes()).hexdigest() != item["sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError):
        return False


def _source_markdown(record: dict, downloaded: list[tuple[dict, bytes]], role: str) -> str:
    title = "System prompt source" if role == "prompt" else "Toolset source"
    lines = [f"# {record['name']} — {title}", "", SOURCE_SCOPE, "", record[f"{role}_scope"], ""]
    selected = [(item, content) for item, content in downloaded if role in item["roles"]]
    if not selected:
        lines.extend(
            [
                "No dedicated source file was identified for this surface in the survey. See the other surface and manifest.",
                "",
            ]
        )
    for item, content in selected:
        text = content.decode("utf-8")
        longest = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
        fence = "`" * max(3, longest + 1)
        lines.extend(
            [
                f"## {item['path']}",
                "",
                f"Source: {item['url']}",
                "",
                f"SHA-256: `{item['sha256']}`",
                "",
                fence,
                text,
                fence,
                "",
            ]
        )
    return "\n".join(lines)


def archive_sources(root: Path, *, agents: str | None = None, latest: bool = False, workers: int = 4) -> list[dict]:
    catalog = read_catalog()
    records = catalog["records"]
    if agents:
        selected = set(agents.split(","))
        unknown = selected - {item["id"] for item in records}
        if unknown:
            raise ValueError(f"unknown source agents: {', '.join(sorted(unknown))}")
        records = [item for item in records if item["id"] in selected]
    root.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(archive_project, item, root, latest=latest): item for item in records}
        for future in as_completed(futures):
            record = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"id": record["id"], "status": "failed", "error": str(exc)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            results.append(result)
    return sorted(results, key=lambda item: item["id"])


def render_source_index(root: Path, output: Path) -> None:
    catalog = read_catalog()
    records = []
    sections = []
    for record in catalog["records"]:
        snapshots = []
        for path in sorted((root / record["id"]).glob("*/manifest.json")):
            manifest = json.loads(path.read_text())
            if not _is_archived(path.parent, manifest["files"]):
                raise ValueError(f"invalid source archive: {path}")
            snapshots.append({**manifest, "path": path.parent.as_posix()})
        snapshots.sort(key=lambda item: item["archived_at"], reverse=True)
        records.append({**record, "snapshots": snapshots})
        name = html.escape(record["name"])
        repo = html.escape(record["repository"])
        body = f'<p><a href="https://github.com/{repo}">{repo}</a></p>'
        if record.get("existing_agent"):
            agent = urllib.parse.quote(record["existing_agent"])
            body += f'<p>Already integrated: <a href="index.html?agent={agent}">request captures</a>. Existing captures are unchanged.</p>'
            status = "request capture"
        elif not snapshots:
            body += "<p>Source archive pending.</p>"
            status = "pending"
        else:
            status = "source archive"
            for snapshot in snapshots:
                base = html.escape(snapshot["path"])
                body += (
                    f"<h3>{html.escape(snapshot['selection'])} · {snapshot['snapshot']}</h3>"
                    f'<p><a href="{base}/prompts.md">Prompt source</a> · <a href="{base}/tools.md">Toolset source</a> · '
                    f'<a href="{base}/manifest.json">Provenance</a> · {len(snapshot["files"])} complete source files</p>'
                )
                body += (
                    "<ul>"
                    + "".join(
                        f'<li><a href="{base}/{html.escape(item["archive_path"])}">{html.escape(item["path"])}</a>'
                        f" <small>{', '.join(item['roles'])} · {item['commit'][:12]}</small></li>"
                        for item in snapshot["files"]
                    )
                    + "</ul>"
                )
        sections.append(f"<details><summary>{name} <small>{status}</small></summary>{body}</details>")
    existing = sum(bool(item.get("existing_agent")) for item in records)
    archived = sum(bool(item["snapshots"]) for item in records)
    body = "\n".join(sections)
    output.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Phistory · Harness source archive</title><style>body{font:16px/1.6 system-ui;margin:40px auto;padding:0 24px;max-width:1000px;"
        "background:#11151c;color:#e5e7eb}a{color:#8dc7ff}details{border-top:1px solid #384152;padding:16px 0}summary{cursor:pointer;font-weight:600}"
        "small{font-weight:400;color:#a8b5c7;margin-left:12px}li{overflow-wrap:anywhere}h1{line-height:1.2}h3{font-size:16px}</style>"
        '<a href="index.html">← Phistory request captures</a><h1>Harness source archive</h1>'
        f"<p>{len(records)} surveyed agents · {existing} already integrated · {archived} additional source archives.</p>"
        f"<p>{html.escape(SOURCE_SCOPE)}</p><p>Each original file is preserved byte for byte at its recorded Git commit, with SHA-256 and collection provenance. "
        "A project can have no fixed system prompt or delegate its tools to another host; the archived code keeps that behavior visible.</p>"
        f"{body}</html>\n",
        encoding="utf-8",
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps({"schema_version": 1, "scope": SOURCE_SCOPE, "records": records}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
