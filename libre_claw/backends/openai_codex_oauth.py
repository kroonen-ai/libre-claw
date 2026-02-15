"""Standalone OpenAI Codex OAuth backend.

Uses OAuth access token from auth-profiles.json and calls ChatGPT Codex Responses API
(`https://chatgpt.com/backend-api/codex/responses`) directly.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .base import BackendConfig, BaseBackend, Message, Response


class OpenAICodexOAuthBackend(BaseBackend):
    def __init__(self, config: Optional[BackendConfig] = None):
        super().__init__(config)
        configured_base = (self.config.openai_codex_base_url or "").rstrip("/")
        # Auto-heal legacy misconfigurations from earlier builds.
        if (not configured_base) or ("api.openai.com" in configured_base) or configured_base.endswith("/v1"):
            configured_base = "https://chatgpt.com/backend-api"
        self._base_url = configured_base
        self._model = self.config.openai_codex_model
        self._access = self._resolve_access_token()
        self._client = httpx.Client(timeout=300.0)

    @property
    def name(self) -> str:
        return "openai-codex-oauth"

    @property
    def supports_tools(self) -> bool:
        return False

    def _resolve_access_token(self) -> Optional[str]:
        profiles_path = Path(
            self.config.openai_codex_auth_profiles_file
            or "~/.openclaw/agents/main/agent/auth-profiles.json"
        ).expanduser()
        if not profiles_path.exists():
            return None

        try:
            data = json.loads(profiles_path.read_text())
            profiles = data.get("profiles", {})
            profile_name = self.config.openai_codex_profile or "openai-codex:default"
            profile = profiles.get(profile_name) or {}
            if profile.get("provider") != "openai-codex":
                return None
            return profile.get("access")
        except Exception:
            return None

    def _build_input(self, messages: List[Message], system_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if system_prompt:
            out.append(
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                }
            )
        for msg in messages:
            if msg.role not in ("user", "assistant", "system"):
                continue
            out.append(
                {
                    "role": msg.role,
                    "content": [{"type": "input_text", "text": msg.content}],
                }
            )
        return out

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        full_prompt = prompt
        if context:
            ctx = "\n\n".join(f"# {k}\n{v}" for k, v in context.items())
            full_prompt = f"{ctx}\n\n# Current Request\n{prompt}"
        return self.chat([Message(role="user", content=full_prompt)], tools=tools)

    def chat(self, messages: List[Message], tools: Optional[List[Dict[str, Any]]] = None) -> Response:
        if not self._access:
            return Response(
                content=(
                    "Error: openai-codex oauth token missing. "
                    "Run 'codex login' and ensure auth-profiles.json contains openai-codex:default."
                ),
                stop_reason="auth_missing",
            )

        # OpenClaw-compatible codex endpoint semantics
        endpoint = f"{self._base_url}/codex/responses"
        model_id = self._model.split("/", 1)[1] if self._model.startswith("openai-codex/") else self._model
        payload = {
            "model": model_id,
            "store": False,
            "stream": True,
            "instructions": "You are helpful and concise.",
            "input": self._build_input(messages),
        }

        try:
            content_parts: List[str] = []
            with self._client.stream(
                "POST",
                endpoint,
                headers={
                    "Authorization": f"Bearer {self._access}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as res:
                if res.status_code >= 400:
                    body = res.read().decode("utf-8", errors="replace")
                    return Response(content=f"Error: OpenAI Codex HTTP {res.status_code}: {body}", stop_reason="error")

                for line in res.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_line = line[6:]
                        try:
                            event = json.loads(data_line)
                        except Exception:
                            continue

                        et = event.get("type")
                        if et == "response.output_text.delta":
                            delta = event.get("delta")
                            if delta:
                                content_parts.append(str(delta))
                        elif et == "response.output_text.done" and not content_parts:
                            txt = event.get("text")
                            if txt:
                                content_parts.append(str(txt))
                        elif et in ("error", "response.failed"):
                            msg = event.get("message") or event.get("error") or event
                            return Response(content=f"Error: {msg}", stop_reason="error")

            content = "".join(content_parts).strip()
            if not content:
                return Response(content="Error: Codex returned no assistant text", stop_reason="error")

            return Response(
                content=content,
                model=self._model,
                stop_reason="stop",
            )
        except Exception as e:
            return Response(content=f"Error: {e}", stop_reason="error")

    def list_models(self) -> List[str]:
        return [
            "openai-codex/gpt-5.1",
            "openai-codex/gpt-5.1-codex-max",
            "openai-codex/gpt-5.1-codex-mini",
            "openai-codex/gpt-5.2",
            "openai-codex/gpt-5.2-codex",
            "openai-codex/gpt-5.3-codex",
            "openai-codex/gpt-5.3-codex-spark",
        ]

    def check_available(self) -> bool:
        return bool(self._access)
