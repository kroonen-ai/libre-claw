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

    SUPPORTS_ATIF: bool = True
    _TRAJECTORY_FILENAME = "trajectory.json"
    _UV_PATH = "/opt/libre-claw-bin/uv"
    _VENV_PATH = "/opt/libre-claw-venv"

    def __init__(self, reasoning_effort: str | None = "auto", *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._reasoning_effort = reasoning_effort

    @staticmethod
    def name() -> str:
        return "libre-claw"

    def version(self) -> str | None:
        return self._version

    def get_version_command(self) -> str | None:
        return f"{self._VENV_PATH}/bin/libre-claw --version"

    def populate_context_post_run(self, context: AgentContext) -> None:
        trajectory_path = self.logs_dir / self._TRAJECTORY_FILENAME
        if not trajectory_path.exists():
            self.logger.warning("Libre Claw ATIF trajectory was not produced.")
            return
        try:
            payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.error("Could not read Libre Claw ATIF trajectory: %s", exc)
            return
        metrics = payload.get("final_metrics", {})
        if not isinstance(metrics, dict):
            return
        context.n_input_tokens = int(metrics.get("total_prompt_tokens") or 0)
        context.n_output_tokens = int(metrics.get("total_completion_tokens") or 0)
        context.n_cache_tokens = int(metrics.get("total_cached_tokens") or 0)
        cost = metrics.get("total_cost_usd")
        context.cost_usd = float(cost) if isinstance(cost, int | float) else None

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
                "mkdir -p /opt/libre-claw-bin && "
                "curl -LsSf https://astral.sh/uv/install.sh | "
                "env UV_UNMANAGED_INSTALL=/opt/libre-claw-bin sh && "
                f"{self._UV_PATH} python install 3.11 && "
                f"{self._UV_PATH} venv --python 3.11 {self._VENV_PATH} && "
                f"{self._UV_PATH} pip install --python {self._VENV_PATH}/bin/python "
                f"{shlex.quote(package_url)} && "
                f"{self._VENV_PATH}/bin/libre-claw --version"
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
        version = self._version or "0.1.0"
        trajectory_options = (
            f"--trajectory-path /logs/agent/{self._TRAJECTORY_FILENAME} "
            f"--trajectory-agent-version {shlex.quote(version)} "
        )
        if self._reasoning_effort:
            trajectory_options += (
                "--trajectory-reasoning-effort "
                f"{shlex.quote(self._reasoning_effort)} "
            )
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
                f"{self._VENV_PATH}/bin/libre-claw "
                "--config /tmp/libre-claw/config.toml "
                "--working-directory . run --auto-approve "
                f"{trajectory_options}"
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
