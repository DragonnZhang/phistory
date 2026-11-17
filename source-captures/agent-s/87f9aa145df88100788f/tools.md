# Agent-S — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## agent_s4/README.md

Source: https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/agent_s4/README.md

SHA-256: `ef8ef5c83b7b30d8e0cf9d8e734d1916faf977a1cb998ebcdf9af63c1c253a01`

````
# agent_s4

The Agent S4 package, from the paper
[Evolving Agent Interfaces](https://arxiv.org/abs/2610.28601). It evolves a
**shadow interface**: a small Python tool library that a computer-use agent
imports to operate a desktop. Each step, a **Builder** agent edits an
interface on the host. A **task agent** then runs benchmark tasks on fresh AWS
VMs with that interface installed, and its score becomes the variant's
fitness.

Computer-use agents are limited by the interfaces humans give them: an agent
may type a spreadsheet cell by cell when one line of code would do. Agent S4
builds those interfaces itself. The Builder studies the task agent's
trajectories on synthetic training tasks, without success labels, and writes
and revises the interfaces and the knowledge of when to use them.
Population-based evolution then keeps the variants that score best on
validation tasks the Builder never sees, which favors changes that generalize.

## Results

On OSWorld V2, Agent S4 with Qwen3.8-27B and its evolved interfaces reaches a
**21.3%** binary score, a new state of the art for open-source computer-use
agents and 3.8 times the same model with fixed interfaces. That is above
GPT-5.5 (13.0%) and Claude Opus 4.8 (20.6%), at about 4.3% and 1.5% of their
respective costs.

> **Note:** OSWorld V2 scores a task as a strict binary pass/fail — 1 only if
> every part of the task's checker succeeds, 0 otherwise, with no partial
> credit for getting most of the way there. That's why these numbers look low
> next to benchmarks that award partial credit: OSWorld V2 is widely regarded
> as the hardest computer-use benchmark, and these are state-of-the-art
> results under that strict scoring, not an indication of low real-world
> reliability.

On ScienceBoard, the evolved interfaces raise the success
rate from 41.4% to 58.6% with the same model.

## Setup guides

This file describes the code. For setup, credentials and the launch scripts
behind the published runs, see the workspace READMEs in the
[Agent-S repository](https://github.com/simular-ai/Agent-S):

- [`osworld_setup/s4/README.md`](https://github.com/simular-ai/Agent-S/blob/main/osworld_setup/s4/README.md):
  OSWorld-V2, which is the default benchmark.
- [`scienceboard_setup/s4/README.md`](https://github.com/simular-ai/Agent-S/blob/main/scienceboard_setup/s4/README.md):
  ScienceBoard.

## Install

`agent-s4` is its own package; installing `gui-agents` does not include it.

The package contains only the code. A run also reads setup files from the
Agent-S repository: the Builder prompts, the seed interface, the task splits
and the pinned Prime Agent runtime, all under `osworld_setup/s4/`. So you
need a clone of the repository either way. Install the package from the
clone:

```bash
git clone https://github.com/simular-ai/Agent-S.git
cd Agent-S
pip install -e ./agent_s4
```

or install it from PyPI and point it at the clone:

```bash
pip install agent-s4
export SHADOW_EVOL_SHARED_ROOT=/path/to/Agent-S/osworld_setup/s4
```

Without those files, `python -m agent_s4.cli run` stops and says what to set.
The benchmark itself (the OSWorld-V2 checkout, AWS and model credentials) is
set up as described in the workspace READMEs above.

## Entry points

| Command | What it does |
|---|---|
| `python -m agent_s4.cli run ...` | Starts or resumes an experiment (`--help` lists the options). |
| `python -m agent_s4.cli rerun --config-path runs/<timestamp>/config.json` | Runs again from a saved config. |
| `python -m agent_s4.osworld_v2_preflight` | Runs the offline OSWorld-V2 workspace checks on their own. |
| `python -m agent_s4.eval.osworld_v2 ...`, `python -m agent_s4.eval.scienceboard_prime ...` | Eval workers. The evaluator harness normally starts these; they can also be run by hand on chosen tasks. |

`run` defaults to `--benchmark osworld_v2`, which uses the splits pinned in
`config/osworld_v2_release_pin.json`: `osworld_v2_train_50.json`,
`osworld_v2_val_25.json` and `osworld_v2_test.json` as the held-out set. No
train or val splits ship for `--benchmark osworld` (the original OSWorld) or
`--benchmark scienceboard`, so both require `--train-set` and `--val-set`.

## How a run works

**1. Bootstrap** (`orchestrator.run`):
- If Prime Agent is used, checks its pinned runtime. For OSWorld-V2 it also
  runs the workspace preflight and checks the configured splits against the
  release pin.
- Makes one clone of the benchmark's Evol checkout per island, named
  `<template>_<island>` (for example `OSWorldV2_Evol_<island>`).
- Copies the seed interface into the clone's `shadow_interface/` directory,
  which has its own git repository, and records it as the seed variant.
- Evaluates the seed on every island in parallel, writes the resolved config
  to `runs/<timestamp>/config.json`, and hands over to the driver.

**2. Driver loop** (`driver.Driver`):
- Keeps one global step counter.
- Each step, the scheduler (`config/scheduler.py`) picks one operator, which
  runs on every island in parallel. The step ends only when all islands have
  finished.
- Every dispatch is logged in the database, so a step interrupted by a crash
  is resumed rather than run twice.
- Validation reevaluation runs every `--reevaluation-interval` steps.
- The loop stops at `--max-steps` or `--max-cost`.

**3. Operators** (`operators/`): each step runs one of these.

| Operator | What it does |
|---|---|
| `mutation` | The usual step; see the sequence below. |
| `crossover` | The Builder combines two parents from the same island into one child, which is then evaluated like a mutation. |
| `migration` | Two phases: each island sends a tournament pick to another island, and the receiving island evaluates the migrant with its own task agent before adding it. `--migration-mode` and `--migration-topology` control the details. |
| `elimination` | Removes the lowest-ranked variants from each island. |
| `reevaluation` | Evaluates each island's current leaders on validation again and averages the results with their earlier scores. It creates no new variants. |

A mutation runs these steps:
1. A tournament picks the parent (`tournament.py`).
2. The parent is checked out, and its task trajectories are staged for the
   Builder to read.
3. Optionally, a scratch VM is started (`scratch_vm.py`) so the Builder can
   try things in a live app.
4. The Builder harness edits `shadow_interface/`. For OSWorld-V2 and
   ScienceBoard it runs inside a Landlock sandbox, because the Base checkout
   next to the clone holds the benchmark's gated evaluators.
5. Edits outside the allowed scope are reverted, and the child is tagged
   `evol/<island>/if-NNN`.
6. The evaluator harness runs the child on the train and val splits. The
   per-task results, scores and costs go into the database.

## Module map

| Area | Modules |
|---|---|
| Configuration | `cli.py` (argument parsing), `config/experiment.py` (`ExperimentConfig`, default splits per benchmark), `config/island.py`, `config/scheduler.py` |
| Control loop | `orchestrator.py` (bootstrap and seed eval), `driver.py`, `state.py` (`LoopState`, passed to each operator), `operators/`, `tournament.py`, `scoring.py` |
| State | `db.py` (SQLite tables: variants, per-task eval results, islands, history, cost events, dispatch log, reevaluation samples), `costs.py`, `git_utils.py` (variant tags and checkouts in each island's `shadow_interface/` repo) |
| Builder side | `harness/builder_common.py` (shared Builder subprocess and log parsing), `builder_sandbox.py` (Landlock), `builder_scratch_vm.py`, `scratch_vm.py` |
| Agent harnesses | `harness/prime/`, `harness/opencode/`: each has the Builder (`builder.py`), the task-agent install and VM command (`evaluator.py`), a runner, and pin checks (`source.py`); see the README in each directory. `harness/__init__.py` is the registry. |
| Evaluation | `harness/ubuntu_vm.py` (`UbuntuVMEvaluator`: starts an eval worker as a subprocess), `eval/osworld.py` (worker code shared by every benchmark), `eval/osworld_v2.py`, `eval/scienceboard_prime.py`, `eval/grader_image_limit.py` |
| Benchmarks | `benchmark_spec.py` (template and base checkout names, how run artefacts are named), `osworld_v2_preflight.py`, `synthetic_tasks.py`, `scienceboard_synthetic_runtime.py` |
| VMs | `vm/utils.py` (shared VM helpers), `vm/owner_tag.py` (adds an `Owner` tag to every EC2 instance) |
| Paths | `paths.py`, `constants.py`, `utils.py` |

## Where files go

`paths.py` sets two roots:

- **Shared root** (`SHADOW_EVOL_SHARED_ROOT`; defaults to `osworld_setup/s4/`):
  the vendored Prime runtime, `vm_assets/`, the seed interfaces and the generic
  Builder prompts.
- **Project root** (`SHADOW_EVOL_PROJECT_ROOT`; defaults to the shared root):
  the run workspace. It holds the benchmark's prompts, splits, data, release
  pin, checkouts and every run artefact. The ScienceBoard launchers set it to
  `scienceboard_setup/s4/`.

Run artefacts are named per benchmark (and per `SHADOW_EVOL_RUN_NAMESPACE`) by
`benchmark_spec.ns()`. For example, OSWorld-V2 uses
`shadow_evolution_osworld_v2_<ns>.db`, `eval_cache_osworld_v2_<ns>/` and
`builder_logs_osworld_v2_<ns>/`.

## Extending

**Add an agent harness:**
- Subclass `BuilderHarness` (`harness/base.py`) in `harness/<name>/builder.py`.
- Put the VM install and task-agent command in `harness/<name>/evaluator.py`.
- Register the key in both `BUILDER_HARNESSES` and `EVALUATOR_HARNESSES`.
- Teach `UbuntuVMEvaluator` and `eval/osworld.py` to dispatch on the key. Both
  currently accept only `prime_agent` and `opencode`.
- Pin and verify the upstream release in a `source.py`, and record it in a
  README, as the existing two harnesses do.

**Add a benchmark:**
- Add an entry to `benchmark_spec.BENCHMARKS`, and to `_DEFAULT_SPLITS` in
  `config/experiment.py` if it ships default splits.
- Write an eval worker that plugs into `eval.osworld.main(impl=...)`, as
  `eval/scienceboard_prime.py` does.
- The benchmark-specific branches in `harness/ubuntu_vm.py` and
  `orchestrator.py` (search for `config.benchmark ==`) cover how the base
  checkout is chosen, which prompt addenda are added and how splits are
  checked.

````

## gui_agents/s3/README.md

Source: https://github.com/simular-ai/Agent-S/blob/0fff0e634869dab72027435b4586e62300185d41/gui_agents/s3/README.md

SHA-256: `600865b1da2a49ee032cc987bfccd182ea6a2a0f73f156872c5a487c04974bf3`

````
<h1 align="center">
  <img src="../../images/agent_s.png" alt="Logo" style="vertical-align:middle" width="60"> Agent S3:
  <small>Scaling Agents for Computer Use</small>
</h1>

<p align="center">
  🌐 <a href="https://www.simular.ai/articles/agent-s3">[Blog]</a>
  📄 <a href="https://arxiv.org/abs/2510.02250">[Paper (TMLR 2026)]</a>
  🎥 <a href="https://www.youtube.com/watch?v=VHr0a3UBsh4">[Video]</a>
  🗨️ <a href="https://discord.gg/E2XfsK9fPV">[Discord]</a>
</p>

Agent S3 is the agent in the `gui-agents` package. It takes a natural-language task, looks at the screen, and completes the task by clicking, typing, and scrolling in ordinary desktop and web applications. It runs on macOS, Windows, and Linux.

## 🎯 Results

<p align="center">
  <img src="../../images/s3_results_new.png" alt="Agent S3 Results" width="700"/>
</p>

On OSWorld, Agent S3 alone reaches 66% in the 100-step setting, already exceeding the previous state of the art of 63.4% (GTA1 w/ GPT-5). With the addition of Behavior Best-of-N, performance climbs even higher to 72.6%, *surpassing* human-level performance on OSWorld (~72%)!

Agent S3 also demonstrates strong zero-shot generalization! On WindowsAgentArena, accuracy rises from 50.2% using only Agent S3 to 56.6% by selecting from 3 rollouts. Similarly on AndroidWorld, performance improves from 68.1% to 71.6%

## 🛠️ Installation & Setup

### Prerequisites
- **Single Monitor**: Our agent is designed for single monitor screens
- **Security**: The agent runs Python code to control your computer - use with care
- **Supported Platforms**: Linux, Mac, and Windows


### Installation
To install Agent S3 without cloning the repository, run
```bash
pip install gui-agents
```
If you would like to test Agent S3 while making changes, clone the repository and install using
```
pip install -e .
```

Don't forget to also `brew install tesseract`! Pytesseract requires this extra installation to work.


### API Configuration

#### Option 1: Environment Variables
Add to your `.bashrc` (Linux) or `.zshrc` (MacOS):
```bash
export OPENAI_API_KEY=<YOUR_API_KEY>
export ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY>
export HF_TOKEN=<YOUR_HF_TOKEN>
```

#### Option 2: Python Script
```python
import os
os.environ["OPENAI_API_KEY"] = "<YOUR_API_KEY>"
```

### Supported Models
We support Azure OpenAI, Anthropic, Gemini, Open Router, and vLLM inference. See [models.md](../../models.md) for details.

### Grounding Models (Required)
For optimal performance, we recommend [UI-TARS-1.5-7B](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B) hosted on Hugging Face Inference Endpoints or another provider. See [Hugging Face Inference Endpoints](https://huggingface.co/learn/cookbook/en/enterprise_dedicated_endpoints) for setup instructions.

## 🚀 Usage


> ⚡️ **Recommended Setup:**  
> For the best configuration, we recommend using **OpenAI gpt-5-2025-08-07** as the main model, paired with **UI-TARS-1.5-7B** for grounding.  


### CLI

Note, this is running Agent S3, our improved agent, without bBoN. 

Run Agent S3 with the required parameters:

```bash
agent_s \
    --provider openai \
    --model gpt-5-2025-08-07 \
    --ground_provider huggingface \
    --ground_url http://localhost:8080 \
    --ground_model ui-tars-1.5-7b \
    --grounding_width 1920 \
    --grounding_height 1080
```

#### Local Coding Environment (Optional)
For tasks that require code execution (e.g., data processing, file manipulation, system automation), you can enable the local coding environment:

```bash
agent_s \
    --provider openai \
    --model gpt-5-2025-08-07 \
    --ground_provider huggingface \
    --ground_url http://localhost:8080 \
    --ground_model ui-tars-1.5-7b \
    --grounding_width 1920 \
    --grounding_height 1080 \
    --enable_local_env
```

⚠️ **WARNING**: The local coding environment executes arbitrary Python and Bash code locally on your machine. Only use this feature in trusted environments and with trusted inputs.

#### Required Parameters
- **`--provider`**: Main generation model provider (e.g., openai, anthropic, etc.) - Default: "openai"
- **`--model`**: Main generation model name (e.g., gpt-5-2025-08-07) - Default: "gpt-5-2025-08-07"
- **`--ground_provider`**: The provider for the grounding model - **Required**
- **`--ground_url`**: The URL of the grounding model - **Required**
- **`--ground_model`**: The model name for the grounding model - **Required**
- **`--grounding_width`**: Width of the output coordinate resolution from the grounding model - **Required**
- **`--grounding_height`**: Height of the output coordinate resolution from the grounding model - **Required**

#### Optional Parameters
- **`--model_temperature`**: The temperature to fix all model calls to (necessary to set to 1.0 for models like o3 but can be left blank for other models)

#### Grounding Model Dimensions
The grounding width and height should match the output coordinate resolution of your grounding model:
- **UI-TARS-1.5-7B**: Use `--grounding_width 1920 --grounding_height 1080`
- **UI-TARS-72B**: Use `--grounding_width 1000 --grounding_height 1000`

#### Optional Parameters
- **`--model_url`**: Custom API URL for main generation model - Default: ""
- **`--model_api_key`**: API key for main generation model - Default: ""
- **`--ground_api_key`**: API key for grounding model endpoint - Default: ""
- **`--max_trajectory_length`**: Maximum number of image turns to keep in trajectory - Default: 8
- **`--enable_reflection`**: Enable reflection agent to assist the worker agent - Default: True
- **`--enable_local_env`**: Enable local coding environment for code execution (WARNING: Executes arbitrary code locally) - Default: False

#### Local Coding Environment Details
The local coding environment enables Agent S3 to execute Python and Bash code directly on your machine. This is particularly useful for:

- **Data Processing**: Manipulating spreadsheets, CSV files, or databases
- **File Operations**: Bulk file processing, content extraction, or file organization
- **System Automation**: Configuration changes, system setup, or automation scripts
- **Code Development**: Writing, editing, or executing code files
- **Text Processing**: Document manipulation, content editing, or formatting

When enabled, the agent can use the `call_code_agent` action to execute code blocks for tasks that can be completed through programming rather than GUI interaction.

**Requirements:**
- **Python**: The same Python interpreter used to run Agent S3 (automatically detected)
- **Bash**: Available at `/bin/bash` (standard on macOS and Linux)
- **System Permissions**: The agent runs with the same permissions as the user executing it

**Security Considerations:**
- The local environment executes arbitrary code with the same permissions as the user running the agent
- Only enable this feature in trusted environments
- Be cautious when the agent generates code for system-level operations
- Consider running in a sandboxed environment for untrusted tasks
- Bash scripts are executed with a 30-second timeout to prevent hanging processes

### `gui_agents` SDK

First, we import the necessary modules. `AgentS3` is the main agent class for Agent S3. `OSWorldACI` is our grounding agent that translates agent actions into executable python code.
```python
import pyautogui
import io
from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import OSWorldACI
from gui_agents.s3.utils.local_env import LocalEnv  # Optional: for local coding environment

# Load in your API keys.
from dotenv import load_dotenv
load_dotenv()

current_platform = "linux"  # "darwin", "windows"
```

Next, we define our engine parameters. `engine_params` is used for the main agent, and `engine_params_for_grounding` is for grounding. For `engine_params_for_grounding`, we support custom endpoints like HuggingFace TGI, vLLM, and Open Router.

```python
engine_params = {
  "engine_type": provider,
  "model": model,
  "base_url": model_url,           # Optional
  "api_key": model_api_key,        # Optional
  "temperature": model_temperature # Optional
}

# Load the grounding engine from a custom endpoint
ground_provider = "<your_ground_provider>"
ground_url = "<your_ground_url>"
ground_model = "<your_ground_model>"
ground_api_key = "<your_ground_api_key>"

# Set grounding dimensions based on your model's output coordinate resolution
# UI-TARS-1.5-7B: grounding_width=1920, grounding_height=1080
# UI-TARS-72B: grounding_width=1000, grounding_height=1000
grounding_width = 1920  # Width of output coordinate resolution
grounding_height = 1080  # Height of output coordinate resolution

engine_params_for_grounding = {
  "engine_type": ground_provider,
  "model": ground_model,
  "base_url": ground_url,
  "api_key": ground_api_key,  # Optional
  "grounding_width": grounding_width,
  "grounding_height": grounding_height,
}
```

Then, we define our grounding agent and Agent S3.

```python
# Optional: Enable local coding environment
enable_local_env = False  # Set to True to enable local code execution
local_env = LocalEnv() if enable_local_env else None

grounding_agent = OSWorldACI(
    env=local_env,  # Pass local_env for code execution capability
    platform=current_platform,
    engine_params_for_generation=engine_params,
    engine_params_for_grounding=engine_params_for_grounding,
    width=1920,  # Optional: screen width
    height=1080  # Optional: screen height
)

agent = AgentS3(
    engine_params,
    grounding_agent,
    platform=current_platform,
    max_trajectory_length=8,  # Optional: maximum image turns to keep
    enable_reflection=True     # Optional: enable reflection agent
)
```

Finally, let's query the agent!

```python
# Get screenshot.
screenshot = pyautogui.screenshot()
buffered = io.BytesIO() 
screenshot.save(buffered, format="PNG")
screenshot_bytes = buffered.getvalue()

obs = {
  "screenshot": screenshot_bytes,
}

instruction = "Close VS Code"
info, action = agent.predict(instruction=instruction, observation=obs)

exec(action[0])
```

Refer to [`cli_app.py`](cli_app.py) for more details on how the inference loop works.

### OSWorld

To deploy Agent S3 in OSWorld, follow the [OSWorld Deployment instructions](../../osworld_setup/s3/OSWorld.md).


````
