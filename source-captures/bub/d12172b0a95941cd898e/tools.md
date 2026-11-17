# Bub — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/bub/builtin/tools.py

Source: https://github.com/bubbuild/bub/blob/d14de719da57640cf2f80658217d94d94a5b7c42/src/bub/builtin/tools.py

SHA-256: `86b72caed8c643ca9f225b401f1d9e45a90c86ecb6567dc54cfe86f3eb61643f`

```
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, TypedDict, cast, final

import republic
from pydantic import BaseModel, Field

from bub.builtin.environment import environment_from_state
from bub.builtin.settings import load_settings, set_session_setting
from bub.builtin.shell_manager import shell_manager
from bub.builtin.subagents import SUBAGENT_TOOLS, subagent_manager
from bub.builtin.subagents import SubAgentInput as SubAgentInput
from bub.builtin.subagents import SubAgentResult as SubAgentResult
from bub.builtin.subagents import run_subagent as run_subagent
from bub.core.tools import Tool, ToolContext, content_result, tool
from bub.skills import discover_skills

if TYPE_CHECKING:
    from bub.builtin.agent import BubAgent
    from bub.core.tape import Tape

DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_HEADERS = {"accept": "text/markdown"}
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10
TOOL_DESCRIBE_TOOL_NAME = "tool.describe"
TOOL_LOADED_EVENT = "tool.loaded"
ALLOWED_TOOLS_STATE_KEY = "_runtime_allowed_tools"
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _to_model_name(name: str) -> str:
    return name.replace(".", "_")


def _tool_name_index(all_names: Iterable[str]) -> dict[str, str]:
    names = tuple(all_names)
    real_names = {tool_name.casefold(): tool_name for tool_name in names}
    alias_names = {_to_model_name(tool_name).casefold(): tool_name for tool_name in names}
    return {**alias_names, **real_names}


def _resolve_explicit_tool_names(names: Iterable[str], index: dict[str, str]) -> tuple[set[str], set[str]]:
    resolved: set[str] = set()
    unknown: set[str] = set()
    for name in names:
        normalized_name = name.strip()
        if resolved_name := index.get(normalized_name.casefold()):
            resolved.add(resolved_name)
        else:
            unknown.add(normalized_name)
    return resolved, unknown


def _raise_unknown_tool_names(names: set[str]) -> None:
    formatted = ", ".join(sorted(repr(name) for name in names))
    raise ValueError(f"unknown tool name(s): {formatted}")


def resolve_tool_names(
    names: Iterable[str] | None = None, *, exclude: Iterable[str] = (), all_names: Iterable[str]
) -> set[str]:
    """Resolve tool names in ``all_names`` from either runtime names or model-facing aliases."""
    available = tuple(all_names)
    index = _tool_name_index(available)
    excluded, unknown_excluded = _resolve_explicit_tool_names(exclude, index)
    if unknown_excluded:
        _raise_unknown_tool_names(unknown_excluded)
    if names is None:
        return set(available) - excluded

    resolved, unknown = _resolve_explicit_tool_names(names, index)
    if unknown:
        _raise_unknown_tool_names(unknown)
    return resolved - excluded


def _tool_signature(tool_item: Tool) -> str:
    properties = tool_item.parameters.get("properties", {})
    if not isinstance(properties, dict) or not properties:
        return f"{_to_model_name(tool_item.name)}()"

    required = tool_item.parameters.get("required", [])
    required_names = set(required) if isinstance(required, list) else set()
    params = [name if name in required_names else f"{name}?" for name in properties]
    return f"{_to_model_name(tool_item.name)}({', '.join(params)})"


def render_tools_prompt(tools: Iterable[Tool]) -> str:
    """Render a human-readable description of tools for builtin agent prompts."""
    agent_tools = [tool_item for tool_item in tools if tool_item.exposure in ("auto", "direct")]
    if not agent_tools:
        return ""
    lines = []
    for tool_item in agent_tools:
        line = f"- {_tool_signature(tool_item)}"
        if tool_item.description:
            line += f": {tool_item.description}"
        lines.append(line)
    return f"<available_tools>\n{'\n'.join(lines)}\n</available_tools>"


def render_deferred_tools_prompt(tools: Iterable[Tool]) -> str:
    """List deferred tools by model-facing name so the model knows what it can load."""
    names = "\n".join(f"- {_to_model_name(tool_item.name)}" for tool_item in tools)
    if not names:
        return ""
    return (
        "<deferred_tools>\n"
        f"These tools are loaded on demand: call {_to_model_name(TOOL_DESCRIBE_TOOL_NAME)} with their names to load "
        "their definitions. This list does not change as tools load. A tool stays loaded for the session once it "
        f"appears in your tool list or {_to_model_name(TOOL_DESCRIBE_TOOL_NAME)} has returned its definition; call it "
        "directly by name and do not load it again.\n"
        f"{names}\n</deferred_tools>"
    )


async def loaded_tool_names(tape: Tape) -> list[str]:
    """Return runtime names of tools loaded on this tape, in load order."""
    names: dict[str, None] = {}
    for entry in await tape.search(tape.query().kinds("event")):
        if entry.payload.get("name") != TOOL_LOADED_EVENT:
            continue
        data = entry.payload.get("data")
        if isinstance(data, Mapping):
            names.update(dict.fromkeys(str(name) for name in data.get("names") or ()))
    return list(names)


def _raise_for_failed_shell(returncode: int | None, output: str) -> None:
    if returncode in (None, 0):
        return

    body = output.strip() or "(no output)"
    raise RuntimeError(f"command exited with code {returncode}\noutput:\n{body}")


def _get_agent(context: ToolContext) -> BubAgent:
    if "_runtime_agent" not in context.state:
        raise RuntimeError("no runtime agent found in tool context")
    return cast("BubAgent", context.state["_runtime_agent"])


class SearchInput(BaseModel):
    query: str = Field(..., description="The search query string.")
    limit: int = Field(20, description="Maximum number of search results to return.")
    start: str | None = Field(None, description="Optional start date to filter entries (ISO format).")
    end: str | None = Field(None, description="Optional end date to filter entries (ISO format).")
    kinds: list[str] = Field(
        default=["message", "tool_result"],
        description="Optional list of entry kinds to filter search results. Can include 'event', 'anchor', 'system', 'message', 'tool_call', 'tool_result', 'error'.",
    )


@final
class SkillList(TypedDict):
    skills: list[str]


@final
class SkillContent(TypedDict):
    name: str
    location: str
    content: str


@final
class ToolFailure(TypedDict):
    """A recoverable failure reported to the caller instead of raising."""

    error: str


class ToolDefinition(TypedDict):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolDescriptions(TypedDict):
    tools: list[ToolDefinition]
    unknown: list[str]
    already_loaded: list[str]
    """Deferred tools loaded earlier in the session, left out of ``tools``; the model calls them directly."""


class TapeInfoResult(TypedDict):
    name: str
    entries: int
    anchors: int
    last_anchor: str | None
    entries_since_last_anchor: int
    last_token_usage: int | None
    last_token_cache_hit_rate: float | None


class TapeSearchMatch(TypedDict):
    date: str
    content: dict[str, Any]


class TapeSearchResult(TypedDict):
    matches: list[TapeSearchMatch]
    filtered: int


class WebFetchResult(TypedDict):
    url: str
    status: int
    content_type: str
    content: str


def _render_skill(result: SkillList | SkillContent | ToolFailure) -> str:
    if "error" in result:
        return f"({result['error']})"
    if "skills" in result:
        return "Available skills:\n" + "\n".join(f"- {name}" for name in result["skills"])
    return f"Location: {result['location']}\n---\n{result['content'] or '(no content)'}"


def _render_tape_info(result: TapeInfoResult) -> str:
    hit_rate = result["last_token_cache_hit_rate"]
    cache_hit_rate = f"{hit_rate:.2%}" if hit_rate is not None else "None"
    return (
        f"name: {result['name']}\n"
        f"entries: {result['entries']}\n"
        f"anchors: {result['anchors']}\n"
        f"last_anchor: {result['last_anchor']}\n"
        f"entries_since_last_anchor: {result['entries_since_last_anchor']}\n"
        f"last_token_usage: {result['last_token_usage']}\n"
        f"last_token_cache_hit_rate: {cache_hit_rate}"
    )


def _render_tape_search(result: TapeSearchResult) -> str:
    matches = result["matches"]
    return f"[tape.search]: {len(matches)} matches ({result['filtered']} filtered)" + "".join(
        f"\n{json.dumps(match)}" for match in matches
    )


def _render_anchors(result: dict[str, list[str]]) -> str:
    if not result["anchors"]:
        return "(no anchors)"
    return "\n".join(f"- {name}" for name in result["anchors"])


@tool(context=True, exposure="direct")
async def bash(
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    background: bool = False,
    *,
    context: ToolContext,
) -> str:
    """Run a shell command. Use background=true to keep it running and fetch output later via bash_output.

    Foreground commands that exceed timeout_seconds continue in the background
    and return a shell ID. Use bash.output to read output or bash.kill to stop them.
    Background commands do not use timeout_seconds.
    """
    environment = environment_from_state(context.state)
    target_cwd = environment.resolve_path(cwd) if cwd else None
    raw_session_id = context.state.get("session_id")
    session_id = str(raw_session_id) if raw_session_id is not None else None
    shell = await shell_manager.start(cmd=command, cwd=target_cwd, session_id=session_id, environment=environment)
    if background:
        return f"Shell started, shell_id: {shell.shell_id}\nRetrieve the output with bash_output or terminate it with bash_kill."
    try:
        async with asyncio.timeout(timeout_seconds):
            shell = await shell_manager.wait_closed(shell.shell_id)
    except asyncio.CancelledError:
        with contextlib.suppress(KeyError):
            await shell_manager.terminate(shell.shell_id)
        raise
    except TimeoutError:
        # Cancellation during descendant cleanup waits for termination to finish.
        # A released shell must not be advertised as a running background command.
        if shell.termination_task is None or not shell.termination_task.done():
            return f"command timed out after {timeout_seconds} seconds; continuing in background\nshell_id: {shell.shell_id}"
    _raise_for_failed_shell(shell.returncode, shell.output)
    return shell.output.strip() or "(no output)"


@tool(name="bash.output", exposure="direct")
async def bash_output(shell_id: str, offset: int = 0, limit: int | None = None) -> str:
    """Read buffered output from a background shell, with optional offset/limit for incremental polling."""
    shell = shell_manager.get(shell_id)
    if shell.returncode is not None:
        await shell_manager.wait_closed(shell_id)
    output = shell.output
    start = max(0, min(offset, len(output)))
    end = len(output) if limit is None else min(len(output), start + max(0, limit))
    chunk = output[start:end].rstrip()
    exit_code = "null" if shell.returncode is None else str(shell.returncode)
    body = chunk or "(no output)"
    return f"id: {shell.shell_id}\nstatus: {shell.status}\nexit_code: {exit_code}\nnext_offset: {end}\noutput:\n{body}"


@tool(name="bash.kill", exposure="direct")
async def kill_bash(shell_id: str) -> str:
    """Terminate a background shell process."""
    shell = await shell_manager.terminate(shell_id)
    return f"id: {shell.shell_id}\nstatus: {shell.status}\nexit_code: {shell.returncode}"


@tool(context=True, name="fs.read", exposure="direct")
async def fs_read(
    path: str, offset: int = 0, limit: int | None = None, *, context: ToolContext
) -> str | dict[str, Any]:
    """Read a text file and return its content. Supports optional pagination with offset and limit.

    PNG, JPEG, WebP and GIF files are returned as images instead of text.
    """
    environment = environment_from_state(context.state)
    resolved_path = environment.resolve_path(path)
    media_type = IMAGE_MEDIA_TYPES.get(PurePath(resolved_path).suffix.lower())
    if media_type is not None:
        data = await environment.read_bytes(resolved_path)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"image is too large to show: {resolved_path} ({len(data)} bytes, limit {MAX_IMAGE_BYTES})"
            )
        return content_result([
            f"Image {PurePath(resolved_path).name} ({media_type}, {len(data)} bytes):",
            republic.image(data, media_type=media_type),
        ])
    text = await environment.read_text(resolved_path)
    lines = text.splitlines()
    start = max(0, min(offset, len(lines)))
    end = len(lines) if limit is None else min(len(lines), start + max(0, limit))
    return "\n".join(lines[start:end])


@tool(context=True, name="fs.write", exposure="direct")
async def fs_write(path: str, content: str, *, context: ToolContext) -> str:
    """Write content to a text file."""
    environment = environment_from_state(context.state)
    resolved_path = environment.resolve_path(path)
    await environment.write_text(resolved_path, content)
    return f"wrote: {resolved_path}"


@tool(context=True, name="fs.edit", exposure="direct")
async def fs_edit(path: str, old: str, new: str, start: int = 0, *, context: ToolContext) -> str:
    """Edit a text file by replacing old text with new text. You can specify the line number to start searching for the old text."""
    environment = environment_from_state(context.state)
    resolved_path = environment.resolve_path(path)
    text = await environment.read_text(resolved_path)
    lines = text.splitlines()
    prev, to_replace = "\n".join(lines[:start]), "\n".join(lines[start:])
    if old not in to_replace:
        raise ValueError(f"'{old}' not found in {resolved_path} from line {start}")
    replaced = to_replace.replace(old, new)
    if prev:
        replaced = prev + "\n" + replaced
    if text.endswith(("\n", "\r")):
        replaced += "\n"
    await environment.write_text(resolved_path, replaced)
    return f"edited: {resolved_path}"


@tool(context=True, name="skill", renderer=_render_skill)
def skill_describe(name: str | None = None, *, context: ToolContext) -> SkillList | SkillContent | ToolFailure:
    """Load the skill content by name. Return the location and skill content.
    If name is not provided, list all available skills in the current workspace.
    """
    from bub.utils import workspace_from_state

    agent = _get_agent(context)
    allowed_skills = context.state.get("allowed_skills")
    if allowed_skills is not None and name and name.casefold() not in allowed_skills:
        return {"error": f"skill '{name}' is not allowed in this context"}

    workspace = workspace_from_state(context.state)
    skill_index = {skill.name: skill for skill in discover_skills(workspace, skill_dirs=agent.skill_dirs)}
    if name is None:
        return {"skills": list(skill_index)}
    if name.casefold() not in skill_index:
        return {"error": "no such skill"}
    skill = skill_index[name.casefold()]
    return {"name": skill.name, "location": str(skill.location), "content": skill.body() or ""}


@tool(context=True, name=TOOL_DESCRIBE_TOOL_NAME, exposure="direct")
async def tool_describe(names: list[str], *, context: ToolContext) -> ToolDescriptions:
    """Load tools by name and return their definitions. Deferred tools become callable from the next step.

    Deferred tools loaded earlier in the session are listed under ``already_loaded`` instead of being described again.
    """
    agent = _get_agent(context)
    allowed_tools = context.state.get(ALLOWED_TOOLS_STATE_KEY)
    available = {
        name: tool_item
        for name, tool_item in agent.tools.items()
        if tool_item.exposure in ("auto", "direct") and (allowed_tools is None or name in allowed_tools)
    }
    index = _tool_name_index(available)
    loaded = set(await loaded_tool_names(context.tape))
    definitions: list[ToolDefinition] = []
    unknown: list[str] = []
    already_loaded: list[str] = []
    newly_loaded: list[str] = []
    for name in names:
        resolved = index.get(name.strip().casefold())
        if resolved is None:
            unknown.append(name)
            continue
        tool_item = available[resolved]
        if tool_item.deferred and resolved in loaded:
            if resolved not in newly_loaded and (model_name := _to_model_name(resolved)) not in already_loaded:
                already_loaded.append(model_name)
            continue
        definitions.append({
            "name": _to_model_name(resolved),
            "description": tool_item.description,
            "parameters": tool_item.parameters,
        })
        if tool_item.deferred and resolved not in loaded:
            loaded.add(resolved)
            newly_loaded.append(resolved)
    if newly_loaded:
        await context.tape.append_event(TOOL_LOADED_EVENT, {"names": newly_loaded})
    return {"tools": definitions, "unknown": unknown, "already_loaded": already_loaded}


@tool(context=True, name="tape.info", renderer=_render_tape_info)
async def tape_info(context: ToolContext) -> TapeInfoResult:
    """Get information about the current tape, such as number of entries and anchors."""
    return cast(TapeInfoResult, asdict(await context.tape.info()))


@tool(context=True, name="tape.search", model=SearchInput, renderer=_render_tape_search)
async def tape_search(param: SearchInput, *, context: ToolContext) -> TapeSearchResult:
    """Search for entries in the current tape that match the query. Returns a list of matching entries."""
    query = context.tape.query().query(param.query).kinds(*param.kinds).limit(param.limit)
    if param.start or param.end:
        query = query.between_dates(param.start or "", param.end or "")

    entries = await context.tape.search(query)
    matches: list[TapeSearchMatch] = []
    for entry in entries:
        match: TapeSearchMatch = {"date": entry.date, "content": entry.payload}
        if "[tape.search]" in json.dumps(match):
            continue
        matches.append(match)
    return {"matches": matches, "filtered": len(entries) - len(matches)}


@tool(context=True, name="tape.reset", renderer=lambda result: result["message"])
async def tape_reset(archive: bool = False, *, context: ToolContext) -> dict[str, str]:
    """Reset the current tape, optionally archiving it."""
    from bub.builtin.archive import archive_dir, reset_tape

    return {"message": await reset_tape(context.tape, archive_to=archive_dir() if archive else None)}


@tool(context=True, name="tape.handoff", renderer=lambda result: f"anchor added: {result['anchor']}")
async def tape_handoff(name: str = "handoff", summary: str = "", *, context: ToolContext) -> dict[str, str]:
    """Add a handoff anchor to the current tape."""
    await context.tape.handoff(name=name, state={"summary": summary})
    return {"anchor": name}


@tool(context=True, name="tape.anchors", renderer=_render_anchors)
async def tape_anchors(*, context: ToolContext) -> dict[str, list[str]]:
    """List anchors in the current tape."""
    anchors = await context.tape.anchors()
    return {"anchors": [anchor.name for anchor in anchors]}


@tool(name="web.fetch", renderer=lambda result: result["content"])
async def web_fetch(url: str, headers: dict | None = None, timeout: int | None = None) -> WebFetchResult:
    """Fetch(GET) the content of a web page, returning markdown if possible."""
    import aiohttp

    headers = {**DEFAULT_HEADERS, **(headers or {})}
    timeout = timeout or DEFAULT_REQUEST_TIMEOUT_SECONDS

    async with (
        aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as session,
        session.get(url) as response,
    ):
        response.raise_for_status()
        return {
            "url": str(response.url),
            "status": response.status,
            "content_type": response.content_type,
            "content": await response.text(),
        }


@tool(name="help", context=True, exposure="command")
def show_help(*, context: ToolContext | None = None) -> str:
    """Show a help message."""
    agent = context.state.get("_runtime_agent") if context is not None else None
    prefix = agent.command_prefix if agent is not None else load_settings().command_prefix
    return (
        f"Commands use '{prefix}' at line start.\n"
        "Known internal commands:\n"
        f"  {prefix}help\n"
        f"  {prefix}skill name=foo\n"
        f"  {prefix}tape.info\n"
        f"  {prefix}tape.search query=error\n"
        f"  {prefix}tape.handoff name=phase-1 summary='done'\n"
        f"  {prefix}tape.anchors\n"
        f"  {prefix}fs.read path=README.md\n"
        f"  {prefix}fs.write path=tmp.txt content='hello'\n"
        f"  {prefix}fs.edit path=tmp.txt old=hello new=world\n"
        f"  {prefix}bash command='sleep 5' background=true\n"
        f"  {prefix}bash.output shell_id=bsh-12345678\n"
        f"  {prefix}bash.kill shell_id=bsh-12345678\n"
        f"  {prefix}code_mode enable=true\n"
        f"  {prefix}quit\n"
        f"Any unknown command after '{prefix}' is executed as shell via bash."
    )


@tool(name="quit", context=True, exposure="command")
async def quit_tool(*, context: ToolContext) -> str:
    """Abort the tasks of the current session. DO NOT use it in a normal workflow."""
    agent = _get_agent(context)
    session_id = str(context.state.get("session_id", "temp/unknown"))
    await subagent_manager.cancel_session(session_id)
    await shell_manager.terminate_session(session_id)
    await agent.framework.quit_via_channel_router(session_id)
    return "Session tasks stopped."


@tool(name="model", context=True, exposure="command")
async def set_model(model_id: str, *, context: ToolContext) -> str:
    """Switch the model for THIS session. Invoke as the `,model <model_id>` command.

    Takes effect on the NEXT turn and persists across restarts. Pass any
    ``provider:model`` string (for example ``openai:gpt-4o`` or
    ``openrouter:openrouter/free``). An invalid model surfaces as an error on the
    next turn — run `,model <valid_id>` again to recover.
    """
    await set_session_setting(context, "model", model_id)
    return f"Session model set to {model_id} (applies from the next turn)."


@tool(name="reasoning_effort", context=True, exposure="command")
async def set_reasoning_effort(reasoning_effort: str, *, context: ToolContext) -> str:
    """Set the reasoning effort for this session starting from the next turn."""
    reasoning_effort = reasoning_effort.strip()
    if not reasoning_effort:
        raise ValueError("reasoning_effort must not be empty")
    await set_session_setting(context, "reasoning_effort", reasoning_effort)
    return f"Session reasoning effort set to {reasoning_effort} (applies from the next turn)."


TOOLS: tuple[Tool, ...] = (
    bash,
    bash_output,
    kill_bash,
    fs_read,
    fs_write,
    fs_edit,
    skill_describe,
    tool_describe,
    tape_info,
    tape_search,
    tape_reset,
    tape_handoff,
    tape_anchors,
    web_fetch,
    *SUBAGENT_TOOLS,
    show_help,
    quit_tool,
    set_model,
    set_reasoning_effort,
)
"""Tools the builtin plugin provides, besides code mode and spill tools."""

```

## src/bub/core/codemode/__init__.py

Source: https://github.com/bubbuild/bub/blob/d14de719da57640cf2f80658217d94d94a5b7c42/src/bub/core/codemode/__init__.py

SHA-256: `8276090e0d6a8fa119d8ee0c03332744a5e888380d535b283da9700d2a90b693`

````
"""Code mode: let the model call tools from Python it writes, through one ``run_code`` tool.

:class:`CodeMode` bundles what an agent needs: the ``run_code`` tool, a system prompt
section describing the code-callable tools as a Python stub, and the ``attach_*`` media
tools. It works with a bare :class:`bub.core.Agent`::

    code_mode = CodeMode([search, fetch], models="openai:gpt-5")
    agent = Agent(model=model, tools=[code_mode.run_code], system_prompt=["You are ...", code_mode.prompt])

Code runs in an :class:`bub.environment.Environment`, a Python process on the host by
default. Calls the code makes go through the running agent's tool events, so hooks and
handlers see them with ``context.code_mode`` set.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import mimetypes
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import republic

from bub.core.codemode.republic_bridge import (
    REPUBLIC_CALL_PREFIX,
    ModelResolver,
    RepublicSession,
    decode_value,
    default_model_resolver,
    encode_value,
)
from bub.core.codemode.stub import _identifier, render_tool_stub
from bub.core.system_prompt import PromptContext
from bub.core.tools import Tool, ToolContext, ToolExecutor, content_result, model_tools
from bub.environment import CallTool, CodeFailed, Environment, LocalEnvironment
from bub.errors import BubError, ErrorKind
from bub.prompt import UserContent
from bub.turn import TurnState

__all__ = [
    "DEFAULT_RUN_CODE_TIMEOUT_SECONDS",
    "MEDIA_TOOLS",
    "RUN_CODE_TOOL_NAME",
    "CodeMode",
    "ModelResolver",
    "attach_audio",
    "attach_image",
    "attach_video",
    "default_model_resolver",
    "render_code_mode_prompt",
    "render_tool_stub",
    "write_stub",
]

RUN_CODE_TOOL_NAME = "run_code"
DEFAULT_RUN_CODE_TIMEOUT_SECONDS = 120

CodeTools = Iterable[Tool] | Callable[[TurnState], Iterable[Tool] | None]
"""The tools callable from code: fixed, or chosen per run from the turn state (None disables ``run_code``)."""


@dataclass(frozen=True)
class _CodeRun:
    environment: Environment
    media: list[UserContent]


_ACTIVE_RUN: contextvars.ContextVar[_CodeRun | None] = contextvars.ContextVar("bub_run_code", default=None)


class CodeMode:
    """The ``run_code`` tool and the system prompt section for a set of code-callable tools.

    Args:
        tools: Tools callable from code as ``tools.<name>(...)``. A callable receives the
            turn state and returns the tools for that run, or None when code mode is off.
        environment: Where the code runs, or a callable returning it for the turn state.
            Defaults to a :class:`bub.environment.LocalEnvironment` in the working directory.
        models: What ``republic.get_*model(spec)`` in the code resolves to: a default
            ``provider:model`` spec for ``get_model()``, or a :data:`ModelResolver`. By default
            specs must be explicit and clients read credentials from the environment.
        stub_dir: Write the tool stub to a file in this directory (or the one returned for
            the turn state) and point the prompt at it, for agents that can read files.
            None puts the stub in the system prompt itself.
        media_tools: Also offer ``attach_image``, ``attach_audio`` and ``attach_video`` to
            the code, which attach media to the ``run_code`` result.
        name: Name of the ``run_code`` tool.
    """

    def __init__(
        self,
        tools: CodeTools = (),
        *,
        environment: Environment | Callable[[TurnState], Environment] | None = None,
        models: str | ModelResolver | None = None,
        stub_dir: Path | Callable[[TurnState], Path] | None = None,
        media_tools: bool = True,
        name: str = RUN_CODE_TOOL_NAME,
    ) -> None:
        self._tools = tools if callable(tools) else list(tools)
        self._environment = LocalEnvironment() if environment is None else environment
        self.resolve_model = models if callable(models) else default_model_resolver(models)
        self.stub_dir = stub_dir
        self.media_tools = media_tools
        self.run_code: Tool = Tool.from_callable(
            self._run_code, name=name, description=_RUN_CODE_DESCRIPTION, context=True, exposure="direct"
        )
        """The model-facing tool; add it to the agent's tools."""

    def code_tools(self, state: TurnState) -> list[Tool] | None:
        """Return the model-safe tools callable from code in this run, or None when code mode is off."""
        tools = self._tools(state) if callable(self._tools) else self._tools
        if tools is None:
            return None
        result = model_tools(tools)
        if self.media_tools:
            names = {tool_item.name for tool_item in result}
            result.extend(tool_item for tool_item in MEDIA_TOOLS if tool_item.name not in names)
        return result

    def environment(self, state: TurnState) -> Environment:
        """Return the environment the code runs in for this run."""
        return self._environment(state) if callable(self._environment) else self._environment

    def prompt(self, context: PromptContext) -> str | None:
        """System prompt section describing the code-callable tools; add it to the agent's system prompt."""
        state = context.state
        if (tools := self.code_tools(state)) is None:
            return None
        stub = render_tool_stub(tools)
        if self.stub_dir is not None:
            directory = self.stub_dir(state) if callable(self.stub_dir) else self.stub_dir
            return render_code_mode_prompt(write_stub(stub, directory), tool_name=self.run_code.name)
        return render_code_mode_prompt(stub, tool_name=self.run_code.name)

    async def _run_code(
        self, code: str, timeout_seconds: int = DEFAULT_RUN_CODE_TIMEOUT_SECONDS, *, context: ToolContext
    ) -> str | dict[str, Any]:
        code_tools = self.code_tools(context.state)
        if code_tools is None:
            raise BubError(ErrorKind.INVALID_INPUT, "Code mode is not enabled for this run.")
        tools_by_name = {_identifier(item.name): item for item in code_tools}
        code_context = replace(context, code_mode=True)
        republic_session = RepublicSession(code_context, self.resolve_model)
        call_tool = _code_tool_caller(tools_by_name, code_context, republic_session)
        environment = self.environment(context.state)

        output: list[str] = []
        media: list[UserContent] = []
        token = _ACTIVE_RUN.set(_CodeRun(environment, media))
        try:
            async with asyncio.timeout(timeout_seconds) as deadline:
                await environment.run_code(code, tools=list(tools_by_name), call_tool=call_tool, write=output.append)
        except TimeoutError:
            if not deadline.expired():
                raise
            raise BubError(
                ErrorKind.TOOL, f"Code timed out after {timeout_seconds} seconds", details={"output": "".join(output)}
            ) from None
        except CodeFailed as exc:
            raise BubError(
                ErrorKind.TOOL,
                f"Code raised {exc.error}",
                details={"output": "".join(output), "traceback": exc.traceback},
            ) from exc
        except BubError as exc:
            raise BubError(exc.kind, exc.message, details={"output": "".join(output), **(exc.details or {})}) from exc
        finally:
            _ACTIVE_RUN.reset(token)
            await republic_session.aclose()
        if media:
            return content_result(["".join(output), *media] if output else media)
        return "".join(output)


_RUN_CODE_DESCRIPTION = """Run Python code in the environment and return everything it prints.

Tools are async functions available as `tools.<name>(...)`: await them with keyword arguments
(top-level `await` is allowed). See the tool stub referenced in the system prompt for their
signatures and result types. The `republic` module provides media loaders and models. Media passed
to `tools.attach_image/audio/video`, as an object, path or URL, is attached to the result. The code is stopped after timeout_seconds."""


def write_stub(content: str, directory: Path) -> Path:
    """Write a stub into ``directory``; the file name depends only on the content, so unchanged stubs keep their path."""
    path = directory / f"tools-{hashlib.sha256(content.encode()).hexdigest()[:12]}.pyi"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    return path


def render_code_mode_prompt(stub: Path | str, *, tool_name: str = RUN_CODE_TOOL_NAME) -> str:
    """Render the code mode prompt for a stub file path, or for the stub text itself."""
    if isinstance(stub, Path):
        where = f"in the stub file: {stub}\nRead the stub before calling a tool you have not used yet. "
    else:
        where = f"in this stub:\n```python\n{stub.rstrip()}\n```\n"
    return (
        "<code_mode>\n"
        f"More tools are available as async Python functions `tools.<name>(...)` inside `{tool_name}`. "
        f"Their signatures, result types and documentation are {where}"
        "Always `await` tool calls (top-level `await` "
        "is allowed) and pass keyword arguments; they return structured values and raise on failure. "
        f"`{tool_name}` returns only what the code prints, so print the results you need, and combine "
        "several tool calls in one run when possible. To look at an image, audio or video, pass its path, URL "
        "or `republic` media object to `tools.attach_image(image=...)`, `tools.attach_audio(audio=...)` or "
        f"`tools.attach_video(video=...)`: it is attached to the `{tool_name}` result.\n"
        "The code can also use the `republic` module (see the end of the stub) to call models with "
        "`republic.get_model()`, "
        "`republic.get_embedding_model(spec)` and `republic.get_decision_model(spec)`; `get_model()` without a "
        "spec is the default model of this session.\n"
        "</code_mode>"
    )


def _code_tool_caller(
    tools_by_name: Mapping[str, Tool], context: ToolContext, republic_session: RepublicSession
) -> CallTool:
    """Serve the code's ``tools.<name>`` and ``republic`` calls."""
    # Code consumes structured results, so this executor does not render them to text.
    executor = ToolExecutor(context.events, render=False)

    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        arguments = decode_value(arguments)
        if name.startswith(REPUBLIC_CALL_PREFIX):
            return encode_value(await republic_session.call(name, arguments))
        tool_item = tools_by_name.get(name)
        if tool_item is None:
            raise BubError(ErrorKind.INVALID_INPUT, f"unknown tool: tools.{name}")
        return encode_value(await executor.execute(tool_item, arguments, context=context))

    return call_tool


_REMOTE_MEDIA_PREFIXES = ("http://", "https://", "gs://", "data:")
_MEDIA_CLASSES: dict[str, type[republic.Image | republic.Audio | republic.Video]] = {
    "image": republic.Image,
    "audio": republic.Audio,
    "video": republic.Video,
}


async def _load_media(
    kind: str, source: str, environment: Environment
) -> republic.Image | republic.Audio | republic.Video:
    """Load media from a URL, a data URL, or a file path in the environment."""
    if source.startswith(_REMOTE_MEDIA_PREFIXES):
        media: republic.Image | republic.Audio | republic.Video = getattr(republic, kind)(source)
        return media
    media_type, _ = mimetypes.guess_type(source)
    if media_type is None:
        raise ValueError(f"cannot guess the media type of {source!r}")
    data = await environment.read_bytes(environment.resolve_path(source))
    return _MEDIA_CLASSES[kind](media_type, data=data)


async def _attach(kind: str, item: republic.Image | republic.Audio | republic.Video | str) -> str:
    run = _ACTIVE_RUN.get()
    if run is None:
        raise BubError(ErrorKind.INVALID_INPUT, f"tools.attach_{kind} can only be called from {RUN_CODE_TOOL_NAME}.")
    if isinstance(item, str):
        try:
            item = await _load_media(kind, item, run.environment)
        except (OSError, ValueError) as exc:
            raise BubError(ErrorKind.INVALID_INPUT, f"Cannot load the {kind}: {exc}") from exc
    run.media.append(item)
    return f"The {kind} is attached to the {RUN_CODE_TOOL_NAME} result."


async def _attach_image(image: republic.Image | str, *, context: ToolContext) -> str:
    """Attach an image to the result of this `run_code` call, so you can see it.

    `image` is a `republic.Image`, a file path in the environment, an http(s) URL, or a data URL.
    """
    return await _attach("image", image)


async def _attach_audio(audio: republic.Audio | str, *, context: ToolContext) -> str:
    """Attach audio to the result of this `run_code` call, so you can hear it.

    `audio` is a `republic.Audio`, a file path in the environment, an http(s) URL, or a data URL.
    """
    return await _attach("audio", audio)


async def _attach_video(video: republic.Video | str, *, context: ToolContext) -> str:
    """Attach a video to the result of this `run_code` call, so you can watch it.

    `video` is a `republic.Video`, a file path in the environment, an http(s) URL, or a data URL.
    """
    return await _attach("video", video)


attach_image = Tool.from_callable(_attach_image, name="attach_image", context=True, exposure="code")
attach_audio = Tool.from_callable(_attach_audio, name="attach_audio", context=True, exposure="code")
attach_video = Tool.from_callable(_attach_video, name="attach_video", context=True, exposure="code")
MEDIA_TOOLS = (attach_image, attach_audio, attach_video)
"""Code-only tools attaching media to the ``run_code`` result; they work only inside ``run_code``."""

````
