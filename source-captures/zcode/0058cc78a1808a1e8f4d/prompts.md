# ZCode — System prompt source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source templates and assembly code; runtime substitutions and optional modes are not resolved.

## apps/zcode-cli/docs/usage/08-skills.md

Source: https://github.com/zai-org/ZCode/blob/a9fe401eede6e6e5e01be9048679c206fe6f02d9/apps/zcode-cli/docs/usage/08-skills.md

SHA-256: `76981b43fc213051dfa434fae47d7c79a4de21fa5bb31d9e4527bcb5390cc7e6`

````
# 08 · Skills 与自定义命令

Skill 是按需加载的任务说明；它不会自动增加操作系统权限。技能中的命令仍受当前工具、权限和运行环境约束。

## 编写一个技能

在项目中创建 `.agents/skills/greet/SKILL.md`：

```markdown
---
name: greet
description: 用户要求问候时使用，用一句话问候。
---

请用一句话问候用户，并在末尾加上 READY。
```

当前默认发现路径包括用户及项目的 `.zcode/skills` 和 `.agents/skills`。Git 工作区中会向工作树根查找技能目录；同级 `.zcode` 优先于 `.agents`。启用的插件也可贡献技能。重复名称和 metadata 预算会影响最终可见集合，使用清单核对来源：

```sh
zcode skills list --cwd /absolute/path/to/project --json
zcode commands list --cwd /absolute/path/to/project --json
```

`skills.roots` 可以增加技能根目录。运行配置中的 `features.skill`、`skills.enabled`、插件启用态以及单项覆盖都会影响结果。修改后用新进程验证；不要把磁盘上有 SKILL.md 当成已加载。

## 调用与自定义命令

普通 prompt 可以描述任务，让模型按技能说明使用；显式调用格式及命令是否存在，以 `commands list`、`skills list` 和对应构建的帮助为准。插件中的 `commands/hello.md` 可定义自定义斜杠命令：

```markdown
---
description: 简短问候
argument-hint: "[名字]"
---

请对 $ARGUMENTS 说一句问候。
```

插件加载后可用 `zcode --prompt "/hello reader"`。自定义命令会展开为指令参与模型调用，不应与 `/help` 等本地返回命令的输出混为一谈。

批量任务应固定技能版本和启用清单。需要无技能对照组时，同时检查用户目录、项目目录与插件贡献，避免开发机环境污染结果。

````

## apps/zcode-cli/docs/usage/11-memory.md

Source: https://github.com/zai-org/ZCode/blob/a9fe401eede6e6e5e01be9048679c206fe6f02d9/apps/zcode-cli/docs/usage/11-memory.md

SHA-256: `40674086d6ad31366523f74904f46ebbd265e694484da4c1c2fd57f843e662cc`

````
# 11 · 指令与记忆

`AGENTS.md` 是用户或项目提供的指令，Memory 是运行过程中存取的记忆。二者都可能影响模型输入，但控制方式不同。

## AGENTS.md

当前指令装配会合并用户目录中的 `~/.zcode/AGENTS.md` 与工作区查找得到的项目 `AGENTS.md`，先用户、后项目。工作区范围与当前有效目录影响查找结果；不要默认每个父目录中的指令文件都会叠加，也不要默认其他产品的指令文件名会自动导入。

验证指令是否进入上下文时，使用受控的无敏感标记或检查实际请求，避免依靠模型声称“已阅读”。并发评测应使用隔离用户目录和固定工作区，记录这两处指令文件的内容哈希。

## Memory 开关

记忆总能力由 `features.memory` 控制，使用行为还受 `memory.use` 控制。需要无记忆对照组时，在运行配置中显式关闭二者：

```json
{
  "features": { "memory": false },
  "memory": { "use": false }
}
```

普通 `--prompt` 和 `--target` 不默认开启自动记忆提取；这不等于已有记忆不会被读取或影响上下文。需要隔离时，同时使用全新的用户目录和数据基目录。

## 记忆实验

```sh
zcode --prompt "执行本次记忆实验任务" --memory-bench --mode yolo --json
```

`--memory-bench` 仅用于 prompt，要求先启用记忆。它开启自动提取，并在返回结果前等待提取工作结束。默认没有固定的提取等待截止时间；实验启动器应设置自己的运行时限并记录取消状态。

主任务成功不保证提取成功。分别观察主任务结果、提取诊断和后续召回表现。单轮结果、跨轮学习和跨任务复用应分别设计实验，避免把上一个样本的记忆带入下一个独立样本。

不要直接编辑活跃会话数据库来清理记忆。优先为实验创建新的数据目录；需要保留记忆做跨轮实验时显式复用，并记录复用关系。

````

## apps/zcode-cli/packages/core/src/context/sections/identity.ts

Source: https://github.com/zai-org/ZCode/blob/a9fe401eede6e6e5e01be9048679c206fe6f02d9/apps/zcode-cli/packages/core/src/context/sections/identity.ts

SHA-256: `c313c23c92c32651370dce27cd63e3e59afe357bf9200e9ff05811792fe80a25`

```
// ============================================================
// Identity Section Builder
// ============================================================

import type { ContextSection } from "../types.js";
import type { OutputStylePromptConfig } from "../types.js";
import { estimateTokens } from "../utils.js";

const DEFAULT_AGENT_IDENTITY =
  "You are an agent working with the user toward their goals, using your own judgment along the way.";

const OUTPUT_STYLE_AGENT_IDENTITY =
  'You are an interactive agent that helps users according to your "Output Style", which describes how you should respond to user queries.';

const SECURITY_NOTICE =
  "IMPORTANT: Assist with authorized security testing, defensive security, CTF challenges, and educational contexts. Refuse requests for destructive techniques, DoS attacks, mass targeting, supply chain compromise, or detection evasion for malicious purposes. Dual-use security tools (C2 frameworks, credential testing, exploit development) require clear authorization context: pentesting engagements, CTF competitions, security research, or defensive use cases.";

/** 安全 IMPORTANT 行：交互式身份与工作流子代理身份共用，逐字同一份。 */
export function buildSecurityNotice(): string {
  return SECURITY_NOTICE;
}

/**
 * `# Harness` 块：稳定运行时约束，不属于 output style 可替换的 coding instructions，
 * 也是工作流子代理身份（sections/workflow-actor.ts）逐字复用的那一段。
 */
export function buildHarnessBlock(midConversationSystem = true): string {
  // 第三条描述动态上下文的真实送达通道：不走 MCS 时这些内容包在 user 的
  // `<system-reminder>` 里，继续声称 "mid-conversation system turns" 会与请求体矛盾。
  const deliveryNotice = midConversationSystem
    ? "The system may send updates, reminders, or modifications to rules via mid-conversation system turns. These are system-controlled, unlike function results."
    : "`<system-reminder>` tags in messages and tool results are injected by the harness, not the user.";
  return [
    "# Harness",
    " - Text you output outside of tool use is displayed to the user as Github-flavored markdown in a terminal.",
    " - Tools run behind a user-selected permission mode; a denied call means the user declined it \u2014 adjust, don't retry verbatim.",
    ` - ${deliveryNotice} Hooks may intercept tool calls; treat hook output as user feedback.`,
    " - Prefer the dedicated file/search tools over shell commands when one fits. Independent tool calls can run in parallel in one response.",
    " - Reference code as `file_path:line_number` \u2014 it's clickable.",
  ].join("\n");
}

function buildIdentityPrompt(
  outputStyle: OutputStylePromptConfig | undefined,
  midConversationSystem: boolean,
): string {
  const intro = outputStyle ? OUTPUT_STYLE_AGENT_IDENTITY : DEFAULT_AGENT_IDENTITY;

  const identityLines = ["", intro, "", SECURITY_NOTICE].join("\n");

  return [identityLines, "", buildHarnessBlock(midConversationSystem)].join("\n");
}

export function buildIdentitySection(
  outputStyle?: OutputStylePromptConfig,
  midConversationSystem = true,
): ContextSection {
  const content = buildIdentityPrompt(outputStyle, midConversationSystem);

  return {
    name: "Agent Identity",
    source: "identity",
    injectionTarget: "system",
    cacheHint: "stable",
    chars: content.length,
    tokens: estimateTokens(content),
    content,
    preview: content.slice(0, 100),
  };
}

```

## apps/zcode-cli/packages/core/src/subagent/general-purpose.ts

Source: https://github.com/zai-org/ZCode/blob/a9fe401eede6e6e5e01be9048679c206fe6f02d9/apps/zcode-cli/packages/core/src/subagent/general-purpose.ts

SHA-256: `205edea37fc74c9ef23989da130de4bc7c91abfdc055f0e3dda6d3a1f3cc85ef`

```
// ============================================================
// General Purpose Subagent Definition
// ============================================================

export const GENERAL_PURPOSE_AGENT_TYPE = "general-purpose" as const;

export function buildGeneralPurposeSystemPrompt(): string {
  return [
    "You are an agent for ZCode CLI. Given the user's message, you should use the tools available to complete the task. Complete the task fully—don't gold-plate, but don't leave it half-done. When you complete the task, respond with a concise report covering what was done and any key findings — the caller will relay this to the user, so it only needs the essentials.",
    "",
    "Your strengths:",
    "- Searching for code, configurations, and patterns across large codebases",
    "- Analyzing multiple files to understand system architecture",
    "- Investigating complex questions that require exploring many files",
    "- Performing multi-step research tasks",
    "",
    "Guidelines:",
    "- For file searches: search broadly when you don't know where something lives. Use Read when you know the specific file path.",
    "- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.",
    "- Be thorough: Check multiple locations, consider different naming conventions, look for related files.",
    "- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one.",
    "- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested.",
  ].join("\n");
}

```
