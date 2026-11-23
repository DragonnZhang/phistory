# eigent — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## backend/app/agent/factory/toolkit_assembler.py

Source: https://github.com/eigent-ai/eigent/blob/9840d8aeb304b4af62838331a35c818f128a7be9/backend/app/agent/factory/toolkit_assembler.py

SHA-256: `41f96eb8bccc26cdfd8d94ad11080e2ef6a1a12801d18ffa43c552b211821bcd`

```
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========

import asyncio
import inspect
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from camel.toolkits import (
    FunctionTool,
    MCPToolkit,
    PlanningWorktreeToolkit,
    RegisteredAgentToolkit,
    ToolkitMessageIntegration,
    WebFetchToolkit,
)

from app.agent.toolkit.depth_limited_agent_toolkit import (
    DepthLimitedAgentToolkit,
)
from app.agent.toolkit.file_write_toolkit import FileToolkit
from app.agent.toolkit.human_toolkit import HumanToolkit
from app.agent.toolkit.hybrid_browser_toolkit import HybridBrowserToolkit
from app.agent.toolkit.memory_toolkit import add_memory_tools
from app.agent.toolkit.observable_todo_toolkit import ObservableTodoToolkit
from app.agent.toolkit.screenshot_toolkit import ScreenshotToolkit
from app.agent.toolkit.search_toolkit import SearchToolkit
from app.agent.toolkit.skill_toolkit import SkillToolkit
from app.agent.toolkit.terminal_toolkit import (
    TerminalToolkit,
    is_secret_broker_environment_key,
)
from app.agent.toolkit.workspace_git_toolkit import WorkspaceGitToolkit
from app.component.environment import env
from app.hands.interface import IHands
from app.model.chat import Chat
from app.run_policy import ToolSafetyClass
from app.run_runtime.tool_checkpoint import declare_tool_safety
from app.service.task import Agents, get_task_lock_if_exists
from app.utils.browser_launcher import normalize_cdp_url
from app.utils.workspace_paths import runtime_task_root
from app.workspace_bundle.runtime import ResolvedRuntimeEnvironment

logger = logging.getLogger("toolkit_assembler")


def _normalize_toolkit_name(value: str) -> str:
    return "".join(
        character for character in value.casefold() if character.isalnum()
    )


_SAFE_READ_TOOLKIT_FUNCTIONS: dict[str, frozenset[str] | None] = {
    # None means every exposed function in this code-owned toolkit is read-only.
    "searchtoolkit": None,
    "webfetchtoolkit": None,
    "screenshottoolkit": frozenset({"read_image"}),
    "workspacegittoolkit": None,
    # Exact code-owned discovery functions. Loading skill instructions can
    # influence a later action, but it does not perform that action; every
    # subsequent tool call still crosses its own permission checkpoint.
    "skilltoolkit": frozenset({"list_skills", "load_skill"}),
    # Only inspection methods are trusted here. File mutation methods remain
    # conservative and continue through the normal policy decision.
    "filetoolkit": frozenset(
        {"read_file", "read_files", "search_files", "glob_files", "grep_files"}
    ),
    # Both functions stay inside Eigent's task UI. Asking creates a durable
    # HumanInteraction; notifying only publishes Run-local progress. Neither
    # mutates the user workspace nor an external service, so prompting for an
    # `mcp.tool.write` approval would be a false safety boundary. Third-party
    # tools with the same names remain conservative because declarations are
    # scoped to this code-owned toolkit.
    "humantoolkit": frozenset({"ask_human_via_gui", "send_message_to_user"}),
    # Eigent's code-owned todo_write only replaces the Run-local todo.md and
    # .todo.json projection. It is deterministic internal progress metadata,
    # not a user-workspace or external side effect, so approval would only
    # interrupt normal planning (and every resumed attempt) without adding a
    # meaningful safety boundary. Third-party tools with the same function
    # name remain conservative because declarations are scoped by toolkit.
    "todotoolkit": frozenset({"todo_write", "step_update"}),
    # This code-owned operation can only stop a TerminalToolkit session that
    # Eigent previously registered under the supplied logical session id. It
    # cannot target an arbitrary process/PID, and repeating it after a restart
    # converges to the same "session is no longer running" state. Treat it as
    # replay-safe so an interrupted cleanup cannot permanently block Resume.
    # Third-party tools with the same function name remain conservative because
    # trusted declarations are scoped to the code-owned toolkit name.
    "terminaltoolkit": frozenset({"shell_kill_process"}),
    # HybridBrowserToolkit intentionally publishes itself as "Browser Toolkit".
    "browsertoolkit": frozenset(
        {
            "browser_console_view",
            "browser_get_page_snapshot",
            "browser_sheet_read",
        }
    ),
    "hybridbrowsertoolkit": frozenset(
        {
            "browser_console_view",
            "browser_get_page_snapshot",
            "browser_sheet_read",
        }
    ),
}

DEFAULT_SINGLE_AGENT_TOOLKIT_CONFIG: dict[str, Any] = {
    "human": {"enabled": True},
    "file": {"enabled": True},
    "screenshot": {"enabled": True},
    "skill": {"enabled": True},
    "todo": {"enabled": True},
    "search": {"enabled": True},
    "browser": {"enabled": True},
    "terminal": {"enabled": True},
    "workspace_git": {"enabled": True},
    "web_fetch": {"enabled": True},
    "planning_worktree": {"enabled": True},
    "mcp": {"enabled": True},
    "agent": {"enabled": True},
}


@dataclass
class ToolkitAssembly:
    tools: list[FunctionTool | Callable] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    toolkits_to_register_agent: list[RegisteredAgentToolkit] = field(
        default_factory=list
    )
    cleanup_toolkits: list[Any] = field(default_factory=list)
    observable_todo_toolkit: ObservableTodoToolkit | None = None
    browser_toolkit: HybridBrowserToolkit | None = None
    browser_port: int | None = None
    browser_cdp_url: str | None = None
    browser_session_id: str | None = None
    browser_owned_by_hands: bool = False

    def add_tools(
        self,
        tools: list[FunctionTool | Callable],
        toolkit_name: str,
    ) -> None:
        if not tools:
            return
        _tag_tools(tools, toolkit_name)
        self.tools.extend(tools)
        if toolkit_name not in self.tool_names:
            self.tool_names.append(toolkit_name)


async def _rollback_runtime_assembly(
    assembly: ToolkitAssembly,
    *,
    project_id: str,
    options: Chat,
    hands: IHands | None,
) -> None:
    """Best-effort rollback when assembly or Agent creation aborts."""

    candidates = list(assembly.cleanup_toolkits)
    if (
        assembly.browser_toolkit is not None
        and assembly.browser_toolkit not in candidates
    ):
        candidates.append(assembly.browser_toolkit)
    disposed: list[Any] = []
    for toolkit in reversed(candidates):
        try:
            cleanup = getattr(toolkit, "disconnect", None)
            if cleanup is None:
                cleanup = getattr(toolkit, "cleanup_tab_tracking", None)
            if cleanup is None:
                cleanup = getattr(toolkit, "cleanup", None)
            if cleanup is not None:
                # Terminal cleanup waits for process groups to stop. Keep that
                # wait off the event loop shared by every other Session.
                outcome = (
                    cleanup()
                    if inspect.iscoroutinefunction(cleanup)
                    else await asyncio.to_thread(cleanup)
                )
                if inspect.isawaitable(outcome):
                    await outcome
            disposed.append(toolkit)
        except Exception:
            logger.exception(
                "Failed to roll back partial Bundle toolkit assembly",
                extra={
                    "project_id": project_id,
                    "toolkit": type(toolkit).__name__,
                },
            )

    task_lock = get_task_lock_if_exists(project_id)
    if task_lock is not None and disposed:
        task_lock.registered_toolkits = [
            toolkit
            for toolkit in task_lock.registered_toolkits
            if toolkit not in disposed
        ]

    if assembly.browser_session_id is not None:
        if assembly.browser_owned_by_hands and hands is not None:
            try:
                hands.release_resource(
                    "browser",
                    assembly.browser_session_id,
                )
            except Exception:
                logger.exception(
                    "Failed to release partial Bundle browser resource",
                    extra={"project_id": project_id},
                )
        elif options.cdp_browsers and assembly.browser_port is not None:
            try:
                from app.agent.factory.browser import _cdp_pool_manager

                _cdp_pool_manager.release_browser(
                    assembly.browser_port,
                    assembly.browser_session_id,
                )
            except Exception:
                logger.exception(
                    "Failed to release partial Bundle CDP reservation",
                    extra={"project_id": project_id},
                )


def _merged_config(options: Chat) -> dict[str, Any]:
    config = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in DEFAULT_SINGLE_AGENT_TOOLKIT_CONFIG.items()
    }
    for key, value in (options.toolkit_config or {}).items():
        config[key] = value
    return config


def _enabled(config: dict[str, Any], name: str, default: bool = True) -> bool:
    value = config.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return bool(value.get("enabled", default))
    return bool(value)


def _options(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key != "enabled"}


def _tag_tools(
    tools: list[FunctionTool | Callable], toolkit_name: str
) -> None:
    safe_functions = _SAFE_READ_TOOLKIT_FUNCTIONS.get(
        _normalize_toolkit_name(toolkit_name), frozenset()
    )
    for tool in tools:
        try:
            tool._toolkit_name = toolkit_name
        except Exception:
            pass
        function_name = (
            tool.get_function_name()
            if hasattr(tool, "get_function_name")
            else getattr(tool, "__name__", "")
        )
        if _normalize_toolkit_name(
            toolkit_name
        ) == "agenttoolkit" and function_name in {
            "agent_run_subagent",
            "agent_get_task_output",
            "agent_stop_task",
        }:
            # Delegation is a code-owned control-plane operation. It is not a
            # read and is deliberately not replay-safe, so keep it distinct
            # from SAFE_READ. Child tool calls still receive independent
            # checkpoints and DepthLimitedAgentToolkit prevents recursion.
            declare_tool_safety(tool, ToolSafetyClass.INTERNAL_CONTROL)
        elif safe_functions is None or function_name in safe_functions:
            declare_tool_safety(tool, ToolSafetyClass.SAFE_READ)


def _get_browser_port(browser: dict) -> int:
    raw_port = browser.get("port")
    if raw_port is not None:
        return int(raw_port)

    raw_endpoint = browser.get("endpoint") or browser.get("cdp_url")
    if raw_endpoint:
        _, _, port = normalize_cdp_url(str(raw_endpoint))
        return port

    return int(env("browser_port", "9222"))


def _get_browser_endpoint(browser: dict) -> str:
    raw_endpoint = browser.get("endpoint") or browser.get("cdp_url")
    if raw_endpoint:
        endpoint, _, _ = normalize_cdp_url(str(raw_endpoint))
        return endpoint

    return f"http://localhost:{_get_browser_port(browser)}"


def _browser_enabled_tools() -> list[str]:
    return [
        "browser_click",
        "browser_type",
        "browser_back",
        "browser_forward",
        "browser_select",
        "browser_console_exec",
        "browser_console_view",
        "browser_switch_tab",
        "browser_enter",
        "browser_visit_page",
        "browser_scroll",
        "browser_sheet_read",
        "browser_sheet_input",
        "browser_get_page_snapshot",
        "browser_open",
        "browser_upload_file",
        "browser_download_file",
    ]


def _mcp_config(
    options: Chat,
    hands: IHands | None,
    *,
    exact_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    source = (
        exact_config if exact_config is not None else options.installed_mcp
    )
    servers = dict((source or {}).get("mcpServers", {}))
    if not servers:
        return None

    if hands is not None:
        servers = {
            name: cfg
            for name, cfg in servers.items()
            if hands.can_use_mcp(name)
        }
        if not servers:
            logger.info("Skipping MCPToolkit: no MCP servers allowed")
            return None

    normalized_servers = {}
    for name, cfg in servers.items():
        server_cfg = dict(cfg)
        server_env = {
            key: value
            for key, value in dict(server_cfg.get("env", {})).items()
            if not is_secret_broker_environment_key(key)
        }
        if exact_config is None:
            server_env.setdefault(
                "MCP_REMOTE_CONFIG_DIR",
                env(
                    "MCP_REMOTE_CONFIG_DIR",
                    os.path.expanduser("~/.mcp-auth"),
                ),
            )
        server_cfg["env"] = server_env
        normalized_servers[name] = server_cfg

    return {"mcpServers": normalized_servers}


async def assemble_single_agent_toolkits(
    options: Chat,
    **kwargs,
) -> ToolkitAssembly:
    assembly = ToolkitAssembly()
    try:
        return await _assemble_single_agent_toolkits(
            options, assembly=assembly, **kwargs
        )
    except BaseException:
        # A failure or cancellation can arrive after the Browser reservation,
        # Terminal or MCP servers were created. Nothing else owns them yet.
        await _rollback_runtime_assembly(
            assembly,
            project_id=options.project_id,
            options=options,
            hands=kwargs.get("hands"),
        )
        raise


async def _assemble_single_agent_toolkits(
    options: Chat,
    *,
    assembly: ToolkitAssembly,
    task_id: str,
    working_directory: str,
    hands: IHands | None,
    can_delegate: bool,
    current_depth: int = 0,
    max_depth: int = 1,
    runtime_environment: ResolvedRuntimeEnvironment | None = None,
) -> ToolkitAssembly:
    config = _merged_config(options)
    pinned_skill_sources = (
        runtime_environment.pinned_skill_sources(Agents.single_agent)
        if runtime_environment is not None
        else None
    )
    pinned_mcp_config = (
        runtime_environment.mcp_config_without_secrets()
        if runtime_environment is not None
        else None
    )
    if runtime_environment is not None and hands is not None:
        denied_mcp_servers = [
            name
            for name in (pinned_mcp_config or {}).get("mcpServers", {})
            if not hands.can_use_mcp(name)
        ]
        if denied_mcp_servers:
            from app.workspace_bundle.runtime import (
                EnvironmentSetupRequiredError,
            )

            # This preflight must happen before Browser, Terminal, or MCP
            # construction so a denied pinned server cannot leak resources.
            raise EnvironmentSetupRequiredError(
                [f"mcp_not_allowed:{name}" for name in denied_mcp_servers]
            )

    human_toolkit = HumanToolkit(options.project_id, Agents.single_agent)
    message_integration = ToolkitMessageIntegration(
        message_handler=human_toolkit.send_message_to_user
    )

    if _enabled(config, "human"):
        assembly.add_tools(
            human_toolkit.get_tools(), HumanToolkit.toolkit_name()
        )

    # Protocol capability: settings may disable Memory capture/use, but the
    # typed management tools and bounded canonical History Search stay present.
    add_memory_tools(
        tools=assembly.tools,
        tool_names=assembly.tool_names,
        api_task_id=options.project_id,
        agent_name=Agents.single_agent,
    )

    if _enabled(config, "file"):
        file_options = {
            "working_directory": working_directory,
            **_options(config, "file"),
        }
        toolkit = FileToolkit(
            options.project_id,
            **file_options,
        )
        toolkit.agent_name = Agents.single_agent
        toolkit = message_integration.register_toolkits(toolkit)
        assembly.add_tools(toolkit.get_tools(), FileToolkit.toolkit_name())

    if _enabled(config, "screenshot"):
        screenshot_options = {
            "working_directory": working_directory,
            "agent_name": Agents.single_agent,
            **_options(config, "screenshot"),
        }
        toolkit = ScreenshotToolkit(
            options.project_id,
            **screenshot_options,
        )
        assembly.toolkits_to_register_agent.append(toolkit)
        registered = message_integration.register_toolkits(toolkit)
        assembly.add_tools(
            registered.get_tools(), ScreenshotToolkit.toolkit_name()
        )

    if _enabled(config, "skill") or bool(pinned_skill_sources):
        skill_options = {
            "working_directory": working_directory,
            "user_id": options.skill_config_user_id(),
            **_options(config, "skill"),
        }
        if runtime_environment is not None:
            # Immutable Bundle inputs are runtime authority, never request
            # customization. Legacy sessions retain their existing options.
            skill_options["working_directory"] = working_directory
            skill_options["pinned_skill_sources"] = pinned_skill_sources
        toolkit = SkillToolkit(
            options.project_id,
            Agents.single_agent,
            **skill_options,
        )
        toolkit = message_integration.register_toolkits(toolkit)
        assembly.add_tools(toolkit.get_tools(), SkillToolkit.toolkit_name())

    if _enabled(config, "todo"):

        def todo_working_dir_for_task(run_id: str):
            return (
                runtime_task_root(
                    options.email,
                    options.project_id,
                    run_id,
                    options.user_id,
                )
                / "todo"
            )

        todo_options = {
            **_options(config, "todo"),
            # Todo state is Run metadata, not a file in the shared Space
            # checkout. Resume reuses this directory; a follow-up Run gets a
            # separate directory even when its Agent instance is reused.
            "working_dir": str(todo_working_dir_for_task(task_id)),
            "working_dir_for_task": todo_working_dir_for_task,
        }
        todo_toolkit = ObservableTodoToolkit(
            api_task_id=options.project_id,
            task_id=task_id,
            **todo_options,
        )
        todo_toolkit.agent_name = Agents.single_agent
        assembly.observable_todo_toolkit = todo_toolkit
        assembly.add_tools(
            todo_toolkit.get_tools(), ObservableTodoToolkit.toolkit_name()
        )

    if _enabled(config, "search"):
        search_tools = SearchToolkit.get_can_use_tools(
            options.project_id, agent_name=Agents.single_agent
        )
        if search_tools:
            search_tools = message_integration.register_functions(search_tools)
            assembly.add_tools(search_tools, SearchToolkit.toolkit_name())

    electron_runtime = env("EIGENT_RUNTIME", "").lower().strip() == "electron"
    has_owned_electron_target = any(
        browser.get("managedBy") == "electron" and browser.get("targetUrl")
        for browser in options.cdp_browsers
    )
    if (
        _enabled(config, "browser")
        and (hands is None or hands.can_use_browser())
        and (not electron_runtime or has_owned_electron_target)
    ):
        toolkit_session_id = str(uuid.uuid4())[:8]
        selected_port: int | None = None
        cdp_url: str | None = None
        cdp_owned_by_hands = False
        owned_target_url: str | None = None
        browser_target_available = True

        if options.cdp_browsers:
            # Reuse the same pool as the Browser Agent so concurrent projects
            # do not accidentally claim the same CDP browser tab set.
            from app.agent.factory.browser import _cdp_pool_manager

            selected_browser = _cdp_pool_manager.acquire_browser(
                options.cdp_browsers,
                toolkit_session_id,
                options.task_id,
            )
            if selected_browser is None:
                if electron_runtime:
                    browser_target_available = False
                    logger.warning(
                        "No unused Eigent embedded browser target is "
                        "available; Browser Toolkit will remain disabled",
                        extra={
                            "project_id": options.project_id,
                            "task_id": options.task_id,
                        },
                    )
                else:
                    selected_browser = options.cdp_browsers[0]
                    logger.warning(
                        "No available CDP browser in pool for Single Agent; "
                        "using first browser",
                        extra={
                            "project_id": options.project_id,
                            "task_id": options.task_id,
                        },
                    )
            if selected_browser is not None:
                selected_port = _get_browser_port(selected_browser)
                cdp_url = _get_browser_endpoint(selected_browser)
                owned_target_url = selected_browser.get("targetUrl")
        else:
            existing_cdp_url = env("EIGENT_CDP_URL", "").strip()
            selected_port = int(env("browser_port", "9222"))
            cdp_url = f"http://localhost:{selected_port}"
            if existing_cdp_url:
                cdp_url = existing_cdp_url
                try:
                    parsed = urlparse(existing_cdp_url)
                    if parsed.port is not None:
                        selected_port = parsed.port
                except Exception:
                    selected_port = int(env("browser_port", "9222"))
            elif hands is not None:
                try:
                    cdp_url = hands.acquire_resource(
                        "browser", toolkit_session_id, port=selected_port
                    )
                    cdp_owned_by_hands = True
                except (NotImplementedError, ValueError):
                    cdp_url = f"http://localhost:{selected_port}"

        if browser_target_available:
            cdp_keep_current = bool(options.cdp_browsers)
            default_start_url = None if cdp_keep_current else "about:blank"
            browser_options = {
                "cdp_keep_current_page": cdp_keep_current,
                "default_start_url": default_start_url,
                "headless": False,
                "browser_log_to_file": True,
                "session_id": toolkit_session_id,
                "cdp_url": cdp_url,
                "enabled_tools": _browser_enabled_tools(),
                **_options(config, "browser"),
                # Host-owned target identity is an admission fact, not Bundle
                # configuration. Keep it authoritative over manifest options.
                "owned_target_url": owned_target_url,
                "stealth": not bool(owned_target_url),
            }
            # Record the reservation first, so rollback can return it even
            # if the toolkit cannot be constructed.
            assembly.browser_port = selected_port
            assembly.browser_cdp_url = cdp_url
            assembly.browser_session_id = toolkit_session_id
            assembly.browser_owned_by_hands = cdp_owned_by_hands
            toolkit = HybridBrowserToolkit(
                options.project_id, **browser_options
            )
            toolkit.agent_name = Agents.single_agent
            assembly.browser_toolkit = toolkit
            assembly.toolkits_to_register_agent.append(toolkit)
            registered = message_integration.register_toolkits(toolkit)
            assembly.add_tools(
                registered.get_tools(), HybridBrowserToolkit.toolkit_name()
            )

    if _enabled(config, "terminal") and (
        hands is None or hands.can_execute_terminal()
    ):
        terminal_options = {
            "working_directory": working_directory,
            "safe_mode": True,
            "clone_current_env": True,
            **_options(config, "terminal"),
        }
        if runtime_environment is not None:
            terminal_options.update(
                {
                    "working_directory": working_directory,
                    "safe_mode": True,
                    "clone_current_env": True,
                    "use_docker_backend": False,
                    "runtime_env_provider": (
                        runtime_environment.process_environment
                    ),
                }
            )
        else:
            terminal_options.setdefault("runtime_env_provider", None)
        terminal_toolkit = TerminalToolkit(
            options.project_id,
            Agents.single_agent,
            **terminal_options,
        )
        assembly.cleanup_toolkits.append(terminal_toolkit)
        toolkit = message_integration.register_toolkits(terminal_toolkit)
        assembly.add_tools(toolkit.get_tools(), TerminalToolkit.toolkit_name())

    if _enabled(config, "workspace_git"):
        toolkit = WorkspaceGitToolkit(
            options.project_id,
            Agents.single_agent,
            **_options(config, "workspace_git"),
        )
        assembly.add_tools(
            toolkit.get_tools(), WorkspaceGitToolkit.toolkit_name()
        )

    if _enabled(config, "web_fetch"):
        toolkit = WebFetchToolkit(**_options(config, "web_fetch"))
        assembly.toolkits_to_register_agent.append(toolkit)
        assembly.add_tools(toolkit.get_tools(), "WebFetchToolkit")

    if _enabled(config, "planning_worktree"):
        planning_options = {
            "working_directory": working_directory,
            **_options(config, "planning_worktree"),
        }
        toolkit = PlanningWorktreeToolkit(
            **planning_options,
        )
        assembly.add_tools(toolkit.get_tools(), "PlanningWorktreeToolkit")

    if _enabled(config, "mcp") or pinned_mcp_config is not None:
        if runtime_environment is None:
            mcp_config = _mcp_config(options, hands)
        else:
            exact_mcp_config = pinned_mcp_config
            mcp_config = _mcp_config(
                options,
                hands,
                exact_config=(exact_mcp_config or {"mcpServers": {}}),
            )
        if mcp_config is not None:
            mcp_options = {
                "timeout": 180,
                **_options(config, "mcp"),
            }
            mcp_options["config_dict"] = mcp_config
            mcp_options["skip_failed"] = runtime_environment is None
            if runtime_environment is not None:
                try:
                    toolkit = MCPToolkit(**mcp_options)
                    # connect() can partially allocate subprocesses before it
                    # raises. Include it in rollback before attempting startup.
                    assembly.cleanup_toolkits.append(toolkit)
                    await toolkit.connect()
                except Exception as exc:
                    from app.workspace_bundle.runtime import (
                        EnvironmentSetupRequiredError,
                    )

                    raise EnvironmentSetupRequiredError(
                        ["bundle_mcp_start_failed"]
                    ) from exc
                assembly.add_tools(toolkit.get_tools(), "MCPToolkit")
            else:
                toolkit = MCPToolkit(**mcp_options)
                # Servers that started before connect() failed or was
                # cancelled must still be stopped by rollback or disposal.
                assembly.cleanup_toolkits.append(toolkit)
                try:
                    await toolkit.connect()
                except Exception:
                    logger.error(
                        "Failed to connect MCPToolkit",
                        exc_info=True,
                    )
                else:
                    assembly.add_tools(toolkit.get_tools(), "MCPToolkit")

    if _enabled(config, "agent") and can_delegate:
        toolkit = DepthLimitedAgentToolkit(
            current_depth=current_depth,
            max_depth=max_depth,
            **_options(config, "agent"),
        )
        assembly.toolkits_to_register_agent.append(toolkit)
        assembly.add_tools(toolkit.get_tools(), toolkit.toolkit_name())

    return assembly

```

## backend/app/agent/toolkit/depth_limited_agent_toolkit.py

Source: https://github.com/eigent-ai/eigent/blob/9840d8aeb304b4af62838331a35c818f128a7be9/backend/app/agent/toolkit/depth_limited_agent_toolkit.py

SHA-256: `70c6d2cc237e95467f3d1caaefdb8686bd956c036bbf5ddd1c344fe2776d9daf`

```
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========

from collections.abc import Callable
from contextvars import Context, copy_context
from threading import Lock
from typing import Any

from camel.toolkits import AgentToolkit, FunctionTool, RegisteredAgentToolkit

from app.agent.toolkit.abstract_toolkit import AbstractToolkit


def _is_agent_tool(tool: FunctionTool | Callable) -> bool:
    func = getattr(tool, "func", tool)
    toolkit = getattr(func, "__self__", None)
    return isinstance(toolkit, AgentToolkit)


class DepthLimitedAgentToolkit(AgentToolkit, AbstractToolkit):
    """CAMEL AgentToolkit with delegated-agent recursion disabled.

    CAMEL's native AgentToolkit clones the parent tool set into child agents.
    For Eigent single-agent mode we want root agents to delegate, while child
    agents must not delegate again. This adapter keeps the CAMEL toolkit API
    and removes AgentToolkit tools from child tool sets.
    """

    def __init__(
        self,
        *,
        current_depth: int = 0,
        max_depth: int = 1,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.current_depth = current_depth
        self.max_depth = max_depth
        self._child_contexts: dict[int, Context] = {}
        self._child_context_lock = Lock()

    def _submit_agent_task(
        self, agent_id: str, agent: Any, prompt: str
    ) -> Any:
        """Capture the admitted Run context before CAMEL changes threads."""

        agent_key = id(agent)
        with self._child_context_lock:
            self._child_contexts[agent_key] = copy_context()
        try:
            return super()._submit_agent_task(agent_id, agent, prompt)
        except Exception:
            with self._child_context_lock:
                self._child_contexts.pop(agent_key, None)
            raise

    def _run_agent_step(self, agent: Any, prompt: str) -> Any:
        # ThreadPoolExecutor.submit does not copy ContextVars. Run the cloned
        # ListenChatAgent inside the exact submission context so each child
        # tool keeps the parent Run/Attempt audit and permission boundary.
        with self._child_context_lock:
            context = self._child_contexts.pop(id(agent), None)
        if context is None:
            return super()._run_agent_step(agent, prompt)
        return context.run(super()._run_agent_step, agent, prompt)

    def _resolve_child_tools(
        self,
        parent,
    ) -> tuple[
        list[FunctionTool | Callable] | None,
        list[RegisteredAgentToolkit] | None,
    ]:
        tools, toolkits_to_register = super()._resolve_child_tools(parent)
        if tools is None:
            return None, toolkits_to_register

        return (
            [tool for tool in tools if not _is_agent_tool(tool)],
            [
                toolkit
                for toolkit in (toolkits_to_register or [])
                if not isinstance(toolkit, AgentToolkit)
            ],
        )

    def _build_system_message(
        self,
        subagent_type: str,
        description: str,
    ) -> str:
        base = super()._build_system_message(subagent_type, description)
        return (
            base + "\nYou are a child sub-agent. Complete the assigned task "
            "directly and do not create or delegate to any further sub-agents."
        )

    @classmethod
    def toolkit_name(cls) -> str:
        return "AgentToolkit"

```

## backend/app/agent/toolkit/file_write_toolkit.py

Source: https://github.com/eigent-ai/eigent/blob/9840d8aeb304b4af62838331a35c818f128a7be9/backend/app/agent/toolkit/file_write_toolkit.py

SHA-256: `834f78ea9766ff2bf1351202c9042d820cefd748720671d8c5207174c2702255`

```
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========

import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from camel.toolkits import FileToolkit as BaseFileToolkit

from app.agent.toolkit.abstract_toolkit import AbstractToolkit
from app.component.environment import env
from app.run_context import RunContext
from app.run_runtime.tool_checkpoint import (
    ToolInvocationNotDispatchedError,
    get_current_tool_checkpoint,
)
from app.service.task import (
    ActionWriteFileData,
    Agents,
    get_task_lock,
    process_task,
)
from app.utils.listen.toolkit_listen import (
    _safe_put_queue,
    auto_listen_toolkit,
    listen_toolkit,
)
from app.utils.space_overlay_client import (
    path_write_lock,
    post_overlay_write,
    relative_to_artifact_root,
    relative_to_workdir,
    run_context_for_task,
    sha256_of_file,
    should_record_overlay,
)
from app.workspace_git import (
    get_default_workspace_mutation_service,
    workspace_block_hint,
)

logger = logging.getLogger("file_write_toolkit")


@dataclass(frozen=True)
class OverlayWriteContext:
    run_context: RunContext
    rel_path: str
    target_path: Path


@dataclass(frozen=True)
class PendingOverlayWrite:
    run_context: RunContext
    rel_path: str
    target_path: Path
    base_hash: str | None
    status: Literal["added", "modified"]
    file_hash: str | None
    size: int
    mode: int


def _describe_file_write(
    *_args,
    filename: str | None = None,
    encoding: str | None = None,
    use_latex: bool = False,
    **_kwargs,
) -> str:
    return (
        f"write content to file: {filename} with encoding: {encoding} "
        f"and use_latex: {use_latex}"
    )


@auto_listen_toolkit(BaseFileToolkit)
class FileToolkit(BaseFileToolkit, AbstractToolkit):
    agent_name: str = Agents.document_agent

    def __init__(
        self,
        api_task_id: str,
        working_directory: str | None = None,
        timeout: float | None = None,
        default_encoding: str = "utf-8",
        backup_enabled: bool = True,
    ) -> None:
        if working_directory is None:
            working_directory = env(
                "file_save_path", os.path.expanduser("~/Downloads")
            )
        super().__init__(
            working_directory, timeout, default_encoding, backup_enabled
        )
        self.api_task_id = api_task_id

    def _prepare_file_write(
        self,
        mutation_service,
        *,
        run_context: RunContext,
        filename: str,
        operation_request_id: str,
        actor_id: str,
        trigger: str,
    ):
        """Prepare Git state before the file operation can touch its target."""

        try:
            return mutation_service.prepare_file_write(
                context=run_context,
                filename=filename,
                operation_request_id=operation_request_id,
                actor_id=actor_id,
                trigger=trigger,
            )
        except Exception as error:
            # Workspace snapshot/admission happens before CAMEL invokes the
            # underlying file operation. Preserve that boundary so an overlay
            # conflict is a known, model-visible failure rather than an
            # ambiguous external side effect that poisons the whole Run.
            error_code = getattr(error, "code", None)
            detail = f"{error_code}: {error}" if error_code else str(error)
            raise ToolInvocationNotDispatchedError(
                "File mutation was not started because its Workspace could "
                f"not be prepared: {detail}.{workspace_block_hint(error)}"
            ) from error

    def _settle_prepared_write(
        self,
        mutation_service,
        prepared_write,
        *,
        operation_request_id: str,
        trigger: str,
    ) -> None:
        """Give every prepared write exactly one completion.

        Completion records a no-op when the file still matches its preimage
        (for example an edit whose ``old_content`` was not found) and
        checkpoints whatever the tool did write. A prepared operation left
        behind would fence every later writer on the checkout, so a failed
        completion is quarantined for the user to review instead.
        """
        try:
            mutation_service.complete_file_write(
                prepared_write,
                operation_request_id=operation_request_id,
                actor_id=self.agent_name,
                trigger=trigger,
            )
        except Exception:
            try:
                mutation_service.mark_file_write_needs_attention(
                    prepared_write
                )
            except Exception:
                logger.exception(
                    "Failed to quarantine an unsettled workspace write",
                    extra={"operation_request_id": operation_request_id},
                )
            raise

    def _overlay_write_context(
        self, filename: str
    ) -> OverlayWriteContext | None:
        context = run_context_for_task(self.api_task_id)
        if context is None:
            return None
        resolved = relative_to_workdir(context, filename)
        if resolved is None:
            return None
        rel_path, target_path = resolved
        if not should_record_overlay(context, target_path):
            return None
        return OverlayWriteContext(context, rel_path, target_path)

    @listen_toolkit(inputs=_describe_file_write)
    def write_to_file(
        self,
        title: str | None = None,
        *,
        content: str | list[list[str]],
        filename: str,
        encoding: str | None = None,
        use_latex: bool = False,
    ) -> str:
        r"""Write the given content to a file.

        If the file exists, it will be overwritten. Supports multiple formats:
        Markdown (.md, .markdown, default), Plaintext (.txt), CSV (.csv),
        DOC/DOCX (.doc, .docx), PDF (.pdf), JSON (.json), YAML (.yml, .yaml),
        and HTML (.html, .htm).

        Args:
            title (Optional[str]): The title of the document. When omitted,
                the file name without its extension is used.
                (default: :obj:`None`)
            content (Union[str, List[List[str]]]): The content to write to the
                file. Content format varies by file type:
                - Text formats (txt, md, html, yaml): string
                - CSV: string or list of lists
                - JSON: string or serializable object
            filename (str): The name or path of the file. If a relative path is
                supplied, it is resolved to self.working_directory.
            encoding (Optional[str]): The character encoding to use. (default:
                :obj: `None`)
            use_latex (bool): Whether to use LaTeX for math rendering.
                (default: :obj:`False`)

        Returns:
            str: A message indicating success or error details.
        """
        if title is None:
            # Models often fill only the status-message title; the document
            # title is cosmetic, so its absence must not fail the write.
            title = Path(filename).stem or filename
        run_context = run_context_for_task(self.api_task_id)
        prepared_write = None
        relative_path = None
        operation_request_id = None
        mutation_service = None
        if run_context is not None:
            checkpoint = get_current_tool_checkpoint()
            operation_request_id = (
                checkpoint.tool_call_id
                if checkpoint is not None
                else f"local-file-write:{uuid.uuid4().hex}"
            )
            mutation_service = get_default_workspace_mutation_service()
            prepared_write = self._prepare_file_write(
                mutation_service,
                run_context=run_context,
                filename=filename,
                operation_request_id=operation_request_id,
                actor_id=self.agent_name,
                trigger="filesystem.write",
            )
        if prepared_write is not None:
            assert operation_request_id is not None
            assert mutation_service is not None
            try:
                res = super().write_to_file(
                    title,
                    content,
                    str(prepared_write.target_path),
                    encoding,
                    use_latex,
                )
            finally:
                self._settle_prepared_write(
                    mutation_service,
                    prepared_write,
                    operation_request_id=operation_request_id,
                    trigger="filesystem.write",
                )
            if "Content successfully written to file: " in res:
                res = (
                    "Content successfully written to file: "
                    f"{prepared_write.relative_path}"
                )
                relative_path = prepared_write.relative_path
        else:
            res = self._write_legacy_or_cloud_overlay(
                title=title,
                content=content,
                filename=filename,
                encoding=encoding,
                use_latex=use_latex,
            )
        if "Content successfully written to file: " in res:
            written_path = res.replace(
                "Content successfully written to file: ", ""
            )
            if relative_path is None and run_context is not None:
                relative_path = relative_to_artifact_root(
                    run_context, written_path
                )
            task_lock = get_task_lock(self.api_task_id)
            # Capture ContextVar value before creating async task
            current_process_task_id = process_task.get("")

            # Use _safe_put_queue to handle both sync and async contexts
            _safe_put_queue(
                task_lock,
                ActionWriteFileData(
                    process_task_id=current_process_task_id,
                    data=written_path,
                    relative_path=relative_path,
                ),
            )
        return res

    def edit_file(
        self,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> str:
        """Edit one file inside the Run-owned Agent worktree.

        CAMEL's base implementation resolves relative paths against the
        toolkit working directory.  That directory is the visible Space for
        compatibility sessions, so calling it directly would let follow-up
        edits bypass the Run commit. Route the edit through the same durable
        exact-path mutation used by ``write_to_file`` instead.
        """

        run_context = run_context_for_task(self.api_task_id)
        prepared_write = None
        relative_path = None
        operation_request_id = None
        mutation_service = None
        if run_context is not None:
            checkpoint = get_current_tool_checkpoint()
            operation_request_id = (
                checkpoint.tool_call_id
                if checkpoint is not None
                else f"local-file-edit:{uuid.uuid4().hex}"
            )
            mutation_service = get_default_workspace_mutation_service()
            prepared_write = self._prepare_file_write(
                mutation_service,
                run_context=run_context,
                filename=file_path,
                operation_request_id=operation_request_id,
                actor_id=self.agent_name,
                trigger="filesystem.edit",
            )
        if prepared_write is not None:
            assert operation_request_id is not None
            assert mutation_service is not None
            try:
                res = super().edit_file(
                    str(prepared_write.target_path),
                    old_content,
                    new_content,
                )
            finally:
                self._settle_prepared_write(
                    mutation_service,
                    prepared_write,
                    operation_request_id=operation_request_id,
                    trigger="filesystem.edit",
                )
            if res.startswith("Successfully edited "):
                relative_path = prepared_write.relative_path
                res = f"Successfully edited {relative_path}"
        else:
            res = super().edit_file(file_path, old_content, new_content)

        if res.startswith("Successfully edited "):
            edited_path = res.removeprefix("Successfully edited ")
            if relative_path is None and run_context is not None:
                relative_path = relative_to_artifact_root(
                    run_context, edited_path
                )
            task_lock = get_task_lock(self.api_task_id)
            current_process_task_id = process_task.get("")
            _safe_put_queue(
                task_lock,
                ActionWriteFileData(
                    process_task_id=current_process_task_id,
                    data=edited_path,
                    relative_path=relative_path,
                ),
            )
        return res

    def _write_legacy_or_cloud_overlay(
        self,
        *,
        title: str,
        content: str | list[list[str]],
        filename: str,
        encoding: str | None,
        use_latex: bool,
    ) -> str:
        overlay_context = self._overlay_write_context(filename)
        if overlay_context is None:
            res = super().write_to_file(
                title, content, filename, encoding, use_latex
            )
        else:
            pending_overlay_write: PendingOverlayWrite | None = None
            with path_write_lock(
                overlay_context.run_context.space_id,
                overlay_context.run_context.project_id,
                overlay_context.run_context.run_id,
                overlay_context.rel_path,
            ):
                existed = overlay_context.target_path.exists()
                base_hash = sha256_of_file(overlay_context.target_path)
                res = super().write_to_file(
                    title, content, filename, encoding, use_latex
                )
                if "Content successfully written to file: " in res:
                    written_path = Path(
                        res.replace(
                            "Content successfully written to file: ", ""
                        )
                    )
                    if not written_path.is_absolute():
                        written_path = (
                            Path(self.working_directory) / written_path
                        )
                    written_hash = sha256_of_file(written_path)
                    written_stat = written_path.stat()
                    pending_overlay_write = PendingOverlayWrite(
                        run_context=overlay_context.run_context,
                        rel_path=overlay_context.rel_path,
                        target_path=written_path,
                        base_hash=base_hash,
                        status="modified" if existed else "added",
                        file_hash=written_hash,
                        size=written_stat.st_size,
                        mode=written_stat.st_mode,
                    )
            if pending_overlay_write is not None:
                post_overlay_write(
                    pending_overlay_write.run_context,
                    pending_overlay_write.rel_path,
                    pending_overlay_write.target_path,
                    base_hash=pending_overlay_write.base_hash,
                    status=pending_overlay_write.status,
                    file_hash=pending_overlay_write.file_hash,
                    size=pending_overlay_write.size,
                    mode=pending_overlay_write.mode,
                )
        return res

```

## backend/app/agent/toolkit/note_taking_toolkit.py

Source: https://github.com/eigent-ai/eigent/blob/9840d8aeb304b4af62838331a35c818f128a7be9/backend/app/agent/toolkit/note_taking_toolkit.py

SHA-256: `032aa712ca2de3cf64c5e3407d2c7d3d411a2cc7e19499ccdfccd393959b22dc`

```
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========= Copyright 2025-2026 @ Eigent.ai All Rights Reserved. =========

from camel.toolkits import NoteTakingToolkit as BaseNoteTakingToolkit

from app.agent.toolkit.abstract_toolkit import AbstractToolkit
from app.service.task import Agents
from app.utils.listen.toolkit_listen import auto_listen_toolkit


@auto_listen_toolkit(BaseNoteTakingToolkit)
class NoteTakingToolkit(BaseNoteTakingToolkit, AbstractToolkit):
    agent_name: str = Agents.document_agent

    def __init__(
        self,
        api_task_id: str,
        agent_name: str | None = None,
        working_directory: str | None = None,
        timeout: float | None = None,
    ) -> None:
        # TODO: Remove default value None since working_directory is required
        # Now the working_directory now is required to be specified
        # as the notes are stored in task specific directory
        if working_directory is None:
            raise ValueError(
                "working_directory is required for NoteTakingToolkit. "
                "Notes must be stored in a task-specific directory."
            )
        self.api_task_id = api_task_id
        if agent_name is not None:
            self.agent_name = agent_name
        super().__init__(
            working_directory=working_directory,
            timeout=timeout,
        )

```
