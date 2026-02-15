"""Abstract base class for Libre Claw backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    """Represents a message in a conversation."""

    role: str  # "user" or "assistant"
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


@dataclass
class Response:
    """Represents a response from the backend."""

    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None
    stop_reason: Optional[str] = None


@dataclass
class BackendConfig:
    """Configuration for a backend."""

    claude_path: str = "/opt/homebrew/bin/claude"
    anthropic_api_key: Optional[str] = None
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama2"
    max_tokens: int = 4096
    temperature: float = 1.0


class BaseBackend(ABC):
    """Abstract base class for AI backends.

    All backends (Claude Code, Anthropic API, Ollama) must implement these methods.
    """

    def __init__(self, config: Optional[BackendConfig] = None):
        """Initialize backend with configuration.

        Args:
            config: Backend configuration
        """
        self.config = config or BackendConfig()
        self._conversation_history: List[Message] = []

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """Generate a completion for the given prompt.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            context: Optional context from workspace files
            tools: Optional tool definitions

        Returns:
            Response object with completion content
        """
        pass

    @abstractmethod
    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """Generate a chat completion for the given messages.

        Args:
            messages: List of conversation messages
            tools: Optional tool definitions

        Returns:
            Response object with completion content
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the backend name."""
        pass

    @property
    def supports_tools(self) -> bool:
        """Check if backend supports tool calls.

        Override in subclass if backend supports tools.
        """
        return False

    def add_message(self, message: Message) -> None:
        """Add a message to conversation history.

        Args:
            message: Message to add
        """
        self._conversation_history.append(message)

    def clear_history(self) -> None:
        """Clear conversation history."""
        self._conversation_history.clear()

    def get_history(self) -> List[Message]:
        """Get conversation history.

        Returns:
            List of messages
        """
        return self._conversation_history.copy()
