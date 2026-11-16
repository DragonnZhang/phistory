# November 16 trace refresh

The eight existing downstream atlas agents were checked against official release sources on November 16, 2026. The qwen atlas baseline is `ba0af65f9aa481493988c5119ba94f50f04d67a0`, with 4,900 captures and 31 lanes. Scheduled Linux captures have continued since that publication; their valid committed artifacts are reused.

| Agent | Previous atlas | Latest stable target |
| --- | --- | --- |
| claude-code | `2.1.319` | `2.1.325` |
| codex | `0.167.0` | `0.169.0` |
| kimi-code | `2.5.2` | `2.6.2` |
| qwen-code | `0.27.0` | `0.27.4` |
| qoder | `1.1.85` | `1.1.89` |
| openclaw | `2026.11.1` | `2026.11.3` |
| hermes | `v0.23.0` | `v0.23.1` |
| opencode | `1.19.5` | `1.19.5` |

Sources are each agent's registered official npm package and the [Hermes release feed](https://github.com/NousResearch/hermes-agent/releases). OpenClaw's `2026.9.37` belongs to its `extended-stable` dist-tag; `latest` remains `2026.11.3`. This refresh follows the existing latest stable release line, retaining already archived history. Preview releases are not new stable targets.

## New Claude model lanes

The [Claude release notes](https://platform.claude.com/docs/en/release-notes/overview) announce `claude-fable-5-2` and `claude-mythos-5-2` on November 9. The [model overview](https://platform.claude.com/docs/en/models/fable-5-2/overview) describes Mythos access as restricted to verified organizations. The [Claude Code model configuration](https://code.claude.com/docs/en/model-config) requires CLI `2.1.321` or later for Fable 5.2.

The exact latest wrapper and its matching Darwin ARM64 platform package were resolved through fresh npm metadata. Both the latest `2.1.325` and boundary `2.1.321` tarballs passed SHA-512 integrity verification. Their binaries contain dedicated catalog objects for both new IDs and the `fable_5_2_prompt_bundle` capability. The [binary baseline](../../.claude/design/anchors-2.1.325-trace-models.txt) records hashes, sizes and byte anchors. `2.1.319` has 22 catalog IDs; `2.1.325` has the same 22 plus these two. No `2.1.320` stable package was published. The new variants therefore start at `2.1.321`, with Mythos labelled as the earliest verified release and `availability=verification-required`. No provider entitlement is inferred from capture-only compatibility.

The new lanes keep transparent forward capture, the normal full prompt surface and the existing deterministic environment. Existing default, non-official and fixed-model variants remain intact. Their immediate predecessor comparisons use Fable 5.1 and Mythos 5.1 at the same CLI version. Missing existing stable releases `2.1.321` and `2.1.324` are included in the Claude backfill; Codex `0.168.0` and Kimi Code `2.6.0` are also missing from scheduled archives and require targeted backfill.

## Codex model verification

The [OpenAI model catalog](https://developers.openai.com/api/docs/models) and [Codex guidance](https://developers.openai.com/codex/models) were read. The exact [0.169.0 release catalog](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/models-manager/models.json) contains nine visible models, all already registered as fixed lanes. Daybreak Blue, Daybreak Red and the auto-review entry remain hidden. There is no new visible model or support boundary to add.

The exact npm Darwin ARM64 binary passed SHA-512 tarball verification and contains all twelve release-catalog IDs. Its SHA-256 is `739cda86e15d30a6e2b40cf8dcbbfb988aaf48b305b27065573bb8e9fd41d002`, with size `280704736` bytes. No local model request was issued.

## Linux capture and publication

All five manually dispatched Linux workflows completed successfully. Each passed Ruff and 197 tests; the latest workflow also passed the package build. All model responses came from claude-tap, without live inference.

| Run | Scope | Accepted independent smoke records in atlas scope |
| --- | --- | ---: |
| [43796327654](https://github.com/DragonnZhang/phistory/actions/runs/43796327654) | Latest captures and fresh-cache smoke | 32 |
| [43796672502](https://github.com/DragonnZhang/phistory/actions/runs/43796672502) | Claude Code, all sixteen variants, `2.1.321`–`2.1.325` | 80 |
| [43797117127](https://github.com/DragonnZhang/phistory/actions/runs/43797117127) | Codex, all eleven variants, `0.168.0` | 11 |
| [43797476060](https://github.com/DragonnZhang/phistory/actions/runs/43797476060) | Kimi Code `2.6.0` | 1 |
| [43797667975](https://github.com/DragonnZhang/phistory/actions/runs/43797667975) | Qoder `1.1.86`–`1.1.89`, including credential scan | 4 |

All 128 smoke records match their committed archives in complete tool definitions and normalized system/developer text. Codex now also carries tool declarations in `input[].type=additional_tools`; comparison includes those declarations and excludes only the enclosing message's transient ID. The documented namespace-root versus expanded-tool count difference remains visible. Qoder retains its capture-only Auto-availability bootstrap and does not verify provider entitlement.

The refresh accepts 139 additional atlas records: 89 reused scheduled captures, two latest new-model captures, 36 Claude historical captures, eleven Codex captures and one Kimi capture. All are `captured`, have nonempty prompt/trace/meta artifacts and a valid main request, and retain actual Linux GitHub Actions provenance. Requested model IDs agree with the emitted model after the existing `[1m]` normalization. All 14,700 prompt/trace/meta blobs belonging to the previous 4,900 atlas captures are unchanged.

No stable release in the selected latest line remains missing after the previous atlas endpoints. The complete atlas now has 1,237 default versions and 5,039 records across 33 lanes. Both new model lanes cover all five stable releases from `2.1.321` through `2.1.325`; their predecessor captures at `2.1.321` support same-CLI comparisons. Existing display boundaries remain intact. OpenCode is unchanged; OpenClaw's extended-stable branch and graph-external agents are not new target lines.

The committed manifest has SHA-256 `66266528f03723b329b9e14073e47a47260c329ae24a9f64170d613ea1995544`. The downstream atlas pins the published source commit containing this research record and this same manifest. The November 15 Pages failure `43701167130` occurred after upload, while fetching artifact metadata. A fresh [deployment](https://github.com/DragonnZhang/phistory/actions/runs/43796322486) succeeded, and the [complete capture source deployment](https://github.com/DragonnZhang/phistory/actions/runs/43797824652) also succeeded. The unchanged binary-only Claude static-extraction skips do not affect request captures.
