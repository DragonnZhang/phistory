# DB-GPT — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/subagent/react_tools.py

Source: https://github.com/eosphoros-ai/DB-GPT/blob/9d50d0a55071a040a8cb7684b3b6acfa13cc7c47/packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/subagent/react_tools.py

SHA-256: `69fcaca76e09dc73d8ca4ec02431857e14fd3e55713c3fc30007968691b03874`

```
"""ReAct tool factory for the react-agent link.

Builds the set of ReAct tools that are bound to a per-(sub-)agent
``react_state`` dict. The main agent and every sub-agent each call
``make_react_tools`` once, capturing their own ``react_state`` — this is the
physical prerequisite for per-sub-agent state isolation (see design spec
§4.5 / plan stage 1).

SQL, Python, skill execution, shell execution and HTML rendering use the shared tool
factories. Each factory captures this agent's state so fixes apply to both the
main agent and sub-agents without sharing their mutable session state.

Returns 8 tools::

    load_skill / load_tools / knowledge_retrieve / sql_query /
    code_interpreter / shell_interpreter / execute_skill_script_file /
    html_interpreter

``todowrite`` is intentionally NOT here — it shares ``_todo_list`` with the
SSE main loop and stays in ``_react_agent_stream``. The module-level skill
tools (``execute_skill_script`` / ``get_skill_resource``) are imported
directly by callers, not rebuilt here.
"""

import json
import logging
from typing import Any, Dict

from dbgpt._private.config import Config
from dbgpt.agent.resource.base import AgentResource, ResourceType
from dbgpt.agent.resource.manage import get_resource_manager
from dbgpt.agent.resource.tool.base import tool
from dbgpt.configs.model_config import SKILLS_DIR
from dbgpt.core.interface.report_capture import get_current_artifact_sink

from ..tools.code_interpreter import (
    make_code_interpreter as make_shared_code_interpreter,
)
from ..tools.html_interpreter import make_html_interpreter
from ..tools.knowledge_retrieve import (
    make_knowledge_retrieve as make_shared_knowledge_retrieve,
)
from ..tools.shell_interpreter import make_shell_interpreter
from ..tools.skill_tools import make_execute_skill_script_file
from ..tools.sql_query import make_sql_query as make_shared_sql_query

CFG = Config()
logger = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = SKILLS_DIR


def make_react_tools(
    react_state: dict,
    *,
    database_connector: Any = None,
    knowledge_resources: Any = None,
) -> Dict[str, Any]:
    """Build a set of ReAct tools bound to the given ``react_state``.

    Each call returns a brand-new set of closure tools that capture the
    passed-in ``react_state``. The main agent and every sub-agent call this
    once, achieving state isolation.

    Args:
        react_state: The per-agent mutable state container. The only
            per-sub-agent isolation anchor. Tools read/write ``conv_id``,
            ``file_path``, ``generated_images``, ``image_url_map``,
            ``auto_data``, ``matched``, ``skill_prompt``.
        database_connector: Shared read-only DB connector (or None). Used by
            ``sql_query``.
        knowledge_resources: Shared read-only knowledge resources (or None).
            Used by ``knowledge_retrieve``.

    Returns:
        A dict mapping tool name -> tool callable, with 8 entries:
        ``load_skill``, ``load_tools``, ``knowledge_retrieve``, ``sql_query``,
        ``code_interpreter``, ``shell_interpreter``,
        ``execute_skill_script_file``, ``html_interpreter``.
    """

    @tool(
        description="Load skill content by skill name and file path. "
        "Returns the SKILL.md content of the specified skill. "
        '参数: {"skill_name": "技能名称", "file_path": "技能文件路径"}'
    )
    def load_skill(skill_name: str, file_path: str) -> str:
        """Load the skill content (SKILL.md) by skill name and file path.

        Args:
            skill_name: The name of the skill to load.
            file_path: The file path of the skill.
        """
        from dbgpt.agent.claude_skill import get_registry

        # Try to get skill from registry
        registry = get_registry()
        matched = registry.get_skill(skill_name)

        # If not found, try case-insensitive match
        if not matched:
            for s in registry.list_skills():
                if s.name.lower() == skill_name.lower():
                    matched = registry.get_skill(s.name)
                    break

        if not matched:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"Skill '{skill_name}' not found",
                        }
                    ]
                },
                ensure_ascii=False,
            )

        # Update react_state for compatibility with existing logic
        react_state["matched"] = matched
        react_state["skill_prompt"] = matched.get_prompt()

        # Build response content
        chunks = [
            {
                "output_type": "text",
                "content": f"Skill: {matched.metadata.name}",
            },
            {
                "output_type": "text",
                "content": f"File path: {file_path}",
            },
            {"output_type": "text", "content": "---"},
        ]

        # Add skill content/prompt
        if matched.instructions:
            chunks.append({"output_type": "markdown", "content": matched.instructions})
        elif matched.prompt_template:
            prompt_text = (
                matched.prompt_template.template
                if hasattr(matched.prompt_template, "template")
                else str(matched.prompt_template)
            )
            chunks.append({"output_type": "markdown", "content": prompt_text})

        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    @tool(description="Resolve required tools for the selected skill.")
    def load_tools() -> str:
        matched = react_state.get("matched")
        rm = get_resource_manager(CFG.SYSTEM_APP)
        required_tools = matched.metadata.required_tools if matched else []
        if not required_tools:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "No required tools specified",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        loaded = []
        failed = []
        for tool_name in required_tools:
            try:
                rm.build_resource_by_type(
                    ResourceType.Tool.value,
                    AgentResource(type=ResourceType.Tool.value, value=tool_name),
                )
                loaded.append(tool_name)
            except Exception as e:
                failed.append(f"{tool_name} ({e})")
        chunks = []
        if loaded:
            chunks.append(
                {"output_type": "text", "content": f"Loaded: {', '.join(loaded)}"}
            )
        if failed:
            chunks.append(
                {"output_type": "text", "content": f"Failed: {', '.join(failed)}"}
            )
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    @tool(
        description="Retrieve relevant information from the knowledge base. "
        "Use this tool when the user question involves content that may be "
        'in the knowledge base. Parameters: {{"query": "search query"}}'
    )
    async def legacy_knowledge_retrieve(query: str) -> str:
        if not knowledge_resources:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "No knowledge base available",
                        }
                    ]
                },
                ensure_ascii=False,
            )

        resource = knowledge_resources[0]
        try:
            chunks = await resource.retrieve(query)
            if chunks:
                content = "\n".join(
                    [f"[{i + 1}] {chunk.content}" for i, chunk in enumerate(chunks[:5])]
                )
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Retrieved {len(chunks)} relevant documents"
                                ),
                            },
                            {"output_type": "markdown", "content": content},
                        ]
                    },
                    ensure_ascii=False,
                )
            else:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": "No relevant information found",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
        except Exception as e:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"Knowledge retrieval failed: {str(e)}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    @tool(
        description=(
            "对用户选择的数据库执行 SQL 查询（仅支持 SELECT）。"
            '参数: {"sql": "SELECT 语句"}'
        )
    )
    def sql_query(sql: str) -> str:
        """Execute a read-only SQL query against the selected database."""
        if database_connector is None:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "未选择数据库，请先在左侧面板选择一个数据源。",
                        }
                    ]
                },
                ensure_ascii=False,
            )

        sql_stripped = sql.strip().rstrip(";")
        sql_upper = sql_stripped.upper().lstrip()
        forbidden = [
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "TRUNCATE",
            "CREATE",
            "GRANT",
            "REVOKE",
        ]
        for kw in forbidden:
            if sql_upper.startswith(kw):
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"安全限制: 不允许执行 {kw} 语句，"
                                    f"仅支持 SELECT 查询。"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        try:
            result = database_connector.run(sql_stripped)
            if not result:
                return json.dumps(
                    {
                        "chunks": [
                            {"output_type": "text", "content": "查询返回空结果。"}
                        ]
                    },
                    ensure_ascii=False,
                )

            # result[0] = column names, result[1:] = data rows
            columns = result[0]
            col_names = [str(c[0]) if isinstance(c, tuple) else str(c) for c in columns]
            rows = result[1:]

            # Build markdown table
            header = "| " + " | ".join(col_names) + " |"
            separator = "| " + " | ".join(["---"] * len(col_names)) + " |"
            md_rows = []
            for row in rows[:50]:
                md_rows.append("| " + " | ".join(str(v) for v in row) + " |")
            table = "\n".join([header, separator] + md_rows)
            if len(rows) > 50:
                table += f"\n\n（仅显示前 50 行，共 {len(rows)} 行）"

            return json.dumps(
                {"chunks": [{"output_type": "markdown", "content": table}]},
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"SQL 执行失败: {str(e)}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    # SQL/Python are semantic execution boundaries. Delegate them to the same
    # implementations used by the main agent so capture, safety, truncation,
    # and future fixes cannot diverge between parent and child agents.
    shared_sql_query = make_shared_sql_query(
        react_state,
        database_connector,
        max_output_chars=None,
    )
    shared_code_interpreter = make_shared_code_interpreter(
        react_state,
        max_output_chars=2_000,
        legacy_truncation_notice=True,
    )
    shared_knowledge_retrieve = make_shared_knowledge_retrieve(
        react_state, knowledge_resources or []
    )

    @tool(
        description="Retrieve relevant information from the knowledge base. "
        "Use this tool when the user question involves content that may be "
        'in the knowledge base. Parameters: {{"query": "search query"}}'
    )
    async def knowledge_retrieve(query: str) -> str:
        if get_current_artifact_sink() is None:
            return await legacy_knowledge_retrieve(query=query)
        return await shared_knowledge_retrieve(query=query)

    return {
        "load_skill": load_skill,
        "load_tools": load_tools,
        "knowledge_retrieve": knowledge_retrieve,
        "sql_query": shared_sql_query,
        "code_interpreter": shared_code_interpreter,
        "shell_interpreter": make_shell_interpreter(react_state),
        "execute_skill_script_file": make_execute_skill_script_file(react_state),
        "html_interpreter": make_html_interpreter(react_state, DEFAULT_SKILLS_DIR),
    }

```

## packages/dbgpt-core/src/dbgpt/agent/expand/actions/tool_action.py

Source: https://github.com/eosphoros-ai/DB-GPT/blob/9d50d0a55071a040a8cb7684b3b6acfa13cc7c47/packages/dbgpt-core/src/dbgpt/agent/expand/actions/tool_action.py

SHA-256: `5899cd2f1e83795a6f4c116def210be75bbf25b1a341c6217f6f8073e3767ad6`

```
"""Plugin Action Module."""

import asyncio
import dataclasses
import json
import logging
from typing import Any, Dict, List, Optional

from dbgpt._private.pydantic import BaseModel, Field
from dbgpt.vis.tags.vis_plugin import Vis, VisPlugin

from ...core.action.base import Action, ActionOutput
from ...core.context.storage import get_current_storage
from ...core.schema import Status
from ...resource.base import AgentResource, Resource, ResourceType
from ...resource.tool.pack import ToolPack

logger = logging.getLogger(__name__)


class ToolInput(BaseModel):
    """Plugin input model."""

    tool_name: str = Field(
        ...,
        description="The name of a tool that can be used to answer the current question"
        " or solve the current task.",
    )
    args: dict = Field(
        default={"arg name1": "", "arg name2": ""},
        description="The tool selected for the current target, the parameter "
        "information required for execution",
    )
    thought: str = Field(..., description="Summary of thoughts to the user")


class ToolAction(Action[ToolInput]):
    """Tool action class."""

    def __init__(self, **kwargs):
        """Tool action init."""
        super().__init__(**kwargs)
        self._render_protocol = VisPlugin()

    @property
    def resource_need(self) -> Optional[ResourceType]:
        """Return the resource type needed for the action."""
        return ResourceType.Tool

    @property
    def render_protocol(self) -> Optional[Vis]:
        """Return the render protocol."""
        return self._render_protocol

    @property
    def out_model_type(self):
        """Return the output model type."""
        return ToolInput

    @property
    def ai_out_schema(self) -> Optional[str]:
        """Return the AI output schema."""
        out_put_schema = {
            "thought": "Summary of thoughts to the user",
            "tool_name": "The name of a tool that can be used to answer the current "
            "question or solve the current task.",
            "args": {
                "arg name1": "arg value1",
                "arg name2": "arg value2",
            },
        }

        return f"""Please response in the following json format:
        {json.dumps(out_put_schema, indent=2, ensure_ascii=False)}
        Make sure the response is correct json and can be parsed by Python json.loads.
        """

    async def run(
        self,
        ai_message: str,
        resource: Optional[AgentResource] = None,
        rely_action_out: Optional[ActionOutput] = None,
        need_vis_render: bool = True,
        **kwargs,
    ) -> ActionOutput:
        """Perform the plugin action.

        Args:
            ai_message (str): The AI message.
            resource (Optional[AgentResource], optional): The resource. Defaults to
                None.
            rely_action_out (Optional[ActionOutput], optional): The rely action output.
                Defaults to None.
            need_vis_render (bool, optional): Whether need visualization rendering.
                Defaults to True.
        """
        try:
            param: ToolInput = self._input_convert(ai_message, ToolInput)
            return await run_tool(
                param.tool_name,
                param.args,
                self.resource,
                self.render_protocol,
                need_vis_render=need_vis_render,
            )
        except Exception as e:
            # If the LLM didn't produce the strict JSON schema, try some
            # pragmatic fallbacks so agents can still execute the intended tool:
            # 1) If the AI output is a plain numeric string, accept it as result.
            # 2) Try to extract a tool name and an `expression` enclosed in backticks
            #    (common pattern: "Use `calculate` with expression `10 * 99`").
            # 3) Otherwise return a helpful failure ActionOutput.
            logger.debug(
                "ToolAction JSON parse failed, attempting fallbacks: %s", str(e)
            )

            text = ai_message or ""
            text = text.strip().strip('"')

            # Fallback 1: pure numeric result
            import re

            if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
                return ActionOutput(
                    is_exe_success=True,
                    content=text,
                    observations=text,
                )

            # Fallback 2: extract tool name and expression in backticks
            tool_name = None
            expr = None
            m_tool = re.search(r"`([A-Za-z0-9_-]+)`", text)
            if m_tool:
                tool_name = m_tool.group(1)
            m_expr = re.search(r"expression\s+`([^`]+)`", text, re.IGNORECASE)
            if m_expr:
                expr = m_expr.group(1)

            # If we found an expression, call the tool with that expression
            if expr:
                try:
                    chosen_tool = tool_name or "calculate"
                    return await run_tool(
                        chosen_tool,
                        {"expression": expr},
                        self.resource,
                        self.render_protocol,
                        need_vis_render=need_vis_render,
                        raw_tool_input=None,
                    )
                except Exception:
                    logger.exception("Fallback tool execution failed")

            logger.exception((str(e)))
            return ActionOutput(
                is_exe_success=False,
                content="The requested correctly structured answer could not be found.",
            )


async def run_tool(
    name: str,
    args: dict,
    resource: Resource,
    render_protocol: Optional[Vis] = None,
    need_vis_render: bool = False,
    raw_tool_input: Optional[str] = None,
) -> ActionOutput:
    """Run the tool."""
    is_terminal = None
    try:
        tool_packs = ToolPack.from_resource(resource)
        if not tool_packs:
            raise ValueError("The tool resource is not found！")
        tool_pack: ToolPack = tool_packs[0]
        response_success = True
        status = Status.RUNNING.value
        err_msg = None

        if raw_tool_input and args is not None:
            try:
                parsed = tool_pack.parse_execute_args(
                    resource_name=name, input_str=raw_tool_input
                )
                if parsed and isinstance(parsed, tuple):
                    args = parsed[1]
                if args is not None and isinstance(args, list) and len(args) == 0:
                    args = {}
            except Exception:
                pass

        try:
            tool_result = await tool_pack.async_execute(resource_name=name, **args)
            status = Status.COMPLETE.value
            is_terminal = tool_pack.is_terminal(name)
        except Exception as e:
            response_success = False
            logger.exception(f"Tool [{name}] execute failed!")
            status = Status.FAILED.value
            err_msg = f"Tool [{name}] execute failed! {str(e)}"
            tool_result = err_msg

        result_str = str(tool_result)
        try:
            payload = (
                tool_result if isinstance(tool_result, dict) else json.loads(result_str)
            )
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and (
            payload.get("success") is False or payload.get("error_code")
        ):
            response_success = False
            status = Status.FAILED.value
            err_msg = str(payload.get("error_code") or "TOOL_FAILED")

        # Layer 2: persist oversized tool results to disk and replace the
        # in-context content with a <persisted-output> preview + file path.
        # The full content is retained in `observations` for DB persistence.
        persisted_path: Optional[str] = None
        in_context_content = result_str
        storage = get_current_storage()
        if storage is not None and response_success:
            try:
                replacement, persisted_path = storage.maybe_persist(
                    content=result_str,
                    tool_name=name,
                    tool_call_id=f"{name}_{id(tool_result):x}",
                )
                if persisted_path:
                    in_context_content = replacement
            except Exception:
                logger.exception(
                    "Tool result persistence failed for %s; using full content",
                    name,
                )
                persisted_path = None
                in_context_content = result_str

        plugin_param = {
            "name": name,
            "args": args,
            "status": status,
            "logo": None,
            "result": in_context_content,
            "err_msg": err_msg,
        }
        if render_protocol:
            view = await render_protocol.display(content=plugin_param)
        elif need_vis_render:
            raise NotImplementedError("The render_protocol should be implemented.")
        else:
            view = None

        return ActionOutput(
            is_exe_success=response_success,
            content=in_context_content,
            view=view,
            observations=result_str,
            terminate=is_terminal,
            persisted_path=persisted_path,
        )
    except Exception as e:
        logger.exception("Tool Action Run Failed！")
        return ActionOutput(
            is_exe_success=False,
            content=f"Tool action run failed!{str(e)}",
            terminate=is_terminal,
        )


@dataclasses.dataclass
class ToolCallSpec:
    """A single tool invocation for batch (parallel) execution.

    Attributes:
        name: The tool name, looked up in the agent's ``ToolPack``.
        args: The tool arguments (already resolved to a dict).
        call_id: Optional provider-native tool call id. Only populated in the
            stage-2 native-function-calling path; ``None`` for the text path.
        raw_tool_input: The raw model-produced argument string, used by
            :func:`run_tool` to re-parse arguments when ``args`` is empty.
    """

    name: str
    args: Dict[str, Any] = dataclasses.field(default_factory=dict)
    call_id: Optional[str] = None
    raw_tool_input: Optional[str] = None


async def run_tools_batch(
    specs: List[ToolCallSpec],
    resource: Resource,
    render_protocol: Optional[Vis] = None,
    need_vis_render: bool = False,
) -> ActionOutput:
    """Execute multiple tool calls concurrently and aggregate into one output.

    A one-element batch is forwarded verbatim to :func:`run_tool`, so batch
    mode is a strict superset of the legacy single-tool behaviour (no change to
    the existing per-tool path).

    Aggregation contract (mapping onto :class:`ActionOutput`):
      - is_exe_success: ``all`` — a failed tool surfaces to the model on the
        next retry round via the joined ``content``.
      - content: per-tool in-context previews joined by blank lines.
      - observations: JSON array of per-tool result dicts (round-trippable by
        the structured memory fragment / read_memories).
      - terminate: ``any`` terminal tool.
      - have_retry: ``any`` (defaults True for every tool).
      - view: concatenated vis-plugin blocks when rendering was requested.
    """
    if not specs:
        return ActionOutput(
            is_exe_success=False,
            content="No tool calls to execute.",
            observations="[]",
        )
    if len(specs) == 1:
        spec = specs[0]
        out = await run_tool(
            name=spec.name,
            args=spec.args,
            resource=resource,
            render_protocol=render_protocol,
            need_vis_render=need_vis_render,
            raw_tool_input=spec.raw_tool_input,
        )
        # run_tool omits `action` (unlike the multi-branch below) — backfill it
        # so native single-tool turns still carry the tool name on ActionOutput.
        if not getattr(out, "action", None):
            out.action = spec.name
        return out

    async def _run_one(spec: ToolCallSpec) -> ActionOutput:
        try:
            out: ActionOutput = await run_tool(
                name=spec.name,
                args=spec.args,
                resource=resource,
                render_protocol=render_protocol,
                need_vis_render=need_vis_render,
                raw_tool_input=spec.raw_tool_input,
            )
        except Exception as e:  # pragma: no cover - run_tool already guards
            out = ActionOutput(
                is_exe_success=False,
                content=f"Tool [{spec.name}] execute failed! {str(e)}",
                observations=f"Tool [{spec.name}] execute failed! {str(e)}",
            )
        if not out.action:
            out.action = spec.name
        return out

    outputs: List[ActionOutput] = list(
        await asyncio.gather(
            *(_run_one(spec) for spec in specs), return_exceptions=True
        )
    )

    # Normalise exceptions returned by gather (defensive; _run_one already
    # converts its own failures into a failure ActionOutput).
    normalized: List[ActionOutput] = []
    for spec, out in zip(specs, outputs):
        if isinstance(out, BaseException):
            normalized.append(
                ActionOutput(
                    is_exe_success=False,
                    content=f"Tool [{spec.name}] execute failed! {out}",
                    observations=f"Tool [{spec.name}] execute failed! {out}",
                    action=spec.name,
                )
            )
        else:
            normalized.append(out)

    is_exe_success = all(o.is_exe_success for o in normalized)
    terminate = any(o.terminate is True for o in normalized)
    have_retry = any(o.have_retry for o in normalized)

    content = "\n\n".join(o.content for o in normalized)
    views = [o.view for o in normalized if o.view]

    tool_results: List[Dict[str, Any]] = [
        {
            "name": spec.name,
            "call_id": spec.call_id,
            "success": out.is_exe_success,
            "content": out.content,
            "observation": out.observations,
            "terminate": out.terminate,
            "persisted_path": out.persisted_path,
        }
        for spec, out in zip(specs, normalized)
    ]

    return ActionOutput(
        is_exe_success=is_exe_success,
        content=content,
        view="\n".join(views) if views else None,
        action=", ".join(spec.name for spec in specs),
        action_input=json.dumps([spec.args for spec in specs], ensure_ascii=False),
        observations=json.dumps(tool_results, ensure_ascii=False),
        have_retry=have_retry,
        terminate=terminate if terminate else None,
    )

```

## packages/dbgpt-core/src/dbgpt/agent/expand/react_agent.py

Source: https://github.com/eosphoros-ai/DB-GPT/blob/9d50d0a55071a040a8cb7684b3b6acfa13cc7c47/packages/dbgpt-core/src/dbgpt/agent/expand/react_agent.py

SHA-256: `3c246365517090d119697ec994cabd5267f2071f16115c779338623db7dc1bee`

```
import json
import logging
from typing import Any, Dict, List, Optional, Type, Union

from dbgpt._private.pydantic import Field
from dbgpt.agent import (
    ActionOutput,
    Agent,
    AgentMemoryFragment,
    AgentMessage,
    ConversableAgent,
    ProfileConfig,
    Resource,
    ResourceType,
    StructuredAgentMemoryFragment,
)
from dbgpt.agent.core.role import AgentRunMode
from dbgpt.agent.resource import BaseTool, ResourcePack, ToolPack
from dbgpt.agent.util.react_parser import ReActOutputParser
from dbgpt.util.configure import DynConfig

from ...core import ModelMessageRoleType
from .actions.react_action import ReActAction, Terminate

logger = logging.getLogger(__name__)

_REACT_DEFAULT_GOAL = """Answer the following questions or solve the tasks by \
selecting the right ACTION from the ACTION SPACE as best as you can. 
# ACTION SPACE Simple Description #
{{ action_space_simple_desc }}
"""

_REACT_SYSTEM_TEMPLATE = """\
You are a {{ role }}, {% if name %}named {{ name }}. {% endif %}\
{{ goal }}
task. The maximum number of steps you can take is {{ max_steps }}.
Do not output an empty string!
{% if native_function_calling %}
# RESPONSE FORMAT #
Use the supplied native functions for actions, with JSON arguments.
Call a function to perform the next step; use intention/thought for a short explanation.
When the task is complete, call terminate with the final answer in output.
{% else %}
{{ action_space }}
# RESPONSE FORMAT #
IMPORTANT:
- You must never answer directly outside the ReAct format.
- Every response must contain exactly one Action and one Action Input.
- If the task is complete, use exactly:
Thought: ...
Phase: 返回最终结果
Action: terminate
Action Input: {"result": "final answer"}
- Do not put the final answer as plain markdown outside Action Input.

For each task input, your response should contain:
1. One analysis of the task and the current environment, reasoning to determine the \
next action (prefix "Thought: ").
2. What this step is trying to do (prefix "Action Intention: "), short and \
user-facing.
3. Why this action is needed now (prefix "Action Reason: "), short and \
user-facing.
4. One action string in the ACTION SPACE (prefix "Action: "), should be one of \
[{{ action_space_names }}].
5. One action input (prefix "Action Input: "), empty if no input is required.
# EXAMPLE INTERACTION #
Thought: ...(Your analysis of the task and reasoning for the next action.)
Action Intention: ...(What this step will do, e.g. "探索数据结构")
Action Reason: ...(Why this action is needed now)
Action: ...
Action Input: ...
Observation: ...(This is output provided by the external environment or Action output, \
you are not allowed to generate it.)
{% endif %}
{% if task_progress %}
{{ task_progress }}
You MUST NOT repeat any action already listed above as ✅ completed.
Pick the NEXT action that has NOT been done yet to make progress toward the final goal.
{% endif %}
Please Solve this task:
{{ question }}\
Please answer in the same language as the user's question.
The current time is: {{ now_time }}.
"""

# The first question-bearing memory fragment can be evicted during long tasks.
# Keep the current objective outside the rolling tool-observation buffer.
_REACT_USER_TEMPLATE = """Question: {{ question }}"""


_REACT_WRITE_MEMORY_TEMPLATE = """\
{% if question %}Question: {{ question }} {% endif %}
{% if thought %}Thought: {{ thought }} {% endif %}
{% if phase %}Phase: {{ phase }} {% endif %}
{% if action_intention %}Action Intention: {{ action_intention }} {% endif %}
{% if action_reason %}Action Reason: {{ action_reason }} {% endif %}
{% if action %}Action: {{ action }} {% endif %}
{% if action_input %}Action Input: {{ action_input }} {% endif %}
{% if observation %}Observation: {{ observation }} {% endif %}
"""


class ReActAgent(ConversableAgent):
    max_retry_count: int = 30
    run_mode: AgentRunMode = AgentRunMode.LOOP

    profile: ProfileConfig = ProfileConfig(
        name=DynConfig(
            "ReAct",
            category="agent",
            key="dbgpt_agent_expand_plugin_assistant_agent_name",
        ),
        role=DynConfig(
            "ReActToolMaster",
            category="agent",
            key="dbgpt_agent_expand_plugin_assistant_agent_role",
        ),
        goal=DynConfig(
            _REACT_DEFAULT_GOAL,
            category="agent",
            key="dbgpt_agent_expand_plugin_assistant_agent_goal",
        ),
        system_prompt_template=_REACT_SYSTEM_TEMPLATE,
        user_prompt_template=_REACT_USER_TEMPLATE,
        write_memory_template=_REACT_WRITE_MEMORY_TEMPLATE,
    )
    parser: ReActOutputParser = Field(default_factory=ReActOutputParser)

    def __init__(self, **kwargs):
        """Init indicator AssistantAgent."""
        super().__init__(**kwargs)

        self._init_actions([ReActAction, Terminate])

        # Auto-enable multi-layer context management if configured
        if (
            self.agent_context is not None
            and self.agent_context.enable_context_management
        ):
            self.init_context_management()

    async def _a_init_reply_message(
        self,
        received_message: AgentMessage,
        rely_messages: Optional[List[AgentMessage]] = None,
    ) -> AgentMessage:
        reply_message = super()._init_reply_message(received_message, rely_messages)

        tool_packs = ToolPack.from_resource(self.resource)
        action_space = []
        action_space_names = []
        action_space_simple_desc = []
        if tool_packs:
            tool_pack = tool_packs[0]
            for tool in tool_pack.sub_resources:
                tool_desc, _ = await tool.get_prompt(lang=self.language)
                action_space_names.append(tool.name)
                action_space.append(tool_desc)
                if isinstance(tool, BaseTool):
                    tool_simple_desc = tool.description
                else:
                    tool_simple_desc = tool.get_prompt()
                action_space_simple_desc.append(f"{tool.name}: {tool_simple_desc}")
        else:
            for action in self.actions:
                action_space_names.append(action.name)
                action_space.append(action.get_action_description())
            # self.actions
        reply_message.context = {
            "native_function_calling": bool(
                self.agent_context and self.agent_context.enable_native_function_calling
            ),
            "max_steps": self.max_retry_count,
            "action_space": "\n".join(action_space),
            "action_space_names": ", ".join(action_space_names),
            "action_space_simple_desc": "\n".join(action_space_simple_desc),
        }
        return reply_message

    async def preload_resource(self) -> None:
        await super().preload_resource()
        self._check_and_add_terminate()

    def _check_and_add_terminate(self):
        if not self.resource:
            return
        _is_has_terminal = False

        def _has_terminal(r: Resource):
            nonlocal _is_has_terminal
            if r.type() == ResourceType.Tool and isinstance(r, Terminate):
                _is_has_terminal = True
            return r

        _has_add_terminal = False

        def _add_terminate(r: Resource):
            nonlocal _has_add_terminal
            if not _has_add_terminal and isinstance(r, ResourcePack):
                terminal = Terminate()
                r._resources[terminal.name] = terminal
                _has_add_terminal = True
            return r

        self.resource.apply(apply_func=_has_terminal)
        if not _is_has_terminal:
            # Add terminal action to the resource
            self.resource.apply(apply_pack_func=_add_terminate)

    async def load_resource(self, question: str, is_retry_chat: bool = False):
        """Load agent bind resource."""
        if self.resource:

            def _remove_tool(r: Resource):
                if r.type() == ResourceType.Tool:
                    return None
                return r

            # Remove all tools from the resource
            # We will handle tools separately
            new_resource = self.resource.apply(apply_func=_remove_tool)
            if new_resource:
                resource_prompt, resource_reference = await new_resource.get_prompt(
                    lang=self.language, question=question
                )
                return resource_prompt, resource_reference
        return None, None

    def prepare_act_param(
        self,
        received_message: Optional[AgentMessage],
        sender: Agent,
        rely_messages: Optional[List[AgentMessage]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Prepare the parameters for the act method."""
        return {
            "parser": self.parser,
        }

    async def act(
        self,
        message: AgentMessage,
        sender: Agent,
        reviewer: Optional[Agent] = None,
        is_retry_chat: bool = False,
        last_speaker_name: Optional[str] = None,
        **kwargs,
    ) -> ActionOutput:
        """Perform actions."""
        message_content = message.content
        if not message_content:
            raise ValueError("The response is empty.")
        try:
            steps = self.parser.parse_current_step(message_content)
            err_msg = None
            if not steps:
                err_msg = (
                    "No correct response found. Please check your response, which must"
                    " be in the format indicated in the system prompt."
                )
            elif len(steps) != 1:
                err_msg = "Only one action is allowed each time."
            if err_msg:
                return ActionOutput(is_exe_success=False, content=err_msg)
        except Exception as e:
            logger.warning(f"review error: {e}")

        action_output = await super().act(
            message=message,
            sender=sender,
            reviewer=reviewer,
            is_retry_chat=is_retry_chat,
            last_speaker_name=last_speaker_name,
            **kwargs,
        )
        return action_output

    @property
    def memory_fragment_class(self) -> Type[AgentMemoryFragment]:
        """Return the memory fragment class."""
        return StructuredAgentMemoryFragment

    async def read_memories(
        self,
        observation: str,
    ) -> Union[str, List["AgentMessage"]]:
        memories = await self.memory.read(observation)
        not_json_memories = []
        messages = []
        structured_memories = []
        # Pair each parsed dict with its originating fragment so we can access
        # snapshot_path later.
        fragment_by_mem: dict = {}
        for m in memories:
            if m.raw_observation:
                try:
                    mem_dict = json.loads(m.raw_observation)
                    if isinstance(mem_dict, dict):
                        structured_memories.append(mem_dict)
                        fragment_by_mem[id(mem_dict)] = m
                    elif isinstance(mem_dict, list):
                        for item in mem_dict:
                            structured_memories.append(item)
                            fragment_by_mem[id(item)] = m
                    else:
                        raise ValueError("Invalid memory format.")
                except Exception:
                    not_json_memories.append(m.raw_observation)

        for mem_dict in structured_memories:
            fragment = fragment_by_mem.get(id(mem_dict))
            snapshot_path = getattr(fragment, "snapshot_path", None)

            question = mem_dict.get("question")
            thought = mem_dict.get("thought")
            phase = mem_dict.get("phase")
            action = mem_dict.get("action")
            action_input = mem_dict.get("action_input")
            observation = mem_dict.get("observation")
            persisted_path = mem_dict.get("persisted_path")
            if question:
                messages.append(
                    AgentMessage(
                        content=f"Question: {question}",
                        role=ModelMessageRoleType.HUMAN,
                    )
                )
            ai_content = []
            if thought:
                ai_content.append(f"Thought: {thought}")
            if phase:
                ai_content.append(f"Phase: {phase}")
            if action:
                ai_content.append(f"Action: {action}")
            if action_input:
                ai_content.append(f"Action Input: {action_input}")
            messages.append(
                AgentMessage(
                    content="\n".join(ai_content),
                    role=ModelMessageRoleType.AI,
                )
            )

            if observation:
                # If the observation is a <persisted-output> preview block it
                # already carries the file path, so we don't append the
                # snapshot suffix. We still record snapshot_path / persisted_path
                # in the message context so Layer 1 compaction can recognize
                # size-bounded observations and skip them.
                is_persisted = "<persisted-output>" in observation
                obs_context: dict = {}
                if snapshot_path:
                    obs_context["snapshot_path"] = snapshot_path
                if persisted_path:
                    obs_context["persisted_path"] = persisted_path
                obs_suffix = ""
                if not is_persisted and snapshot_path:
                    obs_suffix = f"\n[Full detail available at: {snapshot_path}]"
                messages.append(
                    AgentMessage(
                        content=f"Observation: {observation}{obs_suffix}",
                        role=ModelMessageRoleType.HUMAN,
                        context=obs_context if obs_context else None,
                    )
                )

        if not messages and not_json_memories:
            messages.append(
                AgentMessage(
                    content="\n".join(not_json_memories),
                    role=ModelMessageRoleType.HUMAN,
                )
            )
        return messages

```
