# Phistory

[中文](README_zh.md)

Phistory tracks how system prompts change across popular coding-agent CLIs like Claude Code, Codex, Qwen Code, Qoder CLI, DeepSeek Harness, Antigravity, Grok Build, MiniMax Code, Kimi Code, MiMo Code, OpenClaw, Hermes, Kimi CLI, opencode, Pi, and Oh My Pi.

Open the web viewer to compare prompt snapshots across versions and see how agent design changes through prompts, tools, policies, and runtime instructions.

**Start here:** [phistory.cc](https://phistory.cc/)

> Checks for new releases daily. Archive last updated: **2026-09-23 02:59 UTC**.

![Phistory prompt diff viewer](docs/screenshot.png)

## Why Use It

- Follow how Anthropic, OpenAI, and other agent builders iterate on system prompts over time.
- See when new tools, permission checks, model defaults, and user-confirmation rules are added.
- Compare how different CLIs structure agent behavior, tool use, and developer-facing constraints.
- Cite stable prompt snapshots in posts, research notes, audits, or debugging reports.

## How It Works

For each supported release, Phistory installs the exact CLI package and runs each configured snapshot through [`claude-tap`](https://github.com/WEIFENG2333/claude-tap), captures the prompt-bearing HTTP request without calling the real model provider, and stores the result under `captures/<agent>/<version>/variants/<variant>/` with `prompt.md`, `trace.jsonl`, and `meta.json`. Capture configurations use a `default` snapshot as their baseline; selected models or modes are stored as additional variants.

Claude Code's `default` snapshot uses the official API path without pinning a model; the previous custom-API capture is preserved as `non-official`. Its additional fixed-model official API snapshots pin `claude-fable-5-1` in `official-fable-5-1`, `claude-fable-5` in `official-fable`, `claude-opus-5-5[1m]` in `official-opus-5-5`, `claude-opus-5[1m]` in `official-opus`, `claude-opus-4-8[1m]` in `official-opus-4-8`, `claude-opus-4-7[1m]` in `official-opus-4-7`, `claude-sonnet-5` in `official`, and `claude-haiku-4-5` in `official-haiku`; default and all eight use transparent forward-proxy capture so `ANTHROPIC_BASE_URL` remains unset; capture-only mode returns a dummy response locally instead of calling the model provider. Historical entries in these lanes run each old CLI against the same explicit Fable 5.1, Fable 5, Opus 5.5 1M, Opus 5 1M, Opus 4.8 1M, Opus 4.7 1M, Sonnet 5, or Haiku 4.5 model; they do not reconstruct the model that was the official default when that CLI was released. The history recapture workflow can cover the complete stable CLI history for every official lane.

Claude Code captures deliberately set `DISABLE_GROWTHBOOK=1` and `DISABLE_TELEMETRY=1` so remote feature assignments are not fetched, and `CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off` so the internal rolling task-budget reminder does not enter archived prompts. These snapshots use a deterministic, documented baseline rather than the rollout state at capture time; the baseline is recorded in `meta.json`.

Phistory also extracts static prompt-like strings from recent Claude Code packages and prompt material from exact official executables for retired Qoder releases, storing them under `captures/<agent>/<version>/static/`. The candidate archive keeps the raw extraction input so matching rules can be improved later without reinstalling every historical package.

Codex GPT-6 Sol starts at the published `0.157.0-alpha.10` preview; stable `0.156.0` does not bundle its model metadata. See [the capture boundary](docs/research/opus-5-5-gpt-6-sol.md).

GitHub Actions checks automatically tracked CLI releases every day and commits new snapshots when they appear.

## Local Development

Use the hosted viewer at [phistory.cc](https://phistory.cc/). These commands are for local development, capture reproduction, historical backfills, and regenerating generated files.

```bash
# Install the locked development environment.
uv sync --all-groups

# Capture the latest release and every configured snapshot for each CLI.
uv run phistory capture --latest --agents claude-code,codex,qwen-code,dsh,antigravity,grok,minimax-code,kimi-code,mimo,openclaw,hermes,kimi,opencode,pi,omp

# Capture only selected Codex snapshots.
uv run phistory capture --latest --agents codex --variants default,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5

# Capture Claude Code's actual default, custom-API, and fixed-model official snapshots.
uv run phistory capture --latest --agents claude-code --variants default,non-official,official-fable-5-1,official-fable,official-opus-5-5,official-opus,official-opus-4-8,official-opus-4-7,official,official-haiku

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
- Kimi CLI (`MoonshotAI/kimi-cli`)
- opencode (`opencode-ai`)
- Pi (`@earendil-works/pi-coding-agent`)
- Oh My Pi (`@oh-my-pi/pi-coding-agent`)

## Capture Status

Last capture update: 2026-09-23 02:59 UTC

| Agent | Latest | Versions | Snapshots | Last Captured |
| --- | --- | ---: | ---: | --- |
| Claude Code | [2.1.280 - 2026-09-22](captures/claude-code/2.1.280/variants/default/prompt.md) | 430 | 3295 | 2026-09-23 02:58 UTC |
| Codex CLI | [0.157.0-alpha.10 - 2026-09-22](captures/codex/0.157.0-alpha.10/variants/default/prompt.md) | 92 | 339 | 2026-09-23 02:14 UTC |
| DeepSeek Harness | [0.1.5-rc.2 - 2026-09-10](captures/dsh/0.1.5-rc.2/variants/default/prompt.md) | 11 | 57 | 2026-09-18 07:36 UTC |
| Antigravity CLI | [1.2.8 - 2026-09-22](captures/antigravity/1.2.8/variants/default/prompt.md) | 51 | 51 | 2026-09-22 07:55 UTC |
| Grok Build | [1.0.41 - 2026-09-22](captures/grok/1.0.41/variants/default/prompt.md) | 137 | 137 | 2026-09-23 02:07 UTC |
| MiniMax Code | [3.0.73 - 2026-09-18](captures/minimax-code/3.0.73/variants/default/prompt.md) | 37 | 37 | 2026-09-19 07:31 UTC |
| Kimi Code | [2.0.2 - 2026-09-19](captures/kimi-code/2.0.2/variants/default/prompt.md) | 76 | 76 | 2026-09-20 07:54 UTC |
| Qwen Code | [0.24.4 - 2026-09-22](captures/qwen-code/0.24.4/variants/default/prompt.md) | 133 | 133 | 2026-09-23 02:08 UTC |
| Qoder CLI | [1.1.61 - 2026-09-22](captures/qoder/1.1.61/variants/default/prompt.md) | 171 | 171 | 2026-09-23 02:09 UTC |
| MiMo Code | [0.1.15 - 2026-09-22](captures/mimo/0.1.15/variants/default/prompt.md) | 15 | 15 | 2026-09-23 02:08 UTC |
| OpenClaw | [2026.9.5 - 2026-09-19](captures/openclaw/2026.9.5/variants/default/prompt.md) | 76 | 76 | 2026-09-19 07:32 UTC |
| Hermes Agent | [v2026.9.21 - 2026-09-21](captures/hermes/v2026.9.21/variants/default/prompt.md) | 33 | 33 | 2026-09-22 07:56 UTC |
| Kimi CLI | [1.51.0 - 2026-09-21](captures/kimi/1.51.0/variants/default/prompt.md) | 23 | 23 | 2026-09-22 07:56 UTC |
| opencode | [1.18.32 - 2026-09-21](captures/opencode/1.18.32/variants/default/prompt.md) | 115 | 115 | 2026-09-22 07:56 UTC |
| Pi | [0.87.1 - 2026-09-22](captures/pi/0.87.1/variants/default/prompt.md) | 49 | 49 | 2026-09-23 02:08 UTC |
| Oh My Pi | [18.2.10 - 2026-09-22](captures/omp/18.2.10/variants/default/prompt.md) | 95 | 95 | 2026-09-23 02:08 UTC |

## Project Trend

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
