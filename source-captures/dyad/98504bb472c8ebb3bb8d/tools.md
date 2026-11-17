# dyad — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/pro/main/ipc/handlers/local_agent/tool_definitions.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tool_definitions.ts

SHA-256: `413448ded85151d39d4305b7185a02c9b49ff922d423ae52055b6dca3ddb75fa`

```
import { withReferencedAppRead } from "./tools/referenced_app_read";
import { recordShellReviewOutcome } from "./shell_review_history";
import { runShellTool } from "./tools/run_shell";
import { isShellExperimentAvailable } from "@/shared/shell_capability";
/**
 * Tool definitions for Local Agent v2
 * Each tool includes a zod schema, description, and execute function
 */

import { IpcMainInvokeEvent } from "electron";
import log from "electron-log";
import { readSettings, writeSettings } from "@/main/settings";
import type { SqlConsentMetadata } from "@/shared/sqlConsentMetadata";
import {
  rememberUserInputSubscriber,
  userInputRegistry,
} from "@/user_input/main";
import { writeFileTool } from "./tools/write_file";
import { deleteFileTool } from "./tools/delete_file";
import { renameFileTool } from "./tools/rename_file";
import { copyFileTool } from "./tools/copy_file";
import { addDependencyTool } from "./tools/add_dependency";
import { executeSqlTool } from "./tools/execute_sql";
import { getNeonProjectInfoTool } from "./tools/get_neon_project_info";
import { getDatabaseTableSchemaTool } from "./tools/get_database_table_schema";

import { readFileTool } from "./tools/read_file";
import { listFilesTool } from "./tools/list_files";
import { getSupabaseProjectInfoTool } from "./tools/get_supabase_project_info";
import { setChatSummaryTool } from "./tools/set_chat_summary";
import { addIntegrationTool } from "./tools/add_integration";
import { enableNitroTool } from "./tools/enable_nitro";
import { readLogsTool } from "./tools/read_logs";
import { searchReplaceTool } from "./tools/search_replace";
import { webSearchTool } from "./tools/web_search";
import { webCrawlTool } from "./tools/web_crawl";
import { webFetchTool } from "./tools/web_fetch";
import { generateImageTool } from "./tools/generate_image";
import { updateTodosTool } from "./tools/update_todos";
import { runTypeChecksTool } from "./tools/run_type_checks";
import { runTestsTool } from "./tools/run_tests";
import { runPreCommitTool } from "./tools/run_pre_commit";
import { runBuildTool } from "./tools/run_build";
import { generateTestAssertionsTool } from "./tools/generate_test_assertions";
import {
  reinstallAndRestartAppTool,
  restartAppTool,
} from "./tools/app_lifecycle";
import { grepTool } from "./tools/grep";
import { codeSearchTool } from "./tools/code_search";
import { exploreChatHistoryTool } from "./tools/explore_chat_history";
import { searchChatsTool } from "./tools/search_chats";
import { readChatTool } from "./tools/read_chat";
import {
  cancelAgentTool,
  exploreCodeTool,
  followupTaskTool,
  listAgentsTool,
  sendMessageTool,
  spawnAgentTool,
  waitAgentsTool,
} from "./tools/subagent_tools";
import { planningQuestionnaireTool } from "./tools/planning_questionnaire";
import { writePlanTool } from "./tools/write_plan";
import { exitPlanTool } from "./tools/exit_plan";
import { readGuideTool } from "./tools/read_guide";
import {
  buildExecuteSandboxScriptDescription,
  executeSandboxScriptTool,
} from "./tools/execute_sandbox_script";
import { searchMcpToolsTool } from "./tools/search_mcp_tools";
import { getMcpToolSchemaTool } from "./tools/get_mcp_tool_schema";
import {
  suggestPluginTool,
  type SuggestablePlugin,
} from "./tools/suggest_plugin";
import {
  estimateMcpInlineTokens,
  getMcpInlineTokenThreshold,
  type McpToolDef,
} from "./tools/mcp_type_defs";
import { writeAppBlueprintTool } from "./tools/write_app_blueprint";
import {
  gitDiffTool,
  gitLogTool,
  gitRestoreFileTool,
  gitShowCommitTool,
  gitShowFileTool,
  gitStatusTool,
} from "./tools/git";
import type { LanguageModelV3ToolResultOutput } from "@ai-sdk/provider";
import { asSchema } from "ai";
import {
  escapeXmlAttr,
  escapeXmlContent,
  resolveToolDescription,
  type ToolDefinition,
  type AgentContext,
  type ToolResult,
} from "./tools/types";
import {
  assertAppBlueprintApproved,
  requireToolConsentOrThrow,
  shouldTrackToolFileMutation,
  shouldTrackToolMutation,
  trackAppMutation,
  trackFileEditTool,
} from "./tools/tool_invocation";
import type { AgentToolConsent } from "@/lib/schemas";
import { getSupabaseClientCode } from "@/supabase_admin/supabase_context";
import { getNeonClientCode } from "@/neon_admin/neon_context";
import { DyadError, DyadErrorKind } from "@/errors/dyad_error";
import { ExecuteAddDependencyError } from "@/ipc/processors/executeAddDependency";
import { withTrackedMutation } from "./subagents/mutation_activity_tracker";
import { estimateTokens } from "@/ipc/utils/token_utils";

const logger = log.scope("local_agent_tools");

function recordStreamingToolActivity(
  ctx: AgentContext,
  activity: Parameters<NonNullable<AgentContext["onToolActivity"]>>[0],
): void {
  const update = ctx.onToolActivity?.(activity);
  if (update) {
    void update.catch((error) =>
      logger.warn("Failed to record streaming sub-agent activity", error),
    );
  }
}

function getToolErrorDisplayDetails(error: unknown): string {
  if (error instanceof ExecuteAddDependencyError) {
    return error.displayDetails;
  }

  return error instanceof Error ? error.message : String(error);
}

function getToolErrorSummary(error: unknown): string {
  if (error instanceof ExecuteAddDependencyError) {
    return error.displaySummary;
  }

  return error instanceof Error ? error.message : String(error);
}

// Combined tool definitions array
export const TOOL_DEFINITIONS: readonly ToolDefinition[] = [
  writeFileTool,
  searchReplaceTool,
  copyFileTool,
  deleteFileTool,
  renameFileTool,
  addDependencyTool,
  executeSqlTool,
  readFileTool,
  listFilesTool,
  gitStatusTool,
  gitDiffTool,
  gitLogTool,
  gitShowCommitTool,
  gitShowFileTool,
  gitRestoreFileTool,
  grepTool,
  codeSearchTool,
  exploreChatHistoryTool,
  searchChatsTool,
  readChatTool,
  exploreCodeTool,
  spawnAgentTool,
  listAgentsTool,
  waitAgentsTool,
  cancelAgentTool,
  sendMessageTool,
  followupTaskTool,
  getSupabaseProjectInfoTool,
  getNeonProjectInfoTool,
  getDatabaseTableSchemaTool,
  setChatSummaryTool,
  addIntegrationTool,
  enableNitroTool,
  readLogsTool,
  webSearchTool,
  webCrawlTool,
  webFetchTool,
  generateImageTool,
  updateTodosTool,
  runTypeChecksTool,
  runPreCommitTool,
  runShellTool,
  runBuildTool,
  runTestsTool,
  generateTestAssertionsTool,
  restartAppTool,
  reinstallAndRestartAppTool,
  readGuideTool,
  executeSandboxScriptTool,
  searchMcpToolsTool,
  getMcpToolSchemaTool,
  suggestPluginTool,
  // Plan mode tools
  planningQuestionnaireTool,
  writePlanTool,
  exitPlanTool,
  // App blueprint tools
  writeAppBlueprintTool,
];
// ============================================================================
// Agent Tool Name Type (derived from TOOL_DEFINITIONS)
// ============================================================================

export type AgentToolName = (typeof TOOL_DEFINITIONS)[number]["name"];

function getAgentToolConsentSettings(
  toolName: AgentToolName,
  consent: AgentToolConsent,
) {
  const settings = readSettings();
  return {
    agentToolConsents: {
      ...settings.agentToolConsents,
      [toolName]: consent,
    },
  };
}

// ============================================================================
// Agent Tool Consent Management
// ============================================================================

export function getDefaultConsent(toolName: AgentToolName): AgentToolConsent {
  const tool = TOOL_DEFINITIONS.find((t) => t.name === toolName);
  return tool?.defaultConsent ?? "ask";
}

/**
 * When autoApproveNonSchemaSql is enabled, execute_sql calls that the schema
 * classifier determines do not mutate the schema and do not delete data run
 * without a consent prompt. Schema-mutating or data-deleting SQL still
 * requires consent.
 */
export function shouldAutoApproveAgentTool(params: {
  toolName: AgentToolName;
  metadata?: SqlConsentMetadata | null;
  autoApproveNonSchemaSql: boolean | undefined;
}): boolean {
  return (
    params.toolName === "execute_sql" &&
    params.metadata?.sqlMutatesSchema === false &&
    params.metadata?.sqlDeletesData === false &&
    params.autoApproveNonSchemaSql === true
  );
}

export function getAgentToolConsent(toolName: AgentToolName): AgentToolConsent {
  const settings = readSettings();
  const stored = settings.agentToolConsents?.[toolName];
  if (stored) {
    return stored;
  }
  return getDefaultConsent(toolName);
}

export function setAgentToolConsent(
  toolName: AgentToolName,
  consent: AgentToolConsent,
): void {
  writeSettings(getAgentToolConsentSettings(toolName, consent));
}

export function getAllAgentToolConsents(): Record<
  AgentToolName,
  AgentToolConsent
> {
  const settings = readSettings();
  const stored = settings.agentToolConsents ?? {};
  const result: Record<string, AgentToolConsent> = {};

  // Start with defaults, override with stored values
  for (const tool of TOOL_DEFINITIONS) {
    const storedConsent = stored[tool.name];
    if (storedConsent) {
      result[tool.name] = storedConsent;
    } else {
      result[tool.name] = getDefaultConsent(tool.name as AgentToolName);
    }
  }

  return result as Record<AgentToolName, AgentToolConsent>;
}

export async function requireAgentToolConsent(
  event: IpcMainInvokeEvent,
  params: {
    chatId: number;
    toolName: AgentToolName;
    confirmation?: "shell-approval" | "shell-review-retry";
    toolDescription?: string | null;
    inputPreview?: string | null;
    metadata?: SqlConsentMetadata | null;
    abortSignal?: AbortSignal;
    subagent?: {
      threadId: string;
      persona: "explorer" | "implementer";
      taskName: string;
    };
  },
): Promise<boolean> {
  const current = getAgentToolConsent(params.toolName);

  if (params.confirmation && params.toolName !== "run_shell")
    throw new DyadError(
      "Shell confirmation requires run_shell",
      DyadErrorKind.Validation,
    );
  if (current === "never" && params.confirmation) return false;
  if (current === "always" && !params.confirmation) return true;
  if (current === "never")
    throw new DyadError(
      "Should not ask for consent for a tool marked as 'never'",
      DyadErrorKind.Internal,
    );

  if (
    !params.confirmation &&
    shouldAutoApproveAgentTool({
      toolName: params.toolName,
      metadata: params.metadata,
      autoApproveNonSchemaSql: readSettings().autoApproveNonSchemaSql,
    })
  ) {
    return true;
  }

  rememberUserInputSubscriber(event.sender);
  const requestId = userInputRegistry.request({
    kind: "agent-consent",
    confirmation: params.confirmation,
    allowAlways: params.confirmation ? false : undefined,
    chatId: params.chatId,
    toolName: params.toolName,
    toolDescription: params.toolDescription,
    inputPreview: params.inputPreview,
    metadata: params.metadata,
    subagent: params.subagent,
    classifier: "none",
  });
  const response = await userInputRegistry.park(requestId, params.abortSignal);
  return response?.kind === "agent-consent" && response.decision !== "decline";
}

// ============================================================================
// Build Agent Tool Set
// ============================================================================

/**
 * Process placeholders in tool args (e.g. $$SUPABASE_CLIENT_CODE$$, $$NEON_CLIENT_CODE$$)
 * Recursively processes all string values in the args object.
 */
async function processArgPlaceholders<T extends Record<string, any>>(
  args: T,
  ctx: AgentContext,
): Promise<T> {
  const argsStr = JSON.stringify(args);
  const hasSupabasePlaceholder = argsStr.includes("$$SUPABASE_CLIENT_CODE$$");
  const hasNeonPlaceholder = argsStr.includes("$$NEON_CLIENT_CODE$$");

  if (!hasSupabasePlaceholder && !hasNeonPlaceholder) {
    return args;
  }

  let supabaseClientCode: string | undefined;
  if (hasSupabasePlaceholder && ctx.supabaseProjectId) {
    supabaseClientCode = await getSupabaseClientCode({
      projectId: ctx.supabaseProjectId,
      organizationSlug: ctx.supabaseOrganizationSlug ?? null,
    });
  }

  let neonClientCode: string | undefined;
  if (hasNeonPlaceholder) {
    if (ctx.neonProjectId) {
      neonClientCode = getNeonClientCode(ctx.frameworkType);
    } else {
      neonClientCode = "";
    }
  }

  // Process all string values in args
  const processValue = (value: any): any => {
    if (typeof value === "string") {
      let result = value;
      if (supabaseClientCode) {
        result = result.replace(
          /\$\$SUPABASE_CLIENT_CODE\$\$/g,
          supabaseClientCode,
        );
      }
      if (neonClientCode !== undefined) {
        result = result.replace(/\$\$NEON_CLIENT_CODE\$\$/g, neonClientCode);
      }
      return result;
    }
    if (Array.isArray(value)) {
      return value.map(processValue);
    }
    if (value && typeof value === "object") {
      const result: Record<string, any> = {};
      for (const [k, v] of Object.entries(value)) {
        result[k] = processValue(v);
      }
      return result;
    }
    return value;
  };

  return processValue(args) as T;
}

/**
 * Convert our ToolResult to AI SDK format
 */
function convertToolResultForAiSdk(
  result: ToolResult,
): LanguageModelV3ToolResultOutput {
  if (typeof result === "string") {
    return { type: "text", value: result };
  }
  throw new DyadError(
    `Unsupported tool result type: ${typeof result}`,
    DyadErrorKind.Internal,
  );
}

function serializeActivityOutput(result: ToolResult): string {
  return typeof result === "string" ? result : JSON.stringify(result);
}

export interface BuildAgentToolSetOptions {
  /**
   * Selects the fail-closed surface for the active writable mode. Build mode
   * intentionally exposes only the tools needed to create an app without
   * Agent-only orchestration, Engine, diagnostics, or verification tools.
   */
  toolProfile?: "agent" | "build";
  /**
   * If true, exclude tools that modify state (files, database, etc.).
   * Used for read-only modes like "ask" mode.
   */
  readOnly?: boolean;
  /**
   * If true, only include tools that are allowed in plan mode.
   * Plan mode has access to read-only tools plus planning-specific tools.
   */
  planModeOnly?: boolean;
  /**
   * If true, exclude Pro-only tools.
   * Used for basic agent mode where some tools may not be available.
   */
  basicAgentMode?: boolean;
  /**
   * If true, exclude tools that call separate Dyad Engine endpoints.
   * The free Pro model only uses the engine chat-completions endpoint.
   */
  freeModelMode?: boolean;
  /**
   * If false, exclude app blueprint tools (write_app_blueprint).
   */
  enableAppBlueprint?: boolean;
}

export const BUILD_MODE_TOOL_NAMES = [
  "write_file",
  "search_replace",
  "copy_file",
  "delete_file",
  "rename_file",
  "add_dependency",
  "execute_sql",
  "read_file",
  "list_files",
  "grep",
  "get_supabase_project_info",
  "get_neon_project_info",
  "get_database_table_schema",
  "set_chat_summary",
  "add_integration",
  "enable_nitro",
  "restart_app",
  "reinstall_and_restart_app",
  "update_todos",
  "read_guide",
  "planning_questionnaire",
  "write_app_blueprint",
] as const satisfies readonly AgentToolName[];

const BUILD_MODE_TOOL_NAME_SET = new Set<AgentToolName>(BUILD_MODE_TOOL_NAMES);

export async function estimateAgentToolTokens({
  appPath,
  toolProfile = "agent",
  readOnly = false,
  planModeOnly = false,
  basicAgentMode = false,
  freeModelMode = false,
  enableAppBlueprint,
  isDyadPro,
  frameworkType,
  supabaseProjectId,
  supabaseProviderToolsAvailable = false,
  neonProjectId,
  neonActiveBranchId,
  neonProviderToolsAvailable = false,
  testingEnabled = false,
  canUseExplorerSubagent = false,
  canUseImplementerSubagent = false,
  canUseAdvancedSubagentTools = false,
  runTypeScriptForWholeProject = false,
  preCommitHookAvailable = false,
  reinstallAndRestartAppToolAvailable = true,
  mcpToolDefs = [],
  suggestablePlugins = [],
}: {
  toolProfile?: "agent" | "build";
  readOnly?: boolean;
  planModeOnly?: boolean;
  basicAgentMode?: boolean;
  freeModelMode?: boolean;
  enableAppBlueprint: boolean;
  isDyadPro: boolean;
  frameworkType: AgentContext["frameworkType"];
  supabaseProjectId: string | null;
  supabaseProviderToolsAvailable?: boolean;
  neonProjectId: string | null;
  neonActiveBranchId: string | null;
  neonProviderToolsAvailable?: boolean;
  testingEnabled?: boolean;
  canUseExplorerSubagent?: boolean;
  canUseImplementerSubagent?: boolean;
  canUseAdvancedSubagentTools?: boolean;
  runTypeScriptForWholeProject?: boolean;
  preCommitHookAvailable?: boolean;
  reinstallAndRestartAppToolAvailable?: boolean;
  mcpToolDefs?: McpToolDef[];
  suggestablePlugins?: SuggestablePlugin[];
  appPath?: string;
}): Promise<number> {
  const estimateContext = {
    appPath,
    freeModelMode,
    isDyadPro,
    frameworkType,
    supabaseProjectId,
    supabaseProviderToolsAvailable,
    neonProjectId,
    neonActiveBranchId,
    neonProviderToolsAvailable,
    referencedApps: new Map(),
    testingEnabled,
    canUseExplorerSubagent,
    canUseImplementerSubagent,
    canUseAdvancedSubagentTools,
    runTypeScriptForWholeProject,
    preCommitHookAvailable,
    reinstallAndRestartAppToolAvailable,
    suggestablePlugins,
    sandboxWriteFileHostEnabled: !readOnly && !planModeOnly,
  } as AgentContext;
  const options: BuildAgentToolSetOptions = {
    toolProfile,
    readOnly,
    planModeOnly,
    basicAgentMode,
    freeModelMode,
    enableAppBlueprint,
  };
  const mcpInSandboxEnabled =
    toolProfile !== "build" &&
    !readOnly &&
    !planModeOnly &&
    shouldIncludeTool(executeSandboxScriptTool, estimateContext, options);
  estimateContext.mcpToolsEnabled = mcpInSandboxEnabled;
  estimateContext.mcpToolDefs = mcpToolDefs;
  estimateContext.isMcpToolSearchAvailable =
    mcpInSandboxEnabled &&
    !!readSettings().enableMcpToolSearch &&
    estimateMcpInlineTokens(mcpToolDefs) > getMcpInlineTokenThreshold();

  let declarations = await Promise.all(
    TOOL_DEFINITIONS.filter((definition) =>
      shouldIncludeTool(definition, estimateContext, options),
    ).map(async (definition) => ({
      type: "function" as const,
      name: definition.name,
      description: resolveToolDescription(definition, estimateContext),
      inputSchema: await asSchema(
        definition.getInputSchema?.(estimateContext) ?? definition.inputSchema,
      ).jsonSchema,
    })),
  );

  let useMcpToolSearch = estimateContext.isMcpToolSearchAvailable;
  if (
    useMcpToolSearch &&
    !declarations.some((declaration) => declaration.name === "search_mcp_tools")
  ) {
    useMcpToolSearch = false;
    declarations = declarations.filter(
      (declaration) => declaration.name !== "get_mcp_tool_schema",
    );
  }
  const hasGetSchemaTool = declarations.some(
    (declaration) => declaration.name === "get_mcp_tool_schema",
  );
  const sandboxDeclaration = declarations.find(
    (declaration) => declaration.name === "execute_sandbox_script",
  );
  if (sandboxDeclaration) {
    sandboxDeclaration.description = await buildExecuteSandboxScriptDescription(
      mcpToolDefs,
      {
        useSearch: useMcpToolSearch,
        hasGetSchemaTool,
        includeWriteFile: !!estimateContext.sandboxWriteFileHostEnabled,
      },
    );
  }

  if (
    toolProfile !== "build" &&
    !readOnly &&
    !planModeOnly &&
    !mcpInSandboxEnabled
  ) {
    const uniqueMcpTools = new Map(
      mcpToolDefs.map((definition) => [definition.toolKey, definition]),
    );
    declarations.push(
      ...[...uniqueMcpTools.values()].map((definition) => ({
        type: "function" as const,
        name: definition.toolKey,
        description: definition.description ?? "",
        inputSchema: definition.inputSchema,
      })),
    );
  }

  return estimateTokens(JSON.stringify(declarations));
}

export function estimateBuildModeToolTokens(
  options: Omit<Parameters<typeof estimateAgentToolTokens>[0], "toolProfile">,
): Promise<number> {
  return estimateAgentToolTokens({ ...options, toolProfile: "build" });
}

/**
 * Tools that should ONLY be available in plan mode (excluded from normal agent mode).
 * Note: planning_questionnaire is intentionally omitted so it's available in pro agent mode too.
 */
const PLAN_MODE_ONLY_TOOLS = new Set(["write_plan", "exit_plan"]);

/**
 * Planning-specific tools that are allowed in plan mode despite modifying state.
 * Superset of PLAN_MODE_ONLY_TOOLS plus tools that participate in planning
 * but are also available in normal (pro) agent mode.
 */
const PLANNING_SPECIFIC_TOOLS = new Set([
  ...PLAN_MODE_ONLY_TOOLS,
  "planning_questionnaire",
]);

/**
 * Tools only available in Pro agent mode (excluded from basic agent mode).
 */
const PRO_AGENT_ONLY_TOOLS = new Set<string>();

/**
 * Tools that are part of the app blueprint flow. Excluded when the feature
 * is disabled via the Workflow setting or once the per-app blueprint flag is
 * cleared.
 */
const APP_BLUEPRINT_TOOLS = new Set<string>(["write_app_blueprint"]);

/**
 * Tools that enforce the app-blueprint precondition themselves at the
 * capability layer instead of at the wrapper level. execute_sandbox_script
 * is state-modifying only because it MAY expose the write_file host
 * function; gating the whole tool would also block read-only inspection
 * scripts and MCP host calls during blueprint drafting, so the gate runs
 * inside the write_file host capability (see buildWriteFileCapability in
 * execute_sandbox_script.ts).
 */
const CAPABILITY_GATED_BLUEPRINT_TOOLS = new Set<string>([
  "execute_sandbox_script",
]);

function toolModifiesState(
  tool: (typeof TOOL_DEFINITIONS)[number],
  ctx: AgentContext,
): boolean {
  if (typeof tool.modifiesState === "function") {
    return tool.modifiesState(ctx);
  }
  return tool.modifiesState === true;
}

function toolAllowedInReadOnlyModes(
  tool: (typeof TOOL_DEFINITIONS)[number],
  ctx: AgentContext,
): boolean {
  if (typeof tool.allowInReadOnlyModes === "function") {
    return tool.allowInReadOnlyModes(ctx);
  }
  return tool.allowInReadOnlyModes === true;
}

/**
 * Whether a tool belongs in this turn's tool set. Single source of truth for
 * inclusion, so a caller that needs the answer before the set is built (e.g. a
 * tool whose availability depends on another tool) can ask the same question
 * the builder does.
 */
export function shouldIncludeTool(
  tool: (typeof TOOL_DEFINITIONS)[number],
  ctx: AgentContext,
  options: BuildAgentToolSetOptions = {},
  phase: "discovery" | "invocation" = "discovery",
): boolean {
  if (
    tool.name === "run_shell" &&
    !isShellExperimentAvailable({
      settings: ctx.inferenceSettings ?? readSettings(),
      isDyadPro: ctx.isDyadPro,
      ...options,
      freeModelMode: options.freeModelMode ?? ctx.freeModelMode,
      isChild: !!ctx.mutationActivityOwner?.persona,
    })
  )
    return false;
  if (getAgentToolConsent(tool.name) === "never") {
    return false;
  }
  if (
    options.toolProfile === "build" &&
    !BUILD_MODE_TOOL_NAME_SET.has(tool.name)
  ) {
    return false;
  }
  // In plan mode, skip state-modifying tools unless they're planning-specific.
  if (
    options.planModeOnly &&
    toolModifiesState(tool, ctx) &&
    !toolAllowedInReadOnlyModes(tool, ctx) &&
    !PLANNING_SPECIFIC_TOOLS.has(tool.name)
  ) {
    return false;
  }
  // Skip plan-mode-only tools when NOT in plan mode.
  if (!options.planModeOnly && PLAN_MODE_ONLY_TOOLS.has(tool.name)) {
    return false;
  }
  // Skip Pro-only tools in basic agent mode.
  if (options.basicAgentMode && PRO_AGENT_ONLY_TOOLS.has(tool.name)) {
    return false;
  }
  if (options.freeModelMode && tool.usesEngineEndpoint) {
    return false;
  }
  if (tool.subagentOnly && !ctx.isDyadPro) {
    return false;
  }
  // search_chats is superseded by the explore_chat_history sub-agent wherever
  // the explorer is present (Pro): broad recall routes through the explorer
  // and targeted drill-down through read_chat. When the explorer is filtered
  // out (non-Pro, free-model mode), direct search remains available so chat
  // history stays reachable.
  if (
    phase === "discovery" &&
    tool.name === "search_chats" &&
    shouldIncludeTool(exploreChatHistoryTool, ctx, options)
  ) {
    return false;
  }
  // Skip app blueprint tools when the feature is disabled.
  if (
    options.enableAppBlueprint === false &&
    APP_BLUEPRINT_TOOLS.has(tool.name)
  ) {
    return false;
  }
  // In read-only mode, skip tools that modify state.
  if (
    options.readOnly &&
    toolModifiesState(tool, ctx) &&
    !toolAllowedInReadOnlyModes(tool, ctx)
  ) {
    return false;
  }
  if (phase === "discovery" && tool.isDiscoverable && !tool.isDiscoverable(ctx))
    return false;
  if (tool.isEnabled) {
    const enabled = tool.isEnabled(ctx);
    if (!enabled) {
      return false;
    }
  }
  return true;
}

/** Refresh reviewer context from tools the agent can actually invoke. */
export function refreshShellReviewToolInventory(
  ctx: AgentContext,
  registeredTools: Record<string, { description?: string }>,
  options: BuildAgentToolSetOptions = {},
): void {
  if (!ctx.shellReviewContext) return;
  const definitions = new Map<string, (typeof TOOL_DEFINITIONS)[number]>(
    TOOL_DEFINITIONS.map((tool) => [tool.name, tool]),
  );
  for (const entry of ctx.shellReviewContext.tools) {
    const definition = definitions.get(entry.name);
    const registered = registeredTools[entry.name];
    entry.available =
      !!registered &&
      (!definition ||
        shouldIncludeTool(definition, ctx, options, "invocation"));
    if (registered?.description) entry.description = registered.description;
  }
  for (const [name, tool] of Object.entries(registeredTools)) {
    if (
      name !== "run_shell" &&
      !ctx.shellReviewContext.tools.some((entry) => entry.name === name)
    ) {
      const definition = definitions.get(name);
      ctx.shellReviewContext.tools.push({
        name,
        description: tool.description ?? "",
        available:
          !definition ||
          shouldIncludeTool(definition, ctx, options, "invocation"),
      });
    }
  }
}

/** Build ToolSet for AI SDK from tool definitions. */
export function buildAgentToolSet(
  ctx: AgentContext,
  options: BuildAgentToolSetOptions = {},
) {
  const toolSet: Record<string, any> = {};
  if (shouldIncludeTool(runShellTool, ctx, options)) {
    ctx.shellReviewContext = {
      tools: TOOL_DEFINITIONS.filter((tool) => tool.name !== "run_shell").map(
        (tool) => ({
          name: tool.name,
          description: resolveToolDescription(tool, ctx),
          available: false, // Resolved from the callable tool set after registration.
        }),
      ),
      history: [],
    };
  }

  for (const tool of TOOL_DEFINITIONS) {
    if (!shouldIncludeTool(tool, ctx, options)) {
      continue;
    }

    toolSet[tool.name] = {
      description: resolveToolDescription(tool, ctx),
      inputSchema: tool.getInputSchema?.(ctx) ?? tool.inputSchema,
      execute: async (
        args: any,
        executionOptions?: { toolCallId?: string },
      ) => {
        const toolCallId = executionOptions?.toolCallId;
        let presentationXml = "";
        let executionStarted = false;
        const invocationCtx =
          toolCallId && ctx.onToolActivity
            ? {
                ...ctx,
                onXmlStream: (xml: string) => {
                  presentationXml = xml;
                  recordStreamingToolActivity(ctx, {
                    toolCallId,
                    toolName: tool.name,
                    status: "pending",
                    presentationXml: xml,
                  });
                },
                onXmlComplete: (xml: string) => {
                  presentationXml = xml;
                  recordStreamingToolActivity(ctx, {
                    toolCallId,
                    toolName: tool.name,
                    status: "pending",
                    presentationXml: xml,
                  });
                },
              }
            : ctx;
        try {
          // The SDK is not the authority for validation: MCP and sandbox
          // adapters must enter the same invocation boundary.
          if (!shouldIncludeTool(tool, invocationCtx, options, "invocation")) {
            throw new DyadError(
              "Tool is unavailable in this turn",
              DyadErrorKind.Precondition,
            );
          }
          const schema = asSchema(
            tool.getInputSchema?.(invocationCtx) ?? tool.inputSchema,
          );
          if (schema.validate) {
            const validated = await schema.validate(args);
            if (!validated.success) throw validated.error;
            args = validated.value;
          }
          const mutationRequiresTracking =
            toolModifiesState(tool, ctx) &&
            (tool.mutationTracking ?? "automatic") === "automatic";
          const processedArgs = await processArgPlaceholders(
            args,
            invocationCtx,
          );

          if (toolCallId && invocationCtx.onToolActivity) {
            presentationXml = tool.buildXml?.(processedArgs, false) ?? "";
            await invocationCtx.onToolActivity({
              toolCallId,
              toolName: tool.name,
              status: "pending",
              presentationXml,
              inputJson: processedArgs,
            });
          }

          // Reject tools that cannot pass the blueprint gate before asking
          // the user for consent. Consent may wait indefinitely, but this
          // precondition is synchronous and independent of mutation admission.
          if (
            toolModifiesState(tool, ctx) &&
            tool.requiresBlueprintApproval !== false &&
            !APP_BLUEPRINT_TOOLS.has(tool.name) &&
            !PLANNING_SPECIFIC_TOOLS.has(tool.name) &&
            !CAPABILITY_GATED_BLUEPRINT_TOOLS.has(tool.name)
          ) {
            assertAppBlueprintApproved({
              toolName: tool.name,
              chatId: invocationCtx.chatId,
              enabled: options.enableAppBlueprint !== false,
            });
          }

          // Consent can wait indefinitely for the user. Resolve it before
          // registering mutation activity so cancellation cannot let delayed
          // consent enter a closed actor generation.
          // Shell resolves consent after classification, including mandatory one-time approval.
          if (tool.name !== "run_shell")
            await requireToolConsentOrThrow(tool, processedArgs, invocationCtx);
          const invoke = async () => {
            if (
              !shouldIncludeTool(tool, invocationCtx, options, "invocation")
            ) {
              throw new DyadError(
                "Tool is no longer available",
                DyadErrorKind.Precondition,
              );
            }
            if (invocationCtx.abortSignal?.aborted) {
              throw new DyadError(
                "This agent run was cancelled.",
                DyadErrorKind.UserCancelled,
              );
            }
            // Track file edit tool usage before execution to capture all attempts
            // (including failures) for retry/fallback telemetry
            trackFileEditTool(invocationCtx, tool.name, processedArgs);
            executionStarted = true;
            const result = await withReferencedAppRead(
              tool.name,
              processedArgs,
              invocationCtx,
              (readCtx) => tool.execute(processedArgs, readCtx),
            );
            recordShellReviewOutcome(ctx, tool.name, processedArgs, { result });

            // Only completed mutations unblock run_tests. Failed tool calls are
            // still present in fileEditTracker for retry/fallback telemetry, but
            // must not masquerade as a code change.
            const didMutate = shouldTrackToolMutation(
              tool,
              processedArgs,
              result,
              invocationCtx,
            );
            trackAppMutation(
              invocationCtx,
              tool.name,
              didMutate,
              didMutate &&
                (await shouldTrackToolFileMutation(
                  tool,
                  processedArgs,
                  result,
                  invocationCtx,
                )),
            );

            if (toolCallId && invocationCtx.onToolActivity) {
              await invocationCtx.onToolActivity({
                toolCallId,
                toolName: tool.name,
                status: "completed",
                presentationXml,
                inputJson: processedArgs,
                outputText: serializeActivityOutput(result),
              });
            }

            return convertToolResultForAiSdk(result);
          };

          return mutationRequiresTracking
            ? await withTrackedMutation(invocationCtx, invoke)
            : await invoke();
        } catch (error) {
          recordShellReviewOutcome(ctx, tool.name, args, {
            error,
            executed: executionStarted,
          });
          const errorMessage = getToolErrorSummary(error);
          const errorDetails = getToolErrorDisplayDetails(error);

          const errorXml = `<dyad-output type="error" message="Tool '${tool.name}' failed: ${escapeXmlAttr(errorMessage)}">${escapeXmlContent(errorDetails)}</dyad-output>`;
          invocationCtx.onXmlComplete(errorXml);
          if (toolCallId && invocationCtx.onToolActivity) {
            await invocationCtx.onToolActivity({
              toolCallId,
              toolName: tool.name,
              status: invocationCtx.abortSignal?.aborted ? "aborted" : "error",
              presentationXml: errorXml,
              inputJson:
                typeof args === "object" && args !== null ? args : undefined,
              error: errorDetails,
            });
          }
          throw error;
        }
      },
    };
  }

  if (ctx.shellReviewContext) {
    ctx.refreshShellReviewTools = () =>
      refreshShellReviewToolInventory(ctx, toolSet, options);
    ctx.refreshShellReviewTools();
  }
  return toolSet;
}

```

## src/pro/main/ipc/handlers/local_agent/tools/execute_sandbox_script.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/execute_sandbox_script.ts

SHA-256: `8a968d0bd71145dce44f0f037db788e2112648142c605c246ecc9362a9008544`

```
import { z } from "zod";
import { isSandboxSupportedPlatform } from "@/ipc/utils/sandbox/execution";
import {
  runSandboxScript,
  SandboxWorkerError,
} from "@/ipc/utils/sandbox/runner";
import type { RemoteCapabilityMap } from "@/ipc/utils/sandbox/remote_capabilities";
import {
  assertAllowedGuestPath,
  assertSandboxWritePathAllowed,
} from "@/ipc/utils/sandbox/capabilities";
import { SANDBOX_SCRIPT_SOURCE_LIMIT_BYTES } from "@/ipc/utils/sandbox/limits";
import { DyadError, DyadErrorKind, isDyadError } from "@/errors/dyad_error";
import { sendTelemetryEvent } from "@/ipc/utils/telemetry";
import { DYAD_MEDIA_DIR_NAME } from "@/ipc/utils/media_path_utils";
import { readSettings } from "@/main/settings";
import type { UserSettings } from "@/lib/schemas";
import { withTrackedMutation } from "../subagents/mutation_activity_tracker";
import { writeFileTool } from "./write_file";
import {
  AgentContext,
  escapeXmlAttr,
  escapeXmlContent,
  ToolDefinition,
} from "./types";
import {
  assertAppBlueprintApproved,
  getToolConsent,
  requireToolConsentOrThrow,
  shouldTrackToolFileMutation,
  trackAppMutation,
  trackFileEditTool,
  shouldTrackToolMutation,
} from "./tool_invocation";
import {
  collectMcpToolDefs,
  buildMcpTypeDefsBlock,
  buildMcpToolNameInventory,
  buildMcpCapabilityMap,
  type McpToolDef,
} from "./mcp_type_defs";

const executeSandboxScriptSchema = z.object({
  script: z
    .string()
    .max(SANDBOX_SCRIPT_SOURCE_LIMIT_BYTES)
    .describe("Sandboxed JavaScript subset source code to execute."),
  description: z
    .string()
    .max(160)
    .optional()
    .describe("One-line human-readable summary of what the script does."),
});

type ExecuteSandboxScriptArgs = z.infer<typeof executeSandboxScriptSchema>;

export function isSandboxScriptExecutionEnabled(
  settings: Pick<UserSettings, "enableSandboxScriptExecution"> | undefined,
): boolean {
  return !!settings?.enableSandboxScriptExecution;
}

function isAttachmentHostCallPath(path: string | undefined): boolean {
  if (!path) {
    return false;
  }
  if (
    path === "attachments" ||
    path === "attachments:" ||
    path.startsWith("attachments:")
  ) {
    return true;
  }
  const normalized = path.replace(/\\/g, "/");
  return (
    normalized === DYAD_MEDIA_DIR_NAME ||
    normalized.startsWith(`${DYAD_MEDIA_DIR_NAME}/`)
  );
}

function buildSandboxFailureMessage(params: {
  script: string;
  errorMessage: string;
}): string {
  return [
    "This script contains unsupported syntax.",
    "",
    "Script:",
    params.script,
    "",
    "Original error:",
    params.errorMessage,
  ].join("\n");
}

function buildScriptXml(params: {
  args: ExecuteSandboxScriptArgs;
  output: string;
  truncated: boolean;
  fullOutputPath?: string;
  executionMs: number;
}): string {
  const payload = JSON.stringify(
    {
      script: params.args.script,
      output: params.output,
    },
    null,
    2,
  );
  const attrs = [
    `description="${escapeXmlAttr(params.args.description ?? "Ran a script")}"`,
    `state="finished"`,
    `truncated="${params.truncated ? "true" : "false"}"`,
    `execution-ms="${escapeXmlAttr(String(params.executionMs))}"`,
  ];
  if (params.fullOutputPath) {
    attrs.push(`full-output-path="${escapeXmlAttr(params.fullOutputPath)}"`);
  }
  return `<dyad-script ${attrs.join(" ")}>${escapeXmlContent(payload)}</dyad-script>`;
}

// Fresh per-call read of the write_file consent so a mid-turn flip to
// "never" takes effect immediately; `ctx.sandboxWriteFileHostEnabled` is the
// turn-scoped view of the same predicate (via shouldIncludeTool).
function isWriteFileHostEnabled(): boolean {
  return getToolConsent(writeFileTool) !== "never";
}

const WRITE_FILE_HOST_DECLARATIONS = `
declare function write_file(
  path: string,
  content: string,
  description?: string,
): Promise<string>;

declare function write_file(args: {
  path: string;
  content: string;
  description?: string;
}): Promise<string>;
`.trimEnd();

// Built-in sandbox host-function base. Used as-is when no MCP servers are
// enabled, and as the lead-in to the MCP-augmented description otherwise.
// Keep MCP-specific framing out of this block — if it lands in front
// of the model when MCP is off, the model will write calls to host
// functions that don't exist.
function buildBuiltInHostFunctionsPreamble({
  includeWriteFile,
}: {
  includeWriteFile: boolean;
}): string {
  return `Run a small program written in a strict, sandboxed subset of JavaScript (MustardScript) to inspect files.

Use this when you need to slice, search, count, aggregate, summarize file contents${includeWriteFile ? ", or write generated content to files" : ""} before answering. Return only the concise value you need.

Supported language surface:
- let/const, functions, closures, arrow functions, async/await, promises, arrays, plain objects, Map, Set, if/switch, loops, break/continue, try/catch/finally, throw, template literals, destructuring, optional chaining, nullish coalescing, JSON, Math, and conservative Array/String/Object/Date/Intl/RegExp helpers.
- Data helpers include Object.groupBy/Map.groupBy, toSorted/toReversed/toSpliced, Set union/intersection/difference, and delete for object properties or array elements. JSON.parse revivers and JSON.stringify replacers/indentation are supported; JSON callbacks must be synchronous and must not call host functions.
- String.prototype.localeCompare and Intl formatting support en-US only (the default); other locale arguments are rejected, not silently ignored. Date local-time methods such as getHours/getDate return UTC values, not the user's local time; Intl date formatting rejects non-UTC time zones. URI encoding/decoding helpers are available, but URL/URLSearchParams and network access are not.
- Top-level await is supported. Top-level return is not supported; return the final expression value instead, e.g. \`const text = await read_file("attachments:data.csv"); text.length;\`.
- The script has no ambient authority. It can only act through the host functions below.

Recommendations:
- Avoid defining nested helper functions in the main function.

Unsupported / unavailable:
- No var, import/export, require, CommonJS, npm packages, Node APIs, browser/DOM APIs, process, module, exports, global, environment variables, subprocesses, network/fetch, fetch, timers, setTimeout, setInterval, eval, Function constructor, with, classes, generators, custom iterator authoring, Symbols, WeakMap, WeakSet, typed arrays, ArrayBuffer, shared memory, atomics, Proxy, accessors, full prototype/property-descriptor semantics, or arbitrary filesystem access.
- \`console.*\` is not available.
- Unsupported syntax or unsupported built-in behavior fails closed with an error. Rewrite using simpler JavaScript when that happens.

Return only plain structured data (strings, numbers, booleans, null/undefined, arrays, and plain objects). Convert Map/Set to arrays or plain objects and BigInt to a string before returning or passing to host functions. Strings and object keys must not contain lone UTF-16 surrogates.

Deleting an array element leaves a hole without changing its length. Host functions receive sparse arrays with those holes preserved, but final JSON output renders holes as null. Prefer dense arrays when returning data or passing it to host functions.

Avoid returning shared references:

\`\`\`
const row = { key: "x", total: 1 };
({ a: row, b: row }); // rejected
\`\`\`

Use cloned/plain rows instead:

\`\`\`
const row = { key: "x", total: 1 };
({
  a: { key: row.key, total: row.total },
  b: { key: row.key, total: row.total }
});
\`\`\`

Host functions:
\`\`\`ts
type ReadFileOptions = {
  start?: number; // zero-indexed byte offset
  length?: number; // max bytes to read
  encoding?: "utf8" | "base64";
};

type FileStats = {
  size: number;
  isText: boolean;
  mtime: string; // ISO timestamp
};

declare function read_file(
  path: string,
  options?: ReadFileOptions,
): Promise<string>;

declare function list_files(dir?: "." | "attachments:" | string): Promise<string[]>;

declare function file_stats(path: string): Promise<FileStats>;
${includeWriteFile ? WRITE_FILE_HOST_DECLARATIONS : ""}
\`\`\`

Paths are app-relative (including \`.dyad/media/<stored-name>\`), or attachment paths like attachments:filename.ext for read_file/list_files/file_stats.${includeWriteFile ? " write_file accepts app-relative paths only, not attachments: paths." : ""} Prefer range reads, filtering, aggregation, and small summaries over returning entire files.`;
}

function buildMcpAddendum(typeDefsBlock: string): string {
  return `

MCP tools can also be invoked from inside the script. Use this when you need to call one or more MCP tools (optionally chaining their results, or combining them with file reads) before answering.
- Each MCP tool invocation may trigger a user consent prompt. A denied call throws.

MCP host functions (TypeScript declarations):
\`\`\`ts
${typeDefsBlock}
\`\`\``;
}

// Search-mode addendum: used when the `enableMcpToolSearch` setting is on.
// Every tool is listed by NAME ONLY (no descriptions or schemas) so the model
// sees what exists without paying for the full declarations. To call a tool it
// pulls the full signature with `get_mcp_tool_schema` (when it recognizes the
// tool) or finds one with `search_mcp_tools` (when it doesn't).
//
// `hasGetSchemaTool` reflects whether `get_mcp_tool_schema` is actually
// registered this turn. A user can set it to `never` in tool permissions,
// which filters it out while search mode stays on; in that case the wording
// must not tell the model to call a tool that isn't available.
function buildMcpSearchAddendum(
  defs: McpToolDef[],
  hasGetSchemaTool: boolean,
): string {
  const inventory = buildMcpToolNameInventory(defs);
  const howToUse = hasGetSchemaTool
    ? `1. If you recognize the tool you need, call \`get_mcp_tool_schema\` with its name(s) to get its description and full TypeScript declaration.
2. If you are not sure which tool you need, call \`search_mcp_tools\` with keywords (optionally a \`server\`) to find candidates and get their declarations.
3. Call the declared host functions inside this script, exactly as declared.`
    : `1. Call \`search_mcp_tools\` with keywords (optionally a \`server\`) to get the TypeScript declarations of the tools you need.
2. Call the declared host functions inside this script, exactly as declared.`;
  const inventoryHeading = hasGetSchemaTool
    ? "Available MCP tools (call get_mcp_tool_schema for a tool's signature):"
    : "Available MCP tools (search for one with search_mcp_tools to get its signature):";
  return `

MCP tools can also be invoked from inside the script. The tools available on each connected server are listed below by name only. To use one:
${howToUse}
- Each MCP tool invocation may trigger a user consent prompt. A denied call throws.

${inventoryHeading}
\`\`\`
${inventory}
\`\`\``;
}

/**
 * Build the full tool description, appending the MCP host-function
 * declarations and usage notes when any MCP server is enabled. The
 * caller passes in the per-turn defs collected by the local-agent
 * handler; the standalone `collectMcpToolDefs()` fallback is only for
 * callers that don't go through the handler. When no MCP defs are
 * available the description carries no MCP framing at all, so the
 * model does not try to call host functions that don't exist (e.g. in
 * read-only / plan-only turns).
 */
export async function buildExecuteSandboxScriptDescription(
  precomputedDefs: McpToolDef[] | undefined,
  options: {
    useSearch?: boolean;
    hasGetSchemaTool?: boolean;
    /**
     * Whether to advertise the write_file host function. Required (no
     * settings-derived default) because only the caller knows the turn
     * context — a settings-only fallback could advertise write_file in a
     * read-only turn where `buildRemoteCapabilities` never injects it.
     */
    includeWriteFile: boolean;
  },
): Promise<string> {
  const defs = precomputedDefs ?? (await collectMcpToolDefs());
  const builtInHostFunctionsPreamble = buildBuiltInHostFunctionsPreamble({
    includeWriteFile: options.includeWriteFile,
  });
  if (defs.length === 0) {
    return builtInHostFunctionsPreamble;
  }
  // Search mode: list tool names and point the model at the discovery tools
  // instead of inlining every tool's declarations. `hasGetSchemaTool` defaults
  // to true since get_mcp_tool_schema is normally registered alongside search;
  // the handler passes false when tool permissions have filtered it out.
  if (options.useSearch) {
    return (
      builtInHostFunctionsPreamble +
      buildMcpSearchAddendum(defs, options.hasGetSchemaTool ?? true)
    );
  }
  return (
    builtInHostFunctionsPreamble + buildMcpAddendum(buildMcpTypeDefsBlock(defs))
  );
}

export const executeSandboxScriptTool: ToolDefinition<ExecuteSandboxScriptArgs> =
  {
    name: "execute_sandbox_script",
    description:
      "Run a MustardScript program in a sandbox. Supports file inspection, file writes, and MCP tool calls.",
    inputSchema: executeSandboxScriptSchema,
    defaultConsent: "always",
    modifiesState: (ctx) => ctx.sandboxWriteFileHostEnabled === true,
    // The sandbox delegates mutations to capability functions. Each capability
    // owns admission so a script can mix read-only work with writes and MCP
    // calls without trying to re-enter the non-reentrant app mutation lock.
    mutationTracking: "internal",

    isEnabled: () =>
      isSandboxSupportedPlatform() &&
      isSandboxScriptExecutionEnabled(readSettings()),

    getConsentPreview: (args) =>
      args.description?.trim() || "Run a sandboxed script",

    execute: async (args: ExecuteSandboxScriptArgs, ctx: AgentContext) => {
      // Every script runs on the worker thread; a CPU-bound guest on the main
      // thread would freeze the app (#4728). Main-thread-only host functions
      // are served to the worker over its message port by the runner.
      const executionThread = "worker";
      const observeHostCall = ({ path }: { path?: string }) => {
        if (isAttachmentHostCallPath(path)) {
          ctx.onAttachmentAccess?.();
        }
      };
      try {
        const result = await runSandboxScript({
          appPath: ctx.appPath,
          script: args.script,
          onHostCall: observeHostCall,
          remoteCapabilities: buildRemoteCapabilities(ctx),
          signal: ctx.abortSignal,
        });

        ctx.onXmlComplete(
          buildScriptXml({
            args,
            output: result.value,
            truncated: result.truncated,
            fullOutputPath: result.fullOutputPath,
            executionMs: result.executionMs,
          }),
        );

        sendTelemetryEvent("sandbox.script.completed", {
          chatId: ctx.chatId,
          appId: ctx.appId,
          executionMs: result.executionMs,
          truncated: result.truncated,
          executionThread,
        });

        if (result.truncated) {
          sendTelemetryEvent("sandbox.script.truncated", {
            chatId: ctx.chatId,
            appId: ctx.appId,
            fullOutputPath: result.fullOutputPath,
            executionThread,
          });
        }

        return JSON.stringify(result);
      } catch (error) {
        const errorMessage =
          error instanceof Error ? error.message : String(error);
        sendTelemetryEvent(
          errorMessage.includes("timed out")
            ? "sandbox.script.timeout"
            : "sandbox.script.failed",
          {
            chatId: ctx.chatId,
            appId: ctx.appId,
            error: errorMessage,
            executionThread,
          },
        );
        // The worker itself failed, so the script is not at fault.
        if (error instanceof SandboxWorkerError) {
          throw error;
        }
        throw new DyadError(
          buildSandboxFailureMessage({
            script: args.script,
            errorMessage,
          }),
          isDyadError(error) ? error.kind : DyadErrorKind.Validation,
        );
      }
    },
  };

function parseWriteFileHostArgs(
  pathOrArgs: unknown,
  content?: unknown,
  description?: unknown,
) {
  const args =
    pathOrArgs !== null &&
    typeof pathOrArgs === "object" &&
    !Array.isArray(pathOrArgs)
      ? pathOrArgs
      : { path: pathOrArgs, content, description };
  let parsed: z.infer<typeof writeFileTool.inputSchema>;
  try {
    parsed = writeFileTool.inputSchema.parse(args);
  } catch (error) {
    if (error instanceof z.ZodError) {
      throw new DyadError(
        `Invalid write_file arguments: ${error.issues.map((issue) => issue.message).join("; ")}`,
        DyadErrorKind.Validation,
      );
    }
    throw error;
  }
  if (parsed.path.startsWith("attachments:")) {
    throw new DyadError(
      "write_file cannot write attachment paths from sandbox scripts.",
      DyadErrorKind.Validation,
    );
  }
  assertAllowedGuestPath(parsed.path);
  return parsed;
}

function buildWriteFileCapability(ctx: AgentContext) {
  return async (
    pathOrArgs: unknown,
    content?: unknown,
    description?: unknown,
  ) => {
    const args = parseWriteFileHostArgs(pathOrArgs, content, description);
    if (!isWriteFileHostEnabled()) {
      throw new DyadError(
        "write_file is disabled in agent tool permissions.",
        DyadErrorKind.Precondition,
      );
    }
    // Same precondition the tool-set wrapper enforces for state-modifying
    // tools. Enforced here at the capability layer (execute_sandbox_script
    // is exempted from the wrapper-level gate) so read-only scripts and MCP
    // host calls keep working while the blueprint is being drafted.
    assertAppBlueprintApproved({
      toolName: writeFileTool.name,
      chatId: ctx.chatId,
      enabled: ctx.enableAppBlueprint !== false,
    });
    // Reads enforce realpath containment in their sandbox helpers; writes
    // must too, or a symlinked directory inside the app could redirect the
    // write outside it.
    await assertSandboxWritePathAllowed({
      appPath: ctx.appPath,
      guestPath: args.path,
    });
    await requireToolConsentOrThrow(writeFileTool, args, ctx);

    return withTrackedMutation(ctx, async () => {
      trackFileEditTool(ctx, writeFileTool.name, args);
      const result = await writeFileTool.execute(args, ctx);
      // Honor the tool's mutation predicate exactly like the main
      // tool_definitions.ts path, so mutation tracking stays consistent if
      // write_file ever gains a shouldTrackMutation predicate.
      const didMutate = shouldTrackToolMutation(
        writeFileTool,
        args,
        result,
        ctx,
      );
      trackAppMutation(
        ctx,
        writeFileTool.name,
        didMutate,
        didMutate &&
          (await shouldTrackToolFileMutation(writeFileTool, args, result, ctx)),
      );
      const xml = writeFileTool.buildXml?.(args, true);
      if (xml) {
        ctx.onXmlComplete(xml);
      }
      return result;
    });
  };
}

/**
 * Host functions that must execute on the main process: write_file (consent,
 * blueprint gating, mutation tracking, UI cards) and MCP tools (client
 * registry, consent prompts). The runner serves them to the worker over its
 * message port; the file host functions are built inside the worker itself.
 */
function buildRemoteCapabilities(ctx: AgentContext): RemoteCapabilityMap {
  // Only inject MCP host functions when the caller has opted in for
  // this turn (set by `local_agent_handler`). Skipping here keeps
  // read-only and plan-mode turns from exposing MCP tools through
  // the sandbox even if the model invents a host-function name.
  //
  // The handler populates `ctx.mcpToolDefs` with the same defs used
  // to build the dynamic tool description, so the prompt and the
  // capability map can never disagree about which tools exist.
  const defs: McpToolDef[] = ctx.mcpToolsEnabled ? (ctx.mcpToolDefs ?? []) : [];
  const writeFileCaps: RemoteCapabilityMap =
    ctx.sandboxWriteFileHostEnabled === true && isWriteFileHostEnabled()
      ? { write_file: buildWriteFileCapability(ctx) }
      : {};
  const mcpCaps = buildMcpCapabilityMap({
    event: ctx.event,
    ctx,
    defs,
  });
  return {
    ...mcpCaps,
    ...writeFileCaps,
  };
}

```

## src/pro/main/ipc/handlers/local_agent/tools/exit_plan.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/exit_plan.ts

SHA-256: `e0009eefea960d119dfae0e6f4cc660c039e685e5f2ded564c55151e1f14c80e`

```
import { z } from "zod";
import log from "electron-log";
import { eq } from "drizzle-orm";
import { ToolDefinition, AgentContext } from "./types";
import { safeSend } from "@/ipc/utils/safe_sender";
import { DyadError, DyadErrorKind } from "@/errors/dyad_error";
import { db } from "@/db";
import { apps } from "@/db/schema";
import { startPlanHandoffFromMain } from "@/ipc/services/plan_handoff_service";
import { readPlanFromDisk } from "@/ipc/handlers/planPersistence";
import { userInputRegistry } from "@/user_input/main";
import { serializePlanDocument } from "@/plan_handoff/transport";

const logger = log.scope("exit_plan");

const exitPlanSchema = z.object({
  confirmation: z
    .boolean()
    .describe(
      "Whether the user has accepted the plan. Must be true to proceed.",
    ),
});

const DESCRIPTION = `
Exit planning mode after the user has accepted the implementation plan.

IMPORTANT: Only use this tool when:
1. A plan has been presented using the write_plan tool
2. The user has EXPLICITLY accepted the plan (said "yes", "accept", "looks good", etc.)
3. You are ready to begin implementation

This will:
- Switch to Agent mode for implementation
- Change the preview panel back to app preview
- Begin the implementation phase

Do NOT use this tool if:
- The user has requested changes to the plan
- The user has asked questions about the plan
- No plan has been presented yet

Example usage after user says "Looks good, let's build it!":
{
  "confirmation": true
}
`;

export const exitPlanTool: ToolDefinition<z.infer<typeof exitPlanSchema>> = {
  name: "exit_plan",
  description: DESCRIPTION,
  inputSchema: exitPlanSchema,
  defaultConsent: "always",
  modifiesState: true,

  getConsentPreview: () => "Exit plan mode and start implementation",

  buildXml: (args) => {
    if (!args.confirmation) return undefined;

    return `<dyad-exit-plan></dyad-exit-plan>`;
  },

  execute: async (args, ctx: AgentContext) => {
    if (!args.confirmation) {
      throw new DyadError(
        "User must confirm the plan before exiting plan mode",
        DyadErrorKind.Precondition,
      );
    }

    logger.log("Exiting plan mode, transitioning to implementation");
    // A model-authored boolean is not independent user approval. The normal
    // Plan panel accepts directly; conversational acceptance requires a
    // one-shot human decision on this exact persisted version.
    const plan = await readPlanFromDisk({
      appPath: ctx.appPath,
      chatId: ctx.chatId,
    });
    const acceptedVersion = serializePlanDocument(plan);
    const requestId = userInputRegistry.request({
      kind: "agent-consent",
      chatId: ctx.chatId,
      toolName: "Implement plan",
      toolDescription: plan.title,
      inputPreview: plan.content,
      allowAlways: false,
      classifier: "none",
    });
    const response = await userInputRegistry.park(requestId, ctx.abortSignal);
    if (
      response?.kind !== "agent-consent" ||
      response.decision === "decline" ||
      ctx.abortSignal?.aborted
    )
      throw new DyadError(
        "Plan acceptance cancelled",
        DyadErrorKind.UserCancelled,
      );
    if (
      serializePlanDocument(
        await readPlanFromDisk({ appPath: ctx.appPath, chatId: ctx.chatId }),
      ) !== acceptedVersion
    )
      throw new DyadError(
        "The plan changed. Review and accept the current version.",
        DyadErrorKind.Precondition,
      );

    try {
      await db
        .update(apps)
        .set({ needsAppBlueprint: false })
        .where(eq(apps.id, ctx.appId));
    } catch (error) {
      logger.warn(
        `Failed to clear needsAppBlueprint for app ${ctx.appId} on plan exit`,
        error,
      );
    }

    await startPlanHandoffFromMain({
      sourceChatId: ctx.chatId,
      appId: ctx.appId,
      appPath: ctx.appPath,
      acceptInNewChat: ctx.planAcceptInNewChat ?? false,
      senderWebContentsId: ctx.event.sender.id,
    });

    // Compatibility notification only. Main has already admitted the handoff;
    // renderer loss cannot prevent implementation from starting.
    safeSend(ctx.event.sender, "plan:exit", {
      chatId: ctx.chatId,
      appId: ctx.appId,
    });

    return "Plan accepted. Switching to Agent mode to begin implementation. The agreed plan will guide the implementation process.";
  },
};

```

## src/pro/main/ipc/handlers/local_agent/tools/get_mcp_tool_schema.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/get_mcp_tool_schema.ts

SHA-256: `5172086fa6345470d2ea370fcd9446513f4af8459d2b4f8d2fab1b17f215eb50`

```
import { z } from "zod";
import {
  ToolDefinition,
  AgentContext,
  escapeXmlAttr,
  escapeXmlContent,
} from "./types";
import { buildMcpTypeDefsBlock, resolveMcpToolDefs } from "./mcp_type_defs";

const getMcpToolSchemaSchema = z.object({
  tools: z
    .array(z.string())
    .min(1)
    .describe(
      "Names of the MCP tools to fetch full TypeScript signatures for, as " +
        "listed in execute_sandbox_script (e.g. ['github__issue_write']).",
    ),
});

type GetMcpToolSchemaArgs = z.infer<typeof getMcpToolSchemaSchema>;

/**
 * Return the description and full TypeScript `declare function` signature
 * (input schema included) for named MCP tools. The search-mode
 * `execute_sandbox_script` description lists every tool by name only; the
 * model calls this to get the details of the tool(s) it intends to use before
 * calling them as host functions. `search_mcp_tools` remains available for
 * when the name list isn't enough to know which tool fits.
 *
 * Only registered when MCP-in-sandbox is active AND the `enableMcpToolSearch`
 * setting is on (the same setting that lists tool names and registers
 * `search_mcp_tools`).
 */
export const getMcpToolSchemaTool: ToolDefinition<GetMcpToolSchemaArgs> = {
  name: "get_mcp_tool_schema",
  description:
    "Get the description and full TypeScript signature of MCP tools by name. " +
    "The MCP tools listed in execute_sandbox_script show only names; call " +
    "this to get a tool's description and input schema before calling it as " +
    "a host function inside a script. If a name is shared by tools on more " +
    "than one server, the signatures for all of them are returned.",
  inputSchema: getMcpToolSchemaSchema,
  defaultConsent: "always",

  // Only registered in search mode; see ctx.isMcpToolSearchAvailable.
  isEnabled: (ctx) => !!ctx.isMcpToolSearchAvailable,

  getConsentPreview: (args) =>
    `Get schema for MCP tool(s): ${args.tools.join(", ")}`,

  buildXml: (args, isComplete) => {
    if (isComplete) return undefined;
    // buildXml runs on partial, unvalidated args mid-stream; `tools` may not be
    // an array yet, so guard before joining.
    const tools = Array.isArray(args.tools) ? args.tools : [];
    if (tools.length === 0) return undefined;
    return `<dyad-mcp-tool-schema tools="${escapeXmlAttr(tools.join(", "))}">Loading...`;
  },

  execute: async (args: GetMcpToolSchemaArgs, ctx: AgentContext) => {
    const finish = (result: string) => {
      ctx.onXmlComplete(
        `<dyad-mcp-tool-schema tools="${escapeXmlAttr(args.tools.join(", "))}">${escapeXmlContent(result)}</dyad-mcp-tool-schema>`,
      );
      return result;
    };

    // No defs means the handler's collection failed and the sandbox has no MCP
    // host functions this turn, so don't hand the model tools it can't call.
    if (ctx.mcpToolDefs === undefined) {
      return finish("MCP tools are temporarily unavailable. Try again.");
    }

    const { found, missing } = resolveMcpToolDefs(ctx.mcpToolDefs, args.tools);

    if (found.length === 0) {
      return finish(
        `No MCP tool matched [${args.tools.join(", ")}]. Use the names exactly ` +
          `as listed in execute_sandbox_script, or search_mcp_tools to find one.`,
      );
    }

    const block = buildMcpTypeDefsBlock(found);
    const missingNote =
      missing.length > 0 ? `\n\n// No match for: ${missing.join(", ")}` : "";

    return finish(
      `Signature(s) for ${found.length} MCP tool(s). Call these as host ` +
        `functions inside execute_sandbox_script:\n\n${block}${missingNote}`,
    );
  },
};

```

## src/pro/main/ipc/handlers/local_agent/tools/run_shell.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/run_shell.ts

SHA-256: `8234e10d59b152e608065f0e8bd786ec7c25a7ca4fac69fcc9b8f321db237a52`

```
import { runningApps } from "@/ipc/utils/process_manager";
import { z } from "zod";
import { readSettings } from "@/main/settings";
import { isDyadProEnabled } from "@/lib/schemas";
import {
  isShellExperimentAvailable,
  shellExecutionGuidance,
} from "@/shared/shell_capability";
import { withTrackedMutation } from "../subagents/mutation_activity_tracker";
import {
  appOperationCoordinator,
  readAppResource,
  type AppOperationRequest,
} from "@/ipc/services/app_operation_coordinator";
import { reviewShellCommand } from "../shell_review";
import {
  runShellProcess,
  maxShellCommandLength,
  type ShellProcessResult,
} from "./shell_process";
import { trackWorkspaceMutation } from "./tool_invocation";
import { serializeShellResultForModel } from "./shell_model_result";
import {
  tryGetGitStateFingerprint,
  tryCollectSupabaseFunctionEntryPoints,
  scheduleHookGeneratedFileSideEffects,
  deleteHookRemovedFunctions,
} from "./run_pre_commit";
import { escapeXmlAttr, escapeXmlContent, type ToolDefinition } from "./types";

const schema = z.object({
  command: z.string().min(1).max(maxShellCommandLength()),
  description: z.string().min(1).max(1000),
  timeout_ms: z.number().int().min(1).max(300_000).optional(),
});

export const runShellTool: ToolDefinition<z.infer<typeof schema>> = {
  name: "run_shell",
  description: "Run an independently reviewed app command on the host.",
  getDescription: (ctx) =>
    "Run an independently reviewed app command on the host. " +
    shellExecutionGuidance(
      process.platform,
      ctx.appPath ?? "current app directory",
    ),
  getConsentPreview: (args) =>
    `${process.platform === "win32" ? "PowerShell" : "Bash"}: ${args.command}\n\n${args.description}`,
  inputSchema: schema,
  modifiesState: true,
  mutationTracking: "internal",
  usesEngineEndpoint: true,
  defaultConsent: "always",
  isEnabled: (ctx) =>
    isShellExperimentAvailable({
      settings: ctx.inferenceSettings ?? readSettings(),
      isDyadPro: ctx.isDyadPro,
      freeModelMode: ctx.freeModelMode,
      isChild: !!ctx.mutationActivityOwner?.persona,
    }),
  execute: async (input, ctx) => {
    const args = schema.parse(input);
    const shell = process.platform === "win32" ? "PowerShell" : "Bash";
    const present = (status: string, body: string, final = false) => {
      const state = !final
        ? "pending"
        : status === "completed"
          ? "finished"
          : status === "cancelled"
            ? "aborted"
            : "warning";
      const xml = `<dyad-status state="${state}" title="${escapeXmlAttr(`${shell}: ${status}`)}">${escapeXmlContent(`${args.command}\n\n${body}`)}</dyad-status>`;
      if (final) ctx.onXmlComplete(xml);
      else ctx.onXmlStream(xml);
    };
    const blocked = (reason: string) => {
      present("blocked", reason, true);
      return JSON.stringify({ status: "blocked", reason });
    };
    if (!ctx.shellReviewContext)
      return blocked("Shell execution is unavailable in this turn.");
    const available = () => {
      const settings = readSettings();
      const mode = runningApps.get(ctx.appId)?.mode;
      if (mode === "cloud" || mode === "docker") return false;
      return isShellExperimentAvailable({
        settings,
        isDyadPro: ctx.isDyadPro && isDyadProEnabled(settings),
        freeModelMode: ctx.freeModelMode,
        isChild: !!ctx.mutationActivityOwner?.persona,
      });
    };
    if (!available())
      return blocked("The shell experiment or Pro Host access is disabled.");
    if (ctx.abortSignal?.aborted)
      return JSON.stringify({ status: "cancelled" });
    present("reviewing", "Checking command safety…");
    let decision = await reviewShellCommand(
      args.command,
      args.description,
      ctx,
    );
    if (ctx.abortSignal?.aborted) {
      present("cancelled", "Cancelled before execution.", true);
      return JSON.stringify({ status: "cancelled" });
    }
    // An outage has no safety verdict. Retry requires fresh review, never execution approval.
    while (decision.unavailable && !ctx.abortSignal?.aborted) {
      present("review unavailable", decision.reason);
      const retry = await ctx.requireConsent({
        toolName: "run_shell",
        confirmation: "shell-review-retry",
        toolDescription:
          decision.reason +
          " Retry runs the safety check again; it does not execute the command.",
        inputPreview: runShellTool.getConsentPreview!(args),
        abortSignal: ctx.abortSignal,
      });
      if (ctx.abortSignal?.aborted) {
        present("cancelled", "Cancelled before execution.", true);
        return JSON.stringify({ status: "cancelled" });
      }
      if (!retry) {
        present("review unavailable", decision.reason, true);
        return JSON.stringify({
          status: "review_unavailable",
          reason: decision.reason,
          retryable: true,
        });
      }
      if (!available())
        return blocked("Shell access was disabled before retrying.");
      present("reviewing", "Retrying command safety review…");
      decision = await reviewShellCommand(args.command, args.description, ctx);
    }
    if (ctx.abortSignal?.aborted) {
      present("cancelled", "Cancelled before execution.", true);
      return JSON.stringify({ status: "cancelled" });
    }
    if (decision.decision === "block") return blocked(decision.reason);
    const approved = await ctx.requireConsent({
      toolName: "run_shell",
      ...(decision.decision === "ask"
        ? { confirmation: "shell-approval" as const }
        : {}),
      toolDescription: decision.reason,
      inputPreview: runShellTool.getConsentPreview!(args),
      abortSignal: ctx.abortSignal,
    });
    if (!approved || ctx.abortSignal?.aborted) {
      present("cancelled", "Command was not approved for execution.", true);
      return JSON.stringify({
        status: "cancelled",
        reason: "Command was not approved for execution.",
      });
    }
    if (!available())
      return blocked("Shell access was disabled while reviewing.");
    const removedFunctionNames: string[] = [];
    const request: AppOperationRequest = {
      appId: ctx.appId,
      operation: "run-agent-shell",
      resources: [
        readAppResource("app-path"),
        "repository",
        "provider",
        "runtime-config",
        readAppResource("runtime"),
      ],
      signal: ctx.abortSignal,
      refuseWhenRecording: "run shell commands",
    };
    // Review and consent never hold resource claims or mutation activity.
    return withTrackedMutation(ctx, async () => {
      const outcome = await appOperationCoordinator.run(request, async () => {
        if (!available())
          return blocked("Shell access was disabled before execution.");
        if (ctx.abortSignal?.aborted)
          return JSON.stringify({ status: "cancelled" });
        if (!((await decision.revalidateInspection?.()) ?? true))
          return blocked(
            "App files inspected by the safety reviewer changed while waiting. Submit the command again for a fresh safety review.",
          );
        const before = await tryGetGitStateFingerprint(
          ctx.appPath,
          "before",
          ctx.abortSignal,
        );
        const entries = ctx.supabaseProjectId
          ? await tryCollectSupabaseFunctionEntryPoints(ctx.appPath, "before")
          : undefined;
        if (!available())
          return blocked("Shell access was disabled before execution.");
        if (ctx.abortSignal?.aborted) {
          present("cancelled", "Cancelled before execution.", true);
          return JSON.stringify({ status: "cancelled" });
        }
        let output = "";
        let lastUpdate = 0;
        present("running", decision.reason);
        const result: ShellProcessResult = await runShellProcess({
          command: args.command,
          cwd: ctx.appPath,
          timeoutMs: args.timeout_ms ?? 60_000,
          signal: ctx.abortSignal,
          onOutput: (chunk) => {
            output = (output + chunk).slice(-64_000);
            if (Date.now() - lastUpdate >= 150) {
              lastUpdate = Date.now();
              present("running", `${decision.reason}\n\n${output}`);
            }
          },
        }).catch((error: unknown) => ({
          status: "failed" as const,
          executed: false,
          code: null,
          stdout: "",
          stderr: `Could not start the shell: ${error instanceof Error ? error.message : String(error)}`,
          truncated: false,
        }));
        const shutdownNotice =
          "Shell process shutdown could not be confirmed. A descendant may still be running. Stop it externally and restart Dyad before modifying this app.";
        if (result.shutdownUnconfirmed) {
          // Do not release admission to competing mutations while escaped work may remain.
          appOperationCoordinator.blockConflictingOperations(
            request,
            shutdownNotice,
          );
        }
        // Even failed/cancelled commands can leave edits. Never label them rolled back.
        const after = result.shutdownUnconfirmed
          ? undefined
          : await tryGetGitStateFingerprint(ctx.appPath, "after");
        const changed =
          before === undefined || after === undefined || before !== after;
        // Shell effects can include ignored files or external state that Git cannot observe.
        if (result.executed)
          trackWorkspaceMutation(
            ctx,
            changed && ctx.preCommitHookAvailable === true,
          );
        let note: string | undefined = result.shutdownUnconfirmed
          ? shutdownNotice
          : undefined;
        if (
          result.executed &&
          !result.shutdownUnconfirmed &&
          changed &&
          result.status === "completed" &&
          !ctx.abortSignal?.aborted
        ) {
          note = await scheduleHookGeneratedFileSideEffects(
            ctx,
            entries,
            removedFunctionNames,
            "Shell command",
          );
        }
        if (
          changed &&
          (result.status !== "completed" || ctx.abortSignal?.aborted)
        ) {
          note = [
            note,
            "Automatic provider reconciliation was skipped because the command did not complete successfully. Inspect partial edits before making provider changes.",
          ]
            .filter(Boolean)
            .join("\n");
        }
        if (result.status !== "completed")
          note = [
            note,
            "Partial changes may remain; inspect the workspace before continuing.",
          ]
            .filter(Boolean)
            .join("\n");
        const body = `${decision.reason}\nExit code: ${result.code ?? "none"}\n${result.stdout}\n${result.stderr}${result.truncated ? "\n[Output truncated]" : ""}${note ? `\n${note}` : ""}`;
        present(result.status, body, true);
        return serializeShellResultForModel({
          ...result,
          reason: decision.reason,
          note,
        });
      });
      await deleteHookRemovedFunctions(ctx, removedFunctionNames);
      return outcome;
    });
  },
};

```

## src/pro/main/ipc/handlers/local_agent/tools/shell_model_result.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/shell_model_result.ts

SHA-256: `cd36aac50d18cb3208be08cb2630dfc1d87a291b12382555d055fd01a70c7996`

```
import { estimateTokens } from "@/ipc/utils/token_utils";
import type { ShellProcessResult } from "./shell_process";

export const SHELL_MODEL_RESULT_TOKEN_LIMIT = 20_000;
type ShellToolResult = ShellProcessResult & { reason: string; note?: string };

/** Bound serialized model context independently of the retained chat output. */
export function serializeShellResultForModel(result: ShellToolResult): string {
  const full = JSON.stringify(result);
  if (estimateTokens(full) <= SHELL_MODEL_RESULT_TOKEN_LIMIT) return full;

  const capMetadata = (value: string) =>
    value.length > 4000 ? `${value.slice(0, 4000)}…[truncated]` : value;
  const base = {
    ...result,
    reason: capMetadata(result.reason),
    ...(result.note ? { note: capMetadata(result.note) } : {}),
    truncated: true,
    outputNotice:
      "Output truncated for model context; the full retained output is available in chat.",
  };
  const serialize = (budget: number) => {
    let stderrLength = Math.min(result.stderr.length, Math.floor(budget / 2));
    const stdoutLength = Math.min(result.stdout.length, budget - stderrLength);
    stderrLength = Math.min(result.stderr.length, budget - stdoutLength);
    return JSON.stringify({
      ...base,
      stdout: stdoutLength ? result.stdout.slice(-stdoutLength) : "",
      stderr: stderrLength ? result.stderr.slice(-stderrLength) : "",
    });
  };
  // Search serialized size so quotes, control characters, and metadata count too.
  let low = 0;
  let high = result.stdout.length + result.stderr.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (estimateTokens(serialize(middle)) <= SHELL_MODEL_RESULT_TOKEN_LIMIT)
      low = middle;
    else high = middle - 1;
  }
  return serialize(low);
}

```

## src/pro/main/ipc/handlers/local_agent/tools/shell_process.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/shell_process.ts

SHA-256: `cb73f5e2e36851eeeaae33aeaaee921084fcc2061b3229c993e87cf59a973984`

```
import { app } from "electron";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import treeKill from "tree-kill";
import { PROVIDER_TO_ENV_VAR } from "@/ipc/shared/language_model_constants";
import { BoundedOutputBuffer } from "@/ipc/utils/bounded_output_buffer";
import { buildWindowsCommandInvocation } from "@/ipc/utils/windows_command";

const activeShellPids = new Set<number>();
export const SHELL_SHUTDOWN_TIMEOUT_MS = 3_000;
let quitCleanupRegistered = false;
export function maxShellCommandLength(platform = process.platform): number {
  // UTF-16 + base64 expands each Windows code unit to ~2.7 command-line characters.
  // Leave space for the executable, switches, and wrapper under CreateProcess's 32K limit.
  return platform === "win32" ? 9_000 : 16_000;
}

function registerQuitCleanup() {
  if (quitCleanupRegistered || !app?.once) return;
  quitCleanupRegistered = true;
  app.once("will-quit", () => {
    // Electron does not await promises during quit. Deliver termination synchronously.
    for (const pid of activeShellPids) {
      if (process.platform === "win32") {
        spawnSync(
          path.win32.join(
            process.env.SystemRoot ?? "C:\\Windows",
            "System32",
            "taskkill.exe",
          ),
          ["/pid", String(pid), "/T", "/F"],
          { shell: false, stdio: "ignore", timeout: 5000 },
        );
      } else {
        try {
          process.kill(-pid, "SIGKILL");
        } catch {
          /* Already exited. */
        }
      }
    }
  });
}

/** Preserve host CLI configuration/authentication, excluding Dyad secrets and startup injection. */
export function shellEnvironment(
  source: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  const denied = new Set([
    ...Object.values(PROVIDER_TO_ENV_VAR),
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "BASH_ENV",
    "ENV",
    "PROMPT_COMMAND",
    "SHELLOPTS",
    "BASHOPTS",
    "CDPATH",
    "GLOBIGNORE",
    "ZDOTDIR",
    "NODE_OPTIONS",
    "NODE_PATH",
    "ELECTRON_RUN_AS_NODE",
    "PYTHONSTARTUP",
    "PYTHONPATH",
    "RUBYOPT",
    "RUBYLIB",
    "PERL5OPT",
    "PERL5LIB",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
  ]);
  return Object.fromEntries(
    Object.entries(source).filter(([key]) => {
      const normalized = key.toUpperCase();
      return (
        !denied.has(normalized) &&
        !normalized.startsWith("DYAD_") &&
        !normalized.startsWith("DYLD_")
      );
    }),
  );
}

export function shellInvocation(command: string, platform = process.platform) {
  if (command.length > maxShellCommandLength(platform))
    throw new RangeError(
      `Shell command exceeds ${maxShellCommandLength(platform)} characters on this platform.`,
    );
  if (platform === "win32") {
    const executable = path.win32.join(
      process.env.SystemRoot ?? "C:\\Windows",
      "System32",
      "WindowsPowerShell",
      "v1.0",
      "powershell.exe",
    );
    // EncodedCommand preserves arbitrary quotes, newlines, and Unicode without cmd.exe.
    // Windows PowerShell 5.1 treats native stderr as error records. Continue lets
    // warnings flow. Final $? includes cmdlets; LASTEXITCODE can be stale from
    // an earlier native command. Explicit exit commands retain their own code.
    const script = `[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n$ErrorActionPreference = 'Continue'\n${command}\n$dyadShellSucceeded = $?\nif ($dyadShellSucceeded) { exit 0 }\nexit 1\n`;
    return buildWindowsCommandInvocation(
      executable,
      [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        Buffer.from(script, "utf16le").toString("base64"),
      ],
      platform,
    );
  }
  return {
    command: "/bin/bash",
    args: ["--noprofile", "--norc", "-c", command],
  };
}

export interface ShellProcessResult {
  executed: boolean;
  code: number | null;
  status: "completed" | "failed" | "cancelled" | "timed_out";
  stdout: string;
  stderr: string;
  truncated: boolean;
  /** Pipes or process-tree termination could not be confirmed before the hard deadline. */
  shutdownUnconfirmed?: true;
}

export function runShellProcess(
  {
    command,
    cwd,
    timeoutMs,
    signal,
    onOutput,
  }: {
    command: string;
    cwd: string;
    timeoutMs: number;
    signal?: AbortSignal;
    onOutput: (text: string) => void;
  },
  platform: NodeJS.Platform = process.platform,
): Promise<ShellProcessResult> {
  if (signal?.aborted)
    return Promise.resolve({
      executed: false,
      code: null,
      status: "cancelled",
      stdout: "",
      stderr: "",
      truncated: false,
    });
  registerQuitCleanup();
  const invocation = shellInvocation(command, platform);
  return new Promise((resolve, reject) => {
    const stdout = new BoundedOutputBuffer(64_000);
    const stderr = new BoundedOutputBuffer(64_000);
    const outDecoder = new StringDecoder("utf8");
    const errDecoder = new StringDecoder("utf8");
    const child = spawn(invocation.command, invocation.args, {
      cwd,
      env: shellEnvironment(),
      shell: false,
      detached: platform !== "win32",
      stdio: ["ignore", "pipe", "pipe"],
    });
    if (child.pid) activeShellPids.add(child.pid);
    let status: ShellProcessResult["status"] | undefined;
    let forceTimer: ReturnType<typeof setTimeout> | undefined;
    let shutdownTimer: ReturnType<typeof setTimeout> | undefined;
    let killPending: Promise<void> = Promise.resolve();
    let exited = false;
    let settled = false;
    let exitCode: number | null = null;
    const kill = (kind: "SIGTERM" | "SIGKILL") => {
      if (!child.pid) return;
      if (platform !== "win32") {
        try {
          process.kill(-child.pid, kind);
        } catch {
          /* Already exited. */
        }
      } else {
        if (exited) return;
        const pid = child.pid;
        killPending = new Promise<void>((done) =>
          treeKill(pid, kind, () => done()),
        );
      }
    };
    const stop = (next: "cancelled" | "timed_out") => {
      if (status || settled) return;
      status = next;
      kill("SIGTERM");
      if (platform !== "win32")
        forceTimer = setTimeout(() => kill("SIGKILL"), 1_000);
      // Escaped descendants can keep inherited pipes open after the root exits.
      // Never wait indefinitely or taskkill an exited/reused Windows root PID.
      shutdownTimer = setTimeout(
        () => finish(exitCode, true),
        SHELL_SHUTDOWN_TIMEOUT_MS,
      );
    };
    const abort = () => stop("cancelled");
    const timer = setTimeout(() => stop("timed_out"), timeoutMs);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    const cleanup = (shutdownUnconfirmed = false) => {
      if (child.pid && (!shutdownUnconfirmed || exited))
        activeShellPids.delete(child.pid);
      clearTimeout(timer);
      clearTimeout(forceTimer);
      clearTimeout(shutdownTimer);
      signal?.removeEventListener("abort", abort);
    };
    const emit = (text: string) => {
      try {
        onOutput(text);
      } catch {
        /* A disconnected renderer must not release a live process. */
      }
    };
    const finish = (code: number | null, shutdownUnconfirmed = false) => {
      if (settled) return;
      settled = true;
      cleanup(shutdownUnconfirmed);
      if (shutdownUnconfirmed) {
        child.stdout.destroy();
        child.stderr.destroy();
        child.unref();
      }
      emit(outDecoder.end());
      emit(errDecoder.end());
      resolve({
        executed: true,
        code,
        status: status ?? (code === 0 ? "completed" : "failed"),
        stdout: stdout.toString(),
        stderr: stderr.toString(),
        truncated: stdout.wasTruncated || stderr.wasTruncated,
        ...(shutdownUnconfirmed ? { shutdownUnconfirmed: true } : {}),
      });
    };
    child.once("exit", (code) => {
      exited = true;
      exitCode = code;
      // A late Unix exit must retire an unconfirmed root too, before PID reuse.
      // Normal Unix groups remain registered until close so quit can stop descendants.
      if ((platform === "win32" || settled) && child.pid)
        activeShellPids.delete(child.pid);
    });
    child.stdout.on("data", (data: Buffer) => {
      stdout.append(data);
      emit(outDecoder.write(data));
    });
    child.stderr.on("data", (data: Buffer) => {
      stderr.append(data);
      emit(errDecoder.write(data));
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    });
    child.on("close", async (code) => {
      if (settled) return;
      // Clean remaining Unix group members even if a command forked then exited.
      if (platform !== "win32") kill("SIGKILL");
      await killPending;
      finish(code);
    });
  });
}

```

## src/pro/main/ipc/handlers/local_agent/tools/subagent_tools.ts

Source: https://github.com/dyad-sh/dyad/blob/866f6ab17ecc5b906564b29a24637832667a0472/src/pro/main/ipc/handlers/local_agent/tools/subagent_tools.ts

SHA-256: `2fe2ce1def9e33c9e8fb4779c27ad40daa4530936b03d11af05ed078a6699930`

```
import { z } from "zod";
import type { ToolSet } from "ai";
import { DyadError, DyadErrorKind, isDyadError } from "@/errors/dyad_error";
import type { SubagentThreadSummary } from "@/ipc/types";
import { getErrorMessage } from "@/lib/errors";

import { buildAgentToolSet, TOOL_DEFINITIONS } from "../tool_definitions";
import {
  cancelSubagent,
  followupSubagent,
  listSubagents,
  sendSubagentMessage,
  spawnModelSubagent,
  updateSubagentActivity,
  waitForSubagents,
} from "../subagents/subagent_manager";
import { normalizeMutationScope } from "../subagents/mutation_activity_tracker";
import {
  escapeXmlAttr,
  escapeXmlContent,
  type AgentContext,
  type ToolDefinition,
} from "./types";
import {
  formatRawExploreCodeResult,
  normalizeExploreCodeArgsForApp,
  rawExploreCodeSchema,
  runRawExploreCode,
} from "./explore_code_raw";
import { resolveTargetAppPath } from "./resolve_app_context";
import {
  getExploreCodeAvailability,
  getExploreCodeAvailabilityForAppPath,
} from "./explore_code";

const baseSpawnShape = {
  task_name: z
    .string()
    .min(1)
    .max(100)
    .describe("A stable short name for this task"),
  assignment: z.string().min(1).max(20_000).describe(
    // Restating the form here puts the reminder at the moment the call is
    // composed, which is where assignments lose their MUST HOLD content.
    "The assignment. For implementer tasks use the form GOAL / MUST HOLD / OUT OF SCOPE / DONE WHEN, where MUST HOLD lists every project rule the change could touch (access, scoping, invariants) or explains why none apply.",
  ),
  scope: z
    .array(z.string().min(1).max(500))
    .max(100)
    .default([])
    .describe(
      "Advisory relative paths or path prefixes expected to be in scope",
    ),
};

const spawnFallbackSchema = z.object({
  persona: z.enum(["explorer", "implementer"]),
  ...baseSpawnShape,
});

interface SubagentToolContextParams {
  ctx: AgentContext;
  contextOverrides?: Partial<AgentContext>;
  threadId: string;
  persona: "explorer" | "implementer";
  taskName: string;
  scope: string[];
  abortSignal: AbortSignal;
  mutationActivityOwner?: AgentContext["mutationActivityOwner"];
}

export function buildSubagentContext(
  params: SubagentToolContextParams,
): AgentContext {
  const {
    ctx,
    threadId,
    persona,
    taskName,
    scope,
    abortSignal,
    mutationActivityOwner,
    contextOverrides,
  } = params;
  return {
    ...ctx,
    ...contextOverrides,
    subagentThreadId: threadId,
    subagentPersona: persona,
    subagentPathScope: scope.map(normalizeMutationScope),
    mutationActivityOwner,
    abortSignal,
    onWorkspaceMutation: (didMutateFile) => {
      ctx.mutationCount = (ctx.mutationCount ?? 0) + 1;
      if (didMutateFile) {
        ctx.fileMutationCount = (ctx.fileMutationCount ?? 0) + 1;
      }
      ctx.workspaceMutated = true;
      ctx.onWorkspaceMutation?.(didMutateFile);
    },
    requireConsent: (consent) =>
      ctx.requireConsent({
        ...consent,
        abortSignal,
        subagent: { threadId, persona, taskName },
      }),
    allowDeploySideEffects: false,
    onSharedServerModuleChange: (relativePath) => {
      ctx.isSharedModulesChanged = true;
      if (!ctx.sharedServerModulePaths.includes(relativePath)) {
        ctx.sharedServerModulePaths.push(relativePath);
      }
    },
    onDeferredFunctionDeploy: (functionName) => {
      if (!ctx.pendingFunctionDeploys.includes(functionName)) {
        ctx.pendingFunctionDeploys.push(functionName);
      }
    },
    onDeferredFunctionDelete: (functionName) => {
      ctx.pendingFunctionDeletes ??= [];
      if (!ctx.pendingFunctionDeletes.includes(functionName)) {
        ctx.pendingFunctionDeletes.push(functionName);
      }
    },
    canUseExplorerSubagent: false,
    canUseImplementerSubagent: false,
    canUseAdvancedSubagentTools: false,
    referencedApps: new Map(ctx.referencedApps),
    todos: [],
    fileEditTracker: ctx.fileEditTracker,
    onXmlStream: () => {},
    onXmlComplete: () => {},
    onToolActivity: (activity) =>
      updateSubagentActivity({
        chatId: ctx.chatId,
        threadId,
        ...activity,
      }),
  };
}

/**
 * Child capabilities are explicit and fail closed: adding a new root tool does
 * not silently grant it to every sub-agent. This intentionally excludes MCP
 * discovery/execution, sandbox scripts, nested agents/history explorers, and
 * root-owned provider or application configuration side effects.
 */
const SUBAGENT_ALLOWED_TOOL_NAMES = [
  "read_file",
  "list_files",
  "grep",
  "code_search",
  "search_chats",
  "read_chat",
  "explore_code",
  "write_file",
  "search_replace",
  "copy_file",
  "delete_file",
  "rename_file",
  "git_status",
  "git_diff",
  "git_log",
  "git_show_commit",
  "git_show_file",
  "git_restore_file",
  "get_supabase_project_info",
  "get_neon_project_info",
  "get_database_table_schema",
  "read_logs",
  "run_type_checks",
  "run_build",
  "run_tests",
  "restart_app",
  "read_guide",
  "web_search",
  "web_crawl",
  "web_fetch",
] as const;
const SUBAGENT_ALLOWED_TOOLS = new Set<string>(SUBAGENT_ALLOWED_TOOL_NAMES);

export function validateSubagentAllowedToolNames(
  allowedNames: readonly string[],
  registeredTools: readonly Pick<ToolDefinition, "name">[],
): void {
  const registeredNames = new Set(registeredTools.map((tool) => tool.name));
  const unknownNames = allowedNames.filter(
    (name) => !registeredNames.has(name),
  );
  if (unknownNames.length > 0) {
    throw new Error(
      `Unknown sub-agent tool(s) in allowlist: ${unknownNames.join(", ")}`,
    );
  }
}

export function buildSubagentToolSet(
  params: SubagentToolContextParams,
): ToolSet {
  validateSubagentAllowedToolNames(
    SUBAGENT_ALLOWED_TOOL_NAMES,
    TOOL_DEFINITIONS,
  );
  const { ctx, persona } = params;
  const childCtx = buildSubagentContext(params);
  const allTools = buildAgentToolSet(childCtx, {
    readOnly: persona === "explorer",
    enableAppBlueprint: ctx.enableAppBlueprint,
    freeModelMode: ctx.freeModelMode,
  });
  return Object.fromEntries(
    Object.entries(allTools).filter(([name]) =>
      SUBAGENT_ALLOWED_TOOLS.has(name),
    ),
  );
}

export const spawnAgentTool: ToolDefinition<
  z.infer<typeof spawnFallbackSchema>
> = {
  name: "spawn_agent",
  description:
    "Run a blocking depth-one Explorer or enabled Implementer sub-agent. The sub-agent returns its report before the tool call completes. Reviewer is user/application controlled and is never available here.",
  inputSchema: spawnFallbackSchema,
  getInputSchema: (ctx) => {
    const personas = [
      ...(ctx.canUseExplorerSubagent ? ["explorer" as const] : []),
      ...(ctx.canUseImplementerSubagent ? ["implementer" as const] : []),
    ];
    if (personas.length === 0) return spawnFallbackSchema;
    return z.object({
      persona:
        personas.length === 1
          ? z.literal(personas[0])
          : z.enum(
              personas as [
                "explorer" | "implementer",
                ...("explorer" | "implementer")[],
              ],
            ),
      ...baseSpawnShape,
    });
  },
  defaultConsent: "always",
  modifiesState: true,
  // Explorer only reads the app, so Ask and Plan may create its durable
  // orchestration record when the turn-scoped schema excludes Implementer.
  allowInReadOnlyModes: (ctx) => ctx.canUseImplementerSubagent !== true,
  subagentOnly: true,
  mutationTracking: "none",
  requiresBlueprintApproval: false,
  usesEngineEndpoint: true,
  isEnabled: (ctx) =>
    Boolean(
      ctx.isDyadPro &&
      (ctx.canUseExplorerSubagent || ctx.canUseImplementerSubagent) &&
      !ctx.subagentThreadId,
    ),
  execute: async (args, ctx) => {
    const threadId = await spawnModelSubagent({
      ctx,
      persona: args.persona,
      taskName: args.task_name,
      assignment: args.assignment,
      scope: args.scope,
      buildTools: ({ ctx: childCtx, contextOverrides, ...child }) =>
        buildSubagentToolSet({
          ctx: childCtx,
          contextOverrides,
          ...child,
        }),
    });
    ctx.spawnedSubagentThreadIds ??= [];
    ctx.spawnedSubagentThreadIds.push(threadId);
    if (args.persona === "implementer") {
      ctx.spawnedImplementerThreadIds ??= [];
      ctx.spawnedImplementerThreadIds.push(threadId);
    }
    ctx.onXmlComplete(
      `<dyad-subagent chat-id="${escapeXmlAttr(String(ctx.chatId))}" thread-id="${escapeXmlAttr(threadId)}" persona="${escapeXmlAttr(args.persona)}" task-name="${escapeXmlAttr(args.task_name)}"></dyad-subagent>`,
    );
    let subagent: SubagentThreadSummary;
    try {
      [subagent] = await waitForSubagents(
        ctx.chatId,
        [threadId],
        ctx.abortSignal,
      );
    } catch (error) {
      try {
        await cancelSubagent(ctx.chatId, threadId);
      } catch (cancellationError) {
        // Keep the thread registered so the end-of-turn barrier remains
        // fail-closed when cancellation itself did not complete.
        throw new DyadError(
          `The sub-agent wait failed: ${getErrorMessage(error)} Cancellation also failed: ${getErrorMessage(cancellationError)}`,
          isDyadError(error)
            ? error.kind
            : isDyadError(cancellationError)
              ? cancellationError.kind
              : DyadErrorKind.Conflict,
          { cause: new AggregateError([error, cancellationError]) },
        );
      }
      // This tool has now taken responsibility for the thread: it cancelled it
      // and is about to report the failure to the model, which can retry or work
      // around it. Leaving the id in spawnedImplementerThreadIds would hand the
      // end-of-turn join barrier a thread that is permanently "cancelled", and
      // that barrier treats any non-completed Implementer as fatal — so a
      // transient wait failure here would kill the whole turn at the very end,
      // discarding milestones the agent had already finished by other means.
      const implementers = ctx.spawnedImplementerThreadIds;
      if (args.persona === "implementer") {
        ctx.cancelledImplementerNames ??= [];
        if (!ctx.cancelledImplementerNames.includes(args.task_name)) {
          ctx.cancelledImplementerNames.push(args.task_name);
        }
      }
      if (implementers) {
        const at = implementers.indexOf(threadId);
        if (at >= 0) implementers.splice(at, 1);
      }
      const spawned = ctx.spawnedSubagentThreadIds;
      if (spawned) {
        const at = spawned.indexOf(threadId);
        if (at >= 0) spawned.splice(at, 1);
      }
      throw error;
    }
    if (args.persona === "explorer") {
      ctx.deliveredExplorerThreadIds ??= [];
      if (!ctx.deliveredExplorerThreadIds.includes(threadId)) {
        ctx.deliveredExplorerThreadIds.push(threadId);
      }
    }
    return JSON.stringify({
      threadId: subagent.id,
      status: subagent.status,
      report:
        typeof subagent.result?.report === "string"
          ? subagent.result.report
          : null,
      error: subagent.error,
    });
  },
};

const threadIdsSchema = z.object({ thread_ids: z.array(z.string()).min(1) });

function canUseAdvancedSubagentTools(ctx: AgentContext): boolean {
  return Boolean(
    ctx.isDyadPro && ctx.canUseAdvancedSubagentTools && !ctx.subagentThreadId,
  );
}

export const listAgentsTool: ToolDefinition<{}> = {
  name: "list_agents",
  description:
    "List durable sub-agent threads and their current status for this chat.",
  inputSchema: z.object({}),
  defaultConsent: "always",
  subagentOnly: true,
  isEnabled: canUseAdvancedSubagentTools,
  execute: async (_args, ctx) =>
    JSON.stringify(await listSubagents(ctx.chatId)),
};

export const waitAgentsTool: ToolDefinition<z.infer<typeof threadIdsSchema>> = {
  name: "wait_agents",
  description:
    "Wait until all specified sub-agents reach a terminal or idle state.",
  inputSchema: threadIdsSchema,
  defaultConsent: "always",
  modifiesState: true,
  subagentOnly: true,
  mutationTracking: "none",
  requiresBlueprintApproval: false,
  isEnabled: canUseAdvancedSubagentTools,
  execute: async (args, ctx) => {
    const summaries = await waitForSubagents(
      ctx.chatId,
      args.thread_ids,
      ctx.abortSignal,
    );
    return JSON.stringify(summaries);
  },
};

export const cancelAgentTool: ToolDefinition<{ thread_id: string }> = {
  name: "cancel_agent",
  description: "Cancel a running sub-agent at its next safe boundary.",
  inputSchema: z.object({ thread_id: z.string() }),
  defaultConsent: "always",
  modifiesState: true,
  subagentOnly: true,
  mutationTracking: "none",
  requiresBlueprintApproval: false,
  isEnabled: canUseAdvancedSubagentTools,
  execute: async (args, ctx) => {
    await cancelSubagent(ctx.chatId, args.thread_id);
    return "Cancellation requested.";
  },
};

const messageSchema = z.object({
  thread_id: z.string(),
  message: z.string().min(1).max(20_000),
});

export const sendMessageTool: ToolDefinition<z.infer<typeof messageSchema>> = {
  name: "send_message",
  description: "Durably queue a message for an existing sub-agent thread.",
  inputSchema: messageSchema,
  defaultConsent: "always",
  modifiesState: true,
  subagentOnly: true,
  mutationTracking: "none",
  requiresBlueprintApproval: false,
  isEnabled: canUseAdvancedSubagentTools,
  execute: async (args, ctx) => {
    await sendSubagentMessage(
      ctx.chatId,
      args.thread_id,
      args.message,
      ctx.abortSignal,
    );
    return "Message queued durably.";
  },
};

export const followupTaskTool: ToolDefinition<z.infer<typeof messageSchema>> = {
  ...sendMessageTool,
  name: "followup_task",
  description:
    "Queue a durable follow-up assignment on an existing child thread. An idle child will consume it on its next turn.",
  subagentOnly: true,
  mutationTracking: "none",
  requiresBlueprintApproval: false,
  usesEngineEndpoint: true,
  execute: async (args, ctx) => {
    const persona = await followupSubagent(
      ctx.chatId,
      args.thread_id,
      args.message,
      {
        ctx,
        buildTools: ({ ctx: childCtx, contextOverrides, ...child }) =>
          buildSubagentToolSet({
            ctx: childCtx,
            contextOverrides,
            ...child,
          }),
      },
    );
    ctx.spawnedSubagentThreadIds ??= [];
    if (!ctx.spawnedSubagentThreadIds.includes(args.thread_id)) {
      ctx.spawnedSubagentThreadIds.push(args.thread_id);
    }
    if (persona === "implementer") {
      ctx.spawnedImplementerThreadIds ??= [];
      if (!ctx.spawnedImplementerThreadIds.includes(args.thread_id)) {
        ctx.spawnedImplementerThreadIds.push(args.thread_id);
      }
    }
    return "Follow-up queued durably.";
  },
};

export const exploreCodeTool: ToolDefinition<
  z.infer<typeof rawExploreCodeSchema>
> = {
  name: "explore_code",
  description:
    "Explore the configured TypeScript project using compiler-backed symbol and dependency analysis. Returns relevant symbols and line-numbered source windows grouped by file.",
  inputSchema: rawExploreCodeSchema,
  defaultConsent: "always",
  subagentOnly: true,
  isEnabled: (ctx) =>
    ctx.subagentPersona === "explorer" &&
    (getExploreCodeAvailability(ctx).enabled ||
      [...ctx.referencedApps.values()].some(
        (appPath) => getExploreCodeAvailabilityForAppPath(ctx, appPath).enabled,
      )),
  buildXml: (args, isComplete) => {
    if (!args.query || isComplete) return undefined;
    return `<dyad-explore-code${buildExploreCodeAttributes(args)}>Exploring...`;
  },
  execute: async (args, ctx) => {
    const appPath = resolveTargetAppPath(ctx, args.app_name);
    const availability = getExploreCodeAvailabilityForAppPath(ctx, appPath);
    const effectiveArgs = normalizeExploreCodeArgsForApp({
      appPath,
      args,
      fallbackTsconfigPath: availability.tsconfigPath,
    });
    const result = await runRawExploreCode({ appPath, args: effectiveArgs });
    const resultText = formatRawExploreCodeResult(result);
    const attributes = buildExploreCodeAttributes(effectiveArgs, {
      files: result.files.length,
      symbols: result.totalSymbols,
      indexMs: result.indexMs,
      searchMs: result.searchMs,
      truncated: result.truncated,
    });
    ctx.onXmlComplete(
      `<dyad-explore-code${attributes}>\n${escapeXmlContent(resultText)}\n</dyad-explore-code>`,
    );
    return resultText;
  },
};

function buildExploreCodeAttributes(
  args: Partial<z.infer<typeof rawExploreCodeSchema>>,
  result?: {
    files: number;
    symbols: number;
    indexMs: number;
    searchMs: number;
    truncated: boolean;
  },
): string {
  const attributes: string[] = [];
  if (args.query) attributes.push(`query="${escapeXmlAttr(args.query)}"`);
  if (args.app_name)
    attributes.push(`app_name="${escapeXmlAttr(args.app_name)}"`);
  if (args.tsconfig_path) {
    attributes.push(`tsconfig_path="${escapeXmlAttr(args.tsconfig_path)}"`);
  }
  if (result) {
    attributes.push(`files="${result.files}"`);
    attributes.push(`symbols="${result.symbols}"`);
    attributes.push(`index_ms="${result.indexMs}"`);
    attributes.push(`search_ms="${result.searchMs}"`);
    if (result.truncated) attributes.push('truncated="true"');
  }
  return attributes.length > 0 ? ` ${attributes.join(" ")}` : "";
}

```
