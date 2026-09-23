# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Workspace team profiles and model-route checks without inference."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aiohttp import web

from libre_claw.core.cordis_llm import CordisLlmBridge
from libre_claw.core.memory import redact_secrets
from libre_claw.core.orchestration import resolve_profile
from libre_claw.providers.factory import orchestration_provider_config
from libre_claw.providers.model_catalog import discover_models, selected_model_info

if TYPE_CHECKING:
    from libre_claw.daemon import DaemonServer


class OrchestrationAPI:
    def __init__(self, server: DaemonServer, local_request: Callable[[web.Request], bool]) -> None:
        self.server = server
        self.local_request = local_request

    def routes(self) -> list[web.RouteDef]:
        return [web.get("/providers", self.providers), web.get("/orchestration", self.profiles),
                web.post("/orchestration/check", self.check)]

    def _check_local(self, request: web.Request) -> None:
        if not self.local_request(request):
            raise web.HTTPForbidden(text='{"error":"Team profiles require this local dashboard."}',
                                    content_type="application/json")

    async def providers(self, request: web.Request) -> web.Response:
        self._check_local(request)
        bridge = CordisLlmBridge(self.server.config, authorize=lambda: True)
        providers = await bridge.list_providers()
        return web.json_response({"providers": [{**row, "label": row["name"]} for row in providers],
            "limitations": ["Native coding providers such as Codex run their own tools and cannot enforce scoped team delegation."]})

    def _profile(self) -> tuple[dict[str, Any], dict[str, Any]]:
        config = self.server.config
        spec = self.server.cordis_manager.orchestration_profile(config.general.working_directory)
        resolved = resolve_profile(spec["config"], default_provider=config.general.default_provider,
                                   default_model=config.general.default_model)
        for route in [resolved["orchestrator"], *resolved["workers"]]:
            orchestration_provider_config(config, route["provider"], route["model"], route)
        return spec, resolved

    async def profiles(self, request: web.Request) -> web.Response:
        self._check_local(request)
        rows = []
        try:
            installed = await asyncio.to_thread(self.server.cordis_manager.list_plugins,
                                                 self.server.config.general.working_directory)
            plugin = next((item for item in installed if item["id"] == "orchestration"), None)
            if plugin is not None:
                row = {"plugin_id": "orchestration", "name": plugin["name"],
                       "enabled": plugin.get("enabled", False), "ready": False,
                       "orchestrator": {}, "workers": [], "limits": {}}
                try:
                    _, profile = await asyncio.to_thread(self._profile)
                    row.update(ready=True, orchestrator=profile["orchestrator"], workers=profile["workers"],
                               limits={key: profile[key] for key in ("max_concurrent", "max_total_workers")})
                except (ValueError, RuntimeError) as error:
                    row["error"] = redact_secrets(str(error))
                rows.append(row)
        except (ValueError, RuntimeError, OSError) as error:
            return web.json_response({"error": redact_secrets(str(error))}, status=400)
        return web.json_response({"profiles": rows, "default_plugin": ""})

    async def check(self, request: web.Request) -> web.Response:
        self._check_local(request)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text='{"error":"Use application/json."}', content_type="application/json")
        try:
            payload = await request.json()
            if payload != {"plugin_id": "orchestration"}:
                raise ValueError("Select the installed orchestration plugin.")
            spec, profile = await asyncio.to_thread(self._profile)
        except (ValueError, RuntimeError, OSError) as error:
            return web.json_response({"ready": False, "error": redact_secrets(str(error)), "warnings": []}, status=400)
        config = self.server.config
        routes, warnings, errors = [], [], []
        selections = [("orchestrator", profile["orchestrator"]), *[(item["id"], item) for item in profile["workers"]]]
        for worker_id, selection in selections:
            provider = None
            row = {"worker_id": worker_id, "provider": selection["provider"], "model": selection["model"], "ready": False}
            try:
                spec["authorize"]()
                route_config = orchestration_provider_config(config, selection["provider"], selection["model"], selection)
                provider = await asyncio.to_thread(self.server.provider_factory, route_config)
                if hasattr(provider, "sandbox") or hasattr(provider, "approval_policy"):
                    raise ValueError("Select a client-tool provider for scoped orchestration.")
                catalog = await discover_models(route_config, selection["provider"])
                info = selected_model_info(route_config, selection["provider"], selection["model"])
                if info.supports_tools is False:
                    raise ValueError("The selected model does not support tool calls.")
                effort = selection["reasoning_effort"]
                if effort and (info.supports_reasoning is False or
                        (info.supported_reasoning_efforts is not None and effort not in info.supported_reasoning_efforts)):
                    raise ValueError("The selected reasoning effort is not supported by this model.")
                if catalog.error and re.search(r"\b(?:401|403|unauthori[sz]ed|forbidden)\b", catalog.error, re.IGNORECASE):
                    raise ValueError("Provider denied catalog access. Check its credentials or catalog permissions; no inference was attempted.")
                if catalog.error:
                    warnings.append(f"{worker_id}: model catalog unavailable; the explicitly selected model was retained.")
                elif not any(model.model == selection["model"] for model in catalog.models):
                    warnings.append(f"{worker_id}: custom model ID is not listed in the current catalog.")
                row.update(ready=True, catalog_source=catalog.source)
                spec["authorize"]()
            except (ValueError, RuntimeError, OSError) as error:
                row.update(ready=False, error=redact_secrets(str(error)))
                errors.append(f"{worker_id}: {row['error']}")
            finally:
                client = getattr(provider, "_client", None)
                close = getattr(client, "close", None) or getattr(client, "aclose", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            routes.append(row)
        return web.json_response({"plugin_id": "orchestration", "ready": not errors, "routes": routes,
                                  "error": "\n".join(errors), "warnings": warnings, "inference_requests": 0})
