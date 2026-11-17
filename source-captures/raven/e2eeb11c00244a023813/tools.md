# Raven — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## agents/raven-code/plugins/code-flow/code_flow/tools/exec.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/agents/raven-code/plugins/code-flow/code_flow/tools/exec.py

SHA-256: `f0ffccda888bde0a7f13598bfdf08163aba5501a3c32f72257d7d383ccdceeba`

```
"""exec -- the host's shell tool with the fork's fixes, served by name.

The trunk ``ExecTool`` is kept whole (its guards, its background lane, its
machine lane); this subclass changes what a coding run kept tripping over, and
does it on the executor seam ``ExecTool.__init__`` already exposes:

*A long timeout is clamped, not refused.* Trunk's schema says ``maximum: 600``,
so a model asking for 900 seconds for a slow test suite is answered with a
validation error and learns nothing. Here the schema carries no maximum, the
request is clamped to the configured ceiling, and the result says so.

*A timed-out command says so, in as many words.* Trunk's executor used to wait
on ``communicate()``, so when that raised on timeout the buffered output was
gone and the model read ``Timed out after 600s`` with nothing else -- for a
test suite that printed forty passes and one hang, the forty passes vanished.
Trunk drains the pipes itself now and hands back what arrived, so the output is
no longer what separates the two. What remains here is the reading: trunk
signals the kill through ``exit_code`` and a stderr line, while
``CodeExecResult`` flags ``timed_out`` and renders ``TIMED_OUT_NOTE``, which
tells the model the output is a fragment rather than the whole run. The drain
below therefore duplicates trunk's; retiring it is the fork owners' call, and
nothing depends on which of the two performs it.

*A large output is saved whole, not lost in the middle.* Trunk keeps the head
and the tail of 10,000 characters; the middle is nowhere. Here the budget is
30,000 and the complete output is written under Agent home (never into the
repository), with the path in the result, so the model can grep it instead of
re-running blind.

Sandbox discipline: this executor runs on the host, exactly as the trunk's
``DirectExecutor`` does when ``tools.sandbox.backend`` is ``none``. The factory
serves this tool only in that case and declines otherwise, so a configured
sandbox is never bypassed by a same-name replacement.

Environment discipline: the host executor hands a child only an allowlisted
baseline of the process environment (never credentials), and that allowlist is
a private detail of the trunk module -- a product plugin may not import it
(the cargo redlines). So the baseline is learned from the host executor itself:
one probe command run through the public ``exec`` reports which variables a
child receives, and this executor passes exactly those, read fresh from the
process environment at every spawn. No copy of the list, so no drift.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from code_flow.tools.diagnostic_workers import MAX_WORKERS
from code_flow.tools.diagnostics import UNKNOWN, PythonDiagnostics
from raven.agent.tools.shell import ExecTool
from raven.contracts.tool import ToolOutput, ToolResult
from raven.sandbox.direct_executor import DirectExecutor
from raven.sandbox.interfaces import ExecResult

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_TIMEOUT = 1200
MAX_OUTPUT = 30_000
#: Where an oversized output is saved whole, under Agent home.
SPILL_SUBDIR = "exec-output"

TIMED_OUT_NOTE = (
    "[the command hit its time limit and was killed; the output above is what it had "
    "produced by then and is partial. Retry with a larger `timeout` if it just needs "
    "longer, or run it with run_in_background: true and read its log]"
)

_SPILL_COUNTER = itertools.count(1)

#: Variables the probe's own shell adds on top of the baseline; not part of it.
_SHELL_ADDED = frozenset({"PWD", "OLDPWD", "SHLVL", "_"})
_PROBE_TIMEOUT = 15
_DIAGNOSTIC_WAIT_SECONDS = 3.0
_DIAGNOSTIC_FILES = 8
_DIAGNOSTIC_CHARS = 6000
_VISIBILITY_BATCH = 256


def spill_output(text: str, spill_dir: Path | None) -> str | None:
    """Save ``text`` whole under ``spill_dir``; the path, or None when it cannot be saved."""
    if spill_dir is None:
        return None
    path = spill_dir / f"exec-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{next(_SPILL_COUNTER)}.log"
    try:
        spill_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("code-flow exec: could not save the full output to {}: {}", path, exc)
        return None
    return str(path)


@dataclass
class CodeExecResult(ExecResult):
    """Trunk's result plus what a coding run needs to read it right."""

    timed_out: bool = False
    spill_dir: Path | None = None

    def as_text(self, max_chars: int = MAX_OUTPUT) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr.strip():
            parts.append(f"STDERR:\n{self.stderr}")
        parts.append(f"\nExit code: {self.exit_code}")
        if self.timed_out:
            parts.append(TIMED_OUT_NOTE)
        result = "\n".join(parts)
        if len(result) <= max_chars:
            return result
        half = max_chars // 2
        total_lines = result.count("\n") + 1
        spilled = spill_output(result, self.spill_dir)
        if spilled:
            recovery = (
                f"... [full output saved to {spilled}; grep it, or page through it with read_file offset/limit] ...\n\n"
            )
        else:
            recovery = (
                "... [re-run redirecting to a file (`cmd > out.log 2>&1`) and page through it "
                "with read_file to see all of it] ...\n\n"
            )
        marker = (
            f"\n\n... ({len(result) - max_chars:,} chars truncated; full output "
            f"{len(result):,} chars / {total_lines:,} lines) ...\n" + recovery
        )
        return result[:half] + marker + result[-half:]


async def _drain(stream: asyncio.StreamReader | None, into: bytearray) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        into.extend(chunk)


class CodeExecutor(DirectExecutor):
    """The host executor, draining the pipes as the command runs.

    Same spawn (``sh -c``, the baseline environment allowlist, its own
    session so the whole group can be killed) and the same kill on timeout;
    the difference is that output already read is returned instead of
    dropped, and the ceiling is the product's, not the module constant.
    """

    def __init__(self, *, max_timeout: int = DEFAULT_MAX_TIMEOUT, spill_dir: Path | None = None) -> None:
        super().__init__()
        self._max_timeout = max_timeout
        self._spill_dir = spill_dir
        self._allowed_keys: frozenset[str] | None = None

    async def allowed_keys(self) -> frozenset[str]:
        """The environment variables the host executor lets a child see.

        Learned once, through the public ``exec`` of the parent class: the
        probe is this interpreter printing the child's variable names, so what
        comes back is exactly the trunk allowlist as applied on this host. A
        probe that cannot run leaves exec unusable on purpose -- guessing a
        list here is how credentials would start reaching commands.
        """
        if self._allowed_keys is None:
            probe = (
                shlex.quote(sys.executable)
                + " -c 'import json,os,sys;sys.stdout.write(json.dumps(sorted(os.environ)))'"
            )
            result = await super().exec(probe, timeout=_PROBE_TIMEOUT)
            try:
                names = json.loads(result.stdout)
            except ValueError as exc:
                raise RuntimeError(
                    f"could not learn the host executor's environment baseline (exit {result.exit_code}): "
                    f"{result.stderr.strip() or result.stdout[:200]!r}"
                ) from exc
            self._allowed_keys = frozenset(str(n) for n in names) - _SHELL_ADDED
        return self._allowed_keys

    async def _spawn_env(self, extra: dict[str, str] | None) -> dict[str, str]:
        allowed = await self.allowed_keys()
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update(extra or {})
        return env

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CodeExecResult:
        effective = min(DEFAULT_TIMEOUT if timeout is None else timeout, self._max_timeout)
        spawn_env = await self._spawn_env(env)
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=spawn_env,
            start_new_session=True,
        )
        pgid = process.pid
        out, err = bytearray(), bytearray()
        drains = [
            asyncio.create_task(_drain(process.stdout, out)),
            asyncio.create_task(_drain(process.stderr, err)),
        ]
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=effective)
        except asyncio.TimeoutError:
            timed_out = True
            self._kill_process_group(process, pgid)
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            self._kill_process_group(process, pgid)
            for task in drains:
                task.cancel()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            raise
        # The pipes close when the group is gone; a grandchild that kept one open
        # after the shell exited is not waited on for ever.
        try:
            await asyncio.wait_for(asyncio.gather(*drains, return_exceptions=True), timeout=5.0)
        except asyncio.TimeoutError:
            for task in drains:
                task.cancel()
        stderr = err.decode("utf-8", errors="replace")
        if timed_out:
            stderr = (stderr.rstrip("\n") + "\n" if stderr.strip() else "") + f"Timed out after {effective}s"
        return CodeExecResult(
            stdout=out.decode("utf-8", errors="replace"),
            stderr=stderr,
            exit_code=-1 if timed_out else int(process.returncode if process.returncode is not None else -1),
            timed_out=timed_out,
            spill_dir=self._spill_dir,
        )


def _prefixed(note: str, out: str | ToolResult, *, append: bool = False) -> str | ToolResult:
    """``note`` in front of a tool's answer, whatever shape the answer took."""
    text = out.model_text if isinstance(out, ToolResult) else str(out)
    text = text + note if append else note + text
    if isinstance(out, ToolOutput):
        return ToolOutput(
            text,
            out.display_text,
            retryable=out.retryable,
            blocks_call=out.blocks_call,
            continuation=out.continuation,
            ok=out.ok,
            blocks=out.blocks,
            diff=out.diff,
            file_change=out.file_change,
            removed=out.removed,
            written=out.written,
        )
    if isinstance(out, ToolResult):
        return ToolResult(
            model_text=text,
            display_text=out.display_text,
            retryable=out.retryable,
            blocks_call=out.blocks_call,
            continuation=out.continuation,
            ok=out.ok,
            blocks=out.blocks,
            diff=out.diff,
            file_change=out.file_change,
            removed=out.removed,
            written=out.written,
        )
    return text


class CodeExecTool(ExecTool):
    """Trunk's exec with a clamped ceiling and a 30,000-character budget."""

    _MAX_OUTPUT = MAX_OUTPUT

    def __init__(
        self,
        *args: Any,
        max_timeout: int = DEFAULT_MAX_TIMEOUT,
        python_diagnostics: bool = True,
        python_lint: bool = True,
        python_unused_imports: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._python_diagnostics = python_diagnostics
        self._diagnostics = PythonDiagnostics(lint=python_lint, unused_imports=python_unused_imports)
        # The base ``execute`` clamps against ``self._MAX_TIMEOUT``; the
        # instance value replaces the class constant of 600.
        self._MAX_TIMEOUT = max_timeout
        # The loop's backstop above the executor's own timeout, as trunk sets it.
        self.timeout_seconds = float(max_timeout + 60)

    async def _diagnose_writes(self, root: Path, out: str | ToolResult) -> str:
        if self._shadow is None or (shadow := self._shadow(root)) is None:
            return ""
        # The host's write record lists regular files under the command's
        # directory and already skips symlinks; only its Python files matter.
        changed = [
            (item.path, None if item.created else UNKNOWN)
            for item in getattr(out, "written", ())
            if Path(item.path).suffix == ".py"
        ]
        if not changed:
            return ""
        unchecked_visibility = 0
        candidates = []
        for start in range(0, len(changed), _VISIBILITY_BATCH):
            # The host resolves a batch's paths on the loop, so batches bound
            # each stretch and the sleep gives the outer deadline a point to
            # cancel between them. Far wider than the file limit: each query
            # is a git process, and batches of eight spent the wait on ignored
            # files.
            await asyncio.sleep(0)
            batch = changed[start : start + _VISIBILITY_BATCH]
            shown = await shadow.trackable([path for path, _ in batch])
            candidates.extend((Path(path), old) for path, old in batch if path in shown)
            if len(candidates) >= _DIAGNOSTIC_FILES:
                unchecked_visibility = len(changed) - start - len(batch)
                break
        selected = candidates[:_DIAGNOSTIC_FILES]
        limit = asyncio.Semaphore(MAX_WORKERS)

        async def check(path: Path, old: Any) -> str:
            async with limit:
                return await self._diagnostics.check(path, before=old)

        results = await asyncio.gather(*(check(path, old) for path, old in selected))
        notes = []
        size = 0
        for note in results:
            if note:
                if size + len(note) > _DIAGNOSTIC_CHARS:
                    notes.append("\nFurther Python diagnostics omitted to keep this tool result bounded.")
                    break
                notes.append(note)
                size += len(note)
        if len(candidates) > _DIAGNOSTIC_FILES:
            notes.append(
                f"\nPython diagnostics limited to {_DIAGNOSTIC_FILES} changed files; "
                f"{len(candidates) - _DIAGNOSTIC_FILES} more were skipped."
            )
        if unchecked_visibility:
            notes.append(
                f"\n{unchecked_visibility} additional changed files were not checked after the "
                f"{_DIAGNOSTIC_FILES}-file diagnostic limit; their visibility was not evaluated."
            )
        return "\n".join(notes)

    @property
    def parameters(self) -> dict[str, Any]:
        schema = super().parameters
        timeout = schema["properties"]["timeout"]
        timeout.pop("maximum", None)
        timeout["description"] = (
            "Timeout in seconds for a command on THIS computer. Increase for long-running work "
            f"such as a build or a test suite (default {self.timeout}, ceiling {self._MAX_TIMEOUT}; a "
            "larger value is clamped to the ceiling, not rejected). A command that hits its limit is "
            "killed and the output it produced so far comes back marked partial. Not read with "
            "'machine' or 'run_in_background'."
        )
        return schema

    async def execute(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int | None = None,
        machine: str = "",
        run_in_background: bool = False,
        **kwargs: Any,
    ) -> str | ToolResult:
        note = ""
        inspect_writes = (
            self._python_diagnostics and not machine and not run_in_background and not self._executor.is_sandboxed
        )
        if timeout is not None and not machine and not run_in_background and timeout > self._MAX_TIMEOUT:
            note = (
                f"[note: timeout {timeout}s clamped to the {self._MAX_TIMEOUT}s ceiling; for longer "
                "work use run_in_background: true]\n"
            )
            timeout = self._MAX_TIMEOUT
        out = await super().execute(
            command,
            working_dir=working_dir,
            timeout=timeout,
            machine=machine,
            run_in_background=run_in_background,
            **kwargs,
        )
        if inspect_writes and isinstance(out, (ToolOutput, ToolResult)) and not out.blocks_call:
            root = Path(self._cwd_for(working_dir))
            try:
                diagnostics = await asyncio.wait_for(self._diagnose_writes(root, out), timeout=_DIAGNOSTIC_WAIT_SECONDS)
            except Exception as exc:  # noqa: BLE001 - never replace a completed command's output with a checker error
                logger.debug("code-flow: post-exec diagnostics unavailable: {}", exc)
            else:
                if diagnostics:
                    out = _prefixed(diagnostics, out, append=True)
        return _prefixed(note, out) if note else out


__all__ = [
    "DEFAULT_MAX_TIMEOUT",
    "DEFAULT_TIMEOUT",
    "MAX_OUTPUT",
    "SPILL_SUBDIR",
    "TIMED_OUT_NOTE",
    "CodeExecResult",
    "CodeExecTool",
    "CodeExecutor",
    "spill_output",
]

```

## agents/raven-code/plugins/code-flow/code_flow/tools/plugin.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/agents/raven-code/plugins/code-flow/code_flow/tools/plugin.py

SHA-256: `a54b00701eb64d40bbd5ad5072d1cc1bdb903e50e0aa3b1ca8a4ef82a790e7f4`

```
"""Factories for the tool face code-flow contributes (raven-plugin.toml names them).

Every factory reads the flow slice's ``tools`` section and declines (returns
None) when that section leaves the face off or the slice does not parse, so a
product that wants the host's own tools simply says so and gets them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ValidationError

from code_flow.config import FlowConfig, ToolsConfig
from code_flow.tools.exec import SPILL_SUBDIR, CodeExecTool, CodeExecutor
from code_flow.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from code_flow.tools.read_state import owner_for
from code_flow.tools.search import GlobTool
from code_flow.tools.todo import STORES, TodoTool

if TYPE_CHECKING:
    from raven.plugins.context import PluginContext


def _face(ctx: "PluginContext", seat: str) -> ToolsConfig | None:
    """The tool face's own section of the flow slice, or None to decline."""
    try:
        flow = FlowConfig.from_slice(dict(ctx.config or {}))
    except ValidationError as exc:
        logger.warning("code-flow: config slice is malformed; declining {}: {}", seat, exc)
        return None
    if not flow.tools.enabled:
        return None
    return flow.tools


def _fence(ctx: "PluginContext", cfg: ToolsConfig) -> tuple[Path, ...]:
    """The roots these tools may reach, mirroring what the host grants its own.

    The host computes ``allowed_dirs = (workspace,) if restrict_to_workspace``
    (raven/agent/loop/wiring.py) and an empty tuple means the fence is OFF --
    which is what these replacements used to pass, whatever the product asked
    for. The section carries the answer because a plugin factory cannot read
    the host field; the launcher renders it in.
    """
    return (Path(ctx.services.workspace),) if cfg.restrict_to_workspace else ()


def make_read_file(ctx: "PluginContext") -> ReadFileTool | None:
    cfg = _face(ctx, "read_file")
    if not cfg:
        return None
    return ReadFileTool(workspace=ctx.services.workspace, allowed_dirs=_fence(ctx, cfg), read_owner=owner_for(ctx))


def make_write_file(ctx: "PluginContext") -> WriteFileTool | None:
    cfg = _face(ctx, "write_file")
    if not cfg:
        return None
    return WriteFileTool(
        workspace=ctx.services.workspace,
        allowed_dirs=_fence(ctx, cfg),
        syntax_note=cfg.python_syntax_note,
        lint=cfg.python_lint,
        unused_imports=cfg.python_unused_imports,
        read_owner=owner_for(ctx),
    )


def make_edit_file(ctx: "PluginContext") -> EditFileTool | None:
    cfg = _face(ctx, "edit_file")
    if not cfg:
        return None
    return EditFileTool(
        workspace=ctx.services.workspace,
        allowed_dirs=_fence(ctx, cfg),
        syntax_note=cfg.python_syntax_note,
        lint=cfg.python_lint,
        unused_imports=cfg.python_unused_imports,
        read_owner=owner_for(ctx),
        require_read=cfg.require_read_before_edit,
    )


def make_list_dir(ctx: "PluginContext") -> ListDirTool | None:
    cfg = _face(ctx, "list_dir")
    if not cfg:
        return None
    return ListDirTool(workspace=ctx.services.workspace, allowed_dirs=_fence(ctx, cfg))


def make_glob(ctx: "PluginContext") -> GlobTool | None:
    cfg = _face(ctx, "glob")
    if not cfg:
        return None
    return GlobTool(workspace=ctx.services.workspace, allowed_dirs=_fence(ctx, cfg))


def make_exec(ctx: "PluginContext") -> CodeExecTool | None:
    """The shell, with the clamped ceiling, the partial-output marker and the
    30,000-character budget with spill. Served only where the host's own exec
    would run on the host too (``tools.sandbox.backend == "none"``): the
    replacement runs on the host, and a configured sandbox must not be
    bypassed by a same-name tool -- so under any other backend this declines
    and the host's exec keeps the shell."""
    cfg = _face(ctx, "exec")
    if not cfg:
        return None
    if cfg.exec.sandbox_backend != "none":
        logger.info(
            "code-flow: tools.sandbox.backend is {!r}; the host's exec keeps the shell", cfg.exec.sandbox_backend
        )
        return None
    home = Path(ctx.services.workspace)
    executor = CodeExecutor(max_timeout=cfg.exec.max_timeout, spill_dir=home / SPILL_SUBDIR)
    return CodeExecTool(
        timeout=cfg.exec.timeout,
        working_dir=str(home),
        restrict_to_workspace=cfg.restrict_to_workspace,
        path_append=cfg.exec.path_append,
        executor=executor,
        extra_allowed_dirs=(home,),
        max_timeout=cfg.exec.max_timeout,
        python_diagnostics=cfg.python_syntax_note and cfg.exec_python_diagnostics,
        python_lint=cfg.python_lint,
        python_unused_imports=cfg.python_unused_imports,
    )


def make_todo(ctx: "PluginContext") -> TodoTool | None:
    """The checklist, on the store the flow hook binds per session: one store
    per Agent home, so the hook factory and this one meet on the same object."""
    return TodoTool(STORES.for_home(ctx.services.workspace)) if _face(ctx, "todo") else None


__all__ = [
    "make_edit_file",
    "make_exec",
    "make_glob",
    "make_list_dir",
    "make_read_file",
    "make_todo",
    "make_write_file",
]

```

## raven/agent/loop/turn_path.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/loop/turn_path.py

SHA-256: `007933814f9a75631724e4c42779a0ba4c466aaed7ab1be11a374f227787f8fb`

```
"""The turn execution path: dispatch, the agent loop, streaming, recovery,
persistence.
"""

from __future__ import annotations

import contextlib
import functools
from typing import TYPE_CHECKING

from raven.agent.context.silence import is_silence, reads_loose_silence, teaches_silence
from raven.agent.loop._shared import (
    _ABORTED_ACTION_REPLY,
    _CONTEXT_USED_KEY,
    _DELEGATED_KEY,
    _HOOK_INJECTED_KEY,
    _MAX_ITER_STATIC_FALLBACK,
    _MAX_ITER_SYNTHESIS_PROMPT,
    _MID_TURN_USER_KEY,
    _NOTICE_KEY,
    _ORIGIN_KEY,
    _REASONING_MS_KEY,
    _REPLIED_VIA_TOOL_KEY,
    _SILENT_KEY,
    _SKIP_AFTER_SEND_ORIGINS,
    _SKIP_USER_INBOUND_ORIGINS,
    _STALLED_STATIC_FALLBACK,
    _STALLED_SYNTHESIS_PROMPT,
    _TOOL_DURATION_MS_KEY,
    _TOOL_METADATA_KEY,
    _TOOL_PREVIEW_MAX_CHARS,
    _WALL_CLOCK_STATIC_FALLBACK,
    POST_TOOL_NUDGE,
    SKIPPED_AFTER_BLOCKED_CALL,
    Any,
    AskUserTool,
    Awaitable,
    Callable,
    ContextBuilder,
    Continuation,
    LLMResponse,
    LoopOutcome,
    MemoryStore,
    MessageTool,
    NoProgressAction,
    NoProgressGuard,
    Origin,
    RecoveryAction,
    Session,
    TurnSynthesisPolicy,
    _appended_by_hook,
    _call_window,
    _display_label,
    _file_change_payload,
    _file_composed_reply,
    _file_removed_payload,
    _file_written_payload,
    _first_line,
    _runtime_origin,
    _stamp_context_used,
    _stamp_reasoning_ms,
    _stamp_replied_via_tool,
    _strip_inline_images,
    append_hook_note,
    asyncio,
    autofill_resolver,
    current_autofill,
    failure_class,
    is_hard_tool_failure,
    is_only_think_debris,
    json,
    logger,
    loop_break_nudge,
    merge_mid_turn,
    monotonic,
    replace,
    resolve_context_window,
    semconv,
    stream_llm_call,
    strip_think_blocks,
    time,
    trace,
    turn_ask_kind,
    turn_budgets,
    turn_question,
    turn_synthesis,
    uuid4,
    workdir,
)
from raven.agent.loop.dead_end import NO_RESPONSE_FALLBACK, dead_reasons
from raven.agent.loop.failure_text import attach_full_detail, full_failure_text, response_failure_text
from raven.agent.loop.first_call import FirstCallGuard
from raven.agent.loop.recovery import (
    EMPTY_REPLY_FAILURE,
    MAX_CONTINUATIONS,
    NETWORK_CUT_CONTINUATION,
    OUTPUT_LIMIT_CONTINUATION,
    PARTIAL_STREAM_NOTE,
    REFUSAL_EXPLANATION,
    REFUSAL_FAILURE,
    REFUSAL_NO_EXPLANATION,
    THINKING_ONLY_FAILURE,
    THINKING_ONLY_UNRETRIED_FAILURE,
    TRUNCATED_FAILURE,
    TRUNCATED_UNCONTINUED_FAILURE,
    ContinuationGate,
    DraftGate,
    EmptyAttempt,
    RecoveryState,
    ReplyKind,
    SilenceGate,
    cut_reasoning_head,
    decide,
    empty_retry_delay,
    has_thinking_to_refeed,
    leaves_think_block_open,
    next_step_down,
    reasoning_excerpt,
    reply_kind,
    resend_budget,
)
from raven.agent.session_effort import of as session_effort_of
from raven.agent.tools.registry import call_failed
from raven.agent.tools.removals import RemovalWatch
from raven.agent.window import shrink
from raven.agent.window.images import ATTACHED_IMAGE_KEY, IMAGE_SOURCES_KEY, filed_image_note, image_sources
from raven.agent.window.turn_log import TURN_LOG_KEY, TurnLog
from raven.contracts.harness import (
    ActionRequest,
    CapabilityRequest,
    PlanningRequest,
    ShrinkResult,
    WindowPressure,
    WindowState,
)
from raven.contracts.loop_hooks import HookDecision
from raven.observability.purpose import purpose as _llm_purpose
from raven.permissions.turn import set_current_tool_call_id
from raven.providers import usage_record
from raven.providers.base import bound_llm_detail, canonical_llm_error, llm_error_summary, parse_llm_error
from raven.providers.first_byte import first_byte_budget
from raven.providers.tool_calls import openai_tool_call
from raven.session.manager import BUDGET_STOP_TURN_AT, OUTPUT_LIMIT_TURN_AT
from raven.session.refs import TOOL_RESULT_TRUNCATION_MARKER, tool_result_view
from raven.session.turn_shape import REPLIED_VIA_TOOL, TakeBack, TakeBackStep
from raven.spine.events import bound_failure_text
from raven.spine.turn import AnswerlessTurnError
from raven.token_wise import usage_context
from raven.token_wise.turn_spend import TurnSpend
from raven.tracing import config as tracing_config
from raven.utils.tokens import estimate_message_tokens, estimate_prompt_tokens

if TYPE_CHECKING:
    from pathlib import Path

    from raven.agent.loop.checkpoint import CheckpointService
    from raven.contracts.token_strategy import UsageSnapshot
    from raven.providers.base import ErrorClassification
    from raven.spine.events import NoticeKind
    from raven.spine.runner import Drain, Emit, TurnOutcome
    from raven.spine.turn import TurnRequest


def _scrubbed_result(arguments: Any, text: str, *, agent_output: bool = False) -> str:
    """Preserve agent content and scrub credentials from local tool results.

    A read of a dotfile config under home loses its key values, and any value
    Raven itself holds is replaced by where it is kept. Applied where the result
    is first read, so the preview logged and sent to the page is the same text
    the model gets.
    """
    from raven.config.held_secrets import scrub_tool_output

    return text if agent_output else scrub_tool_output(arguments, text)


def _scrubbed_blocks(arguments: Any, blocks: Any, *, agent_output: bool = False) -> Any:
    """The image-bearing form of a result, its text parts scrubbed like :func:`_scrubbed_result`."""
    from raven.config.held_secrets import scrub_tool_blocks

    return blocks if agent_output else scrub_tool_blocks(arguments, blocks)


def _llm_failure_detail(content: str | None, verdict: ErrorClassification | None) -> str:
    """The one line a model call the loop gave up on is reported by.

    The error response's own text, which is the provider's account of the
    failure and nothing else -- no setting decides which of two meanings the
    content carries. A response with no text at all still needs a sentence the
    readers of that format can parse.

    A canonical sentence arrives bounded from the constructor that built it; a
    provider that words a failure its own way (the upstream-transport-failure
    account) is bounded here, so every route out of a failed call carries the
    same ceiling.
    """
    text = (content or "").strip()
    if text:
        return text if parse_llm_error(text) is not None else bound_llm_detail(text)
    category = verdict.category if verdict is not None else "unknown"
    return canonical_llm_error(category, None, "the provider gave no detail")


def _recovery_failure_text(kind: ReplyKind, response: LLMResponse, visible: str | None, spent: RecoveryState) -> str:
    """What a turn is failed with when recovery could not mend a reply of ``kind``, at full length.

    Scrubbed here, ahead of the bounded copy the lane carries, because two of
    the four texts quote the model: its reasoning, or its own refusal.
    ``spent`` is what recovery tried, which the words must not overstate: they
    count only what was asked again about this reply -- its step's asks, of its
    kind -- and not what the turn tried for other replies.
    """
    asked = spent.step_asks[kind]
    if kind is ReplyKind.THINKING_ONLY:
        head = THINKING_ONLY_FAILURE if asked else THINKING_ONLY_UNRETRIED_FAILURE
        excerpt = reasoning_excerpt(response)
        # Hermes appends the excerpt only when the reasoning left text; without
        # it the sentence would end on a colon that leads nowhere.
        text = f"{head}\n\n{excerpt}" if excerpt else head.partition(" Its last reasoning")[0]
    elif kind is ReplyKind.TRUNCATED:
        text = (
            TRUNCATED_FAILURE.format(n=asked, s="" if asked == 1 else "s") if asked else TRUNCATED_UNCONTINUED_FAILURE
        )
    elif kind is ReplyKind.REFUSAL:
        explanation = visible or reasoning_excerpt(response)
        text = REFUSAL_FAILURE + (
            REFUSAL_EXPLANATION.format(text=explanation) if explanation else REFUSAL_NO_EXPLANATION
        )
    else:
        text = EMPTY_REPLY_FAILURE
    return full_failure_text(text)


def _drop_trailing_prefills(messages: list[dict[str, Any]]) -> None:
    """Take the prefill rows a step left at the end of ``messages`` back off."""
    while messages and messages[-1].get("role") == "assistant" and messages[-1].get("_recovery_synthetic"):
        messages.pop()


def _marker_failure(reason: str | None) -> str:
    """How a broken turn's marker tells the MODEL the turn failed.

    Separated from ``turn_ended.reason``, which keeps the provider's own
    account for whoever is diagnosing the failure, the way ``notice`` and
    ``origin`` are separated from what the model reads. A vendor body is not
    written for a model: it spends context on a masked key and an account URL,
    and reads as something to answer rather than as the end of the turn.
    """
    parsed = parse_llm_error(reason or "")
    if parsed is not None:
        category, provider, _detail = parsed
        return llm_error_summary(category, provider)
    return reason or "unknown error"


def _first_turns_within(messages: list[dict[str, Any]], budget: int) -> tuple[list[dict[str, Any]], bool]:
    """The leading whole turns of ``messages`` that fit in ``budget`` tokens.

    The path this step replaces bounded its input by a token budget; without
    one a declined annotation is retried next turn with the tail one turn
    longer, so a tail that was refused for its size is refused larger every
    time and the log stops being written for that session. A tool result
    reaches the annotator as at most a 16k-character view
    (``tool_result_view``), and is counted here as that view, so a handful of
    tool-heavy turns reaches that point.

    Cuts only where a turn ends -- the next ``role == "user"`` -- so the
    annotator still sees whole exchanges. A first turn that alone exceeds the
    budget is still returned whole, because sending nothing would stall the
    cursor on it forever; the second return value says so, and the caller
    needs it. Nothing else records that such a turn was sent, so a refusal
    would otherwise rebuild the same tail and send the same oversized prompt
    on every qualifying turn after it.
    """
    if budget <= 0 or not messages:
        return messages, False
    spent = 0
    cut = 0
    for i, m in enumerate(messages):
        if i and m.get("role") == "user":
            if spent > budget:
                return (messages[:cut], False) if cut else (messages[:i], True)
            cut = i
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            spent += estimate_message_tokens({**m, "content": tool_result_view(m["content"])})
        else:
            spent += estimate_message_tokens(m)
    if spent <= budget:
        return messages, False
    return (messages[:cut], False) if cut else (messages, True)


def _turns_a_person_opened(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``messages`` without the turns the runtime opened for itself.

    The origin mark rides on the one message that opens a turn -- the user
    entry (``initial_messages[-1]`` at the stamp site) -- and nothing else in
    that turn carries it: the reply the model writes, its tool calls and their
    results are all saved unmarked. Deciding message by message would drop a
    Sentinel turn's question and keep its answer, which is the half that ends
    up in ``episodes.md``, which two Sentinel producers read.

    So the turn decides. A ``role == "user"`` entry opens one and its mark
    rules until the next opens; anything before the first user entry is the
    tail of a turn this slice does not contain, and is kept, because the slice
    starts where the last annotation stopped.
    """
    out: list[dict[str, Any]] = []
    keep = True
    for m in messages:
        if m.get("role") == "user":
            keep = not m.get("origin")
        if keep:
            out.append(m)
    return out


def _without_scratch(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn as the after-turn pipeline should read it: its envelope without
    the recall the assembler appended, which is this turn's input, not its record."""
    from raven.context_engine.scratch import TURN_SCRATCH_KEY, strip_turn_scratch

    out = []
    for m in messages:
        if m.get(TURN_SCRATCH_KEY):
            m = {k: v for k, v in m.items() if k != TURN_SCRATCH_KEY} | {"content": strip_turn_scratch(m) or ""}
        out.append(m)
    return out


def _stamp_turn_observers(messages: list[dict[str, Any]], metadata: dict[str, Any] | None, turn_base: int) -> None:
    """File the turn's observer record onto its last substantive assistant message.

    The loop_hooks paper files ``metadata["observers"]`` on THE TURN'S message
    at persist time, so the search never crosses ``turn_base``: an answerless
    turn has no seat, and the assembled window can share dict objects with the
    session record (the curator candidate view), so stamping an earlier
    message would rewrite filed history in place.
    """
    if not metadata:
        return
    observers = metadata.get("observers")
    if not isinstance(observers, dict) or not observers:
        return
    for message in reversed(messages[turn_base:]):
        if message.get("role") == "assistant" and (message.get("content") or message.get("tool_calls")):
            message["observers"] = dict(observers)
            break


# What one log line of model output may carry. The whole reply is already in the
# llm.output artifact; this is the reading copy, so the bound is a paragraph or
# two -- enough to see the intent, short of turning the log into a transcript.
_SAID_LOG_MAX_CHARS = 2000


# The scaffolding a turn raises and does not keep: empty-response recovery marks
# its nudges and prefills, and an attached image rides its own key. They are
# dropped before persistence, so a reader asking what the turn ended on has to
# drop them too, or it answers about a message nobody is going to keep.
_TURN_TRANSIENT_KEYS = ("_recovery_synthetic", ATTACHED_IMAGE_KEY)


def _stamp_context_used_on_kept(messages: list[dict[str, Any]], response: Any) -> None:
    """Put the reading of a call whose reply is not filed on the newest row that is.

    An empty reply never becomes a row of its own in the session: recovery
    stands scaffolding in for it, the loop gives up on it, or it is filed empty
    and dropped on save. Nor does a provider's error, which is never filed. The
    live gauge still showed that call's window, and the window it measured ends
    at the newest row the turn keeps, so the reading goes there. When that row
    is the turn's question it is already on disk and the stamp never reaches
    it: the turn either files a row of its own later, which takes a newer
    reading, or ends on the marker ``_save_broken_turn`` files, which carries
    it.
    """
    used = _call_window(response)
    if used <= 0:
        return
    for message in reversed(messages):
        if not any(message.get(key) for key in _TURN_TRANSIENT_KEYS):
            message[_CONTEXT_USED_KEY] = used
            return


# How a turn reports the model saying nothing with no recovery behind it. The
# texts recovery fails a turn with say what it tried; nothing was tried here,
# and this says why.
_NO_CONTENT_UNRECOVERED = "The model returned no content, and this turn's empty-response recovery is switched off."

# The moves a turn that the message tool has already answered does not take: the three backed-off
# re-asks of the same step, and the failure that follows them.
_GIVEN_UP_ONCE_ANSWERED = frozenset(
    {RecoveryAction.RETRY, RecoveryAction.STEP_DOWN, RecoveryAction.FALLBACK, RecoveryAction.FAIL}
)

# The moves that ask the model again about the reply in hand, counted per step and kind for the failure texts.
_ASKS_AGAIN = frozenset(
    {
        RecoveryAction.PREFILL,
        RecoveryAction.NUDGE,
        RecoveryAction.RETRY,
        RecoveryAction.STEP_DOWN,
        RecoveryAction.CONTINUE,
        RecoveryAction.FALLBACK,
    }
)


async def _settle_silence(gate: SilenceGate | None, *, silent: bool, dropped_marker: bool = False) -> None:
    """End the hold on a kept reply's start: dropped when the reply was a marker, shown otherwise.

    ``dropped_marker`` is a continuation that was a marker the reply leaves out: only what the cut
    call before it carried is shown.
    """
    if gate is None:
        return
    if silent:
        gate.discard()
    elif dropped_marker:
        await gate.keep_carried()
    else:
        await gate.release()


def _log_what_the_model_said(response: Any) -> None:
    """Record the assistant's own words and how much it thought.

    A log that lists what a model did and never what it said cannot be read
    back into an intent: a run of 300 identical tool calls looks the same
    whether the model was converging or stuck. Both fields are truncated and
    report their full length, because either can be tens of thousands of chars.
    """

    def _line(label: str, text: str) -> None:
        # One line, so the log stays greppable by line as the tool lines are.
        flat = " ".join(text.split())
        head = flat[:_SAID_LOG_MAX_CHARS]
        logger.info("{} ({} chars): {}{}", label, len(text), head, " ..." if len(flat) > len(head) else "")

    said = response.content if isinstance(getattr(response, "content", None), str) else ""
    if said.strip():
        _line("Assistant", said)
    thought = getattr(response, "reasoning_content", None)
    if isinstance(thought, str) and thought.strip():
        _line("Assistant reasoning", thought)
    spent = (getattr(response, "usage", None) or {}).get("reasoning_tokens")
    if not (isinstance(thought, str) and thought.strip()) and spent:
        logger.info("Assistant thought for {} tokens the provider did not return", spent)


def _reasoning_wire_keys(provider: Any, model: str | None) -> Any:
    """The provider's "what would this effort send" answer, or None.

    None when the provider cannot say -- a test double, or an adapter from
    before the method existed -- and the step-down of a thinking-only reply then
    reads the labels on their own, which is what it did before. Never allowed to
    be the thing that fails: this is asked while recovering a reply that brought
    no answer, and an exception here would replace that reply with an error.
    """
    ask = getattr(provider, "reasoning_wire_keys", None)
    if not callable(ask):
        return None

    def shape(effort: str | None) -> object:
        try:
            return json.dumps(ask(model, effort), sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 - an unanswerable question is not a different request
            return effort

    return shape


def _file_unanswered(messages: list[dict]) -> None:
    """Append one notice for questions this turn could not put to anyone.

    A function rather than a method: ``_save_turn`` is borrowed by stubs that
    are not the loop, and a method those stubs do not have would fail the save
    they are there to exercise.
    """
    from raven.permissions.turn import UNANSWERED_KIND, unanswered_filing

    filing = unanswered_filing()
    if filing is None:
        return
    for message in messages:
        notice = message.get(_NOTICE_KEY) or message.get("notice")
        if isinstance(notice, dict) and notice.get("kind") == UNANSWERED_KIND:
            return
    text, notice = filing
    messages.append({"role": "assistant", "content": text, _NOTICE_KEY: notice})


def _delivered_files(turn_rows: list[dict]) -> bool:
    """Whether a tool handed the reader files this turn: a call that left a delivery manifest with files."""
    for row in turn_rows:
        metadata = row.get(_TOOL_METADATA_KEY)
        delivery = metadata.get("raven_delivery") if isinstance(metadata, dict) else None
        if isinstance(delivery, dict) and delivery.get("files"):
            return True
    return False


def _taken_back_whole(messages: list[dict], skip: int) -> bool:
    """Whether the session list and the ACP replay will take back the row that closes this turn.

    Asked of the rule they read a transcript with (``TakeBack``), over the rows as ``_save_turn``
    will store them: a recovery nudge is never stored, and the turn's own rows carry their marks
    under the live names. ``skip`` is where the turn's new rows start; the row that opened it may be
    older, filed by the opening write.
    """
    # Every user row sets the rule's state afresh, so reading from the last one before the turn's new
    # rows reads what the whole transcript would, without walking a long session on every save.
    start = next(
        (
            i
            for i in range(skip - 1, -1, -1)
            if messages[i].get("role") == "user" and not messages[i].get("_recovery_synthetic")
        ),
        0,
    )
    take_back = TakeBack()
    taken = False
    for index, row in enumerate(messages[start:], start):
        if row.get("_recovery_synthetic"):
            continue
        step = take_back.read(
            {
                "role": row.get("role"),
                "origin": row.get(_ORIGIN_KEY) or row.get("origin"),
                "silent": row.get(_SILENT_KEY) or row.get("silent"),
                REPLIED_VIA_TOOL: row.get(_REPLIED_VIA_TOOL_KEY) or row.get(REPLIED_VIA_TOOL),
            }
        )
        if index >= skip and row.get("role") == "assistant":
            taken = step is TakeBackStep.TAKES_BACK
    return taken


def _turn_trace_id() -> str | None:
    """The trace id of the ``session.turn`` span this code runs directly inside.

    With tracing off, ``instrument`` calls the turn without opening a span or
    touching the current-span slot, so the slot may still hold the span of
    the turn that dispatched this one; the switch is checked first for that.
    """
    if not tracing_config.enabled():
        return None
    span = trace.current_span()
    if isinstance(span, trace.Span) and span.name == "session.turn" and span.trace_id:
        return span.trace_id
    return None


class TurnPathMixin:
    """The turn execution path: dispatch, the agent loop, streaming, recovery,
    persistence."""

    @staticmethod
    def _checkpoint_active(policy: str, interactive: bool) -> bool:
        """Resolve ``runtime.checkpoint.policy`` against the call-site's
        ``interactive`` signal. ``"interactive"`` (the default) skips the
        snapshot for one-shot ``-m`` invocations — those have no "next turn"
        to inject recovery into, so paying the snapshot cost there is just
        deadweight. ``"always"`` opts in regardless; ``"never"`` opts out
        regardless."""
        if policy == "never":
            return False
        if policy == "always":
            return True
        return interactive  # policy == "interactive"

    def _turn_checkpoint(self) -> "CheckpointService | None":
        """The shadow-git service for the directory the running turn works in.

        Keyed on the bound directory rather than on a session key: ``run_turn``
        already resolved and validated it once for the whole turn, and
        re-resolving here would both repeat the sandbox-mount check and race a
        workdir override changed mid-turn (the web UI can rewrite it).

        Nothing bound means no turn is running, so there is nothing to
        snapshot and the answer is ``None``. Deriving a directory here instead
        would reintroduce both hazards the binding exists to avoid -- a
        mount-check refusal raised at end of turn, and an empty session key
        materializing the ``ws/_`` fallback directory.

        One service per directory: CheckpointService puts its git dir inside
        the directory it protects, so sharing one across working directories
        would cross-contaminate their edited-file sets.
        """
        target = workdir.current()
        if target is None:
            return None
        return self._checkpoint_for(target)

    def _checkpoint_for(self, target: Path) -> "CheckpointService | None":
        """The shadow-git service for ``target``, made on first use; ``None`` when off."""
        if not self._checkpoint_enabled:
            return None
        if target not in self._checkpoints:
            from raven.agent.loop.checkpoint import CheckpointService

            try:
                self._checkpoints[target] = CheckpointService(
                    target,
                    shadow_dir=self.runtime_config.checkpoint.shadow_dir,
                )
            except ValueError as exc:
                logger.warning("runtime.checkpoint disabled for {} -- {}", target, exc)
                self._checkpoints[target] = None
        return self._checkpoints[target]

    def _command_shadow(self, root: Path) -> "CheckpointService | None":
        """The shadow repo a command run in ``root`` is measured against (``ExecTool``).

        The turn's own, and only where it covers ``root``: a ``working_dir``
        outside it has no copy of what its files held. Outside a turn -- a
        session being opened and warmed -- nothing is bound, and ``root`` is the
        session's directory itself.
        """
        repo = self._checkpoint_for(workdir.current() or root)
        return repo if repo is not None and repo.covers(root) else None

    def _stash_recovery(self, session_key: str, outcome: "LoopOutcome") -> None:
        """Remember an interrupted turn's snapshot so the next turn in this
        session gets a recovery prompt. No-op unless checkpoint is enabled
        and the turn was actually interrupted with something to recover.

        Status filter is intentional: only ``"interrupted"`` triggers a
        recovery prompt. ``"error"`` turns still get a per-turn shadow
        commit (useful for audit), but they don't usually have a partial-
        edits trajectory to resume (provider 400 etc.) and surfacing
        "Files modified last turn" for them would be misleading.
        """
        if self._turn_checkpoint() is None or outcome.status != "interrupted":
            return
        if outcome.edited_files or outcome.checkpoint_id:
            self._pending_recovery[session_key] = {
                "checkpoint_id": outcome.checkpoint_id,
                "files": outcome.edited_files,
            }

    def _inject_recovery_block(self, session_key: str, messages: list[dict]) -> None:
        """Prepend a recovery notice to the current user message when the
        previous turn for this session was interrupted. Consumed once on
        successful injection; if the current message's content has an
        unexpected shape (None / dict / etc.) the pending entry is kept so
        a later assembly with a normal content can still inject it."""
        recovery = self._pending_recovery.get(session_key)
        if not recovery or not messages:
            return
        last = messages[-1]
        if last.get("role") != "user":
            # Last message isn't the user turn — keep the recovery pending so
            # the next assembly (which does end with the user message) injects it.
            return
        content = last.get("content")
        files = recovery.get("files") or []
        cid = recovery.get("checkpoint_id")
        lines = ["[Recovery — the previous turn was interrupted before finishing]"]
        if files:
            lines.append("Files modified last turn: " + ", ".join(files))
        if cid:
            lines.append(f"Checkpoint: {cid}")
        lines.append("Verify the current state of these files before continuing.")
        block = "\n".join(lines)
        # Mutate first, pop second — atomic from the caller's perspective. If
        # we can't safely write to ``content`` (unknown shape) the recovery
        # stays pending instead of being silently dropped on the floor.
        if isinstance(content, str):
            last["content"] = f"{block}\n\n{content}"
        elif isinstance(content, list):
            last["content"] = [{"type": "text", "text": block}] + content
        else:
            return  # unexpected content shape → keep pending
        self._pending_recovery.pop(session_key, None)

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content.

        Paired blocks are removed. What is left is then checked for being
        nothing but tag debris: when a backend inlines its reasoning and the
        turn is cut off inside it, content arrives as a lone closing tag with
        no opener to pair against, so the substitution above finds nothing and
        an eleven-character string reads as a real answer. Recovery is skipped
        and the tag is what the user sees.

        The check is on residue, not on vendor spellings -- it does not matter
        which prefix a backend picked. Text that merely mentions a tag keeps
        its other words and is returned untouched.
        """
        if not text:
            return None
        cleaned = strip_think_blocks(text)
        if is_only_think_debris(cleaned):
            return None
        return cleaned or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""

        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'

        return ", ".join(_fmt(tc) for tc in tool_calls)

    @staticmethod
    def _build_usage_snapshot(response, model: str, session_key: str) -> "UsageSnapshot":
        """Build the same reported usage that tracing consumes."""
        from raven.contracts.token_strategy import UsageSnapshot
        from raven.providers.usage import normalize_usage

        usage = normalize_usage(response.usage)
        usage.pop("total_tokens")
        return UsageSnapshot(model=model, session_key=session_key or None, **usage)

    def _reply_room(self, messages: list[dict], tools: list[dict] | None, model: str | None) -> int | None:
        """The ``max_tokens`` this request carries, or None to send it as before.

        None while the model's whole output ceiling still fits beside the
        prompt; see ``headroom.request_max_tokens`` for the arithmetic.
        """
        from raven.context_engine.headroom import request_max_tokens

        return request_max_tokens(
            window=self.context_window_tokens,
            prompt_tokens=estimate_prompt_tokens(messages, tools),
            ceiling=self._wire_output_ceiling(model),
            floor=self.context_config.headroom.min_tokens,
        )

    @trace.instrument("llm.call", extract=semconv.llm_call_stream)
    async def _llm_call_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str | None,
        on_token_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        *,
        hand_back_partial: bool = False,
        **generation: Any,
    ) -> LLMResponse:
        """Stream LLM response via ``provider.chat_stream`` + accumulate to LLMResponse.

        When a turn caller wires ``on_token_delta``, AgentLoop diverts here
        instead of to ``chat_with_retry``. The work itself lives in
        ``raven.providers.streaming`` — a sub-agent backend answering a direct
        chat drives the same call and must not drift from it. This method stays
        as the loop's own entry point (its span, its reconnect budget).

        ``hand_back_partial`` is asked for by the one call that can ask the model
        to go on from a cut answer: the loop's answering call, made through the
        Action role in ``_run_agent_loop``. The other calls that come through
        here -- the wrap-up an exhausted turn asks for, the watch-work judgement
        -- have no continuation behind them and would deliver the fragment as
        their whole reply, so for them a stream that died after its words fails as
        it always did. A named parameter and not part of ``generation``, which
        reaches ``chat_stream`` unchanged.

        Generation parameters travel only as the turn's ``gen_overrides``: the
        session's pinned reasoning effort under a hook's rollback override.
        With none, ``chat_stream``'s own signature defaults stand, as they
        always have. See ``generation_kwargs`` for the callers that build their
        own.
        """
        limits = self._recovery_limits
        return await stream_llm_call(
            self.provider,
            messages=messages,
            tools=tools,
            model=model,
            on_token_delta=on_token_delta,
            on_reasoning_delta=on_reasoning_delta,
            max_reconnects=self._MAX_STREAM_RECONNECTS,
            retry_delays=tuple(limits.llm_error_retry_delays),
            retry_after_output=bool(limits.llm_retry_after_output),
            hand_back_partial=hand_back_partial,
            **generation,
        )

    async def _synthesize_final_on_exhaustion(
        self,
        messages: list[dict],
        model: str | None,
        fallback_models: list[str] | None,
        on_token_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        reasoning_effort: str | None = None,
        prompt: str = _MAX_ITER_SYNTHESIS_PROMPT,
        fallback: str | None = None,
        synthesis_policy: TurnSynthesisPolicy | None = None,
    ) -> str:
        """Wrap up an interrupted turn with tools withheld from the model.

        Instead of returning a canned apology, ask the model to summarize what
        it accomplished and deliver its best partial answer. Tools are withheld
        (``tools=None``) so it cannot start another tool call — or an
        ``ask_user`` — at the cliff edge. Falls back to a static message if the
        call errors or comes back empty, so the turn never ends with nothing.
        A Silence Marker is a reply the model wrote, and comes back as written
        for the caller to read as one.

        A product may add its own guidance and request one format repair. Those
        calls are buffered until a final text is chosen so the streamed reply
        and the persisted reply agree.

        When the turn caller wired streaming callbacks, this synthesized reply
        must stream too — otherwise it never reaches a streaming outlet: the
        run_turn boundary only emits a closing ``Text`` when nothing streamed,
        so a non-streamed wrap-up after an already-streamed turn gets dropped.

        ``prompt`` and ``fallback`` are the caller's because the reason differs
        and the reason is model-visible: a turn stopped on a repeating call has
        not used up its budget, and told that it had, it summarizes the wrong
        thing.
        """
        if synthesis_policy is not None:
            prompt = f"{prompt}\n\n{synthesis_policy.guidance}"
        # The turn's own conversation plus one instruction: the main line, not a
        # side question, so its input reads as a continuation and its output as
        # the turn's last word.
        synth_messages = messages + [{"role": "user", "content": prompt}]
        # The wrap-up is a model call of the same turn, so it pays the turn's
        # effort, sent the way the turn's other calls send it: None included,
        # which is the provider's own default rather than the copy it took at
        # boot (``gen_overrides`` in ``_run_agent_loop`` says why).
        effort_kwargs: dict[str, Any] = {"reasoning_effort": reasoning_effort}
        # The wrap-up is asked for at the cliff edge, where the transcript is
        # longest, so it is sized to the room beside it like any other call.
        if reply_room := self._reply_room(synth_messages, None, model):
            effort_kwargs["max_tokens"] = reply_room
        if synthesis_policy is not None:

            async def _call(rows: list[dict]) -> str:
                with _llm_purpose("main"):
                    response = await self.provider.chat_with_retry(
                        messages=rows,
                        tools=None,
                        model=model,
                        fallback_models=fallback_models,
                        **effort_kwargs,
                    )
                return (self._strip_think(response.content) or "") if response.finish_reason != "error" else ""

            try:
                text = await _call(synth_messages)
            except Exception as exc:
                logger.warning("Early-exit synthesis call failed: {}", exc)
                text = ""
            if text and synthesis_policy.repair_prompt is not None:
                repair = synthesis_policy.repair_prompt(text)
                if repair:
                    try:
                        revised = await _call(
                            synth_messages
                            + [{"role": "assistant", "content": text}, {"role": "user", "content": repair}]
                        )
                        if revised:
                            text = revised
                    except Exception as exc:
                        logger.warning("Early-exit synthesis repair failed: {}", exc)
            if not text:
                text = fallback or _MAX_ITER_STATIC_FALLBACK.format(n=self.max_iterations)
                if synthesis_policy.format_fallback is not None:
                    try:
                        text = synthesis_policy.format_fallback(text)
                    except Exception as exc:
                        logger.warning("Early-exit synthesis fallback formatting failed: {}", exc)
            if on_token_delta is not None:
                await on_token_delta(text)
            return text
        try:
            if on_token_delta is not None or on_reasoning_delta is not None:
                with _llm_purpose("main"):
                    response = await self._llm_call_stream(
                        messages=synth_messages,
                        tools=None,
                        model=model,
                        on_token_delta=on_token_delta,
                        on_reasoning_delta=on_reasoning_delta,
                        **effort_kwargs,
                    )
            else:
                with _llm_purpose("main"):
                    response = await self.provider.chat_with_retry(
                        messages=synth_messages,
                        tools=None,
                        model=model,
                        fallback_models=fallback_models,
                        **effort_kwargs,
                    )
            text = self._strip_think(response.content)
            if response.finish_reason != "error" and text:
                return text
            logger.warning(
                "Early-exit synthesis returned no usable content (finish_reason={})",
                response.finish_reason,
            )
        except Exception as exc:
            logger.warning("Early-exit synthesis call failed: {}", exc)
        if fallback is None:
            fallback = _MAX_ITER_STATIC_FALLBACK.format(n=self.max_iterations)
        # The streamed-success path already delivered its text through
        # ``on_token_delta``; this fallback did not. Push it through the stream
        # too, or the run_turn boundary — which suppresses the closing ``Text``
        # once anything has streamed — would drop it on a streaming outlet.
        if on_token_delta is not None:
            await on_token_delta(fallback)
        return fallback

    def _flush_autofill(self, messages: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        """Write what raven answered for the user into the turn, as a tool call.

        A live row only reaches the screen; a reloaded transcript is rebuilt
        from the persisted ``tool_calls`` and their results, so without this the
        account of what raven decided is gone by the next session.

        Not written where the decision was made. A sub-agent's question arrives
        while its spawn tool is still executing -- after the assistant message
        carrying ``tool_calls`` is in the list and before its ``tool`` results
        are -- and a message spliced into that window breaks the pairing
        documented at the ``add_assistant_message`` call site below, which Chat
        Completions rejects with a 400. Here the loop owns the list on its own
        task with every result already in, which is why ``drain`` merges its
        injected messages at the same point.

        Only host-minted fields go into the arguments. The sub-agent's own
        wording would arrive here unfenced -- ``add_tool_result`` wraps the
        summary below, nothing wraps an assistant message's ``tool_calls`` --
        and that message is read by the next model call and by the next
        autofill's snapshot. The summary names every question regardless.
        """
        for row in rows:
            call_id = f"autofill-{uuid4().hex[:8]}"
            self.context.add_assistant_message(
                messages,
                None,
                [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": autofill_resolver.TOOL_NAME,
                            "arguments": json.dumps(
                                {"agent": row.get("agent", ""), "instance": row.get("instance", "")},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            )
            self.context.add_tool_result(messages, call_id, autofill_resolver.TOOL_NAME, row.get("summary", ""))

    def _answered_by_tool(self, turn_rows: list[dict]) -> bool:
        """Whether this turn's tools already handed the reader its answer.

        Two tools answer for the model: the message tool's send and ``deliver_files``' manifest. Both
        are live verdicts for this turn, so every reader of "already answered" asks this one question
        -- the give-up escape, the answerless exit, and the ``replied_via_tool`` mark filed below.
        """
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool.sent_in_turn:
            return True
        return _delivered_files(turn_rows)

    async def _run_agent_loop(  # noqa: C901 (cc 100: pre-existing, above the ceiling)
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        session_key: str | None = None,
        model: str | None = None,
        fallback_models: list[str] | None = None,
        injected_skill_ids: list[str] | None = None,
        on_token_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_event: Callable[[str, dict], Awaitable[None]] | None = None,
        on_episode_start: Callable[[int], Awaitable[None]] | None = None,
        on_notice: Callable[[NoticeKind, str], Awaitable[None]] | None = None,
        usage_sink: dict[str, Any] | None = None,
        drain: Drain | None = None,
        hook_metadata: dict[str, Any] | None = None,
        session_history: list[dict[str, Any]] | None = None,
        origin: "Origin | None" = None,
        turn_started_at: float | None = None,
        attempt: int = 1,
        rerun_pending: "Callable[[str | None, list[dict], str], bool] | None" = None,
        spend: TurnSpend | None = None,
        turn_log: TurnLog | None = None,
        rungs_used: set[str] | None = None,
        reply_awaited: bool = False,
        window_reading: dict[str, int] | None = None,
        silence_taught: bool = False,
        turn_start_idx: int = 0,
        session_effort: str | None = None,
        surface: str | None = None,
    ) -> tuple[str | None, list[str], list[dict], LoopOutcome]:
        """Run the agent iteration loop.

        ``origin`` is the turn's: the watch-work judgement reads the owner's
        request off the messages, and a turn the runtime re-injected has none
        to read (``watch_work.asked_for``). With ``silence_taught``, whether
        the turn's prompt taught the marker, it also picks the silence rule a
        reply is read by -- unless ``reply_awaited`` says the caller waits on
        the turn's text, as an inbound A2A call's peer does: such a turn takes
        no part in the silence protocol, so nothing is held back for a marker
        and a marker comes back as the text it is.

        ``drain``, when wired, is called at the top of each iteration to pull
        any user messages injected mid-turn (BusyPolicy.INJECT) and merge them
        as user turns before the next LLM call.

        ``session_key`` is the turn's real session key. It labels the
        checkpoint commit only; usage attribution deliberately carries an
        empty session key, since widening it would start filling per-session
        cost buckets that have always been empty.

        ``session_history`` is the filed record of the session this turn
        persists into -- still ending with the previous turn -- stamped once
        onto the iteration hook context. The caller owns which record that is.

        ``surface`` is the front end the turn's request declared; the note after
        a dispatch receipt reads it, since the terminal and the page share a
        channel and only the page draws the board.

        ``turn_started_at`` is when the caller's turn began, on the clock
        :func:`monotonic` reads. A caller that may run this loop more than once
        for one user turn passes it, so that the wall-clock budget and the
        elapsed time recorded at the end both measure the turn rather than the
        attempt; omitted, the attempt is the turn and the clock starts here.

        ``attempt`` is which run of the loop this is, counting from 1. It rides
        the turn's end record so a reader can tell the scope of the count beside
        it -- the iterations there are this attempt's, while the elapsed time is
        the turn's. Nothing in the loop branches on it.

        ``rerun_pending`` lets a caller that budgets reruns say this attempt is
        about to be run again, given ``(final_content, messages, status)``. It is
        asked only when the turn ended with nothing to show, and a yes means the
        terminal seam is skipped: the answer a gate would manufacture there is
        one the rerun discards, and the minutes spent making it come out of the
        turn's own clock. Omitted, the seam fires for every answerless turn,
        which is what every agent that budgets no rerun sees.

        ``spend`` is the turn's cost, which outlives this loop the way the
        clock above does: a rerun is a second attempt at one turn and bills
        into the same scope. A caller that opened no scope gets one for this
        loop alone, which bills its own calls and takes no delegated ones.

        ``turn_log`` is the record the caller persists this attempt from. The
        returned list is the window as the last call saw it, which the window
        passes may have summarised or elided; the log follows every list they
        hand back and keeps what the turn actually said. A caller that passes
        none gets one opened here, so the rerun question below is still asked of
        the turn rather than of the window.

        ``rungs_used`` is the reasoning efforts this turn has already stepped a
        thinking-only reply down to, each asked for once. A caller that may run
        the loop again for one turn hands every run the same set; omitted, this
        run keeps a set of its own.

        ``window_reading``, when given, is kept at ``context_used`` with the
        newest window a call of this loop measured. It outlives the loop the way
        ``spend`` does: a turn that fails or is stopped files it on its marker,
        which can be the only row the turn adds.

        ``session_effort`` is the reasoning effort the session picked for its
        own model calls (``raven.agent.session_effort``), read off its record
        by the caller the way the model is. It outranks a mode's effort and
        the configured default; a hook's rollback override still outranks it.
        """
        spend = spend if spend is not None else TurnSpend(session_key)
        messages = initial_messages
        log = turn_log if turn_log is not None else TurnLog(initial_messages, len(initial_messages))
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        last_tool_defs: list[dict] | None = None
        last_model: str | None = None
        last_effort: str | None = None
        effective_model = model or self.model

        # Track whether the turn was a normal exit or a max-iter interruption.
        # ``status`` labels the shadow-git commit and, with ``error_detail`` --
        # the loop's own words for a model call it gave up on -- stamps the
        # ``LoopOutcome`` the caller fails the turn on. Both the provider-error
        # exit and the exhausted empty-response exit set the pair: a turn that
        # ran out of ways to get a word back has no answer either, and it is the
        # caller that knows whether a tool has already delivered one.
        status = "completed"
        error_detail: str | None = None
        error_full: str | None = None
        # Set with the failure when the model produced nothing at all, which is
        # what tells a caller an empty run from one that failed in some other way.
        no_output = False
        # Set when the loop ended a turn the message tool had already answered, on a
        # reply with nothing in it: that is not running out of budget, so the wrap-up
        # that fills a turn which did is not asked for this one.
        ended_answered = False
        # Set when the turn's last reply is a silence marker: the model's own choice to say
        # nothing, which the caller does not deliver.
        silent = False
        # What the silence gates have put in front of the reader in the step drawn now: one model
        # call with its cut answer's continuations, or the wrap-up. A reply read as the marker
        # reports it (``LoopOutcome.shown``), for a client to take back that much and no more.
        step_shown: list[str] = []

        async def _shown(delta: str) -> None:
            step_shown.append(delta)
            if on_token_delta is not None:
                await on_token_delta(delta)

        # The turn's window bookkeeping: the last billed context size, the retry
        # budgets every shrink draws on, and the picture window. Held here and
        # handed to the Memory role each time it is asked to make the window
        # smaller, because that role outlives the turn. A budget of 0 in the
        # settings means no standing image pass: ``None`` here, and the window
        # only starts to act once a refusal has closed it a notch.
        window = WindowState(
            image_window=shrink.IMAGE_WINDOW_RECENT_MESSAGES,
            image_budget=self._recovery_limits.image_window_budget_bytes or None,
            session_key=session_key or "",
        )

        async def _shrink(pressure: WindowPressure, at_model: str | None) -> ShrinkResult:
            # The log reads the list on both sides of the pass: before, so the
            # pass's copies of this turn's messages carry the log's stamp; after,
            # so the list it hands back is the one the log follows.
            log.follow(messages)
            result = await self.harness.memory.shrink(messages, pressure=pressure, state=window, model=at_model)
            log.follow(result.messages)
            return result

        # Retryable model errors that outlasted the provider's own ladder: how many
        # of the loop's longer waits this turn has spent.
        error_waits = 0
        # The loop's own pre-request awaits, bounded: a provider timeout cannot
        # fire before the call is entered, and 2026-09-10 stalled before that.
        first_call = FirstCallGuard(
            first_byte_budget(getattr(self.provider, "generation", None)),
            model=effective_model,
        )
        # Tool-failure-loop break: track consecutive hard failures of the
        # same tool *with the same kind of error* across iterations; nudge once
        # per fresh streak, bounded/turn.
        loop_fail_key: tuple[str, str] | None = None
        loop_fail_streak = 0
        loop_nudges = 0
        # No-progress ladder: nudge, then refuse the call, then end the turn.
        # Owns its own per-turn counters; see ``no_progress.NoProgressGuard``.
        no_progress = NoProgressGuard(
            nudge_at=self._NO_PROGRESS_THRESHOLD,
            refuse_at=self._NO_PROGRESS_REFUSE,
            max_nudges=self._NO_PROGRESS_MAX,
            max_refusals=self._NO_PROGRESS_REFUSALS_MAX,
        )
        # Set to the tool's name when the ladder's last step fires, which ends
        # the turn the way an exhausted iteration budget does.
        stalled_tool: str | None = None
        # What this turn has written, so a later call that removes one of those
        # files is seen. Per turn for the reason the counters above are: the loop
        # is a singleton and another session's turn is running beside this one.
        removal_watch = RemovalWatch()
        # A session opened by its first message (a channel's) was never warmed
        # as it opened: warm its directory here instead. Once per directory.
        if (target := workdir.current()) is not None and (warm := getattr(self.tools.get("exec"), "warm", None)):
            await warm(target)
        # Empty-response recovery state, local to the turn — the AgentLoop is a
        # long-lived singleton shared across sessions, so per-instance counters
        # would leak across turns; resetting here gives clean per-turn budgets.
        prev_had_tool_calls = False
        recovery = RecoveryState(rungs_used=set() if rungs_used is None else rungs_used)
        # Set when a prefill re-feeds reasoning the ceiling cut: the next call
        # continues mid-thought, and its opening fragment is not content.
        cut_continuation = False
        # Set when a stream cut mid-answer is about to be continued: the next call
        # carries on the step the cut one opened rather than starting another.
        merge_episode = False
        # The silence gate of the call a cut answer is being continued from: what it held is not
        # shown at the cut, because the reply the turn ends on may still be a marker.
        carried_gate: SilenceGate | None = None
        # The first resend of an empty streamed reply asks without streaming (``unstreamed_next``),
        # once a turn (``unstreamed_spent``): a stream can come back empty where the same call made
        # plainly would not -- a stream wrapper that drops a chunk whose only payload is a refusal, a
        # gateway whose streams fail while its plain calls work.
        unstreamed_next = False
        unstreamed_spent = False
        # Sticky across a turn's step-downs: once the descent reaches a rung, the
        # next one must not read the turn's own effort again and hand back the
        # value that already came up thinking and nothing else.
        step_down_effort: str | None = None
        # Whether the call goes through the stream path. Mirrors the test the
        # Action role applies: that is the path with no fallback chain of its own
        # and the only one a reply can be cut mid-answer on.
        streamed_call = on_token_delta is not None or on_reasoning_delta is not None
        # The model the turn is on and the fallbacks it has not tried. A switch is
        # for the rest of the turn -- every later call asks the model that last
        # answered -- and the chain handed to the provider is what is left, so it
        # never walks back onto a model the loop has already given up.
        active_model = effective_model
        fallbacks_left = list(fallback_models or [])
        # The watch-work judgement's state, owned by this turn: the loop is a
        # singleton and turns from other sessions run concurrently, so anything
        # on `self` here would let one session's dispatch silence another's
        # nudge and one session's verdict answer for another's request.
        from raven.agent.subagent import watch_work as _watch_work

        watch_state = _watch_work.TurnWatch()
        watch_request = _watch_work.asked_for(initial_messages, origin=origin)

        # Read once, here: a mode switched mid-turn lands on the next turn.
        policy = self.session_policy(session_key or "")
        iteration_cap = policy.max_iterations or self.max_iterations
        # The effort this turn's own calls run at, short of a hook's rollback
        # override: the session's own pick over its mode's word over the
        # configured default as the file has it now -- ``session_effort.in_force``,
        # the one rule ``session.info`` reports from too. The clamp is against
        # ``model``, the id this loop will actually call: a router can hand the
        # turn a model other than the session's binding, and that is the model
        # whose declaration bounds the word.
        turn_effort = self.session_reasoning_effort(session_key, session_effort, model)
        # The other bound on this turn, and the only one a product supplies rather
        # than the loop: whatever a hook left under ``TURN_BUDGETS_KEY``. Read here
        # for the same reason the cap is, and defaulting to unbounded, so an agent
        # that writes nothing runs exactly as it did before this existed.
        budgets = turn_budgets(hook_metadata)
        # The caller's clock when it has one: a turn that may be re-run hands the
        # same start to every attempt, so one budget covers the turn instead of
        # each attempt receiving a fresh copy of it.
        turn_t0 = turn_started_at if turn_started_at is not None else monotonic()
        stopped_by: str | None = None
        # The session's effort rides every model call of the turn as an explicit
        # argument. Omitted, not None, when the policy names none: an explicit
        # None would override the provider's sentinel and switch its configured
        # default off.

        # The iteration hook chain's context for this whole turn; None when no
        # hook is registered, so a default install pays nothing here.
        from raven.agent.hook import AgentHookContext

        # One metadata dict serves the whole turn: the entrance seeds it at the
        # inbound phase and reads it after the send, so what a hook stashes in
        # one phase group is still there in the next. The mode keys are the
        # loop's own and overwrite whatever a caller seeded under those names.
        turn_meta = hook_metadata if hook_metadata is not None else {}
        turn_meta["mode"] = policy.mode
        turn_meta["mode_overlay"] = dict(policy.mode_overlay)
        hook_ctx = (
            AgentHookContext(
                session_key=session_key or "",
                turn_question=turn_question(initial_messages),
                turn_base=max(0, len(initial_messages) - 1),
                session_history=session_history,
                max_iterations=iteration_cap,
                context_window_tokens=self.context_window_tokens,
                metadata=turn_meta,
            )
            if len(self.hooks) > 0
            else None
        )
        hook_rollbacks = 0
        # Whether anything installed can send a response back. Only then are the
        # deltas worth holding: holding coalesces the reply into one delta, which
        # costs the reader the answer typing out, and a hook that never rolls
        # back would pay that for nothing. Read off the hook's own declaration
        # rather than from the override: overriding the phase only says a hook
        # watches it, and eval_engine's judge overrides it, awaits an LLM call
        # inside it, and promises never to interrupt the reply.
        holds_drafts = hook_ctx is not None and any(
            getattr(hook, "rolls_back_iterations", False) for hook in self.hooks
        )
        iter_msg_base = 0
        pending_gen_overrides: dict[str, Any] | None = None
        # The overrides an output-limit continuation set, compared by identity: an unchanged
        # resend carries the same dict on, and a hook's rollback brings a dict of its own.
        continuation_overrides: dict[str, Any] | None = None

        async def _announce_wait(detail: str) -> None:
            """Say the loop is waiting before it asks the model again, as the status line the next output replaces.

            Only a category or a fixed word, never the vendor's own body: that can carry
            a masked key and an account URL, and this text is read by everyone watching
            the turn. Staged like the episode boundary: a bounded outlet queue with a
            wedged worker behind it would park the turn here for the whole wait, and
            the notice is a status hint -- losing one is a silent wait, not a turn lost.
            """
            if on_notice is None:
                return
            from raven.spine.events import NoticeKind as _NoticeKind

            await first_call.stage(
                "the retry-notice emit",
                on_notice(_NoticeKind.LLM_RETRY, detail),
                iteration=iteration,
                fallback=None,
            )

        def _land_hook_note(decision) -> None:
            """Honor a hook's ``append_note``: the note joins the last message
            the model is about to read. A body shape the loop does not know
            drops the note with a warning rather than guessing it into place."""
            if decision.append_note and not append_hook_note(messages, decision.append_note):
                logger.warning("Hook note dropped: no transcript message to land it on")

        def _hook_rollback(decision) -> bool:
            """Honor a hook's rollback: pop everything this iteration appended
            and re-sample without consuming an iteration. Message helpers
            mutate-and-return the same list, so truncating at the iteration
            watermark removes exactly this iteration's products."""
            nonlocal iteration, hook_rollbacks, pending_gen_overrides
            if not decision.rollback:
                return False
            if hook_rollbacks >= self._MAX_HOOK_ROLLBACKS:
                if hook_ctx is not None:
                    hook_ctx.metadata["rollbacks_refused"] = hook_ctx.metadata.get("rollbacks_refused", 0) + 1
                logger.warning("Hook rollback cap ({}) reached; proceeding without rollback", self._MAX_HOOK_ROLLBACKS)
                return False
            requested = decision.rollback_overrides or {}
            overrides = {k: v for k, v in requested.items() if k in self._ROLLBACK_OVERRIDE_KEYS}
            if len(overrides) != len(requested):
                logger.warning(
                    "Hook rollback overrides dropped (not in allowlist): {}", sorted(set(requested) - set(overrides))
                )
            hook_rollbacks += 1
            if hook_ctx is not None:
                # The honoured count beside the refused one: a gate scoped to the
                # turn boundary (ask_user) needs to know the iteration number it
                # sees is a re-sample, and only the loop knows that.
                hook_ctx.metadata["hook_rollbacks"] = hook_rollbacks
            log.drop(messages[iter_msg_base:])
            del messages[iter_msg_base:]
            for m in decision.rollback_inject or ():
                entry = dict(m)
                # A hook may hand back a copy of a message this turn already
                # wrote; it is a new message, not a rewrite of that one.
                entry.pop(TURN_LOG_KEY, None)
                entry.setdefault("timestamp", self._now_fn().isoformat())
                entry[_HOOK_INJECTED_KEY] = True
                messages.append(entry)
            iteration -= 1
            pending_gen_overrides = overrides or None
            logger.info(
                "Hook rollback {}/{}: popped iteration messages, re-sampling (overrides={})",
                hook_rollbacks,
                self._MAX_HOOK_ROLLBACKS,
                sorted(overrides) or None,
            )
            return True

        def _switch_to_next_model() -> str | None:
            """Put the rest of the turn on its next fallback model, and return the model it leaves.

            The prefill rows at the end of ``messages`` come off: they are the thought of the model
            being left, and a request ending on one is what a prefill-refusing model rejects. The
            rest of the recovery scaffolding stays, so a nudge or a ceiling continuation written
            for the model being left still reaches the new one, ending the request on a user row.
            Whatever the reason for the switch -- an empty reply's FALLBACK or a streamed error --
            the same holds.
            """
            nonlocal active_model
            previous, active_model = active_model, fallbacks_left.pop(0)
            recovery.fallbacks_used += 1
            _drop_trailing_prefills(messages)
            return previous

        async def _end_on_the_partial(last: LLMResponse) -> str:
            """End the turn on a cut answer no continuation will finish, and return what it ends on.

            The continuation failed, or the turn has no iteration or time left to ask for it. What
            streamed is the answer, the note says it is not the whole of it, and the turn is
            ``interrupted``. Nothing more is asked for: a wrap-up would stream onto the partial and be
            all the record kept. When what streamed is the model's marker, the turn ends silent as any
            marker does: nothing shown, no note. The reply carries the window ``last``, the turn's
            newest call, measured, as every reply does.
            """
            nonlocal messages, status, silent
            final = self._strip_think("".join(recovery.parts)) or ""
            held = carried_gate.handed_over() if carried_gate is not None else None
            silent = not reply_awaited and is_silence(final, origin, taught=silence_taught)
            if silent:
                if held is not None:
                    held.discard()
            else:
                note = "\n\n" + PARTIAL_STREAM_NOTE
                final = final + note if final else PARTIAL_STREAM_NOTE
                status = "interrupted"
                if held is not None:
                    await held.release()
                if on_token_delta is not None:
                    await on_token_delta(note)
            messages = self.context.add_assistant_message(messages, final)
            if silent:
                messages[-1][_SILENT_KEY] = True
            _stamp_context_used(messages, last)
            return final

        while iteration < iteration_cap:
            # Checked here and not mid-generation: cancelling a call in flight
            # discards a finished generation and leaves no answer, and a deadline
            # landing between a tool result and the model reading it produces a
            # trajectory nothing can interpret. The cost is an overrun of at most
            # one iteration, which the budget's own docstring states.
            if budgets.wall_clock_seconds is not None and (monotonic() - turn_t0) >= budgets.wall_clock_seconds:
                stopped_by = "wall_clock"
                logger.warning(
                    "Wall-clock budget {}s reached after {} iterations; wrapping up",
                    budgets.wall_clock_seconds,
                    iteration,
                )
                break
            iteration += 1
            logger.info(
                "Iteration {}/{} model={}",
                iteration,
                iteration_cap,
                active_model,
            )

            # Mark the episode boundary (one per model call) so an outlet can
            # group this call's reasoning + text + tools into a single step. Not
            # for the continuation of a cut answer: a boundary there would show
            # the reader one answer as two steps.
            if not merge_episode:
                step_shown.clear()
            if on_episode_start is not None and not merge_episode:
                # A bounded per-outlet queue with a wedged worker behind it parks
                # here forever; the boundary is a grouping hint, so losing one is
                # a step drawn wrong, not a turn lost.
                await first_call.stage(
                    "the episode-boundary emit",
                    on_episode_start(iteration - 1),
                    iteration=iteration,
                    fallback=None,
                )
            merge_episode = False

            # Take any INJECT-ed user messages (BusyPolicy.INJECT) into this
            # iteration before its LLM call. Media-carrying injects keep their
            # file paths in the text so nothing is silently dropped. They are
            # appended one per message here and labelled for the provider at the
            # call seam below, so every reader of ``messages`` sees the shape
            # they arrived in.
            if drain is not None:
                for inj in drain():
                    inj_text = inj.text or ""
                    # Only the paths the message does not already name. A send
                    # from the page bakes its own attachment note into the text
                    # and derives ``media`` from it, so naming them again put a
                    # second, raw copy of the path into the reader's own bubble
                    # once the stored entry was drawn.
                    inj_paths = [m.path for m in inj.media if m.path not in inj_text]
                    if inj_paths:
                        prefix = inj_text + "\n" if inj_text else ""
                        inj_text = f"{prefix}[injected message; attached files: {', '.join(inj_paths)}]"
                    if inj_text:
                        # Marked because the queue has now given this up: it was
                        # delivered once, to a turn that may yet be thrown away and
                        # run again. The mark is what lets the rerun carry it.
                        messages.append(
                            {
                                "role": "user",
                                "content": inj_text,
                                # When it arrived, not when the turn happened to
                                # reach a gap: the wait is the whole point of an
                                # inject, and a long turn is where they are sent.
                                "timestamp": inj.received_at or self._now_fn().isoformat(),
                                _MID_TURN_USER_KEY: True,
                            }
                        )
                        logger.info("inject: merged a mid-turn user message")

            # The two window passes, before the snapshot and the hooks below so
            # every reader of ``messages`` this iteration sees the list the model
            # will. The Memory role decides whether the transcript is near its
            # line and what to give up; this loop rebinds what it hands back.
            # Compaction rebinds ``messages``, which is why it sits above the
            # autofill publish: a snapshot published before the rebind would go
            # quietly stale.
            ahead = await _shrink(WindowPressure.PROACTIVE, active_model)
            messages = ahead.messages
            # The standing image window: the notes it writes are the transcript
            # from here on rather than a per-request rewrite.
            standing = await _shrink(WindowPressure.STANDING, active_model)
            messages = standing.messages

            # Same seam and the same reason as the drain above: only the loop's
            # own task may touch ``messages``, and only here is every tool
            # result already in. Reading the turn's autofill is safe at this
            # point -- unlike at question time, where the ACP connection pool
            # carries the wrong turn's ContextVars -- because ``_run_agent_loop``
            # runs below the ``start_ask_turn`` binding in ``RpcTurnRunner.run``.
            auto = current_autofill()
            if auto is not None:
                # Flush before publishing, so a sub-agent asking a second time
                # this turn sees what raven already answered for the first.
                self._flush_autofill(messages, auto.pending_rows())
                # Every iteration, not once: the overflow and image-demotion
                # recoveries below rebind ``messages`` to a fresh list, so the
                # object published last time can stop being the one the turn is
                # building, and a snapshot of it would go quietly stale.
                auto.set_snapshot(messages)

            capability = await self.harness.capability.select(CapabilityRequest(messages=messages, iteration=iteration))
            tool_defs = capability.tools
            iter_msg_base = len(messages)

            if hook_ctx is not None:
                hook_ctx.iteration = iteration
                hook_ctx.messages = messages
                hook_ctx.tools = tool_defs
                hook_ctx.response = None
                # CompositeHook survives a hook that raises and has nothing to
                # say about one that never returns. A neutral decision is what
                # "no hook ran" already means everywhere else here.
                decision = await first_call.stage(
                    "the before_iteration hooks",
                    self.hooks.before_iteration(hook_ctx),
                    iteration=iteration,
                    fallback=HookDecision(),
                )
                if decision.short_circuit_result is not None:
                    final_content = str(decision.short_circuit_result)
                    messages = self.context.add_assistant_message(messages, final_content)
                    break
                _land_hook_note(decision)
                # Taken from the decision, not from ``hook_ctx.tools``: the two
                # happen to be the same object today, and relying on that would
                # make the rule stop firing silently the day get_definitions
                # returns a cached list. The registry itself is never touched.
                if decision.modified_tools is not None:
                    tool_defs = decision.modified_tools

            # Nothing to switch off here any more. When CacheOptimizer runs it
            # stamps the request it marked, and the provider reads that stamp on
            # the way out, so the two cannot both place breakpoints on the same
            # request -- including on a pool-built provider a session switched
            # onto mid-conversation, which is what this block was added for.
            #
            # The switch it used to set lived on the provider *object* and was
            # never unset, so every later consumer of that object -- the Curator,
            # a subagent, Sentinel, the session titler -- kept sending with no
            # breakpoints at all. That was named as known residue when this block
            # landed; asking the request instead is what retires it.

            # TokenWise before-hook: strategies may rewrite messages, tools,
            # or model (e.g. CacheOptimizer marks cache_control blocks).
            # The session's effort first, a hook's rollback override on top: a
            # mode that asks for more thinking, or the session's own pick, sets
            # the turn's default, and a gate re-sampling one call may still
            # move that one call.
            gen_overrides = {
                # The effort in force (``turn_effort``), a hook's rollback
                # override on top. Always sent, None included: the provider's
                # own copy of the key is frozen at construction (see
                # ``default_reasoning_effort``), so a key left out of the call
                # let a default cleared on a running gateway keep running at
                # the word the gateway booted with. An explicit None is the
                # provider's own default, nothing on the wire.
                "reasoning_effort": turn_effort,
                **(pending_gen_overrides or {}),
            }
            # Kept for an unchanged resend: it asks again with whatever rode this call.
            call_overrides = pending_gen_overrides
            pending_gen_overrides = None
            call_reasoning_effort = gen_overrides.get("reasoning_effort")
            last_tool_defs, last_model, last_effort = tool_defs, active_model, call_reasoning_effort
            call_messages, call_tools, call_model = await first_call.stage(
                "the before_llm_call strategies",
                self.strategies.before_llm_call(
                    messages,
                    tool_defs,
                    active_model,
                ),
                iteration=iteration,
                fallback=(messages, tool_defs, active_model),
            )
            # Last, and on the payload only: the strategies above decide against
            # the shape the messages arrived in, and the prompt-cache prefix
            # stays stable because the same arrivals always fold the same way.
            call_messages = merge_mid_turn(call_messages)
            # Sized to the prompt it goes out beside, which is the room the
            # history strategy's trigger holds back: near that line the model's
            # whole ceiling no longer fits, and a provider that checks
            # ``prompt + max_tokens <= window`` refuses the request outright. A
            # hook's own ``max_tokens`` override stands; the doubled ceiling of an
            # output-limit continuation does not, since the same refusal awaits it.
            # Nor may that doubled ceiling pass the model's own: not every provider
            # clamps what it is asked for.
            continuing = call_overrides is not None and call_overrides is continuation_overrides
            if continuing:
                gen_overrides["max_tokens"] = self._wire_output_ceiling(call_model, pinned=gen_overrides["max_tokens"])
            if "max_tokens" not in gen_overrides or continuing:
                if reply_room := self._reply_room(call_messages, call_tools, call_model):
                    gen_overrides["max_tokens"] = min(gen_overrides.get("max_tokens", reply_room), reply_room)
            # A reply that is a silence marker is shown to no one, so the start of one is
            # held until the loop has read the reply. A turn that reads the loose rule holds the
            # whole call, because its marker may close a report and only the end of the call
            # shows that. The gates below deliver into this one.
            if on_token_delta is None or reply_awaited:
                silence_gate = None
            elif carried_gate is not None:
                silence_gate = carried_gate.handed_over()
            else:
                silence_gate = SilenceGate(_shown, hold_all=reads_loose_silence(origin, taught=silence_taught))
            downstream = silence_gate or on_token_delta
            # A hook can send this whole response back, and a rollback pops the
            # history the stream has already left -- so where hooks are installed
            # the deltas are held until something keeps the response. The draft
            # gate owns the continuation head cut in that case: holding the whole
            # text cuts it exactly, so the two gates never stack.
            draft = (
                DraftGate(downstream, cut_head=cut_continuation) if downstream is not None and holds_drafts else None
            )
            gate = (
                ContinuationGate(downstream) if draft is None and cut_continuation and downstream is not None else None
            )
            # Every provider call ``decide`` makes reaches the provider seam, and
            # an Action may make more than one (best-of-n, a critic pass: see
            # ActionRequest), so none of them is claimed here. The session and
            # the sink are both bound so the seam bills each call to this turn's
            # conversation and to this loop's own registry -- a caller entering
            # below ``run_turn`` has bound neither for it, and the sink is bound
            # per turn because a candidate generation is built while the one it
            # replaces may still be serving.
            with (
                usage_record.bind(self.strategies),
                usage_context.bind(session_key) if session_key else contextlib.nullcontext(),
            ):
                unstreamed, unstreamed_next = unstreamed_next, False
                continuing = bool(recovery.parts)
                try:
                    response = await self.harness.action.decide(
                        ActionRequest(
                            provider=self.provider,
                            messages=call_messages,
                            tools=call_tools,
                            model=call_model,
                            # An unstreamed resend is still the streamed turn's call: the provider's
                            # own chain is not walked, and a model switch stays the loop's to make.
                            fallback_models=[] if unstreamed else list(fallbacks_left),
                            # The one streaming call that asks for a cut answer back: a
                            # continuation (``ReplyKind.NETWORK_CUT``) follows it. The
                            # wrap-up and the watch-work judgement call the same method
                            # and have nothing to follow them.
                            stream_call=functools.partial(self._llm_call_stream, hand_back_partial=True),
                            # Spliced here rather than inside the module: the gates are
                            # the shell's own, and which sink a delta reaches is not a
                            # strategy decision. The module reads the field it is handed,
                            # so the stream/retry branch stays exactly the one the loop
                            # took -- no gate is built unless ``on_token_delta``
                            # already is, so the spliced value is None on exactly the
                            # turns the raw sink was, and a reasoning sink alone still
                            # streams.
                            on_token_delta=None if unstreamed else (draft or gate or downstream),
                            on_reasoning_delta=None if unstreamed else on_reasoning_delta,
                            generation_overrides=gen_overrides,
                        )
                    )
                except Exception as exc:
                    if not continuing:
                        raise
                    # A continuation can fail by raising rather than by answering with an error
                    # (``LiteLLMProvider.chat_stream`` raises its HTTP errors), once the stream's
                    # own reconnects are spent. The reader holds the answer so far either way, so
                    # either way the turn ends on it, as a continuation that errored does.
                    logger.warning(
                        "empty-recovery: the continuation of a cut answer raised; ending the turn on the "
                        "{} chars that streamed: {}",
                        sum(len(part) for part in recovery.parts),
                        exc,
                    )
                    final_content = await _end_on_the_partial(response)
                    break
                # Read inside the bind: the count belongs to it and ends with it.
                recorded_at_seam = usage_record.recorded_inbound()
            if unstreamed and response.finish_reason != "error":
                # What the stream would have shown, shown once the whole reply is in and through the
                # same sinks: the thought first, then the words, which pass the gates a streamed
                # delta passes, so a marker is still held and a reply still reaches the reader once.
                if on_reasoning_delta is not None and response.reasoning_content:
                    await on_reasoning_delta(response.reasoning_content)
                if (sink := draft or gate or downstream) is not None and response.content:
                    await sink(response.content)
            if response.finish_reason != "error" and (note := getattr(self.context_engine, "note_request", None)):
                # What went out and was answered, for a history strategy that
                # summarises by replaying it (its prefix is what is cached).
                try:
                    note(session_key or "", call_messages, model=call_model or effective_model, tools=call_tools)
                except Exception:
                    logger.opt(exception=True).error("the context engine failed to note a request")
            # Assigned, not latched: the fact this carries is that the turn's
            # own last word was cut, so a call that recovers clears it. Latching
            # would report a cut to a reader whose question is what the turn
            # delivered, and an earlier iteration that was cut and then answered
            # in full delivered it. Read off `response.truncated` rather than
            # `finish_reason`, because an upstream can answer a ceiling hit with
            # a success claim instead.
            if hook_metadata is not None:
                hook_metadata["output_limited"] = bool(getattr(response, "truncated", False))
            if cut_continuation:
                cut_continuation = False
                if gate is not None:
                    await gate.finish()
                response.content = cut_reasoning_head(response.content)
            if response.tool_calls:
                # A preamble beside tool calls is not the answer a hook holds back
                # as final, nor a reply a marker could be, and holding it would
                # park it for the whole tool run.
                if draft is not None:
                    await draft.release()
                if silence_gate is not None:
                    await silence_gate.release()
            _log_what_the_model_said(response)
            # TokenWise after-hook: strategies observe the response for
            # usage tracking, budget enforcement, etc. Errors are swallowed.
            usage_snapshot = self._build_usage_snapshot(response, call_model, session_key or "")
            # A call behind this response was recorded at the seam and this row
            # would be a second copy of one of them; with no sink bound (a test,
            # an embedder) nothing was recorded and this row is the only one.
            if not recorded_at_seam:
                await self.strategies.after_llm_call(
                    {
                        "content": response.content,
                        "finish_reason": response.finish_reason,
                        "usage": response.usage,
                    },
                    usage_snapshot,
                )
            spend.note(usage_snapshot.cost_usd)
            # The stream caller (turn.* handler) may want the
            # final-iteration usage to populate `message.complete.payload.usage`
            # on the wire. Use the wire-contract TurnUsage
            # fields (prompt_tokens / completion_tokens / total_tokens) — not
            # the agent-internal snapshot with model / cache / cost fields.
            if response.usage:
                prompt_tokens = int(response.usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(response.usage.get("completion_tokens", 0) or 0)
                # One reading for every consumer: the sink gauge below, the
                # proactive compaction trigger at the top of the next
                # iteration, and the reply that stands for this call, which
                # keeps it for a resume (``_stamp_context_used``). They differ
                # in their thresholds, not in what they measure -- and what
                # they measure is how full the window is, which is not what
                # the prompt was billed as (see ``_window_reading``), and a
                # call can give none (see ``_call_window``).
                context_used = _call_window(response)
                if context_used > 0:
                    window.last_context_used = context_used
                    if window_reading is not None:
                        window_reading["context_used"] = context_used
            if usage_sink is not None and response.usage and context_used <= 0 and usage_sink.get("context_used"):
                # No reading -- a block of zeros, or an account ``_call_window``
                # does not take as one -- so the gauge stays on the call before
                # it, as the reading the turn files does (``window_reading``
                # above moves on the same terms). The cost is the turn's, a
                # flow, and moves on regardless. With no reading to keep, the
                # branch below fills the sink: an answered turn sends it whole.
                usage_sink["cost_usd"] = spend.cost_usd
                usage_sink["cost_missing_calls"] = spend.cost_missing_calls
            elif usage_sink is not None and response.usage:
                # An explicitly configured window always wins over the live
                # table -- that is what setting it means. Otherwise the live
                # window from the model's provider table (e.g. OpenRouter,
                # when LiteLLM lags) answers instead; unknown to that table
                # too, 0 tells the UI to show its empty state rather than a
                # number that isn't this model's.
                from raven.providers.binding import active_binding

                configured = (active_binding() or self._default_binding).configured_window
                if configured:
                    context_max = configured
                else:
                    # Off the event loop: allow_fetch=True here can hit the
                    # network for up to 10s on an OpenRouter model with both
                    # caches expired. See rates._fetch_openrouter_models.
                    context_max = await asyncio.to_thread(resolve_context_window, call_model) or 0
                usage_sink.clear()
                usage_sink["prompt_tokens"] = prompt_tokens
                usage_sink["completion_tokens"] = completion_tokens
                usage_sink["total_tokens"] = int(response.usage.get("total_tokens", 0) or 0)
                # The counts above are this call's: what they feed is a gauge --
                # how full the window is now. The cost is the turn's: it is a
                # flow, so it sums every iteration this turn ran and every call
                # its delegations made while it ran. Billing the final call
                # alone reported a fifth of what a delegating turn spent.
                usage_sink["cost_usd"] = spend.cost_usd
                usage_sink["cost_missing_calls"] = spend.cost_missing_calls
                usage_sink["context_max"] = context_max
                usage_sink["context_used"] = context_used
                usage_sink["context_percent"] = round(100 * context_used / context_max) if context_max else 0

            # Recoveries that retry this iteration. The classifier on the response
            # says what kind of refusal this was; the Memory role says whether it
            # has a move left for it and makes it. ``continue`` re-enters the
            # iteration at the top, so the hooks and the standing passes see the
            # retry as they saw the attempt, and ``iteration`` is not billed for
            # a call that did no work.
            cls_ = response.error_classification
            refused = response.finish_reason == "error" and cls_ is not None
            retry_model = call_model or active_model
            if refused and cls_.should_compress:
                shrunk = await _shrink(WindowPressure.OVERFLOW, retry_model)
                messages = shrunk.messages
                if shrunk.changed:
                    iteration -= 1
                    continue
            if refused and cls_.should_drop_tool_images:
                demoted = await _shrink(WindowPressure.TOOL_IMAGES_REFUSED, retry_model)
                messages = demoted.messages
                if demoted.changed:
                    # Cached so the rest of the process stops paying for the
                    # attempt: the static table in ``capabilities`` guessed wrong
                    # about this endpoint, and this is how it self-corrects.
                    self._image_tool_result_ok[retry_model] = False
                    iteration -= 1
                    continue
            if refused and cls_.strip_images:
                withdrawn = await _shrink(WindowPressure.IMAGES_TOO_LARGE, retry_model)
                messages = withdrawn.messages
                if withdrawn.changed:
                    iteration -= 1
                    continue

            if response.has_tool_calls:
                # A reply that does something ends the run of empty ones, and a
                # cut answer the model went on to act on is no longer the start of
                # the one it gives after the tools, which starts with no
                # continuation of its own. It is what this step said beside its
                # call, which the reader saw, so the call's row keeps it, as it came:
                # joined, it is what one uncut call would have said.
                said_beside = (
                    ("".join(recovery.parts) + (response.content or "")) if recovery.parts else response.content
                )
                recovery.empty_streak.clear()
                recovery.parts.clear()
                carried_gate = None
                recovery.step_asks.clear()
                if hook_ctx is not None:
                    hook_ctx.messages = messages
                    hook_ctx.response = response
                    decision = await self.hooks.before_execute_tools(hook_ctx)
                    if _hook_rollback(decision):
                        continue
                    _land_hook_note(decision)
                    if decision.short_circuit_result is not None:
                        # The tool-call response is dropped entirely: persisting
                        # an assistant message whose tool_calls never executed
                        # leaves dangling calls that strict providers reject.
                        final_content = str(decision.short_circuit_result)
                        messages = self.context.add_assistant_message(messages, final_content)
                        _stamp_context_used(messages, response)
                        break
                # Blocking a call is handled where the call is: the
                # branch below refuses it and cancels the siblings the
                # model wrote beside it. The only answer that has to
                # leave that branch is the one about the turn, and this
                # is it. Every refusal asks for ABORT_TURN today; the
                # point of naming it separately is that the one which
                # should not can say so without touching this loop.
                continuation = Continuation.CONTINUE
                abort_reason = ""
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                # Images this transport cannot carry inside a tool result.
                # Collected across the whole batch and attached once *after* the
                # last tool result: a user message sitting between two tool
                # results leaves an assistant tool_call unanswered at the point
                # the API validates the sequence. Measured on gpt-4o, 2026-07-31,
                # two tool calls with the picture from the first:
                #   tool(c1), user, tool(c2) -> 400 "An assistant message with
                #     'tool_calls' must be followed by tool messages responding
                #     to each 'tool_call_id'"
                #   tool(c1), tool(c2), user -> 200, and the model named the
                #     image's colour
                # Anthropic accepts both, so this only bites on Chat Completions
                # -- which is the only transport that takes this path at all.
                pending_images: list[dict[str, Any]] = []
                pending_sources: list[dict[str, Any]] = []
                tool_call_dicts = [openai_tool_call(tc) for tc in response.tool_calls]
                messages = self.context.add_assistant_message(
                    messages,
                    said_beside,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                _stamp_reasoning_ms(messages, response)
                _stamp_context_used(messages, response)

                for tool_call_index, tool_call in enumerate(response.tool_calls):
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    # Skip the message tool: turn.py emits its tool.complete; a
                    # second emit here would double it.
                    emit_tool_event = on_tool_event is not None and tool_call.name != "message"
                    if emit_tool_event:
                        _tool = self.tools.get(tool_call.name)
                        await on_tool_event(
                            "start",
                            {
                                "tool_call_id": tool_call.id,
                                "name": tool_call.name,
                                "arguments": tool_call.arguments,
                                "blocking": self.tools.is_blocking(tool_call.name, tool_call.arguments),
                                # Tool-authored call label; None -> UI derives one.
                                "display": _display_label(_tool, tool_call.arguments),
                            },
                        )
                    # A tool whose output also reaches the UI on a side channel
                    # (exec's inline diff, run_subagent_dag's progress events)
                    # needs this call's id to correlate with the row the UI drew.
                    if (setter := getattr(self.tools.get(tool_call.name), "set_tool_call_id", None)) is not None:
                        setter(tool_call.id)
                    set_current_tool_call_id(tool_call.id)
                    tool_t0 = time.monotonic()
                    tracker = self.strategies.get("usage_tracker")
                    if tracker is not None:
                        await tracker.record_tool_call(tool_call.name, tool_call.id)
                    preempted = ""
                    if tool_call.name == "ask_user":
                        from raven.agent.subagent import watch_work as _ww

                        preempted = _ww.preempt_owner_ask(watch_state, tool_call.arguments)
                    # Not asked of a preempted call: the branch below wins, so
                    # the refusal would be spent on something the model never sees.
                    stalled_verdict, stalled_answer = (
                        (NoProgressAction.RUN, "")
                        if preempted
                        else no_progress.check(tool_call.name, tool_call.arguments)
                    )
                    if preempted:
                        # The owner registered this answer so they would not be
                        # asked for it; the question never reaches them, and the
                        # reply arrives where the model expected the owner's.
                        result = "This question was not sent to the owner."
                        watch_note = preempted
                        duration_ms = int((time.monotonic() - tool_t0) * 1000)
                    elif stalled_verdict is not NoProgressAction.RUN:
                        # The call is established: it has already answered the
                        # same thing often enough that running it again is known
                        # not to change the answer. Answered rather than run, so
                        # what the model reads is an error it has to deal with
                        # instead of the success the nudge was appended to.
                        # A result is still appended for it either way -- an
                        # advertised tool_call id with no result is a 400 from
                        # every strict provider, including on the way out.
                        result = stalled_answer
                        watch_note = ""
                        duration_ms = int((time.monotonic() - tool_t0) * 1000)
                        if stalled_verdict is NoProgressAction.END_TURN:
                            stalled_tool = tool_call.name
                    else:
                        from raven.agent.subagent.delegate import current_delegate

                        table_before = current_delegate()
                        result = await self.tools.execute(
                            tool_call.name, tool_call.arguments, run_meta=tool_call.run_meta
                        )
                        duration_ms = int((time.monotonic() - tool_t0) * 1000)
                        # The result itself is left alone: the note is the
                        # system's own line and is placed by add_tool_result AFTER
                        # the untrusted fence closes, so the model reads it as this
                        # system speaking rather than as data it must not obey.
                        watch_note = await self._note_watch_work(
                            watch_state,
                            tool_call.name,
                            tool_call.arguments,
                            _scrubbed_result(
                                tool_call.arguments, str(result), agent_output=getattr(result, "agent_output", False)
                            ),
                            watch_request,
                            reasoning_effort=turn_effort,
                        )
                        # A call that bound a Worker Table (load_playbook) moved the
                        # dispatch enums under an identity assembled before it.
                        if (table := current_delegate()) and table is not table_before:
                            from raven.context_engine.segments import render

                            watch_note += render.worker_table_note(
                                render.live_dispatch_tools(self.tools.get_definitions)
                            )
                        from raven.context_engine.segments import render

                        # Read off the tool the call reached, as take_metadata is: through
                        # tool_call, the only route to a run_subagent_dag the fold hides,
                        # a dispatch's receipt comes back under tool_call's name.
                        called = self.tools.get(tool_call.name)
                        reached = called.metadata_owner(tool_call.arguments or {}) if called is not None else None
                        if reached is not None and reached.name in render.DISPATCH_TOOLS:
                            channel = (session_key or "").partition(":")[0]
                            watch_note += render.reply_rules_note(
                                board_shown=render.board_on_page(self._task_board_config.enabled, channel, surface)
                            )
                    # The registry already unwrapped any ToolResult: `result` is
                    # the model-facing text, with the optional display string
                    # riding along on it (ToolOutput). The model always gets the
                    # model text; the UI preview prefers the display string.
                    agent_output = getattr(result, "agent_output", False)
                    model_text = _scrubbed_result(tool_call.arguments, str(result), agent_output=agent_output)
                    display_text = getattr(result, "display_text", None)
                    display_src = (
                        _scrubbed_result(tool_call.arguments, display_text, agent_output=agent_output)
                        if display_text
                        else model_text
                    )
                    # The log stays one line; the UI event keeps newlines so a
                    # tool that reports several items (e.g. ask_user's
                    # question -> answer pairs) renders one row each.
                    #
                    # 200 was a row's worth, and the row is not where this ends
                    # up: the detail card shows the same string, where 200 chars
                    # cut an ordinary error message mid-sentence and left the
                    # reader to guess the rest. The cap is what a card can show
                    # without becoming a file viewer, not what a row can.
                    preview = display_src[:_TOOL_PREVIEW_MAX_CHARS]
                    logger.bind(agent_output=agent_output).info(
                        "Tool result: {} duration={}ms result={}",
                        tool_call.name,
                        duration_ms,
                        preview.replace("\n", " ")[:200],
                    )
                    tool_metadata = self.tools.take_metadata(tool_call.name, tool_call.arguments)
                    if agent_output:
                        # Overwritten, not merged: the key is derived from the
                        # result's own provenance and the spec it ran under, and
                        # a tool answering its own ``take_metadata`` with a
                        # different value is answering about the payload it
                        # hands the UI, not about whose words the result holds.
                        # Merging would let a tool's own dict unscrub the
                        # preview; the derivation stays the only writer.
                        tool_metadata = {**(tool_metadata or {}), "agent_output": True}
                    elif isinstance(tool_metadata, dict) and "agent_output" in tool_metadata:
                        tool_metadata = {key: value for key, value in tool_metadata.items() if key != "agent_output"}
                    # What the tool saw go, plus what this turn wrote and can no
                    # longer find. Settled before this call's own write is noted,
                    # so the file it just wrote is not stat'ed to say it exists.
                    tool_removed = removal_watch.settle(getattr(result, "removed", ()))
                    removal_watch.note_write(getattr(result, "file_change", None))
                    tool_written = _file_written_payload(getattr(result, "written", ()))
                    if emit_tool_event:
                        await on_tool_event(
                            "complete",
                            {
                                "tool_call_id": tool_call.id,
                                "result_preview": preview,
                                "truncated": len(display_src) > _TOOL_PREVIEW_MAX_CHARS,
                                "metadata": tool_metadata,
                                # The tool's own verdict, with the registry's
                                # failure text kept as the backstop for a result
                                # that carried none. See ToolEvent.ok.
                                "ok": not call_failed(result),
                                # The one hop the diff has to make by hand: the
                                # registry attaches it to the result, and only
                                # this event reaches a UI.
                                "diff": getattr(result, "diff", None),
                                # Alongside it, for a surface that renders the
                                # change itself rather than a unified diff of it.
                                "file_change": _file_change_payload(getattr(result, "file_change", None)),
                                # The deletions, which no tool reports as its
                                # result: a command's own watch plus the turn's.
                                "file_removed": _file_removed_payload(tool_removed),
                                # And what a command wrote, which its output never
                                # names: the tool reads it off the directory.
                                "file_written": tool_written,
                            },
                        )
                    # A skill the model loaded itself never passes through
                    # SkillForge injection, so report it here or the skill panel
                    # misses the whole class (builtin guides in particular).
                    if tool_call.name in ("read_skill", "use_skill") and not model_text.startswith("Error"):
                        await self._report_skill_read(session_key or "", tool_call.name, tool_call.arguments)
                    result_blocks = _scrubbed_blocks(
                        tool_call.arguments, getattr(result, "blocks", None), agent_output=agent_output
                    )
                    model_text, blocks, attach_blocks = self._route_result_images(
                        model_text, result_blocks, call_model or active_model
                    )
                    # Provenance for the pictures, taken here where the tool, the
                    # round and the captions are all still in one place. The
                    # window reads it back when it withdraws them.
                    sources = image_sources(tool_call.name, result_blocks or [], iteration) if result_blocks else []
                    provenance = {"agent_output": True} if agent_output else {}
                    if blocks:
                        messages = self.context.add_tool_result(
                            messages,
                            tool_call.id,
                            tool_call.name,
                            model_text,
                            blocks,
                            trusted_note=watch_note,
                            **provenance,
                        )
                        if sources:
                            messages[-1][IMAGE_SOURCES_KEY] = sources
                    else:
                        # Keep the long-standing 4-arg call for text results so no
                        # existing caller or test double sees a signature change.
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name, model_text, trusted_note=watch_note, **provenance
                        )
                    if messages:
                        # Dispatch to result, on the entry that answers the call.
                        # The live tool event was its only carrier, so a restored
                        # transcript had to either invent a number or say nothing
                        # about a call that took two minutes.
                        messages[-1][_TOOL_DURATION_MS_KEY] = duration_ms
                        if tool_metadata:
                            messages[-1][_TOOL_METADATA_KEY] = tool_metadata
                    if (tool_diff := getattr(result, "diff", None)) and messages:
                        # Underscore-keyed while the turn is live so no provider
                        # payload grows a field mid-turn; `_save_turn` renames it
                        # to `diff` on the stored entry. Without this the diff
                        # exists only on the live tool event, and a reloaded page
                        # can never number a change it no longer has.
                        messages[-1]["_diff"] = tool_diff
                    if tool_removed and messages:
                        # The same underscore-then-rename convention as the diff
                        # above, and line counts rather than bodies: a removed
                        # file's text is what the live event carries, while what
                        # a reloaded page needs is that the file went and how big
                        # the hole is.
                        messages[-1]["_file_removed"] = [
                            {"path": removal.path, "del": len((removal.before or "").splitlines())}
                            for removal in tool_removed
                        ]
                    if tool_written and messages:
                        # Same underscore-then-rename convention, and here the
                        # stored shape is the live one: what it carries is already
                        # a count and a size rather than a body.
                        messages[-1]["_file_written"] = tool_written
                    if attach_blocks:
                        pending_images.extend(attach_blocks)
                        pending_sources.extend(sources)
                    if getattr(result, "blocks_call", False):
                        continuation = getattr(result, "continuation", Continuation.ABORT_TURN)
                        # The blocking tool's own words, kept for the reader: the
                        # canned reply below says an operation stopped but never
                        # which one, so without this the user is told a thing
                        # happened and given no way to find out what.
                        abort_reason = _first_line(model_text)
                        # A single assistant message may contain several parallel
                        # tool calls (for example ``rm`` followed by a Python
                        # fallback). Once policy terminates the action, none of
                        # the siblings may execute. We must nevertheless append
                        # one result for every advertised call id: OpenAI-style
                        # providers reject conversation history containing an
                        # assistant tool call without its matching tool result.
                        for skipped_call in response.tool_calls[tool_call_index + 1 :]:
                            messages = self.context.add_tool_result(
                                messages,
                                skipped_call.id,
                                skipped_call.name,
                                SKIPPED_AFTER_BLOCKED_CALL,
                            )
                        break
                    # Track consecutive same-tool deterministic failures
                    # (transient errors excluded — a retry would clear those).
                    if is_hard_tool_failure(model_text):
                        failure_key = (tool_call.name, failure_class(model_text))
                        if failure_key == loop_fail_key:
                            loop_fail_streak += 1
                        else:
                            loop_fail_key, loop_fail_streak = failure_key, 1
                    else:
                        loop_fail_key, loop_fail_streak = None, 0
                    # Counted for every call, failures included: a call failing
                    # identically is stuck too, and the branch above just has an
                    # earlier threshold for that shape. Not for a call the ladder
                    # answered itself, which ran nothing and so answered nothing.
                    # The routed blocks, not the raw ones: what the model receives is
                    # what decides whether this call answered anything new.
                    if stalled_verdict is NoProgressAction.RUN:
                        no_progress.record(tool_call.name, tool_call.arguments, model_text, blocks or attach_blocks)

                if continuation is Continuation.ABORT_TURN:
                    # A normal tool result starts another model iteration. That
                    # is specifically unsafe here: the next plan can translate
                    # the rejected operation into an equivalent interpreter,
                    # script, or tool call. Finish the turn in runtime code and
                    # expose only the non-destructive continuation question.
                    # The model must read this, so it goes into the history as an
                    # assistant message -- but it goes to the CLIENT as a notice.
                    # Pushed down the token stream instead, it arrived as the
                    # model's own prose: glued to whatever the model had just
                    # narrated (nothing separates two segments in one buffer),
                    # dressed in the answer's copy and branch actions, and always
                    # in English no matter what language the turn was in.
                    from raven.spine.events import NoticeKind as _NoticeKind

                    messages = self.context.add_assistant_message(messages, _ABORTED_ACTION_REPLY)
                    if messages:
                        messages[-1][_NOTICE_KEY] = {
                            "kind": _NoticeKind.ACTION_BLOCKED.value,
                            **({"detail": abort_reason} if abort_reason else {}),
                        }
                    final_content = _ABORTED_ACTION_REPLY
                    if on_notice is not None:
                        # Not staged the way the retry notice is: this notice IS
                        # the turn's answer, so abandoning it on a wedged outlet
                        # queue would end the turn saying nothing at all.
                        await on_notice(_NoticeKind.ACTION_BLOCKED, abort_reason)
                    elif on_token_delta is not None:
                        # A channel with no notice outlet still has to say
                        # something, and silence is the worse failure.
                        await on_token_delta(_ABORTED_ACTION_REPLY)
                    break

                # The no-progress ladder's last step. Placed with the aborted
                # action rather than with the nudges below: no further model
                # call happens, so an appended nudge would be read by nobody and
                # a pending picture would be shown to nobody. The batch this
                # call sat in was allowed to finish first -- every advertised
                # tool_call id therefore has its result, which is what strict
                # providers validate on the way out too.
                if stalled_tool is not None:
                    logger.warning(
                        "`{}` repeated with an identical result past the refusal budget; ending the turn",
                        stalled_tool,
                    )
                    break

                # Failure-loop break: the same tool failed deterministically
                # `threshold` times running → append a change-approach nudge to
                # the last tool result so the model stops repeating a dead call.
                if (
                    loop_fail_streak >= self._LOOP_BREAK_THRESHOLD
                    and loop_nudges < self._LOOP_BREAK_MAX
                    and messages
                    and messages[-1].get("role") == "tool"
                ):
                    loop_nudges += 1
                    messages[-1]["content"] = (
                        str(messages[-1].get("content", ""))
                        + "\n\n"
                        + loop_break_nudge(
                            loop_fail_key[0],
                            loop_fail_streak,
                            loop_fail_key[1],
                            suggest_find_skill=self.tools.offers_by_name("find_skill"),
                        )
                    )
                    loop_fail_streak = 0  # fire once per fresh streak
                # And the other stuck shape: the call works, and keeps saying the
                # same thing. Second, because the failure nudge is the more
                # specific advice and reaches its threshold first. Taken only
                # here, where the tool result it belongs to is still the last
                # message -- the guard holds it armed until then.
                elif messages and messages[-1].get("role") == "tool" and (nudge := no_progress.take_nudge()):
                    messages[-1]["content"] = str(messages[-1].get("content", "")) + "\n\n" + nudge
                # After the nudge above, which needs the last message to still be
                # the tool result it appends to. Also after the blocked-call
                # branch, which ends the turn in runtime code -- there is no
                # further model call to show a picture to, so an aborted action
                # deliberately drops it rather than leaving it dangling.
                if pending_images:
                    messages.append(
                        {
                            "role": "user",
                            "content": pending_images,
                            ATTACHED_IMAGE_KEY: True,
                            IMAGE_SOURCES_KEY: pending_sources,
                        }
                    )
                # Dispatched before prev_had_tool_calls is set: a rollback means
                # this iteration never happened, so the empty-response classifier
                # must see the pre-iteration state on the re-sample.
                if hook_ctx is not None:
                    hook_ctx.messages = messages
                    hook_ctx.response = response
                    decision = await self.hooks.after_iteration(hook_ctx)
                    if _hook_rollback(decision):
                        continue
                    _land_hook_note(decision)
                    if decision.short_circuit_result is not None:
                        final_content = str(decision.short_circuit_result)
                        messages = self.context.add_assistant_message(messages, final_content)
                        break
                prev_had_tool_calls = True
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops.
                if response.finish_reason == "error":
                    if recovery.parts:
                        # The continuation of a cut answer failed. The reader holds the answer so far, and a
                        # wait or another model would only ask for more of it again, so the turn ends here as
                        # an exhausted continuation does: what streamed, said to be partial, and no failure.
                        logger.warning(
                            "empty-recovery: the continuation of a cut answer failed; ending the turn on the "
                            "{} chars that streamed: {}",
                            sum(len(part) for part in recovery.parts),
                            (clean or "")[:160],
                        )
                        final_content = await _end_on_the_partial(response)
                        break
                    # The provider's ladder is seconds long and has already run. A
                    # gateway serving error pages for a few minutes outlasts it, and
                    # ending the turn here threw away an hour of work on a 40-second
                    # outage. So a retryable failure waits out a longer ladder before
                    # the turn is given up -- the messages are untouched (nothing was
                    # appended for the failed call), so asking again is the same ask.
                    verdict = response.error_classification
                    if verdict is None and (classify := getattr(self.provider, "classify_error", None)) is not None:
                        verdict = classify(content=clean or None)
                    # A streamed call whose error came back as a response goes to the
                    # next fallback model before the turn waits. That is every failure
                    # that arrives without an exception, and a raised one whose recovery
                    # is the caller's and that came before any output -- a stall (a
                    # first-byte or idle timeout) or a ``strip_images`` verdict
                    # (``stream_llm_call``). Any other error the provider raises never
                    # reaches here: the stream reconnects and waits out this same ladder
                    # itself (``retry_delays``), then the error fails the turn with no
                    # fallback model tried. A plain call walks ``[model, *fallbacks]``
                    # inside the provider's own retry layer, and has already been
                    # through it by the time it comes back an error.
                    if streamed_call and fallbacks_left and verdict is not None and verdict.should_fallback:
                        previous = _switch_to_next_model()
                        logger.warning(
                            "LLM error [{}] on {}; trying fallback model {}: {}",
                            verdict.category,
                            previous,
                            active_model,
                            (clean or "")[:160],
                        )
                        iteration -= 1  # the failed call did no work; don't bill it
                        continue
                    ladder = self._recovery_limits.llm_error_retry_delays
                    if verdict is not None and verdict.retryable and error_waits < len(ladder):
                        delay = ladder[error_waits]
                        error_waits += 1
                        logger.warning(
                            "LLM error [{}] outlasted the provider's retries; asking again in {:.0f}s (wait {}/{}): {}",
                            verdict.category,
                            delay,
                            error_waits,
                            len(ladder),
                            (clean or "")[:160],
                        )
                        await _announce_wait(verdict.category)
                        iteration -= 1  # the failed call did no work; don't bill it
                        await asyncio.sleep(delay)
                        continue
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    status = "error"
                    error_detail = _llm_failure_detail(clean, verdict)
                    error_full = response_failure_text(clean, response.call_record)
                    _stamp_context_used_on_kept(messages, response)
                    break

                if recovery.parts:
                    # A cut answer that, joined to what continued it, says nothing but a thought is
                    # the reply an uncut stream would have given: it is read as that one -- the
                    # thinking-only moves, then the failure -- rather than as an answer with no text.
                    so_far = "".join(recovery.parts)
                    joins = leaves_think_block_open(so_far) or clean
                    whole = (so_far + (response.content or "")) if joins else so_far
                    if not self._strip_think(whole):
                        response = replace(response, content=whole)
                        clean = None
                        recovery.parts.clear()
                        carried_gate = None

                if not (clean or "").strip():
                    _stamp_context_used_on_kept(messages, response)

                # Empty-response recovery. A reply with no tool call is read for
                # what it is (``reply_kind``) and ``decide`` names the move; an
                # empty assistant turn would otherwise end here as a turn with no
                # answer, which fails. Synthetic scaffolding is marked
                # ``_recovery_synthetic`` and stripped before persistence /
                # extraction so it can't poison future context.
                limits = self._recovery_limits
                kind = reply_kind(
                    response,
                    clean,
                    origin=origin,
                    prev_had_tool_calls=prev_had_tool_calls,
                    streamed=streamed_call,
                    reply_awaited=reply_awaited,
                    silence_taught=silence_taught,
                )
                if kind in (ReplyKind.EMPTY, ReplyKind.POST_TOOL_EMPTY):
                    recovery.empty_streak.append(EmptyAttempt.of(response, call_model))
                else:
                    recovery.empty_streak.clear()
                # Asked of the provider for the model this call went to: a
                # prefill-refusing vendor (Anthropic with thinking on) must never
                # be handed a request ending on an assistant message.
                supports_prefill = getattr(self.provider, "supports_assistant_prefill", None)
                resends_allowed = resend_budget(response, limits)
                # Asked of the provider, not of the ladder: two labels can be one
                # request. ``reasoning_wire_keys`` says what a rung would actually
                # send, so a rung the wire cannot tell apart from the one that
                # just came back thinking is skipped instead of paid for again.
                next_rung = (
                    next_step_down(
                        step_down_effort or call_reasoning_effort,
                        _reasoning_wire_keys(self.provider, call_model),
                        used=recovery.rungs_used,
                    )
                    if kind is ReplyKind.THINKING_ONLY
                    else None
                )
                action = decide(
                    kind,
                    recovery,
                    limits,
                    prefill_supported=supports_prefill is None or supports_prefill(call_model),
                    thinking_to_refeed=has_thinking_to_refeed(response),
                    next_rung=next_rung,
                    fallback_left=bool(fallbacks_left),
                    resend_budget=resends_allowed,
                )
                answered_by_tool = False
                if action in _GIVEN_UP_ONCE_ANSWERED:
                    turn_rows = turn_log.messages() if turn_log is not None else messages
                    answered_by_tool = self._answered_by_tool(turn_rows[turn_start_idx:])
                if answered_by_tool:
                    # The reader already has the answer, sent or delivered by a tool. A
                    # reply the instant moves (prefill, nudge, continuation) did not
                    # mend is not worth a backed-off re-ask of the same step, a model
                    # switch, or a verdict on a turn that answered: it ends answered,
                    # with no text of its own, as it did before recovery could tell
                    # the difference.
                    logger.info(
                        "empty-recovery: a tool already answered this turn; ending it on a {} reply instead of {}",
                        kind.name,
                        action.name,
                    )
                    action = RecoveryAction.COMPLETE
                if action in _ASKS_AGAIN:
                    recovery.step_asks[kind] += 1
                if action is RecoveryAction.PREFILL:
                    recovery.prefills += 1
                    logger.warning(
                        "empty-recovery: thinking-only prefill {}/{}",
                        recovery.prefills,
                        limits.thinking_prefill_max_retries,
                    )
                    # Re-feed the model its own reasoning (not stripped) so it
                    # continues into the body. Marked synthetic → dropped before
                    # persistence/extraction; the reasoning fields are stripped
                    # from the wire request by the provider's key allowlist.
                    messages = self.context.add_assistant_message(
                        messages,
                        response.content,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    )
                    messages[-1]["_recovery_synthetic"] = True
                    prev_had_tool_calls = False
                    # Reasoning that ended on the ceiling was cut mid-thought, so
                    # the continuation opens on the rest of that thought.
                    cut_continuation = response.finish_reason == "length"
                    continue
                if action is RecoveryAction.NUDGE:
                    recovery.nudges += 1
                    logger.warning("empty-recovery: post-tool empty nudge")
                    # The (empty) assistant must sit between the tool result and
                    # the nudge — a bare tool→user sequence is a 400 on most APIs.
                    messages = self.context.add_assistant_message(messages, "(empty)")
                    messages[-1]["_recovery_synthetic"] = True
                    messages.append({"role": "user", "content": POST_TOOL_NUDGE, "_recovery_synthetic": True})
                    prev_had_tool_calls = False
                    continue
                if action is RecoveryAction.RETRY:
                    # Nothing in a plain empty reply says what to change, and each
                    # guess made here once -- a lower effort, down to a `minimal`
                    # that Codex refuses with a 400 -- was a request some provider
                    # rejected or repeated at full price. The request goes back as
                    # it was, after a wait: a backend that answered nothing is
                    # given a moment, and this is not a fresh step, so no
                    # iteration is billed.
                    recovery.resends += 1
                    delay = empty_retry_delay(recovery.resends)
                    logger.warning(
                        "empty-recovery: resending the request unchanged {}/{} in {:.1f}s",
                        recovery.resends,
                        resends_allowed,
                        delay,
                    )
                    pending_gen_overrides = call_overrides
                    if streamed_call and kind is ReplyKind.EMPTY and not unstreamed_spent:
                        unstreamed_next = unstreamed_spent = True
                    await _announce_wait("empty_reply")
                    iteration -= 1
                    await asyncio.sleep(delay)
                    continue
                if action is RecoveryAction.STEP_DOWN:
                    # A thinking-only reply is a call whose thinking spent the output
                    # ceiling before the answer began (measured: five in a row, each
                    # cut at exactly the ceiling), so the request that has to change
                    # is the one saying how much of it thinking may take. The descent
                    # rides the loop's per-call override lane (``pending_gen_overrides``,
                    # which a hook rollback uses to re-sample one call) and is spent on
                    # this re-ask only -- a call that answers leaves the turn's own
                    # effort standing. Each rung is asked for once.
                    recovery.rungs_used.add(next_rung)
                    step_down_effort = next_rung
                    delay = empty_retry_delay(len(recovery.rungs_used))
                    logger.warning(
                        "empty-recovery: thinking-only reply, asking again at reasoning_effort {} (was {}) in {:.1f}s",
                        next_rung,
                        call_reasoning_effort or "unstated",
                        delay,
                    )
                    # The prefills were the continuation of a thought that is being
                    # given up: left in, the request would end on an assistant
                    # message, which a prefill-refusing provider rejects.
                    _drop_trailing_prefills(messages)
                    pending_gen_overrides = {"reasoning_effort": next_rung}
                    await _announce_wait("thinking_only")
                    iteration -= 1
                    await asyncio.sleep(delay)
                    continue
                if action is RecoveryAction.FALLBACK:
                    # The same step asked again, of the next model, so no iteration is billed.
                    previous = _switch_to_next_model()
                    # A refusal is switched on once: the next model's own refusal is
                    # the upstream's verdict again, and ends the turn.
                    recovery.refusal_fell_back = recovery.refusal_fell_back or kind is ReplyKind.REFUSAL
                    logger.warning(
                        "empty-recovery: {} reply on {}; trying fallback model {}", kind.name, previous, active_model
                    )
                    iteration -= 1
                    continue
                if action is RecoveryAction.CONTINUE and kind is ReplyKind.NETWORK_CUT:
                    # The stream broke mid-answer. What arrived is kept and the model
                    # is asked to go on from it. Kept as it came, think tags and the
                    # space at the cut included: a cut can split a think block or two
                    # words, and the reply is read once, whole.
                    recovery.continuations += 1
                    recovery.parts.append(response.content or "")
                    logger.warning(
                        "empty-recovery: stream cut mid-answer after {} chars; continuation {}/{}",
                        len(clean or ""),
                        recovery.continuations,
                        MAX_CONTINUATIONS,
                    )
                    # What streamed is the answer so far, and a reader whose deltas
                    # were held back for a hook is shown it before the rest follows --
                    # unless it may still be a marker: the silence gate keeps holding it,
                    # and the continuation's deltas join it there, until the reply the
                    # turn ends on is read.
                    if draft is not None:
                        await draft.release()
                    if silence_gate is not None:
                        silence_gate.carry()
                        carried_gate = silence_gate
                    messages = self.context.add_assistant_message(messages, clean)
                    messages[-1]["_recovery_synthetic"] = True
                    messages.append({"role": "user", "content": NETWORK_CUT_CONTINUATION, "_recovery_synthetic": True})
                    prev_had_tool_calls = False
                    merge_episode = True
                    continue
                if action is RecoveryAction.CONTINUE and kind is ReplyKind.TRUNCATED:
                    # Cut at the ceiling with nothing in it: the model is told so
                    # and given more room. Effort is left alone -- there was no
                    # reasoning to blame. The ceiling doubles per continuation from
                    # the one the first cut call carried; the call holds it to the
                    # model's own output ceiling and to the room its prompt leaves
                    # in the window.
                    recovery.continuations += 1
                    if recovery.max_tokens_base is None:
                        recovery.max_tokens_base = response.max_tokens
                    if recovery.max_tokens_base:
                        pending_gen_overrides = {"max_tokens": recovery.max_tokens_base * 2**recovery.continuations}
                        continuation_overrides = pending_gen_overrides
                    logger.warning(
                        "empty-recovery: reply cut at the output ceiling with nothing in it; continuation {}/{} (max_tokens {})",
                        recovery.continuations,
                        MAX_CONTINUATIONS,
                        (pending_gen_overrides or {}).get("max_tokens", "unchanged"),
                    )
                    # Same assistant-then-user shape as the nudge, for the same reason.
                    messages = self.context.add_assistant_message(messages, "(empty)")
                    messages[-1]["_recovery_synthetic"] = True
                    messages.append({"role": "user", "content": OUTPUT_LIMIT_CONTINUATION, "_recovery_synthetic": True})
                    prev_had_tool_calls = False
                    continue
                if action is RecoveryAction.FAIL:
                    # Same shape as the provider-error exit above, because it is
                    # the same kind of outcome: the turn produced nothing and the
                    # caller has to be able to act on that. What it buys is the
                    # label every surface puts on the turn -- the web page, the
                    # terminal, ``raven agent -p``'s exit code and a channel's
                    # reply -- plus the marker filed in the session and the
                    # failure a cron record and a trajectory reader keep. It is
                    # not about ACP's stop reason: that answers ``end_turn`` with
                    # the failure as text for every failed turn by design.
                    # The words are per kind, because the reader who has to
                    # decide what to do next is a person or a judging model, and
                    # neither can see this log line.
                    logger.error(
                        "empty-recovery: {} reply after {} prefill(s), {} nudge(s), {} resend(s), "
                        "{} continuation(s); ending the turn as an error",
                        kind.name,
                        recovery.prefills,
                        recovery.nudges,
                        recovery.resends,
                        recovery.continuations,
                    )
                    error_full = _recovery_failure_text(kind, response, clean, recovery)
                    error_detail = bound_failure_text(error_full)
                    no_output = kind in (ReplyKind.EMPTY, ReplyKind.POST_TOOL_EMPTY)
                    # Kept as the returned content too. Nothing delivers it: the caller fails
                    # the turn on ``error``, and a turn the message tool had answered never
                    # reaches this branch.
                    final_content = error_detail
                    status = "error"
                    break

                # A continuation that came back a marker on its own has nothing to add.
                own_marker = action is RecoveryAction.SILENT
                if recovery.parts:
                    # The answer is what streamed across every call, joined, read as a reply with
                    # no cut is: closed think blocks out of the whole, since a cut can split one,
                    # and one never closed left in as text. A
                    # continuation inside a block the cut left open is read only that way, as the
                    # rest of a thought and not a reply of its own. Otherwise one that came back
                    # empty, as think-tag debris or as a marker leaves the partial as it was.
                    so_far = "".join(recovery.parts)
                    in_a_thought = leaves_think_block_open(so_far)
                    own_marker = own_marker and not in_a_thought
                    adds = (response.content or "") if (in_a_thought or clean) and not own_marker else ""
                    clean = self._strip_think(so_far + adds) or ""
                # The reply the turn ends on is read for silence once it is whole: a cut is
                # continued rather than judged on its fragment, so after one -- or when one can
                # no longer be continued -- the joined text is what may be the marker.
                if recovery.parts or kind is ReplyKind.NETWORK_CUT:
                    ends_silent = not reply_awaited and is_silence(clean, origin, taught=silence_taught)
                    if own_marker and not ends_silent and not reply_awaited:
                        # Dropping a continuation that is only the marker rests on the reader already
                        # holding what streamed, which a turn holding its whole call never showed. So
                        # the marker is read with what came before it, on its own line, as the same
                        # reply uncut would be; only the loose rule can find it there.
                        with_marker = clean + "\n" + (response.content or "").strip()
                        if is_silence(with_marker, origin, taught=silence_taught):
                            clean, ends_silent = with_marker, True
                else:
                    ends_silent = action is RecoveryAction.SILENT
                dropped_marker = own_marker and not ends_silent

                # Before the text is persisted, so a short-circuit replaces it
                # without leaving the replaced draft in history and a rollback
                # discards-and-re-samples it. The stream is held to match: a
                # rollback pops history, and only the draft gate can keep the
                # reader from having seen what history no longer has. The hooks
                # read the reply the turn keeps: after a continuation, the whole
                # answer rather than the last call's fragment of it.
                if hook_ctx is not None:
                    hook_ctx.messages = messages
                    hook_ctx.response = replace(response, content=clean) if recovery.parts else response
                    decision = await self.hooks.after_iteration(hook_ctx)
                    if _hook_rollback(decision):
                        if draft is not None:
                            draft.discard()
                        continue
                    if draft is not None:
                        await draft.release()
                    await _settle_silence(silence_gate, silent=ends_silent, dropped_marker=dropped_marker)
                    _land_hook_note(decision)
                    if decision.short_circuit_result is not None:
                        final_content = str(decision.short_circuit_result)
                        messages = self.context.add_assistant_message(messages, final_content)
                        _stamp_context_used(messages, response)
                        break
                else:
                    await _settle_silence(silence_gate, silent=ends_silent, dropped_marker=dropped_marker)

                if kind is ReplyKind.NETWORK_CUT and action is RecoveryAction.COMPLETE and not ends_silent:
                    # Cut, and nothing will ask the model to go on: the continuations are
                    # spent, or recovery is switched off, which is the same with none made.
                    # The reader gets what there is, and the note says it is not the whole
                    # of it. Pushed after the hooks above, so a rolled-back reply never
                    # carries it.
                    note = "\n\n" + PARTIAL_STREAM_NOTE
                    clean += note
                    status = "interrupted"
                    sink = draft or on_token_delta
                    if sink is not None:
                        await sink(note)
                messages = self.context.add_assistant_message(
                    messages,
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                _stamp_reasoning_ms(messages, response)
                _stamp_context_used(messages, response)
                # Kept in the history, as the row it is, and filed as a marker so a
                # reload does not read it as an answer.
                silent = ends_silent
                if silent:
                    messages[-1][_SILENT_KEY] = True
                final_content = clean
                # Reachable with no text only when recovery is switched off, or
                # when the message tool had already answered.
                no_output = not clean
                ended_answered = answered_by_tool
                break

        # A row recorded while a tool ran rides the next iteration's flush,
        # because the model always has to be shown the results. The exits that
        # skip that next pass -- max iterations, a blocked call -- lose the
        # write-back. The row was already rendered live and the answer already
        # went to the sub-agent, so this is worth a line, not a repair.
        leftover = current_autofill()
        if leftover is not None and (lost := leftover.pending_rows()):
            logger.info("question autofill: {} row(s) ended the turn unwritten", len(lost))

        exhausted = (
            final_content is None
            and not ended_answered
            and (stalled_tool is not None or iteration >= iteration_cap or stopped_by == "wall_clock")
        )
        # Assigned, not latched, as ``output_limited`` is: a rerun's own run decides.
        if hook_metadata is not None:
            hook_metadata["budget_stopped"] = exhausted
        if exhausted and recovery.parts:
            # The turn ran out -- of iterations or of time -- with a cut answer waiting on its
            # continuation, which there is no room left to ask for. A wrap-up would stream onto
            # the partial the reader already holds, so the partial is the answer.
            stopped_by = "stalled_tool" if stalled_tool is not None else stopped_by or "iteration_cap"
            logger.warning(
                "Turn stopped ({}) with a cut answer awaiting its continuation; ending on what streamed", stopped_by
            )
            final_content = await _end_on_the_partial(response)
            # The budget stopped it, so it is interrupted even when its reply is the marker,
            # as a wrap-up that is one is.
            status = "interrupted"
        elif exhausted:
            if stalled_tool is not None:
                stopped_by = "stalled_tool"
                logger.warning("Turn stopped on a repeating `{}`; synthesizing final answer", stalled_tool)
            elif stopped_by == "wall_clock":
                logger.warning("Wall-clock budget reached; synthesizing final answer")
            else:
                stopped_by = "iteration_cap"
                logger.warning("Max iterations ({}) reached; synthesizing final answer", iteration_cap)
            # Exhaustion is two orthogonal facts, not an either/or:
            #   1. The turn did NOT complete — tag it ``interrupted`` so the
            #      shadow-git checkpoint commit is labelled and the next turn's
            #      recovery prompt can surface the sha + edited files to resume.
            #   2. The user still deserves a useful reply NOW — so, checkpoint
            #      or not, synthesize a best-effort wrap-up with tools disabled
            #      (and one format repair if requested) instead of a
            #      canned apology. Synthesis falls back to a static message
            #      internally if the call fails, so the turn never ends empty.
            status = "interrupted"
            # The wrap-up is the turn's last reply, so it may be a Silence Marker as an
            # answer may: its stream is held back as the answering call's is, and a
            # marker ends the turn silent.
            # The wrap-up streams into the last call's step, after the narration that step keeps.
            step_shown.clear()
            wrap_up_gate = (
                SilenceGate(_shown, hold_all=reads_loose_silence(origin, taught=silence_taught))
                if on_token_delta is not None and not reply_awaited
                else None
            )
            final_content = await self._synthesize_final_on_exhaustion(
                messages,
                active_model,
                list(fallbacks_left),
                on_token_delta=wrap_up_gate or on_token_delta,
                on_reasoning_delta=on_reasoning_delta,
                reasoning_effort=turn_effort,
                prompt=_MAX_ITER_SYNTHESIS_PROMPT if stalled_tool is None else _STALLED_SYNTHESIS_PROMPT,
                fallback=(
                    _STALLED_STATIC_FALLBACK.format(tool=stalled_tool)
                    if stalled_tool is not None
                    else _WALL_CLOCK_STATIC_FALLBACK
                    if stopped_by == "wall_clock"
                    else _MAX_ITER_STATIC_FALLBACK.format(n=self.max_iterations)
                ),
                synthesis_policy=turn_synthesis(turn_meta),
            )
            silent = not reply_awaited and is_silence(final_content, origin, taught=silence_taught)
            await _settle_silence(wrap_up_gate, silent=silent)
            # Persist the wrap-up into history like any normal final reply.
            # Persistence downstream reads only the returned ``messages`` list,
            # so without this the synthesized answer reaches the user via the
            # stream yet never enters the conversation — the next turn (notably
            # an interrupted-turn resume) could not see what was summarized. The
            # synthesis prompt itself stays local to the helper, so only the
            # reply lands here.
            if final_content:
                messages = self.context.add_assistant_message(messages, final_content)
                if silent:
                    messages[-1][_SILENT_KEY] = True

        # Drop transient empty-recovery scaffolding before persistence /
        # extraction / return: empty recovery marks synthetic nudge/prefill
        # messages with ``_recovery_synthetic``; strip them so they never
        # persist. The after-turn pipeline (``context_engine.after_turn`` +
        # ``backend.store`` in ``_process_message``) owns extraction.
        # Attached-image messages are dropped here for the same reason and at the
        # same point: the returned list feeds persistence, ``after_turn``
        # extraction and ``backend.store`` alike, so filtering once upstream of
        # all three is the only place that covers them.
        # The terminal seam: a turn that ended with nothing a reader can see --
        # a provider error, or an exhausted budget whose wrap-up came back
        # empty -- gets one last chance to commit an answer.
        answerless = status == "error" or not (final_content or "").strip()
        if hook_ctx is not None:
            # Written for every turn, not only an answerless one. "This turn was cut
            # short" is not recoverable from ``status``: the iteration cap, a stalled
            # tool and a spent wall clock all land on ``interrupted``, and the wrap-up
            # reply they produce reads as an ordinary answer to everything downstream.
            # Ints and None only -- an observer chain that keeps scalars by type drops a
            # float without a word.
            #
            # Two scopes sit in here and ``attempt`` is what tells them apart. A turn a
            # product budgets a rerun for runs this loop more than once, and the count
            # below is of THIS run, because that is what the loop counts; the elapsed
            # time is of the whole turn, because that is what the budget bounds. One
            # iteration beside twelve seconds otherwise reads as a single slow call.
            # ``salvaged`` joins the record below, after the terminal seam returns, so
            # the gate reading this record is never the one that sees it.
            hook_ctx.metadata["turn_end"] = {
                "status": status,
                "attempt": attempt,
                "iterations": iteration,
                "stopped_by": stopped_by,
                "wall_clock_budget_s": int(budgets.wall_clock_seconds) if budgets.wall_clock_seconds else None,
                "turn_elapsed_s": int(monotonic() - turn_t0),
            }
        # An attempt the caller is about to run again is not salvaged first. The gate
        # behind this seam can spend minutes manufacturing an answer, and both ends
        # that answer can meet are wrong: the rerun discards it, having charged the
        # turn's own clock for it, or -- when the turn ended answerless without
        # erroring -- it fills the emptiness the rerun reads and the rerun never
        # happens, so salvage wins by suppressing the thing measured to beat it.
        #
        # Asked for every turn rather than only an answerless one, and asked once:
        # the caller decides the rerun on this same answer, and a question put twice
        # can come back differently the second time -- the checkpoint and the persist
        # between here and there are seconds a clock can cross. A loop nobody
        # budgeted a rerun for asks no one and salvages as it always did.
        rerun_coming = rerun_pending is not None and rerun_pending(final_content, log.messages(), status)
        if hook_ctx is not None and answerless and not rerun_coming:
            hook_ctx.messages = messages
            hook_ctx.response = None
            decision = await self.hooks.terminal_answerless(hook_ctx)
            if decision.short_circuit_result is not None:
                final_content = str(decision.short_circuit_result)
                messages = self.context.add_assistant_message(messages, final_content)
                hook_ctx.metadata["turn_end"]["salvaged"] = True
                error_detail = None
                error_full = None
                no_output = False

        if any(any(m.get(k) for k in _TURN_TRANSIENT_KEYS) for m in messages):
            messages = [m for m in messages if not any(m.get(k) for k in _TURN_TRANSIENT_KEYS)]

        # Extraction belongs to the caller's after-turn pipeline
        # (``context_engine.after_turn`` + ``backend.store`` + ``backend.feedback``
        # run from ``_process_message``); ``outcome.status`` is surfaced so that
        # pipeline can gate on completion.

        outcome = LoopOutcome(
            status=status,
            error=error_detail,
            error_full=error_full,
            no_output=no_output,
            silent=silent,
            shown="".join(step_shown) if silent else "",
            last_tools=last_tool_defs,
            last_model=last_model,
            last_effort=last_effort,
        )
        checkpoint = self._turn_checkpoint()
        if checkpoint is not None:
            # Per-turn snapshot: one commit covering all of this turn's edits,
            # for both normal and interrupted exits (matches Claude Code/Cursor
            # granularity). Best-effort — commit_turn never raises.
            # The label is read in ``git log`` inside the shadow repo by
            # someone recovering a file. The repo already implies the
            # directory, so the session key is what disambiguates -- several
            # sessions share one shadow repo whenever they resolve to the same
            # directory (LAUNCH_DIR, or an explicit workdir override).
            label = f"turn {session_key} [{status}]" if session_key else f"turn [{status}]"
            cid, changed = await checkpoint.commit_turn(label)
            outcome.checkpoint_id = cid
            if status == "interrupted":
                outcome.edited_files = changed

        return final_content, tools_used, messages, outcome

    # root: a turn is the top of its own trace. Without it a turn opened while
    # another span is still active -- a dispatch that reports back, a follow-up
    # driven by a tool's own completion -- nests inside that span, and the two
    # turns share one trace.
    @trace.instrument(
        "session.turn", root=True, seed=semconv.turn_seed, on_open=semconv.turn_open, extract=semconv.turn
    )
    async def _process_message(  # noqa: C901 (cc 47: pre-existing, above the ceiling)
        self,
        req: TurnRequest,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_token_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_event: Callable[[str, dict], Awaitable[None]] | None = None,
        on_episode_start: Callable[[int], Awaitable[None]] | None = None,
        on_notice: Callable[[NoticeKind, str], Awaitable[None]] | None = None,
        usage_sink: dict[str, Any] | None = None,
        origin: Origin | None = None,
        drain: Drain | None = None,
        hook_sink: dict[str, str] | None = None,
        silence: dict[str, bool | str] | None = None,
        reply_awaited: bool = False,
    ) -> tuple[str | None, list[str]] | None:
        """Process a single turn request and return its reply.

        Returns ``(reply_content, media_paths)`` for a turn that produced an
        outbound reply, or ``None`` when there is nothing to send (the message
        tool already sent, a hook short-circuit chose to return None, or the
        model ended the turn on a silence marker). ``origin`` is the
        spine TurnRequest's origin. ``hook_sink``, when given, receives under
        ``"appended"`` whatever the ``after_send`` chain added to the end of the
        reply: a streamed reply has already left as deltas by then, so the
        caller has to send that tail itself. ``silence``, when given, receives
        ``"silent": True`` for the last of those: a turn the model ended on a
        marker, and that nothing replaced with a reply of its own -- and, when
        that reply had streamed anything first, ``"shown"`` (``LoopOutcome.shown``).
        It receives ``"delivered_files": True`` for a turn a tool handed the
        reader files in: those went out with the call, so such a turn answered
        whatever it ends on, and is never a Silent Turn.
        ``reply_awaited`` keeps a turn whose caller waits on its text out of
        the silence protocol (``_run_agent_loop``).
        """
        from raven.agent.hook import AgentHookContext

        # Captured before any pre-turn work (hooks, personalization, context
        # assembly): this is when the user's message arrived, and it becomes the
        # stored user entry's timestamp. Everything saved by _save_turn is
        # stamped at turn END, so without this the whole turn shares one clock
        # read and a restored transcript cannot say how long the turn took.
        turn_received_at = self._now_fn().isoformat()
        # Taken before any awaited work: this is the turn's own span, which the
        # opening user entry is stored against.
        turn_trace_id = _turn_trace_id()

        channel = req.source.channel
        sender_id = req.source.sender_id
        chat_id = req.source.chat_id
        content = req.text
        metadata = dict(req.source.extras)
        media_paths = [m.path for m in req.media]
        msg_session_key = req.conversation or f"{channel}:{chat_id}"

        # AgentHook ``before_user_inbound`` chain. The chain runs once and:
        #   - lets observer hooks (FeedbackTracker, the on_user_inbound
        #     adapter) record engagement;
        #   - lets short-circuit hooks (DecisionConsumer adapter for
        #     Sentinel /pick replies) halt processing and return their
        #     (content, media) reply directly.
        #
        # Skip the user-inbound hooks for Sentinel / subagent turns (by origin).
        inbound_original = content
        skip_user_inbound = origin in _SKIP_USER_INBOUND_ORIGINS
        if skip_user_inbound:
            from raven.agent.harness.participants import Intake, read_intake
            from raven.agent.subagent.charter import charter_participants
            from raven.contracts.participant import StepView

            participants = charter_participants()
            if participants:
                peeked = self.sessions.peek(msg_session_key)
                step = StepView(
                    session_key=msg_session_key,
                    iteration=0,
                    response=None,
                    transcript=(),
                    history=tuple(peeked.messages if peeked is not None else ()),
                    turn_base=0,
                    question=content,
                    rollbacks=0,
                    mode=None,
                    mode_overlay=None,
                    phase="user_inbound",
                )
                answer = await self.harness.memory.ask_intake(content, step, participants)
                intake = answer if answer is None or isinstance(answer, Intake) else read_intake(answer, text=content)
                if intake is not None:
                    if intake.reply is not None:
                        return str(intake.reply), []
                    content = intake.text

        # The turn's one hook-metadata dict, and the words the user actually
        # sent: the first crosses every phase group with the turn, the second
        # is what history keeps no matter how hooks rewrite the model's view.
        turn_hook_meta: dict[str, Any] = {}
        if len(self.hooks) > 0 and not skip_user_inbound:
            _peeked = self.sessions.peek(msg_session_key)
            _hook_ctx = AgentHookContext(
                session_key=msg_session_key,
                turn_request=req,
                inbound_content=content,
                session_history=_peeked.messages if _peeked is not None else [],
                metadata=turn_hook_meta,
            )
            _decision = await self.hooks.before_user_inbound(_hook_ctx)
            if _decision.short_circuit_result is not None:
                result = _decision.short_circuit_result
                if isinstance(result, tuple) and len(result) == 2:
                    return result
                return str(result), []
            if _decision.modified_content is not None:
                content = _decision.modified_content

        preview = content[:80] + "..." if len(content) > 80 else content
        logger.info("Processing message from {}:{}: {}", channel, sender_id, preview)

        # NOTE: the Sentinel ``decision_consumer`` short-circuit lives in the
        # unified AgentHook ``before_user_inbound`` chain at the
        # top of this method. Reaching this point means no hook claimed
        # the message and we proceed to normal slash-command / agent-loop
        # processing.

        key = session_key or msg_session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = content.strip().lower()
        if cmd == "/new":
            try:
                if not await self.memory_consolidator.consolidate_unconsolidated(session):
                    return (
                        "Memory consolidation failed, session not cleared. Please try again.",
                        [],
                    )
            except Exception:
                logger.exception("/new consolidation failed for {}", session.key)
                return (
                    "Memory consolidation failed, session not cleared. Please try again.",
                    [],
                )

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            from raven.agent.task_board.service import reset_for

            await reset_for(self.task_board, session.key, self.sessions.session_dir(session.key))
            return ("New session started.", [])
        if cmd == "/help":
            lines = [
                "🐦‍⬛ Raven commands:",
                "/new — Start a new conversation",
                "/stop — Stop the current task",
                "/restart — Restart the bot",
                "/help — Show available commands",
            ]
            return ("\n".join(lines), [])
        if not self.harness.memory.owns_compaction:
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        # ── Personalization flow (global switch: agents.defaults.enablePersonalization) ──
        # Skip for a subagent result re-injection: its content is a system-generated
        # announce, not user input — personalizing it would pollute the profile or
        # fire a clarification on the announce. Only SUBAGENT skips here (not the
        # wider after-send / user-inbound sets): a Sentinel notice and cron/heartbeat
        # reach this flow today and keep it.
        if self.personalization_enabled and origin is not Origin.SUBAGENT:
            from datetime import datetime as _dt

            from raven.agent.personalizer import Personalizer

            _personalizer = Personalizer(MemoryStore(self.workspace), self.provider, self.model)

            # ── Step 2 completion: user is answering a pending clarification ──
            if session.pending_clarification:
                _pending = session.pending_clarification

                # Determine whether the user is answering the previous question
                # or starting a fresh request. A fresh request typically contains
                # action verbs and is unrelated to the original; re-classify to
                # decide: if clarification is still needed, treat it as new.
                _recent = session.get_history(max_messages=4)
                _recheck = await _personalizer.classify(content, history=_recent)
                _is_new_request = _recheck.get("needs_clarification", False)

                if _is_new_request:
                    # User started a new request; discard the old pending state and re-classify.
                    session.pending_clarification = None
                    self.sessions.save(session)
                    logger.info("Personalization: new request detected, discarding old pending_clarification")

                    _question = await _personalizer.generate_question(
                        content,
                        _recheck.get("domain", ""),
                    )
                    if _question:
                        _ts = _dt.now().isoformat()
                        self._record_inbound(session, content, origin, _ts, trace_id=turn_trace_id)
                        session.record({"role": "assistant", "content": _question, "timestamp": _ts})
                        session.pending_clarification = {
                            "original_message": content,
                            "question": _question,
                            "domain": _recheck.get("domain", ""),
                        }
                        self.sessions.save(session)
                        logger.info(
                            "Personalization: asked clarification for new request, session {}",
                            session.key,
                        )
                        return (_question, [])
                    # Clear pending state and proceed normally when question generation fails.
                    session.pending_clarification = None

                else:
                    # User is answering the previous question; extract preference and resume the original task.
                    session.pending_clarification = None

                    # Extract preference into MEMORY.md in the background without blocking the response.
                    async def _extract():
                        await _personalizer.extract_and_store_preference(
                            original_message=_pending["original_message"],
                            question=_pending["question"],
                            answer=content,
                        )

                    _t = asyncio.create_task(_extract())
                    self._consolidation_tasks.add(_t)
                    _t.add_done_callback(self._consolidation_tasks.discard)
                    # Continue normally: LLM understands the task via conversation history.

            else:
                # ── Step 1: classify the request — decide whether clarification is needed ──
                _recent = session.get_history(max_messages=4)
                _classification = await _personalizer.classify(content, history=_recent)

                if _classification.get("needs_clarification"):
                    # ── Step 2: pre-action interaction — generate and return a clarifying question ──
                    _question = await _personalizer.generate_question(
                        content,
                        _classification.get("domain", ""),
                    )

                    if _question:
                        # Write the original request and the clarifying question into history to keep the conversation coherent.
                        _ts = _dt.now().isoformat()
                        self._record_inbound(session, content, origin, _ts, trace_id=turn_trace_id)
                        session.record({"role": "assistant", "content": _question, "timestamp": _ts})

                        # Save the pending state so the next message can resume it.
                        session.pending_clarification = {
                            "original_message": content,
                            "question": _question,
                            "domain": _classification.get("domain", ""),
                        }
                        self.sessions.save(session)

                        logger.info("Personalization: asked clarification for session {}", session.key)
                        return (_question, [])
                    # generate_question failed: skip silently and proceed
        # ── End personalization flow ─────────────────────────────────────────

        self._set_tool_context(
            channel, chat_id, metadata.get("message_id"), session_key=key, surface=req.source.surface
        )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()
        # ask_user keys by the true conversation_id (== the lane / gate key),
        # which is topic-aware (req.conversation), not just channel:chat_id.
        if (ask_tool := self.tools.get("ask_user")) and isinstance(ask_tool, AskUserTool):
            ask_tool.set_context(key)

        # No playbook interception. A playbook is one of the things the model can
        # reach for this turn (`load_playbook`), not something that decides ahead
        # of it: the funnel that used to sit here judged one message with no
        # history and, on a hit, replaced the whole turn -- so the party with the
        # least context made the most expensive call. All that remains per turn is
        # telling the tool what the turn is about, so its listing can be ranked.
        if self._playbooks is not None:
            self._playbooks.set_context(channel=channel, chat_id=chat_id, session_key=key, surface=req.source.surface)
            if (pb_tool := self.tools.get("load_playbook")) is not None and hasattr(pb_tool, "set_turn_message"):
                pb_tool.set_turn_message(content)

        context_messages = self._context_messages_for_session(session)
        # SkillForge: Selector picks top-K. See note in the system-message
        # branch above — empty return falls back to the full directory.
        # Routed via ``_select_skills_for_turn`` so the engine can
        # short-circuit selection here.
        selected_skills = await self._select_skills_for_turn(
            content,
            context_messages,
        )
        # ── Model selection: the session's own pick, else the router ─────────
        # Ahead of assembly, not after it: assembly decides whether an
        # attachment is inlined as a picture or described in text, and that
        # question is about the model the request will actually reach. Routing
        # needs only ``content``, so asking first costs nothing and stops one
        # model's verdict from shaping a message another model receives.
        # A per-session model is an explicit user choice, so it outranks the
        # router's heuristic and suppresses its fallback chain.
        routed_model: str | None = session.metadata.get("model")
        fallback_models: list[str] = []
        if routed_model is not None:
            logger.info("Session model: {} → {}", self.model, routed_model)
        elif self.router is not None:
            routed_model, fallback_models = await self.router.select_model_chain(content)
            if routed_model and routed_model != self.model:
                logger.info("Router: {} → {}", self.model, routed_model)
            if fallback_models:
                logger.info("Router fallback chain: {}", fallback_models)

        initial_messages = await self._assemble_context_messages(
            session=session,
            session_key=key,
            current_message=content,
            media=media_paths if media_paths else None,
            channel=channel,
            chat_id=chat_id,
            surface=req.source.surface,
            selected_skills=selected_skills or None,
            model=routed_model,
        )
        if (origin_mark := _runtime_origin(req.origin)) and initial_messages:
            # Stamped on the envelope the assembler just built, which is the entry
            # ``_save_turn`` persists as this turn's user message. Marked here
            # rather than inside the assembler because the origin is a fact about
            # the request, and the assembler is handed a turn, not a request.
            last = initial_messages[-1]
            if last.get("role") == "user":
                last[_ORIGIN_KEY] = origin_mark
        # Planning's one seat, and deliberately after the stamp above: the
        # origin mark belongs on the envelope the assembler built, so a planner
        # that returns a different list must not be able to move which message
        # gets marked. The default passes the list straight through, which is
        # what Raven has always done -- planning is the model's own, and the
        # position *ahead* of the turn was measured to be the wrong one for a
        # harness to take it (see ``harness/planning.py``).
        planning = await self.harness.planning.prepare(
            PlanningRequest(task=content, session_key=key, messages=initial_messages)
        )
        initial_messages = planning.messages
        # Surface the skills SkillForge injected this turn to the web UI's skill
        # panel (populated into _last_injected_skill_ids by the assemble above).
        await self._emit_injected_skills(key)
        degraded = getattr(self, "_last_degraded_segments", [])
        if degraded and on_notice is not None:
            # The answer was produced without an optional organ; saying so is
            # the degrade-with-notice ruling -- silence here would make a
            # memoryless answer indistinguishable from a remembered one.
            await on_notice(
                NoticeKind.ORGAN_DEGRADED,
                "Some capabilities were unavailable this turn ("
                + ", ".join(degraded)
                + "); the answer was produced without them.",
            )

        turn_start_idx = len(initial_messages) - 1
        # The assembled list ends with THIS turn's user message, which is the
        # entry the mark belongs on.
        if req.delegated and initial_messages:
            initial_messages[-1][_DELEGATED_KEY] = dict(req.delegated)
        # The question reaches disk here rather than with the rest of the turn.
        # A session whose first turn is still running had nothing on disk at
        # all, so it was absent from every listing, and a reader who left the
        # page could not find their way back to the turn still running in it.
        # Everything below that slices "what this turn added" off the session
        # counts from this turn's own first entry. Counted after the save, not
        # before it: a save can merge another writer's messages in ahead of the
        # question, which it leaves last.
        question_at = len(session.messages)
        self._save_turn(
            session,
            initial_messages,
            turn_start_idx,
            received_at=turn_received_at,
            inbound_original=inbound_original,
            trace_id=turn_trace_id,
        )
        question = session.messages[question_at:]
        self.sessions.save(session)
        prev_len = len(session.messages) - len(question)

        def _turn_start() -> int:
            """Where this turn's entries start now; a later save may have merged more in ahead of them."""
            if question:
                for index in range(len(session.messages) - 1, -1, -1):
                    if session.messages[index] is question[0]:
                        return index
            return min(prev_len, len(session.messages))

        # Where the three writes that close the turn start from. The question is
        # already filed, and filing it again would both double it and stamp the
        # turn's arrival clock onto the first message injected mid-turn.
        persist_from = turn_start_idx + 1
        # The stream buffers exist so a turn that dies mid-answer still has the
        # text that was already on the reader's screen: the loop only appends an
        # assistant message once the provider call returns, so a cancel in the
        # middle of one would otherwise lose exactly what streamed.
        streamed: dict[str, str] = {"text": "", "thought": ""}
        # The turn's episode counter, kept out here rather than taken from the loop.
        # ``EpisodeStart.index`` is the 0-based step within the TURN, and the loop
        # numbers from its own iteration count, which restarts whenever the turn is
        # run again. The TUI keys episode rows and their fold state by this index, so
        # a second attempt beginning at zero would share both with the first.
        episodes: dict[str, int] = {"next": 0}

        async def _tap_token(delta: str) -> None:
            streamed["text"] += delta
            if on_token_delta is not None:
                await on_token_delta(delta)

        async def _tap_reasoning(delta: str) -> None:
            streamed["thought"] += delta
            if on_reasoning_delta is not None:
                await on_reasoning_delta(delta)

        async def _tap_episode(_index: int) -> None:
            # A new episode is a new stream: without the reset, a buffer that
            # spans two assistant messages matches neither and would be saved
            # as a duplicate of text the loop already committed.
            streamed["text"] = ""
            streamed["thought"] = ""
            index = episodes["next"]
            episodes["next"] = index + 1
            if on_episode_start is not None:
                await on_episode_start(index)

        from raven.agent.subagent.attachments import turn_attachments
        from raven.agent.subagent.mode_tiers import turn_tier

        # The turn's cost, opened around the attempts rather than inside one:
        # every attempt is this turn spending, and so is every sub-agent it
        # dispatches, which bills in from its own session by this key.
        spend = TurnSpend(key)
        # The step-down rungs are asked for once a turn, and a rerun is the same
        # turn: a set per attempt would pay for ``high`` and ``low`` again.
        rungs_used: set[str] = set()

        # The newest window a call of this turn measured, held across attempts.
        # A turn that fails or is stopped after calls that each came back empty
        # or as an error has filed no row of its own, and the question it would
        # otherwise end on is already on disk, so its marker carries the reading.
        reading: dict[str, int] = {}

        async def _attempt(seed: list[dict], attempt: int):
            pending.update(rerun=False, reasons=[])
            # The record this attempt persists from, for the rescue paths below as
            # well as the healthy one. A rerun keeps a record of its own, and a turn
            # cancelled during one used to be saved from the first attempt's -- so a
            # tool the reader watched run in the rerun, and its result, were not in
            # what the cancel filed. A record rather than the list the attempt
            # appends to, because the window passes replace that list mid-turn.
            live["log"] = TurnLog(seed, persist_from)
            return await self._run_agent_loop(
                seed,
                on_progress=on_progress,
                session_key=key,
                model=routed_model,
                fallback_models=fallback_models,
                injected_skill_ids=self._collect_injected_skill_ids(selected_skills),
                on_token_delta=_tap_token if on_token_delta is not None else None,
                on_reasoning_delta=_tap_reasoning if on_reasoning_delta is not None else None,
                on_tool_event=on_tool_event,
                on_episode_start=_tap_episode,
                on_notice=on_notice,
                usage_sink=usage_sink,
                drain=drain,
                hook_metadata=turn_hook_meta,
                session_history=session.messages[: _turn_start()],
                origin=req.origin,
                turn_started_at=turn_t0,
                attempt=attempt,
                rerun_pending=_rerun_pending,
                spend=spend,
                turn_log=live["log"],
                rungs_used=rungs_used,
                reply_awaited=reply_awaited,
                window_reading=reading,
                silence_taught=teaches_silence(content),
                turn_start_idx=turn_start_idx,
                # Read here, beside the model, because this is where the
                # session is: the loop below is handed a key, not a record.
                session_effort=session_effort_of(session.metadata),
                surface=req.source.surface,
            )

        # Taken BEFORE the first attempt, because the loop appends to the list it is
        # handed: rebuilt afterwards it would carry the failed attempt's research, and
        # the whole point is to start again from the question.
        budgets = turn_budgets(turn_hook_meta)
        ask_kind = turn_ask_kind(turn_hook_meta)
        retries_left = budgets.dead_end_retries
        attempt_no = 1
        retry_seed = [dict(m) for m in initial_messages] if retries_left else None
        turn_t0 = monotonic()
        pending: dict[str, Any] = {"rerun": False, "reasons": []}
        live: dict[str, TurnLog] = {"log": TurnLog(initial_messages, persist_from)}

        def _seed_for_the_rerun(msgs: list[dict]) -> list[dict]:
            """The question again, plus whatever the reader said while the attempt ran.

            The rerun exists to discard an attempt's research, and a correction typed
            mid-turn is not research: the loop took it off the inject queue, which will
            not offer it twice, so a seed rebuilt from the question alone silently
            un-asks it. Read off the attempt's record rather than its window, because
            compaction may have summarised the correction away, and rebuilt from the
            untouched snapshot each time, so carrying one forward twice cannot double it.
            """
            return [dict(m) for m in retry_seed] + [dict(m) for m in msgs if m.get(_MID_TURN_USER_KEY)]

        def _dead_reasons(final_content: str | None, msgs: list[dict], status: str) -> list[str]:
            # The scaffolding goes before the reading. This question used to be asked
            # after the attempt returned, on the list it had already been dropped from,
            # and it is asked inside the attempt now; a recovery nudge left sitting at
            # the end would otherwise become what the turn is judged to have ended on.
            turn = [m for m in msgs[turn_start_idx:] if not any(m.get(k) for k in _TURN_TRANSIENT_KEYS)]
            reasons = dead_reasons(messages=turn, final_content=final_content, status=status, ask_kind=ask_kind)
            if budgets.dead_end_reasons:
                reasons = [r for r in reasons if r.startswith(budgets.dead_end_reasons)]
            return reasons

        def _clock_spent() -> bool:
            return bool(budgets.wall_clock_seconds) and (monotonic() - turn_t0) >= budgets.wall_clock_seconds

        def _rerun_pending(final_content: str | None, msgs: list[dict], status: str) -> bool:
            # The turn's one rerun decision, made where the attempt ends. The loop
            # below reads the answer rather than working it out again, so the seam
            # that skipped a salvage on a yes here cannot then meet a no there and
            # leave the turn with neither.
            reasons = _dead_reasons(final_content, msgs, status)
            # Every attempt spends the one turn clock, so a rerun gets what the
            # attempts before it left rather than a fresh copy. A turn whose budget
            # is already gone stops with what it has rather than starting an attempt
            # that would break on its first iteration -- and, saying so here, keeps
            # the salvage it would otherwise have skipped for a rerun it cannot run.
            spent = bool(reasons) and _clock_spent()
            if spent:
                logger.info("Dead end ({}); not re-running: the turn's clock is spent", ", ".join(reasons))
            pending.update(rerun=bool(retries_left) and bool(reasons) and not spent, reasons=reasons)
            return pending["rerun"]

        turn_window: list[dict] = []
        try:
            # The tier this turn dispatches sub-agents at, frozen here for the
            # same reason the iteration cap is read once: a switch arriving mid-turn
            # lands on the next turn, not on a sub-agent this one has yet to call.
            # And its attachments, for the same reader: a dispatch that names a
            # file the user attached is handing it over, one that names any other
            # .pptx is not, and only the turn knows which is which.
            with turn_tier(self.session_tier(key)), turn_attachments(req.media), spend.collecting():
                final_content, _, turn_window, outcome = await _attempt(initial_messages, attempt_no)
                # The conditional rerun. A dead turn has no answer to damage -- "empty
                # implies wrong" is a scoring rule, so the count of right answers among
                # dead turns starts at zero and a second attempt can only raise it. It
                # re-runs from the original question rather than salvaging the failed
                # attempt, because salvage was measured to turn a detectable zero into a
                # confident wrong answer and to empty this trigger at the same time.
                # Unreachable for an agent whose hooks leave no budget, which is every
                # agent but the one that asked for it.
                #
                # The second half of the condition is the answer the attempt that just
                # ended gave; the first is the bound, kept here rather than left to a
                # callback, because a loop whose only exit is a value someone else
                # writes has no exit a reader of this loop can check. The two read one
                # counter for two questions: this one asks how many reruns are left to
                # run, the seam asks whether the attempt it is ending has one after it,
                # which is what decides that attempt's salvage.
                while retries_left > 0 and pending["rerun"]:
                    retries_left -= 1
                    attempt_no += 1
                    logger.info("Dead end ({}); re-running the turn", ", ".join(pending["reasons"]))
                    if on_progress is not None:
                        # Neutral about what the turn was doing and about which
                        # attempt this is: the loop serves every agent, and the
                        # budget allows more reruns than the one.
                        await on_progress("That attempt produced no answer; running the turn again.")
                    final_content, _, turn_window, outcome = await _attempt(
                        _seed_for_the_rerun(live["log"].messages()), attempt_no
                    )
                # Session-level because this turn may persist no assistant row at
                # all -- a turn whose whole budget went to reasoning has no
                # message to hang a record on. Stamped with the index this turn's
                # rows start at, so a reader can tell the fact apart from an
                # earlier turn's.
                #
                # Written OR cleared every turn, which is what actually makes it
                # turn-scoped: the index alone would only be enough if it never
                # went backwards, and `Session.clear()` (what `/new` calls) resets
                # it while `undo_last_turn` rewinds it, neither touching metadata.
                # An old marker could then sit at an index a later turn's own
                # start satisfies, and that turn would be reported as cut -- a
                # false fact, which is worse than the missing one this exists to
                # supply.
                #
                # Ahead of the answerless exit below rather than after it: the
                # turn most likely to have been cut at the ceiling is the one that
                # came back with nothing, and left downstream of the raise that
                # turn both loses its own marker and leaves an earlier turn's
                # standing -- which is exactly the reading ACP's ``max_tokens``
                # refinement makes.
                if turn_hook_meta.get("output_limited"):
                    session.metadata[OUTPUT_LIMIT_TURN_AT] = _turn_start()
                else:
                    # None rather than dropping the key: a save merges its
                    # metadata over the record on disk, so a key left unsaid is
                    # kept rather than cleared (SessionManager._metadata_to_write).
                    # The reader asks whether this is an int, which None is not.
                    session.metadata[OUTPUT_LIMIT_TURN_AT] = None
                # The same slot for a turn its own budget ended -- the tool cap, the
                # wall clock or a stalled tool. Its wrap-up reads as an ordinary
                # answer, so a surface that reports the turn (ACP's stop reason) has
                # only this to tell it from one that finished. Not every
                # ``interrupted`` turn: one that ends on a cut stream's partial is
                # interrupted too, and no budget of its was spent. Written or
                # cleared on the same terms as above.
                budget_stopped = outcome.status == "interrupted" and bool(turn_hook_meta.get("budget_stopped"))
                session.metadata[BUDGET_STOP_TURN_AT] = _turn_start() if budget_stopped else None
                # The loop gave up on the model. Its ladder, its rerun and its
                # salvage have all had their say, so what is left is a turn with
                # no answer: the failure the handler below files and the lane
                # reports, not a reply for the outlets to deliver.
                answered = self._answered_by_tool(live["log"].messages()[turn_start_idx:])
                if not answered:
                    if outcome.error:
                        raise AnswerlessTurnError(
                            outcome.error, full_detail=outcome.error_full, no_output=outcome.no_output
                        )
                    if final_content is None:
                        # The other way a turn arrives here with nothing: recovery
                        # switched off, so the empty response was taken at its word
                        # and no budget was ever spent. Same fact as the exit above
                        # -- the model said nothing -- so it is reported the same
                        # way rather than dressed as a reply.
                        raise AnswerlessTurnError(_NO_CONTENT_UNRECOVERED, no_output=True)
        except asyncio.CancelledError:
            # A stop is not a failure, but it is also not amnesia: what already
            # streamed is work the reader saw, so it lands in the session with a
            # note saying a person ended the turn. Then the cancel proceeds.
            self._save_broken_turn(
                session,
                live["log"].messages(),
                persist_from,
                None,
                streamed,
                status="cancelled",
                context_used=reading.get("context_used"),
            )
            self._park_task_board(key, live["log"].added(), turn_received_at, req.turn_id)
            raise
        except AnswerlessTurnError as exc:
            # The attempt's record, which follows every list a window pass rebound
            # `messages` to, so the marker is filed after this turn's work rather
            # than straight after the question.
            self._save_broken_turn(
                session,
                live["log"].messages(),
                persist_from,
                None,
                streamed,
                status="failed",
                reason=str(exc),
                full_detail=attach_full_detail(exc),
                context_used=reading.get("context_used"),
            )
            self._park_task_board(key, live["log"].added(), turn_received_at, req.turn_id)
            raise
        except Exception as exc:
            self._save_broken_turn(
                session,
                live["log"].messages(),
                persist_from,
                None,
                streamed,
                status="failed",
                # Bounded the way the lane bounds the same crash for its event:
                # an arbitrary exception message is filed in a session a reader
                # and a model both read back.
                reason=bound_failure_text(str(exc)),
                full_detail=attach_full_detail(exc),
                context_used=reading.get("context_used"),
            )
            self._park_task_board(key, live["log"].added(), turn_received_at, req.turn_id)
            raise
        self._stash_recovery(key, outcome)
        # The model's last reply is a marker: nothing is sent for it, though the turn is filed
        # and the send chain below still runs. Cleared if a hook composes a reply in its place.
        silent = outcome.silent

        if final_content is None:
            # Only a turn a tool already answered reaches this now: the guard
            # above fails an unanswered one. What follows is the record, the
            # after_send chain and the log line, and each of them wants a string.
            final_content = NO_RESPONSE_FALLBACK

        # AgentHook ``after_send`` chain — typically a Sentinel
        # NudgeInjector / response_modifier modifying the outbound text. Skip it
        # for system-originated turns (Sentinel / subagent) so their reply
        # doesn't get a nudge layered on. A menu pick is USER (its reply IS the
        # user's intent — runs after_send); the Sentinel supersede notice is
        # SENTINEL and a subagent result is SUBAGENT (both skip). A silent turn
        # runs it like any other: what a hook composes there, a deck built this
        # turn, is still this turn's to deliver.
        skip_after_send = origin in _SKIP_AFTER_SEND_ORIGINS
        if len(self.hooks) > 0 and not skip_after_send:
            from raven.agent.hook import AgentHookContext

            _send_ctx = AgentHookContext(
                session_key=key,
                # None for a marker: the chain keeps the turn's books on a silent turn
                # too, but there is no reply for a hook to modify or append to.
                outbound_content=None if silent else final_content,
                session_history=session.messages[: _turn_start()],
                metadata=turn_hook_meta,
            )
            _send_decision = await self.hooks.after_send(_send_ctx)
            composed = _send_decision.modified_content
            if composed is not None and (not silent or composed.strip()):
                if hook_sink is not None:
                    hook_sink["appended"] = _appended_by_hook(None if silent else final_content, composed)
                # With no reply of the model's own, the separator a hook put in front of
                # its text separates nothing. The tail handed to a streaming caller keeps
                # it: words the model said beside a tool call may be on screen already.
                final_content = composed.lstrip() if silent else composed
                silent = False

        # A hook chain's per-turn observer record files onto the turn's last
        # substantive assistant message at persist time -- after the send fire,
        # so a stash from any phase, ``after_send`` included, reaches the filed
        # record. Per turn, on the turn -- not a session-wide last write.
        # The record, not the window the last call saw: a summary, an elided
        # body or a dropped stretch of the turn is the window's business, and
        # filing it would replay it to every later turn as what happened.
        turn_msgs = live["log"].messages(_TURN_TRANSIENT_KEYS)
        message_tool = self.tools.get("message")
        sent_by_tool = isinstance(message_tool, MessageTool) and message_tool.sent_in_turn
        if outcome.silent and not silent and not sent_by_tool:
            # A hook composed the reply the reader is sent in place of the marker. A turn the
            # message tool answered sends no reply of its own, so its marker row stays.
            _file_composed_reply(turn_msgs, final_content)
        delivered_files = _delivered_files(turn_msgs[turn_start_idx:])
        answered_by_tool = self._answered_by_tool(turn_msgs[turn_start_idx:])
        if answered_by_tool:
            _stamp_replied_via_tool(turn_msgs, turn_start_idx)
        if delivered_files and silence is not None:
            silence["delivered_files"] = True
        if len(self.hooks) > 0:
            _stamp_turn_observers(turn_msgs, turn_hook_meta, turn_start_idx)

        self._save_turn(session, turn_msgs, persist_from)
        # Taken before the save, which may merge another writer's messages in.
        turn_entries = session.messages[_turn_start() :]
        self.sessions.save(session)
        await self.harness.memory.after_turn(
            key,
            {
                "final_content": final_content,
                "messages": _without_scratch(turn_msgs[turn_start_idx:]),
            },
        )
        # Plugin-side indexing (third peer step in the after-turn pipeline).
        self._dispatch_backend_store(key, turn_entries)
        # Forward source-qualified skill-usage feedback. Only
        # ``everos/`` prefix is forwarded to the plugin; static-library
        # sources (``local`` / ``mass``) have no feedback channel.
        await self._dispatch_backend_feedback(
            key,
            self._collect_injected_skill_ids(selected_skills),
        )
        if not self.harness.memory.owns_compaction:
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        # ── Step 4: post-action learning (background, non-blocking) ─────────────
        # Skip for a subagent result re-injection (see the pre-turn flow above):
        # its content is a system-generated announce, not user input to learn from.
        if self.personalization_enabled and origin is not Origin.SUBAGENT and not silent:
            from raven.agent.personalizer import Personalizer

            _p4 = Personalizer(MemoryStore(self.workspace), self.provider, self.model)

            async def _post_learn():
                await _p4.post_learn(content, final_content)

            _t4 = asyncio.create_task(_post_learn())
            self._consolidation_tasks.add(_t4)
            _t4.add_done_callback(self._consolidation_tasks.discard)
        # ── End Step 4 ──────────────────────────────────────────────────────

        # ── Step 5: the host's task board (background, after the reply) ──────
        # Scheduled, never awaited: the reply is already the caller's, and the
        # board settles a few seconds after it.
        if self.task_board is not None:
            shown = await self.task_board.ack_turn_notes(key)
            self._schedule_task_board(
                key,
                turn_window,
                outcome,
                live["log"].added(),
                turn_received_at,
                req.turn_id,
                fallback_models,
                delegated=req.delegated,
                moved_on=[n.group for n in shown if n.kind == "move_on" and n.group],
                # The turn's own verdict rather than the marker: a turn a tool answered is not a
                # Silent Turn whatever it ended on, and a hook's reply has cleared ``silent``.
                silent=silent and not answered_by_tool,
            )

        if origin is Origin.USER:
            self._maybe_annotate_tail(session, _turn_start())

        if sent_by_tool:
            # Defensive fingerprint. A silent return None would leave no
            # trace when the agent replied via the message tool, making
            # stochastic dud-turn bugs invisible to grep. Log
            # the would-be response so future investigations have a trail
            # parallel to "Response to ..." below.
            if final_content:
                preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
                logger.info(
                    "MessageTool sent in turn for {}:{}: {}",
                    channel,
                    sender_id,
                    preview,
                )
            return None

        if delivered_files and outcome.no_output:
            # The files reached the reader with the call that delivered them, and the model gave no
            # words of its own: there is nothing left to send. The fallback text is the record's, not
            # a reply, and a turn the message tool answered returns None on the same terms.
            logger.info("Turn for {}:{} delivered files and gave no reply of its own", channel, sender_id)
            return None

        if silent and delivered_files:
            # The files reached the reader with the call that delivered them, so the turn answered,
            # by the verdict filed above: not a Silent Turn, and the marker is no reply to send.
            logger.info("Turn for {}:{} delivered files and ended on the silence marker", channel, sender_id)
            return None

        if silent:
            logger.info("Silent turn for {}:{}: the model chose not to reply", channel, sender_id)
            if silence is not None:
                silence["silent"] = True
                if outcome.shown:
                    silence["shown"] = outcome.shown
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", channel, sender_id, preview)
        return (final_content, [])

    def _park_task_board(self, key: str, turn_added: list[dict], turn_at: str, turn_id: str | None) -> None:
        """Park what a turn that failed or was stopped dispatched (``park_dispatches``): the
        board makes no call after it, and the next update places the work."""
        from raven.agent.task_board.dispatches import turn_dispatches
        from raven.agent.task_board.runner import park_dispatches

        board = self.task_board
        dispatches = turn_dispatches(turn_added)
        if board is None or not dispatches:
            return
        task = asyncio.create_task(
            park_dispatches(
                board,
                session_key=key,
                dispatches=dispatches,
                turn_at=turn_at,
                turn_id=turn_id,
                generation=board.generation(key),
            )
        )
        self._consolidation_tasks.add(task)
        task.add_done_callback(self._consolidation_tasks.discard)

    def _schedule_task_board(
        self,
        key: str,
        window: list[dict],
        outcome: LoopOutcome | None,
        turn_added: list[dict],
        turn_at: str,
        turn_id: str | None,
        fallback_models: list[str] | None,
        *,
        delegated: dict[str, Any] | None,
        moved_on: list[str],
        silent: bool,
    ) -> None:
        """Start the host's post-turn task board call for the turn that just ended.

        It sends the tools array, model, effort and tool choice the turn's last call
        carried, through the same strategies, so it runs at the main model's settings
        and shares that call's cached prefix up to the reply; only the instruction
        after it is new. Nothing forces the tool: a forced choice voids Anthropic's
        cached messages and several providers refuse one outright. The dispatches
        come from what the turn added -- a spawn still in the history belongs to the
        turn that made it. ``delegated`` (the request's mark of the result that
        opened the turn) and ``moved_on`` (the groups the notes it was shown named)
        say what the turn is about, when anything does.
        """
        from raven.agent.task_board.dispatches import delegated_handle, turn_dispatches
        from raven.agent.task_board.runner import run_post_turn, should_run

        board = self.task_board
        if board is None or outcome is None:
            return
        if outcome.status != "completed":
            # A turn whose provider failed after a tool had answered returns rather than
            # raises, and so does one cut short; neither makes the call, and what it
            # dispatched waits in unassigned as the exits that raise park it.
            self._park_task_board(key, turn_added, turn_at, turn_id)
            return
        dispatches = turn_dispatches(turn_added)
        if outcome.last_tools is None or not window:
            return
        if not should_run(outcome.status, dispatches, silent=silent):
            return
        tools, model = outcome.last_tools, outcome.last_model or self.model
        # The turn's own effort, None included, as the turn sends it: a key left out
        # lets the provider fill it from the copy frozen at its construction.
        effort = outcome.last_effort
        # A turn whose array lacked the tool -- withheld by a deploy's disabledTools --
        # leaves the model nothing to answer the instruction with.
        if not any((t.get("function") or {}).get("name") == "task_board" for t in tools):
            return

        async def call(rows: list[dict]) -> LLMResponse:
            with _llm_purpose("task_board"), usage_context.bind(key):
                call_messages, call_tools, call_model = await self.strategies.before_llm_call(rows, tools, model)
                return await self.provider.chat_with_retry(
                    messages=merge_mid_turn(call_messages),
                    tools=call_tools,
                    model=call_model,
                    fallback_models=fallback_models,
                    reasoning_effort=effort,
                )

        task = asyncio.create_task(
            run_post_turn(
                board,
                session_key=key,
                call=call,
                window=list(window),
                dispatches=dispatches,
                turn_at=turn_at,
                turn_id=turn_id,
                now_ms=board.now_ms,
                generation=board.generation(key),
                delegated=[handle] if (handle := delegated_handle(delegated)) else [],
                moved_on=moved_on,
            )
        )
        self._consolidation_tasks.add(task)
        task.add_done_callback(self._consolidation_tasks.discard)

    def _save_broken_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        received_at: str | None,
        streamed: dict[str, str],
        *,
        status: str,
        reason: str | None = None,
        full_detail: str | None = None,
        inbound_original: str | None = None,
        context_used: int | None = None,
    ) -> None:
        """Persist what a cancelled or failed turn got as far as producing.

        The tail of the turn plus two kinds of repair, then one closing marker:

        - an assistant message whose tool calls never got results gains a
          synthetic ``[interrupted]`` result per open call, because a stored
          history with an unanswered tool call is one strict providers reject
          on the next turn;
        - the text that streamed after the last committed message is saved as
          its own assistant message -- it was on the reader's screen, and the
          loop only commits a message once the provider call returns;
        - the marker entry carries ``turn_ended`` so a client can say WHY the
          transcript stops there, and readable text so the model knows the same.
          The two are worded for their own reader: ``turn_ended.reason`` keeps
          the provider's account for the person diagnosing it, while the text
          the model reads back names the category only (see
          ``_marker_failure``).
          ``turn_ended.full_detail`` is the long account beside the bounded
          ``reason``, filed only when there is more to it.
          ``context_used`` is the newest window the turn measured, filed on the
          marker because it is the newest row: a resume reads the window from
          there, and a turn whose calls left no row of their own has no other.

        ``received_at`` and ``inbound_original`` describe the turn's question,
        which ``_process_message`` files before the attempt starts and hands
        this one a ``skip`` that begins after it: both arrive as None from
        there. They stay on the signature because what this rescues is the tail
        of an arbitrary message list, and a caller whose list still opens on an
        unfiled inbound needs them the way the healthy path does -- a hook's
        ``modified_content`` rewrite shapes only what the model saw, and a
        cancelled turn must not be the door through which the rewritten
        envelope enters the persisted history.

        Never raises: this runs on the way out of a dying turn, and a rescue
        that throws replaces one loss with another.
        """
        try:
            tail: list[dict] = [dict(m) for m in messages[skip:]]
            open_calls: dict[str, str] = {}
            for m in tail:
                if m.get("role") == "assistant":
                    for tc in m.get("tool_calls") or []:
                        cid = str(getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "") or "")
                        if cid:
                            name = getattr(getattr(tc, "function", None), "name", None) or (
                                (tc.get("function") or {}).get("name") if isinstance(tc, dict) else None
                            )
                            open_calls[cid] = str(name or "tool")
                elif m.get("role") == "tool":
                    open_calls.pop(str(m.get("tool_call_id") or ""), None)
            for cid, name in open_calls.items():
                tail.append(
                    {
                        "role": "tool",
                        "tool_call_id": cid,
                        "name": name,
                        "content": "[interrupted] this call never returned",
                    }
                )
            text = (streamed.get("text") or "").strip()
            # A recovery row does not count as committed: it is scaffolding that is
            # dropped below, and the partial of a cut answer is one -- a continuation
            # that died before saying anything leaves the reader's words as exactly
            # that row's text.
            if text and not any(
                m.get("role") == "assistant"
                and not m.get("_recovery_synthetic")
                and str(m.get("content") or "").strip() == text
                for m in tail
            ):
                partial: dict[str, Any] = {"role": "assistant", "content": streamed["text"]}
                if (thought := (streamed.get("thought") or "").strip()) and not any(
                    str(m.get("reasoning_content") or "").strip() == thought for m in tail
                ):
                    partial["reasoning_content"] = streamed["thought"]
                tail.append(partial)
            word = "cancelled by the user" if status == "cancelled" else f"failed: {_marker_failure(reason)}"
            marker: dict[str, Any] = {
                "role": "assistant",
                "content": f"(turn {word})",
                "turn_ended": {
                    "status": status,
                    **({"reason": reason} if reason else {}),
                    **({"full_detail": full_detail} if full_detail else {}),
                },
            }
            if context_used:
                marker[_CONTEXT_USED_KEY] = context_used
            tail.append(marker)
            self._save_turn(session, tail, 0, received_at=received_at, inbound_original=inbound_original)
            self.sessions.save(session)
        except Exception:  # noqa: BLE001 - see docstring
            logger.opt(exception=True).warning("could not persist the broken turn for {}", session.key)

    def _record_inbound(
        self,
        session: Session,
        content: str,
        origin: "Origin | None",
        timestamp: str,
        *,
        trace_id: str | None = None,
    ) -> None:
        """Persist the inbound entry of a turn that answers before ``_save_turn``.

        The personalization flow can reply with a clarifying question and return,
        so the envelope the assembler would have marked is never built and the
        turn's user entry reaches disk from here instead. Both paths that do it
        go through this one, because two copies of the same write is how one of
        them came to be marked and the other not.

        Writes ``origin`` rather than ``_origin``: the underscore exists so a
        mark stays out of the live provider payload until ``_save_turn`` renames
        it, and nothing here is going to a provider.
        """
        entry: dict[str, Any] = {"role": "user", "content": content, "timestamp": timestamp}
        if mark := _runtime_origin(origin):
            entry["origin"] = mark
        if trace_id:
            entry["trace_id"] = trace_id
        session.record(entry)

    def _maybe_annotate_tail(self, session: Session, prev_len: int) -> None:
        """Hand the unannotated tail to the consolidator once enough user turns piled up.

        Detached from the turn, under the consolidator's per-session lock so it
        cannot interleave with the ``/new`` path. The tail is sliced inside the
        lock: a task that waited re-reads the cursor the holder wrote and finds
        nothing left to do. Its cursor is ``annotated_through`` in the session's
        metadata, patched rather than saved so a task that lands mid-turn cannot
        drop keys the turn wrote in between. A session seen for the first time is
        seeded at ``prev_len``, the message count before the turn that just
        ended: history is never back-filled, so an upgraded install or a fork
        does not hand its past to one call, while a new session counts from its
        first turn. Truncation (``clear``, ``undo_last_turn``) moves the cursor
        itself; the clamp here only covers a file edited by hand.
        """
        every = self.memory_config.annotate_every_turns
        if every <= 0:
            return
        # Read here, not in the task: this is the end of a turn, so it is a turn
        # boundary. A task that waited for the lock would otherwise slice to a
        # length the next turn has already grown, ending the tail on a user
        # message whose answer has not arrived.
        end = len(session.messages)
        # Both read here, for the same reason: a task can sit queued behind
        # another annotation on the same session, and a truncation that lands
        # while it waits has already moved the counter by the time it takes
        # the lock. Read together, they describe one moment -- the end of this
        # turn -- and disagree with the session if anything cut it since.
        before = session.truncations

        async def _run() -> None:
            try:
                async with self.memory_consolidator.get_lock(session.key):
                    stored = session.metadata.get("annotated_through")
                    if stored is None:
                        stored = prev_len
                        self._set_annotated_through(session, min(prev_len, len(session.messages)))
                    start = min(int(stored), end, len(session.messages))
                    if start >= end or session.truncations != before:
                        return
                    tail = _turns_a_person_opened(session.messages[start:end])
                    if sum(1 for m in tail if m.get("role") == "user") < every:
                        return
                    # Half the window, the share the token path consolidated
                    # down to. What is left over rides the next qualifying
                    # turn, so the cursor keeps moving and a tail refused for
                    # its size is not retried larger.
                    capped, oversized = _first_turns_within(tail, self.memory_consolidator.context_window_tokens // 2)
                    if len(capped) != len(tail):
                        # Located by identity, not equality: two messages in
                        # one slice can be equal down to the timestamp, and a
                        # search by value would answer with the earlier one,
                        # leaving the cursor short of what was sent.
                        sent = session.messages[start:end]
                        last = next(i for i, m in enumerate(sent) if m is capped[-1]) + 1
                        end_for_this_pass = start + last
                        logger.info(
                            "episodic annotate for {}: tail trimmed to {} of {} messages for this pass",
                            session.key,
                            len(capped),
                            len(tail),
                        )
                    else:
                        end_for_this_pass = end
                    tail = capped
                    wrote = await self.memory_consolidator.consolidate_messages(tail)
                    if not wrote and not oversized:
                        logger.warning("episodic annotate for {} declined; cursor kept at {}", session.key, start)
                        return
                    # The reader may undo or clear while the annotator runs,
                    # and both leave the indices this task sliced against
                    # pointing at other messages. The cursor's value cannot
                    # carry that: a cut below it leaves it alone, and turns
                    # arriving after the cut take the length back past where it
                    # was. The counter moves on every truncation, so it can.
                    if session.truncations != before:
                        logger.info(
                            "episodic annotate for {}: truncated while it ran; cursor left at {}",
                            session.key,
                            session.metadata.get("annotated_through"),
                        )
                        return
                    self._set_annotated_through(session, min(end_for_this_pass, len(session.messages)))
                    if not wrote:
                        # The one refusal the cursor has to absorb: this turn
                        # is larger than the whole budget, so every later pass
                        # would build the same tail and send the same prompt
                        # again. Keeping the cursor here repeats the call
                        # rather than retrying it.
                        logger.warning(
                            "episodic annotate for {} declined a turn larger than the bound; cursor moved past it to {}",
                            session.key,
                            end_for_this_pass,
                        )
                        return
                    await self.memory_consolidator.maybe_refresh_hot_tags()
            except Exception:
                logger.exception("episodic annotate for {} failed", session.key)

        task = asyncio.create_task(_run())
        self._consolidation_tasks.add(task)
        task.add_done_callback(self._consolidation_tasks.discard)

    def _set_annotated_through(self, session: Session, n: int) -> None:
        session.metadata["annotated_through"] = n
        if not self.sessions.append_metadata_patch(session.key, {"annotated_through": n}):
            logger.debug("episodic cursor for {} not persisted: no session file yet", session.key)

    def _save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        *,
        inbound_original: str | None = None,
        received_at: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results.

        ``received_at`` is the wall clock at which the turn's inbound message
        arrived, and it is the opening write that passes one: the turn's other
        writes run after work that took time, so stamping every entry "now"
        would give the user message and the final answer the same timestamp --
        and a restored transcript reads the gap between those two as the turn's
        duration. ``trace_id`` travels with it and marks the same entry, so a
        selection of turns can find each turn's messages in the record.
        """
        # Deferred: ``raven.context_engine`` imports back out of this tier.
        from raven.context_engine.scratch import TURN_SCRATCH_KEY, cut_turn_scratch

        # Both exits that persist a turn come through here, so a question noted
        # on the turn is filed once whichever exit runs. The early inbound write
        # also comes through, before any question, and files nothing. A turn the
        # session list and the ACP replay take back whole files none: a notice
        # after its marker would outlive the take-back as an orphan.
        if not _taken_back_whole(messages, skip):
            _file_unanswered(messages)
        first_user_pending = received_at is not None
        # The turn's first user entry is the inbound message; hooks may have
        # rewritten what the model saw (a memo prepended, a reminder appended),
        # and persisting that view would compound it into every later window.
        # Same rule as the runtime-context strip below: history keeps the words
        # the user sent. Block-shaped inbounds pass through as built.
        inbound_rewrite_pending = inbound_original is not None
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if first_user_pending and role == "user":
                entry.setdefault("timestamp", received_at)
                if trace_id:
                    entry["trace_id"] = trace_id
                first_user_pending = False
            entry.pop(TURN_LOG_KEY, None)
            if (scratch := entry.pop(TURN_SCRATCH_KEY, None)) and role == "user":
                # The turn scratch (recalled memory, routed skills) is rebuilt
                # from the query every turn. Recording it would turn the log
                # from append-only into rewritten -- next turn's request would
                # carry this turn's recall in its history -- and the recalled
                # text would pile up in history forever. Cut before the rewrites
                # below, which compare against the exact text that was appended.
                content = cut_turn_scratch(content, scratch)
                if content is None:
                    continue
                entry["content"] = content
            if entry.get("_recovery_synthetic"):
                continue  # #1a synthetic recovery nudge — never persist scaffolding
            if entry.get(ATTACHED_IMAGE_KEY):
                # Already filtered upstream; kept because this is the last gate
                # before a write that cannot be undone, unlike the code above it.
                continue
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if turn_origin := entry.pop(_ORIGIN_KEY, None):
                entry["origin"] = turn_origin
            if delegated := entry.pop(_DELEGATED_KEY, None):
                # Same rename as the origin below, for the same reason: without
                # it a reload draws a delegated result as a question the user
                # asked, fence and all. The origin says WHO opened the turn;
                # the delegated identity says WHICH run came back, so the two
                # coexist.
                entry["delegated"] = delegated
            if entry.pop(_MID_TURN_USER_KEY, None):
                # Same rename as the origin above: the underscore kept it out of
                # the provider payload, the plain name is what session.resume
                # puts on the wire so a reload draws this message inside the
                # turn it was merged into rather than as one of its own.
                entry["mid_turn"] = True
            if notice := entry.pop(_NOTICE_KEY, None):
                # Same rename as the diff below, for the same reason. Without
                # it a reload draws this runtime prose as the model's answer,
                # so the live view and the restored one disagree about who
                # spoke.
                entry["notice"] = notice
            if entry.pop(_SILENT_KEY, None):
                # Same rename as the notice above, for the same reason: without
                # it a reload cannot tell a marker the model wrote from an answer.
                entry["silent"] = True
            if entry.pop(_REPLIED_VIA_TOOL_KEY, None):
                # Same rename again: a stored reader asks whether the message tool
                # answered the turn, and only the live turn could tell.
                entry["replied_via_tool"] = True
            if tool_diff := entry.pop("_diff", None):
                # Renamed for storage: the private spelling kept it out of the
                # live provider payload, the plain one is what session.resume
                # maps onto the wire so a reloaded page can renumber the change.
                entry["diff"] = tool_diff
            if tool_removed := entry.pop("_file_removed", None):
                # Renamed for storage for the same reason as the diff above: the
                # plain name is what session.resume puts on the wire, so a
                # reloaded page draws the deletion the live view drew.
                entry["file_removed"] = tool_removed
            if tool_written := entry.pop("_file_written", None):
                # Renamed for storage for the reason the removals above are: what
                # a command wrote has no other record, so without this a reloaded
                # page shows a turn whose commands changed nothing.
                entry["file_written"] = tool_written
            if tool_metadata := entry.pop(_TOOL_METADATA_KEY, None):
                entry["metadata"] = tool_metadata
            # Provenance of pictures that lived for this turn only; nothing to file.
            entry.pop(IMAGE_SOURCES_KEY, None)
            # A withdrawn picture's note is filed without this turn's reasons: on
            # the tool-result transport the message outlives the turn, and a
            # resumed session would otherwise replay "this turn's pictures outgrew
            # their budget" as current.
            if isinstance(content, list):
                content = entry["content"] = [
                    {**part, "text": filed_image_note(part["text"])}
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                    else part
                    for part in content
                ]
            for private_key, stored_key in (
                (_REASONING_MS_KEY, "reasoning_ms"),
                (_TOOL_DURATION_MS_KEY, "duration_ms"),
                (_CONTEXT_USED_KEY, "context_used"),
            ):
                # Renamed for the same reason as the diff above. A duration or a
                # window reading is a property of the part it measures, so it is
                # written down with it: a clock started when a page loads, or a
                # count of the stored log, can only ever guess.
                if (took := entry.pop(private_key, None)) is not None:
                    entry[stored_key] = took
            if role == "tool" and isinstance(content, list):
                # A multimodal tool result. Images must never reach the JSONL:
                # a single one adds megabytes that are then replayed on every
                # resume and re-fed to the model, and unlike a code bug that is
                # not revertible once written. The char cap below cannot catch
                # it either — it guards `str` content only.
                content = _strip_inline_images(content)
                entry["content"] = content
            cap = self._TOOL_RESULT_MAX_CHARS
            if role == "tool" and isinstance(content, str) and cap and len(content) > cap:
                # The marker is what retrieval reads to say a span was cut on the
                # way to disk rather than pass a prefix off as the whole.
                entry["content"] = content[:cap] + TOOL_RESULT_TRUNCATION_MARKER
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if inbound_rewrite_pending:
                    inbound_rewrite_pending = False
                    if isinstance(entry.get("content"), str) and entry["content"] != inbound_original:
                        entry["content"] = inbound_original
                if isinstance(content, list):
                    filtered = [
                        c
                        for c in _strip_inline_images(content)
                        if not (
                            isinstance(c, dict)
                            and c.get("type") == "text"
                            and isinstance(c.get("text"), str)
                            and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
                        )
                    ]
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", self._now_fn().isoformat())
            session.record(entry)
        session.updated_at = self._now_fn()

    async def _run_turn(
        self,
        req: TurnRequest,
        emit: Emit,
        drain: Drain,
        *,
        stream: bool = True,
        usage_sink: dict[str, Any] | None = None,
        text_sink: dict[str, Any] | None = None,
        reply_awaited: bool = False,
    ) -> "TurnOutcome":
        """Spine-native turn entry: consume a TurnRequest, fan the agent's output
        onto the single ``emit``, return a TurnOutcome. Collapses the legacy
        output paths (a str return + the five callbacks) onto one boundary.

        Named ``run_turn`` rather than ``run``: ``run`` is the runtime keep-alive
        (executor / debug server / MCP up, then idle). A spine runner calls the
        public ``run_turn`` to satisfy the TurnRunner protocol.

        ``stream`` is the reply-assembly switch: a streaming outlet (TUI) wires
        it True so the reply goes out as StreamDelta and dissolves with no
        trailing Text; a non-streaming outlet (REPL) wires it False so the reply
        is one Text. It gates both LLM callbacks (the loop streams when either is
        wired) and the message-tool routing, so the whole reply travels one way.

        Exceptions propagate so the lane turns them into TurnFailed — run_turn
        does not catch sandbox-init to return an error string (the legacy direct
        path did; the spine surfaces it as a TurnFailed event instead).

        ``usage_sink`` lets a caller observe the turn's full token accounting
        (cost / context, richer than the three-field LoopOutcome.usage): pass a
        dict and it is filled. The TUI passes one to attach the rich usage to
        message.complete; the REPL omits it and uses LoopOutcome.usage.

        ``text_sink`` is its sibling for the reply text: pass a dict and the
        final reply lands in text_sink["text"] (the reply still goes out via
        emit — this is an observation copy, not a second delivery). cron passes
        one so its system event can tell the heartbeat what the run produced.
        Both sinks are transitional, to retire together when taps lands (a
        read-only observer of the turn's output).

        ``drain`` pulls user messages injected mid-turn (BusyPolicy.INJECT); it
        is threaded into the agent loop and consumed at the top of each iteration.

        ``reply_awaited`` is for a caller that waits on the reply's text, as an
        inbound A2A call's peer does: the turn takes no part in the silence
        protocol, so a Silence Marker is delivered as the text it is, whatever
        the request's origin.
        """
        from raven.agent.subagent.direct_chat import DirectChatError
        from raven.proactive_engine.schedulers.cron.tool import CronTool
        from raven.spine.events import (
            EpisodeStart,
            MediaOut,
            Notice,
            NoticeKind,
            Reasoning,
            StreamDelta,
            Text,
            ToolEvent,
            ToolPhase,
            Usage,
        )
        from raven.spine.message import Media
        from raven.spine.runner import TurnOutcome

        cid = req.conversation or f"{req.source.channel}:{req.source.chat_id}"

        # Direct sub-agent turn (direct_target -- see TurnRequest). Deliberately
        # asymmetric with the deliver_text branch below: that one persists to the
        # session before emitting, this one persists nothing. The whole purpose
        # of a direct chat is that the main agent's transcript does not carry it;
        # the turn's evidence is its record directory, and what the main agent
        # eventually learns is the handoff block, not these messages.
        if req.direct_target is not None:
            agent, handle = req.direct_target
            # The lane is this instance's, so that a direct chat runs concurrently
            # with the main agent's turn and with every other instance's. Records,
            # the instance registry and the handoff are the *session's* though --
            # keyed by the lane they would scatter one instance's history into a
            # directory of its own and land the handoff on a conversation nobody
            # reads. The request retains that original session independently.
            from raven.spine import session_key_for

            session_key = session_key_for(req)
            streamed_direct = False

            async def on_direct_delta(text: str) -> None:
                # Text only, never Reasoning: the wire tags an instance on four
                # event types and thinking.delta is not one of them, so a direct
                # chat's reasoning would be rendered into the main transcript.
                nonlocal streamed_direct
                if not text:
                    return
                streamed_direct = True
                await emit(StreamDelta(delta=text))

            try:
                reply, meta = await self.subagents.chat(
                    session_key=session_key,
                    agent=agent,
                    handle=handle,
                    text=req.text,
                    media=req.media,
                    # The session's working directory, the same one this turn's
                    # own tools would get and the same one `spawn` captures. The
                    # binding is not set here -- `workdir.bind` wraps the main
                    # turn body further down, which this branch returns before
                    # reaching -- so it is resolved rather than read. Omitting it
                    # fell back to agent home (`~/.raven/workspace`), which is
                    # raven's memory and skills rather than the work: a direct
                    # chat about the checkout the user is sitting in ran `git
                    # status` against raven's own home and answered about that.
                    workspace=self.session_workdir(session_key),
                    # A non-streaming outlet (the REPL) gets no deltas to
                    # assemble, exactly as it gets none from a normal turn.
                    on_delta=on_direct_delta if stream else None,
                )
            except DirectChatError as exc:
                self._direct_handoff.record(session_key, exc.meta)
                raise
            self._direct_handoff.record(session_key, meta)
            # Same rule as the main path below: what streamed is
            # already on screen, so a closing Text would render the reply twice.
            # An instance whose transport cannot stream never sets the flag and
            # is delivered whole, which is what every direct chat did before.
            if not streamed_direct:
                await emit(Text(content=reply))
            return TurnOutcome(
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                explicit_reply=True,
            )

        # Verbatim delivery (deliver_text — see TurnRequest). Persist before
        # emit, mirroring the normal turn's save-then-reply order, so a save
        # failure never leaves the user a delivered message no turn recorded.
        # The after-turn work a normal turn does in _process_message is reduced
        # to what a no-model delivery needs: backend.store indexes the message;
        # after_turn is a no-op without a turn_id; consolidation is the curator's
        # job on its next assemble.
        if req.deliver_text is not None:
            session = self.sessions.get_or_create(cid)
            msg = {"role": "assistant", "content": req.deliver_text}
            prev_len = len(session.messages)
            self._save_turn(session, [msg], 0)
            added = session.messages[prev_len:]
            self.sessions.save(session)
            self._dispatch_backend_store(cid, added)
            await emit(Text(content=req.deliver_text))
            return TurnOutcome(
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                explicit_reply=True,
            )

        # A direct chat is invisible to the main agent by design, so this is
        # where it finds out one happened: pointers to what was asked and
        # answered, never the text. Take-and-clear, so a segment is reported
        # once. Nothing pending returns immediately -- this runs on every turn.
        handoff = self._direct_handoff.take(cid)
        if handoff is not None:
            req = replace(req, text=f"{handoff}\n\n{req.text}")

        streamed = False
        hook_sink: dict[str, str] = {}
        silence: dict[str, bool | str] = {}

        async def on_token(text: str) -> None:
            nonlocal streamed
            if not text:
                return
            streamed = True
            await emit(StreamDelta(delta=text))

        async def on_reasoning(text: str) -> None:
            if text:
                await emit(Reasoning(content=text))

        async def on_episode(index: int) -> None:
            await emit(EpisodeStart(index=index))

        async def on_tool(phase: str, info: dict[str, Any]) -> None:
            if phase == "start":
                await emit(
                    ToolEvent(
                        phase=ToolPhase.START,
                        tool_call_id=info["tool_call_id"],
                        name=info["name"],
                        arguments=info["arguments"],
                        blocking=bool(info.get("blocking")),
                        display=info.get("display"),
                    )
                )
            else:
                await emit(
                    ToolEvent(
                        phase=ToolPhase.COMPLETE,
                        tool_call_id=info["tool_call_id"],
                        result_preview=info["result_preview"],
                        truncated=info["truncated"],
                        ok=bool(info.get("ok", True)),
                        metadata=info.get("metadata"),
                        diff=info.get("diff"),
                        file_change=info.get("file_change"),
                        file_removed=info.get("file_removed"),
                        file_written=info.get("file_written"),
                    )
                )

        async def on_progress(text: str, tool_hint: bool = False) -> None:
            # Keep the progress/tool-hint distinction so an outlet can gate each on
            # its own config flag (send_progress vs send_tool_hints), as the bus
            # path did — tool-hint text rides NoticeKind.TOOL_HINT, progress rides
            # PROGRESS. Outlets that don't render either eat both kinds anyway.
            if text:
                await emit(
                    Notice(
                        kind=NoticeKind.TOOL_HINT if tool_hint else NoticeKind.PROGRESS,
                        detail=text,
                    )
                )

        async def on_notice(kind: NoticeKind, detail: str) -> None:
            await emit(Notice(kind=kind, detail=detail or None))

        async def _emit_media(paths: list[str]) -> None:
            await emit(
                MediaOut(media=tuple(Media(path=p, mime="application/octet-stream", kind="file") for p in paths))
            )

        # Route the message tool's reply through the token stream so a
        # tool-driven reply streams like the main response; _process_message
        # then returns None, so the boundary below emits nothing for it. The
        # callback is turn-local (a ContextVar in MessageTool), so a concurrent
        # turn cannot clobber this turn's routing — no save/restore needed.
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):

            async def _route_to_stream(content: str, media: list[str]) -> None:
                # A message-tool reply can attach media; emit it independently so
                # it is not dropped (_process_message returns None for a tool reply
                # so the boundary below never sees it). The content follows the same
                # stream switch as the main reply: StreamDelta when streaming, one
                # Text otherwise — else a non-streaming outlet would eat the delta.
                if media:
                    await _emit_media(media)
                if text_sink is not None and content:
                    text_sink["text"] = content
                if stream:
                    await on_token(content)
                elif content:
                    await emit(Text(content=content))

            message_tool.set_send_callback(_route_to_stream)

        # A CRON turn must not let the agent schedule new cron jobs mid-run. The
        # CronTool guards via a ContextVar; set it here, in the lane task that runs
        # the turn, so it propagates to the tool — the cron callback sets it in a
        # different task that never reaches this one.
        cron_tool = self.tools.get("cron")
        cron_token = None
        if req.origin is Origin.CRON and isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)

        if usage_sink is None:
            usage_sink = {}
        try:
            # Resolve inside the try so a bad persisted override (deleted
            # directory, permission change) still releases the cron token
            # below via the outer finally, instead of stranding it.
            try:
                turn_workdir = self.session_workdir(cid)
            except Exception as exc:
                raise RuntimeError(
                    "Session working directory is invalid; clear this session's working "
                    "directory override from the web UI to recover."
                ) from exc
            # Bind the session's working directory around the whole turn body (not
            # just the _set_tool_context call inside _process_message) so every
            # path-aware tool sees it, including on the exception and cancellation
            # paths below -- workdir.bind's finally always resets the ContextVar.
            from raven.token_wise import usage_context

            with (
                workdir.bind(turn_workdir),
                usage_context.bind(cid, self.sessions.get_or_create(cid).metadata.get("usage_owner", {})),
            ):
                try:
                    await self._start_executor()
                    # Fire and forget: a turn must not wait on a handshake, and
                    # every host that serves turns without run() reaches MCP
                    # through here (the TUI has no other path at all, and this is
                    # also the reconnect after close_mcp and the retry after a
                    # prewarm that failed). Idempotent, so an in-flight connect is
                    # not restarted. Once connected, the reconcile beside it picks
                    # up an out-of-band edit of tools.mcpServers the same way --
                    # the turn boundary is the one place every host passes.
                    self.prewarm_mcp()
                    self.reconcile_mcp_from_live()
                    out = await self._process_message(
                        req,
                        session_key=cid,
                        on_progress=on_progress,
                        on_token_delta=on_token if stream else None,
                        on_reasoning_delta=on_reasoning if stream else None,
                        on_tool_event=on_tool,
                        on_episode_start=on_episode if stream else None,
                        on_notice=on_notice,
                        usage_sink=usage_sink,
                        origin=req.origin,
                        drain=drain,
                        hook_sink=hook_sink,
                        silence=silence,
                        reply_awaited=reply_awaited,
                    )
                except AnswerlessTurnError:
                    # A turn the loop gave up on is not a crash: the executor and
                    # the servers it holds are fine, and the next turn needs them.
                    raise
                except Exception:
                    # Before the executor goes: the prewarm this turn started is
                    # still running, and a stdio handshake inside it is spawned
                    # into that executor. Closing under it lands the server in
                    # `error`, which no reload retries.
                    await self.reap_mcp_prewarm()
                    await self.close_executor()
                    raise
        finally:
            if cron_token is not None and isinstance(cron_tool, CronTool):
                cron_tool.reset_cron_context(cron_token)

        # Single return->emit boundary. MediaOut is independent of the
        # stream and precedes Text (the current order is media-first).
        if out is not None:
            reply_content, reply_media = out
            if reply_media:
                await _emit_media(reply_media)
            if not streamed and reply_content:
                await emit(Text(content=reply_content))
            elif streamed and hook_sink.get("appended"):
                # The reply left as deltas before the after_send chain ran, so what a
                # hook appended -- a deck engine's "Deck: / Preview: / MEDIA:" lines,
                # a nudge -- reached no streaming surface: not the web page, not the
                # TUI, not an ACP client. Sent as one more delta, ahead of
                # message.complete, so the reply the client holds is the reply.
                await emit(StreamDelta(delta=hook_sink["appended"]))
            if text_sink is not None and reply_content:
                text_sink["text"] = reply_content
        silent = silence.get("silent", False) is True
        if silent:
            # Named so a client that drew the reply's think block takes back that and no more.
            shown = silence.get("shown")
            await emit(Notice(kind=NoticeKind.SILENT_TURN, detail=shown if isinstance(shown, str) and shown else None))

        usage = Usage(
            prompt_tokens=int(usage_sink.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_sink.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_sink.get("total_tokens", 0) or 0),
        )
        # A message-tool reply returns None from _process_message but did reply,
        # so it counts as an explicit reply too, and so do files a tool delivered.
        sent_by_tool = isinstance(message_tool, MessageTool) and message_tool.sent_in_turn
        replied_via_tool = sent_by_tool or silence.get("delivered_files") is True
        return TurnOutcome(usage=usage, explicit_reply=out is not None or replied_via_tool, silent=silent)

```

## raven/agent/loop/wiring.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/loop/wiring.py

SHA-256: `fac798f54facc5f93053d35eb9976e1288d8604a1a62bbcb6b8040cfdf553dc7`

```
"""Construction-time wiring: providers, bindings, tool registration, playbooks,
workdir and sinks.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from raven.a2a_client.tool import A2aTool
from raven.acp_client.asker import held_question
from raven.agent import knowledge_scope
from raven.agent.loop._shared import (
    SEARCH_PROVIDERS,
    Any,
    AskUserTool,
    Callable,
    ConnectionAddTool,
    EditFileTool,
    ExecTool,
    FindTool,
    GrepTool,
    ImageGenerateTool,
    ImageSearchTool,
    ListDirTool,
    LLMProvider,
    MessageTool,
    ModelBinding,
    Path,
    ReadFileTool,
    SpawnTool,
    SpeechGenerateTool,
    VideoGenerateTool,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
    active_binding,
    image_search_vendor,
    logger,
    resolve_vendor_key,
    workdir,
)
from raven.agent.subagent import charter as charter_mod
from raven.agent.subagent.role import is_subagent_process
from raven.agent.tools.ask_user import DEFAULT_TIMEOUT_S
from raven.contracts.token_strategy import UsageSnapshot
from raven.providers import effort_levels

if TYPE_CHECKING:
    from collections.abc import Sequence

    from raven.agent.subagent.charter import Charter
    from raven.agent.subagent.delegate import DelegateTable
    from raven.agent.tools.deliverables import DeliverableStore
    from raven.config.raven import SkillForgeRouterConfig
    from raven.config.schema import PlaybookConfig
    from raven.providers.pool import ProviderPool
    from raven.skill_hub import SkillHubClient


#: What the iteration cap is held under for the length of a turn.
_CAP_KEY = "agents.defaults.maxToolIterations"
#: Same, for the effort every model call of the turn is sent at.
_EFFORT_KEY = "agents.defaults.reasoningEffort"
#: Same, for the switch both personalization gates of a turn read.
_PERSONALIZATION_KEY = "agents.defaults.enablePersonalization"
#: Both web vendors under one key: they come out of one section, and a turn
#: that searches and fetches must not straddle an edit between the two.
_WEB_VENDORS_KEY = "tools.web.providers"


class WiringMixin:
    """Construction-time wiring: providers, bindings, tool registration, playbooks,
    workdir and sinks."""

    def _report_reserved_disabled_tools(self) -> None:
        """Tell the operator about an off switch the loop cannot honour.

        All this function does now. Withholding a tool is decided per request by
        :meth:`_withheld_tool_names`, which is what makes a switch flipped now
        take effect on the next turn -- this used to *unregister* the tool, and a
        preference expressed by destroying its subject is one that cannot be
        reversed: nothing remembered what to put back.

        Two groups are exempt, and both are theirs by ownership rather than by
        policy. :meth:`_sync_mcp_meta_tools` registers the five MCP meta-tools
        while some connected server serves resources or prompts and withdraws them
        when none does, so it owns those names for the life of the loop.
        ``tool_search`` and ``tool_call`` are owned the same way by
        :meth:`_register_default_tools`, and ``tools.tool_search.enabled`` is the
        switch that speaks for them. An entry naming either group is a preference
        nothing can act on -- reported here once, ignored where the array is built.

        Run after :meth:`_register_default_tools` and after MCP connect, so an
        entry naming a tool from either group is resolvable by the time it is
        checked. Silent on misses: an eval config commonly carries an over-broad
        list that is a no-op in this build.

        Reads the same two sources as :meth:`_withheld_tool_names`, so the notice
        is about the entry the operator can actually see -- most of them are in
        the config file, which is no longer copied into ``_disabled_tools``.
        """
        from raven.agent.tools.tool_search import META_TOOL_NAMES, TOOL_SEARCH_NAME
        from raven.config.live import disabled_tool_names
        from raven.mcp.prompts import PROMPT_TOOL_NAMES
        from raven.mcp.resources import RESOURCE_TOOL_NAMES

        entries = set(disabled_tool_names(self._live_config)) | self._disabled_tools
        if not entries:
            return
        mcp_reserved = RESOURCE_TOOL_NAMES | PROMPT_TOOL_NAMES
        for entry in sorted(entries):
            if entry in self._disabled_tools_reserved_warned:
                continue
            resolved = self.tools.resolve_configured(entry)
            if any(name in mcp_reserved for name in resolved):
                self._disabled_tools_reserved_warned.add(entry)
                logger.warning(
                    "tools.disabled_tools names '{}', which raven registers and withdraws on its "
                    "own as MCP servers serving resources or prompts come and go. The entry has "
                    "no effect; remove it to keep the config honest.",
                    entry,
                )
            elif any(name in META_TOOL_NAMES for name in resolved):
                self._disabled_tools_reserved_warned.add(entry)
                # One remedy per name, because the two are not owned the same
                # way: tools.tool_search.enabled decides whether tool_search is
                # registered at all, while tool_call is registered whatever that
                # switch says -- it is the only route to a schema-hidden tool.
                # A shared "turn the fold off" line sent an operator who wrote
                # tool_call here to a setting that leaves it exactly where it was.
                remedy = (
                    "turn the fold off with tools.tool_search.enabled, which is what registers it"
                    if TOOL_SEARCH_NAME in resolved
                    else "this deploy has no switch for it: it is the only route to a tool whose "
                    "schema is not in the array, so it is registered whatever the fold is doing"
                )
                logger.warning(
                    "tools.disabled_tools names '{}', which raven owns for the life of the loop. "
                    "The entry has no effect; remove it, or {}.",
                    entry,
                    remedy,
                )

    def _withheld_tool_names(self) -> frozenset[str]:
        """Which tools are not on offer right now, read live.

        The registry asks this when it assembles a tool array. Reserved names are
        removed here rather than at the switch: ``_sync_mcp_meta_tools`` owns the
        five MCP meta-tools for the life of the loop (it registers them while some
        connected server serves resources or prompts and withdraws them when none
        does), so an entry naming one is a preference the loop cannot honour --
        reported above, ignored here.

        ``tool_search`` and ``tool_call`` are reserved for that reason and one
        more. Their absence from the array is how ``ToolSearchStrategy`` reads
        "this request has no search route", and it answers by shipping every
        schema instead -- so an off switch here would not slim the array, it would
        unfold it, and mid-turn at that, because this source is read once per
        assembly. The switch that speaks for the fold is
        ``tools.tool_search.enabled``.

        Constructor-supplied names are unioned in because an eval harness passes
        them directly rather than through a config file; a file-less run would
        otherwise lose its blacklist entirely. Nothing copies the config's own
        list in there -- see the note in ``__init__`` on why that made the switch
        one-way.
        """
        from raven.agent.tools.tool_search import META_TOOL_NAMES
        from raven.config.live import disabled_tool_names
        from raven.mcp.prompts import PROMPT_TOOL_NAMES
        from raven.mcp.resources import RESOURCE_TOOL_NAMES

        configured = set(disabled_tool_names(self._live_config)) | set(self._disabled_tools)
        withheld: set[str] = set()
        for entry in configured:
            withheld.update(self.tools.resolve_configured(entry))
        withheld.update(self._unconfigured_tool_names())
        # Nothing to search is not a tool to offer. Read from the turn's own
        # binding rather than from config, so attaching a base takes effect on
        # the next model call and detaching every base takes the tool away
        # again -- both directions, mid-conversation.
        if not knowledge_scope.selected():
            withheld.add("knowledge_search")
        return frozenset(withheld - (RESOURCE_TOOL_NAMES | PROMPT_TOOL_NAMES | META_TOOL_NAMES))

    def _unconfigured_tool_names(self) -> set[str]:
        """Registered tools whose config asks for nothing right now, read live.

        The old registration gate, moved to the withheld axis so it works in
        both directions while the process runs. The rules themselves are
        unchanged: a media tool counts as configured only when its own section
        names a model or a key -- an OpenRouter key set for chat alone must not
        surface tools that bill per call -- and web_search counts a key from
        the file or the environment, or a selected vendor that needs none.

        Gates the built-in INSTANCE, by identity: plugin tools register last
        precisely so one can shadow a built-in by name -- subclassing it or
        not -- and whatever replaced the entry carries its own credential
        story, which the built-in's config section says nothing about. An
        ``isinstance`` check read a plugin *subclass* as the built-in itself
        and withheld it on the built-in's empty section.
        """
        media = self.media_config
        gated = getattr(self, "_config_gated_tools", {})
        names: set[str] = set()
        for cls, kind, fallback in (
            (ImageGenerateTool, "image", media.image),
            (SpeechGenerateTool, "speech", media.speech),
            (VideoGenerateTool, "video", media.video),
        ):
            if self.tools.get(cls.name) is not gated.get(cls.name):
                continue
            cfg = self._live_media_config(kind, fallback)
            if not (cfg.api_key or cfg.model):
                names.add(cls.name)
        search = self.tools.get(WebSearchTool.name)
        # Asked of the tool, not restated here: it resolves the selected
        # vendor's key from the live reader below or from that vendor's own
        # environment variable. A predicate spelled out again here was reading
        # SERPER_API_KEY whatever the deployment had chosen, which withheld a
        # keyed Tavily search.
        if search is not None and search is gated.get(WebSearchTool.name) and not search.ready:
            names.add(WebSearchTool.name)
        pictures = self.tools.get(ImageSearchTool.name)
        if pictures is not None and pictures is gated.get(ImageSearchTool.name) and not pictures.api_key:
            names.add(ImageSearchTool.name)
        # A contributed tool that declared ``configured()`` at the door answers
        # for itself on this same lane (the paper is in
        # raven/contracts/plugin_surface.py): the built-ins above are judged by
        # identity because their section is the loop's to read, while a plugin's
        # credential story is its own. Read off the admitted ``ToolSpec``, never
        # off the live object, and asked per assembly, so a section added or
        # emptied in Settings surfaces or withdraws the tool on the next turn
        # without a restart. A declaration that raises is logged and read as
        # configured: offering a tool that will answer with its own error beats
        # losing the assembly.
        for name in self.tools.names():
            if self.tools.get(name) is gated.get(name):
                continue
            spec = self.tools.spec_of(name)
            if spec is None or spec.configured is None:
                continue
            try:
                offered = bool(spec.configured())
            except Exception as exc:
                logger.warning("tool {} could not say whether it is configured: {}", name, exc)
                continue
            if not offered:
                names.add(name)
        return names

    def _live_exec_extra_deny(self) -> list[str] | None:
        """The exec deny extras the file names now; None keeps the boot extras."""
        from raven.config.live import exec_extra_deny_patterns

        return exec_extra_deny_patterns(self._live_config)

    def _live_web_search_key(self) -> str:
        """The selected vendor's search key the file names now, else the boot value.

        The boot value is the lane eval harnesses pass a key through with no
        file behind it; a file with a section governs entirely, including an
        empty value, which is how a key gets revoked without a restart.

        Both key layouts are read live, canonical slot first, in the same order
        ``WebToolsConfig.vendor_key`` resolves them: reading only the
        pre-vendor leaf made the restart-free behaviour reachable exclusively
        by the layout this schema retired, so a key pasted into the slot the
        settings page writes -- Serper's included -- left the tool withheld
        until the next process. The leaf is Serper's alone, hence consulted
        only when Serper is the selection.
        """
        return self._live_vendor_key(self.web_search_provider)

    def _media_config_reader(self, kind: str, fallback) -> "Callable[[], Any]":
        def read():
            return self._live_media_config(kind, fallback)

        return read

    def _live_media_config(self, kind: str, fallback):
        """The media section the file names now; the boot config when it names none.

        The boot config is the lane eval harnesses pass a section through with
        no file behind it, and the last good answer while the file is mid-write.
        """
        from raven.config.live import media_tool_config, resolve_media_selection

        cfg = media_tool_config(self._live_config, kind)
        return resolve_media_selection(fallback, kind) if cfg is None else cfg

    def _routed_target_ready(self, target: str, needs: "Sequence[str]") -> bool:
        """Whether ``target``'s own lane can spend what its route declared it spends.

        Answers for the routed product, not for this loop. A lane is configured
        from its product folder first and inherits from the host only where the
        folder is silent, so a host credential is the wrong thing to measure in
        both directions: a lane equipped by its own ``.env`` would be refused on
        a host that holds nothing, and a host equipped for something the lane
        cannot use would open a route into a pipeline that has no key for the
        surface it actually calls.

        ``needs`` is the route's own declaration (:data:`ROUTE_REQUIREMENTS`);
        an empty one never reaches here, because a route that declares nothing
        is not probed at all. A requirement this raven does not have a question
        for is warned about and treated as met -- a manifest written for a later
        version must not silently lose its route on an older one.

        Re-read per dispatch, so a key pasted into Settings or into the
        product's ``.env`` opens the route without a restart.

        The Jina reader key is deliberately not counted: ``web_fetch`` is
        registered with or without one (see ``WebFetchConfig``), so it says
        something about extraction quality and nothing about whether a lane can
        search.
        """
        for need in needs:
            if need == "image_generation":
                if not self._lane_generates_images(target):
                    return False
            elif need == "image_search":
                if not self._lane_searches_images(target):
                    return False
            else:
                logger.warning(
                    "A route to {!r} declares the requirement {!r}, which this raven cannot measure; "
                    "treating it as met",
                    target,
                    need,
                )
        return True

    def _lane_generates_images(self, target: str) -> bool:
        """Whether ``target`` has a picture generator: its own key, else the host's.

        ``product_image_key`` is the whole folder side, including the branch an
        explicit image key hides: with no image key anywhere and an OpenRouter
        endpoint, the launcher lets the key paying for the lane's words pay for
        its pictures, so a folder holding only its own LLM key can still draw.

        The fallback is the inheritance the product launchers perform -- a
        folder supplying nothing is configured from the host's
        ``tools.media.image`` -- so the two readers cannot disagree about a lane
        that was going to inherit anyway.

        A folder pinning its pictures to a provider is answered for that provider
        (:meth:`_pinned_lane_generates_images`).
        """
        from raven.agent.subagent.vendored_agents import product_image_key, product_image_pin
        from raven.agent.tools.media_gen import _OpenRouterMediaTool

        host = self._live_media_config("image", self.media_config.image)
        provider, model = product_image_pin(target)
        if provider:
            return self._pinned_lane_generates_images(target, provider, model, host)
        if product_image_key(target):
            return True
        return _OpenRouterMediaTool.has_key(host)

    def _pinned_lane_generates_images(self, target: str, provider: str, model: str, host) -> bool:
        """Whether a lane pinned to ``provider`` draws, by the launcher's rule for the pin.

        A mirror of ``configure_image_generation`` in the agent launchers, not a
        call into it: a launcher can run on an older raven, so the rule is stated
        twice and a test renders each case through both. The launcher refuses a
        provider the image tool cannot run on, and a switch off the host's image
        provider without a model of the lane's own. A switch leaves the host's
        image key behind with the rest of its endpoint: the lane pays with its own
        image key or the host's key for the provider it switched to, and with no
        key at a base of its own. Only OpenRouter takes the key that pays for the
        lane's words, which on the lane's own key is the only OpenRouter key its
        render holds. Read-only, like every readiness question.
        """
        from raven.agent.subagent.vendored_agents import product_image_key, product_llm_key, product_secret
        from raven.agent.tools.media_gen import _OpenRouterMediaTool
        from raven.config.schema import live_media_tool_config, media_provider
        from raven.providers.registry import canonical_provider_name, find_by_name

        wanted = canonical_provider_name(provider)
        spec = find_by_name(wanted)
        if spec is None or not getattr(spec, "image_api", False):
            return False
        runs_on = media_provider(host) if host is not None else ""
        if wanted != runs_on and not model:
            return False
        if product_image_key(target):
            return True
        if product_secret(target, "IMAGE_API_BASE"):
            return False
        if wanted == runs_on:
            return _OpenRouterMediaTool.has_key(host)
        if wanted == "openrouter" and product_llm_key(target):
            return False
        found = live_media_tool_config({"provider": wanted, "model": model}, self._live_config.get("providers"))
        return _OpenRouterMediaTool.has_key(found)

    def _lane_searches_images(self, target: str) -> bool:
        """Whether ``target`` can search for pictures: its vendor's key, its own or the host's.

        The vendor is the target's own where the target holds the host tool out
        and searches through its own plugin (:func:`product_picture_vendor`), else
        the one ``image_search`` itself speaks to -- the selected search vendor
        where that vendor has an image surface, Serper otherwise
        (:func:`raven.agent.tools.web.image_search_vendor`) -- and the key is
        resolved the way the lane resolves it: the product folder's own
        ``<PRODUCT>_<VENDOR>_API_KEY`` first, then the host's slot for that vendor
        (which the launcher copies into the lane's config), then the vendor's bare
        environment variable, which a launched product inherits. Not
        :meth:`_live_web_search_key`: that answers for ``web_search`` on a vendor
        that may search pages only.
        """
        import os

        from raven.agent.subagent.vendored_agents import product_picture_vendor, product_secret

        def key_for(vendor: str) -> str:
            return (
                product_secret(target, f"{vendor.upper()}_API_KEY")
                or self._live_vendor_key(vendor)
                or os.environ.get(SEARCH_PROVIDERS[vendor].env_var, "")
            )

        vendor = product_picture_vendor(target) or image_search_vendor(self.web_search_provider, key_for)
        return bool(key_for(vendor))

    def _live_vendor_key(self, vendor: str) -> str:
        """One named web vendor's key the file holds now, whatever the host selected.

        The selection-free half of :meth:`_live_web_search_key`, resolved in the
        same order: the canonical vendor slot, then the pre-vendor leaf that is
        Serper's alone, then the boot value.
        """
        from raven.config.live import live_vendor_key

        return live_vendor_key(self._live_config, vendor, boot=self._web_key(vendor))

    @property
    def provider(self) -> LLMProvider:
        """The provider of the binding the running turn entered.

        A property, not an attribute: the model is per session now, so there
        is no single answer to cache on the loop. Outside a turn (startup, a
        one-shot CLI call) this is the configured default.
        """
        binding = active_binding()
        return binding.provider if binding is not None else self._default_binding.provider

    @property
    def model(self) -> str:
        """The model id of the binding the running turn entered."""
        binding = active_binding()
        return binding.model if binding is not None else self._default_binding.model

    @property
    def context_window_tokens(self) -> int:
        """How much the binding of the running turn can hold.

        A property for the same reason ``provider`` and ``model`` are: two
        sessions can be on models of different sizes at once, so a single int
        on the loop has no answer that is right for both. Outside a turn this
        is the configured default's window.
        """
        binding = active_binding() or self._default_binding
        return binding.context_window

    def _turn_scope(self):
        """Hold the settings a turn reads more than once, from here on.

        Entered beside ``use_binding`` at the turn boundary and for the same
        reason: one turn, one answer. The cap is resolved here rather than at
        its first read, which happens after context assembly.
        """
        from raven.config.live import hold_for_this_turn, max_tool_iterations, reasoning_effort

        return hold_for_this_turn(
            **{
                _CAP_KEY: max_tool_iterations(self._live_config),
                _EFFORT_KEY: reasoning_effort(self._live_config),
            }
        )

    def _with_live_window(self, binding: ModelBinding) -> ModelBinding:
        """This turn's binding, carrying the window the config has right now.

        Resolved once per turn and then held, like the pair it rides on: the
        budget is read several times while a turn runs -- history trimming, the
        compaction check, the usage report -- and a number that moved between
        those reads would leave one turn disagreeing with itself. Re-read at the
        next turn, which is what makes a window set on a settings surface apply
        without bringing the gateway back.

        The same object comes back when the number has not changed, so the
        binding keeps the window it already resolved from the rates catalogue
        rather than resolving it again every turn.
        """
        from dataclasses import replace

        from raven.config.live import context_window_tokens

        configured = context_window_tokens(self._live_config)
        if configured == binding.configured_window:
            return binding
        return replace(binding, configured_window=configured)

    @property
    def default_reasoning_effort(self) -> str | None:
        """The effort a call runs at when the session pinned none.

        The provider holds a configured default too, but it is frozen at
        construction on purpose (see ``ResolvingProvider``: a credentials
        refresh must not import a live ``agents`` section), so an edit reached
        it only at a restart. Sent as an explicit argument instead, which is
        the path a session's pinned effort already takes.

        None is an answer, not an absence: the turn sends it as an explicit
        None, which the provider takes as its own default (nothing on the
        wire). Leaving the key out instead let the provider fill it from that
        frozen copy, so a key cleared on a running gateway kept every call at
        the word it booted with (``session_effort.in_force`` says the rule).
        """
        from raven.config.live import held, reasoning_effort

        return held(_EFFORT_KEY, lambda: reasoning_effort(self._live_config))

    def session_reasoning_effort(
        self,
        session_key: str | None,
        pick: str | None = None,
        model: str | None = None,
    ) -> str | None:
        """The reasoning effort this session's own calls run at, short of a hook's override.

        ``session_effort.in_force`` over this loop's inputs: the pick the caller
        read off the session's record, the session's mode's word, and the
        configured default as the file has it now. ``model`` is the id the loop
        was handed for this turn -- the router's pick when it made one, else the
        session's own -- and bounds the **pick** (``session_effort.clamp``): a
        level it does not declare, by LiteLLM's table or its provider overlay,
        is dropped rather than sent. The mode's word and the configured default
        are not narrowed here; they are not this conversation's pick and were
        sent as they stand before the pick existed. Beside ``session_tier`` for
        the reason it gives -- the turn and the RPC need the one rule, and the
        policy it reads lives here -- and reached on ``self`` by the turn, so
        nothing imports anything new.
        """
        from raven.agent import session_effort
        from raven.providers.overlay import declared_for
        from raven.providers.registry import find_by_model, split_model_id

        model_id = model or (
            self.session_model(session_key) if session_key and self.has_session_binding(session_key) else self.model
        )
        provider, _rest = split_model_id(model_id)
        if not provider:
            spec = find_by_model(model_id)
            provider = getattr(spec, "name", "") if spec else ""
        levels = effort_levels.levels_for(
            model_id,
            declared_for(self._live_config.get("providers") or {}, provider, model_id),
        )
        valid_pick = session_effort.clamp(pick, levels)
        if pick is not None and valid_pick is None:
            logger.debug(
                "session {}: reasoning effort {} is not declared by {}; using the session default",
                session_key,
                pick,
                model_id,
            )
        return session_effort.in_force(
            valid_pick,
            self.session_policy(session_key or "").reasoning_effort,
            self.default_reasoning_effort,
        )

    @property
    def personalization_enabled(self) -> bool:
        """Whether the personalization flow runs, as the file has it now.

        ``configure_personalization`` still sets what a process was built with;
        the file answers when it has an opinion, so a switch on a settings
        surface reaches the next turn.
        """
        from raven.config.live import held, personalization_enabled

        configured = held(_PERSONALIZATION_KEY, lambda: personalization_enabled(self._live_config))
        return self.enable_personalization if configured is None else configured

    @property
    def max_iterations(self) -> int:
        """The ReAct cap a turn runs under when its session pinned none.

        Read live rather than frozen at build for the reason the permission
        mode is: a cap is a sentence about the next turn, not work to redo.
        A turn holds the value it starts on (``run_turn`` passes it to
        ``hold_for_this_turn``), because the cap is first read after context
        assembly -- which can spend minutes in the curator -- and an edit made
        in that window would otherwise change the request already running.
        """
        from raven.config.live import held, max_tool_iterations

        configured = held(_CAP_KEY, lambda: max_tool_iterations(self._live_config))
        return configured or self._default_max_iterations

    @property
    def provider_pool(self) -> "ProviderPool | None":
        """Where a model id becomes a model id plus the credential for it."""
        return self._provider_pool

    @property
    def default_binding(self) -> ModelBinding:
        """What a session with no switch of its own runs on."""
        return self._default_binding

    def set_session_policy(
        self,
        session_key: str,
        *,
        max_iterations: int | None = None,
        mode: str = "",
        mode_overlay: dict | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        """Record the operating policy this session's next turn runs under.

        Nothing running is touched: a turn reads its policy once at its start,
        so a switch lands on the session's next turn -- the same contract the
        model picker states.
        """
        from raven.agent.loop._shared import SessionPolicy

        self._session_policies[session_key] = SessionPolicy(
            max_iterations=max_iterations,
            mode=mode,
            mode_overlay=dict(mode_overlay or {}),
            reasoning_effort=reasoning_effort or None,
        )

    def session_policy(self, session_key: str):
        """The session's policy, or the loop-wide defaults as one."""
        from raven.agent.loop._shared import SessionPolicy

        return self._session_policies.get(session_key, SessionPolicy())

    def session_tier(self, session_key: str | None) -> str:
        """The sub-agent effort tier in force for this session.

        One rule with one home. Two callers need it and they are not interchangeable:
        the manager reads it per dispatch, and a turn freezes it at its start so a
        switch arriving mid-turn lands on the next one. Both used to spell the
        expression out, so a change to how a tier is found had two places to reach.

        Lives here because the policy does: `session_policy` and `set_session_policy`
        are the pair it is derived from. Both callers reach it on `self` -- the loop is
        one object assembled from these mixins -- so nothing imports anything new.
        """
        return self.session_policy(session_key or "").mode or self._default_tier

    def binding_for_session(self, session_key: str) -> ModelBinding:
        """The binding this session runs on: its own switch, else the default.

        A new session has no entry, so it starts on the configured default
        rather than on whatever the last session switched to.

        A session whose choice is on disk but not yet in memory is restored here,
        on first ask. That is what makes the choice outlive a restart on *every*
        surface: the overrides live in this process, the session record is the
        only place they survive, and hanging the read off a TUI-only resume call
        meant a conversation on a channel came back on the default with its
        choice sitting unread in its own record.
        """
        binding = self._session_bindings.get(session_key)
        if binding is None:
            self._restore_once(session_key)
            binding = self._session_bindings.get(session_key)
        if binding is not None:
            fresh = self._rebound(binding)
            if fresh is not binding:
                self._session_bindings[session_key] = fresh
            return fresh
        fresh = self._rebound(self._default_binding)
        if fresh is not self._default_binding:
            # Adopt the pool's object as the default, through the setter so the
            # out-of-turn fallbacks (subagents, context engine, consolidator)
            # follow the same credential edit. Adopting is also what keeps the
            # identity stable: compared against a default that never moves, the
            # pool's instance would read as "provider changed" on every ask and
            # the transport-verdict caches would never survive a turn.
            self.set_default_binding(fresh)
        return fresh

    def _rebound(self, binding: ModelBinding) -> ModelBinding:
        """The same pair, re-asked from the pool -- so an edited credential
        serves the session's next turn instead of its next process.

        The pool already drops its cache when the credentials fingerprint
        changes; what was missing was a caller asking it again. Asked every
        time rather than behind a change gate: the unchanged round trip is
        1.6 ms (measured; the supplier reloads the config for the
        fingerprint), which is noise at turn entry, and a gate shared across
        sessions let the first asker consume the change for all of them.

        A pair that can no longer be built -- the credential was removed, not
        rotated -- keeps the binding it has: a turn on the provider that
        answered a second ago beats no turn at all, matching what
        ``restore_session_model`` does with the same failure.
        """
        pool = self._provider_pool
        if pool is None:
            return binding
        provider_name = getattr(binding.provider, "provider_name", "") or ""
        if not provider_name:
            # A provider that cannot name its vendor cannot be re-paired safely:
            # deriving one from the model id is a guess about whose credential
            # pays, and the gateway's default is a ResolvingProvider -- a
            # multi-vendor dispatcher that a single direct binding must never
            # replace. Its credential liveness is its own concern; this path
            # refreshes only pairs the pool built.
            return binding
        try:
            fresh = pool.bind(binding.model, provider_name)
        except Exception as exc:
            logger.warning("cannot rebuild the provider for {!r} ({}); keeping the current one", binding.model, exc)
            return binding
        if fresh.provider is not binding.provider:
            self._forget_transport_verdicts()
        return fresh

    def _restore_once(self, session_key: str) -> None:
        """Read this session's stored model, at most once per key per process.

        The negative answer is remembered too. Most sessions never switched, and
        without that this would re-read a record on every turn to learn the same
        nothing.
        """
        if session_key in self._restore_attempted:
            return
        self._restore_attempted.add(session_key)
        sessions = getattr(self, "sessions", None)
        if sessions is None or self._provider_pool is None:
            return
        try:
            record = sessions.peek(session_key)
        except Exception as exc:
            logger.debug("cannot read session {!r} to restore its model: {}", session_key, exc)
            return
        metadata = getattr(record, "metadata", None) or {}
        model = metadata.get("model")
        if model:
            self.restore_session_model(session_key, model, metadata.get("provider"))

    def stored_session_permission_mode(self, session_key: str) -> str | None:
        """The permission mode this session chose in an earlier process, off its record.

        The gate's reader for ``permissions/session.py``: consulted once per
        unknown conversation, the same record and the same tolerance for an
        unreadable one as ``_restore_once`` applies to the model.
        """
        sessions = getattr(self, "sessions", None)
        if sessions is None:
            return None
        try:
            record = sessions.peek(session_key)
        except Exception as exc:
            logger.debug("cannot read session {!r} to restore its permission mode: {}", session_key, exc)
            return None
        metadata = getattr(record, "metadata", None) or {}
        mode = metadata.get("permissions_mode")
        return mode if isinstance(mode, str) and mode else None

    def session_model(self, session_key: str) -> str:
        """What to show this session's user, which is not the global default."""
        return self.binding_for_session(session_key).model

    def has_session_binding(self, session_key: str) -> bool:
        """Did this session switch, or is it just following the default?

        ``session_model`` cannot answer that -- it falls back to the default,
        so it never returns None. Callers that must distinguish "chose this"
        from "inherited this" ask here.

        Restores first, so a session that switched before a restart answers yes
        rather than being reported as having inherited the default.
        """
        if session_key not in self._session_bindings:
            self._restore_once(session_key)
        return session_key in self._session_bindings

    def restore_session_model(self, session_key: str, model: str, provider_name: str | None = None) -> None:
        """Put a session back on the model it was last switched to.

        Session overrides live in memory, so without this a restart moves every
        switched session back to the default and the user's choice lasts exactly
        as long as the process. A model that can no longer be built (a credential
        since removed) leaves the session on the default rather than failing the
        turn that asked.

        Normally reached through ``_restore_once``, which supplies the stored
        pair; kept public for a caller that has the pair already.
        """
        self._restore_attempted.add(session_key)
        pool = self._provider_pool
        if pool is None or not model:
            return
        try:
            self.set_session_binding(session_key, pool.bind(model, provider_name))
        except Exception as exc:
            # Broad on purpose. Building a provider imports a vendor module and
            # checks credentials, so the failures reachable here are open-ended
            # -- ``MissingCredentialsError`` is one that did not exist when this
            # guard was first written, and it escaped a tuple of three. A resume
            # that lands on the default is a worse session; a resume that raises
            # is no session at all.
            logger.warning("session {!r} cannot resume on {!r} ({}); using the default", session_key, model, exc)

    def set_session_binding(self, session_key: str, binding: ModelBinding) -> None:
        """Switch one session, leaving every other session where it was.

        Applied immediately and still safe mid-turn: a turn resolves its
        binding once at ``run_turn`` entry and holds it in a context var for
        its whole tree, including anything it detaches. So a switch during a
        turn cannot move that turn -- it lands on the next one -- and no
        parking is needed to arrange that.
        """
        self._session_bindings[session_key] = binding
        self._forget_transport_verdicts()

    def clear_session_binding(self, session_key: str) -> None:
        """Drop a session's override so it follows the default again.

        Deliberately does not mark the key as consulted. The one caller is
        ``session.delete``, which unlinks the record before this runs, so there
        is nothing left for a later ask to read back in -- and a session that
        somehow kept its record is better served by re-reading it than by a
        marking that claims we looked when we did not.
        """
        self._session_bindings.pop(session_key, None)
        # The Persona goes with the session for the same reason the binding
        # does: a deleted session has nothing left to run as, and this is the
        # one place that says a session is over.
        self._session_personas.pop(session_key, None)

    def _forget_transport_verdicts(self) -> None:
        """Drop the capability verdicts a new provider may answer differently.

        Both caches key on a model id but are computed from the provider serving
        it, so a rebuild that keeps the id keeps the old endpoint's answer. The
        reachable case is an ``apiBase`` repointed at a box with different
        capabilities, or a re-authenticated provider: the credentials
        fingerprint changes, the pool builds a new provider, the model id does
        not move -- and images stay dropped from tool results for the life of
        the process, with nothing in the log to say why.

        Cleared wholesale rather than per binding: the loop now holds several
        providers at once, and the key does not say which one answered.
        """
        self._image_tool_result_ok.clear()
        self._vision_ok.clear()

    def bind_session_charter(self, session_key: str, payload: Any) -> None:
        """Hold the charter a dispatch brought, for that session's next turn.

        Held aside rather than applied here, for the reason
        ``ToolRegistry.bind_session_tools`` gives: the handler that accepts a
        dispatch cannot open the turn's scope, because it submits the turn and
        the turn runs on a task that inherits nothing from it. One process
        serves every session on a connection, so this is keyed by session and
        never global.
        """
        charter = charter_mod.parse(payload)
        if charter is None:
            self._session_charters.pop(session_key, None)
            return
        # The worker-side counterpart of the host's "N worker(s) for this turn".
        # Without it the only record that a brief crossed the process boundary
        # is the behaviour it produced, and a charter that was dropped on the
        # way looks exactly like one that was never written.
        logger.info(
            "agent playbook: charter staged for this session ({} tool(s), {} check(s){})",
            "all" if charter.tools is None else len(charter.tools),
            len(charter.checks),
            ", judge" if charter.code else "",
        )
        self._session_charters[session_key] = charter

    def _take_session_charter(self, session_key: str) -> "Charter | None":
        """The charter staged for this session, consumed.

        Consumed rather than read: a charter describes one dispatch. Leaving it
        would hold the next turn of the same session to a brief written for the
        last one, and a resumable instance takes many turns on one session.
        """
        return self._session_charters.pop(session_key, None)

    def adopt_session_persona(self, session_key: str, resolution: Any) -> None:
        """Make this session run as the Persona that was just generated.

        The generating turn itself does not run as it -- that turn saves the
        artifact and reports it, and a coordinator whose ``intake`` gates on
        trip dates would refuse to answer that. What is adopted here is read by
        every *later* turn of the session, which is the experience the Persona
        was written for.
        """
        if getattr(resolution, "coordinator_charter", None) is None:
            self.bind_session_harness(session_key, None)
            return
        self.bind_session_harness(session_key, resolution.artifact_name or "", spec=resolution.spec)

    def bind_session_harness(self, session_key: str, name: str | None, *, spec: Any = None) -> str | None:
        """Freeze a Harness onto this session, for every turn it takes.

        A snapshot rather than a name to look up later: the window the user
        opened keeps the Harness they chose, so editing the library entry --
        or deleting it -- does not rewrite or break a conversation already
        running on it. It rides the session's own metadata, so it survives a
        restart the way a pinned workdir does.

        ``None`` unbinds. ``spec`` is for a caller that already holds the
        Harness; everything else names one in the library.
        """
        self._session_personas.pop(session_key, None)
        session = self.sessions.get_or_create(session_key)
        if name is None:
            session.metadata.pop("harness", None)
            return None
        if spec is None:
            stored = self._playbooks.spec(name) if self._playbooks is not None else None
            spec = getattr(stored, "harness", None)
            if spec is None:
                raise ValueError(f"no stored Harness named {name!r}")
        # ``exclude_unset`` for the reason ``UnifiedPlaybookSpec.block_dump``
        # gives: a nested default re-emitted here reads back as an explicit
        # choice, and ``Checks.impl`` then fails validation as a disabled field.
        session.metadata["harness"] = {
            "name": name,
            "spec": spec.model_dump(by_alias=True, exclude_none=True, exclude_unset=True),
        }
        logger.info("agent playbook: session bound to Harness {} ({} worker(s))", name or "?", len(spec.delegate))
        return name

    def session_harness_name(self, session_key: str) -> str | None:
        """The Harness this session is bound to, by name, for a client to show."""
        held = self.sessions.get_or_create(session_key).metadata.get("harness")
        return str(held.get("name") or "") or None if isinstance(held, dict) else None

    def session_persona(self, session_key: str) -> "tuple[Charter, Any] | None":
        """This session's bound Harness as seats, or ``None`` for an ordinary one.

        Rebuilt from the frozen snapshot rather than from the library, and
        cached per session because it is asked once a turn.
        """
        held = self._session_personas.get(session_key)
        if held is not None:
            return held
        raw = self.sessions.get_or_create(session_key).metadata.get("harness")
        if not isinstance(raw, dict) or not isinstance(raw.get("spec"), dict):
            return None
        try:
            from raven.playbook.agent_generator import build_coordinator_charter, build_table
            from raven.playbook.agent_spec import AgentPlaybookSpec

            spec = AgentPlaybookSpec.model_validate(raw["spec"])
            charter = build_coordinator_charter(spec)
        except Exception:  # noqa: BLE001 - a Harness this build cannot read is not a reason to lose the turn
            logger.opt(exception=True).warning("agent playbook: session Harness could not be rebuilt; running plain")
            return None
        if charter is None:
            return None
        seats = (charter, build_table(spec, {}))
        self._session_personas[session_key] = seats
        return seats

    async def _resolve_playbook_turn(self, req: Any, session_key: str, binding: Any):
        """Generate, persist and bind the Harness selected by this turn's mode."""
        from raven.playbook.agent_generator import HarnessResolution

        cfg = self._playbook_config
        if cfg is None or not cfg.enabled or getattr(cfg, "agent_harness", "default") != "generate":
            return HarnessResolution()
        asked = getattr(req, "playbook_mode", None)
        mode = asked or getattr(cfg, "default_generation_mode", "task")
        if mode == "off":
            return HarnessResolution()
        if is_subagent_process() or getattr(req, "direct_target", None) is not None:
            return HarnessResolution()
        # A Persona is minted only for a turn that asked for one. The config's
        # default says what this deployment generates, not that every message
        # is a request to be given an identity -- and read as the latter it
        # was: a reader typing "hi" spent a model call minting a Persona,
        # bound the conversation to it, and heard the assistant volunteer a
        # draft nobody had asked about. A Task artifact configures workers for
        # the turn in hand and carries no identity, so the default still
        # reaches it.
        if mode == "persona" and asked != "persona":
            return HarnessResolution()
        try:
            from raven.playbook.agent_generator import (
                PersonaPlaybookGenerator,
                TaskPlaybookGenerator,
                persona_roster_profile,
                task_roster_profile,
            )

            metas = list(self.subagents.list_agents())
            agents = [a.name for a in metas]
            if not agents:
                return HarnessResolution()
            tool_catalog = self.tools.get_definitions()
            query = getattr(req, "text", "") or ""
            if mode == "persona":
                profiles = {meta.name: persona_roster_profile(meta) for meta in metas}
                names = self._playbooks.names() if self._playbooks is not None else []
                resolution = await PersonaPlaybookGenerator(binding.provider, binding.model).resolve(
                    query,
                    agents,
                    tool_catalog,
                    profiles,
                    names,
                )
            else:
                profiles = {meta.name: task_roster_profile(meta) for meta in metas}
                resolution = await TaskPlaybookGenerator(binding.provider, binding.model).resolve(
                    query,
                    agents,
                    tool_catalog,
                    profiles,
                )
            if resolution.active:
                # Only a Persona is a draft. A Task artifact is the record of
                # a graph the turn is about to run and its Workflow is compiled
                # into it when the turn ends (``_finish_playbook_turn``), so
                # holding one back would leave that compile nothing to update.
                if getattr(resolution, "generation_mode", None) == "persona":
                    resolution = self._hold_generated_harness(resolution, query, session_key)
                else:
                    resolution = self._persist_generated_harness(resolution, query)
        except Exception:  # noqa: BLE001 - setup must not cost the turn
            logger.opt(exception=True).warning("agent playbook: resolution failed; running unconfigured")
            return HarnessResolution()
        table = resolution.table
        if table:
            logger.info("agent playbook: {} worker(s) for this turn: {}", len(table.workers), table.labels())
        return resolution

    def _hold_generated_harness(self, resolution: Any, query: str, session_key: str):
        """Hold a validated Harness as this session's draft; nothing is written.

        A generated Persona is an answer on screen, not a library entry. The
        page that asked for it saves it by name (``playbooks.draft_save``) or
        lets it go, and until then the library is untouched -- which is the
        whole difference from what this did before: a row was written on every
        turn that generated one, so a reader who asked twice ended up with
        ``travel-planner`` and ``travel-planner-2`` and nothing saying which of
        the two they had meant to keep.

        The draft still binds to the session (``adopt_session_persona`` runs on
        the resolution this returns), because running as the Persona you just
        described is the point of describing it; saving is what makes it
        outlive the conversation.
        """
        if resolution.spec is None:
            return resolution
        from dataclasses import replace

        artifact = self._generated_artifact(resolution, query)
        self._session_drafts[session_key] = artifact
        logger.info("playbook: holding generated Harness {!r} as {}'s draft", artifact.name, session_key)
        return replace(resolution, artifact_name=artifact.name, persisted=False)

    def _generated_artifact(self, resolution: Any, query: str):
        """The library artifact a generated Harness would be saved as."""
        import re

        from raven.playbook.unified import PlaybookMatch, UnifiedPlaybookSpec

        description = (resolution.description or query.strip().splitlines()[0])[:200]
        keywords = [word.lower() for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", query)[:8]]
        return UnifiedPlaybookSpec(
            name=resolution.artifact_name or resolution.spec.name,
            description=description,
            match=PlaybookMatch(summary=description, keywords=keywords or [description[:80]]),
            harness=resolution.spec,
        )

    def _persist_generated_harness(self, resolution: Any, query: str):
        """Save a validated Harness now; persistence failure never blocks binding.

        The Task path's own. A Persona goes through ``_hold_generated_harness``
        instead, because a Persona is something a reader decides to keep.
        """
        if self._playbooks is None or resolution.spec is None:
            return resolution
        from dataclasses import replace

        artifact = self._generated_artifact(resolution, query)
        try:
            artifact, path = self._save_generated_artifact(artifact)
            self._playbooks.adopt(artifact.name)
            logger.info("playbook: saved generated Harness {!r} at {}", artifact.name, path)
            return replace(
                resolution,
                spec=artifact.harness,
                artifact_name=artifact.name,
                persisted=True,
            )
        except Exception:  # noqa: BLE001 - persistence must not cost the turn
            logger.opt(exception=True).warning("playbook: generated Harness could not be saved; using it in memory")
            return resolution

    def session_draft(self, session_key: str):
        """The Persona this session generated and has not saved, or ``None``."""
        return self._session_drafts.get(session_key)

    def save_session_draft(self, session_key: str, name: str | None = None) -> str:
        """Write this session's draft to the library, and answer its saved name.

        The name is the reader's to choose: the generator proposes one, and the
        page may send another. A collision takes the first free numeric suffix,
        the way every other generated artifact does -- but now only when a
        reader asked for the save, so the suffix means two Personas were kept,
        not two turns were taken.
        """
        artifact = self._session_drafts.get(session_key)
        if artifact is None:
            raise ValueError("this session has no generated Persona to save")
        if self._playbooks is None:
            raise RuntimeError("Playbook runtime is unavailable")
        wanted = (name or artifact.name).strip()
        if not wanted:
            raise ValueError("a Persona needs a name")
        if wanted != artifact.name:
            harness = artifact.harness
            if harness is not None:
                harness = harness.model_copy(update={"name": wanted})
            artifact = artifact.model_copy(update={"name": wanted, "harness": harness})
        saved, path = self._save_generated_artifact(artifact)
        self._playbooks.adopt(saved.name)
        self._session_drafts.pop(session_key, None)
        logger.info("playbook: saved Persona {!r} at {}", saved.name, path)
        return saved.name

    def discard_session_draft(self, session_key: str) -> bool:
        """Drop this session's unsaved Persona; ``True`` when there was one."""
        return self._session_drafts.pop(session_key, None) is not None

    def _save_generated_artifact(self, artifact: Any):
        """Atomically save under the requested name or the first numeric suffix."""
        if self._playbooks is None:
            raise RuntimeError("Playbook runtime is unavailable")
        from raven.playbook.store import PlaybookExistsError

        base = artifact.name
        attempt = 1
        while True:
            name = base if attempt == 1 else f"{base}-{attempt}"
            harness = artifact.harness
            if harness is not None:
                harness = harness.model_copy(update={"name": name})
            candidate = artifact.model_copy(update={"name": name, "harness": harness})
            try:
                return candidate, self._playbooks.store.save(candidate)
            except PlaybookExistsError:
                attempt += 1

    async def _write_worker_table(self, req: Any, session_key: str, binding: Any) -> "DelegateTable | None":
        """Backward-compatible table-only face used by focused tests."""
        return (await self._resolve_playbook_turn(req, session_key, binding)).table

    async def _finish_playbook_turn(self, resolution: Any, capture: Any, binding: Any) -> None:
        """Update a Task artifact with proven Workflow evidence and save the run."""
        if self._playbooks is None:
            return
        from raven.playbook.run_record import RunRecordStore

        try:
            capture.saved_playbook = resolution.artifact_name if resolution.persisted else None
            if resolution.capture_workflow and capture.dags and not resolution.selected_playbook:
                from raven.playbook.workflow_compiler import WorkflowCompiler

                evidence = capture.dags[-1]
                # The evidence's own Harness, when the run extended it with a
                # derived role, is what the compiled graph names. Falling back to
                # the resolution's keeps a run that moved nothing compiled against
                # exactly the Harness the turn was given.
                harness = evidence.harness if evidence.harness is not None else resolution.spec
                artifact = await WorkflowCompiler(binding.provider, binding.model).compile(
                    query=capture.query,
                    dag=evidence.spec,
                    run_id=capture.run_id,
                    harness=harness,
                    name_hint=resolution.artifact_name,
                    description_hint=resolution.description,
                )
                store = self._playbooks.store
                if resolution.persisted:
                    # The compiler parameterizes a proven graph; it does not own
                    # artifact identity. Keep the numeric suffix chosen by the
                    # atomic Harness save even when the model ignores nameHint,
                    # and stamp it on the Harness too: a run's frozen table was
                    # built before a save collision could rename the artifact.
                    stored_harness = (
                        harness.model_copy(update={"name": resolution.artifact_name}) if harness is not None else None
                    )
                    artifact = artifact.model_copy(update={"name": resolution.artifact_name, "harness": stored_harness})
                    path = store.save(artifact, overwrite=True)
                else:
                    artifact, path = self._save_generated_artifact(artifact)
                self._playbooks.adopt(artifact.name)
                capture.saved_playbook = artifact.name
                logger.info("playbook: updated Task artifact {!r} with Workflow at {}", artifact.name, path)
            capture.finish()
        except Exception as exc:  # noqa: BLE001 - persistence must not replace the user's answer
            logger.opt(exception=True).warning("playbook: turn finalization failed")
            capture.finish(status="completed", error=str(exc))
        finally:
            try:
                path = RunRecordStore(self._playbooks.store.root).save(capture)
                logger.info("playbook: saved run record {}", path)
            except Exception:  # noqa: BLE001 - record failure must not cost the turn
                logger.opt(exception=True).warning("playbook: run record could not be saved")

    def set_default_binding(self, binding: ModelBinding) -> None:
        """Change what new sessions start on.

        Sessions that already switched keep their own binding; sessions that
        never did pick this up on their next turn. Subsystem fallbacks are
        re-pointed too, for the paths that run outside a turn and therefore
        have no binding to read.
        """
        self._default_binding = binding
        self._forget_transport_verdicts()
        self.subagents.set_provider(binding.provider, binding.model)
        self.context_engine.set_provider(binding.provider, binding.model)
        self.memory_consolidator.set_provider(binding.provider, binding.model)

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        """Change the default binding, from a pair a caller already built.

        No production caller today -- every switch path goes through the pool
        and lands on ``set_default_binding`` or ``set_session_binding``. Kept as
        the pair-free entry point for an embedder that has a provider in hand,
        which is why it carries the current default's window forward: a window
        the user pinned belongs to whatever they run, and building the binding
        without it here would drop it the day this grows a caller. Taken from
        the binding rather than re-read from the file, because a caller holding
        a provider of its own need not be one this process loaded a config for;
        a turn re-reads the file anyway (see ``_with_live_window``).
        """
        self.set_default_binding(ModelBinding(provider, model, self._default_binding.configured_window))

    def configure_personalization(self, enable: bool) -> None:
        """Global switch for the 4-step personalization flow (PAHF-inspired).

        When enabled, each message goes through:
          Step 1 - classify:          classify() — does this request need a preference question?
          Step 2 - pre-action interaction: ask one question if needed, extract and store the answer
          Step 3 - execute:           normal agent loop (unchanged)
          Step 4 - post-action learn: post_learn() runs in background after every response

        Disabled by default. Enable via config: agents.defaults.enable_personalization: true
        """
        self.enable_personalization = enable
        logger.info("Personalization flow: {}", "enabled" if enable else "disabled")

    @property
    def web_search_provider(self) -> str:
        """The search vendor a call runs on, as the file has it now.

        The keys have been read live since they landed and resolve against this
        selection (``_live_web_search_key``), so a vendor frozen at startup was
        the half that kept the pair on the old endpoint. Held for the turn, like
        the rest of them.
        """
        from raven.config.live import default_live, held, web_providers

        configured = held(_WEB_VENDORS_KEY, lambda: web_providers(default_live()))
        return configured[0] or self._boot_web_search_provider

    @property
    def web_fetch_provider(self) -> str:
        """The fetch vendor a call runs on. See :attr:`web_search_provider`."""
        from raven.config.live import default_live, held, web_providers

        configured = held(_WEB_VENDORS_KEY, lambda: web_providers(default_live()))
        return configured[1] or self._boot_web_fetch_provider

    def _web_key(self, vendor: str) -> str | None:
        return resolve_vendor_key(vendor, self.web_provider_keys, self.search_api_key, self.jina_api_key)

    async def _record_image_usage(self, usage: UsageSnapshot) -> None:
        tracker = self.strategies.get("usage_tracker")
        if tracker is not None:
            await tracker.after_llm_call({}, usage)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dirs = (self.workspace,) if self.restrict_to_workspace else ()
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool, GrepTool, FindTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dirs=allowed_dirs))
        # Only beside a history strategy that puts refs where the model reads
        # them: next to one that does not, the tool is a capability the model
        # reaches for and is always told "found nothing". Read with a default
        # because an engine slotted in through the runtime door need not
        # subclass the contract.
        if getattr(self.context_engine, "mints_source_refs", False):
            from raven.agent.tools.context_retrieve import ContextRetrieveTool

            self.tools.register(ContextRetrieveTool(self.sessions))
        if self._deliverables is not None:
            from raven.agent.tools.deliver import DeliverFilesTool

            self.tools.register(
                DeliverFilesTool(self._deliverables, workspace=self.workspace, allowed_dirs=allowed_dirs)
            )
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
                executor=self._executor,
                extra_allowed_dirs=(self.workspace,),
                record_writes=True,
                shadow=self._command_shadow,
            )
        )
        # The registry writer beside exec's machine channel, for the products
        # that run work on the owner's machines. Registered on the flag rather
        # than withheld: this is a deploy-time product choice, not a credential
        # that may arrive mid-session, and five product tool faces are pinned
        # against a tool boarding uninvited.
        if self.connection_add:
            self.tools.register(ConnectionAddTool())
        # web_search and the media tools register whatever the config holds and
        # are *withheld* while their section asks for nothing -- see
        # ``_unconfigured_tool_names``. Registration used to be the gate, which
        # made the switch one-way: a key added while the process ran could not
        # surface a tool nothing had registered. Withholding is reversible per
        # model call, and the tools read their key through a live reader, so the
        # same edit that lifts the veil also serves the call.
        # The exact objects the config gate below may judge. Identity, not
        # type: a plugin that shadows one of these names -- even with a
        # subclass -- replaces the entry, and the built-in's config section
        # says nothing about the replacement's credential story.
        # The material this conversation was pointed at. Registered always and
        # withheld where nothing was picked, which is the reversible direction:
        # a reader who attaches a base mid-conversation gets the tool on the
        # next model call rather than on the next process.
        from raven.agent.tools.knowledge import KnowledgeSearchTool

        self.tools.register(KnowledgeSearchTool())
        self._config_gated_tools: dict[str, Any] = {}
        web_search = WebSearchTool(
            api_key=self._live_web_search_key,
            proxy=self.web_proxy,
            provider=lambda: self.web_search_provider,
        )
        self.tools.register(web_search)
        self._config_gated_tools[web_search.name] = web_search
        # Pictures: the selected vendor's image surface where it has one, Serper's
        # otherwise, on that vendor's key, gated and withheld the same way -- and
        # only where `tools.web.search.images` asks for the tool at all, so a lane
        # that never places a picture keeps the tool face it had.
        if self.image_search:
            # Both halves follow the selection: the picture vendor is derived
            # from it, so freezing either one pins the pair to the boot choice.
            def picture_vendor() -> str:
                return image_search_vendor(self.web_search_provider, self._web_key)

            image_search = ImageSearchTool(
                api_key=lambda: self._live_vendor_key(picture_vendor()),
                proxy=self.web_proxy,
                provider=picture_vendor,
            )
            self.tools.register(image_search)
            self._config_gated_tools[image_search.name] = image_search
        # web_fetch registers the same way and is never withheld: Jina needs no
        # key, so a keyed backend selected without one is replaced by Jina
        # rather than left to fail.
        # The substitution moved into the tool, which asks it per call: decided
        # here it outlived the key that would have stopped it.
        self.tools.register(
            WebFetchTool(
                api_key=lambda: self._live_vendor_key(self.web_fetch_provider),
                proxy=self.web_proxy,
                provider=lambda: self.web_fetch_provider,
            )
        )
        # The shared browser's tools. Registered always and withheld through
        # ``configured()`` while the browser extra is not installed, so the
        # schema carries no verbs that can only answer with an install hint;
        # the panel is where a reader learns the extra exists.
        from raven.agent.tools.browser import browser_tools

        for tool in browser_tools():
            self.tools.register(tool)
        # Media tools (image/speech/video) are opt-in: a tool is registered only
        # when the user configured it (a model or apiKey under tools.media.<tool>),
        # which Config.effective_media_config() surfaces as a resolved key/model.
        # An OpenRouter key set for chat alone never enables them.
        media = self.media_config
        media_tools = (
            (ImageGenerateTool, "image", media.image),
            (SpeechGenerateTool, "speech", media.speech),
            (VideoGenerateTool, "video", media.video),
        )
        for cls, kind, tool_cfg in media_tools:
            tool = cls(
                self._media_config_reader(kind, tool_cfg),
                workspace=self.workspace,
                proxy=media.proxy,
                output_subdir=media.output_subdir,
                restrict_to_workspace=self.restrict_to_workspace,
                usage_recorder=self._record_image_usage if kind == "image" else None,
            )
            self.tools.register(tool)
            self._config_gated_tools[tool.name] = tool
        self.tools.register(MessageTool())
        # Not registered at all for a sub-agent, rather than hidden from the
        # schema: hiding leaves the tool in the registry, which is exactly how the
        # graph controls stay reachable through ``tool_call``, so it closes nothing.
        if not is_subagent_process():
            self._register_orchestration_tools()
        # The question responder is a per-transport singleton, late-bound via
        # set_broker once the transport (TUI RPC server / gateway hub) exists.
        self.tools.register(AskUserTool(timeout_s=self.ask_user_config.timeout))
        # The plugin market, reachable from the conversation. Unconditional: the
        # catalog ships in the wheel and the connection manager is this loop's
        # own, so the only thing that ever made this impossible was the tool not
        # existing -- an agent asked to connect an integration could reach the
        # engine no other way than telling the user to go to the panel.
        from raven.agent.tools.plughub import PluginTool

        self.tools.register(PluginTool(loop=self, registry=self.tools))
        # Its settings writers and its restart are lent later by the entrance
        # (rpc bootstrap, gateway); until then it reads and writes raw settings.
        # A sub-agent is not given it: the configuration is the host's.
        if not is_subagent_process():
            from raven.agent.tools.raven_config import GUIDE_SKILL_ID as _CONFIG_GUIDE
            from raven.agent.tools.raven_config import RavenConfigTool

            self.tools.register(
                RavenConfigTool(
                    guide_skill_id=self._shipped_guide(_CONFIG_GUIDE),
                    session_model=lambda key: (self.session_model(key), self.has_session_binding(key)),
                    tool_names=lambda: self.tools.names(),
                )
            )
        if self.cron_service:
            # Function-scope import on purpose: the cron tool is cargo the loop must
            # not name at module level (tests/test_l3_open_world.py counts module-level
            # imports), so the edge stays lazy and the loop is fully loaded when it fires.
            from raven.proactive_engine.schedulers.cron.tool import CronTool

            self.tools.register(CronTool(self.cron_service))

        # Plugin-contributed tools (e.g. EverOS's ``understand_media``).
        # Registered last so a plugin can override a built-in by name if
        # it deliberately contributes the same name; ``_withheld_tool_names``
        # still runs afterward and can strip any of them. A tool that declares
        # ``bind_runtime`` is granted the loop's late-bound handles in
        # ``_bind_plugin_runtime``, not here: the handles carry organs (the
        # playbook funnel) assembled after even this registry is populated.
        for tool in self.plugin_tools:
            # A same-name replacement of ``exec`` reports the files its commands
            # wrote and removed, as the built-in does. Asked before the door:
            # measuring raises the tool's ceiling, and the registry reads the
            # ceiling off the spec it admits, never off the tool afterwards.
            measure_writes = getattr(tool, "measure_writes", None)
            if tool.name == "exec" and callable(measure_writes):
                measure_writes(self._command_shadow)
            self.tools.register(tool)

        # Skill retrieval tools (body -> scripts). Both are source-agnostic and
        # both serve local/everos straight from the registry, so both register
        # whenever the registry is reachable; a Hub endpoint only adds their
        # ``hub/`` branch. Gating ``read_skill`` on the client would take the
        # body-fetch route away from the skills that ship with Raven, whose
        # ``inject: description`` entry advertises exactly that route.
        skill_registry = getattr(
            getattr(self.context, "skills", None),
            "registry",
            None,
        )
        if skill_registry is not None or self._skill_hub_client is not None:
            from raven.agent.tools.skill_hub import FindSkillTool, ReadSkillTool, UseSkillTool
            from raven.config.live import skill_hub_enabled

            self.tools.register(
                ReadSkillTool(
                    client=self._skill_hub_client,
                    registry=skill_registry,
                    min_safety=self._skill_min_safety,
                    blocklist=self._skill_blocklist,
                    blocklist_reader=self._skill_blocklist_reader,
                    enabled_reader=lambda: skill_hub_enabled(self._live_config),
                ),
            )
            # Pull-mode discovery: search on the model's own terms through the
            # same router the context engine retrieves with.
            self.tools.register(
                FindSkillTool(
                    lambda: getattr(self.context_engine, "skills_router", None),
                    hub_wired=self._skill_hub_client is not None,
                    enabled_reader=lambda: skill_hub_enabled(self._live_config),
                    min_safety=self._skill_min_safety,
                    blocklist=self._skill_blocklist,
                    blocklist_reader=self._skill_blocklist_reader,
                ),
            )
            self.tools.register(
                UseSkillTool(
                    client=self._skill_hub_client,
                    registry=skill_registry,
                    min_safety=self._skill_min_safety,
                    blocklist=self._skill_blocklist,
                    blocklist_reader=self._skill_blocklist_reader,
                    enabled_reader=lambda: skill_hub_enabled(self._live_config),
                    auto_install=self._skill_auto_install,
                    install_audit_path=(
                        self.workspace / "skills" / "hub" / "installs.jsonl"
                        if self._skill_hub_client is not None
                        else None
                    ),
                ),
            )

        # Progressive tool disclosure. Registered last so the catalog it
        # searches covers every built-in/plugin tool above; MCP tools join
        # later (registered in ``_connect_mcp``) and the strategy picks them up
        # since it re-reads the registry each turn.
        #
        # ``tool_call`` is registered whatever the config says, because folding
        # is not the only thing that keeps a tool out of the schema: every
        # ``hide_from_schema`` tool above (the DAG controls) is dispatchable and
        # unnameable without it, and this feature is off by default. Only
        # ``tool_search`` and the fold itself turn on with the switch.
        from raven.agent.tools.tool_search import (
            DEFAULT_ALWAYS_VISIBLE,
            ToolCallTool,
            ToolSearchController,
            ToolSearchStrategy,
            ToolSearchTool,
        )
        from raven.config.schema import ToolSearchConfig

        cfg = self._tool_search_config or ToolSearchConfig()
        always = set(DEFAULT_ALWAYS_VISIBLE) | set(cfg.always_visible)
        self.tool_search_controller = ToolSearchController(
            self.tools,
            always_visible=always,
            search_result_limit=cfg.search_result_limit,
            compaction_threshold=cfg.compaction_threshold,
        )
        self.tools.register(ToolCallTool(self.tool_search_controller))
        if cfg.enabled:
            self.tools.register(ToolSearchTool(self.tool_search_controller))
            # ``first=True``: filter the tool list before CacheOptimizer marks
            # the final tool with ``cache_control`` (else the marked tool may be
            # filtered out and the breakpoint lost).
            self.strategies.register(
                ToolSearchStrategy(self.tool_search_controller),
                first=True,
            )

    def _register_orchestration_tools(self) -> None:
        """Every tool that hands work to another agent, or steers a hand-off already running.

        Grouped into one method so the sub-agent gate has a single place to
        refuse, rather than a condition repeated over six registrations that a
        seventh would quietly not get.
        """
        self.tools.register(SpawnTool(manager=self.subagents))
        # Outbound A2A: hands work to an external agent rather than one this
        # process manages, but it is still a hand-off this method's gate must
        # cover -- see raven.agent.subagent.role.WITHHELD_FROM_SUBAGENT.
        #
        # Only with a peer configured. This one schema costs about 157 tokens in
        # the `tools` array of every turn of every conversation -- measured off a
        # captured provider call, not estimated; the total is pinned by
        # tests/test_agent_loop_token_budget.py -- and a host with no peers has
        # nowhere to send a message. Reaching an agent that needs no credential
        # still means listing its origin with `credential` empty, which
        # `a2a_client.peers.auth_headers` sends no header for -- so nothing
        # becomes unreachable, it only has to be declared.
        if self.a2a_config.peers:
            self.tools.register(A2aTool(self.a2a_config))
        # Sub-agent DAG orchestration. Registered unconditionally now that
        # the agent table always holds the package's built-in rows: the tool used
        # to be gated on an enabled third-party entry existing, because without one
        # its roster was empty and a node had nothing to name. A graph over
        # research-raven and code-raven is a graph, so that gate would now be
        # withholding the tool from every default install.
        from raven.agent.subagent.dag_tool import SubAgentDagTool

        self.tools.register(
            SubAgentDagTool(
                workspace=self.subagents.workspace,
                registry=self.subagents.registry,
                guide_skill_id=self._dag_guide_skill_id(),
                session_dir=self.sessions.session_dir,
                is_paused=lambda: self.subagents.paused,
                state_for=self.subagents.instance_state,
                memory_for=self.subagents.memory_scope,
                mode_for=self.subagents.resolve_mode,
                model_for=self.subagents.session_model_for,
                effort_for=self.subagents.session_effort_for,
                gate=self.subagents.dispatch_gate,
                announce=self.subagents.announce_dag_result,
                announce_exception=self.subagents.announce_dag_exception,
                adopt=self.subagents.adopt_background_run,
                charge=self.subagents.charge_dag_run,
                # The manager's own sandbox builder, so a graph node's exec
                # runs in the VM its tools.sandbox configured rather than on
                # the host -- the silent fallback the sandbox docs disown.
                sandbox_for=self.subagents.sandbox_executor_for,
                ask=self._confirm_graph,
                control_reachable=self.dag_control_reachable,
                control_advert=self.dag_control_advert,
                provider_for=self._verdict_provider,
                binding_for=self._turn_binding,
                spawn_offered=lambda: self.tools.offers_by_name("spawn"),
                verdict_config=self.subagent_dag_config,
            )
        )
        # The host's own record of the conversation's matters. Host-only for the
        # same reason as everything above: a subagent's turns are its caller's
        # business, and a board kept there would record the caller's matters twice.
        if self._task_board_config.enabled:
            from raven.agent.task_board.service import TaskBoardService
            from raven.agent.task_board.tool import TaskBoardTool

            self.task_board = TaskBoardService(self.sessions)
            self.tools.register(TaskBoardTool(self.task_board, loop=self))
        # The two close the array, in this order, so the roster spawn's
        # description ends with is the last thing the array says and the graph
        # tool's pointer to it lands on the tool read right after.
        self.tools.list_last("run_subagent_dag", "spawn")
        # The graph tool's own acceptance text is the only advertisement these
        # three get: hidden from the schema so the per-turn tool list carries
        # nothing a conversation that never starts a DAG has any use for, they
        # stay reachable through the registry (and tool_call where it exists).
        from raven.agent.subagent.dag_control_tools import CancelDagTool, DagStatusTool, ResolveDagNodeTool

        self.tools.register(CancelDagTool(loop=self))
        self.tools.register(DagStatusTool(loop=self))
        self.tools.register(ResolveDagNodeTool(loop=self))
        self.tools.hide_from_schema("cancel_dag", "dag_status", "resolve_dag_node")

    def _bind_plugin_runtime(self) -> None:
        """Grant the late-bound handles to every plugin tool that declared.

        Runs once from the constructor, after the loop has finished assembling
        itself: the factories ran before this loop existed, and the handles
        carry organs (the playbook funnel) built after even the tool registry
        is populated, so this is the first moment every grant exists. A tool
        that raises :class:`BindDeclinedError` is unregistered quietly -- the grant
        it needs is off in this loop, which is configuration, not a bug. Any
        other exception unregisters loudly rather than leaving a tool
        half-bound. A tool that was withheld or shadowed after registering is
        skipped: binding what the table no longer serves grants power to a
        dead reference.

        The registry's cast gates bind through the same minting path, with
        the opposite failure rule: a gate whose bind raises fails the whole
        assembly (the candidate generation is discarded, or first boot
        refuses to serve). Quietly taking a gate off the table -- the tools'
        unregister path, declines included -- would turn a binding bug into
        a silent permission grant; a gate's sanctioned opt-out is its
        factory returning None, before anything was cast.
        """
        from raven.plugins.context import BindDeclinedError

        for tool in self.plugin_tools:
            bind = getattr(tool, "bind_runtime", None)
            if not callable(bind):
                continue
            if self.tools.get(tool.name) is not tool:
                continue
            namespace = getattr(tool, "contributed_by", None)
            handles = self.mint_runtime_handles(str(namespace) if namespace else None)
            try:
                bind(handles)
            except BindDeclinedError as decline:
                logger.info("plugin tool {} declined its runtime binding ({}); unregistering it", tool.name, decline)
                self.tools.unregister(tool.name)
            except Exception:
                logger.exception("plugin tool {} raised in bind_runtime; unregistering it", tool.name)
                self.tools.unregister(tool.name)
        for gate in self.tools.tool_gates:
            bind = getattr(gate, "bind_runtime", None)
            if not callable(bind):
                continue
            namespace = getattr(gate, "contributed_by", None)
            bind(self.mint_runtime_handles(str(namespace) if namespace else None))

    def mint_runtime_handles(self, namespace: "str | None"):
        """The late-bound grants, minted per holder.

        One minting path for tools, services and gates alike. The wake grant
        is namespaced to the contributing plugin (paper: contracts/scheduling.py):
        a holder's keys can neither see nor move another plugin's wakes, nor
        any plain reminder. None where the host runs no scheduler, or the
        holder carries no stamped identity to namespace by.
        """
        from raven.plugins.context import RuntimeHandles

        wake = None
        if self.cron_service is not None and namespace:
            from raven.proactive_engine.schedulers.cron.grant import NamespacedWakeScheduler

            wake = NamespacedWakeScheduler(self.cron_service, namespace)
        return RuntimeHandles(
            session_dir=self.sessions.session_dir,
            subagent_registry=self.subagents.registry,
            subagents_paused=lambda: self.subagents.paused,
            playbook_runtime=self._playbooks,
            wake_scheduler=wake,
            direct_ask=self._direct_ask,
            rebind_workdir=self._rebind_workdir,
            usage_recorder=self._record_image_usage,
        )

    async def _direct_ask(
        self,
        prompt: str,
        choices: "list[str] | None",
        conversation_id: str,
        timeout_s: "float | None" = None,
    ) -> "str | None":
        """The loop's own user-question face, lent as ``RuntimeHandles.direct_ask``.

        Resolved per call, never at mint: the ask broker is injected by the
        transport after this loop is built, so a mint-time snapshot would
        forever answer None on those hosts. Answers None when no asking
        transport is bound, exactly as the graph-confirm flow experiences it,
        and also when the conversation is still busy with another question at
        the deadline: a plugin gate asks from inside a tool call, and a
        foreground graph runs several of those at once, so two gates (or a
        gate and a relayed sub-agent) would otherwise evict each other from
        the broker's single pending slot. None sends the gate down its
        no-channel path, which is fail-closed. One deadline covers the wait
        and the question, as ``execute`` and the confirm gate do.
        """
        tool = self.tools.get("ask_user")
        if not isinstance(tool, AskUserTool):
            return None
        budget = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        async with held_question(conversation_id, budget) as held:
            if not held:
                return None
            return await tool.ask_direct(prompt, choices, conversation_id, max(0.0, deadline - loop.time()))

    def _rebind_workdir(self, session_key: str, target: "str | Path") -> Path:
        """Repoint one session's working directory, now and from now on
        (``RuntimeHandles.rebind_workdir``).

        Persists the override into the session's metadata -- the durable
        truth ``WorkdirResolver`` reads back (explicit > persisted >
        default) -- then repoints the live binding so the very next tool
        call in the current task resolves the new root. The turn-end reset
        of ``workdir.bind`` still runs; the next turn reads the persisted
        value. Targets pass ``workdir.validate_override`` against this
        loop's agent home.
        """
        resolved = workdir.validate_override(target, self.workspace)
        session = self.sessions.get_or_create(session_key)
        session.metadata["workdir"] = str(resolved)
        self.sessions.save(session)
        workdir.repoint(resolved)
        return resolved

    # Contributed background services and session observers, attached by the
    # assembly root (inert) and run only by a resident host; a one-shot turn
    # never starts the services, so it never attaches the observers either.
    plugin_services: tuple = ()
    session_observers: tuple = ()
    _plugin_services_started = False
    _started_services: tuple = ()

    @property
    def running_plugin_services(self) -> tuple:
        """The contributed services started and not yet stopped: work this host runs with nobody asking."""
        return self._started_services

    async def start_plugin_services(self) -> None:
        """Start every contributed background service, once.

        Loud on error per the paper (contracts/services.py): a service that
        fails to start is reported and left out of the started set -- never
        retried silently, because a watcher that is secretly dead is the lie
        a watching product exists to prevent.

        The contributed session observers ride this same lifecycle: attached
        to the session store here, detached in :meth:`stop_plugin_services`
        (paper: contracts/session_events.py). The host's own task board joins
        them, so a deleted conversation's board goes with it.
        """
        if self._plugin_services_started:
            return
        self._plugin_services_started = True
        started = []
        for service in self.plugin_services:
            namespace = getattr(service, "contributed_by", None)
            handles = self.mint_runtime_handles(str(namespace) if namespace else None)
            try:
                await service.start(handles)
            except Exception:
                logger.exception(
                    "plugin service {} failed to start; it stays stopped",
                    getattr(service, "contributed_by", service),
                )
                continue
            started.append(service)
        self._started_services = tuple(started)
        board = (self.task_board,) if self.task_board is not None else ()
        self.sessions.set_delete_observers((*self.session_observers, *board))

    async def stop_plugin_services(self) -> None:
        """Stop started services, newest first; idempotent, loud on error.

        Detaches the session observers first, so a retiring generation stops
        hearing deletions before its services wind down.
        """
        self.sessions.set_delete_observers(())
        services, self._started_services = self._started_services, ()
        self._plugin_services_started = False
        for service in reversed(services):
            try:
                await service.stop()
            except Exception:
                logger.exception(
                    "plugin service {} raised while stopping; continuing shutdown",
                    getattr(service, "contributed_by", service),
                )

    def _build_playbooks(self) -> None:
        """Build the playbook runtime for the bundled entry tools to bind.

        Runs after ``_register_default_tools`` because the runtime's generator
        needs a real inventory of what this install offers, and that is
        ``self._mcp_servers`` plus a *populated* tool registry. Registering the
        two playbook tools from inside ``_register_default_tools`` was what made
        the two orderings look compatible: the runtime had to exist before
        registration, yet could not be built until after it.

        The two entry tools are plugin cargo now (``raven/plugins/bundled/playbook``):
        they register with the other plugin tools and receive this runtime in
        ``_bind_plugin_runtime``, so the loop assembles the funnel and the
        plugin serves it.

        It read ``self._mcp_servers`` several assignments before that field
        existed, so ``enabled: true`` raised ``AttributeError``, the guard below
        swallowed it, and the whole feature was off on every install that asked
        for it -- with one warning line as the only trace. No test caught it
        because none of them constructed an ``AgentLoop`` with a playbook
        config; the loop-level playbook tests now do.

        A failure to build still leaves the feature off rather than breaking the
        loop: a library that cannot load is not a reason for the agent to refuse
        every turn.
        """
        cfg = self._playbook_config
        # A sub-agent is refused the funnel rather than the two tools directly:
        # ``load_playbook`` on a ``dag`` playbook dispatches from inside the call,
        # so it is a third route to a graph -- and leaving the funnel unbuilt is
        # the path both tools already unregister themselves down.
        if cfg is None or not cfg.enabled or is_subagent_process():
            return
        try:
            self._playbooks = self._build_playbook_runtime(cfg)
        except Exception:
            logger.opt(exception=True).warning("Playbook runtime failed to build; feature disabled")
            return

    def _build_playbook_runtime(self, cfg: "PlaybookConfig"):
        """Assemble the playbook funnel from pieces this loop already owns.

        The executor gets a private SubAgentDagTool instance (never
        registered, so no LLM-facing surface changes) wired to the same
        manager hooks as the registered one: one dispatch gate, one quota,
        one announce path.
        """
        from raven.agent.subagent.dag_tool import SubAgentDagTool
        from raven.playbook import PlaybookExecutor, PlaybookRuntime, PlaybookStore

        # The user layer of the two-layer library; the builtin layer is the
        # store's own default. ``subagents.workspace`` is agent home here, so
        # playbooks sit beside memory and skills rather than following the
        # per-turn working directory.
        user_layer = Path(cfg.dir) if cfg.dir else (self.subagents.workspace / "playbooks")
        dag_tool = SubAgentDagTool(
            workspace=self.subagents.workspace,
            registry=self.subagents.registry,
            guide_skill_id=None,
            session_dir=self.sessions.session_dir,
            is_paused=lambda: self.subagents.paused,
            # A playbook step may now name an `instance`, so it needs the same
            # message-list derivation a spawn and a registered DAG node get.
            # Missing here before because a playbook step could not carry a handle
            # at all -- the executor dropped the field on the way to dispatch.
            state_for=self.subagents.instance_state,
            mode_for=self.subagents.resolve_mode,
            model_for=self.subagents.session_model_for,
            effort_for=self.subagents.session_effort_for,
            gate=self.subagents.dispatch_gate,
            announce=self.subagents.announce_dag_result,
            announce_exception=self.subagents.announce_dag_exception,
            adopt=self.subagents.adopt_background_run,
            charge=self.subagents.charge_dag_run,
            # Same manager hooks as the registered tool: the sandbox builder
            # above, for the same reason -- a stored graph's nodes exec too.
            sandbox_for=self.subagents.sandbox_executor_for,
            ask=self._confirm_graph,
            # Same manager hooks as the registered tool (see the docstring above):
            # a playbook step is an ordinary DAG node, so it is judged on the same
            # terms once judgement is wired in.
            provider_for=self._verdict_provider,
            binding_for=self._turn_binding,
            # PlaybookRuntime opens an explicit delegate scope for every stored
            # graph: its durable Harness table for a v2 composite, or None for a
            # legacy/workflow-only graph. Reading the scope here lets composites
            # resolve aliases without exposing them to unrelated stored DAGs.
            control_reachable=self.dag_control_reachable,
            control_advert=self.dag_control_advert,
            verdict_config=self.subagent_dag_config,
        )
        from raven.playbook import (
            PlaybookGenerator,
            RouterSizes,
            agent_profiles_from_registry,
            live_inventory,
        )

        executor = PlaybookExecutor(
            dag_tool=dag_tool,
            provider=self.provider,
            compose_model=cfg.model,
            # A multi-round stint works a project for hours and takes a checkout
            # of it. The project is the conversation's own working directory,
            # not this process's: a gateway is started from wherever it happens
            # to be started from, and that is nobody's repository.
            workspace_for=self._stint_workspace,
        )
        # Both Playbook model calls use the live, capability-aware agent view.
        executor.set_agent_profiles(lambda: agent_profiles_from_registry(self.subagents.registry))
        store = PlaybookStore(user_layer)
        # Rides on the runtime below: creation shares the library and generator
        # with the funnel, so both entry tools write the same place.
        generator = PlaybookGenerator(
            self.provider,
            None,
            lambda: agent_profiles_from_registry(self.subagents.registry),
            # A real inventory: with the empty one this used to pass,
            # ``check_assets`` judged every skill and mcp server a draft named to be
            # unknown, so a good draft came back annotated as missing everything.
            live_inventory(self._mcp_servers, self.tools.names()),
            model=cfg.model,
        )

        return PlaybookRuntime(
            store=store,
            executor=executor,
            generator=generator,
            # The file as it stands, and deliberately not ``cfg.disabled``
            # beside it: that is a snapshot of the same key, and a runtime
            # holding both can only ever add to the deny list -- ``disable``
            # would apply on the next call while ``enable`` waited for the next
            # process. ``_withheld_tool_names`` above avoids this the same way.
            disabled_source=self._disabled_playbook_names,
            # The live table, asked rather than copied, and the view its own
            # docstring reserves for validating: a row that is switched off is
            # still a resolvable reference, and ``apply_agents`` rebuilds this
            # registry in place, so a copy taken here would go stale against the
            # very dispatch it is meant to agree with.
            known_agents=self.subagents.registry.all_names,
            router=RouterSizes(top_k=cfg.router.top_k, over_fetch_factor=cfg.router.over_fetch_factor),
        )

    def _disabled_playbook_names(self) -> frozenset[str]:
        """The playbook deny list as it stands on disk, for the runtime to ask."""
        from raven.config.live import disabled_playbook_names

        return disabled_playbook_names(self._live_config)

    def _turn_binding(self) -> tuple[Any, str]:
        """The running turn's provider and model, resolved per dispatch.

        Same reason as `_verdict_provider`: both are properties over the turn's
        binding, and reading them while the loop was being built would freeze
        the default onto the graph tool. A node dispatched to a pooled ACP
        worker is what needs them -- the worker keeps the binding it was
        launched with, so a session that switched model has to say so on every
        dispatch, through the graph as much as through `spawn`.
        """
        return self.provider, self.model

    def _permission_judge_provider(self) -> Any:
        """The provider the permission reviewer should call, resolved per review.

        Same resolution as ``_verdict_provider`` below, for the same two
        reasons -- and the pin is read live, so pointing ``permissions.judgeModel``
        at another model takes effect on the next review rather than the next
        restart.
        """
        from raven.config.live import permissions_config

        pinned = permissions_config(self._live_config).judge_model
        if pinned and self._provider_pool is not None:
            binding = self._provider_pool.bind_pin(pinned)
            if binding is not None:
                return binding.provider
        return self.provider

    def _verdict_provider(self) -> Any:
        """The provider the node judge should call, resolved per dispatch.

        `provider` is a property over the running turn's binding, so reading it
        while this loop was still being built froze the default one onto the
        graph tool: a session that switched model never reached the judge. And a
        pinned `verdictModel` has to travel with the provider holding its
        credential -- a bare model id sent through another vendor's provider
        fails, and the judge fails open, so verdicts would go quietly off.
        """
        from raven.agent.subagent.dag_verdict import judge_provider

        return judge_provider(self.provider, self._provider_pool, self.subagent_dag_config.verdict_model)

    async def _confirm_graph(self, conversation_id: str, question: str) -> bool:
        """The graph-level ``confirm`` gate's route to a human.

        A method rather than a closure inside the playbook wiring, because both
        DAG tool instances need it and that wiring only runs when
        ``playbooks.enabled``. Built as a closure there first, it left the
        *registered* ``run_subagent_dag`` -- the one the model calls -- with no
        asker at all: a model-composed graph asking for approval got a log line,
        and was then told a human had approved something no human saw.

        Resolved per call rather than captured: this is handed to a tool built in
        ``_register_default_tools``, and the transport injects the ask broker
        later still.

        No broker (a channel with no question path, a test) returns True and the
        graph runs. Not every surface can put a question to a human, and letting
        the absence of one disable the feature outright is the worse failure;
        ``SubAgentDagTool._confirmed`` logs it when it happens.
        """
        tool = self.tools.get("ask_user")
        if not isinstance(tool, AskUserTool):
            return True
        # Under the conversation's question lock, like every other route to the
        # broker: ``ask_direct`` itself takes none (the relayed sub-agent routes
        # call it already holding it), and an unserialized question here would
        # evict a sub-agent's pending one from the slot. A conversation still
        # busy at the deadline is a "Not now": the graph does not run unseen.
        # One deadline for the wait and the question together, as ``execute``
        # does: a lock had late must not buy the question a fresh full budget.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DEFAULT_TIMEOUT_S
        async with held_question(conversation_id, DEFAULT_TIMEOUT_S) as held:
            if not held:
                return False
            answer = await tool.ask_direct(
                question, ["Run it", "Not now"], conversation_id, max(0.0, deadline - loop.time())
            )
        if answer is None:
            return True
        return answer.strip().lower() in {"run it", "run", "yes", "y", "ok", "go", "sure"}

    def _dag_guide_skill_id(self) -> str | None:
        """The orchestration guide's id for the DAG tool description, or None.

        The tool tells the agent to load this skill before its first call, so
        the pointer must be real: a deployment that removed the builtin skills
        would otherwise burn a turn on a read_skill that cannot resolve. When
        the registry itself is unreachable, keep the pointer — the shipped
        skill is there by default, and losing the instruction is the worse
        failure of the two.
        """
        from raven.agent.subagent.dag_tool import GUIDE_SKILL_ID

        return self._shipped_guide(GUIDE_SKILL_ID)

    def _shipped_guide(self, skill_id: str) -> str | None:
        """``skill_id`` when the skill registry can resolve it, else None.

        The same rule for every tool that points at a companion skill; see
        :meth:`_dag_guide_skill_id` for why an unreachable registry keeps it.
        """
        registry = getattr(getattr(self.context, "skills", None), "registry", None)
        if registry is None:
            return skill_id
        try:
            found = registry.get(skill_id.split("/", 1)[1]) is not None
        except Exception:  # noqa: BLE001 - a registry hiccup must not unregister the guide
            return skill_id
        return skill_id if found else None

    @staticmethod
    def _build_skill_hub_client(
        workspace: Path,
        skill_forge_router_config: "SkillForgeRouterConfig | None",
    ) -> "SkillHubClient | None":
        """Construct the shared Skill Hub client, or ``None`` when no Hub
        endpoint is configured. Downloads land under ``<workspace>/skills/hub``
        so a use_skill'd bundle is discoverable by the on-disk skill registry.

        Built while the switch is off too: the tools ask the live config
        before each Hub call, so turning the source back on does not need the
        loop rebuilt. Only the endpoint is fixed at construction.
        """
        hub_cfg = getattr(skill_forge_router_config, "hub", None)
        if hub_cfg is None or not getattr(hub_cfg, "endpoint", None):
            return None
        from raven.skill_hub import SkillHubClient

        return SkillHubClient(
            hub_cfg.endpoint,
            api_key=hub_cfg.api_key,
            timeout_s=hub_cfg.timeout_s,
            source=hub_cfg.source,
            cache_dir=workspace / "skills" / "hub",
        )

    def session_workdir(self, session_key: str) -> Path:
        """The directory this session's turn works in, ready to be used.

        The mount check runs before the directory is created, so a refusal
        leaves nothing behind on disk.
        """
        resolved = self.peek_session_workdir(session_key)
        self.check_workdir_mounted(resolved, session_key)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    @property
    def deliverables(self) -> "DeliverableStore | None":
        """The registry `deliver_files` writes into, for callers that only read
        it -- the RPC surface that answers what a conversation handed over."""
        return self._deliverables

    def _stint_workspace(self, session_key: str | None) -> Path:
        """The project a stint started in this session works.

        Through the same resolver a tool call goes through, and with no key it
        still goes through it: an operator who launched with ``-w`` named one
        directory for this process, and a stint is the last thing that should
        work a different one.
        """
        return self.peek_session_workdir(session_key or "")

    def peek_session_workdir(self, session_key: str) -> Path:
        """Where this session would work, with no side effect and no refusal.

        The read-only form, for a caller that only reports the path. Without a
        resolver the loop keeps its pre-split behaviour: every session shares
        ``self.workspace``.
        """
        if self._workdir_resolver is None:
            return self.workspace
        return self._workdir_resolver.resolve(session_key, create=False)

    def check_workdir_mounted(self, path: Path, session_key: str | None = None) -> None:
        """Refuse a working directory a sandboxed run could not see.

        A VM's volumes are fixed when the box is created, so a directory
        outside the mount cannot be served by adding one later.
        """
        if self._workdir_resolver is None or not self._executor.is_sandboxed:
            return
        root = self._workdir_resolver.mount_root()
        if workdir.is_within(path, root):
            return
        subject = f"session {session_key} is pinned to {path}" if session_key else str(path)
        raise ValueError(
            f"{subject}, which is outside the sandbox mount {root}; "
            "clear the override or restart with a wider workspace root"
        )

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        session_key: str | None = None,
        surface: str | None = None,
    ) -> None:
        """Update context for all tools that need routing info.

        ``surface`` goes to the tools that start runs, whose results come back as
        turns of their own: the front end the turn declared is where those are read.
        """
        # Before the per-tool contexts because the schema is assembled from this
        # same turn: a channel-bound tool is withheld by the registry, not by
        # its own refusal (see ToolRegistry.set_channel).
        self.tools.set_channel(channel)
        for name in (
            "message",
            "spawn",
            "cron",
            "run_subagent_dag",
            "deliver_files",
            "dag_status",
            "cancel_dag",
            "resolve_dag_node",
            "context_retrieve",
            "task_board",
        ):
            if tool := self.tools.get(name):
                if not hasattr(tool, "set_context"):
                    continue
                if name == "message":
                    tool.set_context(channel, chat_id, message_id)
                elif name in ("spawn", "run_subagent_dag"):
                    tool.set_context(channel, chat_id, session_key or f"{channel}:{chat_id}", surface=surface)
                elif name in (
                    "deliver_files",
                    "dag_status",
                    "cancel_dag",
                    "resolve_dag_node",
                    "context_retrieve",
                    "task_board",
                ):
                    tool.set_context(channel, chat_id, session_key or f"{channel}:{chat_id}")
                else:
                    tool.set_context(channel, chat_id)
        # Not in the name list above: the playbook executor's DAG tool is a
        # private unregistered instance, reachable only through the runtime.
        # Recorded here -- the one place every origin passes -- because a
        # CRON/SENTINEL turn can call load_playbook too, and its announce must go
        # to that turn's own address rather than the last human conversation's.
        if self._playbooks is not None:
            self._playbooks.set_context(
                channel=channel, chat_id=chat_id, session_key=session_key or f"{channel}:{chat_id}", surface=surface
            )

    def dag_tools(self) -> list[Any]:
        """Every live graph tool: the registered one, plus the playbook engine's.

        Two instances exist by design -- one on the model's tool table, one
        private to the playbook executor, because a ``mode: dag`` playbook is
        dispatched by the engine rather than by the model. They share the agent
        table, the dispatch gate, the quota and the announce path, and everything
        a *consumer* asks about a run has to be shared the same way: whether it
        is live, cancelling it, where its progress goes. Reaching only for the
        registered instance answers "not happening" to all three for a run a
        playbook started -- no progress events reach the page, so the graph never
        appears in the conversation at all; the cancel button reports False; and
        the instance rows read the handle as finished.

        Which of the two dispatched a run is not a distinction any consumer
        should be able to observe, so the list is what they are given.
        """
        tools = []
        if (registered := self.tools.get("run_subagent_dag")) is not None:
            tools.append(registered)
        if self._playbooks is not None and (private := self._playbooks.dag_tool) is not None:
            tools.append(private)
        return tools

    def active_dag_run_ids(self) -> set[str]:
        """Run ids in flight across every graph tool instance."""
        live: set[str] = set()
        for tool in self.dag_tools():
            try:
                live.update(tool.active_run_ids())
            except Exception:  # noqa: BLE001 - liveness is advisory, never fatal
                continue
        return live

    def cancel_dag_run(self, run_id: str) -> bool:
        """Stop one in-flight run, whichever instance owns it."""
        return any(tool.request_cancel(run_id) for tool in self.dag_tools())

    def resolve_dag_node(self, run_id: str, node_id: str, decision: str, message: str | None, plan: Any = None) -> bool:
        """Answer one suspended node, whichever instance owns its run."""
        return any(tool.resolve_node(run_id, node_id, decision, message, plan) for tool in self.dag_tools())

    def dag_control_reachable(self) -> bool:
        """Whether the schema-hidden dag control tools have a call path.

        The graph tool advertises ``dag_status`` / ``cancel_dag`` /
        ``resolve_dag_node`` in its acceptance text, and this is the gate that
        keeps the advertisement
        honest: it answers the same question ToolSearchStrategy answers when it
        assembles the per-turn tool list, through the controller's single
        predicate rather than a second copy of the fold condition.
        """
        if self.tool_search_controller is None:
            return False
        return self.tool_search_controller.tool_call_available()

    def dag_control_advert(self, name: str) -> str | None:
        """One schema-hidden dag control tool's definition, as advertisement text.

        The graph tool's result text and the exception report are the only route
        these three have to the model, so they carry the definition itself rather
        than a retelling of it -- generated here, from the registry, so it cannot
        drift from the tool the way three hand-written copies did.
        """
        from raven.agent.subagent.dag_control_advert import render

        return render(self.tools.hidden_definition(name))

    def set_dag_progress_sink(self, sink) -> None:
        """Late-bind the graph tools' progress sink (host wires it to the web
        channel's emitter so a run's events reach the web UI).

        Every instance, not just the registered one -- see :meth:`dag_tools`.
        """
        self._dag_progress_sink = sink
        for tool in self.dag_tools():
            if hasattr(tool, "set_progress_sink"):
                tool.set_progress_sink(sink)

    def apply_agents(self, configs: list) -> None:
        """Hot-apply new agent config to the live runtime (P4), with no restart.

        One call, one table: ``spawn`` and the DAG tool read the same
        ``AgentRegistry``, so applying to the manager is applying to both. This
        used to refresh two independently-built maps through two setters that each
        skipped a bad entry on its own, which made "the manager has hermes, the DAG
        tool does not" a reachable state.

        No registration branch either -- the DAG tool is registered at startup
        whatever config holds, because the built-in rows are always on the table.
        """
        self._agent_configs = list(configs)
        self.subagents.apply_agents(configs)

```

## raven/agent/tools/context_retrieve.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/tools/context_retrieve.py

SHA-256: `b52d3de52c622059e0ea2f3ec2836f31ea8a0a34195d8dfb03e36b7e657f1083`

```
"""``context_retrieve`` -- the way back from a ref to the text it stands for.

A ``[[ref ...]]`` in the window is a promise: the thing it names is one call
away, verbatim. This tool is that call. Without it a ref is decoration, and the
pruner that mints refs would just be deleting output with a citation attached.

Only the ``session`` store resolves here, and only for the session the turn
belongs to. That second half is the part worth stating: a ref is a string the
model can compose, so a tool that resolved whatever scope it was handed would
read another conversation's log on request. The scope is checked against the
session's current :class:`~raven.contracts.context.SessionRevision`, which also
makes a ref minted before a ``/new`` or an undo a miss rather than a wrong
answer.

What comes back is byte-for-byte what the tool returned, up to the caller's
token budget -- the budget is reported when it binds, so a partial answer is
never mistaken for a whole one. A span written before the log kept results
whole is reported as truncated instead: those bytes were never stored and no
amount of asking will produce them.

Read-only: the session is looked up with ``SessionManager.peek`` and nothing
here writes to it or to its file.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from raven.contracts.context import SessionRevision
from raven.contracts.tool import Tool
from raven.session.refs import SESSION_REF_STORE, belongs_to, is_truncated
from raven.utils.refs import SourceRef
from raven.utils.tokens import estimate_message_tokens

if TYPE_CHECKING:
    from raven.session.manager import SessionManager

_DEFAULT_MAX_TOKENS = 4_000
_MIN_MAX_TOKENS = 256
_MAX_MAX_TOKENS = 32_000
_SNIPPET_HEAD_CHARS = 800
_SNIPPET_TAIL_CHARS = 400
_MAX_REFS = 20
"""Refs answered in one call. A span already carries many messages, and the
budget is shared, so a longer list only splits the same allowance further."""


class ContextRetrieveTool(Tool):
    """Reads back the messages a ref names, from this session's log."""

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        self._session_key: ContextVar[str | None] = ContextVar("context_retrieve_session_key", default=None)

    def set_context(self, channel: str, chat_id: str, session_key: str | None = None) -> None:
        """Bind the turn's session (turn-local, like every other routed tool)."""
        self._session_key.set(session_key or f"{channel}:{chat_id}")

    @property
    def name(self) -> str:
        return "context_retrieve"

    @property
    def description(self) -> str:
        return (
            "Read back the verbatim text behind [[ref ...]] tags in a compacted summary or a "
            "pruned tool result, when the one-line record is not enough. Pass the tags as written, "
            'e.g. refs=["[[ref session/<scope>#118]]"].'
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "The [[ref ...]] tags.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["exact", "snippet"],
                    "description": "exact (default): the text; snippet: each entry's opening and closing.",
                },
                "max_tokens": {
                    "type": "integer",
                    "minimum": _MIN_MAX_TOKENS,
                    "maximum": _MAX_MAX_TOKENS,
                    "description": f"Budget for the reply (default {_DEFAULT_MAX_TOKENS}).",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "exact mode: start this many characters in (a cut reply names next_offset).",
                },
            },
            "required": ["refs"],
        }

    async def execute(self, **kwargs: Any) -> str:
        refs = kwargs.get("refs") or []
        if isinstance(refs, str):
            # A list the model serialised into one string is still the list it
            # meant; measured, a model asked this way did exactly that.
            try:
                decoded = json.loads(refs)
            except ValueError:
                decoded = None
            refs = decoded if isinstance(decoded, list) else [refs]
        if not isinstance(refs, list) or not refs:
            return "Error: refs must be a non-empty list of [[ref ...]] tags."
        mode = kwargs.get("mode") or "exact"
        if mode not in ("exact", "snippet"):
            return f"Error: unknown mode {mode!r}; expected 'exact' or 'snippet'."
        budget = _clamp_budget(kwargs.get("max_tokens"))
        offset = _clamp_offset(kwargs.get("offset"))

        session_key = self._session_key.get()
        if not session_key:
            return "Error: context_retrieve has no session bound; nothing to retrieve from."
        session = self._sessions.peek(session_key)
        if session is None:
            return f"Error: session {session_key!r} has no stored log."
        # Both off the same object, read once: the revision is what says which
        # messages the ordinals of an accepted ref name.
        messages = session.messages
        revision = session.revision

        # Every tag in every string: a model that pastes a line holding two refs
        # asked for both.
        wanted: list[SourceRef | str] = []
        for raw in refs:
            found = SourceRef.find_all(str(raw)) or SourceRef.find_all(_bracketed(str(raw)))
            wanted.extend(found or [str(raw)])
        answered = wanted[:_MAX_REFS]

        sections: list[str] = []
        for item in answered:
            section, spent = self._resolve(item, messages, revision, mode=mode, budget=budget, offset=offset)
            sections.append(section)
            budget -= spent
            if budget <= 0:
                remaining = len(answered) - len(sections)
                if remaining > 0:
                    sections.append(
                        f"[{remaining} more ref(s) not read: the token budget for this call is spent. "
                        "Ask again for them, or raise max_tokens.]"
                    )
                break
        if len(wanted) > _MAX_REFS:
            sections.append(
                f"[{len(wanted) - _MAX_REFS} ref(s) beyond the {_MAX_REFS} this call answers were ignored.]"
            )
        return "\n\n".join(sections)

    # ------------------------------------------------------------------

    def _resolve(
        self,
        item: SourceRef | str,
        messages: Sequence[Mapping[str, Any]],
        revision: SessionRevision,
        *,
        mode: str,
        budget: int,
        offset: int = 0,
    ) -> tuple[str, int]:
        """One ref: its entries and what reading them cost."""
        if isinstance(item, str):
            return (f"[{item!r} is not a ref; expected the [[ref store/scope#n]] form]", 0)
        ref = item
        if ref.store != SESSION_REF_STORE:
            return (f"[{ref.render()}: the {ref.store!r} store is not retrievable here]", 0)
        if not belongs_to(ref, revision):
            log_id, _, generation = ref.scope.partition("@")
            if log_id == revision.log_id and generation.isdigit():
                if int(generation) < revision.generation:
                    return (
                        f"[{ref.render()}: minted before this conversation's log was rewritten (an undo, "
                        "or another copy of the session saving over it); the entries it named may now "
                        "hold other messages, so it is not read back]",
                        0,
                    )
                return (
                    f"[{ref.render()}: names generation {generation} of this conversation's log, which is "
                    f"at generation {revision.generation} here; no stored copy of that generation can "
                    "be read back]",
                    0,
                )
            return (
                f"[{ref.render()}: not part of this conversation's log. A ref from before a /new, "
                "or from another session, names nothing that can be read back]",
                0,
            )
        if not 0 <= ref.lo <= ref.hi < len(messages):
            return (f"[{ref.render()}: out of range; this log holds {len(messages)} message(s)]", 0)

        rendered: list[str] = []
        spent = 0
        for index in range(ref.lo, ref.hi + 1):
            if spent >= budget:
                rendered.append(f"[entries {index}-{ref.hi} not read: the token budget for this call is spent]")
                break
            entry, cost = _render_entry(
                SourceRef(ref.store, ref.scope, index, index),
                messages[index],
                mode=mode,
                budget=budget - spent,
                offset=offset,
            )
            rendered.append(entry)
            spent += cost
        return ("\n\n".join(rendered), spent)


def _clamp_offset(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _clamp_budget(raw: Any) -> int:
    try:
        asked = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_TOKENS
    return max(_MIN_MAX_TOKENS, min(_MAX_MAX_TOKENS, asked))


def _render_entry(
    ref: SourceRef,
    message: Mapping[str, Any],
    *,
    mode: str,
    budget: int,
    offset: int = 0,
) -> tuple[str, int]:
    """One message, headed by what it is and how much of it is here.

    ``offset`` (exact mode) starts the body that many characters in, so an entry
    larger than one call's budget is read in turns; a cut answer names the
    ``next_offset`` to ask for.
    """
    body = _body(message.get("content"))
    header = [ref.render(), f"role={message.get('role', '?')}"]
    if name := message.get("name"):
        header.append(f"name={name}")
    header.append(f"chars={len(body):,}")
    if is_truncated(message.get("content")):
        # Written under the old 16000-character cap. The rest was never stored,
        # so saying so is the only honest answer.
        header.append("truncated=true")

    start = min(offset, len(body)) if mode == "exact" else 0
    if start:
        header.append(f"offset={start:,}")
    rest = body[start:]
    shown = rest if mode == "exact" else _snippet(body)
    whole = f"{' '.join(header)}\n{shown}"
    cost = _tokens(whole)
    if cost <= budget and shown == rest:
        return whole, cost
    # The header that will head a cut body, at its longest, is what the body
    # has to leave room for.
    widest = [*header, f"shown={len(body):,}/{len(body):,} chars", f"next_offset={len(body):,}"]
    shown = _fit(shown, budget - _tokens(f"{' '.join(widest)}\n"))
    labels = [*header, f"shown={len(shown):,}/{len(body):,} chars"]
    if mode == "exact" and start + len(shown) < len(body):
        labels.append(f"next_offset={start + len(shown)}")
    text = f"{' '.join(labels)}\n{shown}"
    return text, _tokens(text)


def _tokens(text: str) -> int:
    return estimate_message_tokens({"role": "tool", "content": text})


def _body(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def _snippet(body: str) -> str:
    if len(body) <= _SNIPPET_HEAD_CHARS + _SNIPPET_TAIL_CHARS:
        return body
    elided = len(body) - _SNIPPET_HEAD_CHARS - _SNIPPET_TAIL_CHARS
    return f"{body[:_SNIPPET_HEAD_CHARS]}\n\n[... {elided:,} chars, ask again with mode=exact ...]\n\n{body[-_SNIPPET_TAIL_CHARS:]}"


def _fit(body: str, budget: int) -> str:
    """``body`` cut to at most ``budget`` tokens.

    The characters-per-token ratio is taken from this body rather than assumed,
    so a Chinese stack trace and an English one are both cut where they
    actually reach the budget; the estimate is then checked, because a cut can
    land the count a little over.
    """
    if budget <= 0 or not body:
        return ""
    cost = _tokens(body)
    if cost <= budget:
        return body
    per_token = max(1.0, len(body) / max(1, cost))
    keep = int(budget * per_token)
    while keep > 0 and (over := _tokens(body[:keep]) - budget) > 0:
        keep -= max(1, int(over * per_token))
    return body[: max(0, keep)]


_BARE_REF = re.compile(r"(?:\bref\s+)?(?P<body>[\w.-]+/[^#\]\s\[]+#\d+(?:-\d+)?)")


def _bracketed(raw: str) -> str:
    """``raw`` with every bare ``store/scope#n`` respelled as a full tag.

    A model that copies the address out of a tag and leaves the brackets behind
    named the entry it wants; refusing it on the spelling cost one measured run
    the answer, because the model took the refusal as "not retrievable".
    """
    return _BARE_REF.sub(lambda m: f"[[ref {m['body']}]]", raw)


__all__ = ["ContextRetrieveTool"]

```

## raven/agent/tools/filesystem.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/tools/filesystem.py

SHA-256: `421f9061f316158202f817788035c69006565f912c3af335b7812945bc0f077b`

```
"""File system tools: read, write, edit, list."""

import asyncio
import difflib
import mimetypes
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from raven.agent import workdir
from raven.agent.tools import remote_files, tree_walk
from raven.contracts.tool import FileChange, Tool, ToolResult
from raven.ops import remote_path
from raven.utils.images import detect_image_mime

_DIFF_MAX_LINES = 400
# The approval preview reads the target before the call runs, on the gate's
# path; a file this large is not read at all (the diff would be dropped by the
# line cap anyway) and the prompt shows the path alone.
_PREVIEW_MAX_BYTES = 256 * 1024

#: How a page of a text file is read: this many bytes at a time, the line
#: breaks counted as the bytes come and only the page's own lines decoded.
#: ``read_text().splitlines()`` held the interpreter for the whole file --
#: 7.5 s on a 1 GiB file (decode 2.7 s, split 4.8 s, measured 2026-10-13),
#: during which nothing else ran: on a Raven node, no heartbeat answered;
#: here, no turn served. Each step is now one chunk's, and the file is never
#: whole in memory.
_PAGE_CHUNK = 4 * 1024 * 1024

# Where ``str.splitlines`` ends a line, as UTF-8 bytes: the two-byte ``\r\n``
# first so it counts once, the one-byte breaks, then NEL, LS and PS.
_LINE_BREAK = re.compile(rb"\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e]|\xc2\x85|\xe2\x80[\xa8\xa9]")
# The breaks other than ``\n``: a chunk with none splits on ``\n`` alone, the
# common case, in one C call. Looked for one by one with ``in``, a search at
# memchr speed; a regular expression over the same bytes took twenty times
# as long (measured 2026-10-13 on a 1 GiB file: 80 ms a chunk against 4).
_ODD_BREAKS = (b"\r", b"\x0b", b"\x0c", b"\x1c", b"\x1d", b"\x1e", b"\xc2\x85", b"\xe2\x80\xa8", b"\xe2\x80\xa9")


def _split_lines(body: bytes) -> list[bytes]:
    """``body`` cut at every break ``str.splitlines`` knows; the last piece is what follows the last break."""
    odd = any(sign in body for sign in _ODD_BREAKS)
    return _LINE_BREAK.split(body) if odd else body.split(b"\n")


def _page_of_lines(handle: Any, start: int, limit: int) -> tuple[list[bytes], int]:
    """Lines ``start`` to ``start + limit`` (0-based) of the bytes ``handle`` reads, and how many lines there are.

    Exactly the lines ``read_text(encoding="utf-8").splitlines()`` would give,
    read a chunk at a time and each chunk looked at once: the bytes of the
    line in progress are kept only while that line may be on the page, so a
    line longer than a chunk costs its length and nothing more, wherever it
    falls. (Held whole and looked at again with every chunk, a file with no
    break in it cost time square in its size: 256 MiB took half a minute.)
    A break may straddle two chunks, so the bytes that could begin one are
    held back until the next chunk says; the tail after the last break is a
    line only when it holds something, as ``splitlines`` has it.
    """
    page: list[bytes] = []
    total = 0
    stop = start + limit
    # The line in progress is line ``total``: its bytes, kept while it may be
    # on the page, and whether it holds anything at all.
    line: list[bytes] = []
    begun = False

    def wanted() -> bool:
        return start <= total < stop

    def feed(body: bytes) -> None:
        nonlocal total, line, begun
        head, *rest = _split_lines(body)
        if head:
            begun = True
            if wanted():
                line.append(head)
        if not rest:
            return
        # The first break ends the line in progress, the pieces between
        # breaks are whole lines, and the last piece begins the next line.
        if wanted():
            page.append(b"".join(line))
        total += 1
        line = []
        *middle, last = rest
        if middle:
            if total < stop and total + len(middle) > start:
                page.extend(middle[max(start - total, 0) : stop - total])
            total += len(middle)
        begun = bool(last)
        if last and wanted():
            line = [last]

    kept = b""
    while chunk := handle.read(_PAGE_CHUNK):
        body = kept + chunk
        hold = 2 if body.endswith(b"\xe2\x80") else (1 if body.endswith((b"\r", b"\xc2", b"\xe2")) else 0)
        kept = body[len(body) - hold :] if hold else b""
        feed(body[: len(body) - hold] if hold else body)
    if kept:
        feed(kept)
    if begun:
        if wanted():
            page.append(b"".join(line))
        total += 1
    return page, total


def _write_text_bytes(content: str) -> bytes:
    """The bytes ``Path.write_text`` would leave on disk for ``content``.

    It writes through a text layer with ``newline=None``, which translates
    every ``\n`` to ``os.linesep`` -- so on a CRLF platform this is not the
    plain UTF-8 encoding of the string.
    """
    return content.replace("\n", os.linesep).encode("utf-8")


def _machine_of(path: str) -> str | None:
    """The registered machine ``path`` is written for, or ``None`` for a path here (or one that names no machine)."""
    try:
        where = remote_path.target(path)
    except remote_path.RemotePathError:
        return None
    return where.machine if where is not None else None


def _is_a_device_spelling(path: str) -> bool:
    """Whether ``path`` names a Windows device itself, colon spelling included (``NUL:``).

    ``looks_remote`` reads the colon in ``NUL:`` as a machine prefix, so on a
    Windows host this lets the reserved-name fence answer before the remote
    path does. Only the head of a remote spelling -- the machine's id or
    ``user@address``, before the separator -- can be the device instead of a
    machine: everything after the separator is a path on that machine, where
    ``nul`` and ``aux`` are ordinary file names and must reach it. On a POSIX
    host this is ``False`` and the remote answer runs as it always did.
    """
    if not _WINDOWS or _url_argument(path):
        return False
    if not remote_path.looks_remote(path):
        return any(_windows_device_stem(part) in _WINDOWS_DEVICE_NAMES for part in _supplied_components(Path(path)))
    raw = path.strip()
    return _windows_device_stem(raw.partition(":")[0]) in _WINDOWS_DEVICE_NAMES


def _unified(before: str, after: str, name: str) -> str | None:
    """Unified diff of one write, or None when there is nothing useful to show.

    A UI cannot reconstruct this later: by the time the call is reported, the
    content it replaced is already overwritten. A rewrite too large to render is
    dropped whole rather than truncated -- half a diff reads as a smaller change
    than the one that happened.
    """
    if before == after:
        return None
    out = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=name,
            tofile=name,
            lineterm="",
        )
    )
    if not out or len(out) > _DIFF_MAX_LINES:
        return None
    return "\n".join(out)


# A DOS device name opens the device, not a file: ``nul`` maps to NUL in every
# directory on every Windows version (CPython issue 95486). ``Path.resolve()``
# cannot catch one: on Windows that is ``ntpath.realpath``, which maps only a
# path that is exactly ``nul`` to ``\\.\NUL`` and hands ``<workspace>\nul``
# back unchanged, so the allowed-dir check then accepts it. Windows' naming
# rules hold for every path component, not only the last, and reach further
# than the bare devices: a device name with or without a first extension
# (``subdir/nul.md``), the superscript spellings (``COM¹.txt``, ``LPT²``), a
# trailing dot or space (the bytes land in ``notes.md`` while the receipt
# names ``notes.md.``), and a colon (``notes.md:hidden`` is an NTFS stream no
# directory listing shows; ``NUL:`` is the device itself). The set and the
# per-component order mirror CPython 3.13's ``ntpath.isreserved``, which
# Python 3.12 cannot call: Microsoft's naming rules list COM/LPT 1-9 only, so
# ``com0.txt`` is an ordinary file and stays one.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
    | {f"COM{s}" for s in "¹²³"}
    | {f"LPT{s}" for s in "¹²³"}
)


def _is_reserved_windows_component(part: str) -> bool:
    """Whether one path component is a reserved Windows name in one of its spellings."""
    if part in (".", ".."):
        return False
    if part[-1:] in (".", " "):
        return True
    if ":" in part:
        return True
    return part.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES


def _supplied_components(p: Path) -> tuple[str, ...]:
    """The path components the caller wrote; the anchor (drive and root) is skipped.

    The anchor names an operator-installed location, while Windows applies its
    naming rules to everything the caller supplies, directory components
    included.
    """
    return p.parts[1:] if (p.drive or p.root) else p.parts


def _windows_device_stem(part: str) -> str:
    """The device a component could name: its stem, before any stream colon or first extension."""
    return part.partition(":")[0].partition(".")[0].rstrip(" ").upper()


def _has_reserved_windows_component(p: Path) -> bool:
    """Whether any component the caller supplied is a reserved Windows name."""
    return any(_is_reserved_windows_component(part) for part in _supplied_components(p))


def _url_argument(path: str) -> bool:
    """Whether ``path`` is a URL rather than a path on this machine.

    ``_URL_SCHEME_RE`` accepts a one-letter scheme, and Windows reads
    ``C://work/nul`` as the drive path ``C:\\work\\nul`` -- a drive letter is a
    local path, not a scheme, so the exemption needs two letters.
    """
    match = _URL_SCHEME_RE.match(path.strip())
    return match is not None and len(match.group("scheme")) > 1


# Read once: the platform of a process never changes, and keying the fence on
# the module global keeps it testable off Windows (where ``os.name = "nt"``
# cannot be patched without breaking ``Path`` itself).
_WINDOWS = os.name == "nt"


def resolve_path(
    path: str,
    workspace: Path | None = None,
    allowed_dirs: tuple[Path, ...] = (),
) -> Path:
    """Resolve path against workspace (if relative) and enforce the allowed roots."""
    # The tools echo the path into their result lines, and readers take a file back
    # out of those lines; a line break in a path would let it forge another one.
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in str(path)):
        raise PermissionError(f"Path {path!r} contains a line break or control character")
    p = Path(path).expanduser()
    # A URL argument is not a path on this machine, so its scheme colon is not
    # a stream spelling; the tool's not-found answer is the one that names the
    # tool which can actually serve it, and this fence must not answer first.
    if _WINDOWS and not _url_argument(path) and _has_reserved_windows_component(p):
        raise PermissionError(f"Path {path!r} is a reserved name on Windows; choose a different name")
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dirs:
        for allowed in allowed_dirs:
            try:
                resolved.relative_to(Path(allowed).resolve())
                return resolved
            except ValueError:
                continue
        roots = ", ".join(str(d) for d in allowed_dirs)
        raise PermissionError(f"Path {path} is outside allowed directories {roots}")
    return resolved


def _with_current_root(allowed_dirs: tuple[Path, ...], bound: Path | None) -> tuple[Path, ...]:
    """Add the turn's *live* working-directory binding to the fence, when the fence is on.

    Tools are constructed once for the loop's whole lifetime, before any turn's
    working directory exists, so ``allowed_dirs`` can only ever carry the static
    roots (agent home). The per-turn root reaches the fence here, at resolve
    time, the same way ``ExecTool`` folds its per-call ``cwd`` into its roots.

    ``bound`` must be the raw result of ``workdir.current()`` (or ``None`` for
    a tool that does not follow the ambient binding at all) -- never a value
    that already fell back to the tool's own ``workspace``. Folding in that
    fallback would silently widen the fence to a root the operator never put
    in ``allowed_dirs``. An empty ``allowed_dirs`` must stay empty regardless
    -- that is the "fence disabled" signal ``resolve_path`` checks for.
    """
    if not allowed_dirs or bound is None:
        return allowed_dirs
    return (bound, *allowed_dirs)


_URL_SCHEME_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://")
_HOST_LIKE_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/")


def _elsewhere(path: str) -> str:
    """The registered machines, for an absolute path this computer does not have."""
    from raven.agent.tools.machine_exec import missing_here_note

    return missing_here_note([path.strip()])


def _missing(path: str) -> str:
    """The refusal for a path that is not there, naming the tool that can reach it.

    A URL resolves to a path that cannot exist, and "File not found" describes the
    wrong problem: the file is not missing, the argument belongs to another tool.
    Reported as a missing file it reads as a misspelling, so the caller tries the
    same URL again with the workspace prefixed, percent-decoded, a directory up --
    none of which can work, and the search that produced the URL stalls there.

    Only the message changes: this answers inside the branch that has already found
    nothing on disk, so a path that does resolve never reaches it.
    """
    raw = path.strip()
    if match := _URL_SCHEME_RE.match(raw):
        if match.group("scheme").lower() == "file":
            return f"Error: {path} is a file:// URL; read_file takes a plain path -- try {raw[7:] or '/'}"
        return (
            f"Error: {path} is a URL, not a path on this machine. read_file only reads the "
            "filesystem; fetch it with web_fetch, which returns the page as text."
        )
    if _HOST_LIKE_RE.match(raw):
        return (
            f"Error: File not found: {path} -- which reads as a URL rather than a path. "
            "If it is one, fetch it with web_fetch; read_file only reads the filesystem."
        )
    return f"Error: File not found: {path}"


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dirs: tuple[Path, ...] = (),
        *,
        follow_binding: bool = True,
    ):
        self._workspace = workspace
        self._allowed_dirs = allowed_dirs
        # A sub-agent run is a background asyncio task that can outlive the turn
        # that spawned it, since SubagentManager.spawn captures the workspace at
        # spawn time; its tools must fence on the directory captured for that
        # run, not on whatever the ambient ContextVar happens to hold when the
        # task finally executes. The main loop's tools keep following the live
        # binding as normal.
        self._follow_binding = follow_binding

    def _resolve(self, path: str) -> Path:
        bound = workdir.current() if self._follow_binding else None
        current_root = bound or self._workspace
        return resolve_path(path, current_root, _with_current_root(self._allowed_dirs, bound))


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Text files return numbered lines — use offset and limit to "
            "paginate through large ones. Image files (PNG, JPEG, GIF, WebP, and other common formats) "
            "return the picture itself, downscaled if needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed, default 1)",
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default 2000)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    # Sniffed from the first bytes, not the extension — a .txt that is really a
    # PNG must not be decoded as UTF-8, and vice versa.
    _MAGIC_SNIFF_BYTES = 32

    def _read_image(self, fp: Path, mime: str) -> ToolResult:
        """Image branch: preprocess, then hand back text *and* an image block.

        ``model_text`` is deliberately self-sufficient. Providers that cannot put
        an image in a tool result never see ``blocks``, and the same text is what
        lands in session history, so it carries the metadata and the path rather
        than pointing at a picture that may not be there.
        """
        from raven.agent.tools import media

        payload, out_mime, meta = media.prepare_image(fp.read_bytes(), mime)
        summary = media.describe_image(fp, meta)
        return ToolResult(
            model_text=summary,
            display_text=f"{fp.name} ({meta['width']}x{meta['height']}, ~{meta['tokens']} tok)",
            blocks=[
                media.text_block(summary),
                media.image_block(media.to_data_uri(payload, out_mime)),
            ],
        )

    def _cut_line(self, line: str) -> str:
        """One line of a page, as it is numbered: whole, here.

        A tool with a budget per line cuts it here, before the page's budget
        counts it, so a page of minified lines holds its lines and the page
        that shows the head of one over-long line is never its answer
        (Raven-Code's read cuts to 2000 characters with a mark).
        """
        return line

    async def execute(self, path: str, offset: int = 1, limit: int | None = None, **kwargs: Any) -> str | ToolResult:
        # A file on a registered machine is read there, by its Raven node.
        if (
            not _is_a_device_spelling(path)
            and (remote := await remote_files.answer("read_file", path, {"offset": offset, "limit": limit})) is not None
        ):
            return remote
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return _missing(path) + _elsewhere(path)
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            with fp.open("rb") as fh:
                head = fh.read(self._MAGIC_SNIFF_BYTES)
            sniffed = detect_image_mime(head)
            mime = sniffed
            if mime is None:
                guessed = mimetypes.guess_type(fp.name)[0]
                # Magic bytes only cover the four formats every target inlines, so
                # a raster format Pillow can convert (BMP/TIFF/ICO) reaches this
                # branch as an extension guess. A guess can be wrong both ways --
                # .svg is XML with no Pillow decoder, a .png stub may hold text --
                # so it is trusted provisionally and a decode failure falls back
                # to the text path below rather than refusing to read the file.
                if guessed and guessed.startswith("image/"):
                    mime = guessed
            if mime is not None:
                from raven.agent.tools.media import ImageTooLargeError

                try:
                    return self._read_image(fp, mime)
                except ImageTooLargeError as e:
                    return f"Error: {e}"
                except Exception as e:
                    if sniffed is not None:
                        return f"Error decoding image {path}: {e}"
                    # An extension-only guess that would not decode: fall through.

            if offset < 1:
                offset = 1
            start = offset - 1
            with fp.open("rb") as fh:
                raw_page, total = _page_of_lines(fh, start, limit or self._DEFAULT_LIMIT)
            if total == 0:
                return f"(Empty file: {path})"
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            # Strict, as the whole-file decode was: a byte that is not UTF-8
            # on the page refuses the read with the codec's own sentence.
            lines = [self._cut_line(raw.decode("utf-8")) for raw in raw_page]
            end = start + len(lines)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(lines)]
            result = "\n".join(numbered)

            note = ""
            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                if not trimmed:
                    # The page's first line is over the budget by itself (a
                    # minified script, a one-line JSON, a long CSV row): whole
                    # lines within the budget left nothing, and the footer
                    # sent the reader back to this very line, for ever
                    # (measured 2026-10-15: "Showing lines 1-0 of 1. Use
                    # offset=1"). Its head is shown instead, and the rest is
                    # said to be out of offset's reach, since offset moves by
                    # lines. The break the budget counts makes a line that
                    # just fits land here too; it is shown whole, unremarked.
                    prefix = f"{start + 1}| "
                    room = max(self._MAX_CHARS - len(prefix), 0)
                    trimmed = [prefix + lines[0][:room]]
                    if len(lines[0]) > room:
                        note = (
                            f"\n\n(Line {start + 1} is {len(lines[0])} characters long; the first {room} are shown. "
                            "offset moves by lines and cannot reach the rest of it: grep tells whether a piece is "
                            "in it, and a shell command that cuts by bytes, such as cut -c, shows another stretch.)"
                        )
                end = start + len(trimmed)
                result = "\n".join(trimmed) + note

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class WriteFileTool(_FsTool):
    """Write content to a file."""

    approval_kind = "file.write"

    @property
    def name(self) -> str:
        return "write_file"

    def approval_evidence(self, params: dict[str, Any]) -> dict[str, Any]:
        """The write as a diff against the file as it stands, before it happens."""
        path = str(params.get("path") or "")
        content = str(params.get("content") or "")
        if (where := _machine_of(path)) is not None:
            # The file is on another machine, so there is no file here to diff
            # against; the card names the machine and shows the path as written.
            return {"path": path, "machine": where}
        try:
            fp = self._resolve(path)
        except PermissionError:
            return {"path": path}
        exists = fp.is_file()
        before = ""
        if exists:
            try:
                if fp.stat().st_size > _PREVIEW_MAX_BYTES:
                    return {"path": str(fp), "created": False}
                before = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                return {"path": str(fp), "created": False}
        after = before + content if params.get("mode") == "append" else content
        return {"path": str(fp), "created": not exists, "diff": _unified(before, after, str(fp))}

    @property
    def description(self) -> str:
        return (
            "Write content to a file at the given path. Creates parent directories if needed. "
            "Use mode=append to add to a file rather than replace it."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": (
                        "append adds to the end of the file; overwrite (default) replaces it. "
                        "Use append to continue a file you have already started -- adding to "
                        "one written earlier, or building up long content across several calls."
                    ),
                },
            },
            "required": ["path", "content"],
        }

    @property
    def truncation_hint(self) -> str:
        return (
            "Arguments cut off by that limit are discarded whole rather than partly saved, "
            "so write the content across several calls: mode=overwrite to start a file, "
            "mode=append to continue one you have already begun."
        )

    @property
    def incomplete_hint(self) -> str:
        return (
            "arguments cut off that way are discarded whole rather than partly saved, so "
            "send the content across several calls -- mode=overwrite to start a file, "
            "mode=append to continue one you have already begun."
        )

    async def execute(self, path: str, content: str, mode: str = "overwrite", **kwargs: Any) -> str | ToolResult:
        if mode not in ("overwrite", "append"):
            return f"Error: unknown mode '{mode}' for write_file. Use 'overwrite' or 'append'."
        # An empty append is refused rather than treated as a no-op: it is what
        # a call cut off before its content field looks like, and the one thing
        # it must never silently become is an overwrite.
        if mode == "append" and not content:
            return "Error: write_file with mode=append needs content; refusing to append nothing."
        # A file on a registered machine is written there, by its Raven node.
        if (
            not _is_a_device_spelling(path)
            and (remote := await remote_files.answer("write_file", path, {"content": content, "mode": mode}))
            is not None
        ):
            return remote
        try:
            fp = self._resolve(path)
            # Read before writing: a whole-file write carries no record of what
            # it replaced, so a panel handed only the arguments draws every line
            # of an overwrite as an addition.
            before = ""
            # Three states, not two, and the third is why this is a separate
            # flag: absent, present and readable, present and not decodable as
            # text. Only the first is a new file, and reporting the third as one
            # would tell a client every line is an addition to a file that was
            # already there.
            previous: str | None = None
            if fp.is_file():
                try:
                    before = previous = fp.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    before = ""
                    previous = None
                    unreadable = True
                else:
                    unreadable = False
            else:
                unreadable = False
            fp.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with fp.open("a", encoding="utf-8") as handle:
                    handle.write(content)
                # Bytes, as the line says: ``len(content)`` counted characters, and a
                # model sizing a CJK report off it read a third of its real length.
                return f"Successfully appended {len(_write_text_bytes(content))} bytes to {fp}"
            # The bytes decide, with no text gate in front of them. Text
            # equality is wrong in both directions here: ``read_text`` folds
            # CRLF to LF, so a CRLF file looks equal to the LF content that
            # would replace it, and content carrying CRLF never looks equal to
            # the file already holding exactly those bytes.
            would_write = _write_text_bytes(content)
            if fp.is_file():
                try:
                    unchanged = fp.read_bytes() == would_write
                except OSError:
                    # Reading to decide whether to write must not become a
                    # precondition for writing: a POSIX mode-0200 file is
                    # writable and unreadable, and overwriting one worked
                    # before this check existed. An unreadable file is simply
                    # not a no-op.
                    unchanged = False
                if unchanged:
                    # A byte count for a rewrite that changed nothing reads as
                    # progress, and ``model_text`` is the whole account of the
                    # call the caller gets: a loop shrinking a file can rewrite
                    # the same bytes several times before anything says
                    # otherwise. Nothing is written, so there is no diff or
                    # file_change to carry either.
                    return ToolResult(
                        f"File unchanged: {fp} already holds exactly these "
                        f"{len(would_write)} bytes, so nothing was written.",
                    )
            fp.write_text(content, encoding="utf-8")
            return ToolResult(
                f"Successfully wrote {len(would_write)} bytes to {fp}",
                diff=_unified(before, content, str(fp)),
                # Beside the rendered diff, not instead of it: the unified form is
                # what a text surface shows, and this is what a surface with its
                # own diff view needs. Both come from strings already in hand, so
                # neither costs a second read. Withheld entirely for a file that
                # existed and could not be read, because there is no ``before``
                # to give and every way of faking one misinforms the reader.
                file_change=None if unreadable else FileChange(path=str(fp), after=content, before=previous),
            )
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """Locate old_text in content: exact first, then line-trimmed sliding window.

    Both inputs should use LF line endings (caller normalises CRLF).
    Returns (matched_fragment, count) or (None, 0).
    """
    if old_text in content:
        return old_text, content.count(old_text)

    old_lines = old_text.splitlines()
    if not old_lines:
        return None, 0
    stripped_old = [line.strip() for line in old_lines]
    content_lines = content.splitlines()

    candidates = []
    for i in range(len(content_lines) - len(stripped_old) + 1):
        window = content_lines[i : i + len(stripped_old)]
        if [line.strip() for line in window] == stripped_old:
            candidates.append("\n".join(window))

    if candidates:
        return candidates[0], len(candidates)
    return None, 0


class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""

    approval_kind = "file.write"

    @property
    def name(self) -> str:
        return "edit_file"

    def approval_evidence(self, params: dict[str, Any]) -> dict[str, Any]:
        """The replacement as a diff of the two snippets.

        The snippets, not the file: the file is read only if the edit runs, and
        what the reader is asked to allow is exactly this substitution."""
        path = str(params.get("path") or "")
        old, new = str(params.get("old_text") or ""), str(params.get("new_text") or "")
        if (where := _machine_of(path)) is not None:
            return {"path": path, "machine": where, "created": False, "diff": _unified(old, new, path)}
        try:
            shown = str(self._resolve(path))
        except PermissionError:
            shown = path
        return {"path": shown, "created": False, "diff": _unified(old, new, shown)}

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "Supports minor whitespace/line-ending differences. "
            "Set replace_all=true to replace every occurrence."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **kwargs: Any,
    ) -> str | ToolResult:
        # A file on a registered machine is edited there, by its Raven node.
        sent = {"old_text": old_text, "new_text": new_text, "replace_all": replace_all}
        if (
            not _is_a_device_spelling(path)
            and (remote := await remote_files.answer("edit_file", path, sent)) is not None
        ):
            return remote
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            match, count = _find_match(content, old_text.replace("\r\n", "\n"))

            if match is None:
                return self._not_found_msg(old_text, content, path)
            if count > 1 and not replace_all:
                return (
                    f"Warning: old_text appears {count} times. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")
            new_content = content.replace(match, norm_new) if replace_all else content.replace(match, norm_new, 1)
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            normalised = new_content.replace("\r\n", "\n")
            return ToolResult(
                f"Successfully edited {fp}",
                # Compared line-for-line rather than passing the two snippets:
                # `replace_all` can change several places at once, and the
                # arguments alone do not say where.
                diff=_unified(content, normalised, str(fp)),
                # The whole file both ways. An edit's arguments carry only the
                # replaced fragment, so a surface handed those would render a
                # fragment as though it were the file.
                file_change=FileChange(path=str(fp), after=normalised, before=content),
            )
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(
                difflib.unified_diff(
                    old_lines,
                    lines[best_start : best_start + window],
                    fromfile="old_text (provided)",
                    tofile=f"{path} (actual, line {best_start + 1})",
                    lineterm="",
                )
            )
            return f"Error: old_text not found in {path}.\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""

    _DEFAULT_MAX = 200

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are skipped below the "
            "listed path; name one as the path to see inside it."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory path to list"},
                "recursive": {
                    "type": "boolean",
                    "description": "Recursively list all files (default false)",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum entries to return (default 200)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        path: str,
        recursive: bool = False,
        max_entries: int | None = None,
        **kwargs: Any,
    ) -> str:
        params = {"recursive": recursive, "max_entries": max_entries}
        if (
            not _is_a_device_spelling(path)
            and (remote := await remote_files.answer("list_dir", path, params)) is not None
        ):
            return str(remote)
        try:
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}" + _elsewhere(path)
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            if recursive:
                items, total, incomplete = await asyncio.to_thread(self._walk_entries, dp, cap)
            else:
                entries = self._list_entries(dp)
                items, total, incomplete = entries[:cap], len(entries), False

            if not items and not incomplete:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            notes = []
            if total > cap:
                notes.append(f"truncated, showing first {cap} of {total} entries")
            if incomplete:
                notes.append(
                    f"PARTIAL result: the listing hit its {tree_walk.WALK_DEADLINE_S:g}s traversal budget "
                    "before finishing, so an entry's absence is not conclusive -- narrow the path or "
                    "list without recursive"
                )
            if notes:
                result = f"{result}\n\n({'; '.join(notes)})" if result else f"({'; '.join(notes)})"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"

    @staticmethod
    def _list_entries(dp: Path) -> list[str]:
        return [
            f"{'📁 ' if item.is_dir() else '📄 '}{item.name}"
            for item in sorted(dp.iterdir())
            if item.name not in tree_walk.IGNORE_DIRS
        ]

    @staticmethod
    def _walk_entries(dp: Path, cap: int) -> tuple[list[str], int, bool]:
        """Every entry under ``dp`` in path order, the total, and whether the walk finished.

        Runs in a worker thread. Entries are ordered the way ``sorted(rglob)``
        ordered them, by path components, so a directory's contents follow it.
        """
        found: list[tuple[tuple[str, ...], str]] = []
        incomplete = False
        last_root: str | None = None
        rel_root = Path()
        try:
            for root, name, is_dir in tree_walk.walk(dp):
                if root != last_root:
                    last_root = root
                    rel_root = Path(root).relative_to(dp)
                rel = rel_root / name
                found.append((rel.parts, f"{rel}/" if is_dir else str(rel)))
        except TimeoutError:
            incomplete = True
        found.sort(key=lambda entry: entry[0])
        return [shown for _, shown in found[:cap]], len(found), incomplete

```

## raven/agent/tools/registry.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/tools/registry.py

SHA-256: `2e3fc6224488eeaa728a86c7270e4b01e196a4be652a5acb71d70848508f7971`

```
"""Tool registry for dynamic tool management."""

import asyncio
import copy
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from raven.agent import workdir
from raven.agent.tools.params import cast_params, render_param_errors, validate_params
from raven.contracts.llm_provider import RunMeta, TruncationInfo
from raven.contracts.tool import RAW_ARGUMENTS_KEY, Continuation, Tool, ToolOutput, ToolResult
from raven.observability import semconv
from raven.tracing import trace

if TYPE_CHECKING:
    from raven.mcp.naming import MCPToolRef


class ToolAdmissionError(TypeError):
    """A tool refused at the registry's door: an authored member is missing or
    mis-shaped. Raised at registration, where the author sees it — not deep
    inside the turn that first calls the tool."""


@dataclass(frozen=True)
class ToolSpec:
    """The admitted, frozen shape of one registered tool.

    Dispensed once at the door (:func:`admit_tool`) and the only thing the
    registry's own machinery reads afterwards — a consumer that kept reading
    members off the live object let the de-facto contract widen silently
    (measured: 4 authored members on the paper, 13 consumed). Behaviour
    (``execute`` / ``blocking_for`` / ``metadata_owner`` / ``cast_params``)
    stays on ``tool``, the body; data rides here, frozen at admission. Same
    declare→check→dispense pattern as manifest and config-slice admission.
    """

    name: str
    schema: dict[str, Any]
    # An AUTHORED ``to_schema`` override is a signed declaration of a dynamic
    # shape (load_playbook regenerates its enum per render); the registry then
    # serves the live call instead of the snapshot. Undeclared mutation of
    # ``parameters`` still cannot leak — that path stays frozen.
    schema_dynamic: bool
    channels: frozenset[str] | None
    timeout_seconds: float | None
    truncation_hint: str | None
    incomplete_hint: str | None
    agent_output: bool
    # The optional availability declaration (raven/contracts/plugin_surface.py):
    # a tool that bills against a credential the deployment may not have
    # configured authors ``configured()``, and the withheld axis asks it per
    # assembly. Dispensed here so the consumer reads the admitted shape, not a
    # member discovered off the live object; None when the tool declares none.
    configured: Callable[[], bool] | None
    tool: Tool


def admit_tool(tool: Tool) -> ToolSpec:
    """Check the four authored members, normalize the optional ones, dispense
    the frozen spec.

    The advertised schema is derived HERE, from the authored members, and
    deep-copied: the base class's ``to_schema`` is sugar, not the ticket (a
    duck with the four members boards without it), and a shallow snapshot
    would alias the tool's live ``parameters`` dict — a later mutation of the
    object must not leak into what the model is shown. The one sanctioned
    escape is an authored ``to_schema`` override: writing one declares the
    schema dynamic, and the registry serves it live."""
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name:
        raise ToolAdmissionError(f"tool {tool!r} declares no usable name")
    description = getattr(tool, "description", None)
    if not isinstance(description, str):
        raise ToolAdmissionError(f"tool {name!r}: description must be a string")
    parameters = getattr(tool, "parameters", None)
    if not isinstance(parameters, dict):
        raise ToolAdmissionError(f"tool {name!r}: parameters must be a JSON-schema mapping")
    if not callable(getattr(tool, "execute", None)):
        raise ToolAdmissionError(f"tool {name!r}: execute is not callable")
    channels = getattr(tool, "channels", None)
    timeout = getattr(tool, "timeout_seconds", None)
    if timeout is not None and not isinstance(timeout, (int, float)):
        raise ToolAdmissionError(f"tool {name!r}: timeout_seconds must be a number or None")
    truncation = getattr(tool, "truncation_hint", None)
    incomplete = getattr(tool, "incomplete_hint", None)
    agent_output = getattr(tool, "agent_output", False)
    if not isinstance(agent_output, bool):
        raise ToolAdmissionError(f"tool {name!r}: agent_output must be a bool")
    configured = getattr(tool, "configured", None)
    if configured is not None and not callable(configured):
        # ``configured = False`` reads as an author declaring the tool
        # unavailable; admitting it would offer the tool anyway, silently.
        raise ToolAdmissionError(f"tool {name!r}: configured must be a callable answering bool, or absent")
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": copy.deepcopy(parameters),
        },
    }
    own_to_schema = getattr(type(tool), "to_schema", None)
    dynamic = own_to_schema is not None and own_to_schema is not Tool.to_schema
    return ToolSpec(
        name=name,
        schema=schema,
        schema_dynamic=dynamic,
        channels=frozenset(channels) if channels is not None else None,
        timeout_seconds=float(timeout) if timeout is not None else None,
        truncation_hint=truncation if isinstance(truncation, str) else None,
        incomplete_hint=incomplete if isinstance(incomplete, str) else None,
        agent_output=agent_output,
        configured=configured,
        tool=tool,
    )


def _truncation_error(truncation: TruncationInfo) -> str:
    """What is known, and what is only inferred, kept apart.

    Known: the turn stopped at the output limit, because the upstream said so.
    Inferred: that this call was the cut one, which follows from generation
    being sequential but not from anything the upstream said -- a turn can
    finish a call and then hit the limit in the prose after it. Saying "this
    call was cut" as a fact sends a model to split up a call that was whole.

    The refusal is not conditional on that inference. A call that may be
    incomplete is not dispatched either way; being wrong costs one retry. What
    to do about it is the tool's, via ``Tool.truncation_hint``.
    """
    at = f" at the {truncation.at_tokens}-token output limit" if truncation.at_tokens else " at the output limit"
    return (
        f"Error: [truncated] This turn stopped{at}, and this call was the last thing "
        f"being written, so it may have been cut short. It was not run. Send it again."
    )


def absent_tool_error(name: str, *, tail: str = "") -> str:
    """What either surface says about a name that did not resolve.

    Both readings, because neither surface can tell them apart: the model
    invented the name, or an MCP server was unloaded mid-turn after this turn's
    prompt promised its tools. "Not found" alone reads as "you got the name
    wrong", which is wrong half the time.

    One function because the two surfaces must not word it differently, and a
    test holding two string literals together is the wrong place for that. The
    ``tail`` is how the folded surface adds where its catalog is; the unfolded
    one has none to point at -- the model is already holding every schema.
    """
    return f"Error: tool '{name}' is not available. It may have been unloaded, or the name may be wrong{tail}."


def _received_tail(params: dict[str, Any]) -> str:
    """The text the model actually emitted, for a call whose arguments did not parse.

    Worth quoting back rather than only naming the fault. Reporting an unparsed
    call through schema validation says "you forgot `path`", which is a lie the
    caller acts on: it re-sends the same malformed JSON with the same field in
    it, and loops -- eighteen calls in one observed session, the model
    eventually theorising about ``_raw_arguments``, a key that exists only
    because we put it there. Showing the text is what ends that.

    Empty when the arguments parsed, so it can be appended unconditionally.
    """
    raw = str(params.get(RAW_ARGUMENTS_KEY) or "")
    if not raw:
        return ""
    return f" Received: {raw[:400]}" + ("..." if len(raw) > 400 else "")


def _charter_withheld(tools: "Mapping[str, Tool]") -> frozenset[str]:
    """What this turn's charter, if any, takes off the table.

    Imported inside the call rather than at module level: a charter belongs to
    the dispatch layer, and the registry is reached from places that must not
    pull that in. The import is a cached lookup after the first turn.
    """
    try:
        from raven.agent.subagent.charter import current_charter, narrowed_tools
    except Exception:  # noqa: BLE001 - no charter support is not a reason to lose tools
        return frozenset()
    # Asked before the names are materialised. This runs on every
    # ``withheld_names`` read, which is every ``execute`` and every schema
    # render, so a turn with no charter must not pay a tuple of every tool name
    # for an answer that is always the empty set.
    if current_charter() is None:
        return frozenset()
    return narrowed_tools(tuple(tools))


def _record_call(name: str, params: dict[str, Any]) -> None:
    """Remember a call that ran and did not fail.

    After dispatch, never before: a rule that asks for a prior ``read_file``
    is asking whether the file was read, and a read that errored read nothing.
    """
    try:
        from raven.agent.subagent.charter import record_call

        record_call(name, params)
    except Exception:  # noqa: BLE001 - bookkeeping must not cost the call its result
        pass


def call_failed(result: Any) -> bool:
    """Whether one tool call went wrong, by the registry's own convention.

    The tool's verdict when it gave one, and the failure text as the backstop
    when it did not: ``execute`` returns a :class:`ToolOutput` on the paths that
    run a tool, and a bare string on the ones that refuse before dispatch
    (unparseable arguments, an invalid parameter set, a timeout), where there is
    no object to carry ``ok`` and the leading ``Error`` is the whole of the
    signal.

    One function because the rule had begun to be copied: reading only ``ok``
    calls a refused-before-dispatch call a success, and reading only the text
    calls a tool that answers with the word Error a failure.
    """
    if not bool(getattr(result, "ok", True)):
        return True
    text = getattr(result, "model_text", None)
    return str(text if text is not None else result).startswith("Error")


# When a running tool first says so, and how often after that. The first line is
# late enough that ordinary calls never print one, and early enough that a reader
# tailing the log during a slow call does not have to wait a full minute to learn
# it is a slow call rather than a dead one.
HEARTBEAT_FIRST_S = 30.0
HEARTBEAT_EVERY_S = 60.0


async def _heartbeat(name: str, ceiling: float) -> None:
    """Say that a tool is still running, for as long as it still is.

    A tool between its start line and its result line was indistinguishable from
    a process that had stopped. Measured on a real run: `ppt_prepare` held the
    turn for 600 seconds, twice, and the log for that whole window holds three
    lines -- the call, the timeout, and the next iteration. Nothing said which of
    the tool's own steps it was in, or that it was in any of them, so the first
    question asked of the record ("was it working?") had no answer in it.

    A line rather than an event: this is the registry, which has no outlet, and
    the question it answers is the one asked of a log after the fact. What a
    reader watching the page sees while a tool runs is a separate seam.

    Cancelled by the caller when the call returns, so the last line is always one
    the tool outlived.
    """
    started = time.monotonic()
    delay = HEARTBEAT_FIRST_S
    while True:
        await asyncio.sleep(delay)
        delay = HEARTBEAT_EVERY_S
        logger.info(
            "tools: {} still running after {:.0f}s of its {:.0f}s ceiling",
            name,
            time.monotonic() - started,
            ceiling,
        )


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    # Backstop ceiling for tools that don't set their own ``timeout_seconds``.
    # Generous on purpose: it exists to break an infinite hang (a tool with no
    # internal timeout that never returns), not to enforce a tight per-tool SLA.
    DEFAULT_TOOL_TIMEOUT_S = 300.0

    def __init__(
        self,
        *,
        tool_gates: "Sequence[Any]" = (),
        permission_gate: "Any | None" = None,
        verifier_provider: "Callable[[], Any] | None" = None,
    ):
        self._tools: dict[str, Tool] = {}
        # The Action role, asked once per call for the judgement this
        # dispatch's Charter carries. Injected the way the permission gate
        # beside it is: the module supplies the sentences, ``execute`` still
        # decides what to do with them, so a replaced role can withhold
        # nothing it was not already able to withhold. ``None`` on a registry
        # built without a harness (a test rig, a tool-only surface), which
        # reads as "no judgement" -- the answer this question had before the
        # role existed -- which is the right answer for every registry that is
        # not a turn's: the MCP connection managers and the curator's own
        # lookup table build one to hold tool definitions, never to dispatch a
        # turn's calls, so there is no dispatch for a Charter to judge.
        #
        # A provider rather than the role itself: the registry is built before
        # the loop assembles its harness, so there is no role to hand over yet.
        self._verifier_provider = verifier_provider
        # The platform's own gate (raven.permissions), distinct from the plugin
        # gates below: it can wait on a human mid-adjudication and answers with
        # a full ToolResult, both of which the plugin paper deliberately does
        # not offer. Fixed at construction like the gates -- an unattended
        # registry is built with a gate that refuses to ask.
        self._permission_gate = permission_gate
        # Cast at assembly, fixed for the generation (paper:
        # contracts/tool_gate.py): no setter, no latch -- changing gates is a
        # generation swap. Ordered by (name, contributed_by) so adjudication
        # is deterministic whatever order the builder yielded them in.
        self._tool_gates: tuple[Any, ...] = tuple(
            sorted(tool_gates, key=lambda g: (str(getattr(g, "name", "")), str(getattr(g, "contributed_by", ""))))
        )
        # The admitted, frozen specs, keyed like ``_tools``. The registry's own
        # machinery reads data from here and calls behaviour on the body; the
        # pair is written together in register() and nowhere else.
        self._specs: dict[str, ToolSpec] = {}
        # The one record of where a namespaced tool came from, keyed by the name
        # it is registered under. Every question about an MCP tool -- which
        # server owns it, what it is called there, which registered names a
        # config entry refers to -- is answered from here and nowhere else. It
        # has to be one place: the name is a one-way function of the pair
        # (sanitised, capped, possibly hash-suffixed), so anything that tries to
        # recover the parts from the string is guessing, and three consumers
        # each guessed differently before this existed.
        self._origins: dict[str, MCPToolRef] = {}
        # Turn-local, not an attribute: one gateway registry serves concurrent
        # turns from different channels, and a plain field would let whichever
        # turn set it last decide what the others are shown.
        self._channel: ContextVar[str | None] = ContextVar("tool_registry_channel", default=None)
        # Asked, not stored: the operator's off switches are a *preference*, and
        # a preference read once at startup is one the operator cannot change.
        # See ``set_withheld_source``.
        # ``_withheld``, ``_schema_hidden`` and the turn freeze below are
        # distinct axes: the first is an operator off switch (a withheld tool is
        # unreachable everywhere), the second a design property (a hidden tool
        # stays callable -- ``tool_call`` resolves by registry, never by schema
        # -- it is just not advertised), and the freeze is per turn (a mid-turn
        # arrival is unreachable through every surface until the next turn,
        # unless the turn's own call admits it -- ``admit_to_this_turn`` --
        # and ``offers`` consults it for schema and tool-search, and ``execute``
        # consults it for dispatch, so the three cannot disagree).
        self._withheld: Callable[[], frozenset[str]] | None = None
        self._schema_hidden: set[str] = set()
        # Placed at assembly, not by registering late: an MCP server connects,
        # and a session brings its own tools, after the host wired its own, so
        # registration order cannot keep anything at the end. See ``list_last``.
        self._listed_last: tuple[str, ...] = ()
        # The tools a session brought with it, keyed by session, plus the
        # turn-local view of one session's set. Two halves because the two facts
        # have different lifetimes: the binding lives as long as the session, the
        # visibility only as long as the turn that runs under it.
        #
        # A ContextVar and not a field, for the reason ``_channel`` gives: turns
        # from two sessions run concurrently on one registry, and a field would
        # let whichever turn entered last decide what the other one can see.
        self._session_tools: dict[str, dict[str, Tool]] = {}
        self._overlay: ContextVar[dict[str, Tool] | None] = ContextVar("tool_registry_overlay", default=None)
        # The tools this turn may be shown, captured at turn entry as the
        # (name, instance) pairs themselves. Instances and not names: a
        # same-name re-registration is a different tool wearing a familiar
        # label, and admitting it by name would swap the served schema between
        # two model calls of one turn. A ContextVar for the reason ``_channel``
        # gives; ``None`` (no turn scope) advertises everything, which is what
        # registries that never enter one -- a sub-agent's, the curator's -- get.
        self._turn_names: ContextVar[dict[str, Tool] | None] = ContextVar("tool_registry_turn_names", default=None)
        # The off switches as they stood at turn entry. Unioned into
        # ``withheld_names`` so a switch that turns a tool ON mid-turn lands on
        # the next turn -- the tool array is the prompt-cache prefix, and a
        # live un-withholding would move it between two model calls exactly the
        # way a late registration would. Turning a tool OFF stays live: the
        # union can only add entries, never mask one the current read carries.
        self._turn_withheld: ContextVar[frozenset[str] | None] = ContextVar("tool_registry_turn_withheld", default=None)

    @property
    def tool_gates(self) -> tuple[Any, ...]:
        """The gates cast over this registry at construction, in adjudication
        order. Read-only on purpose: the paper's first discipline."""
        return self._tool_gates

    def set_withheld_source(self, source: "Callable[[], frozenset[str]] | None") -> None:
        """Install the answer to "which tools has the operator switched off".

        A callable rather than a set, because the point is that it can change
        while the process runs. It is asked once per assembly (see
        :meth:`get_definitions`), so it is free to be a cached read of a file
        whose mtime it watches.

        This replaces unregistering the tool. Unregistering expressed the
        preference by destroying the thing it was a preference about, so turning a
        tool back on was not merely unimplemented but unimplementable -- nothing
        remembered what to put back. Withholding is reversible by construction,
        and it is the same shape the channel restriction already has.
        """
        self._withheld = source

    def _verifier_refusals(self, name: str, params: dict[str, Any]) -> list[str]:
        """Why the Action role refuses this call, or an empty list.

        The call log is read here and handed over rather than reached for by
        the role: what already ran this turn is the registry's own record (it
        is the thing that watched them run), and a role asking for it would be
        a module reaching into the shell's state.

        Caught here and nowhere inside: this is a boundary onto a *replaceable*
        role, and the shell does not get to assume what is behind it. The
        default implementation guards nothing of its own -- a defect in rule
        evaluation is meant to be loud -- but a replacement that raises must
        cost its own judgement rather than the turn, which is the same bargain
        the plugin gates beside it are held to.

        A registry nobody handed a role to still asks one. Most of the ten
        registries this tree builds take no provider -- the curator's own tool
        set among them, and it dispatches inside the turn's ``charter_scope``
        -- so an absent provider means "nobody chose a role here", not "this
        dispatch carries no Charter". The default role is what the module-level
        helper this method replaced already was.
        """
        try:
            # Imported in the call, and not for style: ``raven.agent.subagent``
            # pulls its manager on package import, whose backends import this
            # module -- so naming the charter at the top of this file closes a
            # cycle. import-linter reads direction and cannot see this one.
            from raven.agent.harness import DefaultAction
            from raven.agent.subagent.charter import prior_calls

            role = self._verifier_provider() if self._verifier_provider is not None else DefaultAction()
            return list(role.ask_judge(name, params, prior_calls()))
        except Exception:  # noqa: BLE001 - a role that raises must not cost the turn
            logger.warning("tools: the Action role raised judging {!r}; taking no opinion from it", name)
            return []

    def withheld_names(self) -> frozenset[str]:
        """The current off switches, or an empty set when nobody installed a source.

        Inside a turn scope, whatever was withheld at the turn's entry stays
        withheld for the whole turn (see ``_turn_withheld``); switches that
        tighten mid-turn still land on this very read.
        """
        frozen = self._turn_withheld.get() or frozenset()
        # A dispatch's charter narrows this turn the same way an off switch
        # does, and joins by union for the same reason: every source here may
        # take a tool away and none may hand one back. That is what makes a
        # charter safe to accept from another process -- the worst a bad one
        # can do is leave this turn with fewer tools than it would have had.
        charter = _charter_withheld(self._tools)
        if self._withheld is None:
            return frozen | charter
        try:
            return self._withheld() | frozen | charter
        except Exception:  # noqa: BLE001 - a bad read must not cost the turn its tools
            logger.warning("tools: could not read the disabled-tool list; offering everything")
            return frozen | charter

    def hide_from_schema(self, *names: str) -> None:
        """Keep tools registered and callable, but out of the provider's tool schema.

        For tools whose only advertisement is another tool's result text. Unlike
        the off switch this is not operator-reversible: the tool itself is what
        decides it is not for the schema.
        """
        self._schema_hidden.update(names)

    def schema_hidden_names(self) -> frozenset[str]:
        """Every registered name withheld from the schema (still callable)."""
        return frozenset(self._schema_hidden)

    def list_last(self, *names: str) -> None:
        """Close the provider's tool array with these tools, in the order given.

        The array is read top to bottom like the rest of the prompt, so where a
        tool sits is part of what it says when another tool's text points at it:
        ``run_subagent_dag`` names the roster at the end of ``spawn``'s
        description instead of repeating it, which is only the next thing read
        when the two close the array in that order. A name that is not on offer
        leaves no gap; a later call replaces the earlier one.
        """
        self._listed_last = tuple(names)

    def set_channel(self, channel: str | None) -> None:
        """Record the channel this turn is answering on (turn-local).

        Set alongside the per-tool contexts, before the schema is assembled, so
        ``offers_on_this_channel`` can withhold a channel-bound tool. Left unset
        the registry advertises everything, which is what a surface with a
        single channel (the sub-agent and curator registries) wants.
        """
        self._channel.set(channel)

    def offers_on_this_channel(self, tool: Tool) -> bool:
        """Whether this turn's channel may see ``tool`` at all.

        The channel half of :meth:`offers`. Kept separate because the two answer
        different questions -- one is what the surface can carry, the other is
        what the operator asked for -- and only the second one moves while the
        process runs.
        """
        if tool.channels is None:
            return True
        channel = self._channel.get()
        return channel is None or channel in tool.channels

    def offers_by_name(self, name: str) -> bool:
        """Whether ``name`` is a tool the model can actually reach right now.

        The question every caller reaching for ``get(name) is not None`` meant.
        Registration was a fair proxy for availability until the off switch
        stopped unregistering: a withheld tool stays in ``_tools``, which is what
        makes the switch reversible, so ``get`` now answers "this build has such a
        tool" rather than "you may use it".

        It matters wherever the answer becomes prompt text: advertising a tool the
        operator switched off, which ``execute`` then refuses, is the one thing
        the switch is supposed to make impossible.
        """
        tool = self._visible(name)
        return tool is not None and self.offers(tool)

    def offers(self, tool: Tool, withheld: "frozenset[str] | None" = None) -> bool:
        """Whether ``tool`` is reachable at all right now.

        The one predicate behind both surfaces a tool can be reached through --
        the schema and tool-search -- so a tool cannot be hidden from one and
        found through the other. That invariant is why the off switch belongs
        here and not at either call site -- and why the turn freeze does too: a
        mid-turn arrival kept out of the schema but findable through tool-search
        would be the same disagreement.

        ``withheld`` is passed in when a caller is testing many tools at once, so
        the source is asked once per assembly rather than once per tool.
        """
        if not self.offers_on_this_channel(tool):
            return False
        if not self._visible_to_this_turn(tool.name):
            return False
        names = self.withheld_names() if withheld is None else withheld
        return tool.name not in names

    def _visible_to_this_turn(self, name: str) -> bool:
        """Whether the turn freeze admits this name; always true outside a scope.

        Admission is by identity, not by name: the entry pair must still be the
        registered pair. A same-name re-registration therefore reads as the
        removal it starts with -- the name drops out for the rest of the turn,
        which the asymmetry allows -- and the replacement is an addition that
        lands on the next turn like any other, instead of changing an admitted
        entry's schema between two model calls.

        Consulted by ``offers`` (schema, tool-search) and by ``execute``
        (dispatch): a name the turn cannot see is one the turn cannot run,
        or a call composed against the entry instance's schema would be
        dispatched to whatever now wears the name.

        Session-overlay tools are admitted by name: they enter with the turn
        that carries them, after the freeze captured the base registry. A name
        :meth:`admit_to_this_turn` let in is an entry pair like any other, and
        that method adds only names the turn did not already hold, so a
        replacement stays dropped through it too.
        """
        frozen = self._turn_names.get()
        if frozen is None:
            return True
        entry = frozen.get(name)
        if entry is not None and self._tools.get(name) is entry:
            return True
        overlay = self._overlay.get()
        return bool(overlay and name in overlay)

    def register(self, tool: Tool, *, origin: "MCPToolRef | None" = None) -> None:
        """Register a tool, recording where it came from if it has an origin.

        ``origin`` is how a namespaced tool declares its parts. Passed at
        registration rather than read back off the tool afterwards, so the
        record cannot disagree with the registration that created it.
        """
        spec = admit_tool(tool)
        self._tools[spec.name] = tool
        self._specs[spec.name] = spec
        if origin is not None:
            self._origins[spec.name] = origin

    def unregister(self, name: str) -> None:
        """Unregister a tool by name -- the only way a tool leaves.

        No record is kept of what was here. What that costs a turn already in
        flight, and why the miss is worded the way it is, is in :meth:`execute`.
        """
        self._tools.pop(name, None)
        self._specs.pop(name, None)
        self._origins.pop(name, None)

    def origin_of(self, name: str) -> "MCPToolRef | None":
        """Where this registered tool came from, or None if it has no origin."""
        return self._origins.get(name)

    def spec_of(self, name: str) -> ToolSpec | None:
        """The admitted shape of a registered tool, or None for a name the door
        never saw (a session overlay tool, or nothing by that name)."""
        return self._specs.get(name)

    def names_from(self, server: str) -> list[str]:
        """Every registered name contributed by one MCP server.

        The manager teardown path reads this rather than keeping its own set of
        names: a second copy is a second answer, and the blacklist can
        unregister a name between the connect and the teardown.
        """
        return [name for name, ref in self._origins.items() if ref.server == server]

    def bind_session_tools(self, session_key: str, tools: "Mapping[str, Tool]") -> None:
        """Record the tools one session brought with it, without registering them.

        Registering would publish them process-wide: one registry serves every
        session on a connection, so a tool registered for session A stays
        reachable from session B's turn and from the next session on the same
        process. These are held aside instead, and made visible only inside
        :meth:`session_scope_for`.

        Replaces any earlier set for the same session -- a session that brings
        servers twice (``session/new`` then ``session/load``) means the second
        set, not both.
        """
        self._session_tools[session_key] = dict(tools)

    def release_session_tools(self, session_key: str) -> None:
        """Forget one session's tools. Idempotent -- a session that brought none is normal."""
        self._session_tools.pop(session_key, None)

    @contextmanager
    def session_scope(self, tools: "Mapping[str, Tool] | None") -> Iterator[None]:
        """Make ``tools`` visible for the duration of the current task, and only there.

        Entered where the turn runs, not where it was requested: the turn is
        submitted onto the spine and executes on its own task, which does not
        inherit a context set by the request handler.

        Passing nothing (or an empty mapping) is not a no-op -- it clears the
        overlay for this task. A turn whose session brought no servers must not
        see another session's, so "no tools" has to be stated rather than left to
        whatever the surrounding context happened to hold.
        """
        token = self._overlay.set(dict(tools) if tools else None)
        try:
            yield
        finally:
            self._overlay.reset(token)

    @contextmanager
    def session_scope_for(self, session_key: str) -> Iterator[None]:
        """:meth:`session_scope` over whatever this session bound, if anything."""
        with self.session_scope(self._session_tools.get(session_key)):
            yield

    @contextmanager
    def turn_scope(self) -> Iterator[None]:
        """Freeze which registered tools this turn's schema may carry.

        A tool that registers while the turn runs -- an MCP handshake finishing
        in the background is the ordinary case since ``prewarm_mcp`` -- joins the
        next turn instead of appearing in this one's tool array between two model
        calls. The array is the first segment of the prompt-cache prefix, so a
        mid-turn arrival rebuilds the whole cached prompt; the arrival loses
        nothing by waiting, because ``_mcp_tool_notices`` already tells the model
        the server is still connecting.

        The freeze is against ADDITIONS to the array, whatever their mechanism:
        a registration that lands mid-turn, and equally an off switch that turns
        a tool back ON mid-turn (the withheld set as of entry is unioned into
        every ``withheld_names`` read for the turn). Removals pass through both
        axes -- a tool that left the registry has no schema to serve, and a
        switch that tightens mid-turn must bind the very next call.
        A same-name re-registration is both at once -- a removal followed by an
        addition wearing the old label -- and each half keeps its own timing:
        the entry drops out of this turn (captured pairs are checked by
        identity, see ``_visible_to_this_turn``), and the replacement joins the
        next one. Anything else would swap an admitted entry's schema in place,
        moving the array as surely as a new name would. The one lane that may
        still re-render mid-turn is a tool that DECLARED a dynamic schema
        (``schema_dynamic``): its instance is stable and its variability is its
        contract, so the freeze pins the pair and leaves the rendering to it.
        Session-overlay tools are exempt: they enter with the turn that carries
        them, and so are arrivals the turn itself asked for
        (:meth:`admit_to_this_turn`).
        """
        token = self._turn_names.set(dict(self._tools))
        wtoken = self._turn_withheld.set(self.withheld_names())
        try:
            yield
        finally:
            self._turn_withheld.reset(wtoken)
            self._turn_names.reset(token)

    def admit_to_this_turn(self, names: Iterable[str]) -> None:
        """Let arrivals the turn itself asked for join it from the next step.

        The one exception to the freeze in :meth:`turn_scope`. The freeze is
        right for a background arrival: nobody in the turn is waiting for it.
        It is wrong when the turn's own call produced the arrival -- the
        ``plugin`` tool connecting what the user just asked for -- because then
        all the wait buys is a user having to say "go on" before the agent can
        use it. That is paid for with one rebuilt cache prefix, once.

        Mutated in place rather than re-set, so the admission reaches the
        loop's reads however the call was dispatched: a ``set`` made inside a
        task would stay in that task's copy of the context.

        Only names the turn does not already hold are added. A name it holds
        against an instance that has since been replaced keeps the stale pair
        and so stays dropped -- the identity rule in
        :meth:`_visible_to_this_turn` -- rather than swapping in a schema the
        turn's earlier calls were never composed against. The off switch and
        the channel restriction still decide whether an admitted name is
        offered. Outside a scope this is a no-op; everything is already offered.
        """
        frozen = self._turn_names.get()
        if frozen is None:
            return
        for name in names:
            tool = self._tools.get(name)
            if tool is not None:
                frozen.setdefault(name, tool)

    def session_tools_in_scope(self) -> dict[str, Tool]:
        """The session tools this turn can see; empty outside any scope.

        For the one reader that cannot be served by ``_visible_tools``:
        tool-search ranks a BM25 index that is built once and shared by every
        concurrent turn, so a turn's own tools have to be added on the read side
        instead of indexed. ``tool_names`` / ``names`` stay the registration
        view -- MCP teardown diffs them to learn what a connect added.
        """
        return dict(self._overlay.get() or {})

    def _visible(self, name: str) -> Tool | None:
        """One name resolved the way this turn sees it: its session's tools, then the process's.

        The single lookup behind ``get`` / ``has`` / ``execute`` / ``is_blocking``
        / ``take_metadata``, so a session tool cannot be advertised through one
        and missing from another -- the shape that would show up as a tool the
        model is offered and then told does not exist.
        """
        overlay = self._overlay.get()
        if overlay is not None and name in overlay:
            return overlay[name]
        return self._tools.get(name)

    def _visible_tools(self) -> dict[str, Tool]:
        """Every tool this turn can reach, session tools last so a clash resolves to them."""
        overlay = self._overlay.get()
        if not overlay:
            return self._tools
        return {**self._tools, **overlay}

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._visible(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return self._visible(name) is not None

    def resolve_configured(self, configured: str) -> list[str]:
        """Every registered name a config entry refers to (possibly empty).

        Exact match wins outright. The registry is keyed by name, so a config
        entry naming a tool that exists names *that* tool -- fanning out from
        there would disable something the user did not write. That is not
        hypothetical: two different servers can produce the same historical
        spelling (``mcp_openseo_search_v2`` is the raw form of both
        ``('openseo', 'search_v2')`` and ``('openseo_search', 'v2')``), so a
        fan-out reached across servers into a tool named nothing like the entry.

        The fallback exists for the opposite case -- an entry naming a spelling
        no tool carries today, because sanitising or the length cap changed it.
        Such a name cannot be parsed back into a pair, so each origin generates
        its own spellings forward and the entry is tested against them. Several
        origins can legitimately answer: ``a.b`` and ``a/b`` both clean to
        ``a_b``, and an entry naming the collapsed spelling means both.
        """
        from raven.mcp.naming import spellings

        if configured in self._visible_tools():
            return [configured]
        return [name for name, ref in self._origins.items() if configured in spellings(ref.server, ref.tool)]

    def names(self) -> list[str]:
        """Every registered tool name.

        Used to attribute registrations to the attempt that made them: an MCP
        connect registers through this registry, so diffing before and after is
        the only way to know what a *cancelled* attempt managed to add.
        """
        return list(self._tools)

    def is_blocking(self, name: str, params: dict[str, Any] | None = None) -> bool:
        """Whether this call is a blocking interaction (execute() is not timer-wrapped).

        The tool's own ``blocking_for`` is the single fact source, and it takes the
        call's params because a forwarding tool (``tool_call``) inherits the verdict
        of whatever it forwards to. Exposed so a turn stream can tell a consumer
        that this call has no deadline and may go silent for as long as it runs,
        without the consumer keeping its own list of tool names.
        """
        tool = self._visible(name)
        return bool(tool is not None and tool.blocking_for(params or {}))

    def take_metadata(self, name: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Consume the structured payload this call left for the turn stream.

        Takes the call's params for the same reason ``is_blocking`` does: a
        forwarding tool (``tool_call``) owns none of the metadata it returns, so
        the payload must be collected from whatever it forwarded to.
        """
        tool = self._visible(name)
        if tool is None:
            return None
        return tool.metadata_owner(params or {}).take_metadata()

    def get_definitions(self) -> list[dict[str, Any]]:
        """Tool definitions in OpenAI format, minus those not on offer right now.

        Assembled per LLM call, which is what makes an off switch take effect on
        the next turn rather than the next restart: the list is computed here, so
        the only thing a preference has to do is be readable by the time this
        runs.
        """
        withheld = self.withheld_names()
        # Served from the admitted snapshot, not the live object: what the
        # model is shown is what the door checked, whatever the object has
        # grown or mutated since. A tool that AUTHORED its own to_schema has
        # declared a dynamic shape (load_playbook's per-render enum) and is
        # served live -- declared dynamism, not silent widening. A session
        # overlay tool never passed the door and keeps its live schema.
        out: list[dict[str, Any]] = []
        last: dict[str, dict[str, Any]] = {}
        for name, tool in self._visible_tools().items():
            if not self.offers(tool, withheld) or name in self._schema_hidden:
                continue
            spec = self._specs.get(name)
            schema = tool.to_schema() if spec is None or spec.schema_dynamic else spec.schema
            if name in self._listed_last:
                last[name] = schema
            else:
                out.append(schema)
        out.extend(last[name] for name in self._listed_last if name in last)
        return out

    def hidden_definition(self, name: str) -> dict[str, Any] | None:
        """One schema-hidden tool's definition, in the shape a visible tool gets.

        A hidden tool reaches the model only through another tool's result text,
        so that text has to carry the definition -- and carrying a hand-written
        copy of it is what drifted: the model wrote ``action`` for ``decision``
        and lost a call to it three runs running. Served from here so the
        advertisement cannot say anything the door did not check.

        ``None`` when the name is not registered, or when the tool is *not*
        hidden: a visible tool is already in the array, and advertising it a
        second time is the duplication this exists to end.
        """
        if name not in self._schema_hidden:
            return None
        tool = self.get(name)
        if tool is None:
            return None
        spec = self._specs.get(name)
        # Same snapshot-or-live rule as ``get_definitions``: an AUTHORED
        # ``to_schema`` is the tool signing for a shape that is not fixed at
        # admission, and ``resolve_dag_node``'s is -- its node shape carries a
        # ``subagent`` enum built from the hot-appliable agent table.
        if spec is None or spec.schema_dynamic:
            return tool.to_schema()
        return copy.deepcopy(spec.schema)

    @trace.instrument("tool.call", extract=semconv.tool_call)
    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        run_meta: RunMeta | None = None,
    ) -> str:
        """Execute a tool, preserving agent content and scrubbing Raven's own tool results.

        Local results are scrubbed here, inside the traced call, because every reader starts from
        this return value: the main and sub-agent loops, the sentinel's action
        executor, the tool forwarder, and the trace span that records it. A
        loop that scrubbed only its own copy left the span, the file diff sent
        to the page and the sentinel's channel reply holding the key.
        """
        return _scrubbed(params, await self._execute(name, params, run_meta=run_meta))

    async def _execute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        run_meta: RunMeta | None = None,
    ) -> str:
        """Execute a tool by name with given parameters.

        ``run_meta`` carries what happened around the call rather than what it
        asks for -- today only whether it finished arriving, which changes what
        a validation failure means. Keyword-only so the five call sites stay
        self-describing and so a second field does not reorder anything.
        """
        # Appended to an error the model may recover from by doing something
        # else. Deliberately not on the branch below: a tool that is not there
        # is not one of those, and "try a different approach" argues with an
        # answer whose point is that there is nothing to try.
        _hint = "\n\n[Analyze the error above and try a different approach.]"

        # A withheld tool is still in ``_tools`` by construction -- that is what
        # makes the switch reversible -- so the off switch has to be answered here
        # too, or it is an omission from the array rather than a block. Two ways a
        # name arrives anyway: a model calling from habit rather than from the
        # array (an eval harness leaving only ``execute`` still gets asked for
        # ``read_file``), and a switch flipped mid-conversation, where the array is
        # fresh and the history is not.
        #
        # The channel half of ``offers`` cannot move in here: its ContextVar is
        # unset on internally-initiated calls, so testing it would refuse them all.
        # The turn freeze CAN and must: a call composed against the entry
        # instance's schema would otherwise dispatch to a same-name replacement
        # that landed mid-turn, running arguments shaped for a tool that is no
        # longer registered. The freeze admits on an unset scope (a registry
        # that never enters one, an internally-initiated call), so unlike the
        # channel gate it refuses nothing it should not.
        tool = self._visible(name)
        # A registered tool answers data questions from its admitted spec; a
        # session overlay tool never passed the door and answers live.
        spec = self._specs.get(name) if tool is not None and self._tools.get(name) is tool else None
        if not tool or name in self.withheld_names() or not self._visible_to_this_turn(name):
            # No catalog listing on the end of it. Unfolded, every schema is
            # already in this request and a list only repeats it; folded, a
            # cataloged tool is reached through ``tool_call``, which appends the
            # pointer this surface cannot. Measured at 1470 tokens for a
            # 210-tool deploy, in the tool result, kept in history for the run.
            return absent_tool_error(name)

        # Refused before dispatch, not after validation: a truncated call whose
        # required fields happen to have arrived still validates, and running it
        # executes an intent that was never fully transmitted. For write_file
        # that is not merely an incomplete write -- `mode` is optional, so a cut
        # before it arrives falls back to "overwrite" and silently replaces
        # everything an earlier append had written, then reports success.
        #
        # The cost of being wrong here is one retry: a turn can end with a
        # complete tool call and be cut in prose that follows it, which the
        # non-streaming path cannot tell apart (see providers/truncation.py).
        # A wasted turn is cheaper than a silent overwrite.
        truncation = run_meta.truncation if run_meta else None
        if truncation:
            # The tail belongs here too, and this is the path it matters most on:
            # ``flag_truncation`` reads ``arguments_repaired`` to reach its verdict
            # and adds ``truncation`` to that same run_meta, so a streamed reply cut
            # mid-arguments arrives carrying all three -- the flag, the verdict, and
            # the parked text.
            hint = (spec or tool).truncation_hint
            return _truncation_error(truncation) + (f" {hint}" if hint else "") + _received_tail(params)
        if run_meta and run_meta.arguments_repaired and run_meta.last_of_turn:
            # Two facts, two readings, and nothing here to choose between them.
            # The arguments did not parse and nothing arrived after this call,
            # which is the shape a cut leaves -- and equally the shape of a
            # model writing bad JSON on its last call. Each reading gets its own
            # branch rather than one being asserted: a model told to split up a
            # call it merely misspelled goes looking for a size problem it does
            # not have. Longer than a verdict, and the length is the point.
            hint = (spec or tool).incomplete_hint
            limit_branch = (
                f"\n\nIf it was the output limit: {hint}"
                if hint
                else "\n\nIf it was the output limit: send this call again in a smaller form."
            )
            return (
                f"Error: [incomplete arguments] The arguments for '{name}' did not parse, and it "
                f"was the last call of the turn. Two things can cause that: the reply hit its "
                f"output token limit part-way through writing this call, or the arguments were "
                f"simply malformed. It was not run either way, and nothing here tells the two "
                f"apart." + limit_branch + "\n\nIf the arguments were malformed: send the same "
                "call again with well-formed arguments." + _received_tail(params)
            )
        if run_meta and run_meta.arguments_repaired:
            # Calls arrived after this one, so a cut cannot explain it: the
            # model wrote bad JSON. Telling it to send the content in smaller
            # pieces would send it after a problem it does not have.
            return (
                f"Error: [invalid arguments] The arguments for '{name}' were not valid JSON, "
                f"so this call was not run. Send it again with well-formed arguments." + _received_tail(params)
            )

        try:
            params = cast_params(tool.parameters, tool.cast_params(params))

            errors = validate_params(tool.parameters, params) + tool.validate_params(params)
            # This turn's charter, judged where a schema error is judged and for
            # the same reason: the refusal reaches the model as this call's
            # result, so the next attempt can be right. Ahead of the permission
            # gate, which is about what a person allows rather than what this
            # dispatch was briefed to do.
            errors += self._verifier_refusals(name, params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}':" + render_param_errors(errors) + _hint

            # Post-validation, pre-dispatch, and ahead of the plugin gates:
            # platform authority precedes plugin policy. The refusal (or the
            # human's deny) replaces the call; approval waits deliberately
            # happen before the timeout ceiling below, so a human deciding is
            # never timer-killed.
            if self._permission_gate is not None:
                refusal = await self._permission_gate.enforce(name, params, tool=tool)
                if refusal is not None:
                    return ToolOutput(
                        refusal.model_text,
                        refusal.display_text,
                        retryable=refusal.retryable,
                        blocks_call=refusal.blocks_call,
                        continuation=refusal.continuation,
                        ok=refusal.ok,
                    )

            # Post-validation, pre-dispatch: the point the paper names. Guarded
            # so a registry with no gates runs today's path byte for byte.
            if self._tool_gates:
                verdict = await self._adjudicate(name, params)
                if verdict is not None:
                    return verdict

            ceiling = (spec or tool).timeout_seconds or self.DEFAULT_TOOL_TIMEOUT_S
            if tool.blocking_for(params):
                # Intentionally waits on a human — must not be timer-killed.
                result = await tool.execute(**params)
            else:
                # The heartbeat is a bystander: it neither holds the result nor
                # touches the deadline, so a change here cannot alter what the
                # tool returns or when it is killed.
                beat = asyncio.ensure_future(_heartbeat(name, ceiling))
                try:
                    result = await asyncio.wait_for(tool.execute(**params), timeout=ceiling)
                finally:
                    beat.cancel()

            # Unwrap ToolResult here, at the boundary: `execute` promises model
            # text to every caller, and only the agent loop wants the display
            # string and multimodal blocks (which ride along on ToolOutput).
            #
            # Provenance is a permission plus a claim, and takes both. The
            # tool's declaration admits it to carrying another agent's words;
            # the result claims that THIS one does. Judged on the declaration
            # alone, a tool's own sentence about its own failure would read as
            # an agent's -- `a2a_send` interpolates the exception into "the A2A
            # call failed", and a peer credential echoed in it would reach the
            # model unscrubbed. Judged on the claim alone, any tool could
            # unscrub itself. So both are required, and the claim is opt-in: a
            # bare string claims nothing, which is what every refusal and status
            # line this process composed is, and a structured result claims only
            # where it says so. A forwarder owns none of what it returns, so its
            # declaration is read off the tool it forwarded to.
            declared = getattr(spec or tool, "agent_output", False)
            if not declared:
                declared = bool(getattr(tool.metadata_owner(params), "agent_output", False))
            agent_output = declared and bool(getattr(result, "agent_output", False))
            if isinstance(result, (ToolResult, ToolOutput)):
                model_text = result.model_text if isinstance(result, ToolResult) else str(result)
                display_text = result.display_text
                retryable, blocks_call = result.retryable, result.blocks_call
                continuation = result.continuation
                ok = result.ok
                blocks = result.blocks
                diff = result.diff
                file_change = result.file_change
                removed = result.removed
                written = result.written
            else:
                model_text, display_text = str(result), None
                retryable, blocks_call = True, False
                continuation = Continuation.CONTINUE
                ok = bool(getattr(result, "ok", True))
                blocks = None
                diff = None
                file_change = None
                # Neither of the two result shapes, so nothing is declared
                # about what this return carries: read off it the fields a
                # caller may have attached (a `str` subclass a tool built for
                # itself), and default the rest. Read rather than defaulted for
                # the removals and writes, which no other field would carry.
                removed = tuple(getattr(result, "removed", ()) or ())
                written = tuple(getattr(result, "written", ()) or ())
            # Remembered once the verdict is in, and only when it is good. A
            # rule that asks for a prior ``read_file`` is asking whether the
            # file was read; a read that errored read nothing, and letting it
            # satisfy the rule would hand the next call exactly the permission
            # the rule exists to withhold. Judged by ``call_failed`` rather than
            # by ``ok`` alone, for the reason that function was written: a tool
            # answering with a bare error string carries no ``ok`` to read.
            #
            # The result itself, not its text: ``call_failed`` reads ``ok``
            # first and falls back to the text only when there is no ``ok`` to
            # read, so handing it the unwrapped string is what would lose the
            # verdict -- a ``run_shell`` that exited non-zero says so through
            # ``ok`` and its output rarely begins with the word Error.
            if not call_failed(result):
                _record_call(name, params)

            if model_text.startswith("Error"):
                # ``Error:`` describes presentation, not retry semantics.
                # Policy-aware tools return explicit control metadata so the
                # registry does not accidentally turn a security decision into
                # the generic invitation to find an equivalent implementation.
                #
                # An error also replaces the result, so any blocks it came with
                # are no longer what the model should be looking at.
                #
                # What the call did to the disk is kept: a command whose output
                # happens to begin with the word still removed and wrote what it did.
                suffix = _hint if retryable else ""
                return ToolOutput(
                    model_text + suffix,
                    display_text,
                    retryable=retryable,
                    blocks_call=blocks_call,
                    continuation=continuation,
                    ok=False,
                    removed=removed,
                    written=written,
                    agent_output=agent_output,
                )
            return ToolOutput(
                model_text,
                display_text,
                retryable=retryable,
                blocks_call=blocks_call,
                continuation=continuation,
                ok=ok,
                blocks=blocks,
                diff=diff,
                file_change=file_change,
                removed=removed,
                written=written,
                agent_output=agent_output,
            )
        except asyncio.TimeoutError:
            return f"Error: Tool '{name}' timed out after {ceiling:.0f}s." + _hint
        except Exception as e:
            # Never the tool's declaration: this sentence is the registry's own,
            # raised out of a call that produced nothing, so an agent tool that
            # dies here reports its own error as exactly what it is.
            return ToolOutput(f"Error executing {name}: {str(e)}" + _hint, ok=False)

    async def _adjudicate(self, name: str, params: dict[str, Any]) -> str | None:
        """Run the cast gates over one validated call; the first non-None
        verdict replaces it.

        A verdict is returned verbatim -- no retry hint appended: the
        adjudication text is the complete result. A gate that raises refuses
        the call it was adjudicating (fail-closed, naming the gate), because
        failing open would make a gate's bugs silent permission grants. The
        turn's working directory is passed explicitly so a gate never fishes
        it out of params; None outside a bound turn (a subagent backend, an
        internally-initiated call), and the gate decides for itself.
        """
        session_workdir = workdir.current()
        for gate in self._tool_gates:
            gate_name = getattr(gate, "name", None) or type(gate).__name__
            try:
                verdict = await gate.adjudicate(name, params, session_workdir=session_workdir)
            except Exception as e:
                return f"Error: tool call '{name}' was refused by gate {gate_name}: {e}"
            if verdict is not None:
                return verdict
        return None

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def _scrubbed(arguments: Any, out: Any) -> Any:
    """Preserve agent output; scrub local model text, display, blocks and file contents.

    The file contents are display only (the page's diff, the removal watch);
    nothing writes them back, so a redaction there cannot reach a file.
    """
    from dataclasses import replace

    from raven.config.held_secrets import scrub_tool_blocks, scrub_tool_output

    if isinstance(out, ToolOutput) and out.agent_output:
        return out

    def text(value: Any) -> Any:
        return scrub_tool_output(arguments, value) if isinstance(value, str) and value else value

    if not isinstance(out, ToolOutput):
        return text(out)
    change = out.file_change
    return ToolOutput(
        text(str(out)),
        text(out.display_text),
        retryable=out.retryable,
        blocks_call=out.blocks_call,
        continuation=out.continuation,
        ok=out.ok,
        blocks=scrub_tool_blocks(arguments, out.blocks),
        diff=text(out.diff),
        file_change=replace(change, after=text(change.after), before=text(change.before)) if change else None,
        removed=tuple(replace(item, before=text(item.before)) for item in out.removed),
        written=tuple(replace(item, diff=text(item.diff)) for item in out.written),
    )

```

## raven/agent/tools/shell.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/tools/shell.py

SHA-256: `25905fec732ec091988b0e75ec7f2307f90175d34a926076285f847925c2a3fb`

```
"""Shell execution tool.

Authorization lives at the registry's door (``raven.permissions``): whether a
command may run, must ask, or is refused is decided before this tool is
dispatched. What stays here is the tool's own integrity boundary -- the
operator's allowlist and the workspace fence -- and the execution itself.
"""

import asyncio
import fnmatch
import os
import re
import shlex
from pathlib import Path
from typing import Any

from raven.agent import workdir
from raven.agent.tools import command_writes
from raven.contracts.tool import (
    STOP_RETRY_INSTRUCTION,
    Continuation,
    FileRemoval,
    FileWrite,
    Tool,
    ToolOutput,
    ToolResult,
)
from raven.permissions.shell_policy import (
    _MAX_EMBEDDED_SHELL_DEPTH,
    _command_segments_with_separators,
    _embedded_shell_command,
    _runner_inner_command,
    _unwrap_command_wrappers,
    executable_text,
)
from raven.sandbox import DirectExecutor, SandboxExecutor
from raven.sandbox.compat_bin import with_compat


class _UnmodelledExpansionError(Exception):
    """A parameter expansion the fence cannot resolve to the text the shell runs.

    The fence's promise is that it reads what will run. Where it cannot, the
    honest answer is a refusal rather than a scan of text the shell will
    replace: an expansion it does not model can hand the command any path at
    all, and every spelling that reached this point before was a silent bypass.
    Command substitution is not this case -- it holds an arbitrary program,
    which no textual guard can resolve, and it is a declared limit of the fence
    rather than a gap in it.
    """

    def __init__(self, construct: str) -> None:
        super().__init__(construct)
        self.construct = construct


class ExecTool(Tool):
    """Tool to execute shell commands."""

    # Backstop above the 600s internal exec cap (``_MAX_TIMEOUT``); the
    # executor's own timeout fires first, this only catches a wedged executor.
    # A tool that measures its commands' files adds what the measuring may
    # take (``measure_writes``): cutting a call off here loses its output.
    timeout_seconds = 660.0
    approval_kind = "shell.exec"

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        executor: SandboxExecutor | None = None,
        extra_allowed_dirs: tuple[Path, ...] = (),
        *,
        follow_binding: bool = True,
        record_writes: bool = False,
        shadow: command_writes.ShadowFor | None = None,
    ):
        self._timeout = timeout
        self.working_dir = working_dir
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.extra_allowed_dirs = extra_allowed_dirs
        # A sub-agent run is a background asyncio task that can outlive the turn
        # that spawned it, since SubagentManager.spawn captures the workspace at
        # spawn time; its ExecTool must resolve cwd from the directory captured
        # for that run, never from the ambient binding, or it disagrees with its
        # own fs tools about which directory it is in (the same follow_binding
        # convention as the filesystem tools). The main loop's ExecTool keeps
        # following the live binding as normal.
        self.follow_binding = follow_binding
        self.record_writes = False
        self._shadow: command_writes.ShadowFor | None = None
        if record_writes:
            self.measure_writes(shadow)
        self.path_append = path_append
        self._executor: SandboxExecutor = executor if executor is not None else DirectExecutor()
        if not self._executor.is_sandboxed:
            # The model checks a deck by running `soffice --convert-to` itself,
            # with LibreOffice's default profile; on a Mac that profile is the
            # only place its Chinese faces can come from (raven/utils/office.py).
            from raven.utils import office

            office.link_han_faces_into_default_profile()

    @property
    def timeout(self) -> int:
        """The ceiling a command runs under, as the file has it now.

        Read here rather than copied at construction: a timeout is a preference
        about the next command, and this tool outlives any number of turns --
        and a sub-agent's copy outlives the spawn that built it. What it was
        built with answers when the file has no opinion.
        """
        from raven.config.live import default_live, exec_timeout, held

        configured = held("tools.exec.timeout", lambda: exec_timeout(default_live()))
        return configured or self._timeout

    @property
    def name(self) -> str:
        return "exec"

    def measure_writes(self, shadow: command_writes.ShadowFor | None) -> None:
        """Have each command's result say which files it created, rewrote and removed.

        Read off its directory either side (``command_writes``), with ``shadow``
        saying what those files held, for their diffs, and which of them may be
        shown. Asked of the built-in and of a plugin's same-name replacement
        alike, and before either is registered: the ceiling it raises is read
        off the spec the registry admits. Left off for a lane whose runner lists
        every call itself.
        """
        if not self.record_writes:
            self.timeout_seconds += command_writes.MEASURE_SECONDS
        self.record_writes = True
        self._shadow = shadow

    async def warm(self, root: Path) -> None:
        """Start staging ``root`` into the shadow repo, ahead of its first command.

        The first staging of a directory hashes every file in it, which in a
        large tree takes long enough to hold a command up; started as a session
        opens, it runs while the user types and the model replies. Returns at
        once, and does nothing where no shadow repo covers ``root``.
        """
        if not self.record_writes or self._shadow is None:
            return
        shadow = self._shadow(root)
        if shadow is not None:
            await shadow.warm()

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    # What the deletion watch will look at and hold. The candidate cap bounds a
    # stat per token of one command; the byte cap bounds what a removed file's
    # body costs to carry, and past it the deletion is still reported, without
    # its text.
    _MAX_REMOVAL_CANDIDATES = 64
    _MAX_REMOVAL_BYTES = 256 * 1024

    # A token holding one of these is a pattern, not a path: the shell expands it
    # to names this tool never sees, so resolving it as written would stat a file
    # that does not exist under that name.
    _GLOB_CHARS = frozenset("*?[")

    def to_schema(self) -> dict[str, Any]:
        """This call's shape, rendered fresh for every model call.

        Authored rather than inherited on purpose: ``ToolRegistry`` serves a
        tool from the copy it took at registration unless the tool writes its
        own ``to_schema``, and the machine lane below depends on the registry,
        which can be written mid-session. Served from that copy, a process that
        started with no machine kept an ``exec`` without ``machine`` after the
        first ``ops_connection_add`` -- while that tool's reply, the typed-ssh
        refusal and the coding guide all sent the model to ``machine=<id>``
        (reviewed 2026-09-24). The shape moves only when the registry goes from
        empty to not, so the array stays stable across calls otherwise.
        """
        return super().to_schema()

    @property
    def description(self) -> str:
        # Two descriptions, because the second sentence is a lie for an owner
        # who registered no machines. Read per call rather than fixed at
        # construction -- the registry can be written mid-session, and
        # ``to_schema`` above is what makes the registry serve this live.
        base = "Execute a shell command and return its output. Use with caution."
        from raven.agent.tools.machine_exec import machines_registered

        if not machines_registered():
            return base
        return (
            f"{base} "
            "Runs on THIS computer unless you name a machine: pass 'machine' with a "
            "connection id from the owner's registry to run it there instead. When the "
            "owner said where -- a machine's name, a nickname, an address or part of one, "
            "a port -- pass their words as 'machine' even if they are not an id: the reply "
            "says which registered machine they mean. A machine "
            "of the owner's is where their code and their cases live, and work that "
            "outlives the call belongs to a job runner, so this refuses to detach there."
        )

    @property
    def truncation_hint(self) -> str:
        # "Send it in smaller pieces" is meaningless for a command: half a
        # command is not a command. What splits here is the work, not the
        # argument.
        return "Shorten the command, or split the work across several runs."

    @property
    def incomplete_hint(self) -> str:
        # Phrased as the consequent of a condition; see Tool.incomplete_hint.
        return "shorten the command, or split the work across several runs."

    @property
    def parameters(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Timeout in seconds for a command on THIS computer. Increase for long-running "
                        "commands like compilation or installation (default 60, max 600). Not read with "
                        "'machine': a look on a registered machine is capped at 60s whatever this says, and "
                        "anything longer there is a job for the on-call agent's ops_submit."
                    ),
                    "minimum": 1,
                    "maximum": 600,
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Run detached on THIS computer instead of holding the turn: for long "
                        "mechanical local work (a download, an rsync, packaging). Returns a task id "
                        "and a log path at once; 'timeout' is not read on this lane, the task runs "
                        "until it finishes or the session ends. Read the log with a later exec. Not "
                        "combinable with 'machine': work on a registered machine is ops_submit's job."
                    ),
                },
            },
            "required": ["command"],
        }
        from raven.agent.tools.machine_exec import machines_registered

        if machines_registered():
            schema["properties"]["machine"] = {
                "type": "string",
                "description": "Where to run it: a connection id from the owner's machine "
                "registry, exactly as listed (an unknown id answers with the list). "
                "Leave out to run on THIS computer.",
            }
        return schema

    async def execute(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int | None = None,
        machine: str = "",
        run_in_background: bool = False,
        **kwargs: Any,
    ) -> str | ToolResult:
        # Someone else's machine is a different place with different rules: the
        # guards below are about this computer (a deny-list for accidents, an
        # operator's workspace boundary), while there the rule is that work
        # outliving the call belongs on a ledger. Branching before them keeps
        # each set where it means something.
        #
        # Imported here and nowhere else in the execute path: a caller who
        # names no machine must not be able to be broken by the ops layer -- a
        # malformed connection registry has to leave the plain shell working.
        if machine and run_in_background:
            # Background work ON a registered machine is the definition of an
            # ops_submit job; allowing it here would re-legalise the accounting
            # bypass this boundary exists for (a main agent once drove 35 jobs
            # over raw ssh nohup, past the occupancy gate, dedup and the GPU
            # ledger). The two parameters never combine.
            return (
                "Error: 'machine' and 'run_in_background' do not combine. Background work on a "
                "registered machine is a job: submit it with ops_submit (budget, dedup, ledger). "
                "run_in_background is for long mechanical work on THIS computer. Nothing was run."
            )
        if machine:
            from raven.agent.tools.machine_exec import run_on_machine

            return await run_on_machine(command, connection=machine, cwd=working_dir)
        # Same import discipline as above: the registry is read only to
        # recognise a registered address, and a registry that cannot be read
        # recognises nothing.
        from raven.agent.tools.machine_exec import raw_ssh_target

        # Off the loop: a destination the registry does not hold literally is
        # resolved through a local ``ssh -G``, a subprocess.
        if (row := await asyncio.to_thread(raw_ssh_target, command)) is not None:
            # Typed ssh to a registered machine is the registry bypassed: the
            # cap, the process-group sweep and the ledger all live on the
            # other two paths. Refused by name so the model learns the path,
            # not just that this one closed. A copy there with scp, sftp or
            # rsync starts nothing and passes this tool's own question like
            # any other command, so it is left alone. File tools read remote
            # paths through the registered machine's node.

            conn_id = str(row.get("id") or "").strip()
            files = (
                " Its files are read and written with read_file, write_file and edit_file on the path the way "
                "scp writes it."
            )
            return (
                f"Error: this command reaches {row.get('display_name') or conn_id} over raw ssh from "
                f"this computer, and that machine is registered as {conn_id!r}. Look at it with "
                f"exec(machine={conn_id!r}) (capped at 60s, nothing left running), and hand anything "
                "longer, or anything that must keep running, to the on-call agent's ops_submit "
                f"(budget, dedup, ledger).{files} scp and rsync to it are still fine here. Nothing was run."
            )

        cwd = self._cwd_for(working_dir)

        if not self._executor.is_sandboxed:
            guard_error = self._guard_command(command, cwd)
            if guard_error:
                return self._boundary_error(guard_error)
        elif self.restrict_to_workspace:
            # Sandboxed: the microVM provides real isolation, but the workspace
            # fence is an operator-set boundary and still holds.
            workspace_error = self._check_workspace_restriction(command, cwd)
            if workspace_error:
                return self._boundary_error(workspace_error)

        if run_in_background:
            # After the guards on purpose: the background lane changes where the
            # output goes and who holds the turn, never what a command is
            # allowed to do. The permission gate has already ruled on this call
            # at the registry door, whichever lane it takes.
            if self._executor.is_sandboxed:
                # The detached child is a host process; starting one from a
                # sandboxed session would run the command OUTSIDE the sandbox.
                return (
                    "Error: run_in_background is not available in a sandboxed session -- the "
                    "detached process would run outside the sandbox. Run it synchronously, or "
                    "split the work. Nothing was run."
                )
            from raven.agent.tools import background_exec
            from raven.sandbox.direct_executor import baseline_env

            # The same environment hygiene as the synchronous path: the child
            # gets the executor's baseline allowlist, never the full host
            # environment, so a detached download cannot read credentials the
            # capped path already withholds.
            bg_env = baseline_env()
            if self.path_append:
                bg_env["PATH"] = bg_env.get("PATH", "") + os.pathsep + self.path_append
            try:
                task = background_exec.start(command, cwd=cwd, env=bg_env)
            except OSError as exc:
                return f"Error starting background command: {exc}"
            # The note waits up to a second to see whether the command died at once;
            # on the loop's thread that wait would stall every other turn with it.
            return await asyncio.to_thread(background_exec.start_note, task)

        # Use `is None` check — `timeout or default` would treat timeout=0 as falsy.
        effective_timeout = min(self.timeout if timeout is None else timeout, self._MAX_TIMEOUT)

        # Read from the command as written, before the PATH wrapper below
        # rewrites it, and only on this lane: a detached command finishes after
        # the result is gone, and one on another machine names paths that are
        # not this filesystem's.
        watched = self._removal_watch(command, cwd)
        start: command_writes.Before | None = None
        if self.record_writes:
            start = await command_writes.before(Path(cwd), self._shadow)

        env: dict[str, str] | None = None
        if self.path_append:
            if self._executor.is_sandboxed:
                # Inject path inside the VM via command wrapper; never pass os.environ
                # to a sandboxed executor — it would leak host credentials into the VM.
                command = f'export PATH="$PATH:{shlex.quote(self.path_append)}" && {command}'
            else:
                # Pass ONLY the PATH override. Copying os.environ here would hand
                # the full host environment to DirectExecutor and defeat its
                # baseline-allowlist hygiene; the executor supplies the rest.
                base_path = with_compat(os.environ.get("PATH", ""))
                env = {"PATH": base_path + os.pathsep + self.path_append}

        try:
            result = await self._executor.exec(command, cwd=cwd, timeout=effective_timeout, env=env)
        except Exception as e:
            from raven.agent.tools.machine_exec import missing_here_note

            # A working_dir that is not on this disk fails here, before the command runs.
            return f"Error executing command: {str(e)}" + missing_here_note([cwd], command=command)
        text = result.as_text(self._MAX_OUTPUT)
        # `ls x || echo none` exits 0 with the miss only in its text.
        if (result.exit_code != 0 or "No such file or directory" in text) and not self._executor.is_sandboxed:
            from raven.agent.tools.machine_exec import missing_here_note

            text += missing_here_note(self._extract_absolute_paths(command), command=command)
        removed = tuple(
            FileRemoval(path=path, before=before) for path, before in watched.items() if not os.path.exists(path)
        )
        written: tuple[FileWrite, ...] = ()
        if start is not None:
            written, removed = await command_writes.after(start, named=removed)
        # The exit code is the verdict a config change, a security call or a
        # syntax error share, and the text a failing command produced is not
        # token-safe to classify from -- so the caller gets it structurally.
        return ToolOutput(text, ok=result.exit_code == 0, removed=removed, written=written)

    def _removal_watch(self, command: str, cwd: str) -> dict[str, str | None]:
        """The files this command could remove, with the text they hold now.

        No tool deletes a file as its purpose, so a deletion is only ever
        visible as a file that was there before a command ran and is not there
        after. What can be watched is what the command names: a path it computes
        (a glob the shell expands, a name a substitution produces) is outside
        this, the same limit the workspace fence declares for itself.

        The text is read here because after the command there is nothing left to
        read. Absent past the byte cap or on undecodable bytes -- the deletion is
        still reported, only its body is not.
        """
        readable = executable_text(command)
        try:
            # The fence's own reading of the command: split on the shell's
            # operators first, so `rm gone.txt;` names `gone.txt` and not a
            # file called `gone.txt;` that was never there.
            tokens = [token for segment, _ in _command_segments_with_separators(readable) for token in segment]
        except ValueError:
            # Quoting the lexer cannot close. The command may still run (the
            # shell is a better lexer than this one), so fall back to the
            # coarsest split rather than watching nothing.
            tokens = readable.split()
        roots = [Path(cwd).resolve(), *(Path(d).resolve() for d in self.extra_allowed_dirs)]
        watched: dict[str, str | None] = {}
        for token in tokens:
            if len(watched) >= self._MAX_REMOVAL_CANDIDATES:
                break
            raw = token.strip("\"'")
            if not raw or self._GLOB_CHARS & set(raw):
                continue
            try:
                candidate = Path(raw).expanduser()
                if not candidate.is_absolute():
                    candidate = Path(cwd) / candidate
                candidate = candidate.resolve()
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            if not any(root == candidate or root in candidate.parents for root in roots):
                continue
            key = str(candidate)
            if key not in watched:
                watched[key] = self._text_before(candidate)
        return watched

    @classmethod
    def _text_before(cls, path: Path) -> str | None:
        try:
            if path.stat().st_size > cls._MAX_REMOVAL_BYTES:
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return None

    @classmethod
    def _boundary_error(cls, message: str) -> ToolResult:
        """A fence refusal: this call and its siblings do not run, the turn does."""
        return ToolResult(
            model_text=message + STOP_RETRY_INSTRUCTION,
            retryable=False,
            blocks_call=True,
            continuation=Continuation.CONTINUE,
            ok=False,
        )

    def _cwd_for(self, working_dir: str | None) -> str:
        bound = str(workdir.current() or "") if self.follow_binding else ""
        return working_dir or bound or self.working_dir or os.getcwd()

    def listing_root(self, params: dict[str, Any]) -> Path | None:
        """Where this call's files land, as far as this host can see.

        The directory the command runs in, resolved the way ``execute`` will --
        a per-call ``working_dir`` moves it off the bound one -- or ``None`` for
        a command run on a registered machine, whose files are not on this disk
        and would be described by nothing a listing here could find.
        """
        if str(params.get("machine") or "").strip():
            return None
        return Path(self._cwd_for(str(params.get("working_dir") or "") or None))

    def approval_evidence(self, params: dict[str, Any]) -> dict[str, Any]:
        """The command and where it would run, resolved the way ``execute`` will."""
        command = str(params.get("command") or "")
        machine = str(params.get("machine") or "").strip()
        if machine:
            return {"command": command, "machine": machine}
        return {"command": command, "cwd": self._cwd_for(params.get("working_dir") or None)}

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """The tool's own boundary: the operator's allowlist and the workspace fence.

        Reads the executable view (comments and quoted text stripped) so a
        command cannot be talked onto the allowlist by its own comment, and a
        path named in a comment is not scanned as a path the command touches.
        The deny list is not here: refusing dangerous commands is the
        permission gate's job, decided before dispatch.
        """
        cmd = executable_text(command).strip()
        lower = cmd.lower()

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        workspace_error = self._check_workspace_restriction(command, cwd)
        if workspace_error:
            return workspace_error

        return None

    # The null-device family is how a shell mutes or feeds a stream, not an
    # escape from the workspace: `2>/dev/null` names a path only to the
    # guard's regex, and blocking it turns every quiet read-only probe into a
    # terminal safety refusal. Matched on the written form, before resolve(),
    # which on macOS follows /dev/stdout into /dev/fd and out of this set.
    _DEVICE_FILES = frozenset(
        {
            "/dev/null",
            "/dev/stdin",
            "/dev/stdout",
            "/dev/stderr",
            "/dev/tty",
            "/dev/zero",
            "/dev/urandom",
            "/dev/random",
        }
    )

    def _check_workspace_restriction(self, command: str, cwd: str, *, _depth: int = 0) -> str | None:
        """Check only the workspace boundary constraints (no allow-list).

        Reads the executable view rather than trusting the caller to strip: the
        traversal and absolute-path scans have no reason to see text the shell
        discards, and there are two call sites -- the guard and the sandboxed
        path -- so doing it here is what keeps them from diverging. Reading the
        raw text refused ``ls -la  # see ../notes for why`` as path traversal.

        The contract, because the fence is a reader of shell and a reader can
        always meet syntax it does not know:

        **A parameter expansion is resolved faithfully or the command is
        refused.** The set of spellings is finite, so the residue is closed:
        there is no further form that quietly passes. Refusing is visible and
        arguable; the failure it replaces was silent, and silence is what let
        four spellings through at once.

        **Unmodelled directory state is refused.** Conditional and loop syntax
        can hide directory changes, and ``cd -`` reads mutable OLDPWD state.
        The fence rejects those forms and unknown directory options instead of
        treating them as no move or as literal directory names.
        Shell and command-runner payloads share the policy's inspection bound;
        exceeding it refuses the command rather than leaving a payload unseen.

        **Command substitution is a declared limit, not a gap.** It holds an
        arbitrary program, so no textual guard can resolve it, and refusing it
        would refuse ``echo "built at $(date)"`` along with everything else.
        Paths written literally inside one are still scanned; a path the
        substitution computes is beyond this fence, as a symlink inside the
        workspace is, and the sandbox executor is the boundary for both.
        """
        if not self.restrict_to_workspace:
            return None
        command = executable_text(command)

        cmd = command.strip()
        if "..\\" in cmd or "../" in cmd:
            return "Error: Command blocked by safety guard (path traversal detected)"

        cwd_path = Path(cwd).resolve()
        roots = [cwd_path, *(Path(d).resolve() for d in self.extra_allowed_dirs)]
        env = self._child_env(cwd_path)
        try:
            readable = self._as_the_shell_reads_it(cmd, env)
        except _UnmodelledExpansionError as unresolved:
            return f"Error: Command blocked by safety guard (unsupported shell expansion: {unresolved.construct})"

        try:
            segments = list(_command_segments_with_separators(readable))
        except ValueError:
            # Quoting the lexer cannot close. The permission gate reads the
            # same text through the same lexer and refuses it there, so
            # answering here would only duplicate that refusal.
            segments = []

        for segment, _ in segments:
            tokens = _unwrap_command_wrappers(segment)
            if tokens and tokens[0] in {
                "if",
                "then",
                "elif",
                "else",
                "fi",
                "for",
                "while",
                "until",
                "do",
                "done",
                "case",
                "esac",
                "select",
                "function",
                "!",
            }:
                return "Error: Command blocked by safety guard (unsupported shell control flow)"

        if self._steps_outside(segments, cwd_path, roots, env):
            return "Error: Command blocked by safety guard (directory change outside working dir)"

        for raw in self._extract_absolute_paths(readable):
            try:
                if raw in self._DEVICE_FILES:
                    continue
                p = Path(raw.strip()).expanduser().resolve()
            except Exception:
                continue
            if not p.is_absolute():
                continue
            if any(root == p or root in p.parents for root in roots):
                continue
            return "Error: Command blocked by safety guard (path outside working dir)"

        # A nested shell's payload is a program, not an argument: the quoting
        # that stopped this pass expanding it is what hands it to that shell
        # intact, and the names in it expand there. Scanning it with the same
        # rules is the only reading that matches what runs. Depth-capped with
        # the policy's own bound, which is what decides the same question for
        # the deny list.
        for segment, _ in segments:
            # Unwrapped first, the way the policy reads a segment before
            # asking what it runs. Reusing the payload helper without the
            # unwrap that precedes it there reused half the agreement:
            # `env sh -c '...'` put the program back out of view.
            tokens = _unwrap_command_wrappers(segment)
            for inner in (_embedded_shell_command(tokens), _runner_inner_command(tokens)):
                if not inner:
                    continue
                if _depth >= _MAX_EMBEDDED_SHELL_DEPTH:
                    return "Error: Command blocked by safety guard (shell nesting limit exceeded)"
                nested = self._check_workspace_restriction(inner, cwd, _depth=_depth + 1)
                if nested:
                    return nested

        return None

    # `$NAME` and `${NAME}`, the two spellings that carry a path. `$(`, `$$`
    # and `$?` are deliberately not names, so they fall through untouched.
    _VARIABLE = re.compile(r"\$(?:\{([A-Za-z_]\w*)([^}]*)\}|([A-Za-z_]\w*))")

    # The brace bodies that offer a second word the shell may substitute
    # instead of the value (`:-`, `:=`, `:?`, `:+` and their colon-less forms).
    _BRACE_WORD_OPERATOR = re.compile(r"^:?[-=?+]")

    # Suffix and prefix removal. These shorten a path, which walks it UP and
    # out of the workspace, so reading them as decoration beside an in-root
    # value is what let `${PWD%/*}/outside.txt` open the parent directory.
    _BRACE_TRIM = re.compile(r"^(##?|%%?)(.*)$", re.DOTALL)

    # How cmd.exe spells a variable, and ``DirectExecutor`` runs the platform
    # shell, so on Windows this is the ordinary spelling of an outside path.
    # Substituted only for a name the child is actually given, which is cmd's
    # own rule for an undefined one.
    _WINDOWS_VARIABLE = re.compile(r"%([A-Za-z_]\w*)%")

    # Whether the shell that will run the command reads that spelling at all.
    # `%VAR%` is cmd.exe syntax and means nothing to `sh`, which prints it --
    # and HOME, PWD, USER and TMPDIR are on the executor's allowlist on POSIX
    # too, so reading it there refused `echo '%HOME%/notes'`, which names no
    # path. A class attribute rather than a call to `os.name`, so the Windows
    # branch can be driven from a POSIX host.
    _WINDOWS_SHELL = os.name == "nt"

    # A `${` the name pattern does not accept. `${#NAME}` counts characters, so
    # whatever it yields is a number and cannot name a path; anything else here
    # is indirection, an array or a form not modelled, and gets refused.
    _BRACE_OTHER = re.compile(r"\$\{(#[A-Za-z_]\w*|[^}]*)\}")

    @staticmethod
    def _child_env(cwd: Path) -> dict[str, str]:
        """The environment the command will be run with.

        ``PWD`` is overridden because a shell sets it from the directory it is
        started in: this command's ``$PWD`` is the workspace, whatever this
        process inherited.
        """
        from raven.sandbox.direct_executor import baseline_env

        env = baseline_env()
        env["PWD"] = str(cwd)
        return env

    @classmethod
    def _steps_outside(
        cls,
        segments: list[tuple[list[str], str]],
        cwd: Path,
        roots: list[Path],
        env: dict[str, str],
    ) -> bool:
        """Whether the command leaves the workspace before doing its work.

        Reaching out and stepping out are the same escape, but only the first
        leaves a path for :meth:`_extract_absolute_paths` to find. A ``cd``
        whose destination is spelled out is already refused there, because
        ``/etc`` is a path like any other. What is left are the destinations
        the pattern cannot take: ``/`` has nothing after it, ``..`` is not
        absolute, and a ``cd`` with no argument names ``$HOME`` by saying
        nothing -- and after any of them, every relative path in the rest of
        the command resolves somewhere the operator did not allow.

        ``pushd`` moves the shell exactly as far and is read the same way. Its
        stack is what this cannot follow: ``popd`` and a bare ``pushd`` take
        their destination from entries earlier pushes already cleared, so the
        walk falls back to the shallowest of those, which is where the command
        began.

        A successful `cd` advances the paths on which it executes, so
        `cd subdir && cd ..` can return to its starting directory. An AND/OR
        chain can also skip that move while retaining a successful result:
        `x || cd a &&` keeps the directory where x succeeded. The successful
        and failed paths therefore travel separately. A move in a pipeline or
        group may leave the parent's directory unchanged, so a later step must
        stay inside from every retained position.

        A pipeline is one step of an AND/OR chain. Its members start from the
        directory before the pipeline; earlier members cannot change the
        parent's directory. The last member may run in the parent on shells
        with lastpipe semantics, so a move there retains both possibilities.
        """
        # AND/OR chains keep the last exit status when a command is skipped.
        # Track each outcome's directories separately: `x || cd a && cd ..`
        # can reach the final cd after x succeeds, without having entered a.
        passed = failed = [cwd]
        previous_separator = ""
        pipeline_start: list[Path] | None = None
        skipped_passed: list[Path] = []
        skipped_failed: list[Path] = []
        for raw_tokens, separator in segments:
            # Group terminators and following flow operators are separate
            # events: both the parent directory and the next branch matter.
            tokens = _unwrap_command_wrappers(raw_tokens)
            continues_pipe = separator in ("|", "|&")
            if pipeline_start is None:
                skipped_passed = passed if previous_separator == "||" else []
                skipped_failed = failed if previous_separator == "&&" else []
                start = (
                    passed
                    if previous_separator == "&&"
                    else failed
                    if previous_separator == "||"
                    else cls._either(passed, failed)
                )
            else:
                start = pipeline_start
            moved = start
            if tokens and tokens[0] in ("cd", "pushd", "popd"):
                moved = cls._moved(tokens, start, cwd, roots, env)
                if moved is None:
                    return True
            if continues_pipe:
                pipeline_start = start
                continue
            if pipeline_start is not None:
                # lastpipe can retain the last member's cd, and pipefail can
                # report failure even when that member succeeded.
                moved = start = cls._either(start, moved)
            pipeline_start = None
            passed = cls._either(skipped_passed, moved)
            failed = cls._either(skipped_failed, start)
            previous_separator = separator
            if len(cls._either(passed, failed)) > cls._MAX_WALK_POSITIONS:
                # Unreachable for a command a person or a model writes: the
                # list only grows on a `cd` into a directory no earlier step
                # named, and repeats collapse. Refusing past the bound keeps
                # the walk from being a place to spend the caller's time.
                return True
        return False

    @classmethod
    def _moved(
        cls,
        tokens: list[str],
        here: list[Path],
        cwd: Path,
        roots: list[Path],
        env: dict[str, str],
    ) -> list[Path] | None:
        """Where a directory builtin can leave the shell, or ``None`` to refuse.

        ``None`` is the refusal: a destination outside every root, reached from
        any of the places the shell can currently be.
        """

        if tokens[0] == "popd":
            # A stack return lands on a directory some earlier push put there,
            # and each of those was cleared on the way in. The shallowest of
            # them is where the command began.
            return cls._either(here, [cwd])
        arguments = list(tokens[1:])
        while arguments and arguments[0].startswith("-"):
            option = arguments.pop(0)
            if option == "--":
                break
            if option not in {"-L", "-P"}:
                return None
        if arguments and arguments[0] == "-":
            # Even after --, this operand reads mutable OLDPWD state.
            return None
        if not arguments and tokens[0] == "pushd":
            # A bare `pushd` swaps the top two entries rather than naming a
            # destination. Both were cleared on the way in, so the worst case
            # is the one `popd` gets.
            return cls._either(here, [cwd])
        target = arguments[0] if arguments else env.get("HOME", "")
        if not target:
            return here
        landings: list[Path] = []
        for start in here:
            try:
                destination = (start / Path(target).expanduser()).resolve()
            except Exception:
                return None
            if not any(root == destination or root in destination.parents for root in roots):
                return None
            landings.append(destination)
        return landings

    #: How many places the walk will hold at once; see the walk for why the
    #: list grows and why nothing real reaches this.
    _MAX_WALK_POSITIONS = 64

    @staticmethod
    def _either(*groups: list[Path]) -> list[Path]:
        """The places from every group, each once, in the order first seen."""

        seen: dict[Path, None] = {}
        for group in groups:
            for path in group:
                seen.setdefault(path, None)
        return list(seen)

    @staticmethod
    def _windows_value(env: dict[str, str], name: str) -> str | None:
        """``env``'s value for ``name`` the way cmd.exe finds it, or ``None``.

        cmd.exe resolves an environment name without regard to case, so an
        exact-key lookup leaves `%UserProfile%` standing and the path scan then
        sees no drive prefix. ``None`` means the child has no such name, which
        cmd answers by leaving the text as written.
        """
        if name in env:
            return env[name]
        folded = name.casefold()
        return next((value for key, value in env.items() if key.casefold() == folded), None)

    @classmethod
    def _resolve_brace(cls, value: str, body: str, construct: str, env: dict[str, str]) -> str:
        """The value a ``${...}`` yields, or a refusal when that cannot be known.

        Two bodies are resolved. A word operator offers a second word the shell
        may substitute instead of the value, and which one it picks depends on a
        value this cannot read -- so both are emitted, the operator becoming a
        space so the word it introduces starts on a boundary the scan can take.
        A trim shortens the value, which is the case that matters: it walks a
        path UP, so reading it as decoration beside an in-root value is what let
        ``${PWD%/*}/outside.txt`` open the parent.

        Every other body is refused. Substitution, case folding and indirection
        can each hand the command a path this cannot compute, and allowing them
        would make the fence's promise depend on which spellings happen to be
        implemented -- which is the defect, not a smaller version of it.
        """
        if not body:
            return value
        # The body is inserted after this walk has passed the position it lands
        # in, so nothing would read it again. The shell does read it, which is
        # why it goes back through the same pass here rather than out as text:
        # `${UNSET:-$HOME/secret}` is that path, and leaving it unread was the
        # one spelling the resolved-or-refused rule claimed and did not cover.
        if cls._BRACE_WORD_OPERATOR.match(body):
            word = cls._as_the_shell_reads_it(cls._BRACE_WORD_OPERATOR.sub("", body), env)
            return f"{value} {word}"
        trim = cls._BRACE_TRIM.match(body)
        if trim is not None:
            pattern = cls._as_the_shell_reads_it(trim.group(2), env)
            return cls._trim(value, trim.group(1), pattern)
        raise _UnmodelledExpansionError(construct)

    @staticmethod
    def _trim(value: str, operator: str, pattern: str) -> str:
        """``value`` with the matching prefix or suffix removed, as the shell does.

        A doubled operator takes the longest match and a single one the
        shortest, which is why each direction is walked from its own end. No
        match leaves the value alone, exactly as the shell leaves it.
        """
        from_end = operator.startswith("%")
        longest = len(operator) == 2
        if from_end:
            cuts = range(0, len(value) + 1) if longest else range(len(value), -1, -1)
            for cut in cuts:
                if fnmatch.fnmatchcase(value[cut:], pattern):
                    return value[:cut]
            return value
        cuts = range(len(value), -1, -1) if longest else range(0, len(value) + 1)
        for cut in cuts:
            if fnmatch.fnmatchcase(value[:cut], pattern):
                return value[cut:]
        return value

    @classmethod
    def _as_the_shell_reads_it(cls, command: str, env: dict[str, str]) -> str:
        """``command`` with its parameters expanded, as the child will expand them.

        The scan below reads text, so it can only refuse a path it can see, and
        a name is where a path hides: ``$HOME/.ssh/id_rsa`` put a letter in
        front of the ``/`` that :meth:`_extract_absolute_paths` looks for, and
        the whole command went through as naming no path at all. Expanding
        first is what puts the path back in view -- expanding afterwards, which
        is what this used to do, only ever saw candidates the scan had already
        passed.

        Three rules, each taken from the shell rather than invented:

        - **An unknown name is nothing.** ``$NOPE/etc/shadow`` *is*
          ``/etc/shadow``, and it is the case with no literal spelling to fall
          back on.
        - **The child's environment decides.** ``DirectExecutor`` hands over an
          allowlisted baseline, so a name outside that list arrives unset
          however this process reads it; ``os.environ`` would answer for a
          variable the command will never have.
        - **Single quotes expand nothing.** ``echo 'keys go in $HOME/.ssh'`` is
          a sentence, and refusing it would teach the operator to turn the
          fence off.

        A sandboxed executor runs somewhere with its own environment, which
        nothing here can read. The values are a host-side approximation there;
        the rule that carries is the first one, which needs no value to hold.
        """
        out: list[str] = []
        quote = ""
        index = 0
        while index < len(command):
            char = command[index]
            if char == "%" and cls._WINDOWS_SHELL:
                # Ahead of the quote branches on purpose: cmd.exe has no
                # quoting that suppresses this, so a run it would expand must
                # not be hidden here by POSIX quoting rules.
                windows_match = cls._WINDOWS_VARIABLE.match(command, index)
                if windows_match is not None:
                    value = cls._windows_value(env, windows_match.group(1))
                    if value is not None:
                        out.append(value)
                        index = windows_match.end()
                        continue
            if quote == "'":
                out.append(char)
                if char == "'":
                    quote = ""
                index += 1
                continue
            if char == "\\" and index + 1 < len(command):
                escaped = command[index + 1]
                # An escaped `$` is a literal `$` that this level does not
                # expand -- but the character still travels on, and a nested
                # shell handed it does expand it. Keeping the backslash here
                # hid `sh -c "sh -c 'cat \\$HOME/x'"` from the nested scan,
                # because the payload it was given no longer looked like a
                # name. Every other escape keeps both characters: only these
                # two decide whether something expands.
                out.append(escaped if escaped in "$`" else char + escaped)
                index += 2
                continue
            if quote == '"' and char == '"':
                quote = ""
                out.append(char)
                index += 1
                continue
            if not quote and char in "'\"":
                quote = char
                out.append(char)
                index += 1
                continue
            if char == "$":
                name_match = cls._VARIABLE.match(command, index)
                if name_match is not None:
                    value = env.get(name_match.group(1) or name_match.group(3), "")
                    out.append(cls._resolve_brace(value, name_match.group(2) or "", name_match.group(0), env))
                    index = name_match.end()
                    continue
                if command.startswith("${", index):
                    other = cls._BRACE_OTHER.match(command, index)
                    if other is None:
                        # Nesting runs past the first `}`, which this does not
                        # parse. The contract answers that with a refusal.
                        raise _UnmodelledExpansionError(command[index : index + 32])
                    if not other.group(1).startswith("#"):
                        raise _UnmodelledExpansionError(other.group(0))
                    # A character count. Whatever it yields is a number, and a
                    # number cannot name a path, so it needs no value here.
                    out.append("0")
                    index = other.end()
                    continue
            out.append(char)
            index += 1
        return "".join(out)

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        # The boundary class must cover every character a path can be glued
        # to, not just whitespace: --file=/etc/passwd, </etc/passwd,
        # cmd;/bin/x, $(/usr/bin/id) and `/bin/x` all name a path with no
        # space before it, and a boundary the class misses is a fence bypass.
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"=<;(`])(/[^\s\"'>;|<]+)", command)
        home_paths = re.findall(r"(?:^|[\s|>'\"=<;(`])(~[^\s\"'>;|<]*)", command)
        return win_paths + posix_paths + home_paths

```

## raven/agent/tools/tool_search.py

Source: https://github.com/EverMind-AI/Raven/blob/62bc88803384522ec35be4771836c69b39b3a8d2/raven/agent/tools/tool_search.py

SHA-256: `7833ef9753992ef116c4e3d5d60e72eec09900f3a10c16e18c39f5b257660c81`

```
"""Progressive tool disclosure for large catalogs.

When built-ins + plugins + MCP push the tool count past a threshold, injecting
every schema into each request burns context that scales with tool count. This
module withholds most schemas and exposes two meta-tools instead:

  - ``tool_search`` — BM25 keyword search over the hidden catalog; each hit
                      carries name + description + parameter schema, enough to
                      call the tool without a second lookup.
  - ``tool_call``   — invoke a cataloged tool by name; forwards through the
                      registry, which validates arguments and returns a
                      correctable error when they don't fit the schema.

The tool list sent to the model never changes turn-to-turn (always the core
set + these two meta-tools), so the prompt cache stays stable across the
whole session — tools sit ahead of system+messages in the cached prefix, so a
changing tool list would invalidate everything after it. The cost is that
cataloged tools are invoked through ``tool_call`` rather than native
function-calling.

Two visibility tiers per turn (see :class:`ToolSearchStrategy`):
  - always-visible: a core set + the meta-tools (full schema every turn);
  - cataloged:      everything else — searchable, schema withheld until asked.

``tool_call`` outlives that feature switch. Progressive disclosure is one reason
a tool's schema may be absent from a request; ``ToolRegistry.hide_from_schema``
is another, and it applies at every catalog size. A schema-hidden tool is
dispatchable — ``ToolRegistry.execute`` never consults the schema — but a model
can only reach it by naming it, and ``tool_call`` is the only place it can. So
the host registers ``tool_call`` unconditionally and gates only ``tool_search``
and the fold on ``tools.tool_search.enabled``.
"""

from __future__ import annotations

import json
from itertools import zip_longest
from typing import TYPE_CHECKING, Any, NamedTuple

from raven.agent.tools.registry import absent_tool_error
from raven.agent.tools.tool_index import ToolIndex, rank_tools
from raven.contracts.token_strategy import TokenStrategy
from raven.contracts.tool import Tool
from raven.i18n import zh_lexicon

if TYPE_CHECKING:
    from raven.agent.tools.registry import ToolRegistry

# Core tools kept exposed every turn — the agent would be crippled having to
# search for these. Beyond the file/search/exec primitives, ``message``,
# ``ask_user`` and ``spawn`` are interaction/orchestration primitives the agent
# must reach on any turn (reply, unblock via a question, delegate a subagent) —
# hiding them risks the model not thinking to search for them at all.
#
# Deliberately NOT here: ``run_subagent_dag`` (a fan-out of minute-scale
# sub-agent runs is a deliberate act, not a per-turn primitive; it should be
# reached through ``tool_search`` like any other optional capability) and
# ``read_skill``. Under compaction that costs a ``tool_search`` hop before a
# ``inject: description`` skill's body can be loaded — accepted, since the
# alternative is keeping every progressive-disclosure entry point resident.
# Config ``tools.tool_search.always_visible`` adds either one back per deploy.
DEFAULT_ALWAYS_VISIBLE: tuple[str, ...] = (
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "grep",
    "find",
    "exec",
    "message",
    "ask_user",
    "spawn",
    # Resident because the refs it cashes in are already in the window: a ref
    # the model can read but whose tool sits behind a search is a promise the
    # turn has to spend a hop to keep. Registered only beside a strategy that
    # mints refs, so elsewhere this name matches nothing.
    "context_retrieve",
    # Resident because the host's post-turn call forces it by name: a forced
    # tool the fold had hidden is a request the provider refuses. Registered on
    # the host only, so in any other process this name matches nothing.
    "task_board",
)

TOOL_CALL_NAME: str = "tool_call"
TOOL_SEARCH_NAME: str = "tool_search"
# The meta-tools. Never cataloged; ``tool_call`` is registered whether or not
# the fold is on, ``tool_search`` only when it is (see ``ToolSearchStrategy``).
META_TOOL_NAMES: frozenset[str] = frozenset({TOOL_SEARCH_NAME, TOOL_CALL_NAME})


class _Target(NamedTuple):
    """What one ``tool_call`` name resolves to. Exactly one field is set."""

    tool: "Tool | None"
    refusal: str | None


class ToolSearchController:
    """Shared state between the meta-tools and the strategy.

    Holds the live registry (the catalog source of truth) and the BM25 index.
    The visible set is constant (core + meta-tools), which keeps the per-turn
    tool list — and thus the prompt cache — stable.
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        *,
        always_visible: set[str],
        search_result_limit: int = 10,
        compaction_threshold: int = 50,
    ) -> None:
        self._registry = registry
        # The configured spellings, kept as configured. Resolved to registered
        # names on every read instead of once here -- see visible_names.
        self._configured_visible = set(always_visible)
        self.search_result_limit = search_result_limit
        # The single home for the fold threshold: the strategy's per-turn
        # filter and :meth:`tool_call_available` both read it from here, so an
        # advertisement can never promise a route the fold is not shipping.
        self.compaction_threshold = compaction_threshold
        self._index = ToolIndex()

    def _catalog_tools(self) -> list[Tool]:
        """All registered tools except the meta-tools (never self-searchable).

        Deliberately not channel-filtered: the BM25 index is keyed on this set
        and shared by every concurrent turn, so making it channel-dependent
        would have web and IM turns swapping the index out from under each
        other. The channel filter belongs on the reads instead -- ``search``
        and ``resolve_target``.
        """
        out = []
        for name in self._registry.tool_names:
            # Schema-hidden tools are advertised by their owning tool's result
            # text, not by search; indexing them would give them a second,
            # uninvited discovery path (tool_call still resolves them).
            if name in META_TOOL_NAMES or name in self._registry.schema_hidden_names():
                continue
            tool = self._registry.get(name)
            if tool is not None:
                out.append(tool)
        return out

    def refresh(self) -> None:
        """Sync the BM25 index with the current registry (no-op if unchanged)."""
        self._index.ensure(self._catalog_tools())

    def visible_names(self) -> set[str]:
        """Configured names resolved against the registry, per call.

        Not resolved once in ``__init__``, for two reasons that both point the
        same way. MCP servers attach while raven runs, so a name configured here
        may not be registered yet when this object is built. And a namespaced
        tool's registered name need not equal the configured one: sanitising and
        the length cap rewrite it, so a config entry written against the old
        spelling would silently stop keeping its tool resident -- one extra
        search hop for the model, and no error anywhere to say why.

        A configured name that resolves to nothing is kept as written. Such an
        entry usually names a tool this deploy does not have, which is ordinary,
        and dropping it would only hide the typo case rather than report it.
        """
        out = set(META_TOOL_NAMES)
        for entry in self._configured_visible:
            out.update(self._registry.resolve_configured(entry) or [entry])
        return out

    def search(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Hits carry name + description + parameter schema, so the model can go
        straight to tool_call without a separate describe round-trip.

        Rank, then filter, then truncate -- in that order. Truncating first lets
        a tool this channel cannot use consume a result slot, so a usable
        lower-ranked hit is dropped and the model is told nothing matched.
        Reading that many names is free: :meth:`_ranked_names` scores and sorts
        the whole catalog either way, and only the final slice is bounded.
        """
        cap = limit or self.search_result_limit
        ranked = self._ranked_names(query, cap)
        # Asked once for the whole scan, not once per candidate: the source
        # behind it is a file read, and the loop below walks the entire ranked
        # catalog. One answer for the scan is also the more correct one -- a set
        # re-read mid-loop could offer a tool and withhold its neighbour from
        # the same list.
        withheld = self._registry.withheld_names()
        hits = []
        for name in ranked:
            tool = self._registry.get(name)
            if tool is None or not self._registry.offers(tool, withheld):
                continue
            hits.append(
                {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            )
            if len(hits) >= cap:
                break
        return hits

    def _ranked_names(self, query: str, cap: int) -> list[str]:
        """The catalog ranked for this query, plus the tools this turn's session brought.

        The bound on the indexed half is the registry's current size, not the
        index's -- it stays >= the indexed catalog because ``refresh()`` runs at
        the top of every ``before_llm_call``, ahead of any search. Unregistering
        a tool after the last refresh is the one state that inverts that and
        truncates the ranking ahead of the caller's filter.

        Session tools are ranked beside the index rather than in it, for the
        reason :meth:`_catalog_tools` gives about the channel filter: the index
        is keyed on the catalog and shared by every concurrent turn, so making
        it session-dependent would have two sessions' turns swapping it out from
        under each other. They are a small dict and need no index of their own.

        Merged by rank, not by score: the two rankings come from different
        corpora, and BM25's idf falls with corpus size, so comparing the numbers
        would bury a session tool under every catalog hit however well it
        matched. Alternating keeps the catalog's own hit first while still
        getting every session hit in ahead of ``cap``. A session tool shadowing
        a registered name keeps the indexed position -- ``get`` resolves that
        name to the session's tool anyway, so the hit already describes it.
        """
        indexed = self._index.search(query, len(self._registry.tool_names) or cap)
        session = [
            tool
            for name, tool in self._registry.session_tools_in_scope().items()
            # Same two exclusions the indexed catalog is built with.
            if name not in META_TOOL_NAMES and name not in self._registry.schema_hidden_names()
        ]
        if not session:
            return indexed
        seen = set(indexed)
        extra = [name for name in rank_tools(query, session, cap) if name not in seen]
        merged: list[str] = []
        for pair in zip_longest(indexed, extra):
            merged.extend(name for name in pair if name is not None)
        return merged

    def resolve_target(self, name: Any) -> _Target:
        """What ``tool_call`` would forward this name to, and why not when it would not.

        One resolution with two readers. Splitting it meant walking the same
        three checks twice -- the meta-tool guard, the registry lookup, the
        channel filter -- so exactly one of ``tool`` and ``refusal`` is set.

        The reason is part of the return rather than the caller's to reconstruct,
        because the two ways a name fails want different answers:

        - not a string, or a meta-tool: refused here rather than resolved, or
          ``tool_call`` would recurse into itself.
        - absent, or withheld from this channel: one answer for both, and the
          same sentence the direct path uses. Withheld is answered as absent
          deliberately -- "it exists but not for you" is itself the way around
          the filter that keeping it out of the schema was meant to close.
          Appending where the catalog is is safe for that branch because
          ``search`` filters by channel too. It reads as a statement of where
          the truth lives, not an instruction to search again: a model that
          searched, called what it found, and lost the tool in between would
          otherwise be sent round the same loop. It is appended only where
          ``tool_search`` is actually registered: ``tool_call`` ships without it
          wherever the fold is off, and naming a tool that is not there is the
          same broken promise this whole route exists to close.
        """
        if not isinstance(name, str):
            return _Target(None, "Error: 'name' must be a string naming a tool.")
        if name in META_TOOL_NAMES:
            return _Target(None, f"Error: '{name}' cannot be invoked via tool_call.")
        tool = self._registry.get(name)
        if tool is None or not self._registry.offers(tool):
            tail = " -- tool_search lists what is currently loaded" if self.search_visible() else ""
            return _Target(None, absent_tool_error(name, tail=tail))
        return _Target(tool, None)

    def search_visible(self) -> bool:
        """Whether ``tool_search`` reaches the model's tool list this turn.

        Public because it is also the honest gate on anything the host says
        about the fold: ``_mcp_tool_notices`` tells the model a connected
        server's schemas are withheld, and that sentence is true only while
        this is. The old shape of :meth:`tool_call_available`, kept but moved to
        the meta-tool it is actually true of. ``tool_search`` is registered only
        where the fold is configured on, withheld by the same off switch as
        anything else, and dropped from the request again below the threshold --
        so a pointer at it is dishonest in three separate states, not one.
        """
        if TOOL_SEARCH_NAME not in self._registry.tool_names:
            return False
        if TOOL_SEARCH_NAME in self._registry.withheld_names():
            return False
        catalog = sum(1 for t in self._registry.get_definitions() if t["function"]["name"] not in META_TOOL_NAMES)
        return catalog > self.compaction_threshold

    def tool_call_available(self) -> bool:
        """Whether ``tool_call`` is in the model's per-turn tool list right now.

        The predicate behind the DAG acceptance text's advertisement of the
        schema-hidden control tools: it must agree with what
        :class:`ToolSearchStrategy` actually ships, or the text promises a
        route that does not exist.

        The catalog size is deliberately not part of this any more. ``tool_call``
        is the only way a model can name a tool that is not in the schema, so it
        rides in every request and the fold keeps it at any catalog size -- which
        leaves only the two things an operator can still take away: the off
        switch, and a host that never registered it.
        """
        if TOOL_CALL_NAME not in self._registry.tool_names:
            return False
        return TOOL_CALL_NAME not in self._registry.withheld_names()

    def target_is_blocking(self, name: Any, arguments: Any = None) -> bool:
        """Whether the tool ``tool_call`` would forward to is a blocking interaction.

        A meta-tool target is refused by :meth:`call`, so it reports non-blocking
        here too — which also stops the registry lookup recursing back into this
        controller.

        ``arguments`` travel with the question because a target's verdict may turn
        on them: `resolve_dag_node` blocks only for a bound foreground run, which
        it can tell only from ``run_id``. Asked without them it answered "not
        blocking" for every call, and the registry put its default ceiling on a
        wait that has none. A JSON string is tolerated the way :meth:`call`
        tolerates it; anything that is not an object counts as no arguments.
        """
        if not isinstance(name, str) or name in META_TOOL_NAMES:
            return False
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = None
        return self._registry.is_blocking(name, arguments if isinstance(arguments, dict) else {})

    async def call(self, name: str, arguments: dict[str, Any] | None) -> str:
        """Invoke a cataloged tool: forward to the registry (validates args).

        Models sometimes emit the nested ``arguments`` as a JSON string rather
        than an object; parse that case so the call still goes through.
        """
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return "Error: 'arguments' must be a JSON object."
        target = self.resolve_target(name)
        if target.refusal is not None:
            return target.refusal
        return await self._registry.execute(name, arguments or {})


class ToolSearchTool(Tool):
    """Keyword search over tools whose schemas are not currently loaded."""

    def __init__(self, controller: ToolSearchController) -> None:
        self._ctrl = controller

    @property
    def name(self) -> str:
        return "tool_search"

    @property
    def description(self) -> str:
        return (
            "Search the catalog of additional tools that are available but not "
            "currently loaded. Returns matching tools with their description and "
            "parameter schema, ready to invoke with tool_call. Query with task "
            f"keywords, e.g. 'create github issue' or '{zh_lexicon.TOOL_SEARCH_QUERY_EXAMPLE}'."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Task keywords describing the capability you need.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of results.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, limit: int | None = None) -> str:
        hits = self._ctrl.search(query, limit)
        if not hits:
            return f"No tools matched '{query}'. Try broader or different keywords."
        return json.dumps(hits, ensure_ascii=False)


class ToolCallTool(Tool):
    """Invoke a cataloged tool by name. Arguments are validated by the registry."""

    def __init__(self, controller: ToolSearchController) -> None:
        self._ctrl = controller

    @property
    def name(self) -> str:
        return TOOL_CALL_NAME

    @property
    def description(self) -> str:
        return (
            "Invoke a tool by name that is not in your tool list, passing its "
            "arguments. The name may come from tool_search, from another tool's "
            "result, or from a report or notice you were sent that named the call "
            "to make. If the arguments don't fit the tool's schema the registry "
            "returns a validation error describing the fix; adjust and call again."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Exact tool name, from a tool_search result, from a tool result that "
                        "named it, or from a report or notice that named the call to make."
                    ),
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments object for the target tool.",
                },
            },
            "required": ["name"],
        }

    def blocking_for(self, params: dict[str, Any]) -> bool:
        return self._ctrl.target_is_blocking(params.get("name"), params.get("arguments"))

    def metadata_owner(self, params: dict[str, Any]) -> Tool:
        return self._ctrl.resolve_target(params.get("name")).tool or self

    async def execute(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return await self._ctrl.call(name, arguments)


def _schema_name(entry: Any) -> str | None:
    """The name a tool definition carries, or ``None`` for a shape this strategy
    cannot read.

    The arrays raven assembles are always well formed; replay is what is not.
    It feeds a recorded array back through this same chain, so a corrupt
    recording arrives here as an entry whose ``function`` is a string -- and
    that has to end at replay's own backstop as an unreplayable recording,
    not as a TypeError thrown from a strategy that assumed the shape. An entry
    this cannot read is counted as a tool and passed through untouched: the
    fold may only withhold a schema it recognised.
    """
    function = entry.get("function") if isinstance(entry, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    return name if isinstance(name, str) else None


class ToolSearchStrategy(TokenStrategy):
    """``before_llm_call`` hook that compacts the tool list for large catalogs.

    At or below ``compaction_threshold`` tools it passes through unchanged apart
    from dropping ``tool_search``, which has nothing to find while every schema
    is already in the request. Above it, only the always-visible core +
    meta-tools keep their schema; the rest stay reachable via ``tool_search`` /
    ``tool_call``.

    ``tool_call`` survives both branches. It is not only the fold's dispatcher:
    it is the sole route by which a model can name a schema-hidden tool, and
    those exist independently of whether this deploy folds anything.
    """

    def __init__(self, controller: ToolSearchController, *, compaction_threshold: int | None = None) -> None:
        self._ctrl = controller
        # The controller's threshold unless a caller overrides it, so the fold
        # and the controller's availability predicate share one source.
        self._compaction_threshold = (
            compaction_threshold if compaction_threshold is not None else controller.compaction_threshold
        )

    @property
    def name(self) -> str:
        return "tool_search"

    async def before_llm_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, str]:
        if not tools:
            return messages, tools, model
        self._ctrl.refresh()
        catalog_size = sum(1 for t in tools if _schema_name(t) not in META_TOOL_NAMES)
        if catalog_size <= self._compaction_threshold:
            out = [t for t in tools if _schema_name(t) != TOOL_SEARCH_NAME]
            return messages, out, model
        present = {name for t in tools if (name := _schema_name(t)) is not None}
        if not META_TOOL_NAMES <= present:
            # Meta-tools unavailable (e.g. removed via disabled_tools): expose
            # everything rather than strand the cataloged tools behind a search
            # the model cannot invoke.
            return messages, tools, model
        visible = self._ctrl.visible_names()
        out = [t for t in tools if (name := _schema_name(t)) is None or name in visible]
        return messages, out, model

```
