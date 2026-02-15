"""Tests for the Agent class."""

import tempfile
from unittest.mock import MagicMock

from libre_claw.agent import Agent, AgentMode
from libre_claw.backends.base import BaseBackend, BackendConfig, Message, Response
from libre_claw.config import Config
from libre_claw.workspace import Workspace


class MockBackend(BaseBackend):
    """Mock backend for testing."""

    def __init__(self, response_text: str = "Mock response"):
        super().__init__()
        self.response_text = response_text
        self.last_prompt = None
        self.last_system_prompt = None
        self.call_count = 0

    @property
    def name(self) -> str:
        return "mock"

    def complete(self, prompt, system_prompt=None, context=None, tools=None):
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt
        self.call_count += 1
        return Response(content=self.response_text, model="mock")

    def chat(self, messages, tools=None):
        self.call_count += 1
        return Response(content=self.response_text, model="mock")


def test_agent_creation():
    backend = MockBackend()
    config = Config()
    config.memory.enabled = False

    agent = Agent(backend=backend, config=config)
    assert agent.backend.name == "mock"
    assert agent.state.mode == AgentMode.DIRECT


def test_agent_handle_message():
    backend = MockBackend("Hello!")
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        agent = Agent(backend=backend, workspace=ws, config=config)
        response = agent.handle_message("Hi")

        assert response == "Hello!"
        assert backend.call_count == 1
        assert agent.state.mode == AgentMode.DIRECT
        assert agent.state.message_count == 1


def test_agent_heartbeat():
    backend = MockBackend("HEARTBEAT_OK")
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        agent = Agent(backend=backend, workspace=ws, config=config)
        response = agent.heartbeat_tick()

        assert response == "HEARTBEAT_OK"
        assert agent.state.mode == AgentMode.HEARTBEAT


def test_agent_mode_switching():
    backend = MockBackend()
    config = Config()
    config.memory.enabled = False

    agent = Agent(backend=backend, config=config)

    assert agent.state.mode == AgentMode.DIRECT
    agent._set_mode(AgentMode.HEARTBEAT)
    assert agent.state.mode == AgentMode.HEARTBEAT


def test_agent_heartbeat_no_reply():
    backend = MockBackend("NO_REPLY")
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        agent = Agent(backend=backend, workspace=ws, config=config)
        response = agent.handle_heartbeat()

        assert response == "NO_REPLY"


def test_agent_heartbeat_memory_update():
    class FakeMemory:
        def __init__(self):
            self.calls = []

        def remember(self, content, memory_type="general", importance=0.5, tags=None):
            self.calls.append((content, memory_type, importance, tags))
            return True

    backend = MockBackend("MEMORY_UPDATE: captured heartbeat note")
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        fake_memory = FakeMemory()
        agent = Agent(backend=backend, workspace=ws, config=config, memory=fake_memory)
        response = agent.handle_heartbeat()

        assert response == "MEMORY_UPDATE"
        assert fake_memory.calls
        assert fake_memory.calls[0][0] == "captured heartbeat note"
        memory_file = (ws.path / "MEMORY.md").read_text()
        assert "captured heartbeat note" in memory_file


def test_agent_session_info():
    backend = MockBackend()
    config = Config()
    config.memory.enabled = False

    agent = Agent(backend=backend, config=config)
    info = agent.get_session_info()

    assert "session_id" in info
    assert info["mode"] == "direct"
    assert info["backend"] == "mock"
    assert info["message_count"] == 0


def test_agent_switch_backend():
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        agent = Agent(backend=MockBackend(), workspace=ws, config=config)
        agent.switch_backend("ollama")
        assert agent.backend.name == "ollama"


def test_agent_system_prompt_direct():
    backend = MockBackend()
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        agent = Agent(backend=backend, workspace=ws, config=config)
        agent.handle_message("test")

        # Chat path should still record history and produce response in direct mode
        assert agent.state.mode == AgentMode.DIRECT
        assert len(backend.get_history()) >= 2


def test_agent_system_prompt_heartbeat():
    backend = MockBackend()
    config = Config()
    config.memory.enabled = False

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(tmpdir, config)
        ws.init()

        agent = Agent(backend=backend, workspace=ws, config=config)
        agent.handle_heartbeat()

        assert "HEARTBEAT MODE" in backend.last_system_prompt
