# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libre_claw.core.session import ChatMessage


ToolSchema = Mapping[str, Any]


class ProviderConfigurationError(RuntimeError):
    """Raised when a provider cannot be configured for the current session."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TextDelta:
    text: str


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


StreamEvent = TextDelta | ToolCallStart | ToolCallDelta | ToolCallReady | Done | ProviderError


class LLMProvider(ABC):
    """Abstract provider contract used by the agent core."""

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
