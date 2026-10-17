# October 17 trace refresh

The eight atlas agents were checked against their registered official package sources at `2026-10-17T06:55:11.092997+00:00`. Six agents have newer stable releases; the unchanged Kimi Code and Hermes archives remain in scope.

| Agent | Latest stable | Previous atlas | Stable versions added |
| --- | --- | --- | --- |
| claude-code | `2.1.302` | `2.1.300` | `2.1.301`, `2.1.302` |
| codex | `0.163.0` | `0.162.1` | `0.163.0` |
| kimi-code | `2.2.0` | `2.2.0` |  |
| qwen-code | `0.25.2` | `0.25.1` | `0.25.2` |
| qoder | `1.1.73` | `1.1.71` | `1.1.72`, `1.1.73` |
| openclaw | `2026.10.4` | `2026.10.3` | `2026.10.4` |
| hermes | `v0.22.2` | `v0.22.2` |  |
| opencode | `1.19.3` | `1.19.0` | `1.19.1`, `1.19.2`, `1.19.3` |

Version discovery uses official npm metadata and [Hermes GitHub releases](https://github.com/NousResearch/hermes-agent/releases). The successful [October 16 scheduled run](https://github.com/DragonnZhang/phistory/actions/runs/38867410684) already committed 28 in-scope captures in `54801a691098f749a5e57d0dc890a83dc57e99c3`. The [latest capture and smoke](https://github.com/DragonnZhang/phistory/actions/runs/39012057471) added 16 in-scope records in `ed38f2256d69832997dbc9eadec78147695a7aac`: Claude Code 2.1.302, Qoder 1.1.73 and OpenCode 1.19.3. The [targeted backfill and fresh-cache smoke](https://github.com/DragonnZhang/phistory/actions/runs/39012145905) added the intervening OpenCode 1.19.1 in `a75594bb99f06f492d91b98b948eb8742646b257`. Both workflows completed successfully.

## Model evidence

The [OpenAI API model catalog](https://developers.openai.com/api/docs/models) and [Codex availability](https://learn.chatgpt.com/docs/models) were read on October 17. The current coding lineup is already covered. The GPT-5.5 ChatGPT-sign-in retirement remains distinct from API-key support; its historical capture-only lane is preserved. Speed and reasoning modes do not create new model lanes.

The [Claude model overview](https://platform.claude.com/docs/en/models/overview), [platform release notes](https://platform.claude.com/docs/en/release-notes/overview) and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were fetched and read. The latest October 15 notes cover Compliance API and Opus 5.5 fast mode availability, not a new model. Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5 remain the current general lineup; the existing Mythos lines keep their verification-required boundary. No new supported lane or display-boundary change was found.

Both exact platform archives passed their current npm SHA-512 integrity checks:

| Released implementation | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.302` | 267956720 | `5262ce9de86243fed0d21edc39b271555d56780d0112e2b194439fe1c6662d37` |
| `@openai/codex@0.163.0-linux-x64` | 296865480 | `399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215` |

Claude Code 2.1.302 was built `2026-10-16T15:52:41Z` from `2ef144e6f98c1c99d082b735e4b38f50e3391e55`. Its [scoped anchor baseline](../../.claude/design/anchors-2.1.302-trace-models.txt) verifies the same 22 catalog model IDs as 2.1.300; this check does not claim every catalog field is identical. Static evidence establishes configured identities, not provider entitlement. No local prompt-bearing probe was run.

Codex’s [0.163.0 release catalog](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/models-manager/models.json) is byte-identical to 0.162.1, SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`, and occurs unchanged at binary offset `245669092`. All eight visible model IDs have existing lanes. Daybreak and auto-review remain hidden.

## Capture validation and observed changes

All 45 new atlas records were verified against their committed Git blobs: captured status, nonempty prompt/trace/meta, valid main request, requested and observed model identities, zero CLI exit status, and genuine Linux GitHub Actions provenance. Existing capture files were not rewritten. The two workflows each passed formatting, lint and all 160 tests; the latest workflow also passed the package build and Qoder credential archive check.

The 29 in-scope latest smoke records, plus the OpenCode 1.19.1 targeted smoke, have the same ordered tool names and normalized system/developer text as their corresponding committed captures. Qoder uses its separate credential capture step and is excluded from the generic smoke. Codex namespace counts were checked against declarations in `input[].additional_tools`; the atlas expands their functions. This transport and counting distinction already existed in 0.162.1 and is not a new tool change.

All 45 adjacent updates preserve ordered tool names. Claude Code 2.1.301 also preserves normalized system text on all 14 lanes. In 2.1.302, eight lanes have real text changes:

| Lane | System tokens before → after | Added / removed tokens | Observed change |
| --- | --- | --- | --- |
| default | 3,369 → 3,259 | 0 / 110 | Removes optional Bash-routing guidance under Auto Mode |
| non-official | 3,282 → 3,172 | 0 / 110 | Same guidance removal |
| official-opus-5-5 | 3,376 → 3,266 | 0 / 110 | Same guidance removal |
| official-fable-5-1 | 4,633 → 4,552 | 0 / 81 | Removes stronger Bash-first Auto Mode guidance |
| official-mythos-5-1 | 4,481 → 4,400 | 0 / 81 | Same guidance removal |
| official-opus-4-7 | 5,673 → 5,634 | 73 / 112 | Replaces Explore/delegation guidance with context-inheriting fork guidance |
| official-haiku | 5,673 → 5,634 | 73 / 112 | Same fork guidance update |
| official | 7,990 → 7,729 | 73 / 334 | Fork guidance update and removal of Auto Mode Active reminder |

The six other Claude lanes and every new Codex, Qwen Code, Qoder, OpenClaw and OpenCode point preserve normalized system text. These are comparisons of the archived requests, not claims about all conditional CLI surfaces. Raw environment values remain intact in the traces.

Claude Code static extraction completed with the existing explicit skip: the current npm wrapper has no source accepted by the extractor. Request prompts and tools are archived; this does not establish a complete updated static string library.

The final capture manifest has SHA-256 `cd8db0f4df532a9c306feae0b439ca8eb607dde9d6cef279ffed6748fb544932`. The resulting downstream scope is eight agents, 30 lanes, 4,585 captures and 1,145 default points, with all model display boundaries preserved. Latest also archived out-of-atlas Grok 1.0.62, MiniMax Code 3.1.2 and Oh My Pi 18.11.3; these do not extend the atlas scope.

[Pages deployment 39012615539](https://github.com/DragonnZhang/phistory/actions/runs/39012615539) succeeded. The live manifest was downloaded and compared byte-for-byte with the committed capture manifest before publication of this evidence record.
