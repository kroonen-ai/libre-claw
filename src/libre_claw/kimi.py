# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


MoonshotService = Literal["kimi_code", "platform"]

KIMI_CODE_BASE_URL = "https://api.kimi.com/coding/v1"
MOONSHOT_PLATFORM_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_CODE_MODEL_IDS = (
    "k3",
    "kimi-for-coding",
    "kimi-for-coding-highspeed",
)

_KIMI_CODE_MODEL_ALIASES = {
    "kimi-k3": "k3",
    "kimi-k2.7-code": "kimi-for-coding",
    "kimi-k2.7-code-highspeed": "kimi-for-coding-highspeed",
}
_MOONSHOT_PLATFORM_BASE_URLS = {
    "https://api.moonshot.ai/v1",
    "https://api.moonshot.cn/v1",
}


def moonshot_service(config: Mapping[str, Any]) -> MoonshotService:
    """Return the configured Moonshot service, defaulting to Kimi Code."""
    value = str(config.get("service", "kimi_code")).strip().lower().replace("-", "_")
    if value in {"platform", "moonshot", "moonshot_platform"}:
        return "platform"
    return "kimi_code"


def canonical_kimi_code_model(model: str) -> str:
    """Translate legacy Libre Claw model names to official Kimi Code IDs."""
    cleaned = model.strip()
    return _KIMI_CODE_MODEL_ALIASES.get(cleaned.lower(), cleaned)


def is_kimi_code_model(model: str) -> bool:
    return canonical_kimi_code_model(model).lower() in KIMI_CODE_MODEL_IDS


def normalize_moonshot_selection(
    provider_config: Mapping[str, Any],
    model: str,
) -> tuple[str, dict[str, Any]]:
    """Normalize one Moonshot/Kimi model selection and its routing config."""
    service = moonshot_service(provider_config)
    updated = dict(provider_config)
    updated["service"] = service
    cleaned_model = model.strip()

    if service == "kimi_code":
        cleaned_model = canonical_kimi_code_model(cleaned_model)
        base_url = str(updated.get("base_url", "")).strip().rstrip("/")
        if not base_url or base_url in _MOONSHOT_PLATFORM_BASE_URLS:
            updated["base_url"] = KIMI_CODE_BASE_URL
        api_key_env = str(updated.get("api_key_env", "")).strip()
        if not api_key_env or api_key_env == "MOONSHOT_API_KEY":
            updated["api_key_env"] = "KIMI_API_KEY"
    else:
        base_url = str(updated.get("base_url", "")).strip().rstrip("/")
        if not base_url or base_url == KIMI_CODE_BASE_URL:
            updated["base_url"] = MOONSHOT_PLATFORM_BASE_URL

    updated["default_model"] = cleaned_model
    return cleaned_model, updated
