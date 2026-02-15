"""Claude Code backend for Libre Claw.

Wraps the Claude Code CLI for programmatic access.
Uses `claude --print --output-format json` for non-interactive completions.
"""

import json
import subprocess
from typing import Any, Dict, List, Optional

from .base import BackendConfig, BaseBackend, Message, Response


class ClaudeCodeBackend(BaseBackend):
    """Backend that uses Claude Code CLI for completions.

    Requires Claude Code CLI installed and accessible.
    Uses --print mode for non-interactive single-prompt completions.
    """

    def __init__(self, config: Optional[BackendConfig] = None):
        super().__init__(config)
        self._claude_path = self.config.claude_path

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def supports_tools(self) -> bool:
        return True

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[Dict[str, str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """Generate a completion using Claude Code CLI.

        Uses `claude --print --output-format json` which accepts a prompt
        on stdin and returns structured JSON output.
        """
        # Build the full prompt with context
        parts = []
        if system_prompt:
            parts.append(f"<system>\n{system_prompt}\n</system>")
        if context:
            for filename, content in context.items():
                parts.append(f"<file name=\"{filename}\">\n{content}\n</file>")
        parts.append(prompt)
        full_prompt = "\n\n".join(parts)

        # Build command
        cmd = [
            self._claude_path,
            "--print",                # Non-interactive mode
            "--output-format", "json",  # Structured JSON output
        ]

        if self.config.max_tokens:
            cmd.extend(["--max-turns", "1"])

        try:
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                # Try to get useful error info
                error_msg = result.stderr.strip() or f"Exit code {result.returncode}"
                return Response(
                    content=f"Error: Claude Code failed: {error_msg}",
                    stop_reason="error",
                )

            output = result.stdout.strip()
            if not output:
                return Response(content="Error: No output from Claude Code", stop_reason="error")

            # Parse JSON output - Claude CLI returns an array of content blocks
            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                # If not JSON, treat as plain text response
                return Response(content=output, model="claude-code", stop_reason="end_turn")

            # Handle array format (list of content blocks)
            if isinstance(data, list):
                text_parts = []
                tool_calls = []
                for block in data:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": block.get("input", {}),
                            })
                return Response(
                    content="\n".join(text_parts),
                    tool_calls=tool_calls if tool_calls else None,
                    model="claude-code",
                    stop_reason="end_turn",
                )

            # Handle object format
            if isinstance(data, dict):
                content = data.get("content", data.get("text", data.get("result", str(data))))
                if isinstance(content, list):
                    # Content is array of blocks
                    text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    content = "\n".join(text_parts)
                return Response(
                    content=str(content),
                    usage=data.get("usage"),
                    model=data.get("model", "claude-code"),
                    stop_reason=data.get("stop_reason", "end_turn"),
                )

            return Response(content=str(data), model="claude-code", stop_reason="end_turn")

        except subprocess.TimeoutExpired:
            return Response(content="Error: Claude Code timed out after 5 minutes", stop_reason="timeout")
        except FileNotFoundError:
            return Response(
                content=f"Error: Claude Code CLI not found at {self._claude_path}. "
                "Install with: npm install -g @anthropic-ai/claude-code",
                stop_reason="error",
            )
        except Exception as e:
            return Response(content=f"Error: {str(e)}", stop_reason="error")

    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Response:
        """Generate a chat completion by building a prompt from messages."""
        parts = []
        system = None
        for msg in messages:
            if msg.role == "system":
                system = msg.content
            elif msg.role == "user":
                parts.append(f"Human: {msg.content}")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}")

        prompt = "\n\n".join(parts)
        return self.complete(prompt=prompt, system_prompt=system, tools=tools)

    def check_available(self) -> bool:
        """Check if Claude Code CLI is available."""
        try:
            result = subprocess.run(
                [self._claude_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_version(self) -> Optional[str]:
        """Get Claude Code version string."""
        try:
            result = subprocess.run(
                [self._claude_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None
