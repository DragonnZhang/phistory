# Dark-Moon — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## conf/entrypoint-opencode.sh

Source: https://github.com/ASCIT31/Dark-Moon/blob/edfe9cb12a263036a0c3dc1af604713e8197cce9/conf/entrypoint-opencode.sh

SHA-256: `21c7d95256f986e05865b47158720c468104a182fe212efd46e510f4b159ecab`

```
#!/usr/bin/env bash
set -u
# ⚠️ PAS de set -e global (contrôlé manuellement)

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fatal() {
  log "FATAL: $*"
  exit 1
}

#######################################
# Paths
#######################################
AGENTS_DIR="/root/.opencode/agents"
DEFAULT_AGENTS="/opt/darkmoon/default-agents"
DEFAULT_WORKFLOWS="/opt/darkmoon/default-workflows"
WORKFLOWS_DIR="/opt/darkmoon/mcp/server/src/tools/workflows/"
OPENCODE_CONFIG_FILE="/root/.config/opencode/opencode.json"
OPENCODE_AUTH_FILE="/root/.local/share/opencode/auth.json"
APPLY_SCRIPT="/root/conf/apply-settings.sh"

#######################################
# Sanity checks
#######################################
[ -d "$DEFAULT_AGENTS" ] || fatal "Default agents dir missing: $DEFAULT_AGENTS"

#######################################
# Prepare directories (bind-mount safe)
#######################################
log "Preparing directories"
mkdir -p \
  "$AGENTS_DIR" \
  "$(dirname "$OPENCODE_CONFIG_FILE")" \
  "$(dirname "$OPENCODE_AUTH_FILE")"

#######################################
# Apply OpenCode config (ALWAYS)
#######################################
log "Applying OpenCode configuration (forced)"

log "Runtime environment variables available:"
# Redact secret values (anything whose name contains KEY/TOKEN/SECRET/PASSWORD)
env | grep -E 'OPENROUTER_|OPENCODE_' | sed -E 's/^([^=]*(KEY|TOKEN|SECRET|PASSWORD)[^=]*)=.*/\1=REDACTED/I' || true

[ -x "$APPLY_SCRIPT" ] || fatal "Apply script not executable: $APPLY_SCRIPT"

if ! "$APPLY_SCRIPT"; then
  fatal "apply-settings failed"
fi

#######################################
# Seed agents (VOLUME-SAFE)
#######################################
log "Checking agents directory"

mkdir -p "$AGENTS_DIR"

if [ -z "$(ls -A "$AGENTS_DIR" 2>/dev/null)" ]; then
  log "Agents dir empty → seeding from image"

  if ! cp -a "$DEFAULT_AGENTS/." "$AGENTS_DIR/"; then
    fatal "Failed to seed agents"
  fi

  log "Agents seeded successfully"
else
  log "Agents dir already populated → skip"
fi

#######################################
# Seed workflows (VOLUME-SAFE)
#######################################
log "Checking workflows directory"

mkdir -p "$WORKFLOWS_DIR"

if [ -z "$(ls -A "$WORKFLOWS_DIR" 2>/dev/null)" ]; then
  log "Workflows dir empty → seeding from image"

  if ! cp -a "$DEFAULT_WORKFLOWS/." "$WORKFLOWS_DIR/"; then
    fatal "Failed to seed workflows"
  fi

  log "Workflows seeded successfully"
else
  log "Workflows dir already populated → skip"
fi

#######################################
# Final state summary (debug friendly)
#######################################
log "Final agent directory content:"
ls -la "$AGENTS_DIR"


#######################################
# OpenCode Markdown export watcher
#######################################
#######################################
# Real-time Markdown watcher (inotify)
#######################################

SESSIONS_DIR="/root/.local/share/opencode/sessions"

log "Preparing OpenCode sessions directory"
mkdir -p "$SESSIONS_DIR"

log "Starting real-time Markdown watcher (inotify on /)"

inotifywait -m / \
  -e create -e moved_to -e close_write \
  --format '%w%f' |
while read -r path; do
  file="$(basename "$path")"

  case "$file" in
    *.md) ;;
    *) continue ;;
  esac

  case "$path" in
    /*.md) ;;
    *) continue ;;
  esac

  src="$path"
  dst="$SESSIONS_DIR/$file"

  [ -f "$src" ] || continue

  if [ -f "$dst" ]; then
    ts=$(date '+%Y%m%d-%H%M%S')
    dst="$SESSIONS_DIR/${file%.md}-$ts.md"
  fi

  log "Markdown detected → moving $src → $dst"
  mv -f "$src" "$dst"
done &

#######################################
# Growth signals (local, privacy-first, opt-out)
#######################################
# Both the MCP server (writer, api/growth.py) and opencode + the growth CTA plugin
# (reader/displayer) run in THIS container, so they share one growth state dir.
# Pin it under the persisted opencode share (bind-mounted to ./darkmoon-settings)
# so the "shown once" state survives container restarts. Exported before both the
# MCP boot and opencode below, so both inherit it. Honours DARKMOON_NO_TELEMETRY.
export DARKMOON_GROWTH_DIR="${DARKMOON_GROWTH_DIR:-/root/.local/share/opencode/.darkmoon-growth}"
mkdir -p "$DARKMOON_GROWTH_DIR" 2>/dev/null || true

#######################################
# Persistent Darkmoon MCP (issue #40, section 3)
#######################################
# Start the darkmoon MCP as a persistent streamable-http server BEFORE opencode,
# so its per-process vault AND the pre-model tokenization socket are up before any
# session. opencode connects to it as a remote MCP (apply-settings.sh writes the
# matching config), which lets the privacy plugin tokenize the launch prompt — and
# the session-title call that precedes the first model turn — with no wait. Set
# DARKMOON_MCP_TRANSPORT=stdio to fall back to per-session stdio spawning.
if [ "${DARKMOON_MCP_TRANSPORT:-http}" != "stdio" ]; then
  export DARKMOON_MCP_TRANSPORT="${DARKMOON_MCP_TRANSPORT:-http}"
  export DARKMOON_MCP_HOST="${DARKMOON_MCP_HOST:-127.0.0.1}"
  export DARKMOON_MCP_PORT="${DARKMOON_MCP_PORT:-8181}"
  export DARKMOON_MCP_PATH="${DARKMOON_MCP_PATH:-/mcp}"
  # Persistent MCP = one long-lived vault shared by every session; give it a long
  # TTL so placeholders minted for a launch prompt still rehydrate into a report
  # generated much later in a long campaign (the vault is in-memory and local-only,
  # and resets on container restart).
  export DARKMOON_PRIVACY_TTL="${DARKMOON_PRIVACY_TTL:-604800}"
  log "Starting persistent Darkmoon MCP (http) on ${DARKMOON_MCP_HOST}:${DARKMOON_MCP_PORT}${DARKMOON_MCP_PATH}"
  /usr/local/bin/darkmoon-mcp >/tmp/darkmoon-mcp-boot.log 2>&1 &
fi

#######################################
# Start main process
#######################################
log "Starting main process: $*"
exec "$@"
```

## mcp/src/tools/core/executor.py

Source: https://github.com/ASCIT31/Dark-Moon/blob/edfe9cb12a263036a0c3dc1af604713e8197cce9/mcp/src/tools/core/executor.py

SHA-256: `52d1d0753f2a52d63875911157a506d84930bd97f0006e0331dab7aaf1bbb8aa`

```
import shlex
from typing import Optional, Dict, List
from src.docker_client import DarkmoonDockerClient
from src.models.common import ToolOutput, ExecutionResult
from src.execution_guard import classify
from src.tools.core.allowlist import EXECUTOR_ALLOWED_TOOLS


class GenericExecutor:
    """
    Generic command executor for running any tool in the Darkmoon toolbox.
    Provides validation, sanitization, and safe execution of arbitrary commands.
    """

    def __init__(self, docker_client: DarkmoonDockerClient):
        self.docker_client = docker_client

        # Whitelist of allowed tools (for security)
        self.allowed_tools = set(EXECUTOR_ALLOWED_TOOLS)

        # Dangerous commands to block
        self.blocked_patterns = [
            "rm -rf",
            "dd if=",
            "mkfs",
            ":(){ :|:& };:",  # Fork bomb
            "> /dev/sda",
            "wget http",  # Prevent exfiltration without explicit approval
        ]

    def validate_command(self, command: str) -> tuple[bool, Optional[str]]:
        """
        Validate a command for safety.

        Args:
            command: Command to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check for blocked patterns
        for pattern in self.blocked_patterns:
            if pattern in command:
                return False, f"Blocked pattern detected: {pattern}"

        # Refuse commands that provably cannot finish, BEFORE burning the deadline
        # on them. The agent prompts already forbid unbounded credential attacks and
        # giant wordlists, and a model still ran `hydra -P rockyou.txt` (14M
        # candidates) and froze a whole campaign for 72 minutes. A rule that only
        # lives in a prompt is a suggestion; this is the enforcement point.
        # The refusal carries the same remediation the timeout path would give, so
        # the agent can immediately reach its objective a bounded way instead of
        # retrying the identical command.
        verdict = classify(command)
        if verdict.blocked:
            return False, (
                f"[REFUSED: {verdict.label}] This command cannot complete and would "
                f"freeze the campaign.\n\nWHY: {verdict.why}\n\n"
                f"DO THIS INSTEAD: {verdict.instead}\n\n"
                "If two bounded attempts fail, record the vector as not-exploitable "
                "and move on. Abandoning a dead end is a correct outcome."
            )

        # Extract first word (tool name)
        try:
            parts = shlex.split(command)
            if not parts:
                return False, "Empty command"

            tool_name = parts[0].split("/")[-1]  # Handle paths

            # Check if tool is allowed
            if tool_name not in self.allowed_tools:
                return (
                    False,
                    f"Tool '{tool_name}' not in allowed list. Allowed tools: {', '.join(sorted(self.allowed_tools))}",
                )

        except ValueError as e:
            return False, f"Command parsing error: {e}"

        return True, None

    def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        skip_validation: bool = False,
        session_id: Optional[str] = None,
    ) -> ToolOutput:
        """
        Execute a generic command in the toolbox.

        Args:
            command: Command to execute
            timeout: Timeout in seconds
            workdir: Working directory
            environment: Environment variables
            skip_validation: Skip command validation (use with caution!)

        Returns:
            ToolOutput with execution results
        """
        # Validate command
        if not skip_validation:
            is_valid, error_msg = self.validate_command(command)
            if not is_valid:
                return ToolOutput(
                    tool_name="generic_executor",
                    raw_output="",
                    parsed_data={"error": error_msg},
                    execution_result=ExecutionResult(
                        status="failed",
                        stderr=error_msg or "",
                        exit_code=1,
                    ),
                )

        # Execute command in Docker
        result = self.docker_client.execute_command(
            command=command,
            timeout=timeout or 300,
            workdir=workdir,
            environment=environment,
            session_id=session_id,
        )

        # Extract tool name
        try:
            tool_name = shlex.split(command)[0].split("/")[-1]
        except Exception:
            tool_name = "unknown"

        return ToolOutput(
            tool_name=tool_name,
            raw_output=result.stdout,
            parsed_data={
                "command": command,
                "exit_code": result.exit_code,
            },
            execution_result=result,
        )

    def execute_script(
        self,
        script_content: str,
        script_name: str = "script.sh",
        timeout: Optional[int] = None,
    ) -> ToolOutput:
        """
        Execute a bash script in the toolbox.

        Args:
            script_content: Content of the script
            script_name: Name for the script file
            timeout: Timeout in seconds

        Returns:
            ToolOutput with execution results
        """
        # Write script to temp file
        script_path = f"/tmp/{script_name}"
        write_cmd = f"cat > {script_path} << 'EOF'\n{script_content}\nEOF"
        write_result = self.docker_client.execute_command(write_cmd, timeout=30)

        if not write_result.success:
            return ToolOutput(
                tool_name="script_executor",
                raw_output="",
                parsed_data={"error": "Failed to write script"},
                execution_result=write_result,
            )

        # Make executable
        chmod_cmd = f"chmod +x {script_path}"
        self.docker_client.execute_command(chmod_cmd, timeout=10)

        # Execute script
        exec_cmd = f"bash {script_path}"
        result = self.docker_client.execute_command(exec_cmd, timeout=timeout or 600)

        return ToolOutput(
            tool_name="script_executor",
            raw_output=result.stdout,
            parsed_data={"script_name": script_name, "script_path": script_path},
            execution_result=result,
        )

    def add_allowed_tool(self, tool_name: str):
        """Add a tool to the allowed list."""
        self.allowed_tools.add(tool_name)

    def remove_allowed_tool(self, tool_name: str):
        """Remove a tool from the allowed list."""
        self.allowed_tools.discard(tool_name)

    def list_allowed_tools(self) -> List[str]:
        """Get list of allowed tools."""
        return sorted(self.allowed_tools)

```
