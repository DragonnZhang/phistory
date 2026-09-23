# Opus 5.5 and GPT-6 Sol capture boundaries

Verified on 2026-09-23. These lanes use the normal CLI commands through `claude-tap --export-prompt`; they do not call a real model provider. All request captures run in Linux GitHub Actions.

## Claude Code: Opus 5.5

The exact model ID is `claude-opus-5-5`. The latest npm wrapper and matching Darwin ARM64 package are `2.1.280`. The executable's model catalog explicitly names Opus 5.5, maps its first-party provider ID to `claude-opus-5-5`, and declares native 1M context plus support for the `[1m]` selector. The new `official-opus-5-5` lane pins `claude-opus-5-5[1m]`, matching the existing Opus lanes' context selector. The wire model ID omits the suffix.

The executable also lists `lean_prompt` and `opus_5_5_prompt_bundle` capabilities for this model. Those are static implementation evidence; tools and prompt content in the atlas come only from the captured request. Existing Opus 5 and earlier lanes retain their IDs and selectors. The first new capture uses `2.1.280`; this is a verified capture starting point, not a claim that all earlier CLIs lack support.

- [Exact platform package](https://registry.npmjs.org/@anthropic-ai/claude-code-darwin-arm64/-/claude-code-darwin-arm64-2.1.280.tgz)
- Executable size: 217,254,576 bytes; SHA-256: `387a5c5dcdbb815085edf0baf79591f9d8894efe922bceaf3d75b1b08055229d`.
- Embedded build time: `2026-09-21T20:40:17Z`; Git SHA: `80abbfe7d7232280011ff01a21ae3338f4c6e372`.

## Codex: GPT-6 Sol

The [official model ID](https://developers.openai.com/api/docs/models/gpt-6-sol) is `gpt-6-sol`. The latest stable npm release is `0.156.0`, but its [release-tagged catalog](https://github.com/openai/codex/blob/rust-v0.156.0/codex-rs/models-manager/models.json) does not include this model. Its matching Darwin ARM64 executable contains no `gpt-6-sol` string (SHA-256 `6b42db4d33fd53516162bd76a0e2d07e0567287c44e036d4e4c06cb555a432f9`). Capturing that version with an unrecognized model ID would archive generic fallback metadata.

The published `0.157.0-alpha.10` preview [does bundle GPT-6 Sol](https://github.com/openai/codex/blob/rust-v0.157.0-alpha.10/codex-rs/models-manager/models.json), unlike [the preceding alpha.9 catalog](https://github.com/openai/codex/blob/rust-v0.157.0-alpha.9/codex-rs/models-manager/models.json). Phistory therefore starts this lane at `0.157.0-alpha.10`. Its catalog minimum client version, `0.155.0`, is a compatibility floor, not the first release bundling the model.

The catalog uses `code_mode_only`, multi-agent v2, `shell_command`, and experimental async-message/clock tools. Linux captures establish the actual emitted surface. The initial backfill also includes default, non-official, Astra and the older GPT models at the exact same preview version so downstream comparisons have a shared CLI and host baseline. Daily stable captures continue to skip GPT-6 Sol until a supporting stable release ships; that future stable release sorts after its previews and passes the capture floor.

The model was added upstream in [49e95cc73f4e](https://github.com/openai/codex/commit/49e95cc73f4eb2999b1d14f863c009168df6122b); the preview also includes the [subsequent prompt refresh](https://github.com/openai/codex/commit/24462234b2aeeb27373e17bbe226baf9c0e97d3b).

## Reproduce in Linux CI

```bash
gh workflow run capture.yml -R DragonnZhang/phistory --ref main
gh workflow run backfill.yml -R DragonnZhang/phistory --ref main \
  -f agent=codex \
  -f variants=default,non-official,gpt-6-astra,gpt-6-sol,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5 \
  -f from=0.157.0-alpha.10 -f to=0.157.0-alpha.10 -f include_prerelease=true
```

Wait for workflow completion and Pages deployment, then fetch the committed artifacts. Preserve `trace.jsonl` and the actual `meta.json.capture_host`; Darwin binary inspection above is separate from Linux request collection.
