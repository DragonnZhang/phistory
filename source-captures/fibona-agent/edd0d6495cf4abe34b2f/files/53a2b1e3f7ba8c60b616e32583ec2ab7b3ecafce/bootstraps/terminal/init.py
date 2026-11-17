"""Bootstrap cell: set up stop, start the UI and read the user's task."""

import asyncio
import signal
import threading
from collections.abc import Coroutine
from types import CodeType, FrameType
from typing import Any, TypedDict

from IPython.core.async_helpers import get_asyncio_loop
from IPython.core.interactiveshell import ExecutionInfo, ExecutionResult
from openai.types.responses import Response

from bootstraps.terminal.context import initialize_context
from bootstraps.terminal.terminal import STOPPED, Terminal

# Stop: SIGUSR1 (the terminal's Ctrl+C) ends the running cell. Core sees a failed cell and runs its repair cell;
# _wait_after_stop turns that cell into one that waits for the user's message.


class UserStop(BaseException):
    """Raised in the stopped cell; a BaseException, so `except Exception` cannot swallow it."""


class _StopState(TypedDict):
    requested: bool  # A stop not yet landed in cell code.
    task: asyncio.Task[ExecutionResult] | None  # The task of a top-level await cell.


_stop: _StopState = {"requested": False, "task": None}


def _handle_stop(signum: int, frame: FrameType | None) -> None:
    # The frame walk decides how to stop, not just whether:
    # - cell code on the stack (frames with the session's globals, except these stop functions): raise there.
    # - an awaiting cell is off the stack, the loop's select() is: raising would break the loop and leave the
    #   cell's task pending, to resume in the next async cell; cancel the task instead, as ipykernel does.
    # - otherwise IPython, a hook or core between cells is running: do not leave that code half done; signal
    #   again shortly, until the stop lands in a cell's code.
    _stop["requested"] = True
    while frame is not None:
        if frame.f_globals is globals() and frame.f_code not in _STOP_CODE:
            _stop["requested"] = False
            raise UserStop(STOPPED)
        frame = frame.f_back
    task = _stop["task"]
    if task is not None:
        _stop["requested"] = False
        # The handler interrupted the loop itself, so ask the loop to cancel rather than cancel here; the wakeup
        # ends its select(), and the task gets CancelledError at the cell's await, then unwinds.
        task.get_loop().call_soon_threadsafe(task.cancel, STOPPED)
    else:
        threading.Timer(0.01, _resend_stop).start()  # ponytail: 10 ms polling, only until the stop lands.


def _resend_stop() -> None:
    if _stop["requested"]:
        signal.pthread_kill(threading.main_thread().ident, signal.SIGUSR1)


def _run_async_cell(coro: Coroutine[Any, Any, ExecutionResult]) -> ExecutionResult:
    """IPython's loop runner: run_until_complete, keeping the task for a stop to cancel."""
    loop = get_asyncio_loop()
    _stop["task"] = loop.create_task(coro)
    try:
        return loop.run_until_complete(_stop["task"])
    finally:
        _stop["task"] = None


def _wait_after_stop(info: ExecutionInfo) -> None:
    """Make core's repair cell after a stopped cell wait for the user, then wake the mind with the usual context.

    If that fails (say build_context is broken), this cell fails as well, and core's next repair cell runs as usual.
    """
    global call_me
    if not (len(history) > 1 and (history[-2]["error"] or "").rstrip().endswith(STOPPED)):  # noqa: F821 -- core binds it.
        return
    history[-1]["summary"] = "Wait for the user after a stop."  # noqa: F821
    wake = call_me  # Core binds call_me for each cell.

    def wait_for_user(input: object = (), **params: Any) -> Response:
        note = (
            f"The user stopped cell {len(history) - 2} partway; its side effects are not rolled back "  # noqa: F821
            f"(record in history, state in globals()). Their message:\n\n{globals()['input']()}"
        )
        return wake(input=[*build_context(), {"role": "user", "content": note}], **params)

    wait_for_user.keywords = wake.keywords  # The terminal reads the cell's Slot from call_me.keywords.
    call_me = wait_for_user


# Stop's own functions are cell code too; the frame walk skips them.
_STOP_CODE: set[CodeType] = {f.__code__ for f in (_handle_stop, _run_async_cell, _wait_after_stop)}
signal.signal(signal.SIGUSR1, _handle_stop)
get_ipython().loop_runner = _run_async_cell  # noqa: F821 -- provided by IPython.
get_ipython().events.register("pre_run_cell", _wait_after_stop)  # noqa: F821

terminal = Terminal(get_ipython()).start()  # noqa: F821

task = input()
build_context = initialize_context(globals())
response = call_me(  # Core binds call_me.
    input=[*build_context(), {"role": "user", "content": "Start the user's task and submit the next Python cell."}]
)
