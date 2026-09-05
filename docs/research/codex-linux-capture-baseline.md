# Codex Linux capture baseline

Verified on 2026-09-06. All **89 Codex snapshots with macOS environment markers** were replaced by fresh captures from Linux GitHub Actions. The four successful backfills produced 99 snapshots: the 89 replacements plus 10 existing Linux snapshots recaptured to keep all six model lanes at 0.153.1–0.153.4 in the same run.

| CLI versions | Variants | CI snapshots | Mac snapshots replaced | Run |
| --- | --- | ---: | ---: | --- |
| 0.153.1–0.153.4 | default, Astra, Sol, Terra, Luna, GPT-5.5 | 24 | 14 | [33978692330](https://github.com/DragonnZhang/phistory/actions/runs/33978692330) |
| 0.144.0–0.150.1 | GPT-5.6 Sol, Terra, Luna | 48 | 48 | [33978743928](https://github.com/DragonnZhang/phistory/actions/runs/33978743928) |
| 0.131.0–0.145.0 | GPT-5.5 | 26 | 26 | [33978833614](https://github.com/DragonnZhang/phistory/actions/runs/33978833614) |
| 0.150.0 | GPT-5.5 | 1 | 1 | [33979026796](https://github.com/DragonnZhang/phistory/actions/runs/33979026796) |

Each new `meta.json.capture_host` records `platform=Linux`, `architecture=x86_64`, `runner=github-actions`, and the run URL. All 99 prompts report Bash. The runs used the existing `backfill.yml` workflow on `ubuntu-latest`, explicit variants, and `force=true`; `claude-tap` returned dummy responses without calling a real model provider. The raw traces are the newly collected Linux requests.

Compared with the pre-recapture artifacts at [`671f302e`](https://github.com/DragonnZhang/phistory/commit/671f302e), all 99 normalized prompts match after only removing the macOS `/private` prefix before `$PHISTORY_*` placeholders and replacing `<shell>zsh</shell>` with `<shell>bash</shell>`. Exactly 89 prompts changed; there are no other normalized prompt differences. A complete scan of archived Codex prompts and raw traces found no remaining macOS temp-directory, `Platform: darwin`, or zsh environment markers.

The 0.153.4 default lane observes `gpt-6-astra` and now has the same normalized prompt as the pinned Astra lane. The same-version Astra/Sol comparison therefore uses a common Linux environment; see the [Astra analysis](codex-gpt-6-astra.md) for tool and character metrics.

Future real captures, backfills, recaptures, and smoke captures run in Linux CI as required by [AGENTS.md](../../AGENTS.md). Local work can analyze committed captures, run fake-CLI tests, and regenerate the site or downstream atlas. Legacy metadata without `capture_host` is left unchanged unless that snapshot is actually recaptured.
