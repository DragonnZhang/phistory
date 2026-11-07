# November 7 trace refresh

The eight agents already present in the atlas were checked against their official release sources at `2026-11-07T13:25Z`. Seven have newer stable releases. The target matrix contains 36 release/variant pairs, including the unchanged latest OpenCode capture. No new Claude Code or Codex model lane is required.

| Agent | Previous atlas | Latest stable | Intervening stable releases |
| --- | --- | --- | --- |
| claude-code | `2.1.318` | `2.1.319` | `2.1.319` |
| codex | `0.166.0` | `0.167.0` | `0.167.0` |
| kimi-code | `2.4.1` | `2.5.2` | `2.5.0`, `2.5.1`, `2.5.2` |
| qwen-code | `0.26.7` | `0.26.9` | `0.26.8`, `0.26.9` |
| qoder | `1.1.82` | `1.1.85` | `1.1.83`, `1.1.84`, `1.1.85` |
| openclaw | `2026.10.10` | `2026.11.1` | `2026.11.1` |
| hermes | `v0.22.7` | `v0.23.0` | `v0.23.0` |
| opencode | `1.19.5` | `1.19.5` | — |

Sources are the packages registered in Phistory: `@anthropic-ai/claude-code`, `@openai/codex`, `@moonshot-ai/kimi-code`, `@qwen-code/qwen-code`, `@qoder-ai/qodercli`, `openclaw`, and `opencode-ai` on the official npm registry, plus the [Hermes release feed](https://github.com/NousResearch/hermes-agent/releases). All stable releases since the previous atlas endpoint are included; the atlas remains eight agents and 31 lanes.

## Model verification

The [official OpenAI model catalog](https://developers.openai.com/api/docs/models) and [Codex model guidance](https://developers.openai.com/codex/models) were read again. The exact `0.167.0` [release catalog](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/models-manager/models.json) is byte-identical to `0.166.0`, with SHA-256 `1384625c0b6b3e67d3c60da29becea2b017f2c283fc1e2d05b8edafb4698eb10`. All nine visible model IDs already have fixed lanes; hidden Daybreak and auto-review entries are unchanged. The `0.167.0-linux-x64` npm package passed freshly fetched SHA-512 integrity metadata, and its exact executable has SHA-256 `cdcb45aaa01d0f14e298985e09465aebccd47f64ecd298aea44814af7b62bbda`. The catalog occurs verbatim at byte offset `264998474`. This release uses the package manifest's `bin/codex` entry point. GPT-6.1 Terra retains its stable catalog boundary at `0.165.0`.

The [Claude model overview](https://platform.claude.com/docs/en/models/overview) and [Claude Code model configuration](https://code.claude.com/docs/en/model-config) were also read. The exact wrapper's `@anthropic-ai/claude-code-darwin-arm64@2.1.319` dependency passed npm SHA-512 verification. Its executable is `321995200` bytes, with SHA-256 `1f53587e92dee8e089fa349188a4cda64fa34ace6e7b0ca2ee278ddaa3cc2ebd`, built `2026-11-05T18:14:18Z` from `fbdd7e02e8530e4c2ade8cc67d30104e53d2c8f2`. The [scoped binary baseline](../../.claude/design/anchors-2.1.319-trace-models.txt) confirms the same 22 catalog identities as `2.1.318`. This is a model-membership check, not an assertion that every catalog field is unchanged. Existing model support and display boundaries remain intact. No old-source mapping or local prompt-bearing probe was needed.

## Linux capture and artifact acceptance

The [November 7 scheduled capture](https://github.com/DragonnZhang/phistory/actions/runs/42358482167) succeeded after the GitHub account lock was resolved and committed 30 new atlas records at [`8a656ccb538d`](https://github.com/DragonnZhang/phistory/commit/8a656ccb538d9b79793d27975bd2b2184470011e). Its [Pages deployment](https://github.com/DragonnZhang/phistory/actions/runs/42358909986) also succeeded. A separate manual run verifies the restored fresh-cache capture path.

| Linux run | Explicit scope | Committed archive update | Result |
| --- | --- | --- | --- |
| [Latest and full smoke](https://github.com/DragonnZhang/phistory/actions/runs/42377065119) | Latest tracked agents; Qoder captured in its separate credentialed step | [`1fec8475bc2b`](https://github.com/DragonnZhang/phistory/commit/1fec8475bc2b354ce3f55a192425d1d826324cd1): Qoder `1.1.85` | success |
| [Kimi Code backfill and smoke](https://github.com/DragonnZhang/phistory/actions/runs/42377649463) | `default`, `2.5.0`–`2.5.2` | [`a157b246ae35`](https://github.com/DragonnZhang/phistory/commit/a157b246ae35069b7701a724820013a469ad82c8): `2.5.0`, `2.5.1` | success |
| [Qwen Code backfill and smoke](https://github.com/DragonnZhang/phistory/actions/runs/42377814174) | `default`, `0.26.8`–`0.26.9` | [`4accb961fdd6`](https://github.com/DragonnZhang/phistory/commit/4accb961fdd6a50c8f306e03603a81498df2b49c): `0.26.8` | success |
| [Qoder backfill and smoke](https://github.com/DragonnZhang/phistory/actions/runs/42377988862) | `default`, `1.1.83`–`1.1.85` | [`bf67c7080b68`](https://github.com/DragonnZhang/phistory/commit/bf67c7080b68cb6882035076d2ed1071f4f5aca2): `1.1.83` | success |

The four dispatches started from `8a656ccb538d9b79793d27975bd2b2184470011e`, `1fec8475bc2b354ce3f55a192425d1d826324cd1`, `a157b246ae35069b7701a724820013a469ad82c8`, and `4accb961fdd6a50c8f306e03603a81498df2b49c`, respectively. Backfills ran sequentially with `force=false`, `include_prerelease=false`, and `smoke=true`. Existing complete captures were reused; the smoke roots and install caches were fresh.

All four manual runs passed Ruff formatting/lint and all 196 tests. The latest run also passed the package build, latest captures, separate Qoder capture and credential scan, rendering, and full smoke. Its static-extraction step completed with the existing explicit skips for binary-only Claude packages; all fourteen Claude request lanes are runtime captures. Static package strings do not substitute for request evidence.

All 36 targets passed manifest status, nonempty main request, expected model selection, tool-count interpretation, committed-blob and actual Linux GitHub Actions provenance checks. The 35 additions comprise 14 Claude Code, 11 Codex, three Kimi Code, two Qwen Code, three Qoder, one OpenClaw, and one Hermes capture. All 14,592 prompt/trace/meta blobs belonging to the previous 4,864 atlas records are unchanged.

The full smoke supplies 30 atlas captures, excluding Qoder. Targeted smoke adds three Kimi Code, two Qwen Code, and three Qoder captures. All 38 match their committed counterparts in complete tool definitions and normalized system/developer text. Codex additional-tools message IDs vary per request; the comparison includes every tool definition and excludes that enclosing message ID. Existing Codex namespace-root versus expanded-tool counts remain explicitly marked and are explained by the actual nested declarations.

Qoder retains the published capture-only bootstrap: it enables only an existing official Auto catalog entry for request construction, preserving the rest of the catalog and native default selection. The condition remains recorded in `capture_environment` and `compatibility_patches`; model responses come from claude-tap. These archives do not require BYOK and do not claim to verify provider-account access.

The final capture manifest is at `bf67c7080b68cb6882035076d2ed1071f4f5aca2`, SHA-256 `43451cad737611009aa2ae437d785793eebacc46c38ed6a6fd74a59dd8ab1ad6`. The downstream atlas pins the published source commit that also contains this evidence record and records the same manifest hash.
