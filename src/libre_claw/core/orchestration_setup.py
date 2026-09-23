# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Freeze an explicitly selected, trusted team profile for an application task."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from libre_claw.config import LibreClawConfig
from libre_claw.core.orchestration import bind_orchestration, resolve_profile, validate_profile
from libre_claw.core.session import Session
from libre_claw.core.tools import ToolRegistry
from libre_claw.providers.factory import orchestration_provider_config

if TYPE_CHECKING:
    from libre_claw.core.agent import Agent
    from libre_claw.core.cordis import CordisManager


def prepare_orchestration(
    config: LibreClawConfig, manager: CordisManager, session: Session, *, selected: str = "",
) -> LibreClawConfig:
    saved = session.checkpoint.get("orchestration")
    if not selected and "orchestration" not in session.checkpoint:
        return config
    if selected and selected != "orchestration":
        raise ValueError("Select the installed orchestration plugin.")
    spec = manager.orchestration_profile(config.general.working_directory)
    if selected:
        profile = resolve_profile(spec["config"], default_provider=config.general.default_provider,
                                  default_model=config.general.default_model)
        saved = {"plugin_id": spec["plugin_id"], "digest": spec["digest"],
                 "config": copy.deepcopy(spec["config"]), "profile": profile}
    else:
        if (not isinstance(saved, dict) or set(saved) != {"plugin_id", "digest", "config", "profile"}
                or saved["plugin_id"] != spec["plugin_id"] or saved["digest"] != spec["digest"]
                or saved["config"] != spec["config"]):
            raise ValueError("The saved team profile changed or was revoked. Start a new task with the reviewed profile.")
        profile = validate_profile(saved["profile"])
        resolved = resolve_profile(saved["config"], default_provider=profile["orchestrator"]["provider"],
                                   default_model=profile["orchestrator"]["model"])
        if profile != resolved:
            raise ValueError("The saved team routes no longer match the selected profile.")
    orchestrator = profile["orchestrator"]
    for route in profile["workers"]:
        orchestration_provider_config(config, route["provider"], route["model"], route)
    configured = orchestration_provider_config(config, orchestrator["provider"], orchestrator["model"], orchestrator)
    # Extraction is an extra model call outside the selected orchestrator/worker
    # lifecycle. Team tasks keep their own explicit model budget and handoffs.
    configured = replace(configured, providers=config.providers, memory=replace(configured.memory, auto_extract=False))
    spec["authorize"]()
    session.checkpoint["orchestration"] = copy.deepcopy(saved)
    return configured


def orchestration_provider_settings(config: LibreClawConfig, session: Session) -> LibreClawConfig:
    """Apply lead settings only to its provider, not to the workers' defaults."""
    saved = session.checkpoint.get("orchestration")
    if "orchestration" not in session.checkpoint:
        return config
    if not isinstance(saved, dict) or not isinstance(saved.get("profile"), dict):
        raise ValueError("The saved team profile is invalid; start a new team task.")
    route = saved["profile"]["orchestrator"]
    return orchestration_provider_config(config, route["provider"], route["model"], route)


def orchestration_registry(registry: ToolRegistry, session: Session) -> ToolRegistry:
    if "orchestration" not in session.checkpoint:
        return registry
    # Other model-capable extensions keep their own grants, but are not an
    # implicit escape route from this task's explicit worker profile.
    tools = [tool for tool in registry.tools()
        if not tool.name.startswith("subagent_")
        and (not tool.name.startswith("cordis__") or tool.name.startswith("cordis__orchestration__"))]
    required = {f"cordis__orchestration__{name}" for name in ("delegate", "wait", "status", "cancel")}
    if required - {tool.name for tool in tools}:
        raise ValueError("Team tools are unavailable or disabled by the tool allow/deny list. Enable the orchestration plugin tools before running a team task.")
    return ToolRegistry(tools)


def attach_orchestration(agent: Agent, config: LibreClawConfig, manager: CordisManager) -> Any:
    saved = agent.session.checkpoint.get("orchestration")
    if "orchestration" not in agent.session.checkpoint:
        return None
    # Recheck immediately before binding, including on recovered tasks.
    prepare_orchestration(config, manager, agent.session)
    spec = manager.orchestration_profile(config.general.working_directory)
    return bind_orchestration(agent, saved["profile"], spec["authorize"])
