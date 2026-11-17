# agent-zero — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## agent.py

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/agent.py

SHA-256: `fd7fa584f2cdf7bf483a10542735853ffe65cacf9e2a701009f853d488b40b2e`

```
import asyncio, json, random, re, string, threading, time

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Coroutine, Dict, Literal
from enum import Enum
import models

from helpers import (
    extract_tools,
    files,
    errors,
    history,
    responses_history,
    tokens,
    context as context_helper,
    dirty_json,
    subagents,
)
from helpers import extension
from helpers.print_style import PrintStyle

from langchain_core.prompts import (
    ChatPromptTemplate,
)
from langchain_core.messages import SystemMessage, BaseMessage

import helpers.log as Log
from helpers.dirty_json import DirtyJson
from helpers.defer import DeferredTask
from typing import Callable
from helpers.localization import Localization
from helpers import extension
from helpers.errors import RepairableException, InterventionException, HandledException
from helpers.llm_result import (
    LLMResult,
    RESPONSE_METADATA_KEY,
    function_call_output_item,
    metadata_from_llm_result,
    result_from_metadata,
)
from helpers.litellm_transport import ResponsesTransport, TransportMode
from helpers.responses_tools import build_responses_function_tools, original_tool_name

_RESPONSE_STREAM_UPDATE_CHARS = 128
_RESPONSE_STREAM_UPDATE_SECONDS = 0.05


class AgentContextType(Enum):
    USER = "user"
    TASK = "task"
    BACKGROUND = "background"


class AgentContext:

    _contexts: dict[str, "AgentContext"] = {}
    _contexts_lock = threading.RLock()
    _counter: int = 0
    _notification_manager = None

    @extension.extensible
    def __init__(
        self,
        config: "AgentConfig",
        id: str | None = None,
        name: str | None = None,
        agent0: "Agent|None" = None,
        log: Log.Log | None = None,
        paused: bool = False,
        streaming_agent: "Agent|None" = None,
        created_at: datetime | None = None,
        type: AgentContextType = AgentContextType.USER,
        last_message: datetime | None = None,
        data: dict | None = None,
        output_data: dict | None = None,
        set_current: bool = False,
    ):
        # initialize context
        self.id = id or AgentContext.generate_id()
        existing = None
        with AgentContext._contexts_lock:
            existing = AgentContext._contexts.get(self.id, None)
            if existing:
                AgentContext._contexts.pop(self.id, None)
            AgentContext._contexts[self.id] = self
        if existing and existing.task:
            existing.task.kill()
        if set_current:
            AgentContext.set_current(self.id)

        # initialize state
        self.name = name
        self.config = config
        self.data = data or {}
        self.output_data = output_data or {}
        self.log = log or Log.Log()
        self.log.context = self
        self.paused = paused
        self.streaming_agent = streaming_agent
        self.task: DeferredTask | None = None
        self.created_at = created_at or Localization.get().now()
        self.type = type
        AgentContext._counter += 1
        self.no = AgentContext._counter
        self.last_message = last_message or Localization.get().now()

        # initialize agent at last (context is complete now)
        self.agent0 = agent0 or Agent(0, self.config, self)
        if last_message is not None:
            self.last_message = last_message

    @staticmethod
    def get(id: str):
        with AgentContext._contexts_lock:
            return AgentContext._contexts.get(id, None)

    @staticmethod
    def use(id: str):
        context = AgentContext.get(id)
        if context:
            AgentContext.set_current(id)
        else:
            AgentContext.set_current("")
        return context

    @staticmethod
    def current():
        ctxid = context_helper.get_context_data("agent_context_id", "")
        if not ctxid:
            return None
        return AgentContext.get(ctxid)

    @staticmethod
    def set_current(ctxid: str):
        context_helper.set_context_data("agent_context_id", ctxid)

    @staticmethod
    def first():
        with AgentContext._contexts_lock:
            if not AgentContext._contexts:
                return None
            return list(AgentContext._contexts.values())[0]

    @staticmethod
    def all():
        with AgentContext._contexts_lock:
            return list(AgentContext._contexts.values())

    @staticmethod
    def generate_id():
        def generate_short_id():
            return "".join(random.choices(string.ascii_letters + string.digits, k=8))

        while True:
            short_id = generate_short_id()
            with AgentContext._contexts_lock:
                if short_id not in AgentContext._contexts:
                    return short_id

    @classmethod
    def get_notification_manager(cls):
        if cls._notification_manager is None:
            from helpers.notification import NotificationManager  # type: ignore

            cls._notification_manager = NotificationManager()
        return cls._notification_manager

    @staticmethod
    @extension.extensible
    def remove(id: str):
        with AgentContext._contexts_lock:
            context = AgentContext._contexts.pop(id, None)
        if context and context.task:
            context.task.kill()
        return context

    def get_data(self, key: str, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        return self.data.get(key, None)

    def set_data(self, key: str, value: Any, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        self.data[key] = value

    def get_output_data(self, key: str, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        return self.output_data.get(key, None)

    def set_output_data(self, key: str, value: Any, recursive: bool = True):
        # recursive is not used now, prepared for context hierarchy
        self.output_data[key] = value

    # @extension.extensible
    def output(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": (
                Localization.get().serialize_datetime(self.created_at)
                if self.created_at
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "no": self.no,
            "log_guid": self.log.guid,
            "log_version": len(self.log.updates),
            "log_length": len(self.log.logs),
            "paused": self.paused,
            "last_message": (
                Localization.get().serialize_datetime(self.last_message)
                if self.last_message
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "type": self.type.value,
            "running": self.is_running(),
            **self.output_data,
        }

    @staticmethod
    def log_to_all(
        type: Log.Type,
        heading: str | None = None,
        content: str | None = None,
        kvps: dict | None = None,
        update_progress: Log.ProgressUpdate | None = None,
        id: str | None = None,  # Add id parameter
        **kwargs,
    ) -> list[Log.LogItem]:
        items: list[Log.LogItem] = []
        for context in AgentContext.all():
            items.append(
                context.log.log(
                    type, heading, content, kvps, update_progress, id, **kwargs
                )
            )
        return items

    @extension.extensible
    def kill_process(self):
        if self.task:
            self.task.kill()

    @extension.extensible
    def reset(self):
        self.kill_process()
        self.log.reset()
        self.agent0 = Agent(0, self.config, self)
        self.streaming_agent = None
        self.paused = False

    @extension.extensible
    def nudge(self):
        self.kill_process()
        self.paused = False
        self.task = self.communicate(
            UserMessage("", system_message=[self.agent0.read_prompt("fw.msg_nudge.md")])
        )
        return self.task

    @extension.extensible
    def get_agent(self):
        return self.streaming_agent or self.agent0

    def is_running(self) -> bool:
        return (self.task and self.task.is_alive()) or False

    @extension.extensible
    def communicate(self, msg: "UserMessage", broadcast_level: int = 1):
        self.paused = False  # unpause if paused

        current_agent = self.get_agent()

        if self.task and self.task.is_alive():
            # set intervention messages to agent(s):
            intervention_agent = current_agent
            while intervention_agent and broadcast_level != 0:
                intervention_agent.intervention = msg
                broadcast_level -= 1
                intervention_agent = intervention_agent.data.get(
                    Agent.DATA_NAME_SUPERIOR, None
                )
        else:
            self.task = self.run_task(self._process_chain, current_agent, msg)

        return self.task

    @extension.extensible
    def run_task(
        self, func: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any
    ):
        if not self.task:
            self.task = DeferredTask(
                thread_name=self.__class__.__name__,
            )
        self.task.start_task(func, *args, **kwargs)
        return self.task

    # this wrapper ensures that superior agents are called back if the chat was loaded from file and original callstack is gone
    @extension.extensible
    async def _process_chain(self, agent: "Agent", msg: "UserMessage|str", user=True):
        try:
            msg_template = (
                agent.hist_add_user_message(msg)  # type: ignore
                if user
                else agent.hist_add_tool_result(
                    tool_name="call_subordinate", tool_result=msg  # type: ignore
                )
            )
            response = await agent.monologue()  # type: ignore
            superior = agent.data.get(Agent.DATA_NAME_SUPERIOR, None)
            if superior:
                response = await self._process_chain(superior, response, False)  # type: ignore

            # call end of process extensions
            await extension.call_extensions_async("process_chain_end", agent=self.get_agent(), data={})

            return response
        except Exception as e:
            await self.handle_exception("process_chain", e)

    @extension.extensible
    async def handle_exception(self, location: str, exception: Exception):
        if exception:
            raise exception # exception handling is done by extensions


@dataclass
class AgentConfig:
    mcp_servers: str
    profile: str = ""
    knowledge_subdirs: list[str] = field(default_factory=lambda: ["default", "custom"])
    additional: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UserMessage:
    message: str
    attachments: list[str] = field(default_factory=list[str])
    system_message: list[str] = field(default_factory=list[str])
    id: str = ""


class LoopData:
    def __init__(self, **kwargs):
        self.iteration = -1
        self.system = []
        self.user_message: history.Message | None = None
        self.history_output: list[history.OutputMessage] = []
        self.protocol_temporary: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.protocol_persistent: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.extras_temporary: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.extras_persistent: OrderedDict[str, history.MessageContent] = OrderedDict()
        self.last_response = ""
        self.params_temporary: dict = {}
        self.params_persistent: dict = {}
        self.current_tool = None

        # override values with kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


class Agent:

    DATA_NAME_SUPERIOR = "_superior"
    DATA_NAME_SUBORDINATE = "_subordinate"
    DATA_NAME_CTX_WINDOW = "ctx_window"
    DATA_NAME_RESPONSES_STATE = "responses_state"
    DATA_NAME_RESPONSES_TOOL_NAME_MAP = "responses_tool_name_map"
    DATA_NAME_RESPONSES_COMPUTER_SESSION = "responses_computer_session_id"

    @extension.extensible
    def __init__(
        self, number: int, config: AgentConfig, context: AgentContext | None = None
    ):

        # agent config
        self.config = config

        # agent context
        self.context = context or AgentContext(config=config, agent0=self)

        # non-config vars
        self.number = number
        self.agent_name = f"A{self.number}"

        self.history = history.History(self)  # type: ignore[abstract]
        self.last_user_message: history.Message | None = None
        self.intervention: UserMessage | None = None
        self.data: dict[str, Any] = {}  # free data object all the tools can use

        extension.call_extensions_sync("agent_init", self)

    @extension.extensible
    async def monologue(self):
        while True:
            try:
                # loop data dictionary to pass to extensions
                self.loop_data = LoopData(user_message=self.last_user_message)
                # call monologue_start extensions
                await extension.call_extensions_async(
                    "monologue_start", self, loop_data=self.loop_data
                )

                printer = PrintStyle(italic=True, font_color="#b3ffd9", padding=False)

                # let the agent run message loop until he stops it with a response tool
                while True:

                    self.context.streaming_agent = self  # mark self as current streamer
                    self.loop_data.iteration += 1
                    self.loop_data.params_temporary = {}  # clear temporary params
                    last_response_stream_full = ""
                    last_response_stream_chars = 0
                    last_response_stream_at = time.monotonic()
                    response_stream_pending = False

                    # call message_loop_start extensions
                    await extension.call_extensions_async(
                        "message_loop_start", self, loop_data=self.loop_data
                    )
                    await self.handle_intervention()

                    try:
                        # prepare LLM chain (model, system, history)
                        prompt = await self.prepare_prompt(loop_data=self.loop_data)

                        # call before_main_llm_call extensions
                        await extension.call_extensions_async(
                            "before_main_llm_call", self, loop_data=self.loop_data
                        )
                        await self.handle_intervention()


                        async def reasoning_callback(chunk: str, full: str):
                            await self.handle_intervention()
                            if chunk == full:
                                printer.print("Reasoning: ")  # start of reasoning
                            # Pass chunk and full data to extensions for processing
                            stream_data = {"chunk": chunk, "full": full}
                            await extension.call_extensions_async(
                                "reasoning_stream_chunk",
                                self,
                                loop_data=self.loop_data,
                                stream_data=stream_data,
                            )
                            # Stream masked chunk after extensions processed it
                            if stream_data.get("chunk"):
                                printer.stream(stream_data["chunk"])
                            # Use the potentially modified full text for downstream processing
                            await self.handle_reasoning_stream(stream_data["full"])

                        async def stream_callback(chunk: str, full: str):
                            nonlocal last_response_stream_full, last_response_stream_chars
                            nonlocal last_response_stream_at, response_stream_pending
                            await self.handle_intervention()
                            # output the agent response stream
                            if chunk == full:
                                printer.print("Response: ")  # start of response
                            # Pass chunk and full data to extensions for processing
                            stream_data = {"chunk": chunk, "full": full}
                            await extension.call_extensions_async(
                                "response_stream_chunk",
                                self,
                                loop_data=self.loop_data,
                                stream_data=stream_data,
                            )
                            tool_request = extract_tools.extract_tool_request(full)
                            if tool_request is not None:
                                try:
                                    await self.validate_tool_request(tool_request)
                                except Exception:
                                    pass
                                else:
                                    await self.handle_response_stream(stream_data["full"])
                                    response_stream_pending = False
                                    return full.strip()

                            # Stream masked chunk after extensions processed it
                            if stream_data.get("chunk"):
                                printer.stream(stream_data["chunk"])
                            last_response_stream_full = stream_data["full"]
                            response_stream_pending = True
                            now = time.monotonic()
                            if (
                                len(full) - last_response_stream_chars
                                >= _RESPONSE_STREAM_UPDATE_CHARS
                                or now - last_response_stream_at
                                >= _RESPONSE_STREAM_UPDATE_SECONDS
                            ):
                                await self.handle_response_stream(last_response_stream_full)
                                last_response_stream_chars = len(full)
                                last_response_stream_at = time.monotonic()
                                response_stream_pending = False

                        # call main LLM
                        llm_result = await self.call_chat_model_turn(
                            messages=prompt,
                            response_callback=stream_callback,
                            reasoning_callback=reasoning_callback,
                        )
                        agent_response = llm_result.response
                        await self.handle_intervention(agent_response)

                        if response_stream_pending:
                            await self.handle_response_stream(last_response_stream_full)

                        # Notify extensions to finalize their stream filters
                        await extension.call_extensions_async(
                            "reasoning_stream_end", self, loop_data=self.loop_data
                        )
                        await self.handle_intervention(agent_response)

                        await extension.call_extensions_async(
                            "response_stream_end", self, loop_data=self.loop_data
                        )

                        await self.handle_intervention(agent_response)

                        result_data = {"llm_result": llm_result}
                        await extension.call_extensions_async(
                            "message_loop_result",
                            self,
                            loop_data=self.loop_data,
                            result_data=result_data,
                        )
                        if result_data.get("skip_default_processing"):
                            continue

                        agent_response = llm_result.response
                        log_item = self.loop_data.params_temporary.get("log_item_generating")
                        assistant_message = self.hist_add_ai_response(
                            agent_response,
                            id=log_item.id if log_item else "",
                            llm_result=llm_result,
                        )
                        tools_result = await self.process_llm_result_tools(llm_result)
                        if tools_result:  # final response of message loop available
                            return tools_result  # break the execution if the task is done

                    # exceptions inside message loop:
                    except Exception as e:
                        await self.handle_exception("message_loop", e)

                    finally:
                        # call message_loop_end extensions
                        if self.context.task and self.context.task.is_alive(): # don't call extensions post mortem
                            await extension.call_extensions_async(
                                "message_loop_end", self, loop_data=self.loop_data
                            )



            # exceptions outside message loop:
            except Exception as e:
                await self.handle_exception("monologue", e)
            finally:
                self.context.streaming_agent = None  # unset current streamer
                # call monologue_end extensions
                if self.context.task and self.context.task.is_alive(): # don't call extensions post mortem
                    await extension.call_extensions_async(
                        "monologue_end", self, loop_data=self.loop_data
                    )  # type: ignore

    @extension.extensible
    async def prepare_prompt(self, loop_data: LoopData) -> list[BaseMessage]:
        self.context.log.set_progress("Building prompt")

        # call extensions before setting prompts
        await extension.call_extensions_async(
            "message_loop_prompts_before", self, loop_data=loop_data
        )

        # set system prompt and message history
        responses_history.start_prompt(loop_data)
        loop_data.system = await self.get_system_prompt(self.loop_data)
        loop_data.history_output = self.history.output()

        # and allow extensions to edit them
        await extension.call_extensions_async(
            "message_loop_prompts_after", self, loop_data=loop_data
        )

        # concatenate system prompt and remove JSON fence markers from examples
        system_text = files.remove_code_fences(
            "\n\n".join(loop_data.system), language="json"
        )

        # join protocol and extras
        protocol = self._build_context_message(
            "agent.context.protocol.md",
            "protocol",
            {**loop_data.protocol_persistent, **loop_data.protocol_temporary},
            include_empty=False,
        )
        extras = self._build_context_message(
            "agent.context.extras.md",
            "extras",
            {**loop_data.extras_persistent, **loop_data.extras_temporary},
            include_empty=True,
        )
        loop_data.protocol_temporary.clear()
        loop_data.extras_temporary.clear()

        # convert protocol + history + extras to LLM format
        history_langchain: list[BaseMessage] = history.output_langchain(
            protocol + loop_data.history_output + extras
        )

        # build full prompt from system prompt, protocol, message history and extras
        full_prompt: list[BaseMessage] = [
            SystemMessage(content=system_text),
            *history_langchain,
        ]
        full_text = ChatPromptTemplate.from_messages(full_prompt).format()
        responses_history.remember_prompt(loop_data, full_text, full_prompt[0], protocol)

        # store as last context window content
        self.set_data(
            Agent.DATA_NAME_CTX_WINDOW,
            {
                "text": full_text,
                "tokens": tokens.approximate_prompt_tokens(full_text),
            },
        )

        return full_prompt

    def _build_context_message(
        self,
        prompt_file: str,
        variable_name: str,
        values: dict[str, history.MessageContent],
        include_empty: bool,
    ) -> list[history.OutputMessage]:
        if not include_empty and not values:
            return []

        return history.Message(  # type: ignore[abstract]
            False,
            content=self.read_prompt(
                prompt_file,
                **{variable_name: dirty_json.stringify(values, separators=(",", ":"))},
            ),
        ).output()

    @extension.extensible
    async def handle_exception(self, location: str, exception: Exception):
        if exception:
            raise exception # exception handling is done by extensions

        # exception_data = {"exception": exception}
        # await self.call_extensions(
        #     "message_loop_exception", exception_data=exception_data
        # )

        # # If extensions cleared the exception, continue.
        # if not exception_data.get("exception"):
        #     return

        # # Backwards-compatible fallback (should normally be handled by _90 extension).
        # exception = exception_data["exception"]
        # if isinstance(exception, HandledException):
        #     raise exception
        # elif isinstance(exception, asyncio.CancelledError):
        #     PrintStyle(font_color="white", background_color="red", padding=True).print(
        #         f"Context {self.context.id} terminated during message loop"
        #     )
        #     raise HandledException(exception)

        # else:
        #     error_text = errors.error_text(exception)
        #     error_message = errors.format_error(exception)

        #     # Mask secrets in error messages
        #     PrintStyle(font_color="red", padding=True).print(error_message)
        #     self.context.log.log(
        #         type="error",
        #         content=error_message,
        #     )
        #     PrintStyle(font_color="red", padding=True).print(
        #         f"{self.agent_name}: {error_text}"
        #     )

        #     raise HandledException(exception)  # Re-raise the exception to kill the loop

    @extension.extensible
    async def get_system_prompt(self, loop_data: LoopData) -> list[str]:
        system_prompt: list[str] = []
        await extension.call_extensions_async(
            "system_prompt", self, system_prompt=system_prompt, loop_data=loop_data
        )
        return system_prompt

    @extension.extensible
    def parse_prompt(self, _prompt_file: str, **kwargs):
        dirs = subagents.get_paths(self, "prompts")

        prompt = files.parse_file(
            _prompt_file, _directories=dirs, _agent=self, **kwargs
        )
        if isinstance(prompt, str):
            prompt = prompt.rstrip("\n")
        return prompt

    @extension.extensible
    def read_prompt(self, file: str, **kwargs) -> str:
        dirs = subagents.get_paths(self, "prompts")

        prompt = files.read_prompt_file(file, _directories=dirs, _agent=self, **kwargs)
        if files.is_full_json_template(prompt):
            prompt = files.remove_code_fences(prompt)
        if isinstance(prompt, str):
            prompt = prompt.rstrip("\n")
        return prompt

    def get_data(self, field: str):
        return self.data.get(field, None)

    def set_data(self, field: str, value):
        self.data[field] = value

    @extension.extensible
    def hist_add_message(
        self,
        ai: bool,
        content: history.MessageContent,
        tokens: int = 0,
        id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        self.context.last_message = Localization.get().now()
        # Allow extensions to process content before adding to history
        content_data = {"content": content}
        extension.call_extensions_sync(
            "hist_add_before", self, content_data=content_data, ai=ai
        )
        return self.history.add_message(
            ai=ai,
            content=content_data["content"],
            tokens=tokens,
            id=id,
            metadata=metadata,
        )

    @extension.extensible
    def hist_add_user_message(self, message: UserMessage, intervention: bool = False):
        self.history.new_topic()  # user message starts a new topic in history

        # load message template based on intervention
        if intervention:
            content = self.parse_prompt(
                "fw.intervention.md",
                message=message.message,
                attachments=message.attachments,
                system_message=message.system_message,
            )
        else:
            content = self.parse_prompt(
                "fw.user_message.md",
                message=message.message,
                attachments=message.attachments,
                system_message=message.system_message,
            )

        # remove empty parts from template
        if isinstance(content, dict):
            content = {k: v for k, v in content.items() if v}

        # add to history
        msg = self.hist_add_message(False, content=content, id=message.id)  # type: ignore
        self.last_user_message = msg
        return msg

    @extension.extensible
    def hist_add_ai_response(
        self, message: str, llm_result: LLMResult | str | None = None, id: str = ""
    ):
        if isinstance(llm_result, str):
            if id:
                raise TypeError("History message ID supplied twice")
            id, llm_result = llm_result, None
        if llm_result is None:
            llm_result = LLMResult.non_llm()
        self.loop_data.last_response = (
            llm_result.repeat_response_text() if llm_result.function_calls else message
        )
        message = llm_result.function_calls_text() or message
        content = self.parse_prompt("fw.ai_response.md", message=message)
        msg = self.hist_add_message(
            True,
            content=content,
            id=id,
            metadata=metadata_from_llm_result(llm_result),
        )
        self._remember_llm_result_state(llm_result, msg)
        return msg

    @extension.extensible
    def hist_add_warning(self, message: history.MessageContent, id: str = ""):
        content = self.parse_prompt("fw.warning.md", message=message)
        return self.hist_add_message(False, content=content, id=id)

    @extension.extensible
    def hist_add_tool_result(self, tool_name: str, tool_result: str, **kwargs):
        msg_id = kwargs.pop("id", "")
        responses_item = kwargs.pop("_responses_output_item", None) or kwargs.pop(
            "responses_item", None
        )
        metadata = (
            {
                RESPONSE_METADATA_KEY: {
                    "input_items": [responses_item],
                    "output_items": [],
                    "mode": "responses",
                    "state": "provider",
                }
            }
            if isinstance(responses_item, dict)
            else None
        )
        data = {
            "tool_name": tool_name,
            "tool_result": tool_result,
            **kwargs,
        }
        extension.call_extensions_sync("hist_add_tool_result", self, data=data)
        return self.hist_add_message(False, content=data, id=msg_id, metadata=metadata)

    def concat_messages(
        self, messages
    ):  # TODO add param for message range, topic, history
        return self.history.output_text(human_label="user", ai_label="assistant")

    @extension.extensible
    def get_chat_model(self):
        return None

    @extension.extensible
    def get_utility_model(self):
        return None

    @extension.extensible
    def get_embedding_model(self):
        return None

    @extension.extensible
    async def call_utility_model(
        self,
        system: str,
        message: str,
        callback: Callable[[str], Awaitable[None]] | None = None,
        background: bool = False,
    ):
        model = self.get_utility_model()

        # call extensions
        call_data = {
            "model": model,
            "system": system,
            "message": message,
            "callback": callback,
            "background": background,
        }
        await extension.call_extensions_async(
            "util_model_call_before", self, call_data=call_data
        )

        # propagate stream to callback if set
        async def stream_callback(chunk: str, total: str):
            if call_data["callback"]:
                await call_data["callback"](chunk)

        response, _reasoning = await call_data["model"].unified_call(
            system_message=call_data["system"],
            user_message=call_data["message"],
            response_callback=stream_callback if call_data["callback"] else None,
            rate_limiter_callback=(
                self.rate_limiter_callback if not call_data["background"] else None
            ),
        )

        await extension.call_extensions_async(
            "util_model_call_after", self, call_data=call_data, response=response
        )

        return response

    @extension.extensible
    async def call_chat_model(
        self,
        messages: list[BaseMessage],
        response_callback: Callable[[str, str], Awaitable[str | None]] | None = None,
        reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
        background: bool = False,
        explicit_caching: bool = True,
    ):
        response = ""

        # model class
        model = self.get_chat_model()

        # call extensions before
        call_data = {
            "model": model,
            "messages": messages,
            "response_callback": response_callback,
            "reasoning_callback": reasoning_callback,
            "background": background,
            "explicit_caching": explicit_caching,
        }
        await extension.call_extensions_async(
            "chat_model_call_before", self, call_data=call_data
        )

        # call model
        response, reasoning = await call_data["model"].unified_call(
            messages=call_data["messages"],
            reasoning_callback=call_data["reasoning_callback"],
            response_callback=call_data["response_callback"],
            rate_limiter_callback=(
                self.rate_limiter_callback if not call_data["background"] else None
            ),
            explicit_caching=call_data["explicit_caching"],
        )

        await extension.call_extensions_async(
            "chat_model_call_after", self, call_data=call_data, response=response, reasoning=reasoning
        )

        return response, reasoning

    @extension.extensible
    async def call_chat_model_turn(
        self,
        messages: list[BaseMessage],
        response_callback: Callable[[str, str], Awaitable[str | None]] | None = None,
        reasoning_callback: Callable[[str, str], Awaitable[None]] | None = None,
        background: bool = False,
        explicit_caching: bool = True,
    ) -> LLMResult:
        model = self.get_chat_model()
        model_kwargs = getattr(model, "kwargs", {}) if model else {}
        if isinstance(model_kwargs, dict) and model_kwargs.get("responses_delete_on_chat_delete") is False:
            self.set_data("responses_delete_on_chat_delete", False)
        # native function tools are only sent through the Responses API
        api_mode = model_kwargs.get("a0_api_mode") if isinstance(model_kwargs, dict) else None
        response_tools, name_map = (
            build_responses_function_tools(self)
            if TransportMode.from_value(api_mode) is TransportMode.RESPONSES
            else ([], {})
        )
        self.set_data(Agent.DATA_NAME_RESPONSES_TOOL_NAME_MAP, name_map)

        call_data = {
            "model": model,
            "messages": messages,
            "response_callback": response_callback,
            "reasoning_callback": reasoning_callback,
            "background": background,
            "explicit_caching": explicit_caching,
            "a0_responses_function_tools": response_tools,
        }

        previous_state = self._responses_state_for_model(model)
        if previous_state:
            history_counter = int(previous_state.get("history_counter", 0) or 0)
            call_data["previous_response_id"] = previous_state.get("response_id", "")
            call_data["responses_input_items"] = self._responses_input_items_since(
                model,
                history_counter,
            )
        call_data["responses_local_input_items"] = ResponsesTransport.input_from_model_messages(
            model,
            messages,
        )

        await extension.call_extensions_async(
            "chat_model_call_before", self, call_data=call_data
        )

        turn_kwargs = {
            **responses_history.prepare_call(self, call_data),
            "a0_responses_function_tools": call_data.get(
                "a0_responses_function_tools"
            ),
            "responses_local_input_items": call_data.get(
                "responses_local_input_items"
            ),
        }
        for key in (
            "responses_builtin_tools",
            "responses_state",
            "previous_response_id",
            "responses_input_items",
        ):
            if call_data.get(key) is not None:
                turn_kwargs[key] = call_data.get(key)

        llm_result = await call_data["model"].unified_turn(
            messages=call_data["messages"],
            reasoning_callback=call_data["reasoning_callback"],
            response_callback=call_data["response_callback"],
            rate_limiter_callback=(
                self.rate_limiter_callback if not call_data["background"] else None
            ),
            explicit_caching=call_data["explicit_caching"],
            **turn_kwargs,
        )

        downgraded = llm_result.capability.get("builtin_tool_downgrades")
        if downgraded:
            self.context.log.log(
                type="info",
                heading="Responses capability downgrade",
                content=(
                    "Provider rejected Responses built-in tool(s); omitted: "
                    + ", ".join(str(item) for item in downgraded)
                ),
            )

        await extension.call_extensions_async(
            "chat_model_call_after",
            self,
            call_data=call_data,
            response=llm_result.response,
            reasoning=llm_result.reasoning,
        )

        return llm_result

    def _responses_state_for_model(self, model: Any) -> dict[str, Any]:
        state = self.get_data(Agent.DATA_NAME_RESPONSES_STATE)
        if not isinstance(state, dict):
            return {}
        provider_model_key = str(getattr(model, "model_name", "") or "")
        if state.get("provider_model_key") != provider_model_key:
            return {}
        if not state.get("response_id"):
            return {}
        return state

    def _responses_input_items_since(
        self, model: Any, sequence: int
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in self.history.messages_since(sequence):
            items.extend(self._responses_input_items_for_message(model, message))
        return items

    def _responses_input_items_for_message(
        self, model: Any, message: history.Message
    ) -> list[dict[str, Any]]:
        result = result_from_metadata(message.metadata)
        if result:
            if message.ai and result.output_items:
                return [item.to_dict() for item in result.output_items]
            if not message.ai and result.input_items:
                return [dict(item) for item in result.input_items]

        output = message.output()
        langchain_messages = history.output_langchain(output)
        return ResponsesTransport.input_from_model_messages(model, langchain_messages)

    def _remember_llm_result_state(
        self, llm_result: LLMResult, history_message: history.Message
    ) -> None:
        if not llm_result.response_id:
            return
        current = self.get_data(Agent.DATA_NAME_RESPONSES_STATE)
        response_ids = []
        if isinstance(current, dict) and isinstance(current.get("response_ids"), list):
            response_ids = [str(item) for item in current["response_ids"] if item]
        if llm_result.response_id not in response_ids:
            response_ids.append(llm_result.response_id)
        self.set_data(
            Agent.DATA_NAME_RESPONSES_STATE,
            {
                "response_id": llm_result.response_id,
                "previous_response_id": llm_result.previous_response_id,
                "provider_model_key": llm_result.provider_model_key,
                "history_counter": history_message.sequence,
                "response_ids": response_ids,
            },
        )

    @extension.extensible
    async def rate_limiter_callback(
        self, message: str, key: str, total: int, limit: int
    ):
        # show the rate limit waiting in a progress bar, no need to spam the chat history
        self.context.log.set_progress(message, True)
        return False

    @extension.extensible
    async def handle_intervention(self, progress: str = ""):
        await self.wait_if_paused()
        if (
            self.intervention
        ):  # if there is an intervention message, but not yet processed
            msg = self.intervention
            self.intervention = None  # reset the intervention message
            # If a tool was running, save its progress to history
            last_tool = self.loop_data.current_tool
            if last_tool:
                tool_progress = last_tool.progress.strip()
                if tool_progress:
                    self.hist_add_tool_result(last_tool.name, tool_progress)
                    last_tool.set_progress(None)
            if progress.strip():
                self.hist_add_ai_response(progress, llm_result=LLMResult.non_llm())
            # append the intervention message
            self.hist_add_user_message(msg, intervention=True)
            raise InterventionException(msg)

    async def wait_if_paused(self):
        while self.context.paused:
            await asyncio.sleep(0.1)

    async def process_llm_result_tools(self, llm_result: LLMResult):
        await self._log_response_builtin_items(llm_result)
        if llm_result.function_calls:
            for function_call in llm_result.function_calls:
                name_map = self.get_data(Agent.DATA_NAME_RESPONSES_TOOL_NAME_MAP)
                tool_name = original_tool_name(function_call.name, name_map)
                response_item_factory = lambda response, call=function_call: function_call_output_item(
                    call.call_id,
                    response.message,
                )
                result = await self._execute_tool_request(
                    tool_name=tool_name,
                    tool_args=function_call.arguments,
                    message=llm_result.response,
                    raw_tool_name=tool_name,
                    responses_item_factory=response_item_factory,
                )
                if result:
                    return result
            return None
        if (
            llm_result.builtin_items
            and extract_tools.extract_tool_request(llm_result.response) is None
        ):
            return None
        message = llm_result.response
        if not message and llm_result.reasoning:
            if (
                extract_tools.extract_tool_request(llm_result.reasoning) is not None
                or extract_tools.is_misformatted_tool_request(llm_result.reasoning)
            ):
                message = llm_result.reasoning
        return await self.process_tools(message)

    async def _execute_tool_request(
        self,
        tool_name: str,
        tool_args: dict,
        message: str,
        raw_tool_name: str = "",
        responses_item_factory: Callable[[Any], dict[str, Any]] | None = None,
    ):
        raw_tool_name = raw_tool_name or tool_name
        tool_method = None
        tool = None

        try:
            import helpers.mcp_handler as mcp_helper

            mcp_tool_candidate = mcp_helper.MCPConfig.get_instance().get_tool(
                self, tool_name
            )
            if mcp_tool_candidate:
                tool = mcp_tool_candidate
        except ImportError:
            PrintStyle(
                background_color="black", font_color="yellow", padding=True
            ).print("MCP helper module not found. Skipping MCP tool lookup.")
        except Exception as e:
            PrintStyle(background_color="black", font_color="red", padding=True).print(
                f"Failed to get MCP tool '{tool_name}': {e}"
            )

        if not tool:
            tool = self.get_tool(
                name=tool_name,
                method=tool_method,
                args=tool_args,
                message=message,
                loop_data=self.loop_data,
            )

        if not tool:
            error_detail = (
                f"Tool '{raw_tool_name}' not found or could not be initialized."
            )
            wmsg = self.hist_add_warning(error_detail)
            PrintStyle(font_color="red", padding=True).print(error_detail)
            self.context.log.log(
                type="warning",
                content=error_detail,
                id=wmsg.id,
            )
            return None

        self.loop_data.current_tool = tool  # type: ignore
        try:
            await self.handle_intervention()

            await tool.before_execution(**tool_args)
            await self.handle_intervention()

            await extension.call_extensions_async(
                "tool_execute_before",
                self,
                tool_args=tool_args or {},
                tool_name=tool_name,
            )

            response = await tool.execute(**tool_args)
            await self.handle_intervention()

            await extension.call_extensions_async(
                "tool_execute_after",
                self,
                response=response,
                tool_name=tool_name,
            )

            if responses_item_factory:
                response.additional = {
                    **(response.additional or {}),
                    "_responses_output_item": responses_item_factory(response),
                }

            await tool.after_execution(response)
            await self.handle_intervention()

            if response.break_loop:
                self._clear_responses_pending_state()
                return response.message
        finally:
            self.loop_data.current_tool = None
        return None

    async def _log_response_builtin_items(self, llm_result: LLMResult) -> None:
        for item in llm_result.builtin_items:
            if item.type == "computer_call":
                await self._handle_responses_computer_call(item.data)
                continue
            if item.type == "mcp_approval_request":
                self._handle_responses_mcp_approval_request(item.data)
                continue
            self.context.log.log(
                type="info",
                heading=f"Responses tool item: {item.type}",
                content=json.dumps(item.data, ensure_ascii=False, default=str),
            )

    async def _handle_responses_computer_call(self, item: dict[str, Any]) -> None:
        safety_checks = item.get("pending_safety_checks") or item.get("safety_checks")
        if safety_checks:
            message = (
                "Responses computer_call requested safety-check acknowledgement. "
                "Agent Zero requires explicit user acknowledgement before executing it."
            )
            output_item = {
                "type": "computer_call_output",
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "output": {"type": "input_text", "text": message},
            }
            self.hist_add_tool_result(
                "computer_call",
                message,
                responses_item=output_item,
            )
            self.context.log.log(type="warning", content=message)
            return

        args = self._computer_call_args(item)
        if not args:
            message = "Responses computer_call action is unsupported by Agent Zero."
            output_item = {
                "type": "computer_call_output",
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "output": {"type": "input_text", "text": message},
            }
            self.hist_add_tool_result(
                "computer_call",
                message,
                responses_item=output_item,
            )
            self.context.log.log(type="warning", content=message)
            return

        if args.get("action") != "start_session" and not args.get("session_id"):
            session_id = str(
                self.get_data(Agent.DATA_NAME_RESPONSES_COMPUTER_SESSION) or ""
            )
            if session_id:
                args["session_id"] = session_id

        response_item_factory = lambda response: self._computer_call_output_item(
            item,
            response,
        )
        result = await self._execute_tool_request(
            tool_name="computer_use_remote",
            tool_args=args,
            message=json.dumps(item, ensure_ascii=False, default=str),
            raw_tool_name="computer_call",
            responses_item_factory=response_item_factory,
        )
        _ = result

    def _handle_responses_mcp_approval_request(self, item: dict[str, Any]) -> None:
        request_id = str(
            item.get("approval_request_id") or item.get("id") or item.get("call_id") or ""
        )
        message = (
            "Responses MCP approval request received. Agent Zero denied it because "
            "provider-hosted MCP approval requires explicit user approval."
        )
        output_item = {
            "type": "mcp_approval_response",
            "approval_request_id": request_id,
            "approve": False,
        }
        self.hist_add_tool_result(
            "mcp_approval_request",
            message,
            responses_item=output_item,
        )
        self.context.log.log(
            type="warning",
            heading="Responses MCP approval required",
            content=message,
        )

    def _computer_call_args(self, item: dict[str, Any]) -> dict[str, Any]:
        action = item.get("action")
        action_data = dict(action) if isinstance(action, dict) else {}
        action_type = str(
            action_data.get("type")
            or action_data.get("action")
            or item.get("action_type")
            or ""
        ).strip().lower()
        args: dict[str, Any] = {}

        if action_type in {"screenshot", "capture"}:
            args["action"] = "capture"
        elif action_type in {"move", "mousemove"}:
            args.update({"action": "move", "x": action_data.get("x"), "y": action_data.get("y")})
        elif action_type in {"click", "double_click"}:
            args.update(
                {
                    "action": "click",
                    "x": action_data.get("x"),
                    "y": action_data.get("y"),
                    "button": action_data.get("button", "left"),
                    "count": 2 if action_type == "double_click" else action_data.get("count", 1),
                }
            )
        elif action_type == "scroll":
            args.update(
                {
                    "action": "scroll",
                    "dx": action_data.get("dx", action_data.get("scroll_x", 0)),
                    "dy": action_data.get("dy", action_data.get("scroll_y", 0)),
                }
            )
        elif action_type in {"keypress", "key"}:
            args.update(
                {
                    "action": "key",
                    "keys": action_data.get("keys") or action_data.get("key"),
                }
            )
        elif action_type in {"type", "input_text"}:
            args.update({"action": "type", "text": action_data.get("text", "")})
        else:
            return {}

        session_id = item.get("session_id") or action_data.get("session_id")
        if session_id:
            args["session_id"] = session_id
        return args

    def _computer_call_output_item(
        self, source_item: dict[str, Any], response: Any
    ) -> dict[str, Any]:
        output: dict[str, Any] = {
            "type": "input_text",
            "text": str(getattr(response, "message", "") or ""),
        }
        additional = getattr(response, "additional", None)
        raw_content = additional.get("raw_content") if isinstance(additional, dict) else None
        if isinstance(raw_content, list):
            for content in raw_content:
                if not isinstance(content, dict):
                    continue
                if content.get("type") != "image_url":
                    continue
                image_url = content.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if url:
                    output = {"type": "input_image", "image_url": url}
                    break

        session_id_match = re_search_session_id(str(getattr(response, "message", "") or ""))
        if session_id_match:
            self.set_data(Agent.DATA_NAME_RESPONSES_COMPUTER_SESSION, session_id_match)

        return {
            "type": "computer_call_output",
            "call_id": str(source_item.get("call_id") or source_item.get("id") or ""),
            "output": output,
        }

    def _clear_responses_pending_state(self) -> None:
        state = self.get_data(Agent.DATA_NAME_RESPONSES_STATE)
        if isinstance(state, dict):
            state = dict(state)
            state.pop("response_id", None)
            state.pop("previous_response_id", None)
            self.set_data(Agent.DATA_NAME_RESPONSES_STATE, state)

    @extension.extensible
    async def process_tools(self, msg: str):
        # search for tool usage requests in agent message
        tool_request = extract_tools.extract_tool_request(msg)

        raw_tool_name = ""
        tool_args = {}

        # Only validate when extraction produced an object; None means no JSON tool
        # block was found - the misformat warning path below handles that.
        if tool_request is not None:
            try:
                await self.validate_tool_request(tool_request)
                raw_tool_name, tool_args = extract_tools.normalize_tool_request(
                    tool_request
                )
            except ValueError:
                tool_request = None  # treat structural validation errors as misformat

        if tool_request is not None:
            tool_name = raw_tool_name  # Initialize tool_name with raw_tool_name
            tool_method = None  # Initialize tool_method

            tool = None  # Initialize tool to None

            # Try getting tool from MCP first
            try:
                import helpers.mcp_handler as mcp_helper

                mcp_tool_candidate = mcp_helper.MCPConfig.get_instance().get_tool(
                    self, tool_name
                )
                if mcp_tool_candidate:
                    tool = mcp_tool_candidate
            except ImportError:
                PrintStyle(
                    background_color="black", font_color="yellow", padding=True
                ).print("MCP helper module not found. Skipping MCP tool lookup.")
            except Exception as e:
                PrintStyle(
                    background_color="black", font_color="red", padding=True
                ).print(f"Failed to get MCP tool '{tool_name}': {e}")

            # Fallback to local get_tool if MCP tool was not found or MCP lookup failed
            if not tool:
                tool = self.get_tool(
                    name=tool_name,
                    method=tool_method,
                    args=tool_args,
                    message=msg,
                    loop_data=self.loop_data,
                )

            if tool:
                tool.args = tool_args
                self.loop_data.current_tool = tool  # type: ignore
                try:
                    await self.handle_intervention()

                    # Call tool hooks for compatibility
                    await tool.before_execution(**tool_args)
                    await self.handle_intervention()

                    # Allow extensions to preprocess tool arguments
                    await extension.call_extensions_async(
                        "tool_execute_before",
                        self,
                        tool_args=tool_args or {},
                        tool_name=tool_name,
                    )

                    response = await tool.execute(**tool_args)
                    await self.handle_intervention()

                    # Allow extensions to postprocess tool response
                    await extension.call_extensions_async(
                        "tool_execute_after",
                        self,
                        response=response,
                        tool_name=tool_name,
                    )

                    await tool.after_execution(response)
                    await self.handle_intervention()

                    if response.break_loop:
                        return response.message
                finally:
                    self.loop_data.current_tool = None
            else:
                error_detail = (
                    f"Tool '{raw_tool_name}' not found or could not be initialized."
                )
                wmsg = self.hist_add_warning(error_detail)
                PrintStyle(font_color="red", padding=True).print(error_detail)
                self.context.log.log(
                    type="warning", content=error_detail, id=wmsg.id
                )
        else:
            warning_msg_misformat = self.read_prompt("fw.msg_misformat.md")
            wmsg = self.hist_add_warning(warning_msg_misformat)
            PrintStyle(font_color="red", padding=True).print(warning_msg_misformat)
            self.context.log.log(
                type="warning",
                content="Message misformat, no valid tool request found.",
                id=wmsg.id,
            )

    @extension.extensible
    async def validate_tool_request(self, tool_request: Any):
        extract_tools.normalize_tool_request(tool_request)



    async def handle_reasoning_stream(self, stream: str):
        await self.handle_intervention()
        await extension.call_extensions_async(
            "reasoning_stream",
            self,
            loop_data=self.loop_data,
            text=stream,
        )

    async def handle_response_stream(self, stream: str):
        await self.handle_intervention()
        try:
            if len(stream) < 25:
                return  # no reason to try
            response = DirtyJson.parse_string(stream)
            if isinstance(response, dict):
                try:
                    tool_name, tool_args = extract_tools.normalize_tool_request(response)
                    response.update(tool_name=tool_name, tool_args=tool_args)
                except ValueError:
                    pass  # Tool fields may still be incomplete.
                await extension.call_extensions_async(
                    "response_stream",
                    self,
                    loop_data=self.loop_data,
                    text=stream,
                    parsed=response,
                )

        except Exception as e:
            pass

    @extension.extensible
    def get_tool(
        self,
        name: str,
        method: str | None,
        args: dict,
        message: str,
        loop_data: LoopData | None,
        **kwargs,
    ):
        from tools.unknown import Unknown
        from helpers.tool import Tool

        classes = []

        # search for tools in agent's folder hierarchy
        paths = subagents.get_paths(self, "tools", name + ".py")

        for path in paths:
            try:
                classes = extract_tools.load_classes_from_file(path, Tool)  # type: ignore[arg-type]
                break
            except Exception:
                continue

        tool_class = classes[0] if classes else Unknown
        return tool_class(
            agent=self,
            name=name,
            method=method,
            args=args,
            message=message,
            loop_data=loop_data,
            **kwargs,
        )


def re_search_session_id(text: str) -> str:
    match = re.search(r"session_id=([A-Za-z0-9_.:-]+)", text or "")
    return match.group(1) if match else ""

```

## plugins/_a0_connector/prompts/agent.system.tool.code_execution_remote.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_a0_connector/prompts/agent.system.tool.code_execution_remote.md

SHA-256: `eedcf1f0589016df901091a4b05aa8a0f718f98d097abd199126a2c4c8425e7f`

```
# code_execution_remote tool

Run shell-backed commands on a connected A0 CLI host.
Shown when a connected A0 CLI advertises remote execution, which the user enables
with F4 in the CLI. Runs shell-backed execution on the machine where that CLI is
running. Use this tool, not `code_execution_tool`, when the user asks for the
connected local terminal, the A0 CLI host, their local machine, or explicitly
says not to use Docker/server/container execution.
For complex local project work, optionally load skill `host-code-execution`.

Availability and permissions are checked when the tool runs. If no CLI is
connected, remote execution is disabled, or local access is not Read&Write for a
mutating command, report that to the user instead of falling back to server-side
execution.

Do not use this tool as a fallback for host-browser navigation/control. For
"my browser", host browser, local browser/Chrome, or opening a URL in the host
browser, use the `browser` tool. If Browser reports missing Chrome
remote-debugging consent, tell the user to open `chrome://inspect/#remote-debugging`,
enable "Allow remote debugging for this browser instance", run `/browser host on`,
and retry.

## Arguments
- `runtime`: one of `terminal`, `python`, `nodejs`, `output`, `reset`
- `session`: integer session id (default `0`)
- `reset`: optional boolean for `terminal`, `python`, or `nodejs`; when true,
  the CLI resets the session before running the supplied code

Runtime-specific fields:
- `terminal`, `python`, `nodejs`: require `code`
- `reset`: optional `reason`

## Notes
- Reuse `session` when continuing a workflow.
- Use `input_remote` to answer interactive terminal prompts in that session.
- Use `output` to poll a running session and `runtime=reset` for a stuck session.
  Use `reset: true` on a new command when you need a clean session and want to
  run the replacement command immediately.
- Execution and output polling timeouts follow the normal `_code_execution`
  plugin settings. For builds, installs, servers, tests, training, and other
  long work, redirect logs and poll with `runtime=output`.
- Paths and shell syntax are evaluated on the CLI host, not inside Agent Zero.
- When the user gives a relative path like `tmp/file.txt`, keep it relative to
  the CLI host terminal. Do not prepend or `cd` to `/a0/usr/workdir`; that is the
  Agent Zero server/Docker workdir, not the connected local terminal folder.
- If the current terminal folder matters, run `pwd` first or include `pwd` in
  the same command without changing directories.

## Usage
~~~json
{
  "thoughts": [
    "The user asked for the connected local terminal rather than Docker, so I should execute on the A0 CLI host."
  ],
  "headline": "Running command on connected local terminal",
  "tool_name": "code_execution_remote",
  "tool_args": {
    "runtime": "terminal",
    "session": 0,
    "code": "pwd"
  }
}
~~~

```

## plugins/_a0_connector/prompts/agent.system.tool.computer_use_remote.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_a0_connector/prompts/agent.system.tool.computer_use_remote.md

SHA-256: `78b4e223f05c08519d8dfc45e0db62d68e03dcca146955cc8de010413e31132d`

````
### computer_use_remote

Control the desktop on a connected A0 CLI or Launcher host.
Shown when a connected A0 CLI or Launcher host gateway advertises enabled Computer Use (`/computer-use on`) and does not need re-arming. Runtime-gated beta desktop control through that host bridge on the user's machine. Availability, backend support, and trust mode are checked when the tool runs, together with host-bridge presence, local enablement, and re-arm state. Computer Use enablement is scoped to the current CLI session or Launcher Host access lease, not scoped to a single chat context.

Use this for native host desktop UI inspection, screenshots, background-safe window/element actions when supported, clicking, scrolling, typing, key presses, and status checks. Do not use it for ordinary web-page navigation or host-browser control; use the browser tool for web pages unless browser automation cannot express the task. For complex desktop workflows, load and follow skill `host-computer-use` before proceeding.

This is the primary desktop-control path for the user's connected host/local computer. Do not substitute the `linux-desktop` skill, the Agent Zero Desktop/Xpra surface, `desktopctl.sh`, `code_execution_tool`, or Docker/server shell commands for host screen actions; those target the internal Agent Zero runtime and cannot see or control the user's host screen. For a Linux X11/XWayland app limitation during an authorized session, the Linux host skill documents a narrow host-shell workflow that also requires enabled host code execution; it never bypasses a disabled or denied Computer Use grant.

If the tool reports no CLI/Launcher host bridge, disabled computer use, or `COMPUTER_USE_REARM_REQUIRED`, stop and tell the user to run `/computer-use on` in the A0 Launcher chat when using Launcher Host access, or in A0 CLI otherwise, and approve any host permission prompt.

If a Computer Use call returns an error, do not repeat the same action with identical arguments. Report the error, or use a materially different safe recovery only when the task still requires it.

Start with the needed observation: `list_windows` for native apps or `capture` for a screen. A missing chat session starts automatically within that call using the host's existing enablement and normal permission checks; do not make a separate `start_session` call for every chat. Existing sessions are reused. `start_session` remains available explicitly; `status` only reports state and `stop_session` stops the current chat's session. Read `backend_id`, `backend_family`, `features`, and the structured `capabilities` object in results. When capabilities report native windows, window state, element indexes, and background dispatch, prefer `list_windows` -> `get_window_state` -> `element_action` with `dispatch: "background"` before using global coordinates. Interactive coordinate actions should use normalized global-screen coordinates from the most recent capture.

Some actions are backend-specific and intentionally documented only in backend skills. If a result reports backend-specific features or tells you to load a backend skill, load and follow that skill before using those backend-only actions. For structural targeting details, load and follow the backend-specific skill such as `host-computer-use-macos` or `host-computer-use-windows`; do not apply one backend's guidance to another backend.

State-changing actions automatically attach a fresh screen after they run unless the backend returns a definitive structural background result. Treat key presses, clicks, scrolling, and typing as attempts, not success; treat foreground fallbacks the same way. Inspect the latest attached screen, or one explicit `capture` if it is unclear or unchanged, before saying the requested outcome happened. If the tool says a screen was attached but you cannot actually inspect the image, stop and report that visual verification is unavailable; do not continue by assuming the host state. A `type` result proves only that keyboard events were sent unless it explicitly reports both `focus_verified=true` and the target `window_id`.

When Linux advertises `verified-window-focus` and `target-verified-keyboard-input`, use a real frame/window from `list_windows`, inspect it with `get_window_state`, and focus its window element with foreground `element_action`. Continue to `type` only after the focus result reports `focus_verified=true`, and pass that same `window_id` to `type`. Never press an application/frame/window node to activate it.

```json
{
  "tool_name": "computer_use_remote",
  "tool_args": {
    "action": "list_windows"
  }
}
```

Required argument:
- `action`: one of `start_session`, `status`, `capture`, `list_windows`, `get_window_state`, `element_action`, `move`, `click`, `scroll`, `key`, `type`, `stop_session`; backend skills may document additional backend-only action values

Optional arguments by action:
- `session_id`: optional explicit session binding; normally omit it to reuse this chat's session. An explicit stale/mismatched ID fails without automatic replacement.
- `pid`, `window_id`: target a native window for `get_window_state`, `element_action`, scoped backend snapshots, and guarded keyboard input when supported
- `element_index`: top-level index from the latest `get_window_state`
- `target`: backend-specific semantic fields for `element_action`
- `max_depth`, `max_nodes`: bound snapshot size for `get_window_state` and supported backend snapshots
- `include_hidden`, `include_offscreen`, `max_windows`: optional filters for `list_windows`
- `selector`: Windows UIA selector from the latest snapshot
- `path`: element path from the latest backend snapshot
- `operation`: action such as `invoke`, `press`, `set_value`, `focus`, or backend-specific operations
- `value`: replacement for `set_value`
- `dispatch`: `background`, `auto`, or `foreground`; prefer `background` for `element_action`
- `x`, `y`: normalized `[0,1]` global-screen coordinates for `move` and `click`
- `button`: `left`, `right`, or `middle` for `click`
- `count`: click count for `click`
- `dx`, `dy`: scroll amounts for `scroll`
- `key` or `keys`: key press value for `key`
- `text`: text to type for `type`; Linux target-verified input also requires `window_id`
- `submit`: boolean Enter-after-type flag for `type`

Status/session results may include `contract_version` and `capabilities`. Treat `capabilities.identity.pid`, `capabilities.identity.window_id`, `capabilities.identity.element_index`, and `capabilities.dispatch.background` as the authoritative cross-platform contract for whether the native background loop is available. Use `features` for backend-specific refinements and skill selection.

````

## plugins/_a0_connector/prompts/agent.system.tool.input_remote.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_a0_connector/prompts/agent.system.tool.input_remote.md

SHA-256: `06a37aa3247dd46a4cf04071a6eb975e7f6f84ce0e060cc30ce224a988ef66c6`

```
### input_remote
send keyboard input to an interactive program on the connected CLI host
args: `keyboard`, optional `session` (default `0`)
requires permission for `input_remote` and the `terminal` action of `code_execution_remote`; keyboard input can run shell commands
uses the same connection and Read&Write gates as `code_execution_remote`
for Agent Zero's own terminal, use `input`; for host browser actions, use `browser`
usage:
~~~json
{
  "tool_name": "input_remote",
  "tool_args": {
    "keyboard": "Y",
    "session": 0
  }
}
~~~

```

## plugins/_a0_connector/prompts/agent.system.tool.text_editor_remote.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_a0_connector/prompts/agent.system.tool.text_editor_remote.md

SHA-256: `87a8853cf07583e209a91cf21090469ce08b8f824c229769ef614d9879fddc7b`

```
# text_editor_remote tool

Read, write, and patch files on a connected A0 CLI host.
Shown when a connected A0 CLI advertises remote file access. Reads, writes, and
patches files on the machine where that CLI is running. Use this tool, not
server-side file tools, when the user asks for files on the connected local
machine, A0 CLI host, or explicitly says not to use Docker/server files. For
complex remote edits, optionally load skill `host-file-editing`.

Availability and permissions are checked when the tool runs. If no CLI is
connected, remote file access is disabled, or a write/patch needs Read&Write,
report that to the user instead of falling back to server-side file tools.

## Arguments
- `action`: `read`, `write`, or `patch`
- `path`: file path on the CLI host filesystem
- `read`: optional `line_from`, `line_to`
- `write`: requires `content`
- `patch`: requires one of `old_text` + `new_text`, `patch_text`, or `edits`

## Notes
- Prefer `read` before line-number edits.
- If the user says patch, change without rewriting, or don't rewrite, use `action: "patch"` instead of `write`.
- For simple "change X to Y" requests, prefer exact replace with `old_text` and `new_text`; `old_text` must match one exact current span.
- Prefer `patch_text` for context-anchored changes and `edits` only for fresh, surgical line ranges.
- If freshness checks reject a line patch, reread the file and retry with updated ranges.
- Reads are text previews capped at 2,000 lines and 256 KiB. When the result says
  it was truncated, continue from its `next_line` only if another bounded text
  slice is sufficient.
- Write and patch content is capped at 256 KiB. For a complete large file or any
  binary file, use the authenticated CLI/Core HTTP upload or download path; do
  not retry it through this WebSocket tool. If no HTTP-facing action is available
  in the current task, ask the user to attach/upload the file through A0 CLI.
- Relative paths are relative to the CLI host filesystem. Do not rewrite them to
  `/a0/usr/workdir`; that path belongs to the Agent Zero server/Docker side.

## Usage
~~~json
{
  "thoughts": [
    "The user asked for a file on the connected local machine, so I should read it through the A0 CLI host."
  ],
  "headline": "Reading file on connected local machine",
  "tool_name": "text_editor_remote",
  "tool_args": {
    "action": "read",
    "path": "README.md",
    "line_from": 1,
    "line_to": 80
  }
}
~~~

```

## plugins/_a2a_chat/prompts/agent.system.tool.a2a_chat.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_a2a_chat/prompts/agent.system.tool.a2a_chat.md

SHA-256: `587e3f7528fc2bd547d4aff8f0fc51f5a840f0d0bfca0ebb523881683d622749`

```
### a2a_chat
chat with a remote FastA2A-compatible agent; remote context is preserved automatically per `agent_url`
args: `agent_url`, `message`, optional `attachments[]`, optional `reset`
- `agent_url`: base url, accepts `host:port`, `http://host:port`, or a full `/a2a` url
- `message`: text to send to the remote agent
- `attachments[]`: optional absolute uris or paths to send with the message
- `reset`: json boolean; use `true` to start a fresh conversation with the same `agent_url`
do not send `context_id`; the tool handles that internally
example:
~~~json
{
  "thoughts": ["I need to ask a remote agent and keep the session for follow-up."],
  "headline": "Contacting remote FastA2A agent",
  "tool_name": "a2a_chat",
  "tool_args": {
    "agent_url": "http://weather.example.com:8000/a2a",
    "message": "What's the forecast for Berlin today?",
    "attachments": [],
    "reset": false
  }
}
~~~

```

## plugins/_behavior_adjustment/prompts/agent.system.tool.behavior.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_behavior_adjustment/prompts/agent.system.tool.behavior.md

SHA-256: `4f3f7f26646fae8e9a99604b6e619830ea9815b5738ce76f09ec11bd90953581`

```
### behavior_adjustment
update persistent behavioral rules
arg: `adjustments` text describing what to add or remove
use for durable behavior, personality, style, response-format, greeting, and exact-response rules
when the user asks for an exact word, phrase, token, or casing, preserve it verbatim in `adjustments`

```

## plugins/_browser/prompts/agent.system.tool.browser.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_browser/prompts/agent.system.tool.browser.md

SHA-256: `441db4b0d8ebd38b12a2842daecce06a1e7ea3e99e36dfe32f9cc01f22a59b69`

```
### browser
Rendered browser automation for pages that need interaction, JavaScript, forms, downloads, screenshots, or visual inspection.

Prefer `search_engine` or `document_query` for plain text research. The tool must not open a Browser surface automatically. Use the tool headlessly unless the user opens the Browser surface or asks for the optional visible WebUI viewer.

For rendered browsing workflows, multi-step interaction, screenshots, downloads, uploads, forms, or host/container mode decisions, first load `browser-automation` with `skills_tool:load`, then call this tool using the loaded instructions. For fragile forms, `browser-automation` links to `browser-form-workflows`; load it when selects, checkboxes, radios, uploads, contenteditable fields, validation, or submission state are central.

Actions: tabs `open`, `list`, `state`, `set_active`, `navigate`, `back`, `forward`, `reload`, `close`, `close_all`; inspect `content`, `detail`, `screenshot`; interact `click`, `hover`, `double_click`, `right_click`, `drag`, `type`, `submit`, `type_submit`, `scroll`, `select_option`, `set_checked`, `upload_file`; advanced `evaluate`, `key_chord`, `mouse`, `wheel`, `keyboard`, `clipboard`, `set_viewport`, `multi`.

Rules:
- If the user asks for an existing tab, page title, or already-open URL, call `list` first, match by `title` or `currentUrl`, then use `set_active` or `navigate` on that `browser_id`.
- Prefer DOM/CDP actions: use refs from the latest `content`, including frame-chain refs, or same-page `selector`; use coordinates only when refs/selectors are unavailable or the user explicitly asks for visual manual control.
- Screenshots are explicit only; the browser does not automatically load screenshots. Call `vision_load` with the returned `vision_load.tool_args.paths` value before reasoning visually.
- Keep the tab set small; close pages after extracting what you need.
- `multi` is only a browser action: use `tool_name: "browser"` with `tool_args.action: "multi"`. Never use `tool_name: "multi"`.
- `evaluate` requires `script`: a JavaScript expression or function whose result is returned. Browser settings bound execution; timeout recovery may reload the tab.

```

## plugins/_code_execution/prompts/agent.system.tool.code_exe.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_code_execution/prompts/agent.system.tool.code_exe.md

SHA-256: `c9efac5e46b525b810c4ba5312217eb17208b3e732b371d8fb10795ac69ad577`

```
### code_execution_tool
run terminal, python, or nodejs commands
args:
- `runtime`: `terminal`, `python`, `nodejs`, or `output`
- `code`: command or script code
- `session`: terminal session id; default `0`
- `reset`: kill a session before running; `true` or `false`
rules:
- place the command or script in `code`
- use `runtime=output` to poll running work
- use `input` for interactive terminal prompts
- if a session is stuck, call again with the same `session` and `reset=true`
- check dependencies before running code
- replace placeholder or demo data with real values before execution
- use `print()` or `console.log()` when you need explicit output
- do not interleave other tools while waiting
- treat trailing framework `[SYSTEM: ...]` info as execution status, not command output; use it to decide whether to wait, reset, rerun, or continue
- probe cwd files tools and dependencies before expensive commands
- split long work into small commands: inspect, prepare, run, verify
- for builds installs servers training and long tests, redirect logs and poll with `runtime=output`
- after timeout or pause, inspect logs and processes before deciding wait reset or stop
- never claim success from timeout partial output or a still-running command
- stop stale background processes you started before final response
- when exact output matters, verify file path line count bytes and content with commands
examples:
1 terminal command
~~~json
{
    "thoughts": [
        "Need to do...",
        "Need to install..."
    ],
    "headline": "Installing zip package via terminal",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "terminal",
        "session": 0,
        "reset": false,
        "code": "apt-get install zip"
    }
}
~~~

2 execute python code

~~~json
{
    "thoughts": [
        "Need to do...",
        "I can use...",
        "Then I can..."
    ],
    "headline": "Executing Python code to check current directory",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "python",
        "session": 0,
        "reset": false,
        "code": "import os\nprint(os.getcwd())"
    }
}
~~~

3 execute nodejs code

~~~json
{
    "thoughts": [
        "Need to do...",
        "I can use...",
        "Then I can..."
    ],
    "headline": "Executing Javascript code to check current directory",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "nodejs",
        "session": 0,
        "reset": false,
        "code": "console.log(process.cwd());"
    }
}
~~~

4 wait for output with long-running scripts
~~~json
{
    "thoughts": [
        "Waiting for program to finish..."
    ],
    "headline": "Waiting for long-running program to complete",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "output",
        "session": 0
    }
}
~~~

2 python snippet
~~~json
{
  "thoughts": ["A short Python check is faster than using the shell."],
  "headline": "Running Python snippet",
  "tool_name": "code_execution_tool",
  "tool_args": {
    "runtime": "python",
    "session": 0,
    "reset": false,
    "code": "import os\nprint(os.getcwd())"
  }
}
~~~

3 wait for running output
~~~json
{
  "thoughts": ["The previous command is still running, so I should poll for output."],
  "headline": "Waiting for command output",
  "tool_name": "code_execution_tool",
  "tool_args": {
    "runtime": "output",
    "session": 0
  }
}
~~~

```

## plugins/_code_execution/prompts/agent.system.tool.input.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_code_execution/prompts/agent.system.tool.input.md

SHA-256: `334338f2661018620dc758ca7c4348ddfae8d261a27dc3bf56409f85af28ef65`

```
### input:
send keyboard input to an interactive terminal program
use keyboard arg for terminal program input
use session arg for terminal session number
answer dialogues enter passwords etc
not for browser
usage:
~~~json
{
    "thoughts": [
        "The program asks for Y/N..."
    ],
    "headline": "Responding to terminal program prompt",
    "tool_name": "input",
    "tool_args": {
        "keyboard": "Y",
        "session": 0
    }
}
~~~

```

## plugins/_document_query/prompts/agent.system.tool.document_query.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_document_query/prompts/agent.system.tool.document_query.md

SHA-256: `56fcf86798f6bb1e1af1ecb95f26b6549d9890c440c20da68e037fa57b885376`

```
### document_query
read, extract, summarize, compare, or answer questions over local/remote documents, PDFs, Office files, HTML/text/code files, large text-heavy files, and fallback OCR when vision tools cannot read a document image/scan.

For document Q&A, document/code analysis, multi-document comparison, PDF or large-file extraction, or fallback OCR after vision tools are unavailable or insufficient, first load the `document-query` skill with `skills_tool:load`, then call this tool using the loaded instructions.

Use vision tools first for image files, screenshots, scans, charts, photos, diagrams, and other visual inputs when available. Call `document_query` for images only as document-style OCR fallback when vision cannot read the needed text.

Minimal args after loading the skill:
- `document`: one local path/URL or a list of paths/URLs
- `queries`: optional list of questions; omit it to return extracted document content

Use normal user-facing language in final answers. Keep parser/runtime details internal.

```

## plugins/_goal/prompts/agent.system.tool.goal.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_goal/prompts/agent.system.tool.goal.md

SHA-256: `923ffe70914eaee41e47f5cdee8638cdcf168e00b98b0ddbbee99a4b77377303`

```
### goal
Inspect, create, or finish the current chat goal.

Use this only when the user asks for a goal or asks you to manage one. Do not create goals for casual replies or ordinary one-shot answers.

Actions:
- `get`: inspect the current goal; use this before creating one when its state is unknown.
- `create`: make the given concise `objective` active; optional positive `token_budget`.
- `update`: mark the current goal with `status` `complete` or `blocked`; optional revised `objective` and `note`.

Pause, resume, edit, and delete are user controls. Mark `complete` only after achieving the objective; mark `blocked` only after viable alternatives are exhausted and work cannot continue without user input or an external-state change.

Example:
~~~json
{
  "thoughts": ["The user asked me to manage this task as a goal."],
  "headline": "Creating goal",
  "tool_name": "goal",
  "tool_args": {
    "action": "create",
    "objective": "Add the built-in goal plugin with a Web UI strip and slash command"
  }
}
~~~

```

## plugins/_memory/prompts/agent.system.tool.memory.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_memory/prompts/agent.system.tool.memory.md

SHA-256: `71446bcfdc415a4836dca5c4ef8ee5e88f94a0be498df8169e542026bd98b864`

```
## memory tool
use when durable recall or storage is useful
- `memory`: args `action`, then `query`, `text`, `ids`, `area`, `threshold`, `limit`, `filter` as the action needs
- actions: `load` searches by meaning and metadata, `save` stores durable information and returns a memory ID, `delete` removes memories by comma-separated IDs, `forget` finds and removes matching memories

notes:
- `threshold` is similarity from `0` to `1`
- `filter` is a metadata expression (e.g. `area=='main'`)
- confirm destructive changes when accuracy matters
- memories usually include timestamp metadata; use it as a soft recency signal, not a hard TTL
- when the user updates a durable fact/preference, load related memories first, forget/delete superseded versions, then save one complete current version
- do not append a second memory for the same mutable subject when the new statement replaces the old one
- do not forget a memory only because it is old; forget it when current evidence shows it is stale, false, superseded, duplicated, or unwanted
- `forget` also cleans exact matches and derived fragment/solution records related to removed memories
- use `save` for stable current facts, not short-lived test markers, greetings, or one-off conversation events

example:
~~~json
{
  "thoughts": ["I should search memory for relevant prior guidance."],
  "headline": "Loading related memories",
  "tool_name": "memory",
  "tool_args": {
    "action": "load",
    "query": "tool argument format",
    "threshold": 0.7,
    "limit": 3
  }
}
~~~

```

## plugins/_notify_user/prompts/agent.system.tool.notify_user.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_notify_user/prompts/agent.system.tool.notify_user.md

SHA-256: `fba91d310b9607b8a48a778b785758de0a465253812cac7da4f5ec71f14022eb`

```
### notify_user
send a notification for chats that run on their own for a longer time
notifications are non-blocking: they inform an absent user, never ask for a decision, never wait
send only when the user's picture of the work became false or stale: a strategy, plan, or assumption the user has seen was dropped, replaced, or contradicted
never send about starting, resuming, continuing, or attempting work
task end and goal completion go to the final response, never here
before sending, name what changed in the user's picture of the work and what comes next; if either is missing, do not send
if your last notification already covered this change, do not send again
if another agent delegated this work, do not send; report back to it instead
write the message so it makes sense without the chat open
notifications supplement, never replace, the final response
args: `message` required, short self-contained message in plain language
`type`: `info`, `progress`, `success`, `warning`, `error`; defaults to `info`
`priority`: `normal` or `high`; omit for `high`
`title`: optional short label
`detail`: optional longer supporting text
example:
~~~json
{
  "thoughts": ["Staging is unreachable, so the plan changed."],
  "headline": "Notifying strategy change",
  "tool_name": "notify_user",
  "tool_args": {
    "title": "Strategy changed",
    "message": "Staging unreachable; using recorded fixtures instead."
  }
}
~~~

```

## plugins/_office/prompts/agent.system.tool.office_artifact.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_office/prompts/agent.system.tool.office_artifact.md

SHA-256: `ad2f0cdf8e79e0fea1797ff38c0f1c17ecef7faff7ef247fbd644b37139ee3b0`

```
### office_artifact
create/open/read/edit/export Office artifacts in Agent Zero
formats: odt ods odp docx xlsx pptx
defaults: document->odt spreadsheet->ods presentation->odp
actions: create open read edit inspect export version_history restore_version status
common args: action kind title format content path file_id operation find replace sheet
optional UI intent args: open_in_canvas open_in_desktop
export takes optional `target_format` to convert the saved artifact to another office format
read takes optional `max_chars` to bound returned text (default 12000)
edit takes optional `count` (replace first N occurrences) and `preview_chars` (bound the response preview)
restore_version requires `version_id` from version_history
Office formats only; use `text_editor` for Markdown and plain text files
create/read/edit results save or update artifacts only; they do not open a surface automatically unless the user explicitly asks to open the document UI
use action `open`, `open_in_canvas: true`, or `open_in_desktop: true` only when the user explicitly asks to open the Office document/Desktop
automatic refresh is separate from UI opening: already-open Desktop/Office surfaces refresh after saved tool results without any flag
for action `edit`, use operation and put append/prepend/set text in `content` (example: operation `append_text`, content "new line")
after create/edit, answer briefly with what changed and the saved path when useful; do not write faux UI action labels like "Open document" or "Download file"
ODF is first-class for LibreOffice: use ODT for Writer, ODS for Spreadsheet/Calc, and ODP for Presentation/Impress unless the user explicitly requests OOXML compatibility
DOCX/XLSX/PPTX are compatibility formats, not defaults
XLSX charts: use edit operation `create_chart` with `chart` object instead of code execution for embedded spreadsheet charts when an embedded chart is required
chart types: line bar column pie area scatter stock ohlc candlestick
ODS/XLSX create/edit tabular content: CSV, TSV, Markdown tables, or rows arrays become real spreadsheet cells
for nontrivial office artifact work, load the Writer/Calc/Impress skill that matches the requested format

```

## plugins/_scheduler/prompts/agent.system.tool.scheduler.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_scheduler/prompts/agent.system.tool.scheduler.md

SHA-256: `f0b8c4870d8fd6539a925841dd2feb8faa74935513aa5daf34d61441b17d9194`

```
### scheduler
Manage saved tasks and schedules. For complex task work, load skill `scheduled-tasks`.

Actions: `list_tasks`, `find_task_by_name`, `show_task`, `run_task`, `update_task`, `delete_task`, `create_scheduled_task`, `create_adhoc_task`, `create_planned_task`, `wait_for_task`.

Common args: `action`, `name`, `uuid`, `system_prompt`, `prompt`, `attachments`, `schedule`, `timezone`, `plan`, `dedicated_context`.

Rules:
- Before `create_*`, `update_task`, `delete_task`, or `run_task`, inspect existing tasks with `find_task_by_name` or `list_tasks`.
- Do not run scheduled/planned tasks unless the user asks to run now.
- Do not create recursive task prompts that schedule more tasks.
- New tasks use a dedicated context unless `dedicated_context` is `false`.
- Use `create_scheduled_task` for recurring/cron tasks; `schedule` must be cron fields, not an ISO datetime.
- For one planned date/time, use `create_planned_task` with `plan: ["YYYY-MM-DDTHH:MM:SS"]`.
- Use IANA timezones like `Europe/Rome`; include timezone when the user names a timezone.
- For "tomorrow at 9:15 Rome time", scheduled shape is `schedule: {"minute":"15","hour":"9","day":"11","month":"5","weekday":"*","timezone":"Europe/Rome"}`.

```

## plugins/_search_engine/prompts/agent.system.tool.search_engine.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_search_engine/prompts/agent.system.tool.search_engine.md

SHA-256: `0e54de740d8421f97a204f1f7be1cb7996c557f011bded05e5fc0ae1d4333e89`

```
### search_engine
find live news, prices, and other real-time web data
arg: `query` (keyword-based text search query)
returns urls, titles, and descriptions

query rules:
- use keywords, names, exact phrases, model/version numbers, dates, and domains
- do not write a natural-language question or sentence
- omit filler words like "what", "who", "can you tell me", "find information about"
- use 3-10 high-signal terms; add alternatives only when they improve recall
- bad: "What is the latest LiteLLM release and what changed?"
- good: "LiteLLM latest release notes changelog"

example:
~~~json
{
  "thoughts": ["I need current information rather than relying on memory."],
  "headline": "Searching the web",
  "tool_name": "search_engine",
  "tool_args": {
    "query": "LiteLLM latest release notes changelog"
  }
}
~~~

```

## plugins/_text_editor/prompts/agent.system.tool.text_editor.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_text_editor/prompts/agent.system.tool.text_editor.md

SHA-256: `351a428d1f686fa86f4f133c1c3b52f4d02b81decc5a5667ebab3e3ecc4c7ed3`

```
### text_editor
canonical text and Markdown file read write patch with numbered lines
not code execution rejects binary
terminal (grep find sed) advance search/replace
actions: read write patch
common args: action path
optional UI intent args: open_in_canvas
use this tool for Markdown and plain text files; use `office_artifact` only for Office packages such as odt ods odp docx xlsx pptx
if the user explicitly asks to open the Markdown file in the canvas/Editor after a write or patch, set `open_in_canvas: true`; otherwise omit UI flags because already-open Editor sessions refresh automatically

#### read
read file with numbered lines
args path line_from line_to (inclusive optional)
no range -> first {{default_line_count}} lines
long lines cropped output may trim by token limit
read surrounding context before patching
usage:
~~~json
{
    "thoughts": ["I need file context before editing."],
    "headline": "Reading file",
    "tool_name": "text_editor",
    "tool_args": {
        "action": "read",
        "path": "/path/file.py",
        "line_from": 1,
        "line_to": 50
    }
}
~~~

#### write
create/overwrite file auto-creates dirs
args path content
for Markdown files, include `open_in_canvas: true` only when the user explicitly asks to open the canvas/Editor
usage:
~~~json
{
    "thoughts": ["I need to create or replace the file content."],
    "headline": "Writing file",
    "tool_name": "text_editor",
    "tool_args": {
        "action": "write",
        "path": "/path/file.py",
        "content": "import os\nprint('hello')\n"
    }
}
~~~

#### patch
edit existing file. prefer exact replace for simple "change X to Y"; use patch_text for context changes; use edits only right after read for tiny line edits
if the user says patch, change without rewriting, or don't rewrite, use action patch instead of write
args path plus exactly one of: old_text+new_text OR patch_text string OR edits [{from to content}]
for Markdown files, include `open_in_canvas: true` only when the user explicitly asks to open the canvas/Editor
exact replace: `old_text` must be the exact current text span and must match once; `new_text` is the replacement
patch_text uses current file content, no prior read required
patch_text update-only forms:
- insert after anchor: @@ exact existing line then +new lines
- replace: use @@ line before target then -old +new, or @@ old target line then -same old target line +new
- do not repeat the same old line as both a space-context line and a -removed line
- context lines start with space, removals with -, additions with +
- use enough unique context; add @@ anchor when repeated text exists
edits line mode: from/to inclusive, original line numbers from read, no overlaps
edits examples: {from:2 to:2 content:"x\n"} replace; {from:2 to:2} delete; {from:2 content:"x\n"} insert before
for edits, re-read after insert/delete or line-count-changing replace
ensure valid syntax in content (all braces brackets tags closed)
usage:
~~~json
{
    "thoughts": ["I can replace one exact current string without rewriting the whole file."],
    "headline": "Patching file",
    "tool_name": "text_editor",
    "tool_args": {
        "action": "patch",
        "path": "/path/file.py",
        "old_text": "status = 'draft'",
        "new_text": "status = 'ready'"
    }
}
~~~

```

## plugins/_wait/prompts/agent.system.tool.wait.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/plugins/_wait/prompts/agent.system.tool.wait.md

SHA-256: `2b0dbe47a7b445b739653ee7693813f4286ea23de0faa80a81a729b0bd38a5ff`

```
### wait
pause until a duration or timestamp
args: any of `seconds`, `minutes`, `hours`, `days`, or `until` iso timestamp
use only when waiting is actually part of the task

```

## prompts/agent.system.mcp_server.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.mcp_server.md

SHA-256: `0f033eb97ba1be343ad465304cb6d34042442e02230b37533a3c8bafc08dfff6`

```
### MCP server `{{server_name}}` (group only; not a tool)
Context only: {{server_description}}

{{tools}}

```

## prompts/agent.system.mcp_tool.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.mcp_tool.md

SHA-256: `5f20c2e25e243e0eb350b4454b7e03f2c727ed717b8ac1113e32ba3acadcb311`

```
#### MCP tool `{{qualified_name}}`
Allowed operation (exhaustive): {{description}}

##### Input schema for tool_args:
{{input_schema}}

##### Usage:
{
    "thoughts": ["..."],
    "tool_name": "{{qualified_name}}",
    "tool_args": !follow schema above
}

```

## prompts/agent.system.mcp_tools.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.mcp_tools.md

SHA-256: `136e4c1f3fbf7cca4897a2a418b4749b2df010ed2c4fbd6e3139d17e5c86397c`

```
## "Remote (MCP Server) Agent Tools" available:

Server descriptions are context, not callable capabilities. Each "Allowed operation" line is exhaustive. Never infer capabilities from tool names or input schemas, repurpose inputs, or substitute a different operation. If no allowed operation matches, report that the capability is unavailable.

{{tools}}

```

## prompts/agent.system.mcp_tools.native.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.mcp_tools.native.md

SHA-256: `e82de7495af8f6eaf3ca4ff4d11698f1fd183115ef788e4df39b9afad693b8f7`

```
## MCP server context
Server descriptions are context, not callable capabilities. Each provided function's description defines its allowed operation. Never infer capabilities from names or schemas, repurpose inputs, or substitute a different operation. If no allowed operation matches, report that the capability is unavailable.

{{tools}}

```

## prompts/agent.system.response_tool_tips.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.response_tool_tips.md

SHA-256: `5973b164818bca82106aef58c40d1417aef0e8967424565f6192bdf449b076d3`

```
always deliver the final user-facing answer through this `response` tool's `text` arg
for long existing text, use `§§include(path)` instead of rewriting

```

## prompts/agent.system.tool.call_sub.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tool.call_sub.md

SHA-256: `b0ba7571475dd814dbacca976c7ac2bd272cff44105d73b32f53e92bc94bae52`

```
### call_subordinate
{{if current_profile}}
your current profile: {{current_profile}}
{{endif}}
delegate specialized work outside your own profile; do work matching your profile yourself
same-profile agents only for distinct bounded subtasks or independent reviews, never to pass on your whole task
when a request calls for independent perspectives, challenges, or workstreams, delegate them to separate subordinates instead of performing them all with parent tools
args: `message`, optional `profile`, `name`, `reset`, `context_id`, `attachments`
- `profile`: optional prompt profile key for the subordinate; when provided, it must exactly match an available profile; leave empty for the default profile
- `name`: optional label for the child context, shown in listings; defaults to a short task label for new children; when continuing a child, a non-empty name updates its label
- `reset`: use json boolean `true` to create a fresh child; use `false` to continue the default child or the supplied `context_id`
- `context_id`: stable child ID returned by an earlier direct or parallel call; use it with `reset: false` to continue that exact child
- `attachments`: optional list of file paths/URIs passed to the subordinate's task message so it can read them directly
- `message`: define role, goal, and the concrete task
each caller creates its next agent level: A0 creates A1 children, A1 creates A2 children, and so on
after the subordinate returns, answer from its result directly when it satisfies the user request
do not repeat the same solving work or call extra tools after a sufficient subordinate result
example:
~~~json
{
  "thoughts": ["Need focused external research before I continue."],
  "headline": "Delegating research subtask",
  "tool_name": "call_subordinate",
  "tool_args": {
    "profile": "researcher",
    "message": "Research Italy AI trends and return key findings.",
    "reset": true
  }
}
~~~
reuse long subordinate output with `§§include(path)` instead of rewriting it
{{if agent_profiles}}
available profiles:
{{agent_profiles}}
{{endif}}

```

## prompts/agent.system.tool.call_sub.py

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tool.call_sub.py

SHA-256: `a02c8eb41a96b43abbe37b5a070c675f3ead487fdb83a700c3c159a4d4fd50a6`

```
from typing import Any, TYPE_CHECKING
from helpers.files import VariablesPlugin
from helpers import projects, subagents

if TYPE_CHECKING:
    from agent import Agent


class CallSubordinate(VariablesPlugin):
    def get_variables(
        self, file: str, backup_dirs: list[str] | None = None, **kwargs
    ) -> dict[str, Any]:

        # current agent instance
        agent: Agent | None = kwargs.get("_agent", None)
        # current project
        project = projects.get_context_project_name(agent.context) if agent else None
        # available agents in project (or global)
        agents = subagents.get_available_agents_dict(project)

        profiles = [
            " ".join(
                f"- {name} ({profile.title}): {profile.context.strip() or profile.description}".split()
            )
            for name, profile in agents.items()
        ]
        return {
            "current_profile": (agent.config.profile or "default") if agent else None,
            "agent_profiles": "\n".join(profiles) or None,
        }

```

## prompts/agent.system.tool.parallel.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tool.parallel.md

SHA-256: `77e54e3c08f858e487148489f8f4b06aafa17fb0c072c560e4b18d8f5b32c1a4`

```
### parallel
run independent tool calls concurrently, or await/cancel background parallel jobs.

Use only for independent work. Each `tool_calls` item is a normal tool request object using the same `tool_name` and `tool_args` shape as a top-level reply: `{ "tool_name": "...", "tool_args": { ... } }`.
Only `tool_name` and `tool_args` are used; if an item is copied from a full reply object, planning fields like `thoughts` or `headline` are ignored.
Batch all independent calls that are ready now into one `tool_calls` list, even when they use different tools. Do not split by tool type.

Rules:
- do not use for one simple call, dependent steps, ordered steps, shared mutable state, or state/tool-availability changes that must happen in the parent context
- never nest `parallel`
- Never include `document_query` in `tool_calls`; it is too heavy for parallel workers, so call it sequentially.
- Call `goal` sequentially in the owning chat; direct parallel workers have a temporary context and cannot read or update the parent goal.
- Call `response` only as a top-level tool so it ends the message loop; never wrap it inside `parallel.tool_calls`.
- `call_subordinate` uses the same child lifecycle here as it does top-level; fresh siblings are next-level agents, and each job's `context_id` can be continued later with `reset: false`
- Local code jobs keep their own terminal alive until the command finishes or the job is cancelled. Use `wait: false` for long-running servers; await or cancel them with `job_ids`, including after an output timeout.
- Terminal session numbers are isolated per job. Do not send `input`, `runtime: output`, or `runtime: reset` as a new parallel call; use sequential terminal calls for persistent interactive sessions.
- use `wait: false` only when you will collect results later with `job_ids`
- if extras list running or ready parallel jobs, collect them before final synthesis
- `timeout` only limits how long this call waits; running jobs continue and can be awaited again by `job_ids`

Args: `tool_calls`, `job_ids`, `wait` default `true`, `action` as `start|await|collect|cancel`, `timeout`.

Start and wait:
~~~json
{
  "tool_name": "parallel",
  "tool_args": {
    "tool_calls": [
      {"tool_name": "call_subordinate", "tool_args": {"message": "Review option A and return key risks.", "reset": true}},
      {"tool_name": "search_engine", "tool_args": {"query": "official API changelog release notes"}}
    ],
    "wait": true
  }
}
~~~

Collect existing jobs:
~~~json
{"tool_name": "parallel", "tool_args": {"action": "await", "job_ids": ["job-id"], "timeout": 300}}
~~~

```

## prompts/agent.system.tool.response.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tool.response.md

SHA-256: `ca1cce965f7f2dc0f9d0e70a49dcc5ffaeb1db7c4edb9f038148079ad9e987ae`

```
### response:
final answer to user
ends task processing use only when done or no task active
args: `text`
default to balanced, concise answers: informative but tight, not terse and not verbose.
usage:
~~~json
{
    "thoughts": [
        "...",
    ],
    "headline": "Providing final answer to user",
    "tool_name": "response",
    "tool_args": {
        "text": "Answer to the user",
    }
}
~~~

{{ include "agent.system.response_tool_tips.md" }}

```

## prompts/agent.system.tool.skills.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tool.skills.md

SHA-256: `c4cf4bf850066053ebf9a14ff5a4fe3b7dd884b55cb5dc223537b7ff6fbcf245`

```
### skills_tool
use skills only when relevant
actions: list search load read_file
common args: action skill_name query file_path
workflow:
- action `search`: find candidate skills by keywords or trigger phrases from the current task
- action `list`: discover available skills
- action `load`: append one skill's full instructions to chat history by `skill_name`
- action `read_file`: open one file inside a loaded skill directory
if the user says "find/search a skill", call `search` before `load` even when the likely skill name seems obvious
`read_file` requires both `skill_name` and `file_path`; load the skill first, then read `SKILL.md` or the named relative file
after loading a skill, follow its instructions and use referenced files or scripts with other tools
reload a skill if its instructions are no longer in context
example:
~~~json
{
  "thoughts": ["The user's request sounds like a skill trigger phrase, so I should search first."],
  "headline": "Searching for relevant skill",
  "tool_name": "skills_tool",
  "tool_args": {
    "action": "search",
    "query": "set up a0 cli connector"
  }
}
~~~

```

## prompts/agent.system.tools.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tools.md

SHA-256: `1622847aabe01ff63092b97a6240a3682a640efd3e0faa4ea0549aa0b6e39fe2`

```
## available tools
use ONLY the tools listed below. match names exactly. do NOT invent tool names.
Action names are not tool names. Do not invent top-level `multi` or generic batch tools. Keep tool-specific actions inside `tool_args` and call one listed tool at a time unless a listed tool explicitly supports concurrency.
{{tools}}

```

## prompts/agent.system.tools_vision.md

Source: https://github.com/agent0ai/agent-zero/blob/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/prompts/agent.system.tools_vision.md

SHA-256: `ceda368794a813db06b2d0f2534b0d2c737ba4eeccb5e102ae4dfcd47834a215`

````
## multimodal vision tools

### vision_load
load images into the model for visual reasoning
args: `paths` list of absolute image paths or tool-returned ephemeral image refs, optional `query` for focused inspection
rules:
- load all relevant images in one call when comparing screenshots or pages
- add `query` when the visual task is narrower than the user's request or derived during the work
- use when the task depends on screenshots, diagrams, scanned documents, charts, or photos
- only bitmaps are supported; convert other formats first if needed
- the tool result includes loaded/skipped image totals and the corresponding path lists
example:
```json
{
  "thoughts": [
    "I need to compare the screenshots."
  ],
  "headline": "Comparing screenshots",
  "tool_name": "vision_load",
  "tool_args": {
    "paths": ["/path/to/before.png", "/path/to/after.png"],
    "query": "Compare the error-banner alignment."
  }
}
```

````
