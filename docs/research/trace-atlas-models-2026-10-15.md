# October 15 trace refresh

The eight agents in qwen’s published atlas were checked against their official package sources at `2026-10-15T04:35:31.727324+00:00`. Four agents have new stable releases; no intervening stable release was found in those ranges.

| Agent | Latest stable | Published (UTC) | Previous atlas |
| --- | --- | --- | --- |
| claude-code | `2.1.300` | 2026-10-14T14:27:26.554Z | `2.1.299` |
| codex | `0.162.1` | 2026-10-09T19:50:30.512Z | `0.162.1` |
| kimi-code | `2.2.0` | 2026-10-14T15:35:26.375Z | `2.1.1` |
| qwen-code | `0.25.1` | 2026-10-11T10:29:32.140Z | `0.25.1` |
| qoder | `1.1.71` | 2026-10-14T11:14:22.744Z | `1.1.70` |
| openclaw | `2026.10.3` | 2026-10-13T13:43:36.600Z | `2026.10.3` |
| hermes | `v0.22.1` | 2026-10-14T23:59:24Z | `v0.22.0` |
| opencode | `1.19.0` | 2026-10-11T01:38:53.143Z | `1.19.0` |

Version discovery uses the registered official npm sources and [Hermes releases](https://github.com/NousResearch/hermes-agent/releases). The prior scheduled capture succeeded without changing the committed archive. All existing 30 lanes and support/display boundaries are preserved.

## Current model evidence

The [OpenAI API model catalog](https://developers.openai.com/api/docs/models) and [Codex availability](https://learn.chatgpt.com/docs/models) were fetched again on October 15. The current coding-model lineup is already covered. GPT-5.5 retired from Codex with ChatGPT sign-in on October 14 at 19:00 UTC; API-key sessions and the API are unaffected. Its historical capture-only compatibility lane stays archived. Ultra and Ultrafast remain modes, not separate model lanes.

The [Claude model overview](https://platform.claude.com/docs/en/models/overview), [release notes](https://platform.claude.com/docs/en/release-notes/overview) and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were read again. Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5 remain the current general lineup. The October 14 release notes add OpenTelemetry export and AWS scheduled deployments, with no new model launch; the latest listed model launch is Haiku 5.5 on October 7. Existing Mythos lanes retain their verification-required boundary.

Both exact platform archives were freshly downloaded and passed npm SHA-512 integrity checks:

| Released implementation | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.300` | 249556880 | `475517caa611e3318a2f298fa071287ec98955538df78c58f709ecc01e5ae2d3` |
| `@openai/codex@0.162.1-linux-x64` | 294415048 | `628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1` |

Claude Code’s binary is 1,625,184 bytes larger than 2.1.299, built `2026-10-14T12:54:55Z` from `c6733ae1276880507ca0c5b053ae18fb0df090d6`. Its [scoped baseline](../../.claude/design/anchors-2.1.300-trace-models.txt) compares all 22 model catalog objects. There are no added/removed IDs; only the effort-cost indices for Haiku 5.5, Sonnet 5.5 and Opus 5.5 change. All other catalog fields are identical. These are static metadata facts, not live price or performance measurements. No old-source mapping or local CLI request was needed.

Codex’s [release-tag catalog](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/models-manager/models.json) was freshly fetched and matches embedded binary bytes at offset `243481218`; SHA-256 is `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`. All eight `visibility=list` model IDs already have lanes. Daybreak and auto-review remain hidden. No new supported model or boundary adjustment was found.

## Linux capture and archive validation

[Capture run 38682500657](https://github.com/DragonnZhang/phistory/actions/runs/38682500657) completed successfully. Formatting, lint, all 160 tests, build, renderers, latest capture, Qoder credential capture/check and full fresh-cache smoke passed. The workflow committed [6cad27c084dac2e2bde99d06a61332cbc4af9381](https://github.com/DragonnZhang/phistory/commit/6cad27c084dac2e2bde99d06a61332cbc4af9381).

All 17 new in-scope records were checked against the committed bytes: fourteen Claude Code variants at 2.1.300, Kimi Code 2.2.0, Qoder 1.1.71 and Hermes v0.22.1. Every record has nonempty prompt/trace/meta, a valid main request, expected requested/observed model, successful CLI exit and actual Linux GitHub Actions provenance for this run. No existing capture file was rewritten. The shared workflow also archived Oh My Pi 18.11.0 outside the atlas scope.

The downloaded smoke artifact contains 29 in-scope records: Claude Code 14, Codex 10, Kimi Code, Qwen Code, OpenClaw, Hermes and OpenCode one each. Each passed the same request and host checks. New Claude Code, Kimi Code and Hermes normalized system text and ordered tool names match their independent smoke captures. Qoder uses the separate credential-backed capture/check step. Nine existing Codex smoke tool-count differences match the previously audited manifest-versus-expanded-tool counts; no new unexplained mismatch was found.

The recent Claude Code wrappers, including 2.1.300, still have no source extractable by the existing static prompt extractor. The workflow explicitly reports these skips. Request prompts and tools are archived; the package static-string library is not claimed to be complete.

## Changes visible in the atlas

Ordered tool-name lists are unchanged for all 17 added records. Normalized system text is byte-identical across 2.1.299 → 2.1.300 for all fourteen Claude Code lanes, and across Qoder 1.1.70 → 1.1.71. This conclusion compares actual text, not just token totals.

| Agent | System tokens, previous → current | Full-text token diff | Confirmed text change |
| --- | --- | --- | --- |
| Kimi Code | 1,695 → 1,714 | +33 / −14 | Replaces the warning that interim text might be invisible with guidance to keep progress notes to one or two sentences, explain nontrivial steps/findings, and avoid narrating every tool call. |
| Hermes | 2,674 → 2,656 | +0 / −18 | Removes the `teams-meeting-pipeline` entry from the injected skills catalog. |

Raw traces retain collection paths and runtime values. Metrics use the existing `phistory-runtime-context-v1` normalization and `cl100k_base` tokenizer; no environment noise was classified as a rule change.

## Fixed archive and deployment

[Pages run 38683239427](https://github.com/DragonnZhang/phistory/actions/runs/38683239427) succeeded. The online `captures/index.json` was fetched and compared byte for byte with the clean committed archive. SHA-256: `958207400c1c4575bdc62560b5e603e12219531ea5846da79ae4c441988dcc9e`.

The downstream atlas can now add 17 records to its previous 4,522 while retaining all 30 lanes and all support/display boundaries. Its methodology records the final source commit containing this research, generation results and browser validation.

## 12:35 UTC recheck — Hermes v0.22.2

All eight official package sources were checked again at `2026-10-15T12:35:24.693491+00:00`. [Hermes v0.22.2](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.2), published at `2026-10-15T08:21:32Z`, was the only newer stable release. The intervening scheduled [capture run 38700431635](https://github.com/DragonnZhang/phistory/actions/runs/38700431635) had already succeeded without archive changes before that release.

The official OpenAI API catalog, Codex availability, Claude model overview, release notes and model configuration linked above were read again. No additional supported model or boundary change was found. Exact npm metadata still resolves Claude Code to 2.1.300 and Codex to 0.162.1, so the morning exact-binary evidence remains applicable. The freshly fetched Codex release catalog retains SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b` and the same eight visible model IDs. All 30 atlas lanes remain in scope.

[Capture run 38736169856](https://github.com/DragonnZhang/phistory/actions/runs/38736169856) succeeded, including formatting, lint, 160 tests, build, latest captures, Qoder checks, rendering and full fresh-cache smoke. It committed [6f6237da6173e30a12cdcdc3daab12c15385af51](https://github.com/DragonnZhang/phistory/commit/6f6237da6173e30a12cdcdc3daab12c15385af51), adding only the Hermes v0.22.2 capture and regenerated indexes. No existing capture file changed. Recent Claude Code wrappers retain the explicitly reported static-extraction limitation described above.

The new nonempty prompt/trace/meta files match committed bytes. The main request, observed `phistory-dummy` model, 20 tools, successful CLI exit and actual Linux GitHub Actions host/run URL were verified. All 29 in-scope smoke records have valid requests and the correct Linux provenance; their ordered tool-name lists and normalized system text each match the corresponding committed archive. The nine previously explained Codex count-level differences remain unchanged.

Hermes v0.22.1 → v0.22.2 restores exactly the `teams-meeting-pipeline` entry removed in the previous release. System control rises from 2,656 to 2,674 tokens, with +18 / −0 full-text tokens. The 20 ordered tool names are unchanged. Fresh-cache smoke independently reproduces the restored catalog entry; raw traces retain their actual runtime values.

[Pages run 38737161039](https://github.com/DragonnZhang/phistory/actions/runs/38737161039) succeeded. The online manifest matches the committed bytes, SHA-256 `5cdcab314171ee5a21793398609cdae53060a6cdd10eac50b90c92fc2fdcbb37`. The downstream refresh adds one record to the morning 4,539 while preserving every prior record, lane and display boundary.
