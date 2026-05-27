# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from libre_claw.providers.openai import OpenAIProvider

OPENROUTER_HTTP_REFERER = "https://libreclaw.dev"
OPENROUTER_APP_TITLE = "Libre Claw"
OPENROUTER_CATEGORIES = "cli-agent,personal-agent"
OPENROUTER_RANKING_TARGETS = (
    "Productivity",
    "Coding Agents",
    "Personal Agents",
    "CLI Agents",
)


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider using its OpenAI-compatible chat completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str = "https://openrouter.ai/api/v1",
        client: object | None = None,
    ) -> None:
        headers = {
            "HTTP-Referer": OPENROUTER_HTTP_REFERER,
            "X-OpenRouter-Title": OPENROUTER_APP_TITLE,
            "X-OpenRouter-Categories": OPENROUTER_CATEGORIES,
        }

        super().__init__(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            base_url=base_url,
            default_headers=headers,
            display_name="OpenRouter",
            client=client,
        )

    def _extra_body(self) -> dict[str, Any]:
        return {"usage": {"include": True}}
