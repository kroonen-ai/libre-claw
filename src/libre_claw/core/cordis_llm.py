# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Capability-gated DSH model calls through Libre Claw's provider adapters.

Only caller-supplied messages cross this service. Provider configuration, keys,
workspace contents, and Libre Claw session history never become plugin data.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, cast

from libre_claw.config import LibreClawConfig
from libre_claw.core.session import ChatMessage, provider_reasoning_block, text_block, tool_result_block, tool_use_block
from libre_claw.providers.base import (
    Done, LLMProvider, ProviderError, ReasoningDelta, TextDelta,
    ToolCallDelta, ToolCallReady, ToolCallStart, Usage,
)
from libre_claw.providers.factory import create_provider
from libre_claw.providers.model_catalog import ModelCatalog, ModelInfo, discover_models

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024 * 1024
_SAFE_INTEGER = 2**53 - 1
# Codex runs a coding agent with workspace access, not a model-only operation.
_MODEL_PROVIDERS = {
    "anthropic": "Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter",
    "deepseek": "DeepSeek", "opencode": "OpenCode Zen", "opencode-go": "OpenCode Go",
    "moonshot": "Kimi / Moonshot", "ollama": "Ollama", "llamacpp": "llama.cpp",
}


class CordisLlmError(ValueError):
    """A safe public error containing no provider exception or configuration."""


def _invalid(label: str) -> CordisLlmError:
    return CordisLlmError(f"Invalid plugin model {label}.")


def _object(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _invalid(label)
    return value


def _keys(value: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    if not required <= value.keys() or value.keys() - required - optional:
        raise _invalid(label)


def _text(value: Any, label: str, maximum: int = 2048, *, empty: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or (not empty and not value.strip()):
        raise _invalid(label)
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _SAFE_INTEGER:
        raise _invalid(label)
    return value


def _json(value: Any, limit: int) -> str:
    """Bound JSON complexity before serialization; reject cycles and nonfinite values."""
    nodes = 0
    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 100_000 or depth > 32:
            raise _invalid("JSON complexity")
        if item is None or type(item) in {str, bool}:
            return
        if type(item) in {int, float}:
            if not math.isfinite(item):
                raise _invalid("JSON number")
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        for child in _object(item, "JSON value").values():
            visit(child, depth + 1)
    try:
        visit(value, 0)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > limit:
            raise _invalid("message size")
    except (RecursionError, OverflowError, UnicodeError):
        raise _invalid("JSON value") from None
    return encoded


def _array(value: Any, label: str, limit: int = 2048) -> list[Any]:
    if type(value) is not list or len(value) > limit:
        raise _invalid(label)
    return value


def _source(value: Any) -> dict[str, Any]:
    value = _object(value, "message source")
    kind = value.get("kind")
    required = {
        "user": {"kind"}, "plugin": {"kind", "plugin"},
        "tool": {"kind", "callId"}, "model": {"kind", "provider", "model"},
    }.get(kind) if type(kind) is str else None
    if required is None:
        raise _invalid("message source")
    _keys(value, required, {"replayState"} if kind == "model" else set(), "message source")
    for key in required:
        _text(value[key], "message source")
    return value


def _replay(source: dict[str, Any], provider: str, model: str) -> list[dict[str, Any]] | None:
    if "replayState" not in source:
        return None
    # Opaque state from another adapter cannot safely be interpreted or dropped.
    envelope = _object(source["replayState"], "replay state")
    _keys(envelope, {"response"}, set(), "replay state")
    response = _object(envelope["response"], "replay state")
    _keys(response, {"libreClawReasoning"}, set(), "replay state")
    state = _object(response["libreClawReasoning"], "replay state")
    _keys(state, {"version", "provider", "model", "blocks"}, set(), "replay state")
    if type(state["version"]) is not int or state["version"] != 1 or state["provider"] != source["provider"] or state["model"] != source["model"]:
        raise _invalid("replay identity")
    result = []
    for block in _array(state["blocks"], "replay blocks"):
        block = _object(block, "replay block")
        _keys(block, {"provider", "text"}, set(), "replay block")
        result.append(provider_reasoning_block(
            _text(block["text"], "replay text", MAX_REQUEST_BYTES, empty=True),
            _text(block["provider"], "replay provider"),
        ))
    return result if source["provider"] == provider and source["model"] == model else []


def _messages(options: dict[str, Any]) -> tuple[list[ChatMessage], str | None]:
    messages = []
    systems = [options["system"]] if "system" in options else []
    identities: set[str] = set()
    calls: set[str] = set()
    pending: set[str] = set()
    for raw in _array(options["messages"], "messages"):
        raw = _object(raw, "message")
        _keys(raw, {"id", "role", "content", "source"}, set(), "message")
        identity = _text(raw["id"], "message ID")
        if identity in identities:
            raise _invalid("duplicate message ID")
        identities.add(identity)
        role = raw["role"]
        if role not in ("user", "assistant", "system"):
            raise _invalid("message role")
        source = _source(raw["source"])
        if role == "system" and (source["kind"] != "plugin" or messages):
            raise _invalid("system message order or source")
        content = _array(raw["content"], "message content")
        if source["kind"] == "tool" and (
            role != "user" or len(content) != 1
            or not isinstance(content[0], dict) or content[0].get("type") != "tool-result"
            or content[0].get("toolCallId") != source["callId"]
        ):
            raise _invalid("tool result correlation")
        preserved = _replay(source, options["provider"], options["model"]) if source["kind"] == "model" else None
        blocks = []
        for block in content:
            block = _object(block, "content block")
            kind = block.get("type")
            if kind in ("text", "reasoning"):
                _keys(block, {"type", "text"}, set(), "text block")
                text = _text(block["text"], "text", MAX_REQUEST_BYTES, empty=True)
                if kind == "reasoning":
                    if role != "assistant" or source["kind"] != "model":
                        raise _invalid("reasoning source")
                    if preserved is None and source["provider"] == options["provider"]:
                        blocks.append(provider_reasoning_block(text, source["provider"]))
                else:
                    blocks.append(text_block(text))
            elif kind == "tool-call":
                _keys(block, {"type", "id", "name", "arguments"}, set(), "tool call")
                if role != "assistant":
                    raise _invalid("tool call role")
                call = _text(block["id"], "tool call ID")
                if call in calls:
                    raise _invalid("duplicate tool call ID")
                calls.add(call)
                pending.add(call)
                try:
                    arguments = json.loads(_text(block["arguments"], "tool arguments", MAX_REQUEST_BYTES, empty=True))
                except json.JSONDecodeError:
                    raise _invalid("tool arguments JSON") from None
                _json(arguments, MAX_REQUEST_BYTES)
                blocks.append(tool_use_block(call, _text(block["name"], "tool name"), _object(arguments, "tool arguments")))
            elif kind == "tool-result":
                _keys(block, {"type", "toolCallId", "content"}, {"isError"}, "tool result")
                call = _text(block["toolCallId"], "tool result ID")
                if role != "user" or call not in pending or type(block.get("isError", False)) is not bool:
                    raise _invalid("tool result correlation")
                pending.remove(call)
                texts = []
                for part in _array(block["content"], "tool result content"):
                    part = _object(part, "tool result text")
                    _keys(part, {"type", "text"}, set(), "tool result text")
                    if part["type"] != "text":
                        raise _invalid("tool result content type")
                    texts.append(_text(part["text"], "tool result text", MAX_REQUEST_BYTES, empty=True))
                blocks.append(tool_result_block(call, "\n".join(texts), block.get("isError", False)))
            else:
                raise _invalid("content block type")
        if role == "system":
            systems.extend(block["text"] for block in blocks)
        else:
            if preserved:
                blocks.extend(preserved)
            messages.append(ChatMessage(cast(Any, role), blocks))
    if pending:
        raise _invalid("missing tool results")
    return messages, "\n\n".join(systems) if systems else None


def _request(options: Any) -> tuple[dict[str, Any], list[ChatMessage], str | None]:
    # Detach caller-owned values before async discovery or streaming.
    options = json.loads(_json(options, MAX_REQUEST_BYTES))
    options = _object(options, "request")
    _keys(options, {"provider", "model", "messages"}, {
        "system", "tools", "temperature", "maxTokens", "stop", "reasoningEffort", "purpose",
    }, "request")
    for key in ("provider", "model", "reasoningEffort"):
        if key in options:
            _text(options[key], key)
    if "system" in options:
        _text(options["system"], "system prompt", MAX_REQUEST_BYTES, empty=True)
    if "maxTokens" in options:
        _integer(options["maxTokens"], "max tokens", 1)
    if "temperature" in options and (
        type(options["temperature"]) not in {float, int} or not 0 <= options["temperature"] <= 2
    ):
        raise _invalid("temperature")
    if "purpose" in options and options["purpose"] not in ("session-title", "compaction"):
        raise _invalid("request purpose")
    if "stop" in options and _array(options["stop"], "stop sequences", 64):
        raise CordisLlmError("Plugin model calls do not support stop sequences.")
    if "tools" in options:
        seen = set()
        for tool in _array(options["tools"], "tools", 256):
            tool = _object(tool, "tool schema")
            _keys(tool, {"name", "description", "parameters"}, set(), "tool schema")
            name = _text(tool["name"], "tool name")
            _text(tool["description"], "tool description", MAX_REQUEST_BYTES, empty=True)
            _object(tool["parameters"], "tool parameters")
            if name in seen:
                raise _invalid("duplicate tool name")
            seen.add(name)
    messages, system = _messages(options)
    return options, messages, system


def _usage(usage: Usage) -> dict[str, Any]:
    values = {
        "inputTokens": _integer(usage.input_tokens, "input usage"),
        "outputTokens": _integer(usage.output_tokens, "output usage"),
        "cacheReadTokens": _integer(usage.cached_tokens, "cache usage"),
        "cacheWriteTokens": _integer(usage.cache_write_tokens, "cache usage"),
        "reasoningTokens": _integer(usage.reasoning_tokens, "reasoning usage"),
    }
    # DSH input is uncached; Libre Claw input is the aggregate prompt count.
    values["inputTokens"] -= values["cacheReadTokens"] + values["cacheWriteTokens"]
    if values["inputTokens"] < 0:
        raise _invalid("cache usage totals")
    values["totalTokens"] = _integer(usage.total_tokens, "total usage")
    return {"type": "usage", "usage": values}


def _failure(code: str = "PROVIDER_ERROR") -> dict[str, Any]:
    return {"type": "finish", "reason": {"kind": "error", "failure": {
        "message": "The model request could not complete. Check the provider configuration and retry.",
        "code": code,
    }}}


def _visible_reasoning(event: ReasoningDelta) -> str:
    """Project readable reasoning while preserving signed metadata in replay."""
    try:
        payload = json.loads(event.text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("type") == "anthropic_message_content":
        return "".join(block["thinking"] for block in payload.get("content", [])
                       if isinstance(block, dict) and block.get("type") == "thinking"
                       and isinstance(block.get("thinking"), str))
    if isinstance(payload, dict) and payload.get("type") == "responses_output_items":
        return "".join(part["text"] for item in payload.get("output", [])
                       if isinstance(item, dict) and item.get("type") == "reasoning"
                       for part in item.get("summary", [])
                       if isinstance(part, dict) and isinstance(part.get("text"), str))
    if event.provider == "openrouter":
        visible = []
        for line in event.text.splitlines():
            try:
                detail = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(detail, dict):
                text = detail.get("text", detail.get("summary"))
                if isinstance(text, str):
                    visible.append(text)
        return "".join(visible)
    return event.text


class _Chunks:
    """Assemble provider deltas without making partial tools executable."""

    def __init__(self) -> None:
        self.next_index = 0
        self.text: tuple[int, str, str] | None = None
        self.tools: dict[str, dict[str, Any]] = {}
        self.reasoning: list[dict[str, str]] = []
        self.reasoning_bytes = 0

    def close_text(self) -> list[dict[str, Any]]:
        if self.text is None:
            return []
        index, kind, text = self.text
        self.text = None
        return [{"type": "block-end", "index": index, "block": {"type": kind, "text": text}}]

    def text_delta(self, kind: str, text: str) -> list[dict[str, Any]]:
        _text(text, "provider text", MAX_CHUNK_BYTES, empty=True)
        result = []
        if self.text is None or self.text[1] != kind:
            result.extend(self.close_text())
            self.text = (self.next_index, kind, "")
            self.next_index += 1
            result.append({"type": "block-start", "index": self.text[0], "blockType": kind})
        index, _, previous = self.text
        if len(previous) + len(text) > MAX_CHUNK_BYTES:
            raise _invalid("provider text size")
        self.text = (index, kind, previous + text)
        result.append({"type": kind + "-delta", "index": index, "text": text})
        return result

    def accept(self, event: Any) -> list[dict[str, Any]]:
        if isinstance(event, TextDelta):
            return self.text_delta("text", event.text)
        if isinstance(event, ReasoningDelta):
            self.reasoning.append({"provider": _text(event.provider, "reasoning provider"),
                                   "text": _text(event.text, "reasoning text", MAX_CHUNK_BYTES, empty=True)})
            self.reasoning_bytes += len(event.text.encode("utf-8"))
            if self.reasoning_bytes > MAX_REQUEST_BYTES or len(self.reasoning) > 2048:
                raise _invalid("reasoning replay size")
            visible = _visible_reasoning(event)
            return self.text_delta("reasoning", visible) if visible else []
        if not isinstance(event, (ToolCallStart, ToolCallDelta, ToolCallReady)):
            raise _invalid("provider event")
        result = self.close_text()
        identity = _text(event.tool_call_id, "tool call ID")
        name = _text(event.name, "tool name")
        tool = self.tools.get(identity)
        if tool is None:
            if len(self.tools) >= 256:
                raise _invalid("provider tool count")
            tool = {"index": self.next_index, "name": name, "arguments": "", "ready": False}
            self.tools[identity] = tool
            self.next_index += 1
            result.extend([
                {"type": "block-start", "index": tool["index"], "blockType": "tool-call"},
                {"type": "tool-call-delta", "index": tool["index"], "id": identity, "name": name, "argumentsDelta": ""},
            ])
        elif tool["ready"] or tool["name"] != name or isinstance(event, ToolCallStart):
            raise _invalid("provider tool identity")
        if isinstance(event, ToolCallDelta):
            fragment = _text(event.partial_json, "tool argument delta", MAX_CHUNK_BYTES, empty=True)
            tool["arguments"] += fragment
            if len(tool["arguments"]) > MAX_CHUNK_BYTES:
                raise _invalid("tool argument size")
            result.append({"type": "tool-call-delta", "index": tool["index"], "id": identity, "argumentsDelta": fragment})
        if isinstance(event, ToolCallReady):
            encoded = _json(_object(event.input, "tool arguments"), MAX_CHUNK_BYTES)
            if tool["arguments"]:
                try:
                    streamed = json.loads(tool["arguments"])
                except json.JSONDecodeError:
                    raise _invalid("provider tool arguments") from None
                if streamed != event.input:
                    raise _invalid("provider tool arguments")
            else:
                tool["arguments"] = encoded
                result.append({"type": "tool-call-delta", "index": tool["index"], "id": identity, "argumentsDelta": encoded})
            tool["ready"] = True
            result.append({"type": "block-end", "index": tool["index"], "block": {
                "type": "tool-call", "id": identity, "name": name, "arguments": tool["arguments"],
            }})
        return result

    def finish(self, event: Done, provider: str, model: str) -> list[dict[str, Any]]:
        if any(not tool["ready"] for tool in self.tools.values()):
            raise _invalid("incomplete provider tool call")
        reason = event.stop_reason
        if reason in {"length", "max_tokens", "max_output_tokens"}:
            kind = "max-tokens"
        elif reason in {"tool_calls", "tool_use"} or (reason is None and self.tools):
            kind = "tool-calls"
        elif reason in {None, "stop", "end_turn", "stop_sequence"}:
            kind = "stop"
        else:
            raise _invalid("provider finish reason")
        if bool(self.tools) != (kind == "tool-calls"):
            raise _invalid("provider finish and tool calls")
        result = self.close_text()
        if event.usage is not None:
            result.append(_usage(event.usage))
        finish: dict[str, Any] = {"type": "finish", "reason": {"kind": kind}}
        if self.reasoning:
            finish["replayState"] = {"response": {"libreClawReasoning": {
                "version": 1, "provider": provider, "model": model, "blocks": self.reasoning,
            }}}
        result.append(finish)
        return result


class CordisLlmBridge:
    """Expose explicit model calls after the host authorizes model access.

    ``authorize`` is checked before each operation and each stream event. It
    must return exactly True; omission denies access. Cancelling the consuming
    task or closing the iterator closes the provider stream without a fallback
    request. Provider/transport errors produce redacted DSH error finishes.
    """

    def __init__(
        self, config: LibreClawConfig, *, authorize: Callable[[], bool] | None = None,
        provider_factory: Callable[..., LLMProvider] = create_provider,
        model_discovery: Callable[..., Awaitable[ModelCatalog]] = discover_models,
    ) -> None:
        self._config = config
        self._authorize = authorize
        self._provider_factory = provider_factory
        self._model_discovery = model_discovery

    def _check_access(self) -> None:
        if self._authorize is None or self._authorize() is not True:
            raise PermissionError("Plugin model access has not been granted.")

    def _provider(self, provider: Any) -> str:
        provider = _text(provider, "provider")
        if provider not in _MODEL_PROVIDERS or not isinstance(self._config.providers.get(provider), Mapping):
            raise CordisLlmError("The requested model-only provider is not configured or supported.")
        return provider

    async def list_providers(self) -> list[dict[str, str]]:
        """List configured model-only routes without credential or network I/O."""
        self._check_access()
        return [{"id": name, "name": label} for name, label in _MODEL_PROVIDERS.items()
                if isinstance(self._config.providers.get(name), Mapping)]

    async def list_configurable_providers(self) -> list[dict[str, str]]:
        """Identify route namespaces without exposing their settings or keys."""
        return [{**row, "settingsNs": "providers." + row["id"]} for row in await self.list_providers()]

    async def _models(self, provider: str) -> tuple[ModelInfo, ...]:
        try:
            catalog = await self._model_discovery(self._config, provider)
            models = tuple(item for item in catalog.models if item.provider == provider)
            if len(models) > 10_000:
                raise CordisLlmError("The provider model catalog exceeds its limit.")
            return models
        except asyncio.CancelledError:
            raise
        except Exception:
            raise CordisLlmError("The provider model catalog is unavailable.") from None

    async def list_models(self, provider: str) -> list[dict[str, str]]:
        """Discover model IDs and labels; omit errors and all connection settings."""
        self._check_access()
        provider = self._provider(provider)
        models = await self._models(provider)
        self._check_access()
        result = [{"id": _text(item.model, "model ID"), "name": _text(item.label, "model label", 4096)} for item in models]
        _json(result, MAX_CHUNK_BYTES)
        return result

    async def _model(self, provider: str, model: str) -> ModelInfo:
        _text(model, "model")
        matches = [item for item in await self._models(provider) if item.model == model]
        self._check_access()
        if not matches:
            raise CordisLlmError("The requested model is not available in this provider's catalog.")
        return matches[0]

    async def resolve_model_info(self, provider: str, model: str) -> dict[str, Any]:
        """Return only model capability metadata used by the DSH model directory."""
        self._check_access()
        provider = self._provider(provider)
        info = await self._model(provider, model)
        result: dict[str, Any] = {"id": model, "name": info.label}
        if info.context_window_tokens is not None:
            result["context"] = {"contextWindow": _integer(info.context_window_tokens, "context limit", 1)}
        configured_maximum = self._config.providers[provider].get("max_tokens")
        maximum = info.max_completion_tokens
        if type(configured_maximum) is int and configured_maximum > 0:
            maximum = min(configured_maximum, maximum) if maximum is not None else configured_maximum
        if maximum is not None:
            result["defaultMaxTokens"] = _integer(maximum, "output limit", 1)
        if info.supported_reasoning_efforts:
            efforts = list(dict.fromkeys(info.supported_reasoning_efforts))
            if len(efforts) > 64:
                raise _invalid("reasoning efforts")
            result["reasoning"] = {"efforts": [{"id": _text(effort, "reasoning effort"), "name": effort} for effort in efforts]}
            default = self._config.providers[provider].get("reasoning_effort")
            if default in efforts:
                result["reasoning"]["defaultEffort"] = default
        _json(result, MAX_CHUNK_BYTES)
        return result

    async def stream(self, options: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Stream a bounded DSH request; only this call's payload reaches a model."""
        self._check_access()
        options, messages, system = _request(options)
        provider = self._provider(options["provider"])
        model = options["model"]
        source = None
        try:
            info = await self._model(provider, model)
            effort = options.get("reasoningEffort")
            if effort is not None and info.supported_reasoning_efforts is not None and effort not in info.supported_reasoning_efforts:
                raise _invalid("unsupported reasoning effort")
            settings = dict(self._config.providers[provider])
            settings["default_model"] = model
            if effort is not None:
                settings["reasoning_effort"] = effort
                if provider == "ollama":
                    settings["think"] = effort
            if "maxTokens" in options:
                settings["max_tokens"] = options["maxTokens"]
            config = replace(
                self._config,
                general=replace(self._config.general, default_provider=provider, default_model=model),
                agent=replace(self._config.agent, system_prompt="", system_prompt_extra=""),
                providers={**self._config.providers, provider: settings},
            )
            self._check_access()
            adapter = self._provider_factory(config, provider_name=provider, model=model)
            tools = [{"name": tool["name"], "description": tool["description"], "input_schema": tool["parameters"]}
                     for tool in options.get("tools", [])]
            source = adapter.complete(messages, tools=tools or None, system=system, stream=True,
                                      temperature=options.get("temperature", 0.7), max_tokens=options.get("maxTokens"))
            chunks = _Chunks()
            bytes_sent = 0
            async for event in source:
                self._check_access()
                if isinstance(event, ProviderError):
                    yield _failure()
                    return
                outputs = chunks.finish(event, provider, model) if isinstance(event, Done) else chunks.accept(event)
                for output in outputs:
                    self._check_access()
                    bytes_sent += len(_json(output, MAX_CHUNK_BYTES).encode("utf-8"))
                    if bytes_sent > MAX_RESPONSE_BYTES:
                        raise _invalid("response size")
                    yield output
                if isinstance(event, Done):
                    return
            yield _failure("INCOMPLETE_RESPONSE")
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except PermissionError:
            raise
        except Exception:
            yield _failure()
        finally:
            if source is not None and callable(close := getattr(source, "aclose", None)):
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Provider cleanup diagnostics can contain request headers.
                    pass
