# vibesdk — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## worker/agents/tools/customTools.ts

Source: https://github.com/cloudflare/vibesdk/blob/32c9e429cd2712ea5959a89f65ae434162b89229/worker/agents/tools/customTools.ts

SHA-256: `1859a7327ad2546cd487d0ed556d51b7d7bbd6f6d149417277c1568f894e2886`

```
import type { ToolDefinition } from './types';
import { StructuredLogger } from '../../logger';
import { RenderToolCall } from '../operations/UserConversationProcessor';
import { toolWebSearchDefinition } from './toolkit/web-search';
import { toolFeedbackDefinition } from './toolkit/feedback';
import { createQueueRequestTool } from './toolkit/queue-request';
import { createGetLogsTool } from './toolkit/get-logs';
import { createDeployPreviewTool } from './toolkit/deploy-preview';
import { createDeepDebuggerTool } from "./toolkit/deep-debugger";
import { createRenameProjectTool } from './toolkit/rename-project';
import { createAlterBlueprintTool } from './toolkit/alter-blueprint';
import { createReadFilesTool } from './toolkit/read-files';
import { createExecCommandsTool } from './toolkit/exec-commands';
import { createRunAnalysisTool } from './toolkit/run-analysis';
import { createRegenerateFileTool } from './toolkit/regenerate-file';
import { createGenerateFilesTool } from './toolkit/generate-files';
import { createWaitTool } from './toolkit/wait';
import { createGetRuntimeErrorsTool } from './toolkit/get-runtime-errors';
import { createWaitForGenerationTool } from './toolkit/wait-for-generation';
import { createWaitForDebugTool } from './toolkit/wait-for-debug';
import { createGitTool } from './toolkit/git';
import { ICodingAgent } from '../services/interfaces/ICodingAgent';
import { Message } from '../inferutils/common';
import { ChatCompletionMessageFunctionToolCall } from 'openai/resources';
import { DeepDebuggerSession } from '../operations/DeepDebugger';

export async function executeToolWithDefinition<TArgs, TResult>(
    toolCall: ChatCompletionMessageFunctionToolCall,
    toolDef: ToolDefinition<TArgs, TResult>,
    args: TArgs
): Promise<TResult> {
    await toolDef.onStart?.(toolCall, args);
    const result = await toolDef.implementation(args);
    await toolDef.onComplete?.(toolCall, args, result);
    return result;
}

/**
 * Build all available tools for the agent
 * Add new tools here - they're automatically included in the conversation
 */
export function buildTools(
    agent: ICodingAgent,
    logger: StructuredLogger,
    toolRenderer: RenderToolCall,
    streamCb: (chunk: string) => void,
): ToolDefinition<any, any>[] {
    // NOTE: `get_browser_console_logs` is registered with the ThinkAgent's
    // own tool runtime (see `worker/agents/think/ThinkAgent.ts`); the
    // think behavior bypasses this `buildTools()` path entirely so
    // adding it here would be unreachable.
    return [
        toolWebSearchDefinition,
        toolFeedbackDefinition,
        createQueueRequestTool(agent, logger),
        createGetLogsTool(agent, logger),
        createDeployPreviewTool(agent, logger),
        createWaitForGenerationTool(agent, logger),
        createWaitForDebugTool(agent, logger),
        createRenameProjectTool(agent, logger),
        createAlterBlueprintTool(agent, logger),
        // Git tool (safe version - no reset for user conversations)
        createGitTool(agent, logger, { excludeCommands: ['reset'] }),
        // Deep autonomous debugging assistant tool
        createDeepDebuggerTool(agent, logger, toolRenderer, streamCb),
    ];
}

export function buildDebugTools(session: DeepDebuggerSession, logger: StructuredLogger, toolRenderer?: RenderToolCall): ToolDefinition<any, any>[] {
    const tools = [
        createGetLogsTool(session.agent, logger),
        createGetRuntimeErrorsTool(session.agent, logger),
        createReadFilesTool(session.agent, logger),
        createRunAnalysisTool(session.agent, logger),
        createExecCommandsTool(session.agent, logger),
        createRegenerateFileTool(session.agent, logger),
        createGenerateFilesTool(session.agent, logger),
        createDeployPreviewTool(session.agent, logger),
        createWaitTool(logger),
        createGitTool(session.agent, logger),
    ];
    return withRenderer(tools, toolRenderer);
}

/**
 * Decorate tools with renderer for UI visualization and conversation sync
 */
export function withRenderer(
    tools: ToolDefinition<any, any>[],
    toolRenderer?: RenderToolCall,
    onComplete?: (message: Message) => Promise<void>
): ToolDefinition<any, any>[] {
    if (!toolRenderer) return tools;

    return tools.map(td => {
        const originalOnStart = td.onStart;
        const originalOnComplete = td.onComplete;

        return {
            ...td,
            onStart: async (tc: ChatCompletionMessageFunctionToolCall, args: Record<string, unknown>) => {
                await originalOnStart?.(tc, args);
                if (toolRenderer) {
                    toolRenderer({ name: td.name, status: 'start', args });
                }
            },
            onComplete: async (tc: ChatCompletionMessageFunctionToolCall, args: Record<string, unknown>, result: unknown) => {
                await originalOnComplete?.(tc, args, result);
                if (toolRenderer) {
                    toolRenderer({
                        name: td.name,
                        status: 'success',
                        args,
                        result: typeof result === 'string' ? result : JSON.stringify(result)
                    });
                }
                if (onComplete) {
                    const toolMessage: Message = {
                        role: 'tool',
                        content: typeof result === 'string' ? result : JSON.stringify(result),
                        name: td.name,
                        tool_call_id: tc.id,
                    };
                    await onComplete(toolMessage);
                }
            }
        };
    });
}

```

## worker/agents/tools/toolkit/exec-commands.ts

Source: https://github.com/cloudflare/vibesdk/blob/32c9e429cd2712ea5959a89f65ae434162b89229/worker/agents/tools/toolkit/exec-commands.ts

SHA-256: `fa97b42c646e080520356f806032f11d6812567f3776317f7f01cb8a5a3aa752`

```
import { tool, t } from '../types';
import { StructuredLogger } from '../../../logger';
import { ICodingAgent } from 'worker/agents/services/interfaces/ICodingAgent';
import { ExecuteCommandsResponse } from 'worker/services/sandbox/sandboxTypes';

export type ExecCommandsResult = ExecuteCommandsResponse | { error: string };

export function createExecCommandsTool(
	agent: ICodingAgent,
	logger: StructuredLogger
) {
	return tool({
		name: 'exec_commands',
		description:
			'Execute shell commands in the sandbox. CRITICAL shouldSave rules: (1) Set shouldSave=true ONLY for package management with specific packages (e.g., "bun add react", "npm install lodash"). (2) Set shouldSave=false for: file operations (rm, mv, cp), plain installs ("bun install"), run commands ("bun run dev"), and temporary operations. Invalid commands in shouldSave=true will be automatically filtered out. Always use bun for package management.',
		args: {
			commands: t.commands().describe('Array of shell commands to execute'),
			shouldSave: t.boolean().default(true).describe('Whether to save package management commands to blueprint'),
			timeout: t.number().default(30000).describe('Timeout in milliseconds'),
		},
		run: async ({ commands, shouldSave, timeout }) => {
			try {
				const shouldSaveValue = shouldSave ?? true;
				const timeoutValue = timeout ?? 30000;

				logger.info('Executing commands', {
					count: commands.length,
					commands,
					shouldSave: shouldSaveValue,
					timeout: timeoutValue,
				});
				const output = await agent.execCommands(commands, shouldSave, timeout);
				
				// Truncate output to max 1000 characters per result
				const MAX_OUTPUT_LENGTH = 1000;
				const truncatedOutput = {
					...output,
					results: output.results.map((result) => ({
						...result,
						output:
							result.output.length > MAX_OUTPUT_LENGTH
								? result.output.substring(0, MAX_OUTPUT_LENGTH) + '\n[truncated to max 1000 characters]'
								: result.output,
					})),
				};
				return truncatedOutput;
			} catch (error) {
				return {
					error:
						error instanceof Error
							? `Failed to execute commands: ${error.message}`
							: 'Unknown error occurred while executing commands',
				};
			}
		},
	});
}

```
