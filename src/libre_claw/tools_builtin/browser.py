# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import importlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from libre_claw.core.sandbox import SandboxViolation
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


MAX_BROWSER_TEXT_CHARS = 30000
MAX_BROWSER_EXTRACT_CHARS = 60000
DEFAULT_PROFILE = "default"
COOKIE_CONSENT_SELECTORS = (
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('Accept Cookies')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
    "button:has-text('OK')",
    "button:has-text('Got it')",
    "[id*='accept'][role='button']",
    "[class*='accept'][role='button']",
    "#onetrust-accept-btn-handler",
    ".cc-allow",
    ".cc-accept",
    ".cookie-accept",
)


class BrowserState:
    """A persistent Playwright context for one Libre Claw run/profile."""

    def __init__(self, tool: BaseTool, profile: str) -> None:
        self.profile = _safe_profile(profile)
        self.tool = tool
        self.playwright: Any | None = None
        self.context: Any | None = None
        self.page: Any | None = None

    async def ensure_page(self) -> tuple[Any | None, str | None]:
        try:
            async_api = importlib.import_module("playwright.async_api")
        except ModuleNotFoundError:
            return None, "Playwright is not installed. Install the browser extra before using browser tools."

        try:
            if self.playwright is None:
                self.playwright = await async_api.async_playwright().start()
            if self.context is None:
                profile_path = self.tool.context.browser_profile_dir.expanduser() / self.profile
                profile_path.mkdir(parents=True, exist_ok=True)
                downloads = _resolve_browser_output_dir(self.tool, self.tool.context.browser_downloads_dir)
                downloads.mkdir(parents=True, exist_ok=True)
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(profile_path),
                    accept_downloads=True,
                    downloads_path=str(downloads),
                    headless=self.tool.context.browser_headless,
                )
            if self.page is None:
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            return self.page, None
        except Exception as exc:
            return None, str(exc)


def _browser_state(tool: BaseTool, profile: str) -> BrowserState:
    states = tool.context.shared_state.setdefault("browser_states", {})
    if not isinstance(states, dict):
        states = {}
        tool.context.shared_state["browser_states"] = states
    key = _safe_profile(profile)
    state = states.get(key)
    if not isinstance(state, BrowserState):
        state = BrowserState(tool, key)
        states[key] = state
    return state


@register_tool
class BrowserNavigateTool(BaseTool):
    name = "browser_navigate"
    description = "Open a URL in a persistent headless browser profile. Requires Playwright."
    parameters = {
        "url": {"type": "string", "description": "HTTP or HTTPS URL to open"},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "timeout_ms": {"type": "integer", "description": "Navigation timeout in milliseconds", "default": 30000},
        "dismiss_cookies": {"type": "boolean", "description": "Try to dismiss common cookie consent popups after load", "default": True},
    }
    required = ("url",)
    permission_level = "ask"

    async def execute(
        self,
        url: str,
        profile: str = DEFAULT_PROFILE,
        timeout_ms: int | None = None,
        dismiss_cookies: bool = True,
    ) -> ToolResult:
        try:
            timeout = _timeout(timeout_ms, self.context.browser_default_timeout_ms)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        policy_error = _domain_policy_error(url, self.context.browser_allowed_domains, self.context.browser_denied_domains)
        if policy_error is not None:
            return ToolResult(error=policy_error)

        state = _browser_state(self, profile)
        page, error = await state.ensure_page()
        if error is not None:
            return ToolResult(error=error)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            dismissed = await _dismiss_cookie_banners(page) if dismiss_cookies else []
            title = await page.title()
        except Exception as exc:
            return ToolResult(error=str(exc))

        post_error = _domain_policy_error(page.url, self.context.browser_allowed_domains, self.context.browser_denied_domains)
        if post_error is not None:
            return ToolResult(error=f"Navigation reached a blocked URL: {post_error}")
        status = getattr(response, "status", None)
        return ToolResult(
            content=(
                f"Opened {page.url}\ntitle: {title}\nstatus: {status if status is not None else 'unknown'}"
                + (f"\ndismissed_cookie_selectors: {len(dismissed)}" if dismiss_cookies else "")
            ),
            metadata={
                "artifact_type": "browser_page",
                "profile": state.profile,
                "url": page.url,
                "title": title,
                "status": status,
                "dismissed_cookie_selectors": dismissed,
            },
        )


@register_tool
class BrowserReadTool(BaseTool):
    name = "browser_read"
    description = "Extract visible text from the current browser page or a DOM selector."
    parameters = {
        "selector": {"type": "string", "description": "Optional CSS selector to read instead of body", "default": "body"},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "max_chars": {
            "type": "integer",
            "description": f"Maximum page text characters to return, capped at {MAX_BROWSER_TEXT_CHARS}",
            "default": 12000,
        },
        "timeout_ms": {"type": "integer", "description": "Selector read timeout in milliseconds", "default": 5000},
    }
    permission_level = "allow"

    async def execute(
        self,
        selector: str = "body",
        profile: str = DEFAULT_PROFILE,
        max_chars: int = 12000,
        timeout_ms: int = 5000,
    ) -> ToolResult:
        if max_chars < 1:
            return ToolResult(error="max_chars must be >= 1")
        if max_chars > MAX_BROWSER_TEXT_CHARS:
            return ToolResult(error=f"max_chars must be <= {MAX_BROWSER_TEXT_CHARS}")
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            title = await page.title()
            locator = page.locator(selector or "body").first
            text = await locator.inner_text(timeout=_timeout(timeout_ms, 5000))
        except ValueError as exc:
            return ToolResult(error=str(exc))
        except Exception as exc:
            return ToolResult(error=str(exc))

        content, truncated = _cap_text(text, max_chars)
        return ToolResult(
            content=f"title: {title}\nurl: {page.url}\nselector: {selector or 'body'}\n\n{content}",
            metadata={
                "artifact_type": "browser_read",
                "profile": _safe_profile(profile),
                "url": page.url,
                "title": title,
                "selector": selector or "body",
                "characters": len(text),
                "truncated": truncated,
            },
        )


@register_tool
class BrowserExtractTool(BaseTool):
    name = "browser_extract"
    description = "Extract image URLs, links, metadata, and structured data from the current browser page without relying on visible text."
    parameters = {
        "kind": {"type": "string", "description": "Data to extract: all, images, links, structured, or metadata", "default": "all"},
        "selector": {"type": "string", "description": "Optional CSS selector to scope extraction", "default": "document"},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "max_items": {"type": "integer", "description": "Maximum items per extracted collection", "default": 200},
        "max_chars": {"type": "integer", "description": f"Maximum JSON characters to return, capped at {MAX_BROWSER_EXTRACT_CHARS}", "default": 30000},
    }
    permission_level = "allow"

    async def execute(
        self,
        kind: str = "all",
        selector: str = "document",
        profile: str = DEFAULT_PROFILE,
        max_items: int = 200,
        max_chars: int = 30000,
    ) -> ToolResult:
        if kind not in {"all", "images", "links", "structured", "metadata"}:
            return ToolResult(error="kind must be all, images, links, structured, or metadata")
        if max_items < 1:
            return ToolResult(error="max_items must be >= 1")
        if max_items > 1000:
            return ToolResult(error="max_items must be <= 1000")
        if max_chars < 1:
            return ToolResult(error="max_chars must be >= 1")
        if max_chars > MAX_BROWSER_EXTRACT_CHARS:
            return ToolResult(error=f"max_chars must be <= {MAX_BROWSER_EXTRACT_CHARS}")

        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")

        try:
            data = await page.evaluate(_BROWSER_EXTRACT_SCRIPT, {"kind": kind, "selector": selector or "document", "maxItems": max_items})
            title = await page.title()
        except Exception as exc:
            return ToolResult(error=str(exc))

        text, truncated = _json_text(data, max_chars)
        return ToolResult(
            content=text,
            metadata={
                "artifact_type": "browser_extract",
                "profile": _safe_profile(profile),
                "url": page.url,
                "title": title,
                "kind": kind,
                "selector": selector or "document",
                "truncated": truncated,
            },
        )


@register_tool
class BrowserExecuteTool(BaseTool):
    name = "browser_execute"
    description = "Run JavaScript in the current browser page context and return the serialized result."
    parameters = {
        "script": {"type": "string", "description": "JavaScript expression or function body accepted by Playwright page.evaluate"},
        "arg": {"type": "object", "description": "Optional JSON-serializable argument passed to the script", "default": {}},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "timeout_ms": {"type": "integer", "description": "Execution timeout in milliseconds", "default": 5000},
        "max_chars": {"type": "integer", "description": f"Maximum JSON characters to return, capped at {MAX_BROWSER_EXTRACT_CHARS}", "default": 30000},
    }
    required = ("script",)
    permission_level = "ask"

    async def execute(
        self,
        script: str,
        arg: dict[str, Any] | None = None,
        profile: str = DEFAULT_PROFILE,
        timeout_ms: int = 5000,
        max_chars: int = 30000,
    ) -> ToolResult:
        if not script.strip():
            return ToolResult(error="script must not be empty")
        if max_chars < 1:
            return ToolResult(error="max_chars must be >= 1")
        if max_chars > MAX_BROWSER_EXTRACT_CHARS:
            return ToolResult(error=f"max_chars must be <= {MAX_BROWSER_EXTRACT_CHARS}")
        try:
            timeout = _timeout(timeout_ms, 5000)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            result = await asyncio.wait_for(page.evaluate(script, arg or {}), timeout=timeout / 1000)
        except TimeoutError:
            return ToolResult(error=f"JavaScript execution timed out after {timeout} ms")
        except Exception as exc:
            return ToolResult(error=str(exc))

        text, truncated = _json_text(result, max_chars)
        return ToolResult(
            content=text,
            metadata={
                "artifact_type": "browser_execute",
                "profile": _safe_profile(profile),
                "url": page.url,
                "truncated": truncated,
            },
        )


@register_tool
class BrowserDismissCookiesTool(BaseTool):
    name = "browser_dismiss_cookies"
    description = "Try to dismiss common cookie consent popups on the current browser page."
    parameters = {
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
    }
    permission_level = "allow"

    async def execute(self, profile: str = DEFAULT_PROFILE) -> ToolResult:
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        dismissed = await _dismiss_cookie_banners(page)
        return ToolResult(
            content=f"Dismissed {len(dismissed)} cookie consent element(s).",
            metadata={"artifact_type": "browser_action", "profile": _safe_profile(profile), "url": page.url, "action": "dismiss_cookies", "selectors": dismissed},
        )


@register_tool
class BrowserClickTool(BaseTool):
    name = "browser_click"
    description = "Click a DOM element in the current browser page using a CSS selector."
    parameters = {
        "selector": {"type": "string", "description": "CSS selector to click"},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "timeout_ms": {"type": "integer", "description": "Click timeout in milliseconds", "default": 30000},
    }
    required = ("selector",)
    permission_level = "ask"

    async def execute(self, selector: str, profile: str = DEFAULT_PROFILE, timeout_ms: int | None = None) -> ToolResult:
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            await page.locator(selector).first.click(timeout=_timeout(timeout_ms, self.context.browser_default_timeout_ms))
            title = await page.title()
        except ValueError as exc:
            return ToolResult(error=str(exc))
        except Exception as exc:
            return ToolResult(error=str(exc))
        post_error = _domain_policy_error(page.url, self.context.browser_allowed_domains, self.context.browser_denied_domains)
        if post_error is not None:
            return ToolResult(error=f"Click reached a blocked URL: {post_error}")
        return ToolResult(
            content=f"Clicked {selector}\nurl: {page.url}\ntitle: {title}",
            metadata={"artifact_type": "browser_action", "profile": _safe_profile(profile), "url": page.url, "selector": selector, "action": "click"},
        )


@register_tool
class BrowserTypeTool(BaseTool):
    name = "browser_type"
    description = "Type text into a DOM element in the current browser page."
    parameters = {
        "selector": {"type": "string", "description": "CSS selector to type into"},
        "text": {"type": "string", "description": "Text to enter"},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "clear": {"type": "boolean", "description": "Clear the element before typing", "default": True},
        "press_enter": {"type": "boolean", "description": "Press Enter after typing", "default": False},
        "timeout_ms": {"type": "integer", "description": "Typing timeout in milliseconds", "default": 30000},
    }
    required = ("selector", "text")
    permission_level = "ask"

    async def execute(
        self,
        selector: str,
        text: str,
        profile: str = DEFAULT_PROFILE,
        clear: bool = True,
        press_enter: bool = False,
        timeout_ms: int | None = None,
    ) -> ToolResult:
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            locator = page.locator(selector).first
            timeout = _timeout(timeout_ms, self.context.browser_default_timeout_ms)
            if clear:
                await locator.fill("", timeout=timeout)
                await locator.fill(text, timeout=timeout)
            else:
                await locator.type(text, timeout=timeout)
            if press_enter:
                await locator.press("Enter", timeout=timeout)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        except Exception as exc:
            return ToolResult(error=str(exc))
        post_error = _domain_policy_error(page.url, self.context.browser_allowed_domains, self.context.browser_denied_domains)
        if post_error is not None:
            return ToolResult(error=f"Typing reached a blocked URL: {post_error}")
        return ToolResult(
            content=f"Typed {len(text)} character(s) into {selector}\nurl: {page.url}",
            metadata={"artifact_type": "browser_action", "profile": _safe_profile(profile), "url": page.url, "selector": selector, "action": "type"},
        )


@register_tool
class BrowserWaitTool(BaseTool):
    name = "browser_wait"
    description = "Wait for a DOM selector state or a browser load state."
    parameters = {
        "selector": {"type": "string", "description": "CSS selector to wait for; omit for page load state", "default": ""},
        "state": {
            "type": "string",
            "description": "Selector state visible|attached|detached|hidden, or load state load|domcontentloaded|networkidle",
            "default": "visible",
        },
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "timeout_ms": {"type": "integer", "description": "Wait timeout in milliseconds", "default": 30000},
    }
    permission_level = "allow"

    async def execute(
        self,
        selector: str = "",
        state: str = "visible",
        profile: str = DEFAULT_PROFILE,
        timeout_ms: int | None = None,
    ) -> ToolResult:
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            timeout = _timeout(timeout_ms, self.context.browser_default_timeout_ms)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        try:
            if selector:
                if state not in {"visible", "attached", "detached", "hidden"}:
                    return ToolResult(error="selector state must be visible, attached, detached, or hidden")
                await page.locator(selector).first.wait_for(state=state, timeout=timeout)
                target = f"{selector} ({state})"
            else:
                if state not in {"load", "domcontentloaded", "networkidle"}:
                    return ToolResult(error="page load state must be load, domcontentloaded, or networkidle")
                await page.wait_for_load_state(state, timeout=timeout)
                target = f"page {state}"
        except Exception as exc:
            return ToolResult(error=str(exc))
        return ToolResult(
            content=f"Waited for {target}\nurl: {page.url}",
            metadata={"artifact_type": "browser_action", "profile": _safe_profile(profile), "url": page.url, "selector": selector, "state": state, "action": "wait"},
        )


@register_tool
class BrowserDownloadTool(BaseTool):
    name = "browser_download"
    description = "Click a DOM selector that starts a download and save it inside the working directory."
    parameters = {
        "selector": {"type": "string", "description": "CSS selector that triggers a download"},
        "path": {"type": "string", "description": "Output file or directory path", "default": ""},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "timeout_ms": {"type": "integer", "description": "Download timeout in milliseconds", "default": 30000},
    }
    required = ("selector",)
    permission_level = "ask"

    async def execute(
        self,
        selector: str,
        path: str = "",
        profile: str = DEFAULT_PROFILE,
        timeout_ms: int | None = None,
    ) -> ToolResult:
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            timeout = _timeout(timeout_ms, self.context.browser_default_timeout_ms)
        except ValueError as exc:
            return ToolResult(error=str(exc))
        try:
            async with page.expect_download(timeout=timeout) as download_info:
                await page.locator(selector).first.click(timeout=timeout)
            download = await download_info.value
            suggested = str(getattr(download, "suggested_filename", "") or "download.bin")
            resolved = _resolve_browser_file(self, path, self.context.browser_downloads_dir, suggested)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            await download.save_as(str(resolved))
        except SandboxViolation as exc:
            return ToolResult(error=str(exc))
        except Exception as exc:
            return ToolResult(error=str(exc))
        size_bytes = resolved.stat().st_size
        return ToolResult(
            content=f"Saved download to {resolved} ({size_bytes} bytes)",
            metadata={
                "artifact_type": "browser_download",
                "profile": _safe_profile(profile),
                "url": page.url,
                "selector": selector,
                "path": str(resolved),
                "suggested_filename": suggested,
                "size_bytes": size_bytes,
            },
        )


@register_tool
class BrowserScreenshotTool(BaseTool):
    name = "browser_screenshot"
    description = "Save a screenshot of the current browser page or a DOM selector inside the working directory."
    parameters = {
        "path": {"type": "string", "description": "Screenshot output path", "default": ""},
        "selector": {"type": "string", "description": "Optional CSS selector to capture", "default": ""},
        "profile": {"type": "string", "description": "Persistent browser profile name", "default": DEFAULT_PROFILE},
        "full_page": {"type": "boolean", "description": "Capture the full page instead of viewport", "default": True},
    }
    permission_level = "allow"

    async def execute(
        self,
        path: str = "",
        selector: str = "",
        profile: str = DEFAULT_PROFILE,
        full_page: bool = True,
    ) -> ToolResult:
        page = _current_page(self, profile)
        if page is None:
            return ToolResult(error="No browser page is open. Use browser_navigate first.")
        try:
            default_name = f"screenshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
            resolved = _resolve_browser_file(self, path, self.context.browser_screenshots_dir, default_name)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            if selector:
                await page.locator(selector).first.screenshot(path=str(resolved))
            else:
                await page.screenshot(path=str(resolved), full_page=full_page)
        except SandboxViolation as exc:
            return ToolResult(error=str(exc))
        except Exception as exc:
            return ToolResult(error=str(exc))

        size_bytes = resolved.stat().st_size
        return ToolResult(
            content=f"Saved screenshot to {resolved} ({size_bytes} bytes)",
            metadata={
                "artifact_type": "browser_screenshot",
                "profile": _safe_profile(profile),
                "path": str(resolved),
                "url": page.url,
                "selector": selector,
                "full_page": full_page if not selector else False,
                "size_bytes": size_bytes,
            },
        )


def _current_page(tool: BaseTool, profile: str) -> Any | None:
    states = tool.context.shared_state.get("browser_states")
    if not isinstance(states, dict):
        return None
    state = states.get(_safe_profile(profile))
    if not isinstance(state, BrowserState):
        return None
    return state.page


async def _dismiss_cookie_banners(page: Any) -> list[str]:
    dismissed: list[str] = []
    for selector in COOKIE_CONSENT_SELECTORS:
        try:
            await page.locator(selector).first.click(timeout=1000)
            dismissed.append(selector)
            break
        except Exception:
            continue
    return dismissed


def _domain_policy_error(url: str, allowed_domains: tuple[str, ...], denied_domains: tuple[str, ...]) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "url must be an absolute http(s) URL"
    host = (parsed.hostname or "").lower().rstrip(".")
    if any(_domain_matches(host, pattern) for pattern in denied_domains):
        return f"Browser access to {host} is denied by [browser].denied_domains"
    if allowed_domains and not any(_domain_matches(host, pattern) for pattern in allowed_domains):
        return f"Browser access to {host} is not in [browser].allowed_domains"
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


def _resolve_browser_output_dir(tool: BaseTool, path: Path) -> Path:
    return tool.resolve_path(str(path))


def _resolve_browser_file(tool: BaseTool, path: str, default_dir: Path, default_name: str) -> Path:
    requested = path.strip()
    if requested:
        candidate = Path(requested).expanduser()
        if requested.endswith("/") or candidate.suffix == "":
            candidate = candidate / default_name
    else:
        candidate = default_dir / default_name
    return tool.resolve_path(str(candidate))


def _safe_profile(profile: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", (profile or DEFAULT_PROFILE).strip()).strip(".-")
    return cleaned[:80] or DEFAULT_PROFILE


def _timeout(value: int | None, default: int) -> int:
    timeout = default if value is None else int(value)
    if timeout < 1000:
        raise ValueError("timeout_ms must be >= 1000")
    return timeout


def _cap_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + f"\n... truncated {len(text) - max_chars} characters ...", True


def _json_text(value: Any, max_chars: int) -> tuple[str, bool]:
    try:
        text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        text = json.dumps(str(value), ensure_ascii=False)
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + f"\n... truncated {len(text) - max_chars} characters ...", True


_BROWSER_EXTRACT_SCRIPT = """
({ kind, selector, maxItems }) => {
  const root = selector && selector !== "document" ? document.querySelector(selector) : document;
  if (!root) {
    throw new Error(`selector not found: ${selector}`);
  }
  const limit = (items) => Array.from(items).slice(0, maxItems);
  const absolute = (value) => {
    try {
      return value ? new URL(value, document.baseURI).href : "";
    } catch {
      return value || "";
    }
  };
  const result = {
    url: location.href,
    title: document.title,
  };
  if (kind === "all" || kind === "images") {
    result.images = limit(root.querySelectorAll("img, source[srcset], picture source")).map((node) => ({
      tag: node.tagName.toLowerCase(),
      src: absolute(node.currentSrc || node.src || node.getAttribute("src") || ""),
      srcset: node.getAttribute("srcset") || "",
      alt: node.getAttribute("alt") || "",
      width: Number(node.naturalWidth || node.width || 0),
      height: Number(node.naturalHeight || node.height || 0),
      loading: node.getAttribute("loading") || "",
    }));
  }
  if (kind === "all" || kind === "links") {
    result.links = limit(root.querySelectorAll("a[href]")).map((node) => ({
      href: absolute(node.getAttribute("href") || ""),
      text: (node.innerText || node.textContent || "").trim().slice(0, 500),
      title: node.getAttribute("title") || "",
      target: node.getAttribute("target") || "",
      rel: node.getAttribute("rel") || "",
    }));
  }
  if (kind === "all" || kind === "structured") {
    result.structured = limit(root.querySelectorAll("script[type='application/ld+json']")).map((node) => {
      const raw = node.textContent || "";
      try {
        return JSON.parse(raw);
      } catch {
        return { raw };
      }
    });
  }
  if (kind === "all" || kind === "metadata") {
    result.metadata = {
      canonical: document.querySelector("link[rel='canonical']")?.href || "",
      description: document.querySelector("meta[name='description']")?.content || "",
      metas: limit(document.querySelectorAll("meta[name], meta[property]")).map((node) => ({
        name: node.getAttribute("name") || "",
        property: node.getAttribute("property") || "",
        content: node.getAttribute("content") || "",
      })),
    };
  }
  return result;
}
"""
