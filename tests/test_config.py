"""Tests for configuration management."""

import tempfile
from pathlib import Path

from libre_claw.config import Config, BackendConfig, HeartbeatConfig


def test_default_config():
    config = Config()
    assert config.backend.type == "claude_code"
    assert config.heartbeat.enabled is True
    assert config.memory.enabled is True
    assert config.git.enabled is True


def test_backend_config_defaults():
    bc = BackendConfig()
    assert bc.type == "claude_code"
    assert "claude" in bc.claude_path
    assert bc.ollama_url == "http://localhost:11434"


def test_config_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("backend:\n  type: ollama\n  ollama_model: llama3\n")
        f.flush()
        config = Config.from_yaml(Path(f.name))
        assert config.backend.type == "ollama"
        assert config.backend.ollama_model == "llama3"


def test_config_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "config.yaml"
        config = Config()
        config.save(path)
        assert path.exists()

        loaded = Config.from_yaml(path)
        assert loaded.backend.type == config.backend.type


def test_heartbeat_config():
    hc = HeartbeatConfig()
    assert hc.interval_seconds == 30
    assert "HEARTBEAT" in hc.prompt


def test_heartbeat_interval_string_config():
    hc = HeartbeatConfig(interval_seconds="2h")
    assert hc.interval_seconds == 7200


def test_heartbeat_interval_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("heartbeat:\n  interval_seconds: 15m\n")
        f.flush()
        config = Config.from_yaml(Path(f.name))
        assert config.heartbeat.interval_seconds == 900
