# CowAgent — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## agent/tools/bash/bash.py

Source: https://github.com/zhayujie/CowAgent/blob/354bc709bab71cf6e400addfc92a00a0dd2838ff/agent/tools/bash/bash.py

SHA-256: `69b4dacc9ace3a08f424c8b827f1c781dd5ff26305b2dbff9deae41501f6c580`

```
"""
Bash tool - Execute bash commands
"""

import os
import re
import signal
import sys
import subprocess
import tempfile
import threading
import time
from typing import Dict, Any

from agent.tools.base_tool import BaseTool, ToolResult
from agent.tools.bash import background, exit_codes
from agent.tools.bash.decode import decode_output
from agent.tools.utils.truncate import truncate_tail, format_size, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES
from common.log import logger
from common.utils import expand_path


class _Cancelled(Exception):
    """Raised inside the wait loop when the user cancelled the run."""


class Bash(BaseTool):
    """Tool for executing bash commands"""

    _IS_WIN = sys.platform == "win32"
    _PROGRESS_MAX_BYTES = 4 * 1024
    _PROGRESS_INTERVAL = 0.5
    # cmd.exe command line limit is ~8191 chars; rewrite python -c above this.
    _WIN_CMD_SAFE_LEN = 7000
    # A command that finishes early returns early, so a generous default costs
    # nothing and spares the model a retry on every install or build. MAX is
    # the ceiling for waiting on a result; a long-lived process is a different
    # thing and goes to run_in_background.
    DEFAULT_TIMEOUT = 120
    MAX_TIMEOUT = 600

    name: str = "bash"
    description: str = f"""Execute a {'command' if _IS_WIN else 'bash command'} in the current working directory. Returns stdout and stderr. Output is truncated to last {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). If truncated, full output is saved to a temp file.
{'''
PLATFORM: Windows (cmd.exe), not Bash, WSL, PowerShell, or Windows Terminal. Use cmd.exe syntax: double quotes (single quotes are literal), `>nul 2>&1` instead of `/dev/null`, `&&` instead of `;`, and `findstr /I "pattern"` without grep-style `-i`/`-e` flags. Do not invoke `bash script.sh` or use Unix-only commands such as grep, head, tail, sed, or awk. Use the search_files tool for file/content search and Python for portable scripting.
''' if _IS_WIN else ''}
ENVIRONMENT: All API keys from env_config are auto-injected. Use $VAR_NAME directly.

SAFETY:
- Freely create/modify/delete files within the workspace
- For destructive commands out of workspace ({{cwd}}), explain and confirm first"""

    params: dict = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command to execute. Omit when reading from or killing a background command."
            },
            "timeout": {
                "type": "integer",
                "description": f"Seconds to wait, default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}. The call returns as soon as the command finishes, so a high value never costs waiting time - set it generously for installs and builds instead of risking a timeout."
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Start the command and return immediately with a bash_id instead of waiting. Use this ONLY for a long-lived process that is meant to keep running, such as a server or a watcher. A command that is only slow should use a larger timeout rather than set this parameter, so its output comes back in the same call. No need to add '&' yourself. Background jobs keep running after the task ends unless you kill them."
            },
            "bash_id": {
                "type": "string",
                "description": "Read whatever a background command has printed since you last looked. Pass this instead of command."
            },
            "kill": {
                "type": "boolean",
                "description": "With bash_id, stop that background command."
            }
        },
        "required": []
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cwd = self.config.get("cwd", os.getcwd())
        # Ensure working directory exists
        if not os.path.exists(self.cwd):
            os.makedirs(self.cwd, exist_ok=True)
        self.default_timeout = self.config.get("timeout", self.DEFAULT_TIMEOUT)
        # Enable safety mode by default (can be disabled in config)
        self.safety_mode = self.config.get("safety_mode", True)
        # Keep the template with the {cwd} placeholder so the description can be
        # re-rendered when the working directory changes (e.g. opening a project).
        self._description_template = self.description
        # Desktop runs on the user's own machine (often non-technical users),
        # so require explicit confirmation for destructive ops outside the workspace.
        if os.environ.get("COW_DESKTOP") == "1":
            self._description_template = self._description_template.replace(
                "- For destructive commands out of workspace ({cwd}), explain and confirm first",
                "- For delete or destructive operations on files out of workspace ({cwd}), "
                "be cautious and confirm with the user before executing, unless the user explicitly requested it",
            )
        # Show the concrete workspace path so the model knows what "the workspace" is
        self.description = self._description_template.replace("{cwd}", self.cwd)

    def set_cwd(self, cwd: str) -> None:
        """Retarget the working directory and re-render the description.

        Called when the session opens a project directory so both the execution
        cwd and the path shown in the tool description follow the project.
        """
        if not cwd:
            return
        self.cwd = cwd
        if not os.path.exists(self.cwd):
            try:
                os.makedirs(self.cwd, exist_ok=True)
            except Exception:
                pass
        template = getattr(self, "_description_template", None)
        if template:
            self.description = template.replace("{cwd}", self.cwd)

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute a bash command
        
        :param args: Dictionary containing the command and optional timeout
        :return: Command output or error
        """
        command = (args.get("command") or "").strip()
        timeout = args.get("timeout", self.default_timeout)
        run_in_background = bool(args.get("run_in_background", False))
        bash_id = (args.get("bash_id") or "").strip()

        # Reading from or killing a background command needs no command string.
        if bash_id:
            return self._background_followup(bash_id, bool(args.get("kill", False)))

        if not command:
            return ToolResult.fail("Error: command parameter is required")

        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            return ToolResult.fail(f"Error: timeout must be an integer, got: {timeout!r}")
        if timeout <= 0:
            return ToolResult.fail("Error: timeout must be a positive integer")
        if timeout > self.MAX_TIMEOUT:
            return ToolResult.fail(
                f"Error: timeout above {self.MAX_TIMEOUT}s is not allowed. "
                f"Use run_in_background=true for a command that runs this long."
            )

        # Security check: Prevent direct access to the credential file
        if re.search(r'\.cow[/\\]\.env', command):
            return ToolResult.fail(
                "Error: Access denied. API keys and credentials must be accessed through the env_config tool only."
            )

        # Optional safety check - only warn about extremely dangerous commands
        if self.safety_mode:
            warning = self._get_safety_warning(command)
            if warning:
                return ToolResult.fail(
                    f"Safety Warning: {warning}\n\nIf you believe this command is safe and necessary, please ask the user for confirmation first, explaining what the command does and why it's needed.")

        try:
            # Prepare environment with .env file variables
            env = os.environ.copy()

            # The console connection belongs to this process. A child inheriting
            # these would, if it started another instance, log in as the same
            # client and take the connection over.
            for _name in ("CLOUD_DEPLOYMENT_ID", "CLOUD_CLIENT_ID"):
                env.pop(_name, None)

            # Anchor artifact outputs to the workspace/project dir regardless of
            # any `cd` inside the command, so tools (e.g. image-generation) can
            # resolve a stable output dir instead of relying on the live cwd.
            if self.cwd:
                env["AGENT_WORKSPACE"] = self.cwd

            # Pass the current run/source identity into child processes
            try:
                from common import utils as _cow_utils
                _attr = {}
                _cow_utils.apply_client_source(_attr)
                _header_to_env = {
                    "X-Agent-Run-Id": "COW_AGENT_RUN_ID",
                    "X-Client-Source": "COW_CLIENT_SOURCE",
                    "X-Client-OS": "COW_CLIENT_OS",
                    "X-Client-Version": "COW_CLIENT_VERSION",
                    "X-Deployment-Id": "COW_DEPLOYMENT_ID",
                }
                for _h, _e in _header_to_env.items():
                    if _attr.get(_h):
                        env[_e] = _attr[_h]
            except Exception:
                pass

            # Load environment variables from ~/.cow/.env if it exists
            env_file = expand_path("~/.cow/.env")
            dotenv_vars = {}
            if os.path.exists(env_file):
                try:
                    from dotenv import dotenv_values
                    dotenv_vars = {key: value for key, value in dotenv_values(env_file).items() if value is not None}
                    env.update(dotenv_vars)
                    logger.debug(f"[Bash] Loaded {len(dotenv_vars)} variables from {env_file}")
                except ImportError:
                    logger.debug("[Bash] python-dotenv not installed, skipping .env loading")
                except Exception as e:
                    logger.debug(f"[Bash] Failed to load .env: {e}")

            # getuid() only exists on Unix-like systems
            if hasattr(os, 'getuid'):
                logger.debug(f"[Bash] Process UID: {os.getuid()}")
            else:
                logger.debug(f"[Bash] Process User: {os.environ.get('USERNAME', os.environ.get('USER', 'unknown'))}")
            
            # Temp script written for long `python -c` commands (Windows only),
            # cleaned up after execution.
            temp_script_path = None

            # On Windows, convert $VAR references to %VAR% for cmd.exe
            user_command = command
            if self._IS_WIN:
                env["PYTHONIOENCODING"] = "utf-8"
                command = self._convert_env_vars_for_windows(command, dotenv_vars)
                user_command = command
                # cmd.exe stops at the first line feed and at ~8191 chars, so
                # multi-line or long `python -c "..."` code is silently cut off.
                # Spill the inline code into a temp .py file instead.
                if len(command) > self._WIN_CMD_SAFE_LEN or "\n" in command or "\r" in command:
                    command, temp_script_path = self._rewrite_python_c(command)
                if command and not command.strip().lower().startswith("chcp"):
                    command = f"chcp 65001 >nul 2>&1 & {command}"

            if run_in_background:
                # Ownership of temp_script_path passes to the registry - the
                # process is still reading it, so it can only go once the job does.
                try:
                    job_id = background.start(command, self.cwd, env, temp_script_path)
                except Exception:
                    # The registry owns the spill only after a successful start.
                    if temp_script_path:
                        try:
                            os.remove(temp_script_path)
                        except OSError:
                            pass
                    raise
                return ToolResult.success({
                    "output": (
                        f"Started in background (bash_id: {job_id}). "
                        f"Read its output with bash(bash_id=\"{job_id}\"), "
                        f"stop it with bash(bash_id=\"{job_id}\", kill=true)."
                    ),
                    "bash_id": job_id,
                })

            files_before = self._snapshot_files(user_command)
            try:
                result = self._run_streaming(
                    command,
                    timeout,
                    env,
                    dotenv_vars,
                )
            finally:
                if temp_script_path:
                    try:
                        os.remove(temp_script_path)
                    except OSError:
                        pass
            
            logger.debug(f"[Bash] Exit code: {result.returncode}")
            logger.debug(f"[Bash] Stdout length: {len(result.stdout)}")
            logger.debug(f"[Bash] Stderr length: {len(result.stderr)}")
            
            # Workaround for exit code 126 with no output
            if result.returncode == 126 and not result.stdout and not result.stderr:
                logger.warning("[Bash] Exit 126 with no output - trying alternative execution method")
                # Try using argument list instead of shell=True
                import shlex
                try:
                    parts = shlex.split(command)
                    if len(parts) > 0:
                        logger.info(f"[Bash] Retrying with argument list: {parts[:3]}...")
                        raw = subprocess.run(
                            parts,
                            cwd=self.cwd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=timeout,
                            env=env
                        )
                        from types import SimpleNamespace
                        retry_result = SimpleNamespace(
                            returncode=raw.returncode,
                            stdout=decode_output(raw.stdout),
                            stderr=decode_output(raw.stderr),
                        )
                        logger.debug(f"[Bash] Retry exit code: {retry_result.returncode}, stdout: {len(retry_result.stdout)}, stderr: {len(retry_result.stderr)}")
                        
                        # If retry succeeded, use retry result
                        if retry_result.returncode == 0 or retry_result.stdout or retry_result.stderr:
                            result = retry_result
                        else:
                            # Both attempts failed - check if this is openai-image-vision skill
                            if 'openai-image-vision' in command or 'vision.sh' in command:
                                # Create a mock result with helpful error message
                                from types import SimpleNamespace
                                result = SimpleNamespace(
                                    returncode=1,
                                    stdout='{"error": "图片无法解析", "reason": "该图片格式可能不受支持，或图片文件存在问题", "suggestion": "请尝试其他图片"}',
                                    stderr=''
                                )
                                logger.info("[Bash] Converted exit 126 to user-friendly image error message for vision skill")
                except Exception as retry_err:
                    logger.warning(f"[Bash] Retry failed: {retry_err}")

            # When command succeeds with stdout, keep output clean (stderr goes to server log only).
            # When command fails or stdout is empty, include stderr so the agent can diagnose.
            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout
                if result.stderr:
                    logger.info(f"[Bash] stderr (not forwarded): {result.stderr[:500]}")
            else:
                output = result.stdout
                if result.stderr:
                    output += "\n" + result.stderr

            # Check if we need to save full output to temp file
            temp_file_path = None
            truncation = truncate_tail(output)

            if truncation.truncated:
                # Save full output to temp file. encoding='utf-8' is required:
                # the default text-mode encoding is the platform locale (e.g.
                # cp936/GBK on Chinese Windows), which raises UnicodeEncodeError
                # for output containing emoji or other non-locale characters and
                # would discard an otherwise successful command result.
                # newline='' writes the line breaks as the command printed
                # them; on Windows, text mode turns each CRLF into CR CR LF.
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log', prefix='bash-', encoding='utf-8', newline='') as f:
                    f.write(output)
                    temp_file_path = f.name

            output_text = truncation.content or "(no output)"

            # Build result
            details = {}

            if truncation.truncated:
                details["truncation"] = truncation.to_dict()
                if temp_file_path:
                    details["full_output_path"] = temp_file_path

                # Build notice
                start_line = truncation.total_lines - truncation.output_lines + 1
                end_line = truncation.total_lines

                if truncation.last_line_partial:
                    # Edge case: last line alone > 30KB
                    last_line = output.split('\n')[-1] if output else ""
                    last_line_size = format_size(len(last_line.encode('utf-8')))
                    output_text += f"\n\n[Showing last {format_size(truncation.output_bytes)} of line {end_line} (line is {last_line_size}). Full output: {temp_file_path}]"
                elif truncation.truncated_by == "lines":
                    output_text += f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines}. Full output: {temp_file_path}]"
                else:
                    output_text += f"\n\n[Showing lines {start_line}-{end_line} of {truncation.total_lines} ({format_size(DEFAULT_MAX_BYTES)} limit). Full output: {temp_file_path}]"

            # Check exit code
            is_error, note = exit_codes.interpret(user_command, result.returncode)
            if is_error:
                output_text += f"\n\nCommand exited with code {result.returncode}"
                hint = self._windows_failure_hint(user_command)
                if hint:
                    output_text += f"\n\n{hint}"
                return ToolResult.fail({
                    "output": output_text,
                    "exit_code": result.returncode,
                    "details": details if details else None
                })
            if note:
                output_text += f"\n\n[Exit code {result.returncode}: {note}]"

            payload = {}
            files = self._files_written(user_command, files_before)
            if files:
                payload["files_written"] = files
            payload.update({
                "output": output_text,
                "exit_code": result.returncode,
                "details": details if details else None
            })
            return ToolResult.success(payload)

        except _Cancelled:
            return ToolResult.fail("Command was stopped because the user cancelled the run.")
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"Error: Command timed out after {timeout} seconds")
        except Exception as e:
            return ToolResult.fail(f"Error executing command: {str(e)}")

    def _snapshot_files(self, command: str) -> dict:
        if not self.cwd:
            return {}
        try:
            from agent.protocol.artifact import snapshot_command_files
            return snapshot_command_files(command, self.cwd)
        except Exception as e:
            logger.debug(f"[Bash] file snapshot skipped: {e}")
            return {}

    def _files_written(self, command: str, before: dict) -> list:
        """User-facing files the command created or changed, so they surface as outputs."""
        if not self.cwd:
            return []
        try:
            from agent.protocol.artifact import files_changed_by_command
            return files_changed_by_command(command, self.cwd, before)
        except Exception as e:
            logger.debug(f"[Bash] changed-file scan skipped: {e}")
            return []

    def _background_followup(self, bash_id: str, want_kill: bool) -> ToolResult:
        """Read from, or kill, an already-running background command."""
        if want_kill:
            if background.kill(bash_id) is None:
                return ToolResult.fail(self._unknown_job_message(bash_id))
            return ToolResult.success({"output": f"Killed background command {bash_id}."})

        state = background.read(bash_id)
        if state is None:
            return ToolResult.fail(self._unknown_job_message(bash_id))

        output = state["output"] or "(no new output)"
        if state["dropped_bytes"]:
            output = (
                f"[{format_size(state['dropped_bytes'])} of earlier output dropped - "
                f"buffer keeps only the most recent]\n" + output
            )
        if state["running"]:
            status = f"\n\n[Still running, {state['elapsed']}s elapsed]"
        else:
            status = f"\n\n[Finished with exit code {state['exit_code']} after {state['elapsed']}s]"

        payload = {
            "output": output + status,
            "running": state["running"],
            "exit_code": state["exit_code"],
        }
        # A non-zero exit is a failure the model should react to, same as
        # foreground - but only once the process has actually finished.
        if not state["running"] and state["exit_code"] != 0:
            return ToolResult.fail(payload)
        return ToolResult.success(payload)

    @staticmethod
    def _unknown_job_message(bash_id: str) -> str:
        jobs = background.list_jobs()
        if not jobs:
            return f"Error: no background command with id {bash_id} (none are being tracked)."
        known = ", ".join(j["id"] for j in jobs)
        return f"Error: no background command with id {bash_id}. Currently tracked: {known}."

    def _run_streaming(self, command: str, timeout: int, env: dict, dotenv_vars: dict):
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=self.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=not self._IS_WIN,
        )
        stdout_chunks, stderr_chunks = [], []
        recent = bytearray()
        recent_lock = threading.Lock()

        def drain(stream, chunks):
            while True:
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    break
                chunks.append(chunk)
                with recent_lock:
                    recent.extend(chunk)
                    if len(recent) > self._PROGRESS_MAX_BYTES:
                        del recent[:-self._PROGRESS_MAX_BYTES]

        readers = [
            threading.Thread(target=drain, args=(process.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr_chunks), daemon=True),
        ]
        for reader in readers:
            reader.start()

        started = time.monotonic()
        last_reported_at = started
        last_snapshot = None
        try:
            while process.poll() is None:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= timeout:
                    self._kill_process(process)
                    raise subprocess.TimeoutExpired(command, timeout)
                if self.is_cancelled():
                    self._kill_process(process)
                    raise _Cancelled()
                if elapsed >= self._PROGRESS_INTERVAL and now - last_reported_at >= self._PROGRESS_INTERVAL:
                    with recent_lock:
                        snapshot = decode_output(bytes(recent))
                    snapshot = self._redact_progress(snapshot, dotenv_vars)
                    if snapshot and snapshot != last_snapshot:
                        self.report_progress(snapshot)
                        last_snapshot = snapshot
                    last_reported_at = now
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                self._kill_process(process)
            process.wait()
            join_deadline = time.monotonic() + 5
            for reader in readers:
                reader.join(timeout=max(0, join_deadline - time.monotonic()))

        from types import SimpleNamespace
        return SimpleNamespace(
            returncode=process.returncode,
            stdout=decode_output(b"".join(stdout_chunks)),
            stderr=decode_output(b"".join(stderr_chunks)),
        )

    def _kill_process(self, process):
        if self._IS_WIN:
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode != 0 and process.poll() is None:
                    process.kill()
            except (OSError, subprocess.SubprocessError):
                if process.poll() is None:
                    process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                if process.poll() is None:
                    process.kill()

    @staticmethod
    def _redact_progress(text: str, dotenv_vars: dict) -> str:
        text = re.sub(
            r'(?i)\b(API_KEY|TOKEN|PASSWORD|AUTHORIZATION)\s*=\s*[^\s]+',
            lambda match: f"{match.group(1)}=[REDACTED]",
            text,
        )
        for value in dotenv_vars.values():
            value = str(value or "")
            if len(value) >= 6:
                text = text.replace(value, "[REDACTED]")
        return text

    def _get_safety_warning(self, command: str) -> str:
        """
        Get safety warning for absolutely catastrophic commands only.
        Keep the blocklist minimal so the agent retains maximum freedom.

        :param command: Command to check
        :return: Warning message if dangerous, empty string if safe
        """
        # Tokenize to avoid substring false positives (e.g. `rm -rf /tmp/x`
        # must not match `rm -rf /`).
        tokens = command.lower().split()

        # `rm -rf /` or `rm -rf /*` targeting the real root.
        for i, tok in enumerate(tokens):
            if tok != "rm":
                continue
            has_r = False
            has_f = False
            for j in range(i + 1, len(tokens)):
                t = tokens[j]
                if t in ("--recursive",):
                    has_r = True
                elif t in ("--force",):
                    has_f = True
                elif t.startswith("--"):
                    continue
                elif t.startswith("-"):
                    if "r" in t:
                        has_r = True
                    if "f" in t:
                        has_f = True
                elif t in ("/", "/*"):
                    if has_r and has_f:
                        return "This command will delete the entire filesystem"
                    break
                else:
                    break

        # Disk wiping
        if "if=/dev/zero" in command.lower() and "dd " in command.lower():
            return "This command can destroy disk data"

        # Power control - match only as a standalone word (\b enforces word boundary)
        if re.search(r'\b(shutdown|reboot|halt|poweroff)\b', command.lower()):
            return "This command will shut down or restart the system"

        return ""

    @classmethod
    def _windows_failure_hint(cls, command: str) -> str:
        """Return focused cmd.exe corrections for detected Unix syntax.

        The hint is intentionally absent for ordinary command failures: only a
        recognized shell mismatch should add another instruction to the model.
        """
        if not cls._IS_WIN:
            return ""

        lower = command.lower()
        corrections = []
        if re.search(r"\bfindstr\b[^\r\n]*(?:^|\s)-[a-z]", lower):
            corrections.append(
                'findstr uses /I and a quoted search string, for example '
                'findstr /I "cow agent"; it does not accept grep-style -i/-e flags'
            )
        if "/dev/null" in lower:
            corrections.append("redirect to nul, for example >nul 2>&1, not /dev/null")
        if re.search(r"(?:^|[&|()]\s*|\s)(?:bash|sh)\s+\S", lower):
            corrections.append("do not invoke bash/sh; use a cmd.exe command or a Python script")
        if re.search(r"'[^'\r\n]*'", command):
            corrections.append("use double quotes because cmd.exe treats single quotes literally")
        if re.search(r"(?:^|[&|()]\s*|\s)(?:grep|head|tail|sed|awk)\b", lower):
            corrections.append("use search_files for file/content search instead of Unix text tools")
        if ";" in command:
            corrections.append("chain commands with && instead of ;")

        if not corrections:
            return ""
        return "[Windows cmd.exe hint: " + "; ".join(corrections) + ".]"

    @staticmethod
    def _convert_env_vars_for_windows(command: str, dotenv_vars: dict) -> str:
        """
        Convert bash-style $VAR / ${VAR} references to cmd.exe %VAR% syntax.
        Only converts variables loaded from .env (user-configured API keys etc.)
        to avoid breaking $PATH, jq expressions, regex, etc.
        """
        if not dotenv_vars:
            return command

        def replace_match(m):
            var_name = m.group(1) or m.group(2)
            if var_name in dotenv_vars:
                return f"%{var_name}%"
            return m.group(0)

        return re.sub(r'\$\{(\w+)\}|\$(\w+)', replace_match, command)

    # [path\]<python|python3|py>[.exe] [interpreter flags] -c, followed by a
    # quote, at the start of the command or right after an operator, so
    # `tool.py -c "..."` and `echo python -c "..."` are left alone.
    _PY_C_RE = re.compile(
        r'(?:^|[&|(])\s*(?:"[^"\r\n]*[\\/]|"|[^\s"&|<>()]*[\\/])?'
        r'(?:python3?|py)(?:\.exe)?"?'
        r'(?:\s+(?:-[XW]\s*\S+|-(?!c\b)\S+))*\s+-c\s+(?=["\'])',
        re.IGNORECASE,
    )

    @classmethod
    def _rewrite_python_c(cls, command: str):
        """
        Move the code of `python -c "<code>"` into a temp .py file.

        The file is loaded back through exec() under -c rather than run as a
        script, so sys.path[0], sys.argv[0], the missing __file__ and the
        `<string>` traceback filename all stay as with the original -c.

        Returns (new_command, temp_file_path). On any parse failure the original
        command and None are returned, so behavior is unchanged when unmatched.
        """
        m = cls._PY_C_RE.search(command)
        if not m:
            return command, None

        quote = command[m.end()]
        body_start = m.end() + 1
        end = cls._closing_quote(command, body_start, quote)
        if end < 0:
            return command, None
        code = command[body_start:end]
        if quote == '"':
            # Windows CRT argument parsing: a run of backslashes before a quote
            # or the closing delimiter contributes half as many literal ones.
            code = re.sub(r'(\\+)(?="|\Z)', lambda r: "\\" * (len(r.group(1)) // 2), code)
        else:
            code = code.replace("\\'", "'")
        suffix = command[end + 1:].strip()

        try:
            fd, path = tempfile.mkstemp(suffix=".py", prefix="bash-pyc-")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(code)
        except OSError:
            return command, None

        # Hex keeps quote- and shell-sensitive path characters out of the loader.
        path_hex = path.encode("utf-8").hex()
        loader = (
            "exec(compile((lambda f:(f.read(),f.close())[0])"
            f"(open(bytes.fromhex('{path_hex}').decode('utf-8'),"
            "encoding='utf-8')),'<string>','exec'))"
        )
        new_command = f'{command[:m.end()]}"{loader}"'
        if suffix:
            new_command += f' {suffix}'
        return new_command, path

    @staticmethod
    def _closing_quote(command: str, start: int, quote: str) -> int:
        """Index of the quote that closes the -c code, or -1.

        Take the first unescaped quote followed by whitespace, an operator or
        the end of the command, so script arguments and a trailing
        `&& echo "done"` stay outside the code. Fall back to the last unescaped
        quote, which tolerates unescaped quotes inside the code.
        """
        last = -1
        index = command.find(quote, start)
        while index >= 0:
            backslashes = 0
            while index - backslashes - 1 >= start and command[index - backslashes - 1] == "\\":
                backslashes += 1
            if backslashes % 2 == 0:
                if re.match(r'(?:\s|$|[&|<>])', command[index + 1:]):
                    return index
                last = index
            index = command.find(quote, index + 1)
        return last

```

## agent/tools/edit/edit.py

Source: https://github.com/zhayujie/CowAgent/blob/354bc709bab71cf6e400addfc92a00a0dd2838ff/agent/tools/edit/edit.py

SHA-256: `c3bb5b0f1bd328b00aa70403e4b3f2fef8524a702679fca09300fa5354dc3d74`

```
"""
Edit tool - Precise file editing
Edit files through exact text replacement
"""

import os
import threading
from typing import Dict, Any

from agent.tools.base_tool import BaseTool, ToolResult
from common.atomic_write import write_text_atomic
from common.utils import expand_path
from agent.tools.utils.credentials import DENIED_MESSAGE, is_credential_path
from agent.tools.utils.diff import (
    strip_bom,
    detect_line_ending,
    normalize_to_lf,
    restore_line_endings,
    find_match_spans,
    generate_diff_string,
    looks_like_line_numbered_block,
    reindent_replacement,
    strip_line_number_prefixes,
    trim_outer_newlines,
)
from agent.tools.utils.file_state import note_write, staleness_warning
from agent.tools.utils.memory_path import feeds_memory_index
from agent.tools.utils.syntax_check import review as syntax_review


_EDIT_LOCKS = tuple(threading.RLock() for _ in range(256))


class Edit(BaseTool):
    """Tool for precise file editing"""
    
    name: str = "edit"
    description: str = "Edit a file by replacing exact text, or append to end if oldText is empty. For append: use empty oldText. For replace: oldText must match exactly (including whitespace) and must be unique unless replaceAll is true. IMPORTANT: the read tool prefixes each line with `12|` for display only - never include those prefixes in oldText or newText."
    
    params: dict = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit (relative or absolute)"
            },
            "oldText": {
                "type": "string",
                "description": "Text to find and replace, copied from the file itself WITHOUT the `12|` line-number prefixes shown by the read tool. Use empty string to append to end of file. For replacement: must match exactly including whitespace."
            },
            "newText": {
                "type": "string",
                "description": "New text to replace the old text with (no line-number prefixes)"
            },
            "replaceAll": {
                "type": "boolean",
                "description": "Replace every occurrence of oldText instead of requiring it to be unique. Default false."
            }
        },
        "required": ["path", "oldText", "newText"]
    }
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cwd = self.config.get("cwd", os.getcwd())
        self.memory_manager = self.config.get("memory_manager", None)
    
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path", "").strip()
        if not path:
            return ToolResult.fail("Error: path parameter is required")
        key = os.path.normcase(os.path.realpath(self._resolve_path(path)))
        # Atomic replacement protects file integrity but not two edits made
        # from the same snapshot. Serialize this process's whole edit cycle.
        with _EDIT_LOCKS[hash(key) % len(_EDIT_LOCKS)]:
            return self._execute_locked(args)

    def _execute_locked(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute file edit operation
        
        :param args: Contains file path, old text and new text
        :return: Operation result
        """
        path = args.get("path", "").strip()
        old_text = args.get("oldText", "")
        new_text = args.get("newText", "")
        replace_all = bool(args.get("replaceAll", False))
        replacements_made = 1
        
        if not path:
            return ToolResult.fail("Error: path parameter is required")
        
        # Resolve path
        absolute_path = self._resolve_path(path)

        # Same guard the read tool applies. Editing is also a read: the success
        # result carries a diff whose context lines would expose the secrets.
        if is_credential_path(absolute_path):
            return ToolResult.fail(DENIED_MESSAGE)

        # Check if file exists
        if not os.path.exists(absolute_path):
            return ToolResult.fail(f"Error: File not found: {path}")

        if not os.path.isfile(absolute_path):
            return ToolResult.fail(f"Error: {path} is not a regular file")
        
        # Check if readable/writable
        if not os.access(absolute_path, os.R_OK | os.W_OK):
            return ToolResult.fail(f"Error: File is not readable/writable: {path}")
        
        try:
            # Read the file's bytes instead of opening it in text mode. A
            # newline=None read translates every CRLF to LF before we ever see
            # it, so the detect_line_ending() call below could only ever answer
            # '\n' and restore_line_endings() was guaranteed to be a no-op.
            # Decoding the bytes leaves the real ending intact for it to find.
            with open(absolute_path, 'rb') as f:
                raw_content = f.read().decode('utf-8')
            
            # Remove BOM (LLM won't include invisible BOM in oldText)
            bom, content = strip_bom(raw_content)
            
            # Detect original line ending
            original_ending = detect_line_ending(content)
            
            # Normalize to LF
            normalized_content = normalize_to_lf(content)
            normalized_old_text = normalize_to_lf(old_text)
            normalized_new_text = normalize_to_lf(new_text)
            
            # Special case: empty oldText means append to end of file
            if not old_text:
                # Append mode: add newText to the end
                # Add newline before newText if file doesn't end with one
                if normalized_content and not normalized_content.endswith('\n'):
                    new_content = normalized_content + '\n' + normalized_new_text
                else:
                    new_content = normalized_content + normalized_new_text
                base_content = normalized_content  # For verification
            else:
                # Normal edit mode: find and replace.
                # Exact match is preferred; the fuzzy pattern only kicks in when
                # the exact substring is absent (see find_match_spans).
                spans, exact = find_match_spans(normalized_content, normalized_old_text)

                if not spans:
                    # Fallback: the model may have copied the `12|` gutter out of
                    # read output. Retry once without it. Doing this only after a
                    # normal miss means content that genuinely contains `12|` is
                    # never mangled.
                    retry_old = strip_line_number_prefixes(normalized_old_text)
                    if retry_old:
                        spans, exact = find_match_spans(normalized_content, retry_old)
                        if spans:
                            normalized_old_text = retry_old
                            stripped_new = strip_line_number_prefixes(normalized_new_text)
                            if stripped_new:
                                normalized_new_text = stripped_new

                if not spans:
                    return ToolResult.fail(
                        f"Error: Could not find the exact text in {path}. "
                        "The old text must match exactly including all whitespace and newlines."
                    )

                if len(spans) > 1 and not replace_all:
                    return ToolResult.fail(
                        f"Error: Found {len(spans)} occurrences of the text in {path}. "
                        "The text must be unique. Please provide more context to make it unique, "
                        "or set replaceAll to true to replace all of them."
                    )

                # Rebuild the file around the matched spans, back to front so the
                # earlier offsets stay valid.
                base_content = normalized_content
                new_content = base_content
                for start, end in reversed(spans):
                    replacement = normalized_new_text
                    if not exact:
                        # A fuzzy match leaves oldText's outer newlines in the
                        # file, so newText must not add its own copies of them.
                        replacement = trim_outer_newlines(
                            normalized_old_text, replacement, base_content, start, end
                        )
                        # A fuzzy match swallowed the file's own indentation;
                        # re-anchor the replacement to it instead of silently
                        # reindenting the line to whatever the model sent.
                        replacement = reindent_replacement(
                            base_content[start:end], normalized_old_text, replacement
                        )
                    new_content = new_content[:start] + replacement + new_content[end:]
                replacements_made = len(spans)
            
            # Checked after the fallback above, so a newText whose gutter was
            # already stripped alongside oldText still goes through.
            if looks_like_line_numbered_block(normalized_new_text):
                return ToolResult.fail(
                    f"Error: newText looks like read tool output ('12|content'), not file "
                    f"content. Those line-number prefixes are display only - strip them "
                    f"before editing {path}."
                )

            # Verify replacement actually changed content
            if base_content == new_content:
                return ToolResult.fail(
                    f"Error: No changes made to {path}. "
                    "The replacement produced identical content. "
                    "This might indicate an issue with special characters or the text not existing as expected."
                )
            
            # Restore original line endings
            final_content = bom + restore_line_endings(new_content, original_ending)

            # Check before writing - our own write would reset the mtime.
            warning = staleness_warning(absolute_path)

            blocking, syntax_warning = syntax_review(absolute_path, base_content, new_content)
            if blocking:
                return ToolResult.fail(f"Error: {blocking}")

            # newline='' writes final_content verbatim; text mode would turn
            # every '\n' into os.linesep and undo the ending restored above.
            write_text_atomic(absolute_path, final_content, newline='')
            note_write(absolute_path)
            
            # Generate diff
            diff_result = generate_diff_string(base_content, new_content)

            if replacements_made > 1:
                message = f"Successfully replaced {replacements_made} occurrences in {path}"
            else:
                message = f"Successfully replaced text in {path}"

            result = {
                "message": message,
                "path": path,
                "diff": diff_result['diff'],
                "first_changed_line": diff_result['first_changed_line']
            }
            if replacements_made > 1:
                result["replacements"] = replacements_made
            warnings = [w for w in (warning, syntax_warning) if w]
            if warnings:
                result["warning"] = " ".join(warnings)
            
            # Notify memory manager if file is in memory directory
            if self.memory_manager and feeds_memory_index(
                absolute_path, self.memory_manager, self.cwd
            ):
                try:
                    self.memory_manager.mark_dirty()
                except Exception:
                    # Don't fail the edit if memory notification fails
                    pass
            
            return ToolResult.success(result)
            
        except UnicodeDecodeError:
            return ToolResult.fail(f"Error: File is not a valid text file (encoding error): {path}")
        except PermissionError:
            return ToolResult.fail(f"Error: Permission denied accessing {path}")
        except Exception as e:
            return ToolResult.fail(f"Error editing file: {str(e)}")
    
    def _resolve_path(self, path: str) -> str:
        """
        Resolve path to absolute path
        
        :param path: Relative or absolute path
        :return: Absolute path
        """
        # Expand ~ to user home directory
        path = expand_path(path)
        if os.path.isabs(path):
            return path
        return os.path.abspath(os.path.join(self.cwd, path))

```

## agent/tools/mcp/tool_retrieval.py

Source: https://github.com/zhayujie/CowAgent/blob/354bc709bab71cf6e400addfc92a00a0dd2838ff/agent/tools/mcp/tool_retrieval.py

SHA-256: `d4e3da07f7466c6c2d073fc679ab5abaa8cb2fcce92135766538ba0ccab4c7e4`

```
# encoding:utf-8
"""
On-demand MCP tool retrieval.

Pure, stateless selection helpers used by the streaming executor to decide
which MCP tools to inject into a given LLM turn. Vector precompute + caching
live in ToolManager (the tool-lifecycle owner, a process-wide singleton);
only the context-aware selection lives here, because only the executor knows
the conversation context.

Invariants (per maintainer review of the feature proposal):
  * Built-in tools are never handled here — the caller injects them in full.
  * The legacy selector returns None on any failure / missing input so the
    caller falls back to full injection; tools must never be silently dropped.
    The metadata selector represents the same fallback as a decision with a
    ``fallback_reason``.
  * Selection is union-accumulated across turns by the caller (only-grows),
    so a tool that already produced a tool_use in the message history can
    never disappear from the schema mid-run (which would make Claude/MiniMax
    raise a message-format error).
"""
from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# How many trailing messages to concatenate into the retrieval query. Tool
# needs drift across a multi-turn tool-call loop, so a single (initial) user
# query is not enough; a short recent window captures the drift without
# bloating the query with stale context.
DEFAULT_QUERY_MESSAGES = 5


@dataclass(frozen=True)
class McpRetrievalDecision:
    """Metadata for one MCP tool retrieval decision.

    ``selected`` is the accumulated tool set to inject. ``ranked`` contains the
    current turn's similarity ranking, without query text or raw vectors.
    """
    selected: Set[str]
    ranked: List[Tuple[str, float]]
    candidate_count: int
    fallback_reason: Optional[str] = None


def build_retrieval_query(messages: list, max_messages: int = DEFAULT_QUERY_MESSAGES) -> str:
    """Concatenate the text of the most recent messages into a retrieval query.

    Only ``text`` content blocks are kept; ``tool_use`` / ``tool_result`` blocks
    are skipped so the query stays short and focused on natural-language intent
    rather than large serialized tool payloads.

    Args:
        messages: Claude-style message list, each ``{"role", "content"}`` where
            content is either a string or a list of typed blocks.
        max_messages: Size of the trailing window to consider.

    Returns:
        A single string (possibly empty if no text is found).
    """
    if not messages:
        return ""

    parts: List[str] = []
    for message in messages[-max_messages:]:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            if content.strip():
                parts.append(content.strip())
            continue
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
    return "\n".join(parts)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def select_mcp_tools(
    query_vector: Optional[Sequence[float]],
    tool_vectors: Dict[str, Sequence[float]],
    top_k: int,
    already_selected: Optional[Set[str]] = None,
) -> Optional[Set[str]]:
    """Return the accumulated set of MCP tool names to inject this turn.

    Computes cosine similarity between ``query_vector`` and each candidate
    tool vector, keeps the ``top_k`` best, and unions them with
    ``already_selected`` so the injected set only ever grows within a run.

    Args:
        query_vector: Embedding of the current retrieval query, or None.
        tool_vectors: ``{mcp_tool_name: vector}`` for candidate MCP tools.
        top_k: Max number of tools to add from this turn's ranking.
        already_selected: Names accumulated in previous turns of this run.

    Returns:
        The union set of tool names to inject, or None to signal
        "fall back to full injection" (no query vector, empty/invalid index,
        or any unexpected error). This function never raises.
    """
    decision = select_mcp_tools_with_metadata(
        query_vector,
        tool_vectors,
        top_k,
        already_selected,
    )
    if decision is None or decision.fallback_reason is not None:
        return None
    return decision.selected


def _is_finite_vector(vector: Sequence[float]) -> bool:
    """Return whether every vector value is a finite number."""
    try:
        return all(math.isfinite(float(value)) for value in vector)
    except (TypeError, ValueError):
        return False


def select_mcp_tools_with_metadata(
    query_vector: Optional[Sequence[float]],
    tool_vectors: Dict[str, Sequence[float]],
    top_k: int,
    already_selected: Optional[Set[str]] = None,
) -> Optional[McpRetrievalDecision]:
    """Return MCP retrieval selection plus metadata for observability.

    A decision with ``fallback_reason`` set describes a safe full-injection
    fallback. The legacy ``select_mcp_tools`` wrapper converts that decision
    back to ``None`` so existing callers keep their current behavior.
    """
    accumulated: Set[str] = set(already_selected) if already_selected else set()

    try:
        if query_vector is None:
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="missing_query_vector",
            )
        if len(query_vector) == 0:
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="missing_query_vector",
            )
        if not _is_finite_vector(query_vector):
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="invalid_query_vector",
            )
        if not tool_vectors:
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="empty_tool_index",
            )
        if top_k <= 0:
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="invalid_top_k",
            )

        expected_dim = len(query_vector)
        # Only rank candidates whose vector dimensionality matches the query.
        # A dimension mismatch means the index was built with a different
        # embedding model; ranking across dims is meaningless.
        candidates = {}
        for name, vec in tool_vectors.items():
            try:
                if (
                    vec is not None
                    and len(vec) > 0
                    and len(vec) == expected_dim
                    and _is_finite_vector(vec)
                ):
                    candidates[name] = vec
            except (TypeError, ValueError):
                continue
        if not candidates:
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="no_compatible_candidates",
            )

        ranked = _rank_by_similarity(query_vector, candidates)
        if not all(math.isfinite(float(score)) for _name, score in ranked):
            return McpRetrievalDecision(
                selected=accumulated,
                ranked=[],
                candidate_count=0,
                fallback_reason="non_finite_score",
            )
        accumulated.update(name for name, _score in ranked[:top_k])
        return McpRetrievalDecision(
            selected=accumulated,
            ranked=ranked,
            candidate_count=len(candidates),
        )
    except Exception:
        # Selection must never break the agent — fall back to full injection.
        return McpRetrievalDecision(
            selected=accumulated,
            ranked=[],
            candidate_count=0,
            fallback_reason="selection_error",
        )


def _rank_by_similarity(
    query_vector: Sequence[float],
    candidates: Dict[str, Sequence[float]],
) -> List[tuple]:
    """Return ``[(name, score), ...]`` sorted by descending cosine similarity.

    Uses numpy when available (vectorized, matching the memory-search path),
    with a pure-Python fallback so the feature works without numpy installed.
    """
    names = list(candidates.keys())

    if _HAS_NUMPY:
        matrix = np.array([candidates[n] for n in names], dtype=np.float32)  # (N, D)
        q_vec = np.array(query_vector, dtype=np.float32)                     # (D,)
        dots = matrix @ q_vec                                                # (N,)
        row_norms = np.linalg.norm(matrix, axis=1)                          # (N,)
        q_norm = float(np.linalg.norm(q_vec))
        denominators = row_norms * q_norm
        np.maximum(denominators, 1e-10, out=denominators)                   # avoid div-by-zero
        sims = dots / denominators
        order = np.argsort(sims)[::-1]
        return [(names[i], float(sims[i])) for i in order]

    scored = [(n, cosine_similarity(query_vector, candidates[n])) for n in names]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

```

## agent/tools/read/read.py

Source: https://github.com/zhayujie/CowAgent/blob/354bc709bab71cf6e400addfc92a00a0dd2838ff/agent/tools/read/read.py

SHA-256: `61dd782ea62d73bfd710f392f0cf52d83f3ff3d2ba39d23100abb2b452b915dd`

```
"""
Read tool - Read file contents
Supports text files, images (jpg, png, gif, webp), and PDF files
"""

import os
import shlex
import stat
import sys
from typing import Dict, Any, Optional
from pathlib import Path

from agent.tools.base_tool import BaseTool, ToolResult
from agent.tools.utils.credentials import DENIED_MESSAGE, is_credential_path
from agent.tools.utils.file_state import note_read
from agent.tools.utils.truncate import truncate_head, format_size, DEFAULT_MAX_LINES, DEFAULT_MAX_BYTES
from common.utils import expand_path

EMPTY_FILE_NOTICE = "[File exists but is empty]"

_IS_WIN = sys.platform == "win32"

# PowerShell ends a single-quoted string at any of these, not only at the
# ASCII apostrophe, and a doubled one stands for itself.
_PS_SINGLE_QUOTES = "'\u2018\u2019\u201a\u201b"


def split_lines(content: str) -> list:
    """Split into lines, dropping the empty element after a trailing newline.

    Without this a file ending in "\\n" reports one line too many and renders a
    phantom numbered blank line at the end.
    """
    lines = content.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines


PDF_MAX_PAGES_PER_READ = 20


def _parse_page_range(pages, total_pages: int):
    """Parse a PDF ``pages`` argument into an inclusive (first, last) pair.

    Accepts "5", "2-8" and the open-ended "10-". Defaults to the first
    PDF_MAX_PAGES_PER_READ pages so a large document is never parsed whole.
    """
    if total_pages <= 0:
        return 1, 0

    spec = str(pages or "").strip()
    if not spec:
        return 1, min(PDF_MAX_PAGES_PER_READ, total_pages)

    if '-' in spec:
        head, _, tail = spec.partition('-')
        head, tail = head.strip(), tail.strip()
    else:
        head = tail = spec.strip()

    try:
        first = int(head) if head else 1
        last = int(tail) if tail else total_pages
    except ValueError:
        raise ValueError(f'Invalid pages value "{spec}". Use e.g. "3", "1-5" or "10-".')

    if first < 1 or last < first:
        raise ValueError(f'Invalid pages range "{spec}" for a {total_pages}-page document.')
    if first > total_pages:
        raise ValueError(f'Page {first} is beyond the end of the document ({total_pages} pages).')

    last = min(last, total_pages, first + PDF_MAX_PAGES_PER_READ - 1)
    return first, last


def number_lines(text: str, start_line: int) -> str:
    """Prefix each line with its 1-indexed file line number as ``12|code``.

    The compact gutter (no padding) keeps the token cost low while letting the
    model line up read output with grep hits and ask for a precise offset.
    Callers must tell the model these prefixes are display-only.
    """
    return '\n'.join(
        f"{start_line + i}|{line}" for i, line in enumerate(text.split('\n'))
    )


def _long_line_command(path: str, line_number: int) -> str:
    """A bash tool command that prints up to DEFAULT_MAX_BYTES bytes of one line.

    On Windows the bash tool runs cmd.exe, which has no tail or head, so the
    line is read with PowerShell there. It writes raw bytes, so the console
    code page does not matter, and it stops before a character the limit would
    cut, so the output is valid UTF-8 and stays within the bash tool's limit.
    """
    if not _IS_WIN:
        return f"tail -n +{line_number} -- {shlex.quote(path)} | head -c {DEFAULT_MAX_BYTES}"
    literal = "'" + "".join(c * 2 if c in _PS_SINGLE_QUOTES else c for c in path) + "'"
    if "%" in path:
        # cmd.exe expands %NAME% even inside double quotes, so never hand it a %.
        literal = "(" + literal.replace("%", "'+[char]37+'") + ")"
    return (
        'powershell -NoProfile -Command "'
        f"$bytes = [Text.Encoding]::UTF8.GetBytes([string](Get-Content -LiteralPath {literal} -Encoding UTF8"
        f" | Select-Object -Skip {line_number - 1} -First 1)); "
        f"$end = [Math]::Min($bytes.Length, {DEFAULT_MAX_BYTES}); "
        # Step back over UTF-8 continuation bytes so no character is cut.
        "while ($end -lt $bytes.Length -and ($bytes[$end] -band 0xC0) -eq 0x80) { $end-- }; "
        '[Console]::OpenStandardOutput().Write($bytes, 0, $end)"'
    )


class Read(BaseTool):
    """Tool for reading file contents"""
    
    name: str = "read"
    description: str = f"Read or inspect file contents. For text/PDF/Word/Excel/PPT files, returns content (truncated to {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB). Each line is prefixed with its line number as `12|content` - these prefixes are display aids for locating lines and are NOT part of the file, so strip them whenever you reuse the content (in edit's oldText/newText, when writing it elsewhere, or when quoting it to the user). For images/videos/audio, returns metadata only (file info, size, type). Use offset/limit for large text files."
    
    params: dict = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read. IMPORTANT: Relative paths are based on workspace directory. To access files outside workspace, use absolute paths starting with ~ or /."
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed, optional). Use negative values to read from end (e.g. -20 for last 20 lines)"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read (optional)"
            },
            "pages": {
                "type": "string",
                "description": f"PDF only: page range to extract, e.g. \"3\", \"1-5\" or \"10-\". Defaults to the first {PDF_MAX_PAGES_PER_READ} pages; at most {PDF_MAX_PAGES_PER_READ} pages are read per call."
            }
        },
        "required": ["path"]
    }
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cwd = self.config.get("cwd", os.getcwd())
        
        # File type categories
        self.image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico'}
        self.video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v'}
        self.audio_extensions = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma'}
        self.binary_extensions = {'.exe', '.dll', '.so', '.dylib', '.bin', '.dat', '.db', '.sqlite'}
        self.archive_extensions = {'.zip', '.tar', '.gz', '.rar', '.7z', '.bz2', '.xz'}
        self.pdf_extensions = {'.pdf'}
        self.office_extensions = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}

        # Readable text formats (will be read with truncation)
        self.text_extensions = {
            '.txt', '.md', '.markdown', '.rst', '.log', '.csv', '.tsv', '.json', '.xml', '.yaml', '.yml',
            '.py', '.js', '.ts', '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs', '.rb', '.php',
            '.html', '.css', '.scss', '.sass', '.less', '.vue', '.jsx', '.tsx',
            '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
            '.sql', '.r', '.m', '.swift', '.kt', '.scala', '.clj', '.erl', '.ex',
            '.dockerfile', '.makefile', '.cmake', '.gradle', '.properties', '.ini', '.conf', '.cfg',
        }
    
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute file read operation
        
        :param args: Contains file path and optional offset/limit parameters
        :return: File content or error message
        """
        # Support 'location' as alias for 'path' (LLM may use it from skill listing)
        path = args.get("path", "") or args.get("location", "")
        path = path.strip() if isinstance(path, str) else ""
        offset = args.get("offset")
        limit = args.get("limit")
        pages = args.get("pages")

        if not path:
            return ToolResult.fail("Error: path parameter is required")
        
        # Resolve path
        absolute_path = self._resolve_path(path)

        # Reached only once the path already missed, so nothing that resolves
        # today changes: a "knowledge/..." miss is retried under the shared
        # root before giving up. Runs ahead of the credential check below so
        # whatever is finally read is still screened by it.
        if not os.path.exists(absolute_path):
            shared_page = self._shared_knowledge_path(path)
            if shared_page:
                absolute_path = shared_page

        # Security check: block credential files and their aliases.
        # See issue #2913 (/proc/self/environ bypass) and #2863 (scope).
        if self._is_credential_path(absolute_path):
            return ToolResult.fail(DENIED_MESSAGE)
        
        # Check if file exists. Both misses below name the tool that answers
        # the question instead - otherwise the model tends to guess at nearby
        # paths and burn turns on more failed reads.
        if not os.path.exists(absolute_path):
            hint = "Hint: use search_files with target='files' to locate it by name."
            # Provide helpful hint if using relative path
            if not os.path.isabs(path) and not path.startswith('~'):
                return ToolResult.fail(
                    f"Error: File not found: {path}\n"
                    f"Resolved to: {absolute_path}\n"
                    f"Hint: Relative paths are based on workspace ({self.cwd}). For files outside workspace, use absolute paths.\n"
                    f"{hint}"
                )
            return ToolResult.fail(f"Error: File not found: {path}\n{hint}")

        if os.path.isdir(absolute_path):
            return ToolResult.fail(
                f"Error: {path} is a directory, not a file. "
                f"Use the ls tool to list what is inside it."
            )
        
        # A FIFO has size zero yet open/read waits indefinitely for a writer.
        # Inspect the resolved target before dispatching to any content parser.
        try:
            mode = os.stat(absolute_path).st_mode
        except OSError as exc:
            return ToolResult.fail(f"Error inspecting file: {exc}")
        if not stat.S_ISREG(mode):
            return ToolResult.fail(f"Error: {path} is not a regular file")

        # Check if readable
        if not os.access(absolute_path, os.R_OK):
            return ToolResult.fail(f"Error: File is not readable: {path}")
        
        # Check file type
        file_ext = Path(absolute_path).suffix.lower()
        file_size = os.path.getsize(absolute_path)
        
        # Check if image - return metadata for sending
        if file_ext in self.image_extensions:
            return self._read_image(absolute_path, file_ext)
        
        # Check if video/audio/binary/archive - return metadata only
        if file_ext in self.video_extensions:
            return self._return_file_metadata(absolute_path, "video", file_size)
        if file_ext in self.audio_extensions:
            return self._return_file_metadata(absolute_path, "audio", file_size)
        if file_ext in self.binary_extensions or file_ext in self.archive_extensions:
            return self._return_file_metadata(absolute_path, "binary", file_size)
        
        # Check if PDF
        if file_ext in self.pdf_extensions:
            return self._read_pdf(absolute_path, path, offset, limit, pages)

        # Check if Office document (.docx, .xlsx, .pptx, etc.)
        if file_ext in self.office_extensions:
            return self._read_office(absolute_path, path, file_ext, offset, limit)

        # Read text file (with truncation for large files)
        return self._read_text(absolute_path, path, offset, limit)
    
    def _shared_knowledge_path(self, path: str) -> Optional[str]:
        """An existing knowledge page under the shared root, or None.

        ``state_dir`` sends an Agent with no ``knowledge/`` of its own to the
        shared root, which is outside the workspace relative paths resolve
        against here. That is the spelling memory_search results and the links
        in index.md use, so accept it rather than making the model translate
        it. Anything else returns None, keeping this to one extra lookup on a
        path that has already missed.
        """
        if os.path.isabs(path) or path.startswith('~'):
            return None
        parts = Path(path).parts
        if len(parts) < 2 or parts[0] != 'knowledge':
            return None
        try:
            from common import state_dir
            root = os.path.realpath(str(state_dir.knowledge_dir(base=self.cwd)))
        except Exception:
            return None
        candidate = os.path.realpath(os.path.join(root, *parts[1:]))
        # "knowledge/../.." must not turn this into a general way to name files
        # elsewhere; the fallback only ever means a page under the shared root.
        if candidate != root and not candidate.startswith(root + os.sep):
            return None
        return candidate if os.path.exists(candidate) else None

    def _resolve_path(self, path: str) -> str:
        """
        Resolve path to absolute path
        
        :param path: Relative or absolute path
        :return: Absolute path
        """
        # Expand ~ to user home directory
        path = expand_path(path)
        if os.path.isabs(path):
            return path
        return os.path.abspath(os.path.join(self.cwd, path))

    @staticmethod
    def _paginate(all_lines, offset, limit, display_path):
        """Slice, truncate and number a list of lines for presentation.

        Shared by the text, PDF and Office readers so that offset/limit,
        truncation and the continuation hints behave identically everywhere -
        previously each reader reimplemented this and they had drifted apart
        (only the text reader handled negative offsets correctly).

        :return: (result_dict, error_message) - exactly one is not None.
        """
        total_lines = len(all_lines)

        if total_lines == 0:
            return {
                "content": EMPTY_FILE_NOTICE,
                "total_lines": 0,
                "start_line": 0,
                "output_lines": 0,
                "is_empty": True,
            }, None

        start_line = 0
        if offset is not None:
            if offset < 0:
                # -20 means "the last 20 lines".
                start_line = max(0, total_lines + offset)
            else:
                start_line = max(0, offset - 1)
                if start_line >= total_lines:
                    return None, (
                        f"Error: Offset {offset} is beyond end of file "
                        f"({total_lines} lines total)"
                    )

        end_line = total_lines
        user_limited = False
        if limit is not None:
            # limit=0 used to return an empty page with a "continue" hint that
            # pointed back at the same line; a negative one sliced from the end.
            if limit <= 0:
                return None, (
                    f"Error: limit must be a positive number of lines (got {limit}). "
                    f"Omit limit to read the whole file, or pass limit=20 to read 20 lines."
                )
            end_line = min(start_line + limit, total_lines)
            user_limited = True

        selected = '\n'.join(all_lines[start_line:end_line])
        truncation = truncate_head(selected)
        start_display = start_line + 1

        if truncation.first_line_exceeds_limit:
            size = format_size(len(all_lines[start_line].encode('utf-8')))
            return {
                "content": (
                    f"[Line {start_display} is {size}, exceeds "
                    f"{format_size(DEFAULT_MAX_BYTES)} limit. Use bash tool to read: "
                    f"{_long_line_command(expand_path(display_path), start_display)}]"
                ),
                "total_lines": total_lines,
                "start_line": start_display,
                "output_lines": 0,
                "details": {"truncation": truncation.to_dict()},
            }, None

        output_text = number_lines(truncation.content, start_display)
        details = {}

        if truncation.truncated:
            end_display = start_display + truncation.output_lines - 1
            limit_note = (
                "" if truncation.truncated_by == "lines"
                else f" ({format_size(DEFAULT_MAX_BYTES)} limit)"
            )
            output_text += (
                f"\n\n[Showing lines {start_display}-{end_display} of {total_lines}"
                f"{limit_note}. Use offset={end_display + 1} to continue.]"
            )
            details["truncation"] = truncation.to_dict()
        elif user_limited and end_line < total_lines:
            output_text += (
                f"\n\n[{total_lines - end_line} more lines in file. "
                f"Use offset={end_line + 1} to continue.]"
            )

        result = {
            "content": output_text,
            "total_lines": total_lines,
            "start_line": start_display,
            "output_lines": truncation.output_lines,
        }
        if details:
            result["details"] = details
        return result, None

    def _is_credential_path(self, absolute_path: str) -> bool:
        """Shared guard; see agent.tools.utils.credentials.is_credential_path."""
        return is_credential_path(absolute_path)

    def _return_file_metadata(self, absolute_path: str, file_type: str, file_size: int) -> ToolResult:
        """
        Return file metadata for non-readable files (video, audio, binary, etc.)
        
        :param absolute_path: Absolute path to the file
        :param file_type: Type of file (video, audio, binary, etc.)
        :param file_size: File size in bytes
        :return: File metadata
        """
        file_name = Path(absolute_path).name
        file_ext = Path(absolute_path).suffix.lower()
        
        # Determine MIME type
        mime_types = {
            # Video
            '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
            '.mkv': 'video/x-matroska', '.webm': 'video/webm',
            # Audio
            '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
            '.m4a': 'audio/mp4', '.flac': 'audio/flac',
            # Binary
            '.zip': 'application/zip', '.tar': 'application/x-tar',
            '.gz': 'application/gzip', '.rar': 'application/x-rar-compressed',
        }
        mime_type = mime_types.get(file_ext, 'application/octet-stream')
        
        result = {
            "type": f"{file_type}_metadata",
            "file_type": file_type,
            "path": absolute_path,
            "file_name": file_name,
            "mime_type": mime_type,
            "size": file_size,
            "size_formatted": format_size(file_size),
            "message": f"{file_type.capitalize()} 文件: {file_name} ({format_size(file_size)})\n提示: 如果需要发送此文件，请使用 send 工具。"
        }
        
        return ToolResult.success(result)
    
    def _read_image(self, absolute_path: str, file_ext: str) -> ToolResult:
        """
        Read image file - always return metadata only (images should be sent, not read into context)
        
        :param absolute_path: Absolute path to the image file
        :param file_ext: File extension
        :return: Result containing image metadata for sending
        """
        try:
            # Get file size
            file_size = os.path.getsize(absolute_path)
            
            # Determine MIME type
            mime_type_map = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp'
            }
            mime_type = mime_type_map.get(file_ext, 'image/jpeg')
            
            # Return metadata for images (NOT file_to_send - use send tool to actually send)
            result = {
                "type": "image_metadata",
                "file_type": "image",
                "path": absolute_path,
                "mime_type": mime_type,
                "size": file_size,
                "size_formatted": format_size(file_size),
                "message": f"图片文件: {Path(absolute_path).name} ({format_size(file_size)})\n提示: 如果需要发送此图片，请使用 send 工具。"
            }
            
            return ToolResult.success(result)
            
        except Exception as e:
            return ToolResult.fail(f"Error reading image file: {str(e)}")
    
    def _read_text(self, absolute_path: str, display_path: str, offset: int = None, limit: int = None) -> ToolResult:
        """
        Read text file
        
        :param absolute_path: Absolute path to the file
        :param display_path: Path to display
        :param offset: Starting line number (1-indexed)
        :param limit: Maximum number of lines to read
        :return: File content or error message
        """
        try:
            # Check file size first
            file_size = os.path.getsize(absolute_path)
            MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
            
            if file_size > MAX_FILE_SIZE:
                # File too large, return metadata only
                return ToolResult.success({
                    "type": "file_to_send",
                    "file_type": "document",
                    "path": absolute_path,
                    "size": file_size,
                    "size_formatted": format_size(file_size),
                    "message": f"文件过大 ({format_size(file_size)} > 50MB)，无法读取内容。文件路径: {absolute_path}"
                })
            
            # Read file (utf-8-sig strips BOM automatically on Windows)
            # Note: Truncation is unified via truncate_head (DEFAULT_MAX_LINES / DEFAULT_MAX_BYTES)
            # so that offset/limit can paginate the entire file correctly.
            with open(absolute_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()

            result, error = self._paginate(
                split_lines(content), offset, limit, display_path
            )
            if error:
                return ToolResult.fail(error)

            # Record the read so edit/write can warn if the file changes later.
            note_read(absolute_path)
            return ToolResult.success(result)
            
        except UnicodeDecodeError:
            return ToolResult.fail(f"Error: File is not a valid text file (encoding error): {display_path}")
        except Exception as e:
            return ToolResult.fail(f"Error reading file: {str(e)}")
    
    def _read_office(self, absolute_path: str, display_path: str, file_ext: str,
                     offset: int = None, limit: int = None) -> ToolResult:
        """Read Office documents (.docx, .xlsx, .pptx) using python-docx / openpyxl / python-pptx."""
        try:
            text = self._extract_office_text(absolute_path, file_ext)
        except ImportError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"Error reading Office document: {e}")

        if not text or not text.strip():
            return ToolResult.success({
                "content": f"[Office file {Path(absolute_path).name}: no text content could be extracted]",
            })

        result, error = self._paginate(
            split_lines(text), offset, limit, display_path
        )
        if error:
            return ToolResult.fail(error)

        note_read(absolute_path)
        return ToolResult.success(result)

    @staticmethod
    def _extract_office_text(absolute_path: str, file_ext: str) -> str:
        """Extract plain text from an Office document."""
        if file_ext in ('.docx', '.doc'):
            try:
                from docx import Document
            except ImportError:
                raise ImportError("Error: python-docx library not installed. Install with: pip install python-docx")
            from common.office_text import iter_docx_body_text
            doc = Document(absolute_path)
            return '\n'.join(iter_docx_body_text(doc))

        if file_ext in ('.xlsx', '.xls'):
            try:
                from openpyxl import load_workbook
            except ImportError:
                raise ImportError("Error: openpyxl library not installed. Install with: pip install openpyxl")
            from common.office_text import spreadsheet_sheets
            parts = []
            with spreadsheet_sheets(absolute_path, load_workbook) as sheets:
                for name, rows in sheets:
                    parts.append(f"--- Sheet: {name} ---")
                    parts.extend('\t'.join(row) for row in rows)
            return '\n'.join(parts)

        if file_ext in ('.pptx', '.ppt'):
            try:
                from pptx import Presentation
            except ImportError:
                raise ImportError("Error: python-pptx library not installed. Install with: pip install python-pptx")
            from common.office_text import iter_pptx_shape_text
            prs = Presentation(absolute_path)
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                parts.append(f"--- Slide {i} ---")
                parts.extend(iter_pptx_shape_text(slide.shapes))
            return '\n'.join(parts)

        return ""

    def _read_pdf(self, absolute_path: str, display_path: str, offset: int = None,
                  limit: int = None, pages: str = None) -> ToolResult:
        """
        Read PDF file content
        
        :param absolute_path: Absolute path to the file
        :param display_path: Path to display
        :param offset: Starting line number (1-indexed)
        :param limit: Maximum number of lines to read
        :return: PDF text content or error message
        """
        try:
            # Try to import pypdf
            try:
                from pypdf import PdfReader
            except ImportError:
                return ToolResult.fail(
                    "Error: pypdf library not installed. Install with: pip install pypdf"
                )
            
            # Read PDF
            reader = PdfReader(absolute_path)
            total_pages = len(reader.pages)

            try:
                first_page, last_page = _parse_page_range(pages, total_pages)
            except ValueError as e:
                return ToolResult.fail(f"Error: {e}")

            # Only extract the requested window; a large PDF is expensive to
            # parse in full and the model rarely needs every page.
            text_parts = []
            for page_num in range(first_page, last_page + 1):
                page_text = reader.pages[page_num - 1].extract_text()
                if page_text.strip():
                    text_parts.append(f"--- Page {page_num} ---\n{page_text}")

            if not text_parts:
                scope = (
                    f"pages {first_page}-{last_page} of {total_pages}"
                    if (first_page, last_page) != (1, total_pages)
                    else f"{total_pages} pages"
                )
                return ToolResult.success({
                    "content": f"[PDF file with {scope}, but no text content could be extracted]",
                    "total_pages": total_pages,
                    "message": "PDF may contain only images or be encrypted"
                })

            result, error = self._paginate(
                split_lines("\n\n".join(text_parts)), offset, limit, display_path
            )
            if error:
                return ToolResult.fail(error)

            result["total_pages"] = total_pages
            result["pages_read"] = f"{first_page}-{last_page}"
            if last_page < total_pages:
                result["content"] += (
                    f"\n\n[Read pages {first_page}-{last_page} of {total_pages}. "
                    f"Use pages=\"{last_page + 1}-\" to continue.]"
                )

            note_read(absolute_path)
            return ToolResult.success(result)
            
        except Exception as e:
            return ToolResult.fail(f"Error reading PDF file: {str(e)}")

```

## agent/tools/subagent/subagent.py

Source: https://github.com/zhayujie/CowAgent/blob/354bc709bab71cf6e400addfc92a00a0dd2838ff/agent/tools/subagent/subagent.py

SHA-256: `613bb43d3c8a4ffe8ca4542844c26b415dfb2aae424a20689ff0b01b0dcb8a46`

```
"""The tool the main Agent uses to hand work to a sub agent.

Deliberately not the tool for reaching a peer Agent. A sub agent is a way this
Agent gets work done; a peer has its own workspace, identity and permissions, so
handing work across that boundary is a different decision, needs a different
authorization, and belongs in its own tool.
"""

import json
import threading
import uuid
from typing import List

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger


_STATIC_DESCRIPTION = (
    "Hand a self-contained task to a sub agent that works in its own context "
    "and reports back with only its conclusion."
)

# How much of a failed step's error travels out with it. Enough to say what
# went wrong, not enough for twenty of them to weigh on the stream.
_STEP_ERROR_CHARS = 300


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"


def _error_text(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    return f"{text[:_STEP_ERROR_CHARS]}…" if len(text) > _STEP_ERROR_CHARS else text


def format_results(results: List[dict]) -> str:
    """The spawn's outcome, written for a person.

    The tool returns JSON because that is what the parent model parses. Nobody
    who just waited minutes for a report wants to read a JSON blob, so the
    same outcome goes out a second time as markdown for whoever is watching.
    """
    numbered = len(results) > 1
    blocks = []
    for position, item in enumerate(results, 1):
        heading = item.get("subagent_type") or "subagent"
        if numbered:
            heading = f"{position}. {heading}"
        if item.get("duration_seconds"):
            heading += f" · {_format_duration(item['duration_seconds'])}"

        status = item.get("status")
        body = item.get("summary") or item.get("error") or "(no output)"
        if status != "completed":
            body = f"**{status or 'unknown'}** — {body}"
        blocks.append(f"### {heading}\n\n{body}")
    return "\n\n---\n\n".join(blocks)


class _SpawnView:
    """What someone following the run gets to see of one spawn call.

    The parent's context is unaffected by any of this: it still receives only
    the returned summary, which is the whole point of spawning. This is the
    other audience — the person watching — for whom a sub agent that reports
    nothing until it finishes is indistinguishable from one that hung.
    """

    def __init__(self, tool, tasks: List):
        self.tool = tool
        self.tasks = tasks
        # Several sub agents need a card each; one spinner cannot say which of
        # them is still going. A lone sub agent already has the spawn call's
        # own card, and a second card for the same work is just noise.
        self.own_cards = len(tasks) > 1
        if self.own_cards:
            self.card_ids = [uuid.uuid4().hex[:12] for _ in tasks]
        else:
            self.card_ids = [getattr(tool, "tool_call_id", None) or uuid.uuid4().hex[:12]]
        self._closed = set()
        # Files each sub agent wrote, by task. Sub agents run on their own
        # threads, so this is written from several at once.
        self._files: dict = {}
        self._lock = threading.Lock()

    def files_for(self, index: int) -> List[str]:
        with self._lock:
            return list(self._files.get(index, ()))

    def on_state(self, index: int, state: dict) -> None:
        if not self.own_cards:
            return
        name = f"subagent:{state.get('subagent_type') or 'unknown'}"
        if state.get("status") == "running":
            self.tool.emit_event("tool_execution_start", {
                "tool_call_id": self.card_ids[index],
                "tool_name": name,
                "arguments": {"goal": self.tasks[index].goal},
            })
            return
        # A task cancelled on timeout settles twice: once when the timeout is
        # declared, once when the run it belongs to notices. The first is the
        # one that explains what happened. Those two arrive on different
        # threads, so claiming the task has to be one step.
        with self._lock:
            if index in self._closed:
                return
            self._closed.add(index)
        completed = state.get("status") == "completed"
        self.tool.emit_event("tool_execution_end", {
            "tool_call_id": self.card_ids[index],
            "tool_name": name,
            "status": "success" if completed else "error",
            "result": state.get("summary") or state.get("error") or "",
            "display": format_results([state]),
            "execution_time": state.get("duration_seconds", 0),
        })

    def on_event(self, index: int, event: dict) -> None:
        """Relay the parts of a sub agent's run that belong to this spawn.

        Its tool calls and the files it writes describe work the user asked
        for. Its prose and reasoning do not travel: those streams render as
        the assistant speaking, and a sub agent talking to itself in the main
        reply reads as the assistant losing the thread.
        """
        event_type = event.get("type")
        data = event.get("data") or {}

        if event_type == "artifact":
            path = data.get("path")
            if path:
                with self._lock:
                    self._files.setdefault(index, []).append(path)
            self.tool.emit_event("artifact", data)
        elif event_type == "tool_execution_start":
            self.tool.emit_event("subagent_step", {
                "card_id": self.card_ids[index],
                "step_id": self._step_id(index, data),
                "phase": "start",
                "tool_name": data.get("tool_name", "tool"),
                "arguments": data.get("arguments") or {},
            })
        elif event_type == "tool_execution_end":
            status = data.get("status", "success")
            step = {
                "card_id": self.card_ids[index],
                "step_id": self._step_id(index, data),
                "phase": "end",
                "tool_name": data.get("tool_name", "tool"),
                "status": status,
                "execution_time": round(data.get("execution_time") or 0, 2),
            }
            # What a step returned is already accounted for in the sub agent's
            # report, so carrying it here would be the same findings a second
            # time, in the one place too small to read them. An error is the
            # exception: nothing else says why that step came up empty.
            if status != "success":
                step["error"] = _error_text(data.get("result"))
            self.tool.emit_event("subagent_step", step)

    def _step_id(self, index: int, data: dict) -> str:
        # Sub agents pick tool call ids independently, so two of them running
        # at once can choose the same one. The card scopes it back to unique.
        return f"{self.card_ids[index]}:{data.get('tool_call_id') or uuid.uuid4().hex[:8]}"


class SubagentTool(BaseTool):
    name = "subagent"
    # Sub agents share nothing but the workspace, so two of them running at
    # once is the same situation as two tasks inside one call. Models routinely
    # express independent work as several calls rather than one call with a
    # list; this lets that phrasing run just as fast.
    parallel_safe = True

    params = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "What the sub agent should accomplish. Write it as a complete "
                    "instruction to someone who has never seen this conversation."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Everything the sub agent needs and cannot look up: file paths, "
                    "error text, decisions already made, what to leave alone. It "
                    "cannot see your conversation, so anything you omit is lost."
                ),
            },
            "subagent_type": {
                "type": "string",
                "description": "Which kind of sub agent to use. See the list in this tool's description.",
            },
            "tasks": {
                "type": "array",
                "description": (
                    "Run several tasks at once instead of one. Each entry takes "
                    "the same goal / context / subagent_type fields, and every "
                    "entry runs in parallel."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string"},
                        "context": {"type": "string"},
                        "subagent_type": {"type": "string"},
                    },
                    "required": ["goal"],
                },
            },
        },
        "required": [],
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cwd = self.config.get("cwd")

    def is_available(self) -> bool:
        """Follows the setting as it stands, not as it stood at startup, so
        switching sub agents off takes the tool away on the next turn rather
        than at the next restart."""
        from agent.subagent import SubagentSettings

        return SubagentSettings.from_config().enabled

    # --- what the model reads -------------------------------------------------

    @property
    def description(self) -> str:
        """Rebuilt on every read so a template added to the workspace is
        offered on the next turn, without a restart."""
        from agent.subagent import SubagentSettings, load_templates

        try:
            templates = load_templates(self.cwd)
            settings = SubagentSettings.from_config()
        except Exception as e:
            logger.debug(f"[SubagentTool] Falling back to static description: {e}")
            return _STATIC_DESCRIPTION

        listing = "\n".join(
            f"- {name}: {template.description}"
            for name, template in sorted(templates.items())
        )
        return (
            "Hand a self-contained task to a sub agent that runs in its own "
            "context and returns only its conclusion, keeping everything it "
            "reads or runs out of yours.\n\n"
            "Reach for it on substantial research, investigation or data "
            "gathering — one task, or several at once via `tasks` (up to "
            f"{settings.max_concurrent}) which run in parallel. A sub agent "
            "takes more time and costs more than working directly, so use it "
            "only when needed; do simple work you can finish yourself directly "
            "instead.\n\n"
            "The sub agent starts with no history and cannot reach you or the "
            "user mid-task, so state the goal in full and put every path, "
            "identifier and constraint it needs into `context`; a vague goal "
            "comes back as a vague answer. Its reply is not shown to the user, "
            "so read it and say what matters in your own words.\n\n"
            "Available sub agent types:\n"
            f"{listing}"
        )

    # --- execution ------------------------------------------------------------

    def _collect_tasks(self, params: dict) -> List:
        from agent.subagent import SubagentTask

        raw_tasks = params.get("tasks")
        if isinstance(raw_tasks, list) and raw_tasks:
            tasks = []
            for entry in raw_tasks:
                if not isinstance(entry, dict):
                    continue
                goal = str(entry.get("goal") or "").strip()
                if not goal:
                    continue
                tasks.append(
                    SubagentTask(
                        goal=goal,
                        context=str(entry.get("context") or ""),
                        subagent_type=str(entry.get("subagent_type") or "").strip() or None,
                    )
                )
            return tasks

        goal = str(params.get("goal") or "").strip()
        if not goal:
            return []
        return [
            SubagentTask(
                goal=goal,
                context=str(params.get("context") or ""),
                subagent_type=str(params.get("subagent_type") or "").strip() or None,
            )
        ]

    def renders_own_cards(self, arguments: dict) -> bool:
        """True exactly when the tasks get cards of their own, which is the
        same condition `_SpawnView` uses. A lone sub agent keeps reporting
        under the spawn call's card, so that one has to stay."""
        return len(self._collect_tasks(arguments or {})) > 1

    def _spawn_view(self, tasks: List):
        """One spawn call, as the client sees it.

        A sub agent runs for minutes behind a single tool call. Left alone the
        client shows one spinner for the lot: no telling how many are running,
        which is still going, what any of them is doing, or what they found.
        This turns the spawn into entries the client already knows how to
        render, and relays the work happening inside each one.

        Several sub agents get a card each, since one spinner cannot stand for
        all of them. A lone sub agent reports under the spawn call itself,
        which already stands for exactly it.
        """
        return _SpawnView(self, tasks)

    def execute(self, params: dict) -> ToolResult:
        from agent.subagent import SubagentSettings, current_depth, load_templates, run_tasks

        settings = SubagentSettings.from_config()
        if not settings.enabled:
            return ToolResult.fail("Sub agents are disabled. Set subagent.enabled in config.json.")

        parent = getattr(self, "context", None)
        if parent is None:
            return ToolResult.fail("No parent agent available to spawn from.")

        depth = current_depth()
        if depth >= settings.max_depth:
            return ToolResult.fail(
                f"Already {depth} level(s) deep, and subagent.max_depth is "
                f"{settings.max_depth}. Do this task yourself instead of delegating it."
            )

        tasks = self._collect_tasks(params)
        if not tasks:
            return ToolResult.fail("Provide 'goal', or 'tasks' with at least one goal.")
        if len(tasks) > settings.max_concurrent:
            return ToolResult.fail(
                f"{len(tasks)} tasks requested but subagent.max_concurrent is "
                f"{settings.max_concurrent}. Send fewer, or split across turns."
            )

        templates = load_templates(parent.workspace_dir or self.cwd)
        unknown = sorted(
            {t.subagent_type for t in tasks if t.subagent_type and t.subagent_type not in templates}
        )
        if unknown:
            return ToolResult.fail(
                f"Unknown sub agent type(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(templates))}."
            )

        # Deliberately no progress message: the goal is already the call's
        # arguments, so restating it says nothing the client is not showing.
        # What it wants during those minutes is the steps below.
        logger.info(f"[SubagentTool] Running {len(tasks)} task(s) at depth {depth + 1}")

        view = self._spawn_view(tasks)
        results = run_tasks(
            parent, tasks, templates, settings,
            on_state=view.on_state, on_event=view.on_event,
            cancel_event=self.cancel_event,
        )
        for item in results:
            # A sub agent names its files in prose, if at all. Listing them
            # spares the parent from parsing them back out of the summary, and
            # is the only record once the run's events are gone.
            files = view.files_for(item.get("task_index"))
            if files:
                item["files"] = files

        payload = json.dumps({"results": results}, ensure_ascii=False)
        display = format_results(results)
        if all(r.get("status") not in ("completed", "cancelled") for r in results):
            # Every task failed or timed out: surfacing this as success would
            # let the parent report findings that do not exist.
            return ToolResult.fail(payload, display=display)
        return ToolResult.success(payload, display=display)

```

## agent/tools/write/write.py

Source: https://github.com/zhayujie/CowAgent/blob/354bc709bab71cf6e400addfc92a00a0dd2838ff/agent/tools/write/write.py

SHA-256: `3f85734d0270afc01401a9220fcbe6e5c495a7e4f44c65fb23d6d1141208c311`

```
"""
Write tool - Write file content
Creates or overwrites files, automatically creates parent directories
"""

import os
from typing import Dict, Any

from agent.tools.base_tool import BaseTool, ToolResult
from common.atomic_write import write_text_atomic
from agent.tools.utils.credentials import is_credential_path
from agent.tools.utils.diff import (
    detect_line_ending,
    looks_like_line_numbered_block,
    normalize_to_lf,
    restore_line_endings,
)
from agent.tools.utils.file_state import note_write, staleness_warning
from agent.tools.utils.memory_path import feeds_memory_index
from agent.tools.utils.syntax_check import review as syntax_review
from common.utils import expand_path


class Write(BaseTool):
    """Tool for writing file content"""
    
    name: str = "write"
    description: str = "Write content to a file, overwriting the existing file if there is one at the path. Creates parent directories automatically. Prefer the edit tool for modifying an existing file - it only sends the text being changed. Only use write to create new files or for complete rewrites."
    
    params: dict = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write (relative or absolute)"
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file"
            }
        },
        "required": ["path", "content"]
    }
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cwd = self.config.get("cwd", os.getcwd())
        self.memory_manager = self.config.get("memory_manager", None)
    
    def execute(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute file write operation
        
        :param args: Contains file path and content
        :return: Operation result
        """
        path = args.get("path", "").strip()
        content = args.get("content", "")
        
        if not path:
            return ToolResult.fail("Error: path parameter is required")

        # write replaces the whole file, so echoing read's numbered output here
        # would corrupt every line at once.
        if looks_like_line_numbered_block(content):
            return ToolResult.fail(
                f"Error: content looks like read tool output ('12|content'), not file "
                f"content. Those line-number prefixes are display only - strip them "
                f"before writing {path}."
            )
        
        try:
            # Resolve path (may raise PermissionError for protected locations)
            absolute_path = self._resolve_path(path)

            # Create parent directory (if needed)
            parent_dir = os.path.dirname(absolute_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            
            # Check before writing - our own write would reset the mtime.
            warning = staleness_warning(absolute_path)

            previous = None
            if os.path.isfile(absolute_path):
                try:
                    # newline='' leaves the file's own line endings in place
                    # for detect_line_ending() below.
                    with open(absolute_path, 'r', encoding='utf-8', newline='') as f:
                        previous = f.read()
                except (OSError, UnicodeDecodeError):
                    previous = None
            # The syntax check gets the LF text a text-mode read would give.
            blocking, syntax_warning = syntax_review(
                absolute_path, normalize_to_lf(previous) if previous is not None else None, content
            )
            if blocking:
                return ToolResult.fail(f"Error: {blocking}")

            # LF content keeps the file's own line endings, as edit does, or
            # gets the platform's when the file has none yet. Content that
            # already uses CRLF is written as given.
            if '\r\n' not in content:
                ending = os.linesep
                if previous and ('\n' in previous or '\r' in previous):
                    ending = detect_line_ending(previous)
                content = restore_line_endings(content, ending)
            # newline='' writes content as is; on Windows text mode would turn
            # every '\n' into '\r\n' again, and '\r\n' into '\r\r\n'.
            write_text_atomic(absolute_path, content, newline='')
            note_write(absolute_path)
            
            # Get bytes written
            bytes_written = len(content.encode('utf-8'))
            
            # Auto-sync to memory database if this is a memory file
            if self.memory_manager and feeds_memory_index(
                absolute_path, self.memory_manager, self.cwd
            ):
                try:
                    self.memory_manager.mark_dirty()
                except Exception as exc:
                    syntax_warning = " ".join(filter(None, (syntax_warning, f"File saved, but memory index notification failed: {exc}")))
            
            result = {
                "message": f"Successfully wrote {bytes_written} bytes to {path}",
                "path": path,
                "bytes_written": bytes_written
            }
            warnings = [w for w in (warning, syntax_warning) if w]
            if warnings:
                result["warning"] = " ".join(warnings)
            
            return ToolResult.success(result)
            
        except PermissionError as e:
            return ToolResult.fail(f"Error: {str(e) or f'Permission denied writing to {path}'}")
        except Exception as e:
            return ToolResult.fail(f"Error writing file: {str(e)}")
    
    def _resolve_path(self, path: str) -> str:
        """
        Resolve path to absolute path

        :param path: Relative or absolute path
        :return: Absolute path
        :raises PermissionError: if the path targets a protected location
        """
        # Expand ~ to user home directory
        path = expand_path(path)
        if os.path.isabs(path):
            absolute = path
        else:
            absolute = os.path.abspath(os.path.join(self.cwd, path))

        real = os.path.realpath(absolute)

        # Always block the credentials file, mirroring the bash tool's guard.
        # The suffix rule also covers non-home .cow/.env files; the shared guard
        # adds symlink resolution and the /proc environ aliases.
        if real.endswith(os.path.join(".cow", ".env")) or is_credential_path(absolute):
            raise PermissionError("writing to ~/.cow/.env is not allowed")

        # Optional workspace confinement. Off by default to preserve existing
        # behavior; enable via config.json tools.write.restrict_to_workspace.
        if self.config.get("restrict_to_workspace"):
            ws = os.path.realpath(self.cwd)
            if os.path.commonpath([ws, real]) != ws:
                raise PermissionError(
                    f"writing outside the workspace is not allowed: {path}"
                )

        return absolute

```
