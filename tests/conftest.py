# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Close resources owned by tests that call daemon helpers without AppRunner."""

import asyncio
import weakref

import pytest_asyncio

from libre_claw.core.cordis_engine import CordisEngine


@pytest_asyncio.fixture(autouse=True)
async def close_direct_test_engines(monkeypatch):
    # Production entrypoints own and close their engine. Some focused daemon
    # tests deliberately call private helpers without starting an application;
    # their resources still need to stop before pytest closes the owning loop.
    owner = asyncio.get_running_loop()
    engines = weakref.WeakSet()
    start = CordisEngine.start

    async def tracked(engine):
        if asyncio.get_running_loop() is owner:
            engines.add(engine)
        return await start(engine)

    monkeypatch.setattr(CordisEngine, "start", tracked)
    yield
    for engine in engines:
        await engine.aclose()
