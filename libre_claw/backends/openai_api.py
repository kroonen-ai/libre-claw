"""OpenAI API backend for Libre Claw.

Supports API key or OAuth-style access token loaded from a JSON auth file.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .base import BackendConfig, BaseBackend, Message, Response


class OpenAIBackend(BaseBackend):
    """Backend that uses OpenAI Chat Completions API."""

    def __init__(self, config: Optional[BackendConfig] = None):
        super().__init__(config)
        self._base_url = self.config.openai_base_url.rstrip("/")
        self._model = self.config.openai_model
        self._token = self._resolve_token()
        self._client = httpx.Client(timeout=300.0)

    @property
    def name(self) -> str:
        return "openai"

    @property
    def supports_tools(self) -> bool:
        return False

    def _resolve_token(self) -> Optional[str]:
        if self.config.openai_api_key:
            return self.config.openai_api_key

        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key:
            return env_key

        if self.config.openai_auth_file:
            path = Path(self.config.openai_auth_file).expanduser()
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    return data.get("access_token") or data.get("api_key")
                except Exception:
                    return None

        return None

    def _build_messages(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if system_prompt:
            out.append({"role": "system", "content": system_prompt})
        for msg in messages:
            if msg.role in ("user", "assistant", "system"):
                out.append({"role": msg.role, "content": msg.content})
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

        return self.chat([Message(role="user", content=full_prompt)], tools=tools if tools else None)

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        if not self._token:
            return Response(
                content=(
                    "Error: OpenAI auth missing. Set OPENAI_API_KEY / LIBRE_CLAW_BACKEND__OPENAI_API_KEY "
                    "or provide openai_auth_file JSON with access_token."
                ),
                stop_reason="auth_missing",
            )

        try:
            payload = {
                "model": self._model,
                "messages": self._build_messages(messages),
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }

            res = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if res.status_code >= 400:
                return Response(content=f"Error: OpenAI HTTP {res.status_code}: {res.text}", stop_reason="error")

            data = res.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            usage = data.get("usage")

            return Response(
                content=msg.get("content", ""),
                usage=usage,
                model=data.get("model", self._model),
                stop_reason=choice.get("finish_reason"),
            )
        except Exception as e:
            return Response(content=f"Error: {e}", stop_reason="error")

    def check_available(self) -> bool:
        return bool(self._token)

    def list_models(self) -> List[str]:
        """Return available OpenAI model ids for the current auth token."""
        if not self._token:
            return []
        try:
            res = self._client.get(
                f"{self._base_url}/models",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if res.status_code >= 400:
                return []
            data = res.json()
            items = data.get("data", [])
            ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
            return sorted(ids)
        except Exception:
            return []
