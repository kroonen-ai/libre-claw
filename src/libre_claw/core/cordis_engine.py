# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Persistent, offline Cordis control plane for Libre Claw's core services.

Cordis owns service activation, dependencies, operation dispatch and lifecycle.
Python implements the service algorithms. Each invocation grants one callback
to one opaque operation; neither model credentials nor task content crosses the
process boundary. Extension plugins run in their own separately granted runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.runs import settle_finalization


T = TypeVar("T")
MAX_ENGINE_FRAME = 64 * 1024
MAX_ENGINE_OPERATIONS = 128
_COMPONENT_IDS = frozenset({"agent", "providers", "tools", "sessions", "memory", "workflows"})
_RUNTIME = Path(__file__).resolve().parents[1] / "cordis_runtime" / "engine.mjs"
_CONTROL_TIMEOUT = 15
_DISPATCH_TIMEOUT = 15


class CordisEngineError(RuntimeError):
    """The core service graph could not perform an operation."""


@dataclass
class _Operation:
    service: str
    method: str
    mode: str
    handler: Callable[[], Any]
    queue: asyncio.Queue[tuple[str, int | None]] = field(default_factory=lambda: asyncio.Queue(maxsize=2))
    task: asyncio.Task[None] | None = None
    acknowledgement: asyncio.Future[None] | None = None
    sequence: int = 0
    value: Any = None
    error: BaseException | None = None
    dispatched: asyncio.Future[None] | None = None


class CordisEngine:
    """One service graph per daemon (or explicitly scoped standalone agent).

    ``call`` accepts a zero-argument callback returning a value or awaitable.
    ``stream`` accepts a callback producing an async iterator. Callbacks may
    recursively use this engine; streams keep at most one undelivered item each.
    Closing a stream cancels its callback and runs generator cleanup.

    A crashed or closed engine never silently falls back to direct execution.
    Construct a new instance to deliberately start a replacement engine.
    """

    def __init__(
        self,
        *,
        node_executable: str = "node",
        components: Mapping[str, bool] | None = None,
        plugin_loader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.node_executable = node_executable
        self._components = dict(components or {})
        self._plugin_loader = plugin_loader
        self._plugin_specs: list[dict[str, Any]] = []
        self._component_ids = set(_COMPONENT_IDS)
        self._process: asyncio.subprocess.Process | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._controls: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._operations: dict[str, _Operation] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._state = "new"
        self._failure: CordisEngineError | None = None
        self.isolation: str | None = None

    @property
    def running(self) -> bool:
        return self._state == "running" and self._process is not None and self._process.returncode is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def busy(self) -> bool:
        return bool(self._operations)

    def is_enabled(self, service: str) -> bool:
        """Return the last accepted enabled flag without starting a process."""
        return service in self._component_ids and self._components.get(service, True) is True

    async def __aenter__(self) -> CordisEngine:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.running:
                return
            if self._state != "new":
                raise self._failure or CordisEngineError("The Cordis engine is closed.")
            self._state = "starting"
            try:
                if self._plugin_loader is not None:
                    self._plugin_specs = await asyncio.to_thread(self._plugin_loader)
                    self._component_ids.update(row["id"] for spec in self._plugin_specs for row in spec["services"])
                self._temporary = tempfile.TemporaryDirectory(prefix="libre-claw-engine-")
                prepared, cancelled = await settle_finalization(asyncio.create_task(asyncio.to_thread(
                    prepare_cordis_process,
                    self.node_executable,
                    _RUNTIME,
                    _RUNTIME.parent,
                    Path(self._temporary.name),
                    read_paths=tuple(Path(spec["root"]) for spec in self._plugin_specs),
                )))
                if cancelled:
                    raise asyncio.CancelledError
                self.isolation = prepared.isolation
                self._process, cancelled = await settle_finalization(asyncio.create_task(asyncio.create_subprocess_exec(
                    *prepared.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=prepared.env,
                    cwd=prepared.cwd,
                    limit=MAX_ENGINE_FRAME + 1,
                    close_fds=True,
                )))
                if cancelled:
                    raise asyncio.CancelledError
                self._reader_task = asyncio.create_task(self._read_frames(), name="cordis-engine-reader")
                self._stderr_task = asyncio.create_task(self._drain_stderr(), name="cordis-engine-stderr")
                await self._control("initialize", components=self._components,
                                    **({"plugins": self._plugin_specs} if self._plugin_specs else {}))
                self._state = "running"
            except BaseException as exc:
                self._fail("The offline Cordis engine could not start.")
                await settle_finalization(asyncio.create_task(self._stop_process()))
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, CordisSecurityError):
                    raise CordisEngineError(str(exc)) from exc
                if isinstance(exc, CordisEngineError):
                    raise
                raise CordisEngineError("The offline Cordis engine could not start.") from exc

    async def configure(self, components: Mapping[str, bool]) -> dict[str, Any]:
        """Atomically validate enabled flags; dependency/busy errors change nothing."""
        await self.start()
        result = await self._control("configure", components=dict(components))
        self._components.update(components)
        return self._snapshot(result)

    async def inspect(self) -> dict[str, Any]:
        await self.start()
        return self._snapshot(await self._control("inspect"))

    def _snapshot(self, result: dict[str, Any]) -> dict[str, Any]:
        return {**result, "isolation": self.isolation}

    async def call(self, service: str, method: str, *, handler: Callable[[], T | Awaitable[T]]) -> T:
        operation_id, operation = await self._invoke(service, method, "call", handler)
        try:
            kind, _ = await operation.queue.get()
            if kind != "done":
                raise CordisEngineError("Invalid Cordis call response.")
            if operation.error is not None:
                raise operation.error
            return operation.value
        finally:
            await self._release(operation_id, operation)

    async def stream(
        self,
        service: str,
        method: str,
        *,
        handler: Callable[[], AsyncIterator[T] | Awaitable[AsyncIterator[T]]],
    ) -> AsyncIterator[T]:
        operation_id, operation = await self._invoke(service, method, "stream", handler)
        try:
            while True:
                kind, sequence = await operation.queue.get()
                if kind == "done":
                    if operation.error is not None:
                        raise operation.error
                    return
                if kind != "item" or sequence != operation.sequence:
                    raise CordisEngineError("Invalid Cordis stream response.")
                yield operation.value
                await self._send({"type": "ack", "id": operation_id, "sequence": sequence})
        finally:
            await self._release(operation_id, operation)

    async def _invoke(self, service: str, method: str, mode: str, handler: Callable[[], Any]) -> tuple[str, _Operation]:
        await self.start()
        await self.verify_plugins()
        if not callable(handler):
            raise TypeError("An engine operation requires an explicit handler.")
        if len(self._operations) >= MAX_ENGINE_OPERATIONS:
            raise CordisEngineError("Too many active engine operations.")
        operation_id = uuid.uuid4().hex
        operation = _Operation(service, method, mode, handler)
        operation.dispatched = asyncio.get_running_loop().create_future()
        self._operations[operation_id] = operation
        try:
            try:
                # Node's own timer cannot interrupt a synchronous extension
                # loop. Bound both the pipe write and admission in Python,
                # since a blocked Node process may also stop draining stdin.
                async with asyncio.timeout(_DISPATCH_TIMEOUT):
                    await self._send({"type": "invoke", "id": operation_id, "service": service, "method": method, "mode": mode})
                    await asyncio.shield(operation.dispatched)
            except TimeoutError:
                self._fail("The Cordis engine did not dispatch the operation within its time limit.")
                raise self._failure from None
        except BaseException:
            # Cancellation can arrive after write() but before drain(). Revoke
            # the remote operation too, even when its dispatch reply is pending.
            await self._release(operation_id, operation)
            raise
        return operation_id, operation

    async def verify_plugins(self) -> None:
        """Revocation or changed code/config invalidates active core extensions."""
        if self._plugin_loader is None:
            return
        try:
            current = await asyncio.to_thread(self._plugin_loader)
            # New grants wait for an explicit restart. Only a changed/revoked
            # implementation already loaded into this graph invalidates it.
            current_by_id = {spec["plugin_id"]: spec for spec in current}
            if all(current_by_id.get(spec["plugin_id"]) == spec for spec in self._plugin_specs):
                return
        except Exception:
            # A loader failure invalidates the graph through the same safe error
            # below; it must never leave previously granted extensions active.
            pass
        self._fail("Core plugin code, configuration, or grants changed. Restart the engine after review.")
        raise self._failure

    async def _run_handler(self, operation_id: str, operation: _Operation) -> None:
        iterator: AsyncIterator[Any] | None = None
        try:
            await self.verify_plugins()
            result = operation.handler()
            if inspect.isawaitable(result):
                result = await result
            if operation.mode == "call":
                operation.value = result
            else:
                iterator = aiter(result)
                async for item in iterator:
                    await self.verify_plugins()
                    operation.value = item
                    operation.acknowledgement = asyncio.get_running_loop().create_future()
                    await self._send({"type": "host.item", "id": operation_id, "sequence": operation.sequence})
                    await operation.acknowledgement
                    operation.sequence += 1
                    operation.value = None
            await self._send({"type": "host.done", "id": operation_id})
        except asyncio.CancelledError:
            if operation.error is None:
                operation.error = asyncio.CancelledError()
            with contextlib.suppress(CordisEngineError, BrokenPipeError, ConnectionError):
                await self._send({"type": "host.error", "id": operation_id})
            raise
        except Exception as exc:
            operation.error = exc
            with contextlib.suppress(CordisEngineError, BrokenPipeError, ConnectionError):
                await self._send({"type": "host.error", "id": operation_id})
        finally:
            closer = getattr(iterator, "aclose", None)
            if closer is not None:
                await closer()

    async def _release(self, operation_id: str, operation: _Operation) -> None:
        # Revoke the callback before acknowledging cancellation, so an in-flight
        # host.call cannot start it after its consumer has gone away.
        self._operations.pop(operation_id, None)
        if self.running:
            with contextlib.suppress(CordisEngineError, BrokenPipeError, ConnectionError):
                await self._send({"type": "cancel", "id": operation_id})
        if operation.task is not None:
            if not operation.task.done():
                operation.task.cancel()
            await asyncio.gather(operation.task, return_exceptions=True)

    async def _control(self, kind: str, **params: Any) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._controls[request_id] = future
        try:
            await self._send({"type": kind, "id": request_id, **params})
            return await asyncio.wait_for(future, timeout=_CONTROL_TIMEOUT)
        except TimeoutError as exc:
            self._fail("The Cordis engine did not respond.")
            raise CordisEngineError("The Cordis engine did not respond.") from exc
        finally:
            self._controls.pop(request_id, None)
            # A write failure may fail every pending control before this call
            # has started awaiting its future. Consume that exception as well.
            if future.done() and not future.cancelled():
                future.exception()

    async def _send(self, frame: dict[str, Any]) -> None:
        if self._state in {"failed", "closed"}:
            raise self._failure or CordisEngineError("The Cordis engine is closed.")
        try:
            encoded = json.dumps(frame, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        except (TypeError, ValueError) as exc:
            raise CordisEngineError("Engine control frames must be JSON values.") from exc
        if len(encoded) > MAX_ENGINE_FRAME:
            raise CordisEngineError("Engine control frame exceeds 64 KiB.")
        async with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or process.returncode is not None:
                raise CordisEngineError("The Cordis engine is unavailable.")
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                self._fail("The Cordis engine stopped unexpectedly.")
                raise self._failure from exc

    async def _read_frames(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                if len(line) > MAX_ENGINE_FRAME:
                    raise CordisEngineError("Oversized engine response.")
                frame = json.loads(line)
                if not isinstance(frame, dict) or not isinstance(frame.get("id"), str):
                    raise CordisEngineError("Invalid engine response.")
                self._receive(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail("The Cordis engine sent an invalid response.")
        finally:
            if self._state not in {"closing", "closed"}:
                self._fail("The Cordis engine stopped unexpectedly.")

    def _receive(self, frame: dict[str, Any]) -> None:
        operation_id = frame["id"]
        if frame.get("type") == "result":
            future = self._controls.get(operation_id)
            if future is None or future.done():
                return
            if frame.get("failed"):
                future.set_exception(CordisEngineError(frame.get("error", "The engine request failed.")))
            elif isinstance(frame.get("result"), dict):
                future.set_result(frame["result"])
            else:
                raise CordisEngineError("Invalid engine control response.")
            return
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        kind = frame.get("type")
        if kind == "host.call":
            if operation.task is not None or (frame.get("service"), frame.get("method")) != (operation.service, operation.method):
                raise CordisEngineError("The engine requested an unauthorized callback.")
            operation.task = asyncio.create_task(self._run_handler(operation_id, operation), name=f"cordis-{operation.service}-{operation.method}")
            if operation.dispatched is not None and not operation.dispatched.done():
                operation.dispatched.set_result(None)
        elif kind == "host.ack":
            acknowledgement = operation.acknowledgement
            if frame.get("sequence") != operation.sequence or acknowledgement is None or acknowledgement.done():
                raise CordisEngineError("Invalid engine stream acknowledgement.")
            acknowledgement.set_result(None)
        elif kind == "host.cancel":
            if operation.task is not None:
                operation.task.cancel()
        elif kind == "item":
            operation.queue.put_nowait(("item", frame.get("sequence")))
        elif kind == "done":
            if operation.dispatched is not None and not operation.dispatched.done():
                operation.dispatched.set_result(None)
            if frame.get("failed") and operation.error is None:
                operation.error = CordisEngineError(frame.get("error", "The engine operation failed."))
            operation.queue.put_nowait(("done", None))
        else:
            raise CordisEngineError("Unknown engine response.")

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        total = 0
        while chunk := await self._process.stderr.read(4096):
            total += len(chunk)
            if total > MAX_ENGINE_FRAME:
                self._fail("The Cordis engine exceeded its diagnostic output limit.")
                return

    def _fail(self, message: str) -> None:
        if self._state in {"closed", "failed"}:
            return
        self._state = "failed"
        self._failure = CordisEngineError(message)
        for future in self._controls.values():
            if not future.done():
                future.set_exception(CordisEngineError(message))
        for operation in self._operations.values():
            if operation.dispatched is not None and not operation.dispatched.done():
                operation.dispatched.set_result(None)
            operation.error = CordisEngineError(message)
            if operation.task is not None:
                operation.task.cancel()
            while not operation.queue.empty():
                operation.queue.get_nowait()
            operation.queue.put_nowait(("done", None))
        if self._process is not None and self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()

    async def aclose(self, *, cancel_active: bool = True) -> None:
        async with self._lifecycle_lock:
            if not cancel_active and self.busy:
                raise CordisEngineError("Wait for active engine operations before stopping the engine.")
            if self._state == "closed":
                return
            if self.running:
                self._state = "closing"
                with contextlib.suppress(Exception):
                    await self._control("close")
            for operation in list(self._operations.values()):
                if operation.task is not None:
                    operation.task.cancel()
            tasks = [operation.task for operation in self._operations.values() if operation.task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for operation in self._operations.values():
                operation.error = CordisEngineError("The Cordis engine is closed.")
                while not operation.queue.empty():
                    operation.queue.get_nowait()
                operation.queue.put_nowait(("done", None))
            self._state = "closed"
            await self._stop_process()

    async def _stop_process(self) -> None:
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
