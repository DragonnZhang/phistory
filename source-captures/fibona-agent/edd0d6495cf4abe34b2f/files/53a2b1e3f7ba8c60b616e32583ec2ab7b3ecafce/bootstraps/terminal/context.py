"""Editable working instructions and context helpers for the terminal bootstrap."""

import inspect
import types
from functools import partial
from pathlib import Path

from core import agent as core_agent
from core import env as core_env

CONTEXT_INSTRUCTIONS = """
Your working instructions, variables() and build_context() are Python state in this session.
Their original implementation is in source_code['bootstraps/terminal/context.py']; you can replace them in later cells.
source_code also contains the core runtime source. Read relevant excerpts and include them in input when needed.
Use call_me(input=[*build_context(), {"role": "user", "content": observations}]) to send these instructions and your selected context.
Keep useful data and task-specific helpers in session variables; update var_docs to describe them.
startup_task preserves the initial task. Maintain task_state before each call_me():
update goal and constraints when the user steers the task, and completed and pending as work progresses.
Record verified outcomes in completed; planned work belongs in pending. build_context() carries all fields on every call.
History summaries record intent; use cell output and errors to determine what actually happened.
""".strip()

# Names the host or startup put in, as opposed to what cells defined; `variables` hides them.
_EXCLUDED_NAMES = {"task", "call_me", "call_llm_api", "quit", "history", "input", "source_code"}


def initialize_context(namespace):
    """Set up working state and return a context builder bound to the live REPL namespace.

    Merges into instructions, source_code and var_docs that earlier bootstrap code already set.
    """
    namespace.update(
        startup_task=namespace["task"],  # Preserve the initial task when the current goal changes.
        task_state={"goal": namespace["task"], "constraints": [], "completed": [], "pending": []},
        source_code={
            **namespace.get("source_code", {}),
            "core/agent.py": inspect.getsource(core_agent),
            "core/env.py": inspect.getsource(core_env),
            "bootstraps/terminal/context.py": Path(__file__).read_text(encoding="utf-8"),
        },
        instructions="\n\n".join(part for part in (namespace.get("instructions", ""), CONTEXT_INSTRUCTIONS) if part),
        RECENT=namespace.get("RECENT", 10),  # Finished task cells in the default context; others stay in history.
        var_docs={  # Variable name -> description, maintained by the agent in this session.
            **namespace.get("var_docs", {}),
            "startup_task": "Original task; preserve when the current goal changes.",
            "openai_responses_api_docs": "Complete official OpenAI Responses API reference (page -> Markdown). Search/read excerpts on demand.",
            "task_state": "Current goal, constraints, verified completed work and pending work; update before call_me().",
        },
        variables=partial(variables, namespace),
    )
    return partial(build_context, namespace)


def variables(namespace):
    """What cells have defined: name -> type, with the length of containers. Cells may replace this policy."""
    hidden = namespace["get_ipython"]().user_ns_hidden
    found = {}
    for name, value in namespace.items():
        if name.startswith("_") or name in hidden or name in _EXCLUDED_NAMES:
            continue
        if isinstance(value, types.ModuleType):
            continue
        kind = type(value).__name__
        try:
            kind += f"[{len(value)}]"
        except Exception:
            pass
        found[name] = kind
    return found


def build_context(namespace):
    """Select context from current session variables and the host's refreshed history.

    Cells can replace the bound build_context and variables helpers, working instructions,
    var_docs and RECENT. The original host records are only read here.

    Returns the working instructions as a developer message, then the context as a user message.

    Example (cell 14; `report` has no description in `var_docs`; the user message):
        Startup task: Summarize the Hacker News front page
        Current goal: Summarize the Hacker News front page
        Constraints: []
        Completed: ['fetched 30 pages']
        Pending: ['write report.md']
        Working directory: /home/user/work
        Current task cell: 14
        Variables: pages: dict[30] (url -> html, fetched live), failed: list[2], report: str
        cell 0: Start the task. [ok]
        cell 1: Fetch the front page [failed: HTTPError: HTTP Error 403: Forbidden]
        cell 2: Fetch with a browser User-Agent [ok]
        Recent task cells:
        ### cell 12 · Summarize each page in one line
        code:
        summaries = [call_llm_api("One line:\\n" + html).output_text for html in pages.values()]
        output:
        Summarized 30 pages.

        ### cell 13 · Write the report
        code:
        open("report.md", "w").write(render(summaries))
        error:
        Traceback (most recent call last):
        ...
        NameError: name 'render' is not defined
    """
    history = namespace["history"]
    var_docs = namespace["var_docs"]
    task_state = namespace["task_state"]
    described = [
        f"{name}: {kind}" + (f" ({var_docs[name]})" if var_docs.get(name) else "")
        for name, kind in namespace["variables"]().items()
    ]
    lines = [
        f"Startup task: {namespace['startup_task']}",
        f"Current goal: {task_state['goal']}",
        f"Constraints: {task_state['constraints']!r}",
        f"Completed: {task_state['completed']!r}",
        f"Pending: {task_state['pending']!r}",
        f"Working directory: {Path.cwd()}",
        f"Current task cell: {len(history) - 1}",
        "Variables: " + (", ".join(described[:200]) or "none"),
    ]
    # Older records stay accessible by index; summarize their intent and outcome here.
    start = max(0, len(history) - namespace["RECENT"])
    for number, cell in enumerate(history[:start]):
        result = f"failed: {cell['error'].strip().splitlines()[-1]}" if cell["error"] else "ok"
        lines.append(f"cell {number}: {cell['summary']} [{result}]")
    blocks = []
    for number, cell in enumerate(history[start:], start=start):
        parts = [f"### cell {number} · {cell['summary']}", "code:", cell["code"].strip()]
        if cell["output"].strip():
            output = cell["output"].strip()
            # Keep short output intact; for long output keep the head and tail and point to the full text.
            if len(output) > 1000:
                output = output[:500] + f"\n[Full output: history[{number}]['output']]\n" + output[-500:]
            parts += ["output:", output]
        if cell["error"]:
            parts += ["error:", cell["error"].strip()]
        blocks.append("\n".join(parts))
    context = "\n".join([*lines, "Recent task cells:", "\n\n".join(blocks) or "none yet"])
    return [{"role": "developer", "content": namespace["instructions"]}, {"role": "user", "content": context}]
