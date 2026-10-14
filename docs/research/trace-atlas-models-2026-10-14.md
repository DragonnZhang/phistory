# October 14 trace refresh

The eight agents already present in qwen’s atlas were checked against official package sources at `2026-10-14T04:32:24.125600+00:00`. Four agents have new stable releases; no intervening stable version was found in any of the four ranges.

| Agent | Latest stable | Published (UTC) | Previous atlas |
| --- | --- | --- | --- |
| claude-code | `2.1.299` | 2026-10-14T00:23:19.588Z | `2.1.296` |
| codex | `0.162.1` | 2026-10-09T19:50:30.512Z | `0.162.1` |
| kimi-code | `2.1.1` | 2026-09-24T07:27:15.480Z | `2.1.1` |
| qwen-code | `0.25.1` | 2026-10-11T10:29:32.140Z | `0.25.1` |
| qoder | `1.1.70` | 2026-10-13T10:09:26.289Z | `1.1.69` |
| openclaw | `2026.10.3` | 2026-10-13T13:43:36.600Z | `2026.10.2` |
| hermes | `v0.22.0` | 2026-10-14T03:21:46Z | `v0.21.7` |
| opencode | `1.19.0` | 2026-10-11T01:38:53.143Z | `1.19.0` |

Version discovery uses the registered official npm sources and [Hermes releases](https://github.com/NousResearch/hermes-agent/releases). The prior scheduled run added only out-of-atlas Antigravity `1.3.4`; all eight atlas capture directories were unchanged before this refresh.

## Model discovery

The [OpenAI API catalog](https://developers.openai.com/api/docs/models) and [Codex model availability](https://learn.chatgpt.com/docs/models) were searched and read on October 14. The current coding-model IDs remain GPT-6.1 Sol, GPT-6 Astra, GPT-6 Sol/Luna and the existing GPT-5.6 family. GPT-5.5’s announced retirement from ChatGPT/Work/Codex is today; the API is unaffected. Its archived capture-only compatibility lane is retained. Ultra and Ultrafast are modes rather than additional model IDs.

The [Claude model overview](https://platform.claude.com/docs/en/models/overview), [release notes](https://platform.claude.com/docs/en/release-notes/overview) and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were read again. Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5 remain the general lineup. The latest release-notes entry is the October 9 managed-workflow feature, and the latest model launch there is Haiku 5.5 on October 7. Existing Mythos lanes retain their documented verification requirement. No new supported model ID or support/display boundary was found.

Fresh exact npm metadata resolves Claude Code’s wrapper to `@anthropic-ai/claude-code-darwin-arm64@2.1.299`. The downloaded platform archive passed SHA-512 integrity verification. Its binary is 247,931,696 bytes (7,267,264 bytes larger than 2.1.296), SHA-256 `ba5ab7b8c195fa03ab8d5bf3915c76153b576a2c40e79a5c46e265e7ed9744b3`, built `2026-10-13T21:37:33Z` from `c416f56ec7eca27e49747c35af51d5c92c856ba1`.

The [new scoped binary baseline](../../.claude/design/anchors-2.1.299-trace-models.txt) compares all 22 catalog objects with the prior binary. There are no added or removed model IDs. Changes in eleven objects are confined to image-limit metadata and effort-cost indices: all eleven change image limits, and Haiku 5.5, Sonnet 5.5 and Opus 5.5 also gain effort-cost indices. Other catalog fields are unchanged. These are static implementation facts, not measurements of provider prices or live model behavior. No old-source mapping or local CLI request was used.

Codex remains `0.162.1`. Its exact Linux x64 archive passed the freshly queried npm SHA-512 check; binary SHA-256 is `628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1`. The [release-tag model catalog](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/models-manager/models.json) was fetched again and matches the embedded binary bytes at offset `243481218`; SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`. All eight `visibility=list` IDs have registered lanes; Daybreak and auto-review remain hidden in the unchanged catalog.

## Linux capture validation

[Capture and fresh-cache smoke 38512195534](https://github.com/DragonnZhang/phistory/actions/runs/38512195534) completed successfully. Formatting, lint, all 160 tests, package build, both renderers, Qoder’s credentialed capture/secret check and the full smoke passed. The run committed the new captures in [`e720f644`](https://github.com/DragonnZhang/phistory/commit/e720f64431ec299473a81cdfe83c2311d8da31d5), including out-of-atlas Antigravity `1.3.5` and Oh My Pi `18.10.4`.

All 17 in-scope records were individually checked: fourteen Claude Code `2.1.299` lanes and one each for Qoder `1.1.70`, OpenClaw `2026.10.3` and Hermes `v0.22.0`. Each has nonempty prompt, raw trace and metadata, a valid main request, exit code zero, matching model selection where exposed, and actual Linux GitHub Actions provenance from this run. Local files match the committed artifacts byte-for-byte. Qoder’s main request is on trace line 2 and does not expose a model ID; OpenClaw’s is on line 1 and Hermes’s on line 12, both with the capture-only `phistory-dummy` model. Claude Code default resolves to `claude-opus-5-5`; its fixed lanes retain their requested model IDs, with the existing `[1m]` wire normalization.

| Agent / lane | Tools before → after | System tokens before → after | Normalized system diff |
| --- | ---: | ---: | --- |
| Claude Code default | 12 → 12 | 3,369 → 3,369 | unchanged |
| Claude Code non-official | 21 → 21 | 3,282 → 3,282 | unchanged |
| Claude Code twelve fixed-model lanes | 12 → 12 each | unchanged in each lane | unchanged |
| Qoder | 31 → 31 | 2,815 → 2,815 | unchanged |
| OpenClaw | 12 → 12 | 11,453 → 11,453 | unchanged |
| Hermes | 24 → 20 | 2,771 → 2,674 | +0 / −97 tokens |

The ordered tool names and normalized system text are unchanged for every new lane except Hermes. Hermes adds `manage_connections` and removes `browser_vault_enter_code`, `browser_vault_fill`, `browser_vault_list`, `browser_vault_save_login` and `browser_vault_unlock` from the directly exposed tool list. Its tool-search description now lists a deferred `browser_vault`; absence from the direct list does not establish removal of that capability.

The only normalized system-text change in Hermes is removal of the `Async handoff` section, which told the agent to end its turn when a delegated result could arrive only afterward. The following `Skills` text is unchanged. Removing the heading changes how the existing section classifier assigns the subsequent text between workflow and harness sections; the section-level moves are not additional body edits. Tool-schema and tool-description changes remain separate from the system metric.

All 29 in-scope fresh-cache smoke artifacts were downloaded and individually checked: fourteen Claude Code lanes, ten Codex lanes and five ordinary agents. All new non-Qoder lanes reproduce the source ordered tool list and normalized system text exactly. Qoder runs in its separate credentialed step and is not part of this smoke matrix. The nine known Codex namespace/additional-tools versus expanded-tool count differences are unchanged; raw values and evidence are preserved.

The static-extraction step explicitly reports that recent Claude Code npm wrappers, including `2.1.299`, contain no extractable source. Request prompts and tools are archived normally; this does not claim a refreshed complete static string library.

[Pages deployment 38512914861](https://github.com/DragonnZhang/phistory/actions/runs/38512914861) succeeded. The online manifest was compared byte-for-byte with the committed manifest; SHA-256 is `ed5e11dc8310cf49bbe615d2b2332edb758badb11154ac47aa77b861c1bacfc9`. Since the previous atlas source, every capture-file change is an addition except the manifest update; no prior prompt, metadata or raw trace was rewritten. The atlas will add four default version points and 17 records, preserving all eight agents, 30 lanes and existing model boundaries.
