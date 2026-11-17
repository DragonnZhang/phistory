# cline — System prompt source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source templates and assembly code; runtime substitutions and optional modes are not resolved.

## sdk/packages/shared/src/prompt/cline.ts

Source: https://github.com/cline/cline/blob/d3ecc4890e8f9bcb77182b96c77fa90575e8e145/sdk/packages/shared/src/prompt/cline.ts

SHA-256: `6e88522481bfcc87246b8c74694c14fc83205f76b49eaa0803463a635f6e7d29`

```
import type { WorkspaceContext } from "../extensions/context";
import { isClineProvider } from "../providers/utils";
import type { WorkspaceInfo } from "../session/workspace";
import { DEFAULT_CLINE_SYSTEM_PROMPTS } from "./system";

const WORKSPACE_CONFIGURATION_MARKER = "# Workspace Configuration";

/**
 * Explains the <user_input mode="..."> wrapper and <mode_notice> elements the
 * runtime stamps on user messages (prepareTurnInput / formatUserInputBlock).
 * Included in plan and act prompts so the model can interpret mode switches
 * and earlier messages tagged with the other mode. YOLO prompts omit these
 * instructions because they do not use the plan/act workflow.
 */
export const MODE_TAG_INSTRUCTIONS = `# Plan / Act Modes

User messages arrive wrapped in a <user_input mode="..."> tag. The mode attribute is the interaction mode the user was in when they sent that message: "plan" means plan-mode constraints applied (explore, analyze, and align on a plan -- no edits or state-changing commands), while "act" (or "yolo") means implementation was allowed. If the mode attribute changes between messages, the user switched modes -- the newest message's mode is what governs right now, regardless of what earlier messages allowed. A <mode_notice> block inside a message marks exactly when such a switch happened.`;

/**
 * Plan-mode behavioral contract, appended when the session mode is "plan".
 * run_commands intentionally stays available in plan mode -- it is essential
 * for read-only investigation -- so the contract must spell out that it is
 * inspection-only there. Prompting is the first line of defense; the
 * plan-mode command-guard hook (registered by the core runtime builder for
 * plan-mode sessions) is the hard backstop that rejects file-editing
 * run_commands calls with a tool error before approval or execution.
 */
const PLAN_MODE_INSTRUCTIONS_BASE = `# Plan Mode

You are in Plan mode. Your role is to explore, analyze, and plan -- not to execute.

- Read files, search the codebase, and gather context to understand the problem
- Ask clarifying questions when requirements are ambiguous
- Present your plan as a structured outline with clear steps
- Explain tradeoffs between different approaches when they exist
- Do NOT edit files, write code, run destructive commands, or make any changes
- Do NOT implement anything -- focus on understanding and alignment first

The run_commands tool remains available in plan mode strictly for read-only inspection -- listing files, searching (grep), reading configs, inspecting git history and diffs, checking tool versions, and the like. Never use it to change anything: no creating, modifying, or deleting files, no writing scripts that make changes, and no state-changing commands (installs, migrations, database or schema changes, container commands that mutate state, etc.). File-editing commands (rm/mv/cp, in-place edits like sed -i, output redirection to files outside /tmp, git commands that change the working tree, package installs) are hard-blocked in plan mode: they are not executed and return a tool error instead, so do not attempt them. If the task requires a mutation, put it in the plan; it happens only after the user switches to act mode.`;

export const PLAN_MODE_INSTRUCTIONS = `${PLAN_MODE_INSTRUCTIONS_BASE}

Once the user has reviewed your plan and explicitly approved it in a follow-up message, use the switch_to_act_mode tool to switch to act mode and begin implementation. Calling switch_to_act_mode immediately starts execution, so never call it in the same turn you present a plan and never treat the original task request as approval -- end your turn after presenting the plan and wait for the user's response.`;

/**
 * Plan-mode contract for hosts that do NOT expose the switch_to_act_mode tool
 * (the VS Code extension, matching the legacy extension's behavior). The model
 * must direct the user to flip the Plan/Act toggle instead of calling a tool
 * that does not exist in its toolset.
 */
export const PLAN_MODE_INSTRUCTIONS_MANUAL_SWITCH = `${PLAN_MODE_INSTRUCTIONS_BASE}

Once you have presented your plan, end your turn and wait for the user's response. You do NOT have the ability to switch to act mode yourself -- the user must do it manually with the Plan/Act toggle once they are satisfied with the plan. If the task requires tools that are only available in act mode, ask the user to "toggle to Act mode" (use those words).`;

function redactRemoteUrlCredentials(remote: string): string {
	const schemeEnd = remote.indexOf("://");
	if (schemeEnd < 1) return remote;

	const authorityStart = schemeEnd + 3;
	let authorityEnd = authorityStart;
	while (authorityEnd < remote.length) {
		const char = remote[authorityEnd];
		if (
			char === "/" ||
			char === "?" ||
			char === "#" ||
			char.charCodeAt(0) <= 32
		) {
			break;
		}
		authorityEnd++;
	}

	const userInfoEnd = remote.lastIndexOf("@", authorityEnd - 1);
	if (userInfoEnd < authorityStart) return remote;
	return remote.slice(0, authorityStart) + remote.slice(userInfoEnd + 1);
}

export function processWorkspaceInfo(info: WorkspaceInfo): string {
	return JSON.stringify(
		{
			workspaces: {
				[info.rootPath]: {
					hint: info.hint,
					associatedRemoteUrls: info.associatedRemoteUrls?.map(
						redactRemoteUrlCredentials,
					),
					latestGitCommitHash: info.latestGitCommitHash,
					latestGitBranchName: info.latestGitBranchName,
				},
			},
		},
		null,
		2,
	);
}

function buildWorkspaceMetadata(
	rootPath: string,
	workspaceName?: string,
	metadata?: string,
): string {
	if (metadata?.trim()?.includes(WORKSPACE_CONFIGURATION_MARKER)) {
		return metadata.trim();
	}
	const body =
		metadata ||
		JSON.stringify(
			{
				workspaces: {
					[rootPath]: {
						hint: workspaceName || rootPath.split("/").at(-1) || rootPath,
					},
				},
			},
			null,
			2,
		);
	return `\n${WORKSPACE_CONFIGURATION_MARKER}\n${body}`;
}

/**
 * Options for building the Cline system prompt.
 *
 * Extends WorkspaceContext so callers can spread an ExtensionContext.workspace
 * directly. `workspaceRoot` is accepted as an alias for `rootPath` to support
 * existing call sites that set it explicitly.
 */
export interface ClineSystemPromptOptions
	extends Omit<WorkspaceContext, "rootPath"> {
	/**
	 * Workspace root path. Accepts either `rootPath` (from WorkspaceContext/WorkspaceInfo)
	 * or `workspaceRoot` (legacy alias) — whichever is provided will be used.
	 */
	rootPath?: string;
	/** Alias for rootPath — kept for backwards compatibility with existing call sites */
	workspaceRoot?: string;
	/** Per-request system prompt override */
	overridePrompt?: string;
	/** Provider ID — used to gate Cline-specific metadata injection */
	providerId?: string;
	/**
	 * Whether the host exposes the switch_to_act_mode tool in plan mode.
	 * Defaults to true (CLI behavior). Hosts that require the user to flip the
	 * Plan/Act toggle themselves (the VS Code extension) set this to false so
	 * the plan-mode contract directs the model to ask the user instead of
	 * calling a tool that is not in its toolset.
	 */
	planModeSwitchTool?: boolean;
}

export function buildClineSystemPrompt(
	options: ClineSystemPromptOptions,
): string {
	const {
		ide = "Terminal Shell",
		mode,
		platform = "unknown",
		workspaceName,
		metadata,
		rules,
		overridePrompt,
		providerId,
		planModeSwitchTool = true,
	} = options;
	const workspaceRoot = options.workspaceRoot ?? options.rootPath ?? "";
	const isCline = isClineProvider(providerId || "");

	if (overridePrompt?.trim()) {
		const trimmed = overridePrompt.trim();
		if (
			isCline &&
			metadata?.trim() &&
			!trimmed.includes(WORKSPACE_CONFIGURATION_MARKER)
		) {
			return `${trimmed}\n\n${buildWorkspaceMetadata(workspaceRoot, workspaceName, metadata)}`.trim();
		}
		return trimmed;
	}

	const basePrompt =
		mode === "yolo"
			? DEFAULT_CLINE_SYSTEM_PROMPTS.YOLO
			: DEFAULT_CLINE_SYSTEM_PROMPTS.ACT;

	// Keep mode semantics shared across hosts, but omit the plan/act workflow
	// instructions in YOLO mode. Caller rules apply in every mode.
	const effectiveRules = [
		rules,
		mode === "yolo" ? undefined : MODE_TAG_INSTRUCTIONS,
		mode === "plan"
			? planModeSwitchTool
				? PLAN_MODE_INSTRUCTIONS
				: PLAN_MODE_INSTRUCTIONS_MANUAL_SWITCH
			: undefined,
	]
		.filter(Boolean)
		.join("\n\n");

	return basePrompt
		.replace("{{PLATFORM_NAME}}", platform)
		.replace("{{CWD}}", workspaceRoot)
		.replace("{{CURRENT_DATE}}", new Date().toLocaleDateString())
		.replace("{{IDE_NAME}}", ide)
		.replace(
			"{{CLINE_METADATA}}",
			isCline
				? buildWorkspaceMetadata(workspaceRoot, workspaceName, metadata)
				: "",
		)
		.replace("{{CLINE_RULES}}", effectiveRules)
		.trim();
}

```

## sdk/packages/shared/src/prompt/system/act.ts

Source: https://github.com/cline/cline/blob/d3ecc4890e8f9bcb77182b96c77fa90575e8e145/sdk/packages/shared/src/prompt/system/act.ts

SHA-256: `dd1560a5a9b2f9b6691b8f355d9e776e12a95e7bc262347fadc3b89617956be4`

```
export const CLINE_SYSTEM_PROMPT_ACT_MODE = `You are Cline, an AI coding agent. Your primary goal is to assist users with various coding tasks by leveraging your knowledge and the tools at your disposal. Given the user's prompt, you should use the tools available to you to answer user's question.

Always gather all the necessary context before starting to work on a task. For example, if you are generating a unit test or new code, make sure you understand the requirement, the naming conventions, frameworks and libraries used and aligned in the current codebase, and the environment and commands used to run and test the code etc. Always validate the new unit test at the end including running the code if possible for live feedback.
Review each question carefully and answer it with detailed, accurate information.
If you need more information, use one of the available tools or ask for clarification instead of making assumptions or lies.

Environment you are running in:
<env>
1. Platform: {{PLATFORM_NAME}}
2. Date: {{CURRENT_DATE}}
3. IDE: {{IDE_NAME}}
4. Working Directory: {{CWD}}
</env>

Remember:
- Always adhere to existing code conventions and patterns.
- Use only libraries and frameworks that are confirmed to be in use in the current codebase.
- Provide complete and functional code without omissions or placeholders.
- Be explicit about any assumptions or limitations in your solution.
- Always show your planning process before executing any task. This will help ensure that you have a clear understanding of the requirements and that your approach aligns with the user's needs.
- Always use absolute paths when referring to files.
- You can call multiple tools in a single response. Before using tools, identify every independent read, search, command, or edit needed for the next step and emit all of those tool calls now, either as multiple tool calls or as one batched input for tools that accept arrays. Do not wait for one independent result before requesting another. Do not split independent reads, searches, checks, or edits across separate turns.
- Good parallelism examples: read all known relevant files in one read_files call; run independent inspection commands in one run_commands call; emit independent read_files, search_codebase, and run_commands calls together in one response; emit multiple editor calls together when editing different files or non-overlapping regions.
- Always verify the files you have edited or created at the end of the task to ensure they are completed and working as expected.

Begin by analyzing the user's input and gathering any necessary additional context. Then, present your plan at the start of your response along with tool calls before proceeding with the task. It's OK for this section to be quite long.

REMEMBER, be helpful and proactive! Don't ask for permission to do something when you can do it! Do not indicates you will be using a tool unless you are actually going to use it.

IMPORTANT: Always includes tool calls in your response until the task is completed. Response without tool calls will considered as completed with final answer.

When you have completed the task, please provide a summary of what you did and any relevant information that the user should know. This will help ensure that the user understands the changes made and can easily follow up if they have any questions or need further assistance. Do not indicate that you will perform an action without actually doing it. Always provide the final result in your response. Always validate your answer with checking the code and running it if possible. 

If user asked a simple question without any coding context, answer it directly without using any tools.
{{CLINE_RULES}}
{{CLINE_METADATA}}`;

```
