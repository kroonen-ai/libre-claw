# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import libre_claw.auth.codex as codex_auth


async def test_codex_status_reports_missing_cli(monkeypatch) -> None:
    monkeypatch.setattr(codex_auth, "codex_available", lambda executable="codex": False)

    status = await codex_auth.codex_status()

    assert status.available is False
    assert status.logged_in is False
    assert "not installed" in status.detail


async def test_codex_status_reports_chatgpt_login(monkeypatch) -> None:
    async def fake_run(args, input_text=None, timeout=None):  # noqa: ANN001
        del input_text, timeout
        return codex_auth.CodexCommandResult(args=tuple(args), exit_code=0, stdout="Logged in using ChatGPT\n", stderr="")

    monkeypatch.setattr(codex_auth, "codex_available", lambda executable="codex": True)
    monkeypatch.setattr(codex_auth, "run_codex_command", fake_run)

    status = await codex_auth.codex_status()

    assert status.available is True
    assert status.logged_in is True
    assert "ChatGPT" in status.detail


async def test_stream_cancellation_terminates_cli_and_owned_children(tmp_path) -> None:
    import asyncio
    import os
    import sys

    import pytest
    if os.name != "posix":
        pytest.skip("Process-group cleanup uses POSIX process groups")
    marker = tmp_path / "orphan-wrote.txt"
    child = f"import pathlib,time; time.sleep(0.35); pathlib.Path({str(marker)!r}).write_text('orphan')"
    script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "print('ready',flush=True); time.sleep(30)"
    )
    stream = codex_auth.stream_codex_command([sys.executable, "-u", "-c", script])
    assert (await anext(stream)).text.strip() == "ready"
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await asyncio.sleep(0.45)
    assert not marker.exists()


async def test_closing_stream_terminates_cli_before_later_side_effect(tmp_path) -> None:
    import asyncio
    import sys
    marker = tmp_path / "late-write.txt"
    script = (
        "import pathlib,time; print('ready',flush=True); time.sleep(0.2); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    stream = codex_auth.stream_codex_command([sys.executable, "-u", "-c", script])
    assert (await anext(stream)).text.strip() == "ready"
    await stream.aclose()
    await asyncio.sleep(0.3)
    assert not marker.exists()


async def test_nonstream_timeout_terminates_owned_processes(tmp_path) -> None:
    import asyncio
    import sys
    marker = tmp_path / "late-write.txt"
    script = (
        "import pathlib,time; time.sleep(0.2); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    result = await codex_auth.run_codex_command([sys.executable, "-c", script], timeout=0.03)
    assert result.exit_code == 124
    await asyncio.sleep(0.3)
    assert not marker.exists()


async def test_nonstream_cancellation_terminates_cli(tmp_path) -> None:
    import asyncio
    import sys
    import pytest
    ready = tmp_path / "ready"
    marker = tmp_path / "late-write.txt"
    script = (
        f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready'); "
        f"time.sleep(0.3); pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    task = asyncio.create_task(codex_auth.run_codex_command([sys.executable, "-c", script]))
    async with asyncio.timeout(2):
        while not ready.exists():
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.4)
    assert not marker.exists()


async def test_timeout_stops_child_even_after_cli_leader_has_exited(tmp_path) -> None:
    import asyncio
    import os
    import sys
    import pytest
    if os.name != "posix":
        pytest.skip("Process-group cleanup uses POSIX process groups")
    marker = tmp_path / "orphan-write.txt"
    child = f"import pathlib,time; time.sleep(0.3); pathlib.Path({str(marker)!r}).write_text('orphan')"
    script = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    result = await codex_auth.run_codex_command([sys.executable, "-c", script], timeout=0.05)
    assert result.exit_code == 124
    await asyncio.sleep(0.4)
    assert not marker.exists()
