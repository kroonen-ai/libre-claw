# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Private stdio embedding API for Libre WebUI (protocol version 1).

The caller owns authentication and model credentials. No daemon, user config,
keyring, provider discovery, or network client is started here. Provider calls
are capabilities granted by the parent for one turn. Work mode never mounts
host tools or writes a session store; its durable state stays in WebUI SQL.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from libre_claw.config import PermissionsConfig
from libre_claw.core.agent import Agent, AgentError, AgentPermissionRequest, AgentTextDelta, AgentToolCall, AgentToolResult
from libre_claw.core.cordis_engine import CordisEngine
from libre_claw.core.permissions import PermissionManager
from libre_claw.core.session import ChatMessage, Session, text_block, tool_result_block, tool_use_block
from libre_claw.core.tools import ToolContext, ToolRegistry
from libre_claw.providers.base import Done, LLMProvider, ReasoningDelta, TextDelta, ToolCallReady, Usage

PROTOCOL_VERSION = 1
MAX_FRAME = 8 * 1024 * 1024
MAX_CALLBACK_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 10000
MAX_ACTIVE = 64
SESSION_ID = re.compile(r"^[a-f0-9]{32}$")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string(value: Any, name: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _tokens(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ValueError("Provider token usage must be a nonnegative safe integer")
    return value


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"Nonfinite JSON number: {value}")


def _permission(value: Any) -> str:
    if value not in {"read-only", "workspace-write"}:
        raise ValueError("Unknown permission mode")
    return value


def bridge_messages(messages: Sequence[ChatMessage], system: str | None = None) -> list[dict[str, Any]]:
    """Preserve tool correlation and provider reasoning across callback turns."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": str(system)})
    for message in messages:
        text, thinking, calls, results = [], [], [], []
        metadata: dict[str, Any] = {}
        for block in message.content:
            kind = block.get("type")
            if kind == "text":
                text.append(str(block.get("text", "")))
            elif kind == "provider_reasoning":
                thinking.append(str(block.get("text", "")))
            elif kind == "webui_provider_metadata":
                metadata.update(block.get("value", {}))
            elif kind == "tool_use":
                calls.append({"id": block["id"], "name": block["name"], "arguments": json.dumps(block["input"]), **({"providerMetadata": block["providerMetadata"]} if block.get("providerMetadata") else {})})
            elif kind == "tool_result":
                results.append({"role": "tool", "content": str(block.get("content", "")), "toolCallId": block["tool_use_id"]})
            elif kind == "image":
                raise ValueError("Host bridge text sessions do not accept image blocks; use Work or Chat attachments")
        if text or thinking or calls or not results:
            out.append({"role": message.role, "content": "".join(text),
                        **({"thinking": "".join(thinking)} if thinking else {}),
                        **({"toolCalls": calls} if calls else {}),
                        **({"providerMetadata": metadata} if metadata else {})})
        out.extend(results)
    return out


def work_session(request: dict[str, Any]) -> Session:
    session = Session()
    for message in request.get("messages", []):
        message = _object(message, "message")
        role = message.get("role")
        content = _string(message.get("content", ""), "message content", optional=True)
        if role == "system":
            continue
        if role == "tool":
            session.add_tool_result_blocks([tool_result_block(_string(message.get("tool_call_id"), "tool_call_id"), content)])
        elif role == "assistant":
            blocks = [text_block(content)] if content else []
            for call in message.get("tool_calls", []):
                fn = _object(call.get("function"), "function")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                blocks.append(tool_use_block(_string(call.get("id"), "tool id"), _string(fn.get("name"), "tool name"), _object(args, "arguments")))
            session.add_assistant_blocks(blocks)
        elif role == "user":
            session.add_user_message(content)
        else:
            raise ValueError("Unknown Work message role")
    return session


@dataclass
class Callback:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=4096))
    bytes: int = 0
    received_bytes: int = 0
    terminal: bool = False


@dataclass
class BridgeSession:
    id: str
    cwd: str
    title: str
    model: str
    permission: str
    created: int
    transient: bool = False
    session: Session = field(default_factory=Session)
    migration: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None
    turn_id: str | None = None
    approvals: dict[str, AgentPermissionRequest] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "createdAt": self.created,
                "workspacePath": self.cwd, "eventCount": len(self.session.messages)}

    def snapshot(self) -> dict[str, Any]:
        projected = []
        for index, message in enumerate(bridge_messages(self.session.messages)):
            projected.append({"id": f"{self.id}:{index}", "role": message["role"], "text": message["content"],
                              "source": "model" if message["role"] == "assistant" else message["role"],
                              **({"reasoning": message["thinking"]} if message.get("thinking") else {}),
                              **({"toolCalls": [{"callId": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in message["toolCalls"]]} if message.get("toolCalls") else {}),
                              **({"toolResults": [{"callId": message["toolCallId"], "output": message["content"], "isError": False}]} if message.get("toolCallId") else {})})
        return {**self.summary(), "active": self.task is not None and not self.task.done(),
                "settings": {"model": self.model, "permissionMode": self.permission},
                "capabilities": {"permissions": True, "approvals": True},
                "messages": projected,
                "approvals": [{"id": key, "sessionId": self.id, "callId": event.call.id, "toolName": event.call.name} for key, event in self.approvals.items()]}


class CallbackProvider(LLMProvider):
    def __init__(self, bridge: Bridge, turn_id: str, model: str, *, request: dict[str, Any] | None = None) -> None:
        self.bridge, self.turn_id, self.model, self.request = bridge, turn_id, model, request
        self.result: dict[str, Any] | None = None
        self.metadata: dict[str, Any] = {}
        self.tool_metadata: dict[str, dict[str, Any]] = {}

    async def complete(self, messages, tools=None, system=None, stream=True, temperature=0.7, max_tokens=None):
        params = {"turnId": self.turn_id}
        if self.request is None:
            method = "provider.stream"
            params.update({"model": self.model, "messages": bridge_messages(messages, system),
                           "tools": [{"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema", {})} for t in tools or []],
                           "temperature": temperature, **({"maxTokens": max_tokens} if max_tokens else {})})
        else:
            method = "work.provider"
            params["request"] = self.request
        calls: dict[str, dict[str, str]] = {}
        usage = None
        reason = "stop"
        streamed = {"text": "", "reasoning": ""}
        terminal = False
        model_finished = False
        async with contextlib.aclosing(self.bridge.callback(method, params)) as frames:
            async for frame in frames:
                if frame.get("error"):
                    raise RuntimeError(str(frame["error"].get("message", "Provider failed")))
                if frame.get("done"):
                    terminal = True
                    if self.request is not None:
                        self.result = _object(frame.get("result"), "Work response")
                        response = _object(self.result.get("message"), "Work response message")
                        if self.result.get("done") is not True or response.get("role") != "assistant":
                            raise ValueError("Work response must contain a completed assistant message")
                        if response.get("providerMetadata") is not None:
                            self.metadata.update(_object(response["providerMetadata"], "provider metadata"))
                        for kind, field_name in (("text", "content"), ("reasoning", "thinking")):
                            full = _string(response.get(field_name, ""), "Work response text", optional=True)
                            prefix = streamed[kind]
                            if not full.startswith(prefix):
                                raise ValueError("Provider final text differs from its streamed prefix")
                            delta = full[len(prefix):]
                            if delta:
                                if kind == "text":
                                    yield TextDelta(delta)
                                else:
                                    await self.bridge.emit(self.turn_id, {"type": "reasoning", "text": delta})
                                    yield ReasoningDelta(delta, "libre-webui")
                        for raw in response.get("tool_calls", []):
                            fn = _object(raw.get("function"), "tool function")
                            call_id = raw.get("id") or f"claw-{uuid.uuid4().hex}"
                            raw["id"] = call_id
                            args = fn.get("arguments", {})
                            calls[call_id] = {"name": _string(fn.get("name"), "tool name"), "arguments": args if isinstance(args, str) else json.dumps(args)}
                        reason = "tool-calls" if calls else "stop"
                    elif not model_finished:
                        raise ValueError("Provider stream ended without a model completion event")
                    break
                event = _object(frame.get("event"), "provider event")
                kind = event.get("type")
                if model_finished:
                    raise ValueError("Provider sent data after model completion")
                if kind in {"text", "reasoning"}:
                    delta = _string(event.get("text"), "delta", optional=True)
                    streamed[kind] += delta
                    if len(streamed[kind]) > MAX_CALLBACK_BYTES:
                        raise ValueError("Provider response exceeds buffer limit")
                    if kind == "text":
                        yield TextDelta(delta)
                    else:
                        await self.bridge.emit(self.turn_id, {"type": "reasoning", "text": delta})
                        yield ReasoningDelta(delta, "libre-webui")
                elif kind == "tool-call":
                    call = _object(event.get("toolCall"), "tool call")
                    call_id = _string(call.get("id"), "tool id")
                    name = _string(call.get("name", ""), "tool name", optional=True)
                    args = _string(call.get("arguments", ""), "tool arguments", optional=True)
                    previous = calls.setdefault(call_id, {"name": name, "arguments": ""})
                    if previous["name"] and name and previous["name"] != name:
                        raise ValueError("Provider changed a tool name within a call")
                    previous["name"] = previous["name"] or name
                    previous["arguments"] += args
                    if call.get("providerMetadata") is not None:
                        self.tool_metadata.setdefault(call_id, {}).update(_object(call["providerMetadata"], "tool metadata"))
                elif kind == "usage":
                    usage = Usage(input_tokens=_tokens(event.get("inputTokens", event.get("promptTokens", 0))), output_tokens=_tokens(event.get("outputTokens", event.get("completionTokens", 0))))
                    if self.request is not None:
                        await self.bridge.emit(self.turn_id, event)
                elif kind == "done":
                    reason = event.get("reason", "stop")
                    self.metadata.update(_object(event.get("providerMetadata", {}), "provider metadata"))
                    if reason not in {"stop", "tool-calls", "max-tokens"}:
                        raise RuntimeError("The model reported an unsuccessful completion")
                    model_finished = True
                else:
                    raise ValueError("Unknown provider event")
        if not terminal:
            raise RuntimeError("Provider callback ended without completion")
        for call_id, call in calls.items():
            yield ToolCallReady(call_id, _string(call["name"], "tool name"), _object(json.loads(call["arguments"]), "tool arguments"))
        yield Done(usage=usage, stop_reason=reason)


class Bridge:
    def __init__(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.send = send
        self.engine: CordisEngine | None = None
        self.initialized = False
        self.mode = ""
        self.workspace = Path()
        self.store: Path | None = None
        self.tools_enabled = False
        self.model = ""
        self.sessions: dict[str, BridgeSession] = {}
        self.callbacks: dict[str, Callback] = {}
        self.work_tasks: dict[str, asyncio.Task[Any]] = {}
        self.closed = False

    async def callback(self, method: str, params: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if len(self.callbacks) >= MAX_ACTIVE:
            raise RuntimeError("Provider concurrency limit reached")
        callback_id = uuid.uuid4().hex
        state = self.callbacks[callback_id] = Callback()
        try:
            await self.send({"callbackId": callback_id, "method": method, "params": params})
            while True:
                frame = await asyncio.wait_for(state.queue.get(), timeout=300)
                state.bytes -= len(json.dumps(frame).encode())
                yield frame
                if frame.get("done") or frame.get("error"):
                    return
        finally:
            self.callbacks.pop(callback_id, None)
            if not state.terminal and not self.closed:
                await self.send({"event": "callback.cancel", "callbackId": callback_id})

    def receive_callback(self, frame: dict[str, Any]) -> None:
        callback_id = _string(frame.get("callbackId"), "callbackId")
        state = self.callbacks.get(callback_id)
        if state is None:
            return  # A cancelled turn may still have an in-flight parent frame.
        if state.terminal:
            raise ValueError("Provider sent data after callback completion")
        if "done" in frame and type(frame["done"]) is not bool:
            raise ValueError("Callback completion must be a boolean")
        size = len(json.dumps(frame, allow_nan=False).encode())
        if state.received_bytes + size > MAX_CALLBACK_BYTES or state.queue.full():
            raise ValueError("Provider callback buffer limit exceeded")
        state.bytes += size
        state.received_bytes += size
        state.terminal = bool(frame.get("done") or frame.get("error"))
        state.queue.put_nowait(frame)

    async def emit(self, turn_id: str, chunk: dict[str, Any]) -> None:
        await self.send({"event": "stream", "turnId": turn_id, "chunk": chunk})

    def directory(self, cwd: Any) -> Path:
        candidate = Path(_string(cwd, "cwd")).resolve(strict=True)
        if not candidate.is_dir() or not candidate.is_relative_to(self.workspace):
            raise ValueError("Session workspace must be inside the configured workspace")
        return candidate

    def registry(self, record: BridgeSession) -> ToolRegistry:
        if not self.tools_enabled:
            return ToolRegistry()
        from libre_claw.tools_builtin.filesystem import EditFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
        from libre_claw.tools_builtin.search import GlobTool, SearchFilesTool
        context = ToolContext(working_directory=Path(record.cwd), restrict_to_working_dir=True,
                              automations_enabled=False, skills_enabled=False, web_search_enabled=False)
        tools = [ReadFileTool(context), ListDirectoryTool(context), GlobTool(context), SearchFilesTool(context)]
        if record.permission == "workspace-write":
            tools.extend([WriteFileTool(context), EditFileTool(context)])
        return ToolRegistry(tools)

    def persist(self, record: BridgeSession) -> None:
        if self.store is None or record.transient:
            return
        data = {"version": 1, "id": record.id, "cwd": record.cwd, "title": record.title,
                "model": record.model, "permission": record.permission, "created": record.created,
                "session": asdict(record.session),
                **({"migration": record.migration} if record.migration is not None else {})}
        fd, name = tempfile.mkstemp(dir=self.store, prefix=".session-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.store / f"{record.id}.json")
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name)

    def restore(self) -> None:
        if self.store is None:
            return
        for path in self.store.glob("*.json"):
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError("Session store limit exceeded")
            if not SESSION_ID.fullmatch(path.stem) or path.is_symlink() or path.stat().st_size > MAX_CALLBACK_BYTES:
                raise ValueError("Invalid persisted bridge session")
            data = json.loads(path.read_text(), parse_constant=_reject_nonfinite)
            if data.get("version") != 1 or data.get("id") != path.stem:
                raise ValueError("Unsupported bridge session format")
            # A deployment may have deliberately retired a workspace. Do not
            # restore capabilities to it or make the entire host inaccessible.
            try:
                cwd = str(self.directory(data["cwd"]))
            except (ValueError, FileNotFoundError):
                continue
            values = _object(data.get("session"), "stored session")
            values["messages"] = [ChatMessage(**message) for message in values.get("messages", [])]
            values["archived_messages"] = [ChatMessage(**message) for message in values.get("archived_messages", [])]
            session = Session(**values)
            session.recover_interrupted_tools()
            self.sessions[path.stem] = BridgeSession(path.stem, cwd, data["title"], data["model"],
                                                    _permission(data["permission"]), data["created"], session=session,
                                                    migration=_object(data["migration"], "migration") if "migration" in data else None)

    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.engine is not None or self.initialized:
            raise ValueError("Bridge is already initialized")
        if params.get("protocolVersion") != PROTOCOL_VERSION or params.get("mode") not in {"host", "work"}:
            raise ValueError("Unsupported bridge protocol or mode")
        self.mode = params["mode"]
        self.model = _string(params.get("model"), "model", optional=True)
        self.engine = CordisEngine(node_executable=os.environ.get("LIBRE_CLAW_NODE", "node"))
        try:
            if self.mode == "host":
                self.workspace = Path(_string(params.get("workspacePath"), "workspacePath")).resolve(strict=True)
                if not self.workspace.is_dir():
                    raise ValueError("workspacePath must be a directory")
                self.tools_enabled = params.get("tools") is True
                if params.get("persistence") is True:
                    store = Path(_string(params.get("sessionStorePath"), "sessionStorePath")) / "libre-claw"
                    if store.is_symlink():
                        raise ValueError("Session store cannot be a symlink")
                    store.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if store.stat().st_uid != os.getuid():
                        raise ValueError("Session store is not owned by this process")
                    os.chmod(store, 0o700)
                    self.store = store.resolve()
                    self.restore()
            elif params.get("persistence") or params.get("tools"):
                raise ValueError("Work mode cannot mount host tools or persistence")
            await self.engine.start()
            status = await self.engine.inspect()
            self.initialized = True
            return {"protocolVersion": PROTOCOL_VERSION, "engine": "libre-claw",
                    "services": [{"name": row["id"], "state": "ready" if row["state"] == "ACTIVE" else "failed"} for row in status["components"]]}
        except BaseException:
            await self.engine.aclose()
            raise

    def record(self, session_id: Any) -> BridgeSession:
        record = self.sessions.get(_string(session_id, "sessionId"))
        if record is None:
            raise ValueError("Session not found")
        return record

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return await self.initialize(params)
        if method == "shutdown":
            await self.close()
            return True
        if not self.initialized or self.closed:
            raise RuntimeError("Bridge is not initialized")
        if method == "turn.cancel":
            record = self.sessions.get(params.get("sessionId"))
            task = record.task if record else self.work_tasks.get(params.get("turnId"))
            if task is None or task.done():
                return False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return True
        if method == "work.generate":
            if self.mode != "work":
                raise ValueError("work.generate requires Work mode")
            return await self.work_generate(params)
        if self.mode != "host":
            raise ValueError("Host operations are unavailable in Work mode")
        if method == "session.list":
            return [s.summary() for s in sorted(self.sessions.values(), key=lambda s: s.created, reverse=True) if not s.transient]
        if method == "session.get":
            record = self.sessions.get(params.get("sessionId"))
            return record.snapshot() if record else None
        if method == "session.create":
            if len(self.sessions) >= MAX_SESSIONS:
                raise RuntimeError("Session limit reached")
            cwd = str(self.directory(params.get("cwd", str(self.workspace))))
            record = BridgeSession(uuid.uuid4().hex, cwd, _string(params.get("title", ""), "title", optional=True),
                                   _string(params.get("model", self.model), "model", optional=True),
                                   _permission(params.get("permissionMode", "read-only")), int(time.time() * 1000),
                                   transient=params.get("transient") is True)
            self.persist(record)
            self.sessions[record.id] = record
            return record.snapshot()
        if method == "agent.list":
            return [{"id": s.id, "root": True} for s in self.sessions.values() if s.task is not None and not s.task.done()]
        if method == "tool.list":
            sample = BridgeSession("", str(self.workspace), "", "", "workspace-write", 0)
            return [{"name": tool.name, "description": tool.description} for tool in self.registry(sample).tools()]
        record = self.record(params.get("sessionId"))
        if method == "session.update":
            if record.task and not record.task.done():
                raise ValueError("Cannot change settings during an active turn")
            settings = _object(params.get("settings", {}), "settings")
            permission = _permission(settings.get("permissionMode", record.permission))
            model = _string(settings.get("model", record.model), "model", optional=True)
            record.permission, record.model = permission, model
            self.persist(record)
            return record.snapshot()
        if method == "session.delete":
            if record.task and not record.task.done():
                record.task.cancel()
                await asyncio.gather(record.task, return_exceptions=True)
            self.sessions.pop(record.id)
            if self.store and not record.transient:
                (self.store / f"{record.id}.json").unlink(missing_ok=True)
            return True
        if method == "session.approve":
            approval = record.approvals.get(params.get("approvalId"))
            if params.get("outcome") not in {"allowed-once", "rejected"}:
                raise ValueError("Invalid approval outcome")
            if approval is None or approval.future.done():
                return False
            approval.future.set_result("allow_once" if params["outcome"] == "allowed-once" else "deny")
            record.approvals.pop(params["approvalId"], None)
            await self.emit(record.turn_id or "", {"type": "approval-decision", "approvalId": params["approvalId"], "outcome": params["outcome"]})
            return True
        if method == "turn.start":
            if record.task and not record.task.done():
                raise ValueError("Session already has an active turn")
            if sum(bool(s.task and not s.task.done()) for s in self.sessions.values()) >= MAX_ACTIVE:
                raise ValueError("Turn concurrency limit reached")
            if params.get("cwd") and str(self.directory(params["cwd"])) != record.cwd:
                raise ValueError("Turn workspace differs from its session")
            text = _string(params.get("text"), "text")
            turn_id = _string(params.get("turnId"), "turnId")
            record.model = _string(params.get("model", record.model), "model", optional=True)
            record.turn_id = turn_id
            record.task = asyncio.create_task(self.run_turn(record, text, turn_id))
            # Broken output pipes can fail the terminal send after the caller
            # has gone away. Always retrieve detached turn exceptions.
            record.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            return {"turnId": turn_id}
        raise ValueError("Unknown bridge method")

    async def run_turn(self, record: BridgeSession, text: str, turn_id: str) -> None:
        provider = CallbackProvider(self, turn_id, record.model)
        async def checkpoint(session: Session) -> None:
            if session.messages and session.messages[-1].role == "assistant":
                if provider.metadata:
                    session.messages[-1].content.append({"type": "webui_provider_metadata", "value": dict(provider.metadata)})
                    provider.metadata.clear()
                for block in session.messages[-1].content:
                    if block.get("type") == "tool_use" and block.get("id") in provider.tool_metadata:
                        block["providerMetadata"] = provider.tool_metadata.pop(block["id"])
            self.persist(record)
        agent = Agent(record.session, provider, self.registry(record), PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True)),
                      "You are Libre Claw, assisting in the selected workspace. Respect the user's instructions and tool approvals.",
                      engine=self.engine, checkpoint_callback=checkpoint)
        reason, interrupted = "stop", False
        try:
            async with contextlib.aclosing(agent.run(text)) as events:
                async for event in events:
                    if isinstance(event, AgentTextDelta):
                        await self.emit(turn_id, {"type": "text", "text": event.text})
                    elif isinstance(event, AgentToolCall):
                        await self.emit(turn_id, {"type": "tool-call", "callId": event.call.id, "name": event.call.name, "arguments": json.dumps(event.call.arguments)})
                    elif isinstance(event, AgentToolResult):
                        await self.emit(turn_id, {"type": "tool-result", "callId": event.call.id, "name": event.call.name, "isError": event.result.is_error, "output": event.result.as_text()})
                    elif isinstance(event, AgentPermissionRequest):
                        approval_id = uuid.uuid4().hex
                        record.approvals[approval_id] = event
                        await self.emit(turn_id, {"type": "approval-request", "approval": {"id": approval_id, "sessionId": record.id, "callId": event.call.id, "toolName": event.call.name}})
                    elif isinstance(event, AgentError):
                        reason = "error"
                        await self.emit(turn_id, {"type": "error", "message": event.message, "code": "LIBRE_CLAW_TURN_FAILED"})
        except asyncio.CancelledError:
            reason, interrupted = "cancelled", True
        except Exception as exc:
            reason = "error"
            await self.emit(turn_id, {"type": "error", "message": str(exc), "code": "LIBRE_CLAW_TURN_FAILED"})
        finally:
            for key, approval in list(record.approvals.items()):
                if not approval.future.done():
                    approval.future.cancel()
                await self.emit(turn_id, {"type": "approval-decision", "approvalId": key, "outcome": "cancelled"})
            record.approvals.clear()
            try:
                self.persist(record)
            except Exception:
                reason = "error"
                await self.emit(turn_id, {"type": "error", "message": "Could not persist the session", "code": "LIBRE_CLAW_SESSION_STORE_FAILED"})
            await self.emit(turn_id, {"type": "done", "reason": reason, "interrupted": interrupted})

    async def work_generate(self, params: dict[str, Any]) -> Any:
        if self.work_tasks:
            raise ValueError("A Work step is already active")
        turn_id = _string(params.get("turnId"), "turnId")
        request = _object(params.get("request"), "request")
        task = asyncio.current_task()
        assert task is not None
        self.work_tasks[turn_id] = task
        try:
            provider = CallbackProvider(self, turn_id, str(request.get("model", "")), request=request)
            session = work_session(request)
            agent = Agent(session, provider, ToolRegistry(), PermissionManager(PermissionsConfig(default_level="ask", auto_approve_read=True)), "",
                          engine=self.engine, auto_compact_threshold=1000)
            async with contextlib.aclosing(agent.step()) as events:
                async for event in events:
                    if isinstance(event, AgentTextDelta):
                        await self.emit(turn_id, {"type": "text", "text": event.text})
                    elif isinstance(event, AgentError):
                        raise RuntimeError(event.message)
            if provider.result is None:
                raise RuntimeError("Work provider produced no result")
            return provider.result
        finally:
            self.work_tasks.pop(turn_id, None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        tasks = [s.task for s in self.sessions.values() if s.task and not s.task.done()]
        tasks.extend(t for t in self.work_tasks.values() if not t.done() and t is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.engine:
            await self.engine.aclose()


async def serve() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME)
    read_transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    lock = asyncio.Lock()
    async def send(frame: dict[str, Any]) -> None:
        payload = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(payload) > MAX_FRAME:
            raise ValueError("Bridge output frame exceeds limit")
        async with lock:
            writer.write(payload)
            await writer.drain()
    bridge = Bridge(send)
    requests: dict[str, asyncio.Task[None]] = {}
    async def request(frame: dict[str, Any]) -> None:
        request_id = frame["id"]
        try:
            result = await bridge.dispatch(_string(frame.get("method"), "method"), _object(frame.get("params", {}), "params"))
            await send({"id": request_id, "result": result})
        except asyncio.CancelledError:
            await send({"id": request_id, "error": {"code": "CANCELLED", "message": "Bridge request cancelled"}})
        except Exception as exc:
            await send({"id": request_id, "error": {"code": "LIBRE_CLAW_BRIDGE_ERROR", "message": str(exc)}})
        finally:
            requests.pop(request_id, None)
    current = asyncio.current_task()
    def request_finished(task: asyncio.Task[None]) -> None:
        # A failed response write must end the connection and reap the engine,
        # rather than leave an unobserved task and an orphaned child alive.
        if not task.cancelled() and task.exception() is not None and current:
            current.cancel()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: current.cancel() if current else None)
    try:
        while not bridge.closed:
            raw = await reader.readline()
            if not raw:
                break
            if len(raw) > MAX_FRAME:
                raise ValueError("Bridge input frame exceeds limit")
            if not raw.endswith(b"\n"):
                raise ValueError("Bridge input ended in a partial frame")
            frame = _object(json.loads(raw, parse_constant=_reject_nonfinite), "frame")
            if "callbackId" in frame:
                bridge.receive_callback(frame)
                continue
            request_id = _string(frame.get("id"), "id")
            if request_id in requests or len(requests) >= MAX_ACTIVE or len(request_id) > 128:
                raise ValueError("Invalid or excessive concurrent bridge request")
            requests[request_id] = asyncio.create_task(request(frame))
            requests[request_id].add_done_callback(request_finished)
            if frame.get("method") == "shutdown":
                await requests[request_id]
                break
    finally:
        await bridge.close()
        for task in list(requests.values()):
            task.cancel()
        await asyncio.gather(*list(requests.values()), return_exceptions=True)
        read_transport.close()
        transport.close()


def main() -> None:
    # Imported libraries must never mix log lines into the machine protocol.
    import logging
    import structlog
    logging.basicConfig(stream=sys.stderr)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    try:
        asyncio.run(serve())
    except (KeyboardInterrupt, asyncio.CancelledError, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
