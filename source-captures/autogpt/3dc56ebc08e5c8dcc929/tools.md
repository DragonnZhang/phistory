# AutoGPT — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## autogpt_platform/backend/backend/copilot/baseline/service.py

Source: https://github.com/Significant-Gravitas/AutoGPT/blob/13ac38c5b6583bd01b0a6142b0060fc03e102b6e/autogpt_platform/backend/backend/copilot/baseline/service.py

SHA-256: `50a62dc8fe9391e5a12b4b8291f71ec105f1d12a359cb2ab47d76236d8deeb1c`

```
"""Baseline LLM fallback — OpenAI-compatible streaming with tool calling.

Used when ``CHAT_USE_CLAUDE_AGENT_SDK=false``, e.g. as a fallback when the
Claude Agent SDK / Anthropic API is unavailable.  Routes through any
OpenAI-compatible provider (OpenRouter by default) and reuses the same
shared tool registry as the SDK path.
"""

import asyncio
import base64
import logging
import math
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import orjson
from openai import APIConnectionError
from openai import omit as openai_omit
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from backend.blocks.desktop._common import workspace_volume_mounts
from backend.copilot import engine_switch
from backend.copilot.ai_spans import stamp_turn_trace_id
from backend.copilot.anthropic_rate_card import (
    compute_anthropic_cost_usd,
    get_max_output_tokens,
)
from backend.copilot.baseline.image_context import prune_image_context
from backend.copilot.baseline.reasoning import (
    BaselineReasoningEmitter,
    anthropic_thinking_extra_body,
    reasoning_extra_body,
)
from backend.copilot.baseline.tool_order import writes_files
from backend.copilot.baseline.tool_persistence import BaselineToolPersistence
from backend.copilot.budget_signal import build_turn_budget_block
from backend.copilot.builder_context import (
    build_builder_context_turn_prefix,
    build_builder_system_prompt_suffix,
)
from backend.copilot.capabilities.dispatch import resolve_tool_dispatch
from backend.copilot.config import CopilotLlmAuthProvider, CopilotLLMModel
from backend.copilot.context import (
    get_workspace_manager,
    is_unattended_turn,
    set_execution_context,
)
from backend.copilot.continuation import send_trailing_note_as_user
from backend.copilot.expert_context import build_expert_identity_suffix
from backend.copilot.expert_kickoff import is_expert_kickoff_turn
from backend.copilot.gate import active_mode
from backend.copilot.gate.held import resolve_answered
from backend.copilot.graphiti.context import fetch_warm_context
from backend.copilot.graphiti.context_marker import append_injected_memory_block
from backend.copilot.graphiti.context_refresh import join_refresh, start_refresh
from backend.copilot.graphiti.ingest import enqueue_conversation_turn
from backend.copilot.hidden_rows import visible_to_model
from backend.copilot.host_fs import save_new_file
from backend.copilot.learning.capture import capture_chat_turn, find_initiating_user_row
from backend.copilot.legacy_first_turn_memory import without_stored_first_turn_memory
from backend.copilot.llm_attribution import openrouter_attribution
from backend.copilot.local_context_probe import (
    compaction_target_for_window,
    probe_local_context_window,
)
from backend.copilot.markers import append_error_marker
from backend.copilot.model import (
    ChatMessage,
    ChatSession,
    RoutingSource,
    clear_pending_question,
    get_chat_session,
    maybe_append_user_message,
    upsert_chat_session,
)
from backend.copilot.model_normalize import normalize_model_for_transport
from backend.copilot.model_router import ResolvedModel, resolve_model_route
from backend.copilot.moonshot import is_moonshot_model
from backend.copilot.pending_message_helpers import (
    combine_pending_with_current,
    drain_pending_safe,
    drained_rows_entry,
    persist_pending_as_user_rows,
    persist_session_safe,
    persist_unfolded_rows,
)
from backend.copilot.pending_messages import (
    PendingMessage,
    drain_pending_messages,
    format_pending_as_user_message,
    pending_message_text,
)
from backend.copilot.permissions import allowed_providers, denied_tool_names
from backend.copilot.prompting import (
    SHARED_TOOL_NOTES,
    approval_mode_supplement,
    get_chat_platform_supplement,
    get_delegation_supplement,
    get_expert_oversight_supplement,
    get_graphiti_supplement,
    get_provider_ceiling_supplement,
    get_team_building_supplement,
)
from backend.copilot.provider_failure import classify as classify_provider_failure
from backend.copilot.rate_limit import build_budget_ctx
from backend.copilot.response_model import (
    StreamBaseResponse,
    StreamCheckpoint,
    StreamError,
    StreamFinish,
    StreamFinishStep,
    StreamModeChanged,
    StreamProviderFailure,
    StreamStart,
    StreamStartStep,
    StreamStatus,
    StreamTextDelta,
    StreamTextEnd,
    StreamTextStart,
    StreamToolDisplayAvailable,
    StreamToolInputAvailable,
    StreamToolInputStart,
    StreamToolOutputAvailable,
    StreamUsage,
    ToolDisplayData,
)
from backend.copilot.sandbox_status import build_sandbox_replaced_notice
from backend.copilot.service import (
    _build_system_prompt,
    _get_main_client,
    _update_title_async,
    config,
    inject_user_context,
    strip_user_context_tags,
)
from backend.copilot.session_cleanup import prune_orphan_tool_calls
from backend.copilot.stream_checkpoint import turn_checkpoint
from backend.copilot.thinking_stripper import ThinkingStripper as _ThinkingStripper
from backend.copilot.token_tracking import (
    _extract_cache_creation_tokens,
    persist_and_record_usage,
)
from backend.copilot.tool_display import tool_calls_for_provider, tool_display_context
from backend.copilot.tools import (
    ToolGroup,
    execute_tool,
    expert_tool_disabled_groups,
    get_available_tools,
    kickoff_turn_disabled_tools,
    origin_disabled_tools,
    tool_names_in_groups,
)
from backend.copilot.tools.e2b_sandbox import (
    count_expert_turn,
    forget_turn_ceiling,
    get_or_create_sandbox,
    pause_sandbox_direct,
)
from backend.copilot.tools.seen_capabilities import build_seen_capabilities_notice
from backend.copilot.tools.session_context import build_session_context
from backend.copilot.tools.skill_matching import build_matched_skills_notice
from backend.copilot.tools.skills import (
    build_skills_context,
    build_skills_update_notice,
)
from backend.copilot.tracking import track_user_message
from backend.copilot.transcript import (
    STOP_REASON_END_TURN,
    STOP_REASON_TOOL_USE,
    TranscriptDownload,
    detect_gap,
    download_transcript,
    extract_context_messages,
    fill_hole_between_transcript_and_gap,
    next_uncovered_sequence,
    strip_for_upload,
    upload_transcript,
    validate_transcript,
)
from backend.copilot.transcript_builder import TranscriptBuilder
from backend.util import json as util_json
from backend.util.exceptions import NotFoundError
from backend.util.llm.provider_billing import (
    PROVIDER_UNAVAILABLE_CODE,
    PROVIDER_UNAVAILABLE_MESSAGE,
    is_provider_out_of_credits,
    report_provider_out_of_credits,
)
from backend.util.llm.providers import call_provider_stream
from backend.util.prompt import (
    compress_context,
    estimate_token_count,
    estimate_token_count_str,
)
from backend.util.tool_call_loop import (
    LLMLoopResponse,
    LLMToolCall,
    ToolCallResult,
    tool_call_loop,
)

if TYPE_CHECKING:
    from backend.copilot.permissions import CopilotPermissions
    from backend.copilot.tree import TurnEnvelope

logger = logging.getLogger(__name__)

# Set to hold background tasks to prevent garbage collection
_background_tasks: set[asyncio.Task[Any]] = set()


def _pause_uncounted_box(
    sandbox: Any, session_id: str, expert_id: str | None
) -> asyncio.Task[Any] | None:
    """Pause a box opened for a turn that ended before its turn was counted.

    A session's box has nobody else on it, so it is paused straight away
    (fire-and-forget, like the turn-end pause).  An expert's box may be
    carrying another turn and this one never counted itself, so it is left
    for the lifecycle timeout rather than paused under someone else.
    """
    if expert_id:
        return None
    task = asyncio.create_task(pause_sandbox_direct(sandbox, session_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _pause_counted_box(sandbox: Any, session_id: str, expert_id: str | None) -> None:
    """Pause the box at a counted turn's end, fire-and-forget; an expert's box
    stays running while another of its turns is active."""
    task = asyncio.create_task(
        pause_sandbox_direct(sandbox, session_id, expert_id=expert_id)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Hint appended on the last tool round so the model wraps up with a summary
# instead of issuing another tool call that gets cut off cold. The shared
# ``tool_call_loop`` drops ``tools`` on the last iteration (see util/tool_call_loop.py),
# so the model is forced to produce text and always finishes naturally.
_LAST_ITERATION_HINT = (
    "You have reached the tool-call budget for this turn. Do not call any "
    "more tools — produce a final text response summarizing what you did, "
    "what remains, and how the user can continue the work in the next turn."
)

# Fallback surfaced when the tool-round budget is exhausted *and* the forced-
# text last round left the user with zero visible response.
_BUDGET_EXHAUSTED_FALLBACK_TEXT = (
    "Reached the tool-call budget for this turn. "
    "Send a follow-up message to continue from here."
)

# Fallback surfaced when the model finished naturally (under the budget) but
# left the terminal round with zero visible text — the baseline equivalent
# of the SDK's ``StreamError(code="empty_completion")``. Without this the FE
# silently shows nothing after a long thinking-only turn on baseline-routed
# sessions (e.g. some Kimi K2.6 cohorts).
_NATURAL_FINISH_EMPTY_FALLBACK_TEXT = (
    "The model returned no response for this turn. "
    "Please try again or rephrase your request."
)


def _silent_finish_fallback_events(
    notice: str | None,
) -> tuple[list[StreamBaseResponse], str]:
    """Wrap a fallback notice (or ``None``) into the matching wire events.

    Shared by the budget-exhausted and natural-finish-empty branches so the
    event sequencing stays identical — only the message text differs.
    """
    if notice is None:
        return [], ""
    block_id = str(uuid.uuid4())
    events: list[StreamBaseResponse] = [
        StreamTextStart(id=block_id),
        StreamTextDelta(id=block_id, delta=notice),
        StreamTextEnd(id=block_id),
    ]
    return events, notice


def _budget_exhausted_notice_text(terminal_round_text: str) -> str | None:
    """Return the fallback notice when a budget-exhausted turn produced no
    visible text, or ``None`` when the model already summarised itself.

    ``terminal_round_text`` is the text added by the *final* round only —
    earlier-round chatter shouldn't mask a silent final round.
    """
    if terminal_round_text.strip():
        return None
    return _BUDGET_EXHAUSTED_FALLBACK_TEXT


def _natural_finish_empty_notice_text(terminal_round_text: str) -> str | None:
    """Return the fallback notice when a naturally-finishing turn produced
    no visible text, or ``None`` when the model already summarised itself.
    """
    if terminal_round_text.strip():
        return None
    return _NATURAL_FINISH_EMPTY_FALLBACK_TEXT


def _build_budget_exhausted_fallback_events(
    terminal_round_text: str,
) -> tuple[list[StreamBaseResponse], str]:
    """Build the fallback stream events surfaced when a budget-exhausted
    turn left the terminal round with no visible text.

    Returns ``(events, text_to_append)``.  Empty list + empty string when
    no fallback is needed.  Split out of the async generator so it's unit-
    testable without the surrounding streaming machinery.
    """
    return _silent_finish_fallback_events(
        _budget_exhausted_notice_text(terminal_round_text)
    )


def _build_natural_finish_empty_fallback_events(
    terminal_round_text: str,
) -> tuple[list[StreamBaseResponse], str]:
    """Build the fallback stream events surfaced when a naturally-finishing
    turn left the terminal round with no visible text.

    Returns ``(events, text_to_append)``.  Empty list + empty string when
    no fallback is needed.
    """
    return _silent_finish_fallback_events(
        _natural_finish_empty_notice_text(terminal_round_text)
    )


# Max seconds to wait for transcript upload in the finally block before
# letting it continue as a background task (tracked in _background_tasks).
_TRANSCRIPT_UPLOAD_TIMEOUT_S = 5

# MIME types that can be embedded as vision content blocks (OpenAI format).
_VISION_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


# Max size for embedding images directly in the user message (20 MiB raw).
_MAX_INLINE_IMAGE_BYTES = 20 * 1024 * 1024

# OpenRouter-specific extra_body flag that embeds the real generation cost
# into the final usage chunk. Module-level constant so we don't reallocate
# an identical dict on every streaming call.
_OPENROUTER_INCLUDE_USAGE_COST = {"usage": {"include": True}}


def _extract_usage_cost(usage: CompletionUsage) -> float | None:
    """Return the provider-reported USD cost on a streaming usage chunk.

    OpenRouter piggybacks a ``cost`` field on the OpenAI-compatible usage
    object when the request body includes ``usage: {"include": True}``.
    The OpenAI SDK's typed ``CompletionUsage`` does not declare it, so we
    read it off ``model_extra`` (the pydantic v2 container for extras) to
    keep the access fully typed — no ``getattr``.

    Returns ``None`` when the field is absent, explicitly null,
    non-numeric, non-finite, or negative. Invalid values (including
    present-but-null) are logged here — they indicate a provider bug
    worth chasing; plain absences are silent so the caller can dedupe
    the "missing cost" warning per stream.
    """
    extras = usage.model_extra or {}
    if "cost" not in extras:
        return None
    raw = extras["cost"]
    if raw is None:
        logger.error("[Baseline] usage.cost is present but null")
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.error("[Baseline] usage.cost is not numeric: %r", raw)
        return None
    if not math.isfinite(val) or val < 0:
        logger.error("[Baseline] usage.cost is non-finite or negative: %r", val)
        return None
    return val


async def _prepare_baseline_attachments(
    file_ids: list[str],
    user_id: str,
    session_id: str,
    working_dir: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Download workspace files and prepare them for the baseline LLM.

    Images become OpenAI-format vision content blocks.  Non-image files are
    saved to *working_dir* so tool handlers can access them.

    Returns ``(hint_text, image_blocks)``.
    """
    if not file_ids or not user_id:
        return "", []

    try:
        manager = await get_workspace_manager(user_id, session_id)
    except Exception:
        logger.warning(
            "Failed to create workspace manager for file attachments",
            exc_info=True,
        )
        return "", []

    image_blocks: list[dict[str, Any]] = []
    file_descriptions: list[str] = []

    for fid in file_ids:
        try:
            file_info = await manager.get_file_info(fid)
            if file_info is None:
                continue
            content = await manager.read_file_by_id(fid)
            mime = (file_info.mime_type or "").split(";")[0].strip().lower()

            if mime in _VISION_MIME_TYPES and len(content) <= _MAX_INLINE_IMAGE_BYTES:
                b64 = base64.b64encode(content).decode("ascii")
                image_blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": b64},
                    }
                )
                file_descriptions.append(
                    f"- {file_info.name} ({mime}, "
                    f"{file_info.size_bytes:,} bytes) [embedded as image]"
                )
            else:
                candidate = save_new_file(working_dir, file_info.name, content)
                file_descriptions.append(
                    f"- {file_info.name} ({mime}, "
                    f"{file_info.size_bytes:,} bytes) saved to "
                    f"{os.path.basename(candidate)}"
                )
        except Exception:
            logger.warning("Failed to prepare file %s", fid[:12], exc_info=True)

    if not file_descriptions:
        return "", []

    noun = "file" if len(file_descriptions) == 1 else "files"
    has_non_images = len(file_descriptions) > len(image_blocks)
    read_hint = (
        " Use the read_workspace_file tool to view non-image files."
        if has_non_images
        else ""
    )
    hint = (
        f"\n[The user attached {len(file_descriptions)} {noun}.{read_hint}\n"
        + "\n".join(file_descriptions)
        + "]"
    )
    return hint, image_blocks


def _filter_tools_by_permissions(
    tools: list[ChatCompletionToolParam],
    permissions: "CopilotPermissions",
) -> list[ChatCompletionToolParam]:
    """Filter OpenAI-format tools based on CopilotPermissions.

    Uses short tool names (the ``function.name`` field) to compute the
    effective allowed set, then keeps only matching tools.
    """
    from backend.copilot.permissions import all_known_tool_names

    if permissions.is_empty():
        return tools

    all_tools = all_known_tool_names()
    effective = permissions.effective_allowed_tools(all_tools)

    return [
        t
        for t in tools
        if t.get("function", {}).get("name") in effective  # type: ignore[union-attr]
    ]


async def _resolve_baseline_model(
    tier: CopilotLLMModel | None, user_id: str | None
) -> ResolvedModel:
    """Pick the model for the baseline path based on the per-request tier.

    Delegates to :func:`copilot.model_router.resolve_model_route` so the
    ``(fast, tier)`` cell resolves LD → registry cell → env.  ``None`` tier
    maps to ``"standard"``.  The routing source rides along so persisted
    assistant messages can be stamped for product-intelligence segmentation.
    """
    tier_name = "advanced" if tier == "advanced" else "standard"
    return await resolve_model_route("fast", tier_name, user_id, config=config)


@dataclass
class _BaselineStreamState:
    """Mutable state shared between the tool-call loop callbacks.

    Extracted from ``stream_chat_completion_baseline`` so that the callbacks
    can be module-level functions instead of deeply nested closures.
    """

    model: str = ""
    # Which routing layer picked ``model`` — stamped onto persisted assistant
    # messages for product-intelligence segmentation. The baseline path only
    # ever produces "ld" | "catalog" | "env" ("fallback" is SDK-only, marking
    # a CLI 529-overload swap); typed as the shared RoutingSource superset.
    routing_source: RoutingSource = "env"
    # The connection this turn runs on, stamped onto each assistant row as
    # it is built. Recorded per turn so a later route change cannot rewrite
    # what already ran; see backend/copilot/segments.py.
    llm_auth_provider: CopilotLlmAuthProvider | None = None
    llm_credential_id: str | None = None
    # Whose turn this is and in which chat, sent on OpenRouter requests so
    # Broadcast attributes each generation (see ``llm_attribution``).
    user_id: str | None = None
    session_id: str | None = None
    # Live delivery channel drained concurrently by ``stream_chat_completion_baseline``
    # so reasoning / text / tool events reach the SSE wire **during** the upstream
    # LLM stream, not after ``_baseline_llm_caller`` returns.  Before this was a
    # ``list`` drained per ``tool_call_loop`` iteration, so any model with
    # extended thinking (Anthropic via OpenRouter, Moonshot, future reasoning
    # routes) froze the UI for the entire duration of each LLM round before
    # flushing the backlog in one burst.  The queue is single-producer (the
    # streaming loop) / single-consumer (the outer async-gen yield loop);
    # ``None`` is the close sentinel.
    pending_events: asyncio.Queue[StreamBaseResponse | None] = field(
        default_factory=asyncio.Queue
    )
    # Mirror of every event put on ``pending_events`` — kept for unit tests that
    # inspect post-hoc what was emitted.  Not consumed by production code.
    emitted_events: list[StreamBaseResponse] = field(default_factory=list)
    assistant_text: str = ""
    text_block_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text_started: bool = False
    reasoning_emitter: BaselineReasoningEmitter = field(init=False)
    turn_prompt_tokens: int = 0
    turn_completion_tokens: int = 0
    turn_cache_read_tokens: int = 0
    turn_cache_creation_tokens: int = 0
    cost_usd: float | None = None
    # Tracks whether we've already warned about a missing `cost` field in
    # the usage chunk this stream, so non-OpenRouter providers don't
    # generate one warning per streaming call.
    cost_missing_logged: bool = False
    thinking_stripper: _ThinkingStripper = field(default_factory=_ThinkingStripper)
    # MUTATE in place only — ``__post_init__`` hands this list reference to
    # ``BaselineReasoningEmitter`` so reasoning rows can be appended as
    # deltas stream in.  Reassigning (``state.session_messages = [...]``)
    # would silently detach the emitter from the new list.
    session_messages: list[ChatMessage] = field(default_factory=list)
    tool_persistence: BaselineToolPersistence = field(
        default_factory=BaselineToolPersistence
    )
    # Tracks how much of ``assistant_text`` has already been flushed to
    # ``session.messages`` via mid-loop pending drains, so the ``finally``
    # block only appends the *new* assistant text (avoiding duplication of
    # round-1 text when round-1 entries were cleared from session_messages).
    _flushed_assistant_text_len: int = 0
    # Memoised system-message dict with cache_control applied.  The system
    # prompt is static within a session, so we build it once on the first
    # LLM round and reuse the same dict on subsequent rounds — avoiding
    # an O(N) dict-copy of the growing ``messages`` list on every tool-call
    # iteration.  ``None`` means "not yet computed" (or the first message
    # wasn't a system role, so no marking applies).
    cached_system_message: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # Wire the reasoning emitter to ``session_messages`` so it can
        # append ``role="reasoning"`` rows as reasoning streams in — the
        # frontend's ``convertChatSessionToUiMessages`` relies on these
        # rows to render the Reasoning collapse after the AI SDK's
        # stream-end hydrate swaps in the DB-backed message list.
        self.reasoning_emitter = BaselineReasoningEmitter(self.session_messages)


async def _refresh_follow_up_warm_context(
    warm_ctx: str | None,
    *,
    graphiti_enabled: bool,
    user_id: str | None,
    expert_id: str | None,
    is_user_message: bool,
    pre_drain_msg_count: int,
    message: str | None,
) -> str | None:
    """Fill in warm context on a follow-up user turn (SECRT-2378).

    Returns the refreshed block, or ``warm_ctx`` UNCHANGED whenever the
    refresh yields nothing — fall back, never overwrite. Called after the
    pending fold, so the query is the combined message (a queued substantive
    request paired with a short "ok" must still drive recall), and scoped by
    ``expert_id`` to the same graph the first turn read.

    Diverges from the SDK's ``_append_follow_up_warm_context`` in two
    respects. The baseline compactor doesn't surface ``was_compacted``, so
    there is no ``force=True`` and a trivially short post-compaction turn
    skips recall here. And the refresh starts here, after the fold, with
    nothing to overlap, so the join grace (``join_refresh``,
    ``warm_context_refresh_join_grace_ms``) is its whole budget: a refresh
    still running then is cancelled and the turn goes on without it.
    Documented debt; SDK is the production engine. The block is appended to
    the model's input only, never to the transcript.
    """
    if not (graphiti_enabled and user_id and is_user_message):
        return warm_ctx
    if pre_drain_msg_count <= 1:
        return warm_ctx
    pending = start_refresh(user_id, message, expert_id=expert_id)
    if pending is None:
        return warm_ctx
    refreshed = await join_refresh(pending)
    return refreshed if refreshed else warm_ctx


def _emit(state: "_BaselineStreamState", event: StreamBaseResponse) -> None:
    """Queue *event* for the live SSE wire AND mirror into ``emitted_events``.

    Single helper so every streaming producer (LLM stream loop, tool executor,
    conversation updater) posts to the same single-consumer queue.  The mirror
    list is read-only from production code — it exists so unit tests can assert
    on the full sequence emitted during one call.
    """
    state.pending_events.put_nowait(event)
    state.emitted_events.append(event)


def _emit_all(
    state: "_BaselineStreamState", events: Iterable[StreamBaseResponse]
) -> None:
    """Queue *events* in order — convenience for emitter batches."""
    for event in events:
        _emit(state, event)


def _is_anthropic_model(model: str) -> bool:
    """Return True if *model* routes to Anthropic (native or via OpenRouter).

    Examples that return True:
      - ``anthropic/claude-sonnet-4-6`` (OpenRouter route)
      - ``claude-3-5-sonnet-20241022`` (direct Anthropic API)
      - ``anthropic.claude-3-5-sonnet`` (Bedrock-style)

    False for ``openai/gpt-4o``, ``google/gemini-2.5-pro``, ``xai/grok-4``
    etc.  Moonshot is False here too even though Moonshot's
    Anthropic-compat endpoint honours ``cache_control`` — use
    :func:`_supports_prompt_cache_markers` for the cache-gating decision,
    which also allows Moonshot routes.  This function stays scoped to
    "genuinely Anthropic" so callers that need the stricter check (e.g.
    ``anthropic-beta`` header emission) keep their existing semantics.
    """
    lowered = model.lower()
    return "claude" in lowered or lowered.startswith("anthropic")


def _supports_prompt_cache_markers(model: str) -> bool:
    """Return True when *model* accepts Anthropic-style ``cache_control``.

    Superset of :func:`_is_anthropic_model` — also allows Moonshot
    (``moonshotai/*``), whose OpenRouter Anthropic-compat endpoint
    honours the marker and empirically lifts cache hit rate on
    continuation turns from near-zero (Moonshot's own automatic prefix
    cache, which drifts readily) to the 60-95% Anthropic ballpark.

    OpenAI / Grok / Gemini still 400 on ``cache_control``, so this
    function returns False for those providers — add new vendors here
    only after verifying their endpoint accepts the field.
    """
    return _is_anthropic_model(model) or is_moonshot_model(model)


def _fresh_ephemeral_cache_control() -> dict[str, str]:
    """Return a FRESH ephemeral ``cache_control`` dict each call.

    The ``ttl`` is sourced from :attr:`ChatConfig.baseline_prompt_cache_ttl`
    (default ``1h``) so the static prefix stays warm across many users'
    requests in the same workspace cache.  Anthropic caches are keyed
    per-workspace, so every copilot user reading the same system prompt
    hits the same cached entry.

    Using a shared module-level dict would let any downstream mutation
    (e.g. the OpenAI SDK normalising fields in-place) poison every future
    request's marker.  Construction is O(1) so the safety margin is free.
    """
    return {"type": "ephemeral", "ttl": config.baseline_prompt_cache_ttl}


def _fresh_anthropic_caching_headers() -> dict[str, str]:
    """Return a FRESH ``extra_headers`` dict requesting the Anthropic
    prompt-caching beta.

    Same reasoning as :func:`_fresh_ephemeral_cache_control`: never hand a
    shared module-level dict to third-party SDKs.  OpenRouter auto-forwards
    cache_control for Anthropic routes without this header, but passing it
    makes the intent unambiguous on-wire and is a no-op for non-Anthropic
    providers (unknown headers are dropped).
    """
    return {"anthropic-beta": "prompt-caching-2024-07-31"}


def _mark_tools_with_cache_control(
    tools: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copy of *tools* with ``cache_control`` on the last entry.

    Marking the last tool is a cache breakpoint that covers the whole tool
    schema block as a cacheable prefix segment.  Extracted from
    :func:`_mark_system_message_with_cache_control` so callers can precompute
    the marked tool list once per session — the tool set is static within a
    request and the ~43 dict-copies would otherwise run on every LLM round
    in the tool-call loop.

    **Only call this for Anthropic model routes.**  Non-Anthropic providers
    (OpenAI, Grok, Gemini) reject the unknown ``cache_control`` field with
    a 400 schema validation error.  Gate via :func:`_is_anthropic_model`.
    """
    cached: list[dict[str, Any]] = [dict(t) for t in tools]
    if cached:
        cached[-1] = {
            **cached[-1],
            "cache_control": _fresh_ephemeral_cache_control(),
        }
    return cached


def _build_cached_system_message(
    system_message: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy of *system_message* with ``cache_control`` applied.

    Anthropic's cache uses prefix-match with up to 4 explicit breakpoints.
    Combined with the last-tool marker this gives two cache segments — the
    system block alone, and system+all-tools — so requests that share only
    the system prefix still get a partial cache hit.

    The system message is rebuilt via spread (``{**original, ...}``) so any
    unknown fields the caller set (e.g. ``name``) survive the transformation.
    Non-Anthropic models silently ignore the markers.

    Returns the original dict (shallow-copied) unchanged when the content
    shape is unsupported (missing / non-string / empty) — callers should
    splice it into the message list as-is in that case.
    """
    sys_copy = dict(system_message)
    sys_content = sys_copy.get("content")
    if isinstance(sys_content, str) and sys_content:
        sys_copy["content"] = [
            {
                "type": "text",
                "text": sys_content,
                "cache_control": _fresh_ephemeral_cache_control(),
            }
        ]
    return sys_copy


_AVAILABLE_SKILLS_BOUNDARY_RE = re.compile(
    r"^(.*?</available_skills>\n\n)(.*)$", re.DOTALL
)


def _split_user_message_after_skills_block(
    content: str,
) -> list[dict[str, Any]] | None:
    """Split *content* into a cacheable prefix + variable suffix at the
    ``</available_skills>\\n\\n`` boundary.

    Returns a two-block content array suitable for an Anthropic-style
    user message — the first block carries the static skill index (any
    server-injected blocks that precede it land in the same prefix) and
    is tagged with ``cache_control: ephemeral`` so the prefix bytes are
    cached PER-USER across that user's turns.  The skill index is stable
    per-user-per-skill-version, so hits stay high until the user
    adds/deletes a skill.

    Returns ``None`` when there is no ``</available_skills>`` boundary
    to split on (no skills configured for this user, or the block was
    not injected) — callers then fall back to passing the message
    string through unchanged.
    """
    match = _AVAILABLE_SKILLS_BOUNDARY_RE.match(content)
    if not match:
        return None
    prefix, suffix = match.group(1), match.group(2)
    # Always emit at least one block — when the suffix is empty (rare:
    # the user typed nothing after the auto-injected skill index) drop
    # the empty trailing block since Anthropic 400s on zero-length text.
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": prefix,
            "cache_control": _fresh_ephemeral_cache_control(),
        }
    ]
    if suffix:
        blocks.append({"type": "text", "text": suffix})
    return blocks


def _apply_skills_cache_breakpoint(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return *messages* with a ``cache_control`` breakpoint placed
    AFTER the ``<available_skills>`` block in the first user message
    that carries one.

    Per-user-stable bytes — the skill index + any other server-injected
    prefix blocks — sit in their own cached prefix.  Only call this when
    the model accepts ``cache_control`` (``_supports_prompt_cache_markers``).

    When no user message carries a ``</available_skills>`` boundary the
    list is returned as-is (with identity-preserved entries — the system
    memoisation in :func:`_baseline_llm_caller` depends on the first
    entry being the cached dict reference).  Only the one message that
    needs the breakpoint is replaced with a shallow copy that carries
    the split content blocks; siblings keep their original identity.
    """
    target_index: int | None = None
    target_blocks: list[dict[str, Any]] | None = None
    for idx, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            # Already a content-block list, or a non-string payload —
            # leave alone so we don't double-mark.
            continue
        blocks = _split_user_message_after_skills_block(content)
        if blocks is None:
            continue
        # Only the first user message carrying ``<available_skills>``
        # is split — that's where ``inject_user_context`` writes it
        # (first turn).  Later user turns won't have the block.
        target_index = idx
        target_blocks = blocks
        break
    if target_index is None or target_blocks is None:
        # Nothing to mark — preserve caller's list identity AND inner
        # dict references so a memoised system dict at messages[0]
        # keeps its `is` identity (test_baseline_llm_caller_memoises_
        # cached_system_message relies on this).  ``cast`` narrows
        # Mapping → dict for Pyright without runtime conversion;
        # entries are already dicts at runtime
        # (built by ``_build_messages_for_llm``).
        return cast(list[dict[str, Any]], list(messages))
    # Targeted shallow copy: only the message that needs the breakpoint
    # is replaced.  Other entries (including a memoised system dict)
    # keep their original identity — same identity-preservation rule
    # as the no-target branch above.
    cached_messages: list[dict[str, Any]] = cast(list[dict[str, Any]], list(messages))
    new_msg = dict(messages[target_index])
    new_msg["content"] = target_blocks
    cached_messages[target_index] = new_msg
    return cached_messages


def _prepend_skills_notice_to_current_message(
    openai_messages: list[dict[str, Any]], notice: str
) -> None:
    """Prepend a ``<skills_update>`` drift notice to the current turn.

    Reverse scan so the notice lands on the current turn's user message,
    not an older one when pending messages were drained. Mutates in place
    (mirrors the builder-context prepend just below the call site) and is
    query-only — callers must not copy this into the persisted transcript.
    No-op for an empty notice.
    """
    if not notice:
        return
    for msg in reversed(openai_messages):
        if msg["role"] == "user":
            _add_message_text(msg, notice, prepend=True)
            break


def _add_message_text(msg: dict[str, Any], text: str, *, prepend: bool) -> None:
    content = msg.get("content") or ""
    if isinstance(content, list):
        block = {"type": "text", "text": text}
        msg["content"] = [block, *content] if prepend else [*content, block]
    else:
        msg["content"] = text + content if prepend else content + text


def _mark_system_message_with_cache_control(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copy of *messages* with ``cache_control`` on the system block.

    Thin wrapper around :func:`_build_cached_system_message` that preserves
    the original list shape.  Prefer the memoised path in
    ``_baseline_llm_caller`` (which builds the cached system dict once per
    session) for hot-loop callers; this function is retained for call sites
    outside the tool-call loop where per-call copying is acceptable.
    """
    cached_messages: list[dict[str, Any]] = [dict(m) for m in messages]
    if cached_messages and cached_messages[0].get("role") == "system":
        cached_messages[0] = _build_cached_system_message(cached_messages[0])
    return cached_messages


async def _baseline_llm_caller(
    messages: list[dict[str, Any]],
    tools: Sequence[Any],
    *,
    state: _BaselineStreamState,
) -> LLMLoopResponse:
    """Stream an OpenAI-compatible response and collect results.

    Extracted from ``stream_chat_completion_baseline`` for readability.
    """
    _emit(state, StreamStartStep())
    # Fresh thinking-strip state per round so a malformed unclosed
    # block in one LLM call cannot silently drop content in the next.
    state.thinking_stripper = _ThinkingStripper()

    round_text = ""
    try:
        prune_image_context(messages)
        client = _get_main_client()
        supports_cache = _supports_prompt_cache_markers(state.model)
        if supports_cache:
            # Build cached system message once; splice on each round to avoid O(n) list copy.
            if (
                state.cached_system_message is None
                and messages
                and messages[0].get("role") == "system"
            ):
                state.cached_system_message = _build_cached_system_message(messages[0])
            if state.cached_system_message is not None and messages:
                final_messages = [state.cached_system_message, *messages[1:]]
            else:
                final_messages = messages
            # Apply a per-user cache breakpoint AFTER the
            # ``<available_skills>`` block in the first user message
            # carrying one.  The skill index is stable per-user across
            # turns (until the user adds/deletes a skill), so caching
            # those bytes separately lifts hit rate on a prefix that
            # the message-level boundary would otherwise re-tokenise
            # every turn.  Anthropic caches use prefix-match with up to
            # four explicit breakpoints; the system + tool markers
            # already use two, leaving room for this one.
            final_messages = _apply_skills_cache_breakpoint(final_messages)
            extra_headers = (
                _fresh_anthropic_caching_headers()
                if _is_anthropic_model(state.model)
                else None
            )
        else:
            final_messages = messages
            extra_headers = None
        typed_messages = cast(list[ChatCompletionMessageParam], final_messages)
        # The wire format must match the endpoint ``main_client_credentials``
        # actually dialed — ``baseline_provider`` is the shared truth for both
        # (local → openrouter_active → anthropic). Keying on
        # ``transport.name == "openrouter"`` would send direct-Anthropic shape
        # to an OpenRouter endpoint in subscription mode with OR creds present,
        # where ``transport.name`` is ``"subscription"`` but the baseline client
        # still routes to OpenRouter.
        baseline_provider = config.baseline_provider
        is_openrouter_transport = baseline_provider == "openrouter"
        extra_body: dict[str, Any] = {}
        if baseline_provider == "local":
            # Local backends govern their own context window at launch (e.g.
            # OLLAMA_CONTEXT_LENGTH); Otto reads it back at runtime for
            # compaction (see local_context_probe). Send no extra_body — skip
            # the OpenRouter ``usage.include`` extension and reasoning params,
            # which stricter local backends reject outright.
            pass
        elif is_openrouter_transport:
            # OR-only: Anthropic's compat endpoint 400s on usage.include + reasoning
            # and on the Broadcast fields, which only OpenRouter reads anyway.
            extra_body.update(dict(_OPENROUTER_INCLUDE_USAGE_COST))
            extra_body.update(
                openrouter_attribution(
                    user_id=state.user_id,
                    session_id=state.session_id,
                    generation_name="copilot-baseline",
                )
            )
            reasoning_param = reasoning_extra_body(
                state.model, config.claude_agent_max_thinking_tokens
            )
            if reasoning_param:
                extra_body.update(reasoning_param)
        else:
            # Direct mode (non-OR, non-local): use native Anthropic thinking param.
            thinking_param = anthropic_thinking_extra_body(
                state.model, config.claude_agent_max_thinking_tokens
            )
            if thinking_param:
                extra_body.update(thinking_param)
        # Direct: Anthropic requires max_tokens > budget_tokens explicitly; OR injects a default.
        max_tokens_arg: int | Any = openai_omit
        if not is_openrouter_transport and "thinking" in extra_body:
            model_max = get_max_output_tokens(state.model)
            budget = min(config.claude_agent_max_thinking_tokens, model_max - 1)
            extra_body["thinking"]["budget_tokens"] = budget
            max_tokens_arg = min(budget + 4096, model_max)
        # Route through the shared providers helper so future provider
        # work (streaming flex tier, new SDK upgrades) propagates here
        # without a parallel migration. The pre-built ``client`` is
        # passed through so the module-level client keeps its HTTP
        # connection pool warm across turns.
        response = await call_provider_stream(
            client=client,
            model=state.model,
            messages=cast(list[dict[str, Any]], typed_messages),
            extra_body=extra_body,
            extra_headers=extra_headers,
            # OR-only: Anthropic's compat endpoint 400s on stream_options; OR embeds cost via it.
            # Local transport (``is_openrouter_transport == False``) also gets
            # ``None`` so stricter local backends don't 400 on the extension.
            stream_options=(
                {"include_usage": True} if is_openrouter_transport else None
            ),
            tools=cast(list[dict[str, Any]] | None, list(tools)) if tools else None,
            max_tokens=max_tokens_arg,
        )
        tool_calls_by_index: dict[int, dict[str, str]] = {}

        # Iterate under an inner try/finally so early exits (cancel, tool-call
        # break, exception) always release the underlying httpx connection.
        # Without this, openai.AsyncStream leaks the streaming response and
        # the TCP socket ends up in CLOSE_WAIT until the process exits.
        try:
            async for chunk in response:
                if chunk.usage:
                    state.turn_prompt_tokens += chunk.usage.prompt_tokens or 0
                    state.turn_completion_tokens += chunk.usage.completion_tokens or 0
                    ptd = chunk.usage.prompt_tokens_details
                    if ptd:
                        state.turn_cache_read_tokens += ptd.cached_tokens or 0
                        state.turn_cache_creation_tokens += (
                            _extract_cache_creation_tokens(ptd)
                        )
                    cost = _extract_usage_cost(chunk.usage)
                    # Rate-card recovery covers direct-Anthropic mode —
                    # Anthropic's OpenAI-compat endpoint doesn't emit OR's
                    # ``usage.cost`` extension, so the rate card is what
                    # produces a cost number on that path. Local
                    # (Ollama/vLLM) transports also lack ``usage.cost`` but
                    # ``compute_anthropic_cost_usd`` returns None for any
                    # non-Anthropic slug, so the recovery is a no-op for
                    # local — skip it explicitly so the intent is clear and
                    # we don't burn a rate-card lookup per usage chunk.
                    direct_mode = baseline_provider == "anthropic"
                    if cost is None and direct_mode:
                        # Direct mode: no usage.cost field (OR extension); compute from rate card.
                        ptd = chunk.usage.prompt_tokens_details
                        cost = compute_anthropic_cost_usd(
                            model=state.model,
                            prompt_tokens=chunk.usage.prompt_tokens or 0,
                            completion_tokens=chunk.usage.completion_tokens or 0,
                            cache_read_tokens=(ptd.cached_tokens or 0) if ptd else 0,
                            cache_creation_tokens=(
                                _extract_cache_creation_tokens(ptd) if ptd else 0
                            ),
                            cache_ttl=config.baseline_prompt_cache_ttl,
                        )
                    if cost is not None:
                        state.cost_usd = (state.cost_usd or 0.0) + cost
                    elif (
                        not direct_mode
                        and "cost" not in (chunk.usage.model_extra or {})
                        and not state.cost_missing_logged
                    ):
                        # OR with cost field absent — warn once so monitoring catches persistent misses.
                        logger.warning(
                            "[Baseline] usage chunk missing cost (model=%s, "
                            "prompt=%s, completion=%s) — rate-limit will "
                            "skip this call",
                            state.model,
                            chunk.usage.prompt_tokens,
                            chunk.usage.completion_tokens,
                        )
                        state.cost_missing_logged = True

                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                _emit_all(state, state.reasoning_emitter.on_delta(delta))

                if delta.content:
                    # Text and reasoning must not interleave on the wire — the
                    # AI SDK maps distinct start/end pairs to distinct UI
                    # parts.  Close any open reasoning block before emitting
                    # the first text delta of this run.
                    _emit_all(state, state.reasoning_emitter.close())
                    emit = state.thinking_stripper.process(delta.content)
                    if emit:
                        if not state.text_started:
                            _emit(state, StreamTextStart(id=state.text_block_id))
                            state.text_started = True
                        round_text += emit
                        _emit(
                            state,
                            StreamTextDelta(id=state.text_block_id, delta=emit),
                        )

                if delta.tool_calls:
                    # Same rule as the text branch: close any open reasoning
                    # block before a tool_use starts so the AI SDK treats
                    # reasoning and tool-use as distinct parts.
                    _emit_all(state, state.reasoning_emitter.close())
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        entry = tool_calls_by_index[idx]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function and tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            entry["arguments"] += tc.function.arguments
        finally:
            # Release the streaming httpx connection back to the pool on every
            # exit path (normal completion, break, exception). openai.AsyncStream
            # does not auto-close when the async-for loop exits early.
            try:
                await response.close()
            except Exception:
                pass

    finally:
        # Close open blocks on both normal and exception paths so the
        # frontend always sees matched start/end pairs.  An exception mid
        # ``async for chunk in response`` would otherwise leave reasoning
        # and/or text unterminated and only ``StreamFinishStep`` emitted —
        # the Reasoning / Text collapses would never finalise.
        _emit_all(state, state.reasoning_emitter.close())
        # Flush any buffered text held back by the thinking stripper.
        tail = state.thinking_stripper.flush()
        if tail:
            if not state.text_started:
                _emit(state, StreamTextStart(id=state.text_block_id))
                state.text_started = True
            round_text += tail
            _emit(state, StreamTextDelta(id=state.text_block_id, delta=tail))
        if state.text_started:
            _emit(state, StreamTextEnd(id=state.text_block_id))
            state.text_started = False
            state.text_block_id = str(uuid.uuid4())
        # Always persist partial text so the session history stays consistent,
        # even when the stream is interrupted by an exception.
        state.assistant_text += round_text
        # Always emit StreamFinishStep to match the StreamStartStep,
        # even if an exception occurred during streaming.
        _emit(state, StreamFinishStep())

    # Convert to shared format
    llm_tool_calls = [
        LLMToolCall(
            id=tc["id"],
            name=tc["name"],
            arguments=tc["arguments"] or "{}",
        )
        for tc in tool_calls_by_index.values()
    ]

    response = LLMLoopResponse(
        response_text=round_text or None,
        tool_calls=llm_tool_calls,
        raw_response=None,  # Not needed for baseline conversation updater
        prompt_tokens=0,  # Tracked via state accumulators
        completion_tokens=0,
    )
    if response.tool_calls:
        _begin_baseline_tool_round(state, response)
    return response


async def _baseline_tool_executor(
    tool_call: LLMToolCall,
    tools: Sequence[Any],
    *,
    state: _BaselineStreamState,
    user_id: str | None,
    session: ChatSession,
    disabled_groups: Sequence[ToolGroup],
    disabled_tools: frozenset[str],
) -> ToolCallResult:
    """Execute a tool via the copilot tool registry.

    Extracted from ``stream_chat_completion_baseline`` for readability.

    ``disabled_groups`` and ``disabled_tools`` are the same values used to
    build the turn's schema list; passing them here makes the capability and
    kickoff gates an enforcement boundary rather than a presentation filter.
    """
    tool_call_id = tool_call.id
    tool_name = tool_call.name
    raw_args = tool_call.arguments or "{}"

    try:
        tool_args = orjson.loads(raw_args)
    except orjson.JSONDecodeError as parse_err:
        parse_error = f"Invalid JSON arguments for tool '{tool_name}': {parse_err}"
        logger.warning("[Baseline] %s", parse_error)
        _emit(
            state,
            StreamToolOutputAvailable(
                toolCallId=tool_call_id,
                toolName=tool_name,
                output=parse_error,
                success=False,
            ),
        )
        return state.tool_persistence.record_result(
            ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=parse_error,
                is_error=True,
            )
        )

    # A dispatch of a platform tool IS a call to that tool, so everything below
    # names the tool that runs rather than the dispatcher it arrived through.
    # ``execute_tool`` still gets the call the model made: refusing a deferred
    # tool named directly is a judgement about that call, not this one.
    called_name, called_args = tool_name, tool_args
    if dispatch := resolve_tool_dispatch(tool_name, tool_args):
        called_name, called_args = dispatch.name, dispatch.args

    _emit(
        state,
        StreamToolInputStart(toolCallId=tool_call_id, toolName=called_name),
    )
    _emit(
        state,
        StreamToolInputAvailable(
            toolCallId=tool_call_id,
            toolName=called_name,
            input=called_args,
        ),
    )

    def on_display_name(name: str) -> None:
        state.tool_persistence.set_display_name(tool_call_id, name)
        _emit(
            state,
            StreamToolDisplayAvailable(
                id=tool_call_id,
                data=ToolDisplayData(toolCallId=tool_call_id, displayName=name),
            ),
        )

    try:
        with tool_display_context(on_display_name):
            result: StreamToolOutputAvailable = await execute_tool(
                tool_name=tool_name,
                parameters=tool_args,
                user_id=user_id,
                session=session,
                tool_call_id=tool_call_id,
                disabled_groups=disabled_groups,
                disabled_tools=disabled_tools,
            )
        _emit(state, result)
        tool_output = (
            result.output if isinstance(result.output, str) else str(result.output)
        )
        return state.tool_persistence.record_result(
            ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=called_name,
                content=tool_output,
                is_error=not result.success,
                image_blocks=[result.image.anthropic()] if result.image else [],
            )
        )
    except Exception as e:
        error_output = f"Tool execution error: {e}"
        logger.error(
            "[Baseline] Tool %s failed: %s",
            called_name,
            error_output,
            exc_info=True,
        )
        _emit(
            state,
            StreamToolOutputAvailable(
                toolCallId=tool_call_id,
                toolName=called_name,
                output=error_output,
                success=False,
            ),
        )
        return state.tool_persistence.record_result(
            ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=called_name,
                content=error_output,
                is_error=True,
            )
        )


def _mutate_openai_messages(
    messages: list[dict[str, Any]],
    response: LLMLoopResponse,
    tool_results: list[ToolCallResult] | None,
) -> None:
    """Append assistant / tool-result entries to the OpenAI message list.

    This is the side-effect boundary for the next LLM call — no transcript
    mutation happens here.
    """
    if tool_results:
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if response.response_text:
            assistant_msg["content"] = response.response_text
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in response.tool_calls
        ]
        messages.append(assistant_msg)
        for tr in tool_results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id,
                    "content": tr.content,
                }
            )
        image_content = [
            block
            for tr in tool_results
            if tr.image_blocks
            for block in (
                {
                    "type": "text",
                    "text": f"Image returned by {tr.tool_name} ({tr.tool_call_id}):",
                },
                *[
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image['source']['media_type']};base64,{image['source']['data']}"
                        },
                    }
                    for image in tr.image_blocks
                ],
            )
        ]
        if image_content:
            messages.append({"role": "user", "content": image_content})
    elif response.response_text:
        messages.append({"role": "assistant", "content": response.response_text})


def _record_turn_to_transcript(
    response: LLMLoopResponse,
    tool_results: list[ToolCallResult] | None,
    *,
    transcript_builder: TranscriptBuilder,
    model: str,
) -> None:
    """Append assistant + tool-result entries to the transcript builder.

    Kept separate from :func:`_mutate_openai_messages` so the two
    concerns (next-LLM-call payload vs. durable conversation log) can
    evolve independently.
    """
    if tool_results:
        content_blocks: list[dict[str, Any]] = []
        if response.response_text:
            content_blocks.append({"type": "text", "text": response.response_text})
        for tc in response.tool_calls:
            try:
                args = orjson.loads(tc.arguments) if tc.arguments else {}
            except (ValueError, TypeError, orjson.JSONDecodeError) as parse_err:
                logger.debug(
                    "[Baseline] Failed to parse tool_call arguments "
                    "(tool=%s, id=%s): %s",
                    tc.name,
                    tc.id,
                    parse_err,
                )
                args = {}
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": args,
                }
            )
        if content_blocks:
            transcript_builder.append_assistant(
                content_blocks=content_blocks,
                model=model,
                stop_reason=STOP_REASON_TOOL_USE,
            )
        for tr in tool_results:
            # Record tool result to transcript AFTER the assistant tool_use
            # block to maintain correct Anthropic API ordering:
            # assistant(tool_use) → user(tool_result)
            transcript_builder.append_tool_result(
                tool_use_id=tr.tool_call_id,
                content=(
                    [{"type": "text", "text": tr.content}, *tr.image_blocks]
                    if tr.image_blocks
                    else tr.content
                ),
            )
    elif response.response_text:
        transcript_builder.append_assistant(
            content_blocks=[{"type": "text", "text": response.response_text}],
            model=model,
            stop_reason=STOP_REASON_END_TURN,
        )


def _baseline_conversation_updater(
    messages: list[dict[str, Any]],
    response: LLMLoopResponse,
    tool_results: list[ToolCallResult] | None = None,
    *,
    transcript_builder: TranscriptBuilder,
    model: str = "",
    state: _BaselineStreamState | None = None,
) -> None:
    """Update OpenAI message list with assistant response + tool results.

    Also finishes the pending tool round for session persistence.
    """
    _mutate_openai_messages(messages, response, tool_results)
    _record_turn_to_transcript(
        response,
        tool_results,
        transcript_builder=transcript_builder,
        model=model,
    )
    if state is not None and tool_results:
        state.tool_persistence.finish(state.session_messages)


def _begin_baseline_tool_round(
    state: _BaselineStreamState, response: LLMLoopResponse
) -> None:
    state.tool_persistence.begin(
        ChatMessage(
            role="assistant",
            model=state.model,
            routing_source=state.routing_source,
            llm_auth_provider=state.llm_auth_provider,
            llm_credential_id=state.llm_credential_id,
            content=response.response_text or "",
            tool_calls=[
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ],
        ),
        state.session_messages,
    )


async def _compress_session_messages(
    messages: list[ChatMessage],
    model: str,
) -> list[ChatMessage]:
    """Compress session messages if they exceed the model's token limit.

    Uses the shared compress_context() utility which supports LLM-based
    summarization of older messages while keeping recent ones intact,
    with progressive truncation and middle-out deletion as fallbacks.
    """
    messages_dict = []
    for msg in messages:
        msg_dict: dict[str, Any] = {"role": msg.role}
        if msg.content:
            msg_dict["content"] = msg.content
        if msg.tool_calls:
            msg_dict["tool_calls"] = tool_calls_for_provider(msg.tool_calls)
        if msg.tool_call_id:
            msg_dict["tool_call_id"] = msg.tool_call_id
        messages_dict.append(msg_dict)

    # Under the local transport the model slug isn't in the cloud model
    # registry, so compress_context()'s default target would fall back to
    # ~120k and never fire before the (much smaller) local window overflows.
    # Probe the backend for its real window and target compaction at it.
    target_tokens: int | None = None
    if config.effective_transport == "local" and config.base_url:
        window = await probe_local_context_window(config.base_url, model)
        target_tokens = compaction_target_for_window(window)

    try:
        result = await compress_context(
            messages=messages_dict,
            target_tokens=target_tokens,
            model=model,
            client=_get_main_client(),
        )
    except Exception as e:
        logger.warning("[Baseline] Context compression with LLM failed: %s", e)
        result = await compress_context(
            messages=messages_dict,
            target_tokens=target_tokens,
            model=model,
            client=None,
        )

    if result.was_compacted:
        logger.info(
            "[Baseline] Context compacted: %d -> %d tokens (%d summarized, %d dropped)",
            result.original_token_count,
            result.token_count,
            result.messages_summarized,
            result.messages_dropped,
        )
        return [
            ChatMessage(
                role=m["role"],
                content=m.get("content"),
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id"),
            )
            for m in result.messages
        ]

    return messages


def _humanize_baseline_error(e: Exception) -> str:
    """Map a raw streaming exception to a user-facing message.

    A connection error under the local transport almost always means the
    operator's LLM backend (Ollama/vLLM/…) isn't running — say so, instead of
    surfacing a bare "Connection error.".
    """
    if isinstance(e, APIConnectionError) and config.effective_transport == "local":
        return (
            f"Can't reach the local LLM backend at {config.base_url}. Make sure "
            "your model server is running — for Ollama, start it with "
            "`ollama serve` (or check CHAT_BASE_URL)."
        )
    return str(e) or type(e).__name__


def _is_platform_out_of_credits(e: Exception, auth_provider: str | None) -> bool:
    """An out-of-credits refusal on the account the platform pays for.

    A linked subscription (Codex) running out is the user's own limit and
    keeps its typed envelope; only the platform route is our outage.
    """
    return auth_provider in (None, "platform") and is_provider_out_of_credits(e)


def should_upload_transcript(user_id: str | None, upload_safe: bool) -> bool:
    """Return ``True`` when the caller should upload the final transcript.

    Uploads require a logged-in user (for the storage key) *and* a safe
    upload signal from ``_load_prior_transcript`` — i.e. GCS does not hold a
    newer version that we'd be overwriting.
    """
    return bool(user_id) and upload_safe


def _append_gap_to_builder(
    gap: list[ChatMessage],
    builder: TranscriptBuilder,
) -> None:
    """Append gap messages from chat-db into the TranscriptBuilder.

    Converts ChatMessage (OpenAI format) to TranscriptBuilder entries
    (Claude CLI JSONL format) so the uploaded transcript covers all turns.

    Pre-condition: ``gap`` always starts at a user or assistant boundary
    (never mid-turn at a ``tool`` role), because ``detect_gap`` enforces
    ``session_messages[wm-1].role == 'assistant'`` before returning a non-empty
    gap.  Any ``tool`` role messages within the gap always follow an assistant
    entry that already exists in the builder or in the gap itself.
    """
    for msg in gap:
        if msg.role == "user":
            builder.append_user(msg.content or "")
        elif msg.role == "assistant":
            content_blocks: list[dict] = []
            if msg.content:
                content_blocks.append({"type": "text", "text": msg.content})
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    input_data = util_json.loads(fn.get("arguments", "{}"), fallback={})
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", "") if isinstance(tc, dict) else "",
                            "name": fn.get("name", "unknown"),
                            "input": input_data,
                        }
                    )
            if not content_blocks:
                # Fallback: ensure every assistant gap message produces an entry
                # so the builder's entry count matches the gap length.
                content_blocks.append({"type": "text", "text": ""})
            builder.append_assistant(content_blocks=content_blocks)
        elif msg.role == "tool":
            if msg.tool_call_id:
                builder.append_tool_result(
                    tool_use_id=msg.tool_call_id,
                    content=msg.content or "",
                )
            else:
                # Malformed tool message — no tool_call_id to link to an
                # assistant tool_use block.  Skip to avoid an unmatched
                # tool_result entry in the builder (which would confuse --resume).
                logger.warning(
                    "[Baseline] Skipping tool gap message with no tool_call_id"
                )


async def _load_prior_transcript(
    user_id: str,
    session_id: str,
    session_messages: list[ChatMessage],
    transcript_builder: TranscriptBuilder,
) -> tuple[bool, "TranscriptDownload | None"]:
    """Download and load the prior CLI session into ``transcript_builder``.

    Returns a tuple of (upload_safe, transcript_download):
    - ``upload_safe`` is ``True`` when it is safe to upload at the end of this
      turn.  Upload is suppressed only for **download errors** (unknown GCS
      state) — missing and invalid files return ``True`` because there is
      nothing in GCS worth protecting against overwriting.
    - ``transcript_download`` is a ``TranscriptDownload`` with str content
      (pre-decoded and stripped) when available, or ``None`` when no valid
      transcript could be loaded.  Callers pass this to
      ``extract_context_messages`` to build the LLM context.
    """
    try:
        restore = await download_transcript(
            user_id, session_id, log_prefix="[Baseline]"
        )
    except Exception as e:
        logger.warning("[Baseline] Session restore failed: %s", e)
        # Unknown GCS state — be conservative, skip upload.
        return False, None

    if restore is None:
        logger.debug("[Baseline] No CLI session available — will upload fresh")
        # Nothing in GCS worth keeping (no file, or one the restore would
        # not resume from): allow upload, with the session's history so far
        # in the builder so the upload covers every turn, not just this one.
        _append_gap_to_builder(
            _history_before_turn(session_messages), transcript_builder
        )
        return True, None

    content_bytes = restore.content
    try:
        raw_str = (
            content_bytes.decode("utf-8")
            if isinstance(content_bytes, bytes)
            else content_bytes
        )
    except UnicodeDecodeError:
        logger.warning("[Baseline] CLI session content is not valid UTF-8")
        # Corrupt file in GCS; overwriting with a valid one is better.
        return True, None

    try:
        stripped = strip_for_upload(raw_str)
    except ValueError:
        logger.warning("[Baseline] CLI session content could not be scrubbed")
        # Corrupt file in GCS; overwriting with a valid one is better.
        return True, None
    if not validate_transcript(stripped):
        logger.warning("[Baseline] CLI session content invalid after strip")
        # Corrupt file in GCS; overwriting with a valid one is better.
        return True, None

    transcript_builder.load_previous(stripped, log_prefix="[Baseline]")
    logger.info(
        "[Baseline] Loaded CLI session: %dB, msg_count=%d",
        len(content_bytes) if isinstance(content_bytes, bytes) else len(raw_str),
        restore.message_count,
    )

    gap = detect_gap(restore, session_messages)
    # Hole-fill: when the cap engaged AND the windowed view starts above the
    # transcript watermark, the sequences in between live in DB but neither
    # the transcript nor the loaded gap.  Persisting them to the builder here
    # (so the next-turn upload includes them) closes the loop — without this,
    # detect_gap on every subsequent turn would re-discover the same hole.
    hole: list[ChatMessage] = []
    if restore.message_count > 0:
        hole = await fill_hole_between_transcript_and_gap(
            session_id, restore.message_count, gap
        )
    if hole or gap:
        # The transcript is what later turns read back as their own history,
        # so a row kept from the model never enters it. The upload's
        # watermark still counts it, so it is not found again as a gap.
        _append_gap_to_builder(visible_to_model(hole + gap), transcript_builder)
        logger.info(
            "[Baseline] Filled gap: loaded %d transcript msgs + %d hole msgs "
            "+ %d gap msgs from DB",
            restore.message_count,
            len(hole),
            len(gap),
        )

    # Return a str-content version so extract_context_messages receives a
    # pre-decoded, stripped transcript (avoids redundant decode + strip).
    # TranscriptDownload.content is typed as bytes | str; we pass str here
    # to avoid a redundant encode + decode round-trip.
    str_restore = TranscriptDownload(
        content=stripped,
        message_count=restore.message_count,
        mode=restore.mode,
    )
    return True, str_restore


def _history_before_turn(session_messages: list[ChatMessage]) -> list[ChatMessage]:
    """The session's messages before the current turn, as the model reads
    them (``extract_context_messages`` without a transcript): a row kept
    from the model is dropped after the slice, so it never seeds the
    transcript later turns read back as their own history."""
    return visible_to_model(
        without_stored_first_turn_memory(
            [m for m in session_messages if m.role != "reasoning"][:-1]
        )
    )


async def _upload_final_transcript(
    user_id: str,
    session_id: str,
    transcript_builder: TranscriptBuilder,
    session_msg_count: int,
) -> None:
    """Serialize and upload the transcript for next-turn continuity.

    Uses the builder's own invariants to decide whether to upload,
    avoiding a JSONL re-parse.  A builder that ends with an assistant
    entry is structurally complete; a builder that doesn't (empty, or
    ends mid-turn) is skipped.
    """
    try:
        if transcript_builder.last_entry_type != "assistant":
            logger.debug(
                "[Baseline] No complete assistant turn to upload (last_entry=%s)",
                transcript_builder.last_entry_type,
            )
            return
        content = transcript_builder.to_jsonl()
        if not content:
            logger.debug("[Baseline] Empty transcript content, skipping upload")
            return
        # Track the upload as a background task so a timeout doesn't leak an
        # orphaned coroutine; shield it so cancellation of this caller doesn't
        # abort the in-flight GCS write.
        upload_task = asyncio.create_task(
            upload_transcript(
                user_id=user_id,
                session_id=session_id,
                content=content.encode("utf-8"),
                message_count=session_msg_count,
                mode="baseline",
                log_prefix="[Baseline]",
            )
        )
        _background_tasks.add(upload_task)
        upload_task.add_done_callback(_background_tasks.discard)
        # Bound the wait: a hung storage backend must not block the response
        # from finishing. The task keeps running in _background_tasks on
        # timeout and will be cleaned up when it resolves.
        await asyncio.wait_for(
            asyncio.shield(upload_task), timeout=_TRANSCRIPT_UPLOAD_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        # Upload is still running in _background_tasks; we just stopped waiting.
        logger.info(
            "[Baseline] Transcript upload exceeded %ss wait — continuing as background task",
            _TRANSCRIPT_UPLOAD_TIMEOUT_S,
        )
    except Exception as upload_err:
        logger.error("[Baseline] Transcript upload failed: %s", upload_err)


async def _fetch_graphiti_context(
    user_id: str,
    session: ChatSession,
    message: str | None,
) -> str | None:
    return await fetch_warm_context(
        user_id,
        message or "",
        expert_id=session.expert_id,
    )


async def _enqueue_graphiti_turn(
    user_id: str,
    session: ChatSession,
    session_id: str,
    message: str,
    assistant_msg: str,
) -> None:
    await enqueue_conversation_turn(
        user_id,
        session_id,
        message,
        assistant_msg=assistant_msg,
        expert_id=session.expert_id,
        bridge_origin=session.metadata.source_platform is not None,
    )


async def stream_chat_completion_baseline(
    session_id: str,
    message: str | None = None,
    is_user_message: bool = True,
    user_id: str | None = None,
    session: ChatSession | None = None,
    file_ids: list[str] | None = None,
    permissions: "CopilotPermissions | None" = None,
    envelope: "TurnEnvelope | None" = None,
    context: dict[str, str] | None = None,
    model: CopilotLLMModel | None = None,
    request_arrival_at: float = 0.0,
    organization_id: str | None = None,
    team_id: str | None = None,
    message_metadata: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> AsyncGenerator[StreamBaseResponse, None]:
    """Baseline LLM with tool calling via OpenAI-compatible API.

    Designed as a fallback when the Claude Agent SDK is unavailable.
    Uses the same tool registry as the SDK path but routes through any
    OpenAI-compatible provider (e.g. OpenRouter).

    Flow: stream response -> if tool_calls, execute them -> feed results back -> repeat.
    """
    if session is None:
        session = await get_chat_session(session_id, user_id)

    if not session:
        raise NotFoundError(
            f"Session {session_id} not found. Please create a new session first."
        )

    expert_session_suffix = await build_expert_identity_suffix(
        session.user_id,
        session.expert_id,
        organization_id=session.organization_id,
        team_id=session.team_id,
    )

    # The session row is the tenancy anchor; the turn entry's org/team only
    # backfills sessions created before org tagging (pre-migration rows).
    if session.organization_id is None and organization_id:
        session.organization_id = organization_id
        session.team_id = team_id

    # Drop orphan tool_use + trailing stop-marker rows left by a previous
    # Stop mid-tool-call so the new turn starts from a well-formed message list.
    prune_orphan_tool_calls(
        session.messages, log_prefix=f"[Baseline] [{session_id[:12]}]"
    )

    # Strip any user-injected <user_context> tags on every turn.
    # Only the server-injected prefix on the first message is trusted.
    if message:
        message = strip_user_context_tags(message)

    # A reply is the only thing that clears a Home "Needs You" question.
    # Unconditional on the append result: the HTTP path pre-saves the user
    # message, so the append is a no-op dedup there.
    if is_user_message and message and message.strip():
        await clear_pending_question(session)

    if maybe_append_user_message(session, message, is_user_message, message_metadata):
        if is_user_message:
            track_user_message(
                user_id=user_id,
                session_id=session_id,
                message_length=len(message or ""),
                expert_id=session.expert_id,
                origin=session.metadata.origin,
                source_platform=session.metadata.source_platform,
            )

    # Capture count *before* the pending drain so is_first_turn and the
    # transcript staleness check are not skewed by queued messages.
    _pre_drain_msg_count = len(session.messages)
    learning_initiating_user = find_initiating_user_row(session, message)
    learning_initiating_text = message
    # The stream's rows start after the message that triggered it; the rows
    # appended before the turn's first yield are announced once it opens.
    turn_start = _pre_drain_msg_count
    opening_entries: list[StreamBaseResponse] = []

    # Drain any messages the user queued via POST /messages/pending
    # while this session was idle (or during a previous turn whose
    # mid-loop drains missed them).
    # The drained content is appended after ``message`` so the user's submitted
    # message remains the leading context (better UX: the user sent their primary
    # message first, queued follow-ups second).  The already-saved user message
    # in the DB is updated via update_message_content_by_sequence rather than
    # inserting a new row, because routes.py has already saved the user message
    # before the executor picks up the turn (using insert_pending_before_last +
    # persist_session_safe would add a duplicate row at sequence N+1).
    drained_at_start_pending = await drain_pending_safe(session_id, "[Baseline]")
    if drained_at_start_pending:
        logger.info(
            "[Baseline] Draining %d pending message(s) at turn start for session %s",
            len(drained_at_start_pending),
            session_id,
        )
        # NOTE: combine + per-row persist both happen *after*
        # ``inject_user_context`` below — see the comment near that
        # call for the ordering rationale.  At this point ``message``
        # is still the original turn-starting send.

    # Select model based on the per-request tier toggle (standard / advanced).
    # The path (fast vs extended_thinking) is already decided — we're in the
    # baseline (fast) path; ``mode`` is accepted for logging parity only.
    # Normalize immediately so EVERY downstream helper (E2B context,
    # ``_compress_session_messages``, the streaming caller) sees the
    # transport-correct slug — otherwise compaction in direct-Anthropic
    # mode silently falls back to the non-LLM path because the OR slug
    # would be rejected by the direct client.  Pass the baseline-side
    # ``config`` so monkeypatch fixtures targeting this module's
    # ``config`` symbol drive the decision.
    resolved_route = await _resolve_baseline_model(model, user_id)
    resolved_model = resolved_route.model
    routing_source = resolved_route.source
    try:
        active_model = normalize_model_for_transport(resolved_model, config)
    except ValueError as exc:
        # Mirror SDK's LD-reject soft-fallback (see ``copilot.sdk.service``):
        # a per-user LD ``copilot-model-routing`` cell can pin a non-Anthropic
        # slug (e.g. ``moonshotai/kimi-*``) on a direct-Anthropic deployment
        # where the baseline transport rejects it.  Fall back to the
        # TIER-SPECIFIC config default so the request still streams instead
        # of erroring at the user.  Re-raises the original error if the
        # config default is also invalid (deployment-level misconfig caught
        # by the ``model_validator`` at startup).
        tier_default = (
            config.fast_advanced_model
            if model == "advanced"
            else config.fast_standard_model
        )
        try:
            active_model = normalize_model_for_transport(tier_default, config)
        except ValueError:
            raise exc
        routing_source = "env"
        logger.warning(
            "[Baseline] [%s] LD model %r rejected for tier=%s (%s); falling "
            "back to tier default %s",
            session_id[:12] if session_id else "?",
            resolved_model,
            "advanced" if model == "advanced" else "standard",
            exc,
            active_model,
        )

    # --- E2B sandbox setup (feature parity with SDK path) ---
    e2b_sandbox = None
    e2b_api_key = config.active_e2b_api_key
    if e2b_api_key:
        try:
            # An expert session runs on the expert's own persistent box;
            # everything else gets a per-session sandbox.
            e2b_sandbox = await get_or_create_sandbox(
                session_id,
                api_key=e2b_api_key,
                template=config.e2b_sandbox_template,
                timeout=config.e2b_sandbox_timeout,
                on_timeout=config.e2b_sandbox_on_timeout,
                volume_mounts=workspace_volume_mounts(user_id, session.expert_id),
                expert_id=session.expert_id,
                user_id=user_id,
                providers=allowed_providers(permissions),
                # Counted just before the try/finally that releases it, below:
                # everything between here and there can still fail or be
                # stopped, and a count with no release keeps the expert's box
                # unpaused at every later turn end.
                count_turn=False,
            )
        except Exception:
            logger.warning("[Baseline] E2B sandbox setup failed", exc_info=True)
            await forget_turn_ceiling(session_id, session.expert_id)

    # --- Transcript support (feature parity with SDK path) ---
    transcript_builder = TranscriptBuilder()
    transcript_upload_safe = True

    # Build system prompt only on the first turn to avoid mid-conversation
    # changes from concurrent chats updating business understanding.
    # Use the pre-drain count so queued pending messages don't incorrectly
    # flip is_first_turn to False on an actual first turn.
    is_first_turn = _pre_drain_msg_count <= 1
    # Gate context fetch on both first turn AND user message so that assistant-
    # role calls (e.g. tool-result submissions) on the first turn don't trigger
    # a needless DB lookup for user understanding.
    should_inject_user_context = is_first_turn and is_user_message

    if should_inject_user_context:
        prompt_task = _build_system_prompt(user_id)
    else:
        prompt_task = _build_system_prompt(None)

    # Run download + prompt build concurrently — both are independent I/O
    # on the request critical path.  Use the pre-drain count so pending
    # messages drained at turn start don't spuriously trigger a transcript
    # load on an actual first turn.
    transcript_download: TranscriptDownload | None = None
    if user_id and _pre_drain_msg_count > 1:
        (
            (transcript_upload_safe, transcript_download),
            (base_system_prompt, understanding),
        ) = await asyncio.gather(
            _load_prior_transcript(
                user_id=user_id,
                session_id=session_id,
                session_messages=session.messages,
                transcript_builder=transcript_builder,
            ),
            prompt_task,
        )
    else:
        base_system_prompt, understanding = await prompt_task

    # Append user message to transcript after context injection below so the
    # transcript receives the prefixed message when user context is available.

    # NOTE: drained pending messages are folded into the current user
    # message's content (see the turn-start drain above), so the single
    # ``transcript_builder.append_user`` call below records the combined
    # text in the transcript. Do NOT also append drained items
    # individually here — on the ``transcript_download is None`` path
    # that would produce N separate pending entries plus the combined
    # entry, duplicating the pending content in the JSONL uploaded for
    # the next turn's ``--resume``.

    # Generate title for new sessions. The title model reads the first
    # message without the memory block an older session stored in it.
    if is_user_message and not session.title:
        user_messages = [
            m
            for m in without_stored_first_turn_memory(session.messages)
            if m.role == "user"
        ]
        if len(user_messages) == 1:
            first_message = user_messages[0].content or message or ""
            if first_message:
                task = asyncio.create_task(
                    _update_title_async(
                        session_id,
                        first_message,
                        user_id,
                        bridge_origin=session.metadata.source_platform is not None,
                    )
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

    message_id = str(uuid.uuid4())

    # Append tool documentation, technical notes, and Graphiti memory instructions
    graphiti_enabled = bool(user_id)

    graphiti_supplement = get_graphiti_supplement() if graphiti_enabled else ""
    auto_mode_supplement = approval_mode_supplement(
        await active_mode(user_id, session),
        interactive=not is_unattended_turn(),
    )
    experts_enabled = bool(user_id)
    delegation_supplement = get_delegation_supplement() if experts_enabled else ""
    oversight_supplement = get_expert_oversight_supplement(
        experts_enabled=experts_enabled, expert_id=session.expert_id
    )
    team_building_supplement = get_team_building_supplement(
        experts_enabled=experts_enabled, expert_id=session.expert_id
    )
    chat_platform_supplement = get_chat_platform_supplement(
        session.metadata.source_platform
    )
    # Append the builder-session block (graph id+name + full building guide)
    # AFTER the shared supplements so the system prompt is byte-identical
    # across turns of the same builder session — Claude's prompt cache keeps
    # the ~20KB guide warm for the whole session.  Empty string for
    # non-builder sessions keeps the cross-user cache hot.
    builder_session_suffix = await build_builder_system_prompt_suffix(session)
    system_prompt = (
        base_system_prompt
        + SHARED_TOOL_NOTES
        # Empty unless the run's permissions restrict connected accounts.
        + get_provider_ceiling_supplement(permissions)
        + delegation_supplement
        + oversight_supplement
        + team_building_supplement
        + chat_platform_supplement
        + graphiti_supplement
        + auto_mode_supplement
        + builder_session_suffix
        + expert_session_suffix
    )

    # Warm context: pre-load relevant facts from Graphiti on first turn.
    # Use the pre-drain count so pending messages drained at turn start
    # don't prevent warm context injection on an actual first turn.
    # Stored here but injected into the user message (not the system prompt)
    # after openai_messages is built — keeps system prompt static for caching.
    warm_ctx: str | None = None
    if graphiti_enabled and user_id and _pre_drain_msg_count <= 1:
        warm_ctx = await _fetch_graphiti_context(user_id, session, message)
    # NOTE: the follow-up-turn refresh (SECRT-2378) runs after the pending
    # drain — see ``_refresh_follow_up_warm_context`` below for why.

    # Context path: transcript content (compacted, isCompactSummary preserved) +
    # gap (DB messages after watermark) + current user turn.
    # This avoids re-reading the full session history from DB on every turn.
    # See extract_context_messages() in transcript.py for the shared primitive.
    # The last stored message stands for the current turn. A turn that stored
    # none of its own reads an earlier one, maybe the session's first, which
    # is read without the memory block an older session stored in it.
    prior_context = await extract_context_messages(
        transcript_download, session.messages, session_id=session.session_id
    )
    messages_for_context = await _compress_session_messages(
        prior_context + without_stored_first_turn_memory(session.messages[-1:]),
        model=active_model,
    )

    # Build OpenAI message list from session history.
    # Include tool_calls on assistant messages and tool-role results so the
    # model retains full context of what tools were invoked and their outcomes.
    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]
    for msg in messages_for_context:
        if msg.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant"}
            if msg.content:
                entry["content"] = msg.content
            if msg.tool_calls:
                entry["tool_calls"] = tool_calls_for_provider(msg.tool_calls)
            if msg.content or msg.tool_calls:
                openai_messages.append(entry)
        elif msg.role == "tool" and msg.tool_call_id:
            openai_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content or "",
                }
            )
        elif msg.role == "user" and msg.content:
            openai_messages.append({"role": msg.role, "content": msg.content})
    # Before anything is appended below: this reads the last entry, and the
    # per-turn blocks then land on the note as they would on a typed message.
    send_trailing_note_as_user(openai_messages)

    # Inject user context into the first user message on first turn.
    # Done before attachment/URL injection so the context prefix lands at
    # the very start of the message content.
    turn_text = message or ""
    if not turn_text and session.messages and session.messages[-1].role == "user":
        turn_text = session.messages[-1].content or ""
    user_message_for_transcript = turn_text
    transcript_context_prefix = ""
    if should_inject_user_context:
        user_message_for_transcript = strip_user_context_tags(turn_text)
        # Mirror the SDK path's "model knows about budget" behaviour by
        # injecting a per-turn ``<budget_context>`` block — the baseline
        # path has no equivalent of the SDK CLI's native
        # ``max_budget_usd`` running-cost reminder, so this is the only
        # signal the model gets about how much USD spend headroom the
        # user has left.
        budget_ctx = await build_budget_ctx(
            user_id=user_id,
            default_daily_cost_limit=config.daily_cost_limit_microdollars,
            default_weekly_cost_limit=config.weekly_cost_limit_microdollars,
        )
        # Per-session follow-up awareness — gives the baseline model the
        # current session_id + pending follow-up summary on its first turn
        # so "cancel that" / "what did I schedule" works without a
        # round-trip to ``list_schedules``.  Lands in the per-turn user
        # message (after the last cache breakpoint) so injection does NOT
        # bust the prefix cache.
        session_ctx_content = ""
        if user_id:
            session_ctx_content = await build_session_context(
                session_id=session_id, user_id=user_id
            )
        # Skill index — same content/contract as the SDK path.  Failures
        # here MUST NOT block the turn; log and proceed with empty index.
        skills_ctx = ""
        try:
            skills_ctx = await build_skills_context(
                user_id, expert_id=session.expert_id
            )
        except Exception:
            logger.exception(
                "[skills] failed to build skills_ctx — proceeding without it"
            )
        prefixed = await inject_user_context(
            understanding,
            user_message_for_transcript,
            session_id,
            session.messages,
            budget_ctx=budget_ctx,
            session_ctx=session_ctx_content,
            skills_ctx=skills_ctx,
            user_id=user_id,
            expert_id=session.expert_id,
        )
        if prefixed is not None:
            # Reverse scan so we update the current turn's user message, not
            # the first (oldest) one when pending messages were drained.
            for msg in reversed(openai_messages):
                if msg["role"] == "user":
                    msg["content"] = prefixed
                    break
            transcript_context_prefix = prefixed.removesuffix(
                user_message_for_transcript
            )
        else:
            logger.warning("[Baseline] No user message found for context injection")
    # Now that ``inject_user_context`` has wrapped + persisted the
    # original turn-starting send into its row, fold pending into the
    # model's current-turn input AND persist each pending message as
    # its own raw-text user row.  Three things to keep in sync:
    #
    #  1. ``openai_messages`` (what the model sees this round) — append
    #     each pending as a separate user entry, matching the mid-turn
    #     drain pattern below.
    #  2. ``message`` and ``user_message_for_transcript`` — combine.
    #  3. ``session.messages`` + DB — one raw row per pending via
    #     ``persist_pending_as_user_rows(transcript_builder=None)``.
    #
    # Order matters: persist must run after inject so inject targets
    # the routes.py-saved row, not a pending row.
    if drained_at_start_pending:
        # Persist FIRST.  Only fold pending into the model's prompt
        # (``message`` for transcript fallback + ``openai_messages`` for
        # the live LLM call) when persistence succeeded — if the helper
        # rolled back and re-queued the pending into Redis, leaving
        # those untouched ensures the NEXT turn's drain doesn't
        # double-combine (re-queued pending + combined-from-this-turn)
        # into the model's context.
        persisted_ok = await persist_pending_as_user_rows(
            session,
            None,
            drained_at_start_pending,
            log_prefix="[Baseline]",
        )
        if persisted_ok:
            opening_entries.append(drained_rows_entry(drained_at_start_pending))
            user_message_for_transcript = combine_pending_with_current(
                drained_at_start_pending,
                user_message_for_transcript,
                request_arrival_at=request_arrival_at,
            )
            message = combine_pending_with_current(
                drained_at_start_pending,
                message,
                request_arrival_at=request_arrival_at,
            )
            turn_text = combine_pending_with_current(
                drained_at_start_pending,
                turn_text,
                request_arrival_at=request_arrival_at,
            )
            for pm in drained_at_start_pending:
                openai_messages.append(format_pending_as_user_message(pm))

    if is_user_message:
        for msg in reversed(openai_messages):
            if msg["role"] == "user":
                matched_skills_notice = await build_matched_skills_notice(
                    user_id, session, turn_text, permissions=permissions
                )
                if matched_skills_notice:
                    _add_message_text(msg, matched_skills_notice, prepend=False)
                break

    # Live budget, every turn — the first-turn ``<budget_context>`` above is
    # stale from turn two onward and says nothing about the tree. After the
    # pending fold so it lands on the message the model reads last, and never
    # on ``user_message_for_transcript``: that would persist one stale figure
    # per turn, the same trap the warm-context injection below names.
    budget_status = await build_turn_budget_block(envelope, user_id)
    if e2b_sandbox is not None:
        budget_status += await build_sandbox_replaced_notice(session_id)
    if budget_status:
        for msg in reversed(openai_messages):
            if msg["role"] == "user":
                _add_message_text(msg, budget_status, prepend=True)
                break

    warm_ctx = await _refresh_follow_up_warm_context(
        warm_ctx,
        graphiti_enabled=graphiti_enabled,
        user_id=user_id,
        expert_id=session.expert_id,
        is_user_message=is_user_message,
        pre_drain_msg_count=_pre_drain_msg_count,
        message=message,
    )

    # Inject Graphiti warm context into the current turn's user message (not
    # the system prompt) so the system prompt stays static and cacheable.
    # warm_ctx is already wrapped in <temporal_context>: the first turn's
    # fetch, or a follow-up's refresh. Appended AFTER user_context so
    # <user_context> stays at the very start, and stamped with the injection
    # mark the SDK engine's blocks carry (``graphiti/context_marker.py``).
    # Reverse scan so we update the current turn's user message, not the
    # oldest one when pending messages were drained.
    if memory_block := append_injected_memory_block("", warm_ctx):
        for msg in reversed(openai_messages):
            if msg["role"] == "user":
                _add_message_text(msg, memory_block, prepend=False)
                break
        # Model input only: NOT the stored user message (``inject_user_context``
        # never takes it) nor ``user_message_for_transcript``, so no later
        # turn reads it again, a fact forgotten after this turn included.

    # Inject the per-turn ``<builder_context>`` prefix when the session is
    # bound to a graph via ``metadata.builder_graph_id``.  Runs on every
    # user turn (not just the first) so the LLM always sees the live graph
    # snapshot — if the user edits the graph between turns, the next turn
    # carries the updated nodes/links. Only version + nodes + links here;
    # the static guide + graph id live in the system prompt via
    # ``build_builder_system_prompt_suffix`` (session-stable, prompt-cached).
    # Prepended AFTER any <user_context>/<env_context> blocks — same trust
    # tier as those server-injected prefixes. Not persisted to the
    # transcript: the snapshot is stale-by-definition after the turn ends.
    if is_user_message and session.metadata.builder_graph_id:
        builder_block = await build_builder_context_turn_prefix(session, user_id)
        if builder_block:
            for msg in reversed(openai_messages):
                if msg["role"] == "user":
                    _add_message_text(msg, builder_block, prepend=True)
                    break

    # Skill-drift notice — same query-only contract as the builder block
    # above: prepended to the live model input, never to
    # ``user_message_for_transcript``, so the persisted history keeps the
    # session-start baseline the next turn diffs against. On the first
    # turn ``inject_user_context`` just wrote a fresh index into history,
    # so the diff is empty by construction and this is a no-op.
    if is_user_message and user_id:
        try:
            skills_notice = await build_skills_update_notice(
                user_id,
                session_id=session.session_id,
                expert_id=session.expert_id,
                prior_contents=[
                    m.content or "" for m in session.messages if m.role == "user"
                ],
            )
        except Exception:
            logger.exception("[skills] failed to build skills update notice")
            skills_notice = ""
        _prepend_skills_notice_to_current_message(openai_messages, skills_notice)
        # NOTE: keep the helper above in sync with _maybe_prepend_skills_update
        # in sdk/service.py — both engines share the query-only contract.
        # Already-seen capability record (SECRT-2791) — same contract, see
        # _maybe_prepend_seen_capabilities in sdk/service.py.
        _prepend_skills_notice_to_current_message(
            openai_messages, build_seen_capabilities_notice(session)
        )

    # Append user message to transcript.
    # Always append when the message is present and is from the user,
    # even on duplicate-suppressed retries (is_new_message=False).
    # The loaded transcript may be stale (uploaded before the previous
    # attempt stored this message), so skipping it would leave the
    # transcript without the user turn, creating a malformed
    # assistant-after-assistant structure when the LLM reply is added.
    # Keep trusted context ahead of user text, including older pending messages.
    if user_message_for_transcript and is_user_message:
        transcript_builder.append_user(
            content=transcript_context_prefix + user_message_for_transcript
        )

    working_dir: str | None = None
    if file_ids and user_id:
        working_dir = tempfile.mkdtemp(prefix=f"copilot-baseline-{session_id[:8]}-")

    # Propagate execution context so tool handlers can read session-level
    # flags. This must precede attachment resolution: the workspace manager
    # derives the expert's file scope from the session set here.
    set_execution_context(
        user_id,
        session,
        sandbox=e2b_sandbox,
        sdk_cwd=working_dir,
        permissions=permissions,
        envelope=envelope,
    )

    # --- File attachments (feature parity with SDK path) ---
    attachment_hint = ""
    image_blocks: list[dict[str, Any]] = []
    if file_ids and user_id and working_dir:
        attachment_hint, image_blocks = await _prepare_baseline_attachments(
            file_ids, user_id, session_id, working_dir
        )

    # --- URL context ---
    context_hint = ""
    if context and context.get("url"):
        url = context["url"]
        content_text = context.get("content", "")
        if content_text:
            context_hint = (
                f"\n[The user shared a URL: {url}\nContent:\n{content_text[:8000]}]"
            )
        else:
            context_hint = f"\n[The user shared a URL: {url}]"

    # Append attachment + context hints and image blocks to the last user
    # message in a single reverse scan.
    extra_hint = attachment_hint + context_hint
    if extra_hint or image_blocks:
        for msg in reversed(openai_messages):
            if msg.get("role") == "user":
                if extra_hint:
                    _add_message_text(msg, "\n" + extra_hint, prepend=False)
                if image_blocks:
                    content = msg.get("content") or ""
                    parts = (
                        content
                        if isinstance(content, list)
                        else [{"type": "text", "text": content}]
                    )
                    msg["content"] = [
                        *parts,
                        *[
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        f"data:{img['source']['media_type']};"
                                        f"base64,{img['source']['data']}"
                                    )
                                },
                            }
                            for img in image_blocks
                        ],
                    ]
                break

    disabled_tool_groups: list[ToolGroup] = []
    if not graphiti_enabled:
        disabled_tool_groups.append("graphiti")
    # ``experts_enabled`` was resolved with the system-prompt supplements
    # above; the role split lives in the shared helper.
    disabled_tool_groups.extend(
        expert_tool_disabled_groups(
            experts_enabled=experts_enabled, expert_id=session.expert_id
        )
    )
    # A hire's kickoff turn is a server-sent control message, so nothing on
    # it was asked for: narrow it to the onboarding card and nothing else.
    # Otherwise hide what the origin gate would refuse anyway.
    disabled_tools = (
        kickoff_turn_disabled_tools()
        if is_expert_kickoff_turn(session)
        else origin_disabled_tools(session.metadata.origin)
    )
    tools = get_available_tools(
        disabled_groups=disabled_tool_groups, disabled_tools=disabled_tools
    )

    # --- Permission filtering ---
    if permissions is not None:
        tools = _filter_tools_by_permissions(tools, permissions)

    # run_capability reaches deferred tools by id; bound it with the same
    # hidden set that shaped the schema list above.
    set_execution_context(
        user_id,
        session,
        sandbox=e2b_sandbox,
        sdk_cwd=working_dir,
        permissions=permissions,
        envelope=envelope,
        hidden_tools=(
            tool_names_in_groups(disabled_tool_groups)
            | disabled_tools
            | denied_tool_names(permissions)
        ),
    )

    # Pre-mark cache_control on the last tool schema once per session.  The
    # tool set is static within a request, so doing this here (instead of in
    # _baseline_llm_caller) avoids re-copying ~43 tool dicts on every LLM
    # round of the tool-call loop.
    #
    # Applies to Anthropic AND Moonshot routes — OpenAI/Grok/Gemini 400
    # on the unknown ``cache_control`` field inside tool definitions, so
    # the gate stays narrow (see :func:`_supports_prompt_cache_markers`).
    if _supports_prompt_cache_markers(active_model):
        tools = cast(
            list[ChatCompletionToolParam], _mark_tools_with_cache_control(tools)
        )

    try:
        yield StreamStart(messageId=message_id, sessionId=session_id)
    except BaseException:
        # Closed or cancelled while suspended on the first yield: the finally
        # that pauses the box sits further down and would never run.
        if e2b_sandbox is not None:
            _pause_uncounted_box(e2b_sandbox, session_id, session.expert_id)
        raise

    if e2b_sandbox is not None:
        # From here the finally below always runs, so the turn can be counted.
        await count_expert_turn(session_id, session.expert_id)

    # After the execution context: an approved held call runs here, in this
    # turn's sandbox and tool bounds, and its result opens the turn.
    unsaved_held: list[PendingMessage] = []
    held_results: list[PendingMessage] = []
    try:
        async for held_status in resolve_answered(
            user_id, session, unsaved_held.append
        ):
            yield held_status
        held_results = [*unsaved_held]
        # The helper appends the rows before its first await, so from here the
        # session carries them.
        unsaved_held.clear()
        folded = bool(held_results) and await persist_pending_as_user_rows(
            session, transcript_builder, held_results, log_prefix="[Baseline]"
        )
    except BaseException:
        # Stopped before the finally below: save what already ran, its approval
        # spent, and end the counted turn on the box.
        await persist_unfolded_rows(session, unsaved_held, log_prefix="[Baseline]")
        if held_results:
            # Cut inside the fold's own save: its rows are on the session only.
            await persist_session_safe(session, "[Baseline]")
        if e2b_sandbox is not None:
            _pause_counted_box(e2b_sandbox, session_id, session.expert_id)
        raise
    if folded:
        opening_entries.append(drained_rows_entry(held_results))
        openai_messages.extend(
            format_pending_as_user_message(pm) for pm in held_results
        )

    _stream_error = False  # Track whether an error occurred during streaming
    state = _BaselineStreamState(
        model=active_model,
        routing_source=routing_source,
        llm_auth_provider=(
            session.metadata.llm_auth_provider if session is not None else None
        ),
        llm_credential_id=(
            session.metadata.llm_credential_id if session is not None else None
        ),
        user_id=user_id,
        session_id=session_id,
    )

    # Queued, not yielded: the loop below yields them inside the try whose
    # finally pauses the sandbox.
    for opening in opening_entries:
        _emit(state, opening)

    # Bind extracted module-level callbacks to this request's state/session
    # using functools.partial so they satisfy the Protocol signatures.
    _bound_llm_caller = partial(_baseline_llm_caller, state=state)

    # ``session`` is reassigned after each mid-turn ``persist_session_safe``
    # call (``upsert_chat_session`` returns a fresh ``model_copy``).  Holding
    # the object via ``partial(session=session)`` would pin tool executions
    # to the *original* object — any post-persist ``session.successful_agent_runs``
    # mutation from a run_agent tool call would then land on the stale copy
    # and be lost on the final persist.  Wrap in a 1-element holder and read
    # the current binding lazily so the executor always sees the latest session.
    _session_holder: list[ChatSession] = [session]
    final_checkpoint: StreamCheckpoint | None = None

    async def _bound_tool_executor(
        tool_call: LLMToolCall, tools: Sequence[Any]
    ) -> ToolCallResult:
        return await _baseline_tool_executor(
            tool_call,
            tools,
            state=state,
            user_id=user_id,
            session=_session_holder[0],
            disabled_groups=disabled_tool_groups,
            disabled_tools=disabled_tools,
        )

    _bound_conversation_updater = partial(
        _baseline_conversation_updater,
        transcript_builder=transcript_builder,
        model=active_model,
        state=state,
    )

    # Run the tool-call loop concurrently with the event consumer so
    # ``StreamReasoning*`` / ``StreamText*`` deltas emitted inside
    # ``_baseline_llm_caller`` reach the SSE wire DURING the upstream LLM
    # stream instead of only at iteration boundaries.  Any reasoning route
    # that streams for several minutes per round (extended thinking on
    # Anthropic / Moonshot / future providers) would otherwise freeze the
    # UI for the whole window before flushing the backlog in one burst.
    loop_result_holder: list[Any] = [None]
    loop_task: asyncio.Task[None] | None = None
    # Length of ``state.assistant_text`` at the end of the last non-final
    # yield — used as an anchor by the budget-exhausted fallback to check
    # whether the *terminal* round produced any visible text, not the whole
    # turn. Without this, earlier-round chatter would suppress a fallback
    # that should fire.
    text_len_before_final_round: list[int] = [0]

    async def _run_tool_call_loop() -> None:
        # Read/write the current session via ``_session_holder`` so this
        # closure doesn't need to ``nonlocal session`` — pyright can't narrow
        # the outer ``session: ChatSession | None`` through a nested scope,
        # but the holder is typed non-optional after the preflight guard
        # above.
        try:
            max_tool_rounds = config.agent_max_turns
            async for loop_result in tool_call_loop(
                messages=openai_messages,
                tools=tools,
                llm_call=_bound_llm_caller,
                execute_tool=_bound_tool_executor,
                update_conversation=_bound_conversation_updater,
                max_iterations=max_tool_rounds,
                last_iteration_message=_LAST_ITERATION_HINT,
                runs_alone=writes_files,
            ):
                loop_result_holder[0] = loop_result
                # Engine switch requested (enter_agent_building_mode): end
                # the loop at this iteration boundary — the round's tool
                # results are already folded into the conversation, so the
                # tool-call rows persist and the processor's derived
                # building-mode signal holds. The processor dispatches the
                # SDK continuation turn after the slot is released.
                if engine_switch.is_pending(session_id):
                    logger.info(
                        f"[Baseline] Engine switch pending for "
                        f"{session_id[:12]} — ending tool loop"
                    )
                    break
                # Inject any messages the user queued while the turn was
                # running.  ``tool_call_loop`` mutates ``openai_messages``
                # in-place, so appending here means the model sees the new
                # messages on its next LLM call.
                #
                # IMPORTANT: skip when the loop has already finished (no
                # more LLM calls are coming).  ``tool_call_loop`` yields
                # a final ``ToolCallLoopResult`` on both paths:
                #   - natural finish: ``finished_naturally=True``
                #   - hit max_iterations: ``finished_naturally=False``
                #                         and ``iterations >= max_iterations``
                # In either case the loop is about to return on the next
                # ``async for`` step, so draining here would silently
                # lose the message (the user sees 202 but the model never
                # reads the text).  Those messages stay in the buffer and
                # get picked up at the start of the next turn.
                is_final_yield = (
                    loop_result.finished_naturally
                    or loop_result.iterations >= max_tool_rounds
                )
                if is_final_yield:
                    continue
                # Non-final yield: the next round may be the last one, so
                # record where ``assistant_text`` ends now.  If that next
                # round hits the budget without adding any text, the outer
                # fallback uses this anchor to detect a silent finish.
                text_len_before_final_round[0] = len(state.assistant_text)
                try:
                    pending = await drain_pending_messages(session_id)
                except Exception:
                    logger.warning(
                        "[Baseline] mid-loop drain_pending_messages failed for "
                        "session %s",
                        session_id,
                        exc_info=True,
                    )
                    pending = []
                if pending:
                    # Flush any buffered assistant/tool messages from completed
                    # rounds into session.messages BEFORE appending the pending
                    # user message.  ``_baseline_conversation_updater`` only
                    # records assistant+tool rounds into ``state.session_messages``
                    # — they are normally batch-flushed in the finally block.
                    # Without this in-order flush, the mid-loop pending user
                    # message lands before the preceding round's assistant/tool
                    # entries, producing chronologically-wrong session.messages
                    # on persist (user interposed between an assistant tool_call
                    # and its tool-result), which breaks OpenAI tool-call ordering
                    # invariants on the next turn's replay.
                    #
                    # Also persist any assistant text from text-only rounds (rounds
                    # with no tool calls, which ``_baseline_conversation_updater``
                    # does NOT record in session_messages).  If we only update
                    # ``_flushed_assistant_text_len`` without persisting the text,
                    # that text is silently lost: the finally block only appends
                    # assistant_text[_flushed_assistant_text_len:], so text generated
                    # before this drain never reaches session.messages.
                    recorded_text = "".join(
                        m.content or ""
                        for m in state.session_messages
                        if m.role == "assistant"
                    )
                    unflushed_text = state.assistant_text[
                        state._flushed_assistant_text_len :
                    ]
                    text_only_text = (
                        unflushed_text[len(recorded_text) :]
                        if unflushed_text.startswith(recorded_text)
                        else unflushed_text
                    )
                    current_session = _session_holder[0]
                    if text_only_text.strip():
                        current_session.messages.append(
                            ChatMessage(
                                role="assistant",
                                content=text_only_text,
                                model=state.model,
                                routing_source=state.routing_source,
                                llm_auth_provider=state.llm_auth_provider,
                                llm_credential_id=state.llm_credential_id,
                            )
                        )
                    for _buffered in state.session_messages:
                        current_session.messages.append(_buffered)
                    state.session_messages.clear()
                    # Record how much assistant_text has been covered by the
                    # structured entries just flushed, so the finally block's
                    # final-text dedup doesn't re-append rounds already persisted.
                    state._flushed_assistant_text_len = len(state.assistant_text)

                    # Persist the assistant/tool flush BEFORE the pending append
                    # so a later pending-persist failure can roll back the
                    # pending rows without also discarding LLM output.
                    stamp_turn_trace_id(current_session.messages, turn_start)
                    current_session = await persist_session_safe(
                        current_session, "[Baseline]"
                    )
                    # ``upsert_chat_session`` may return a *new* ``ChatSession``
                    # instance (e.g. when a concurrent title update has written a
                    # newer title to Redis, it returns ``session.model_copy``).
                    # Keep ``_session_holder`` in sync so subsequent tool rounds
                    # executed via ``_bound_tool_executor`` see the fresh session
                    # — any tool-side mutations on the stale object would be
                    # discarded when the new one is persisted in the ``finally``.
                    _session_holder[0] = current_session

                    # Image blocks go only to the model; persisted chat rows
                    # keep the same formatted text and attachment/context hints.
                    # Rollback trims the model messages to the recorded anchor.
                    formatted_by_pm = {
                        id(pm): format_pending_as_user_message(pm) for pm in pending
                    }

                    def _formatted(pm: PendingMessage) -> str:
                        return pending_message_text(pm)

                    _openai_anchor = len(openai_messages)
                    for pm in pending:
                        openai_messages.append(formatted_by_pm[id(pm)])

                    def _trim_openai_on_rollback(_session_anchor: int) -> None:
                        del openai_messages[_openai_anchor:]

                    if await persist_pending_as_user_rows(
                        current_session,
                        transcript_builder,
                        pending,
                        log_prefix="[Baseline]",
                        content_of=_formatted,
                        on_rollback=_trim_openai_on_rollback,
                    ):
                        matched_skills_notice = await build_matched_skills_notice(
                            user_id,
                            current_session,
                            "\n\n".join(pm.content for pm in pending),
                            permissions=permissions,
                        )
                        if matched_skills_notice:
                            _add_message_text(
                                openai_messages[-1],
                                matched_skills_notice,
                                prepend=False,
                            )
                        _emit(state, drained_rows_entry(pending, _formatted))
                    checkpoint = turn_checkpoint(current_session.messages, turn_start)
                    if checkpoint is not None:
                        _emit(state, checkpoint)
        finally:
            # Always post the sentinel so the outer consumer exits — even if
            # ``tool_call_loop`` raised.  ``_baseline_llm_caller``'s own
            # finally block has already pushed ``StreamReasoningEnd`` /
            # ``StreamTextEnd`` / ``StreamFinishStep`` at this point, so the
            # sentinel only terminates the consumer; it does not suppress
            # any still-unflushed events.
            state.pending_events.put_nowait(None)

    loop_task = asyncio.create_task(_run_tool_call_loop())
    try:
        while True:
            evt = await state.pending_events.get()
            if evt is None:
                break
            yield evt
        # Sentinel received — surface any exception the inner task hit.
        await loop_task
        loop_result = loop_result_holder[0]
        # Budget was reached when iterations hit the configured cap. This
        # covers both exit paths out of ``tool_call_loop``:
        #   - ``finished_naturally=True``: the last iteration ran with
        #     ``tools=[]`` and the model returned text (may be empty)
        #   - ``finished_naturally=False``: a non-compliant model still
        #     emitted tool calls despite the empty tool list, so the loop
        #     fell through the ``while`` guard
        # Either way, we check the terminal round's text contribution — an
        # empty one means the user got no explanation and we need to emit
        # the fallback notice.
        budget_reached = bool(
            loop_result and loop_result.iterations >= config.agent_max_turns
        )
        if budget_reached:
            if loop_result and not loop_result.finished_naturally:
                logger.warning(
                    "[Baseline] Hit %d-round tool budget without natural finish; "
                    "ending turn gracefully",
                    loop_result.iterations,
                )
            terminal_round_text = state.assistant_text[text_len_before_final_round[0] :]
            fallback_events, fallback_text = _build_budget_exhausted_fallback_events(
                terminal_round_text
            )
            for evt in fallback_events:
                yield evt
            state.assistant_text += fallback_text
        elif loop_result and loop_result.finished_naturally:
            # mirrors SDK's empty_completion surface — prevents blank bubble on thinking-only turns
            terminal_round_text = state.assistant_text[text_len_before_final_round[0] :]
            fallback_events, fallback_text = (
                _build_natural_finish_empty_fallback_events(terminal_round_text)
            )
            if fallback_events:
                logger.warning(
                    "[Baseline] Natural finish with empty terminal round (%d "
                    "iterations); surfacing fallback notice",
                    loop_result.iterations,
                )
            for evt in fallback_events:
                yield evt
            state.assistant_text += fallback_text
    except Exception as e:
        _stream_error = True
        out_of_credits = _is_platform_out_of_credits(
            e, session.metadata.llm_auth_provider if session else None
        )
        error_msg = (
            PROVIDER_UNAVAILABLE_MESSAGE
            if out_of_credits
            else _humanize_baseline_error(e)
        )
        logger.error("[Baseline] Streaming error: %s", error_msg, exc_info=True)
        if out_of_credits:
            report_provider_out_of_credits(
                provider=config.effective_transport,
                model=active_model,
                surface="copilot_baseline",
                error=e,
                session_id=session_id,
                user_id=user_id,
            )
        # Drain any queued tail events (reasoning/text close + finish step)
        # that ``_baseline_llm_caller``'s finally block pushed before the
        # sentinel arrived — without this the frontend would be missing the
        # matching end / finish parts for the partial round.
        while not state.pending_events.empty():
            evt = state.pending_events.get_nowait()
            if evt is not None:
                yield evt
        # An empty platform account is not the user's limit, so it must not
        # reach the "switch connection / wait for your limit" envelope.
        failure = (
            None
            if out_of_credits
            else classify_provider_failure(
                e,
                auth_provider=(session.metadata.llm_auth_provider if session else None),
                credential_id=session.metadata.llm_credential_id if session else None,
                message=error_msg,
            )
        )
        # Written before the error is yielded, and deliberately not in
        # ``finally``. The consumer breaks out of its loop the moment it sees
        # a StreamError and closes this generator, so the tail of ``finally``
        # -- including its session upsert -- is cut short at the first await.
        # A marker appended there is built correctly and then thrown away.
        #
        # ``_session_holder[0]`` rather than ``session``: the inner task can
        # replace the binding mid-turn, and the marker has to land on the
        # object that is actually current.
        _failed_session = _session_holder[0]
        if append_error_marker(
            _failed_session,
            error_msg,
            retryable=failure.retryable if failure is not None else True,
            failure=failure.as_part() if failure is not None else None,
        ):
            stamp_turn_trace_id(_failed_session.messages, turn_start)
            try:
                await upsert_chat_session(_failed_session)
            except Exception as marker_err:
                logger.error(
                    "[Baseline] Failed to persist the error marker: %s", marker_err
                )
        if failure is not None:
            # Before the error, so a client that acts on the envelope has it
            # in hand by the time the turn is reported failed.
            yield StreamProviderFailure(failure=failure.as_part())
            yield StreamError(errorText=error_msg, code=failure.kind.value)
        elif out_of_credits:
            yield StreamError(errorText=error_msg, code=PROVIDER_UNAVAILABLE_CODE)
        else:
            yield StreamError(errorText=error_msg, code="baseline_error")
    finally:
        # Cancel the inner task if we're unwinding early (client disconnect,
        # unexpected error in the consumer) so it doesn't keep streaming
        # tokens into a dead queue.
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
            try:
                await loop_task
            except (asyncio.CancelledError, Exception):
                pass
        # Re-sync the outer ``session`` binding in case the inner task
        # reassigned it via a mid-loop ``persist_session_safe`` call.
        session = _session_holder[0]

        # In-flight tool-call announcements are only meaningful for the
        # current turn; clear at the top of the outer finally so the next
        # turn starts with a clean scratch buffer even if one of the
        # awaited cleanup steps below (usage persistence, session upsert,
        # transcript upload) raises.  The buffer is a process-local scratch
        # set — if we leak it into the next turn the guide-read guard would
        # observe a phantom in-flight call and skip its gate, so this must
        # run unconditionally.
        session.clear_inflight_tool_calls()
        state.tool_persistence.finish(state.session_messages)

        # --- Pause E2B sandbox to stop billing between turns (parity with
        # the SDK path). Fire-and-forget: best-effort and must not block the
        # cleanup below. An expert's box is left running while another of
        # its turns is still active.
        if e2b_sandbox is not None:
            _pause_counted_box(e2b_sandbox, session_id, session.expert_id)

        # Pending messages are drained atomically at turn start and
        # between tool rounds, so there's nothing to clear in finally.
        # Any message pushed after the final drain window stays in the
        # buffer and gets picked up at the start of the next turn.

        # Fallback: estimate tokens via tiktoken when the provider does
        # not honour stream_options={"include_usage": True}.
        # ``openai_messages`` is mutated in-place by tool_call_loop so it
        # contains the full accumulated conversation (system + history + all
        # tool-call rounds) by this point — the estimate covers the complete
        # prompt, not just the final round.
        # Skip fallback when an error occurred and no output was produced —
        # charging rate-limit tokens for completely failed requests is unfair.
        if (
            state.turn_prompt_tokens == 0
            and state.turn_completion_tokens == 0
            and not (_stream_error and not state.assistant_text)
        ):
            state.turn_prompt_tokens = max(
                estimate_token_count(openai_messages, model=active_model), 1
            )
            state.turn_completion_tokens = estimate_token_count_str(
                state.assistant_text, model=active_model
            )
            logger.info(
                "[Baseline] No streaming usage reported; estimated tokens: "
                "prompt=%d, completion=%d",
                state.turn_prompt_tokens,
                state.turn_completion_tokens,
            )
        # Safety net: recover cost from rate card if usage chunk was dropped
        # (truncated SSE). OR mode skips recovery — OR's markup differs from
        # raw Anthropic pricing. Local Ollama/vLLM never emit ``usage.cost``
        # *and* have no rate card to recover from (``compute_anthropic_cost_usd``
        # returns None for any non-Anthropic slug), so cost stays None for
        # the whole turn — fine, since local deployments are self-hosted and
        # ``persist_and_record_usage`` no-ops the cost-credit charge when
        # ``cost_usd`` is None. Skip the rate-card call explicitly under
        # local transport so the intent is clear.
        baseline_provider = config.baseline_provider
        if state.cost_usd is None and baseline_provider == "anthropic":
            recovered = compute_anthropic_cost_usd(
                model=active_model,
                prompt_tokens=state.turn_prompt_tokens,
                completion_tokens=state.turn_completion_tokens,
                cache_read_tokens=state.turn_cache_read_tokens,
                cache_creation_tokens=state.turn_cache_creation_tokens,
                cache_ttl=config.baseline_prompt_cache_ttl,
            )
            if recovered is not None:
                state.cost_usd = recovered

        # OAI-compat prompt_tokens includes cache buckets; subtract to keep the three buckets disjoint.
        uncached_prompt = max(
            0,
            state.turn_prompt_tokens
            - state.turn_cache_read_tokens
            - state.turn_cache_creation_tokens,
        )
        await persist_and_record_usage(
            session=session,
            user_id=user_id,
            prompt_tokens=uncached_prompt,
            completion_tokens=state.turn_completion_tokens,
            cache_read_tokens=state.turn_cache_read_tokens,
            cache_creation_tokens=state.turn_cache_creation_tokens,
            log_prefix="[Baseline]",
            cost_usd=state.cost_usd,
            model=active_model,
            provider=(
                "open_router"
                if baseline_provider == "openrouter"
                else config.transport.cost_log_provider
            ),
        )

        # Persist structured tool-call history (assistant + tool messages)
        # collected by the conversation updater, then the final text response.
        for msg in state.session_messages:
            session.messages.append(msg)
        # Append the final assistant text (from the last LLM call that had
        # no tool calls, i.e. the natural finish).  Only add it if the
        # conversation updater didn't already record it as part of a
        # tool-call round (which would have empty response_text).
        # Only consider assistant text produced AFTER the last mid-loop
        # flush.  ``_flushed_assistant_text_len`` tracks the prefix already
        # persisted via structured session_messages during mid-loop pending
        # drains; including it here would duplicate those rounds.
        final_text = state.assistant_text[state._flushed_assistant_text_len :]
        if state.session_messages:
            # Strip text already captured in tool-call round messages
            recorded = "".join(
                m.content or "" for m in state.session_messages if m.role == "assistant"
            )
            if final_text.startswith(recorded):
                final_text = final_text[len(recorded) :]
        if final_text.strip():
            session.messages.append(
                ChatMessage(
                    role="assistant",
                    content=final_text,
                    model=state.model,
                    routing_source=state.routing_source,
                    llm_auth_provider=state.llm_auth_provider,
                    llm_credential_id=state.llm_credential_id,
                )
            )
        stamp_turn_trace_id(session.messages, turn_start)
        try:
            await upsert_chat_session(session)
            final_checkpoint = turn_checkpoint(session.messages, turn_start)
        except Exception as persist_err:
            logger.error("[Baseline] Failed to persist session: %s", persist_err)

        # --- Graphiti: ingest conversation turn for temporal memory ---
        if graphiti_enabled and user_id and message and is_user_message:
            # Pass only the final assistant reply (after stripping tool-loop
            # chatter) so derived-finding distillation sees the substantive
            # response, not intermediate tool-planning text.
            _ingest_task = asyncio.create_task(
                _enqueue_graphiti_turn(
                    user_id,
                    session,
                    session_id,
                    message,
                    final_text if state else "",
                )
            )
            _background_tasks.add(_ingest_task)
            _ingest_task.add_done_callback(_background_tasks.discard)

        # --- Skill learning: record this turn as a learning source ---
        # Independent of Graphiti; honors the user's learning preferences.
        if user_id and message and is_user_message:
            _capture_task = asyncio.create_task(
                capture_chat_turn(
                    user_id,
                    session,
                    list(session.messages[turn_start:]),
                    message,
                    initiating_user=learning_initiating_user,
                    initiating_text=learning_initiating_text,
                )
            )
            _background_tasks.add(_capture_task)
            _capture_task.add_done_callback(_background_tasks.discard)

        # --- Upload transcript for next-turn continuity ---
        # Backfill partial assistant text that wasn't recorded by the
        # conversation updater (e.g. when the stream aborted mid-round).
        # Without this, mode-switching after a failed turn would lose
        # the partial assistant response from the transcript.
        if _stream_error and state.assistant_text:
            if transcript_builder.last_entry_type != "assistant":
                transcript_builder.append_assistant(
                    content_blocks=[{"type": "text", "text": state.assistant_text}],
                    model=active_model,
                    stop_reason=STOP_REASON_END_TURN,
                )

        if user_id and should_upload_transcript(user_id, transcript_upload_safe):
            # Watermark = the next uncovered DB sequence (= max non-reasoning
            # sequence + 1).  Stays accurate on cap-engaged sessions because
            # the loaded window always includes the most-recent rows; the
            # max(sequence) is the last assistant just appended this turn.
            await _upload_final_transcript(
                user_id=user_id,
                session_id=session_id,
                transcript_builder=transcript_builder,
                session_msg_count=next_uncovered_sequence(session.messages),
            )

        # Clean up the ephemeral working directory used for file attachments.
        if working_dir is not None:
            shutil.rmtree(working_dir, ignore_errors=True)

    # Yield usage and finish AFTER try/finally (not inside finally).
    # PEP 525 prohibits yielding from finally in async generators during
    # aclose() — doing so raises RuntimeError on client disconnect.
    # On GeneratorExit the client is already gone, so unreachable yields
    # are harmless; on normal completion they reach the SSE stream.
    if final_checkpoint is not None:
        yield final_checkpoint
    if state.turn_prompt_tokens > 0 or state.turn_completion_tokens > 0:
        # Report uncached prompt tokens to match what was billed — both
        # cache_read and cache_creation are excluded so the three
        # buckets emitted on ``StreamUsage`` are disjoint and the
        # frontend can sum them without double-counting cache writes.
        billed_prompt = max(
            0,
            state.turn_prompt_tokens
            - state.turn_cache_read_tokens
            - state.turn_cache_creation_tokens,
        )
        yield StreamUsage(
            prompt_tokens=billed_prompt,
            completion_tokens=state.turn_completion_tokens,
            total_tokens=billed_prompt + state.turn_completion_tokens,
            cache_read_tokens=state.turn_cache_read_tokens,
            cache_creation_tokens=state.turn_cache_creation_tokens,
        )

    for event in _engine_switch_finish_events(session_id):
        yield event
    yield StreamFinish()


def _engine_switch_finish_events(session_id: str) -> "list[StreamBaseResponse]":
    """Terminal events for a turn that registered an engine switch.

    Emitted right before StreamFinish so the frontend learns the engine
    changed (StreamModeChanged) and narrates the handoff (StreamStatus)
    exactly once, at the turn boundary where the baseline loop stopped.
    There is no mode picker to flip any more — the client uses this only to
    widen its post-finish refetch window, since a switch takes longer to
    settle.
    """
    if not engine_switch.is_pending(session_id):
        return []
    return [
        StreamModeChanged(mode="extended_thinking"),
        StreamStatus(message="Switching engines for agent building…"),
    ]

```

## autogpt_platform/backend/backend/copilot/sdk/e2b_file_tools.py

Source: https://github.com/Significant-Gravitas/AutoGPT/blob/13ac38c5b6583bd01b0a6142b0060fc03e102b6e/autogpt_platform/backend/backend/copilot/sdk/e2b_file_tools.py

SHA-256: `834546989b50c73e9b60ea92f1f6543c987626cdfc84e2b614551653de6a068a`

```
"""Unified MCP file-tool handlers for both E2B (sandbox) and non-E2B (local) modes.

When E2B is active, Read/Write/Edit/Glob/Grep route to the sandbox so that
all file operations share the same ``/home/user`` and ``/tmp`` filesystems
as ``bash_exec``.

In non-E2B mode (no sandbox), Read/Write/Edit operate on the SDK working
directory (``/tmp/copilot-<session>/``), providing the same truncation
detection and path-validation guarantees.

SDK-internal paths (``~/.claude/projects/…/tool-results/``) are handled
by the separate ``Read`` MCP tool registered in ``tool_adapter.py``.
"""

import asyncio
import base64
import collections
import functools
import hashlib
import itertools
import json
import logging
import os
import shlex
from typing import Any, Callable

from backend.copilot.context import (
    E2B_ALLOWED_DIRS,
    E2B_ALLOWED_DIRS_STR,
    E2B_WORKDIR,
    get_current_sandbox,
    get_sdk_cwd,
    is_allowed_local_path,
    is_sdk_tool_path,
    is_within_allowed_dirs,
    resolve_allowed_local_path,
    resolve_sandbox_path,
)
from backend.copilot.host_fs import open_no_follow
from backend.copilot.sdk.sandbox_image_read import read_sandbox_image
from backend.copilot.tool_image import (
    BINARY_IMAGE_READ_GUIDANCE,
    IMAGE_EXTENSIONS,
    IMAGE_READ_DESCRIPTION,
    IMAGE_TOO_LARGE_MESSAGE,
    MAX_IMAGE_BYTES,
    ImageReadError,
    prepare_image,
)
from backend.util.sandbox_login import run_internal
from backend.util.sandbox_output import read_sandbox_file
from backend.util.settings import Config

logger = logging.getLogger(__name__)

# Default number of lines returned by ``read_file`` when the caller does not
# specify a limit.  Also used as the threshold in ``bridge_to_sandbox`` to
# decide whether the model is requesting the full file (and thus whether the
# bridge copy is worthwhile).
_DEFAULT_READ_LIMIT = 2000

# Per-path lock for edit operations to prevent parallel lost updates.
# When MCP tools are dispatched in parallel (readOnlyHint=True annotation),
# two Edit calls on the same file could race through read-modify-write
# and silently drop one change.  Keyed by resolved absolute path.
# Bounded to _EDIT_LOCKS_MAX entries (LRU eviction) to prevent unbounded
# memory growth across long-running server processes.
_EDIT_LOCKS_MAX = 1_000
_edit_locks: collections.OrderedDict[str, asyncio.Lock] = collections.OrderedDict()

# Inline content above this threshold triggers a warning — it survived this
# time but is dangerously close to the API output-token truncation limit.
_LARGE_CONTENT_WARN_CHARS = 50_000

_READ_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".bz2",
        ".xz",
        ".7z",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".o",
        ".a",
        ".pyc",
        ".pyo",
        ".class",
        ".wasm",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".wav",
        ".flac",
        ".sqlite",
        ".db",
    }
)


def _is_likely_binary(path: str) -> bool:
    """Heuristic check for binary files by extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in _READ_BINARY_EXTENSIONS


def _binary_read_guidance(path: str) -> str:
    if os.path.splitext(path)[1].lower() in {".pdf", ".mp4", ".avi", ".mov", ".mkv"}:
        return BINARY_IMAGE_READ_GUIDANCE
    return (
        "This binary file has not been viewed. "
        "Use bash_exec with 'file' or 'xxd' to inspect it."
    )


# Read and Edit hold a file whole; past this they refuse it.
_MAX_READ_BYTES = Config().max_file_size_mb * 1024 * 1024


def _read_capped_text(path: str) -> str | None:
    """A host file as text; ``None`` past ``_MAX_READ_BYTES``."""
    with open(path, encoding="utf-8", opener=open_no_follow) as fh:
        if os.fstat(fh.fileno()).st_size > _MAX_READ_BYTES:
            return None
        text = fh.read(_MAX_READ_BYTES + 1)
    return None if len(text) > _MAX_READ_BYTES else text


def _read_capped_lines(path: str, offset: int, limit: int) -> list[str] | None:
    """Lines ``offset`` to ``offset + limit`` of a host file, reading no further;
    ``None`` past ``_MAX_READ_BYTES``."""
    with open(path, encoding="utf-8", errors="replace", opener=open_no_follow) as fh:
        if os.fstat(fh.fileno()).st_size > _MAX_READ_BYTES:
            return None
        budget = _MAX_READ_BYTES
        selected: list[str] = []
        for index in range(offset + limit):
            line = fh.readline(budget + 1)
            if not line:
                break
            budget -= len(line)
            if budget < 0:
                return None
            if index >= offset:
                selected.append(line)
    return selected


def _too_large(path: str, done: str) -> str:
    return (
        f"{os.path.basename(path)} is larger than {_MAX_READ_BYTES // 2**20} MB "
        f"and cannot be {done} whole. Use bash_exec with head, tail, "
        "sed -n or split to work on parts of it."
    )


_PARTIAL_TRUNCATION_MSG = (
    "Your Write call was truncated (file_path missing but content "
    "was present). The content was too large for a single tool call. "
    "Write in chunks: use bash_exec with "
    "'cat > file << \"EOF\"\\n...\\nEOF' for the first section, "
    "'cat >> file << \"EOF\"\\n...\\nEOF' to append subsequent "
    "sections, then reference the file with "
    "@@agptfile:/path/to/file if needed."
)

_COMPLETE_TRUNCATION_MSG = (
    "Your Write call had empty arguments — this means your previous "
    "response was too long and the tool call was truncated by the API. "
    "Break your work into smaller steps. For large content, write "
    "section-by-section using bash_exec with "
    "'cat > file << \"EOF\"\\n...\\nEOF' and "
    "'cat >> file << \"EOF\"\\n...\\nEOF'."
)

_EDIT_PARTIAL_TRUNCATION_MSG = (
    "Your Edit call was truncated (file_path missing but old_string/new_string "
    "were present). The arguments were too large for a single tool call. "
    "Break your edit into smaller replacements, or use bash_exec with "
    "'sed' for large-scale find-and-replace."
)


def _check_truncation(file_path: str, content: str) -> dict[str, Any] | None:
    """Return an error response if the args look truncated, else ``None``."""
    if not file_path:
        if content:
            return _mcp(_PARTIAL_TRUNCATION_MSG, error=True)
        return _mcp(_COMPLETE_TRUNCATION_MSG, error=True)
    return None


def _resolve_and_validate(
    file_path: str, sdk_cwd: str
) -> tuple[str, None] | tuple[None, dict[str, Any]]:
    """Resolve *file_path* against *sdk_cwd* and validate it stays within bounds.

    Returns ``(resolved_path, None)`` on success, or ``(None, error_response)``
    on failure.  Open the result with ``open_no_follow``.
    """
    resolved = resolve_allowed_local_path(file_path, sdk_cwd)
    if resolved is None:
        return None, _mcp(
            f"Path must be within the working directory: {os.path.basename(file_path)}",
            error=True,
        )
    return resolved, None


async def _check_sandbox_symlink_escape(
    sandbox: Any,
    parent: str,
) -> str | None:
    """Resolve the canonical parent path inside the sandbox to detect symlink escapes.

    ``normpath`` (used by ``resolve_sandbox_path``) only normalises the string;
    ``readlink -f`` follows actual symlinks on the sandbox filesystem.

    Returns the canonical parent path, or ``None`` if the path escapes
    the allowed sandbox directories.

    Note: There is an inherent TOCTOU window between this check and the
    subsequent ``sandbox.files.write()``.  A symlink could theoretically be
    replaced between the two operations.  This is acceptable in the E2B
    sandbox model since the sandbox is single-user and ephemeral.
    """
    canonical_res = await run_internal(
        sandbox,
        f"readlink -f {shlex.quote(parent or E2B_WORKDIR)}",
        cwd=E2B_WORKDIR,
        timeout=5,
    )
    canonical_parent = (canonical_res.stdout or "").strip()
    if (
        canonical_res.exit_code != 0
        or not canonical_parent
        or not is_within_allowed_dirs(canonical_parent)
    ):
        return None
    return canonical_parent


def _get_sandbox():
    return get_current_sandbox()


def _is_allowed_local(path: str) -> bool:
    return is_allowed_local_path(path, get_sdk_cwd())


def _mcp(
    text: str, *, error: bool = False, outside: tuple[str, ...] | None = None
) -> dict[str, Any]:
    if error:
        text = json.dumps({"error": text, "type": "error"})
    result = {"content": [{"type": "text", "text": text}], "isError": error}
    return result if outside is None else DeclaredResult(result, outside=outside)


class DeclaredResult(dict[str, Any]):
    """An MCP result whose ``outside`` names what in it came from outside
    AutoGPT (see ``ToolResponseBase.from_outside``). The wrapper reads it
    before the cap, which builds a plain dict."""

    def __init__(self, result: dict[str, Any], *, outside: tuple[str, ...]):
        super().__init__(result)
        self.outside = outside


def _get_sandbox_and_path(
    file_path: str,
) -> tuple[Any, str] | dict[str, Any]:
    """Common preamble: get sandbox + resolve path, or return MCP error."""
    sandbox = _get_sandbox()
    if sandbox is None:
        return _mcp("No E2B sandbox available", error=True)
    try:
        remote = resolve_sandbox_path(file_path)
    except ValueError as exc:
        return _mcp(str(exc), error=True)
    return sandbox, remote


async def _sandbox_write(sandbox: Any, path: str, content: str | bytes) -> None:
    """Write *content* to *path* inside the sandbox.

    The E2B filesystem API (``sandbox.files.write``) and the command API
    (``sandbox.commands.run``) run as **different users**.  On ``/tmp``
    (which has the sticky bit set) this means ``sandbox.files.write`` can
    create new files but cannot overwrite files previously created by
    ``sandbox.commands.run`` (or itself), because the sticky bit restricts
    deletion/rename to the file owner.

    To work around this, writes targeting ``/tmp`` are performed via
    ``tee`` through the command API, which runs as the sandbox ``user``
    and can therefore always overwrite user-owned files.

    *content* may be ``str`` (text) or ``bytes`` (binary).  Both paths
    are handled correctly: text is encoded to bytes for the base64 shell
    pipe, and raw bytes are passed through without any encoding.
    """
    if path == "/tmp" or path.startswith("/tmp/"):
        raw = content.encode() if isinstance(content, str) else content
        encoded = base64.b64encode(raw).decode()
        result = await run_internal(
            sandbox,
            f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}",
            cwd=E2B_WORKDIR,
            timeout=10,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"shell write failed (exit {result.exit_code}): "
                + (result.stderr or "").strip()
            )
    else:
        await sandbox.files.write(path, content)


# Tool handlers


async def _handle_read_file(args: dict[str, Any]) -> dict[str, Any]:
    """Read lines from a file — E2B sandbox, local SDK working dir, or SDK-internal paths."""
    if not args:
        return _mcp(
            "Your read_file call had empty arguments \u2014 this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    file_path: str = args.get("file_path", "")
    try:
        offset: int = max(0, int(args.get("offset", 0)))
        limit: int = max(1, int(args.get("limit", _DEFAULT_READ_LIMIT)))
    except (ValueError, TypeError):
        return _mcp("Invalid offset/limit \u2014 must be integers.", error=True)

    if not file_path:
        if "offset" in args or "limit" in args:
            return _mcp(
                "Your read_file call was truncated (file_path missing but "
                "offset/limit were present). Resend with the full file_path.",
                error=True,
            )
        return _mcp("file_path is required", error=True)

    # SDK-internal tool-results/tool-outputs paths are on the host filesystem in
    # both E2B and non-E2B mode — always read them locally.
    # When E2B is active, also copy the file into the sandbox so bash_exec can
    # process it further.
    # NOTE: when E2B is active we intentionally use `is_sdk_tool_path` (not
    # `_is_allowed_local`) so that sdk_cwd-relative paths (e.g. "output.txt")
    # are NOT captured here.  In E2B mode the agent's working directory is the
    # sandbox, not sdk_cwd on the host, so relative paths should be read from
    # the sandbox below.
    sandbox_active = _get_sandbox() is not None
    local_check = (
        is_sdk_tool_path(file_path) if sandbox_active else _is_allowed_local(file_path)
    )
    if local_check:
        result = await asyncio.to_thread(
            _read_local, file_path, offset, limit if "limit" in args else None
        )
        if not result.get("isError"):
            sandbox = _get_sandbox()
            if sandbox is not None:
                annotation = await bridge_and_annotate(
                    sandbox, file_path, offset, limit
                )
                if annotation:
                    result["content"][0]["text"] += annotation
        return result

    sandbox = _get_sandbox()
    if sandbox is not None:
        # E2B path — read from sandbox filesystem
        result = _get_sandbox_and_path(file_path)
        if isinstance(result, dict):
            return result
        sandbox, remote = result

        try:
            ext = os.path.splitext(remote)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                info = await sandbox.files.get_info(remote)
                if info.size > MAX_IMAGE_BYTES:
                    return _mcp(
                        IMAGE_TOO_LARGE_MESSAGE,
                        error=True,
                        outside=(),
                    )
            elif _is_likely_binary(remote):
                return _mcp(
                    _binary_read_guidance(remote),
                    error=True,
                    outside=(),
                )
            if ext in IMAGE_EXTENSIONS:
                raw = await read_sandbox_image(sandbox, remote)
                return await asyncio.to_thread(
                    _image_result, raw, offset, args.get("limit")
                )
            raw = await read_sandbox_file(sandbox, remote, _MAX_READ_BYTES)
            content = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            return _mcp(f"Failed to read {os.path.basename(remote)}: {exc}", error=True)
        if len(raw) > _MAX_READ_BYTES:
            return _mcp(_too_large(remote, "read"), error=True)

        lines = content.splitlines(keepends=True)
        selected = list(itertools.islice(lines, offset, offset + limit))
        numbered = "".join(
            f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)
        )
        return _mcp(numbered, outside=(numbered,))

    # Non-E2B path — read from SDK working directory
    sdk_cwd = get_sdk_cwd()
    if not sdk_cwd:
        return _mcp("No SDK working directory available", error=True)

    resolved, err = _resolve_and_validate(file_path, sdk_cwd)
    if err is not None:
        return err
    assert resolved is not None

    if os.path.splitext(resolved)[1].lower() in IMAGE_EXTENSIONS:
        return await asyncio.to_thread(
            _read_local_image, resolved, offset, args.get("limit")
        )

    if _is_likely_binary(resolved):
        return _mcp(
            _binary_read_guidance(resolved),
            error=True,
            outside=(),
        )

    try:
        selected = _read_capped_lines(resolved, offset, limit)
    except FileNotFoundError:
        return _mcp(f"File not found: {file_path}", error=True)
    except PermissionError:
        return _mcp(f"Permission denied: {file_path}", error=True)
    except Exception as exc:
        return _mcp(f"Failed to read {file_path}: {exc}", error=True)
    if selected is None:
        return _mcp(_too_large(resolved, "read"), error=True)

    numbered = "".join(
        f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)
    )
    return _mcp(numbered, outside=(numbered,))


async def _handle_write_file(args: dict[str, Any]) -> dict[str, Any]:
    """Write content to a file — E2B sandbox or local SDK working directory."""
    if not args:
        return _mcp(_COMPLETE_TRUNCATION_MSG, error=True)
    file_path: str = args.get("file_path", "")
    content: str = args.get("content", "")

    truncation_err = _check_truncation(file_path, content)
    if truncation_err is not None:
        return truncation_err

    sandbox = _get_sandbox()
    if sandbox is not None:
        # E2B path — write to sandbox filesystem
        try:
            remote = resolve_sandbox_path(file_path)
        except ValueError as exc:
            return _mcp(str(exc), error=True)

        try:
            parent = os.path.dirname(remote)
            if parent and parent not in E2B_ALLOWED_DIRS:
                await sandbox.files.make_dir(parent)
            canonical_parent = await _check_sandbox_symlink_escape(sandbox, parent)
            if canonical_parent is None:
                return _mcp(
                    f"Path must be within {E2B_ALLOWED_DIRS_STR}: {os.path.basename(parent)}",
                    error=True,
                )
            remote = os.path.join(canonical_parent, os.path.basename(remote))
            await _sandbox_write(sandbox, remote, content)
        except Exception as exc:
            return _mcp(
                f"Failed to write {os.path.basename(remote)}: {exc}", error=True
            )

        msg = f"Successfully wrote to {file_path}"
        if len(content) > _LARGE_CONTENT_WARN_CHARS:
            logger.warning(
                "[Write] large inline content (%d chars) for %s",
                len(content),
                remote,
            )
            msg += (
                f"\n\nWARNING: The content was very large ({len(content)} chars). "
                "Next time, write large files in sections using bash_exec with "
                "'cat > file << EOF ... EOF' and 'cat >> file << EOF ... EOF' "
                "to avoid output-token truncation."
            )
        return _mcp(msg)

    # Non-E2B path — write to SDK working directory
    sdk_cwd = get_sdk_cwd()
    if not sdk_cwd:
        return _mcp("No SDK working directory available", error=True)

    resolved, err = _resolve_and_validate(file_path, sdk_cwd)
    if err is not None:
        return err
    assert resolved is not None

    try:
        with open(
            resolved,
            "w",
            encoding="utf-8",
            opener=functools.partial(open_no_follow, make_parents=True),
        ) as f:
            f.write(content)
    except Exception as exc:
        logger.error("Write failed for %s: %s", resolved, exc, exc_info=True)
        return _mcp(
            f"Failed to write {os.path.basename(resolved)}: {type(exc).__name__}",
            error=True,
        )

    msg = f"Successfully wrote to {file_path}"
    if len(content) > _LARGE_CONTENT_WARN_CHARS:
        logger.warning(
            "[Write] large inline content (%d chars) for %s",
            len(content),
            resolved,
        )
        msg += (
            f"\n\nWARNING: The content was very large ({len(content)} chars). "
            "Next time, write large files in sections using bash_exec with "
            "'cat > file << EOF ... EOF' and 'cat >> file << EOF ... EOF' "
            "to avoid output-token truncation."
        )
    return _mcp(msg)


async def _handle_edit_file(args: dict[str, Any]) -> dict[str, Any]:
    """Replace a substring in a file — E2B sandbox or local SDK working directory."""
    if not args:
        return _mcp(
            "Your Edit call had empty arguments \u2014 this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    file_path: str = args.get("file_path", "")
    old_string: str = args.get("old_string", "")
    new_string: str = args.get("new_string", "")
    replace_all: bool = args.get("replace_all", False)

    # Partial truncation: file_path missing but edit strings present
    if not file_path:
        if old_string or new_string:
            return _mcp(_EDIT_PARTIAL_TRUNCATION_MSG, error=True)
        return _mcp(
            "Your Edit call had empty arguments \u2014 this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )

    if not old_string:
        return _mcp("old_string is required", error=True)

    sandbox = _get_sandbox()
    if sandbox is not None:
        # E2B path — edit in sandbox filesystem
        try:
            remote = resolve_sandbox_path(file_path)
        except ValueError as exc:
            return _mcp(str(exc), error=True)

        parent = os.path.dirname(remote)
        try:
            canonical_parent = await _check_sandbox_symlink_escape(sandbox, parent)
        except Exception as exc:
            return _mcp(f"Failed to edit {os.path.basename(remote)}: {exc}", error=True)
        if canonical_parent is None:
            return _mcp(
                f"Path must be within {E2B_ALLOWED_DIRS_STR}: {os.path.basename(parent)}",
                error=True,
            )
        remote = os.path.join(canonical_parent, os.path.basename(remote))

        try:
            raw = await read_sandbox_file(sandbox, remote, _MAX_READ_BYTES)
            content = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            return _mcp(f"Failed to read {os.path.basename(remote)}: {exc}", error=True)
        if len(raw) > _MAX_READ_BYTES:
            return _mcp(_too_large(remote, "edited"), error=True)

        count = content.count(old_string)
        if count == 0:
            return _mcp(f"old_string not found in {file_path}", error=True)
        if count > 1 and not replace_all:
            return _mcp(
                f"old_string appears {count} times in {file_path}. "
                "Use replace_all=true or provide a more unique string.",
                error=True,
            )

        updated = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        try:
            await _sandbox_write(sandbox, remote, updated)
        except Exception as exc:
            return _mcp(
                f"Failed to write {os.path.basename(remote)}: {exc}", error=True
            )

        return _mcp(
            f"Edited {file_path} ({count} replacement{'s' if count > 1 else ''})"
        )

    # Non-E2B path — edit in SDK working directory
    sdk_cwd = get_sdk_cwd()
    if not sdk_cwd:
        return _mcp("No SDK working directory available", error=True)

    resolved, err = _resolve_and_validate(file_path, sdk_cwd)
    if err is not None:
        return err
    assert resolved is not None

    # Per-path lock prevents parallel edits from racing through
    # the read-modify-write cycle and silently dropping changes.
    # LRU-bounded: evict the oldest entry when the dict is full so that
    # _edit_locks does not grow unboundedly in long-running server processes.
    if resolved not in _edit_locks:
        if len(_edit_locks) >= _EDIT_LOCKS_MAX:
            _edit_locks.popitem(last=False)
        _edit_locks[resolved] = asyncio.Lock()
    else:
        _edit_locks.move_to_end(resolved)
    lock = _edit_locks[resolved]
    async with lock:
        try:
            content = _read_capped_text(resolved)
        except FileNotFoundError:
            return _mcp(f"File not found: {file_path}", error=True)
        except PermissionError:
            return _mcp(f"Permission denied: {file_path}", error=True)
        except Exception as exc:
            return _mcp(f"Failed to read {file_path}: {exc}", error=True)
        if content is None:
            return _mcp(_too_large(resolved, "edited"), error=True)

        count = content.count(old_string)
        if count == 0:
            return _mcp(f"old_string not found in {file_path}", error=True)
        if count > 1 and not replace_all:
            return _mcp(
                f"old_string appears {count} times in {file_path}. "
                "Use replace_all=true or provide a more unique string.",
                error=True,
            )

        updated = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )

        # Yield to the event loop between the read and write phases so other
        # coroutines waiting on this lock can be scheduled.  The lock above
        # ensures they cannot enter the critical section until we release it.
        await asyncio.sleep(0)

        try:
            with open(resolved, "w", encoding="utf-8", opener=open_no_follow) as f:
                f.write(updated)
        except Exception as exc:
            return _mcp(f"Failed to write {file_path}: {exc}", error=True)

    return _mcp(f"Edited {file_path} ({count} replacement{'s' if count > 1 else ''})")


async def _handle_glob(args: dict[str, Any]) -> dict[str, Any]:
    """Find files matching a name pattern inside the sandbox using ``find``."""
    if not args:
        return _mcp(
            "Your glob call had empty arguments \u2014 this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    pattern: str = args.get("pattern", "")
    path: str = args.get("path", "")

    if not pattern:
        return _mcp("pattern is required", error=True)

    sandbox = _get_sandbox()
    if sandbox is None:
        return _mcp("No E2B sandbox available", error=True)

    try:
        search_dir = resolve_sandbox_path(path) if path else E2B_WORKDIR
    except ValueError as exc:
        return _mcp(str(exc), error=True)

    cmd = f"find {shlex.quote(search_dir)} -name {shlex.quote(pattern)} -type f 2>/dev/null | head -500"
    try:
        result = await run_internal(sandbox, cmd, cwd=E2B_WORKDIR, timeout=10)
    except Exception as exc:
        return _mcp(f"Glob failed: {exc}", error=True)

    files = [line for line in (result.stdout or "").strip().splitlines() if line]
    return _mcp(json.dumps(files, indent=2), outside=tuple(files))


async def _handle_grep(args: dict[str, Any]) -> dict[str, Any]:
    """Search file contents by regex inside the sandbox using ``grep -rn``."""
    if not args:
        return _mcp(
            "Your grep call had empty arguments \u2014 this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    pattern: str = args.get("pattern", "")
    path: str = args.get("path", "")
    include: str = args.get("include", "")

    if not pattern:
        return _mcp("pattern is required", error=True)

    sandbox = _get_sandbox()
    if sandbox is None:
        return _mcp("No E2B sandbox available", error=True)

    try:
        search_dir = resolve_sandbox_path(path) if path else E2B_WORKDIR
    except ValueError as exc:
        return _mcp(str(exc), error=True)

    parts = ["grep", "-rn", "--color=never"]
    if include:
        parts.extend(["--include", include])
    parts.extend([pattern, search_dir])
    cmd = " ".join(shlex.quote(p) for p in parts) + " 2>/dev/null | head -200"

    try:
        result = await run_internal(sandbox, cmd, cwd=E2B_WORKDIR, timeout=15)
    except Exception as exc:
        return _mcp(f"Grep failed: {exc}", error=True)

    output = (result.stdout or "").strip()
    if not output:
        return _mcp("No matches found.", outside=())
    return _mcp(output, outside=(output,))


# Bridging: copy SDK-internal files into E2B sandbox

# Files larger than this are written to /home/user/ via sandbox.files.write()
# instead of /tmp/ via shell base64, to avoid shell argument length limits
# and E2B command timeouts.  Base64 expands content by ~33%, so keep this
# well under the typical Linux ARG_MAX (128 KB).
_BRIDGE_SHELL_MAX_BYTES = 32 * 1024  # 32 KB
# Files larger than this are skipped entirely to avoid excessive transfer times.
_BRIDGE_SKIP_BYTES = 50 * 1024 * 1024  # 50 MB


async def bridge_to_sandbox(
    sandbox: Any,
    file_path: str,
    offset: int,
    limit: int,
    content: str | None = None,
) -> str | None:
    """Best-effort copy of a host-side SDK file into the E2B sandbox.

    When the model reads an SDK-internal file (e.g. tool-results), it often
    wants to process the data with bash.  Copying the file into the sandbox
    under a stable name lets ``bash_exec`` access it without extra steps.

    Only copies when offset=0 and limit is large enough to indicate the model
    wants the full file.  Errors are logged but never propagated.

    *content*, when given, is written in place of the file's bytes, so a
    caller that showed the model a transformed view of the file (e.g. an
    unwrapped MCP envelope) can give bash the same text.

    Returns the sandbox path on success, or ``None`` on skip/failure.

    Size handling:
    - <= 32 KB: written to ``/tmp/<hash>-<basename>`` via shell base64
      (``_sandbox_write``).  Kept small to stay within ARG_MAX.
    - 32 KB - 50 MB: written to ``/home/user/<hash>-<basename>`` via
      ``sandbox.files.write()`` to avoid shell argument length limits.
    - > 50 MB: skipped entirely with a warning.

    The sandbox filename is prefixed with a short hash of the full source
    path to avoid collisions when different source files share the same
    basename (e.g. multiple ``result.json`` files).
    """
    if offset != 0 or limit < _DEFAULT_READ_LIMIT:
        return None
    try:
        expanded = os.path.realpath(os.path.expanduser(file_path))
        basename = os.path.basename(expanded)
        source = f"{expanded}:transformed" if content is not None else expanded
        source_id = hashlib.sha256(source.encode()).hexdigest()[:12]
        unique_name = f"{source_id}-{basename}"
        read = (
            await _read_host_file(expanded, _BRIDGE_SKIP_BYTES)
            if content is None
            else (content, len(content.encode("utf-8")))
        )
        if read is None or read[1] > _BRIDGE_SKIP_BYTES:
            logger.warning(
                "[E2B] Skipping bridge for file over %d bytes: %s",
                _BRIDGE_SKIP_BYTES,
                basename,
            )
            return None

        data, file_size = read
        if file_size <= _BRIDGE_SHELL_MAX_BYTES:
            sandbox_path = f"/tmp/{unique_name}"
            await _sandbox_write(sandbox, sandbox_path, data)
        else:
            sandbox_path = f"/home/user/{unique_name}"
            await sandbox.files.write(sandbox_path, data)
        logger.info(
            "[E2B] Bridged SDK file to sandbox: %s -> %s", basename, sandbox_path
        )
        return sandbox_path
    except Exception:
        logger.warning(
            "[E2B] Failed to bridge SDK file to sandbox: %s",
            file_path,
            exc_info=True,
        )
        return None


async def bridge_and_annotate(
    sandbox: Any,
    file_path: str,
    offset: int,
    limit: int,
    content: str | None = None,
) -> str | None:
    """Bridge a host file to the sandbox and return a newline-prefixed annotation.

    Combines ``bridge_to_sandbox`` with the standard annotation suffix so
    callers don't need to duplicate the pattern.  Returns a string like
    ``"\\n[Sandbox copy available at /tmp/abc-file.txt]"`` on success, or
    ``None`` if bridging was skipped or failed.
    """
    sandbox_path = await bridge_to_sandbox(sandbox, file_path, offset, limit, content)
    if sandbox_path is None:
        return None
    return f"\n[Sandbox copy available at {sandbox_path}]"


async def _read_host_file(path: str, limit: int) -> tuple[str | bytes, int] | None:
    """The file as text when it decodes as UTF-8, else its raw bytes, with its
    size; ``None`` when it holds more than *limit* bytes."""

    def _read_bytes() -> bytes | None:
        with open(path, "rb", opener=open_no_follow) as fh:
            if os.fstat(fh.fileno()).st_size > limit:
                return None
            raw = fh.read(limit + 1)
        return None if len(raw) > limit else raw

    raw_content = await asyncio.to_thread(_read_bytes)
    if raw_content is None:
        return None
    try:
        return raw_content.decode("utf-8"), len(raw_content)
    except UnicodeDecodeError:
        return raw_content, len(raw_content)


# Local read (for SDK-internal paths)


def _read_local(file_path: str, offset: int, limit: int | None) -> dict[str, Any]:
    """Read from the host filesystem (defence-in-depth path check)."""
    expanded = resolve_allowed_local_path(file_path, get_sdk_cwd())
    if expanded is None:
        return _mcp(f"Path not allowed: {file_path}", error=True)
    if os.path.splitext(expanded)[1].lower() in IMAGE_EXTENSIONS:
        return _read_local_image(expanded, offset, limit)
    if _is_likely_binary(expanded):
        return _mcp(
            _binary_read_guidance(expanded),
            error=True,
            outside=(),
        )
    try:
        limit = _DEFAULT_READ_LIMIT if limit is None else limit
        selected = _read_capped_lines(expanded, offset, limit)
        if selected is None:
            return _mcp(_too_large(expanded, "read"), error=True)
        numbered = "".join(
            f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)
        )
        return _mcp(numbered, outside=(numbered,))
    except FileNotFoundError:
        return _mcp(f"File not found: {file_path}", error=True)
    except Exception as exc:
        return _mcp(f"Error reading {file_path}: {exc}", error=True)


def _read_local_image(path: str, offset: int, limit: int | None) -> dict[str, Any]:
    try:
        with open(path, "rb", opener=open_no_follow) as file:
            raw = file.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        return _mcp(f"Could not read image: {os.path.basename(path)}.", error=True)
    return _image_result(raw, offset, limit)


def _image_result(raw: bytes, offset: int, limit: int | None) -> dict[str, Any]:
    if offset or limit is not None:
        return _mcp(
            "Image not shown: offset/limit only apply to text. Read the whole image.",
            error=True,
            outside=(),
        )
    try:
        image = prepare_image(raw)
    except ImageReadError as exc:
        return _mcp(str(exc), error=True, outside=())
    except ValueError as exc:
        return _mcp(str(exc), error=True)
    return {
        "content": [
            {"type": "text", "text": "Viewed image (first frame only)."},
            image.mcp(),
        ],
        "isError": False,
    }


# Tool descriptors (name, description, schema, handler)

E2B_FILE_TOOLS: list[tuple[str, str, dict[str, Any], Callable[..., Any]]] = [
    (
        "read_file",
        "Read a file from the cloud sandbox (/home/user or /tmp). "
        "Use offset and limit for large text files. " + IMAGE_READ_DESCRIPTION,
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path (relative to /home/user, or absolute under /home/user or /tmp).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line to start reading from (0-indexed). Default: 0.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read. Default: 2000.",
                },
            },
        },
        _handle_read_file,
    ),
    (
        "write_file",
        "Write or create a file in the cloud sandbox (/home/user or /tmp). "
        "Parent directories are created automatically. "
        "To copy a workspace file into the sandbox, use "
        "read_workspace_file with save_to_path instead.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path (relative to /home/user, or absolute under /home/user or /tmp).",
                },
                "content": {"type": "string", "description": "Content to write."},
            },
        },
        _handle_write_file,
    ),
    (
        "edit_file",
        "Targeted text replacement in a sandbox file. "
        "old_string must appear in the file and is replaced with new_string.",
        {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path (relative to /home/user, or absolute under /home/user or /tmp).",
                },
                "old_string": {"type": "string", "description": "Text to find."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false).",
                },
            },
        },
        _handle_edit_file,
    ),
    (
        "glob",
        "Search for files by name pattern in the cloud sandbox.",
        {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. *.py).",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search. Default: /home/user.",
                },
            },
        },
        _handle_glob,
    ),
    (
        "grep",
        "Search file contents by regex in the cloud sandbox.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern."},
                "path": {
                    "type": "string",
                    "description": "File or directory. Default: /home/user.",
                },
                "include": {
                    "type": "string",
                    "description": "Glob to filter files (e.g. *.py).",
                },
            },
        },
        _handle_grep,
    ),
]

E2B_FILE_TOOL_NAMES: list[str] = [name for name, *_ in E2B_FILE_TOOLS]


# ---------------------------------------------------------------------------
# Unified tool descriptors — used by tool_adapter.py in both E2B and non-E2B modes
# ---------------------------------------------------------------------------

WRITE_TOOL_NAME = "Write"
WRITE_TOOL_DESCRIPTION = (
    "Write or create a file. Parent directories are created automatically. "
    "For large content (>2000 words), prefer writing in sections using "
    "bash_exec with 'cat > file' and 'cat >> file' instead."
)
WRITE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": (
                "The path to the file to write. "
                "Relative paths are resolved against the working directory."
            ),
        },
        "content": {
            "type": "string",
            "description": "The content to write to the file.",
        },
    },
}

READ_TOOL_NAME = "read_file"
READ_TOOL_DESCRIPTION = (
    "Read a file from the working directory. Returns content with line numbers "
    "(cat -n format). Use offset and limit to read specific ranges for large text files. "
    + IMAGE_READ_DESCRIPTION
)
READ_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": (
                "The path to the file to read. "
                "Relative paths are resolved against the working directory."
            ),
        },
        "offset": {
            "type": "integer",
            "description": (
                "Line number to start reading from (0-indexed). Default: 0."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Number of lines to read. Default: 2000.",
        },
    },
}

EDIT_TOOL_NAME = "Edit"
EDIT_TOOL_DESCRIPTION = (
    "Make targeted text replacements in a file. Finds old_string in the file "
    "and replaces it with new_string. For replacing all occurrences, set "
    "replace_all=true."
)
EDIT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": (
                "The path to the file to edit. "
                "Relative paths are resolved against the working directory."
            ),
        },
        "old_string": {
            "type": "string",
            "description": "The text to find in the file.",
        },
        "new_string": {
            "type": "string",
            "description": "The replacement text.",
        },
        "replace_all": {
            "type": "boolean",
            "description": (
                "Replace all occurrences of old_string (default: false). "
                "When false, old_string must appear exactly once."
            ),
        },
    },
}


def get_write_tool_handler() -> Callable[..., Any]:
    """Return the Write handler for non-E2B mode."""
    return _handle_write_file


def get_read_tool_handler() -> Callable[..., Any]:
    """Return the Read handler for non-E2B mode."""
    return _handle_read_file


def get_edit_tool_handler() -> Callable[..., Any]:
    """Return the Edit handler for non-E2B mode."""
    return _handle_edit_file

```

## autogpt_platform/backend/backend/copilot/tools/bash_exec.py

Source: https://github.com/Significant-Gravitas/AutoGPT/blob/13ac38c5b6583bd01b0a6142b0060fc03e102b6e/autogpt_platform/backend/backend/copilot/tools/bash_exec.py

SHA-256: `918bb6ae8eaca9c554c7bfeef020cd0e8a6a4bc46a1ed6b18994ba2b6a73d935`

```
"""Bash execution tool — run shell commands on E2B or in a bubblewrap sandbox.

When an E2B sandbox is available in the current execution context the command
runs directly on the remote E2B cloud environment.  This means:

- **Persistent filesystem**: files survive across turns via HTTP-based sync
  with the sandbox's ``/home/user`` directory (E2B files API), shared with
  SDK Read/Write/Edit tools.
- **Full internet access**: E2B sandboxes have unrestricted outbound network.
- **Execution isolation**: E2B provides a fresh, containerised Linux environment.

Connected accounts reach the box as environment variables (``GH_TOKEN`` and
``GITHUB_TOKEN`` for GitHub).  When the box's egress goes through the
credential swap proxy (``backend.util.e2b_network``), those variables hold a
placeholder and the proxy puts the real value in on the way out, so no stored
credential is ever in the box.  Without the proxy they hold the real token,
as they always have.

When E2B is *not* configured the tool falls back to **bubblewrap** (bwrap):
OS-level isolation with a whitelist-only filesystem, no network, and resource
limits.  Requires bubblewrap to be installed (Linux only).
"""

import asyncio
import contextlib
import logging
import shlex
from typing import Any

from e2b import AsyncSandbox, CommandExitException
from e2b.exceptions import NotFoundException, TimeoutException
from pydantic import BaseModel

from backend.copilot.constants import HUNG_TOOL_CAP_SECONDS
from backend.copilot.context import (
    E2B_WORKDIR,
    get_bash_command_as_written,
    get_current_permissions,
    get_current_sandbox,
    looks_like_sdk_tool_result_path,
    sdk_tool_result_redirect_hint,
)
from backend.copilot.credential_selection import selected_credentials
from backend.copilot.gate.executed_files import run_targets
from backend.copilot.gate.policy import Effect
from backend.copilot.gate.shell_write import workspace_write_target
from backend.copilot.gate.subject import Subject
from backend.copilot.integration_creds import (
    expert_allowed_ids,
    get_github_user_git_identity,
    get_integration_env_vars,
    grant_to_box,
    placeholder_env,
    placeholder_grants,
)
from backend.copilot.model import ChatSession
from backend.copilot.permissions import allowed_providers
from backend.copilot.sdk.env import config as chat_config
from backend.util.e2b_network import proxy_address, reattach_command
from backend.util.sandbox_login import (
    changed_login_files,
    judged_text,
    read_capped,
    run_internal,
)

from .base import BaseTool
from .connect_integration import requested_scopes
from .e2b_sandbox import SandboxOwner, keep_sandbox_running
from .models import BashExecResponse, ErrorResponse, ToolResponseBase
from .sandbox import MAX_OUTPUT_SIZE, get_workspace_dir, has_full_sandbox, run_sandboxed

logger = logging.getLogger(__name__)

# Headroom on the box's running-time limit past the command's own timeout, so
# the box is still up to report the result (or the timeout) and to be killed.
_SANDBOX_LIMIT_MARGIN_SECONDS = 60
# A stream that breaks this close to the command's deadline is its timeout.
# The SDK's own deadline starts after ours and is rounded to the millisecond,
# so it never fires earlier than this; any earlier break is the box's doing.
_DEADLINE_SLACK_SECONDS = 0.05
# Reattaches to a running command after its stream to the box drops.
_MAX_RECONNECTS = 3
_KILL_TIMEOUT_SECONDS = 10
# The turn's idle watchdog fires HUNG_TOOL_CAP_SECONDS after the last SDK
# message, which is before the gate, keep_sandbox_running (up to ~40s) and
# stream setup. Capping this far below it lets the tool's own timeout land first.
_HUNG_CAP_MARGIN_SECONDS = 120

# envd's kill reaches only the shell it started, so the children of a compound
# command (`npm install && npm run build`) would keep running. This stops the
# shell first, so it cannot start the next step once a child dies, then stops
# and kills every process under it. The shell itself is left for envd's kill.
# Exits non-zero if any of them is still alive afterwards (e.g. one run as root).
# /proc is read through cat because awk aborts on an input file that vanished
# after the glob expanded, which any process exiting meanwhile causes, and the
# listing would then come back empty. A child can fork between a listing and
# its STOP, so the stopping repeats until the listing holds still; if it never
# does, whatever forked after the last listing may survive, so that exits 1 too.
_KILL_TREE_SCRIPT = r"""
p=__PID__
kill -STOP "$p" 2>/dev/null || exit 0
under() {
  cat /proc/[0-9]*/stat 2>/dev/null | awk -v root="$p" '
    { pid = $1; s = $0; sub(/.*\) /, "", s); split(s, f, " ")
      parent[pid] = f[2]; state[pid] = f[1] }
    END { q[1] = root; n = 1
          while (n) { r = q[n--]
                      for (k in parent) if (parent[k] == r) {
                        q[++n] = k; if (state[k] != "Z") print k } } }
  ' | sort -n
}
kids= settled=
for _ in 1 2 3 4 5 6 7 8 9 10; do
  now=$(under); [ "$now" = "$kids" ] && settled=1 && break
  kids=$now; [ -n "$kids" ] && kill -STOP $kids 2>/dev/null
done
[ -n "$kids" ] && kill -KILL $kids 2>/dev/null
[ -n "$settled" ] || exit 1
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -z "$(under)" ] && exit 0
  sleep 0.1
done
exit 1
"""


class _E2BRun(BaseModel):
    """How a command on the box ended, with all the output we received."""

    stdout: str
    stderr: str
    elapsed: float
    exit_code: int | None = None
    timed_out: bool = False
    # It printed more than MAX_OUTPUT_SIZE on one stream; the rest is not kept.
    output_exceeded: bool = False
    # Kill sent after either: True done, False not found, None unknown.
    killed: bool | None = None
    # The processes the command started were killed along with it.
    children_killed: bool = False
    # Why we stopped following a command that had not ended: the stream
    # error's type name.
    lost: str | None = None
    # The command ended while we were not attached: no exit code.
    ended_unseen: bool = False
    reconnects: int = 0


def _build_completion_response(
    stdout: str | None,
    stderr: str | None,
    exit_code: int,
    secret_values: list[str],
    session_id: str | None,
) -> BashExecResponse:
    """The command's result, with any injected token replaced by [REDACTED].

    Not a security control: a literal match on one command's output, beaten by
    base64, ``rev`` or a newline, blind to files read back and to anything sent
    over the network.  It only keeps an accidental ``env`` from printing a
    token into the chat.  Behind the swap proxy there is no token in the box
    and nothing to redact.
    """
    out = stdout or ""
    err = stderr or ""
    for secret in secret_values:
        out = out.replace(secret, "[REDACTED]")
        err = err.replace(secret, "[REDACTED]")
    return BashExecResponse(
        message=f"Command executed with status code {exit_code}",
        stdout=out,
        stderr=err,
        exit_code=exit_code,
        timed_out=False,
        session_id=session_id,
    ).from_outside(out, err)


class BashExecTool(BaseTool):
    """Execute Bash commands on E2B or in a bubblewrap sandbox."""

    has_gate_subject = True
    writes_files = True

    @property
    def name(self) -> str:
        return "bash_exec"

    @property
    def description(self) -> str:
        return (
            "Execute a Bash command or script. Shares filesystem with SDK file tools. "
            "Useful for scripts, data processing, and package installation. "
            "Killed after `timeout` seconds. Anything that should persist belongs "
            "in ~/workspace (a durable folder; the user's uploads are not in it, "
            "see list_workspace_files); other paths are scratch. The "
            "desktop (start_desktop) is this same machine, all paths included. "
            "Expert sessions: ~/workspace is the expert's own machine, ~/shared "
            "is the user's folder."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command or script.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds; raise for long-running commands.",
                    "default": 120,
                    "title": "Time limit",
                    "format": "seconds",
                },
            },
            "required": ["command"],
        }

    @property
    def requires_auth(self) -> bool:
        # True because _execute_on_e2b hands the box the user's connected
        # accounts (GH_TOKEN etc.): the tokens themselves without the swap
        # proxy, placeholders the proxy turns into them with it.  Either way
        # the command acts with the user's accounts, so only an authenticated
        # user may run one.
        return True

    async def gate_subject(
        self, user_id: str, session: ChatSession, args: dict[str, Any]
    ) -> Subject | None:
        """A command that only writes one workspace file is judged as that write."""
        command = args.get("command")
        target = workspace_write_target(command) if isinstance(command, str) else None
        if target is None or not await _lands_on(target):
            return None
        return Subject(key="write_workspace_file", name=target, effect=Effect.WORKSPACE)

    async def gate_context(self, args: dict[str, Any]) -> dict[str, str | None] | None:
        """What the scripts this command runs contain, and the login files changed
        since the sandbox was made; None where one could not be read or told."""
        command = args.get("command")
        sandbox = get_current_sandbox()
        if not isinstance(command, str) or sandbox is None:
            return None
        targets = run_targets(command)
        reads = await asyncio.gather(
            *(read_capped(sandbox, path) for path in targets.paths),
            return_exceptions=True,
        )
        scripts: dict[str, str | None] = {run: None for run in targets.unclear}
        for path, raw in zip(targets.paths, reads):
            if isinstance(raw, BaseException):
                scripts[path] = None
            elif raw is not None:  # Absent: made by this command, or it fails.
                scripts[path] = judged_text(raw)
        # `bash -l` runs these before the command itself.
        return {**await changed_login_files(sandbox), **scripts}

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        command: str = "",
        timeout: int = 120,
        **kwargs: Any,
    ) -> ToolResponseBase:
        """Run a bash command on E2B (if available) or in a bubblewrap sandbox.

        Dispatches to :meth:`_execute_on_e2b` when a sandbox is present in the
        current execution context, otherwise falls back to the local bubblewrap
        sandbox.  Returns a :class:`BashExecResponse` on success or an
        :class:`ErrorResponse` when the sandbox is unavailable or the command
        is empty.
        """
        session_id = session.session_id if session else None

        command = command.strip()
        timeout = int(timeout)

        if not command:
            return ErrorResponse(
                message="No command provided.",
                error="empty_command",
                session_id=session_id,
            ).from_outside()

        # Pre-flight redirect: bash sandbox can't reach host-side SDK
        # tool-result paths. Without this the model burns turns retrying
        # `cat /root/.claude/projects/...` after `Permission denied`.
        # Checks the command as written, minus its ``@@agptfile:`` refs, so
        # neither a ref nor the file content it expands to trips the check.
        # A command that is only refs is unset, and its expanded script is
        # checked instead.
        as_written = get_bash_command_as_written()
        if as_written is None:
            as_written = command
        if looks_like_sdk_tool_result_path(as_written):
            return ErrorResponse(
                message=sdk_tool_result_redirect_hint(as_written),
                error="sdk_tool_result_path_in_bash_command",
                session_id=session_id,
            ).from_outside()

        sandbox = get_current_sandbox()
        if sandbox is not None:
            return await self._execute_on_e2b(
                sandbox,
                command,
                timeout,
                session_id,
                user_id,
                required_scopes=requested_scopes(session),
                owner=(
                    SandboxOwner.for_session(session.session_id, session.expert_id)
                    if session
                    else None
                ),
            )

        # Bubblewrap fallback: local isolated execution.
        if not has_full_sandbox():
            return ErrorResponse(
                message="bash_exec requires bubblewrap sandbox (Linux only).",
                error="sandbox_unavailable",
                session_id=session_id,
            ).from_outside()

        workspace = get_workspace_dir(session_id or "default")

        stdout, stderr, exit_code, timed_out = await run_sandboxed(
            command=["bash", "-c", command],
            cwd=workspace,
            timeout=timeout,
        )

        return BashExecResponse(
            message=(
                "Execution timed out"
                if timed_out
                else f"Command executed with status code {exit_code}"
            ),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            session_id=session_id,
        ).from_outside(stdout, stderr)

    async def _execute_on_e2b(
        self,
        sandbox: AsyncSandbox,
        command: str,
        timeout: int,
        session_id: str | None,
        user_id: str | None = None,
        required_scopes: dict[str, frozenset[str]] | None = None,
        owner: SandboxOwner | None = None,
    ) -> ToolResponseBase:
        """Execute *command* on the E2B sandbox via commands.run().

        Connected accounts go into the command's env (e.g. GH_TOKEN), so CLI
        tools like ``gh`` work without manual authentication: as placeholders
        when the box egresses through the swap proxy, as the real tokens when
        it does not.
        """
        # The turn stops following a tool after the hung-tool cap and pauses
        # the box, so a longer timeout only keeps an unwatched box billing.
        timeout = min(max(timeout, 1), HUNG_TOOL_CAP_SECONDS - _HUNG_CAP_MARGIN_SECONDS)
        envs: dict[str, str] = {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        }
        # Injected token values, redacted from the output (see
        # _build_completion_response).  Placeholders are not secret.
        secret_values: list[str] = []
        if user_id is not None:
            selected = await selected_credentials(session_id)
            # An expert's box gets only what the expert was granted.
            allowed_ids = await expert_allowed_ids(
                user_id, owner.id if owner is not None and owner.is_expert else None
            )
            # The run's ceiling on connected accounts.  Without the proxy,
            # leaving a provider's token out is what keeps it from the box.
            # Behind it, a provider outside the ceiling gets no grant (and so
            # empty variables); the ceiling recorded when the box's egress was
            # pinned, enforced by the swap service, is what holds.
            providers = allowed_providers(get_current_permissions())
            if proxy_address() is not None:
                grants = await placeholder_grants(
                    user_id,
                    required_scopes,
                    selected,
                    providers,
                    allowed_ids=allowed_ids,
                )
                try:
                    # Before the command runs: its placeholders resolve only
                    # for credentials granted to this box.
                    await grant_to_box(sandbox.sandbox_id, grants)
                except Exception:
                    # The command still runs; its placeholders go out as they
                    # are and fail at the provider, which is the safe side.
                    logger.warning(
                        "[E2B] Could not grant credentials to %.12s",
                        sandbox.sandbox_id,
                        exc_info=True,
                    )
                envs.update(placeholder_env(grants))
            else:
                integration_env = await get_integration_env_vars(
                    user_id,
                    required_scopes,
                    selected,
                    providers,
                    allowed_ids=allowed_ids,
                )
                secret_values = [v for v in integration_env.values() if v]
                envs.update(integration_env)

            # Set git author/committer identity from the user's GitHub profile
            # so commits made in the sandbox are attributed correctly; not for
            # a run whose ceiling leaves GitHub out, which may not use it.
            if providers is None or "github" in providers:
                git_identity = await get_github_user_git_identity(
                    user_id, selected.get("github"), allowed_ids=allowed_ids
                )
                if git_identity:
                    envs.update(git_identity)

        # The box's running-time limit was armed when this turn connected;
        # E2B pauses the box at it, breaking the command's stream mid-run.
        await keep_sandbox_running(
            sandbox,
            max(
                chat_config.e2b_sandbox_timeout,
                timeout + _SANDBOX_LIMIT_MARGIN_SECONDS,
            ),
            owner,
        )
        try:
            run = await _follow_command(sandbox, command, timeout, envs)
        except TimeoutException as exc:
            return ErrorResponse(
                message=(
                    "The sandbox did not confirm the command started within "
                    "the request timeout, so it may or may not have run "
                    f"({exc}). This is not the command's {timeout}s timeout."
                ),
                error="e2b_start_timeout",
                session_id=session_id,
            ).from_outside()
        except Exception as exc:
            logger.error("[E2B] bash_exec failed: %s", exc, exc_info=True)
            return ErrorResponse(
                message=f"E2B execution failed: {exc}",
                error="e2b_execution_error",
                session_id=session_id,
            ).from_outside(str(exc))
        return _run_response(run, timeout, secret_values, session_id)


async def _follow_command(
    sandbox: AsyncSandbox, command: str, timeout: int, envs: dict[str, str]
) -> _E2BRun:
    """Run *command* on the box and follow it to its end or its *timeout*.

    The SDK raises ``TimeoutException`` for more than the command's own
    deadline: the box pausing or dying, or a proxy cancelling the stream,
    surface the same way at any elapsed time.  Only a break at the deadline
    is the timeout (the command is then killed); before it, the command is
    still running on the box, so the stream is reattached instead of the
    command reported as failed.  Output printed while detached is not
    replayed by E2B.  Raises only when the command could not be started.
    """
    loop = asyncio.get_running_loop()
    stdout = _CappedOutput()
    stderr = _CappedOutput()
    started = loop.time()
    deadline = started + timeout
    handle = await sandbox.commands.run(
        f"bash -c {shlex.quote(command)}",
        background=True,
        cwd=E2B_WORKDIR,
        timeout=timeout,
        envs=envs,
        on_stdout=stdout.append,
        on_stderr=stderr.append,
    )
    pid = handle.pid
    reconnects = 0

    def outcome(**kwargs: Any) -> _E2BRun:
        return _E2BRun(
            stdout="".join(stdout.chunks),
            stderr="".join(stderr.chunks),
            elapsed=loop.time() - started,
            reconnects=reconnects,
            **kwargs,
        )

    while True:
        try:
            result = await handle.wait()
            return outcome(exit_code=result.exit_code)
        except CommandExitException as exc:
            return outcome(exit_code=exc.exit_code)
        except Exception as exc:
            if stdout.exceeded or stderr.exceeded:
                # The handle keeps its own copy of every chunk while followed;
                # the kill still runs if detaching fails.
                with contextlib.suppress(Exception):
                    await handle.disconnect()
                return outcome(output_exceeded=True, **await _kill(sandbox, pid))
            remaining = deadline - loop.time()
            if remaining <= _DEADLINE_SLACK_SECONDS:
                return outcome(timed_out=True, **await _kill(sandbox, pid))
            logger.warning(
                "[E2B] bash_exec lost its stream to pid %s after %.1fs of %ds "
                "(%s: %s)",
                pid,
                loop.time() - started,
                timeout,
                type(exc).__name__,
                exc,
            )
            if reconnects >= _MAX_RECONNECTS:
                return outcome(lost=type(exc).__name__)
            reconnects += 1
            try:
                handle = await reattach_command(
                    sandbox,
                    pid,
                    timeout=remaining,
                    on_stdout=stdout.append,
                    on_stderr=stderr.append,
                )
            except NotFoundException:
                return outcome(ended_unseen=True)
            except Exception as reconnect_exc:
                if deadline - loop.time() <= _DEADLINE_SLACK_SECONDS:
                    return outcome(timed_out=True, **await _kill(sandbox, pid))
                logger.warning(
                    "[E2B] bash_exec could not reattach to pid %s: %s",
                    pid,
                    reconnect_exc,
                )
                return outcome(lost=type(exc).__name__)


class _OutputExceeded(Exception):
    """A command printed more than ``MAX_OUTPUT_SIZE`` on one stream."""


class _CappedOutput:
    """One output stream of a command, kept up to ``MAX_OUTPUT_SIZE``."""

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.exceeded = False
        self._room = MAX_OUTPUT_SIZE

    def append(self, chunk: str) -> None:
        # Raising stops the SDK reading the stream, and handle.wait() re-raises it.
        kept = chunk[: self._room]
        self.chunks.append(kept)
        self._room -= len(kept)
        if len(kept) < len(chunk):
            self.exceeded = True
            raise _OutputExceeded


async def _kill(sandbox: AsyncSandbox, pid: int) -> dict[str, Any]:
    children_killed = await _kill_children(sandbox, pid)
    try:
        killed = await asyncio.wait_for(
            sandbox.commands.kill(pid), timeout=_KILL_TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning(f"[E2B] Could not kill pid {pid}: {exc}")
        killed = None
    return {"killed": killed, "children_killed": children_killed}


async def _kill_children(sandbox: AsyncSandbox, pid: int) -> bool:
    try:
        result = await run_internal(
            sandbox,
            _KILL_TREE_SCRIPT.replace("__PID__", str(pid)),
            timeout=_KILL_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("[E2B] Could not kill the children of pid %s: %s", pid, exc)
        return False
    return result.exit_code == 0


def _run_response(
    run: _E2BRun, timeout: int, secret_values: list[str], session_id: str | None
) -> BashExecResponse:
    if run.exit_code is not None:
        response = _build_completion_response(
            run.stdout, run.stderr, run.exit_code, secret_values, session_id
        )
        if run.reconnects:
            response.message += (
                f" (the connection to the sandbox dropped {run.reconnects} "
                "time(s) and was resumed; output printed meanwhile may be "
                "missing)"
            )
        return response

    elapsed = f"{run.elapsed:.1f}s"
    if run.timed_out or run.output_exceeded:
        if run.killed is True and run.children_killed:
            fate = "it was killed, with every process it started."
        elif run.killed is True:
            fate = (
                "the shell was killed, but processes it started may still be "
                "running."
            )
        elif run.killed is False:
            fate = "it had already ended."
        else:
            fate = "it could not be killed and may still be running."
        if run.timed_out:
            note = f"Timed out after {timeout}s"
        else:
            note = (
                f"Printed more than {MAX_OUTPUT_SIZE:,} characters; redirect "
                "output to a file and read parts of it"
            )
        message = f"{note}; {fate}"
    elif run.ended_unseen:
        note = (
            f"Lost the connection to the sandbox after {elapsed} (timeout "
            f"{timeout}s); the command ended meanwhile, exit code unknown"
        )
        message = (
            f"{note}. Any output it printed after the drop is lost; it was "
            "not killed and did not time out."
        )
    else:
        note = (
            f"Lost the connection to the sandbox after {elapsed} (timeout "
            f"{timeout}s); the command may still be running"
        )
        message = (
            f"{note}. It did not time out and was not killed (the stream "
            f"to it broke: {run.lost}). "
            "Check whether it is still running (e.g. with ps) before running "
            "it again."
        )
    response = _build_completion_response(
        run.stdout, run.stderr, -1, secret_values, session_id
    )
    # First line of stderr, so it is what the chat shows for this call.
    response.stderr = f"{note}\n{response.stderr}".rstrip("\n")
    response.message = message
    response.timed_out = run.timed_out
    return response


async def _lands_on(path: str) -> bool:
    """A shell write to ``path`` lands there: no symlink on it, and no changed login
    file, which runs first and can redirect it (``CDPATH``, an exported ``cat``).
    Same accepted race as ``_check_sandbox_symlink_escape``."""
    sandbox = get_current_sandbox()
    if sandbox is None:
        return False
    try:
        if await changed_login_files(sandbox):
            return False
        result = await run_internal(
            sandbox, f"readlink -m -- {shlex.quote(path)}", cwd=E2B_WORKDIR, timeout=5
        )
    except Exception:
        logger.warning("Could not resolve a bash_exec write target", exc_info=True)
        return False
    return result.exit_code == 0 and (result.stdout or "").strip() == path

```

## autogpt_platform/backend/backend/copilot/tools/find_capability.py

Source: https://github.com/Significant-Gravitas/AutoGPT/blob/13ac38c5b6583bd01b0a6142b0060fc03e102b6e/autogpt_platform/backend/backend/copilot/tools/find_capability.py

SHA-256: `6dbafb7705c5b3148536b2c5b6ff2b6b3f48efb25f887da27a606e920c18fecc`

```
"""Search the capability registry: integrations, blocks, MCP servers,
platform tools, the session owner's skills and experts behind one query."""

import asyncio
import logging
from typing import Any

from backend.copilot.capabilities.index import SearchHit
from backend.copilot.capabilities.models import CapabilityKindName
from backend.copilot.capabilities.ranking import (
    ConnectionState,
    resolve_needs_expert_grant,
)
from backend.copilot.capabilities.resolve import load_connection_state
from backend.copilot.context import get_current_permissions
from backend.copilot.model import ChatSession

from .base import BaseTool
from .chat_platform_hint import POST_TOOL, ChatPlatformLinks, chat_platform_links
from .models import (
    CapabilityListResponse,
    ErrorResponse,
    NoResultsResponse,
    ToolResponseBase,
)
from .session_registry import session_registry

logger = logging.getLogger(__name__)

CONTEXTS = ("direct", "graph")
KINDS = ("tool", "block", "mcp_server", "skill", "expert")
_KIND_ARG: dict[str, CapabilityKindName] = {
    "tool": "tool",
    "block": "block",
    "mcp_server": "mcp_server",
    "skill": "skill",
    "expert": "expert",
}


NEEDS_EXPERT_GRANT = "needs_expert_grant"


class FindCapabilityTool(BaseTool):
    """Ranked capability search with connection state."""

    @property
    def name(self) -> str:
        return "find_capability"

    @property
    def description(self) -> str:
        return (
            "Search everything the platform can do: integrations, blocks, MCP "
            "servers, platform tools, skills and experts to hire, by service, "
            "action or role. "
            "Results are ranked and show whether the user has connected each "
            "one. Call this before saying something is not possible. Then "
            "describe_capability(id) to see inputs, and run_capability(id, "
            "input) to act."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Service, action or name, e.g. 'linear issue', "
                        "'send email', 'http request', 'sentry'."
                    ),
                },
                "context": {
                    "type": "string",
                    "enum": list(CONTEXTS),
                    "description": (
                        "'direct' (default) for things to run now; 'graph' when "
                        "choosing blocks for an agent graph (includes graph-only "
                        "blocks such as inputs and outputs)."
                    ),
                    "default": "direct",
                },
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "Usually omit; 'tool' hides blocks.",
                },
            },
            "required": ["query"],
        }

    @property
    def requires_auth(self) -> bool:
        return True

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        query: str = "",
        context: str = "direct",
        kind: str | None = None,
        **kwargs,
    ) -> ToolResponseBase:
        query = (query or "").strip()
        session_id = session.session_id
        if not query:
            return ErrorResponse(
                message="Please provide a query", session_id=session_id
            )
        if context not in CONTEXTS:
            context = "direct"
        if kind is not None and kind not in KINDS:
            return ErrorResponse(
                message=f"kind must be one of {', '.join(KINDS)}",
                session_id=session_id,
            )
        if not user_id:
            return ErrorResponse(
                message="Authentication required", session_id=session_id
            )

        connections, index = await asyncio.gather(
            load_connection_state(user_id, session.expert_id),
            session_registry(user_id, session),
        )
        result = index.search(
            query,
            context="graph" if context == "graph" else "direct",
            kind=_KIND_ARG.get(kind or ""),
            connections=connections,
            permissions=get_current_permissions(),
            prefer_mcp=session.expert_id is not None and context != "graph",
        )
        if not result.hits and not result.fallback and not result.other_kinds:
            return NoResultsResponse(
                message=f"No capability found for '{query}'",
                suggestions=_no_result_suggestions(kind),
                session_id=session_id,
            )
        linked = (
            await chat_platform_links(user_id)
            if any(
                hit.entry.name == POST_TOOL
                for hit in result.hits + result.fallback + result.other_kinds
            )
            else ChatPlatformLinks()
        )
        capabilities = [_listing(hit, connections, linked) for hit in result.hits]
        fallback = [_listing(hit, connections, linked) for hit in result.fallback]
        other_kinds = [_listing(hit, connections, linked) for hit in result.other_kinds]
        message = _message(
            result.service,
            len(result.hits),
            len(result.fallback),
            skills=any(hit.entry.kind == "skill" for hit in result.hits),
            experts=any(hit.entry.kind == "expert" for hit in result.hits),
            needs_grant=any(
                c.get("connected") == NEEDS_EXPERT_GRANT
                for c in capabilities + fallback + other_kinds
            ),
        )
        if result.other_kinds:
            message += (
                f" kind='{kind}' hid {result.hidden_by_kind} match(es) of other "
                "kinds that cover this as well or better, listed under "
                "other_kinds; search again without kind before concluding "
                "nothing covers this."
            )
        return CapabilityListResponse(
            message=message,
            query=query,
            capabilities=capabilities,
            count=len(result.hits),
            fallback=fallback,
            other_kinds=other_kinds,
            service=result.service,
            session_id=session_id,
        )


def _no_result_suggestions(kind: str | None) -> list[str]:
    """What to try next.  An off-catalog MCP server is the last resort, so it
    is never suggested while a ``kind`` filter may be what emptied the list:
    integrations such as Gmail and Slack are blocks, and kind='tool' or
    'mcp_server' hides them (SECRT-2820)."""
    if kind is not None:
        return [
            f"Search again without kind: kind='{kind}' hides every other kind "
            "of capability, including the user's connected blocks",
        ]
    return [
        "Try the service name alone, or a broader action ('email', 'sheet', 'http')",
        "For a service with no result, web_search '<service> MCP server' and "
        "call run_capability with the server URL as the id",
    ]


def _listing(
    hit: SearchHit, connections: ConnectionState, linked: ChatPlatformLinks
) -> dict[str, Any]:
    listing = hit.entry.listing()
    # The bot posts only where the user linked a platform to it, which no
    # credential records: without this the model cannot tell a tool that
    # will post from one that answers "nothing is linked".  Channels need a
    # linked server; a DM link lets it DM the user only.
    if hit.entry.name == POST_TOOL:
        listing["linked"] = sorted(linked.servers)
        listing["dm_linked"] = sorted(linked.dms)
    if hit.entry.connection.required:
        listing["connected"] = (
            NEEDS_EXPERT_GRANT
            if resolve_needs_expert_grant(hit.entry, connections)
            else hit.connected
        )
    return listing


def _message(
    service: str | None,
    hits: int,
    fallback: int,
    *,
    skills: bool = False,
    experts: bool = False,
    needs_grant: bool = False,
) -> str:
    parts = [f"Found {hits} capabilit{'y' if hits == 1 else 'ies'}"]
    if service:
        parts.append(f"for {service}")
    text = " ".join(parts) + "."
    if fallback:
        text += f" {fallback} generic fallback(s) listed separately."
    if hits or fallback:
        text += (
            " Call describe_capability(id) before first use, then "
            "run_capability(id, input). connected=false means the user must sign in "
            "first: run_capability returns the sign-in card."
        )
    if needs_grant:
        text += (
            f" connected='{NEEDS_EXPERT_GRANT}' means the account already has "
            "this integration but this expert has not been granted it: do not "
            "ask the user to sign in. run_capability returns the setup card "
            "with a Grant button for the existing credential; surface it and "
            "ask the user to grant access."
        )
    if skills:
        text += (
            " A kind=skill result is a saved procedure: "
            "run_capability(id, input={}) loads its body and package files; "
            "read it before acting."
        )
    if experts:
        text += (
            " A kind=expert result is an AI expert: hired=false is on the "
            "roster and running it proposes the hire on an approval card; "
            "hired=true is on the user's team and running it delegates a task."
        )
    return text

```

## autogpt_platform/backend/backend/copilot/tools/run_sub_session.py

Source: https://github.com/Significant-Gravitas/AutoGPT/blob/13ac38c5b6583bd01b0a6142b0060fc03e102b6e/autogpt_platform/backend/backend/copilot/tools/run_sub_session.py

SHA-256: `4997599355a5265519df2ab3ef41110071c4c3c0389678ac8e757d7bae66a1f9`

```
"""Start a sub-Otto conversation via the copilot_executor queue.

Mirror-image of ``run_agent`` + ``view_agent_output`` for copilot turns:

1. The tool creates (or validates ownership of) an inner ``ChatSession``
   and calls :func:`run_copilot_turn_via_queue` — the shared primitive
   that creates the stream-registry session meta, enqueues a
   ``CoPilotExecutionEntry``, and waits on the Redis stream until the
   terminal event arrives or the cap fires.
2. Any available ``copilot_executor`` worker claims the job, runs
   the SDK stream to completion, and publishes the final
   ``StreamFinish`` event on the session's Redis stream.
3. If the terminal event arrives in the wait window, the aggregated
   :class:`SessionResult` (response text, tool calls, usage) comes back
   in memory — no DB round-trip. Otherwise the tool returns
   ``status="running"`` + the sub's ``session_id`` and the agent polls
   via :mod:`get_sub_session_result`.

Compared to the prior in-process ``asyncio.Task`` implementation this
gives us deploy/crash resilience, natural load balancing across
workers, and a uniform conversation model — a sub is just another
copilot turn routed through the same queue and event bus as every
other turn.
"""

import json
import logging
import time
from datetime import timedelta
from typing import Any

from backend.copilot.active_turns import spawn_limit_message
from backend.copilot.background_subtasks import start_background_subtask
from backend.copilot.background_task import DELIVERY_SECONDS, FOLLOWUP_SECONDS
from backend.copilot.budget_signal import build_spawn_state_note
from backend.copilot.constants import MAX_TOOL_WAIT_SECONDS
from backend.copilot.context import get_current_permissions, get_workspace_manager
from backend.copilot.gate import child_autopilot_mode
from backend.copilot.model import (
    ChatSession,
    create_chat_session,
    delete_chat_session,
    get_chat_session,
)
from backend.copilot.sdk.session_waiter import (
    SessionOutcome,
    SessionResult,
    run_copilot_turn_via_queue,
)
from backend.copilot.sdk.stream_accumulator import ToolCallEntry
from backend.copilot.tree import SpawnRequest, TreeRefusal
from backend.data.workspace_internal import is_internal_workspace_file

from .base import BaseTool
from .expert_delegation import sent_from_metadata
from .models import (
    DelegatedExpertInfo,
    ErrorResponse,
    SubSessionStatusResponse,
    ToolResponseBase,
    WorkspaceFileInfoData,
)
from .workspace_files import AGENT_CREATED_METADATA

logger = logging.getLogger(__name__)


# Max wait for a single run_sub_session / get_sub_session_result call.
# Shared with every other long-running tool so the stream idle timeout's
# 2x headroom holds uniformly.
MAX_SUB_SESSION_WAIT_SECONDS = MAX_TOOL_WAIT_SECONDS

# Cap on how many sub-written files we enumerate in a completed response's
# manifest. Bounds context size; a sub producing more than this is pathological.
_WORKSPACE_FILE_MANIFEST_LIMIT = 50


class RunSubSessionTool(BaseTool):
    """Delegate a task to a fresh sub-Otto via the copilot_executor queue."""

    @property
    def name(self) -> str:
        return "run_sub_session"

    @property
    def requires_auth(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Run a durable subtask. Foreground waits; "
            "poll tool:get_sub_session_result if still running."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Task to run.",
                    "title": "Task",
                },
                "system_context": {
                    "type": "string",
                    "description": "Context prepended to prompt.",
                    "default": "",
                    "title": "Background",
                },
                "sub_autopilot_session_id": {
                    "type": "string",
                    "description": (
                        "Prior subtask to continue/queue; empty = new. "
                        "Reuse for related work."
                    ),
                    "default": "",
                    "title": "Continues",
                    "entity": "chat_session",
                },
                "wait_for_result": {
                    "type": "integer",
                    "description": (
                        "Inline wait in seconds; 0 returns immediately. "
                        f"Max {MAX_SUB_SESSION_WAIT_SECONDS}."
                    ),
                    "default": 60,
                    "title": "Waits up to",
                    "format": "seconds",
                },
                "background": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Only if the user asks for background work: fresh subtask, "
                        "10-minute deadline, then one budgeted summary here without tools. "
                        "Returns immediately; no polling."
                    ),
                    "title": "Run in background",
                },
            },
            "required": ["prompt"],
        }

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        *,
        prompt: str = "",
        system_context: str = "",
        sub_autopilot_session_id: str = "",
        wait_for_result: int = 60,
        background: bool = False,
        **kwargs,
    ) -> ToolResponseBase:
        if not prompt.strip():
            return ErrorResponse(
                message="prompt is required",
                session_id=session.session_id,
            ).from_outside()
        if user_id is None:
            return ErrorResponse(
                message="Authentication required",
                session_id=session.session_id,
            ).from_outside()

        if background:
            if sub_autopilot_session_id.strip():
                return ErrorResponse(
                    message="Background subtasks must start a fresh conversation."
                )
            background_prompt = (
                f"{system_context.strip()}\n\n{prompt}"
                if system_context.strip()
                else prompt
            )
            try:
                task = await start_background_subtask(
                    user_id, session, background_prompt
                )
            except TreeRefusal as refused:
                return ErrorResponse(message=refused.message).from_outside()
            return SubSessionStatusResponse(
                session_id=session.session_id,
                status=(
                    "queued"
                    if task.outcome is None
                    else (
                        "completed"
                        if task.outcome == "completed"
                        else "cancelled" if task.outcome == "cancelled" else "error"
                    )
                ),
                sub_session_id=task.child_session_id,
                sub_autopilot_session_id=task.child_session_id,
                sub_autopilot_session_link=_sub_session_link(task.child_session_id),
                background_task_id=task.id,
                background_expires_at=(
                    task.deadline
                    + timedelta(seconds=DELIVERY_SECONDS + FOLLOWUP_SECONDS)
                ).isoformat(),
                message=(
                    "Subtask accepted in the background. You can finish this reply now. "
                    "Otto will follow up here within the original budget. "
                    "Use the subtask's Stop control to cancel it. No polling is needed."
                ),
            ).from_outside()

        # Resolve the sub's ChatSession id — either resume an owned one or
        # create a fresh session that inherits the parent's dry_run so a
        # sub spawned inside a dry-run conversation doesn't silently
        # escalate to a live run.
        sub_session_param = sub_autopilot_session_id.strip()
        if sub_session_param:
            owned = await get_chat_session(sub_session_param)
            if owned is None or owned.user_id != user_id:
                return ErrorResponse(
                    message=(
                        f"sub_autopilot_session_id {sub_session_param} is not "
                        "a session you own. Leave empty to start a fresh sub, "
                        "or pass a session_id returned by a previous "
                        "run_sub_session call of yours."
                    ),
                    session_id=session.session_id,
                ).from_outside()
            if owned.expert_id != session.expert_id:
                return ErrorResponse(
                    message=(
                        f"sub_autopilot_session_id {sub_session_param} is not "
                        "in the current memory scope. Leave empty to start a "
                        "fresh sub for this assistant."
                    ),
                    session_id=session.session_id,
                ).from_outside()
            # Only the session that opened a sub may steer it: same scope is
            # not enough, or any sibling — or the sub itself — could queue a
            # prompt into it under its envelope rather than their own.
            #
            # Subs created before this tool started recording provenance have
            # ``None`` here and cannot satisfy the check. Refusing them would
            # strand every in-flight sub at deploy, so they fall back to the
            # scope + origin rules that governed them when they were made —
            # the group is closed (nothing new joins it) and ages out with the
            # 6h turn lifetime.
            if (
                owned.metadata.delegated_by_session_id is not None
                and owned.metadata.delegated_by_session_id != session.session_id
            ):
                return ErrorResponse(
                    message=(
                        f"sub_autopilot_session_id {sub_session_param} was not "
                        "started by this session. Leave empty to start a fresh "
                        "sub."
                    ),
                    session_id=session.session_id,
                ).from_outside()
            # Subs are created as automations below, so only a sub may be
            # resumed as one. Otherwise this tool would run a model-authored
            # prompt inside an interactive session the caller happens to own,
            # under an origin the staffing guard lets through.
            #
            # Positively `interactive` only, never "not automation": a session
            # persisted before `origin` existed reads back as None, and any
            # sub started before this deploy holds one of those. Refusing them
            # would break live sub-sessions to close a hole they never opened
            # — the staffing guard is where an unknown origin fails closed
            # instead. Same call as `blocks/autopilot.py` on resume.
            if owned.metadata.origin == "interactive":
                return ErrorResponse(
                    message=(
                        f"sub_autopilot_session_id {sub_session_param} was "
                        "started by a person, not by a previous run_sub_session "
                        "call. Leave empty to start a fresh sub for this "
                        "session."
                    ),
                    session_id=session.session_id,
                ).from_outside()
            if (
                owned.metadata.llm_auth_provider != session.metadata.llm_auth_provider
                or owned.metadata.llm_credential_id
                != session.metadata.llm_credential_id
            ):
                return ErrorResponse(
                    message="codex_session_route_mismatch",
                    session_id=session.session_id,
                ).from_outside()
            inner_session_id = sub_session_param
            opened_here = False
        else:
            new_session = await create_chat_session(
                user_id,
                dry_run=session.dry_run,
                organization_id=session.organization_id,
                team_id=session.team_id,
                llm_auth_provider=session.metadata.llm_auth_provider,
                llm_credential_id=session.metadata.llm_credential_id,
                expert_id=session.expert_id,
                # Provenance doubles as the resume capability above and as
                # the poll capability in get_sub_session_result. It also makes
                # subs visible to ``chain_refusal``'s walk, which is intended:
                # a sub hop spends delegation budget like any other hop (both
                # bounds refuse the same 4th hop), and a sub delegating back
                # to its own parent expert is seen as the loop it is.
                delegated_by_expert_id=session.expert_id,
                delegated_by_session_id=session.session_id,
                # A sub is machine-driven whatever opened it: its prompt is
                # written by the parent model, not typed by the user, and no
                # tool restriction applies to it. Inheriting an "interactive"
                # origin would put the staffing tools one hop away from the
                # gate that refuses them directly.
                origin="automation",
                # It starts in the mode the parent's gate enforces.
                inherited_autopilot_mode=child_autopilot_mode(session, None),
            )
            inner_session_id = new_session.session_id
            opened_here = True

        effective_prompt = prompt
        if system_context.strip():
            effective_prompt = f"[System Context: {system_context.strip()}]\n\n{prompt}"

        cap = max(0, min(wait_for_result, MAX_SUB_SESSION_WAIT_SECONDS))
        started_at = time.monotonic()
        outcome, result = await run_copilot_turn_via_queue(
            session_id=inner_session_id,
            user_id=user_id,
            message=effective_prompt,
            timeout=cap,
            permissions=get_current_permissions(),
            tool_call_id=(f"sub:{session.session_id}" if session.session_id else "sub"),
            tool_name="run_sub_session",
            # An isolate shares its spawner's memory namespace, so it must not
            # write to it; depth bounds how far it may spawn onward.
            spawn=SpawnRequest(may_spawn=True, shares_memory=True),
            allow_queue=False,
            message_metadata=sent_from_metadata(session),
        )
        elapsed = time.monotonic() - started_at
        discarded = opened_here and await discard_unused_sub_session(
            inner_session_id, user_id, outcome
        )
        workspace_files = (
            await list_sub_workspace_files(user_id, inner_session_id)
            if outcome == "completed"
            else None
        )
        outcome_response = response_from_outcome(
            outcome=outcome,
            result=result,
            inner_session_id=inner_session_id,
            parent_session_id=session.session_id,
            elapsed=elapsed,
            workspace_files=workspace_files,
        )
        outcome_response.message += await build_spawn_state_note()
        if discarded:
            # The row is gone, so its id and link would send the model to poll
            # a sub-session that no longer exists. Keep the reason, drop the
            # handles — the same shape handoff_to_expert returns on refusal.
            return ErrorResponse(
                message=outcome_response.message, session_id=session.session_id
            ).from_outside()
        return outcome_response


def apply_delegated_expert(
    response: SubSessionStatusResponse,
    expert: DelegatedExpertInfo | None,
) -> SubSessionStatusResponse:
    """Attach delegated expert metadata without rewriting the response text."""
    if expert is None:
        return response
    return response.model_copy(update={"expert": expert})


def _sub_session_link(inner_session_id: str | None) -> str | None:
    """Build the CoPilot UI URL for a sub-Otto session.

    Kept in one place so the format stays consistent across the
    running/completed/error paths, and so the frontend only has one
    contract to honour.
    """
    if not inner_session_id:
        return None
    return f"/copilot?sessionId={inner_session_id}"


async def list_sub_workspace_files(
    user_id: str,
    inner_session_id: str,
) -> list[WorkspaceFileInfoData] | None:
    """Authoritative manifest of the persistent files a sub wrote, read from
    the sub's session workspace.

    A sub may deliver its real output by writing workspace files and only
    summarising in its final message (SECRT-2377). This queries the sub's
    session for ``agent-created`` files directly, so it captures writes from
    *any* turn — including ones absent from the current turn's tool-call log
    (e.g. the cold-poll / already-terminal path in ``get_sub_session_result``).

    The ``origin=agent-created`` filter means only files the sub persisted via
    ``write_workspace_file`` are listed — transient working-directory artefacts
    (e.g. a ``git clone`` the sub inspects but never persists) are not workspace
    files and never appear here, and neither do the sub's own working files
    (its build scratchpad). When the sub persists more than
    ``_WORKSPACE_FILE_MANIFEST_LIMIT`` files, the most recently written ones win
    (the listing is ordered ``createdAt`` descending).

    Returns ``None`` on lookup failure so callers can fall back to mining the
    tool-call log; an empty list means the sub genuinely wrote nothing.
    """
    try:
        manager = await get_workspace_manager(user_id, inner_session_id)
        files = await manager.list_files(
            limit=_WORKSPACE_FILE_MANIFEST_LIMIT,
            metadata_equals=AGENT_CREATED_METADATA,
            include_internal=False,
        )
    except Exception:
        logger.warning(
            f"Failed to list workspace files for sub {inner_session_id[:12]}",
            exc_info=True,
        )
        return None
    return [
        WorkspaceFileInfoData(
            file_id=f.id,
            name=f.name,
            path=f.path,
            mime_type=f.mime_type,
            size_bytes=f.size_bytes,
        )
        for f in files
    ]


def _workspace_files_from_tool_calls(
    tool_calls: list[ToolCallEntry],
) -> list[WorkspaceFileInfoData]:
    """Mine the files a sub wrote from its tool-call log — the cheap fallback
    when the authoritative workspace listing is unavailable.

    ``write_workspace_file`` outputs already carry the fully session-qualified
    ``path`` (the workspace manager resolves it on write), so it is directly
    usable with ``read_workspace_file``. The output is an opaque
    ``WorkspaceWriteResponse`` payload — a JSON string on the live-drain path,
    a dict on the persisted-replay path — so we parse defensively and skip
    anything that doesn't carry the fields we need.
    """
    files: list[WorkspaceFileInfoData] = []
    seen_ids: set[str] = set()
    for tc in tool_calls:
        if tc.tool_name != "write_workspace_file" or tc.success is False:
            continue
        payload = _as_payload(tc.output)
        if payload is None:
            continue
        file_id = payload.get("file_id")
        path = payload.get("path")
        if not file_id or not path or file_id in seen_ids:
            continue
        seen_ids.add(file_id)
        name = payload.get("name") or path.rsplit("/", 1)[-1]
        # The log does not carry the mark, but this tool's writes are marked by
        # name and path alone, so the same rule gives the same answer.
        if is_internal_workspace_file(
            name=name, path=path, metadata=AGENT_CREATED_METADATA
        ):
            continue
        files.append(
            WorkspaceFileInfoData(
                file_id=file_id,
                name=name,
                path=path,
                mime_type=payload.get("mime_type") or "",
                size_bytes=_coerce_size_bytes(payload.get("size_bytes")),
            )
        )
    return files


def _coerce_size_bytes(raw: Any) -> int:
    """Coerce a mined ``size_bytes`` to a non-negative int — the payload is
    untrusted (JSON parsed from the tool log), so a missing, malformed, or
    negative value must not surface as invalid size data."""
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _as_payload(output: Any) -> dict[str, Any] | None:
    """Coerce a tool-call ``output`` (JSON string or dict) into a dict, or
    ``None`` when it isn't a usable object."""
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


# Outcomes meaning the turn never reached the sub's session, so the row we
# opened for it holds nothing. Notably NOT ``failed``: there the turn ran and
# errored, and its thread is the only record of that.
_NOTHING_QUEUED: frozenset[SessionOutcome] = frozenset(
    {"refused", "rejected_concurrent_turn_cap"}
)


async def discard_unused_sub_session(
    session_id: str, user_id: str, outcome: SessionOutcome
) -> bool:
    """Drop a thread opened for a turn that was then refused, reporting
    whether it is gone.

    A tree refusal or the concurrent-turn cap rejects the turn after the
    session row exists, leaving an empty conversation the user can open from
    their history. Callers use the return value to withhold the sub-session
    handles, which would otherwise point at a row that no longer exists.
    Best-effort by contract: a failed cleanup must not turn a refusal into an
    error, so it reports False and the handles stay valid.
    """
    if outcome not in _NOTHING_QUEUED:
        return False
    try:
        await delete_chat_session(session_id, user_id)
    except Exception:
        logger.warning(
            "Failed to clean up unused sub-session %s", session_id, exc_info=True
        )
        return False
    return True


def response_from_outcome(
    *,
    outcome: SessionOutcome,
    result: SessionResult,
    inner_session_id: str,
    parent_session_id: str | None,
    elapsed: float,
    workspace_files: list[WorkspaceFileInfoData] | None = None,
    actor: str = "Subtask",
) -> SubSessionStatusResponse:
    """Translate a ``(SessionOutcome, SessionResult)`` tuple into the
    ``SubSessionStatusResponse`` contract the LLM sees.

    ``actor`` names who ran the turn in the human-readable message — the
    default ``"Subtask"`` for a same-scope sub, or the delegate's name
    when the caller already knows it (e.g. ``delegate_to_expert``), so the
    message is built correctly once instead of via a post-hoc string
    substitution against this function's own wording.

    ``completed`` surfaces the aggregated response text, plus a manifest of
    any workspace files the sub wrote (SECRT-2377). Pass ``workspace_files``
    to supply the authoritative listing from the sub's session; when omitted,
    the files are mined from ``result.tool_calls`` as a fallback.
    ``failed`` returns the error marker with the same handles.
    ``running`` returns just the polling handles so the agent can resume.
    ``queued`` means the target session already had a turn in flight; the
    message was appended to its pending buffer and will be processed by
    the existing turn on its next drain.
    """
    link = _sub_session_link(inner_session_id)
    if outcome == "queued":
        return SubSessionStatusResponse(
            message=(
                f"Target session already had a turn in flight; the message "
                f"was queued ({result.pending_buffer_length} now pending) and "
                "will be processed by the existing turn on its next drain. "
                f"Call tool:get_sub_session_result to poll progress"
                f"{f' or watch live at {link}' if link else ''}."
            ),
            session_id=parent_session_id,
            status="queued",
            sub_session_id=inner_session_id,
            sub_autopilot_session_id=inner_session_id,
            sub_autopilot_session_link=link,
            elapsed_seconds=round(elapsed, 2),
        ).from_outside()

    if outcome == "queued_for_slot":
        return SubSessionStatusResponse(
            message=(
                f"{actor} is queued: the user's task slots for sub-work are all "
                "taken, and it starts on its own when one frees. Call "
                "tool:get_sub_session_result to wait for it or check on it"
                f"{f', or watch at {link}' if link else ''}."
            ),
            session_id=parent_session_id,
            status="queued",
            sub_session_id=inner_session_id,
            sub_autopilot_session_id=inner_session_id,
            sub_autopilot_session_link=link,
            elapsed_seconds=round(elapsed, 2),
        ).from_outside()

    if outcome == "running":
        return SubSessionStatusResponse(
            message=(
                f"{actor} is still running after {elapsed:.0f}s."
                f"{f' Watch live at {link}.' if link else ''} "
                "Call tool:get_sub_session_result (optionally with "
                "include_progress=true) to wait, poll, or inspect progress."
            ),
            session_id=parent_session_id,
            status="running",
            sub_session_id=inner_session_id,
            sub_autopilot_session_id=inner_session_id,
            sub_autopilot_session_link=link,
            elapsed_seconds=round(elapsed, 2),
        ).from_outside()

    if outcome == "refused":
        # The turn never started; the tree or the target said why.
        return SubSessionStatusResponse(
            message=result.refusal or f"{actor} could not start this task.",
            session_id=parent_session_id,
            status="error",
            sub_session_id=inner_session_id,
            sub_autopilot_session_id=inner_session_id,
            sub_autopilot_session_link=link,
            elapsed_seconds=round(elapsed, 2),
        ).from_outside()

    if outcome == "rejected_concurrent_turn_cap":
        # No sub-session record / transcript exists yet — the per-user
        # concurrent-turn cap rejected before ``create_session`` ran.
        # Render the actionable message instead of a "see transcript"
        # pointer to nothing.
        return SubSessionStatusResponse(
            message=spawn_limit_message(),
            session_id=parent_session_id,
            status="error",
            sub_session_id=inner_session_id,
            sub_autopilot_session_id=inner_session_id,
            sub_autopilot_session_link=link,
            elapsed_seconds=round(elapsed, 2),
        ).from_outside()

    if outcome == "failed":
        return SubSessionStatusResponse(
            message=f"{actor} failed. See the sub's transcript for details.",
            session_id=parent_session_id,
            status="error",
            sub_session_id=inner_session_id,
            sub_autopilot_session_id=inner_session_id,
            sub_autopilot_session_link=link,
            elapsed_seconds=round(elapsed, 2),
        ).from_outside()

    # completed — prefer the authoritative listing supplied by the caller;
    # fall back to mining the tool-call log when it's unavailable.
    if workspace_files is None:
        workspace_files = _workspace_files_from_tool_calls(result.tool_calls)
    message = f"{actor} completed.{f' View at {link}.' if link else ''}"
    if workspace_files:
        # The sub may have put its real output in files and only summarised in
        # `response`. Flag the files explicitly so the parent reads them rather
        # than treating the run as empty (SECRT-2377).
        message += (
            f" It wrote {len(workspace_files)} workspace file(s); read them via "
            "read_workspace_file(path=<read_path>) — see sub_workspace_files."
        )
    return SubSessionStatusResponse(
        message=message,
        session_id=parent_session_id,
        status="completed",
        sub_session_id=inner_session_id,
        sub_autopilot_session_id=inner_session_id,
        sub_autopilot_session_link=link,
        response=result.response_text,
        sub_tool_call_count=len(result.tool_calls),
        sub_workspace_files=workspace_files or None,
        elapsed_seconds=round(elapsed, 2),
    ).from_outside(result.response_text, workspace_files)

```
