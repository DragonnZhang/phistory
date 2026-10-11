# October 11 trace refresh

Queried the registered official package sources at 2026-10-11T04:36:58Z. The downstream scope is the eight agents already present in qwen's published atlas.

| Agent | Latest stable | Published (UTC) | Previous atlas |
| --- | --- | --- | --- |
| claude-code | `2.1.296` | 2026-10-09T16:58:10.924Z | `2.1.296` |
| codex | `0.162.1` | 2026-10-09T19:50:30.512Z | `0.162.1` |
| kimi-code | `2.1.1` | 2026-09-24T07:27:15.480Z | `2.1.1` |
| qwen-code | `0.25.0` | 2026-10-05T09:55:55.980Z | `0.25.0` |
| qoder | `1.1.68` | 2026-10-10T13:51:49.408Z | `1.1.67` |
| openclaw | `2026.10.1` | 2026-10-10T22:45:05.897Z | `2026.9.9` |
| hermes | `v0.21.6` | 2026-10-08T11:51:57Z | `v0.21.6` |
| opencode | `1.19.0` | 2026-10-11T01:38:53.143Z | `1.18.35` |

The registered npm sources are `@anthropic-ai/claude-code`, `@openai/codex`, `@moonshot-ai/kimi-code`, `@qwen-code/qwen-code`, `@qoder-ai/qodercli`, `openclaw`, and `opencode-ai`; Hermes uses [official GitHub releases](https://github.com/NousResearch/hermes-agent/releases). Stable version enumeration confirms no intervening releases for the three changed agents. Existing complete captures and all model lanes are retained.

## Model evidence and access limitation

The latest Claude Code and Codex releases are unchanged. Their cached exact platform archives were rechecked against freshly fetched npm SHA-512 integrity metadata; their binaries remain identical to the [October 10 baseline](trace-atlas-models-2026-10-10.md). No CLI or model request ran locally.

| Exact released implementation | Bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.296` | 240664432 | `c9b5341637becbd423ddffc5b254afb645682a3868cb708bbc6cc0e7bb419937` |
| `@openai/codex@0.162.1-linux-x64` | 294415048 | `628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1` |

The Claude binary still contains the six current general/specialized catalog IDs recorded in the existing [anchor baseline](../../.claude/design/anchors-2.1.296-trace-models.txt): Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5, Mythos 5.1 and Mythos 5. The [Codex release-tag catalog](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/models-manager/models.json), fetched again through GitHub, is byte-identical to the embedded binary catalog: SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`. Its eight listed GPT model IDs are already archived; Daybreak and auto-review remain hidden. There is no new model in these released CLI catalogs, and no support floor is changed.

Current official documentation could not be independently read during this run. [Claude's model overview](https://platform.claude.com/docs/en/models/overview) and [release notes](https://platform.claude.com/docs/en/release-notes/overview) redirected to an unavailable-region page, including alternate official/Markdown routes. [OpenAI's model catalog](https://developers.openai.com/api/docs/models), [Codex model documentation](https://developers.openai.com/codex/models), and [Codex availability](https://learn.chatgpt.com/docs/models) returned HTTP errors or connection timeouts; the availability page also timed out in Chrome. Official-domain search attempts were unavailable. The October 10 documentation findings are prior evidence, not a fresh October 11 catalog check. This refresh therefore makes no claim that today's official model announcements or service rollout were exhaustively checked. Existing lanes remain supported by exact released implementations and Linux traces; no speculative lane is added.

## Linux capture validation

Full latest capture and fresh-cache smoke [38112177333](https://github.com/DragonnZhang/phistory/actions/runs/38112177333) succeeded, including all 160 tests, formatting, lint, package build and both renderers. It committed the three requested captures in [`e7c511c0`](https://github.com/DragonnZhang/phistory/commit/e7c511c0ec29a486a643b17cc8e513ef6c3c5ebd); the shared workflow also archived out-of-atlas Oh My Pi 18.8.9. Qoder's credentialed capture and secret archive check passed. All three new atlas records have nonempty prompt/trace/meta, valid main requests, exit code zero, matching exact versions, and genuine Linux GitHub Actions provenance. The local files match the CI commit byte-for-byte. Their observed/extracted tool counts agree: Qoder 31, OpenClaw 12, OpenCode 10.

All 29 in-scope smoke artifacts were downloaded and checked, covering fourteen Claude Code lanes, ten Codex lanes, and five other agents. Nine Codex tool-count differences are the existing top-level namespace/additional-tools versus expanded-tool counting distinction; the original counts and traces remain intact. MiniMax's repaired installer also passed the full smoke. Claude Code's recent wrappers still explicitly skip static extraction because they contain no extractable source; the request prompts and tools are archived normally.

[Pages deployment](https://github.com/DragonnZhang/phistory/actions/runs/38112474382) succeeded, and the online manifest matches the committed index: SHA-256 `b67c53d9bd72379d9569f11dcf4e431b41c7841188e5fc43d2cd3ed1f303cf05`. Existing captures were not rewritten.
