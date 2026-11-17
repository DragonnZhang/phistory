# LibreChat — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## packages/api/src/agents/ptc.ts

Source: https://github.com/LibreChat-AI/LibreChat/blob/f4154b649efa37e1345ccc517a4d8d1ed2178c82/packages/api/src/agents/ptc.ts

SHA-256: `29f78a9a7bf7c3e8b10b526b32b86cfd98cd35430815225588f48a91d5ab122c`

```
import type { StructuredToolInterface } from '@librechat/agents/langchain/tools';
import type { PtcToolCallEvent } from 'librechat-data-provider';

/** Whole-preview budget for one inner call's arguments. */
const ARGS_PREVIEW_MAX_CHARS = 96;
/** Per-value budget, so one long string can't crowd out the other keys. */
const ARGS_VALUE_MAX_CHARS = 40;
const ERROR_PREVIEW_MAX_CHARS = 160;
/**
 * Collapsing whitespace can only shorten a string, so a window this many times
 * the visible budget is always long enough to fill it. Slicing to the window
 * before rewriting matters: this runs synchronously ahead of every inner
 * `invoke`, and without it a multi-megabyte argument would be collapsed in
 * full to produce a forty-character preview.
 */
const CLIP_OVERSCAN = 4;

/** Bounded collapse-and-clip: never rewrites more of `input` than the budget
 *  can possibly need, and marks any truncation it performed. */
const clip = (input: string, max: number): string => {
  const window = input.length > max * CLIP_OVERSCAN ? input.slice(0, max * CLIP_OVERSCAN) : input;
  const collapsed = window.replace(/\s+/g, ' ').trim();
  if (collapsed.length <= max && window.length === input.length) {
    return collapsed;
  }
  return `${collapsed.slice(0, max)}…`;
};

/**
 * Collapses an inner call's input into a single `key=value, key=value` line
 * for the CLI-style trace. Values are clipped individually and iteration stops
 * as soon as the joined preview can no longer grow, so a call with a large
 * body or many keys costs the same as a small one.
 */
export function summarizePtcArgs(input: unknown): string {
  if (input == null) {
    return '';
  }
  if (typeof input === 'string') {
    return clip(input, ARGS_PREVIEW_MAX_CHARS);
  }
  if (typeof input !== 'object' || Array.isArray(input)) {
    return clip(String(input), ARGS_PREVIEW_MAX_CHARS);
  }

  const record = input as Record<string, unknown>;
  const entries: string[] = [];
  let budget = ARGS_PREVIEW_MAX_CHARS;
  /* Keys, not entries: `Object.entries` would materialize every value before
   * the loop starts, so the budget check below could never skip the work it
   * exists to skip. */
  for (const key of Object.keys(record)) {
    if (budget <= 0) {
      break;
    }
    const value = record[key];
    if (value == null || value === '') {
      continue;
    }
    /* Strings are the values that get large (file bodies, request payloads),
     * and `clip` bounds them without touching the tail. Everything else is
     * small enough that serializing it first is cheaper than inspecting it. */
    const rendered = typeof value === 'string' ? value : safeStringify(value);
    if (rendered === '') {
      continue;
    }
    const entry = `${key}=${clip(rendered, ARGS_VALUE_MAX_CHARS)}`;
    entries.push(entry);
    budget -= entry.length + 2;
  }

  return clip(entries.join(', '), ARGS_PREVIEW_MAX_CHARS);
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value) ?? '';
  } catch {
    return '';
  }
}

export interface InstrumentPtcToolMapParams {
  /** The tool map the PTC runner resolves inner calls against. */
  toolMap: Map<string, StructuredToolInterface>;
  /** The PTC run step's tool call id — the card the trace renders under. */
  toolCallId: string;
  runId?: string;
  /**
   * Whether argument and failure previews may ride the stream. False when the
   * deployment filters tool arguments for PII: inner calls never pass through
   * `filteredToolArgumentsResult` (the sandbox bridge invokes them directly),
   * so a preview would put values on the wire that the configured policy
   * exists to keep off it — and a failure message routinely quotes the very
   * argument that caused it. The trace still reports name, status and duration.
   */
  includePreviews?: boolean;
  /**
   * Inner tools whose *name* trips the deployment's PII policy. The event
   * carries the tool name unconditionally, so a name the `name` filter would
   * have blocked on a direct call cannot be allowed to ride the trace instead.
   * These tools still execute — they are simply left unwrapped, so no event
   * about them is ever emitted.
   */
  traceExclusions?: ReadonlySet<string>;
  emit: (event: PtcToolCallEvent) => void;
}

/**
 * Wraps every tool the PTC sandbox can reach so each inner invocation reports
 * its lifecycle on the live stream. The runner (`executeTools` in
 * `@librechat/agents`) resolves a tool by name and calls `invoke` on it, so a
 * `Proxy` intercepting only `invoke` is enough — `name`, `schema`, `mcp` and
 * every other property the runner reads pass straight through to the real
 * tool, and nothing about execution changes.
 */
export function instrumentPtcToolMap({
  toolMap,
  toolCallId,
  runId,
  includePreviews = true,
  traceExclusions,
  emit,
}: InstrumentPtcToolMapParams): Map<string, StructuredToolInterface> {
  let sequence = 0;
  /** Emission is telemetry: a dead stream must never fail the program. */
  const safeEmit = (event: PtcToolCallEvent): void => {
    try {
      emit(event);
    } catch {
      /* stream closed or transport rejected — the run continues */
    }
  };

  const instrumented = new Map<string, StructuredToolInterface>();
  for (const [name, tool] of toolMap) {
    if (traceExclusions?.has(name)) {
      instrumented.set(name, tool);
      continue;
    }
    instrumented.set(
      name,
      new Proxy(tool, {
        get(target, property) {
          if (property !== 'invoke') {
            /** `target` as the receiver, not the proxy: LangChain tools read
             *  private class state through their own getters. */
            return Reflect.get(target, property, target);
          }
          return async (input: unknown, config?: unknown): Promise<unknown> => {
            const callId = `${toolCallId}:${sequence++}`;
            const startedAt = Date.now();
            safeEmit({
              tool_call_id: toolCallId,
              call_id: callId,
              name,
              status: 'running',
              ...(includePreviews ? { args: summarizePtcArgs(input) } : {}),
              ...(runId != null ? { runId } : {}),
            });
            try {
              const result = await (
                target.invoke as (input: unknown, config?: unknown) => Promise<unknown>
              ).call(target, input, config);
              safeEmit({
                tool_call_id: toolCallId,
                call_id: callId,
                name,
                status: 'success',
                durationMs: Date.now() - startedAt,
                ...(runId != null ? { runId } : {}),
              });
              return result;
            } catch (error) {
              safeEmit({
                tool_call_id: toolCallId,
                call_id: callId,
                name,
                status: 'error',
                ...(includePreviews
                  ? {
                      error: clip(
                        error instanceof Error ? error.message : String(error),
                        ERROR_PREVIEW_MAX_CHARS,
                      ),
                    }
                  : {}),
                durationMs: Date.now() - startedAt,
                ...(runId != null ? { runId } : {}),
              });
              throw error;
            }
          };
        },
      }) as StructuredToolInterface,
    );
  }

  return instrumented;
}

```

## packages/api/src/agents/run.ts

Source: https://github.com/LibreChat-AI/LibreChat/blob/f4154b649efa37e1345ccc517a4d8d1ed2178c82/packages/api/src/agents/run.ts

SHA-256: `37ce0fdd9f3d9f739f7fe5102f2e5eaede683978266084c3de30394620302c29`

```
import { randomUUID } from 'crypto';
import { logger } from '@librechat/data-schemas';
import { ensureHandler } from '@langchain/core/callbacks/manager';
import { Run, Providers, Constants, GraphEvents, HookRegistry } from '@librechat/agents';
import {
  KnownEndpoints,
  EModelEndpoint,
  ReasoningEffort,
  MAX_SUBAGENT_DEPTH,
  MAX_SUBAGENT_RUN_CONFIGS,
  extractEnvVariable,
  providerEndpointMap,
  normalizeEndpointName,
  mapModelToAzureConfig,
  resolveUseResponsesApi,
} from 'librechat-data-provider';
import type {
  SummarizationConfig as AgentSummarizationConfig,
  MultiAgentGraphConfig,
  ContextPruningConfig,
  CompactionSemanticIndex,
  OpenAIClientOptions,
  StandardGraphConfig,
  StreamPreemption,
  LCToolRegistry,
  SubagentConfig,
  SubagentResolveContext,
  SubagentConfigEntry,
  HookCallback,
  AgentInputs,
  FallbackConfig,
  GenericTool,
  RunConfig,
  IState,
  LCTool,
  SubagentTaskConfig,
} from '@librechat/agents';
import type {
  Agent,
  ImageDetail,
  TAzureConfig,
  CodeApprovalMode,
  TAgentsEndpoint,
  AgentModelParameters,
  AgentSubagentsConfig,
  AgentSubagentGraph,
  ReasoningResponseKey,
  SummarizationConfig,
  TurnFileConsumers,
} from 'librechat-data-provider';
import type { AppConfig, IAgentFadingTier, IUser } from '@librechat/data-schemas';
import type { CallbackHandlerMethods } from '@langchain/core/callbacks/base';
import type { ToolApprovalGrantStorage } from 'librechat-data-provider';
import type { BaseMessage } from '@librechat/agents/langchain/messages';
import type { Callbacks } from '@langchain/core/callbacks/manager';
import type { TerminalSteerHook, SteerPreemption } from '~/agents/steering/runtime';
import type { ModelBoundChatModelCallback } from '~/middleware/modelBoundContent';
import type { ScheduledMCPPolicyError } from '~/schedules/authorization/policy';
import type { ScheduleMCPExecution } from '~/schedules/authorization/execution';
import type { ModelErrorTrackerCallback } from '~/agents/failures/tracker';
import type { ToolInputValidationError } from '~/agents/toolValidation';
import type { ResolvedToolApprovalHook } from '~/agents/hitl/hooks';
import type { LangfuseTraceContext } from '~/langfuse/identity';
import type { ResolvedAlwaysApplySkill } from '~/agents/skills';
import type { CodeExecutionContext } from '~/agents/execution';
import type { SubagentCodeHostArgSpecs } from '~/code/targets';
import type { MCPToolAlias } from '~/tools/classification';
import type { ReviewedToolApprovals } from './hitl/modes';
import type { SubagentUsageEvent } from '~/agents/usage';
import type { ProvisionState } from '~/agents/resources';
import type { RunFileSession } from './files/session';
import type { RunFadingTiers } from './fading';
import type * as t from '~/types';
import {
  assertAttachedCodeEnvironmentApprovalSupported,
  collectNativeEditFileAgentIds,
  collectAttachedCodeEnvironmentAgentIds,
  collectAttachedCodeEnvironmentPolicySettings,
  collectAttachedCodeApprovalPolicies,
  createAttachedCodeEnvironmentPolicyHook,
  resolveAttachedCodeApprovalMode,
} from '~/agents/hitl/byom';
import {
  CHECK_BACKGROUND_TASK_NAME,
  registerBackgroundTaskTool,
  stripBackgroundFromToolRegistry,
  stripBackgroundFromToolDefinitions,
} from '~/agents/background';
import {
  createSubagentWakeupHandleHook,
  agentUsesSubagentCompletionWakeups,
  usesSubagentCompletionWakeups,
} from '~/agents/subagentDelivery';
import {
  resolveStreamLimits,
  resolveModelTransportTimeouts,
  resolveSubagentMaxTurns,
  resolveRecursionLimit,
} from '~/agents/config';
import {
  isSteeringSupported,
  isSteerPreemptSupported,
  isSteerTerminalContinuationSupported,
} from '~/agents/steering/runtime';
import {
  ASK_USER_QUESTION_TOOL_NAME,
  createAskUserQuestionTool,
} from '~/agents/hitl/askUserQuestionTool';
import {
  createRunFileTools,
  eventOnlyRunFileTools,
  isRunFileSharingSupported,
} from './files/runtime';
import { resolveToolApprovalPolicy, exemptAskUserQuestionFromApproval } from '~/agents/hitl/policy';
import { applyCustomHandoffPromptKeyCompatibility } from '~/agents/handoffPromptKeyCompatibility';
import { stripIntentFromToolRegistry, stripIntentFromToolDefinitions } from '~/agents/intent';
import { createAgentToolApprovalSession, bindRunToolApprovalSession } from './hitl/modes';
import { resolveConfigHeaders, resolveModelHeaders, mergeHeaders } from '~/utils/headers';
import { interruptToolHandler, supportsRunInterruption } from '~/agents/steering/tools';
import { extractDefaultParams, resolveReasoningParams } from '~/endpoints/openai/llm';
import { getLLMConfig as getAnthropicLLMConfig } from '~/endpoints/anthropic/llm';
import { reportSubagentResolutionFailure } from '~/agents/failures/subagent';
import { createScheduledMCPRunPolicy } from '~/schedules/authorization/run';
import { CREATE_FILE_TOOL_NAME, EDIT_FILE_TOOL_NAME } from '~/agents/tools';
import { buildAgentInitialToolSessions } from '~/agents/codeFilesSession';
import { getDirectDispatcher, getProxyDispatcher } from '~/utils/proxy';
import { getAzureCredentials, constructAzureURL } from '~/utils/azure';
import { buildEffectiveToolApprovalPolicy } from '~/agents/hitl/allow';
import { prepareQueuedCodeFileContext } from '~/files/code/queued';
import { getBuiltInBaseURL } from '~/endpoints/openai/initialize';
import { getProviderConfig } from '~/endpoints/config/providers';
import { buildToolApprovalHooks } from '~/agents/hitl/hooks';
import { getAgentCheckpointer } from '~/agents/checkpointer';
import { getPluginHookSource } from '~/agents/hooks/source';
import { getOpenAIConfig } from '~/endpoints/openai/config';
import { createStepBudgetHook } from '~/agents/stepBudget';
import { buildHITLRunWiring } from '~/agents/hitl/runtime';
import { buildLangfuseConfig } from '~/langfuse/config';
import { applyTestRunHook } from '~/agents/testHook';
import { frameDynamicInstructions } from './tail';
import { isUserProvided } from '~/utils/common';
import { createSafeUser } from '~/utils/env';

/** Expected shape of JSON tool search results */
interface ToolSearchJsonResult {
  found?: number;
  tools?: Array<{ name: string }>;
}

/**
 * Parses tool names from JSON-formatted tool_search output.
 * Format: { "found": N, "tools": [{ "name": "tool_name", ... }], ... }
 *
 * @param content - The JSON string content
 * @param discoveredTools - Set to add discovered tool names to
 * @returns true if parsing succeeded, false otherwise
 */
function parseToolSearchJson(content: string, discoveredTools: Set<string>): boolean {
  try {
    const parsed = JSON.parse(content) as ToolSearchJsonResult;
    if (!parsed.tools || !Array.isArray(parsed.tools)) {
      return false;
    }
    for (const tool of parsed.tools) {
      if (tool.name && typeof tool.name === 'string') {
        discoveredTools.add(tool.name);
      }
    }
    return parsed.tools.length > 0;
  } catch {
    return false;
  }
}

/**
 * Parses tool names from legacy text-formatted tool_search output.
 * Format: "- tool_name (score: X.XX)"
 *
 * @param content - The text content
 * @param discoveredTools - Set to add discovered tool names to
 */
function parseToolSearchLegacy(content: string, discoveredTools: Set<string>): void {
  const toolNameRegex = /^- ([^\s(]+)\s*\(score:/gm;
  let match: RegExpExecArray | null;
  while ((match = toolNameRegex.exec(content)) !== null) {
    const toolName = match[1];
    if (toolName) {
      discoveredTools.add(toolName);
    }
  }
}

/**
 * Extracts discovered tool names from message history by parsing tool_search results.
 * When the LLM calls tool_search, the result contains tool names that were discovered.
 * These tools should have defer_loading overridden to false on subsequent turns.
 *
 * Supports both:
 * - New JSON format: { "tools": [{ "name": "tool_name" }] }
 * - Legacy text format: "- tool_name (score: X.XX)"
 *
 * @param messages - The conversation message history
 * @returns Set of tool names that were discovered via tool_search
 */
export function extractDiscoveredToolsFromHistory(messages: BaseMessage[]): Set<string> {
  const discoveredTools = new Set<string>();

  for (const message of messages) {
    const msgType = message._getType?.() ?? message.constructor?.name ?? '';
    if (msgType !== 'tool') {
      continue;
    }

    const name = (message as { name?: string }).name;
    if (name !== Constants.TOOL_SEARCH) {
      continue;
    }

    const content = message.content;
    if (typeof content !== 'string') {
      continue;
    }

    /** Try JSON format first (new), fall back to regex (legacy) */
    if (!parseToolSearchJson(content, discoveredTools)) {
      parseToolSearchLegacy(content, discoveredTools);
    }
  }

  return discoveredTools;
}

/** MCP key-spelling aliases each run knows, including those its lazy subagents reported. */
const runMCPToolAliases = new WeakMap<object, readonly MCPToolAlias[]>();

/** The run's live alias list, so a pause can be judged against the aliases the run used. */
export function getRunMCPToolAliases(run: object | null | undefined): readonly MCPToolAlias[] {
  return run == null ? [] : (runMCPToolAliases.get(run) ?? []);
}

export interface RunDiscoverySnapshot {
  getDiscoveredTools?: () => string[];
  getRunMessages?: () => BaseMessage[] | undefined;
}

/** Reads canonical run discovery state, with best-effort history parsing for older releases. */
export function getRunDiscoveredTools(run: RunDiscoverySnapshot): string[] {
  if (typeof run.getDiscoveredTools === 'function') {
    const discoveredTools = run.getDiscoveredTools();
    if (Array.isArray(discoveredTools)) {
      return Array.from(new Set(discoveredTools));
    }
  }

  if (typeof run.getRunMessages !== 'function') {
    return [];
  }
  const messages = run.getRunMessages();
  if (!Array.isArray(messages) || messages.length === 0) {
    return [];
  }
  return Array.from(extractDiscoveredToolsFromHistory(messages));
}

/**
 * Extracts skill names that were invoked in previous turns from raw message payload.
 * Scans assistant messages for tool_call content parts where name === 'skill'.
 * Works with TPayload (raw message objects) so it can run before formatAgentMessages.
 *
 * @param payload - The raw conversation message payload
 * @returns Set of skill names that were previously invoked
 */
export function extractInvokedSkillsFromPayload(
  payload: Array<Partial<{ role: string; content: unknown }>>,
): Set<string> {
  const invokedSkills = new Set<string>();

  for (const message of payload) {
    if (message.role !== 'assistant') {
      continue;
    }

    const content = message.content;
    if (!Array.isArray(content)) {
      continue;
    }

    for (const part of content) {
      if (
        part == null ||
        typeof part !== 'object' ||
        (part as { type?: string }).type !== 'tool_call'
      ) {
        continue;
      }
      const toolCall = (part as { tool_call?: { name?: string; args?: unknown } }).tool_call;
      if (toolCall?.name !== Constants.SKILL_TOOL) {
        continue;
      }
      const rawArgs = toolCall.args;
      const args =
        typeof rawArgs === 'string'
          ? (() => {
              try {
                return JSON.parse(rawArgs) as Record<string, unknown>;
              } catch {
                return {};
              }
            })()
          : (rawArgs as Record<string, unknown> | undefined);
      const skillName = args?.skillName;
      if (typeof skillName === 'string' && skillName.length > 0) {
        invokedSkills.add(skillName);
      }
    }
  }

  return invokedSkills;
}

/**
 * Overrides defer_loading to false for tools that were already discovered via tool_search.
 * This prevents the LLM from having to re-discover tools on every turn.
 *
 * @param toolRegistry - The tool registry to modify (mutated in place)
 * @param discoveredTools - Set of tool names that were previously discovered
 * @returns Number of tools that had defer_loading overridden
 */
export function overrideDeferLoadingForDiscoveredTools(
  toolRegistry: LCToolRegistry,
  discoveredTools: Set<string>,
): number {
  let overrideCount = 0;
  for (const toolName of discoveredTools) {
    const toolDef = toolRegistry.get(toolName);
    if (toolDef && toolDef.defer_loading === true) {
      toolDef.defer_loading = false;
      overrideCount++;
    }
  }
  return overrideCount;
}

const customProviders = new Set([
  Providers.XAI,
  Providers.DEEPSEEK,
  Providers.MOONSHOT,
  Providers.OPENROUTER,
  KnownEndpoints.ollama,
]);

type AgentReasoningKey = 'reasoning_content' | 'reasoning';

function includesOpenRouter(value?: string | null): boolean {
  return typeof value === 'string' && value.toLowerCase().includes(KnownEndpoints.openrouter);
}

export function getReasoningKey(
  provider: Providers,
  llmConfig: t.RunLLMConfig,
  agentEndpoint?: string | null,
  customReasoningKey?: ReasoningResponseKey,
): AgentReasoningKey {
  if (customReasoningKey) {
    return customReasoningKey as AgentReasoningKey;
  }

  let reasoningKey: AgentReasoningKey = 'reasoning_content';
  if (provider === Providers.GOOGLE) {
    reasoningKey = 'reasoning';
  } else if (
    includesOpenRouter(llmConfig.configuration?.baseURL) ||
    includesOpenRouter(agentEndpoint)
  ) {
    reasoningKey = 'reasoning';
  } else if (
    (llmConfig as OpenAIClientOptions).useResponsesApi === true &&
    (provider === Providers.OPENAI || provider === Providers.AZURE)
  ) {
    reasoningKey = 'reasoning';
  }
  return reasoningKey;
}

const DEEPSEEK_MODEL_PATTERN = /^deepseek(?:[-/]|$)/i;
const OPENROUTER_LATEST_ROUTING_PREFIX = /^~/;

function matchesDeepSeekModel(model?: string | null): boolean {
  if (typeof model !== 'string' || model.length === 0) {
    return false;
  }
  return DEEPSEEK_MODEL_PATTERN.test(model.replace(OPENROUTER_LATEST_ROUTING_PREFIX, ''));
}

/**
 * Whether the (provider, model) pair targets DeepSeek's thinking-mode
 * tool-calling contract, which requires `reasoning_content` to be replayed
 * on every prior assistant message that emitted `tool_calls`.
 * @see https://api-docs.deepseek.com/guides/thinking_mode#tool-calls
 */
export function isDeepSeekReasoningProvider(
  provider: string | Providers | undefined | null,
  model?: string | null,
): boolean {
  if (typeof provider === 'string' && provider.length > 0) {
    const normalized = provider.toLowerCase();
    if (normalized === Providers.DEEPSEEK) {
      return true;
    }
    if (normalized === Providers.OPENROUTER) {
      return matchesDeepSeekModel(model);
    }
  }
  return matchesDeepSeekModel(model);
}

/**
 * Whether prior assistant tool-call messages should have `reasoning_content`
 * reconstructed when reformatting persisted history (cross-turn replay): either
 * DeepSeek thinking-mode (#13366) or a custom OpenAI-compatible endpoint that
 * opted in via `customParams.includeReasoningHistory` (e.g. Xiaomi MiMo, Kimi).
 */
export function shouldReplayReasoningContent(
  agent?: {
    provider?: string | Providers | null;
    model?: string | null;
    model_parameters?: { model?: string | null } | null;
    includeReasoningHistory?: boolean | null;
  } | null,
): boolean {
  if (agent == null) {
    return false;
  }
  if (agent.includeReasoningHistory === true) {
    return true;
  }
  return isDeepSeekReasoningProvider(agent.provider, agent.model_parameters?.model ?? agent.model);
}

type RunAgent = Omit<Agent, 'tools'> & {
  provisionState?: ProvisionState;
  fileConsumers?: TurnFileConsumers;
  azureOptions?: t.AzureOptions;
  tools?: GenericTool[];
  maxContextTokens?: number;
  /** Pre-ratio context budget from initializeAgent. */
  baseContextTokens?: number;
  useLegacyContent?: boolean;
  imageDetail?: ImageDetail;
  toolContextMap?: Record<string, unknown>;
  dynamicToolContextMap?: Record<string, unknown>;
  toolRegistry?: LCToolRegistry;
  /** Serializable tool definitions for event-driven execution */
  toolDefinitions?: LCTool[];
  /** Precomputed flag indicating if any tools have defer_loading enabled */
  hasDeferredTools?: boolean;
  /** Both-direction identity aliases for MCP tools whose key spelling changed */
  mcpToolAliases?: MCPToolAlias[];
  /** Names of tools injected with the `run_in_background` param (excluded from eager execution). */
  backgroundToolNames?: string[];
  /** Names of tools with the host-injected `intent` param (stripped from self-spawn inputs). */
  intentToolNames?: string[];
  /** Marker-verified tool names whose intent labels are safe compaction guidance. */
  semanticIntentToolNames?: string[];
  /**
   * Per-agent codeenv gate set by `initializeAgent`: admin-level
   * `execute_code` capability AND the agent actually requested
   * `execute_code` in its tools. Used here to enable
   * `RunConfig.toolOutputReferences` only on runs where the bash tool
   * is actually registered.
   */
  codeEnvAvailable?: boolean;
  /**
   * Per-agent stateful-session gate set by `initializeAgent`: the admin
   * `stateful_code_sessions` capability AND the agent's builder opt-in AND
   * `codeEnvAvailable`. Carried into per-agent tool loading and prewarming.
   */
  statefulCodeSessions?: boolean;
  /** Per-agent stateful workspace sharing scope. */
  statefulCodeEnvironment?: Agent['stateful_code_environment'];
  /** Trusted partition for transient code session ids and file references. */
  codeSessionKey?: string;
  /** Trusted Code API route selected during initialization. */
  codeExecutionContext?: CodeExecutionContext;
  /** Whether this initialized agent can route skills/ writes to persistent skill storage. */
  skillAuthoringAvailable?: boolean;
  /** Optional per-agent summarization overrides */
  summarization?: SummarizationConfig;
  /** Response field to read model reasoning from for custom OpenAI-compatible endpoints. */
  reasoningKey?: ReasoningResponseKey;
  /** Whether to reconstruct `reasoning_content` from persisted history across turns. */
  includeReasoningHistory?: boolean;
  /**
   * Maximum characters allowed in a single tool result before truncation.
   * Overrides the default computed from maxContextTokens.
   */
  maxToolResultChars?: number;
  /** Initialized subagent configs (loaded by initialize.js from agent.subagents.agent_ids). */
  subagentAgentConfigs?: RunAgent[];
  /**
   * Inert, VIEW-checked descriptors for explicit children that are initialized
   * only after the SDK selects them. These resolvers are request-scoped: they
   * may use the active request's authorization and tool-loading context.
   */
  lazySubagentConfigs?: LazySubagentAgent[];
  /** All-or-nothing saved-agent teams resolved by initialize.js. */
  subagentGraphConfigs?: Array<{
    definition: AgentSubagentGraph;
    memberConfigs: RunAgent[];
  }>;
  /** Member-scoped always-apply skills resolved during agent initialization. */
  alwaysApplySkillPrimes?: ResolvedAlwaysApplySkill[];
  /** Source subagent spawning configuration (enabled / allowSelf / agent_ids). */
  subagents?: AgentSubagentsConfig;
};

type LazySubagentAgent = Pick<
  RunAgent,
  | 'id'
  | 'name'
  | 'description'
  | 'provider'
  | 'model'
  | 'model_parameters'
  | 'recursion_limit'
  | 'subagents'
  | 'codeEnvAvailable'
  | 'statefulCodeSessions'
  | 'statefulCodeEnvironment'
  | 'codeExecutionContext'
  | 'codeSessionKey'
  | 'skillAuthoringAvailable'
  | 'includeReasoningHistory'
  | 'mcpToolAliases'
> & {
  configId: string;
  /** Per-call machine choices declared on the child's subagent call. */
  subagentHostArgs?: SubagentCodeHostArgSpecs;
  /** The routes behind those choices, for run-wide gates and approval bindings. */
  codeExecutionChoices?: CodeExecutionContext[];
  subagentAgentConfigs?: RunAgent[];
  lazySubagentConfigs?: LazySubagentAgent[];
  /** Lightweight graph-member metadata used only by run-wide capability gates. */
  subagentGraphMemberMetadata?: SubagentTreeNode[];
  resolve: (context: SubagentResolveContext) => Promise<RunAgent>;
  /**
   * Wraps the whole selection, through the inputs handed to the SDK, so host state the
   * call reserved is kept only when the child is actually exposed.
   */
  settle?: <T>(context: SubagentResolveContext, resolveInputs: () => Promise<T>) => Promise<T>;
};

type SubagentTreeNode = Pick<
  RunAgent,
  | 'id'
  | 'provider'
  | 'model'
  | 'model_parameters'
  | 'codeEnvAvailable'
  | 'statefulCodeSessions'
  | 'statefulCodeEnvironment'
  | 'codeExecutionContext'
  | 'codeSessionKey'
  | 'skillAuthoringAvailable'
  | 'includeReasoningHistory'
  | 'mcpToolAliases'
> & {
  codeExecutionChoices?: CodeExecutionContext[];
  subagentAgentConfigs?: SubagentTreeNode[];
  lazySubagentConfigs?: SubagentTreeNode[];
  subagentGraphMemberMetadata?: SubagentTreeNode[];
  subagentGraphConfigs?: Array<{ memberConfigs: SubagentTreeNode[] }>;
};

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

const UNRESOLVED_ENV_VAR_PLACEHOLDER = /\$\{[^}]+\}/;

function hasUnresolvedPlaceholder(value: string): boolean {
  return UNRESOLVED_ENV_VAR_PLACEHOLDER.test(value);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value);
}

const nullableAgentModelParameterKeys = [
  'temperature',
  'maxContextTokens',
  'max_context_tokens',
  'max_output_tokens',
  'top_p',
  'frequency_penalty',
  'presence_penalty',
] satisfies Array<keyof AgentModelParameters>;

function normalizeAgentModelParameters(
  modelParameters: AgentModelParameters | undefined,
): Partial<AgentModelParameters> | undefined {
  if (!modelParameters) {
    return undefined;
  }
  const normalized: Partial<AgentModelParameters> = { ...modelParameters };
  for (const key of nullableAgentModelParameterKeys) {
    if (normalized[key] === null) {
      delete normalized[key];
    }
  }
  return normalized;
}

/**
 * Merges user-supplied summarization parameters on top of endpoint-resolved
 * overrides. User params win for top-level keys; `configuration` is
 * deep-merged so user additions (e.g. `defaultQuery`) don't wipe out the
 * resolved `baseURL`/`defaultHeaders`/`fetchOptions`. When transport resolution already
 * consumed the user's URL, retain its normalized form instead of restoring the raw template.
 */
function mergeParameters(
  overrides: SummarizationClientOverrides,
  userParams: SummarizationConfig['parameters'],
  resolvedTransport = false,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...overrides, ...(userParams ?? {}) };
  const userConfiguration = (userParams as Record<string, unknown> | undefined)?.configuration;
  if (isPlainObject(overrides.configuration) && isPlainObject(userConfiguration)) {
    merged.configuration = {
      ...overrides.configuration,
      ...userConfiguration,
      ...(resolvedTransport ? { baseURL: overrides.configuration.baseURL } : {}),
      defaultHeaders: mergeHeaders(
        overrides.configuration.defaultHeaders as Record<string, string> | undefined,
        userConfiguration.defaultHeaders as Record<string, string> | undefined,
      ),
      defaultQuery: {
        ...(isPlainObject(overrides.configuration.defaultQuery)
          ? overrides.configuration.defaultQuery
          : {}),
        ...(isPlainObject(userConfiguration.defaultQuery) ? userConfiguration.defaultQuery : {}),
      },
    };
  }
  return merged;
}

/** `model_parameters` is the agent's resolved `llmConfig`, whose kwargs the schema type omits. */
function agentModelKwargs(
  modelParameters: AgentModelParameters | undefined,
): Record<string, unknown> | undefined {
  if (modelParameters == null || !('modelKwargs' in modelParameters)) {
    return undefined;
  }
  return isPlainObject(modelParameters.modelKwargs) ? modelParameters.modelKwargs : undefined;
}

/**
 * `getOpenAILLMConfig` carries an Azure Astra deployment alias in
 * `modelKwargs.model`, which langchain spreads after `model`. The SDK's
 * same-provider summarizer copies the agent's client options and overrides only
 * `model`, so a summarizer on another model would still reach the agent's
 * deployment. Hand it the agent's kwargs without the alias.
 */
function summarizationModelKwargs(
  agentKwargs: Record<string, unknown> | undefined,
  agentModel: string | undefined,
  summarizationModel: string | undefined,
): Record<string, unknown> | undefined {
  if (agentKwargs == null || !('model' in agentKwargs)) {
    return undefined;
  }
  if (!isNonEmptyString(summarizationModel) || summarizationModel === agentModel) {
    return undefined;
  }
  const { model: _alias, ...kwargs } = agentKwargs;
  return kwargs;
}

/**
 * Mirrors `getOpenAIConfig`'s `llmConfig` shape (plus its `configOptions`
 * assigned to `configuration`). Index signature covers fields that the
 * helper emits dynamically per provider variant.
 */
interface SummarizationClientOverrides {
  apiKey?: string;
  streaming?: boolean;
  configuration?: t.OpenAIConfiguration;
  [key: string]: unknown;
}

/**
 * A user-supplied base URL in `summarization.parameters` points the summarizer
 * at a gateway whose contract is not the built-in provider's, so no built-in
 * request shaping may be claimed for it.
 */
function hasBaseURLOverride(parameters: SummarizationConfig['parameters']): boolean {
  if (!isPlainObject(parameters)) {
    return false;
  }
  const params = parameters as Record<string, unknown>;
  if (isNonEmptyString(params.baseURL)) {
    return true;
  }
  return isPlainObject(params.configuration) && 'baseURL' in params.configuration;
}

/**
 * The scalar `reasoning_effort` the yaml schema accepts, when it names a known
 * effort. `getOpenAIConfig` reads the effort from `modelOptions` — the merged
 * `parameters` reach it too late — so a summarizer that configures one has to
 * hand it over for the same API routing the agent flow performs.
 */
function summarizationReasoningEffort(
  parameters: SummarizationConfig['parameters'],
): ReasoningEffort | undefined {
  if (!isPlainObject(parameters)) {
    return undefined;
  }
  const effort = (parameters as Record<string, unknown>).reasoning_effort;
  const known = Object.values(ReasoningEffort) as string[];
  return typeof effort === 'string' && known.includes(effort)
    ? (effort as ReasoningEffort)
    : undefined;
}

/**
 * Builds the model-specific request shaping a built-in provider's client needs,
 * for the cross-provider case where the SDK builds that client from these
 * parameters alone.
 *
 * The custom-endpoint path below already runs `getOpenAIConfig`; built-in
 * providers skipped it entirely, so a summarizer never learned which API its
 * model takes or whether its endpoint is first-party — and the agents SDK
 * defaults its model-specific constraints off without that declaration
 * (LibreChat#15598).
 *
 * Credentials and base URLs are not returned: the SDK still resolves them as
 * before. Cross-provider OpenAI-family clients do receive the Agent transport
 * timeout policy, even when a URL override prevents built-in request shaping.
 */
function resolveBuiltInClientOverrides(
  provider: string,
  target: {
    model?: string;
    parameters?: SummarizationConfig['parameters'];
    agentProvider?: string;
  },
  appConfig: AppConfig,
): SummarizationClientOverrides | undefined {
  const { model, parameters } = target;
  /**
   * Mirrors the SDK's own condition: when the summarization provider matches the
   * agent's, `buildSummarizationClientConfig` spreads the agent's resolved client
   * options and these parameters layer on top. A custom-endpoint agent is
   * normalized to the `openAI` provider while keeping its own endpoint name, so
   * declaring built-in constraints here would claim OpenAI's contract for that
   * gateway.
   */
  if (provider === target.agentProvider) {
    return undefined;
  }
  let transportOverrides: SummarizationClientOverrides | undefined;
  if (provider === Providers.OPENAI || provider === Providers.AZURE) {
    const timeouts = resolveModelTransportTimeouts(appConfig.endpoints?.agents);
    transportOverrides = {
      configuration: {
        fetchOptions: {
          dispatcher:
            getProxyDispatcher(process.env.PROXY, timeouts) ?? getDirectDispatcher(timeouts),
        },
      },
    };
  }
  const baseURL = getBuiltInBaseURL(provider);
  /** URL overrides still get timeouts, but must not inherit first-party request shaping. */
  if (!isNonEmptyString(model) || hasBaseURLOverride(parameters) || isUserProvided(baseURL)) {
    return transportOverrides;
  }
  const { llmConfig } = getOpenAIConfig(
    '',
    {
      modelOptions: { model, reasoning_effort: summarizationReasoningEffort(parameters) },
      reverseProxyUrl: baseURL,
    },
    provider,
  );
  const {
    apiKey: _apiKey,
    model: _model,
    modelName: _modelName,
    streaming: _streaming,
    ...shaping
  } = llmConfig;
  return Object.keys(shaping).length > 0
    ? { ...shaping, ...transportOverrides }
    : transportOverrides;
}

/**
 * Memory bound for the warning deduplication below, not an operator setting: it only decides when a
 * warning for one of more than this many distinct, concurrently recurring problems is logged again.
 */
const MAX_UNRESOLVED_SUMMARIZATION_WARNINGS = 256;
/** Reported summarization misconfigurations per tenant, least recently seen first. */
const unresolvedSummarizationWarnings = new Set<string>();

function warnUnresolvedSummarization(message: string, tenantId?: string): void {
  const key = `${tenantId ?? ''}\n${message}`;
  if (unresolvedSummarizationWarnings.delete(key)) {
    unresolvedSummarizationWarnings.add(key);
    return;
  }
  if (unresolvedSummarizationWarnings.size >= MAX_UNRESOLVED_SUMMARIZATION_WARNINGS) {
    const [leastRecentlySeen] = unresolvedSummarizationWarnings;
    unresolvedSummarizationWarnings.delete(leastRecentlySeen);
  }
  unresolvedSummarizationWarnings.add(key);
  if (tenantId == null) {
    logger.warn(`[createRun] ${message}`);
    return;
  }
  logger.warn(`[createRun] ${message}`, { tenantId });
}

/** Azure base URL templates the client fills from its own options rather than the environment. */
const AZURE_URL_TEMPLATE = /(\$\{(?:INSTANCE_NAME|DEPLOYMENT_NAME)\})/;

/** The URL's segments around Azure's reserved templates, which sit at the odd indexes. */
function splitAzureURLTemplates(url: string): string[] {
  return url.split(AZURE_URL_TEMPLATE);
}

/** Expands environment references in a URL, leaving Azure's reserved templates for an Azure client. */
function expandTransportURL(url: string, targetsAzure: boolean): string {
  if (!targetsAzure) {
    return extractEnvVariable(url);
  }
  return splitAzureURLTemplates(url)
    .map((segment, index) => (index % 2 === 1 ? segment : extractEnvVariable(segment)))
    .join('');
}

/**
 * Admin-authored transport values may reference environment variables, as endpoint credentials
 * do. Expanded once, before resolution and before the parameters are layered over the client.
 */
function expandSummarizationTransport(
  parameters: SummarizationConfig['parameters'],
  targetsAzure: boolean,
): SummarizationConfig['parameters'] {
  if (!isPlainObject(parameters)) {
    return parameters;
  }
  const params = parameters as Record<string, unknown>;
  const expanded: Record<string, unknown> = { ...params };
  if (typeof params.apiKey === 'string') {
    expanded.apiKey = extractEnvVariable(params.apiKey);
  }
  if (typeof params.baseURL === 'string') {
    expanded.baseURL = expandTransportURL(params.baseURL, targetsAzure);
  }
  if (isPlainObject(params.configuration) && typeof params.configuration.baseURL === 'string') {
    expanded.configuration = {
      ...params.configuration,
      baseURL: expandTransportURL(params.configuration.baseURL, targetsAzure),
    };
  }
  return expanded as SummarizationConfig['parameters'];
}

/** The base URL and API key a summarization target's own parameters set, which replace the resolved ones. */
function summarizationTransportOverrides(parameters: SummarizationConfig['parameters']): {
  baseURL?: string;
  apiKey?: string;
} {
  const configuration = (parameters as Record<string, unknown> | undefined)?.configuration;
  const configurationBaseURL =
    isPlainObject(configuration) && typeof configuration.baseURL === 'string'
      ? configuration.baseURL
      : undefined;
  return {
    baseURL:
      configurationBaseURL ??
      (typeof parameters?.baseURL === 'string' ? parameters.baseURL : undefined),
    apiKey: typeof parameters?.apiKey === 'string' ? parameters.apiKey : undefined,
  };
}

/** Reject unusable transports while shaping the run, before a compaction client can fail it. */
function isSummarizationURLAvailable(baseURL: string | undefined, azure?: t.AzureOptions): boolean {
  if (baseURL == null) {
    return azure == null || isNonEmptyString(azure.azureOpenAIApiInstanceName);
  }
  try {
    const url = new URL(constructAzureURL({ baseURL, azureOptions: azure }));
    return (
      (url.protocol === 'https:' || url.protocol === 'http:') && !hasUnresolvedPlaceholder(url.href)
    );
  } catch {
    return false;
  }
}

/**
 * Azure Responses shares OpenAI's SDK provider, so switching endpoints must replace its transport.
 * Returns `undefined` when the OpenAI credentials cannot be resolved without a per-user lookup.
 */
function resolveOpenAISummarization(
  model: string,
  appConfig: AppConfig | undefined,
  parameters: SummarizationConfig['parameters'],
  headerContext: { user?: IUser; tenantId?: string; requestBody?: t.RequestBody },
): { provider: string; clientOverrides: SummarizationClientOverrides } | undefined {
  const overrides = summarizationTransportOverrides(parameters);
  const baseURL = overrides.baseURL ?? getBuiltInBaseURL(EModelEndpoint.openAI);
  const apiKey = overrides.apiKey ?? process.env.OPENAI_API_KEY;
  if (
    !apiKey ||
    !isSummarizationURLAvailable(baseURL) ||
    isUserProvided(baseURL) ||
    isUserProvided(apiKey) ||
    hasUnresolvedPlaceholder(apiKey) ||
    (baseURL != null && hasUnresolvedPlaceholder(baseURL))
  ) {
    warnUnresolvedSummarization(
      `Summarization with OpenAI model "${model}" is disabled for Azure OpenAI agents: it needs a server-configured OpenAI API key and base URL.`,
      headerContext.tenantId,
    );
    return undefined;
  }
  const headers = mergeHeaders(
    appConfig?.endpoints?.all?.headers,
    appConfig?.endpoints?.openAI?.headers,
  );
  const { llmConfig, configOptions } = getOpenAIConfig(
    apiKey,
    {
      modelOptions: {
        model,
        reasoning_effort: summarizationReasoningEffort(parameters),
        useResponsesApi:
          typeof parameters?.useResponsesApi === 'boolean' ? parameters.useResponsesApi : undefined,
      },
      reverseProxyUrl: baseURL,
      proxy: process.env.PROXY ?? undefined,
      headers: resolveModelHeaders({
        headers: headers ?? {},
        user: createSafeUser(headerContext.user),
        tenantId: headerContext.tenantId,
        body: headerContext.requestBody,
      }),
      transportTimeouts: resolveModelTransportTimeouts(appConfig?.endpoints?.agents),
    },
    EModelEndpoint.openAI,
  );
  return {
    provider: Providers.OPENAI,
    clientOverrides: {
      ...llmConfig,
      apiKey,
      useResponsesApi: llmConfig.useResponsesApi ?? false,
      firstPartyEndpoint: llmConfig.firstPartyEndpoint ?? false,
      reasoning: llmConfig.reasoning,
      modelKwargs: llmConfig.modelKwargs ?? {},
      configuration: configOptions,
    },
  };
}

type AzureSummarizationTarget = Omit<ReturnType<typeof mapModelToAzureConfig>, 'azureOptions'> & {
  azureOptions: t.AzureOptions;
  group?: TAzureConfig['groupMap'][string];
};

/**
 * The summary model's Azure credentials, resolved the way `initializeOpenAI` resolves an agent's:
 * the `azureOpenAI` configuration when present, otherwise the legacy environment credentials.
 * Returns `undefined` (reported once) when the configuration does not define the model.
 */
function resolveAzureSummarizationTarget(
  model: string,
  azureConfig: TAzureConfig | undefined,
  tenantId: string | undefined,
): AzureSummarizationTarget | undefined {
  if (!azureConfig) {
    return { azureOptions: getAzureCredentials() };
  }
  try {
    const groupName = azureConfig.modelGroupMap[model]?.group;
    return {
      ...mapModelToAzureConfig({
        modelName: model,
        modelGroupMap: azureConfig.modelGroupMap,
        groupMap: azureConfig.groupMap,
      }),
      group: groupName ? azureConfig.groupMap[groupName] : undefined,
    };
  } catch (error) {
    warnUnresolvedSummarization(
      `Summarization with Azure OpenAI model "${model}" is disabled: ${(error as Error).message}`,
      tenantId,
    );
    return undefined;
  }
}

/** Reuse request-resolved credentials for self-summaries, including user-provided Azure keys. */
function azureSummarizationSource(agent: RunAgent): AzureSummarizationTarget {
  const options = agent.model_parameters as Partial<t.OAIClientOptions> & t.AzureOptions;
  const configuration = options.configuration;
  const baseURL = configuration?.baseURL ?? options.azureOpenAIBasePath;
  const deployment = agentModelKwargs(agent.model_parameters)?.model;
  return {
    serverless: agent.useLegacyContent === true,
    baseURL: baseURL ?? undefined,
    azureOptions: {
      azureOpenAIApiKey:
        options.azureOpenAIApiKey ??
        (typeof options.apiKey === 'string' ? options.apiKey : undefined),
      azureOpenAIApiInstanceName:
        options.azureOpenAIApiInstanceName ?? agent.azureOptions?.azureOpenAIApiInstanceName,
      azureOpenAIApiDeploymentName:
        options.azureOpenAIApiDeploymentName ??
        (typeof deployment === 'string' ? deployment : options.model),
      azureOpenAIApiVersion:
        options.azureOpenAIApiVersion ??
        agent.azureOptions?.azureOpenAIApiVersion ??
        configuration?.defaultQuery?.['api-version'] ??
        undefined,
    },
  };
}

/**
 * Resolve the summary model's deployment and transport before the SDK inherits agent options.
 * A same-provider summarizer layers these over the agent's client options, so a summary group
 * without a base path clears the agent's instead of sending its deployment to that resource.
 * Returns `undefined` when the model cannot be resolved without a per-user lookup.
 */
function resolveAzureSummarization(
  model: string,
  appConfig: AppConfig | undefined,
  parameters: SummarizationConfig['parameters'],
  headerContext: { user?: IUser; tenantId?: string; requestBody?: t.RequestBody },
  source?: RunAgent,
): { provider: string; clientOverrides: SummarizationClientOverrides } | undefined {
  const sourceOptions = source?.model_parameters as Partial<t.OAIClientOptions> | undefined;
  const target = source
    ? azureSummarizationSource(source)
    : resolveAzureSummarizationTarget(
        model,
        appConfig?.endpoints?.[EModelEndpoint.azureOpenAI],
        headerContext.tenantId,
      );
  if (!target) {
    return undefined;
  }
  const { baseURL, headers, serverless, group } = target;
  const overrides = summarizationTransportOverrides(parameters);
  const azureOptions: t.AzureOptions = {
    ...target.azureOptions,
    azureOpenAIApiKey: overrides.apiKey ?? target.azureOptions.azureOpenAIApiKey,
  };
  const resolvedBaseURL =
    overrides.baseURL ?? baseURL ?? getBuiltInBaseURL(EModelEndpoint.azureOpenAI);
  if (
    !azureOptions.azureOpenAIApiKey ||
    !isSummarizationURLAvailable(resolvedBaseURL, serverless ? undefined : azureOptions) ||
    Object.values(azureOptions).some(
      (value) =>
        typeof value === 'string' && (isUserProvided(value) || hasUnresolvedPlaceholder(value)),
    ) ||
    isUserProvided(resolvedBaseURL) ||
    (resolvedBaseURL != null &&
      splitAzureURLTemplates(resolvedBaseURL).some(
        (segment, index) => index % 2 === 0 && hasUnresolvedPlaceholder(segment),
      ))
  ) {
    warnUnresolvedSummarization(
      `Summarization with Azure OpenAI model "${model}" is disabled: it needs a server-configured Azure OpenAI API key and base URL.`,
      headerContext.tenantId,
    );
    return undefined;
  }
  const resolvedHeaders = sourceOptions
    ? (sourceOptions.configuration?.defaultHeaders as Record<string, string> | undefined)
    : resolveModelHeaders({
        headers: mergeHeaders(appConfig?.endpoints?.all?.headers, headers) ?? {},
        user: createSafeUser(headerContext.user),
        tenantId: headerContext.tenantId,
        body: headerContext.requestBody,
      });
  const { llmConfig, configOptions } = getOpenAIConfig(
    azureOptions.azureOpenAIApiKey,
    {
      azure: serverless ? undefined : azureOptions,
      reverseProxyUrl: resolvedBaseURL,
      proxy: process.env.PROXY ?? undefined,
      headers:
        serverless || sourceOptions != null
          ? { ...resolvedHeaders, 'api-key': azureOptions.azureOpenAIApiKey }
          : resolvedHeaders,
      defaultQuery:
        serverless && azureOptions.azureOpenAIApiVersion
          ? { 'api-version': azureOptions.azureOpenAIApiVersion }
          : undefined,
      modelOptions: {
        model: source?.model ?? model,
        reasoning_effort: summarizationReasoningEffort(parameters),
        useResponsesApi:
          typeof parameters?.useResponsesApi === 'boolean'
            ? parameters.useResponsesApi
            : sourceOptions?.useResponsesApi,
      },
      addParams: {
        ...group?.addParams,
        ...(typeof parameters?.useResponsesApi === 'boolean'
          ? { useResponsesApi: parameters.useResponsesApi }
          : {}),
      },
      dropParams: group?.dropParams?.filter(
        (key) => key !== 'useResponsesApi' || typeof parameters?.useResponsesApi !== 'boolean',
      ),
      transportTimeouts: resolveModelTransportTimeouts(appConfig?.endpoints?.agents),
    },
    EModelEndpoint.azureOpenAI,
  );
  const sourceKwargs = agentModelKwargs(source?.model_parameters);
  const preservesApiMode = !!sourceOptions?.useResponsesApi === !!llmConfig.useResponsesApi;
  const sourceTokenLimit = sourceKwargs?.max_output_tokens ?? sourceKwargs?.max_completion_tokens;
  const inheritedTokenLimit =
    typeof sourceTokenLimit === 'number'
      ? {
          [llmConfig.useResponsesApi ? 'max_output_tokens' : 'max_completion_tokens']:
            sourceTokenLimit,
        }
      : {};
  return {
    provider: !serverless && !llmConfig.useResponsesApi ? Providers.AZURE : Providers.OPENAI,
    clientOverrides: {
      ...sourceOptions,
      azureOpenAIBasePath: undefined,
      azureOpenAIApiKey: undefined,
      azureOpenAIApiInstanceName: undefined,
      azureOpenAIApiDeploymentName: undefined,
      azureOpenAIApiVersion: undefined,
      ...llmConfig,
      apiKey: azureOptions.azureOpenAIApiKey,
      useResponsesApi: llmConfig.useResponsesApi ?? false,
      firstPartyEndpoint: llmConfig.firstPartyEndpoint ?? false,
      reasoning: llmConfig.reasoning ?? (preservesApiMode ? sourceOptions?.reasoning : undefined),
      modelKwargs: {
        ...(preservesApiMode ? sourceKwargs : undefined),
        ...inheritedTokenLimit,
        ...llmConfig.modelKwargs,
        model: llmConfig.modelKwargs?.model ?? llmConfig.model,
      },
      configuration: sourceOptions
        ? { ...sourceOptions.configuration, ...configOptions }
        : configOptions,
    },
  };
}

/**
 * Resolves a summarization provider string (which may be a custom-endpoint name
 * like "Ollama") into the SDK-recognized provider and any client-option
 * overrides required to talk to that endpoint.
 *
 * Without this step, a `summarization.provider: "Ollama"` entry in
 * `librechat.yaml` flows verbatim to the agents SDK, which only knows a fixed
 * set of provider names and throws "Unsupported LLM provider: Ollama".
 */
function resolveSummarizationProvider(
  rawProvider: string,
  appConfig: AppConfig | undefined,
  headerContext: { user?: IUser; tenantId?: string; requestBody?: t.RequestBody },
  target: {
    model?: string;
    parameters?: SummarizationConfig['parameters'];
    agentProvider?: string;
  } = {},
): {
  provider: string;
  clientOverrides?: SummarizationClientOverrides;
} {
  if (!appConfig || !isNonEmptyString(rawProvider)) {
    return { provider: rawProvider };
  }
  try {
    const { overrideProvider, customEndpointConfig } = getProviderConfig({
      provider: rawProvider,
      appConfig,
    });
    if (!customEndpointConfig) {
      return {
        provider: overrideProvider,
        clientOverrides: resolveBuiltInClientOverrides(overrideProvider, target, appConfig),
      };
    }
    const rawApiKey = customEndpointConfig.apiKey ?? '';
    const rawBaseURL = customEndpointConfig.baseURL ?? '';
    /**
     * User-provided credentials require an async DB lookup and expiry checks
     * that are out of scope here. Keep the raw provider so the SDK surfaces
     * a clear "Unsupported LLM provider" error rather than silently
     * remapping to `openAI` and routing summaries to the default backend.
     * Callers wanting user-provided summarization against a non-agent
     * endpoint must hit the same endpoint as the agent (handled upstream).
     */
    if (isUserProvided(rawApiKey) || isUserProvided(rawBaseURL)) {
      return { provider: rawProvider };
    }
    const apiKey = extractEnvVariable(rawApiKey);
    const baseURL = extractEnvVariable(rawBaseURL);
    /**
     * `extractEnvVariable` leaves any unresolved `${VAR}` placeholder in place
     * — including in the middle of a prefix/suffix string — when the env var
     * is missing. If the value is still broken, keep the raw provider so the
     * SDK errors out loudly instead of forwarding a malformed URL/key.
     */
    if (
      !apiKey ||
      !baseURL ||
      hasUnresolvedPlaceholder(apiKey) ||
      hasUnresolvedPlaceholder(baseURL)
    ) {
      return { provider: rawProvider };
    }
    /**
     * Resolve templated header values (e.g. `${PORTKEY_API_KEY}`,
     * `{{LIBRECHAT_BODY_PARENTMESSAGEID}}`) before handing them to
     * `getOpenAIConfig`, matching the agent main flow where `resolveHeaders`
     * runs on `llmConfig.configuration.defaultHeaders`.
     */
    const resolvedHeaders =
      customEndpointConfig.headers != null
        ? resolveModelHeaders({
            headers: customEndpointConfig.headers as Record<string, string>,
            user: createSafeUser(headerContext.user),
            tenantId: headerContext.tenantId,
            body: headerContext.requestBody,
          })
        : undefined;
    /**
     * Native Anthropic custom endpoints must build their config with the
     * Anthropic client (`/v1/messages`), not `getOpenAIConfig` (which would emit
     * OpenAI-shaped requests). The self-summarize case is handled earlier by
     * `isSameEndpointAsAgent`; this covers summarizing against a *different*
     * Anthropic-native custom endpoint.
     */
    if (customEndpointConfig.provider === EModelEndpoint.anthropic) {
      const { llmConfig } = getAnthropicLLMConfig(apiKey, {
        modelOptions: {},
        proxy: process.env.PROXY ?? undefined,
        reverseProxyUrl: baseURL,
        headers: resolvedHeaders,
        addParams: customEndpointConfig.addParams,
        dropParams: customEndpointConfig.dropParams,
        defaultParams: extractDefaultParams(customEndpointConfig.customParams?.paramDefinitions),
      });
      const { apiKey: resolvedApiKey, ...llmConfigOverrides } = llmConfig as Record<
        string,
        unknown
      >;
      const clientOverrides: SummarizationClientOverrides = { ...llmConfigOverrides };
      if (typeof resolvedApiKey === 'string') {
        clientOverrides.apiKey = resolvedApiKey;
      }
      /** Strip the default model so the user-supplied `summarization.model` wins. */
      delete clientOverrides.model;
      delete clientOverrides.modelName;
      return { provider: Providers.ANTHROPIC, clientOverrides };
    }

    /**
     * Run the endpoint config through `getOpenAIConfig` so summarization
     * inherits the same `headers`, `defaultQuery`, `addParams`/`dropParams`,
     * and `customParams` transforms that `initializeCustom` applies for the
     * main agent flow. Without this, summarization drops endpoint-specific
     * behavior (e.g. Anthropic/Google param transforms, required headers)
     * that the main agent relied on. `proxy` is forwarded so outbound proxy
     * dispatchers (`PROXY` env var) apply to cross-endpoint summarization.
     */
    const {
      llmConfig,
      configOptions,
      provider: detectedProvider,
    } = getOpenAIConfig(
      apiKey,
      {
        reverseProxyUrl: baseURL,
        proxy: process.env.PROXY ?? null,
        headers: resolvedHeaders,
        addParams: customEndpointConfig.addParams,
        dropParams: customEndpointConfig.dropParams,
        customParams: customEndpointConfig.customParams,
        directEndpoint: customEndpointConfig.directEndpoint,
        transportTimeouts: resolveModelTransportTimeouts(appConfig?.endpoints?.agents),
      },
      rawProvider,
    );
    const { apiKey: resolvedApiKey, ...llmConfigOverrides } = llmConfig;
    const clientOverrides: SummarizationClientOverrides = { ...llmConfigOverrides };
    if (typeof resolvedApiKey === 'string') {
      clientOverrides.apiKey = resolvedApiKey;
    }
    if (configOptions) {
      clientOverrides.configuration = configOptions;
    }
    /**
     * `model`/`modelName` on `llmConfig` default to whatever `getOpenAIConfig`
     * produces from empty modelOptions. Strip them so the user-supplied
     * `summarization.model` wins.
     */
    delete clientOverrides.model;
    delete clientOverrides.modelName;
    /**
     * `getOpenAIConfig` detects OpenRouter from the resolved `baseURL`, which
     * `getProviderConfig` cannot do for an endpoint whose config name isn't
     * `openrouter` — it reports `openAI` for those. Prefer the detected
     * provider so a cross-endpoint summarizer builds the same client the main
     * agent flow builds for that endpoint (`initializeAgent` applies the same
     * precedence).
     */
    const provider = detectedProvider ?? overrideProvider;
    /**
     * On the agent's provider the SDK layers these over the agent's own client options, so this
     * different endpoint replaces the agent's API mode, first-party declaration, reasoning and
     * request kwargs (an Azure Astra agent's Responses routing, for one) instead of inheriting them.
     */
    if (provider === target.agentProvider) {
      clientOverrides.useResponsesApi ??= false;
      clientOverrides.firstPartyEndpoint ??= false;
      clientOverrides.modelKwargs ??= {};
      clientOverrides.reasoning ??= undefined;
    }
    return { provider, clientOverrides };
  } catch (error) {
    logger.warn(
      `[resolveSummarizationProvider] failed to resolve "${rawProvider}"; falling back to raw provider`,
      error,
    );
    return { provider: rawProvider };
  }
}

/** Shapes a SummarizationConfig into the format expected by AgentInputs. */
function shapeSummarizationConfig(
  config: SummarizationConfig | undefined,
  fallbackProvider: string,
  fallbackModel: string | undefined,
  appConfig: AppConfig | undefined,
  agentEndpoint: string | undefined,
  headerContext: { user?: IUser; tenantId?: string; requestBody?: t.RequestBody },
  agent?: RunAgent,
) {
  const rawProvider = config?.provider ?? fallbackProvider;
  /**
   * When the summarization provider resolves to the same custom endpoint as
   * the main agent, skip client-option overrides. The SDK's self-summarize
   * path will reuse `agentContext.clientOptions` as-is, preserving any
   * request-resolved dynamic headers, fetch/proxy options, and other state
   * that `getOpenAIConfig` produced from raw yaml config does not capture.
   */
  const isSameEndpointAsAgent =
    agentEndpoint != null &&
    isNonEmptyString(rawProvider) &&
    normalizeEndpointName(rawProvider) === normalizeEndpointName(agentEndpoint);

  const model = config?.model ?? fallbackModel;
  const targetsAzure =
    rawProvider === EModelEndpoint.azureOpenAI ||
    (agentEndpoint === EModelEndpoint.azureOpenAI && config?.provider == null);
  const userParameters = expandSummarizationTransport(config?.parameters, targetsAzure);

  const selfAzureModel =
    agentEndpoint === EModelEndpoint.azureOpenAI &&
    (model === fallbackModel || model === agent?.model);
  const transportOverrides = summarizationTransportOverrides(userParameters);
  const overridesAzureTransport =
    transportOverrides.baseURL != null ||
    transportOverrides.apiKey != null ||
    typeof userParameters?.useResponsesApi === 'boolean';
  const selectsAzureDeployment =
    targetsAzure &&
    isNonEmptyString(model) &&
    config?.enabled !== false &&
    (agentEndpoint !== EModelEndpoint.azureOpenAI ||
      model !== fallbackModel ||
      overridesAzureTransport);
  const azureOverrides = selectsAzureDeployment
    ? resolveAzureSummarization(
        model,
        appConfig,
        userParameters,
        headerContext,
        selfAzureModel ? agent : undefined,
      )
    : undefined;
  const selectsOpenAIForAzureAgent =
    agentEndpoint === EModelEndpoint.azureOpenAI &&
    config?.provider === EModelEndpoint.openAI &&
    config.enabled !== false &&
    isNonEmptyString(model);
  const openAIOverrides = selectsOpenAIForAzureAgent
    ? resolveOpenAISummarization(model, appConfig, userParameters, headerContext)
    : undefined;
  /**
   * A target resolved here is not handed to another client when resolution fails. Azure Responses
   * shares the `openAI` provider, so the SDK would summarize an OpenAI target through the agent's
   * Azure resource, and would send an unmapped Azure model name where a deployment belongs.
   */
  const targetUnavailable =
    (selectsAzureDeployment && azureOverrides == null) ||
    (selectsOpenAIForAzureAgent && openAIOverrides == null);
  const { provider, clientOverrides } =
    openAIOverrides ??
    azureOverrides ??
    (isSameEndpointAsAgent
      ? { provider: fallbackProvider, clientOverrides: undefined }
      : resolveSummarizationProvider(rawProvider, appConfig, headerContext, {
          model,
          parameters: userParameters,
          agentProvider: fallbackProvider,
        }));
  const trigger =
    config?.trigger?.type && typeof config?.trigger?.value === 'number'
      ? { type: config.trigger.type, value: config.trigger.value }
      : undefined;

  /**
   * Custom-endpoint overrides are merged into `parameters` so the SDK's
   * `buildSummarizationClientConfig` spreads them onto the summarization
   * client options. Azure self-summaries with explicit transport overrides also resolve here,
   * using the initialized agent's identity and credentials instead of a new configuration lookup.
   *
   * Order matters: `clientOverrides` supplies endpoint defaults (baseURL,
   * apiKey, headers, transforms), then explicit user `summarization.parameters`
   * are spread on top so settings like `streaming: false` still win over
   * `getOpenAIConfig`'s defaults. `configuration` is deep-merged so a user
   * adding e.g. `configuration.defaultQuery` keeps the resolved `baseURL`
   * and `defaultHeaders` rather than replacing the whole object.
   */
  const mergedParameters =
    clientOverrides != null
      ? mergeParameters(
          clientOverrides,
          userParameters,
          azureOverrides != null || openAIOverrides != null,
        )
      : userParameters;
  /** Placed first so an explicit user `modelKwargs` still replaces the agent's wholesale. */
  const modelKwargs =
    provider === fallbackProvider
      ? summarizationModelKwargs(agentModelKwargs(agent?.model_parameters), fallbackModel, model)
      : undefined;
  /**
   * A scalar `reasoning_effort` — the only reasoning shape the yaml schema
   * accepts — is inert as a client option and leaves the summarizer running at
   * whatever effort the main agent resolved. Translate it the way the main
   * flow's `getOpenAIConfig` would for the summarization target.
   */
  let parameters = resolveReasoningParams({
    provider,
    model,
    parameters: modelKwargs != null ? { modelKwargs, ...mergedParameters } : mergedParameters,
  });

  /** The SDK sets maxTokens for this cap, but LangChain spreads modelKwargs after it. */
  const parameterTokenCap = userParameters?.maxSummaryTokens;
  const summaryTokenCap =
    typeof parameterTokenCap === 'number' && parameterTokenCap > 0
      ? parameterTokenCap
      : config?.maxSummaryTokens;
  const inheritedKwargs =
    provider === fallbackProvider ? agentModelKwargs(agent?.model_parameters) : undefined;
  const effectiveKwargs = isPlainObject(parameters?.modelKwargs)
    ? parameters.modelKwargs
    : inheritedKwargs;
  if (typeof summaryTokenCap === 'number' && effectiveKwargs != null) {
    const {
      max_tokens: _maxTokens,
      max_completion_tokens: _maxCompletionTokens,
      max_output_tokens: _maxOutputTokens,
      ...kwargs
    } = effectiveKwargs;
    parameters = { ...parameters, modelKwargs: kwargs };
  }

  return {
    enabled:
      !targetUnavailable &&
      config?.enabled !== false &&
      isNonEmptyString(provider) &&
      isNonEmptyString(model),
    config: {
      trigger,
      provider,
      model,
      parameters,
      prompt: config?.prompt,
      updatePrompt: config?.updatePrompt,
      reserveRatio: config?.reserveRatio,
      maxSummaryTokens: config?.maxSummaryTokens,
      retainRecent: config?.retainRecent,
    } satisfies AgentSummarizationConfig,
    contextPruning: config?.contextPruning as ContextPruningConfig | undefined,
    reserveRatio: config?.reserveRatio,
  };
}

/**
 * Below this context budget a summarization cycle cannot make progress: the
 * summary allocation rounds down to a handful of tokens, the rewritten history
 * still overflows, and the graph re-triggers summarization on every step until
 * the recursion limit aborts the run. Dozens of wasted LLM calls surfaced to
 * the user as an opaque LangGraph error. Falling back to plain pruning instead
 * either fits the request or fails fast with the actionable `empty_messages`
 * token-budget breakdown. Matches the floor `initializeAgent` applies when the
 * user supplies no override.
 */
const MIN_SUMMARIZATION_CONTEXT_TOKENS = 1024;

/**
 * Applies `reserveRatio` against the pre-ratio base context budget, falling
 * back to the pre-computed `maxContextTokens` from initializeAgent.
 */
function computeEffectiveMaxContextTokens(
  reserveRatio: number | undefined,
  baseContextTokens: number | undefined,
  maxContextTokens: number | undefined,
): number | undefined {
  if (reserveRatio == null || reserveRatio <= 0 || reserveRatio >= 1 || baseContextTokens == null) {
    return maxContextTokens;
  }
  const ratioComputed = Math.max(1024, Math.round(baseContextTokens * (1 - reserveRatio)));
  return Math.min(maxContextTokens ?? ratioComputed, ratioComputed);
}

type CallbackClientOptions = {
  callbacks?: Callbacks;
  fallbacks?: FallbackConfig[];
};

type RunModelCallback = ModelBoundChatModelCallback | ModelErrorTrackerCallback;

/**
 * Installs run-stable callbacks on the model client itself. Subagent child
 * graphs intentionally replace invocation callbacks with their own event
 * forwarders, while intrinsic client callbacks survive root, child, detached,
 * and summarization calls.
 */
function withModelCallbacks<T extends object>(
  options: T,
  modelCallbacks: readonly RunModelCallback[] | undefined,
): T {
  if (!modelCallbacks?.length) {
    return options;
  }

  const callbackOptions = options as T & CallbackClientOptions;
  const existingCallbacks = callbackOptions.callbacks;
  /** The domain callback consumes only the model-bound message prefix of
   *  LangChain's callback arguments; the trailing run metadata is ignored. */
  const modelHandlers = modelCallbacks as unknown as readonly CallbackHandlerMethods[];
  let callbacks: CallbackClientOptions['callbacks'];
  if (existingCallbacks == null || Array.isArray(existingCallbacks)) {
    callbacks = [...(existingCallbacks ?? []), ...modelHandlers];
  } else {
    const manager = existingCallbacks.copy();
    for (const callback of modelHandlers) {
      manager.addHandler(ensureHandler(callback), true);
    }
    callbacks = manager;
  }
  const withCallbacks = {
    ...callbackOptions,
    callbacks,
  } as T & CallbackClientOptions;

  if (Array.isArray(callbackOptions.fallbacks)) {
    withCallbacks.fallbacks = callbackOptions.fallbacks.map((fallback) => ({
      ...fallback,
      clientOptions: withModelCallbacks({ ...(fallback.clientOptions ?? {}) }, modelCallbacks),
    }));
  }

  return withCallbacks;
}

/** Identifier for the self-spawn subagent (reuses parent's AgentInputs in an isolated child graph). */
const SELF_SUBAGENT_TYPE = 'self';

interface SubagentBuildState {
  configCount: number;
  rootAgentIds: string[];
}

function countSubagentConfig(state: SubagentBuildState): void {
  state.configCount += 1;
  if (state.configCount > MAX_SUBAGENT_RUN_CONFIGS) {
    logger.warn('[createRun] Subagent run configuration limit exceeded', {
      expandedConfigCount: state.configCount,
      maxSubagentRunConfigs: MAX_SUBAGENT_RUN_CONFIGS,
      rootAgentIds: state.rootAgentIds,
    });
    throw new Error(
      `Subagent run configuration exceeds the maximum of ${MAX_SUBAGENT_RUN_CONFIGS} expanded entries.`,
    );
  }
}

function assertSubagentDepth(depth: number, agentId: string): void {
  if (depth > MAX_SUBAGENT_DEPTH) {
    logger.warn('[createRun] Subagent graph depth limit exceeded', {
      agentId,
      depth,
      maxSubagentDepth: MAX_SUBAGENT_DEPTH,
    });
    throw new Error(
      `Subagent graph exceeds the maximum depth of ${MAX_SUBAGENT_DEPTH} at agent ${agentId}.`,
    );
  }
}

function createLazySubagentConfig(
  child: LazySubagentAgent,
  toInput: (child: RunAgent, opts?: { isSubagent?: boolean }) => AgentInputs,
  agentsEConfig: Partial<TAgentsEndpoint> | undefined,
  ancestors: Set<string>,
  depth: number,
  prebuiltGraphInputs?: ReadonlyMap<string, AgentInputs>,
  onResolvedAgent?: (agent: RunAgent) => void,
): SubagentConfig {
  const resolveInputs = async (context: SubagentResolveContext): Promise<AgentInputs> => {
    if (context.signal.aborted) {
      throw context.signal.reason ?? new Error('Subagent resolution was aborted.');
    }
    const resolvedChild = await child.resolve(context);
    if (context.signal.aborted) {
      throw context.signal.reason ?? new Error('Subagent resolution was aborted.');
    }
    onResolvedAgent?.(resolvedChild);
    /** Graph members initialized with the child may run on its per-call machine. */
    for (const graph of resolvedChild.subagentGraphConfigs ?? []) {
      for (const member of graph.memberConfigs) {
        onResolvedAgent?.(member);
      }
    }
    const childInputs = buildIsolatedAgentInputs(resolvedChild, toInput);
    const resolutionState: SubagentBuildState = {
      configCount: 1,
      rootAgentIds: [resolvedChild.id],
    };
    const grandchildConfigs = buildSubagentConfigs(
      resolvedChild,
      childInputs,
      toInput,
      resolutionState,
      agentsEConfig,
      ancestors,
      depth,
      prebuiltGraphInputs,
      false,
      onResolvedAgent,
    );
    if (grandchildConfigs.length > 0) {
      childInputs.subagentConfigs = grandchildConfigs;
    }
    return childInputs;
  };
  const config: SubagentConfig = {
    type: child.id,
    name: child.name ?? child.id,
    description:
      child.description ??
      `Delegate a subtask to the ${child.name ?? child.id} agent in an isolated context.`,
    configId: child.configId,
    allowNested: true,
    maxTurns: resolveSubagentMaxTurns(agentsEConfig, child),
    resolveAgentInputs: (context) =>
      child.settle == null
        ? resolveInputs(context)
        : child.settle(context, () => resolveInputs(context)),
  };
  /** Assigned rather than spread so the field typechecks against SDKs that predate it. */
  return child.subagentHostArgs == null
    ? config
    : Object.assign(config, { hostArgs: child.subagentHostArgs });
}

function enqueueSubagentChildren(
  agent: SubagentTreeNode,
  pending: Array<SubagentTreeNode | null | undefined>,
  visited: ReadonlySet<string>,
  includeLazyDescriptors = true,
  includeCapabilityMetadata = true,
): void {
  for (const child of agent.subagentAgentConfigs ?? []) {
    if (child != null && !visited.has(child.id)) {
      pending.push(child);
    }
  }
  if (includeLazyDescriptors) {
    for (const child of agent.lazySubagentConfigs ?? []) {
      if (!visited.has(child.id)) {
        pending.push(child);
      }
    }
  }
  if (includeCapabilityMetadata) {
    for (const member of agent.subagentGraphMemberMetadata ?? []) {
      if (!visited.has(member.id)) {
        pending.push(member);
      }
    }
  }
  for (const graph of agent.subagentGraphConfigs ?? []) {
    for (const member of graph.memberConfigs) {
      if (member != null && !visited.has(member.id)) {
        pending.push(member);
      }
    }
  }
}

/**
 * Collect MCP key-spelling aliases from every eagerly known agent in the run.
 * Lazy descriptors are revisited when they resolve, because initializing MCP
 * tools solely to discover aliases would defeat lazy loading.
 */
export function collectRunMCPToolAliases(
  agents: Array<RunAgent | SubagentTreeNode | null | undefined>,
): MCPToolAlias[] {
  const aliases: MCPToolAlias[] = [];
  const seenAliases = new Set<string>();
  const visited = new Set<string>();
  const pending: Array<RunAgent | SubagentTreeNode | null | undefined> = [...agents];

  for (let index = 0; index < pending.length; index++) {
    const agent = pending[index];
    if (agent == null) {
      continue;
    }
    for (const alias of agent.mcpToolAliases ?? []) {
      const key = `${alias.name}\u0000${alias.aliasName}`;
      if (!seenAliases.has(key)) {
        seenAliases.add(key);
        aliases.push(alias);
      }
    }
    if (visited.has(agent.id)) {
      // The same saved agent can appear as both a lazy descriptor and a
      // pre-initialized graph member. Keep traversing each representation so
      // its unique children stay reachable, but avoid descending forever.
      enqueueSubagentChildren(agent, pending, visited);
      continue;
    }
    visited.add(agent.id);
    enqueueSubagentChildren(agent, pending, visited);
  }
  return aliases;
}

/**
 * Recursive any-true check across the agent tree: returns `true` if this
 * agent or any subagent (transitively) has the per-agent codeenv gate
 * enabled.
 *
 * The SDK's tool-output reference registry is shared across every
 * `ToolNode` compiled from the run's graph (parent + every subagent
 * alike), so a single subagent with `bash_tool` registered is enough to
 * make `RunConfig.toolOutputReferences` worth activating for the whole
 * run — without it, the subagent's `{{tool<idx>turn<turn>}}`
 * placeholders would pass through to the shell unsubstituted.
 *
 * Cycle-safe via a `visited` set. The bash tool description itself is
 * still gated per-agent in `initializeAgent`, so only agents that actually
 * have bash registered learn the `{{…}}` syntax — broadening the run-level
 * registry gate doesn't broaden the model-facing surface.
 */
function anyAgentHasCodeEnv(agents: RunAgent[]): boolean {
  const visited = new Set<string>();
  const pending: SubagentTreeNode[] = [...agents];

  for (let index = 0; index < pending.length; index++) {
    const agent = pending[index];
    if (visited.has(agent.id)) {
      continue;
    }
    visited.add(agent.id);
    if (agent.codeEnvAvailable === true || (agent.codeExecutionChoices?.length ?? 0) > 0) {
      return true;
    }
    enqueueSubagentChildren(agent, pending, visited);
  }
  return false;
}

/**
 * Whether a single agent's tool surface includes the `ask_user_question` tool, in any
 * of the three places a tool can live on a `RunAgent`: loaded instances (`tools`), the
 * schema-only registry (`toolRegistry`), or serialized definitions (`toolDefinitions`).
 * Checked against TOP-LEVEL agents only (not subagents — the tool is stripped from
 * child configs in `buildAgentInput`, since a child graph executing outside the parent
 * run's stream cannot pause the parent).
 *
 * Exported for AgentClient's pre-turn orphan-checkpoint prune gate: the prune must
 * fire whenever THIS turn may attach a checkpointer, which since the ask tool is no
 * longer coupled to `toolApproval.enabled` includes ask-capable runs.
 */
export function agentRequestsAskUserQuestion(agent: {
  tools?: unknown[];
  toolRegistry?: Map<string, unknown>;
  toolDefinitions?: Array<{ name: string }>;
}): boolean {
  return (
    agent.tools?.some(
      (tool) => (tool as { name?: string } | undefined)?.name === ASK_USER_QUESTION_TOOL_NAME,
    ) === true ||
    agent.toolRegistry?.has(ASK_USER_QUESTION_TOOL_NAME) === true ||
    agent.toolDefinitions?.some((def) => def.name === ASK_USER_QUESTION_TOOL_NAME) === true
  );
}

/**
 * Whether the admin tool filter (`includedTools` allowlist, else `filteredTools`
 * exclude list — same precedence as `loadAndFormatTools`) disables
 * `ask_user_question`. Enforced at RUN BUILD, not just in the tools-dialog listing:
 * agents saved before an admin filtered the tool out would otherwise keep exposing
 * it to the model, attaching checkpointers, and pausing runs — for a run-pausing
 * tool the filter must be an actual kill switch.
 */
export function isAskUserQuestionAdminDisabled(appConfig?: AppConfig): boolean {
  const included = appConfig?.includedTools;
  if (included != null && included.length > 0) {
    return !included.includes(ASK_USER_QUESTION_TOOL_NAME);
  }
  return appConfig?.filteredTools?.includes(ASK_USER_QUESTION_TOOL_NAME) === true;
}

/**
 * Whether any agent reachable in the run — primary, handoff/parallel, or a
 * nested subagent — opts into cross-turn `reasoning_content` reconstruction.
 * Walks `subagentAgentConfigs` like {@link anyAgentHasCodeEnv}, since an
 * opted-in custom endpoint may appear only as a (possibly pruned) subagent.
 */
export function anyAgentReplaysReasoningContent(
  agents: Array<RunAgent | null | undefined>,
): boolean {
  const visited = new Set<string>();
  const pending: Array<SubagentTreeNode | null | undefined> = [...agents];

  for (let index = 0; index < pending.length; index++) {
    const agent = pending[index];
    if (agent == null || visited.has(agent.id)) {
      continue;
    }
    visited.add(agent.id);
    if (shouldReplayReasoningContent(agent)) {
      return true;
    }
    enqueueSubagentChildren(agent, pending, visited);
  }
  return false;
}

/**
 * Builds SubagentConfig entries for an agent: optional self-spawn plus any
 * explicit eager children and inert lazy descriptors. Returns an empty array
 * when subagents are disabled or no spawn targets are available.
 */
function buildIsolatedAgentInputs(
  child: RunAgent,
  toInput: (agent: RunAgent, opts?: { isSubagent?: boolean }) => AgentInputs,
): AgentInputs {
  const childInputs = toInput(child, { isSubagent: true });
  if ((child.backgroundToolNames?.length ?? 0) > 0) {
    childInputs.toolDefinitions = stripBackgroundFromToolDefinitions(
      childInputs.toolDefinitions,
      child.backgroundToolNames,
    );
    childInputs.toolRegistry = stripBackgroundFromToolRegistry(
      childInputs.toolRegistry,
      child.backgroundToolNames,
    );
  }
  if ((child.intentToolNames?.length ?? 0) > 0) {
    childInputs.toolDefinitions = stripIntentFromToolDefinitions(
      childInputs.toolDefinitions,
      child.intentToolNames,
    );
    childInputs.toolRegistry = stripIntentFromToolRegistry(
      childInputs.toolRegistry,
      child.intentToolNames,
    );
  }
  return childInputs;
}

function buildSubagentConfigs(
  agent: RunAgent,
  agentInput: AgentInputs,
  toInput: (child: RunAgent, opts?: { isSubagent?: boolean }) => AgentInputs,
  state: SubagentBuildState,
  agentsEConfig: Partial<TAgentsEndpoint> | undefined,
  ancestors: Set<string> = new Set(),
  depth = 0,
  prebuiltGraphInputs?: ReadonlyMap<string, AgentInputs>,
  detachedTasksEnabled = false,
  onResolvedAgent?: (agent: RunAgent) => void,
): SubagentConfigEntry[] {
  if (!agent.subagents?.enabled) {
    return [];
  }

  const configs: SubagentConfigEntry[] = [];
  const allowSelf = agent.subagents.allowSelf !== false;

  if (allowSelf) {
    const selfName = agentInput.name ?? agent.name ?? 'self';
    countSubagentConfig(state);
    /**
     * Self-spawn reuses the parent's AgentInputs. When the parent has
     * background or host-injected intent tools, provide a sanitized copy so
     * the isolated child — which runs the direct/child-graph path rather
     * than the host interceptors — doesn't advertise `run_in_background` /
     * `check_background_task` or an injected `intent` param its direct tool
     * invocations would forward to tools that never declared it. The
     * resolver keeps a provided `agentInputs` even with `self: true`.
     */
    const hasBackground = detachedTasksEnabled || (agent.backgroundToolNames?.length ?? 0) > 0;
    const hasInjectedIntent = (agent.intentToolNames?.length ?? 0) > 0;
    const sanitizedToolRegistry = stripIntentFromToolRegistry(
      stripBackgroundFromToolRegistry(agentInput.toolRegistry, agent.backgroundToolNames),
      agent.intentToolNames,
    );
    configs.push({
      self: true,
      type: SELF_SUBAGENT_TYPE,
      name: selfName,
      description: `Spawn ${selfName} in an isolated context to handle a focused subtask. Verbose tool output stays in the child's context; only a summary returns.`,
      /** Self-spawn reuses the parent's config, so mirror the parent's recursion limit. */
      maxTurns: resolveSubagentMaxTurns(agentsEConfig, agent),
      ...(hasBackground || hasInjectedIntent
        ? {
            agentInputs: {
              ...agentInput,
              toolDefinitions: stripIntentFromToolDefinitions(
                stripBackgroundFromToolDefinitions(
                  agentInput.toolDefinitions,
                  agent.backgroundToolNames,
                ),
                agent.intentToolNames,
              ),
              /** `registerBackgroundTaskTool` mutates the parent registry after
               * configs are built. Detach its self-child snapshot so the host
               * poll tool cannot appear there through that shared Map. */
              toolRegistry:
                detachedTasksEnabled && sanitizedToolRegistry != null
                  ? new Map(sanitizedToolRegistry)
                  : sanitizedToolRegistry,
            },
          }
        : {}),
    });
  }

  /** Cycle-safety: include the current agent in `ancestors` before
   *  descending into children so a `A → B → A` configuration stops at
   *  the second encounter of A rather than recursing forever. Skip
   *  `A → A` too (already guarded) and anything that would re-enter
   *  an ancestor. */
  const nextAncestors = new Set(ancestors);
  nextAncestors.add(agent.id);

  for (const child of agent.subagentAgentConfigs ?? []) {
    if (!child?.id || child.id === agent.id) {
      continue;
    }
    if (ancestors.has(child.id)) {
      continue;
    }
    const childDepth = depth + 1;
    assertSubagentDepth(childDepth, child.id);
    countSubagentConfig(state);
    const childInputs = buildIsolatedAgentInputs(child, toInput);
    /**
     * Recursively resolve the child's own spawn targets so multi-level
     * delegation (A → B → C) works. Without this, a child whose own
     * `subagents.enabled` is true loses every explicit target when
     * invoked as a subagent — only the top-level loop attaches
     * `subagentConfigs`, and that only runs for the outer agents in
     * `agents[]`. Cycle-safe via `nextAncestors`.
     */
    const grandchildConfigs = buildSubagentConfigs(
      child,
      childInputs,
      toInput,
      state,
      agentsEConfig,
      nextAncestors,
      childDepth,
      prebuiltGraphInputs,
      detachedTasksEnabled,
      onResolvedAgent,
    );
    if (grandchildConfigs.length > 0) {
      childInputs.subagentConfigs = grandchildConfigs;
    }
    configs.push({
      type: child.id,
      name: child.name ?? child.id,
      description:
        child.description ??
        `Delegate a subtask to the ${child.name ?? child.id} agent in an isolated context.`,
      agentInputs: childInputs,
      /** Preserve the child's resolved subagent configs when the SDK builds its isolated graph. */
      allowNested: true,
      /** Honor each child agent's own resolved recursion limit. */
      maxTurns: resolveSubagentMaxTurns(agentsEConfig, child),
    });
  }

  for (const child of agent.lazySubagentConfigs ?? []) {
    if (!child.id || child.id === agent.id || ancestors.has(child.id)) {
      continue;
    }
    const childDepth = depth + 1;
    assertSubagentDepth(childDepth, child.id);
    countSubagentConfig(state);
    configs.push(
      createLazySubagentConfig(
        child,
        toInput,
        agentsEConfig,
        nextAncestors,
        childDepth,
        prebuiltGraphInputs,
        onResolvedAgent,
      ),
    );
  }

  for (const { definition, memberConfigs } of agent.subagentGraphConfigs ?? []) {
    if (memberConfigs.length === 0) {
      continue;
    }
    countSubagentConfig(state);
    const maxTurns = Math.min(
      ...memberConfigs.map((member) => resolveSubagentMaxTurns(agentsEConfig, member)),
    );
    configs.push({
      kind: 'graph',
      type: definition.type,
      name: definition.name,
      description: definition.description,
      agents: memberConfigs.map(
        (member) =>
          prebuiltGraphInputs?.get(member.id) ?? buildIsolatedAgentInputs(member, toInput),
      ),
      /**
       * The persisted API accepts `excludeResults: false` as the explicit
       * form of the default. The SDK reserves this field for prompted edges
       * and rejects any defined value when no prompt exists, so erase the
       * no-op false value at the host boundary.
       */
      edges: definition.edges.map((edge) => {
        if (edge.excludeResults !== false) {
          return edge;
        }
        const { excludeResults: _excludeResults, ...normalizedEdge } = edge;
        return normalizedEdge;
      }),
      entryAgentId: definition.entry_agent_id,
      resultAgentId: definition.result_agent_id,
      maxTurns,
    });
  }

  return configs;
}

/**
 * Creates a new Run instance with custom handlers and configuration.
 *
 * @param options - The options for creating the Run instance.
 * @param options.agents - The agents for this run.
 * @param options.signal - The signal for this run.
 * @param options.runId - Optional run ID; otherwise, a new run ID will be generated.
 * @param options.customHandlers - Custom event handlers.
 * @param options.streaming - Whether to use streaming.
 * @param options.streamUsage - Whether to stream usage information.
 * @param options.messages - Optional message history to extract discovered tools from.
 *   When provided, tools that were previously discovered via tool_search will have
 *   their defer_loading overridden to false, preventing redundant re-discovery.
 * @returns {Promise<Run<IState>>} A promise that resolves to a new Run instance.
 */
/** The caller's trace context over run-derived defaults for the fields it left unset. */
function resolveRunTraceContext({
  agents,
  conversationId,
  requestBody,
  traceContext,
}: {
  agents: RunAgent[];
  conversationId?: string;
  requestBody?: t.RequestBody;
  traceContext?: LangfuseTraceContext;
}): LangfuseTraceContext {
  const primaryAgent = agents[0];
  return {
    ...traceContext,
    conversationId: traceContext?.conversationId ?? conversationId ?? requestBody?.conversationId,
    provider: traceContext?.provider ?? primaryAgent?.provider,
    model: traceContext?.model ?? primaryAgent?.model_parameters?.model ?? primaryAgent?.model,
  };
}

export async function createRun({
  runId,
  signal,
  conversationId,
  agents,
  messages,
  discoveredToolNames,
  requestBody,
  codeApprovalMode: requestedCodeApprovalMode,
  user,
  tenantId,
  centralTraceExportEnabled,
  isTemporary,
  traceContext,
  tokenCounter,
  customHandlers,
  indexTokenCountMap,
  initialSessions,
  summarizationConfig,
  summarizeOnly = false,
  compactionSemanticIndex,
  initialSummary,
  modelCallbacks,
  clientToolNames,
  calibrationRatio,
  fadingTier,
  fadingTiers,
  appConfig,
  subagentUsageSink,
  subagentTasks,
  runFiles,
  steering,
  activityLabel,
  activityPhase,
  eventActorCheckpointing = false,
  hitlCapable = false,
  resolvedToolApprovalHooks,
  toolApprovalStorage,
  reviewedToolApprovals,
  scheduledMCPExecution,
  recordScheduledMCPDenial,
  toolApprovalAllows,
  toolInputValidationErrors,
  sessionStartSource,
  streaming = true,
  streamUsage = true,
}: {
  agents: RunAgent[];
  signal: AbortSignal;
  runId?: string;
  /** Conversation-stable identity, used by the e2e run hook to tell a resumed
   *  run apart from a fresh attempt (a resume carries no messages). */
  conversationId?: string;
  streaming?: boolean;
  streamUsage?: boolean;
  requestBody?: t.RequestBody;
  codeApprovalMode?: CodeApprovalMode;
  user?: IUser;
  tenantId?: string;
  /**
   * Defaults to true. Set false to suppress central Langfuse export for this
   * run. Tenant fanout can still export when tenant routing is available.
   */
  centralTraceExportEnabled?: boolean;
  /** A temporary chat's run is not traced unless `langfuse.trace.temporaryChats` opts in. */
  isTemporary?: boolean;
  /**
   * Exact tool names the owner approved for the rest of this conversation, read from the
   * stored conversation (never from the request body). Honored only when
   * `toolApproval.allowAlways` is on; admin `deny`/`ask` rules and hooks still win.
   */
  toolApprovalAllows?: readonly string[];
  /**
   * Request values the deployment may export as Langfuse trace metadata
   * (`langfuse.trace.conversationMetadataFields`). The conversation id,
   * provider, and model default from the run itself.
   */
  traceContext?: LangfuseTraceContext;
  /** Message history for extracting previously discovered tools */
  messages?: BaseMessage[];
  /**
   * Pre-discovered deferred-tool names to force-load directly, bypassing message
   * extraction. The HITL resume path rebuilds the graph with `messages: []` (state
   * comes from the durable checkpoint), so the in-turn `tool_search` results that
   * would normally mark a deferred tool discovered aren't present — without this the
   * paused tool's schema would be absent from the rebuilt model binding. Captured at
   * pause from canonical run state (with message parsing for older SDK releases) and
   * replayed here. Merged with (not replacing) names extracted from `messages`.
   */
  discoveredToolNames?: string[];
  summarizationConfig?: SummarizationConfig;
  /**
   * Manual compaction: the primary agent summarizes the history outright and
   * the run ends after the summary without a model call. Applies to the
   * primary agent only; a chained or delegated agent never runs.
   */
  summarizeOnly?: boolean;
  /** Bounded, source-addressed navigation guidance derived with provider messages. */
  compactionSemanticIndex?: CompactionSemanticIndex;
  /** Cross-run summary from formatAgentMessages, forwarded to AgentContext */
  initialSummary?: { text: string; tokenCount: number };
  /** Model callbacks inherited by root, summary, fallback, and subagent clients. */
  modelCallbacks?: readonly RunModelCallback[];
  /** Caller-executed tools must never run eagerly before handoff is decided. */
  clientToolNames?: ReadonlySet<string>;
  /** Calibration ratio from previous run's contextMeta, seeds the pruner EMA */
  calibrationRatio?: number;
  /**
   * Default agent's latched context-fading tier from the previous run's
   * contextMeta. It seeds the pruner so the provider-only projection of
   * historical tool results keeps the same bytes across runs; graph messages
   * stay canonical. Legacy v1 tiers are not passed to the v2 SDK; it derives
   * them afresh using the current-turn exchange width.
   */
  fadingTier?: (IAgentFadingTier & { v: 2 }) | null;
  /**
   * Latched tiers keyed by agent ID from the previous run's contextMeta, so
   * every agent of a multi-agent run restores its own tier. Same SDK
   * availability as `fadingTier`.
   */
  fadingTiers?: RunFadingTiers | null;
  /**
   * Resolved app config. Used to translate custom-endpoint provider names
   * (e.g. "Ollama") in the summarization config to SDK-recognized providers.
   */
  appConfig?: AppConfig;
  /**
   * Receives per-model-call usage from subagent child runs so hosts can bill
   * them (child graphs execute outside the run's `streamEvents` loop, so
   * their usage never reaches `customHandlers`). Typed structurally — not as
   * `Pick<RunConfig, 'subagentUsageSink'>` — because the field ships in
   * `@librechat/agents` > 3.2.33; older SDK versions ignore it at runtime.
   * Switch to the `RunConfig` pick once the dependency is bumped.
   */
  subagentUsageSink?: (event: SubagentUsageEvent) => void;
  /** Host-owned detached-subagent task store and trusted parent-thread scope. */
  subagentTasks?: SubagentTaskConfig;
  /** Run-scoped file authorization and child context, supplied by the host. */
  runFiles?: RunFileSession;
  /**
   * The run-scoped steer-drain hook (a `PostToolBatch` callback built via
   * `createSteerDrainHook`). Registered on the run's hook registry independent
   * of the tool-approval policy — steering needs neither HITL nor a
   * checkpointer (injection merges via the messages reducer inside the tool
   * node). Only the resumable agents controller passes this; the
   * OpenAI-compatible and Responses controllers have no job/SSE surface.
   */
  steering?: {
    hook: HookCallback<'PostToolBatch'>;
    /**
     * The PreemptBoundary twin of `hook`, built via
     * `createSteerPreemptBoundaryHook` from the same drain closures. Fires
     * when the SDK seals a model stream mid-generation on a preempt request.
     */
    preemptHook?: HookCallback<'PreemptBoundary'>;
    /**
     * Atomically claims queued steers at the SDK's terminal Stop boundary or
     * seals admission so later messages become ordinary follow-up turns.
     */
    terminalHook?: TerminalSteerHook;
    /**
     * Level-triggered O(1) poll over the job's armed preempt requests
     * (`createSteerPreemptPoll`). Threaded into `RunConfig.preemption`, which
     * also makes the SDK reserve recursion-limit headroom for its seals.
     */
    preemption?: StreamPreemption & Partial<Pick<SteerPreemption, 'disable'>>;
  };
  /**
   * Run-scoped tool-batch summary hook (PostToolBatch). Like steering, it
   * registers independently of the approval policy and needs no checkpointer;
   * the hook returns immediately and generates off the critical path.
   */
  activityLabel?: { hook: HookCallback<'PostToolBatch'> };
  /** Run-wide parent phase collector; registered after child batch labels. */
  activityPhase?: { hook: HookCallback<'PostToolBatch'> };
  /** Persist clean terminal checkpoints for an isolated bound-event invocation. */
  eventActorCheckpointing?: boolean;
  /**
   * Whether the caller implements the HITL pause/resume lifecycle (inspects
   * `run.getInterrupt()`, persists a pending action, exposes a resume route). Gates the
   * approval pause and checkpointer: only AgentClient (chat + resume) sets this.
   * All callers still enforce an enabled tool policy; without this flag the SDK
   * blocks `ask` decisions rather than pausing a run with no resume surface.
   */
  hitlCapable?: boolean;
  /**
   * Request-scoped approval hooks already resolved by the scheduled-run admission guard.
   * Reuse them here so a context-aware factory is evaluated exactly once for the run.
   */
  resolvedToolApprovalHooks?: readonly ResolvedToolApprovalHook[];
  toolApprovalStorage?: ToolApprovalGrantStorage;
  reviewedToolApprovals?: ReviewedToolApprovals;
  scheduledMCPExecution?: ScheduleMCPExecution;
  recordScheduledMCPDenial?: (error: ScheduledMCPPolicyError) => Promise<boolean>;
  /** Plugin-hook SessionStart lifecycle source: 'startup' (default) or 'resume' on HITL-rebuild paths. */
  sessionStartSource?: string;
  /** Request-scoped tool input failures consumed by the completion handler. */
  toolInputValidationErrors?: Map<string, ToolInputValidationError>;
} & Pick<
  RunConfig,
  'tokenCounter' | 'customHandlers' | 'indexTokenCountMap' | 'initialSessions'
>): Promise<Run<IState>> {
  const resolvedRunId = runId ?? randomUUID();
  const runFilesActive =
    runFiles?.activate(
      resolvedRunId,
      conversationId ?? requestBody?.conversationId ?? '',
      agents.map((agent) => agent.id),
      signal,
    ) === true;
  if (
    appConfig?.endpoints?.agents?.fileSharing?.enabled === true &&
    agents[0]?.subagents?.enabled === true &&
    agents[0]?.subagents?.shareFiles === true &&
    !runFilesActive
  ) {
    throw new Error('Run file sharing is not supported by this endpoint: a file host is required.');
  }
  if (runFilesActive && !isRunFileSharingSupported()) {
    throw new Error('Run file sharing requires an agents SDK with subagent context support.');
  }
  // Detached child threads resume in a new host request without this run's
  // input snapshot or publication routing. Shared children stay foreground.
  // Detached completion turns cannot yet restore enrolled schedule authority.
  const activeSubagentTasks =
    runFilesActive || scheduledMCPExecution?.enrolled === true ? undefined : subagentTasks;
  /**
   * Only extract discovered tools if:
   * 1. We have message history to parse
   * 2. At least one agent has deferred tools (using precomputed flag)
   *
   * This optimization avoids iterating through messages in the ~95% of cases
   * where no agent uses deferred tool loading.
   */
  const hasAnyDeferredTools = agents.some((agent) => agent.hasDeferredTools === true);

  const discoveredTools = new Set<string>();
  if (hasAnyDeferredTools) {
    // Normal path: extract from this run's message history (tool_search results).
    if (messages?.length) {
      for (const name of extractDiscoveredToolsFromHistory(messages)) {
        discoveredTools.add(name);
      }
    }
    // Resume path: replay names captured at pause, since `messages` is empty (the
    // paused run's tool_search results live only in the checkpoint, not here).
    if (discoveredToolNames?.length) {
      for (const name of discoveredToolNames) {
        discoveredTools.add(name);
      }
    }
  }

  /** Admin kill switch for the ask tool — see {@link isAskUserQuestionAdminDisabled}. */
  const askToolAdminDisabled = isAskUserQuestionAdminDisabled(appConfig);

  // Independently initialized agents must reserve the same live paths before advertising uploads.
  const codeFileAgents = new Map<string, RunAgent>();
  const visitedCodeFileAgents = new Set<string>();
  const pendingCodeFileAgents: Array<RunAgent | null | undefined> = [...agents];
  for (let index = 0; index < pendingCodeFileAgents.length; index++) {
    const agent = pendingCodeFileAgents[index];
    if (!agent?.id || codeFileAgents.has(agent.id)) continue;
    codeFileAgents.set(agent.id, agent);
    visitedCodeFileAgents.add(agent.id);
    enqueueSubagentChildren(agent, pendingCodeFileAgents, visitedCodeFileAgents, false, false);
  }
  for (const agent of codeFileAgents.values()) {
    prepareQueuedCodeFileContext(agent, codeFileAgents.values(), user?.id);
  }

  const preparedCodeFileAgents = new WeakSet(codeFileAgents.values());
  const buildAgentInput = (agent: RunAgent, opts: { isSubagent?: boolean } = {}): AgentInputs => {
    if (!preparedCodeFileAgents.has(agent)) {
      if (agent.provisionState) agent.provisionState.codeEnvDestinations = undefined;
      codeFileAgents.set(agent.id, agent);
      prepareQueuedCodeFileContext(agent, codeFileAgents.values(), user?.id, true);
      preparedCodeFileAgents.add(agent);
    }
    const isSubagent = opts.isSubagent === true;
    if (runFilesActive) {
      for (const { memberConfigs } of agent.subagentGraphConfigs ?? []) {
        const deliveryTargets = new Set(
          memberConfigs.map((member) =>
            JSON.stringify([
              member.provider,
              member.endpoint ?? member.provider,
              member.model_parameters?.model ?? member.model,
              resolveUseResponsesApi(member.model_parameters?.useResponsesApi) === true,
              // Unset detail inherits the request value, which may differ from explicit auto.
              member.imageDetail ?? null,
            ]),
          ),
        );
        if (deliveryTargets.size > 1) {
          throw new Error(
            'Shared-file subagent teams must use the same provider, endpoint, model, API mode, and image-detail setting for every member.',
          );
        }
      }
    }
    const provider =
      (providerEndpointMap[
        agent.provider as keyof typeof providerEndpointMap
      ] as unknown as Providers) ?? agent.provider;
    const selfModel = agent.model_parameters?.model ?? (agent.model as string | undefined);

    /**
     * Resolve request-based headers across provider-specific header locations
     * (OpenAI `configuration.defaultHeaders`, Anthropic `clientOptions.defaultHeaders`,
     * Google `customHeaders`). Done at this step because the request body may
     * contain dynamic values (e.g. conversationId) that are only known after
     * agent initialization. Resolve before a self-summary snapshots this configuration.
     */
    resolveConfigHeaders({
      llmConfig: agent.model_parameters as Partial<t.RunLLMConfig>,
      user: createSafeUser(user),
      tenantId,
      body: requestBody,
    });

    const modelParameters = normalizeAgentModelParameters(agent.model_parameters);
    const shapedSummarization = shapeSummarizationConfig(
      agent.summarization ?? summarizationConfig,
      provider as string,
      selfModel,
      appConfig,
      agent.endpoint ?? undefined,
      { user, tenantId, requestBody },
      agent,
    );
    const summarization = modelCallbacks?.length
      ? {
          ...shapedSummarization,
          config: {
            ...shapedSummarization.config,
            parameters: withModelCallbacks(
              { ...(shapedSummarization.config.parameters ?? {}) },
              modelCallbacks,
            ),
          },
        }
      : shapedSummarization;

    const hasExplicitStreamUsage = Object.prototype.hasOwnProperty.call(
      modelParameters ?? {},
      'streamUsage',
    );
    const llmConfig = withModelCallbacks(
      Object.assign(
        {
          provider,
          streaming,
          streamUsage,
        },
        modelParameters,
      ) as t.RunLLMConfig,
      modelCallbacks,
    );

    const joinInstructionMap = (map?: Record<string, unknown>) =>
      Object.values(map ?? {})
        .filter((value): value is string => typeof value === 'string' && value !== '')
        .join('\n')
        .trim();

    const toolInstructions = joinInstructionMap(agent.toolContextMap);
    const dynamicToolInstructions = joinInstructionMap(agent.dynamicToolContextMap);

    const systemContent = [toolInstructions, agent.instructions ?? ''].join('\n').trim();

    let additionalInstructions = [dynamicToolInstructions, agent.additional_instructions ?? '']
      .join('\n')
      .trim();
    if (isSubagent && agent.alwaysApplySkillPrimes?.length) {
      const skillInstructions = agent.alwaysApplySkillPrimes
        .map((prime) => `# Always-apply skill: ${prime.name}\n${prime.body}`)
        .join('\n\n');
      additionalInstructions = [additionalInstructions, skillInstructions]
        .filter(Boolean)
        .join('\n\n');
    }

    /** Resolves issues with new OpenAI usage field */
    if (
      customProviders.has(agent.provider) ||
      (agent.provider === Providers.OPENAI && agent.endpoint !== agent.provider)
    ) {
      if (!hasExplicitStreamUsage) {
        llmConfig.streamUsage = false;
      }
      llmConfig.usage = true;
    }

    /**
     * Override defer_loading for tools that were discovered in previous
     * turns. This prevents the LLM from having to re-discover tools via
     * tool_search. Also add the discovered tools' definitions so the
     * LLM has their schemas.
     *
     * Skipped for subagent children (`isSubagent`) — they run in an
     * isolated context by contract, so inheriting the parent's
     * tool-search state leaks unrelated history and pre-loads tools the
     * child shouldn't care about. Mutations on `agent.toolRegistry`
     * and additions to `toolDefinitions` both happen here, so the flag
     * has to gate the whole block (clearing fields post-return can't
     * undo registry writes).
     */
    let toolDefinitions = agent.toolDefinitions ?? [];
    let toolRegistry = agent.toolRegistry;
    if (!isSubagent && discoveredTools.size > 0 && agent.toolRegistry) {
      overrideDeferLoadingForDiscoveredTools(agent.toolRegistry, discoveredTools);

      /** Add discovered tools' definitions so the LLM can see their schemas */
      const existingToolNames = new Set(toolDefinitions.map((d) => d.name));
      for (const toolName of discoveredTools) {
        if (existingToolNames.has(toolName)) {
          continue;
        }
        const toolDef = agent.toolRegistry.get(toolName);
        if (toolDef) {
          toolDefinitions = [...toolDefinitions, toolDef];
        }
      }
    } else if (isSubagent && agent.toolRegistry) {
      /**
       * Subagent children: hand the child a deep-enough clone of the
       * registry so later parent-graph builds (e.g. when the same
       * agent also appears as a handoff target in the outer loop)
       * can't mutate `defer_loading` on tool definitions the child
       * already holds a reference to. Clone the `Map` *and* each
       * `LCTool` — `overrideDeferLoadingForDiscoveredTools` writes
       * through to the tool object itself, so a shallow Map copy
       * alone wouldn't isolate the flag.
       */
      toolRegistry = new Map();
      for (const [name, tool] of agent.toolRegistry.entries()) {
        toolRegistry.set(name, { ...tool });
      }
      /** Child's own `toolDefinitions` list gets the same shallow-
       *  copied view so any later parent mutation of shared definitions
       *  is contained to the parent-graph path. */
      toolDefinitions = toolDefinitions.map((def) => ({ ...def }));
    }

    /**
     * `ask_user_question` pauses via a LangGraph `interrupt()` raised from its own
     * tool body, so it must execute IN-PROCESS inside the graph's ToolNode — the
     * event-dispatched path runs tool bodies in the host handler outside the Pregel
     * task frame, where `interrupt()` throws and becomes an error ToolMessage. The
     * tool therefore never rides the schema-only `toolDefinitions`/`toolRegistry`
     * surfaces: on every path it is REMOVED from them (clone-before-mutate,
     * matching the registry-clone discipline above), and on the one path where it
     * can actually work — an HITL-capable caller's top-level agent, with the admin
     * filter allowing it — a real instance is supplied via `graphTools`, the SDK's
     * in-graph direct-tool seam (bound to the model, executed inside the task
     * frame; requires `@librechat/agents` > 3.2.57, older versions ignore the
     * field). Everywhere else (OpenAI-compatible + Responses controllers with no
     * resume surface, subagent child graphs that compile without a checkpointer,
     * admin-disabled) it is stripped fail-closed with no replacement.
     */
    let tools = agent.tools;
    let graphTools: GenericTool[] | undefined;
    if (agentRequestsAskUserQuestion(agent)) {
      tools = tools?.filter(
        (tool) => (tool as { name?: string } | undefined)?.name !== ASK_USER_QUESTION_TOOL_NAME,
      );
      toolDefinitions = toolDefinitions.filter((def) => def.name !== ASK_USER_QUESTION_TOOL_NAME);
      if (toolRegistry?.has(ASK_USER_QUESTION_TOOL_NAME)) {
        toolRegistry = new Map(toolRegistry);
        toolRegistry.delete(ASK_USER_QUESTION_TOOL_NAME);
      }
      if (hitlCapable && !isSubagent && !askToolAdminDisabled) {
        graphTools = [
          createAskUserQuestionTool(toolInputValidationErrors) as unknown as GenericTool,
        ];
      }
    }

    if (runFilesActive) {
      tools = eventOnlyRunFileTools(tools, toolDefinitions);
    }

    const effectiveMaxContextTokens = computeEffectiveMaxContextTokens(
      summarization.reserveRatio,
      agent.baseContextTokens,
      agent.maxContextTokens,
    );

    const summarizationViable =
      effectiveMaxContextTokens == null ||
      effectiveMaxContextTokens >= MIN_SUMMARIZATION_CONTEXT_TOKENS;
    if (summarization.enabled && !summarizationViable) {
      logger.warn(
        '[createRun] Summarization disabled for this run: context budget below viable minimum',
        {
          agentId: agent.id,
          effectiveMaxContextTokens,
          minimum: MIN_SUMMARIZATION_CONTEXT_TOKENS,
        },
      );
    }

    const reasoningKey = getReasoningKey(provider, llmConfig, agent.endpoint, agent.reasoningKey);
    const agentInput: AgentInputs = {
      provider,
      endpoint: agent.endpoint ?? provider,
      reasoningKey,
      toolDefinitions,
      agentId: agent.id,
      tools,
      clientOptions: llmConfig,
      instructions: systemContent,
      additional_instructions:
        frameDynamicInstructions({
          instructions: additionalInstructions,
          provider,
          promptCache: 'promptCache' in llmConfig && llmConfig.promptCache === true,
        }) || undefined,
      name: agent.name ?? undefined,
      toolRegistry,
      maxContextTokens: effectiveMaxContextTokens,
      useLegacyContent: agent.useLegacyContent ?? false,
      discoveredTools:
        !isSubagent && discoveredTools.size > 0 ? Array.from(discoveredTools) : undefined,
      summarizationEnabled: summarization.enabled && summarizationViable,
      summarizationConfig: summarization.config,
      ...(!isSubagent && compactionSemanticIndex != null ? { compactionSemanticIndex } : {}),
      initialSummary: isSubagent ? undefined : initialSummary,
      contextPruningConfig: summarization.contextPruning,
      maxToolResultChars: agent.maxToolResultChars,
      initialSessions: buildAgentInitialToolSessions(agent, initialSessions),
      codeSessionKey: agent.codeSessionKey,
    };
    if (runFilesActive && runFiles != null && (isSubagent || agent.subagents?.enabled === true)) {
      graphTools = [...(graphTools ?? []), ...createRunFileTools(runFiles, agent.id, signal)];
    }
    if (graphTools) {
      /**
       * Typed structurally — not as `AgentInputs['graphTools']` — because the
       * field ships in `@librechat/agents` > 3.2.57 (agents#289); older SDK
       * versions ignore it at runtime (the tool is then simply absent, never
       * broken). Inline the field in the literal once the dependency is bumped.
       */
      (agentInput as AgentInputs & { graphTools?: GenericTool[] }).graphTools = graphTools;
    }
    return agentInput;
  };

  const agentsEndpointConfig = appConfig?.endpoints?.[EModelEndpoint.agents];
  const attachedCodeEnvironmentAgentIds = collectAttachedCodeEnvironmentAgentIds(agents);
  const attachedCodeEnvironmentSettings = collectAttachedCodeEnvironmentPolicySettings(agents);
  const codeApprovalMode = resolveAttachedCodeApprovalMode(
    requestedCodeApprovalMode,
    collectAttachedCodeApprovalPolicies(agents),
    agentsEndpointConfig?.toolApproval?.enabled !== false,
  );
  assertAttachedCodeEnvironmentApprovalSupported({
    hasAttachedCodeEnvironment: attachedCodeEnvironmentAgentIds.size > 0,
    hitlCapable,
    approvalExplicitlyDisabled: agentsEndpointConfig?.toolApproval?.enabled === false,
  });

  // Assigned after the run-wide HITL registry is built. Lazy descriptors
  // capture this indirection now and report their aliases when they resolve.
  let registerResolvedMCPToolAliases: (agent: RunAgent) => void = () => undefined;
  const agentInputs: AgentInputs[] = [];
  const subagentBuildState: SubagentBuildState = {
    configCount: 0,
    rootAgentIds: agents.map((agent) => agent.id),
  };
  const prebuiltGraphInputs = new Map<string, AgentInputs>();
  const visitedConfigIds = new Set<string>();
  const pendingConfigs: Array<RunAgent | null | undefined> = [...agents];
  for (let index = 0; index < pendingConfigs.length; index++) {
    const config = pendingConfigs[index];
    if (!config?.id || visitedConfigIds.has(config.id)) {
      continue;
    }
    visitedConfigIds.add(config.id);
    if (!prebuiltGraphInputs.has(config.id)) {
      prebuiltGraphInputs.set(config.id, buildIsolatedAgentInputs(config, buildAgentInput));
    }
    for (const graph of config.subagentGraphConfigs ?? []) {
      for (const member of graph.memberConfigs) {
        if (!prebuiltGraphInputs.has(member.id)) {
          prebuiltGraphInputs.set(member.id, buildIsolatedAgentInputs(member, buildAgentInput));
        }
      }
    }
    enqueueSubagentChildren(config, pendingConfigs, visitedConfigIds, false, false);
  }
  for (const agent of agents) {
    const agentInput = buildAgentInput(agent);
    if (summarizeOnly && agent === agents[0]) {
      agentInput.summarizeOnly = true;
    }
    const subagentConfigs = buildSubagentConfigs(
      agent,
      agentInput,
      buildAgentInput,
      subagentBuildState,
      agentsEndpointConfig,
      undefined,
      0,
      prebuiltGraphInputs,
      activeSubagentTasks != null,
      (resolvedAgent) => registerResolvedMCPToolAliases(resolvedAgent),
    );
    if (subagentConfigs.length > 0) {
      agentInput.subagentConfigs = subagentConfigs;
      /** Seed the SDK countdown that bounds nested delegation across isolated child graphs. */
      agentInput.maxSubagentDepth = MAX_SUBAGENT_DEPTH;
    }
    if (activeSubagentTasks != null) {
      agentInput.toolDefinitions = registerBackgroundTaskTool({
        toolRegistry: agentInput.toolRegistry,
        toolDefinitions: agentInput.toolDefinitions,
        subagentCompletionWakeups: agentUsesSubagentCompletionWakeups(
          activeSubagentTasks,
          agent.id,
        ),
      }).toolDefinitions;
    }
    agentInputs.push(agentInput);
  }

  const graphConfig: RunConfig['graphConfig'] = {
    signal,
    agents: agentInputs,
    edges: agents[0].edges ?? [],
  };

  if (agentInputs.length > 1 || ((graphConfig as MultiAgentGraphConfig).edges?.length ?? 0) > 0) {
    (graphConfig as unknown as MultiAgentGraphConfig).type = 'multi-agent';
  } else {
    (graphConfig as StandardGraphConfig).type = 'standard';
  }

  /**
   * Enable tool-output references when the bash tool is actually
   * present anywhere in this run — top-level agent OR any subagent
   * (transitively). `codeEnvAvailable` on each `RunAgent` is the
   * per-agent gate (admin `execute_code` capability AND the agent's
   * own `tools` listing `execute_code`), so the feature follows the
   * same activation as the bash-tool registration in
   * `initializeAgent`. The walk into `subagentAgentConfigs` is
   * load-bearing: a parent without `execute_code` can spawn a
   * subagent that has it, and the SDK's shared registry serves
   * every `ToolNode` compiled from this run's graph — so missing
   * subagents in this gate would leave the child's
   * `{{tool<idx>turn<turn>}}` placeholders unsubstituted. SDK
   * defaults (~400 KB per output, 5 MB total) keep substituted
   * payloads inside typical shell ARG_MAX limits, so no overrides
   * are needed for the experimental rollout.
   */
  const enableToolOutputReferences = anyAgentHasCodeEnv(agents);

  /**
   * Endpoint tool approval is off by default. An enabled policy installs the
   * `PreToolUse` hooks for every run. Only callers with a resume surface also
   * enable real HITL interrupts and a durable checkpointer; on headless runs
   * the SDK blocks both `deny` and `ask` before executing the tool.
   */
  // Resolve the effective policy through the single seam so BYOM defaults and
  // future persisted per-agent / per-skill sources do not leak into this call site.
  const toolApprovalPolicy = resolveToolApprovalPolicy({
    endpoint: agentsEndpointConfig?.toolApproval,
    attachedCodeEnvironment: attachedCodeEnvironmentAgentIds.size > 0,
  });
  // Every caller needs the policy hooks, including API-key ingresses. Only
  // AgentClient supports pause/resume; the SDK blocks `ask` without HITL enabled.
  // Keep the checkpointer and humanInTheLoop switch exclusive to those callers.
  /** Both-direction key-spelling aliases collected from every eagerly known
   *  agent, including explicit and graph subagents. Lazy subagents report
   *  theirs through `registerResolvedMCPToolAliases` below. */
  const mcpToolAliases = collectRunMCPToolAliases(agents);
  const mcpToolAliasKeys = new Set(
    mcpToolAliases.map(({ name, aliasName }) => `${name}\u0000${aliasName}`),
  );
  const effectiveToolApprovalPolicy = () =>
    exemptAskUserQuestionFromApproval(
      buildEffectiveToolApprovalPolicy(toolApprovalPolicy, mcpToolAliases, toolApprovalAllows),
      ASK_USER_QUESTION_TOOL_NAME,
    );
  const nativeEditFileAgentIds = collectNativeEditFileAgentIds(agents);
  const agentApprovalSession = createAgentToolApprovalSession({
    agents: [...codeFileAgents.values()],
    storage: toolApprovalPolicy?.agentModes === true ? toolApprovalStorage : undefined,
    authorizationStorage: toolApprovalStorage,
    reviewed: reviewedToolApprovals,
    policy: effectiveToolApprovalPolicy,
    lookupTimeoutMs: toolApprovalPolicy?.grantLookupTimeoutMs,
    scope:
      user?.id && (conversationId ?? requestBody?.conversationId)
        ? {
            userId: user.id,
            tenantId: tenantId ?? user.tenantId,
            conversationId: (conversationId ?? requestBody?.conversationId)!,
          }
        : undefined,
  });
  const approvalWiring = buildHITLRunWiring(
    // The ask tool is exempt from the approval prompt (unless explicitly
    // listed by the admin) — approving the right to ask a question is a
    // pure double-pause; the tool has no side effects to gate. Pattern
    // lists are healed against the tools' other key spellings first, so
    // admin globs written for pre-strip upstream names keep applying (a
    // non-matching deny would fail OPEN), and rules written against
    // current catalog names reach legacy-named instances.
    effectiveToolApprovalPolicy(),
    {
      userId: user?.id,
      conversationId: requestBody?.conversationId,
      tenantId: tenantId ?? user?.tenantId,
      appConfig,
    },
    mcpToolAliases,
    [
      { hook: agentApprovalSession.hook },
      ...(resolvedToolApprovalHooks ??
        buildToolApprovalHooks({
          userId: user?.id,
          conversationId: requestBody?.conversationId,
          tenantId: tenantId ?? user?.tenantId,
          appConfig,
        })),
      ...(attachedCodeEnvironmentAgentIds.size > 0
        ? [
            {
              hook: createAttachedCodeEnvironmentPolicyHook(
                attachedCodeEnvironmentAgentIds,
                attachedCodeEnvironmentSettings,
                codeApprovalMode,
              ),
            },
          ]
        : []),
    ],
    nativeEditFileAgentIds,
  );
  approvalWiring?.hooks.register('PostToolUse', { hooks: [agentApprovalSession.rememberHook] });
  approvalWiring?.hooks.register('PostToolBatch', {
    hooks: [agentApprovalSession.settleBatchHook],
  });
  const hitl = hitlCapable ? approvalWiring : undefined;
  const scheduledPolicy = scheduledMCPExecution
    ? createScheduledMCPRunPolicy(
        scheduledMCPExecution,
        agents,
        agents[0].edges ?? [],
        recordScheduledMCPDenial,
      )
    : undefined;
  registerResolvedMCPToolAliases = (resolvedAgent) => {
    agentApprovalSession.addAgent(resolvedAgent);
    scheduledPolicy?.registerAgent(resolvedAgent);
    for (const agentId of collectNativeEditFileAgentIds([resolvedAgent])) {
      nativeEditFileAgentIds.add(agentId);
    }
    if (resolvedAgent.codeExecutionContext?.environmentType === 'attached') {
      // The admission hook closes over these collections. A lazily resolved agent
      // therefore receives its own current machine policy before its first tool call;
      // a mode that machine does not permit safely falls back to ask/deny there.
      attachedCodeEnvironmentAgentIds.add(resolvedAgent.id);
      attachedCodeEnvironmentSettings.set(resolvedAgent.id, {
        configSchema: resolvedAgent.codeExecutionContext.codeEnvironmentConfigSchema,
        settings: resolvedAgent.codeExecutionContext.codeEnvironmentSettings,
        skillAuthoringAvailable: resolvedAgent.skillAuthoringAvailable === true,
      });
    } else {
      /** A routable child counted as attached before its call chose a route; it resolved
       *  off attached machines, and runs on that one route for the whole request. */
      attachedCodeEnvironmentAgentIds.delete(resolvedAgent.id);
      attachedCodeEnvironmentSettings.delete(resolvedAgent.id);
    }
    const discoveredAliases = collectRunMCPToolAliases([resolvedAgent]).filter(
      ({ name, aliasName }) => {
        const key = `${name}\u0000${aliasName}`;
        if (mcpToolAliasKeys.has(key)) {
          return false;
        }
        mcpToolAliasKeys.add(key);
        return true;
      },
    );
    if (discoveredAliases.length === 0) {
      return;
    }
    mcpToolAliases.push(...discoveredAliases);
    approvalWiring?.addMCPToolAliases(discoveredAliases, effectiveToolApprovalPolicy());
  };
  /**
   * The `ask_user_question` tool pauses via LangGraph `interrupt()` from inside its own
   * body, which needs only a durable checkpointer — NOT the tool-approval policy
   * (`humanInTheLoop`/hooks stay off unless approval is separately enabled; verified
   * end-to-end in `api/.../agents/__tests__/askUserQuestion.e2e.spec.js`). Top-level
   * check only: subagent copies of the tool are stripped in `buildAgentInput`. Gated on
   * `hitlCapable` like approval, and the tool itself was stripped from non-HITL callers
   * above, so a checkpointer here always has a resume surface. The LazyMongoSaver only
   * persists when a run actually pauses, so attaching it is near-zero overhead.
   */
  const asksUserQuestions =
    hitlCapable && !askToolAdminDisabled && agents.some(agentRequestsAskUserQuestion);
  if (hitl || asksUserQuestions || eventActorCheckpointing) {
    const checkpointer = await getAgentCheckpointer(agentsEndpointConfig?.checkpointer);
    graphConfig.compileOptions = { ...graphConfig.compileOptions, checkpointer };
  }

  /**
   * The run's hook registry: tool policy hooks (when approval is enabled)
   * plus the steer-drain PostToolBatch hook. Steering registers independently
   * of the approval policy and requires no checkpointer, but is hard-gated on
   * SDK support — draining on an SDK that ignores `injectedMessages` would
   * silently drop the user's words (the steer controller 501s in that case;
   * this guard is defense in depth).
   */
  let hooks = approvalWiring?.hooks;
  if (scheduledPolicy) {
    hooks ??= new HookRegistry();
    hooks.register('PreToolUse', { hooks: [scheduledPolicy.hook, scheduledPolicy.receipt] });
  }
  if (usesSubagentCompletionWakeups(activeSubagentTasks)) {
    hooks = hooks ?? new HookRegistry();
    hooks.register('PostToolUse', {
      pattern: String(Constants.SUBAGENT),
      hooks: [
        createSubagentWakeupHandleHook((agentId) =>
          agentUsesSubagentCompletionWakeups(activeSubagentTasks, agentId),
        ),
      ],
      internal: true,
    });
  }
  /** Activity labels register BEFORE the steer drain: the label must claim
   *  its slot while the batch's tool parts are still the content tail. If a
   *  steer drained first, its injected part would flush the tool block in
   *  sequential rendering and orphan the label outside its group. With the
   *  label claimed first, parts order as [tools…, label, steer] — the label
   *  terminates the group and the steer renders after it. */
  if (activityLabel != null) {
    hooks = hooks ?? new HookRegistry();
    hooks.register('PostToolBatch', { hooks: [activityLabel.hook] });
  }
  if (activityPhase != null) {
    hooks = hooks ?? new HookRegistry();
    hooks.register('PostToolBatch', { hooks: [activityPhase.hook] });
  }
  if (steering != null && isSteeringSupported()) {
    hooks = hooks ?? new HookRegistry();
    hooks.register('PostToolBatch', { hooks: [steering.hook] });
    if (steering.preemptHook != null && isSteerPreemptSupported()) {
      hooks.register('PreemptBoundary', { hooks: [steering.preemptHook] });
    }
    if (steering.terminalHook != null && isSteerTerminalContinuationSupported()) {
      const stopFinalizeRegistry = hooks as unknown as {
        register: (event: 'StopFinalize', matcher: { hooks: TerminalSteerHook[] }) => () => void;
      };
      stopFinalizeRegistry.register('StopFinalize', { hooks: [steering.terminalHook] });
    }
  }
  /**
   * Step-budget awareness. Registered unconditionally (no config, no checkpointer,
   * no SDK capability gate, since `additionalContext` has been part of `BaseHookOutput`
   * since hooks shipped) because running out of steps mid-turn is a failure mode on
   * every ingress, and a model that knows its budget is running low usually avoids
   * it. Registered after the label/steer hooks so their content-slot ordering is
   * untouched; `additionalContexts` accumulate independently of injected messages.
   */
  hooks = hooks ?? new HookRegistry();
  hooks.register('PostToolBatch', {
    hooks: [
      createStepBudgetHook({
        recursionLimit: resolveRecursionLimit(agentsEndpointConfig, agents[0]),
      }),
    ],
    internal: true,
  });
  /**
   * Deployment-plugin hooks (Agent Plugins `ai.librechat/hooks/hooks.json`)
   * register last so internal policy hooks (HITL, labels, steering) keep
   * their ordering. The source is wired at startup by the plugins package
   * (see `setPluginHookSource` in api/server/index.js) and stays empty
   * unless the operator installed plugins with hook documents AND opted in
   * via DEPLOYMENT_PLUGIN_HOOKS. The conversation id doubles as the plugin
   * "session", giving SessionStart its once-per-conversation scope.
   */
  const pluginHookSource = getPluginHookSource();
  if (pluginHookSource?.hasHooks() === true) {
    hooks = hooks ?? new HookRegistry();
    const primaryAgent = agents[0];
    pluginHookSource.register({
      registry: hooks,
      context: {
        sessionId: requestBody?.conversationId,
        userId: user?.id,
        sessionStartSource,
        model: primaryAgent?.model_parameters?.model ?? primaryAgent?.model ?? undefined,
        agentType: primaryAgent?.id,
      },
      // `ask` needs the checkpointer + resume surface; without HITL wiring the
      // source tightens plugin `ask` decisions to `deny` rather than stranding
      // the run on an un-resumable interrupt.
      askDecisionSupported: hitl != null,
    });
  }

  const streamLimits = resolveStreamLimits(agentsEndpointConfig);

  /**
   * Built as a variable (not an inline literal) so the extra
   * `subagentUsageSink` field passes assignability against SDK versions
   * whose `RunConfig` predates it (<= 3.2.33, where it is ignored at
   * runtime) — excess-property checks only apply to fresh literals. Inline
   * the field at the call site once the dependency is bumped.
   */
  let preemption = steering?.preemption;
  if (preemption != null && !supportsRunInterruption(agentInputs)) {
    await preemption.disable?.();
    preemption = undefined;
  }
  const toolHandler = customHandlers?.[GraphEvents.ON_TOOL_EXECUTE];
  const runHandlers =
    toolHandler != null && preemption != null && isSteerPreemptSupported()
      ? {
          ...customHandlers,
          [GraphEvents.ON_TOOL_EXECUTE]: interruptToolHandler(toolHandler, preemption),
        }
      : customHandlers;

  const runConfig = {
    runId: resolvedRunId,
    graphConfig,
    tokenCounter,
    customHandlers: runHandlers,
    initialSessions,
    calibrationRatio,
    fadingTier,
    fadingTiers,
    indexTokenCountMap,
    subagentUsageSink,
    subagentTasks: activeSubagentTasks,
    // Without it a subagent that cannot start logs nothing beyond the SDK's fixed `cause`.
    onSubagentResolutionFailure: reportSubagentResolutionFailure,
    ...(runFilesActive &&
      runFiles != null && {
        subagentContext: { prepare: runFiles.prepare, complete: runFiles.complete },
      }),
    // Exclude side-effecting / large-free-form-arg tools from eager execution.
    // Eager speculatively runs a tool mid-stream; for a big streamed arg (a
    // file body, a bash heredoc, a code block) the accumulated args can diverge
    // from the final tool call and trip the SDK's "changed after eager
    // execution" guard, and a speculative write/exec can land before the turn
    // commits. create_file/edit_file write files; execute_code/bash_tool run
    // code with large `code`/`command` args. `excludeToolNames` requires
    // @librechat/agents with the eager-exclusion support (agents#281); older
    // versions ignore the field. ask_user_question raises a LangGraph
    // `interrupt()` from its tool body, which must run inside the Pregel task
    // frame — a speculative eager execution could never pause the run.
    eagerEventToolExecution: {
      enabled: true,
      excludeToolNames: [
        CREATE_FILE_TOOL_NAME,
        EDIT_FILE_TOOL_NAME,
        Constants.EXECUTE_CODE,
        Constants.BASH_TOOL,
        ASK_USER_QUESTION_TOOL_NAME,
        /**
         * Background-capable tools: eager execution could launch the detached
         * task with speculative/partial args before the final tool call, and a
         * background side effect (unlike a foreground eager mismatch) can't be
         * canceled once dispatched. The poll tool is excluded for the same
         * reason: collecting a task's artifact is a one-shot claim that must
         * not fire from a speculative snapshot the SDK may later discard.
         */
        CHECK_BACKGROUND_TASK_NAME,
        ...agents.flatMap((agent) => agent.backgroundToolNames ?? []),
        ...(clientToolNames ?? []),
      ],
    },
    // Let host file tools share the code-execution sandbox session so a file
    // created with create_file/edit_file is visible to later
    // execute_code/bash_tool calls (and vice versa). The SDK folds these tools'
    // returned exec session/files into the shared code session and injects the
    // existing session into their requests. Membership here also stamps the
    // stateful `runtimeSessionHint` and excludes the tool from eager execution
    // — read_file needs both, or its sandbox `cat` runs hintless on the Code
    // API's per-user default runtime session and cannot see files bash_tool
    // just wrote in the conversation's session. Requires @librechat/agents
    // with codeSessionToolNames support (agents#283); older versions ignore it.
    // `check_background_task` participates so a backgrounded code call's exec
    // session/files (returned as the poll result's artifact when claimed) fold
    // into the shared code session, keeping same-run continuity for later
    // foreground code calls. Poll results carry an artifact only for code
    // tasks, so non-code polls never touch the session.
    codeSessionToolNames: [
      CREATE_FILE_TOOL_NAME,
      EDIT_FILE_TOOL_NAME,
      Constants.READ_FILE,
      CHECK_BACKGROUND_TASK_NAME,
    ],
    // Derive the Langfuse trace id deterministically from runId so message
    // feedback can be scored against the trace without a lookup (see the
    // feedback route in api/server/routes/messages.js). No-op unless Langfuse
    // tracing is enabled. Requires @librechat/agents >= 3.2.21.
    langfuse: buildLangfuseConfig({
      appConfig,
      runId: resolvedRunId,
      tenantId: tenantId ?? user?.tenantId,
      centralTraceExportEnabled,
      isTemporary,
      user,
      traceContext: resolveRunTraceContext({ agents, conversationId, requestBody, traceContext }),
    }),
    ...(enableToolOutputReferences && {
      toolOutputReferences: { enabled: true },
    }),
    // Only resumable callers enable real approval interrupts. The PreToolUse policy
    // hook stays in `hooks` for headless callers, where `ask` fails closed.
    // The steer-drain hook rides the same registry but independently of the approval
    // policy: a PostToolBatch-only registry keeps the SDK's eager execution fast paths
    // (it gates on result-altering hooks, not registry presence).
    ...(hitl && { humanInTheLoop: hitl.humanInTheLoop }),
    ...(hooks && { hooks }),
    // Preemption is observation-only like the boundary hooks: the poll never
    // mutates and the SDK refuses to seal unless a PreemptBoundary matcher is
    // live, so gating both on the same capability keeps them in lockstep.
    ...(preemption != null && isSteerPreemptSupported() && { preemption }),
    // Stream circuit breakers (librechat.yaml endpoints.agents.maxToolCallArgBytes /
    // maxDeltaEventsPerTurn). Omitted when unset so the SDK defaults apply: a runaway
    // streamed tool-call argument aborts the run at 64 KiB, the per-turn delta event
    // cap stays off. Requires @librechat/agents with streamLimits support (agents#381);
    // older versions ignore the field.
    ...(streamLimits && { streamLimits }),
  };
  const run = await Run.create(runConfig);
  if (approvalWiring != null) bindRunToolApprovalSession(run, agentApprovalSession);
  runMCPToolAliases.set(run, mcpToolAliases);

  applyCustomHandoffPromptKeyCompatibility(run, runConfig.graphConfig);
  applyTestRunHook(run, {
    messages,
    agents,
    modelCallbacks,
    conversationId: conversationId ?? requestBody?.conversationId ?? undefined,
  });
  return run;
}

```

## packages/api/src/agents/tools.ts

Source: https://github.com/LibreChat-AI/LibreChat/blob/f4154b649efa37e1345ccc517a4d8d1ed2178c82/packages/api/src/agents/tools.ts

SHA-256: `62e4943b06336cd3f22df7c76eb3e3ed1853dc48a150868ceda4ed697304641f`

```
import { HOST_FILE_EDIT_HARD_MAX_COUNT } from 'librechat-data-provider';
import {
  Constants as AgentConstants,
  CODE_EXECUTION_TOOLS,
  BashExecutionToolDefinition,
  ReadFileToolDefinition,
  SkillToolDefinition,
  buildBashExecutionToolDescription,
} from '@librechat/agents';
import {
  Tools,
  Constants,
  CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES,
  CODE_ENVIRONMENT_READ_FILE_HARD_MAX_LINES,
  normalizeActionToolName,
  normalizeServerName,
  splitMCPToolKey,
} from 'librechat-data-provider';
import type {
  AgentToolOptions,
  CodeWorkspaceOperation,
  CodeWorkspaceDescriptor,
  GraphEdge,
} from 'librechat-data-provider';
import type { LCTool, LCToolRegistry } from '@librechat/agents';
import type { ReachableAgent } from './traversal';
import {
  buildAttachedWorkspaceBashSchema,
  buildAttachedWorkspaceBashDescription,
} from '~/code/command';
import { toolkitExpansion } from '~/tools/toolkits/mapping';
import { normalizeAgentToolKeys } from '~/mcp/utils';
import { collectReachableAgents } from './traversal';

export const CREATE_FILE_TOOL_NAME = 'create_file';
export const EDIT_FILE_TOOL_NAME = 'edit_file';
export const SEARCH_WORKSPACE_TOOL_NAME = 'search_workspace';
export const LIST_WORKSPACE_FILES_TOOL_NAME = 'list_workspace_files';
export const HOST_FILE_AUTHORING_ARTIFACT_KEY = '__librechat_file_authoring';
export const FILE_AUTHORING_TOOL_NAMES: ReadonlySet<string> = new Set([
  CREATE_FILE_TOOL_NAME,
  EDIT_FILE_TOOL_NAME,
]);

/**
 * Every tool that reads or writes the code environment. Eligibility and provisioning both
 * consult this: a turn starting with any of them needs its files in the sandbox already,
 * and an agent that has any of them wants code-file provisioning built even when it never
 * names the `execute_code` marker itself.
 */
export const CODE_FILE_TOOL_NAMES: ReadonlySet<string> = new Set([
  ...CODE_EXECUTION_TOOLS,
  ...FILE_AUTHORING_TOOL_NAMES,
  AgentConstants.READ_FILE,
  AgentConstants.WRITE_FILE,
]);

export function isCodeFileToolName(name: string): boolean {
  return CODE_FILE_TOOL_NAMES.has(name);
}

export function isCodeSessionToolName(
  name: string,
  hostFileAuthoringToolNames?: ReadonlySet<string>,
): boolean {
  return (
    CODE_EXECUTION_TOOLS.has(name) ||
    name === SEARCH_WORKSPACE_TOOL_NAME ||
    name === LIST_WORKSPACE_FILES_TOOL_NAME ||
    hostFileAuthoringToolNames?.has(name) === true
  );
}

/** Host-only tool batches do not need live workspace capability discovery. */
export function requiresCodeWorkspaceCapabilities(toolNames: readonly string[]): boolean {
  return toolNames.some((name) => isCodeFileToolName(name) || isCodeSessionToolName(name));
}

/** Tools that consume shared code, search, or image resources need current file records. */
export function isFileResourceToolName(name: string): boolean {
  return (
    isCodeFileToolName(name) ||
    isCodeSessionToolName(name) ||
    name === Tools.file_search ||
    name === 'image_gen_oai' ||
    name === 'image_edit_oai' ||
    name === 'gemini_image_gen'
  );
}

/** File-authoring artifacts opt in explicitly; other tool artifacts keep their normal delivery. */
export function isCodeArtifactToolOutput(output: { name: string; artifact?: unknown }): boolean {
  const artifact = output.artifact;
  return (
    isCodeSessionToolName(output.name) ||
    (artifact != null &&
      typeof artifact === 'object' &&
      HOST_FILE_AUTHORING_ARTIFACT_KEY in artifact &&
      artifact[HOST_FILE_AUTHORING_ARTIFACT_KEY] === true)
  );
}

interface ToolDefLike {
  name: string;
  [key: string]: unknown;
}

interface ToolInstanceLike {
  name: string;
  [key: string]: unknown;
}

export interface BuildToolSetConfig {
  toolDefinitions?: ToolDefLike[];
  tools?: (ToolInstanceLike | null | undefined)[];
  /** Tool names retained on unresolved agent descriptors for history replay. */
  historicalToolNames?: readonly string[];
}

export interface BuildHistoricalToolNamesConfig {
  configuredToolNames?: readonly string[];
  alwaysApplyToolNames?: readonly string[];
  toolOptions?: AgentToolOptions;
  rawMcpServerNames?: readonly string[];
  codeExecutionAvailable?: boolean;
  memoryAvailable?: boolean;
  skillsAvailable?: boolean;
  skillFileAccessAvailable?: boolean;
  skillAuthoringAvailable?: boolean;
  deferredToolsAvailable?: boolean;
  programmaticToolsAvailable?: boolean;
  backgroundToolsAvailable?: boolean;
}

export interface SkillToolAvailabilityConfig {
  /** True when at least one catalog-visible (model-invocable) skill resolved for the run. */
  modelInvocableSkillsAvailable?: boolean;
  /** True when the model may create or edit skill files during the run. */
  skillAuthoringAvailable?: boolean;
}

/**
 * Single rule for whether the `skill` tool reaches the model, shared by live
 * registration (`injectSkillCatalog`) and the lazy-history prediction below so
 * the two cannot disagree about a run's tool names.
 *
 * Authoring runs register it even with an empty catalog: a skill the model
 * creates mid-run becomes a valid invocation target, and tool definitions bind
 * at initialization, so a run that only learned about the skill afterwards
 * would have no way to invoke what it just wrote.
 */
export function isSkillToolAvailable(config: SkillToolAvailabilityConfig): boolean {
  return config.modelInvocableSkillsAvailable === true || config.skillAuthoringAvailable === true;
}

/** Derives the model-facing names an unresolved lazy agent can expose without loading it. */
export function buildHistoricalToolNames(config: BuildHistoricalToolNamesConfig): Set<string> {
  const configuredToolNames = [
    ...(config.configuredToolNames ?? []),
    ...(config.alwaysApplyToolNames ?? []),
  ];
  const normalized = normalizeAgentToolKeys({
    tools: configuredToolNames,
    toolOptions: config.toolOptions,
    rawServerNames: config.rawMcpServerNames ?? [],
  });
  const toolNames = new Set((normalized.tools ?? []).map(normalizeActionToolName));

  const normalizedOptions: AgentToolOptions = {};
  for (const [name, options] of Object.entries(normalized.toolOptions ?? {})) {
    const normalizedName = normalizeActionToolName(name);
    normalizedOptions[normalizedName] =
      normalizedName !== name
        ? { ...options, ...normalizedOptions[normalizedName] }
        : { ...normalizedOptions[name], ...options };
  }

  for (const name of [...toolNames]) {
    for (const child of toolkitExpansion[name as keyof typeof toolkitExpansion] ?? []) {
      toolNames.add(child);
    }
  }

  if (config.codeExecutionAvailable === true) {
    toolNames.add('bash_tool');
    toolNames.add('read_file');
    toolNames.add(CREATE_FILE_TOOL_NAME);
    toolNames.add(EDIT_FILE_TOOL_NAME);
    toolNames.add(SEARCH_WORKSPACE_TOOL_NAME);
    toolNames.add(LIST_WORKSPACE_FILES_TOOL_NAME);
  }
  if (config.memoryAvailable === true) {
    toolNames.add('set_memory');
    toolNames.add('delete_memory');
  }
  if (
    isSkillToolAvailable({
      modelInvocableSkillsAvailable: config.skillsAvailable,
      skillAuthoringAvailable: config.skillAuthoringAvailable,
    })
  ) {
    toolNames.add(SkillToolDefinition.name);
  }
  if ((config.skillFileAccessAvailable ?? config.skillsAvailable) === true) {
    toolNames.add('read_file');
  }
  if (config.skillAuthoringAvailable === true) {
    toolNames.add('read_file');
    toolNames.add(CREATE_FILE_TOOL_NAME);
    toolNames.add(EDIT_FILE_TOOL_NAME);
  }

  const options = normalizedOptions;
  const hasDeferredTool = [...toolNames].some((name) => options[name]?.defer_loading === true);
  if (config.deferredToolsAvailable === true && hasDeferredTool) {
    toolNames.add('tool_search');
  }
  const hasProgrammaticTool = [...toolNames].some((name) =>
    options[name]?.allowed_callers?.includes('code_execution'),
  );
  if (
    config.programmaticToolsAvailable === true &&
    config.codeExecutionAvailable === true &&
    hasProgrammaticTool
  ) {
    toolNames.add('run_tools_with_bash');
  }
  const hasBackgroundTool =
    config.codeExecutionAvailable === true ||
    [...toolNames].some((name) => options[name]?.run_in_background === true);
  if (config.backgroundToolsAvailable === true && hasBackgroundTool) {
    toolNames.add(`${Constants.CHECK_BACKGROUND_TASK}`);
  }

  return toolNames;
}

/**
 * Builds a Set of tool names for use with formatAgentMessages.
 *
 * In event-driven mode, tools are defined via toolDefinitions (which includes
 * deferred tools like tool_search). In legacy mode, tools come from loaded
 * tool instances.
 *
 * This ensures tool_search and other deferred tools are included in the toolSet,
 * allowing their ToolMessages to be preserved in conversation history.
 */
export function buildToolSet(agentConfig: BuildToolSetConfig | null | undefined): Set<string> {
  if (!agentConfig) {
    return new Set();
  }

  const { toolDefinitions, tools, historicalToolNames } = agentConfig;

  const toolNames =
    toolDefinitions && toolDefinitions.length > 0
      ? toolDefinitions.map((def) => def.name)
      : (tools ?? []).map((tool) => tool?.name);

  return new Set(
    [...toolNames, ...(historicalToolNames ?? [])].filter((name): name is string => Boolean(name)),
  );
}

export interface RunToolSetConfig extends BuildToolSetConfig, ReachableAgent<RunToolSetConfig> {
  readonly edges?: readonly GraphEdge[];
  readonly accessibleMcpServerNames?: readonly string[];
  readonly historicalMcpServerNames?: readonly string[];
}

interface HistoricalToolCallIdentity {
  name: string;
  mcpServerName?: string;
}

function collectHistoricalToolCalls(
  messages?: Iterable<unknown> | null,
): HistoricalToolCallIdentity[] {
  const calls: HistoricalToolCallIdentity[] = [];
  const addCall = (value: unknown) => {
    if (value == null || typeof value !== 'object') {
      return;
    }
    const call = value as {
      name?: unknown;
      mcpServerName?: unknown;
      function?: { name?: unknown };
      tool_call?: { name?: unknown; mcpServerName?: unknown; subagent_content?: unknown };
      subagent_content?: unknown;
    };
    const name = call.name ?? call.function?.name ?? call.tool_call?.name;
    if (typeof name === 'string') {
      const mcpServerName = call.mcpServerName ?? call.tool_call?.mcpServerName;
      calls.push({
        name,
        ...(typeof mcpServerName === 'string' ? { mcpServerName } : {}),
      });
    }
    const nested = call.tool_call?.subagent_content ?? call.subagent_content;
    if (Array.isArray(nested)) {
      nested.forEach(addCall);
    }
  };

  for (const value of messages ?? []) {
    if (value == null || typeof value !== 'object') {
      continue;
    }
    const message = value as {
      content?: unknown;
      tool_calls?: unknown;
      additional_kwargs?: { tool_calls?: unknown };
    };
    if (Array.isArray(message.tool_calls)) {
      message.tool_calls.forEach(addCall);
    }
    if (Array.isArray(message.additional_kwargs?.tool_calls)) {
      message.additional_kwargs.tool_calls.forEach(addCall);
    }
    if (Array.isArray(message.content)) {
      message.content.forEach(addCall);
    }
  }
  return calls;
}

/** Builds the historical tool allowlist for the complete effective run topology. */
export function buildRunToolSet(
  primaryConfig: RunToolSetConfig | null | undefined,
  additionalConfigs?: Iterable<RunToolSetConfig | null | undefined> | null,
  hostGeneratedToolNames?: Iterable<string> | null,
  historicalMessages?: Iterable<unknown> | null,
  allowAmbiguousMcpToolNamesWithoutIdentity = false,
): Set<string> {
  const roots = [primaryConfig];
  if (additionalConfigs) {
    roots.push(...additionalConfigs);
  }

  const agents = collectReachableAgents(roots);
  if (agents.length === 0) {
    return new Set();
  }

  const toolSet = new Set<string>([`${Constants.SUBAGENT}`, 'conditional_transfer']);
  const wildcardServerNames = new Set<string>();
  const knownServerNames = new Set<string>();
  for (const name of hostGeneratedToolNames ?? []) {
    toolSet.add(name);
  }
  for (const agent of agents) {
    for (const rawName of [
      ...(agent.accessibleMcpServerNames ?? []),
      ...(agent.historicalMcpServerNames ?? []),
    ]) {
      knownServerNames.add(rawName);
      knownServerNames.add(normalizeServerName(rawName));
    }
    for (const name of buildToolSet(agent)) {
      toolSet.add(name);
      const wildcardPrefix = `${Constants.mcp_all}${Constants.mcp_delimiter}`;
      if (name.startsWith(wildcardPrefix)) {
        const rawServerName = name.slice(wildcardPrefix.length);
        if (rawServerName) {
          wildcardServerNames.add(normalizeServerName(rawServerName));
          knownServerNames.add(rawServerName);
          knownServerNames.add(normalizeServerName(rawServerName));
        }
      }
    }
  }

  if (wildcardServerNames.size > 0) {
    /** A wildcard authorizes every callable under one exact normalized server identity.
     * Resolve the longest known boundary so delimiter-bearing tool names remain valid
     * while a distinct longer server identity cannot masquerade as a selected suffix. */
    const boundaryNames = [...knownServerNames];
    for (const call of collectHistoricalToolCalls(historicalMessages)) {
      const { name, mcpServerName } = call;
      if (mcpServerName != null) {
        if (wildcardServerNames.has(normalizeServerName(mcpServerName))) {
          toolSet.add(name);
        }
        continue;
      }
      const [toolName, serverName] = splitMCPToolKey(name, boundaryNames);
      if (
        serverName != null &&
        toolName.length > 0 &&
        (allowAmbiguousMcpToolNamesWithoutIdentity ||
          !toolName.includes(Constants.mcp_delimiter)) &&
        wildcardServerNames.has(normalizeServerName(serverName))
      ) {
        toolSet.add(name);
      }
    }
  }

  for (const edge of primaryConfig?.edges ?? []) {
    if (edge.edgeType === 'direct') {
      continue;
    }
    const destinations = Array.isArray(edge.to) ? edge.to : [edge.to];
    for (const destination of destinations) {
      if (destination) {
        toolSet.add(`${Constants.LC_TRANSFER_TO_}${destination}`);
      }
    }
  }

  return toolSet;
}

export interface RegisterCodeExecutionToolsParams {
  toolRegistry: LCToolRegistry | undefined;
  toolDefinitions: LCTool[] | undefined;
  /**
   * When `true`, register `bash_tool` alongside `read_file`. When `false`,
   * register `read_file` only — manually-primed skills still need it to
   * load `references/*` files from storage even without a sandbox.
   *
   * Callers:
   * - `initializeAgent` passes `true` iff the `execute_code` capability
   *   is enabled for the run.
   * - `injectSkillCatalog` passes whatever `codeEnvAvailable` resolved to
   *   for the run.
   *
   * Both callers reach this helper in the same `initializeAgent` run
   * sequentially; the registry `.has()` check keeps the second call a
   * no-op so there is exactly one copy of each tool in `toolDefinitions`.
   */
  includeBash: boolean;
  /**
   * When `true`, `read_file` tells the model about skill-file paths
   * (`{skillName}/{path}`). When `false`, it is described only as a
   * code-execution sandbox reader so execute-code-only agents don't get
   * prompted to discover skills that are not enabled for the run.
   */
  includeSkillFileInstructions?: boolean;
  /**
   * When `true`, `read_file` advertises the explicit `workspace/` namespace
   * backed by the selected attached worker, including bounded line pagination.
   */
  workspaceTools?: boolean;
  /** Live operation ceiling for the selected workspace. Omitted for managed runtimes. */
  workspaceOperations?: ReadonlySet<CodeWorkspaceOperation>;
  /** Deployment ceiling advertised on attached Bash tool definitions. */
  workspaceCommandTimeoutMaxMs?: number;
  workspaceCommandTimeoutDefaultMs?: number;
  workspaceReadFileDefaultLines?: number;
  workspaceEnvironment?: CodeWorkspaceDescriptor['environment'];
  /** The worker runs `.worktrees/<name>` in its own lane; advertise `cwd` routing to the model. */
  workspaceLinkedWorktrees?: boolean;
  /** The worker advertises its native SRT sandbox; describe the read-only filesystem to the model. */
  workspaceNativeSandbox?: boolean;
  /**
   * When `true`, the registered `bash_tool` description includes the
   * LLM-facing `{{tool<idx>turn<turn>}}` reference syntax guide so the
   * model knows it can substitute prior tool outputs in subsequent
   * commands. Paired with `RunConfig.toolOutputReferences` in `createRun`.
   */
  enableToolOutputReferences?: boolean;
  /**
   * When `true`, the registered `bash_tool` description is the hedged
   * stateful-session variant (workspace usually persists across calls, may
   * reset at any time). Transport routing is resolved independently from the
   * actually executing agent at tool-load time.
   */
  statefulSessions?: boolean;
}

export interface RegisterCodeExecutionToolsResult {
  toolDefinitions: LCTool[];
  /** Tool names newly registered (skipped names that already existed). */
  registered: string[];
  /**
   * Every tool name this registration manages, whether newly registered or
   * already present. `initializeAgent` records these under the capability
   * marker that triggered the call, so a `tool_options` entry keyed by the
   * marker projects onto exactly the definitions the capability produced —
   * the registrar itself is the source of truth, not a hand-maintained map.
   */
  toolNames: string[];
}

export type RegisterFileAuthoringToolsResult = RegisterCodeExecutionToolsResult;

export interface RegisterFileAuthoringToolsParams {
  toolRegistry: LCToolRegistry | undefined;
  toolDefinitions: LCTool[] | undefined;
  /**
   * When true, descriptions point the model at the skill file namespace
   * (`skills/{skillName}/...`) in addition to sandbox paths. When false,
   * descriptions stay focused on code-execution sandbox files.
   */
  includeSkillFileInstructions?: boolean;
  /** When true, non-skill paths use the attached worker's workspace/ namespace. */
  workspaceTools?: boolean;
  /** Live operation ceiling for the selected workspace. Omitted for managed runtimes. */
  workspaceOperations?: ReadonlySet<CodeWorkspaceOperation>;
}

/** Locally extends SDK read parameters without changing the SDK path or intent contract. */
const READ_FILE_RANGE_PROPERTIES = Object.freeze({
  start_line: {
    type: 'integer',
    minimum: 1,
    description: 'Optional one-based starting line for a skill, sandbox, or workspace text file.',
  },
  max_lines: {
    type: 'integer',
    minimum: 1,
    maximum: CODE_ENVIRONMENT_READ_FILE_HARD_MAX_LINES,
    description: `Optional line limit for a skill, sandbox, or workspace text file. Defaults to ${CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES} when either range parameter is supplied for skill or sandbox text; the byte budget may truncate sooner.`,
  },
});

const READ_FILE_RANGE_INSTRUCTIONS = `Omit both range parameters for full skill/sandbox reads; range defaults: 1/${CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES}.`;

const SKILL_READ_FILE_PARAMETERS = Object.freeze({
  ...ReadFileToolDefinition.parameters,
  required: [...(ReadFileToolDefinition.parameters.required ?? ['path'])],
  properties: {
    ...ReadFileToolDefinition.parameters.properties,
    ...READ_FILE_RANGE_PROPERTIES,
  },
}) as LCTool['parameters'];

const SKILL_READ_FILE_DESCRIPTION = `${ReadFileToolDefinition.description}

Also accepts authored skill file paths using "skills/{skillName}/...", including "skills/{skillName}/SKILL.md".

${READ_FILE_RANGE_INSTRUCTIONS}`;

const READ_FILE_DEF: LCTool = Object.freeze({
  name: ReadFileToolDefinition.name,
  toolType: 'builtin',
  description: SKILL_READ_FILE_DESCRIPTION,
  parameters: SKILL_READ_FILE_PARAMETERS,
  responseFormat: ReadFileToolDefinition.responseFormat,
}) as LCTool;

const CODE_READ_FILE_DESCRIPTION = `Read a known code-sandbox file. Text is line-numbered and truncates around 256KB; png, jpeg, gif, and webp images return as visual content.

Use paths returned by tool output, just written, or under /mnt/data/. Do not run ls/find to rediscover known paths. Use bash_tool for binary or large files, transforms, metadata, and filesystem discovery.

For managed execution, only retained files under /mnt/data reach later calls. $HOME, /tmp, $TMPDIR, shell/environment state, cwd, global installs, and background processes are call-local.

${READ_FILE_RANGE_INSTRUCTIONS}`;

const ATTACHED_WORKSPACE_READ_FILE_INSTRUCTIONS = `For an attached environment, read registered files as "workspace/{relativePath}". Use a canonical relative path without empty, ".", or ".." segments; the worker's host path stays private. Only the registered workspace persists for attached commands. Project dependencies stored there persist, while $HOME and global/system packages are operator-managed. Use start_line and max_lines for bounded pagination.`;

/** Skill files live in LibreChat storage; attached commands only see the registered workspace. */
const ATTACHED_WORKSPACE_SKILL_FILE_INSTRUCTIONS =
  'Skill files are not on the attached machine, so bash_tool cannot run them by path; to run a skill script there, read it and write it into the workspace first.';

const CODE_READ_FILE_PARAMETERS: LCTool['parameters'] = Object.freeze({
  ...SKILL_READ_FILE_PARAMETERS,
  type: 'object',
  properties: {
    ...SKILL_READ_FILE_PARAMETERS?.properties,
    path: {
      type: 'string',
      description:
        'Path to a file from code execution output, such as "/mnt/data/result.csv" or another path returned by the execution tool.',
    },
  },
  required: ['path'],
}) as LCTool['parameters'];

function attachedReadFileLineDescription(defaultReadFileLines: number): string {
  return `Optional line limit for a skill, sandbox, or workspace text file. Defaults to ${defaultReadFileLines} for workspace reads and ${CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES} for explicit skill or sandbox ranges. Omit both range parameters for a full skill or sandbox read. The byte budget may truncate the result sooner.`;
}

const ATTACHED_WORKSPACE_READ_FILE_PARAMETERS: LCTool['parameters'] = Object.freeze({
  ...SKILL_READ_FILE_PARAMETERS,
  type: 'object',
  properties: {
    ...SKILL_READ_FILE_PARAMETERS?.properties,
    path: {
      type: 'string',
      description:
        'Use "workspace/{relativePath}" with a canonical relative path (no empty, ".", or ".." segments) for a file in the attached worker workspace directory, or a code-execution sandbox path such as "/mnt/data/result.csv".',
    },
    max_lines: {
      ...READ_FILE_RANGE_PROPERTIES.max_lines,
      description: attachedReadFileLineDescription(CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES),
    },
  },
  required: ['path'],
}) as LCTool['parameters'];

const CODE_READ_FILE_DEF: LCTool = Object.freeze({
  name: ReadFileToolDefinition.name,
  toolType: 'builtin',
  description: CODE_READ_FILE_DESCRIPTION,
  parameters: CODE_READ_FILE_PARAMETERS,
  responseFormat: ReadFileToolDefinition.responseFormat,
}) as LCTool;

/**
 * `attachedCommands` is true only when the attached `bash_tool` is registered
 * beside this definition: the skill-file warning names it.
 */
function createAttachedWorkspaceReadFileDef(
  includeSkillFileInstructions: boolean,
  defaultReadFileLines: number = CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES,
  attachedCommands = false,
): LCTool {
  const baseDescription = includeSkillFileInstructions
    ? SKILL_READ_FILE_DESCRIPTION
    : CODE_READ_FILE_DESCRIPTION;
  const attachedInstructions =
    includeSkillFileInstructions && attachedCommands
      ? `${ATTACHED_WORKSPACE_READ_FILE_INSTRUCTIONS} ${ATTACHED_WORKSPACE_SKILL_FILE_INSTRUCTIONS}`
      : ATTACHED_WORKSPACE_READ_FILE_INSTRUCTIONS;
  return Object.freeze({
    name: ReadFileToolDefinition.name,
    toolType: 'builtin',
    description: `${baseDescription}\n\n${attachedInstructions}`,
    parameters:
      defaultReadFileLines === CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES
        ? ATTACHED_WORKSPACE_READ_FILE_PARAMETERS
        : {
            ...ATTACHED_WORKSPACE_READ_FILE_PARAMETERS,
            properties: {
              ...ATTACHED_WORKSPACE_READ_FILE_PARAMETERS?.properties,
              max_lines: {
                ...ATTACHED_WORKSPACE_READ_FILE_PARAMETERS?.properties?.max_lines,
                description: attachedReadFileLineDescription(defaultReadFileLines),
              },
            },
          },
    responseFormat: ReadFileToolDefinition.responseFormat,
  }) as LCTool;
}

const ATTACHED_CODE_READ_FILE_DEF = createAttachedWorkspaceReadFileDef(false);
const ATTACHED_SKILL_READ_FILE_DEF = createAttachedWorkspaceReadFileDef(true);
const ATTACHED_SKILL_COMMAND_READ_FILE_DEF = createAttachedWorkspaceReadFileDef(
  true,
  CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES,
  true,
);

/** Skill-only `read_file` beside the attached `bash_tool` when the worker does not offer `read_file`. */
const ATTACHED_SKILL_ONLY_READ_FILE_DEF: LCTool = Object.freeze({
  ...READ_FILE_DEF,
  description: `${SKILL_READ_FILE_DESCRIPTION}\n\n${ATTACHED_WORKSPACE_SKILL_FILE_INSTRUCTIONS}`,
}) as LCTool;

/**
 * The SDK constraint the authoring variant rewrites. Left alone it tells the
 * model that catalog names are the only legal `skillName`, which is false for a
 * run that can author skills, and worst when the catalog is empty and the only
 * reachable skill is the one the model just created.
 */
const CATALOG_ONLY_SKILL_CONSTRAINT =
  '- Skill names come from the catalog only. Do not guess names.';

const AUTHORED_SKILL_CONSTRAINTS = `- Skill names come from the catalog, or from a skill you created in this conversation with create_file at "skills/{skillName}/SKILL.md". Do not guess any other name.
- Creating a skill does not load it. Invoke it here when you want to follow its instructions.`;

const AUTHORED_SKILL_NAME_DESCRIPTION =
  'The kebab-case identifier of the skill to invoke (e.g. "financial-analyzer", "meeting-notes"). Must match a name from the "Available Skills" section, or the name of a skill you created in this conversation.';

/**
 * Rewrites in place while the SDK still ships the catalog-only constraint, so
 * every other constraint it declares survives; appends otherwise, so the
 * authored-skill guidance reaches the model even if that text moves.
 *
 * Exported for the drift tests: appending leaves a reworded catalog-only
 * sentence standing next to guidance that contradicts it, so both branches have
 * to be pinned rather than inferred. `tools.spec.ts` also asserts the real SDK
 * export still carries the sentence, which fails CI on the bump that would
 * quietly move this onto the append branch.
 */
export function buildAuthoringSkillToolDescription(baseDescription: string): string {
  return baseDescription.includes(CATALOG_ONLY_SKILL_CONSTRAINT)
    ? baseDescription.replace(CATALOG_ONLY_SKILL_CONSTRAINT, AUTHORED_SKILL_CONSTRAINTS)
    : `${baseDescription}\n${AUTHORED_SKILL_CONSTRAINTS}`;
}

/** The shape the authoring variant needs from the SDK's `skill` schema. */
interface SkillToolParametersView {
  properties?: Record<string, { description?: string } | undefined>;
}

/**
 * Retargets the SDK's `skillName` guidance at skills authored this run, leaving
 * the rest of the schema alone.
 *
 * Takes the schema rather than reading the module import so both branches are
 * reachable from a test. The property is non-optional in the SDK's types, so
 * this reads it through a widened view: an installed package can disagree with
 * the types it shipped, and this definition is built at module load, where an
 * unguarded dereference would fail the whole `packages/api` import rather than
 * one tool's wording.
 */
export function buildAuthoringSkillToolParameters(
  baseParameters: LCTool['parameters'],
): LCTool['parameters'] {
  const view = baseParameters as unknown as SkillToolParametersView;
  const skillName = view.properties?.skillName;
  if (skillName == null) {
    return baseParameters;
  }
  return {
    ...view,
    properties: {
      ...view.properties,
      skillName: { ...skillName, description: AUTHORED_SKILL_NAME_DESCRIPTION },
    },
  } as unknown as LCTool['parameters'];
}

const SKILL_TOOL_DEF: LCTool = Object.freeze({
  name: SkillToolDefinition.name,
  description: SkillToolDefinition.description,
  parameters: SkillToolDefinition.parameters as unknown as LCTool['parameters'],
}) as LCTool;

const AUTHORING_SKILL_TOOL_DEF: LCTool = Object.freeze({
  name: SkillToolDefinition.name,
  description: buildAuthoringSkillToolDescription(SkillToolDefinition.description),
  parameters: buildAuthoringSkillToolParameters(
    SkillToolDefinition.parameters as unknown as LCTool['parameters'],
  ),
}) as LCTool;

/**
 * Model-facing `skill` definition for the run. Authoring runs get the variant
 * whose guidance accepts a name the model created during the run; every other
 * run gets the SDK definition untouched.
 */
export function getSkillToolDefinition(skillAuthoringAvailable: boolean): LCTool {
  return skillAuthoringAvailable ? AUTHORING_SKILL_TOOL_DEF : SKILL_TOOL_DEF;
}

const SEARCH_WORKSPACE_TOOL_DEF: LCTool = Object.freeze({
  name: SEARCH_WORKSPACE_TOOL_NAME,
  toolType: 'builtin',
  description:
    'Search for literal text within the attached worker workspace directory. Git is not required. Respects normal ignore files, does not follow symlinks, and returns bounded path, line, column, and text matches. Use path to limit the search to a relative file or directory.',
  parameters: Object.freeze({
    type: 'object',
    properties: {
      query: {
        type: 'string',
        description: 'Non-empty literal text to find. This is not a regular expression.',
      },
      path: {
        type: 'string',
        description:
          'Optional canonical relative file or directory within the attached workspace; do not use empty, ".", or ".." segments.',
      },
      max_results: {
        type: 'integer',
        minimum: 1,
        maximum: 200,
        description: 'Optional maximum number of matches to return. Defaults to 50.',
      },
    },
    required: ['query'],
  }) as LCTool['parameters'],
}) as LCTool;

const LIST_WORKSPACE_FILES_TOOL_DEF: LCTool = Object.freeze({
  name: LIST_WORKSPACE_FILES_TOOL_NAME,
  toolType: 'builtin',
  description:
    'List relative file paths in the attached worker workspace directory. Use this to discover files in an existing project, Git repository, or empty directory before reading or searching them. Respects normal ignore files, does not follow symlinks, and returns a bounded deterministic listing. When a result supplies an after_path continuation, pass it unchanged with the same path to fetch the next page.',
  parameters: Object.freeze({
    type: 'object',
    properties: {
      path: {
        type: 'string',
        description:
          'Optional canonical relative file or directory within the attached workspace; do not use empty, ".", or ".." segments.',
      },
      max_results: {
        type: 'integer',
        minimum: 1,
        maximum: 500,
        description: 'Optional maximum number of relative file paths to return. Defaults to 100.',
      },
      after_path: {
        type: 'string',
        description:
          'Optional canonical continuation path from the preceding truncated result. Pass it back unchanged with the same path.',
      },
    },
  }) as LCTool['parameters'],
}) as LCTool;

const SKILL_CREATE_FILE_PARAMETERS: LCTool['parameters'] = Object.freeze({
  type: 'object',
  properties: {
    path: {
      type: 'string',
      description:
        'Path to write. Use "skills/{skillName}/..." for skill files when available, or a code-execution sandbox path such as "/mnt/data/result.txt" when code execution is enabled. For SKILL.md, the YAML frontmatter name must match {skillName}.',
    },
    content: {
      type: 'string',
      description:
        'Complete file contents. Keep a single call well under the streamed tool-argument limit (64 KB by default); build larger files incrementally with edit_file.',
    },
    overwrite: {
      type: 'boolean',
      description: 'Must be true to replace an existing file. Refuses otherwise.',
      default: false,
    },
  },
  required: ['path', 'content'],
}) as LCTool['parameters'];

const CODE_CREATE_FILE_PARAMETERS: LCTool['parameters'] = Object.freeze({
  type: 'object',
  properties: {
    path: {
      type: 'string',
      description:
        'Path to write in the code-execution sandbox, such as "/mnt/data/result.txt". Prefer /mnt/data/{file} for files that should remain available to later sandbox calls.',
    },
    content: {
      type: 'string',
      description:
        'Complete file contents. Keep a single call well under the streamed tool-argument limit (64 KB by default); build larger files incrementally with edit_file.',
    },
    overwrite: {
      type: 'boolean',
      description: 'Must be true to replace an existing file. Refuses otherwise.',
      default: false,
    },
  },
  required: ['path', 'content'],
}) as LCTool['parameters'];

const SKILL_EDIT_FILE_PARAMETERS: LCTool['parameters'] = Object.freeze({
  type: 'object',
  properties: {
    path: {
      type: 'string',
      description:
        'Path to edit. Use "skills/{skillName}/..." for skill files when available, or a code-execution sandbox path such as "/mnt/data/result.txt" when code execution is enabled. edit_file cannot rename skills; keep SKILL.md frontmatter name equal to {skillName}.',
    },
    old_text: {
      type: 'string',
      description: 'Exact text to find. Must match exactly one location in the file.',
    },
    new_text: {
      type: 'string',
      description: 'Replacement text.',
    },
    replace_all: {
      type: 'boolean',
      description: 'Replace every location old_text matches instead of requiring exactly one.',
    },
    edits: {
      type: 'array',
      maxItems: HOST_FILE_EDIT_HARD_MAX_COUNT,
      description:
        'Optional batch of replacements. Each old_text must match exactly once unless its replace_all is true.',
      items: {
        type: 'object',
        properties: {
          old_text: { type: 'string' },
          new_text: { type: 'string' },
          replace_all: { type: 'boolean' },
        },
        required: ['old_text', 'new_text'],
      },
    },
  },
  required: ['path'],
}) as LCTool['parameters'];

const CODE_EDIT_FILE_PARAMETERS: LCTool['parameters'] = Object.freeze({
  type: 'object',
  properties: {
    path: {
      type: 'string',
      description: 'Path to edit in the code-execution sandbox, such as "/mnt/data/result.txt".',
    },
    old_text: {
      type: 'string',
      description: 'Exact text to find. Must match exactly one location in the file.',
    },
    new_text: {
      type: 'string',
      description: 'Replacement text.',
    },
    replace_all: {
      type: 'boolean',
      description: 'Replace every location old_text matches instead of requiring exactly one.',
    },
    edits: {
      type: 'array',
      maxItems: HOST_FILE_EDIT_HARD_MAX_COUNT,
      description:
        'Optional batch of replacements. Each old_text must match exactly once unless its replace_all is true.',
      items: {
        type: 'object',
        properties: {
          old_text: { type: 'string' },
          new_text: { type: 'string' },
          replace_all: { type: 'boolean' },
        },
        required: ['old_text', 'new_text'],
      },
    },
  },
  required: ['path'],
}) as LCTool['parameters'];

const SKILL_CREATE_FILE_DESCRIPTION = `Create a new file, or overwrite an existing file with explicit intent.

Use for new files and full rewrites. Requires overwrite: true to replace existing files.

Paths starting with "skills/" write skill files:
- skills/{skillName}/SKILL.md - main instructions; keep it lean with YAML frontmatter, trigger-friendly description, workflow steps, and short snippets.
- skills/{skillName}/references/{file} - long docs, schemas, examples, large templates, HTML/CSS/JS dashboards.
- skills/{skillName}/scripts/{file} - helper scripts.
- skills/{skillName}/assets/{file} - static assets.
- skills/{skillName}/templates/{file} - reusable output templates.

For SKILL.md, frontmatter name must match {skillName}; create skills/{newName}/SKILL.md to rename. Put large runnable artifacts in bundled files such as references/template.html, and have SKILL.md tell the agent when to read or reuse them.

Non-skills paths target the code-execution sandbox when enabled. Prefer /mnt/data/{file}.`;

const CODE_CREATE_FILE_DESCRIPTION = `Create a new file, or overwrite an existing file with explicit intent.

Use for new files and full rewrites where the change is larger than half the file. Requires overwrite: true to replace existing files. Refuses otherwise.

Targets code-execution sandbox paths. Prefer /mnt/data/{file} for files that should remain available to later sandbox calls.

Very long content can exceed the streamed tool-argument limit (64 KB by default) and fail the call. For large files, create the file with its first section, then extend it with edit_file.`;

const SKILL_EDIT_FILE_DESCRIPTION = `Apply targeted text replacements to an existing file.

Use for small, precise changes. Each old_text must match exactly one location. Tries exact match first; falls back to whitespace-tolerant matching if needed. Reports which matching strategy was used. Returns a unified diff.

For skills/{skillName}/SKILL.md, edit description, title, or body content, but keep YAML frontmatter name equal to {skillName}. edit_file cannot rename skills; create a new skills/{newName}/SKILL.md for a different skill name. Keep SKILL.md concise; move large templates, HTML/CSS/JS dashboards, examples, schemas, and long docs into references/, scripts/, assets/, or templates/ files and point to them from SKILL.md.

Paths starting with "skills/" target the skill file system. When code execution is enabled, non-skills paths target the code-execution sandbox.`;

const CODE_EDIT_FILE_DESCRIPTION = `Apply targeted text replacements to an existing file.

Use for small, precise changes. Each old_text must match exactly one location. Tries exact match first; falls back to whitespace-tolerant matching if needed. Reports which matching strategy was used. Returns a unified diff.

Targets code-execution sandbox paths, such as /mnt/data/result.txt.`;

const SKILL_CREATE_FILE_DEF: LCTool = Object.freeze({
  name: CREATE_FILE_TOOL_NAME,
  toolType: 'builtin',
  description: SKILL_CREATE_FILE_DESCRIPTION,
  parameters: SKILL_CREATE_FILE_PARAMETERS,
  responseFormat: 'content_and_artifact' as LCTool['responseFormat'],
}) as LCTool;

const CODE_CREATE_FILE_DEF: LCTool = Object.freeze({
  name: CREATE_FILE_TOOL_NAME,
  toolType: 'builtin',
  description: CODE_CREATE_FILE_DESCRIPTION,
  parameters: CODE_CREATE_FILE_PARAMETERS,
  responseFormat: 'content_and_artifact' as LCTool['responseFormat'],
}) as LCTool;

const SKILL_EDIT_FILE_DEF: LCTool = Object.freeze({
  name: EDIT_FILE_TOOL_NAME,
  toolType: 'builtin',
  description: SKILL_EDIT_FILE_DESCRIPTION,
  parameters: SKILL_EDIT_FILE_PARAMETERS,
  responseFormat: 'content_and_artifact' as LCTool['responseFormat'],
}) as LCTool;

const CODE_EDIT_FILE_DEF: LCTool = Object.freeze({
  name: EDIT_FILE_TOOL_NAME,
  toolType: 'builtin',
  description: CODE_EDIT_FILE_DESCRIPTION,
  parameters: CODE_EDIT_FILE_PARAMETERS,
  responseFormat: 'content_and_artifact' as LCTool['responseFormat'],
}) as LCTool;

const ATTACHED_CODE_CREATE_FILE_DESCRIPTION = `Create a new file in the selected attached environment, or overwrite one with explicit intent.

Use a path in the form "workspace/{relativePath}". Requires overwrite: true to replace an existing file and refuses otherwise. The workspace may be an existing project, a Git repository, or an empty directory.

Very long content can exceed the streamed tool-argument limit (64 KB by default). The attached workspace also limits each write to 1 MiB. Keep each call bounded.`;

const ATTACHED_CODE_EDIT_FILE_DESCRIPTION = `Apply one or more ordered text replacements to an existing file in the selected attached environment.

Use a path in the form "workspace/{relativePath}". Every old_text must match exactly one location at its step in the batch, unless that edit sets replace_all. Exact matching is tried first; where this environment allows it, whitespace-only differences are also accepted, and a whitespace-only miss names the line to copy. Up to 100 replacements and 1 MiB of edit text are allowed; the entire batch commits atomically or makes no change. A failure names every edit that did not apply and why, and may quote the current text where a missing edit most likely belongs; fix those edits against any quoted current text (read_file shows lines it leaves out or shortens), and retry.`;

const ATTACHED_SKILL_CREATE_FILE_DESCRIPTION = `${SKILL_CREATE_FILE_DESCRIPTION.replace(
  'Non-skills paths target the code-execution sandbox when enabled. Prefer /mnt/data/{file}.',
  'For the selected attached environment, non-skill paths must use "workspace/{relativePath}".',
)}`;

const ATTACHED_SKILL_EDIT_FILE_DESCRIPTION = `Apply targeted text replacements to an existing file.

For skills/{skillName}/... paths, exact matching falls back to whitespace-tolerant matching when needed and the result includes a unified diff. Keep SKILL.md YAML frontmatter name equal to {skillName}; create a new skills/{newName}/SKILL.md to rename a skill.

For workspace/{relativePath} paths in the selected attached environment, every old_text must match exactly one location at its step unless that edit sets replace_all. Exact matching is tried first; where this environment allows it, whitespace-only differences are also accepted, and a whitespace-only miss names the line to copy. Up to 100 replacements and 1 MiB of edit text commit atomically, a failure names every edit that did not apply and why (and may quote the current text where a missing edit most likely belongs), and the result is a write summary rather than a unified diff.`;

function attachedFileAuthoringParameters(
  parameters: LCTool['parameters'],
  includeSkillFileInstructions: boolean,
): LCTool['parameters'] {
  const properties = parameters?.properties ?? {};
  return Object.freeze({
    ...parameters,
    properties: {
      ...properties,
      path: {
        type: 'string',
        description: includeSkillFileInstructions
          ? 'Use "skills/{skillName}/..." for a LibreChat skill file, or "workspace/{relativePath}" for a file in the selected attached environment.'
          : 'Use "workspace/{relativePath}" for a file in the selected attached environment.',
      },
    },
  }) as LCTool['parameters'];
}

const ATTACHED_CODE_CREATE_FILE_DEF: LCTool = Object.freeze({
  ...CODE_CREATE_FILE_DEF,
  description: ATTACHED_CODE_CREATE_FILE_DESCRIPTION,
  parameters: attachedFileAuthoringParameters(CODE_CREATE_FILE_PARAMETERS, false),
}) as LCTool;

const ATTACHED_CODE_EDIT_FILE_DEF: LCTool = Object.freeze({
  ...CODE_EDIT_FILE_DEF,
  description: ATTACHED_CODE_EDIT_FILE_DESCRIPTION,
  parameters: attachedFileAuthoringParameters(CODE_EDIT_FILE_PARAMETERS, false),
}) as LCTool;

const ATTACHED_SKILL_CREATE_FILE_DEF: LCTool = Object.freeze({
  ...SKILL_CREATE_FILE_DEF,
  description: ATTACHED_SKILL_CREATE_FILE_DESCRIPTION,
  parameters: attachedFileAuthoringParameters(SKILL_CREATE_FILE_PARAMETERS, true),
}) as LCTool;

const ATTACHED_SKILL_EDIT_FILE_DEF: LCTool = Object.freeze({
  ...SKILL_EDIT_FILE_DEF,
  description: ATTACHED_SKILL_EDIT_FILE_DESCRIPTION,
  parameters: attachedFileAuthoringParameters(SKILL_EDIT_FILE_PARAMETERS, true),
}) as LCTool;

function buildReadFileDef(
  includeSkillFileInstructions: boolean,
  workspaceTools: boolean,
  defaultReadFileLines: number = CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES,
  attachedCommands = false,
): LCTool {
  if (workspaceTools) {
    if (defaultReadFileLines !== CODE_ENVIRONMENT_READ_FILE_DEFAULT_LINES) {
      return createAttachedWorkspaceReadFileDef(
        includeSkillFileInstructions,
        defaultReadFileLines,
        attachedCommands,
      );
    }
    if (!includeSkillFileInstructions) {
      return ATTACHED_CODE_READ_FILE_DEF;
    }
    return attachedCommands ? ATTACHED_SKILL_COMMAND_READ_FILE_DEF : ATTACHED_SKILL_READ_FILE_DEF;
  }
  return includeSkillFileInstructions ? READ_FILE_DEF : CODE_READ_FILE_DEF;
}

function buildFileAuthoringDefs(
  includeSkillFileInstructions: boolean,
  workspaceTools: boolean,
): LCTool[] {
  if (workspaceTools) {
    return includeSkillFileInstructions
      ? [ATTACHED_SKILL_CREATE_FILE_DEF, ATTACHED_SKILL_EDIT_FILE_DEF]
      : [ATTACHED_CODE_CREATE_FILE_DEF, ATTACHED_CODE_EDIT_FILE_DEF];
  }
  return includeSkillFileInstructions
    ? [SKILL_CREATE_FILE_DEF, SKILL_EDIT_FILE_DEF]
    : [CODE_CREATE_FILE_DEF, CODE_EDIT_FILE_DEF];
}

function isCodeOnlyReadFileDef(def: LCTool | undefined): boolean {
  return (
    def?.name === ReadFileToolDefinition.name &&
    (def?.description === CODE_READ_FILE_DESCRIPTION ||
      def?.description === ATTACHED_CODE_READ_FILE_DEF.description)
  );
}

function isCodeOnlyFileAuthoringDef(def: LCTool | undefined): boolean {
  if (def?.name === CREATE_FILE_TOOL_NAME) {
    return (
      def.description === CODE_CREATE_FILE_DESCRIPTION ||
      def.description === ATTACHED_CODE_CREATE_FILE_DESCRIPTION
    );
  }
  if (def?.name === EDIT_FILE_TOOL_NAME) {
    return (
      def.description === CODE_EDIT_FILE_DESCRIPTION ||
      def.description === ATTACHED_CODE_EDIT_FILE_DESCRIPTION
    );
  }
  return false;
}

export function isFileAuthoringToolDefinition(def: LCTool | undefined): boolean {
  if (def?.name === CREATE_FILE_TOOL_NAME) {
    return (
      def.description === CODE_CREATE_FILE_DESCRIPTION ||
      def.description === SKILL_CREATE_FILE_DESCRIPTION ||
      def.description === ATTACHED_CODE_CREATE_FILE_DESCRIPTION ||
      def.description === ATTACHED_SKILL_CREATE_FILE_DESCRIPTION
    );
  }
  if (def?.name === EDIT_FILE_TOOL_NAME) {
    return (
      def.description === CODE_EDIT_FILE_DESCRIPTION ||
      def.description === SKILL_EDIT_FILE_DESCRIPTION ||
      def.description === ATTACHED_CODE_EDIT_FILE_DESCRIPTION ||
      def.description === ATTACHED_SKILL_EDIT_FILE_DESCRIPTION
    );
  }
  return false;
}

/**
 * The `bash_tool` description varies along exactly one axis — whether
 * the LLM-facing `{{tool<idx>turn<turn>}}` reference syntax guide is
 * appended — so two frozen module-level singletons cover every call
 * site. Both are shaped identically to the legacy `BASH_TOOL_DEF`
 * constant; only `description` differs. Sharing references across
 * every agent init avoids per-call `Object.freeze` + SDK
 * `buildBashExecutionToolDescription` work, matching the no-allocation
 * intent of the original constant while keeping the per-agent gate
 * behavior introduced for tool-output references.
 */
function createBashToolDef(
  enableToolOutputReferences: boolean,
  statefulSessions = false,
  workspaceTools = false,
  workspaceCommandTimeoutMaxMs?: number,
  workspaceEnvironment?: CodeWorkspaceDescriptor['environment'],
  workspaceLinkedWorktrees = false,
  workspaceCommandTimeoutDefaultMs?: number,
  workspaceNativeSandbox = false,
): LCTool {
  /* Passed as a variable (not an inline literal) so the extra
   * `statefulSessions` key stays assignable against pinned SDK versions
   * whose builder predates it (ignored at runtime there). */
  const descriptionOpts = { enableToolOutputReferences, statefulSessions };
  return Object.freeze({
    name: BashExecutionToolDefinition.name,
    toolType: 'builtin',
    description: workspaceTools
      ? buildAttachedWorkspaceBashDescription(
          enableToolOutputReferences,
          workspaceEnvironment,
          workspaceNativeSandbox,
        )
      : buildBashExecutionToolDescription(descriptionOpts),
    parameters: (workspaceTools
      ? buildAttachedWorkspaceBashSchema(
          workspaceCommandTimeoutMaxMs,
          workspaceEnvironment,
          workspaceLinkedWorktrees,
          workspaceCommandTimeoutDefaultMs,
        )
      : BashExecutionToolDefinition.schema) as unknown as LCTool['parameters'],
  }) as LCTool;
}

const BASH_TOOL_DEF_WITH_OUTPUT_REFS = createBashToolDef(true);
const BASH_TOOL_DEF_WITHOUT_OUTPUT_REFS = createBashToolDef(false);

function buildBashToolDef(opts: {
  enableToolOutputReferences: boolean;
  statefulSessions?: boolean;
  workspaceTools?: boolean;
  workspaceCommandTimeoutMaxMs?: number;
  workspaceCommandTimeoutDefaultMs?: number;
  workspaceEnvironment?: CodeWorkspaceDescriptor['environment'];
  /** The worker runs `.worktrees/<name>` in its own lane; advertise `cwd` routing to the model. */
  workspaceLinkedWorktrees?: boolean;
  workspaceNativeSandbox?: boolean;
}): LCTool {
  /* Stateful defs are built on demand: the stateless pair covers the
   * default path, and per-run construction is negligible next to init. */
  if (opts.statefulSessions === true || opts.workspaceTools === true) {
    return createBashToolDef(
      opts.enableToolOutputReferences,
      opts.statefulSessions === true,
      opts.workspaceTools === true,
      opts.workspaceCommandTimeoutMaxMs,
      opts.workspaceEnvironment,
      opts.workspaceLinkedWorktrees === true,
      opts.workspaceCommandTimeoutDefaultMs,
      opts.workspaceNativeSandbox === true,
    );
  }
  return opts.enableToolOutputReferences
    ? BASH_TOOL_DEF_WITH_OUTPUT_REFS
    : BASH_TOOL_DEF_WITHOUT_OUTPUT_REFS;
}

/**
 * Idempotently registers the code-execution tool pair (`bash_tool` +
 * `read_file`) into the run's tool registry and tool-definition list.
 * `read_file` can be registered with a code-only description first and
 * upgraded to the skill-aware description later in the same run if
 * skills are actually injected.
 *
 * Replaces the legacy `CodeExecutionToolDefinition` / `execute_code`
 * registration. `execute_code` as a capability name and as an
 * `agent.tools` entry is preserved — it just expands into this tool
 * pair at load time so there is only one code-execution tool path
 * end-to-end (no same-run dedupe surprises for agents with both
 * `execute_code` capability AND skills active).
 */
export function registerCodeExecutionTools(
  params: RegisterCodeExecutionToolsParams,
): RegisterCodeExecutionToolsResult {
  const {
    toolRegistry,
    toolDefinitions,
    includeBash,
    includeSkillFileInstructions = true,
    workspaceTools = false,
    workspaceOperations,
    workspaceCommandTimeoutMaxMs,
    workspaceCommandTimeoutDefaultMs,
    workspaceReadFileDefaultLines,
    workspaceEnvironment,
    workspaceLinkedWorktrees,
    workspaceNativeSandbox,
    enableToolOutputReferences = false,
    statefulSessions = false,
  } = params;

  const supportsWorkspaceOperation = (operation: CodeWorkspaceOperation): boolean =>
    !workspaceTools || workspaceOperations?.has(operation) === true;
  const candidates: LCTool[] = [];
  const bashAvailable = includeBash && supportsWorkspaceOperation('execute_command');
  const attachedCommands = workspaceTools && bashAvailable;
  if (!workspaceTools || supportsWorkspaceOperation('read_file')) {
    candidates.push(
      buildReadFileDef(
        includeSkillFileInstructions,
        workspaceTools,
        workspaceReadFileDefaultLines,
        attachedCommands,
      ),
    );
  } else if (includeSkillFileInstructions) {
    candidates.push(attachedCommands ? ATTACHED_SKILL_ONLY_READ_FILE_DEF : READ_FILE_DEF);
  }
  if (bashAvailable) {
    candidates.push(
      buildBashToolDef({
        enableToolOutputReferences,
        statefulSessions,
        workspaceTools,
        workspaceCommandTimeoutMaxMs,
        workspaceCommandTimeoutDefaultMs,
        workspaceEnvironment,
        workspaceLinkedWorktrees,
        workspaceNativeSandbox,
      }),
    );
  }
  if (workspaceTools && supportsWorkspaceOperation('search_text')) {
    candidates.push(SEARCH_WORKSPACE_TOOL_DEF);
  }
  if (workspaceTools && supportsWorkspaceOperation('list_files')) {
    candidates.push(LIST_WORKSPACE_FILES_TOOL_DEF);
  }
  const toolNames = candidates.map((def) => def.name);

  const inputDefinitions = toolDefinitions ?? [];
  let workingDefinitions = inputDefinitions;

  const registered: string[] = [];
  const newDefs: LCTool[] = [];
  for (const def of candidates) {
    const existingIndex = workingDefinitions.findIndex((d) => d.name === def.name);
    const existingDef = existingIndex >= 0 ? workingDefinitions[existingIndex] : undefined;
    const registryDef = toolRegistry?.get(def.name);
    if (
      def.name === ReadFileToolDefinition.name &&
      includeSkillFileInstructions &&
      (isCodeOnlyReadFileDef(existingDef) || isCodeOnlyReadFileDef(registryDef))
    ) {
      if (isCodeOnlyReadFileDef(existingDef)) {
        workingDefinitions =
          workingDefinitions === inputDefinitions ? [...inputDefinitions] : workingDefinitions;
        workingDefinitions[existingIndex] = def;
      }
      if (isCodeOnlyReadFileDef(registryDef)) {
        toolRegistry?.set(def.name, def);
      }
      continue;
    }

    const inRegistry = toolRegistry?.has(def.name) === true;
    const inDefs = existingIndex >= 0;
    if (inRegistry || inDefs) {
      continue;
    }
    toolRegistry?.set(def.name, def);
    newDefs.push(def);
    registered.push(def.name);
  }

  /**
   * Skip the array spread on the common second-call path when no tools were
   * newly registered. Returns the input array by reference unless an existing
   * code-only `read_file` definition was upgraded above.
   */
  if (newDefs.length === 0) {
    return { toolDefinitions: workingDefinitions, registered, toolNames };
  }
  return {
    toolDefinitions: [...workingDefinitions, ...newDefs],
    registered,
    toolNames,
  };
}

export function registerFileAuthoringTools(
  params: RegisterFileAuthoringToolsParams,
): RegisterFileAuthoringToolsResult {
  const {
    toolRegistry,
    toolDefinitions,
    includeSkillFileInstructions = true,
    workspaceTools = false,
    workspaceOperations,
  } = params;

  const supportsWorkspaceOperation = (operation: CodeWorkspaceOperation): boolean =>
    !workspaceTools || workspaceOperations?.has(operation) === true;
  let candidates = buildFileAuthoringDefs(includeSkillFileInstructions, false);
  if (workspaceTools) {
    candidates = [];
    if (includeSkillFileInstructions) {
      candidates.push(
        supportsWorkspaceOperation('write_file')
          ? ATTACHED_SKILL_CREATE_FILE_DEF
          : SKILL_CREATE_FILE_DEF,
        supportsWorkspaceOperation('edit_file')
          ? ATTACHED_SKILL_EDIT_FILE_DEF
          : SKILL_EDIT_FILE_DEF,
      );
    } else {
      if (supportsWorkspaceOperation('write_file')) candidates.push(ATTACHED_CODE_CREATE_FILE_DEF);
      if (supportsWorkspaceOperation('edit_file')) candidates.push(ATTACHED_CODE_EDIT_FILE_DEF);
    }
  }
  const toolNames = candidates.map((def) => def.name);
  const inputDefinitions = toolDefinitions ?? [];
  let workingDefinitions = inputDefinitions;

  const registered: string[] = [];
  const newDefs: LCTool[] = [];
  for (const def of candidates) {
    const existingIndex = workingDefinitions.findIndex((d) => d.name === def.name);
    const existingDef = existingIndex >= 0 ? workingDefinitions[existingIndex] : undefined;
    const registryDef = toolRegistry?.get(def.name);

    if (
      includeSkillFileInstructions &&
      (isCodeOnlyFileAuthoringDef(existingDef) || isCodeOnlyFileAuthoringDef(registryDef))
    ) {
      if (isCodeOnlyFileAuthoringDef(existingDef)) {
        workingDefinitions =
          workingDefinitions === inputDefinitions ? [...inputDefinitions] : workingDefinitions;
        workingDefinitions[existingIndex] = def;
      }
      if (isCodeOnlyFileAuthoringDef(registryDef)) {
        toolRegistry?.set(def.name, def);
      }
      continue;
    }

    const inRegistry = toolRegistry?.has(def.name) === true;
    const inDefs = existingIndex >= 0;
    if (inRegistry || inDefs) {
      continue;
    }

    toolRegistry?.set(def.name, def);
    newDefs.push(def);
    registered.push(def.name);
  }

  if (newDefs.length === 0) {
    return { toolDefinitions: workingDefinitions, registered, toolNames };
  }
  return {
    toolDefinitions: [...workingDefinitions, ...newDefs],
    registered,
    toolNames,
  };
}

```
