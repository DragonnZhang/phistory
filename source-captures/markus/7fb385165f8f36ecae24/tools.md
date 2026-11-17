# markus — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## packages/core/src/agent-manager.ts

Source: https://github.com/markus-global/markus/blob/72bf7d72474bcf82734753e620f2d0478e80c8e7/packages/core/src/agent-manager.ts

SHA-256: `3f1abf040f69842f282cc9b659aed24b5c7bd18b5c645cecbddec05fb7877aa3`

```
import {
  createLogger,
  agentId as genAgentId,
  generateId,
  stripInternalBlocks,
  type AgentConfig,
  type AgentConcurrentConfig,
  type AgentProfile,
  type LLMAssignment,
  type AgentActivity,
  type IdentityContext,
  type HumanUser,
  type SystemAnnouncement,
  type PathAccessPolicy,
  type RoleTemplate,
  type RoleCategory,
  saveConfig,
  markusPath,
  type CodingToolName,
  type CodingToolConfig,
  type GoalConfig,
  type UserInputQuestion,
  type UserInputAnswer,
  DEFAULT_HEARTBEAT_INTERVAL_MS,
  MailboxPriorityLevel,
} from '@markus/shared';
import { Agent, type AgentToolHandler, type AgentOptions } from './agent.js';

/**
 * Peer agent as seen by `askAgent` (the `agent_ask_agent` tool).
 *
 * The options bag is typed with the REAL `sendMessage` signature on purpose.
 * An earlier version declared it as `o?: unknown`, which silently disabled the
 * compiler check and let `sourceType: 'a2a'` through. `'a2a'` is not a
 * `MailboxItemType`, so the mailbox had no registry entry for it →
 * `detectAbnormalCompletion()` returned "normal" → the item was marked
 * `completed` in 0 ms without ever running an LLM turn, and the caller's
 * responsePromise never resolved. The tool failed 100% of the time, silently.
 * Deriving the type from `Agent['sendMessage']` makes that class of typo a
 * compile error instead of a silent runtime no-op.
 */
type PeerSendOptions = NonNullable<Parameters<Agent['sendMessage']>[3]>;
type PeerAgent = {
  sendMessage: (
    message: string,
    senderId?: string,
    senderInfo?: { name?: string; role?: string },
    options?: PeerSendOptions,
  ) => Promise<string>;
};
import type { OrgContext } from './context-engine.js';
import type { LLMRouter } from './llm/router.js';
import { RoleLoader } from './role-loader.js';
import { EventBus } from './events.js';
import { initTokenCounter } from './token-counter.js';
import { createBuiltinTools } from './tools/builtin.js';
import { MCPClientManager } from './tools/mcp-client.js';
import { BrowserSessionManager } from './tools/browser-session.js';
import { createManagerTools, createPackageTools, createSecretaryTools, type SecretaryToolsContext } from './tools/manager.js';
import { createWorkflowTools, type WorkflowToolsContext } from './tools/workflow-tools.js';
import { createA2ATools, type A2AContext } from './tools/a2a.js';
import { createStructuredA2ATools } from './tools/a2a-structured.js';
import { createAgentTaskTools, type AgentTaskContext } from './tools/task-tools.js';
import { createProjectTools, type ProjectServiceBridge, type DeliverableServiceBridge, type ProjectToolsContext } from './tools/project-tools.js';
import { extractTextFromFile } from './file-converter.js';
import { createOfficeGenerateTool } from './tools/office-generate.js';
import { createMemoryTools } from './tools/memory.js';
import { createMailboxTools, type MailboxToolContext } from './tools/mailbox-tools.js';
import { createSettingsTools, type SettingsAgentContext } from './tools/settings.js';
import { createMultiModalTools } from './tools/multimodal.js';
import { createFeishuTools, type FeishuToolsConfig } from './tools/feishu.js';
import { createRecallTool, type RecallCallbacks } from './tools/recall.js';
import { createSessionTool, createMemorySessionRepo, type SessionRepo, type SessionCompactor, type SessionSlotStore, type SessionFragmentStore } from './tools/session.js';
import { SemanticMemorySearch, OpenAIEmbeddingProvider, LocalVectorStore } from './memory/semantic-search.js';
import type { SkillRegistry } from './skills/types.js';
import {
  buildSkillWarnings,
  setAgentSkillWarnings,
  getAgentSkillWarnings,
  type SkillWarnings,
} from './skill-warnings.js';
import { clickChromeAllowDialog } from './tools/chrome-dialog-clicker.js';
import { MarkusBrowserBridge } from './tools/markus-browser-bridge.js';
import { createBridgeToolHandlers, getBridgeToolDescriptors } from './tools/markus-browser-mcp.js';
import type { EmbeddedBrowserHost } from './tools/embedded-browser-host.js';
import type { EmbeddedTerminalHost } from './tools/embedded-terminal-host.js';
import {
  TerminalSessionManager,
  createTerminalToolHandlers,
} from './tools/terminal-session.js';
import { runQuickBrowserTest, runChaosBrowserTest, type BrowserTestResult, type ChaosEvent } from './tools/browser-test.js';
import { SecurityGuard, type SecurityPolicy } from './security.js';
import { DelegationManager, type TaskDelegation } from '@markus/a2a';
import type { TemplateRegistry } from './templates/registry.js';
import type { TemplateInstantiateRequest } from './templates/types.js';
import { join, dirname, resolve, sep } from 'node:path';
import { mkdirSync, readFileSync, existsSync, copyFileSync, rmSync, readdirSync, writeFileSync } from 'node:fs';

const log = createLogger('agent-manager');

/**
 * Resolve the task ID for submitForReview from **execution context only**:
 * ALS `getCurrentTaskId()` then `activeTasks` with status `in_progress`.
 * Does NOT guess from the TaskService board — outside a task session the
 * caller must pass `task_id` explicitly.
 */
function resolveCurrentTaskId(
  agentObj: Agent | undefined,
  ts: { getTask(id: string): { status: string } | undefined },
  agentId: string,
): string {
  const activeTasks = agentObj?.getActiveTasks?.() ?? [];

  const currentId = agentObj?.getCurrentTaskId?.();
  if (currentId && (activeTasks.length === 0 || activeTasks.some(t => t.taskId === currentId))) {
    const currentTask = ts.getTask(currentId);
    if (currentTask?.status === 'in_progress') return currentId;
    if (currentId && activeTasks.some(t => t.taskId === currentId)) {
      log.warn('Agent currentTaskId is no longer in_progress, searching activeTasks for a valid candidate', {
        agentId, currentTaskId: currentId, actualStatus: currentTask?.status,
      });
    }
  }

  for (const t of activeTasks) {
    const task = ts.getTask(t.taskId);
    if (task?.status === 'in_progress') return t.taskId;
  }

  // Clean up stale local activeTasks entries
  for (const t of activeTasks) {
    const task = ts.getTask(t.taskId);
    if (task && ['completed', 'failed', 'cancelled', 'archived'].includes(task.status)) {
      log.warn('Removing stale task from agent activeTasks', { agentId, taskId: t.taskId, status: task.status });
      agentObj?.removeActiveTask(t.taskId);
    }
  }

  if (activeTasks.length === 0) {
    throw new Error(
      'No active task execution context — cannot submit for review. Call `task_submit_review` from a task session (auto-fills task_id), or pass `task_id` explicitly.',
    );
  }
  throw new Error(
    `No in_progress task in execution context for agent ${agentId}. Active task IDs: [${activeTasks.map(t => t.taskId).join(', ')}]. Pass task_id explicitly if you are outside a task execution session.`,
  );
}

/** Validate an explicit task_id for submitForReview (no board guessing). */
function assertSubmittableTask(
  ts: { getTask(id: string): { status: string; assignedAgentId?: string } | undefined },
  taskId: string,
  agentId: string,
): { id: string; status: string; assignedAgentId?: string; reviewerId?: string } {
  const task = ts.getTask(taskId) as
    | { id?: string; status: string; assignedAgentId?: string; reviewerId?: string }
    | undefined;
  if (!task) throw new Error(`Task not found: ${taskId}`);
  if (task.assignedAgentId && task.assignedAgentId !== agentId) {
    throw new Error(
      `Cannot submit task ${taskId}: assigned to ${task.assignedAgentId}, not ${agentId}.`,
    );
  }
  if (task.status !== 'in_progress') {
    throw new Error(
      `Cannot submit task ${taskId}: status is "${task.status}", expected "in_progress".`,
    );
  }
  return { id: task.id ?? taskId, status: task.status, assignedAgentId: task.assignedAgentId, reviewerId: task.reviewerId };
}

/** Minimal interface that AgentManager needs from TaskService */
export interface RequirementServiceBridge {
  proposeRequirement(request: {
    orgId: string;
    title: string;
    description: string;
    priority?: string;
    source: string;
    createdBy: string;
    projectId?: string;
    tags?: string[];
  }): { id: string; title: string; status: string };
  listRequirements(filters?: {
    orgId?: string;
    status?: string;
    projectId?: string;
    createdBy?: string;
  }): Array<{
    id: string;
    title: string;
    description: string;
    status: string;
    priority: string;
    source: string;
    createdBy?: string;
    taskIds: string[];
  }>;
  updateRequirementStatus(
    id: string,
    status: string,
    userId?: string,
    actorType?: 'human' | 'agent' | 'system',
  ): { id: string; title: string; status: string };
  rejectRequirement(
    id: string,
    userId: string,
    reason: string
  ): { id: string; title: string; status: string };
  cancelRequirement(id: string, cancelledBy?: string, cancelledByType?: 'human' | 'agent' | 'system'): { id: string; title: string; status: string };
  updateRequirement(
    id: string,
    data: { title?: string; description?: string; priority?: string; tags?: string[] }
  ): { id: string; title: string; status: string };
  resubmitRequirement(
    id: string,
    updates?: { title?: string; description?: string; priority?: string; tags?: string[] }
  ): { id: string; title: string; status: string };
  getRequirement(id: string): {
    id: string; title: string; description: string; status: string;
    priority: string; source: string; createdBy?: string;
    approvedBy?: string; approvedAt?: string; rejectedReason?: string;
    taskIds: string[]; tags?: string[]; createdAt: string; updatedAt: string;
  } | undefined;
  getRequirementStatusHistory?(requirementId: string, limit?: number): unknown[];
  getActiveGoals?(orgId: string): Array<{ id: string; title: string; status: string; taskIds: string[]; goalConfig?: GoalConfig }>;
  enableGoalLoop?(reqId: string, config: { completionCriteria: string; maxIterations?: number; autoResume?: boolean }): Promise<void>;
  disableGoalLoop?(reqId: string): Promise<void>;
  getById?(id: string): { id: string; title: string; status: string; taskIds: string[]; goalConfig?: GoalConfig } | undefined;
}

export interface TaskServiceBridge {
  createTask(request: {
    orgId: string;
    title: string;
    description: string;
    priority?: string;
    assignedAgentId: string;
    reviewerId: string;
    reviewerType?: 'agent' | 'human';
    requirementId?: string;
    projectId?: string;
    blockedBy?: string[];
    createdBy?: string;
    creatorRole?: string;
    taskType?: string;
    notes?: string;
    acceptanceCriteria?: string;
    deadline?: string;
    scheduleConfig?: {
      cron?: string;
      every?: string;
      runAt?: string;
      timezone?: string;
      maxRuns?: number;
    };
  }): { id: string; title: string; status: string };
  listTasks(filters?: { orgId?: string; status?: string; assignedAgentId?: string; requirementId?: string; projectId?: string }): Array<{
    id: string;
    title: string;
    description: string;
    status: string;
    priority: string;
    updatedAt: string;
    assignedAgentId?: string;
    requirementId?: string;
  }>;
  queryTasks(opts?: {
    orgId?: string;
    status?: string;
    assignedAgentId?: string;
    priority?: string;
    projectId?: string;
    requirementId?: string;
    search?: string;
    sortBy?: string;
    sortOrder?: string;
    page?: number;
    pageSize?: number;
  }): {
    tasks: Array<{
      id: string;
      title: string;
      description: string;
      status: string;
      priority: string;
      updatedAt: string;
      assignedAgentId?: string;
      requirementId?: string;
    }>;
    total: number;
    page: number;
    pageSize: number;
    totalPages: number;
  };
  updateTaskStatus(id: string, status: string, updatedBy?: string, internal?: boolean, skipAutoStart?: boolean, updatedByType?: 'human' | 'agent' | 'system'): { id: string; title: string; status: string };
  getTask(
    id: string
  ):
    | {
        id: string;
        title: string;
        description: string;
        status: string;
        priority: string;
        assignedAgentId?: string;
        reviewerId?: string;
        subtasks?: Array<{ id: string; title: string; status: string }>;
      }
    | undefined;
  assignTask(id: string, agentId: string, updatedBy?: string): { id: string; status: string };
  addTaskNote(id: string, note: string, author?: string): void;
  updateTask(id: string, data: { description?: string; blockedBy?: string[] }, updatedBy?: string): { id: string; title: string; status: string };
  rejectTask(id: string, userId?: string): { id: string; title: string; status: string };
  addSubtask(taskId: string, title: string): { id: string; title: string; status: string };
  completeSubtask(taskId: string, subtaskId: string): { id: string; title: string; status: string };
  cancelSubtask(taskId: string, subtaskId: string): { id: string; title: string; status: string };
  getSubtasks?(taskId: string): Array<{ id: string; title: string; status: string }>;
  submitForReview(taskId: string, deliverables: Array<{ type: string; reference: string; summary: string; diffStats?: unknown; testResults?: unknown }>, reviewerId?: string, completionSummary?: string): Promise<{ id: string; status: string }>;
  requestRevision(taskId: string, reason: string, author?: string): Promise<{ id: string; title: string; status: string }>;
  /** Re-open a pending task's creation-gate approval after a 打回 (see TaskService.requestTaskApproval). */
  requestTaskApproval?(taskId: string, actorId?: string): { taskId: string; approvalId: string };
  findDuplicateTasks?(orgId: string): Array<{ group: string; tasks: Array<{ id: string; title: string; status: string; createdAt: string }> }>;
  cleanupDuplicateTasks?(orgId: string): { cancelledIds: string[]; count: number };
  getTaskBoardHealth?(orgId: string): Record<string, unknown>;
  postTaskComment?(taskId: string, authorId: string, authorName: string, content: string, mentions?: string[], activityId?: string, opts?: { replyToId?: string }): Promise<{ id: string; comment?: Record<string, unknown> }>;
  postRequirementComment?(requirementId: string, authorId: string, authorName: string, content: string, mentions?: string[], activityId?: string, opts?: { replyToId?: string }): Promise<{ id: string; comment?: Record<string, unknown> }>;
  getRequirementComments?(requirementId: string): Array<{ id: string; authorId: string; authorName: string; content: string; replyToId?: string; replyToAuthor?: string; replyToContent?: string; createdAt: string }>;
  getTaskComments?(taskId: string): Promise<Array<{ id: string; authorId: string; authorName: string; content: string; replyToId?: string; replyToAuthor?: string; replyToContent?: string; createdAt: string }>>;
  getTaskStatusHistory?(taskId: string, limit?: number): unknown[];
  updateScheduleFields?(taskId: string, fields: { every?: string; cron?: string; maxRuns?: number; timezone?: string }): Promise<{ id: string; title: string; status: string }>;
}

export interface MCPServerConfig {
  command: string;
  args?: string[];
  env?: Record<string, string>;
}

export interface CreateAgentRequest {
  name: string;
  roleName?: string;
  orgId?: string;
  teamId?: string;
  agentRole?: 'manager' | 'worker';
  skills?: string[];
  heartbeatIntervalMs?: number;
  tools?: AgentToolHandler[];
  orgContext?: OrgContext;
  enableSandbox?: boolean;
  mcpServers?: Record<string, MCPServerConfig>;
  securityPolicy?: SecurityPolicy;
  profile?: AgentProfile;
  /** Override model config: when provided with modelMode 'custom', agent uses this provider */
  llmProvider?: string;
  /** When true, skip copying template role files (caller will provide custom files) */
  skipTemplateCopy?: boolean;
}

export interface RoleFileStatus {
  file: string;
  status: 'identical' | 'modified' | 'added_in_template' | 'agent_only';
}

export interface RoleUpdateStatus {
  agentId: string;
  roleId: string;
  templateId: string;
  hasTemplate: boolean;
  isUpToDate: boolean;
  files: RoleFileStatus[];
}

export interface RoleFileDiff {
  file: string;
  agentContent: string | null;
  templateContent: string | null;
}

export interface RoleSyncResult {
  agentId: string;
  success: boolean;
  error?: string;
  synced: string[];
}

/**
 * Agent 私有 EventBus → manager EventBus 的**转发白名单**。
 *
 * 契约（由 `scripts/architecture-guard.mjs` 的 `event-reachability` 规则强制）：
 * 凡是在 agent 私有 bus 上 `emit`、又在 agent 私有 bus 之外被订阅的事件，必须登记在本表；
 * 否则订阅方（`cli/start.ts` 的 WS 广播、TaskService 的自愈等）永远收不到 —— 相关路径会静默
 * 变成死代码。历史事故（审计 P0-1）：`agent:incomplete` 漏登，导致「任务执行闭包丢失 → 自动
 * 重调度」这条自愈路径从引入至今不可达，任务可永久卡在 `in_progress`。
 */
export const AGENT_FORWARDED_EVENTS = [
  'agent:activity-log',
  'agent:activity_log',
  'agent:started',
  'agent:stopped',
  'agent:paused',
  'agent:resumed',
  'agent:focus-changed',
  'agent:message',
  'agent:notify-user',
  'agent:ui-layout',
  'agent:escalation',
  'agent:heartbeat-interval-changed',
  // P0-1：任务执行闭包丢失的自愈信号（agent.ts / attention.ts 在私有 bus 上 emit，
  // start.ts 在 manager bus 上订阅）。补登前该事件永不可达。
  'agent:incomplete',
  // P0-1 同族：实体亲和锁冲突（conflictPolicy:'report'）的可见性信号。
  'agent:entity-conflict',
  'task:completed',
  'task:failed',
  'mailbox:new-item',
  'attention:decision',
  'attention:state-changed',
  'attention:triage',
] as const;

/**
 * 把 agent 私有 bus 上的事件转发到 manager bus。
 *
 * 抽为纯函数，便于单测直接验证「agent bus emit → manager bus 收到」的可达性契约
 * （`test/agent-event-forwarding.test.ts`），而不必构造完整的 AgentManager。
 */
export function wireAgentEventForwarding(
  agentBus: EventBus,
  managerBus: EventBus,
  events: readonly string[] = AGENT_FORWARDED_EVENTS,
): void {
  for (const eventName of events) {
    agentBus.on(eventName, (payload: unknown) => {
      managerBus.emit(eventName, payload);
    });
  }
}

export class AgentManager {
  private agents = new Map<string, Agent>();
  private eventBus: EventBus;
  private llmRouter: LLMRouter;
  private roleLoader: RoleLoader;
  private dataDir: string;
  private sharedDataDir?: string;
  private mcpManager: MCPClientManager;
  private browserSessionManager: BrowserSessionManager;
  private terminalSessionManager: TerminalSessionManager;
  private remoteDebuggingPort = 0;
  private autoClickAllowDialog = false;
  private chromeAutoClickRunning = false;
  /** Selected browser backend: 'embedded' (built-in Electron) or 'system-chrome' (Chrome extension). */
  private browserMode: 'embedded' | 'system-chrome' = 'embedded';
  /**
   * Element-selection strategy. Orthogonal to `browserMode`: that one picks WHICH browser runs,
   * this one picks HOW the agent chooses the element to act on.
   */
  private browserElementSelection: 'direct' | 'jev' = 'direct';
  private browserBridge: MarkusBrowserBridge;
  /** Desktop-only: Electron WebContentsView CDP backend (preferred over npx when set). */
  private embeddedBrowserHost: EmbeddedBrowserHost | null = null;
  /** Desktop-only: node-pty right-panel terminal backend. */
  private embeddedTerminalHost: EmbeddedTerminalHost | null = null;
  private globalSecurityPolicy?: SecurityPolicy;
  private globalMcpServers?: Record<string, MCPServerConfig>;
  /** When set, register native Feishu send tools (incl. local image upload). */
  private feishuToolsConfig?: FeishuToolsConfig;
  private skillRegistry?: SkillRegistry;
  private skillSearcher?: (query: string) => Promise<Array<{ name: string; description: string; source: string; slug?: string; author?: string; githubRepo?: string; githubSkillPath?: string }>>;
  private skillInstaller?: (request: Record<string, unknown>) => Promise<{ installed: boolean; name: string; method: string }>;
  private skillImporter?: (request: { path: string; name?: string; targetDir?: string; force?: boolean; sourceUrl?: string }) => Promise<{ name: string; method: string; path: string }>;
  private userApprovalRequester?: (opts: {
    agentId: string; agentName: string; title: string; description: string;
    options?: Array<{ id: string; label: string; description?: string }>;
    questions?: UserInputQuestion[];
    allowFreeform?: boolean; priority?: string; relatedTaskId?: string;
  }) => Promise<{ approved: boolean; comment?: string; selectedOption?: string; answers?: UserInputAnswer[] }>;
  private userNotifier?: (opts: { type: string; title: string; body: string; priority?: string; actionType?: string; actionTarget?: string; metadata?: Record<string, unknown> }) => void;
  private runtimeViewerContext?: { locale?: string; timezone?: string };
  /** org-manager injects this so session_rename on cs_* sessions persists to Sqlite + broadcasts to UI. */
  private sessionTitleUpdater?: (sessionId: string, title: string) => void;
  private taskService?: TaskServiceBridge;
  private projectService?: ProjectServiceBridge;
  private deliverableService?: DeliverableServiceBridge;
  private webUiBaseUrl?: string;
  private semanticSearch?: SemanticMemorySearch;
  private requirementService?: RequirementServiceBridge;
  private agentAuditCallback?: (
    agentId: string,
    event: {
      type: string;
      action: string;
      tokensUsed?: number;
      durationMs?: number;
      success: boolean;
      detail?: string;
    }
  ) => void;
  private escalationHandler?: (agentId: string, reason: string) => void;
  private approvalHandler?: (
    agentId: string,
    request: { toolName: string; toolArgs: Record<string, unknown>; reason: string; taskId?: string; sessionId?: string; chatSessionId?: string }
  ) => Promise<{ approved: boolean; comment?: string }>;
  private stateChangeHandler?: (
    agentId: string,
    state: { status: string; tokensUsedToday: number; activeTaskIds: string[]; lastError?: string; lastErrorAt?: string; currentActivity?: AgentActivity; lastHeartbeat?: string }
  ) => void;
  private disabledChangeHandler?: (agentId: string, disabled: boolean) => void;
  /** Grace timers for releasing scoped MCP processes after agent goes idle */
  private mcpReleaseTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private static readonly MCP_IDLE_GRACE_MS = 30_000;
  private activityCallbacks?: {
    onStart: (activity: AgentActivity & { agentId: string }) => void;
    onLog: (data: { activityId: string; agentId: string; seq: number; type: string; content: string; metadata?: Record<string, unknown> }) => void;
    onEnd: (activityId: string, summary: { endedAt: string; totalTokens: number; totalTools: number; success: boolean }) => void;
  };
  private recallCallbacks?: RecallCallbacks;
  private delegationManager: DelegationManager;
  private _maxToolIterations = Infinity;
  private _concurrentConfig?: AgentConcurrentConfig;
  private _maxPendingTasksPerAgent?: number;
  private _codingToolsEnabled = false;
  private _codingToolsConfigs?: Record<string, CodingToolConfig>;
  private templateRegistry?: TemplateRegistry;
  private builderService?: { listArtifacts: (type?: 'agent' | 'team' | 'skill') => Array<{ type: string; name: string; description?: string }>; installArtifact: (type: 'agent' | 'team' | 'skill', name: string, teamId?: string) => Promise<{ type: string; installed: unknown }> };
  private hubClient?: { search: (opts?: { type?: string; query?: string }) => Promise<Array<{ id: string; name: string; type: string; description: string; author: string; version?: string; downloads?: number }>>; downloadAndInstall: (itemId: string) => Promise<{ type: string; installed: unknown }> };
  private teamUpdater?: (teamId: string, data: { name?: string; description?: string }) => Promise<{ id: string; name: string; description?: string }>;
  private teamCreator?: (orgId: string, name: string, description?: string) => Promise<{ id: string; name: string; description?: string }>;
  private orgTeamLister?: (orgId: string) => Array<{ id: string; name: string; description?: string; memberAgentIds: string[] }>;
  private agentConfigPersister?: (agentId: string, data: Record<string, unknown>) => Promise<void>;

  private groupChatHandlers?: {
    sendGroupMessage: (
      channelKey: string,
      message: string,
      senderId: string,
      senderName: string,
      replyToId?: string,
      /** Parent exchange (trace-style): outbound inherits it so a request and
       *  the answer it provokes share one chainId. Absent = new conversation. */
      chain?: { chainId?: string; depth?: number },
    ) => Promise<string>;
    createGroupChat: (
      name: string,
      creatorId: string,
      creatorName: string,
      memberIds: string[]
    ) => Promise<{ id: string; name: string }>;
    listGroupChats: () => Promise<
      Array<{ id: string; name: string; type: string; channelKey: string }>
    >;
    getChannelMessages?: (
      channelKey: string,
      limit: number,
      before?: string
    ) => Promise<{ messages: Array<{ id?: string; senderName: string; senderType: string; text: string; replyToId?: string; replyToSender?: string; replyToText?: string; createdAt: string }>; hasMore: boolean }>;
    getChatSessionMessages?: (
      sessionId: string,
      limit: number,
      before?: string,
      agentId?: string,
    ) => Promise<{ messages: Array<{ id?: string; role: string; text: string; createdAt: string }>; hasMore: boolean }>;
    ensureDmChannel?: (
      channelKey: string,
      member1: { id: string; name: string },
      member2: { id: string; name: string },
    ) => Promise<{ channelKey: string }>;
  };

  private buildDeliverableCallbacks(agentId: string, projectId?: string): Pick<
    ProjectToolsContext,
    'deliverableCreate' | 'deliverableSearch' | 'deliverableList' | 'deliverableUpdate' | 'deliverableRead'
  > {
    if (!this.deliverableService) return {};
    const ds = this.deliverableService;
    return {
      deliverableCreate: async (opts) => {
        // `tags` 是 string[] 直通：deliverable_create 不再 join 成逗号串，这里
        // 也就不该再 split。那段“join → split”往返正是把数组写成 `"a,b"` 的来源。
        const tags = opts.tags;
        return ds.create({
          type: opts.type as 'file' | 'directory',
          title: opts.title,
          summary: opts.summary,
          reference: opts.reference,
          format: opts.format,
          tags,
          agentId,
          // Prefer per-call project_id from the tool; fall back to callback-scoped project.
          projectId: opts.projectId ?? projectId,
          source: opts.source,
          knowledgeRoot: opts.knowledgeRoot,
          content: opts.content,
        });
      },
      deliverableSearch: async (opts) => {
        return ds.search({
          query: opts.query,
          projectId: opts.projectId,
          agentId: opts.agentId,
          type: opts.type,
          source: opts.source,
          limit: opts.limit,
        }).results;
      },
      deliverableList: async (opts) => {
        return ds.search({
          projectId: opts.projectId,
          agentId: opts.agentId,
          type: opts.type,
          status: opts.status,
          source: opts.source,
          limit: opts.limit,
        }).results;
      },
      deliverableUpdate: async (id, data) => {
        // string[] 直通（同 deliverableCreate：join→split 往返已删除）
        const tags = data.tags;
        return ds.update(id, {
          title: data.title,
          summary: data.summary,
          reference: data.reference,
          status: data.status,
          tags,
        });
      },
      deliverableRead: async ({ reference, projectId }) => {
        if (!reference) return null;
        // Knowledge-read prefix guard (T4 review suggestion ①): when the caller
        // scopes the read to a project, only allow files inside the project's
        // bound knowledge directories (knowledge_base_paths). This keeps the
        // knowledge tool from degrading into an arbitrary file-read primitive.
        if (projectId && this.projectService) {
          const roots = this.projectService.getProject(projectId)?.knowledgeBasePaths ?? [];
          // A project with NO bound knowledge roots has nothing to read — the
          // unbound case must not silently open arbitrary files.
          if (roots.length === 0) {
            log.warn('kb_read rejected: project has no knowledge base paths', { projectId });
            return null;
          }
          const resolvedRef = resolve(reference);
          const withinRoot = roots.some((root) => {
            const resolvedRoot = resolve(root);
            return resolvedRef === resolvedRoot || resolvedRef.startsWith(resolvedRoot + sep);
          });
          if (!withinRoot) return null;
        }
        // A missing file is not readable — report null so the tool returns an
        // error instead of a misleading success with empty content.
        if (!existsSync(reference)) return null;
        try {
          const content = await extractTextFromFile(reference);
          return { content, reference };
        } catch {
          return null;
        }
      },
    };
  }

  constructor(options: {
    llmRouter: LLMRouter;
    roleLoader?: RoleLoader;
    dataDir?: string;
    sharedDataDir?: string;
    eventBus?: EventBus;
    securityPolicy?: SecurityPolicy;
    mcpServers?: Record<string, MCPServerConfig>;
    feishuToolsConfig?: FeishuToolsConfig;
    skillRegistry?: SkillRegistry;
    taskService?: TaskServiceBridge;
    templateRegistry?: TemplateRegistry;
  }) {
    this.llmRouter = options.llmRouter;
    this.roleLoader = options.roleLoader ?? new RoleLoader();
    this.dataDir = options.dataDir ?? markusPath('agents');
    this.sharedDataDir = options.sharedDataDir;
    this.eventBus = options.eventBus ?? new EventBus();
    // P1-10：启动期接线 Anthropic 精确 token 计数。
    // 此前 `initTokenCounter()` 只在测试里被调用，生产启动路径从未启用它 →
    // Claude 全部走启发式估算，长上下文预算精度无保证（“入口存在、接线缺失”类）。
    // 按已配置的环境变量初始化；缺 key 时保持启发式，不影响启动。
    initTokenCounter({
      anthropicApiKey: process.env['ANTHROPIC_API_KEY'],
      anthropicBaseUrl: process.env['ANTHROPIC_BASE_URL'],
    });
    this.mcpManager = new MCPClientManager();
    this.mcpManager.setOnReconnect((serverName) => {
      this.triggerChromeDialogAutoClick(serverName);
    });
    // P1-11：MCP 工具清单变化（退出→[] / 连接→最新 tools/list）时保持可见，
    // 避免 stale 工具静默残留、新增工具不可见。
    this.mcpManager.setOnToolsChanged((serverName, tools) => {
      log.info('MCP tool set changed', {
        serverName,
        toolCount: tools.length,
        tools: tools.map(t => t.name),
      });
      this.eventBus.emit('agent:mcp-tools-changed', {
        serverName,
        toolCount: tools.length,
        toolNames: tools.map(t => t.name),
      });
    });
    this.browserSessionManager = new BrowserSessionManager();
    this.browserSessionManager.onOwnershipChange((event) => {
      this.eventBus.emit('browser:tab-ownership', event);
    });
    this.terminalSessionManager = new TerminalSessionManager();
    this.terminalSessionManager.onOwnershipChange((event) => {
      this.eventBus.emit('terminal:session-ownership', event);
    });
    this.browserBridge = new MarkusBrowserBridge();
    this.globalSecurityPolicy = options.securityPolicy;
    this.globalMcpServers = options.mcpServers;
    this.feishuToolsConfig = options.feishuToolsConfig;
    this.skillRegistry = options.skillRegistry;
    this.taskService = options.taskService;

    const embeddingApiKey = process.env['OPENAI_API_KEY'] ?? process.env['EMBEDDING_API_KEY'];
    if (embeddingApiKey) {
      const embeddingProvider = new OpenAIEmbeddingProvider({
        apiKey: embeddingApiKey,
        baseUrl: process.env['EMBEDDING_BASE_URL'] ?? process.env['OPENAI_BASE_URL'],
        model: process.env['EMBEDDING_MODEL'],
      });
      const vectorStore = new LocalVectorStore(this.dataDir);
      this.semanticSearch = new SemanticMemorySearch(embeddingProvider, vectorStore);
      this.semanticSearch.initialize().then(ok => {
        if (ok) log.info('Semantic memory search initialized (LocalVectorStore)');
        else log.warn('Semantic memory search initialization failed');
      }).catch(err => {
        log.warn('Semantic memory search init error', { error: String(err) });
      });
    }

    this.delegationManager = new DelegationManager();
    this.delegationManager.onDelegationReceived(async (envelope, delegation) => {
      const targetAgent = this.agents.get(envelope.to);
      if (!targetAgent) {
        log.warn('Delegation target agent not found', { to: envelope.to });
        return;
      }

      if (this.taskService) {
        const task = this.taskService.createTask({
          orgId: targetAgent.config.orgId,
          title: delegation.title,
          description: delegation.description,
          priority: delegation.priority ?? 'medium',
          assignedAgentId: envelope.to,
          reviewerId: envelope.from,
          createdBy: envelope.from,
          creatorRole: 'manager',
          notes: delegation.context,
          acceptanceCriteria: delegation.expectedOutput,
          deadline: delegation.deadline,
        });
        log.info('Delegation created real task', {
          taskId: task.id,
          delegatedTo: envelope.to,
          from: envelope.from,
          status: task.status,
        });
      } else {
        // No task service — send as a direct message to the agent
        await targetAgent.sendMessage(
          `[Delegated Task from ${envelope.from}]\nTitle: ${delegation.title}\nDescription: ${delegation.description}\nPriority: ${delegation.priority}`,
          envelope.from,
          { name: envelope.from, role: 'manager' },
          { sourceType: 'a2a_message', sessionId: `sys_${envelope.from}_${Date.now()}`, waitForReply: false }
        );
      }
    });
    this.templateRegistry = options.templateRegistry;
    mkdirSync(this.dataDir, { recursive: true });
  }

  get maxToolIterations(): number {
    return this._maxToolIterations;
  }

  set maxToolIterations(value: number) {
    this._maxToolIterations = value <= 0 ? Infinity : value;
  }

  /**
   * Per-agent *pending-task* cap（智能体设置 → 任务上限）。
   *
   * 语义：该上限对**每个 agent 各自独立计数**生效（每个 agent 累计自己名下未
   * 终结任务数后与该值比较）。`undefined` 表示「未配置」→ TaskService 回退到
   * 治理策略默认值（20）。
   *
   * 单一事实源：唯一写入点 = POST /api/settings/agent（与 maxToolIterations /
   * concurrentConfig 同一条链路），落库 markus.json `agent.maxPendingTasksPerAgent`，
   * 启动时由 createServices 读回。本 getter 只被 TaskService.checkTaskLimits 读取。
   */
  get maxPendingTasksPerAgent(): number | undefined {
    return this._maxPendingTasksPerAgent;
  }

  set maxPendingTasksPerAgent(value: number | undefined) {
    this._maxPendingTasksPerAgent = value && value > 0 ? value : undefined;
  }

  get concurrentConfig(): AgentConcurrentConfig | undefined {
    return this._concurrentConfig;
  }

  set concurrentConfig(value: AgentConcurrentConfig | undefined) {
    this._concurrentConfig = value;
    // 热传播：同步到所有已加载 agent 的 attention（无需重启进程）。
    for (const info of this.listAgents()) {
      try {
        const agent = this.getAgent(info.id);
        if (!agent) continue;
        const cfg = value ?? { enabled: true, maxWorkers: 3 };
        if (agent.config) agent.config.concurrent = cfg;
        // 统一并发闸：worker 数与任务并发上限一起改（applyConcurrency 内部同源计算）。
        // 旧实现只在 `enabled === true` 时下发，导致「运行中关掉并发」不会把
        // workerCount / 任务闸降回 1；这里始终下发，enabled:false ⇒ 两者都回 1。
        agent.applyConcurrency?.(cfg);
        agent.attention?.setConflictPolicy(cfg.conflictPolicy ?? 'auto');
      } catch { /* 单个 agent 传播失败不阻塞整体 */ }
    }
  }

  get codingToolsEnabled(): boolean {
    return this._codingToolsEnabled;
  }

  setCodingToolsConfig(config?: {
    enabled?: boolean;
    tools?: Record<string, {
      enabled?: boolean;
      binaryPath?: string;
      defaultArgs?: string[];
      timeoutMs?: number;
      env?: Record<string, string>;
      defaultModel?: string;
      maxBudgetPerSessionUsd?: number;
      approvalRequired?: boolean;
    }>;
  }): void {
    this._codingToolsEnabled = config?.enabled ?? false;
    if (config?.tools) {
      this._codingToolsConfigs = {};
      for (const [name, cfg] of Object.entries(config.tools)) {
        this._codingToolsConfigs[name] = {
          tool: name as CodingToolName,
          enabled: cfg.enabled ?? true,
          binaryPath: cfg.binaryPath,
          defaultArgs: cfg.defaultArgs,
          timeoutMs: cfg.timeoutMs,
          env: cfg.env,
          defaultModel: cfg.defaultModel,
          maxBudgetPerSessionUsd: cfg.maxBudgetPerSessionUsd,
          approvalRequired: cfg.approvalRequired,
        };
      }
    } else {
      this._codingToolsConfigs = undefined;
    }
  }

  private async registerCodingTools(agent: Agent): Promise<void> {
    // ── TEMPORARY DISABLING (2026-08) ─────────────────────────────────────────
    // 编程工具（Claude Code / Codex / Cursor）集成被判定为过度工程，暂从 agent
    // 工具面移除，避免出现在 agent 上下文中。agent 如需写代码，自行直接用
    // 文件/终端工具即可。要恢复时，删除下面两行 return 即可（连同下面的
    // !_codingToolsEnabled 检查一起恢复）。
    void agent;
    return;
    // ── /TEMPORARY DISABLING ──────────────────────────────────────────────────
    // if (!this._codingToolsEnabled) return;

    const { createCodingTools } = await import('./coding-tools/index.js');
    const codingTools = createCodingTools({
      serverUrl: this.webUiBaseUrl,
      getConfigs: () => this._codingToolsConfigs,
    });
    for (const tool of codingTools) {
      agent.registerTool(tool);
    }
  }

  setBrowserBringToFront(value: boolean): void {
    this.browserSessionManager.bringToFront = value;
  }

  setBrowserAutoCloseTabs(value: boolean): void {
    this.browserSessionManager.autoCloseTabs = value;
  }

  setBrowserRemoteDebuggingPort(port: number): void {
    this.remoteDebuggingPort = port;
  }

  setBrowserAutoClickAllowDialog(enabled: boolean): void {
    this.autoClickAllowDialog = enabled;
  }

  setBrowserMode(mode: 'embedded' | 'system-chrome'): void {
    this.browserMode = mode;
  }

  /**
   * 'direct' (default) = the model reads the a11y snapshot and picks the element itself.
   * 'jev' = code enumerates candidates and a decision model ranks them.
   */
  setBrowserElementSelection(mode: 'direct' | 'jev'): void {
    this.browserElementSelection = mode;
  }

  getBrowserElementSelection(): 'direct' | 'jev' {
    return this.browserElementSelection;
  }

  /**
   * Wire the Electron embedded-browser CDP host (desktop only).
   * When available, browser tools prefer this over npx chrome-devtools-mcp
   * so the agent operates on the same right-panel tabs the user sees.
   * The Chrome extension bridge still wins when connected (user's real Chrome).
   */
  setEmbeddedBrowserHost(host: EmbeddedBrowserHost | null): void {
    this.embeddedBrowserHost = host;
  }

  /**
   * Wire the Electron embedded-terminal PTY host (desktop only).
   * When available, terminal__* tools operate on the same right-panel tabs the user sees.
   */
  setEmbeddedTerminalHost(host: EmbeddedTerminalHost | null): void {
    this.embeddedTerminalHost = host;
    this.terminalSessionManager.setHost(host);
    // Register tools on already-loaded agents so host hot-wiring after create still works.
    if (host?.available()) {
      for (const [agentId, agent] of this.agents) {
        this.attachTerminalTools(agent, agentId);
      }
    }
  }

  /** Current agent→terminal ownership for right-panel UI badges. */
  getTerminalSessionOwnership(): Array<{ terminalId: string; agentId: string; agentName: string }> {
    return this.terminalSessionManager.listOwnership().map(({ terminalId, agentId }) => {
      let agentName = agentId;
      try {
        const agent = this.agents.get(agentId);
        if (agent) agentName = agent.config.name || agentId;
      } catch { /* agent may be mid-removal */ }
      return { terminalId, agentId, agentName };
    });
  }

  private attachTerminalTools(agent: Agent, agentId: string): void {
    const host = this.embeddedTerminalHost;
    if (!host?.available()) return;
    // Avoid duplicate registration if host is re-wired.
    if (agent.hasToolPrefix('terminal')) return;
    const handlers = this.terminalSessionManager.wrapToolHandlers(
      createTerminalToolHandlers(host),
      agentId,
    );
    for (const tool of handlers) agent.registerTool(tool);
  }

  startBrowserBridge(port?: number): void {
    if (port !== undefined) {
      this.browserBridge = new MarkusBrowserBridge(port);
    }
    this.browserBridge.onEvent((event, data) => {
      if (event === 'tab_closed') {
        const { pageId } = (data ?? {}) as { pageId?: number };
        this.browserSessionManager.handleTabClosed(pageId);
      }
    });
    this.browserBridge.start();
  }

  stopBrowserBridge(): void {
    this.browserBridge.stop();
  }

  get browserExtensionConnected(): boolean {
    return this.browserBridge.connected;
  }

  getBrowserBridge(): MarkusBrowserBridge {
    return this.browserBridge;
  }

  /** Current agent→page ownership for embedded-browser UI badges. */
  getBrowserTabOwnership(): Array<{ pageId: number; agentId: string; agentName: string }> {
    return this.browserSessionManager.listOwnership().map(({ pageId, agentId }) => {
      let agentName = agentId;
      try {
        const agent = this.agents.get(agentId);
        if (agent) agentName = agent.config.name || agentId;
      } catch { /* agent may be mid-removal */ }
      return { pageId, agentId, agentName };
    });
  }

  async runQuickBrowserTest(): Promise<BrowserTestResult> {
    // 'system-chrome' mode: the full quick test targets the Chrome extension bridge.
    // Default 'embedded' mode: run a lightweight check against the built-in browser.
    if (this.browserMode === 'system-chrome') {
      return runQuickBrowserTest(this.browserBridge, this.browserSessionManager);
    }
    return this.runEmbeddedBrowserQuickTest();
  }

  /**
   * Lightweight connectivity check for the embedded (built-in) browser backend.
   * Creates a throwaway about:blank page, evaluates a script, then closes it.
   * Returns the same BrowserTestResult shape the Settings UI already consumes.
   */
  private async runEmbeddedBrowserQuickTest(): Promise<BrowserTestResult> {
    const t0 = Date.now();
    const steps: BrowserTestResult['steps'] = [];
    const host = this.embeddedBrowserHost;

    const fail = (summary: string): BrowserTestResult => ({
      connected: false,
      steps,
      totalDurationMs: Date.now() - t0,
      passed: 0,
      failed: 0,
      summary,
    });

    if (!host || !host.available()) {
      return fail('Embedded browser not available (desktop host not injected or browser window closed)');
    }

    let created = 0;

    const step = async (group: string, name: string, fn: () => Promise<void>): Promise<void> => {
      const st = Date.now();
      try {
        await fn();
        steps.push({ name, group, passed: true, durationMs: Date.now() - st });
      } catch (err) {
        steps.push({
          name,
          group,
          passed: false,
          durationMs: Date.now() - st,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    };

    await step('Setup', 'new_page (about:blank)', async () => {
      const r = await host!.callTool('new_page', { url: 'about:blank' });
      if (r.error) throw new Error(`Failed to create embedded page: ${r.error}`);
      const m = /(\d+):.*\[selected\]/.exec(r.content);
      if (!m) throw new Error(`Could not parse created pageId: ${String(r.content).slice(0, 80)}`);
      created = parseInt(m[1], 10);
    });

    await step('Verify', 'evaluate_script (document.title)', async () => {
      const r = await host!.callTool('evaluate_script', { expression: 'document.title' });
      if (r.error) throw new Error(r.error);
    });

    if (created > 0) {
      await step('Cleanup', 'close_page', async () => {
        const r = await host!.callTool('close_page', { pageId: created });
        if (r.error) throw new Error(r.error);
      });
    }

    const passed = steps.filter((s) => s.passed).length;
    const failed = steps.length - passed;
    return {
      connected: failed === 0 && created > 0,
      steps,
      totalDurationMs: Date.now() - t0,
      passed,
      failed,
      summary: failed === 0 ? 'Embedded browser OK' : `Embedded browser check failed (${failed}/${steps.length} steps)`,
    };
  }

  runChaosBrowserTest(opts: {
    durationMs?: number;
    agentCount?: number;
    signal?: AbortSignal;
  }): AsyncGenerator<ChaosEvent> {
    return runChaosBrowserTest(this.browserBridge, this.browserSessionManager, opts);
  }

  /**
   * When remoteDebuggingPort is configured, replace --autoConnect with
   * --browserUrl so that the chrome-devtools MCP server reuses a persistent
   * debugging connection instead of requesting a new permission each time.
   */
  private enrichChromeDevtoolsConfig(
    serverName: string,
    config: { command: string; args?: string[]; env?: Record<string, string> },
  ): { command: string; args?: string[]; env?: Record<string, string> } {
    if (serverName !== 'chrome-devtools' || this.remoteDebuggingPort <= 0) return config;
    const args = [...(config.args ?? [])];
    const autoIdx = args.indexOf('--autoConnect');
    if (autoIdx !== -1) args.splice(autoIdx, 1);
    if (!args.includes('--browserUrl') && !args.includes('--browser-url')) {
      args.push('--browserUrl', `http://127.0.0.1:${this.remoteDebuggingPort}`);
    }
    return { ...config, args };
  }

  /**
   * Native Feishu tools that cover gaps in lark-mcp (local image upload/send).
   * Register only — LIVE schemas require discover_tools (progressive disclosure).
   */
  private registerFeishuTools(agent: Agent): void {
    if (!this.feishuToolsConfig) return;
    try {
      const tools = createFeishuTools(this.feishuToolsConfig);
      const names: string[] = [];
      for (const tool of tools) {
        agent.registerTool(tool);
        names.push(tool.name);
      }
      log.info('Native Feishu tools registered (inactive until discover_tools)', {
        toolCount: tools.length,
        tools: names,
      });
    } catch (error) {
      log.warn('Failed to register native Feishu tools', { error: String(error) });
    }
  }

  /**
   * Trigger Chrome dialog auto-click with smart mutex.
   * Only one clicker process runs at a time. If triggered while already running,
   * it's a no-op (the running clicker will detect the dialog when it appears).
   */
  private triggerChromeDialogAutoClick(serverName: string): void {
    if (serverName !== 'chrome-devtools' || !this.autoClickAllowDialog) return;
    if (this.chromeAutoClickRunning) return;
    this.chromeAutoClickRunning = true;
    clickChromeAllowDialog(60)
      .catch((err) => { log.debug('Chrome dialog auto-click failed', { error: String(err) }); })
      .finally(() => { this.chromeAutoClickRunning = false; });
  }

  /**
   * Register chrome-devtools tools for an agent WITHOUT starting the MCP process.
   *
   * Tool handlers dynamically choose backend at CALL TIME:
   * - If the Chrome extension is connected, use the WebSocket bridge (no dialog, instant).
   * - Else if the Electron embedded browser host is available, use WebContentsView CDP.
   * - Otherwise, fall back to npx chrome-devtools-mcp (lazy-started on first call).
   *
   * This ensures agents created before the extension connects can still
   * use the bridge once it becomes available, and vice versa.
   */
  private async registerChromeDevtoolsLazy(
    agentId: string,
    serverName: string,
    serverConfig: { command: string; args?: string[]; env?: Record<string, string> },
  ): Promise<AgentToolHandler[]> {
    const toolDescriptors = getBridgeToolDescriptors();

    // Register npx config lazily so callToolScoped can auto-connect when needed.
    this.mcpManager.registerLazyScoped(serverName, serverConfig, agentId, toolDescriptors);

    let mcpTools: AgentToolHandler[] = toolDescriptors.map((tool) => ({
      name: `${serverName}__${tool.name}`,
      description: `[MCP:${serverName}] ${tool.description}`,
      inputSchema: tool.inputSchema,
      execute: async (args: Record<string, unknown>) => {
        if (this.browserMode === 'system-chrome') {
          // System Chrome mode: extension only — no embedded fallback.
          if (this.browserBridge.connected) {
            const result = await this.browserBridge.callTool(tool.name, args);
            if (result.error) return `Error: ${result.error}`;
            return result.content;
          }
          return `Browser backend unavailable: browser.mode is set to "system-chrome" but the Chrome extension is not connected. Open Settings → Browser Automation → switch to "内置浏览器", or install & connect the Chrome extension (tool: ${tool.name}).`;
        }
        // Default 'embedded' mode: prefer built-in browser, then extension, then npx.
        if (this.embeddedBrowserHost?.available()) {
          const result = await this.embeddedBrowserHost.callTool(tool.name, args);
          if (result.error) return `Error: ${result.error}`;
          return result.content;
        }
        if (this.browserBridge.connected) {
          const result = await this.browserBridge.callTool(tool.name, args);
          if (result.error) return `Error: ${result.error}`;
          return result.content;
        }
        // npx fallback — strip _pageId (npx MCP doesn't understand it;
        // tab selection is handled by ensureCorrectPage + select_page)
        const { _pageId, ...cleanArgs } = args;
        return this.mcpManager.callToolScoped(serverName, agentId, tool.name, cleanArgs);
      },
    }));

    mcpTools = this.browserSessionManager.wrapToolHandlers(mcpTools, agentId);
    this.browserSessionManager.setReconnector(agentId, serverName, async () => {
      if (!this.browserBridge.connected && !this.embeddedBrowserHost?.available()) {
        await this.mcpManager.disconnectServerScoped(serverName, agentId);
        await this.mcpManager.connectServerScoped(serverName, serverConfig, agentId);
      }
    });
    return mcpTools;
  }

  setTaskService(taskService: TaskServiceBridge): void {
    this.taskService = taskService;
  }

  setProjectService(projectService: ProjectServiceBridge): void {
    this.projectService = projectService;
  }

  setRequirementService(requirementService: RequirementServiceBridge): void {
    this.requirementService = requirementService;
  }


  setDeliverableService(deliverableService: DeliverableServiceBridge): void {
    this.deliverableService = deliverableService;
  }

  private workflowToolsFactory?: (teamId: string) => WorkflowToolsContext | null;

  setWorkflowToolsFactory(factory: (teamId: string) => WorkflowToolsContext | null): void {
    this.workflowToolsFactory = factory;
  }

  private wireWorkflowForManager(agent: Agent, teamId: string): void {
    if (!this.workflowToolsFactory) return;
    const wfFactory = this.workflowToolsFactory;

    agent.setWorkflowContextFetcher(() => {
      try {
        const wfCtx = wfFactory(teamId);
        if (!wfCtx) return undefined;
        return {
          activeRuns: wfCtx.getActiveRuns().map(r => ({
            workflowName: r.workflowName,
            runNumber: r.runNumber,
            status: r.status,
            taskCount: r.taskIds.length,
            startedAt: r.startedAt,
          })),
          availableWorkflows: wfCtx.listWorkflows().map(w => ({
            name: w.name,
            description: w.description,
            stepCount: w.stepCount,
          })),
        };
      } catch (err) {
        log.debug('Failed to fetch workflow context', { teamId, error: String(err) });
        return undefined;
      }
    });

    const wfCtx = wfFactory(teamId);
    if (wfCtx) {
      for (const tool of createWorkflowTools(wfCtx)) {
        agent.registerTool(tool);
      }
    }
  }

  setWebUiBaseUrl(url: string): void {
    this.webUiBaseUrl = url.replace(/\/+$/, '');
  }

  setSkillSearcher(cb: (query: string) => Promise<Array<{ name: string; description: string; source: string; slug?: string; author?: string; githubRepo?: string; githubSkillPath?: string }>>): void {
    this.skillSearcher = cb;
  }

  setSkillInstaller(cb: (request: Record<string, unknown>) => Promise<{ installed: boolean; name: string; method: string }>): void {
    this.skillInstaller = cb;
  }

  setSkillImporter(cb: (request: { path: string; name?: string; targetDir?: string; force?: boolean; sourceUrl?: string }) => Promise<{ name: string; method: string; path: string }>): void {
    this.skillImporter = cb;
  }

  setUserApprovalRequester(cb: (opts: {
    agentId: string; agentName: string; title: string; description: string;
    options?: Array<{ id: string; label: string; description?: string }>;
    questions?: UserInputQuestion[];
    allowFreeform?: boolean; priority?: string; relatedTaskId?: string;
  }) => Promise<{ approved: boolean; comment?: string; selectedOption?: string; answers?: UserInputAnswer[] }>): void {
    this.userApprovalRequester = cb;
    for (const info of this.listAgents()) {
      try { this.getAgent(info.id).setUserApprovalRequester(cb); } catch (err) { log.debug('Failed to set approval requester on agent', { agentId: info.id, error: String(err) }); }
    }
  }

  /**
   * Set the locale/timezone used to localize prompts for autonomous runs
   * (typically the org owner's preferences). Applies to all current agents
   * and is remembered for agents created later.
   */
  setRuntimeViewerContext(ctx: { locale?: string; timezone?: string } | undefined): void {
    this.runtimeViewerContext = ctx;
    for (const info of this.listAgents()) {
      try { this.getAgent(info.id).setRuntimeViewerContext(ctx); } catch (err) { log.debug('Failed to set runtime viewer context on agent', { agentId: info.id, error: String(err) }); }
    }
  }

  /**
   * Inject a hook that persists a chat (cs_*) session title renames into the
   * Sqlite chat store and broadcasts to the web UI (wired by the org-manager
   * server). Lets agents rename the current chat session via session_rename.
   */
  setSessionTitleUpdater(fn: (sessionId: string, title: string) => void): void {
    this.sessionTitleUpdater = fn;
  }

  setUserNotifier(cb: (opts: { type: string; title: string; body: string; priority?: string; actionType?: string; actionTarget?: string; metadata?: Record<string, unknown> }) => void): void {
    this.userNotifier = cb;
    for (const info of this.listAgents()) {
      try { this.getAgent(info.id).setUserNotifier(cb); } catch (err) { log.debug('Failed to set notifier on agent', { agentId: info.id, error: String(err) }); }
    }
  }

  getSharedDataDir(): string | undefined {
    return this.sharedDataDir;
  }

  getDataDir(): string {
    return this.dataDir;
  }

  /**
   * Build a PathAccessPolicy for an agent, including shared workspace
   * and any project repos as read-only paths.
   */
  private static readonly BUILDER_ROLES = new Set(['agent-father', 'team-factory', 'skill-architect']);

  /**
   * Deliver an A2A message (status-broadcast shortcut + DM channel routing).
   *
   * This logic used to be duplicated verbatim in two tool-context factories in
   * this file, and only one copy forwarded the parent `chainId`. The effect was
   * that trace propagation worked or silently did nothing depending on which
   * context object a caller happened to receive — a bug that cannot be seen by
   * reading either copy alone. One implementation, one behaviour.
   */
  private async deliverA2AMessage(args: {
    targetId: string;
    message: string;
    fromId: string;
    fromName: string;
    priority?: number;
    waitForReply?: boolean;
  }): Promise<string> {
    const { targetId, message, fromId, fromName, priority, waitForReply } = args;

    // Lightweight path: informational broadcasts go to the daily log instead of
    // paying for a full LLM turn.
    try {
      const parsed = JSON.parse(message) as {
        type?: string;
        sender?: { name?: string };
        payload?: { status?: string; currentTask?: { title?: string } };
      };
      if (parsed.type === 'status_broadcast') {
        const target = this.getAgent(targetId);
        const senderName = parsed.sender?.name ?? fromName;
        const payload = parsed.payload ?? {};
        target.getMemory().writeDailyLog(
          targetId,
          `[Status] ${senderName}: ${payload.status ?? 'unknown'}${payload.currentTask?.title ? ' — ' + payload.currentTask.title : ''}`
        );
        return `Status from ${senderName} noted.`;
      }
    } catch { /* not JSON — expected for normal messages, fall through */ }

    // Route through a deterministic DM channel for persistent history and a
    // stable session: dm:a2a:{sorted_id_1}:{sorted_id_2}
    const dmChannelKey = this.getA2ADmChannelKey(fromId, targetId);
    if (this.groupChatHandlers?.ensureDmChannel) {
      const target = this.getAgent(targetId);
      await this.groupChatHandlers.ensureDmChannel(
        dmChannelKey,
        { id: fromId, name: fromName },
        { id: targetId, name: target.config.name },
      );
      // Parent exchange = the sender's current chain, so a request and the
      // answer it provokes can be correlated (trace-id rule).
      await this.groupChatHandlers.sendGroupMessage(
        dmChannelKey, message, fromId, fromName, undefined, this.getAgent(fromId)?.currentChain(),
      );
      return `Message sent via DM channel ${dmChannelKey}`;
    }

    // Fallback: direct enqueue when group chat handlers are not wired.
    const target = this.getAgent(targetId);
    const reply = await target.sendMessage(
      message,
      fromId,
      { name: fromName, role: this.getAgent(fromId)?.config.agentRole ?? 'worker' },
      {
        sourceType: 'a2a_message',
        sessionId: `a2a_${targetId}_${Date.now()}`,
        scenario: 'a2a',
        channelKey: dmChannelKey,
        priority: priority as 0 | 1 | 2 | 3 | 4 | undefined,
        waitForReply,
      }
    );
    return stripInternalBlocks(reply);
  }

  buildPathPolicy(agentId: string, workspacePath: string, roleDir?: string, teamDataDir?: string, builderArtifactsDir?: string): PathAccessPolicy {
    // Block writes to other agents' directories only.
    // The agent's own directory is excluded from the deny list.
    const agentOwnDir = join(this.dataDir, agentId);
    const denyWritePaths: string[] = [];
    if (existsSync(this.dataDir)) {
      for (const entry of readdirSync(this.dataDir, { withFileTypes: true })) {
        if (entry.isDirectory() && entry.name !== agentId) {
          denyWritePaths.push(join(this.dataDir, entry.name));
        }
      }
    }

    const policy: PathAccessPolicy = {
      primaryWorkspace: workspacePath,
      denyWritePaths: denyWritePaths.length ? denyWritePaths : undefined,
    };
    if (this.sharedDataDir) {
      policy.sharedWorkspace = this.sharedDataDir;
    }
    if (roleDir) {
      policy.roleDir = roleDir;
    }
    if (teamDataDir) {
      policy.teamDataDir = teamDataDir;
    }
    if (builderArtifactsDir) {
      policy.builderArtifactsDir = builderArtifactsDir;
    }
    return policy;
  }

  async createAgent(request: CreateAgentRequest): Promise<Agent> {
    if (!request.name?.trim()) throw new Error('Agent name is required');
    const id = genAgentId();
    const roleName = request.roleName || 'custom';
    const buildCustomRole = (): RoleTemplate => ({
      id: generateId('role'),
      name: request.name,
      description: '',
      category: 'custom' as RoleCategory,
      systemPrompt: `# ${request.name}\n\nYou are ${request.name}.`,
      defaultSkills: [],
      heartbeatChecklist: '',
      defaultPolicies: [],
      builtIn: false,
    });

    // A roleName may be a built-in template id (e.g. 'developer') or an
    // arbitrary custom slug from a builder-authored artifact (e.g. a team
    // member's 'content-director'). Custom slugs have no built-in template, so
    // loading them would throw — fall back to a placeholder custom role. The
    // caller copies the real ROLE.md files afterward and calls reloadRole(),
    // which replaces this transient role with the artifact's actual content.
    let isCustomRole = roleName === 'custom';
    let role: RoleTemplate;
    if (isCustomRole) {
      role = buildCustomRole();
    } else {
      try {
        role = this.roleLoader.loadRole(roleName);
      } catch {
        role = buildCustomRole();
        isCustomRole = true;
      }
    }

    const agentDataDir = join(this.dataDir, id);
    mkdirSync(agentDataDir, { recursive: true });

    const agentRoleDir = join(agentDataDir, 'role');
    mkdirSync(agentRoleDir, { recursive: true });

    if (!isCustomRole && !request.skipTemplateCopy) {
      const templateDir = this.roleLoader.resolveTemplateDir(roleName);
      if (templateDir) {
        for (const file of ['ROLE.md', 'HEARTBEAT.md', 'POLICIES.md', 'CONTEXT.md']) {
          const src = join(templateDir, file);
          if (existsSync(src)) copyFileSync(src, join(agentRoleDir, file));
        }
      }
    }

    const heartbeatPath = join(agentRoleDir, 'HEARTBEAT.md');
    if (!existsSync(heartbeatPath)) {
      writeFileSync(heartbeatPath, [
        '# Heartbeat Checklist',
        '',
        '- [ ] Check mailbox for new messages and respond to urgent items',
        '- [ ] Review assigned tasks — update progress, unblock if possible',
        '- [ ] Check team announcements for new information',
        '- [ ] Scan recent channel messages for anything requiring attention',
      ].join('\n'), 'utf-8');
    }

    // Create memory system directories (sessions/, daily-logs/) and knowledge.md.
    // NOTE: state.md is intentionally NOT created any more — it was the "short-lived
    // situational state" half of an old dual store that never got a write tool, and its
    // job is covered by the notebook's `system` tier. Not creating it keeps new agents
    // from inheriting a store nobody writes to.
    // See docs/architecture/memory-system.md §10.2 (option A).
    const sessionsDir = join(agentDataDir, 'sessions');
    const dailyLogsDir = join(agentDataDir, 'daily-logs');
    mkdirSync(sessionsDir, { recursive: true });
    mkdirSync(dailyLogsDir, { recursive: true });
    const knowledgePath = join(agentDataDir, 'knowledge.md');
    if (!existsSync(knowledgePath)) {
      writeFileSync(knowledgePath, [
        '# Knowledge',
        '',
        '## procedures',
        '',
        '## architecture',
        '',
        '## lessons-learned',
        '',
      ].join('\n'), 'utf-8');
    }

    const config: AgentConfig = {
      id,
      name: request.name,
      roleId: roleName,
      orgId: request.orgId ?? 'default',
      teamId: request.teamId,
      agentRole: request.agentRole ?? 'worker',
      skills: request.skills ?? role.defaultSkills,
      profile: request.profile,
      llmConfig: request.llmProvider
        ? { modelMode: 'custom' as const, primary: request.llmProvider }
        : { modelMode: 'default' as const, primary: this.llmRouter.defaultProviderName },
      channels: [],
      heartbeatIntervalMs: request.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS,
      concurrent: this._concurrentConfig,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    // Determine workspace: profile.workspacePath or default per-agent directory
    const workspacePath = request.profile?.workspacePath ?? join(this.dataDir, id, 'workspace');
    mkdirSync(workspacePath, { recursive: true });

    // Team managers get write access to team data dir for announcements/norms
    const teamDataDir = (request.teamId && request.agentRole === 'manager')
      ? markusPath('teams', request.teamId)
      : undefined;

    const builderArtifactsDir = markusPath('builder-artifacts');

    const pathPolicy = this.buildPathPolicy(id, workspacePath, agentRoleDir, teamDataDir, builderArtifactsDir);

    const basePolicy = request.securityPolicy ?? this.globalSecurityPolicy;
    const security = new SecurityGuard({
      ...basePolicy,
    });
    const agentMeta = {
      agentId: id,
      agentName: request.name,
      teamName: request.teamId,
      orgId: request.orgId ?? 'default',
    };
    const tools = request.tools ?? createBuiltinTools({ agentId: id, agentMeta, security, workspacePath, pathPolicy });

    const agentOpts: AgentOptions = {
      config,
      role,
      llmRouter: this.llmRouter,
      dataDir: agentDataDir,
      tools,
      orgContext: request.orgContext,
      pathPolicy,
      skillRegistry: this.skillRegistry,
      maxToolIterations: this._maxToolIterations,
      handbookPath: this.resolveHandbookPath(roleName),
    };

    const agent = new Agent(agentOpts);

    // (0.9.7 context-root-fix) Agent-driven context compaction: agents may
    // actively collapse stale session history on demand via the session tool.
    // ContextOS: also expose slot pinning + fragment archive management.
    const mem = agent.getMemory();
    const compactor: SessionCompactor = {
      compactOnDemand: (sessionId: string, keepLast: number) =>
        mem.compactSession(sessionId, keepLast),
      compactWithAnchor: (sessionId, keepLast, anchor) => {
        // Pin the structured anchor into the durable [SLOTS] segment so the
        // agent keeps its position even after compaction, then compact.
        if (mem.setSlot && anchor.goal) mem.setSlot(sessionId, 'goal', anchor.goal);
        if (mem.setSlot && anchor.done) mem.setSlot(sessionId, 'done', anchor.done);
        if (mem.setSlot && anchor.next) mem.setSlot(sessionId, 'next', anchor.next);
        const res = mem.compactSession(sessionId, keepLast);
        return { ...res, anchorKey: ['goal', 'done', 'next'].filter((k) => anchor[k as keyof typeof anchor]).join(',') || 'anchor' };
      },
    };
    const slotStore: SessionSlotStore = {
      getSlots: (sid) => mem.getSlots ? mem.getSlots(sid) : [],
      setSlot: (sid, k, v) => { if (mem.setSlot) mem.setSlot(sid, k, v); },
      removeSlot: (sid, k) => { if (mem.removeSlot) mem.removeSlot(sid, k); },
      serialize: (sid) => mem.serializeSlots ? mem.serializeSlots(sid) : '',
    };
    const fragmentStore: SessionFragmentStore = {
      retrieveFragments: (q, mx) => mem.retrieveFragments ? mem.retrieveFragments(q, mx) : [],
      includeFragment: (sid, fid) => mem.includeFragment ? mem.includeFragment(sid, fid) : { ok: false, message: 'unavailable' },
      purgeSessionFragments: (sid) => mem.purgeSessionFragments ? mem.purgeSessionFragments(sid) : 0,
      sessionStats: (sid) => mem.sessionStats ? mem.sessionStats(sid) : { messageCount: 0, slotKeys: [], fragmentCount: 0 },
    };

    // Progressive disclosure: skill catalog is metadata-only (name + description).
    // Full SKILL.md bodies enter context only after discover_tools activation.
    if (this.skillRegistry) {
      agent.setAvailableSkillCatalog(this.skillRegistry.getSkillCatalog());
    }

    // H9: resolve assigned skills that are not installed and stamp the agent so
    // the degradation is structurally visible (agent state → API → UI), not only
    // a log line. Shared computation with the restore path (see skill-warnings.ts);
    // the startup aggregate warn lives in OrgService after the restore loop.
    const skillWarnings = buildSkillWarnings(config, this.skillRegistry);
    setAgentSkillWarnings(agent, skillWarnings);
    if (skillWarnings.missing.length > 0) {
      log.debug(`Agent ${config.name} (${id}) references skills not found in registry`, {
        missing: skillWarnings.missing,
        available: skillWarnings.available,
      });
    }

    // Connect MCP servers for assigned skills (tools only — instructions on demand)
    if (this.skillRegistry && config.skills.length > 0) {
      // Connect MCP servers declared by explicitly assigned skills
      for (const skillName of config.skills) {
        const skill = this.skillRegistry.get(skillName);
        if (skill?.manifest.mcpServers) {
          const isolation = skill.manifest.isolation ?? 'shared';
          for (const [serverName, rawServerConfig] of Object.entries(skill.manifest.mcpServers)) {
            try {
              let mcpTools: AgentToolHandler[];
              const serverConfig = this.enrichChromeDevtoolsConfig(serverName, rawServerConfig);
              if (serverName === 'chrome-devtools') {
                // Lazy start: register tools now, connect on first call.
                // Startup semaphore in MCPClientManager serializes connections.
                mcpTools = await this.registerChromeDevtoolsLazy(id, serverName, serverConfig);
              } else if (isolation === 'per-agent' || isolation === 'pooled') {
                await this.mcpManager.connectServerScoped(serverName, serverConfig, id);
                mcpTools = this.mcpManager.getToolHandlersScoped(serverName, id);
                mcpTools = this.browserSessionManager.wrapToolHandlers(mcpTools, id);
                this.browserSessionManager.setReconnector(id, serverName, async () => {
                  await this.mcpManager.disconnectServerScoped(serverName, id);
                  await this.mcpManager.connectServerScoped(serverName, serverConfig, id);
                });
              } else {
                await this.mcpManager.connectServer(serverName, serverConfig);
                mcpTools = this.mcpManager.getToolHandlers(serverName);
              }
              const toolNames: string[] = [];
              for (const tool of mcpTools) {
                agent.registerTool(tool);
                toolNames.push(tool.name);
              }
              // Register only — activate via discover_tools when the agent needs them.
              log.info(`Skill ${skillName} MCP server ${serverName} registered for agent ${id}`, {
                toolCount: mcpTools.length, isolation, live: false,
              });
            } catch (error) {
              log.warn(`Failed to connect skill ${skillName} MCP server ${serverName} for agent ${id}`, {
                error: String(error),
              });
            }
          }
        }
      }
    }

    // Set skill MCP activator callback for runtime activation via discover_tools
    agent.setSkillMcpActivator(async (skillName, mcpServers) => {
      let tools: AgentToolHandler[] = [];
      const skill = this.skillRegistry?.get(skillName);
      const isolation = skill?.manifest.isolation ?? 'shared';
      for (const [serverName, rawSrvConfig] of Object.entries(mcpServers)) {
        // reuseGlobal: reuse the globally-configured MCP server (with real credentials from config)
        if ((rawSrvConfig as Record<string, unknown>).reuseGlobal === true) {
          const globalConfig = this.globalMcpServers?.[serverName];
          if (globalConfig) {
            const gc = this.enrichChromeDevtoolsConfig(serverName, globalConfig);
            await this.mcpManager.connectServer(serverName, gc);
          }
          const handlers = this.mcpManager.getToolHandlers(serverName);
          if (handlers.length > 0) tools.push(...handlers);
          continue;
        }
        const srvConfig = this.enrichChromeDevtoolsConfig(serverName, rawSrvConfig);
        if (serverName === 'chrome-devtools') {
          const chromeTools = await this.registerChromeDevtoolsLazy(id, serverName, srvConfig);
          tools.push(...chromeTools);
        } else if (isolation === 'per-agent' || isolation === 'pooled') {
          await this.mcpManager.connectServerScoped(serverName, srvConfig, id);
          tools.push(...this.mcpManager.getToolHandlersScoped(serverName, id));
        } else {
          await this.mcpManager.connectServer(serverName, srvConfig);
          tools.push(...this.mcpManager.getToolHandlers(serverName));
        }
      }
      // Wrap with BrowserSessionManager for per-agent tools (tab isolation)
      const hasChromeDevtools = Object.keys(mcpServers).includes('chrome-devtools');
      if (!hasChromeDevtools && (isolation === 'per-agent' || isolation === 'pooled')) {
        tools = this.browserSessionManager.wrapToolHandlers(tools, id);
        for (const [serverName, rawSrvConfig] of Object.entries(mcpServers)) {
          const srvConfig = this.enrichChromeDevtoolsConfig(serverName, rawSrvConfig);
          this.browserSessionManager.setReconnector(id, serverName, async () => {
            await this.mcpManager.disconnectServerScoped(serverName, id);
            await this.mcpManager.connectServerScoped(serverName, srvConfig, id);
          });
        }
      }
      return tools;
    });

    // Set skill search/install callbacks (injected by org-manager layer)
    if (this.skillSearcher) agent.setSkillSearcher(this.skillSearcher);
    if (this.skillInstaller) agent.setSkillInstaller(this.skillInstaller);
    if (this.skillImporter) agent.setSkillImporter(this.skillImporter);
    if (this.userApprovalRequester) agent.setUserApprovalRequester(this.userApprovalRequester);
    if (this.userNotifier) agent.setUserNotifier(this.userNotifier);
    if (this.runtimeViewerContext) agent.setRuntimeViewerContext(this.runtimeViewerContext);

    // A2A tools — every agent can message colleagues (all agents visible for cross-team)
    const a2aContext: A2AContext = {
      selfId: id,
      selfName: config.name,
      wakeAgent: async (agentId: string) => { await this.startAgent(agentId); },
      isRunning: (agentId: string) => {
        const a = this.agents.get(agentId);
        return !!a && a.getState().status !== 'offline';
      },
      listColleagues: () =>
        this.listAgents().map(a => {
          try {
            const ag = this.getAgent(a.id);
            return { ...a, skills: ag.config.skills };
          } catch (err) {
            log.debug('Agent not found while listing colleagues', { agentId: a.id, error: String(err) });
            return { ...a, skills: [] };
          }
        }),
      sendMessage: async (targetId: string, message: string, fromId: string, fromName: string, priority?: number, waitForReply?: boolean) =>
        this.deliverA2AMessage({ targetId, message, fromId, fromName, priority, waitForReply }),
      delegateTask: async (targetId: string, delegation: TaskDelegation) =>
        this.delegationManager.delegateTask(id, delegation, targetId),
      ...(this.groupChatHandlers
        ? {
            sendGroupMessage: this.groupChatHandlers.sendGroupMessage,
            createGroupChat: (name: string, memberIds: string[]) =>
              this.groupChatHandlers!.createGroupChat(name, id, config.name, memberIds),
            listGroupChats: this.groupChatHandlers.listGroupChats,
            getChannelMessages: this.groupChatHandlers.getChannelMessages,
            getChatSessionMessages: this.groupChatHandlers.getChatSessionMessages
              ? (sessionId: string, limit: number, before?: string) =>
                  this.groupChatHandlers!.getChatSessionMessages!(sessionId, limit, before, id)
              : undefined,
          }
        : {}),
    };
    for (const tool of createA2ATools(a2aContext)) agent.registerTool(tool);
    for (const tool of createStructuredA2ATools(a2aContext)) agent.registerTool(tool);

    // Memory tools — every agent can save/search/list memories
    for (const tool of createMemoryTools({
      agentId: id,
      agentName: config.name,
      memory: agent.getMemory(),
      semanticSearch: this.semanticSearch,
    })) {
      agent.registerTool(tool);
    }

    // Mailbox + working memory tools — every agent can inspect/manage queue and cognition
    const attnCtrl = agent.getAttentionController();
    const mailboxToolCtx: MailboxToolContext = {
      agentId: id,
      getMindState: () => attnCtrl.getMindState(),
      deferItem: (itemId, reason, ms) => attnCtrl.deferItem(itemId, reason, ms),
      dropItem: (itemId, reason) => attnCtrl.dropItem(itemId, reason),
      prioritizeItem: (itemId, p) => attnCtrl.prioritizeItem(itemId, p),
      updateWorkingMemory: (key, content) => agent.updateWorkingMemory(key, content),
      clearWorkingMemory: (key) => agent.clearWorkingMemory(key),
      getWorkingMemorySnapshot: () => agent.getWorkingMemorySnapshot(),
    };
    for (const tool of createMailboxTools(mailboxToolCtx)) agent.registerTool(tool);

    // Recall tool — agents can query their own execution history
    if (this.recallCallbacks) {
      agent.registerTool(createRecallTool({ agentId: id, ...this.recallCallbacks }));
    }
    // Session tool — agents can query and manage their OWN conversation sessions.
    // Uses the Memory-backed repo (the same session space the agent's context is
    // built from) so list/get/compact/pin/status/fragments stay consistent.
    // ContextOS fix: previously wired to the Sqlite `cs_*` repo here (split-brain
    // with MemoryStore `sess_*`); memory is the single source of truth.
    agent.registerTool(createSessionTool({
      agentId: id, chatSessionRepo: createMemorySessionRepo(mem),
      compactor, slotStore, fragmentStore,
      titleUpdater: this.sessionTitleUpdater,
      currentDbSessionId: () => agent.getDbSessionId(),
      currentMemorySessionId: () => agent.getCurrentSessionId(),
      resolveMemorySessionByDbSessionId: (dbId) => agent.getMemorySessionIdForDbSession(dbId),
      diskSessionCount: () => mem.countSessionsOnDisk?.() ?? 0,
    }));

    // Settings tools — agents can list providers and switch models via chat
    for (const tool of createSettingsTools({
      llmRouter: this.llmRouter,
      persistConfig: (updates) => { try { saveConfig(updates); } catch (err) { log.debug('Failed to persist config', { error: String(err) }); } },
      agent: this.buildSettingsAgentContext(id),
    })) {
      agent.registerTool(tool);
    }

    // Multi-modal tools — image generation, TTS, STT, video generation
    for (const tool of createMultiModalTools({
      resolveCandidates: (capabilityType) => this.llmRouter.resolveModalityCandidates(capabilityType),
      resolveProvider: (name) => {
        const hit = this.llmRouter.resolveProviderByName(name);
        return hit ? { provider: hit.provider, name: hit.name } : undefined;
      },
      listProviderNames: () => this.llmRouter.listRegisteredProviderNames(),
      isProviderDisabled: (name) => this.llmRouter.isProviderDisabled(name),
      resolveHubCredentials: () => this.llmRouter.getHubCredentials(),
    })) {
      agent.registerTool(tool);
    }

    await this.registerCodingTools(agent);

    if (this.semanticSearch) {
      agent.getContextEngine().setSemanticSearch(this.semanticSearch);
      agent.setSemanticSearch(this.semanticSearch);
    }

    // Task tools — every agent can create/list/update tasks
    if (this.taskService) {
      const ts = this.taskService;
      const orgId = config.orgId;
      const taskCtx: AgentTaskContext = {
        agentId: id,
        agentName: config.name,
        createTask: async params => {
          return ts.createTask({
            orgId,
            title: params.title,
            description: params.description,
            priority: params.priority,
            assignedAgentId: params.assignedAgentId,
            reviewerId: params.reviewerId,
            reviewerType: params.reviewerType,
            requirementId: params.requirementId,
            projectId: params.projectId,
            blockedBy: params.blockedBy,
            taskType: params.taskType,
            scheduleConfig: params.scheduleConfig,
            createdBy: id,
            creatorRole: 'worker',
          });
        },
        listTasks: async filter => {
          return ts.queryTasks({
            orgId,
            status: filter?.status as any,
            assignedAgentId: filter?.assignedToMe ? id : undefined,
            priority: filter?.priority as any,
            requirementId: filter?.requirementId,
            projectId: filter?.projectId,
            search: filter?.search,
            sortBy: filter?.sortBy as any,
            sortOrder: filter?.sortOrder as any,
            page: filter?.page,
            pageSize: filter?.pageSize,
          });
        },
        updateTaskStatus: async (taskId, status) => {
          // Explicit actor type — the platform must never INFER "who am I" from the
          // payload. T25 (2026-10-17): inference defaulted unknown ids to 'human'
          // and let an agent revert a completed card.
          return ts.updateTaskStatus(taskId, status, id, false, false, 'agent');
        },
        askAgent: async (toAgentId, question, contextText, timeoutSeconds) => {
          if (!toAgentId) throw new Error('agent_id is required');
          if (toAgentId === id) throw new Error('You cannot ask yourself — settle it from evidence, or ask the human with request_user_input.');
          let target: PeerAgent | undefined;
          try { target = this.getAgent(toAgentId) as unknown as PeerAgent; } catch { target = undefined; }
          if (!target) throw new Error(`Unknown agent "${toAgentId}" — use agent_list_colleagues to find a real one.`);
          const timeoutMs = Math.min(Math.max((timeoutSeconds ?? 300) * 1000, 5_000), 900_000);
          let fromName = 'another agent';
          try { fromName = (this.getAgent(id) as unknown as { name?: string }).name ?? fromName; } catch { /* keep fallback */ }
          const message = [
            question.trim(),
            contextText?.trim() ? `\n\nContext provided by ${fromName}:\n${contextText.trim()}` : '',
            '\n\n(You are being asked directly by another agent. Your reply is returned to them verbatim — answer in one paragraph. You may ask the human if you truly cannot answer; you cannot ask another agent.)',
          ].join('');
          const started = Date.now();
          try {
            const reply = await Promise.race([
              target.sendMessage(
                message, id, { name: fromName, role: 'agent' },
                { scenario: 'peer_query', sourceType: 'a2a_message', peerQueryDepth: 1, priority: MailboxPriorityLevel.high },
              ),
              new Promise<never>((_, reject) => {
                const t = setTimeout(() => reject(new Error('PEER_QUERY_TIMEOUT')), timeoutMs);
                (t as unknown as { unref?: () => void }).unref?.();
              }),
            ]);
            return { status: 'answered' as const, agent: { id: toAgentId }, answer: String(reply ?? '').trim(), elapsed_ms: Date.now() - started };
          } catch (err) {
            if (String(err).includes('PEER_QUERY_TIMEOUT')) {
              return {
                status: 'timeout' as const,
                agent: { id: toAgentId },
                elapsed_ms: Date.now() - started,
                message: `No answer within ${Math.round(timeoutMs / 1000)}s. Proceed on your best judgement and state the assumption you used, or ask the human with request_user_input.`,
              };
            }
            return { status: 'unavailable' as const, agent: { id: toAgentId }, elapsed_ms: Date.now() - started, message: String(err) };
          }
        },
        requestRevision: async (taskId, reason) => {
          return ts.requestRevision(taskId, reason, id);
        },
        requestTaskApproval: ts.requestTaskApproval
          ? async (taskId: string) => ts.requestTaskApproval!(taskId, id)
          : undefined,
        getTask: async taskId => {
          return ts.getTask(taskId) ?? null;
        },
        assignTask: async (taskId, agentId) => {
          return ts.assignTask(taskId, agentId, id);
        },
        addTaskNote: async (taskId, note, author) => {
          ts.addTaskNote(taskId, note, author);
        },
        updateTaskFields: async (taskId, fields) => {
          const task = ts.updateTask(taskId, fields, id);
          return { id: task.id, title: task.title, status: task.status };
        },
        updateScheduleConfig: async (taskId, config) => {
          if (!ts.updateScheduleFields) throw new Error('Schedule editing not supported');
          const task = await ts.updateScheduleFields(taskId, config);
          return { id: task.id, title: task.title, status: task.status };
        },
        cancelPendingTask: async (taskId) => {
          const task = ts.rejectTask(taskId, id);
          return { id: task.id, title: task.title, status: task.status };
        },
        addSubtask: async (taskId, title) => {
          return ts.addSubtask(taskId, title);
        },
        completeSubtask: async (taskId, subtaskId) => {
          return ts.completeSubtask(taskId, subtaskId);
        },
        cancelSubtask: async (taskId, subtaskId) => {
          return ts.cancelSubtask(taskId, subtaskId);
        },
        getSubtasks: async (taskId) => {
          const task = ts.getTask(taskId);
          return task?.subtasks ?? [];
        },
        submitForReview: async (summary, inputDeliverables, knownIssues, explicitTaskId) => {
          const taskId = explicitTaskId?.trim()
            ? assertSubmittableTask(ts, explicitTaskId.trim(), id).id
            : resolveCurrentTaskId(this.agents.get(id), ts, id);
          const task = assertSubmittableTask(ts, taskId, id);
          const reviewerId = task.reviewerId;
          const _validTypes = new Set(['file', 'directory']);
          const completionSummary = `${summary}${knownIssues ? `\n\nKnown issues: ${knownIssues}` : ''}`;
          const deliverables: Array<{ type: string; reference: string; summary: string }> = [];
          if (Array.isArray(inputDeliverables)) {
            for (const d of inputDeliverables) {
              if (d?.reference) {
                deliverables.push({
                  type: _validTypes.has(d.type ?? '') ? d.type! : 'file',
                  reference: d.reference, summary: d.summary || String(d.reference),
                });
              }
            }
          }
          return ts.submitForReview(taskId, deliverables, reviewerId, completionSummary);
        },
        proposeRequirement: this.requirementService
          ? async params => {
              return this.requirementService!.proposeRequirement({
                orgId,
                title: params.title,
                description: params.description,
                priority: params.priority,
                source: 'agent',
                createdBy: id,
                projectId: params.projectId,
                tags: params.tags,
              });
            }
          : undefined,
        listRequirements: this.requirementService
          ? async filter => {
              return this.requirementService!.listRequirements({
                orgId,
                status: filter?.status,
                projectId: filter?.projectId,
                createdBy: filter?.createdBy,
              });
            }
          : undefined,
        getRequirement: this.requirementService
          ? async (reqId) => {
              const req = this.requirementService!.getRequirement(reqId);
              if (!req) return null;
              const comments = ts.getRequirementComments?.(reqId) ?? [];
              return { ...req, comments };
            }
          : undefined,
        updateRequirementStatus: this.requirementService
          ? async (reqId, status, reason) => {
              if (status === 'rejected') {
                return this.requirementService!.rejectRequirement(reqId, id, reason ?? '');
              }
              if (status === 'cancelled') {
                return this.requirementService!.cancelRequirement(reqId, id, 'agent');
              }
              return this.requirementService!.updateRequirementStatus(reqId, status, id, 'agent');
            }
          : undefined,
        updateRequirement: this.requirementService
          ? async (reqId, data) => {
              return this.requirementService!.updateRequirement(reqId, data);
            }
          : undefined,
        resubmitRequirement: this.requirementService
          ? async (reqId, updates) => {
              return this.requirementService!.resubmitRequirement(reqId, updates);
            }
          : undefined,
        getTaskComments: ts.getTaskComments
          ? async (taskId) => ts.getTaskComments!(taskId)
          : undefined,
        postTaskComment: ts.postTaskComment
          ? async (taskId, content, mentions, activityId, replyToId?) => ts.postTaskComment!(taskId, id, config.name, content, mentions, activityId, { replyToId })
          : undefined,
        postRequirementComment: ts.postRequirementComment
          ? async (reqId, content, mentions, activityId, replyToId?) => ts.postRequirementComment!(reqId, id, config.name, content, mentions, activityId, { replyToId })
          : undefined,
        getCurrentActivityId: () => agent.getCurrentActivityId(),
        getStatusHistory: ts.getTaskStatusHistory
          ? async (entityType, entityId) => {
              if (entityType === 'task') return ts.getTaskStatusHistory!(entityId) as any[];
              if (entityType === 'requirement' && this.requirementService?.getRequirementStatusHistory) return this.requirementService.getRequirementStatusHistory(entityId) as any[];
              return [];
            }
          : undefined,
        requirementService: this.requirementService?.getActiveGoals ? {
          create: async (data: any) => this.requirementService!.proposeRequirement(data),
          enableGoalLoop: (rid: string, cfg: any) => this.requirementService!.enableGoalLoop!(rid, cfg),
          disableGoalLoop: (rid: string) => this.requirementService!.disableGoalLoop!(rid),
          getById: (gid: string) => this.requirementService!.getById?.(gid) ?? this.requirementService!.getRequirement(gid) as any,
          getActiveGoals: (oid: string) => this.requirementService!.getActiveGoals!(oid),
        } : undefined,
        orgId,
      };
      for (const tool of createAgentTaskTools(taskCtx)) {
        agent.registerTool(tool);
      }

      // Live colleague liveness for Team Status (prompt tail) — read at
      // prompt-build time; the identity snapshot only refreshes on org changes.
      agent.setColleagueStatusProvider(() => {
        const statuses: Record<string, string> = {};
        for (const a of this.agents.values()) {
          try { statuses[a.id] = a.getState().status; } catch { /* skip */ }
        }
        return statuses;
      });

      // Wire tasks fetcher — show all org tasks so agents have full board visibility
      agent.setTasksFetcher(() => {
        try {
          return ts.listTasks({ orgId }).map(t => ({
            id: t.id,
            title: t.title,
            description: t.description,
            status: t.status,
            priority: t.priority,
            assignedAgentId: t.assignedAgentId,
            assignedAgentName: t.assignedAgentId
              ? this.agents.get(t.assignedAgentId)?.config.name
              : undefined,
          }));
        } catch (err) {
          log.debug('Failed to list tasks for agent context', { error: String(err) });
          return [];
        }
      });

      if (this.requirementService?.getActiveGoals) {
        const rs = this.requirementService;
        agent.setGoalFetcher(() => {
          try { return rs.getActiveGoals!(orgId); } catch { return []; }
        });
      }

      log.info(`Task tools injected for agent ${id}`);
    }

    // Workflow context + tools for manager agents
    if (request.agentRole === 'manager' && request.teamId) {
      this.wireWorkflowForManager(agent, request.teamId);
    }

    // Project tools — every agent can list/view/create/update/delete projects + knowledge + stats
    if (this.projectService) {
      const ah = this.approvalHandler;
      const ps = this.projectService;
      const ts = this.taskService;
      const rs = this.requirementService;
      const dvCallbacks = this.buildDeliverableCallbacks(id);
      for (const tool of createProjectTools({
        agentId: id,
        orgId: config.orgId,
        webUiBaseUrl: this.webUiBaseUrl,
        projectService: ps,
        requestApproval: ah ? (req) => ah(id, req) : undefined,
        getProjectInfo: async (projectId?: string) => {
          let resolvedId = projectId;
          if (!resolvedId && config.teamId) {
            resolvedId = ps.listProjects(config.orgId).find(p =>
              p.teamIds.includes(config.teamId!)
            )?.id;
          }
          if (!resolvedId) return null;
          const project = ps.getProject(resolvedId);
          return project ?? null;
        },
        getProjectStats: ts ? async (projectId: string) => {
          const tasks = ts.listTasks({ orgId: config.orgId, projectId });
          const reqs = rs?.listRequirements?.({ projectId }) ?? [];
          const stats = {
            totalTasks: tasks.length,
            completed: 0, inProgress: 0, inReview: 0, blocked: 0, pending: 0, failed: 0,
            totalRequirements: reqs.length,
            completedRequirements: 0,
          };
          for (const t of tasks) {
            if (t.status === 'completed' || t.status === 'accepted') stats.completed++;
            else if (t.status === 'in_progress') stats.inProgress++;
            else if (t.status === 'review' || t.status === 'revision') stats.inReview++;
            else if (t.status === 'blocked') stats.blocked++;
            else if (t.status === 'failed') stats.failed++;
            else stats.pending++;
          }
          for (const r of reqs) {
            if ((r as any).status === 'completed') stats.completedRequirements++;
          }
          return stats;
        } : undefined,
        ...dvCallbacks,
      })) {
        agent.registerTool(tool);
      }

      // Office 产出物生成工具（T6）：docx/xlsx/pptx/pdf，
      // 成功后自动经既有 deliverableCreate 桥接登记为 source='agent' 交付物。
      if (dvCallbacks.deliverableCreate) {
        agent.registerTool(
          createOfficeGenerateTool({
            agentId: id,
            webUiBaseUrl: this.webUiBaseUrl,
            deliverableCreate: dvCallbacks.deliverableCreate,
          }),
        );
      }
    }

    // If this is a manager agent, inject manager-specific tools (scoped to own team)
    if (request.agentRole === 'manager') {
      const myTeamId = config.teamId;
      const filterByTeam = <T extends { teamId?: string }>(list: T[]): T[] =>
        myTeamId ? list.filter(a => a.teamId === myTeamId) : list;
      const managerTools = createManagerTools({
        listAgents: () =>
          filterByTeam(this.listAgents()).map(a => {
            try {
              const ag = this.getAgent(a.id);
              return { ...a, skills: ag.config.skills };
            } catch (err) {
              log.debug('Agent not found while listing for manager tools', { agentId: a.id, error: String(err) });
              return { ...a, skills: [] };
            }
          }),
        delegateMessage: async (targetId, message, _from) => {
          const target = this.getAgent(targetId);
          const reply = await target.sendMessage(message, id, { name: config.name, role: 'manager' }, { sourceType: 'a2a_message', waitForReply: false });
          return stripInternalBlocks(reply);
        },
        getTeamStatus: () =>
          filterByTeam(this.listAgents()).map(a => {
            try {
              const ag = this.getAgent(a.id);
              const state = ag.getState();
              return {
                ...a,
                currentTask: state.currentTaskId,
                tokensUsedToday: state.tokensUsedToday,
              };
            } catch (err) {
              log.debug('Agent not found while getting team status', { agentId: a.id, error: String(err) });
              return { ...a, tokensUsedToday: 0 };
            }
          }),
        findDuplicateTasks: this.taskService?.findDuplicateTasks
          ? (orgId: string) => this.taskService!.findDuplicateTasks!(orgId)
          : undefined,
        cleanupDuplicateTasks: this.taskService?.cleanupDuplicateTasks
          ? (orgId: string) => this.taskService!.cleanupDuplicateTasks!(orgId)
          : undefined,
        getTaskBoardHealth: this.taskService?.getTaskBoardHealth
          ? (orgId: string) => this.taskService!.getTaskBoardHealth!(orgId)
          : undefined,
        updateTeam: this.teamUpdater
          ? async (teamId: string, data: { name?: string; description?: string }) => this.teamUpdater!(teamId, data)
          : undefined,
        updateAgentConfig: async (agentId: string, data: { name?: string }) => {
          const targetAgent = this.getAgent(agentId);
          if (data.name !== undefined) {
            (targetAgent.config as unknown as Record<string, unknown>).name = data.name;
          }
          if (this.agentConfigPersister) {
            await this.agentConfigPersister(agentId, data);
          }
          return { id: agentId, name: targetAgent.config.name };
        },
        stopAgent: async (agentId: string) => { await this.stopAgent(agentId); },
        startAgent: async (agentId: string) => { await this.startAgent(agentId); },
      });
      for (const tool of managerTools) {
        agent.registerTool(tool);
      }
    }

    // Secretary tools (list_teams, team_stop, team_start)
    if (request.roleName?.toLowerCase() === 'secretary') {
      const secretaryTools = createSecretaryTools(this.buildSecretaryToolsContext(config.orgId ?? 'default'));
      for (const tool of secretaryTools) agent.registerTool(tool);
    }

    // Package tools (package_install, package_list, hub_search, hub_install) — available to all agents
    // Uses getter functions (late-binding) so services set after agent creation are still accessible
    {
      const pkgAh = this.approvalHandler;
      const packageTools = createPackageTools({
        installArtifact: () => this.builderService
          ? (type: 'agent' | 'team' | 'skill', name: string, teamId?: string) => this.builderService!.installArtifact(type, name, teamId)
          : undefined,
        listArtifacts: () => this.builderService
          ? (type?: 'agent' | 'team' | 'skill') => this.builderService!.listArtifacts(type)
          : undefined,
        hireFromTemplate: () => this.templateRegistry
          ? async (templateId: string, name: string, skills?: string[], teamId?: string) => {
              const newAgent = await this.createAgentFromTemplate({
                templateId,
                name,
                orgId: config.orgId ?? 'default',
                teamId: teamId || config.teamId,
                overrides: skills ? { skills } : undefined,
              });
              await this.startAgent(newAgent.id);
              return { id: newAgent.id, name: newAgent.config.name, role: newAgent.role.name };
            }
          : undefined,
        listTemplates: () => this.templateRegistry
          ? () => this.templateRegistry!.list().map(t => ({
              id: t.id, name: t.name, description: t.description, roleId: t.roleId, category: t.category ?? 'general',
            }))
          : undefined,
        searchHub: () => this.hubClient
          ? (opts) => this.hubClient!.search(opts)
          : undefined,
        downloadAndInstall: () => this.hubClient
          ? (itemId) => this.hubClient!.downloadAndInstall(itemId)
          : undefined,
        ensureTeam: async (name: string, description?: string) =>
          this.ensureTeam(config.orgId ?? 'default', name, description),
        requestApproval: pkgAh ? (req) => pkgAh(id, req) : undefined,
      });
      for (const tool of packageTools) agent.registerTool(tool);
    }

    // Connect MCP servers and register their tools
    const mcpConfigs = request.mcpServers ?? this.globalMcpServers;
    if (mcpConfigs) {
      for (const [serverName, rawServerConfig] of Object.entries(mcpConfigs)) {
        try {
          const serverConfig = this.enrichChromeDevtoolsConfig(serverName, rawServerConfig);
          let mcpTools: AgentToolHandler[];
          if (serverName === 'chrome-devtools') {
            mcpTools = await this.registerChromeDevtoolsLazy(id, serverName, serverConfig);
          } else {
            await this.mcpManager.connectServer(serverName, serverConfig);
            mcpTools = this.mcpManager.getToolHandlers(serverName);
          }
          for (const tool of mcpTools) {
            agent.registerTool(tool);
          }
          log.info(`MCP server ${serverName} tools registered for agent ${id}`, {
            toolCount: mcpTools.length,
          });
        } catch (error) {
          log.warn(`Failed to connect MCP server ${serverName} for agent ${id}`, {
            error: String(error),
          });
        }
      }
    }

    this.registerFeishuTools(agent);

    if (this.agentAuditCallback) {
      const cb = this.agentAuditCallback;
      agent.setAuditCallback(event => cb(id, event));
    }
    if (this.escalationHandler) {
      agent.setEscalationCallback(this.escalationHandler);
    }
    if (this.approvalHandler) {
      const ah = this.approvalHandler;
      agent.setApprovalCallback(
        async (req: { toolName: string; toolArgs: Record<string, unknown>; reason: string; taskId?: string; sessionId?: string; chatSessionId?: string }) =>
          ah(id, req)
      );
    }

    agent.setStateChangeCallback(this.buildStateChangeCallback());
    if (this.activityCallbacks) {
      agent.setActivityCallbacks(this.activityCallbacks);
    }
    agent.setBrowserCloseTabsHelper((sessionId) =>
      this.browserSessionManager.consumeCloseTabsReminder(id, sessionId),
    );
    // Callback, not a snapshot: the setting can change while agents are already running.
    agent.setBrowserElementSelectionProvider(() => this.browserElementSelection);
    this.forwardAgentEvents(agent);

    if (config.teamId) {
      agent.setTeamDataDir(markusPath('teams', config.teamId));
    }

    this.attachTerminalTools(agent, id);
    this.agents.set(id, agent);
    this.delegationManager.registerAgentCard({
      agentId: id,
      name: config.name,
      role: role.name,
      capabilities: config.skills,
      skills: config.skills,
      status: 'idle',
    });
    this.eventBus.emit('agent:created', { agentId: id, name: request.name });
    log.info(`Agent created: ${request.name} (${id})`);

    return agent;
  }

  /**
   * Restore an agent from a persisted DB row, reusing its original ID.
   * Used on server startup to rebuild in-memory state from the database.
   */
  async restoreAgent(row: {
    id: string;
    name: string;
    orgId: string;
    teamId: string | null;
    roleId: string;
    roleName: string;
    agentRole: string;
    skills: unknown;
    llmConfig: unknown;
    heartbeatIntervalMs: number;
    profile?: unknown;
    tokensUsedToday?: number;
    activeTaskIds?: unknown;
  }): Promise<Agent> {
    const id = row.id;
    const agentDataDir = join(this.dataDir, id);
    mkdirSync(agentDataDir, { recursive: true });

    // Prefer agent's own role dir; fall back to template and migrate
    const agentRoleDir = join(agentDataDir, 'role');
    let role: RoleTemplate;
    if (existsSync(join(agentRoleDir, 'ROLE.md'))) {
      role = this.roleLoader.loadRole(agentRoleDir);
    } else if (row.roleId === 'custom') {
      role = {
        id: generateId('role'),
        name: row.name,
        description: '',
        category: 'custom' as RoleCategory,
        systemPrompt: `# ${row.name}\n\nYou are ${row.name}.`,
        defaultSkills: [],
        heartbeatChecklist: '',
        defaultPolicies: [],
        builtIn: false,
      };
    } else {
      role = (() => {
        try { return this.roleLoader.loadRole(row.roleId); } catch (err) { log.debug('Role not found by roleId, trying roleName', { roleId: row.roleId, error: String(err) }); }
        try { return this.roleLoader.loadRole(row.roleName); } catch (err) { log.debug('Role not found by roleName, scanning templates', { roleName: row.roleName, error: String(err) }); }
        const available = this.roleLoader.listAvailableRoles();
        for (const templateName of available) {
          try {
            const candidate = this.roleLoader.loadRole(templateName);
            if (candidate.name.toLowerCase() === row.roleName.toLowerCase()) return candidate;
          } catch (err) { log.debug('Failed to load role template', { templateName, error: String(err) }); }
        }
        throw new Error(`Role not found: ${row.roleName} (roleId: ${row.roleId})`);
      })();
      const templateDir = this.roleLoader.resolveTemplateDir(row.roleId)
        ?? this.roleLoader.resolveTemplateDir(row.roleName);
      if (templateDir) {
        mkdirSync(agentRoleDir, { recursive: true });
        for (const file of ['ROLE.md', 'HEARTBEAT.md', 'POLICIES.md', 'CONTEXT.md']) {
          const src = join(templateDir, file);
          if (existsSync(src)) copyFileSync(src, join(agentRoleDir, file));
        }
      }
    }

    const agentRole = (row.agentRole as 'manager' | 'worker') ?? 'worker';

    const config: AgentConfig = {
      id,
      name: row.name,
      roleId: row.roleId,
      orgId: row.orgId,
      teamId: row.teamId ?? undefined,
      agentRole,
      skills: Array.isArray(row.skills) ? (row.skills as string[]) : role.defaultSkills,
      profile: row.profile as AgentProfile | undefined,
      llmConfig: (() => {
        const raw = (row.llmConfig ?? {}) as Record<string, unknown>;
        return {
          modelMode: (raw.modelMode as 'default' | 'custom') ?? 'default',
          primary: (raw.primary as string) ?? 'anthropic',
          defaultModel: raw.defaultModel as string | undefined,
          fallback: raw.fallback as string | undefined,
          maxTokensPerRequest: raw.maxTokensPerRequest as number | undefined,
          maxTokensPerDay: raw.maxTokensPerDay as number | undefined,
        };
      })(),
      channels: [],
      heartbeatIntervalMs: row.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS,
      concurrent: (row as Record<string, unknown>)['concurrent'] as AgentConfig['concurrent'] ?? this._concurrentConfig,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    const workspacePath = config.profile?.workspacePath ?? join(this.dataDir, id, 'workspace');
    mkdirSync(workspacePath, { recursive: true });

    const teamDataDir = (config.teamId && config.agentRole === 'manager')
      ? markusPath('teams', config.teamId)
      : undefined;

    const builderArtifactsDir = markusPath('builder-artifacts');

    const pathPolicy = this.buildPathPolicy(id, workspacePath, agentRoleDir, teamDataDir, builderArtifactsDir);

    const basePolicy = this.globalSecurityPolicy;
    const security = new SecurityGuard({
      ...basePolicy,
    });
    const agentMeta = {
      agentId: id,
      agentName: row.name ?? 'unknown',
      teamName: row.teamId as string | undefined,
      orgId: (row.orgId as string) ?? 'default',
    };
    const tools = createBuiltinTools({ agentId: id, agentMeta, security, workspacePath, pathPolicy });

    const agent = new Agent({
      config,
      role,
      llmRouter: this.llmRouter,
      dataDir: agentDataDir,
      tools,
      pathPolicy,
      restoredState: { tokensUsedToday: row.tokensUsedToday ?? 0 },
      skillRegistry: this.skillRegistry,
      maxToolIterations: this._maxToolIterations,
      handbookPath: this.resolveHandbookPath(row.roleId ?? row.roleName),
    });

    // Progressive disclosure: catalog metadata only; full bodies via discover_tools
    if (this.skillRegistry) {
      agent.setAvailableSkillCatalog(this.skillRegistry.getSkillCatalog());
    }

    // H9: same structured-missing-skill computation as the create path. A single
    // aggregate warn is emitted by OrgService after the whole restore loop, so no
    // per-agent warn here (dozens of restored agents must not flood the log).
    const skillWarnings = buildSkillWarnings(config, this.skillRegistry);
    setAgentSkillWarnings(agent, skillWarnings);
    if (skillWarnings.missing.length > 0) {
      log.debug(`Restored agent ${config.name} (${id}) references skills not found in registry`, {
        missing: skillWarnings.missing,
        available: skillWarnings.available,
      });
    }

    // Connect MCP servers for assigned skills (tools only — instructions on demand)
    if (this.skillRegistry && config.skills.length > 0) {
      // Connect MCP servers declared by explicitly assigned skills (background, non-blocking).
      // Skip chrome-devtools during restore — it connects lazily when the agent actually
      // needs browser tools. This prevents flooding Chrome with 20+ concurrent CDP connections
      // on startup which causes Chrome to crash.
      const mcpConnections: Array<Promise<void>> = [];
      for (const skillName of config.skills) {
        const skill = this.skillRegistry.get(skillName);
        if (skill?.manifest.mcpServers) {
          const isolation = skill.manifest.isolation ?? 'shared';
          for (const [serverName, rawServerConfig] of Object.entries(skill.manifest.mcpServers)) {
            if (serverName === 'chrome-devtools') {
              mcpConnections.push((async () => {
                try {
                  const serverConfig = this.enrichChromeDevtoolsConfig(serverName, rawServerConfig);
                  const mcpTools = await this.registerChromeDevtoolsLazy(id, serverName, serverConfig);
                  for (const tool of mcpTools) {
                    agent.registerTool(tool);
                  }
                  log.info(`Skill ${skillName} chrome-devtools registered lazily for agent ${id} (inactive until discover_tools)`);
                } catch (error) {
                  log.warn(`Failed to register chrome-devtools lazily for agent ${id}`, { error: String(error) });
                }
              })());
              continue;
            }
            mcpConnections.push((async () => {
              try {
                let mcpTools: AgentToolHandler[];
                if (isolation === 'per-agent' || isolation === 'pooled') {
                  const serverConfig = this.enrichChromeDevtoolsConfig(serverName, rawServerConfig);
                  await this.mcpManager.connectServerScoped(serverName, serverConfig, id);
                  mcpTools = this.mcpManager.getToolHandlersScoped(serverName, id);
                  mcpTools = this.browserSessionManager.wrapToolHandlers(mcpTools, id);
                  this.browserSessionManager.setReconnector(id, serverName, async () => {
                    await this.mcpManager.disconnectServerScoped(serverName, id);
                    await this.mcpManager.connectServerScoped(serverName, serverConfig, id);
                  });
                } else {
                  const serverConfig = this.enrichChromeDevtoolsConfig(serverName, rawServerConfig);
                  await this.mcpManager.connectServer(serverName, serverConfig);
                  mcpTools = this.mcpManager.getToolHandlers(serverName);
                }
                for (const tool of mcpTools) {
                  agent.registerTool(tool);
                }
                log.info(`Skill ${skillName} MCP server ${serverName} restored for agent ${id}`, {
                  toolCount: mcpTools.length, isolation, live: false,
                });
              } catch (error) {
                log.warn(`Failed to restore skill ${skillName} MCP server ${serverName} for agent ${id}`, {
                  error: String(error),
                });
              }
            })());
          }
        }
      }
      void Promise.all(mcpConnections);
    }

    // Connect global MCP servers (e.g. feishu-lark) for booted agents — same as createAgentFromRequest
    if (this.globalMcpServers) {
      for (const [serverName, rawServerConfig] of Object.entries(this.globalMcpServers)) {
        if (agent.hasToolPrefix(serverName)) continue; // already connected via skill
        void (async () => {
          try {
            const serverConfig = this.enrichChromeDevtoolsConfig(serverName, rawServerConfig);
            await this.mcpManager.connectServer(serverName, serverConfig);
            const mcpTools = this.mcpManager.getToolHandlers(serverName);
            for (const tool of mcpTools) {
              agent.registerTool(tool);
            }
            log.info(`Global MCP server ${serverName} registered for booted agent ${id}`, {
              toolCount: mcpTools.length, live: false,
            });
          } catch (error) {
            log.warn(`Failed to connect global MCP server ${serverName} for booted agent ${id}`, {
              error: String(error),
            });
          }
        })();
      }
    }

    this.registerFeishuTools(agent);

    // Set skill MCP activator callback for runtime activation via discover_tools
    agent.setSkillMcpActivator(async (skillName, mcpServers) => {
      let tools: AgentToolHandler[] = [];
      const skill = this.skillRegistry?.get(skillName);
      const isolation = skill?.manifest.isolation ?? 'shared';
      for (const [serverName, rawSrvConfig] of Object.entries(mcpServers)) {
        // reuseGlobal: reuse the globally-configured MCP server (with real credentials from config)
        if ((rawSrvConfig as Record<string, unknown>).reuseGlobal === true) {
          const globalConfig = this.globalMcpServers?.[serverName];
          if (globalConfig) {
            const gc = this.enrichChromeDevtoolsConfig(serverName, globalConfig);
            await this.mcpManager.connectServer(serverName, gc);
          }
          const handlers = this.mcpManager.getToolHandlers(serverName);
          if (handlers.length > 0) tools.push(...handlers);
          continue;
        }
        const srvConfig = this.enrichChromeDevtoolsConfig(serverName, rawSrvConfig);
        if (serverName === 'chrome-devtools') {
          const chromeTools = await this.registerChromeDevtoolsLazy(id, serverName, srvConfig);
          tools.push(...chromeTools);
        } else if (isolation === 'per-agent' || isolation === 'pooled') {
          await this.mcpManager.connectServerScoped(serverName, srvConfig, id);
          tools.push(...this.mcpManager.getToolHandlersScoped(serverName, id));
        } else {
          await this.mcpManager.connectServer(serverName, srvConfig);
          tools.push(...this.mcpManager.getToolHandlers(serverName));
        }
      }
      const hasChromeDevtools = Object.keys(mcpServers).includes('chrome-devtools');
      if (!hasChromeDevtools && (isolation === 'per-agent' || isolation === 'pooled')) {
        tools = this.browserSessionManager.wrapToolHandlers(tools, id);
        for (const [serverName, rawSrvConfig] of Object.entries(mcpServers)) {
          const srvConfig = this.enrichChromeDevtoolsConfig(serverName, rawSrvConfig);
          this.browserSessionManager.setReconnector(id, serverName, async () => {
            await this.mcpManager.disconnectServerScoped(serverName, id);
            await this.mcpManager.connectServerScoped(serverName, srvConfig, id);
          });
        }
      }
      return tools;
    });

    // Set skill search/install callbacks (injected by org-manager layer)
    if (this.skillSearcher) agent.setSkillSearcher(this.skillSearcher);
    if (this.skillInstaller) agent.setSkillInstaller(this.skillInstaller);
    if (this.skillImporter) agent.setSkillImporter(this.skillImporter);
    if (this.userApprovalRequester) agent.setUserApprovalRequester(this.userApprovalRequester);
    if (this.userNotifier) agent.setUserNotifier(this.userNotifier);
    if (this.runtimeViewerContext) agent.setRuntimeViewerContext(this.runtimeViewerContext);

    const a2aCtx = {
      selfId: id,
      selfName: config.name,
      wakeAgent: async (agentId: string) => { await this.startAgent(agentId); },
      isRunning: (agentId: string) => {
        const a = this.agents.get(agentId);
        return !!a && a.getState().status !== 'offline';
      },
      listColleagues: () =>
        this.listAgents().map(a => {
          try {
            const ag = this.getAgent(a.id);
            return { ...a, skills: ag.config.skills };
          } catch (err) {
            log.debug('Agent not found while listing colleagues', { agentId: a.id, error: String(err) });
            return { ...a, skills: [] };
          }
        }),
      sendMessage: async (targetId: string, message: string, fromId: string, fromName: string, priority?: number, waitForReply?: boolean) =>
        this.deliverA2AMessage({ targetId, message, fromId, fromName, priority, waitForReply }),
      delegateTask: async (targetId: string, delegation: TaskDelegation) =>
        this.delegationManager.delegateTask(id, delegation, targetId),
      ...(this.groupChatHandlers
        ? {
            sendGroupMessage: this.groupChatHandlers.sendGroupMessage,
            createGroupChat: (name: string, memberIds: string[]) =>
              this.groupChatHandlers!.createGroupChat(name, id, config.name, memberIds),
            listGroupChats: this.groupChatHandlers.listGroupChats,
            getChannelMessages: this.groupChatHandlers.getChannelMessages,
            getChatSessionMessages: this.groupChatHandlers.getChatSessionMessages
              ? (sessionId: string, limit: number, before?: string) =>
                  this.groupChatHandlers!.getChatSessionMessages!(sessionId, limit, before, id)
              : undefined,
          }
        : {}),
    };
    for (const tool of createA2ATools(a2aCtx)) agent.registerTool(tool);
    for (const tool of createStructuredA2ATools(a2aCtx)) agent.registerTool(tool);

    for (const tool of createMemoryTools({
      agentId: id,
      agentName: config.name,
      memory: agent.getMemory(),
      semanticSearch: this.semanticSearch,
    })) {
      agent.registerTool(tool);
    }

    // Mailbox + working memory tools
    const attnCtrl2 = agent.getAttentionController();
    const mailboxToolCtx2: MailboxToolContext = {
      agentId: id,
      getMindState: () => attnCtrl2.getMindState(),
      deferItem: (itemId, reason, ms) => attnCtrl2.deferItem(itemId, reason, ms),
      dropItem: (itemId, reason) => attnCtrl2.dropItem(itemId, reason),
      prioritizeItem: (itemId, p) => attnCtrl2.prioritizeItem(itemId, p),
      updateWorkingMemory: (key, content) => agent.updateWorkingMemory(key, content),
      clearWorkingMemory: (key) => agent.clearWorkingMemory(key),
      getWorkingMemorySnapshot: () => agent.getWorkingMemorySnapshot(),
    };
    for (const tool of createMailboxTools(mailboxToolCtx2)) agent.registerTool(tool);

    if (this.recallCallbacks) {
      agent.registerTool(createRecallTool({ agentId: id, ...this.recallCallbacks }));
    }
    // Memory-backed session repo — see createMemorySessionRepo doc for the
    // split-brain fix (was: this.sessionRepo Sqlite cs_*).
    {
      const mem2 = agent.getMemory();
      agent.registerTool(createSessionTool({
        agentId: id,
        chatSessionRepo: createMemorySessionRepo(mem2),
        compactor: {
          compactOnDemand: (sid, keep) => mem2.compactSession(sid, keep),
          compactWithAnchor: (sid, keep, anchor) => {
            if (mem2.setSlot && anchor.goal) mem2.setSlot(sid, 'goal', anchor.goal);
            if (mem2.setSlot && anchor.done) mem2.setSlot(sid, 'done', anchor.done);
            if (mem2.setSlot && anchor.next) mem2.setSlot(sid, 'next', anchor.next);
            const res = mem2.compactSession(sid, keep);
            return { ...res, anchorKey: ['goal', 'done', 'next'].filter((k) => anchor[k as keyof typeof anchor]).join(',') || 'anchor' };
          },
        },
        slotStore: {
          getSlots: (sid) => mem2.getSlots ? mem2.getSlots(sid) : [],
          setSlot: (sid, k, v) => { if (mem2.setSlot) mem2.setSlot(sid, k, v); },
          removeSlot: (sid, k) => { if (mem2.removeSlot) mem2.removeSlot(sid, k); },
          serialize: (sid) => mem2.serializeSlots ? mem2.serializeSlots(sid) : '',
        },
        fragmentStore: {
          retrieveFragments: (q, mx) => mem2.retrieveFragments ? mem2.retrieveFragments(q, mx) : [],
          includeFragment: (sid, fid) => mem2.includeFragment ? mem2.includeFragment(sid, fid) : { ok: false, message: 'unavailable' },
          purgeSessionFragments: (sid) => mem2.purgeSessionFragments ? mem2.purgeSessionFragments(sid) : 0,
          sessionStats: (sid) => mem2.sessionStats ? mem2.sessionStats(sid) : { messageCount: 0, slotKeys: [], fragmentCount: 0 },
        },
        titleUpdater: this.sessionTitleUpdater,
        currentDbSessionId: () => agent.getDbSessionId(),
        currentMemorySessionId: () => agent.getCurrentSessionId(),
        resolveMemorySessionByDbSessionId: (dbId) => agent.getMemorySessionIdForDbSession(dbId),
        diskSessionCount: () => mem2.countSessionsOnDisk?.() ?? 0,
      }));
    }

    for (const tool of createSettingsTools({
      llmRouter: this.llmRouter,
      persistConfig: (updates) => { try { saveConfig(updates); } catch (err) { log.debug('Failed to persist config', { error: String(err) }); } },
      agent: this.buildSettingsAgentContext(id),
    })) {
      agent.registerTool(tool);
    }

    for (const tool of createMultiModalTools({
      resolveCandidates: (capabilityType) => this.llmRouter.resolveModalityCandidates(capabilityType),
      resolveProvider: (name) => {
        const hit = this.llmRouter.resolveProviderByName(name);
        return hit ? { provider: hit.provider, name: hit.name } : undefined;
      },
      listProviderNames: () => this.llmRouter.listRegisteredProviderNames(),
      isProviderDisabled: (name) => this.llmRouter.isProviderDisabled(name),
      resolveHubCredentials: () => this.llmRouter.getHubCredentials(),
    })) {
      agent.registerTool(tool);
    }

    await this.registerCodingTools(agent);

    if (this.semanticSearch) {
      agent.getContextEngine().setSemanticSearch(this.semanticSearch);
      agent.setSemanticSearch(this.semanticSearch);
    }

    if (this.taskService) {
      const ts = this.taskService;
      const orgId = config.orgId;
      const taskCtx: AgentTaskContext = {
        agentId: id,
        agentName: config.name,
        createTask: async params => ts.createTask({ orgId, ...params, createdBy: id, creatorRole: 'worker' }),
        listTasks: async filter =>
          ts.queryTasks({
            orgId,
            status: filter?.status as any,
            assignedAgentId: filter?.assignedToMe ? id : undefined,
            priority: filter?.priority as any,
            requirementId: filter?.requirementId,
            projectId: filter?.projectId,
            search: filter?.search,
            sortBy: filter?.sortBy as any,
            sortOrder: filter?.sortOrder as any,
            page: filter?.page,
            pageSize: filter?.pageSize,
          }),
        updateTaskStatus: async (taskId, status) =>
          ts.updateTaskStatus(taskId, status, id, false, false, 'agent'),
        askAgent: async (toAgentId, question, contextText, timeoutSeconds) => {
          if (!toAgentId) throw new Error('agent_id is required');
          if (toAgentId === id) throw new Error('You cannot ask yourself — settle it from evidence, or ask the human with request_user_input.');
          let target: PeerAgent | undefined;
          try { target = this.getAgent(toAgentId) as unknown as PeerAgent; } catch { target = undefined; }
          if (!target) throw new Error(`Unknown agent "${toAgentId}" — use agent_list_colleagues to find a real one.`);
          const timeoutMs = Math.min(Math.max((timeoutSeconds ?? 300) * 1000, 5_000), 900_000);
          let fromName = 'another agent';
          try { fromName = (this.getAgent(id) as unknown as { name?: string }).name ?? fromName; } catch { /* keep fallback */ }
          const message = [
            question.trim(),
            contextText?.trim() ? `\n\nContext provided by ${fromName}:\n${contextText.trim()}` : '',
            '\n\n(You are being asked directly by another agent. Your reply is returned to them verbatim — answer in one paragraph. You may ask the human if you truly cannot answer; you cannot ask another agent.)',
          ].join('');
          const started = Date.now();
          try {
            const reply = await Promise.race([
              target.sendMessage(
                message, id, { name: fromName, role: 'agent' },
                { scenario: 'peer_query', sourceType: 'a2a_message', peerQueryDepth: 1, priority: MailboxPriorityLevel.high },
              ),
              new Promise<never>((_, reject) => {
                const t = setTimeout(() => reject(new Error('PEER_QUERY_TIMEOUT')), timeoutMs);
                (t as unknown as { unref?: () => void }).unref?.();
              }),
            ]);
            return { status: 'answered' as const, agent: { id: toAgentId }, answer: String(reply ?? '').trim(), elapsed_ms: Date.now() - started };
          } catch (err) {
            if (String(err).includes('PEER_QUERY_TIMEOUT')) {
              return {
                status: 'timeout' as const,
                agent: { id: toAgentId },
                elapsed_ms: Date.now() - started,
                message: `No answer within ${Math.round(timeoutMs / 1000)}s. Proceed on your best judgement and state the assumption you used, or ask the human with request_user_input.`,
              };
            }
            return { status: 'unavailable' as const, agent: { id: toAgentId }, elapsed_ms: Date.now() - started, message: String(err) };
          }
        },
        requestRevision: async (taskId, reason) => ts.requestRevision(taskId, reason, id),
        requestTaskApproval: ts.requestTaskApproval
          ? async (taskId: string) => ts.requestTaskApproval!(taskId, id)
          : undefined,
        getTask: async taskId => ts.getTask(taskId) ?? null,
        assignTask: async (taskId, agentId) => ts.assignTask(taskId, agentId, id),
        addTaskNote: async (taskId, note, author) => {
          ts.addTaskNote(taskId, note, author);
        },
        updateTaskFields: async (taskId, fields) => {
          const task = ts.updateTask(taskId, fields, id);
          return { id: task.id, title: task.title, status: task.status };
        },
        updateScheduleConfig: async (taskId, config) => {
          if (!ts.updateScheduleFields) throw new Error('Schedule editing not supported');
          const task = await ts.updateScheduleFields(taskId, config);
          return { id: task.id, title: task.title, status: task.status };
        },
        cancelPendingTask: async (taskId) => {
          const task = ts.rejectTask(taskId, id);
          return { id: task.id, title: task.title, status: task.status };
        },
        addSubtask: async (taskId, title) => {
          return ts.addSubtask(taskId, title);
        },
        completeSubtask: async (taskId, subtaskId) => {
          return ts.completeSubtask(taskId, subtaskId);
        },
        cancelSubtask: async (taskId, subtaskId) => {
          return ts.cancelSubtask(taskId, subtaskId);
        },
        getSubtasks: async (taskId) => {
          const task = ts.getTask(taskId);
          return task?.subtasks ?? [];
        },
        submitForReview: async (summary, inputDeliverables, knownIssues, explicitTaskId) => {
          const taskId = explicitTaskId?.trim()
            ? assertSubmittableTask(ts, explicitTaskId.trim(), id).id
            : resolveCurrentTaskId(this.agents.get(id), ts, id);
          const task = assertSubmittableTask(ts, taskId, id);
          const reviewerId = task.reviewerId;
          const _validTypes = new Set(['file', 'directory']);
          const completionSummary = `${summary}${knownIssues ? `\n\nKnown issues: ${knownIssues}` : ''}`;
          const deliverables: Array<{ type: string; reference: string; summary: string }> = [];
          if (Array.isArray(inputDeliverables)) {
            for (const d of inputDeliverables) {
              if (d?.reference) {
                deliverables.push({
                  type: _validTypes.has(d.type ?? '') ? d.type! : 'file',
                  reference: d.reference, summary: d.summary || String(d.reference),
                });
              }
            }
          }
          return ts.submitForReview(taskId, deliverables, reviewerId, completionSummary);
        },
        proposeRequirement: this.requirementService
          ? async params => {
              return this.requirementService!.proposeRequirement({
                orgId,
                title: params.title,
                description: params.description,
                priority: params.priority,
                source: 'agent',
                createdBy: id,
                projectId: params.projectId,
                tags: params.tags,
              });
            }
          : undefined,
        listRequirements: this.requirementService
          ? async filter => {
              return this.requirementService!.listRequirements({
                orgId,
                status: filter?.status,
                projectId: filter?.projectId,
                createdBy: filter?.createdBy,
              });
            }
          : undefined,
        getRequirement: this.requirementService
          ? async (reqId) => {
              const req = this.requirementService!.getRequirement(reqId);
              if (!req) return null;
              const comments = ts.getRequirementComments?.(reqId) ?? [];
              return { ...req, comments };
            }
          : undefined,
        updateRequirementStatus: this.requirementService
          ? async (reqId, status, reason) => {
              if (status === 'rejected') {
                return this.requirementService!.rejectRequirement(reqId, id, reason ?? '');
              }
              if (status === 'cancelled') {
                return this.requirementService!.cancelRequirement(reqId, id, 'agent');
              }
              return this.requirementService!.updateRequirementStatus(reqId, status, id, 'agent');
            }
          : undefined,
        updateRequirement: this.requirementService
          ? async (reqId, data) => {
              return this.requirementService!.updateRequirement(reqId, data);
            }
          : undefined,
        resubmitRequirement: this.requirementService
          ? async (reqId, updates) => {
              return this.requirementService!.resubmitRequirement(reqId, updates);
            }
          : undefined,
        getTaskComments: ts.getTaskComments
          ? async (taskId) => ts.getTaskComments!(taskId)
          : undefined,
        postTaskComment: ts.postTaskComment
          ? async (taskId, content, mentions, _activityId?, replyToId?) => ts.postTaskComment!(taskId, id, config.name, content, mentions, undefined, { replyToId })
          : undefined,
        postRequirementComment: ts.postRequirementComment
          ? async (reqId, content, mentions, _activityId?, replyToId?) => ts.postRequirementComment!(reqId, id, config.name, content, mentions, undefined, { replyToId })
          : undefined,
        getStatusHistory: ts.getTaskStatusHistory
          ? async (entityType, entityId) => {
              if (entityType === 'task') return ts.getTaskStatusHistory!(entityId) as any[];
              if (entityType === 'requirement' && this.requirementService?.getRequirementStatusHistory) return this.requirementService.getRequirementStatusHistory(entityId) as any[];
              return [];
            }
          : undefined,
        requirementService: this.requirementService?.getActiveGoals ? {
          create: async (data: any) => this.requirementService!.proposeRequirement(data),
          enableGoalLoop: (rid: string, cfg: any) => this.requirementService!.enableGoalLoop!(rid, cfg),
          disableGoalLoop: (rid: string) => this.requirementService!.disableGoalLoop!(rid),
          getById: (gid: string) => this.requirementService!.getById?.(gid) ?? this.requirementService!.getRequirement(gid) as any,
          getActiveGoals: (oid: string) => this.requirementService!.getActiveGoals!(oid),
        } : undefined,
        orgId,
      };
      for (const tool of createAgentTaskTools(taskCtx)) agent.registerTool(tool);
      // Live colleague liveness for Team Status (prompt tail) — read at
      // prompt-build time; the identity snapshot only refreshes on org changes.
      agent.setColleagueStatusProvider(() => {
        const statuses: Record<string, string> = {};
        for (const a of this.agents.values()) {
          try { statuses[a.id] = a.getState().status; } catch { /* skip */ }
        }
        return statuses;
      });
      agent.setTasksFetcher(() => {
        try {
          return ts.listTasks({ orgId }).map(t => ({
            id: t.id,
            title: t.title,
            description: t.description,
            status: t.status,
            priority: t.priority,
            assignedAgentId: t.assignedAgentId,
            assignedAgentName: t.assignedAgentId
              ? this.agents.get(t.assignedAgentId)?.config.name
              : undefined,
          }));
        } catch (err) {
          log.debug('Failed to list tasks for restored agent context', { error: String(err) });
          return [];
        }
      });

      if (this.requirementService?.getActiveGoals) {
        const rs = this.requirementService;
        agent.setGoalFetcher(() => {
          try { return rs.getActiveGoals!(orgId); } catch { return []; }
        });
      }
    }

    // Workflow context + tools for restored manager agents
    if (config.agentRole === 'manager' && config.teamId) {
      this.wireWorkflowForManager(agent, config.teamId);
    }

    if (this.projectService) {
      const ah2 = this.approvalHandler;
      const ps2 = this.projectService;
      const ts2 = this.taskService;
      const rs2 = this.requirementService;
      const dvCallbacks2 = this.buildDeliverableCallbacks(id);
      for (const tool of createProjectTools({
        agentId: id,
        orgId: config.orgId,
        webUiBaseUrl: this.webUiBaseUrl,
        projectService: ps2,
        requestApproval: ah2 ? (req) => ah2(id, req) : undefined,
        getProjectInfo: async (projectId?: string) => {
          let resolvedId2 = projectId;
          if (!resolvedId2 && config.teamId) {
            resolvedId2 = ps2.listProjects(config.orgId).find(p =>
              p.teamIds.includes(config.teamId!)
            )?.id;
          }
          if (!resolvedId2) return null;
          const project = ps2.getProject(resolvedId2);
          return project ?? null;
        },
        getProjectStats: ts2 ? async (projectId: string) => {
          const tasks = ts2.listTasks({ orgId: config.orgId, projectId });
          const reqs = rs2?.listRequirements?.({ projectId }) ?? [];
          const stats = {
            totalTasks: tasks.length,
            completed: 0, inProgress: 0, inReview: 0, blocked: 0, pending: 0, failed: 0,
            totalRequirements: reqs.length,
            completedRequirements: 0,
          };
          for (const t of tasks) {
            if (t.status === 'completed' || t.status === 'accepted') stats.completed++;
            else if (t.status === 'in_progress') stats.inProgress++;
            else if (t.status === 'review' || t.status === 'revision') stats.inReview++;
            else if (t.status === 'blocked') stats.blocked++;
            else if (t.status === 'failed') stats.failed++;
            else stats.pending++;
          }
          for (const r of reqs) {
            if ((r as any).status === 'completed') stats.completedRequirements++;
          }
          return stats;
        } : undefined,
        ...dvCallbacks2,
      })) {
        agent.registerTool(tool);
      }

      if (dvCallbacks2.deliverableCreate) {
        agent.registerTool(
          createOfficeGenerateTool({
            agentId: id,
            webUiBaseUrl: this.webUiBaseUrl,
            deliverableCreate: dvCallbacks2.deliverableCreate,
          }),
        );
      }
    }

    if (config.agentRole === 'manager') {
      const restoredTeamId = config.teamId;
      const filterByTeamRestored = <T extends { teamId?: string }>(list: T[]): T[] =>
        restoredTeamId ? list.filter(a => a.teamId === restoredTeamId) : list;
      const managerTools = createManagerTools({
        listAgents: () =>
          filterByTeamRestored(this.listAgents()).map(a => {
            try {
              const ag = this.getAgent(a.id);
              return { ...a, skills: ag.config.skills };
            } catch (err) {
              log.debug('Agent not found while listing for manager tools', { agentId: a.id, error: String(err) });
              return { ...a, skills: [] };
            }
          }),
        delegateMessage: async (targetId, message) => {
          const target = this.getAgent(targetId);
          const reply = await target.sendMessage(message, id, { name: config.name, role: 'manager' }, { sourceType: 'a2a_message', waitForReply: false });
          return stripInternalBlocks(reply);
        },
        getTeamStatus: () =>
          filterByTeamRestored(this.listAgents()).map(a => {
            try {
              const ag = this.getAgent(a.id);
              const state = ag.getState();
              return {
                ...a,
                currentTask: state.currentTaskId,
                tokensUsedToday: state.tokensUsedToday,
              };
            } catch (err) {
              log.debug('Agent not found while getting team status', { agentId: a.id, error: String(err) });
              return { ...a, tokensUsedToday: 0 };
            }
          }),
        findDuplicateTasks: this.taskService?.findDuplicateTasks
          ? (orgId: string) => this.taskService!.findDuplicateTasks!(orgId)
          : undefined,
        cleanupDuplicateTasks: this.taskService?.cleanupDuplicateTasks
          ? (orgId: string) => this.taskService!.cleanupDuplicateTasks!(orgId)
          : undefined,
        getTaskBoardHealth: this.taskService?.getTaskBoardHealth
          ? (orgId: string) => this.taskService!.getTaskBoardHealth!(orgId)
          : undefined,
        updateTeam: this.teamUpdater
          ? async (teamId: string, data: { name?: string; description?: string }) => this.teamUpdater!(teamId, data)
          : undefined,
        updateAgentConfig: async (agentId: string, data: { name?: string }) => {
          const targetAgent = this.getAgent(agentId);
          if (data.name !== undefined) {
            (targetAgent.config as unknown as Record<string, unknown>).name = data.name;
          }
          if (this.agentConfigPersister) {
            await this.agentConfigPersister(agentId, data);
          }
          return { id: agentId, name: targetAgent.config.name };
        },
        stopAgent: async (agentId: string) => { await this.stopAgent(agentId); },
        startAgent: async (agentId: string) => { await this.startAgent(agentId); },
      });
      for (const tool of managerTools) agent.registerTool(tool);
    }

    // Secretary tools (list_teams, team_stop, team_start)
    if (row.roleName?.toLowerCase() === 'secretary') {
      const secretaryTools = createSecretaryTools(this.buildSecretaryToolsContext(config.orgId ?? 'default'));
      for (const tool of secretaryTools) agent.registerTool(tool);
    }

    // Package tools (package_install, package_list, hub_search, hub_install) — available to all agents
    // Uses getter functions (late-binding) so services set after agent creation are still accessible
    {
      const pkgAh2 = this.approvalHandler;
      const packageTools = createPackageTools({
        installArtifact: () => this.builderService
          ? (type: 'agent' | 'team' | 'skill', name: string, teamId?: string) => this.builderService!.installArtifact(type, name, teamId)
          : undefined,
        listArtifacts: () => this.builderService
          ? (type?: 'agent' | 'team' | 'skill') => this.builderService!.listArtifacts(type)
          : undefined,
        hireFromTemplate: () => this.templateRegistry
          ? async (templateId: string, name: string, skills?: string[], teamId?: string) => {
              const newAgent = await this.createAgentFromTemplate({
                templateId,
                name,
                orgId: config.orgId ?? 'default',
                teamId: teamId || config.teamId,
                overrides: skills ? { skills } : undefined,
              });
              await this.startAgent(newAgent.id);
              return { id: newAgent.id, name: newAgent.config.name, role: newAgent.role.name };
            }
          : undefined,
        listTemplates: () => this.templateRegistry
          ? () => this.templateRegistry!.list().map(t => ({
              id: t.id, name: t.name, description: t.description, roleId: t.roleId, category: t.category ?? 'general',
            }))
          : undefined,
        searchHub: () => this.hubClient
          ? (opts) => this.hubClient!.search(opts)
          : undefined,
        downloadAndInstall: () => this.hubClient
          ? (itemId) => this.hubClient!.downloadAndInstall(itemId)
          : undefined,
        ensureTeam: async (name: string, description?: string) =>
          this.ensureTeam(config.orgId ?? 'default', name, description),
        requestApproval: pkgAh2 ? (req) => pkgAh2(id, req) : undefined,
      });
      for (const tool of packageTools) agent.registerTool(tool);
    }

    if (this.agentAuditCallback) {
      const cb = this.agentAuditCallback;
      agent.setAuditCallback(event => cb(id, event));
    }
    if (this.escalationHandler) {
      agent.setEscalationCallback(this.escalationHandler);
    }
    if (this.approvalHandler) {
      const ah = this.approvalHandler;
      agent.setApprovalCallback(
        async (req: { toolName: string; toolArgs: Record<string, unknown>; reason: string; taskId?: string; sessionId?: string; chatSessionId?: string }) =>
          ah(id, req)
      );
    }

    agent.setStateChangeCallback(this.buildStateChangeCallback());
    if (this.activityCallbacks) {
      agent.setActivityCallbacks(this.activityCallbacks);
    }
    agent.setBrowserCloseTabsHelper((sessionId) =>
      this.browserSessionManager.consumeCloseTabsReminder(id, sessionId),
    );
    // Callback, not a snapshot: the setting can change while agents are already running.
    agent.setBrowserElementSelectionProvider(() => this.browserElementSelection);
    this.forwardAgentEvents(agent);

    if (config.teamId) {
      agent.setTeamDataDir(markusPath('teams', config.teamId));
    }

    this.attachTerminalTools(agent, id);
    this.agents.set(id, agent);
    this.delegationManager.registerAgentCard({
      agentId: id,
      name: config.name,
      role: role.name,
      capabilities: config.skills,
      skills: config.skills,
      status: 'idle',
    });
    this.eventBus.emit('agent:restored', { agentId: id, name: row.name });
    log.info(`Agent restored: ${row.name} (${id})`, {
      profile: config.profile ? 'yes' : 'no',
      tokensUsedToday: row.tokensUsedToday ?? 0,
      activeTaskIds: Array.isArray(row.activeTaskIds) ? (row.activeTaskIds as string[]).length : 0,
    });
    return agent;
  }

  /**
   * Re-queue interrupted tasks for a restored agent.
   * Called after all agents are restored and started.
   */
  async rehydrateAgentTasks(agentId: string, activeTaskIds: string[]): Promise<void> {
    if (!this.taskService || activeTaskIds.length === 0) return;

    const agent = this.agents.get(agentId);
    if (!agent) return;

    for (const taskId of activeTaskIds) {
      try {
        const task = this.taskService.getTask(taskId);
        if (!task) {
          log.warn('Task not found during rehydration, skipping', { agentId, taskId });
          continue;
        }
        if (
          task.status === 'completed' ||
          task.status === 'cancelled' ||
          task.status === 'failed'
        ) {
          log.debug('Task already terminal, skipping rehydration', {
            agentId,
            taskId,
            status: task.status,
          });
          continue;
        }
        // Re-assign and the task service will handle execution
        log.info('Re-queuing interrupted task', { agentId, taskId, title: task.title });
        this.taskService.assignTask(taskId, agentId);
      } catch (error) {
        log.warn('Failed to rehydrate task', { agentId, taskId, error: String(error) });
      }
    }
  }

  async startAgent(agentId: string, options?: { initialHeartbeatDelayMs?: number; skipDisabledFlag?: boolean }): Promise<void> {
    const agent = this.getAgent(agentId);
    await agent.start(options);
    if (!options?.skipDisabledFlag) {
      this.disabledChangeHandler?.(agentId, false);
    }
  }

  async stopAgent(agentId: string): Promise<void> {
    const agent = this.getAgent(agentId);
    await agent.stop();
    this.cancelMcpRelease(agentId);
    this.browserSessionManager.cleanupAgent(agentId);
    this.terminalSessionManager.cleanupAgent(agentId);
    await this.mcpManager.removeAllForScope(agentId);
    this.disabledChangeHandler?.(agentId, true);
  }

  /**
   * OB-3 兜底转发：清除该 agent 无存活任务支撑的残留活动并回收至 idle（非破坏性）。
   * @returns 是否实际清理/回收成功。
   */
  reconcileAgentToIdle(agentId: string): boolean {
    const agent = this.agents.get(agentId);
    if (!agent) return false;
    try {
      return agent.reconcileToIdle();
    } catch (err) {
      log.warn('reconcileAgentToIdle failed', { agentId, error: String(err) });
      return false;
    }
  }

  /** OB-3 兜底转发：触发一次心跳，让 agent 自行核对任务并自愈（私有 bus 上发事件）。 */
  triggerAgentHeartbeat(agentId: string): boolean {
    const agent = this.agents.get(agentId);
    if (!agent) return false;
    try {
      agent.triggerHeartbeat();
      return true;
    } catch (err) {
      log.warn('triggerAgentHeartbeat failed', { agentId, error: String(err) });
      return false;
    }
  }

  async removeAgent(agentId: string, opts?: { purgeFiles?: boolean }): Promise<void> {
    const agent = this.agents.get(agentId);
    if (agent) {
      try { await agent.stop(); } catch (err) { log.warn('Agent stop failed during removal, proceeding', { agentId, error: String(err) }); }
      this.cancelMcpRelease(agentId);
      this.browserSessionManager.cleanupAgent(agentId);
      this.terminalSessionManager.cleanupAgent(agentId);
      await this.mcpManager.removeAllForScope(agentId);
      this.delegationManager.unregisterAgentCard(agentId);
      this.agents.delete(agentId);
      this.eventBus.emit('agent:removed', { agentId });
    }

    if (opts?.purgeFiles) {
      const agentDir = join(this.dataDir, agentId);
      if (existsSync(agentDir)) {
        try {
          rmSync(agentDir, { recursive: true, force: true });
          log.info(`Agent data directory purged: ${agentDir}`);
        } catch (err) {
          log.warn('Failed to purge agent data directory', { agentId, error: String(err) });
        }
      }
    }

    log.info(`Agent removed: ${agentId}`, { purgeFiles: !!opts?.purgeFiles });
  }

  /** Remove orphaned agent directories that have no matching DB record */
  purgeOrphanedAgentDirs(knownAgentIds: Set<string>): { removed: string[]; failed: string[] } {
    const removed: string[] = [];
    const failed: string[] = [];
    if (!existsSync(this.dataDir)) return { removed, failed };
    for (const entry of readdirSync(this.dataDir, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name === 'vector-store') continue;
      if (knownAgentIds.has(entry.name)) continue;
      const dirPath = join(this.dataDir, entry.name);
      try {
        rmSync(dirPath, { recursive: true, force: true });
        removed.push(entry.name);
      } catch (err) {
        log.warn('Failed to remove orphaned agent directory', { dir: dirPath, error: String(err) });
        failed.push(entry.name);
      }
    }
    if (removed.length > 0) log.info(`Purged ${removed.length} orphaned agent directories`);
    return { removed, failed };
  }

  getAgent(agentId: string): Agent {
    const agent = this.agents.get(agentId);
    if (!agent) throw new Error(`Agent not found: ${agentId}`);
    return agent;
  }

  hasAgent(agentId: string): boolean {
    return this.agents.has(agentId);
  }


  setAuditCallback(
    cb: (
      agentId: string,
      event: {
        type: string;
        action: string;
        tokensUsed?: number;
        inputTokens?: number;
        outputTokens?: number;
        cacheReadTokens?: number;
        cacheWriteTokens?: number;
        cost?: number;
        cuCost?: number;
        provider?: string;
        durationMs?: number;
        success: boolean;
        detail?: string;
      }
    ) => void
  ): void {
    this.agentAuditCallback = cb;
    for (const [id, agent] of this.agents) {
      agent.setAuditCallback(event => cb(id, event));
    }
  }

  setEscalationHandler(handler: (agentId: string, reason: string) => void): void {
    this.escalationHandler = handler;
    for (const [, agent] of this.agents) {
      agent.setEscalationCallback(handler);
    }
  }

  setApprovalHandler(
    handler: (
      agentId: string,
      request: { toolName: string; toolArgs: Record<string, unknown>; reason: string; taskId?: string; sessionId?: string; chatSessionId?: string }
    ) => Promise<{ approved: boolean; comment?: string }>
  ): void {
    this.approvalHandler = handler;
    for (const [id, agent] of this.agents) {
      agent.setApprovalCallback(
        async (req: { toolName: string; toolArgs: Record<string, unknown>; reason: string; taskId?: string; sessionId?: string; chatSessionId?: string }) =>
          handler(id, req)
      );
    }
  }


  /**
   * Build the combined state-change callback for an agent. Handles:
   * 1. MCP scoped-process lifecycle (release on idle, cancel release on working)
   * 2. DelegationManager status sync
   * 3. External handler forwarding (DB persistence, WS broadcast, etc.)
   */
  private buildStateChangeCallback(): (
    agentId: string,
    state: { status: string; tokensUsedToday: number; activeTaskIds: string[]; lastError?: string; lastErrorAt?: string; currentActivity?: AgentActivity; lastHeartbeat?: string }
  ) => void {
    return (agentId, state) => {
      if (state.status === 'idle' && state.activeTaskIds.length === 0) {
        this.scheduleMcpRelease(agentId);
      } else if (state.status === 'working') {
        this.cancelMcpRelease(agentId);
      }
      this.delegationManager.updateAgentStatus(agentId, state.status);
      if (this.stateChangeHandler) {
        this.stateChangeHandler(agentId, state);
      }
    };
  }

  private scheduleMcpRelease(agentId: string): void {
    if (this.mcpReleaseTimers.has(agentId)) return;
    const timer = setTimeout(() => {
      this.mcpReleaseTimers.delete(agentId);
      // Disconnect idle non-browser MCP processes.
      // chrome-devtools manages its own lifecycle via the MCP idle timer (5min).
      this.mcpManager.disconnectAllForScope(agentId, { skip: ['chrome-devtools'] }).catch((err) => { log.debug('MCP disconnect failed for idle agent', { agentId, error: String(err) }); });
      log.info(`Released scoped MCP processes for idle agent: ${agentId}`);
    }, AgentManager.MCP_IDLE_GRACE_MS);
    timer.unref();
    this.mcpReleaseTimers.set(agentId, timer);
  }

  private cancelMcpRelease(agentId: string): void {
    const timer = this.mcpReleaseTimers.get(agentId);
    if (timer) {
      clearTimeout(timer);
      this.mcpReleaseTimers.delete(agentId);
    }
  }

  setStateChangeHandler(
    handler: (
      agentId: string,
      state: { status: string; tokensUsedToday: number; activeTaskIds: string[]; lastError?: string; lastErrorAt?: string; currentActivity?: AgentActivity; lastHeartbeat?: string }
    ) => void
  ): void {
    this.stateChangeHandler = handler;
    for (const [, agent] of this.agents) {
      agent.setStateChangeCallback(this.buildStateChangeCallback());
    }
  }

  setDisabledChangeHandler(handler: (agentId: string, disabled: boolean) => void): void {
    this.disabledChangeHandler = handler;
  }

  /**
   * Forward events from an agent's private EventBus to the manager's
   * EventBus so that external listeners (e.g. start.ts WS broadcasts) receive them.
   *
   * Each Agent creates its own EventBus. Without forwarding, events emitted by
   * the agent, its mailbox, and its attention controller would never reach the
   * manager-level bus where start.ts registers WS broadcast handlers.
   */
  private forwardAgentEvents(agent: Agent): void {
    // 白名单与转发实现见模块级 `AGENT_FORWARDED_EVENTS` / `wireAgentEventForwarding`：
    // 抽出去是为了让「事件可达性」既有单测、又能在 architecture-guard 里静态强制。
    wireAgentEventForwarding(agent.getEventBus(), this.eventBus);
  }

  setActivityCallbacks(cbs: {
    onStart: (activity: AgentActivity & { agentId: string }) => void;
    onLog: (data: { activityId: string; agentId: string; seq: number; type: string; content: string; metadata?: Record<string, unknown> }) => void;
    onEnd: (activityId: string, summary: { endedAt: string; totalTokens: number; totalTools: number; success: boolean; summary?: string; keywords?: string }) => void;
  }): void {
    this.activityCallbacks = cbs;
    for (const [, agent] of this.agents) {
      agent.setActivityCallbacks(cbs);
    }
  }

  setRecallCallbacks(cbs: RecallCallbacks): void {
    this.recallCallbacks = cbs;
    for (const [id, agent] of this.agents) {
      agent.registerTool(createRecallTool({ agentId: id, ...cbs }));
    }
  }

  /**
   * Legacy wiring hook kept for API compatibility. The session tool is now
   * ALWAYS Memory-backed (createSessionRepo) — the agent's context is built
   * from MemoryStore sessions, so the tool must read/write that same space.
   * Wiring it to the Sqlite `cs_*` repo (as before) split brain: ownership
   * checks passed for cs_* ids but MemoryStore had no such sessions, so
   * pin/compact silently persisted nothing. The external `repo` is ignored
   * for tool wiring (kept for any external consumers of this hook).
   */
  setSessionRepo(_repo: SessionRepo): void {
    for (const [id, agent] of this.agents) {
      const mem = agent.getMemory();
      agent.registerTool(createSessionTool({
        agentId: id,
        chatSessionRepo: createMemorySessionRepo(mem),
        compactor: {
          compactOnDemand: (sid, keep) => mem.compactSession(sid, keep),
          compactWithAnchor: (sid, keep, anchor) => {
            if (mem.setSlot && anchor.goal) mem.setSlot(sid, 'goal', anchor.goal);
            if (mem.setSlot && anchor.done) mem.setSlot(sid, 'done', anchor.done);
            if (mem.setSlot && anchor.next) mem.setSlot(sid, 'next', anchor.next);
            const res = mem.compactSession(sid, keep);
            return { ...res, anchorKey: ['goal', 'done', 'next'].filter((k) => anchor[k as keyof typeof anchor]).join(',') || 'anchor' };
          },
        },
        slotStore: {
          getSlots: (sid) => mem.getSlots ? mem.getSlots(sid) : [],
          setSlot: (sid, k, v) => { if (mem.setSlot) mem.setSlot(sid, k, v); },
          removeSlot: (sid, k) => { if (mem.removeSlot) mem.removeSlot(sid, k); },
          serialize: (sid) => mem.serializeSlots ? mem.serializeSlots(sid) : '',
        },
        fragmentStore: {
          retrieveFragments: (q, mx) => mem.retrieveFragments ? mem.retrieveFragments(q, mx) : [],
          includeFragment: (sid, fid) => mem.includeFragment ? mem.includeFragment(sid, fid) : { ok: false, message: 'unavailable' },
          purgeSessionFragments: (sid) => mem.purgeSessionFragments ? mem.purgeSessionFragments(sid) : 0,
          sessionStats: (sid) => mem.sessionStats ? mem.sessionStats(sid) : { messageCount: 0, slotKeys: [], fragmentCount: 0 },
        },
        titleUpdater: this.sessionTitleUpdater,
        currentDbSessionId: () => agent.getDbSessionId(),
        currentMemorySessionId: () => agent.getCurrentSessionId(),
        resolveMemorySessionByDbSessionId: (dbId) => agent.getMemorySessionIdForDbSession(dbId),
        diskSessionCount: () => mem.countSessionsOnDisk?.() ?? 0,
      }));
    }
  }

  listAgents(): Array<{
    id: string;
    name: string;
    role: string;
    status: string;
    agentRole: string;
    skills: string[];
    activeTaskCount: number;
    teamId?: string;
    teamName?: string;
    lastError?: string;
    lastErrorAt?: string;
    currentTaskId?: string;
    currentActivity?: AgentActivity;
    activeTaskIds?: string[];
    lastHeartbeat?: string;
    lastProgressAt?: string;
    tokensUsedToday?: number;
    mailboxDepth?: number;
    attentionState?: string;
    modelSupportsVision?: boolean;
  }> {
    return [...this.agents.values()].map(a => {
      const state = a.getState();
      let mailboxDepth: number | undefined;
      let attentionState: string | undefined;
      try {
        mailboxDepth = a.getMailbox().depth;
        attentionState = a.getAttentionController().getState();
      } catch (err) { log.debug('Mailbox/attention not initialized', { agentId: a.id, error: String(err) }); }
      return {
        id: a.id,
        name: a.config.name,
        role: a.role.name,
        status: state.status,
        agentRole: a.config.agentRole,
        skills: a.config.skills,
        activeTaskCount: state.activeTaskCount ?? 0,
        teamId: a.config.teamId,
        teamName: a.getTeamName(),
        lastError: state.lastError,
        lastErrorAt: state.lastErrorAt,
        currentTaskId: state.currentTaskId,
        currentActivity: state.currentActivity,
        activeTaskIds: state.activeTaskIds,
        lastHeartbeat: state.lastHeartbeat,
        lastProgressAt: state.lastProgressAt,
        tokensUsedToday: state.tokensUsedToday,
        mailboxDepth,
        attentionState,
        modelSupportsVision: a.getModelSupportsVision(),
      };
    });
  }

  listAvailableRoles(): string[] {
    return this.roleLoader.listAvailableRoles();
  }

  /**
   * Resolve the absolute path to the AGENT HANDBOOK (templates/roles/HANDBOOK.md) for a role.
   * resolveTemplateDir(roleName) returns the ROLE subdir (…/templates/roles/secretary); the
   * handbook is a sibling at the roles/ level (…/templates/roles/HANDBOOK.md), so we step up
   * one directory. Returning a definite absolute path means the agent reads it WITHOUT searching.
   * Returns undefined when the role is custom (no template dir), in which case ContextEngine
   * falls back to a source-dir default.
   */
  private resolveHandbookPath(roleIdOrName: string | undefined): string | undefined {
    const target = roleIdOrName && roleIdOrName !== 'custom'
      ? this.roleLoader.resolveTemplateDir(roleIdOrName)
      : undefined;
    return target ? join(dirname(target), 'HANDBOOK.md') : undefined;
  }

  getEventBus(): EventBus {
    return this.eventBus;
  }



  getDelegationManager(): DelegationManager {
    return this.delegationManager;
  }

  setTemplateRegistry(registry: TemplateRegistry): void {
    this.templateRegistry = registry;
  }

  getTemplateRegistry(): TemplateRegistry | undefined {
    return this.templateRegistry;
  }

  setBuilderService(service: { listArtifacts: (type?: 'agent' | 'team' | 'skill') => Array<{ type: string; name: string; description?: string }>; installArtifact: (type: 'agent' | 'team' | 'skill', name: string, teamId?: string) => Promise<{ type: string; installed: unknown }> }): void {
    this.builderService = service;
  }

  setTeamUpdater(updater: (teamId: string, data: { name?: string; description?: string }) => Promise<{ id: string; name: string; description?: string }>): void {
    this.teamUpdater = updater;
  }

  setTeamCreator(creator: (orgId: string, name: string, description?: string) => Promise<{ id: string; name: string; description?: string }>): void {
    this.teamCreator = creator;
  }

  setOrgTeamLister(lister: (orgId: string) => Array<{ id: string; name: string; description?: string; memberAgentIds: string[] }>): void {
    this.orgTeamLister = lister;
  }

  /** Find a team by display name, or create it (used by package_install team_name). */
  private async ensureTeam(
    orgId: string,
    name: string,
    description?: string,
  ): Promise<{ id: string; name: string; created: boolean }> {
    const trimmed = name.trim();
    if (!trimmed) throw new Error('team name is required');
    const existing = this.orgTeamLister?.(orgId)?.find(
      t => t.name.trim().toLowerCase() === trimmed.toLowerCase(),
    );
    if (existing) return { id: existing.id, name: existing.name, created: false };
    if (!this.teamCreator) {
      throw new Error('Team ensure service is not available yet. Please try again in a moment.');
    }
    const created = await this.teamCreator(orgId, trimmed, description);
    return { id: created.id, name: created.name, created: true };
  }

  private buildSecretaryToolsContext(orgId: string): SecretaryToolsContext {
    return {
      listTeams: () => {
        if (this.orgTeamLister) {
          return this.orgTeamLister(orgId).map(t => ({
            id: t.id,
            name: t.name,
            description: t.description,
            memberCount: t.memberAgentIds.length,
            members: t.memberAgentIds.map(agentId => {
              try {
                const ag = this.getAgent(agentId);
                return { id: agentId, name: ag.config.name, status: ag.getState().status };
              } catch {
                return { id: agentId, name: agentId, status: 'unknown' };
              }
            }),
          }));
        }
        // Fallback: infer teams from agents that already have a teamId (misses empty teams).
        const teams: Array<{ id: string; name: string; memberCount: number; members: Array<{ id: string; name: string; status: string }> }> = [];
        for (const [, ag] of this.agents) {
          if (ag.config.teamId) {
            const teamId = ag.config.teamId;
            let team = teams.find(t => t.id === teamId);
            if (!team) {
              team = { id: teamId, name: teamId, memberCount: 0, members: [] };
              teams.push(team);
            }
            team.members.push({ id: ag.id, name: ag.config.name, status: ag.getState().status });
            team.memberCount = team.members.length;
          }
        }
        return teams;
      },
      stopTeam: async (teamId: string) => this.stopAgentsByIds(
        [...this.agents.values()].filter(a => a.config.teamId === teamId).map(a => a.id),
      ),
      startTeam: async (teamId: string) => this.startAgentsByIds(
        [...this.agents.values()].filter(a => a.config.teamId === teamId && a.getState().status === 'offline').map(a => a.id),
      ),
    };
  }

  setAgentConfigPersister(persister: (agentId: string, data: Record<string, unknown>) => Promise<void>): void {
    this.agentConfigPersister = persister;
  }

  /**
   * Build the per-agent context passed to the settings tools so an agent can
   * inspect and update its OWN default model. Mutates the live config (takes
   * effect next call) and persists to DB via the injected persister when set.
   */
  private buildSettingsAgentContext(agentId: string): SettingsAgentContext {
    return {
      agentId,
      getLlmConfig: () => this.getAgent(agentId)?.config.llmConfig,
      persistLlmConfig: (patch: Partial<LLMAssignment>) => this.persistAgentLlmConfig(agentId, patch),
    };
  }

  private async persistAgentLlmConfig(agentId: string, patch: Partial<LLMAssignment>): Promise<boolean> {
    const target = this.getAgent(agentId);
    if (!target) return false;
    try {
      // Mutate the nested object in place (readonly only bars reassigning `config`,
      // exactly like Agent.setHeartbeatInterval mutates config fields).
      target.config.llmConfig = { ...target.config.llmConfig, ...patch };
      if (this.agentConfigPersister) {
        try { await this.agentConfigPersister(agentId, { llmConfig: target.config.llmConfig }); } catch { /* best effort */ }
      }
      return true;
    } catch {
      return false;
    }
  }

  setHubClient(client: { search: (opts?: { type?: string; query?: string }) => Promise<Array<{ id: string; name: string; type: string; description: string; author: string; version?: string; downloads?: number }>>; downloadAndInstall: (itemId: string) => Promise<{ type: string; installed: unknown }> }): void {
    this.hubClient = client;
  }

  setGroupChatHandlers(handlers: {
    sendGroupMessage: (
      channelKey: string,
      message: string,
      senderId: string,
      senderName: string
    ) => Promise<string>;
    createGroupChat: (
      name: string,
      creatorId: string,
      creatorName: string,
      memberIds: string[]
    ) => Promise<{ id: string; name: string }>;
    listGroupChats: () => Promise<
      Array<{ id: string; name: string; type: string; channelKey: string }>
    >;
    getChannelMessages?: (
      channelKey: string,
      limit: number,
      before?: string
    ) => Promise<{ messages: Array<{ senderName: string; senderType: string; text: string; createdAt: string }>; hasMore: boolean }>;
    getChatSessionMessages?: (
      sessionId: string,
      limit: number,
      before?: string,
      agentId?: string,
    ) => Promise<{ messages: Array<{ id?: string; role: string; text: string; createdAt: string }>; hasMore: boolean }>;
    ensureDmChannel?: (
      channelKey: string,
      member1: { id: string; name: string },
      member2: { id: string; name: string },
    ) => Promise<{ channelKey: string }>;
  }): void {
    this.groupChatHandlers = handlers;
  }

  /** Deterministic channel key for A2A DM between two agents. Sorted to ensure one channel per pair. */
  getA2ADmChannelKey(agentId1: string, agentId2: string): string {
    const sorted = [agentId1, agentId2].sort();
    return `dm:a2a:${sorted[0]}:${sorted[1]}`;
  }

  /**
   * Create an agent from a template with optional overrides.
   */
  async createAgentFromTemplate(request: TemplateInstantiateRequest): Promise<Agent> {
    if (!this.templateRegistry) throw new Error('Template registry not configured');
    const template = this.templateRegistry.get(request.templateId);
    if (!template) throw new Error(`Template not found: ${request.templateId}`);

    const llmProvider = request.overrides?.llmProvider ?? template.llmProvider;
    return this.createAgent({
      name: request.name,
      roleName: template.roleId,
      orgId: request.orgId,
      teamId: request.teamId,
      agentRole: template.agentRole,
      skills: request.overrides?.skills ?? template.skills,
      heartbeatIntervalMs: request.overrides?.heartbeatIntervalMs ?? template.heartbeatIntervalMs,
      llmProvider,
    });
  }

  // ─── Batch Agent Control ───────────────────────────────────────────────────

  async startAgentsByIds(
    ids: string[],
    options?: { staggerHeartbeats?: boolean },
  ): Promise<{ success: string[]; failed: Array<{ id: string; error: string }> }> {
    const stagger = options?.staggerHeartbeats ?? true;
    const success: string[] = [];
    const failed: Array<{ id: string; error: string }> = [];
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i]!;
      try {
        let initialHeartbeatDelayMs: number | undefined;
        if (stagger && ids.length > 1) {
          const agent = this.getAgent(id);
          const intervalMs = agent.config.heartbeatIntervalMs || DEFAULT_HEARTBEAT_INTERVAL_MS;
          initialHeartbeatDelayMs = Math.floor((i / ids.length) * intervalMs);
        }
        await this.startAgent(id, { initialHeartbeatDelayMs });
        success.push(id);
      } catch (err) {
        failed.push({ id, error: String(err) });
      }
    }
    return { success, failed };
  }

  async stopAgentsByIds(ids: string[]): Promise<{ success: string[]; failed: Array<{ id: string; error: string }> }> {
    const success: string[] = [];
    const failed: Array<{ id: string; error: string }> = [];
    for (const id of ids) {
      try {
        await this.stopAgent(id);
        success.push(id);
      } catch (err) {
        failed.push({ id, error: String(err) });
      }
    }
    return { success, failed };
  }


  // ─── Global Agent Control ──────────────────────────────────────────────────

  private emergencyMode = false;

  async stopAllAgents(reason?: string): Promise<void> {
    for (const [id, agent] of this.agents) {
      try {
        await agent.stop(reason);
        this.disabledChangeHandler?.(id, true);
      } catch (err) {
        log.warn('Failed to stop agent', { agentId: id, error: String(err) });
      }
    }
    this.eventBus.emit('system:pause-all', { reason });
    log.info('All agents stopped', { reason });
  }

  async startAllAgents(): Promise<void> {
    for (const [id, agent] of this.agents) {
      try {
        if (agent.getState().status === 'offline') {
          await agent.start();
          this.disabledChangeHandler?.(id, false);
        }
      } catch (err) {
        log.warn('Failed to start agent', { agentId: id, error: String(err) });
      }
    }
    this.emergencyMode = false;
    this.eventBus.emit('system:resume-all', {});
    log.info('All agents started');
  }

  async emergencyStop(): Promise<void> {
    for (const [id, agent] of this.agents) {
      try {
        agent.cancelActiveStream();
        await agent.stop();
      } catch (err) {
        log.warn('Failed to stop agent during emergency', { agentId: id, error: String(err) });
      }
    }
    this.emergencyMode = true;
    this.eventBus.emit('system:emergency-stop', {});
    log.warn('EMERGENCY STOP — all agents stopped');
  }

  isGlobalStopped(): boolean {
    for (const [, agent] of this.agents) {
      if (agent.getState().status !== 'offline') return false;
    }
    return this.agents.size > 0;
  }

  isEmergencyMode(): boolean {
    return this.emergencyMode;
  }

  clearEmergencyMode(): void {
    this.emergencyMode = false;
    log.info('Emergency mode cleared');
  }

  async shutdown(): Promise<void> {
    for (const [, agent] of this.agents) {
      try {
        await agent.stop();
      } catch (err) { log.debug('Agent stop failed during shutdown', { agentId: agent.id, error: String(err) }); }
    }
    await this.mcpManager.disconnectAll();
    log.info('AgentManager shutdown complete — all metrics flushed');
  }

  // ─── Role Template Versioning & Sync ──────────────────────────────────────

  private static readonly ROLE_FILES = ['ROLE.md', 'HEARTBEAT.md', 'POLICIES.md', 'CONTEXT.md'] as const;

  checkRoleUpdate(agentId: string): RoleUpdateStatus {
    const agent = this.getAgent(agentId);
    const { roleId } = agent.config;
    const agentRoleDir = join(this.dataDir, agentId, 'role');

    // Skip comparison for agents with custom roles (e.g. installed from builder artifacts)
    const originPath = join(agentRoleDir, '.role-origin.json');
    if (existsSync(originPath)) {
      try {
        const origin = JSON.parse(readFileSync(originPath, 'utf-8'));
        if (origin.customRole) {
          return { agentId, roleId, templateId: roleId, hasTemplate: false, isUpToDate: true, files: [] };
        }
      } catch (err) { log.debug('Malformed .role-origin.json', { agentId, error: String(err) }); }
    }

    const templateDir = this.roleLoader.resolveTemplateDir(roleId);

    if (!templateDir) {
      return { agentId, roleId, templateId: roleId, hasTemplate: false, isUpToDate: true, files: [] };
    }

    // Fallback heuristic: if the ROLE.md title (first # heading) differs from the
    // template's, this is a custom-built agent — skip template comparison.
    const agentRolePath = join(agentRoleDir, 'ROLE.md');
    const templateRolePath = join(templateDir, 'ROLE.md');
    if (existsSync(agentRolePath) && existsSync(templateRolePath)) {
      const headingOf = (text: string) => text.match(/^#\s+(.+)/m)?.[1]?.trim();
      const agentTitle = headingOf(readFileSync(agentRolePath, 'utf-8'));
      const templateTitle = headingOf(readFileSync(templateRolePath, 'utf-8'));
      if (agentTitle && templateTitle && agentTitle !== templateTitle) {
        return { agentId, roleId, templateId: roleId, hasTemplate: false, isUpToDate: true, files: [] };
      }
    }

    const files: RoleFileStatus[] = [];
    let allIdentical = true;

    for (const file of AgentManager.ROLE_FILES) {
      const tPath = join(templateDir, file);
      const aPath = join(agentRoleDir, file);
      const tExists = existsSync(tPath);
      const aExists = existsSync(aPath);

      if (!tExists && !aExists) continue;

      if (tExists && !aExists) {
        files.push({ file, status: 'added_in_template' });
        allIdentical = false;
      } else if (!tExists && aExists) {
        files.push({ file, status: 'agent_only' });
      } else {
        const tContent = readFileSync(tPath, 'utf-8');
        const aContent = readFileSync(aPath, 'utf-8');
        if (tContent === aContent) {
          files.push({ file, status: 'identical' });
        } else {
          files.push({ file, status: 'modified' });
          allIdentical = false;
        }
      }
    }

    return { agentId, roleId, templateId: roleId, hasTemplate: true, isUpToDate: allIdentical, files };
  }

  getRoleFileDiff(agentId: string, fileName: string): RoleFileDiff {
    const agent = this.getAgent(agentId);
    const { roleId } = agent.config;
    const templateDir = this.roleLoader.resolveTemplateDir(roleId);
    const agentRoleDir = join(this.dataDir, agentId, 'role');

    const aPath = join(agentRoleDir, fileName);
    const tPath = templateDir ? join(templateDir, fileName) : null;

    return {
      file: fileName,
      agentContent: existsSync(aPath) ? readFileSync(aPath, 'utf-8') : null,
      templateContent: tPath && existsSync(tPath) ? readFileSync(tPath, 'utf-8') : null,
    };
  }

  syncRoleFromTemplate(agentId: string, fileNames?: string[]): RoleSyncResult {
    const agent = this.getAgent(agentId);
    const { roleId } = agent.config;
    const templateDir = this.roleLoader.resolveTemplateDir(roleId);

    if (!templateDir) {
      return { agentId, success: false, error: `No template found for roleId: ${roleId}`, synced: [] };
    }

    const agentRoleDir = join(this.dataDir, agentId, 'role');
    mkdirSync(agentRoleDir, { recursive: true });

    const filesToSync = fileNames ?? [...AgentManager.ROLE_FILES];
    const synced: string[] = [];

    for (const file of filesToSync) {
      const src = join(templateDir, file);
      if (existsSync(src)) {
        copyFileSync(src, join(agentRoleDir, file));
        synced.push(file);
      }
    }

    agent.reloadRole();

    log.info('Synced agent role from template', { agentId, roleId, synced });
    return { agentId, success: true, synced };
  }

  checkAllRoleUpdates(): RoleUpdateStatus[] {
    const results: RoleUpdateStatus[] = [];
    for (const [agentId] of this.agents) {
      try {
        results.push(this.checkRoleUpdate(agentId));
      } catch (err) {
        log.debug('Role update check failed for agent', { agentId, error: String(err) });
      }
    }
    return results;
  }

  // ─── System Announcements ────────────────────────────────────────────────

  private announcements: SystemAnnouncement[] = [];

  broadcastAnnouncement(announcement: SystemAnnouncement): void {
    const now = new Date().toISOString();
    this.announcements = this.announcements.filter(a => !a.expiresAt || a.expiresAt > now);
    this.announcements.push(announcement);

    for (const [, agent] of this.agents) {
      if (agent.getState().status !== 'offline') {
        agent.enqueueToMailbox('system_event', {
          summary: `[Announcement] ${announcement.title}`.slice(0, 100),
          content: JSON.stringify(announcement),
        }, {
          metadata: { senderId: 'system', senderName: 'System' },
        });
      }
    }

    this.eventBus.emit('system:announcement', announcement);
    log.info('Announcement broadcast', { id: announcement.id, title: announcement.title });
  }

  getActiveAnnouncements(): SystemAnnouncement[] {
    const now = new Date().toISOString();
    return this.announcements.filter(a => !a.expiresAt || a.expiresAt > now);
  }

  acknowledgeAnnouncement(announcementId: string, agentId: string): void {
    const announcement = this.announcements.find(a => a.id === announcementId);
    if (announcement && !announcement.acknowledged.includes(agentId)) {
      announcement.acknowledged.push(agentId);
    }
  }

  /**
   * Rebuild and inject identity context for all agents in an organization.
   * Colleagues are scoped to the agent's own team; other teams are listed for cross-team awareness.
   */
  refreshIdentityContexts(
    orgId: string,
    orgName: string,
    humans: HumanUser[],
    teams?: Array<{ id: string; name: string; description?: string; memberAgentIds: string[] }>,
    projects?: Array<{ id: string; name: string; description: string; status: string; teamIds: string[] }>,
  ): void {
    const orgAgents = [...this.agents.values()].filter(a => a.config.orgId === orgId);

    const resolvedProjects = projects ?? (this.projectService
      ? this.projectService.listProjects(orgId)
      : []);

    const agentTeamMap = new Map<string, string>();
    for (const t of teams ?? []) {
      for (const aid of t.memberAgentIds) agentTeamMap.set(aid, t.id);
    }

    for (const agent of orgAgents) {
      const myTeamId = agent.config.teamId ?? agentTeamMap.get(agent.id);
      const myTeam = teams?.find(t => t.id === myTeamId);

      const sameTeamAgents = myTeamId
        ? orgAgents.filter(a => a.id !== agent.id && (a.config.teamId === myTeamId || agentTeamMap.get(a.id) === myTeamId))
        : orgAgents.filter(a => a.id !== agent.id);

      const colleagues = sameTeamAgents.map(a => ({
        id: a.id,
        name: a.config.name,
        role: a.role.name,
        type: 'agent' as const,
        skills: a.config.skills,
        status: a.getState().status,
      }));

      const teamManager = sameTeamAgents.find(a => a.config.agentRole === 'manager');

      const otherTeams = (teams ?? [])
        .filter(t => t.id !== myTeamId)
        .map(t => ({
          id: t.id,
          name: t.name,
          members: t.memberAgentIds
            .map(aid => {
              try {
                const a = this.getAgent(aid);
                return { id: a.id, name: a.config.name, role: a.role.name };
              } catch (err) { log.debug('Agent not found while mapping team members', { agentId: aid, error: String(err) }); return null; }
            })
            .filter((m): m is { id: string; name: string; role: string } => m !== null),
        }))
        .filter(t => t.members.length > 0);

      const teamProjects = myTeamId && resolvedProjects.length > 0
        ? resolvedProjects
            .filter(p => p.teamIds.includes(myTeamId))
            .map(p => ({ id: p.id, name: p.name, description: p.description, status: p.status }))
        : undefined;

      const identity: IdentityContext = {
        self: {
          id: agent.id,
          name: agent.config.name,
          role: agent.role.name,
          agentRole: agent.config.agentRole,
          skills: agent.config.skills,
        },
        organization: { id: orgId, name: orgName },
        team: myTeam ? { id: myTeam.id, name: myTeam.name, description: myTeam.description } : undefined,
        colleagues,
        otherTeams: otherTeams.length > 0 ? otherTeams : undefined,
        humans: humans.map(h => ({ id: h.id, name: h.name, role: h.role })),
        manager:
          teamManager && teamManager.id !== agent.id
            ? { id: teamManager.id, name: teamManager.config.name }
            : undefined,
        teamProjects: teamProjects && teamProjects.length > 0 ? teamProjects : undefined,
      };

      agent.setIdentityContext(identity);
    }

    log.info(`Refreshed identity contexts for ${orgAgents.length} agents in org ${orgId}`);
  }
}

```

## packages/core/src/tools/builtin.ts

Source: https://github.com/markus-global/markus/blob/72bf7d72474bcf82734753e620f2d0478e80c8e7/packages/core/src/tools/builtin.ts

SHA-256: `e938433f6064a3215adabff5bc526900d04d7050292627ee37f974ffe6e44df9`

```
import type { AgentToolHandler } from '../agent.js';
import type { PathAccessPolicy } from '@markus/shared';
import { createShellTool, type ShellAgentMeta, type CommandApprovalCallback } from './shell.js';
import { createFileReadTool, createFileWriteTool, createFileEditTool } from './file.js';
import { WebFetchTool } from './web-fetch.js';
import { WebSearchTool } from './web-search.js';
import { WebExtractTool } from './web-extract.js';
import { createGrepTool, createGlobTool, createListDirectoryTool } from './search.js';
import { createPatchTool } from './patch.js';
import { createBackgroundExecTool, createProcessTool } from './process-manager.js';
import type { SecurityGuard } from '../security.js';

export interface BuiltinToolsOptions {
  agentId?: string;
  agentMeta?: ShellAgentMeta;
  security?: SecurityGuard;
  workspacePath?: string;
  /** Multi-tier access policy (takes precedence over workspacePath when set) */
  pathPolicy?: PathAccessPolicy;
  enableBackgroundExec?: boolean;
  onCommandApproval?: CommandApprovalCallback;
}

export function createBuiltinTools(opts?: BuiltinToolsOptions): AgentToolHandler[] {
  const policy = opts?.pathPolicy;
  const wp = policy?.primaryWorkspace ?? opts?.workspacePath;

  const tools: AgentToolHandler[] = [
    createShellTool(opts?.security, wp, opts?.agentMeta, policy, opts?.onCommandApproval),
    createFileReadTool(opts?.security, wp, policy),
    createFileWriteTool(opts?.security, wp, policy),
    createFileEditTool(opts?.security, wp, policy),
    createPatchTool(opts?.security, wp, policy),
    createGrepTool(wp, policy),
    createGlobTool(wp, policy),
    createListDirectoryTool(wp, policy),
    WebFetchTool,
    WebSearchTool,
    WebExtractTool,
  ];

  if (opts?.enableBackgroundExec !== false) {
    tools.push(createBackgroundExecTool(wp));
    tools.push(createProcessTool());
  }

  return tools;
}

```

## packages/core/src/tools/session.ts

Source: https://github.com/markus-global/markus/blob/72bf7d72474bcf82734753e620f2d0478e80c8e7/packages/core/src/tools/session.ts

SHA-256: `b1d99dd04e97df332a69713489487a593dfb83fd59f10201bb2fa82147840ff0`

```
import type { AgentToolHandler } from '../agent.js';
import { createLogger, getTextContent } from '@markus/shared';
import type { IMemoryStore } from '../memory/types.js';
import { resetAllFileReadCaches } from './file.js';
import { DEFAULT_COMPACT_KEEP_LAST, COMPACT_KEEP_LAST_MIN, COMPACT_KEEP_LAST_MAX } from '../context-budget.js';

const log = createLogger('session-tools');

export interface SessionRow {
  id: string;
  agentId: string;
  userId?: string | null;
  title?: string | null;
  isMain?: boolean;
  createdAt?: string | Date;
  lastMessageAt?: string | Date;
  [k: string]: unknown;
}

export interface SessionMsgRow {
  id: string;
  sessionId: string;
  agentId?: string;
  role?: string;
  content?: string;
  createdAt?: string | Date;
  [k: string]: unknown;
}

/** 存储层查询能力的最小契约（由 SqliteChatSessionRepo 满足）。 */
export interface SessionRepo {
  listSessionsPaginated(
    agentId: string,
    opts: { since?: string; until?: string; userId?: string; page?: number; pageSize?: number },
  ): {
    sessions: SessionRow[];
    total: number;
    page: number;
    pageSize: number;
    hasMore: boolean;
  };
  getSession(sessionId: string): SessionRow | null | undefined;
  listMessagesPaginated(
    sessionId: string,
    opts: { since?: string; until?: string; page?: number; pageSize?: number },
  ): {
    messages: SessionMsgRow[];
    total: number;
    page: number;
    pageSize: number;
    hasMore: boolean;
  };
  /** 统计某 session 中某 agent 发出的消息数（用于"参与"权限判定）。 */
  countMessagesByAgent(sessionId: string, agentId: string): number;
  /** 重命名一个 session。可选 —— 不支持的适配器可忽略。 */
  updateTitle?(sessionId: string, title: string): void | null | { id: string; title: string | null };
}

/** 可选注入：让 agent 通过 session 工具主动压缩自己的历史上下文（0.9.7 context-root-fix）。 */
export interface SessionCompactor {
  compactOnDemand(sessionId: string, keepLast: number): {
    summary: string;
    flushedCount: number;
    /** Messages retained after compaction. Optional — mocks may omit. */
    remaining?: number;
    /** false when the session does not exist (memory + disk). Optional — mocks may omit. */
    found?: boolean;
  };
  /** ContextOS: compact with an explicit structured anchor (goal/done/next). */
  compactWithAnchor?(
    sessionId: string,
    keepLast: number,
    anchor: { goal?: string; done?: string; next?: string },
  ): {
    summary: string;
    flushedCount: number;
    remaining?: number;
    found?: boolean;
    anchorKey: string;
  };
}

/** ContextOS: agent-managed slot store — pin/unpin facts that survive compaction. */
export interface SessionSlotStore {
  getSlots(sessionId: string): Array<{ key: string; text: string; updatedAt?: number }>;
  setSlot(sessionId: string, key: string, text: string): void;
  removeSlot(sessionId: string, key: string): void;
  /** Serialize this session's slots into the fixed [SLOTS] injection segment. */
  serialize(sessionId: string): string;
}

/** ContextOS: archived-fragment store — retrieve/include/purge compacted history. */
export interface SessionFragmentStore {
  retrieveFragments(
    query: string,
    maxResults: number,
  ): Array<{ id: string; content: string; metadata?: Record<string, unknown> }>;
  includeFragment(sessionId: string, fragmentId: string): { ok: boolean; message: string };
  purgeSessionFragments(sessionId: string): number;
  sessionStats(sessionId: string): { messageCount: number; slotKeys: string[]; fragmentCount: number };
}

export interface SessionToolContext {
  agentId: string;
  chatSessionRepo: SessionRepo;
  /** 可选。提供后，agent 可用 `operation: "compact"` 主动折叠旧历史，替换为锚点摘要。 */
  compactor?: SessionCompactor;
  /** ContextOS 可选。提供后，agent 可用 pin/unpin 钉住固定槽位、status 查看占用。 */
  slotStore?: SessionSlotStore;
  /** ContextOS 可选。提供后，agent 可用 retrieve/include/purge 管理已归档片段。 */
  fragmentStore?: SessionFragmentStore;
  /** 可选。提供后，rename 会把聊天（cs_*）会话标题同步写入 Sqlite 并广播到 UI（由 org-manager 注入）。 */
  titleUpdater?: (sessionId: string, title: string) => void;
  /** 可选。返回 agent 当前绑定的聊天会话 id（cs_*）——用于把当前 memory session 的改名映射到 UI 可见的聊天标题。 */
  currentDbSessionId?: () => string | null;
  /** 可选。返回 agent 当前内存会话 id（sess_ 或 task_ 前缀）——让 status 不传 session_id 时默认作用于「当前会话」。 */
  currentMemorySessionId?: () => string | null;
  /**
   * 可选。把聊天会话 id（cs_*）解析为 agent 绑定过的内存会话 id（sess_ 或 task_ 前缀）。
   * 有了它，session 工具才同时接受两种 id 空间 —— agent 才能自证「我在哪条会话」，
   * 而不是拿 cs_* 去 MemoryStore 里查、拿到一个误导性的 not_found。
   */
  resolveMemorySessionByDbSessionId?: (dbSessionId: string) => string | null;
  /** 可选。磁盘上的会话文件总数 —— 用于如实报告 total（内存只热加载最近 N 个）。 */
  diskSessionCount?: () => number;
}

/**
 * Memory-backed SessionRepo — adapts MemoryStore conversation sessions to the
 * SessionRepo contract used by createSessionTool.
 *
 * WHY: the session tool must operate on the SAME session space the agent's
 * context is actually built from (MemoryStore `sess_*` / `task_*` / `a2a_*`),
 * NOT the Sqlite `cs_*` UI space. Mixing the two broke ContextOS:
 *   - checkOwnership used Sqlite (cs_* always "exists" → permission passed)
 *   - slot/compact/fragment operations used MemoryStore (no cs_* session →
 *     silent no-op returning ok but persisting nothing)
 * This adapter makes list/get/status/compact/pin/fragment ALL read/write the
 * same MemoryStore session records the agent truly manages.
 */
export function createMemorySessionRepo(mem: IMemoryStore): SessionRepo {
  return {
    listSessionsPaginated(agentId: string, opts: {
      since?: string; until?: string; userId?: string; page?: number; pageSize?: number;
    }) {
      const sessions = mem.listSessions(agentId)
        .filter((s) => {
          if (opts.since && s.lastActivityAt && s.lastActivityAt < opts.since) return false;
          if (opts.until && s.lastActivityAt && s.lastActivityAt > opts.until) return false;
          return true;
        })
        .sort((a, b) =>
          (b.lastActivityAt ?? '').localeCompare(a.lastActivityAt ?? ''),
        );
      const page = Math.max(1, Math.trunc(Number(opts.page) || 1) || 1);
      const pageSize = Math.min(Math.max(1, Math.trunc(Number(opts.pageSize) || 20) || 20), 50);
      const total = sessions.length;
      const slice = sessions.slice((page - 1) * pageSize, page * pageSize);
      return {
        sessions: slice.map((s) => ({
          id: s.id,
          agentId: s.agentId,
          userId: null,
          title: null,
          isMain: false,
          createdAt: s.startedAt,
          lastMessageAt: s.lastActivityAt,
        })),
        total,
        page,
        pageSize,
        hasMore: page * pageSize < total,
      };
    },
    getSession(sessionId: string): SessionRow | null | undefined {
      const s = mem.getSession(sessionId);
      if (!s) return null;
      return {
        id: s.id,
        agentId: s.agentId,
        userId: null,
        title: null,
        isMain: false,
        createdAt: s.startedAt,
        lastMessageAt: s.lastActivityAt,
      };
    },
    listMessagesPaginated(sessionId: string, opts: {
      since?: string; until?: string; page?: number; pageSize?: number;
    }) {
      const s = mem.getSession(sessionId);
      // LLMMessage has no createdAt — memory sessions store messages verbatim,
      // so since/until time filtering is not applicable at message level.
      const all = s?.messages ?? [];
      const page = Math.max(1, Math.trunc(Number(opts.page) || 1) || 1);
      const pageSize = Math.min(Math.max(1, Math.trunc(Number(opts.pageSize) || 50) || 50), 100);
      const total = all.length;
      const slice = all.slice((page - 1) * pageSize, page * pageSize);
      return {
        messages: slice.map((m) => ({
          id: '',
          sessionId,
          agentId: s?.agentId,
          role: m.role ?? null,
          content: getTextContent(m.content),
        })),
        total,
        page,
        pageSize,
        hasMore: page * pageSize < total,
      };
    },
    countMessagesByAgent(sessionId: string, agentId: string): number {
      const s = mem.getSession(sessionId);
      if (!s) return 0;
      // 会话归属该 agent 即视为"参与"（memory session 都是单主会话）
      return s.agentId === agentId ? s.messages.length : 0;
    },
    updateTitle(sessionId: string, title: string) {
      if (mem.renameSession) mem.renameSession(sessionId, title);
      return { id: sessionId, title };
    },
  };
}

const OP_ALIASES = new Set([
  'list', 'get', 'compact',
  'pin', 'unpin', 'include', 'retrieve', 'purge', 'status',
  'rename', 'title', 'update_title', 'rename_title', 'set_title',
]);

export function normalizeSessionArgs(args: Record<string, unknown>): {
  operation: 'list' | 'get' | 'compact' | 'pin' | 'unpin' | 'include' | 'retrieve' | 'purge' | 'status' | 'rename';
  sessionId: string | undefined;
  since: string | undefined;
  until: string | undefined;
  userId: string | undefined;
  page: number | undefined;
  pageSize: number | undefined;
  keepLast: number | undefined;
  key: string | undefined;
  content: string | undefined;
  query: string | undefined;
  maxResults: number | undefined;
  fragmentId: string | undefined;
  goal: string | undefined;
  done: string | undefined;
  next: string | undefined;
  title: string | undefined;
} {
  const sessionId = (args.session_id ?? args.sessionId ?? args.id) as string | undefined;
  let since = (args.since ?? args.after) as string | undefined;
  const until = (args.until ?? args.before) as string | undefined;
  const userId = (args.user_id ?? args.userId) as string | undefined;
  const page = (args.page ?? args.page_no) as number | undefined;
  const pageSize = (args.page_size ?? args.pageSize ?? args.limit) as number | undefined;
  const keepLast = (args.keep_last ?? args.keepLast ?? args.keep) as number | undefined;
  const key = (args.key ?? args.slot_key ?? args.slotKey) as string | undefined;
  const content = (args.content ?? args.value ?? args.text) as string | undefined;
  const query = (args.query ?? args.q ?? args.search) as string | undefined;
  const maxResults = (args.max_results ?? args.maxResults ?? args.limit) as number | undefined;
  const fragmentId = (args.fragment_id ?? args.fragmentId) as string | undefined;
  const goal = (args.goal ?? args.anchor_goal) as string | undefined;
  const done = (args.done ?? args.anchor_done) as string | undefined;
  const next = (args.next ?? args.anchor_next) as string | undefined;
  const title = (args.title ?? args.new_title ?? args.name) as string | undefined;

  let explicit = (args.operation ?? args.op ?? args.action ?? args.mode) as string | undefined;
  if (typeof explicit === 'string') explicit = explicit.trim().toLowerCase();
  if (typeof args.type === 'string' && OP_ALIASES.has(args.type.trim().toLowerCase())) {
    explicit = args.type.trim().toLowerCase();
  }
  if (explicit?.startsWith('list')) explicit = 'list';
  else if (explicit?.startsWith('get')) explicit = 'get';
  else if (explicit?.startsWith('compact')) explicit = 'compact';
  else if (explicit?.startsWith('pin')) explicit = 'pin';
  else if (explicit?.startsWith('unpin')) explicit = 'unpin';
  else if (explicit?.startsWith('include')) explicit = 'include';
  else if (explicit?.startsWith('retrieve')) explicit = 'retrieve';
  else if (explicit?.startsWith('purge')) explicit = 'purge';
  else if (explicit?.startsWith('status')) explicit = 'status';
  else if (explicit?.startsWith('rename')) explicit = 'rename';
  else if (explicit === 'title' || explicit?.startsWith('set_title') || explicit?.startsWith('update_title')) explicit = 'rename';
  if (explicit && !OP_ALIASES.has(explicit)) explicit = undefined;

  // Free-form query like "get cs-1"
  if (typeof since === 'string') {
    const lower = since.trim().toLowerCase();
    if (!explicit && (lower === 'get' || lower.startsWith('get '))) {
      explicit = 'get';
      const id = since.trim().slice(3).trim();
      if (id && !sessionId) since = undefined;
    }
  }

  let operation: 'list' | 'get' | 'compact' | 'pin' | 'unpin' | 'include' | 'retrieve' | 'purge' | 'status' | 'rename';
  if (explicit === 'get') operation = 'get';
  else if (explicit === 'list') operation = 'list';
  else if (explicit === 'compact') operation = 'compact';
  else if (explicit === 'pin') operation = 'pin';
  else if (explicit === 'unpin') operation = 'unpin';
  else if (explicit === 'include') operation = 'include';
  else if (explicit === 'retrieve') operation = 'retrieve';
  else if (explicit === 'purge') operation = 'purge';
  else if (explicit === 'status') operation = 'status';
  else if (explicit === 'rename') operation = 'rename';
  else if (sessionId) operation = 'get';
  else operation = 'list';

  return {
    operation, sessionId, since, until, userId, page, pageSize, keepLast,
    key, content, query, maxResults, fragmentId, goal, done, next, title,
  };
}

function iso(v: string | Date | undefined): string | undefined {
  if (!v) return undefined;
  return v instanceof Date ? v.toISOString() : String(v);
}

/** Ownership: agent may write/manage a session only if it owns it (agentId matches). */
function checkOwnership(
  repo: SessionRepo,
  sessionId: string,
  agentId: string,
): { session?: SessionRow; error?: string } {
  const session = repo.getSession(sessionId);
  if (!session) return { error: JSON.stringify({ status: 'not_found', message: `No session with id ${sessionId}.` }) };
  if (session.agentId !== agentId) {
    return { error: JSON.stringify({ status: 'forbidden', message: `Not authorized to manage session ${sessionId}.` }) };
  }
  return { session };
}

export function createSessionTool(ctx: SessionToolContext): AgentToolHandler {
  const repo = ctx.chatSessionRepo;

  async function doList(args: Record<string, unknown>): Promise<string> {
    const n = normalizeSessionArgs(args);
    const page = Math.max(1, Math.trunc(Number(n.page) || 1) || 1);
    const pageSize = Math.min(Math.max(1, Math.trunc(Number(n.pageSize) || 20) || 20), 50);
    const res = repo.listSessionsPaginated(ctx.agentId, {
      since: n.since,
      until: n.until,
      userId: n.userId,
      page,
      pageSize,
    });
    const currentMemorySessionId = ctx.currentMemorySessionId?.() ?? null;
    const currentChatSessionId = ctx.currentDbSessionId?.() ?? null;
    const totalOnDisk = ctx.diskSessionCount?.();
    return JSON.stringify({
      status: 'ok',
      sessions: res.sessions.map(s => ({
        id: s.id,
        agentId: s.agentId,
        userId: s.userId ?? null,
        title: s.title ?? null,
        isMain: !!s.isMain,
        createdAt: iso(s.createdAt as string | Date),
        lastMessageAt: iso(s.lastMessageAt as string | Date),
        isCurrent: !!currentMemorySessionId && s.id === currentMemorySessionId,
      })),
      current: {
        memorySessionId: currentMemorySessionId,
        chatSessionId: currentChatSessionId,
      },
      total: res.total,
      ...(typeof totalOnDisk === 'number' ? { totalOnDisk } : {}),
      page: res.page,
      pageSize: res.pageSize,
      hasMore: res.hasMore,
      ...(typeof totalOnDisk === 'number' && totalOnDisk > res.total
        ? {
            note: `total counts only the ${res.total} session(s) warm in memory; ${totalOnDisk} exist on disk. ` +
              `An older session is still readable via { "operation": "get", "session_id": "<id>" } when you know its id.`,
          }
        : {}),
    });
  }

  async function doGet(args: Record<string, unknown>, chatSessionId?: string): Promise<string> {
    const n = normalizeSessionArgs(args);
    if (!n.sessionId) {
      return JSON.stringify({
        status: 'error',
        message: 'Getting a session needs session_id. Example: { "operation": "get", "session_id": "sess_..." }. List first with { "operation": "list" }.',
      });
    }
    const session = repo.getSession(n.sessionId);
    if (!session) {
      return JSON.stringify({ status: 'not_found', message: `No session with id ${n.sessionId}.` });
    }
    // 权限：归属 或 参与（本 agent 在该 session 发过消息）
    const isOwner = session.agentId === ctx.agentId;
    if (!isOwner) {
      const participated = repo.countMessagesByAgent(n.sessionId, ctx.agentId) > 0;
      if (!participated) {
        return JSON.stringify({ status: 'forbidden', message: `Not authorized to read session ${n.sessionId}.` });
      }
    }
    const page = Math.max(1, Math.trunc(Number(n.page) || 1) || 1);
    const pageSize = Math.min(Math.max(1, Math.trunc(Number(n.pageSize) || 50) || 50), 100);
    const msgs = repo.listMessagesPaginated(n.sessionId, {
      since: n.since,
      until: n.until,
      page,
      pageSize,
    });
    return JSON.stringify({
      status: 'ok',
      session: {
        id: session.id,
        chatSessionId: chatSessionId ?? null,
        agentId: session.agentId,
        userId: session.userId ?? null,
        title: session.title ?? null,
        isMain: !!session.isMain,
        createdAt: iso(session.createdAt as string | Date),
        lastMessageAt: iso(session.lastMessageAt as string | Date),
      },
      messages: msgs.messages.map(m => ({
        id: m.id,
        role: m.role ?? null,
        content: m.content ?? '',
        createdAt: iso(m.createdAt as string | Date),
      })),
      totalMessages: msgs.total,
      page: msgs.page,
      pageSize: msgs.pageSize,
      hasMore: msgs.hasMore,
    });
  }

  async function doCompact(args: Record<string, unknown>): Promise<string> {
    if (!ctx.compactor) {
      return JSON.stringify({
        status: 'error',
        message: 'Context compaction is not available in this runtime (no compactor injected).',
      });
    }
    const n = normalizeSessionArgs(args);
    if (!n.sessionId) {
      return JSON.stringify({
        status: 'error',
        message: `Compacting needs session_id. Example: { "operation": "compact", "session_id": "sess_...", "keep_last": ${DEFAULT_COMPACT_KEEP_LAST} }.`,
      });
    }
    // 权限：只允许压缩自己拥有的 session（避免 agent 篡改他人上下文）。
    const own = checkOwnership(repo, n.sessionId, ctx.agentId);
    if (own.error) return own.error;
    const keepLast = Math.max(COMPACT_KEEP_LAST_MIN, Math.min(COMPACT_KEEP_LAST_MAX, Math.trunc(Number(n.keepLast) || DEFAULT_COMPACT_KEEP_LAST) || DEFAULT_COMPACT_KEEP_LAST));
    try {
      const hasAnchor = n.goal || n.done || n.next;
      let res: { summary: string; flushedCount: number; remaining?: number; found?: boolean; anchorKey?: string };
      if (hasAnchor && ctx.compactor.compactWithAnchor) {
        res = ctx.compactor.compactWithAnchor(n.sessionId, keepLast, {
          goal: n.goal, done: n.done, next: n.next,
        });
      } else {
        res = ctx.compactor.compactOnDemand(n.sessionId, keepLast);
      }
      // Distinguish a genuine failure from a valid no-op. The 2026-09-30 刘利
      // P0 report: a session evicted from the in-memory LRU made compact() a
      // silent no-op that still returned status:'ok', so the agent could not
      // tell "nothing to compact" from "compaction failed" and never recovered.
      if (res.found === false) {
        return JSON.stringify({
          status: 'error',
          message: `No session with id "${n.sessionId}" (not in memory nor on disk). Use session_list to find the correct id.`,
        });
      }
      const noop = res.flushedCount === 0;

      // A real compaction rewrote history, so the agent's earlier file reads may
      // no longer be in its context. `file_read` dedupe answers "unchanged, you
      // already have it" — which becomes a LIE the moment history is rewritten.
      // Reset it here, at the one place that knows compaction actually happened.
      if (!noop) resetAllFileReadCaches();

      return JSON.stringify({
        status: 'ok',
        flushedCount: res.flushedCount,
        remaining: res.remaining ?? 0,
        summary: res.summary,
        ...(res.anchorKey ? { anchored_as: res.anchorKey } : {}),
        ...(noop
          ? {
              skipped: true,
              reason: 'within_keep_last',
              note: `Nothing to compact: the session holds <= keep_last (${keepLast}) messages, so no history was dropped and no token was freed. This is a valid no-op, not a failure.`,
            }
          : {
              note: hasAnchor
                ? 'As a pinch: pin a goal/done/next anchor keeps your position alive even after compaction. Use session_pin to persist them as durable slots.'
                : 'Earlier messages were compacted into the anchor summary above. Continue the work from this point; you do NOT need to re-read the flushed messages.',
            }),
      });
    } catch (err) {
      log.error('session compact failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doPin(args: Record<string, unknown>): Promise<string> {
    if (!ctx.slotStore) {
      return JSON.stringify({ status: 'error', message: 'Slot pinning is not available in this runtime (no slotStore injected).' });
    }
    const n = normalizeSessionArgs(args);
    if (!n.sessionId || !n.key || n.content === undefined) {
      return JSON.stringify({
        status: 'error',
        message: 'Pinning needs session_id, key, content. Example: { "operation": "pin", "session_id": "sess_...", "key": "goal", "content": "..." }.',
      });
    }
    const own = checkOwnership(repo, n.sessionId, ctx.agentId);
    if (own.error) return own.error;
    try {
      ctx.slotStore.setSlot(n.sessionId, n.key, n.content);
      return JSON.stringify({
        status: 'ok',
        pinned: { [n.key]: n.content.slice(0, 120) + (n.content.length > 120 ? '…' : '') },
        note: `Pinned key "${n.key}" into session ${n.sessionId}. It stays in the fixed [SLOTS] segment and is NEVER compacted until you unpin it.`,
      });
    } catch (err) {
      log.error('session pin failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doUnpin(args: Record<string, unknown>): Promise<string> {
    if (!ctx.slotStore) {
      return JSON.stringify({ status: 'error', message: 'Slot pinning is not available in this runtime (no slotStore injected).' });
    }
    const n = normalizeSessionArgs(args);
    if (!n.sessionId || !n.key) {
      return JSON.stringify({ status: 'error', message: 'Unpinning needs session_id and key.' });
    }
    const own = checkOwnership(repo, n.sessionId, ctx.agentId);
    if (own.error) return own.error;
    try {
      ctx.slotStore.removeSlot(n.sessionId, n.key);
      return JSON.stringify({ status: 'ok', unpinned: n.key, note: `Removed pinned key "${n.key}".` });
    } catch (err) {
      log.error('session unpin failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doRetrieve(args: Record<string, unknown>): Promise<string> {
    if (!ctx.fragmentStore) {
      return JSON.stringify({ status: 'error', message: 'Fragment retrieval is not available in this runtime (no fragmentStore injected).' });
    }
    const n = normalizeSessionArgs(args);
    const query = String(n.query ?? '');
    const maxResults = Math.max(1, Math.min(20, Math.trunc(Number(n.maxResults) || 5) || 5));
    try {
      const hits = ctx.fragmentStore.retrieveFragments(query, maxResults);
      if (!hits.length) {
        return JSON.stringify({
          status: 'ok',
          hits: [],
          note: query
            ? `No archived fragments matched "${query}". Compaction archives raw history recoverable via session_retrieve.`
            : 'No archived fragments. Compaction archives raw history recoverable via session_retrieve.',
        });
      }
      return JSON.stringify({
        status: 'ok',
        hits: hits.map((h) => ({ id: h.id, content: h.content.slice(0, 2000) })),
        note: 'Return the fragment_id to session_include to reinject it into context.',
      });
    } catch (err) {
      log.error('session retrieve failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doInclude(args: Record<string, unknown>): Promise<string> {
    if (!ctx.fragmentStore) {
      return JSON.stringify({ status: 'error', message: 'Fragment inclusion is not available in this runtime (no fragmentStore injected).' });
    }
    const n = normalizeSessionArgs(args);
    if (!n.sessionId || !n.fragmentId) {
      return JSON.stringify({ status: 'error', message: 'Including needs session_id and fragment_id (from session_retrieve).' });
    }
    const own = checkOwnership(repo, n.sessionId, ctx.agentId);
    if (own.error) return own.error;
    try {
      const res = ctx.fragmentStore.includeFragment(n.sessionId, n.fragmentId);
      return JSON.stringify(res.ok ? { status: 'ok', ...res } : { status: 'error', ...res });
    } catch (err) {
      log.error('session include failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doPurge(args: Record<string, unknown>): Promise<string> {
    if (!ctx.fragmentStore) {
      return JSON.stringify({ status: 'error', message: 'Fragment purge is not available in this runtime (no fragmentStore injected).' });
    }
    const n = normalizeSessionArgs(args);
    if (!n.sessionId) {
      return JSON.stringify({ status: 'error', message: 'Purging needs session_id.' });
    }
    const own = checkOwnership(repo, n.sessionId, ctx.agentId);
    if (own.error) return own.error;
    try {
      const removed = ctx.fragmentStore.purgeSessionFragments(n.sessionId);
      return JSON.stringify({
        status: 'ok',
        purgedFragments: removed,
        note: `Removed ${removed} archived fragment(s) for session ${n.sessionId}. These are permanently deleted (compact no longer archived if purged).`,
      });
    } catch (err) {
      log.error('session purge failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doStatus(args: Record<string, unknown>, chatSessionId?: string): Promise<string> {
    const n = normalizeSessionArgs(args);
    // No session_id → default to the CURRENT session, so "which session am I in?"
    // is answerable without knowing the sess_*/cs_* id-space split.
    let sessionId = n.sessionId;
    if (!sessionId) {
      sessionId = ctx.currentMemorySessionId?.() ?? undefined;
      if (!sessionId) {
        return JSON.stringify({ status: 'error', message: 'Status needs session_id (or an active session to default to).' });
      }
      chatSessionId = chatSessionId ?? ctx.currentDbSessionId?.() ?? undefined;
    }
    // status is read-only: ownership OR participation is enough (same as get).
    const session = repo.getSession(sessionId);
    if (!session) {
      return JSON.stringify({ status: 'not_found', message: `No session with id ${sessionId}.` });
    }
    const isOwner = session.agentId === ctx.agentId;
    if (!isOwner) {
      const participated = repo.countMessagesByAgent(sessionId, ctx.agentId) > 0;
      if (!participated) {
        return JSON.stringify({ status: 'forbidden', message: `Not authorized to read session ${sessionId}.` });
      }
    }
    try {
      const fragmentStats = ctx.fragmentStore?.sessionStats ? ctx.fragmentStore.sessionStats(sessionId) : undefined;
      const slots = ctx.slotStore?.getSlots ? ctx.slotStore.getSlots(sessionId) : [];
      return JSON.stringify({
        status: 'ok',
        sessionId,
        chatSessionId: chatSessionId ?? null,
        isCurrent: (ctx.currentMemorySessionId?.() ?? null) === sessionId,
        messageCount: fragmentStats?.messageCount ?? repo.listMessagesPaginated(sessionId, { page: 1, pageSize: 1 }).total,
        fragmentCount: fragmentStats?.fragmentCount ?? 0,
        slots: slots.map((s) => s.key),
        note: 'Use session_status to observe your context water level; use session_compact / session_pin to actively manage it.',
      });
    } catch (err) {
      log.error('session status failed', { error: String(err) });
      return JSON.stringify({ status: 'error', message: String(err) });
    }
  }

  async function doRename(args: Record<string, unknown>): Promise<string> {
    const n = normalizeSessionArgs(args);
    const title = String(n.title ?? '').trim();
    if (!title) {
      return JSON.stringify({ status: 'error', message: 'Rename needs a non-empty title (summarize the purpose in a few words).' });
    }
    if (title.length > 120) {
      return JSON.stringify({ status: 'error', message: 'Title too long (max 120 chars).' });
    }
    let sessionId = n.sessionId;
    if (!sessionId) {
      // Documented behaviour: omitting session_id renames the CURRENT session.
      // (Previously this errored out — the description promised something the
      //  implementation never did.)
      const curDb = ctx.currentDbSessionId?.() ?? null;
      if (curDb && curDb.startsWith('cs_') && ctx.titleUpdater) {
        ctx.titleUpdater(curDb, title);
        return JSON.stringify({
          status: 'ok',
          sessionId: ctx.currentMemorySessionId?.() ?? curDb,
          chatSessionId: curDb,
          title,
          note: `Current chat session (${curDb}) title updated — visible in Team chat History.`,
        });
      }
      sessionId = ctx.currentMemorySessionId?.() ?? undefined;
      if (!sessionId) {
        return JSON.stringify({ status: 'error', message: 'Rename needs session_id (or an active session to default to).' });
      }
    }
    // Chat sessions (cs_*) live in the UI/team-chat store — update via the
    // server-side titleUpdater so the title is persisted in Sqlite and the
    // web UI refreshes the History panel immediately.
    if (ctx.titleUpdater && sessionId.startsWith('cs_')) {
      ctx.titleUpdater(sessionId, title);
      return JSON.stringify({
        status: 'ok', sessionId, title,
        note: 'Chat session title updated — visible in Team chat History.',
      });
    }
    // Agent's own memory session: if it is currently bound to a chat (cs_*)
    // session, rename that chat session so the title change reaches the UI.
    if (ctx.titleUpdater && ctx.currentDbSessionId) {
      const dbId = ctx.currentDbSessionId();
      if (dbId && dbId.startsWith('cs_')) {
        ctx.titleUpdater(dbId, title);
        return JSON.stringify({
          status: 'ok', sessionId, chatSessionId: dbId, title,
          note: `Current chat session (${dbId}) title updated — visible in Team chat History.`,
        });
      }
    }
    const own = checkOwnership(repo, sessionId, ctx.agentId);
    if (own.error) return own.error;
    if (typeof repo.updateTitle !== 'function') {
      return JSON.stringify({ status: 'error', message: 'Rename is not supported for this session type.' });
    }
    repo.updateTitle(sessionId, title);
    return JSON.stringify({ status: 'ok', sessionId, title, note: 'Memory session title updated.' });
  }

  return {
    name: 'session',
    description: [
      'Manage your own conversation sessions and context. Your context lives in MemoryStore sessions (ids like sess_* / task_* / a2a_* / hb_*); Team-chat (UI) sessions use cs_* ids.',
      'Both id spaces are accepted: a cs_* id is resolved to the memory session bound to it.',
      'Not sure which session you are in? Call { "operation": "status" } with NO session_id — it reports your CURRENT session (both ids).',
      '',
      'Commands (pick one):',
      '• session_list — list your sessions. Returns { sessions: [{id, ..., isCurrent}], current: {memorySessionId, chatSessionId}, total, totalOnDisk, page, page_size, has_more }. total counts only sessions warm in memory; totalOnDisk is the true on-disk count.',
      '  Example: { "operation": "list", "since": "2026-08-01", "page": 1, "page_size": 20 }',
      '• session_get — get one session + its messages. Args: session_id, since/until, page, page_size. Returns { status, sessionId, messages: [{role, content}], total, page }.',
      '  Example: { "operation": "get", "session_id": "sess_...", "page_size": 50 }',
      '• session_compact — collapse stale history into an anchor summary so future turns stop re-reading it. Atomic: an assistant tool-call and its tool results are NEVER split. Optionally pass goal/done/next to anchor your position. Use when earlier tool results are obsolete or context feels bloated. Returns { status, flushedCount, remaining, summary } — flushedCount = messages paged out (0 means nothing was compacted), remaining = messages kept, summary = a [SYSTEM]-prefixed anchor injected into the session; the raw flushed messages are archived and recoverable via session_retrieve. If the session already fits within keep_last you get flushedCount:0, skipped:true, reason:"within_keep_last" (a valid no-op, NOT a failure); an unknown session_id returns status:"error".',
      `  Args: session_id (required), keep_last (5-200, default ${DEFAULT_COMPACT_KEEP_LAST}), goal/done/next (optional anchors).`,
      `  Example: { "operation": "compact", "session_id": "sess_...", "keep_last": ${DEFAULT_COMPACT_KEEP_LAST} }`,
      '• session_pin — write a durable slot into the fixed [SLOTS] segment: persisted per session and injected every turn, NEVER compacted until you unpin it. Best for your current goal / what is done / what is next (use key=goal/done/next). Returns { status, pinned: {key: content} }.',
      '  Args: session_id, key, content. Example: { "operation": "pin", "session_id": "sess_...", "key": "goal", "content": "..." }',
      '• session_unpin — remove a pinned slot. Args: session_id, key. Returns { status, unpinned: key }.',
      '• session_retrieve — search archived (compacted) history fragments by keyword. Returns { status, hits: [{id: fragment_id, content}] } — note the field is "hits", each element carries the fragment id needed for session_include. Args: session_id, query, max_results.',
      '• session_include — reinject an archived fragment (by fragment_id) back into context. Args: session_id, fragment_id.',
      '• session_purge — permanently delete archived fragments for a session. Args: session_id.',
      '• session_status — read-only snapshot: message count, pinned slot keys, archived fragment count. Returns { status, sessionId, chatSessionId, isCurrent, messageCount, fragmentCount, slots: [key...] }. Args: session_id (optional — defaults to your CURRENT session).',
      '• session_rename — set the title of the CURRENT chat session (or any session you own) to a short summary of its purpose, so it is easy to find again in Team chat History or session_list. Args: session_id (or omit to target your current session), title (≤120 chars).',
      '  MUST call this once, in the same turn, when you handle the FIRST user message of a new Team-chat conversation — you are the one who can name it after understanding the goal (the server fallback only copies the first 60 raw characters). Write a "goal + object" phrase in the user\'s language; do not copy their wording. Call it again only if the topic material changes.',
      '  Example: { "operation": "rename", "title": "修复 Team chat 历史列表不完整 + 新增 session_rename 工具" }',
      '',
      'Permissions: you may list sessions you own; get/status sessions you own OR participated in; compact/pin/unpin/include/purge/rename ONLY sessions you own.',
    ].join('\n'),
    inputSchema: {
      type: 'object',
      properties: {
        operation: {
          type: 'string',
          enum: ['list', 'get', 'compact', 'pin', 'unpin', 'include', 'retrieve', 'purge', 'status', 'rename'],
          description: 'Which session operation to run. Default: list when no session_id, get when session_id present.',
        },
        session_id: { type: 'string', description: 'The session id to operate on (sess_* / task_* / a2a_* / hb_* / cs_* — see session_list).' },
        title: { type: 'string', description: 'For rename: the new session title (≤120 chars), summarizing the user\'s purpose.' },
        since: { type: 'string', description: 'ISO timestamp — filter sessions/messages with timestamp >= since.' },
        until: { type: 'string', description: 'ISO timestamp — filter sessions/messages with timestamp <= until.' },
        page: { type: 'number', description: '1-based page number (default 1).' },
        page_size: { type: 'number', description: 'Page size (list default 20 max 50; get default 50 max 100).' },
        keep_last: { type: 'number', description: `For compact: how many most-recent messages to keep verbatim (default ${DEFAULT_COMPACT_KEEP_LAST}, range ${COMPACT_KEEP_LAST_MIN}-${COMPACT_KEEP_LAST_MAX}).` },
        key: { type: 'string', description: 'For pin/unpin: the slot key (e.g. goal, done, next).' },
        content: { type: 'string', description: 'For pin: the fact/anchor text to pin (> up to 1200 chars).' },
        query: { type: 'string', description: 'For retrieve: keyword(s) to search archived fragments.' },
        max_results: { type: 'number', description: 'For retrieve: max results (default 5, max 20).' },
        fragment_id: { type: 'string', description: 'For include: the fragment id (from retrieve) to reinject.' },
        goal: { type: 'string', description: 'For compact: anchor goal (optional).' },
        done: { type: 'string', description: 'For compact: anchor done-so-far (optional).' },
        next: { type: 'string', description: 'For compact: anchor next-step (optional).' },
      },
      required: [],
    },
    async execute(args: Record<string, unknown>, _onOutput?: unknown): Promise<string> {
      try {
        const n = normalizeSessionArgs(args);
        // Accept BOTH id spaces: a cs_* Team-chat id resolves to the memory
        // session bound to it, so the agent can run any operation by either id.
        // Without this, `get cs_…` returned a misleading not_found and the agent
        // could not answer "which conversation am I in?".
        // (rename excluded — it already has a native cs_* path that edits the
        //  UI-visible chat title.)
        let effectiveArgs = args;
        let chatSessionId: string | undefined;
        if (n.operation !== 'rename' && typeof n.sessionId === 'string' && n.sessionId.startsWith('cs_')) {
          const memoryId = ctx.resolveMemorySessionByDbSessionId?.(n.sessionId) ?? null;
          if (memoryId) {
            chatSessionId = n.sessionId;
            effectiveArgs = { ...args, session_id: memoryId };
            delete effectiveArgs['sessionId'];
            delete effectiveArgs['id'];
          } else if (n.operation === 'get' || n.operation === 'status') {
            return JSON.stringify({
              status: 'not_found',
              chatSessionId: n.sessionId,
              message:
                `Chat session ${n.sessionId} is not bound to one of your memory sessions. ` +
                'cs_* ids are Team-chat (UI) session ids; your own context lives in sess_*/task_* sessions. ' +
                'Use { "operation": "list" } to see your sessions, or { "operation": "status" } with no session_id to inspect your CURRENT session.',
            });
          }
        }
        switch (n.operation) {
          case 'get': return await doGet(effectiveArgs, chatSessionId);
          case 'compact': return await doCompact(effectiveArgs);
          case 'pin': return await doPin(effectiveArgs);
          case 'unpin': return await doUnpin(effectiveArgs);
          case 'retrieve': return await doRetrieve(effectiveArgs);
          case 'include': return await doInclude(effectiveArgs);
          case 'purge': return await doPurge(effectiveArgs);
          case 'status': return await doStatus(effectiveArgs, chatSessionId);
          case 'rename': return await doRename(effectiveArgs);
          default: return await doList(effectiveArgs);
        }
      } catch (err) {
        log.error('session tool failed', { error: String(err) });
        return JSON.stringify({ status: 'error', message: String(err) });
      }
    },
  };
}

```

## packages/core/src/tools/subagent.ts

Source: https://github.com/markus-global/markus/blob/72bf7d72474bcf82734753e620f2d0478e80c8e7/packages/core/src/tools/subagent.ts

SHA-256: `448551a56794e9704130fb6be57189a9609f3d8721b116ef45ef6dfc4ef7afc2`

```
import {
  createLogger,
  type LLMMessage,
  type LLMTool,
  SUBAGENT_TASK_PREVIEW_CHARS,
  SUBAGENT_THINKING_PREVIEW_CHARS,
  SUBAGENT_RESULT_PREVIEW_CHARS,
  SUBAGENT_LOG_ENTRY_CHARS,
  SUBAGENT_ERROR_PREVIEW_CHARS,
  SUBAGENT_MAX_PARALLEL,
  SUBAGENT_MAX_AGGREGATE_ITERATIONS,
  SUBAGENT_MAX_LLM_RETRIES,
  SUBAGENT_RETRY_BASE_MS,
} from '@markus/shared';
import type { AgentToolHandler } from '../agent.js';
import type { LLMRouter } from '../llm/router.js';
import type { ContextEngine } from '../context-engine.js';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { isToolErrorResult } from './result.js';

const log = createLogger('subagent');

const DEFAULT_MAX_SUBAGENT_ITERATIONS = Infinity;

/**
 * Progress callback for subagent execution.
 * Emitted for each significant step so the caller can relay to the frontend.
 */
export type SubagentProgressCallback = (event: {
  type: 'started' | 'tool_start' | 'tool_end' | 'thinking' | 'iteration' | 'completed' | 'error';
  content: string;
  metadata?: Record<string, unknown>;
}) => void;

export interface SubagentContext {
  llmRouter: LLMRouter;
  contextEngine: ContextEngine;
  getTools: () => Map<string, AgentToolHandler>;
  getProvider: () => string | undefined;
  agentId: string;
  offloadLargeResult: (toolName: string, result: string) => string;
  maxToolIterations?: number;
  /** Directory to persist subagent logs (e.g. agent's dataDir) */
  dataDir?: string;
  /** Retrieve the progress callback from the current execution context (e.g. via ALS) */
  getProgressCallback?: () => SubagentProgressCallback | undefined;
}

/**
 * H6 — terminal status of a single subagent loop. A child is NEVER reported as a bare
 * success string: an exhausted budget or a hit iteration cap is surfaced explicitly.
 */
export type SubagentStopStatus = 'completed' | 'budget_exhausted' | 'max_iterations';

/**
 * H6 — structured result of one subagent loop. `output` is guaranteed non-empty so the
 * parent can always distinguish a truncated child from a successful one.
 */
export interface SubagentLoopResult {
  status: SubagentStopStatus;
  /** Never an empty string. When `status !== 'completed'` it carries an explicit
   *  "incomplete" note describing why the child stopped early. */
  output: string;
  /** Tool iterations actually executed by this child. */
  iterations: number;
  /** True when the child was stopped by the shared fan-out ceiling (`aggregateBudget`). */
  aggregateCeilingReached: boolean;
  /**
   * F1 — WHICH budget stopped this child, so a parent can tell "this child ran out of its
   * own rope" from "a sibling drained the shared fan-out breaker":
   *   'own'            → its own `iterationBudget`
   *   'aggregate'      → the shared `aggregateBudget` circuit breaker
   *   'max_iterations' → the legacy hard cap (`maxIterations` / `ctx.maxToolIterations`)
   *   null             → it completed normally
   */
  budgetHit: 'own' | 'aggregate' | 'max_iterations' | null;
  /**
   * F2 — distinct file paths this child changed through a file-writing tool
   * (`file_write` / `file_edit` / `apply_patch`, ignoring `dry_run`). A **best-effort hint, not
   * an audit**: a child can also change files by means that are not counted (shell redirection,
   * scripts, builds). Read a non-empty list as "it wrote these"; read an empty list as "no
   * file-tool write was recorded" — never as "nothing changed".
   */
  filesTouched: string[];
  /**
   * F3 — tool-call accounting. `writes` counts successful file-tool write calls — the same
   * best-effort scope and caveat as {@link filesTouched} (a hint, not proof that nothing else
   * changed).
   */
  toolCalls: { total: number; writes: number };
}

export interface SubagentLoopOptions {
  systemPrompt?: string;
  allowedTools?: string[];
  /** Hard cap on tool iterations for this child (legacy option). */
  maxIterations?: number;
  /**
   * H6: the child's OWN, INDEPENDENT iteration budget. Reaching it stops only this child
   * and yields `status: 'budget_exhausted'` — one child can never drain a sibling's quota.
   */
  iterationBudget?: number;
  /**
   * H6: aggregate circuit breaker shared across one `spawn_subagents` fan-out. It is a
   * breaker only — when exhausted the child stops with `aggregateCeilingReached: true`
   * and `status: 'budget_exhausted'`. Mutated in place so the caller can observe it.
   */
  aggregateBudget?: { remaining: number };
  /** @deprecated Legacy alias for {@link SubagentLoopOptions.aggregateBudget}. */
  sharedBudget?: { remaining: number };
  onProgress?: SubagentProgressCallback;
}

const isErrorResult = isToolErrorResult;

const BLOCKED_TOOLS = new Set([
  'spawn_subagent', 'spawn_subagents',
  'notify_user', 'request_user_input', 'request_user_approval', 'discover_tools',
  'schedule_wakeup', 'cancel_wakeup',
]);

function buildToolMap(
  parentTools: Map<string, AgentToolHandler>,
  allowedTools?: string[],
): Map<string, AgentToolHandler> {
  const toolMap = new Map<string, AgentToolHandler>();
  if (allowedTools && allowedTools.length > 0) {
    for (const name of allowedTools) {
      const handler = parentTools.get(name);
      if (handler && !BLOCKED_TOOLS.has(name)) {
        toolMap.set(name, handler);
      }
    }
  } else {
    for (const [name, handler] of parentTools) {
      if (!BLOCKED_TOOLS.has(name)) {
        toolMap.set(name, handler);
      }
    }
  }
  return toolMap;
}

function stringPaths(v: unknown): string[] {
  return typeof v === 'string' && v.length > 0 ? [v] : [];
}

/**
 * F2 — the tools that mutate files, and how to read the path(s) they touched out of their
 * arguments. Schema-copied from `tools/file.ts` (`file_write` / `file_edit`) and
 * `tools/patch.ts` (`apply_patch`). An `apply_patch` with `dry_run: true` writes nothing and
 * is therefore ignored.
 */
const FILE_WRITE_TOOLS: Record<string, (args: Record<string, unknown>) => string[]> = {
  file_write: (args) => stringPaths(args['path'] ?? args['file'] ?? args['file_path'] ?? args['filePath']),
  file_edit: (args) => stringPaths(args['path'] ?? args['file'] ?? args['file_path'] ?? args['filePath']),
  apply_patch: (args) => {
    if (args['dry_run'] === true) return [];
    const patches = args['patches'];
    if (!Array.isArray(patches)) return [];
    return patches.flatMap((p) => stringPaths((p as Record<string, unknown> | null)?.['file']));
  },
};

/**
 * Strip `<think>...</think>` blocks leaked by reasoning models (DeepSeek, Qwen, etc.).
 * These are internal chain-of-thought and should not appear in tool results or final output.
 */
function stripThinkTags(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/g, '')
    .replace(/^\s*\n/, '');
}

const RETRYABLE_STATUS_CODES = new Set([429, 500, 502, 503, 504]);

function isRetryableError(err: unknown): boolean {
  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();
  if (msg.includes('rate limit') || msg.includes('429') || msg.includes('too many requests')) return true;
  if (msg.includes('500') || msg.includes('502') || msg.includes('503') || msg.includes('504')) return true;
  if (msg.includes('server_error') || msg.includes('internal server error')) return true;
  if (msg.includes('timeout') || msg.includes('econnreset') || msg.includes('fetch failed')) return true;
  for (const code of RETRYABLE_STATUS_CODES) {
    if (msg.includes(`${code}`)) return true;
  }
  return false;
}

async function llmCallWithRetry<T>(fn: () => Promise<T>, label: string): Promise<T> {
  let lastErr: unknown;
  for (let attempt = 0; attempt <= SUBAGENT_MAX_LLM_RETRIES; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (!isRetryableError(err) || attempt >= SUBAGENT_MAX_LLM_RETRIES) {
        throw err;
      }
      const delay = SUBAGENT_RETRY_BASE_MS * Math.pow(2, attempt);
      log.warn(`${label}: retryable error, attempt ${attempt + 1}/${SUBAGENT_MAX_LLM_RETRIES + 1}`, {
        error: String(err).slice(0, SUBAGENT_ERROR_PREVIEW_CHARS),
        delay,
      });
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
  throw lastErr;
}

interface SubagentLogEntry {
  ts: string;
  role: string;
  content?: string;
  toolCalls?: Array<{ name: string; arguments?: unknown }>;
  toolCallId?: string;
  toolName?: string;
}

function persistSubagentLog(dataDir: string, subagentId: string, entries: SubagentLogEntry[]): string | undefined {
  try {
    const logsDir = join(dataDir, 'subagent-logs');
    if (!existsSync(logsDir)) {
      mkdirSync(logsDir, { recursive: true });
    }
    const filePath = join(logsDir, `${subagentId}.jsonl`);
    const content = entries.map(e => JSON.stringify(e)).join('\n') + '\n';
    writeFileSync(filePath, content);
    log.debug('Subagent log persisted', { path: filePath, entries: entries.length });
    return filePath;
  } catch (err) {
    log.warn('Failed to persist subagent log', { error: String(err) });
    return undefined;
  }
}

/**
 * Run a lightweight subagent loop: independent messages[], parent's tools, sync return.
 *
 * Claude Code subagent pattern (learn-claude-code s04):
 * - Fresh messages[] per child — clean context, no pollution of parent conversation
 * - Inherits parent tools — no separate registration needed
 * - Synchronous return — result flows back as tool_result to parent
 *
 * Exported so other modules (e.g. task system) can invoke subagent execution
 * without going through the tool dispatch path.
 *
 * H6: returns a structured {@link SubagentLoopResult}. The child's iteration budget is
 * INDEPENDENT (`iterationBudget`); `aggregateBudget` is only an overall circuit breaker.
 * When either is hit the result is explicit (`status: 'budget_exhausted'`) and `output`
 * is never empty — a parent can always tell a truncated child from a successful one.
 */
export async function runSubagentLoop(
  ctx: SubagentContext,
  task: string,
  opts?: SubagentLoopOptions,
): Promise<SubagentLoopResult> {
  const hardCap = ctx.maxToolIterations ?? DEFAULT_MAX_SUBAGENT_ITERATIONS;
  const maxIterations = Math.min(opts?.maxIterations ?? hardCap, hardCap);
  /** This child's own iteration budget — never shared with siblings. */
  const iterationBudget = opts?.iterationBudget;
  /** Shared fan-out circuit breaker (legacy `sharedBudget` treated as the same thing). */
  const aggregateBudget = opts?.aggregateBudget ?? opts?.sharedBudget;
  const onProgress = opts?.onProgress;
  let status: SubagentStopStatus = 'completed';
  let aggregateCeilingReached = false;
  /** F1: which budget stopped this child. */
  let budgetHit: SubagentLoopResult['budgetHit'] = null;
  /** F2/F3: what the child actually touched, and how much of its work was writes. */
  const filesTouched = new Set<string>();
  let toolCallCount = 0;
  let writeCallCount = 0;

  const subagentId = `sub_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const logEntries: SubagentLogEntry[] = [];

  const parentTools = ctx.getTools();
  const provider = ctx.getProvider();
  // Cold-start fix: same bounded, never-throwing readiness wait as the main turn
  // preflight. A subagent spawned on the very first turn must pack against real
  // Hub values rather than the fallback window. O(1) no-op once warm.
  await ctx.llmRouter.ensureMarkusCatalogLoaded?.({ timeoutMs: 3000 });
  const contextWindow = ctx.llmRouter.getModelContextWindow(provider);

  const toolMap = buildToolMap(parentTools, opts?.allowedTools);

  const llmTools: LLMTool[] = [...toolMap.values()].map(t => ({
    name: t.name,
    description: t.description,
    inputSchema: t.inputSchema,
  }));

  const systemContent = opts?.systemPrompt
    ?? 'You are a focused subagent spawned to handle a specific subtask. Your goal is to complete the assigned work thoroughly and return a clear, concise result.\n\nGuidelines:\n- Work with the information provided — do not ask follow-up questions\n- Stay within the scope of the assigned subtask — do not expand beyond what was asked\n- If you encounter an error, try an alternative approach before reporting failure\n- Verify your results before returning — check for correctness, completeness, and edge cases\n- Return structured output: what you did, what you found, and any caveats or limitations\n- Never include secrets, credentials, or system internals in your output';

  let messages: LLMMessage[] = [
    { role: 'system', content: systemContent },
    { role: 'user', content: task },
  ];

  logEntries.push({ ts: new Date().toISOString(), role: 'system', content: systemContent });
  logEntries.push({ ts: new Date().toISOString(), role: 'user', content: task });

  log.info('Subagent started', {
    parentAgent: ctx.agentId,
    subagentId,
    taskLength: task.length,
    toolCount: toolMap.size,
    maxIterations,
    iterationBudget,
  });

  onProgress?.({
    type: 'started',
    content: `Subagent ${subagentId} started`,
    metadata: { subagentId, toolCount: toolMap.size, taskPreview: task.slice(0, SUBAGENT_TASK_PREVIEW_CHARS) },
  });

  let response = await llmCallWithRetry(
    () => ctx.llmRouter.chat({
      messages,
      tools: llmTools.length > 0 ? llmTools : undefined,
      metadata: { agentId: ctx.agentId, sessionId: subagentId },
    }, provider),
    `subagent-${subagentId}-init`,
  );

  let iterations = 0;

  while (
    (response.finishReason === 'tool_use' && response.toolCalls?.length) ||
    response.finishReason === 'max_tokens'
  ) {
    // H6: aggregate circuit breaker — shared across the fan-out, checked first. Its
    // stop is explicit so the parent never mistakes it for a clean completion.
    if (aggregateBudget) {
      if (aggregateBudget.remaining <= 0) {
        aggregateCeilingReached = true;
        status = 'budget_exhausted';
        budgetHit = 'aggregate';
        log.warn('Subagent stopped: aggregate budget ceiling reached', {
          parentAgent: ctx.agentId, subagentId, iterations,
        });
        onProgress?.({ type: 'error', content: 'Aggregate subagent budget ceiling reached' });
        break;
      }
      aggregateBudget.remaining--;
    }

    // H6: the child's OWN iteration budget. Independent per child — reaching it stops
    // only this child and is reported as `budget_exhausted`, never as a silent success.
    if (iterationBudget !== undefined && iterations >= iterationBudget) {
      status = 'budget_exhausted';
      budgetHit = 'own';
      log.warn('Subagent stopped: own iteration budget exhausted', {
        parentAgent: ctx.agentId, subagentId, iterations, iterationBudget,
      });
      onProgress?.({ type: 'error', content: `Subagent iteration budget exhausted (${iterationBudget})` });
      break;
    }

    // Hard iteration cap (legacy `maxIterations` / ctx.maxToolIterations).
    if (iterations >= maxIterations) {
      status = 'max_iterations';
      budgetHit = 'max_iterations';
      log.warn('Subagent hit max iterations', { parentAgent: ctx.agentId, subagentId, iterations, maxIterations });
      onProgress?.({ type: 'error', content: `Subagent hit max iterations (${maxIterations})` });
      break;
    }

    iterations++;

    onProgress?.({
      type: 'iteration',
      content: `Iteration ${iterations}/${maxIterations}`,
      metadata: { iteration: iterations, finishReason: response.finishReason },
    });

    if (response.finishReason === 'max_tokens' && !response.toolCalls?.length) {
      messages.push({ role: 'assistant', content: response.content, reasoningContent: response.reasoningContent });
      logEntries.push({ ts: new Date().toISOString(), role: 'assistant', content: response.content });
      messages.push({
        role: 'user',
        content: '[Continue from where you left off. Do not repeat what you already said.]',
      });
    } else {
      messages.push({
        role: 'assistant',
        content: response.content,
        toolCalls: response.toolCalls,
        reasoningContent: response.reasoningContent,
      });
      logEntries.push({
        ts: new Date().toISOString(),
        role: 'assistant',
        content: response.content,
        toolCalls: response.toolCalls?.map(tc => ({ name: tc.name, arguments: tc.arguments })),
      });

      if (response.content) {
        onProgress?.({
          type: 'thinking',
          content: stripThinkTags(response.content).slice(0, SUBAGENT_THINKING_PREVIEW_CHARS),
        });
      }

      for (const tc of response.toolCalls!) {
        const handler = toolMap.get(tc.name);
        let result: string;

        onProgress?.({
          type: 'tool_start',
          content: tc.name,
          metadata: { toolCallId: tc.id, arguments: tc.arguments },
        });

        const toolStart = Date.now();
        if (!handler) {
          result = JSON.stringify({ error: `Unknown tool: ${tc.name}` });
        } else {
          try {
            result = await handler.execute(tc.arguments);
            result = ctx.offloadLargeResult(tc.name, result);
          } catch (err) {
            result = `Error: ${String(err)}`;
          }
        }
        const toolDuration = Date.now() - toolStart;

        // F2/F3: count every tool call; record the paths a *successful* file-writing call
        // actually changed. An error result touches nothing, so it is not counted.
        toolCallCount++;
        const writePaths = FILE_WRITE_TOOLS[tc.name];
        if (writePaths && !isErrorResult(result)) {
          const paths = writePaths((tc.arguments ?? {}) as Record<string, unknown>);
          if (paths.length > 0) {
            writeCallCount++;
            for (const p of paths) filesTouched.add(p);
          }
        }

        onProgress?.({
          type: 'tool_end',
          content: tc.name,
          metadata: {
            toolCallId: tc.id,
            durationMs: toolDuration,
            success: !isErrorResult(result),
            resultPreview: result.slice(0, SUBAGENT_RESULT_PREVIEW_CHARS),
          },
        });

        logEntries.push({
          ts: new Date().toISOString(),
          role: 'tool',
          content: result.slice(0, SUBAGENT_LOG_ENTRY_CHARS),
          toolCallId: tc.id,
          toolName: tc.name,
        });

        messages.push({ role: 'tool', content: result, toolCallId: tc.id });
      }
    }

    messages = ctx.contextEngine.shrinkMessages(messages, contextWindow);

    response = await llmCallWithRetry(
      () => ctx.llmRouter.chat({
        messages,
        tools: llmTools.length > 0 ? llmTools : undefined,
        metadata: { agentId: ctx.agentId, sessionId: subagentId },
      }, provider),
      `subagent-${subagentId}-iter${iterations}`,
    );
  }

  const rawResult = response.content;
  let cleanResult = stripThinkTags(rawResult ?? '');

  // H6: make any early stop visible, and GUARANTEE a non-empty result. A parent must
  // never receive an empty string that looks like a successful child.
  if (status !== 'completed') {
    const reason = status === 'max_iterations'
      ? `reached its ${maxIterations}-iteration cap`
      : aggregateCeilingReached
        ? 'shared aggregate iteration budget exhausted'
        : `own iteration budget (${iterationBudget}) exhausted`;
    // F2/F3 (scoped 2026-10-09): this note is a *reminder to verify*, not an audit. A child can
    // change the workspace in ways we cannot enumerate (shell redirection, scripts, builds), so we
    // never assert a negative we cannot prove — we report what we saw and tell the parent to check.
    const wroteHint = filesTouched.size > 0
      ? ` Files written via file tools (may be half-applied): ${[...filesTouched].join(', ')}.`
      : '';
    const note =
      `[INCOMPLETE: subagent stopped early — ${reason} after ${iterations} iteration(s); ` +
      `${toolCallCount} tool call(s), ${writeCallCount} write(s).${wroteHint} ` +
      `Its result is incomplete — verify the workspace before trusting it (a subagent can also ` +
      `change files by means this notice does not track, e.g. shell).]`;
    cleanResult = cleanResult.trim().length > 0
      ? `${cleanResult.trimEnd()}\n\n${note}`
      : note;
  } else if (cleanResult.trim().length === 0) {
    // A genuine `end_turn` with no text still must not serialise to an empty string.
    cleanResult = '[Subagent completed without producing textual output.]';
  }

  logEntries.push({
    ts: new Date().toISOString(),
    role: 'assistant',
    content: cleanResult,
  });

  let logPath: string | undefined;
  if (ctx.dataDir) {
    logPath = persistSubagentLog(ctx.dataDir, subagentId, logEntries);
  }

  log.info('Subagent finished', {
    parentAgent: ctx.agentId,
    subagentId,
    status,
    iterations,
    aggregateCeilingReached,
    budgetHit,
    toolCalls: toolCallCount,
    writes: writeCallCount,
    filesTouched: [...filesTouched],
    resultLength: cleanResult.length,
    logPath,
  });

  onProgress?.({
    type: status === 'completed' ? 'completed' : 'error',
    content: status === 'completed'
      ? `Subagent completed in ${iterations} iterations`
      : `Subagent stopped early (${status}) after ${iterations} iterations`,
    metadata: { subagentId, status, iterations, aggregateCeilingReached, budgetHit, filesTouched: [...filesTouched], logPath, resultLength: cleanResult.length },
  });

  return {
    status,
    output: cleanResult,
    iterations,
    aggregateCeilingReached,
    budgetHit,
    filesTouched: [...filesTouched],
    toolCalls: { total: toolCallCount, writes: writeCallCount },
  };
}

/**
 * Creates the spawn_subagent tool (single subagent).
 */
export function createSubagentTool(ctx: SubagentContext): AgentToolHandler {
  return {
    name: 'spawn_subagent',
    description:
      'Spawn a lightweight subagent with a clean, independent context to handle a focused subtask. ' +
      'The subagent inherits your tools but gets its own message history — it will not pollute your conversation. ' +
      'Use this to break down complex tasks: deep code analysis, research, file refactoring, test generation, etc. ' +
      'The subagent runs to completion and returns a STRUCTURED result: `status` ' +
      "('completed' | 'budget_exhausted' | 'max_iterations'), `budgetHit`, `filesTouched`, `toolCalls`. " +
      'A child that stops early is reported as incomplete — never as an empty success — and tells you ' +
      'to verify the workspace; `filesTouched` is a best-effort hint, not an exhaustive audit. ' +
      'For running multiple subagents in parallel, use spawn_subagents instead.',
    inputSchema: {
      type: 'object',
      properties: {
        task: {
          type: 'string',
          description: 'The focused task prompt for the subagent. Be specific about what you want it to do and what result to return.',
        },
        system_prompt: {
          type: 'string',
          description: 'Optional custom system prompt for the subagent. Defaults to a focused task-execution prompt.',
        },
        allowed_tools: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional subset of tool names the subagent can use. If omitted, it inherits all parent tools.',
        },
        max_iterations: {
          type: 'number',
          description: 'Hard cap on tool iterations for THIS child (per child — never shared with siblings). Prefer iteration_budget when you want a clean budget_exhausted status.',
        },
        iteration_budget: {
          type: 'number',
          description: 'Optional per-child iteration budget. When reached, the subagent returns an explicit budget_exhausted status (never an empty success).',
        },
      },
      required: ['task'],
    },

    async execute(args: Record<string, unknown>): Promise<string> {
      const task = args['task'] as string;
      if (!task) {
        return JSON.stringify({ status: 'error', error: 'task is required' });
      }
      try {
        const loop = await runSubagentLoop(ctx, task, {
          systemPrompt: args['system_prompt'] as string | undefined,
          allowedTools: args['allowed_tools'] as string[] | undefined,
          maxIterations: args['max_iterations'] as number | undefined,
          iterationBudget: args['iteration_budget'] as number | undefined,
          onProgress: ctx.getProgressCallback?.(),
        });
        // H6 + F1–F3: surface the child's real terminal status and an auditable schema
        // (status / which budget / what files / how much of the work was writes) verbatim.
        // `result` is never empty.
        return JSON.stringify({
          status: loop.status,
          result: loop.output,
          iterations: loop.iterations,
          aggregateCeilingReached: loop.aggregateCeilingReached,
          budgetHit: loop.budgetHit,
          filesTouched: loop.filesTouched,
          toolCalls: loop.toolCalls,
        });
      } catch (err) {
        log.error('Subagent execution failed', { error: String(err) });
        return JSON.stringify({ status: 'error', error: `Subagent failed: ${String(err)}` });
      }
    },
  };
}

/**
 * Creates the spawn_subagents tool (parallel batch execution).
 *
 * Runs multiple subagent loops concurrently via Promise.allSettled.
 * Each subagent gets an independent context and tool set.
 * All results are collected and returned together.
 *
 * This solves the limitation where the task execution path runs tools
 * sequentially — by accepting multiple tasks in a single tool call,
 * the subagents execute in parallel regardless of the calling path.
 */
export function createParallelSubagentTool(ctx: SubagentContext): AgentToolHandler {
  return {
    name: 'spawn_subagents',
    description:
      'Spawn multiple subagents in PARALLEL, each with an independent context. ' +
      'All subagents run concurrently and their results are collected and returned together. ' +
      'Use this when you have multiple independent subtasks that can be worked on simultaneously: ' +
      'analyzing different files, researching different topics, implementing separate modules, etc. ' +
      'Each subagent gets its own clean message history and inherits your tools. ' +
      'BUDGETS: each child may set its own `iteration_budget`, and that budget is private to the child — ' +
      'one child can never drain a sibling. Separately, the whole fan-out shares an aggregate ceiling of ' +
      `${SUBAGENT_MAX_AGGREGATE_ITERATIONS} iterations; it is a CIRCUIT BREAKER, not a schedule, so a large ` +
      'fan-out can trip it even when every child sets its own budget. A child stopped early reports why via ' +
      "`budgetHit` ('own' | 'aggregate' | 'max_iterations') and `filesTouched`. " +
      'IMPORTANT: Only use for truly independent tasks — subagents cannot communicate with each other.',
    inputSchema: {
      type: 'object',
      properties: {
        tasks: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              id: {
                type: 'string',
                description: 'A short identifier for this subtask (used to label results). E.g. "auth-review", "api-tests".',
              },
              task: {
                type: 'string',
                description: 'The focused task prompt for this subagent.',
              },
              allowed_tools: {
                type: 'array',
                items: { type: 'string' },
                description: 'Optional subset of tools this subagent can use.',
              },
              max_iterations: {
                type: 'number',
                description: 'Optional max iterations for this subagent.',
              },
              iteration_budget: {
                type: 'number',
                description: 'Optional INDEPENDENT iteration budget for this subagent. Reaching it stops only this child and returns an explicit budget_exhausted status.',
              },
            },
            required: ['id', 'task'],
          },
          description: 'Array of subtasks to execute in parallel. Each gets an independent subagent.',
        },
        system_prompt: {
          type: 'string',
          description: 'Optional shared system prompt for all subagents.',
        },
      },
      required: ['tasks'],
    },

    async execute(args: Record<string, unknown>): Promise<string> {
      const onProgress = ctx.getProgressCallback?.();
      const tasks = args['tasks'] as Array<{
        id: string;
        task: string;
        allowed_tools?: string[];
        max_iterations?: number;
        iteration_budget?: number;
      }>;
      if (!tasks || !Array.isArray(tasks) || tasks.length === 0) {
        return JSON.stringify({ status: 'error', error: 'tasks array is required and must not be empty' });
      }

      if (tasks.length > SUBAGENT_MAX_PARALLEL) {
        return JSON.stringify({
          status: 'error',
          error: `Too many parallel subagents (${tasks.length}). Maximum is ${SUBAGENT_MAX_PARALLEL}.`,
        });
      }

      const sharedSystemPrompt = args['system_prompt'] as string | undefined;

      log.info('Spawning parallel subagents', {
        parentAgent: ctx.agentId,
        count: tasks.length,
        taskIds: tasks.map(t => t.id),
      });

      onProgress?.({
        type: 'started',
        content: `Spawning ${tasks.length} parallel subagents`,
        metadata: { taskIds: tasks.map(t => t.id) },
      });

      const startTime = Date.now();

      // H6: the aggregate pool is ONLY an overall circuit breaker — it is NOT the
      // per-child budget. Each child keeps its own INDEPENDENT `iterationBudget`, so no
      // child can silently drain (and thereby starve) a sibling.
      const aggregateBudget = { remaining: SUBAGENT_MAX_AGGREGATE_ITERATIONS };

      const results = await Promise.allSettled(
        tasks.map(async (t) => {
          const perTaskProgress: SubagentProgressCallback | undefined = onProgress
            ? (event) => onProgress({
                ...event,
                content: `[${t.id}] ${event.content}`,
                metadata: { ...event.metadata, parallelTaskId: t.id },
              })
            : undefined;

          const loop = await runSubagentLoop(ctx, t.task, {
            systemPrompt: sharedSystemPrompt,
            allowedTools: t.allowed_tools,
            maxIterations: t.max_iterations,
            iterationBudget: t.iteration_budget,
            onProgress: perTaskProgress,
            aggregateBudget,
          });
          return { id: t.id, loop };
        })
      );

      const budgetExceeded = aggregateBudget.remaining <= 0;

      const output: Array<{
        id: string;
        status: SubagentStopStatus | 'error';
        result: string;
        iterations?: number;
        aggregateCeilingReached?: boolean;
        budgetHit?: 'own' | 'aggregate' | 'max_iterations' | null;
        filesTouched?: string[];
        toolCalls?: { total: number; writes: number };
        error?: string;
      }> = results.map((r, i) => {
        if (r.status === 'fulfilled') {
          const { id, loop } = r.value;
          // H6: `result` is NEVER empty — a child that produced nothing still surfaces
          // an explicit, actionable message instead of an empty success.
          const result = loop.output.trim().length > 0
            ? loop.output
            : `[INCOMPLETE: subagent "${id}" produced no output (status: ${loop.status}).]`;
          return {
            id,
            status: loop.status,
            result,
            iterations: loop.iterations,
            aggregateCeilingReached: loop.aggregateCeilingReached,
            budgetHit: loop.budgetHit,
            filesTouched: loop.filesTouched,
            toolCalls: loop.toolCalls,
          };
        }
        const reason = String(r.reason);
        return {
          id: tasks[i]!.id,
          status: 'error' as const,
          result: `[INCOMPLETE: subagent failed — ${reason}]`,
          error: reason,
        };
      });

      const completed = output.filter(o => o.status === 'completed').length;
      const stoppedEarly = output.filter(o => o.status === 'budget_exhausted' || o.status === 'max_iterations').length;
      const failed = output.filter(o => o.status === 'error').length;
      const durationMs = Date.now() - startTime;

      log.info('Parallel subagents finished', {
        parentAgent: ctx.agentId,
        completed,
        stoppedEarly,
        failed,
        budgetExceeded,
        totalDurationMs: durationMs,
      });

      onProgress?.({
        type: stoppedEarly > 0 || failed > 0 ? 'error' : 'completed',
        content: `${completed}/${tasks.length} subagents completed${stoppedEarly > 0 ? `, ${stoppedEarly} stopped early` : ''} (${durationMs}ms)`,
        metadata: { completed, stoppedEarly, failed, budgetExceeded, durationMs },
      });

      // H6: make a partial fan-out legible at a glance.
      const summaryParts = [`${completed}/${tasks.length} subagents completed`];
      if (stoppedEarly > 0) summaryParts.push(`${stoppedEarly} stopped early (incomplete)`);
      if (failed > 0) summaryParts.push(`${failed} failed`);
      if (budgetExceeded) summaryParts.push('aggregate iteration budget exhausted');

      return JSON.stringify({
        status: 'completed',
        summary: summaryParts.join('; '),
        durationMs,
        budgetExceeded,
        aggregateIterationBudget: SUBAGENT_MAX_AGGREGATE_ITERATIONS,
        results: output,
      });
    },
  };
}

```
