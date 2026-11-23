# gpt-researcher — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## deep_agents/agent.py

Source: https://github.com/assafelovic/gpt-researcher/blob/8b474fe7d92aca112601cb87e1ec0999103bc01a/deep_agents/agent.py

SHA-256: `323c88c80d609a910bdd3599a6922558fe73a27c976a364fb50f12afe3a3302b`

```
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from deep_agents.tools import build_research_tools

CHIEF_EDITOR_PROMPT = """You are the Chief Editor of an autonomous research team. \
Your job is to produce a comprehensive, well-cited research report on the topic \
the user gives you.

You do not research topics yourself. You coordinate, review, and edit. Follow \
this workflow:

1. **Scope**: Use `quick_search` once to get an overview of the topic.
2. **Plan**: Use `write_todos` to plan the report: one todo per section \
(respect the maximum number of sections in the request), plus todos for review \
and final assembly.
3. **Delegate**: For each section, call the `task` tool with the `researcher` \
subagent. Issue all section tasks in a single turn so they run in parallel. \
Tell each researcher:
   - the section topic and the overall report topic,
   - the exact file to write its draft to: `sections/<two-digit-index>-<slug>.md`.
4. **Review**: Read each draft with `read_file`. If a draft is off-topic, \
shallow, or violates the guidelines, delegate a revision to the `researcher` \
subagent with concrete feedback. Fix small issues yourself with `edit_file`.
5. **Assemble**: Write the final report to `report.md`. It must contain:
   - a title and a short introduction you write yourself,
   - a table of contents,
   - the section drafts, edited for flow and consistent formatting (remove \
per-section source lists),
   - a conclusion you write yourself,
   - a single deduplicated **References** section at the end containing every \
source cited across sections.

Rules:
- Never write a section's body from your own knowledge; section content must \
come from researcher drafts.
- Keep your own messages short; the report lives in files, not in chat.
- When you are done, reply with a one-paragraph summary of the report and the \
path `report.md`.
{guidelines}"""

RESEARCHER_PROMPT = """You are a research specialist on a report-writing team. \
You will be given one section of a larger report to research and a file path to \
write your draft to.

Follow these steps:
1. Call `deep_research` exactly once with the section topic as `query` and the \
overall report topic as `parent_query`. It returns a detailed, cited markdown \
report - this is your source material.
2. Write the draft to the file path you were given using `write_file`. The \
draft must be well-structured markdown, keep all in-text citations and source \
URLs from the research, and start with a `##` heading for the section.
3. Reply with only: a summary of the section in at most 150 words, the number \
of sources used, and the file path. Never paste the full draft into your reply.
"""


def build_agent(task: dict, run_dir: str):
    model = os.environ.get("STRATEGIC_LLM") or task.get("model", "openai:gpt-5.4")
    quick_search, deep_research = build_research_tools(task)

    guidelines = ""
    if task.get("follow_guidelines") and task.get("guidelines"):
        rules = "\n".join(f"- {g}" for g in task["guidelines"])
        guidelines = f"\nThe report MUST follow these guidelines:\n{rules}"

    researcher_subagent = {
        "name": "researcher",
        "description": (
            "Conducts in-depth, citation-grade research on one report section "
            "and writes a draft to a file. Give it one section topic, the "
            "overall report topic, and the target file path."
        ),
        "system_prompt": RESEARCHER_PROMPT,
        "tools": [quick_search, deep_research],
    }

    return create_deep_agent(
        model=model,
        tools=[quick_search],
        system_prompt=CHIEF_EDITOR_PROMPT.format(guidelines=guidelines),
        subagents=[researcher_subagent],
        backend=FilesystemBackend(root_dir=run_dir, virtual_mode=True),
    )

```

## deep_agents/tools.py

Source: https://github.com/assafelovic/gpt-researcher/blob/8b474fe7d92aca112601cb87e1ec0999103bc01a/deep_agents/tools.py

SHA-256: `43ec85b58ce50b1f897fa6dc2061d5e7331c25d5ccd804ca732871c1709a323a`

```
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gpt_researcher import GPTResearcher


def build_research_tools(task: dict, cost_tracker: dict | None = None):
    """Build the research tools with the task's source config bound in.

    The report source ("web", "local" or "hybrid") is task configuration, not
    something the agent should decide per call, so it is closed over here
    rather than exposed as a tool argument. For "local" and "hybrid", set the
    DOC_PATH env var to your documents directory.

    Pass a dict as cost_tracker to accumulate GPT Researcher API costs under
    its "total" key.
    """
    report_source = task.get("source", "web")

    def _track_cost(researcher: GPTResearcher):
        if cost_tracker is not None:
            cost_tracker["total"] = cost_tracker.get("total", 0.0) + researcher.get_costs()

    async def quick_search(query: str) -> str:
        """Run a fast web search and return the top results with snippets and URLs.

        Use this to scope a topic, verify a fact, or gather just enough context
        to plan a report outline. Results are raw snippets - iterate with
        refined queries if the first search does not surface what you need.
        For in-depth, citation-grade research on a section, use deep_research
        instead.
        """
        researcher = GPTResearcher(query=query, report_type="research_report", verbose=False)
        results = await researcher.quick_search(query)
        _track_cost(researcher)
        if isinstance(results, list):
            formatted = "\n\n".join(
                f"[{i}] {r.get('title') or ''}\n{r.get('body') or r.get('content') or ''}\n"
                f"URL: {r.get('href') or r.get('url') or ''}".strip()
                for i, r in enumerate(results, 1)
            )
            return formatted or "No results found."
        return results or "No results found."

    async def deep_research(query: str, parent_query: str = "") -> str:
        """Run the full GPT Researcher pipeline on a query.

        Plans sub-queries, gathers and validates dozens of sources (web pages
        and/or local documents, depending on the task configuration), and
        returns a detailed markdown research report with in-text citations and
        a reference list. This is slow (a few minutes) and thorough - use it
        once per report section, not for quick lookups.

        Args:
            query: The research question or section topic to investigate.
            parent_query: The overall report topic, when researching a section
                of a larger report. Keeps the research focused in context.
        """
        report_type = "subtopic_report" if parent_query else "research_report"
        researcher = GPTResearcher(
            query=query,
            report_type=report_type,
            report_source=report_source,
            parent_query=parent_query,
            verbose=False,
        )
        await researcher.conduct_research()
        report = await researcher.write_report()
        _track_cost(researcher)

        sources = researcher.get_source_urls()
        if sources:
            report += "\n\n### Sources\n\n" + "\n".join(f"- {url}" for url in sources)
        return report

    return quick_search, deep_research

```
