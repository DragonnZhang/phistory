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
import tiktoken
tiktoken.encoding_for_model("gpt-4o")
tiktoken.encoding_for_model("gpt-4")
subprocess.run(["uvx", "browser-use", "--help"], check=True)
"""

GPT_RESEARCHER_TASK = """
import asyncio
import os
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
from deep_agents.main import run_research_task
task = "Reply with one short sentence."
asyncio.run(run_research_task(task, {
    "query": task, "max_sections": 3, "source": "web",
    "follow_guidelines": False, "verbose": False,
}))
"""

DEERFLOW_TASK = """
import os
from pathlib import Path
import yaml
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
os.chdir(source)
config = yaml.safe_load((source / "config.example.yaml").read_text())
config["models"] = [{
    "name": "phistory-openai", "use": "langchain_openai:ChatOpenAI",
    "model": "gpt-4.1", "api_key": "$OPENAI_API_KEY",
    "base_url": "https://api.openai.com/v1", "supports_thinking": False,
    "supports_vision": True,
}]
path = source / "config.yaml"
path.write_text(yaml.safe_dump(config, sort_keys=False))
os.environ["DEER_FLOW_PROJECT_ROOT"] = str(source)
from deerflow.client import DeerFlowClient
print(DeerFlowClient(config_path=str(path)).chat("Reply with one short sentence."))
"""

AGENTICSEEK_TASK = """
import asyncio
import os
os.environ["AGENT_RUNTIME_DIR"] = os.path.join(os.environ["PHISTORY_HOME"], "agenticseek-runtime")
os.environ["WORK_DIR"] = os.path.join(os.environ["PHISTORY_HOME"], "work")
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
from sources.llm_provider import Provider
from sources.agents.code_agent import CoderAgent
provider = Provider("openai", "gpt-4.1", is_local=False)
agent = CoderAgent("coder", "prompts/jarvis/coder_agent.txt", provider, verbose=False)
asyncio.run(agent.process("Reply with one short sentence.", None))
"""

DEEPANALYZE_TASK = """
from deepanalyze import DeepAnalyzeVLLM
from pathlib import Path
DeepAnalyzeVLLM(
    "DeepAnalyze-8B", api_url="http://deepanalyze.phistory.invalid/v1/chat/completions",
    max_rounds=1,
).generate("Reply with one short sentence.", workspace=str(Path.cwd()))
"""

BAMBOOAI_TASK = """
import os
from pathlib import Path
from bambooai.cli import init_home, load_env
state = Path(os.environ["PHISTORY_HOME"]) / "bambooai"
init_home(state, quiet=True)
load_env(state)
os.chdir(state)
from web_app.llm_config_builder import build_user_config
assert build_user_config("phistory")
from bambooai import BambooAI
BambooAI(user_id="phistory").pd_agent_converse(question="Reply with one short sentence.", mode="quick")
"""

COWAGENT_TASK = """
import os
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
from config import load_config
load_config()
from bridge.bridge import Bridge
print(Bridge().fetch_agent_reply("Reply with one short sentence.", None))
"""
