# System Prompt

You are an expert coding assistant operating inside rpi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
- read  — Read file contents
- bash  — Execute Bash commands (Bash/POSIX syntax, also on Windows)
- edit  — Find/replace edits to existing files
- write — Create or overwrite files
- docs  — Look up rpi commands, extensions, Pi package compatibility and .rpi configuration instead of guessing

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
- Reply in the same language as the user's latest message (the conversation's language when
  that message is too short to tell), unless the user asks for another.
- Be concise in your responses
- Show file paths clearly when working with files
- Prefer the smallest change that solves the problem
- Your reasoning is carried back to you on the next turn: do not re-derive it or restate
  the plan. Continue from the last unfinished step, and when a step lands, say which one.
  Re-stating the same plan without acting on it is a bug, not progress.
- Read only what you need. If a file or search result was already shown earlier in this
  conversation, use it instead of fetching it again.

Current working directory: $PHISTORY_WORKSPACE

# User Message

Reply with one short sentence.

# Tools

## bash

Execute a Bash command in the current working directory using Bash/POSIX syntax, even on Windows. PowerShell cmdlets (Get-ChildItem, Select-Object, Select-String) are not valid here; use ls, find, rg, and POSIX pipelines instead. Returns stdout and stderr. Output is truncated to last 2000 lines or 50KB (whichever is hit first). If truncated, full output is saved to a temp file. Defaults to a 120-second timeout; provide timeout in seconds to override it. A non-zero exit code is a command result: probe commands such as grep (no match) or git config --get (missing key) legitimately return 1. Read the output before deciding whether to retry; details carries exit_code and failure_kind. Always pass `description`: a concise 5-10 word summary of the command's purpose (e.g. "Run the test suite"); hosts show it in the collapsed tool row.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "BashInput",
  "description": "Input for the bash tool. Mirrors TS `BashToolInput` plus a host-side extension: `description` (optional) lets the model state the command's intent in 5–10 words. Collapsed tool rows show it instead of the raw command (ZCode's Bash row reads `input.description` the same way).",
  "type": "object",
  "required": [
    "command",
    "description",
    "timeout"
  ],
  "properties": {
    "command": {
      "description": "Bash command to execute.",
      "type": "string"
    },
    "description": {
      "description": "Brief description of what this command does (5–10 words), shown to the user in the collapsed tool row. Optional; the host falls back to the command text when omitted.",
      "default": null,
      "type": [
        "string",
        "null"
      ]
    },
    "timeout": {
      "description": "Timeout in seconds (optional, defaults to 120 seconds).",
      "default": null,
      "type": [
        "number",
        "null"
      ],
      "format": "double"
    }
  },
  "additionalProperties": false
}
```

## docs

Look up rpi usage documentation. Omit topic (or use topic=list) to list topics; pass a topic such as guide, authoring, agent, debugging, architecture, or overview. Add query to find relevant sections. Use this before guessing rpi commands, creating an agent project or extension, extension APIs, or .rpi configuration.

```json
{
  "type": "object",
  "properties": {
    "topic": {
      "description": "Topic to open. Omit it, or use `list`, to list available topics.",
      "default": null,
      "type": [
        "string",
        "null"
      ]
    },
    "query": {
      "description": "Optional case-insensitive text to find inside the selected topic.",
      "default": null,
      "type": [
        "string",
        "null"
      ]
    }
  },
  "required": []
}
```

## durable

Prototype native session/lane durable workflow and manual queue. No scheduler or delegation. Default status; status with runId observes that explicit run. Mutations require an explicit requestId (never the provider call ID); expected may be omitted to sample the current scope once. Retry only identical guarded requests; status with requestId queries its receipt after uncertain commits (do not also pass runId). Child actors cannot start/reset/recover/forget_run. Core limits: 64 receipts, 1 MiB record; wire limits: 64 KiB request, 480 KiB response.

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": [
        "status",
        "start",
        "checkpoint",
        "pause",
        "resume",
        "complete",
        "fail",
        "reset",
        "recover",
        "forget_run",
        "enqueue",
        "queue",
        "claim",
        "finish",
        "cancel"
      ]
    },
    "requestId": {
      "type": "string",
      "description": "Mutation identity, or receipt target for status (without runId).",
      "minLength": 1,
      "maxLength": 128,
      "pattern": "^[A-Za-z0-9._-]+$"
    },
    "expected": {
      "type": "object",
      "description": "Complete scope from status; required for identical guarded retries."
    },
    "runId": {
      "type": "string",
      "description": "Explicit run for status/recover/forget_run only."
    },
    "name": {
      "type": "string"
    },
    "steps": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "id",
          "title"
        ],
        "properties": {
          "id": {
            "type": "string"
          },
          "title": {
            "type": "string"
          }
        }
      }
    },
    "step": {
      "type": "string"
    },
    "status": {
      "type": "string",
      "enum": [
        "pending",
        "in_progress",
        "completed",
        "failed"
      ]
    },
    "note": {
      "type": [
        "string",
        "null"
      ]
    },
    "reason": {
      "type": [
        "string",
        "null"
      ]
    },
    "text": {
      "type": "string"
    },
    "item": {
      "type": "string"
    }
  },
  "required": []
}
```

## edit

Edit a single file using exact text replacement. Before editing, use read to inspect the current target region and copy oldText from that output, preserving whitespace and excluding display line numbers or truncation markers. If matching fails, read the target region again and rebuild the edit from the current content before retrying. Every edits[].oldText must match a unique, non-overlapping region of the original file. Each edit is matched against the same original file snapshot, not incrementally after earlier edits. Do not include overlapping or nested edits. If two changes affect the same block or nearby lines, merge them into one edit. Choose enough surrounding lines from the read output to make each oldText occur exactly once in the entire file; do not use a repeated statement or short identifier alone. If multiple matches are reported, use their line numbers and context to expand oldText for the intended location before retrying. Use camelCase oldText/newText fields.

```json
{
  "type": "object",
  "properties": {
    "path": {
      "description": "Path to the file to edit (relative or absolute).",
      "type": "string"
    },
    "edits": {
      "description": "One or more targeted replacements.",
      "type": "array",
      "items": {
        "$ref": "#/definitions/ReplaceEditJson"
      }
    }
  },
  "required": [
    "edits",
    "path"
  ],
  "definitions": {
    "ReplaceEditJson": {
      "description": "JSON-schema-facing edit shape (camelCase to match the TS wire contract).",
      "type": "object",
      "required": [
        "newText",
        "oldText"
      ],
      "properties": {
        "oldText": {
          "description": "Exact text for one targeted replacement. Must be unique in the original file and must not overlap with any other edit's `oldText` in the same call.",
          "type": "string"
        },
        "newText": {
          "description": "Replacement text for this targeted edit.",
          "type": "string"
        }
      }
    }
  }
}
```

## read

Read the contents of a file. Supports text files and images (jpg, png, gif, webp, bmp). Images are sent as attachments. For text files, output is truncated to 2000 lines or 50KB (whichever is hit first). Use offset/limit for large files. When you need the full file, continue with offset until complete.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ReadInput",
  "description": "Input for the read tool. Mirrors TS `ReadToolInput`. `offset` is 1-indexed.",
  "type": "object",
  "required": [
    "path",
    "offset",
    "limit"
  ],
  "properties": {
    "path": {
      "description": "Path to the file to read (relative or absolute).",
      "type": "string"
    },
    "offset": {
      "description": "Line number to start reading from (1-indexed).",
      "default": null,
      "type": [
        "integer",
        "null"
      ],
      "format": "uint32",
      "minimum": 0.0
    },
    "limit": {
      "description": "Maximum number of lines to read.",
      "default": null,
      "type": [
        "integer",
        "null"
      ],
      "format": "uint32",
      "minimum": 0.0
    }
  },
  "additionalProperties": false
}
```

## write

Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "WriteInput",
  "description": "Input for the write tool. Mirrors TS `WriteToolInput`.",
  "type": "object",
  "required": [
    "path",
    "content"
  ],
  "properties": {
    "path": {
      "description": "Path to the file to write (relative or absolute).",
      "type": "string"
    },
    "content": {
      "description": "Content to write to the file.",
      "type": "string"
    }
  },
  "additionalProperties": false
}
```
