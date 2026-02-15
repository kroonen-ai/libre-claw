"""Agent core for Libre Claw.

Provides Agent class with handle_message, heartbeat_tick, and mode switching.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .backends import BackendConfig, BaseBackend, Message, get_backend
from .config import Config
from .heartbeat import Heartbeat
from .memory import MemoryManager
from .workspace import Workspace


class AgentMode(Enum):
    DIRECT = "direct"
    HEARTBEAT = "heartbeat"


@dataclass
class AgentState:
    mode: AgentMode = AgentMode.DIRECT
    session_id: str = ""
    started_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    message_count: int = 0


class Agent:
    """Main agent class for Libre Claw."""

    def __init__(
        self,
        backend: Optional[BaseBackend] = None,
        workspace: Optional[Workspace] = None,
        config: Optional[Config] = None,
        memory: Optional[MemoryManager] = None,
    ):
        self.config = config or Config()

        if backend is None:
            backend = get_backend(
                self.config.backend.type,
                BackendConfig(
                    claude_path=self.config.backend.claude_path,
                    anthropic_api_key=self.config.backend.anthropic_api_key,
                    ollama_url=self.config.backend.ollama_url,
                    ollama_model=self.config.backend.ollama_model,
                ),
            )
        self.backend = backend

        if workspace is None:
            workspace = Workspace(path=self.config.workspace.path, config=self.config)
        self.workspace = workspace

        if memory is None and self.config.memory.enabled:
            memory = MemoryManager(
                url=self.config.memory.chromadb_url,
                collection_name="libre_claw_memories",
            )
        self.memory = memory

        self.heartbeat = Heartbeat(
            workspace=self.workspace,
            config=self.config.heartbeat,
            on_tick=self._on_heartbeat_tick,
        )

        self.state = AgentState(
            session_id=str(uuid.uuid4()),
            started_at=datetime.now(),
        )

    def handle_message(
        self,
        message: str,
        context: Optional[Dict[str, str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Handle an incoming message in direct mode."""
        self._set_mode(AgentMode.DIRECT)

        # Load mode-aware context
        if context is None:
            context = self.workspace.get_context(mode="direct")

        system_prompt = self._build_system_prompt(context, is_heartbeat=False)
        self.backend.add_message(Message(role="user", content=message))

        response = self.backend.complete(
            prompt=message,
            system_prompt=system_prompt,
            context=context,
            tools=tools,
        )

        self.backend.add_message(Message(role="assistant", content=response.content))
        self.state.message_count += 1
        self.state.last_activity = datetime.now()

        return response.content

    def handle_heartbeat(self, prompt: Optional[str] = None) -> str:
        """Handle a heartbeat poll in heartbeat mode."""
        self._set_mode(AgentMode.HEARTBEAT)

        context = self.workspace.get_context(mode="heartbeat")
        hb_prompt = prompt or self.config.heartbeat.prompt

        # Include HEARTBEAT.md content in the prompt
        hb_content = self.workspace.read("HEARTBEAT.md")
        if hb_content:
            hb_prompt += f"\n\n# HEARTBEAT.md\n{hb_content}"

        system_prompt = self._build_system_prompt(context, is_heartbeat=True)

        response = self.backend.complete(
            prompt=hb_prompt,
            system_prompt=system_prompt,
            context=context,
        )

        self.state.last_activity = datetime.now()
        return response.content

    def heartbeat_tick(self) -> str:
        return self.handle_heartbeat()

    def _on_heartbeat_tick(self) -> Any:
        return self.handle_heartbeat()

    def _set_mode(self, mode: AgentMode) -> None:
        if self.state.mode != mode:
            self.state.mode = mode

    def _build_system_prompt(
        self,
        context: Dict[str, str],
        is_heartbeat: bool = False,
    ) -> str:
        """Build system prompt from workspace context."""
        parts = []

        if "SOUL.md" in context:
            parts.append(f"# SOUL\n{context['SOUL.md']}")
        if "AGENTS.md" in context:
            parts.append(f"# RULES\n{context['AGENTS.md']}")
        if "USER.md" in context:
            parts.append(f"# USER\n{context['USER.md']}")
        if "IDENTITY.md" in context:
            parts.append(f"# IDENTITY\n{context['IDENTITY.md']}")
        if "MEMORY.md" in context:
            parts.append(f"# MEMORY\n{context['MEMORY.md']}")
        if "HEARTBEAT.md" in context:
            parts.append(f"# HEARTBEAT\n{context['HEARTBEAT.md']}")

        # Daily note context
        for key, val in context.items():
            if key.startswith("memory/"):
                parts.append(f"# {key}\n{val}")

        if is_heartbeat:
            parts.append(
                "\n# MODE\nYou are in HEARTBEAT MODE. Be proactive. Maintain systems. "
                "Follow HEARTBEAT.md checklist. Do not infer or repeat old tasks."
            )
        else:
            parts.append(
                "\n# MODE\nYou are in DIRECT MODE. Follow RULE #0: single task discipline. "
                "Do only what the user asks. Nothing more."
            )

        return "\n\n".join(parts)

    async def start_heartbeat(self) -> None:
        await self.heartbeat.start()

    async def stop_heartbeat(self) -> None:
        await self.heartbeat.stop()

    def search_memory(
        self,
        query: str,
        memory_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        if not self.memory:
            return []
        return self.memory.recall(query=query, memory_type=memory_type, limit=limit)

    def remember(
        self,
        content: str,
        memory_type: str = "general",
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
    ) -> bool:
        if not self.memory:
            return False
        return self.memory.remember(
            content=content, memory_type=memory_type, importance=importance, tags=tags,
        )

    def get_session_info(self) -> Dict[str, Any]:
        return {
            "session_id": self.state.session_id,
            "mode": self.state.mode.value,
            "started_at": self.state.started_at.isoformat() if self.state.started_at else None,
            "last_activity": self.state.last_activity.isoformat() if self.state.last_activity else None,
            "message_count": self.state.message_count,
            "backend": self.backend.name,
            "workspace": str(self.workspace.path),
        }
