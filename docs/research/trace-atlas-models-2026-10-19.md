# October 19 trace refresh

The eight atlas agents were checked against their registered official package sources at `2026-10-19T04:33:56.101859+00:00`. Claude Code and OpenClaw have one new stable release each. All intervening stable versions were checked; no historical backfill is needed. Existing model lanes and display boundaries remain unchanged.

| Agent | Latest stable | Previous atlas | Stable versions added |
| --- | --- | --- | --- |
| claude-code | `2.1.303` | `2.1.302` | `2.1.303` |
| codex | `0.163.0` | `0.163.0` | — |
| kimi-code | `2.2.0` | `2.2.0` | — |
| qwen-code | `0.25.2` | `0.25.2` | — |
| qoder | `1.1.73` | `1.1.73` | — |
| openclaw | `2026.10.5` | `2026.10.4` | `2026.10.5` |
| hermes | `v0.22.2` | `v0.22.2` | — |
| opencode | `1.19.3` | `1.19.3` | — |

Version discovery uses official npm metadata and [Hermes GitHub releases](https://github.com/NousResearch/hermes-agent/releases). Claude Code 2.1.303 was published `2026-10-18T21:15:23.178Z`; OpenClaw 2026.10.5 was published `2026-10-18T21:16:43.569Z`. The prior [October 18 scheduled capture](https://github.com/DragonnZhang/phistory/actions/runs/39107475976) added only out-of-atlas Antigravity 1.4.2, MiniMax Code 3.1.3 and Oh My Pi 18.12.1, in `3c7f74d178317ca8dbd90330afd133a5a0e2da79`.

## Model discovery and released implementation

The [OpenAI model catalog](https://developers.openai.com/api/docs/models), [Codex model availability](https://learn.chatgpt.com/docs/models), [Claude model overview](https://platform.claude.com/docs/en/models/overview), [Claude release notes](https://platform.claude.com/docs/en/release-notes/overview), and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were fetched and read on October 19. The current general Claude lineup remains Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5; the latest platform release note is October 15 and introduces no new model. The overview's omission of a specialized-model section does not establish Mythos retirement. Existing Mythos 5/5.1 lanes retain their verification-required boundary. OpenAI's published coding models are already represented; ChatGPT sign-in retirement and API compatibility are separate from archived model history. No new supported model lane was found.

Both exact platform archives passed fresh npm SHA-512 integrity checks. The unchanged Codex archive was reused only after comparison with the current exact-version metadata.

| Released implementation | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.303` | 267973136 | `d2da3b003dc08b88b7230469014fe3e1dc6fe94bc8f648e89b34175d30269370` |
| `@openai/codex@0.163.0-linux-x64` | 296865480 | `399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215` |

Claude Code 2.1.303 was built `2026-10-18T20:20:57Z` from `07df2aedd2d01d2f60907b8404acbda187b04072`: 16,416 bytes larger and built 2 days 4:28:16 later than the prior baseline. The [scoped anchor baseline](../../.claude/design/anchors-2.1.303-trace-models.txt) confirms the same 22 catalog model IDs as 2.1.302; this does not claim all catalog fields are identical. The official configuration supports the existing Sonnet 5.5, Opus 5.5 and Haiku 5.5 boundaries. Fable 5.1's published display start stays at 2.1.258, despite configuration compatibility from 2.1.257. Static evidence establishes configured identities, not provider access. The old source dictionary was not needed; no local prompt-bearing probe was run.

Codex's [0.163.0 release catalog](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/models-manager/models.json) was fetched again and remains byte-identical to the previous audit, SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`. It occurs unchanged at binary offset `245669092`. All eight visible model IDs have existing lanes; Daybreak and auto-review remain hidden.

## Capture validation and observed changes

[Latest capture and full smoke 39200441254](https://github.com/DragonnZhang/phistory/actions/runs/39200441254) passed formatting, lint, all 160 tests, package build, capture and fresh-cache smoke. Qoder's separate credential capture and archive check passed. The workflow committed the 15 in-scope captures in `5c935fb28e075b145c8a8a0e828b5e9bc08c5ccf`, plus out-of-atlas Oh My Pi 18.12.2. This does not expand the atlas scope.

All 15 new records were checked against committed Git blobs: captured status, nonempty prompt/trace/meta, valid main request, requested and observed model identities, zero CLI exit status, and genuine Linux GitHub Actions provenance. All prior capture files remained unchanged. The 29 in-scope latest smoke records have identical complete tool declarations and normalized system/developer text to their committed captures. Qoder is excluded from generic smoke and uses its separate credential step. Codex namespace counts were checked against declarations in `input[].additional_tools`; the atlas expands their functions and preserves the original mismatch flags.

All 14 Claude Code 2.1.303 lanes preserve the prior version's normalized system text and complete tool declarations. OpenClaw 2026.10.5 retains the same 12 ordered wire-tool names but changes its system text from 11,453 to 11,330 tokens: 262 added and 385 removed. Its archived text revises learned-skill editing guidance, plugin reload/resume and permitted-session descriptions, makes unconfigured web search explicit, clarifies CLI versus Gateway identity writes, and adds the `x` message channel. The `exec` declaration separately revises host, yielding and background-service guidance and removes `sandbox` from its host enum. These schema edits are not counted as system text; tool-use descriptions that actually occur in the system message remain counted. These are facts from the captured requests, not claims about every conditional CLI surface.

Claude Code static extraction completed with the existing explicit skip: the current npm wrapper has no source accepted by the extractor. Request prompts and tools are archived; a complete updated static string library is not established.

The final capture manifest SHA-256 is `df9c41b9e62296aebfcdf0c09f6f1e2abb7275a85cbff991853a70549bd1b332`. The resulting downstream scope is eight agents, 30 lanes, 4,600 captures and 1,147 default points, with all model display boundaries preserved.

[Pages deployment 39201098089](https://github.com/DragonnZhang/phistory/actions/runs/39201098089) succeeded. Its live manifest was downloaded and compared byte-for-byte with the committed capture manifest before publishing this evidence record.
