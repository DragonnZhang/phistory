# October 23 trace refresh

The eight atlas agents were checked against their registered official sources at `2026-10-23T04:36:02.779445+00:00`. Seven have new stable releases. All intervening stable versions were checked; the targeted gaps are Claude Code 2.1.307 and Hermes v0.22.3. Existing model lanes and display boundaries remain unchanged.

| Agent | Latest stable | Previous atlas | Stable versions added |
| --- | --- | --- | --- |
| claude-code | `2.1.309` | `2.1.306` | `2.1.307`, `2.1.308`, `2.1.309` |
| codex | `0.163.0` | `0.163.0` | — |
| kimi-code | `2.3.0` | `2.2.0` | `2.3.0` |
| qwen-code | `0.26.0` | `0.25.3` | `0.26.0` |
| qoder | `1.1.77` | `1.1.75` | `1.1.76`, `1.1.77` |
| openclaw | `2026.10.6` | `2026.10.5` | `2026.10.6` |
| hermes | `v0.22.4` | `v0.22.2` | `v0.22.3`, `v0.22.4` |
| opencode | `1.19.4` | `1.19.3` | `1.19.4` |

Discovery uses official npm metadata and [Hermes GitHub releases](https://github.com/NousResearch/hermes-agent/releases). The [October 21 scheduled run](https://github.com/DragonnZhang/phistory/actions/runs/39565576343) archived Kimi Code 2.3.0. The [October 22 scheduled run](https://github.com/DragonnZhang/phistory/actions/runs/39743677500) archived all fourteen Claude Code 2.1.308 lanes, Qwen Code 0.26.0, Qoder 1.1.76 and Hermes v0.22.4. These 18 committed records are reused.

## Model discovery and released implementation

The [OpenAI API model catalog](https://developers.openai.com/api/docs/models), [Codex model availability](https://learn.chatgpt.com/docs/models), [Claude model overview](https://platform.claude.com/docs/en/models/overview), [Claude release notes](https://platform.claude.com/docs/en/release-notes/overview), and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were fetched and read on October 23. The Claude general lineup remains Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5. The October 21–22 platform notes concern the API version, SDKs and Google Cloud integration, not a new model. Both Mythos lanes retain their verification-required status. Codex general coding models are already represented; ChatGPT sign-in retirement remains separate from API compatibility and archived history. No new supported model lane was found.

Both exact platform archives passed checks against freshly fetched npm SHA-512 metadata. The existing Codex archive was reused only after re-verification:

| Released implementation | Binary bytes | SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.309` | 275923296 | `5eadc0009cdcdbc0d06e58110d7b2baf88ced6088b1c33a60a21217fe410619c` |
| `@openai/codex@0.163.0-linux-x64` | 296865480 | `399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215` |

Claude Code 2.1.309 was built `2026-10-22T16:51:07Z` from `1ce7e4ff2da8d4b0f5c15b291e4a9b54716970c2`. It is 2,939,120 bytes larger and built 1 day, 15:52:16 after 2.1.306. The [scoped anchor baseline](../../.claude/design/anchors-2.1.309-trace-models.txt) confirms the same 22 catalog model IDs; this does not claim all catalog fields are identical. The documented support boundaries remain unchanged. Fable 5.1 retains its published display start at 2.1.258, despite documented configuration compatibility from 2.1.257. No local prompt-bearing probe was run, and the older source dictionary was not needed.

The [Codex 0.163.0 release](https://github.com/openai/codex/releases/tag/rust-v0.163.0) remains stable. Its freshly fetched [release catalog](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/models-manager/models.json) remains SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`, byte-identical to the prior audit, and occurs at binary offset `245669092`. All eight visible model IDs have existing lanes. Daybreak and auto-review remain hidden.

## CI repair and capture acceptance

The [initial latest workflow](https://github.com/DragonnZhang/phistory/actions/runs/39901615646) passed formatting, lint and 162 tests and built both distributions, but OpenClaw 2026.10.6 failed in both archive and smoke installation. Its published engine requirement is `>=24.22.1 <25 || >=26.12.1`; setup-node reused the runner's cached 24.21.0. [Node's release index](https://nodejs.org/dist/index.json) and the matching npm runtime package confirm that 24.22.1 was already available.

[The repair](https://github.com/DragonnZhang/phistory/commit/f102587074fa975ca6cd541e98c5670e35de08c6) enables `check-latest` for the Node 24 setup steps in latest, backfill and historical recapture workflows. This preserves the selected major version while resolving current patches instead of an older runner cache. The [targeted OpenClaw retry](https://github.com/DragonnZhang/phistory/actions/runs/39902883820) resolved Node 24.22.1 and successfully archived and independently smoke-captured 2026.10.6. No engine check was bypassed. The original failed workflow remains in history; its failed target is covered by this successful retry.

The [Claude Code 2.1.307 backfill](https://github.com/DragonnZhang/phistory/actions/runs/39901708685) passed 162 tests and captured all fourteen variants twice, including fresh-cache smoke. The [Hermes v0.22.3 backfill](https://github.com/DragonnZhang/phistory/actions/runs/39903006290) and repaired OpenClaw workflow each passed 164 tests and their targeted smoke checks. No other capture target failed.

Latest added 16 atlas records: Claude Code 2.1.309 ×14, Qoder 1.1.77 and OpenCode 1.19.4. Claude backfill added 14, OpenClaw added one and Hermes added one. Together with the 18 scheduled records, the atlas gains 50 captures and eleven default points, reaching 4,695 records across 30 lanes and 1,164 default points.

All 50 additions have nonempty prompt/trace/meta files, valid primary requests, matching requested/observed models, exit status zero and actual Linux GitHub Actions run identity. File bytes match committed Git blobs. All 44 successful atlas-scope fresh-cache smoke records match the committed complete wire tool declarations and normalized system/developer text: 28 latest, 14 Claude backfill, one Hermes and one repaired OpenClaw. The failed initial OpenClaw attempt is not counted as a capture or smoke success. Qoder's separate authenticated archive and secret scan passed. Codex namespace declaration counts retain the previously explained difference from flattened tool counts; no new unexplained mismatch was found.

## Observed changes

- Claude Code 2.1.307 revises the `Agent` tool description in eleven lanes, explaining delegation costs, when to keep work local, independent review and explicit task briefs. The three other lanes retain their prior complete declarations. 2.1.308 has no further schema change. 2.1.309 updates all fourteen `Bash` descriptions to explain GitHub mention notifications and quoting placeholder handles. All forty-two added Claude records retain their existing ordered tool names and normalized system text. Tool descriptions remain part of wire evidence and are excluded from system-token statistics.
- Kimi Code 2.3.0 adds a TodoList progress rule: mark completed work promptly and reconcile the list before the final reply. System control grows from 1,714 to 1,768 tokens, +54 / −0. The 25 tool names stay unchanged. Full definitions update the TodoList and Skill descriptions; the Agent description no longer lists WebSearch for plan/coder/explore subagents. This reports the captured description, not a claim that every search capability was removed.
- Qwen Code 0.26.0 keeps ten tools and 6,952 system tokens. `edit` and `notebook_edit` gain `record_as_artifact`; `write_file` explains additional artifact types and that suppressing creation still refreshes an existing artifact for the path. The notebook description is shortened. These definition changes do not alter system metrics.
- Hermes v0.22.3 removes the `teams-meeting-pipeline` skill listing: system control falls from 2,673 to 2,655 normalized tokens, +0 / −18. v0.22.4 matches v0.22.3. All twenty complete tool definitions remain unchanged.
- Qoder 1.1.76 and 1.1.77 retain 31 tools and 2,814 system tokens; OpenClaw 2026.10.6 retains twelve and 11,330; OpenCode 1.19.4 retains ten and 2,539. Their complete declarations and normalized text match the preceding versions.

The static extraction step ran with explicit skips for wrapper packages lacking extractable source, including Claude Code 2.1.308 and 2.1.309. Backfill skips static extraction. This existing limitation remains: latest wire prompts/tools and the scoped binary model catalog are verified, but a complete latest static prompt library is not claimed.

## Python toolchain normalization

Hermes's Python patch changed between collection environments: `python3=3.14.8` became `python3=3.14.9`. The released [environment probe](https://github.com/NousResearch/hermes-agent/blob/v0.22.4/tools/env_probe.py) confirms that these values come from the local Python executables and `pip --version`. They are collection facts, not changed model instructions.

Phistory display and atlas extraction now normalize version numbers only inside labelled `Python toolchain:` lines, covering python3, the optional python alias and pip's bound interpreter. They preserve executable labels, missing/present fields, pip mismatch, PEP 668 and tool availability diagnostics, as well as ordinary Python version requirements in prose. Serialized and rendered containers share the same treatment. The raw traces, capture hosts and readable archive Markdown remain unchanged.

A before/after reconstruction of all 4,695 records finds 27 affected Hermes prompts, including 25 existing records. No complete event disappears; nineteen retained events are recomputed because their lengths, sections or token deltas change. The Python-version hunk is removed from the v0.22.3 change while its actual eighteen-token skill deletion remains. The downstream `prompt-python-toolchain-audit.json` records every affected version and recomputed event; earlier normalization audits remain preserved.

Both render commands were run. All 164 local Phistory tests, formatting, lint and package build passed before publishing the repair; both subsequent targeted Linux workflows also passed 164 tests. Regression tests verify idempotence, display-layer consistency, preservation of diagnostics and real rules, the actual Hermes skill deletion, and raw-evidence immutability.

## Published capture source

Capture source: [`082a2249bbc1557661c9206c7f6b5068bfabdbe4`](https://github.com/DragonnZhang/phistory/commit/082a2249bbc1557661c9206c7f6b5068bfabdbe4), following repaired OpenClaw commit `a06050a50a047fe32eb9c9eac20876fe28e27d93`. The [capture Pages deployment](https://github.com/DragonnZhang/phistory/actions/runs/39903258727) passed. The live manifest was fetched and matched the committed bytes:

`a716ec03e9dfef2e3bc9cf477a9a7e5bcf20dec3b261a811048b12de4d62b74c`

Only `captures/index.json` changes among previously tracked capture files since the October 21 atlas source; all other changes under `captures/` are additions. No raw trace or host metadata is rewritten. The subsequent evidence commit retains this manifest; qwen methodology pins that final source commit and its Pages deployment.
