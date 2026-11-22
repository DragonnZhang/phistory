# Phistory

[中文](README_zh.md)

Phistory tracks how system prompts change across popular coding-agent CLIs like Claude Code, Codex, Qwen Code, Qoder CLI, DeepSeek Harness, Antigravity, Grok Build, MiniMax Code, Kimi Code, MiMo Code, OpenClaw, Hermes, Kimi CLI, opencode, Pi, and Oh My Pi.

Open the web viewer to compare prompt snapshots across versions and see how agent design changes through prompts, tools, policies, and runtime instructions.
The default diff includes the main request's prompts, all message roles, and tools. Select **System** for top-level prompts plus system/developer messages, or **Messages** for the ordered conversation. Capture paths and labelled runtime values are normalized for comparison; **Trace** retains the raw request. Links can pin `scope=system` or `scope=messages`. Version-picker change bars describe the archived `prompt.md` only.

**Start here:** [phistory.cc](https://phistory.cc/)

[Harness source archive](sources.html) preserves prompt templates, assembly code, and tool declarations for additional projects from the 95-agent survey. Source files have their own provenance and are kept separate from runtime request captures.

> Checks for new releases daily. Archive last updated: **2026-11-22 10:33 UTC**.

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

Last capture update: 2026-11-22 10:33 UTC

| Agent | Latest | Versions | Snapshots | Last Captured |
| --- | --- | ---: | ---: | --- |
| Claude Code | [2.1.330 - 2026-11-20](captures/claude-code/2.1.330/variants/default/prompt.md) | 476 | 3914 | 2026-11-21 10:08 UTC |
| Codex CLI | [0.171.0 - 2026-11-20](captures/codex/0.171.0/variants/default/prompt.md) | 116 | 586 | 2026-11-20 10:24 UTC |
| DeepSeek Harness | [0.2.2-rc.5 - 2026-11-18](captures/dsh/0.2.2-rc.5/variants/default/prompt.md) | 18 | 99 | 2026-11-19 10:47 UTC |
| Antigravity CLI | [1.5.14 - 2026-11-21](captures/antigravity/1.5.14/variants/default/prompt.md) | 89 | 89 | 2026-11-21 10:08 UTC |
| Grok Build | [1.0.92 - 2026-11-20](captures/grok/1.0.92/variants/default/prompt.md) | 149 | 149 | 2026-11-21 10:08 UTC |
| MiniMax Code | [3.1.17 - 2026-11-20](captures/minimax-code/3.1.17/variants/default/prompt.md) | 55 | 55 | 2026-11-21 10:09 UTC |
| Kimi Code | [2.7.2 - 2026-11-20](captures/kimi-code/2.7.2/variants/default/prompt.md) | 96 | 96 | 2026-11-20 10:24 UTC |
| Qwen Code | [0.28.3 - 2026-11-21](captures/qwen-code/0.28.3/variants/default/prompt.md) | 157 | 157 | 2026-11-21 10:09 UTC |
| Qoder CLI | [1.1.95 - 2026-11-21](captures/qoder/1.1.95/variants/default/prompt.md) | 205 | 205 | 2026-11-22 05:11 UTC |
| MiMo Code | [0.1.17 - 2026-11-13](captures/mimo/0.1.17/variants/default/prompt.md) | 17 | 17 | 2026-11-13 09:55 UTC |
| OpenClaw | [2026.11.6 - 2026-11-19](captures/openclaw/2026.11.6/variants/default/prompt.md) | 96 | 96 | 2026-11-20 10:25 UTC |
| Hermes Agent | [v0.23.3 - 2026-11-20](captures/hermes/v0.23.3/variants/default/prompt.md) | 48 | 48 | 2026-11-20 10:25 UTC |
| Kimi CLI | [1.51.0 - 2026-09-21](captures/kimi/1.51.0/variants/default/prompt.md) | 23 | 23 | 2026-09-22 07:56 UTC |
| opencode | [1.19.6 - 2026-11-17](captures/opencode/1.19.6/variants/default/prompt.md) | 125 | 125 | 2026-11-17 11:03 UTC |
| Pi | [1.6.0 - 2026-11-20](captures/pi/1.6.0/variants/default/prompt.md) | 63 | 63 | 2026-11-21 10:09 UTC |
| Oh My Pi | [19.1.0 - 2026-11-21](captures/omp/19.1.0/variants/default/prompt.md) | 148 | 148 | 2026-11-22 10:11 UTC |
| Gemini CLI | [0.70.0 - 2026-11-19](captures/gemini-cli/0.70.0/variants/default/prompt.md) | 3 | 3 | 2026-11-20 10:26 UTC |
| Goose | [v1.60.2 - 2026-11-20](captures/goose/v1.60.2/variants/default/prompt.md) | 3 | 3 | 2026-11-20 10:26 UTC |
| Crush | [0.102.0 - 2026-11-19](captures/crush/0.102.0/variants/default/prompt.md) | 2 | 2 | 2026-11-20 10:26 UTC |
| mini-swe-agent | [2.4.6 - 2026-07-23](captures/mini-swe-agent/2.4.6/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:59 UTC |
| nanobot | [0.4.2 - 2026-11-21](captures/nanobot/0.4.2/variants/default/prompt.md) | 2 | 2 | 2026-11-22 10:11 UTC |
| Bub | [0.6.1 - 2026-11-04](captures/bub/0.6.1/variants/default/prompt.md) | 1 | 1 | 2026-11-17 11:59 UTC |
| Jcode | [v1.0.3 - 2026-11-22](captures/jcode/v1.0.3/variants/default/prompt.md) | 3 | 3 | 2026-11-22 10:11 UTC |
| pi-rust | [0.27.1 - 2026-11-22](captures/pi-rust/0.27.1/variants/default/prompt.md) | 6 | 6 | 2026-11-22 10:11 UTC |
| Codewhale | [0.10.2 - 2026-10-24](captures/codewhale/0.10.2/variants/default/prompt.md) | 1 | 2 | 2026-11-17 12:41 UTC |
| OpenDev | [v0.1.9 - 2026-07-10](captures/opendev/v0.1.9/variants/default/prompt.md) | 1 | 1 | 2026-11-17 12:51 UTC |
| Kilo Code | [7.8.32 - 2026-11-20](captures/kilocode/7.8.32/variants/default/prompt.md) | 2 | 2 | 2026-11-21 10:10 UTC |
| Cline | [3.0.74 - 2026-10-26](captures/cline/3.0.74/variants/default/prompt.md) | 1 | 2 | 2026-11-17 12:42 UTC |
| Letta Code | [0.34.68 - 2026-11-21](captures/letta-code/0.34.68/variants/local/prompt.md) | 5 | 5 | 2026-11-21 10:18 UTC |
| Prime Agent | [v0.10.5 - 2026-11-18](captures/prime-agent/v0.10.5/variants/default/prompt.md) | 3 | 3 | 2026-11-19 10:49 UTC |
| OpenInterpreter | [rust-v0.0.61 - 2026-11-11](captures/openinterpreter/rust-v0.0.61/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:03 UTC |
| Fibona · Python | [js-v0.1.3 - 2026-11-16](captures/fibona-agent/js-v0.1.3/variants/minimal/prompt.md) | 1 | 2 | 2026-11-17 12:55 UTC |
| CTXAgent · paper runtime | [app-v0.3.2 - 2026-11-18](captures/ctxagent/app-v0.3.2/variants/paper-api/prompt.md) | 2 | 2 | 2026-11-18 10:40 UTC |
| DeepSeek Reasonix · Studio | [studio-v2.61.0 - 2026-11-12](captures/deepseek-reasonix/studio-v2.61.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:03 UTC |
| Quorum CLI | [1.5.1 - 2026-09-27](captures/quorum-cli/1.5.1/variants/standard-api/prompt.md) | 1 | 1 | 2026-11-17 12:55 UTC |
| Tianshu Harness | [3.37.4 - 2026-10-31](captures/tianshu-harness/3.37.4/variants/default/prompt.md) | 1 | 2 | 2026-11-17 12:55 UTC |
| Octomind | [0.72.0 - 2026-11-15](captures/octomind/0.72.0/variants/http/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| Headlong | [72f599562d104c7785798dde63dd5c1c7562280a](captures/headlong/72f599562d104c7785798dde63dd5c1c7562280a/variants/default/prompt.md) | 2 | 2 | 2026-11-17 12:56 UTC |
| OpenAgent | [v2.95.1 - 2026-10-05](captures/openagent/v2.95.1/variants/store-api/prompt.md) | 1 | 1 | 2026-11-17 14:05 UTC |
| Nausicaa Harness | [0.2.0 - 2026-09-19](captures/nausicaa-harness/0.2.0/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| Cheetahclaws | [3.5.90 - 2026-11-08](captures/cheetahclaws/3.5.90/variants/default/prompt.md) | 1 | 2 | 2026-11-17 14:04 UTC |
| Trashclaw | [6dce9edbd7c6633da75971156911636a7b073792](captures/trashclaw/6dce9edbd7c6633da75971156911636a7b073792/variants/default/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| MiniAGI | [f012aa41e46a7b7dea99d1358305f2fb605a6599](captures/mini-agi/f012aa41e46a7b7dea99d1358305f2fb605a6599/variants/think-api/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| OpenManus | [3309bf4e416fb1c74b008f3e86494439a31bad53](captures/openmanus/3309bf4e416fb1c74b008f3e86494439a31bad53/variants/default/prompt.md) | 1 | 2 | 2026-11-17 14:05 UTC |
| CUGA | [0.4.5 - 2026-11-03](captures/cuga-agent/0.4.5/variants/hello-tools-api/prompt.md) | 1 | 1 | 2026-11-17 13:13 UTC |
| AutoResearchClaw | [v0.5.0 - 2026-05-20](captures/autoresearchclaw/v0.5.0/variants/topic-init/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| Inkos | [1.8.0 - 2026-08-17](captures/inkos/1.8.0/variants/chat-openai/prompt.md) | 1 | 1 | 2026-11-17 13:12 UTC |
| DB-GPT | [547882b84b15b32b2894f1bccc6bfb535b7b70c3](captures/db-gpt/547882b84b15b32b2894f1bccc6bfb535b7b70c3/variants/official-react-example/prompt.md) | 2 | 2 | 2026-11-17 16:51 UTC |
| LoopTroop | [0.6.4 - 2026-11-06](captures/looptroop/0.6.4/variants/native-relevant-files-scan/prompt.md) | 1 | 1 | 2026-11-17 18:30 UTC |
| Hivekeep | [e6bf9aea4159312730a690b94697084616b44556](captures/hivekeep/e6bf9aea4159312730a690b94697084616b44556/variants/native-queenie-onboarding/prompt.md) | 1 | 1 | 2026-11-17 17:25 UTC |
| AnythingLLM | [472b1e3f4715c6ccd38a099aecd4651a449188aa](captures/anything-llm/472b1e3f4715c6ccd38a099aecd4651a449188aa/variants/native-workspace-agent/prompt.md) | 5 | 5 | 2026-11-21 10:31 UTC |
| LibreChat | [4700c44eb77c142b4f9f8b87476924f22890eb39](captures/librechat/4700c44eb77c142b4f9f8b87476924f22890eb39/variants/native-calculator-agent/prompt.md) | 2 | 2 | 2026-11-20 10:41 UTC |
| VibeSDK | [32c9e429cd2712ea5959a89f65ae434162b89229](captures/vibesdk/32c9e429cd2712ea5959a89f65ae434162b89229/variants/native-general-agentic/prompt.md) | 1 | 1 | 2026-11-17 18:46 UTC |
| DarkMoon | [539607182cc4070ddcb9cf8e75fa71d0fa6eacc8](captures/dark-moon/539607182cc4070ddcb9cf8e75fa71d0fa6eacc8/variants/native-compatible-pentest/prompt.md) | 4 | 4 | 2026-11-20 10:45 UTC |
| AutoGPT | [autogpt-platform-beta-v0.12.0 - 2026-11-19](captures/autogpt/autogpt-platform-beta-v0.12.0/variants/native-baseline-copilot/prompt.md) | 2 | 2 | 2026-11-20 10:47 UTC |
| QM | [0.1.18 - 2026-11-11](captures/qm/0.1.18/variants/native-local-web-turn/prompt.md) | 1 | 1 | 2026-11-17 18:39 UTC |
| Orkas | [3163c7ef1c2b03e21e6f85c6301515609361c570](captures/orkas/3163c7ef1c2b03e21e6f85c6301515609361c570/variants/native-electron-commander/prompt.md) | 1 | 1 | 2026-11-17 18:57 UTC |
| Dyad | [63d28b887c1b62b8509373ba58d4b03573bb714a](captures/dyad/63d28b887c1b62b8509373ba58d4b03573bb714a/variants/native-electron-basic-agent/prompt.md) | 2 | 2 | 2026-11-17 19:03 UTC |
| AgentLink | [3968dd5f4205973ddf3ab12e556bf58378b053b3](captures/agentlink/3968dd5f4205973ddf3ab12e556bf58378b053b3/variants/native-tui-openai/prompt.md) | 3 | 3 | 2026-11-22 10:24 UTC |
| UFO LinuxAgent | [eae3c0a8d168c47cb10df0652e293e092eda1078](captures/ufo/eae3c0a8d168c47cb10df0652e293e092eda1078/variants/linux-device-agent/prompt.md) | 1 | 1 | 2026-11-17 16:51 UTC |
| Agentlas OS | [6108604d8487519b820cac916c5061cd6e619688](captures/agentlas-os/6108604d8487519b820cac916c5061cd6e619688/variants/claude-core-ready-inline/prompt.md) | 3 | 4 | 2026-11-17 18:04 UTC |
| LongHorizon-Harness | [0.1.8 - 2026-10-21](captures/longhorizon-harness/0.1.8/variants/codex-manager-initial/prompt.md) | 1 | 1 | 2026-11-17 16:09 UTC |
| OpenAgents | [9024c135b7cf02a27f107bc52f98ddfcc928201b](captures/openagents/9024c135b7cf02a27f107bc52f98ddfcc928201b/variants/plain-microcoder/prompt.md) | 5 | 5 | 2026-11-18 11:02 UTC |
| Markus | [0.12.4 - 2026-11-14](captures/markus/0.12.4/variants/secretary-openai-api/prompt.md) | 1 | 1 | 2026-11-17 15:56 UTC |
| WeChat Mac RPA | [31cf55be1264acf35d436e754deda06833ec4087](captures/wechat-mac-rpa/31cf55be1264acf35d436e754deda06833ec4087/variants/portable-reply-api/prompt.md) | 4 | 4 | 2026-11-17 15:45 UTC |
| AutoRef | [2cd47b74226f76c785f0679e6ccc61e33e55b7e6](captures/autoref/2cd47b74226f76c785f0679e6ccc61e33e55b7e6/variants/scene-planner-initial/prompt.md) | 1 | 1 | 2026-11-17 15:45 UTC |
| Agent-S S3 | [05287e93e557078638219a9ed9ffc40d7a39772a](captures/agent-s/05287e93e557078638219a9ed9ffc40d7a39772a/variants/s3-linux-desktop/prompt.md) | 3 | 3 | 2026-11-21 10:43 UTC |
| DevOpsGPT | [dba1cb5ec9145ac871168e7f2e2d2b35c43d109b](captures/devopsgpt/dba1cb5ec9145ac871168e7f2e2d2b35c43d109b/variants/code-review/prompt.md) | 1 | 1 | 2026-11-17 18:10 UTC |
| Critic-training | [1c4aa0d3c28c522fe9e7541b14b86fe0c4a13e5c](captures/critic-training/1c4aa0d3c28c522fe9e7541b14b86fe0c4a13e5c/variants/prm-actor-initial/prompt.md) | 1 | 1 | 2026-11-17 15:37 UTC |
| Empryo / public SoulForge v2 | [c27dac116d2ee1637abfc6634bebf08b5c379454](captures/empryo/c27dac116d2ee1637abfc6634bebf08b5c379454/variants/public-soulforge-v2/prompt.md) | 3 | 3 | 2026-11-17 15:37 UTC |
| Web3GPT | [1c9ba797563c2305bd32bb30f078c9be2d3e2d55](captures/web3gpt/1c9ba797563c2305bd32bb30f078c9be2d3e2d55/variants/builtin-agent-api/prompt.md) | 1 | 1 | 2026-11-17 15:54 UTC |
| Atomic Agent | [v0.8.0 - 2026-11-17](captures/atomic-agent/v0.8.0/variants/cloud-openai/prompt.md) | 1 | 1 | 2026-11-17 15:28 UTC |
| Self-Operating Computer | [1.5.8 - 2025-02-28](captures/self-operating-computer/1.5.8/variants/default/prompt.md) | 1 | 1 | 2026-11-17 18:21 UTC |
| Hive | [1.0.12 - 2026-10-25](captures/hive/1.0.12/variants/queen-api/prompt.md) | 1 | 1 | 2026-11-17 15:29 UTC |
| IntentKit | [afc8cc44255210db6352f7437e569ed8b88ed5e1](captures/intentkit/afc8cc44255210db6352f7437e569ed8b88ed5e1/variants/local-lead-api/prompt.md) | 1 | 1 | 2026-11-17 15:29 UTC |
| MIRIX | [9077e4b229dd8f61c5703b431a97788c6b61acfb](captures/mirix/9077e4b229dd8f61c5703b431a97788c6b61acfb/variants/episodic-api/prompt.md) | 1 | 1 | 2026-11-17 15:29 UTC |
| Ouroboros | [v7.11.0 - 2026-11-17](captures/ouroboros/v7.11.0/variants/headless-task/prompt.md) | 1 | 1 | 2026-11-17 15:56 UTC |
| ZCode | [fec975905a2abd4c07e35c40d51fa097a2258ec9](captures/zcode/fec975905a2abd4c07e35c40d51fa097a2258ec9/variants/personal-openai/prompt.md) | 1 | 1 | 2026-11-17 14:23 UTC |
| Agent Zero | [2b0b3a416d80638379dc575798a4174ab4d3e1d9](captures/agent-zero/2b0b3a416d80638379dc575798a4174ab4d3e1d9/variants/context-api/prompt.md) | 3 | 3 | 2026-11-18 11:04 UTC |
| Raven | [v0.2.8 - 2026-11-17](captures/raven/v0.2.8/variants/default/prompt.md) | 1 | 1 | 2026-11-17 14:20 UTC |
| GPT Researcher | [ddd4db36208d9c8e49f3b2436efda97baecb5433](captures/gpt-researcher/ddd4db36208d9c8e49f3b2436efda97baecb5433/variants/deep-agent/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| OpenEvolve | [4167db4325727138215c55470fdd5cd302f74b66](captures/openevolve/4167db4325727138215c55470fdd5cd302f74b66/variants/function-minimization/prompt.md) | 6 | 6 | 2026-11-21 10:45 UTC |
| DeerFlow | [585abb979fbc483485c0fc53ed0e571ab7eebadb](captures/deer-flow/585abb979fbc483485c0fc53ed0e571ab7eebadb/variants/embedded-openai/prompt.md) | 6 | 6 | 2026-11-19 11:15 UTC |
| Omni-Rewriter | [846fee695fbfd9b816d481e294b5e385dabc4581](captures/omni-rewriter/846fee695fbfd9b816d481e294b5e385dabc4581/variants/api-writer-analysis/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| AgenticSeek | [8f705be8ca625e9549389f188b1e37f700d3ed34](captures/agenticseek/8f705be8ca625e9549389f188b1e37f700d3ed34/variants/coder-openai/prompt.md) | 1 | 1 | 2026-11-17 14:32 UTC |
| nanoMuse | [v1.0.9 - 2026-10-29](captures/nanomuse/v1.0.9/variants/default/prompt.md) | 1 | 1 | 2026-11-17 14:12 UTC |
| PR-Agent | [0.50.0 - 2026-11-08](captures/pr-agent/0.50.0/variants/plain-diff-review/prompt.md) | 1 | 1 | 2026-11-17 14:13 UTC |
| DeepAnalyze | [afc697a11675c59683b9f6596c3ab3b3d661182a](captures/deepanalyze/afc697a11675c59683b9f6596c3ab3b3d661182a/variants/vllm-client/prompt.md) | 1 | 1 | 2026-11-17 14:13 UTC |
| localGPT | [34f3107f56a88203859092312231b945a711c619](captures/localgpt/34f3107f56a88203859092312231b945a711c619/variants/default/prompt.md) | 1 | 1 | 2026-11-17 14:14 UTC |
| BambooAI | [2.8.1 - 2026-11-20](captures/bambooai/2.8.1/variants/sdk-free-quick/prompt.md) | 3 | 3 | 2026-11-20 10:58 UTC |
| CowAgent | [51ffe5a8df75295a5c063d5bc32a055c643a2599](captures/cowagent/51ffe5a8df75295a5c063d5bc32a055c643a2599/variants/bridge-api/prompt.md) | 5 | 5 | 2026-11-20 10:58 UTC |

## Project Trend

![Phistory star history](https://api.star-history.com/svg?repos=WEIFENG2333/phistory&type=Date)
