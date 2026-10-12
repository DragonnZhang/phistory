# October 12 trace refresh

The official package sources were checked on October 12, 2026 (UTC) for the eight agents already present in qwen's published atlas. Qwen Code is the only new stable release in that scope.

| Agent | Latest stable | Published (UTC) | Previous atlas |
| --- | --- | --- | --- |
| claude-code | `2.1.296` | 2026-10-09T16:58:10.924Z | `2.1.296` |
| codex | `0.162.1` | 2026-10-09T19:50:30.512Z | `0.162.1` |
| kimi-code | `2.1.1` | 2026-09-24T07:27:15.480Z | `2.1.1` |
| qwen-code | `0.25.1` | 2026-10-11T10:29:32.140Z | `0.25.0` |
| qoder | `1.1.68` | 2026-10-10T13:51:49.408Z | `1.1.68` |
| openclaw | `2026.10.1` | 2026-10-10T22:45:05.897Z | `2026.10.1` |
| hermes | `v0.21.6` | 2026-10-08T11:51:57Z | `v0.21.6` |
| opencode | `1.19.0` | 2026-10-11T01:38:53.143Z | `1.19.0` |

Version discovery uses each registered official npm source and [Hermes releases](https://github.com/NousResearch/hermes-agent/releases). Stable enumeration from Qwen Code `0.25.0` to `0.25.1` found no intervening version. The existing 30 lanes have no missing archived versions. The earlier scheduled capture added only Oh My Pi `18.9.0`; the eight atlas agents' capture directories were byte-identical to the October 11 source before this refresh.

## Current model evidence

Official documentation was successfully read today, resolving the access limitation reported on October 11. The [OpenAI API model catalog](https://developers.openai.com/api/docs/models) and [Codex model availability](https://learn.chatgpt.com/docs/models) list the already archived GPT-6.1 Sol, GPT-6 Astra, GPT-6 Sol/Luna and GPT-5.6 Sol/Terra/Luna. GPT-5.5 remains archived; its October 14 retirement from ChatGPT/Work/Codex does not remove its history. Ultra and Ultrafast are modes, not additional model IDs.

The [Claude model overview](https://platform.claude.com/docs/en/models/overview), [release notes](https://platform.claude.com/docs/en/release-notes/overview), and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) confirm Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5. The latest model launch in those release notes is Haiku 5.5 on October 7. The separate [Mythos 5.1](https://platform.claude.com/docs/en/models/mythos-5-1/overview) and [Mythos 5](https://platform.claude.com/docs/en/models/mythos-5/overview) pages still require organization verification. These model IDs are all covered by the existing lanes; provider eligibility is not established by capture-only requests.

Fresh npm metadata confirms unchanged Claude Code `2.1.296` and Codex `0.162.1`. Cached exact platform archives passed the freshly queried npm SHA-512 integrity checks. No CLI request ran locally.

| Released implementation | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.296` | 240664432 | `c9b5341637becbd423ddffc5b254afb645682a3868cb708bbc6cc0e7bb419937` |
| `@openai/codex@0.162.1-linux-x64` | 294415048 | `628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1` |

The Claude model catalog anchors still match the [existing binary baseline](../../.claude/design/anchors-2.1.296-trace-models.txt). The [Codex release-tag catalog](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/models-manager/models.json) was fetched again through GitHub and matches the exact binary's embedded bytes at offset `243481218`; SHA-256 is `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`. All eight models with `visibility=list` have registered and captured lanes. Daybreak and auto-review retain their existing hidden status. No new model lane or support/display boundary is needed.

## Linux capture validation

[Capture and fresh-cache smoke 38204550043](https://github.com/DragonnZhang/phistory/actions/runs/38204550043) completed successfully. Formatting, lint, all 160 tests, package build, both renderers, Qoder's credentialed capture/secret check and the full smoke passed. The run committed Qwen Code `0.25.1` in [`bca9cfad`](https://github.com/DragonnZhang/phistory/commit/bca9cfadabad5aef72560daa5abaca09e389c29d); the shared workflow also archived out-of-atlas Oh My Pi `18.10.0`.

The new Qwen record has a nonempty prompt, raw trace and metadata; a valid main request on trace line 1; model `qwen3.8-max`; exit code zero; and actual Linux GitHub Actions provenance for this run. Local files match the committed files byte-for-byte. Fresh-cache smoke reproduces the exact tool list and normalized system text. The tool count changes from 14 to 10, removing `agent`, `get_goal`, `list_agents`, and `update_goal` from this default request surface. This is not a claim about every conditional tool available in the CLI. System control increases from 4,951 to 6,952 tokens, with 2,510 added and 509 removed; the change is in memory instructions, not collection-environment values.

All 29 in-scope smoke artifacts were downloaded and individually checked: fourteen Claude Code lanes, ten Codex lanes, and five other agents. The nine Codex count differences match the previously documented namespace/additional-tools versus expanded-tool distinction. Raw counts and traces are preserved. Claude Code's recent wrappers still explicitly skip static extraction because they contain no extractable source; their request prompts and tools are archived normally.

The committed capture manifest SHA-256 is `b40df2a368eae960030a1b663b389ce8ef621c31a054c6778107f3bdc40860c8`. Existing captures were not rewritten.
