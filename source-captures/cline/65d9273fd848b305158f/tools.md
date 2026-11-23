# cline — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## sdk/packages/core/src/extensions/tools/command-guard-extension.ts

Source: https://github.com/cline/cline/blob/34c104e6fc49781cda4adcd73ffdccd8f029fef9/sdk/packages/core/src/extensions/tools/command-guard-extension.ts

SHA-256: `f3c75bed0e8505e525f73f95e45d26c1d2cd001e84eb9d50bf99035904078ba5`

```
/**
 * Plan-Mode Command Guard Extension
 *
 * Runtime extension that enforces the plan-mode command blacklist
 * (./command-guard.ts) as a `beforeTool` hook. The runtime builder registers
 * it for plan-mode sessions, making command blocking session policy in one
 * shared place: the hook fires for every `run_commands` tool in the runtime —
 * the SDK built-in and host-provided replacements like the VS Code
 * extension's terminal-backed tool — without threading a flag through each
 * layer.
 *
 * Because `beforeTool` hooks run before tool policies and user approval,
 * a blocked call is rejected up front: the user is never asked to approve a
 * command that would only fail, and the model receives the plan-mode error
 * as the tool result (`skip`, not `stop`, so the run continues).
 */

import type {
	AgentBeforeToolContext,
	AgentBeforeToolResult,
	AgentExtension,
	ITelemetryService,
} from "@cline/shared";
import { capturePlanModeCommandBlocked } from "../../services/telemetry/core-events";
import {
	findFileEditingCommand,
	formatPlanModeBlockedCommandError,
} from "./command-guard";
import { DefaultToolNames } from "./constants";
import { normalizeRunCommandsInput } from "./helpers";

export const PLAN_MODE_COMMAND_GUARD_EXTENSION_NAME =
	"core.plan-mode-command-guard";

export interface PlanModeCommandGuardOptions {
	telemetry?: ITelemetryService;
}

export function createPlanModeCommandGuardExtension(
	options: PlanModeCommandGuardOptions = {},
): AgentExtension {
	const beforeTool = (
		context: AgentBeforeToolContext,
	): AgentBeforeToolResult | undefined => {
		if (context.tool.name !== DefaultToolNames.RUN_COMMANDS) {
			return undefined;
		}

		let commands: ReturnType<typeof normalizeRunCommandsInput>;
		try {
			commands = normalizeRunCommandsInput(context.input);
		} catch {
			// Unparseable input: let the tool produce its own validation error.
			return undefined;
		}

		for (const command of commands) {
			const blocked = findFileEditingCommand(command);
			if (blocked) {
				capturePlanModeCommandBlocked(options.telemetry, {
					tool_name: "run_commands",
					blocked_construct: blocked,
					command_count: commands.length,
					agent_id: context.snapshot.agentId,
					conversation_id: context.snapshot.conversationId,
					run_id: context.snapshot.runId,
					iteration: context.snapshot.iteration,
					tool_call_id: context.toolCall.toolCallId,
				});
				return {
					skip: true,
					reason: formatPlanModeBlockedCommandError(blocked),
				};
			}
		}

		return undefined;
	};

	return {
		name: PLAN_MODE_COMMAND_GUARD_EXTENSION_NAME,
		manifest: {
			capabilities: ["hooks"],
		},
		hooks: {
			beforeTool,
		},
	};
}

```

## sdk/packages/core/src/extensions/tools/definitions.ts

Source: https://github.com/cline/cline/blob/34c104e6fc49781cda4adcd73ffdccd8f029fef9/sdk/packages/core/src/extensions/tools/definitions.ts

SHA-256: `8214438eb85755ee40abf6e37652552728195288c770618ed10f6f3937ef519d`

````
/**
 * Default AgentTool Definitions
 *
 * Factory functions for creating the default tools.
 */

import {
	type AgentTool,
	type AgentToolContext,
	createTool,
	getDefaultShell,
	getPowerShellEdition,
	getShellKind,
	type ITelemetryService,
	validateWithZod,
	zodToJsonSchema,
} from "@cline/shared";
import { captureRunCommandsTimeout } from "../../services/telemetry/core-events";
import { CommandExitError, CommandTerminationError } from "./executors/bash";
import {
	MAX_COMMAND_OUTPUT_CHARS,
	MAX_READ_LINES,
	MAX_READ_OUTPUT_CHARS,
	MAX_SEARCH_OUTPUT_CHARS,
} from "./executors/output-limits";
import {
	coalesceOrphanReadRanges,
	formatError,
	formatReadFileQuery,
	formatRunCommandQueryPreview,
	getEditorSizeError,
	getReadFileRangeError,
	normalizeRunCommandsInput,
	TimeoutError,
	withTimeout,
} from "./helpers";
import {
	type ApplyPatchInput,
	ApplyPatchInputSchema,
	ApplyPatchInputUnionSchema,
	type AskQuestionInput,
	AskQuestionInputSchema,
	type EditFileInput,
	EditFileInputSchema,
	type FetchWebContentInput,
	FetchWebContentInputSchema,
	type ReadFileRequest,
	type ReadFilesInput,
	ReadFilesInputSchema,
	ReadFilesInputUnionSchema,
	RunCommandsInputSchema,
	type SearchCodebaseInput,
	SearchCodebaseInputSchema,
	SearchCodebaseUnionInputSchema,
	type SkillsInput,
	SkillsInputSchema,
	type StructuredCommandInput,
	type SubmitInput,
	SubmitInputSchema,
} from "./schemas";
import type {
	ApplyPatchExecutor,
	AskQuestionExecutor,
	CreateDefaultToolsOptions,
	DefaultToolsConfig,
	EditorExecutor,
	FileReadExecutor,
	SearchExecutor,
	ShellExecutor,
	SkillsExecutorWithMetadata,
	ToolOperationResult,
	VerifySubmitExecutor,
	WebFetchExecutor,
} from "./types";

// =============================================================================
// Helper Functions
// =============================================================================

function getStringMetadata(
	context: AgentToolContext,
	key: string,
): string | undefined {
	const value = context.metadata?.[key];
	return typeof value === "string" ? value : undefined;
}

function captureRunCommandsTimeoutFromContext(
	telemetry: ITelemetryService | undefined,
	context: AgentToolContext,
	properties: {
		effectiveTimeoutMs: number;
		timeoutSource: "default_setting" | "configured_setting";
		commandCount: number;
		durationMs: number;
	},
): void {
	captureRunCommandsTimeout(telemetry, {
		tool_name: "run_commands",
		effective_timeout_ms: properties.effectiveTimeoutMs,
		timeout_source: properties.timeoutSource,
		command_count: properties.commandCount,
		duration_ms: properties.durationMs,
		ulid: context.sessionId,
		mode: getStringMetadata(context, "mode"),
		source: getStringMetadata(context, "source"),
		session_id: context.sessionId,
		agent_id: context.agentId,
		conversation_id: context.conversationId,
		run_id: context.runId,
		iteration: context.iteration,
		tool_call_id: context.toolCallId,
	});
}

function getHeredocDelimiter(command: string): string | undefined {
	const match = command.match(
		/(?<![<])<<-?\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_./-]+))/,
	);
	return match?.[1] ?? match?.[2] ?? match?.[3];
}

function coalesceSplitHeredocCommands(commands: string[]): string[] {
	const coalesced: string[] = [];
	for (let index = 0; index < commands.length; index += 1) {
		const command = commands[index];
		const delimiter = getHeredocDelimiter(command);
		if (!delimiter) {
			coalesced.push(command);
			continue;
		}

		const endIndex = commands.findIndex(
			(nextCommand, nextIndex) =>
				nextIndex > index && nextCommand.trim() === delimiter,
		);
		if (endIndex === -1) {
			coalesced.push(command);
			continue;
		}

		const parts = [command];
		while (index < endIndex) {
			index += 1;
			const nextCommand = commands[index];
			parts.push(nextCommand);
		}
		coalesced.push(parts.join("\n"));
	}
	return coalesced;
}

function coalesceAdjacentStringHeredocs(
	commands: Array<string | StructuredCommandInput>,
): Array<string | StructuredCommandInput> {
	const coalesced: Array<string | StructuredCommandInput> = [];
	let stringRun: string[] = [];

	const flushStringRun = () => {
		if (stringRun.length > 0) {
			coalesced.push(...coalesceSplitHeredocCommands(stringRun));
			stringRun = [];
		}
	};

	for (const command of commands) {
		if (typeof command === "string") {
			stringRun.push(command);
			continue;
		}

		flushStringRun();
		coalesced.push(command);
	}

	flushStringRun();
	return coalesced;
}

async function executeShellCommands(
	commands: Array<string | StructuredCommandInput>,
	options: {
		executor: ShellExecutor;
		cwd: string;
		context: AgentToolContext;
		timeoutMs: number;
		timeoutSource: "default_setting" | "configured_setting";
		telemetry?: ITelemetryService;
	},
): Promise<ToolOperationResult[]> {
	const { executor, cwd, context, timeoutMs, timeoutSource, telemetry } =
		options;

	return Promise.all(
		commands.map(
			async (command, commandIndex): Promise<ToolOperationResult> => {
				const startedAt = Date.now();
				const query = formatRunCommandQueryPreview(command);
				const commandContext: AgentToolContext = context.emitUpdate
					? {
							...context,
							emitUpdate: (update) => {
								const payload =
									update && typeof update === "object" && !Array.isArray(update)
										? (update as Record<string, unknown>)
										: { update };
								context.emitUpdate?.({
									...payload,
									commandIndex,
									query,
								});
							},
						}
					: context;
				try {
					const output = await withTimeout(
						executor(command, cwd, commandContext),
						timeoutMs,
						`Command timed out after ${timeoutMs}ms`,
					);
					return {
						query,
						result: output,
						success: true,
					};
				} catch (error) {
					if (error instanceof TimeoutError) {
						captureRunCommandsTimeoutFromContext(telemetry, context, {
							effectiveTimeoutMs: error.timeoutMs,
							timeoutSource,
							commandCount: commands.length,
							durationMs: Date.now() - startedAt,
						});
					}
					if (
						error instanceof CommandExitError ||
						error instanceof CommandTerminationError
					) {
						return {
							query,
							result: error.output,
							error: error.message,
							success: false,
						};
					}
					const msg = formatError(error);
					return {
						query,
						result: "",
						error: `Command failed: ${msg}`,
						success: false,
					};
				}
			},
		),
	);
}

// =============================================================================
// AgentTool Factory Functions
// =============================================================================

/**
 * Create the read_files tool
 *
 * Reads the content of one or more files from the filesystem.
 */
export function createReadFilesTool(
	executor: FileReadExecutor,
	config: Pick<DefaultToolsConfig, "fileReadTimeoutMs"> = {},
): AgentTool<ReadFilesInput, ToolOperationResult[]> {
	const timeoutMs = config.fileReadTimeoutMs ?? 10000;

	return createTool<ReadFilesInput, ToolOperationResult[]>({
		name: "read_files",
		description:
			"Read the content of text or image files at the provided absolute paths, or return only an inclusive one-based line range when start_line/end_line are provided on the same file entry as its path. " +
			"When you already know multiple files you need, read them together in one call, and call this tool in the same response as other independent tool calls. " +
			`Each read returns at most ${MAX_READ_LINES} lines / ~${Math.round(MAX_READ_OUTPUT_CHARS / 1024)}k characters; longer files report their total line count, page through them with start_line/end_line on that file's entry. ` +
			"Binary files that are not image and large files are not supported. " +
			"Returns file contents or error messages for each path. ",
		inputSchema: zodToJsonSchema(ReadFilesInputSchema),
		timeoutMs: timeoutMs * 2, // Account for multiple files
		retryable: true,
		maxRetries: 1,
		execute: async (input, context) => {
			const validate = validateWithZod(
				ReadFilesInputUnionSchema,
				coalesceOrphanReadRanges(input),
			);
			let requests: ReadFileRequest[];
			if (typeof validate === "string") {
				requests = [{ path: validate }];
			} else if (Array.isArray(validate)) {
				requests = validate.map((value) =>
					typeof value === "string" ? { path: value } : value,
				);
			} else if ("files" in validate) {
				const files = Array.isArray(validate.files)
					? validate.files
					: [validate.files];
				requests = files.map((file) =>
					typeof file === "string" ? { path: file } : file,
				);
			} else if ("file_paths" in validate) {
				const filePaths = Array.isArray(validate.file_paths)
					? validate.file_paths
					: [validate.file_paths];
				requests = filePaths.map((path) => ({ path }));
			} else if ("paths" in validate) {
				const paths = Array.isArray(validate.paths)
					? validate.paths
					: [validate.paths];
				requests = paths.map((path) =>
					typeof path === "string" ? { path } : path,
				);
			} else {
				requests = [validate];
			}

			return Promise.all(
				requests.map(async (request): Promise<ToolOperationResult> => {
					const rangeError = getReadFileRangeError(request);
					if (rangeError) {
						return {
							query: formatReadFileQuery(request),
							result: "",
							error: `Invalid file range: ${rangeError}`,
							success: false,
						};
					}

					try {
						const content = await withTimeout(
							executor(request, context),
							timeoutMs,
							`File read timed out after ${timeoutMs}ms`,
						);
						return {
							query: formatReadFileQuery(request),
							result: content,
							success: true,
						};
					} catch (error) {
						const msg = formatError(error);
						return {
							query: formatReadFileQuery(request),
							result: "",
							error: `Error reading file: ${msg}`,
							success: false,
						};
					}
				}),
			);
		},
	});
}

/**
 * Create the search_codebase tool
 *
 * Performs regex pattern searches across the codebase.
 */
export function createSearchTool(
	executor: SearchExecutor,
	config: Pick<DefaultToolsConfig, "cwd" | "searchTimeoutMs"> = {},
): AgentTool<SearchCodebaseInput, ToolOperationResult[]> {
	const timeoutMs = config.searchTimeoutMs ?? 30000;
	const cwd = config.cwd ?? process.cwd();

	return createTool<SearchCodebaseInput, ToolOperationResult[]>({
		name: "search_codebase",
		description:
			"Perform regex pattern searches across the codebase. " +
			"Supports multiple parallel searches. When several search patterns could be useful and do not depend on each other, run them together in one call, and call this tool in the same response as other independent tool calls. " +
			"Use for finding code patterns, function definitions, class names, imports, etc. " +
			`Output beyond ~${Math.round(MAX_SEARCH_OUTPUT_CHARS / 1000)}k characters per query is middle-truncated; narrow patterns beat broad ones.`,
		inputSchema: zodToJsonSchema(SearchCodebaseInputSchema),
		timeoutMs: timeoutMs * 2,
		retryable: true,
		maxRetries: 1,
		execute: async (input, context) => {
			// Validate input with Zod schema
			const validate = validateWithZod(SearchCodebaseUnionInputSchema, input);
			const queries = Array.isArray(validate)
				? validate
				: typeof validate === "object"
					? Array.isArray(validate.queries)
						? validate.queries
						: [validate.queries]
					: [validate];

			return Promise.all(
				queries.map(async (query): Promise<ToolOperationResult> => {
					try {
						const results = await withTimeout(
							executor(query, cwd, context),
							timeoutMs,
							`Search timed out after ${timeoutMs}ms`,
						);
						return {
							query,
							result: results,
							success: true,
						};
					} catch (error) {
						const msg = formatError(error);
						return {
							query,
							result: "",
							error: `Search failed: ${msg}`,
							success: false,
						};
					}
				}),
			);
		},
	});
}

const RUN_COMMANDS_SHARED_INSTRUCTIONS =
	"Use for listing files, checking git status, running builds, executing tests, etc. " +
	"Commands must be non-interactive. Commands that require follow-up input like pagers should be skipped or used with supported flags/env (e.g. git --no-pager, --non-interactive) to bypass the interaction steps. ";

/**
 * PowerShell quoting guidance. Models default to bash/C escaping, but in
 * PowerShell a backslash is an ordinary character: `"a \"b\" c"` ends the
 * string at the first `\"`, and the rest becomes separate arguments. Through
 * `cmd /c` that can hand `rmdir /s /q` a bare `\`, which is the root of the
 * current drive.
 */
const POWERSHELL_QUOTING_INSTRUCTIONS =
	'PowerShell escapes with a backtick (`), not a backslash: inside double quotes, \\" ends the string. Put literal paths in single quotes. ' +
	"Use cmdlets such as Remove-Item -LiteralPath instead of cmd /c; cmd re-parses the quotes, and a mis-quoted path can become extra targets such as the drive root. " +
	"Before a recursive or forced delete, confirm the exact target, for example with -WhatIf. ";

/**
 * Build the run_commands tool description for the shell that will actually
 * execute the commands. Derive the edition and wrapper guidance from the
 * same executable, without probing a version or adding volatile prompt text.
 * isWindows describes the host, not the shell's edition.
 */
export function buildRunCommandsDescription(
	shell: string,
	isWindows: boolean,
): string {
	const shellKind = getShellKind(shell);
	if (shellKind === "powershell" || shellKind === "cmd") {
		const edition = getPowerShellEdition(shell);
		const executable =
			edition === "windows"
				? "powershell.exe"
				: edition === "core"
					? isWindows
						? "pwsh.exe"
						: "pwsh"
					: "cmd.exe";
		const shellName =
			edition === "windows"
				? `Windows PowerShell (${executable})`
				: edition === "core"
					? `PowerShell (${executable})`
					: executable;
		const wrapper = `${executable} ${shellKind === "powershell" ? "-Command" : "/c"}`;
		const sequencingOperator = shellKind === "powershell" ? "';'" : "'&&'";
		return (
			`Run non-interactive shell commands from the root of the workspace${isWindows ? " in Windows environment" : ""}. ` +
			RUN_COMMANDS_SHARED_INSTRUCTIONS +
			`Output beyond ~${Math.round(MAX_COMMAND_OUTPUT_CHARS / 1000)}k characters is middle-truncated (start and end preserved); filter output when you need specific sections. ` +
			`Commands run through ${shellName}; quote paths and arguments for ${executable} and use ${sequencingOperator} to sequence commands. ` +
			`Write commands directly; do not wrap them in another ${wrapper} invocation. ` +
			(shellKind === "powershell" ? POWERSHELL_QUOTING_INSTRUCTIONS : "") +
			"Only start another shell when you intentionally need a different shell or a separate process. " +
			"Include multiple commands in the same call when they are independent and safe to run concurrently. When independent reads, searches, or edits are also needed, call those tools in the same response."
		);
	}

	const environmentNote =
		shellKind === "wsl"
			? "Commands run through bash in WSL (wsl.exe); the Windows working directory is mounted under /mnt/<drive>. "
			: isWindows
				? "Commands run through a POSIX (bash-compatible) shell on Windows. "
				: "";
	return (
		"Run non-interactive shell commands from the root of the workspace. " +
		RUN_COMMANDS_SHARED_INSTRUCTIONS +
		environmentNote +
		"Commands should be properly shell-escaped and targeted to avoid error or timeout. Include multiple commands in the same call when they are independent complete shell commands and safe to run concurrently; multiline scripts and heredocs must be a single command string. When independent reads, searches, or edits are also needed, call those tools in the same response. " +
		`Output beyond ~${Math.round(MAX_COMMAND_OUTPUT_CHARS / 1000)}k characters is middle-truncated (start and end preserved); pipe through grep/head/tail when you need specific sections of large output. ` +
		"For long-running commands, run them in background and redirect output to a tmp file that you can read from later."
	);
}

/**
 * Create the run_commands shell tool for the current platform.
 *
 * This preserves the SDK's platform-specific prompting/schema choices while
 * exposing a single generic shell-tool factory for host integrations. Pass
 * config.shell (matching the executor's shell) so the syntax guidance in the
 * tool description matches the shell that actually runs the commands.
 *
 * config.shell may be a provider function instead of a string. The runtime
 * reads `description` when building each model request, so a provider is
 * consulted at that boundary: a shell change made while the model is
 * generating does not affect the request in flight, and the next request
 * names the new shell. The provider must return the shell the executor will
 * use for tool calls issued by that next request.
 */
export function createShellTool(
	executor: ShellExecutor,
	config: Pick<DefaultToolsConfig, "cwd" | "bashTimeoutMs" | "telemetry"> & {
		shell?: string | (() => string);
	} = {},
): AgentTool<unknown, ToolOperationResult[]> {
	const timeoutMs = config.bashTimeoutMs ?? 30000;
	const timeoutSource =
		config.bashTimeoutMs === undefined
			? "default_setting"
			: "configured_setting";
	const cwd = config.cwd ?? process.cwd();
	const isWindows = process.platform === "win32";
	const configShell = config.shell;
	const resolveShell =
		typeof configShell === "function"
			? configShell
			: () => configShell ?? getDefaultShell(process.platform);
	const describe = () => buildRunCommandsDescription(resolveShell(), isWindows);

	const tool = createTool<unknown, ToolOperationResult[]>({
		name: "run_commands",
		description: describe(),
		inputSchema: zodToJsonSchema(RunCommandsInputSchema),
		timeoutMs: timeoutMs * 2,
		retryable: false,
		maxRetries: 0,
		execute: async (input, context) => {
			const commands = coalesceAdjacentStringHeredocs(
				normalizeRunCommandsInput(input),
			);

			return executeShellCommands(commands, {
				executor,
				cwd,
				context,
				timeoutMs,
				timeoutSource,
				telemetry: config.telemetry,
			});
		},
	});

	if (typeof configShell === "function") {
		// The runtime rebuilds tool definitions from this property for every
		// model request, so a getter re-derives the description at exactly the
		// send-to-model boundary. AgentTool consumers only read `description`.
		Object.defineProperty(tool, "description", {
			get: describe,
			enumerable: true,
		});
	}
	return tool;
}

/**
 * Create the fetch_web_content tool
 *
 * Fetches content from URLs and analyzes them using provided prompts.
 */
export function createWebFetchTool(
	executor: WebFetchExecutor,
	config: Pick<DefaultToolsConfig, "webFetchTimeoutMs"> = {},
): AgentTool<FetchWebContentInput, ToolOperationResult[]> {
	const timeoutMs = config.webFetchTimeoutMs ?? 30000;

	return createTool<FetchWebContentInput, ToolOperationResult[]>({
		name: "fetch_web_content",
		description:
			"Fetch content from URLs and analyze them using the provided prompts. " +
			"Use for retrieving documentation, API references, or any web content. " +
			"Each request includes a URL and a prompt describing what information to extract. Fetch independent URLs together in one call, and call this tool in the same response as other independent tool calls.",
		inputSchema: zodToJsonSchema(FetchWebContentInputSchema),
		timeoutMs: timeoutMs * 2,
		retryable: true,
		maxRetries: 2,
		execute: async (input, context) => {
			// Validate input with Zod schema
			const validatedInput = validateWithZod(FetchWebContentInputSchema, input);

			return Promise.all(
				validatedInput.requests.map(
					async (request): Promise<ToolOperationResult> => {
						try {
							const content = await withTimeout(
								executor(request.url, request.prompt, context),
								timeoutMs,
								`Web fetch timed out after ${timeoutMs}ms`,
							);
							return {
								query: request.url,
								result: content,
								success: true,
							};
						} catch (error) {
							const msg = formatError(error);
							return {
								query: request.url,
								result: "",
								error: `Error fetching web content: ${msg}`,
								success: false,
							};
						}
					},
				),
			);
		},
	});
}

const APPLY_PATCH_TOOL_DESC = `Use \`apply_patch\` to edit files with the canonical freeform patch grammar. Pass the patch text directly as the \`input\` string. Prefer the exact format below:

*** Begin Patch
*** Update File: path/to/file.ts
@@ optional section marker
 [context before]
-[old line]
+[new line]
 [context after]
*** End Patch

Supported actions:
- \`*** Add File: <path>\`
- \`*** Update File: <path>\`
- \`*** Delete File: <path>\`
- optional \`*** Move to: <new path>\` immediately after an Update File header

Rules:
- In an Add File section, every file-content line must start with \`+\`.
- In an Update section, use context lines plus \`-\` and \`+\` lines to describe the change.
- Use \`@@\` markers when extra context is needed to disambiguate repeated code blocks.
- Do not use line numbers; this format is context-based.
- Prefer sending the patch body directly. Legacy shell wrappers such as \`%%bash\` and \`apply_patch <<"EOF"\` are accepted for compatibility but are not preferred.

Example:

*** Begin Patch
*** Update File: src/page.tsx
@@
   return (
     <div>
       <button onClick={() => console.log("clicked")}>Click me</button>
+      <button onClick={() => console.log("cancel clicked")}>Cancel</button>
     </div>
   );
 }
*** End Patch`;

/**
 * Create the apply_patch tool
 *
 * Applies the canonical apply_patch format to one or more files.
 */
export function createApplyPatchTool(
	executor: ApplyPatchExecutor,
	config: Pick<DefaultToolsConfig, "cwd" | "applyPatchTimeoutMs"> = {},
): AgentTool<ApplyPatchInput, ToolOperationResult> {
	const timeoutMs = config.applyPatchTimeoutMs ?? 30000;
	const cwd = config.cwd ?? process.cwd();

	return createTool<ApplyPatchInput, ToolOperationResult>({
		name: "apply_patch",
		description: APPLY_PATCH_TOOL_DESC,
		inputSchema: zodToJsonSchema(ApplyPatchInputSchema),
		timeoutMs,
		retryable: false,
		maxRetries: 0,
		execute: async (input, context) => {
			const validate = validateWithZod(ApplyPatchInputUnionSchema, input);
			const patchInput =
				typeof validate === "string" ? validate : validate.input;

			try {
				const result = await withTimeout(
					executor({ input: patchInput }, cwd, context),
					timeoutMs,
					`apply_patch timed out after ${timeoutMs}ms`,
				);

				return {
					query: "apply_patch",
					result,
					success: true,
				};
			} catch (error) {
				const msg = formatError(error);
				return {
					query: "apply_patch",
					result: "",
					error: `apply_patch failed: ${msg}`,
					success: false,
				};
			}
		},
	});
}

/**
 * Create the editor tool
 *
 * Supports controlled filesystem edits with create, replace, and insert commands.
 */
export function createEditorTool(
	executor: EditorExecutor,
	config: Pick<DefaultToolsConfig, "cwd" | "editorTimeoutMs"> = {},
): AgentTool<EditFileInput, ToolOperationResult> {
	const timeoutMs = config.editorTimeoutMs ?? 30000;
	const cwd = config.cwd ?? process.cwd();

	return createTool<EditFileInput, ToolOperationResult>({
		name: "editor",
		description:
			"An editor for controlled filesystem edits on the text file at the provided path. " +
			"Provide `insert_line` to insert `new_text` at a specific line number. " +
			"Otherwise, the tool replaces `old_text` with `new_text`, or creates the file with `new_text` if file does not exist. " +
			"Use this tool for making small, precise edits to existing files or creating new files over shell commands. If several edits to different files or non-overlapping regions are already known, emit multiple editor tool calls in the same response instead of serializing them across turns.",

		inputSchema: zodToJsonSchema(EditFileInputSchema),
		timeoutMs,
		retryable: false, // Editing operations are stateful and should not auto-retry
		maxRetries: 0,
		execute: async (input, context) => {
			const validatedInput = validateWithZod(EditFileInputSchema, input);
			const operation = validatedInput.insert_line == null ? "edit" : "insert";
			const sizeError = getEditorSizeError(validatedInput);

			if (sizeError) {
				return {
					query: `${operation}:${validatedInput.path}`,
					result: "",
					error: sizeError,
					success: false,
				};
			}

			try {
				const result = await withTimeout(
					executor(validatedInput, cwd, context),
					timeoutMs,
					`Editor operation timed out after ${timeoutMs}ms`,
				);

				return {
					query: `${operation}:${validatedInput.path}`,
					result,
					success: true,
				};
			} catch (error) {
				const msg = formatError(error);
				return {
					query: `${operation}:${validatedInput.path}`,
					result: "",
					error: `Editor operation failed: ${msg}`,
					success: false,
				};
			}
		},
	});
}

/**
 * Create the skills tool
 *
 * Invokes a configured skill by name and optional arguments.
 */
export function createSkillsTool(
	executor: SkillsExecutorWithMetadata,
	config: Pick<DefaultToolsConfig, "skillsTimeoutMs"> = {},
): AgentTool<SkillsInput, string> {
	const timeoutMs = config.skillsTimeoutMs ?? 15000;

	const baseDescription =
		"Execute a skill within the main conversation. " +
		"When users ask you to perform tasks, check if any available skills match. " +
		"When users reference a slash command, invoke it with this tool. " +
		'Input: `skill` (required) and optional `args`. Example: `skill: "pdf"`, `skill: "commit", args: "-m \\"Fix bug\\""`, `skill: "review-pr", args: "123"`, `skill: "ms-office-suite:pdf"`. ' +
		"When a skill matches the user's request, invoking this tool is a blocking requirement before any other response. " +
		"Never mention a skill without invoking this tool.";

	const tool = createTool<SkillsInput, string>({
		name: "skills",
		description: baseDescription,
		inputSchema: zodToJsonSchema(SkillsInputSchema),
		timeoutMs,
		retryable: false,
		maxRetries: 0,
		execute: async (input, context) => {
			const validatedInput = validateWithZod(SkillsInputSchema, input);
			return withTimeout(
				executor(
					validatedInput.skill,
					validatedInput.args || undefined,
					context,
				),
				timeoutMs,
				`Skills operation timed out after ${timeoutMs}ms`,
			);
		},
	});

	Object.defineProperty(tool, "description", {
		get() {
			const skills = executor.configuredSkills
				?.filter((s) => !s.disabled)
				.map((s) => (s.id.includes(":") ? s.id : s.name));
			if (skills && skills.length > 0) {
				return `${baseDescription} Available skills: ${skills.join(", ")}.`;
			}
			return baseDescription;
		},
		enumerable: true,
		configurable: true,
	});

	return tool;
}

/**
 * An empty answer (dismissed, timed out, session torn
 * down) is an error so the model never assumes an answer the user did not give.
 */
function validateAskQuestionAnswer(
	answer: Awaited<ReturnType<AskQuestionExecutor>>,
): Awaited<ReturnType<AskQuestionExecutor>> {
	const answered =
		typeof answer === "string"
			? answer.trim()
			: answer.some((part) => part.type === "image" || part.text.trim());
	if (!answered) {
		throw new Error(
			"The user did not answer. Do not assume an answer: make a reasonable choice and say so, or stop and report what you need.",
		);
	}
	return answer;
}

/**
 * Create the ask_question tool
 *
 * Asks the user a single clarifying question with 2-5 selectable options.
 */
export function createAskQuestionTool(
	executor: AskQuestionExecutor,
): AgentTool<AskQuestionInput, Awaited<ReturnType<AskQuestionExecutor>>> {
	return {
		name: "ask_question",
		successContext:
			"The user has answered the question. Continue working on their task using that answer.",
		description:
			"Ask user a question for clarifying or gathering information needed to complete the task. " +
			"For example, ask the user clarifying questions about a key implementation decision. " +
			"You should only ask one question. " +
			"Provide an array of 2-5 options for the user to choose from. " +
			"Never include an option to toggle to Act mode. " +
			"The run continues after the user answers: keep working on the task using their answer.",
		inputSchema: zodToJsonSchema(AskQuestionInputSchema),
		retryable: false,
		maxRetries: 0,
		execute: async (input, context) => {
			const validatedInput = validateWithZod(AskQuestionInputSchema, input);
			const answer = await executor(
				validatedInput.question,
				validatedInput.options,
				context,
			);
			return validateAskQuestionAnswer(answer);
		},
	};
}

export function createSubmitAndExitTool(
	executor: VerifySubmitExecutor,
	config: Pick<DefaultToolsConfig, "submitTimeoutMs"> = {},
): AgentTool<SubmitInput, string> {
	const timeoutMs = config.submitTimeoutMs ?? 15000;

	return createTool<SubmitInput, string>({
		name: "submit_and_exit",
		description:
			"Submit the final answer and exit the conversation. " +
			"For example, submit a summary of the investigation and confirm the issue is resolved. " +
			"You should only submit once all necessary steps are completed AND you have verified the result by execution. " +
			"Before calling this, re-read the task, then confirm from your own tool output that every required file exists at the specified path and that its contents match the expected format, data types, and values. " +
			"If the task provides tests, run them and confirm they pass; if it does not, run your own solution end to end and read the output back as evidence. " +
			"Do not submit on the assumption that your solution works — submit because you have observed evidence that it does. " +
			"Provide a summary of the investigation and confirm the issue is resolved.",
		inputSchema: zodToJsonSchema(SubmitInputSchema),
		lifecycle: {
			completesRun: true,
		},
		timeoutMs,
		retryable: false,
		maxRetries: 0,
		execute: async (input, context) => {
			const validatedInput = validateWithZod(SubmitInputSchema, input);
			return withTimeout(
				executor(validatedInput.summary, validatedInput.verified, context),
				timeoutMs,
				`submit_and_exit timed out after ${timeoutMs}ms`,
			);
		},
	});
}

// =============================================================================
// Default Tools Factory
// =============================================================================

/**
 * Create a set of default tools for an agent
 *
 * This function creates the default tools based on the provided configuration
 * and executors. Only tools that are enabled AND have an executor provided
 * will be included in the returned array.
 *
 * @example
 * ```typescript
 * import { Agent, createDefaultTools } from "@cline/core"
 * import * as fs from "fs/promises"
 * import { exec } from "child_process"
 *
 * const tools = createDefaultTools({
 *   executors: {
 *     readFile: async ({ path }) => fs.readFile(path, "utf-8"),
 *     bash: async (cmd, cwd) => {
 *       return new Promise((resolve, reject) => {
 *         exec(cmd, { cwd }, (err, stdout, stderr) => {
 *           if (err) reject(new Error(stderr || err.message))
 *           else resolve(stdout)
 *         })
 *       })
 *     },
 *   },
 *   enableReadFiles: true,
 *   enableBash: true,
 *   enableSearch: false, // Disabled
 *   enableWebFetch: false, // Disabled
 *   cwd: "/path/to/project",
 * })
 *
 * const agent = new Agent({
 *   // ... provider config
 *   tools,
 * })
 * ```
 */
export function createDefaultTools(
	options: CreateDefaultToolsOptions,
): AgentTool[] {
	const {
		executors,
		enableReadFiles = true,
		enableSearch = true,
		enableBash = true,
		enableWebFetch = true,
		enableApplyPatch = false,
		enableEditor = true,
		enableSkills = true,
		enableAskQuestion = true,
		enableSubmitAndExit = false,
		...config
	} = options;

	const tools: AgentTool<never, unknown>[] = [];

	// Add read_files tool if enabled and executor provided
	if (enableReadFiles && executors.readFile) {
		tools.push(createReadFilesTool(executors.readFile, config));
	}

	// Add search_codebase tool if enabled and executor provided
	if (enableSearch && executors.search) {
		tools.push(createSearchTool(executors.search, config));
	}

	// Add run_commands tool if enabled and executor provided
	if (enableBash && executors.bash) {
		tools.push(createShellTool(executors.bash, config));
	}

	// Add fetch_web_content tool if enabled and executor provided
	if (enableWebFetch && executors.webFetch) {
		tools.push(createWebFetchTool(executors.webFetch, config));
	}

	// Add editor tool if enabled and executor provided,
	// else check if apply_patch tool is enabled and executor provided
	// NOTE: Do not enable two similar tools at the same time.
	if (enableEditor && executors.editor) {
		tools.push(createEditorTool(executors.editor, config));
	} else if (enableApplyPatch && executors.applyPatch) {
		tools.push(createApplyPatchTool(executors.applyPatch, config));
	}

	// Add skills tool if enabled and executor provided
	if (enableSkills && executors.skills) {
		tools.push(createSkillsTool(executors.skills, config));
	}

	const submitExecutor = enableSubmitAndExit ? executors.submit : undefined;

	// Add ask_question tool if enabled and executor provided
	if (enableAskQuestion && executors.askQuestion && !submitExecutor) {
		tools.push(createAskQuestionTool(executors.askQuestion));
	}

	// Add submit_and_exit tool if enabled and executor provided
	if (submitExecutor) {
		tools.push(createSubmitAndExitTool(submitExecutor, config));
	}

	return tools as unknown as AgentTool[];
}

````

## sdk/packages/core/src/extensions/tools/executors/bash.ts

Source: https://github.com/cline/cline/blob/34c104e6fc49781cda4adcd73ffdccd8f029fef9/sdk/packages/core/src/extensions/tools/executors/bash.ts

SHA-256: `0088b2c57e8ff68260c45fe261fa32cee2f5cef82d393bc9517e2f7312f4bc60`

````
/**
 * Bash Executor
 *
 * Built-in implementation for running shell commands using Node.js spawn.
 */

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
	createWriteStream,
	type Dirent,
	existsSync,
	mkdtempSync,
	renameSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { readdir, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StringDecoder } from "node:string_decoder";
import {
	type AgentToolContext,
	getDefaultShell,
	getShellInvocation,
} from "@cline/shared";
import {
	type ProcessStartTokenProbeResult,
	probeProcessStartTokenAsync,
} from "../../../runtime/process-start-token";
import { TimeoutError } from "../helpers";
import type { ShellExecutor } from "../types";
import {
	MAX_COMMAND_OUTPUT_CHARS,
	truncateCommandOutput,
} from "./output-limits";
import type { RunCommandExecutionController } from "./run-command-execution-controller";

const MAX_DETACHED_LOG_BYTES = 10 * 1024 * 1024;
const DEFAULT_DETACHED_LOG_RETENTION_MS = 24 * 60 * 60 * 1_000;
const DEFAULT_ACTIVE_COMMAND_POLL_INTERVAL_MS = 60_000;
const DETACHED_LOG_DIRECTORY_PREFIX = "cline-command-";
const DETACHED_LOG_FILENAME = "output.log";
const DETACHED_LOG_ACTIVE_COMMAND_FILENAME = "active-command.json";
const DETACHED_LOG_COMPLETED_FILENAME = "completed-at";
const COMMAND_PROGRESS_FLUSH_INTERVAL_MS = 48;
/**
 * How long the executor waits after the shell process exits for its stdio
 * streams to close before completing a command whose background children
 * still hold the pipes open.
 */
const EXIT_STREAM_GRACE_MS = 1_000;

type CommandProgressStream = "stdout" | "stderr";

export type ProcessStartTokenProbe = (
	pid: number,
) => ProcessStartTokenProbeResult | Promise<ProcessStartTokenProbeResult>;

export interface DetachedCommandLogCleanupOptions {
	tempDirectory?: string;
	retentionMs?: number;
	nowMs?: number;
	processStartTokenProbe?: ProcessStartTokenProbe;
	activeCommandPollIntervalMs?: number;
}

type DetachedCommandMarker = {
	version: 1;
	executionId: string;
	pid: number;
	processStartToken: string;
	detachedAtMs: number;
};

function resolveDetachedLogRetentionMs(value: number | undefined): number {
	return typeof value === "number" && Number.isFinite(value)
		? Math.max(0, value)
		: DEFAULT_DETACHED_LOG_RETENTION_MS;
}

function resolveActiveCommandPollIntervalMs(value: number | undefined): number {
	return typeof value === "number" && Number.isFinite(value)
		? Math.max(1, value)
		: DEFAULT_ACTIVE_COMMAND_POLL_INTERVAL_MS;
}

type ResolvedDetachedCommandLogCleanupOptions = {
	retentionMs: number;
	nowMs: number;
	probeProcessStartToken: ProcessStartTokenProbe;
	activeCommandPollIntervalMs: number;
};

function parseDetachedCommandMarker(
	text: string,
): DetachedCommandMarker | undefined {
	try {
		const marker = JSON.parse(text) as Partial<DetachedCommandMarker>;
		if (
			marker.version !== 1 ||
			typeof marker.executionId !== "string" ||
			marker.executionId.length === 0 ||
			!Number.isSafeInteger(marker.pid) ||
			(marker.pid ?? 0) <= 0 ||
			typeof marker.processStartToken !== "string" ||
			marker.processStartToken.length === 0 ||
			typeof marker.detachedAtMs !== "number" ||
			!Number.isFinite(marker.detachedAtMs) ||
			marker.detachedAtMs < 0
		) {
			return undefined;
		}
		return marker as DetachedCommandMarker;
	} catch {
		return undefined;
	}
}

function writeLifecycleMarker(path: string, text: string): void {
	const temporaryPath = `${path}.${randomUUID()}.tmp`;
	try {
		writeFileSync(temporaryPath, text, "utf8");
		try {
			renameSync(temporaryPath, path);
		} catch (error) {
			// Windows does not replace an existing destination atomically. Existing
			// lifecycle markers are only replaced when corrupt, so remove that
			// unusable generation before installing the valid one.
			if (
				error instanceof Error &&
				"code" in error &&
				["EEXIST", "EPERM"].includes(
					String((error as NodeJS.ErrnoException).code),
				)
			) {
				rmSync(path, { force: true });
				renameSync(temporaryPath, path);
			} else {
				throw error;
			}
		}
	} finally {
		rmSync(temporaryPath, { force: true });
	}
}

function parseCompletedAtMs(
	text: string | undefined,
	nowMs: number,
): number | undefined {
	const normalized = text?.trim();
	if (!normalized) return undefined;
	const value = Number(normalized);
	if (!Number.isFinite(value) || value < 0) return undefined;
	// Clock rollback or corrupt future timestamps must not retain a log beyond
	// one full inspection window from this reconciliation.
	return Math.min(value, nowMs);
}

async function readOptionalTextFile(path: string): Promise<string | undefined> {
	try {
		return await readFile(path, "utf8");
	} catch (error) {
		if (
			error instanceof Error &&
			"code" in error &&
			(error as NodeJS.ErrnoException).code === "ENOENT"
		) {
			return undefined;
		}
		throw error;
	}
}

async function reconcileDetachedCommandLogDirectory(
	directory: string,
	options: ResolvedDetachedCommandLogCleanupOptions,
): Promise<boolean> {
	const activeCommandPath = join(
		directory,
		DETACHED_LOG_ACTIVE_COMMAND_FILENAME,
	);
	const completedAtPath = join(directory, DETACHED_LOG_COMPLETED_FILENAME);
	const completedAtText = await readOptionalTextFile(completedAtPath);
	let retentionStartedAtMs = parseCompletedAtMs(completedAtText, options.nowMs);
	const activeCommandText = await readOptionalTextFile(activeCommandPath);
	if (retentionStartedAtMs !== undefined) {
		// Completion is written before the active marker is removed. Treat it as
		// authoritative after a host crash between those two operations.
		if (activeCommandText !== undefined) {
			await rm(activeCommandPath, { force: true });
		}
	} else if (activeCommandText !== undefined) {
		const marker = parseDetachedCommandMarker(activeCommandText);
		if (marker) {
			let probe: ProcessStartTokenProbeResult;
			try {
				probe = await options.probeProcessStartToken(marker.pid);
			} catch {
				// Custom identity providers can fail by rejecting instead of returning
				// an explicit unavailable result. Preserve the same conservative
				// lifecycle semantics and keep polling in either case.
				probe = { status: "unavailable" };
			}
			if (
				probe.status === "found" &&
				probe.token === marker.processStartToken
			) {
				// The detached command, rather than the host that launched it, owns the
				// active lifecycle. Matching both PID and kernel start token prevents a
				// later process that reuses the PID from extending this log's lifetime.
				scheduleActiveCommandLogReconciliation(directory, options);
				return false;
			}
			if (probe.status === "unavailable") {
				// An unavailable identity provider is not evidence that the command
				// exited. Keep the advertised log and retry until the provider can
				// prove either the same identity, a replacement process, or absence.
				scheduleActiveCommandLogReconciliation(directory, options);
				return false;
			}
		}
		// A missing process, mismatched identity, or malformed marker begins
		// bounded retention. Never fall back to PID-only polling.
		writeLifecycleMarker(completedAtPath, String(options.nowMs));
		await rm(activeCommandPath, { force: true });
		retentionStartedAtMs = options.nowMs;
	}

	if (retentionStartedAtMs === undefined) {
		// Logs without lifecycle markers use their last write as a best-effort
		// fallback so malformed or interrupted state cannot accumulate forever.
		try {
			retentionStartedAtMs = (
				await stat(join(directory, DETACHED_LOG_FILENAME))
			).mtimeMs;
		} catch {
			retentionStartedAtMs = (await stat(directory)).mtimeMs;
		}
	}
	const cutoffMs = options.nowMs - options.retentionMs;
	if (retentionStartedAtMs > cutoffMs) {
		scheduleDetachedLogCleanup(
			directory,
			Math.max(0, retentionStartedAtMs + options.retentionMs - options.nowMs),
		);
		return false;
	}
	await rm(directory, { recursive: true, force: true });
	return true;
}

function scheduleActiveCommandLogReconciliation(
	directory: string,
	options: ResolvedDetachedCommandLogCleanupOptions,
	delayMs = options.activeCommandPollIntervalMs,
): void {
	const timer = setTimeout(() => {
		void reconcileDetachedCommandLogDirectory(directory, {
			...options,
			nowMs: Date.now(),
		}).catch(() => undefined);
	}, delayMs);
	timer.unref();
}

/**
 * Reaps completed detached command logs outside the retention window,
 * schedules retained logs for their remaining lifetime, and follows active
 * command identities until they exit. Local runtime hosts call this at startup
 * so cleanup survives host exits and restarts instead of depending only on the
 * process that launched the command.
 */
export async function cleanupStaleDetachedCommandLogs(
	options: DetachedCommandLogCleanupOptions = {},
): Promise<number> {
	const tempDirectory = options.tempDirectory ?? tmpdir();
	const retentionMs = resolveDetachedLogRetentionMs(options.retentionMs);
	const nowMs = options.nowMs ?? Date.now();
	const probeProcessStartToken =
		options.processStartTokenProbe ?? probeProcessStartTokenAsync;
	const activeCommandPollIntervalMs = resolveActiveCommandPollIntervalMs(
		options.activeCommandPollIntervalMs,
	);
	let entries: Dirent[];
	try {
		entries = await readdir(tempDirectory, { withFileTypes: true });
	} catch {
		return 0;
	}

	let removed = 0;
	for (const entry of entries) {
		if (
			!entry.isDirectory() ||
			!entry.name.startsWith(DETACHED_LOG_DIRECTORY_PREFIX)
		) {
			continue;
		}
		const directory = join(tempDirectory, entry.name);
		try {
			if (
				await reconcileDetachedCommandLogDirectory(directory, {
					retentionMs,
					nowMs,
					probeProcessStartToken,
					activeCommandPollIntervalMs,
				})
			) {
				removed += 1;
			}
		} catch {
			// Cleanup is best effort: one inaccessible or concurrently removed log
			// must not prevent the Hub from starting or other logs from being reaped.
		}
	}
	return removed;
}

export class CommandExitError extends Error {
	constructor(
		readonly exitCode: number,
		readonly output: string,
	) {
		super(`Command exited with code ${exitCode}`);
		this.name = "CommandExitError";
	}
}

function commandCompletionMessage(
	code: number | null,
	signal: NodeJS.Signals | null,
): string {
	if (code !== null) return `Command exited with code ${code}`;
	return signal
		? `Command terminated by signal ${signal}`
		: "Command terminated without an exit code";
}

/** The process terminated without a numeric exit code, usually by signal. */
export class CommandTerminationError extends Error {
	constructor(
		readonly signal: NodeJS.Signals | null,
		readonly output: string,
	) {
		super(commandCompletionMessage(null, signal));
		this.name = "CommandTerminationError";
	}
}

/**
 * The shell process could not be started, so the command never ran and there
 * is no exit code. `code` carries the operating system error libuv reported —
 * `ENOENT`, `EACCES`, `EFTYPE` for a file that is not a valid executable — so
 * a host can count "the shell is missing" separately from "the command
 * failed" without parsing the message.
 *
 * `ENOENT` alone is ambiguous: spawn reports it both when the executable is
 * not found and when the working directory no longer exists, with the same
 * message. `missing` settles it by checking the directory at failure time, so
 * "shell not on PATH" and "workspace folder gone" stay separately countable.
 */
export class CommandSpawnError extends Error {
	readonly code: string | undefined;
	/** For `ENOENT`, which path was absent. Undefined for every other code. */
	readonly missing: "executable" | "cwd" | undefined;

	constructor(cause: Error, options: { cwd?: string } = {}) {
		super(`Failed to execute command: ${cause.message}`);
		this.name = "CommandSpawnError";
		const code = (cause as NodeJS.ErrnoException).code;
		this.code = typeof code === "string" ? code : undefined;
		if (this.code !== "ENOENT") {
			this.missing = undefined;
		} else if (options.cwd !== undefined && !existsSync(options.cwd)) {
			this.missing = "cwd";
		} else {
			this.missing = "executable";
		}
	}
}

/**
 * Options for the shell executor
 */
export interface ShellExecutorOptions {
	/**
	 * Shell to use for execution
	 * @default "/bin/bash" on Unix, "powershell" on Windows
	 */
	shell?: string;

	/**
	 * Timeout for command execution in milliseconds
	 * @default 30000 (30 seconds)
	 */
	timeoutMs?: number;

	/**
	 * Maximum output kept, in characters. Output beyond this is
	 * middle-truncated: the head and tail are preserved and the middle is
	 * elided, since build and test failures usually live at the end of the
	 * output.
	 * @default 48_000 — see MAX_COMMAND_OUTPUT_CHARS in output-limits.ts
	 */
	maxOutputChars?: number;

	/**
	 * @deprecated Misnamed — the limit was always enforced in characters,
	 * not bytes. Use {@link maxOutputChars}; this alias is honored when
	 * maxOutputChars is not set.
	 */
	maxOutputBytes?: number;

	/**
	 * Environment variables to add/override
	 */
	env?: Record<string, string>;

	/**
	 * Whether to combine stdout and stderr
	 * @default true
	 */
	combineOutput?: boolean;

	/**
	 * Optional host-scoped controller that can release an in-flight command
	 * from its tool call while continuing to drain output to a bounded log.
	 */
	executionController?: RunCommandExecutionController;

	/**
	 * How long a completed detached command log remains available before its
	 * temporary directory is removed.
	 *
	 * @default 24 hours
	 */
	detachedLogRetentionMs?: number;

	/** Process identity provider used to enable safe command detachment. */
	processStartTokenProbe?: ProcessStartTokenProbe;
}

interface SpawnConfig {
	executable: string;
	args: string[];
	cwd: string;
	env: Record<string, string>;
	input?: string;
}

/**
 * Collects stream output with bounded memory: the first half of the budget
 * is kept verbatim, the rest rolls so the latest output always survives.
 */
function createRollingCollector(maxChars: number) {
	const headLimit = Math.ceil(maxChars / 2);
	const tailLimit = Math.max(1, maxChars - headLimit);
	// StringDecoder keeps multibyte UTF-8 sequences split across stream
	// chunks intact instead of corrupting them at chunk boundaries.
	const decoder = new StringDecoder("utf8");
	let head = "";
	let tail = "";
	let totalChars = 0;

	const appendText = (text: string): void => {
		if (!text) return;
		totalChars += text.length;
		const headRoom = headLimit - head.length;
		if (headRoom > 0) {
			head += text.slice(0, headRoom);
			tail = (tail + text.slice(headRoom)).slice(-tailLimit);
			return;
		}
		tail = (tail + text).slice(-tailLimit);
	};

	return {
		append(data: Buffer): string {
			const text = decoder.write(data);
			appendText(text);
			return text;
		},
		current() {
			return {
				text: head + tail,
				totalChars,
				dropped: totalChars > head.length + tail.length,
			};
		},
		snapshot() {
			// Flush bytes the decoder buffered for an incomplete multibyte
			// sequence at end-of-stream; otherwise the final characters of
			// non-ASCII output are silently dropped.
			const finalChunk = decoder.end();
			appendText(finalChunk);
			return {
				text: head + tail,
				totalChars,
				dropped: totalChars > head.length + tail.length,
				finalChunk,
			};
		},
	};
}

/**
 * Coalesces high-volume process output before it enters the runtime event
 * pipeline. Each stream keeps only a bounded pending tail and produces at
 * most one update per flush interval, preventing a noisy child process from
 * creating an unbounded queue of Hub and websocket events.
 */
function createCommandProgressEmitter(
	context: AgentToolContext,
	executionId: string,
	isDetachable: () => boolean,
	maxOutputChars: number,
) {
	const emitUpdate = context.emitUpdate;
	const maxPendingChars =
		typeof maxOutputChars === "number" && Number.isFinite(maxOutputChars)
			? Math.max(
					1,
					Math.floor(Math.min(MAX_COMMAND_OUTPUT_CHARS, maxOutputChars)),
				)
			: MAX_COMMAND_OUTPUT_CHARS;
	let pending: Partial<
		Record<CommandProgressStream, { chunk: string; droppedChars: number }>
	> = {};
	let streamOrder: CommandProgressStream[] = [];
	let flushTimer: NodeJS.Timeout | undefined;
	let stopped = false;

	const clearFlushTimer = () => {
		if (!flushTimer) return;
		clearTimeout(flushTimer);
		flushTimer = undefined;
	};

	const flush = () => {
		clearFlushTimer();
		if (!emitUpdate) {
			pending = {};
			streamOrder = [];
			return;
		}
		const buffered = pending;
		const order = streamOrder;
		pending = {};
		streamOrder = [];
		for (const stream of order) {
			const entry = buffered[stream];
			if (!entry?.chunk) continue;
			const marker =
				entry.droppedChars > 0
					? `\u001b[0m[${entry.droppedChars} command-output characters skipped while streaming]\n`
					: "";
			const markerFits = marker.length < maxPendingChars;
			const chunk = markerFits
				? marker + entry.chunk.slice(-(maxPendingChars - marker.length))
				: entry.chunk.slice(-maxPendingChars);
			emitUpdate({
				stream,
				chunk,
				executionId,
				detachable: isDetachable(),
				...(entry.droppedChars > 0
					? { truncated: true, droppedChars: entry.droppedChars }
					: {}),
			});
		}
	};

	const scheduleFlush = () => {
		if (!emitUpdate || flushTimer || stopped) return;
		flushTimer = setTimeout(flush, COMMAND_PROGRESS_FLUSH_INTERVAL_MS);
		flushTimer.unref();
	};

	return {
		append(stream: CommandProgressStream, text: string): void {
			if (!emitUpdate || stopped || !text) return;
			let entry = pending[stream];
			if (!entry) {
				entry = { chunk: "", droppedChars: 0 };
				pending[stream] = entry;
				streamOrder.push(stream);
			}
			if (text.length >= maxPendingChars) {
				entry.droppedChars +=
					entry.chunk.length + text.length - maxPendingChars;
				entry.chunk = text.slice(-maxPendingChars);
			} else {
				const overflow = entry.chunk.length + text.length - maxPendingChars;
				if (overflow > 0) {
					entry.droppedChars += overflow;
					entry.chunk = entry.chunk.slice(overflow);
				}
				entry.chunk += text;
			}
			scheduleFlush();
		},
		flush,
		stop(options: { flush?: boolean } = {}): void {
			if (stopped) return;
			if (options.flush) {
				flush();
			} else {
				clearFlushTimer();
				pending = {};
				streamOrder = [];
			}
			stopped = true;
		},
	};
}

function scheduleDetachedLogCleanup(
	directory: string,
	retentionMs: number,
): void {
	const cleanupTimer = setTimeout(() => {
		void rm(directory, { recursive: true, force: true }).catch(() => undefined);
	}, retentionMs);
	cleanupTimer.unref();
}

function createDetachedLog(retentionMs: number, marker: DetachedCommandMarker) {
	const directory = mkdtempSync(join(tmpdir(), DETACHED_LOG_DIRECTORY_PREFIX));
	const path = join(directory, DETACHED_LOG_FILENAME);
	const activeCommandPath = join(
		directory,
		DETACHED_LOG_ACTIVE_COMMAND_FILENAME,
	);
	const completedAtPath = join(directory, DETACHED_LOG_COMPLETED_FILENAME);
	try {
		writeLifecycleMarker(activeCommandPath, `${JSON.stringify(marker)}\n`);
	} catch (error) {
		rmSync(directory, { recursive: true, force: true });
		throw error;
	}
	const stream = createWriteStream(path, { encoding: "utf8" });
	let bytesWritten = 0;
	let capped = false;
	let writable = true;
	let streamClosed = false;
	let completed = false;
	let cleanupScheduled = false;
	const scheduleCleanup = () => {
		if (cleanupScheduled) return;
		cleanupScheduled = true;
		scheduleDetachedLogCleanup(directory, retentionMs);
	};
	stream.on("error", () => {
		// The detached command has already released its caller. A late log write
		// failure must not become an unhandled stream error in the hub process.
		writable = false;
	});
	stream.once("close", () => {
		writable = false;
		streamClosed = true;
		if (completed) scheduleCleanup();
	});

	const complete = () => {
		if (completed) return;
		completed = true;
		try {
			writeLifecycleMarker(completedAtPath, String(Date.now()));
		} catch {
			// The startup reaper can fall back to the output timestamp if the
			// completion marker cannot be persisted.
		}
		try {
			rmSync(activeCommandPath, { force: true });
		} catch {
			// Cleanup remains best effort and must not surface from a stream event.
		}
		if (streamClosed) {
			scheduleCleanup();
			return;
		}
		writable = false;
		stream.end();
	};

	return {
		path,
		write(text: string): void {
			if (!text || capped || !writable) return;
			const remaining = MAX_DETACHED_LOG_BYTES - bytesWritten;
			if (remaining <= 0) {
				capped = true;
				writable = false;
				stream.end();
				return;
			}
			const data = Buffer.from(text, "utf8");
			const accepted =
				data.length <= remaining ? data : data.subarray(0, remaining);
			stream.write(accepted);
			bytesWritten += accepted.length;
			if (
				accepted.length < data.length ||
				bytesWritten >= MAX_DETACHED_LOG_BYTES
			) {
				capped = true;
				writable = false;
				stream.end();
			}
		},
		complete,
	};
}

function spawnAndCollect(
	config: SpawnConfig,
	context: AgentToolContext,
	timeoutMs: number,
	maxOutputChars: number,
	combineOutput: boolean,
	detachedLogRetentionMs: number,
	executionController?: RunCommandExecutionController,
	processStartTokenProbe: ProcessStartTokenProbe = probeProcessStartTokenAsync,
): Promise<string> {
	if (context.signal?.aborted) {
		return Promise.reject(new Error("Command was aborted"));
	}
	return new Promise((resolve, reject) => {
		const isWindows = process.platform === "win32";

		const child = spawn(config.executable, config.args, {
			cwd: config.cwd,
			env: { ...process.env, ...config.env },
			stdio: ["pipe", "pipe", "pipe"],
			detached: !isWindows,
			// Prevent a console window from flashing on Windows when the
			// parent process has no console (or a different console).
			// No-op on non-Windows platforms.
			windowsHide: true,
		});
		const childPid = child.pid;

		const stdout = createRollingCollector(maxOutputChars);
		const stderr = createRollingCollector(maxOutputChars);
		const executionId = randomUUID();
		const supportsDetachment = Boolean(
			executionController && context.sessionId,
		);
		let detachable = false;
		let processStartToken: string | undefined;
		let killed = false;
		let settled = false;
		let settledOnExitGrace = false;
		let exitStreamGraceTimer: NodeJS.Timeout | undefined;
		let detached = false;
		let detachedLog: ReturnType<typeof createDetachedLog> | undefined;
		let unregisterExecution = () => {};
		const progress = createCommandProgressEmitter(
			context,
			executionId,
			() => detachable,
			maxOutputChars,
		);

		const settle = (fn: () => void) => {
			if (settled) return;
			settled = true;
			fn();
		};

		const killProcessTree = async (): Promise<void> => {
			if (!childPid) return;
			if (isWindows) {
				await new Promise<void>((done) => {
					let finished = false;
					let killer: ReturnType<typeof spawn>;
					const finish = () => {
						if (finished) return;
						finished = true;
						clearTimeout(watchdog);
						done();
					};
					try {
						killer = spawn(
							"taskkill.exe",
							["/PID", String(childPid), "/T", "/F"],
							{ stdio: "ignore", shell: false, windowsHide: true },
						);
					} catch {
						child.kill();
						done();
						return;
					}
					const watchdog = setTimeout(() => {
						killer.kill();
						child.kill();
						finish();
					}, 5_000);
					killer.once("error", () => {
						child.kill();
						finish();
					});
					killer.once("close", (code) => {
						if (code !== 0) child.kill();
						finish();
					});
				});
				return;
			}
			try {
				process.kill(-childPid, "SIGKILL");
			} catch {
				child.kill("SIGKILL");
			}
		};

		let timeout: NodeJS.Timeout;
		const abortHandler = () => killAndReject(new Error("Command was aborted"));
		const cleanup = () => {
			clearTimeout(timeout);
			if (exitStreamGraceTimer) {
				clearTimeout(exitStreamGraceTimer);
			}
			context.signal?.removeEventListener("abort", abortHandler);
			unregisterExecution();
		};
		const killAndReject = (error: Error) => {
			if (killed || settled) return;
			killed = true;
			progress.stop({ flush: true });
			cleanup();
			void killProcessTree().finally(() => settle(() => reject(error)));
		};

		timeout = setTimeout(
			() =>
				killAndReject(
					new TimeoutError(`Command timed out after ${timeoutMs}ms`, timeoutMs),
				),
			timeoutMs,
		);

		if (context.signal) {
			context.signal.addEventListener("abort", abortHandler, { once: true });
			if (context.signal.aborted) abortHandler();
		}

		const detach = (): boolean => {
			if (
				!detachable ||
				killed ||
				settled ||
				!childPid ||
				child.exitCode !== null ||
				child.signalCode !== null
			) {
				return false;
			}
			if (!processStartToken) return false;
			const log = createDetachedLog(detachedLogRetentionMs, {
				version: 1,
				executionId,
				pid: childPid,
				processStartToken,
				detachedAtMs: Date.now(),
			});
			progress.stop({ flush: true });
			detached = true;
			detachedLog = log;
			const currentOut = stdout.current();
			const currentErr = stderr.current();
			detachedLog.write(currentOut.text);
			if (currentErr.text) {
				detachedLog.write(`\n[stderr]\n${currentErr.text}`);
			}
			cleanup();
			child.unref();
			(
				child.stdout as (typeof child.stdout & { unref?: () => void }) | null
			)?.unref?.();
			(
				child.stderr as (typeof child.stderr & { unref?: () => void }) | null
			)?.unref?.();
			const notice = [
				`[Command is still running. Output will continue in ${detachedLog.path}]`,
				combineOutput
					? currentOut.text +
						(currentErr.text ? `\n[stderr]\n${currentErr.text}` : "")
					: currentOut.text,
			]
				.filter(Boolean)
				.join("\n");
			settle(() => resolve(notice));
			return true;
		};

		child.once("spawn", () => {
			if (killed) return;
			context.emitUpdate?.({
				stream: "stdout",
				chunk: "",
				executionId,
				detachable: false,
			});
			const sessionId = context.sessionId;
			if (
				!supportsDetachment ||
				!childPid ||
				!sessionId ||
				!executionController
			) {
				return;
			}
			void (async () => {
				const probe = await processStartTokenProbe(childPid);
				if (
					probe.status !== "found" ||
					killed ||
					settled ||
					child.exitCode !== null ||
					child.signalCode !== null
				) {
					return;
				}
				processStartToken = probe.token;
				detachable = true;
				unregisterExecution = executionController.register({
					executionId,
					sessionId,
					toolCallId: context.toolCallId,
					detach,
				});
				context.emitUpdate?.({
					stream: "stdout",
					chunk: "",
					executionId,
					detachable: true,
				});
			})().catch(() => undefined);
		});

		child.stdout?.on("data", (data: Buffer) => {
			const chunk = stdout.append(data);
			if (!chunk) return;
			if (detached) {
				detachedLog?.write(chunk);
				return;
			}
			progress.append("stdout", chunk);
		});

		child.stderr?.on("data", (data: Buffer) => {
			const chunk = stderr.append(data);
			if (!chunk) return;
			if (detached) {
				detachedLog?.write(chunk);
				return;
			}
			progress.append("stderr", chunk);
		});

		// Shared completion path for 'close' (stdio drained) and the
		// exit-grace fallback below: snapshot the collectors, flush the final
		// decoder chunks, and settle with the exit code or signal. `trailingNote` is
		// appended after truncation so it always reaches the caller.
		const completeCommand = (
			code: number | null,
			signal: NodeJS.Signals | null,
			trailingNote: string,
		) => {
			const out = stdout.snapshot();
			const err = stderr.snapshot();
			if (out.finalChunk) {
				progress.append("stdout", out.finalChunk);
			}
			if (err.finalChunk) {
				progress.append("stderr", err.finalChunk);
			}
			progress.stop({ flush: true });
			cleanup();

			if (code !== 0) {
				let failureOutput = combineOutput
					? out.text + (err.text ? `\n[stderr]\n${err.text}` : "")
					: out.text;
				const dropped = out.dropped || (combineOutput && err.dropped);
				const totalChars = combineOutput
					? out.totalChars + err.totalChars
					: out.totalChars;
				if (dropped || failureOutput.length > maxOutputChars) {
					failureOutput = truncateCommandOutput(failureOutput, {
						maxChars: maxOutputChars,
						totalChars,
					});
				}
				const notice = `[${commandCompletionMessage(code, signal)}]`;
				const result =
					failureOutput.length > 0 ? `${notice}\n${failureOutput}` : notice;
				const output = trailingNote ? `${result}\n${trailingNote}` : result;
				settle(() =>
					reject(
						code === null
							? new CommandTerminationError(signal, output)
							: new CommandExitError(code, output),
					),
				);
			} else {
				let output = combineOutput
					? out.text + (err.text ? `\n[stderr]\n${err.text}` : "")
					: out.text;
				const dropped = out.dropped || (combineOutput && err.dropped);
				if (dropped || output.length > maxOutputChars) {
					const totalChars = combineOutput
						? out.totalChars + err.totalChars
						: out.totalChars;
					output = truncateCommandOutput(output, {
						maxChars: maxOutputChars,
						totalChars,
					});
				}
				settle(() =>
					resolve(trailingNote ? `${output}\n${trailingNote}` : output),
				);
			}
		};

		child.on("close", (code, signal) => {
			if (killed || settledOnExitGrace) return;

			const out = stdout.snapshot();
			const err = stderr.snapshot();
			if (detached) {
				// The log is finalized here; the exit-grace timer must not
				// double-write it later.
				if (exitStreamGraceTimer) {
					clearTimeout(exitStreamGraceTimer);
				}
				detachedLog?.write(out.finalChunk);
				detachedLog?.write(err.finalChunk);
				detachedLog?.write(`\n[${commandCompletionMessage(code, signal)}]\n`);
				detachedLog?.complete();
				return;
			}
			completeCommand(code, signal ?? null, "");
		});

		// 'close' fires only once the stdio streams have drained. A command
		// that backgrounds a child (`cmd &`, Start-Process, nohup) leaves the
		// inherited pipe write-ends held open, so after the shell itself exits
		// 'close' never arrives and the command would hang until the timeout
		// kills the whole tree — even though the command, like a prompt
		// returning in an interactive terminal, actually finished (GitHub
		// #12417). When the process has exited and the streams stay open past
		// a grace period, settle with the exit code and the output collected
		// so far, and release the stream handles so the host process is not
		// kept alive by the orphaned pipes. Output still trickling in after
		// the grace belongs to the background processes and is intentionally
		// not awaited; a command whose own final output needs more than the
		// grace to drain after exit is rare, and its collected output is
		// preserved up to that point. A detached command gets the same
		// treatment for its log: without this, a detached shell that exits
		// while a descendant holds the pipes would leave the log forever in
		// the active state, waiting for a 'close' that never comes.
		child.on("exit", (code, signal) => {
			if (killed) return;
			exitStreamGraceTimer = setTimeout(() => {
				if (killed || settledOnExitGrace) return;
				if (detached) {
					// Write the exit record and complete the log here instead
					// of leaving it for the startup reaper to retire as stale.
					const out = stdout.snapshot();
					const err = stderr.snapshot();
					detachedLog?.write(out.finalChunk);
					detachedLog?.write(err.finalChunk);
					detachedLog?.write(`\n[${commandCompletionMessage(code, signal)}]\n`);
					detachedLog?.complete();
					return;
				}
				if (settled) return;
				settledOnExitGrace = true;
				(
					child.stdout as (typeof child.stdout & { unref?: () => void }) | null
				)?.unref?.();
				(
					child.stderr as (typeof child.stderr & { unref?: () => void }) | null
				)?.unref?.();
				child.unref();
				completeCommand(
					code,
					signal ?? null,
					"[Command completed with background processes still running; their output is no longer captured]",
				);
			}, EXIT_STREAM_GRACE_MS);
		});

		child.on("error", (error) => {
			if (killed) return;
			if (detached) {
				detachedLog?.write(`\n[Command failed: ${error.message}]\n`);
				detachedLog?.complete();
				return;
			}
			progress.stop({ flush: true });
			cleanup();
			settle(() => reject(new CommandSpawnError(error, { cwd: config.cwd })));
		});

		child.stdin?.on("error", (error) => {
			if (killed || settled) return;
			killAndReject(
				new Error(`Failed to write command input: ${error.message}`),
			);
		});
		child.stdin?.end(config.input, "utf8");
	});
}

/**
 * Create a shell executor using Node.js spawn
 *
 * @example
 * ```typescript
 * const shell = createShellExecutor({
 *   timeoutMs: 60000, // 1 minute timeout
 *   shell: "/bin/zsh",
 * })
 *
 * const output = await shell("ls -la", "/path/to/project", context)
 * ```
 */
export function createShellExecutor(
	options: ShellExecutorOptions = {},
): ShellExecutor {
	const {
		shell = getDefaultShell(process.platform),
		timeoutMs = 30000,
		env = {},
		combineOutput = true,
		executionController,
		processStartTokenProbe = probeProcessStartTokenAsync,
	} = options;
	const detachedLogRetentionMs = resolveDetachedLogRetentionMs(
		options.detachedLogRetentionMs,
	);
	const maxOutputChars =
		options.maxOutputChars ??
		options.maxOutputBytes ??
		MAX_COMMAND_OUTPUT_CHARS;

	return (command, cwd, context) => {
		// Spawn without a shell only when the args key is present (even
		// empty), marking input the caller already split. An object with no
		// args key, like { command: "echo hello" }, holds a full command
		// line, and spawning that string as an executable fails with ENOENT.
		// Same key-presence rule as the VS Code host's formatCommandForTerminal.
		const directExec = typeof command !== "string" && "args" in command;
		const invocation = directExec
			? { executable: command.command, args: command.args ?? [] }
			: getShellInvocation(
					shell,
					typeof command === "string" ? command : command.command,
				);
		return spawnAndCollect(
			{
				executable: invocation.executable,
				args: invocation.args,
				cwd,
				env,
				input: invocation.input,
			},
			context,
			timeoutMs,
			maxOutputChars,
			combineOutput,
			detachedLogRetentionMs,
			executionController,
			processStartTokenProbe,
		);
	};
}

````

## sdk/packages/core/src/extensions/tools/executors/output-limits.ts

Source: https://github.com/cline/cline/blob/34c104e6fc49781cda4adcd73ffdccd8f029fef9/sdk/packages/core/src/extensions/tools/executors/output-limits.ts

SHA-256: `09fa2788b6a610176e1f3a2da2a144822716bf29ed8202ec241c69e29bdbf775`

```
/**
 * Shared caps for how much tool output may enter the conversation. Every
 * character returned by an executor is re-sent to the model on each
 * subsequent request, so oversized outputs cost quadratically over the
 * remaining run. Limits are measured in characters (UTF-16 code units),
 * which tracks token cost more closely than bytes and is what JS strings
 * measure exactly. Executors enforce these caps; tool descriptions
 * reference them so the model pages or narrows instead of retrying.
 *
 * Truncation notices always live in the preserved head/tail of an entry,
 * never in the elided middle. Provider-request building may re-truncate
 * long strings with its own middle-cut backstop
 * (session/services/message-builder.ts) — tighter than these caps for
 * command and search output, looser for file reads so a read window
 * reaches the model as the footer describes it; keeping the notices at
 * the edges means the recovery guidance survives that cut too.
 */

/** Max characters of command output kept; beyond this the middle is elided. */
export const MAX_COMMAND_OUTPUT_CHARS = 48_000;

export function truncateCommandOutput(
	text: string,
	options: { maxChars?: number; totalChars?: number } = {},
): string {
	const maxChars = options.maxChars ?? MAX_COMMAND_OUTPUT_CHARS;
	const totalChars = options.totalChars ?? text.length;
	if (text.length <= maxChars && totalChars <= maxChars) {
		return text;
	}

	const headLimit = Math.ceil(maxChars / 2);
	const tailLimit = Math.max(1, maxChars - headLimit);
	return (
		`${text.slice(0, headLimit)}\n` +
		`[... output truncated: ${totalChars} chars total. ` +
		"Refine the command (grep, head, tail) to view the elided middle ...]\n" +
		text.slice(-tailLimit)
	);
}

/** Max lines returned per file read when the range is larger or absent. */
export const MAX_READ_LINES = 2_000;

/** Max characters kept per line in file reads (defangs minified files). */
export const MAX_LINE_CHARS = 2_000;

/** Max characters returned per file read window. */
export const MAX_READ_OUTPUT_CHARS = 48_000;

/** Max characters returned per search query; beyond this the middle is elided. */
export const MAX_SEARCH_OUTPUT_CHARS = 48_000;

```

## sdk/packages/core/src/extensions/tools/team/delegated-agent.ts

Source: https://github.com/cline/cline/blob/34c104e6fc49781cda4adcd73ffdccd8f029fef9/sdk/packages/core/src/extensions/tools/team/delegated-agent.ts

SHA-256: `e5913bafc45efc9328ac05b9da9dd83a0e1bf560bccbaf1cfa980a642378c371`

```
import type {
	AgentConfig,
	AgentEvent,
	AgentHooks,
	AgentTool,
	BasicLogger,
	HookErrorMode,
	ITelemetryService,
	ToolApprovalRequest,
	ToolApprovalResult,
} from "@cline/shared";
import { SessionRuntime } from "../../../runtime/orchestration/session-runtime-orchestrator";
import {
	buildSubAgentSystemPrompt,
	buildTeammateSystemPrompt,
} from "./subagent-prompts";

type AgentExtension = NonNullable<AgentConfig["extensions"]>[number];

export type DelegatedAgentConnectionConfig = Pick<
	AgentConfig,
	| "providerId"
	| "modelId"
	| "apiKey"
	| "baseUrl"
	| "headers"
	| "onAuthError"
	| "providerConfig"
	| "knownModels"
	| "thinking"
	| "reasoningEffort"
	| "thinkingBudgetTokens"
	| "maxTokensPerTurn"
	| "temperature"
>;

export interface DelegatedAgentRuntimeConfig
	extends DelegatedAgentConnectionConfig {
	cwd?: string;
	providerId: string;
	clinePlatform?: string;
	clineIdeName?: string;
	maxIterations?: number;
	hooks?: AgentHooks;
	extensions?: AgentExtension[];
	logger?: BasicLogger;
	telemetry?: ITelemetryService;
	workspaceMetadata?: string;
	/**
	 * Stable end-user identity inherited from the parent session so
	 * delegated-agent telemetry (Langfuse `userId`) groups with the user.
	 */
	distinctId?: string;
	/**
	 * Root core session id inherited from the parent session so
	 * delegated-agent telemetry (Langfuse `sessionId`) groups with it.
	 */
	sessionId?: string;
}

export interface DelegatedAgentConfigProvider {
	getRuntimeConfig(): DelegatedAgentRuntimeConfig;
	getConnectionConfig(): DelegatedAgentConnectionConfig;
	updateConnectionDefaults(
		overrides: Partial<DelegatedAgentConnectionConfig>,
	): void;
}

export type DelegatedAgentKind = "subagent" | "teammate";

export interface BuildDelegatedAgentConfigOptions {
	kind: DelegatedAgentKind;
	prompt: string;
	tools: AgentTool[];
	configProvider: DelegatedAgentConfigProvider;
	parentAgentId?: string;
	maxIterations?: number;
	abortSignal?: AbortSignal;
	onEvent?: (event: AgentEvent) => void;
	hookErrorMode?: HookErrorMode;
	toolPolicies?: AgentConfig["toolPolicies"];
	requestToolApproval?: (
		request: ToolApprovalRequest,
	) => Promise<ToolApprovalResult> | ToolApprovalResult;
	role?: string;
	cwd?: string;
}

export function createDelegatedAgentConfigProvider(
	initialConfig: DelegatedAgentRuntimeConfig,
): DelegatedAgentConfigProvider {
	let runtimeConfig: DelegatedAgentRuntimeConfig = { ...initialConfig };

	return {
		getRuntimeConfig: () => runtimeConfig,
		getConnectionConfig: () => ({
			providerId: runtimeConfig.providerId,
			modelId: runtimeConfig.modelId,
			apiKey: runtimeConfig.apiKey,
			baseUrl: runtimeConfig.baseUrl,
			headers: runtimeConfig.headers,
			onAuthError: runtimeConfig.onAuthError,
			providerConfig: runtimeConfig.providerConfig,
			knownModels: runtimeConfig.knownModels,
			thinking: runtimeConfig.thinking,
			reasoningEffort: runtimeConfig.reasoningEffort,
			thinkingBudgetTokens: runtimeConfig.thinkingBudgetTokens,
			maxTokensPerTurn: runtimeConfig.maxTokensPerTurn,
			temperature: runtimeConfig.temperature,
		}),
		updateConnectionDefaults: (overrides) => {
			runtimeConfig = {
				...runtimeConfig,
				...overrides,
			};
		},
	};
}

export function buildDelegatedAgentConfig(
	options: BuildDelegatedAgentConfigOptions,
): AgentConfig & { role?: string } {
	const runtimeConfig = options.configProvider.getRuntimeConfig();
	const systemPrompt =
		options.kind === "teammate"
			? buildTeammateSystemPrompt(options.prompt, runtimeConfig)
			: buildSubAgentSystemPrompt(options.prompt, runtimeConfig);

	return {
		...options.configProvider.getConnectionConfig(),
		distinctId: runtimeConfig.distinctId,
		sessionId: runtimeConfig.sessionId,
		systemPrompt,
		tools: options.tools,
		maxIterations: options.maxIterations ?? runtimeConfig.maxIterations,
		parentAgentId: options.parentAgentId,
		abortSignal: options.abortSignal,
		onEvent: options.onEvent,
		hooks: runtimeConfig.hooks,
		extensions: runtimeConfig.extensions,
		hookErrorMode: options.hookErrorMode,
		toolPolicies: options.toolPolicies,
		requestToolApproval: options.requestToolApproval,
		logger: runtimeConfig.logger,
		role: options.role,
	};
}

export function createDelegatedAgent(
	options: BuildDelegatedAgentConfigOptions,
): SessionRuntime {
	const config = buildDelegatedAgentConfig(options);
	const session = new SessionRuntime(config);
	if (config.onEvent) {
		session.subscribeEvents(config.onEvent);
	}
	return session;
}

```
