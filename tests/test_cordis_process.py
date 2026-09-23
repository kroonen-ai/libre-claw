# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import gc
import sys

import pytest

from libre_claw.core.cordis_engine import CordisEngine
from libre_claw.core.cordis_security import CordisProcess
from libre_claw.core.cordis_worker import CordisWorker


@pytest.mark.parametrize("owner", ["engine", "worker"])
def test_exited_runtime_with_paused_output_closes_pipes_before_loop_disposal(tmp_path, owner):
    async def exercise():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import sys; sys.stdout.write('x' * 1048576); sys.stdout.flush()",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=1024,
        )
        if owner == "engine":
            runtime = CordisEngine()
            runtime._state = "failed"
        else:
            runtime = CordisWorker(CordisProcess((), {}, tmp_path, "test"), {})
            runtime._closed = True
        runtime._process = process
        try:
            async with asyncio.timeout(5):
                while not process.stdout._paused:
                    await asyncio.sleep(0.01)
                process.kill()
                # Deliberately let the exit notification arrive first. wait()
                # now returns immediately while the paused pipe is still open.
                while process.returncode is None:
                    await asyncio.sleep(0.01)
                await runtime.aclose()
            assert process.stdin.is_closing()
            assert process.stdout.at_eof()
            assert process.stderr.at_eof()
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()

    asyncio.run(exercise())
    gc.collect()
