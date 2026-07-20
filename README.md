# Phistory

[中文](README_zh.md)

Phistory tracks how system prompts change across popular coding-agent CLIs like Claude Code, Codex, Antigravity, Grok Build, Kimi Code, MiMo Code, OpenClaw, Hermes, Kimi CLI, opencode, Pi, and Oh My Pi.

Open the web viewer to compare prompt snapshots across versions and see how agent design changes through prompts, tools, policies, and runtime instructions.

**Start here:** [phistory.cc](https://phistory.cc/)

> Checks for new releases hourly. Archive last updated: **2026-07-20 08:26 UTC**.

![Phistory prompt diff viewer](docs/screenshot.png)

## Why Use It

- Follow how Anthropic, OpenAI, and other agent builders iterate on system prompts over time.
- See when new tools, permission checks, model defaults, and user-confirmation rules are added.
- Compare how different CLIs structure agent behavior, tool use, and developer-facing constraints.
- Cite stable prompt snapshots in posts, research notes, audits, or debugging reports.

## How It Works

For each supported release, Phistory installs the exact CLI package, runs it once through [`claude-tap`](https://github.com/WEIFENG2333/claude-tap), captures the prompt-bearing HTTP request without calling the real model provider, and stores the result under `captures/<agent>/<version>/` with `prompt.md`, `trace.jsonl`, and `meta.json`.

For recent Claude Code releases, Phistory also extracts static prompt-like strings from the installed package and stores them as `static-prompts.md`, `static-prompts.json`, and `static-candidates.json`. The candidate archive keeps the raw extraction input so matching rules can be improved later without reinstalling every historical package.

GitHub Actions checks supported CLI releases every hour and commits new snapshots when they appear.

## Local Development

Use the hosted viewer at [phistory.cc](https://phistory.cc/). These commands are for local development, capture reproduction, historical backfills, and regenerating generated files.

```bash
# Install the locked development environment.
uv sync --all-groups

# Capture the latest supported CLI releases.
uv run phistory capture --latest --agents claude-code,codex,antigravity,grok,kimi-code,mimo,openclaw,hermes,kimi,opencode,pi,omp

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
- Antigravity CLI (`google-antigravity/antigravity-cli`)
- Grok Build (`@xai-official/grok`)
- Kimi Code (`@moonshot-ai/kimi-code`)
- MiMo Code (`@mimo-ai/cli`)
- OpenClaw (`openclaw`)
- Hermes Agent (`hermes-agent`)
- Kimi CLI (`MoonshotAI/kimi-cli`)
- opencode (`opencode-ai`)
- Pi (`@earendil-works/pi-coding-agent`)
- Oh My Pi (`@oh-my-pi/pi-coding-agent`)

## Capture Status

Last capture update: 2026-07-20 08:26 UTC

| Agent | Latest | Captures | Last Captured |
| --- | --- | ---: | --- |
| Claude Code | [2.1.215 - 2026-07-19](captures/claude-code/2.1.215/prompt.md) | 371 | 2026-07-19 04:53 UTC |
| Codex CLI | [0.144.6 - 2026-07-18](captures/codex/0.144.6/prompt.md) | 70 | 2026-07-18 13:59 UTC |
| Antigravity CLI | [1.1.4 - 2026-07-18](captures/antigravity/1.1.4/prompt.md) | 18 | 2026-07-18 00:58 UTC |
| Grok Build | [0.2.106 - 2026-07-19](captures/grok/0.2.106/prompt.md) | 121 | 2026-07-19 22:28 UTC |
| Kimi Code | [0.28.0 - 2026-07-20](captures/kimi-code/0.28.0/prompt.md) | 49 | 2026-07-20 08:26 UTC |
| MiMo Code | [0.1.6 - 2026-07-15](captures/mimo/0.1.6/prompt.md) | 6 | 2026-07-15 12:54 UTC |
| OpenClaw | [2026.7.1-2 - 2026-07-18](captures/openclaw/2026.7.1-2/prompt.md) | 69 | 2026-07-18 04:30 UTC |
| Hermes Agent | [v2026.7.7.2 - 2026-07-08](captures/hermes/v2026.7.7.2/prompt.md) | 19 | 2026-07-08 03:49 UTC |
| Kimi CLI | [1.49.0 - 2026-07-16](captures/kimi/1.49.0/prompt.md) | 21 | 2026-07-16 11:21 UTC |
| opencode | [1.18.3 - 2026-07-16](captures/opencode/1.18.3/prompt.md) | 89 | 2026-07-16 16:51 UTC |
| Pi | [0.80.10 - 2026-07-16](captures/pi/0.80.10/prompt.md) | 33 | 2026-07-16 22:33 UTC |
| Oh My Pi | [17.0.5 - 2026-07-18](captures/omp/17.0.5/prompt.md) | 25 | 2026-07-18 22:26 UTC |

## Project Trend

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
