# open-swe — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## mda/rt/remote_files.py

Source: https://github.com/langchain-ai/open-swe/blob/1d0c1be6b299c33bf177cc6de8d658454e08ee7f/mda/rt/remote_files.py

SHA-256: `5d6abdb86b5aa65b87b614fea78b10f22e63e82db7891f58eff09cfb154c0cd5`

```
"""The paths the Robin backend serves in the thread's files, as a deepagents backend.

The backend holds some of the thread's files rather than the sandbox, such as the images
a run offloaded (``FilesSpec.backend_paths``). This backend reaches them through the typed
files API (``runtime_api.files``), so the file tools work on them as they do in the
sandbox; it runs no commands.
"""

import base64
from collections.abc import Callable
from typing import Final

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    ContextLine,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.tools import BaseTool
from pydantic import BaseModel
from runtime_api import files as file_api
from runtime_api.endpoint import Endpoint, Transport, call
from runtime_api.operations import RunRequest

# deepagents' file and shell tools.
FILE_TOOLS: Final = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)


def _file_info(entry: file_api.FileInfo) -> FileInfo:
    info = FileInfo(path=entry.path)
    if entry.is_dir is not None:
        info["is_dir"] = entry.is_dir
    if entry.size is not None:
        info["size"] = entry.size
    if entry.modified_at is not None:
        info["modified_at"] = entry.modified_at
    return info


def _file_data(data: file_api.FileData) -> FileData:
    file_data = FileData(content=data.content, encoding=data.encoding)
    if data.created_at is not None:
        file_data["created_at"] = data.created_at
    if data.modified_at is not None:
        file_data["modified_at"] = data.modified_at
    return file_data


def _context(lines: list[file_api.ContextLine]) -> list[ContextLine]:
    return [ContextLine(line=line.line, text=line.text) for line in lines]


def _grep_match(match: file_api.GrepMatch) -> GrepMatch:
    found = GrepMatch(path=match.path, line=match.line, text=match.text)
    if match.context_before is not None:
        found["context_before"] = _context(match.context_before)
    if match.context_after is not None:
        found["context_after"] = _context(match.context_after)
    return found


class RemoteFiles(BackendProtocol):
    """The paths of the thread's files the backend serves, for this run."""

    def __init__(self, transport: Transport, run_id: Callable[[], str | None]) -> None:
        self._transport = transport
        self._run_id = run_id

    async def _call[Request: RunRequest, Reply: BaseModel](
        self, endpoint: Endpoint[Request, Reply], request: Request
    ) -> Reply:
        return await call(
            self._transport, endpoint, request.model_copy(update={"run_id": self._run_id()})
        )

    async def als(self, path: str) -> LsResult:
        reply = await self._call(file_api.LS, file_api.PathRequest(path=path))
        return LsResult(
            error=reply.error,
            entries=None
            if reply.entries is None
            else [_file_info(entry) for entry in reply.entries],
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        reply = await self._call(
            file_api.READ,
            file_api.ReadRequest(file_path=file_path, offset=offset, limit=limit),
        )
        return ReadResult(
            error=reply.error,
            file_data=None if reply.file_data is None else _file_data(reply.file_data),
            total_lines=reply.total_lines,
            start_line=reply.start_line,
            end_line=reply.end_line,
            next_offset=reply.next_offset,
            no_lines_requested=reply.no_lines_requested,
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        reply = await self._call(
            file_api.WRITE, file_api.WriteRequest(file_path=file_path, content=content)
        )
        return WriteResult(error=reply.error, path=reply.path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        reply = await self._call(
            file_api.EDIT,
            file_api.EditRequest(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            ),
        )
        return EditResult(error=reply.error, path=reply.path, occurrences=reply.occurrences)

    async def adelete(self, file_path: str) -> DeleteResult:
        reply = await self._call(file_api.DELETE, file_api.DeleteRequest(file_path=file_path))
        return DeleteResult(error=reply.error, path=reply.path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        reply = await self._call(
            file_api.GLOB,
            file_api.GlobRequest(pattern=pattern, path=None if path is None else path),
        )
        return GlobResult(
            error=reply.error,
            matches=None
            if reply.matches is None
            else [_file_info(match) for match in reply.matches],
            truncated=reply.truncated,
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        reply = await self._call(
            file_api.GREP,
            file_api.GrepRequest(
                pattern=pattern,
                path=None if path is None else path,
                glob=glob,
                max_count=max_count,
            ),
        )
        return GrepResult(
            error=reply.error,
            matches=None
            if reply.matches is None
            else [_grep_match(match) for match in reply.matches],
            truncated=reply.truncated,
        )

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        reply = await self._call(
            file_api.UPLOAD,
            file_api.UploadRequest(
                files=[
                    file_api.UploadedFile(path=path, content_base64=base64.b64encode(data).decode())
                    for path, data in files
                ]
            ),
        )
        return [FileUploadResponse(path=item.path, error=item.error) for item in reply.files]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        reply = await self._call(file_api.DOWNLOAD, file_api.DownloadRequest(paths=paths))
        return [
            FileDownloadResponse(
                path=item.path,
                error=item.error,
                content=None
                if item.content_base64 is None
                else base64.b64decode(item.content_base64),
            )
            for item in reply.files
        ]


def remote_file_tools(remote: RemoteFiles) -> dict[str, BaseTool]:
    """deepagents' own file and shell tools, against the backend's files, where only the file
    tools work.

    The tools read large results back only from a composite backend.
    """
    files = CompositeBackend(default=remote, routes={})
    return {tool.name: tool for tool in FilesystemMiddleware(backend=files).tools}

```

## mda/rt/run_tools.py

Source: https://github.com/langchain-ai/open-swe/blob/1d0c1be6b299c33bf177cc6de8d658454e08ee7f/mda/rt/run_tools.py

SHA-256: `97ba9a7dc62418cbd455bce305d0abea58f1dd6ae7a849aa804c251105261f36`

```
"""The run's tools and models, from its catalog: offered to each model call, run by name.

MDA builds the graph once and shares it between every run of every thread, so the
graph registers none of the operations a run's catalog names, nor the tools of the
MCP servers it connects to itself (``rt.mcp``). ``RunToolsMiddleware`` offers each
model call the run's own tools and hands LangGraph's tool node the tool a call names;
code mode reaches the same tools (``RunCodeInterpreter``). A subagent's prompt and
model, and the run's limit on model calls, are the run's too.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langchain_quickjs._ptc import (  # noqa: PLC2701 - lists code mode tools in a message
    filter_tools_for_ptc,
    is_valid_ptc_tool_name,
    render_ptc_prompt,
)
from langchain_quickjs.middleware import PersistenceMode, REPLState
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command
from runtime_api.catalog import Catalog, SubagentSpec, ToolSpec
from runtime_api.loop import ModelCall
from runtime_api.provenance import Provenance

from rt.backend import BackendCallError, OpenSweBackend, Run, RunLocal, current_run, harness_message
from rt.file_tools import FileToolsMiddleware
from rt.gateway import RUN_GATEWAY
from rt.loop import latest_with_id, visible_messages
from rt.mcp import McpTools
from rt.models import build_model
from rt.search_tools import resolve_tool, search_tools_tool
from rt.slack_move import MOVE_TOOL, SlackMoveMiddleware
from rt.tools import backend_tool, operation_tool, run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

_TASK_TOOL: Final = "task"
_EVAL_TOOL: Final = "eval"
# Starts the id of the message listing the tools code mode calls.
_CODE_TOOLS: Final = "code-tools:"

ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]
ModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]


def subagent_description(name: str) -> str:
    """What the graph declares as subagent ``name``'s description, which each run's replaces."""
    return f"[[open-swe subagent {name}]]"


def subagent_prompt(name: str) -> str:
    """What the graph declares as subagent ``name``'s system prompt, which each run's replaces."""
    return f"[[open-swe subagent prompt {name}]]"


class RunTools:
    """One run's tools and models, as its catalog names them, and once the run connects to
    its MCP servers (``connect``), their tools too."""

    def __init__(
        self, catalog: Catalog, backend: OpenSweBackend, files: FileToolsMiddleware
    ) -> None:
        self.catalog = catalog
        self._backend = backend
        self._files = files
        self._operations = [operation_tool(spec, backend) for spec in catalog.operations]
        # None until the run connects to the MCP servers its catalog names.
        self._mcp: McpTools | None = None if catalog.mcp_servers else McpTools()
        self._connecting = asyncio.Lock()
        self.excluded = frozenset(catalog.excluded)
        # Offered to every model call, so the list stays the same from run to run, but
        # refused when called: this run may not use them.
        self.disabled = frozenset(catalog.disabled)
        self._offer(McpTools())
        self.subagents = {subagent.name: subagent for subagent in catalog.subagents}
        models = catalog.models
        self.subagent_model: BaseChatModel | None = (
            None if models is None else build_model(models.subagent_model)
        )
        self._route_models: dict[str, BaseChatModel] = {}

    def subagent_model_for(self, route: str | None) -> BaseChatModel | None:
        """The model a subagent call goes to: the run's route's, or else the run's subagent model."""
        models = self.catalog.models
        if models is None or route is None or (spec := models.routes.get(route)) is None:
            return self.subagent_model
        if route not in self._route_models:
            self._route_models[route] = build_model(spec)
        return self._route_models[route]

    def _offer(self, mcp: McpTools) -> None:
        """Offer the run's operations and ``mcp``'s tools: to each model call, or from code.

        The backend's ``search_tools`` finds only the tools it serves, so the tools of the
        MCP servers the run connects to itself are offered to each model call.
        """
        catalog = self.catalog
        tools = [*self._operations, *mcp.tools]
        searched = (
            [search_tools_tool(self._backend, catalog.search_tools_description)]
            if catalog.search_tools
            else []
        )
        self.by_name = {tool.name: tool for tool in (*tools, *searched)}
        # Every schema offered costs each call its context, and OpenAI answers some
        # requests carrying hundreds with nothing; the tools the model seldom needs are
        # called from code. A run with code mode calls its MCP servers' tools from code
        # too, so the tools offered to the model are the same whoever's servers they are.
        code_only = frozenset(catalog.code_only) | (
            {tool.name for tool in mcp.tools if is_valid_ptc_tool_name(tool.name)}
            if catalog.code_only
            else frozenset()
        )
        self.offered = [tool for tool in self.by_name.values() if tool.name not in code_only]
        in_code = [tool for tool in self.by_name.values() if tool.name in code_only - self.disabled]
        # A move from code goes through MDA too, as the model's own call of it does.
        moves = SlackMoveMiddleware(self._backend)
        self.from_code = [
            moves.code_tool(tool) if tool.name == MOVE_TOOL else tool
            for tool in [
                *in_code,
                *(
                    t
                    for t in tools
                    if t.name in catalog.also_from_code
                    and t.name not in self.disabled
                    and t not in in_code
                ),
            ]
        ] + self._files.code_tools([*catalog.also_from_code, *catalog.code_only])
        self.hidden = self.excluded | code_only

    async def connect(self) -> None:
        """Load the tools of the MCP servers the run connects to itself, once for the run.

        A person who has not granted a server's connection is asked to, and the call that
        connects waits on them.
        """
        if self._mcp is not None:
            return
        async with self._connecting:
            if self._mcp is not None:
                return
            self._mcp = await McpTools.load(
                self.catalog.mcp_servers,
                self._backend,
                taken=self.by_name.keys(),
            )
            self._offer(self._mcp)

    def subagent_tools(self, subagent: SubagentSpec) -> list[BaseTool]:
        """What ``subagent`` may call of the run's tools: all it is offered, or those it names."""
        if subagent.tools is None:
            return self.offered
        return [self.by_name[name] for name in subagent.tools if name in self.by_name]


class RunToolsets:
    """Each run's ``RunTools``, made from its catalog once, and again once a worker adopts its own."""

    def __init__(self, backend: OpenSweBackend, files: FileToolsMiddleware) -> None:
        self._backend = backend
        self._files = files
        self._runs = RunLocal[list[RunTools]](list)

    def get(self, run: Run | None = None) -> RunTools:
        run = run if run is not None else self._backend.run
        if run.catalog.needs_tool_specs:
            raise RuntimeError("The run has yet to read its tool specs")
        held = self._runs.get(run)
        if not held or held[0].catalog is not run.catalog:
            held[:] = [RunTools(run.catalog, self._backend, self._files)]
        return held[0]

    async def ready(self) -> RunTools:
        """The run's tools, once it has read their specs: a run picked up from its
        checkpoint in another process reads them here, past the hook that reads them."""
        run = self._backend.run
        await run.load_tool_specs(run_id())
        return self.get(run)

    async def load(self) -> RunTools:
        """The run's tools once it has read their specs, with the tools of the MCP servers
        it connects to itself."""
        tools = await self.ready()
        await tools.connect()
        return tools


class BackendTools:
    """The tools the run calls that its catalog leaves out, as the backend resolves them.

    Code mode resolves a name it has no tool for from the REPL's own thread, outside
    any run, so the tool it gets is the same for every run and finds its run when called;
    a call by name the run's tools don't have gets the same tool.
    """

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend
        self._tools: dict[str, BaseTool] = {}

    def tool(self, name: str) -> BaseTool:
        """The tool called as ``name``, whichever run calls it."""
        if name not in self._tools:
            self._tools[name] = backend_tool(name, self._backend, self.resolve)
        return self._tools[name]

    async def resolve(self, name: str) -> ToolSpec | None:
        """The tool the calling run calls as ``name``, resolved once per run."""
        run = current_run()
        if not run.catalog.search_tools:
            return None
        if name not in run.backend_tools:
            run.backend_tools[name] = await resolve_tool(self._backend, name)
        return run.backend_tools[name]


class RunToolsMiddleware(AgentMiddleware):
    """Offer each model call the run's tools as the graph would, and run each call of one.

    The backend's tools act on its sandbox, so they stay offered even where MDA's
    filesystem middleware drops one it cannot back itself, such as ``execute``. Hidden
    tools still run when called by name, as in-process; the backend names the ones its
    middleware hides from each call. The run connects to its MCP servers before its
    first model call, or at a call of one of their tools that a resumed run makes first.
    ``subagent`` names the subagent this middleware serves; the agent itself otherwise.
    A call by a name the run's tools don't have goes to ``backend_tools``, for a tool
    the catalog leaves out.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(
        self,
        toolsets: RunToolsets,
        *,
        subagent: str | None = None,
        backend_tools: BackendTools | None = None,
    ) -> None:
        self._toolsets = toolsets
        self._subagent = subagent
        self._backend_tools = backend_tools
        # The ``task`` tool as each run describes its subagents, by the description.
        self._task_tools = RunLocal[dict[tuple[int, str], BaseTool]](dict)

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        # In a node of its own: a person asked to grant a connection resumes this node
        # alone, not the model call, whose outer wrappers, such as summarization, would
        # do their work again.
        await self._toolsets.load()

    async def awrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        tools = await self._toolsets.load()
        if self._subagent is None:
            hidden = tools.hidden
            own = tools.offered
        else:
            hidden = tools.excluded
            spec = tools.subagents.get(self._subagent)
            own = [] if spec is None else tools.subagent_tools(spec)
        planned = request.state.get("open_swe_call")
        if isinstance(planned, dict):
            hidden |= frozenset(ModelCall.model_validate(planned).hidden_tools)
        if not tools.subagents:
            hidden |= {_TASK_TOOL}
        offered = [
            self._described(tool, tools)
            if isinstance(tool, BaseTool) and tool.name == _TASK_TOOL
            else tool
            for tool in request.tools
            if getattr(tool, "name", None) not in hidden
        ]
        names = {getattr(tool, "name", None) for tool in offered}
        missing = [tool for tool in own if tool.name not in names | hidden]
        return await handler(request.override(tools=[*offered, *missing]))

    def _described(self, task: BaseTool, tools: RunTools) -> BaseTool:
        """``task`` describing the run's own subagents."""
        description = task.description
        for spec in tools.subagents.values():
            description = description.replace(subagent_description(spec.name), spec.description)
        key = (id(task), description)
        task_tools = self._task_tools.get(current_run())
        if key not in task_tools:
            task_tools[key] = task.model_copy(update={"description": description})
        return task_tools[key]

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolHandler
    ) -> ToolMessage | Command:
        name = request.tool_call["name"]
        tools = await self._toolsets.ready()
        if name in tools.disabled:
            return ToolMessage(
                content=f"Error: `{name}` is not available in this run.",
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )
        if request.tool is None:
            tool = tools.by_name.get(name)
            if tool is None and tools.catalog.mcp_servers:
                # A resumed run's call of an MCP server's tool, before any model call.
                tool = (await self._toolsets.load()).by_name.get(name)
            # Only a name the backend knows: any other gets the tool node's own answer
            # for a tool that is not there, which the run recovers from.
            if (
                tool is None
                and self._backend_tools is not None
                and tools.catalog.search_tools
                and await self._backend_tools.resolve(name) is not None
            ):
                tool = self._backend_tools.tool(name)
            if tool is not None:
                request = request.override(tool=tool)
        return await handler(request)


class RunSubagentMiddleware(AgentMiddleware):
    """Give subagent ``name`` the system prompt and model the run's catalog gives it."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, toolsets: RunToolsets, name: str) -> None:
        self._toolsets = toolsets
        self._name = name

    async def awrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        tools = await self._toolsets.ready()
        spec = tools.subagents.get(self._name)
        if spec is not None and request.system_message is not None:
            text = request.system_message.text.replace(
                subagent_prompt(self._name), spec.system_prompt
            )
            request = request.override(system_message=SystemMessage(content=text))
        if (model := tools.subagent_model_for(current_run().route)) is not None:
            request = request.override(model=model)
        return await RUN_GATEWAY.call(request, handler, run=current_run(), routed_model=model)


@dataclass
class _CodeToolsListing:
    tools: tuple[BaseTool, ...] = ()
    # The API reference the interpreter appends to its prompt, and the message listing it.
    reference: str = ""
    text: str = ""


class RunCodeInterpreter(CodeInterpreterMiddleware):
    """Code mode over the run's tools: the graph's own it names, and those the run offers from code.

    ``active`` says whether a run has code mode at all; one without it is offered no
    ``eval`` and pays nothing for the interpreter. With ``listed_in_messages`` the tools
    code mode calls are listed in a message added whenever they change, not in the system
    prompt: they differ from person to person and from run to run, and a changed system
    prompt misses the provider's cache for everything after it.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(
        self,
        toolsets: RunToolsets,
        *,
        named: Sequence[str | BaseTool] = (),
        active: Callable[[RunTools], bool] = lambda _tools: True,
        subagents: bool = True,
        mode: PersistenceMode | None = None,
        backend_tools: BackendTools | None = None,
        listed_in_messages: bool = False,
    ) -> None:
        self._toolsets = toolsets
        self._named = list(named)
        self._active = active
        self._listed_in_messages = listed_in_messages
        self._listings = RunLocal(_CodeToolsListing)
        self._tools_by_slot = RunLocal[dict[str, tuple[BaseTool, ...]]](dict)
        super().__init__(
            ptc=[],
            subagents=subagents,
            mode=mode,
            resolve_tool=None if backend_tools is None else backend_tools.tool,
        )

    @property
    def _ptc(self) -> list[str | BaseTool]:
        try:
            # Read only once a hook made sure the run has its tool specs.
            tools = self._toolsets.get()
        except BackendCallError, RuntimeError:
            # Outside a run, such as at build: only the graph's own.
            return list(self._named)
        return [*self._named, *tools.from_code]

    @_ptc.setter
    def _ptc(self, _ptc: object) -> None:
        """The interpreter sets its tools once; these are each run's."""

    @property
    def _ptc_tools_by_slot(self) -> dict[str, tuple[BaseTool, ...]]:
        """Each slot's code tools, kept by the run so a run that dies before ``after_agent`` frees them.

        They hold the run's MCP tools and backend client, and every run evals on a slot of its
        own, so a cache on the shared middleware would keep a failed run's until the process ends.
        """
        try:
            return self._tools_by_slot.get(current_run())
        except BackendCallError, RuntimeError:
            # Outside a run nothing evals, so there is nothing to keep.
            return {}

    @_ptc_tools_by_slot.setter
    def _ptc_tools_by_slot(self, _cache: object) -> None:
        """The interpreter keeps one cache for every run; each run keeps its own."""

    async def _runs_code(self) -> bool:
        return self._active(await self._toolsets.ready())

    async def abefore_agent(self, state: REPLState, runtime: Runtime) -> dict[str, object] | None:
        if not await self._runs_code():
            return None
        update = self._slot_update_for_runtime()
        # Each run evals on a slot of its own. QuickJS keeps a bounded number of warm slots
        # per process and closes the oldest past that, so a finished run's is never closed here.
        prepared: REPLState = {**state, "_quickjs_slot_id": update["_quickjs_slot_id"]}
        restored = await super().abefore_agent(prepared, runtime)
        return {**update, **(restored or {})} or None

    async def abefore_model(self, state: REPLState, runtime: Runtime) -> dict[str, object] | None:  # noqa: ARG002
        if not self._listed_in_messages or not await self._runs_code():
            return None
        listing = self._listing().text
        if not listing or listing == latest_with_id(visible_messages(state), _CODE_TOOLS):
            return None
        message = harness_message(
            listing,
            message_id=f"{_CODE_TOOLS}{uuid.uuid4()}",
            source="code_tools",
            provenance=Provenance(kind="context", sender="run:code-tools"),
        )
        return {"messages": [message]}

    def _listing(self) -> _CodeToolsListing:
        """The tools code mode calls, as the message that lists them."""
        exposed = tuple(filter_tools_for_ptc([], self._ptc, self_tool_name=self._tool_name))
        cached = self._listings.get(current_run())
        if len(cached.tools) == len(exposed) and all(
            previous is tool for previous, tool in zip(cached.tools, exposed, strict=True)
        ):
            return cached
        reference = render_ptc_prompt(exposed, tool_name=self._tool_name)
        body = reference.strip().replace("]]>", "]]]]><![CDATA[>")
        text = (
            f'<dynamic-context kind="run" id="code-tools"><![CDATA[\n{body}\n]]></dynamic-context>'
            if body
            else ""
        )
        cached.tools, cached.reference, cached.text = exposed, reference, text
        return cached

    async def awrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        if not await self._runs_code():
            tools = [tool for tool in request.tools if getattr(tool, "name", None) != _EVAL_TOOL]
            return await handler(request.override(tools=tools))
        if not self._listed_in_messages:
            return await super().awrap_model_call(request, handler)
        prompt = self._prepare_for_call(request)
        reference = self._listing().reference
        if reference:
            prompt = prompt.removesuffix(reference).replace(
                "documented in the API reference below.",
                'listed in the latest `<dynamic-context kind="run" id="code-tools">` message.',
            )
        return await handler(
            request.override(system_message=self._extend(request.system_message, prompt))
        )

    async def aafter_agent(self, state: REPLState, runtime: Runtime) -> dict[str, object] | None:
        if not await self._runs_code():
            return None
        return await super().aafter_agent(state, runtime)


class RunModelCallLimit(ModelCallLimitMiddleware):
    """End a run at its catalog's limit on model calls, or at ``default`` for a catalog without one."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend, default: int) -> None:
        self._run_backend = backend
        self._default = default
        super().__init__(run_limit=default, exit_behavior="end")

    @property
    def run_limit(self) -> int:
        try:
            limit = self._run_backend.run.catalog.model_call_limit
        except BackendCallError, RuntimeError:
            return self._default
        return self._default if limit is None else limit

    @run_limit.setter
    def run_limit(self, _limit: int | None) -> None:
        """The middleware sets its limit once; this is each run's."""

```

## mda/rt/search_tools.py

Source: https://github.com/langchain-ai/open-swe/blob/1d0c1be6b299c33bf177cc6de8d658454e08ee7f/mda/rt/search_tools.py

SHA-256: `447d1935ed5b3264194a3f6f0190263946b839db7653fd9516ac84571dbf5772`

```
"""``search_tools``: the run's operations that best match what the model looks for.

Code mode keeps most tools off the model's list, so the model finds them here, with
the schemas it needs to call them and the name code mode calls them by. The run's
catalog leaves its integrations out; the backend keeps every tool, and a run whose
catalog names the set (``Catalog.tool_set``) reads it once per process and searches
it here, as the backend would (``runtime_api.tool_search``). A run without one
searches on the backend.
"""

import threading
from collections import OrderedDict
from typing import Final

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import JsonValue
from runtime_api.catalog import SEARCH_TOOLS, ToolSpec
from runtime_api.endpoint import call
from runtime_api.operations import RunRequest
from runtime_api.tool_search import RESOLVE, SEARCH, TOOL_SET, ToolIndex, ToolLookup, ToolSearch

from rt.backend import OpenSweBackend, current_run
from rt.tools import run_id

# How many sets this process keeps read: a set never changes under its digest, and
# one may come to a megabyte.
_READ_LIMIT: Final = 16
# The sets read, least recently used first; runs on other event loops share them.
_read: OrderedDict[str, ToolIndex] = OrderedDict()
_reading = threading.Lock()


async def run_tool_index(backend: OpenSweBackend) -> ToolIndex | None:
    """Every tool the calling run may call, to search and resolve here; None when its
    catalog names no set, and the backend searches."""
    digest = current_run().catalog.tool_set
    if digest is None:
        return None
    with _reading:
        index = _read.get(digest)
        if index is not None:
            _read.move_to_end(digest)
            return index
    # Two runs missing the same set at once both read it, which costs a read, no more.
    kept = await call(backend, TOOL_SET, RunRequest(run_id=run_id()))
    index = ToolIndex(kept.tools, kept.excluded)
    if kept.digest == digest:
        with _reading:
            _read[digest] = index
            while len(_read) > _READ_LIMIT:
                _read.popitem(last=False)
    return index


async def resolve_tool(backend: OpenSweBackend, name: str) -> ToolSpec | None:
    """The tool the calling run calls as ``name``, with its grant: a run that searches its
    set here finds here that it has no such tool; only the grant of one it has comes
    from the backend."""
    index = await run_tool_index(backend)
    if index is not None and index.resolve(name) is None:
        return None
    lookup = ToolLookup(run_id=run_id(), name=name)
    return (await call(backend, RESOLVE, lookup)).tool


def search_tools_tool(backend: OpenSweBackend, description: str | None = None) -> BaseTool:
    """``search_tools`` over every tool the run may call."""

    async def search_tools(query: str) -> list[dict[str, JsonValue]]:
        index = await run_tool_index(backend)
        if index is not None:
            found = [ToolIndex.match(tool) for tool in index.search(query)]
        else:
            request = ToolSearch(run_id=run_id(), query=query)
            found = (await call(backend, SEARCH, request)).matches
        return [match.model_dump(mode="json") for match in found]

    return StructuredTool.from_function(
        coroutine=search_tools,
        name=SEARCH_TOOLS,
        # The backend's description, which says how to call what it finds.
        description=description or "Search the tools available in this run.",
    )

```
