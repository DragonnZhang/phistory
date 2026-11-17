"""Tool registry and implementations.

Every tool returns a ToolResult carrying, besides the text the model sees:

  resource_key -- a normalised identity for the thing observed ("file:c:/a/b.py").
                  Two results with the same key observe the same object, which is
                  what lets the supersession pass know a later read obsoletes an
                  earlier one.
  mutates      -- whether this call *changed* that resource. A mutation between
                  two observations is the signal that promotes "probably
                  redundant" to "definitively obsolete".

Access is confined to the session's configured working directories.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from . import readers
from .observations import observation, version
from .readers import fold as foldmod, folder, llm as readers_llm

MAX_TOOL_CHARS = 200_000


#: How much of a resource an observation captured. Supersession may only
#: collapse an older observation when a later one is at least as detailed --
#: otherwise a level-0 outline would "supersede", and destroy, a full read of
#: the same file. Full reads sit above every outline level by construction.
DETAIL_FULL = 100

#: Observation channels. `detail` is only comparable WITHIN a channel:
#: a structural outline and an LLM summary of the same file say different
#: kinds of thing, so neither may supersede the other however deep it is.
CH_RAW = "raw"            # full content
CH_OUTLINE = "outline"    # deterministic structure
CH_SEMANTIC = "semantic"  # model-generated meaning


@dataclass
class ToolResult:
    text: str
    resource_key: str | None = None
    mutates: bool = False
    error: bool = False
    detail: int = DETAIL_FULL
    channel: str = CH_RAW
    meta: dict[str, Any] = field(default_factory=dict)
    observation: dict[str, Any] = field(default_factory=dict)
    images: list[dict[str, Any]] = field(default_factory=list)
    pinned: bool = False


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

def _norm(p: str | Path) -> str:
    return os.path.normcase(str(Path(p).resolve())).replace("\\", "/")


class Sandbox:
    def __init__(self, roots: list[str]):
        live = [r for r in roots if r and Path(r).exists()]
        # Comparison uses normcase (Windows paths are case-insensitive), but
        # display and joining use the original casing -- the model sees these
        # paths and echoes them back, so they should look like the real thing.
        self.display_roots = [str(Path(r).resolve()) for r in live]
        self.roots = [_norm(r) for r in self.display_roots]

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        if not self.roots:
            raise ToolError(
                "No working directory is configured for this chat. "
                "Add one in the sidebar before using file tools."
            )
        p = Path(path)
        if not p.is_absolute():
            p = Path(self.display_roots[0]) / p
        full = _norm(p)
        ok = any(full == r or full.startswith(r.rstrip("/") + "/") for r in self.roots)
        if not ok:
            raise ToolError(
                f"Path is outside the allowed working directories: {path}\n"
                f"Allowed: {', '.join(self.roots)}"
            )
        rp = Path(str(Path(p).resolve()))
        if must_exist and not rp.exists():
            raise ToolError(f"Not found: {rp}")
        return rp

    def key(self, path: Path) -> str:
        return f"file:{_norm(path)}"


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

async def t_read_file(sb: Sandbox, path: str, offset: int = 1, limit: int = 2000) -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if p.is_dir():
        raise ToolError(f"{p} is a directory; use list_dir.")
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        raise ToolError(f"Could not read {p}: {e}")
    lines = text.splitlines()
    offset = max(1, int(offset))
    limit = max(1, int(limit))
    chunk = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(f"{i + offset:6d}\t{ln}" for i, ln in enumerate(chunk))
    header = f"{p}  ({len(lines)} lines"
    if offset > 1 or offset - 1 + limit < len(lines):
        header += f", showing {offset}-{min(len(lines), offset - 1 + limit)}"
    header += ")"
    return ToolResult(
        text=f"{header}\n{numbered}"[:MAX_TOOL_CHARS],
        resource_key=sb.key(p),
        meta={"lines": len(lines)},
        observation={"version": hashlib.sha256(raw).hexdigest(), "representation": "source-lines",
            "coverage": {"kind": "lines", "start": offset, "end": offset + len(chunk) - 1},
            "complete": len(header) + len(numbered) + 1 <= MAX_TOOL_CHARS},
    )


def _detect_newline(raw: bytes) -> str:
    """The file's line ending: CRLF only if every LF in it is part of a CRLF."""
    lf = raw.count(b"\n")
    return "\r\n" if lf and raw.count(b"\r\n") == lf else "\n"


def _write_preserving(p: Path, text: str, newline: str, bom: bool = False) -> None:
    """Write `text` (LF-normalised in memory) with an explicit line ending.

    Path.write_text on Windows translates every '\\n' to CRLF, which silently
    converted LF repositories to CRLF on each edit (and broke tests that search
    source text for '\\n}\\n'). Bytes are written exactly; nothing is translated.
    """
    body = text.replace("\r\n", "\n")
    if newline != "\n":
        body = body.replace("\n", newline)
    raw = body.encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    with open(p, "wb") as fh:
        fh.write(raw)


async def t_write_file(sb: Sandbox, path: str, content: str) -> ToolResult:
    p = sb.resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    # Keep an existing file's line ending (and BOM); a new file gets what the
    # content itself uses (LF unless the content is CRLF throughout).
    if existed:
        old = p.read_bytes()
        nl, bom = _detect_newline(old), old.startswith(b"\xef\xbb\xbf")
    else:
        nl, bom = _detect_newline(content.encode("utf-8")), False
    _write_preserving(p, content, nl, bom)
    n = content.count("\n") + 1
    return ToolResult(
        text=f"{'Overwrote' if existed else 'Created'} {p} ({n} lines, {len(content)} chars).",
        resource_key=sb.key(p),
        mutates=True,
    )


async def t_edit_file(
    sb: Sandbox, path: str, old_string: str, new_string: str, replace_all: bool = False
) -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if not old_string:
        raise ToolError("old_string must not be empty.")
    raw = p.read_bytes()
    newline = _detect_newline(raw)
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig" if bom else "utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise ToolError(f"{p} is not valid UTF-8 ({e}); edit it another way.")
    # Match and replace in LF space whatever the file uses on disk, then write back with
    # the file's own line ending (see _write_preserving).
    text = text.replace("\r\n", "\n")
    old_string = old_string.replace("\r\n", "\n")
    new_string = new_string.replace("\r\n", "\n")
    count = text.count(old_string)
    if count == 0:
        raise ToolError(
            f"old_string not found in {p}. Read the file again — it may have changed."
        )
    if count > 1 and not replace_all:
        raise ToolError(
            f"old_string occurs {count} times in {p}. Make it unique or set replace_all=true."
        )
    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    _write_preserving(p, new_text, newline, bom)
    return ToolResult(
        text=f"Edited {p}: {count if replace_all else 1} replacement(s).",
        resource_key=sb.key(p),
        mutates=True,
    )


async def t_list_dir(sb: Sandbox, path: str = ".") -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if not p.is_dir():
        raise ToolError(f"{p} is not a directory.")
    rows = []
    for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.name.startswith(".") and entry.name not in (".env", ".gitignore"):
            continue
        if entry.is_dir():
            rows.append(f"  {entry.name}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            rows.append(f"  {entry.name}  ({size} B)")
    return ToolResult(
        text=f"{p}\n" + ("\n".join(rows) if rows else "  (empty)"),
        resource_key=f"dir:{_norm(p)}",
    )


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build", ".ctxagent", ".ctxagent-build"}


def _walk(root: Path, max_files: int = 20000, *, include_dirs: bool = False):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
                      and not (Path(dirpath) / d).is_symlink()
                      and not getattr(Path(dirpath) / d, "is_junction", lambda: False)()]
        if include_dirs:
            for dn in dirnames:
                yield Path(dirpath) / dn
        for fn in filenames:
            if (Path(dirpath) / fn).is_symlink():
                continue
            yield Path(dirpath) / fn
            max_files -= 1
            if max_files <= 0:
                return


async def t_glob_search(sb: Sandbox, pattern: str, path: str = ".") -> ToolResult:
    from . import search
    p = sb.resolve(path, must_exist=True)
    hits = []
    pat = pattern.replace("\\", "/")
    # Directories are matched too: a pattern like "**/readers" is asking where a
    # package lives, and returning nothing sends the agent on a grep hunt.
    fs = await asyncio.to_thread(search.list_files, p, include_dirs=True, use_git=False)
    gf = search.GlobFilter(pat, p)
    for f in fs.files:
        rel = str(f.relative_to(p)).replace("\\", "/")
        if gf(f) or fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("/" + rel, pat):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                mtime = 0.0
            hits.append((mtime, str(f) + ("/" if f.is_dir() else "")))
    hits.sort(reverse=True)
    body = "\n".join(h[1] for h in hits[:500]) or "(no matches)"
    more = f"\n… {len(hits) - 500} more" if len(hits) > 500 else ""
    notes = ""
    if fs.pruned_dirs:
        names = sorted(fs.pruned_dirs)
        notes += ("\n(not searched below root: " + ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
                  + " — pass one as `path` to search it)")
    if fs.truncated:
        notes += f"\n⚠ INCOMPLETE file list ({fs.limit_note}): narrow `path`"
    return ToolResult(
        text=f"glob {pattern!r} in {p} — {len(hits)} match(es){notes}\n{body}{more}",
        resource_key=f"glob:{_norm(p)}:{pattern}",
    )


async def t_grep_search(
    sb: Sandbox, pattern: str, path: str = ".", glob: str = "*", max_results: int = 200,
    context: int = 0, case_insensitive: bool = False, literal: bool = False,
    mode: str = "content",
) -> ToolResult:
    from . import search
    p = sb.resolve(path, must_exist=True)
    if literal:
        pattern_rx = re.escape(pattern)
    else:
        pattern_rx = pattern
    try:
        rx = re.compile(pattern_rx, re.IGNORECASE if case_insensitive else 0)
    except re.error as e:
        raise ToolError(f"Bad regex: {e}. Set literal=true to search the text as-is.")
    mode = (mode or "content").lower()
    if mode not in ("content", "files", "count"):
        raise ToolError("mode must be 'content', 'files' or 'count'")
    max_results = max(1, min(5000, int(max_results)))
    context = max(0, min(20, int(context)))
    r = await asyncio.to_thread(
        search.grep, p, rx, glob=glob or "*", max_results=max_results, context=context,
        mode=mode, resolve=lambda f: sb.resolve(str(f), must_exist=True))
    text = search.format_grep(pattern, p, r, mode=mode, max_results=max_results)
    return ToolResult(
        text=text[:MAX_TOOL_CHARS],
        resource_key=f"grep:{_norm(p)}:{pattern}:{glob}:{mode}:{context}:{int(case_insensitive)}",
    )


async def t_run_command(
    sb: Sandbox, command: str, cwd: str = ".", timeout: int = 120
) -> ToolResult:
    wd = sb.resolve(cwd, must_exist=True)
    if not wd.is_dir():
        wd = wd.parent
    if sys.platform == "win32":
        shell_exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        args = [shell_exe, "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        args = ["/bin/bash", "-lc", command]

    def _run() -> tuple[int, str, str]:
        proc = subprocess.run(
            args,
            cwd=str(wd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    try:
        rc, so, se = await asyncio.get_running_loop().run_in_executor(None, _run)
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out after {timeout}s: {command}")
    # Arguments already contain the exact command. Do not echo a multi-kB
    # inline script into context a second time merely to label its output.
    preview = command if len(command) <= 240 else command[:180] + '\n[command preview shortened; full command is in the tool call]'
    parts = [f"$ {preview}   (cwd={wd}, exit={rc})"]
    if so.strip():
        parts.append(so.rstrip())
    if se.strip():
        parts.append("--- stderr ---\n" + se.rstrip())
    if not so.strip() and not se.strip():
        parts.append("(no output)")
    return ToolResult(
        text="\n".join(parts)[:MAX_TOOL_CHARS],
        # A shell command can change anything; key it to the cwd so repeated
        # identical invocations still dedup, but do not claim it supersedes reads.
        resource_key=f"cmd:{_norm(wd)}:{command.strip()[:120]}",
        mutates=False,
        error=rc != 0,
        meta={"exit": rc},
    )


async def t_fetch_url(sb: Sandbox, url: str, max_chars: int = 40000) -> ToolResult:
    from . import browsing
    if not 1 <= max_chars <= 40000:
        raise ToolError('max_chars must be 1..40000; use web_open for paged browsing')
    receipt = await browsing._get(url)
    data = await asyncio.to_thread(browsing._extract, receipt['raw'], receipt['url'], receipt['content_type'], receipt['encoding'])
    suffix = '\n[More text available: use web_open for a cached paged snapshot.]' if len(data['text']) > max_chars else ''
    return ToolResult(text=f"{receipt['url']}\n\n{data['text'][:max_chars]}" + suffix,
                      resource_key='url:' + receipt['url'])


# recall is special: it needs the conversation, so the agent injects it.
async def t_recall(sb: Sandbox, segment_id: str) -> ToolResult:  # pragma: no cover
    raise ToolError("recall is handled by the agent loop")


# compact_context too (an agent-directed deep compaction; compaction/manual.py).
async def t_compact_context(sb: Sandbox, summary: str = "", keep_from: str = "last_user",
                            keep_ids=None, dry_run: bool = False,
                            index: bool = True) -> ToolResult:  # pragma: no cover
    raise ToolError("compact_context is handled by the agent loop")


# curate_context likewise (agent-curated incremental review; compaction/curate.py).
async def t_curate_context(sb: Sandbox, drop=None, rest: str = "keep", commit: bool = True,
                           dry_run: bool = False, min_tokens: int = 0) -> ToolResult:  # pragma: no cover
    raise ToolError("curate_context is handled by the agent loop")


# pin likewise operates on the conversation.
async def t_pin(sb: Sandbox, segment_ids=None, batches: int = 0, unpin: bool = False,
                list_pins: bool = False) -> ToolResult:  # pragma: no cover
    raise ToolError("pin is handled by the agent loop")


# ---------------------------------------------------------------------------
# Document reading and summarisation
# ---------------------------------------------------------------------------

async def t_read_document(
    sb: Sandbox, path: str, offset: int = 1, limit: int = 3000
) -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if p.is_dir():
        raise ToolError(f"{p} is a directory; use list_dir or summarize_folder.")
    source_version = version(p)
    kind = readers.detect_kind(p)
    try:
        text = readers.extract_text(p, kind)
    except Exception as e:
        raise ToolError(f"Could not extract text from {p} ({kind}): {type(e).__name__}: {e}")
    lines = text.splitlines()
    offset = max(1, int(offset))
    limit = max(1, int(limit))
    chunk = lines[offset - 1: offset - 1 + limit]
    shown = f", showing lines {offset}-{min(len(lines), offset - 1 + limit)}" \
        if (offset > 1 or offset - 1 + limit < len(lines)) else ""
    header = f"{p}  [{kind}, {len(lines)} lines extracted{shown}]"
    body = "\n".join(chunk)
    if offset - 1 + limit < len(lines):
        body += (f"\n\n[{len(lines) - (offset - 1 + limit)} more lines — "
                 f"call again with offset={offset + limit}, or use "
                 f"summarize_file for an overview]")
    return ToolResult(
        text=f"{header}\n{body}"[:MAX_TOOL_CHARS],
        resource_key=sb.key(p),
        detail=DETAIL_FULL,
        meta={"kind": kind, "lines": len(lines)},
        observation=observation(p, "extracted-lines",
            {"kind": "lines", "start": offset, "end": offset + len(chunk) - 1},
            complete=len(header) + len(body) + 1 <= MAX_TOOL_CHARS, source_version=source_version),
    )


async def t_summarize_file(
    sb: Sandbox, path: str, level: int = 2, mode: str = "static", ctx=None
) -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if p.is_dir():
        raise ToolError(f"{p} is a directory; use summarize_folder.")
    level = max(0, min(readers.MAX_LEVEL, int(level)))
    mode = (mode or "static").lower().strip()
    source_version = version(p)
    kind = readers.detect_kind(p)

    if mode in ("static", "structure", "outline"):
        try:
            text = readers.outline(p, level, kind)
        except Exception as e:
            raise ToolError(f"Could not outline {p} ({kind}): {type(e).__name__}: {e}")
        return ToolResult(
            text=text[:MAX_TOOL_CHARS],
            resource_key=sb.key(p),
            detail=level,
            channel=CH_OUTLINE,
            meta={"kind": kind, "level": level, "mode": "static"},
            observation=observation(p, "static-outline", {"kind": "whole"},
                                    complete=len(text) <= MAX_TOOL_CHARS, source_version=source_version),
        )

    if mode in ("llm", "semantic", "generated"):
        if ctx is None or not ctx.get("complete_fn"):
            raise ToolError("LLM summaries are unavailable in this context.")
        try:
            text, meta = await readers_llm.summarize(
                p, level, ctx.get("summary_model"), ctx["complete_fn"], kind
            )
        except Exception as e:
            raise ToolError(f"LLM summary failed for {p}: {type(e).__name__}: {e}")
        note = (f"[LLM summary of {p.name} — kind={meta['kind']}, level={meta['level']}, "
                f"model={ctx.get('summary_model')}"
                + (", source truncated" if meta["truncated"] else "") + "]")
        return ToolResult(
            text=f"{note}\n{text}"[:MAX_TOOL_CHARS],
            resource_key=sb.key(p),
            detail=min(level, 8),
            channel=CH_SEMANTIC,
            meta={**meta, "mode": "llm"},
        )

    raise ToolError(f"mode must be 'static' or 'llm', got {mode!r}")


async def t_focus_file(
    sb: Sandbox, path: str, select, level: int = 1, max_tokens: int = 24000
) -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if p.is_dir():
        raise ToolError(f"{p} is a directory; use summarize_folder.")
    if isinstance(select, str):
        selectors = [select]
    elif isinstance(select, (list, tuple)):
        selectors = [str(x) for x in select if str(x).strip()]
    else:
        raise ToolError("select must be a string or a list of strings.")
    if not selectors:
        raise ToolError("select must name at least one part of the document.")
    source_version = version(p)
    kind = readers.detect_kind(p)
    try:
        text = foldmod.fold(p, selectors, level=max(0, min(4, int(level))),
                            kind=kind, max_tokens=max(500, int(max_tokens)))
    except Exception as e:
        raise ToolError(f"Could not fold {p}: {type(e).__name__}: {e}")
    return ToolResult(
        text=text[:MAX_TOOL_CHARS],
        resource_key=sb.key(p),
        # A folded view shows part of the file in full but not the rest, so it
        # must never supersede a full read -- nor be superseded by one, since it
        # carries structure a flat read does not.
        detail=level,
        channel=CH_OUTLINE,
        meta={"kind": kind, "selectors": selectors, "mode": "fold"},
        observation=observation(p, "fold", {"kind": "selectors", "values": selectors},
            complete=len(text) <= MAX_TOOL_CHARS and "NO MATCH" not in text
                     and "TRUNCATED" not in text and "nodes unfolded;" in text,
            source_version=source_version),
    )


async def t_summarize_folder(
    sb: Sandbox, path: str = ".", level: int = 2, token_budget: int = 0
) -> ToolResult:
    p = sb.resolve(path, must_exist=True)
    if not p.is_dir():
        raise ToolError(f"{p} is not a directory; use summarize_file.")
    level = max(0, min(readers.MAX_LEVEL + 1, int(level)))
    budget = int(token_budget) or folder.default_budget(level)
    try:
        text = folder.overview(p, level, token_budget=max(1000, budget))
    except Exception as e:
        raise ToolError(f"Could not survey {p}: {type(e).__name__}: {e}")
    return ToolResult(
        text=text[:MAX_TOOL_CHARS],
        resource_key=f"folder:{_norm(p)}:L{level}",
        detail=level,
        channel=CH_OUTLINE,
        meta={"level": level},
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    group: str = "general"
    dangerous: bool = False
    #: receives a `ctx` kwarg carrying {complete_fn, summary_model} -- for tools
    #: that need to call a model themselves (the LLM summariser).
    needs_ctx: bool = False


def _p(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


REGISTRY: dict[str, ToolSpec] = {}

#: Shared description of what `level` means per format. Kept in one place so the
#: tool schema the model sees always matches what the readers actually do.
LEVELS_DOC = (
    "LEVEL MEANS TWO DIFFERENT THINGS, by mode. "
    "mode='static' -> a STRUCTURAL level (0-5), not a size limit: "
    "0 barest skeleton, each step adds one layer, 5 is the document's full text. "
    "Per format: code -> class names, then function names, then full signatures, "
    "then docstrings; markdown/rst/html/latex -> one more heading depth each "
    "step; csv/xlsx/parquet -> dtypes and row labels, then per-column statistics, "
    "then sample rows; pdf -> bookmark tree, then first lines of each page; "
    "latex -> sections, then figures/tables with captions and equation labels, "
    "then the opening sentence of every paragraph; json/yaml/xml -> one more "
    "nesting depth. Static outlines are free and instant, so pick the level by "
    "the detail you need, not by size. "
    "mode='llm' -> a TOKEN BUDGET for the generated summary: "
    "0=100, 1=300, 2=1000, 3=3000, 4=10000, 5=uncapped. "
    "Start at level 1 or 2; raise it only if the answer is not there."
)


def _reg(spec: ToolSpec) -> None:
    REGISTRY[spec.name] = spec


_reg(ToolSpec(
    "read_file",
    "Read a text file. Returns line-numbered content. Use offset/limit for large files.",
    _p({
        "path": {"type": "string", "description": "Absolute path, or relative to the working directory."},
        "offset": {"type": "integer", "description": "First line to read (1-based). Default 1."},
        "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
    }, ["path"]),
    t_read_file, "files",
))

_reg(ToolSpec(
    "write_file",
    "Write a file, creating or overwriting it. Provide the complete content.",
    _p({
        "path": {"type": "string"},
        "content": {"type": "string"},
    }, ["path", "content"]),
    t_write_file, "files", dangerous=True,
))

_reg(ToolSpec(
    "edit_file",
    "Replace an exact string in a file. old_string must be unique unless replace_all is true.",
    _p({
        "path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
        "replace_all": {"type": "boolean"},
    }, ["path", "old_string", "new_string"]),
    t_edit_file, "files", dangerous=True,
))

_reg(ToolSpec(
    "list_dir",
    "List the entries of a directory.",
    _p({"path": {"type": "string"}}, []),
    t_list_dir, "files",
))

_reg(ToolSpec(
    "glob_search",
    "Find files by glob pattern (e.g. '**/*.py'), newest first.",
    _p({"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
    t_glob_search, "search",
))

_reg(ToolSpec(
    "grep_search",
    "Search file contents with a Python regular expression (ripgrep-like). Returns "
    "path:line: match. No size cap: large files are streamed. Inside a git repo the "
    "file list honours .gitignore; dot-dirs below the root are skipped (pass one as "
    "path to search it). The header always states what was searched and warns when "
    "results are incomplete (max_results hit, time budget, skipped files), so "
    "'(no matches)' without a warning is a real negative.",
    _p({
        "pattern": {"type": "string", "description": "Python regex (or plain text with literal=true)."},
        "path": {"type": "string", "description": "File or directory. Default: the working directory."},
        "glob": {"type": "string", "description": "File filter: '*.py', 'src/**/*.cpp', several separated by commas, '!*.min.js' excludes. No slash = file name."},
        "max_results": {"type": "integer", "description": "Max matching lines (content) or files (files/count). Default 200."},
        "context": {"type": "integer", "description": "Lines of context before/after each match (0-20), like grep -C."},
        "case_insensitive": {"type": "boolean"},
        "literal": {"type": "boolean", "description": "Treat pattern as plain text, not a regex."},
        "mode": {"type": "string", "enum": ["content", "files", "count"], "description": "content = matching lines (default); files = paths only; count = hits per file."},
    }, ["pattern"]),
    t_grep_search, "search",
))

_reg(ToolSpec(
    "run_command",
    "Run a shell command (PowerShell on Windows, bash elsewhere) in a working directory.",
    _p({
        "command": {"type": "string"},
        "cwd": {"type": "string"},
        "timeout": {"type": "integer"},
    }, ["command"]),
    t_run_command, "shell", dangerous=True,
))

_reg(ToolSpec(
    "fetch_url",
    "Fetch a web page and return its text content.",
    _p({"url": {"type": "string"}, "max_chars": {"type": "integer"}}, ["url"]),
    t_fetch_url, "web",
))

_reg(ToolSpec(
    "recall",
    "Restore the full original content of an earlier tool result that context "
    "compaction shortened. Pass the segment_id shown in the placeholder. The "
    "restored content is protected from incremental compaction. Deep compaction may "
    "archive it again; the same segment_id remains recallable.",
    _p({"segment_id": {"type": "string"}}, ["segment_id"]),
    t_recall, "context",
))

_reg(ToolSpec(
    "pin",
    "Keep tool results verbatim: a pinned result is never masked, offloaded or "
    "condensed and survives deep compaction. Pass the segment ids of earlier tool "
    "results ('last' = the previous tool batch's results). batches>0 makes the pin "
    "expire after that many further tool batches; 0 = until unpinned. A shortened "
    "result is restored to its full original when pinned. unpin=true releases; "
    "list_pins=true lists current pins with their token cost. Pin sparingly: pins "
    "count against the context window.",
    _p({
        "segment_ids": {"type": "array", "items": {"type": "string"},
                        "description": "Segment ids (see recall placeholders / context_report), or ['last']."},
        "batches": {"type": "integer", "description": "Expire after N more tool batches (0 = no expiry)."},
        "unpin": {"type": "boolean"},
        "list_pins": {"type": "boolean"},
    }, []),
    t_pin, "context",
))

_reg(ToolSpec(
    "compact_context",
    "Deliberate deep compaction, under YOUR control - rare, at topic boundaries. Everything between "
    "the system prompt and `keep_from` is archived (still recallable by id) and replaced by YOUR "
    "`summary`: the handoff you would give a colleague taking over (goal, decisions and why, files "
    "changed, results/numbers, open issues, ids worth recalling). Use it when a task is finished and "
    "the conversation moves on: every later request then stops paying cache reads for the old work. "
    "keep_from: 'last_user' (default: keep the newest user message and all work since), "
    "'current_batch', or a segment id. keep_ids: tool results (with their call) to keep verbatim; "
    "manual pins are kept too. An index of the archived results is appended unless index=false. "
    "dry_run=true reports tokens saved and the cache break-even (requests until the one-time cache "
    "rewrite pays off) without changing anything.",
    _p({
        "summary": {"type": "string", "description": "Your handoff text replacing the compacted part (>= 200 chars)."},
        "keep_from": {"type": "string", "description": "'last_user' (default), 'current_batch', or a segment id: kept verbatim from here on."},
        "keep_ids": {"type": "array", "items": {"type": "string"}, "description": "Segment ids before the cut to keep verbatim."},
        "dry_run": {"type": "boolean"},
        "index": {"type": "boolean", "description": "Append an index of archived tool results (default true)."},
    }, []),
    t_compact_context, "context",
))

_reg(ToolSpec(
    "curate_context",
    "Incremental context review under YOUR control (the light-weight sibling of compact_context). "
    "Without `drop` it LISTS the unfrozen tool results you have already seen: id, tool, tokens, call "
    "args, first line. With `drop` it replaces the named results by a stub carrying YOUR note (the "
    "takeaway, e.g. 'BUILD_OK, 0 warnings' or 'Foo::bar defined at x.cpp:120') - the full text stays "
    "recallable. Results you do not name are kept verbatim (rest='keep'); rest='auto' additionally "
    "applies age-based masking to the remaining unpinned ones. drop=[] means 'reviewed, keep all'. "
    "commit=true (default) freezes the reviewed older region (cache-stable) and resets the review "
    "counter. Use it when the harness hints a review is due, or after a subtask whose bulky output "
    "(logs, big reads, searches) is no longer needed. Dropping invalidates the cache from that "
    "point: dry_run=true reports the break-even. Prefer dropping large, old, dead results.",
    _p({
        "drop": {"type": "array", "items": {"anyOf": [
            {"type": "string", "description": "segment id, optionally 'id: note'"},
            {"type": "object", "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
             "required": ["id"]}]},
            "description": "Results to drop; the note (<=400 chars) becomes the visible stub."},
        "rest": {"type": "string", "enum": ["keep", "auto"]},
        "commit": {"type": "boolean"},
        "dry_run": {"type": "boolean"},
        "min_tokens": {"type": "integer", "description": "Listing: hide results smaller than this."},
    }, []),
    t_curate_context, "context",
))

_reg(ToolSpec(
    "read_document",
    "Extract the full readable text of any supported document: PDF, DOCX, PPTX, "
    "Jupyter notebook, CSV/TSV/Excel/Parquet, HTML, JSON/YAML/TOML/XML, or plain "
    "text and source files. Use this instead of read_file for non-plain-text "
    "formats. For a large document, prefer summarize_file first.",
    _p({
        "path": {"type": "string"},
        "offset": {"type": "integer", "description": "First extracted line to return (1-based)."},
        "limit": {"type": "integer", "description": "Max lines to return. Default 3000."},
    }, ["path"]),
    t_read_document, "documents",
))

_reg(ToolSpec(
    "summarize_file",
    "Summarise ONE file at a chosen level of detail, without reading all of it. "
    "mode='static' is free, instant and cannot hallucinate: it reports the real "
    "structure of the file (class/function signatures for code, headings for "
    "markdown, columns and dtypes for tables, bookmarks for PDFs). "
    "mode='llm' asks a small model what the document actually means and says. "
    "Start here for a large or unfamiliar file; a file you can read in one read_file call needs no outline. " + LEVELS_DOC,
    _p({
        "path": {"type": "string"},
        "level": {"type": "integer", "minimum": 0, "maximum": 5,
                  "description": "For mode='static': structural depth 0-5 "
                                 "(5 = full text). For mode='llm': token budget "
                                 "0=100, 1=300, 2=1000, 3=3000, 4=10000, "
                                 "5=uncapped. Default 2."},
        "mode": {"type": "string", "enum": ["static", "llm"],
                 "description": "'static' = structural outline (free, instant, cannot "
                                "hallucinate). 'llm' = generated prose summary of what "
                                "the document means."},
    }, ["path"]),
    t_summarize_file, "documents", needs_ctx=True,
))

_reg(ToolSpec(
    "focus_file",
    "Read ONE part of a document in full, embedded in a collapsed outline of the "
    "rest — a folded view, like a JSON viewer with one key open or a file browser "
    "with one folder expanded. Use this instead of read_file whenever you want a "
    "specific function, section, key, column or page out of a large file: you get "
    "the exact content plus the surrounding structure, at a fraction of the tokens. "
    "SELECTORS: a name (`run`), a dotted path (`CompactionEngine.run`, "
    "`server.tls`), a regex between slashes (`/^_snap/`), or a raw line range "
    "(`L120-160`, works on any file). Pass several to open several parts. "
    "If nothing matches, the reply lists the addressable names — retry with one.",
    _p({
        "path": {"type": "string"},
        "select": {
            "anyOf": [{"type": "string"},
                      {"type": "array", "items": {"type": "string"}}],
            "description": "What to unfold: name, dotted path, /regex/, or Lstart-end.",
        },
        "level": {"type": "integer", "minimum": 0, "maximum": 4,
                  "description": "Detail of the COLLAPSED lines: 0 labels only, "
                                 "1 adds line spans and sizes. Default 1."},
        "max_tokens": {"type": "integer",
                       "description": "Cap on unfolded content. Default 24000."},
    }, ["path", "select"]),
    t_focus_file, "documents",
))

_reg(ToolSpec(
    "summarize_folder",
    "Survey a directory tree at a chosen level of detail. level=0 is a plain "
    "listing. At level N each FILE is outlined at level N-1, and each SUBFOLDER "
    "is surveyed at level N-2 — so detail decays with depth and the output stays "
    "bounded. Use this once to orient yourself in an unfamiliar project "
    "instead of many list_dir/read_file round trips.",
    _p({
        "path": {"type": "string"},
        "level": {"type": "integer", "minimum": 0, "maximum": 6,
                  "description": "0 = listing only. At level N each file gets a "
                                 "static outline at level N-1 and each subfolder "
                                 "is surveyed at N-2. Default 2."},
        "token_budget": {"type": "integer",
                         "description": "Hard cap on output size. Default 12000."},
    }, []),
    t_summarize_folder, "documents",
))


async def t_context_report(sb: Sandbox, top: int = 5, recent: int = 20, ctx=None) -> ToolResult:
    from .context_report import report
    import json
    session = ctx['session']
    return ToolResult(text=json.dumps(report(session.conv, session.cfg, session.usage,
                                            top=top, recent=recent), ensure_ascii=True))


_reg(ToolSpec(
    'context_report',
    'Inspect this chat\'s context sizes, largest tools/segments, recorded cache hits/costs and inactive settings. '
    'Read-only, no API calls or message bodies. Local tokens are estimates. Defaults: top=5, recent=20 agent requests.',
    _p({'top': {'type': 'integer', 'minimum': 1, 'maximum': 10},
        'recent': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, []),
    t_context_report, 'context', needs_ctx=True,
))


DEFAULT_TOOLS = [
    "read_file", "write_file", "edit_file", "list_dir",
    "glob_search", "grep_search", "run_command", "recall", "pin", "compact_context",
    "curate_context", "read_document", "summarize_file", "summarize_folder", "focus_file",
    "view_image", "render_pdf", "start_job", "job_status", "job_wait", "list_jobs",
    "read_job_log", "cancel_job", "context_report",
    "document_open", "document_close", "document_list",
    "document_prepare_edits", "document_json_update", "document_table_update", "document_apply_edits",
]


def openai_schema(names: list[str]) -> list[dict[str, Any]]:
    out = []
    for n in names:
        spec = REGISTRY.get(n)
        if not spec:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        })
    return out


def catalogue() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "description": s.description,
            "group": s.group,
            "dangerous": s.dangerous,
            "needs_ctx": s.needs_ctx,
        }
        for s in REGISTRY.values()
    ]


# Adapters are separate; services do not import the registry.
from .productivity_tools import register as _register_productivity
_register_productivity(REGISTRY, ToolSpec, ToolResult, ToolError, _p)

from .workflow_tools import register as _register_workflows
_register_workflows(REGISTRY, ToolSpec, ToolResult, _p)

from .research_tools import register as _register_research
_register_research(REGISTRY, ToolSpec, ToolResult, _p)

from .workspace_tools import register as _register_workspace
_register_workspace()

from .document_format_tools import register as _register_document_formats
_register_document_formats(REGISTRY, ToolSpec, ToolResult, _p)
from .document_formats import NAMES as _document_operations
DEFAULT_TOOLS.extend(n for n in _document_operations if n not in DEFAULT_TOOLS)

from .agent_tools import register as _register_agents, AGENT_TOOLS, WEB_TOOLS
_register_agents(REGISTRY, ToolSpec, ToolResult, _p)
DEFAULT_TOOLS.extend(AGENT_TOOLS + WEB_TOOLS)

from .browser_tools import register as _register_browser, BROWSER_TOOLS
_register_browser(REGISTRY, ToolSpec, _p)
DEFAULT_TOOLS.extend(BROWSER_TOOLS)
