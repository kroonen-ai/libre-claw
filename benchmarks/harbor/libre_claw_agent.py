"""Harbor installed-agent adapter for evaluating Libre Claw."""

from __future__ import annotations

import json
import os
import shlex
import tomllib
from pathlib import Path, PurePosixPath

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.constants import PACKAGE_CACHE_DIR
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class LibreClawAgent(BaseInstalledAgent):
    """Install Libre Claw in a Harbor task container and run its real agent loop."""

    SUPPORTS_ATIF: bool = True
    _TRAJECTORY_FILENAME = "trajectory.json"
    _UV_PATH = "/opt/libre-claw-bin/uv"
    _VENV_PATH = "/opt/libre-claw-venv"

    def __init__(
        self,
        reasoning_effort: str | None = "auto",
        agent_timeout_sec: float | None = None,
        deadline_reserve_seconds: float = 60.0,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._reasoning_effort = reasoning_effort
        self._agent_timeout_sec = agent_timeout_sec
        self._deadline_reserve_seconds = max(5.0, deadline_reserve_seconds)

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

        outer_timeout = self._agent_timeout_sec or _task_agent_timeout(self.logs_dir)
        deadline_seconds: float | None = None
        deadline_reserve = 0.0
        if outer_timeout is not None:
            outer_reserve = min(
                self._deadline_reserve_seconds,
                max(5.0, outer_timeout * 0.1),
            )
            deadline_seconds = max(1.0, outer_timeout - outer_reserve)
            deadline_reserve = min(15.0, max(1.0, deadline_seconds * 0.05))

        command_timeout = 600
        if deadline_seconds is not None:
            command_timeout = max(30, min(600, int(deadline_seconds / 3)))
        config_text = _benchmark_config(model, command_timeout=command_timeout)
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
        deadline_options = ""
        if deadline_seconds is not None:
            deadline_options = (
                f"--deadline-seconds {deadline_seconds:.3f} "
                f"--deadline-reserve-seconds {deadline_reserve:.3f} "
            )
        else:
            self.logger.warning(
                "Could not resolve this task's Harbor timeout; internal deadline is disabled."
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
                "set -o pipefail && printf '%s' \"$HARBOR_INSTRUCTION\" | "
                f"{self._VENV_PATH}/bin/libre-claw "
                "--config /tmp/libre-claw/config.toml "
                "--working-directory . run --auto-approve "
                f"{deadline_options}"
                f"{trajectory_options}"
                "2>&1 | stdbuf -oL tee /logs/agent/libre-claw.txt"
            ),
            env=env,
        )


def _benchmark_config(model: str, *, command_timeout: int = 600) -> str:
    benchmark_prompt = (
        "You are Libre Claw running an isolated Terminal-Bench task. Complete the requested "
        "change in the current workspace. Inspect before editing, use the supplied coding tools, "
        "preserve unrelated files, and verify the result. Avoid broad environment exploration, "
        "unnecessary dependency installation, and repeated commands. Use apply_patch for compact "
        "validated edit batches and process for long-running or interactive commands. Keep enough "
        "time to run a focused verification and leave the workspace in its final state. Do not "
        "wait for approval."
    )
    tool_allowlist = [
        "read_file",
        "write_file",
        "edit_file",
        "apply_patch",
        "list_directory",
        "glob",
        "search_files",
        "git_status",
        "think",
        "bash",
        "process",
    ]
    return f"""[general]
default_provider = "ollama"
default_model = {json.dumps(model)}
working_directory = "."

[agent]
max_tool_calls_per_turn = 100
auto_compact_threshold = 0.5
compact_keep_last = 4
provider_retry_attempts = 2
provider_retry_initial_delay = 1.0
tool_allowlist = {json.dumps(tool_allowlist)}
system_prompt = {json.dumps(benchmark_prompt)}
system_prompt_extra = ""

[permissions]
default_level = "allow"
auto_approve_read = true

[sandbox]
command_timeout = {command_timeout}
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


def _task_agent_timeout(logs_dir: Path) -> float | None:
    """Read the task timeout Harbor resolved into its local package cache."""
    try:
        trial_dir = logs_dir.parent
        trial_config = json.loads((trial_dir / "config.json").read_text(encoding="utf-8"))
        if not isinstance(trial_config, dict):
            return None
        task = trial_config.get("task", {})
        if not isinstance(task, dict):
            return None
        task_name = task.get("name")
        task_ref = task.get("ref")
        if not isinstance(task_name, str) or not isinstance(task_ref, str):
            return None

        task_name_path = PurePosixPath(task_name)
        if task_name_path.is_absolute():
            return None
        parts = task_name_path.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            return None
        digest = task_ref.removeprefix("sha256:")
        if not digest or "/" in digest or "\\" in digest:
            return None

        cache_root = PACKAGE_CACHE_DIR.resolve()
        task_path = cache_root.joinpath(*parts, digest, "task.toml").resolve()
        if not task_path.is_relative_to(cache_root):
            return None
        task_config = tomllib.loads(task_path.read_text(encoding="utf-8"))
        agent_config = task_config.get("agent", {})
        if not isinstance(agent_config, dict):
            return None
        timeout = agent_config.get("timeout_sec")
        if not isinstance(timeout, int | float) or timeout <= 0:
            return None

        multiplier = 1.0
        lock_path = trial_dir / "lock.json"
        if lock_path.exists():
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            if not isinstance(lock, dict):
                return None
            configured_multiplier = lock.get("timeout_multiplier")
            if isinstance(configured_multiplier, int | float) and configured_multiplier > 0:
                multiplier = float(configured_multiplier)
        return float(timeout) * multiplier
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return None
