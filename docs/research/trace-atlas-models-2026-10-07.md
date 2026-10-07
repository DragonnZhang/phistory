# Sonnet 5.5, GPT-6.1 Sol, and GPT-6 Luna capture boundaries

Verified on 2026-10-07. This update adds three fixed-model lanes while preserving real defaults, non-official lanes, and previous model histories. Captures use Linux GitHub Actions and `claude-tap` dummy responses; no live model API is called.

## Model evidence

| Variant | Exact CLI model | First capture version | Evidence |
| --- | --- | --- | --- |
| `official-sonnet-5-5` | `claude-sonnet-5-5` | Claude Code `2.1.284` | Absent in the exact `2.1.283` platform binary; an explicit catalog entry is present in `2.1.284` and latest `2.1.292`. |
| `gpt-6.1-sol` | `gpt-6.1-sol` | Codex `0.159.1` | Absent from release `0.159.0`'s catalog, present in `0.159.1` and later stable releases. |
| `gpt-6-luna` | `gpt-6-luna` | Codex `0.157.0-alpha.10` | Absent from stable `0.156.0`, present in the already-archived `0.157.0-alpha.10` preview and stable `0.157.0`. This explicitly selected preview boundary does not claim an exhaustive alpha scan. |

Model identities were checked against [Claude's current catalog](https://platform.claude.com/docs/en/models/overview), [Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview), [OpenAI's catalog](https://developers.openai.com/api/docs/models), and [Codex model availability](https://learn.chatgpt.com/docs/models). Specialized API-only models and hidden review models are outside the atlas's normal model-selection surface.

Codex release catalogs: [0.156.0](https://github.com/openai/codex/blob/rust-v0.156.0/codex-rs/models-manager/models.json), [0.157.0-alpha.10](https://github.com/openai/codex/blob/rust-v0.157.0-alpha.10/codex-rs/models-manager/models.json), [0.159.0](https://github.com/openai/codex/blob/rust-v0.159.0/codex-rs/models-manager/models.json), [0.159.1](https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/models-manager/models.json), [0.160.1](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/models-manager/models.json).

The `0.160.1` published Darwin ARM64 binary contains the entire release catalog byte-for-byte at offset `185316916`. Its SHA-256 is `fd219bd9f061278275f528939f82f54d2eb97df4b25c23b022adbe48813d920b`, identical to the `0.159.1` catalog. GPT-6.1 Sol's `minimal_client_version=0.153.0` and Luna's `0.155.0` are compatibility gates, not first bundled versions. Accepting an unknown model string is not evidence of a model-specific prompt.

## Exact package verification

All downloaded platform tarballs passed npm SHA-512 integrity checks. Static inspection was local; actual requests are captured only in Linux CI.

| Platform package/version | Binary bytes | Binary SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.283` | 225036032 | `d8cb1e5c79684cc12a8bfc813e3a2073406921b6245744b3009be3ab5651d21e` |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.284` | 226563088 | `50a14c2f50f56668380fdda490167f1d3630d5cc18fb8aed3073c2c7ea7314fe` |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.292` | 235017328 | `97a01e5bc74a199e67189435d0331ea3a24eac2e07db4b76d9148c5b0386138f` |
| `@openai/codex@0.160.1-darwin-arm64` | 241556032 | `09fa44fdc37a5fc70dc1ace31235f90468a2e193d0e85f7552eab068ea2582be` |

Claude latest identifies build time `2026-10-06T05:25:12Z` and Git SHA `37832d0b7cad7b40bac7c82dff58629313913edf`. Its Sonnet 5.5 entry starts at byte `183493900`, compared with `178320681` in `2.1.284`. Both declare a native 1M window and `lean_prompt`. Latest has `mid_conv_tool_change` and `per_turn_timing`; the first entry had `mid_conv_system`. These are confirmed catalog facts, not a union of wire tools. Old readable Claude source is not used to infer model identities.

## Incremental capture scope

Latest stable versions at discovery: Claude Code `2.1.292`, Codex `0.160.1`, Kimi Code `2.1.1`, Qwen Code `0.25.0`, Qoder `1.1.65`, OpenClaw `2026.9.8`, Hermes `v2026.9.24`, OpenCode `1.18.35`. All latest defaults were already archived in Linux CI.

The gap check found Claude Code `2.1.290`, Codex `0.159.1`, and Kimi Code `2.1.0` missing after the atlas's previous endpoints. Backfill workflow choices now cover all eight atlas agents through the existing generic CLI. New models are bounded by the registry minimums above; old history is not rewritten to manufacture availability.

This code-stage commit defines and tests targets. CI results and the committed artifact audit will be recorded after capture completion; registration alone does not mean a lane has been captured.
