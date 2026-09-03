# Phistory

[English](README.md)

Phistory 追踪 Claude Code、Codex、Qwen Code、Qoder CLI、DeepSeek Harness、Antigravity、Grok Build、MiniMax Code、Kimi Code、MiMo Code、OpenClaw、Hermes、Kimi CLI、opencode、Pi、Oh My Pi 等热门 coding-agent CLI 的系统提示词如何随版本变化。

打开网页查看器，可以对比不同版本的提示词快照，从 prompts、tools、策略和运行时指令里观察 agent 设计如何变化。

**从这里开始：** [phistory.cc](https://phistory.cc/)

> 每天自动检查新版本，归档最近更新于 **2026-09-03 07:22 UTC**。

![Phistory prompt diff viewer](docs/screenshot.png)

## 为什么看它

- 观察 Anthropic、OpenAI 等团队如何持续迭代 system prompt。
- 看到新工具、权限检查、默认模型行为和用户确认规则是什么时候加入的。
- 对比不同 CLI 如何组织 agent 行为、工具调用和面向开发者的约束。
- 在文章、研究笔记、审计或排障记录里引用稳定的提示词快照。

## 工作原理

Phistory 会安装每个受支持的具体 CLI 版本，再通过 [`claude-tap`](https://github.com/WEIFENG2333/claude-tap) 分别运行每个已配置快照，抓取包含系统提示词的 HTTP 请求，不调用真实模型服务，然后把结果保存到 `captures/<agent>/<version>/variants/<variant>/`，里面包含 `prompt.md`、`trace.jsonl` 和 `meta.json`。抓取配置以 `default` 快照为基线，显式选择的模型或模式会作为额外变体保存。

Claude Code 的 `default` 快照保留非官方／自定义 API 路径；七个官方 API 快照按 Fable、Opus、Sonnet、Haiku 排列：在 `official-fable-5-1` 中固定使用 `claude-fable-5-1`，在 `official-fable` 中固定使用 `claude-fable-5`，在 `official-opus` 中固定使用 `claude-opus-5[1m]`，在 `official-opus-4-8` 中固定使用 `claude-opus-4-8[1m]`，在 `official-opus-4-7` 中固定使用 `claude-opus-4-7[1m]`，在 `official` 中固定使用 `claude-sonnet-5`，在 `official-haiku` 中固定使用 `claude-haiku-4-5`。七者都通过透明正向代理抓取，使 `ANTHROPIC_BASE_URL` 保持未设置。capture-only 模式会在本地返回虚拟响应，不会调用真实模型服务。这些通道的历史条目会让每个旧版 CLI 显式使用同一个 Fable 5.1、Fable 5、Opus 5 1M、Opus 4.8 1M、Opus 4.7 1M、Sonnet 5 或 Haiku 4.5 模型，并不还原该版本发布时的官方默认模型。历史回捕工作流可以覆盖每条官方线路的完整稳定版 CLI 历史。

Claude Code 抓取会固定设置 `DISABLE_GROWTHBOOK=1` 和 `DISABLE_TELEMETRY=1`，避免拉取远程灰度配置。同时设置 `CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off`，避免内部滚动任务预算提醒进入归档提示词。因此快照采用明确记录的确定性基线，而不是抓取当天的灰度状态；该基线会记录在 `meta.json` 中。

Phistory 还会从近期 Claude Code 安装包里提取疑似静态 prompt 的字符串，并从退役 Qoder 版本的精确官方可执行文件中提取 prompt 内容，保存在 `captures/<agent>/<version>/static/`。候选文件会保留原始内容，方便以后改进匹配规则时不用重新安装所有历史包。

GitHub Actions 每天检查一次已自动追踪的 CLI 版本；发现新版本后，会自动抓取并提交新的提示词快照。

## 本地开发

日常查看直接使用托管网页：[phistory.cc](https://phistory.cc/)。下面这些命令主要用于本地开发、复现抓取、回填历史版本，以及重新生成项目里的生成文件。

```bash
# 安装锁定的开发环境。
uv sync --all-groups

# 抓取每个 CLI 的最新版本及其全部已配置快照。
uv run phistory capture --latest --agents claude-code,codex,qwen-code,dsh,antigravity,grok,minimax-code,kimi-code,mimo,openclaw,hermes,kimi,opencode,pi,omp

# 只抓取 Codex 的指定快照。
uv run phistory capture --latest --agents codex --variants default,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5

# 抓取 Claude Code 的非官方默认快照和全部官方 API 模型快照。
uv run phistory capture --latest --agents claude-code --variants default,official-fable-5-1,official-fable,official-opus,official-opus-4-8,official-opus-4-7,official,official-haiku

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
- Kimi CLI (`MoonshotAI/kimi-cli`)
- opencode (`opencode-ai`)
- Pi (`@earendil-works/pi-coding-agent`)
- Oh My Pi (`@oh-my-pi/pi-coding-agent`)

## 抓取状态

最近抓取更新：2026-09-03 07:22 UTC

| Agent | 最新版本 | 版本数 | 快照数 | 最近抓取 |
| --- | --- | ---: | ---: | --- |
| Claude Code | [2.1.259 - 2026-09-02](captures/claude-code/2.1.259/variants/default/prompt.md) | 412 | 2752 | 2026-09-03 07:21 UTC |
| Codex CLI | [0.153.0 - 2026-09-03](captures/codex/0.153.0/variants/default/prompt.md) | 83 | 191 | 2026-09-03 07:21 UTC |
| DeepSeek Harness | [0.1.1-rc.2 - 2026-08-21](captures/dsh/0.1.1-rc.2/variants/default/prompt.md) | 8 | 39 | 2026-08-21 13:43 UTC |
| Antigravity CLI | [1.1.25 - 2026-09-03](captures/antigravity/1.1.25/variants/default/prompt.md) | 39 | 39 | 2026-09-03 07:21 UTC |
| Grok Build | [1.0.13 - 2026-08-28](captures/grok/1.0.13/variants/default/prompt.md) | 131 | 131 | 2026-08-29 09:20 UTC |
| MiniMax Code | [3.0.68 - 2026-08-27](captures/minimax-code/3.0.68/variants/default/prompt.md) | 32 | 32 | 2026-08-27 13:03 UTC |
| Kimi Code | [0.40.1 - 2026-09-02](captures/kimi-code/0.40.1/variants/default/prompt.md) | 70 | 70 | 2026-09-03 07:22 UTC |
| Qwen Code | [0.22.3 - 2026-08-28](captures/qwen-code/0.22.3/variants/default/prompt.md) | 123 | 123 | 2026-08-29 09:20 UTC |
| Qoder CLI | [1.1.41 - 2026-09-02](captures/qoder/1.1.41/variants/default/prompt.md) | 153 | 153 | 2026-09-03 07:22 UTC |
| MiMo Code | [0.1.14 - 2026-09-02](captures/mimo/0.1.14/variants/default/prompt.md) | 14 | 14 | 2026-09-03 07:22 UTC |
| OpenClaw | [2026.8.2 - 2026-09-01](captures/openclaw/2026.8.2/variants/default/prompt.md) | 71 | 71 | 2026-09-02 03:38 UTC |
| Hermes Agent | [v2026.8.31 - 2026-08-31](captures/hermes/v2026.8.31/variants/default/prompt.md) | 29 | 29 | 2026-09-01 07:58 UTC |
| Kimi CLI | [1.50.0 - 2026-09-01](captures/kimi/1.50.0/variants/default/prompt.md) | 22 | 22 | 2026-09-02 03:38 UTC |
| opencode | [1.18.27 - 2026-09-02](captures/opencode/1.18.27/variants/default/prompt.md) | 111 | 111 | 2026-09-03 07:22 UTC |
| Pi | [0.84.4 - 2026-08-28](captures/pi/0.84.4/variants/default/prompt.md) | 43 | 43 | 2026-08-29 09:20 UTC |
| Oh My Pi | [18.1.5 - 2026-09-03](captures/omp/18.1.5/variants/default/prompt.md) | 76 | 76 | 2026-09-03 07:22 UTC |

## 项目趋势

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
