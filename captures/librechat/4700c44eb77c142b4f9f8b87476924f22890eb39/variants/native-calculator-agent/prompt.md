# User Message

Reply with one short sentence.

# Tools

## calculator

Useful for getting the result of a math expression. The input to this tool should be a valid mathematical expression that could be executed by a simple calculator.

```json
{
  "type": "object",
  "properties": {
    "input": {
      "type": "string",
      "description": "A valid mathematical expression to evaluate"
    }
  },
  "required": [
    "input"
  ]
}
```
