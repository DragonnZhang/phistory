from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from phistory.models import CaptureTarget
from phistory.static_prompts.extract import QODER_BUN_EXTRACTOR, QODER_NATIVE_EXTRACTOR, extract_static_prompts
from phistory.static_prompts.models import StaticPromptResult
from phistory.storage import prepare_version_dir, write_meta

STATIC_ONLY_REASON_CODE = "retired-packaged-client"
STATIC_ONLY_REASON = (
    "This retired packaged Qoder client no longer reaches a prompt-bearing generation request. "
    "Prompt material extracted from this release's exact official executable is archived instead, with its extraction "
    "mode recorded explicitly."
)


def archive_qoder_static_prompt(target: CaptureTarget, install_dir: Path) -> StaticPromptResult:
    if target.agent.id != "qoder":
        raise ValueError("native static-only archives are supported only for Qoder CLI")
    if target.variant.id != "default":
        raise ValueError("native static-only archives support only Qoder's default variant")

    result = extract_static_prompts(target, install_dir)
    if result is None or not result.matches:
        count = len(result.matches) if result is not None else 0
        raise RuntimeError(f"expected embedded Qoder prompt material, found {count} candidates")
    exact_template = len(result.matches) == 1 and result.matches[0].candidate.kind == "go-string"
    extractor = QODER_NATIVE_EXTRACTOR if exact_template else QODER_BUN_EXTRACTOR
    if exact_template:
        content = result.matches[0].candidate.content
        prompt = _static_only_prompt(content)
        archive_mode = "exact-template"
    else:
        prompt = _static_only_candidates_prompt(target.static_prompts_path.read_text(encoding="utf-8"))
        content = prompt
        archive_mode = "prompt-candidates"
    archived_at = _iso_now()
    exact_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    prepare_version_dir(target)
    target.prompt_path.write_text(prompt, encoding="utf-8")
    target.trace_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "phistory.capture-status",
                "capture_status": "static-only",
                "reason_code": STATIC_ONLY_REASON_CODE,
                "message": STATIC_ONLY_REASON,
                "static_prompt": "../../static/prompts.md",
                "archive_mode": archive_mode,
                "archived_content_sha256": exact_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_meta(
        target,
        {
            "agent_id": target.agent.id,
            "agent": target.agent.display_name,
            "package": target.agent.package,
            "version": target.version.version,
            "variant": {
                "id": target.variant.id,
                "label": target.variant.label,
                "dimensions": target.variant.dimensions,
            },
            "requested": target.variant.dimensions,
            "observed": {},
            "published_at": target.version.published_at,
            "tarball_url": target.version.tarball_url,
            "binary_version": target.version.version,
            "captured_at": archived_at,
            "archived_at": archived_at,
            "tap_client": target.agent.tap_client,
            "capture_status": "static-only",
            "capture_unavailable": {
                "reason_code": STATIC_ONLY_REASON_CODE,
                "message": STATIC_ONLY_REASON,
            },
            "target": "exact published native binary static extraction",
            "static_archive": {
                "source": result.source,
                "extractor": extractor,
                "mode": archive_mode,
                "archived_content_sha256": exact_sha256,
                "prompt_count": len(result.matches),
            },
        },
    )
    return result


def _static_only_prompt(content: str) -> str:
    return (
        "# Qoder coder system prompt template (static-only)\n\n"
        "> [!NOTE]\n"
        "> No prompt-bearing live request was captured for this retired packaged client. The content below is the exact "
        "coder system-prompt template extracted from this release's official executable. See `meta.json` and "
        "`../../static/candidates.json` for provenance.\n\n"
        "## Exact embedded Go template\n\n"
        f"{content.rstrip()}\n"
    )


def _static_only_candidates_prompt(markdown: str) -> str:
    body = markdown.replace("# Static Prompts", "## Extracted prompt candidates", 1)
    return (
        "# Qoder prompt material (static-only)\n\n"
        "> [!NOTE]\n"
        "> No prompt-bearing live request was captured for this retired packaged client. The sections below are "
        "prompt-like strings extracted exactly from this release's official Bun executable; they are not presented as "
        "a fully rendered runtime request. See `meta.json` and `../../static/candidates.json` for provenance.\n\n"
        f"{body.rstrip()}\n"
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
