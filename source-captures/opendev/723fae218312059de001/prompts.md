# opendev — System prompt source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source templates and assembly code; runtime substitutions and optional modes are not resolved.

## crates/opendev-agents/src/prompts/composer/factories.rs

Source: https://github.com/opendev-to/opendev/blob/d32c660e4eed1a8e988d1fd58da88e41ba641d08/crates/opendev-agents/src/prompts/composer/factories.rs

SHA-256: `760678e8e91c681b5dda4a77adddb3c6b98929795452664c4c92c4a24ab1bc26`

```
//! Factory functions that create pre-configured [`PromptComposer`] instances.
//!
//! Each factory registers the appropriate set of prompt sections for
//! the default agent mode.

use std::path::Path;

use super::CachePolicy;
use super::PromptComposer;
use super::conditions::{ctx_bool, ctx_eq, ctx_in, ctx_present};

/// Create a default composer with standard sections registered.
///
/// Priority ranges:
/// - 10-30: Core identity and policies (always loaded, `Static`)
/// - 40-50: Tool guidance and interaction patterns (`Static`)
/// - 55-65: Code quality and workflows (`Static`)
/// - 70-80: Conditional sections (git, MCP, etc.)
/// - 85-95: Context-specific additions (`Cached` for dynamic content)
pub fn create_default_composer(templates_dir: impl AsRef<Path>) -> PromptComposer {
    let mut composer = PromptComposer::new(templates_dir.as_ref());

    // Core sections (always included) - Priority 10-30, Static
    composer.register_section_with_policy(
        "mode_awareness",
        "system/main/main-mode-awareness.md",
        None,
        12,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "security_policy",
        "system/main/main-security-policy.md",
        None,
        15,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "tone_and_style",
        "system/main/main-tone-and-style.md",
        None,
        20,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "output_efficiency",
        "system/main/main-output-efficiency.md",
        None,
        22,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "no_time_estimates",
        "system/main/main-no-time-estimates.md",
        None,
        25,
        CachePolicy::Static,
    );

    // Tool guidance - Priority 45, Static
    composer.register_section_with_policy(
        "tool_selection",
        "system/main/main-tool-selection.md",
        None,
        45,
        CachePolicy::Static,
    );

    // Code quality and workflows - Priority 55-65, Static
    composer.register_section_with_policy(
        "code_quality",
        "system/main/main-code-quality.md",
        None,
        55,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "action_safety",
        "system/main/main-action-safety.md",
        None,
        56,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "error_recovery",
        "system/main/main-error-recovery.md",
        None,
        60,
        CachePolicy::Static,
    );

    // Conditional sections - Priority 65-80, Static
    composer.register_section_with_policy(
        "subagent_guide",
        "system/main/main-subagent-guide.md",
        Some(ctx_bool("has_subagents")),
        65,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "agent_team_guide",
        "system/main/main-agent-team-guide.md",
        Some(ctx_bool("has_agent_teams")),
        66,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "git_workflow",
        "system/main/main-git-workflow.md",
        Some(ctx_bool("in_git_repo")),
        70,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "verification",
        "system/main/main-verification.md",
        None,
        72,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "task_tracking",
        "system/main/main-task-tracking.md",
        Some(ctx_bool("todo_tracking_enabled")),
        75,
        CachePolicy::Static,
    );

    // Provider-specific sections - Priority 80, Static
    composer.register_section_with_policy(
        "provider_openai",
        "system/main/main-provider-openai.md",
        Some(ctx_eq("model_provider", "openai")),
        80,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "provider_anthropic",
        "system/main/main-provider-anthropic.md",
        Some(ctx_eq("model_provider", "anthropic")),
        80,
        CachePolicy::Static,
    );
    composer.register_section_with_policy(
        "provider_fireworks",
        "system/main/main-provider-fireworks.md",
        Some(ctx_in("model_provider", &["fireworks", "fireworks-ai"])),
        80,
        CachePolicy::Static,
    );

    // Context awareness - Priority 85-95
    composer.register_section_with_policy(
        "output_awareness",
        "system/main/main-output-awareness.md",
        None,
        85,
        CachePolicy::Static,
    );
    // Auto memory: Cached — content may change if memory files are updated mid-session
    composer.register_section_with_policy(
        "auto_memory",
        "system/main/main-auto-memory.md",
        None,
        86,
        CachePolicy::Cached,
    );
    // Scratchpad: Cached — session-specific, refreshed on /clear or /compact
    composer.register_section_with_policy(
        "scratchpad",
        "system/main/main-scratchpad.md",
        Some(ctx_present("session_id")),
        87,
        CachePolicy::Cached,
    );
    composer.register_section_with_policy(
        "code_references",
        "system/main/main-code-references.md",
        None,
        90,
        CachePolicy::Static,
    );
    // System reminders note: Cached — refreshed on /clear or /compact
    composer.register_section_with_policy(
        "system_reminders_note",
        "system/main/main-reminders-note.md",
        None,
        95,
        CachePolicy::Cached,
    );

    composer
}

/// Create the appropriate composer for a given mode.
pub fn create_composer(templates_dir: impl AsRef<Path>, _mode: &str) -> PromptComposer {
    create_default_composer(templates_dir)
}

#[cfg(test)]
#[path = "factories_tests.rs"]
mod tests;

```

## crates/opendev-agents/templates/system/main/main-code-quality.md

Source: https://github.com/opendev-to/opendev/blob/d32c660e4eed1a8e988d1fd58da88e41ba641d08/crates/opendev-agents/templates/system/main/main-code-quality.md

SHA-256: `d847bd85b592132d69ac7966285127e312d8660c3ab65dd4b0192227b700bd0e`

```
<!--
name: 'System Prompt: Code Quality and Task Execution'
description: How to approach tasks, code quality standards, and anti-patterns
version: 3.0.0
-->

# Doing Tasks

- The user will primarily request you to perform software engineering tasks. These may include solving bugs, adding new functionality, refactoring code, explaining code, and more. When given an unclear or generic instruction, consider it in the context of these software engineering tasks and the current working directory.
- You are highly capable and often allow users to complete ambitious tasks that would otherwise be too complex or take too long. Defer to user judgment about whether a task is too large to attempt.
- In general, do not propose changes to code you haven't read. If a user asks about or wants you to modify a file, read it first. Understand existing code before suggesting modifications.
- **Always read a file before editing it.** The Edit tool requires old_content to match exactly — if you haven't read the file recently, your edit will fail.
- Do not create files unless they're absolutely necessary for achieving your goal. Prefer editing an existing file to creating a new one.
- If an approach fails, diagnose why before switching tactics — read the error, check your assumptions, try a focused fix. Don't retry the identical action blindly, but don't abandon a viable approach after a single failure either. Ask the user only when you're genuinely stuck after investigation.
- Be careful not to introduce security vulnerabilities such as command injection, XSS, SQL injection, and other OWASP top 10 vulnerabilities. If you notice insecure code, fix it immediately.

# Code Quality Standards

- Follow existing conventions strictly; keep changes focused and minimal
- Don't add features, refactor code, or make "improvements" beyond what was asked. A bug fix doesn't need surrounding code cleaned up. A simple feature doesn't need extra configurability.
- Don't add docstrings, comments, or type annotations to code you didn't change. Only add comments where the logic isn't self-evident.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust internal code and framework guarantees. Only validate at system boundaries (user input, external APIs).
- Don't create helpers, utilities, or abstractions for one-time operations. Don't design for hypothetical future requirements. The right amount of complexity is what the task actually requires — no speculative abstractions, but no half-finished implementations either. Three similar lines of code is better than a premature abstraction.
- Don't use feature flags or backwards-compatibility shims when you can just change the code.
- Avoid backwards-compatibility hacks like renaming unused _vars, re-exporting types, or adding comments for removed code. If you are certain something is unused, delete it completely.

## Anti-patterns to Avoid

- **Over-engineering**: Creating abstractions for single-use code
- **Scope creep**: Adding features not requested
- **Premature optimization**: Optimizing before measuring
- **Backward-compatibility hacks**: Keeping unused code "just in case"
- **Focused changes**: Minimal diff, clear purpose ✓
- **Existing patterns**: Follow what's already there ✓
- **Delete unused code**: If certain it's unused, delete completely ✓

```

## crates/opendev-agents/templates/system/main/main-error-recovery.md

Source: https://github.com/opendev-to/opendev/blob/d32c660e4eed1a8e988d1fd58da88e41ba641d08/crates/opendev-agents/templates/system/main/main-error-recovery.md

SHA-256: `73dc98e9c0b49a7f76a8b735798bf8d53d39308c0d525beb8b2ec2ccba74fb83`

```
<!--
name: 'System Prompt: Error Recovery'
description: Common error patterns and resolution strategies
version: 2.0.0
-->

# Error Recovery

When a tool fails, read the error message carefully and apply the matching resolution:

- **"File not found"** — Path is incorrect. Use `Glob` or `search` to locate the correct path before retrying.
- **"Permission denied"** — Insufficient permissions. Check file permissions or try a different approach.
- **"old_content not found"** — The file has changed since you last read it, or your memory of the content is wrong. Re-read the file and retry with the correct content.
- **Rate limit errors** — Too many requests. The system retries automatically; if it persists, reduce concurrency.

**Process**:
1. Read the error message carefully
2. Match the error to a known pattern above
3. Apply the resolution strategy
4. Retry with the corrected approach
5. If still failing, ask the user for help

**NEVER**:
- Retry the same failing command repeatedly without changes
- Ignore error messages
- Continue without fixing the root cause

```
