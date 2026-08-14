# Phistory

[中文](README_zh.md)

Phistory tracks how system prompts change across popular coding-agent CLIs like Claude Code, Codex, DeepSeek Harness, Antigravity, Grok Build, MiniMax Code, Kimi Code, MiMo Code, OpenClaw, Hermes, Kimi CLI, opencode, Pi, and Oh My Pi.

Open the web viewer to compare prompt snapshots across versions and see how agent design changes through prompts, tools, policies, and runtime instructions.

**Start here:** [phistory.cc](https://phistory.cc/)

> Checks for new releases hourly. Archive last updated: **2026-08-14 13:57 UTC**.

![Phistory prompt diff viewer](docs/screenshot.png)

## Why Use It

- Follow how Anthropic, OpenAI, and other agent builders iterate on system prompts over time.
- See when new tools, permission checks, model defaults, and user-confirmation rules are added.
- Compare how different CLIs structure agent behavior, tool use, and developer-facing constraints.
- Cite stable prompt snapshots in posts, research notes, audits, or debugging reports.

## How It Works

For each supported release, Phistory installs the exact CLI package, runs it once through [`claude-tap`](https://github.com/WEIFENG2333/claude-tap), captures the prompt-bearing HTTP request without calling the real model provider, and stores the result under `captures/<agent>/<version>/` with `prompt.md`, `trace.jsonl`, and `meta.json`.

For recent Claude Code releases, Phistory also extracts static prompt-like strings from the installed package and stores them as `static-prompts.md`, `static-prompts.json`, and `static-candidates.json`. The candidate archive keeps the raw extraction input so matching rules can be improved later without reinstalling every historical package.

GitHub Actions checks automatically tracked CLI releases every hour and commits new snapshots when they appear.

## Local Development

Use the hosted viewer at [phistory.cc](https://phistory.cc/). These commands are for local development, capture reproduction, historical backfills, and regenerating generated files.

```bash
# Install the locked development environment.
uv sync --all-groups

# Capture the latest automatically tracked CLI releases.
uv run phistory capture --latest --agents claude-code,codex,dsh,antigravity,grok,minimax-code,kimi-code,mimo,openclaw,hermes,kimi,opencode,pi,omp

# Capture a historical version range for one agent.
uv run phistory backfill claude-code --from 2.1.113 --to latest

# Rebuild static prompt files for the latest 10 captured Claude Code versions.
uv run phistory extract-static claude-code --latest-captured 10

# Regenerate README.md, README_zh.md, docs/captures.md, and captures/index.json.
uv run phistory render-index

# Regenerate the static web viewer at index.html.
uv run phistory render-site
```

## Supported Agents

- Claude Code (`@anthropic-ai/claude-code`)
- Codex CLI (`@openai/codex`)
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

Last capture update: 2026-08-14 13:57 UTC

| Agent | Latest | Captures | Last Captured |
| --- | --- | ---: | --- |
| Claude Code | [2.1.232 - 2026-08-13](captures/claude-code/2.1.232/prompt.md) | 386 | 2026-08-14 00:10 UTC |
| Codex CLI | [0.147.0 - 2026-08-07](captures/codex/0.147.0/prompt.md) | 74 | 2026-08-07 03:32 UTC |
| DeepSeek Harness | [0.1.0-rc.6 - 2026-08-13](captures/dsh/0.1.0-rc.6/prompt.md) | 5 | 2026-08-13 14:06 UTC |
| Antigravity CLI | [1.1.13 - 2026-08-14](captures/antigravity/1.1.13/prompt.md) | 27 | 2026-08-14 03:08 UTC |
| Grok Build | [1.0.3 - 2026-08-12](captures/grok/1.0.3/prompt.md) | 128 | 2026-08-12 08:49 UTC |
| MiniMax Code | [3.0.60 - 2026-08-07](captures/minimax-code/3.0.60/prompt.md) | 26 | 2026-08-07 06:50 UTC |
| Kimi Code | [0.36.1 - 2026-08-14](captures/kimi-code/0.36.1/prompt.md) | 62 | 2026-08-14 13:57 UTC |
| MiMo Code | [0.1.12 - 2026-08-13](captures/mimo/0.1.12/prompt.md) | 12 | 2026-08-13 09:09 UTC |
| OpenClaw | [2026.7.1-2 - 2026-07-18](captures/openclaw/2026.7.1-2/prompt.md) | 69 | 2026-07-18 04:30 UTC |
| Hermes Agent | [v2026.8.13 - 2026-08-13](captures/hermes/v2026.8.13/prompt.md) | 23 | 2026-08-13 21:25 UTC |
| Kimi CLI | [1.49.0 - 2026-07-16](captures/kimi/1.49.0/prompt.md) | 21 | 2026-07-16 11:21 UTC |
| opencode | [1.18.18 - 2026-08-13](captures/opencode/1.18.18/prompt.md) | 103 | 2026-08-13 09:09 UTC |
| Pi | [0.84.2 - 2026-08-14](captures/pi/0.84.2/prompt.md) | 41 | 2026-08-14 10:35 UTC |
| Oh My Pi | [17.3.4 - 2026-08-14](captures/omp/17.3.4/prompt.md) | 59 | 2026-08-14 13:57 UTC |

## Project Trend

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
