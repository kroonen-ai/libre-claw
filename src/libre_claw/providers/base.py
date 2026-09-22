# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libre_claw.core.session import ChatMessage
    from libre_claw.providers.model_catalog import ModelInfo


ToolSchema = Mapping[str, Any]


class CacheableSystemPrompt(str):
    """A normal prompt string with an explicit stable cache boundary.

    Providers that understand this metadata can mark the stable prefix while
    still sending the complete, current prompt. Other providers see plain text.
    """

    cache_prefix: str
    cache_scope: str | None

    def __new__(cls, prefix: str, suffix: str = "", *, cache_scope: str | None = None):
        value = super().__new__(cls, "\n\n".join(part for part in (prefix, suffix) if part))
        value.cache_prefix = prefix
        value.cache_scope = cache_scope
        return value


class ProviderConfigurationError(RuntimeError):
    """Raised when a provider cannot be configured for the current session."""


@dataclass(frozen=True)
class Usage:
    """Input totals include cache reads and writes; cache fields are subsets."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float | None = None
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def combine_usage(left: Usage | None, right: Usage | None) -> Usage | None:
    """Add provider-reported usage objects without inventing missing costs."""
    if left is None:
        return right
    if right is None:
        return left

    cost: float | None
    if left.cost is None or right.cost is None:
        cost = None
    else:
        cost = left.cost + right.cost

    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        cost=cost,
        cache_write_tokens=left.cache_write_tokens + right.cache_write_tokens,
    )


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """Provider-private reasoning that must be retained across tool turns."""

    text: str
    provider: str


@dataclass(frozen=True)
class ToolCallStart:
    tool_call_id: str
    name: str


@dataclass(frozen=True)
class ToolCallDelta:
    tool_call_id: str
    name: str
    partial_json: str


@dataclass(frozen=True)
class ToolCallReady:
    tool_call_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Done:
    usage: Usage | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class ProviderError:
    message: str


StreamEvent = (
    TextDelta
    | ReasoningDelta
    | ToolCallStart
    | ToolCallDelta
    | ToolCallReady
    | Done
    | ProviderError
)


class LLMProvider(ABC):
    """Abstract provider contract used by the agent core."""

    @property
    def model_info(self) -> "ModelInfo | None":
        config = getattr(self, "_capability_config", None)
        if config is None:
            return getattr(self, "_model_info", None)
        from libre_claw.providers.model_catalog import selected_model_info
        return selected_model_info(config, self._capability_provider, getattr(self, "model", ""))

    @model_info.setter
    def model_info(self, value: "ModelInfo | None") -> None:
        self._model_info = value

    @abstractmethod
    def complete(
        self,
        messages: Sequence["ChatMessage"],
        tools: Sequence[ToolSchema] | None = None,
        system: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield normalized streaming events from the provider."""
