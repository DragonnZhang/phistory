# DeepSeek-Reasonix — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## internal/assembly/boot/agent_preset.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/assembly/boot/agent_preset.go

SHA-256: `ce8ad31ed24503231694ada157dd8c49780b112ac44cc6697c33dd850fb6393b`

```
package boot

import (
	"reasonix/internal/contract/ablation"
	"reasonix/internal/contract/agentpreset"
	"reasonix/internal/contract/tool"
	"reasonix/internal/ext/plugin"
	"reasonix/internal/tools/advisor"
	"reasonix/internal/tools/builtin"
	"reasonix/internal/tools/script"
)

// Canonical Agent role-setting identifiers re-exported for frontends that
// already import boot. Prefer agentpreset directly in new code.
const (
	AgentPresetBalanced = string(agentpreset.Balanced)
	AgentPresetDelivery = string(agentpreset.Delivery)
)

// Deprecated TokenMode constants — one compatibility version. Prefer
// AgentPreset* and NormalizeAgentPreset.
const (
	TokenModeFull     = "full"
	TokenModeEconomy  = "economy"
	TokenModeDelivery = "delivery"
)

// NormalizeAgentPreset maps free-form and legacy values to a canonical
// agent role setting. Empty/unknown → balanced.
func NormalizeAgentPreset(raw string) string {
	return string(agentpreset.Normalize(raw))
}

// NormalizeTokenMode is the deprecated alias that returns legacy tokenMode
// names (economy/full/delivery) for dual-write and older clients.
func NormalizeTokenMode(mode string) string {
	return agentpreset.LegacyTokenMode(agentpreset.Normalize(mode))
}

// AgentPresetFromTokenMode maps a legacy tokenMode onto a role setting.
func AgentPresetFromTokenMode(mode string) string {
	return string(agentpreset.FromLegacyTokenMode(mode))
}

// CoreProviderToolNames is the stable top-level tool surface shared by every
// Agent role setting under identical configuration. Host-control tools
// (ask, update_goal, todo_write, complete_step) are appended when enabled.
func CoreProviderToolNames() []string {
	return []string{
		"bash",
		"bash_output",
		"kill_shell",
		"wait",
		"read_file",
		"edit_file",
		"write_file",
		"compress",
		"recall",
		"context_budget",
		"use_capability",
	}
}

// HostControlToolNames are collaboration/contract tools that may appear in the
// provider schema independently of the Agent role setting. The turn-exit tools
// are here because the host names them as ways out of an unfinished turn, and a
// name the schema does not carry is one the model cannot call.
func HostControlToolNames() []string {
	return []string{
		"ask",
		"await_user",
		"update_goal",
		"todo_write",
		"complete_step",
		"setting",
	}
}

// GoalOnlyToolNames are host-control tools whose contract exists only inside a
// Goal turn. An assembly that can never arm one drops them from the schema
// rather than shipping a definition the model can only fail to call.
func GoalOnlyToolNames() []string {
	return []string{"update_goal"}
}

// EvidenceToolNames are the tools whose whole purpose is the evidence
// contract, which the evidence arm drops from the schema. todo_write is not
// one, and neither is await_user: neither is an evidence claim, and an arm
// dropping them would measure more than the contract.
func EvidenceToolNames() []string {
	return []string{"complete_step"}
}

// BrowserToolNames are shown when the assembly has a browser to drive.
func BrowserToolNames() []string {
	var names []string
	for _, t := range builtin.BrowserTools(nil) {
		names = append(names, t.Name())
	}
	return names
}

// ComputerToolNames are shown when the host can operate this machine's
// applications.
func ComputerToolNames() []string {
	var names []string
	for _, t := range builtin.ComputerTools(nil) {
		names = append(names, t.Name())
	}
	return names
}

// UnifiedProviderToolNames returns the provider-visible allowlist for a boot
// with host-control tools enabled.
func UnifiedProviderToolNames() []string {
	core := CoreProviderToolNames()
	host := HostControlToolNames()
	out := make([]string, 0, len(core)+len(host))
	out = append(out, core...)
	out = append(out, host...)
	return out
}

// applyUnifiedProviderToolSurface restricts Schemas/ContractEntries to the
// shared core + host-control tools, plus the tools of the MCP servers named in
// alwaysLoad. use_capability can still Get every registered tool, including
// those hidden from the provider schema.
func applyUnifiedProviderToolSurface(reg *tool.Registry, goalTurnsUnreachable bool, arm ablation.Set, alwaysLoad map[string]bool) {
	if reg == nil {
		return
	}
	unreachable := map[string]bool{}
	if goalTurnsUnreachable {
		for _, name := range GoalOnlyToolNames() {
			unreachable[name] = true
		}
	}
	// The arm has to reach the schema, not only the gate: left visible, both
	// runs pay the same prompt and the same evidence arguments, and the
	// comparison answers for the gate when the question was the contract.
	if arm.Off(ablation.Evidence) {
		for _, name := range EvidenceToolNames() {
			unreachable[name] = true
		}
	}
	// recall keeps its read half, so the arm swaps the tool rather than hiding
	// it: an address the fold index still carries stays readable either way.
	if arm.Off(ablation.RecallSearch) {
		reg.Add(builtin.RecallWithoutSearch())
	}
	allow := make([]string, 0, 16)
	for _, name := range UnifiedProviderToolNames() {
		if unreachable[name] {
			continue
		}
		if _, ok := reg.Get(name); ok {
			allow = append(allow, name)
		}
	}
	// A browser tool is shown only with a browser session behind it, which the
	// config decides at boot, so the schema stays the same for the whole session.
	for _, name := range BrowserToolNames() {
		if t, ok := reg.Get(name); ok && builtin.BrowserBound(t) {
			allow = append(allow, name)
		}
	}
	for _, name := range ComputerToolNames() {
		if t, ok := reg.Get(name); ok && builtin.ComputerBound(t) {
			allow = append(allow, name)
		}
	}
	// advise, best_of_n, run_script, the peer tools and read_mcp_resource are
	// registered only when config asks, at boot; present_files follows the same rule.
	for _, name := range []string{advisor.Name, script.Name, "list_peers", "send_peer_message", "dispatch_to_project", "open_projects", "view_image", "present_files", plugin.ResourceReadToolName} {
		if _, ok := reg.Get(name); ok {
			allow = append(allow, name)
		}
	}
	allow = append(allow, alwaysLoadedMCPTools(reg, alwaysLoad)...)
	// Always keep use_capability if somehow only that remains.
	if len(allow) == 0 {
		if _, ok := reg.Get("use_capability"); ok {
			allow = []string{"use_capability"}
		}
	}
	reg.SetProviderVisibleTools(allow)
}

// ApplyUnifiedProviderToolSurface restricts a registry to what a provider is
// shown, at this arm. Exported so a harness reproduces the real surface
// instead of a second copy of this rule.
func ApplyUnifiedProviderToolSurface(reg *tool.Registry, goalTurnsUnreachable bool, arm ablation.Set) {
	applyUnifiedProviderToolSurface(reg, goalTurnsUnreachable, arm, nil)
}

// pinnedMCPServers is the always-load servers whose schema was known at boot.
// A server discovered during this session waits for the next one, whatever
// the discovery race, so the same config and cache give the same schema.
func pinnedMCPServers(alwaysLoad, schemaKnown map[string]bool) map[string]bool {
	pinned := map[string]bool{}
	for name := range alwaysLoad {
		if schemaKnown[name] {
			pinned[name] = true
		}
	}
	return pinned
}

// alwaysLoadedMCPTools names the registered tools of the always-loaded servers.
// Only a server whose schema is already known at boot contributes: a connect
// stub has no tools to show, and tools arriving after boot would rewrite the
// cached prefix mid-session. An unauthorized server stays behind
// use_capability, which is where its approval is asked for.
func alwaysLoadedMCPTools(reg *tool.Registry, alwaysLoad map[string]bool) []string {
	if len(alwaysLoad) == 0 {
		return nil
	}
	var names []string
	for _, b := range reg.MCPBindings() {
		if !alwaysLoad[b.Server] {
			continue
		}
		if t, ok := reg.Get(b.CallableName); ok && tool.IsMCPServerAuthorized(t) {
			names = append(names, b.CallableName)
		}
	}
	return names
}

```

## internal/assembly/boot/build.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/assembly/boot/build.go

SHA-256: `439fc32b27e1fe2eb1e528dc9031663b3fdb6802eb7d3dccb2dbaa0bd266c137`

```
package boot

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"reasonix/internal/base/netclient"
	"reasonix/internal/base/netdns"
	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/agentcomposition"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/pricing"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/ext/command"
	"reasonix/internal/ext/extension"
	"reasonix/internal/ext/extension/sidecar"
	"reasonix/internal/ext/hook"
	"reasonix/internal/ext/plugin"
	"reasonix/internal/ext/pluginspec"
	"reasonix/internal/model/billing"
	"reasonix/internal/platform/browser"
	"reasonix/internal/platform/gitcmd"
	"reasonix/internal/platform/lsp"
	"reasonix/internal/platform/otlp"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/runtime/delegation"
	"reasonix/internal/safety/endpointclient"
	"reasonix/internal/safety/permission"
	"reasonix/internal/safety/sandbox"
	"reasonix/internal/session/control"
	"reasonix/internal/tools/script"
)

// builder carries one build's state from stage to stage. Each stage reads
// what the earlier ones settled and adds its own; none reaches back.
type builder struct {
	compositionBinding func(agentcomposition.Version, string) *agentcomposition.Binding
	ctx                context.Context
	closeLifetime      context.CancelFunc
	opts               Options
	timer              *phaseTimer
	owner              *extension.RuntimeOwner
	fileWriteReceipt   func(path string, hadPrior bool, prior []byte)
	stderr             io.Writer
	trace              *otlp.Exporter
	root               string
	repo               gitcmd.Repo // root's git identity, settled in load
	roots              config.Roots
	cfg                *config.Config
	sink               event.Sink
	quoteContext       *event.QuoteContext
	proxy              netclient.ProxySpec
	ext                *extensionStage
	pendingMgr         *sidecar.Manager // sidecars no RuntimeSet owns yet
	providers          providerStage
	model              modelSelection
	keep               agent.KeepPolicy
	session            sessionRuntime
	balanceClient      *http.Client
	keyedClient        *http.Client // carries provider keys; refuses cross-origin redirects
	execProv           provider.Provider
	shell              sandbox.Shell
	prompt             promptAssembly
	additionalDirs     []string

	tools   toolStage
	cleanup func()
}

// toolStage is everything the registry was built from, which the controller
// and the extension snapshot both read.
type toolStage struct {
	reg         *tool.Registry
	env         toolEnvironment
	host        *plugin.Host
	specOptions pluginspec.Options
	mcp         mcpSpecPlan
	configSpecs []plugin.Spec
	lsp         *lsp.Manager
	browser     *browser.Session
	peerLink    *tool.PeerLink
	subagents   *delegation.SubagentStore
	settingLink *tool.SettingsLink
	peerTools   func(enabled, dispatch bool)
	maxSteps    int
	policy      permission.Policy
	gate        *control.SharedHeadlessGate
	hooks       []hook.ResolvedHook
	hookRunner  *hook.Runner
	roles       roleWiring
	sub         subagentConfig
	runner      *delegation.Runner
	skillRun    *skillSubagents
	runners     skillRunners
	cmds        []command.Command
	caps        *capabilitySurface
	// mcpSchemaKnown names the configured servers whose tools were known at
	// registration, the only ones an always-load can show this session.
	mcpSchemaKnown map[string]bool
}

// build is the assembly behind BuildRuntime: it loads config, resolves the
// models, wires the runtime, and freezes the extension snapshot from the
// objects it just assembled. The controller owns plugin subprocesses until
// Controller.Close releases them.
func build(ctx context.Context, opts Options) (*BuildResult, error) {
	b := &builder{timer: newPhaseTimer(), compositionBinding: newLiveCompositionBinding}
	return b.build(ctx, opts)
}

func (b *builder) build(ctx context.Context, opts Options) (*BuildResult, error) {
	opts = observeOverrides(opts)
	b.timer.observe = opts.OnPhase
	// The runtime outlives the request that built it (Studio opens a pane with
	// one), and its MCP servers and sidecars start on that context later.
	life, detach, closeLifetime := constructionLifetime(ctx)
	b.closeLifetime = closeLifetime
	b.ctx, b.opts, b.owner, b.fileWriteReceipt = bindRuntimeOwner(life, opts)
	defer b.retireUnownedSidecars()
	delivered := false
	var constructed *control.Controller
	defer func() {
		if !delivered {
			closeLifetime()
			if constructed != nil {
				constructed.ReleaseResources()
			} else {
				b.discardBuildResources()
			}
		}
	}()
	if b.owner.Notices != nil && b.opts.Sink != nil {
		window := b.owner.Notices.Begin(b.opts.Sink)
		b.opts.Sink = window
		defer func() { window.Close(delivered) }()
	}
	if err := b.load(); err != nil {
		return nil, err
	}
	if err := b.wireTools(); err != nil {
		return nil, err
	}
	if err := b.bindCompositionMCPContracts(ctx); err != nil {
		return nil, err
	}
	ctrl, err := b.controller()
	if err != nil {
		return nil, err
	}
	constructed = ctrl
	res, err := b.freeze(ctrl)
	if err == nil {
		if cancelErr := detach(); cancelErr != nil {
			ctrl.Close()
			return nil, cancelErr
		}
	}
	delivered = err == nil
	return res, err
}

// workspaceExtraRoots is the one list of folders that count as the workspace
// beside the root: the granted extra roots and this session's --add-dir folders.
// The executor and every sub-agent it spawns read it, so a write in one is
// observed the same way whoever made it.
func (b *builder) workspaceExtraRoots() []string {
	return appendUniquePaths(b.cfg.ExtraRoots(), b.additionalDirs...)
}

// retireUnownedSidecars restores adopted clients and closes fresh sidecars when
// the build fails before the extension snapshot takes ownership.
func (b *builder) retireUnownedSidecars() {
	if b.pendingMgr != nil {
		close(b.ext.failed)
		b.pendingMgr.RollbackPlanStart(b.opts.Extensions)
	}
}

// load settles configuration, the extension generation, the executor model,
// the session resources and the system prompt.
func (b *builder) load() error {
	opts := b.opts
	if opts.Observe != nil && opts.Observe.Pending == nil {
		return errors.New("boot: the read-only posture needs somewhere to park what needs a person")
	}
	b.stderr = opts.Stderr
	if b.stderr == nil {
		b.stderr = os.Stderr
	}
	b.root, b.roots = resolveWorkspaceRoot(opts.WorkspaceRoot), opts.roots()
	if opts.Observe != nil {
		if err := checkObserveRoot(b.root); err != nil {
			return err
		}
	}
	b.repo = workspaceRepo(b.ctx, opts.WorkspaceRepo, b.root)
	var err error
	if b.additionalDirs, err = normalizeAdditionalDirs(b.root, opts.AdditionalDirs); err != nil {
		return err
	}
	var migrations configMigrations
	if opts.Observe == nil {
		migrations = runConfigMigrations(b.roots, b.root)
	}
	if b.cfg, err = b.loadConfig(); err != nil {
		return err
	}
	if err := b.prepareComposition(); err != nil {
		return err
	}
	opts = b.opts
	cfg := b.cfg
	// Name resolution is settled before anything opens a socket, and here rather
	// than in an `init` because the setting lives in the config. Empty leaves the
	// resolver as it was; the proxy rides along because a DoH query is HTTPS.
	proxyForDNS, _ := netclient.ProxyFunc(cfg.NetworkProxySpec())
	netdns.InstallConfigured(cfg.NetworkDNS(), cfg.NetworkDoH(), proxyForDNS)
	migrations.deepSeekErr = deepSeekProtocolMigrationNoticeError(handleConfigLoadWarnings(opts, cfg, b.stderr), migrations.deepSeekErr)
	// [secrets] is user-global, so arming these package globals before any
	// subprocess can spawn is correct for every concurrent workspace.
	secrets.SetFilterSubprocessEnv(cfg.Secrets.FilterSubprocessEnv)
	secrets.SetProtectSensitiveFiles(cfg.Secrets.ProtectSensitiveFiles)
	secrets.SetProtectCredentialFiles(cfg.Secrets.ProtectCredentialFiles)
	secrets.RegisterCredentialEnvKeys(cfg.CredentialEnvNames())
	// One synchronized sink for every emitter: background jobs and sidecars
	// emit from their own goroutines. The goal tee and the coalescer wrap it
	// here, before the extension hub captures it, so agents emit through both.
	b.trace = newTraceExporter(cfg, b.root, opts.Version, b.stderr)
	quoted, quoteContext := quotedSink(cfg, opts, b.trace)
	b.quoteContext = quoteContext
	b.sink = control.NewGoalUsageTee(event.Coalesce(quoted, event.DefaultStreamDeltaWindow))
	// Tag a shell result whose whole output is a diff, once, before the
	// extension hub captures the sink — every emitter shares this one.
	b.sink = event.MarkEmbeddedDiffs(b.sink, cfg.EmbeddedDiffDetectionEnabled())

	b.proxy = cfg.NetworkProxySpec()
	if b.ext, err = startExtensions(b.ctx, opts, b.roots, b.root, b.owner, b.sink); err != nil {
		return err
	}
	b.pendingMgr = b.ext.mgr
	b.timer.mark("extensions")
	if b.providers, err = resolveProviders(opts, cfg, b.proxy, b.ext.mgr, b.owner); err != nil {
		return err
	}
	if b.model, err = selectModel(opts, cfg, b.providers.extension); err != nil {
		return err
	}
	b.keep = agentKeepPolicy(cfg.Agent.Keep)

	migrations.report(b.sink, cfg)
	b.timer.mark("config")
	migrateLegacySources(opts, b.sink)
	b.timer.mark("migrations")
	b.reportModelNotices()
	if b.session, err = startSessionRuntime(opts, cfg, b.root, b.sink); err != nil {
		return err
	}
	b.timer.mark("sessions")

	// Validated before the first provider is constructed.
	if err := netclient.Validate(b.proxy); err != nil {
		return err
	}
	if b.balanceClient, err = netclient.NewHTTPClient(b.proxy, netclient.TransportOptions{}); err != nil {
		return err
	}
	if b.keyedClient, err = endpointclient.New(b.proxy, netclient.TransportOptions{}); err != nil {
		return err
	}
	if b.execProv, err = resolveProvider(b.providers.effective, cfg, b.proxy, provider.Selection{Ref: b.model.ref, Effort: opts.EffortOverride}); err != nil {
		return err
	}
	b.timer.mark("provider")
	if opts.resolvedShell != nil {
		b.shell = *opts.resolvedShell
	} else {
		confine := sandbox.BashConfine(effectiveBashMode(opts, cfg), opts.roots().SandboxDir())
		b.shell = resolveShellWithNotice(opts, cfg.Tools.Shell.Prefer, cfg.Tools.Shell.Path, cfg.Tools.Shell.Args, confine, b.stderr, b.sink)
	}
	// Record the resolved interpreter for diagnostics, staying at Debug because
	// headless `run` must leave stderr empty unless --debug is passed. A launch
	// failure emits an always-on Warn with the same kind/path/source fields.
	b.timer.mark("shell")
	slog.Debug("boot: shell tool interpreter resolved", "kind", b.shell.Kind.String(), "path", b.shell.Path, "prefer", cfg.Tools.Shell.Prefer)
	b.prompt, err = buildPromptAssembly(b.ctx, opts, cfg, b.root, b.shell, b.sink, b.timer)
	if err == nil {
		err = b.bindCompositionSkills()
	}
	return err
}

// resolveShellWithNotice keeps shell-discovery warnings on stderr for CLI
// diagnostics and also reports them through the boot sink, where the settings
// surface can show which interpreter actually runs.
func resolveShellWithNotice(opts Options, prefer, path string, args []string, confine func(string) bool, stderr io.Writer, sink event.Sink) sandbox.Shell {
	var warnings strings.Builder
	d := sandbox.ShellDiscovery{Prefer: prefer, Path: path, Args: args, Confine: confine, Warn: io.MultiWriter(stderr, &warnings), ProofDir: opts.roots().CacheDir()}
	if opts.tuneShell != nil {
		opts.tuneShell(&d)
	}
	shell := d.Resolve()
	if detail := strings.TrimSpace(warnings.String()); detail != "" {
		report(sink, event.Event{Level: event.LevelWarn, Text: "Shell tool interpreter fallback.", Detail: detail})
	}
	return shell
}

// loadConfig reads the configuration this build runs under. The read-only
// posture takes the user's own and nothing the workspace declares, and holds no
// MCP server at all.
func (b *builder) loadConfig() (*config.Config, error) {
	if b.opts.Observe == nil {
		return b.roots.LoadForRoot(b.root)
	}
	cfg, err := b.roots.LoadUserScopeForRoot(b.root)
	if err != nil {
		return nil, err
	}
	cfg.Plugins = nil
	cfg.Secrets.ProtectSensitiveFiles, cfg.Secrets.ProtectCredentialFiles = true, true
	return cfg, nil
}

// skippedDefaultDetail says why the saved default was passed over. A decision
// source exists but answers system_one only, which is a different fix from a name
// nothing declares.
func (b *builder) skippedDefaultDetail() string {
	var mismatch *config.AnswersMismatchError
	if errors.As(b.cfg.RequireAnswers(b.model.skipped, config.AnswersChat), &mismatch) {
		return fmt.Sprintf("default_model = %q is a decision source: it answers system_one's questions, not conversation; using %q. Choosing a default model replaces it; until then the file keeps it as written.", b.model.skipped, b.model.ref)
	}
	return fmt.Sprintf("default_model = %q names no configured provider or model; using %q. Choosing a default model replaces it; until then the file keeps it as written.", b.model.skipped, b.model.ref)
}

func (b *builder) reportModelNotices() {
	cfg, entry := b.cfg, b.model.entry
	if ignored := cfg.IgnoredProjectDefaultModel(); ignored != "" {
		report(b.sink, event.Event{Level: event.LevelWarn, Text: "Ignored the project config's default_model.", Detail: fmt.Sprintf("./reasonix.toml sets default_model = %q but no configured provider serves it; using %q from your user config instead. Edit or remove that default_model line to silence this notice.", ignored, cfg.DefaultModel)})
	}
	if b.model.skipped != "" {
		report(b.sink, event.Event{Level: event.LevelWarn, Code: event.NoticeCodeDefaultModelUnavailable,
			Text:   "The saved default model is not configured, so another configured model is in use.",
			Detail: b.skippedDefaultDetail()})
	}
	// Without RequireKey the UI stays reachable, so a missing key would
	// otherwise surface only as a silently failing first request.
	if !b.opts.RequireKey && entry.RequiresAPIKey() && entry.APIKey() == "" {
		report(b.sink, event.Event{Text: "Selected model is missing its API key.", Detail: fmt.Sprintf("model %q is selected but its API key %s is not set — requests will fail until you set it", b.model.name, entry.APIKeyEnv)})
	}
}

// wireTools builds the registry: built-ins, MCP and LSP, delegation, session,
// skill and slash tools, and the use_capability surface over all of them.
func (b *builder) wireTools() error {
	opts, cfg, root := b.opts, b.cfg, b.root
	t := &b.tools
	t.reg = newToolRegistry(opts)
	t.env = resolveToolEnvironment(opts, cfg, b.roots, root, b.additionalDirs, b.shell, b.stderr)
	env := t.env
	// The full inventory registers for use_capability; the provider-visible surface narrows later.
	addBuiltins(t.reg, cfg.Tools.Enabled, env.writeRoots, env.bash, env.bashTimeout, env.search, b.stderr, root, b.proxy, env.forbidReadRoots, env.readRoots, env.readPaths, env.sessionGuard, env.managedConfig, opts.FileOverlay, opts.TerminalRunner, env.sessionTemp, b.fileWriteReceipt)
	addViewImage(t.reg, cfg, b.model.entry, env, root)
	addPresentFiles(t.reg, cfg, env, root)
	bindFileViews(t.reg, cfg.Tools.ChangedFilesProtected())
	t.peerLink, t.peerTools = peerTools(t.reg, cfg)
	t.settingLink = addSettingTool(t.reg, cfg)
	addSystemOne(t.reg, cfg.Tools.Enabled, cfg, b.keyedClient)
	addAdvisor(t.reg, cfg, b.proxy, b.sink)
	addWebSearch(t.reg, cfg, b.proxy, b.sink)
	addFetchChain(t.reg, cfg, b.proxy, b.sink)
	if cfg.Agent.CodeMode {
		t.reg.Add(script.New())
	}
	if err := b.wireMCP(); err != nil {
		return err
	}
	t.browser = bindMachineTools(t.reg, cfg, root, opts.BrowserSession, opts.ComputerDriver)
	b.timer.mark("mcp")

	t.maxSteps = max(opts.MaxSteps, 0)
	subagentStore, err := newSubagentStore(b.session.dir, opts.SubagentParentLive)
	if err != nil {
		return err
	}
	t.subagents = subagentStore
	if subagentStore != nil {
		subagentStore.WithDestroyedChecker(b.session.jobs.IsDestroying)
	}
	// One gate for the executor and every sub-agent, so a child is never a
	// weaker path around the parent. A headless caller always names a mode:
	// Ask fails closed, Auto allows writer fallbacks, DontAsk denies (#6927).
	mediaAsk := askWithUnsandboxedMedia(cfg.Permissions.Allow, cfg.Permissions.Ask, cfg.Permissions.Deny, env.bash.Enforce() && sandbox.AvailableFor(env.bash))
	t.policy = permission.New(cfg.Permissions.Mode, cfg.Permissions.Allow, mediaAsk, cfg.Permissions.Deny).
		WithAllowDynamicBashFallback(cfg.Permissions.AllowDynamicBash).
		WithAskOnOrigin(cfg.Permissions.AskOnExternalOrigin).
		WithSessionAllow(opts.PermissionAllow)
	t.gate = control.NewSharedHeadlessGate(t.policy, opts.HeadlessApprovalMode)
	t.hooks, t.hookRunner, err = loadHooks(opts, b.roots, root, b.shell, b.sink)
	if err != nil {
		return err
	}

	t.roles = roleWiring{cfg: cfg, roots: b.roots, resolver: b.providers.effective, extension: b.providers.extension,
		proxy: b.proxy, sink: b.sink, gate: t.gate, reg: t.reg, keep: b.keep, hooks: t.hookRunner, checkTargetAccess: t.env.targetAccessCheck()}
	t.sub = newSubagentConfig(opts, cfg, b.model.entry, b.model.name, b.providers.effective, b.proxy, b.prompt.skillStore)
	if t.sub.inheritedEffortDropped {
		report(b.sink, event.Event{
			Code:   event.NoticeCodeInheritedSubagentEffortDropped,
			Level:  event.LevelWarn,
			Text:   "Ignored the inherited subagent effort for the selected model.",
			Detail: fmt.Sprintf("Inherited subagent effort %q is not supported by the current execution model %q; subagents that follow it will use the provider/model default effort. The persisted setting was not changed.", firstNonEmpty(cfg.Agent.RoleEfforts["subagent"], cfg.Agent.SubagentEffort), b.model.ref),
		})
	}
	t.runner, t.skillRun = t.roles.delegation(delegationInputs{opts: opts, sub: t.sub, exec: b.execProv, entry: b.model.entry,
		modelName: b.model.name, root: root, maxSteps: t.maxSteps, delivery: b.model.delivery, store: subagentStore,
		session: b.session, bashEnforced: env.bash.Enforce, extraRoots: b.workspaceExtraRoots(), childTools: childViewImage(cfg, env, root)})
	registerSessionTools(t.reg, opts.Ablation, b.roots, b.session.dir, b.prompt.memory.Store)
	if opts.NativeSurfaces {
		t.reg.Add(agent.NewRenderSurfaceTool())
	}

	t.runners = skillRunners{readOnly: t.skillRun.runReadOnly, run: t.skillRun.run, profile: skillProfile(cfg, t.sub.inheritedFor)}
	t.cmds = loadCommands(opts, root)
	addInstallSourceTool(b.ctx, t.reg, t.host, root, b.balanceClient, t.specOptions, opts.Stderr)
	var liveCommands func() []command.Command
	if opts.Composition.Live() {
		liveCommands = func() []command.Command { return loadCommands(opts, root) }
	}
	registerSkillTools(t.reg, opts.Ablation, b.prompt.skillStore, b.prompt.implicitSkills, t.runners, t.cmds, liveCommands)

	t.caps = newCapabilitySurface(b.ctx, cfg, root, t.specOptions, t.host, t.reg, b.prompt.skillStore, b.model.profile, t.mcp.enabled, t.mcp.extra...)
	if opts.Composition.Live() {
		t.caps.runtime.ConfigureComposition(opts.Composition.Resolved)
		t.caps.runtime.ConfigureInventorySource(b.liveMCPInventory())
		t.reg.BindMCPRuntime(t.caps.runtime.BindRegisteredTool)
	}
	if _, ok := t.reg.Get(plugin.ResourceReadToolName); ok {
		t.reg.Add(t.caps.runtime.ResourceReadTool())
	}
	t.skillRun.capRuntime = t.caps.runtime
	addExtensionTools(t.reg, b.ext.mgr, b.ext.warn)
	if opts.Composition.Live() {
		t.caps.refresh = func() { addExtensionTools(t.reg, b.ext.mgr, b.ext.warn) }
	}
	t.reg.Add(t.caps.proxy)
	gateSkillsOnCatalog(b.prompt.skillStore, t.caps.catalog)
	return nil
}

// controller builds the executor, the optional planner around it, and the
// controller that drives them, then binds what could only exist after it.
func (b *builder) controller() (*control.Controller, error) {
	t := &b.tools
	executor := b.executor()
	runner, label, err := t.roles.planner(b.opts, executor, b.model.entry.Model, b.prompt.memory.StaticContext(), t.caps.runtime)
	if err != nil {
		return nil, err
	}
	ctrlOpts := b.controllerOptions(runner, executor, label)
	ctrl := withWindowPosture(control.New(ctrlOpts), b.cfg, b.opts.StatsSource, b.sink)
	if b.opts.NewConversation && b.cfg.DefaultPlanMode() {
		ctrl.SetPlanMode(true)
	}
	reportDormantPermissionRules(b.sink, b.cfg, ctrl)
	b.ext.publish(ctrl)
	if t.caps.runtime != nil {
		ctrl.SetCapabilityProxyTools(t.caps.runtime.ConnectedProxyTools)
	}
	if t.caps.runtime != nil {
		t.runner.SetCapabilityRuntime(t.caps.runtime)
	}
	router := t.roles.semanticRouter(t.sub, b.execProv, b.model.ref, b.model.entry.Price, t.caps.audit)
	ctrl.WireCapabilityRouting(b.cfg.Plugins, t.caps.specs, router, t.caps.audit)
	ctrl.SetCapabilityProxyRouting(true)
	reportKeptTools(b.sink, t.reg.DisableTools(b.cfg.DisabledTools()))
	// Every role setting sees one provider-visible surface, fixed before the
	// snapshot freezes registry schemas for cache diagnostics.
	if b.opts.Observe == nil {
		pinned := pinnedMCPServers(t.mcp.alwaysLoad, t.mcpSchemaKnown)
		if b.opts.Composition.Live() {
			pinned = nil
		}
		applyUnifiedProviderToolSurface(t.reg, b.opts.GoalTurnsUnreachable, b.opts.Ablation, pinned)
	}
	if binding := b.opts.Composition; binding != nil {
		if binding.Digest != "" {
			t.reg.SetProviderVisibleTools(binding.ToolSurface)
		} else if !binding.Live() && binding.Version.Definition.Tools.Mode == "select" {
			t.reg.SetProviderVisibleTools(nil)
		}
	}
	return ctrl, nil
}

func (b *builder) executor() *agent.Agent {
	cfg, entry, t := b.cfg, b.model.entry, &b.tools
	triageProv, triageRef, triagePrice := resolveTriage(cfg, b.model.ref, b.proxy)
	ag := agent.New(b.execProv, t.reg, newObservedSession(b.prompt.prompt), agent.Options{
		MaxSteps:       t.maxSteps,
		MaxStepsKey:    b.opts.MaxStepsKey,
		Temperature:    cfg.Agent.Temperature,
		ProgressWatch:  control.ProgressWatchFromConfig(cfg),
		Pricing:        entry.Price,
		ModelRef:       b.model.ref,
		TriageProvider: triageProv, TriageModelRef: triageRef, TriagePricing: triagePrice,
		ScreenExternalContent:   cfg.Agent.ScreenExternalContent,
		MaxPerseverationRetries: perseverationRetries(cfg, entry),
		Gate:                    t.gate,
		CheckTargetAccess:       t.roles.checkTargetAccess,
		Hooks:                   t.hookRunner,
		Jobs:                    b.session.jobs,
		// Reserving writes at the executor entry covers every writer, late MCP
		// adds included, without wrapping tool schemas.
		WriteScheduler:     t.sub.scheduler,
		WriteWorkspaceRoot: b.root, ExtraWorkspaceRoots: b.workspaceExtraRoots(), WorkspaceScanLimit: b.opts.WorkspaceScanLimit, WorkspaceVCS: b.prompt.workspaceVCS, WorkspaceUnversioned: b.prompt.workspaceVCS == "", RenderRoot: renderRoot(t.browser, entry, b.root),
		ProjectChecks: b.prompt.projectChecks, ProjectSensitivePaths: b.prompt.sensitivePaths,
		AgentPreset:       b.model.preset,
		DeliveryProfile:   b.model.delivery,
		Ablation:          b.opts.Ablation,
		WorkspaceLease:    b.session.lease,
		CapabilityLedger:  t.caps.ledger,
		CapabilityAudit:   t.caps.audit,
		ContextWindow:     entry.ContextWindow,
		CompactRatio:      cfg.Agent.CompactRatio,
		RecentKeep:        cfg.Agent.RecentKeep,
		CompactionBudgets: compactionBudgets(cfg),
		ArchiveDir:        b.roots.ArchiveDir(),
		KeepPolicy:        b.keep,
		ReasoningLanguage: cfg.ReasoningLanguage(),
		SubagentDepth:     0,
		MaxSubagentDepth:  t.sub.maxDepth,
		ProviderRetries:   agent.ProviderRetriesFromConfig(cfg.Agent.MaxProviderRetries),
	}, b.sink)
	ag.SetNativeVideo(nativeVideoSource(entry, cfg))
	return ag
}

// perseverationRetries resolves the degenerate-generation-loop budget for the
// active provider: the provider's own setting when it has one, else the global
// [progress_watch].perseveration_retries default. Unset reports; negative
// disables.
func perseverationRetries(cfg *config.Config, entry *config.ProviderEntry) *int {
	if entry != nil && entry.PerseverationRetries != nil {
		return entry.PerseverationRetries
	}
	return cfg.ProgressWatch.PerseverationRetries
}

func (b *builder) controllerOptions(runner agent.Runner, executor *agent.Agent, label string) control.Options {
	opts, cfg, root, entry, t := b.opts, b.cfg, b.root, b.model.entry, &b.tools
	specOptions := t.specOptions
	providerIdentity := ResolveProviderBuildIdentity(entry, b.proxy, nil)
	return control.Options{
		Observe:                        b.observeRun(),
		GoalTokenBudget:                cfg.Agent.GoalTokenBudget,
		Runner:                         runner,
		Executor:                       executor,
		Sink:                           b.sink,
		QuoteContext:                   b.quoteContext,
		Policy:                         t.policy,
		SubagentGate:                   t.gate,
		Label:                          label,
		ModelRef:                       b.model.ref,
		Effort:                         providerIdentity.Effort,
		ProviderFingerprint:            providerIdentity.Fingerprint,
		ModelModes:                     config.RequestModes(entry),
		ModelEntry:                     entry,
		SystemPrompt:                   b.prompt.prompt,
		SessionDir:                     b.session.dir,
		Peers:                          peersOptions(cfg, t.peerLink, t.peerTools),
		Subagents:                      subagentReader(t.subagents),
		Settings:                       t.settingLink,
		Host:                           t.host,
		Commands:                       t.cmds,
		Skills:                         b.prompt.skills,
		AllSkills:                      b.prompt.allSkills,
		SkillStore:                     b.prompt.skillStore,
		AllSkillStore:                  b.prompt.allSkillStore,
		Clock:                          b.clock(),
		DisableImplicitSkillInvocation: !b.prompt.implicitSkills || b.opts.Observe != nil,
		SkillRunner:                    t.runners.run,
		ReadOnlySkillRunner:            t.runners.readOnly,
		SideFactory:                    t.skillRun.newSide,
		SkillProfile:                   t.runners.profile,
		Hooks:                          t.hookRunner,
		Memory:                         b.prompt.memory,
		// Read at Close time: freeze chains the extension runtime set onto it.
		Cleanup:               func() { defer b.closeLifetime(); b.cleanup() },
		Balance:               opts.BalanceStore.Cache(b.keyedClient, entry.BalanceURL, billing.Format(entry.BalanceFormat), entry.APIKey()),
		Tariff:                func(at time.Time) (pricing.Tariff, bool) { return b.cfg.LiveTariff(b.model.ref, at) },
		Feedback:              control.FeedbackOptions{Service: feedbackService(b.roots.Home(), b.proxy, opts.AccountsURL), ProviderKind: feedbackProviderKind(entry), Surface: opts.FeedbackSurface},
		Jobs:                  b.session.jobs,
		TaskStore:             opts.TaskStore,
		WorkspaceLease:        b.session.lease,
		Registry:              t.reg,
		PluginCtx:             b.ctx,
		MCPDefaultCallTimeout: specOptions.DefaultCallTimeout,
		MCPConfigureSpec: func(spec *plugin.Spec) {
			if spec == nil {
				return
			}
			spec.LaunchManager = specOptions.LaunchManager
			spec.Package = strings.TrimSpace(specOptions.PackageOwners[spec.Name])
			if strings.TrimSpace(spec.ConfigSource) == "" {
				spec.ConfigSource = specOptions.ConfigSource
			}
			if spec.DefaultStartupTimeout <= 0 {
				spec.DefaultStartupTimeout = specOptions.DefaultStartupTimeout
			}
			pluginspec.ApplyIsolation(spec, root, specOptions)
		},
		CapabilityRuntime:      t.caps.runtime,
		WorkspaceRoot:          root,
		WorkspaceRepo:          b.repo,
		Posture:                control.PostureEvidence{WritesConfined: sandbox.IntegrityEnforced(t.env.bash), Home: b.roots.Home()},
		ExternalFolderToolRefs: t.env.readPaths,
		ResponseLanguage:       cfg.ResponseLanguage(),
		ReasoningLanguage:      cfg.ReasoningLanguage(),
		TurnNarration:          cfg.TurnNarration(),
		EmbeddedDiffDetection:  cfg.EmbeddedDiffDetectionEnabled(),
		Shell:                  b.shell,
		ApprovalTimeout:        opts.ApprovalTimeout,
		RuntimeProfile:         b.model.profile,
		Ablation:               opts.Ablation,
		OnRemember: func(rule string) control.RememberResult {
			return rememberPermissionRule(b.roots, root, rule)
		},
		SessionRecoveryMeta: opts.SessionRecoveryMeta,
		OnSessionRecovered:  opts.OnSessionRecovered,
		// Nil without provider-declaring sidecars; otherwise frontends list plugin/... models through it.
		ProviderResolver:  b.providers.extension,
		RuntimeGeneration: b.ext.generation,
		RuntimeOwner:      b.owner,
		// The manager bash and grep were bound to, so tools and controller share one generation.
		SessionTemp:      t.env.sessionTemp,
		BrowserSession:   t.browser,
		Guardian:         t.roles.guardian(),
		RecoveryReviewer: t.roles.recoveryReviewer(b.model.ref),
		// HeadlessApprovalMode declares the frontend has no decision channel;
		// ApprovalTimeout is not a proxy for that.
		RecoveryHeadless: recoveryHeadlessMode(opts),
		GoalEvaluator:    goalEvaluator(cfg, b.model.ref, b.proxy, b.sink),
		PromptRefiner:    promptRefiner(entry, b.proxy, b.sink),
		CommitMessenger:  commitMessenger(entry, b.proxy, b.sink),
	}
}

// freeze assembles the extension snapshot from the objects this build wired,
// hands the sidecars to its runtime set, and binds the result to the
// controller. The snapshot records the base provider catalog: sidecar
// providers enter through the manager's own contributions.
func (b *builder) freeze(ctrl *control.Controller) (*BuildResult, error) {
	t, ext := &b.tools, b.ext
	snap, runtimeSet, dispatcher, snapErr := assembleLegacySnapshot(b.ctx, legacyAssembly{
		systemPrompt: b.prompt.prompt,
		registry:     t.reg,
		skills:       b.prompt.skills,
		commands:     t.cmds,
		hooks:        t.hooks,
		mcpSpecs:     enabledMCPSpecs(t.configSpecs, t.mcp.extra),
		providers:    b.providers.base.Catalog(),
	}, ext.generation, extensionBoot{
		session:            ext.session(),
		ui:                 ext.hub,
		onWarning:          ext.warn,
		onSidecarDown:      ext.sidecarDown,
		skipPromptStrategy: shouldSkipPromptStrategy(b.opts.PreviousPlan),
	}, ext.mgr)
	// Assembly owns the sidecars on every path: closed inside, or in the runtime set.
	b.pendingMgr = nil
	extensionMgr, hub := ext.mgr, ext.hub
	if snapErr != nil {
		if extensionContractBroken(snapErr) {
			ctrl.ReleaseResources()
			return nil, fmt.Errorf("boot: %w", snapErr)
		}
		slog.Warn("boot: extension snapshot assembly failed; continuing without a runtime snapshot", "err", snapErr)
		runtimeSet = extension.NewRuntimeSet(ext.generation)
		// The failed assembly already retired the sidecars; bind neither hub nor manager.
		extensionMgr = nil
	}
	installSidecarStreamRouters(extensionMgr, b.providers.extension)
	providerResolver := b.providers.base
	if b.providers.extension != nil {
		providerResolver = b.providers.extension
	}
	b.cleanup = wireRuntimeScopeCleanup(runtimeSet, b.cleanup, b.opts.SharedHost, t.host, t.lsp, b.opts.SessionTemp)
	ctrl.SetExtensions(dispatcher)
	if extensionMgr == nil {
		hub = nil
	} else {
		ctrl.SetExtensionUI(hub)
	}
	if providerResolver != nil {
		ctrl.SetProviderResolver(providerResolver)
	}
	// A prompt strategy may have replaced the prompt the executor session was
	// built with; swap it in before any turn so session and snapshot agree.
	if snap != nil {
		if final := snap.SystemPrompt(); final != b.prompt.prompt {
			ctrl.ApplyExtensionSystemPrompt(final)
		}
	}
	ctrl.SealObserveSurface()
	ctrl.ConfigureCompositions(b.roots.Home(), nil)
	if err := b.finishComposition(ctrl, snap); err != nil {
		ctrl.ReleaseResources()
		return nil, err
	}
	assembly := &ReusedAssembly{
		SystemPrompt:            b.prompt.prompt,
		Skills:                  b.prompt.skills,
		Commands:                t.cmds,
		Hooks:                   t.hooks,
		Registry:                t.reg,
		ImplicitSkillInvocation: b.prompt.implicitSkills,
		Memory:                  b.prompt.memory,
		ProjectChecks:           b.prompt.projectChecks, ProjectSensitivePaths: b.prompt.sensitivePaths,
	}
	return finalizeBuildResult(b.roots, &BuildResult{ComputerDriver: b.opts.ComputerDriver, Controller: ctrl, Snapshot: snap, Runtime: runtimeSet, Owner: b.owner, Extensions: extensionMgr, Dispatcher: dispatcher, ExtensionUI: hub, ProviderResolver: providerResolver, BaseProviderResolver: b.providers.base, Assembly: assembly, Phases: b.timer.done("assemble")}, !b.opts.deferPublish), nil
}

```

## internal/runtime/agent/tool_output_spill.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/runtime/agent/tool_output_spill.go

SHA-256: `727e0d1331616b1f647948d984ab1fbcf8b089a7e025e726ab581367dc9883ce`

```
package agent

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reasonix/internal/state/sessionstore"
	"runtime"
	"strings"

	"reasonix/internal/base/textutil"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/tool"
	"reasonix/internal/state/store"
)

// Oversize tool output goes to a file; the context keeps a pointer. A head+tail
// sketch is a guess about which part mattered, made by the wrong party — only
// the model knows, and read_file plus grep let it decide.

// A spill is a fixed price: the instructions, plus at most the same again in
// preview. Counting the preview in lines would instead make a wide result cost
// more to leave context than a narrow one — the opposite of the point.

// spillToolOutput writes body beside the session and returns the pointer that
// replaces it, plus where it landed. ok is false when there is nowhere the model
// could read the file back from, or when spilling would not pay for itself.
// There is deliberately no size threshold: context pays the pointer, the pointer
// is a pure function of the body, so the decision is derived rather than configured.
func (a *Agent) spillToolOutput(body, toolName, toolCallID string) (string, string, bool) {
	dir := a.spillDir()
	if dir == "" {
		return "", "", false
	}
	path := filepath.Join(dir, spillFileName(toolName, toolCallID))
	pointer := spillPointer(path, body, toolName)
	if !spillPays(len(body), len(pointer)) {
		return "", "", false
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", "", false
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		return "", "", false
	}
	return pointer, path, true
}

// spillPays reports whether moving a result out of context earns its keep. What
// is spent is the pointer plus the turn spent reading it back, and that turn was
// what comparing bytes alone kept leaving out: a body a single fetch recovers
// whole buys both to arrive exactly where leaving it would have. Past that limit
// the model reads a window instead, which is the case a pointer exists for.
func spillPays(body, pointer int) bool {
	if body <= MaxToolOutputBytes {
		return false
	}
	return body-pointer >= pointer
}

// spillDir is where this agent's oversize results go: beside its transcript
// when it has one, the archive it inherited otherwise, and failing both a
// scratch directory. The last is what keeps the lossless path open — with
// nowhere to write, the only remaining bound is truncation, which discards
// almost all of a large result rather than parking it.
func (a *Agent) spillDir() string {
	if dir := store.SessionOutputsDir(a.SessionPath()); dir != "" {
		return dir
	}
	if dir := a.archiveOutputsDir(); dir != "" {
		return dir
	}
	return sessionstore.ScratchOutputsDir()
}

// toolIsPaged reports whether this tool addresses its own continuation, read off
// its contract rather than its name.
func (a *Agent) toolIsPaged(name string) bool {
	p, ok := toolCapability[tool.Paged](a.svc.tools, name)
	return ok && p.Paged()
}

// toolCapability type-asserts a registered tool to one of its optional capability
// interfaces. An unregistered tool and one lacking the capability are the same
// answer to a caller, so both report false.
func toolCapability[T any](reg *tool.Registry, name string) (T, bool) {
	var zero T
	if reg == nil {
		return zero, false
	}
	t, ok := reg.Get(name)
	if !ok {
		return zero, false
	}
	c, ok := t.(T)
	return c, ok
}

// readsSpilledOutput reports whether this call is the model fetching a spilled
// result back. Such a fetch must never spill: it would return a pointer to a
// further file, and read_file's line numbers grow the body each round, so the
// loop has no exit. The tool names the path it reads because only it knows its
// own arguments; a shell names none, so its output spills as usual.
func (a *Agent) readsSpilledOutput(toolName, toolArgs string) bool {
	rt, ok := toolCapability[tool.ReadTargeter](a.svc.tools, toolName)
	if !ok {
		return false
	}
	target := rt.ReadTarget(json.RawMessage(toolArgs))
	if target == "" {
		return false
	}
	abs, err := filepath.Abs(target)
	if err != nil {
		return false
	}
	// A spill lands directly in the directory, so the file's own parent is the
	// only candidate; grep aimed at the directory itself matches as-is.
	for _, dir := range []string{a.spillDir(), a.archiveOutputsDir()} {
		if dir == "" {
			continue
		}
		if sameDir(filepath.Dir(abs), dir) || sameDir(abs, dir) {
			return true
		}
	}
	return false
}

// sameDir compares paths the way the host filesystem resolves them. Erring
// toward equal is the safe side: a false match only forgoes one spill, while a
// missed one restores the read-back loop.
func sameDir(a, b string) bool {
	a, b = filepath.Clean(a), filepath.Clean(b)
	if runtime.GOOS == "windows" || runtime.GOOS == "darwin" {
		return strings.EqualFold(a, b)
	}
	return a == b
}

// archiveOutputsDir is where an agent with no transcript of its own spills.
// Sub-agents are the case that matters: they carry no session path, so without
// this every oversize result they saw was truncated while the lossless path sat
// one field away. The archive already holds content moved out of context, and
// they inherit its location from the parent.
func (a *Agent) archiveOutputsDir() string {
	root := strings.TrimSpace(a.archiveDir)
	if root == "" {
		return ""
	}
	return filepath.Join(root, "outputs")
}

// spillFileName keys the file by tool call, so a directory listing reads as the
// turn's history and a re-read finds the same file.
func spillFileName(toolName, toolCallID string) string {
	base := strings.TrimSpace(toolCallID)
	if base == "" {
		base = strings.TrimSpace(toolName)
	}
	if base == "" {
		base = "output"
	}
	safe := make([]rune, 0, len(base))
	for _, r := range base {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_':
			safe = append(safe, r)
		default:
			safe = append(safe, '-')
		}
	}
	trimmed := strings.Trim(string(safe), "-")
	if trimmed == "" {
		trimmed = "output"
	}
	if len(trimmed) > 96 {
		trimmed = trimmed[:96]
	}
	return trimmed + ".txt"
}

// spillPointer is what the model sees: the size, the path, how to get the rest,
// then as much of the start as the instructions themselves cost.
func spillPointer(path, body, toolName string) string {
	instr := spillInstructions(path, body, toolName)
	head := headWithin(body, len(instr))
	if head == "" {
		return instr
	}
	return instr + "\nOpening lines:\n" + head + "\n"
}

// spillInstructions is the irreducible part: where the output went and how to
// read it back. Its size is what a spill really costs, so it also bounds the
// preview — the model is told the shape, not shown a guess at the substance.
func spillInstructions(path, body, toolName string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "[%s output kept out of context: %d lines, %s]\n", displayToolName(toolName), strings.Count(body, "\n")+1, textutil.HumanBytes(int64(len(body))))
	fmt.Fprintf(&b, "Full output: %s\n", path)
	b.WriteString("Read a window of it with read_file (offset/limit), or search it with grep. It is not in this conversation.\n")
	return b.String()
}

// headWithin returns the whole leading lines of body that fit in max bytes, so a
// preview never cuts mid-line and never exceeds its budget.
func headWithin(body string, max int) string {
	end := 0
	for end < len(body) {
		nl := strings.IndexByte(body[end:], '\n')
		if nl < 0 {
			if len(body) <= max {
				end = len(body)
			}
			break
		}
		if end+nl+1 > max {
			break
		}
		end += nl + 1
	}
	return strings.TrimRight(body[:end], "\n")
}

func displayToolName(name string) string {
	if n := strings.TrimSpace(name); n != "" {
		return n
	}
	return "tool"
}

// boundToolOutput is the single gate every tool result passes before it becomes
// context. The bound records which of the three outcomes happened, so a frontend
// can tell a result that moved to disk from one that lost its middle; the last
// return is the truncation notice, empty unless something was lost. failed comes
// from the caller because reading it back out of the body would guess at wording.
func (a *Agent) boundToolOutput(body, toolName, toolCallID, toolArgs string, failed bool) (string, event.OutputBound, string) {
	bound := event.OutputBound{Lines: strings.Count(body, "\n") + 1, Bytes: len(body)}
	paged := a.toolIsPaged(toolName) || a.readsSpilledOutput(toolName, toolArgs)
	if !paged {
		if pointer, path, ok := a.spillToolOutput(body, toolName, toolCallID); ok {
			bound.Kind, bound.Path = event.BoundSpilled, path
			return pointer, bound, ""
		}
	}
	if len(body) <= MaxToolOutputBytes {
		bound.KeptBytes = len(body)
		return body, bound, ""
	}
	// A paged read addresses its own continuation, so cutting it back to a leading
	// window discards nothing: the model reads on from where this stops. Snipping
	// its middle would, and would also undo the paging the tool already did.
	if paged {
		out := windowToolOutput(body, MaxToolOutputBytes)
		bound.Kind, bound.KeptBytes = event.BoundWindowed, len(out)
		return out, bound, ""
	}
	strategy := a.window().snipStrategyFor(toolName)
	if failed {
		strategy = strategy.forFailure(MaxToolOutputBytes)
	}
	out, notice := truncateToolOutputFor(body, toolName, toolCallID, MaxToolOutputBytes, strategy)
	bound.Kind, bound.KeptBytes = event.BoundTruncated, len(out)
	return out, bound, notice
}

const windowMarker = "\n…[window ends here to fit one result — nothing was discarded; read on from where this stops]…\n"

// windowToolOutput cuts body back to whole leading lines that fit in cap. What
// is left out stays reachable by continuing the page, so this is a window, not
// a loss — which is why it carries no truncation notice.
func windowToolOutput(body string, cap int) string {
	keep := cap - len(windowMarker) - 8
	if keep < 1 || len(body) <= keep {
		return body
	}
	cut := strings.LastIndexByte(body[:keep], '\n')
	if cut <= 0 {
		cut = len(snapToRuneBoundary(body, 0, keep))
	}
	return body[:cut] + windowMarker
}

```

## internal/runtime/usecap/usecapability.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/runtime/usecap/usecapability.go

SHA-256: `2b55a18b236f997796e269a83c990d93d79f2efde71144c23b7a8df6c90a0315`

```
package usecap

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"sort"
	"strings"
	"sync"
	"time"

	"reasonix/internal/contract/agentcomposition"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/tool"
	"reasonix/internal/ext/plugin"
	"reasonix/internal/runtime/capability"
)

// MCPCapabilityRuntime is the session-shared MCP substrate: Host, boot specs,
// provider-visible registry for already-registered tools, schema cache catalog,
// and live connection snapshots. Each agent (executor, planner, task/fleet
// child) gets its own UseCapabilityTool frontend so ledger/audit never cross
// agent boundaries, while process connections remain on the shared Host.
type MCPCapabilityRuntime struct {
	lifeCtx  context.Context
	host     *plugin.Host
	registry *tool.Registry
	catalog  func() capability.Catalog

	// dispatchMu linearizes server enable/spec mutations against MCP process
	// startup and tools/call. Calls may run concurrently under RLock; a disable,
	// uninstall, or hot update waits for in-flight dispatch and invalidates every
	// target that has not begun its final runtime-bound execution check.
	dispatchMu        sync.RWMutex
	compositionFrozen bool
	composition       *agentcomposition.Definition
	source            *inventorySource
	mu                sync.RWMutex
	servers           map[string]mcpRuntimeServer
	gates             mcpServerGates
	// shared connection observation across all frontends on this session.
	state *mcpProxySharedState
}

type mcpRuntimeServer struct {
	entry      config.PluginEntry
	spec       plugin.Spec
	enabled    bool
	cached     []plugin.CachedTool
	cacheKeyOK bool
}

type mcpProxySharedState struct {
	mu        sync.Mutex
	connected map[string]bool
	liveTools map[string][]plugin.CachedTool
}

// NewMCPCapabilityRuntime builds the session-shared MCP substrate. lifeCtx owns
// on-demand MCP child process lifetimes; specs must be the boot-converted specs.
func NewMCPCapabilityRuntime(lifeCtx context.Context, host *plugin.Host, specs []plugin.Spec, reg *tool.Registry, catalog func() capability.Catalog) *MCPCapabilityRuntime {
	r := &MCPCapabilityRuntime{
		lifeCtx:  lifeCtx,
		host:     host,
		registry: reg,
		catalog:  catalog,
		servers:  map[string]mcpRuntimeServer{},
		state:    &mcpProxySharedState{connected: map[string]bool{}},
	}
	r.ConfigureServers(nil, specs, nil)
	return r
}

// ConfigureServers replaces the runtime's configured MCP inventory. enabled is
// keyed by server name; nil keeps the standalone/test default that every spec is
// enabled. Boot passes the activation-resolved set so disabled servers are
// visible to discovery but cannot reuse a sibling tab's shared Host client.
func (r *MCPCapabilityRuntime) ConfigureServers(entries []config.PluginEntry, specs []plugin.Spec, enabled map[string]bool) {
	if r == nil {
		return
	}
	r.dispatchMu.Lock()
	defer r.dispatchMu.Unlock()
	byName := make(map[string]config.PluginEntry, len(entries))
	for _, entry := range entries {
		name := strings.TrimSpace(entry.Name)
		if name != "" {
			byName[name] = runtimePluginEntry(entry)
		}
	}
	next := make(map[string]mcpRuntimeServer, len(specs))
	for _, raw := range specs {
		spec := cloneMCPSpec(raw)
		if !r.compositionAllows(spec) {
			continue
		}
		name := strings.TrimSpace(spec.Name)
		if name == "" {
			continue
		}
		entry, ok := byName[name]
		if !ok {
			entry = config.PluginEntry{Name: name}
		}
		isEnabled := true
		if enabled != nil {
			isEnabled = enabled[name]
		}
		cached, keyOK := cachedToolsForSpec(spec)
		next[name] = mcpRuntimeServer{
			entry:      entry,
			spec:       spec,
			enabled:    isEnabled,
			cached:     cached,
			cacheKeyOK: keyOK,
		}
	}
	r.mu.Lock()
	previous := r.servers
	r.servers = next
	r.mu.Unlock()
	for name, old := range previous {
		current, exists := next[name]
		if !exists || !current.enabled || old.spec.Authorized != current.spec.Authorized || !plugin.MCPRuntimeSpecMatches(old.spec, current.spec) {
			r.state.clearServer(name)
			if r.source != nil && r.host != nil {
				r.host.Remove(name)
			}
		}
	}
	for name := range next {
		if !next[name].enabled {
			r.state.clearServer(name)
		}
	}
}

// SetServerEnabled revokes or restores this controller's right to use a server.
// It is intentionally independent from Host connectivity because desktop tabs
// may share one Host while keeping different enable states.
func (r *MCPCapabilityRuntime) SetServerEnabled(name string, enabled bool) bool {
	if r == nil {
		return false
	}
	r.dispatchMu.Lock()
	defer r.dispatchMu.Unlock()
	name = strings.TrimSpace(name)
	r.mu.Lock()
	server, ok := r.servers[name]
	if ok {
		server.enabled = enabled
		r.servers[name] = server
	}
	r.mu.Unlock()
	if ok && !enabled {
		r.state.clearServer(name)
	}
	return ok
}

// RemoveServer removes an uninstalled/runtime-only MCP from discovery and
// clears any live tool snapshot that could otherwise keep it routable.
func (r *MCPCapabilityRuntime) RemoveServer(name string) bool {
	if r == nil {
		return false
	}
	r.dispatchMu.Lock()
	defer r.dispatchMu.Unlock()
	name = strings.TrimSpace(name)
	r.mu.Lock()
	_, ok := r.servers[name]
	delete(r.servers, name)
	r.mu.Unlock()
	r.state.clearServer(name)
	return ok
}

// CatalogState returns deterministic, privacy-minimal routing inputs for this
// controller. Configuration secrets are never copied into the transient route.
func (r *MCPCapabilityRuntime) CatalogState() (entries []config.PluginEntry, cached map[string][]plugin.CachedTool, keyOK map[string]bool, disabled map[string]bool) {
	if r == nil {
		return nil, nil, nil, nil
	}
	r.dispatchMu.RLock()
	defer r.dispatchMu.RUnlock()
	return r.catalogStateLocked()
}

// CapabilityCatalogState returns configuration and live proxy tools from one
// lifecycle generation. Callers must use this combined snapshot when building
// a route: taking the two halves separately can otherwise pair a just-updated
// spec with a stale pre-update live-tool directory.
func (r *MCPCapabilityRuntime) CapabilityCatalogState() (entries []config.PluginEntry, cached map[string][]plugin.CachedTool, keyOK map[string]bool, disabled map[string]bool, proxyTools map[string][]plugin.CachedTool) {
	if r == nil {
		return nil, nil, nil, nil, nil
	}
	if r.refreshInventory() != nil {
		return nil, nil, nil, nil, nil
	}
	r.dispatchMu.RLock()
	defer r.dispatchMu.RUnlock()
	entries, cached, keyOK, disabled = r.catalogStateLocked()
	proxyTools = r.connectedProxyToolsLocked()
	return entries, cached, keyOK, disabled, proxyTools
}

func (r *MCPCapabilityRuntime) catalogStateLocked() (entries []config.PluginEntry, cached map[string][]plugin.CachedTool, keyOK map[string]bool, disabled map[string]bool) {
	r.mu.RLock()
	names := make([]string, 0, len(r.servers))
	for name := range r.servers {
		names = append(names, name)
	}
	sort.Strings(names)
	entries = make([]config.PluginEntry, 0, len(names))
	cached = make(map[string][]plugin.CachedTool, len(names))
	keyOK = make(map[string]bool, len(names))
	disabled = make(map[string]bool)
	for _, name := range names {
		server := r.servers[name]
		entries = append(entries, runtimePluginEntry(server.entry))
		if len(server.cached) > 0 {
			cached[name] = cloneCachedTools(server.cached)
			keyOK[name] = server.cacheKeyOK
		}
		if !server.enabled {
			disabled[name] = true
		}
	}
	r.mu.RUnlock()
	if len(cached) == 0 {
		cached = nil
		keyOK = nil
	}
	if len(disabled) == 0 {
		disabled = nil
	}
	return entries, cached, keyOK, disabled
}

func (r *MCPCapabilityRuntime) configuredServers() []mcpRuntimeServer {
	if r == nil {
		return nil
	}
	r.mu.RLock()
	names := make([]string, 0, len(r.servers))
	for name := range r.servers {
		names = append(names, name)
	}
	sort.Strings(names)
	out := make([]mcpRuntimeServer, 0, len(names))
	for _, name := range names {
		server := r.servers[name]
		server.spec = cloneMCPSpec(server.spec)
		server.entry = runtimePluginEntry(server.entry)
		server.cached = cloneCachedTools(server.cached)
		out = append(out, server)
	}
	r.mu.RUnlock()
	return out
}

func (r *MCPCapabilityRuntime) enabledSpec(server string) (plugin.Spec, bool) {
	if r == nil {
		return plugin.Spec{}, false
	}
	r.mu.RLock()
	configured, ok := r.servers[strings.TrimSpace(server)]
	r.mu.RUnlock()
	if !ok || !configured.enabled {
		return plugin.Spec{}, false
	}
	return cloneMCPSpec(configured.spec), true
}

func (r *MCPCapabilityRuntime) serverEnabled(server string) bool {
	if r == nil {
		return false
	}
	r.mu.RLock()
	configured, ok := r.servers[strings.TrimSpace(server)]
	r.mu.RUnlock()
	return ok && configured.enabled
}

func cachedToolsForSpec(spec plugin.Spec) ([]plugin.CachedTool, bool) {
	cached, keyOK := capability.LoadCachedToolsForSpecs([]plugin.Spec{spec})
	return cloneCachedTools(cached[spec.Name]), keyOK[spec.Name]
}

func runtimePluginEntry(entry config.PluginEntry) config.PluginEntry {
	out := config.PluginEntry{Name: strings.TrimSpace(entry.Name), Source: entry.Source}
	if entry.AutoStart != nil {
		value := *entry.AutoStart
		out.AutoStart = &value
	}
	return out
}

func cloneCachedTools(in []plugin.CachedTool) []plugin.CachedTool {
	if len(in) == 0 {
		return nil
	}
	out := make([]plugin.CachedTool, len(in))
	copy(out, in)
	for i := range out {
		out[i].Schema = append(json.RawMessage(nil), in[i].Schema...)
	}
	return out
}

func cloneMCPSpec(in plugin.Spec) plugin.Spec {
	out := in
	out.Args = append([]string(nil), in.Args...)
	out.LaunchArgs = append([]string(nil), in.LaunchArgs...)
	out.LauncherIdentityArgs = append([]string(nil), in.LauncherIdentityArgs...)
	out.Env = cloneStringMap(in.Env)
	out.Headers = cloneStringMap(in.Headers)
	if in.ToolTimeouts != nil {
		out.ToolTimeouts = make(map[string]time.Duration, len(in.ToolTimeouts))
		maps.Copy(out.ToolTimeouts, in.ToolTimeouts)
	}
	return out
}

func cloneStringMap(in map[string]string) map[string]string {
	if in == nil {
		return nil
	}
	out := make(map[string]string, len(in))
	maps.Copy(out, in)
	return out
}

// NewFrontend returns a per-agent use_capability instance. ledger/audit may be
// nil for ordinary sub-agents that do not run Delivery capability gates.
func (r *MCPCapabilityRuntime) NewFrontend(ledger *capability.Ledger, audit *capability.Audit) *UseCapabilityTool {
	if r == nil {
		return NewUseCapabilityTool(context.Background(), nil, nil, nil, ledger, audit, nil)
	}
	return &UseCapabilityTool{
		host:     r.host,
		lifeCtx:  r.lifeCtx,
		runtime:  r,
		registry: r.registry,
		ledger:   ledger,
		audit:    audit,
		catalog:  r.catalog,
		state:    r.state,
	}
}

// ConnectedProxyTools returns live tool metadata for servers connected through
// any frontend on this runtime, keyed by server name.
func (r *MCPCapabilityRuntime) ConnectedProxyTools() map[string][]plugin.CachedTool {
	if r == nil || r.state == nil {
		return nil
	}
	r.dispatchMu.RLock()
	defer r.dispatchMu.RUnlock()
	return r.connectedProxyToolsLocked()
}

func (r *MCPCapabilityRuntime) connectedProxyToolsLocked() map[string][]plugin.CachedTool {
	live := r.state.snapshotLiveTools()
	if len(live) == 0 {
		return nil
	}
	r.mu.RLock()
	for name := range live {
		server, ok := r.servers[name]
		if !ok || !server.enabled {
			delete(live, name)
		}
	}
	r.mu.RUnlock()
	if len(live) == 0 {
		return nil
	}
	return live
}

// UseCapabilityTool is the stable MCP capability proxy for Delivery, the
// two-model Planner, and task/fleet sub-agents. It lists, inspects, calls, or
// declines catalog capabilities without adding dynamic MCP tools to the
// provider-visible registry — subsequent calls keep using this stable schema.
// Multiple frontends may share one MCPCapabilityRuntime (Host + connection
// state) while keeping independent ledger/audit.
type UseCapabilityTool struct {
	host *plugin.Host
	// lifeCtx is the session-scoped context that owns on-demand MCP child
	// processes (mirrors lazySpawn.ctx): a proxied server must outlive the tool
	// call that started it and die with the session, not with a resolve-phase
	// timeout. nil falls back to context.Background() for direct/test use.
	lifeCtx context.Context
	// specs are the boot-converted plugin specs (env expansion, workspace
	// overrides and timeouts). The proxy never rebuilds
	// specs from raw config entries — that would fork the conversion logic.
	specs     []plugin.Spec
	runtime   *MCPCapabilityRuntime
	registry  *tool.Registry // live registry for already-exposed MCP tools
	bindLocal func(tool.Tool) tool.Tool
	ledger    *capability.Ledger
	audit     *capability.Audit
	catalog   func() capability.Catalog
	// state is session-shared connection observation when built via
	// MCPCapabilityRuntime; nil falls back to a private map for tests.
	state *mcpProxySharedState
}

// BindLocalTools installs execution-local file state before frontend publication.
func (t *UseCapabilityTool) BindLocalTools(bind func(tool.Tool) tool.Tool) { t.bindLocal = bind }

// NewUseCapabilityTool builds a standalone capability proxy (tests and simple
// boots). Prefer MCPCapabilityRuntime.NewFrontend when multiple agents share
// one session Host.
func NewUseCapabilityTool(lifeCtx context.Context, host *plugin.Host, specs []plugin.Spec, reg *tool.Registry, ledger *capability.Ledger, audit *capability.Audit, catalog func() capability.Catalog) *UseCapabilityTool {
	return &UseCapabilityTool{
		host:     host,
		lifeCtx:  lifeCtx,
		specs:    append([]plugin.Spec(nil), specs...),
		registry: reg,
		ledger:   ledger,
		audit:    audit,
		catalog:  catalog,
		state:    &mcpProxySharedState{connected: map[string]bool{}},
	}
}

// CloneForAgent returns a new frontend sharing Host/specs/connection state but
// with independent ledger and audit (nil unless provided).
// Execution-local bindings are installed separately before publication.
func (t *UseCapabilityTool) CloneForAgent(ledger *capability.Ledger, audit *capability.Audit) *UseCapabilityTool {
	if t == nil {
		return nil
	}
	state := t.state
	if state == nil {
		state = &mcpProxySharedState{connected: map[string]bool{}}
	}
	return &UseCapabilityTool{
		host:     t.host,
		lifeCtx:  t.lifeCtx,
		specs:    t.specs,
		runtime:  t.runtime,
		registry: t.registry,
		ledger:   ledger,
		audit:    audit,
		catalog:  t.catalog,
		state:    state,
	}
}

func (*UseCapabilityTool) Name() string { return "use_capability" }

// CapabilityIDExamples are the concrete ids the proxy description names. Each
// must be listed, inspectable, and callable in a real assembly; the boot effect
// test that reads this stops the description from promising an id the catalog
// refuses.
var CapabilityIDExamples = []string{"tool:grep", "skill:review", "task:subagent"}

func (*UseCapabilityTool) Description() string {
	return "Search and call deferred tools, skills, and MCP through stable IDs such as tool:grep, skill:review, task:subagent, and mcp-tool:server/tool. Before saying a tool, network, or integration is unavailable, search by natural-language goal; results include ranked IDs and available schemas. list enumerates the catalog, inspect loads one definition, call invokes it, and decline records why a preferred capability is skipped. task:subagent delegates 1-8 tasks that run in parallel. Calls retain all permission, sandbox, and execution checks."
}

func (*UseCapabilityTool) ReadOnly() bool { return true }

func (*UseCapabilityTool) Schema() json.RawMessage {
	// Stable schema — must not change across turns or when MCP connects.
	// capability_id is optional for list/search; inspect/call/decline still
	// require it at resolve time. One intentional prefix upgrade; thereafter
	// install/connect churn does not change this schema.
	return json.RawMessage(`{
		"type":"object",
		"properties":{
			"action":{"type":"string","description":"search | list | inspect | call | decline"},
			"query":{"type":"string","description":"Natural-language search goal"},
			"limit":{"type":"integer","minimum":1,"maximum":20,"description":"Result limit; default 5"},
			"capability_id":{"type":"string","description":"Stable ID required by inspect, call, and decline"},
			"arguments":{"type":"object","description":"Target arguments for call"},
			"reason":{"type":"string","description":"Non-empty decline reason"}
		},
		"required":["action"]
	}`)
}

// ResolveCall implements tool.CallResolver so the agent can run permission,
// hooks, and evidence against the real MCP target before execution.
func (t *UseCapabilityTool) ResolveCall(ctx context.Context, args json.RawMessage) (tool.ResolvedCall, error) {
	var p struct {
		Action       string          `json:"action"`
		CapabilityID string          `json:"capability_id"`
		Query        string          `json:"query"`
		Limit        int             `json:"limit"`
		Arguments    json.RawMessage `json:"arguments"`
		Reason       string          `json:"reason"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return tool.ResolvedCall{}, fmt.Errorf("invalid args: %w", err)
	}
	action := strings.ToLower(strings.TrimSpace(p.Action))
	id := strings.TrimSpace(p.CapabilityID)
	base := tool.ResolvedCall{
		DisplayName:  "use_capability",
		ProxyAction:  action,
		CapabilityID: id,
		Args:         p.Arguments,
	}
	if t.runtime != nil {
		if err := t.runtime.refreshInventory(); err != nil {
			return tool.ResolvedCall{}, err
		}
	}
	if resolved, ok := t.resolveDisabled(base, id, ""); ok {
		return resolved, nil
	}
	switch action {
	case ActionSearch:
		out, err := t.searchCapabilities(p.Query, p.Limit)
		if err != nil {
			if t.audit != nil {
				t.audit.RecordMCPProxy(true, false, true)
			}
			return tool.ResolvedCall{}, err
		}
		if t.audit != nil {
			t.audit.RecordMCPProxy(true, false, false)
		}
		base.SkipExecute = true
		base.Result = out
		base.ReadOnly = true
		return base, nil
	case ActionList:
		out, err := t.listCapabilities()
		if err != nil {
			if t.audit != nil {
				t.audit.RecordMCPProxy(true, false, true)
			}
			return tool.ResolvedCall{}, err
		}
		if t.audit != nil {
			t.audit.RecordMCPProxy(true, false, false)
		}
		base.SkipExecute = true
		base.Result = out
		base.ReadOnly = true
		return base, nil
	case ActionInspect:
		if id == "" {
			return tool.ResolvedCall{}, fmt.Errorf("capability_id is required for action=inspect%s", misplacedArgumentHint(p.Arguments, "capability_id"))
		}
		out, err := t.inspect(ctx, id)
		if err != nil {
			if t.audit != nil {
				t.audit.RecordMCPProxy(true, false, true)
			}
			return tool.ResolvedCall{}, err
		}
		if t.audit != nil {
			t.audit.RecordMCPProxy(true, false, false)
		}
		base.SkipExecute = true
		base.Result = out
		base.ReadOnly = true
		return base, nil
	case ActionDecline:
		if id == "" {
			return tool.ResolvedCall{}, fmt.Errorf("capability_id is required for action=decline%s", misplacedArgumentHint(p.Arguments, "capability_id"))
		}
		reason := strings.TrimSpace(p.Reason)
		if reason == "" {
			return tool.ResolvedCall{}, fmt.Errorf("reason is required for action=decline")
		}
		// Decline must not skip require. The mutation itself is delayed until the
		// agent has applied its post-resolution host boundary.
		if t.ledger != nil {
			if e, ok := t.ledger.Get(id); ok && e.Policy == capability.AutoUseRequire {
				return tool.ResolvedCall{}, fmt.Errorf("cannot decline a require capability %q", id)
			}
		}
		base.SkipExecute = true
		base.Result = fmt.Sprintf("declined capability %s: %s", id, reason)
		base.ReadOnly = true
		base.Commit = func() error {
			if t.ledger != nil {
				if err := t.ledger.MarkDeclined(id, reason); err != nil {
					return err
				}
			}
			if t.audit != nil {
				t.audit.RecordDecline()
			}
			return nil
		}
		return base, nil
	case ActionCall:
		if id == "" {
			return tool.ResolvedCall{}, fmt.Errorf("capability_id is required for action=call%s", misplacedArgumentHint(p.Arguments, "capability_id"))
		}
		return t.resolveCall(ctx, id, p.Arguments, base)
	default:
		return tool.ResolvedCall{}, fmt.Errorf("unknown action %q; use search, list, inspect, call, or decline", p.Action)
	}
}

// TargetArgs is what a call action forwards to its target, read without
// resolving it; every other action forwards nothing.
func (*UseCapabilityTool) TargetArgs(args json.RawMessage) json.RawMessage {
	var p struct {
		Action    string          `json:"action"`
		Arguments json.RawMessage `json:"arguments"`
	}
	if json.Unmarshal(args, &p) != nil || strings.ToLower(strings.TrimSpace(p.Action)) != "call" {
		return nil
	}
	return p.Arguments
}

func (t *UseCapabilityTool) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	resolved, err := t.ResolveCall(ctx, args)
	if err != nil {
		return "", err
	}
	if resolved.SkipExecute {
		if resolved.Commit != nil {
			if err := resolved.Commit(); err != nil {
				return "", err
			}
		}
		if resolved.ProxyAction == "call" && !resolved.Unavailable {
			if t.ledger != nil {
				t.ledger.MarkSucceeded(resolved.CapabilityID)
			}
			if t.audit != nil {
				t.audit.RecordMCPProxy(false, true, false)
			}
		}
		return resolved.Result, nil
	}
	if resolved.Unavailable {
		if t.ledger != nil {
			t.ledger.MarkUnavailable(resolved.CapabilityID, resolved.UnavailableReason)
		}
		return "", fmt.Errorf("capability unavailable: %s", resolved.UnavailableReason)
	}
	if resolved.Target == nil {
		return "", fmt.Errorf("no target tool resolved for %s", resolved.CapabilityID)
	}
	if t.ledger != nil {
		t.ledger.MarkInvoked(resolved.CapabilityID)
	}
	if t.audit != nil {
		t.audit.RecordMCPProxy(false, true, false)
	}
	out, err := resolved.Target.Execute(ctx, resolved.Args)
	if err != nil {
		return out, t.recordCallFailure(resolved, err)
	}
	if t.ledger != nil {
		t.ledger.MarkSucceeded(resolved.CapabilityID)
	}
	return out, nil
}

func (t *UseCapabilityTool) inspect(ctx context.Context, id string) (string, error) {
	cat := t.currentCatalog()
	if e, ok := cat.Lookup(id); ok {
		info := map[string]any{
			"id":          e.ID,
			"kind":        e.Kind,
			"name":        e.Name,
			"description": e.Description,
			"status":      e.Status,
			"read_only":   e.ReadOnly,
			"auto_use":    e.AutoUse,
			"requires":    e.Requires,
			"profiles":    e.Profiles,
			"tool_name":   e.ToolName,
			"auto_start":  e.AutoStart,
		}
		if len(e.Aliases) > 0 {
			info["aliases"] = e.Aliases
		}
		// A registry tool answers with its own schema. Without it the model is
		// left guessing argument names for a tool the provider schema hides.
		if e.Kind == capability.KindTool && t.registry != nil {
			if tl, ok := t.registry.Get(e.ToolName); ok {
				info["input_schema"] = json.RawMessage(tl.Schema())
			}
		}
		b, _ := json.MarshalIndent(info, "", "  ")
		// For MCP entries, list tools without side effects: live tools when the
		// server is already connected, cached schema otherwise. Inspect runs
		// during call resolution — before permission and hook gates — so it must
		// never start a subprocess or open a network connection.
		if e.Kind == capability.KindMCPServer || e.Kind == capability.KindMCPTool {
			server := e.Source
			if server == "" {
				server = e.ConnectName
			}
			toolFilter := ""
			if e.Kind == capability.KindMCPTool {
				parsedServer, raw, err := parseMCPCapabilityID(e.ID)
				if err != nil {
					return string(b), nil
				}
				if server == "" {
					server = parsedServer
				} else if parsedServer != server {
					return string(b), nil
				}
				toolFilter = raw
			}
			if server != "" {
				if !t.serverEnabled(server) {
					return string(b) + "\n\nServer is disabled in this session.", nil
				}
				if t.host != nil && t.host.HasClient(server) {
					// serverTools refreshes the snapshot too: inspecting a
					// server another tab connected restores tool routing here.
					tools, err := t.serverTools(ctx, server)
					if err != nil {
						return string(b) + "\n\nTool listing failed: " + err.Error(), nil
					}
					return string(b) + "\n\nTools:\n" + inspectToolListJSON(server, filterInspectTools(tools, toolFilter)), nil
				}
				if spec, ok := t.specFor(server); ok {
					if cs, ok := plugin.LoadCachedSchemaForSpec(spec); ok && len(cs.Tools) > 0 {
						var list []inspectToolInfo
						for _, ct := range cs.Tools {
							if toolFilter != "" && ct.Name != toolFilter {
								continue
							}
							list = append(list, inspectToolInfo{
								ID:          "mcp-tool:" + server + "/" + ct.Name,
								Name:        plugin.ModelToolName(server, ct.Name),
								Description: ct.Description,
								ReadOnly:    ct.ReadOnly,
								Schema:      ct.Schema,
							})
						}
						extra, _ := json.MarshalIndent(list, "", "  ")
						return string(b) + "\n\nTools (from cached schema; server not started):\n" + string(extra), nil
					}
					return string(b) + "\n\nServer not connected and no cached tool schema; call use_capability(action=\"call\", capability_id=\"mcp-server:" + server + "\") to connect (after approval) and list its tools.", nil
				}
			}
		}
		return string(b), nil
	}
	return "", fmt.Errorf("unknown capability_id %q", id)
}

// filterInspectTools narrows concrete mcp-tool inspection to that exact tool.
// Server inspection intentionally keeps the full directory. This prevents a
// restricted sub-agent allowed one tool from discovering sibling tool schemas
// through action=inspect on its allowed capability ID.
func filterInspectTools(tools []tool.Tool, raw string) []tool.Tool {
	if raw == "" {
		return tools
	}
	filtered := make([]tool.Tool, 0, 1)
	for _, tl := range tools {
		if m, ok := tl.(tool.MCPMetadata); ok && m.MCPRawToolName() == raw {
			filtered = append(filtered, tl)
			break
		}
	}
	return filtered
}

type inspectToolInfo struct {
	ID          string          `json:"id"`
	Name        string          `json:"name"`
	Description string          `json:"description"`
	ReadOnly    bool            `json:"read_only"`
	Schema      json.RawMessage `json:"input_schema,omitempty"`
}

// inspectToolListJSON renders a server's live tools as the capability-id
// directory shared by inspect and the first-discovery connect result.
func inspectToolListJSON(server string, tools []tool.Tool) string {
	var list []inspectToolInfo
	for _, tl := range tools {
		raw := ""
		if m, ok := tl.(tool.MCPMetadata); ok {
			raw = m.MCPRawToolName()
		}
		list = append(list, inspectToolInfo{
			ID:          "mcp-tool:" + server + "/" + raw,
			Name:        tl.Name(),
			Description: tl.Description(),
			ReadOnly:    tl.ReadOnly(),
			Schema:      tl.Schema(),
		})
	}
	extra, _ := json.MarshalIndent(list, "", "  ")
	return string(extra)
}

func (t *UseCapabilityTool) resolveCall(ctx context.Context, id string, args json.RawMessage, base tool.ResolvedCall) (tool.ResolvedCall, error) {
	// Registry-backed tools and skills share the unified proxy. Real writers
	// still pass permission/plan/sandbox/lease checks via ResolvedCall.Target.
	if name, ok := strings.CutPrefix(id, "tool:"); ok {
		return t.resolveRegistryTool(strings.TrimSpace(name), id, args, base)
	}
	if name, ok := strings.CutPrefix(id, "skill:"); ok {
		return t.resolveSkillCall(strings.TrimSpace(name), id, args, base)
	}
	// An alias resolves through the catalog that listed it: the id the model
	// read from list/inspect is the id this call answers to.
	if e, ok := t.currentCatalog().Lookup(id); ok && e.Kind == capability.KindTool && e.ToolName != "" {
		return t.resolveRegistryTool(e.ToolName, id, args, base)
	}
	// Server-level call is the first-discovery path for servers with no
	// schema cache: it resolves to a gated connect-and-list target so the
	// model can learn tool names without inspect ever starting a process.
	if server, ok := ParseMCPServerCapabilityID(id); ok {
		return t.resolveServerConnect(ctx, server, base)
	}
	server, raw, err := parseMCPCapabilityID(id)
	if err != nil {
		return tool.ResolvedCall{}, err
	}
	if !t.serverEnabled(server) {
		return t.resolveUnavailable(base, id, plugin.ModelToolName(server, raw), fmt.Sprintf("MCP server %q is disabled in this session", server)), nil
	}
	var runtimeSpec plugin.Spec
	if t.runtime != nil {
		spec, unlock, lockErr := t.lockAuthorizedRuntimeServer(ctx, server)
		if lockErr != nil {
			return t.resolveUnavailable(base, id, plugin.ModelToolName(server, raw), lockErr.Error()), nil
		}
		runtimeSpec = spec
		unlock()
	}
	// Prefer already-exposed registry tool (auto-started MCP). The model name
	// MUST come from the plugin layer's canonical constructor: it appends a
	// collision hash for sanitised raw names, and permission/hook rules are
	// written against that executed name — a proxy-local normalization would
	// let them silently miss.
	modelName := plugin.ModelToolName(server, raw)
	if t.registry != nil {
		if tl, ok := routableRegistryTool(t.registry, modelName); ok {
			if t.runtime != nil && !mcpToolMatchesRuntime(tl, runtimeSpec) {
				return t.resolveUnavailable(base, id, modelName, fmt.Sprintf("connected MCP server %q identity does not match the current runtime configuration", server)), nil
			}
			base.TargetName = modelName
			base.Target = t.bindRuntimeMCP(runtimeSpec, tl)
			base.ReadOnly = tl.ReadOnly()
			if len(args) == 0 {
				base.Args = json.RawMessage(`{}`)
			} else {
				base.Args = args
			}
			return base, nil
		}
	}
	// Server already connected (auto-started, a previous proxy call, or a
	// sibling tab sharing this host): resolving against live tools is
	// side-effect-free. serverTools also refreshes the catalog snapshot so a
	// cross-tab connect still yields routable mcp-tool entries here.
	if t.host != nil && t.host.HasClient(server) {
		var tools []tool.Tool
		if t.runtime != nil {
			tools, err = t.serverToolsForSpec(ctx, server, runtimeSpec)
		} else {
			tools, err = t.serverTools(ctx, server)
		}
		if err != nil {
			return t.resolveUnavailable(base, id, modelName, err.Error()), nil
		}
		target := findMCPTool(tools, raw, modelName)
		if target == nil {
			return t.resolveUnavailable(base, id, modelName, fmt.Sprintf("MCP tool %q not found on server %q", raw, server)), nil
		}
		base.Target = t.bindRuntimeMCP(runtimeSpec, target)
		base.TargetName = target.Name()
		base.ReadOnly = target.ReadOnly()
		if len(args) == 0 {
			base.Args = json.RawMessage(`{}`)
		} else {
			base.Args = args
		}
		return base, nil
	}
	// Unconnected server: resolution must stay pure — no subprocess, no network.
	// Return a deferred target that connects in Execute, after the permission
	// gate and PreToolUse hooks have approved the real target name/arguments.
	spec := runtimeSpec
	if t.runtime == nil {
		var ok bool
		spec, ok = t.specFor(server)
		if !ok {
			return t.resolveUnavailable(base, id, modelName, fmt.Sprintf("MCP server %q is not configured", server)), nil
		}
		spec = plugin.ResolveStoredAuthorization(ctx, spec)
	}
	destructive := false
	if t.catalog != nil {
		if entry, found := t.catalog().Lookup(id); found {
			destructive = entry.Destructive
		}
	}
	readOnly := false
	if cached, found := plugin.CachedToolSafetyForSpec(spec, raw); found {
		destructive = destructive || cached.Destructive
		readOnly = cached.ReadOnly
	}
	lazy := &onDemandMCPTool{proxy: t, spec: spec, server: server, raw: raw, modelName: modelName, destructive: destructive}
	lazy.readOnly = readOnly
	base.Target = lazy
	base.TargetName = modelName
	// Cached server hints control ordinary approval. Strict read-only execution
	// additionally requires server authorization and live read-only metadata.
	base.ReadOnly = lazy.ReadOnly()
	if len(args) == 0 {
		base.Args = json.RawMessage(`{}`)
	} else {
		base.Args = args
	}
	return base, nil
}

// resolveRegistryTool binds a registry tool by name for use_capability call.
// Provider-visible core tools remain callable this way, but the catalog prefers
// listing only non-visible tools so the model uses the top-level surface first.
func (t *UseCapabilityTool) resolveRegistryTool(name, id string, args json.RawMessage, base tool.ResolvedCall) (tool.ResolvedCall, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return tool.ResolvedCall{}, fmt.Errorf("capability id %q is missing a tool name", id)
	}
	if name == "use_capability" {
		return tool.ResolvedCall{}, fmt.Errorf("cannot proxy use_capability through itself")
	}
	if resolved, ok := t.resolveDisabled(base, id, name); ok {
		return resolved, nil
	}
	if t.registry == nil {
		return t.resolveUnavailable(base, id, name, "tool registry is unavailable"), nil
	}
	tl, ok := t.registry.Get(name)
	if !ok {
		return t.resolveUnavailable(base, id, name, fmt.Sprintf("tool %q is not registered in this session", name)), nil
	}
	if t.bindLocal != nil {
		tl = t.bindLocal(tl)
	}
	base.Target = tl
	base.TargetName = name
	base.ReadOnly = tl.ReadOnly()
	if len(args) == 0 {
		base.Args = json.RawMessage(`{}`)
	} else {
		base.Args = args
	}
	return base, nil
}

// resolveSkillCall routes skill:<name> through run_skill / read_only_skill /
// read_skill when present, preserving the real skill tool name for evidence.
func (t *UseCapabilityTool) resolveSkillCall(skillName, id string, args json.RawMessage, base tool.ResolvedCall) (tool.ResolvedCall, error) {
	skillName = strings.TrimSpace(skillName)
	if skillName == "" {
		return tool.ResolvedCall{}, fmt.Errorf("capability id %q is missing a skill name", id)
	}
	if t.registry == nil {
		return t.resolveUnavailable(base, id, skillName, "tool registry is unavailable"), nil
	}
	// Prefer full run_skill; fall back to read-only variants when the session
	// only exposes them (planner / plan mode).
	for _, toolName := range []string{"run_skill", "read_only_skill", "read_skill"} {
		if tl, ok := t.registry.Get(toolName); ok {
			payload := args
			if len(payload) == 0 || string(payload) == "null" {
				payload = json.RawMessage(fmt.Sprintf(`{"name":%q}`, skillName))
			} else {
				var m map[string]any
				if json.Unmarshal(payload, &m) == nil {
					if _, has := m["name"]; !has {
						m["name"] = skillName
						if b, err := json.Marshal(m); err == nil {
							payload = b
						}
					}
				}
			}
			base.Target = tl
			base.TargetName = toolName
			base.ReadOnly = tl.ReadOnly()
			base.Args = payload
			return base, nil
		}
	}
	return t.resolveUnavailable(base, id, skillName, fmt.Sprintf("skill tools are not available for %q", skillName)), nil
}

// findMCPTool matches a server's tool list by raw MCP name or by the
// canonical namespaced model-visible name (plugin.ModelToolName).
func findMCPTool(tools []tool.Tool, raw, modelName string) tool.Tool {
	for _, tl := range tools {
		if m, ok := tl.(tool.MCPMetadata); ok && m.MCPRawToolName() == raw {
			return tl
		}
		if tl.Name() == modelName {
			return tl
		}
	}
	return nil
}

// onDemandMCPTool defers MCP server startup to Execute so permission and hook
// gates always run before any subprocess or network side effect. Before the live
// handshake it remains write-capable until the resolved MCP tool is classified.
type onDemandMCPTool struct {
	proxy     *UseCapabilityTool
	spec      plugin.Spec
	server    string
	raw       string
	modelName string
	// destructive comes from the schema cache when available. A live promotion
	// is detected in Execute so a retry re-enters the current Plan/read-only
	// execution boundary.
	destructive bool
	readOnly    bool
}

func (o *onDemandMCPTool) Name() string { return o.modelName }

func (o *onDemandMCPTool) Description() string {
	return "on-demand MCP tool " + o.server + "/" + o.raw + " (connects when first used)"
}

func (o *onDemandMCPTool) Schema() json.RawMessage { return json.RawMessage(`{"type":"object"}`) }

func (o *onDemandMCPTool) ReadOnly() bool {
	return o.readOnly
}

func (o *onDemandMCPTool) ReadOnlyExecutionHostMutation() bool { return true }

func (o *onDemandMCPTool) MCPServerAuthorized() bool {
	// Spec.Authorized is the single runtime authorization result. Boot/install
	// and ResolveStoredAuthorization set it; this path never invents trust.
	return o.spec.ServerAuthorized()
}

func (o *onDemandMCPTool) ReadOnlyExecutionBlockReason() string {
	return "connect this MCP capability from a parent session first"
}

// MCPServerName/MCPRawToolName expose the deferred target for audit and
// diagnostics (tool.MCPMetadata).
func (o *onDemandMCPTool) MCPServerName() string  { return o.server }
func (o *onDemandMCPTool) MCPRawToolName() string { return o.raw }
func (o *onDemandMCPTool) MCPDestructiveHint() bool {
	return o.destructive
}

func (o *onDemandMCPTool) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	text, _, err := o.executeWithImages(ctx, args)
	return text, err
}

// ExecuteWithImages preserves structured MCP image results on the first call,
// when the deferred target must connect the server before dispatch. Keeping the
// resolution and safety checks in executeWithImages ensures text-only and image
// callers share the same authorization and runtime-identity boundary.
func (o *onDemandMCPTool) ExecuteWithImages(ctx context.Context, args json.RawMessage) (string, []string, error) {
	return o.executeWithImages(ctx, args)
}

func (o *onDemandMCPTool) executeWithImages(ctx context.Context, args json.RawMessage) (string, []string, error) {
	// Final runtime-bound authorization and identity check before any
	// process/network start. The read lock also linearizes this dispatch against
	// disable, uninstall, and same-name hot replacement.
	spec, unlock, err := o.proxy.lockAuthorizedRuntimeServer(ctx, o.server)
	if err != nil {
		msg := err.Error()
		if o.proxy.ledger != nil {
			o.proxy.ledger.MarkUnavailable("mcp-tool:"+o.server+"/"+o.raw, msg)
		}
		return "", nil, err
	}
	defer unlock()
	if !plugin.MCPRuntimeSpecMatches(spec, o.spec) {
		return "", nil, fmt.Errorf("MCP server %q runtime identity changed after resolution; retry so Reasonix can bind the current configuration", o.server)
	}
	tools, err := o.proxy.ensureServerToolsForSpec(ctx, o.server, spec)
	if err != nil {
		// Audit for the call path is recorded once by the agent loop
		// (noteCapabilityInvocation); only the ledger outcome lands here.
		if o.proxy.ledger != nil {
			o.proxy.ledger.MarkUnavailable("mcp-tool:"+o.server+"/"+o.raw, err.Error())
		}
		return "", nil, err
	}
	target := findMCPTool(tools, o.raw, o.modelName)
	if target == nil {
		msg := fmt.Sprintf("MCP tool %q not found on server %q", o.raw, o.server)
		if o.proxy.ledger != nil {
			o.proxy.ledger.MarkUnavailable("mcp-tool:"+o.server+"/"+o.raw, msg)
		}
		return "", nil, fmt.Errorf("%s", msg)
	}
	if !mcpToolMatchesRuntime(target, spec) {
		return "", nil, fmt.Errorf("connected MCP server %q identity does not match the current runtime configuration; reconnect this server before retrying", o.server)
	}
	if _, err := plugin.ReconcileCachedToolSafety(o.server, o.raw, plugin.CachedToolSafety{
		ReadOnly:    o.readOnly,
		Destructive: o.destructive,
	}, target); err != nil {
		return "", nil, err
	}
	// Planner non-destructive lane and reader lane: re-check live metadata
	// before tools/call even when Reconcile did not see a cache promotion.
	if tool.HasNonDestructiveMCPExecutionIntent(ctx) {
		if !tool.IsMCPServerAuthorized(target) || tool.HasMCPDestructiveHint(target) {
			return "", nil, fmt.Errorf("MCP server %q changed the authorization or destructive classification for tool %q; the call was blocked before dispatch — retry so Reasonix can re-apply the current Planner MCP safety boundary", o.server, o.raw)
		}
	}
	if imageTool, ok := target.(tool.ImageTool); ok {
		return imageTool.ExecuteWithImages(ctx, args)
	}
	text, err := target.Execute(ctx, args)
	return text, nil, err
}

func (t *UseCapabilityTool) ensureServerToolsForSpec(ctx context.Context, server string, spec plugin.Spec) ([]tool.Tool, error) {
	// Reuse shared host if already connected (including auto-started).
	if t.host.HasClient(server) {
		return t.serverToolsForSpec(ctx, server, spec)
	}
	// On-demand connect: the child and handshake belong to the session. This
	// tool call waits briefly, but a slow healthy server continues in the
	// background instead of being killed and restarted on every retry. Tools
	// stay off the main provider-visible registry.
	life := t.lifeCtx
	if life == nil {
		life = context.Background()
	}
	result := t.host.EnsureConnectedInBackground(life, spec)
	waitBudget := plugin.DefaultStartupWaitBudget()
	timer := time.NewTimer(waitBudget)
	defer timer.Stop()
	var tools []tool.Tool
	var err error
	select {
	case connected := <-result:
		tools, err = connected.Tools, connected.Err
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-timer.C:
		return nil, fmt.Errorf("MCP server %q is still initializing after %s; startup continues in background (limit %s) — retry on a later turn",
			server, waitBudget, spec.ResolvedStartupTimeout())
	}
	if err != nil {
		if plugin.IsServerAlreadyConnected(err) {
			return t.serverToolsForSpec(ctx, server, spec)
		}
		t.host.RecordFailure(spec, err)
		return nil, fmt.Errorf("connect %q: %w", server, err)
	}
	t.ensureState().markConnected(server)
	// Intentionally do NOT add tools to t.registry — provider schema stays stable.
	_ = tools
	return t.serverToolsForSpec(ctx, server, spec)
}

// serverTools fetches the live tools for a connected server and refreshes the
// shared catalog snapshot so mcp-tool entries stay routable once the server
// is StatusReady (its tools are absent from the provider-visible registry).
func (t *UseCapabilityTool) serverTools(ctx context.Context, server string) ([]tool.Tool, error) {
	spec, unlock, err := t.lockAuthorizedRuntimeServer(ctx, server)
	if err != nil {
		return nil, err
	}
	defer unlock()
	return t.serverToolsForSpec(ctx, server, spec)
}

func (t *UseCapabilityTool) serverToolsForSpec(ctx context.Context, server string, spec plugin.Spec) ([]tool.Tool, error) {
	tools, err := t.host.ToolsForSpec(ctx, spec)
	if err != nil {
		return nil, err
	}
	snap := make([]plugin.CachedTool, 0, len(tools))
	for _, tl := range tools {
		m, ok := tl.(tool.MCPMetadata)
		if !ok || m.MCPRawToolName() == "" {
			continue
		}
		snap = append(snap, plugin.CachedTool{
			Name:        m.MCPRawToolName(),
			Description: tl.Description(),
			Schema:      tl.Schema(),
			ReadOnly:    tl.ReadOnly(),
			Destructive: tool.HasMCPDestructiveHint(tl),
		})
	}
	t.ensureState().setLiveTools(server, snap)
	return tools, nil
}

// ConnectedProxyTools returns raw tool metadata for servers connected through
// any frontend sharing this proxy state, keyed by server name. Catalog builders
// consume it so concrete mcp-tool capabilities survive an on-demand connect
// without ever touching the provider-visible registry.
func (t *UseCapabilityTool) ConnectedProxyTools() map[string][]plugin.CachedTool {
	if t == nil {
		return nil
	}
	return t.ensureState().snapshotLiveTools()
}

func (t *UseCapabilityTool) ensureState() *mcpProxySharedState {
	if t.state == nil {
		t.state = &mcpProxySharedState{connected: map[string]bool{}}
	}
	return t.state
}

// specFor looks up the boot-converted spec for server. The proxy deliberately
// holds []plugin.Spec, not raw config entries: env expansion, workspace
// overrides, call timeouts, and read-only tool names all live in the
// shared conversion and must not be re-derived here.
func (t *UseCapabilityTool) specFor(server string) (plugin.Spec, bool) {
	if t.runtime != nil {
		return t.runtime.enabledSpec(server)
	}
	for _, s := range t.specs {
		if s.Name == server {
			return s, true
		}
	}
	return plugin.Spec{}, false
}

// lockAuthorizedRuntimeServer acquires the shared runtime dispatch read lock
// and returns the current enabled, authorized spec. The caller must keep the
// returned lock until the identity-bound Host operation or tools/call has
// crossed its dispatch boundary; lifecycle mutations take the write lock.
func (t *UseCapabilityTool) lockAuthorizedRuntimeServer(ctx context.Context, server string) (plugin.Spec, func(), error) {
	if t.runtime == nil {
		spec, ok := t.specFor(server)
		if !ok {
			return plugin.Spec{}, func() {}, fmt.Errorf("MCP server %q is not configured", server)
		}
		spec = plugin.ResolveStoredAuthorization(ctx, spec)
		if !spec.ServerAuthorized() {
			return plugin.Spec{}, func() {}, fmt.Errorf("MCP server %q is not authorized; install it or complete project identity approval before connecting", server)
		}
		return spec, func() {}, nil
	}

	if err := t.runtime.refreshInventory(); err != nil {
		return plugin.Spec{}, func() {}, err
	}
	t.runtime.dispatchMu.RLock()
	unlock := t.runtime.dispatchMu.RUnlock
	t.runtime.mu.RLock()
	configured, ok := t.runtime.servers[strings.TrimSpace(server)]
	t.runtime.mu.RUnlock()
	if !ok {
		unlock()
		return plugin.Spec{}, func() {}, fmt.Errorf("MCP server %q is not configured", server)
	}
	if !configured.enabled {
		unlock()
		return plugin.Spec{}, func() {}, fmt.Errorf("MCP server %q is disabled in this session", server)
	}
	spec := plugin.ResolveStoredAuthorization(ctx, cloneMCPSpec(configured.spec))
	if !spec.ServerAuthorized() {
		unlock()
		return plugin.Spec{}, func() {}, fmt.Errorf("MCP server %q is not authorized; install it or complete project identity approval before connecting", server)
	}
	return spec, unlock, nil
}

func (t *UseCapabilityTool) bindRuntimeMCP(spec plugin.Spec, target tool.Tool) tool.Tool {
	if t.runtime == nil || target == nil {
		return target
	}
	if bound, ok := target.(*runtimeBoundMCPTool); ok && bound.proxy.runtime == t.runtime {
		return target
	}
	return &runtimeBoundMCPTool{
		proxy:      t,
		target:     target,
		server:     spec.Name,
		authorized: spec.ServerAuthorized(),
	}
}

func (t *UseCapabilityTool) withRuntimeBoundMCP(ctx context.Context, server string, target tool.Tool, execute func() error) error {
	if t.runtime == nil {
		return execute()
	}
	spec, unlock, err := t.lockAuthorizedRuntimeServer(ctx, server)
	if err != nil {
		return err
	}
	defer unlock()
	if !spec.ToolEnabled(mcpRawToolName(target)) {
		return fmt.Errorf("MCP tool %s is disabled", target.Name())
	}
	if !plugin.MCPToolMatchesSpec(target, spec) {
		return fmt.Errorf("connected MCP server %q identity does not match the current runtime configuration; reconnect this server before retrying", server)
	}
	return t.runtime.withServerGate(ctx, server, execute)
}

func (t *UseCapabilityTool) serverEnabled(server string) bool {
	if t.runtime != nil {
		return t.runtime.serverEnabled(server)
	}
	// Standalone proxies predate the authoritative runtime and may resolve
	// already-registered MCP tools without carrying a duplicate spec slice.
	return true
}

func (t *UseCapabilityTool) configuredServers() []mcpRuntimeServer {
	if t.runtime != nil {
		return t.runtime.configuredServers()
	}
	servers := make([]mcpRuntimeServer, 0, len(t.specs))
	seen := map[string]bool{}
	for _, raw := range t.specs {
		spec := cloneMCPSpec(raw)
		name := strings.TrimSpace(spec.Name)
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		servers = append(servers, mcpRuntimeServer{
			entry:   config.PluginEntry{Name: name},
			spec:    spec,
			enabled: true,
		})
	}
	sort.Slice(servers, func(i, j int) bool { return servers[i].spec.Name < servers[j].spec.Name })
	return servers
}

func (t *UseCapabilityTool) currentCatalog() capability.Catalog {
	if t.catalog != nil {
		return t.catalog()
	}
	return capability.Catalog{}
}

// ParseMCPServerCapabilityID extracts the server name from an mcp-server id.
func ParseMCPServerCapabilityID(id string) (string, bool) {
	if !strings.HasPrefix(id, "mcp-server:") {
		return "", false
	}
	name := strings.TrimSpace(strings.TrimPrefix(id, "mcp-server:"))
	return name, name != ""
}

// resolveServerConnect resolves action=call on an mcp-server id. A connected
// server lists its tools immediately (side-effect free); an unconnected one
// resolves to a deferred connect target that runs only after the permission
// gate and PreToolUse hooks approve it. Stored project authorization is applied
// at resolve time so unauthorized project MCP never reaches process startup.
func (t *UseCapabilityTool) resolveServerConnect(ctx context.Context, server string, base tool.ResolvedCall) (tool.ResolvedCall, error) {
	id := "mcp-server:" + server
	if !t.serverEnabled(server) {
		return t.resolveUnavailable(base, id, plugin.ToolPrefix(server), fmt.Sprintf("MCP server %q is disabled in this session", server)), nil
	}
	if t.host != nil && t.host.HasClient(server) {
		out, err := t.listServerTools(ctx, server)
		if err != nil {
			return t.resolveUnavailable(base, id, plugin.ToolPrefix(server), err.Error()), nil
		}
		base.SkipExecute = true
		base.HostCompleted = true
		base.Result = out
		base.ReadOnly = true
		return base, nil
	}
	spec, unlock, err := t.lockAuthorizedRuntimeServer(ctx, server)
	if err != nil {
		return t.resolveUnavailable(base, id, plugin.ToolPrefix(server), err.Error()), nil
	}
	unlock()
	connect := &onDemandMCPConnect{proxy: t, spec: spec, server: server}
	base.Target = connect
	// A dedicated exact identity names the connect for permission and hook
	// rules. It cannot collide with a real mcp__ tool, and rules do not need to
	// rely on unsupported tool-name glob matching.
	base.TargetName = connect.Name()
	// Connecting spawns a subprocess, so it is never a read-only fast path for
	// ordinary Plan/strict agents. PlannerMCPExecution may allow authorized
	// connects; unauthorized specs are blocked before process/network start.
	base.ReadOnly = false
	base.Args = json.RawMessage(`{}`)
	return base, nil
}

// listServerTools renders the live tool directory of a connected server and
// refreshes the proxy snapshot on the way (via serverTools).
func (t *UseCapabilityTool) listServerTools(ctx context.Context, server string) (string, error) {
	tools, err := t.serverTools(ctx, server)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("connected MCP server %q; %d tools:\n%s", server, len(tools), inspectToolListJSON(server, tools)), nil
}

func (t *UseCapabilityTool) listServerToolsForSpec(ctx context.Context, server string, spec plugin.Spec) (string, error) {
	tools, err := t.serverToolsForSpec(ctx, server, spec)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("connected MCP server %q; %d tools:\n%s", server, len(tools), inspectToolListJSON(server, tools)), nil
}

func parseMCPCapabilityID(id string) (server, raw string, err error) {
	id = strings.TrimSpace(id)
	switch {
	case strings.HasPrefix(id, "mcp-tool:"):
		rest := strings.TrimPrefix(id, "mcp-tool:")
		server, raw, ok := strings.Cut(rest, "/")
		if !ok || server == "" || raw == "" {
			return "", "", fmt.Errorf("invalid mcp-tool id %q; want mcp-tool:<server>/<tool>", id)
		}
		return server, raw, nil
	case strings.HasPrefix(id, "mcp-server:"):
		return "", "", fmt.Errorf("%q is a server id; call it directly to connect and list tools, or use mcp-tool:<server>/<tool>", id)
	default:
		return "", "", fmt.Errorf("action=call requires an mcp-tool capability id, got %q", id)
	}
}

// Ensure UseCapabilityTool satisfies the tool contracts used by the agent.
var (
	_ tool.Tool             = (*UseCapabilityTool)(nil)
	_ tool.CallResolver     = (*UseCapabilityTool)(nil)
	_ tool.TargetArgsReader = (*UseCapabilityTool)(nil)
)

// EmitProxyAudit is a helper for frontends: returns a notice describing the
// proxy name and real target for user audit trails.
func EmitProxyAudit(sink event.Sink, resolved tool.ResolvedCall) {
	if sink == nil || resolved.TargetName == "" {
		return
	}
	sink.Emit(event.Event{
		Kind:     event.Notice,
		Level:    event.LevelInfo,
		Audience: event.NoticeAudienceOperator,
		Text:     fmt.Sprintf("capability proxy: %s → %s", resolved.DisplayName, resolved.TargetName),
		Detail:   resolved.CapabilityID,
	})
}

```

## internal/tools/builtin/compress.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/tools/builtin/compress.go

SHA-256: `dea6c51b4d9d938d288a913cf65601746e413feedb90cc9adc0013d568ec599b`

```
package builtin

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"reasonix/internal/contract/tool"
)

const (
	maxCompressAnchorBytes = 512
	maxCompressFocusBytes  = 2000
)

func init() { tool.RegisterBuiltin(compressContext{}) }

type compressContext struct{}

func (compressContext) Name() string { return "compress" }

func (compressContext) Description() string {
	return "Compress a selected part of the current model-visible conversation without deleting visible history. Use only when the user explicitly asks for context compression. Choose `before` to summarize everything before the uniquely matched user turn while keeping that turn and later context, or `after` to summarize from that turn through the last completed turn while keeping the active turn. The anchor must be an exact, unique excerpt from a real user message; use a longer excerpt if the tool reports multiple matches."
}

func (compressContext) Schema() json.RawMessage {
	return json.RawMessage(`{
"type":"object",
"additionalProperties":false,
"properties":{
  "direction":{"type":"string","enum":["before","after"],"description":"Which side of the anchor user turn to compress."},
  "anchor":{"type":"string","minLength":1,"maxLength":512,"description":"An exact, unique excerpt from one real user message in the current model-visible conversation."},
  "focus":{"type":"string","maxLength":2000,"description":"Optional guidance about facts or decisions the summary must preserve."}
},
"required":["direction","anchor"]
}`)
}

// ReadOnly is true in the permission/workspace sense: compress changes only
// the owning Agent's context projection. The Agent batcher separately forces it
// into a serial lane because projection installation is stateful.
func (compressContext) ReadOnly() bool { return true }

// Sequential is true even though ReadOnly is: a fold rewrites the transcript
// every other call in the batch is reading from.
func (compressContext) Sequential(context.Context, json.RawMessage) bool { return true }

func (compressContext) PlanModeSafe() bool { return true }

func (compressContext) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	var request struct {
		Direction string `json:"direction"`
		Anchor    string `json:"anchor"`
		Focus     string `json:"focus"`
	}
	if err := json.Unmarshal(args, &request); err != nil {
		return "", fmt.Errorf("invalid compress args: %w", err)
	}
	request.Direction = strings.TrimSpace(request.Direction)
	request.Anchor = strings.TrimSpace(request.Anchor)
	request.Focus = strings.TrimSpace(request.Focus)
	if request.Direction != "before" && request.Direction != "after" {
		return "", fmt.Errorf("compress: direction must be before or after")
	}
	if request.Anchor == "" {
		return "", fmt.Errorf("compress: anchor must not be empty")
	}
	if len(request.Anchor) > maxCompressAnchorBytes {
		return "", fmt.Errorf("compress: anchor exceeds %d bytes", maxCompressAnchorBytes)
	}
	if len(request.Focus) > maxCompressFocusBytes {
		return "", fmt.Errorf("compress: focus exceeds %d bytes", maxCompressFocusBytes)
	}
	compressor, ok := tool.ContextCompressorFromContext(ctx)
	if !ok {
		return "", fmt.Errorf("compress is unavailable outside an active agent session")
	}
	result, err := compressor.CompressContext(ctx, tool.CompressRequest{
		Direction: request.Direction,
		Anchor:    request.Anchor,
		Focus:     request.Focus,
	})
	if err != nil {
		return "", err
	}
	out, err := json.Marshal(result)
	if err != nil {
		return "", fmt.Errorf("encode compress result: %w", err)
	}
	return string(out), nil
}

```

## internal/tools/builtin/workspace.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/tools/builtin/workspace.go

SHA-256: `57d44aa9d21b686872caf1dfa22438f56712a708ae99a60a43ba8c1039ce6659`

```
package builtin

import (
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"reasonix/internal/base/netclient"
	"reasonix/internal/contract/tool"
	"reasonix/internal/safety/sandbox"
	"reasonix/internal/state/sessiontemp"
)

// Workspace builds a built-in tool set bound to a working directory, so several
// agents can run concurrently with independent path roots — a desktop front-end
// opening one tab per project, say. The process working directory is global and
// cannot be made per-agent (os.Chdir is process-wide), so each tool instead
// resolves relative paths against this directory and bash runs in it.
//
// Dir is that directory (empty yields process-cwd tools, byte-identical to the
// compile-time built-ins). WriteRoots confines the file-writers (as
// ConfineWriters); when empty and Dir is set, Dir itself becomes the sole write
// root, so writes stay inside the project by default. ForbidReadRoots confines
// the read/list/search built-ins so they cannot peek at the listed directories.
// Bash is the OS-sandbox spec for the bash tool (as ConfineBash). SessionGuard
// rejects writer-tool targets inside Reasonix's own session stores and makes
// bash warn when a command references them (see SessionDataGuard).
type Workspace struct {
	Dir             string
	WriteRoots      []string
	ForbidReadRoots []string
	// ReadRoots, when set, confines every read tool to these directories after
	// symlinks are resolved; empty leaves reads unconfined.
	ReadRoots    []string
	Bash         sandbox.Spec
	BashTimeout  time.Duration
	Search       SearchSpec
	ProxySpec    netclient.ProxySpec
	SystemOne    SystemOneSpec
	ReadPaths    *PathResolver
	SessionGuard SessionDataGuard
	// ManagedConfig names the Reasonix-owned config files the file-writers may
	// touch outside WriteRoots after a fresh per-write human approval (see
	// ManagedConfigPaths). The zero value disables the escape hatch.
	ManagedConfig ManagedConfigPaths
	// FileOverlay, when non-nil, serves every file tool's content through the
	// host transport (unsaved editor buffers) with disk fallback; Terminal, when
	// non-nil, runs foreground bash in a host-owned terminal when the local OS
	// sandbox is not enforcing. Both are nil outside host transports like ACP.
	FileOverlay FileOverlay
	Terminal    TerminalRunner
	// SessionTemp is the logical-session private temporary directory manager
	// shared by bash and ripgrep-backed grep. Nil leaves those tools without a
	// session-private temp (platform defaults apply).
	SessionTemp *sessiontemp.Manager
	// FileWriteReceipt receives prior-state evidence after a successful
	// write_file mutation. It is instance-scoped so concurrent runtimes never
	// record into another session's recovery ledger.
	FileWriteReceipt func(path string, hadPrior bool, prior []byte)
}

// Tools returns the built-in tools bound to the workspace, ready to Add to a
// per-run tool.Registry. An empty enabled list yields every built-in; otherwise
// only the named ones are returned (unknown names are ignored). This is the
// per-workspace analogue of the cli's process-cwd assembly — a desktop driver
// calls it once per agent instead of relying on the global working directory.
func (w Workspace) Tools(enabled ...string) []tool.Tool {
	writeRoots := w.WriteRoots
	if len(writeRoots) == 0 && w.Dir != "" {
		writeRoots = []string{w.Dir}
	}
	roots := append(realRoots(writeRoots), networkSpellings(append([]string{w.Dir}, writeRoots...))...)
	netRoots := networkRoots(w.Dir, w.WriteRoots, w.ReadRoots)
	forbidRoots := realRoots(w.ForbidReadRoots)
	readRoots := realRoots(w.ReadRoots)
	if len(w.ReadRoots) > 0 && len(readRoots) == 0 {
		// A scope that resolves to nothing must refuse everything, not confine nothing.
		readRoots = []string{filepath.Join(string(filepath.Separator), "\x00unresolved")}
	}

	overrides := map[string]tool.Tool{
		"read_file":        readFile{workDir: w.Dir, netRoots: netRoots, readRoots: readRoots, paths: w.ReadPaths, forbidRoots: forbidRoots, overlay: w.FileOverlay},
		"write_file":       writeFile{workDir: w.Dir, roots: roots, readRoots: readRoots, forbidRoots: forbidRoots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp, overlay: w.FileOverlay, receipt: w.FileWriteReceipt},
		"edit_file":        editFile{readPolicy: editReadPolicy{readRoots: readRoots, forbidRoots: forbidRoots}, workDir: w.Dir, roots: roots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp, overlay: w.FileOverlay},
		"multi_edit":       multiEdit{readPolicy: editReadPolicy{readRoots: readRoots, forbidRoots: forbidRoots}, workDir: w.Dir, roots: roots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp, overlay: w.FileOverlay},
		"move_file":        moveFile{workDir: w.Dir, roots: roots, readRoots: readRoots, forbidRoots: forbidRoots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp},
		"notebook_edit":    notebookEdit{readPolicy: editReadPolicy{readRoots: readRoots, forbidRoots: forbidRoots}, workDir: w.Dir, roots: roots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp, overlay: w.FileOverlay},
		"delete_range":     deleteRange{readPolicy: editReadPolicy{readRoots: readRoots, forbidRoots: forbidRoots}, workDir: w.Dir, roots: roots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp, overlay: w.FileOverlay},
		"delete_symbol":    deleteSymbol{readPolicy: editReadPolicy{readRoots: readRoots, forbidRoots: forbidRoots}, workDir: w.Dir, roots: roots, guard: w.SessionGuard, managed: w.ManagedConfig, sessionTemp: w.SessionTemp, overlay: w.FileOverlay},
		"code_index":       codeIndex{workDir: w.Dir, netRoots: netRoots, readRoots: readRoots, forbidRoots: forbidRoots},
		"bash":             bash{workDir: w.Dir, sb: w.Bash, timeout: w.BashTimeout, guard: w.SessionGuard, terminal: w.Terminal, sessionTemp: w.SessionTemp, paths: w.ReadPaths},
		"ls":               listDir{workDir: w.Dir, netRoots: netRoots, readRoots: readRoots, paths: w.ReadPaths, forbidRoots: forbidRoots},
		"glob":             globTool{workDir: w.Dir, netRoots: netRoots, readRoots: readRoots, paths: w.ReadPaths, forbidRoots: forbidRoots},
		"grep":             grepTool{workDir: w.Dir, netRoots: netRoots, readRoots: readRoots, paths: w.ReadPaths, rg: w.Search.RgPath, forbidRoots: forbidRoots, sb: w.Bash, sessionTemp: w.SessionTemp},
		"read_video":       readVideo{workDir: w.Dir, netRoots: netRoots, readRoots: readRoots, paths: w.ReadPaths, forbidRoots: forbidRoots, sb: w.Bash},
		"web_fetch":        webFetch{proxySpec: w.ProxySpec},
		"import_web_image": webImage{workDir: w.Dir, roots: roots, guard: w.SessionGuard, proxySpec: w.ProxySpec, receipt: w.FileWriteReceipt},
	}
	all := tool.Builtins()
	if len(enabled) == 0 {
		for i, t := range all {
			if bound, ok := overrides[t.Name()]; ok {
				all[i] = bound
			}
		}
		if SystemOneConfigured(w.SystemOne) {
			all = append(all, NewSystemOne(w.SystemOne))
		}
		return all
	}
	want := make(map[string]bool, len(enabled))
	for _, n := range enabled {
		want[n] = true
	}
	out := make([]tool.Tool, 0, len(enabled))
	for _, t := range all {
		if want[t.Name()] {
			if bound, ok := overrides[t.Name()]; ok {
				t = bound
			}
			out = append(out, t)
		}
	}
	if want["system_one"] && SystemOneConfigured(w.SystemOne) {
		out = append(out, NewSystemOne(w.SystemOne))
	}
	return out
}

// resolveIn maps a tool's path/pattern argument into a working directory. With
// an empty workDir it returns p unchanged — the process-cwd behavior the
// compile-time built-ins have always had, so existing callers are unaffected.
// Otherwise a relative p is joined onto workDir; an absolute p is returned as-is
// (an explicit absolute path is honored verbatim — the write-confiner, not this,
// enforces the workspace boundary). An empty p resolves to workDir itself, so a
// defaulted "." (ls/grep) targets the workspace root.
func resolveIn(workDir, p string) string {
	if workDir == "" {
		return p
	}
	if p == "" || p == "." {
		return workDir
	}
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(workDir, p)
}

// resolveSessionTemp rewrites a leading session-temp variable into the directory
// it names. The host exports these spellings to bash and names $TMPDIR in its
// own refusal text, so a writer that cannot read them creates a literal
// "$TMPDIR" directory inside the workspace. Only the first segment is expanded.
func resolveSessionTemp(temp *sessiontemp.Manager, p string) string {
	if temp == nil || p == "" {
		return p
	}
	head, rest, _ := strings.Cut(filepath.ToSlash(p), "/")
	named := false
	for _, key := range sandbox.SessionTempEnvKeys {
		if head == "$"+key || head == "${"+key+"}" || strings.EqualFold(head, "%"+key+"%") {
			named = true
			break
		}
	}
	if !named {
		return p
	}
	dir := temp.Dir()
	if dir == "" {
		// The generation is created lazily by whoever first needs it — usually
		// bash. A writer that names the directory needs it to exist too.
		lease, err := temp.Acquire()
		if err != nil {
			return p
		}
		dir = lease.Dir()
		lease.Release()
	}
	if dir == "" {
		return p
	}
	return filepath.Join(dir, filepath.FromSlash(rest))
}

// PathResolver maps session-authorized token paths to local read-only roots.
// It is intentionally used only by read tools; write tools continue to rely on
// WriteRoots confinement and never resolve these aliases.
type PathResolver struct {
	mu    sync.RWMutex
	roots map[string]string
}

// NewPathResolver returns a resolver whose root set can be updated after the
// workspace tools have been registered.
func NewPathResolver() *PathResolver {
	return &PathResolver{roots: map[string]string{}}
}

// RegisterReadRoot authorizes token and all of its local subpaths to resolve
// under root for read-only tools in this session.
func (r *PathResolver) RegisterReadRoot(token, root string) {
	if r == nil {
		return
	}
	token = normalizeReadToken(token)
	root = filepath.Clean(strings.TrimSpace(root))
	if token == "" || root == "" {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.roots == nil {
		r.roots = map[string]string{}
	}
	r.roots[token] = root
}

// Resolve maps a submitted path or pattern to a local path when it begins with
// a registered token. ok is false for ordinary workspace paths.
func (r *PathResolver) Resolve(path string) (ResolvedPath, bool) {
	if r == nil {
		return ResolvedPath{}, false
	}
	key := normalizeReadToken(path)
	if key == "" {
		return ResolvedPath{}, false
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	if root, ok := r.roots[key]; ok {
		return ResolvedPath{Path: root, DisplayPath: key, Root: root, DisplayRoot: key, External: true}, true
	}
	for token, root := range r.roots {
		if !strings.HasPrefix(key, token+"/") {
			continue
		}
		sub, ok := cleanReadSubpath(strings.TrimPrefix(key, token+"/"))
		if !ok {
			return ResolvedPath{}, false
		}
		return ResolvedPath{
			Path:        filepath.Join(root, filepath.FromSlash(sub)),
			DisplayPath: token + "/" + sub,
			Root:        root,
			DisplayRoot: token,
			External:    true,
		}, true
	}
	return ResolvedPath{}, false
}

// TokensIn names the registered tokens a command mentions. The registry is the
// authority: these keys were minted by the host, so finding one is recognition
// rather than a guess about what a word might mean.
func (r *PathResolver) TokensIn(command string) []string {
	if r == nil || command == "" {
		return nil
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	var found []string
	for token := range r.roots {
		if strings.Contains(command, token) {
			found = append(found, token)
		}
	}
	slices.Sort(found)
	return found
}

// ResolvedPath carries both the local path used for I/O and the token path that
// should appear in tool output.
type ResolvedPath struct {
	Path        string
	DisplayPath string
	Root        string
	DisplayRoot string
	External    bool
}

func (p ResolvedPath) DisplayFor(path string) string {
	if !p.External {
		return path
	}
	rel, err := filepath.Rel(p.Root, path)
	if err != nil || !filepath.IsLocal(rel) {
		return path
	}
	if rel == "." {
		return p.DisplayRoot
	}
	return filepath.ToSlash(filepath.Join(p.DisplayRoot, rel))
}

func (p ResolvedPath) ErrorText(err error) string {
	if err == nil {
		return ""
	}
	msg := err.Error()
	if !p.External {
		return msg
	}
	return strings.ReplaceAll(msg, p.Root, p.DisplayRoot)
}

func resolveReadablePath(workDir, path string, resolver *PathResolver) ResolvedPath {
	if rp, ok := resolver.Resolve(path); ok {
		return rp
	}
	p := resolveIn(workDir, path)
	return ResolvedPath{Path: p, DisplayPath: p, Root: p, DisplayRoot: p}
}

func normalizeReadToken(token string) string {
	token = strings.TrimSpace(token)
	token = strings.TrimPrefix(token, "@")
	token = filepath.ToSlash(token)
	token = strings.TrimRight(token, "/")
	return token
}

func cleanReadSubpath(sub string) (string, bool) {
	sub = strings.TrimPrefix(filepath.ToSlash(strings.TrimSpace(sub)), "/")
	if sub == "" || sub == "." {
		return ".", true
	}
	cleaned := filepath.Clean(filepath.FromSlash(sub))
	if cleaned == "." {
		return ".", true
	}
	if !filepath.IsLocal(cleaned) {
		return "", false
	}
	return filepath.ToSlash(cleaned), true
}

// vendorDirs are directory names grep and glob skip during a recursive walk:
// dependency, VCS, and build-cache trees that almost never hold the searched
// source and would otherwise dominate the walk (node_modules alone can be 100k+
// files) and fill the result cap with noise. Only skipped when nested — a walk
// rooted directly at one (an explicit `grep node_modules`) still searches it.
var vendorDirs = map[string]bool{
	".git": true, ".svn": true, ".hg": true, ".jj": true,
	"node_modules": true, "vendor": true, ".venv": true,
	"__pycache__": true, ".mypy_cache": true, ".pytest_cache": true,
}

// skipWalkDir reports whether a directory should be pruned from a recursive walk
// rooted at root. The root itself is never pruned, so explicitly targeting a
// vendor dir still works.
func skipWalkDir(root, path, name string) bool {
	if path == root {
		return false
	}
	return vendorDirs[name] || isProtectedDir(absClean(path))
}

// skipForbidDir reports whether a directory should be pruned from a recursive
// walk because it is within any forbid-read root. forbidRoots are pre-resolved
// absolute paths; empty means unconfined.
func skipForbidDir(path string, forbidRoots []string) bool {
	return confineRead(forbidRoots, path)
}

```

## internal/tools/script/script.go

Source: https://github.com/esengine/DeepSeek-Reasonix/blob/f4f32cc7d3078f7031c7852fbf0b7de410958199/internal/tools/script/script.go

SHA-256: `94c597c9b8a977d82376586a7ec93680f24b08d92b9fafc3a669c6b5e6cf758d`

```
// Package script is the run_script tool: the model writes a short Starlark
// program that calls tools, loops over their results and prints what it
// needs, so several dependent steps cost one round trip and only the printed
// summary enters the conversation. Every call inside the script goes through
// the agent's own invoker, so it is approved, hooked and observed exactly as
// if the model had made it; the interpreter itself can reach nothing else.
package script

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"go.starlark.net/starlark"
	"go.starlark.net/starlarkstruct"
	"go.starlark.net/syntax"

	starjson "go.starlark.net/lib/json"

	"reasonix/internal/contract/tool"
)

// Name is the tool name the model calls.
const Name = "run_script"

const (
	maxSteps       = 20_000_000
	maxCalls       = 64
	maxPrintBytes  = 64 * 1024
	maxScriptBytes = 32 * 1024
)

var (
	// ErrNoInvoker is a script run where no agent offers tool calls.
	ErrNoInvoker = errors.New("run_script: no tool invoker is bound to this call")
	// ErrNested is run_script called from inside a script.
	ErrNested = errors.New("run_script cannot be called from inside a script")
	// ErrScriptFailed is a script that stopped on an error; the result carries
	// what it printed first.
	ErrScriptFailed = errors.New("script failed")
)

var fileOptions = &syntax.FileOptions{Set: true, While: true, TopLevelControl: true, GlobalReassign: true}

type runScript struct{}

// New returns the run_script tool.
func New() tool.Tool { return runScript{} }

func (runScript) Name() string { return Name }

func (runScript) Description() string {
	return "Run a short Starlark (Python-like) script that calls tools, so several dependent steps take one round trip and only what the script prints comes back. " +
		"Use it to read or search many files and keep only what matters, run a command and act on its output, or repeat one tool over a list. " +
		"In the script: call(name, **args) runs a tool and returns its output as a string, stopping the script if the call fails (read_file returns the file's exact text, ready to edit and pass to write_file); " +
		"try_call(name, **args) returns a struct with ok, output and error instead of stopping; json.decode/json.encode convert JSON; print(...) writes the result. " +
		"Every call is checked and approved exactly as if you made it directly. The script has no file, network or clock access of its own, " +
		"at most 64 tool calls, and cannot call run_script. Prefer ordinary calls for one or two independent steps."
}

func (runScript) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{"script":{"type":"string","description":"Starlark source. Example: for p in [\"a.go\", \"b.go\"]:\n    src = call(\"read_file\", path=p)\n    print(p, len(src.splitlines()))"}},"required":["script"],"additionalProperties":false}`)
}

func (runScript) ReadOnly() bool { return false }

func (runScript) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	var p struct {
		Script string `json:"script"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return "", fmt.Errorf("invalid args: %w", err)
	}
	if strings.TrimSpace(p.Script) == "" {
		return "", errors.New("invalid args: script is required")
	}
	if len(p.Script) > maxScriptBytes {
		return "", fmt.Errorf("invalid args: script is over %d bytes", maxScriptBytes)
	}
	if tool.IsNested(ctx) {
		return "", ErrNested
	}
	inv, ok := tool.InvokerFrom(ctx)
	if !ok {
		return "", ErrNoInvoker
	}
	r := &run{ctx: ctx, inv: inv, counts: map[string]int{}}
	return r.exec(p.Script)
}

// run is one script execution: what it printed and which tools it called.
type run struct {
	ctx     context.Context
	inv     tool.Invoker
	out     strings.Builder
	clipped bool
	calls   int
	counts  map[string]int
}

func (r *run) exec(src string) (string, error) {
	thread := &starlark.Thread{Name: Name, Print: r.print}
	thread.SetMaxExecutionSteps(maxSteps)
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-r.ctx.Done():
			thread.Cancel("the turn was cancelled")
		case <-done:
		}
	}()
	predeclared := starlark.StringDict{
		"call":     starlark.NewBuiltin("call", r.call),
		"try_call": starlark.NewBuiltin("try_call", r.tryCall),
		"json":     starjson.Module,
		"struct":   starlark.NewBuiltin("struct", starlarkstruct.Make),
	}
	_, err := starlark.ExecFileOptions(fileOptions, thread, "script.star", src, predeclared)
	result := r.result()
	if err != nil {
		msg := err.Error()
		var evalErr *starlark.EvalError
		if errors.As(err, &evalErr) {
			msg = evalErr.Backtrace()
		}
		return result, fmt.Errorf("%w: %s", ErrScriptFailed, msg)
	}
	return result, nil
}

func (r *run) print(_ *starlark.Thread, msg string) {
	if r.clipped {
		return
	}
	if r.out.Len()+len(msg)+1 > maxPrintBytes {
		r.out.WriteString("[… output past 64 KiB left out …]\n")
		r.clipped = true
		return
	}
	r.out.WriteString(msg + "\n")
}

// result is what the script printed, then one line naming the calls it made.
func (r *run) result() string {
	var b strings.Builder
	b.WriteString(r.out.String())
	if r.out.Len() == 0 {
		b.WriteString("(the script printed nothing)\n")
	}
	names := make([]string, 0, len(r.counts))
	for name := range r.counts {
		names = append(names, name)
	}
	sort.Strings(names)
	parts := make([]string, 0, len(names))
	for _, name := range names {
		parts = append(parts, fmt.Sprintf("%s ×%d", name, r.counts[name]))
	}
	fmt.Fprintf(&b, "--- %d tool call(s)", r.calls)
	if len(parts) > 0 {
		b.WriteString(": " + strings.Join(parts, ", "))
	}
	return b.String()
}

func (r *run) call(thread *starlark.Thread, fn *starlark.Builtin, args starlark.Tuple, kwargs []starlark.Tuple) (starlark.Value, error) {
	out, err := r.invoke(thread, fn, args, kwargs)
	if err != nil {
		return nil, err
	}
	return starlark.String(out), nil
}

func (r *run) tryCall(thread *starlark.Thread, fn *starlark.Builtin, args starlark.Tuple, kwargs []starlark.Tuple) (starlark.Value, error) {
	out, err := r.invoke(thread, fn, args, kwargs)
	errText := ""
	if err != nil {
		if !errors.Is(err, tool.ErrNestedCallFailed) {
			return nil, err
		}
		errText = err.Error()
	}
	return starlarkstruct.FromStringDict(starlarkstruct.Default, starlark.StringDict{
		"ok": starlark.Bool(err == nil), "output": starlark.String(out), "error": starlark.String(errText),
	}), nil
}

// invoke reads call(name, **args) or call(name, {args}) and runs it. A failed
// call returns its output with an error wrapping tool.ErrNestedCallFailed.
func (r *run) invoke(thread *starlark.Thread, fn *starlark.Builtin, args starlark.Tuple, kwargs []starlark.Tuple) (string, error) {
	if len(args) < 1 || len(args) > 2 {
		return "", fmt.Errorf("%s: want a tool name, then keyword arguments or one dict", fn.Name())
	}
	name, ok := starlark.AsString(args[0])
	if !ok || strings.TrimSpace(name) == "" {
		return "", fmt.Errorf("%s: the tool name must be a string", fn.Name())
	}
	if name == Name {
		return "", ErrNested
	}
	var payload starlark.Value
	switch {
	case len(args) == 2 && len(kwargs) > 0:
		return "", fmt.Errorf("%s: pass arguments as keywords or as one dict, not both", fn.Name())
	case len(args) == 2:
		payload = args[1]
	default:
		d := starlark.NewDict(len(kwargs))
		for _, kv := range kwargs {
			if err := d.SetKey(kv[0], kv[1]); err != nil {
				return "", err
			}
		}
		payload = d
	}
	encoded, err := starlark.Call(thread, starjson.Module.Members["encode"], starlark.Tuple{payload}, nil)
	if err != nil {
		return "", fmt.Errorf("%s(%q): arguments are not JSON: %w", fn.Name(), name, err)
	}
	if r.calls >= maxCalls {
		return "", fmt.Errorf("%s: the script reached its limit of %d tool calls", fn.Name(), maxCalls)
	}
	r.calls++
	r.counts[name]++
	raw, _ := starlark.AsString(encoded)
	return r.inv.Invoke(r.ctx, name, json.RawMessage(raw))
}

```
