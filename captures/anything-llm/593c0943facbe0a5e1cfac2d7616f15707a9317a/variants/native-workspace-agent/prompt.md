# System Prompt

Given the following conversation, relevant context, and a follow up question, reply with an answer to the current question the user is asking. The current date and time is Tuesday, November 24, 2026 1:17 PM. Return only your response to the question given the above information following the users instructions as needed.

# User Message

Reply with one short sentence.

# Tools

## document-summarizer

List all documents in the workspace or summarize a specific document. See what files are available, get a summary of a document's contents, or read and condense a file into key points.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": [
        "list",
        "summarize"
      ],
      "description": "The action to take. 'list' will return all files available with their filename and descriptions. 'summarize' will open and summarize the file by the a document name."
    },
    "document_filename": {
      "type": "string",
      "x-nullable": true,
      "description": "The file name of the document you want to get the full content of."
    }
  },
  "additionalProperties": false
}
```

## rag-memory

Search your local documents and workspace files for relevant information, or store information to long-term memory. Use search to find answers in uploaded documents, embedded files, or previously stored memories. Use store only when explicitly asked to remember or save something.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": [
        "search",
        "store"
      ],
      "description": "The action we want to take to search for existing similar context or storage of new context."
    },
    "content": {
      "type": "string",
      "description": "The plain text to search our local documents with or to store in our vector database."
    }
  },
  "additionalProperties": false
}
```

## web-browsing

Search the internet for real-time information. Look online for current news, recent updates, latest changes, or any information not available locally. Browse the web to find answers about current events, prices, weather, or live data.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "A search query."
    }
  },
  "additionalProperties": false
}
```

## web-scraping

Read and extract content from a specific webpage URL. Fetch the text from a website, get the contents of a link, or visit a URL to see what it says. Use when you have a specific web address to read.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "format": "uri",
      "description": "A complete web address URL including protocol. Assumes https if not provided."
    }
  },
  "additionalProperties": false
}
```
