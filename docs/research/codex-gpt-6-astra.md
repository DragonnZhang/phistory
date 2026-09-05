# GPT-6 Astra capture boundary

Verified on 2026-09-05. The official model ID is [`gpt-6-astra`](https://developers.openai.com/api/docs/models/gpt-6-astra). The npm `latest` tag for `@openai/codex` was `0.153.4`.

Phistory starts the fixed Astra lane at **0.153.1**, the first stable CLI with Astra in its bundled model catalog. This differs from both the catalog's minimum client version and the release that exposes Astra in the model picker:

| Stable CLI | Bundled Astra entry | Visibility | Catalog minimum client |
| --- | --- | --- | --- |
| 0.153.0 | Absent | — | — |
| 0.153.1 | Present | `hide` | 0.153.0 |
| 0.153.2 | Present | `hide` | 0.153.0 |
| 0.153.3 | Present | `hide` | 0.153.0 |
| 0.153.4 | Present | `list` | 0.153.0 |

Sources: release-tagged catalogs for [0.153.0](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/models-manager/models.json), [0.153.1](https://github.com/openai/codex/blob/rust-v0.153.1/codex-rs/models-manager/models.json), [0.153.2](https://github.com/openai/codex/blob/rust-v0.153.2/codex-rs/models-manager/models.json), [0.153.3](https://github.com/openai/codex/blob/rust-v0.153.3/codex-rs/models-manager/models.json), and [0.153.4](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/models-manager/models.json); [npm release metadata](https://registry.npmjs.org/@openai/codex).

The matching npm Darwin ARM64 binaries corroborate the boundary. The 0.153.0 executable contains no `gpt-6-astra` string; 0.153.1 contains the model entry. Executable SHA-256 hashes:

| CLI | SHA-256 |
| --- | --- |
| 0.153.0 | `a29d9e86eef88cbbd69f97ce8c590b1d0a287c8f77424f5eef226b883d7eaa22` |
| 0.153.1 | `62709f1e3beddf61abdc16fc6e702e7fc90ad2aed26e33e6d319ab4a5a090c7a` |

The catalog configures Astra with `tool_mode=code_mode_only`, `multi_agent_version=v2`, and experimental `send_user_message_async` / `clock` tools. These catalog settings describe available behavior; the archived request determines which tools entered each capture. API documentation and CLI catalog context limits also describe different surfaces and should not be substituted for each other.

At CLI 0.153.1, the archived [Astra request](../../captures/codex/0.153.1/variants/gpt-6-astra/trace.jsonl) exposes 11 tools after expanding namespaces, versus 9 in the [Sol request](../../captures/codex/0.153.1/variants/gpt-5.6-sol/trace.jsonl). Astra adds `functions / request_user_input_async` and `clock / sleep`. Phistory metadata counts the top-level tool entries instead (3 versus 2); the trace atlas retains that count mismatch and displays the expanded tool surface.

Using the trace atlas's normalized system/developer text metric, Astra has 28,126 characters versus Sol's 23,122: a net increase of 5,004. The section diff adds 18,252 and removes 13,248 characters across three sections. These are character counts for the captured CLI prompts, not token counts or API model limits.

Reproduce the stable captures with:

```bash
uv run phistory backfill codex --from 0.153.1 --to 0.153.4 --variants gpt-6-astra --skip-static
uv run phistory render-index
uv run phistory render-site
```

Each capture runs the normal `codex exec --model gpt-6-astra` command through `claude-tap` with isolated fake ChatGPT auth. The raw request is archived without calling a real model provider. The `default` lane continues to omit an explicit model override.
