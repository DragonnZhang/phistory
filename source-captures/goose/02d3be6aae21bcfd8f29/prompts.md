# goose — System prompt source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source templates and assembly code; runtime substitutions and optional modes are not resolved.

## crates/goose/src/agents/platform_extensions/developer/mod.rs

Source: https://github.com/aaif-goose/goose/blob/bfe8ccd23de1d2c47a4cb2970d522f093ae7f2ba/crates/goose/src/agents/platform_extensions/developer/mod.rs

SHA-256: `d08a26e29ef052f1a07474008446dc1ec1b8fd7a83596a6e2d934d1ff019c542`

```
mod edit;
pub mod image;
pub mod shell;
mod shell_output_streaming;
mod shell_process;
pub mod tree;

use crate::agents::mcp_client::{Error, McpClientTrait};
use crate::agents::ToolCallContext;
use anyhow::Result;
use async_trait::async_trait;
use edit::{EditTools, FileEditParams, FileWriteParams};
use image::{ImageReadParams, ImageTool};
use indoc::indoc;
use rmcp::model::{
    Annotations, CallToolResult, ContentBlock, Implementation, InitializeResult, JsonObject,
    ListToolsResult, ServerCapabilities, TextContent, Tool, ToolAnnotations,
};
use schemars::{schema_for, JsonSchema};
use serde_json::Value;
use shell::{shell_display_name, ShellOutput, ShellParams, ShellTool};
use std::sync::Arc;
use tokio_util::sync::CancellationToken;
use tree::{TreeParams, TreeTool};

pub static EXTENSION_NAME: &str = "developer";

fn visible_text(text: impl Into<String>) -> ContentBlock {
    ContentBlock::Text(
        TextContent::new(text).with_annotations(Annotations::default().with_priority(0.0)),
    )
}

pub struct DeveloperClient {
    info: InitializeResult,
    shell_tool: Arc<ShellTool>,
    edit_tools: Arc<EditTools>,
    tree_tool: Arc<TreeTool>,
    image_tool: Arc<ImageTool>,
}

fn developer_instructions() -> &'static str {
    if cfg!(windows) {
        indoc! {"
            Use the developer extension to build software and operate a terminal.

            Make sure to use the tools *efficiently* - reading all the content you need in as few
            iterations as possible and then making the requested edits or running commands. You are
            responsible for managing your context window, and to minimize unnecessary turns which
            cost the user money.

            For editing software, prefer the flow of using tree to understand the codebase structure
            and file sizes. When you need to search, prefer findstr or Select-String (via shell).
            Then use type or Get-Content to gather the context you need, always reading before
            editing. Use write and edit to efficiently make changes. Test and verify as appropriate.
        "}
    } else {
        indoc! {"
            Use the developer extension to build software and operate a terminal.

            Make sure to use the tools *efficiently* - reading all the content you need in as few
            iterations as possible and then making the requested edits or running commands. You are
            responsible for managing your context window, and to minimize unnecessary turns which
            cost the user money.

            For editing software, prefer the flow of using tree to understand the codebase structure
            and file sizes. When you need to search, prefer rg which correctly respects gitignored
            content. Then use cat or sed to gather the context you need, always reading before editing.
            Use write and edit to efficiently make changes. Test and verify as appropriate.

            When running Python scripts or commands, always use `python3` instead of `python`.
        "}
    }
}

impl DeveloperClient {
    pub fn new() -> Result<Self> {
        let info = InitializeResult::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new(EXTENSION_NAME, "1.0.0").with_title("Developer"))
            .with_instructions(developer_instructions());

        Ok(Self {
            info,
            shell_tool: Arc::new(ShellTool::new()?),
            edit_tools: Arc::new(EditTools::new()),
            tree_tool: Arc::new(TreeTool::new()),
            image_tool: Arc::new(ImageTool::new()),
        })
    }

    fn schema<T: JsonSchema>() -> JsonObject {
        serde_json::to_value(schema_for!(T))
            .expect("schema serialization should succeed")
            .as_object()
            .expect("schema should serialize to an object")
            .clone()
    }

    fn parse_args<T: serde::de::DeserializeOwned>(
        arguments: Option<JsonObject>,
    ) -> Result<T, String> {
        let value = arguments
            .map(Value::Object)
            .ok_or_else(|| "Missing arguments".to_string())?;
        serde_json::from_value(value).map_err(|e| format!("Failed to parse arguments: {e}"))
    }

    pub(crate) fn get_tools() -> Vec<Tool> {
        vec![
            Tool::new(
                "write".to_string(),
                "Create a new file or overwrite an existing file. Creates parent directories if needed.".to_string(),
                Self::schema::<FileWriteParams>(),
            )
            .annotate(ToolAnnotations::from_raw(
                Some("Write".to_string()),
                Some(false),
                Some(true),
                Some(false),
                Some(false),
            )),
            Tool::new(
                "edit".to_string(),
                "Edit a file by finding and replacing text. The before text must match exactly and uniquely. Use empty after text to delete.".to_string(),
                Self::schema::<FileEditParams>(),
            )
            .annotate(ToolAnnotations::from_raw(
                Some("Edit".to_string()),
                Some(false),
                Some(true),
                Some(false),
                Some(false),
            )),
            {
                let shell = shell_display_name();
                let newline_note = if shell == "cmd" {
                    " Commands must be on a single line — cmd.exe silently truncates at the \
                     first newline. Use `&` to chain (e.g. `echo a & echo b`) or set \
                     GOOSE_SHELL=powershell for multi-line support."
                } else {
                    ""
                };
                let description = format!(
                    "Execute a shell command in the current dir. Commands run under `{shell}` \
                     (set GOOSE_SHELL to override) - write command strings in that shell's \
                     syntax.{newline_note} Returns an object with stdout and stderr as separate \
                     fields. The output of each stream is limited to up to 2000 lines, and \
                     longer outputs will be saved to a temporary file.",
                );
                Tool::new("shell".to_string(), description, Self::schema::<ShellParams>())
            }
            .with_output_schema::<ShellOutput>()
            .annotate(ToolAnnotations::from_raw(
                Some("Shell".to_string()),
                Some(false),
                Some(true),
                Some(false),
                Some(true),
            )),
            Tool::new(
                "tree".to_string(),
                "List a directory tree with line counts. Traversal respects .gitignore rules.".to_string(),
                Self::schema::<TreeParams>(),
            )
            .annotate(ToolAnnotations::from_raw(
                Some("Tree".to_string()),
                Some(true),
                Some(false),
                Some(true),
                Some(false),
            )),
            Tool::new(
                "read_image".to_string(),
                "Read an image from a local file path or http(s) URL and return it as image content for the model to inspect. Supports png, jpeg, gif, and webp.".to_string(),
                Self::schema::<ImageReadParams>(),
            )
            .annotate(ToolAnnotations::from_raw(
                Some("Read Image".to_string()),
                Some(false),
                Some(false),
                Some(true),
                Some(true),
            )),
        ]
    }
}

#[async_trait]
impl McpClientTrait for DeveloperClient {
    async fn list_tools(
        &self,
        _session_id: &str,
        _next_cursor: Option<String>,
        _cancellation_token: CancellationToken,
    ) -> Result<ListToolsResult, Error> {
        Ok(ListToolsResult {
            tools: Self::get_tools(),
            next_cursor: None,
            meta: None,
            ..Default::default()
        })
    }

    async fn call_tool(
        &self,
        ctx: &ToolCallContext,
        name: &str,
        arguments: Option<JsonObject>,
        cancel_token: CancellationToken,
    ) -> Result<CallToolResult, Error> {
        let working_dir = ctx.working_dir.as_deref();
        match name {
            "shell" => match Self::parse_args::<ShellParams>(arguments) {
                Ok(params) => Ok(self
                    .shell_tool
                    .shell_with_cwd_and_emitter(
                        params,
                        working_dir,
                        Some(&ctx.session_id),
                        ctx.notification_emitter().cloned(),
                        cancel_token,
                    )
                    .await),
                Err(error) => Ok(ShellTool::error_result(&format!("Error: {error}"), None)),
            },
            "write" => match Self::parse_args::<FileWriteParams>(arguments) {
                Ok(params) => Ok(self.edit_tools.file_write_with_cwd(params, working_dir)),
                Err(error) => Ok(CallToolResult::error(vec![visible_text(format!(
                    "Error: {error}"
                ))])),
            },
            "edit" => match Self::parse_args::<FileEditParams>(arguments) {
                Ok(params) => Ok(self.edit_tools.file_edit_with_cwd(params, working_dir)),
                Err(error) => Ok(CallToolResult::error(vec![visible_text(format!(
                    "Error: {error}"
                ))])),
            },
            "tree" => match Self::parse_args::<TreeParams>(arguments) {
                Ok(params) => Ok(self.tree_tool.tree_with_cwd(params, working_dir)),
                Err(error) => Ok(CallToolResult::error(vec![visible_text(format!(
                    "Error: {error}"
                ))])),
            },
            "read_image" => match Self::parse_args::<ImageReadParams>(arguments) {
                Ok(params) => Ok(self
                    .image_tool
                    .image_read_with_cwd(params, working_dir)
                    .await),
                Err(error) => Ok(CallToolResult::error(vec![visible_text(format!(
                    "Error: {error}"
                ))])),
            },
            _ => Ok(CallToolResult::error(vec![visible_text(format!(
                "Error: Unknown tool: {name}"
            ))])),
        }
    }

    fn get_info(&self) -> Option<&InitializeResult> {
        Some(&self.info)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rmcp::model::ContentBlock;
    use rmcp::object;
    use std::fs;

    #[test]
    fn developer_tools_are_flat() {
        let names: Vec<String> = DeveloperClient::get_tools()
            .into_iter()
            .map(|t| t.name.to_string())
            .collect();

        assert_eq!(names, vec!["write", "edit", "shell", "tree", "read_image"]);
    }

    #[test]
    fn read_image_annotations_reflect_network_access() {
        let read_image = DeveloperClient::get_tools()
            .into_iter()
            .find(|tool| tool.name == "read_image")
            .unwrap();
        let annotations = read_image.annotations.unwrap();

        assert_eq!(annotations.read_only_hint, Some(false));
        assert_eq!(annotations.open_world_hint, Some(true));
    }

    fn first_text(result: &CallToolResult) -> &str {
        match &result.content[0] {
            ContentBlock::Text(text) => &text.text,
            _ => panic!("expected text content"),
        }
    }

    #[tokio::test]
    async fn developer_client_uses_working_dir_for_file_tools() {
        let temp = tempfile::tempdir().unwrap();
        let client = DeveloperClient::new().unwrap();
        let cwd = temp.path().join("workspace");
        fs::create_dir_all(&cwd).unwrap();

        let ctx = ToolCallContext::new("session".to_owned(), Some(cwd.clone()), None);
        let write = client
            .call_tool(
                &ctx,
                "write",
                Some(object!({
                    "path": "notes.txt",
                    "content": "first line"
                })),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(write.is_error, Some(false));
        assert_eq!(
            fs::read_to_string(cwd.join("notes.txt")).unwrap(),
            "first line"
        );

        let edit = client
            .call_tool(
                &ctx,
                "edit",
                Some(object!({
                    "path": "notes.txt",
                    "before": "first",
                    "after": "updated"
                })),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(edit.is_error, Some(false));
        assert_eq!(
            fs::read_to_string(cwd.join("notes.txt")).unwrap(),
            "updated line"
        );
    }

    #[cfg(not(windows))]
    #[tokio::test]
    async fn developer_client_passes_session_id_to_shell_tool() {
        let client = DeveloperClient::new().unwrap();
        let ctx = ToolCallContext::new("session-789".to_owned(), None, None);

        let result = client
            .call_tool(
                &ctx,
                "shell",
                Some(object!({
                    "command": "printenv AGENT_SESSION_ID"
                })),
                CancellationToken::new(),
            )
            .await
            .unwrap();

        assert_eq!(result.is_error, Some(false));
        assert_eq!(first_text(&result), "session-789");
    }

    #[cfg(not(windows))]
    #[tokio::test]
    async fn developer_client_uses_working_dir_for_shell_tool() {
        let temp = tempfile::tempdir().unwrap();
        let client = DeveloperClient::new().unwrap();
        let cwd = temp.path().join("workspace");
        fs::create_dir_all(&cwd).unwrap();

        let ctx = ToolCallContext::new("session".to_owned(), Some(cwd.clone()), None);
        let result = client
            .call_tool(
                &ctx,
                "shell",
                Some(object!({
                    "command": "pwd"
                })),
                CancellationToken::new(),
            )
            .await
            .unwrap();
        assert_eq!(result.is_error, Some(false));
        let observed = std::fs::canonicalize(first_text(&result)).unwrap();
        let expected = std::fs::canonicalize(&cwd).unwrap();
        assert_eq!(observed, expected);
    }
}

```

## crates/goose/src/agents/prompt_manager.rs

Source: https://github.com/aaif-goose/goose/blob/bfe8ccd23de1d2c47a4cb2970d522f093ae7f2ba/crates/goose/src/agents/prompt_manager.rs

SHA-256: `42cace99f2036579b023b71c24099fbdcc0fcfa81f246a643c3b8de6cfb9afe0`

```
#[cfg(test)]
use chrono::DateTime;
use chrono::Utc;
use indexmap::IndexMap;
use serde::Serialize;

use crate::agents::{extension::ExtensionInfo, moim};
use crate::hints::load_hints::build_gitignore;
use crate::hints::{get_context_filenames, load_hint_files};
use crate::session::Session;
use crate::{
    config::{Config, GooseMode},
    prompt_template,
    utils::sanitize_unicode_tags,
};
use std::path::Path;

pub struct PromptManager {
    current_date_timestamp: String,
}

impl Default for PromptManager {
    fn default() -> Self {
        PromptManager::new()
    }
}

#[derive(Serialize)]
struct SystemPromptContext {
    extensions: Vec<ExtensionInfo>,
    current_date_time: String,
    goose_mode: GooseMode,
    is_autonomous: bool,
    code_execution_mode: bool,
    include_extensions: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    moim_system_prompt_block: Option<String>,
}

pub struct SystemPromptBuilder<'a, M> {
    manager: &'a M,

    system_prompt_override: Option<String>,
    extensions_info: Vec<ExtensionInfo>,
    prompt_extras: IndexMap<String, String>,
    hints: Option<String>,
    code_execution_mode: bool,
    include_extensions: bool,
    goose_mode: Option<GooseMode>,
}

impl<'a> SystemPromptBuilder<'a, PromptManager> {
    pub fn with_session(mut self, session: &Session) -> Self {
        self.system_prompt_override = session.system_prompt_override.clone();
        self.prompt_extras
            .extend(session.system_prompt_extras.clone());
        self
    }

    pub fn with_extension(mut self, extension: ExtensionInfo) -> Self {
        self.extensions_info.push(extension);
        self
    }

    pub fn with_extensions(mut self, extensions: impl Iterator<Item = ExtensionInfo>) -> Self {
        for extension in extensions {
            self.extensions_info.push(extension);
        }
        self
    }

    pub fn with_prompt_extras(
        mut self,
        extras: impl IntoIterator<Item = (String, String)>,
    ) -> Self {
        self.prompt_extras.extend(extras);
        self
    }

    pub fn with_code_execution_mode(mut self, enabled: bool) -> Self {
        self.code_execution_mode = enabled;
        self
    }

    pub fn without_extensions(mut self) -> Self {
        self.include_extensions = false;
        self
    }

    pub fn with_hints(mut self, working_dir: &Path) -> Self {
        let hints_filenames = get_context_filenames();
        let ignore_patterns = build_gitignore(working_dir);

        let hints = load_hint_files(working_dir, &hints_filenames, &ignore_patterns);

        if !hints.is_empty() {
            self.hints = Some(hints);
        }
        self
    }

    pub fn with_goose_mode(mut self, mode: GooseMode) -> Self {
        self.goose_mode = Some(mode);
        self
    }

    pub fn build(self) -> anyhow::Result<String> {
        Ok(self.build_parts()?.join())
    }

    pub fn build_parts(self) -> anyhow::Result<SystemPromptParts> {
        let mut extensions_info = self.extensions_info;

        // Stable tool ordering is important for multi session prompt caching.
        extensions_info.sort_by(|a, b| a.name.cmp(&b.name));

        let sanitized_extensions_info: Vec<ExtensionInfo> = extensions_info
            .into_iter()
            .map(|mut ext_info| {
                ext_info.instructions = sanitize_unicode_tags(&ext_info.instructions);
                ext_info
            })
            .collect();
        let goose_mode = self
            .goose_mode
            .unwrap_or_else(|| Config::global().get_goose_mode().unwrap_or_default());

        let context = SystemPromptContext {
            extensions: sanitized_extensions_info,
            current_date_time: self.manager.current_date_timestamp.clone(),
            goose_mode,
            is_autonomous: goose_mode == GooseMode::Auto,
            code_execution_mode: self.code_execution_mode,
            include_extensions: self.include_extensions,
            moim_system_prompt_block: moim::system_prompt_block(),
        };

        let base_is_override = self.system_prompt_override.is_some();
        let rendered = if let Some(override_prompt) = &self.system_prompt_override {
            let sanitized_override_prompt = sanitize_unicode_tags(override_prompt);
            prompt_template::render_string(&sanitized_override_prompt, &context)
        } else {
            prompt_template::render_template("system.md", &context)
        }
        .map_err(|error| anyhow::anyhow!("Could not render system prompt: {error}"))?;

        let mut extras = self.prompt_extras;

        if let Some(hints) = self.hints {
            extras.insert("hints".to_string(), hints);
        }

        if goose_mode == GooseMode::Chat {
            extras.insert(
                "chat_mode".to_string(),
                "Right now you are in the chat only mode, no access to any tool use and system."
                    .to_string(),
            );
        }

        let extras = extras
            .into_iter()
            .map(|(key, extra)| (key, sanitize_unicode_tags(&extra)))
            .collect();

        Ok(SystemPromptParts {
            base: rendered.clone(),
            rendered,
            base_is_override,
            extras,
        })
    }
}

pub struct SystemPromptParts {
    rendered: String,
    pub base: String,
    pub base_is_override: bool,
    pub extras: IndexMap<String, String>,
}

impl SystemPromptParts {
    pub fn join(&self) -> String {
        if self.extras.is_empty() {
            return self.rendered.clone();
        }
        format!(
            "{}\n\n# Additional Instructions:\n\n{}",
            self.rendered,
            self.extras
                .values()
                .cloned()
                .collect::<Vec<_>>()
                .join("\n\n")
        )
    }
}

impl PromptManager {
    pub fn new() -> Self {
        PromptManager {
            // Use the fixed current date time so that prompt cache can be used.
            // Filtering to an hour to balance user time accuracy and multi session prompt cache hits.
            current_date_timestamp: Utc::now().format("%Y-%m-%d %H:00 %:z").to_string(),
        }
    }

    #[cfg(test)]
    pub fn with_timestamp(dt: DateTime<Utc>) -> Self {
        PromptManager {
            current_date_timestamp: dt.format("%Y-%m-%d %H:%M:%S %:z").to_string(),
        }
    }

    pub fn build_system_prompt(
        &self,
        session: &Session,
        prompt_parts: Vec<(String, String)>,
        goose_mode: GooseMode,
    ) -> anyhow::Result<String> {
        Ok(self
            .build_system_prompt_parts(session, prompt_parts, goose_mode)?
            .join())
    }

    pub fn build_system_prompt_parts(
        &self,
        session: &Session,
        prompt_parts: Vec<(String, String)>,
        goose_mode: GooseMode,
    ) -> anyhow::Result<SystemPromptParts> {
        self.builder()
            .with_session(session)
            .with_prompt_extras(prompt_parts)
            .with_hints(&session.working_dir)
            .with_goose_mode(goose_mode)
            .without_extensions()
            .build_parts()
    }

    pub fn builder<'a>(&'a self) -> SystemPromptBuilder<'a, Self> {
        SystemPromptBuilder {
            manager: self,

            system_prompt_override: None,
            extensions_info: vec![],
            prompt_extras: IndexMap::new(),
            hints: None,
            code_execution_mode: false,
            include_extensions: true,
            goose_mode: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use insta::assert_snapshot;

    use super::*;

    fn session_with_override(system_prompt_override: &str) -> Session {
        Session {
            system_prompt_override: Some(system_prompt_override.to_string()),
            ..Session::default()
        }
    }

    #[test]
    fn test_build_system_prompt_sanitizes_override() {
        let manager = PromptManager::new();
        let session =
            session_with_override("System prompt\u{E0041}\u{E0042}\u{E0043}with hidden text");

        let result = manager.builder().with_session(&session).build().unwrap();

        assert!(!result.contains('\u{E0041}'));
        assert!(!result.contains('\u{E0042}'));
        assert!(!result.contains('\u{E0043}'));
        assert!(result.contains("System prompt"));
        assert!(result.contains("with hidden text"));
    }

    #[test]
    fn test_current_date_time_includes_timezone() {
        let manager = PromptManager::with_timestamp(DateTime::<Utc>::from_timestamp(0, 0).unwrap());
        let session = session_with_override("It is currently {{current_date_time}}");

        let result = manager.builder().with_session(&session).build().unwrap();

        assert_eq!(result, "It is currently 1970-01-01 00:00:00 +00:00");
    }

    #[test]
    fn test_build_system_prompt_sanitizes_extras() {
        let malicious_extra = "Extra instruction\u{E0041}\u{E0042}\u{E0043}hidden";

        let result = PromptManager::new()
            .builder()
            .with_prompt_extras([("test".to_string(), malicious_extra.to_string())])
            .build()
            .unwrap();

        assert!(!result.contains('\u{E0041}'));
        assert!(!result.contains('\u{E0042}'));
        assert!(!result.contains('\u{E0043}'));
        assert!(result.contains("Extra instruction"));
        assert!(result.contains("hidden"));
    }

    #[test]
    fn build_parts_preserves_prompt_contribution_keys() {
        let manager = PromptManager::new();
        let parts = manager
            .builder()
            .with_prompt_extras([
                ("recipe".to_string(), "Follow the recipe.".to_string()),
                ("project".to_string(), "Project instructions.".to_string()),
            ])
            .build_parts()
            .unwrap();

        assert_eq!(
            parts.extras.keys().map(String::as_str).collect::<Vec<_>>(),
            ["recipe", "project"]
        );
        assert!(parts.join().contains("Follow the recipe."));
        assert!(parts.join().contains("Project instructions."));
    }

    #[test]
    fn prompt_contributions_are_not_retained() {
        let manager = PromptManager::new();

        let with_contribution = manager
            .builder()
            .with_prompt_extras([("operation".to_string(), "temporary instruction".to_string())])
            .build()
            .unwrap();
        let without_contribution = manager.builder().build().unwrap();

        assert!(with_contribution.contains("temporary instruction"));
        assert!(!without_contribution.contains("temporary instruction"));
    }

    #[test]
    fn composed_prompt_uses_contributions_instead_of_the_extension_catalog() {
        let manager = PromptManager::new();
        let working_dir = tempfile::tempdir().unwrap();

        let session = Session {
            working_dir: working_dir.path().to_path_buf(),
            ..Session::default()
        };

        let prompt = manager
            .build_system_prompt(
                &session,
                vec![(
                    "extensions".to_string(),
                    "# Extensions\n\n## developer".to_string(),
                )],
                GooseMode::Auto,
            )
            .unwrap();

        assert!(prompt.contains("## developer"));
        assert!(!prompt.contains("No extensions are defined"));
    }

    #[test]
    fn project_git_metadata_does_not_reach_system_prompt() {
        let project = tempfile::tempdir().unwrap();
        std::fs::create_dir(project.path().join(".git")).unwrap();
        std::fs::create_dir(project.path().join("docs")).unwrap();
        std::fs::write(
            project.path().join(".git/config"),
            "url = https://oauth2:PROMPT_SECRET@example.invalid/repo.git",
        )
        .unwrap();
        std::fs::write(
            project.path().join("docs/config.md"),
            "legitimate project configuration",
        )
        .unwrap();
        std::fs::write(
            project.path().join(crate::hints::AGENTS_MD_FILENAME),
            "project instructions\n@.git/config\n@docs/config.md",
        )
        .unwrap();
        let ignore_patterns = build_gitignore(project.path());
        let hints = load_hint_files(
            project.path(),
            &[crate::hints::AGENTS_MD_FILENAME.to_string()],
            &ignore_patterns,
        );

        let prompt = PromptManager::new()
            .builder()
            .with_prompt_extras([("hints".to_string(), hints)])
            .build()
            .unwrap();

        assert!(prompt.contains("project instructions"));
        assert!(prompt.contains("legitimate project configuration"));
        assert!(!prompt.contains("PROMPT_SECRET"));
    }

    #[test]
    fn test_build_system_prompt_sanitizes_multiple_extras() {
        let result = PromptManager::new()
            .builder()
            .with_prompt_extras([
                ("test1".to_string(), "First\u{E0041}instruction".to_string()),
                (
                    "test2".to_string(),
                    "Second\u{E0042}instruction".to_string(),
                ),
                ("test3".to_string(), "Third\u{E0043}instruction".to_string()),
            ])
            .build()
            .unwrap();

        assert!(!result.contains('\u{E0041}'));
        assert!(!result.contains('\u{E0042}'));
        assert!(!result.contains('\u{E0043}'));
        assert!(result.contains("Firstinstruction"));
        assert!(result.contains("Secondinstruction"));
        assert!(result.contains("Thirdinstruction"));
    }

    #[test]
    fn test_build_system_prompt_preserves_legitimate_unicode_in_extras() {
        let legitimate_unicode = "Instruction with 世界 and 🌍 emojis";

        let result = PromptManager::new()
            .builder()
            .with_prompt_extras([("test".to_string(), legitimate_unicode.to_string())])
            .build()
            .unwrap();

        assert!(result.contains("世界"));
        assert!(result.contains("🌍"));
        assert!(result.contains("Instruction with"));
        assert!(result.contains("emojis"));
    }

    #[test]
    fn test_build_system_prompt_sanitizes_extension_instructions() {
        let manager = PromptManager::new();
        let malicious_extension_info = ExtensionInfo::new(
            "test_extension",
            "Extension help\u{E0041}\u{E0042}\u{E0043}hidden instructions",
            false,
        );

        let result = manager
            .builder()
            .with_extension(malicious_extension_info)
            .build()
            .unwrap();

        assert!(!result.contains('\u{E0041}'));
        assert!(!result.contains('\u{E0042}'));
        assert!(!result.contains('\u{E0043}'));
        assert!(result.contains("Extension help"));
        assert!(result.contains("hidden instructions"));
    }

    #[test]
    fn test_invalid_override_prompt_returns_error() {
        let manager = PromptManager::new();
        let session = session_with_override("{{ unclosed");

        let result = manager.builder().with_session(&session).build();

        assert!(result.is_err());
    }

    #[test]
    fn test_basic() {
        let manager = PromptManager::with_timestamp(DateTime::<Utc>::from_timestamp(0, 0).unwrap());

        let system_prompt = manager.builder().build().unwrap();

        assert_snapshot!(system_prompt)
    }

    #[test]
    fn test_one_extension() {
        let manager = PromptManager::with_timestamp(DateTime::<Utc>::from_timestamp(0, 0).unwrap());

        let system_prompt = manager
            .builder()
            .with_extension(ExtensionInfo::new(
                "test",
                "how to use this extension",
                true,
            ))
            .build()
            .unwrap();

        assert_snapshot!(system_prompt)
    }

    #[test]
    fn test_typical_setup() {
        let manager = PromptManager::with_timestamp(DateTime::<Utc>::from_timestamp(0, 0).unwrap());

        let system_prompt = manager
            .builder()
            .with_extension(ExtensionInfo::new(
                "extension_A",
                "<instructions on how to use extension A>",
                true,
            ))
            .with_extension(ExtensionInfo::new(
                "extension_B",
                "<instructions on how to use extension B (no resources)>",
                false,
            ))
            .build()
            .unwrap();

        assert_snapshot!(system_prompt)
    }

    // The snapshot lists every platform extension, so it needs every feature that adds one.
    #[cfg(all(
        feature = "chat-recall",
        feature = "code-mode",
        feature = "platform-apps",
        feature = "scheduler",
        feature = "tree-sitter"
    ))]
    #[tokio::test]
    async fn test_all_platform_extensions() {
        use crate::agents::platform_extensions::{PlatformExtensionContext, PLATFORM_EXTENSIONS};
        use crate::config::GooseMode;
        use crate::session::SessionManager;
        use std::sync::Arc;

        let tmp_dir = tempfile::tempdir().unwrap();
        let temp_root = tmp_dir.path().display().to_string();
        let _guard = env_lock::lock_env([
            ("HOME", Some(temp_root.as_str())),
            ("GOOSE_PATH_ROOT", Some(temp_root.as_str())),
        ]);
        let session_manager = Arc::new(SessionManager::new(tmp_dir.path().to_path_buf()));
        let session = session_manager
            .create_session(
                tmp_dir.path().to_path_buf(),
                "test session".to_owned(),
                crate::session::SessionType::Hidden,
                GooseMode::default(),
            )
            .await
            .unwrap();
        let scheduler = crate::scheduler::Scheduler::new(
            tmp_dir.path().join("schedules"),
            session_manager.clone(),
        )
        .await
        .unwrap();
        let context = PlatformExtensionContext {
            extension_manager: None,
            providers: Default::default(),
            session_manager,
            scheduler: Some(scheduler),
        };

        let mut extensions = Vec::new();
        for def in PLATFORM_EXTENSIONS.values() {
            let Some(client) = (def.client_factory)(context.clone()) else {
                continue;
            };
            let instructions = client
                .get_instructions(&session.id, &session.working_dir)
                .await
                .unwrap_or_default();
            let has_resources = client
                .get_info()
                .and_then(|i| i.capabilities.resources.as_ref())
                .is_some();
            extensions.push(ExtensionInfo::new(def.name, &instructions, has_resources));
        }

        extensions.sort_by(|a, b| a.name.cmp(&b.name));

        let manager = PromptManager::with_timestamp(DateTime::<Utc>::from_timestamp(0, 0).unwrap());
        let system_prompt = manager
            .builder()
            .with_extensions(extensions.into_iter())
            .build()
            .unwrap();

        assert_snapshot!(system_prompt);
    }
}

```

## crates/goose/src/prompt_template.rs

Source: https://github.com/aaif-goose/goose/blob/bfe8ccd23de1d2c47a4cb2970d522f093ae7f2ba/crates/goose/src/prompt_template.rs

SHA-256: `0a9ead88f03c9903eb25e1a92c822076bbaf18f0694286bbedebc9eabbbd401c`

```
use crate::config::paths::Paths;
use include_dir::{include_dir, Dir};
use minijinja::{Environment, Error as MiniJinjaError, Value as MJValue};
use serde::Serialize;
use std::path::PathBuf;

static CORE_PROMPTS_DIR: Dir = include_dir!("$CARGO_MANIFEST_DIR/src/prompts");

static TEMPLATE_REGISTRY: &[(&str, &str)] = &[
    (
        "system.md",
        "Main system prompt that defines goose's personality and behavior",
    ),
    (
        "compaction.md",
        "Prompt for summarizing conversation history when context limits are reached",
    ),
    (
        "compaction_summary.md",
        "Renders the structured compaction output into the post-compaction context",
    ),
    (
        "subagent_system.md",
        "System prompt for subagents spawned to handle specific tasks",
    ),
    (
        "apps_create.md",
        "Prompt for generating new Goose apps based on the user instructions",
    ),
    (
        "apps_iterate.md",
        "Prompt for updating existing Goose apps based on feedback",
    ),
    (
        "permission_judge.md",
        "Prompt for analyzing tool operations for read-only detection",
    ),
    (
        "tiny_model_system.md",
        "System prompt for tiny local models using shell command emulation",
    ),
    (
        "session_name.md",
        "System prompt for generating short session names from conversation history",
    ),
];

/// Information about a template including its content and customization status
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Template {
    pub name: String,
    pub description: String,
    pub default_content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_content: Option<String>,
    pub is_customized: bool,
}

fn builtin_content(name: &str) -> Option<String> {
    CORE_PROMPTS_DIR
        .get_file(name)
        .map(|file| String::from_utf8_lossy(file.contents()).to_string())
        .or_else(|| goose_context_management::templates::builtin_template(name))
}

fn user_prompts_dir() -> PathBuf {
    Paths::config_dir().join("prompts")
}

fn is_registered(name: &str) -> bool {
    TEMPLATE_REGISTRY.iter().any(|(n, _)| *n == name)
}

/// Wrap code in a markdown fence longer than any backtick run it contains,
/// so embedded fences cannot close the block early.
fn code_fence(code: String) -> String {
    let longest_run = code
        .chars()
        .fold((0usize, 0usize), |(max, run), c| {
            if c == '`' {
                (max.max(run + 1), run + 1)
            } else {
                (max, 0)
            }
        })
        .0;
    let fence = "`".repeat((longest_run + 1).max(3));
    format!("{fence}\n{}\n{fence}", code.trim_end_matches('\n'))
}

pub fn render_string<T: Serialize>(
    template_str: &str,
    context: &T,
) -> Result<String, MiniJinjaError> {
    let mut env = Environment::new();
    env.set_trim_blocks(true);
    env.set_lstrip_blocks(true);
    env.add_filter("code_fence", code_fence);
    env.add_template("template", template_str)?;
    let tmpl = env.get_template("template")?;
    let ctx = MJValue::from_serialize(context);
    let rendered = tmpl.render(ctx)?;
    Ok(rendered.trim().to_string())
}

pub fn render_template<T: Serialize>(name: &str, context: &T) -> Result<String, MiniJinjaError> {
    if !is_registered(name) {
        return Err(MiniJinjaError::new(
            minijinja::ErrorKind::TemplateNotFound,
            format!("Template '{}' is not registered", name),
        ));
    }

    let user_path = user_prompts_dir().join(name);
    let template_str = if user_path.exists() {
        std::fs::read_to_string(&user_path).map_err(|e| {
            MiniJinjaError::new(
                minijinja::ErrorKind::InvalidOperation,
                format!("Failed to read user template: {}", e),
            )
        })?
    } else {
        builtin_content(name).ok_or_else(|| {
            MiniJinjaError::new(
                minijinja::ErrorKind::TemplateNotFound,
                format!("Built-in template '{}' not found", name),
            )
        })?
    };

    render_string(&template_str, context)
}

pub fn template_source(name: &str) -> Result<String, MiniJinjaError> {
    let user_path = user_prompts_dir().join(name);
    if user_path.exists() {
        return std::fs::read_to_string(&user_path).map_err(|e| {
            MiniJinjaError::new(
                minijinja::ErrorKind::InvalidOperation,
                format!("Failed to read user template: {}", e),
            )
        });
    }
    builtin_content(name).ok_or_else(|| {
        MiniJinjaError::new(
            minijinja::ErrorKind::TemplateNotFound,
            format!("Built-in template '{}' not found", name),
        )
    })
}

pub fn get_template(name: &str) -> Option<Template> {
    let (_, description) = TEMPLATE_REGISTRY.iter().find(|(n, _)| *n == name)?;

    let default_content = builtin_content(name)?;

    let user_path = user_prompts_dir().join(name);
    let user_content = if user_path.exists() {
        std::fs::read_to_string(&user_path).ok()
    } else {
        None
    };

    let is_customized = user_content.is_some();

    Some(Template {
        name: name.to_string(),
        description: description.to_string(),
        default_content,
        user_content,
        is_customized,
    })
}

pub fn save_template(name: &str, content: &str) -> std::io::Result<()> {
    if !is_registered(name) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("Template '{}' is not registered", name),
        ));
    }

    let prompts_dir = user_prompts_dir();
    std::fs::create_dir_all(&prompts_dir)?;
    let path = prompts_dir.join(name);
    std::fs::write(path, content)
}

/// Reset a template to its default by removing the user customization.
pub fn reset_template(name: &str) -> std::io::Result<()> {
    if !is_registered(name) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("Template '{}' is not registered", name),
        ));
    }

    let path = user_prompts_dir().join(name);
    if path.exists() {
        std::fs::remove_file(path)
    } else {
        Ok(())
    }
}

pub fn list_templates() -> Vec<Template> {
    TEMPLATE_REGISTRY
        .iter()
        .filter_map(|(name, description)| {
            let default_content = builtin_content(name)?;

            let user_path = user_prompts_dir().join(name);
            let user_content = if user_path.exists() {
                std::fs::read_to_string(&user_path).ok()
            } else {
                None
            };

            let is_customized = user_content.is_some();

            Some(Template {
                name: name.to_string(),
                description: description.to_string(),
                default_content,
                user_content,
                is_customized,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn test_get_template() {
        let template = get_template("system.md");
        assert!(template.is_some(), "system.md should be registered");

        let template = template.unwrap();
        assert_eq!(template.name, "system.md");
        assert!(!template.description.is_empty());
        assert!(!template.default_content.is_empty());
        assert!(!template.is_customized);
    }

    #[test]
    fn test_render_template() {
        let context: HashMap<String, String> = HashMap::new();
        let result = render_template("system.md", &context);
        assert!(result.is_ok(), "Should be able to render system.md");
        assert!(!result.unwrap().is_empty());
    }

    #[test]
    fn test_list_templates() {
        let templates = list_templates();
        assert_eq!(templates.len(), TEMPLATE_REGISTRY.len());

        let has_system = templates.iter().any(|t| t.name == "system.md");
        assert!(has_system, "system.md should be in the template list");

        for template in templates {
            assert!(
                !template.description.is_empty(),
                "Each template should have a description"
            );
            assert!(
                !template.default_content.is_empty(),
                "Each template should have content"
            );
        }
    }
}

```

## crates/goose/src/prompts/subagent_system.md

Source: https://github.com/aaif-goose/goose/blob/bfe8ccd23de1d2c47a4cb2970d522f093ae7f2ba/crates/goose/src/prompts/subagent_system.md

SHA-256: `fdae9eb072c3755c488c6385a6d6c273e0bb1bc25e5889aa116a4fce47811c6f`

```
You are a specialized subagent within the goose AI framework, created by AAIF (Agentic AI Foundation). You were spawned by the main goose agent to handle a specific task efficiently.

# Your Role
You are an autonomous subagent with these characteristics:
- **Independence**: Make decisions and execute tools within your scope
- **Specialization**: Focus on specific tasks assigned by the main agent
- **Efficiency**: Use tools sparingly and only when necessary
- **Bounded Operation**: Operate within defined limits (turn count, timeout)
- **Security**: Cannot spawn additional subagents
The maximum number of turns to respond is {{max_turns}}.

# Tool Usage Guidelines
**CRITICAL**: Be efficient with tool usage. Use tools only when absolutely necessary to complete your task. Here are the available tools you have access to:
You have access to {{tool_count}} tools: {{available_tools}}

**Tool Efficiency Rules**:
- Use the minimum number of tools needed to complete your task
- Avoid exploratory tool usage unless explicitly required
- Stop using tools once you have sufficient information
- Provide clear, concise responses without excessive tool calls

# Communication Guidelines
- **Progress Updates**: Report progress clearly and concisely
- **Completion**: Clearly indicate when your task is complete
- **Scope**: Stay focused on your assigned task
- **Format**: Use Markdown formatting for responses
- **Summarization**: If asked for a summary or report of your work, that should be the last message you generate

Remember: You are part of a larger system. Your specialized focus helps the main agent handle multiple concerns efficiently. Complete your task efficiently with less tool usage.

```

## crates/goose/src/prompts/system.md

Source: https://github.com/aaif-goose/goose/blob/bfe8ccd23de1d2c47a4cb2970d522f093ae7f2ba/crates/goose/src/prompts/system.md

SHA-256: `e0a59861edd6791f3df404e27de8bc360d5e5053f535a14b2d775f7815fdb3f0`

```
You are a general-purpose AI agent called goose, created by AAIF (Agentic AI Foundation).
goose is being developed as an open-source software project.

{% if moim_system_prompt_block is defined %}
{{ moim_system_prompt_block }}
{% endif %}

{% if include_extensions and not code_execution_mode %}

# Extensions

Extensions provide additional tools and context from different data sources and applications.
You can dynamically enable or disable extensions as needed to help complete tasks.

{% if (extensions is defined) and extensions %}
Because you dynamically load extensions, your conversation history may refer
to interactions with extensions that are not currently active. The currently
active extensions are below. Each of these extensions provides tools that are
in your tool specification.

{% for extension in extensions %}

## {{extension.name}}

{% if extension.has_resources %}
{{extension.name}} supports resources.
{% endif %}
{% if extension.instructions %}### Instructions
{{extension.instructions}}{% endif %}
{% endfor %}

{% else %}
No extensions are defined. You should let the user know that they should add extensions.
{% endif %}
{% endif %}

# Response Guidelines

Use Markdown formatting for all responses.

```
