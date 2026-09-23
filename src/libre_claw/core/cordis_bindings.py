# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Explicit application bindings; storage implementations remain independently usable."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import aclosing, asynccontextmanager
from functools import wraps
from typing import Any

from libre_claw.core.cordis_engine import CordisEngine, CordisEngineError
from libre_claw.providers.base import LLMProvider, StreamEvent


RUN_METHODS = {
    "create_run": ("sessions", "create"),
    "append_event": ("sessions", "append"),
    "update_state": ("sessions", "save"),
    "finish_run": ("sessions", "save"),
    "list_runs": ("sessions", "list"),
    "load_run": ("sessions", "get"),
    "load_events": ("sessions", "history"),
    "save_session": ("sessions", "checkpoint"),
    "load_session": ("sessions", "load"),
    "set_workspace": ("sessions", "save"),
    "set_runtime": ("sessions", "save"),
    "queue_message": ("sessions", "append"),
    "queued_messages": ("sessions", "history"),
    "take_queued_message": ("sessions", "save"),
    "release_queued_message": ("sessions", "save"),
    "finish_turn": ("sessions", "save"),
}
MEMORY_METHODS = {
    "initialize": ("memory", "initialize"),
    "add_fact": ("memory", "remember"),
    "list_facts": ("memory", "load"),
    "forget_fact": ("memory", "delete"),
    "add_memory_item": ("memory", "remember"),
    "list_memory_items": ("memory", "load"),
    "list_always_injected_memories": ("memory", "retrieve"),
    "get_memory_item": ("memory", "load"),
    "search_memory_items": ("memory", "search"),
    "forget_memory_item": ("memory", "delete"),
    "memory_status": ("memory", "load"),
    "append_session_event": ("sessions", "append"),
    "load_session_events": ("sessions", "history"),
    "save_session": ("sessions", "save"),
    "load_session": ("sessions", "load"),
    "list_sessions": ("sessions", "list"),
    "log_file_edit": ("sessions", "append"),
    "list_file_edits": ("sessions", "history"),
}
AUTOMATION_METHODS = {
    "create": ("workflows", "create"),
    "list": ("workflows", "list"),
    "load": ("workflows", "get"),
    "update_status": ("workflows", "configure"),
    "update": ("workflows", "configure"),
    "update_global_model": ("workflows", "configure"),
    "delete": ("workflows", "delete"),
    "due": ("workflows", "list"),
    "mark_run": ("workflows", "save"),
}


class BoundStore:
    """Dispatch public async operations before entering their implementation.

    Internal implementation calls stay local: the outer operation already owns
    the service lifecycle, and storage locks never wait for a nested dispatch.
    A dynamic getter follows deliberate engine replacement. Engine failure is
    propagated; recovery callers must explicitly retain their own raw store.
    """

    def __init__(
        self, store: Any, engine_getter: Callable[[], CordisEngine],
        methods: Mapping[str, tuple[str, str]],
    ) -> None:
        self.implementation = store
        self._engine_getter = engine_getter
        self._methods = dict(methods)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.implementation, name)
        if name in {"close", "aclose"}:
            # Cleanup must remain callable after the service graph has stopped.
            return target
        operation = self._methods.get(name)
        if operation is None:
            if not name.startswith("_") and inspect.iscoroutinefunction(target):
                raise CordisEngineError(f"Storage operation {name} has no engine binding.")
            return target

        @wraps(target)
        async def dispatch(*args: Any, **kwargs: Any) -> Any:
            engine = self._engine_getter()
            if engine is None:
                raise CordisEngineError("The application storage engine is unavailable.")
            return await engine.call(*operation, handler=lambda: target(*args, **kwargs))

        return dispatch


@asynccontextmanager
async def provider_stream(
    provider: LLMProvider, *, engine: CordisEngine | None = None, **options: Any,
) -> AsyncIterator[AsyncIterator[StreamEvent]]:
    """Join provider cleanup on errors, cancellation, or an early consumer exit."""
    source = (
        engine.stream("providers", "complete", handler=lambda: provider.complete(**options))
        if engine is not None else provider.complete(**options)
    )
    async with aclosing(source):
        yield source
