# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from libre_claw.providers.local import LocalProvider

DEFAULT_LLAMACPP_BASE_URL = "http://localhost:8080"


class LlamaCppDiscoveryError(RuntimeError):
    """Raised when a llama.cpp / llama-swap endpoint cannot list its models."""


def normalize_llamacpp_base_url(base_url: str) -> str:
    """Normalize a llama.cpp endpoint URL.

    Users paste both host roots and OpenAI paths ("http://stargate.local:8080/v1");
    the provider always appends its own /v1 path, so a trailing /v1 is stripped.
    """
    cleaned = base_url.strip().rstrip("/")
    if cleaned.lower().endswith("/v1"):
        cleaned = cleaned[: -len("/v1")].rstrip("/")
    return cleaned


class LlamaCppProvider(LocalProvider):
    """llama.cpp provider for llama-server and llama-swap endpoints.

    Both expose the OpenAI-compatible API, so the provider reuses the local
    OpenAI code path. llama-swap additionally lists every configured model on
    `/v1/models` and loads them on demand, which is what model discovery uses.
    """


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
    url = f"{normalize_llamacpp_base_url(base_url)}/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as owned_client:
                response = await owned_client.get(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        msg = f"Could not list llama.cpp models from {url}: {exc}"
        raise LlamaCppDiscoveryError(msg) from exc
    except ValueError as exc:
        msg = f"llama.cpp endpoint {url} returned invalid JSON."
        raise LlamaCppDiscoveryError(msg) from exc

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return ()
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
