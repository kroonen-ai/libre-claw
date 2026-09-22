# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Provider-owned model catalogs, with bounded discovery and offline suggestions.

The catalog is advisory: configured model IDs remain usable even when a provider
does not publish them, and no request is validated against a model allowlist.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

import httpx

from libre_claw.auth.api_keys import ApiKeyStore
from libre_claw.config import LibreClawConfig
from libre_claw.kimi import normalize_moonshot_selection
from libre_claw.providers.capabilities import apply_model_overrides, parse_capabilities
from libre_claw.providers.llamacpp import normalize_llamacpp_base_url
from libre_claw.providers.local import _ollama_api_url, _openai_base_url
from libre_claw.opencode import canonical_opencode_provider, lookup_opencode_key
from libre_claw.providers.opencode import OPENCODE_BASE_URLS, discover_opencode_rows


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model: str
    label: str
    context_window_tokens: int | None = None
    max_completion_tokens: int | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None
    supported_reasoning_efforts: tuple[str, ...] | None = None
    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None
    supports_temperature: bool | None = None
    capability_source: str = "unknown"


@dataclass(frozen=True)
class ModelCatalog:
    models: tuple[ModelInfo, ...]
    source: str
    error: str = ""


@dataclass(frozen=True)
class _CachedCatalog:
    models: tuple[ModelInfo, ...]
    expires_at: float
    error: str = ""


_CACHE_TTL = 300.0
_ERROR_TTL = 30.0
_DISCOVERY_TIMEOUT = 12.0
_MAX_PAGES = 100
_MAX_CACHE_ENTRIES = 128
_CACHE: dict[tuple[str, str], _CachedCatalog] = {}
# Offline readers never access keyrings, the network, or spawn a CLI. Discovery
# binds each settings scope to the credential fingerprint that last resolved it.
_ACTIVE_KEYS: dict[str, tuple[str, str]] = {}
_SELECTED_CACHE: dict[tuple[tuple[str, str], str], tuple[ModelInfo, float]] = {}


@dataclass
class _DiscoveryLock:
    lock: asyncio.Lock
    users: int = 0


_LOCKS: dict[tuple[asyncio.AbstractEventLoop, tuple[str, str]], _DiscoveryLock] = {}

_DEFAULT_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "moonshot": "https://api.kimi.com/coding/v1",
    "ollama": "http://localhost:11434",
    "llamacpp": "http://localhost:8080",
    **OPENCODE_BASE_URLS,
}
_KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshot": "KIMI_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
}


def cached_models(config: LibreClawConfig, provider: str) -> tuple[ModelInfo, ...]:
    """Return in-process cached and configured models, without live I/O."""
    provider = _canonical_provider(provider)
    scope = _settings_scope(config, provider, _provider_config(config, provider))
    key = _ACTIVE_KEYS.get(scope)
    cached = _CACHE.get(key) if key else None
    return _merge_models(config, provider, cached.models if cached else ())


def selected_model_info(config: LibreClawConfig, provider: str, model: str) -> ModelInfo:
    """Get the selected model's cached metadata and per-model config overrides."""
    provider = _canonical_provider(provider)
    settings = _provider_config(config, provider)
    scope = _settings_scope(config, provider, settings)
    key = _ACTIVE_KEYS.get(scope)
    cached = _CACHE.get(key) if key else None
    info = next((item for item in cached.models if item.model == model), None) if cached else None
    info = info or ModelInfo(provider, model, model)
    details = _SELECTED_CACHE.get((key, model)) if key else None
    if details:
        updates = {field.name: getattr(details[0], field.name) for field in fields(info)
                   if field.name not in {"provider", "model", "label"} and getattr(details[0], field.name) is not None}
        info = replace(info, **updates)
    return apply_model_overrides(info, settings)


async def discover_model(
    config: LibreClawConfig, provider: str, model: str, *,
    api_key_store: ApiKeyStore | None = None, client: httpx.AsyncClient | None = None,
    refresh: bool = False,
) -> ModelInfo:
    """Discover a selected model; Ollama show is fetched once, never for every row."""
    provider = _canonical_provider(provider)
    await discover_models(config, provider, api_key_store=api_key_store, client=client, refresh=refresh)
    settings = _provider_config(config, provider)
    if provider != "ollama" or _text(settings.get("api_format"), "ollama").lower() != "ollama":
        return selected_model_info(config, provider, model)
    scope = _settings_scope(config, provider, settings)
    key = _ACTIVE_KEYS.get(scope)
    if key is None:
        return selected_model_info(config, provider, model)
    detail_key = (key, model)
    async with _catalog_lock((scope, key[1] + ":" + model)):
        cached = _SELECTED_CACHE.get(detail_key)
        if cached and not refresh and cached[1] > time.monotonic():
            return selected_model_info(config, provider, model)
        try:
            async with asyncio.timeout(_DISCOVERY_TIMEOUT):
                api_key = await asyncio.to_thread(_resolve_api_key, config, provider, settings, api_key_store)
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                async def fetch(active_client: httpx.AsyncClient) -> ModelInfo:
                    response = await active_client.post(_ollama_api_url(_base_url(provider, settings), "show"),
                                                        headers=headers, json={"model": model}, timeout=8.0)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, Mapping):
                        raise _DiscoveryError("The provider returned invalid model details.")
                    model_info = payload.get("model_info")
                    context = None
                    if isinstance(model_info, Mapping):
                        context = next((_positive_int(value) for name, value in model_info.items()
                                        if str(name).endswith(".context_length") and _positive_int(value)), None)
                    return ModelInfo(provider, model, model, context_window_tokens=context,
                                     **parse_capabilities(provider, payload))
                if client is not None:
                    info = await fetch(client)
                else:
                    async with httpx.AsyncClient(timeout=8.0) as owned_client:
                        info = await fetch(owned_client)
                _SELECTED_CACHE[detail_key] = (info, time.monotonic() + _CACHE_TTL)
        except (httpx.HTTPError, OSError, RuntimeError, TimeoutError, ValueError):
            info = cached[0] if cached else ModelInfo(provider, model, model)
            _SELECTED_CACHE[detail_key] = (info, time.monotonic() + _ERROR_TTL)
        while len(_SELECTED_CACHE) > _MAX_CACHE_ENTRIES:
            _SELECTED_CACHE.pop(next(iter(_SELECTED_CACHE)))
    return selected_model_info(config, provider, model)


async def discover_models(
    config: LibreClawConfig,
    provider: str,
    *,
    api_key_store: ApiKeyStore | None = None,
    client: httpx.AsyncClient | None = None,
    refresh: bool = False,
) -> ModelCatalog:
    """Discover provider models, retaining configured IDs and stale data on failure.

    Successful discoveries are cached for five minutes; failures for thirty
    seconds. Endpoint, service, auth settings, and resolved credentials isolate
    entries, so another account or local server cannot reuse the wrong list.
    ``refresh=True`` bypasses either TTL. An injected client remains caller-owned.
    """
    provider = _canonical_provider(provider)
    settings = _provider_config(config, provider)
    scope = _settings_scope(config, provider, settings)
    if provider not in {*_DEFAULT_URLS, "codex"}:
        return ModelCatalog(_merge_models(config, provider, ()), "configured", "Unsupported provider.")

    try:
        async with asyncio.timeout(_DISCOVERY_TIMEOUT):
            if provider == "codex":
                api_key = await asyncio.to_thread(_codex_identity)
            else:
                api_key = await asyncio.to_thread(
                    _resolve_api_key, config, provider, settings, api_key_store
                )
    except (OSError, RuntimeError, TimeoutError, ValueError):
        # Do not surface credential-store exception text or use a previous
        # account's catalog when its current credentials cannot be resolved.
        return ModelCatalog(
            _merge_models(config, provider, ()), "configured", "Could not read provider credentials."
        )

    key = (scope, _fingerprint(api_key))
    _ACTIVE_KEYS[scope] = key
    async with _catalog_lock(key):
        return await _discover_cached(config, provider, settings, api_key, client, refresh, key)


@asynccontextmanager
async def _catalog_lock(key: tuple[str, str]) -> AsyncIterator[None]:
    # Coalesce initial loads without leaving locks bound to a completed event
    # loop. A cancelled waiter does not cancel another caller's active request.
    lock_key = (asyncio.get_running_loop(), key)
    entry = _LOCKS.setdefault(lock_key, _DiscoveryLock(asyncio.Lock()))
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if not entry.users:
            _LOCKS.pop(lock_key, None)


async def _discover_cached(
    config: LibreClawConfig,
    provider: str,
    settings: Mapping[str, Any],
    api_key: str,
    client: httpx.AsyncClient | None,
    refresh: bool,
    key: tuple[str, str],
) -> ModelCatalog:
    cached = _CACHE.get(key)
    if not refresh and cached and cached.expires_at > time.monotonic():
        return _catalog(config, provider, cached)

    try:
        async with asyncio.timeout(_DISCOVERY_TIMEOUT):
            if provider == "codex":
                models = await _discover_codex(config, settings)
            elif provider in {"openai", "anthropic", "deepseek", "moonshot"} and not api_key:
                raise _DiscoveryError("Set a provider API key to load available models.")
            elif client is not None:
                models = await _discover_http(client, provider, settings, api_key, refresh=refresh)
            else:
                async with httpx.AsyncClient(timeout=8.0) as owned_client:
                    models = await _discover_http(owned_client, provider, settings, api_key, refresh=refresh)
        if not models:
            raise _DiscoveryError("The provider returned no available models.")
    except (httpx.HTTPError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        entry = _CachedCatalog(
            cached.models if cached else (),
            time.monotonic() + _ERROR_TTL,
            _discovery_error(exc),
        )
        _store_cache(key, entry)
        return _catalog(config, provider, entry)

    entry = _CachedCatalog(models, time.monotonic() + _CACHE_TTL)
    _store_cache(key, entry)
    return ModelCatalog(_merge_models(config, provider, models), "live")


class _DiscoveryError(RuntimeError):
    pass


def _catalog(config: LibreClawConfig, provider: str, entry: _CachedCatalog) -> ModelCatalog:
    return ModelCatalog(
        _merge_models(config, provider, entry.models),
        "cache" if entry.models else "configured",
        entry.error,
    )


def _store_cache(key: tuple[str, str], entry: _CachedCatalog) -> None:
    _CACHE[key] = entry
    evicted = set()
    while len(_CACHE) > _MAX_CACHE_ENTRIES:
        oldest_key = next(iter(_CACHE))
        _CACHE.pop(oldest_key)
        evicted.add(oldest_key)
    for scope, active_key in tuple(_ACTIVE_KEYS.items()):
        if active_key in evicted:
            _ACTIVE_KEYS.pop(scope, None)


def _canonical_provider(provider: str) -> str:
    provider = canonical_opencode_provider(provider)
    if provider == "local":
        return "ollama"
    if provider in {"llama-cpp", "llama_cpp", "llama.cpp", "llama-swap", "llamaswap"}:
        return "llamacpp"
    return provider


def _provider_config(config: LibreClawConfig, provider: str) -> dict[str, Any]:
    raw = config.providers.get(provider, {})
    settings = dict(raw) if isinstance(raw, Mapping) else {}
    if provider == "moonshot":
        _, settings = normalize_moonshot_selection(settings, _text(settings.get("default_model")))
    return settings


def _base_url(provider: str, settings: Mapping[str, Any]) -> str:
    configured = _text(settings.get("base_url"))
    if not configured and provider in {"openai", "anthropic"}:
        configured = os.getenv(f"{provider.upper()}_BASE_URL", "")
    return (configured or _DEFAULT_URLS.get(provider, "")).rstrip("/")


def _settings_scope(config: LibreClawConfig, provider: str, settings: Mapping[str, Any]) -> str:
    env = _text(settings.get("api_key_env"), _KEY_ENVS.get(provider, ""))
    # Hash credentials even in memory keys; never retain them in catalog records.
    identity = [
        provider, _base_url(provider, settings),
        _text(settings.get("service")), _text(settings.get("api_format"), "ollama"),
        env, os.getenv(env, ""),
        os.getenv("MOONSHOT_API_KEY", "") if provider == "moonshot" else "",
        os.getenv("KIMI_API_KEY", "") if provider == "moonshot" else "",
        os.getenv("OPENCODE_API_KEY", "") if provider == "opencode-go" else "",
        str(config.auth.fallback_keys_path), config.auth.keyring_service,
    ]
    if provider == "codex":
        identity.extend([
            _text(settings.get("executable"), "codex"),
            os.getenv("CODEX_HOME", os.path.expanduser("~/.codex")),
            str(config.general.working_directory),
        ])
    return _fingerprint(json.dumps(identity))


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _codex_identity() -> str:
    # Login/logout and CLI configuration changes invalidate the account's list.
    codex_directory = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
    identity = []
    for name in ("auth.json", "config.toml"):
        try:
            stat = (codex_directory / name).stat()
            identity.append((name, stat.st_mtime_ns, stat.st_size, stat.st_ino))
        except FileNotFoundError:
            identity.append((name, None))
    return json.dumps(identity)


def _resolve_api_key(
    config: LibreClawConfig,
    provider: str,
    settings: Mapping[str, Any],
    store: ApiKeyStore | None,
) -> str:
    store = store or ApiKeyStore.from_config(config.auth)
    env = _text(settings.get("api_key_env"), _KEY_ENVS.get(provider, ""))
    if provider in OPENCODE_BASE_URLS:
        configured_env = settings.get("api_key_env")
        return lookup_opencode_key(store, provider, configured_env if isinstance(configured_env, str) else None).value or ""
    aliases = {
        "moonshot": ("kimi",), "ollama": ("local",), "llamacpp": ("llama-cpp", "llama-swap"),
    }.get(provider, ())
    lookup = store.get_api_key(provider, env or None, aliases=aliases)
    if not lookup.value and provider == "moonshot":
        alternate = "MOONSHOT_API_KEY" if env != "MOONSHOT_API_KEY" else "KIMI_API_KEY"
        lookup = store.get_api_key(provider, alternate, aliases=aliases)
    return lookup.value or ""


def _merge_models(
    config: LibreClawConfig, provider: str, discovered: Sequence[ModelInfo]
) -> tuple[ModelInfo, ...]:
    settings = _provider_config(config, provider)
    configured = [_text(settings.get("default_model"))]
    for section in (config.general, config.telegram):
        if _canonical_provider(section.default_provider) == provider:
            configured.append(section.default_model)
    if _canonical_provider(config.goal.judge_provider) == provider:
        configured.append(config.goal.judge_model)
    configured.extend(
        route.model for route in config.fallback.routes
        if _canonical_provider(route.provider) == provider
    )
    models = {model.model: model for model in discovered}
    for model_id in configured:
        model_id = model_id.strip()
        if model_id:
            models.setdefault(model_id, ModelInfo(provider, model_id, model_id))
    scope = _settings_scope(config, provider, settings)
    key = _ACTIVE_KEYS.get(scope)
    for model_id, info in tuple(models.items()):
        detail = _SELECTED_CACHE.get((key, model_id)) if key else None
        if detail:
            updates = {field.name: getattr(detail[0], field.name) for field in fields(info)
                       if field.name not in {"provider", "model", "label"} and getattr(detail[0], field.name) is not None}
            models[model_id] = replace(info, **updates)
    return tuple(sorted((apply_model_overrides(item, settings) for item in models.values()),
                        key=lambda item: (item.label.casefold(), item.model)))


async def _discover_http(
    client: httpx.AsyncClient, provider: str, settings: Mapping[str, Any], api_key: str,
    *, refresh: bool = False,
) -> tuple[ModelInfo, ...]:
    base_url = _base_url(provider, settings)
    if provider in OPENCODE_BASE_URLS:
        rows = await discover_opencode_rows(client, provider, base_url, api_key, refresh=refresh)
        return _parse_models(provider, rows)
    headers: dict[str, str] = {}
    if provider == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        # Anthropic's SDK appends /v1 to its base URL (including custom prefixes).
        url = base_url + "/v1/models"
    elif provider == "ollama" and _text(settings.get("api_format"), "ollama").lower() == "ollama":
        url = _ollama_api_url(base_url, "tags")
    elif provider == "llamacpp":
        try:
            url = normalize_llamacpp_base_url(base_url) + "/v1/models"
        except ValueError as exc:
            raise _DiscoveryError(str(exc)) from exc
    elif provider == "ollama":
        url = _openai_base_url(base_url) + "models"
    else:
        url = base_url + "/models"
    if api_key and provider != "anthropic":
        headers["Authorization"] = f"Bearer {api_key}"

    models: dict[str, ModelInfo] = {}
    params: dict[str, str | int] = {"limit": 1000} if provider == "anthropic" else {}
    cursors: set[str] = set()
    for _ in range(_MAX_PAGES):
        response = await client.get(url, headers=headers, params=params, timeout=8.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise _DiscoveryError("The provider returned an invalid model list.")
        field = "models" if provider == "ollama" and url.endswith("/tags") else "data"
        rows = payload.get(field)
        if not isinstance(rows, list):
            raise _DiscoveryError("The provider returned an invalid model list.")
        for model in _parse_models(provider, rows):
            models[model.model] = model
        if provider != "anthropic" or payload.get("has_more") is not True:
            return tuple(models.values())
        cursor = _text(payload.get("last_id"))
        if not cursor or cursor in cursors:
            raise _DiscoveryError("The provider returned invalid model pagination.")
        cursors.add(cursor)
        params["after_id"] = cursor
    raise _DiscoveryError("The provider returned too many model pages.")


def _parse_models(provider: str, rows: Sequence[Any]) -> tuple[ModelInfo, ...]:
    result = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("hidden") is True:
            continue
        architecture = row.get("architecture")
        if isinstance(architecture, Mapping):
            outputs = architecture.get("output_modalities")
            if isinstance(outputs, list) and outputs and "text" not in outputs:
                continue
        model_id = _text(row.get("id"))
        if provider in {"codex", "ollama"}:
            model_id = _text(row.get("model")) or model_id or _text(row.get("name"))
        if not model_id:
            continue
        label = (
            _text(row.get("display_name")) or _text(row.get("displayName"))
            or _text(row.get("name")) or model_id
        )
        top_provider = row.get("top_provider")
        top_provider = top_provider if isinstance(top_provider, Mapping) else {}
        limit = row.get("limit") if provider in OPENCODE_BASE_URLS else {}
        limit = limit if isinstance(limit, Mapping) else {}
        context = (
            _positive_int(row.get("context_length"))
            or _positive_int(row.get("context_window"))
            or _positive_int(row.get("context_window_tokens"))
            or _positive_int(row.get("max_input_tokens"))
            or _positive_int(top_provider.get("context_length"))
            or _positive_int(limit.get("context"))
        )
        output = (
            _positive_int(row.get("max_completion_tokens"))
            or _positive_int(row.get("max_output_tokens"))
            or _positive_int(row.get("max_tokens"))
            or _positive_int(top_provider.get("max_completion_tokens"))
            or _positive_int(limit.get("output"))
        )
        result.append(ModelInfo(provider, model_id, label, context, output, **parse_capabilities(provider, row)))
    return tuple(result)


async def _discover_codex(
    config: LibreClawConfig, settings: Mapping[str, Any]
) -> tuple[ModelInfo, ...]:
    # Official app-server model/list uses the installed CLI's account and cache.
    # This handshake never starts a task, a model turn, or an inference request.
    process = await asyncio.create_subprocess_exec(
        _text(settings.get("executable"), "codex"), "app-server",
        cwd=config.general.working_directory,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=2**20,
    )
    try:
        assert process.stdin is not None and process.stdout is not None

        async def send(message: dict[str, Any]) -> None:
            assert process.stdin is not None
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def receive(request_id: int) -> Mapping[str, Any]:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise _DiscoveryError("Codex model discovery exited before returning models.")
                response = json.loads(line)
                if not isinstance(response, Mapping) or response.get("id") != request_id:
                    continue
                if "error" in response or not isinstance(response.get("result"), Mapping):
                    raise _DiscoveryError("Codex could not list models. Check the CLI login and version.")
                return response["result"]

        await send({
            "id": 0, "method": "initialize",
            "params": {"clientInfo": {"name": "libre_claw", "version": "0.1.0"}},
        })
        await receive(0)
        await send({"method": "initialized", "params": {}})
        models: list[ModelInfo] = []
        cursor: str | None = None
        cursors: set[str] = set()
        for request_id in range(1, _MAX_PAGES + 1):
            params: dict[str, Any] = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            await send({"id": request_id, "method": "model/list", "params": params})
            payload = await receive(request_id)
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise _DiscoveryError("Codex returned an invalid model list.")
            models.extend(_parse_models("codex", rows))
            cursor = _text(payload.get("nextCursor"))
            if not cursor:
                return tuple(models)
            if cursor in cursors:
                raise _DiscoveryError("Codex returned invalid model pagination.")
            cursors.add(cursor)
        raise _DiscoveryError("Codex returned too many model pages.")
    finally:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _discovery_error(exc: Exception) -> str:
    if isinstance(exc, _DiscoveryError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Model discovery failed (HTTP {exc.response.status_code})."
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "Model discovery timed out."
    if isinstance(exc, FileNotFoundError):
        return "The configured Codex CLI executable was not found."
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "The provider returned an invalid model list."
    return "Could not reach the provider model catalog."
