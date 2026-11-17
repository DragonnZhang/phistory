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
init_home(str(state), quiet=True)
load_env(str(state))
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
from bridge.context import Context, ContextType
text = "Reply with one short sentence."
context = Context(ContextType.TEXT, text, kwargs={"session_id": "phistory"})
print(Bridge().fetch_agent_reply(text, context))
"""

AGENT_ZERO_PREFETCH = """
import os
import importlib
import tiktoken
tiktoken.get_encoding("cl100k_base")
tiktoken.get_encoding("o200k_base")
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
module = importlib.import_module("plugins._model_config.extensions.python.startup_migration._20_bootstrap_model_presets")
assert module.BootstrapModelPresets(None).execute() != "error"
from helpers import dotenv
dotenv.save_dotenv_value("RFC_PASSWORD", os.environ["RFC_PASSWORD"])
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2").encode(["public asset prefetch"])
"""

AGENT_ZERO_TASK = """
import asyncio
import os
import hashlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
os.environ["A0_SET_RFC_URL"] = "127.0.0.1"
os.environ["A0_SET_RFC_PORT_HTTP"] = str(port)
server = subprocess.Popen([sys.executable, "run_ui.py", "--host", "127.0.0.1", "--port", str(port)])
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("native Agent Zero UI/RFC worker exited during startup")
        try:
            with opener.open(f"http://127.0.0.1:{port}/", timeout=2):
                break
        except urllib.error.HTTPError as error:
            if error.code < 500:
                break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    else:
        raise RuntimeError("native Agent Zero UI/RFC worker did not become ready")
    from helpers import runtime, dotenv
    runtime.initialize()
    dotenv.load_dotenv()
    from plugins._model_config.helpers import model_config
    path = Path(model_config._get_presets_path())
    print("Native presets SHA-256:", hashlib.sha256(path.read_bytes()).hexdigest(), flush=True)
    print("Native selected preset:", model_config.resolve_preset(model_config.get_configured_preset_name()), flush=True)
    from agent import AgentContext, UserMessage
    from initialize import initialize_agent
    async def main():
        context = AgentContext(initialize_agent())
        task = context.communicate(UserMessage(message="Reply with one short sentence."))
        await task.result()
    asyncio.run(main())
finally:
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
"""

ZCODE_TASK = """
import os
from pathlib import Path
os.environ["ZCODE_HTTP_PROXY"] = os.environ["HTTP_PROXY"]
os.environ["ZCODE_AGENT_CA_CERT"] = os.environ["SSL_CERT_FILE"]
path = Path(os.environ["PHISTORY_SOURCE_ROOT"]) / "apps/zcode-cli/packages/cli/dist/zcode.cjs"
os.execvp("node", ["node", str(path), "--prompt", "Reply with one short sentence.", "--cwd", str(Path.cwd()), "--json"])
"""

OUROBOROS_SERVER = """
import os
os.environ["OUROBOROS_EXTRA_CA_BUNDLE"] = os.environ["SSL_CERT_FILE"]
os.execvp("ouroboros", ["ouroboros", "server", "--host", "127.0.0.1", "--port", os.environ["PHISTORY_SERVER_PORT"], "--no-ui"])
"""

INTENTKIT_DATABASE_SETUP = """
import os
import psycopg
from psycopg import sql
import redis
with psycopg.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
    user=os.environ["DB_USERNAME"], password=os.environ["DB_PASSWORD"], dbname="postgres",
    autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(os.environ["DB_NAME"])))
redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ["REDIS_PORT"])).flushdb()
"""

SELF_OPERATING_TASK = """
import os
os.execvp("xvfb-run", ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", "operate", "--prompt", "Reply with one short sentence."])
"""

DEVOPSGPT_TASK = """
import json
import os
import sys
from pathlib import Path
import yaml
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
os.chdir(source)
sys.path.insert(0, str(source / "backend"))
config = yaml.safe_load((source / "env.yaml.tpl").read_text())
for field in ("GPT_KEYS", "GPT_KEYS_BACKUP"):
    providers = json.loads(config[field])
    provider = providers["openai"]
    provider["keys"] = [{os.environ["OPENAI_API_KEY"]: {"count": 0, "timestamp": 0}}]
    config[field] = json.dumps({"openai": provider})
(source / "env.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
from app.pkgs.prompt.code_basic import CodeBasic
print(CodeBasic().aiReviewCode(0, "Reply with one short sentence.", "print('hello')\\n", "main.py"))
"""

CRITIC_TRAINING_TASK = """
import os
from pathlib import Path
import yaml
from minisweagent.agents.default_prm import DefaultPRMAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel
source = Path(os.environ["PHISTORY_SOURCE_ROOT"]) / "mini-swe-agent"
config = yaml.safe_load((source / "src/minisweagent/config/default.yaml").read_text())["agent"]
config.update(step_limit=1, use_prm=True)
model = LitellmModel(model_name="gpt-4.1", model_kwargs={
    "custom_llm_provider": "openai", "api_base": "https://api.openai.com/v1",
    "api_key": os.environ["OPENAI_API_KEY"],
})
print(DefaultPRMAgent(model, LocalEnvironment(cwd=str(Path.cwd())), **config).run("Reply with one short sentence."))
"""

WECHAT_REPLY_TASK = """
import os
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
from src.tools import register_builtin_tools
from src.utils.qwen_client import QwenClient
from src.reply.generator import ReplyGenerator
from src.models.base import ChatMessage, SenderType
register_builtin_tools()
message = ChatMessage(text="Reply with one short sentence.", sender="Phistory", sender_type=SenderType.OTHER, chat_name="Phistory")
print(ReplyGenerator(llm_client=QwenClient()).generate([message], [message]))
"""

AUTOREF_TASK = """
import os
import sys
from pathlib import Path
from PIL import Image
from autoref.generate import main
state = Path(os.environ["PHISTORY_HOME"]) / "autoref"
state.mkdir(parents=True, exist_ok=True)
reference = state / "owned-reference.png"
Image.new("RGB", (64, 64), (80, 120, 160)).save(reference)
sys.argv = ["autoref.generate", "--refs", str(reference), "--prompt", "Reply with one short sentence.", "--out", str(state / "out.png")]
main()
"""

AGENT_S_TASK = """
import os
os.execvp("xvfb-run", ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", "agent_s",
    "--task", "Reply with one short sentence.",
    "--ground_provider", "huggingface", "--ground_url", "https://api.endpoints.huggingface.cloud/v1",
    "--ground_api_key", "phistory-fake-api-key", "--ground_model", "ui-tars-1.5-7b",
    "--grounding_width", "1280", "--grounding_height", "800"])
"""

LONG_HORIZON_SETUP = """
import subprocess
subprocess.run(["git", "init", "--quiet"], check=True)
subprocess.run(["lh-harness", "init"], check=True)
"""

AGENTLAS_SETUP = """
import json
import os
import subprocess
from pathlib import Path
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
installed = subprocess.run(["bash", str(source / "scripts/install-all-runtimes.sh")], cwd=source)
if installed.returncode not in (0, 1):
    raise RuntimeError("native Agentlas installer failed")
launcher = Path(os.environ["PHISTORY_HOME"]) / ".agentlas/os-bin/agentlas-os"
subprocess.run([str(launcher), "features", "status"], check=True)
status = json.loads(subprocess.check_output([str(launcher), "host", "status", "claude"], text=True))
session = status.get("session", {})
runtime = Path(status.get("runtime", "/nonexistent"))
release = (runtime / "RELEASE").read_text().strip()
if not (status.get("status") == "partial" and status.get("reason") == "session_activation_not_observed" and status.get("version", "").startswith(release + "+os.") and runtime.is_dir() and runtime.resolve().is_relative_to(Path(os.environ["PHISTORY_HOME"]).resolve()) and (runtime / "RELEASE").read_text().strip() == release and session.get("reason") == "session_activation_not_observed" and session.get("hostCommand", [])[:1] == ["claude"] and any(item.get("kind") == "core" and Path(item.get("pluginDir", ""), ".mcp.json").is_file() for item in session.get("components", []))):
    raise RuntimeError("native Agentlas runtime/session integrity was not verified: " + json.dumps(status))
print("Native Agentlas prepared runtime:", json.dumps(status), flush=True)
"""

AGENTLAS_TASK = """
import os
import subprocess
from pathlib import Path
print("Native host provenance:", os.environ["PHISTORY_NATIVE_HOST"], flush=True)
launcher = str(Path(os.environ["PHISTORY_HOME"]) / ".agentlas/os-bin/agentlas-os")
subprocess.run([launcher, "host", "status", "claude"], check=False)
os.execv(launcher, [launcher, "host", "session", "claude", "--", "-p", "/agentlas-os-core:agentlas Reply with one short sentence.", "--output-format", "json"])
"""

LONG_HORIZON_TASK = """
import os
print("Native host provenance:", os.environ["PHISTORY_NATIVE_HOST"], flush=True)
os.execvp("lh-harness", ["lh-harness", "run", "--task", "Reply with one short sentence.", "--agent", "codex", "--max-rounds", "1", "--api-key", os.environ["OPENAI_API_KEY"], "--base-url", "https://api.openai.com/v1"])
"""

OPENAGENTS_SETUP = r'''
import json
import os
import platform
import subprocess
from pathlib import Path
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
state = Path(os.environ["PHISTORY_HOME"]) / "openagents"
state.mkdir(parents=True, exist_ok=True)
facts = json.dumps({"os": platform.system(), "architecture": platform.machine(), "app": "OpenAgents", "cwd": str(Path.cwd())})
includes = [source / path for path in ("coax/lib/microcoder/run.coax", "coax/lib/gateway/plan.coax", "coax/lib/gateway/openrouter.coax")]
code = '#import "Basic";\n' + ''.join('#load ' + json.dumps(str(path)) + ';\n' for path in includes)
code += r"""
phistory_task :: () -> int #export_c {
  run: [1] McRun; sb: [1] McStateBuffers; lb: [1] McLoopBuffers;
  p: [1] GwPlan; pb: [1] GwPlanBuffers; bench: [1] GwBench;
  gw_plan_init(p[..], pb[..]);
  h := GW_OPENROUTER_HEADERS;
  added := gw_plan_add(p[..], gw_openrouter(false), GW_OPENROUTER_FLASH, h[..]);
  mc_init(run[0].l[..], sb[..], lb[..], FACTS, TASK, "{\"route\":\"other\",\"difficulty\":\"trivial\",\"effort\":\"low\",\"local_work\":0.1}", 0.1);
  mc_run_start(run[..], p[..], bench[..], WORKDIR, "low");
  ev: [16] McRunEvent;
  while !run[0].ended { n := mc_run_poll(run[..], ev[..]); }
  print("native Microcoder ended\n");
  return 0;
}
"""
code = code.replace('FACTS', json.dumps(facts)).replace('TASK', json.dumps("Reply with one short sentence.")).replace('WORKDIR', json.dumps(str(Path.cwd())))
driver = state / "task.coax"
driver.write_text(code)
c_file = state / "task.c"
header = state / "task.h"
entry = state / "entry.c"
entry.write_text('#include "task.h"\nint main(void) { return (int)phistory_task(); }\n')
with c_file.open("w") as output:
    subprocess.run([str(source / "coax/bin/coax"), "c", str(driver), "--library", "--header", str(header)], cwd=source, stdout=output, check=True)
subprocess.run(["cc", "-std=c11", "-O1", "-g", "-o", str(state / "task"), str(entry), str(c_file), str(source / "coax/platform/process-native.c"), "-lm"], check=True)
'''

OPENAGENTS_TASK = """
import os
from pathlib import Path
os.environ["CURL_CA_BUNDLE"] = os.environ["SSL_CERT_FILE"]
os.execv(str(Path(os.environ["PHISTORY_HOME"]) / "openagents/task"), ["openagents-native-microcoder-task"])
"""

HIVEKEEP_TASK = """
import os
from pathlib import Path
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
os.chdir(source)
base = "http://127.0.0.1:" + os.environ["PHISTORY_SERVER_PORT"]
os.environ.update(HIVEKEEP_DATA_DIR=str(Path(os.environ["PHISTORY_HOME"]) / "hivekeep-data"), HOST="127.0.0.1", PORT=os.environ["PHISTORY_SERVER_PORT"], PUBLIC_URL=base, BETTER_AUTH_BASE_URL=base)
os.execvp("bun", ["bun", "src/server/index.ts"])
"""

AGENTLINK_SETUP = """
import os
import subprocess
from pathlib import Path
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
env = dict(os.environ, AGENTLINK_HOME=str(Path(os.environ["PHISTORY_HOME"]) / ".agentlink"))
subprocess.run(["node", str(source / "apps/cli/dist/agentlink.js"), "--project", os.getcwd(), "config", "model", "openai/gpt-5.6-sol"], env=env, check=True)
"""

AGENTLINK_TASK = """
import os
from pathlib import Path
os.environ.pop("CI", None)
os.environ["AGENTLINK_HOME"] = str(Path(os.environ["PHISTORY_HOME"]) / ".agentlink")
os.execvp("node", ["node", str(Path(os.environ["PHISTORY_SOURCE_ROOT"]) / "apps/cli/dist/agentlink.js"), "--project", os.getcwd()])
"""

ANYTHINGLLM_SETUP = """
import os
import subprocess
from pathlib import Path
server = Path(os.environ["PHISTORY_SOURCE_ROOT"]) / "server"
storage = server / "storage"
storage.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, STORAGE_DIR=str(storage), NODE_ENV="production")
for command in ["generate", "migrate"]:
    args = [str(server / "node_modules/.bin/prisma"), command]
    if command == "migrate":
        args.append("deploy")
    subprocess.run([*args, "--schema=./prisma/schema.prisma"], cwd=server, env=env, check=True)
subprocess.run(["node", "-e", "new (require('./utils/EmbeddingEngines/native').NativeEmbedder)().embedderClient().then(() => process.exit(0)).catch(e => {console.error(e);process.exit(1)})"], cwd=server, env=env, check=True)
"""

ANYTHINGLLM_TASK = """
import os
import subprocess
from pathlib import Path
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
env = dict(os.environ, STORAGE_DIR=str(source / "server/storage"), NODE_ENV="production", SERVER_PORT=os.environ["PHISTORY_SERVER_PORT"], COLLECTOR_PORT=os.environ["PHISTORY_COLLECTOR_PORT"])
processes = []
try:
    for name in ["collector", "server"]:
        processes.append(subprocess.Popen(["node", "index.js"], cwd=source / name, env=env))
    processes[-1].wait()
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        process.wait(timeout=10)
"""

UFO_SETUP = """
import os
from pathlib import Path
import yaml
try:
    import tiktoken
except ImportError:
    pass
else:
    tiktoken.get_encoding("cl100k_base")
    tiktoken.get_encoding("o200k_base")
source = Path(os.environ["PHISTORY_SOURCE_ROOT"])
config = yaml.safe_load((source / "config/ufo/agents.yaml.template").read_text())
for item in config.values():
    if isinstance(item, dict) and "API_KEY" in item:
        item["API_KEY"] = os.environ["OPENAI_API_KEY"]
(source / "config/ufo/agents.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
"""

UFO_TASK = """
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
os.chdir(os.environ["PHISTORY_SOURCE_ROOT"])
services = []
try:
    services.append(subprocess.Popen([sys.executable, "-m", "ufo.client.mcp.http_servers.linux_mcp_server", "--host", "localhost", "--port", "8010"]))
    services.append(subprocess.Popen([sys.executable, "-m", "ufo.server.app", "--host", "127.0.0.1", "--port", "5001", "--platform", "linux", "--api-key", os.environ["PHISTORY_UFO_CONTROL_KEY"]]))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 120
    for url in ["http://localhost:8010/mcp", "http://127.0.0.1:5001/"]:
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in services):
                raise RuntimeError("native UFO service exited during startup")
            try:
                with opener.open(url, timeout=2):
                    break
            except urllib.error.HTTPError as error:
                if error.code < 500:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("native UFO services did not become ready")
    subprocess.run([sys.executable, "-m", "ufo.client.client", "--ws", "--ws-server", "ws://127.0.0.1:5001/ws?token=" + os.environ["PHISTORY_UFO_CONTROL_KEY"], "--client-id", "phistory_linux", "--platform", "linux", "--request", "Reply with one short sentence.", "--task_name", "phistory"], check=True)
finally:
    for process in services:
        if process.poll() is None:
            process.terminate()
    for process in services:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
"""
