# Claude Code Opus 5 delegation guard: public evidence

Date: 2026-08-31  
Current binary checked separately: Claude Code 2.1.251

## Bottom line

The public evidence does not contain an Anthropic maintainer explanation for these two Opus 5 system-prompt lines:

```text
Do not call the AgentTool unless the user requested it
Do not use workflows or deep-research unless the user requested it
```

The strongest explanation supported by official material is that the second line is a cost-and-consent guard. Dynamic workflows can launch many agents and consume substantially more tokens, and Anthropic's workflow documentation frames both a workflow request and UltraCode as explicit human opt-ins. Claude Code 2.1.218 also deliberately changed `/deep-research` so Claude could no longer start it autonomously.

The extension of that policy to ordinary subagents looks overbroad or unfinished. It conflicts with Anthropic's subagent documentation, which says Claude can delegate automatically based on the request, agent description, and current context. The prompt also names `AgentTool`, while the actual registered tool is `Agent`, and it remains present when UltraCode is enabled.

The most defensible interpretation is therefore:

- `workflows or deep-research`: probably a deliberate guard against silently starting expensive, highly parallel work without user consent;
- `AgentTool`: probably an overcorrection, experiment artifact, or implementation defect bundled into the same model-specific counter-steer;
- `xhigh`: not the cause. The latest binary gates the block on the Opus 5 prompt capability plus a feature-flag override, not on effort;
- UltraCode: does not remove the block. It adds a standing user opt-in that separately tells Claude to use Workflow on substantive tasks.

## Confirmed chronology

- Claude Code 2.1.218, released 2026-07-22, says `/deep-research` now starts only when manually invoked and Claude no longer launches it itself.
- Claude Code 2.1.219, released 2026-07-24, introduced Opus 5, expanded nested subagents from depth 1 to depth 3, and changed dynamic workflows to a default guideline of fewer than 15 agents.
- Public binary-analysis reports locate the exact two-line block in 2.1.219 and later, but not 2.1.218. They describe it as an Opus-5-capability-gated prompt bundle with a feature-flag kill switch and no documented user setting.
- A current 2.1.251 runtime A/B capture confirms that both `xhigh + ultracode=false` and `xhigh + ultracode=true` retain the block.

The release timing is suggestive: the block arrived with Opus 5, and Anthropic described Opus 5 as having stronger agency and greater thoroughness. It is reasonable to infer that the model-specific instruction was intended to keep that increased agency from turning into unrequested agent fan-out. This is an inference, not a published Anthropic rationale.

## Evidence for the cost-and-consent explanation

Anthropic's workflow documentation says:

- users can explicitly ask Claude to start a workflow or use the `ultracode` keyword;
- UltraCode is an explicit opt-in and combines `xhigh` effort with automatic workflows;
- workflows use meaningfully more tokens than a normal session;
- runs exceeding 25 agents or 1.5 million tokens trigger a warning, while UltraCode suppresses that warning because the user has already opted into a large run.

Those statements explain the purpose of restricting workflows and deep research much better than an effort-level theory does.

## Evidence that the implementation is inconsistent

Anthropic's subagent documentation says Claude automatically decides whether to delegate from the user's request, the agent description, and the current context. It even recommends wording such as “use proactively” in an agent description. A blanket “unless the user requested it” instruction is stricter than those published semantics.

Multiple public bug reports identify the same inconsistencies:

- the name `AgentTool` does not match the actual `Agent` tool;
- the restriction can override project instructions asking Claude to delegate proactively;
- enabling UltraCode does not remove it;
- it is injected as system-level text and has no documented user-facing opt-out;
- one reproduction saw explicit delegation recover subagent use, but the reporter also found that the preceding version sometimes failed to delegate, so this block is not the sole explanation for every delegation regression.

## Sources

Primary Anthropic sources:

- [Claude Code 2.1.218 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.218)
- [Claude Code 2.1.219 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.219)
- [Dynamic workflows documentation](https://code.claude.com/docs/en/workflows)
- [Subagents documentation](https://code.claude.com/docs/en/sub-agents)
- [Claude Opus 5 announcement](https://www.anthropic.com/news/claude-opus-5)

Public reports and binary analyses:

- [Issue #80988: Opus 5 system prompt silently disables agent delegation](https://github.com/anthropics/claude-code/issues/80988)
- [Issue #82456: regression report for automatic custom-agent selection](https://github.com/anthropics/claude-code/issues/82456)
- [Issue #83439: UltraCode does not suppress the restriction](https://github.com/anthropics/claude-code/issues/83439)
- [Issue #81263: `AgentTool` mismatch and conflicting prompt instructions](https://github.com/anthropics/claude-code/issues/81263)
- [Issue #80998: invisible system-prompt injection and precedence](https://github.com/anthropics/claude-code/issues/80998)
- [Issue #84053: version boundary and earlier server-side mechanism](https://github.com/anthropics/claude-code/issues/84053)

## Confidence boundary

Confirmed: version boundary, release-note changes, official workflow cost/opt-in semantics, official subagent auto-delegation semantics, latest-binary gating, and the latest runtime A/B result.

Inferred: Anthropic added the model-specific counter-steer because Opus 5 is more agentic and might otherwise over-delegate. No public maintainer statement found states that motive directly.
