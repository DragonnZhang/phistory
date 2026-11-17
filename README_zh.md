# Phistory

[English](README.md)

Phistory 追踪 Claude Code、Codex、Qwen Code、Qoder CLI、DeepSeek Harness、Antigravity、Grok Build、MiniMax Code、Kimi Code、MiMo Code、OpenClaw、Hermes、Kimi CLI、opencode、Pi、Oh My Pi 等热门 coding-agent CLI 的系统提示词如何随版本变化。

打开网页查看器，可以对比不同版本的提示词快照，从 prompts、tools、策略和运行时指令里观察 agent 设计如何变化。

**从这里开始：** [phistory.cc](https://phistory.cc/)

[Harness 源码归档](sources.html)保存 95 项调研中新增项目的提示词模板、装配代码与工具声明，保留完整原文件和来源，单独展示，不与真实请求捕获混用。

> 每天自动检查新版本，归档最近更新于 **2026-11-17 19:03 UTC**。

![Phistory prompt diff viewer](docs/screenshot.png)

## 为什么看它

- 观察 Anthropic、OpenAI 等团队如何持续迭代 system prompt。
- 看到新工具、权限检查、默认模型行为和用户确认规则是什么时候加入的。
- 对比不同 CLI 如何组织 agent 行为、工具调用和面向开发者的约束。
- 在文章、研究笔记、审计或排障记录里引用稳定的提示词快照。
- 默认对比主请求中的提示词、全部角色消息和工具；**System** 筛选顶层提示词及 system/developer 消息，**Messages** 按请求顺序对比全部消息。链接支持 `scope=system` / `scope=messages`。对比时归一化采集路径和明确标注的运行环境值，**Trace** 保留原始请求。版本选择器的变化条仅反映归档 `prompt.md`。

## 工作原理

Phistory 会安装每个受支持的具体 CLI 版本，再通过 [`claude-tap`](https://github.com/WEIFENG2333/claude-tap) 分别运行每个已配置快照，抓取包含系统提示词的 HTTP 请求，不调用真实模型服务，然后把结果保存到 `captures/<agent>/<version>/variants/<variant>/`，里面包含 `prompt.md`、`trace.jsonl` 和 `meta.json`。抓取配置以 `default` 快照为基线，显式选择的模型或模式会作为额外变体保存。

Claude Code 的 `default` 快照使用官方 API 路径且不指定模型；旧的非官方／自定义 API 路径保留为 `non-official`。固定模型的官方 API 快照按 Fable、Opus、Sonnet、Haiku 排列：在 `official-fable-5-1` 中固定使用 `claude-fable-5-1`，在 `official-fable` 中固定使用 `claude-fable-5`，在 `official-opus-5-5` 中固定使用 `claude-opus-5-5[1m]`，在 `official-opus` 中固定使用 `claude-opus-5[1m]`，在 `official-opus-4-8` 中固定使用 `claude-opus-4-8[1m]`，在 `official-opus-4-7` 中固定使用 `claude-opus-4-7[1m]`，在 `official-sonnet-5-5` 中固定使用 `claude-sonnet-5-5`，在 `official` 中固定使用 `claude-sonnet-5`，在 `official-haiku-5-5` 中固定使用 `claude-haiku-5-5`，在 `official-haiku` 中固定使用 `claude-haiku-4-5`。默认线路与固定模型线路都通过透明正向代理抓取，使 `ANTHROPIC_BASE_URL` 保持未设置。capture-only 模式会在本地返回虚拟响应，不会调用真实模型服务。这些通道的历史条目会让每个旧版 CLI 显式使用同一个模型，并不还原该版本发布时的官方默认模型。每条线路保留已验证的模型支持起点；Haiku 5.5 从最早已验证的 2.1.293 开始。另有需要组织验证的 Mythos 5.1 与 Mythos 5 线路，分别固定 `claude-mythos-5-1` 与 `claude-mythos-5`，从最早已验证的 2.1.296 开始；这些仅捕获请求的归档不验证账号访问资格。

Claude Code 抓取会固定设置 `DISABLE_GROWTHBOOK=1` 和 `DISABLE_TELEMETRY=1`，避免拉取远程灰度配置。同时设置 `CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off`，避免内部滚动任务预算提醒进入归档提示词。因此快照采用明确记录的确定性基线，而不是抓取当天的灰度状态；该基线会记录在 `meta.json` 中。

Phistory 还会从近期 Claude Code 安装包里提取疑似静态 prompt 的字符串，并从退役 Qoder 版本的精确官方可执行文件中提取 prompt 内容，保存在 `captures/<agent>/<version>/static/`。候选文件会保留原始内容，方便以后改进匹配规则时不用重新安装所有历史包。

Codex GPT-6 Sol 从已发布的 `0.157.0-alpha.10` 预览版开始归档；稳定版 `0.156.0` 尚未内置其模型元数据，详见[采集边界](docs/research/opus-5-5-gpt-6-sol.md)。

GitHub Actions 每天检查一次已自动追踪的 CLI 版本；发现新版本后，会自动抓取并提交新的提示词快照。

旧 Kimi CLI 的历史归档保留到 `1.51.0`。最终版 `1.52.0` 只输出迁移提示（[上游变更](https://github.com/MoonshotAI/kimi-cli/pull/2666)），因此已退出自动最新版本采集；新版 Kimi Code 继续独立追踪。

## 本地开发

日常查看直接使用托管网页：[phistory.cc](https://phistory.cc/)。下面这些命令主要用于本地开发、复现抓取、回填历史版本，以及重新生成项目里的生成文件。

```bash
# 安装锁定的开发环境。
uv sync --all-groups

# 在 Linux CI 中抓取每个仍在追踪的 CLI 的最新版本及其全部已配置快照。
uv run phistory capture --latest --agents claude-code,codex,qwen-code,dsh,antigravity,grok,minimax-code,kimi-code,mimo,openclaw,hermes,opencode,pi,omp

# 只抓取 Codex 的指定快照。
uv run phistory capture --latest --agents codex --variants default,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5

# 抓取 Claude Code 的真实默认、非官方与全部固定模型官方快照。
uv run phistory capture --latest --agents claude-code --variants default,non-official,official-fable-5-1,official-fable,official-opus-5-5,official-opus,official-opus-4-8,official-opus-4-7,official-sonnet-5-5,official,official-haiku-5-5,official-haiku,official-mythos-5-1,official-mythos

# 回填某个 agent 的历史版本区间。
uv run phistory backfill claude-code --from 2.1.113 --to latest

# 重建最近 10 个已捕获 Claude Code 版本的静态 prompt 文件。
uv run phistory extract-static claude-code --latest-captured 10

# 从退役 Qoder 版本的精确官方可执行文件中归档 prompt 内容。
uv run phistory archive-static qoder --from 0.0.16 --to 0.2.7

# 重新生成 README.md、README_zh.md、docs/captures.md 和 captures/index.json。
uv run phistory render-index

# 重新生成静态网页查看器 index.html。
uv run phistory render-site
```

## 支持的 Agent

- Claude Code (`@anthropic-ai/claude-code`)
- Codex CLI (`@openai/codex`)
- Qwen Code (`@qwen-code/qwen-code`)
- Qoder CLI (`@qoder-ai/qodercli`)
- DeepSeek Harness (`@deepseek-ai/dsh`)
- Antigravity CLI (`google-antigravity/antigravity-cli`)
- Grok Build (`@xai-official/grok`)
- MiniMax Code 桌面应用（[官方下载](https://agent.minimax.io/download)）
- Kimi Code (`@moonshot-ai/kimi-code`)
- MiMo Code (`@mimo-ai/cli`)
- OpenClaw (`openclaw`)
- Hermes Agent (`hermes-agent`)
- Kimi CLI（`MoonshotAI/kimi-cli`，历史归档保留到 `1.51.0`）
- opencode (`opencode-ai`)
- Pi (`@earendil-works/pi-coding-agent`)
- Oh My Pi (`@oh-my-pi/pi-coding-agent`)

## 抓取状态

最近抓取更新：2026-11-17 19:03 UTC

| Agent | 最新版本 | 版本数 | 快照数 | 最近抓取 |
| --- | --- | ---: | ---: | --- |
| Claude Code | [2.1.326 - 2026-11-16](captures/claude-code/2.1.326/variants/default/prompt.md) | 472 | 3850 | 2026-11-17 11:02 UTC |
| Codex CLI | [0.169.0 - 2026-11-13](captures/codex/0.169.0/variants/default/prompt.md) | 114 | 560 | 2026-11-13 09:55 UTC |
| DeepSeek Harness | [0.2.2-rc.1 - 2026-11-04](captures/dsh/0.2.2-rc.1/variants/default/prompt.md) | 17 | 93 | 2026-11-07 09:10 UTC |
| Antigravity CLI | [1.5.10 - 2026-11-17](captures/antigravity/1.5.10/variants/default/prompt.md) | 85 | 85 | 2026-11-17 11:02 UTC |
| Grok Build | [1.0.88 - 2026-11-16](captures/grok/1.0.88/variants/default/prompt.md) | 147 | 147 | 2026-11-17 11:02 UTC |
| MiniMax Code | [3.1.16 - 2026-11-16](captures/minimax-code/3.1.16/variants/default/prompt.md) | 54 | 54 | 2026-11-16 10:06 UTC |
| Kimi Code | [2.6.2 - 2026-11-13](captures/kimi-code/2.6.2/variants/default/prompt.md) | 92 | 92 | 2026-11-14 09:19 UTC |
| Qwen Code | [0.27.4 - 2026-11-15](captures/qwen-code/0.27.4/variants/default/prompt.md) | 155 | 155 | 2026-11-15 09:49 UTC |
| Qoder CLI | [1.1.90 - 2026-11-17](captures/qoder/1.1.90/variants/default/prompt.md) | 200 | 200 | 2026-11-17 11:04 UTC |
| MiMo Code | [0.1.17 - 2026-11-13](captures/mimo/0.1.17/variants/default/prompt.md) | 17 | 17 | 2026-11-13 09:55 UTC |
| OpenClaw | [2026.11.5 - 2026-11-17](captures/openclaw/2026.11.5/variants/default/prompt.md) | 95 | 95 | 2026-11-17 11:03 UTC |
| Hermes Agent | [v0.23.2 - 2026-11-16](captures/hermes/v0.23.2/variants/default/prompt.md) | 47 | 47 | 2026-11-17 11:03 UTC |
| Kimi CLI | [1.51.0 - 2026-09-21](captures/kimi/1.51.0/variants/default/prompt.md) | 23 | 23 | 2026-09-22 07:56 UTC |
| opencode | [1.19.6 - 2026-11-17](captures/opencode/1.19.6/variants/default/prompt.md) | 125 | 125 | 2026-11-17 11:03 UTC |
| Pi | [1.5.2 - 2026-11-10](captures/pi/1.5.2/variants/default/prompt.md) | 62 | 62 | 2026-11-11 09:49 UTC |
| Oh My Pi | [18.22.0 - 2026-11-16](captures/omp/18.22.0/variants/default/prompt.md) | 144 | 144 | 2026-11-17 11:04 UTC |
| Gemini CLI | [0.68.0 - 2026-11-10](captures/gemini-cli/0.68.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:58 UTC |
| Goose | [v1.59.2 - 2026-11-16](captures/goose/v1.59.2/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:58 UTC |
| Crush | [0.101.0 - 2026-11-12](captures/crush/0.101.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:59 UTC |
| mini-swe-agent | [2.4.6 - 2026-07-23](captures/mini-swe-agent/2.4.6/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:59 UTC |
| nanobot | [0.4.1 - 2026-11-06](captures/nanobot/0.4.1/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:59 UTC |
| Bub | [0.6.1 - 2026-11-04](captures/bub/0.6.1/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:59 UTC |
| Jcode | [v1.0.0 - 2026-11-17](captures/jcode/v1.0.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:41 UTC |
| pi-rust | [0.22.1 - 2026-11-13](captures/pi-rust/0.22.1/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:51 UTC |
| Codewhale | [0.10.2 - 2026-10-24](captures/codewhale/0.10.2/variants/default/prompt.md) | 1 | 2 | 2026-11-17 12:41 UTC |
| OpenDev | [v0.1.9 - 2026-07-10](captures/opendev/v0.1.9/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:51 UTC |
| Kilo Code | [7.8.27 - 2026-11-05](captures/kilocode/7.8.27/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:42 UTC |
| Cline | [3.0.74 - 2026-10-26](captures/cline/3.0.74/variants/default/prompt.md) | 1 | 2 | 2026-11-17 12:42 UTC |
| Letta Code | [0.34.63 - 2026-11-15](captures/letta-code/0.34.63/variants/local/prompt.md) | 1 | 1 | 2026-11-17 12:51 UTC |
| Prime Agent | [v0.10.3 - 2026-11-12](captures/prime-agent/v0.10.3/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:42 UTC |
| OpenInterpreter | [rust-v0.0.61 - 2026-11-11](captures/openinterpreter/rust-v0.0.61/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:03 UTC |
| Fibona · Python | [js-v0.1.3 - 2026-11-16](captures/fibona-agent/js-v0.1.3/variants/minimal/prompt.md) | 1 | 2 | 2026-11-17 12:55 UTC |
| CTXAgent · paper runtime | [app-v0.2.1 - 2026-11-11](captures/ctxagent/app-v0.2.1/variants/paper-api/prompt.md) | 1 | 1 | 2026-11-17 12:55 UTC |
| DeepSeek Reasonix · Studio | [studio-v2.61.0 - 2026-11-12](captures/deepseek-reasonix/studio-v2.61.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:03 UTC |
| Quorum CLI | [1.5.1 - 2026-09-27](captures/quorum-cli/1.5.1/variants/standard-api/prompt.md) | 1 | 1 | 2026-11-17 12:55 UTC |
| Tianshu Harness | [3.37.4 - 2026-10-31](captures/tianshu-harness/3.37.4/variants/default/prompt.md) | 1 | 2 | 2026-11-17 12:55 UTC |
| Octomind | [0.72.0 - 2026-11-15](captures/octomind/0.72.0/variants/http/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| Headlong | [72f599562d104c7785798dde63dd5c1c7562280a](captures/headlong/72f599562d104c7785798dde63dd5c1c7562280a/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:56 UTC |
| OpenAgent | [v2.95.1 - 2026-10-05](captures/openagent/v2.95.1/variants/store-api/prompt.md) | 1 | 1 | 2026-11-17 14:05 UTC |
| Nausicaa Harness | [0.2.0 - 2026-09-19](captures/nausicaa-harness/0.2.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| Cheetahclaws | [3.5.90 - 2026-11-08](captures/cheetahclaws/3.5.90/variants/default/prompt.md) | 1 | 2 | 2026-11-17 14:04 UTC |
| Trashclaw | [6dce9edbd7c6633da75971156911636a7b073792](captures/trashclaw/6dce9edbd7c6633da75971156911636a7b073792/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| MiniAGI | [f012aa41e46a7b7dea99d1358305f2fb605a6599](captures/mini-agi/f012aa41e46a7b7dea99d1358305f2fb605a6599/variants/think-api/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| OpenManus | [3309bf4e416fb1c74b008f3e86494439a31bad53](captures/openmanus/3309bf4e416fb1c74b008f3e86494439a31bad53/variants/default/prompt.md) | 1 | 2 | 2026-11-17 14:05 UTC |
| CUGA | [0.4.5 - 2026-11-03](captures/cuga-agent/0.4.5/variants/hello-tools-api/prompt.md) | 1 | 1 | 2026-11-17 13:13 UTC |
| AutoResearchClaw | [v0.5.0 - 2026-05-20](captures/autoresearchclaw/v0.5.0/variants/topic-init/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| Inkos | [1.8.0 - 2026-08-17](captures/inkos/1.8.0/variants/chat-openai/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| DB-GPT | [547882b84b15b32b2894f1bccc6bfb535b7b70c3](captures/db-gpt/547882b84b15b32b2894f1bccc6bfb535b7b70c3/variants/official-react-example/prompt.md) | 1 | 1 | 2026-11-17 16:51 UTC |
| LoopTroop | [0.6.4 - 2026-11-06](captures/looptroop/0.6.4/variants/native-relevant-files-scan/prompt.md) | 1 | 1 | 2026-11-17 18:30 UTC |
| Hivekeep | [e6bf9aea4159312730a690b94697084616b44556](captures/hivekeep/e6bf9aea4159312730a690b94697084616b44556/variants/native-queenie-onboarding/prompt.md) | 1 | 1 | 2026-11-17 17:25 UTC |
| AnythingLLM | [160afa00ac0eca6315aabe662b2e7673c8c96172](captures/anything-llm/160afa00ac0eca6315aabe662b2e7673c8c96172/variants/native-workspace-agent/prompt.md) | 1 | 1 | 2026-11-17 18:15 UTC |
| DarkMoon | [edfe9cb12a263036a0c3dc1af604713e8197cce9](captures/dark-moon/edfe9cb12a263036a0c3dc1af604713e8197cce9/variants/native-compatible-pentest/prompt.md) | 1 | 1 | 2026-11-17 18:32 UTC |
| AutoGPT | [autogpt-platform-beta-v0.11.3 - 2026-11-15](captures/autogpt/autogpt-platform-beta-v0.11.3/variants/native-baseline-copilot/prompt.md) | 1 | 1 | 2026-11-17 18:32 UTC |
| QM | [0.1.18 - 2026-11-11](captures/qm/0.1.18/variants/native-local-web-turn/prompt.md) | 1 | 1 | 2026-11-17 18:39 UTC |
| Dyad | [63d28b887c1b62b8509373ba58d4b03573bb714a](captures/dyad/63d28b887c1b62b8509373ba58d4b03573bb714a/variants/native-electron-basic-agent/prompt.md) | 1 | 1 | 2026-11-17 19:03 UTC |
| AgentLink | [5e6450f0a8f420991ddff5c4c17f921c5f40790c](captures/agentlink/5e6450f0a8f420991ddff5c4c17f921c5f40790c/variants/native-tui-openai/prompt.md) | 1 | 1 | 2026-11-17 18:39 UTC |
| UFO LinuxAgent | [eae3c0a8d168c47cb10df0652e293e092eda1078](captures/ufo/eae3c0a8d168c47cb10df0652e293e092eda1078/variants/linux-device-agent/prompt.md) | 1 | 1 | 2026-11-17 16:51 UTC |
| Agentlas OS | [6108604d8487519b820cac916c5061cd6e619688](captures/agentlas-os/6108604d8487519b820cac916c5061cd6e619688/variants/claude-core-ready-inline/prompt.md) | 1 | 2 | 2026-11-17 18:04 UTC |
| LongHorizon-Harness | [0.1.8 - 2026-10-21](captures/longhorizon-harness/0.1.8/variants/codex-manager-initial/prompt.md) | 1 | 1 | 2026-11-17 16:09 UTC |
| OpenAgents | [b4f1c79b43426824821d9028c080359f37d9a67b](captures/openagents/b4f1c79b43426824821d9028c080359f37d9a67b/variants/plain-microcoder/prompt.md) | 1 | 1 | 2026-11-17 18:12 UTC |
| Markus | [0.12.4 - 2026-11-14](captures/markus/0.12.4/variants/secretary-openai-api/prompt.md) | 1 | 1 | 2026-11-17 15:56 UTC |
| WeChat Mac RPA | [31cf55be1264acf35d436e754deda06833ec4087](captures/wechat-mac-rpa/31cf55be1264acf35d436e754deda06833ec4087/variants/portable-reply-api/prompt.md) | 1 | 1 | 2026-11-17 15:45 UTC |
| AutoRef | [2cd47b74226f76c785f0679e6ccc61e33e55b7e6](captures/autoref/2cd47b74226f76c785f0679e6ccc61e33e55b7e6/variants/scene-planner-initial/prompt.md) | 1 | 1 | 2026-11-17 15:45 UTC |
| Agent-S S3 | [0fff0e634869dab72027435b4586e62300185d41](captures/agent-s/0fff0e634869dab72027435b4586e62300185d41/variants/s3-linux-desktop/prompt.md) | 1 | 1 | 2026-11-17 16:09 UTC |
| DevOpsGPT | [dba1cb5ec9145ac871168e7f2e2d2b35c43d109b](captures/devopsgpt/dba1cb5ec9145ac871168e7f2e2d2b35c43d109b/variants/code-review/prompt.md) | 1 | 1 | 2026-11-17 18:10 UTC |
| Critic-training | [1c4aa0d3c28c522fe9e7541b14b86fe0c4a13e5c](captures/critic-training/1c4aa0d3c28c522fe9e7541b14b86fe0c4a13e5c/variants/prm-actor-initial/prompt.md) | 1 | 1 | 2026-11-17 15:37 UTC |
| Empryo / public SoulForge v2 | [c27dac116d2ee1637abfc6634bebf08b5c379454](captures/empryo/c27dac116d2ee1637abfc6634bebf08b5c379454/variants/public-soulforge-v2/prompt.md) | 1 | 1 | 2026-11-17 15:37 UTC |
| Web3GPT | [1c9ba797563c2305bd32bb30f078c9be2d3e2d55](captures/web3gpt/1c9ba797563c2305bd32bb30f078c9be2d3e2d55/variants/builtin-agent-api/prompt.md) | 1 | 1 | 2026-11-17 15:54 UTC |
| Atomic Agent | [v0.8.0 - 2026-11-17](captures/atomic-agent/v0.8.0/variants/cloud-openai/prompt.md) | 1 | 1 | 2026-11-17 15:28 UTC |
| Self-Operating Computer | [1.5.8 - 2025-02-28](captures/self-operating-computer/1.5.8/variants/default/prompt.md) | 1 | 1 | 2026-11-17 18:21 UTC |
| Hive | [1.0.12 - 2026-10-25](captures/hive/1.0.12/variants/queen-api/prompt.md) | 1 | 1 | 2026-11-17 15:29 UTC |
| IntentKit | [afc8cc44255210db6352f7437e569ed8b88ed5e1](captures/intentkit/afc8cc44255210db6352f7437e569ed8b88ed5e1/variants/local-lead-api/prompt.md) | 1 | 1 | 2026-11-17 15:29 UTC |
| MIRIX | [9077e4b229dd8f61c5703b431a97788c6b61acfb](captures/mirix/9077e4b229dd8f61c5703b431a97788c6b61acfb/variants/episodic-api/prompt.md) | 1 | 1 | 2026-11-17 15:29 UTC |
| Ouroboros | [v7.11.0 - 2026-11-17](captures/ouroboros/v7.11.0/variants/headless-task/prompt.md) | 1 | 1 | 2026-11-17 15:56 UTC |
| ZCode | [fec975905a2abd4c07e35c40d51fa097a2258ec9](captures/zcode/fec975905a2abd4c07e35c40d51fa097a2258ec9/variants/personal-openai/prompt.md) | 1 | 1 | 2026-11-17 14:23 UTC |
| Agent Zero | [ba9b67df0f54ac78106ae85c552ef5d2458f7c9a](captures/agent-zero/ba9b67df0f54ac78106ae85c552ef5d2458f7c9a/variants/context-api/prompt.md) | 1 | 1 | 2026-11-17 15:31 UTC |
| Raven | [v0.2.8 - 2026-11-17](captures/raven/v0.2.8/variants/default/prompt.md) | 1 | 1 | 2026-11-17 14:20 UTC |
| GPT Researcher | [ddd4db36208d9c8e49f3b2436efda97baecb5433](captures/gpt-researcher/ddd4db36208d9c8e49f3b2436efda97baecb5433/variants/deep-agent/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| OpenEvolve | [a7950ffc0150f1e7282a39d1ffac57d675dc5551](captures/openevolve/a7950ffc0150f1e7282a39d1ffac57d675dc5551/variants/function-minimization/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| DeerFlow | [2cc30714c935ed8f00920d7a285c5634c63fec37](captures/deer-flow/2cc30714c935ed8f00920d7a285c5634c63fec37/variants/embedded-openai/prompt.md) | 1 | 1 | 2026-11-17 14:13 UTC |
| Omni-Rewriter | [846fee695fbfd9b816d481e294b5e385dabc4581](captures/omni-rewriter/846fee695fbfd9b816d481e294b5e385dabc4581/variants/api-writer-analysis/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| AgenticSeek | [8f705be8ca625e9549389f188b1e37f700d3ed34](captures/agenticseek/8f705be8ca625e9549389f188b1e37f700d3ed34/variants/coder-openai/prompt.md) | 1 | 1 | 2026-11-17 14:32 UTC |
| nanoMuse | [v1.0.9 - 2026-10-29](captures/nanomuse/v1.0.9/variants/default/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| PR-Agent | [0.50.0 - 2026-11-08](captures/pr-agent/0.50.0/variants/plain-diff-review/prompt.md) | 1 | 1 | 2026-11-17 14:13 UTC |
| DeepAnalyze | [afc697a11675c59683b9f6596c3ab3b3d661182a](captures/deepanalyze/afc697a11675c59683b9f6596c3ab3b3d661182a/variants/vllm-client/prompt.md) | 1 | 1 | 2026-11-17 14:13 UTC |
| localGPT | [34f3107f56a88203859092312231b945a711c619](captures/localgpt/34f3107f56a88203859092312231b945a711c619/variants/default/prompt.md) | 1 | 1 | 2026-11-17 14:14 UTC |
| BambooAI | [2.7.4 - 2026-11-14](captures/bambooai/2.7.4/variants/sdk-free-quick/prompt.md) | 1 | 1 | 2026-11-17 14:31 UTC |
| CowAgent | [bcc36de52ca17b45d1132f388a04b7a456bd3f95](captures/cowagent/bcc36de52ca17b45d1132f388a04b7a456bd3f95/variants/bridge-api/prompt.md) | 1 | 1 | 2026-11-17 14:22 UTC |

## 项目趋势

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
