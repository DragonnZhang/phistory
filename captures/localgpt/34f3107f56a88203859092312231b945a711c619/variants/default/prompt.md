# User Message


You are a query routing expert. Analyze the user's question and decide which backend should handle it.

Choose **exactly one** category:

1. "rag_query" – Questions about the user's uploaded documents or specific document content that should be searched. Examples: "What is the invoice amount?", "Summarize the research paper", "What companies are mentioned?"

2. "direct_answer" – General knowledge questions, greetings, or queries unrelated to uploaded documents. Examples: "Who are the CEOs of Tesla and Amazon?", "What is the capital of France?", "Hello", "Explain quantum physics"

IMPORTANT: For general world knowledge about well-known companies, people, or facts NOT related to uploaded documents, choose "direct_answer".

User query: "Reply with one short sentence."

Respond with JSON: {"category": "<your_choice>"}

# Tools

_No tools captured._
