# October 21 trace refresh

The eight atlas agents were checked against their registered official package sources at `2026-10-21T04:31:56.617843+00:00`. Claude Code, Qwen Code and Qoder have new stable releases. All intervening stable versions were checked; Claude Code 2.1.305 needs a targeted backfill between scheduled 2.1.304 and latest 2.1.306. Existing model lanes and display boundaries remain unchanged.

| Agent | Latest stable | Previous atlas | Stable versions added |
| --- | --- | --- | --- |
| claude-code | `2.1.306` | `2.1.303` | `2.1.304`, `2.1.305`, `2.1.306` |
| codex | `0.163.0` | `0.163.0` | — |
| kimi-code | `2.2.0` | `2.2.0` | — |
| qwen-code | `0.25.3` | `0.25.2` | `0.25.3` |
| qoder | `1.1.75` | `1.1.73` | `1.1.74`, `1.1.75` |
| openclaw | `2026.10.5` | `2026.10.5` | — |
| hermes | `v0.22.2` | `v0.22.2` | — |
| opencode | `1.19.3` | `1.19.3` | — |

Discovery uses official npm metadata and [Hermes GitHub releases](https://github.com/NousResearch/hermes-agent/releases). The [October 19 scheduled run](https://github.com/DragonnZhang/phistory/actions/runs/39219938911) archived Qwen Code 0.25.3. The [October 20 scheduled run](https://github.com/DragonnZhang/phistory/actions/runs/39389462155) archived all 14 Claude Code 2.1.304 lanes and Qoder 1.1.74. These 16 existing records are reused from committed CI artifacts.

## Model discovery and released implementation

The [OpenAI API model catalog](https://developers.openai.com/api/docs/models), [Codex model availability](https://learn.chatgpt.com/docs/models), [Claude model overview](https://platform.claude.com/docs/en/models/overview), [Claude release notes](https://platform.claude.com/docs/en/release-notes/overview), and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were fetched and read on October 21. The Claude general lineup remains Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5. The October 20 platform notes concern inference hooks, user profiles and webhooks, not a new model. Both Mythos lanes retain their verification-required status. Codex's current general coding models are already represented; ChatGPT sign-in retirement is separate from API compatibility and archived history. No new supported model lane was found.

Both exact platform archives passed fresh npm SHA-512 integrity checks:

| Released implementation | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.306` | 272984176 | `b5d795b7760032d79954b55a9cd1876dcf3a8dc9608fd6bb9f618593f69fd44b` |
| `@openai/codex@0.163.0-linux-x64` | 296865480 | `399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215` |

Claude Code 2.1.306 was built `2026-10-21T00:58:51Z` from `01ae8327ca9931db91a0802d6e8e377419dd44e6`. It is 5,011,040 bytes larger and built 2 days, 4:37:54 after 2.1.303. The [scoped anchor baseline](../../.claude/design/anchors-2.1.306-trace-models.txt) confirms the same 22 catalog model IDs; this does not claim all catalog fields are identical. The documented model support boundaries remain unchanged. Fable 5.1 retains its published display start at 2.1.258, despite documented configuration compatibility from 2.1.257. No local prompt-bearing probe was run, and the older source dictionary was not needed.

The freshly fetched [Codex 0.163.0 release catalog](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/models-manager/models.json) remains SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`, byte-identical to the prior audit, and occurs at binary offset `245669092`. All eight visible model IDs have existing lanes. Daybreak and auto-review remain hidden.


## Captures and observed changes

The [latest workflow](https://github.com/DragonnZhang/phistory/actions/runs/39544774054) and [2.1.305 backfill](https://github.com/DragonnZhang/phistory/actions/runs/39544780135) both completed successfully. Each passed formatting, lint and 160 tests; latest also built both distributions. Latest added 15 atlas records (Claude Code 2.1.306 ×14 and Qoder 1.1.75); backfill added 14 records. Together with the 16 scheduled records, the atlas gains 45 captures and six default points, reaching 4,645 records across 30 lanes and 1,153 default points.

All 45 additions have nonempty prompt/trace/meta files, valid primary requests, matching requested/observed model IDs, exit status zero and actual Linux GitHub Actions `capture_host` run identity. File bytes match committed Git blobs. All 43 atlas-scope fresh-cache smoke captures (29 latest excluding Qoder, 14 backfill) match the committed full wire tool declarations and normalized system/developer text after the runtime-version correction below. Qoder's separate authenticated capture and secret scan passed. Codex namespace declaration counts still differ from flattened tool counts as previously documented; no new unexplained tool-count mismatch was found.

Observed adjacent changes:

- Claude Code 2.1.304 updates the `plugin-authoring` skill description in eleven existing lanes, adding nine and removing twelve tokens per lane. Opus 4.7 and Haiku 4.5 system text remain unchanged. The non-official lane additionally adopts deferred tool loading: the directly declared table shrinks from 21 to 12, eleven tools move to the deferred-name reminder, and `ToolSearch` plus `DeferredToolPlaceholder` are added. Its system text adds 96 and removes 12 tokens (3,172 → 3,256). This is a request-surface change, not removal of all those capabilities. The other thirteen lanes' full tool declarations remain unchanged.
- All fourteen Claude Code lanes are unchanged from 2.1.304 through 2.1.305 and 2.1.306, after normalization.
- Qwen Code 0.25.3 retains ten tools and 6,952 system tokens; complete declarations and normalized text match 0.25.2.
- Qoder 1.1.74 retains 31 tool names but adds image-to-image editing to `ImageGen`, including an optional `images` array with at most three URL or local-path inputs. The full schema persists in 1.1.75. Neither version changes normalized system text; both have 2,814 system tokens under the corrected normalization.

The static extraction step completed with explicit skips for wrapper packages lacking extractable source, including 2.1.304 and 2.1.306. Backfill also skips static extraction. This existing limitation remains: current wire prompts/tools and the scoped binary model catalog are verified, but a complete latest static prompt library is not claimed.

## Runtime-version correction

Independent Hermes v0.22.2 smoke exposed only a host-kernel change, `Host: Linux (6.17.0-1022-azure)` → `Host: Linux (7.0.0-1017-azure)`. Qoder 1.1.74 → 1.1.75 exposed only `Node.js version: v24.21.0` → `v24.22.0` in system text. These are collector-environment values, not model instructions.

The atlas and Phistory display normalization now replace these labelled values while preserving the OS family, field presence, surrounding instructions and version requirements in prose. Serialized and rendered containers share the same treatment. Phistory's raw capture files, capture host and readable capture Markdown are not rewritten. Tests cover idempotence, both representations, real rule changes, raw-evidence immutability, and Qoder's genuine ImageGen schema change. All 162 Phistory tests, formatting, lint and package build pass; both render commands were run.

A before/after scan of all 4,645 records found 157 affected prompt records (128 Qoder, 29 Hermes), including 155 existing records. It eliminates three pure-noise Qoder events (1.1.41, 1.1.61, 1.1.75), recomputes 43 retained event records, and preserves all other text. Retained-event recomputation includes length/section statistics even when added/removed counts remain equal. The downstream `prompt-runtime-version-audit.json` lists every affected version and event; full-history regression uses this audit in addition to the original normalization audit. Tool names, capture membership and source evidence are unchanged.

The local rendered Phistory page confirms Qoder 1.1.74 → 1.1.75 System says “No changes”; 1.1.73 → 1.1.74 All content still shows image-to-image schema support and includes user messages. System excludes user messages.

## Published capture source

Capture source: [`2babb729242376c177f59c830076bb639bfbd551`](https://github.com/DragonnZhang/phistory/commit/2babb729242376c177f59c830076bb639bfbd551), following latest capture commit `795b7b8f49f24a8848c82101d41ee8ab6ee315a7`. The [capture Pages deployment](https://github.com/DragonnZhang/phistory/actions/runs/39545688995) passed; the live manifest was fetched and matched the committed bytes:

`14fd91c7ce2baedc4ecb9ef27927228b88c4d749d57b0b135a2d3cf8d1f1a034`

Only `captures/index.json` changes among previously tracked capture files since the October 19 atlas source; all other changes under `captures/` are additions. No raw trace or host metadata is rewritten. The subsequent display/evidence commit retains the same manifest; qwen methodology pins that final source commit and its final Pages deployment.
