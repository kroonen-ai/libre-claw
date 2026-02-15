"""Libre Claw - Agentic AI Framework for Kroonen AI Inc.

A flexible framework wrapping Claude Code CLI as its primary backend,
with support for additional backends (Anthropic API, Ollama).
"""

__version__ = "0.1.0"
__author__ = "Robin Kroonen"

from .agent import Agent
from .backends import ClaudeCodeBackend, OllamaBackend, get_backend
from .config import Config
from .heartbeat import Heartbeat
from .memory import MemoryManager
from .workspace import Workspace

__all__ = [
    "__version__",
    "Agent",
    "ClaudeCodeBackend",
    "OllamaBackend",
    "get_backend",
    "Config",
    "Workspace",
    "Heartbeat",
    "MemoryManager",
]
