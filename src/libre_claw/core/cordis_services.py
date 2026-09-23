# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Persistent, explicitly granted host services for reviewed Cordis adapters."""

from __future__ import annotations

import asyncio
import copy
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libre_claw.config import LibreClawConfig
from libre_claw.core.cordis import CordisError, CordisManager
from libre_claw.core.cordis_engine import CordisEngine
from libre_claw.core.cordis_ipc import CordisIpcError, CordisNativeProviderServer
from libre_claw.core.cordis_llm import CordisLlmBridge


def _manager(config: LibreClawConfig) -> CordisManager:
    return CordisManager(tool_timeout=config.cordis.tool_timeout, config=config)


class _EngineBridge:
    """Keep host service requests in the shared first-party Cordis graph."""

    def __init__(self, bridge: CordisLlmBridge, engine_getter: Callable[[], CordisEngine]) -> None:
        self.bridge = bridge
        self.engine_getter = engine_getter

    async def list_providers(self) -> list[dict[str, Any]]:
        return await self.engine_getter().call("providers", "models", handler=self.bridge.list_providers)

    async def list_models(self, provider: str) -> list[dict[str, Any]]:
        return await self.engine_getter().call("providers", "models", handler=lambda: self.bridge.list_models(provider))

    async def resolve_model_info(self, provider: str, model: str) -> dict[str, Any]:
        return await self.engine_getter().call("providers", "models", handler=lambda: self.bridge.resolve_model_info(provider, model))

    async def stream(self, options: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        async with contextlib.aclosing(self.engine_getter().stream("providers", "complete", handler=lambda: self.bridge.stream(options))) as source:
            async for chunk in source:
                yield chunk


@dataclass
class _Service:
    specification: dict[str, Any]
    providers: dict[str, Any]
    default_provider: str
    default_model: str
    server: CordisNativeProviderServer


class CordisServicePool:
    """Own native-provider listeners for enabled, immutable plugin snapshots.

    Construction has no I/O. ``sync`` starts only recognized host adapters with
    explicit model access, and closes disabled, replaced or changed services.
    ``force=True`` also refreshes unchanged services after credential changes.
    The authorization callback re-reads the registry and verifies source bytes
    on every operation/chunk; periodic reconciliation is not an access window.
    """

    def __init__(
        self,
        config_getter: Callable[[], LibreClawConfig],
        *,
        manager_factory: Callable[[LibreClawConfig], CordisManager] = _manager,
        bridge_factory: Callable[..., CordisLlmBridge] = CordisLlmBridge,
        engine_getter: Callable[[], CordisEngine] | None = None,
    ) -> None:
        self.config_getter = config_getter
        self.manager_factory = manager_factory
        self.bridge_factory = bridge_factory
        self.engine_getter = engine_getter
        self._services: dict[tuple[Path, str], _Service] = {}
        self._states: dict[tuple[Path, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def status(self, workspace: Path | str) -> list[dict[str, Any]]:
        """Return operational state only; no model settings or plugin config."""
        project = Path(workspace).resolve()
        result = []
        for key, state in sorted(self._states.items(), key=lambda row: row[0][1]):
            if key[0] != project:
                continue
            row = dict(state)
            if service := self._services.get(key):
                row.update(running=service.server.running, state="running" if service.server.running else "stopped",
                           active_requests=service.server.active_requests, socket_path=str(service.server.socket_path))
            result.append(row)
        return result

    def _authorized(self, key: tuple[Path, str], expected: _Service) -> bool:
        if self._closed:
            return False
        try:
            config = self.config_getter()
            if not config.cordis.enabled or not self._same_provider_config(expected, config):
                return False
            actual = self.manager_factory(config).host_service_spec(key[1], key[0])
            return actual.get("enabled") is True and actual == expected.specification
        except (CordisError, OSError, ValueError):
            return False

    @staticmethod
    def _same_provider_config(service: _Service, config: LibreClawConfig) -> bool:
        return (
            service.providers == config.providers
            and service.default_provider == config.general.default_provider
            and service.default_model == config.general.default_model
        )

    async def sync(self, workspace: Path | str, *, force: bool = False) -> list[dict[str, Any]]:
        project = Path(workspace).resolve()
        async with self._lock:
            if self._closed:
                raise CordisIpcError("The Cordis service pool is closed.")
            config = self.config_getter()
            wanted: dict[tuple[Path, str], dict[str, Any]] = {}
            states: dict[tuple[Path, str], dict[str, Any]] = {}
            try:
                manager = self.manager_factory(config)
                plugins = manager.list_plugins(project) if config.cordis.enabled else []
                for plugin in plugins:
                    if plugin.get("adapter") != "native-provider":
                        continue
                    key = (project, plugin["id"])
                    states[key] = {"plugin_id": plugin["id"], "adapter": "native-provider", "state": "disabled", "running": False, "active_requests": 0}
                    if not plugin.get("enabled"):
                        continue
                    try:
                        specification = manager.host_service_spec(plugin["id"], project)
                        if specification.get("adapter") != "native-provider" or specification.get("adapter_version") != 1:
                            raise CordisIpcError("The plugin host adapter is unsupported.")
                        if specification.get("enabled") is True:
                            wanted[key] = specification
                    except (CordisError, CordisIpcError) as exc:
                        states[key].update(state="error", error=str(exc))
            except (CordisError, OSError):
                states[(project, "registry")] = {"plugin_id": "registry", "state": "error", "running": False,
                                                  "active_requests": 0, "error": "The local plugin registry could not be read."}
            for key, service in list(self._services.items()):
                globally_invalid = force or not config.cordis.enabled or not self._same_provider_config(service, config)
                if key[0] != project and not globally_invalid:
                    continue
                if globally_invalid or not service.server.running or wanted.get(key) != service.specification:
                    await service.server.aclose()
                    del self._services[key]
                    if key in self._states:
                        self._states[key].update(state="stopped", running=False, active_requests=0)
            for key, specification in wanted.items():
                if key not in self._services:
                    try:
                        # The closure's entry is assigned before start() checks
                        # authorization; no callback can run with a partial grant.
                        holder: list[_Service] = []
                        authorize = lambda key=key, holder=holder: bool(holder) and self._authorized(key, holder[0])
                        bridge = self.bridge_factory(config, authorize=authorize)
                        effective_bridge = _EngineBridge(bridge, self.engine_getter) if self.engine_getter else bridge
                        options = specification["config"]
                        server = CordisNativeProviderServer(
                            options["socketPath"], effective_bridge,
                            request_timeout_ms=options["requestTimeoutMs"], max_concurrent_requests=options["maxConcurrentRequests"],
                        )
                        entry = _Service(copy.deepcopy(specification), copy.deepcopy(config.providers), config.general.default_provider, config.general.default_model, server)
                        holder.append(entry)
                        await server.start()
                        self._services[key] = entry
                    except (CordisError, CordisIpcError, PermissionError) as exc:
                        states[key].update(state="error", error=str(exc))
                    except Exception:
                        states[key].update(state="error", error="The native provider service could not start.")
                if key in self._services:
                    states[key].update(state="running", running=True)
            for key in list(self._states):
                if key[0] == project:
                    del self._states[key]
            self._states.update(states)
            return self.status(project)

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            for service in self._services.values():
                await service.server.aclose()
            self._services.clear()
            for state in self._states.values():
                state.update(state="stopped", running=False, active_requests=0)
