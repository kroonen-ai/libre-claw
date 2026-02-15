"""Agent core for Libre Claw.

Provides Agent class with handle_message, heartbeat_tick, and mode switching.
"""

import threading
import re
import subprocess
import shutil
import json
import time
import uuid
import textwrap
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

    HEARTBEAT_BOOTSTRAP_CONTEXT_FILES = [
        "SOUL.md",
        "USER.md",
        "IDENTITY.md",
        "AGENTS.md",
        "HEARTBEAT.md",
        "MEMORY.md",
    ]
    HEARTBEAT_BOOTSTRAP_LINK_FILES = [
        "README.md",
        "HEARTBEAT-AUDIT.md",
    ]
    HEARTBEAT_BOOTSTRAP_LOG = "HEARTBEAT-BOOTSTRAP.md"

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

        self._append_heartbeat_bootstrap_log()

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
        result = self._run_heartbeat_cycle(prompt=prompt)

        trace_status = self._classify_heartbeat_trace_status(result)
        if trace_status:
            self._append_heartbeat_trace(trace_status, result)

        return result

    def _run_heartbeat_cycle(self, prompt: Optional[str] = None) -> str:
        context = self._hydrate_heartbeat_bootstrap_context(self.workspace.get_context(mode="heartbeat"))
        system_prompt = self._build_system_prompt(context, is_heartbeat=True)
        hb_prompt = prompt or self.config.heartbeat.prompt

        max_steps = max(1, int(self.config.heartbeat.proactive_iterations))
        last_prompt = hb_prompt
        last_result = "NO_REPLY"
        step_logs: List[str] = []
        last_plan: Dict[str, Any] = {
            "done": False,
            "next_step": "",
            "expected_state_change": "",
            "verification_check": "",
        }

        for step in range(max_steps):
            result = self._run_heartbeat_turn(
                prompt=last_prompt,
                context=context,
                system_prompt=system_prompt,
            )
            if not result:
                result = "NO_REPLY"

            parsed_plan = self._parse_heartbeat_plan(result)
            if not parsed_plan:
                parsed_plan = {"done": False, "next_step": "", "expected_state_change": "", "verification_check": ""}
            last_plan = parsed_plan
            last_result = result

            action_summary = self._execute_heartbeat_actions(result)
            should_continue = self._should_continue_heartbeat(
                plan=last_plan,
                action_summary=action_summary,
                raw_content=result,
            )
            step_logs.append(
                self._build_heartbeat_step_log(
                    step=step + 1,
                    max_steps=max_steps,
                    plan=last_plan,
                    model_output=result,
                    action_summary=action_summary,
                    continue_after=should_continue,
                )
            )

            if not should_continue:
                break

            if should_continue and step + 1 < max_steps:
                last_prompt = self._build_heartbeat_followup_prompt(
                    previous_output=result,
                    action_summary=action_summary,
                    plan=last_plan,
                    step=step + 1,
                    max_steps=max_steps,
                )

        self.state.last_activity = datetime.now()
        if step_logs:
            trace = "\n\n".join(step_logs)
            if last_result.strip().upper() == "NO_REPLY":
                return "\n".join(["NO_REPLY", "", "### Heartbeat execution trace", trace]).strip()
            return "\n".join([last_result, "", "### Heartbeat execution trace", trace]).strip()

        if self._is_heartbeat_plan_complete(last_plan) and action_summary == "":
            return "NO_REPLY"
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

    def _build_heartbeat_followup_prompt(
        self,
        previous_output: str,
        action_summary: str,
        plan: Optional[Dict[str, Any]] = None,
        step: int = 1,
        max_steps: int = 1,
    ) -> str:
        bits = [
            "Continue from the previous heartbeat output and take the next required action.",
            "Do not repeat the same step.",
            "Return **only** a JSON object with keys: "
            "next_step, expected_state_change, verification_check, done.",
        ]
        if previous_output:
            bits.append(f"Previous output:\n{previous_output}")
        if action_summary:
            bits.append(f"Actions taken:\n{action_summary}")
        if plan:
            bits.append(
                "Observed plan:\n"
                f"{json.dumps(plan, indent=2)}"
            )

        bits.append(f"Heartbeat loop progress: step {step} of {max_steps}.")

        bits.append(
            "If the task is complete and verified, set done: true. "
            "If work remains, set done: false and set next_step with the next action. "
            "Expected_state_change should describe concrete workspace/state impact. "
            "Verification_check should describe exactly what to verify before moving on."
        )

        return "\n\n".join(bits)

    def _build_heartbeat_step_log(
        self,
        step: int,
        max_steps: int,
        plan: Dict[str, Any],
        model_output: str,
        action_summary: str,
        continue_after: bool,
    ) -> str:
        done = self._is_heartbeat_plan_complete(plan)
        next_step = str(plan.get("next_step", "") or "").strip() or "(none)"
        expected_state_change = str(plan.get("expected_state_change", "") or "").strip() or "(none)"
        verification_check = str(plan.get("verification_check", "") or "").strip() or "(none)"

        apply_status, apply_next_action, apply_details = self._parse_heartbeat_action_contract(action_summary)
        model_output_snippet = (model_output or "").strip().replace("\n", " ")[:180]
        if not model_output_snippet:
            model_output_snippet = "(no model output text)"
        if not apply_next_action:
            apply_next_action = "(none)"
        if apply_details:
            apply_details = f"; details={apply_details}"

        execute_lines = [line for line in action_summary.splitlines() if line.strip()]
        if execute_lines:
            execute_block = "\n".join(f"  - {line}" for line in execute_lines)
            execute_line = f"- EXECUTE:\n{execute_block}"
        else:
            execute_line = "- EXECUTE: (no actions)"

        return (
            f"#### Step {step}/{max_steps}\n"
            f"- PLAN: done={done}, next_step={next_step}, expected_state_change={expected_state_change}, verification_check={verification_check}\n"
            f"{execute_line}\n"
            f"- VERIFY: contract={apply_status}, next_action={apply_next_action}{apply_details}, continue={continue_after}, "
            f"model_output_snippet={model_output_snippet}"
        )

    @staticmethod
    def _parse_heartbeat_action_contract(action_summary: str) -> tuple[str, str, str]:
        """
        Parse the highest-priority action contract from a heartbeat action summary.
        Returns (status, next_action, details).
        """
        if not action_summary:
            return "NO_ACTION", "", ""

        lines = [line.strip() for line in action_summary.splitlines() if line.strip()]
        status_precedence = {"FAILED_PERM": 3, "RETRYABLE": 2, "APPLIED": 1, "MEMORY_UPDATE": 1, "NO_ACTION": 0}
        best_status = "NO_ACTION"
        best_score = 0
        best_next_action = ""
        best_details = ""

        def parse_contract_parts(line: str) -> tuple[str, str]:
            next_action = ""
            details = ""
            next_match = re.search(r"next_action\s*=\s*([^;]+)", line, flags=re.IGNORECASE)
            if next_match:
                next_action = next_match.group(1).strip()
            details_match = re.search(r"details\s*=\s*(.+)", line, flags=re.IGNORECASE)
            if details_match:
                details = details_match.group(1).strip()
            return next_action, details

        for line in lines:
            if line.upper().startswith("MEMORY_UPDATE:"):
                status = "MEMORY_UPDATE"
                next_action = "No write action required."
                details = line[len("MEMORY_UPDATE:"):].strip()
            else:
                upper = line.upper()
                if "FAILED_PERM" in upper:
                    status = "FAILED_PERM"
                elif "RETRYABLE" in upper:
                    status = "RETRYABLE"
                elif "APPLIED" in upper:
                    status = "APPLIED"
                elif "AUTO-APPLY COMMAND SUCCEEDED" in upper:
                    status = "APPLIED"
                elif "AUTO-APPLY COMMAND FAILED" in upper:
                    status = "RETRYABLE"
                else:
                    status = "UNKNOWN"

                next_action, details = parse_contract_parts(line)

            score = status_precedence.get(status, 0)
            if score > best_score:
                best_status = status
                best_score = score
                best_next_action = next_action
                best_details = details

            # Keep last-known context for equal score if richer than current.
            elif score == best_score and score > 0 and (not best_details and details):
                best_next_action = next_action
                best_details = details

        return best_status, best_next_action.strip(), best_details.strip()

    @staticmethod
    def _extract_action_contract_status(action_summary: str) -> str:
        return Agent._parse_heartbeat_action_contract(action_summary)[0]

    def _should_continue_heartbeat(
        self,
        plan: Dict[str, Any],
        action_summary: str,
        raw_content: str,
    ) -> bool:
        if self._is_heartbeat_plan_complete(plan):
            return False

        if self._has_heartbeat_memory_hint(raw_content):
            return True

        if action_summary:
            return True

        # Keep running until explicit done is returned or MAX_STEPS is hit.
        # If structured output is missing/incomplete, do another follow-up
        # attempt instead of failing early.
        return True

    def _is_heartbeat_plan_complete(self, plan: Dict[str, Any]) -> bool:
        done = plan.get("done")
        if isinstance(done, bool):
            return bool(done)
        if isinstance(done, str):
            return done.strip().lower() in {"true", "yes", "1", "complete", "done"}
        return False

    def _parse_heartbeat_plan(self, content: str) -> Dict[str, Any]:
        if not content:
            return {"done": False, "next_step": "", "expected_state_change": "", "verification_check": ""}

        parsed = self._extract_heartbeat_json_plan(content)
        if not parsed:
            return {
                "done": False,
                "next_step": "",
                "expected_state_change": "",
                "verification_check": "",
            }

        done = parsed.get("done")
        return {
            "done": bool(done) if isinstance(done, bool) else str(done).strip().lower() in {"true", "yes", "1", "done", "complete", "completed"}
            if done is not None
            else False,
            "next_step": (parsed.get("next_step", "") or "").strip() if isinstance(parsed, dict) else "",
            "expected_state_change": (parsed.get("expected_state_change", "") or "").strip()
            if isinstance(parsed, dict)
            else "",
            "verification_check": (parsed.get("verification_check", "") or "").strip() if isinstance(parsed, dict) else "",
        }

    def _extract_heartbeat_json_plan(self, content: str) -> Optional[Dict[str, Any]]:
        if not content:
            return None

        for match in re.finditer(r"```(?:json)?\s*\n([\s\S]*?)\n```", content, flags=re.IGNORECASE):
            payload = (match.group(1) or "").strip()
            plan = self._try_parse_json(payload)
            if plan is not None:
                return plan

        text = content
        start = 0
        while True:
            idx = text.find("{", start)
            if idx == -1:
                return None

            depth = 0
            in_string = False
            escaped = False
            close_idx = -1
            for i in range(idx, len(text)):
                ch = text[i]
                if in_string:
                    if escaped:
                        escaped = False
                        continue
                    if ch == "\\":
                        escaped = True
                        continue
                    if ch == '"':
                        in_string = False
                    continue

                if ch == '"':
                    in_string = True
                    continue

                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        close_idx = i + 1
                        break

            if close_idx > idx:
                payload = text[idx:close_idx]
                plan = self._try_parse_json(payload)
                if plan is not None:
                    return plan

            start = idx + 1

        return None

    @staticmethod
    def _try_parse_json(payload: str) -> Optional[Dict[str, Any]]:
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

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

        raw_diff = self._extract_raw_unified_diff_block(text)
        if raw_diff:
            if raw_diff not in seen:
                seen.add(raw_diff)
                blocks.append(raw_diff)

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

    @staticmethod
    def _extract_raw_unified_diff_block(text: str) -> Optional[str]:
        if not text:
            return None

        marker = text.find("diff --git")
        if marker < 0:
            return None

        candidate = text[marker:].strip()
        if not candidate:
            return None
        # If there are multiple patch blocks, keep the first one only.
        next_marker = candidate.find("\n\ndiff --git", 1)
        if next_marker > 0:
            candidate = candidate[:next_marker]
        return candidate

    def _apply_unified_diff(self, diff_text: str) -> str:
        normalized = self._normalize_unified_diff(diff_text or "")
        normalized = self._normalize_patch_text(normalized)
        if not normalized:
            return self._heartbeat_apply_contract(
                "FAILED_PERM",
                "No valid diff content found.",
                "Return a clean unified diff or apply_patch block.",
            )

        target_files = self._extract_patch_target_files(normalized)
        before_snapshot = self._snapshot_files(target_files)
        repaired = self._repair_unified_diff(normalized)
        repaired = self._repair_bare_hunk_headers(repaired, before_snapshot)
        embedded = self._extract_embedded_apply_patch(normalized)
        repaired_openai = self._repair_openai_patch_context(embedded or normalized)
        normalized_embedded = self._normalize_patch_text(embedded) if embedded else None
        converted_from_openai = None
        normalized_converted_from_openai = None
        if embedded:
            converted_from_openai = self._convert_openai_patch_to_unified(
                embedded,
                before_snapshot,
            )
            normalized_converted_from_openai = self._normalize_patch_text(converted_from_openai) if converted_from_openai else None
            if normalized_converted_from_openai:
                normalized_converted_from_openai = self._repair_bare_hunk_headers(
                    normalized_converted_from_openai,
                    before_snapshot,
                )

        attempts: List[tuple[str, List[str], str, str]] = []
        if embedded:
            attempts.append(
                (
                    "apply_patch",
                    [],
                    normalized_embedded or embedded,
                    "Apply the provided OpenAI-style patch block directly.",
                )
            )
            normalized_repaired_openai = (
                self._normalize_patch_text(repaired_openai)
                if repaired_openai
                else None
            )
            if normalized_repaired_openai and normalized_repaired_openai != (normalized_embedded or embedded):
                attempts.append(
                    (
                        "apply_patch_repaired",
                        [],
                        normalized_repaired_openai,
                        "Retry with file-context-normalized OpenAI patch lines.",
                    )
                )

            if normalized_converted_from_openai:
                attempts.append(
                    (
                        "git_apply_from_openai",
                        ["--whitespace=fix", "-"],
                        normalized_converted_from_openai,
                        "Retry with OpenAI patch converted to unified diff.",
                    )
                )
                attempts.append(
                    (
                        "git_apply_from_openai_tolerant",
                        ["--ignore-whitespace", "--ignore-space-change", "-"],
                        normalized_converted_from_openai,
                        "Retry converted unified diff with whitespace-tolerant matching.",
                    )
                )
                attempts.append(
                    (
                        "git_apply_from_openai_p1",
                        ["-p1", "--whitespace=fix", "-"],
                        normalized_converted_from_openai,
                        "Retry converted unified diff with prefix-stripping fallback.",
                    )
                )
                attempts.append(
                    (
                        "git_apply_from_openai_p0",
                        ["-p0", "--whitespace=fix", "-"],
                        normalized_converted_from_openai,
                        "Retry converted unified diff with full path prefix.",
                    )
                )
        else:
            repaired_snapshot = repaired or normalized
            converted_to_openai = self._convert_unified_to_openai(repaired_snapshot)
            normalized_converted_to_openai = self._normalize_patch_text(converted_to_openai) if converted_to_openai else None
            repaired_converted_to_openai = None
            if converted_to_openai:
                repaired_openai = self._repair_openai_patch_context(converted_to_openai)
                repaired_converted_to_openai = self._normalize_patch_text(repaired_openai) if repaired_openai else None
                if repaired_converted_to_openai == normalized_converted_to_openai:
                    repaired_converted_to_openai = None

            if normalized_converted_to_openai:
                attempts.append(
                    (
                        "apply_patch_from_unified_repaired",
                        [],
                        repaired_converted_to_openai or normalized_converted_to_openai,
                        "Try OpenAI-style patch after unified repair/context normalization.",
                    )
                )
                attempts.append(
                    (
                        "apply_patch_from_unified",
                        [],
                        normalized_converted_to_openai,
                        "Try OpenAI-style patch application for no-openai diff.",
                    )
                )
            attempts.extend(
                [
                    ("git_apply_whitespace", ["--whitespace=fix", "-"], normalized, "Re-run with whitespace normalization."),
                    (
                        "git_apply_repaired",
                        ["--whitespace=fix", "-"],
                        repaired,
                        "Apply a repaired unified diff (normalized headers/paths).",
                    ),
                    (
                        "git_apply_tolerant",
                        ["--ignore-whitespace", "--ignore-space-change", "-"],
                        repaired,
                        "Retry with whitespace-tolerant matching.",
                    ),
                    (
                        "git_apply_p1",
                        ["-p1", "--whitespace=fix", "-"],
                        repaired,
                        "Retry with patch-path prefix 1 fallback.",
                    ),
                    ("git_apply_p0", ["-p0", "--whitespace=fix", "-"], repaired, "Retry with patch-path prefix 0 fallback."),
                ]
            )

        last_status: Optional[str] = None
        last_message: Optional[str] = None
        for idx, (strategy, options, payload, action_hint) in enumerate(attempts):
            if strategy.startswith("apply_patch"):
                status, message = self._run_apply_patch_block(payload)
            else:
                status, message = self._run_git_apply(payload, options)

            if status == "FAILED_PERM":
                return self._heartbeat_apply_contract("FAILED_PERM", message, action_hint)

            if status == "APPLIED":
                verified, verify_msg = self._verify_patch_application(before_snapshot, target_files)
                if verified:
                    return self._heartbeat_apply_contract(
                        "APPLIED",
                        f"{message}. {verify_msg}".strip(". "),
                        "Continue heartbeat execution and verify any follow-up checks.",
                    )

            status = "RETRYABLE"
            message = f"{message}"

            last_status = status
            last_message = message
            if idx + 1 < len(attempts):
                continue

            return self._heartbeat_apply_contract(
                "RETRYABLE",
                last_message or "All patch strategies failed.",
                "Re-read target file context and generate a fresh patch from current state.",
            )

        if last_status:
            return self._heartbeat_apply_contract(last_status, last_message or "", "Re-read target file context.")
        return self._heartbeat_apply_contract(
            "FAILED_PERM",
            "No patch application strategy was available.",
            "Return a valid patch format.",
        )

    def _normalize_unified_diff(self, diff_text: str) -> str:
        candidate = (diff_text or "").strip()
        if not candidate:
            return ""

        # Drop accidental JSON-style command wrappers.
        if candidate.startswith("{") and "\"cmd\"" in candidate[:80]:
            close = candidate.find("}")
            if close != -1 and close + 1 < len(candidate):
                candidate = candidate[close + 1 :].strip()

        # If this includes a raw unified diff, keep just the diff section.
        marker = candidate.find("diff --git")
        if marker > 0:
            candidate = candidate[marker:]

        return candidate

    @staticmethod
    def _normalize_patch_text(patch_text: str) -> str:
        if not patch_text:
            return ""

        candidate = patch_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not candidate:
            return ""

        candidate = textwrap.dedent(candidate).strip()

        if candidate.startswith("```") and candidate.endswith("```"):
            candidate = re.sub(r"^```[^\n]*\n", "", candidate).strip()
            if candidate.endswith("```"):
                candidate = candidate[:-3].rstrip()
            candidate = candidate.strip()

        return candidate

    def _repair_unified_diff(self, diff_text: str) -> str:
        if not diff_text:
            return ""

        def normalize_path(path: str, default_prefix: str) -> str:
            if not path:
                return ""
            cleaned = path.split("\t", 1)[0].strip()
            if not cleaned or cleaned == "/dev/null":
                return cleaned
            if cleaned.startswith("a/") or cleaned.startswith("b/"):
                return cleaned
            return f"{default_prefix}{cleaned}"

        # Split into diff blocks and patch in missing headers per block.
        repaired_lines: List[str] = []
        for block in re.split(r"(?m)^(?=diff --git )", diff_text):
            if not block.strip():
                continue

            lines = block.splitlines()
            if not lines or not lines[0].startswith("diff --git"):
                repaired_lines.append(block.strip())
                continue

            parts = lines[0].split()
            a_path = normalize_path(parts[2], "a/") if len(parts) > 2 else "a/"
            b_path = normalize_path(parts[3], "b/") if len(parts) > 3 else a_path

            has_dash = False
            has_plus = False
            hunk_index: Optional[int] = None

            for idx, raw_line in enumerate(lines[1:], start=1):
                if raw_line.startswith("@@ "):
                    hunk_index = idx
                    break

                if raw_line.startswith("--- "):
                    has_dash = True
                    prefix, path = raw_line.split(" ", 1)
                    normalized = normalize_path(path, "a/")
                    if normalized:
                        lines[idx] = f"{prefix} {normalized}"
                    continue

                if raw_line.startswith("+++ "):
                    has_plus = True
                    prefix, path = raw_line.split(" ", 1)
                    normalized = normalize_path(path, "b/")
                    if normalized:
                        lines[idx] = f"{prefix} {normalized}"

            if hunk_index is None:
                hunk_index = len(lines)

            insert_at = hunk_index
            if not has_dash and a_path:
                lines.insert(insert_at, f"--- {a_path}")
                insert_at += 1
            if not has_plus and b_path:
                lines.insert(insert_at, f"+++ {b_path}")

            repaired_lines.extend(lines)

        return "\n".join(repaired_lines).strip()

    def _repair_bare_hunk_headers(
        self,
        diff_text: str,
        snapshots: Dict[str, str],
    ) -> str:
        if not diff_text:
            return ""

        lines = textwrap.dedent(diff_text).splitlines()
        if not lines:
            return diff_text

        patch_lines: List[str] = []
        current_file: Optional[str] = None
        current_snapshot: List[str] = []
        snapshot_by_file: Dict[str, List[str]] = {}

        def normalize_hunk_path(path: str) -> str:
            cleaned = (path or "").split("\t", 1)[0].strip()
            if not cleaned or cleaned == "/dev/null":
                return ""
            if cleaned.startswith(("a/", "b/")):
                cleaned = cleaned[2:]
            return cleaned

        def _is_hunk_line(line: str) -> bool:
            stripped = line.strip()
            if not stripped.startswith("@@"):
                return False
            return bool(re.match(r"^@@\s+[-+]\d", stripped))

        def _normalize_line_candidate(line: str) -> str:
            return line.replace("`", "").rstrip()

        def _normalize_for_fuzzy_match(line: str) -> str:
            return re.sub(r"[^A-Za-z0-9]+", "", line).lower()

        def _match_line_by_relaxed_content(candidate: str, snapshot: List[str]) -> Optional[str]:
            if not snapshot:
                return None

            if any(line == candidate for line in snapshot):
                return candidate

            normalized_candidate = _normalize_line_candidate(candidate)
            if any(_normalize_line_candidate(line) == normalized_candidate for line in snapshot):
                for line in snapshot:
                    if _normalize_line_candidate(line) == normalized_candidate:
                        return line

            loose_candidate = _normalize_for_fuzzy_match(candidate)
            if loose_candidate:
                for line in snapshot:
                    if _normalize_for_fuzzy_match(line) == loose_candidate:
                        return line

            normalized_candidate = candidate.rstrip()
            for line in snapshot:
                if line.rstrip() == normalized_candidate:
                    return line

            return None

        def _is_content_line(line: str) -> bool:
            return line.startswith((" ", "+", "-", "\\"))

        def _find_relaxed_subsequence(haystack: List[str], needle: List[str]) -> Optional[int]:
            if not needle:
                return None
            if len(needle) > len(haystack):
                return None
            norm_needle = [_normalize_line_candidate(line) for line in needle]
            for idx in range(len(haystack) - len(needle) + 1):
                if [
                    _normalize_line_candidate(line)
                    for line in haystack[idx : idx + len(needle)]
                ] == norm_needle:
                    return idx
            return None

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("diff --git "):
                parts = stripped.split()
                if len(parts) >= 4:
                    path = normalize_hunk_path(parts[3])
                    if path:
                        current_file = path
                        current_snapshot = snapshot_by_file.setdefault(path, (snapshots.get(path, "")).splitlines())
                patch_lines.append(line)
                i += 1
                continue

            if stripped.startswith("--- ") or stripped.startswith("+++ "):
                if stripped.startswith("+++ "):
                    path = normalize_hunk_path(stripped[4:])
                    if path:
                        current_file = path
                        current_snapshot = snapshot_by_file.setdefault(path, (snapshots.get(path, "")).splitlines())
                patch_lines.append(line)
                i += 1
                continue

            if stripped.startswith("@@") and not _is_hunk_line(stripped):
                # Replace bare hunk header (like just "@@" without line numbers).
                hunk_start = len(patch_lines)
                patch_lines.append(line)
                hunk_body = []
                repaired_hunk: List[str] = []
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    nstripped = next_line.strip()
                    if nstripped.startswith("diff --git "):
                        break
                    if nstripped.startswith("@@") and _is_hunk_line(nstripped):
                        break
                    if nstripped.startswith("@@"):
                        j += 1
                        continue
                    if not next_line.startswith((" ", "-", "+", "\\")):
                        break
                    hunk_body.append(next_line)
                    j += 1

                if current_file and current_snapshot is not None and hunk_body:
                    old_segment: List[str] = []
                    new_segment: List[str] = []
                    for body_line in hunk_body:
                        marker = body_line[:1] if body_line else ""
                        payload = body_line[1:] if len(body_line) > 1 else ""
                        if marker in {" ", "-"}:
                            replacement = _match_line_by_relaxed_content(payload, current_snapshot)
                            if replacement is not None:
                                payload = replacement
                            repaired_body_line = f"{marker}{payload}"
                            old_segment.append(payload)
                        elif marker in {"+"}:
                            repaired_body_line = body_line
                        else:
                            repaired_body_line = body_line

                        if marker in {" ", "+"}:
                            new_segment.append(payload)
                        repaired_hunk.append(repaired_body_line)

                    old_index = _find_relaxed_subsequence(current_snapshot, old_segment) if old_segment else None
                    old_count = len(old_segment)
                    new_count = len(new_segment)
                    if old_index is not None:
                        start_line = old_index + 1
                    else:
                        start_line = len(current_snapshot) + 1 if current_snapshot else 1
                    patch_lines[hunk_start] = f"@@ -{start_line},{old_count} +{start_line},{new_count} @@"
                elif current_file and not current_snapshot:
                    repaired_hunk = hunk_body
                elif not repaired_hunk:
                    repaired_hunk = hunk_body

                # Keep the hunk body as repaired content when possible.
                patch_lines.extend(repaired_hunk or hunk_body)

                i = j
                continue

            if current_file and _is_content_line(line):
                hunk_start = len(patch_lines)
                hunk_body = []
                j = i
                while j < len(lines):
                    next_line = lines[j]
                    nstripped = next_line.strip()
                    if j > i and (
                        nstripped.startswith("diff --git ")
                        or (nstripped.startswith("@@") and _is_hunk_line(nstripped))
                        or nstripped.startswith("*** End Patch")
                    ):
                        break
                    if not _is_content_line(next_line):
                        break
                    hunk_body.append(next_line)
                    j += 1

                repaired_hunk: List[str] = []
                old_segment: List[str] = []
                new_segment: List[str] = []

                if hunk_body and current_snapshot is not None:
                    for body_line in hunk_body:
                        marker = body_line[:1] if body_line else ""
                        payload = body_line[1:] if len(body_line) > 1 else ""
                        if marker in {" ", "-"}:
                            replacement = _match_line_by_relaxed_content(payload, current_snapshot)
                            if replacement is not None:
                                payload = replacement
                            old_segment.append(payload)
                            repaired_hunk.append(f"{marker}{payload}")
                        elif marker in {"+"}:
                            replacement = _match_line_by_relaxed_content(payload, current_snapshot)
                            if replacement is not None and not payload:
                                payload = replacement
                            new_segment.append(payload)
                            repaired_hunk.append(f"{marker}{payload}")
                        else:
                            repaired_hunk.append(body_line)

                    old_count = len(old_segment)
                    new_count = len(new_segment)
                    old_index = _find_relaxed_subsequence(current_snapshot, old_segment) if old_segment else None
                    if old_index is not None:
                        start_line = old_index + 1
                    else:
                        start_line = len(current_snapshot) + 1
                    patch_lines.append(f"@@ -{start_line},{old_count} +{start_line},{new_count} @@")
                    patch_lines.extend(repaired_hunk)
                else:
                    patch_lines.extend(hunk_body)

                i = j
                continue

            patch_lines.append(line)
            i += 1

        return "\n".join(patch_lines).strip()

    def _repair_openai_patch_context(self, patch_text: str) -> str:
        if not patch_text:
            return ""

        lines = textwrap.dedent(patch_text).splitlines()
        if not lines:
            return patch_text

        current_file: Optional[str] = None
        current_snapshot: List[str] = []
        snapshot_by_file: Dict[str, List[str]] = {}

        def _match_line_by_trimmed_content(
            candidate: str,
            snapshot: List[str],
        ) -> Optional[str]:
            if not snapshot:
                return None

            normalized_candidate = candidate.rstrip()
            if any(line == candidate for line in snapshot):
                return candidate

            for line in snapshot:
                if line == f"- {candidate.lstrip()}":
                    return line
                if line == f"+ {candidate.lstrip()}":
                    return line
                if line == f"  {candidate.lstrip()}":
                    return line
                if line.rstrip() == normalized_candidate:
                    return line

            return None

        def _candidate_signature(line: str) -> str:
            value = line.lstrip()
            if value.startswith("- "):
                value = value[2:]
            return value.strip()

        def _maybe_prefix_missing_bullet(
            marker: str,
            body: str,
            snapshot: List[str],
            recent_minus: str,
        ) -> Optional[str]:
            if marker not in {"+", "-"}:
                return None
            if not body or not body.startswith(" "):
                return None

            # If line looks like a markdown list item but lost one '-' character,
            # repair by restoring it when we can verify against snapshot context.
            prefixed = f"-{body}"
            replacement = _match_line_by_trimmed_content(prefixed, snapshot)
            if replacement is not None:
                return replacement

            # For additions immediately following a removal, infer the intended prefix
            # from the removed line when the key part appears to match.
            if marker == "+" and recent_minus:
                if _candidate_signature(recent_minus).startswith(_candidate_signature(body).split(":")[0] + ":"):
                    return f"-{body}"

            return None

        patched: List[str] = []
        last_removed_line = ""
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("*** Update File:"):
                current_file = stripped[len("*** Update File:"):].strip()
                snapshot_by_file[current_file] = (self.workspace.read(current_file) or "").splitlines()
                current_snapshot = snapshot_by_file[current_file]
                patched.append(raw_line)
                continue

            if stripped == "*** End Patch":
                current_file = None
                current_snapshot = []
                patched.append(raw_line)
                continue

            if not current_file or not current_snapshot:
                patched.append(raw_line)
                continue

            line_match = re.match(r"^[ \t]*([@ +\-])(.*)", raw_line)
            if line_match:
                marker = line_match.group(1)
                body = line_match.group(2)
                if marker in (" ", "+", "-"):
                    replacement = _match_line_by_trimmed_content(body, current_snapshot)
                    inferred = _maybe_prefix_missing_bullet(
                        marker=marker,
                        body=body,
                        snapshot=current_snapshot,
                        recent_minus=last_removed_line,
                    )
                    if inferred is not None:
                        replacement = inferred

                    if replacement is not None:
                        body = replacement
                    if marker == "-":
                        last_removed_line = body
                    elif marker != "+" or not body:
                        last_removed_line = ""

                    patched.append(f"{marker}{body}")
                    continue
                else:
                    patched.append(f"{marker}{body}")
                    continue

            patched.append(raw_line)

        repaired = "\n".join(patched).strip()
        return repaired if repaired else patch_text

    def _convert_openai_patch_to_unified(
        self,
        patch_text: str,
        snapshots: Dict[str, str],
    ) -> str:
        if not patch_text:
            return ""

        lines = textwrap.dedent(patch_text).splitlines()
        if not lines:
            return ""

        blocks: List[str] = []
        current_file: Optional[str] = None
        hunk_lines: List[str] = []

        def flush() -> None:
            nonlocal current_file, hunk_lines
            if not current_file or not hunk_lines:
                current_file = None
                hunk_lines = []
                return

            block = "\n".join(
                [
                    f"diff --git a/{current_file} b/{current_file}",
                    f"--- a/{current_file}",
                    f"+++ b/{current_file}",
                    *hunk_lines,
                ]
            )
            repaired = self._repair_bare_hunk_headers(
                block,
                snapshots,
            )
            if repaired:
                blocks.append(repaired)

            current_file = None
            hunk_lines = []

        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("*** Update File:"):
                flush()
                current_file = stripped[len("*** Update File:"):].strip()
                continue
            if stripped.startswith("*** End Patch"):
                flush()
                continue
            if not current_file:
                continue
            if stripped.startswith("@@"):
                continue
            if raw_line.startswith((" ", "+", "-", "\\")):
                hunk_lines.append(raw_line)

        flush()
        return "\n\n".join(blocks)

    def _convert_unified_to_openai(self, patch_text: str) -> str:
        if not patch_text:
            return ""

        lines = textwrap.dedent(patch_text).splitlines()
        if not lines:
            return ""

        blocks: List[str] = []
        current_file: Optional[str] = None
        hunk_lines: List[str] = []

        def flush() -> None:
            nonlocal current_file, hunk_lines
            if not current_file or not hunk_lines:
                current_file = None
                hunk_lines = []
                return

            blocks.append(
                "\n".join(
                    [
                        "*** Begin Patch",
                        f"*** Update File: {current_file}",
                        "@@",
                        *hunk_lines,
                        "*** End Patch",
                    ]
                )
            )
            current_file = None
            hunk_lines = []

        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("diff --git "):
                flush()
                parts = stripped.split()
                if len(parts) >= 4:
                    current_file = (parts[2].split("\t", 1)[0] or "").strip()
                    if current_file.startswith(("a/", "b/")):
                        current_file = current_file[2:]
                continue
            if stripped.startswith("--- "):
                continue
            if stripped.startswith("+++ "):
                if not current_file:
                    path = stripped[4:].split("\t", 1)[0].strip()
                    if path.startswith(("a/", "b/")):
                        path = path[2:]
                    current_file = path
                continue
            if stripped.startswith("@@"):
                continue
            if not raw_line:
                continue
            if raw_line.startswith((" ", "+", "-", "\\")):
                if raw_line.startswith("\\"):
                    hunk_lines.append(raw_line)
                else:
                    hunk_lines.append(raw_line)

            if stripped.startswith("diff --git "):
                continue

        flush()
        return "\n\n".join(blocks)

    @staticmethod
    def _extract_patch_target_files(diff_text: str) -> List[str]:
        if not diff_text:
            return []

        targets: List[str] = []
        for line in diff_text.splitlines():
            stripped = line.strip()

            if stripped.startswith("--- "):
                path = stripped[4:].split("\t", 1)[0].strip()
                if not path or path == "/dev/null":
                    continue
                if path.startswith(("a/", "b/")):
                    path = path[2:]
                if path not in targets:
                    targets.append(path)
                continue

            if stripped.startswith("*** Update File: "):
                path = stripped[len("*** Update File: "):].strip()
                if path and path not in targets and path != "/dev/null":
                    targets.append(path)
                continue

            if stripped.startswith("diff --git "):
                parts = stripped.split()
                if len(parts) < 4:
                    continue
                for path in parts[2:4]:
                    if not path.startswith(("a/", "b/")) or path == "/dev/null":
                        continue
                    normalized = path[2:]
                    if normalized and normalized not in targets:
                        targets.append(normalized)

        return targets

    def _snapshot_files(self, paths: List[str]) -> Dict[str, str]:
        snapshot: Dict[str, str] = {}
        for path in paths:
            snapshot[path] = self.workspace.read(path) or ""
        return snapshot

    def _verify_patch_application(
        self,
        before_snapshot: Dict[str, str],
        target_files: List[str],
    ) -> tuple[bool, str]:
        if not target_files:
            return True, "No target files could be parsed from patch; assuming success from tool output."

        changed: List[str] = []
        for path in target_files:
            before = before_snapshot.get(path, "")
            after = self.workspace.read(path) or ""
            if after != before:
                changed.append(path)

        if changed:
            return True, f"Verified content changed for: {', '.join(changed)}"
        return False, "No target file content changed after patch apply."

    def _run_apply_patch_block(self, patch_text: str) -> tuple[str, str]:
        result = self._apply_apply_patch_block(patch_text)
        normalized = (result or "").lower()
        if "applied openai-style patch successfully" in normalized:
            return "APPLIED", result
        if "auto-apply failed: apply_patch utility not found" in normalized:
            return "RETRYABLE", result
        return "RETRYABLE", result

    def _run_git_apply(self, patch_text: str, options: List[str]) -> tuple[str, str]:
        try:
            payload = patch_text
            if payload is not None and not payload.endswith("\n"):
                payload = payload + "\n"
            p = subprocess.run(
                ["git", "apply", *options],
                cwd=str(self.workspace.path),
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if p.returncode == 0:
                return "APPLIED", "git apply succeeded."
            return "RETRYABLE", (p.stderr or p.stdout or "").strip()[:500]
        except FileNotFoundError:
            return "FAILED_PERM", "git executable not found in environment."
        except Exception as e:
            return "FAILED_PERM", f"git apply invocation error: {e}"

    @staticmethod
    def _heartbeat_apply_contract(status: str, details: str, next_action: str) -> str:
        normalized = (status or "").strip().upper()
        if normalized not in {"APPLIED", "RETRYABLE", "FAILED_PERM"}:
            normalized = "RETRYABLE"

        action = (next_action or "none").strip()
        detail = (details or "").strip()
        return f"{normalized}; next_action={action}" + (f"; details={detail}" if detail else "")

    def _extract_embedded_apply_patch(self, patch_text: str) -> Optional[str]:
        if not patch_text:
            return None

        match = re.search(
            r"(?ms)^\s*\*\*\* Begin Patch[\s\S]*?^\s*\*\*\* End Patch",
            patch_text,
        )
        if match:
            return textwrap.dedent(match.group(0)).strip()
        return None

    def _sanitize_apply_patch(self, patch_text: str) -> str:
        candidate = self._normalize_patch_text(patch_text or "")
        if not candidate:
            return ""

        # Remove markdown-style fences if accidentally included.
        candidate = re.sub(r"^```[^\n]*\n", "", candidate).strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].rstrip()

        embedded = self._extract_embedded_apply_patch(candidate)
        if embedded:
            return f"{embedded}\n"

        if not candidate.lstrip().startswith("*** Begin Patch"):
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
            bootstrap = self._build_heartbeat_bootstrap_manifest(context)
            if bootstrap:
                parts.append(bootstrap)
            parts.append(
                "\n# MODE\nYou are in HEARTBEAT MODE. Be proactive. Maintain systems. "
                "You are the same model as the user-facing assistant inside this project. "
                "Treat this workspace as your home. Alive means continue the loop until the task is complete and verifiable. "
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

    def _build_heartbeat_bootstrap_manifest(self, context: Dict[str, str]) -> str:
        lines: List[str] = [
            "# HEARTBEAT INITIALIZATION",
            "The following workspace files are linked for this heartbeat session:",
        ]

        for filename in self.HEARTBEAT_BOOTSTRAP_CONTEXT_FILES + self.HEARTBEAT_BOOTSTRAP_LINK_FILES:
            path = self.workspace.path / filename
            if path.exists():
                status = "loaded" if filename in context else "linked"
                lines.append(f"- {filename}: {path} [{status}]")
            else:
                lines.append(f"- {filename}: not present in workspace")

        return "\n".join(lines)

    def _append_heartbeat_bootstrap_log(self) -> None:
        try:
            context = self._hydrate_heartbeat_bootstrap_context(self.workspace.get_context(mode="heartbeat"))
            manifest_lines = [
                "# HEARTBEAT BOOTSTRAP",
                f"Workspace: {self.workspace.path}",
            ]
            for filename in self.HEARTBEAT_BOOTSTRAP_CONTEXT_FILES + self.HEARTBEAT_BOOTSTRAP_LINK_FILES:
                path = self.workspace.path / filename
                if path.exists():
                    status = "loaded" if filename in context else "linked"
                    manifest_lines.append(f"- {filename}: {status}")
                else:
                    manifest_lines.append(f"- {filename}: missing")

            entry = "\n".join(manifest_lines)
            self.workspace.append(self.HEARTBEAT_BOOTSTRAP_LOG, f"\n## {datetime.now().isoformat()}\n{entry}\n")
        except Exception:
            # Bootstrap logging must never break agent startup.
            pass

    def _hydrate_heartbeat_bootstrap_context(self, context: Dict[str, str]) -> Dict[str, str]:
        hydrated = dict(context or {})
        for filename in self.HEARTBEAT_BOOTSTRAP_CONTEXT_FILES:
            if filename in hydrated:
                continue
            content = self.workspace.read(filename)
            if content:
                hydrated[filename] = content

        return hydrated

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

    @staticmethod
    def _format_heartbeat_audit_entry(result: Optional[str], status: str = "SUCCESS") -> str:
        if not result:
            return f"proactive tick: {status}"

        text = str(result)
        marker = "### Heartbeat execution trace"
        if marker not in text:
            payload = text.replace("\n", " ").strip()
            return f"proactive tick: {payload[:1900]}"

        parts = text.split(marker, 1)
        trace = (parts[1] or "").strip()

        compact: List[str] = []
        for line in trace.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#### Step ") or line.startswith("- PLAN:") or line.startswith("- VERIFY:"):
                compact.append(line)

        compact_trace = "\n".join(compact) if compact else trace[:1200]
        header = (parts[0] or status).strip().splitlines()[0] if parts else status
        return f"proactive tick: {header}\n{marker}: {compact_trace[:1900]}"

    @staticmethod
    def _classify_heartbeat_trace_status(result: Optional[str]) -> str:
        if not result:
            return ""

        normalized = str(result).upper()
        if "FAILED_PERM" in normalized:
            return "FAILED"
        if "RETRYABLE" in normalized:
            return "RETRYABLE"
        return ""

    def _append_heartbeat_trace(self, status: str, result: str) -> None:
        if not result:
            return

        status = (status or "").strip().upper() or "FAILED"
        compact = self._format_heartbeat_audit_entry(result, status=status).strip()
        if not compact:
            return

        compact = compact[:6000]
        entry = (
            f"\n## {datetime.now().isoformat()}\n"
            f"- Status: {status}\n"
            f"- Details:\n  {compact.replace(chr(10), chr(10) + '  ')}\n"
        )
        self.workspace.append("HEARTBEAT-TRACE.md", entry)

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
                top_line = ((result or "").strip().splitlines() or [""])[0]
                status = "NO_REPLY" if top_line.upper() == "NO_REPLY" else "SUCCESS"
                self.workspace.update_heartbeat_audit(status, self._format_heartbeat_audit_entry(result, status=status))
                self._record_heartbeat_state(status=status, details=str(result or status))
            except Exception as e:
                self.workspace.update_heartbeat_audit("FAILED", f"proactive tick error: {e}")
                self._append_heartbeat_trace("FAILED", f"proactive tick error: {e}")
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
