import json
import struct

import pytest

from phistory.models import AgentSpec, CaptureTarget, VersionInfo
from phistory.static_prompts.archive import archive_qoder_static_prompt
from phistory.static_prompts.bun import BUN_TRAILER, _bun_blob_from_macho_section, _is_entrypoint_name
from phistory.static_prompts.catalog import load_catalog, match_candidates, normalize_for_match
from phistory.static_prompts.extract import (
    StaticSourceUnavailable,
    _claude_code_source,
    _keep_known_or_prompt_like,
    normalize_static_prompt_markdown_content,
    read_static_candidates,
    render_static_prompts_markdown,
    write_static_candidates,
)
from phistory.static_prompts.javascript import extract_prompt_candidates, extract_string_candidates
from phistory.static_prompts.models import StaticCandidatesResult, StaticPromptMatch, StaticPromptResult
from phistory.static_prompts.qoder import QODER_CODER_TEMPLATE_MARKER, extract_qoder_coder_prompt
from phistory.storage import is_captured


def _qoder_prompt() -> bytes:
    return (
        QODER_CODER_TEMPLATE_MARKER
        + b" Use the instructions below and the tools available to you to assist the user.\n\n"
        + b"{{.EnvironmentInfo}}\n\n# Code References\nUse file_path:line_number.\n{{end}}"
    )


def _synthetic_elf_qoder_binary(prompt: bytes) -> bytes:
    data = bytearray(0x400 + len(prompt))
    data[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<H", data, 54, 56)
    struct.pack_into("<H", data, 56, 1)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 5, 0, 0x400000, 0x400000, len(data), len(data), 0x1000)
    prompt_offset = 0x300
    data[prompt_offset : prompt_offset + len(prompt)] = prompt
    struct.pack_into("<QQ", data, 0x180, 0x400000 + prompt_offset, len(prompt))
    return bytes(data)


def _synthetic_macho_qoder_binary(prompt: bytes) -> bytes:
    data = bytearray(0x400 + len(prompt))
    struct.pack_into("<IiiIIIII", data, 0, 0xFEEDFACF, 0x0100000C, 0, 2, 1, 72, 0, 0)
    struct.pack_into(
        "<II16sQQQQiiII",
        data,
        32,
        0x19,
        72,
        b"__TEXT\0\0\0\0\0\0\0\0\0\0",
        0x100000000,
        len(data),
        0,
        len(data),
        7,
        5,
        0,
        0,
    )
    prompt_offset = 0x300
    data[prompt_offset : prompt_offset + len(prompt)] = prompt
    struct.pack_into("<QQ", data, 0x180, 0x100000000 + prompt_offset, len(prompt))
    return bytes(data)


def test_bun_blob_extraction_supports_macho_qoder_section():
    blob = b"compiled entrypoint" + b"\0" * 32 + BUN_TRAILER
    section = struct.pack("<Q", len(blob)) + blob
    data = bytearray(0x200 + len(section))
    struct.pack_into("<IiiIIIII", data, 0, 0xFEEDFACF, 0x0100000C, 0, 2, 1, 152, 0, 0)
    struct.pack_into("<II16sQQQQiiII", data, 32, 0x19, 152, b"__BUN", 0x100000000, len(data), 0, len(data), 3, 3, 1, 0)
    struct.pack_into(
        "<16s16sQQIIIIIIII", data, 104, b"__bun", b"__BUN", 0x100000200, len(section), 0x200, 0, 0, 0, 0, 0, 0, 0
    )
    data[0x200:] = section

    assert _bun_blob_from_macho_section(bytes(data)) == blob
    assert _is_entrypoint_name("/$bunfs/root/index.js")


@pytest.mark.parametrize("binary_factory", [_synthetic_elf_qoder_binary, _synthetic_macho_qoder_binary])
def test_qoder_native_prompt_extraction_uses_exact_go_string_header(tmp_path, binary_factory):
    prompt = _qoder_prompt()
    binary = tmp_path / "qodercli"
    binary.write_bytes(binary_factory(prompt))

    assert extract_qoder_coder_prompt(binary) == prompt.decode("utf-8")


def test_qoder_native_static_archive_is_transparently_labeled(tmp_path):
    install_dir = tmp_path / "install"
    package_dir = install_dir / "node_modules/@qoder-ai/qodercli"
    binary = package_dir / "bin/qodercli"
    binary.parent.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps({"name": "@qoder-ai/qodercli", "version": "0.1.29", "bin": {"qodercli": "bin/qodercli"}}),
        encoding="utf-8",
    )
    prompt = _qoder_prompt()
    binary.write_bytes(_synthetic_elf_qoder_binary(prompt))
    agent = AgentSpec(
        id="qoder",
        display_name="Qoder CLI",
        package="@qoder-ai/qodercli",
        tap_client="qoder",
        fake_env={},
    )
    target = CaptureTarget(
        agent, VersionInfo("0.1.29", "2026-01-01T00:00:00Z"), agent.default_variant, tmp_path / "captures"
    )

    result = archive_qoder_static_prompt(target, install_dir)

    assert is_captured(target)
    assert result.matches[0].entry is not None
    assert result.matches[0].entry.id == "qoder-coder-system-template"
    assert prompt.decode("utf-8") in target.prompt_path.read_text(encoding="utf-8")
    trace = json.loads(target.trace_path.read_text(encoding="utf-8"))
    meta = json.loads(target.meta_path.read_text(encoding="utf-8"))
    assert trace["record_type"] == "phistory.capture-status"
    assert trace["capture_status"] == "static-only"
    assert "request" not in trace
    assert meta["capture_status"] == "static-only"
    assert meta["capture_unavailable"]["reason_code"] == "retired-packaged-client"
    assert meta["static_archive"]["source"] == "node_modules/@qoder-ai/qodercli/bin/qodercli"
    assert meta["static_archive"]["mode"] == "exact-template"


def test_claude_code_binary_only_package_marks_static_source_unavailable(tmp_path):
    package_dir = tmp_path / "node_modules/@anthropic-ai/claude-code"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps({"bin": {"claude": "bin/claude"}}), encoding="utf-8")
    binary = package_dir / "bin/claude"
    binary.parent.mkdir()
    binary.write_bytes(b"\x00native executable")

    with pytest.raises(StaticSourceUnavailable, match="does not include extractable source"):
        _claude_code_source(tmp_path)


def test_javascript_prompt_extraction_skips_comments_and_matches_known_catalog():
    entry = next(item for item in load_catalog("claude-code") if item.id == "agent-auto-mode-rule-reviewer")
    content = "\n\n".join(entry.anchors[:3])
    source = "\n".join(
        [
            "// You are not a real prompt in a comment.",
            "const small = 'You are too short';",
            f"const prompt = {content!r};",
        ]
    )

    candidates = extract_prompt_candidates(source)
    matches = match_candidates("claude-code", candidates)

    assert len(candidates) == 1
    assert matches[0].entry is not None
    assert matches[0].entry.id == "agent-auto-mode-rule-reviewer"
    assert matches[0].confidence == "anchor"


def test_javascript_prompt_extraction_filters_static_resources():
    source = "\n".join(
        [
            "const regex = '\\\\b(ll(AgentInExperience|CreateKeyValue|DeleteKeyValue|Sin|Cos|Tan))';",
            "const tokens = 'ABS ACCRINT ACCRINTM ACOS ACOSH ACOT ACOTH AGGREGATE ADDRESS AMORDEGRC AMORLINC AND ARABIC AREAS ASC ASIN ASINH ATAN ATAN2 ATANH AVEDEV AVERAGE AVERAGEA AVERAGEIF';",
            "const source = `// Shared filesystem + string helpers used across the converter modules.\\n// Pure functions only -- no process globals, no CLI parsing.\\nimport { existsSync, readFileSync } from 'node:fs';\\nimport { dirname, join } from 'node:path';`;",
            'const html = `<!DOCTYPE html><html><head><style>.at-a-glance { color: red; }</style></head><body><div class="at-a-glance">${value}</div><section>${other}</section></body></html>`;',
            "const prompt = `You are an expert reviewer of auto mode classifier rules for Claude Code.\\n\\nYour task is to critique the user's custom rules for clarity, completeness, and potential issues. Be concise and constructive. Only comment on rules that could be improved.`;",
        ]
    )

    candidates = extract_prompt_candidates(source)

    assert len(candidates) == 1
    assert "expert reviewer" in candidates[0].content


def test_known_catalog_matches_are_kept_before_strict_unknown_filtering():
    entry = next(item for item in load_catalog("claude-code") if item.id == "agent-auto-mode-rule-reviewer")
    source = f"const prompt = {' '.join(entry.anchors[:3])!r};"

    raw_matches = match_candidates("claude-code", extract_string_candidates(source, min_length=20))
    kept = _keep_known_or_prompt_like(raw_matches)

    assert len(kept) == 1
    assert kept[0].entry is not None
    assert kept[0].entry.id == entry.id


def test_catalog_matching_normalizes_template_variable_names():
    assert normalize_for_match("Use ${internalName} now") == normalize_for_match("Use ${} now")


def test_static_prompt_markdown_normalizes_template_variable_names():
    assert normalize_static_prompt_markdown_content("Use ${Yh} and ${zh} now") == "Use ${} and ${} now"


def test_static_prompt_markdown_collapses_duplicate_template_ternary():
    content = '${Xxo()?"Confirm first.":"Confirm first."} Then proceed.'

    assert normalize_static_prompt_markdown_content(content) == "Confirm first. Then proceed."


def test_static_prompt_markdown_preserves_template_ternary_branches_without_variable_noise():
    content = '${Xxo()?"Confirm first.":"Proceed without asking."} Then report.'

    assert (
        normalize_static_prompt_markdown_content(content)
        == '${? "Confirm first." : "Proceed without asking."} Then report.'
    )


def test_static_prompt_markdown_inlines_constant_string_substitutions():
    content = '${"Use the tools carefully."} Then stop.'

    assert normalize_static_prompt_markdown_content(content) == "Use the tools carefully. Then stop."


def test_static_prompt_markdown_normalizes_residual_ternary_prefix():
    content = "${Su()?````\ngit commit\n````:''}"

    assert normalize_static_prompt_markdown_content(content) == "${?````\ngit commit\n````:''}"


def test_static_prompt_markdown_normalizes_common_minified_iterator_names():
    content = "${B1r.join(`\nnext\n${c.map((R)=>`- ${}`).join(`\n`)} Use ${?jw:oR}."

    assert (
        normalize_static_prompt_markdown_content(content)
        == "${[].join(`\nnext\n${[].map(($)=>`- ${}`).join(`\n`)} Use ${?}."
    )


def test_unknown_static_prompt_title_uses_stable_normalized_hash():
    first = extract_string_candidates(
        "const prompt = 'You must write a concise plan for the user before editing files.';", min_length=20
    )[0]
    second = extract_string_candidates(
        "const prompt = 'You must write a concise plan for the user before editing files.';", min_length=20
    )[0]
    result = StaticPromptResult(
        agent_id="claude-code",
        version="1.2.3",
        source="node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        matches=(
            StaticPromptMatch(first, None, "unknown", "test"),
            StaticPromptMatch(second, None, "unknown", "test"),
        ),
    )

    markdown = render_static_prompts_markdown(result)

    assert "Unknown static prompt 1" not in markdown
    assert markdown.count("### Unknown static prompt ") == 2


def test_static_candidates_roundtrip(tmp_path):
    candidates = tuple(
        extract_string_candidates("const prompt = 'You must write a concise plan for the user.';", min_length=20)
    )
    result = StaticCandidatesResult(
        agent_id="claude-code",
        version="1.2.3",
        source="node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        extractor="test",
        min_length=20,
        candidates=candidates,
    )
    path = tmp_path / "static-candidates.json"

    write_static_candidates(path, result)
    loaded = read_static_candidates(path)

    assert loaded.agent_id == result.agent_id
    assert loaded.version == result.version
    assert loaded.source == result.source
    assert loaded.candidates == result.candidates
