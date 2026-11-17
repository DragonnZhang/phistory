# atomic-agent — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/prompt/default-tool-descriptors-a.ts

Source: https://github.com/AtomicBot-ai/atomic-agent/blob/fdc7813cb1b2d7a72781c957b211e03af5e02130/src/prompt/default-tool-descriptors-a.ts

SHA-256: `527c0701066d852c561edb807e3eb83991cae4d2afc831cd4f3b70e8d16a03fd`

```
import { OS_FS_READ_CONTRACT } from "../tools/os/fs/fs-read-contract.js";
import { OS_FS_LIST_CONTRACT } from "../tools/os/fs/fs-list-contract.js";
import { OS_FS_GLOB_CONTRACT } from "../tools/os/fs/fs-glob-contract.js";
import { OS_FS_GREP_CONTRACT } from "../tools/os/fs/fs-grep-contract.js";
import { OS_FS_DIFF_CONTRACT } from "../tools/os/fs/fs-diff-contract.js";
import { OS_FS_WATCH_CONTRACT } from "../tools/os/fs/fs-watch-contract.js";
import { OS_FS_WRITE_CONTRACT } from "../tools/os/fs/fs-write-contract.js";
import { OS_FS_EDIT_CONTRACT } from "../tools/os/fs/fs-edit-contract.js";
import { OS_FS_PATCH_CONTRACT } from "../tools/os/fs/fs-patch-contract.js";
import { OS_FS_TRASH_CONTRACT } from "../tools/os/fs/fs-trash-contract.js";
import { OS_FS_RESTORE_CONTRACT } from "../tools/os/fs/fs-restore-contract.js";
import { OS_FS_MOVE_CONTRACT } from "../tools/os/fs/fs-move-contract.js";
import { OS_FS_LOCATE_PROJECT_CONTRACT } from "../tools/os/fs/fs-locate-project-contract.js";
import { OS_FS_HASH_CONTRACT } from "../tools/os/fs/fs-hash-contract.js";
import type { ToolDescriptor } from "./stable-prefix.js";

/** First half of `DEFAULT_TOOL_DESCRIPTORS` (order is load-bearing). */
export const DEFAULT_TOOL_DESCRIPTORS_A: readonly ToolDescriptor[] = [
  {
    name: "browser.navigate",
    summary:
      "Open a URL in the controlled browser tab. LAST RESORT — do NOT use this to read a page (use os.web.fetch) or to search (use os.web.search). Allowed ONLY when the user explicitly asks for the browser, or the page truly needs JS/login/clicks that os.web.fetch cannot deliver.",
    argsSchema: "{ url: string }",
  },
  {
    name: "browser.click",
    summary: "Click a snapshot element by aria-ref from the latest read.",
    argsSchema: "{ ref: string }",
  },
  {
    name: "browser.type",
    summary: "Type into a snapshot element; optional Enter.",
    argsSchema: "{ ref: string, text: string, pressEnter?: boolean }",
  },
  {
    name: "browser.read_aria",
    summary:
      "Capture the page as an ARIA text snapshot. mode: full (whole page), interactive (controls only), diff (changes since the last snapshot of this tab), region (the part under ref).",
    argsSchema: "{ mode?: string, ref?: string }",
  },
  {
    name: "browser.search",
    summary:
      "Web search in the live browser (opens the SERP, refreshes the world snapshot). LAST RESORT — to search the web use os.web.search instead. Allowed ONLY when the user explicitly asks to search via the browser, or you must then click/read the live results.",
    argsSchema: "{ query: string, engine?: string }",
  },
  {
    name: "browser.tabs",
    summary: "List, switch, close, or open browser tabs.",
    argsSchema: `{ action: "list" | "switch" | "close" | "new", index?: number, url?: string }`,
  },
  {
    name: "browser.scroll",
    summary:
      "Scroll the page; does not refresh ARIA — read_aria after if needed.",
    argsSchema: `{ direction: "up" | "down" | "top" | "bottom", amount?: "page" | "half" | number }`,
  },
  {
    name: "browser.screenshot",
    summary:
      "JPEG of the page (viewport), the whole page (fullPage) or one element (ref); returns a path for vision.describe or reply.attachments.",
    argsSchema: "{ ref?: string, fullPage?: boolean }",
  },
  {
    name: "os.shell.run",
    summary:
      "Run a shell command in the working directory (may require approval). Not for deleting user files (use os.fs.trash when the user wants paths removed) or for moving, renaming or sorting them into folders (use os.fs.move), or for controlling a web browser (AppleScript, `open -a <browser> <url>`: use the browser tools). To run a command, `cmd` is required: the program, with its arguments in `args`, or a full command line. Run task steps (installs, clones, builds) yourself rather than telling the user to type them; hand over only a step the result says needs the user. A command still running at the default timeout comes back as a job: wait for it, kill it, or list jobs; `background: true` starts a server or watcher as a kept job at once.",
    argsSchema:
      "{ cmd: string, args: string[], cwd?: string, timeoutMs?: number, keep?: boolean, background?: boolean } | { wait: number /* job id */, timeoutMs?: number, keep?: boolean } | { kill: number } | { jobs: true }",
  },
  OS_FS_READ_CONTRACT.descriptor,
  OS_FS_WRITE_CONTRACT.descriptor,
  OS_FS_RESTORE_CONTRACT.descriptor,
  OS_FS_TRASH_CONTRACT.descriptor,
  OS_FS_LIST_CONTRACT.descriptor,
  OS_FS_GLOB_CONTRACT.descriptor,
  OS_FS_LOCATE_PROJECT_CONTRACT.descriptor,
  OS_FS_GREP_CONTRACT.descriptor,
  OS_FS_EDIT_CONTRACT.descriptor,
  {
    // Models reach for read_document on `.py` / `.ts` source files, hit the
    // unsupported-extension error and burn a step guessing `format`. The
    // summary therefore names the sibling tool explicitly: source code goes
    // to os.fs.read, this one is for document extraction. It deliberately
    // does NOT say "not for text files" — this tool does read .txt/.md/.csv
    // as `plain`, and a summary that contradicts the tool is the same
    // ambiguity one level up.
    //
    // `format` is spelled out as a closed set for the same reason: the bad
    // guess in issue #113 was `format: "text"`, and a model that guesses it
    // preemptively never sees the runtime hint.
    name: "os.fs.read_document",
    summary:
      "Extract plain text from documents — PDF, Office, ODF, RTF (markers in output). NOT for source code: use os.fs.read. Read-only.",
    argsSchema:
      "{ path: string, format?: 'pdf' | 'docx' | 'doc' | 'xlsx' | 'rtf' | 'odt' | 'pptx' | 'plain', maxBytes?: number, maxPages?: number, pagesFrom?: number, pagesTo?: number, sheets?: (string | number)[], pageSeparators?: boolean, includeTables?: boolean }",
  },
  {
    name: "os.fs.archive.list",
    summary: "List archive entries (zip, tar, tar.gz, gz) without extracting.",
    argsSchema: "{ path: string, format?: 'zip' | 'tar' | 'tar.gz' | 'gz' }",
    tier: "rare",
  },
  {
    name: "os.fs.archive.read_entry",
    summary: "Read one archive entry without writing to disk. Read-only.",
    argsSchema:
      "{ path: string, entry: string, as?: 'utf8' | 'base64', maxBytes?: number, format?: 'zip' | 'tar' | 'tar.gz' | 'gz' }",
    tier: "rare",
  },
  {
    name: "os.fs.archive.extract",
    summary: "Extract an archive to destDir (guarded; may require approval).",
    argsSchema:
      "{ path: string, destDir: string, overwrite?: boolean, followSymlinks?: boolean, include?: string[], limits?: { maxTotalBytes?: number, maxEntryBytes?: number, maxEntries?: number }, format?: 'zip' | 'tar' | 'tar.gz' | 'gz' }",
    tier: "rare",
  },
  OS_FS_HASH_CONTRACT.descriptor,
  OS_FS_DIFF_CONTRACT.descriptor,
  OS_FS_PATCH_CONTRACT.descriptor,
  OS_FS_WATCH_CONTRACT.descriptor,
  {
    name: "os.git.status",
    summary: "Working tree status (porcelain) and current branch. Read-only.",
    argsSchema: "{ repo?: string }",
  },
  {
    name: "os.git.log",
    summary: "Commit history with structured fields. Read-only.",
    argsSchema:
      "{ repo?: string, limit?: number, revisionRange?: string, path?: string }",
  },
  {
    name: "os.git.diff",
    summary: "Unified diff (working tree, index, or revisions). Read-only.",
    argsSchema:
      "{ repo?: string, revisionRange?: string, staged?: boolean, paths?: string[], context?: number }",
  },
  {
    name: "os.git.show",
    summary: "One commit: metadata, numstat, optional patch. Read-only.",
    argsSchema: "{ repo?: string, revision?: string, patch?: boolean }",
    tier: "rare",
  },
  {
    name: "os.git.blame",
    summary: "Per-line authorship for a file. Read-only.",
    argsSchema:
      "{ repo?: string, path: string, revision?: string, startLine?: number, endLine?: number }",
    tier: "rare",
  },
  {
    name: "os.git.branch",
    summary:
      "List branches; optional remotes, pattern, or contains. Read-only.",
    argsSchema:
      "{ repo?: string, includeRemote?: boolean, contains?: string, pattern?: string }",
    tier: "rare",
  },
  {
    name: "os.git.checkout",
    summary:
      "Switch to a branch, or create one first (may require approval). Never discards local changes.",
    argsSchema:
      "{ branch: string, create?: boolean, startPoint?: string, repo?: string }",
  },
  {
    name: "os.git.commit",
    summary:
      "Stage (`paths` or `all`) and commit with `message` (may require approval). Neither → commits what is already staged.",
    argsSchema:
      "{ message: string, paths?: string[], all?: boolean, repo?: string }",
  },
  {
    name: "os.git.push",
    summary:
      "Push the current (or named) branch to a remote, `-u` by default (may require approval). No force-push. Uses the connected GitHub token for github.com.",
    argsSchema:
      "{ remote?: string, branch?: string, setUpstream?: boolean, repo?: string }",
  },
  {
    name: "github.whoami",
    summary:
      "The GitHub account behind the connected token, plus its scopes. Read-only.",
    argsSchema: "{}",
    tier: "rare",
  },
  {
    name: "github.pr.list",
    summary:
      "List pull requests of a repo (default: origin of the working dir). Read-only.",
    argsSchema: `{ repo?: string /* owner/name */, state?: "open" | "closed" | "all", limit?: number }`,
    tier: "rare",
  },
  {
    name: "github.pr.create",
    summary:
      "Open a pull request (may require approval). Push the branch with os.git.push first. `head`/`base`/`repo` default to the current branch, the repo's default branch and origin.",
    argsSchema:
      "{ title: string, body?: string, head?: string, base?: string, draft?: boolean, repo?: string /* owner/name */ }",
    tier: "rare",
  },
  {
    name: "github.issue.list",
    summary:
      "List issues of a repo (default: origin), PRs excluded. Read-only.",
    argsSchema: `{ repo?: string /* owner/name */, state?: "open" | "closed" | "all", labels?: string[], limit?: number }`,
    tier: "rare",
  },
  {
    name: "github.issue.create",
    summary: "File an issue (may require approval).",
    argsSchema:
      "{ title: string, body?: string, labels?: string[], repo?: string /* owner/name */ }",
    tier: "rare",
  },
  {
    name: "github.issue.comment",
    summary:
      "Comment on an issue or pull request by number (may require approval).",
    argsSchema:
      "{ number: number, body: string, repo?: string /* owner/name */ }",
    tier: "rare",
  },
  {
    name: "os.git.remote",
    summary:
      "List, add, re-point or remove remotes. add/set-url need Remote sync on and approval; URLs with embedded credentials are refused.",
    argsSchema: `{ repo?: string, action?: "list" | "add" | "set-url" | "remove", name?: string, url?: string }`,
    tier: "rare",
  },
  {
    name: "os.git.fetch",
    summary: "Fetch a remote (default origin) or all. Needs Remote sync on; approval-gated.",
    argsSchema: "{ repo?: string, remote?: string, all?: boolean, prune?: boolean }",
    tier: "rare",
  },
  {
    name: "os.git.pull",
    summary: "Pull the current branch's upstream, fast-forward only unless rebase. Needs Remote sync on; approval-gated.",
    argsSchema: "{ repo?: string, remote?: string, branch?: string, rebase?: boolean }",
    tier: "rare",
  },
  {
    name: "os.git.push",
    summary:
      "Push a branch (default: current) to a remote (default origin); sets upstream on first push; never forces. Needs Remote sync on; approval-gated.",
    argsSchema: "{ repo?: string, remote?: string, branch?: string }",
    examples: ['{"remote":"origin"}'],
    tier: "rare",
  },
  {
    name: "os.git.clone",
    summary: "Clone a repository into the working directory (or dest). Needs Remote sync on; approval-gated.",
    argsSchema: "{ url: string, dest?: string, branch?: string, depth?: number }",
    examples: ['{"url":"https://github.com/owner/repo.git"}'],
    tier: "rare",
  },
  {
    name: "os.git.init",
    summary:
      "Create a local git repository (no remote); optional repo-local identity. No-op on an existing repo. Approval like a file write.",
    argsSchema:
      "{ path?: string, initialBranch?: string, userName?: string, userEmail?: string }",
    tier: "rare",
  },
  {
    name: "os.git.add",
    summary:
      "Stage changes (`all` = everything, or `paths`); `unstage` reverses. Approval like a file write.",
    argsSchema:
      "{ repo?: string, paths?: string[], all?: boolean, unstage?: boolean }",
    examples: ['{"all":true}'],
  },
  {
    name: "os.git.commit",
    summary:
      "Commit staged changes with a message; `all` also commits tracked modifications. Approval like a file write.",
    argsSchema: "{ repo?: string, message: string, all?: boolean }",
    examples: ['{"message":"feat: add parser"}'],
  },
  {
    name: "os.git.checkout",
    summary:
      "Switch branches; `create` makes a new one (optionally from `startPoint`). Branches only, never paths. Approval like a file write.",
    argsSchema:
      "{ repo?: string, branch: string, create?: boolean, startPoint?: string }",
    tier: "rare",
  },
  {
    name: "os.proc.list",
    summary: "List processes (filter, limit). Read-only.",
    argsSchema: "{ filter?: string, limit?: number }",
  },
  {
    name: "os.proc.kill",
    summary: "Send a signal to a PID (may require approval).",
    argsSchema: `{ pid: number, signal?: "SIGTERM" | "SIGKILL" | "SIGINT" | "SIGHUP" }`,
  },
  {
    name: "os.http.request",
    summary:
      "Raw HTTP GET/POST via curl for APIs/JSON; returns the body verbatim (no HTML extraction). Host allowlist + approval from config.http. To read a web page, use os.web.fetch.",
    argsSchema:
      "{ url: string, method?: 'GET' | 'POST', headers?: Record<string, string>, body?: string | object, timeoutMs?: number, followRedirects?: boolean }",
  },
  {
    name: "os.web.search",
    summary:
      "Search the web via the configured provider (Exa by default with a DuckDuckGo fallback; SearXNG/Brave configurable; Exa/Brave can use env API keys). Returns compact title/url/snippet results. Use os.web.fetch to read a chosen result.",
    argsSchema: `{ query: string, maxResults?: number }`,
    examples: [
      '{"query":"atomic agent local operator runtime","maxResults":5}',
      '{"query":"latest llama.cpp server grammar cache_prompt slot_id"}',
    ],
  },
  {
    name: "os.web.fetch",
    summary:
      "Read a web page as readable markdown/text (cf-markdown → Readability → basic). GET only, no JS, no auth; SSRF-guarded; read-only. For raw API/JSON or POST, use os.http.request.",
    argsSchema: `{ url: string, extractMode?: "markdown" | "text", maxChars?: number, timeoutMs?: number }`,
    examples: [
      '{"url":"https://example.com/article"}',
      '{"url":"https://docs.example.com/guide","extractMode":"text","maxChars":20000}',
    ],
  },
  // Appended rather than placed beside the other os.fs entries: the
  // frozen Git duplicates above are pinned by catalog position
  // (`tool-contract-conformance.test.ts`), and a rare tool renders as
  // one line in `# extras` wherever it sits.
  OS_FS_MOVE_CONTRACT.descriptor,
];

```

## src/prompt/default-tool-descriptors-b.ts

Source: https://github.com/AtomicBot-ai/atomic-agent/blob/fdc7813cb1b2d7a72781c957b211e03af5e02130/src/prompt/default-tool-descriptors-b.ts

SHA-256: `c9aa49a688d7a4f19c71f295df2c1774fbe8df9ce8d38c5a889a0f520b03ed16`

```
import type { ToolDescriptor } from "./stable-prefix.js";
import { SCREEN_TOOL_DESCRIPTORS } from "./screen-tool-descriptors.js";

/** Second half of `DEFAULT_TOOL_DESCRIPTORS` (order is load-bearing). */
export const DEFAULT_TOOL_DESCRIPTORS_B: readonly ToolDescriptor[] = [
  {
    name: "os.clipboard.read",
    summary: "Read the system clipboard as text.",
    argsSchema: "{}",
    tier: "rare",
  },
  {
    name: "os.clipboard.write",
    summary: "Write text to the system clipboard.",
    argsSchema: "{ value: string }",
    tier: "rare",
  },
  {
    name: "os.window.list",
    summary: "List window titles. Read-only.",
    argsSchema: "{}",
    tier: "rare",
  },
  {
    name: "os.window.focus",
    summary: "Focus a window by title substring.",
    argsSchema: "{ title: string }",
    tier: "rare",
  },
  {
    name: "os.notify",
    summary: "System notification (title + message).",
    argsSchema: "{ title: string, message: string, sound?: boolean }",
    tier: "rare",
  },
  {
    name: "os.email.inbox",
    summary:
      "Newest messages in the agent's own e-mail inbox (Atomic Mail): sender, subject, preview, unread.",
    argsSchema: "{ limit?: number }",
    tier: "rare",
  },
  {
    name: "os.email.send",
    summary:
      "Send a plain-text e-mail from the agent's own inbox; the operator approves recipient + subject first.",
    argsSchema: "{ to: string, subject: string, text: string }",
    tier: "rare",
  },
  ...SCREEN_TOOL_DESCRIPTORS,
  {
    name: "skill.view",
    summary: "Load an installed skill body (SKILL.md) into the session tail.",
    argsSchema: "{ name: string }",
  },
  {
    name: "tool.view",
    summary:
      "Load full args schema for a tool listed in `# extras` into `### loaded-tools`.",
    argsSchema: '{ name: string } /* e.g. "os.git.show" */',
  },
  {
    name: "skill.run_script",
    summary:
      "Run a script from a skill's requires_scripts (may require approval).",
    argsSchema:
      "{ skill: string, script: string, args?: string[], timeoutMs?: number }",
    tier: "rare",
  },
  {
    name: "memory.profile.set",
    summary:
      "Upsert a durable user profile fact (pinned vs contextual with keywords).",
    argsSchema:
      "{ key: string, value: string, pinned?: boolean, keywords?: string[] /* required when pinned=false */ }",
    examples: [
      '{"key":"language","value":"ru"}',
      '{"key":"deploy_cmd","value":"pnpm run deploy","pinned":false,"keywords":["deploy","release","ship"]}',
    ],
  },
  {
    name: "memory.profile.remove",
    summary: "Delete a profile fact by key.",
    argsSchema: "{ key: string }",
  },
  {
    name: "memory.profile.list",
    summary: "List all profile facts with metadata. Read-only.",
    argsSchema: "{}",
  },
  {
    name: "memory.profile.history",
    summary:
      "Return the bi-temporal history of one profile key (oldest first; active value last).",
    argsSchema: "{ key: string }",
    examples: ['{"key":"language"}'],
  },
  {
    name: "memory.notes.store",
    summary:
      "Store a durable note (triggers: remember, outcomes, preferences; before reply on non-trivial work).",
    argsSchema:
      "{ content: string /* max 4000 chars */, tags?: string[] /* 1–4 */ }",
    examples: [
      '{"content":"Prefer pnpm; package-lock ignored","tags":["prefs","tooling"]}',
      '{"content":"Fix auth test via Date mock — 8f2a1c9","tags":["bugfix"]}',
    ],
  },
  {
    name: "memory.notes.recall",
    summary: "Search notes or fetch by id (from memory-index pointers).",
    argsSchema:
      "{ query?: string, id?: number, k?: number, scope?: 'project' | 'all', tags?: string[] }",
    examples: [
      '{"query":"pnpm","k":3}',
      '{"query":"flaky login","scope":"project"}',
      '{"id":42}',
    ],
  },
  {
    name: "memory.notes.forget",
    summary: "Delete a note by id (user asked to forget or note obsolete).",
    argsSchema: "{ id: number }",
    tier: "rare",
  },
  {
    name: "memory.lessons.recall",
    summary:
      "Read distilled lessons by id (pointer from `### lessons`) or BM25 query. Returns full principle bodies; the prompt only shows activation pointers.",
    argsSchema: "{ id?: number, query?: string, k?: number /* 1..10 */ }",
    examples: ['{"id":42}', '{"query":"pnpm install","k":2}'],
  },
  {
    name: "memory.procedures.recall",
    summary:
      "Read advisory how-to procedures by id (pointer from `### procedures`) or BM25 query. Returns ordered steps (description + optional toolHint); the prompt only shows activation pointers. Steps are guidance, not commands — follow them or consciously deviate.",
    argsSchema: "{ id?: number, query?: string, k?: number /* 1..10 */ }",
    examples: [
      '{"id":17}',
      '{"query":"extract typescript function signatures","k":2}',
    ],
  },
  {
    name: "tasks.schedule",
    summary: "Schedule a one-shot task; current session or newSession.",
    argsSchema:
      '{ userMessage: string, at?: number, inSeconds?: number, newSession?: boolean, notify?: "telegram" }',
    examples: [
      '{"userMessage":"check build","inSeconds":300}',
      '{"userMessage":"PR follow-up","at":1735689600000,"newSession":true}',
    ],
    tier: "rare",
  },
  {
    name: "tasks.cron",
    summary: "Recurring cron task; runs in a dedicated persistent session.",
    argsSchema:
      '{ userMessage: string, expression: string, tz?: string /* IANA */, notify?: "telegram" /* report result to paired Telegram */ }',
    examples: [
      '{"userMessage":"digest","expression":"0 9 * * *","tz":"Europe/Berlin","notify":"telegram"}',
    ],
    tier: "rare",
  },
  {
    name: "tasks.list",
    summary: "List tasks (filter by status CSV; optional session). Read-only.",
    argsSchema: "{ status?: string, sessionId?: string, limit?: number }",
    tier: "rare",
  },
  {
    name: "tasks.cancel",
    summary: "Cancel a task by id; idempotent on terminal rows.",
    argsSchema: "{ id: string }",
    tier: "rare",
  },
  {
    name: "tasks.show",
    summary: "One task by id. Read-only.",
    argsSchema: "{ id: string }",
    tier: "rare",
  },
  {
    // `frequent` tier: vision.describe needs the full args schema +
    // examples in the stable prefix so the model first-shots a valid
    // call. `rare` (single-line manifest) led to the model guessing
    // `{paths: [...]}` without `prompt` and burning a step on the
    // schema error before retrying with the right shape.
    name: "vision.describe",
    summary:
      "Describe one or more images via the configured vision LLM. Only available when the active model + provider support multimodal input. Accepts at most 4 images per call by default (`vision.maxImagesPerCall`); to cover more images, split them across several calls.",
    argsSchema:
      "{ prompt: string, path?: string, paths?: string[] /* png|jpg|jpeg|webp|gif; at most 4 by default */ }",
    examples: [
      '{"path":"./screenshot.png","prompt":"What error is shown?"}',
      '{"paths":["a.png","b.png"],"prompt":"Compare these two diagrams"}',
    ],
  },
  {
    // Frequent tier, like vision.describe: "draw me a ..." must reach a
    // valid call first time, and the summary carries the one step a
    // model would otherwise skip (attaching the file to the reply).
    // Offered only while an OpenRouter key is configured
    // (`GATED_TOOLS.image`).
    name: "image.generate",
    summary:
      "Generate pictures from a text prompt (cloud image model on OpenRouter via Atomic Chat Core; paid, so it may ask the user first). Saves image files in the working folder and returns their paths; to show them, pass the paths in reply.attachments. Write the prompt in English with subject, style, lighting and composition.",
    argsSchema:
      '{ prompt: string, size?: string /* square|landscape|portrait or 1:1|16:9|9:16|4:3|3:4|3:2|2:3; default square */, count?: number /* 1..4, default 1 */, seed?: number, model?: string /* OpenRouter image model id; omit for the default */ }',
    examples: [
      '{"prompt":"A red fox in a snowy birch forest at dawn, soft golden light, photorealistic"}',
      '{"prompt":"Flat vector logo of a paper rocket, two colours","size":"square","count":2}',
    ],
  },
  {
    // Frequent tier beside image.generate, for the same reason: "make a
    // video of ..." must reach a valid call first time and the summary
    // carries the reply.attachments step. Offered only while an
    // OpenRouter key is configured (`GATED_TOOLS.video`).
    name: "video.generate",
    summary:
      "Generate a short video clip from a text prompt, optionally starting from an image file (cloud video model on OpenRouter via Atomic Chat Core; paid per second, so it asks the user first and takes one to a few minutes). Saves an .mp4 in the working folder and returns its path; to show it, pass the path in reply.attachments. If it answers that the model is busy, tell the user and ask which listed model to use; pass `model` only when the user names one. Write the prompt in English: subject, action, camera movement, style, sound.",
    argsSchema:
      '{ prompt: string, image?: string /* path of a png|jpg|webp first frame */, duration?: number /* seconds; default 4; the default model takes 4|6|8 */, aspect?: string /* 16:9|9:16 or landscape|portrait; default 16:9 */, resolution?: string /* 720p|1080p|4K; omit for the model default */, audio?: boolean, seed?: number, model?: string /* OpenRouter video model id; omit for the default */ }',
    examples: [
      '{"prompt":"A red fox trotting through a snowy birch forest at dawn, slow tracking shot, soft golden light"}',
      '{"prompt":"The rocket lifts off and flies out of frame","image":"rocket.png","duration":6,"aspect":"9:16"}',
    ],
  },
  {
    // MCP discovery / resource / prompt tools. The actual MCP tool
    // calls themselves come from the dynamic descriptor builder
    // (see `mcp-descriptor-builder.ts`) and ship at tier `rare`.
    name: "mcp.resource.list",
    summary:
      "List resources exposed by an MCP server. Pass `server` (one of the configured MCP server names) and optional `limit` (1..100, default 30).",
    argsSchema: "{ server: string, limit?: number /* 1..100 */ }",
    tier: "rare",
  },
  {
    name: "mcp.resource.read",
    summary:
      "Read a resource exposed by an MCP server. Pass `server` and the exact `uri` returned by `mcp.resource.list`.",
    argsSchema: "{ server: string, uri: string }",
    tier: "rare",
  },
  {
    // The quoting note belongs HERE, not in the `ToolDefinition`
    // description in `mcp/mcp-prompt-tools.ts`. That string reaches
    // only `http/route-capabilities.ts`; what the model reads is this
    // `summary` — rendered into the stable prefix by `formatToolRare`
    // and into the native function spec by `openai-tool-call-adapter`
    // as `${summary}\nArgs: ${argsSchema}`. A convention explained
    // only in the definition is a convention the model never sees, so
    // it would meet a quoted key with no idea to strip the quotes and
    // send `"my (odd) name"` verbatim — the "unknown prompt" the
    // quoting exists to prevent.
    name: "mcp.prompt.list",
    summary:
      'List prompt templates exposed by an MCP server. Pass `server` and optional `limit` (1..100, default 30). One entry per line: `<name>(arg1, arg2?) — description`. A name or argument name that would be ambiguous in that format is printed JSON-quoted (`"my (odd) name"`); pass it decoded, without the quotes, as `mcp.prompt.get`\'s `name` or as an `arguments` key.',
    argsSchema: "{ server: string, limit?: number /* 1..100 */ }",
    tier: "rare",
  },
  {
    name: "mcp.prompt.get",
    summary:
      "Render a prompt template from an MCP server. Pass `server`, `name`, and optional `arguments` (object of string values for the template parameters).",
    argsSchema:
      "{ server: string, name: string, arguments?: Record<string,string> }",
    tier: "rare",
  },
  {
    // Frequent tier: the review step of a fan-out reaches for it, and a
    // reviewer that has to `tool.view` first reviews less. Read-only, so
    // the fusion orchestrator gate lets it through (decision D1).
    name: "verify.syntax",
    summary:
      "Syntax-check files, one checker per file by extension (.js/.json in-process then node --check, .ts via the project's tsc, .py, .sh, .html inline scripts + a warning for content after </html>, .css braces, .go/.rb/.php/.lua/.zsh via their own parse-only tool when installed). Read-only. Reports a file with no checker as unchecked, never as passing.",
    argsSchema: "{ files: string[] }",
  },
  {
    // Frequent tier for the same reason, and with an example: the
    // runtime failures no syntax check sees (a throw on load, a dead
    // button, a probe that never moves) are what this catches, and a
    // page run with two probes is not something a model first-shots
    // from a one-line manifest.
    name: "verify.run",
    summary:
      "Run what was built against a throwaway copy of the working directory (nothing it writes reaches the workspace; may require approval). kind 'command': a test runner, compiler or script. kind 'service': start a server, wait for a port/url, send requests. kind 'page': a local HTML file or url in a headless browser (add `start` to serve the url first; the page opens once `ready` passes, by default when the url answers 2xx) — scripted input, then `seconds` of runtime with `probes` sampled; collects uncaught errors, console errors and failed getElementById/querySelector lookups. `checks` are assertions on the result. Default `network: false` (a soft proxy block, not a sandbox).",
    argsSchema:
      "{ kind: 'command' | 'service' | 'page', cmd?: string, args?: string[], start?: { cmd: string, args?: string[] }, ready?: { port?: number, url?: string, timeoutMs?: number }, requests?: [{ method?: string, path?: string, url?: string, body?: string, expectStatus?: number, expectBody?: string }], path?: string, url?: string, script?: [{ action: 'click' | 'key' | 'type' | 'wait', selector?: string, key?: string, text?: string, ms?: number }], seconds?: number /* page runtime, default 5 */, probes?: [{ name: string, expr: string /* JS expression */ }], checks?: string[] /* 'exit 0' | 'exit != 0' | 'stdout contains \"x\"' | 'stderr not contains \"x\"' | 'status 200' | 'no errors' | 'missing selectors 0' | 'probe <name> decreases|increases|equals <v>|reaches <v>|stays <v>' */, cwd?: string, env?: Record<string,string>, timeoutMs?: number, network?: boolean }",
    examples: [
      '{"kind":"page","path":"index.html","script":[{"action":"click","selector":"#launch"},{"action":"key","key":"ArrowLeft"}],"seconds":6,"probes":[{"name":"lives","expr":"window.game.lives"},{"name":"score","expr":"window.game.score"}],"checks":["no errors","missing selectors 0","probe score increases"]}',
      '{"kind":"command","cmd":"npm","args":["test"],"checks":["exit 0","stdout not contains \\"failed\\""]}',
    ],
  },
  {
    // `frequent` tier and a full example: the whole point of the tool is
    // that a cloud orchestrator reaches for it instead of doing the bulk
    // itself, and a one-line manifest is not enough to first-shot a
    // well-formed task list. Only mounted in fusion mode, so the tokens
    // are paid by exactly the runs that use it.
    name: "fusion.delegate",
    summary:
      "Delegate independent parts of the work to local worker agents that run concurrently and report back. Each task becomes one throwaway worker turn that sees the operator's original request and your `instructions`, but nothing else from this conversation, so `instructions` must carry what the request does not (exact paths, the contract between parts, acceptance criteria, the answer format you want). Returns every worker's reply plus a per-task status. You choose `maxWorkers`; it is bounded only by the task count and the machine. Call it on its own, never alongside other tool calls.",
    argsSchema:
      '{ tasks: [{ id: string, instructions: string, title?: string /* defaults to the id */, deliverable?: string, files?: string[], etaSeconds?: number /* your estimate of how long this part takes; shown to the operator beside the running clock so they can tell thinking from stuck */ }] /* 1..8 */, maxWorkers?: number /* how many run at once; you decide */, contract?: { inputs?: string[] /* the files the request provides: workers edit them in place, never replace */, owners?: { [path]: taskId }, provides?: [{ task, kind: "symbol"|"file"|"id"|"endpoint"|"env"|"flag"|"other", name, in?: path }], requires?: [{ task, name /* a provides name */ }], checks?: [{ task?, ...verify.run args }] } /* the interface between the parts: shown to every worker, checked after the fan-out */ }',
    examples: [
      '{"tasks":[{"id":"t1","title":"Map the auth routes","instructions":"List every route under src/http/ that touches auth. For each: path, method, and the middleware it runs.","deliverable":"one bullet per route","etaSeconds":120},{"id":"t2","title":"Summarise the session store","instructions":"Read src/session/session-store.ts and describe its public API and persistence model.","files":["src/session/session-store.ts"]}]}',
    ],
  },
  {
    name: "reply",
    summary:
      "Final natural-language answer; ends the macro-turn. Never use to announce a pending action; keep text short (no huge dumps). If the task requires an exact answer format or marker, `text` must be ONLY that bare value or marker line — no preamble or commentary. `attachments`: paths of existing files to deliver with the reply (a report you wrote, a screenshot, a converted file) — on Telegram/Discord they are sent as files after the text; never paste a file's contents into `text` when you can attach it.",
    argsSchema: "{ text: string, attachments?: string[] }",
  },
  {
    name: "finish",
    summary:
      "End the session with a final summary; only if the user asked to end.",
    argsSchema: "{ summary: string }",
  },
];

```

## src/prompt/stable-prefix.ts

Source: https://github.com/AtomicBot-ai/atomic-agent/blob/fdc7813cb1b2d7a72781c957b211e03af5e02130/src/prompt/stable-prefix.ts

SHA-256: `c2191efcb1d84d62ed970dd8e36e74a5c6d5d91a7736caae95f3b5200221598b`

```
import type { ToolCallTransport } from "../llm/provider/completion-types.js";
import { COMPOSIO_GUIDANCE, isComposioActive } from "./composio-guidance.js";
import { GITHUB_GUIDANCE, isGithubActive } from "./github-guidance.js";
import { buildFusionGuidance, isFusionActive } from "./fusion-guidance.js";
import type { FusionMachineFacts } from "./fusion-machine-facts.js";
import {
  formatSkillCatalogLine,
  formatSkillCatalogOmittedLine,
} from "../skills/skill-catalog.js";
import { partitionByRole, type ToolRole } from "../tools/tool-roles.js";

/**
 * `frequent` — full `args` + optional `examples` in the stable prefix.
 * `rare` — one-line manifest in the prefix; use `tool.view` (or error-path
 * autoload) to materialise the full schema in `### loaded-tools`.
 */
export type ToolTier = "frequent" | "rare";

export interface ToolDescriptor {
  name: string;
  summary: string;
  /**
   * Human-readable args sketch rendered into the stable prefix's
   * `### tools` section and consumed by the GBNF + parser path. Stays
   * authoritative for prompt rendering even when `argsJsonSchema` is
   * also populated — the LLM reads it as part of the prompt.
   */
  argsSchema: string;
  /**
   * Defaults to `frequent` when omitted.
   */
  tier?: ToolTier;
  /**
   * Optional few-shot argument examples rendered under the tool in the
   * stable prefix. Each entry is a pre-formatted JSON literal (as a
   * string) so we do not stringify at prompt-build time. Keep short —
   * they live in the stable prefix and cost tokens on every turn.
   */
  examples?: readonly string[];
  /**
   * Optional structured JSON Schema for the tool's `args` object. Used
   * exclusively by the cloud `native_tools` transport (OpenAI / OpenRouter
   * `tool_calls`) to constrain the model's argument shape. When omitted
   * the OpenAI adapter falls back to `{ type: "object",
   * additionalProperties: true }` — i.e. anything goes, which is what we
   * shipped before Memory-v2 phase 7b. Local llama-server with GBNF does
   * **not** consume this field; the grammar there already constrains the
   * shape and the prompt-side `argsSchema` text is what the model reads.
   *
   * For MCP-namespaced tools this field carries the server's
   * `inputSchema` verbatim (see `mcp-descriptor-builder.ts`).
   */
  argsJsonSchema?: Record<string, unknown>;
}

export interface CapabilitiesSummary {
  platform: NodeJS.Platform;
  arch: string;
  browserChannel: string;
  workingDir: string;
  hasClipboard: boolean;
  hasWmctrl: boolean;
  hasNotifications: boolean;
  /** The agent's own e-mail address (Atomic Mail), when an inbox exists. */
  emailAddress?: string | null;
}

export interface SkillCatalogEntry {
  name: string;
  description: string;
  source: "global" | "project";
}

export interface StablePrefixInput {
  toolDescriptors: readonly ToolDescriptor[];
  capabilities: CapabilitiesSummary;
  skillCatalog: readonly SkillCatalogEntry[];
  /**
   * Installed skills `buildSkillCatalogSection` left out of
   * `skillCatalog` because `skills.catalogTokenBudget` ran out. When
   * above zero the `### skills` block ends on a truncation marker
   * naming the knob, so the model does not read a clipped catalog as
   * the complete one (issue #466). Omitted / `0` keeps the block
   * byte-identical to the legacy output (KV-cache safe).
   */
  skillCatalogDropped?: number;
  systemPersona?: string;
  reasoningSystemToken?: string;
  /**
   * When set, the stable prefix opens with this native turn token (e.g.
   * `"<|turn>system\n"`) and the `reasoningSystemToken` is placed at the
   * very top of the system turn (before `### system`) — required by Gemma 4
   * to activate its reasoning channel. When omitted, the prefix keeps the
   * legacy monolithic head (`### system` first) and is byte-identical.
   */
  turnSystemOpen?: string;
  maxParallelToolCalls?: number;
  /**
   * Transport the serving link uses for tool calls. Under
   * `"native_tools"` the persona's emission mandate and the
   * `### instructions` block switch to native function-calling guidance:
   * the text-JSON "Emit a JSON ARRAY" mandate contradicts the
   * request-level `tools` payload and drives cloud models back to text
   * emission (issue #285). The `### tools` catalog is rendered in both
   * modes — the provider fallback chain can hand a native-shaped request
   * to a grammar-only llama-server link, and the catalog carries the
   * tier / `tool.view` semantics. Omitted or `"grammar"` keeps the
   * prefix byte-identical to the legacy output (KV-cache safe).
   */
  toolTransport?: ToolCallTransport;
  /**
   * What this machine serves fusion workers with — slot count and local
   * model. Read only when the `### fusion` block renders at all, so a
   * non-fusion install's prefix stays byte-identical whatever is passed
   * here. Omitted, the block keeps its behavioural lines and states no
   * numbers; see `fusion-machine-facts.ts` for why an unknown fact is
   * left unsaid rather than guessed.
   */
  fusion?: FusionMachineFacts;
  /**
   * The turn's tool role (`tool-roles.ts`). Under `builder` or
   * `orchestrator` the `### tools` block describes the role's tools as
   * today (frequent in full, rare as one-liners) and lists every other
   * descriptor on ONE line of names, "also available via tool.view".
   * The prefix is therefore per role — stable within a turn, one cold
   * read on a role change. Omitted or `"full"` keeps the block
   * byte-identical to before roles existed.
   */
  toolRole?: ToolRole;
}

/** Header of the out-of-role names line — pinned by tests, read by the model. */
export const ALSO_AVAILABLE_VIA_TOOL_VIEW = "# also available via `tool.view`:";

/**
 * Persona lines shared verbatim between the grammar and native-tools
 * variants. Only the emission mandate (line 1), the bias-toward-action
 * phrasing (line 2), and the reply-discipline line (line 5, see
 * `REPLY_DISCIPLINE_LINE_*`) differ per transport; everything else is
 * transport-neutral. Extracted so the two personas cannot drift apart.
 */
const SYSTEM_PERSONA_TERMINALS_LINE =
  "Terminals: `reply` returns the final answer to the user and ends the current macro-turn (session stays open). `finish` ends the entire session; only with explicit user intent.";

/**
 * Right after the bias-toward-action line in both personas (F51). Live,
 * five first attempts across two local models rewrote the file the
 * request named as the input from memory; `os.fs.write` now refuses that
 * (`fs-input-guard.ts`), and this is the model told the rule before it
 * reaches for the tool. Since ATO-851 it no longer names `overwrite:
 * true`: the flag stopped lifting the refusal, and teaching it here was
 * the one-step bypass. Pinned by `stable-prefix.test.ts`.
 */
export const SYSTEM_PERSONA_INPUTS_LINE =
  "Files that existed before this turn are the user's: edit them in place and write new files beside them; do not regenerate a provided file from memory. To replace one the request did not ask to replace, ask the user first.";

/** Byte-identical to the pre-#285 line (KV-cache safe). */
const REPLY_DISCIPLINE_LINE_GRAMMAR =
  "`reply` is ONLY for the final user-facing text after all needed tools ran. The user does not see intermediate text — if another tool is next, emit that tool JSON, not `reply`.";

/**
 * Native variant of the reply-discipline line: "emit that tool JSON" is
 * yet another text-JSON emission mandate, so under `native_tools` it
 * becomes "call that tool" (issue #285).
 */
const REPLY_DISCIPLINE_LINE_NATIVE =
  "`reply` is ONLY for the final user-facing text after all needed tools ran. The user does not see intermediate text — if another tool is next, call that tool, not `reply`.";

const SYSTEM_PERSONA_SHARED_LINES = [
  "Output discipline: when the request specifies an exact answer format, marker, length, or units, the `reply` text MUST be ONLY that — the bare value or the exact required line and nothing else (correct units, no preamble, no restating the question, no extra commentary or markdown before or after). If a specific final-answer line or marker is required, emit exactly that line as the entire reply. When no format is specified, answer as fully and helpfully as the task warrants.",
  "Finishing the job: when the user asks you to build, run, compute, or verify something, the deliverable is a real result backed by actual tool output — not a description of one. Do not stop after a stub, a plan, or a single command; keep calling tools until you have actually produced the requested result, then `reply` with what real execution returned. If a tool, install, or network call fails and blocks the real path, say so directly and try an alternative (a different approach, or `reply` to ask the user). NEVER substitute plausible-looking fabricated output (made-up data, invented file contents, synthesised API responses) for results you could not actually produce — reporting a blocker honestly is always better than inventing a result.",
  "When a line in `### skills` matches the user's request, emit `skill.view` first — the catalog line is a stub, the body has the actual procedure — unless that skill is already under `### loaded-skills`. This applies to every skill, including text-only workflows; do not guess the answer from the catalog summary. Rare tool? `tool.view` first. Loop: tools, read `### world` / `### conversation`, then more tools or `reply`. `browser.navigate` / `browser.search` refresh the world; avoid redundant `read_aria`. Do not invent facts — use `reply` to ask if stuck.",
  "Web — READ THIS BEFORE ANY WEB ACTION. DEFAULT and ALWAYS-FIRST path: use `os.web.search` to search the web and `os.web.fetch` to read ANY page (clean markdown, no browser). The `browser.*` tools are a LAST RESORT, not the default: use them ONLY when (a) the user explicitly says to use the browser, or (b) the page genuinely needs JS/login/clicks that `os.web.fetch` cannot deliver. Opening `browser.navigate` / `browser.search` just to read a page or run a search is WRONG — it wastes the turn and bloats context. If you catch yourself reaching for the browser, stop and use `os.web.search` or `os.web.fetch` instead.",
  "Large directories: `os.fs.list` only shows up to maxEntries matches—use extensions, pattern, sort, or `os.fs.glob` to narrow before assuming a file type is absent. For many PDFs or resumes prefer filename `os.fs.glob` patterns plus `os.fs.read_document` on a short candidate list; avoid sweeping `os.fs.grep` with `glob` over huge `*.pdf` trees.",
  "Commands: run task steps yourself with `os.shell.run` (installs such as `brew`, `npm i -g`, `pip`; clones, builds, servers) — never print commands for the user to type; approval keeps them in control. Hand over only a step that needs them (browser sign-in, password or `sudo`, keychain, a terminal question), then continue.",
  "Deleting files or directories: when the user asks to delete, remove, erase, or trash paths, call `os.fs.trash` with concrete absolute paths in `paths` (use `os.fs.list` / `os.fs.glob` first if you need to discover names). Do not use `os.shell.run` with `rm`, `unlink`, or `rmdir` for that unless the user explicitly demands permanent irreversible shell deletion. To move, rename, or sort files into folders, use `os.fs.move` (it creates missing folders), not `mv`/`mkdir` in the shell.",
  "Memory: persist with `memory.profile.*` and `memory.notes.*` as needed. Use `### lessons` (pointer view of distilled rules from past episodes — call `memory.lessons.recall { id }` to read the full principle), `### procedures` (pointer view of advisory how-to templates — call `memory.procedures.recall { id }` to read the `steps[]`; templates are guidance, not law — follow them or consciously deviate), `### recalled` / `### memory-index` and `memory.notes.recall` for past context. Store distilled facts, not full dumps. `### notice` in the tail is a hard nudge to change strategy.",
];

/**
 * The stable prefix is the part of the prompt that must stay byte-stable
 * within a session so llama.cpp can reuse its KV-cache. Order and spacing
 * are intentional — changing any byte invalidates the slot.
 */
export const DEFAULT_SYSTEM_PERSONA = [
  "You are atomic-agent, a local operator. Each step emits exactly one JSON array matching the tool grammar — no other prose.",
  "Bias toward action: keep planning minimal; unless the user explicitly asked for analysis or explanation only, choose the next tool-call array quickly instead of long deliberation. If the template forces a separate reasoning or thinking block before JSON, keep that block to a few words (or effectively empty), then emit the array.",
  SYSTEM_PERSONA_INPUTS_LINE,
  SYSTEM_PERSONA_TERMINALS_LINE,
  REPLY_DISCIPLINE_LINE_GRAMMAR,
  ...SYSTEM_PERSONA_SHARED_LINES,
].join("\n");

/**
 * Persona for the `native_tools` transport. The request already carries
 * an OpenAI `tools` array with `tool_choice: "auto"`, so the persona must
 * mandate the function-calling interface — repeating the grammar
 * persona's "exactly one JSON array" line alongside a native `tools`
 * payload is a dual mandate that measurably drives models back to
 * text-JSON emission (issue #285: 0/6 native `tool_calls` under
 * `native_tools`). Lines past the first two are shared with
 * `DEFAULT_SYSTEM_PERSONA`.
 */
export const NATIVE_TOOLS_SYSTEM_PERSONA = [
  "You are atomic-agent, a local operator. Each step calls tools through the native function-calling interface — never write tool-call JSON into the text of your answer, and never put your answer in the reasoning channel.",
  "Bias toward action: keep planning minimal; unless the user explicitly asked for analysis or explanation only, choose the next tool call quickly instead of long deliberation. Keep any reasoning or thinking to a few words (or effectively empty), then make the call.",
  SYSTEM_PERSONA_INPUTS_LINE,
  SYSTEM_PERSONA_TERMINALS_LINE,
  REPLY_DISCIPLINE_LINE_NATIVE,
  ...SYSTEM_PERSONA_SHARED_LINES,
].join("\n");

/**
 * `### rules` body shared verbatim between the transports. Only the
 * emission-shape opener differs: grammar mandates the length-1 text
 * array, native mandates the function-calling interface — leaving the
 * grammar opener in place under `native_tools` re-creates the dual
 * mandate the transport split exists to remove (issue #285).
 */
const RULES_SHARED_TAIL =
  "Destructive or privileged tools may require user approval. If the user declines a call, do not retry it or reach the same result another way (another tool, a shell command); tell them it was not done and ask what they want instead. If `### skills` lists a playbook that fits the user goal, call `skill.view` first unless that skill is already under `### loaded-skills`; do not act on a catalog stub — the body has the procedure. This holds for every skill (text replies included), not just browser/shell shortcuts. Summaries in `# extras` list rare tools; call `tool.view` to load the full `args` schema into `### loaded-tools` before use. Large trees: narrow with `os.fs.list` filters or `os.fs.glob` before reading content; do not use `os.fs.grep` with broad binary globs (e.g. every `*.pdf`) across huge folders—use tight globs then `os.fs.read_document` on candidates.";

/**
 * The grammar transport's `### rules` line: the pre-#285 line with the
 * declined-call sentence ATO-225 added to the shared tail. Stable within
 * a build, so the KV-cache prefix still holds across turns.
 */
const GRAMMAR_RULES_LINE = `One tool-call array per step (including \`skill.view\`); a solo action is a length-1 array. ${RULES_SHARED_TAIL}`;

const NATIVE_TOOLS_RULES_LINE = `One batch of tool calls per step (including \`skill.view\`), all through the native function-calling interface — never written out as JSON text; a solo action is a single call. ${RULES_SHARED_TAIL}`;

/**
 * Windows-only nudge appended after the persona. The default persona and
 * examples are POSIX-flavoured (`grep`/`cat`/`rm`), so on Windows the model
 * needs an explicit steer toward native `cmd.exe` equivalents. Rendered only
 * when `capabilities.platform === "win32"`; other platforms keep the stable
 * prefix byte-identical. Platform is fixed for the lifetime of a session, so
 * this stays KV-cache stable within a session.
 */
export const WINDOWS_PLATFORM_HINT = [
  "Windows environment: `os.shell.run` uses a `cmd.exe` subshell. Prefer native Windows commands — `findstr` (not grep), `where` (not which), `type` (not cat), `dir` (not `ls -la`), `copy`, `del`/`rmdir` semantics. Reference environment variables as `%VAR%` and use backslash `\\` path separators (e.g. `C:\\Users\\me\\file.txt`). Chain commands with `&&`, `||`, and pipe with `|`.",
  "PowerShell cmdlets (`New-Item`, `Move-Item`, `Copy-Item`, `Get-ChildItem`, `Remove-Item`) do not exist in `cmd.exe` and fail as unrecognized commands — do not use them.",
  "To move, rename or sort files into folders, call `os.fs.move` (`tool.view` it first), not `move`/`ren`/`mkdir` in the shell: it creates missing folders, takes paths with spaces as they are, and moves many files in one call. Only an empty folder needs the shell: `mkdir \"C:\\path\\Folder\"` (quote every path that contains spaces).",
  "Deletion still goes through `os.fs.trash`, never `del`/`rmdir`, unless the user explicitly demands a permanent shell delete.",
].join("\n");

export function buildStablePrefix(input: StablePrefixInput): string {
  // Present only while a Composio session is mounted, so an install
  // with no key pays nothing for it and its prefix is byte-identical
  // to before the integration existed.
  const composioActive = isComposioActive(input.toolDescriptors);
  // Same contract for GitHub: the `github.*` descriptors are only in
  // the catalog while the hub holds a token.
  const githubActive = isGithubActive(input.toolDescriptors);
  const integrationsBlock = [
    ...(composioActive ? [COMPOSIO_GUIDANCE] : []),
    ...(githubActive ? [GITHUB_GUIDANCE] : []),
  ];
  // Same contract as the Composio block: present only while the tool it
  // describes is mounted, so a non-fusion install pays zero bytes.
  const fusionActive = isFusionActive(input.toolDescriptors);
  const nativeTools = input.toolTransport === "native_tools";
  const persona =
    input.systemPersona ??
    (nativeTools ? NATIVE_TOOLS_SYSTEM_PERSONA : DEFAULT_SYSTEM_PERSONA);
  const maxParallelToolCalls = input.maxParallelToolCalls ?? 8;
  // A role splits the catalog: its own tools render as they always
  // have, the rest collapse to one line of names. `full` (or no role)
  // puts everything on the inside, so the block below is byte-identical
  // to the pre-role output — `outside` is empty and adds no line.
  const { inRole, outside } = partitionByRole(
    input.toolRole,
    input.toolDescriptors,
  );
  const frequent: ToolDescriptor[] = [];
  const rare: ToolDescriptor[] = [];
  for (const d of inRole) {
    if (d.tier === "rare") rare.push(d);
    else frequent.push(d);
  }
  const commonBlock = frequent.map(formatToolFrequent).join("\n");
  const extrasBlock = rare.map(formatToolRare).join("\n");
  const outsideLine =
    outside.length > 0
      ? `${ALSO_AVAILABLE_VIA_TOOL_VIEW} ${outside.map((d) => d.name).join(", ")}`
      : null;
  const caps = formatCapabilities(input.capabilities);
  const skillsDropped = input.skillCatalogDropped ?? 0;
  const skills =
    input.skillCatalog.length > 0
      ? [
          ...input.skillCatalog.map(formatSkillCatalogLine),
          ...(skillsDropped > 0
            ? [formatSkillCatalogOmittedLine(skillsDropped)]
            : []),
        ].join("\n")
      : "(none installed)";
  // Head ordering. With `turnSystemOpen` (Gemma 4 turn-framing) the system
  // turn opens first and the reasoning token sits at the very top of it,
  // before `### system`. Otherwise the legacy monolithic head is preserved
  // byte-for-byte so qwen/plain KV-cache is untouched.
  const head = input.turnSystemOpen
    ? [
        input.turnSystemOpen.trimEnd(),
        ...(input.reasoningSystemToken
          ? [input.reasoningSystemToken.trimEnd()]
          : []),
        `### system`,
      ]
    : [
        `### system`,
        ...(input.reasoningSystemToken
          ? [input.reasoningSystemToken.trimEnd()]
          : []),
      ];
  return [
    ...head,
    persona,
    ...(input.capabilities.platform === "win32"
      ? [``, WINDOWS_PLATFORM_HINT]
      : []),
    ``,
    `### rules`,
    nativeTools ? NATIVE_TOOLS_RULES_LINE : GRAMMAR_RULES_LINE,
    ``,
    `### skills`,
    skills,
    ``,
    `### tools`,
    `# common (full)`,
    commonBlock,
    ``,
    `# extras (one-line; use \`tool.view\` { name: "<tool>" } for full schema)`,
    extrasBlock,
    ...(outsideLine !== null ? [outsideLine] : []),
    ``,
    `### capabilities`,
    caps,
    ``,
    ...(integrationsBlock.length > 0
      ? [`### integrations`, integrationsBlock.join("\n"), ``]
      : []),
    ...(fusionActive
      ? [`### fusion`, buildFusionGuidance(input.fusion), ``]
      : []),
    `### instructions`,
    // The emission instructions are the one transport-dependent block.
    // Grammar links parse text-JSON (GBNF-constrained locally), so they
    // mandate the array literal; native links carry an OpenAI `tools`
    // payload, and repeating the text-JSON mandate there is a dual
    // mandate that drives models back to text emission (issue #285).
    // The `### tools` catalog above stays in both modes — see
    // `StablePrefixInput.toolTransport`.
    ...(nativeTools
      ? [
          `Call tools now, through the native function-calling interface (the \`tools\` your API request carries) — do NOT write tool-call JSON as text. The \`### tools\` catalog above is reference documentation for those same tools (tiers, examples, \`tool.view\`). For the final user-facing answer call \`reply\`, or answer in plain text.`,
          `PARALLEL: when you need multiple INDEPENDENT actions (e.g. read 3 different files, run 2 globs, look up 4 git logs), emit up to ${maxParallelToolCalls} tool calls in the SAME response — they run in parallel and cut wall time by ~Nx.`,
          `Emit a single tool call (no others alongside) when: it is \`reply\`/\`finish\`, may need approval (\`os.shell.run\`, \`os.fs.write\`, \`os.fs.edit\`, \`os.fs.trash\`, \`os.fs.patch\`, \`os.fs.restore\`, \`os.fs.move\`, \`os.fs.archive.extract\`, \`os.proc.kill\`, \`os.http.request\`, \`skill.run_script\`), or its args depend on a previous call's result.`,
        ]
      : [
          `Emit a JSON ARRAY of tool calls now. Always start with \`[\` and end with \`]\`, even for a single call. Use \`reply\` for natural-language answers to the user.`,
          `PARALLEL: when you need multiple INDEPENDENT actions (e.g. read 3 different files, run 2 globs, look up 4 git logs), put up to ${maxParallelToolCalls} calls in the SAME array — they run in parallel and cut wall time by ~Nx. Examples:`,
          `  - one call: [{"tool":"os.fs.read","args":{"path":"a.ts"}}]`,
          `  - parallel batch: [{"tool":"os.fs.read","args":{"path":"a.csv"}},{"tool":"os.fs.read","args":{"path":"b.csv"}},{"tool":"os.fs.read","args":{"path":"c.csv"}}]`,
          `  - reply: [{"tool":"reply","args":{"text":"..."}}]`,
          `Keep a call solo (length-1 array) when: it is \`reply\`/\`finish\`, may need approval (\`os.shell.run\`, \`os.fs.write\`, \`os.fs.edit\`, \`os.fs.trash\`, \`os.fs.patch\`, \`os.fs.restore\`, \`os.fs.move\`, \`os.fs.archive.extract\`, \`os.proc.kill\`, \`os.http.request\`, \`skill.run_script\`), or its args depend on a previous call's result.`,
        ]),
    ``,
  ].join("\n");
}

/**
 * Renders a frequent tool for the stable prefix: summary + `args` + optional examples.
 */
export function formatToolFrequent(descriptor: ToolDescriptor): string {
  const head = `- ${descriptor.name} — ${descriptor.summary}\n  args: ${descriptor.argsSchema}`;
  if (!descriptor.examples || descriptor.examples.length === 0) {
    return head;
  }
  const examples = descriptor.examples.map((ex) => `    - ${ex}`).join("\n");
  return `${head}\n  examples:\n${examples}`;
}

/** Renders a rare tool as a single-line manifest (no `args` in the prefix). */
function formatToolRare(descriptor: ToolDescriptor): string {
  return `- ${descriptor.name} — ${descriptor.summary}`;
}

/**
 * Renders a loaded tool block for the variable tail (`### loaded-tools`).
 * Uses the same shape as `formatToolFrequent` without `examples` if absent.
 */
export function formatToolForLoadedTail(
  name: string,
  summary: string,
  argsSchema: string,
  examples?: readonly string[],
): string {
  const head = `- ${name} — ${summary}\n  args: ${argsSchema}`;
  if (!examples || examples.length === 0) return head;
  const ex = examples.map((e) => `    - ${e}`).join("\n");
  return `${head}\n  examples:\n${ex}`;
}

export function formatCapabilities(caps: CapabilitiesSummary): string {
  return [
    `platform: ${caps.platform}/${caps.arch}`,
    `browser: ${caps.browserChannel}`,
    `working_dir: ${caps.workingDir}`,
    `clipboard: ${caps.hasClipboard ? "yes" : "no"}`,
    `wmctrl: ${caps.hasWmctrl ? "yes" : "no"}`,
    `notifications: ${caps.hasNotifications ? "yes" : "no"}`,
    ...(caps.emailAddress
      ? [`email: ${caps.emailAddress} (os.email.inbox / os.email.send)`]
      : []),
  ].join("\n");
}

```

## src/tools/os/shell/shell-result.ts

Source: https://github.com/AtomicBot-ai/atomic-agent/blob/fdc7813cb1b2d7a72781c957b211e03af5e02130/src/tools/os/shell/shell-result.ts

SHA-256: `4837f9bdc6eb65ef673036be1786bb90b2ad42e2080f06292ccf974b3ffa5b98`

```
import {
  compressToolResult,
  type CompressedToolResult,
  type CompressorOptions,
} from "../../../compressor/result-compressor.js";
import { getConfig } from "../../../config/index.js";
import type {
  CommandJobExit,
  CommandJobOutput,
} from "../../../sandbox/command-job.js";
import type { GuardVerdict } from "../shell-command-guard/index.js";
import {
  formatShellTimeoutNotice,
  type ResolvedShellTimeout,
} from "./shell-timeout.js";

/**
 * How an `os.shell.run` result is put together, whichever way the
 * command ended — at its exit, at an explicit timeout, detached at the
 * default one, or collected later by a `wait` / `kill`. One renderer so
 * a job's result reads exactly like a fresh run's.
 */

export const GOG_MAX_OUTPUT_BYTES = 4 * 1024 * 1024;
const GOG_COMPRESS_OPTIONS = {
  maxSummaryLength: 64_000,
  maxTailLines: 10_000,
} as const;

/**
 * What an ordinary (non-`gog`) command's output is compressed to on the
 * way in. The compressor runs inside the tool and its summary is what
 * is stored as the `tool_result` turn, so this is not a render budget —
 * whatever it drops is gone for good. Passing nothing here left every
 * shell command on the compressor's bare defaults (400 chars / 12
 * lines) while `os.fs.read`, `os.git.diff` and the archive tools all
 * passed 8–64 KB.
 *
 * `overflow` is `"tail"` for a command's own output: the end of it is
 * what answers the call — the `exit:` line, the test verdict, the last
 * error. The compressor's default cut takes the tail and then keeps its
 * *first* characters, which drops exactly that.
 *
 * Read per result rather than at module load so the env knobs apply to
 * a running agent; the defaults live in `ENV_DEFAULTS`
 * (`src/config/config-schema.ts`) and the bounds in `load-config.ts`.
 */
function shellCompressOptions(overflow: "head" | "tail"): CompressorOptions {
  const { shellToolResultCharCap, shellToolResultTailLines } =
    getConfig().agent;
  return {
    maxSummaryLength: shellToolResultCharCap,
    maxTailLines: shellToolResultTailLines,
    overflow,
  };
}

/** What a result says about the command, fixed when it was started. */
export interface ShellCommandFacts {
  cmd: string;
  /** The argv that ran (globs expanded on the direct-exec path). */
  args: string[];
  rawArgs: string[];
  cwd: string;
  /** Ran through the OS subshell rather than a direct exec. */
  shell: boolean;
  commandLine: string;
  /** A bare interpreter ran with nothing after it (F40) — said on the command line. */
  noArguments: boolean;
  /** `gog` output is kept far longer than any other command's. */
  gog: boolean;
  guard: GuardVerdict;
}

export interface ShellResultInput {
  facts: ShellCommandFacts;
  status: "ok" | "error";
  /** Lines said first, above the command line. */
  notices: readonly string[];
  /** The line under the command: `exit: 0`, `still running (job 3, …)`. */
  statusLine: string;
  body: string;
  /** Result-specific fields, placed between the command's and the guard's. */
  details: Record<string, unknown>;
  /**
   * Which end of the summary survives if it is over the char cap.
   * Default `"tail"`: the end of a command's output is what the call was
   * made for. A status report — a detached or killed job — passes
   * `"head"` instead, because its body is already a short pre-tailed
   * excerpt and the notice above the command line is the part that
   * matters.
   */
  overflow?: "head" | "tail";
}

export function renderShellResult(input: ShellResultInput): CompressedToolResult {
  const { facts } = input;
  const header = `$ ${facts.commandLine}${facts.noArguments ? " (ran with no arguments)" : ""}\n${input.statusLine}`;
  const output = [...input.notices, header, input.body]
    .filter((part) => part.length > 0)
    .join("\n");
  return compressToolResult(
    {
      tool: "os.shell.run",
      status: input.status,
      output,
      details: {
        cmd: facts.cmd,
        args: facts.args,
        rawArgs: facts.rawArgs,
        cwd: facts.cwd,
        shell: facts.shell,
        ...input.details,
        guardVerdict: facts.guard.action,
        guardRule: facts.guard.rule,
        guardReason: facts.guard.reason,
      },
    },
    facts.gog
      ? GOG_COMPRESS_OPTIONS
      : shellCompressOptions(input.overflow ?? "tail"),
  );
}

/** stdout and stderr, the non-empty ones, separated. */
export function joinShellOutput(output: CommandJobOutput): string {
  return [output.stdout, output.stderr]
    .filter((s) => s.trim().length > 0)
    .join("\n---\n");
}

/**
 * The last `count` non-blank lines, with a marker for what was left
 * out. The result compressor keeps only a short tail of the output; a
 * notice above a long output survives only when the body is already
 * short enough for the notice to be inside that tail.
 */
export function tailShellOutput(text: string, count: number): string {
  const lines = text.split("\n").filter((line) => line.trim().length > 0);
  if (lines.length <= count) return lines.join("\n");
  return `… [${lines.length - count} earlier lines]\n${lines.slice(-count).join("\n")}`;
}

export function formatExitStatus(exit: CommandJobExit): string {
  return `${exit.exitCode ?? "signal:" + exit.signal}`;
}

/** The result of a command that ran to its exit — fresh, or collected by a `wait`. */
export function renderShellExit(
  facts: ShellCommandFacts,
  exit: CommandJobExit,
  output: CommandJobOutput,
  extra: { notices?: readonly string[]; jobId?: number } = {},
): CompressedToolResult {
  return renderShellResult({
    facts,
    status: exit.exitCode === 0 ? "ok" : "error",
    notices: extra.notices ?? [],
    statusLine: `exit: ${formatExitStatus(exit)}`,
    body: joinShellOutput(output),
    details: {
      exitCode: exit.exitCode,
      signal: exit.signal,
      durationMs: exit.durationMs,
      timedOut: false,
      ...(extra.jobId === undefined ? {} : { jobId: extra.jobId }),
      truncated: output.truncated,
    },
  });
}

/**
 * The result of a command stopped at the model's own `timeoutMs`. Said
 * first, above the command line: which limit stopped it and what to
 * pass for a longer run.
 */
export function renderShellTimedOut(
  facts: ShellCommandFacts,
  exit: CommandJobExit,
  output: CommandJobOutput,
  timeout: ResolvedShellTimeout,
  notices: readonly string[] = [],
): CompressedToolResult {
  return renderShellResult({
    facts,
    status: "error",
    notices: [...notices, formatShellTimeoutNotice(timeout)],
    statusLine: `exit: ${formatExitStatus(exit)} (timed out)`,
    body: joinShellOutput(output),
    details: {
      exitCode: exit.exitCode,
      signal: exit.signal,
      durationMs: exit.durationMs,
      timedOut: true,
      timeoutMs: timeout.timeoutMs,
      source: timeout.source,
      truncated: output.truncated,
    },
  });
}

```
