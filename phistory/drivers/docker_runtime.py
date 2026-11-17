from __future__ import annotations

import ipaddress
import json
import secrets
import subprocess
import time
from pathlib import Path

DOCKER_APPS = {"autogpt", "qm", "dark-moon"}


def prepare_docker_env(env: dict[str, str]) -> str:
    network = json.loads(subprocess.check_output(["docker", "network", "inspect", "bridge"], text=True, timeout=30))
    gateway = network[0]["IPAM"]["Config"][0]["Gateway"]
    address = ipaddress.ip_address(gateway)
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise RuntimeError("native Docker capture requires an owned private bridge gateway")
    home = Path(env["PHISTORY_HOME"])
    env.update(
        PHISTORY_DOCKER_HOST=gateway,
        PHISTORY_DOCKER_APP_NAME="phistory-" + secrets.token_hex(8),
        PHISTORY_NATIVE_READY=str(home / "native-app-ready"),
        PHISTORY_QM_DIR=str(home / "qm-deployment"),
    )
    if env.get("PHISTORY_SOURCE_ROOT"):
        source = Path(env["PHISTORY_SOURCE_ROOT"])
        env["COMPOSE_PROJECT_NAME"] = env["PHISTORY_DOCKER_APP_NAME"]
        env["COMPOSE_FILE"] = str(source / "docker-compose.yml") + ":" + str(source / "docker-compose.phistory.json")
    return gateway


def wait_docker_ready(env: dict[str, str], process: subprocess.Popen, deadline: float) -> None:
    while time.monotonic() < deadline:
        if Path(env["PHISTORY_NATIVE_READY"]).is_file():
            return
        if process.poll() is not None:
            raise RuntimeError(f"native Docker application exited during bootstrap ({process.returncode})")
        time.sleep(0.5)
    raise TimeoutError("native Docker application did not complete its full startup health checks")


def cleanup_docker_app(agent_id: str, env: dict[str, str], log) -> None:
    if agent_id == "autogpt":
        subprocess.run(
            ["docker", "logs", "--tail", "200", env["PHISTORY_DOCKER_APP_NAME"]],
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        command = ["docker", "rm", "-f", env["PHISTORY_DOCKER_APP_NAME"]]
        cwd = None
    elif agent_id == "qm":
        if not (Path(env["PHISTORY_QM_DIR"]) / "qm.config.jsonc").is_file():
            return
        command = ["qm", "down", "--purge"]
        cwd = env["PHISTORY_QM_DIR"]
    else:
        command = ["docker", "compose", "down", "--volumes", "--remove-orphans"]
        cwd = env["PHISTORY_SOURCE_ROOT"]
    subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
