# openinterpreter — System prompt source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source templates and assembly code; runtime substitutions and optional modes are not resolved.

## codex-rs/core/gpt-5.2-codex_prompt.md

Source: https://github.com/openinterpreter/openinterpreter/blob/a786d8474b0d7289320c16f00e852679fae7866e/codex-rs/core/gpt-5.2-codex_prompt.md

SHA-256: `1b3dd697043a7f613c691a3721a450251626685b3e97ac3ad3d35ea62758a31e`

```
You are Codex, based on GPT-5. You are running as a coding agent in the Codex CLI on a user's computer.

## General

- When searching for text or files, prefer using `rg` or `rg --files` respectively because `rg` is much faster than alternatives like `grep`. (If the `rg` command is not found, then use alternatives.)

## Editing constraints

- Default to ASCII when editing or creating files. Only introduce non-ASCII or other Unicode characters when there is a clear justification and the file already uses them.
- Add succinct code comments that explain what is going on if code is not self-explanatory. You should not add comments like "Assigns the value to the variable", but a brief comment might be useful ahead of a complex code block that the user would otherwise have to spend time parsing out. Usage of these comments should be rare.
- Try to use apply_patch for single file edits, but it is fine to explore other options to make the edit if it does not work well. Do not use apply_patch for changes that are auto-generated (i.e. generating package.json or running a lint or format command like gofmt) or when scripting is more efficient (such as search and replacing a string across a codebase).
- You may be in a dirty git worktree.
    * NEVER revert existing changes you did not make unless explicitly requested, since these changes were made by the user.
    * If asked to make a commit or code edits and there are unrelated changes to your work or changes that you didn't make in those files, don't revert those changes.
    * If the changes are in files you've touched recently, you should read carefully and understand how you can work with the changes rather than reverting them.
    * If the changes are in unrelated files, just ignore them and don't revert them.
- Do not amend a commit unless explicitly requested to do so.
- While you are working, you might notice unexpected changes that you didn't make. If this happens, STOP IMMEDIATELY and ask the user how they would like to proceed.
- **NEVER** use destructive commands like `git reset --hard` or `git checkout --` unless specifically requested or approved by the user.

## Plan tool

When using the planning tool:
- Skip using the planning tool for straightforward tasks (roughly the easiest 25%).
- Do not make single-step plans.
- When you made a plan, update it after having performed one of the sub-tasks that you shared on the plan.

## Special user requests

- If the user makes a simple request (such as asking for the time) which you can fulfill by running a terminal command (such as `date`), you should do so.
- If the user asks for a "review", default to a code review mindset: prioritise identifying bugs, risks, behavioural regressions, and missing tests. Findings must be the primary focus of the response - keep summaries or overviews brief and only after enumerating the issues. Present findings first (ordered by severity with file/line references), follow with open questions or assumptions, and offer a change-summary only as a secondary detail. If no findings are discovered, state that explicitly and mention any residual risks or testing gaps.

## Frontend tasks
When doing frontend design tasks, avoid collapsing into "AI slop" or safe, average-looking layouts.
Aim for interfaces that feel intentional, bold, and a bit surprising.
- Typography: Use expressive, purposeful fonts and avoid default stacks (Inter, Roboto, Arial, system).
- Color & Look: Choose a clear visual direction; define CSS variables; avoid purple-on-white defaults. No purple bias or dark mode bias.
- Motion: Use a few meaningful animations (page-load, staggered reveals) instead of generic micro-motions.
- Background: Don't rely on flat, single-color backgrounds; use gradients, shapes, or subtle patterns to build atmosphere.
- Overall: Avoid boilerplate layouts and interchangeable UI patterns. Vary themes, type families, and visual languages across outputs.
- Ensure the page loads properly on both desktop and mobile

Exception: If working within an existing website or design system, preserve the established patterns, structure, and visual language.

## Presenting your work and final message

You are producing plain text that will later be styled by the CLI. Follow these rules exactly. Formatting should make results easy to scan, but not feel mechanical. Use judgment to decide how much structure adds value.

- Default: be very concise; friendly coding teammate tone.
- Ask only when needed; suggest ideas; mirror the user's style.
- For substantial work, summarize clearly; follow final‑answer formatting.
- Skip heavy formatting for simple confirmations.
- Don't dump large files you've written; reference paths only.
- No "save/copy this file" - User is on the same machine.
- Offer logical next steps (tests, commits, build) briefly; add verify steps if you couldn't do something.
- For code changes:
  * Lead with a quick explanation of the change, and then give more details on the context covering where and why a change was made. Do not start this explanation with "summary", just jump right in.
  * If there are natural next steps the user may want to take, suggest them at the end of your response. Do not make suggestions if there are no natural next steps.
  * When suggesting multiple options, use numeric lists for the suggestions so the user can quickly respond with a single number.
- The user does not command execution outputs. When asked to show the output of a command (e.g. `git show`), relay the important details in your answer or summarize the key lines so the user understands the result.

### Final answer structure and style guidelines

- Plain text; CLI handles styling. Use structure only when it helps scanability.
- Headers: optional; short Title Case (1-3 words) wrapped in **…**; no blank line before the first bullet; add only if they truly help.
- Bullets: use - ; merge related points; keep to one line when possible; 4–6 per list ordered by importance; keep phrasing consistent.
- Monospace: backticks for commands/paths/env vars/code ids and inline examples; use for literal keyword bullets; never combine with **.
- Code samples or multi-line snippets should be wrapped in fenced code blocks; include an info string as often as possible.
- Structure: group related bullets; order sections general → specific → supporting; for subsections, start with a bolded keyword bullet, then items; match complexity to the task.
- Tone: collaborative, concise, factual; present tense, active voice; self‑contained; no "above/below"; parallel wording.
- Don'ts: no nested bullets/hierarchies; no ANSI codes; don't cram unrelated keywords; keep keyword lists short—wrap/reformat if long; avoid naming formatting styles in answers.
- Adaptation: code explanations → precise, structured with code refs; simple tasks → lead with outcome; big changes → logical walkthrough + rationale + next actions; casual one-offs → plain sentences, no headers/bullets.
- File References: When referencing files in your response follow the below rules:
  * Use inline code to make file paths clickable.
  * Each reference should have a stand alone path. Even if it's the same file.
  * Accepted: absolute, workspace‑relative, a/ or b/ diff prefixes, or bare filename/suffix.
  * Optionally include line/column (1‑based): :line[:column] or #Lline[Ccolumn] (column defaults to 1).
  * Do not use URIs like file://, vscode://, or https://.
  * Do not provide range of lines
  * Examples: src/app.ts, src/app.ts:42, b/server/index.js#L10, C:\repo\project\main.rs:12:5

```

## codex-rs/core/src/harness/minimal.rs

Source: https://github.com/openinterpreter/openinterpreter/blob/a786d8474b0d7289320c16f00e852679fae7866e/codex-rs/core/src/harness/minimal.rs

SHA-256: `4e2564f16580817186e4a1915fabfb45693597bfe5c04a8f734f0f1e63cfbf40`

```
use crate::client_common::Prompt;
use crate::event_mapping::is_contextual_user_message_content;
use codex_chat_wire_compat::ToolKinds;
use codex_chat_wire_compat::ToolOutputKind;
use codex_protocol::models::ContentItem;
use codex_protocol::models::FunctionCallOutputContentItem;
use codex_protocol::models::FunctionCallOutputPayload;
use codex_protocol::models::ImageDetail;
use codex_protocol::models::ImageReference;
use codex_protocol::models::LocalShellAction;
use codex_protocol::models::ReasoningItemContent;
use codex_protocol::models::ResponseItem;
use codex_protocol::openai_models::ModelInfo;
use codex_protocol::openai_models::ReasoningControl;
use codex_protocol::openai_models::ReasoningEffort as ReasoningEffortConfig;
use codex_tools::ResponsesApiTool;
use codex_tools::ToolSpec;
use serde_json::Value;
use serde_json::json;

const MINIMAL_SYSTEM_PROMPT: &str = "You are an expert software engineer working in the user's current directory.\nUse the tools to investigate the codebase and complete the task. Read code before changing it, match existing conventions, and verify your work when possible.";

pub(crate) fn build_request(
    prompt: &Prompt,
    model_info: &ModelInfo,
    effort: Option<ReasoningEffortConfig>,
) -> Result<(Value, ToolKinds), serde_json::Error> {
    let mut messages = vec![json!({
        "role": "system",
        "content": MINIMAL_SYSTEM_PROMPT,
    })];
    messages.extend(build_messages(prompt.get_formatted_input())?);
    let tools = build_tools(&prompt.tools)?;
    let tool_kinds = prompt
        .tools
        .iter()
        .map(|tool| (tool.name().to_string(), ToolOutputKind::Function))
        .collect();

    let mut request = json!({
        "model": model_info.slug,
        "messages": messages,
        "stream": true,
        "stream_options": {
            "include_usage": true,
        },
        "tools": tools,
    });

    if model_info.reasoning_control == ReasoningControl::ThinkingToggle {
        request["thinking"] = json!({
            "type": if matches!(effort, Some(ReasoningEffortConfig::None)) {
                "disabled"
            } else {
                "enabled"
            },
        });
    }

    Ok((request, tool_kinds))
}

fn build_messages(
    items: &[ResponseItem],
) -> Result<impl Iterator<Item = Value>, serde_json::Error> {
    let mut messages = Vec::new();
    let mut pending_tool_calls = Vec::new();
    let mut pending_tool_call_content = String::new();
    let mut pending_reasoning_content = String::new();

    for item in items {
        match item {
            ResponseItem::Message { role, content, .. } => match role.as_str() {
                "assistant" => {
                    if let Some(message_content) = convert_message_content(content) {
                        if message_content.as_str().is_some_and(str::is_empty) {
                            continue;
                        }
                        if !pending_tool_calls.is_empty() {
                            append_message_text(&mut pending_tool_call_content, &message_content);
                            continue;
                        }
                        let mut message = json!({
                            "role": "assistant",
                            "content": message_content,
                        });
                        attach_pending_reasoning_content(
                            &mut message,
                            &mut pending_reasoning_content,
                        );
                        messages.push(message);
                    }
                }
                "user" => {
                    if is_contextual_user_message_content(content) {
                        continue;
                    }
                    flush_pending_tool_calls(
                        &mut messages,
                        &mut pending_tool_calls,
                        &mut pending_tool_call_content,
                        &mut pending_reasoning_content,
                    );
                    if let Some(message_content) = convert_message_content(content) {
                        messages.push(json!({
                            "role": "user",
                            "content": message_content,
                        }));
                    }
                }
                "developer" => {
                    flush_pending_tool_calls(
                        &mut messages,
                        &mut pending_tool_calls,
                        &mut pending_tool_call_content,
                        &mut pending_reasoning_content,
                    );
                    if let Some(message_content) = convert_message_content(content) {
                        messages.push(json!({
                            "role": "user",
                            "content": message_content,
                        }));
                    }
                }
                _ => {}
            },
            ResponseItem::FunctionCall {
                name,
                arguments,
                call_id,
                ..
            } => pending_tool_calls.push(json!({
                "type": "function",
                "id": call_id,
                "function": {
                    "name": name,
                    "arguments": arguments,
                }
            })),
            ResponseItem::CustomToolCall {
                call_id,
                name,
                input,
                ..
            } => pending_tool_calls.push(json!({
                "type": "function",
                "id": call_id,
                "function": {
                    "name": name,
                    "arguments": json!({ "input": input }).to_string(),
                }
            })),
            ResponseItem::LocalShellCall {
                id,
                call_id,
                action,
                ..
            } => {
                let call_id = call_id
                    .clone()
                    .or_else(|| id.as_ref().map(ToString::to_string))
                    .ok_or_else(|| {
                        serde_json::Error::io(std::io::Error::new(
                            std::io::ErrorKind::InvalidData,
                            "local_shell history item missing call id",
                        ))
                    })?;
                let arguments = match action {
                    LocalShellAction::Exec(exec) => json!({
                        "command": exec.command,
                        "timeout": exec.timeout_ms.map(|timeout_ms| timeout_ms / 1000),
                    })
                    .to_string(),
                };
                pending_tool_calls.push(json!({
                    "type": "function",
                    "id": call_id,
                    "function": {
                        "name": "bash",
                        "arguments": arguments,
                    }
                }));
            }
            ResponseItem::FunctionCallOutput {
                call_id, output, ..
            } => {
                flush_pending_tool_calls(
                    &mut messages,
                    &mut pending_tool_calls,
                    &mut pending_tool_call_content,
                    &mut pending_reasoning_content,
                );
                messages.push(json!({
                    "role": "tool",
                    "content": minimal_tool_output_content(output),
                    "tool_call_id": call_id,
                }));
            }
            ResponseItem::CustomToolCallOutput {
                call_id, output, ..
            } => {
                flush_pending_tool_calls(
                    &mut messages,
                    &mut pending_tool_calls,
                    &mut pending_tool_call_content,
                    &mut pending_reasoning_content,
                );
                messages.push(json!({
                    "role": "tool",
                    "content": minimal_tool_output_content(output),
                    "tool_call_id": call_id,
                }));
            }
            ResponseItem::Reasoning { content, .. } => {
                if let Some(content) = content {
                    for entry in content {
                        let text = match entry {
                            ReasoningItemContent::ReasoningText { text }
                            | ReasoningItemContent::Text { text } => text,
                        };
                        pending_reasoning_content.push_str(text);
                    }
                }
            }
            ResponseItem::ToolSearchCall { .. }
            | ResponseItem::ToolSearchOutput { .. }
            | ResponseItem::AgentMessage { .. }
            | ResponseItem::WebSearchCall { .. }
            | ResponseItem::ImageGenerationCall { .. }
            | ResponseItem::Compaction { .. }
            | ResponseItem::CompactionTrigger { .. }
            | ResponseItem::ContextCompaction { .. }
            | ResponseItem::AdditionalTools { .. }
            | ResponseItem::ConfigurationUpdate { .. }
            | ResponseItem::Other => {}
        }
    }

    flush_pending_tool_calls(
        &mut messages,
        &mut pending_tool_calls,
        &mut pending_tool_call_content,
        &mut pending_reasoning_content,
    );
    Ok(messages.into_iter())
}

fn build_tools(tools: &[ToolSpec]) -> Result<Vec<Value>, serde_json::Error> {
    let mut converted = Vec::new();
    for tool in tools {
        let ToolSpec::Function(ResponsesApiTool {
            name,
            description,
            parameters,
            ..
        }) = tool
        else {
            continue;
        };
        converted.push(json!({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        }));
    }
    Ok(converted)
}

fn flush_pending_tool_calls(
    messages: &mut Vec<Value>,
    pending_tool_calls: &mut Vec<Value>,
    pending_tool_call_content: &mut String,
    pending_reasoning_content: &mut String,
) {
    if pending_tool_calls.is_empty() {
        return;
    }
    let mut message = json!({
        "role": "assistant",
        "content": std::mem::take(pending_tool_call_content),
        "tool_calls": std::mem::take(pending_tool_calls),
    });
    attach_pending_reasoning_content(&mut message, pending_reasoning_content);
    messages.push(message);
}

fn attach_pending_reasoning_content(message: &mut Value, pending_reasoning_content: &mut String) {
    if pending_reasoning_content.is_empty() {
        return;
    }
    message["reasoning_content"] = json!(std::mem::take(pending_reasoning_content));
}

fn append_message_text(output: &mut String, content: &Value) {
    let text = content
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(|| content.to_string());
    if text.is_empty() {
        return;
    }
    if !output.is_empty() {
        output.push('\n');
    }
    output.push_str(&text);
}

fn convert_message_content(content: &[ContentItem]) -> Option<Value> {
    let parts = content
        .iter()
        .map(|item| match item {
            ContentItem::InputText { text } | ContentItem::OutputText { text } => {
                json!({ "type": "text", "text": text })
            }
            ContentItem::InputImage { image, detail } => chat_image_content_part(image, *detail),
            ContentItem::InputAudio { .. } => {
                json!({ "type": "text", "text": "[audio content omitted]" })
            }
        })
        .collect::<Vec<_>>();
    match parts.as_slice() {
        [] => None,
        [single]
            if single
                .get("type")
                .and_then(Value::as_str)
                .is_some_and(|kind| kind == "text") =>
        {
            single.get("text").cloned()
        }
        _ => Some(Value::Array(parts)),
    }
}

fn minimal_tool_output_content(output: &FunctionCallOutputPayload) -> Value {
    if let Some(text) = output.text_content() {
        return json!(text);
    }
    if let Some(content_items) = output.content_items() {
        let parts = content_items
            .iter()
            .map(|item| match item {
                FunctionCallOutputContentItem::InputText { text } => {
                    json!({ "type": "text", "text": text })
                }
                FunctionCallOutputContentItem::InputImage { image, detail } => {
                    chat_image_content_part(image, *detail)
                }
                FunctionCallOutputContentItem::InputAudio { .. } => json!({
                    "type": "text",
                    "text": "[audio content omitted]",
                }),
                FunctionCallOutputContentItem::InputVideo { .. } => json!({
                    "type": "text",
                    "text": "[video content omitted]",
                }),
                FunctionCallOutputContentItem::EncryptedContent { .. } => json!({
                    "type": "text",
                    "text": "[encrypted content omitted]",
                }),
            })
            .collect::<Vec<_>>();
        return match parts.as_slice() {
            [] => json!(""),
            [single]
                if single
                    .get("type")
                    .and_then(Value::as_str)
                    .is_some_and(|kind| kind == "text") =>
            {
                single.get("text").cloned().unwrap_or_else(|| json!(""))
            }
            _ => Value::Array(parts),
        };
    }
    json!(output.to_string())
}

fn chat_image_content_part(image: &ImageReference, detail: Option<ImageDetail>) -> Value {
    match image {
        ImageReference::Inline { image_url } => {
            let mut image_url_value = json!({ "url": image_url });
            if let Some(detail) = detail {
                image_url_value["detail"] = json!(detail);
            }
            json!({
                "type": "image_url",
                "image_url": image_url_value,
            })
        }
        ImageReference::File { file_id } => json!({
            "type": "file",
            "file": { "file_id": file_id },
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use codex_protocol::models::ContentItem;
    use codex_protocol::models::FunctionCallOutputBody;
    use codex_protocol::models::FunctionCallOutputContentItem;
    use codex_protocol::models::ResponseItem;
    use pretty_assertions::assert_eq;

    fn thinking_toggle_model_info() -> ModelInfo {
        serde_json::from_value(json!({
            "slug": "deepseek-v4-pro",
            "display_name": "DeepSeek V4 Pro",
            "description": "desc",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [],
            "reasoning_control": "thinking_toggle",
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": true,
            "priority": 1,
            "upgrade": null,
            "base_instructions": "ignored",
            "model_messages": null,
            "supports_reasoning_summaries": false,
            "support_verbosity": false,
            "default_verbosity": null,
            "apply_patch_tool_type": null,
            "truncation_policy": {"mode": "bytes", "limit": 10000},
            "supports_parallel_tool_calls": true,
            "supports_image_detail_original": false,
            "context_window": 1000000,
            "auto_compact_token_limit": null,
            "experimental_supported_tools": []
        }))
        .expect("deserialize model info")
    }

    fn vision_thinking_toggle_model_info() -> ModelInfo {
        serde_json::from_value(json!({
            "slug": "deepseek-vl-test",
            "display_name": "DeepSeek VL Test",
            "description": "desc",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [],
            "reasoning_control": "thinking_toggle",
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": true,
            "priority": 1,
            "upgrade": null,
            "base_instructions": "ignored",
            "model_messages": null,
            "supports_reasoning_summaries": false,
            "support_verbosity": false,
            "default_verbosity": null,
            "apply_patch_tool_type": null,
            "truncation_policy": {"mode": "bytes", "limit": 10000},
            "supports_parallel_tool_calls": true,
            "supports_image_detail_original": false,
            "context_window": 1000000,
            "auto_compact_token_limit": null,
            "experimental_supported_tools": [],
            "input_modalities": ["text", "image"]
        }))
        .expect("deserialize model info")
    }

    fn test_prompt() -> Prompt {
        Prompt {
            input: vec![ResponseItem::Message {
                id: Some(codex_protocol::ResponseItemId::from_server(
                    "user".to_string(),
                )),
                role: "user".to_string(),
                content: vec![ContentItem::InputText {
                    text: "hello".to_string(),
                }],
                phase: None,

                internal_chat_message_metadata_passthrough: None,
            }],
            cwd: Some(std::env::temp_dir()),
            ..Prompt::default()
        }
    }

    #[test]
    fn thinking_toggle_model_sends_enabled_by_default() {
        let (request, _) = build_request(
            &test_prompt(),
            &thinking_toggle_model_info(),
            /*effort*/ None,
        )
        .expect("build request");

        assert_eq!(request.get("thinking"), Some(&json!({"type": "enabled"})));
    }

    #[test]
    fn thinking_toggle_model_sends_disabled_for_none_effort() {
        let (request, _) = build_request(
            &test_prompt(),
            &thinking_toggle_model_info(),
            Some(ReasoningEffortConfig::None),
        )
        .expect("build request");

        assert_eq!(request.get("thinking"), Some(&json!({"type": "disabled"})));
    }

    #[test]
    fn developer_messages_are_preserved_as_user_messages() {
        let prompt = Prompt {
            input: vec![
                ResponseItem::Message {
                    id: Some(codex_protocol::ResponseItemId::from_server(
                        "developer".to_string(),
                    )),
                    role: "developer".to_string(),
                    content: vec![ContentItem::InputText {
                        text: "<skills_instructions>\n- imagegen\n</skills_instructions>"
                            .to_string(),
                    }],
                    phase: None,

                    internal_chat_message_metadata_passthrough: None,
                },
                ResponseItem::Message {
                    id: Some(codex_protocol::ResponseItemId::from_server(
                        "user".to_string(),
                    )),
                    role: "user".to_string(),
                    content: vec![ContentItem::InputText {
                        text: "$imagegen what is this".to_string(),
                    }],
                    phase: None,

                    internal_chat_message_metadata_passthrough: None,
                },
            ],
            cwd: Some(std::env::temp_dir()),
            ..Prompt::default()
        };

        let (request, _) =
            build_request(&prompt, &thinking_toggle_model_info(), /*effort*/ None)
                .expect("build request");
        let messages = request
            .get("messages")
            .and_then(Value::as_array)
            .expect("messages");

        assert_eq!(messages[1]["role"], json!("user"));
        assert_eq!(
            messages[1]["content"],
            json!("<skills_instructions>\n- imagegen\n</skills_instructions>")
        );
        assert_eq!(messages[2]["role"], json!("user"));
        assert_eq!(messages[2]["content"], json!("$imagegen what is this"));
    }

    #[test]
    fn reasoning_content_is_passed_back_on_assistant_tool_call_messages() {
        let prompt = Prompt {
            input: vec![
                ResponseItem::Message {
                    id: Some(codex_protocol::ResponseItemId::from_server(
                        "user".to_string(),
                    )),
                    role: "user".to_string(),
                    content: vec![ContentItem::InputText {
                        text: "run date".to_string(),
                    }],
                    phase: None,

                    internal_chat_message_metadata_passthrough: None,
                },
                ResponseItem::Reasoning {
                    id: Some(codex_protocol::ResponseItemId::from_server(
                        "reasoning".to_string(),
                    )),
                    summary: vec![],
                    content: Some(vec![ReasoningItemContent::ReasoningText {
                        text: "I should inspect the clock.".to_string(),
                    }]),
                    encrypted_content: None,

                    internal_chat_message_metadata_passthrough: None,
                },
                ResponseItem::FunctionCall {
                    id: None,
                    name: "bash".to_string(),
                    namespace: None,
                    arguments: json!({"command": "date"}).to_string(),
                    call_id: "call-date".to_string(),

                    encrypted_function_args: None,

                    internal_chat_message_metadata_passthrough: None,
                },
                ResponseItem::FunctionCallOutput {
                    id: None,
                    call_id: Some("call-date".to_string()),
                    name: None,
                    namespace: None,
                    output: FunctionCallOutputPayload::from_text("Tue Apr 29".to_string()),

                    internal_chat_message_metadata_passthrough: None,
                },
                ResponseItem::Message {
                    id: Some(codex_protocol::ResponseItemId::from_server(
                        "user2".to_string(),
                    )),
                    role: "user".to_string(),
                    content: vec![ContentItem::InputText {
                        text: "what did you run?".to_string(),
                    }],
                    phase: None,

                    internal_chat_message_metadata_passthrough: None,
                },
            ],
            cwd: Some(std::env::temp_dir()),
            ..Prompt::default()
        };

        let (request, _) =
            build_request(&prompt, &thinking_toggle_model_info(), /*effort*/ None)
                .expect("build request");
        let messages = request
            .get("messages")
            .and_then(Value::as_array)
            .expect("messages");

        assert_eq!(messages[2]["role"], json!("assistant"));
        assert_eq!(
            messages[2]["reasoning_content"],
            json!("I should inspect the clock.")
        );
        assert_eq!(
            messages[2]["tool_calls"][0]["function"]["name"],
            json!("bash")
        );
    }

    #[test]
    fn image_content_is_preserved_for_vision_models() {
        let prompt = Prompt {
            input: vec![ResponseItem::Message {
                id: Some(codex_protocol::ResponseItemId::from_server(
                    "user".to_string(),
                )),
                role: "user".to_string(),
                content: vec![
                    ContentItem::InputText {
                        text: "describe this".to_string(),
                    },
                    ContentItem::InputImage {
                        image: ImageReference::Inline {
                            image_url: "data:image/png;base64,AAA".to_string(),
                        },
                        detail: Some(ImageDetail::High),
                    },
                ],
                phase: None,

                internal_chat_message_metadata_passthrough: None,
            }],
            cwd: Some(std::env::temp_dir()),
            ..Prompt::default()
        };

        let (request, _) = build_request(
            &prompt,
            &vision_thinking_toggle_model_info(),
            /*effort*/ None,
        )
        .expect("build request");
        let content = &request["messages"][1]["content"];

        assert_eq!(
            content,
            &json!([
                {"type": "text", "text": "describe this"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,AAA",
                        "detail": "high"
                    }
                }
            ])
        );
    }

    #[test]
    fn view_image_tool_output_preserves_image_payload() {
        let output = FunctionCallOutputPayload {
            body: FunctionCallOutputBody::ContentItems(vec![
                FunctionCallOutputContentItem::InputImage {
                    image: ImageReference::Inline {
                        image_url: "data:image/png;base64,BBB".to_string(),
                    },
                    detail: Some(ImageDetail::High),
                },
            ]),
            success: Some(true),
        };

        assert_eq!(
            minimal_tool_output_content(&output),
            json!([{
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,BBB",
                    "detail": "high"
                }
            }])
        );
    }

    #[test]
    fn view_image_tool_output_preserves_file_id() {
        let output = FunctionCallOutputPayload {
            body: FunctionCallOutputBody::ContentItems(vec![
                FunctionCallOutputContentItem::InputImage {
                    image: ImageReference::File {
                        file_id: "file_123".to_string(),
                    },
                    detail: None,
                },
            ]),
            success: Some(true),
        };

        assert_eq!(
            minimal_tool_output_content(&output),
            json!([{
                "type": "file",
                "file": { "file_id": "file_123" },
            }])
        );
    }
}

```
