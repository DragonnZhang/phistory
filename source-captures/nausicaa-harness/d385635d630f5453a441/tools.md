# nausicaa-harness — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/runtime/l0-agent-loop.ts

Source: https://github.com/jackispm/nausicaa-harness/blob/b61edf67c46e1faf512695c3c12d0f7a4dd80eb3/src/runtime/l0-agent-loop.ts

SHA-256: `2b04114fd06fdcdd086689f2018e542fd7674a91a4dde0c9acaa74769df637c1`

```
import type {
  AgentTool,
  ModelPort,
  ModelRequest,
  ModelResponse,
  ModelStreamEvent,
  ToolDefinition,
  ToolResult,
} from "../domain/ports.js";
import type { ConversationMessage, TokenUsage, ToolCall } from "../domain/types.js";
import { validateModelResponse, validateModelStreamEvent } from "./model-response-validation.js";

/** Default bound for a standalone kernel invocation. */
export const DEFAULT_L0_MAX_STEPS = 16;
/** Keep accidental provider/tool loops finite even when a caller omits a bound. */
export const MAX_L0_MAX_STEPS = 1_024;
/** Conservative upper bound for an optional cumulative model usage budget. */
export const MAX_L0_MODEL_TOKENS = 1_000_000_000;

export type L0ToolExecutionMode = "sequential" | "parallel";

/** Request fields are assembled by the host; messages and tools are kernel-owned. */
export type L0ModelRequest = Omit<ModelRequest, "messages" | "tools" | "signal">;

export interface L0AgentLoopInput {
  /** Materialized context at the start of this invocation. It is never persisted here. */
  readonly messages: readonly ConversationMessage[];
  readonly request: L0ModelRequest;
  /** Workspace is only passed to injected tools; it is not interpreted by L0. */
  readonly workspace?: string;
  readonly signal?: AbortSignal;
  /** Maximum number of model requests, including the initial request. */
  readonly maxSteps?: number;
  /** Optional cumulative input/output/cache token budget. */
  readonly maxModelTokens?: number;
}

export interface L0AgentLoopDeps {
  readonly model: ModelPort;
  readonly tools?: readonly AgentTool[];
  /** Default is parallel, matching Pi's independent tool-call behavior. */
  readonly toolExecution?: L0ToolExecutionMode;
  /** Best-effort observation; it cannot change kernel state or fail the loop. */
  readonly onEvent?: (event: L0AgentLoopEvent) => void;
  /** Raw provider stream events, useful for UI adapters and parity fixtures. */
  readonly onStreamEvent?: (event: ModelStreamEvent) => void;
}

export type L0AgentLoopEvent =
  | { type: "model.requested"; step: number; request: ModelRequest }
  | { type: "model.stream"; step: number; event: ModelStreamEvent }
  | { type: "assistant.message"; step: number; message: ConversationMessage }
  | { type: "tool.started"; step: number; call: ToolCall }
  | { type: "tool.completed"; step: number; call: ToolCall; message: ConversationMessage }
  | {
      type: "step.completed";
      step: number;
      response: ModelResponse;
      toolMessages: readonly ConversationMessage[];
    };

export interface L0AgentLoopResult {
  /** Only messages generated during this invocation; input context is excluded. */
  readonly messages: readonly ConversationMessage[];
  readonly finalText: string;
  readonly usage: TokenUsage;
  readonly steps: number;
  readonly completed: boolean;
  readonly aborted: boolean;
  readonly stopReason?: string;
  readonly finalMessage?: ConversationMessage;
}

/** Functional entrypoint for hosts that do not need to retain loop state. */
export async function runL0AgentLoop(
  input: L0AgentLoopInput,
  deps: L0AgentLoopDeps,
): Promise<L0AgentLoopResult> {
  return new L0AgentLoop(deps).run(input);
}

/** Raised when the provider stream violates the ModelPort completion contract. */
export class L0ProtocolError extends Error {
  override readonly name = "L0ProtocolError";
}

/**
 * Provider-neutral single-lane cognition mechanics.
 *
 * This class deliberately has no Ledger, store, filesystem, retry, permission,
 * compaction, or scheduler dependency. A host can persist the returned message
 * delta and wrap the model/tool ports with its own durable policy boundary.
 */
export class L0AgentLoop {
  private readonly model: ModelPort;
  private readonly tools: readonly AgentTool[];
  private readonly toolExecution: L0ToolExecutionMode;
  private readonly onEvent: L0AgentLoopDeps["onEvent"];
  private readonly onStreamEvent: L0AgentLoopDeps["onStreamEvent"];

  constructor(deps: L0AgentLoopDeps) {
    this.model = deps.model;
    const tools = [...(deps.tools ?? [])];
    const names = new Set<string>();
    for (const tool of tools) {
      const name = tool.definition.name.trim();
      if (name.length === 0) throw new Error("Tool names must not be empty");
      if (names.has(name)) throw new Error(`Duplicate tool: ${name}`);
      names.add(name);
    }
    this.tools = Object.freeze(tools);
    this.toolExecution = deps.toolExecution ?? "parallel";
    if (this.toolExecution !== "parallel" && this.toolExecution !== "sequential") {
      throw new Error("toolExecution must be parallel or sequential");
    }
    this.onEvent = deps.onEvent;
    this.onStreamEvent = deps.onStreamEvent;
  }

  async run(input: L0AgentLoopInput): Promise<L0AgentLoopResult> {
    validateInput(input);
    throwIfAborted(input.signal);

    const maxSteps = input.maxSteps ?? DEFAULT_L0_MAX_STEPS;
    const maxModelTokens = input.maxModelTokens;
    const context: ConversationMessage[] = input.messages.map(cloneMessage);
    const generated: ConversationMessage[] = [];
    let usage = emptyUsage();
    let finalText = "";
    let finalMessage: ConversationMessage | undefined;
    let stopReason: string | undefined;
    let steps = 0;

    for (let step = 1; step <= maxSteps; step += 1) {
      throwIfAborted(input.signal);
      if (maxModelTokens !== undefined && chargedTokens(usage) >= maxModelTokens) break;

      const request: ModelRequest = {
        ...input.request,
        messages: context.map(cloneMessage),
        tools: this.tools.map((tool) => cloneDefinition(tool.definition)),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      };
      this.emit({ type: "model.requested", step, request: cloneRequest(request) });
      const response = await this.requestModel(request, step);
      throwIfAborted(input.signal);

      steps += 1;
      usage = addUsage(usage, response.usage);
      finalText = response.content;
      stopReason = response.stopReason;

      const assistantMessage: ConversationMessage = {
        role: "assistant",
        content: response.content,
        toolCalls: structuredClone(response.toolCalls),
        createdAt: new Date().toISOString(),
      };
      context.push(assistantMessage);
      generated.push(assistantMessage);
      finalMessage = assistantMessage;
      this.emit({
        type: "assistant.message",
        step,
        message: cloneMessage(assistantMessage),
      });

      if (response.stopReason === "aborted") {
        this.emit({ type: "step.completed", step, response: structuredClone(response), toolMessages: [] });
        return result(generated, finalText, usage, steps, false, true, stopReason, finalMessage);
      }

      const toolMessages = response.toolCalls.length === 0
        ? []
        : response.stopReason === "length"
          ? this.failTruncatedToolCalls(step, response.toolCalls)
          : await this.executeTools(input, step, response.toolCalls);
      context.push(...toolMessages);
      generated.push(...toolMessages);
      this.emit({
        type: "step.completed",
        step,
        response: structuredClone(response),
        toolMessages: toolMessages.map(cloneMessage),
      });

      if (response.toolCalls.length === 0) {
        return result(
          generated,
          finalText,
          usage,
          steps,
          response.stopReason === "stop",
          false,
          stopReason,
          finalMessage,
        );
      }
      if (maxModelTokens !== undefined && chargedTokens(usage) >= maxModelTokens) break;
    }

    return result(generated, finalText, usage, steps, false, false, stopReason, finalMessage);
  }

  private async requestModel(request: ModelRequest, step: number): Promise<ModelResponse> {
    throwIfAborted(request.signal);
    if (this.model.stream === undefined) {
      const response = await raceAbort(this.model.complete(request), request.signal);
      validateL0ModelResponse(response);
      return response;
    }

    const stream = this.model.stream(request);
    const iterator = stream[Symbol.asyncIterator]();
    let response: ModelResponse | undefined;
    try {
      while (true) {
        const next = await raceAbort(iterator.next(), request.signal);
        if (next.done) break;
        const event = next.value;
        validateL0ModelStreamEvent(event);
        this.emit({ type: "model.stream", step, event: cloneStreamEvent(event) });
        try {
          // Keep observers from mutating the provider event consumed below.
          this.onStreamEvent?.(cloneStreamEvent(event));
        } catch {
          // Stream rendering is observational and cannot change cognition state.
        }
        if (event.type === "done") {
          response = event.response;
          break;
        }
        if (event.type === "error") throw event.error;
      }
    } catch (error: unknown) {
      try {
        void Promise.resolve(iterator.return?.()).catch(() => undefined);
      } catch {
        // A broken iterator close cannot replace the provider failure.
      }
      throw error;
    }
    if (response === undefined) {
      throw new L0ProtocolError("Model stream ended without a final response");
    }
    validateL0ModelResponse(response);
    return response;
  }

  private async executeTools(
    input: L0AgentLoopInput,
    step: number,
    calls: readonly ToolCall[],
  ): Promise<ConversationMessage[]> {
    if (this.toolExecution === "sequential") {
      const messages: ConversationMessage[] = [];
      for (const call of calls) {
        messages.push(await this.executeTool(input, step, call));
      }
      throwIfAborted(input.signal);
      return messages;
    }

    // Promise.all preserves source order in its returned array while each
    // tool starts immediately, so completion observation and model context
    // ordering remain separate contracts.
    const messages = await Promise.all(calls.map((call) => this.executeTool(input, step, call)));
    throwIfAborted(input.signal);
    return messages;
  }

  private failTruncatedToolCalls(
    step: number,
    calls: readonly ToolCall[],
  ): ConversationMessage[] {
    return calls.map((call) => {
      const message = toolMessage(
        call,
        `Tool call "${call.name}" was not executed: the response hit the output token limit, so its arguments may be truncated. Re-issue the tool call with complete arguments.`,
        true,
      );
      this.emit({
        type: "tool.completed",
        step,
        call: structuredClone(call),
        message: cloneMessage(message),
      });
      return message;
    });
  }

  private async executeTool(
    input: L0AgentLoopInput,
    step: number,
    call: ToolCall,
  ): Promise<ConversationMessage> {
    let message: ConversationMessage;
    const tool = this.tools.find((candidate) => candidate.definition.name === call.name);
    if (input.signal?.aborted === true) {
      message = toolMessage(call, "Operation aborted", true);
    } else if (tool === undefined) {
      message = toolMessage(call, `Tool ${call.name} not found`, true);
    } else {
      const validationError = validateArguments(tool.definition, call.arguments);
      if (validationError !== undefined) {
        message = toolMessage(call, validationError, true);
      } else if (call.name.length === 0) {
        message = toolMessage(call, "Tool call name must not be empty", true);
      } else {
        const operationId = `l0:${input.request.runId}:${input.request.laneId}:step:${step}:tool:${call.id}`;
        this.emit({ type: "tool.started", step, call: structuredClone(call) });
        try {
          const result = await tool.execute(call.arguments, {
            runId: input.request.runId,
            laneId: input.request.laneId,
            workspace: input.workspace ?? "",
            operationId,
            ...(input.signal === undefined ? {} : { signal: input.signal }),
          });
          message = toolMessage(call, result.content, result.isError, result.images);
        } catch (error: unknown) {
          message = toolMessage(call, errorMessage(error), true);
        }
      }
    }
    this.emit({
      type: "tool.completed",
      step,
      call: structuredClone(call),
      message: cloneMessage(message),
    });
    return message;
  }

  private emit(event: L0AgentLoopEvent): void {
    try {
      this.onEvent?.(event);
    } catch {
      // Event observers are intentionally outside the kernel's state machine.
    }
  }
}

function result(
  messages: readonly ConversationMessage[],
  finalText: string,
  usage: TokenUsage,
  steps: number,
  completed: boolean,
  aborted: boolean,
  stopReason: string | undefined,
  finalMessage: ConversationMessage | undefined,
): L0AgentLoopResult {
  return {
    messages: Object.freeze(messages.map(cloneMessage)),
    finalText,
    usage,
    steps,
    completed,
    aborted,
    ...(stopReason === undefined ? {} : { stopReason }),
    ...(finalMessage === undefined ? {} : { finalMessage: cloneMessage(finalMessage) }),
  };
}

function toolMessage(
  call: ToolCall,
  content: string,
  isError: boolean,
  images?: ToolResult["images"],
): ConversationMessage {
  return {
    role: "tool",
    content,
    toolCallId: call.id,
    toolName: call.name,
    isError,
    ...(images === undefined ? {} : { images: structuredClone(images) }),
    createdAt: new Date().toISOString(),
  };
}

function validateInput(input: L0AgentLoopInput): void {
  if (input.request.runId.trim().length === 0) throw new Error("runId is required");
  if (input.request.laneId.trim().length === 0) throw new Error("laneId is required");
  if (input.request.sessionId.trim().length === 0) throw new Error("sessionId is required");
  if (input.request.model.trim().length === 0) throw new Error("model is required");
  if (!Number.isSafeInteger(input.request.maxOutputTokens) || input.request.maxOutputTokens < 1) {
    throw new Error("maxOutputTokens must be a positive integer");
  }
  const maxSteps = input.maxSteps ?? DEFAULT_L0_MAX_STEPS;
  if (!Number.isSafeInteger(maxSteps) || maxSteps < 1 || maxSteps > MAX_L0_MAX_STEPS) {
    throw new Error(`maxSteps must be an integer between 1 and ${MAX_L0_MAX_STEPS}`);
  }
  if (input.maxModelTokens !== undefined
    && (!Number.isSafeInteger(input.maxModelTokens)
      || input.maxModelTokens < 1
      || input.maxModelTokens > MAX_L0_MODEL_TOKENS)) {
    throw new Error(`maxModelTokens must be an integer between 1 and ${MAX_L0_MODEL_TOKENS}`);
  }
}

function validateL0ModelResponse(value: unknown): asserts value is ModelResponse {
  try {
    validateModelResponse(value);
  } catch (error: unknown) {
    throw new L0ProtocolError(errorMessage(error));
  }
}

function validateL0ModelStreamEvent(value: unknown): asserts value is ModelStreamEvent {
  try {
    validateModelStreamEvent(value);
  } catch (error: unknown) {
    throw new L0ProtocolError(errorMessage(error));
  }
}

function validateArguments(definition: ToolDefinition, value: Record<string, unknown>): string | undefined {
  const schema = definition.parameters as unknown as SchemaNode;
  if (schema === null || typeof schema !== "object" || Array.isArray(schema)) {
    return "Invalid tool schema";
  }
  if (schema.type !== undefined && schema.type !== "object") {
    return `Tool arguments must be object for ${definition.name}`;
  }
  const properties = schema.properties;
  if (properties !== undefined && (!isRecord(properties) || Array.isArray(properties))) {
    return "Invalid tool schema properties";
  }
  const required = schema.required;
  if (required !== undefined && (!Array.isArray(required) || required.some((key) => typeof key !== "string"))) {
    return "Invalid tool schema required list";
  }
  for (const key of (required as string[] | undefined) ?? []) {
    if (!Object.hasOwn(value, key)) return `arguments.${key} is required`;
  }
  if (schema.additionalProperties === false && isRecord(properties)) {
    for (const key of Object.keys(value)) {
      if (!Object.hasOwn(properties, key)) return `arguments.${key} is not allowed`;
    }
  }
  if (isRecord(properties)) {
    for (const [key, propertySchema] of Object.entries(properties)) {
      if (!Object.hasOwn(value, key)) continue;
      const error = validateValue(value[key], propertySchema, `arguments.${key}`);
      if (error !== undefined) return error;
    }
  }
  return undefined;
}

function validateValue(value: unknown, schema: unknown, path: string): string | undefined {
  if (schema === true || schema === undefined) return undefined;
  if (schema === false) return `${path} is rejected by the declared schema`;
  if (!isRecord(schema)) return "Invalid tool schema";
  if (schema.const !== undefined && !sameJsonValue(value, schema.const)) {
    return `${path} must equal the declared constant`;
  }
  if (Array.isArray(schema.enum) && !schema.enum.some((candidate) => sameJsonValue(value, candidate))) {
    return `${path} must be one of the declared values`;
  }
  const type = schema.type;
  if (type === undefined) return undefined;
  const valid = type === "string" ? typeof value === "string"
    : type === "integer" ? typeof value === "number" && Number.isSafeInteger(value)
      : type === "number" ? typeof value === "number" && Number.isFinite(value)
        : type === "boolean" ? typeof value === "boolean"
          : type === "null" ? value === null
            : type === "array" ? Array.isArray(value)
              : type === "object" ? isRecord(value) : false;
  if (!valid) return `${path} must be ${type}`;
  if (type === "array" && schema.items !== undefined && Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      const error = validateValue(value[index], schema.items, `${path}[${index}]`);
      if (error !== undefined) return error;
    }
  }
  if (type === "object" && isRecord(value)) {
    const nested: ToolDefinition = {
      name: path,
      description: "",
      parameters: schema as unknown as ToolDefinition["parameters"],
    };
    return validateArguments(nested, value);
  }
  return undefined;
}

interface SchemaNode {
  type?: unknown;
  properties?: unknown;
  required?: unknown;
  additionalProperties?: unknown;
  items?: unknown;
  enum?: unknown;
  const?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sameJsonValue(left: unknown, right: unknown): boolean {
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function cloneMessage(message: ConversationMessage): ConversationMessage {
  return structuredClone(message);
}

function cloneDefinition(definition: ToolDefinition): ToolDefinition {
  return structuredClone(definition);
}

function cloneRequest(request: ModelRequest): ModelRequest {
  return {
    ...request,
    messages: request.messages.map(cloneMessage),
    tools: request.tools.map(cloneDefinition),
  };
}

function cloneStreamEvent(event: ModelStreamEvent): ModelStreamEvent {
  return structuredClone(event);
}

function emptyUsage(): TokenUsage {
  return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 };
}

function addUsage(left: TokenUsage, right: TokenUsage): TokenUsage {
  return {
    input: safeUsageAdd(left.input, right.input, "input tokens"),
    output: safeUsageAdd(left.output, right.output, "output tokens"),
    cacheRead: safeUsageAdd(left.cacheRead, right.cacheRead, "cache-read tokens"),
    cacheWrite: safeUsageAdd(left.cacheWrite, right.cacheWrite, "cache-write tokens"),
    costUsd: safeCostAdd(left.costUsd ?? 0, right.costUsd ?? 0),
  };
}

function chargedTokens(usage: TokenUsage): number {
  return safeUsageAdd(
    safeUsageAdd(usage.input, usage.output, "model tokens"),
    safeUsageAdd(usage.cacheRead, usage.cacheWrite, "model tokens"),
    "model tokens",
  );
}

function safeUsageAdd(left: number, right: number, label: string): number {
  const result = left + right;
  if (!Number.isSafeInteger(result)) throw new L0ProtocolError(`${label} exceed the safe integer range`);
  return result;
}

function safeCostAdd(left: number, right: number): number {
  const result = left + right;
  if (!Number.isFinite(result)) throw new L0ProtocolError("model cost exceeds the finite number range");
  return result;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (!signal?.aborted) return;
  throw abortError(signal);
}

function abortError(signal: AbortSignal): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : new DOMException("The operation was aborted", "AbortError");
}

async function raceAbort<T>(pending: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (signal === undefined) return pending;
  throwIfAborted(signal);
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => reject(abortError(signal));
    signal.addEventListener("abort", onAbort, { once: true });
    pending.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

```

## src/tools/bash.ts

Source: https://github.com/jackispm/nausicaa-harness/blob/b61edf67c46e1faf512695c3c12d0f7a4dd80eb3/src/tools/bash.ts

SHA-256: `cab71ade43366f36ac467fe81c0a946d0ec6f480178e98c8f8d2869dfdf0a0a5`

```
import { stat } from "node:fs/promises";

import type { AgentTool, ToolResult } from "../domain/ports.js";
import type { ShellOutputSink } from "./shell-output.js";
import { executeShellCommand, type ShellExecutionResult } from "./shell-process.js";
import { diagnosePermissionFailure } from "./permission-diagnostics.js";

export type { ShellOutputSink } from "./shell-output.js";

const MAX_TIMEOUT_SECONDS = 2_147_483_647 / 1_000;

export interface BashCommandExecutionInput {
  command: string;
  cwd: string;
  timeoutMs?: number;
  signal?: AbortSignal;
  /** Optional host-owned observers for complete, sanitized output streams. */
  outputSink?: ShellOutputSink;
}

/** Narrow execution seam for an OS sandbox or another host-owned backend. */
export type BashCommandExecutor = (
  input: BashCommandExecutionInput,
) => Promise<ShellExecutionResult>;

export interface BashToolOptions {
  /** Defaults to the existing unrestricted host-shell executor. */
  commandExecutor?: BashCommandExecutor;
  /** Optional host-owned observers for complete, sanitized output streams. */
  outputSink?: ShellOutputSink;
}

export function createBashTool(options: BashToolOptions = {}): AgentTool {
  const commandExecutor = options.commandExecutor ?? executeShellCommand;
  return {
    definition: {
      name: "bash",
      description: [
        "Execute a Bash command with the working directory fixed to the current workspace.",
        "The command is interpreted by Bash, including variables, pipes, redirects, and command substitution.",
        "This privileged tool follows the active host permission profile; an interactive TUI can request a wider boundary when execution is denied.",
        "Stdout and stderr retain only their bounded tails.",
        "Workspace sandbox mode protects Git metadata; use the read-only git_* tools for inspection and full-access for repository writes.",
        "Background jobs are unsupported; where OS-level process containment is unavailable, cleanup is best-effort.",
      ].join(" "),
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "Bash source to execute" },
          timeout: {
            type: "number",
            exclusiveMinimum: 0,
            maximum: MAX_TIMEOUT_SECONDS,
            description: "Optional timeout in seconds; there is no default timeout",
          },
        },
        required: ["command"],
        additionalProperties: false,
      },
    },

    async execute(arguments_, context): Promise<ToolResult> {
      try {
        const command = requiredCommand(arguments_.command);
        const timeout = optionalTimeout(arguments_.timeout);
        if (context.signal?.aborted) {
          return failure({
            error: "Command aborted",
            exitCode: null,
            aborted: true,
            timedOut: false,
            ...emptyOutput(),
          });
        }
        const workspaceStat = await stat(context.workspace);
        if (!workspaceStat.isDirectory()) {
          return failure({ error: "Workspace is not a directory" });
        }

        const execution = await commandExecutor({
          command,
          cwd: context.workspace,
          ...(timeout === undefined ? {} : { timeoutMs: timeout * 1_000 }),
          ...(context.signal === undefined ? {} : { signal: context.signal }),
          ...(options.outputSink === undefined ? {} : { outputSink: options.outputSink }),
        });
        return formatResult(execution, timeout, command);
      } catch (error: unknown) {
        const diagnostic = diagnosePermissionFailure({ error });
        return failure({
          error: safeMessage(error),
          ...(diagnostic === undefined ? {} : { diagnostic }),
        });
      }
    },
  };
}

export const bashTool: AgentTool = createBashTool();

function formatResult(
  execution: ShellExecutionResult,
  timeout: number | undefined,
  command: string,
): ToolResult {
  const diagnostic = diagnosePermissionFailure({
    command,
    error: execution.spawnError,
    stderr: execution.stderr.content,
  });
  const output = {
    stdout: execution.stdout.content,
    stderr: execution.stderr.content,
    exitCode: execution.exitCode,
    aborted: execution.aborted,
    timedOut: execution.timedOut,
    ...(diagnostic === undefined ? {} : { diagnostic }),
    truncated: execution.stdout.truncated || execution.stderr.truncated,
    ...(execution.stdout.outputSinkError === undefined && execution.stderr.outputSinkError === undefined
      ? {}
      : {
          outputSinkError: execution.stdout.outputSinkError ?? execution.stderr.outputSinkError,
        }),
    truncation: {
      stdout: truncationMetadata(execution.stdout),
      stderr: truncationMetadata(execution.stderr),
    },
  };

  if (execution.spawnError !== undefined) {
    return failure({ ...output, error: execution.spawnError.message });
  }
  if (execution.aborted) {
    return failure({ ...output, error: "Command aborted" });
  }
  if (execution.timedOut) {
    return failure({ ...output, error: `Command timed out after ${timeout} seconds` });
  }
  if (execution.exitCode !== 0) {
    return failure({ ...output, error: `Command exited with code ${execution.exitCode}` });
  }
  return success(output);
}

function truncationMetadata(snapshot: ShellExecutionResult["stdout"]): {
  truncated: boolean;
  truncatedBy: "bytes" | "lines" | null;
  totalBytes: number;
  totalLines: number;
  outputBytes: number;
  outputLines: number;
} {
  return {
    truncated: snapshot.truncated,
    truncatedBy: snapshot.truncatedBy,
    totalBytes: snapshot.totalBytes,
    totalLines: snapshot.totalLines,
    outputBytes: snapshot.outputBytes,
    outputLines: snapshot.outputLines,
  };
}

function emptyOutput(): object {
  const stream = {
    truncated: false,
    truncatedBy: null,
    totalBytes: 0,
    totalLines: 0,
    outputBytes: 0,
    outputLines: 0,
  };
  return {
    stdout: "",
    stderr: "",
    truncated: false,
    truncation: { stdout: stream, stderr: stream },
  };
}

function requiredCommand(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError("command must be a non-empty string");
  }
  return value;
}

function optionalTimeout(value: unknown): number | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new TypeError("timeout must be a finite number greater than zero");
  }
  if (value > MAX_TIMEOUT_SECONDS) {
    throw new TypeError(`timeout must not exceed ${MAX_TIMEOUT_SECONDS} seconds`);
  }
  return value;
}

function success(value: unknown): ToolResult {
  return { content: JSON.stringify(value), isError: false };
}

function failure(value: unknown): ToolResult {
  return { content: JSON.stringify(value), isError: true };
}

function safeMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Command execution failed";
}

```

## src/tools/index.ts

Source: https://github.com/jackispm/nausicaa-harness/blob/b61edf67c46e1faf512695c3c12d0f7a4dd80eb3/src/tools/index.ts

SHA-256: `dbf09c2345a5b25debfcf4177f1764e6f4ce89323eb0d80db1dfaede13256b1e`

```
export {
  createBashTool,
  bashTool,
  type BashCommandExecutionInput,
  type BashCommandExecutor,
  type BashToolOptions,
} from "./bash.js";
export {
  SHELL_MAX_OUTPUT_BYTES,
  SHELL_MAX_OUTPUT_LINES,
  ShellOutputCapture,
  sanitizeShellOutput,
  type ShellOutputCaptureOptions,
  type ShellOutputChunkSink,
  type ShellOutputSink,
  type ShellOutputSnapshot,
} from "./shell-output.js";
export {
  artifactReadPointer,
  createArtifactReadTool,
  runArtifactHandle,
  ARTIFACT_READ_POINTER_PREFIX,
  ARTIFACT_READ_TOOL_NAME,
  DEFAULT_ARTIFACT_READ_BYTES,
  MAX_ARTIFACT_READ_BYTES,
  MAX_ARTIFACT_SOURCE_BYTES,
  ARTIFACT_READ_CACHE_MAX_BYTES,
  ARTIFACT_READ_CACHE_MAX_ENTRIES,
  ARTIFACT_READ_IN_FLIGHT_MAX_BYTES,
  ARTIFACT_READ_IN_FLIGHT_MAX_ENTRIES,
  RunArtifactAuthorization,
  type ArtifactReadStore,
  type ArtifactReadCacheOptions,
  type RunArtifactHandle,
} from "./artifact-read.js";
export { createEditFileTool, editFileTool } from "./edit-file.js";
export {
  APPLY_PATCH_MOWE_METADATA,
  applyPatchTool,
  createApplyPatchTool,
} from "./apply-patch.js";
export { createFindTool, findTool } from "./find.js";
export {
  createFileInfoTool,
  fileInfoTool,
  type FileInfo,
  type FileInfoToolOptions,
} from "./file-info.js";
export { createGrepTool, grepTool } from "./grep.js";
export {
  createGitDiffTool,
  createGitLogTool,
  createGitShowTool,
  createGitStatusTool,
  createGitTools,
  gitDiffTool,
  gitLogTool,
  gitShowTool,
  gitStatusTool,
  GIT_TOOL_MAX_OUTPUT_BYTES,
  GIT_TOOL_MAX_OUTPUT_LINES,
  GIT_TOOL_TIMEOUT_MS,
  type GitToolOptions,
} from "./git.js";
export { createListFilesTool, listFilesTool } from "./list-files.js";
export { createReadFileTool, readFileTool } from "./read-file.js";
export { createReadManyTool, readManyTool } from "./read-many.js";
export { createReadImageTool, readImageTool } from "./read-image.js";
export {
  createDirectoryCreateTool,
  createPathCopyTool,
  createPathDeleteTool,
  createPathMoveTool,
  directoryCreateTool,
  pathCopyTool,
  pathDeleteTool,
  pathMoveTool,
} from "./path-operations.js";
export {
  ProcessJobManager,
  FileProcessJobRegistry,
  MemoryProcessJobRegistry,
  ProcessJobRegistryError,
  createProcessJobTools,
  createProcessKillTool,
  createProcessListTool,
  createProcessOutputTool,
  createProcessStartTool,
  createProcessStatusTool,
  DEFAULT_PROCESS_JOB_MAX_JOBS,
  DEFAULT_PROCESS_JOB_MAX_OUTPUT_BYTES,
  DEFAULT_PROCESS_JOB_TIMEOUT_SECONDS,
  MAX_PROCESS_JOB_MAX_JOBS,
  MAX_PROCESS_JOB_OUTPUT_BYTES,
  MAX_PROCESS_JOB_REGISTRY_BYTES,
  MAX_PROCESS_JOB_REGISTRY_ENTRIES,
  MAX_PROCESS_JOB_TIMEOUT_SECONDS,
  PROCESS_JOB_MOWE_METADATA,
  type ProcessJobManagerOptions,
  type ProcessJobOutput,
  type ProcessJobOutputRequest,
  type ProcessJobListEntry,
  type ProcessJobRegistry,
  type ProcessJobRegistryEntry,
  type ProcessJobRegistryStatus,
  type ProcessJobSnapshot,
  type ProcessJobStartRequest,
  type ProcessJobState,
  type ProcessJobTerminationReason,
} from "./process-jobs.js";
export {
  type WorkspacePathPolicy,
  WorkspacePathError,
} from "./workspace-path.js";
export {
  buildBubblewrapWorkspaceArguments,
  buildSeatbeltWorkspaceProfile,
  createWorkspaceSandboxBashExecutor,
  WorkspaceCommandSandbox,
  WorkspaceCommandSandboxUnavailableError,
  type WorkspaceCommandSandboxOptions,
  type WorkspaceSandboxAvailability,
  type WorkspaceSandboxBackend,
} from "./workspace-command-sandbox.js";
export { createWriteFileTool, writeFileTool } from "./write-file.js";
export {
  createDuckDuckGoSearchTool,
  DuckDuckGoSearchProvider,
  parseDuckDuckGoResults,
  type DuckDuckGoSearchOptions,
} from "./web-search.js";
export {
  createWebFetchTool,
  createWebSearchTool,
  DEFAULT_WEB_FETCH_LIMITS,
  HttpWebFetchProvider,
  parseWebSearchQueries,
  WebToolError,
  type WebFetchBody,
  type WebFetchFunction,
  type WebFetchLimits,
  type WebFetchProvider,
  type WebFetchResult,
  type WebSearchProvider,
  type WebSearchResult,
  type WebSearchSource,
  type WebToolErrorCode,
  type WebToolOptions,
} from "./web.js";

import type { AgentTool } from "../domain/ports.js";
import { createBashTool, type BashCommandExecutor } from "./bash.js";
import { createEditFileTool } from "./edit-file.js";
import { createApplyPatchTool } from "./apply-patch.js";
import { createFindTool } from "./find.js";
import { createFileInfoTool } from "./file-info.js";
import { createGrepTool } from "./grep.js";
import { createGitTools } from "./git.js";
import { createListFilesTool } from "./list-files.js";
import { createReadFileTool } from "./read-file.js";
import { createReadManyTool } from "./read-many.js";
import { createReadImageTool } from "./read-image.js";
import {
  createDirectoryCreateTool,
  createPathCopyTool,
  createPathDeleteTool,
  createPathMoveTool,
} from "./path-operations.js";
import {
  createProcessJobTools,
  ProcessJobManager,
} from "./process-jobs.js";
import type { WorkspacePathPolicy } from "./workspace-path.js";
import { DuckDuckGoSearchProvider } from "./web-search.js";
import {
  createWebFetchTool,
  createWebSearchTool,
  HttpWebFetchProvider,
} from "./web.js";
import { createWriteFileTool } from "./write-file.js";

export interface WorkspaceToolOptions extends WorkspacePathPolicy {
  allowShell?: boolean;
  /** Replace the foreground Bash execution backend; ignored unless allowShell is true. */
  bashCommandExecutor?: BashCommandExecutor;
  allowWrite?: boolean;
  /** Include directory/copy/move/delete helpers with write tools. */
  allowPathOperations?: boolean;
  /** Include the default read-only file metadata capability. */
  includeFileInfo?: boolean;
  /** Include bounded read-only repository status/history/diff capabilities. */
  includeGit?: boolean;
  /** Enable provider-native workspace image input for the Main lane. */
  allowImages?: boolean;
  /** Enable run-scoped background shell jobs; requires allowShell as well. */
  allowProcessJobs?: boolean;
  /** Reuse a manager when several tool catalogs share one Run lifecycle. */
  processJobManager?: ProcessJobManager;
  /** Enable public web search/fetch providers for this tool surface. */
  allowNetwork?: boolean;
  /** @deprecated Use allowNetwork. Kept as a source-compatible alias. */
  allowWeb?: boolean;
  /** Inject a replacement web fetch backend. */
  webFetchProvider?: import("./web.js").WebFetchProvider;
  /** Inject a replacement web search backend. */
  webSearchProvider?: import("./web.js").WebSearchProvider;
}

export function createWorkspaceTools(options: WorkspaceToolOptions = {}): AgentTool[] {
  const policy = { protectedPaths: [...(options.protectedPaths ?? [])] };
  const tools = [
    createReadFileTool(policy),
    createReadManyTool(policy),
    createListFilesTool(policy),
    createGrepTool(policy),
    createFindTool(policy),
  ];
  if (options.includeFileInfo !== false) {
    tools.push(createFileInfoTool(policy));
  }
  if (options.includeGit !== false) {
    tools.push(...createGitTools(policy));
  }
  if (options.allowImages === true) {
    tools.push(createReadImageTool(policy));
  }
  if (options.allowNetwork === true || options.allowWeb === true) {
    const fetchProvider = options.webFetchProvider
      ?? new HttpWebFetchProvider();
    const searchProvider = options.webSearchProvider
      ?? new DuckDuckGoSearchProvider();
    tools.push(createWebFetchTool(fetchProvider), createWebSearchTool(searchProvider));
  }
  if (options.allowWrite === true) {
    tools.push(createWriteFileTool(policy), createEditFileTool(policy), createApplyPatchTool(policy));
    // A write-enabled product surface includes the complete workspace
    // mutation set. Legacy/evaluation callers can explicitly narrow it with
    // allowPathOperations: false.
    if (options.allowPathOperations !== false) {
      tools.push(
        createDirectoryCreateTool(policy),
        createPathCopyTool(policy),
        createPathMoveTool(policy),
        createPathDeleteTool(policy),
      );
    }
  }
  if (options.allowShell === true) {
    tools.push(createBashTool({
      ...(options.bashCommandExecutor === undefined
        ? {}
        : { commandExecutor: options.bashCommandExecutor }),
    }));
    if (options.allowProcessJobs === true) {
      tools.push(...createProcessJobTools(
        options.processJobManager ?? new ProcessJobManager(policy),
      ));
    }
  }
  return tools;
}

```
