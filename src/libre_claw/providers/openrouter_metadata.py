# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

import httpx

from libre_claw.auth.api_keys import ApiKeyStore
from libre_claw.config import LibreClawConfig


OPENROUTER_METADATA_TTL_SECONDS = 6 * 60 * 60
OPENROUTER_METADATA_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class OpenRouterModelLimits:
    context_window_tokens: int | None = None
    max_completion_tokens: int | None = None
    source: str = ""

    @property
    def detected(self) -> bool:
        return self.context_window_tokens is not None or self.max_completion_tokens is not None


_CACHE: dict[tuple[str, str, bool], tuple[float, OpenRouterModelLimits]] = {}


async def detect_openrouter_model_limits(
    config: LibreClawConfig,
    *,
    model: str | None = None,
    api_key_store: ApiKeyStore | None = None,
    client: httpx.AsyncClient | None = None,
) -> OpenRouterModelLimits:
    """Return OpenRouter model limits from metadata endpoints, with safe fallback."""
    provider_config = config.providers.get("openrouter", {})
    if isinstance(provider_config, Mapping) and provider_config.get("auto_context_window") is False:
        return OpenRouterModelLimits(source="disabled")

    selected_model = (model or config.general.default_model).strip()
    if not selected_model:
        return OpenRouterModelLimits(source="missing_model")
    base_url = _provider_value(provider_config, "base_url", "https://openrouter.ai/api/v1").rstrip("/")
    api_key = _openrouter_api_key(config, provider_config, api_key_store)
    cache_key = (base_url, selected_model, bool(api_key))
    cached = _CACHE.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < OPENROUTER_METADATA_TTL_SECONDS:
        return cached[1]

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    close_client = client is None
    active_client: httpx.AsyncClient | None = client
    try:
        if active_client is None:
            active_client = httpx.AsyncClient(timeout=OPENROUTER_METADATA_TIMEOUT_SECONDS)
        limits = await _fetch_limits(active_client, base_url, selected_model, headers)
    except Exception:
        limits = OpenRouterModelLimits(source="unavailable")
    finally:
        if close_client and active_client is not None:
            try:
                await active_client.aclose()
            except Exception:
                pass
    _CACHE[cache_key] = (now, limits)
    return limits


def apply_openrouter_model_limits(
    config: LibreClawConfig,
    limits: OpenRouterModelLimits,
    *,
    model: str | None = None,
) -> LibreClawConfig:
    if not limits.detected:
        return config

    agent = config.agent
    if limits.context_window_tokens is not None:
        agent = replace(agent, context_window_tokens=limits.context_window_tokens)

    providers: dict[str, Mapping[str, Any]] = {}
    for name, value in config.providers.items():
        providers[name] = dict(value) if isinstance(value, Mapping) else value
    openrouter_config = dict(providers.get("openrouter", {}))
    if limits.context_window_tokens is not None:
        openrouter_config["detected_context_window_tokens"] = limits.context_window_tokens
    if limits.max_completion_tokens is not None:
        openrouter_config["detected_max_completion_tokens"] = limits.max_completion_tokens
    if limits.source:
        openrouter_config["detected_context_source"] = limits.source
    if model:
        openrouter_config["detected_context_model"] = model
    providers["openrouter"] = openrouter_config
    return replace(config, agent=agent, providers=providers)


async def _fetch_limits(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    headers: Mapping[str, str],
) -> OpenRouterModelLimits:
    if headers:
        limits = await _fetch_limits_from_model_list(client, f"{base_url}/models/user", model, headers, "models/user")
        if limits.detected:
            return limits
    limits = await _fetch_limits_from_model_list(client, f"{base_url}/models", model, headers, "models")
    if limits.detected:
        return limits
    return await _fetch_limits_from_endpoints(client, base_url, model, headers)


async def _fetch_limits_from_model_list(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    headers: Mapping[str, str],
    source: str,
) -> OpenRouterModelLimits:
    response = await client.get(url, headers=dict(headers))
    response.raise_for_status()
    payload = response.json()
    for item in _data_items(payload):
        if str(item.get("id", "")).strip() != model:
            continue
        top_provider = item.get("top_provider")
        top = top_provider if isinstance(top_provider, Mapping) else {}
        context = max(
            _positive_int(item.get("context_length")) or 0,
            _positive_int(top.get("context_length")) or 0,
        )
        max_completion = max(
            _positive_int(item.get("max_completion_tokens")) or 0,
            _positive_int(top.get("max_completion_tokens")) or 0,
        )
        return OpenRouterModelLimits(
            context_window_tokens=context or None,
            max_completion_tokens=max_completion or None,
            source=source,
        )
    return OpenRouterModelLimits(source=f"{source}:not_found")


async def _fetch_limits_from_endpoints(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    headers: Mapping[str, str],
) -> OpenRouterModelLimits:
    if "/" not in model:
        return OpenRouterModelLimits(source="endpoints:unsupported_model_id")
    response = await client.get(f"{base_url}/models/{quote(model, safe='/')}/endpoints", headers=dict(headers))
    response.raise_for_status()
    payload = response.json()
    endpoints = _endpoint_items(payload)
    contexts = [
        value
        for endpoint in endpoints
        for value in (_positive_int(endpoint.get("max_prompt_tokens")), _positive_int(endpoint.get("context_length")))
        if value is not None
    ]
    max_completions = [
        value
        for endpoint in endpoints
        for value in (_positive_int(endpoint.get("max_completion_tokens")),)
        if value is not None
    ]
    return OpenRouterModelLimits(
        context_window_tokens=max(contexts) if contexts else None,
        max_completion_tokens=max(max_completions) if max_completions else None,
        source="endpoints",
    )


def _data_items(payload: object) -> list[Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else payload
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [item for item in data if isinstance(item, Mapping)]
    return []


def _endpoint_items(payload: object) -> list[Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else payload
    if isinstance(data, Mapping):
        endpoints = data.get("endpoints")
        if isinstance(endpoints, Sequence) and not isinstance(endpoints, (str, bytes)):
            return [item for item in endpoints if isinstance(item, Mapping)]
        return [data]
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [item for item in data if isinstance(item, Mapping)]
    return []


def _openrouter_api_key(
    config: LibreClawConfig,
    provider_config: Mapping[str, Any] | object,
    api_key_store: ApiKeyStore | None,
) -> str:
    if not isinstance(provider_config, Mapping):
        provider_config = {}
    api_key_env = _provider_value(provider_config, "api_key_env", "OPENROUTER_API_KEY")
    try:
        store = api_key_store or ApiKeyStore.from_config(config.auth)
        return store.get_api_key("openrouter", api_key_env).value
    except Exception:
        return ""


def _provider_value(config: object, key: str, default: str) -> str:
    if isinstance(config, Mapping):
        value = config.get(key, default)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        integer = int(value)
        return integer if integer > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        integer = int(value)
        return integer if integer > 0 else None
    return None
