# Prime Agent — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## crates/pa-core/src/prompts/layers/core.md

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/74591cddef28d11249c3171112114fc4755375d7/crates/pa-core/src/prompts/layers/core.md

SHA-256: `da49a8d20592ade2c199368a286e583e125248ee205ca2bdcbf4c7b388185f60`

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
- `rlm.end_turn()`: async; the sanctioned terminal programmatic call — returns the turn to the user; the turn ends when the batch's end-turn call finishes Ok (any other tool calls in the same message still execute first, one end-turn is enough, no further assistant response follows); background work keeps running after the turn returns, and the remaining jobs' completion notices collect the results; raises where the host does not support it, never a silent no-op

## Subagents

prime-agent is a recursive harness. Each session builds a tree of agents, starting at depth 0. Each agent up to a user-configured limit can launch subagents. These subagents are persistent, just like the root session. Each agent can communicate with its nuclear family: its singular parent (except for depth 0 sessions which have no parent), its siblings (other subagents under the same parent), and its children (its own subagents).

The following programmatic tools are available in the REPL for subagent management:

- `rlm.spawn(prompt: str, *, name: str, model: str | None = None, thinking: str | None = None, cwd: str | None = None) -> RLMSpawnHandle`: spawns a new subagent one level deeper than the caller; returns immediately; errors when the caller cannot create subagents; if not given, `model` and `thinking` are inherited from the parent; `cwd` starts the child in that directory (relative paths resolve against the session cwd; it must exist) and loads its project context; results never arrive as `rlm.spawn()` return values, they arrive only via agent_message; `name` must be unique among the spawned agent's siblings, and be meaningful and descriptive but short
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

## crates/pa-core/src/tools/ipython.rs

Source: https://github.com/PrimeIntellect-ai/prime-agent/blob/74591cddef28d11249c3171112114fc4755375d7/crates/pa-core/src/tools/ipython.rs

SHA-256: `7fdc6ccda1166721cd36682d800aa96c5f5c1691bbe50ddad02bff0ad5b2c6ea`

```
//! The `ipython` tool: persistent Python REPL execution through a kernel —
//! the model-facing definition, result text composition, busy-kernel choice,
//! and the rlm bootstrap code. Kernel process management lives behind the
//! [`IpythonKernelProvisioner`] trait, owned by the kernel manager module.

use std::fmt::Write as _;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use serde_json::json;

use crate::settings::types::DEFAULT_PYTHON_CELL_TIMEOUT_MS;

mod diff;
use crate::tools::tool_definition::{
    AbortSignal, ExecutionMode, OnUpdate, ToolContentBlock, ToolDefinition, ToolExecutionResult,
    ToolUpdate,
};
use diff::generate_diff_string;

/// Mime types the model context accepts as images.
pub const IMAGE_MIME_TYPES: [&str; 4] = ["image/jpeg", "image/png", "image/gif", "image/webp"];

// Kernel execution types

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct KernelErrorInfo {
    pub ename: String,
    pub evalue: String,
    pub traceback: Vec<String>,
}

/// A media attachment loaded into context (e.g. by the attach-image skill).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KernelAttachment {
    pub mime_type: String,
    pub data: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum ExecuteStatus {
    #[default]
    Ok,
    Error,
    Aborted,
}

#[derive(Debug, Clone, Default)]
pub struct ExecuteResult {
    pub status: ExecuteStatus,
    pub stdout: String,
    pub stderr: String,
    pub result: Option<String>,
    pub duration_ms: Option<u64>,
    /// Output that arrived without this cell's id, shown separately.
    pub background_output: Option<String>,
    pub error: Option<KernelErrorInfo>,
    pub attachments: Vec<KernelAttachment>,
    /// File edits displayed by this cell, in order.
    pub diffs: Vec<crate::kernel::shared::KernelDiffDisplay>,
    /// Agent messages sent from this cell, in order (TS
    /// `sentAgentMessages` on the tool-result details).
    pub sent_agent_messages: Vec<crate::kernel::shared::KernelSentAgentMessage>,
    /// The `bash()` commands this cell started, summarized for display
    /// (`bashCommands` on the tool-result details).
    pub bash_commands: Option<crate::kernel::shared::KernelBashCommands>,
    /// The cell's `rlm.session.messages` reads (`transcriptReads` on the
    /// tool-result details).
    pub transcript_reads: Vec<serde_json::Value>,
    /// The time limit that interrupted this cell, if one did.
    pub timed_out_after_ms: Option<u64>,
    /// The cell ended the turn via the sanctioned `rlm.end_turn()` call:
    /// the host marked the execution, and this result terminates the tool
    /// batch (the "turn ended" note on the tool-result text).
    pub ended_turn: bool,
}

/// The wire form of one sent agent message (TS `KernelSentAgentMessage`):
/// `id`, `message`, `deliveryStatus`, `receiverRole` when present, and the
/// `target` endpoint (`sessionName` only when present).
#[must_use]
pub fn sent_agent_message_json(
    sent: &crate::kernel::shared::KernelSentAgentMessage,
) -> serde_json::Value {
    use crate::kernel::shared::{SentAgentMessageTarget, SentDeliveryStatus};
    let crate::kernel::shared::KernelSentAgentMessage {
        id,
        message,
        delivery_status,
        receiver_role,
        target:
            SentAgentMessageTarget {
                active_session_id,
                session_id,
                session_name,
            },
    } = sent;
    let delivery = match delivery_status {
        SentDeliveryStatus::Delivered => "delivered",
        SentDeliveryStatus::Queued => "queued",
    };
    let mut value = json!({
        "id": id,
        "message": message,
        "deliveryStatus": delivery,
        "target": {
            "activeSessionId": active_session_id,
            "sessionId": session_id,
        },
    });
    if let Some(role) = receiver_role {
        value["receiverRole"] = json!(role.as_str());
    }
    if let Some(name) = session_name {
        value["target"]["sessionName"] = json!(name);
    }
    value
}

/// The kernel is still running a previously interrupted cell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KernelBusyAfterInterruptError {
    pub message: String,
}

impl std::fmt::Display for KernelBusyAfterInterruptError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl Default for KernelBusyAfterInterruptError {
    fn default() -> Self {
        Self {
            message: "The Python kernel is still running the previously interrupted cell. Wait and try again, or kill the kernel to start fresh.".to_string(),
        }
    }
}

#[derive(Debug)]
pub enum KernelExecError {
    /// The kernel is busy with a previously interrupted cell.
    BusyAfterInterrupt(KernelBusyAfterInterruptError),
    /// Any other kernel failure.
    Other(anyhow::Error),
}

impl KernelExecError {
    /// True when this error is a busy-after-interrupt error.
    #[must_use]
    pub fn is_busy_after_interrupt(&self) -> bool {
        matches!(self, KernelExecError::BusyAfterInterrupt(_))
    }

    /// The model-facing message.
    #[must_use]
    pub fn message(&self) -> String {
        match self {
            KernelExecError::BusyAfterInterrupt(err) => err.message.clone(),
            // The full context chain, not just the outermost layer: a bare
            // top-level message hides the actual cause of kernel failures.
            KernelExecError::Other(err) => format!("{err:#}"),
        }
    }
}

pub type StreamFn<'a> = Option<&'a (dyn Fn(&str, &'static str) + Send + Sync)>;

pub type LateSentAgentMessageHandler =
    std::sync::Arc<dyn Fn(&str, crate::kernel::shared::KernelSentAgentMessage) + Send + Sync>;

pub struct KernelExecuteOptions<'a> {
    pub signal: Option<AbortSignal>,
    /// The model's per-call `timeout`, clamped to the allowed range; `None`
    /// keeps the session's default limit.
    pub timeout_ms: Option<u64>,
    /// Streams cell output while the cell runs.
    pub on_stream: StreamFn<'a>,
    pub on_late_sent_agent_message: Option<crate::kernel::shared::LateSentAgentMessageCallback>,
}

type ExecuteCellFuture =
    Pin<Box<dyn Future<Output = Result<ExecuteResult, KernelExecError>> + Send>>;

/// A running kernel able to execute code cells.
pub trait KernelExecutor: Send + Sync {
    /// Execute one cell in the persistent kernel.
    fn execute(&self, code: &str, options: KernelExecuteOptions<'_>) -> ExecuteCellFuture;
}

/// Startup progress handler (TS: `KernelBootstrapProgressHandler`).
pub type BootstrapProgressHandler = Arc<dyn Fn(&str) + Send + Sync>;

type EnsureFuture = Pin<Box<dyn Future<Output = anyhow::Result<Box<dyn KernelExecutor>>> + Send>>;

/// Owns the lazy create+start+bootstrap of one session's Python kernel.
/// Implementations memoize one running kernel (concurrent `ensure` calls await
/// the same in-flight startup; a failed startup clears the memo), and `kill`
/// terminates the kernel losing all in-memory state. Object-safe on purpose.
pub trait IpythonKernelProvisioner: Send + Sync {
    /// Start (or reuse) the kernel; resolves once it is ready to execute.
    fn ensure(
        &self,
        on_progress: Option<BootstrapProgressHandler>,
        signal: Option<AbortSignal>,
    ) -> EnsureFuture;

    /// Kill the kernel immediately, losing all in-memory state.
    fn kill(&self) -> Pin<Box<dyn Future<Output = ()> + Send>>;
}

// Busy-kernel choice UI

pub const BUSY_KERNEL_WAIT_CHOICE: &str = "Wait and preserve state";
pub const BUSY_KERNEL_KILL_CHOICE: &str = "Kill kernel and restart";

pub fn busy_kernel_prompt() -> String {
    [
        "Interrupted Python cell is still running",
        "Ctrl+C sent an interrupt, but the previous cell has not stopped yet. A new command cannot start until it finishes.",
        "Waiting preserves the current kernel state. Killing restarts the kernel and loses in-memory variables, imports, and running tasks.",
    ]
    .join("\n")
}

pub fn kernel_restart_notice() -> &'static str {
    "<ipython_kernel_reset>\nThe Python kernel was restarted after a previous interrupted cell kept running. Variables, imports, async tasks, and open resources from before the restart are no longer available; recreate them before using them.\n</ipython_kernel_reset>"
}

/// The UI surface the ipython tool needs from the host session (the TS tool context).
pub trait IpythonToolUi: Send + Sync {
    /// Prompt the user to choose between `choices`.
    fn select(
        &self,
        prompt: &str,
        choices: &[&str],
        signal: Option<&AbortSignal>,
    ) -> Pin<Box<dyn Future<Output = Option<String>> + Send>>;

    /// Show/hide a transient working message.
    fn set_working_message(&self, message: Option<&str>);
}

// Tool definition

/// Turn kernel image attachments into image blocks; non-images are dropped.
pub fn image_blocks_from_attachments(attachments: &[KernelAttachment]) -> Vec<ToolContentBlock> {
    attachments
        .iter()
        .filter(|a| IMAGE_MIME_TYPES.contains(&a.mime_type.as_str()))
        .map(|a| ToolContentBlock::Image {
            data: a.data.clone(),
            mime_type: a.mime_type.clone(),
        })
        .collect()
}

fn format_execute_text(
    result: &ExecuteResult,
    background_output: Option<&str>,
    kernel_restarted: bool,
) -> String {
    let mut text = result.stdout.clone();
    if !result.stderr.is_empty() {
        text.push_str(if text.is_empty() { "" } else { "\n" });
        text.push_str(&result.stderr);
    }
    if let Some(result_text) = &result.result {
        text.push_str(if text.is_empty() { "" } else { "\n" });
        text.push_str(result_text);
    }
    if let Some(limit_ms) = result.timed_out_after_ms {
        // Replaces the KeyboardInterrupt traceback, which only says where the cell was.
        text.push_str(if text.is_empty() { "" } else { "\n" });
        text.push_str(&cell_timeout_message(limit_ms, kernel_restarted));
    } else if result.status == ExecuteStatus::Error {
        if let Some(error) = &result.error {
            text.push_str(if text.is_empty() { "" } else { "\n" });
            text.push_str(&error.traceback.join("\n"));
        }
    }
    if let Some(background) = background_output {
        let separator = if text.is_empty() { "" } else { "\n" };
        let _ = write!(
            text,
            "{separator}[background output (unattributed)]\n{background}"
        );
    }
    text
}

/// The model-facing cause and fix for a cell interrupted at its time limit.
fn cell_timeout_message(limit_ms: u64, kernel_restarted: bool) -> String {
    let mut message = format!(
        "This cell was interrupted after {}: long blocking work stops the session. Make it \
non-blocking: start the work as a task (`task = asyncio.create_task(...)`; wrap blocking libraries \
with `asyncio.to_thread`) and check `task.done()` / `task.result()` in later cells. Run shell \
commands with `bash()`. A cell that legitimately needs longer can pass a larger `timeout` (max \
{PYTHON_REPL_TIMEOUT_MAX_S} s), but long work belongs in tasks.",
        humanize_ms(limit_ms)
    );
    if kernel_restarted {
        message.push_str(" The cell did not stop on the interrupt, so the kernel was restarted.");
    }
    message
}

/// Milliseconds as one plain reading: whole seconds when the value divides,
/// a decimal otherwise, raw milliseconds under one second.
fn humanize_ms(ms: u64) -> String {
    if ms < 1000 {
        format!("{ms}ms")
    } else if ms.is_multiple_of(1000) {
        format!("{}s", ms / 1000)
    } else {
        format!("{}.{:03}s", ms / 1000, ms % 1000)
    }
}

async fn execute_with_busy_kernel_choice(
    provisioner: &dyn IpythonKernelProvisioner,
    report_startup_progress: &BootstrapProgressHandler,
    code: &str,
    execute: KernelExecuteOptions<'_>,
    on_working_message: &(dyn Fn(Option<&str>) + Send + Sync),
    ui: Option<&Arc<dyn IpythonToolUi>>,
) -> Result<(ExecuteResult, bool), KernelExecError> {
    let mut kernel_restarted = false;
    loop {
        let manager = provisioner
            .ensure(
                Some(report_startup_progress.clone()),
                execute.signal.clone(),
            )
            .await
            .map_err(KernelExecError::Other)?;
        let result = manager
            .execute(
                code,
                KernelExecuteOptions {
                    signal: execute.signal.clone(),
                    timeout_ms: execute.timeout_ms,
                    on_stream: execute.on_stream,
                    on_late_sent_agent_message: execute.on_late_sent_agent_message.clone(),
                },
            )
            .await;
        match result {
            Ok(result) => {
                if result.timed_out_after_ms.is_some() && result.status == ExecuteStatus::Aborted {
                    // The cell outlived its limit and ignored the interrupt:
                    // no one is there to choose, so take the Kill choice.
                    provisioner.kill().await;
                    kernel_restarted = true;
                }
                return Ok((result, kernel_restarted));
            }
            Err(err) => {
                let aborted = execute
                    .signal
                    .as_ref()
                    .is_some_and(tokio_util::sync::CancellationToken::is_cancelled);
                if !err.is_busy_after_interrupt() || aborted {
                    return Err(err);
                }
                // No UI (headless): cancel immediately.
                let Some(ui) = ui else {
                    return Err(err);
                };
                let choice = ui
                    .select(
                        &busy_kernel_prompt(),
                        &[BUSY_KERNEL_WAIT_CHOICE, BUSY_KERNEL_KILL_CHOICE],
                        execute.signal.as_ref(),
                    )
                    .await;
                match choice.as_deref() {
                    Some(BUSY_KERNEL_WAIT_CHOICE) => {
                        on_working_message(Some("Waiting for Python kernel..."));
                    }
                    Some(BUSY_KERNEL_KILL_CHOICE) => {
                        on_working_message(Some("Restarting Python kernel..."));
                        provisioner.kill().await;
                        kernel_restarted = true;
                    }
                    _ => return Err(err),
                }
            }
        }
    }
}

pub struct IpythonToolOptions {
    /// Shared provisioner owning the kernel lifecycle.
    pub provisioner: Arc<dyn IpythonKernelProvisioner>,
    /// UI surface; `None` in headless sessions.
    pub ui: Option<Arc<dyn IpythonToolUi>>,
    pub on_late_sent_agent_message: Option<LateSentAgentMessageHandler>,
}

pub fn ipython_tool_schema() -> serde_json::Value {
    json!({
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute in the persistent Python REPL. Use the target project's own environment for project imports, tests, scripts, CLIs, and dependency checks instead of direct kernel imports."
            },
            "timeout": {
                "type": "integer",
                "description": format!(
                    "Seconds before this cell is interrupted (default {} unless the user configured otherwise, max {PYTHON_REPL_TIMEOUT_MAX_S}).",
                    DEFAULT_PYTHON_CELL_TIMEOUT_MS / 1000
                )
            }
        }
    })
}

/// The largest per-call `timeout` the model may pass, in seconds.
pub const PYTHON_REPL_TIMEOUT_MAX_S: u64 = 180;

/// The model's `timeout` argument as a cell limit in milliseconds, clamped to
/// 1..=[`PYTHON_REPL_TIMEOUT_MAX_S`] seconds.
fn timeout_arg_ms(params: &serde_json::Value) -> Option<u64> {
    let seconds = params.get("timeout")?.as_f64()?;
    Some((seconds as u64).clamp(1, PYTHON_REPL_TIMEOUT_MAX_S) * 1000)
}

pub fn ipython_tool_description() -> &'static str {
    "Execute Python code in a persistent Python REPL. Top-level `await` is supported. Variables, imports, and loaded data persist across calls, and are revived on a best-effort basis when a session is resumed (objects that cannot be serialized are dropped and reported). Run shell commands with `bash('cmd')` / `await bash('cmd')`. Project imports, tests, scripts, CLIs, and dependency checks should run through the target project's own environment."
}

#[tracing::instrument(
    level = "debug",
    name = "tool_ipython_execute",
    skip(options, on_update, on_late_sent_agent_message)
    fields(code),
)]
pub async fn execute_ipython(
    options: &IpythonToolOptions,
    code: &str,
    timeout_ms: Option<u64>,
    signal: Option<AbortSignal>,
    on_update: Option<OnUpdate>,
    on_late_sent_agent_message: Option<crate::kernel::shared::LateSentAgentMessageCallback>,
) -> anyhow::Result<ToolExecutionResult> {
    let set_tool_working_message = |message: Option<&str>| {
        if let Some(ui) = &options.ui {
            ui.set_working_message(message);
        }
    };

    let ui = options.ui.clone();
    let report_startup_progress: BootstrapProgressHandler = {
        let on_update = on_update.clone();
        let ui = ui.clone();
        Arc::new(move |message: &str| {
            if let Some(ui) = &ui {
                ui.set_working_message(Some(message));
            }
            if let Some(on_update) = &on_update {
                on_update(ToolUpdate {
                    content: vec![ToolContentBlock::text(message.to_string())],
                    details: Some(json!({ "status": "starting" })),
                });
            }
        })
    };

    // Stream cell output chunks as in-flight updates (TS onStream).
    let stream_update = |chunk: &str, _stream: &'static str| {
        if let Some(on_update) = &on_update {
            on_update(ToolUpdate {
                content: vec![ToolContentBlock::text(chunk.to_string())],
                details: Some(json!({ "status": "ok" })),
            });
        }
    };

    let result = execute_with_busy_kernel_choice(
        options.provisioner.as_ref(),
        &report_startup_progress,
        code,
        KernelExecuteOptions {
            signal,
            timeout_ms,
            on_stream: Some(&stream_update),
            on_late_sent_agent_message,
        },
        &|message| {
            set_tool_working_message(message);
        },
        options.ui.as_ref(),
    )
    .await;

    set_tool_working_message(None);

    let (r, kernel_restarted) = result.map_err(|err| anyhow::anyhow!("{}", err.message()))?;

    let mut text = format_execute_text(&r, r.background_output.as_deref(), kernel_restarted);
    if kernel_restarted {
        text = if text.is_empty() {
            kernel_restart_notice().to_string()
        } else {
            format!("{}\n\n{}", kernel_restart_notice(), text)
        };
    }
    // The sanctioned turn-end confirmation: short, and it lands in the
    // transcript for replay of the ended turn.
    if r.ended_turn {
        let separator = if text.is_empty() { "" } else { "\n" };
        let _ = write!(text, "{separator}turn ended");
    }

    let image_blocks = image_blocks_from_attachments(&r.attachments);
    let mut content = vec![ToolContentBlock::text(text)];
    content.extend(image_blocks);

    // A timed-out cell is an error even when it had to be force-aborted:
    // nobody aborted it.
    let status = match r.status {
        ExecuteStatus::Ok => "ok",
        ExecuteStatus::Aborted if r.timed_out_after_ms.is_none() => "aborted",
        ExecuteStatus::Error | ExecuteStatus::Aborted => "error",
    };
    let mut details = json!({
        "status": status,
        "kernelRestarted": kernel_restarted,
    });
    if let Some(duration) = r.duration_ms {
        details["durationMs"] = json!(duration);
    }
    if !r.stdout.is_empty() {
        details["stdout"] = json!(r.stdout);
    }
    if !r.stderr.is_empty() {
        details["stderr"] = json!(r.stderr);
    }
    if let Some(result_text) = &r.result {
        details["result"] = json!(result_text);
    }
    if let Some(bash) = &r.bash_commands {
        details["bashCommands"] = json!({
            "first": bash.first,
            "count": bash.count,
            "lines": bash.lines,
        });
    }
    if !r.transcript_reads.is_empty() {
        details["transcriptReads"] = json!(r.transcript_reads);
    }
    if let Some(background) = &r.background_output {
        details["backgroundOutput"] = json!(background);
    }
    if let Some(error) = &r.error {
        details["error"] = json!({
            "ename": error.ename,
            "evalue": error.evalue,
            "traceback": error.traceback,
        });
        details["errorEname"] = json!(error.ename);
    }
    if !r.diffs.is_empty() {
        details["diffs"] = json!(r
            .diffs
            .iter()
            .map(|diff| {
                let start_line = diff
                    .start_line
                    .and_then(|line| usize::try_from(line).ok())
                    .unwrap_or(1);
                json!({
                    "path": diff.path,
                    "diff": generate_diff_string(&diff.old_str, &diff.new_str, 4, start_line),
                })
            })
            .collect::<Vec<_>>());
    }
    if !r.sent_agent_messages.is_empty() {
        details["sentAgentMessages"] = json!(r
            .sent_agent_messages
            .iter()
            .map(sent_agent_message_json)
            .collect::<Vec<_>>());
    }

    // The flagged cell ends the turn: the batch rule terminates the run's
    // turn once every call in the message has executed.
    Ok(ToolExecutionResult {
        content,
        details: Some(details),
        is_error: r.status == ExecuteStatus::Error || r.status == ExecuteStatus::Aborted,
        terminate: r.ended_turn.then_some(true),
    })
}

/// The `python_repl` tool definition: exact name, schema, and description.
#[must_use]
pub fn create_ipython_tool_definition(_cwd: &str, options: IpythonToolOptions) -> ToolDefinition {
    let options = Arc::new(options);
    let execute: crate::tools::tool_definition::ExecuteFn = {
        let options = options;
        Arc::new(move |tool_call_id, params, signal, on_update| {
            let options = options.clone();
            let on_late_sent_agent_message =
                options.on_late_sent_agent_message.as_ref().map(|handler| {
                    let handler = std::sync::Arc::clone(handler);
                    let tool_call_id = tool_call_id.to_string();
                    std::sync::Arc::new(move |message| handler(&tool_call_id, message))
                        as crate::kernel::shared::LateSentAgentMessageCallback
                });
            Box::pin(async move {
                let code = params
                    .get("code")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| anyhow::anyhow!("python_repl tool requires a code string"))?
                    .to_string();
                execute_ipython(
                    &options,
                    &code,
                    timeout_arg_ms(&params),
                    signal,
                    on_update,
                    on_late_sent_agent_message,
                )
                .await
            })
        })
    };
    ToolDefinition {
        name: pa_types::ai::PYTHON_TOOL_NAME.to_string(),
        label: pa_types::ai::PYTHON_TOOL_NAME.to_string(),
        description: ipython_tool_description().to_string(),
        prompt_snippet:
            "python_repl - persistent Python REPL for code, state, and bash() orchestration"
                .to_string(),
        // The kernel is single-threaded; calls must not run in parallel.
        execution_mode: Some(ExecutionMode::Sequential),
        parameters: ipython_tool_schema(),
        prepare_arguments: None,
        execute,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timeout_arg_is_clamped_to_the_allowed_range() {
        assert_eq!(timeout_arg_ms(&json!({ "code": "" })), None);
        assert_eq!(timeout_arg_ms(&json!({ "timeout": 5 })), Some(5_000));
        assert_eq!(timeout_arg_ms(&json!({ "timeout": 0 })), Some(1_000));
        assert_eq!(
            timeout_arg_ms(&json!({ "timeout": 3600 })),
            Some(PYTHON_REPL_TIMEOUT_MAX_S * 1000)
        );
    }

    #[test]
    fn humanize_ms_pads_the_millisecond_remainder() {
        assert_eq!(humanize_ms(500), "500ms");
        assert_eq!(humanize_ms(2000), "2s");
        assert_eq!(humanize_ms(60_000), "60s");
        assert_eq!(humanize_ms(1500), "1.500s");
        // A sub-100ms remainder must not read as tenths: 60.005s, not 60.5s.
        assert_eq!(humanize_ms(60_005), "60.005s");
    }

    #[test]
    fn sent_agent_message_json_matches_ts_wire_shape() {
        let sent = crate::kernel::shared::KernelSentAgentMessage {
            id: "agentmsg_1".to_string(),
            message: "Ping.\nThen report back.".to_string(),
            delivery_status: crate::kernel::shared::SentDeliveryStatus::Delivered,
            receiver_role: Some(crate::kernel::shared::ReceiverRole::Parent),
            target: crate::kernel::shared::SentAgentMessageTarget {
                active_session_id: "worker-active".to_string(),
                session_id: "worker-session".to_string(),
                session_name: Some("Worker".to_string()),
            },
        };
        assert_eq!(
            sent_agent_message_json(&sent),
            json!({
                "id": "agentmsg_1",
                "message": "Ping.\nThen report back.",
                "deliveryStatus": "delivered",
                "receiverRole": "parent",
                "target": {
                    "activeSessionId": "worker-active",
                    "sessionId": "worker-session",
                    "sessionName": "Worker",
                },
            })
        );
        // The queued receipt without a role or session name omits both.
        let queued = crate::kernel::shared::KernelSentAgentMessage {
            id: "agentmsg_2".to_string(),
            message: "Ping.".to_string(),
            delivery_status: crate::kernel::shared::SentDeliveryStatus::Queued,
            receiver_role: None,
            target: crate::kernel::shared::SentAgentMessageTarget {
                active_session_id: "a1".to_string(),
                session_id: "s1".to_string(),
                session_name: None,
            },
        };
        assert_eq!(
            sent_agent_message_json(&queued),
            json!({
                "id": "agentmsg_2",
                "message": "Ping.",
                "deliveryStatus": "queued",
                "target": { "activeSessionId": "a1", "sessionId": "s1" },
            })
        );
    }
}

```
