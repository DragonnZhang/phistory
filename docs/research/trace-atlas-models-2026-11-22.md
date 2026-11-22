# November 22 trace atlas release and model verification

| Agent | Previous atlas endpoint | Latest stable target |
| --- | --- | --- |
| Claude Code | `2.1.325` | `2.1.330` |
| Codex | `0.169.0` | `0.171.0` |
| Kimi Code | `2.6.2` | `2.7.2` |
| Qwen Code | `0.27.4` | `0.28.3` |
| Qoder | `1.1.89` | `1.1.95` |
| OpenClaw | `2026.11.3` | `2026.11.6` |
| Hermes | `v0.23.1` | `v0.23.3` |
| OpenCode | `1.19.5` | `1.19.6` |

The comparison uses the packages registered in Phistory and the [Hermes release feed](https://github.com/NousResearch/hermes-agent/releases). The npm targets follow the existing `latest` publication line, including for OpenClaw, which also has an `extended-stable` line. Qwen Code has only two new stable releases in this interval, `0.28.0` and `0.28.3`.

## Codex

OpenAI lists `gpt-6.2-astra` and `gpt-6.2-sol` in its [model catalog](https://developers.openai.com/api/docs/models) and [Codex model guidance](https://developers.openai.com/codex/models). The adjacent stable release catalogs and matching npm Darwin ARM64 binaries agree. Each npm tarball passed the registry SHA-512 integrity check before binary extraction.

| Stable release catalog | Platform binary SHA-256 | GPT-6.2 catalog entries |
| --- | --- | --- |
| [`0.169.0`](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/models-manager/models.json) | `739cda86e15d30a6e2b40cf8dcbbfb988aaf48b305b27065573bb8e9fd41d002` | Absent |
| [`0.170.0`](https://github.com/openai/codex/blob/rust-v0.170.0/codex-rs/models-manager/models.json) | `9248cfeebec9b4cc3034d5114caab10fbf426cbfa79dba410c5fa1b72762abd6` | Sol at byte `218606295`, Astra at byte `218672927` |
| [`0.171.0`](https://github.com/openai/codex/blob/rust-v0.171.0/codex-rs/models-manager/models.json) | `fc8d77cf7585aded2b9bc487dad29c4ee2f066123e4e76dd3b17653a8322ce42` | Sol at byte `244132847`, Astra at byte `244199479` |

Both model entries have `visibility=list` and `minimal_client_version=0.166.0`. The stable history starts at `0.170.0`, the first stable catalog containing the entries. The compatibility minimum is a separate field. The two fixed-model capture lanes use this stable boundary and preserve the existing default and non-official lanes. Intended first-version comparisons are GPT-6 Astra → GPT-6.2 Astra and GPT-6.1 Sol → GPT-6.2 Sol, both at CLI `0.170.0`.

## Claude Code

The latest npm wrapper `2.1.330` resolves to `@anthropic-ai/claude-code-darwin-arm64@2.1.330`. The SHA-512-verified tarball contains a 363,855,808-byte binary with SHA-256 `f7c5093b9dd6560d08af28bc27b123fb55091deae3180ac39df199522da86815`. Its 24 family-bearing catalog IDs match the [2.1.325 baseline](../../.claude/design/anchors-2.1.325-trace-models.txt). `claude-fable-5-2` and `claude-mythos-5-2` occur at byte offsets `289609170` and `289611562`. The [Claude Code model documentation](https://code.claude.com/docs/en/model-config) and [Claude Platform release notes](https://platform.claude.com/docs/en/release-notes/overview) support retaining the existing Fable and Mythos lanes and their recorded boundaries. Catalog inspection used the exact binary. Request-level tool and prompt comparisons use the Linux CI captures.
