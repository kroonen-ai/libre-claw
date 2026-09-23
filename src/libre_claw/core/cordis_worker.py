# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Persistent transport for one explicitly isolated Cordis extension process.

This module never chooses grants, imports user packages, or supplies host
capabilities. The caller provides a prepared process and separately authorized
handlers. Each request owns its callback scope and its own traffic budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from libre_claw.core.cordis_security import CordisProcess

MAX_FRAME_BYTES = 1024 * 1024
MAX_OPERATION_BYTES = 4 * MAX_FRAME_BYTES
MAX_HOST_REQUESTS = 256


class CordisWorkerError(RuntimeError):
    """A stable, public transport failure that contains no plugin data."""


class _ProtocolError(CordisWorkerError):
    pass


@dataclass
class _Budget:
    transferred: int = 0
    identifiers: set[str | int] = field(default_factory=set)

    def charge(self, size: int) -> None:
        self.transferred += size
        if size > MAX_FRAME_BYTES or self.transferred > MAX_OPERATION_BYTES:
            raise _ProtocolError("Plugin runtime exceeded its operation traffic limit.")


@dataclass
class _Operation:
    identifier: int
    future: asyncio.Future[Any]
    handler: Callable[..., Any] | None
    budget: _Budget = field(default_factory=_Budget)
    timeout: asyncio.Timeout | None = None
    revoked: bool = False


def _encoded(payload: dict[str, Any]) -> bytes:
    try:
        data = (json.dumps(payload, allow_nan=False, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise _ProtocolError("Plugin transport requires bounded JSON data.") from None
    if len(data) > MAX_FRAME_BYTES:
        raise _ProtocolError("Plugin transport frame exceeds its size limit.")
    return data


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


class CordisWorker:
    """One process, with serialized RPCs and a continuously serviced host pipe.

    A host handler receives ``(method, params, *, stream, timeout)``. It returns
    a value/awaitable for a normal request, or an async iterator for a stream.
    ``timeout`` is the current request's asyncio deadline; an interactive host
    may temporarily reschedule it while waiting for the user. Background
    callbacks receive their own bounded deadline and never a task callback.
    """

    def __init__(self, prepared: CordisProcess, initialize: dict[str, Any], *,
                 background_host_handler: Callable[..., Any] | None = None) -> None:
        self.prepared = CordisProcess(prepared.command, dict(prepared.env), prepared.cwd, prepared.isolation)
        self.initialize = json.loads(_encoded(initialize))
        self.background_host_handler = background_host_handler
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._host_tasks: dict[asyncio.Task[None], _Operation | None] = {}
        self._active_host_ids: set[str | int] = set()
        self._active: _Operation | None = None
        self._sequence = 0
        self._start_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._started = False
        self._closing = False
        self._closed = False
        self._fault: CordisWorkerError | None = None
        self._shutdown_requested = False
        self.startup: Any = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._closed and self._fault is None

    @property
    def isolation(self) -> str:
        return self.prepared.isolation

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def processPid(self) -> int | None:
        return self.pid

    async def start(self) -> Any:
        async with self._start_lock:
            if self._closed or self._closing or self._fault:
                raise CordisWorkerError("The plugin worker is closed.")
            if self._started:
                return self.startup
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *self.prepared.command, env=self.prepared.env, cwd=self.prepared.cwd,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=MAX_FRAME_BYTES + 1,
                )
                self._reader = asyncio.create_task(self._read_loop(), name="cordis-extension-reader")
                self.startup = await self._request("initialize", self.initialize, None, 30)
                self._started = True
                return self.startup
            except BaseException as error:
                self._abort(CordisWorkerError("The plugin worker could not start."))
                await self._stop()
                if isinstance(error, (asyncio.CancelledError, CordisWorkerError)):
                    raise
                raise CordisWorkerError("The plugin worker could not start.") from None

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      host_handler: Callable[..., Any] | None = None,
                      timeout: float | None = 30) -> Any:
        await self.start()
        return await self._request(method, params or {}, host_handler, timeout)

    async def _request(self, method: str, params: dict[str, Any], handler: Callable[..., Any] | None,
                       timeout: float | None, *, closing: bool = False) -> Any:
        try:
            # Queue time belongs to this caller's budget. Expiring while queued
            # must not cancel a different task that currently owns the process.
            async with asyncio.timeout(timeout) as deadline:
                async with self._request_lock:
                    if not self.running or (self._closing and not closing):
                        raise CordisWorkerError("The plugin worker is closed.")
                    self._sequence += 1
                    operation = _Operation(self._sequence, asyncio.get_running_loop().create_future(), handler)
                    operation.timeout = deadline
                    self._active = operation
                    try:
                        await self._send({"id": operation.identifier, "method": method, "params": params}, operation.budget)
                        return await operation.future
                    except asyncio.CancelledError:
                        self._abort(CordisWorkerError("The plugin operation timed out." if deadline.expired()
                                                      else "The plugin operation was cancelled."))
                        raise
                    except _ProtocolError as error:
                        self._abort(error)
                        raise
                    finally:
                        operation.revoked = True
                        operation.handler = None
                        await self._join_host_tasks(operation)
                        if self._active is operation:
                            self._active = None
                        if operation.future.done() and not operation.future.cancelled():
                            operation.future.exception()
                        if self._fault is not None:
                            await self._stop()
        except TimeoutError:
            raise CordisWorkerError("The plugin operation timed out.") from None

    async def _send(self, payload: dict[str, Any], budget: _Budget) -> None:
        data = _encoded(payload)
        budget.charge(len(data))
        async with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or process.returncode is not None:
                raise _ProtocolError("The plugin transport is closed.")
            try:
                process.stdin.write(data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError):
                raise _ProtocolError("The plugin transport is closed.") from None

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    if self._shutdown_requested:
                        return
                    raise _ProtocolError("The plugin runtime closed unexpectedly.")
                if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
                    raise _ProtocolError("The plugin runtime emitted an oversized or incomplete frame.")
                try:
                    frame = json.loads(line, parse_constant=_reject_constant, parse_float=_finite_float)
                except (ValueError, UnicodeError, RecursionError):
                    raise _ProtocolError("The plugin runtime returned invalid JSON.") from None
                if not isinstance(frame, dict):
                    raise _ProtocolError("The plugin runtime returned an invalid frame.")
                if "host_cancel_id" in frame:
                    identifier = frame["host_cancel_id"]
                    if set(frame) != {"host_cancel_id"} or type(identifier) not in {str, int}:
                        raise _ProtocolError("Invalid host cancellation frame.")
                    if self._active is not None:
                        self._active.budget.charge(len(line))
                    for task in self._host_tasks:
                        if getattr(task, "_cordis_host_id", None) == identifier and not task.done() and not task.cancelling():
                            task.cancel()
                    continue
                if "host_call_id" in frame:
                    self._accept_host(frame, len(line))
                    continue
                operation = self._active
                if operation is None or operation.future.done() or type(frame.get("id")) is not int or frame["id"] != operation.identifier:
                    raise _ProtocolError("The plugin runtime returned an unexpected response.")
                operation.budget.charge(len(line))
                if set(frame) not in ({"id", "result"}, {"id", "error"}):
                    raise _ProtocolError("The plugin runtime returned an invalid response.")
                if "error" in frame:
                    operation.future.set_exception(CordisWorkerError("The plugin runtime request failed."))
                else:
                    operation.future.set_result(frame["result"])
        except asyncio.CancelledError:
            raise
        except (ValueError, OSError, CordisWorkerError):
            self._abort(_ProtocolError("The plugin transport failed or exceeded its limits."))

    def _accept_host(self, frame: dict[str, Any], size: int) -> None:
        identifier = frame.get("host_call_id")
        if (type(identifier) not in {str, int}
                or isinstance(identifier, str) and not 1 <= len(identifier) <= 128
                or isinstance(identifier, int) and not 0 < identifier <= 2**53 - 1
                or set(frame) - {"host_call_id", "method", "params", "stream"}
                or not isinstance(frame.get("method"), str) or not 1 <= len(frame["method"]) <= 160
                or not isinstance(frame.get("params"), dict) or type(frame.get("stream", False)) is not bool
                or len(self._host_tasks) >= 64 or identifier in self._active_host_ids):
            raise _ProtocolError("The plugin runtime returned an invalid host request.")
        operation = self._active
        if operation is not None and (operation.revoked or operation.future.done() or operation.handler is None):
            operation = None
        handler = operation.handler if operation is not None else self.background_host_handler
        budget = operation.budget if operation is not None else _Budget()
        budget.charge(size)
        if identifier in budget.identifiers or len(budget.identifiers) >= MAX_HOST_REQUESTS:
            raise _ProtocolError("The plugin exceeded its host request limit.")
        budget.identifiers.add(identifier)
        task = asyncio.create_task(self._host_request(frame, handler, operation, budget), name="cordis-extension-host")
        task._cordis_host_id = identifier
        self._host_tasks[task] = operation
        self._active_host_ids.add(identifier)
        task.add_done_callback(lambda finished: self._host_done(finished, identifier))

    def _host_done(self, task: asyncio.Task[None], identifier: str | int) -> None:
        self._host_tasks.pop(task, None)
        self._active_host_ids.discard(identifier)
        if not task.cancelled():
            with contextlib.suppress(Exception):
                task.result()

    async def _host_request(self, frame: dict[str, Any], handler: Callable[..., Any] | None,
                            operation: _Operation | None, budget: _Budget) -> None:
        identifier = frame["host_call_id"]

        async def denied() -> None:
            await self._send({"host_call_id": identifier, "error": {"message": "Plugin host operation was denied or could not complete."}}, budget)

        async def dispatch(deadline: asyncio.Timeout | None) -> None:
            if handler is None or operation is not None and operation.revoked:
                await denied()
                return
            result = handler(frame["method"], frame["params"], stream=frame.get("stream", False), timeout=deadline)
            if inspect.isawaitable(result):
                result = await result
            if frame.get("stream"):
                iterator = result.__aiter__()
                try:
                    async for chunk in iterator:
                        if operation is not None and operation.revoked:
                            raise asyncio.CancelledError
                        await self._send({"host_call_id": identifier, "chunk": chunk}, budget)
                finally:
                    close = getattr(iterator, "aclose", None)
                    if close is not None:
                        await close()
                result = None
            if operation is not None and operation.revoked:
                raise asyncio.CancelledError
            await self._send({"host_call_id": identifier, "result": result}, budget)

        try:
            if operation is None:
                async with asyncio.timeout(30) as deadline:
                    await dispatch(deadline)
            else:
                await dispatch(operation.timeout)
        except asyncio.CancelledError:
            if self.running and not self._closing and self._fault is None:
                with contextlib.suppress(CordisWorkerError):
                    await denied()
            raise
        except _ProtocolError as error:
            self._abort(error)
        except Exception:
            try:
                await denied()
            except CordisWorkerError as error:
                self._abort(error)

    def _abort(self, error: CordisWorkerError) -> None:
        if self._fault is None:
            self._fault = error
        if self._active is not None:
            self._active.revoked = True
            if not self._active.future.done():
                self._active.future.set_exception(error)
        if self._process is not None and self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()
        current = asyncio.current_task()
        for task in self._host_tasks:
            if task is not current and not task.done() and not task.cancelling():
                task.cancel()
        if self._active is None and self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop(), name="cordis-extension-cleanup")

    async def _join_host_tasks(self, owner: _Operation | None = None, *, all_tasks: bool = False) -> None:
        current = asyncio.current_task()
        tasks = [task for task, operation in self._host_tasks.items()
                 if task is not current and (all_tasks or operation is owner)]
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop(self) -> None:
        self._closed = True
        await self._join_host_tasks(all_tasks=True)
        process = self._process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 0.5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if self._reader is not None and self._reader is not asyncio.current_task():
            if not self._reader.done():
                self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if process is not None and process.stdin is not None:
            process.stdin.close()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                await self._stop()
                return
            self._closing = True
            try:
                if self._active is not None:
                    self._abort(CordisWorkerError("The plugin worker was closed."))
                elif self.running:
                    self._shutdown_requested = True
                    with contextlib.suppress(CordisWorkerError, TimeoutError):
                        await self._request("shutdown", {}, None, 2, closing=True)
                    if self._process is not None:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(self._process.wait(), 0.5)
            finally:
                await self._stop()

    async def __aenter__(self) -> CordisWorker:
        await self.start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()
