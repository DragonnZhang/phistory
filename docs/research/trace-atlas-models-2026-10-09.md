# Haiku 5.5 and October 9 trace refresh

Verified on 2026-10-09 (Asia/Singapore). Version metadata comes from each registry package or official GitHub release; model discovery also reads the official [Claude catalog](https://platform.claude.com/docs/en/models/overview), [Claude release notes](https://platform.claude.com/docs/en/release-notes/overview), [OpenAI catalog](https://developers.openai.com/api/docs/models) and [Codex availability](https://learn.chatgpt.com/docs/models).

## Targets

| Agent | Latest stable release | Published (UTC) |
| --- | --- | --- |
| claude-code | `2.1.295` | 2026-10-08T18:22:58.621Z |
| codex | `0.162.0` | 2026-10-08T19:01:18.070Z |
| kimi-code | `2.1.1` | 2026-09-24T07:27:15.480Z |
| qwen-code | `0.25.0` | 2026-10-05T09:55:55.980Z |
| qoder | `1.1.66` | 2026-10-08T12:20:28.530Z |
| openclaw | `2026.9.9` | 2026-10-08T09:38:01.687Z |
| hermes | `v0.21.6` | 2026-10-08T11:51:57Z |
| opencode | `1.18.35` | 2026-10-06T20:21:30.784Z |

Preserve all existing lanes. Add `official-haiku-5-5` with exact model `claude-haiku-5-5`, forward capture, and `min_version=2.1.293`. Backfill that lane through 2.1.295 and fill 2.1.294 for existing Claude lanes. Existing 2.1.293 and Codex 0.161.0 captures from Linux run [37756290767](https://github.com/DragonnZhang/phistory/actions/runs/37756290767) are reused. Latest captures cover the remaining endpoints. Hermes changed release numbering from v2026.9.24 to v0.21.6; publication chronology, not numeric magnitude, determines its latest release.

## Confirmed binary facts

Both new platform tarballs passed npm SHA-512 integrity verification. No CLI request was executed locally.

| Package | Bytes | Binary SHA-256 |
| --- | ---: | --- |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.293` (verified cached package) | 236330608 | `4e21122a227857da1178aca3299700c1fd7f2b77c93f12e73c2c76db796a105e` |
| `@anthropic-ai/claude-code-darwin-arm64@2.1.295` | 239695888 | `0116ee2e0a513900b633d9951367f18747686478e2b462805b8c31609f047f70` |
| `@openai/codex@0.162.0-linux-x64` | 294410952 | `50ed828f357c655a3c82054d346cab8434f24901f14ec19b8267571bdd008b38` |

The latest Claude binary reports build time `2026-10-08T16:50:59Z` and Git SHA `07e8f67ea3282bf154a9e05673a0942b1b173cef`. Its explicit Haiku 5.5 catalog object is at byte 186673038; the matching 2.1.293 object is at 184392108. Both have first-party ID `claude-haiku-5-5`, native 1M context, 128k output, medium default effort, adaptive thinking and lean-prompt capability. These are model-specific catalog entries, not merely an arbitrary accepted CLI string. The model launched on October 7 in the official release notes. Version 2.1.293 is the earliest verified launch-day CLI in this investigation, not a claim that every earlier binary was exhaustively checked.

The Codex [0.162.0 release catalog](https://github.com/openai/codex/blob/rust-v0.162.0/codex-rs/models-manager/models.json) has SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b` and occurs byte-for-byte in the matching Linux binary at byte 243512246. It retains the eight listed GPT models already archived. Hidden Daybreak and auto-review entries are outside the normal model picker; there is no new ordinary Codex model lane. GPT-6.1 Sol Ultrafast is a speed mode, not a new model ID.

## Scoped anchor baseline

Compared with the verified 2.1.293 TaskStop baseline (`anchors-2.1.293-task-stop.txt`), the latest binary is 3,365,280 bytes larger. This investigation tracks only model catalog identity and capture support. It does not enumerate unrelated hooks, slash commands, telemetry or feature gates. The old Claude source snapshot was not used to claim current behavior. Request-level model and prompt verification must come from the Linux captures below; binary capabilities alone do not prove wire tool exposure.

## Linux capture and Hermes repair

Latest run [37885033077](https://github.com/DragonnZhang/phistory/actions/runs/37885033077) committed Claude Code 2.1.295 (all 12 lanes), Codex 0.162.0 (all 10 lanes), Qoder 1.1.66 and OpenClaw 2026.9.9. Its fresh smoke produced 38 valid captures, including 26 within the atlas scope. Hermes alone failed in both capture and smoke before emitting a request: the v0.21.6 archive contains a root `pyproject.toml` plus a nested `pm/pyproject.toml`, while the installer required exactly one project anywhere in the archive. Official tagged Git blobs confirm both files (`648a597cad21b14c94782dca09d9d0db3bf54c2a` and `4c62dcff35ed795eba0eac29027e3ace6758ec25`).

The general fix selects a single outermost Python project and still rejects ambiguous sibling projects. A fake archive installation test covers the nested layout. The optional `backfill.yml` smoke input repeats only the selected range using a fresh root/cache, so this repair can be verified without retrying successful agents. Release/site ordering also uses publication chronology for GitHub release sources; regression coverage ensures that Hermes v0.21.6 follows v2026.9.24 and becomes the latest comparison endpoint.

Claude backfill [37885095068](https://github.com/DragonnZhang/phistory/actions/runs/37885095068) succeeded, adding all 12 lanes at 2.1.294 plus Haiku 5.5 at 2.1.293. Earlier valid 2.1.293 captures were reused. Recent Claude wrapper packages still contain no source supported by the static extractor; the explicit skip is unchanged, and the atlas uses the actual captured request prompts and tools.

First targeted Hermes retry [37886067098](https://github.com/DragonnZhang/phistory/actions/runs/37886067098) passed root selection, then failed at runtime with missing `ruamel.yaml`. The same release pins Python 3.14 in `.python-version`; its `pyproject.toml` explicitly limits runtime dependencies to Python ≥3.14 while admitting older Python only for its updater. The CI host used Python 3.12. The installer now honors each editable release's own `.python-version` before installing its dependencies, instead of adding individual missing packages. Fake installation tests cover both pinned and unpinned archives.

The second targeted retry [37886446571](https://github.com/DragonnZhang/phistory/actions/runs/37886446571) succeeded in both capture and fresh-cache smoke. The main request contains 24 tools and model `phistory-dummy`; both archives retain their actual Linux/GitHub Actions provenance. Its committed capture is in `064e4710`. No further capture retry was needed. The optional smoke artifact guard accepts its actual capture root and is tested against both clean and deliberately contaminated fake traces before allowing upload.

Final archive verification covers 62 new or endpoint records across all eight atlas agents, including 59 records added since the previous atlas source. Every checked prompt/trace/meta is nonempty and byte-identical to its committed artifact; selected main requests and fixed-model IDs match their targets. All new records have real Linux `capture_host` links. The 26 in-scope broad smoke captures and the one targeted Hermes smoke are valid; Qoder's separate credentialed capture passed its archive check. No existing raw trace was rewritten. Haiku 5.5 has captures at 2.1.293, 2.1.294 and 2.1.295, with Haiku 4.5 available at the same CLI versions for controlled comparisons.
