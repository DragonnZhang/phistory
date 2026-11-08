# November 8 trace refresh

The eight atlas agents were checked against their official release sources at `2026-11-08T04:34Z`. Qwen Code `0.27.0`, published at `2026-11-07T20:55:40.797Z`, is the only new stable release since the November 7 atlas. The target matrix contains all 31 latest agent/variant pairs; one new capture is required.

| Agent | Previous atlas | Latest stable |
| --- | --- | --- |
| claude-code | `2.1.319` | `2.1.319` |
| codex | `0.167.0` | `0.167.0` |
| kimi-code | `2.5.2` | `2.5.2` |
| qwen-code | `0.26.9` | `0.27.0` |
| qoder | `1.1.85` | `1.1.85` |
| openclaw | `2026.11.1` | `2026.11.1` |
| hermes | `v0.23.0` | `v0.23.0` |
| opencode | `1.19.5` | `1.19.5` |

Sources are the packages registered in Phistory: `@anthropic-ai/claude-code`, `@openai/codex`, `@moonshot-ai/kimi-code`, [`@qwen-code/qwen-code`](https://registry.npmjs.org/@qwen-code/qwen-code/0.27.0), `@qoder-ai/qodercli`, `openclaw`, and `opencode-ai` on the official npm registry, plus the [Hermes release feed](https://github.com/NousResearch/hermes-agent/releases). There are no intervening stable releases missing from the atlas.

## Model verification

The [OpenAI model catalog](https://developers.openai.com/api/docs/models), [Codex model guidance](https://developers.openai.com/codex/models), [Claude model overview](https://platform.claude.com/docs/en/models/overview), and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were read again. No new supported model lane or changed support boundary was found.

Codex still resolves to `0.167.0`. Its freshly fetched [release catalog](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/models-manager/models.json) retains SHA-256 `1384625c0b6b3e67d3c60da29becea2b017f2c283fc1e2d05b8edafb4698eb10`; all nine visible models already have fixed lanes, and the hidden Daybreak and auto-review entries are unchanged. Fresh platform-package SHA-512 metadata matches the exact package verified on November 7, so that day's executable evidence is reused: SHA-256 `cdcb45aaa01d0f14e298985e09465aebccd47f64ecd298aea44814af7b62bbda`. The Codex binary was not re-extracted today.

Claude Code still resolves to `2.1.319`. The cached exact Darwin ARM64 package was checked against fresh npm SHA-512 metadata, its binary was rehashed, and all 22 catalog identity anchors were checked at their recorded byte offsets. The executable remains `321995200` bytes with SHA-256 `1f53587e92dee8e089fa349188a4cda64fa34ace6e7b0ca2ee278ddaa3cc2ebd`. The unchanged [binary baseline](../../.claude/design/anchors-2.1.319-trace-models.txt) is retained. This verifies model membership; no local prompt-bearing probe or old-source mapping was used.

## Linux capture and acceptance

[Latest capture and full smoke](https://github.com/DragonnZhang/phistory/actions/runs/42447439625) was dispatched on `main` at `2026-11-08T04:35:33Z`, from `28e5c38104e2dbdf0b5e64323fac5e81b66ab4b4`. The exact run completed successfully. Ruff formatting/lint, 196 tests, package build, latest capture, the separate Qoder step and credential scan, rendering, and fresh-cache smoke all passed. Static extraction retained the existing explicit skips for binary-only Claude packages; request captures remain the atlas's prompt evidence.

The workflow committed Qwen Code `0.27.0` at [`8f16ec74fb26`](https://github.com/DragonnZhang/phistory/commit/8f16ec74fb2690ab83710dc99b80a8326f53c544). Its shared capture scope also added Oh My Pi `18.18.1`, which remains outside the atlas. No existing capture was replaced.

All 31 target records pass manifest status, nonempty committed artifact, main-request selection, model identity, and actual Linux GitHub Actions provenance checks. All 14,697 prompt/trace/meta blobs belonging to the previous 4,899 atlas records are unchanged. The known Codex namespace-root versus expanded-tool counts remain explained by the nested declarations.

The independent smoke contains 30 atlas records, excluding Qoder. Every record matches its archive in complete tool definitions and normalized system/developer text. Qoder `1.1.85` is unchanged and retains its successful [November 7 targeted smoke](https://github.com/DragonnZhang/phistory/actions/runs/42377988862); today's separate Qoder step reused that archive. Its documented capture-only Auto-availability bootstrap is unchanged. All model responses come from claude-tap, with no BYOK or live inference added.

Qwen Code `0.27.0` and `0.26.9` have identical complete tool definitions and normalized system text: 10 tools and 4,675 system tokens under the atlas's existing tokenizer. The normalized text has 22,601 characters and SHA-256 `67ea237ae5f75cce27077565564a89df555306d30851577c123fa37b173b8beb`. The new version is a real additional sample with no tool or system-content change.

The capture manifest has SHA-256 `c9e29bcf9c01124a6030ec6b4866436e7bc7bb8dddd3944aadb10d55118a9bd8`. The downstream atlas pins the published source commit containing this record and the same manifest. The expected atlas total is 1,213 default versions and 4,900 records across the existing 31 lanes.
