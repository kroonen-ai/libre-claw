"""Agent core for Libre Claw.

Provides Agent class with handle_message, heartbeat_tick, and mode switching.
"""

import threading
import time
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
                    codex_path=self.config.backend.codex_path,
                    codex_model=self.config.backend.codex_model,
                    openai_codex_auth_profiles_file=self.config.backend.openai_codex_auth_profiles_file,
                    openai_codex_profile=self.config.backend.openai_codex_profile,
                    openai_codex_model=self.config.backend.openai_codex_model,
                    openai_codex_base_url=self.config.backend.openai_codex_base_url,
                    anthropic_api_key=self.config.backend.anthropic_api_key,
                    anthropic_auth_file=self.config.backend.anthropic_auth_file,
                    anthropic_model=self.config.backend.anthropic_model,
                    anthropic_base_url=self.config.backend.anthropic_base_url,
                    openai_api_key=self.config.backend.openai_api_key,
                    openai_auth_file=self.config.backend.openai_auth_file,
                    openai_model=self.config.backend.openai_model,
                    openai_base_url=self.config.backend.openai_base_url,
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

        self._proactive_thread: Optional[threading.Thread] = None
        self._proactive_stop = threading.Event()

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

        # Use chat history so context window actually accumulates across turns.
        history = self.backend.get_history()
        messages = [Message(role="system", content=system_prompt)] + history
        response = self.backend.chat(
            messages=messages,
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

        processed = self._process_heartbeat_response(response.content)
        response.content = processed
        self.state.last_activity = datetime.now()
        return response.content

    def _process_heartbeat_response(self, content: str) -> str:
        if not content:
            return content

        normalized = content.strip()
        if normalized.upper() == "NO_REPLY":
            return "NO_REPLY"

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return content

        first_line = lines[0]
        directives = ("MEMORY_UPDATE:", "MEMORY:", "CURATE_MEMORY:")
        for marker in directives:
            marker_len = len(marker)
            if first_line.upper().startswith(marker):
                memory_text = first_line[marker_len:].strip()
                if not memory_text and len(lines) > 1:
                    memory_text = "\n".join(lines[1:]).strip()
                if memory_text:
                    self._remember_heartbeat_memory(memory_text)
                # If only memory directive exists, keep response concise.
                return "\n".join(lines[1:]).strip() if len(lines) > 1 else "MEMORY_UPDATE"

        return content

    def heartbeat_tick(self) -> str:
        return self.handle_heartbeat()

    def _on_heartbeat_tick(self) -> Any:
        return self.handle_heartbeat()

    def _remember_heartbeat_memory(self, text: str) -> None:
        note = text.strip()
        if not note:
            return

        timestamp = datetime.now().isoformat()
        self.workspace.append("MEMORY.md", f"- [{timestamp}] {note}")

        if self.memory:
            try:
                self.memory.remember(
                    content=note,
                    memory_type="heartbeat",
                    importance=0.7,
                    tags=["heartbeat", "auto"],
                )
            except Exception:
                pass

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
            parts.append(
                "\n# DIRECT MODE HARD RULES\n"
                "- If user asks to update profile/context files and required fields are missing, ask concise targeted follow-up questions first.\n"
                "- Never replace unknown user facts with 'Not provided' unless the user explicitly asked for placeholders.\n"
                "- Never switch to jokes/sarcasm when the user asks for concrete updates."
            )

        return "\n\n".join(parts)

    async def start_heartbeat(self) -> None:
        await self.heartbeat.start()

    async def stop_heartbeat(self) -> None:
        await self.heartbeat.stop()

    def _record_heartbeat_state(self, status: str, details: str = "") -> None:
        state = self.workspace.get_heartbeat_state()
        state["total_runs"] = state.get("total_runs", 0) + 1
        state["last_run_at"] = datetime.now().isoformat()
        state["last_status"] = status
        if details:
            state["last_status_details"] = details[:2000]

        if status in {"FAILED", "ERROR", "TIMEOUT"}:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        else:
            state["consecutive_failures"] = 0

        if status == "NO_REPLY":
            state["consecutive_no_reply"] = state.get("consecutive_no_reply", 0) + 1
        else:
            state["consecutive_no_reply"] = 0

        self.workspace.save_heartbeat_state(state)

    @property
    def proactive_running(self) -> bool:
        return self._proactive_thread is not None and self._proactive_thread.is_alive()

    def start_proactive(self) -> None:
        """Start proactive heartbeat loop in background thread."""
        if self.proactive_running:
            return

        self._proactive_stop.clear()
        self._proactive_thread = threading.Thread(target=self._proactive_loop, daemon=True)
        self._proactive_thread.start()

    def stop_proactive(self) -> None:
        """Stop proactive background loop."""
        self._proactive_stop.set()
        if self._proactive_thread and self._proactive_thread.is_alive():
            self._proactive_thread.join(timeout=2.0)

    def _proactive_loop(self) -> None:
        interval = max(5, int(self.config.heartbeat.interval_seconds))
        while not self._proactive_stop.is_set():
            try:
                # Run only when idle for at least one interval
                if self.state.last_activity:
                    idle_for = (datetime.now() - self.state.last_activity).total_seconds()
                    if idle_for < interval:
                        time.sleep(1)
                        continue

                result = self.handle_heartbeat()
                status = "NO_REPLY" if (result or "").strip().upper() == "NO_REPLY" else "SUCCESS"
                self.workspace.update_heartbeat_audit(status, f"proactive tick: {result or status}")
                self._record_heartbeat_state(status=status, details=str(result or status))
            except Exception as e:
                self.workspace.update_heartbeat_audit("FAILED", f"proactive tick error: {e}")
                self._record_heartbeat_state(status="FAILED", details=str(e))

            # Sleep in short chunks for responsive stop
            slept = 0
            while slept < interval and not self._proactive_stop.is_set():
                time.sleep(1)
                slept += 1

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

    def switch_backend(self, backend_type: str) -> None:
        """Switch active backend at runtime."""
        self.backend = get_backend(
            backend_type,
            BackendConfig(
                claude_path=self.config.backend.claude_path,
                codex_path=self.config.backend.codex_path,
                codex_model=self.config.backend.codex_model,
                openai_codex_auth_profiles_file=self.config.backend.openai_codex_auth_profiles_file,
                openai_codex_profile=self.config.backend.openai_codex_profile,
                openai_codex_model=self.config.backend.openai_codex_model,
                openai_codex_base_url=self.config.backend.openai_codex_base_url,
                anthropic_api_key=self.config.backend.anthropic_api_key,
                anthropic_auth_file=self.config.backend.anthropic_auth_file,
                anthropic_model=self.config.backend.anthropic_model,
                anthropic_base_url=self.config.backend.anthropic_base_url,
                openai_api_key=self.config.backend.openai_api_key,
                openai_auth_file=self.config.backend.openai_auth_file,
                openai_model=self.config.backend.openai_model,
                openai_base_url=self.config.backend.openai_base_url,
                ollama_url=self.config.backend.ollama_url,
                ollama_model=self.config.backend.ollama_model,
            ),
        )
        self.config.backend.type = backend_type

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
