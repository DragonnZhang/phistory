# October 10 trace refresh and Mythos coverage

Verified on 2026-10-10 (Asia/Singapore). Versions were queried from each registered package source at 04:34 UTC. The official [Claude catalog](https://platform.claude.com/docs/en/models/overview), [release notes](https://platform.claude.com/docs/en/release-notes/overview), [OpenAI catalog](https://developers.openai.com/api/docs/models), and [Codex availability](https://learn.chatgpt.com/docs/models) were read again.

## Version targets

| Agent | Latest stable | Published (UTC) |
| --- | --- | --- |
| claude-code | `2.1.296` | 2026-10-09T16:58:10.924Z |
| codex | `0.162.1` | 2026-10-09T19:50:30.512Z |
| kimi-code | `2.1.1` | 2026-09-24T07:27:15.480Z |
| qwen-code | `0.25.0` | 2026-10-05T09:55:55.980Z |
| qoder | `1.1.67` | 2026-10-09T13:27:27.027Z |
| openclaw | `2026.9.9` | 2026-10-08T09:38:01.687Z |
| hermes | `v0.21.6` | 2026-10-08T11:51:57Z |
| opencode | `1.18.35` | 2026-10-06T20:21:30.784Z |

Claude Code, Codex and Qoder each have one new stable release since the prior atlas. There are no intervening stable versions to backfill. Preserve the other five agents' archives and all existing model lanes. Latest Linux run [38024660398](https://github.com/DragonnZhang/phistory/actions/runs/38024660398) targets 23 new in-scope archives: twelve Claude lanes, ten Codex lanes and Qoder.

## Model discovery

The general Claude lineup remains Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5. The catalog's separate Specialized models section also lists [Mythos 5.1](https://platform.claude.com/docs/en/models/mythos-5-1/overview) and [Mythos 5](https://platform.claude.com/docs/en/models/mythos-5/overview). These were released September 1 and June 9 respectively; they are newly covered by this atlas, not newly launched today. Both require organization verification. This limits provider access, but does not prevent archiving the CLI's model-specific request through the existing capture-only transport.

Add `official-mythos-5-1` for exact ID `claude-mythos-5-1`; add `official-mythos` for `claude-mythos-5` to provide a same-CLI predecessor comparison. Both use forward capture and carry `availability=verification-required` in their dimensions and an explicit label. Set both support/display floors to `2.1.296`, the earliest release verified in this investigation, not the first release that ever supported them. Do not backfill months of unverified history. The existing generic model-family discovery and same-family predecessor selection can handle Mythos without hardcoded atlas data.

GPT-6.1 Sol, GPT-6 Astra, GPT-6 Sol/Luna, GPT-5.6 Sol/Terra/Luna and GPT-5.5 remain the eight listed Codex models. Hidden Daybreak and auto-review entries are not ordinary model-picker options. Ultrafast and Ultra are modes, not new model IDs. Official docs announce GPT-5.5 retirement from ChatGPT/Work/Codex on October 14; retain its current lane on October 10 and preserve its historical archive after retirement. No new Codex lane is needed.

## Exact released implementations

Both platform tarballs pass their npm SHA-512 integrity values after completing interrupted downloads. No CLI or model request was executed locally.

| Package | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.296` | 240664432 | `c9b5341637becbd423ddffc5b254afb645682a3868cb708bbc6cc0e7bb419937` |
| `@openai/codex@0.162.1-linux-x64` | 294415048 | `628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1` |

Claude's build time is `2026-10-09T01:31:52Z`, Git SHA `fdb6c17a97b6e0e3cf15e3566663fca58e7c43a9`. Compared with the previously verified 2.1.295 binary, it is 968,544 bytes larger. Explicit catalog objects confirm the Mythos IDs and dedicated model capabilities; the scoped [anchor baseline](../../.claude/design/anchors-2.1.296-trace-models.txt) records offsets. Mythos 5.1 includes adaptive thinking, lean prompt, Fable 5.1 prompt bundle and high default effort. Merely accepting an arbitrary model string would not establish this. The old Claude source snapshot was not used as current-behavior evidence.

The [Codex 0.162.1 tagged model catalog](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/models-manager/models.json) has SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`, unchanged from 0.162.0. Its exact bytes occur in the matching binary at offset 243481218. All existing CLI support floors remain unchanged.

## Linux validation

Registry commit `82b2ad73` passed formatting, lint, 148 tests, package build and both renderers. Latest run [38024660398](https://github.com/DragonnZhang/phistory/actions/runs/38024660398) committed all 23 requested endpoint captures in `736fe869`; Qoder's credentialed capture and archive check passed. Its overall result is failure only because the out-of-atlas MiniMax Code 3.1.1 fresh-cache smoke rejected the upstream archive's SHA-512 checksum. All 27 in-scope smoke captures are valid. Do not retry already successful targets or report the full workflow as successful.

Targeted backfill and fresh-cache smoke [38025197853](https://github.com/DragonnZhang/phistory/actions/runs/38025197853) succeeded for both Mythos variants at 2.1.296, committing them in `e7357299`. Both archives and both smoke requests use the exact configured model ID and contain twelve tools; no account access or live inference was tested. Together, all 25 new in-scope records have nonempty prompt/trace/meta files, valid main requests and genuine Linux GitHub Actions provenance. Their files match committed artifacts byte-for-byte; no prior raw trace was rewritten.

Claude's latest wrapper still contains no source supported by the static extractor, so the workflow explicitly skips static extraction; request prompts and tools are captured normally. The current model baseline is therefore based on verified platform binaries and Linux request traces, not a claim of a complete static string archive.

## MiniMax download repair

The subsequent CI investigation reproduced a downloader bug: an HTTP response advertising ten bytes but closing after four was accepted as a completed archive. The old resume path also accepted HTTP 416 without proving completeness. The failed smoke did not retain its downloaded ZIP, so its exact damaged bytes cannot be reconstructed. A complete read-only download of the official MiniMax Code 3.1.1 arm64 ZIP contains 340,227,006 bytes and passes the original updater manifest's SHA-512 (`MD9au63mpfvxXY5sr9DkBypRsy9lGD0GF4acuGdaN2DMEcdE2gWQ0sCeIPk7Yt9tVLKivBj9mXcdYyAJa5LT/g==`); the current upstream asset is consistent with its manifest.

Repair commit [`67582af4`](https://github.com/DragonnZhang/phistory/commit/67582af4e9e51b9d1c2805d8fb0e5b249ddb5ebc) checks response lengths, manifest size and SHA-512 before publishing the ZIP to the installer. It validates resumed ranges, sends `If-Range` when a response validator is available, restarts when the server ignores/rejects a range, and retries integrity failures from scratch within the existing three-attempt limit. Persistent corruption still fails. Twelve new HTTP regression cases cover truncated bodies, ignored/invalid ranges, HTTP 416, checksum recovery and persistent failure; all 160 tests, lint, formatting, package build and both renderers passed. MiniMax is now selectable in the existing targeted Linux backfill/smoke workflow.

Targeted Linux run [38030298995](https://github.com/DragonnZhang/phistory/actions/runs/38030298995) succeeded on that commit. It preserved the complete archived 3.1.1 capture, then installed the official package in a fresh smoke cache and captured a valid request with 21 tools and model `minimax-code-capture`. The downloaded smoke artifact has nonempty prompt, system/messages and raw trace, exit code zero, and genuine Linux GitHub Actions provenance for that exact run. No capture files changed and no live provider inference was performed. The earlier global run remains historically failed; the affected target is now verified successfully without repeating unrelated captures.
