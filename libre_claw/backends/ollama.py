"""Ollama backend for Libre Claw.

Uses httpx to connect to local Ollama API.
"""

from typing import Any, Dict, List, Optional

import httpx

from .base import BackendConfig, BaseBackend, Message, Response


class OllamaBackend(BaseBackend):
    """Backend that uses Ollama for local AI completions.

    Requires Ollama server running and accessible.
    """

    def __init__(self, config: Optional[BackendConfig] = None):
        """Initialize Ollama backend.

        Args:
            config: Backend configuration
        """
        super().__init__(config)
        self._url = self.config.ollama_url
        self._model = self.config.ollama_model
        self._client = httpx.Client(timeout=300.0)

    @property
    def name(self) -> str:
        """Get the backend name."""
        return "ollama"

    @property
    def supports_tools(self) -> bool:
        """Ollama has limited tool support (depends on model)."""
        return False

    def _build_messages_format(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Convert messages to Ollama format.

        Args:
            messages: List of messages
            system_prompt: Optional system prompt

        Returns:
            List of messages in Ollama format
        """
        ollama_messages = []

        # Add system prompt if provided
        if system_prompt:
            ollama_messages.append({"role": "system", "content": system_prompt})

        # Convert messages
        for msg in messages:
            role = msg.role
            if role == "tool":
                role = "tool"  # Ollama uses tool role directly
            ollama_messages.append({"role": role, "content": msg.content})

        return ollama_messages

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """Generate a completion using Ollama.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            context: Optional context from workspace files (ignored for Ollama)
            tools: Optional tool definitions (ignored for Ollama)

        Returns:
            Response object with completion content
        """
        # Build full prompt with context
        full_prompt = prompt
        if context:
            context_str = "\n\n".join(f"# {k}\n{v}" for k, v in context.items())
            full_prompt = f"{context_str}\n\n# Current Request\n{prompt}"

        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{full_prompt}"

        # Use chat endpoint for better formatting
        messages = [Message(role="user", content=full_prompt)]
        return self.chat(messages, tools=tools)

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """Generate a chat completion using Ollama.

        Args:
            messages: List of conversation messages
            tools: Optional tool definitions (ignored for Ollama)

        Returns:
            Response object with completion content
        """
        try:
            ollama_messages = self._build_messages_format(messages)

            payload = {
                "model": self._model,
                "messages": ollama_messages,
                "stream": False,
                "options": {
                    "temperature": self.config.temperature,
                    "num_predict": self.config.max_tokens,
                },
            }

            response = self._client.post(
                f"{self._url}/api/chat",
                json=payload,
            )
            response.raise_for_status()

            data = response.json()

            return Response(
                content=data.get("message", {}).get("content", ""),
                usage=data.get("eval_count"),  # Ollama uses eval_count for token count
                model=data.get("model", self._model),
                stop_reason=data.get("done_reason"),
            )

        except httpx.HTTPStatusError as e:
            return Response(
                content=f"Error: Ollama HTTP error {e.response.status_code}: {e.response.text}",
                stop_reason="error",
            )
        except httpx.RequestError as e:
            return Response(
                content=f"Error: Failed to connect to Ollama: {e}",
                stop_reason="connection_error",
            )
        except Exception as e:
            return Response(
                content=f"Error: {str(e)}",
                stop_reason="error",
            )

    def check_available(self) -> bool:
        """Check if Ollama server is available.

        Returns:
            True if Ollama is running and responding
        """
        try:
            response = self._client.get(f"{self._url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """List available Ollama models.

        Returns:
            List of model information dictionaries
        """
        try:
            response = self._client.get(f"{self._url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return data.get("models", [])
        except Exception:
            return []

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        template: Optional[str] = None,
    ) -> Response:
        """Generate text using Ollama (non-chat endpoint).

        Args:
            prompt: Generation prompt
            system: Optional system prompt
            template: Optional prompt template

        Returns:
            Response object with generated content
        """
        try:
            payload = {
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.config.temperature,
                    "num_predict": self.config.max_tokens,
                },
            }

            if system:
                payload["system"] = system
            if template:
                payload["template"] = template

            response = self._client.post(
                f"{self._url}/api/generate",
                json=payload,
            )
            response.raise_for_status()

            data = response.json()

            return Response(
                content=data.get("response", ""),
                usage=data.get("eval_count"),
                model=data.get("model", self._model),
                stop_reason=data.get("done_reason"),
            )

        except Exception as e:
            return Response(
                content=f"Error: {str(e)}",
                stop_reason="error",
            )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __del__(self) -> None:
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass
