"""Agent core for Libre Claw.

Provides Agent class with handle_message, heartbeat_tick, and mode switching.
"""

import threading
import re
import subprocess
import shutil
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
        history = [
            msg
            for msg in self.backend.get_history()
            if not self._is_heartbeat_history_message(msg.content)
        ]
        messages = [Message(role="system", content=system_prompt)] + history
        response = self.backend.chat(
            messages=messages,
            tools=tools,
        )

        response_text = response.content or "NO_REPLY"
        if response_text.strip().upper() == "NO_REPLY":
            response_text = "Hi—I'm here. What can I help with?"

        self.backend.add_message(Message(role="assistant", content=response_text))
        self.state.message_count += 1
        self.state.last_activity = datetime.now()

        return response_text

    def handle_heartbeat(self, prompt: Optional[str] = None) -> str:
        """Handle a heartbeat poll in heartbeat mode."""
        self._set_mode(AgentMode.HEARTBEAT)
        return self._run_heartbeat_cycle(prompt=prompt)

    def _run_heartbeat_cycle(self, prompt: Optional[str] = None) -> str:
        context = self.workspace.get_context(mode="heartbeat")
        system_prompt = self._build_system_prompt(context, is_heartbeat=True)
        hb_prompt = prompt or self.config.heartbeat.prompt

        # Include HEARTBEAT.md content in the prompt
        hb_content = self.workspace.read("HEARTBEAT.md")
        if hb_content:
            hb_prompt += f"\n\n# HEARTBEAT.md\n{hb_content}"

        max_steps = max(1, int(self.config.heartbeat.proactive_iterations))
        last_prompt = hb_prompt
        last_result = "NO_REPLY"

        for step in range(max_steps):
            result = self._run_heartbeat_turn(
                prompt=last_prompt,
                context=context,
                system_prompt=system_prompt,
            )
            if not result:
                result = "NO_REPLY"

            last_result = result

            action_summary = self._execute_heartbeat_actions(result)
            if not self._should_continue_heartbeat(content=result, action_summary=action_summary):
                break

            if step + 1 < max_steps:
                last_prompt = self._build_heartbeat_followup_prompt(
                    previous_output=result,
                    action_summary=action_summary,
                )

        self.state.last_activity = datetime.now()
        return last_result

    def _run_heartbeat_turn(
        self,
        prompt: str,
        context: Dict[str, str],
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        self.backend.add_message(
            Message(role="user", content=f"[HEARTBEAT] {prompt}")
        )

        history = self.backend.get_history()
        messages = [Message(role="system", content=system_prompt)] + history

        response_text = ""
        try:
            response = self.backend.chat(messages=messages, tools=tools)
            response_text = response.content
        except Exception:
            response = self.backend.complete(
                prompt=prompt,
                system_prompt=system_prompt,
                context=context,
                tools=tools,
            )
            response_text = response.content

        if response_text is None:
            response_text = ""

        self.backend.add_message(
            Message(role="assistant", content=f"[HEARTBEAT] {response_text}")
        )
        return response_text

    @staticmethod
    def _is_heartbeat_history_message(content: str) -> bool:
        return bool(content) and content.startswith("[HEARTBEAT] ")

    def _build_heartbeat_followup_prompt(self, previous_output: str, action_summary: str) -> str:
        bits = [
            "Continue from the previous heartbeat output and take the next required action.",
            "Do not repeat the same step.",
            "If no further action is needed, respond exactly: NO_REPLY.",
        ]
        if previous_output:
            bits.append(f"Previous output:\n{previous_output}")
        if action_summary:
            bits.append(f"Actions taken:\n{action_summary}")

        bits.append(
            "When summarizing memory, keep using MEMORY_UPDATE: <text>. "
            "For changes, include one ```diff``` block or one ```bash``` block."
        )
        return "\n\n".join(bits)

    def _should_continue_heartbeat(self, content: str, action_summary: str) -> bool:
        if self._is_no_more_action(content):
            return bool(action_summary) or self._has_heartbeat_memory_hint(content)

        return True

    def _is_no_more_action(self, content: str) -> bool:
        if not content:
            return True
        normalized = content.strip().upper()
        if normalized in {"NO_REPLY", "DONE"}:
            return True
        if any(marker in normalized for marker in ("DONE", "TASK COMPLETE", "ALL_DONE")):
            return True
        if "NO_REPLY" in normalized and len(normalized) <= 20:
            return True
        return False

    def _has_heartbeat_memory_hint(self, content: str) -> bool:
        if not content:
            return False
        normalized = content.strip().upper()
        return normalized.startswith(("MEMORY_UPDATE:", "MEMORY:", "CURATE_MEMORY:"))

    def _extract_memory_updates(self, content: str) -> List[str]:
        updates: List[str] = []
        if not content:
            return updates

        lines = content.splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("MEMORY_UPDATE:"):
                body = stripped[len("MEMORY_UPDATE:"):].strip()
                if not body:
                    body_lines = []
                    for follow_line in lines[idx + 1 :]:
                        if not follow_line.strip():
                            break
                        body_lines.append(follow_line.strip())
                    body = "\n".join(body_lines).strip()
                if body:
                    updates.append(body)
            elif upper.startswith("MEMORY:"):
                body = stripped[len("MEMORY:"):].strip()
                if body:
                    updates.append(body)
            elif upper.startswith("CURATE_MEMORY:"):
                body = stripped[len("CURATE_MEMORY:"):].strip()
                if body:
                    updates.append(body)

        return updates

    def _extract_diff_blocks(self, text: str) -> List[str]:
        if not text:
            return []

        blocks: List[str] = []
        seen: set[str] = set()

        # Fenced diff blocks (```diff ... ``` or generic patch blocks).
        for match in re.finditer(r"```(?:diff|apply_patch|patch)\n([\s\S]*?)\n?```", text):
            candidate = (match.group(1) or "").strip()
            embedded = self._extract_embedded_apply_patch(candidate)
            if embedded:
                candidate = embedded
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            blocks.append(candidate)

        # Bare OpenAI-style apply_patch blocks.
        for match in re.finditer(r"\*\*\* Begin Patch[\s\S]*?\n\*\*\* End Patch", text):
            candidate = (match.group(0) or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            blocks.append(candidate)

        return blocks

    def _extract_bash_blocks(self, text: str) -> List[str]:
        if not text:
            return []

        blocks: List[str] = []
        for match in re.finditer(r"```(?:bash|sh|shell)\n([\s\S]*?)\n?```", text):
            candidate = (match.group(1) or "").strip()
            if candidate:
                blocks.append(candidate)
        return blocks

    def _apply_unified_diff(self, diff_text: str) -> str:
        if self._extract_embedded_apply_patch(diff_text) or diff_text.strip().startswith("*** Begin Patch"):
            return self._apply_apply_patch_block(diff_text)
        try:
            p = subprocess.run(
                ["git", "apply", "--whitespace=fix", "-"],
                cwd=str(self.workspace.path),
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if p.returncode == 0:
                return "Applied unified diff successfully."
            return f"Diff apply failed: {(p.stderr or p.stdout).strip()[:300]}"
        except Exception as e:
            return f"Diff apply error: {e}"

    def _extract_embedded_apply_patch(self, patch_text: str) -> Optional[str]:
        if not patch_text:
            return None

        match = re.search(r"\*\*\* Begin Patch[\s\S]*?\n\*\*\* End Patch", patch_text)
        if match:
            return match.group(0).strip()
        return None

    def _sanitize_apply_patch(self, patch_text: str) -> str:
        candidate = (patch_text or "").strip()
        if not candidate:
            return ""

        # Remove markdown-style fences if accidentally included.
        candidate = re.sub(r"^```[^\n]*\n", "", candidate).strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].rstrip()

        embedded = self._extract_embedded_apply_patch(candidate)
        if embedded:
            return f"{embedded}\n"

        if not candidate.startswith("*** Begin Patch"):
            return ""
        return f"{candidate}\n"

    def _apply_apply_patch_block(self, patch_text: str) -> str:
        apply_patch_path = shutil.which("apply_patch")
        if not apply_patch_path:
            return "Auto-apply failed: apply_patch utility not found"

        candidate = self._sanitize_apply_patch(patch_text)
        if not candidate:
            return "Auto-apply failed: not an apply_patch block"

        try:
            p = subprocess.run(
                [apply_patch_path],
                cwd=str(self.workspace.path),
                input=candidate,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if p.returncode == 0:
                return "Applied OpenAI-style patch successfully."
            return f"Patch apply failed: {(p.stderr or p.stdout).strip()[:300]}"
        except Exception as e:
            return f"Patch apply error: {e}"

    def _auto_apply_shell_script(self, script: str) -> str:
        blocked = ["rm -rf /", "mkfs", "shutdown", "reboot", "diskutil erase"]
        lowered = script.lower()
        if any(blocked in lowered for blocked in blocked):
            return "Auto-apply blocked: destructive command detected"

        try:
            result = subprocess.run(
                ["bash", "-lc", script],
                cwd=str(self.workspace.path),
                capture_output=True,
                text=True,
                timeout=90,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if result.returncode == 0:
                return f"Auto-apply command succeeded. {(out or err)[:300]}".strip()
            return f"Auto-apply command failed (exit {result.returncode}). {(err or out)[:300]}".strip()
        except Exception as e:
            return f"Auto-apply command error: {e}"

    def _execute_heartbeat_actions(self, content: str) -> str:
        outputs: List[str] = []

        updates = self._extract_memory_updates(content)
        for update in updates:
            self._remember_heartbeat_memory(update)
            outputs.append(f"MEMORY_UPDATE: {update}")

        if not self.config.heartbeat.auto_apply_actions:
            return "\n".join(outputs)

        diff_blocks = self._extract_diff_blocks(content)
        for diff_block in diff_blocks:
            outputs.append(self._apply_unified_diff(diff_block))

        shell_blocks = self._extract_bash_blocks(content)
        for block in shell_blocks:
            outputs.append(self._auto_apply_shell_script(block))

        return "\n".join(outputs)

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
