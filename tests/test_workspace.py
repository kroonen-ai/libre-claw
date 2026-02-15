"""Tests for workspace management."""

import tempfile
from pathlib import Path

from libre_claw.workspace import Workspace
from libre_claw.config import Config


def test_workspace_init():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.init()

        assert (Path(tmpdir) / "SOUL.md").exists()
        assert (Path(tmpdir) / "AGENTS.md").exists()
        assert (Path(tmpdir) / "USER.md").exists()
        assert (Path(tmpdir) / "MEMORY.md").exists()
        assert (Path(tmpdir) / "HEARTBEAT.md").exists()
        assert (Path(tmpdir) / "memory").is_dir()


def test_workspace_read_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.ensure_exists()

        ws.write("test.md", "Hello World")
        content = ws.read("test.md")
        assert content == "Hello World"


def test_workspace_read_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        assert ws.read("nonexistent.md") is None


def test_workspace_append():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.ensure_exists()

        ws.write("log.md", "Line 1\n")
        ws.append("log.md", "Line 2\n")
        content = ws.read("log.md")
        assert "Line 1" in content
        assert "Line 2" in content


def test_workspace_context_direct():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.init()

        ctx = ws.get_context(mode="direct")
        assert "SOUL.md" in ctx
        assert "AGENTS.md" in ctx
        assert "MEMORY.md" in ctx
        # HEARTBEAT.md should NOT be in direct mode
        assert "HEARTBEAT.md" not in ctx


def test_workspace_context_heartbeat():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.init()

        ctx = ws.get_context(mode="heartbeat")
        assert "SOUL.md" in ctx
        assert "AGENTS.md" in ctx
        assert "HEARTBEAT.md" in ctx
        # MEMORY.md should NOT be in heartbeat mode
        assert "MEMORY.md" not in ctx


def test_workspace_list_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.init()

        files = ws.list_files("*.md")
        filenames = [f.name for f in files]
        assert "SOUL.md" in filenames


def test_workspace_daily_note():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.ensure_exists()

        ws.write_daily_note("Test entry")
        files = list(Path(tmpdir).glob("memory/*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "Test entry" in content


def test_workspace_heartbeat_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir)
        ws.ensure_exists()

        # Empty state
        state = ws.get_heartbeat_state()
        assert state == {}

        # Save and load
        ws.save_heartbeat_state({"last_check": "test", "count": 1})
        state = ws.get_heartbeat_state()
        assert state["count"] == 1
