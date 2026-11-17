# letta-code — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/tools/impl/bash.ts

Source: https://github.com/letta-ai/letta-code/blob/353365bce618b7a7cbf620d0ca66f221664d8d86/src/tools/impl/bash.ts

SHA-256: `acfbea90c349f5e6fe6a65537cd856391f733add077cfc5e81bf58330509a728`

```
import { rmSync } from "node:fs";
import { INTERRUPTED_BY_USER } from "@/constants";
import {
  consumeWorkingDirectoryRecovery,
  getCurrentWorkingDirectory,
} from "@/runtime-context";
import {
  captureSecretRedactions,
  createSecretStreamScrubber,
  scrubSecretsFromString,
} from "@/tools/secret-substitution";
import {
  addToMessageQueue,
  isQueueBridgeConnected,
} from "@/utils/message-queue-bridge.js";
import {
  formatTaskNotification,
  resolveNotificationScope,
} from "@/utils/task-notifications.js";
import { noteExpectedWorktreeForLauncher } from "@/websocket/listener/worktree-ownership";
import {
  commandRunsForegroundSleep,
  FOREGROUND_SLEEP_BLOCKED_MESSAGE,
} from "./foreground-sleep.js";
import {
  appendBackgroundProcessOutput,
  appendToOutputFile,
  assertBackgroundProcessCapacity,
  type BackgroundProcess,
  backgroundProcesses,
  createBackgroundOutputFile,
  getNextBashId,
  scheduleBackgroundProcessCleanup,
  scrubCompletedBackgroundOutput,
} from "./process_manager.js";
import {
  findSelfMatchingPgrepPattern,
  selfMatchingPgrepMessage,
} from "./self-matching-pgrep.js";
import { getShellEnv } from "./shell-env.js";
import {
  buildShellLaunchers,
  selectAvailableShellLauncher,
  withStrictShellPrelude,
} from "./shell-launchers.js";
import {
  type ShellExecutionError,
  spawnWithLauncher,
  startShellProcess,
} from "./shell-runner.js";
import { applyShellSandbox } from "./shell-sandbox.js";
import { LIMITS, truncateByChars } from "./truncation.js";
import { validateRequiredParams } from "./validation.js";
import { assertSafeWindowsCommand } from "./windows-command-safety.js";

// Cache the working shell launcher after first successful spawn
let cachedWorkingLauncher: string[] | null = null;

/** Time to wait for an ordinary Bash command before returning it as a task. */
const DEFAULT_FOREGROUND_YIELD_MS = 10_000;

type ShellProcessStarter = typeof startShellProcess;
let shellProcessStarter: ShellProcessStarter = startShellProcess;

/** Test-only override for observing shell process launch options. */
export function __setShellProcessStarterForTests(
  starter: ShellProcessStarter | null,
): void {
  shellProcessStarter = starter ?? startShellProcess;
}

function rebuildCachedLauncher(
  command: string,
  secretEnv?: Record<string, string>,
): string[] | null {
  if (!cachedWorkingLauncher) return null;
  const cachedExecutable = cachedWorkingLauncher[0]?.toLowerCase();
  if (!cachedExecutable) return null;

  const launchers = buildShellLaunchers(command, {
    powershellEnvAliases: secretEnv ? Object.keys(secretEnv) : undefined,
  });
  return (
    launchers.find(
      (launcher) => launcher[0]?.toLowerCase() === cachedExecutable,
    ) ?? null
  );
}

/**
 * Get the first working shell launcher for background processes.
 * Uses cached launcher if available, otherwise returns first launcher from buildShellLaunchers.
 * For background processes, we can't easily do async fallback, so we rely on cached launcher
 * from previous foreground commands or the default launcher order.
 */
export function getBackgroundLauncher(
  command: string,
  env: NodeJS.ProcessEnv,
  secretEnv?: Record<string, string>,
): string[] {
  const cachedLauncher = rebuildCachedLauncher(command, secretEnv);
  if (cachedLauncher) return cachedLauncher;

  const launchers = buildShellLaunchers(command, {
    powershellEnvAliases: secretEnv ? Object.keys(secretEnv) : undefined,
  });
  return selectAvailableShellLauncher(launchers, env) || [];
}

/**
 * Execute a command using spawn with explicit shell.
 * This avoids the double-shell parsing that exec() does.
 * Uses buildShellLaunchers() to try multiple shells with ENOENT fallback.
 * Exported for use by bash mode in the CLI.
 */
export async function spawnCommand(
  command: string,
  options: {
    cwd: string;
    env: NodeJS.ProcessEnv;
    timeout: number;
    signal?: AbortSignal;
    onOutput?: (chunk: string, stream: "stdout" | "stderr") => void;
    secretEnv?: Record<string, string>;
  },
): Promise<{ stdout: string; stderr: string; exitCode: number | null }> {
  assertSafeWindowsCommand(command, {
    cwd: options.cwd,
    env: options.secretEnv
      ? { ...options.env, ...options.secretEnv }
      : options.env,
  });
  const env = options.secretEnv
    ? { ...options.env, ...options.secretEnv }
    : options.env;
  const commandToRun = withStrictShellPrelude(command, env);

  // On Unix (Linux/macOS), use simple bash -c approach (original behavior)
  // This avoids the complexity of fallback logic which caused issues on ARM64 CI
  if (process.platform !== "win32") {
    // On macOS, prefer zsh due to bash 3.2's HEREDOC bug with apostrophes
    const executable = process.platform === "darwin" ? "/bin/zsh" : "bash";
    const innerLauncher = [executable, "-c", commandToRun];
    const sandboxed = applyShellSandbox(innerLauncher, options.cwd, env);
    if (sandboxed.backend) {
      // The sandbox wrapper hides the inner shell from launcher inspection;
      // note the unwrapped launcher so `git worktree add` ownership resolves.
      noteExpectedWorktreeForLauncher(innerLauncher, options.cwd);
    }
    return spawnWithLauncher(sandboxed.launcher, {
      cwd: options.cwd,
      env: sandboxed.env,
      timeoutMs: options.timeout,
      sourceCommand: command,
      signal: options.signal,
      onOutput: options.onOutput,
    });
  }

  // On Windows, use fallback logic to handle PowerShell ENOENT errors (PR #482)
  if (cachedWorkingLauncher) {
    const newLauncher = rebuildCachedLauncher(commandToRun, options.secretEnv);
    if (newLauncher) {
      try {
        const result = await spawnWithLauncher(newLauncher, {
          cwd: options.cwd,
          env,
          timeoutMs: options.timeout,
          sourceCommand: command,
          signal: options.signal,
          onOutput: options.onOutput,
        });
        return result;
      } catch (error) {
        const err = error as ShellExecutionError;
        // Only an executable-lookup ENOENT justifies retrying other shells.
        // A missing cwd fails identically for every launcher.
        if (err.code !== "ENOENT" || err.reason === "cwd_missing") {
          throw error;
        }
        cachedWorkingLauncher = null;
      }
    }
  }

  const launchers = buildShellLaunchers(commandToRun, {
    powershellEnvAliases: options.secretEnv
      ? Object.keys(options.secretEnv)
      : undefined,
  });
  if (launchers.length === 0) {
    throw new Error("No shell launchers available");
  }

  const tried: string[] = [];
  let lastError: Error | null = null;

  for (const launcher of launchers) {
    try {
      const result = await spawnWithLauncher(launcher, {
        cwd: options.cwd,
        env,
        timeoutMs: options.timeout,
        sourceCommand: command,
        signal: options.signal,
        onOutput: options.onOutput,
      });
      cachedWorkingLauncher = launcher;
      return result;
    } catch (error) {
      const err = error as ShellExecutionError;
      if (err.code === "ENOENT" && err.reason !== "cwd_missing") {
        tried.push(launcher[0] || "unknown");
        lastError = err;
        continue;
      }
      throw error;
    }
  }

  const suffix = tried.filter(Boolean).join(", ");
  const reason = lastError?.message || "Shell unavailable";
  throw new Error(suffix ? `${reason} (tried: ${suffix})` : reason);
}

/** Lines of each stream to replay in a completion notification. */
const NOTIFICATION_TAIL_LINES = 50;

function formatStreamTail(
  label: "stdout" | "stderr",
  retainedLines: string[],
  totalLines: number,
): string | undefined {
  if (retainedLines.length === 0) {
    return undefined;
  }
  const tail = retainedLines.slice(-NOTIFICATION_TAIL_LINES);
  const header =
    tail.length < totalLines
      ? `[${label} - last ${tail.length} of ${totalLines} lines]`
      : `[${label}]`;
  return `${header}\n${tail.join("\n")}`;
}

function formatBackgroundOutputTail(bgProcess: BackgroundProcess): string {
  const sections = [
    formatStreamTail(
      "stdout",
      bgProcess.stdout ?? [],
      bgProcess.totalStdoutLines ?? bgProcess.stdout?.length ?? 0,
    ),
    formatStreamTail(
      "stderr",
      bgProcess.stderr ?? [],
      bgProcess.totalStderrLines ?? bgProcess.stderr?.length ?? 0,
    ),
  ].filter(Boolean);

  return sections.length > 0 ? sections.join("\n\n") : "(no output)";
}

/**
 * Queue the completion notification for a background shell.
 *
 * Mirrors the subagent path in task.ts: format the same `<task-notification>`
 * XML and hand it to the message-queue bridge, which wakes the parent turn.
 * Bash.md promises the agent it will be notified when a `run_in_background`
 * command finishes, so without this the agent plans around a wake-up that
 * never arrives (letta-ai/letta-code#3226).
 *
 * Called from the shell runner's single completion promise, which settles
 * exactly once, so the agent is woken exactly once per background shell.
 */
function notifyBackgroundCompletion(params: {
  bashId: string;
  description?: string;
  outputFile: string;
  bgProcess: BackgroundProcess;
  scope: ReturnType<typeof resolveNotificationScope>;
  status: "completed" | "failed";
  detail: string;
}): void {
  const { bashId, description, outputFile, bgProcess, scope, status, detail } =
    params;
  if (bgProcess.completionNotificationSuppressed) {
    return;
  }

  const label = description?.trim() || bgProcess.command;
  const durationMs = bgProcess.startTime
    ? Math.max(0, Date.now() - bgProcess.startTime.getTime())
    : undefined;

  const { content: result } = truncateByChars(
    scrubSecretsFromString(
      [`$ ${bgProcess.command}`, detail, formatBackgroundOutputTail(bgProcess)]
        .filter(Boolean)
        .join("\n\n"),
      bgProcess.secrets ?? {},
    ),
    LIMITS.BASH_NOTIFICATION_CHARS,
    "Bash",
    { useMiddleTruncation: true },
  );

  addToMessageQueue({
    kind: "task_notification",
    text: formatTaskNotification({
      taskId: bashId,
      status,
      summary: `Background command "${label}" ${status}`,
      result,
      outputFile,
      usage: durationMs === undefined ? undefined : { durationMs },
    }),
    ...scope,
  });
}

interface BashArgs {
  command: string;
  timeout?: number;
  description?: string;
  run_in_background?: boolean;
  signal?: AbortSignal;
  onOutput?: (chunk: string, stream: "stdout" | "stderr") => void;
  secretEnv?: Record<string, string>;
  parentScope?: { agentId: string; conversationId: string };
  /** Test-only override; not exposed in the Bash tool schema. */
  foregroundYieldMs?: number;
}

interface BashResult {
  content: Array<{
    type: string;
    text: string;
  }>;
  status: "success" | "error";
}

export async function bash(args: BashArgs): Promise<BashResult> {
  validateRequiredParams(args, ["command"], "Bash");
  const {
    command,
    timeout,
    description,
    run_in_background = false,
    signal,
    onOutput,
    secretEnv,
    parentScope,
    foregroundYieldMs = DEFAULT_FOREGROUND_YIELD_MS,
  } = args;
  const userCwd = getCurrentWorkingDirectory();
  assertSafeWindowsCommand(command, {
    cwd: userCwd,
    env: secretEnv ? { ...getShellEnv(), ...secretEnv } : getShellEnv(),
  });
  const redactions = captureSecretRedactions(
    parentScope?.agentId,
    secretEnv ?? {},
  );
  const sanitizeOutput = (text: string) =>
    scrubSecretsFromString(text, redactions);
  // Per-stream scrubbers hold back potential partial secret matches so a
  // credential split across output chunks never reaches the retained output,
  // the output file, or the completion notification unredacted.
  const streamScrubbers = {
    stdout: createSecretStreamScrubber(redactions),
    stderr: createSecretStreamScrubber(redactions),
  };

  if (command === "/bg") {
    const processes = Array.from(backgroundProcesses.entries());
    if (processes.length === 0) {
      return {
        content: [{ type: "text", text: "(no content)" }],
        status: "success",
      };
    }
    let output = "";
    for (const [id, proc] of processes) {
      const runtime = proc.startTime
        ? `${Math.floor((Date.now() - proc.startTime.getTime()) / 1000)}s`
        : "unknown";
      output += `${id}: ${proc.command} (${proc.status}, runtime: ${runtime})\n`;
    }
    return {
      content: [{ type: "text", text: output.trim() }],
      status: "success",
    };
  }

  if (!run_in_background && commandRunsForegroundSleep(command)) {
    return {
      content: [{ type: "text", text: FOREGROUND_SLEEP_BLOCKED_MESSAGE }],
      status: "error",
    };
  }

  const selfMatchingPattern = findSelfMatchingPgrepPattern(command);
  if (selfMatchingPattern !== null) {
    return {
      content: [
        { type: "text", text: selfMatchingPgrepMessage(selfMatchingPattern) },
      ],
      status: "error",
    };
  }

  // If the runtime cwd was found deleted and repaired to a fallback (e.g. the
  // agent removed its own worktree), tell the model instead of silently
  // running from a different directory.
  const recoveredFrom = consumeWorkingDirectoryRecovery();
  const recoveryNote = recoveredFrom
    ? `Note: working directory ${recoveredFrom} no longer exists; running in ${userCwd} instead.\n`
    : "";

  try {
    assertBackgroundProcessCapacity();
  } catch (error) {
    return {
      content: [
        {
          type: "text",
          text: error instanceof Error ? error.message : String(error),
        },
      ],
      status: "error",
    };
  }

  const bgEnv = secretEnv ? { ...getShellEnv(), ...secretEnv } : getShellEnv();
  const bgCommand = withStrictShellPrelude(command, bgEnv);
  const bashId = getNextBashId();
  const outputFile = createBackgroundOutputFile(bashId);
  const launcher = getBackgroundLauncher(bgCommand, bgEnv, secretEnv);
  const [executable] = launcher;
  if (!executable) {
    return {
      content: [{ type: "text", text: "No shell available" }],
      status: "error",
    };
  }
  // Note the unwrapped launcher first; the sandbox wrapper (below) hides the
  // inner shell from launcher inspection.
  noteExpectedWorktreeForLauncher(launcher, userCwd);
  const sandboxed = applyShellSandbox(launcher, userCwd, bgEnv);
  let bgProcess: BackgroundProcess;
  let outputWriteFailed = false;
  const foregroundOutput = { stdout: "", stderr: "" };
  const effectiveTimeout = run_in_background
    ? timeout !== undefined && timeout > 0
      ? timeout
      : 0
    : timeout === undefined
      ? 0
      : Math.min(Math.max(timeout, 1), 600000);
  const runningProcess = shellProcessStarter(sandboxed.launcher, {
    cwd: userCwd,
    env: sandboxed.env,
    timeoutMs: effectiveTimeout,
    sourceCommand: command,
    signal: run_in_background ? undefined : signal,
    captureOutput: false,
    onOutput(text, stream) {
      const sanitizedText = streamScrubbers[stream].push(text);
      if (!run_in_background) {
        foregroundOutput[stream] += text;
        onOutput?.(text, stream);
      }
      if (!sanitizedText) return;
      appendBackgroundProcessOutput(bgProcess, stream, sanitizedText);
      const wrote = appendToOutputFile(
        outputFile,
        stream === "stderr" ? `[stderr] ${sanitizedText}` : sanitizedText,
      );
      if (!wrote && bgProcess.status === "running") {
        outputWriteFailed = true;
        appendBackgroundProcessOutput(
          bgProcess,
          "stderr",
          "[output file write failed; output may be incomplete]",
        );
        bgProcess.status = "failed";
        try {
          runningProcess.process.kill("SIGTERM");
        } catch {
          // Process may have already exited.
        }
      }
    },
  });
  bgProcess = {
    process: runningProcess.process,
    command,
    stdout: [],
    stderr: [],
    status: "running",
    exitCode: null,
    startTime: new Date(),
    outputFile,
    totalStdoutLines: 0,
    totalStderrLines: 0,
    runtimeScope: parentScope,
    secrets: redactions,
  };
  backgroundProcesses.set(bashId, bgProcess);

  // Resolve the routing scope now rather than on completion: by the time the
  // shell exits the turn that launched it is gone, so the process-global
  // agent context may already point at a different conversation.
  const notificationScope = resolveNotificationScope(parentScope);
  // The completion promise settles after the final stdout/stderr data events,
  // so this is where held-back partial matches are flushed into the retained
  // output and the output file before the final whole-file scrub.
  const flushStreamScrubbers = () => {
    for (const stream of ["stdout", "stderr"] as const) {
      const rest = streamScrubbers[stream].flush();
      if (!rest) continue;
      appendBackgroundProcessOutput(bgProcess, stream, rest);
      appendToOutputFile(
        outputFile,
        stream === "stderr" ? `[stderr] ${rest}` : rest,
      );
    }
  };
  const settled = runningProcess.completion.then(
    ({ exitCode }) => {
      flushStreamScrubbers();
      bgProcess.status =
        exitCode === 0 && !outputWriteFailed ? "completed" : "failed";
      bgProcess.exitCode = exitCode;
      appendToOutputFile(outputFile, `\n[exit code: ${exitCode}]\n`);
      scrubCompletedBackgroundOutput(bgProcess);
      return {
        status:
          exitCode === 0 && !outputWriteFailed
            ? ("completed" as const)
            : ("failed" as const),
        detail: outputWriteFailed
          ? "Output file write failed; output may be incomplete"
          : exitCode === null
            ? "Terminated by signal before exiting"
            : `Exit code: ${exitCode}`,
        exitCode,
      };
    },
    (error: unknown) => {
      flushStreamScrubbers();
      const err = error as Error & { killed?: boolean };
      const message = sanitizeOutput(
        err.killed
          ? `Command timed out after ${effectiveTimeout}ms`
          : err.message,
      );
      bgProcess.status = "failed";
      appendBackgroundProcessOutput(bgProcess, "stderr", message);
      appendToOutputFile(
        outputFile,
        err.killed
          ? `\n[timeout after ${effectiveTimeout}ms]\n`
          : `\n[error] ${message}\n`,
      );
      scrubCompletedBackgroundOutput(bgProcess);
      return {
        status: "failed" as const,
        detail: err.killed ? message : `Error: ${message}`,
        error: err,
      };
    },
  );

  const notifyWhenSettled = () => {
    void settled.then(({ status, detail }) => {
      notifyBackgroundCompletion({
        bashId,
        description,
        outputFile,
        bgProcess,
        scope: notificationScope,
        status,
        detail,
      });
      scheduleBackgroundProcessCleanup(bashId);
    });
  };

  if (!run_in_background) {
    const settledOutcome = settled.then((result) => ({
      type: "settled" as const,
      result,
    }));
    // A yielded command reports back only through a task notification. One-shot
    // headless runs, including every subagent, have no queue consumer, and they
    // are already background work that nobody waits on interactively, so there
    // the command simply blocks until it exits or times out.
    const outcome = isQueueBridgeConnected()
      ? await Promise.race([
          settledOutcome,
          new Promise<{ type: "yield" }>((resolve) => {
            const timer = setTimeout(
              () => resolve({ type: "yield" }),
              Math.max(0, foregroundYieldMs),
            );
            if (
              typeof timer === "object" &&
              timer !== null &&
              "unref" in timer
            ) {
              timer.unref();
            }
          }),
        ])
      : await settledOutcome;

    if (outcome.type === "settled") {
      backgroundProcesses.delete(bashId);
      rmSync(outputFile, { force: true });
      const { result } = outcome;
      const output = [foregroundOutput.stdout, foregroundOutput.stderr]
        .filter(Boolean)
        .join("\n");
      const failed = result.status === "failed";
      // Both outcomes save the full output. Keeping the file on failure is a
      // deliberate departure from Claude Code, which omits it: the foreground
      // output file is already deleted, so the excerpt would be all that is left
      // of a large failing build or test log.
      const { content: truncatedOutput } = truncateByChars(
        output || "(Command completed with no output)",
        failed ? LIMITS.BASH_FAILURE_OUTPUT_CHARS : LIMITS.BASH_OUTPUT_CHARS,
        "Bash",
        {
          workingDirectory: userCwd,
          secrets: redactions,
          // Failures keep a head-and-tail excerpt; successes a short prefix.
          ...(failed
            ? { useMiddleTruncation: true }
            : { previewChars: LIMITS.OVERFLOW_PREVIEW_CHARS }),
        },
      );
      if (failed) {
        const isAbort =
          signal?.aborted ||
          ("error" in result &&
            (result.error.name === "AbortError" ||
              (result.error as NodeJS.ErrnoException).code === "ABORT_ERR"));
        return {
          content: [
            {
              type: "text",
              text: isAbort
                ? INTERRUPTED_BY_USER
                : `${recoveryNote}${result.detail}\n${truncatedOutput}`,
            },
          ],
          status: "error",
        };
      }
      return {
        content: [{ type: "text", text: `${recoveryNote}${truncatedOutput}` }],
        status: "success",
      };
    }

    backgroundProcesses.set(bashId, bgProcess);
    notifyWhenSettled();
    return {
      content: [
        {
          type: "text",
          text: `${recoveryNote}Command is still running with task ID: ${bashId}\nOutput file: ${outputFile}\nYou will be notified when it completes. Do not poll unless you need intermediate output.`,
        },
      ],
      status: "success",
    };
  }

  notifyWhenSettled();
  return {
    content: [
      {
        type: "text",
        text: `${recoveryNote}Command running in background with ID: ${bashId}\nOutput file: ${outputFile}`,
      },
    ],
    status: "success",
  };
}

```

## src/tools/impl/code-mode.ts

Source: https://github.com/letta-ai/letta-code/blob/353365bce618b7a7cbf620d0ca66f221664d8d86/src/tools/impl/code-mode.ts

SHA-256: `ff5444a6fd508be0a19f905091aca38173bfc841956c9da30c0a1657c52fd8fa`

```
import { rmSync } from "node:fs";
import { INTERRUPTED_BY_USER } from "@/constants";
import {
  createScriptToolScope,
  resolveScriptParentAgentId,
} from "@/tools/script-tool-scope";
import { formatWorkflowDuration } from "@/tools/workflow/format-stats";
import { executeToolScript } from "@/tools/workflow/workflow-engine";
import {
  addToMessageQueue,
  isQueueBridgeConnected,
} from "@/utils/message-queue-bridge";
import {
  formatTaskNotification,
  resolveNotificationScope,
} from "@/utils/task-notifications";
import {
  appendToOutputFile,
  assertBackgroundProcessCapacity,
  type BackgroundProcess,
  backgroundProcesses,
  createBackgroundOutputFile,
  getNextCodeModeId,
  notifyBackgroundProcessStateChanged,
  scheduleBackgroundProcessCleanup,
} from "./process_manager.js";

interface CodeModeArgs {
  code: string;
  args?: unknown;
  run_in_background?: boolean;
  signal?: AbortSignal;
  parentScope?: { agentId: string; conversationId: string };
  /** Test-only override; not exposed in the CodeMode tool schema. */
  foregroundYieldMs?: number;
}

// Match Bash and exec_command's foreground yield.
const DEFAULT_FOREGROUND_YIELD_MS = 10_000;
const MAX_NOTIFICATION_RESULT_CHARS = 30_000;

type Outcome = { ok: true; text: string } | { ok: false; error: string };

function truncateResult(text: string): string {
  if (text.length <= MAX_NOTIFICATION_RESULT_CHARS) return text;
  const notice =
    "\n\n[CodeMode result truncated. Read the task output file for the full value.]";
  return `${text.slice(0, MAX_NOTIFICATION_RESULT_CHARS - notice.length)}${notice}`;
}

/**
 * Tool composition without Workflow metadata, model calls, or SDK setup.
 * Backgrounding mirrors Bash: wait briefly, then yield a task whose
 * completion arrives as a task notification.
 */
export async function code_mode(args: CodeModeArgs) {
  const {
    run_in_background = false,
    signal,
    foregroundYieldMs = DEFAULT_FOREGROUND_YIELD_MS,
  } = args;
  if (typeof args.code !== "string" || !args.code.trim()) {
    return {
      content: "Provide a non-empty JavaScript function body in code.",
      status: "error",
    };
  }
  // The turn's signal stops the script only while the call is in the
  // foreground; once yielded, TaskStop aborts it.
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(signal?.reason);
  if (!run_in_background) {
    if (signal?.aborted) forwardAbort();
    else signal?.addEventListener("abort", forwardAbort, { once: true });
  }
  let scope: Awaited<ReturnType<typeof createScriptToolScope>>;
  try {
    assertBackgroundProcessCapacity();
    scope = await createScriptToolScope(
      await resolveScriptParentAgentId(args),
      controller.signal,
    );
  } catch (error) {
    signal?.removeEventListener("abort", forwardAbort);
    return {
      content: error instanceof Error ? error.message : String(error),
      status: "error",
    };
  }

  const taskId = getNextCodeModeId();
  const outputFile = createBackgroundOutputFile(taskId);
  const firstLine = args.code.trim().split("\n")[0] ?? "";
  const processState: BackgroundProcess = {
    process: {
      kill() {
        controller.abort(new Error("CodeMode stopped via TaskStop"));
      },
    },
    command: `CodeMode: ${firstLine.slice(0, 80)}`,
    status: "running",
    exitCode: null,
    startTime: new Date(),
    outputFile,
    runtimeScope: args.parentScope,
  };
  backgroundProcesses.set(taskId, processState);
  // Resolve now: by completion the launching turn may be gone.
  const notificationScope = resolveNotificationScope(args.parentScope);

  const output: string[] = [];
  const settled: Promise<Outcome> = executeToolScript({
    ...scope,
    script: args.code,
    args: args.args,
    signal: controller.signal,
    onProgress: (event) => {
      if (event.kind !== "log") return;
      output.push(event.message);
      appendToOutputFile(outputFile, `${event.message}\n`);
    },
  })
    .then((result): Outcome => {
      try {
        return { ok: true, text: JSON.stringify({ result, output }) };
      } catch (error) {
        return {
          ok: false,
          error: `Script result is not JSON-serializable: ${String(error)}`,
        };
      }
    })
    .catch(
      (error: unknown): Outcome => ({
        ok: false,
        error: controller.signal.aborted
          ? "CodeMode script stopped."
          : error instanceof Error
            ? error.message
            : String(error),
      }),
    )
    .then((outcome) => {
      processState.status = outcome.ok ? "completed" : "failed";
      processState.exitCode = outcome.ok ? 0 : 1;
      appendToOutputFile(
        outputFile,
        outcome.ok
          ? `\n[result]\n${outcome.text}\n`
          : `\n[error] ${outcome.error}\n`,
      );
      return outcome;
    })
    .finally(() => scope.cleanup());

  const notifyWhenSettled = () => {
    notifyBackgroundProcessStateChanged(notificationScope);
    void settled.then((outcome) => {
      notifyBackgroundProcessStateChanged(notificationScope);
      scheduleBackgroundProcessCleanup(taskId);
      if (processState.completionNotificationSuppressed) return;
      const status = outcome.ok ? "completed" : "failed";
      const durationMs = Date.now() - (processState.startTime?.getTime() ?? 0);
      addToMessageQueue({
        kind: "task_notification",
        text: formatTaskNotification({
          taskId,
          status,
          summary: `CodeMode script ${status} after ${formatWorkflowDuration(durationMs)}`,
          result: truncateResult(
            outcome.ok ? outcome.text : `CodeMode failed: ${outcome.error}`,
          ),
          outputFile,
          usage: { durationMs },
        }),
        ...notificationScope,
      });
    });
  };

  if (!run_in_background) {
    // One-shot headless runs have no queue consumer to receive a
    // notification, so there the script blocks until it settles.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const first = isQueueBridgeConnected()
      ? await Promise.race([
          settled,
          new Promise<"yield">((resolve) => {
            timer = setTimeout(() => resolve("yield"), foregroundYieldMs);
          }),
        ])
      : await settled;
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
    if (first !== "yield") {
      backgroundProcesses.delete(taskId);
      rmSync(outputFile, { force: true });
      if (first.ok) return { content: first.text, status: "success" };
      return {
        content: signal?.aborted ? INTERRUPTED_BY_USER : first.error,
        status: "error",
      };
    }
    notifyWhenSettled();
    return {
      content: `CodeMode script is still running with task ID: ${taskId}\nOutput file: ${outputFile}\nYou will be notified when it completes. Do not poll unless you need intermediate output.`,
      status: "success",
    };
  }

  notifyWhenSettled();
  return {
    content: `CodeMode script running in background with ID: ${taskId}\nOutput file: ${outputFile}`,
    status: "success",
  };
}

```

## src/tools/impl/truncation.ts

Source: https://github.com/letta-ai/letta-code/blob/353365bce618b7a7cbf620d0ca66f221664d8d86/src/tools/impl/truncation.ts

SHA-256: `144e2567dc6494085daaeda8f2548b8989e26124c21a275a5cf5ffd36ba239f4`

```
/**
 * Centralized truncation utilities for tool outputs.
 * Implements limits similar to Claude Code to prevent excessive token usage.
 * When outputs exceed limits, full content can be written to overflow files.
 */

import { debugLog } from "@/utils/debug.js";
import { OVERFLOW_CONFIG, writeOverflowFile } from "./overflow.js";

// Limits based on Claude Code's proven production values
export const LIMITS = {
  // Command output limits
  BASH_OUTPUT_CHARS: 30_000, // 30K characters for bash/shell output
  BASH_FAILURE_OUTPUT_CHARS: 10_000, // Head-and-tail excerpt for failures
  TASK_OUTPUT_CHARS: 30_000, // 30K characters for subagent task output
  HOOK_OUTPUT_CHARS: 10_000, // Max characters per model-facing hook string
  OVERFLOW_PREVIEW_CHARS: 2_000, // Prefix shown when full output is saved
  // Background shell completion notifications are injected into the agent's
  // context unprompted, so they get a tighter budget than a tool return the
  // agent actually asked for. The full transcript stays in the output file.
  BASH_NOTIFICATION_CHARS: 10_000,

  // File reading limits
  READ_MAX_LINES: 2_000, // Max lines per file read
  READ_MAX_CHARS_PER_LINE: 2_000, // Max characters per line
  READ_OUTPUT_CHARS: 30_000, // 30K total characters for file read output

  // Search/discovery limits
  GREP_OUTPUT_CHARS: 10_000, // Max characters for grep results
  GLOB_MAX_FILES: 2_000, // Max number of file paths
  LS_MAX_ENTRIES: 1_000, // Max directory entries
  MEMORY_MAX_ENTRIES: 10, // Files + subdirectories per Memory() listing

  // Backstop for any model-facing tool return that doesn't apply its own
  // clamp (MCP/external tools, mod tools, item-count-limited tools, etc.).
  // Slightly above BASH_OUTPUT_CHARS so outputs already clamped by a tool
  // (30K + truncation notice) pass through unchanged.
  TOOL_RETURN_MAX_CHARS: 32_000,
} as const;

/**
 * Options for truncation with overflow support
 */
export interface TruncationOptions {
  /** Working directory for overflow file creation */
  workingDirectory?: string;
  /** Whether to use middle truncation (keep beginning and end) */
  useMiddleTruncation?: boolean;
  /** Prefix to show instead of the maxChars excerpt when the full output was saved to a file */
  previewChars?: number;
  /** Secret values available to this tool invocation */
  secrets?: Readonly<Record<string, string>>;
}

/**
 * Writes the full output to an overflow file when overflow is enabled and a
 * working directory was provided. Returns undefined when nothing was written.
 */
function writeOverflow(
  getContent: () => string,
  toolName: string,
  options?: TruncationOptions,
): string | undefined {
  if (!OVERFLOW_CONFIG.ENABLED || !options?.workingDirectory) {
    return undefined;
  }
  try {
    return writeOverflowFile(
      getContent(),
      options.workingDirectory,
      toolName,
      options.secrets,
    );
  } catch (error) {
    // Silently fail if overflow file creation fails
    debugLog("truncation", "Failed to write overflow file: %O", error);
    return undefined;
  }
}

/**
 * Truncates text to a maximum character count.
 * Adds a truncation notice when content exceeds limit.
 * Optionally writes full output to an overflow file.
 */
export function truncateByChars(
  text: string,
  maxChars: number,
  toolName: string = "output",
  options?: TruncationOptions,
): { content: string; wasTruncated: boolean; overflowPath?: string } {
  if (text.length <= maxChars) {
    return { content: text, wasTruncated: false };
  }

  const overflowPath = writeOverflow(() => text, toolName, options);

  // A prefix preview only applies when the full output was saved; otherwise
  // fall back to the normal maxChars excerpt so nothing becomes unreachable.
  const filePreviewChars = overflowPath ? options?.previewChars : undefined;
  const shownChars = Math.min(maxChars, filePreviewChars ?? maxChars);
  const useMiddleTruncation =
    filePreviewChars === undefined &&
    (options?.useMiddleTruncation ?? OVERFLOW_CONFIG.MIDDLE_TRUNCATE);

  let truncated: string;
  if (useMiddleTruncation) {
    // Middle truncation: keep beginning and end
    const halfMax = Math.floor(shownChars / 2);
    const beginning = text.slice(0, halfMax);
    const end = text.slice(-halfMax);
    const omittedChars = text.length - shownChars;
    const middleNotice = `\n... [${omittedChars.toLocaleString()} characters omitted] ...\n`;
    truncated = beginning + middleNotice + end;
  } else {
    // Post truncation: keep beginning only
    truncated = text.slice(0, shownChars);
  }

  const noticeLines = [
    `[Output truncated: showing ${shownChars.toLocaleString()} of ${text.length.toLocaleString()} characters.]`,
  ];

  if (overflowPath) {
    noticeLines.push(`[Full output written to: ${overflowPath}]`);
  }

  const notice = `\n\n${noticeLines.join("\n")}`;

  return {
    content: truncated + notice,
    wasTruncated: true,
    overflowPath,
  };
}

/**
 * Truncates an array of items (file paths, directory entries, etc.)
 * Optionally writes full output to an overflow file.
 */
export function truncateArray<T>(
  items: T[],
  maxItems: number,
  formatter: (items: T[]) => string,
  itemType: string = "items",
  toolName: string = "output",
  options?: TruncationOptions,
): { content: string; wasTruncated: boolean; overflowPath?: string } {
  if (items.length <= maxItems) {
    return { content: formatter(items), wasTruncated: false };
  }

  // Determine if we should use middle truncation
  const useMiddleTruncation =
    options?.useMiddleTruncation ?? OVERFLOW_CONFIG.MIDDLE_TRUNCATE;

  let selectedItems: T[];
  if (useMiddleTruncation) {
    // Middle truncation: keep beginning and end
    const halfMax = Math.floor(maxItems / 2);
    const beginning = items.slice(0, halfMax);
    const end = items.slice(-halfMax);
    // Note: We can't insert a marker in the middle of a typed array,
    // so we'll just show beginning and end
    selectedItems = [...beginning, ...end];
  } else {
    // Post truncation: keep beginning only
    selectedItems = items.slice(0, maxItems);
  }

  const overflowPath = writeOverflow(() => formatter(items), toolName, options);

  const content = formatter(selectedItems);
  const noticeLines = [
    `[Output truncated: showing ${maxItems.toLocaleString()} of ${items.length.toLocaleString()} ${itemType}.]`,
  ];

  if (useMiddleTruncation) {
    const omitted = items.length - maxItems;
    noticeLines.push(
      `[${omitted.toLocaleString()} ${itemType} omitted from middle.]`,
    );
  }

  if (overflowPath) {
    noticeLines.push(`[Full output written to: ${overflowPath}]`);
  }

  const notice = `\n\n${noticeLines.join("\n")}`;

  return {
    content: content + notice,
    wasTruncated: true,
    overflowPath,
  };
}

```

## src/tools/toolset-catalog.ts

Source: https://github.com/letta-ai/letta-code/blob/353365bce618b7a7cbf620d0ca66f221664d8d86/src/tools/toolset-catalog.ts

SHA-256: `70d90deb49132f4ccb29bfbba4d0fa113b8c58537df5063583707ffde7c6ba5a`

```
import type { ToolName } from "./tool-definitions";
import type {
  ToolsetName,
  ToolsetOption,
  ToolsetPreference,
} from "./toolset-types";

interface ToolsetDefinition extends Omit<ToolsetOption, "id"> {
  tools: readonly ToolName[];
}

export const WORKTREE_TOOL_NAMES = new Set<ToolName>([
  "EnterWorktree",
  "ExitWorktree",
]);

/** Presets in picker order. Availability filters and explicit allowlists apply afterward. */
export const TOOLSET_CATALOG: Readonly<Record<ToolsetName, ToolsetDefinition>> =
  {
    letta: {
      display_name: "Letta",
      label: "Letta toolset",
      description: "Experimental unified toolset for every model",
      is_featured: true,
      tools: [
        "EnterWorktree",
        "ExitWorktree",
        "SetWorkingDirectory",
        "Task",
        "SendAgentMessage",
        "Monitor",
        "WatchPR",
        "Wake",
        "TaskStop",
        "Skill",
        "Memory",
        "CodeMode",
        "Workflow",
        "exec_command",
        "write_stdin",
        "Read",
        "Edit",
        "Write",
        "ViewImage",
        "UpdatePlan",
      ],
    },
    none: {
      display_name: "None",
      label: "None",
      description: "Empty built-in preset; keeps connected tools",
      is_featured: true,
      tools: [],
    },
    default: {
      display_name: "Claude",
      label: "Claude toolset",
      description:
        "Optimized for Anthropic models, recommended for all non-OpenAI models",
      is_featured: true,
      tools: [
        "Bash",
        "Monitor",
        "WatchPR",
        "Wake",
        "EnterWorktree",
        "ExitWorktree",
        "SetWorkingDirectory",
        "Edit",
        "TaskStop",
        "Read",
        "Skill",
        "Memory",
        "CodeMode",
        "Workflow",
        "Task",
        "SendAgentMessage",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskUpdate",
        "Write",
      ],
    },
    codex: {
      display_name: "Codex",
      label: "Codex toolset",
      description: "Optimized for GPT/Codex models",
      is_featured: true,
      tools: [
        "EnterWorktree",
        "ExitWorktree",
        "SetWorkingDirectory",
        "Task",
        "SendAgentMessage",
        "Monitor",
        "WatchPR",
        "Wake",
        "TaskStop",
        "Skill",
        "Memory",
        "CodeMode",
        "Workflow",
        "exec_command",
        "write_stdin",
        "ViewImage",
        "ApplyPatch",
        "UpdatePlan",
      ],
    },
  };

/** Auto selects a preset; client-facing options omit the internal tool lists. */
export const TOOLSET_OPTIONS: readonly ToolsetOption[] = [
  {
    id: "auto",
    display_name: "Auto",
    label: "Auto",
    description: "Auto-select based on the model",
    is_featured: true,
  },
  ...(Object.keys(TOOLSET_CATALOG) as ToolsetName[]).map((id) => {
    const { tools: _tools, ...option } = TOOLSET_CATALOG[id];
    return { id, ...option };
  }),
];

export function isToolsetPreference(
  value: unknown,
): value is ToolsetPreference {
  return TOOLSET_OPTIONS.some((option) => option.id === value);
}

```

## src/tools/workflow/workflow-engine.ts

Source: https://github.com/letta-ai/letta-code/blob/353365bce618b7a7cbf620d0ca66f221664d8d86/src/tools/workflow/workflow-engine.ts

SHA-256: `513fefad80491c1940737ee34d9dc9f7ad0c47cffd65094b989ec42f0dc043c8`

```
/**
 * The workflow engine: parses the meta block, builds the script-facing hooks
 * (agent / decide / tools / parallel / pipeline / phase / log / args), executes the script
 * body inside a node:vm context, and appends every subagent outcome to the
 * run's journal.
 *
 * The engine is deliberately small: it schedules calls to the injected
 * spawner and nothing else. Everything a subagent does happens in the
 * spawner (sdk-spawner.ts in production).
 *
 * The vm context gives the script a clean global scope with only the hooks
 * in it. It is not a security boundary: the hooks are host-realm functions,
 * so a script can reach the host through them (node:vm documents this). The
 * script runs with the CLI's own privileges, like the Bash tool; the
 * approval prompt shows its source so the user approves what actually runs.
 */

import { format } from "node:util";
import vm from "node:vm";
import { submitWorkflowDecision } from "./decide.ts";
import { appendJournalEntry } from "./journal.ts";
import { parseWorkflowMeta, stripMetaExport } from "./meta.ts";
import type {
  AgentCallOptions,
  RunWorkflowOptions,
  SubagentSpawner,
  WorkflowExecutionResult,
  WorkflowMeta,
  WorkflowProgressEvent,
  WorkflowToolInfo,
  WorkflowToolResult,
} from "./types.ts";

export const DEFAULT_MAX_CONCURRENT = 16;
const DEFAULT_MAX_TOTAL_AGENTS = 1000;
const DEFAULT_MAX_TOTAL_DECISIONS = 1000;
const DEFAULT_MAX_TOTAL_TOOL_CALLS = 1000;
const MAX_ITEMS_PER_HELPER = 4096;

class Semaphore {
  private available: number;
  private waiters: Array<() => void> = [];

  constructor(slots: number) {
    this.available = Math.max(1, slots);
  }

  async acquire(): Promise<void> {
    if (this.available > 0) {
      this.available--;
      return;
    }
    await new Promise<void>((resolve) => this.waiters.push(resolve));
  }

  release(): void {
    const next = this.waiters.shift();
    if (next) next();
    else this.available++;
  }
}

function mcpText(content: unknown[]): string[] {
  return content.flatMap((part) =>
    part &&
    typeof part === "object" &&
    (part as { type?: unknown }).type === "text" &&
    typeof (part as { text?: unknown }).text === "string"
      ? [(part as { text: string }).text]
      : [],
  );
}

/** Text results resolve to a string (parsed when JSON); others stay raw. */
function mcpContentValue(content: unknown[]): unknown {
  const text = mcpText(content);
  if (text.length !== content.length || text.length === 0) return content;
  const joined = text.join("\n");
  try {
    return JSON.parse(joined);
  } catch {
    return joined;
  }
}

function mcpErrorText(result: { content: unknown[] }): string {
  return mcpText(result.content).join("\n").slice(0, 2000) || "MCP tool error";
}

function defaultLabel(prompt: string): string {
  const oneLine = prompt.replace(/\s+/g, " ").trim();
  return oneLine.length <= 48 ? oneLine : `${oneLine.slice(0, 45)}...`;
}

export async function executeWorkflow(
  spawner: SubagentSpawner,
  options: RunWorkflowOptions,
): Promise<WorkflowExecutionResult> {
  const meta = parseWorkflowMeta(options.script);
  return executeScript(spawner, options, meta);
}

/** The same tool bindings, decide(), and call limits, without metadata or agent orchestration. */
export async function executeToolScript(
  options: Pick<
    RunWorkflowOptions,
    | "script"
    | "args"
    | "signal"
    | "callTool"
    | "builtinToolNames"
    | "listTools"
    | "onProgress"
    | "env"
    | "maxTotalToolCalls"
    | "maxTotalDecisions"
  > & {
    /** Extra script globals, e.g. Monitor's `emit` and `sleep`. */
    globals?: Record<string, unknown>;
  },
): Promise<unknown> {
  const run = await executeScript(
    undefined,
    options,
    { name: "code-mode", description: "Combine tools with JavaScript" },
    options.globals,
  );
  return run.result;
}

async function executeScript(
  spawner: SubagentSpawner | undefined,
  options: RunWorkflowOptions,
  meta: WorkflowMeta,
  extraGlobals?: Record<string, unknown>,
): Promise<WorkflowExecutionResult> {
  const signal = options.signal ?? new AbortController().signal;
  // Progress stops once the run has settled, not at abort: an interrupted
  // subagent still reports its outcome (and tokens) while the run drains.
  let settled = false;
  const emit = (event: WorkflowProgressEvent) => {
    if (!settled) options.onProgress?.(event);
  };
  // Every agent() call, awaited by the script or not. The run does not
  // settle until all of them have, so a completion never precedes a worker.
  const inFlight = new Set<Promise<unknown>>();
  const maxTotalAgents = options.maxTotalAgents ?? DEFAULT_MAX_TOTAL_AGENTS;
  const maxTotalDecisions =
    options.maxTotalDecisions ?? DEFAULT_MAX_TOTAL_DECISIONS;
  const maxTotalToolCalls =
    options.maxTotalToolCalls ?? DEFAULT_MAX_TOTAL_TOOL_CALLS;
  const semaphore = new Semaphore(
    options.maxConcurrent ?? DEFAULT_MAX_CONCURRENT,
  );

  let currentPhase: string | null = null;
  let callCounter = 0;
  let decisionCounter = 0;
  let toolCallCounter = 0;
  let agentsSpawned = 0;
  let totalTokens = 0;

  function trackCall<T>(pending: Promise<T>): Promise<T> {
    // Scripts can forget to await a call; that must not surface as an
    // unhandled rejection. Awaiting it still observes the original error.
    void pending.catch(() => {});
    inFlight.add(pending);
    void pending.finally(() => inFlight.delete(pending)).catch(() => {});
    return pending;
  }

  function agent(prompt: unknown, callOptions?: unknown): Promise<unknown> {
    return trackCall(callAgent(prompt, callOptions));
  }

  function decide(
    state: unknown,
    questions: unknown,
    callOptions?: unknown,
  ): Promise<unknown> {
    return trackCall(callDecision(state, questions, callOptions));
  }

  // Codex-style `tools` global: `await tools.Read(args)` or
  // `await tools.mcp__server__tool(args)`. Only the invoking turn's built-ins
  // and agent MCP tools are exposed; any other name is undefined.
  const builtinToolNames = new Set(options.builtinToolNames ?? []);
  const isCallable = (name: string) =>
    builtinToolNames.has(name) || name.startsWith("mcp__");
  const tools = new Proxy(Object.freeze({}), {
    get: (_target, name) =>
      typeof name === "string" && isCallable(name)
        ? (args?: unknown) => trackCall(callTool(name, args))
        : undefined,
  });

  let toolInfo: Promise<WorkflowToolInfo[]> | undefined;
  function loadToolInfo(): Promise<WorkflowToolInfo[]> {
    if (!options.listTools) return Promise.resolve([]);
    toolInfo ??= options.listTools().catch((error: unknown) => {
      toolInfo = undefined;
      throw error;
    });
    return toolInfo;
  }

  /** `[{ name, description }]` for every tool callable through `tools`. */
  async function listTools(): Promise<
    Array<{ name: string; description: string }>
  > {
    return (await loadToolInfo()).map(({ name, description }) => ({
      name,
      description,
    }));
  }

  /** `{ name, description, inputSchema }`, or undefined for an unknown tool. */
  async function describeTool(
    name: unknown,
  ): Promise<WorkflowToolInfo | undefined> {
    if (typeof name !== "string" || !name) {
      throw new Error("describeTool() requires a tool name string.");
    }
    return (await loadToolInfo()).find((tool) => tool.name === name);
  }

  async function callTool(toolName: string, args?: unknown): Promise<unknown> {
    const call = options.callTool;
    if (!call) {
      throw new Error("tools are unavailable: no invoking tool scope.");
    }
    if (
      args !== undefined &&
      (!args || typeof args !== "object" || Array.isArray(args))
    ) {
      throw new Error(`tools.${toolName}() arguments must be an object.`);
    }
    if (signal.aborted) throw new Error("Workflow aborted.");
    if (toolCallCounter >= maxTotalToolCalls) {
      throw new Error(
        `Lifetime tool call cap of ${maxTotalToolCalls} reached.`,
      );
    }
    toolCallCounter++;
    await semaphore.acquire();
    const startedAt = Date.now();
    const journal = (isError: boolean, error?: string) => {
      if (!options.journalPath) return;
      appendJournalEntry(options.journalPath, {
        kind: "tool_call",
        toolName,
        isError,
        durationMs: Date.now() - startedAt,
        ...(error ? { error } : {}),
      });
    };
    let result: WorkflowToolResult;
    try {
      if (signal.aborted) throw new Error("Workflow aborted.");
      result = await call(toolName, (args ?? {}) as Record<string, unknown>);
    } catch (error) {
      journal(true, error instanceof Error ? error.message : String(error));
      throw error;
    } finally {
      semaphore.release();
    }
    if (result.isError) {
      const message = mcpErrorText(result);
      journal(true, message);
      throw new Error(`${toolName} failed: ${message}`);
    }
    journal(false);
    // Built-ins resolve to their text output verbatim; MCP results normalize.
    if (builtinToolNames.has(toolName))
      return mcpText(result.content).join("\n");
    return result.structuredContent ?? mcpContentValue(result.content);
  }

  async function callDecision(
    state: unknown,
    questions: unknown,
    callOptions?: unknown,
  ): Promise<unknown> {
    if (signal.aborted) throw new Error("Workflow aborted.");
    if (decisionCounter >= maxTotalDecisions) {
      throw new Error(`Lifetime decision cap of ${maxTotalDecisions} reached.`);
    }
    if (
      callOptions !== undefined &&
      (!callOptions ||
        typeof callOptions !== "object" ||
        Array.isArray(callOptions))
    ) {
      throw new Error("decide() options must be an object.");
    }
    decisionCounter++;
    await semaphore.acquire();
    try {
      if (signal.aborted) throw new Error("Workflow aborted.");
      return await submitWorkflowDecision(
        {
          ...(callOptions as Record<string, unknown> | undefined),
          state,
          questions,
        },
        signal,
        (result) => {
          totalTokens += result.totalTokens;
          emit({ kind: "decision_usage", totalTokens: result.totalTokens });
          if (options.journalPath) {
            appendJournalEntry(options.journalPath, {
              kind: "decision",
              ...result,
            });
          }
        },
      );
    } finally {
      semaphore.release();
    }
  }

  async function callAgent(
    prompt: unknown,
    callOptions?: unknown,
  ): Promise<unknown> {
    if (typeof prompt !== "string" || !prompt.trim()) {
      throw new Error("agent() requires a non-empty prompt string.");
    }
    if (signal.aborted) throw new Error("Workflow aborted.");
    if (callCounter >= maxTotalAgents) {
      throw new Error(`Lifetime agent cap of ${maxTotalAgents} reached.`);
    }
    if (
      callOptions !== undefined &&
      (!callOptions ||
        typeof callOptions !== "object" ||
        Array.isArray(callOptions))
    ) {
      throw new Error("agent() options must be an object.");
    }
    const opts: AgentCallOptions = { ...(callOptions as AgentCallOptions) };
    if (
      opts.conversationId !== undefined &&
      (typeof opts.conversationId !== "string" ||
        !/^(?:conv-|local-conv-)[A-Za-z0-9_-]+$/.test(opts.conversationId))
    ) {
      throw new Error(
        "agent() conversationId must be a conv-... or local-conv-... ID.",
      );
    }
    if (
      opts.maxToolCalls !== undefined &&
      (!Number.isSafeInteger(opts.maxToolCalls) || opts.maxToolCalls <= 0)
    ) {
      throw new Error("agent() maxToolCalls must be a positive safe integer.");
    }
    if (
      opts.skills !== undefined &&
      (!Array.isArray(opts.skills) ||
        !opts.skills.every((id) => typeof id === "string" && id.trim()))
    ) {
      throw new Error("agent() skills must be an array of skill names.");
    }
    const callIndex = callCounter++;
    const label = opts.label ?? defaultLabel(prompt);
    const phase = opts.phase ?? currentPhase;

    emit({ kind: "agent", callIndex, label, phase, status: "queued" });
    await semaphore.acquire();
    try {
      if (signal.aborted) throw new Error("Workflow aborted.");
      emit({ kind: "agent", callIndex, label, phase, status: "running" });
      agentsSpawned++;
      if (!spawner)
        throw new Error("Subagents are unavailable in tool scripts.");
      const outcome = await spawner(
        { prompt, options: opts, callIndex },
        signal,
        {
          onStarted: (conversationId) => {
            if (options.journalPath) {
              appendJournalEntry(options.journalPath, {
                kind: "agent_started",
                callIndex,
                conversationId,
              });
            }
          },
          // Live usage so status rows can show tokens before the agent ends.
          onUsage: (totalTokens) =>
            emit({
              kind: "agent",
              callIndex,
              label,
              phase,
              status: "running",
              totalTokens,
            }),
        },
      );
      // Account for the outcome even when the run was aborted meanwhile: the
      // spawner returns what the interrupted subagent had already consumed.
      totalTokens += outcome.totalTokens ?? 0;
      if (options.journalPath) {
        appendJournalEntry(options.journalPath, {
          callIndex,
          label,
          prompt,
          ...(opts.conversationId
            ? { resumedConversationId: opts.conversationId }
            : {}),
          outcome,
        });
      }
      emit({
        kind: "agent",
        callIndex,
        label,
        phase,
        status: outcome.failed ? "error" : "done",
        detail: outcome.error,
        durationMs: outcome.durationMs,
        totalTokens: outcome.totalTokens,
      });
      if (signal.aborted) throw new Error("Workflow aborted.");
      if (outcome.failed) {
        const worker = outcome.conversationId
          ? ` (${outcome.conversationId})`
          : "";
        throw Object.assign(
          new Error(
            `Workflow agent "${label}"${worker} failed: ${outcome.error ?? "unknown error"}`,
          ),
          { callIndex, conversationId: outcome.conversationId },
        );
      }
      return outcome.value;
    } finally {
      semaphore.release();
    }
  }

  async function collectResults(tasks: Promise<unknown>[]): Promise<unknown[]> {
    // Preserve sibling completion, but never turn an unhandled failure into
    // a successful null result. Scripts can catch inside each callback for
    // best-effort processing.
    const outcomes = await Promise.allSettled(tasks);
    return outcomes.map((outcome) => {
      if (outcome.status === "rejected") throw outcome.reason;
      return outcome.value;
    });
  }

  async function parallel(thunks: unknown): Promise<unknown[]> {
    if (!Array.isArray(thunks)) {
      throw new Error("parallel() takes an array of zero-arg functions.");
    }
    if (thunks.length > MAX_ITEMS_PER_HELPER) {
      throw new Error(
        `parallel() accepts at most ${MAX_ITEMS_PER_HELPER} items, got ${thunks.length}.`,
      );
    }
    return collectResults(
      thunks.map(async (thunk) => {
        if (typeof thunk !== "function") {
          throw new Error("parallel() takes an array of zero-arg functions.");
        }
        return await thunk();
      }),
    );
  }

  async function pipeline(
    items: unknown,
    ...stages: unknown[]
  ): Promise<unknown[]> {
    if (!Array.isArray(items)) {
      throw new Error(
        "pipeline() takes an array of items followed by stage functions.",
      );
    }
    if (items.length > MAX_ITEMS_PER_HELPER) {
      throw new Error(
        `pipeline() accepts at most ${MAX_ITEMS_PER_HELPER} items, got ${items.length}.`,
      );
    }
    type StageFn = (prev: unknown, item: unknown, index: number) => unknown;
    const stageFns = stages.filter(
      (s): s is StageFn => typeof s === "function",
    );
    // No barrier between stages: each item flows through its whole chain
    // independently, so item A can be in stage 3 while item B is in stage 1.
    return collectResults(
      items.map(async (item, index) => {
        let value: unknown = item;
        for (const stage of stageFns) {
          value = await stage(value, item, index);
        }
        return value;
      }),
    );
  }

  function phase(title: unknown): void {
    if (typeof title !== "string" || !title) {
      throw new Error("phase() requires a title string.");
    }
    currentPhase = title;
    emit({ kind: "phase", title });
  }

  function log(...messages: unknown[]): void {
    emit({ kind: "log", message: format(...messages) });
  }

  const context = vm.createContext({
    tools,
    listTools,
    describeTool,
    decide,
    ...(spawner ? { agent, parallel, pipeline, phase, log } : {}),
    args: options.args,
    console: { log, warn: log, error: log, info: log },
    // Only `env`: the host process's exit, kill, and bindings stay out.
    process: Object.freeze({ env: Object.freeze({ ...options.env }) }),
    ...extraGlobals,
  });
  const wrapped = `(async () => { "use strict";\n${spawner ? stripMetaExport(options.script) : options.script}\n})()`;
  const parseError = (error: unknown) =>
    new Error(
      `${spawner ? "Workflow" : "CodeMode"} script failed to parse (scripts are plain JavaScript, not TypeScript): ${String(error)}`,
    );
  let script: vm.Script;
  try {
    script = new vm.Script(wrapped, { filename: `${meta.name}.workflow.js` });
  } catch (error) {
    throw parseError(error);
  }

  let pending: Promise<unknown>;
  try {
    // Node reports syntax errors at construction; Bun compiles lazily, so
    // they can surface here instead.
    pending = script.runInContext(context) as Promise<unknown>;
  } catch (error) {
    if ((error as { name?: string }).name === "SyntaxError") {
      throw parseError(error);
    }
    throw error;
  }

  const abortRun = new Promise<never>((_resolve, reject) => {
    if (signal.aborted) reject(new Error("Workflow aborted."));
    signal.addEventListener(
      "abort",
      () => reject(new Error("Workflow aborted.")),
      { once: true },
    );
  });
  void abortRun.catch(() => {});
  // Whether the script returned, threw, or was aborted, wait for every
  // launched subagent to report back before settling. Aborted ones return
  // promptly because the spawner honors the signal.
  const drain = async () => {
    while (inFlight.size > 0) await Promise.allSettled([...inFlight]);
  };
  try {
    const result = await Promise.race([pending, abortRun]);
    await drain();
    return { meta, result, agentsSpawned, totalTokens };
  } catch (error) {
    await drain();
    throw error;
  } finally {
    settled = true;
  }
}

```
