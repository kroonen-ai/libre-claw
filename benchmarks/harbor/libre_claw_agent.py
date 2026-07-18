"""Harbor installed-agent adapter for evaluating Libre Claw."""

from __future__ import annotations

import json
import os
import shlex

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class LibreClawAgent(BaseInstalledAgent):
    """Install Libre Claw in a Harbor task container and run its real agent loop."""

    @staticmethod
    def name() -> str:
        return "libre-claw"

    def version(self) -> str | None:
        return self._version

    def get_version_command(self) -> str | None:
        return "/opt/libre-claw-venv/bin/libre-claw --version"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "apt-get update && "
                "apt-get install -y git python3 python3-pip python3-venv ripgrep curl"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        ref = self._version or os.environ.get("LIBRE_CLAW_EVAL_REF", "main")
        package_url = f"git+https://github.com/kroonen-ai/libre-claw.git@{ref}"
        await self.exec_as_agent(
            environment,
            command=(
                "python3 -m venv /opt/libre-claw-venv && "
                "/opt/libre-claw-venv/bin/python -m pip install --upgrade pip && "
                f"/opt/libre-claw-venv/bin/pip install {shlex.quote(package_url)} && "
                "/opt/libre-claw-venv/bin/libre-claw --version"
            ),
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del context
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must use provider/model format, for example ollama/glm-5.2:cloud")
        provider, model = self.model_name.split("/", 1)
        if provider != "ollama":
            raise ValueError("This adapter currently supports the ollama provider prefix.")
        api_key = os.environ.get("OLLAMA_API_KEY")
        if not api_key:
            raise ValueError("Set OLLAMA_API_KEY in the Harbor process environment.")

        config_text = _benchmark_config(model)
        env = {
            "HARBOR_INSTRUCTION": instruction,
            "OLLAMA_API_KEY": api_key,
        }
        await self.exec_as_agent(
            environment,
            command=(
                "mkdir -p /tmp/libre-claw && "
                f"cat > /tmp/libre-claw/config.toml << 'LIBRECLAWCONFIG'\n"
                f"{config_text}"
                "LIBRECLAWCONFIG\n"
            ),
            env=env,
            timeout_sec=10,
        )
        await self.exec_as_agent(
            environment,
            command=(
                "/opt/libre-claw-venv/bin/libre-claw "
                "--config /tmp/libre-claw/config.toml "
                "--working-directory . run --auto-approve "
                '"$HARBOR_INSTRUCTION" '
                "2>&1 | stdbuf -oL tee /logs/agent/libre-claw.txt"
            ),
            env=env,
        )


def _benchmark_config(model: str) -> str:
    benchmark_prompt = (
        "You are running inside an isolated Terminal-Bench task container. "
        "Work autonomously until the task is complete. Inspect the environment before acting, "
        "use the available tools instead of merely describing commands, preserve unrelated files, "
        "and verify the result. Do not wait for interactive approval."
    )
    return f"""[general]
default_provider = "ollama"
default_model = {json.dumps(model)}
working_directory = "."

[agent]
max_tool_calls_per_turn = 250
system_prompt_extra = {json.dumps(benchmark_prompt)}

[permissions]
default_level = "allow"
auto_approve_read = true

[sandbox]
command_timeout = 180
allow_sudo = true
blocked_patterns = []
restrict_to_working_dir = false

[memory]
enabled = false
archive_sessions = false

[skills]
enabled = false
external_discovery_enabled = false

[petdex]
enabled = false

[automations]
enabled = false

[providers.ollama]
base_url = "https://ollama.com"
api_key_env = "OLLAMA_API_KEY"
default_model = {json.dumps(model)}
max_tokens = 16384
api_format = "ollama"
supports_tools = true
tool_mode = "auto"
think = "auto"
"""
