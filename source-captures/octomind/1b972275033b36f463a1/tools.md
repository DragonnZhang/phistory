# octomind — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/mcp/runtime/capability.rs

Source: https://github.com/Muvon/octomind/blob/4cc5600403885e1d9bc2cba6750f5c3c42dbed2f/src/mcp/runtime/capability.rs

SHA-256: `0ee5355eeb7ee87a43d775c544e72f56c88ef1b9f21ff2bfd6cbdea9d6230e28`

```
// Copyright 2026 Muvon Un Limited
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! Capability tool — runtime discovery and activation of capabilities.
//!
//! A capability is a domain abstraction (e.g., `database.postgres`,
//! `web.search`) that resolves to one or more MCP servers and a set of
//! allowed tools. Taps declare must-have capabilities in agent manifests;
//! those are merged into the effective config at boot.
//!
//! Two activation paths exist:
//!
//! - **Deterministic auto-activation** (preferred). On every fresh user
//!   message, `auto_activate_capabilities` embeds the intent and matches
//!   it against each inactive capability's hand-authored `triggers`
//!   (mean-of-top-K cosine + margin gate). On a hit, the capability's
//!   MCP servers are registered and enabled directly — no LLM in the
//!   routing loop, no extra tool-call turn.
//!
//! - **Manual via this tool** (fallback). The `capability` tool exposes
//!   `list`, `enable`, `disable`, `discover` for cases where auto-
//!   activation didn't fire (offline, model still warming up, intent
//!   too ambiguous to clear the margin gate).
//!
//! Actions:
//! - `list`     — show all installed capabilities (active marked).
//! - `enable`   — activate a capability by name (registers + enables its MCP servers).
//! - `disable`  — deactivate a previously-enabled capability.
//! - `discover` — find capabilities matching an intent string (semantic match via embeddings, falls back to keyword match).

use crate::config::Config;
use crate::mcp::{McpFunction, McpToolCall, McpToolResult};
use anyhow::{Context, Result};
use serde_json::json;
use std::collections::HashMap;
use std::sync::{Arc, OnceLock, RwLock};
use std::time::Instant;

// ---------------------------------------------------------------------------
// Active capabilities registry (process-global; mirrors dynamic.rs pattern)
//
// We track per-capability state so we can do LRU eviction when the active
// set hits a soft cap. Eviction is the only auto-disable mechanism; we
// deliberately don't time-decay or domain-shift evict because production
// agent UX is hurt more by false-disable than by carrying an idle cap.
// ---------------------------------------------------------------------------

/// State for one active capability. `server_tools` is the list of MCP
/// servers + the bare tool names this capability registered when it was
/// activated. Per-server tool granularity is required because multiple
/// capabilities can share one MCP server (e.g. `codesearch` exposes
/// `semantic_search`+`view_signatures` while `codesearch-graph` exposes
/// `graphrag`, both backed by the same `octocode` server). On eviction
/// we strip only THIS cap's tools and only kill the server when no other
/// active cap still references it (refcount → 0). `last_used` updates on
/// every successful tool call from any of these servers; LRU eviction
/// picks the entry with the smallest `last_used`.
#[derive(Debug, Clone)]
struct CapState {
	server_tools: Vec<(String, Vec<String>)>,
	last_used: Instant,
}

/// Soft cap on simultaneously-active capabilities. When a new activation
/// would exceed this, the LRU entry is disabled first to make room.
///
/// Sized to balance two pressures:
/// - **Tool overload research** (Microsoft, AWS, Boundary, Chroma) shows
///   sharp accuracy degradation past ~20-25 tools exposed to the model.
///   With baseline always-on tools (~15-20) plus ~4-5 tools per capability,
///   4 active caps keeps total tool surface in the safe zone (~35-40).
/// - **Real task concurrency** rarely needs more than 2-3 capabilities at
///   once; 4 leaves headroom for cross-domain tasks without churning.
///
/// Eviction is purely demand-driven: caps stay active indefinitely until
/// a new activation hits the cap. No background timers or idle cleanup.
const MAX_ACTIVE_CAPS: usize = 4;

/// Capabilities activated at runtime by this tool, per session (`""` outside
/// one). Capabilities pre-loaded from the tap manifest at boot are NOT tracked
/// here — they are already merged into the agent's effective config and
/// represented as regular MCP servers.
///
/// Per session because what an activation registers is: dynamic servers live in
/// the session's own registry. One `octomind server` process hosts every session
/// on a machine, and a process-wide set answered the second session's
/// `OCTOMIND_CAPABILITIES` load with "already active", so only the first session
/// to bind ever got those tools.
type CapRegistry = Arc<RwLock<HashMap<String, CapState>>>;
static ACTIVE_CAPABILITIES: OnceLock<RwLock<HashMap<String, CapRegistry>>> = OnceLock::new();

fn registry() -> CapRegistry {
	let session = crate::session::context::current_session_id().unwrap_or_default();
	let all = ACTIVE_CAPABILITIES.get_or_init(Default::default);
	if let Some(caps) = all.read().unwrap().get(&session) {
		return Arc::clone(caps);
	}
	Arc::clone(all.write().unwrap().entry(session).or_default())
}

/// Forget a session's active capabilities when the session ends.
pub(crate) fn clear_session_capabilities(session_id: &str) {
	if let Some(all) = ACTIVE_CAPABILITIES.get() {
		all.write().unwrap().remove(session_id);
	}
}

fn is_active(name: &str) -> bool {
	registry().read().unwrap().contains_key(name)
}

fn active_count() -> usize {
	registry().read().unwrap().len()
}

fn mark_active(name: &str, server_tools: Vec<(String, Vec<String>)>) {
	registry().write().unwrap().insert(
		name.to_string(),
		CapState {
			server_tools,
			last_used: Instant::now(),
		},
	);
}

// `mark_inactive` removed — handle_disable / evict_lru_if_full now
// remove cap entries directly under the same write lock that builds
// the per-server disable plan, so a separate helper is dead weight.

/// Find which active capability owns the given MCP server name and bump
/// its `last_used` to now. Called from the tool-call dispatch path so
/// LRU eviction tracks real usage, not just activation order.
pub(crate) fn touch_capability_for_server(server_name: &str) {
	let caps = registry();
	let mut reg = caps.write().unwrap();
	for state in reg.values_mut() {
		if state.server_tools.iter().any(|(s, _)| s == server_name) {
			state.last_used = Instant::now();
			return;
		}
	}
}

/// Count how many active capabilities (other than `excluding`) still
/// reference `server_name`. Used by eviction to decide whether the
/// underlying MCP server should be fully shut down or only have its
/// caller's tools stripped from the global tool_map.
fn server_refcount(reg: &HashMap<String, CapState>, server_name: &str, excluding: &str) -> usize {
	reg.iter()
		.filter(|(name, _)| name.as_str() != excluding)
		.filter(|(_, st)| st.server_tools.iter().any(|(s, _)| s == server_name))
		.count()
}

/// Pure helper: find the entry with the smallest `last_used` and remove it.
/// Returns `(name, server_tools)` so the caller can disable the underlying
/// servers selectively; doesn't touch the dynamic-server registry itself.
/// Separated from `evict_lru_if_full` so the selection logic is unit-
/// testable without touching global state or needing a `Config`.
/// Per-capability tool ownership: (server_name, bare tool names this
/// cap registered on that server). Multiple caps can list the same
/// `server_name` with disjoint tool sets — refcount logic uses this.
pub(crate) type ServerToolGroups = Vec<(String, Vec<String>)>;

/// Disable plan entry: server name, the specific tools to strip from
/// the global tool_map, and whether to fully kill the server (true =
/// no other active cap references it).
type DisablePlanEntry = (String, Vec<String>, bool);

fn select_lru_in(map: &mut HashMap<String, CapState>) -> Option<(String, ServerToolGroups)> {
	let lru_name = map.iter().min_by_key(|(_, st)| st.last_used).map(|(n, _)| n.clone())?;
	let st = map.remove(&lru_name)?;
	Some((lru_name, st.server_tools))
}

/// If the active set is at or above the soft cap, evict the LRU entry
/// (lowest `last_used`) and disable its MCP-server tools. Logged at info
/// level so users see what flipped off.
///
/// Refcount-aware: for each (server, tools) the evicted cap registered,
/// the underlying server is fully shut down ONLY when no other active
/// cap still references that server name. Otherwise just THIS cap's
/// tools are stripped from the global tool_map and the server keeps
/// running for its other consumers.
///
/// Called before activating a new capability; idempotent when the active
/// set is below the cap. Errors disabling individual servers are logged
/// but don't block: we'd rather have the eviction happen with one stale
/// server than fail the new activation.
fn evict_lru_if_full(config: &Config) {
	if active_count() < MAX_ACTIVE_CAPS {
		return;
	}

	// Compute the disable plan under one write lock so refcounts are
	// consistent: read the LRU's server list, remove it from the
	// registry, then count remaining references for each server.
	//
	// `kill` here means "tear down the underlying MCP server process", not
	// "strip this cap's tools". Two reasons to keep the server alive:
	//   1. Another active capability still references it (refcount > 0).
	//   2. The role's static config declares it — the role still owns it
	//      regardless of dynamic-cap activity.
	let plan: Option<(String, Vec<DisablePlanEntry>)> = {
		let caps = registry();
		let mut reg = caps.write().unwrap();
		select_lru_in(&mut reg).map(|(lru_name, server_tools)| {
			let entries = server_tools
				.into_iter()
				.map(|(srv, tools)| {
					let static_owned = config.mcp.servers.iter().any(|s| s.name() == srv);
					let kill = !static_owned && server_refcount(&reg, &srv, &lru_name) == 0;
					(srv, tools, kill)
				})
				.collect();
			(lru_name, entries)
		})
	};

	if let Some((name, entries)) = plan {
		// Drop overlay contributions before stripping tools so the next
		// merge sees the reduced filter.
		crate::config::runtime_overlay::clear_capability_extras(&name);

		let server_count = entries.len();
		for (srv, tools, kill) in &entries {
			if let Err(e) = crate::mcp::runtime::dynamic::disable_server_tools(srv, tools, *kill, Some(config)) {
				crate::log_debug!(
					"capability LRU evict: failed to disable tools for server '{}' (kill={}, {} tools): {}",
					srv,
					kill,
					tools.len(),
					e
				);
			}
		}
		crate::log_info!(
			"capability LRU evicted: '{}' ({} server-tool-group(s) processed)",
			name,
			server_count
		);
	}
}

// ---------------------------------------------------------------------------
// McpFunction definition
// ---------------------------------------------------------------------------

pub fn get_capability_function() -> McpFunction {
	McpFunction {
		name: "capability".to_string(),
		annotations: None,
		description: r#"Discover and activate capabilities mid-session. Capabilities are domain bundles (e.g., database-postgres, filesystem, kubernetes) that resolve to MCP servers and tools. Use when the agent needs functionality outside its starting kit.

Actions:
- list: show all installed capabilities. Active ones are marked. One line per capability: name + brief description.
- enable: activate a capability by name. Registers and enables its MCP servers, exposing tools in subsequent turns.
- disable: deactivate a previously-enabled capability.
- discover: find capabilities matching an intent string (semantic match via embeddings, falls back to keyword match).

Workflow: call list or discover to find the right capability, then enable to activate it. Tool surface grows on demand. When intent is generic (e.g. 'I need a database') and multiple capabilities could fit, prefer list or discover over guessing."#.to_string(),
		parameters: json!({
			"type": "object",
			"properties": {
				"action": {
					"type": "string",
					"enum": ["list", "enable", "disable", "discover"],
					"description": "Action to perform"
				},
				"name": {
					"type": "string",
					"description": "Capability name (required for enable and disable). Bare name searches every tap; octomind/<name> pins the baseline tap, <org>/<name> a connected tap."
				},
				"intent": {
					"type": "string",
					"description": "Free-text intent for discover action (e.g., 'I need to query a database')"
				}
			},
			"required": ["action"]
		}),
	}
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

/// Filter a capability list down to those available in the current session's
/// domain. Wraps `agent::registry::cap_available_in_domain` against the
/// session-scoped domain string from `session::context::current_session_domain`.
///
/// When no domain is set (early init / out-of-session tool calls), only
/// universal (empty-`domains`) caps survive — strict interpretation of the
/// hard-bound rule: a domain-restricted cap requires a known domain context.
fn filter_caps_by_domain(
	caps: Vec<crate::agent::registry::ResolvedCapability>,
) -> Vec<crate::agent::registry::ResolvedCapability> {
	let domain = crate::session::context::current_session_domain();
	let domain_ref: &str = domain.as_deref().unwrap_or("");
	caps.into_iter()
		.filter(|c| crate::agent::registry::cap_available_in_domain(&c.domains, domain_ref))
		.collect()
}

/// Domain check for a single capability's domains list. Same rule as
/// `filter_caps_by_domain` but for the single-cap activation path
/// (`handle_enable`, `activate_capability_inline`).
fn cap_in_current_domain(domains: &[String]) -> bool {
	let cur = crate::session::context::current_session_domain();
	let cur_ref: &str = cur.as_deref().unwrap_or("");
	crate::agent::registry::cap_available_in_domain(domains, cur_ref)
}

pub async fn execute_capability_command(call: &McpToolCall, config: &Config) -> Result<McpToolResult> {
	let action = match call.parameters.get("action").and_then(|v| v.as_str()) {
		Some(a) if !a.trim().is_empty() => a.trim().to_string(),
		_ => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				"Missing required parameter 'action'".to_string(),
			));
		}
	};
	match action.as_str() {
		"list" => handle_list(call, config).await,
		"enable" => handle_enable(call, config).await,
		"disable" => handle_disable(call, config).await,
		"discover" => handle_discover(call, config).await,
		other => Ok(McpToolResult::error(
			call.tool_name.clone(),
			call.tool_id.clone(),
			format!("Unknown action '{other}'. Use list, enable, disable, or discover."),
		)),
	}
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async fn handle_list(call: &McpToolCall, config: &Config) -> Result<McpToolResult> {
	let caps = match crate::agent::registry::list_all_capabilities(&config.capabilities) {
		Ok(c) => c,
		Err(e) => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Failed to enumerate capabilities: {e}"),
			));
		}
	};
	let caps = filter_caps_by_domain(caps);
	if caps.is_empty() {
		return Ok(McpToolResult::success(
			call.tool_name.clone(),
			call.tool_id.clone(),
			"No capabilities installed in any tap.".to_string(),
		));
	}
	let mut output = format!("Installed capabilities ({}):\n", caps.len());
	for cap in &caps {
		let env_missing = if cap.required_env_keys.is_empty() {
			None
		} else {
			check_env_readiness(&cap.required_env_keys).err()
		};
		if let Some(missing) = &env_missing {
			crate::log_debug!(
				"capability list: '{}' not env-ready — missing: {}",
				cap.name,
				missing.join(", ")
			);
		}
		let marker = if is_active(&cap.name) {
			"[active] "
		} else if env_missing.is_some() {
			"[missing env] "
		} else {
			""
		};
		let env_note = match &env_missing {
			Some(missing) => format!(" (missing env: {})", missing.join(", ")),
			None => String::new(),
		};
		output.push_str(&format!(
			"- {}{} — {}{}\n",
			marker,
			cap.name,
			triggers_preview(&cap.triggers),
			env_note
		));
	}
	output.push_str("\nUse capability(action=\"enable\", name=\"<name>\") to activate.");
	Ok(McpToolResult::success(
		call.tool_name.clone(),
		call.tool_id.clone(),
		output,
	))
}

/// Render the first few triggers of a capability as a comma-separated
/// preview so users see *what they'd say* to invoke it. More useful than
/// a hand-written description.
fn triggers_preview(triggers: &[String]) -> String {
	let take = triggers.iter().take(3).cloned().collect::<Vec<_>>();
	let suffix = if triggers.len() > 3 { ", …" } else { "" };
	format!(
		"{}{}",
		take.iter().map(|t| format!("\"{t}\"")).collect::<Vec<_>>().join(", "),
		suffix
	)
}

async fn handle_enable(call: &McpToolCall, config: &Config) -> Result<McpToolResult> {
	let name = match call.parameters.get("name").and_then(|v| v.as_str()) {
		Some(n) if !n.trim().is_empty() => n.trim().to_string(),
		_ => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				"Missing required parameter 'name'".to_string(),
			));
		}
	};

	if is_active(crate::agent::registry::capability_bare_name(&name)) {
		return Ok(McpToolResult::success(
			call.tool_name.clone(),
			call.tool_id.clone(),
			format!("Capability '{name}' is already active."),
		));
	}

	let resolved = match crate::agent::registry::parse_capability_toml(&name, &config.capabilities) {
		Ok(r) => r,
		Err(e) => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Capability '{name}' not found: {e}"),
			));
		}
	};

	// Env readiness gate: refuse to activate a capability whose required
	// env vars are not set. Prevents activating a server that will fail
	// at first use because its API key is missing.
	if !resolved.required_env_keys.is_empty() {
		if let Err(missing) = check_env_readiness(&resolved.required_env_keys) {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!(
					"Capability '{name}' requires env vars: {} — set them before activating.",
					missing.join(", ")
				),
			));
		}
	}

	// Domain gate. Refuses to enable a capability that's bound to other
	// domains, regardless of whether the user invoked `capability enable`
	// directly, the auto-activator routed here, or `OCTOMIND_CAPABILITIES`
	// env-loaded it at boot. Hard-bound — no bypass.
	if !cap_in_current_domain(&resolved.domains) {
		let current = crate::session::context::current_session_domain().unwrap_or_else(|| "<unknown>".to_string());
		return Ok(McpToolResult::error(
			call.tool_name.clone(),
			call.tool_id.clone(),
			format!(
				"Capability '{name}' is bound to domains {:?}; current domain is '{current}'. \
				 Run the matching role (e.g. `octomind run {}:general`) to access it.",
				resolved.domains,
				resolved.domains.first().map(String::as_str).unwrap_or("<domain>"),
			),
		));
	}

	// Deps-only capabilities (no MCP servers): activation runs the dep
	// installers — that IS the activation. Toolchain caps like
	// `programming-nodejs` use this path to install node/npm/npx so the
	// agent's shell can use them. Genuinely empty caps (no servers AND no
	// deps) remain an error.
	if resolved.mcp_servers.is_empty() {
		if resolved.deps.is_empty() {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Capability '{name}' has no [[mcp.servers]] and no [deps] — nothing to activate."),
			));
		}
		evict_lru_if_full(config);
		if let Err(e) = crate::agent::deps::run_dep_entries(&resolved.deps, &resolved.tap_root, None).await {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Failed to install deps for capability '{name}': {e:#}"),
			));
		}
		mark_active(&resolved.name, Vec::new());
		return Ok(McpToolResult::success(
			call.tool_name.clone(),
			call.tool_id.clone(),
			format!(
				"Capability '{name}' enabled. Installed deps: {}",
				resolved.deps.join(", ")
			),
		));
	}

	// Make room before activating — drops the LRU active capability if
	// we'd exceed `MAX_ACTIVE_CAPS`. No-op when below the cap.
	evict_lru_if_full(config);

	let mut activated_tools: Vec<String> = Vec::new();
	let mut activated_servers: Vec<String> = Vec::new();
	// Per-(server, bare-tool-names) record we hand to `mark_active` so
	// LRU eviction can strip only THIS cap's tools when servers are
	// shared with other active caps. See CapState docs.
	let mut activated_server_tools: Vec<(String, Vec<String>)> = Vec::new();
	// Track whether *any* server activation passed a non-empty filter, so
	// the success message can distinguish "all-tools" from "filter-applied".
	let mut any_filter_applied = false;

	// Per-server tool contributions for the runtime overlay. Only servers
	// that are already in the role's static config get an overlay entry —
	// fully-dynamic servers are surfaced through `dynamic::get_all_functions`
	// and don't need overlay extras to be visible.
	let mut overlay_per_server: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();

	for server in &resolved.mcp_servers {
		let server_name = server.name().to_string();

		// Compute the filter for *this* server. `allowed_tools` patterns in
		// capability TOMLs are namespace-prefixed (e.g., `playwright:*`,
		// `playwright:browser_navigate`) so a single capability config can
		// scope tools across multiple MCP servers. But `enable_server`
		// matches against the *bare* tool names returned by the server
		// (e.g., `browser_navigate`, not `playwright:browser_navigate`),
		// so we strip the `<server>:` prefix here. Patterns for *other*
		// servers in the same cap are dropped for this server. Patterns
		// without any namespace apply to every server.
		let filter_for_this = filter_for_server(&resolved.allowed_tools, &server_name);
		if filter_for_this.is_some() {
			any_filter_applied = true;
		}

		// Two activation paths share the same registry/overlay/tool-map
		// shape so disable/eviction is uniform regardless of where the
		// server originated.
		//
		// 1. Server already in the role's static config (declared by the
		//    role's `capabilities = [...]` at boot). The MCP init already
		//    exposes its tools via the static path, but this capability's
		//    `allowed_tools` for that server may include names the role's
		//    own filter rejects. We extend the role's effective filter
		//    via the runtime overlay (consulted by
		//    `RoleMcpConfig::get_enabled_servers`) AND register the bare
		//    tool names in the global tool_map so dispatch can route them.
		// 2. Server is fully dynamic (capability brought it in at runtime).
		//    Register + enable through the dynamic registry as before; the
		//    dynamic `get_all_functions` path surfaces its tools, no
		//    overlay needed.
		if let Some(server_config) = config.mcp.servers.iter().find(|s| s.name() == server_name) {
			let bare_names = static_server_grant(server_config, filter_for_this, config).await;

			// Register THIS cap's tools in the global tool_map so the
			// dispatcher can route a call like `octofs:shell` even though
			// the role's static filter never listed it.
			if !bare_names.is_empty() {
				crate::mcp::tool_map::register_dynamic_server_tools(&server_name, server_config, &bare_names);
				crate::mcp::server::clear_function_cache_for_server(&server_name);
				overlay_per_server.insert(server_name.clone(), bare_names.clone());
			}

			activated_tools.extend(bare_names.iter().cloned());
			activated_server_tools.push((server_name.clone(), bare_names));
			activated_servers.push(server_name);
			continue;
		}

		// Fully dynamic — register + enable.
		if !crate::mcp::runtime::dynamic::is_dynamic(&server_name) {
			if let Err(e) = crate::mcp::runtime::dynamic::register_server(server.clone()) {
				return Ok(McpToolResult::error(
					call.tool_name.clone(),
					call.tool_id.clone(),
					format!("Failed to register server '{server_name}' for capability '{name}': {e}"),
				));
			}
		}

		match crate::mcp::runtime::dynamic::enable_server(&server_name, filter_for_this).await {
			Ok(functions) => {
				let bare_names: Vec<String> = functions.iter().map(|f| f.name.clone()).collect();
				activated_tools.extend(bare_names.iter().cloned());
				activated_server_tools.push((server_name.clone(), bare_names));
				activated_servers.push(server_name);
			}
			Err(e) => {
				return Ok(McpToolResult::error(
					call.tool_name.clone(),
					call.tool_id.clone(),
					format!("Failed to enable server '{server_name}' for capability '{name}': {e}"),
				));
			}
		}
	}

	// Publish overlay entries so the next config merge picks up this
	// capability's contributions to static servers' filters.
	crate::config::runtime_overlay::set_capability_extras(&resolved.name, overlay_per_server);

	mark_active(&resolved.name, activated_server_tools);

	// Don't mislead the LLM with "Tools available: none" when no filter
	// was applied — that path means "expose all server tools", and an
	// empty function list at activation time can simply mean the server
	// hasn't completed its tool-list handshake yet (e.g., Playwright MCP
	// initializes lazily). Saying "none" makes the agent disable the
	// server it just activated. Distinguish the three cases explicitly.
	let tools_summary = if !any_filter_applied {
		"all tools the server exposes (list populates on first use if empty now)".to_string()
	} else if activated_tools.is_empty() {
		"none — the configured allowed_tools filter excluded every tool the server reported".to_string()
	} else {
		activated_tools.join(", ")
	};

	let msg = format!(
		"Capability '{name}' enabled. Activated {} server(s): {}\nTools available: {}",
		activated_servers.len(),
		activated_servers.join(", "),
		tools_summary
	);
	Ok(McpToolResult::success(
		call.tool_name.clone(),
		call.tool_id.clone(),
		msg,
	))
}

async fn handle_disable(call: &McpToolCall, config: &Config) -> Result<McpToolResult> {
	let name = match call.parameters.get("name").and_then(|v| v.as_str()) {
		Some(n) if !n.trim().is_empty() => crate::agent::registry::capability_bare_name(n.trim()).to_string(),
		_ => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				"Missing required parameter 'name'".to_string(),
			));
		}
	};

	if !is_active(&name) {
		return Ok(McpToolResult::success(
			call.tool_name.clone(),
			call.tool_id.clone(),
			format!("Capability '{name}' is not active."),
		));
	}

	// Compute the disable plan under one write lock so refcounts are
	// consistent: pull THIS cap's (server, tools) record, remove the
	// cap from the registry, then count remaining references for each
	// server. Mirrors `evict_lru_if_full`.
	//
	// `kill` only flips true when no other active capability references
	// the server AND the server is not in the role's static config. The
	// static-config check stops `disable` from tearing down servers the
	// role still relies on (the LRU eviction path uses the same rule).
	let plan: Option<(CapState, Vec<DisablePlanEntry>)> = {
		let caps = registry();
		let mut reg = caps.write().unwrap();
		reg.remove(&name).map(|state| {
			// Build the plan from a clone so the original state can be
			// re-inserted verbatim if any disable step fails mid-loop.
			let entries = state
				.server_tools
				.clone()
				.into_iter()
				.map(|(srv, tools)| {
					let static_owned = config.mcp.servers.iter().any(|s| s.name() == srv);
					let kill = !static_owned && server_refcount(&reg, &srv, &name) == 0;
					(srv, tools, kill)
				})
				.collect();
			(state, entries)
		})
	};

	let (original_state, plan) = match plan {
		Some(p) => p,
		None => {
			// Race: someone else evicted between is_active check and the
			// write-lock above. Treat as no-op.
			return Ok(McpToolResult::success(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Capability '{name}' is not active."),
			));
		}
	};

	// Drop the overlay entry so the next merge sees the reduced per-server
	// filter for static servers this cap was contributing to. Order matters:
	// clear before tool_map updates so the two stay in sync if a concurrent
	// merge reads them.
	crate::config::runtime_overlay::clear_capability_extras(&name);

	let mut disabled_servers: Vec<String> = Vec::new();
	for (srv, tools, kill) in &plan {
		// Always strip THIS cap's tool entries from the global tool_map,
		// even on static servers — the cap brought them in via the runtime
		// overlay, so they need to leave the map when it's disabled.
		// `kill=false` selects the strip-only path inside
		// `disable_server_tools`; static servers reach this branch via the
		// `static_owned` rule above.
		if let Err(e) = crate::mcp::runtime::dynamic::disable_server_tools(srv, tools, *kill, Some(config)) {
			// Re-insert the cap so the user can retry. Fail closed — partial
			// disable is worse than reporting the error. Partially stripped
			// servers are restored by the user retrying enable (enable
			// re-applies overlay + tools).
			registry().write().unwrap().insert(name.clone(), original_state);
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Failed to disable server '{srv}' for capability '{name}': {e}"),
			));
		}
		if *kill {
			disabled_servers.push(srv.clone());
		}
	}

	Ok(McpToolResult::success(
		call.tool_name.clone(),
		call.tool_id.clone(),
		format!(
			"Capability '{name}' disabled. Fully shut down {} server(s): {}",
			disabled_servers.len(),
			if disabled_servers.is_empty() {
				"(none — all servers still in use by other active capabilities)".to_string()
			} else {
				disabled_servers.join(", ")
			}
		),
	))
}

// ---------------------------------------------------------------------------
// Deterministic auto-activation — embed each fresh user message and flip
// the matching capability on without a tool-call round-trip through the LLM.
//
// Why deterministic: agents are unreliable as routers and every extra
// tool-call turn costs money. The embedding layer is fast (≈30ms cold,
// cached thereafter), local (BGE-small-en-v1.5), and cheap. We trade a
// small false-positive risk — bounded by the margin gate — for not
// burning a turn on every capability decision.
//
// Algorithm:
//   1. Embed the user's message once.
//   2. Embed each inactive capability's `triggers` (cached, so this is
//      free after the first turn — triggers don't change mid-session).
//   3. Per capability: cosine vs each trigger, take the mean of the
//      top-K (K = 3). Aurelio Labs Semantic Router pattern; triggers
//      drag the centroid into the query distribution where one-line
//      descriptions don't reach.
//   4. Margin gate: activate iff `top1 >= THRESHOLD && top1 - top2 >= MARGIN`.
//      Single most important precision lever — ambiguous matches abstain
//      rather than activating the wrong capability.
//   5. On a hit, register + enable the underlying MCP servers directly.
//      The agent never sees the routing decision; it just gets a wider
//      tool surface next turn.
// ---------------------------------------------------------------------------

/// Mean-of-top-K cosine threshold a capability must clear to be auto-activated.
/// Tuned for `muvon/octomind-embed` (granite-embedding-30m soup) over short
/// hand-authored triggers.
///
/// 0.65 is the 2026-09 calibration for the granite soup, measured on the
/// int8 ONNX graph the runtime loads (quantization shifts cosines) against
/// the raw trigger corpus: gate accuracy 0.848 at null false-positive rate
/// 0.022 on `eval_real`. This model's cosines sit higher than the BGE
/// fine-tune's, so the floor does real work again alongside the margin;
/// chitchat / OOD inputs are pushed below it by the `_oos` sink cluster
/// learned at training time (octomind-tap/model/scripts/build_dataset.py).
///
/// Re-calibrate after every model retrain with
/// `octomind-tap/model/scripts/calibrate_thresholds.py`.
///
/// History: 0.42 (base BGE, recall-tuned) → 0.55 (base BGE, FP-tuned for
/// chitchat aversion) → 0.45 (BGE FT, margin is now the binding gate) →
/// 0.65 (granite soup, 2026-09: its cosines sit higher; calibrated on the
/// int8 graph against the raw trigger corpus, gate 0.848 at fpr 0.022).
const AUTO_ACTIVATE_THRESHOLD: f32 = 0.65;

/// Required gap between top-1 and top-2 capability scores. Prevents
/// activating one of two near-tied capabilities (e.g. `database-postgres`
/// vs `database-mysql`) when the user's intent doesn't disambiguate.
/// Ambiguous matches abstain — the user (or the agent later via
/// `capability(action="discover")`) clarifies. Was 0.05 → 0.08 for the BGE
/// fine-tune (near-ties on chitchat); 0.06 for the granite soup, whose
/// margins are narrower but whose absolute threshold now does the
/// chitchat filtering (calibrated together with the threshold above).
const AUTO_ACTIVATE_MARGIN: f32 = 0.06;

/// How many triggers per capability contribute to the per-cap score.
/// Mean-of-top-K smooths a single noisy trigger while still rewarding
/// capabilities whose authored examples align with the user's wording.
const AUTO_ACTIVATE_TOP_K: usize = 3;

/// Sort `(score, T)` pairs descending and return the top entry only if
/// `top1 >= threshold` and `top1 - top2 >= margin`. With a single
/// candidate, top2 is treated as 0.0. Sort is stable (Timsort) so ties
/// preserve insertion order. Pure helper, separated from the embedding-
/// driven path so threshold/margin behavior is unit-testable.
fn select_with_margin<T>(mut scored: Vec<(f32, T)>, threshold: f32, margin: f32) -> Option<(f32, T)> {
	if scored.is_empty() {
		return None;
	}
	scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
	let top1_score = scored[0].0;
	if top1_score < threshold {
		return None;
	}
	let top2_score = scored.get(1).map(|x| x.0).unwrap_or(0.0);
	if top1_score - top2_score < margin {
		return None;
	}
	scored.into_iter().next()
}

/// Score one capability against the user's intent: mean of the top-K
/// cosines between the intent vector and each trigger vector. Empty
/// trigger lists score 0.0.
fn score_capability(intent_vec: &[f32], trigger_vecs: &[Vec<f32>]) -> f32 {
	if trigger_vecs.is_empty() {
		return 0.0;
	}
	let mut scores: Vec<f32> = trigger_vecs
		.iter()
		.map(|v| crate::embeddings::cosine(intent_vec, v))
		.collect();
	scores.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
	let take = scores.len().min(AUTO_ACTIVATE_TOP_K);
	let sum: f32 = scores.iter().take(take).sum();
	sum / take as f32
}

/// Inspect the most recent user message and, if a non-active capability
/// strongly matches, activate it directly via `dynamic::enable_server`.
/// Silent no-op when the model isn't ready, no capabilities are installed,
/// Progress events emitted by `load_env_capabilities`.
///
/// Mirrors `crate::mcp::McpInitProgress` so the boot spinner can drive both
/// phases (static MCP init + env-capability load) through one UI loop.
#[derive(Debug, Clone)]
pub enum EnvCapabilityProgress {
	/// Initial event with the full list of capabilities about to load.
	Starting { capabilities: Vec<String> },
	/// One capability finished (success or failure).
	Completed { capability: String, success: bool },
}

/// Load capabilities from the `OCTOMIND_CAPABILITIES` env var (if set).
///
/// Mirrors `skill_auto::load_env_skills`: parses a comma-separated list of
/// capability names and force-activates each one before the agent's first
/// turn. Bypasses both the auto-activation embedding pipeline and the
/// `capability` tool — capabilities listed here are always loaded,
/// regardless of intent matching.
///
/// `progress` is an optional callback driven during loading so a boot
/// spinner / TUI can show per-capability status alongside the standard MCP
/// init phase. Pass `None` for headless flows (ACP, WebSocket).
///
/// Failures are logged and skipped (never abort the session). Already-active
/// capabilities are no-ops. Use this from CLI / CI / non-interactive runs
/// that need a deterministic tool surface (e.g., `OCTOMIND_CAPABILITIES=cron,docker octomind run -r ...`).
pub async fn load_env_capabilities(config: &Config, progress: Option<&(dyn Fn(EnvCapabilityProgress) + Send + Sync)>) {
	let env_val = match std::env::var("OCTOMIND_CAPABILITIES") {
		Ok(v) if !v.trim().is_empty() => v,
		_ => return,
	};
	let names: Vec<String> = env_val
		.split(',')
		.map(|s| s.trim().to_string())
		.filter(|s| !s.is_empty())
		.collect();
	if names.is_empty() {
		return;
	}

	if let Some(cb) = progress {
		cb(EnvCapabilityProgress::Starting {
			capabilities: names.clone(),
		});
	}

	let suppress = crate::config::with_thread_config(|c| c.output_mode())
		.map(|m| m.should_suppress_cli_output())
		.unwrap_or(false);

	for name in &names {
		if is_active(name) {
			if let Some(cb) = progress {
				cb(EnvCapabilityProgress::Completed {
					capability: name.clone(),
					success: true,
				});
			}
			continue;
		}
		let call = crate::mcp::McpToolCall {
			tool_name: "capability".to_string(),
			tool_id: format!("env_{name}"),
			parameters: serde_json::json!({"action": "enable", "name": name}),
		};
		let success = match handle_enable(&call, config).await {
			Ok(result) if result.is_error() => {
				let msg = result.extract_content();
				if !suppress {
					eprintln!("OCTOMIND_CAPABILITIES: capability '{name}' failed: {msg}");
				} else {
					crate::log_debug!("OCTOMIND_CAPABILITIES: capability '{}' failed: {}", name, msg);
				}
				false
			}
			Ok(_) => {
				crate::log_debug!("OCTOMIND_CAPABILITIES: enabled capability '{}'", name);
				true
			}
			Err(e) => {
				if !suppress {
					eprintln!("OCTOMIND_CAPABILITIES: capability '{name}' failed: {e:#}");
				} else {
					crate::log_debug!("OCTOMIND_CAPABILITIES: capability '{}' failed: {:#}", name, e);
				}
				false
			}
		};
		if let Some(cb) = progress {
			cb(EnvCapabilityProgress::Completed {
				capability: name.clone(),
				success,
			});
		}
	}
}

/// Snapshot of currently-active capability names. Used by the boot flow to
/// print "Using capability: X" summary lines after env loading completes,
/// mirroring the per-skill summary lines.
pub fn list_active_names() -> Vec<String> {
	let mut names: Vec<String> = registry().read().unwrap().keys().cloned().collect();
	names.sort();
	names
}

/// Designed to run before every API request from `prepare_for_api_call`.
/// Does not block the hot path on model warmup — `is_ready` is consulted
/// first and skips silently while the model is still downloading.
pub async fn auto_activate_capabilities(session: &mut crate::session::chat::session::ChatSession, config: &Config) {
	// Fire only on a fresh user message. Tool-loop iterations are skipped.
	let intent = match session.session.messages.last() {
		Some(m) if crate::session::is_real_user_task_message(m) => m.content.clone(),
		_ => return,
	};

	let _ = auto_activate_capabilities_for_intent(&intent, config).await;
}

/// Trigger capability auto-activation for explicit intent text.
///
/// This is the same scoring path as fresh user-message activation, exposed for
/// runtime prompts that ask the session to load missing tools.
pub async fn auto_activate_capabilities_for_intent(intent: &str, config: &Config) -> Vec<String> {
	// Control-plane text (supervisor steers/recalls, skill replays, continuation
	// wrappers) is not a user intent — mirrors the same gate in
	// `skill_auto::run_activation`.
	if crate::session::is_system_managed_user_content(intent) {
		crate::log_debug!("capability auto-activate: skipping — system-managed content");
		return Vec::new();
	}

	// Strip XML blocks (skill injections, <log> pastes, system tags, etc.)
	// so pasted content doesn't drive false-positive capability matches.
	let intent = crate::mcp::runtime::skill_auto::strip_xml_blocks(intent);

	// Skip embedding + scoring entirely for short/empty inputs. Short
	// acknowledgments ("try", "ok", "do it") produce noisy embeddings that
	// can clear the threshold against an unrelated trigger by coincidence;
	// they also waste the embed call on no real intent. Mirrors the same
	// gate applied in `skill_auto::run_activation`.
	if !crate::mcp::runtime::skill_auto::intent_has_enough_signal(&intent) {
		crate::log_debug!(
			"capability auto-activate: skipping — intent below {} non-ws chars: {:?}",
			crate::mcp::runtime::skill_auto::MIN_INTENT_NON_WS_CHARS,
			intent
		);
		return Vec::new();
	}

	if !crate::embeddings::is_ready() {
		crate::log_debug!("capability auto-activate: embedding model not ready yet, skipping this turn");
		return Vec::new();
	}

	let caps = match crate::agent::registry::list_all_capabilities(&config.capabilities) {
		Ok(c) => c,
		Err(e) => {
			crate::log_debug!("capability auto-activate: enumeration failed ({})", e);
			return Vec::new();
		}
	};
	// Domain gate: skip out-of-domain caps before embedding their triggers.
	// Saves embed work AND prevents the gate from picking, say, medical-
	// reference for a `developer:general` user message that happens to score
	// well against medical-domain triggers.
	let caps = filter_caps_by_domain(caps);

	// Env readiness gate: filter out caps whose required env vars are not
	// set before scoring. Saves embedding compute on caps that can't
	// activate, and prevents the auto-activator from picking a cap that
	// would fail at activation time.
	let inactive: Vec<&crate::agent::registry::ResolvedCapability> = caps
		.iter()
		.filter(|c| {
			if is_active(&c.name) {
				return false;
			}
			if let Err(missing) = check_env_readiness(&c.required_env_keys) {
				crate::log_debug!(
					"capability auto-activate: filtering out '{}' — missing env vars: {}",
					c.name,
					missing.join(", ")
				);
				return false;
			}
			true
		})
		.collect();
	if inactive.is_empty() {
		return Vec::new();
	}

	let intent_vec = match crate::embeddings::embed(&intent).await {
		Ok(v) => v,
		Err(e) => {
			crate::log_debug!("capability auto-activate: intent embed failed ({})", e);
			return Vec::new();
		}
	};

	// Flatten all triggers into one batch to amortize the embed call.
	// `embed_many` caches by content hash, so subsequent turns are free.
	let mut flat: Vec<String> = Vec::new();
	let mut offsets: Vec<(usize, usize)> = Vec::with_capacity(inactive.len());
	for cap in &inactive {
		let start = flat.len();
		flat.extend(cap.triggers.iter().cloned());
		offsets.push((start, flat.len()));
	}
	if flat.is_empty() {
		return Vec::new();
	}

	let trigger_vecs = match crate::embeddings::embed_many(&flat).await {
		Ok(v) => v,
		Err(e) => {
			crate::log_debug!("capability auto-activate: trigger embed failed ({})", e);
			return Vec::new();
		}
	};

	let mut scored: Vec<(f32, &crate::agent::registry::ResolvedCapability)> = inactive
		.iter()
		.zip(offsets.iter())
		.map(|(cap, (start, end))| {
			let score = score_capability(&intent_vec, &trigger_vecs[*start..*end]);
			(score, *cap)
		})
		.collect();
	scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
	let preview: Vec<String> = scored
		.iter()
		.take(5)
		.map(|(s, c)| format!("{}={s:.3}", c.name))
		.collect();
	crate::log_debug!(
		"capability auto-activate: intent={:?} candidates={} threshold={} margin={} top5=[{}]",
		intent,
		scored.len(),
		AUTO_ACTIVATE_THRESHOLD,
		AUTO_ACTIVATE_MARGIN,
		preview.join(", ")
	);

	let top = select_with_margin(scored.clone(), AUTO_ACTIVATE_THRESHOLD, AUTO_ACTIVATE_MARGIN);
	if top.is_none() {
		let top1 = scored.first().map(|x| x.0).unwrap_or(0.0);
		let top2 = scored.get(1).map(|x| x.0).unwrap_or(0.0);
		let top1_name = scored.first().map(|x| x.1.name.as_str()).unwrap_or("<none>");
		let reason = if top1 < AUTO_ACTIVATE_THRESHOLD {
			format!("top1 {top1:.3} below threshold {:.3}", AUTO_ACTIVATE_THRESHOLD)
		} else {
			format!(
				"margin {:.3} below required {:.3} (top1={top1:.3} top2={top2:.3})",
				top1 - top2,
				AUTO_ACTIVATE_MARGIN
			)
		};
		crate::log_debug!(
			"capability auto-activate: no winner — {} (top1 was '{}')",
			reason,
			top1_name
		);
	}
	let top = evaluate_pick(config, &intent, top, &scored).await;

	if let Some((score, cap)) = top {
		match activate_capability_inline(&cap.name, config).await {
			Ok(servers) => {
				crate::log_info!(
					"· capability auto-activated: '{}' (score {:.2}) — servers: [{}]",
					cap.name,
					score,
					servers.join(", ")
				);
				return vec![cap.name.clone()];
			}
			Err(e) => {
				crate::log_debug!("capability auto-activate: failed to enable '{}' ({})", cap.name, e);
			}
		}
	}

	Vec::new()
}

/// The evaluation seam's say over the cosine decision: a cosine winner the
/// Choice gives under `CAPABILITY_CONFIRM_AT` is vetoed; with no winner, the
/// Choice's pick activates at `CAPABILITY_ACTIVATE_AT` or above, carrying its
/// probability as the score. The Choice lists the `CAPABILITY_TOP_K`
/// best-scored candidates (name and description) plus `none` over the
/// stripped intent. Off or unavailable, the cosine decision stands.
async fn evaluate_pick<'a>(
	config: &Config,
	intent: &str,
	cosine: Option<(f32, &'a crate::agent::registry::ResolvedCapability)>,
	ranked: &[(f32, &'a crate::agent::registry::ResolvedCapability)],
) -> Option<(f32, &'a crate::agent::registry::ResolvedCapability)> {
	use crate::supervisor::evaluate::{self, Seam};

	if !evaluate::enabled(config, Seam::Capabilities) {
		return cosine;
	}
	let candidates = &ranked[..ranked.len().min(evaluate::CAPABILITY_TOP_K)];
	let question = evaluate::capability_question(
		candidates
			.iter()
			.map(|(_, cap)| (cap.name.as_str(), cap.description.as_str())),
	);
	let questions = std::collections::BTreeMap::from([(evaluate::CAPABILITY_QUESTION_ID.to_string(), question)]);
	let Some(answers) = evaluate::run(
		config,
		Seam::Capabilities,
		serde_json::Value::String(intent.to_string()),
		questions,
	)
	.await
	else {
		return cosine;
	};
	// The runner answers every asked id with its own type or a refusal.
	let Some(octolib::evaluation::Answer::Choice {
		choice, probabilities, ..
	}) = answers.get(evaluate::CAPABILITY_QUESTION_ID)
	else {
		evaluate::unavailable(Seam::Capabilities, "refused");
		return cosine;
	};
	let cosine_name = cosine.map(|(_, cap)| cap.name.as_str());
	let names: Vec<&str> = candidates.iter().map(|(_, cap)| cap.name.as_str()).collect();
	let verdict = match apply_capability_answer(cosine_name, &names, choice, probabilities) {
		Ok(verdict) => verdict,
		Err(reason) => {
			evaluate::unavailable(Seam::Capabilities, reason);
			return cosine;
		}
	};
	let probability = |name: &str| probabilities.get(name).copied().unwrap_or(0.0);
	let picked = match verdict {
		Some(name) if cosine_name == Some(name) => cosine,
		Some(name) => candidates
			.iter()
			.find(|(_, cap)| cap.name == name)
			.map(|(_, cap)| (probability(name) as f32, *cap)),
		None => None,
	};
	if picked.map(|(_, cap)| cap.name.as_str()) != cosine_name {
		crate::log_debug!(
			"capability evaluate: {} → {} (choice '{}' p={:.2})",
			cosine_name.unwrap_or("none"),
			picked.map(|(_, cap)| cap.name.as_str()).unwrap_or("none"),
			choice,
			probability(choice)
		);
		crate::supervisor::stats::evaluate_applied(Seam::Capabilities, 1);
	}
	picked
}

/// Apply the seam's rule to one Choice answer: `cosine` is the cosine
/// winner's name, `candidates` the names the Choice listed. `Ok(Some)` is the
/// capability to activate, `Ok(None)` an abstain; `Err` names a choice that
/// was never offered. Pure, so the two floors are unit-testable.
fn apply_capability_answer<'a>(
	cosine: Option<&'a str>,
	candidates: &[&'a str],
	choice: &str,
	probabilities: &std::collections::BTreeMap<String, f64>,
) -> Result<Option<&'a str>, &'static str> {
	use crate::supervisor::evaluate::{CAPABILITY_ACTIVATE_AT, CAPABILITY_CONFIRM_AT, CAPABILITY_NONE};

	let probability = |name: &str| probabilities.get(name).copied().unwrap_or(0.0);
	if choice != CAPABILITY_NONE && !candidates.contains(&choice) {
		return Err("invalid response");
	}
	Ok(match cosine {
		Some(winner) => (probability(winner) >= CAPABILITY_CONFIRM_AT).then_some(winner),
		None => candidates
			.iter()
			.copied()
			.find(|name| *name == choice && probability(name) >= CAPABILITY_ACTIVATE_AT),
	})
}

/// Translate capability `allowed_tools` patterns into the bare-name
/// patterns `enable_server` expects, for one server.
///
/// Capability TOMLs use a namespaced convention (`<server>:<tool>` or
/// `<server>:*`) so a single capability config can scope tools across
/// multiple MCP servers. The actual tool names returned by an MCP
/// server are bare (`browser_navigate`, not `playwright:browser_navigate`),
/// so we strip the prefix here. Rules:
///
/// - `<server_name>:<rest>` → `<rest>` (applies to this server)
/// - `<other>:<...>` → dropped (pattern is for a different server)
/// - `<bare_name_or_glob>` → unchanged (applies to all servers in cap)
///
/// Returns `None` when the input list is empty (no filter ⇒ all tools)
/// or all patterns are scoped to other servers (also "no filter for me",
/// expose all). Returns `Some(...)` only when at least one pattern
/// genuinely scopes this server.
fn filter_for_server(allowed_tools: &[String], server_name: &str) -> Option<Vec<String>> {
	if allowed_tools.is_empty() {
		return None;
	}
	let prefix = format!("{server_name}:");
	let kept: Vec<String> = allowed_tools
		.iter()
		.filter_map(|p| {
			if let Some(rest) = p.strip_prefix(&prefix) {
				Some(rest.to_string())
			} else if p.contains(':') {
				None
			} else {
				Some(p.clone())
			}
		})
		.collect();
	if kept.is_empty() {
		None
	} else {
		Some(kept)
	}
}

/// Concrete tool names a capability grants on a server the role's static
/// config already runs. The tool map and the runtime overlay hold real names,
/// so globs (`orchestration:*` → `*`) are expanded against the server's full
/// tool list — registering the pattern itself maps a tool literally named `*`
/// while the static filter keeps hiding the grant. No filter means the
/// capability grants every tool on the server.
async fn static_server_grant(
	server: &crate::config::McpServerConfig,
	filter: Option<Vec<String>>,
	config: &Config,
) -> Vec<String> {
	let (globs, mut names): (Vec<String>, Vec<String>) = filter
		.unwrap_or_else(|| vec!["*".to_string()])
		.into_iter()
		.partition(|pattern| pattern.ends_with('*'));
	if globs.is_empty() {
		return names;
	}
	let mut unfiltered = server.clone();
	unfiltered.tools_mut().clear();
	for function in crate::mcp::server_functions_for(&unfiltered, config).await {
		if !names.contains(&function.name) && crate::mcp::is_tool_allowed_by_patterns(&function.name, &globs) {
			names.push(function.name);
		}
	}
	names
}

/// Register + enable a capability's MCP servers and mark the capability
/// active. Mirrors `handle_enable`'s logic minus the `McpToolResult`
/// wrapping — errors propagate as `anyhow::Error` for the caller to log
/// Check that all required env keys are set and non-empty.
/// Returns Ok(()) if all present, Err(missing_keys) listing the unset ones.
pub(crate) fn check_env_readiness(required: &[String]) -> Result<(), Vec<String>> {
	let missing: Vec<String> = required
		.iter()
		.filter(|key| std::env::var(key).map(|v| v.is_empty()).unwrap_or(true))
		.cloned()
		.collect();
	if missing.is_empty() {
		Ok(())
	} else {
		Err(missing)
	}
}

/// or surface. Idempotent: returns `Ok(empty)` when already active.
async fn activate_capability_inline(name: &str, config: &Config) -> Result<Vec<String>> {
	if is_active(crate::agent::registry::capability_bare_name(name)) {
		return Ok(Vec::new());
	}
	let resolved = crate::agent::registry::parse_capability_toml(name, &config.capabilities)?;
	if let Err(missing) = check_env_readiness(&resolved.required_env_keys) {
		crate::log_debug!(
			"capability activation: skipping '{}' — missing env vars: {}",
			name,
			missing.join(", ")
		);
		anyhow::bail!(
			"capability '{}' requires env vars: {} — set them before activating",
			name,
			missing.join(", ")
		);
	}
	// Deps-only capability: activation installs its toolchain. Mirrors
	// `handle_enable` so auto-activation and manual `enable` behave the same.
	if resolved.mcp_servers.is_empty() {
		if resolved.deps.is_empty() {
			anyhow::bail!("capability '{}' has no [[mcp.servers]] and no [deps]", name);
		}
		evict_lru_if_full(config);
		crate::agent::deps::run_dep_entries(&resolved.deps, &resolved.tap_root, None)
			.await
			.with_context(|| format!("dep install failed for capability '{name}'"))?;
		mark_active(&resolved.name, Vec::new());
		return Ok(Vec::new());
	}
	// Make room before activating — drops the LRU active capability if
	// we'd exceed `MAX_ACTIVE_CAPS`. No-op when below the cap.
	evict_lru_if_full(config);

	let mut activated_servers: Vec<String> = Vec::new();
	let mut activated_server_tools: Vec<(String, Vec<String>)> = Vec::new();
	let mut overlay_per_server: std::collections::HashMap<String, Vec<String>> = std::collections::HashMap::new();
	for server in &resolved.mcp_servers {
		let server_name = server.name().to_string();
		let filter = filter_for_server(&resolved.allowed_tools, &server_name);

		// Server already provided by the role's static config — extend
		// rather than re-register. Mirrors the static-server branch
		// in `handle_enable`. The overlay extends the role's per-server
		// filter at next merge; tool_map registration makes named tools
		// dispatchable now.
		if let Some(server_config) = config.mcp.servers.iter().find(|s| s.name() == server_name) {
			let bare_names = static_server_grant(server_config, filter, config).await;
			if !bare_names.is_empty() {
				crate::mcp::tool_map::register_dynamic_server_tools(&server_name, server_config, &bare_names);
				crate::mcp::server::clear_function_cache_for_server(&server_name);
				overlay_per_server.insert(server_name.clone(), bare_names.clone());
			}
			activated_server_tools.push((server_name.clone(), bare_names));
			activated_servers.push(server_name);
			continue;
		}

		if !crate::mcp::runtime::dynamic::is_dynamic(&server_name) {
			crate::mcp::runtime::dynamic::register_server(server.clone())?;
		}
		let functions = crate::mcp::runtime::dynamic::enable_server(&server_name, filter).await?;
		let bare_names: Vec<String> = functions.iter().map(|f| f.name.clone()).collect();
		activated_server_tools.push((server_name.clone(), bare_names));
		activated_servers.push(server_name);
	}

	crate::config::runtime_overlay::set_capability_extras(&resolved.name, overlay_per_server);
	mark_active(&resolved.name, activated_server_tools);
	Ok(activated_servers)
}

async fn handle_discover(call: &McpToolCall, config: &Config) -> Result<McpToolResult> {
	let intent = match call.parameters.get("intent").and_then(|v| v.as_str()) {
		Some(i) if !i.trim().is_empty() => i.trim().to_string(),
		_ => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				"Missing required parameter 'intent'".to_string(),
			));
		}
	};

	let caps = match crate::agent::registry::list_all_capabilities(&config.capabilities) {
		Ok(c) => c,
		Err(e) => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!("Failed to enumerate capabilities: {e}"),
			));
		}
	};
	let caps = filter_caps_by_domain(caps);

	if caps.is_empty() {
		return Ok(McpToolResult::success(
			call.tool_name.clone(),
			call.tool_id.clone(),
			"No capabilities installed in any tap.".to_string(),
		));
	}

	// Embedding-only — same scoring pipeline as auto-activation, just with
	// the threshold/margin gate replaced by "return top 5". No keyword
	// fallback: capability authors give us hand-authored triggers, the
	// SOTA path runs always.
	let scored = match score_caps_by_triggers(&intent, &caps).await {
		Ok(s) => s,
		Err(e) => {
			return Ok(McpToolResult::error(
				call.tool_name.clone(),
				call.tool_id.clone(),
				format!(
					"Capability discover requires the embedding model. Init failed: {e}. \
					 If the model is still downloading, retry in a moment."
				),
			));
		}
	};

	let top: Vec<_> = scored.into_iter().take(5).collect();
	if top.is_empty() {
		return Ok(McpToolResult::success(
			call.tool_name.clone(),
			call.tool_id.clone(),
			format!(
				"No capabilities matched intent '{intent}'. Try `capability list` to see all installed capabilities."
			),
		));
	}

	let mut output = format!("Capabilities matching '{intent}':\n");
	for (score, cap) in top {
		let marker = if is_active(&cap.name) { "[active] " } else { "" };
		output.push_str(&format!(
			"- {}{} (score {:.2}) — {}\n",
			marker,
			cap.name,
			score,
			triggers_preview(&cap.triggers)
		));
	}
	output.push_str("\nUse capability(action=\"enable\", name=\"<name>\") to activate.");
	Ok(McpToolResult::success(
		call.tool_name.clone(),
		call.tool_id.clone(),
		output,
	))
}

/// Score every capability by mean-of-top-K cosine over its triggers —
/// the same pipeline `auto_activate_capabilities` uses, just without the
/// threshold/margin gate. Returns capabilities sorted by score descending,
/// filtered to scores above a low noise floor (0.2) so empty intents
/// don't pull every capability into the result.
async fn score_caps_by_triggers<'a>(
	intent: &str,
	caps: &'a [crate::agent::registry::ResolvedCapability],
) -> Result<Vec<(f32, &'a crate::agent::registry::ResolvedCapability)>> {
	let intent_vec = crate::embeddings::embed(intent).await?;

	let mut flat: Vec<String> = Vec::new();
	let mut offsets: Vec<(usize, usize)> = Vec::with_capacity(caps.len());
	for cap in caps {
		let start = flat.len();
		flat.extend(cap.triggers.iter().cloned());
		offsets.push((start, flat.len()));
	}
	let trigger_vecs = crate::embeddings::embed_many(&flat).await?;

	let mut scored: Vec<(f32, &crate::agent::registry::ResolvedCapability)> = caps
		.iter()
		.zip(offsets.iter())
		.map(|(cap, (start, end))| {
			let score = score_capability(&intent_vec, &trigger_vecs[*start..*end]);
			(score, cap)
		})
		.filter(|(score, _)| *score > 0.2)
		.collect();
	scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
	Ok(scored)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
#[path = "capability_inline_tests.rs"]
mod inline_tests;

#[cfg(test)]
#[path = "capability_command_tests.rs"]
mod command_tests;

```

## src/providers.rs

Source: https://github.com/Muvon/octomind/blob/4cc5600403885e1d9bc2cba6750f5c3c42dbed2f/src/providers.rs

SHA-256: `2298880766fc4f75f7ee0ef38f6cc73a70f59cdcfb6d81b865af38681978c7bf`

```
// Copyright 2026 Muvon Un Limited
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! Provider abstraction layer - now powered by octolib
//!
//! This module serves as an adapter between Octomind and the octolib provider system.
//! It maintains backward compatibility while leveraging the self-sufficient octolib crate.

use crate::config::Config;
use crate::session::Message;
use tokio::sync::watch;

// Re-export octolib types with compatibility aliases
pub use octolib::llm::{
	AiProvider, AmazonBedrockProvider, AnthropicProvider, CloudflareWorkersAiProvider, DeepSeekProvider,
	GenericToolCall, GoogleVertexProvider, OpenAiProvider, OpenRouterProvider, ProviderFactory,
	StructuredOutputRequest,
};

// Re-export some octolib types directly
pub use octolib::llm::{ModelPricing, ProviderExchange, ThinkingBlock, TokenUsage};

// Define Octomind-specific ProviderResponse that uses McpToolCall
#[derive(Debug, Clone)]
pub struct ProviderResponse {
	pub content: String,
	pub exchange: ProviderExchange,
	pub tool_calls: Option<Vec<crate::mcp::McpToolCall>>,
	pub thinking: Option<ThinkingBlock>,
	pub finish_reason: Option<String>,
	pub response_id: Option<String>,
	pub structured_output: Option<serde_json::Value>,
}

/// The header carrying [`ModelPurpose`] to the octohub proxy.
pub const MODEL_PURPOSE_HEADER: &str = "X-Model-Purpose";

/// Where in octomind a model call originates. Sent on every completion as the
/// `X-Model-Purpose` header so octohub's virtual `auto` model can route each
/// purpose to a different real model; providers that aren't octohub ignore it.
///
/// This set is a CONTRACT with the control plane. Internal mechanics sharing a
/// profile also share one purpose, so a proxy cannot silently reintroduce
/// per-mechanic model routing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelPurpose {
	/// The session's own conversation turns — also cache keepalive pings,
	/// which must hit the same model they are keeping warm.
	#[default]
	Main,
	/// Gate, resolve, plan, and tool-output condensation.
	Supervisor,
	/// Conversation-compression decisions and summaries.
	Compression,
}

impl ModelPurpose {
	pub fn as_str(&self) -> &'static str {
		match self {
			Self::Main => "main",
			Self::Supervisor => "supervisor",
			Self::Compression => "compression",
		}
	}
}

// Keep the original ChatCompletionParams for backward compatibility
/// Parameters for chat completion requests (Octomind version)
///
/// This struct maintains the original Octomind API while adapting to octolib internally.
#[derive(Clone)]
pub struct ChatCompletionParams<'a> {
	/// Array of conversation messages
	pub messages: &'a [Message],
	/// Model identifier (e.g., "claude-3-5-sonnet", "gpt-4")
	pub model: &'a str,
	/// Sampling temperature (0.0 to 2.0)
	pub temperature: f32,
	/// Top-p nucleus sampling (0.0 to 1.0)
	pub top_p: f32,
	/// Top-k sampling (1 to infinity)
	pub top_k: u32,
	/// Maximum tokens to generate (0 = no limit)
	pub max_tokens: u32,
	/// Maximum retry attempts on failure
	pub max_retries: u32,
	/// Base timeout for exponential backoff retry logic
	pub retry_timeout: std::time::Duration,
	/// Total deadline per HTTP or WebSocket request attempt. None disables it.
	pub request_timeout: Option<std::time::Duration>,
	/// SSE/WebSocket response inactivity limit. None disables it.
	pub stream_timeout: Option<std::time::Duration>,
	/// Persistent-connection transport for octohub and chatgpt; other providers ignore it.
	pub request_transport: crate::config::RequestTransportConfig,
	/// Configuration object
	pub config: &'a Config,
	/// Cancellation token for request abortion
	pub cancellation_token: Option<watch::Receiver<bool>>,
	/// Optional JSON schema for structured output
	pub schema: Option<serde_json::Value>,
	/// Optional reasoning effort override (falls back to `config.reasoning_effort`)
	pub reasoning_effort: Option<crate::config::ReasoningEffortConfig>,
	/// Attach MCP tools to the request (default true). Text-only internal
	/// calls (compression, learning extraction) disable this: the model never
	/// calls tools there, the definitions waste input tokens, and their
	/// presence blocks schema enforcement on proxy providers.
	pub tools: bool,
	/// Send image attachments (default true). Callers clear this for models that
	/// cannot read images: history keeps tool-result screenshots and images from
	/// earlier vision models, and a text-only model rejects the whole request.
	pub vision: bool,
	/// Where this call originates (main | supervisor | compression) — becomes
	/// the `X-Model-Purpose` header. Defaults to Main.
	pub purpose: ModelPurpose,
	/// Cache pre-warm request (keepalive): refresh the prompt cache TTL without
	/// generating output. Only providers with a keepalive policy honor it.
	pub cache_prewarm: bool,
}

impl<'a> ChatCompletionParams<'a> {
	/// Create new chat completion parameters
	pub fn new(
		messages: &'a [Message],
		model: &'a str,
		temperature: f32,
		top_p: f32,
		top_k: u32,
		max_tokens: u32,
		config: &'a Config,
	) -> Self {
		Self {
			messages,
			model,
			temperature,
			top_p,
			top_k,
			max_tokens,
			max_retries: config.max_retries,
			retry_timeout: std::time::Duration::from_secs(config.retry_timeout),
			request_timeout: match config.request_timeout_seconds {
				0 => None,
				n => Some(std::time::Duration::from_secs(n)),
			},
			stream_timeout: match config.stream_timeout_seconds {
				0 => None,
				n => Some(std::time::Duration::from_secs(n)),
			},
			request_transport: config.request_transport,
			config,
			cancellation_token: None,
			schema: None,
			reasoning_effort: None,
			tools: true,
			vision: true,
			purpose: ModelPurpose::default(),
			cache_prewarm: false,
		}
	}

	/// Set maximum retry attempts
	pub fn with_max_retries(mut self, max_retries: u32) -> Self {
		self.max_retries = max_retries;
		self
	}

	pub fn with_retry_timeout(mut self, retry_timeout: std::time::Duration) -> Self {
		self.retry_timeout = retry_timeout;
		self
	}

	pub fn with_request_timeout(mut self, request_timeout: Option<std::time::Duration>) -> Self {
		self.request_timeout = request_timeout;
		self
	}

	pub fn with_stream_timeout(mut self, stream_timeout: Option<std::time::Duration>) -> Self {
		self.stream_timeout = stream_timeout;
		self
	}

	pub fn with_request_transport(mut self, request_transport: crate::config::RequestTransportConfig) -> Self {
		self.request_transport = request_transport;
		self
	}

	/// Set cancellation token
	pub fn with_cancellation_token(mut self, token: watch::Receiver<bool>) -> Self {
		self.cancellation_token = Some(token);
		self
	}

	/// Set JSON schema for structured output
	pub fn with_schema(mut self, schema: serde_json::Value) -> Self {
		self.schema = Some(schema);
		self
	}

	/// Override reasoning effort for this call (otherwise inherits from config).
	pub fn with_reasoning_effort(mut self, effort: crate::config::ReasoningEffortConfig) -> Self {
		self.reasoning_effort = Some(effort);
		self
	}

	/// Don't attach MCP tools — for text-only calls (compression, learning).
	pub fn without_tools(mut self) -> Self {
		self.tools = false;
		self
	}

	/// Whether the target model can read image attachments.
	pub fn with_vision(mut self, vision: bool) -> Self {
		self.vision = vision;
		self
	}

	/// Tag this call's origin for purpose-based routing (octohub `auto`).
	pub fn with_purpose(mut self, purpose: ModelPurpose) -> Self {
		self.purpose = purpose;
		self
	}

	/// Send as a cache pre-warm: bills the cache read only, no output.
	pub fn with_cache_prewarm(mut self) -> Self {
		self.cache_prewarm = true;
		self
	}

	/// Convert to octolib ChatCompletionParams with MCP tools
	pub async fn to_octolib_params(
		&self,
		provider_name: &str,
	) -> Result<octolib::llm::ChatCompletionParams, octolib::MessageError> {
		let octolib_messages: Result<Vec<octolib::llm::Message>, _> =
			self.messages.iter().map(convert_message_to_octolib).collect();

		let mut octolib_messages = octolib_messages?;

		if !self.vision {
			for message in &mut octolib_messages {
				message.images = None;
			}
		}

		// Long cache TTL on system message — always enabled (Anthropic 1h cache).
		if let Some(sys_msg) = octolib_messages.iter_mut().find(|m| m.role == "system" && m.cached) {
			sys_msg.cache_ttl = Some("1h".to_string());
		}

		// Some providers (e.g. Gemini, Mistral) require the last message to be from the user.
		// After conversation compression the last message can be an assistant summary, which
		// causes those providers to return an error.  Appending a lightweight "Please continue."
		// user message is the safest fix: it satisfies the constraint without altering session
		// state and is semantically neutral (the model simply continues from where it left off).
		let last_non_system_is_assistant = octolib_messages
			.iter()
			.rev()
			.find(|m| m.role != "system")
			.map(|m| m.role == "assistant")
			.unwrap_or(false);

		if last_non_system_is_assistant {
			crate::log_debug!(
				"Last message is assistant after compression - appending synthetic user message to satisfy provider requirements"
			);
			let synthetic = octolib::llm::MessageBuilder::user("Please continue.")
				.build()
				.map_err(|_| octolib::MessageError::InvalidRole {
					role: "synthetic_user".to_string(),
				})?;
			octolib_messages.push(synthetic);
		}

		let mut params = octolib::llm::ChatCompletionParams::new(
			&octolib_messages,
			self.model,
			self.temperature,
			self.top_p,
			self.top_k,
			self.max_tokens,
		)
		.with_max_retries(self.max_retries)
		.with_retry_timeout(self.retry_timeout)
		.with_request_timeout(self.request_timeout)
		.with_stream_timeout(self.stream_timeout)
		.with_long_cache(true)
		.with_cache_prewarm(self.cache_prewarm)
		.with_reasoning_effort(
			self.reasoning_effort
				.unwrap_or(self.config.reasoning_effort)
				.to_octolib(),
		)
		// Sent on every request: only the octohub proxy interprets it (for the
		// virtual `auto` model); other providers ignore an unknown X- header.
		.with_extra_headers(std::collections::HashMap::from([(
			MODEL_PURPOSE_HEADER.to_string(),
			self.purpose.as_str().to_string(),
		)]));

		if provider_name == "chatgpt" {
			params = params.with_chatgpt_transport(self.request_transport.to_chatgpt());
		}

		if provider_name == "octohub" {
			params = params.with_octohub_transport(self.request_transport.to_octohub());
		}

		if let Some(token) = &self.cancellation_token {
			params = params.with_cancellation_token(token.clone());
		}

		let session_id = crate::session::context::current_session_id();

		// A failing provider is retried with backoff sleeps that add up to minutes;
		// tell the user each one instead of going silent. Routed like MCP
		// notifications — the session's WebSocket, or the CLI drain (spinner
		// line, stderr, JSONL) — with the session captured here because the call
		// may run outside the task-local scope.
		let retry_session_id = session_id.clone();
		let retry_provider = provider_name.to_string();
		let retry_model = self.model.to_string();
		let retry_purpose = self.purpose;
		params = params.with_retry_observer(octolib::llm::RetryObserver::new(move |event| {
			tracing::warn!(
				"{} model {}:{} request failed ({}), retry {}/{} in {:?}",
				retry_purpose.as_str(),
				retry_provider,
				retry_model,
				event.reason,
				event.attempt,
				event.max_retries,
				event.delay
			);
			crate::mcp::process::send_notification_message_for(
				crate::websocket::ServerMessage::retry(
					event,
					&retry_provider,
					&retry_model,
					retry_purpose,
					retry_session_id.clone(),
				),
				retry_session_id.as_ref(),
			);
		}));

		// OpenAI-family providers route prompt caching by this key; without it the
		// turns of one session miss the cache (0 of 11 turns hit on chatgpt).
		if let Some(session_id) = session_id {
			params = params.with_prompt_cache_key(session_id);
		}

		// Fetch and add MCP tools if MCP is configured
		if self.tools && !self.config.mcp.servers.is_empty() {
			let mcp_functions = crate::mcp::get_available_functions(self.config).await;
			if !mcp_functions.is_empty() {
				// Convert MCP functions to octolib FunctionDefinitions
				let mut octolib_tools: Vec<octolib::llm::FunctionDefinition> = mcp_functions
					.into_iter()
					.map(|f| octolib::llm::FunctionDefinition {
						name: f.name,
						description: f.description,
						parameters: f.parameters,
						cache_control: None, // Will be set below if needed
					})
					.collect();

				// Add cache control to the LAST tool if system message is cached
				// This matches the old Anthropic provider behavior
				let system_cached = self.messages.iter().any(|m| m.role == "system" && m.cached);
				if system_cached && !octolib_tools.is_empty() {
					if let Some(last_tool) = octolib_tools.last_mut() {
						last_tool.cache_control = Some(serde_json::json!({
							"type": "ephemeral",
							"ttl": "1h"
						}));
					}
				}

				params = params.with_tools(octolib_tools);
			}
		}

		// Apply structured output schema if provided
		if let Some(ref schema) = self.schema {
			params =
				params.with_structured_output(StructuredOutputRequest::json_schema(schema.clone()).with_strict_mode());
		}

		Ok(params)
	}
}

/// Convert Octomind Message to octolib Message with proper error handling
fn convert_message_to_octolib(msg: &Message) -> Result<octolib::llm::Message, octolib::MessageError> {
	let mut builder = match msg.role.as_str() {
		"user" => octolib::llm::MessageBuilder::user(&msg.content),
		"assistant" => {
			let mut builder = octolib::llm::MessageBuilder::assistant(&msg.content);
			// CRITICAL: Convert tool_calls to unified GenericToolCall format.
			// Malformed shapes return a typed error (see convert_to_generic_tool_calls)
			// rather than panicking, so a misbehaving model fails the request
			// cleanly instead of bringing the whole process down.
			if let Some(ref tool_calls) = msg.tool_calls {
				let generic_calls = convert_to_generic_tool_calls(tool_calls)?;
				if !generic_calls.is_empty() {
					builder = builder.with_tool_calls(generic_calls);
				}
			}
			builder
		}
		"system" => octolib::llm::MessageBuilder::system(&msg.content),
		"tool" => {
			let tool_call_id = msg
				.tool_call_id
				.as_deref()
				.ok_or_else(|| octolib::MessageError::MissingToolField {
					field: "tool_call_id".to_string(),
				})?;
			let name = msg
				.name
				.as_deref()
				.ok_or_else(|| octolib::MessageError::MissingToolField {
					field: "name".to_string(),
				})?;
			octolib::llm::MessageBuilder::tool(msg.content.clone(), tool_call_id.to_string(), name.to_string())
		}
		_ => return Err(octolib::MessageError::InvalidRole { role: msg.role.clone() }),
	};

	// Set timestamp
	builder = builder.timestamp(msg.timestamp);

	// Set message ID if present (for assistant messages with tool calls)
	if let Some(ref id) = msg.id {
		builder = builder.id(id);
	}

	// Set cache marker and TTL if needed
	if msg.cached {
		builder = builder.cached();
		if let Some(ref ttl) = msg.cache_ttl {
			builder = builder.cache_ttl(ttl);
		}
	}

	// Convert images if present
	if let Some(images) = &msg.images {
		let octolib_images: Vec<octolib::llm::ImageAttachment> = images.iter().map(convert_image_to_octolib).collect();
		builder = builder.with_images(octolib_images);
	}

	// Convert videos if present
	if let Some(videos) = &msg.videos {
		let octolib_videos: Vec<octolib::llm::VideoAttachment> = videos.iter().map(convert_video_to_octolib).collect();
		builder = builder.with_videos(octolib_videos);
	}

	// CRITICAL FIX: Convert thinking field for Moonshot and other thinking models
	// Moonshot requires reasoning_content for assistant messages with tool_calls
	// The thinking field is stored as serde_json::Value, convert to ThinkingBlock
	if let Some(ref thinking_value) = msg.thinking {
		match serde_json::from_value::<octolib::ThinkingBlock>(thinking_value.clone()) {
			Ok(thinking_block) => {
				builder = builder.thinking(thinking_block);
			}
			Err(e) => {
				// Only log failures - success is expected and too verbose
				crate::log_debug!(
					"Failed to deserialize thinking field for {} message: {}. Value: {:?}",
					msg.role,
					e,
					thinking_value
				);
			}
		}
	}

	let mut message = builder.build()?;
	message.provider_items.clone_from(&msg.provider_items);
	Ok(message)
}

/// Convert Octomind ImageAttachment to octolib ImageAttachment
fn convert_image_to_octolib(img: &crate::session::image::ImageAttachment) -> octolib::llm::ImageAttachment {
	let data = match &img.data {
		crate::session::image::ImageData::Base64(data) => octolib::llm::ImageData::Base64(data.clone()),
		crate::session::image::ImageData::Url(url) => octolib::llm::ImageData::Url(url.clone()),
	};

	let source_type = match &img.source_type {
		crate::session::image::SourceType::File(path) => octolib::llm::SourceType::File(path.clone()),
		crate::session::image::SourceType::Clipboard => octolib::llm::SourceType::Clipboard,
		crate::session::image::SourceType::Url => octolib::llm::SourceType::Url,
	};

	octolib::llm::ImageAttachment {
		data,
		media_type: img.media_type.clone(),
		source_type,
		dimensions: img.dimensions,
		size_bytes: img.size_bytes,
	}
}

/// Convert Octomind VideoAttachment to octolib VideoAttachment
fn convert_video_to_octolib(video: &crate::session::video::VideoAttachment) -> octolib::llm::VideoAttachment {
	let data = match &video.data {
		crate::session::video::VideoData::Base64(data) => octolib::llm::VideoData::Base64(data.clone()),
		crate::session::video::VideoData::Url(url) => octolib::llm::VideoData::Url(url.clone()),
	};

	let source_type = match &video.source_type {
		crate::session::video::SourceType::File(path) => octolib::llm::SourceType::File(path.clone()),
		crate::session::video::SourceType::Clipboard => octolib::llm::SourceType::Clipboard,
		crate::session::video::SourceType::Url => octolib::llm::SourceType::Url,
	};

	octolib::llm::VideoAttachment {
		data,
		media_type: video.media_type.clone(),
		source_type,
		dimensions: video.dimensions,
		size_bytes: video.size_bytes,
		duration_secs: video.duration_secs,
	}
}

/// Convert tool_calls from session format to unified GenericToolCall format.
///
/// Session loading reconstructs tool_calls in OpenAI format. This function converts
/// them to the unified GenericToolCall format that octolib requires. Hostile or
/// buggy model output that doesn't match an expected shape returns a typed error
/// so the request fails cleanly — it must NOT crash the long-running process.
pub(crate) fn convert_to_generic_tool_calls(
	tool_calls: &serde_json::Value,
) -> Result<Vec<octolib::llm::GenericToolCall>, octolib::MessageError> {
	// Check if it's already in unified GenericToolCall format
	if let Ok(calls) = serde_json::from_value::<Vec<octolib::llm::GenericToolCall>>(tool_calls.clone()) {
		return Ok(calls);
	}

	// Handle OpenAI format (array with "type": "function") - from session loading
	if let Some(calls_array) = tool_calls.as_array() {
		let mut generic_calls = Vec::new();
		for call in calls_array {
			let function = call
				.get("function")
				.ok_or_else(|| octolib::MessageError::MissingToolField {
					field: "function".to_string(),
				})?;

			let (id, name, args_str) = match (
				call.get("id").and_then(|v| v.as_str()),
				function.get("name").and_then(|v| v.as_str()),
				function.get("arguments").and_then(|v| v.as_str()),
			) {
				(Some(id), Some(name), Some(args)) => (id, name, args),
				_ => {
					return Err(octolib::MessageError::MissingToolField {
						field: "function.{id|name|arguments}".to_string(),
					})
				}
			};

			// Parse arguments string to JSON. `ToolCallsError` wraps the
			// underlying serde_json::Error via `#[from]`, surfacing the
			// exact parse failure to the caller.
			let arguments = if args_str.trim().is_empty() {
				serde_json::json!({})
			} else {
				serde_json::from_str::<serde_json::Value>(args_str)?
			};

			// Root-level meta, preserved exactly like the unified-format
			// branch above; absent or non-object meta stays None.
			let meta = call.get("meta").and_then(|m| m.as_object()).cloned();

			generic_calls.push(octolib::llm::GenericToolCall {
				id: id.to_string(),
				name: name.to_string(),
				arguments,
				meta,
			});
		}
		return Ok(generic_calls);
	}

	Err(octolib::MessageError::MissingToolField {
		field: "tool_calls (root must be Vec<GenericToolCall> or OpenAI array)".to_string(),
	})
}

/// Convert octolib ProviderResponse to Octomind ProviderResponse
pub fn convert_response_from_octolib(response: octolib::llm::ProviderResponse) -> ProviderResponse {
	// Convert tool calls if present
	let tool_calls = response.tool_calls.map(|calls| {
		calls
			.into_iter()
			.map(|call| crate::mcp::McpToolCall {
				tool_name: call.name,
				tool_id: call.id,
				parameters: call.arguments,
			})
			.collect()
	});

	ProviderResponse {
		content: response.content,
		exchange: response.exchange,
		tool_calls,
		thinking: response.thinking,
		finish_reason: response.finish_reason,
		response_id: response.id,
		structured_output: response.structured_output,
	}
}

// Keep the retry module for backward compatibility
pub mod retry {
	pub use octolib::llm::retry::*;
}

#[cfg(test)]
#[path = "providers_tests.rs"]
mod tests;

```

## src/session/chat/response/tool_result_processor.rs

Source: https://github.com/Muvon/octomind/blob/4cc5600403885e1d9bc2cba6750f5c3c42dbed2f/src/session/chat/response/tool_result_processor.rs

SHA-256: `051ca90a3f638be19080888286a74d27a33d44ba137dd6686d1c7ec6e2eeaa9a`

```
// Copyright 2026 Muvon Un Limited
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Tool result processor module - handles tool result processing, caching, and follow-up API calls

use crate::config::Config;
use crate::session::chat::session::ChatSession;
use crate::session::ChatCompletionWithValidationParams;
use crate::{log_debug, log_info};
use anyhow::Result;
use colored::Colorize;

// Process tool results and handle follow-up API calls
pub async fn process_tool_results(
	tool_results: Vec<crate::mcp::McpToolResult>,
	total_tool_time_ms: u64,
	chat_session: &mut ChatSession,
	config: &Config,
	role: &str,
	operation_cancelled: tokio::sync::watch::Receiver<bool>,
) -> Result<
	Option<(
		String,
		crate::session::ProviderExchange,
		Option<Vec<crate::mcp::McpToolCall>>,
		Option<String>,                          // response_id from follow-up API call
		Option<crate::providers::ThinkingBlock>, // thinking from follow-up API call
	)>,
> {
	// Add the accumulated tool execution time to the session total
	chat_session.session.info.total_tool_time_ms += total_tool_time_ms;

	// Check for cancellation before making another request
	if *operation_cancelled.borrow() {
		crate::log_debug!("Operation cancelled by user.");
		// Do NOT add any confusing message to the session
		return Ok(None);
	}

	// Start animation (uses state already set by api_executor.rs)
	// CRITICAL FIX: Don't recalculate animation parameters here to avoid flickering
	// Animation state is set once at request start in api_executor.rs and remains stable
	use crate::session::chat::get_animation_manager;
	let animation_manager = get_animation_manager();

	// DEFENSE: Don't start animation if suspended (e.g., during user prompt)
	// This prevents animation from covering prompts in race conditions
	if !animation_manager.is_suspended() {
		animation_manager
			.start_animation(
				&crate::config::with_thread_config(|c| c.output_mode())
					.unwrap_or(crate::session::output::OutputMode::NonInteractive),
			)
			.await;
	}

	// 🔍 PERFORMANCE DEBUG: Track where time is spent during tool result processing
	let processing_start = std::time::Instant::now();

	// CRITICAL FIX: Check cache threshold after EACH tool result, not after all
	let cache_manager = crate::session::cache::CacheManager::new();
	let supports_caching = crate::session::model_supports_caching(&chat_session.model);

	let mut cache_check_time = 0u128;
	add_tool_results(&tool_results, chat_session, config)?;

	// External plan manager: a sparse hidden signal rides with the specialist's
	// normal action batch. Reconcile only after results exist, then inject the
	// manager-owned state before the already-needed follow-up request. With no
	// signal this is a free no-op.
	if config.supervisor.enabled && config.supervisor.plan.enabled {
		animation_manager.set_phase("Reconciling plan …").await;
		if let Err(error) =
			crate::supervisor::plan::reconcile_after_actions(chat_session, config, operation_cancelled.clone()).await
		{
			crate::log_debug!("External plan reconciliation failed: {}", error);
		}
		animation_manager.clear_phase();
	}

	// 🗜️ ADAPTIVE CONVERSATION COMPRESSION: Check if context should be compressed
	if let Err(e) = crate::session::chat::conversation_compression::check_and_compress_conversation(
		chat_session,
		config,
		operation_cancelled.clone(),
		crate::session::chat::conversation_compression::CompressionTrigger::Automatic,
	)
	.await
	{
		// See api_prep: ensure_context_within_ceiling below is the
		// model-free fallback and reports its own error when the context
		// still does not fit, so a failed compression call must not end the
		// turn on its own.
		if crate::session::chat::conversation_compression::within_ceiling_margin(chat_session, config).await {
			log_info!(
				"Forced compression inside the context ceiling margin failed during tool processing: {}. Falling back to deterministic trimming.",
				e
			);
		} else {
			log_debug!(
				"Adaptive conversation compression failed during tool processing: {}.",
				e
			);
		}
	}
	// Tool-loop follow-ups bypass the turn-entry executor, so recite here as well —
	// on change only, before the ceiling check so the note is counted in it.
	crate::supervisor::recite::inject_before_request(
		chat_session,
		config,
		crate::supervisor::recite::RecitalRefresh::OnChangeOnly,
	)?;
	crate::session::chat::conversation_compression::ensure_context_within_ceiling(chat_session, config).await?;

	// CRITICAL FIX: Check cache threshold AFTER all tool results are processed
	// This ensures cache markers are set at the correct boundary - after all parallel
	// tool results are added to session, but before sending the complete batch to server
	let cache_start = std::time::Instant::now();
	if let Ok(true) =
		cache_manager.check_and_apply_auto_cache_threshold(&mut chat_session.session, config, supports_caching, role)
	{
		log_debug!("Auto-cache threshold reached after processing all tool results - cache checkpoint applied before follow-up API request.");
	}
	cache_check_time += cache_start.elapsed().as_millis();

	// 🔍 PERFORMANCE DEBUG: Report processing breakdown and track processing time
	let total_processing_time = processing_start.elapsed().as_millis() as u64;

	// Add the processing time to the session total
	chat_session.session.info.total_layer_time_ms += total_processing_time;

	if total_processing_time > 100 {
		log_debug!(
			"🔍 Tool result processing took {}ms (cache: {}ms)",
			total_processing_time,
			cache_check_time
		);
	}

	// Check spending threshold before making follow-up API call
	match chat_session.check_spending_threshold(config) {
		Ok(should_continue) => {
			if !should_continue {
				// User chose not to continue due to spending threshold
				// Stop global animation before returning
				animation_manager.stop_current().await;
				// stderr: stdout carries the session's data (`--format jsonl` events).
				if !crate::logging::tracing_setup::is_structured_output_mode() {
					eprintln!(
						"{}",
						"✗ Tool follow-up cancelled due to spending threshold.".bright_red()
					);
				}
				return Ok(None);
			}
		}
		Err(e) => {
			// Error checking threshold, log warning and continue
			use colored::*;
			eprintln!(
				"{}: {}",
				"Warning: Error checking spending threshold".bright_yellow(),
				e
			);
		}
	}

	// Check request spending threshold before making follow-up API call
	match chat_session.check_request_spending_threshold(config) {
		Ok(should_continue) => {
			if !should_continue {
				// Request spending threshold exceeded - stop execution
				// Stop global animation before returning
				animation_manager.stop_current().await;
				if !crate::logging::tracing_setup::is_structured_output_mode() {
					eprintln!(
						"{}",
						"✗ Tool follow-up cancelled due to request spending threshold.".bright_red()
					);
				}
				return Ok(None);
			}
		}
		Err(e) => {
			// Error checking request threshold, log warning and continue
			use colored::*;
			eprintln!(
				"{}: {}",
				"Warning: Error checking request spending threshold".bright_yellow(),
				e
			);
		}
	}

	// CRITICAL FIX: Check for cancellation before making follow-up API call
	if *operation_cancelled.borrow() {
		// Stop global animation before returning
		animation_manager.stop_current().await;
		crate::log_debug!("Operation cancelled by user.");
		return Ok(None);
	}

	// Inject accumulated tool-misuse hints as a user message so the AI sees guidance
	// without polluting individual tool result strings. Hints are deduplicated across
	// all parallel tool calls in this round and cleared after injection.
	let hints = crate::mcp::hint_accumulator::drain_hints();
	if !hints.is_empty() {
		let bullet_list = hints.iter().map(|h| format!("• {h}")).collect::<Vec<_>>().join("\n");
		let hint_message =
			format!("⚠️ Tool usage notice:\n{bullet_list}\n\nPlease prefer the recommended tools going forward.");
		chat_session.add_system_managed_user_message(&hint_message)?;
	}

	// Supervisor: deliver any queued steer note HERE — during the tool loop, before
	// the follow-up call — not only at the next user turn. The detector sets
	// `steer_pending` mid-loop, but it was previously consumed only at the turn-start
	// injection point (api_executor), so a runaway identical-call loop never actually
	// saw the steer (it only landed when a real user message began a fresh turn). This
	// is the round-by-round delivery that makes the steer reach the model in the loop.
	if let Some(note) = chat_session.steer_pending.take() {
		chat_session.add_system_managed_user_message(&note)?;
		crate::log_debug!("Supervisor steer injected (tool loop)");
	}

	// Deliver everything that landed in the inbox WHILE this turn was running — a
	// finished background job, a monitor batch, a tap reply. The inbox was drained
	// only between turns, so a result that arrived mid-loop stayed invisible until
	// the model happened to stop; waiting for it was therefore something the model
	// had to arrange itself, and one that polls instead of yielding pays a
	// full-context request per poll. Delivered here, after this round's tool
	// results and before the next call, the result reaches the model on the very
	// next round. Human-shaped injections stay queued for the turn boundary, where
	// they get real user-turn semantics.
	let delivered: Vec<_> = std::iter::from_fn(crate::session::inbox::try_pop_system_managed_message).collect();
	for msg in &delivered {
		crate::log_debug!("Inbox delivered mid-turn from {:?}", msg.source);
		// `--format jsonl` is structured too: its notification sender prints the
		// event as a JSON line, where the human rendering would corrupt stdout.
		if crate::logging::tracing_setup::is_structured_output_mode()
			|| config.output_mode().should_suppress_cli_output()
		{
			let injected = crate::websocket::ServerMessage::Injected(crate::websocket::protocol::InjectedPayload {
				source_kind: msg.source.display_kind().to_string(),
				source_label: msg.source.display_label(),
				content: msg.content.clone(),
				session_id: chat_session.session.info.name.clone(),
			});
			crate::mcp::process::send_notification_message(injected);
		} else {
			crate::session::inbox::display_injected_input(msg);
		}
	}
	chat_session
		.add_mid_turn_inbox(delivered, config, operation_cancelled.clone())
		.await?;

	// Make follow-up API call; summaries are projected only for the request.
	let full_summaries = chat_session.project_summaries_for_request();
	let follow_up_result = make_follow_up_api_call(chat_session, config, operation_cancelled.clone()).await;
	chat_session.restore_summaries_after_request(full_summaries);

	// NOTE: Don't stop animation here - only stop when we're actually done with tools
	// If there are more tools to call, the animation should continue running
	// Animation will be stopped after checking should_continue_conversation

	match follow_up_result {
		Ok(response) => {
			// Use structured tool_calls from the API response; the legacy
			// text-parse fallback never returned anything.
			let has_more_tools = response.tool_calls.as_ref().is_some_and(|calls| !calls.is_empty());

			// Check finish_reason to determine if we should continue the conversation
			let should_continue_conversation = check_should_continue(&response, config, has_more_tools);
			log_debug!(
				"Provider response [follow-up]: finish={}, tool_calls={}, continue={}",
				response.finish_reason.as_deref().unwrap_or("none"),
				response.tool_calls.as_ref().map_or(0, Vec::len),
				should_continue_conversation
			);

			// Handle cost tracking from follow-up API call
			handle_follow_up_cost_tracking(chat_session, &response.exchange, config);

			// Show the cost line for this follow-up round. Printed only on the
			// success path and only after cost tracking: the Err path must NOT
			// print (on Ctrl+C the main loop prints the cancellation snapshot —
			// printing here too produced a doubled identical line), and printing
			// before tracking showed the previous round's stale total.
			use crate::session::chat::cost_tracker::CostTracker;
			CostTracker::display_intermediate_cost_breakdown(chat_session);

			// CRITICAL FIX: Update animation state after cost tracking
			// This ensures the animation shows updated cost/tokens during multi-hop tool loops
			// The animation loop reads from shared state every 100ms, so this keeps it current
			let current_cost = chat_session.session.info.total_cost;
			let current_context_tokens = chat_session.get_full_context_tokens(config).await as u64;
			animation_manager.get_state().update_cost(current_cost);
			animation_manager
				.get_state()
				.update_context_tokens(current_context_tokens);

			// Display rate limit information if available
			display_rate_limit_info(&response.exchange);

			if should_continue_conversation {
				Ok(Some((
					response.content,
					response.exchange,
					response.tool_calls,
					response.response_id, // Include response_id from follow-up response
					response.thinking,    // CRITICAL FIX: Include thinking from follow-up response for Moonshot
				)))
			} else {
				// If no more tools, stop animation and return
				animation_manager.stop_current().await;
				Ok(Some((
					response.content,
					response.exchange,
					None,
					response.response_id,
					response.thinking, // CRITICAL FIX: Include thinking even when stopping
				)))
			}
		}
		Err(e) => {
			// Centralized error printing happens in the main loop's Err branch
			// (handle_followup_api_error). Just log diagnostics, stop animation,
			// and propagate so the caller can offer a Ctrl+G retry.
			log_debug!("Follow-up API call failed for model {}: {}", chat_session.model, e);
			log_debug!("Temperature: {}", chat_session.temperature);

			// Stop animation on error before returning
			animation_manager.stop_current().await;

			Err(e)
		}
	}
}

/// Append one round's tool results to the session as `tool` messages, each capped
/// by `mcp_response_tokens_threshold`. Every tool call in the transcript must be
/// answered, including on a round that ends the turn without a follow-up call.
pub fn add_tool_results(
	tool_results: &[crate::mcp::McpToolResult],
	chat_session: &mut ChatSession,
	config: &Config,
) -> Result<()> {
	// IMPROVED APPROACH: Add tool results as proper "tool" role messages
	// This follows the standard OpenAI/Anthropic format and avoids double-serialization
	for tool_result in tool_results {
		// CRITICAL FIX: Extract ONLY the actual tool output, not our custom JSON wrapper.
		// Deduplication already ran upstream in execute_tools_parallel (before display),
		// where a duplicate was converted into an error result carrying the placeholder.
		// Here we just extract and pass the body through.
		let tool_content = extract_tool_content(tool_result);

		// (Resource links a tool advertised — detached background jobs — are
		// registered as pending in the MCP client the moment the tool returns,
		// before the job can complete; see `mcp::client::call_tool`.)

		// Apply global MCP response token truncation before adding to session
		let (tool_content, was_truncated) = crate::utils::truncation::truncate_mcp_response_global(
			&tool_content,
			config.mcp_response_tokens_threshold,
			&tool_result.tool_name,
		);
		// Truncation marker is rendered inline on each tool's close line in
		// `display_tool_success` (e.g. `╰ ✓ view 55ms · 6.9K tokens · truncated
		// to 4K tokens`). Re-printing a separate `⚠️ … truncated …` line here
		// would duplicate the warning and break the framed block.
		let _ = was_truncated;

		// Use the new add_tool_message method which handles token tracking properly
		// NOTE: Compression intentionally does NOT run mid-loop here. Compressing while
		// we are still adding tool_results for the current assistant's tool_calls would
		// orphan the tool_use blocks (drain the parent assistant message) and leave
		// subsequent tool_results with no matching tool_use — the API would then reject
		// the follow-up request. Compression is safely handled after the full batch is
		// added (see check_and_compress_conversation call below) and before the next
		// API request (see api_prep.rs).
		chat_session.add_tool_message(
			&tool_content,
			&tool_result.tool_id,
			&tool_result.tool_name,
			config,
			tool_result.extract_images(),
		)?;
	}
	Ok(())
}

// Extract tool content from tool result
fn extract_tool_content(tool_result: &crate::mcp::McpToolResult) -> String {
	tool_result.extract_content()
}

// Make follow-up API call with cancellation support
async fn make_follow_up_api_call(
	chat_session: &ChatSession,
	config: &Config,
	cancellation_token: tokio::sync::watch::Receiver<bool>,
) -> Result<crate::providers::ProviderResponse> {
	let profile = chat_session.model_profile(config);

	// CRITICAL FIX: Pass cancellation token to ensure immediate cancellation
	let validation_params =
		ChatCompletionWithValidationParams::from_profile(&chat_session.session.messages, &profile, config)
			.with_cancellation_token(cancellation_token);

	// Carry the structured-output schema onto every follow-up turn too. Without
	// this the schema only applies to the first call (which returns a tool call,
	// not the answer), so the final post-tool reply is unconstrained. The schema
	// is native `response_format` and coexists with tool calling.
	let validation_params = if let Some(schema) = chat_session.schema.clone() {
		validation_params.with_schema(schema)
	} else {
		validation_params
	};
	crate::session::chat_completion_with_validation(validation_params).await
}

// Check if conversation should continue based on finish_reason
pub fn check_should_continue(
	response: &crate::providers::ProviderResponse,
	_config: &Config,
	has_more_tools: bool,
) -> bool {
	match response.finish_reason.as_deref() {
		Some("tool_calls") | Some("tool_use") => true,
		Some("stop") | Some("length") | Some("end_turn") => false,
		Some(other) => {
			// Unknown finish_reason, be conservative and continue
			log_info!("Unknown finish_reason '{}', continuing conversation", other);
			true
		}
		None => has_more_tools,
	}
}

// Handle cost tracking from follow-up API call
fn handle_follow_up_cost_tracking(
	chat_session: &mut ChatSession,
	exchange: &crate::session::ProviderExchange,
	_config: &Config,
) {
	if exchange.usage.is_none() {
		chat_session.session.info.main_cost = None;
	}
	if let Some(usage) = &exchange.usage {
		// The reply is not in the transcript yet: the messages are exactly the
		// prompt this usage report describes.
		if let Some(estimate) = chat_session.raw_context_estimate() {
			chat_session.observe_prompt_tokens(
				estimate,
				usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
			);
		}
		// Every follow-up exchange = one completed API call (mirrors CostTracker::track_exchange_cost)
		chat_session.session.info.total_api_calls += 1;

		// Update session token counts using cache manager with octolib data directly
		let cache_manager = crate::session::cache::CacheManager::new();
		cache_manager.update_token_tracking(
			&mut chat_session.session,
			usage.input_tokens, // Non-cached input tokens from API
			usage.output_tokens,
			usage.cache_read_tokens,
			usage.cache_write_tokens,
			usage.reasoning_tokens,
		);

		// Track API time from the follow-up exchange
		if let Some(api_time_ms) = usage.request_time_ms {
			chat_session.session.info.total_api_time_ms += api_time_ms;
		}

		let raw_cost = exchange
			.response
			.get("usage")
			.and_then(|value| value.get("cost"))
			.and_then(|value| value.as_f64());
		let (cost, cost_source) = match (usage.cost, raw_cost) {
			(Some(cost), _) => (Some(cost), "normalized"),
			(None, Some(cost)) => (Some(cost), "raw"),
			(None, None) => (None, "unreported"),
		};
		chat_session.session.info.record_main_cost(cost);
		chat_session.estimated_cost = chat_session.session.info.total_cost;
		let cost_summary = cost
			.map(|value| format!("${value:.5} ({cost_source})"))
			.unwrap_or_else(|| cost_source.to_string());
		let latency = usage
			.request_time_ms
			.map(|value| format!("{value}ms"))
			.unwrap_or_else(|| "unreported".to_string());
		log_debug!(
			"Provider usage [follow-up]: provider={}, input={}, output={}, cache_read={}, cache_write={}, reasoning={}, cost={}, session_total=${:.5}, latency={}",
			exchange.provider,
			usage.input_tokens,
			usage.output_tokens,
			usage.cache_read_tokens,
			usage.cache_write_tokens,
			usage.reasoning_tokens,
			cost_summary,
			chat_session.session.info.total_cost,
			latency
		);
		if cost.is_none()
			&& exchange.provider == "openrouter"
			&& !exchange
				.request
				.get("usage")
				.and_then(|value| value.get("include"))
				.and_then(|value| value.as_bool())
				.unwrap_or(false)
		{
			log_debug!("OpenRouter cost unavailable: request usage.include was false");
		}
	} else {
		log_debug!(
			"Provider usage [follow-up]: provider={}, usage unavailable",
			exchange.provider
		);
	}
}

// Helper function to display rate limit information from provider response
fn display_rate_limit_info(exchange: &crate::session::ProviderExchange) {
	if let Some(ref rate_limit_headers) = exchange.rate_limit_headers {
		let mut rate_limit_info = Vec::new();

		match exchange.provider.as_str() {
			"anthropic" => {
				// Anthropic rate limit format
				if let (Some(tokens_remaining), Some(tokens_limit)) = (
					rate_limit_headers.get("tokens_remaining"),
					rate_limit_headers.get("tokens_limit"),
				) {
					rate_limit_info.push(format!("Tokens: {}/{}", tokens_remaining, tokens_limit));
				}

				if let (Some(input_remaining), Some(input_limit)) = (
					rate_limit_headers.get("input_tokens_remaining"),
					rate_limit_headers.get("input_tokens_limit"),
				) {
					rate_limit_info.push(format!("Input tokens: {}/{}", input_remaining, input_limit));
				}

				if let (Some(output_remaining), Some(output_limit)) = (
					rate_limit_headers.get("output_tokens_remaining"),
					rate_limit_headers.get("output_tokens_limit"),
				) {
					rate_limit_info.push(format!("Output tokens: {}/{}", output_remaining, output_limit));
				}

				if !rate_limit_info.is_empty() {
					crate::log_info!("📊 Anthropic rate limits: {}", rate_limit_info.join(" | "));
				}
			}
			"openai" => {
				// OpenAI rate limit format
				if let (Some(requests_remaining), Some(requests_limit)) = (
					rate_limit_headers.get("requests_remaining"),
					rate_limit_headers.get("requests_limit"),
				) {
					rate_limit_info.push(format!("Requests: {}/{}", requests_remaining, requests_limit));
				}

				if let (Some(tokens_remaining), Some(tokens_limit)) = (
					rate_limit_headers.get("tokens_remaining"),
					rate_limit_headers.get("tokens_limit"),
				) {
					rate_limit_info.push(format!("Tokens: {}/{}", tokens_remaining, tokens_limit));
				}

				if let Some(request_reset) = rate_limit_headers.get("request_reset") {
					rate_limit_info.push(format!("Request reset: {}", request_reset));
				}

				if !rate_limit_info.is_empty() {
					crate::log_info!("📊 OpenAI rate limits: {}", rate_limit_info.join(" | "));
				}
			}
			_ => {
				// Generic rate limit display for other providers
				if !rate_limit_headers.is_empty() {
					let info: Vec<String> = rate_limit_headers
						.iter()
						.map(|(k, v)| format!("{}: {}", k, v))
						.collect();
					crate::log_info!("📊 {} rate limits: {}", exchange.provider, info.join(" | "));
				}
			}
		}
	}
}

#[cfg(test)]
#[path = "tool_result_processor_tests.rs"]
mod tests;

```
