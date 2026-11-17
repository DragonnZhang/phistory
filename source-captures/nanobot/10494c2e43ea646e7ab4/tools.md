# nanobot — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## nanobot/agent/runner.py

Source: https://github.com/HKUDS/nanobot/blob/382f1b12e8d9b2d4dbe2d2803e5f5b9a6e262972/nanobot/agent/runner.py

SHA-256: `a4c1ceb0d4444138bdba08023cb865a86dde2de6289bc04b8323556873b1ffe7`

```
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, ParamSpec, TypeVar, cast
from uuid import uuid4

from loguru import logger

from nanobot.agent.context import TranscriptInput
from nanobot.agent.context_governance import (
    ContextCompactionState,
    ContextGovernanceConfig,
    ContextGovernor,
    HistoryConsolidator,
    ModelRequestState,
    ProviderCompactionConsolidator,
    TranscriptBuilder,
)
from nanobot.agent.execution_policy import ExecutionControlError
from nanobot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from nanobot.agent.tools.context import tool_log_content_allowed
from nanobot.agent.tools.execution import ToolCallResult, execute_tool_calls
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.events import NO_EVENTS, EventSink, ModelRequestEvent
from nanobot.llm_usage.context import (
    LLMUsageSource,
    bind_llm_usage_source,
    reset_llm_usage_source,
    source_from_session_key,
)
from nanobot.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ProviderCallContext,
    ProviderConversationState,
)
from nanobot.providers.compaction import ProviderStateCommitError
from nanobot.providers.conversation_state import ProviderConversationStateController
from nanobot.session.history_visibility import HIDDEN_HISTORY_META, is_hidden_history_message
from nanobot.session.summary import SessionSummaryCheckpoint
from nanobot.utils.cancellation import shield_and_drain
from nanobot.utils.helpers import (
    build_assistant_message,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    extract_reasoning,
    strip_reasoning_tags,
)
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    build_budget_exhausted_finalization_message,
    build_finalization_retry_message,
    build_length_recovery_message,
    is_blank_text,
)


def _raise_if_cancelling() -> None:
    """Do not resume model work when a dependency suppresses cancellation."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


ContinuationCallback = Callable[[], Awaitable[str | None]]
CheckpointCallback = Callable[[dict[str, Any]], Awaitable[None]]
InjectionCallback = Callable[[], Awaitable[Iterable[Any] | None]]

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_ARREARAGE_ERROR_MESSAGE = (
    "The AI provider rejected the request because the API key is out of quota or the "
    "account is in arrears. Please top up / check the billing status of your API key and try again."
)
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_PromptArgs = ParamSpec("_PromptArgs")
_PromptResult = TypeVar("_PromptResult")


def _restore_outer_whitespace(content: str, original: str | None) -> str:
    if not original:
        return content
    leading_size = len(original) - len(original.lstrip())
    trailing_size = len(original) - len(original.rstrip())
    leading = original[:leading_size]
    trailing = original[-trailing_size:] if trailing_size else ""
    return f"{leading}{content}{trailing}"


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]] | None
    tools: ToolRegistry
    runtime: LLMRuntime
    max_iterations: int
    max_tool_result_chars: int
    transcript_input: TranscriptInput | None = None
    transcript_builder: TranscriptBuilder | None = None
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    workspace: Path | None = None
    session_key: str | None = None
    provider_retry_mode: str = "standard"
    checkpoint_callback: CheckpointCallback | None = None
    consolidate_history: HistoryConsolidator | None = None
    consolidate_provider_compaction: ProviderCompactionConsolidator | None = None
    injection_callback: InjectionCallback | None = None
    terminal_injection_callback: InjectionCallback | None = None
    continuation_callback: ContinuationCallback | None = None
    finalize_on_max_iterations: bool = True
    provider_state: ProviderConversationState | None = None
    llm_usage_source: LLMUsageSource | None = None
    events: EventSink = NO_EVENTS


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of a shared agent execution."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: LLMUsage | None = None
    # One entry per runner-visible model round. Recovery dispatches needed to
    # produce that round's response are folded into the same usage value.
    round_usages: list[LLMUsage] = field(default_factory=list)
    stop_reason: str = "completed"
    error: str | None = None
    failure_error_kind: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False
    # Unstreamed terminal content or tail; excludes any prefix already delivered.
    pending_stream_content: str | None = None
    provider_state: ProviderConversationState | None = field(default=None, repr=False)
    summary_checkpoint: SessionSummaryCheckpoint | None = field(default=None, repr=False)
    provider_compaction_applied: bool = field(default=False, repr=False)


class AgentRunner:
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self) -> None:
        self.context_governor = ContextGovernor()

    async def prepare_prompt(
        self, operation: Callable[_PromptArgs, _PromptResult],
        *args: _PromptArgs.args, **kwargs: _PromptArgs.kwargs,
    ) -> _PromptResult:
        """Share the bounded context worker for prompt filesystem preparation."""
        return await self.context_governor.prepare_in_background(operation, *args, **kwargs)

    async def _run_context_snapshot(
        self, hook: AgentHook, messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not messages or not hook.has_run_hooks():
            return []
        return await asyncio.to_thread(deepcopy, messages)

    @staticmethod
    def _append_injected_messages(
        messages: list[dict[str, Any]],
        injections: list[dict[str, Any]],
    ) -> None:
        """Append injected messages without rewriting the raw transcript."""
        messages.extend(injections)

    async def _try_drain_injections(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        phase: str,
        terminal: bool = False,
    ) -> bool:
        injections = await self._drain_injections(spec, terminal=terminal)
        if not injections:
            return False
        self._append_injected_messages(messages, injections)
        preview = "[content hidden]"
        if tool_log_content_allowed():
            preview = "\n\n".join(
                content for message in injections
                if isinstance(content := message.get("content"), str)
                and not is_hidden_history_message(message)
            )
            preview = preview[:80] + "..." if len(preview) > 80 else preview
        logger.info(
            "Injected {} follow-up message(s) {}: {}",
            len(injections), phase, preview,
        )
        return True

    @staticmethod
    async def _continuation_prompt(spec: AgentRunSpec) -> str | None:
        prompt = await spec.continuation_callback() if spec.continuation_callback is not None else None
        _raise_if_cancelling()
        return prompt

    async def _drain_injections(
        self,
        spec: AgentRunSpec,
        *,
        terminal: bool = False,
    ) -> list[dict[str, Any]]:
        callback = (
            spec.terminal_injection_callback
            if terminal
            else spec.injection_callback
        )
        if callback is None:
            return []
        try:
            items = await callback()
        except ExecutionControlError:
            raise
        except Exception:
            logger.opt(exception=tool_log_content_allowed()).error("injection_callback failed")
            return []
        if not items:
            return []
        injected_messages: list[dict[str, Any]] = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, dict):
                message_item = cast(dict[str, Any], item)
                if message_item.get("role") == "user" and "content" in message_item:
                    if self._has_injection_content(message_item.get("content")):
                        injected_messages.append(message_item)
                continue
            content = getattr(item, "content") if hasattr(item, "content") else str(item)
            if self._has_injection_content(content):
                injected_messages.append({"role": "user", "content": content})
        return injected_messages

    @staticmethod
    def _has_injection_content(content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return bool(cast(list[Any], content))
        return True

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        hook = spec.hook or AgentHook()
        has_run_hooks = hook.has_run_hooks()
        messages: list[dict[str, Any]] = []
        context = AgentRunHookContext(messages=[])
        llm_usage_source_token = bind_llm_usage_source(
            spec.llm_usage_source or source_from_session_key(spec.session_key)
        )

        try:
            try:
                try:
                    messages, compaction = await self.prepare_prompt(self._initial_transcript_and_compaction, spec)
                    context.messages = await self._run_context_snapshot(hook, messages) if has_run_hooks else []
                    await hook.before_run(context)
                    result = await self._run_core(spec, hook, messages, compaction)
                except Exception as exc:
                    context.messages = await self._run_context_snapshot(hook, messages) if has_run_hooks else []
                    context.stop_reason = "error"
                    context.error = f"Error: {type(exc).__name__}: {exc}"
                    context.exception = exc
                    await hook.on_error(context)
                    raise
                else:
                    context.messages = await self._run_context_snapshot(hook, result.messages) if has_run_hooks else []
                    context.final_content = result.final_content
                    context.tools_used = list(result.tools_used)
                    context.usage = result.usage
                    context.stop_reason = result.stop_reason
                    context.error = result.error
                    context.tool_events = deepcopy(result.tool_events)
                    context.had_injections = result.had_injections
                    context.exception = None
                    if context.error is not None:
                        await hook.on_error(context)
                    await hook.after_run(context)
                    return result
            except asyncio.CancelledError as exc:
                context.stop_reason = "cancelled"
                context.error = None
                context.exception = exc
                raise
            finally:
                if has_run_hooks:
                    try:
                        if isinstance(context.exception, asyncio.CancelledError):
                            context.messages = await self._run_context_snapshot(hook, messages)
                        else:
                            context.messages = await self._run_context_snapshot(hook, messages)
                    except asyncio.CancelledError as exc:
                        context.stop_reason = "cancelled"
                        context.error = None
                        context.exception = exc
                        context.messages = await self._run_context_snapshot(hook, messages)
                        raise
        finally:
            try:
                if context.exception is None:
                    await hook.on_finally(context)
                else:
                    try:
                        await hook.on_finally(context)
                    except Exception:
                        logger.opt(exception=tool_log_content_allowed()).error(
                            "AgentHook.on_finally error after {}",
                            context.stop_reason or "run exception",
                        )
            finally:
                reset_llm_usage_source(llm_usage_source_token)

    @staticmethod
    def _initial_transcript_and_compaction(
        spec: AgentRunSpec,
    ) -> tuple[list[dict[str, Any]], ContextCompactionState]:
        """Build the initial transcript and its compaction state."""
        consolidate_history = spec.consolidate_history
        if consolidate_history is None:
            raise ValueError("consolidate_history is required")
        transcript_input = spec.transcript_input
        if transcript_input is not None:
            if spec.initial_messages is not None:
                raise ValueError("provide either transcript_input or initial_messages, not both")
            transcript_builder = spec.transcript_builder
            if transcript_builder is None:
                raise ValueError("transcript_builder is required with transcript_input")
            return ContextCompactionState.from_transcript(
                transcript_input,
                transcript_builder,
                consolidate_history,
                spec.consolidate_provider_compaction,
            )
        if spec.initial_messages is None:
            raise ValueError("initial_messages is required without transcript_input")
        messages = list(spec.initial_messages)
        return messages, ContextCompactionState.from_messages(
            messages,
            consolidate_history,
            spec.consolidate_provider_compaction,
        )

    async def _run_core(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
        compaction: ContextCompactionState,
    ) -> AgentRunResult:
        final_content: str | None = None
        tools_used: list[str] = []
        usage: LLMUsage | None = None
        round_usages: list[LLMUsage] = []
        error: str | None = None
        failure_error_kind: str | None = None
        stop_reason = "completed"
        tool_events: list[dict[str, str]] = []
        external_lookup_counts: dict[str, int] = {}
        workspace_violation_counts: dict[str, int] = {}
        empty_content_retries = 0
        # Segments from one uninterrupted length-recovery chain. Tool work or
        # injected user input starts a new logical answer and clears the chain.
        length_recovery_parts: list[str] = []
        pending_length_segment: tuple[AgentHookContext, str] | None = None
        had_injections = False
        pending_stream_content: str | None = None

        async def checkpoint_compaction(state: ProviderConversationState) -> None:
            await self._emit_checkpoint(spec, {
                "phase": "model_input",
                "iteration": iteration,
                "model": spec.runtime.model,
                "assistant_message": None,
                "completed_tool_results": [],
                "pending_tool_calls": [],
                "provider_state": state,
            }, messages)

        conversation_state = ProviderConversationStateController(
            provider=spec.runtime.provider,
            model=spec.runtime.model,
            messages=messages,
            state=spec.provider_state,
            session_id=spec.session_key,
            checkpoint_callback=checkpoint_compaction,
        )
        governance_config = ContextGovernanceConfig(
            provider=spec.runtime.provider,
            model=spec.runtime.model,
            tools=spec.tools,
            workspace=spec.workspace,
            session_key=spec.session_key,
            max_tool_result_chars=spec.max_tool_result_chars,
            context_window_tokens=spec.runtime.context_window_tokens,
            max_tokens=spec.runtime.generation.max_tokens,
        )
        request_state = ModelRequestState(
            config=governance_config,
            conversation=conversation_state,
            compaction=compaction,
            events=spec.events,
        )

        async def end_length_segment(*, interrupted: bool) -> None:
            nonlocal pending_length_segment
            if pending_length_segment is None:
                return
            segment_context, segment_content = pending_length_segment
            pending_length_segment = None
            if interrupted:
                length_recovery_parts.clear()
            else:
                messages.append(build_length_recovery_message(segment_content))
            if hook.wants_streaming():
                segment_context.stream_continues_current_message = not interrupted
                await hook.on_stream_end(segment_context, resuming=True)

        iteration = -1
        followup_requested = False
        while (
            iteration + 1 < spec.max_iterations
            or followup_requested
            or await self._continuation_prompt(spec) is not None
        ):
            iteration += 1
            # A response boundary already admitted its follow-up request. Do not
            # reread continuation state for that same decision at loop entry.
            followup_requested = False
            # A resumed iteration must not inherit a previous iteration's failure.
            stop_reason = "completed"
            error = None
            # The session inbox cuts a finite snapshot before every model call.
            # This includes follow-ups that arrived before the first request and
            # messages received while the previous request or tools were running.
            drained_before_request = await self._try_drain_injections(
                spec, messages, phase="before model call",
            )
            if drained_before_request:
                had_injections = True
            await end_length_segment(interrupted=drained_before_request)
            context = AgentHookContext(
                iteration=iteration,
                messages=messages,
                session_key=spec.session_key,
            )
            await hook.before_iteration(context)
            request_message_count = len(messages)
            request_messages = await self.prepare_prompt(
                request_state.compaction.request_messages, messages,
            )
            request_state.recent_round_usages.append(None)
            response, raw_usage = await self._request_model(
                spec,
                request_messages,
                hook,
                context,
                request_state=request_state,
                transcript=messages,
            )
            assert request_state.messages is not None
            messages_for_model = request_state.messages
            conversation_state.observe_response(response, messages)
            await self.context_governor.accept_request(
                request_state, messages_for_model,
                raw_boundary=request_message_count,
            )
            context.response = response
            context.tool_calls = list(response.tool_calls)

            original_content = response.content
            reasoning_text, cleaned_content = extract_reasoning(
                response.reasoning_content,
                response.thinking_blocks,
                response.content,
            )
            response.content = cleaned_content
            round_usages.append(raw_usage)
            context.usage = raw_usage
            usage = self._merge_usage(usage, raw_usage)
            if reasoning_text and not context.streamed_reasoning:
                await hook.emit_reasoning(reasoning_text)
                await hook.emit_reasoning_end()
                context.streamed_reasoning = True

            if response.should_execute_tools:
                context.tool_calls = list(response.tool_calls)
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                assistant_message = conversation_state.project_response_message(
                    assistant_message,
                    response,
                )
                messages.append(assistant_message)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": iteration,
                        "model": spec.runtime.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [tc.to_openai_tool_call() for tc in response.tool_calls],
                    },
                    messages,
                )

                await hook.before_execute_tools(context)

                async def settle_tool_results(completed: list[ToolCallResult]) -> None:
                    tool_events.extend(entry.event for entry in completed)
                    tools_used.extend(
                        entry.tool_call.name for entry in completed
                        if entry.event.get("status") == "ok"
                    )
                    context.tool_results = [entry.result for entry in completed]
                    context.tool_events = [entry.event for entry in completed]
                    completed_by_id = {entry.tool_call.id: entry for entry in completed}
                    interrupted = len(completed) != len(response.tool_calls)
                    completed_tool_results: list[dict[str, Any]] = []
                    for tool_call in response.tool_calls:
                        entry = completed_by_id.get(tool_call.id)
                        tool_message: dict[str, Any] = {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                        }
                        if entry is not None:
                            tool_message["content"] = self.context_governor.normalize_tool_result(
                                governance_config,
                                tool_call.id,
                                tool_call.name,
                                entry.result,
                            )
                        else:
                            # Close interrupted calls as recovery does, without
                            # inventing a result or permitting an implicit retry.
                            tool_message["content"] = (
                                "Error: Task interrupted; this tool's outcome is unknown. "
                                "Check its effects before retrying."
                            )
                            tool_message["_recovery_interrupted"] = True
                        messages.append(tool_message)
                        completed_tool_results.append(tool_message)
                    checkpoint_model_messages = (
                        await self.prepare_prompt(
                            self.context_governor.prepare_messages_for_model, governance_config,
                            messages,
                        )
                        if response.provider_state is not None and not interrupted
                        else None
                    )
                    await self._emit_checkpoint(
                        spec,
                        {
                            "phase": "tools_completed",
                            "iteration": iteration,
                            "model": spec.runtime.model,
                            "assistant_message": assistant_message,
                            "completed_tool_results": completed_tool_results,
                            "pending_tool_calls": [],
                            "provider_state": conversation_state.checkpoint(
                                messages,
                                model_messages=checkpoint_model_messages,
                            ) if not interrupted else None,
                        },
                        messages,
                    )

                await execute_tool_calls(
                    spec.tools,
                    response.tool_calls,
                    concurrent=spec.concurrent_tools,
                    external_lookup_counts=external_lookup_counts,
                    workspace_violation_counts=workspace_violation_counts,
                    hook=hook,
                    context=context,
                    model_messages=messages_for_model,
                    compacted_tool_results=request_state.compacted_tool_results,
                    on_settled=settle_tool_results,
                )
                empty_content_retries = 0
                length_recovery_parts.clear()
                await hook.after_iteration(context)
                continue

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason,
                    spec.session_key or "default",
                )

            clean = hook.finalize_content(context, response.content)
            if (
                response.finish_reason
                not in {"error", "length", "refusal", "content_filter"}
                and is_blank_text(clean)
            ):
                empty_content_retries += 1
                if empty_content_retries < _MAX_EMPTY_RETRIES:
                    logger.warning(
                        "Empty response on turn {} for {} ({}/{}); retrying",
                        iteration,
                        spec.session_key or "default",
                        empty_content_retries,
                        _MAX_EMPTY_RETRIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    iteration,
                    spec.session_key or "default",
                    empty_content_retries,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                request_state.recent_round_usages.append(None)
                response = await self._request_finalization_retry(
                    spec,
                    messages_for_model,
                    request_state=request_state,
                    transcript=messages,
                )
                retry_usage = await self._record_request_usage(spec, request_state, response)
                round_usages.append(retry_usage)
                usage = self._merge_usage(usage, retry_usage)
                raw_usage = self._merge_usage(raw_usage, retry_usage)
                context.response = response
                context.usage = raw_usage
                context.tool_calls = list(response.tool_calls)
                original_content = response.content
                clean = hook.finalize_content(context, response.content)

            if response.finish_reason == "length":
                if len(length_recovery_parts) < _MAX_LENGTH_RECOVERIES:
                    length_recovery_parts.append(
                        _restore_outer_whitespace(clean or "", original_content)
                    )
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        iteration,
                        spec.session_key or "default",
                        len(length_recovery_parts),
                        _MAX_LENGTH_RECOVERIES,
                    )
                    messages.append(conversation_state.project_response_message(
                        build_assistant_message(
                            clean,
                            reasoning_content=response.reasoning_content,
                            thinking_blocks=response.thinking_blocks,
                        ),
                        response,
                    ))
                    # The next input snapshot decides whether to continue this
                    # answer or close its stream before answering a new question.
                    pending_length_segment = (context, clean or "")
                    await hook.after_iteration(context)
                    continue

            # Some streaming providers recover with a complete response but no
            # content deltas. When an earlier length segment is already visible,
            # emit this terminal segment into the same stream; otherwise the
            # regular full response would duplicate the visible prefix.
            if (
                length_recovery_parts
                and hook.wants_streaming()
                and not context.streamed_content
                and response.finish_reason != "error"
                and not is_blank_text(clean)
            ):
                await hook.on_stream(
                    context,
                    _restore_outer_whitespace(clean or "", original_content),
                )
                context.streamed_content = True

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                assistant_message = conversation_state.project_response_message(
                    assistant_message,
                    response,
                )
                messages.append(assistant_message)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "final_response",
                        "iteration": iteration,
                        "model": spec.runtime.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [],
                        "provider_state": conversation_state.checkpoint(messages),
                    },
                    messages,
                )

            if response.finish_reason == "error":
                interrupted_parts = [
                    content
                    for part in context.streamed_content_parts
                    if (content := extract_reasoning(None, None, part)[1])
                ]
                interrupted_content = hook.finalize_content(
                    context, "\n\n".join(interrupted_parts),
                )
                if not is_blank_text(interrupted_content):
                    # This is unsynchronized output, not a completed response or
                    # provider-owned projection. Keep it in the next native delta.
                    messages.append({
                        **build_assistant_message(interrupted_content),
                        "_interrupted": True,
                    })

            # Check for mid-turn injections BEFORE signaling stream end.
            # If injections are found we keep the stream alive (resuming=True)
            # so streaming channels don't prematurely finalize the card.
            normal_response = (
                assistant_message is not None
                and response.finish_reason not in {"error", "length", "refusal", "content_filter"}
            )
            ordinary_budget_remaining = iteration + 1 < spec.max_iterations
            continuation_prompt = None
            if normal_response and not ordinary_budget_remaining:
                continuation_prompt = await self._continuation_prompt(spec)
            can_make_followup_request = ordinary_budget_remaining or continuation_prompt is not None
            should_continue = can_make_followup_request and await self._try_drain_injections(
                spec, messages, phase="after final response",
            )
            if not should_continue and normal_response:
                if ordinary_budget_remaining:
                    continuation_prompt = await self._continuation_prompt(spec)
                if continuation_prompt is not None:
                    messages.append({
                        "role": "user", "content": continuation_prompt,
                        HIDDEN_HISTORY_META: {"kind": "internal_continuation"},
                    })
                    should_continue = True
                    logger.info("Injected caller-requested continuation after final response")
                elif can_make_followup_request:
                    should_continue = await self._try_drain_injections(
                        spec, messages, phase="at terminal response", terminal=True,
                    )
            if should_continue:
                had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                followup_requested = True
                length_recovery_parts.clear()
                await hook.after_iteration(context)
                continue

            if response.finish_reason == "error":
                if LLMProvider.is_arrearage_response(response):
                    final_content = _ARREARAGE_ERROR_MESSAGE
                else:
                    final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                if can_make_followup_request and await self._try_drain_injections(
                    spec, messages, phase="after LLM error",
                ):
                    had_injections = True
                    length_recovery_parts.clear()
                    continue
                failure_error_kind = LLMProvider.public_error_kind(response)
                break
            if is_blank_text(clean):
                final_content = EMPTY_FINAL_RESPONSE_MESSAGE
                stop_reason = "empty_final_response"
                error = final_content
                self._append_final_message(messages, final_content)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                if can_make_followup_request and await self._try_drain_injections(
                    spec, messages, phase="after empty response",
                ):
                    had_injections = True
                    length_recovery_parts.clear()
                    continue
                break

            if length_recovery_parts:
                final_content = (
                    "".join(length_recovery_parts)
                    + _restore_outer_whitespace(clean or "", original_content)
                ).strip()
            else:
                final_content = clean
            context.final_content = final_content
            context.stop_reason = stop_reason
            await hook.after_iteration(context)
            break
        else:
            await end_length_segment(interrupted=False)
            stop_reason = "max_iterations"
            # Once a goal allowed tool work beyond the ordinary budget, its
            # closure deserves an answer, not a claim that work is unfinished.
            goal_overrun = iteration + 1 > spec.max_iterations
            terminal_content = None
            if spec.finalize_on_max_iterations:
                terminal_content, usage = await self._try_finalize_after_max_iterations(
                    spec,
                    hook,
                    messages,
                    usage,
                    iteration=iteration + 1,
                    request_state=request_state,
                    round_usages=round_usages,
                )
            if terminal_content is not None and goal_overrun:
                stop_reason = "completed"
                pending_stream_content = terminal_content
            if terminal_content is None:
                if goal_overrun and spec.finalize_on_max_iterations:
                    terminal_content = EMPTY_FINAL_RESPONSE_MESSAGE
                    stop_reason = "empty_final_response"
                    error = terminal_content
                    pending_stream_content = terminal_content
                else:
                    terminal_content = self._max_iterations_fallback(spec)
            if length_recovery_parts:
                terminal_tail = f"\n\n{terminal_content.lstrip()}"
                final_content = (
                    "".join(length_recovery_parts).rstrip() + terminal_tail
                ).strip()
                pending_stream_content = terminal_tail
            else:
                final_content = terminal_content
            self._append_final_message(messages, terminal_content)

        return AgentRunResult(
            final_content=final_content,
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            round_usages=round_usages,
            stop_reason=stop_reason,
            error=error,
            failure_error_kind=failure_error_kind,
            tool_events=tool_events,
            had_injections=had_injections,
            pending_stream_content=pending_stream_content,
            provider_state=conversation_state.finish(messages),
            summary_checkpoint=request_state.compaction.summary_checkpoint,
            provider_compaction_applied=request_state.provider_compaction_applied,
        )

    async def _prepare_model_request(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        request_state: ModelRequestState,
        transcript: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        messages, provider_context = await self.context_governor.prepare_request(
            request_state,
            messages,
            tool_definitions=tools,
            transcript=transcript,
        )
        generation = spec.runtime.generation
        return messages, {
            "messages": messages,
            "tools": tools,
            "model": spec.runtime.model,
            "retry_mode": spec.provider_retry_mode,
            "temperature": generation.temperature,
            "max_tokens": generation.max_tokens,
            "reasoning_effort": generation.reasoning_effort,
            "provider_context": replace(
                provider_context or ProviderCallContext(),
                response_preset=spec.runtime.model_preset or "",
            ),
        }

    async def _invoke_model(
        self, spec: AgentRunSpec, state: ModelRequestState, kwargs: dict[str, Any],
        *, record_timing: Callable[[LLMResponse], None] | None = None,
    ) -> LLMResponse:
        """Observe the logical provider invocation, after request preparation.

        Physical retries/fallbacks and provider-internal pre-request compaction
        remain inside the call. Post-return compaction summarization is outside.
        Only provider-returned usage is published here, never estimated zeros.
        Callers reserve a live round slot; recovery invocations update it.
        """
        request_id = uuid4().hex

        async def emit(phase: Literal["started", "completed", "failed", "cancelled"]) -> None:
            state.request_sequence += 1
            if not spec.events.accepts(ModelRequestEvent):
                return
            await spec.events.emit(ModelRequestEvent(
                request_id=request_id, run_id=state.request_run_id,
                sequence=state.request_sequence, phase=phase,
                usage=state.request_usage,
                round_usages=tuple(usage for usage in state.recent_round_usages if usage is not None),
                context_window_tokens=spec.runtime.context_window_tokens,
            ))

        try:
            await emit("started")
            _raise_if_cancelling()
            response = await spec.runtime.provider.chat_stream_with_retry(**kwargs)
            _raise_if_cancelling()
        except asyncio.CancelledError:
            try:
                await shield_and_drain(emit("cancelled"))
            except asyncio.CancelledError:
                pass
            raise
        except Exception:
            await emit("failed")
            raise
        if record_timing is not None:
            record_timing(response)
        if response.usage is not None:
            reported_usage = response.usage.with_timing(
                generation_ms=response.generation_ms, ttft_ms=response.ttft_ms,
            )
            state.request_usage = self._merge_usage(state.request_usage, reported_usage)
            state.recent_round_usages[-1] = self._merge_usage(
                state.recent_round_usages[-1], reported_usage,
            )
        del state.recent_round_usages[:-8]
        await emit("failed" if response.finish_reason == "error" else "completed")
        return response

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
        *,
        request_state: ModelRequestState,
        malformed_retry: bool = False,
        transcript: list[dict[str, Any]] | None,
    ) -> tuple[LLMResponse, LLMUsage]:
        _raise_if_cancelling()
        messages, kwargs = await self._prepare_model_request(
            spec,
            messages,
            tools=spec.tools.get_definitions(),
            request_state=request_state,
            transcript=transcript,
        )
        _raise_if_cancelling()
        wants_streaming = hook.wants_streaming()

        active_hosted_tools: dict[str, dict[str, Any]] = {}
        native_reasoning_open = False
        native_reasoning_close_task: asyncio.Task[None] | None = None
        request_started_at = 0.0
        first_output_at: float | None = None
        generation_started_at: float | None = None
        generation_elapsed_s = 0.0
        stream_buf = ""

        def _end_stream_segment() -> None:
            nonlocal stream_buf
            if stream_buf:
                context.streamed_content_parts.append(stream_buf)
                stream_buf = ""

        def _generation_delta(delta: str) -> None:
            nonlocal first_output_at, generation_started_at
            if not delta:
                return
            now = time.perf_counter()
            if first_output_at is None:
                first_output_at = now
            if generation_started_at is None:
                generation_started_at = now

        def _pause_generation() -> None:
            nonlocal generation_elapsed_s, generation_started_at
            if generation_started_at is None:
                return
            generation_elapsed_s += max(0.0, time.perf_counter() - generation_started_at)
            generation_started_at = None

        async def _close_native_reasoning() -> None:
            nonlocal native_reasoning_open, native_reasoning_close_task
            if native_reasoning_close_task is None:
                if not native_reasoning_open:
                    return
                native_reasoning_open = False
                native_reasoning_close_task = asyncio.create_task(
                    hook.emit_reasoning_end()
                )

            close_task = native_reasoning_close_task
            cancellation: asyncio.CancelledError | None = None
            while not close_task.done():
                try:
                    await asyncio.shield(close_task)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
            try:
                close_task.result()
            finally:
                if native_reasoning_close_task is close_task:
                    native_reasoning_close_task = None
            if cancellation is not None:
                raise cancellation

        async def _provider_tool_event(event: dict[str, Any]) -> None:
            if event.get("kind") != "hosted_tool":
                return
            await _close_native_reasoning()
            if event.get("phase") == "start":
                _end_stream_segment()
            await hook.on_provider_tool_event(context, event)
            call_id = event.get("call_id")
            if not call_id:
                return
            call_id = str(call_id)
            if event.get("phase") == "start":
                active_hosted_tools[call_id] = dict(event)
            elif event.get("phase") in {"end", "error"}:
                active_hosted_tools.pop(call_id, None)

        if wants_streaming:
            thinking_buf = ""

            async def _stream(delta: str) -> None:
                nonlocal stream_buf
                stream_buf += delta
                _generation_delta(delta)
                if delta:
                    context.streamed_content = True
                    await _close_native_reasoning()
                await hook.on_stream(context, delta)

            async def _thinking(delta: str) -> None:
                nonlocal native_reasoning_open, thinking_buf
                if not delta:
                    return
                _generation_delta(delta)
                prev_clean = strip_reasoning_tags(thinking_buf)
                thinking_buf += delta
                new_clean = strip_reasoning_tags(thinking_buf)
                incremental = new_clean[len(prev_clean):]
                if incremental:
                    context.streamed_reasoning = True
                    native_reasoning_open = True
                    await hook.emit_reasoning(incremental)

            async def _stream_recover() -> None:
                _pause_generation()
                await _close_native_reasoning()
                _end_stream_segment()
                await hook.on_stream_end(context, resuming=True)

            kwargs.update(
                on_content_delta=_stream,
                on_thinking_delta=_thinking,
                on_tool_call_delta=_provider_tool_event,
                on_stream_recover=_stream_recover,
            )
        def record_timing(response: LLMResponse) -> None:
            _pause_generation()
            if first_output_at is not None:
                response.ttft_ms = max(0, round((first_output_at - request_started_at) * 1000))
            if generation_elapsed_s > 0:
                response.generation_ms = max(1, round(generation_elapsed_s * 1000))

        coro = self._invoke_model(spec, request_state, kwargs, record_timing=record_timing)

        # Providers bound the wait for each stream event, including reasoning and tool deltas.
        request_started_at = time.perf_counter()
        try:
            response = await coro
            _raise_if_cancelling()
        except asyncio.CancelledError:
            _pause_generation()
            await _close_native_reasoning()
            raise
        _pause_generation()
        await _close_native_reasoning()
        _end_stream_segment()
        await self.context_governor.summarize_provider_compaction(
            request_state,
            response,
            current_request_boundary=(len(transcript) if transcript is not None else None),
        )
        request_state.provider_compaction_applied |= response.provider_compaction_applied
        round_usage = await self._record_request_usage(spec, request_state, response)
        # chat_stream_with_retry may recover internally, so only fail unfinished
        # hosted calls after the provider returns its final error response.
        if response.finish_reason == "error":
            for event in list(active_hosted_tools.values()):
                await _provider_tool_event({
                    **event,
                    "phase": "error",
                    "result": None,
                    "error": response.content
                    or "Model request failed before the provider-hosted tool completed.",
                })
        dropped, all_dropped, original_finish_reason = (
            self._drop_malformed_tool_calls(response)
        )
        if (
            all_dropped
            and original_finish_reason in ("tool_calls", "function_call")
            and not malformed_retry
        ):
            logger.warning(
                "Retrying LLM request after all {} malformed tool call(s) were dropped",
                dropped,
            )
            retry_messages = self._malformed_tool_call_retry_messages(
                messages, response.content,
            )
            retry_response, retry_usage = await self._request_model(
                spec, retry_messages, hook, context,
                request_state=request_state,
                malformed_retry=True,
                transcript=None,
            )
            return retry_response, round_usage + retry_usage
        if (
            all_dropped
            and original_finish_reason in ("tool_calls", "function_call")
            and malformed_retry
        ):
            logger.warning(
                "Malformed tool calls persisted after retry; falling back to no-tools request",
            )
            fallback_messages = self._malformed_tool_call_retry_messages(
                messages, response.content,
            )
            fallback_response = await self._request_no_tools(
                spec,
                fallback_messages,
                request_state=request_state,
            )
            fallback_usage = await self._record_request_usage(
                spec,
                request_state,
                fallback_response,
            )
            return fallback_response, round_usage + fallback_usage
        return response, round_usage

    @staticmethod
    def _drop_malformed_tool_calls(
        response: LLMResponse,
    ) -> tuple[int, bool, str | None]:
        """Strip tool calls whose name is missing/non-string from the response.

        Returns (dropped_count, all_dropped, original_finish_reason).

        A degenerate call (name=None or "") cannot be executed, and if it were
        persisted into the assistant message it would be replayed on every
        subsequent turn, causing upstream validation errors
        (``tool_use.name: Input should be a valid string``) that permanently
        wedge the session. Dropping it here keeps it out of execution, the
        assistant message, and the saved history in one place.
        """
        calls = getattr(response, "tool_calls", None)
        if not calls:
            return (0, False, getattr(response, "finish_reason", None))
        valid = [tc for tc in calls if tc.has_valid_name()]
        if len(valid) == len(calls):
            return (0, False, getattr(response, "finish_reason", None))
        dropped = len(calls) - len(valid)
        original_finish_reason = getattr(response, "finish_reason", None)
        logger.warning(
            "Dropped {} malformed tool call(s) with missing/non-string name "
            "from LLM response (finish_reason={!r})",
            dropped,
            original_finish_reason,
        )
        response.tool_calls = valid
        # The opaque candidate still contains every raw function_call item.
        # Advancing it after dropping even one call would replay an unmatched
        # call without a corresponding tool output on the next request.
        response.provider_state = None
        if not valid and original_finish_reason in ("tool_calls", "function_call"):
            response.finish_reason = "stop"
        return (dropped, not valid, original_finish_reason)

    @staticmethod
    def _malformed_tool_call_retry_messages(
        messages: list[dict[str, Any]],
        assistant_text: str | None,
    ) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        note = (
            "The previous model response attempted to call tools, but every tool call "
            "was malformed: the tool_use blocks had missing or non-string tool names. "
            "Do not answer with a promise to use tools. Either call the required tools again "
            "using valid tool names from the provided tool list and JSON object inputs, or give "
            "a final answer only if no tool is required."
        )
        if assistant_text:
            note += (
                f"\n\nPrevious assistant text before the malformed calls:\n"
                f"{assistant_text}"
            )
        retry_messages.append({"role": "user", "content": note})
        return retry_messages

    async def _request_finalization_retry(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        request_state: ModelRequestState,
        transcript: list[dict[str, Any]],
    ) -> LLMResponse:
        retry_messages = self._finalization_retry_messages(messages)
        response = await self._request_no_tools(
            spec,
            retry_messages,
            request_state=request_state,
            transcript=transcript,
        )
        request_state.conversation.observe_response(
            response,
            transcript,
            adopt_candidate_state=False,
        )
        return response

    @staticmethod
    def _finalization_retry_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        retry_messages.append(build_finalization_retry_message())
        return retry_messages

    async def _try_finalize_after_max_iterations(
        self,
        spec: AgentRunSpec,
        hook: AgentHook,
        messages: list[dict[str, Any]],
        usage: LLMUsage | None,
        *,
        iteration: int,
        request_state: ModelRequestState,
        round_usages: list[LLMUsage],
    ) -> tuple[str | None, LLMUsage | None]:
        compaction = request_state.compaction
        request_messages = await self.prepare_prompt(compaction.request_messages, messages)
        retry_messages = (
            self._finalization_retry_messages(request_messages)
            if iteration > spec.max_iterations
            else self._budget_exhausted_finalization_messages(request_messages)
        )
        request_state.recent_round_usages.append(None)
        try:
            response = await self._request_no_tools(
                spec,
                retry_messages,
                request_state=request_state,
                transcript=messages,
            )
        except ProviderStateCommitError:
            raise
        except Exception:
            logger.opt(exception=tool_log_content_allowed()).error(
                "Budget-exhausted finalization failed for {}; using fallback",
                spec.session_key or "default",
            )
            return None, usage

        raw_usage = await self._record_request_usage(spec, request_state, response)
        round_usages.append(raw_usage)
        usage = self._merge_usage(usage, raw_usage)
        if response.finish_reason == "error" or response.has_tool_calls:
            logger.warning(
                "Budget-exhausted finalization returned finish_reason='{}' "
                "with {} tool call(s) for {}; using fallback",
                response.finish_reason,
                len(response.tool_calls),
                spec.session_key or "default",
            )
            return None, usage

        context = AgentHookContext(
            iteration=iteration,
            messages=messages,
            response=response,
            usage=raw_usage,
            session_key=spec.session_key,
        )
        clean = hook.finalize_content(context, response.content)
        if is_blank_text(clean):
            return None, usage
        return clean, usage

    async def _request_no_tools(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        request_state: ModelRequestState,
        transcript: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        _raise_if_cancelling()
        _, kwargs = await self._prepare_model_request(
            spec,
            messages,
            tools=None,
            request_state=request_state,
            transcript=transcript,
        )
        _raise_if_cancelling()
        response = await self._invoke_model(spec, request_state, kwargs)
        _raise_if_cancelling()
        await self.context_governor.summarize_provider_compaction(
            request_state,
            response,
            current_request_boundary=(len(transcript) if transcript is not None else None),
        )
        _raise_if_cancelling()
        request_state.provider_compaction_applied |= response.provider_compaction_applied
        return response

    @staticmethod
    def _budget_exhausted_finalization_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        retry_messages = list(messages)
        retry_messages.append(build_budget_exhausted_finalization_message())
        return retry_messages

    @staticmethod
    def _max_iterations_fallback(spec: AgentRunSpec) -> str:
        if spec.max_iterations_message:
            return spec.max_iterations_message.format(
                max_iterations=spec.max_iterations,
            )
        return render_template(
            "agent/max_iterations_message.md",
            strip=True,
            max_iterations=spec.max_iterations,
        )

    def _usage_or_estimate(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        response: LLMResponse,
        *,
        tool_definitions: list[dict[str, Any]] | None,
    ) -> LLMUsage:
        usage = response.usage
        if response.finish_reason == "error":
            if usage is None or usage.total_tokens == 0:
                usage = LLMUsage.empty_request()
        elif usage is None or usage.total_tokens == 0:
            usage = self._estimate_response_usage(
                spec,
                messages,
                response,
                tool_definitions=tool_definitions,
            )
        return usage.with_timing(
            generation_ms=response.generation_ms,
            ttft_ms=response.ttft_ms,
        )

    async def _record_request_usage(
        self,
        spec: AgentRunSpec,
        state: ModelRequestState,
        response: LLMResponse,
    ) -> LLMUsage:
        assert state.messages is not None
        if response.finish_reason != "error" and (
            response.usage is None or response.usage.total_tokens == 0
        ):
            usage = await self.prepare_prompt(
                self._usage_or_estimate, spec, state.messages, response,
                tool_definitions=state.tool_definitions,
            )
        else:
            usage = self._usage_or_estimate(
                spec, state.messages, response, tool_definitions=state.tool_definitions,
            )
        # A rewritten input or another model's count cannot bound the next request.
        state.usage = None if response.provider_compaction_applied or response.is_fallback else usage
        return usage

    def _estimate_response_usage(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        response: LLMResponse,
        *,
        tool_definitions: list[dict[str, Any]] | None,
    ) -> LLMUsage:
        prompt_tokens, _ = estimate_prompt_tokens_chain(
            spec.runtime.provider,
            spec.runtime.model,
            messages,
            tool_definitions,
        )
        assistant_message = build_assistant_message(
            response.content or "",
            tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
            reasoning_content=response.reasoning_content,
            thinking_blocks=response.thinking_blocks,
        )
        completion_tokens = estimate_message_tokens(assistant_message)
        return LLMUsage.estimated(
            input_tokens=max(0, prompt_tokens),
            output_tokens=max(0, completion_tokens),
        )

    @staticmethod
    def _merge_usage(
        left: LLMUsage | None,
        right: LLMUsage | None,
    ) -> LLMUsage | None:
        if left is None:
            return right
        if right is None:
            return left
        return left + right

    async def _emit_checkpoint(
        self,
        spec: AgentRunSpec,
        payload: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        callback = spec.checkpoint_callback
        if callback is not None:
            if spec.transcript_input is not None:
                start = spec.transcript_input.message_count
            else:
                assert spec.initial_messages is not None
                start = len(spec.initial_messages)
            # Replacing the checkpoint must retain every earlier round of this
            # run, including follow-ups consumed since the accepted input.
            batch_size = (
                0 if payload["phase"] == "model_input"
                else 1 + len(payload["completed_tool_results"])
            )
            await callback({
                **payload,
                "preceding_messages": messages[start:len(messages) - batch_size],
            })

    @staticmethod
    def _append_final_message(messages: list[dict[str, Any]], content: str | None) -> None:
        if not content:
            return
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            if messages[-1].get("content") == content:
                return
            messages[-1] = build_assistant_message(content)
            return
        messages.append(build_assistant_message(content))

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

```

## nanobot/agent/tools/filesystem.py

Source: https://github.com/HKUDS/nanobot/blob/382f1b12e8d9b2d4dbe2d2803e5f5b9a6e262972/nanobot/agent/tools/filesystem.py

SHA-256: `61670d0b3b66b238be00a8a609d3d742b6b888445155e48241d3ca11b61cdee0`

```
# pyright: reportPrivateUsage=false, reportUnusedFunction=false

import difflib
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.file_state import FileStates, current_file_states
from nanobot.agent.tools.filesystem_config import FileToolsConfig
from nanobot.agent.tools.path_utils import resolve_workspace_path
from nanobot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.security.workspace_access import current_tool_workspace
from nanobot.utils.file_edit_events import FileDiff, FileEditResult, display_file_edit_path
from nanobot.utils.helpers import build_image_content_blocks, detect_image_mime


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    config_key = "file"

    @classmethod
    def config_cls(cls):
        return FileToolsConfig

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.file.enable

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
        extra_read_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_files: list[Path] | None = None,
        file_states: FileStates | None = None,
        restrict_to_workspace: bool | None = None,
        sandbox_restricts_workspace: bool = False,
        extra_read_allowed_files: list[Path] | None = None,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        # Legacy alias: extra_allowed_dirs is read-only. Write-capable tools
        # must opt in via extra_write_allowed_dirs.
        self._extra_read_allowed_dirs = [
            *(extra_allowed_dirs or []),
            *(extra_read_allowed_dirs or []),
        ]
        self._extra_read_allowed_files = list(extra_read_allowed_files or [])
        self._extra_write_allowed_dirs = list(extra_write_allowed_dirs or [])
        self._extra_write_allowed_files = list(extra_write_allowed_files or [])
        self._restrict_to_workspace = (
            bool(restrict_to_workspace)
            if restrict_to_workspace is not None
            else allowed_dir is not None
        )
        self._sandbox_restricts_workspace = sandbox_restricts_workspace
        # Explicit state is used by isolated runners like Dream/subagents.
        # Main AgentLoop tools leave this unset and resolve state from the
        # current async task, which keeps shared tool instances session-safe.
        self._explicit_file_states = file_states
        self._fallback_file_states = FileStates()

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        from nanobot.agent.skill_metadata import BUILTIN_SKILLS_DIR

        agent_workspace = Path(ctx.workspace)
        resolved_agent_workspace = agent_workspace.expanduser().resolve(strict=False)
        restrict = (
            ctx.config.restrict_to_workspace
            or ctx.config.exec.sandbox
        )
        sandbox_restricts = bool(ctx.config.exec.sandbox)
        allowed_dir = agent_workspace if restrict else None
        # Agent-owned skills stay available from project scopes. History is a narrower
        # capability: expose only the append-only log, not the surrounding memory directory.
        return cls(
            workspace=agent_workspace,
            allowed_dir=allowed_dir,
            extra_read_allowed_dirs=[BUILTIN_SKILLS_DIR, resolved_agent_workspace / "skills"],
            extra_read_allowed_files=[resolved_agent_workspace / "memory" / "history.jsonl"],
            file_states=ctx.file_state_store,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
            sandbox_restricts_workspace=sandbox_restricts,
        )

    @property
    def _file_states(self) -> FileStates:
        if self._explicit_file_states is not None:
            return self._explicit_file_states
        return current_file_states(self._fallback_file_states)

    def _effective_allowed_root(self, access_allowed_root: Path | None) -> Path | None:
        if self._allowed_dir is None or self._workspace is None:
            return access_allowed_root
        try:
            allowed_dir = Path(self._allowed_dir).expanduser().resolve(strict=False)
            workspace = Path(self._workspace).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return access_allowed_root if access_allowed_root is not None else self._allowed_dir
        if allowed_dir == workspace:
            return access_allowed_root
        return allowed_dir

    def _resolve_with_extra(
        self,
        path: str,
        extra_allowed_dirs: list[Path] | None,
        extra_allowed_files: list[Path] | None,
        *,
        include_media_dir: bool,
        extra_files_require_allowed_root: bool = False,
    ) -> Path:
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
            sandbox_restricts_workspace=self._sandbox_restricts_workspace,
        )
        allowed_root = self._effective_allowed_root(access.allowed_root)
        if extra_files_require_allowed_root and allowed_root is None:
            extra_allowed_files = None
        return resolve_workspace_path(
            path,
            access.project_path,
            allowed_root,
            extra_allowed_dirs,
            extra_allowed_files,
            include_media_dir=include_media_dir,
        )

    def _resolve_read(self, path: str) -> Path:
        plugin_skill_dirs: list[Path] = []
        if self._workspace is not None:
            from nanobot.agent.plugins import enabled_agent_plugin_skill_dirs

            try:
                access = current_tool_workspace(
                    self._workspace,
                    restrict_to_workspace=self._restrict_to_workspace,
                    sandbox_restricts_workspace=self._sandbox_restricts_workspace,
                )
                if self._effective_allowed_root(access.allowed_root) is not None:
                    candidate = Path(path).expanduser()
                    if not candidate.is_absolute() and access.project_path is not None:
                        candidate = access.project_path / candidate
                    plugin_skill_dirs = list(
                        enabled_agent_plugin_skill_dirs(
                            Path(self._workspace),
                            requested_path=candidate.resolve(strict=False),
                        )
                    )
            except (OSError, RuntimeError):
                pass
        return self._resolve_with_extra(
            path,
            [*self._extra_read_allowed_dirs, *plugin_skill_dirs],
            self._extra_read_allowed_files,
            include_media_dir=True,
            extra_files_require_allowed_root=True,
        )

    def _resolve_write(self, path: str) -> Path:
        return self._resolve_with_extra(
            path,
            self._extra_write_allowed_dirs,
            self._extra_write_allowed_files,
            include_media_dir=False,
        )

    def _resolve(self, path: str) -> Path:
        return self._resolve_read(path)

    def _display_workspace(self) -> Path | None:
        return current_tool_workspace(self._workspace).project_path


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(path: str | Path) -> bool:
    """Check if path is a blocked device that could hang or produce infinite output."""
    import re
    raw = str(path)

    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(r"/proc/self/fd/[012]$", resolved):
        return True

    if resolved.startswith("/dev/"):
        return True
    return False


def _builtin_skill_read_path(path: str) -> Path | None:
    from nanobot.agent.skill_metadata import BUILTIN_SKILLS_DIR

    requested = Path(path)
    if requested.is_absolute():
        return None
    parts = requested.parts
    if len(parts) < 2 or parts[0] != "skills":
        return None
    root = BUILTIN_SKILLS_DIR.resolve()
    candidate = (root / Path(*parts[1:])).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            description="1-based text or extracted-document line (default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            description="Maximum lines to return (default 2000)",
            minimum=1,
        ),
        pages=StringSchema("PDF page number or range, e.g. '7' or '1-5' (max 20 pages)"),
        force=BooleanSchema(
            description="Return an unchanged range again",
            default=False,
        ),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""
    _scopes = {"core", "subagent", "memory"}

    _MAX_CHARS = 128_000
    _MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read text, images, PDFs, and Office documents by path. "
            "Text is line-numbered; use offset/limit or pages for targeted ranges."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        path: str | None = None,
        offset: int = 1,
        limit: int | None = None,
        pages: str | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            if not path:
                return ToolResult.error("Error reading file: Unknown path")

            if _is_blocked_device(path):
                return ToolResult.error(f"Error: Reading {path} is blocked (device path that could hang or produce infinite output).")

            fp = self._resolve_read(path)
            if not fp.exists():
                fp = _builtin_skill_read_path(path) or fp
            if _is_blocked_device(fp):
                return ToolResult.error(f"Error: Reading {fp} is blocked (device path that could hang or produce infinite output).")
            if not fp.exists():
                return ToolResult.error(f"Error: File not found: {path}")
            if not fp.is_file():
                return ToolResult.error(f"Error: Not a file: {path}")

            file_size = fp.stat().st_size
            if file_size > self._MAX_FILE_SIZE_BYTES:
                size_mib = file_size / (1024 * 1024)
                max_mib = self._MAX_FILE_SIZE_BYTES // (1024 * 1024)
                return ToolResult.error(
                    f"Error: File too large to read ({size_mib:.1f} MiB). "
                    f"Maximum is {max_mib} MiB."
                )

            if fp.suffix.lower() == ".pdf":
                return self._read_pdf(fp, pages)

            if fp.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                return self._read_office_doc(fp, offset, limit)

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            content_hash = hashlib.sha256(raw).hexdigest()
            if not force and self._file_states.is_unchanged(
                fp, offset=offset, limit=limit, content_hash=content_hash,
            ):
                return f"[File unchanged since last read: {path}]"
            from nanobot.utils.document import _decode_bom_text

            text_content = _decode_bom_text(raw)
            if text_content is None:
                try:
                    text_content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # Match the former eager extractor for known text formats while
                    # keeping arbitrary binary files on the guarded error path.
                    from nanobot.utils.document import _is_text_extension

                    if _is_text_extension(fp.suffix.lower()):
                        text_content = raw.decode("latin-1")
                    else:
                        mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
                        if mime and mime.startswith("image/"):
                            return build_image_content_blocks(
                                raw,
                                mime,
                                str(fp),
                                f"(Image file: {path})",
                            )
                        return ToolResult.error(
                            f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). "
                            "Only supported text files and images can be read."
                        )

            if not text_content:
                return f"(Empty file: {path})"

            # Normalize CRLF -> LF before line-splitting. Primarily a Windows
            # concern (git checkouts with autocrlf, editors saving CRLF) but
            # applied on all platforms so downstream StrReplace/Grep behavior
            # is consistent regardless of where the file was written.
            text_content = text_content.replace("\r\n", "\n")

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return ToolResult.error(f"Error: offset {offset} is beyond end of file ({total} lines)")

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)
            line_truncated = False

            if len(result) > self._MAX_CHARS:
                trimmed: list[str] = []
                chars = 0
                for line in numbered:
                    extra = len(line) + (1 if trimmed else 0)
                    if chars + extra > self._MAX_CHARS:
                        if not trimmed:
                            trimmed.append(line[: self._MAX_CHARS])
                            line_truncated = True
                        break
                    trimmed.append(line)
                    chars += extra
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if line_truncated:
                result += (
                    f"\n\n(Line {offset} truncated; its remaining characters are not shown. "
                    "Use exec with a targeted command to inspect the omitted content.)"
                )
            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            self._file_states.record_read(
                fp, offset=offset, limit=limit, content_hash=content_hash, result=result,
            )
            return result
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error reading file: {e}")

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        from nanobot.utils.document import PdfPageRangeError, PdfSafetyError, extract_pdf_pages

        try:
            extraction = extract_pdf_pages(
                fp,
                pages=pages,
                max_pages=self._MAX_PDF_PAGES,
                max_chars=self._MAX_CHARS,
            )
        except PdfPageRangeError as e:
            return ToolResult.error(f"Error: Invalid page range '{pages}': {e!s}.")
        except PdfSafetyError as e:
            return ToolResult.error(f"Error reading PDF: {e}")
        except Exception as e:
            return ToolResult.error(f"Error reading PDF: {e}")

        if not extraction.text:
            return f"(PDF has no extractable text: {fp})"

        result = extraction.text
        if extraction.truncated:
            result += (
                f"\n\n(Page {extraction.end_page + 1} was truncated; "
                "the remaining text on this page is not shown.)"
            )
        if extraction.end_page < extraction.total_pages - 1:
            next_start = extraction.end_page + 2
            next_end = min(extraction.end_page + 1 + self._MAX_PDF_PAGES, extraction.total_pages)
            result += (
                f"\n\n(Showing pages {extraction.start_page + 1}-{extraction.end_page + 1} "
                f"of {extraction.total_pages}. Use pages='{next_start}-{next_end}' to continue.)"
            )
        return result

    def _read_office_doc(
        self,
        fp: Path,
        offset: int,
        limit: int | None,
    ) -> str:
        from nanobot.utils.document import open_document_line_source

        offset = max(1, offset)
        requested_limit = limit or self._DEFAULT_LIMIT
        source_iterator = None
        try:
            source = open_document_line_source(fp)
            if source is None:
                return ToolResult.error(f"Error: Unsupported file format: {fp.suffix}")
            source_iterator = source.lines
            numbered: list[str] = []
            output_chars = 0
            total_seen = 0
            end = offset - 1
            has_more = False
            line_was_clipped = False

            for line in source_iterator:
                total_seen = line.extracted_line
                if line.extracted_line < offset:
                    continue
                if len(numbered) >= requested_limit:
                    has_more = True
                    break

                rendered = f"{line.extracted_line}| {line.text}"
                extra = 1 if numbered else 0
                if output_chars + extra + len(rendered) > self._MAX_CHARS:
                    if numbered:
                        has_more = True
                        break
                    prefix = f"{line.extracted_line}| "
                    available = max(0, self._MAX_CHARS - len(prefix) - 3)
                    rendered = f"{prefix}{line.text[:available]}..."
                    line_was_clipped = True
                    has_more = True
                numbered.append(rendered)
                output_chars += extra + len(rendered)
                end = line.extracted_line
                if line_was_clipped:
                    break

            if not numbered:
                if total_seen == 0:
                    return (
                        f"({fp.suffix.upper().lstrip('.')} has no extractable text: {fp})"
                    )
                return ToolResult.error(
                    f"Error: offset {offset} is beyond end of extracted document "
                    f"({total_seen} lines)"
                )

            output = "\n".join(numbered)
            if has_more:
                if line_was_clipped:
                    output += (
                        "\n\n(Document text truncated at ~128K chars; line clipped. "
                        f"Use offset={end + 1} to continue.)"
                    )
                else:
                    output += (
                        f"\n\n(Showing extracted lines {offset}-{end}. "
                        f"Use offset={end + 1} to continue.)"
                    )
            else:
                output += f"\n\n(End of document — {total_seen} extracted lines total)"
            return output
        except Exception as e:
            return ToolResult.error(f"Error reading {fp.suffix.upper()} file: {e!s}")
        finally:
            close = getattr(source_iterator, "close", None)
            if close is not None:
                close()


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to write to"),
        content=StringSchema("The content to write"),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """Write content to a file."""
    _scopes = {"core", "subagent", "memory"}

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a file or replace an entire file. "
            "Overwrites existing files and creates parent directories. "
            "Prefer apply_patch for partial edits."
        )

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve_write(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8", newline="")
            self._file_states.record_write(fp)
            return f"Successfully wrote {len(content)} characters to {fp}"
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error writing file: {e}")


_QUOTE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "'": "'", '"': '"',
})


def _normalize_quotes(s: str) -> str:
    return s.translate(_QUOTE_TABLE)


def _curly_double_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            parts.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            parts.append(ch)
    return "".join(parts)


def _curly_single_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for i, ch in enumerate(text):
        if ch != "'":
            parts.append(ch)
            continue
        prev_ch = text[i - 1] if i > 0 else ""
        next_ch = text[i + 1] if i + 1 < len(text) else ""
        if prev_ch.isalnum() and next_ch.isalnum():
            parts.append("\u2019")
            continue
        parts.append("\u2018" if opening else "\u2019")
        opening = not opening
    return "".join(parts)


def _preserve_quote_style(old_text: str, actual_text: str, new_text: str) -> str:
    if _normalize_quotes(old_text.strip()) != _normalize_quotes(actual_text.strip()) or old_text == actual_text:
        return new_text

    styled = new_text
    if any(ch in actual_text for ch in ("\u201c", "\u201d")) and '"' in styled:
        styled = _curly_double_quotes(styled)
    if any(ch in actual_text for ch in ("\u2018", "\u2019")) and "'" in styled:
        styled = _curly_single_quotes(styled)
    return styled


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent_like_match(old_text: str, actual_text: str, new_text: str) -> str:
    # A terminal newline does not add a logical line, even at an unterminated EOF.
    old_lines = old_text.removesuffix("\n").split("\n")
    actual_lines = actual_text.removesuffix("\n").split("\n")
    if len(old_lines) != len(actual_lines):
        return new_text

    comparable = [
        (old_line, actual_line)
        for old_line, actual_line in zip(old_lines, actual_lines)
        if old_line.strip() and actual_line.strip()
    ]
    if not comparable or any(
        _normalize_quotes(old_line.strip()) != _normalize_quotes(actual_line.strip())
        for old_line, actual_line in comparable
    ):
        return new_text

    old_ws = _leading_ws(comparable[0][0])
    actual_ws = _leading_ws(comparable[0][1])
    if actual_ws == old_ws:
        return new_text

    if old_ws:
        if not actual_ws.startswith(old_ws):
            return new_text
        delta = actual_ws[len(old_ws):]
    else:
        delta = actual_ws

    if not delta:
        return new_text

    return "\n".join((delta + line) if line else line for line in new_text.split("\n"))


@dataclass(slots=True)
class _MatchSpan:
    start: int
    end: int
    text: str
    line: int


def _match_end_line(match: _MatchSpan) -> int:
    comparable = match.text[:-1] if match.text.endswith("\n") else match.text
    return match.line + comparable.count("\n")


def _match_covers_line(match: _MatchSpan, line: int) -> bool:
    return match.line <= line <= _match_end_line(match)


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = content.find(old_text, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(old_text))
    return matches


def _find_trim_matches(content: str, old_text: str, *, normalize_quotes: bool = False) -> list[_MatchSpan]:
    old_lines = old_text.splitlines()
    if not old_lines:
        return []

    content_lines = content.splitlines()
    content_lines_keepends = content.splitlines(keepends=True)
    if len(content_lines) < len(old_lines):
        return []

    offsets: list[int] = []
    pos = 0
    for line in content_lines_keepends:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)

    if normalize_quotes:
        stripped_old = [_normalize_quotes(line.strip()) for line in old_lines]
    else:
        stripped_old = [line.strip() for line in old_lines]

    matches: list[_MatchSpan] = []
    window_size = len(stripped_old)
    for i in range(len(content_lines) - window_size + 1):
        window = content_lines[i : i + window_size]
        if normalize_quotes:
            comparable = [_normalize_quotes(line.strip()) for line in window]
        else:
            comparable = [line.strip() for line in window]
        if comparable != stripped_old:
            continue

        start = offsets[i]
        end = offsets[i + window_size]
        # Include the line terminator only when the requested match includes it.
        if (
            not old_text.endswith("\n")
            and content_lines_keepends[i + window_size - 1].endswith("\n")
        ):
            end -= 1
        matches.append(
            _MatchSpan(
                start=start,
                end=end,
                text=content[start:end],
                line=i + 1,
            )
        )
    return matches


def _find_quote_matches(content: str, old_text: str) -> list[_MatchSpan]:
    norm_content = _normalize_quotes(content)
    norm_old = _normalize_quotes(old_text)
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = norm_content.find(norm_old, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(norm_old))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    for matcher in (
        lambda: _find_exact_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text, normalize_quotes=True),
        lambda: _find_quote_matches(content, old_text),
    ):
        matches = matcher()
        if matches:
            return matches
    return []


def _collapse_internal_whitespace(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _diagnose_near_match(old_text: str, actual_text: str) -> list[str]:
    hints: list[str] = []

    if old_text.lower() == actual_text.lower() and old_text != actual_text:
        hints.append("letter case differs")
    if _collapse_internal_whitespace(old_text) == _collapse_internal_whitespace(actual_text) and old_text != actual_text:
        hints.append("whitespace differs")
    if old_text.rstrip("\n") == actual_text.rstrip("\n") and old_text != actual_text:
        hints.append("trailing newline differs")
    if _normalize_quotes(old_text) == _normalize_quotes(actual_text) and old_text != actual_text:
        hints.append("quote style differs")

    return hints


def _best_window(old_text: str, content: str) -> tuple[float, int, list[str], list[str]]:
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))

    best_ratio, best_start = -1.0, 0
    best_window_lines: list[str] = []

    for i in range(max(1, len(lines) - window + 1)):
        current = lines[i : i + window]
        ratio = difflib.SequenceMatcher(None, old_lines, current).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
            best_window_lines = current

    actual_text = "".join(best_window_lines).replace("\r\n", "\n").rstrip("\n")
    hints = _diagnose_near_match(old_text.replace("\r\n", "\n").rstrip("\n"), actual_text)
    return best_ratio, best_start, best_window_lines, hints


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace; copy it from read_file."),
        new_text=StringSchema(
            "The replacement text; must differ from old_text for an existing file."
        ),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        occurrence=IntegerSchema(
            description="Optional 1-based occurrence to replace when old_text appears multiple times.",
            minimum=1,
            nullable=True,
        ),
        line_hint=IntegerSchema(
            description=(
                "Optional exact 1-based target line copied from read_file. "
                "The selected old_text match must cover this line."
            ),
            minimum=1,
            nullable=True,
        ),
        expected_replacements=IntegerSchema(
            description="Optional guard for the number of replacements that must be made.",
            minimum=1,
            nullable=True,
        ),
        required=["path", "old_text", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""
    _scopes = {"core", "subagent", "memory"}

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Perform a small, exact replacement in one file. "
            "Prefer apply_patch for multi-file, structural, or generated edits. "
            "occurrence, line_hint, and replace_all=true are mutually exclusive."
        )

    @staticmethod
    def _strip_trailing_ws(text: str, *, preserve_last_line: bool = False) -> str:
        """Strip line-ending whitespace, except a final fragment that continues inline."""
        lines = text.split("\n")
        return "\n".join(
            line if preserve_last_line and i == len(lines) - 1 else line.rstrip()
            for i, line in enumerate(lines)
        )

    def _format_summary(
        self, resolved_path: Path, before: str, after: str, *,
        created: bool = False,
    ) -> FileEditResult:
        diff = FileDiff.from_text(before, after)
        added, deleted = diff.added, diff.deleted
        action = "add" if created else "update"
        stats = f" (+{added}/-{deleted})" if added or deleted else ""
        path = display_file_edit_path(resolved_path, self._display_workspace())
        text = f"Patch applied:\n- {action} {path}{stats}"
        return FileEditResult(text, {resolved_path: diff})

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, occurrence: int | None = None,
        line_hint: int | None = None, expected_replacements: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")
            if occurrence is not None and occurrence < 1:
                return ToolResult.error("Error: occurrence must be >= 1.")
            if line_hint is not None and line_hint < 1:
                return ToolResult.error("Error: line_hint must be >= 1.")
            if expected_replacements is not None and expected_replacements < 1:
                return ToolResult.error("Error: expected_replacements must be >= 1.")

            fp = self._resolve_write(path)
            file_exists = fp.exists()
            if file_exists and old_text == new_text:
                return ToolResult.error("Error: new_text must be different from old_text.")

            if not file_exists:
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8", newline="")
                    self._file_states.record_write(fp)
                    return self._format_summary(fp, "", fp.read_bytes().decode("utf-8"), created=True)
                return self._file_not_found_msg(path, fp)

            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return ToolResult.error(f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB.")

            if old_text == "":
                raw = fp.read_bytes()
                content = raw.decode("utf-8")
                if content.strip():
                    return ToolResult.error(f"Error: Cannot create file — {path} already exists and is not empty.")
                fp.write_text(new_text, encoding="utf-8", newline="")
                self._file_states.record_write(fp)
                return self._format_summary(fp, content, fp.read_bytes().decode("utf-8"))

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if replace_all and occurrence is not None:
                return ToolResult.error("Error: occurrence cannot be used with replace_all=true.")
            if replace_all and line_hint is not None:
                return ToolResult.error("Error: line_hint cannot be used with replace_all=true.")
            if occurrence is not None and line_hint is not None:
                return ToolResult.error("Error: line_hint cannot be used with occurrence.")
            if occurrence is not None and occurrence > count:
                return ToolResult.error(
                    f"Error: occurrence {occurrence} is out of range; "
                    f"old_text appears {count} time(s)."
                )
            if count > 1 and not replace_all and occurrence is None and line_hint is None:
                line_numbers = [match.line for match in matches]
                preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                if len(line_numbers) > 3:
                    preview += ", ..."
                location_hint = f" at {preview}" if preview else ""
                return (
                    f"Warning: old_text appears {count} times{location_hint}. "
                    "Provide more context, set occurrence to choose one match, "
                    "or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")

            if replace_all:
                selected = matches
            elif occurrence is not None:
                selected = [matches[occurrence - 1]]
            elif line_hint is not None:
                candidates = [match for match in matches if _match_covers_line(match, line_hint)]
                if not candidates:
                    locations = ", ".join(f"line {match.line}" for match in matches[:3])
                    if len(matches) > 3:
                        locations += ", ..."
                    return ToolResult.error(
                        f"Error: line_hint {line_hint} does not match the old_text location. "
                        f"old_text appears at {locations}. Re-read the intended region and "
                        "copy old_text that covers the target line."
                    )
                if len(candidates) > 1:
                    return ToolResult.error(
                        f"Error: line_hint {line_hint} is ambiguous; "
                        f"old_text appears {len(candidates)} times on that line."
                    )
                selected = candidates
            else:
                selected = [matches[0]]
            if expected_replacements is not None and len(selected) != expected_replacements:
                return ToolResult.error(
                    f"Error: expected {expected_replacements} replacements but "
                    f"would make {len(selected)}."
                )
            new_content = content
            for match in reversed(selected):
                replacement = norm_new
                # Preserve separator whitespace when the remaining line has content.
                # Markdown keeps all trailing whitespace for hard line breaks.
                if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                    line_end = content.find("\n", match.end)
                    if line_end == -1:
                        line_end = len(content)
                    replacement = self._strip_trailing_ws(
                        replacement,
                        preserve_last_line=bool(content[match.end:line_end].strip()),
                    )
                replacement = _preserve_quote_style(norm_old, match.text, replacement)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

                # Only consume the trailing newline when deleting complete lines;
                # inline suffix deletions must preserve the remaining line boundary.
                end = match.end
                if (
                    replacement == ""
                    and (match.start == 0 or content[match.start - 1] == "\n")
                    and not match.text.endswith("\n")
                    and content[end:end + 1] == "\n"
                ):
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            self._file_states.record_write(fp)
            return self._format_summary(fp, content, new_content)
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error editing file: {e}")

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return ToolResult.error("\n".join(parts))

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return ToolResult.error(
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )

        if hints:
            return ToolResult.error(
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return ToolResult.error(f"Error: old_text not found in {path}. No similar text found. Verify the file content.")


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The directory path to list"),
        recursive=BooleanSchema(description="Recursively list all files (default false)"),
        max_entries=IntegerSchema(
            description="Maximum entries to return (default 200)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""
    _scopes = {"core", "subagent"}

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List directory contents, skipping common noise directories "
            "(.git, node_modules, __pycache__, etc.)."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return ToolResult.error(f"Error: Directory not found: {path}")
            if not dp.is_dir():
                return ToolResult.error(f"Error: Not a directory: {path}")

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    rel = item.relative_to(dp)
                    if any(p in self._IGNORE_DIRS for p in rel.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error listing directory: {e}")

```
