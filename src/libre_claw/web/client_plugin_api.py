# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Local render-tree transport; no third-party JavaScript enters this origin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web

from libre_claw.core.cordis_client import CordisClientPool
from libre_claw.web.plugins_api import error, response


class ClientPluginAPI:
    def __init__(self, server, local_request) -> None:
        self.server = server
        self.local_request = local_request
        self.pool = CordisClientPool(server.cordis_manager)

    def routes(self) -> list[web.RouteDef]:
        prefix = "/plugins/{plugin_id}/ui"
        return [web.post(prefix + "/open", self.open),
                web.get(prefix + "/{session_id}", self.snapshot),
                web.post(prefix + "/{session_id}/events", self.event),
                web.delete(prefix + "/{session_id}", self.dispose)]

    def _check(self, request: web.Request, *, json_body: bool = False) -> None:
        if not self.local_request(request):
            raise web.HTTPForbidden(text='{"error":"Client interfaces require this local dashboard."}',
                                    content_type="application/json")
        if json_body and request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text='{"error":"Use application/json for client events."}',
                                              content_type="application/json")

    async def open(self, request: web.Request) -> web.Response:
        self._check(request, json_body=True)
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {"run_id"}:
                raise ValueError("Send an empty object or an explicit run_id.")
            workspace = self.server.config.general.working_directory
            context = None
            if "run_id" in body:
                if not isinstance(body["run_id"], str) or not 1 <= len(body["run_id"]) <= 256:
                    raise ValueError("Invalid task identity.")
                run = await self.server.run_store.load_run(body["run_id"])
                if run is None:
                    raise ValueError("The selected task does not exist.")
                if Path(run.working_directory).resolve() != Path(workspace).resolve():
                    raise ValueError("The client interface and selected task must belong to the same workspace.")
                context = {"run_id": run.run_id, "state": run.state}
            value = await self.server.engine.call("workflows", "run", handler=lambda: self.pool.open(
                request.match_info["plugin_id"], Path(workspace), context=context))
            return response(value)
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def snapshot(self, request: web.Request) -> web.Response:
        self._check(request)
        return await self._request(request)

    async def event(self, request: web.Request) -> web.Response:
        self._check(request, json_body=True)
        try:
            body = await request.json()
        except ValueError as exc:
            return error(exc)
        if not isinstance(body, dict):
            return error(ValueError("Client events must be JSON objects."))
        return await self._request(request, body)

    async def _request(self, request: web.Request, event: Any = None) -> web.Response:
        try:
            value = await self.server.engine.call("workflows", "run", handler=lambda: self.pool.request(
                request.match_info["plugin_id"], request.match_info["session_id"], event))
            return response(value)
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def dispose(self, request: web.Request) -> web.Response:
        self._check(request)
        try:
            # Disposal is a recovery control and stays available if core stops.
            await self.pool.close(request.match_info["plugin_id"], request.match_info["session_id"])
            return response({"closed": True})
        except (ValueError, OSError, RuntimeError) as exc:
            return error(exc)

    async def close(self) -> None:
        await self.pool.aclose()
