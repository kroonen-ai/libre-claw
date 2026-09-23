# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Private native-provider IPC without granting Node network access.

Node's offline permission scope also rejects Unix sockets. This first-party
transport implements Libre WebUI's native-provider HTTP protocol in the host,
delegating only explicit model requests to a capability-gated CordisLlmBridge.
It binds no IP ports and does not load or execute extension JavaScript.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import stat
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web

from libre_claw.core.cordis_llm import (
    MAX_CHUNK_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    CordisLlmBridge,
    CordisLlmError,
    _json,
    _request,
)


NATIVE_PROVIDER_INSTANCE_HEADER = "x-native-provider-instance"


class CordisIpcError(ValueError):
    """The private IPC transport could not be safely configured."""


class _ProtocolError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "NATIVE_PROVIDER_STREAM_FAILED") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _validate_socket_path(value: Path | str) -> Path:
    raw = os.fspath(value)
    if (
        not isinstance(raw, str) or not raw.startswith(os.sep) or raw.startswith(os.sep * 2)
        or os.path.normpath(raw) != raw or len(os.fsencode(raw)) > 100
        or "\0" in raw or not Path(raw).name
    ):
        raise CordisIpcError("The native provider requires an absolute canonical Unix socket path of at most 100 bytes.")
    return Path(raw)


def validate_native_config(
    value: Any, *, state_dir: Path, write_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Normalize a reviewed native-provider config without filesystem access.

    Relative DSH state markers stay inside the adapter's compact private IPC
    directory. An absolute custom socket requires an explicit write grant when
    it is outside that directory. This does not grant Node any IPC or network
    permission; the host owns the listener.
    """
    if type(value) is not dict or set(value) - {"socketPath", "requestTimeoutMs", "maxConcurrentRequests"}:
        raise CordisIpcError("Invalid native provider configuration fields.")
    configured = value.get("socketPath")
    root = Path(state_dir)
    if not root.is_absolute() or os.path.normpath(str(root)) != str(root):
        raise CordisIpcError("The native provider state directory must be absolute and canonical.")
    if type(configured) is dict and set(configured) == {"$libreStatePath"}:
        relative = configured["$libreStatePath"]
        if type(relative) is not str or not relative or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise CordisIpcError("The native provider state path must remain inside its private directory.")
        configured = str(root / relative)
    if type(configured) is not str:
        raise CordisIpcError("The native provider requires a socketPath.")
    path = _validate_socket_path(configured)
    if not any(path == grant or grant in path.parents for grant in (root, *map(Path, write_paths))):
        raise CordisIpcError("A native provider socket outside private state requires an explicit write grant.")
    timeout = value.get("requestTimeoutMs", 600_000)
    maximum = value.get("maxConcurrentRequests", 8)
    if type(timeout) is not int or not 10 <= timeout <= 3_600_000:
        raise CordisIpcError("Invalid native provider request timeout.")
    if type(maximum) is not int or not 1 <= maximum <= 64:
        raise CordisIpcError("Invalid native provider request capacity.")
    return {"socketPath": str(path), "requestTimeoutMs": timeout, "maxConcurrentRequests": maximum}


def _prepare_parent(socket_path: Path) -> None:
    """Walk physical directories without following any symlink, then check mode."""
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        raise CordisIpcError("Native provider IPC requires a POSIX host with Unix sockets.")
    parent = socket_path.parent
    descriptor = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for segment in parent.parts[1:]:
            try:
                child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(segment, mode=0o700, dir_fd=descriptor)
                child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CordisIpcError("The native provider socket directory must be owned by the current user and mode 0700.")
        try:
            os.stat(socket_path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise CordisIpcError("The native provider socket path already exists; refusing to replace it.")
    except OSError:
        raise CordisIpcError("The native provider socket directory must be a physical directory without symbolic links.") from None
    finally:
        os.close(descriptor)


class CordisNativeProviderServer:
    """Serve one explicitly authorized model bridge over a private Unix socket.

    The owner must close the server when its plugin is disabled, and call
    ``rotate`` when provider configuration or credential references change.
    The bridge checks model-access authorization on every request and stream
    event. The socket is accessible only to the current OS user.
    """

    def __init__(
        self,
        socket_path: Path | str,
        bridge: CordisLlmBridge,
        *,
        request_timeout_ms: int = 600_000,
        max_concurrent_requests: int = 8,
    ) -> None:
        self.socket_path = _validate_socket_path(socket_path)
        if type(request_timeout_ms) is not int or not 10 <= request_timeout_ms <= 3_600_000:
            raise CordisIpcError("Native provider request timeout must be between 10 and 3,600,000 milliseconds.")
        if type(max_concurrent_requests) is not int or not 1 <= max_concurrent_requests <= 64:
            raise CordisIpcError("Native provider request capacity must be between 1 and 64.")
        self.bridge = bridge
        self.request_timeout_ms = request_timeout_ms
        self.max_concurrent_requests = max_concurrent_requests
        self.instance_id = str(uuid.uuid4())
        self._runner: web.AppRunner | None = None
        self._identity: tuple[int, int] | None = None
        self._active: set[asyncio.Task[Any]] = set()
        self._cancellations: dict[asyncio.Task[Any], _ProtocolError] = {}
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._stopping

    @property
    def active_requests(self) -> int:
        return len(self._active)

    async def __aenter__(self) -> CordisNativeProviderServer:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        if self.running:
            return
        if self._stopping:
            raise CordisIpcError("The native provider transport is closed.")
        # Authorization is checked before creating a socket or listing models.
        await self.bridge.list_providers()
        _prepare_parent(self.socket_path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Binding our own socket avoids asyncio's automatic stale-socket
            # unlink, so an existing user's path is never silently replaced.
            listener.bind(str(self.socket_path))
            info = self.socket_path.lstat()
            self._identity = (info.st_dev, info.st_ino)
            os.chmod(self.socket_path, 0o600, follow_symlinks=False)
            info = self.socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise CordisIpcError("Native provider socket permissions are invalid.")
            listener.setblocking(False)
            app = web.Application(client_max_size=MAX_REQUEST_BYTES)
            app.router.add_route("*", "/{path:.*}", self._handle)
            self._runner = web.AppRunner(
                app, access_log=None, handler_cancellation=True, auto_decompress=False,
                shutdown_timeout=2, keepalive_timeout=15,
            )
            await self._runner.setup()
            await web.SockSite(self._runner, listener).start()
        except BaseException:
            listener.close()
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None
            self._unlink_owned_socket()
            raise

    async def rotate(self) -> str:
        """Invalidate cached routes and abort all requests using the old catalog."""
        self.instance_id = str(uuid.uuid4())
        reason = _ProtocolError("Native provider configuration changed; refresh its catalog.", 409, "NATIVE_PROVIDER_CHANGED")
        await self._cancel_requests(reason)
        return self.instance_id

    async def _cancel_requests(self, reason: _ProtocolError) -> None:
        tasks = list(self._active)
        for task in tasks:
            self._cancellations[task] = reason
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        self._stopping = True
        await self._cancel_requests(_ProtocolError("Native provider transport stopped.", 503))
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._unlink_owned_socket()

    def _unlink_owned_socket(self) -> None:
        try:
            current = self.socket_path.lstat()
        except FileNotFoundError:
            self._identity = None
            return
        if self._identity == (current.st_dev, current.st_ino) and stat.S_ISSOCK(current.st_mode):
            self.socket_path.unlink()
        self._identity = None

    async def _body(self, request: web.Request) -> Any:
        if request.content_type != "application/json" or "Content-Encoding" in request.headers:
            raise _ProtocolError("Native provider requests require unencoded application/json.", 415)
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            raise _ProtocolError("Native provider request exceeds its byte limit.", 413)
        data = bytearray()
        async for chunk in request.content.iter_chunked(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_REQUEST_BYTES:
                raise _ProtocolError("Native provider request exceeds its byte limit.", 413)
        try:
            return json.loads(data.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError, RecursionError):
            raise _ProtocolError("Native provider request must be valid UTF-8 JSON.") from None

    async def _catalog(self, generation: str, selection: tuple[str, str] | None = None) -> dict[str, Any]:
        models = []
        seen = set()
        for provider in await self.bridge.list_providers():
            if selection is not None and provider["id"] != selection[0]:
                continue
            try:
                entries = await self.bridge.list_models(provider["id"])
                for entry in entries:
                    if selection is not None and entry["id"] != selection[1]:
                        continue
                    resolved = await self.bridge.resolve_model_info(provider["id"], entry["id"])
                    identity = (provider["id"], entry["id"])
                    if identity in seen:
                        raise _ProtocolError("Native provider catalog contains a duplicate model.", 502)
                    seen.add(identity)
                    model = {"providerId": provider["id"], "providerName": provider["name"], "model": entry["id"], "name": entry["name"]}
                    if window := resolved.get("context", {}).get("contextWindow"):
                        model["contextWindow"] = window
                    for key in ("defaultMaxTokens", "reasoning"):
                        if key in resolved:
                            model[key] = resolved[key]
                    models.append(model)
                    if len(models) > 10_000:
                        raise _ProtocolError("Native provider catalog exceeds its model limit.", 413)
            except CordisLlmError:
                # Match the native plugin: one unavailable route does not hide
                # healthy provider catalogs. Never emit provider diagnostics.
                continue
        if generation != self.instance_id:
            raise _ProtocolError("Native provider configuration changed; refresh its catalog.", 409, "NATIVE_PROVIDER_CHANGED")
        result = {"instanceId": generation, "models": models}
        _json(result, MAX_CHUNK_BYTES)
        return result

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        if self._stopping or len(self._active) >= self.max_concurrent_requests:
            return self._error(_ProtocolError("Native provider request capacity is unavailable.", 503))
        task = asyncio.current_task()
        assert task is not None
        self._active.add(task)
        response: web.StreamResponse | None = None
        generation = self.instance_id
        sent = 0
        finished = False
        try:
            async with asyncio.timeout(self.request_timeout_ms / 1000):
                if request.method != "POST" or request.raw_path not in {"/catalog", "/generate"}:
                    raise _ProtocolError("Unknown native provider operation.", 404)
                body = await self._body(request)
                if request.path == "/catalog":
                    if type(body) is not dict or body:
                        raise _ProtocolError("Native provider catalog request must be an empty object.")
                    return web.json_response(await self._catalog(generation), headers={
                        NATIVE_PROVIDER_INSTANCE_HEADER: generation, "Cache-Control": "no-store",
                    })
                if type(body) is not dict:
                    raise _ProtocolError("Invalid native provider request.")
                selected_instance = body.pop("instanceId", None)
                if selected_instance is not None and (type(selected_instance) is not str or not selected_instance.strip() or len(selected_instance) > 128):
                    raise _ProtocolError("Invalid native provider instance ID.")
                if selected_instance is not None and selected_instance != generation:
                    raise _ProtocolError("Native provider configuration changed; refresh its catalog.", 409, "NATIVE_PROVIDER_CHANGED")
                if request.headers.get(NATIVE_PROVIDER_INSTANCE_HEADER, generation) != generation:
                    raise _ProtocolError("Native provider configuration changed; refresh its catalog.", 409, "NATIVE_PROVIDER_CHANGED")
                options, _, _ = _request(body)
                available = await self._catalog(generation, (options["provider"], options["model"]))
                if not available["models"]:
                    raise _ProtocolError("The selected native provider model is unavailable.", 422)
                response = web.StreamResponse(headers={
                    "Content-Type": "application/x-ndjson", NATIVE_PROVIDER_INSTANCE_HEADER: generation,
                    "Cache-Control": "no-store",
                })
                await response.prepare(request)
                async with contextlib.aclosing(self.bridge.stream(options)) as stream:
                    async for chunk in stream:
                        if generation != self.instance_id:
                            raise _ProtocolError("Native provider configuration changed; refresh its catalog.", 409, "NATIVE_PROVIDER_CHANGED")
                        encoded = (_json(chunk, MAX_CHUNK_BYTES) + "\n").encode("utf-8")
                        sent += len(encoded)
                        if sent > MAX_RESPONSE_BYTES:
                            raise _ProtocolError("Native provider response exceeds its byte limit.", 413)
                        await response.write(encoded)
                        if chunk.get("type") == "finish":
                            finished = True
                            break
                if not finished:
                    raise _ProtocolError("Native provider ended without a terminal finish.", 502)
                await response.write_eof()
                return response
        except asyncio.CancelledError:
            reason = self._cancellations.get(task)
            if reason is None:
                raise
            return await self._failure_response(response, reason, finished)
        except TimeoutError:
            return await self._failure_response(response, _ProtocolError("Native provider request timed out.", 504), finished)
        except PermissionError:
            return await self._failure_response(response, _ProtocolError("Plugin model access has not been granted.", 403), finished)
        except CordisLlmError as exc:
            return await self._failure_response(response, _ProtocolError(str(exc)), finished)
        except _ProtocolError as exc:
            return await self._failure_response(response, exc, finished)
        except (ConnectionError, BrokenPipeError):
            raise
        except Exception:
            return await self._failure_response(response, _ProtocolError("Native provider request failed.", 502), finished)
        finally:
            self._active.discard(task)
            self._cancellations.pop(task, None)

    @staticmethod
    def _error(error: _ProtocolError) -> web.Response:
        return web.json_response({"error": str(error)}, status=error.status, headers={"Cache-Control": "no-store", "Connection": "close"})

    async def _failure_response(self, response: web.StreamResponse | None, error: _ProtocolError, finished: bool) -> web.StreamResponse:
        if response is None or not response.prepared:
            return self._error(error)
        if not finished:
            chunk = {"type": "finish", "reason": {"kind": "error", "failure": {"code": error.code, "message": str(error)}}}
            with contextlib.suppress(ConnectionError, RuntimeError):
                await response.write((json.dumps(chunk, separators=(",", ":")) + "\n").encode())
                await response.write_eof()
        return response
