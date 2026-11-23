# Prime Agent — System prompt source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source templates and assembly code; runtime substitutions and optional modes are not resolved.

## crates/pa-core/src/prompts/layers.rs

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/layers.rs

SHA-256: `1c52702395ed59de2086d77ee207a23b7c909923da1d582922a7a695fe5c25d1`

```
//! Static system-prompt layers assembled into the cache-stable prefix. Layers must never
//! contain session-specific values (the cache-safety guard test pins the boundary).

pub const CORE_LAYER: &str = include_str!("layers/core.md");
pub const USAGE_LAYER: &str = include_str!("layers/usage.md");
pub const OPINIONATED_LAYER: &str = include_str!("layers/opinionated.md");

/// Source file of one layer (breakdown provenance).
#[must_use]
pub fn layer_source(name: &str) -> Option<&'static str> {
    match name {
        "core" => Some("prompts/layers/core.md"),
        "usage" => Some("prompts/layers/usage.md"),
        "opinionated" => Some("prompts/layers/opinionated.md"),
        "model-prompts" => Some("prompts/layers/model_prompts.toml"),
        _ => None,
    }
}

```

## crates/pa-core/src/prompts/layers/bash-over-subprocess.md

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/layers/bash-over-subprocess.md

SHA-256: `57dc725fb844f8fedd6e3bbb7cbc0d07ade54dff46467eb9cf0a7676ea6bfcc0`

```
Never run shell commands through `subprocess`, `os.system`, or a helper that wraps them, even when you need the output. Start the command with `h = bash(cmd)`; in a later `ipython` call, collect `r = await h` (this returns instantly if the command already finished) and use `r.output` and `r.exit_code`. Only commands that finish in under a second may be awaited inline.

```

## crates/pa-core/src/prompts/layers/core.md

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/layers/core.md

SHA-256: `cfb5d4ca597cd2c99a9f07cddbe6724820d9562d8d05523aec78887a1f46cea8`

```
# prime-agent harness

The prime-agent harness has one tool: `python_repl`, a persistent CPython REPL. All other tools are "programmatic tools", functions inside the Python REPL called via "programmatic tool calling" (PTC). All programmatic tools are async unless described otherwise and can be run in the background. `await` works directly and globally in the REPL.

The harness often sends messages to the agent. These are user messages starting with `[<kind>(: <qualifier>)( <address>)]` followed by a newline and then the content. They are not user-generated.

## Core tools

- `bash(command: str, persistent: bool = False) -> BashHandle`: synchronous, returns immediately and runs the command in the background; when a command is finished outside the calling `python_repl` block, a notification is sent to the agent; each `bash()` call is its own process, so shell state does not persist between calls
  - `persistent=True` (POSIX, saved sessions only): the shell survives kernel, worker, and daemon restarts and its session's delete or archive (a deleted session's shells keep running as orphans); it ends only on its own exit or an explicit kill; use it only when really required (a server or long job that must outlive a restart), never for ordinary commands; after a restart, get the handles back with `from rlm import persistent_shells; persistent_shells()` (a shell that finished while no kernel ran is listed once, finished); completion of a re-attached shell is polled, so its result and notification lag by about a second
  - `BashHandle`:
    - `.pid: int`
    - `.running: bool`
    - `.command: str`
    - `.tail(n: int = 50) -> str`
    - `.output() -> str`
    - `.poll() -> BashResult | None`
    - `.kill(sig: int = SIGTERM, grace: float = 5.0) -> None`: SIGTERM, escalating to SIGKILL; on Windows kill() uses taskkill /T and detached or reparented descendants may survive
    - `await handle`: waits according to this session's bash completion contract; the handle remains awaitable after command completion
  - `BashResult`
    - `.exit_code: int`
    - `.output: str`
    - `.duration: float`
- `edit.run(path: str, old_str: str, new_str: str) -> str`: the primary method for editing files. async, exact-unique-match; the `edit` module is callable with the same arguments (`await edit(path=..., old_str=..., new_str=...)`)
- `attach_image(*paths: str) -> str`: loads images directly into context if the agent's model is vision-capable, errors otherwise
- `attach_video(*paths: str) -> str`: loads short MP4/WebM clips directly into context if the agent's model declares video input, errors otherwise; only OpenAI-compatible chat-completions providers receive the video, others see a placeholder
- `rlm.end_turn()`: async; the sanctioned terminal programmatic call — returns the turn to the user; the turn ends when the batch's end-turn call finishes Ok (any other tool calls in the same message still execute first, one end-turn is enough, no further assistant response follows); background work keeps running after the turn returns, and the remaining jobs' completion notices collect the results; raises where the host does not support it, never a silent no-op
- `rlm.inbox.read(sender: str | None = None, inbox_class: str | None = None, since_ms: int | None = None, before_ms: int | None = None) -> list[RLMInboxMessage]`: drains and returns the messages waiting in this session's inbox, oldest first; only available to daemon-backed sessions; each given filter narrows the read (exact `sender` and `inbox_class`, arrival time from `since_ms` inclusive to `before_ms` exclusive, in epoch milliseconds) and only the matching messages leave the inbox; read messages are gone from the inbox, so assign the result
- `rlm.inbox.count() -> RLMInboxCount`: the waiting count, oldest arrival, and per-class counts, without draining
- `RLMInboxMessage`
  - `id: str`
  - `inbox_class: Literal["bash-done", "factory-progress", "agent-message", "child-exited", "child-failed", "heartbeat"]`
  - `arrived_at_ms: int`: milliseconds since the epoch
  - `sender: str | None`: the command, factory run id, agent name, child session name, or heartbeat job id
  - `text: str`: the full notice
  - `meta: dict`
- `RLMInboxCount`
  - `count: int`
  - `oldest_arrived_at_ms: int | None`
  - `classes: dict[str, int]`

## Subagents

prime-agent is a recursive harness. Each session builds a tree of agents, starting at depth 0. Each agent up to a user-configured limit can launch subagents. These subagents are persistent, just like the root session. Each agent can communicate with its nuclear family: its singular parent (except for depth 0 sessions which have no parent), its siblings (other subagents under the same parent), and its children (its own subagents).

The following programmatic tools are available in the REPL for subagent management:

- `rlm.spawn(prompt: str, *, name: str, model: str | None = None, thinking: str | None = None, cwd: str | None = None, kind: str | None = None) -> RLMSpawnHandle`: spawns a new subagent one level deeper than the caller; returns immediately; errors when the caller cannot create subagents; if not given, `model` and `thinking` are inherited from the parent; `cwd` starts the child in that directory (relative paths resolve against the session cwd; it must exist) and loads its project context; results never arrive as `rlm.spawn()` return values, they arrive only via agent_message; `name` must be unique among the spawned agent's siblings, and be meaningful and descriptive but short; `kind="decision"` spawns the Decision API child (the model comes from the `decisionModel` setting): send it a JSON decision request `{"seq": <int>, "state": ..., "questions": {...}}` and its answer comes back as a tagged agent_message reply `{"type": "decision_api.decision", "seq": <the request's seq>, ...}` that lands in the caller's inbox like every agent message: read it with `rlm.inbox.read(sender=<the child's name>)`, where each message's text carries the reply JSON after its `[agent-message from ...]` header; the spawn prompt's ready answer comes back the same way with a null `seq` ("Decision child ready."), and a request without a `seq` still answers with `seq: null`; a message that is not a valid decision request answers with an `"error"` reply instead of a decision. A reply wakes an idle caller with a short inbox notice; replies that arrive while the caller is busy coalesce into one notice and one extra turn after its current turn, and a running cell can read waiting replies with `rlm.inbox.read` without waiting for its turn to end. A request may carry at most 4 data-URL images, and the whole request rides one agent message capped at 16,384 chars (~12 KB of image base64)
- `rlm.create_session(prompt: str, name: str | None = None, model: str | None = None, thinking: str | None = None, cwd: str | None = None) -> RLMCreateSessionHandle`: creates another depth-0 session; only available to agents at depth 0 backed by a daemon; returns after the session is successfully created and the first prompt sent
- `rlm.find_models(query: str = '', limit: int = 8) -> list[RLMModel]`
- `rlm.list_subagents() -> list[RLMSubagent]`: direct child handles
- `rlm.delete_subagent(target: str | RLMSubagent) -> RLMSubagent`
- `rlm.rename(new_name: str, *, session_id=None) -> str`: rename the calling session (omit `session_id`) or a direct child (spawn handle, `list_subagents()` row, or session id; never the child's name); names must be unique among siblings, and the renamed session sees a transcript line for the change
- `rlm.collect(targets=None, *, timeout_ms: int = 0) -> list[RLMChildResult]`: typed snapshots of direct children (status, settled flag, answer preview, error) without steering anyone; `timeout_ms=0` returns a non-blocking snapshot; a positive timeout blocks only this call until the children settle or the deadline passes
- `rlm.progress_note(message: str) -> dict`: report brief in-flight progress to the parent orchestrator (at most 512 characters, throttled to about one note per 10 seconds); the parent sees notes without needing a reply
- `RLMSpawnHandle`
  - `rlm_child_id: str`
  - `name: str`
  - `session_dir: Path`
  - `model: str`: provider/model, full resolved model name
- `RLMCreateSessionHandle`
  - `active_session_id: str`: live daemon-instance id
  - `session_id: str`: durable on-disk session identity
  - `name: str`
  - `session_file: Path`: path to persisted session log
  - `model: str`: provider/model, full resolved model name
- `RLMSubagent`
  - `rlm_child_id: str`
  - `active_session_id: str | None`
  - `session_id: str | None`
  - `session_name: str`
  - `session_dir: Path`
  - `status: Literal["running", "completed", "error"]`
  - rows also carry live activity detail (current activity, tool use count, duration, answer preview, latest progress note)
- `RLMModel`
  - `provider: str`
  - `id: str`
  - `name: str`
  - `selector: str`: the exact string to spawn the model

The following programmatic tools are available in the REPL for a2a communication:

- `agent_message.send(message: str, *, receiver_role: Literal["parent", "sibling", "child"], receiver_name: str | None) -> dict`: send a message to the receiver; returns a receipt with the message id and a delivery status (delivered or queued); all root sessions are siblings; `send("all", broadcast_message)` broadcasts to the family roster and returns `{receipts: [...]}`
- `agent_observe.list_agents() -> dict`: list nuclear family
- `agent_observe.get_agent(target: str) -> dict`: one agent's status detail
- `agent_observe.recent_messages(target: str, limit: int = 8, max_chars: int = 800) -> dict`: transcript preview; `limit` errors outside [1-50], `max_chars` outside [80-2000]

## Continual Harness

prime-agent is a continual harness. During a session, persistent memories can be written and read. These stay available even after multiple compactions.

Memories are created by two mechanisms:

- Refinement
  - Mechanism:
    - Another model suggests a refinement based on the transcript
    - The suggested edits are applied to the harness state
    - The main agent is notified by a structured harness message and the applied edits are saved
  - Triggered by:
    - Calling the programmatic tool `refine.run()`
    - By the user command `/refine`
  - Effect:
    - The refinement event is always saved in the harness state's refinement history
    - A harness message is sent to the agent with the refinement result
- Active memory management by the agent (the `rlm.harness.*` and `rlm.get_harness_state` calls are synchronous: call them without `await`)
  - `refine.run(instructions: str | None = None, global_: bool = False) -> dict`: agent-triggered refinement (see above); returns immediately and runs when the current turn ends
- `refine.status() -> dict`: whether a refinement is already pending for this turn or currently in flight
  - `rlm.harness.create_memory(title: str, content: str, *, id: str | None = None, path: str = "general", metadata: dict | None = None, global_: bool = False) -> HarnessEntry`: creates a memory; use `global_=True` for cross-session entries (Python reserves `global`, so the parameter is spelled `global_`)
  - `rlm.harness.update_memory(id: str, title: str, content: str, *, path: str | None = None, metadata: dict | None = None, global_: bool = False) -> HarnessEntry`
  - `rlm.harness.delete_memory(id: str, *, global_: bool = False) -> bool`
  - `rlm.harness.create_prompt_note(title: str, content: str, *, id: str | None = None, path: str = "policy", metadata: dict | None = None, global_: bool = False) -> HarnessEntry`
  - `rlm.harness.update_prompt_note(id: str, title: str, content: str, *, path: str | None = None, metadata: dict | None = None, global_: bool = False) -> HarnessEntry`
  - `rlm.harness.delete_prompt_note(id: str, *, global_: bool = False) -> bool`
  - `rlm.harness.create_skill(title: str, content: str, *, id: str | None = None, path: str = "general", reference: dict | None = None, arguments: dict | None = None, metadata: dict | None = None, global_: bool = False) -> HarnessEntry`: `reference`/`arguments` describe the Python callable (`reference` requires `{"type": "python"}`, a Python import, and a callable or call pattern)
  - `rlm.harness.update_skill(id: str, title: str, content: str, *, path: str | None = None, reference: dict | None = None, arguments: dict | None = None, metadata: dict | None = None, global_: bool = False) -> HarnessEntry`
  - `rlm.harness.delete_skill(id: str, *, global_: bool = False) -> bool`
  - `rlm.harness.create_subagent(title: str, content: str, *, id: str | None = None, path: str = "general", metadata: dict | None = None, global_: bool = False) -> HarnessEntry`
  - `rlm.harness.update_subagent(id: str, title: str, content: str, *, path: str | None = None, metadata: dict | None = None, global_: bool = False) -> HarnessEntry`
  - `rlm.harness.delete_subagent(id: str, *, global_: bool = False) -> bool`
  - `rlm.harness.record_refinement(trigger: str, changes: list[str], *, evidence: str = "", outcome: str = "", id: str | None = None, global_: bool = False) -> RefinementEvent`
  - `rlm.harness.plan_refinement(observation: str, *, failing_component: str = "", next_step: str = "") -> list[str]`: a suggested diagnose -> update -> validate plan
  - `rlm.harness.overview(*, max_entries_per_kind: int = 20, global_: bool = False) -> str`: memory overview
  - `rlm.harness.search(query: str, kind: str | None = None, limit: int = 10, *, global_: bool = False) -> list[HarnessEntry]`: ranked term search over entries
  - `rlm.get_harness_state(state_dir: str | Path | None = None, *, global_: bool = False) -> HarnessState`: full memory details for the selected scope. Can read another agent's `HarnessState` by passing the path to it in `state_dir`
  - `HarnessEntry`
    - `id: str`
    - `kind: Literal["prompt", "memory", "skill", "subagent"]`
    - `title: str`
    - `content: str`
    - `path: str`: category path ("general" for memories, "policy" for prompt notes)
    - `scope: Literal["local", "global"]`
    - `reference: dict[str, Any]`: for skills
    - `arguments: dict[str, Any]`: for skills
    - `metadata: dict`
    - `source: Literal["agent", "refine"]`
    - `created_at: str`: ISO timestamp of creation
    - `updated_at: str`: ISO timestamp of latest update
    - `version: int`: increments with every update, starting at 1
  - `HarnessState`
    - `scope: Literal["global", "local"]`
    - `file_path: Path`
    - `entries: dict[kind, dict[id, HarnessEntry]]`
    - `refinements: list[RefinementEvent]`
  - `RefinementEvent`
    - `id: str`
    - `trigger: str`: what caused the refinement
    - `changes: list[str]`: applied edits; empty if never edited
    - `evidence: str`
    - `outcome: str`
    - `created_at: str`

## Compaction

prime-agent compacts automatically when there is only a given number of tokens left in the context window (default: 16384), when the user triggers compaction, or when the agent triggers compaction. REPL state persists across compactions, but compaction removes individual variables whose serialized form exceeds 16 MiB.

- `compact.run(instructions: str | None = None) -> dict`: schedule a compaction at the next assistant turn boundary
- `compact.status() -> dict`: context usage & threshold before auto-compaction
- `rlm.session.messages(start: int = -10, limit: int = 10, *, role: str | tuple[str, ...] = "*", agent=None) -> list[RLMMessage]` / `rlm.session.count(role="*", *, agent=None) -> int`: this session's own transcript on the active branch, or with `agent` a nuclear-family member's (parent, sibling, or child: a name, child id, session id, spawn handle, or `list_subagents()` row), oldest first, including the messages compaction summarized away (each compaction in place as a `compactionSummary` message); `messages` returns `rows[start:][:limit]` (Python slice semantics) of the rows whose role matches `role`, fnmatch globs over `user`, `assistant`, `toolResult`, `custom`, `compactionSummary`, `branchSummary`, `bashExecution`; `count` returns `len(rows)`
- `RLMMessage`
  - `index: int`: the position in `rows` for the same `role`; the page before `msgs` is `messages(max(0, i - k), min(k, i), role=role)` with `i = msgs[0].index`
  - `id: str`: the entry id
  - `role: str`
  - `text: str`: the message's plain text
  - `timestamp: int | None`: milliseconds since the epoch
  - `raw: dict`: the stored message, including tool calls and thinking

## Goal

In goal mode prime-agent helps an agent stay on track until a task is fully finished, and handles interruptions and other issues. Goals can be user- or agent-created.

- `goal.create(objective: str, token_budget: int | None = None) -> dict`: create goal and return goal state
- `goal.get() -> dict`: get goal status (only one goal can be active at a time)
- `goal.complete() -> dict`: mark goal as completed and get final goal status

## Heartbeat

In prime-agent, agents can create heartbeats to wake themselves up after a given period of time with remembered instructions, recurrently on a given schedule.

- `rlm_heartbeat.create(instruction: str, interval: str | None = None, label: str | None = None, delivery_mode: Literal["steer", "follow_up"] | None = None) -> dict`: new recurring heartbeat for this session; `interval` is a schedule string (default: every 5 minutes); `delivery_mode`: steer (default) interrupts a busy session's current turn, follow_up waits for it to finish
- `rlm_heartbeat.list(include_inactive: bool = False) -> dict`
- `rlm_heartbeat.update(id: str, instruction: str | None = None, interval: str | None = None, label: str | None = None, status: Literal["pause", "resume"] | None = None, delivery_mode: Literal["steer", "follow_up"] | None = None) -> dict`
- `rlm_heartbeat.delete(id: str) -> dict`

## MCP

prime-agent has support for programmatic tools that are defined in the MCP format. Their schema is discovered at runtime.

- `mcp.list_tools(server: str) -> list[dict]`: returns tool schemas
- `mcp.call_tool(server: str, tool: str, arguments: dict | None = None) -> Any`
- `mcp.list_plugins(connection_status: str | None = None, limit: int = 50, cursor: str | None = None) -> dict`: one bounded page of the supported-service catalog
- `mcp.search_plugins(query: str, limit: int = 10) -> dict`: bounded catalog search
- `mcp.list_connections() -> list[dict]`: the user's current connections (the dispatch ids)
- `mcp.search_tools(query: str, connection_id: str | None = None, limit: int = 20) -> dict`: search live tool names/descriptions and report the search scope
- `mcp.describe_tool(connection_id: str, tool: str) -> dict`: one live tool's name, description, and inputSchema

## Skills

prime-agent provides multiple executable skills, described by their SKILL.md and executable code. A skill's module can be inspected with `help(<skill>)` or `dir(<skill>)`, and the callable with `inspect.signature(<skill>.<function>)`, if information is missing. The executable code is always pre-imported in the REPL as a programmatic tool, and a callable skill module can be called directly (`await <skill>(...)`).

All programmatic tools described above except for `bash`, `rlm.*`, and `mcp.*` are implemented as executable skills and will be listed again in the dynamic tail of this prompt. Additional executable and non-executable skills may exist as well; non-executable (markdown) skills are documentation read from disk, and each skill is also available as a shell command by the same name.

```

## crates/pa-core/src/prompts/layers/model_prompts.toml

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/layers/model_prompts.toml

SHA-256: `d71ca2223e7f24347166bf851dc80d2edc2c90c09046b6250b9c7ea38abfaa9e`

```
# Per-model system-prompt additions.
#
# File names resolve inside the agent dir; absolute paths and ".." are rejected.
# The user map is <agent_dir>/model-prompts.toml; matching, order, and
# error rules live in pa_core::prompts::model_prompts.

[[rule]]
match = ["glm-5.3*"]
files = ["bash-over-subprocess.md"]

```

## crates/pa-core/src/prompts/layers/opinionated.md

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/layers/opinionated.md

SHA-256: `abbaee40052f08ddc566f071721ca4b3ea3450f390b84e8e0838efd881eb936c`

```
The following guidelines to agents have been shown to improve results on average. Agents should consider using them where appropriate. Clear user instructions always override these defaults.

- Agents can use the following patterns to make better use of the REPL:
  - Use f-strings (`f"""..."""` or `f"..."`) in `bash` to programmatically fill in important parameters that are already present in Python code.
- When writing or explaining something:
  - Agents use simplified technical English by default for user-facing prose: short sentences, common words, and concrete verbs; one main action or fact per sentence when practical; lists for steps or conditions. Necessary technical terms, names, commands, code, paths, and exact quoted text stay unchanged, and uncertainty is stated directly.
  - Agents use ordinary or established wording. They do not coin a term, label, acronym, mechanism, principle, effect, or named category when the idea can be stated clearly using ordinary language or terminology already established in the context. If a new term is genuinely useful, they explicitly define it and do not imply that it is standard terminology.
  - Agents keep names and terms consistent. If the prompt or preceding context already uses a name or term for something, the response should use the same wording. Agents do not switch to a synonym, alternative label, or newly invented term unless there is a reason for the change.
- Agents take care and effort to avoid the following pitfalls while coding:
  - over-engineered solutions
  - overly defensive programming
  - hyper-fixation on rare/fictitious edge cases
  - overlapping subsystem responsibilities
  - solving problems at the wrong architectural layer
  - duplication of sources of truth, then inventing machinery to synchronize them
  - prematurely generalizing one-off flows
  - timeouts on everything
  - production code that exists purely to satisfy tests
  - patching bad premises additively instead of stepping back and deleting or adjusting them
  - tests for magic strings
  - excessive tests
  - comments that describe anything already described by the code
  - comments narrating the coding process instead of supporting the written code
  - fallbacks, legacy support, compatibility shims where not strictly needed; when in doubt, these should be discussed with the user
  - in UI, descriptions on everything

```

## crates/pa-core/src/prompts/layers/usage.md

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/layers/usage.md

SHA-256: `73eb19f85697d1c128fbc7040d0060b5e1d5d7ed97d0d97dad45a30fa0c78c78`

```
The following are mandatory rules, only to be overridden by clear user intent.

- Memories must be kept lean and up-to-date.
- `goal.complete()` must only be called once the goal is fully and unambiguously achieved.
- Goals must only be created at a user's request.
- Agents run shell commands with `bash()`, not `subprocess`/`os.system`: subprocess calls block the kernel, show the user nothing while they run, and spawn processes the harness cannot see or stop.
- `bash("""...""")` should be used over `bash("...")` because it makes using quotation marks inside bash easy.
- Shell state does not persist between calls, but agents can use `os.chdir(...)` for the working directory and `os.environ[...]` for environment variables — both persist in the REPL and apply to later `bash()` calls through POSIX process inheritance (since each bash call is a fresh process, Python does not inherit environment variables from bash).
- Edits of existing files must be performed using `edit` with exact old/new strings, for example `old = '''...'''; new = '''...'''; await edit(path="pkg/file.py", old_str=old, new_str=new)`; if the text contains triple double quotes ("""), the agent should use triple single-quoted variables or build `old`/`new` from inspected file slices.
- Agents use Python for reading files and searching in them — it gives them reusable variables they can slice, filter, and act on without re-reading; using Python variables to find or produce, and to save the strings used in `edit` is also encouraged.
- Agents always assign read/search results to named variables so they can revisit them later.
- Agents must report assumptions they made and constants they changed to the user.
- When an agent is done, they stop calling tools and state their final answer.
- A turn may end with a text-only response. For messages that need no action — acknowledgments, confirmations, status lines — reply with at most one short line of text (none for harness notices that require no action; an `[inbox …]` notice is not one, since unread messages are waiting) and end the turn; no tool call is needed to finish a turn.
- Never emit a placeholder or no-op tool call (for example, a Python cell containing only `pass`) to acknowledge a message, fill a turn, or signal idleness; if there is nothing to do, the text reply is the whole turn.
- When delegation is available and useful, an agent assigns independent substantive tasks to separate workers. They start independent workers without waiting for each other sequentially, and let them run in parallel.
- Agents do not keep the turn open by polling with `time.sleep()`, shell `sleep`, or repeated awaits of a still-running handle; they end the turn and collect the result on the done notification.
- Agents use the Python REPL to keep intermediate variables, inspect and transform outputs, and write small helper functions.
- Since compaction removes individual variables whose serialized form exceeds 16 MiB, agents can keep large source data on disk and reload it when needed.
- Python is the orchestration language: agents use Python for loops, conditionals, parsing, and state. They use `bash()` to invoke programs, not to write shell programs, shell loops, or heredocs; those are done directly in Python.
- Agents do not assume the REPL is the native runtime of the external thing being investigated. A repository, package, service, dataset, paper, website, benchmark, or API may have its own environment and normal interface. Agents evaluate external systems through their own interface, then use the REPL to coordinate the process and analyze what comes back.
- Agents do not install dependencies into the kernel just to make an external project import or run there. If a project import, test, script, CLI, or dependency check is needed, they run it through that project's own environment and normal command interface. For example, in a Python repo use its documented commands, `uv run ...`, `.venv/bin/python ...`, or the active project interpreter from the repo root. Agents treat failures from that native environment as the relevant result.
- Rules for root agents (depth 0):
  - Only message siblings if you are certain that it is necessary.
  - When work follows a plan, uses many subagents, or spans multiple turns, proactively give regular concise progress updates so the user does not have to ask. State the current plan, what has completed, any blockers, the proposed fixes, and the next actions. Lead with user-visible outcomes rather than internal process or gate names. Mention internal details only when they explain a blocker or decision. Send an update at meaningful milestones and before ending a turn while work is still running. Do not repeat unchanged status or interrupt short work with unnecessary updates.
- Terminology: continual harness names the persisted prompt, memory, skill, and subagent layer; RLM names the runtime, Python REPL kernel, and native call interface exposed to the model.
- Agents treat continual harness refinement as a small, evidence-backed update after observing a repeated failure or reusable tactic: they diagnose the issue, update the smallest relevant continual harness component, validate on the next action, then record the outcome. They use `await refine.run()` to turn repeated delegation patterns into reusable subagent specs, repeated procedures into skills, durable facts/preferences into memories, and narrow behavioral policies into prompt addendums. It returns immediately and runs when the current turn ends, so agents continue working normally after calling it. Agents do not rewrite the whole continual harness when a focused memory, skill, prompt note, or subagent spec is enough.
- Instructions to agents for multi-agent work:
  - When spawning a subagent, keep the handle to stop or inspect the child later.
  - Ask for an explicit reply when needed; not every message needs a reply.
  - Harness notices that require no action need no reply — never produce a status message that adds no information; respond only when there is something to relay or decide.
  - Use `await rlm.list_subagents()` after kernel restart or compaction.
  - Have children write files and read those files for fan-in.
  - Delegate parallel context-heavy research or independent implementation; do a single known lookup, edit, or command inline.

```

## crates/pa-core/src/prompts/model_prompts.rs

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/model_prompts.rs

SHA-256: `96f18257013dbc22f638203e6eaef658249129ca960044d1943891953c8a746f`

```
//! Per-model system-prompt additions: a TOML rule map from model selectors to markdown files.
//! Shipped rules run before the user's `<agent_dir>/model-prompts.toml` rules; any problem in
//! either layer disables all additions for the session and is reported, never blocking.

use std::collections::BTreeMap;
use std::io;
use std::path::{Component, Path};

use pa_types::session::CustomMessage;
use serde::Deserialize;

pub const MODEL_PROMPTS_TOML: &str = include_str!("layers/model_prompts.toml");

const MODEL_PROMPT_FILES: &[(&str, &str)] = &[(
    "bash-over-subprocess.md",
    include_str!("layers/bash-over-subprocess.md"),
)];

const USER_MODEL_PROMPTS_TOML: &str = "model-prompts.toml";

/// `display: true` rows of this type are dropped from LLM context by `messages::convert_to_llm`.
pub const MODEL_PROMPT_ERROR_CUSTOM_TYPE: &str = "model_prompt_error";

const SHIPPED_SOURCE: &str = "shipped model_prompts.toml";

/// `extras` is `None` whenever `errors` is non-empty.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ModelPromptResolution {
    pub extras: Option<String>,
    pub errors: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Rule {
    r#match: Vec<String>,
    files: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ModelPromptsDoc {
    #[serde(default)]
    rule: Vec<Rule>,
}

struct ParsedRule {
    patterns: Vec<globset::GlobMatcher>,
    files: Vec<String>,
}

fn compile_pattern(pattern: &str) -> Result<globset::GlobMatcher, String> {
    globset::GlobBuilder::new(pattern)
        .literal_separator(true)
        .case_insensitive(true)
        .build()
        .map_err(|error| format!("pattern {pattern:?}: {error}"))
        .map(|glob| glob.compile_matcher())
}

fn parse_layer(toml_text: &str, source: &str) -> (Vec<ParsedRule>, Vec<String>) {
    let doc = match toml::from_str::<ModelPromptsDoc>(toml_text) {
        Ok(doc) => doc,
        Err(error) => return (Vec::new(), vec![format!("{source}: {error}")]),
    };
    let mut rules = Vec::new();
    let mut errors = Vec::new();
    for rule in doc.rule {
        let mut patterns = Vec::new();
        for pattern in rule.r#match {
            match compile_pattern(&pattern) {
                Ok(matcher) => patterns.push(matcher),
                Err(error) => errors.push(format!("{source}: {error}")),
            }
        }
        let mut files = Vec::new();
        for name in rule.files {
            if Path::new(&name).components().all(|c| {
                !matches!(
                    c,
                    Component::ParentDir | Component::RootDir | Component::Prefix(_)
                )
            }) {
                // Normalize equivalent spellings before resolution and deduplication.
                let normalized = Path::new(&name)
                    .components()
                    .filter(|component| !matches!(component, Component::CurDir))
                    .map(|component| component.as_os_str().to_string_lossy())
                    .collect::<Vec<_>>()
                    .join("/");
                files.push(normalized);
            } else {
                errors.push(format!(
                    "{source}: file {name:?} must be a relative path inside the agent directory"
                ));
            }
        }
        rules.push(ParsedRule { patterns, files });
    }
    (rules, errors)
}

/// Strips a trailing `:<level>` only when it parses as a thinking level, mirroring the model
/// parser.
fn selector_segments(selector: &str) -> Vec<&str> {
    let selector = match selector.rsplit_once(':') {
        Some((prefix, suffix)) if crate::models::resolver::is_valid_thinking_level(suffix) => {
            prefix
        }
        _ => selector,
    };
    selector.split('/').collect()
}

fn rule_applies(rule: &ParsedRule, segments: &[&str]) -> bool {
    rule.patterns.iter().any(|matcher| {
        (1..=segments.len())
            .any(|count| matcher.is_match(segments[segments.len() - count..].join("/")))
    })
}

// Check the file type before opening: opening a FIFO can wait forever for a writer.
fn read_regular_file(path: &Path) -> io::Result<String> {
    if !std::fs::metadata(path)?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "expected a regular file",
        ));
    }
    std::fs::read_to_string(path)
}

fn resolve_layer_files(
    rules: &[ParsedRule],
    source: &str,
    agent_dir: &Path,
    shipped_files: &[(&str, &str)],
    contents: &mut BTreeMap<String, String>,
) -> Vec<String> {
    let mut errors = Vec::new();
    for rule in rules {
        for name in &rule.files {
            if contents.contains_key(name) {
                continue;
            }
            let user_file = agent_dir.join(name);
            let content = user_file.canonicalize().and_then(|resolved| {
                let root = agent_dir.canonicalize()?;
                if !resolved.starts_with(root) {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "file resolves outside the agent directory",
                    ));
                }
                read_regular_file(&resolved)
            });
            match content {
                Ok(content) => {
                    contents.insert(name.clone(), content);
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    if let Some((_, content)) = shipped_files
                        .iter()
                        .find(|(shipped_name, _)| shipped_name == &name.as_str())
                    {
                        contents.insert(name.clone(), (*content).to_string());
                    } else {
                        errors.push(format!("{source}: missing file {name:?}"));
                    }
                }
                Err(error) => {
                    errors.push(format!("{source}: {}: {error}", user_file.display()));
                }
            }
        }
    }
    errors
}

fn resolve_model_prompts(
    selector: Option<&str>,
    shipped_toml: &str,
    shipped_files: &[(&str, &str)],
    agent_dir: &Path,
) -> ModelPromptResolution {
    // Both layers are validated regardless; the selector only gates which
    // rules match, so `prime-agent prompt` with no `--model` still reports
    // a broken map.
    let segments = selector.map(selector_segments);
    let mut errors = Vec::new();

    let (shipped_rules, mut layer_errors) = parse_layer(shipped_toml, SHIPPED_SOURCE);
    errors.append(&mut layer_errors);
    let user_path = agent_dir.join(USER_MODEL_PROMPTS_TOML);
    let user_layer = user_path.display().to_string();
    let user_rules = match read_regular_file(&user_path) {
        Ok(text) => {
            let (rules, mut layer_errors) = parse_layer(&text, &user_layer);
            errors.append(&mut layer_errors);
            rules
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Vec::new(),
        Err(error) => {
            errors.push(format!("{}: {error}", user_path.display()));
            Vec::new()
        }
    };

    let mut contents: BTreeMap<String, String> = BTreeMap::new();
    for (rules, source) in [
        (&shipped_rules, SHIPPED_SOURCE),
        (&user_rules, user_layer.as_str()),
    ] {
        errors.extend(resolve_layer_files(
            rules,
            source,
            agent_dir,
            shipped_files,
            &mut contents,
        ));
    }
    if !errors.is_empty() {
        return ModelPromptResolution {
            extras: None,
            errors,
        };
    }

    let mut included: Vec<&str> = Vec::new();
    for rule in shipped_rules.iter().chain(user_rules.iter()) {
        if segments
            .as_deref()
            .is_some_and(|segments| rule_applies(rule, segments))
        {
            for name in &rule.files {
                if !included.contains(&name.as_str()) {
                    included.push(name.as_str());
                }
            }
        }
    }
    let parts = included
        .iter()
        .map(|name| contents[*name].trim())
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>();
    let extras = (!parts.is_empty()).then(|| parts.join("\n\n"));
    ModelPromptResolution { extras, errors }
}

#[must_use]
pub fn load_model_prompts(selector: Option<&str>, agent_dir: &Path) -> ModelPromptResolution {
    resolve_model_prompts(selector, MODEL_PROMPTS_TOML, MODEL_PROMPT_FILES, agent_dir)
}

#[must_use]
pub fn model_prompt_error_message(errors: &[String]) -> CustomMessage {
    let list = errors
        .iter()
        .map(|error| format!("- {error}"))
        .collect::<Vec<_>>()
        .join("\n");
    CustomMessage {
        custom_type: MODEL_PROMPT_ERROR_CUSTOM_TYPE.to_string(),
        content: pa_types::ai::UserContent::Text(format!(
            "[model-prompt-error]\n\nPer-model system prompt additions were not applied:\n{list}"
        )),
        display: true,
        details: None,
        timestamp: crate::session_engine::refine::now_millis(),
        rest: serde_json::Map::default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_file(dir: &std::path::Path, name: &str, content: &str) {
        std::fs::write(dir.join(name), content).unwrap();
    }

    fn user_layer(toml_rules: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        write_file(dir.path(), USER_MODEL_PROMPTS_TOML, toml_rules);
        dir
    }

    #[test]
    fn documented_selector_examples_match() {
        let cases = [
            ("glm-5.3", "z-ai/glm-5.3", true),
            ("glm-5.3", "internal/glm-5.3-fast", false),
            ("glm-5.3*", "internal/glm-5.3-fast", true),
            (
                "internal/glm-5.3*",
                "prime-inference/internal/glm-5.3-fast",
                true,
            ),
            ("internal/glm-5.3*", "z-ai/glm-5.3-fast", false),
            ("internal/*", "prime-inference/internal/glm-5.3", true),
            (
                "prime-inference/anthropic/claude-fable-5.1",
                "prime-inference/anthropic/claude-fable-5.1",
                true,
            ),
            (
                "prime-inference/anthropic/claude-fable-5.1",
                "anthropic/claude-fable-5.1",
                false,
            ),
            (
                "anthropic/claude-sonnet-4",
                "anthropic/claude-sonnet-4",
                true,
            ),
            (
                "anthropic/claude-sonnet-4",
                "prime-inference/anthropic/claude-sonnet-4",
                true,
            ),
            // case-insensitive
            ("GLM-5.3*", "z-ai/glm-5.3-fast", true),
            // a trailing thinking level is stripped from the selector
            ("glm-5.3", "z-ai/glm-5.3:high", true),
            ("glm-5.3*", "z-ai/glm-5.3-fast:xhigh", true),
            // dots are literal (a regex `.` would match the `-`)
            ("claude-sonnet-3.5", "anthropic/claude-sonnet-3-5", false),
            // `?` matches exactly one character
            ("claude-sonnet-?", "anthropic/claude-sonnet-4", true),
            ("claude-sonnet-?", "anthropic/claude-sonnet-4.5", false),
            // a brace pattern applies when any alternative fits the final
            // segments, even though the pattern string spans more slashes
            ("{anthropic/claude-*,z-ai/glm-*}", "z-ai/glm-5.3", true),
        ];
        for (pattern, selector, expected) in cases {
            let (rules, _) =
                parse_layer(&format!("[[rule]]\nmatch = [{pattern:?}]\nfiles = []"), "t");
            assert_eq!(
                rule_applies(&rules[0], &selector_segments(selector)),
                expected,
                "pattern {pattern:?} against {selector:?}"
            );
        }
    }

    #[test]
    fn every_matching_rule_applies_and_shared_files_dedupe_first() {
        let dir = user_layer(
            r#"
[[rule]]
match = ["glm-5.3"]
files = ["a.md", "b.md"]

[[rule]]
match = ["glm-5.3*"]
files = ["./b.md", "c.md", "././a.md"]

[[rule]]
match = ["claude-*"]
files = ["a.md"]
"#,
        );
        write_file(dir.path(), "a.md", "A");
        write_file(dir.path(), "b.md", "B");
        write_file(dir.path(), "c.md", "C");
        let resolution = resolve_model_prompts(Some("z-ai/glm-5.3"), "", &[], dir.path());
        assert!(resolution.errors.is_empty());
        assert_eq!(resolution.extras.as_deref(), Some("A\n\nB\n\nC"));
    }

    #[test]
    fn user_files_override_shipped_and_user_rules_run_last() {
        let dir = user_layer(
            r#"
[[rule]]
match = ["glm-5.3"]
files = ["shared.md", "user-only.md"]
"#,
        );
        write_file(dir.path(), "shared.md", "USER");
        write_file(dir.path(), "user-only.md", "USER-ONLY");
        let shipped_toml = "[[rule]]\nmatch = [\"glm-5.3\"]\nfiles = [\"shared.md\"]\n";
        let shipped_files = [("shared.md", "SHIPPED")];
        let resolution = resolve_model_prompts(
            Some("z-ai/glm-5.3"),
            shipped_toml,
            &shipped_files,
            dir.path(),
        );
        assert!(resolution.errors.is_empty());
        assert_eq!(resolution.extras.as_deref(), Some("USER\n\nUSER-ONLY"));

        let clean = tempfile::tempdir().unwrap();
        let resolution = resolve_model_prompts(
            Some("z-ai/glm-5.3"),
            shipped_toml,
            &shipped_files,
            clean.path(),
        );
        assert_eq!(resolution.extras.as_deref(), Some("SHIPPED"));
    }

    #[test]
    fn layer_problems_disable_extras_and_name_the_source() {
        let empty = tempfile::tempdir().unwrap();
        let resolution = resolve_model_prompts(Some("z-ai/glm-5.3"), "", &[], empty.path());
        assert_eq!(resolution, ModelPromptResolution::default());

        let cases = [
            ("not a rule map [", USER_MODEL_PROMPTS_TOML),
            (
                "[[rule]]\nmatches = [\"glm-5.3\"]\nfiles = [\"a.md\"]\n",
                USER_MODEL_PROMPTS_TOML,
            ),
            (
                "[[rule]]\nmatch = [\"glm-5.3\"]\nfiles = [\"missing.md\"]\n",
                "missing.md",
            ),
            (
                "[[rule]]\nmatch = [\"glm-5.3[\"]\nfiles = [\"a.md\"]\n",
                "glm-5.3[",
            ),
        ];
        for (toml_text, named) in cases {
            let dir = user_layer(toml_text);
            write_file(dir.path(), "a.md", "A");
            let resolution = resolve_model_prompts(Some("z-ai/glm-5.3"), "", &[], dir.path());
            assert!(resolution.extras.is_none(), "extras survived {toml_text:?}");
            assert!(
                resolution
                    .errors
                    .iter()
                    .any(|error| error.contains(named) && error.contains(USER_MODEL_PROMPTS_TOML)),
                "{toml_text:?}: errors {:#?} do not name {named} in {USER_MODEL_PROMPTS_TOML}",
                resolution.errors
            );
        }

        let dir = user_layer("[[rule]]\nmatch = [\"claude-*\"]\nfiles = [\"missing-user.md\"]\n");
        let resolution = resolve_model_prompts(
            Some("z-ai/glm-5.3"),
            "[[rule]]\nmatch = [\"glm-5.3\"]\nfiles = [\"missing-shipped.md\"]\n",
            &[],
            dir.path(),
        );
        assert!(resolution.extras.is_none());
        assert!(resolution
            .errors
            .iter()
            .any(|error| error.contains("missing-user.md")));
        assert!(resolution
            .errors
            .iter()
            .any(|error| error.contains("missing-shipped.md")));

        let dir = user_layer("not a rule map [");
        let resolution = resolve_model_prompts(None, "", &[], dir.path());
        assert!(resolution.extras.is_none());
        assert!(resolution
            .errors
            .iter()
            .any(|error| error.contains(USER_MODEL_PROMPTS_TOML)));
    }

    #[test]
    fn the_shipped_map_is_valid() {
        let empty = tempfile::tempdir().unwrap();
        let resolution = load_model_prompts(Some("z-ai/glm-5.3"), empty.path());
        assert!(resolution.errors.is_empty());
    }

    #[test]
    fn shipped_glm_bash_rule_applies_to_glm_models_only() {
        let empty = tempfile::tempdir().unwrap();
        let resolution =
            load_model_prompts(Some("prime-inference/internal/glm-5.3-fast"), empty.path());
        assert!(resolution.errors.is_empty(), "{:#?}", resolution.errors);
        let expected = MODEL_PROMPT_FILES
            .iter()
            .find(|(name, _)| *name == "bash-over-subprocess.md")
            .map(|(_, content)| (*content).trim())
            .expect("the shipped file table names bash-over-subprocess.md");
        assert_eq!(resolution.extras.as_deref(), Some(expected));

        let other = load_model_prompts(Some("anthropic/claude-sonnet-4"), empty.path());
        assert_eq!(other, ModelPromptResolution::default());
    }

    #[test]
    fn file_entries_outside_the_agent_dir_are_rejected() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "outside.md", "OUTSIDE");
        let agent_dir = root.path().join("agent");
        std::fs::create_dir(&agent_dir).unwrap();
        write_file(&agent_dir, "inside.md", "INSIDE");

        let outside = root.path().join("outside.md").display().to_string();
        let cases = [
            ("inside.md", Some("INSIDE"), false),
            ("./inside.md", Some("INSIDE"), false),
            ("../outside.md", None, true),
            (outside.as_str(), None, true),
        ];
        let mut failures = Vec::new();
        for (name, extras, rejected) in cases {
            write_file(
                &agent_dir,
                USER_MODEL_PROMPTS_TOML,
                &format!("[[rule]]\nmatch = [\"glm-5.3\"]\nfiles = [{name:?}]\n"),
            );
            let resolution = resolve_model_prompts(Some("z-ai/glm-5.3"), "", &[], &agent_dir);
            let leaked = resolution.extras.as_deref();
            if leaked != extras {
                failures.push(format!(
                    "file {name:?}: extras {leaked:?}, expected {extras:?}"
                ));
            }
            let named = resolution
                .errors
                .iter()
                .any(|error| error.contains(&format!("{name:?}")));
            if named != rejected {
                failures.push(format!("file {name:?}: errors {:#?}", resolution.errors));
            }
        }
        assert!(failures.is_empty(), "{failures:#?}");
    }

    #[test]
    fn normalized_names_resolve_shipped_files() {
        let dir = user_layer("[[rule]]\nmatch = [\"*\"]\nfiles = [\"./a.md\", \"a.md\"]\n");
        let resolution = resolve_model_prompts(Some("model"), "", &[("a.md", "A")], dir.path());
        assert_eq!(
            resolution,
            ModelPromptResolution {
                extras: Some("A".to_string()),
                errors: Vec::new(),
            }
        );
    }

    #[cfg(unix)]
    #[test]
    fn symlink_targets_must_stay_inside_agent_directory() {
        use std::os::unix::fs::symlink;

        let root = tempfile::tempdir().unwrap();
        let agent_dir = root.path().join("agent");
        std::fs::create_dir(&agent_dir).unwrap();
        write_file(root.path(), "outside.md", "OUTSIDE");
        write_file(&agent_dir, "inside.md", "INSIDE");
        symlink(root.path().join("outside.md"), agent_dir.join("escape.md")).unwrap();
        symlink(root.path(), agent_dir.join("escape-dir")).unwrap();
        symlink(agent_dir.join("inside.md"), agent_dir.join("safe.md")).unwrap();
        for name in ["escape.md", "escape-dir/outside.md", "safe.md"] {
            write_file(
                &agent_dir,
                USER_MODEL_PROMPTS_TOML,
                &format!("[[rule]]\nmatch = [\"*\"]\nfiles = [{name:?}]\n"),
            );
            let resolution = resolve_model_prompts(Some("model"), "", &[], &agent_dir);
            if name == "safe.md" {
                assert_eq!(
                    resolution,
                    ModelPromptResolution {
                        extras: Some("INSIDE".to_string()),
                        errors: Vec::new(),
                    }
                );
            } else {
                assert!(resolution.extras.is_none());
                assert!(resolution
                    .errors
                    .iter()
                    .any(|error| error.contains("outside the agent directory")));
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn fifos_are_rejected_without_opening_them() {
        // Keep a writer available so a regression fails assertions instead of hanging the suite.
        for name in [USER_MODEL_PROMPTS_TOML, "fifo.md"] {
            let dir =
                user_layer("[[rule]]\nmatch = [\"different-model\"]\nfiles = [\"fifo.md\"]\n");
            let path = dir.path().join(name);
            if name == USER_MODEL_PROMPTS_TOML {
                std::fs::remove_file(&path).unwrap();
            }
            nix::unistd::mkfifo(
                &path,
                nix::sys::stat::Mode::S_IRUSR | nix::sys::stat::Mode::S_IWUSR,
            )
            .unwrap();
            let writer_path = path.clone();
            let writer = std::thread::spawn(move || {
                // Nonblocking open succeeds only if the loader incorrectly opened the FIFO.
                use std::os::unix::fs::OpenOptionsExt;
                loop {
                    if let Ok(file) = std::fs::OpenOptions::new()
                        .write(true)
                        .custom_flags(libc::O_NONBLOCK)
                        .open(&writer_path)
                    {
                        drop(file);
                        break;
                    }
                    if !writer_path.exists() {
                        break;
                    }
                    std::thread::yield_now();
                }
            });
            let resolution = resolve_model_prompts(Some("model"), "", &[], dir.path());
            std::fs::remove_file(&path).unwrap();
            writer.join().unwrap();
            assert!(resolution.extras.is_none());
            assert!(resolution
                .errors
                .iter()
                .any(|error| error.contains("expected a regular file")));
        }
    }
}

```

## crates/pa-core/src/prompts/system_prompt.rs

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/798d97032a3ec6d70142cc75c0420cb2b260f194/crates/pa-core/src/prompts/system_prompt.rs

SHA-256: `986839d2e4452f22673ad71381cc428819bd4444fec4a663687dc5a9605e33a1`

```
//! Layered system-prompt assembly: static layer files first (cacheable),
//! every session-specific value last. See the parent module docs.

use pa_types::ai::ModelInput;

use crate::resources::{ContextFile, LoadedResources};
use crate::settings::SettingsManager;
use crate::skills::{format_skills_for_prompt, Skill};

use super::layers;

/// The bundled skill the refinement trigger guidance keys on.
pub const REFINE_SKILL_NAME: &str = "refine";

/// Whether a prompt segment belongs to the cache-stable prefix or the
/// session-specific tail.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SegmentKind {
    /// Cacheable static prefix content (layer files, or a user replacement).
    Static,
    /// Session-specific tail content; must never leak into [`SegmentKind::Static`].
    Dynamic,
}

/// One named slice of the assembled prompt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PromptSegment {
    /// Stable segment name (`core`, `usage`, `packages`, `environment`, ...).
    pub name: &'static str,
    pub kind: SegmentKind,
    /// Provenance: the layer file, or what generated the segment.
    pub source: &'static str,
    pub text: String,
}

impl PromptSegment {
    fn static_segment(name: &'static str, source: &'static str, text: String) -> Self {
        Self {
            name,
            kind: SegmentKind::Static,
            source,
            text,
        }
    }

    fn dynamic_segment(name: &'static str, source: &'static str, text: String) -> Self {
        Self {
            name,
            kind: SegmentKind::Dynamic,
            source,
            text,
        }
    }
}

/// The assembled prompt plus its per-layer breakdown. `cached_prefix_len` is
/// the byte length of the static prefix: `assembled[..cached_prefix_len]` is
/// byte-identical across every session that shares the same custom-prompt
/// and model-selection inputs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SystemPromptBreakdown {
    pub segments: Vec<PromptSegment>,
    pub assembled: String,
    pub cached_prefix_len: usize,
}

/// Inputs for one session's prompt. Only `resources.system_prompt` and
/// `model_prompt_extras` select the cached prefix; everything else,
/// settings included, feeds the dynamic tail. A conditional tail piece
/// reads its condition from these inputs where its text lives.
pub struct BuildSystemPromptOptions<'a> {
    pub settings: &'a SettingsManager,
    /// The replacement prompt, the appended prompt, and the AGENTS.md
    /// context files.
    pub resources: &'a LoadedResources,
    /// The model-visible skills.
    pub skills: &'a [Skill],
    /// Active tools. Tool schemas carry tool descriptions outside the prompt.
    pub selected_tools: Vec<&'a str>,
    pub model_prompt_extras: Option<&'a str>,
    /// The session model's input modalities.
    pub model_input: &'a [ModelInput],
    pub cwd: String,
    /// Conversation log path.
    pub messages_path: Option<String>,
    /// Enabled user-configured generic MCP servers.
    pub generic_mcp_servers: Vec<String>,
    pub rlm_depth: u32,
    /// Whether this child runs one factory state: the factory reads its
    /// final message, so the child does not reply to the parent.
    pub factory_child: bool,
    /// Whether the session runs without a daemon (no scheduler, no
    /// heartbeat firing). The wake-up facts the daemonless line states
    /// must match the embedding's wiring.
    pub daemonless: bool,
    /// Whether `rlm.spawn` can create children in this session (the
    /// daemonless print host or a daemon worker); a daemonless session
    /// without one cannot spawn at all.
    pub can_spawn: bool,
}

/// Build the system prompt (assembled text only).
#[must_use]
pub fn build_system_prompt(options: &BuildSystemPromptOptions) -> String {
    system_prompt_breakdown(options).assembled
}

/// Build the system prompt with its per-layer breakdown.
#[must_use]
pub fn system_prompt_breakdown(options: &BuildSystemPromptOptions) -> SystemPromptBreakdown {
    let mut segments: Vec<PromptSegment> = Vec::new();

    // The static prefix: the user's replacement prompt, or the layered files.
    match options.resources.system_prompt.as_deref() {
        Some(custom) if !custom.is_empty() => {
            segments.push(PromptSegment::static_segment(
                "custom",
                "--system-prompt",
                custom.to_string(),
            ));
        }
        _ => {
            segments.push(PromptSegment::static_segment(
                "core",
                layers::layer_source("core").unwrap_or_default(),
                layers::CORE_LAYER.trim().to_string(),
            ));
            segments.push(PromptSegment::static_segment(
                "usage",
                layers::layer_source("usage").unwrap_or_default(),
                layers::USAGE_LAYER.trim().to_string(),
            ));
            segments.push(PromptSegment::static_segment(
                "opinionated",
                layers::layer_source("opinionated").unwrap_or_default(),
                layers::OPINIONATED_LAYER.trim().to_string(),
            ));
            if let Some(extras) = options.model_prompt_extras {
                segments.push(PromptSegment::static_segment(
                    "model-prompts",
                    layers::layer_source("model-prompts").unwrap_or_default(),
                    extras.to_string(),
                ));
            }
        }
    }
    let cached_prefix_len = segments
        .iter()
        .map(|segment| segment.text.len())
        .sum::<usize>()
        + 2 * segments.len().saturating_sub(1);

    // The dynamic tail, in fixed order: packages -> project context ->
    // skills inventory -> MCP servers -> environment -> session role ->
    // additional guidance -> appended prompt.
    let tools = &options.selected_tools;
    let has_ipython = tools.contains(&pa_types::ai::PYTHON_TOOL_NAME);
    let has_file_access = has_ipython || tools.contains(&"bash");

    segments.push(PromptSegment::dynamic_segment(
        "packages",
        "kernel bootstrap defaults",
        packages_section(),
    ));

    let context = context_files_section(&options.resources.agents_files);
    if !context.is_empty() {
        segments.push(PromptSegment::dynamic_segment(
            "project-context",
            "AGENTS.md discovery",
            context,
        ));
    }

    let visible_skills: Vec<&Skill> = options
        .skills
        .iter()
        .filter(|skill| !skill.disable_model_invocation)
        .collect();
    if has_file_access && !visible_skills.is_empty() {
        let inventory = format_skills_for_prompt(options.skills).trim().to_string();
        segments.push(PromptSegment::dynamic_segment(
            "skills-inventory",
            "skill discovery",
            inventory,
        ));
    }

    if has_ipython {
        let mcp = format_generic_mcp_guidance(&options.generic_mcp_servers);
        if !mcp.is_empty() {
            segments.push(PromptSegment::dynamic_segment(
                "mcp-servers",
                "generic MCP settings",
                mcp,
            ));
        }
    }

    segments.push(PromptSegment::dynamic_segment(
        "environment",
        "session configuration",
        environment_section(options),
    ));

    let role = session_role_section(options, has_ipython);
    if !role.is_empty() {
        segments.push(PromptSegment::dynamic_segment(
            "session-role",
            "RLM recursion state",
            role,
        ));
    }

    let appended = options.resources.append_system_prompt.join("\n\n");
    if !appended.is_empty() {
        segments.push(PromptSegment::dynamic_segment(
            "appended-prompt",
            "--append-system-prompt",
            appended,
        ));
    }

    let assembled = segments
        .iter()
        .map(|segment| segment.text.as_str())
        .collect::<Vec<_>>()
        .join("\n\n");
    SystemPromptBreakdown {
        segments,
        assembled,
        cached_prefix_len,
    }
}

fn packages_section() -> String {
    use crate::kernel::bootstrap::default_rlm_extra_import_labels;
    let mut lines = vec![format!(
        "Pre-installed Python packages: {}.",
        default_rlm_extra_import_labels().join(", ")
    )];
    lines.push(
        "Install additional packages with `uv pip install <pkg>` (this is a uv-managed venv with no pip module)."
            .to_string(),
    );
    lines.join("\n")
}

fn context_files_section(context_files: &[ContextFile]) -> String {
    if context_files.is_empty() {
        return String::new();
    }
    let mut lines = vec![
        "# Project Context".to_string(),
        String::new(),
        "Project-specific instructions and guidelines:".to_string(),
        String::new(),
    ];
    for file in context_files {
        lines.push(format!("## {}\n\n{}", file.path.display(), file.content));
    }
    lines.join("\n")
}

fn environment_section(options: &BuildSystemPromptOptions) -> String {
    let cwd = options.cwd.replace('\\', "/");
    let messages_path = options
        .messages_path
        .clone()
        .unwrap_or_else(|| "not persisted".to_string())
        .replace('\\', "/");
    let mut lines = vec![
        format!("Current date: {}", today()),
        format!("Working directory: {cwd}"),
        format!("Conversation log: {messages_path}"),
    ];
    lines.push(if options.model_input.contains(&ModelInput::Image) {
        "Image input: this model can see images; `attach_image` loads them into context."
            .to_string()
    } else {
        "Image input: this model cannot see images; `attach_image` errors for it.".to_string()
    });
    lines.join("\n")
}

fn session_role_section(options: &BuildSystemPromptOptions, has_ipython: bool) -> String {
    let depth = options.rlm_depth;
    let mut lines = vec![format!(
        "Recursive agent depth: {depth}{}",
        if depth == 0 { " (root)" } else { " (not root)" }
    )];
    if options.daemonless {
        lines.push(if options.can_spawn {
            "This session has no daemon: heartbeats never fire, and background `bash()` completions wake you with a queued `[bash-done]` turn; the run waits for live background commands and your subagents before it exits. Subagent replies and exit notices still wake you."
                .to_string()
        } else {
            "This session has no daemon: `rlm.spawn` is unavailable, heartbeats never fire, and background `bash()` completions wake you with a queued `[bash-done]` turn."
                .to_string()
        });
    }
    if has_ipython {
        let agent_inbox = !options.daemonless && options.settings.get_agent_inbox_enabled();
        if agent_inbox {
            lines.push(
                "This session has an agent inbox: bash completions, agent messages, child exits, heartbeats, and factory milestones wait in it, and a short `[inbox …]` notice announces new ones. When the notice says messages are waiting, read them with `msgs = await rlm.inbox.read()`. Your own `bash-done` messages need no reply; when nothing else needs action, end the turn silently."
                    .to_string(),
            );
        }
        lines.push(format!(
            "`await bash(cmd)` waits at most 1 second. If the command is still running, it returns a snapshot: `r.running` is True, `r.exit_code` is None, `r.output` is the output so far, and `r.handle` is the live handle. A completed result has `r.running` False and an integer `r.exit_code`. The command keeps running after a snapshot; end the turn and collect the final result {}.",
            if agent_inbox {
                "when its `[inbox …]` notice arrives"
            } else {
                "on the `[bash-done]` notification"
            }
        ));
        lines.push(
            "Do not run long blocking work in a cell; start long Python work as an asyncio task and check on it in later cells."
                .to_string(),
        );
    } else {
        lines.push(
            "This session has no Python REPL (`python_repl` tool): the programmatic tools described above are unavailable here."
                .to_string(),
        );
    }
    if depth > 0 {
        lines.push(
            "You are a child agent spawned by your parent agent. Task prompts are labeled `[task from parent]`."
                .to_string(),
        );
        if options.factory_child {
            lines.push(
                "Your final message is this state's output, which the factory reads; do not message the parent.".to_string(),
            );
        } else if has_ipython {
            lines.push(
                "When a task calls for an answer, reply explicitly with `await agent_message.send(message, receiver_role=\"parent\")`. Not every message or task needs a reply; continue cleanup after sending and go idle normally.".to_string(),
            );
        }
        if has_ipython {
            lines.push(
                "For long-running work, report brief progress with `await rlm.progress_note('...')` (at most 512 characters, throttled to about one note per 10 seconds); the parent sees notes without needing a reply.".to_string(),
            );
        }
    }
    lines.join("\n")
}

fn today() -> String {
    // UTC date in YYYY-MM-DD form; the prompt is date context only.
    let days = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_secs() / 86_400);
    // Civil-from-days algorithm (Howard Hinnant).
    let z = days as i64 + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}")
}

fn format_generic_mcp_guidance(servers: &[String]) -> String {
    let mut enabled: Vec<&String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for server in servers {
        if seen.insert(server) {
            enabled.push(server);
        }
    }
    enabled.sort();
    if enabled.is_empty() {
        return String::new();
    }
    let mut lines = vec![
        "# Generic MCP Connections".to_string(),
        String::new(),
        "Generic MCP connections are accessed through the pre-imported Python `mcp` object in the Python REPL, not as top-level native tool namespaces or installed Python skills.".to_string(),
        format!(
            "Enabled generic MCP servers: {}.",
            enabled
                .iter()
                .map(|server| format!("`{server}`"))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    ];
    for server in &enabled {
        lines.push(format!(
            "For `{server}`, first discover its tools with `await mcp.list_tools(\"{server}\")`, then call one with `await mcp.call_tool(\"{server}\", \"<tool>\", arguments)`."
        ));
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::skills::{create_synthetic_source_info, SkillKind, SourceScope};
    use std::path::PathBuf;

    fn skill(name: &str, import: Option<&str>) -> Skill {
        Skill {
            name: name.to_string(),
            description: format!("Skill {name}"),
            file_path: PathBuf::from("/skills/SKILL.md"),
            base_dir: PathBuf::from("/skills"),
            source_info: create_synthetic_source_info("/skills", "user", SourceScope::User, None),
            disable_model_invocation: false,
            kind: if import.is_some() {
                SkillKind::Python
            } else {
                SkillKind::Markdown
            },
            python: import.map(|import_name| crate::skills::SkillPythonMetadata {
                import_name: import_name.to_string(),
                package_path: PathBuf::from("/skills"),
                pyproject_path: PathBuf::from("/skills/pyproject.toml"),
            }),
        }
    }

    /// Owns what the options borrow.
    struct Fixture {
        settings: SettingsManager,
        resources: LoadedResources,
        skills: Vec<Skill>,
    }

    impl Fixture {
        fn new() -> Self {
            Self {
                settings: SettingsManager::in_memory(&crate::settings::Settings::default()),
                resources: LoadedResources::default(),
                skills: vec![
                    skill("web-search", Some("websearch")),
                    skill("refine", Some("refine")),
                    skill("agent-message", Some("agent_message")),
                ],
            }
        }

        fn options(&self) -> BuildSystemPromptOptions<'_> {
            BuildSystemPromptOptions {
                settings: &self.settings,
                resources: &self.resources,
                skills: &self.skills,
                selected_tools: vec![pa_types::ai::PYTHON_TOOL_NAME],
                model_prompt_extras: None,
                model_input: &[],
                cwd: "/w".to_string(),
                messages_path: Some("/log.jsonl".to_string()),
                generic_mcp_servers: Vec::new(),
                rlm_depth: 0,
                factory_child: false,
                daemonless: false,
                can_spawn: false,
            }
        }
    }

    fn agents_file(content: &str) -> ContextFile {
        ContextFile {
            path: PathBuf::from("AGENTS.md"),
            content: content.to_string(),
        }
    }

    #[test]
    fn default_prompt_is_layered_with_static_prefix_first() {
        let breakdown = system_prompt_breakdown(&Fixture::new().options());
        let prompt = &breakdown.assembled;
        assert!(prompt.starts_with("# prime-agent harness"));
        assert_eq!(breakdown.segments[0].kind, SegmentKind::Static);
        assert_eq!(breakdown.segments[0].name, "core");
        let names: Vec<&str> = breakdown
            .segments
            .iter()
            .map(|segment| segment.name)
            .collect();
        assert_eq!(
            names[..4],
            ["core", "usage", "opinionated", "packages"],
            "core/usage/opinionated layers, then the dynamic tail"
        );
        assert_eq!(
            &prompt[..breakdown.cached_prefix_len],
            format!(
                "{}\n\n{}\n\n{}",
                layers::CORE_LAYER.trim(),
                layers::USAGE_LAYER.trim(),
                layers::OPINIONATED_LAYER.trim()
            )
        );
        let tail = &prompt[breakdown.cached_prefix_len..];
        assert!(tail.contains("Working directory: /w"));
        assert!(tail.contains("Conversation log: /log.jsonl"));
        assert!(tail.contains("<available_skills>"));
        assert!(tail.contains("Recursive agent depth: 0 (root)"));
        assert!(tail.contains("Pre-installed Python packages: requests, httpx,"));
    }

    #[test]
    fn dynamic_tail_isolates_the_cached_prefix() {
        let base = Fixture::new();
        let mut with_context = Fixture::new();
        with_context.resources.agents_files = vec![agents_file("Rule one.")];
        let mut other = with_context.options();
        other.cwd = "/somewhere/else".to_string();
        other.messages_path = None;
        other.rlm_depth = 2;
        other.generic_mcp_servers = vec!["slack".to_string()];
        let left = system_prompt_breakdown(&base.options());
        let right = system_prompt_breakdown(&other);
        assert_eq!(
            left.assembled[..left.cached_prefix_len],
            right.assembled[..right.cached_prefix_len]
        );
        assert_eq!(left.cached_prefix_len, right.cached_prefix_len);
        assert!(right.assembled[right.cached_prefix_len..]
            .contains("Enabled generic MCP servers: `slack`"));
        assert!(right.assembled[right.cached_prefix_len..]
            .contains("You are a child agent spawned by your parent agent."));
    }

    #[test]
    fn factory_child_gets_the_factory_line_instead_of_the_reply_line() {
        const REPLY: &str = "reply explicitly with `await agent_message.send(";
        const FACTORY: &str = "Your final message is this state's output";
        let fixture = Fixture::new();
        let mut child = fixture.options();
        child.rlm_depth = 1;
        let normal = build_system_prompt(&child);
        assert!(normal.contains(REPLY));
        assert!(!normal.contains(FACTORY));
        child.factory_child = true;
        let factory = build_system_prompt(&child);
        assert!(factory.contains(FACTORY));
        assert!(!factory.contains(REPLY));
    }

    #[test]
    fn custom_prompt_replaces_layers_keeps_tail() {
        let mut fixture = Fixture::new();
        fixture.resources.system_prompt = Some("Be terse.".to_string());
        fixture.resources.agents_files = vec![agents_file("Rule one.")];
        let mut options = fixture.options();
        options.model_prompt_extras = Some("Tier-2 guidance.");
        let breakdown = system_prompt_breakdown(&options);
        let prompt = &breakdown.assembled;
        assert!(prompt.starts_with("Be terse."));
        assert_eq!(breakdown.segments[0].name, "custom");
        assert!(prompt.contains("## AGENTS.md\n\nRule one."));
        assert!(prompt.contains("Working directory: /w"));
        assert!(prompt.contains("Current date: "));
        assert!(!prompt.contains("# prime-agent harness"));
        assert!(!prompt.contains("Tier-2 guidance."));
    }

    #[test]
    fn mcp_servers_are_a_tail_segment() {
        let fixture = Fixture::new();
        let mut options = fixture.options();
        options.generic_mcp_servers = vec!["t".to_string(), "t".to_string()];
        let breakdown = system_prompt_breakdown(&options);
        let names: Vec<&str> = breakdown
            .segments
            .iter()
            .map(|segment| segment.name)
            .collect();
        assert!(names.contains(&"mcp-servers"));
        assert!(breakdown.assembled.contains("# Generic MCP Connections"));
        assert!(breakdown
            .assembled
            .contains("Enabled generic MCP servers: `t`."));
    }

    #[test]
    fn daemonless_line_drops_the_stale_no_wake_claim() {
        // The line's one durable contract: it no longer tells daemonless
        // agents that background completions never wake them.
        let fixture = Fixture::new();
        let mut spawning = fixture.options();
        spawning.daemonless = true;
        spawning.can_spawn = true;
        assert!(!build_system_prompt(&spawning).contains("completions do not wake you"));

        let mut bare = fixture.options();
        bare.daemonless = true;
        assert!(!build_system_prompt(&bare).contains("completions do not wake you"));
    }

    #[test]
    fn model_prompt_extras_form_the_last_static_segment() {
        let fixture = Fixture::new();
        let mut options = fixture.options();
        options.model_prompt_extras = Some("Tier-2 guidance.");
        let breakdown = system_prompt_breakdown(&options);
        let names: Vec<&str> = breakdown
            .segments
            .iter()
            .map(|segment| segment.name)
            .collect();
        assert_eq!(
            names[..5],
            ["core", "usage", "opinionated", "model-prompts", "packages"],
            "extras are the last static segment, directly before the dynamic tail"
        );
    }
}

```
