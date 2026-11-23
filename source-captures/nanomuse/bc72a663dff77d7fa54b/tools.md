# nanoMuse — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## nanomuse/agent/core.py

Source: https://github.com/nano-muse/nanoMuse/blob/09850259cab00d27af83786f00faa09b5d8494bf/nanomuse/agent/core.py

SHA-256: `da8d097e0906a9720e5712aac3664ffacf40a9d242b8f16aca368473b9ccfd40`

```
"""The agent loop: think (LLM) → act (tools through the Sentinel) → repeat."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from nanomuse import prompts
from nanomuse.agent.context import fit_context
from nanomuse.calendar import CalendarFeeds
from nanomuse.config import Settings
from nanomuse.contacts import ContactBook
from nanomuse.goals import GoalStore
from nanomuse.llm import budget as task_budget
from nanomuse.llm import session as llm_session
from nanomuse.llm.base import BaseLLM
from nanomuse.llm.vision import keep_newest_images
from nanomuse.logger import logger
from nanomuse.memory import MemoryItem, MemoryStore
from nanomuse.runtime import device
from nanomuse.schema import AgentState, Attachment, Message, RiskLevel, Role, ToolResult, new_id
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.skills import SkillLibrary
from nanomuse.tools.base import ToolCollection
from nanomuse.ui import UI


class Incoming:
    """A user message on its way to the agent: the text, what was attached to it, the
    language of the client's screens (a BCP-47 tag, "" when the client did not say), and,
    for *Use nanoMuse Cloud this time*, the model client this one turn runs on (``llm``)
    and its id (``model``); None and "" for a turn on the configured model. ``run`` is what
    set the turn going, in the relay's word (``person``, ``schedule``, ``feed``, ``goal``,
    ``hub``; docs/cloud.md "Provenance")."""

    __slots__ = ("delivery", "files", "language", "llm", "model", "run", "text")

    def __init__(
        self,
        text: str,
        files: list[Attachment] | None = None,
        language: str = "",
        llm: BaseLLM | None = None,
        model: str = "",
        delivery: str = "",
        run: str = "person",
    ):
        self.text = text
        self.files = files or []
        self.language = language
        self.llm = llm
        self.model = model
        self.delivery = delivery
        self.run = run


class MuseAgent:
    def __init__(
        self,
        settings: Settings,
        llm: BaseLLM,
        tools: ToolCollection,
        sentinel: Sentinel,
        ui: UI,
        audit: AuditLog,
        memory: MemoryStore | None = None,
        goals: GoalStore | None = None,
        calendar: CalendarFeeds | None = None,
        contacts: ContactBook | None = None,
        skills: SkillLibrary | None = None,
        session_file: Path | None = None,
        conversation_id: str | None = None,
    ):
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.sentinel = sentinel
        self.ui = ui
        self.audit = audit
        self.memory = memory
        self.goals = goals
        self.calendar = calendar
        self.contacts = contacts
        self.skills = skills
        self.session_file = session_file
        # The conversation (thread) this agent is; approvals "for this conversation" are
        # bound to it and last as long as it does. Without one, each run stands alone.
        self.conversation_id = conversation_id
        # The id model calls carry for a vendor that wants one per conversation (OpenCode Go):
        # the thread's, else one of this agent's own for as long as it lives.
        self.session_id = conversation_id or llm_session.new_fallback()
        # the id the record keeps the latest model call under and where it was kept
        # (``cloud`` or ``own``; ""/"" before the first), for the notes about the turn
        self.last_call: tuple[str, str] = ("", "")
        self.messages: list[Message] = []
        self.state = AgentState.IDLE
        self.turns = 0
        # Optional queue of user messages that arrive *while* a run is in progress (the app
        # lets you interrupt or pile on requests). They are folded into the conversation
        # before the next model call instead of waiting for the current run to finish.
        self.inbox: asyncio.Queue[str | Incoming] | None = None
        self._told_no_vision = False
        self.last_budget: dict[str, Any] = {}
        self._task_start = 0
        # The language of the screens the person reads this conversation on, from the latest
        # message that said (a BCP-47 tag); the language rule names it ahead of the script
        # of one message. "" means no client has said, and the script decides.
        self.ui_language = ""
        # Words for this conversation alone, read when the system prompt is built (the first
        # conversation's phase, nanomuse/server/firstrun.py); None or "" adds nothing.
        self.prompt_addendum: Callable[[], str] | None = None

    def _drain_inbox(self) -> int:
        if self.inbox is None:
            return 0
        count = 0
        while not self.inbox.empty():
            # a look at the head before taking it: what must run as a turn of its own stays
            item = self.inbox._queue[0]  # type: ignore[attr-defined]
            incoming = item if isinstance(item, Incoming) else Incoming(item)
            if incoming.llm is not None or incoming.model or incoming.delivery:
                # A one-turn client belongs to the queued turn, not to the run that happens
                # to be in progress, and scheduled work (a reminder, a routine, a trigger)
                # is a background run with a purpose of its own that acknowledges its
                # source when it ends. Both wait for the worker to run them alone.
                break
            try:
                self.inbox.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
            if incoming.language:
                self.ui_language = incoming.language
            text = self._expand_skill(incoming.text)
            self.messages.append(self.user_message(text, incoming.files))
            self.audit.record("user_message", content=text, interjected=True)
            count += 1
        return count

    def _expand_skill(self, text: str) -> str:
        """A message that opens with a skill's slash command (``/weekly-review …``) gets
        the skill's instructions, whether it starts a turn or is folded into one already
        running."""
        if self.skills is not None and "skills" in self.tools:
            return self.skills.expand(text)
        return text

    def user_message(self, text: str, files: list[Attachment] | None = None) -> Message:
        """The user's message as the model gets it: the text, a line per attached file
        (path and what it is), and the pictures as images when the model takes them."""
        if not files:
            return Message(
                role=Role.USER,
                content=text,
                meta={"user_request": True, "request_id": new_id("message")},
            )
        lines = [f"- {a.path} ({a.describe()})" for a in files]
        note = "[Attached files]\n" + "\n".join(lines)
        if any(a.kind != "image" for a in files):
            note += "\nRead them with `files` action=read."
        content = f"{text}\n\n{note}" if text.strip() else note
        workspace = Path(self.settings.agent.workspace).expanduser()
        images = [str(workspace / a.path) for a in files if a.kind == "image"]
        return Message(
            role=Role.USER,
            content=content,
            images=images or None,
            meta={"user_request": True, "request_id": new_id("message")},
        )

    # ------------------------------------------------------------------ prompt
    def sandbox_note(self) -> str:
        """How commands run, from the shell tool's sandbox (the tools own it)."""
        shell = self.tools.get("shell")
        box = getattr(shell, "sandbox", None)
        if box is not None and box.active:
            note = box.describe().replace("Commands run", "Commands (shell, python_execute) run")
            if box.blocks_network:
                note += (
                    " Commands that need the network say so by what they run (curl, pip, git, a "
                    "URL, a script that imports requests …); otherwise pass network=true."
                )
            return note
        return "Commands (shell, python_execute) run in the workspace with a scrubbed environment."

    def skills_section(self) -> str:
        """The index of skills — names and descriptions; the tool has the instructions."""
        lib = self.skills
        if lib is None or "skills" not in self.tools:
            return ""
        index = lib.index()
        if not index:
            return ""
        return prompts.SKILLS_SECTION.format(items=index)

    def phone_section(self) -> str:
        """The phone, when GUI operation is on: connected or not, and what is on it."""
        task = self.tools.get("phone_task")
        link = getattr(task, "link", None)
        if task is None or link is None:
            return ""
        device = link.device
        if device is None:
            status = (
                "- GUI operation is on, but no phone is connected right now: the `phone_*` tools "
                "will fail until the user opens the nanoMuse app on the phone (or the MobileGym "
                "module). Say so if a step needs the phone."
            )
        else:
            apps = device.app_list()
            status = f"- The user's phone is connected: {device.name} ({device.platform})."
            if apps:
                status += f" Apps on it: {apps}."
        return prompts.PHONE_SECTION.format(status=status)

    def device_section(self) -> str:
        """The phone this runs on, when its capabilities are there as `device__*` tools."""
        from nanomuse.runtime import DEVICE_PREFIX

        names = sorted(
            t.name[len(DEVICE_PREFIX) :] for t in self.tools if t.name.startswith(DEVICE_PREFIX)
        )
        if not names:
            return ""
        return prompts.DEVICE_SECTION.format(tools=", ".join(names))

    def computer_section(self) -> str:
        """This computer's screen, when the hands are on: available or not, and what is in front.
        On a computer with the hands off, one line saying where the switch is — so a request
        for a desktop application gets the way to it, not a shrug."""
        task = self.tools.get("computer_task")
        link = getattr(task, "link", None)
        if task is None or link is None:
            if device() is None and not self.settings.hands.enabled:
                return prompts.COMPUTER_OFF_SECTION
            return ""
        status = link.status() if hasattr(link, "status") else {}
        if not status.get("available", True):
            reason = status.get("reason") or "no backend"
            status_line = (
                "- The hands are turned on, but they cannot drive this computer right now "
                f"({reason}): the `computer_*` tools will fail. Say so if a step needs the screen."
            )
        else:
            last = link.last_screen
            status_line = (
                f"- The hands can drive this computer ({status.get('backend') or 'hands'} on "
                f"{link.device.platform}, screen {link.device.width}×{link.device.height})."
            )
            if last is not None and last.title:
                status_line += f" In front last time: {last.title}."
        return prompts.COMPUTER_SECTION.format(status=status_line)

    def devices_section(self) -> str:
        """The user's other devices, when this computer is on the hub (the `devices` tool)."""
        tool = self.tools.get("devices")
        hub = getattr(tool, "hub", None)
        if tool is None or hub is None:
            return ""
        others = hub.others()
        if not getattr(hub, "client", None) or not hub.client.connected.is_set():
            status = (
                "- This computer is not connected to the nanoMuse hub right now, so the other "
                "devices cannot be reached; say so if a step needs one."
            )
        elif not others:
            status = (
                f"- This computer is on the hub as “{hub.device_name}”; no other device of the "
                "user's has joined yet."
            )
        else:
            listed = ", ".join(
                f"{d.get('name')} ({d.get('kind') or 'device'}, "
                f"{'online' if d.get('online') else 'offline'})"
                for d in others
            )
            status = f"- This computer is “{hub.device_name}”. Other devices: {listed}."
        return prompts.DEVICES_SECTION.format(status=status)

    def contacts_note(self) -> str:
        """One line on the address book, when there is one (the tool does the looking up)."""
        book = self.contacts
        if book is None or "contacts" not in self.tools or not book.configured:
            return ""
        n = len(book)
        return (
            f"- Address book: {n} {'person' if n == 1 else 'people'}; look someone up with "
            "`contacts` before writing to them; never guess an address\n"
        )

    async def recall_for(self, user_input: str) -> list[MemoryItem] | None:
        """The memories to put in the system prompt for this message — by meaning too,
        when an embedding endpoint is set up (that is the awaited part)."""
        if self.memory is None or not self.settings.memory.enabled:
            return None
        try:
            return await self.memory.relevant_async(
                user_input, limit=self.settings.memory.max_inject
            )
        except Exception as exc:  # noqa: BLE001 — recall must never stop a turn
            logger.warning("recall failed ({}); using the keyword ranking", exc)
            return self.memory.relevant(user_input, limit=self.settings.memory.max_inject)

    def language_rule(self, user_input: str) -> str:
        """The `## Language` paragraph of the system prompt. A reply language set under
        Settings wins; else the language of the client's screens when a client has said
        (``ui_language``); else the script of the message."""
        fixed = self.settings.agent.language
        if fixed not in ("", "auto"):
            return prompts.LANGUAGE_FIXED.format(language=fixed)
        detected = prompts.detect_language(user_input)
        ui = prompts.language_name(self.ui_language)
        if ui:
            return prompts.LANGUAGE_UI.format(language=ui, detected=detected)
        return prompts.LANGUAGE_AUTO.format(detected=detected)

    def build_system_prompt(
        self, user_input: str, memories_: list[MemoryItem] | None = None
    ) -> str:
        """``memories_`` is what ``recall_for`` returned; without it the keyword ranking is
        used on the spot."""
        a = self.settings.agent
        language_rule = self.language_rule(user_input)
        memories = ""
        if self.memory is not None and self.settings.memory.enabled:
            items = (
                memories_
                if memories_ is not None
                else self.memory.relevant(user_input, limit=self.settings.memory.max_inject)
            )
            if items:
                memories = prompts.MEMORY_SECTION.format(
                    items="\n".join(f"- {m.render()}" for m in items)
                )
                if self.turns <= 1:
                    memories += prompts.MEMORY_OPENING_NOTE
        goals = ""
        if self.goals is not None:
            active = self.goals.list("active")[:5]
            if active:
                lines = []
                for g in active:
                    nxt = g.next_step
                    bits = [f"progress {g.progress}"]
                    if g.category:
                        bits.append(g.category)
                    if g.due:
                        bits.append(f"due {g.due}" + (", overdue" if g.overdue else ""))
                    if nxt:
                        bits.append(f"next: {nxt.idx}. {nxt.title}")
                    if g.proposal:
                        bits.append("a plan change is awaiting the user's answer")
                    lines.append(f"- {g.id}: {g.title} ({', '.join(bits)})")
                goals = prompts.GOALS_SECTION.format(items="\n".join(lines))
        calendar = ""
        if self.calendar is not None and self.calendar.configured:
            # today and tomorrow, from the cache — the tool fetches when more is needed
            today = datetime.now(self.calendar.tz).date()
            events = self.calendar.agenda(today, 2)
            calendar = prompts.CALENDAR_SECTION.format(
                items=self.calendar.render(events, today)
                if events
                else "(nothing on the calendar today or tomorrow)"
            )
        profile = (
            prompts.USER_PROFILE_SECTION.format(profile=a.user_profile.strip())
            if a.user_profile.strip()
            else ""
        )
        extra = (
            f"\n## Additional instructions\n{a.instructions.strip()}\n"
            if a.instructions.strip()
            else ""
        )
        addendum = (self.prompt_addendum() if self.prompt_addendum is not None else "").strip()
        if addendum:
            extra += f"\n## This conversation\n{addendum}\n"
        return prompts.SYSTEM_PROMPT.format(
            name=a.name,
            language_rule=language_rule,
            now=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%A, UTC%z)"),
            workspace=str(a.workspace.resolve()),
            sentinel_mode=self.settings.sentinel.mode,
            sandbox=self.sandbox_note(),
            contacts=self.contacts_note(),
            tool_names=", ".join(t.name for t in self.tools),
            user_profile=profile,
            memories=memories,
            goals=goals
            + calendar
            + self.device_section()
            + self.phone_section()
            + self.computer_section()
            + self.devices_section()
            + self.skills_section(),
            extra=extra,
        )

    # ------------------------------------------------------------------ context window
    def context_messages(self) -> list[Message]:
        """The window the model sees: the newest ``max_context_messages`` (never starting
        inside a tool exchange), with pictures only on the newest ``max_context_images``
        image-bearing messages — a hands session would otherwise send every screenshot of
        the run on every step (C9). Copies where a picture was dropped; the history itself
        keeps them."""
        limit = self.settings.agent.max_context_messages
        msgs = self.messages
        if len(msgs) <= limit:
            window = list(msgs)
        else:
            cutoff = len(msgs) - limit
            # Never start in the middle of a tool exchange: advance to the next user message.
            # The step-limit wrap-up prompt is not a boundary: cutting there would leave the
            # next turn only the wrap-up, none of the work it sums up.
            while cutoff < len(msgs) and (
                msgs[cutoff].role != Role.USER or msgs[cutoff].meta.get("wrap_up")
            ):
                cutoff += 1
            if cutoff >= len(msgs) or all(m.role != Role.USER for m in msgs[cutoff + 1 :]):
                # A single long turn can fill the window, and after one the next user
                # message is the only boundary left: cutting there would leave the model
                # nothing of the work before it. Keep the newest messages instead, with the
                # leading tool exchange whole, even if that takes a few over the limit.
                cutoff = slice(-limit, None).indices(len(msgs))[0]
                while 0 < cutoff < len(msgs) and msgs[cutoff].role == Role.TOOL:
                    cutoff -= 1
            window = list(msgs[cutoff:])
        return keep_newest_images(window, self.settings.agent.max_context_images)

    # ------------------------------------------------------------------ stuck detection
    def _is_stuck(self) -> bool:
        assistants = [m for m in self.messages if m.role == Role.ASSISTANT][-3:]
        if len(assistants) < 3:
            return False

        def sig(m: Message) -> str:
            calls = [(tc.function.name, tc.function.arguments) for tc in (m.tool_calls or [])]
            return json.dumps([m.content, calls], ensure_ascii=False, sort_keys=True)

        return len({sig(m) for m in assistants}) == 1

    # ------------------------------------------------------------------ main loop
    async def run(
        self,
        user_input: str,
        purpose: str | None = None,
        files: list[Attachment] | None = None,
        language: str = "",
        run: str = "person",
    ) -> str:
        if self.state == AgentState.RUNNING:
            raise RuntimeError("agent is already running")
        token = task_budget.begin(self.settings.agent.budget, inherit=False)
        budget = task_budget.current()
        assert budget is not None
        self._task_start = len(self.messages)
        deadline = asyncio.timeout(self.settings.agent.budget.max_seconds or None)
        try:
            async with deadline:
                return await self._run(
                    user_input, purpose=purpose, files=files, language=language, run=run
                )
        except (task_budget.BudgetExceeded, TimeoutError) as exc:
            if isinstance(exc, TimeoutError) and not deadline.expired():
                raise
            reason = exc.reason if isinstance(exc, task_budget.BudgetExceeded) else "time"
            budget.stopped = reason
            reply_language = language or self.ui_language or self.settings.agent.language
            if reply_language in ("", "auto"):
                reply_language = "zh" if prompts.detect_language(user_input) == "Chinese" else "en"
            final = task_budget.BudgetExceeded(reason).message(reply_language)
            self._repair_dangling_tool_calls(reason=final)
            self.messages.append(Message.assistant(final))
            self.ui.on_assistant_message(final, None)
            self.state = AgentState.FINISHED
            self._save_session()
            return final
        finally:
            try:
                self.last_budget = budget.snapshot()
                # a line in the audit log when a budget is in force or was reached; a task
                # with no limits set leaves the log as it was
                if budget.limited or budget.stopped:
                    self.audit.record("task_budget", **self.last_budget)
            finally:
                task_budget.end(token)

    def _previous_requests(self) -> list[Message]:
        """The requests of the turn before this one. "Carry on" or "send that report" means
        nothing once a long turn has pushed them out of the window."""
        earlier = self.messages[: self._task_start]
        starts = [i for i, m in enumerate(earlier) if m.meta.get("turn_start")]
        if not starts:
            # history saved before turns were marked: its last request
            return [m for m in earlier if m.meta.get("user_request")][-1:]
        return [m for m in earlier[starts[-1] :] if m.meta.get("user_request")]

    def _request_context(
        self,
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
        tail: Message | None = None,
    ) -> list[Message]:
        window = self.context_messages()
        protected = self._previous_requests() + [
            m for m in self.messages[self._task_start :] if m.meta.get("user_request")
        ]
        # A long turn pushes the request out of the message window; without it the model
        # forgets what it was asked and guesses, so it stays in view whatever the limits,
        # and so do the previous turn's.
        present = {m.meta.get("request_id") for m in window}
        window = [m for m in protected if m.meta.get("request_id") not in present] + window
        if tail:
            window.append(tail)
        # pinned requests may carry pictures: the cap counts them too
        window = keep_newest_images(window, self.settings.agent.max_context_images)
        window = fit_context(
            [Message.system(system_prompt), *window],
            tools,
            self.settings.agent.max_context_tokens,
            protected,
        )
        while True:
            visible = {call.id for message in window for call in message.tool_calls or []}
            ledger = self._task_ledger(visible)
            if ledger is None:
                return window
            # The note can evict another exchange. Recompute from the retained window
            # until the action IDs stop changing; removed messages never reenter.
            position = len(window) - 1 if tail is not None and window[-1] is tail else len(window)
            candidate = window[:position] + [ledger] + window[position:]
            fitted = fit_context(
                candidate,
                tools,
                self.settings.agent.max_context_tokens,
                protected + [ledger],
            )
            remaining = {call.id for message in fitted for call in message.tool_calls or []}
            if remaining == visible:
                return fitted
            window = [message for message in fitted if message is not ledger]

    def _task_ledger(self, visible: set[str]) -> Message | None:
        """The sensitive actions and file writes whose calls are no longer in view.
        Out of the window, the model sends the same mail again. Files
        read are left out: listing their names did not cut rereads."""
        pending: dict[str, tuple[int, Any]] = {}
        succeeded: list[tuple[int, Any]] = []
        for index, message in enumerate(self.messages):
            if message.role == Role.ASSISTANT:
                for call in message.tool_calls or []:
                    pending[call.id] = (index, call)
            elif message.role == Role.TOOL and message.tool_call_id in pending:
                entry = pending.pop(message.tool_call_id)
                if message.meta.get("tool_ok") is True and message.tool_call_id not in visible:
                    succeeded.append(entry)

        done: list[str] = []
        for _, call in sorted(succeeded, key=lambda entry: entry[0]):
            tool = self.tools.get(call.name)
            if tool is None:
                continue
            args = call.arguments
            action = args.get("action") if call.name == "files" else None
            try:
                assessment = tool.assess(args)
                risk, summary = assessment.risk, assessment.summary
            except Exception:  # noqa: BLE001
                risk, summary = tool.risk, tool.name
            if risk == RiskLevel.SENSITIVE or action in ("write", "append"):
                done.append(summary)
        if not done:
            return None
        lines = ["[Runtime note, not from the user] Earlier in this conversation, out of view now:"]
        lines.append("Done:")
        if len(done) > 60:
            lines.append(f"({len(done) - 60} older actions omitted)")
        lines.extend(f"- {summary}" for summary in done[-60:])
        lines.append("Do not repeat an action listed as done unless the user asks for it again.")
        return Message.user("\n".join(lines))

    async def _run(
        self,
        user_input: str,
        purpose: str | None = None,
        files: list[Attachment] | None = None,
        language: str = "",
        run: str = "person",
    ) -> str:
        """One task: a user message (or a background prompt) worked to completion.

        ``purpose`` is what approval cards show as the reason for an action; it defaults
        to the message itself. ``files`` are the attachments that came with the message.
        ``language`` is the BCP-47 tag of the client's screens when the client sent one;
        it is remembered for the conversation and names the reply language ahead of the
        script of the message. ``run`` is what set the turn going (``person`` by default;
        ``schedule``, ``feed``, ``goal``, ``hub`` for the runs nobody typed), told to the
        relay with every call of the turn. Task-scoped approvals end when this call returns.
        """
        if self.state == AgentState.RUNNING:
            raise RuntimeError("agent is already running")
        self.state = AgentState.RUNNING
        self.turns += 1
        if language:
            self.ui_language = language
        user_input = self._expand_skill(user_input)
        request = self.user_message(user_input, files)
        request.meta["turn_start"] = True
        self.messages.append(request)
        attached = {"files": [a.path for a in files]} if files else {}
        self.audit.record("user_message", content=user_input, **attached)
        system_prompt = self.build_system_prompt(user_input, await self.recall_for(user_input))
        tool_params = self.tools.to_params()
        final: str | None = None
        step = 0
        empty_replies = 0
        task_token = self.sentinel.begin_task(
            purpose or user_input, conversation=self.conversation_id, execution_llm=self.llm
        )
        session_token = llm_session.enter(self.session_id)
        turn_token = llm_session.begin_turn(run, self.turns)
        try:
            while step < self.settings.agent.max_steps:
                step += 1
                if self._drain_inbox():
                    logger.debug("folded queued user message(s) into the running turn")
                    # the language rule and the memory section follow the latest message
                    latest = self.messages[-1].content or ""
                    system_prompt = self.build_system_prompt(latest, await self.recall_for(latest))
                context = self._request_context(system_prompt, tool_params)
                response = await task_budget.ask(
                    self.llm,
                    context,
                    tools=tool_params,
                    on_delta=self.ui.on_text_delta,
                    on_reasoning_delta=self.ui.on_reasoning_delta,
                )
                if (
                    response.finish_reason == "length"
                    and not response.content
                    and not response.tool_calls
                ):
                    # the model thought until the budget ran out (reasoning models do on a
                    # hard step): the same call once more, with room to think and answer
                    logger.info("reply cut off before it began; asking again with more room")
                    response = await task_budget.ask(
                        self.llm,
                        context,
                        tools=tool_params,
                        on_delta=self.ui.on_text_delta,
                        on_reasoning_delta=self.ui.on_reasoning_delta,
                        max_tokens=self.llm.roomier_max_tokens(),
                    )
                if response.request_id:
                    self.last_call = (response.request_id, response.turn_source)
                if self.llm.vision_available is False and not self._told_no_vision:
                    if any(m.images for m in self.messages if m.role == Role.USER):
                        self._told_no_vision = True
                        self.ui.warn(
                            "This model does not take images, so the picture was described to "
                            "it by name only. It stays in the workspace; a model with vision "
                            "(Settings → Connections) would see it."
                        )
                assistant = response.to_message()
                assistant.meta.update({"step": step, "usage": response.usage})
                self.messages.append(assistant)
                self.ui.on_assistant_message(response.content, response.reasoning)
                self.audit.record(
                    "assistant_message",
                    step=step,
                    content=response.content or "",
                    tool_calls=[tc.function.name for tc in response.tool_calls],
                    usage=response.usage,
                )

                if not response.tool_calls:
                    if response.content:
                        final = response.content
                        break
                    empty_replies += 1
                    if empty_replies >= 2:
                        final = ""
                        break
                    self.messages.append(
                        Message.user(
                            "(Your reply was empty. Continue the task, or call `terminate` if it is done.)"
                        )
                    )
                    continue

                stop = False
                pictures: list[str] = []
                for call in response.tool_calls:
                    budget = task_budget.current()
                    if budget is not None:
                        budget.check()
                    tool = self.tools.get(call.name)
                    if tool is None:
                        summary = f"{call.name}(?)"
                    elif "__raw__" in call.arguments:
                        raw = str(call.arguments["__raw__"])
                        summary = f"{call.name}: arguments cut off ({len(raw)} chars)"
                    else:
                        try:
                            summary = tool.assess(call.arguments).summary
                        except Exception as exc:  # noqa: BLE001
                            # the gate's assess will fail the same way, as a tool result
                            summary = f"{call.name}: bad arguments ({type(exc).__name__})"
                    self.ui.on_tool_call(call, summary)
                    if tool is None:
                        result = ToolResult.fail(
                            f"unknown tool '{call.name}'. Available tools: {', '.join(t.name for t in self.tools)}"
                        )
                    else:
                        result = await self.sentinel.guard(call, tool)
                    self.ui.on_tool_result(call, result)
                    tool_message = Message.tool(result.for_model(), call.id, call.name)
                    tool_message.meta["tool_ok"] = result.ok  # read by the task ledger
                    self.messages.append(tool_message)
                    if result.images:
                        pictures.extend(result.images)
                    if result.stop:
                        final = result.output
                        stop = True
                if stop:
                    break
                if (
                    pictures
                    and self.llm.vision_available is not False
                    and self.settings.llm.vision != "off"
                ):
                    # A tool result cannot carry an image, so the picture (the phone's screen)
                    # follows as a user message; models without vision get the text alone.
                    self.messages.append(
                        Message.user(
                            "[The screenshot that goes with the tool result above.]",
                            images=pictures[-2:],
                        )
                    )
                if self._is_stuck():
                    logger.warning("agent seems stuck; nudging")
                    self.messages.append(Message.user(prompts.STUCK_PROMPT))
            else:
                # Step budget exhausted: ask for a wrap-up without tools.
                # The window is taken before the prompt joins the history: as the newest
                # user message it would be the cutoff, and the wrap-up would see no work.
                wrap_up = Message.user(prompts.MAX_STEPS_PROMPT)
                wrap_up.meta["wrap_up"] = True
                context = self._request_context(system_prompt, None, wrap_up)
                self.messages.append(wrap_up)
                response = await task_budget.ask(
                    self.llm,
                    context,
                    tools=None,
                    on_delta=self.ui.on_text_delta,
                    on_reasoning_delta=self.ui.on_reasoning_delta,
                )
                self.messages.append(response.to_message())
                self.ui.on_assistant_message(response.content, response.reasoning)
                final = response.content or "(step limit reached)"
            self.state = AgentState.FINISHED
        except Exception:
            self.state = AgentState.ERROR
            raise
        finally:
            llm_session.end_turn(turn_token)
            llm_session.leave(session_token)
            self.sentinel.end_task(task_token)
            self._save_session()
        return final or ""

    # ------------------------------------------------------------------ session persistence
    def reset(self) -> None:
        self.messages.clear()
        # only this conversation: the taint of the chats running beside this one is theirs
        self.sentinel.untaint(self.conversation_id or "")
        self.state = AgentState.IDLE

    def _save_session(self) -> None:
        if not self.session_file:
            return
        temporary: Path | None = None
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {
                "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "turns": self.turns,
                "messages": [m.model_dump(mode="json") for m in self.messages],
            }
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.session_file.parent,
                prefix=f".{self.session_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.session_file)
            temporary = None
        except OSError as exc:  # pragma: no cover
            logger.warning("could not save session: {}", exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_session(self, path: Path) -> int:
        data = json.loads(Path(path).read_text("utf-8"))
        self.messages = [Message.model_validate(m) for m in data.get("messages", [])]
        self.turns = int(data.get("turns", 0))
        self._repair_dangling_tool_calls()
        return len(self.messages)

    def _repair_dangling_tool_calls(self, reason: str = "") -> None:
        """A restart in the middle of a tool call (say, while an approval was pending)
        leaves an assistant message whose tool calls have no results. Providers reject
        that history outright, so give every orphan a result that says what happened."""
        repaired: list[Message] = []
        i = 0
        while i < len(self.messages):
            msg = self.messages[i]
            repaired.append(msg)
            i += 1
            if msg.role != Role.ASSISTANT or not msg.tool_calls:
                continue
            answered: set[str] = set()
            while i < len(self.messages) and self.messages[i].role == Role.TOOL:
                repaired.append(self.messages[i])
                answered.add(self.messages[i].tool_call_id or "")
                i += 1
            for call in msg.tool_calls:
                if call.id not in answered:
                    repaired.append(
                        Message.tool(
                            reason
                            or "(no result: the app was restarted before this call finished)",
                            call.id,
                            call.function.name,
                        )
                    )
                    if reason:
                        self.ui.on_tool_result(call, ToolResult.fail(reason))
        if len(repaired) != len(self.messages):
            logger.info("repaired {} unanswered tool call(s)", len(repaired) - len(self.messages))
            self.messages = repaired


__all__ = ["MuseAgent"]

```

## nanomuse/tools/coding_tool.py

Source: https://github.com/nano-muse/nanoMuse/blob/09850259cab00d27af83786f00faa09b5d8494bf/nanomuse/tools/coding_tool.py

SHA-256: `1fc5e6996490efaded5c99e2ec2b8045adc4c0410bb71b93e9736c6696430d8a`

```
"""``coding_agents``: the user's Cursor / Codex / Claude Code sessions, here or on another
computer of theirs, and a way to send a message into one.

The Muse uses it when the person asks what their coding agent is doing ("is Cursor done
with the refactor?"), wants the gist of a session, or wants to tell the agent something
from wherever they are ("tell Codex to also add tests"). Reading is safe; sending a message
starts work on that computer, so it is moderate risk and shows up as an approval when the
sentinel asks for one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

if TYPE_CHECKING:
    from nanomuse.coding.service import CodingService


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


class CodingAgents(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "coding_agents"
    description: str = (
        "The user's coding agents (Cursor, Codex, Claude Code, DeepSeek Harness as `dsh`) on "
        "this computer or on another of their computers (`device`). Actions: `agents` (which "
        "are installed, how many are "
        "running), `sessions` (recent chats: title, workspace, when, whether one is running now; "
        "optional `agent`, `workspace`), `session` (one chat's transcript; needs `agent` and "
        "`session_id`), `send` (a message into a chat: `agent`, `text`, and `session_id` to "
        "continue one or `workspace` to start a new one; the agent works on it there and the "
        "reply comes back when it is done), `runs` (messages sent this way and how they went), "
        "`stop` (`run_id`). Prefer `sessions` then `session` to answer questions about what an "
        "agent did; `send` only when the user asks to tell the agent something."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["agents", "sessions", "session", "send", "runs", "stop"],
            },
            "agent": {"type": "string", "enum": ["cursor", "codex", "claude", "dsh"]},
            "session_id": {"type": "string"},
            "workspace": {
                "type": "string",
                "description": "a project folder, to filter or to start in",
            },
            "text": {"type": "string", "description": "for send: the message, in the user's words"},
            "run_id": {"type": "string"},
            "device": {
                "type": "string",
                "description": "another computer of the user's, by the name `devices` lists; omit for this one",
            },
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    coding: Any  # CodingService

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        a = super().assess(args)
        action = str(args.get("action") or "?")
        agent = str(args.get("agent") or "")
        where = f" on {args['device']}" if args.get("device") else ""
        if action == "send":
            # the agent then runs with the permissions it has on that computer (edits,
            # commands, the network): the same level as `shell`, as docs/coding-agents.md says
            a.risk = RiskLevel.SENSITIVE
            folder = f" in {args['workspace']}" if args.get("workspace") else ""
            a.summary = (
                f"tell {agent or 'the coding agent'}{where}{folder}: "
                f"{str(args.get('text') or '')[:60]}"
            )
        elif action == "stop":
            a.risk = RiskLevel.MODERATE
            a.summary = f"stop coding run {args.get('run_id', '')}{where}"
        else:
            a.summary = f"coding agents {action}{(' ' + agent) if agent else ''}{where}"
        return a

    async def execute(
        self,
        action: str = "",
        agent: str = "",
        session_id: str = "",
        workspace: str = "",
        text: str = "",
        run_id: str = "",
        device: str = "",
        limit: int = 0,
        **_: Any,
    ) -> ToolResult:
        from nanomuse.coding.service import CodingError

        coding: CodingService = self.coding
        try:
            if device:
                return await self._remote(
                    coding, device, action, agent, session_id, workspace, text, run_id, limit
                )
            if action == "agents":
                return ToolResult(output=_json({"agents": coding.agents()}))
            if action == "sessions":
                rows = coding.sessions(agent or None, limit or 15, workspace or None)
                slim = [
                    {
                        k: s[k]
                        for k in (
                            "agent",
                            "id",
                            "title",
                            "workspace",
                            "updated_at",
                            "status",
                            "messages",
                            "last_assistant",
                        )
                    }
                    for s in rows
                ]
                return ToolResult(output=_json({"sessions": slim}))
            if action == "session":
                if not (agent and session_id):
                    return ToolResult.fail("`agent` and `session_id` are required")
                s = coding.session(agent, session_id)
                s["transcript"] = s.get("transcript", [])[-40:]
                return ToolResult(output=_json(s))
            if action == "send":
                if not (agent and text):
                    return ToolResult.fail("`agent` and `text` are required")
                run = await coding.send(
                    agent, text, session_id=session_id, workspace=workspace, wait=True
                )
                return ToolResult(output=_json(_slim_run(run)))
            if action == "runs":
                return ToolResult(
                    output=_json({"runs": [_slim_run(r) for r in coding.list_runs(limit or 10)]})
                )
            if action == "stop":
                if not run_id:
                    return ToolResult.fail("`run_id` is required")
                return ToolResult(output=_json({"stopped": coding.stop(run_id)}))
            return ToolResult.fail(f"unknown action {action!r}")
        except CodingError as exc:
            return ToolResult.fail(f"{exc.message} ({exc.code})")

    async def _remote(
        self,
        coding: CodingService,
        device: str,
        action: str,
        agent: str,
        session_id: str,
        workspace: str,
        text: str,
        run_id: str,
        limit: int,
    ) -> ToolResult:
        args: dict[str, Any] = {
            "agent": agent,
            "session_id": session_id,
            "workspace": workspace,
            "limit": limit or None,
        }
        if action == "send":
            args.update({"text": text, "wait": True})
        if action == "stop":
            args["run"] = run_id
        result = await coding.remote(
            device, f"coding.{action}", {k: v for k, v in args.items() if v}
        )
        if action == "session" and isinstance(result.get("transcript"), list):
            result["transcript"] = result["transcript"][-40:]
        if action == "sessions" and isinstance(result.get("sessions"), list):
            result["sessions"] = [
                {
                    k: s.get(k)
                    for k in (
                        "agent",
                        "id",
                        "title",
                        "workspace",
                        "updated_at",
                        "status",
                        "messages",
                        "last_assistant",
                    )
                }
                for s in result["sessions"]
            ]
        return ToolResult(output=_json(result))


def _slim_run(run: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: run.get(k)
        for k in (
            "id",
            "agent",
            "session_id",
            "status",
            "resumed",
            "error",
            "tools",
            "started_at",
            "ended_at",
        )
    }
    out["output"] = str(run.get("output") or "")[-4000:]
    return out


__all__ = ["CodingAgents"]

```

## nanomuse/tools/files.py

Source: https://github.com/nano-muse/nanoMuse/blob/09850259cab00d27af83786f00faa09b5d8494bf/nanomuse/tools/files.py

SHA-256: `0c814af1f0338370e16de7411284d7afa43edebc5fc6c0fbe9b7db027eaa29c8`

```
"""Workspace file operations (read / write / append / list / search).

Paths are resolved inside the agent workspace. Reading outside of it is allowed
only for explicitly configured extra roots and is treated as *private data*.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
from pathlib import Path, PureWindowsPath
from typing import Any

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.stdio import decode_output
from nanomuse.tools.base import BaseTool, CallAssessment, short_json

MAX_READ_CHARS = 60_000
# a character takes at most four bytes (UTF-8) and two (UTF-16), so this many bytes always
# hold as many characters as a report keeps: the cut lands between characters, not inside one
_BYTES_PER_CHAR = 4


def read_capped_text(path: Path, limit: int) -> str:
    """The first ``limit`` characters of a file, read the way the shell tool reads a command's
    output: a byte-order mark decides, then UTF-8, then the console's code page.

    A file often is a command's output: ``dir > out.txt`` on PowerShell 5 writes UTF-16LE,
    ``reg export`` too, and a redirect on a Chinese console writes code page 936. Read as
    UTF-8, all three reached the model as NULs and replacement characters.
    """
    window = limit * _BYTES_PER_CHAR + 4
    with path.open("rb") as fh:
        data = fh.read(window)
    if len(data) == window:
        data = whole_utf8(data)
    return decode_output(data)


def whole_utf8(data: bytes) -> bytes:
    """``data`` without a UTF-8 sequence a cut left unfinished at its end: a lead byte of a
    two-, three- or four-byte character followed by fewer continuation bytes than it needs.

    Without this, a UTF-8 file longer than the window stopped being UTF-8 to the strict first
    read, and a console code page that accepts any byte (cp1252) read the whole file as
    mojibake. A cut through anything else (ASCII, UTF-16, a code page) is left as it is.
    """
    for back in range(1, 4):
        if back > len(data):
            return data
        byte = data[-back]
        if 0x80 <= byte <= 0xBF:  # a continuation byte: the lead is further back
            continue
        if 0xC2 <= byte <= 0xF4:  # a lead byte: whole only if its continuation bytes all follow
            need = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return data[:-back] if need > back else data
        return data  # ASCII, or a byte UTF-8 never uses: not a cut character
    return data


def unescape_flat(content: str) -> tuple[str, bool]:
    """Turn a one-line text full of literal ``\\n`` into lines.

    Small models double-escape newlines in JSON arguments, so a three-day itinerary
    arrives as one line with ``\\n`` between the days. Only text that has *no* real
    newline and at least two escaped ones is touched — code has real newlines, and a
    one-liner that genuinely contains ``\\n`` twice is not something people write.
    """
    if "\n" in content or content.count("\\n") < 2:
        return content, False
    return content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t"), True


def pdf_text(path: Path, max_pages: int = 60) -> str:
    """The text of a PDF, page by page — what the model reads when the user attaches one.
    Scanned PDFs have no text layer; the result says so instead of coming back empty."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return "(this PDF is encrypted; the text cannot be read without its password)"
        pages = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — a damaged file is a result, not a crash
        return f"(this PDF could not be read: {type(exc).__name__}: {str(exc)[:200]})"
    parts: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages], start=1):
        try:
            content = (page.extract_text() or "").strip()
        except Exception as exc:  # noqa: BLE001
            content = f"(page could not be read: {exc})"
        parts.append(f"--- page {i} of {pages} ---\n{content}")
    if pages > max_pages:
        parts.append(f"... [{pages - max_pages} more pages not shown]")
    text = "\n\n".join(parts)
    if not any(p.split("---\n", 1)[-1].strip() for p in parts):
        return (
            f"(this PDF has {pages} page{'s' if pages != 1 else ''} but no text layer; it is "
            "probably scanned images; the text cannot be extracted here)"
        )
    return text


# an unbounded `search` over a home directory would walk for minutes; this is plenty
SEARCH_MAX_ENTRIES = 50_000
# how many a report carries before it stops: a listing of a build folder is thousands of
# lines the model cannot use, and a cut list it believes is whole is worse than a short one
LIST_MAX = 500
SEARCH_MAX_MATCHES = 200


_WIN_DEVICE_NAMES = frozenset(
    [
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        *(f"COM{i}" for i in "¹²³"),
        *(f"LPT{i}" for i in "¹²³"),
    ]
)


def windows_rejects(name: str) -> str | None:
    """Why a path component is a device or would be normalized by Windows.

    Win32 reserves device names with extensions too, such as ``NUL.txt``. Refuse them
    consistently across supported Windows versions, along with trailing dots and spaces.
    """
    if name in (".", ".."):
        return None
    upper = name.split(".", 1)[0].rstrip(" ").upper()
    if upper in _WIN_DEVICE_NAMES:
        return f"'{name}' is a Windows device name: the write would reach the device, and no file would appear"
    stripped = name.rstrip(". ")
    if stripped != name:
        return f"'{name}' ends in a dot or a space, which Windows drops: the file would land as '{stripped}'"
    return None


ON_WINDOWS = os.name == "nt"


def refuses_windows_path(path: str) -> str | None:
    """Check all components before normalization, only on Windows.

    PureWindowsPath also lets the same validation tests run on other platforms without
    changing os.name or constructing a filesystem-bound WindowsPath.
    """
    if not ON_WINDOWS:
        return None
    if path.replace("/", "\\").startswith("\\\\.\\"):
        return "Windows device paths are not workspace files"
    parsed = PureWindowsPath(path)
    for part in parsed.parts:
        if part == parsed.anchor:
            continue
        reason = windows_rejects(part)
        if reason:
            return reason
    return None


def shown_path(path: Path, workspace: Path) -> str:
    """``path`` as the report names it: relative to the workspace when it is inside it, the
    absolute path otherwise. An extra root on another drive has no relative form on Windows
    (``os.path.relpath`` raises "path is on mount 'D:', start on mount 'C:'"), and by the
    time the name is written the file is already on disk."""
    try:
        return os.path.relpath(path, workspace)
    except ValueError:
        return str(path)


class Files(BaseTool):
    name: str = "files"
    description: str = (
        "Work with files in the workspace. Actions: "
        "`read` (path; PDFs come back as text, page by page), `write` (path, content; overwrites), `append` (path, content), "
        "`list` (path, optional), `search` (pattern glob such as '**/*.md'). "
        "Paths are relative to the workspace unless they start with '/' or '~'."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "write", "append", "list", "search"]},
            "path": {"type": "string", "description": "File or directory path."},
            "content": {"type": "string", "description": "Content for write/append."},
            "pattern": {"type": "string", "description": "Glob pattern for search."},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE

    workspace: Path
    extra_roots: list[Path] = []

    # ------------------------------------------------------------------ helpers
    def _resolve(self, path: str | None, must_exist: bool = False) -> Path:
        raw = path or "."
        # Validate before strip() or resolve() can erase the spelling that was requested.
        for spelling in (raw, raw.strip()):
            reason = refuses_windows_path(os.path.expanduser(spelling))
            if reason:
                raise OSError(reason)
        raw = raw.strip()
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        roots = [self.workspace.resolve(), *[r.resolve() for r in self.extra_roots]]
        if not any(resolved == r or r in resolved.parents for r in roots):
            raise PermissionError(
                f"path '{raw}' is outside the workspace ({self.workspace}). "
                "Ask the user to add it to agent.extra_roots if access is really needed."
            )
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"'{raw}' does not exist")
        return resolved

    def _inside_workspace(self, path: Path) -> bool:
        ws = self.workspace.resolve()
        return path == ws or ws in path.parents

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = args.get("action", "")
        path = args.get("path") or "."
        summary = (
            f"files.{action} {path}"
            if action != "search"
            else f"files.search {args.get('pattern', '')}"
        )
        try:
            resolved = self._resolve(path)
            inside = self._inside_workspace(resolved)
        except Exception:  # noqa: BLE001 - reported at execution time
            inside = False  # a path that cannot be resolved does not get the workspace's trust
        if action in ("write", "append"):
            risk = RiskLevel.MODERATE if inside else RiskLevel.SENSITIVE
            return CallAssessment(
                risk=risk, summary=summary + f" ({len(args.get('content') or '')} chars)"
            )
        # reads
        return CallAssessment(
            risk=RiskLevel.SAFE if inside else RiskLevel.MODERATE,
            reads_private_data=not inside,
            summary=summary,
        )

    # ------------------------------------------------------------------ execution
    async def execute(
        self,
        action: str = "",
        path: str | None = None,
        content: str | None = None,
        pattern: str | None = None,
        **_: Any,
    ) -> ToolResult:
        try:
            if action == "read":
                target = self._resolve(path, must_exist=True)
                if target.is_dir():
                    return await self.execute(action="list", path=path)
                if target.suffix.lower() == ".pdf":
                    # pypdf on a long document takes seconds: off the event loop, so the
                    # web app and the hub keep answering meanwhile
                    text = await asyncio.to_thread(pdf_text, target)
                    if len(text) > MAX_READ_CHARS:
                        text = text[:MAX_READ_CHARS] + f"\n... [truncated, {len(text)} chars total]"
                    return ToolResult(output=text or "(empty file)")
                # read what fits and one more character, not the whole file: a log of a few
                # hundred MB is truncated either way, and need not be loaded for it
                text = read_capped_text(target, MAX_READ_CHARS + 1)
                if len(text) > MAX_READ_CHARS:
                    size = target.stat().st_size
                    text = text[:MAX_READ_CHARS] + f"\n... [truncated, {size} bytes total]"
                return ToolResult(output=text or "(empty file)")
            if action in ("write", "append"):
                if content is None:
                    return ToolResult.fail("`content` is required")
                content, fixed = unescape_flat(content)
                target = self._resolve(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                # the lines the model wrote are the lines the file gets: text mode on Windows
                # turned each "\n" into "\r\n", so a script or a config written for a Unix tool
                # arrived with carriage returns, and a read of it was not what was written
                with target.open(
                    "a" if action == "append" else "w", encoding="utf-8", newline=""
                ) as fh:
                    fh.write(content)
                rel = shown_path(target, self.workspace)
                note = " (the content had escaped newlines; written as lines)" if fixed else ""
                return ToolResult(
                    output=f"{'Appended' if action == 'append' else 'Wrote'} {len(content)} chars "
                    f"to {rel}{note}"
                )
            if action == "list":
                target = self._resolve(path, must_exist=True)
                if target.is_file():
                    return ToolResult(output=f"{target.name} ({target.stat().st_size} bytes)")
                entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                lines = [f"{'[dir] ' if p.is_dir() else ''}{p.name}" for p in entries[:LIST_MAX]]
                if len(entries) > LIST_MAX:
                    # the model reads the list as the whole folder unless it says otherwise
                    lines.append(
                        f"... [{len(entries)} entries in all; the first {LIST_MAX} are listed]"
                    )
                return ToolResult(output="\n".join(lines) or "(empty directory)")
            if action == "search":
                if not pattern:
                    return ToolResult.fail("`pattern` is required")
                root = self._resolve(path)
                # matches are named relative to the workspace when the root is in it, and
                # as absolute paths under an extra root (relative_to raised there before)
                ws = self.workspace.resolve()
                in_ws = root == ws or ws in root.parents
                glob = str(pattern)

                def _walk() -> tuple[list[str], str]:
                    # a walk of tens of thousands of entries stays off the event loop
                    matches: list[str] = []
                    scanned = 0
                    for p in root.rglob("*"):
                        scanned += 1
                        if p.is_file() and fnmatch.fnmatch(p.relative_to(root).as_posix(), glob):
                            matches.append(p.relative_to(ws).as_posix() if in_ws else str(p))
                        if len(matches) >= SEARCH_MAX_MATCHES:
                            # a cut list it reads as whole is worse than a short, honest one
                            return matches, (
                                f"\n(stopped at {SEARCH_MAX_MATCHES} matches; "
                                "narrow the pattern or the `path`)"
                            )
                        if scanned >= SEARCH_MAX_ENTRIES:
                            return matches, (
                                f"\n(stopped after {SEARCH_MAX_ENTRIES} entries; "
                                "search a narrower `path`)"
                            )
                    return matches, ""

                matches, note = await asyncio.to_thread(_walk)
                return ToolResult(output=("\n".join(matches) or "(no matches)") + note)
            return ToolResult.fail(f"unknown action '{action}'; use read/write/append/list/search")
        except (PermissionError, FileNotFoundError, IsADirectoryError, OSError) as exc:
            return ToolResult.fail(str(exc))

    def __repr__(self) -> str:  # pragma: no cover
        return f"Files({short_json({'workspace': str(self.workspace)})})"


__all__ = ["Files"]

```

## nanomuse/tools/shell.py

Source: https://github.com/nano-muse/nanoMuse/blob/09850259cab00d27af83786f00faa09b5d8494bf/nanomuse/tools/shell.py

SHA-256: `8a955c0698b86f32bce02956dbf7bcca948c144b85b68da76e9772cc52599749`

```
"""Shell and Python execution inside the workspace.

Both run as subprocesses with a scrubbed environment: anything that looks like a
credential (``*_KEY``, ``*TOKEN*``, ``*SECRET*``, ``*PASSWORD*``, ``NANOMUSE_*`` …) is
removed before the child starts, so a script the model wrote cannot read the model's
own API key, the vault key or the app token out of ``os.environ``. Neither tool takes
``{{vault:NAME}}`` placeholders (``accepts_secrets`` is off): a command that carries one is
refused with the reason instead of running with the literal text, and the vault's values
reach only the connectors that are configured with them (mail, calendars, the model).

With a working sandbox (``nanomuse.sandbox``, bubblewrap on Linux) each call also gets
its own namespace: the workspace is the only writable place, the home directory is not
there, and there is no network unless the call was assessed as needing it.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from nanomuse.sandbox import Sandbox, needs_network
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.stdio import decode_output
from nanomuse.tools.base import BaseTool, CallAssessment, number_arg

_SECRET_ENV = re.compile(
    r"(KEY|TOKEN|SECRET|PASSW|PASSPHRASE|CREDENTIAL|_AUTH|AUTH_|COOKIE|SESSION)", re.IGNORECASE
)
_SECRET_ENV_PREFIXES = ("NANOMUSE_", "AWS_", "AZURE_", "GOOGLE_", "GH_", "GITHUB_", "NPM_")
# the user's ssh agent is a credential too, even though the name does not say so
_ALWAYS_DROP = {"SSH_AUTH_SOCK", "GPG_AGENT_INFO"}


def scrubbed_env(source: dict[str, str] | None = None) -> dict[str, str]:
    """The parent's environment minus anything that looks like a credential."""
    env: dict[str, str] = {}
    for name, value in (source if source is not None else os.environ).items():
        upper = name.upper()
        if (
            name in _ALWAYS_DROP
            or _SECRET_ENV.search(upper)
            or upper.startswith(_SECRET_ENV_PREFIXES)
        ):
            continue
        env[name] = value
    env["NANOMUSE_SANDBOX"] = "1"
    return env


def kill_tree(proc: Any) -> None:
    """Stop a child started with ``start_new_session`` and everything it started, so a
    timed-out or cancelled command cannot linger. ``proc`` is an asyncio or subprocess
    process; on Windows ``taskkill /T`` walks the tree, on POSIX the process group goes."""
    if os.name == "nt":
        import subprocess

        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        return
    try:
        # looked up by name so mypy on Windows does not object to what its os module lacks
        killpg = getattr(os, "killpg")  # noqa: B009
        sigkill = getattr(signal, "SIGKILL")  # noqa: B009
        # the child is its own group leader (start_new_session), so its pid names the
        # group; a child that is not raises ProcessLookupError and is killed alone
        killpg(proc.pid, sigkill)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


# Stop at command boundaries and the standalone end-of-options marker. A letter inside
# a different long option (for example --preserve-root) is not an rm short flag.
_RM_OPTIONS = r"(?:(?![ \t]--(?:[ \t\r\n|;&]|$))[^\n|;&])*"
_RM_FLAG_END = r"(?=[ \t\r\n|;&]|$)"

_DANGEROUS = [
    (
        re.compile(
            r"\brm(?=[ \t])"
            rf"(?={_RM_OPTIONS}[ \t](?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive){_RM_FLAG_END})"
            rf"(?={_RM_OPTIONS}[ \t](?:-[a-zA-Z]*f[a-zA-Z]*|--force){_RM_FLAG_END})"
        ),
        "recursive force delete",
    ),
    (re.compile(r"\bsudo\b"), "privilege escalation (sudo)"),
    (re.compile(r"\bmkfs\b|\bdd\s+if="), "disk-level operation"),
    (re.compile(r"curl[^|]*\|\s*(ba)?sh|wget[^|]*\|\s*(ba)?sh"), "pipes a download into a shell"),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "world-writable permissions"),
    (re.compile(r">\s*/dev/sd|\bshutdown\b|\breboot\b"), "system-level command"),
    (re.compile(r"\bgit\s+push\b.*--force"), "force push"),
]


def _spliced(command: str) -> str:
    # POSIX shells remove an unquoted backslash-newline pair before splitting words,
    # and also remove it inside double quotes. Thus `rm \<newline>-rf x` runs as
    # `rm -rf x`, and `su\<newline>do` as `sudo`. This pattern scan is not a shell parser:
    # it also joins pairs inside single quotes, so literal text can trigger a warning.
    return command.replace("\\\n", "")


_SKIP_PROGRAMS = {"cd", "export", "set", "true", "time", "env", "nohup", "exec"}

# What a Python script can reach beyond plain computation and the workspace. Each hit
# makes the call sensitive and puts its reason on the approval card.
_REACH: list[tuple[str, re.Pattern[str]]] = [
    (
        "network",
        re.compile(
            r"\b(import\s+(socket|urllib|http\.client|httpx|requests|aiohttp|ftplib|smtplib|"
            r"imaplib|telnetlib|websocket|paramiko)|from\s+(urllib|http|httpx|requests|aiohttp|"
            r"socket)\b)"
        ),
    ),
    (
        "processes",
        re.compile(
            r"\b(import\s+(subprocess|pty|multiprocessing)|from\s+subprocess|os\.(system|popen|"
            r"exec[lv]p?e?|spawn[lv]p?e?|fork|kill)|ctypes)\b"
        ),
    ),
    ("environment", re.compile(r"\bos\.(environ|getenv|putenv)\b")),
    (
        # a module fetched by name or an attribute looked up by string slips past the
        # patterns above; naming it on the card is the honest answer
        "dynamic code",
        re.compile(
            r"\b(__import__|importlib|getattr\s*\(\s*(os|sys|builtins|subprocess)\b|"
            r"globals\s*\(\s*\)\s*\[|eval\s*\(|exec\s*\(|compile\s*\()"
        ),
    ),
    (
        "deletion",
        re.compile(
            r"\b(shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs)"
            r"|Path\([^)]*\)\.(unlink|rmdir))\b"
        ),
    ),
    (
        "outside the workspace",
        re.compile(
            r"(['\"](/(etc|home|root|usr|var|proc|sys|dev|tmp|opt|Users)/|~/?)|"
            r"Path\.home\(\)|expanduser\()"
        ),
    ),
]
_REACH_LABELS = {
    "network": "reaches the network",
    "processes": "starts other programs",
    "environment": "reads environment variables",
    "dynamic code": "loads code or modules by name",
    "deletion": "deletes files",
    "outside the workspace": "touches paths outside the workspace",
}


_ABS_PATH = re.compile(r"['\"](/[^'\"\n]*)")
_HOME = re.compile(r"['\"]~|Path\.home\(\)|expanduser\(")


def code_reach(code: str, workspace: Path | None = None) -> dict[str, str]:
    """``{kind: human label}`` for everything a script reaches beyond the workspace.

    With ``workspace`` given, absolute paths that stay inside it do not count as
    "outside" (a workspace under ``/tmp`` or ``/home`` would otherwise flag every write).
    """
    reach = {kind: _REACH_LABELS[kind] for kind, pattern in _REACH if pattern.search(code)}
    if "outside the workspace" in reach and workspace is not None and not _HOME.search(code):
        root = workspace.resolve().as_posix().rstrip("/") + "/"
        paths = [m.group(1) for m in _ABS_PATH.finditer(code)]
        if paths and all(p.startswith(root) or p + "/" == root for p in paths):
            del reach["outside the workspace"]
    return reach


def programs_of(command: str) -> str | None:
    """The programs a shell command runs (``cd web && npm run build`` → ``npm``).

    This is what an approval for a shell command is bound to: allowing ``git`` for the
    session does not allow ``curl``. Returns ``None`` when nothing recognisable is found.
    """
    names: list[str] = []
    # a command inside `$(…)` or backticks runs too: it counts as a segment of its own
    flattened = re.sub(r"\$\(|`|<\(|>\(", "\n", command)
    for segment in re.split(r"\|\||&&|;|\||\n", flattened):
        tokens = segment.strip().split()
        while tokens and (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]) or tokens[0] in ("env", "time")
        ):
            tokens.pop(0)
        if not tokens:
            continue
        program = tokens[0].rsplit("/", 1)[-1].rstrip(")")
        if program in _SKIP_PROGRAMS or not re.fullmatch(r"[A-Za-z0-9_.+-]+", program):
            continue
        if program not in names:
            names.append(program)
    return ",".join(sorted(names)) if names else None


# the most of a command's stdout (and, separately, stderr) kept in memory; the model sees
# 20k characters of it anyway, and `cat` of a disk image must not be the runtime's memory
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, int]:
    """Read a pipe to its end, keeping the first ``cap`` bytes; returns them and how many
    bytes there were in all. The rest is drained so the child never blocks on a full pipe."""
    chunks: list[bytes] = []
    kept = total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if kept < cap:
            piece = chunk[: cap - kept]
            chunks.append(piece)
            kept += len(piece)
    return b"".join(chunks), total


async def _run(
    cmd: list[str] | str,
    cwd: Path,
    timeout: float,
    shell: bool,
    sandbox: Sandbox | None = None,
    network: bool = True,
    extra_env: dict[str, str] | None = None,
) -> ToolResult:
    env = scrubbed_env()
    if extra_env:
        # the CLI bridge's call token: set on purpose, after the scrub, for this command only
        env.update(extra_env)
    boxed = sandbox is not None and sandbox.active
    # a box that cannot take the network away never ran the command without it
    without_network = boxed and sandbox is not None and sandbox.blocks_network and not network
    if boxed:
        assert sandbox is not None
        argv = ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)
        cmd = sandbox.wrap(argv, network=network, cwd=cwd)
        shell = False
    # its own session on POSIX, so a timeout (or a cancelled turn) can stop the shell and
    # everything it started: `sleep 999 | tee`, a server put in the background
    own_session = os.name != "nt"
    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                cmd if isinstance(cmd, str) else " ".join(cmd),
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=own_session,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,  # type: ignore[misc]
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=own_session,
            )
        try:
            assert proc.stdout is not None and proc.stderr is not None
            (out, out_total), (err, err_total), _ = await asyncio.wait_for(
                asyncio.gather(
                    _read_capped(proc.stdout, MAX_OUTPUT_BYTES),
                    _read_capped(proc.stderr, MAX_OUTPUT_BYTES),
                    proc.wait(),
                ),
                timeout=timeout,
            )
        except TimeoutError:
            kill_tree(proc)
            await proc.wait()
            return ToolResult.fail(f"timed out after {timeout:.0f}s")
        except asyncio.CancelledError:
            kill_tree(proc)
            raise
    except FileNotFoundError as exc:
        return ToolResult.fail(str(exc))
    # the console's code page, not ours: `cmd /c chcp` on a Chinese Windows answers in 936
    stdout = decode_output(out)
    stderr = decode_output(err)
    text = stdout
    if out_total > MAX_OUTPUT_BYTES:
        text += f"\n[stdout cut: {out_total} bytes in all, the first {MAX_OUTPUT_BYTES} kept]"
    if stderr.strip():
        text += ("\n" if text else "") + f"[stderr]\n{stderr}"
    if err_total > MAX_OUTPUT_BYTES:
        text += f"\n[stderr cut: {err_total} bytes in all, the first {MAX_OUTPUT_BYTES} kept]"
    text += f"\n[exit code {proc.returncode}]"
    if proc.returncode != 0:
        if without_network and _NO_NETWORK.search(stderr + stdout):
            text += (
                "\n[sandbox: this command ran without network access. Commands that reach the "
                "network say so by what they run (curl, pip, git …) or by a URL in them; if this "
                "one needs the network, run it again with network=true.]"
            )
        return ToolResult(output=text.strip(), error=f"exit code {proc.returncode}")
    return ToolResult(output=text.strip())


# what a command says when it wanted the network and the box had none
_NO_NETWORK = re.compile(
    r"Could not resolve|Name or service not known|Temporary failure in name resolution|"
    r"Network is unreachable|getaddrinfo|nodename nor servname|No address associated|"
    r"NameResolutionError|ConnectError|Failed to connect",
    re.IGNORECASE,
)


class Shell(BaseTool):
    name: str = "shell"
    description: str = (
        "Run a shell command in the workspace directory (bash). Use for git, package managers, "
        "file conversions, quick data processing. Long-running or interactive commands are not "
        "supported. Output is truncated."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "number", "description": "Seconds (default 60, max 600)."},
            "network": {
                "type": "boolean",
                "description": (
                    "Set when the command needs the network and that is not obvious from "
                    "what it runs (curl, pip, git … and URLs are recognised on their own)."
                ),
            },
        },
        "required": ["command"],
    }
    risk: RiskLevel = RiskLevel.SENSITIVE
    egress: bool = True  # a shell can reach the network
    workspace: Path
    # With a working sandbox the command gets the network only when it says so, and
    # only then does the Sentinel treat it as egress. Without one, every shell command
    # may reach the network and is treated that way.
    sandbox: Sandbox | None = None
    # The CLI bridge (nanomuse.bridge.server.Bridge) when a server is running: each
    # command gets a call token so `nanomuse-device` and friends work from inside it.
    bridge: Any = None

    def _network(self, args: dict[str, Any]) -> bool:
        command = str(args.get("command", ""))
        return bool(args.get("network")) or needs_network(command, programs_of(command))

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        command = str(args.get("command", ""))
        warnings = [label for pattern, label in _DANGEROUS if pattern.search(_spliced(command))]
        # a box that takes the network away decides which commands get it; one that
        # cannot (see Sandbox.blocks_network), like no box, leaves every command able to
        boxed = self.sandbox is not None and self.sandbox.active and self.sandbox.blocks_network
        network = self._network(args)
        return CallAssessment(
            risk=RiskLevel.SENSITIVE,
            egress=network if boxed else True,
            egress_target=None,
            target=programs_of(command),
            # in the box the card says when a command gets the network; unboxed, all do
            summary=f"shell{' (network)' if boxed and network else ''}: {command[:160]}",
            warnings=[f"command looks dangerous: {w}" for w in warnings],
        )

    async def execute(
        self, command: str = "", timeout: float = 60, network: bool = False, **_: Any
    ) -> ToolResult:
        if not command.strip():
            return ToolResult.fail("empty command")
        timeout = number_arg(timeout, 60, 1.0, 600.0)
        self.workspace.mkdir(parents=True, exist_ok=True)
        env, grant = bridge_env(self.bridge, self.name, timeout)
        try:
            return await _run(
                command,
                self.workspace,
                timeout,
                shell=True,
                sandbox=self.sandbox,
                network=self._network({"command": command, "network": network}),
                extra_env=env,
            )
        finally:
            if self.bridge is not None:
                self.bridge.release(grant)


class PythonExecute(BaseTool):
    name: str = "python_execute"
    description: str = (
        "Execute a Python script in a subprocess (cwd = workspace) and return stdout/stderr. "
        "Use print() to output results. Good for calculations, data wrangling, and small automations."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "timeout": {"type": "number", "description": "Seconds (default 60, max 600)."},
        },
        "required": ["code"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    egress: bool = True
    workspace: Path
    # In the sandbox a script gets the network only when it imports something that uses
    # it (or starts programs, which could); see ``code_reach``.
    sandbox: Sandbox | None = None
    bridge: Any = None  # as on Shell

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        """Plain computation and files in the workspace are moderate (auto-allowed in the
        default mode) when a sandbox holds the script to that. Anything that reaches further
        — the network, other processes, the environment, paths outside the workspace,
        deletions — is sensitive and stops for approval, with the reason on the card; and
        without a working sandbox (macOS, Windows, a Linux box without bubblewrap) every
        script is, since the static check is then the only wall between the code and the
        rest of the computer."""
        code = str(args.get("code", ""))
        first = code.strip().splitlines()[0][:100] if code.strip() else ""
        reach = code_reach(code, self.workspace)
        boxed = self.sandbox is not None and self.sandbox.active
        summary = f"python_execute: {first} ({len(code)} chars)"
        if not boxed:
            # the level, not a warning: a warning is something in the code itself and is
            # never waved through; this is the computer's condition, which `auto` mode and
            # always_allow_tools are entitled to accept
            summary += "; runs without a sandbox on this computer"
        return CallAssessment(
            risk=RiskLevel.SENSITIVE if reach or not boxed else RiskLevel.MODERATE,
            egress=bool(reach.get("network")) or bool(reach.get("processes")),
            target=None,
            summary=summary,
            warnings=[f"code {what}" for what in reach.values()],
        )

    async def execute(self, code: str = "", timeout: float = 60, **_: Any) -> ToolResult:
        if not code.strip():
            return ToolResult.fail("empty code")
        timeout = number_arg(timeout, 60, 1.0, 600.0)
        self.workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".py",
            prefix="nanomuse_",
            dir=self.workspace,
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(code)
            script = Path(fh.name)
        reach = code_reach(code, self.workspace)
        env, grant = bridge_env(self.bridge, self.name, timeout)
        try:
            return await _run(
                [sys.executable, str(script)],
                self.workspace,
                timeout,
                shell=False,
                sandbox=self.sandbox,
                network=bool(reach.get("network")) or bool(reach.get("processes")),
                extra_env=env,
            )
        finally:
            script.unlink(missing_ok=True)
            if self.bridge is not None:
                self.bridge.release(grant)


def bridge_env(bridge: Any, tool: str, ttl: float) -> tuple[dict[str, str] | None, Any]:
    """The call token for one command, when a bridge is there; ``(None, None)`` otherwise."""
    if bridge is None:
        return None, None
    minted = bridge.env_for(uuid.uuid4().hex[:12], tool, ttl)
    if minted is None:
        return None, None
    return minted


__all__ = [
    "PythonExecute",
    "Shell",
    "bridge_env",
    "code_reach",
    "kill_tree",
    "needs_network",
    "programs_of",
    "scrubbed_env",
]

```
