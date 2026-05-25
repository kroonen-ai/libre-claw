# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


MAX_HTTP_RESPONSE_CHARS = 200000
MAX_HTTP_DOWNLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}


@register_tool
class HTTPRequestTool(BaseTool):
    name = "http_request"
    description = "Make an HTTP request for APIs, downloads, and URL fetches without using the shell."
    parameters = {
        "url": {"type": "string", "description": "Absolute HTTP or HTTPS URL"},
        "method": {"type": "string", "description": "HTTP method: GET, HEAD, POST, PUT, PATCH, or DELETE", "default": "GET"},
        "headers": {"type": "object", "description": "Optional request headers", "default": {}},
        "params": {"type": "object", "description": "Optional query parameters", "default": {}},
        "body": {"type": "string", "description": "Optional raw request body", "default": ""},
        "json_body": {"type": "object", "description": "Optional JSON request body", "default": {}},
        "output_path": {"type": "string", "description": "Optional path to save the response body inside the working directory", "default": ""},
        "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 30},
        "max_response_chars": {"type": "integer", "description": f"Maximum text characters to return, capped at {MAX_HTTP_RESPONSE_CHARS}", "default": 30000},
        "follow_redirects": {"type": "boolean", "description": "Follow HTTP redirects", "default": True},
    }
    required = ("url",)
    permission_level = "ask"

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        body: str = "",
        json_body: Mapping[str, Any] | None = None,
        output_path: str = "",
        timeout: int = 30,
        max_response_chars: int = 30000,
        follow_redirects: bool = True,
    ) -> ToolResult:
        method = method.upper().strip()
        if method not in ALLOWED_HTTP_METHODS:
            return ToolResult(error=f"method must be one of {', '.join(sorted(ALLOWED_HTTP_METHODS))}")
        if timeout < 1:
            return ToolResult(error="timeout must be >= 1")
        if timeout > 300:
            return ToolResult(error="timeout must be <= 300")
        if max_response_chars < 1:
            return ToolResult(error="max_response_chars must be >= 1")
        if max_response_chars > MAX_HTTP_RESPONSE_CHARS:
            return ToolResult(error=f"max_response_chars must be <= {MAX_HTTP_RESPONSE_CHARS}")
        if headers is not None and not isinstance(headers, Mapping):
            return ToolResult(error="headers must be an object")
        if params is not None and not isinstance(params, Mapping):
            return ToolResult(error="params must be an object")
        if json_body is not None and not isinstance(json_body, Mapping):
            return ToolResult(error="json_body must be an object")
        if body and json_body:
            return ToolResult(error="provide body or json_body, not both")
        policy_error = _domain_policy_error(url, self.context.browser_allowed_domains, self.context.browser_denied_domains)
        if policy_error is not None:
            return ToolResult(error=policy_error)

        resolved_output: Path | None = None
        if output_path.strip():
            try:
                resolved_output = self.resolve_path(output_path)
            except SandboxViolation as exc:
                return ToolResult(error=str(exc))

        request_headers = _string_mapping(headers or {})
        request_params = _string_mapping(params or {})
        request_json = dict(json_body) if json_body else None
        request_content = body.encode("utf-8") if body and request_json is None else None

        try:
            async with httpx.AsyncClient(follow_redirects=follow_redirects, timeout=timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=request_headers,
                    params=request_params,
                    content=request_content,
                    json=request_json,
                )
        except Exception as exc:
            return ToolResult(error=str(exc))

        body_bytes = response.content
        if len(body_bytes) > MAX_HTTP_DOWNLOAD_BYTES and resolved_output is not None:
            return ToolResult(error=f"response body exceeds {MAX_HTTP_DOWNLOAD_BYTES} bytes")

        saved_path = ""
        if resolved_output is not None:
            try:
                resolved_output.parent.mkdir(parents=True, exist_ok=True)
                resolved_output.write_bytes(body_bytes)
                saved_path = str(resolved_output)
            except OSError as exc:
                return ToolResult(error=str(exc))

        text = _response_text(response, max_response_chars)
        truncated = len(_decode_response(response)) > max_response_chars
        reason = getattr(response, "reason_phrase", "")
        status_line = f"{response.status_code} {reason}".strip()
        parts = [
            f"{method} {response.url}",
            f"status: {status_line}",
            f"content_type: {response.headers.get('content-type', '')}",
            f"bytes: {len(body_bytes)}",
        ]
        if saved_path:
            parts.append(f"saved: {saved_path}")
        if method != "HEAD" and not saved_path:
            parts.extend(["", text])

        return ToolResult(
            content="\n".join(parts).rstrip(),
            metadata={
                "artifact_type": "http_request",
                "method": method,
                "requested_url": url,
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "bytes": len(body_bytes),
                "saved_path": saved_path,
                "truncated": truncated,
            },
        )


def _string_mapping(values: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in values.items()}


def _response_text(response: httpx.Response, max_chars: int) -> str:
    text = _decode_response(response)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... truncated {len(text) - max_chars} characters ..."


def _decode_response(response: httpx.Response) -> str:
    try:
        return response.text
    except UnicodeDecodeError:
        return response.content.decode("utf-8", "replace")


def _domain_policy_error(url: str, allowed_domains: tuple[str, ...], denied_domains: tuple[str, ...]) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "url must be an absolute http(s) URL"
    host = (parsed.hostname or "").lower().rstrip(".")
    if any(_domain_matches(host, pattern) for pattern in denied_domains):
        return f"HTTP access to {host} is denied by [browser].denied_domains"
    if allowed_domains and not any(_domain_matches(host, pattern) for pattern in allowed_domains):
        return f"HTTP access to {host} is not in [browser].allowed_domains"
    return None


def _domain_matches(host: str, pattern: str) -> bool:
    normalized = pattern.strip().lower().rstrip(".")
    if not normalized:
        return False
    if normalized == "*":
        return True
    if normalized.startswith("*."):
        suffix = normalized[1:]
        return host.endswith(suffix) or host == normalized[2:]
    return host == normalized or host.endswith("." + normalized)
