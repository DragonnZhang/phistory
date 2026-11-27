# System Prompt

<available_skills>
- gh: GitHub CLI skill for interacting with GitHub via the gh command line tool. Use when Bub needs to (1) Create, view, or manage GitHub repositories, (2) Work with issues and pull requests, (3) Create and manage releases, (4) Run and monitor GitHub Actions workflows, (5) Create and manage gists, (6) Install and manage agent skills, or (7) Perform any GitHub operations via command line.
- skill-creator: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Bub's capabilities with specialized knowledge, workflows, or tool integrations.
- telegram: Telegram Bot skill for sending and editing Telegram messages via Bot API.
Use when Bub needs to: (1) Send a message to a Telegram user/group/channel,
(2) Reply to a specific Telegram message with reply_to_message_id,
(3) Edit an existing Telegram message, or (4) Push proactive Telegram notifications.
Prefer rich formatted messages (headings, lists, tables, blockquotes, math, collapsible details, captioned media) over plain markdown whenever the content is more than one short paragraph.
</available_skills>

<general_instruct>
Call tools or skills to finish the task.
</general_instruct>
<context_contract>
Excessively long context may cause model call failures. In this case, you MAY use tape.info to retrieve the token usage and you SHOULD use tape.handoff tool to shorten the retrieved history.
</context_contract>

# User Message

sender_id=human|channel=$cli|chat_id=local
---Date: 2026-11-27T11:21:30+00:00---
Reply with one short sentence.

# Tools

## bash

Run a shell command. Use background=true to keep it running and fetch output later via bash_output.

Foreground commands that exceed timeout_seconds continue in the background
and return a shell ID. Use bash.output to read output or bash.kill to stop them.
Background commands do not use timeout_seconds.

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string"
    },
    "cwd": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "timeout_seconds": {
      "type": "integer",
      "default": 30
    },
    "background": {
      "type": "boolean",
      "default": false
    }
  },
  "required": [
    "command"
  ]
}
```

## bash_kill

Terminate a background shell process.

```json
{
  "type": "object",
  "properties": {
    "shell_id": {
      "type": "string"
    }
  },
  "required": [
    "shell_id"
  ]
}
```

## bash_output

Read buffered output from a background shell, with optional offset/limit for incremental polling.

```json
{
  "type": "object",
  "properties": {
    "shell_id": {
      "type": "string"
    },
    "offset": {
      "type": "integer",
      "default": 0
    },
    "limit": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "required": [
    "shell_id"
  ]
}
```

## fs_edit

Edit a text file by replacing old text with new text. You can specify the line number to start searching for the old text.

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string"
    },
    "old": {
      "type": "string"
    },
    "new": {
      "type": "string"
    },
    "start": {
      "type": "integer",
      "default": 0
    }
  },
  "required": [
    "path",
    "old",
    "new"
  ]
}
```

## fs_read

Read a text file and return its content. Supports optional pagination with offset and limit.

PNG, JPEG, WebP and GIF files are returned as images instead of text.

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string"
    },
    "offset": {
      "type": "integer",
      "default": 0
    },
    "limit": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "required": [
    "path"
  ]
}
```

## fs_write

Write content to a text file.

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string"
    },
    "content": {
      "type": "string"
    }
  },
  "required": [
    "path",
    "content"
  ]
}
```

## skill

Load the skill content by name. Return the location and skill content.
If name is not provided, list all available skills in the current workspace.

```json
{
  "type": "object",
  "properties": {
    "name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  }
}
```

## spill_read

Read bounded chunks from an oversized tool result stored in the current session's spill tape.

```json
{
  "type": "object",
  "properties": {
    "handle": {
      "type": "string"
    },
    "cursor": {
      "type": "integer",
      "default": 0
    },
    "count": {
      "type": "integer",
      "default": 1
    },
    "from_end": {
      "type": "boolean",
      "default": false
    }
  },
  "required": [
    "handle"
  ]
}
```

## subagent

Delegate a task to a sub-agent that runs its own loop with this agent's tools, in the background.

Returns a task ID at once; the sub-agent's answer arrives later as a <subagent-notification>
message. Start several to work in parallel. The sub-agent only sees `prompt` (and, with
session='fork', the current conversation), so make the prompt self-contained.

```json
{
  "properties": {
    "prompt": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        }
      ],
      "description": "The task for the sub-agent, either as a string or a list of message parts.",
      "title": "Prompt"
    },
    "agent": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Name of a subagent profile to use; its settings apply unless given here.",
      "title": "Agent"
    },
    "description": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "A short label for the task, shown in task lists.",
      "title": "Description"
    },
    "model": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "The model to use for the sub-agent.",
      "title": "Model"
    },
    "reasoning_effort": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "The reasoning effort of the sub-agent's model.",
      "title": "Reasoning Effort"
    },
    "session": {
      "default": "temp",
      "description": "'temp' (default): a new empty conversation that is discarded afterwards. 'fork': starts from a copy of the current conversation, discarded afterwards. Any other value names a session that is kept and can be continued later.",
      "title": "Session",
      "type": "string"
    },
    "allowed_tools": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Tool names the sub-agent may use; defaults to the tools available to the current agent.",
      "title": "Allowed Tools"
    },
    "allowed_skills": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Skill names the sub-agent may use; defaults to the skills available to the current agent.",
      "title": "Allowed Skills"
    },
    "timeout_seconds": {
      "anyOf": [
        {
          "exclusiveMinimum": 0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Stop the sub-agent after this many seconds.",
      "title": "Timeout Seconds"
    }
  },
  "required": [
    "prompt"
  ],
  "title": "SubAgentInput",
  "type": "object"
}
```

## subagent_cancel

Stop a running subagent task.

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string"
    }
  },
  "required": [
    "task_id"
  ]
}
```

## subagent_send

Send a message to a subagent task.

A running task receives it before its next step. A finished task with a named session
continues that conversation as a new background task.

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string"
    },
    "message": {
      "type": "string"
    }
  },
  "required": [
    "task_id",
    "message"
  ]
}
```

## subagent_wait

Return subagent task results, waiting up to timeout_seconds for them to finish (0 checks without waiting).

Without task_ids, covers this session's tasks whose results were not read yet.

```json
{
  "type": "object",
  "properties": {
    "task_ids": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "timeout_seconds": {
      "type": "integer",
      "default": 60
    }
  }
}
```

## tape_anchors

List anchors in the current tape.

```json
{
  "type": "object",
  "properties": {}
}
```

## tape_handoff

Add a handoff anchor to the current tape.

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "default": "handoff"
    },
    "summary": {
      "type": "string",
      "default": ""
    }
  }
}
```

## tape_info

Get information about the current tape, such as number of entries and anchors.

```json
{
  "type": "object",
  "properties": {}
}
```

## tape_reset

Reset the current tape, optionally archiving it.

```json
{
  "type": "object",
  "properties": {
    "archive": {
      "type": "boolean",
      "default": false
    }
  }
}
```

## tape_search

Search for entries in the current tape that match the query. Returns a list of matching entries.

```json
{
  "properties": {
    "query": {
      "description": "The search query string.",
      "title": "Query",
      "type": "string"
    },
    "limit": {
      "default": 20,
      "description": "Maximum number of search results to return.",
      "title": "Limit",
      "type": "integer"
    },
    "start": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Optional start date to filter entries (ISO format).",
      "title": "Start"
    },
    "end": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Optional end date to filter entries (ISO format).",
      "title": "End"
    },
    "kinds": {
      "default": [
        "message",
        "tool_result"
      ],
      "description": "Optional list of entry kinds to filter search results. Can include 'event', 'anchor', 'system', 'message', 'tool_call', 'tool_result', 'error'.",
      "items": {
        "type": "string"
      },
      "title": "Kinds",
      "type": "array"
    }
  },
  "required": [
    "query"
  ],
  "title": "SearchInput",
  "type": "object"
}
```

## web_fetch

Fetch(GET) the content of a web page, returning markdown if possible.

```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string"
    },
    "headers": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "timeout": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "required": [
    "url"
  ]
}
```
