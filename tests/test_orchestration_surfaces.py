# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from libre_claw.cli import main
from libre_claw.config import load_config
from libre_claw.core.agent import AgentDone
from libre_claw.core.session import ChatMessage, Session
from libre_claw.core.tools import BaseTool, ToolRegistry, ToolResult
from libre_claw.headless import HeadlessRunResult, run_headless
from libre_claw.providers.base import Done, LLMProvider
from libre_claw.tui.app import LibreClawApp


class KeylessProvider(LLMProvider):
    async def complete(self, *args, **kwargs):
        pytest.fail("This setup/lifecycle test must not run a model")
        yield Done()


@pytest.fixture
async def team_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("libre_claw.tui.app.create_provider", lambda config: KeylessProvider())
    monkeypatch.setattr("libre_claw.tui.app.create_fallback_providers", lambda config: ())
    config = load_config(working_directory=tmp_path)
    config = replace(config, general=replace(config.general, default_provider="deepseek", default_model="base-model"),
                     providers={"deepseek": {"default_model": "base-model"}},
                     fallback=replace(config.fallback, enabled=True), memory=replace(config.memory, enabled=False),
                     heartbeat=replace(config.heartbeat, enabled=False), petdex=replace(config.petdex, enabled=False),
                     tui=replace(config.tui, use_daemon=False))
    app = LibreClawApp(config=config)
    app.notes = []
    monkeypatch.setattr(app, "_append_system", app.notes.append)
    monkeypatch.setattr(app, "_update_shell_chrome", lambda: None)
    monkeypatch.setattr(app, "_append_startup_entry", lambda: None)
    monkeypatch.setattr(app, "_render_transcript", lambda: None)
    monkeypatch.setattr(app, "_hide_change_review", lambda: None)
    monkeypatch.setattr(app, "_hide_permission_prompt", lambda: None)
    monkeypatch.setattr(app, "query_one", lambda *args: SimpleNamespace(focus=lambda: None))
    async def no_artifacts(*args):
        return None
    monkeypatch.setattr(app, "_show_artifact_panel", no_artifacts)
    package = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/examples/orchestration"
    app.cordis_manager.install(package)
    app.cordis_manager.enable("orchestration", tmp_path, allow_model=True)
    try:
        yield app
    finally:
        await app.cordis_manager.aclose()
        await app.engine.aclose()


async def test_tui_selects_only_a_fresh_team_and_new_builds_a_fresh_controller(team_app):
    app = team_app
    original = app.config
    await app._handle_team_command("orchestration")
    assert app._orchestration_plugin == "orchestration"
    assert app._team_base_config == original
    assert not app.config.fallback.enabled
    controller = app.agent.tool_registry.context.shared_state["orchestration_controller"]
    app.session.add_user_message("existing task")
    await app._handle_team_command("off")
    assert app._orchestration_plugin == "orchestration"
    assert "use /new" in app.notes[-1]
    app._clear_transcript()
    assert not app.session.messages and app.agent.tool_registry.context.shared_state["orchestration_controller"] is not controller
    assert "orchestration" in app.session.checkpoint
    await app._handle_team_command("off")
    assert app._orchestration_plugin == "" and app.config.fallback.enabled
    assert "orchestration" not in app.session.checkpoint
    assert getattr(app.agent, "orchestration", None) is None


async def test_tui_missing_team_checkpoint_never_creates_a_replacement_profile(team_app):
    app = team_app
    await app._handle_team_command("orchestration")
    app.session.checkpoint.pop("orchestration")
    app._rebuild_agent()
    assert app.agent is None and "unavailable" in app.provider_error
    assert "orchestration" not in app.session.checkpoint


@pytest.mark.parametrize("history", ["summary", "archive"])
async def test_team_selection_rejects_compacted_history_even_without_visible_messages(team_app, history):
    app = team_app
    if history == "summary":
        app.session.summary = "A previous task's summary"
    else:
        app.session.archived_messages = [ChatMessage("user", [{"type": "text", "text": "A previous task"}])]
    await app._handle_team_command("orchestration")
    assert app._orchestration_plugin == "" and "use /new" in app.notes[-1]


async def test_team_setup_failure_does_not_replace_current_tui_session(team_app, monkeypatch):
    app = team_app
    original, config = app.session, app.config

    def fail(config, manager, session, **kwargs):
        session.checkpoint["partial"] = "discard"
        raise PermissionError("Team permission revoked")

    monkeypatch.setattr("libre_claw.tui.app.prepare_orchestration", fail)
    await app._handle_team_command("orchestration")
    assert app.session is original and app.config is config
    assert app.session.checkpoint == {} and app._orchestration_plugin == ""
    assert "Team permission revoked" in app.notes[-1]


async def test_resume_missing_or_unclaimed_team_checkpoint_leaves_current_task_untouched(team_app):
    app = team_app
    original, config, agent = app.session, app.config, app.agent
    run = await app.run_store.create_run("Broken team", kind="chat", provider="deepseek", model="base-model", working_directory=app.config.general.working_directory, orchestration_plugin="orchestration")
    await app.run_store.save_session(run.run_id, Session())
    await app._handle_resume_command(run.run_id)
    assert app.session is original and app.config is config and app.agent is agent
    assert app._resumed_run_id is None and "unavailable" in app.notes[-1]

    await app._handle_team_command("orchestration")
    saved = app.session
    await app._handle_team_command("off")
    unclaimed = await app.run_store.create_run("Unclaimed", kind="chat", provider="deepseek", model="base-model", working_directory=app.config.general.working_directory)
    imported = Session(checkpoint={"orchestration": {"untrusted": True}})
    await app.run_store.save_session(unclaimed.run_id, imported)
    await app._handle_resume_command(unclaimed.run_id)
    assert app.session is saved and "does not authorize" in app.notes[-1]


async def test_valid_durable_team_restores_exact_profile_and_normal_resume_restores_base_config(team_app):
    app = team_app
    base = app.config
    await app._handle_team_command("orchestration")
    app.session.add_user_message("Saved team task")
    original_profile = app.session.checkpoint["orchestration"]
    team = await app.run_store.create_run("Team", kind="chat", provider="deepseek", model="base-model", state="done",
                                         working_directory=app.config.general.working_directory, orchestration_plugin="orchestration")
    await app.run_store.save_session(team.run_id, app.session)
    app._clear_transcript()
    await app._handle_team_command("off")
    await app._handle_resume_command(team.run_id)
    assert app.session.checkpoint["orchestration"] == original_profile
    assert app._resumed_run_id == team.run_id and app.agent is not None
    assert app.agent.tool_registry.context.shared_state["orchestration_controller"] is not None

    ordinary = await app.run_store.create_run("Ordinary", kind="chat", provider="deepseek", model="base-model", state="done", working_directory=app.config.general.working_directory)
    await app.run_store.save_session(ordinary.run_id, Session())
    await app._handle_resume_command(ordinary.run_id)
    assert app._orchestration_plugin == "" and app._team_base_config is None
    assert app.config.fallback == base.fallback and app.config.agent.context_window_tokens == base.agent.context_window_tokens
    assert "orchestration_controller" not in app.agent.tool_registry.context.shared_state


async def test_null_team_checkpoint_in_a_durable_run_fails_closed(team_app):
    app = team_app
    original, agent = app.session, app.agent
    run = await app.run_store.create_run("Invalid team", kind="chat", provider="deepseek", model="base-model", state="done",
                                        working_directory=app.config.general.working_directory, orchestration_plugin="orchestration")
    await app.run_store.save_session(run.run_id, Session(checkpoint={"orchestration": None}))
    await app._handle_resume_command(run.run_id)
    assert app.session is original and app.agent is agent and app._resumed_run_id is None
    assert "unavailable" in app.notes[-1]


async def test_team_mode_suppresses_background_metadata_and_daemon_model_updates(team_app, monkeypatch):
    app = team_app
    await app._handle_team_command("orchestration")
    original = app.config

    class Client:
        async def update_model(self, *args, **kwargs):
            pytest.fail("A team must not update the daemon's default model")

        async def update_fallback(self, *args, **kwargs):
            pytest.fail("A team must not update the daemon's fallback")

    async def detect(*args, **kwargs):
        pytest.fail("Automatic metadata must not override the team context budget")

    app.daemon_client = Client()
    monkeypatch.setattr("libre_claw.tui.app.detect_openrouter_model_limits", detect)
    await app._update_daemon_model_runtime("openrouter", "different")
    assert await app._sync_daemon_fallback_after_selection(app.config.fallback) == ""
    await app._refresh_openrouter_model_limits()
    app._apply_daemon_model_payload({"provider": "openrouter", "model": "different"}, announce_model_change=False)
    assert app.config is original


async def test_named_session_export_cannot_drop_team_permissions_or_keep_a_stale_team_controller(team_app):
    app = team_app
    await app._handle_team_command("orchestration")
    await app._save_session_async("unsafe-export")
    assert await app.memory_store.load_session("unsafe-export") is None
    original = app.session
    await app.memory_store.save_session("ordinary", Session())
    await app._load_session_async("ordinary")
    assert app.session is original and "Use /new and /team off" in app.notes[-1]
    await app._handle_team_command("off")
    app._resumed_run_id = "old-run"
    app.session.checkpoint["objective"] = "old objective"
    await app._load_session_async("ordinary")
    assert app._resumed_run_id is None and app.session.checkpoint == {}
    assert getattr(app.agent, "orchestration", None) is None


async def test_daemon_team_start_omits_provider_model_overrides(team_app, monkeypatch):
    app = team_app
    await app._handle_team_command("orchestration")
    calls = []

    class Client:
        async def start_run(self, message, **payload):
            calls.append(payload)
            return {"run": {"run_id": "daemon-team", "orchestration_plugin": "orchestration"}}

    async def no_poll(*args):
        return None

    app.daemon_client = Client()
    monkeypatch.setattr(app, "_poll_daemon_run", no_poll)
    await app._stream_daemon_response("Build", 0)
    assert calls[0]["orchestration_plugin"] == "orchestration"
    assert "provider" not in calls[0] and "model" not in calls[0]
    assert app._orchestration_plugin == "orchestration"


async def test_tui_refresh_keeps_unrelated_model_plugins_out_of_the_team_tool_registry(team_app, monkeypatch):
    app = team_app
    await app._handle_team_command("orchestration")
    observed = []

    class UnrelatedModelPlugin(BaseTool):
        name = "cordis__unrelated__delegate"
        description = "An unrelated model-capable extension"

        async def execute(self):
            return ToolResult(content="must not run")

    def refresh(config, registry):
        return ToolRegistry([*registry.tools(), UnrelatedModelPlugin(registry.context)])

    async def turn(*args, **kwargs):
        observed.extend(tool.name for tool in app.agent.tool_registry.tools())
        yield AgentDone()

    monkeypatch.setattr("libre_claw.tui.app.refresh_cordis_tools", refresh)
    monkeypatch.setattr(app.agent, "run", turn)
    await app._stream_agent_response("Work", 0)
    assert "cordis__unrelated__delegate" not in observed
    assert {"cordis__orchestration__delegate", "cordis__orchestration__wait", "cordis__orchestration__status", "cordis__orchestration__cancel"} <= set(observed)


@pytest.mark.parametrize("stage", ["profile", "registry", "provider", "binding"])
async def test_headless_setup_errors_are_useful_and_always_close_resources(tmp_path, monkeypatch, stage):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(working_directory=tmp_path)
    config = replace(config, memory=replace(config.memory, enabled=False))
    closed = []

    class Manager:
        def __init__(self, **kwargs):
            pass

        async def aclose(self):
            closed.append("plugins")

    class Engine:
        async def aclose(self):
            closed.append("engine")

    def failure(*args, **kwargs):
        raise PermissionError(f"{stage} permission unavailable")

    monkeypatch.setattr("libre_claw.headless.CordisManager", Manager)
    monkeypatch.setattr("libre_claw.headless.CordisEngine", Engine)
    monkeypatch.setattr("libre_claw.headless.bind_cordis_manager", lambda *args: None)
    monkeypatch.setattr("libre_claw.headless.prepare_orchestration", lambda config, *args, **kwargs: config)
    monkeypatch.setattr("libre_claw.headless.create_fallback_providers", lambda *args: ())
    monkeypatch.setattr("libre_claw.headless.create_provider", lambda *args: KeylessProvider())
    functions = {"profile": "prepare_orchestration", "registry": "orchestration_registry", "provider": "create_provider", "binding": "attach_orchestration"}
    monkeypatch.setattr("libre_claw.headless." + functions[stage], failure)
    result = await run_headless(config, "Build", orchestration_plugin="orchestration", tool_registry=ToolRegistry())
    assert not result.succeeded and f"{stage} permission unavailable" in result.error
    assert closed == ["plugins", "engine"]


def test_cli_forwards_explicit_team_and_prints_expected_setup_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = []

    async def fail(config, prompt, **kwargs):
        seen.append(kwargs.get("orchestration_plugin"))
        return HeadlessRunResult("", error="Team must be enabled with model access")

    monkeypatch.setattr("libre_claw.cli.run_headless", fail)
    result = CliRunner().invoke(main, ["--working-directory", str(tmp_path), "run", "Build", "--team", "orchestration"])
    assert result.exit_code == 1 and "Error: Team must be enabled with model access" in result.output
    assert seen == ["orchestration"]

    async def invalid(config, prompt, **kwargs):
        raise PermissionError("Model route access was revoked")

    monkeypatch.setattr("libre_claw.cli.run_headless", invalid)
    result = CliRunner().invoke(main, ["--working-directory", str(tmp_path), "run", "Build", "--team", "orchestration"])
    assert result.exit_code == 1 and "Error: Model route access was revoked" in result.output
