# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from libre_claw.config import PetdexConfig


PETDEX_KNOWN_STATES = frozenset(
    {
        "idle",
        "ready",
        "thinking",
        "running",
        "working",
        "command",
        "success",
        "failed",
        "error",
        "waving",
    }
)


@dataclass(frozen=True)
class PetdexUpdateResult:
    ok: bool
    skipped: bool = False
    message: str = ""


class PetdexClient:
    """Small authenticated client for the optional Petdex local companion."""

    def __init__(self, config: PetdexConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._http_client = http_client

    @property
    def configured(self) -> bool:
        return self.config.enabled

    @property
    def token_available(self) -> bool:
        return self.config.token_path.exists()

    def status_text(self) -> str:
        enabled = "enabled" if self.config.enabled else "disabled"
        token = "found" if self.token_available else "missing"
        return "\n".join(
            [
                "Petdex integration:",
                f"enabled: {enabled}",
                f"endpoint: {self.config.base_url}/state",
                f"token: {token} at {self.config.token_path}",
                f"source: {self.config.source}",
            ]
        )

    async def send_state(
        self,
        state: str,
        *,
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> PetdexUpdateResult:
        clean_state = state.strip().lower()
        if not self.config.enabled:
            return PetdexUpdateResult(ok=False, skipped=True, message="Petdex integration is disabled.")
        if not clean_state:
            return PetdexUpdateResult(ok=False, skipped=True, message="Petdex state is empty.")

        try:
            token = self.config.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return PetdexUpdateResult(ok=False, skipped=True, message=f"Petdex token not found at {self.config.token_path}.")
        if not token:
            return PetdexUpdateResult(ok=False, skipped=True, message=f"Petdex token is empty at {self.config.token_path}.")

        payload: dict[str, Any] = {
            "state": clean_state,
            "source": self.config.source or "libre-claw",
        }
        if message:
            payload["message"] = _truncate_text(message, 300)
        if details:
            payload["details"] = _compact_details(details)

        try:
            if self._http_client is not None:
                response = await self._http_client.post(
                    f"{self.config.base_url}/state",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
            else:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    response = await client.post(
                        f"{self.config.base_url}/state",
                        headers={"Authorization": f"Bearer {token}"},
                        json=payload,
                    )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return PetdexUpdateResult(ok=False, message=f"Petdex update failed: {exc}")
        return PetdexUpdateResult(ok=True)


def petdex_message_preview(text: str, *, limit: int = 120) -> str:
    return _truncate_text(" ".join(text.split()), limit)


def petdex_tool_details(tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {"tool": tool_name}
    if tool_name == "bash":
        command = str(arguments.get("command", "")).strip()
        if command:
            details["command"] = _truncate_text(command, 200)
    elif tool_name in {"read_file", "write_file", "edit_file", "search_files", "glob", "list_directory"}:
        path = str(arguments.get("path", "")).strip()
        if path:
            details["path"] = _truncate_text(path, 240)
    elif tool_name.startswith("browser_"):
        url = str(arguments.get("url", "")).strip()
        if url:
            details["url"] = _truncate_text(url, 240)
    elif tool_name in {"http_request", "web_search"}:
        value = str(arguments.get("url") or arguments.get("query") or "").strip()
        if value:
            details["target"] = _truncate_text(value, 240)
    return details


def _compact_details(details: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in details.items():
        if value is None:
            continue
        if isinstance(value, str):
            compact[str(key)] = _truncate_text(value, 300)
        elif isinstance(value, int | float | bool):
            compact[str(key)] = value
        else:
            compact[str(key)] = _truncate_text(str(value), 300)
    return compact


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
