# System Prompt

You are a rigorous research planner who identifies NOVEL, TIMELY research angles. You follow recent trends from top venues in the relevant domain and propose research that advances the frontier rather than repeating known results.

NOVELTY PRINCIPLES:
- A good research angle addresses a GAP not yet covered by existing work.
- Avoid pure benchmark/comparison studies unless the methodology is novel.
- Prefer angles that combine existing techniques in new ways, apply methods to underexplored domains, or challenge common assumptions.
- The research must be FEASIBLE with limited compute (single GPU, hours not days).
- Check: would a reviewer say 'this is already well-known'? If so, find a sharper angle.

# User Message

Create a SMART research goal in markdown.
Topic: Reply with one short sentence.
Domains: machine-learning
Project: my-research
Quality threshold: 4.0

Required sections:
- **Topic**: The broad area
- **Novel Angle**: What specific aspect has NOT been well-studied? Why is this timely? What recent development creates an opportunity? How does this differ from standard approaches?
- **Scope**: Focused enough for a single paper
- **SMART Goal**: Specific, Measurable, Achievable, Relevant, Time-bound
- **Constraints**: Compute budget, available tools, data access
- **Success Criteria**: What results would make this publishable?
- **Generated**: Timestamp

IMPORTANT: The 'Novel Angle' section must convincingly argue why this specific research direction is NOT already covered by existing work. If the topic is well-studied (e.g., 'comparing optimizers'), you MUST find a specific unexplored aspect (e.g., 'under distribution shift with noisy gradients', 'in the few-shot regime', 'with modern architectures').

TREND VALIDATION (MANDATORY):
- Describe the research trend or gap that motivates this work. Do NOT fabricate specific paper titles or citations — actual papers will be retrieved in the literature search stage.
- Name the specific benchmark/dataset that will be used for evaluation.
- If no standard benchmark exists, explain how results will be measured.
- State whether SOTA results exist on this benchmark and what they are.
- Add a 'Benchmark' subsection listing: name, source, metrics, current SOTA (if known).

# Tools

_No tools captured._
