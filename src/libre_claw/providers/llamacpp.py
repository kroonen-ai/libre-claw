# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from libre_claw.providers.base import LLMProvider, ProviderConfigurationError
from libre_claw.providers.local import LocalApiFormat, LocalProvider, LocalToolMode, OllamaThink

DEFAULT_LLAMACPP_BASE_URL = "http://localhost:8080"


class LlamaCppDiscoveryError(RuntimeError):
    """Raised when a llama.cpp / llama-swap endpoint cannot list its models."""


def normalize_llamacpp_base_url(base_url: str) -> str:
    """Normalize a llama.cpp endpoint URL.

    Accept a server root, its OpenAI /v1 path, or a copied web UI URL while
    retaining reverse-proxy prefixes. Browser queries and fragments are not
    part of the API endpoint.
    """
    cleaned = base_url.strip()
    try:
        parts = urlsplit(cleaned)
        valid = parts.scheme in {"http", "https"} and bool(parts.hostname)
        # Accessing .port also checks malformed and out-of-range ports.
        parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid llama.cpp HTTP or HTTPS server URL.") from exc
    if not valid or any(character.isspace() for character in cleaned):
        raise ValueError("Enter a valid llama.cpp HTTP or HTTPS server URL.")
    path = parts.path.rstrip("/")
    for suffix in ("/ui/index.html", "/ui", "/v1"):
        if path.lower().endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class LlamaCppProvider(LocalProvider):
    """llama.cpp provider for llama-server and llama-swap endpoints.

    Both expose the OpenAI-compatible API, so the provider reuses the local
    OpenAI code path. llama-swap additionally lists every configured model on
    `/v1/models` and loads them on demand, which is what model discovery uses.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        max_tokens: int,
        api_format: LocalApiFormat = "openai",
        api_key: str = "llama-cpp",
        supports_tools: bool = True,
        tool_mode: LocalToolMode = "auto",
        think: OllamaThink = False,
        client: Any | None = None,
        openai_provider: LLMProvider | None = None,
    ) -> None:
        try:
            base_url = normalize_llamacpp_base_url(base_url)
        except ValueError as exc:
            raise ProviderConfigurationError(str(exc)) from exc
        super().__init__(
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            api_format=api_format,
            api_key=api_key,
            supports_tools=supports_tools,
            tool_mode=tool_mode,
            think=think,
            client=client,
            openai_provider=openai_provider,
        )


@dataclass(frozen=True)
class LlamaCppModel:
    model: str
    label: str


def _model_label(model_id: str) -> str:
    # llama-swap ids are config keys such as "qwen3-30b" or file-ish names;
    # keep them recognizable and only trim noisy path/extension fragments.
    label = model_id.rsplit("/", 1)[-1]
    if label.lower().endswith(".gguf"):
        label = label[: -len(".gguf")]
    return label or model_id


async def discover_llamacpp_models(
    base_url: str,
    api_key: str | None = None,
    client: Any | None = None,
    timeout: float = 5.0,
) -> tuple[LlamaCppModel, ...]:
    """List models a llama-swap (or llama-server) endpoint can serve.

    Queries the OpenAI-compatible `/v1/models` route; llama-swap returns every
    model in its config, a bare llama-server returns the loaded one.
    """
    try:
        url = f"{normalize_llamacpp_base_url(base_url)}/v1/models"
    except ValueError as exc:
        raise LlamaCppDiscoveryError(str(exc)) from exc
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as owned_client:
                response = await owned_client.get(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        msg = f"Timed out connecting to llama.cpp at {url}. Check that the server is reachable."
        raise LlamaCppDiscoveryError(msg) from exc
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or type(exc).__name__
        msg = f"Could not list llama.cpp models from {url}: {detail}"
        raise LlamaCppDiscoveryError(msg) from exc
    except ValueError as exc:
        msg = f"llama.cpp endpoint {url} returned invalid JSON."
        raise LlamaCppDiscoveryError(msg) from exc

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise LlamaCppDiscoveryError(f"llama.cpp endpoint {url} returned an invalid model list.")
    models: list[LlamaCppModel] = []
    seen: set[str] = set()
    for entry in entries:
        model_id = str(entry.get("id", "")).strip() if isinstance(entry, dict) else ""
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(LlamaCppModel(model=model_id, label=_model_label(model_id)))
    models.sort(key=lambda item: item.label.lower())
    return tuple(models)
