# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Narrow host services for one approved plugin invocation and its own session."""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import io
import json
import math
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.core.cordis_config import bounded_json
from libre_claw.core.cordis_llm import CordisLlmBridge
from libre_claw.core.questions import validate_answers, validate_questions
from libre_claw.core.session import Session
from libre_claw.core.session import UserAttachment
from libre_claw.core.tools import ToolContext, current_tool_call


def active_orchestration_controller(plugin_id: str, context: ToolContext | None) -> Any:
    """Resolve only the controller explicitly attached to this exact task."""
    if plugin_id != "orchestration" or context is None:
        raise PermissionError("Orchestration requires an explicitly selected task profile.")
    session = context.shared_state.get("agent_session")
    controller = context.shared_state.get("orchestration_controller")
    if (not isinstance(session, Session) or controller is None
            or getattr(controller, "plugin_id", None) != plugin_id
            or getattr(controller, "session", None) is not session
            or not callable(getattr(controller, "authorize", None))
            or controller.authorize() is not True):
        raise PermissionError("Orchestration is not authorized for this task.")
    return controller


def tool_attachments(result: dict[str, Any]) -> tuple[UserAttachment, ...]:
    """Accept inline verified images; opaque file references confer no read access."""
    blocks = result.get("content_blocks", [])
    bounded_json(blocks, limit=512 * 1024, label="Plugin content blocks")
    if not isinstance(blocks, list) or len(blocks) > 64:
        raise ValueError("Invalid plugin content blocks.")
    attachments = []
    from PIL import Image
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("Invalid plugin content block.")
        if block.get("type") in {"text", "reasoning"}:
            if set(block) != {"type", "text"} or not isinstance(block["text"], str):
                raise ValueError("Invalid plugin text block.")
            continue
        if block.get("type") != "image":
            raise ValueError("Unsupported plugin content block.")
        if "attachment" in block:
            raise PermissionError("Plugin attachment references require an authorized attachment store; return inline image data.")
        if "source" in block:
            source = block["source"]
            if (set(block) != {"type", "source"} or not isinstance(source, dict)
                    or set(source) != {"type", "media_type", "data"} or source["type"] != "base64"):
                raise ValueError("Plugin images require inline base64 data.")
            media, data = source["media_type"], source["data"]
        else:
            if set(block) not in ({"type", "data", "mediaType"}, {"type", "data", "mimeType"}):
                raise ValueError("Plugin images require inline base64 data.")
            media, data = block.get("mediaType", block.get("mimeType")), block["data"]
        formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}
        if not isinstance(media, str) or media not in formats or not isinstance(data, str):
            raise ValueError("Unsupported plugin image format.")
        try:
            raw = base64.b64decode(data, validate=True)
            if not raw or base64.b64encode(raw).decode() != data:
                raise ValueError("Noncanonical image data")
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != formats[media] or image.width * image.height > 40_000_000:
                    raise ValueError("Image format or dimensions do not match")
                image.verify()
        except (ValueError, binascii.Error, OSError, Image.DecompressionBombError):
            raise ValueError("Plugin image data is invalid or exceeds the pixel limit.") from None
        attachments.append(UserAttachment(media_type=media, data=data))
    return tuple(attachments)


class CordisHost:
    """Authorize every host operation without giving the plugin the host object."""

    def __init__(self, plugin_id: str, authorize: Callable[[], dict[str, Any]], *,
                 config: LibreClawConfig | None = None, context: ToolContext | None = None, engine: Any = None,
                 orchestration_authorize: Callable[[], bool] | None = None) -> None:
        self.plugin_id = plugin_id
        self.authorize = authorize
        self.context = context
        self.config = config
        self.engine = engine
        self.orchestration_authorize = orchestration_authorize
        self.timeout: asyncio.Timeout | None = None
        self._paused_calls = 0
        self._paused_timeout: asyncio.Timeout | None = None
        self._paused_remaining: float | None = None
        self.llm = CordisLlmBridge(config, authorize=lambda: self.authorize().get("allow_model") is True) if config else None
        self.session: Session | None = None
        self.harness_services = None
        self.harness_effects_allowed = True
        self.execution: dict[str, Any] = {}
        if context is not None and isinstance(context.shared_state.get("agent_session"), Session):
            self.session = context.shared_state["agent_session"]
            # A replaced/resumed Session gets its own identity; do not derive an
            # identity from a file path or expose an unrelated daemon run ID.
            identity = context.shared_state.get("cordis_session_identity")
            if not isinstance(identity, tuple) or identity[0] is not self.session:
                identity = (self.session, secrets.token_hex(16))
                context.shared_state["cordis_session_identity"] = identity
            call = current_tool_call()
            self.execution = {"agent_id": identity[1], "session_id": identity[1],
                              "call_id": call.id if call else secrets.token_hex(16),
                              "cwd": str(context.working_directory)}
            saved = self.session.checkpoint.get("cordis_events", {}).get(plugin_id, [])
            bounded_json(saved, limit=256 * 1024, label="Plugin session events")
            self.execution["session_events"] = json.loads(json.dumps(saved))
            from libre_claw.core.cordis_harness_services import HarnessHostServices
            services = context.shared_state.setdefault("harness_host_services", {})
            existing = services.get(plugin_id)
            if existing is None or existing.closed or existing.session is not self.session:
                existing = services[plugin_id] = HarnessHostServices(plugin_id, context, authorize, config=config)
            self.harness_services = existing
            existing.actor_token = self.execution["session_id"]

    async def initialize(self) -> dict[str, Any]:
        grants = self.authorize()
        result: dict[str, Any] = {"host_services": ["userQuestions", "sessionProjections", "systemPrompt", "jobs", "agents", "lsp", "ptcRuntime"],
                                 "host_data": {"harness": {"writable": bool(grants.get("write_paths")),
                                     "commandTimeoutMs": (self.context.command_timeout if self.context else 120) * 1000}}}
        if grants.get("read_paths") or grants.get("write_paths"):
            result["host_services"].extend(["fs", "shell"])
        if self.config is not None:
            result["host_data"]["lspProviders"] = [{"id": identifier, "extensionToLanguage": server["extensions"]}
                for identifier, server in self.config.cordis.lsp_servers.items()]
        if grants.get("allow_model") is True and self.llm is not None:
            result["host_services"].append("llm")
            result["host_data"].update(providers=await self._models(self.llm.list_providers),
                                       configurableProviders=await self._models(self.llm.list_configurable_providers))
        return result

    async def _models(self, handler: Callable[[], Any]) -> Any:
        engine = self._engine()
        if engine is not None:
            return await engine.call("providers", "models", handler=handler)
        return await handler()

    def _engine(self) -> Any:
        engine = self.engine() if callable(self.engine) else self.engine
        if self.engine is not None and engine is None:
            raise PermissionError("The core provider engine is unavailable.")
        return engine

    @asynccontextmanager
    async def _answer_time(self) -> AsyncIterator[None]:
        timeout = self.timeout
        if self._paused_calls == 0:
            self._paused_timeout = timeout
            self._paused_remaining = None
            if timeout is not None and timeout.when() is not None:
                self._paused_remaining = max(0, timeout.when() - asyncio.get_running_loop().time())
                timeout.reschedule(None)
        self._paused_calls += 1
        try:
            yield
        finally:
            self._paused_calls -= 1
            timeout, remaining = self._paused_timeout, self._paused_remaining
            if self._paused_calls == 0 and timeout is not None and remaining is not None and not timeout.expired():
                try:
                    timeout.reschedule(asyncio.get_running_loop().time() + remaining)
                except RuntimeError:
                    # Transport teardown can finish the request while joining
                    # its cancelled host operations; that timer is already closed.
                    pass

    async def dispatch(self, method: str, params: Any) -> Any:
        self.authorize()
        if not isinstance(params, dict):
            raise ValueError("Plugin host request parameters must be an object.")
        bounded_json(params, limit=1024 * 1024, label="Plugin host request")
        if method.startswith("harness."):
            if self.harness_services is None or not self.harness_effects_allowed:
                raise PermissionError("Harness host operations require an active task.")
            async with self._answer_time():
                return await self.harness_services.dispatch(method.removeprefix("harness."), params)
        if method.startswith("orchestration."):
            return await self._orchestration(method.removeprefix("orchestration."), params)
        if method == "llm.listModels" and set(params) == {"provider"}:
            if self.llm is None:
                raise PermissionError("Plugin model access is unavailable.")
            return await self._models(lambda: self.llm.list_models(params["provider"]))
        if method == "llm.resolveModelInfo" and set(params) == {"provider", "model"}:
            if self.llm is None:
                raise PermissionError("Plugin model access is unavailable.")
            return await self._models(lambda: self.llm.resolve_model_info(params["provider"], params["model"]))
        if method == "userQuestions.ask" and set(params) == {"questions"}:
            if self.context is None or self.session is None:
                raise PermissionError("User questions require an active task.")
            handler = self.context.shared_state.get("user_question_handler")
            if not callable(handler):
                raise PermissionError("This task cannot receive user answers.")
            questions = validate_questions(params["questions"])
            async with self._answer_time():
                answer = await handler(questions)
            self.authorize()
            return validate_answers(questions, answer)
        if method == "session.append" and set(params) == {"session_id", "type", "data"}:
            return await self._append(params)
        raise PermissionError("The requested plugin host operation is unavailable.")

    async def _orchestration(self, method: str, params: dict[str, Any]) -> Any:
        if (self.authorize().get("allow_model") is not True or self.orchestration_authorize is None
                or self.orchestration_authorize() is not True):
            raise PermissionError("Orchestration requires the included plugin and explicit model access.")
        controller = active_orchestration_controller(self.plugin_id, self.context)
        if controller.session is not self.session:
            raise PermissionError("The calling orchestration task changed.")
        if method == "dispatch" and set(params) == {"tasks"}:
            if self.session is not None and self.session.mode == "plan" and not controller.tasks_read_only(params["tasks"]):
                raise PermissionError("Plan mode permits only read-only worker assignments.")
            result = controller.dispatch(params["tasks"])
        elif method in {"wait", "cancel"} and not set(params) - ({"ids", "timeout"} if method == "wait" else {"ids"}):
            ids = params.get("ids")
            if ids is not None and (not isinstance(ids, list) or len(ids) > 32
                                    or any(not isinstance(value, str) or not 1 <= len(value) <= 160 for value in ids)):
                raise ValueError("Orchestration worker IDs must be a bounded list of strings.")
            if method == "wait":
                timeout = params.get("timeout", 0)
                if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 <= timeout <= 60:
                    raise ValueError("Orchestration wait timeout must be between 0 and 60 seconds.")
                # The requested wait is explicit and bounded. Retain the normal
                # tool execution budget before and after this waiting period.
                async with self._answer_time(), asyncio.timeout(timeout + 1):
                    result = await controller.wait(ids, timeout)
            else:
                result = controller.cancel(ids)
        elif method == "status" and not params:
            result = controller.status()
        else:
            raise PermissionError("The requested orchestration operation is unavailable.")
        if inspect.isawaitable(result):
            result = await result
        self.authorize()
        if self.orchestration_authorize() is not True:
            raise PermissionError("Orchestration access was revoked.")
        if (active_orchestration_controller(self.plugin_id, self.context) is not controller
                or controller.session is not self.session):
            raise PermissionError("The calling orchestration controller changed.")
        bounded_json(result, limit=256 * 1024, label="Orchestration report")
        return result

    async def stream(self, method: str, params: Any) -> AsyncIterator[dict[str, Any]]:
        self.authorize()
        if method != "llm.stream" or self.llm is None:
            raise PermissionError("The requested plugin host stream is unavailable.")
        engine = self._engine()
        stream = engine.stream("providers", "stream", handler=lambda: self.llm.stream(params)) if engine is not None else self.llm.stream(params)
        try:
            async for chunk in stream:
                self.authorize()
                yield chunk
        finally:
            await stream.aclose()

    async def _append(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.context is None:
            raise PermissionError("Plugin session events require an active task.")
        lock = self.context.shared_state.setdefault("cordis_session_lock", asyncio.Lock())
        async with lock:
            return await self._append_locked(params)

    async def _append_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        self.authorize()
        if self.session is None or params["session_id"] != self.execution.get("session_id"):
            raise PermissionError("Plugins can append only to their calling task session.")
        event_type = params["type"]
        if not isinstance(event_type, str) or not 1 <= len(event_type) <= 160:
            raise ValueError("Plugin session events require a bounded type.")
        bounded_json(params["data"], label="Plugin session event")
        data = json.loads(json.dumps(params["data"]))
        steps = None
        if event_type == "todo/write":
            if not isinstance(data, dict) or set(data) != {"todos"} or not isinstance(data["todos"], list) or len(data["todos"]) > 128:
                raise ValueError("Invalid plugin todo list.")
            steps, seen = [], set()
            statuses = {"pending": "pending", "in_progress": "running", "completed": "done"}
            for todo in data["todos"]:
                if (not isinstance(todo, dict) or set(todo) != {"content", "status"}
                        or not isinstance(todo["content"], str) or not todo["content"].strip()
                        or len(todo["content"]) > 4000 or todo["content"] in seen
                        or not isinstance(todo["status"], str) or todo["status"] not in statuses):
                    raise ValueError("Invalid plugin todo item.")
                seen.add(todo["content"])
                steps.append({"text": todo["content"], "status": statuses[todo["status"]]})
        elif not event_type.startswith(self.plugin_id + "/"):
            raise PermissionError("Plugin event types must use their own plugin ID namespace.")
        # Each plugin sees only its own bounded events; no general session reader
        # or append API is exposed across the process boundary.
        existing = self.session.checkpoint.get("cordis_events", {})
        if not isinstance(existing, dict):
            raise ValueError("Invalid plugin event checkpoint.")
        previous = existing.get(self.plugin_id, [])
        if not isinstance(previous, list):
            raise ValueError("Invalid plugin event checkpoint.")
        sequence = previous[-1].get("seq", 0) + 1 if previous else 1
        event = {"type": event_type, "data": data, "seq": sequence, "time": int(time.time() * 1000)}
        updated = {**existing, self.plugin_id: [*previous, event][-128:]}
        bounded_json(updated, limit=256 * 1024, label="Plugin session checkpoint")
        had_events = "cordis_events" in self.session.checkpoint
        old_steps = self.session.plan_steps
        old_outstanding = self.session.checkpoint.get("outstanding")
        had_outstanding = "outstanding" in self.session.checkpoint
        self.session.checkpoint["cordis_events"] = updated
        if steps is not None:
            self.session.plan_steps = steps
            self.session.checkpoint["outstanding"] = [step["text"] for step in steps if step["status"] != "done"][:24]
        callback = self.context.shared_state.get("checkpoint_callback") if self.context else None
        try:
            if callable(callback):
                pending = callback(self.session)
                if inspect.isawaitable(pending):
                    await pending
        except BaseException:
            if had_events:
                self.session.checkpoint["cordis_events"] = existing
            else:
                self.session.checkpoint.pop("cordis_events", None)
            if steps is not None:
                self.session.plan_steps = old_steps
                if had_outstanding:
                    self.session.checkpoint["outstanding"] = old_outstanding
                else:
                    self.session.checkpoint.pop("outstanding", None)
            raise
        self.authorize()
        return event
