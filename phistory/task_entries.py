"""Small drivers calling upstream public task APIs without assembling prompts."""

CTXAGENT_PREFETCH = """
import asyncio
from ctxagent.providers import providers
assert asyncio.run(providers(force=True)), "OpenRouter provider directory could not be cached"
"""

CTXAGENT_TASK = """
import asyncio
from pathlib import Path
from ctxagent import store
from ctxagent.agent import AgentSession
from ctxagent.config import SessionConfig
from ctxagent.segments import Conversation

async def main():
    store.init()
    config = SessionConfig(workdirs=[str(Path.cwd())])
    chat_id = store.create_chat("Phistory", config.to_dict())
    session = AgentSession(chat_id, config, Conversation())
    async for event in session.run_turn("Reply with one short sentence."):
        print(event, flush=True)

asyncio.run(main())
"""

QUORUM_TASK = """
import asyncio
from quorum.team import FourPhaseConsensusTeam
from quorum.models import close_pool

async def main():
    team = FourPhaseConsensusTeam(
        model_ids=["openai:gpt-4.1", "openai:gpt-4.1-mini"],
        method_override="standard",
    )
    try:
        async for event in team.run_stream("Reply with one short sentence."):
            print(event, flush=True)
    finally:
        await close_pool()

asyncio.run(main())
"""

MINIAGI_TASK = """
from miniagi import MiniAGI
from exceptions import InvalidLLMResponseError

agent = MiniAGI("gpt-4", "gpt-3.5-turbo", "Reply with one short sentence.", 4000, 2000)
try:
    agent.think()
except InvalidLLMResponseError:
    pass
"""

BROWSER_USE_PREFETCH = """
import subprocess
subprocess.run(["uvx", "browser-use", "--help"], check=True)
"""
