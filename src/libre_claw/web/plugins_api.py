# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Local plugin installation, configuration, and runtime checks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aiohttp import web

from libre_claw.core.cordis_packages import CordisPackagePreviews

if TYPE_CHECKING:
    from libre_claw.daemon import DaemonServer


class PluginAPI:
    def __init__(self, server: DaemonServer, local_request: Callable[[web.Request], bool]) -> None:
        self.server = server
        self.local_request = local_request
        self._previews: CordisPackagePreviews | None = None
        self._lock = asyncio.Lock()

    @property
    def previews(self) -> CordisPackagePreviews:
        if self._previews is None:
            self._previews = CordisPackagePreviews(self.server.cordis_manager)
        return self._previews

    def routes(self) -> list[web.RouteDef]:
        return [
            web.post("/plugins/preview", self.preview),
            web.delete("/plugins/preview/{token}", self.discard),
            web.post("/plugins/install", self.install),
            web.get("/plugins/{plugin_id}", self.details),
            web.put("/plugins/{plugin_id}/config", self.configure),
            web.post("/plugins/{plugin_id}/inspect", self.inspect),
            web.delete("/plugins/{plugin_id}", self.remove),
        ]

    def _check(self, request: web.Request, *, json_body: bool = False) -> None:
        if not self.local_request(request):
            raise web.HTTPForbidden(text='{"error":"Plugin management requires this local dashboard."}',
                                    content_type="application/json")
        if json_body and request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text='{"error":"Use application/json for plugin changes."}',
                                              content_type="application/json")

    async def _body(self, request: web.Request, fields: set[str]) -> dict[str, Any]:
        self._check(request, json_body=True)
        value = await request.json()
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Send only these fields: " + ", ".join(sorted(fields)))
        return value

    async def _write(self, operation: Callable[..., Any], *args: Any) -> Any:
        # Finish a registry transaction before cancellation can remove staging.
        async with self._lock:
            task = asyncio.create_task(asyncio.to_thread(operation, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def close(self) -> None:
        async with self._lock:
            if self._previews is not None:
                await asyncio.to_thread(self._previews.close)

    async def preview(self, request: web.Request) -> web.Response:
        try:
            data = await self._body(request, {"source"})
            if not isinstance(data["source"], str) or not data["source"].strip():
                raise ValueError("Enter a plugin folder, archive, or npm package.")
            async with self._lock:
                result = await self.previews.preview(data["source"], self.server.config.general.working_directory)
            return response(result)
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def discard(self, request: web.Request) -> web.Response:
        self._check(request)
        try:
            await self._write(self.previews.discard, request.match_info["token"],
                              self.server.config.general.working_directory)
            return response({"discarded": True})
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def install(self, request: web.Request) -> web.Response:
        try:
            data = await self._body(request, {"token"})
            if not isinstance(data["token"], str):
                raise ValueError("Check a package before installing it.")
            plugin = await self._write(self.previews.install, data["token"],
                                       self.server.config.general.working_directory)
            await self.server.reconcile_plugins()
            return response({"plugin": plugin})
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def details(self, request: web.Request) -> web.Response:
        self._check(request)
        try:
            plugin = await asyncio.to_thread(self.server.cordis_manager.details,
                request.match_info["plugin_id"], self.server.config.general.working_directory)
            service = next((item for item in self.server.plugin_services.status(self.server.config.general.working_directory)
                            if item["plugin_id"] == plugin["id"]), None)
            if service is not None:
                plugin["service"] = service
            return response({"plugin": plugin})
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def configure(self, request: web.Request) -> web.Response:
        try:
            data = await self._body(request, {"config"})
            if not isinstance(data["config"], dict):
                raise ValueError("Plugin configuration must be a JSON object.")
            async with self._lock:
                plugin = await self.server.cordis_manager.configure_async(
                    request.match_info["plugin_id"], self.server.config.general.working_directory, data["config"])
                await self.server.reconcile_plugins()
            return response({"plugin": plugin})
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def inspect(self, request: web.Request) -> web.Response:
        try:
            await self._body(request, set())
            if not self.server.config.cordis.enabled:
                raise ValueError("Cordis is disabled in configuration.")
            async with self._lock:
                plugin = await self.server.cordis_manager.inspect(request.match_info["plugin_id"],
                    self.server.config.general.working_directory)
                await self.server.reconcile_plugins()
                service = next((item for item in self.server.plugin_services.status(self.server.config.general.working_directory)
                                if item["plugin_id"] == request.match_info["plugin_id"]), None)
                if service is not None:
                    plugin["service"] = service
                    plugin["state"] = service["state"].upper()
            return response({"plugin": plugin})
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def remove(self, request: web.Request) -> web.Response:
        self._check(request)
        try:
            async with self._lock:
                result = await self.server.cordis_manager.remove_async(request.match_info["plugin_id"])
                await self.server.reconcile_plugins()
            return response(result)
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)


def response(payload: dict[str, Any], *, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


def error(exc: Exception) -> web.Response:
    return response({"error": str(exc)}, status=400)
