# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import replace
from typing import Any, Literal

from libre_claw.core.session import ChatMessage, ContentBlock
from libre_claw.providers.base import (
    ProviderConfigurationError, ProviderError, ReasoningDelta, StreamEvent, ToolCallReady, ToolSchema, Usage,
)
from libre_claw.providers.openai import (
    OpenAIProvider,
    _OpenAIToolAccumulator,
    _format_assistant_message,
    _object_field,
)


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DeepSeekThinking = Literal["enabled", "disabled"]
DeepSeekReasoningEffort = Literal["low", "high", "max"]


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek Chat Completions with persistent reasoning and client tools."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        *,
        base_url: str = DEEPSEEK_BASE_URL,
        thinking: DeepSeekThinking = "enabled",
        reasoning_effort: DeepSeekReasoningEffort = "high",
        client: object | None = None,
    ) -> None:
        if thinking not in {"enabled", "disabled"}:
            raise ProviderConfigurationError("DeepSeek thinking must be 'enabled' or 'disabled'.")
        if reasoning_effort not in {"low", "high", "max"}:
            raise ProviderConfigurationError("DeepSeek reasoning_effort must be 'low', 'high', or 'max'.")
        _validate_max_tokens(max_tokens)
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        super().__init__(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            base_url=base_url,
            display_name="DeepSeek",
            client=client,
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            if max_tokens is not None:
                _validate_max_tokens(max_tokens)
        except ProviderConfigurationError as exc:
            yield ProviderError(str(exc))
            return
        async with aclosing(super().complete(messages, tools, system, stream, temperature, max_tokens)) as response:
            async for event in response:
                yield event

    def _max_tokens_field(self) -> str:
        return "max_tokens"

    def _extra_body(self) -> dict[str, Any]:
        return {"thinking": {"type": "enabled" if self._thinking_enabled() else "disabled"}}

    def _extra_request_parameters(self) -> dict[str, Any]:
        return {"reasoning_effort": self.reasoning_effort} if self._thinking_enabled() else {}

    def _supports_temperature(self) -> bool:
        return not self._thinking_enabled()

    def _thinking_enabled(self) -> bool:
        info = self.model_info
        return self.thinking == "enabled" and not (info and info.supports_reasoning is False)

    def _finalize_request(self, request: dict[str, Any]) -> None:
        thinking_enabled = self._thinking_enabled()
        if thinking_enabled:
            super()._finalize_request(request)
        request.setdefault("extra_body", {})["thinking"] = {
            "type": "enabled" if thinking_enabled else "disabled",
        }
        if thinking_enabled:
            request.pop("temperature", None)
            # reasoning_effort is newer than our supported OpenAI SDK floor.
            # Send it as a DeepSeek extension after capability validation.
            if effort := request.pop("reasoning_effort", None):
                request["extra_body"]["reasoning_effort"] = effort
        else:
            # A non-none reasoning_effort can enable thinking again on DeepSeek.
            request.pop("reasoning_effort", None)
        # DeepSeek defaults to auto with tools; omitting this also works on V4
        # deployments that reject an explicit tool_choice in thinking mode.
        request.pop("tool_choice", None)
        if thinking_enabled and request.get("tools"):
            for message in request["messages"]:
                if message["role"] == "assistant" and "reasoning_content" not in message:
                    raise ProviderConfigurationError(
                        "DeepSeek thinking with tools requires saved DeepSeek reasoning for every assistant turn. "
                        "Start a new DeepSeek session or set providers.deepseek.thinking = 'disabled' "
                        "to continue a history from another provider or non-thinking mode."
                    )

    def _format_assistant_message(self, blocks: Sequence[ContentBlock]) -> dict[str, Any]:
        message = _format_assistant_message(blocks, reasoning_provider="deepseek")
        if message.get("tool_calls") and message["content"] is None:
            message["content"] = ""
        return message

    def _reasoning_delta(self, delta: Any) -> ReasoningDelta | None:
        content = _object_field(delta, "reasoning_content")
        return ReasoningDelta(content, provider="deepseek") if isinstance(content, str) else None

    def _usage_from(self, raw_usage: Any, previous: Usage | None) -> Usage | None:
        usage = super()._usage_from(raw_usage, previous)
        cache_hits = _object_field(raw_usage, "prompt_cache_hit_tokens")
        if usage is not None and isinstance(cache_hits, int) and not isinstance(cache_hits, bool):
            usage = replace(usage, cached_tokens=cache_hits)
        return usage

    def _response_error(self, stop_reason: str | None, has_tool_calls: bool) -> str | None:
        reasons = {
            "length": "reached the output token limit; increase providers.deepseek.max_tokens or shorten the request",
            "content_filter": "was interrupted by a content filter",
            "insufficient_system_resource": "was interrupted because the service has insufficient resources; retry the request",
            "aborted": "was aborted by the service; retry the request",
        }
        if stop_reason in reasons:
            return f"DeepSeek generation {reasons[stop_reason]}. No tool calls from this response were executed."
        if stop_reason not in {"stop", "tool_calls"}:
            return "DeepSeek stream ended without a successful finish reason. Retry the request."
        if has_tool_calls != (stop_reason == "tool_calls"):
            return "DeepSeek returned incomplete tool-call framing. Retry the request."
        return None

    def _finalize_tool_calls(self, accumulators: dict[int, _OpenAIToolAccumulator]) -> list[StreamEvent]:
        if any(not item.tool_call_id or not item.name for item in accumulators.values()):
            raise ProviderConfigurationError("DeepSeek returned an incomplete tool call without its ID or function name.")
        calls: list[StreamEvent] = [
            ToolCallReady(item.tool_call_id, item.name, item.parse_arguments())
            for _, item in sorted(accumulators.items())
        ]
        accumulators.clear()
        return calls


def _validate_max_tokens(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderConfigurationError("DeepSeek max_tokens must be a positive integer.")
