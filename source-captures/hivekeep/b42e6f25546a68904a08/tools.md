# Hivekeep — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/server/services/tool-output-spill.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/services/tool-output-spill.ts

SHA-256: `c4df337541e91c51ff4c66b378c0b413c6c9e9fb476a377d388528390e7c75bd`

```
import { mkdirSync, writeFileSync, readdirSync, statSync, unlinkSync } from 'fs'
import { join } from 'path'
import { createHash } from 'crypto'
import type { Tool } from '@/server/tools/tool-helper'
import { config } from '@/server/config'
import { createLogger } from '@/server/logger'
import { countTokens } from '@/shared/token-estimator'

const log = createLogger('tool-output-spill')

/** Tools exempt from spilling (they already have offset/limit or produce small metadata) */
const SPILL_EXEMPT_TOOLS = new Set(['read_file'])

const SPILL_DIR_NAME = '.tool-outputs'

// ─── Core Spill ──────────────────────────────────────────────────────────────

interface SpillReference {
  __spilled: true
  toolName: string
  file: string
  sizeBytes: number
  lineCount: number
  preview: string
  hint: string
}

/**
 * Cut a preview out of the serialized result, bounded by BOTH the line count
 * and a hard character budget.
 *
 * The character bound is the one that matters: `JSON.stringify` escapes
 * newlines, so a result whose bulk is a single string (an email body, grep
 * output, shell stdout) serializes to a few enormous lines. Slicing by line
 * count alone then returns the whole payload, and a "spilled" output costs the
 * same context as an inline one. That is how a 109k-token email survived into
 * every subsequent turn.
 */
export function buildPreview(lines: string[], maxLines: number, maxChars: number): string {
  const byLines = lines.slice(0, Math.max(1, maxLines)).join('\n')
  if (maxChars <= 0 || byLines.length <= maxChars) return byLines
  const omitted = byLines.length - maxChars
  return `${byLines.slice(0, maxChars)}\n… [preview truncated, ${omitted.toLocaleString()} more characters — read the file for the rest]`
}

/**
 * Cap a tool result that is about to be replayed to the model.
 *
 * The keep-window cap in `buildMessageHistory` only runs when the NEXT turn
 * rebuilds history from the DB. Within the current turn, results are appended
 * raw and re-sent at every subsequent step, so one oversized result (a
 * spill-exempt `read_file`, anything the spill threshold let through) is paid
 * for again on each step. Same placeholder wording as the rebuild path, so the
 * model sees a consistent story across turns.
 */
export function capToolResultText(text: string, toolName: string, capTokens: number): string {
  if (capTokens <= 0) return text
  // ~4 chars/token; cheap guard before paying for a real token count.
  if (text.length <= capTokens * 4) return text
  const tokens = countTokens(text)
  if (tokens <= capTokens) return text
  return `[Tool result trimmed: ${toolName} returned ~${tokens.toLocaleString()} tokens, exceeding the ${capTokens.toLocaleString()}-token keep-window cap. Re-run the tool if you need the full output.]`
}

/**
 * If a tool result exceeds the configured byte threshold, save the full
 * serialized result to a temp file and return a compact reference with a
 * preview. Otherwise return the result unchanged.
 */
export function maybeSpillToolOutput(
  workspacePath: string,
  toolName: string,
  result: unknown,
): unknown {
  if (SPILL_EXEMPT_TOOLS.has(toolName)) return result

  const threshold = config.toolOutputs?.spillThreshold ?? 10000
  if (threshold <= 0) return result // spilling disabled

  let serialized: string
  try {
    serialized = JSON.stringify(result, null, 2)
  } catch {
    return result // not serializable — return as-is
  }

  const sizeBytes = Buffer.byteLength(serialized, 'utf-8')
  if (sizeBytes <= threshold) return result

  // Spill to file
  const spillDir = join(workspacePath, SPILL_DIR_NAME)
  mkdirSync(spillDir, { recursive: true })

  const hash = createHash('sha256').update(serialized).digest('hex').slice(0, 8)
  const filename = `tool-result-${Date.now()}-${hash}.txt`
  const filePath = join(spillDir, filename)

  try {
    writeFileSync(filePath, serialized, 'utf-8')
  } catch (err) {
    log.warn({ err, filePath }, 'Failed to write spilled tool output')
    return result // fallback to inline
  }

  const lines = serialized.split('\n')
  const lineCount = lines.length
  const previewLines = config.toolOutputs?.previewLines ?? 200
  const previewMaxChars = config.toolOutputs?.previewMaxChars ?? 4000
  const preview = buildPreview(lines, previewLines, previewMaxChars)
  const relativePath = `${SPILL_DIR_NAME}/${filename}`

  const ref: SpillReference = {
    __spilled: true,
    toolName,
    file: relativePath,
    sizeBytes,
    lineCount,
    preview,
    hint: `Full output saved to file. Use read_file("${relativePath}", offset=N, limit=M) to read specific sections.`,
  }

  log.debug({ toolName, sizeBytes, lineCount, file: relativePath }, 'Tool output spilled to file')

  return ref
}

// ─── Tool Wrapping ───────────────────────────────────────────────────────────

/**
 * Wrap all tools in a record so that large results are automatically
 * spilled to temp files. Exempt tools and tools without execute are
 * passed through unchanged.
 */
export function wrapToolsWithSpill(
  tools: Record<string, Tool<any, any>>,
  workspacePath: string,
): Record<string, Tool<any, any>> {
  const wrapped: Record<string, Tool<any, any>> = {}

  for (const [name, tool] of Object.entries(tools)) {
    if (!('execute' in tool) || typeof tool.execute !== 'function' || SPILL_EXEMPT_TOOLS.has(name)) {
      wrapped[name] = tool
      continue
    }

    const originalExecute = tool.execute
    wrapped[name] = {
      ...tool,
      execute: async (args: unknown, options: unknown) => {
        const result = await (originalExecute as Function)(args, options)
        return maybeSpillToolOutput(workspacePath, name, result)
      },
    }
  }

  return wrapped
}

// ─── Cleanup ─────────────────────────────────────────────────────────────────

/**
 * Delete spilled tool output files older than the configured TTL.
 * Scans all workspace subdirectories for `.tool-outputs/` dirs.
 * Returns the number of files deleted.
 */
export function cleanupSpilledOutputs(workspacesBaseDir: string): number {
  const ttlMs = (config.toolOutputs?.ttlHours ?? 24) * 60 * 60 * 1000
  if (ttlMs <= 0) return 0

  const now = Date.now()
  let deletedCount = 0

  let workspaces: string[]
  try {
    workspaces = readdirSync(workspacesBaseDir)
  } catch {
    return 0
  }

  for (const ws of workspaces) {
    const spillDir = join(workspacesBaseDir, ws, SPILL_DIR_NAME)
    let files: string[]
    try {
      files = readdirSync(spillDir)
    } catch {
      continue // no .tool-outputs dir
    }

    for (const file of files) {
      const filePath = join(spillDir, file)
      try {
        const stat = statSync(filePath)
        if (!stat.isFile()) continue
        if (now - stat.mtimeMs > ttlMs) {
          unlinkSync(filePath)
          deletedCount++
        }
      } catch {
        // skip inaccessible files
      }
    }
  }

  if (deletedCount > 0) {
    log.debug({ deletedCount }, 'Cleaned up spilled tool outputs')
  }

  return deletedCount
}

```

## src/server/tools/filesystem-tools.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/tools/filesystem-tools.ts

SHA-256: `586b90a1e5fdb1488c9ab3948e2858128f326af2358bede119c355f3493810c3`

```
import { tool } from '@/server/tools/tool-helper'
import { z } from 'zod'
import { resolve, relative, extname, basename } from 'path'
import { existsSync, statSync, readdirSync } from 'fs'
import { readFile, writeFile, mkdir } from 'fs/promises'
import { createLogger } from '@/server/logger'
import { noteReadFile, formatReadRange, recordReadPath, hasReadPath, recordGuardFire } from '@/server/services/tool-call-tracker'
import type { ToolRegistration } from '@/server/tools/types'
import { config } from '@/server/config'
import { resolveToolWorkspace } from '@/server/tools/workspace'
import { emitWorkspaceChangedForTool } from '@/server/services/workspace-files'

const log = createLogger('filesystem-tools')

const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10 MB
const MAX_LINES = 2000

// Default directories to skip when listing
const DEFAULT_IGNORE = new Set([
  'node_modules', '.git', '.svn', '.hg', '__pycache__',
  '.DS_Store', 'Thumbs.db', '.cache', 'dist', '.next',
  '.nuxt', '.output', 'coverage', '.nyc_output',
])

// Paths that are always blocked
const BLOCKED_PATHS = [
  '/etc/shadow', '/etc/passwd', '/etc/sudoers',
  '/root', '/proc', '/sys',
]

// Files that decrypt or ARE the platform's own state: with the encryption key
// plus the DB file, the whole vault is decryptable offline. Agents keep broad
// host access by design (trusted agents administer the machine), but never
// through these.
const SENSITIVE_PLATFORM_FILES = [
  resolve(config.dataDir, '.encryption-key'),
  resolve(config.db.path),
  resolve(config.db.path) + '-wal',
  resolve(config.db.path) + '-shm',
]

const EXTENSION_LANGUAGES: Record<string, string> = {
  '.ts': 'typescript', '.tsx': 'tsx', '.js': 'javascript', '.jsx': 'jsx',
  '.py': 'python', '.rb': 'ruby', '.go': 'go', '.rs': 'rust',
  '.java': 'java', '.kt': 'kotlin', '.swift': 'swift', '.cs': 'csharp',
  '.cpp': 'cpp', '.c': 'c', '.h': 'c', '.hpp': 'cpp',
  '.html': 'html', '.css': 'css', '.scss': 'scss', '.less': 'less',
  '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
  '.xml': 'xml', '.md': 'markdown', '.sql': 'sql', '.sh': 'bash',
  '.bash': 'bash', '.zsh': 'zsh', '.fish': 'fish', '.ps1': 'powershell',
  '.dockerfile': 'dockerfile', '.lua': 'lua', '.r': 'r', '.php': 'php',
  '.vue': 'vue', '.svelte': 'svelte', '.graphql': 'graphql',
  '.env': 'dotenv', '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini',
}

function detectLanguage(filePath: string): string | undefined {
  const ext = extname(filePath).toLowerCase()
  if (EXTENSION_LANGUAGES[ext]) return EXTENSION_LANGUAGES[ext]
  const name = basename(filePath).toLowerCase()
  if (name === 'dockerfile') return 'dockerfile'
  if (name === 'makefile') return 'makefile'
  if (name === '.gitignore' || name === '.dockerignore') return 'gitignore'
  return undefined
}

function isBinary(buffer: Buffer): boolean {
  // Check first 8KB for null bytes
  const check = buffer.subarray(0, 8192)
  for (let i = 0; i < check.length; i++) {
    if (check[i] === 0) return true
  }
  return false
}

export function isPathBlocked(absPath: string): boolean {
  for (const blocked of BLOCKED_PATHS) {
    if (absPath === blocked || absPath.startsWith(blocked + '/')) return true
  }
  if (SENSITIVE_PLATFORM_FILES.includes(absPath)) return true
  // Block SSH keys and the GPG keyring
  if (absPath.includes('/.ssh/') || absPath.includes('/.gnupg/')) return true
  return false
}

export function resolveAndValidate(inputPath: string, workspace: string): string {
  const absPath = resolve(workspace, inputPath)
  if (isPathBlocked(absPath)) {
    throw new Error(`Access denied: ${inputPath}`)
  }
  return absPath
}

// ── read_file ──────────────────────────────────────────────

export const readFileTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Read a text file or extract text from a PDF. **Use this instead of run_shell with cat/head/tail/sed.** For partial reads of large files, set `offset` (1-indexed start line) and `limit`. For searching content across files, use `grep` instead. **Parallel-safe**: multiple read_file calls in one assistant turn run concurrently.',
      inputSchema: z.object({
        path: z.string().describe('Relative to workspace or absolute'),
        offset: z.number().int().min(1).optional().describe('Start line (1-indexed)'),
        limit: z.number().int().min(1).max(MAX_LINES).optional().describe(`Default/max: ${MAX_LINES}`),
      }),
      execute: async ({ path: filePath, offset, limit }) => {
        const workspace = resolveToolWorkspace(ctx)
        const absPath = resolveAndValidate(filePath, workspace)

        try {
          const stat = statSync(absPath)
          if (!stat.isFile()) {
            return { success: false, error: `Not a file: ${filePath}` }
          }
          if (stat.size > MAX_FILE_SIZE) {
            return {
              success: false,
              error: `File too large: ${(stat.size / 1024 / 1024).toFixed(1)} MB (max ${MAX_FILE_SIZE / 1024 / 1024} MB)`,
            }
          }

          const buffer = await readFile(absPath)
          if (isBinary(buffer)) {
            // PDF: extract text instead of rejecting
            if (absPath.endsWith('.pdf')) {
              try {
                const pdfParse = (await import('pdf-parse') as any).default
                const pdf = await pdfParse(buffer)
                const text = pdf.text
                const allLines = text.split('\n')
                const totalLines = allLines.length
                const startLine = offset ?? 1
                const maxLines = limit ?? MAX_LINES
                const endLine = Math.min(startLine + maxLines - 1, totalLines)
                const selectedLines = allLines.slice(startLine - 1, endLine)
                const content = selectedLines.join('\n')

                log.info({ agentId: ctx.agentId, path: filePath, totalLines, startLine, endLine, pages: pdf.numpages }, 'PDF text extracted')

                const dup = noteReadFile(ctx.taskId, filePath, { offset, limit })
                recordReadPath(ctx.taskId, filePath)
                return {
                  success: true,
                  content,
                  path: filePath,
                  totalLines,
                  startLine,
                  endLine,
                  language: 'text',
                  note: `Extracted text from PDF (${pdf.numpages} pages)`,
                  ...(dup.previousRanges.length > 0
                    ? {
                        duplicate: true as const,
                        previousReadCount: dup.previousRanges.length,
                        previousRanges: dup.previousRanges.map(formatReadRange),
                        hint: `You already read this PDF ${dup.previousRanges.length} time(s) earlier in this task at line ${dup.previousRanges.map(formatReadRange).join(', ')}. The pages are still upstream in your context — re-scan your earlier turns before requesting another window. Re-read only if the file changed since.`,
                      }
                    : {}),
                }
              } catch (e) {
                return {
                  success: false,
                  error: `Failed to extract text from PDF: ${e instanceof Error ? e.message : String(e)}`,
                  fileSize: stat.size,
                }
              }
            }
            return {
              success: false,
              error: 'Binary file detected. Use run_shell to inspect binary files.',
              fileSize: stat.size,
            }
          }

          const fullContent = buffer.toString('utf-8')
          const allLines = fullContent.split('\n')
          const totalLines = allLines.length

          const startLine = offset ?? 1
          const maxLines = limit ?? MAX_LINES
          const endLine = Math.min(startLine + maxLines - 1, totalLines)
          const selectedLines = allLines.slice(startLine - 1, endLine)
          const content = selectedLines.join('\n')
          const language = detectLanguage(absPath)

          log.info({ agentId: ctx.agentId, path: filePath, totalLines, startLine, endLine }, 'File read')

          const dup = noteReadFile(ctx.taskId, filePath, { offset, limit })
          recordReadPath(ctx.taskId, filePath)
          return {
            success: true,
            path: filePath,
            content,
            totalLines,
            startLine,
            endLine,
            language: language ?? null,
            truncated: endLine < totalLines,
            ...(dup.previousRanges.length > 0
              ? {
                  duplicate: true as const,
                  previousReadCount: dup.previousRanges.length,
                  previousRanges: dup.previousRanges.map(formatReadRange),
                  hint: `You already read this file ${dup.previousRanges.length} time(s) earlier in this task at line ${dup.previousRanges.map(formatReadRange).join(', ')}. The content is still upstream in your context — re-scan your earlier turns before requesting another window. Only request a new range if you need lines outside the prior windows OR the file changed since.`,
                }
              : {}),
          }
        } catch (err: any) {
          if (err.code === 'ENOENT') {
            return { success: false, error: `File not found: ${filePath}` }
          }
          return { success: false, error: err.message }
        }
      },
    }),
}

// ── write_file ─────────────────────────────────────────────

export const writeFileTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  expandsSecrets: true,
  create: (ctx) =>
    tool({
      description:
        'Write content to a file. Creates if missing, overwrites if exists. Prefer edit_file or multi_edit for targeted changes to existing files.',
      inputSchema: z.object({
        path: z.string().describe('Relative to workspace or absolute'),
        content: z.string(),
        createDirectories: z.boolean().optional().describe('Default: true'),
      }),
      execute: async ({ path: filePath, content, createDirectories }) => {
        const workspace = resolveToolWorkspace(ctx)
        const absPath = resolveAndValidate(filePath, workspace)
        const shouldCreateDirs = createDirectories !== false

        let previousContent: string | null = null
        let created = true

        try {
          // Check if file exists and read previous content
          if (existsSync(absPath)) {
            const stat = statSync(absPath)
            if (!stat.isFile()) {
              return { success: false, error: `Not a file: ${filePath}` }
            }
            const buf = await readFile(absPath)
            if (!isBinary(buf)) {
              previousContent = buf.toString('utf-8')
            }
            created = false
          }

          // Create parent directories
          if (shouldCreateDirs) {
            const dir = resolve(absPath, '..')
            await mkdir(dir, { recursive: true })
          }

          await writeFile(absPath, content, 'utf-8')
          emitWorkspaceChangedForTool(ctx, absPath, created ? 'created' : 'modified')
          const lines = content.split('\n').length
          const bytes = Buffer.byteLength(content, 'utf-8')
          const language = detectLanguage(absPath)

          log.info({ agentId: ctx.agentId, path: filePath, bytes, created }, 'File written')

          return {
            success: true,
            path: filePath,
            bytesWritten: bytes,
            linesWritten: lines,
            created,
            language: language ?? null,
            previousContent,
          }
        } catch (err: any) {
          return { success: false, error: err.message }
        }
      },
    }),
}

// ── edit_file ──────────────────────────────────────────────

export const editFileTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  expandsSecrets: true,
  create: (ctx) =>
    tool({
      description:
        'Edit a file by replacing exact text. **You must `read_file` this path at least once earlier in the task** — edits without a prior read are refused (prevents hallucinated edits). By default oldText must match exactly once; set replaceAll=true to replace all occurrences. For multiple different edits to the same file, use multi_edit instead.',
      inputSchema: z.object({
        path: z.string().describe('Relative to workspace or absolute'),
        oldText: z.string().describe('Must match exactly including whitespace'),
        newText: z.string(),
        replaceAll: z
          .boolean()
          .optional()
          .describe('If true, replace ALL occurrences of oldText. Default: false'),
      }),
      execute: async ({ path: filePath, oldText, newText, replaceAll }) => {
        const workspace = resolveToolWorkspace(ctx)
        const absPath = resolveAndValidate(filePath, workspace)

        if (!hasReadPath(ctx.taskId, filePath)) {
          recordGuardFire(ctx.taskId, 'readBeforeEditRefusal')
          return {
            success: false,
            applied: false,
            error: `Refusing to edit \`${filePath}\` — you have not read this file in this task yet. Call read_file first, then retry the edit. This guard prevents hallucinated edits based on assumed content.`,
            path: filePath,
          }
        }

        try {
          if (!existsSync(absPath)) {
            return { success: false, error: `File not found: ${filePath}` }
          }

          const buf = await readFile(absPath)
          const content = buf.toString('utf-8')

          // Count occurrences
          const occurrences = content.split(oldText).length - 1
          if (occurrences === 0) {
            return {
              success: false,
              applied: false,
              error: 'oldText not found in file. Make sure it matches exactly (including whitespace and newlines).',
              path: filePath,
            }
          }
          if (!replaceAll && occurrences > 1) {
            return {
              success: false,
              applied: false,
              error: `oldText matches ${occurrences} locations. It must match exactly once. Use a larger context to disambiguate, or set replaceAll=true to replace all occurrences.`,
              path: filePath,
            }
          }

          // Apply the edit(s)
          const newContent = replaceAll
            ? content.split(oldText).join(newText)
            : content.replace(oldText, newText)
          await writeFile(absPath, newContent, 'utf-8')
          emitWorkspaceChangedForTool(ctx, absPath, 'modified')

          const language = detectLanguage(absPath)

          log.info(
            { agentId: ctx.agentId, path: filePath, replacementCount: replaceAll ? occurrences : 1 },
            'File edited',
          )

          // For replaceAll, skip per-edit context (too many locations)
          if (replaceAll && occurrences > 1) {
            return {
              success: true,
              applied: true,
              path: filePath,
              oldText,
              newText,
              replacementCount: occurrences,
              language: language ?? null,
            }
          }

          // Extract context lines for single replacement
          const lines = newContent.split('\n')
          const editStart = content.indexOf(oldText)
          const linesBefore = content.substring(0, editStart).split('\n')
          const editLineNum = linesBefore.length
          const contextStart = Math.max(0, editLineNum - 4)
          const newTextLines = newText.split('\n').length
          const contextEnd = Math.min(lines.length, editLineNum + newTextLines + 3)

          return {
            success: true,
            applied: true,
            path: filePath,
            oldText,
            newText,
            replacementCount: 1,
            language: language ?? null,
            editLine: editLineNum,
            contextBefore: lines.slice(contextStart, editLineNum - 1).join('\n') || undefined,
            contextAfter: lines.slice(editLineNum + newTextLines - 1, contextEnd).join('\n') || undefined,
          }
        } catch (err: any) {
          return { success: false, error: err.message }
        }
      },
    }),
}

// ── list_directory ─────────────────────────────────────────

interface DirEntry {
  name: string
  type: 'file' | 'directory'
  size?: number
  children?: DirEntry[]
}

function listDir(
  absPath: string,
  recursive: boolean,
  maxDepth: number,
  pattern: RegExp | null,
  currentDepth: number,
): DirEntry[] {
  const entries: DirEntry[] = []
  let dirEntries: any[]
  try {
    dirEntries = readdirSync(absPath, { withFileTypes: true }) as any[]
  } catch {
    return entries
  }

  for (const entry of dirEntries) {
    const name = String(entry.name)
    if (DEFAULT_IGNORE.has(name)) continue
    if (name.startsWith('.') && name !== '.env' && name !== '.gitignore') continue

    const isDir = entry.isDirectory()
    const isFile = entry.isFile()
    if (!isDir && !isFile) continue

    if (pattern && !pattern.test(name)) {
      // For directories, still recurse (the pattern filters files)
      if (!isDir) continue
    }

    const item: DirEntry = {
      name,
      type: isDir ? 'directory' : 'file',
    }

    if (isFile) {
      try {
        item.size = statSync(resolve(absPath, name)).size
      } catch { /* ignore */ }
    }

    if (isDir && recursive && currentDepth < maxDepth) {
      item.children = listDir(
        resolve(absPath, name),
        recursive,
        maxDepth,
        pattern,
        currentDepth + 1,
      )
    }

    entries.push(item)
  }

  // Sort: directories first, then alphabetically
  entries.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'directory' ? -1 : 1
    return a.name.localeCompare(b.name)
  })

  return entries
}

export const listDirectoryTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'List directory contents. Skips node_modules, .git, etc. by default. **Use this instead of run_shell with ls/find.** For searching file contents, use `grep` instead. **Parallel-safe**: multiple list_directory calls in one assistant turn run concurrently.',
      inputSchema: z.object({
        path: z.string().optional().describe('Defaults to workspace root'),
        recursive: z.boolean().optional().describe('Default: false'),
        maxDepth: z.number().int().min(1).max(10).optional().describe('Default: 3'),
        pattern: z.string().optional().describe('Glob pattern (e.g. "*.ts")'),
      }),
      execute: async ({ path: dirPath, recursive, maxDepth, pattern }) => {
        const workspace = resolveToolWorkspace(ctx)
        const absPath = dirPath ? resolveAndValidate(dirPath, workspace) : workspace

        try {
          if (!existsSync(absPath)) {
            return { success: false, error: `Directory not found: ${dirPath ?? '.'}` }
          }
          const stat = statSync(absPath)
          if (!stat.isDirectory()) {
            return { success: false, error: `Not a directory: ${dirPath ?? '.'}` }
          }

          // Convert simple glob to regex
          let patternRegex: RegExp | null = null
          if (pattern) {
            const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')
            patternRegex = new RegExp(`^${escaped}$`, 'i')
          }

          const entries = listDir(
            absPath,
            recursive ?? false,
            maxDepth ?? 3,
            patternRegex,
            0,
          )

          log.info({ agentId: ctx.agentId, path: dirPath ?? '.', entryCount: entries.length }, 'Directory listed')

          return {
            success: true,
            path: dirPath ?? '.',
            entries,
          }
        } catch (err: any) {
          return { success: false, error: err.message }
        }
      },
    }),
}

```

## src/server/tools/history-tools.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/tools/history-tools.ts

SHA-256: `e21906bb7f8069e68a72598e122217e148f0e2f486fe9d8f216eb1a8acefd014`

```
import { tool } from '@/server/tools/tool-helper'
import { z } from 'zod'
import { sqlite } from '@/server/db/index'
import { db } from '@/server/db/index'
import { eq, asc, desc } from 'drizzle-orm'
import { compactingSummaries } from '@/server/db/schema'
import { createLogger } from '@/server/logger'
import type { ToolRegistration } from '@/server/tools/types'

const log = createLogger('tools:history')

/**
 * search_history — keyword search across message history for an Agent.
 * Uses FTS5 keyword search with optional date range filtering and pagination.
 */
export const searchHistoryTool: ToolRegistration = {
  availability: ['main'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Keyword search in your message history. Optional date range + pagination. Returns totalCount. Result content is truncated to 500 chars — use read_message(id) for the full text or the surrounding conversation.',
      inputSchema: z.object({
        query: z.string().describe('Search keywords'),
        startDate: z.string().optional().describe('ISO date string for range start (e.g. "2026-01-15")'),
        endDate: z.string().optional().describe('ISO date string for range end (e.g. "2026-03-20")'),
        limit: z.number().int().min(1).max(30).optional().describe('Max results to return. Default: 10'),
        offset: z.number().int().min(0).optional().describe('Skip this many results for pagination. Default: 0'),
      }),
      execute: async ({ query, startDate, endDate, limit, offset }) => {
        log.debug({ agentId: ctx.agentId, query, startDate, endDate }, 'History search invoked')
        const maxResults = limit ?? 10
        const skip = offset ?? 0

        try {
          // Escape FTS5 special characters
          const ftsQuery = query
            .replace(/['"*()]/g, ' ')
            .split(/\s+/)
            .filter(Boolean)
            .map((term) => `"${term}"`)
            .join(' OR ')

          if (!ftsQuery) return { messages: [], totalCount: 0 }

          // Build date filter clause
          let dateFilter = ''
          const params: (string | number)[] = [ftsQuery, ctx.agentId]

          if (startDate) {
            dateFilter += ' AND m.created_at >= ?'
            params.push(new Date(startDate).getTime())
          }
          if (endDate) {
            dateFilter += ' AND m.created_at <= ?'
            params.push(new Date(endDate).getTime())
          }

          // Get total count first
          const countResult = sqlite
            .query<{ cnt: number }, (string | number)[]>(
              `SELECT COUNT(*) as cnt
               FROM messages_fts fts
               JOIN messages m ON m.rowid = fts.rowid
               WHERE messages_fts MATCH ? AND m.agent_id = ? AND m.is_redacted = 0${dateFilter}`,
            )
            .get(...params)

          const totalCount = countResult?.cnt ?? 0

          // Get paginated results
          const rows = sqlite
            .query<
              { id: string; role: string; content: string; source_type: string; created_at: number },
              (string | number)[]
            >(
              `SELECT m.id, m.role, m.content, m.source_type, m.created_at
               FROM messages_fts fts
               JOIN messages m ON m.rowid = fts.rowid
               WHERE messages_fts MATCH ? AND m.agent_id = ? AND m.is_redacted = 0${dateFilter}
               ORDER BY fts.rank
               LIMIT ? OFFSET ?`,
            )
            .all(...params, maxResults, skip)

          return {
            totalCount,
            messages: rows.map((r) => ({
              id: r.id,
              role: r.role,
              content: r.content.length > 500 ? r.content.slice(0, 500) + '...' : r.content,
              sourceType: r.source_type,
              createdAt: r.created_at,
            })),
          }
        } catch {
          return { messages: [], totalCount: 0, error: 'Search failed' }
        }
      },
    }),
}

/**
 * browse_history — view messages from a specific time period with pagination.
 */
export const browseHistoryTool: ToolRegistration = {
  availability: ['main'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Browse message history for a time range. Chronological order with pagination. totalCount = messages in range.',
      inputSchema: z.object({
        startDate: z.string().describe('ISO date string for range start (e.g. "2026-01-15")'),
        endDate: z.string().describe('ISO date string for range end (e.g. "2026-03-20")'),
        limit: z.number().int().min(1).max(50).optional().describe('Max messages to return. Default: 20'),
        offset: z.number().int().min(0).optional().describe('Skip this many messages for pagination. Default: 0'),
      }),
      execute: async ({ startDate, endDate, limit, offset }) => {
        log.debug({ agentId: ctx.agentId, startDate, endDate }, 'History browse invoked')
        const maxResults = limit ?? 20
        const skip = offset ?? 0

        try {
          const startMs = new Date(startDate).getTime()
          const endMs = new Date(endDate).getTime()

          // Get total count
          const countResult = sqlite
            .query<{ cnt: number }, [string, number, number]>(
              `SELECT COUNT(*) as cnt
               FROM messages
               WHERE agent_id = ? AND created_at >= ? AND created_at <= ?
                 AND is_redacted = 0 AND task_id IS NULL AND session_id IS NULL
                 AND source_type != 'compacting'`,
            )
            .get(ctx.agentId, startMs, endMs)

          const totalCount = countResult?.cnt ?? 0

          // Get paginated results in chronological order
          const rows = sqlite
            .query<
              { id: string; role: string; content: string; source_type: string; source_id: string | null; created_at: number },
              [string, number, number, number, number]
            >(
              `SELECT id, role, content, source_type, source_id, created_at
               FROM messages
               WHERE agent_id = ? AND created_at >= ? AND created_at <= ?
                 AND is_redacted = 0 AND task_id IS NULL AND session_id IS NULL
                 AND source_type != 'compacting'
               ORDER BY created_at ASC
               LIMIT ? OFFSET ?`,
            )
            .all(ctx.agentId, startMs, endMs, maxResults, skip)

          return {
            totalCount,
            showing: { from: skip + 1, to: skip + rows.length },
            messages: rows.map((r) => ({
              id: r.id,
              role: r.role,
              content: r.content.length > 500 ? r.content.slice(0, 500) + '...' : r.content,
              sourceType: r.source_type,
              createdAt: r.created_at,
            })),
          }
        } catch {
          return { messages: [], totalCount: 0, error: 'Browse failed' }
        }
      },
    }),
}

/**
 * read_message — read the FULL text of a single message by ID, optionally with a
 * window of surrounding messages (anchored view). Complements search_history,
 * whose hits are truncated to 500 chars and carry no surrounding context.
 */
export const readMessageTool: ToolRegistration = {
  availability: ['main'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Read the FULL text of a message by its ID (search_history truncates results to 500 chars). Optionally include the surrounding messages from the same conversation for context (anchored view).',
      inputSchema: z.object({
        messageId: z.string().describe('The message ID, e.g. from a search_history result'),
        contextBefore: z.number().int().min(0).max(20).optional().describe('Include this many preceding messages from the same conversation. Default: 0'),
        contextAfter: z.number().int().min(0).max(20).optional().describe('Include this many following messages from the same conversation. Default: 0'),
      }),
      execute: async ({ messageId, contextBefore, contextAfter }) => {
        const before = contextBefore ?? 0
        const after = contextAfter ?? 0
        log.debug({ agentId: ctx.agentId, messageId, before, after }, 'Read message invoked')

        try {
          const target = sqlite
            .query<
              { rowid: number; id: string; role: string; content: string | null; source_type: string; task_id: string | null; session_id: string | null; created_at: number; is_redacted: number; agent_id: string },
              [string]
            >(
              `SELECT rowid, id, role, content, source_type, task_id, session_id, created_at, is_redacted, agent_id
               FROM messages WHERE id = ?`,
            )
            .get(messageId)

          if (!target || target.agent_id !== ctx.agentId) return { error: 'Message not found' }
          if (target.is_redacted) return { error: 'Message is redacted and cannot be read' }

          const full = (r: { id: string; role: string; content: string | null; source_type: string; created_at: number }) => ({
            id: r.id,
            role: r.role,
            content: r.content ?? '',
            sourceType: r.source_type,
            createdAt: r.created_at,
          })
          // Surrounding messages are truncated (read_message them individually
          // for full text) so an anchored window stays a bounded payload.
          const windowed = (r: { id: string; role: string; content: string | null; source_type: string; created_at: number }) => {
            const c = r.content ?? ''
            return { ...full(r), content: c.length > 800 ? c.slice(0, 800) + '...' : c }
          }

          const result: {
            message: ReturnType<typeof full>
            context?: { before: ReturnType<typeof windowed>[]; after: ReturnType<typeof windowed>[] }
          } = { message: full(target) }

          if (before > 0 || after > 0) {
            // Anchor the window to the SAME conversation stream as the target
            // (agent + matching task_id/session_id bucket), ordered by rowid
            // (monotonic = chronological). Redacted + compaction rows excluded.
            const taskClause = target.task_id === null ? 'task_id IS NULL' : 'task_id = ?'
            const sessionClause = target.session_id === null ? 'session_id IS NULL' : 'session_id = ?'
            const bucket: (string | number)[] = []
            if (target.task_id !== null) bucket.push(target.task_id)
            if (target.session_id !== null) bucket.push(target.session_id)

            const cols = 'rowid, id, role, content, source_type, created_at'
            const where = `agent_id = ? AND is_redacted = 0 AND source_type != 'compacting' AND ${taskClause} AND ${sessionClause}`

            const beforeRows = before > 0
              ? sqlite
                  .query<{ id: string; role: string; content: string | null; source_type: string; created_at: number }, (string | number)[]>(
                    `SELECT ${cols} FROM messages WHERE ${where} AND rowid < ? ORDER BY rowid DESC LIMIT ?`,
                  )
                  .all(ctx.agentId, ...bucket, target.rowid, before)
                  .reverse()
              : []

            const afterRows = after > 0
              ? sqlite
                  .query<{ id: string; role: string; content: string | null; source_type: string; created_at: number }, (string | number)[]>(
                    `SELECT ${cols} FROM messages WHERE ${where} AND rowid > ? ORDER BY rowid ASC LIMIT ?`,
                  )
                  .all(ctx.agentId, ...bucket, target.rowid, after)
              : []

            result.context = { before: beforeRows.map(windowed), after: afterRows.map(windowed) }
          }

          return result
        } catch {
          return { error: 'Failed to read message' }
        }
      },
    }),
}

/**
 * list_summaries — list all compacting summaries (in-context and archived) with metadata.
 */
export const listSummariesTool: ToolRegistration = {
  availability: ['main'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'List all conversation summaries (active in context + archived). Shows date range, depth, in-context status. Use read_summary for full text.',
      inputSchema: z.object({
        includeArchived: z.boolean().optional().describe('Include archived/merged summaries. Default: false'),
      }),
      execute: async ({ includeArchived }) => {
        log.debug({ agentId: ctx.agentId, includeArchived }, 'List summaries invoked')

        try {
          let query = db
            .select({
              id: compactingSummaries.id,
              firstMessageAt: compactingSummaries.firstMessageAt,
              lastMessageAt: compactingSummaries.lastMessageAt,
              messageCount: compactingSummaries.messageCount,
              tokenEstimate: compactingSummaries.tokenEstimate,
              isInContext: compactingSummaries.isInContext,
              depth: compactingSummaries.depth,
              createdAt: compactingSummaries.createdAt,
            })
            .from(compactingSummaries)
            .where(eq(compactingSummaries.agentId, ctx.agentId))
            .orderBy(asc(compactingSummaries.lastMessageAt))

          const allSummaries = await query.all()

          const filtered = includeArchived
            ? allSummaries
            : allSummaries.filter((s) => s.isInContext)

          return {
            totalCount: filtered.length,
            summaries: filtered.map((s) => ({
              id: s.id,
              firstMessageAt: s.firstMessageAt.toISOString(),
              lastMessageAt: s.lastMessageAt.toISOString(),
              messageCount: s.messageCount,
              tokenEstimate: s.tokenEstimate,
              isInContext: s.isInContext,
              depth: s.depth,
              depthLabel: (s.depth ?? 0) === 0 ? 'detailed' : 'compressed',
              createdAt: s.createdAt.toISOString(),
            })),
          }
        } catch {
          return { summaries: [], totalCount: 0, error: 'Failed to list summaries' }
        }
      },
    }),
}

/**
 * read_summary — read the full text of a specific summary by ID.
 */
export const readSummaryTool: ToolRegistration = {
  availability: ['main'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Read the full text of a conversation summary by its ID. Use list_summaries first to find available summary IDs.',
      inputSchema: z.object({
        summaryId: z.string().describe('The ID of the summary to read'),
      }),
      execute: async ({ summaryId }) => {
        log.debug({ agentId: ctx.agentId, summaryId }, 'Read summary invoked')

        try {
          const summary = await db
            .select()
            .from(compactingSummaries)
            .where(eq(compactingSummaries.id, summaryId))
            .get()

          if (!summary) {
            return { error: 'Summary not found' }
          }

          if (summary.agentId !== ctx.agentId) {
            return { error: 'Summary belongs to another Agent' }
          }

          return {
            id: summary.id,
            summary: summary.summary,
            firstMessageAt: summary.firstMessageAt.toISOString(),
            lastMessageAt: summary.lastMessageAt.toISOString(),
            messageCount: summary.messageCount,
            isInContext: summary.isInContext,
            depth: summary.depth,
            depthLabel: (summary.depth ?? 0) === 0 ? 'detailed' : 'compressed',
          }
        } catch {
          return { error: 'Failed to read summary' }
        }
      },
    }),
}

```

## src/server/tools/register.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/tools/register.ts

SHA-256: `c2277ef5385863063b8ac7da9415d9fa967b23940221f6a58ccb5f51a828f469`

```
import { createLogger } from '@/server/logger'
import { toolRegistry } from '@/server/tools/index'
import {
  browseUrlTool,
  extractLinksTool,
  screenshotUrlTool,
} from '@/server/tools/browse-tools'
import {
  listSearchProvidersTool,
  webSearchTool,
} from '@/server/tools/search-tools'
import {
  listEmailAccountsTool,
  listEmailsTool,
  readEmailTool,
  searchEmailsTool,
  sendEmailTool,
  downloadEmailAttachmentTool,
} from '@/server/tools/email-tools'
import {
  describeTriggerConditionsTool,
  listEmailFoldersTool,
  createAccountTriggerTool,
  listAccountTriggersTool,
  updateAccountTriggerTool,
  deleteAccountTriggerTool,
} from '@/server/tools/account-trigger-tools'
import {
  listAddressBooksTool,
  listAddressBookContactsTool,
  getAddressBookContactTool,
  searchAddressBookTool,
} from '@/server/tools/address-book-tools'
import {
  listCalendarAccountsTool,
  listCalendarsTool,
  listEventsTool,
  getEventTool,
  createEventTool,
  updateEventTool,
  deleteEventTool,
} from '@/server/tools/calendar-tools'
import {
  listTtsProvidersTool,
  listVoicesTool,
  textToSpeechTool,
  listSttProvidersTool,
  listSttModelsTool,
  transcribeAudioTool,
} from '@/server/tools/voice-tools'
import {
  browserOpenSessionTool,
  browserCloseSessionTool,
  browserListSessionsTool,
  browserNavigateTool,
  browserClickTool,
  browserTypeTool,
  browserSelectTool,
  browserPressKeyTool,
  browserScrollTool,
  browserWaitForTool,
  browserScreenshotTool,
  browserSetCookiesTool,
  browserGetCookiesTool,
  browserClearCookiesTool,
  browserRequestHumanTool,
  browserSaveStateTool,
  browserListStatesTool,
  browserDeleteStateTool,
} from '@/server/tools/browser-session-tools'
import {
  getContactTool,
  searchContactsTool,
  createContactTool,
  updateContactTool,
  deleteContactTool,
  setContactNoteTool,
  findContactByIdentifierTool,
} from '@/server/tools/contact-tools'
import {
  recallTool,
  memorizeTool,
  updateMemoryTool,
  forgetTool,
  listMemoriesTool,
  reviewMemoriesTool,
  editProfileTool,
} from '@/server/tools/memory-tools'
import { searchHistoryTool, browseHistoryTool, readMessageTool, listSummariesTool, readSummaryTool } from '@/server/tools/history-tools'
import {
  getSecretTool,
  redactSecretLeakTool,
  revealSecretTool,
  createSecretTool,
  updateSecretTool,
  deleteSecretTool,
  searchSecretsTool,
  getVaultEntryTool,
  createVaultEntryTool,
  createVaultTypeTool,
  getVaultAttachmentTool,
} from '@/server/tools/vault-tools'
import {
  spawnSelfTool,
  spawnAgentTool,
  respondToTaskTool,
  cancelTaskTool,
  listTasksTool,
  listActiveQueuesTool,
  getTaskDetailTool,
  getTaskMessagesTool,
} from '@/server/tools/task-tools'
import {
  reportToParentTool,
  updateTaskStatusTool,
  requestInputTool,
} from '@/server/tools/subtask-tools'
import { scoutTool } from '@/server/tools/scout-tool'
import { promptHumanTool } from '@/server/tools/human-prompt-tools'
import { requestToolAccessTool } from '@/server/tools/tool-access-tools'
import { notifyTool } from '@/server/tools/notify-tool'
import {
  sendMessageTool,
  replyTool,
  listAgentsTool,
} from '@/server/tools/inter-agent-tools'
import {
  createCronTool,
  updateCronTool,
  deleteCronTool,
  listCronsTool,
  getCronJournalTool,
  triggerCronTool,
} from '@/server/tools/cron-tools'
import {
  createCustomToolTool,
  writeCustomToolFileTool,
  runCustomToolSetupTool,
  testCustomToolTool,
  updateCustomToolTool,
  deleteCustomToolTool,
  listCustomToolsTool,
  createToolDomainTool,
  listToolDomainsTool,
  updateToolDomainTool,
  deleteToolDomainTool,
} from '@/server/tools/custom-tool-tools'
import { getCustomToolDocsTool } from '@/server/tools/custom-tool-docs'
import { generateImageTool, listImageModelsTool, describeImageModelTool } from '@/server/tools/image-tools'
import { listProvidersTool, listModelsTool } from '@/server/tools/provider-tools'
import {
  describeProviderConfigTool,
  listProviderTypesTool,
  testProviderTool,
  enableProviderCapabilityTool,
  setDefaultProviderTool,
  setDefaultModelTool,
  getDefaultModelsTool,
  getGlobalPromptTool,
  setGlobalPromptTool,
  getAvatarStyleTool,
  setAvatarStyleTool,
  setAvatarSubjectTool,
  listAvatarPresetsTool,
  setAvatarBaseEnabledTool,
  generateAvatarBaseTool,
  resetAvatarBaseTool,
  testChannelTool,
} from '@/server/tools/config-tools'
import { requestProviderSetupTool, requestChannelSetupTool, promptSecretTool } from '@/server/tools/secure-input-tools'
import { runShellTool } from '@/server/tools/shell-tools'
import {
  addMcpServerTool,
  updateMcpServerTool,
  removeMcpServerTool,
  listMcpServersTool,
} from '@/server/tools/mcp-tools'
import {
  storeFileTool,
  getStoredFileTool,
  listStoredFilesTool,
  searchStoredFilesTool,
  updateStoredFileTool,
  deleteStoredFileTool,
  downloadStoredFileTool,
} from '@/server/tools/file-storage-tools'
import {
  createAgentTool,
  updateAgentTool,
  deleteAgentTool,
  getAgentDetailsTool,
} from '@/server/tools/agent-management-tools'
import {
  listToolsTool,
  listToolboxesTool,
  createToolboxTool,
  updateToolboxTool,
  deleteToolboxTool,
} from '@/server/tools/toolbox-tools'
import {
  createWebhookTool,
  updateWebhookTool,
  deleteWebhookTool,
  listWebhooksTool,
} from '@/server/tools/webhook-tools'
import {
  listChannelsTool,
  listChannelConversationsTool,
  listEndpointsTool,
  sendChannelMessageTool,
  sendToContactTool,
  createChannelTool,
  updateChannelTool,
  deleteChannelTool,
  activateChannelTool,
  deactivateChannelTool,
  transferChannelTool,
} from '@/server/tools/channel-tools'
import { getPlatformLogsTool, getPlatformConfigTool, listPlatformConfigOptionsTool, updatePlatformConfigTool, restartPlatformTool } from '@/server/tools/platform-tools'
import { getSystemInfoTool } from '@/server/tools/system-info-tools'
import { getSetupHealthTool } from '@/server/tools/health-tools'
import { httpRequestTool } from '@/server/tools/http-request-tools'
import { executeSqlTool } from '@/server/tools/database-tools'
import {
  listUsersTool,
  getUserTool,
  createInvitationTool,
} from '@/server/tools/user-tools'
import {
  wakeMeInTool,
  wakeMeEveryTool,
  cancelWakeupTool,
  listWakeupsTool,
} from '@/server/tools/wakeup-tools'
import {
  createMiniAppTool,
  updateMiniAppTool,
  deleteMiniAppTool,
  listMiniAppsTool,
  writeMiniAppFileTool,
  readMiniAppFileTool,
  deleteMiniAppFileTool,
  listMiniAppFilesTool,
  getMiniAppStorageTool,
  setMiniAppStorageTool,
  deleteMiniAppStorageTool,
  listMiniAppStorageTool,
  clearMiniAppStorageTool,
  createMiniAppSnapshotTool,
  listMiniAppSnapshotsTool,
  rollbackMiniAppTool,
  generateMiniAppIconTool,
  getMiniAppConsoleTool,
  getMiniAppBackendStatusTool,
  reloadMiniAppTool,
  editMiniAppFileTool,
  multiEditMiniAppFileTool,
  setMiniAppMaintainerTool,
} from '@/server/tools/mini-app-tools'
import { getMiniAppTemplatesTool } from '@/server/tools/mini-app-templates'
import { getMiniAppDocsTool } from '@/server/tools/mini-app-docs'
import { browseMiniAppsTool } from '@/server/tools/mini-app-gallery'
import {
  saveRunLearningTool,
  deleteRunLearningTool,
} from '@/server/tools/cron-learning-tools'
import { attachFileTool } from '@/server/tools/attach-file-tool'
import {
  readFileTool,
  writeFileTool,
  editFileTool,
  listDirectoryTool,
} from '@/server/tools/filesystem-tools'
import { grepTool } from '@/server/tools/grep-tools'
import { multiEditTool } from '@/server/tools/multi-edit-tools'
import { thinkTool } from '@/server/tools/think-tool'
import { taskTodosTool } from '@/server/tools/task-todos-tool'
const log = createLogger('tools')

/**
 * Register all native tools in the tool registry.
 * Called once at server startup.
 *
 * Tools from later phases (tasks, inter-agent, etc.) will be
 * registered here as they are implemented.
 */
export function registerAllTools(): void {
  // Web browsing — read-only one-shot tools
  toolRegistry.register('browse_url', browseUrlTool, 'browse')
  toolRegistry.register('extract_links', extractLinksTool, 'browse')
  toolRegistry.register('screenshot_url', screenshotUrlTool, 'browse')

  // Search tools — discovery + action. Use browse_url for follow-up fetch.
  toolRegistry.register('list_search_providers', listSearchProvidersTool, 'search')
  toolRegistry.register('web_search', webSearchTool, 'search')

  // Email tools — read/search/send through a slug-resolved email account.
  toolRegistry.register('list_email_accounts', listEmailAccountsTool, 'email')
  toolRegistry.register('list_emails', listEmailsTool, 'email')
  toolRegistry.register('read_email', readEmailTool, 'email')
  toolRegistry.register('search_emails', searchEmailsTool, 'email')
  toolRegistry.register('send_email', sendEmailTool, 'email')
  toolRegistry.register('download_email_attachment', downloadEmailAttachmentTool, 'email')
  // Account triggers — automate Agent reactions to incoming email.
  toolRegistry.register('describe_trigger_conditions', describeTriggerConditionsTool, 'email')
  toolRegistry.register('list_email_folders', listEmailFoldersTool, 'email')
  toolRegistry.register('create_account_trigger', createAccountTriggerTool, 'email')
  toolRegistry.register('list_account_triggers', listAccountTriggersTool, 'email')
  toolRegistry.register('update_account_trigger', updateAccountTriggerTool, 'email')
  toolRegistry.register('delete_account_trigger', deleteAccountTriggerTool, 'email')

  // Address-book tools — read-only EXTERNAL contacts (iCloud, …), distinct from
  // Hivekeep's own contacts CRM. Resolved through a slug-based account.
  toolRegistry.register('list_address_books', listAddressBooksTool, 'contacts')
  toolRegistry.register('list_address_book_contacts', listAddressBookContactsTool, 'contacts')
  toolRegistry.register('get_address_book_contact', getAddressBookContactTool, 'contacts')
  toolRegistry.register('search_address_book', searchAddressBookTool, 'contacts')

  // Calendar tools — read + write events on a slug-resolved calendar account.
  toolRegistry.register('list_calendar_accounts', listCalendarAccountsTool, 'calendar')
  toolRegistry.register('list_calendars', listCalendarsTool, 'calendar')
  toolRegistry.register('list_events', listEventsTool, 'calendar')
  toolRegistry.register('get_event', getEventTool, 'calendar')
  toolRegistry.register('create_event', createEventTool, 'calendar')
  toolRegistry.register('update_event', updateEventTool, 'calendar')
  toolRegistry.register('delete_event', deleteEventTool, 'calendar')

  // Voice tools — TTS + STT discovery and actions. Audio bytes flow
  // through the messages-attachment files table (same path as
  // generate_image), never as base64 in the LLM context.
  toolRegistry.register('list_tts_providers', listTtsProvidersTool, 'voice')
  toolRegistry.register('list_voices', listVoicesTool, 'voice')
  toolRegistry.register('text_to_speech', textToSpeechTool, 'voice')
  toolRegistry.register('list_stt_providers', listSttProvidersTool, 'voice')
  toolRegistry.register('list_stt_models', listSttModelsTool, 'voice')
  toolRegistry.register('transcribe_audio', transcribeAudioTool, 'voice')

  // Web browsing — stateful sessions (defaultDisabled: granted only when a toolbox lists them)
  toolRegistry.register('browser_open_session', browserOpenSessionTool, 'browse')
  toolRegistry.register('browser_close_session', browserCloseSessionTool, 'browse')
  toolRegistry.register('browser_list_sessions', browserListSessionsTool, 'browse')
  toolRegistry.register('browser_navigate', browserNavigateTool, 'browse')
  toolRegistry.register('browser_click', browserClickTool, 'browse')
  toolRegistry.register('browser_type', browserTypeTool, 'browse')
  toolRegistry.register('browser_select', browserSelectTool, 'browse')
  toolRegistry.register('browser_press_key', browserPressKeyTool, 'browse')
  toolRegistry.register('browser_scroll', browserScrollTool, 'browse')
  toolRegistry.register('browser_wait_for', browserWaitForTool, 'browse')
  toolRegistry.register('browser_screenshot', browserScreenshotTool, 'browse')
  toolRegistry.register('browser_set_cookies', browserSetCookiesTool, 'browse')
  toolRegistry.register('browser_get_cookies', browserGetCookiesTool, 'browse')
  toolRegistry.register('browser_clear_cookies', browserClearCookiesTool, 'browse')
  toolRegistry.register('browser_request_human', browserRequestHumanTool, 'browse')
  toolRegistry.register('browser_save_state', browserSaveStateTool, 'browse')
  toolRegistry.register('browser_list_states', browserListStatesTool, 'browse')
  toolRegistry.register('browser_delete_state', browserDeleteStateTool, 'browse')

  // Phase 11: Contact tools
  toolRegistry.register('get_contact', getContactTool, 'contacts')
  toolRegistry.register('search_contacts', searchContactsTool, 'contacts')
  toolRegistry.register('create_contact', createContactTool, 'contacts')
  toolRegistry.register('update_contact', updateContactTool, 'contacts')
  toolRegistry.register('delete_contact', deleteContactTool, 'contacts')
  toolRegistry.register('set_contact_note', setContactNoteTool, 'contacts')
  toolRegistry.register('find_contact_by_identifier', findContactByIdentifierTool, 'contacts')

  // Phase 12: Memory tools
  toolRegistry.register('recall', recallTool, 'memory')
  toolRegistry.register('memorize', memorizeTool, 'memory')
  toolRegistry.register('update_memory', updateMemoryTool, 'memory')
  toolRegistry.register('forget', forgetTool, 'memory')
  toolRegistry.register('list_memories', listMemoriesTool, 'memory')
  toolRegistry.register('review_memories', reviewMemoriesTool, 'memory')
  toolRegistry.register('edit_profile', editProfileTool, 'memory')

  // Phase 12: History tools
  toolRegistry.register('search_history', searchHistoryTool, 'memory')
  toolRegistry.register('browse_history', browseHistoryTool, 'memory')
  toolRegistry.register('read_message', readMessageTool, 'memory')
  toolRegistry.register('list_summaries', listSummariesTool, 'memory')
  toolRegistry.register('read_summary', readSummaryTool, 'memory')

  // Phase 14: Vault tools
  toolRegistry.register('get_secret', getSecretTool, 'vault')
  toolRegistry.register('redact_secret_leak', redactSecretLeakTool, 'vault')
  toolRegistry.register('reveal_secret', revealSecretTool, 'vault')
  toolRegistry.register('create_secret', createSecretTool, 'vault')
  toolRegistry.register('update_secret', updateSecretTool, 'vault')
  toolRegistry.register('delete_secret', deleteSecretTool, 'vault')
  toolRegistry.register('search_secrets', searchSecretsTool, 'vault')
  toolRegistry.register('get_vault_entry', getVaultEntryTool, 'vault')
  toolRegistry.register('create_vault_entry', createVaultEntryTool, 'vault')
  toolRegistry.register('create_vault_type', createVaultTypeTool, 'vault')
  toolRegistry.register('get_vault_attachment', getVaultAttachmentTool, 'vault')

  // Phase 15: Task tools (parent — main only)
  toolRegistry.register('spawn_self', spawnSelfTool, 'tasks')
  toolRegistry.register('spawn_agent', spawnAgentTool, 'tasks')
  toolRegistry.register('respond_to_task', respondToTaskTool, 'tasks')
  toolRegistry.register('cancel_task', cancelTaskTool, 'tasks')
  toolRegistry.register('list_tasks', listTasksTool, 'tasks')
  toolRegistry.register('list_active_queues', listActiveQueuesTool, 'tasks')
  toolRegistry.register('get_task_detail', getTaskDetailTool, 'tasks')
  toolRegistry.register('get_task_messages', getTaskMessagesTool, 'tasks')

  // Scout: cheap read-only delegation (main + sub-agent). Spawns an await child
  // on the scout model with the read-only 'scout' toolbox and blocks for its
  // digest. The 'scout' toolbox excludes scout/spawn tools → scouts are leaves.
  toolRegistry.register('scout', scoutTool, 'tasks')

  // Phase 15: Sub-Agent tools (sub-agent only)
  toolRegistry.register('report_to_parent', reportToParentTool, 'tasks')
  toolRegistry.register('update_task_status', updateTaskStatusTool, 'tasks')
  toolRegistry.register('request_input', requestInputTool, 'tasks')

  // Cron learning tools (sub-agent only, active during cron tasks)
  toolRegistry.register('save_run_learning', saveRunLearningTool, 'tasks')
  toolRegistry.register('delete_run_learning', deleteRunLearningTool, 'tasks')

  // Human-in-the-loop (main + sub-agent)
  toolRegistry.register('prompt_human', promptHumanTool, 'tasks')
  toolRegistry.register('notify', notifyTool, 'tasks')

  // Phase 16: Inter-Agent tools (main only)
  toolRegistry.register('send_message', sendMessageTool, 'inter-agent')
  toolRegistry.register('reply', replyTool, 'inter-agent')
  toolRegistry.register('list_kins', listAgentsTool, 'inter-agent')

  // Phase 17: Cron tools (main only)
  toolRegistry.register('create_cron', createCronTool, 'crons')
  toolRegistry.register('update_cron', updateCronTool, 'crons')
  toolRegistry.register('delete_cron', deleteCronTool, 'crons')
  toolRegistry.register('list_crons', listCronsTool, 'crons')
  toolRegistry.register('get_cron_journal', getCronJournalTool, 'crons')
  toolRegistry.register('trigger_cron', triggerCronTool, 'crons')

  // Custom tools (GLOBAL, first-class). Authoring/admin tools are main-only;
  // the resulting tools are exposed separately as `custom_<slug>` (resolved by
  // services/custom-tools.ts, MCP-style — not registered here).
  toolRegistry.register('get_custom_tool_docs', getCustomToolDocsTool, 'custom')
  toolRegistry.register('create_custom_tool', createCustomToolTool, 'custom')
  toolRegistry.register('write_custom_tool_file', writeCustomToolFileTool, 'custom')
  toolRegistry.register('run_custom_tool_setup', runCustomToolSetupTool, 'custom')
  toolRegistry.register('test_custom_tool', testCustomToolTool, 'custom')
  toolRegistry.register('update_custom_tool', updateCustomToolTool, 'custom')
  toolRegistry.register('delete_custom_tool', deleteCustomToolTool, 'custom')
  toolRegistry.register('list_custom_tools', listCustomToolsTool, 'custom')
  toolRegistry.register('create_tool_domain', createToolDomainTool, 'custom')
  toolRegistry.register('list_tool_domains', listToolDomainsTool, 'custom')
  toolRegistry.register('update_tool_domain', updateToolDomainTool, 'custom')
  toolRegistry.register('delete_tool_domain', deleteToolDomainTool, 'custom')

  // Phase 21: Image tools
  toolRegistry.register('generate_image', generateImageTool, 'images')
  toolRegistry.register('list_image_models', listImageModelsTool, 'images')
  toolRegistry.register('describe_image_model', describeImageModelTool, 'images')

  // Provider & model discovery tools (main + sub-agent)
  toolRegistry.register('list_providers', listProvidersTool, 'system')
  toolRegistry.register('list_models', listModelsTool, 'system')

  // Platform configuration tools (configurator Agent / admin) — provider config
  // discovery + capability/default management + global prompt. Mutations are
  // admin-only (enforced inside each tool).
  toolRegistry.register('describe_provider_config', describeProviderConfigTool, 'system')
  toolRegistry.register('list_provider_types', listProviderTypesTool, 'system')
  toolRegistry.register('test_provider', testProviderTool, 'system')
  toolRegistry.register('enable_provider_capability', enableProviderCapabilityTool, 'system')
  toolRegistry.register('set_default_provider', setDefaultProviderTool, 'system')
  toolRegistry.register('set_default_model', setDefaultModelTool, 'system')
  toolRegistry.register('get_default_models', getDefaultModelsTool, 'system')
  toolRegistry.register('get_global_prompt', getGlobalPromptTool, 'system')
  toolRegistry.register('set_global_prompt', setGlobalPromptTool, 'system')
  toolRegistry.register('get_avatar_style', getAvatarStyleTool, 'system')
  toolRegistry.register('set_avatar_style', setAvatarStyleTool, 'system')
  toolRegistry.register('set_avatar_subject', setAvatarSubjectTool, 'system')
  toolRegistry.register('list_avatar_presets', listAvatarPresetsTool, 'system')
  toolRegistry.register('set_avatar_base_enabled', setAvatarBaseEnabledTool, 'system')
  toolRegistry.register('generate_avatar_base', generateAvatarBaseTool, 'system')
  toolRegistry.register('reset_avatar_base', resetAvatarBaseTool, 'system')
  toolRegistry.register('test_channel', testChannelTool, 'system')

  // Secure-input tools (configurator Agent) — request a secret via UI popup; the
  // value goes straight to the vault / encrypted provider config, never to the LLM.
  toolRegistry.register('request_provider_setup', requestProviderSetupTool, 'system')
  toolRegistry.register('request_channel_setup', requestChannelSetupTool, 'system')
  toolRegistry.register('prompt_secret', promptSecretTool, 'system')

  // Phase 18: MCP management tools (main only)
  toolRegistry.register('add_mcp_server', addMcpServerTool, 'mcp')
  toolRegistry.register('update_mcp_server', updateMcpServerTool, 'mcp')
  toolRegistry.register('remove_mcp_server', removeMcpServerTool, 'mcp')
  toolRegistry.register('list_mcp_servers', listMcpServersTool, 'mcp')

  // Shell execution (main + sub-agent)
  toolRegistry.register('run_shell', runShellTool, 'shell')

  // File storage tools (main only)
  toolRegistry.register('store_file', storeFileTool, 'file-storage')
  toolRegistry.register('get_stored_file', getStoredFileTool, 'file-storage')
  toolRegistry.register('download_stored_file', downloadStoredFileTool, 'file-storage')
  toolRegistry.register('list_stored_files', listStoredFilesTool, 'file-storage')
  toolRegistry.register('search_stored_files', searchStoredFilesTool, 'file-storage')
  toolRegistry.register('update_stored_file', updateStoredFileTool, 'file-storage')
  toolRegistry.register('delete_stored_file', deleteStoredFileTool, 'file-storage')

  // Agent management tools (main only, opt-in required)
  toolRegistry.register('create_agent', createAgentTool, 'agent-management')
  toolRegistry.register('update_agent', updateAgentTool, 'agent-management')
  toolRegistry.register('delete_agent', deleteAgentTool, 'agent-management')
  toolRegistry.register('get_agent_details', getAgentDetailsTool, 'agent-management')
  toolRegistry.register('list_toolboxes', listToolboxesTool, 'agent-management')
  toolRegistry.register('list_tools', listToolsTool, 'agent-management')
  toolRegistry.register('request_tool_access', requestToolAccessTool, 'agent-management')
  toolRegistry.register('create_toolbox', createToolboxTool, 'agent-management')
  toolRegistry.register('update_toolbox', updateToolboxTool, 'agent-management')
  toolRegistry.register('delete_toolbox', deleteToolboxTool, 'agent-management')

  // Webhook tools (main only)
  toolRegistry.register('create_webhook', createWebhookTool, 'webhooks')
  toolRegistry.register('update_webhook', updateWebhookTool, 'webhooks')
  toolRegistry.register('delete_webhook', deleteWebhookTool, 'webhooks')
  toolRegistry.register('list_webhooks', listWebhooksTool, 'webhooks')

  // Channel tools (main only, send_channel_message/create/update/delete are opt-in)
  toolRegistry.register('list_channels', listChannelsTool, 'channels')
  toolRegistry.register('list_channel_conversations', listChannelConversationsTool, 'channels')
  toolRegistry.register('list_endpoints', listEndpointsTool, 'channels')
  toolRegistry.register('send_channel_message', sendChannelMessageTool, 'channels')
  toolRegistry.register('send_to_contact', sendToContactTool, 'channels')
  toolRegistry.register('create_channel', createChannelTool, 'channels')
  toolRegistry.register('update_channel', updateChannelTool, 'channels')
  toolRegistry.register('delete_channel', deleteChannelTool, 'channels')
  toolRegistry.register('activate_channel', activateChannelTool, 'channels')
  toolRegistry.register('deactivate_channel', deactivateChannelTool, 'channels')
  toolRegistry.register('transfer_channel', transferChannelTool, 'channels')
  toolRegistry.register('attach_file', attachFileTool, 'channels')

  // Platform / system tools (main only, opt-in required)
  toolRegistry.register('get_platform_logs', getPlatformLogsTool, 'system')
  toolRegistry.register('get_platform_config', getPlatformConfigTool, 'system')
  toolRegistry.register('list_platform_config_options', listPlatformConfigOptionsTool, 'system')
  toolRegistry.register('update_platform_config', updatePlatformConfigTool, 'system')
  toolRegistry.register('restart_platform', restartPlatformTool, 'system')
  toolRegistry.register('get_system_info', getSystemInfoTool, 'system')
  // Read-only "doctor 2.0" diagnostic: capability coverage, invalid providers,
  // stale defaults, channel status, public-URL sanity + a prioritized fix list.
  toolRegistry.register('get_setup_health', getSetupHealthTool, 'system')
  toolRegistry.register('http_request', httpRequestTool, 'browse')

  // Database tools (main only, opt-in required — God Tier)
  toolRegistry.register('execute_sql', executeSqlTool, 'database')

  // User management tools (main only)
  toolRegistry.register('list_users', listUsersTool, 'users')
  toolRegistry.register('get_user', getUserTool, 'users')
  toolRegistry.register('create_invitation', createInvitationTool, 'users')

  // Wake-up scheduler tools (main only)
  toolRegistry.register('wake_me_in', wakeMeInTool, 'crons')
  toolRegistry.register('wake_me_every', wakeMeEveryTool, 'crons')
  toolRegistry.register('cancel_wakeup', cancelWakeupTool, 'crons')
  toolRegistry.register('list_wakeups', listWakeupsTool, 'crons')

  // Mini-App tools (main only)
  toolRegistry.register('create_mini_app', createMiniAppTool, 'mini-apps')
  toolRegistry.register('update_mini_app', updateMiniAppTool, 'mini-apps')
  toolRegistry.register('delete_mini_app', deleteMiniAppTool, 'mini-apps')
  toolRegistry.register('list_mini_apps', listMiniAppsTool, 'mini-apps')
  toolRegistry.register('write_mini_app_file', writeMiniAppFileTool, 'mini-apps')
  toolRegistry.register('read_mini_app_file', readMiniAppFileTool, 'mini-apps')
  toolRegistry.register('delete_mini_app_file', deleteMiniAppFileTool, 'mini-apps')
  toolRegistry.register('list_mini_app_files', listMiniAppFilesTool, 'mini-apps')
  toolRegistry.register('get_mini_app_storage', getMiniAppStorageTool, 'mini-apps')
  toolRegistry.register('set_mini_app_storage', setMiniAppStorageTool, 'mini-apps')
  toolRegistry.register('delete_mini_app_storage', deleteMiniAppStorageTool, 'mini-apps')
  toolRegistry.register('list_mini_app_storage', listMiniAppStorageTool, 'mini-apps')
  toolRegistry.register('clear_mini_app_storage', clearMiniAppStorageTool, 'mini-apps')
  toolRegistry.register('create_mini_app_snapshot', createMiniAppSnapshotTool, 'mini-apps')
  toolRegistry.register('list_mini_app_snapshots', listMiniAppSnapshotsTool, 'mini-apps')
  toolRegistry.register('rollback_mini_app', rollbackMiniAppTool, 'mini-apps')
  toolRegistry.register('get_mini_app_templates', getMiniAppTemplatesTool, 'mini-apps')
  toolRegistry.register('get_mini_app_docs', getMiniAppDocsTool, 'mini-apps')
  toolRegistry.register('browse_mini_apps', browseMiniAppsTool, 'mini-apps')
  toolRegistry.register('generate_mini_app_icon', generateMiniAppIconTool, 'mini-apps')
  toolRegistry.register('get_mini_app_console', getMiniAppConsoleTool, 'mini-apps')
  toolRegistry.register('get_mini_app_backend_status', getMiniAppBackendStatusTool, 'mini-apps')
  toolRegistry.register('reload_mini_app', reloadMiniAppTool, 'mini-apps')
  toolRegistry.register('edit_mini_app_file', editMiniAppFileTool, 'mini-apps')
  toolRegistry.register('multi_edit_mini_app_file', multiEditMiniAppFileTool, 'mini-apps')
  toolRegistry.register('set_mini_app_maintainer', setMiniAppMaintainerTool, 'mini-apps')

  // Filesystem tools (main + sub-agent)
  toolRegistry.register('read_file', readFileTool, 'filesystem')
  toolRegistry.register('write_file', writeFileTool, 'filesystem')
  toolRegistry.register('edit_file', editFileTool, 'filesystem')
  toolRegistry.register('multi_edit', multiEditTool, 'filesystem')
  toolRegistry.register('list_directory', listDirectoryTool, 'filesystem')
  toolRegistry.register('grep', grepTool, 'filesystem')

  // Reasoning aid: free-form thought logger, no side effects.
  toolRegistry.register('think', thinkTool, 'tasks')

  // Sub-Agent structured planning (TodoWrite-equivalent).
  toolRegistry.register('task_todos', taskTodosTool, 'tasks')

  log.info({ count: toolRegistry.registeredCount }, 'Native tools registered')
}

```

## src/server/tools/subtask-tools.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/tools/subtask-tools.ts

SHA-256: `76f3021041318b58bfa317896aa493beff6d8a96958f53907b497c6b0b5dd943`

```
import { tool } from '@/server/tools/tool-helper'
import { z } from 'zod'
import {
  reportToParent,
  updateTaskStatus,
  requestInput,
} from '@/server/services/tasks'
import { createLogger } from '@/server/logger'
import type { ToolRegistration } from '@/server/tools/types'

const log = createLogger('tools:subtask')

/**
 * report_to_parent — send a message or intermediate result to the parent Agent.
 * Available to sub-Agents only.
 */
export const reportToParentTool: ToolRegistration = {
  availability: ['sub-agent'],
  create: (ctx) =>
    tool({
      description:
        'Send a message or intermediate result to the parent Agent.',
      inputSchema: z.object({
        message: z
          .string(),
      }),
      execute: async ({ message }) => {
        log.debug({ agentId: ctx.agentId, taskId: ctx.taskId }, 'report_to_parent invoked')
        if (!ctx.taskId) {
          return { error: 'No task context — this tool is only available to sub-Agents' }
        }
        const success = await reportToParent(ctx.taskId, message)
        if (!success) {
          return { error: 'Task not found or not active' }
        }
        return { success: true }
      },
    }),
}

/**
 * update_task_status — update the status of the current task.
 * Available to sub-Agents only.
 */
export const updateTaskStatusTool: ToolRegistration = {
  availability: ['sub-agent'],
  create: (ctx) =>
    tool({
      description:
        'Update task status. "completed" or "failed" finalizes the task.',
      inputSchema: z.object({
        status: z
          .enum(['in_progress', 'completed', 'failed']),
        result: z
          .string()
          .optional()
          .describe('For status="completed"'),
        error: z
          .string()
          .optional()
          .describe('For status="failed"'),
      }),
      execute: async ({ status, result, error }) => {
        log.debug({ agentId: ctx.agentId, taskId: ctx.taskId, status }, 'update_task_status invoked')
        if (!ctx.taskId) {
          return { error: 'No task context — this tool is only available to sub-Agents' }
        }
        const success = await updateTaskStatus(ctx.taskId, status, result, error)
        if (!success) {
          return { error: 'Task not found' }
        }
        return { success: true }
      },
    }),
}

/**
 * request_input — ask the parent Agent for clarification or a decision.
 * Available to sub-Agents only,
 * capped at `config.tasks.maxRequestInput` invocations per task.
 *
 * The tool **pauses the task**: the runner stops the multi-step loop and
 * resumes the sub-Agent only once the response arrives. The `note` in the
 * returned payload tells the model to emit nothing further on this turn —
 * any tool call you'd make next would race the suspension and likely be
 * the wrong move with the question still unanswered.
 */
export const requestInputTool: ToolRegistration = {
  availability: ['sub-agent'],
  create: (ctx) =>
    tool({
      description:
        'Ask the parent Agent for clarification or a decision. The task is suspended until the response arrives. DO NOT emit any further tool calls in this turn. Limited calls per task.',
      inputSchema: z.object({
        question: z.string(),
      }),
      execute: async ({ question }) => {
        log.debug({ agentId: ctx.agentId, taskId: ctx.taskId }, 'request_input invoked')
        if (!ctx.taskId) {
          return { error: 'No task context — this tool is only available to sub-Agents' }
        }
        const result = await requestInput(ctx.taskId, question)
        if (!result.success) {
          return { error: result.error }
        }
        return {
          success: true,
          paused: true as const,
          note:
            'Your task is now PAUSED waiting for the answer. Do NOT emit any further tool calls on this turn — the runner will stop the loop after this step and resume your sub-Agent once the response arrives in your message history. Acting on a guess before the human answers usually picks the wrong path; wait for the actual response.',
        }
      },
    }),
}

```

## src/server/tools/task-tools.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/tools/task-tools.ts

SHA-256: `40ce4a41c9f3777df2ec9243ed1f2bec630ff10d42a865a0ee64d7b8f6388ac3`

```
import { tool } from '@/server/tools/tool-helper'
import { z } from 'zod'
import { eq, and, asc, inArray } from 'drizzle-orm'
import {
  spawnTask,
  respondToTask,
  cancelTask,
  getTask,
  listTasksFiltered,
  getTaskMessages,
  TaskNotFoundError,
  type ListTasksFilters,
} from '@/server/services/tasks'
import { resolveAgentId } from '@/server/services/agent-resolver'
import { db } from '@/server/db/index'
import { messages, tasks } from '@/server/db/schema'
import { sql } from 'drizzle-orm'
import { createLogger } from '@/server/logger'
import type { ToolRegistration } from '@/server/tools/types'

const log = createLogger('tools:tasks')

/**
 * Resolve an array of toolbox **names** (as accepted by the spawn tools) into
 * toolbox **ids** to freeze onto the task row. When `toolboxes` is absent but
 * the deprecated `tool_preset` alias is present, the preset name is used as a
 * single-element list (mapping to the built-in toolbox of the same name).
 *
 * Unknown names are skipped with a warning. Returns `undefined` when nothing
 * resolves, so `spawnTask` falls back to its own default ('all',
 * else 'all') at execution time.
 */
async function resolveToolboxNamesToIds(
  names: string[] | undefined,
  toolPreset: string | undefined,
): Promise<string[] | undefined> {
  const requested = names && names.length > 0 ? names : toolPreset ? [toolPreset] : []
  if (requested.length === 0) return undefined

  const { getToolboxByName } = await import('@/server/services/toolboxes')
  const ids: string[] = []
  for (const name of requested) {
    const box = getToolboxByName(name.trim())
    if (box) {
      ids.push(box.id)
    } else {
      log.warn({ name }, 'spawn: unknown toolbox name ignored')
    }
  }
  return ids.length > 0 ? ids : undefined
}

/**
 * spawn_self — clone the current Agent with a specific mission.
 * Available to main agents and sub-agent tasks (enables router → worker pattern).
 */
export const spawnSelfTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  create: (ctx) =>
    tool({
      description:
        'Spawn a sub-Agent copy of yourself with a specific task. Your current turn ends immediately after spawning.',
      inputSchema: z.object({
        title: z.string().describe('Short label, max ~60 chars'),
        task_description: z.string(),
        mode: z
          .enum(['await', 'async'])
          .describe(
            '"await" = result triggers a new turn; "async" = informational, no new turn',
          ),
        model: z.string().optional().describe('Model ID (e.g. "claude-sonnet-4-6", "gpt-5.5"). Omit to use the parent Agent\'s default model.'),
        provider_id: z.string().optional()
          .describe('Provider slug (e.g. "openai-codex", "claude-max") or UUID — get it from list_providers or list_models (the `slug`/`providerSlug` field). Slug is preferred (stable, human-readable). Required whenever `model` is set, since the same model name can be served by several providers.'),
        allow_human_prompt: z.boolean().optional().describe('Default: true'),
        concurrency_group: z.string().optional()
          .describe('Queue name for concurrency control (e.g. "batch-issues", "api-calls"). ' +
            'Tasks in the same group are limited to concurrency_max parallel executions. ' +
            'Excess tasks are queued and auto-promoted when a slot frees.'),
        concurrency_max: z.number().int().min(1).optional()
          .describe('Max concurrent tasks in this group. Required if concurrency_group is set. Default: 1'),
        thinking: z.boolean().optional()
          .describe('Enable extended thinking/reasoning for this task. Omit to inherit from parent Agent config.'),
        toolboxes: z.array(z.string()).optional()
          .describe('Names of the toolboxes whose tools the sub-Agent may use. The sub-Agent\'s native toolset is the mandatory core floor unioned with every chosen toolbox\'s tools. Built-ins: "code", "research", "ops", "scout" (read-only), "all" (full surface). Use list_toolboxes to discover available toolboxes. Omit to default to "all".'),
        tool_preset: z.enum(['code', 'research', 'ops', 'all']).optional()
          .describe('DEPRECATED — use `toolboxes` instead. When set and `toolboxes` is absent, it maps to the built-in toolbox of the same name.'),
      }),
      execute: async ({ title, task_description, mode, model, provider_id, allow_human_prompt, concurrency_group, concurrency_max, thinking, toolboxes, tool_preset }) => {
        log.debug({ agentId: ctx.agentId, mode, spawnType: 'self', toolboxes, preset: tool_preset }, 'Task spawn requested (spawn_self)')
        if (model && !provider_id) {
          throw new Error(
            'When overriding the parent Agent\'s model, you must pass provider_id too. ' +
              'Use list_models to find the right (model, providerId) pair — the same model name can be served by several providers ' +
              '(e.g. an OpenAI API key and a Codex CLI subscription), and hivekeep cannot guess which one you mean.',
          )
        }
        const toolboxIds = await resolveToolboxNamesToIds(toolboxes, tool_preset)
        const { taskId, queued } = await spawnTask({
          parentAgentId: ctx.agentId,
          title,
          description: task_description,
          mode,
          spawnType: 'self',
          model,
          providerId: provider_id,
          allowHumanPrompt: allow_human_prompt,
          channelOriginId: ctx.channelOriginId,
          parentTaskId: ctx.taskId ?? undefined,
          depth: ctx.taskDepth ? ctx.taskDepth + 1 : undefined,
          concurrencyGroup: concurrency_group,
          concurrencyMax: concurrency_max ?? (concurrency_group ? 1 : undefined),
          thinkingConfig: thinking !== undefined ? { enabled: thinking } : undefined,
          toolboxIds,
          toolPreset: tool_preset,
        })
        return { taskId, status: queued ? 'queued' : 'pending' }
      },
    }),
}

/**
 * spawn_agent — instantiate another Agent from the platform with a specific mission.
 * Available to main agents and sub-agent tasks (enables router → worker pattern).
 */
export const spawnAgentTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  create: (ctx) =>
    tool({
      description:
        'Spawn another Agent as a sub-Agent for a specific task. Your current turn ends immediately after spawning.',
      inputSchema: z.object({
        agent_slug: z.string(),
        title: z.string().describe('Short label, max ~60 chars'),
        task_description: z.string(),
        mode: z
          .enum(['await', 'async'])
          .describe(
            '"await" = result triggers a new turn; "async" = informational, no new turn',
          ),
        model: z.string().optional().describe('Model ID (e.g. "claude-sonnet-4-6", "gpt-5.5"). Omit to use the spawned Agent\'s default model.'),
        provider_id: z.string().optional()
          .describe('Provider slug (e.g. "openai-codex", "claude-max") or UUID — get it from list_providers or list_models (the `slug`/`providerSlug` field). Slug is preferred (stable, human-readable). Required whenever `model` is set, since the same model name can be served by several providers.'),
        allow_human_prompt: z.boolean().optional().describe('Default: true'),
        concurrency_group: z.string().optional()
          .describe('Queue name for concurrency control (e.g. "batch-issues", "api-calls"). ' +
            'Tasks in the same group are limited to concurrency_max parallel executions. ' +
            'Excess tasks are queued and auto-promoted when a slot frees.'),
        concurrency_max: z.number().int().min(1).optional()
          .describe('Max concurrent tasks in this group. Required if concurrency_group is set. Default: 1'),
        thinking: z.boolean().optional()
          .describe('Enable extended thinking/reasoning for this task. Omit to inherit from parent Agent config.'),
        toolboxes: z.array(z.string()).optional()
          .describe('Names of the toolboxes whose tools the sub-Agent may use. The sub-Agent\'s native toolset is the mandatory core floor unioned with every chosen toolbox\'s tools. Use list_toolboxes to discover available toolboxes. Omit to default to "all". See spawn_self for built-in descriptions.'),
        tool_preset: z.enum(['code', 'research', 'ops', 'all']).optional()
          .describe('DEPRECATED — use `toolboxes` instead. When set and `toolboxes` is absent, it maps to the built-in toolbox of the same name.'),
      }),
      execute: async ({ agent_slug, title, task_description, mode, model, provider_id, allow_human_prompt, concurrency_group, concurrency_max, thinking, toolboxes, tool_preset }) => {
        if (model && !provider_id) {
          return {
            error:
              'When overriding the spawned Agent\'s model, you must pass provider_id too. ' +
              'Use list_models to find the right (model, providerId) pair — the same model name can be served by several providers ' +
              '(e.g. an OpenAI API key and a Codex CLI subscription), and hivekeep cannot guess which one you mean.',
          }
        }
        const agentId = resolveAgentId(agent_slug)
        if (!agentId) {
          return { error: `Agent not found for slug "${agent_slug}"` }
        }
        log.debug({ agentId: ctx.agentId, targetAgentId: agentId, mode, spawnType: 'other', toolboxes, preset: tool_preset }, 'Task spawn requested (spawn_agent)')
        const toolboxIds = await resolveToolboxNamesToIds(toolboxes, tool_preset)
        const { taskId, queued } = await spawnTask({
          parentAgentId: ctx.agentId,
          title,
          description: task_description,
          mode,
          spawnType: 'other',
          sourceAgentId: agentId,
          model,
          providerId: provider_id,
          allowHumanPrompt: allow_human_prompt,
          channelOriginId: ctx.channelOriginId,
          parentTaskId: ctx.taskId ?? undefined,
          depth: ctx.taskDepth ? ctx.taskDepth + 1 : undefined,
          concurrencyGroup: concurrency_group,
          concurrencyMax: concurrency_max ?? (concurrency_group ? 1 : undefined),
          thinkingConfig: thinking !== undefined ? { enabled: thinking } : undefined,
          toolboxIds,
          toolPreset: tool_preset,
        })
        return { taskId, status: queued ? 'queued' : 'pending' }
      },
    }),
}

/**
 * respond_to_task — answer a clarification request from a sub-Agent.
 * Available to main agents only.
 */
export const respondToTaskTool: ToolRegistration = {
  availability: ['main'],
  create: (ctx) =>
    tool({
      description:
        'Answer a clarification request from a sub-Agent. Triggers a new LLM turn on the sub-Agent.',
      inputSchema: z.object({
        task_id: z.string(),
        answer: z.string(),
      }),
      execute: async ({ task_id, answer }) => {
        const success = await respondToTask(task_id, answer)
        if (!success) {
          return { error: 'Task not found or not active' }
        }
        return { success: true }
      },
    }),
}

/**
 * cancel_task — cancel a task in progress.
 * Available to main agents only.
 */
export const cancelTaskTool: ToolRegistration = {
  availability: ['main'],
  create: (ctx) =>
    tool({
      description: 'Cancel a sub-Agent task that is pending or in progress.',
      inputSchema: z.object({
        task_id: z.string(),
      }),
      execute: async ({ task_id }) => {
        const success = await cancelTask(task_id, ctx.agentId)
        if (!success) {
          return { error: 'Task not found, not owned by you, or already finished' }
        }
        return { success: true }
      },
    }),
}

function parseTimestampInput(value: string | number | undefined): number | undefined {
  if (value === undefined) return undefined
  if (typeof value === 'number') return value
  const parsed = Date.parse(value)
  if (Number.isFinite(parsed)) return parsed
  const asNum = Number(value)
  return Number.isFinite(asNum) ? asNum : undefined
}

/**
 * list_tasks — list tasks related to this Agent with filters and pagination.
 * Available to main agents and sub-agent tasks.
 */
export const listTasksTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'List tasks you spawned or were assigned, with filters and pagination. ' +
        'Returns lightweight summaries (id, title, status, kind, agent slugs, timing, duration_ms). ' +
        'Use get_task_detail(id) for full task details, or get_task_messages(id, limit, offset) ' +
        'for paginated message history. Default limit is 20, max 100.',
      inputSchema: z.object({
        status: z
          .enum(['queued', 'pending', 'in_progress', 'paused', 'awaiting_human_input', 'awaiting_agent_response', 'awaiting_subtask', 'completed', 'failed', 'cancelled', 'all'])
          .optional()
          .describe('Filter by task status. Defaults to no filter (all statuses).'),
        parent_agent_slug: z
          .string()
          .optional()
          .describe('Filter to tasks spawned by this Agent (parent).'),
        child_agent_slug: z
          .string()
          .optional()
          .describe('Filter to tasks executed by this Agent (child / source). Useful with spawn_agent.'),
        kind: z
          .enum(['spawn_self', 'spawn_agent', 'webhook', 'cron', 'all'])
          .optional()
          .describe('Filter by how the task was created.'),
        since: z
          .union([z.string(), z.number()])
          .optional()
          .describe('Only tasks created at or after this time. Accepts ISO 8601 string or Unix ms.'),
        until: z
          .union([z.string(), z.number()])
          .optional()
          .describe('Only tasks created at or before this time. Accepts ISO 8601 string or Unix ms.'),
        limit: z.number().int().min(1).max(100).default(20).describe('Page size (max 100).'),
        offset: z.number().int().min(0).default(0).describe('Pagination offset.'),
      }),
      execute: async (args) => {
        const filters: ListTasksFilters = {
          status: args.status,
          parentAgentSlug: args.parent_agent_slug,
          childAgentSlug: args.child_agent_slug,
          kind: args.kind,
          since: parseTimestampInput(args.since),
          until: parseTimestampInput(args.until),
          limit: args.limit,
          offset: args.offset,
          // Scope to tasks where this Agent is either the parent (spawner) or
          // the source (executor) unless the caller explicitly targets an Agent slug.
          relatedToAgentId: args.parent_agent_slug || args.child_agent_slug ? undefined : ctx.agentId,
        }

        const { tasks: rows, total } = await listTasksFiltered(filters)
        const limit = args.limit
        const offset = args.offset
        return {
          tasks: rows.map((t) => ({
            id: t.id,
            title: t.title,
            status: t.status,
            kind: t.kind,
            parent_agent_slug: t.parentAgentSlug,
            child_agent_slug: t.childAgentSlug,
            depth: t.depth,
            created_at: t.createdAt,
            updated_at: t.updatedAt,
            duration_ms: t.durationMs,
          })),
          pagination: {
            total,
            offset,
            limit,
            hasMore: offset + rows.length < total,
          },
        }
      },
    }),
}

/**
 * list_active_queues — list all active concurrency groups with status.
 * Available to main agents and sub-agent tasks.
 */
export const listActiveQueuesTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  readOnly: true,
  concurrencySafe: true,
  create: (_ctx) =>
    tool({
      description:
        'List all active concurrency groups (queues) with their current status: active count, queued count, and max concurrent limit.',
      inputSchema: z.object({}),
      execute: async () => {
        const rows = await db
          .select({
            group: tasks.concurrencyGroup,
            concurrencyMax: tasks.concurrencyMax,
            activeCount: sql<number>`count(case when ${tasks.status} in ('pending', 'in_progress', 'awaiting_human_input', 'awaiting_agent_response', 'awaiting_subtask') then 1 end)`,
            queuedCount: sql<number>`count(case when ${tasks.status} = 'queued' then 1 end)`,
          })
          .from(tasks)
          .where(
            and(
              sql`${tasks.concurrencyGroup} is not null`,
              inArray(tasks.status, ['queued', 'pending', 'in_progress', 'awaiting_human_input', 'awaiting_agent_response', 'awaiting_subtask']),
            ),
          )
          .groupBy(tasks.concurrencyGroup)
          .all()

        return {
          queues: rows.map((r) => ({
            group: r.group,
            active: r.activeCount,
            queued: r.queuedCount,
            max: r.concurrencyMax,
          })),
        }
      },
    }),
}

/**
 * get_task_detail — fetch full details and message history of a task.
 * Works for tasks you spawned OR tasks where you were the executing Agent.
 */
export const getTaskDetailTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Get full details and message history of a task you spawned or were assigned.',
      inputSchema: z.object({
        task_id: z.string(),
      }),
      execute: async ({ task_id }) => {
        const task = await getTask(task_id)
        if (!task) return { error: 'Task not found' }

        // Verify the Agent has access (either parent or source)
        if (task.parentAgentId !== ctx.agentId && task.sourceAgentId !== ctx.agentId) {
          return { error: 'Access denied — you are not related to this task' }
        }

        // Fetch task messages
        const taskMessages = await db
          .select({
            role: messages.role,
            content: messages.content,
            sourceType: messages.sourceType,
            createdAt: messages.createdAt,
          })
          .from(messages)
          .where(and(eq(messages.agentId, task.parentAgentId), eq(messages.taskId, task_id)))
          .orderBy(asc(messages.createdAt))
          .all()

        return {
          task: {
            id: task.id,
            title: task.title,
            description: task.description,
            status: task.status,
            mode: task.mode,
            spawnType: task.spawnType,
            result: task.result,
            error: task.error,
            depth: task.depth,
            createdAt: task.createdAt.toISOString(),
            updatedAt: task.updatedAt.toISOString(),
          },
          messages: taskMessages.map((m) => ({
            role: m.role,
            content: m.content,
            sourceType: m.sourceType,
            createdAt: m.createdAt.toISOString(),
          })),
        }
      },
    }),
}

/**
 * get_task_messages — paginated view of a task's message history with previews.
 * Use this to inspect long-running tasks without loading every message body
 * into the calling Agent's context.
 */
export const getTaskMessagesTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  readOnly: true,
  concurrencySafe: true,
  create: (ctx) =>
    tool({
      description:
        'Get a paginated view of a task\'s message history with content previews. ' +
        'Useful to inspect long-running task progress without loading full bodies into context. ' +
        'Each message returns a 200-char preview, the full content length, and tool call counts.',
      inputSchema: z.object({
        task_id: z.string().describe('The task to fetch messages from.'),
        limit: z
          .number()
          .int()
          .min(1)
          .max(50)
          .default(20)
          .describe('Number of messages to return (max 50).'),
        offset: z
          .number()
          .int()
          .default(0)
          .describe('Pagination offset. Use a negative value to fetch from the end (e.g. -20 returns the last 20 messages).'),
        order: z
          .enum(['asc', 'desc'])
          .default('desc')
          .describe('asc = oldest first, desc = newest first.'),
      }),
      execute: async ({ task_id, limit, offset, order }) => {
        // Verify the calling Agent is related to the task (parent or source).
        const task = await getTask(task_id)
        if (!task) return { error: 'Task not found' }
        if (task.parentAgentId !== ctx.agentId && task.sourceAgentId !== ctx.agentId) {
          return { error: 'Access denied — you are not related to this task' }
        }

        try {
          const result = await getTaskMessages(task_id, limit, offset, order)
          return {
            task_id: result.taskId,
            task_title: result.taskTitle,
            task_status: result.taskStatus,
            messages: result.messages.map((m) => ({
              id: m.id,
              role: m.role,
              source_type: m.sourceType,
              created_at: m.createdAt,
              content_preview: m.contentPreview,
              content_length: m.contentLength,
              has_tool_calls: m.hasToolCalls,
              tool_call_count: m.toolCallCount,
            })),
            pagination: {
              total: result.total,
              offset,
              limit,
              hasMore: offset >= 0 ? offset + result.messages.length < result.total : false,
            },
          }
        } catch (err) {
          if (err instanceof TaskNotFoundError) return { error: 'Task not found' }
          throw err
        }
      },
    }),
}

```

## src/server/tools/tool-access-tools.ts

Source: https://github.com/MarlBurroW/hivekeep/blob/e6bf9aea4159312730a690b94697084616b44556/src/server/tools/tool-access-tools.ts

SHA-256: `532a53a8965898ddd0eae72a957116243d9d632915c662d080972ffba1aeba53`

```
import { tool } from '@/server/tools/tool-helper'
import { z } from 'zod'
import { createHumanPrompt } from '@/server/services/human-prompts'
import { getAgentExtraToolNames } from '@/server/services/toolset-resolver'
import { toolRegistry } from '@/server/tools/index'
import { resolveCustomTools } from '@/server/services/custom-tools'
import { getToolboxByName, resolveToolboxNames, CORE_TOOLS } from '@/server/services/toolboxes'
import { resolveAgentToolboxIds } from '@/server/services/toolset-resolver'
import { db } from '@/server/db/index'
import { agents } from '@/server/db/schema'
import { eq } from 'drizzle-orm'
import { createLogger } from '@/server/logger'
import type { ToolRegistration } from '@/server/tools/types'

const log = createLogger('tools:tool-access')

/**
 * request_tool_access — let an Agent ask the user for access to tools it does
 * not currently have. Creates a `tool_access` human prompt: the user sees a
 * card with one checkbox per requested tool plus the Agent's reason, can grant
 * a subset (or deny), and the approved names land in `agents.extra_tool_names`
 * (permanent, revocable from the Agent's Tools tab). Every tool known to the
 * platform is requestable — the human approval is the gate.
 */
export const requestToolAccessTool: ToolRegistration = {
  availability: ['main', 'sub-agent'],
  create: (ctx) => {
    let requestedThisTurn = false
    return tool({
      description:
        'Request access to tools you do not currently have. Use list_tools first to discover what exists (names + domains). Pass the exact tool names you need and a short reason — the user is prompted to grant or deny, and granted tools become permanently available to you. One request per turn.',
      inputSchema: z.object({
        tool_names: z
          .array(z.string())
          .min(1)
          .max(25)
          .describe('Exact tool names to request (from list_tools). To request a whole domain, pass its tools explicitly.'),
        reason: z
          .string()
          .min(10)
          .max(500)
          .describe('Why you need these tools — shown verbatim to the user on the approval card.'),
      }),
      execute: async ({ tool_names, reason }) => {
        if (requestedThisTurn) {
          return { error: 'You already requested tool access this turn. Wait for the user to respond before requesting again.' }
        }
        requestedThisTurn = true

        // Universe check: native + plugin (registry) + enabled custom tools.
        const registryNames = new Set(toolRegistry.list().map((t) => t.name))
        const customNames = new Set(Object.keys(resolveCustomTools()))
        const unknown = tool_names.filter((n) => !registryNames.has(n) && !customNames.has(n))
        if (unknown.length > 0) {
          return {
            error: `Unknown tool name${unknown.length > 1 ? 's' : ''}: ${unknown.join(', ')}. Use list_tools to discover the exact names.`,
          }
        }

        // Drop tools the Agent can already use (toolboxes ∪ extras ∪ core floor).
        const agentRow = await db.select().from(agents).where(eq(agents.id, ctx.agentId)).get()
        const boxNames = new Set([
          ...CORE_TOOLS,
          ...resolveToolboxNames(resolveAgentToolboxIds(agentRow?.toolboxIds)),
          ...(await getAgentExtraToolNames(ctx.agentId)),
        ])
        const missing = tool_names.filter((n) => !boxNames.has(n))
        if (missing.length === 0) {
          return { message: 'You already have access to all of the requested tools — no request was sent.' }
        }

        const domains = new Map(toolRegistry.list().map((t) => [t.name, t.domain]))
        const { promptId } = await createHumanPrompt({
          agentId: ctx.agentId,
          taskId: ctx.taskId,
          promptType: 'tool_access',
          question: `Tool access request (${missing.length} tool${missing.length > 1 ? 's' : ''})`,
          description: reason,
          options: missing.map((name) => ({
            label: name,
            value: name,
            description: domains.has(name) ? `domain: ${domains.get(name)}` : 'custom tool',
          })),
        })

        log.info({ agentId: ctx.agentId, promptId, tools: missing }, 'Tool access requested')
        return {
          promptId,
          status: 'pending',
          requested: missing,
          message: 'The user has been asked to grant access. Their decision will arrive as a new message — granted tools become available on your next turn. Please wait.',
        }
      },
    })
  },
}

```
