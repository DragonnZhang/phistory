# Phistory

[中文](README_zh.md)

Phistory tracks how system prompts change across popular coding-agent CLIs like Claude Code, Codex, Qwen Code, Qoder CLI, DeepSeek Harness, Antigravity, Grok Build, MiniMax Code, Kimi Code, MiMo Code, OpenClaw, Hermes, Kimi CLI, opencode, Pi, and Oh My Pi.

Open the web viewer to compare prompt snapshots across versions and see how agent design changes through prompts, tools, policies, and runtime instructions.
The default diff includes the main request's prompts, all message roles, and tools. Select **System** for top-level prompts plus system/developer messages, or **Messages** for the ordered conversation. Capture paths and labelled runtime values are normalized for comparison; **Trace** retains the raw request. Links can pin `scope=system` or `scope=messages`. Version-picker change bars describe the archived `prompt.md` only.

**Start here:** [phistory.cc](https://phistory.cc/)

> Checks for new releases daily. Archive last updated: **2026-10-29 08:51 UTC**.

![Phistory prompt diff viewer](docs/screenshot.png)

## Why Use It

- Follow how Anthropic, OpenAI, and other agent builders iterate on system prompts over time.
- See when new tools, permission checks, model defaults, and user-confirmation rules are added.
- Compare how different CLIs structure agent behavior, tool use, and developer-facing constraints.
- Cite stable prompt snapshots in posts, research notes, audits, or debugging reports.

## How It Works

For each supported release, Phistory installs the exact CLI package and runs each configured snapshot through [`claude-tap`](https://github.com/WEIFENG2333/claude-tap), captures the prompt-bearing HTTP request without calling the real model provider, and stores the result under `captures/<agent>/<version>/variants/<variant>/` with `prompt.md`, `trace.jsonl`, and `meta.json`. Capture configurations use a `default` snapshot as their baseline; selected models or modes are stored as additional variants.

Claude Code's `default` snapshot uses the official API path without pinning a model; the previous custom-API capture is preserved as `non-official`. Its additional fixed-model official API snapshots pin `claude-fable-5-1` in `official-fable-5-1`, `claude-fable-5` in `official-fable`, `claude-opus-5-5[1m]` in `official-opus-5-5`, `claude-opus-5[1m]` in `official-opus`, `claude-opus-4-8[1m]` in `official-opus-4-8`, `claude-opus-4-7[1m]` in `official-opus-4-7`, `claude-sonnet-5-5` in `official-sonnet-5-5`, `claude-sonnet-5` in `official`, `claude-haiku-5-5` in `official-haiku-5-5`, and `claude-haiku-4-5` in `official-haiku`; default and all fixed-model lanes use transparent forward-proxy capture so `ANTHROPIC_BASE_URL` remains unset; capture-only mode returns a dummy response locally instead of calling the model provider. Historical entries in these lanes run each old CLI against the same explicit model; they do not reconstruct the model that was the official default when that CLI was released. Each lane keeps its verified model-support floor; Haiku 5.5 starts at the earliest verified release 2.1.293. The verification-required Mythos 5.1 and Mythos 5 lanes pin `claude-mythos-5-1` and `claude-mythos-5` from the earliest verified release 2.1.296. These capture-only archives do not verify account access.

Claude Code captures deliberately set `DISABLE_GROWTHBOOK=1` and `DISABLE_TELEMETRY=1` so remote feature assignments are not fetched, and `CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off` so the internal rolling task-budget reminder does not enter archived prompts. These snapshots use a deterministic, documented baseline rather than the rollout state at capture time; the baseline is recorded in `meta.json`.

Phistory also extracts static prompt-like strings from recent Claude Code packages and prompt material from exact official executables for retired Qoder releases, storing them under `captures/<agent>/<version>/static/`. The candidate archive keeps the raw extraction input so matching rules can be improved later without reinstalling every historical package.

Codex GPT-6 Sol starts at the published `0.157.0-alpha.10` preview; stable `0.156.0` does not bundle its model metadata. See [the capture boundary](docs/research/opus-5-5-gpt-6-sol.md).

GitHub Actions checks automatically tracked CLI releases every day and commits new snapshots when they appear.

Legacy Kimi CLI is archived through `1.51.0`. Its final `1.52.0` release only prints a migration notice ([upstream change](https://github.com/MoonshotAI/kimi-cli/pull/2666)), so it is excluded from automatic latest captures. Kimi Code continues to be tracked separately.

## Local Development

Use the hosted viewer at [phistory.cc](https://phistory.cc/). These commands are for local development, capture reproduction, historical backfills, and regenerating generated files.

```bash
# Install the locked development environment.
uv sync --all-groups

# Capture the latest release and every configured snapshot for each active CLI (Linux CI).
uv run phistory capture --latest --agents claude-code,codex,qwen-code,dsh,antigravity,grok,minimax-code,kimi-code,mimo,openclaw,hermes,opencode,pi,omp

# Capture only selected Codex snapshots.
uv run phistory capture --latest --agents codex --variants default,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5

# Capture Claude Code's actual default, custom-API, and fixed-model official snapshots.
uv run phistory capture --latest --agents claude-code --variants default,non-official,official-fable-5-1,official-fable,official-opus-5-5,official-opus,official-opus-4-8,official-opus-4-7,official-sonnet-5-5,official,official-haiku-5-5,official-haiku,official-mythos-5-1,official-mythos

# Capture a historical version range for one agent.
uv run phistory backfill claude-code --from 2.1.113 --to latest

# Rebuild static prompt files for the latest 10 captured Claude Code versions.
uv run phistory extract-static claude-code --latest-captured 10

# Archive prompt material from exact official executables for retired Qoder releases.
uv run phistory archive-static qoder --from 0.0.16 --to 0.2.7

# Regenerate README.md, README_zh.md, docs/captures.md, and captures/index.json.
uv run phistory render-index

# Regenerate the static web viewer at index.html.
uv run phistory render-site
```

## Supported Agents

- Claude Code (`@anthropic-ai/claude-code`)
- Codex CLI (`@openai/codex`)
- Qwen Code (`@qwen-code/qwen-code`)
- Qoder CLI (`@qoder-ai/qodercli`)
- DeepSeek Harness (`@deepseek-ai/dsh`)
- Antigravity CLI (`google-antigravity/antigravity-cli`)
- Grok Build (`@xai-official/grok`)
- MiniMax Code desktop app ([official download](https://agent.minimax.io/download))
- Kimi Code (`@moonshot-ai/kimi-code`)
- MiMo Code (`@mimo-ai/cli`)
- OpenClaw (`openclaw`)
- Hermes Agent (`hermes-agent`)
- Kimi CLI (`MoonshotAI/kimi-cli`, historical archive through `1.51.0`)
- opencode (`opencode-ai`)
- Pi (`@earendil-works/pi-coding-agent`)
- Oh My Pi (`@oh-my-pi/pi-coding-agent`)

## Capture Status

Last capture update: 2026-10-29 08:51 UTC

| Agent | Latest | Versions | Snapshots | Last Captured |
| --- | --- | ---: | ---: | --- |
| Claude Code | [2.1.312 - 2026-10-27](captures/claude-code/2.1.312/variants/default/prompt.md) | 460 | 3670 | 2026-10-28 08:55 UTC |
| Codex CLI | [0.165.0 - 2026-10-27](captures/codex/0.165.0/variants/default/prompt.md) | 108 | 493 | 2026-10-28 08:55 UTC |
| DeepSeek Harness | [0.2.1-rc.2 - 2026-10-17](captures/dsh/0.2.1-rc.2/variants/default/prompt.md) | 15 | 81 | 2026-10-19 08:04 UTC |
| Antigravity CLI | [1.4.10 - 2026-10-29](captures/antigravity/1.4.10/variants/default/prompt.md) | 75 | 75 | 2026-10-29 08:50 UTC |
| Grok Build | [1.0.70 - 2026-10-23](captures/grok/1.0.70/variants/default/prompt.md) | 145 | 145 | 2026-10-26 01:12 UTC |
| MiniMax Code | [3.1.7 - 2026-10-28](captures/minimax-code/3.1.7/variants/default/prompt.md) | 45 | 45 | 2026-10-29 08:50 UTC |
| Kimi Code | [2.4.0 - 2026-10-28](captures/kimi-code/2.4.0/variants/default/prompt.md) | 84 | 84 | 2026-10-29 08:50 UTC |
| Qwen Code | [0.26.3 - 2026-10-28](captures/qwen-code/0.26.3/variants/default/prompt.md) | 144 | 144 | 2026-10-29 08:50 UTC |
| Qoder CLI | [1.1.80 - 2026-10-27](captures/qoder/1.1.80/variants/default/prompt.md) | 190 | 190 | 2026-10-28 08:58 UTC |
| MiMo Code | [0.1.16 - 2026-10-26](captures/mimo/0.1.16/variants/default/prompt.md) | 16 | 16 | 2026-10-27 08:19 UTC |
| OpenClaw | [2026.10.8 - 2026-10-27](captures/openclaw/2026.10.8/variants/default/prompt.md) | 88 | 88 | 2026-10-28 08:57 UTC |
| Hermes Agent | [v0.22.5 - 2026-10-23](captures/hermes/v0.22.5/variants/default/prompt.md) | 42 | 42 | 2026-10-26 01:14 UTC |
| Kimi CLI | [1.51.0 - 2026-09-21](captures/kimi/1.51.0/variants/default/prompt.md) | 23 | 23 | 2026-09-22 07:56 UTC |
| opencode | [1.19.5 - 2026-10-28](captures/opencode/1.19.5/variants/default/prompt.md) | 124 | 124 | 2026-10-28 08:57 UTC |
| Pi | [1.4.0 - 2026-10-21](captures/pi/1.4.0/variants/default/prompt.md) | 59 | 59 | 2026-10-22 07:55 UTC |
| Oh My Pi | [18.13.3 - 2026-10-28](captures/omp/18.13.3/variants/default/prompt.md) | 129 | 129 | 2026-10-29 08:51 UTC |

## Project Trend

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
