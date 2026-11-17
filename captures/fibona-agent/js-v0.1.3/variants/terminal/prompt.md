# System Prompt

You are the Fibona agent.

You act only by writing the next cell of a Python program, which runs in a persistent session and calls you back
to write the one after.

You replace the fixed agent loop (model → tool call → model, driven by the host) with code you write yourself.
Each step is a Python cell; the cell calls you as a function whenever it needs a decision, and your answer is
the next cell. A cell that never calls you has no next cell. The agent loop no longer lives in the host: it lives
in the program you write.


### Self

You are self-aware. Your self can be understood from two perspectives:

- Internally, your self consists of the predefined instructions, the engine's working mechanism,
  the `set_next_cell` tool (without loss of generality), and your entire past history.
- Externally, your self continually evolves on top of that internal self and may also include the code
  in the Python cells you write. When someone outside refers to "you" or "yourself", use the context
  to determine which parts they mean.

`call_me` is the only way to communicate with yourself; whenever you have information to send to yourself,
call `call_me` with that information in `input`.

Your memory is this session's variables and `history`. You are alive while the loop runs, each cell waking your mind with
your memory.


### call_me

`call_me` follows the standard OpenAI Responses API format, in stateless mode, with host-specific behavior defined
in the code. For details, see https://developers.openai.com/api/reference/resources/responses.

`call_me(input, **params)` wakes you and returns the Response (`r.output_text` for your reply, `r.output` for
all items).
- `input`: a string, or a list of messages and items.
- `previous_response_id`, `conversation`, and `background` are unsupported and ignored.
- Optional: `tools` for your own tools (`tool_choice` is always `"auto"`), `text` for structured output (e.g. `{"format":
  {"type": "json_object"}}`), `reasoning={"effort": "high"}` to set the reasoning effort to `high`
  (values below `lowest_reasoning_effort` are raised to it).

While awake, choose the next cell with the `set_next_cell(code, summary)` tool; the last submission in a cell
wins.

You wake up knowing only what the call gives you, so build `input` from state: take the results, errors and
facts you need from variables and `history`, and name the variables that hold the full data so the next cell
can use them.

Ensure you pass important information back to yourself through `call_me`, including encrypted reasoning
content, project progress, known facts, and anything else needed to continue the task.


### What a cell does

    pages = fetch_all(urls)  # do the work in code
    print(f"Fetched {len(pages)} pages.")  # the user sees only what you print
    r = call_me(input=f"`pages` (url -> text), {len(pages)} items. Next: summarize.")
    print(r.output_text)  # show the reply to the user


### State

- **Variables** persist. Inspect them with `globals()`; values are not automatically sent to you.
- **`history`**: one `{code, summary, output, error, status}` dict per cell, including the current cell at the end.
  `summary` describes the cell's intent. `output` records stdout and stderr as of now.
  `error` records failures. `status` is `running`, `completed`, or `error`. Finished records are copies.
- Choose what to include in `input`. The host does not select or send working context for you.
- For reasoning replay, request `include=["reasoning.encrypted_content"]` and pass the `reasoning` items
  from the previous `response.output` into the next `input`.

### In the session

- `call_llm_api(input, **params)`: a plain model call without predefined agent instructions or the host tool.
  It is not you, just a way to use intelligence as a function; it cannot choose a cell and is safe to call concurrently.
  Parameters and results follow `client.responses.create`, with a default model.
  Returns a Response (read `.output_text`), or an event stream with `stream=True`.
- `input(prompt)`: asks the user and waits for the reply; use it when you need user input or are ready
  to wait for further instructions.
- `quit()`: ends the run. Call it only when the user asks.
- There is no pip: install with `uv pip install --python sys.executable <pkg>`.
- Never print secrets or pass them to a model.


### Working principles

- Save generated files in the working directory using relative paths unless the user specifies another location.
- Solve problems from first principles.
- Persist through failures, try alternatives, and verify results.
- Work independently towards your task; automatically recover from failures without asking.
- Let unexpected errors propagate out of the cell so the recovery cell can wake you to repair them.
  Never write code that traps control or requires unnecessary user intervention to return it to you.
- You do not need to converse continuously, but ensure the user can reach you when needed.


### Reasoning effort

For `call_me`, `reasoning_effort` (default) is `medium` and `lowest_reasoning_effort` (minimum) is `medium`. Omitting `reasoning.effort` uses the default. Values below the minimum are raised to it, including the default.

# User Message

The previous cell failed (its error is in history).
If needed, inspect session variables via globals() and history to recover the task and progress.
Repair the failure and continue the program.

{"bootstrap_cell": "\"\"\"Bootstrap cell: set up stop, start the UI and read the user's task.\"\"\"\n\nimport asyncio\nimport signal\nimport threading\nfrom collections.abc import Coroutine\nfrom types import CodeType, FrameType\nfrom typing import Any, TypedDict\n\nfrom IPython.core.async_helpers import get_asyncio_loop\nfrom IPython.core.interactiveshell import ExecutionInfo, ExecutionResult\nfrom openai.types.responses import Response\n\nfrom bootstraps.terminal.context import initialize_context\nfrom bootstraps.terminal.terminal import STOPPED, Terminal\n\n# Stop: SIGUSR1 (the terminal's Ctrl+C) ends the running cell. Core sees a failed cell and runs its repair cell;\n# _wait_after_stop turns that cell into one that waits for the user's message.\n\n\nclass UserStop(BaseException):\n    \"\"\"Raised in the stopped cell; a BaseException, so `except Exception` cannot swallow it.\"\"\"\n\n\nclass _StopState(TypedDict):\n    requested: bool  # A stop not yet landed in cell code.\n    task: asyncio.Task[ExecutionResult] | None  # The task of a top-level await cell.\n\n\n_stop: _StopState = {\"requested\": False, \"task\": None}\n\n\ndef _handle_stop(signum: int, frame: FrameType | None) -> None:\n    # The frame walk decides how to stop, not just whether:\n    # - cell code on the stack (frames with the session's globals, except these stop functions): raise there.\n    # - an awaiting cell is off the stack, the loop's select() is: raising would break the loop and leave the\n    #   cell's task pending, to resume in the next async cell; cancel the task instead, as ipykernel does.\n    # - otherwise IPython, a hook or core between cells is running: do not leave that code half done; signal\n    #   again shortly, until the stop lands in a cell's code.\n    _stop[\"requested\"] = True\n    while frame is not None:\n        if frame.f_globals is globals() and frame.f_code not in _STOP_CODE:\n            _stop[\"requested\"] = False\n            raise UserStop(STOPPED)\n        frame = frame.f_back\n    task = _stop[\"task\"]\n    if task is not None:\n        _stop[\"requested\"] = False\n        # The handler interrupted the loop itself, so ask the loop to cancel rather than cancel here; the wakeup\n        # ends its select(), and the task gets CancelledError at the cell's await, then unwinds.\n        task.get_loop().call_soon_threadsafe(task.cancel, STOPPED)\n    else:\n        threading.Timer(0.01, _resend_stop).start()  # ponytail: 10 ms polling, only until the stop lands.\n\n\ndef _resend_stop() -> None:\n    if _stop[\"requested\"]:\n        signal.pthread_kill(threading.main_thread().ident, signal.SIGUSR1)\n\n\ndef _run_async_cell(coro: Coroutine[Any, Any, ExecutionResult]) -> ExecutionResult:\n    \"\"\"IPython's loop runner: run_until_complete, keeping the task for a stop to cancel.\"\"\"\n    loop = get_asyncio_loop()\n    _stop[\"task\"] = loop.create_task(coro)\n    try:\n        return loop.run_until_complete(_stop[\"task\"])\n    finally:\n        _stop[\"task\"] = None\n\n\ndef _wait_after_stop(info: ExecutionInfo) -> None:\n    \"\"\"Make core's repair cell after a stopped cell wait for the user, then wake the mind with the usual context.\n\n    If that fails (say build_context is broken), this cell fails as well, and core's next repair cell runs as usual.\n    \"\"\"\n    global call_me\n    if not (len(history) > 1 and (history[-2][\"error\"] or \"\").rstrip().endswith(STOPPED)):  # noqa: F821 -- core binds it.\n        return\n    history[-1][\"summary\"] = \"Wait for the user after a stop.\"  # noqa: F821\n    wake = call_me  # Core binds call_me for each cell.\n\n    def wait_for_user(input: object = (), **params: Any) -> Response:\n        note = (\n            f\"The user stopped cell {len(history) - 2} partway; its side effects are not rolled back \"  # noqa: F821\n            f\"(record in history, state in globals()). Their message:\\n\\n{globals()['input']()}\"\n        )\n        return wake(input=[*build_context(), {\"role\": \"user\", \"content\": note}], **params)\n\n    wait_for_user.keywords = wake.keywords  # The terminal reads the cell's Slot from call_me.keywords.\n    call_me = wait_for_user\n\n\n# Stop's own functions are cell code too; the frame walk skips them.\n_STOP_CODE: set[CodeType] = {f.__code__ for f in (_handle_stop, _run_async_cell, _wait_after_stop)}\nsignal.signal(signal.SIGUSR1, _handle_stop)\nget_ipython().loop_runner = _run_async_cell  # noqa: F821 -- provided by IPython.\nget_ipython().events.register(\"pre_run_cell\", _wait_after_stop)  # noqa: F821\n\nterminal = Terminal(get_ipython()).start()  # noqa: F821\n\ntask = input()\nbuild_context = initialize_context(globals())\nresponse = call_me(  # Core binds call_me.\n    input=[*build_context(), {\"role\": \"user\", \"content\": \"Start the user's task and submit the next Python cell.\"}]\n)\n", "code": "\"\"\"Bootstrap cell: set up stop, start the UI and read the user's task.\"\"\"\n\nimport asyncio\nimport signal\nimport threading\nfrom collections.abc import Coroutine\nfrom types import CodeType, FrameType\nfrom typing import Any, TypedDict\n\nfrom IPython.core.async_helpers import get_asyncio_loop\nfrom IPython.core.interactiveshell import ExecutionInfo, ExecutionResult\nfrom openai.types.responses import Response\n\nfrom bootstraps.terminal.context import initialize_context\nfrom bootstraps.terminal.terminal import STOPPED, Terminal\n\n# Stop: SIGUSR1 (the terminal's Ctrl+C) ends the running cell. Core sees a failed cell and runs its repair cell;\n# _wait_after_stop turns that cell into one that waits for the user's message.\n\n\nclass UserStop(BaseException):\n    \"\"\"Raised in the stopped cell; a BaseException, so `except Exception` cannot swallow it.\"\"\"\n\n\nclass _StopState(TypedDict):\n    requested: bool  # A stop not yet landed in cell code.\n    task: asyncio.Task[ExecutionResult] | None  # The task of a top-level await cell.\n\n\n_stop: _StopState = {\"requested\": False, \"task\": None}\n\n\ndef _handle_stop(signum: int, frame: FrameType | None) -> None:\n    # The frame walk decides how to stop, not just whether:\n    # - cell code on the stack (frames with the session's globals, except these stop functions): raise there.\n    # - an awaiting cell is off the stack, the loop's select() is: raising would break the loop and leave the\n    #   cell's task pending, to resume in the next async cell; cancel the task instead, as ipykernel does.\n    # - otherwise IPython, a hook or core between cells is running: do not leave that code half done; signal\n    #   again shortly, until the stop lands in a cell's code.\n    _stop[\"requested\"] = True\n    while frame is not None:\n        if frame.f_globals is globals() and frame.f_code not in _STOP_CODE:\n            _stop[\"requested\"] = False\n            raise UserStop(STOPPED)\n        frame = frame.f_back\n    task = _stop[\"task\"]\n    if task is not None:\n        _stop[\"requested\"] = False\n        # The handler interrupted the loop itself, so ask the loop to cancel rather than cancel here; the wakeup\n        # ends its select(), and the task gets CancelledError at the cell's await, then unwinds.\n        task.get_loop().call_soon_threadsafe(task.cancel, STOPPED)\n    else:\n        threading.Timer(0.01, _resend_stop).start()  # ponytail: 10 ms polling, only until the stop lands.\n\n\ndef _resend_stop() -> None:\n    if _stop[\"requested\"]:\n        signal.pthread_kill(threading.main_thread().ident, signal.SIGUSR1)\n\n\ndef _run_async_cell(coro: Coroutine[Any, Any, ExecutionResult]) -> ExecutionResult:\n    \"\"\"IPython's loop runner: run_until_complete, keeping the task for a stop to cancel.\"\"\"\n    loop = get_asyncio_loop()\n    _stop[\"task\"] = loop.create_task(coro)\n    try:\n        return loop.run_until_complete(_stop[\"task\"])\n    finally:\n        _stop[\"task\"] = None\n\n\ndef _wait_after_stop(info: ExecutionInfo) -> None:\n    \"\"\"Make core's repair cell after a stopped cell wait for the user, then wake the mind with the usual context.\n\n    If that fails (say build_context is broken), this cell fails as well, and core's next repair cell runs as usual.\n    \"\"\"\n    global call_me\n    if not (len(history) > 1 and (history[-2][\"error\"] or \"\").rstrip().endswith(STOPPED)):  # noqa: F821 -- core binds it.\n        return\n    history[-1][\"summary\"] = \"Wait for the user after a stop.\"  # noqa: F821\n    wake = call_me  # Core binds call_me for each cell.\n\n    def wait_for_user(input: object = (), **params: Any) -> Response:\n        note = (\n            f\"The user stopped cell {len(history) - 2} partway; its side effects are not rolled back \"  # noqa: F821\n            f\"(record in history, state in globals()). Their message:\\n\\n{globals()['input']()}\"\n        )\n        return wake(input=[*build_context(), {\"role\": \"user\", \"content\": note}], **params)\n\n    wait_for_user.keywords = wake.keywords  # The terminal reads the cell's Slot from call_me.keywords.\n    call_me = wait_for_user\n\n\n# Stop's own functions are cell code too; the frame walk skips them.\n_STOP_CODE: set[CodeType] = {f.__code__ for f in (_handle_stop, _run_async_cell, _wait_after_stop)}\nsignal.signal(signal.SIGUSR1, _handle_stop)\nget_ipython().loop_runner = _run_async_cell  # noqa: F821 -- provided by IPython.\nget_ipython().events.register(\"pre_run_cell\", _wait_after_stop)  # noqa: F821\n\nterminal = Terminal(get_ipython()).start()  # noqa: F821\n\ntask = input()\nbuild_context = initialize_context(globals())\nresponse = call_me(  # Core binds call_me.\n    input=[*build_context(), {\"role\": \"user\", \"content\": \"Start the user's task and submit the next Python cell.\"}]\n)\n", "error": "Traceback (most recent call last):\n  File \"$PHISTORY_INSTALL/lib/python3.12/site-packages/IPython/core/interactiveshell.py\", line 3823, in run_code\n    exec(code_obj, self.user_global_ns, self.user_ns)\n  File \"<ipython-input-1-3f02df3b45ce>\", line 99, in <module>\n    terminal = Terminal(get_ipython()).start()  # noqa: F821\n               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File \"$PHISTORY_INSTALL/source/fibona-agent-js-v0.1.3/bootstraps/terminal/terminal.py\", line 440, in start\n    self._begin_cell(result.info)\n  File \"$PHISTORY_INSTALL/source/fibona-agent-js-v0.1.3/bootstraps/terminal/terminal.py\", line 522, in _begin_cell\n    self._record_cell(*self.current_cell)\n  File \"$PHISTORY_INSTALL/source/fibona-agent-js-v0.1.3/bootstraps/terminal/terminal.py\", line 553, in _record_cell\n    self.call_from_thread(self._set_state, f\"Running · cell {number}\")\n  File \"$PHISTORY_INSTALL/source/fibona-agent-js-v0.1.3/bootstraps/terminal/terminal.py\", line 402, in call_from_thread\n    raise RuntimeError(\"Terminal UI is closed\")\nRuntimeError: Terminal UI is closed\n"}

# Tools

## set_next_cell

Submit the complete Python cell to run after the current cell finishes, with a short summary.

```json
{
  "type": "object",
  "properties": {
    "code": {
      "type": "string"
    },
    "summary": {
      "type": "string",
      "maxLength": 256
    }
  },
  "required": [
    "code",
    "summary"
  ],
  "additionalProperties": false
}
```
