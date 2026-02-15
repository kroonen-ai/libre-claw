"""Codex CLI backend for Libre Claw.

Uses local Codex login session (ChatGPT OAuth) via `codex exec`.
"""

import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional

from .base import BackendConfig, BaseBackend, Message, Response


class CodexCLIBackend(BaseBackend):
    def __init__(self, config: Optional[BackendConfig] = None):
        super().__init__(config)
        self._codex_path = self.config.codex_path

    @property
    def name(self) -> str:
        return "codex-cli"

    @property
    def supports_tools(self) -> bool:
        return False

    def _build_prompt(self, prompt: str, system_prompt: Optional[str], context: Optional[Dict[str, str]]) -> str:
        parts = []
        if system_prompt:
            parts.append(f"<system>\n{system_prompt}\n</system>")
        if context:
            for filename, content in context.items():
                parts.append(f"<file name=\"{filename}\">\n{content}\n</file>")
        parts.append(prompt)
        return "\n\n".join(parts)

    def complete_with_progress(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        add_dirs: Optional[List[str]] = None,
    ) -> Response:
        full_prompt = self._build_prompt(prompt, system_prompt, context)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=True) as out:
            cmd = [
                self._codex_path,
                "exec",
                "--skip-git-repo-check",
                "--output-last-message",
                out.name,
                "--json",
            ]
            if self.config.codex_model:
                cmd.extend(["-m", self.config.codex_model])
            if add_dirs:
                for d in add_dirs:
                    cmd.extend(["--add-dir", d])
            cmd.append(full_prompt)

            try:
                if progress_callback:
                    progress_callback("starting codex")

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if proc.stdout:
                    for line in proc.stdout:
                        text = (line or "").strip()
                        if not text:
                            continue
                        if progress_callback:
                            progress_callback(f"codex: {text[:120]}")

                return_code = proc.wait(timeout=300)
                stderr = proc.stderr.read().strip() if proc.stderr else ""

                if return_code != 0:
                    return Response(content=f"Error: Codex exec failed: {stderr}", stop_reason="error")

                out.seek(0)
                content = out.read().decode("utf-8").strip()
                return Response(content=content, model="codex-cli", stop_reason="end_turn")
            except FileNotFoundError:
                return Response(content=f"Error: Codex CLI not found at {self._codex_path}", stop_reason="error")
            except Exception as e:
                return Response(content=f"Error: {e}", stop_reason="error")

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        return self.complete_with_progress(prompt, system_prompt=system_prompt, context=context)

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        parts = []
        system = None
        for msg in messages:
            if msg.role == "system":
                system = msg.content
            elif msg.role == "user":
                parts.append(f"Human: {msg.content}")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}")

        return self.complete(prompt="\n\n".join(parts), system_prompt=system)

    def check_available(self) -> bool:
        try:
            status = subprocess.run([self._codex_path, "login", "status"], capture_output=True, text=True, timeout=10)
            return status.returncode == 0
        except Exception:
            return False
