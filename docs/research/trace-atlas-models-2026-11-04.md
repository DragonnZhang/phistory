# November 4 trace refresh

Official release discovery at `2026-11-04T04:50:29.995718+00:00` checked the eight agents already present in the atlas. The target set contains 47 release/variant pairs, including unchanged latest lanes. Existing scheduled Linux captures are reused. This refresh accepts 46 targets and adds 33 records to the atlas; Qoder `1.1.82` remains an unresolved capture target, so this is a partial release.

| Agent | Previous atlas | Latest stable | Intervening stable releases |
| --- | --- | --- | --- |
| claude-code | `2.1.316` | `2.1.318` | `2.1.317`, `2.1.318` |
| codex | `0.166.0` | `0.166.0` | — |
| kimi-code | `2.4.1` | `2.4.1` | — |
| qwen-code | `0.26.5` | `0.26.7` | `0.26.6`, `0.26.7` |
| qoder | `1.1.81` | `1.1.82` | `1.1.82` |
| openclaw | `2026.10.9` | `2026.10.10` | `2026.10.10` |
| hermes | `v0.22.5` | `v0.22.7` | `v0.22.6`, `v0.22.7` |
| opencode | `1.19.5` | `1.19.5` | — |

The sources are the registered official npm packages and the [Hermes release feed](https://github.com/NousResearch/hermes-agent/releases). Every published stable release between the previous atlas endpoint and the latest target is checked. The only missing target is Qoder `1.1.82`; its last valid atlas point remains `1.1.81`. Qoder static-only archives and the retired Codex `gpt-5.6` fallback alias remain excluded. The atlas scope is unchanged.

## Model verification

The [official OpenAI model catalog](https://developers.openai.com/api/docs/models) and [Codex model guidance](https://developers.openai.com/codex/models) still list the existing GPT families, including GPT-6.1 Terra. Exact release `0.166.0` remains the latest stable Codex. Its [release catalog](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/models-manager/models.json) is byte-identical to the previously checked copy, SHA-256 `1384625c0b6b3e67d3c60da29becea2b017f2c283fc1e2d05b8edafb4698eb10`. The same catalog occurs at byte offset `264781427` in the exact Linux x64 executable. The cached npm archive passed the newly fetched SHA-512 integrity metadata; the executable hash is `b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8`. All nine visible model IDs already have fixed lanes. Hidden Daybreak and auto-review entries are unchanged. Terra retains its first stable bundled-catalog boundary at `0.165.0`; compatibility minima are not history starts.

The [Claude model overview](https://platform.claude.com/docs/en/models/overview) and [Claude Code model configuration](https://support.claude.com/en/articles/11940350-claude-code-model-configuration) still list Fable 5.1, Opus 5.5, Sonnet 5.5 and Haiku 5.5. The exact `@anthropic-ai/claude-code-darwin-arm64@2.1.318` package passed npm SHA-512 verification. Its executable is `319598464` bytes with SHA-256 `677a2ea7bf576cedd20e191d973914ed8a41796f53a63b1923381ddc4d5b5e7d`, built `2026-11-03T18:52:53Z` from `ca1fa9dea62c1027756786c841c548788644560d`. The [scoped binary baseline](../../.claude/design/anchors-2.1.318-trace-models.txt) confirms the same 22 catalog identities as `2.1.316`. New feature/path strings and an unbounded binary-string match were checked separately and do not establish new model IDs. No model lane or display boundary changes. No old-source mapping or local prompt-bearing probe was needed.

## Linux capture and artifact acceptance

The [November 3 scheduled capture](https://github.com/DragonnZhang/phistory/actions/runs/41615073611) committed 17 atlas records at `60884efb4964c15c379695147629b4f6a27b7b71`. The [November 4 manual latest run](https://github.com/DragonnZhang/phistory/actions/runs/41775855556) committed another 16 at [`8103bbae16ced`](https://github.com/DragonnZhang/phistory/commit/8103bbae16ced5dad6aced1aac0eb15cb96bfcbb). Together they add 28 Claude Code records (all fourteen lanes at each of `2.1.317` and `2.1.318`), two Qwen Code records (`0.26.6`, `0.26.7`), one OpenClaw record (`2026.10.10`), and two Hermes records (`v0.22.6`, `v0.22.7`).

Each of those 33 records passed individual manifest, nonempty main request, requested/observed model, tool-count, committed-blob and actual Linux GitHub Actions provenance checks. The existing raw archives are unchanged. Thirty atlas records from the manual run's independent fresh-cache smoke match their committed counterparts in complete tool definitions and normalized system/developer text. That smoke excludes Qoder, as specified by the existing workflow; it is not evidence for Qoder success.

Ruff formatting/lint, all 179 unit tests, package build, main latest capture and smoke succeeded. The manual workflow is nevertheless **failed**, because its separate Qoder step failed. The Claude Code static extraction step completed with the existing explicit binary-only-package skips for the latest ten releases; no static prompt archive was substituted for runtime evidence. All fourteen Claude request lanes have valid runtime captures.

## Unresolved Qoder target

The initial `1.1.82` attempt reported TLS handshake timeouts for `api1.qoder.sh`, `center.qoder.sh` and `api2.qoder.sh`, plus a failed HTTPDNS connection to `203.107.1.1:443`, before producing any valid prompt request. The [targeted backfill retry](https://github.com/DragonnZhang/phistory/actions/runs/41777281458) used only `qoder/default`, exact range `1.1.82` to `1.1.82`, `force=false`, and `smoke=true`. It also completed with failure: the CLI exited with code 1, `api_calls: 0`, and `no valid records found in trace file`. It committed no new capture; its independent smoke was correctly skipped after the archive failed.

Further Linux diagnostics identify the blocking error before request construction:

- [Same-runner comparison](https://github.com/DragonnZhang/phistory/actions/runs/41783426200): `1.1.81` captures successfully with the same credential, while `1.1.82` records `ModelNotFoundError: main model "auto" is unavailable.` in its native log.
- [Model-selection observation](https://github.com/DragonnZhang/phistory/actions/runs/41784125580): authentication succeeds, the model catalog returns HTTP 200 and 15 entries, and `auto` exists with the expected `qoder` source but `enabled: false`. Passing only the PAT instead of both supported credential aliases produces the same result. The temporary observation prints these fields without changing selection logic; it is diagnostic evidence, not a published capture.
- [Unmodified CLI check](https://github.com/DragonnZhang/phistory/actions/runs/41785197056): native `--list-models` exits successfully and lists only `Qwen3.8-Flash` for this credential. The default capture still fails with the same native model error. Official JSON output hides that cause behind a generic `error_code: 500`, so changing output format would not repair the diagnostic gap.

The exact `1.1.82` bundle rejects a selected model whose catalog entry has `enabled: false`. The [official model guide](https://docs.qoder.com/cli/model) still describes Auto as the default; it also documents the account-specific `--list-models` check. This establishes the current credential's catalog restriction, not why the service disabled Auto. Expired authentication, exhausted credits, global Auto retirement, and a broken request hook are not established. The older release reaching the capture boundary does not verify live model access.

Phistory now extracts only the recognized native model-selection error from newly written log bytes while the isolated HOME still exists. It includes that reason in the failed capture and stops the session-persistence fallback and generic empty-trace retries for that failure. Full native logs and account data are not copied into artifacts. Transient failures retain their bounded retries; an already captured prompt remains valid even if the CLI later reports an error. Four fake-CLI regressions cover fail-fast behavior, credential redaction, preservation of existing archives, stale-log exclusion, transient retry, and valid-prompt retention. All 183 tests, Ruff, and the package build pass.

The default model and CLI arguments remain unchanged. Restoring the `1.1.82` default capture requires Auto to become available to the CI credential or an upstream default-selection fix. Qoder `1.1.82` remains outside the accepted data, and the existing `1.1.81` point remains visible.

## Committed source and downstream generation

The accepted capture commit is `8103bbae16ced5dad6aced1aac0eb15cb96bfcbb`, manifest SHA-256 `52c0378c426b2efab9264271081f84651bab951b3d1a1608d508849c88b7c7d4`. Its [Pages deployment](https://github.com/DragonnZhang/phistory/actions/runs/41777510770) succeeded. The qwen methodology records the final clean published source including this verification note and the separately verified online manifest.

The external generator's usual directory is absent. Generation reuses the existing clean clone at `/private/tmp/agent-trace-nov1/generator`, commit `1b69f743e84d43f713253fb1aa39a435deb40272`, with `scripts/build_history.py` SHA-256 `92a2f7ee2e6c534b894ca3a95cafe247400f1f83dcf59ef87b73496ef42b462f`. No generator-repository changes are made. Model identities, historical display boundaries and same-CLI comparisons are retained; the qwen release records its historical reconstruction, curve, build and browser checks separately.
