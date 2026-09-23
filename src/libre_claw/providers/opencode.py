# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""OpenCode's plan-specific catalogs and metadata-selected API protocols.

Availability comes from OpenCode itself. Protocols and capabilities come from
Models.dev, the same public metadata source used by OpenCode. Metadata never
selects a credential destination or installs an SDK package.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from libre_claw import __version__
from libre_claw.core.session import ChatMessage
from libre_claw.providers.anthropic import AnthropicProvider, AsyncAnthropic
from libre_claw.providers.base import (
    Done, LLMProvider, ProviderConfigurationError, ProviderError,
    ReasoningDelta, StreamEvent, ToolCallReady, ToolCallStart, ToolSchema,
)
from libre_claw.providers.openai import (
    AsyncOpenAI, OpenAIProvider, _format_assistant_message, _object_field,
)


OPENCODE_BASE_URLS = {
    "opencode": "https://opencode.ai/zen/v1",
    "opencode-go": "https://opencode.ai/zen/go/v1",
}
MODELS_METADATA_URL = "https://models.dev/api.json"
USER_AGENT = f"libre-claw/{__version__}"
_METADATA_TTL = 300.0
_METADATA_ERROR_TTL = 30.0
_MAX_METADATA_BYTES = 16 * 1024 * 1024
_METADATA: dict[str, dict[str, Any]] = {}
_METADATA_EXPIRES = 0.0


@dataclass
class _MetadataLock:
    lock: asyncio.Lock
    users: int = 0


_METADATA_LOCKS: dict[asyncio.AbstractEventLoop, _MetadataLock] = {}
_PROTOCOLS = {
    "@ai-sdk/openai-compatible": "chat",
    "@ai-sdk/openai": "responses",
    "@ai-sdk/anthropic": "anthropic",
    "@ai-sdk/google": "google",
}
_FORMAT_ALIASES = {
    "openai": "chat", "openai-compatible": "chat", "chat-completions": "chat",
    "openai-responses": "responses", "messages": "anthropic", "gemini": "google",
}


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Reuse a caller's transport without inheriting auth or following redirects."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.client.send(request, stream=True, auth=None, follow_redirects=False)


class _PrivateLogger:
    def __init__(self, logger, secret: str):
        self.logger, self.secret = logger, secret

    def __getattr__(self, method):
        def log(event, **fields):
            return getattr(self.logger, method)(event, **{
                key: value.replace(self.secret, "[redacted]") if self.secret and isinstance(value, str) else value
                for key, value in fields.items()
            })
        return log


class _CheckedMessagesStream:
    """Require Anthropic's terminal event before accepting parsed tool blocks."""

    def __init__(self, manager):
        self.manager = manager

    async def __aenter__(self):
        self.stream = await self.manager.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.manager.__aexit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    async def __aiter__(self):
        completed = False
        async for event in self.stream:
            if getattr(event, "type", None) == "message_stop":
                completed = True
            yield event
        if not completed:
            raise ProviderConfigurationError("The Messages stream ended before its completion marker.")


class _CheckedMessages:
    def __init__(self, messages):
        self.messages = messages

    def stream(self, **request):
        return _CheckedMessagesStream(self.messages.stream(**request))

    def __getattr__(self, name):
        return getattr(self.messages, name)


class _CheckedAnthropicClient:
    def __init__(self, client):
        self.client = client
        self.messages = _CheckedMessages(client.messages)

    def with_options(self, **kwargs):
        return _CheckedAnthropicClient(self.client.with_options(**kwargs))

    def __getattr__(self, name):
        return getattr(self.client, name)


def normalize_opencode_base_url(base_url: str) -> str:
    try:
        parts = urlsplit(base_url.strip())
        valid = (parts.scheme in {"http", "https"} and bool(parts.hostname)
                 and not parts.username and not parts.password and not parts.query and not parts.fragment)
        parts.port
        if not valid or any(character.isspace() or ord(character) < 32 for character in base_url):
            raise ValueError
    except (ValueError, AttributeError):
        raise ProviderConfigurationError("OpenCode base_url must be an HTTP or HTTPS API URL without credentials, query, or fragment.") from None
    path = parts.path.rstrip("/")
    # All adapters share one versioned API root; Anthropic's SDK appends /v1.
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def opencode_metadata(
    client: httpx.AsyncClient | None = None, *, refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Read only public metadata, without forwarding any client credentials."""
    global _METADATA_EXPIRES
    if not refresh and _METADATA_EXPIRES > time.monotonic():
        return _METADATA
    loop = asyncio.get_running_loop()
    entry = _METADATA_LOCKS.setdefault(loop, _MetadataLock(asyncio.Lock()))
    entry.users += 1
    try:
        async with entry.lock:
            if not refresh and _METADATA_EXPIRES > time.monotonic():
                return _METADATA
            async def fetch(active: httpx.AsyncClient):
                # Construct directly: borrowed-client default headers, cookies,
                # and auth must not cross into the public metadata request.
                request = httpx.Request("GET", MODELS_METADATA_URL,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    extensions={"timeout": httpx.Timeout(8).as_dict()})
                response = await active.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > _MAX_METADATA_BYTES:
                            raise ValueError("OpenCode model metadata exceeds its size limit")
                        body.extend(chunk)
                    payload = json.loads(body)
                finally:
                    await response.aclose()
                if not isinstance(payload, dict):
                    raise ValueError("Invalid model metadata")
                parsed = {}
                for provider in OPENCODE_BASE_URLS:
                    record = payload.get(provider)
                    if isinstance(record, dict) and isinstance(record.get("models"), dict):
                        parsed[provider] = record
                if not parsed:
                    raise ValueError("Missing OpenCode metadata")
                _METADATA.clear()
                _METADATA.update(parsed)
            try:
                if client is not None:
                    await fetch(client)
                else:
                    async with httpx.AsyncClient(timeout=8) as owned:
                        await fetch(owned)
                _METADATA_EXPIRES = time.monotonic() + _METADATA_TTL
            except (httpx.HTTPError, OSError, ValueError, TimeoutError):
                # Keep public stale metadata through a temporary outage. An
                # unknown model still requires an explicit protocol override.
                _METADATA_EXPIRES = time.monotonic() + _METADATA_ERROR_TTL
            return _METADATA
    finally:
        entry.users -= 1
        if not entry.users:
            _METADATA_LOCKS.pop(loop, None)


def metadata_model(provider: str, model: str, metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    record = metadata.get(provider, {})
    rows = record.get("models", {}) if isinstance(record, Mapping) else {}
    row = rows.get(model) if isinstance(rows, Mapping) else None
    if not isinstance(row, Mapping):
        return None
    result = dict(row)
    override = row.get("provider")
    npm = override.get("npm") if isinstance(override, Mapping) else None
    result["_opencode_npm"] = npm or record.get("npm")
    result["_opencode_metadata"] = True
    return result


async def discover_opencode_rows(
    client: httpx.AsyncClient, provider: str, base_url: str, api_key: str,
    *, refresh: bool = False,
) -> list[dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = await client.get(normalize_opencode_base_url(base_url) + "/models",
                                headers=headers, timeout=8, follow_redirects=False)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid OpenCode model catalog")
    metadata = await opencode_metadata(client, refresh=refresh)
    result = []
    for row in payload["data"]:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            continue
        enriched = metadata_model(provider, row["id"], metadata) or {}
        modalities = enriched.get("modalities")
        outputs = modalities.get("output") if isinstance(modalities, Mapping) else None
        if isinstance(outputs, list) and "text" not in outputs:
            continue
        # The live catalog owns identifiers and availability. Metadata-only
        # models, including deprecated entries, never add catalog rows.
        result.append({**enriched, **row})
    return result


class _OpenCodeChatProvider(OpenAIProvider):
    def __init__(self, *args, reasoning_scope: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.reasoning_scope = reasoning_scope

    def _max_tokens_field(self) -> str:
        return "max_tokens"

    def _accept_stop_with_complete_tools(self) -> bool:
        """OpenCode gateways can label a complete tool payload as ``stop``."""
        return True

    def _reasoning_delta(self, delta: Any) -> ReasoningDelta | None:
        text = _object_field(delta, "reasoning_content") or _object_field(delta, "reasoning")
        return ReasoningDelta(str(text), self.reasoning_scope) if isinstance(text, str) and text else None

    def _format_assistant_message(self, blocks):
        message = _format_assistant_message(blocks, reasoning_provider=self.reasoning_scope)
        if message.get("tool_calls") and message.get("content") is None:
            # Some compatible reasoning APIs reject null on tool-only turns.
            message["content"] = ""
        return message

    def _finalize_request(self, request: dict[str, Any]) -> None:
        super()._finalize_request(request)
        # Keep compatibility with the declared minimum OpenAI SDK version.
        if effort := request.pop("reasoning_effort", None):
            request.setdefault("extra_body", {})["reasoning_effort"] = effort

    def _response_error(self, stop_reason: str | None, has_tool_calls: bool) -> str | None:
        if stop_reason is None:
            return f"{self.display_name} stream ended before its completion marker. No tool calls were executed."
        if has_tool_calls and stop_reason != "tool_calls":
            return f"{self.display_name} did not complete its tool calls. No tool calls were executed."
        if stop_reason == "tool_calls" and not has_tool_calls:
            return f"{self.display_name} returned a tool completion without any tool calls."
        if stop_reason not in {"stop", "tool_calls", "length"}:
            return f"{self.display_name} did not complete a valid response."
        return None


class OpenCodeProvider(LLMProvider):
    """One plan and model, routed by advertised protocol without model lists."""

    def __init__(
        self, api_key: str, model: str, max_tokens: int, *, provider: str = "opencode",
        base_url: str | None = None, api_format: str = "auto", reasoning_effort: str | None = None,
        session_id: str | None = None, client: httpx.AsyncClient | None = None,
        catalog_client: httpx.AsyncClient | None = None, prompt_caching: bool = False,
    ) -> None:
        if provider not in OPENCODE_BASE_URLS:
            raise ProviderConfigurationError("Choose the OpenCode Zen or OpenCode Go provider.")
        if not isinstance(api_format, str):
            raise ProviderConfigurationError("OpenCode api_format must be a string.")
        api_format = _FORMAT_ALIASES.get(api_format.strip().lower(), api_format.strip().lower())
        if api_format not in {"auto", "chat", "responses", "anthropic", "google"}:
            raise ProviderConfigurationError("OpenCode api_format must be auto, chat, responses, anthropic, or google.")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ProviderConfigurationError("OpenCode max_tokens must be a positive integer.")
        self.api_key, self.model, self.max_tokens = api_key, model, max_tokens
        self.provider = provider
        self.base_url = normalize_opencode_base_url(base_url or OPENCODE_BASE_URLS[provider])
        self.api_format = api_format
        self.reasoning_effort = reasoning_effort
        self.prompt_caching = prompt_caching
        self.session_id = session_id
        self._fallback_session_id = uuid.uuid4().hex
        self._client = client
        self._catalog_client = catalog_client or client
        self._delegates: dict[str, LLMProvider] = {}
        self._scope = f"{provider}:{hashlib.sha256((self.base_url + ':' + model).encode()).hexdigest()[:24]}"
        self.display_name = "OpenCode Go" if provider == "opencode-go" else "OpenCode Zen"

    def _headers(self, system: str | None) -> dict[str, str]:
        del system
        # A random conversation identity keeps identical opening prompts in
        # separate conversations distinct and does not fingerprint user text.
        session = hashlib.sha256(str(self.session_id).encode()).hexdigest() if self.session_id else self._fallback_session_id
        return {"User-Agent": USER_AGENT, "x-opencode-session": session}

    async def _protocol(self) -> str:
        if self.api_format != "auto":
            return self.api_format
        row = metadata_model(self.provider, self.model, await opencode_metadata(self._catalog_client))
        protocol = _PROTOCOLS.get(row.get("_opencode_npm")) if row else None
        if protocol is None:
            raise ProviderConfigurationError(
                f"The API protocol for {self.display_name} model '{self.model}' is not available. "
                f"Refresh the model catalog, or set [providers.{self.provider}].api_format explicitly."
            )
        if self.model_info is None:
            # Direct users of this provider (outside the agent's catalog
            # binding) still get advertised limits and request capabilities.
            from libre_claw.providers.model_catalog import _parse_models
            parsed = _parse_models(self.provider, [{**row, "id": self.model}])
            if parsed:
                self.model_info = parsed[0]
        return protocol

    def _delegate(self, protocol: str, headers: Mapping[str, str]) -> LLMProvider:
        delegate = self._delegates.get(protocol)
        if delegate is None:
            sdk_args = {}
            if protocol in {"chat", "anthropic"}:
                # Anthropic's x-api-key header would otherwise survive a
                # redirect to another origin. Pin both SDKs to this API root.
                sdk_args["http_client"] = httpx.AsyncClient(
                    transport=_BorrowedTransport(self._client) if self._client is not None else None,
                    timeout=httpx.Timeout(600, connect=10), follow_redirects=False,
                )
            if protocol == "chat":
                client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                                     default_headers=dict(headers), max_retries=0, **sdk_args)
                delegate = _OpenCodeChatProvider(self.api_key, self.model, self.max_tokens,
                    base_url=self.base_url, display_name=self.display_name, client=client,
                    prompt_caching=False, reasoning_scope=self._scope + ":chat")
            elif protocol == "anthropic":
                base = self.base_url.removesuffix("/v1")
                client = AsyncAnthropic(api_key=self.api_key, base_url=base,
                                        default_headers=dict(headers), max_retries=0, **sdk_args)
                delegate = AnthropicProvider(self.api_key, self.model, self.max_tokens,
                    base_url=base, client=_CheckedAnthropicClient(client), prompt_caching=self.prompt_caching)
            elif protocol == "responses":
                from libre_claw.providers.responses import ResponsesProvider
                delegate = ResponsesProvider(self.api_key, self.model, self.max_tokens,
                    base_url=self.base_url, client=self._client, default_headers=dict(headers),
                    provider_scope=self.provider)
            else:
                from libre_claw.providers.opencode_google import OpenCodeGoogleProvider
                delegate = OpenCodeGoogleProvider(self.api_key, self.model, self.max_tokens,
                    base_url=self.base_url, client=self._client, default_headers=dict(headers),
                    display_name=self.display_name, provider_scope=self._scope + ":google")
            self._delegates[protocol] = delegate
            if hasattr(delegate, "_logger"):
                delegate._logger = _PrivateLogger(delegate._logger, self.api_key)
        if protocol in {"chat", "anthropic"}:
            delegate._client = delegate._client.with_options(default_headers=dict(headers))
        else:
            delegate.default_headers = dict(headers)
        delegate.model_info = self.model_info
        delegate.reasoning_effort = self.reasoning_effort
        for field in ("_capability_config", "_capability_provider", "auto_context_window"):
            if hasattr(self, field):
                setattr(delegate, field, getattr(self, field))
        return delegate

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSchema] | None = None,
        system: str | None = None, stream: bool = True, temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            protocol = await self._protocol()
            delegate = self._delegate(protocol, self._headers(system))
            anthropic_scope = self._scope + ":anthropic"
            if protocol == "anthropic":
                # Anthropic's opaque content belongs only to this plan/endpoint/
                # model. Never replay direct-Anthropic or another plan's data.
                messages = [replace(message, content=[
                    {**block, "provider": "anthropic"} if block.get("provider") == anthropic_scope else block
                    for block in message.content
                    if block.get("type") != "provider_reasoning" or block.get("provider") == anthropic_scope
                ]) for message in messages]
            pending_calls: list[ToolCallReady] = []
            call_ids: set[str] = set()
            started_calls: set[str] = set()
            completed = False
            async with aclosing(delegate.complete(messages, tools, system, stream, temperature, max_tokens)) as response:
                async for event in response:
                    if protocol == "anthropic" and isinstance(event, ReasoningDelta):
                        event = ReasoningDelta(event.text, anthropic_scope)
                    if isinstance(event, ProviderError):
                        yield self._error(event.message)
                        return
                    if isinstance(event, ToolCallStart):
                        if not event.tool_call_id or not event.name or event.tool_call_id in started_calls:
                            yield self._error("The response contained an invalid or duplicate tool call. No tool calls were executed.")
                            return
                        started_calls.add(event.tool_call_id)
                    if isinstance(event, ToolCallReady):
                        # SDKs can finish one tool block before the entire
                        # message, or before discovering a malformed sibling.
                        if (not event.tool_call_id or not event.name or event.tool_call_id in call_ids
                                or not isinstance(event.input, dict)):
                            yield self._error("The response contained an invalid or duplicate tool call. No tool calls were executed.")
                            return
                        try:
                            json.dumps(event.input, allow_nan=False)
                        except (TypeError, ValueError):
                            yield self._error("The response contained invalid tool arguments. No tool calls were executed.")
                            return
                        call_ids.add(event.tool_call_id)
                        pending_calls.append(event)
                        continue
                    if isinstance(event, Done):
                        if started_calls - call_ids:
                            yield self._error("The response contained an unfinished tool call. No tool calls were executed.")
                            return
                        if protocol == "anthropic":
                            accepted = {"end_turn", "stop_sequence", "max_tokens", "refusal", "pause_turn"}
                            if ((pending_calls and event.stop_reason != "tool_use")
                                    or (not pending_calls and event.stop_reason not in accepted)):
                                yield self._error("The Messages response did not complete. No tool calls were executed.")
                                return
                        for call in pending_calls:
                            yield call
                        pending_calls.clear()
                        completed = True
                    yield event
            if not completed:
                yield self._error("The stream ended before completion. No tool calls were executed.")
        except asyncio.CancelledError:
            raise
        except ProviderConfigurationError as exc:
            yield ProviderError(str(exc).replace(self.api_key, "[redacted]") if self.api_key else str(exc))
        except Exception:
            yield ProviderError(f"{self.display_name} request could not be completed.")

    def _error(self, message: str) -> ProviderError:
        if self.api_key:
            message = message.replace(self.api_key, "[redacted]")
        if not message.startswith(self.display_name):
            message = f"{self.display_name}: {message}"
        return ProviderError(message)
