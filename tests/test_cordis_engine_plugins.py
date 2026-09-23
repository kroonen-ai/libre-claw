# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json

import pytest

from libre_claw.config import load_config
from libre_claw.core.cordis import CordisManager
from libre_claw.core.cordis_engine import CordisEngineError
from libre_claw.core.cordis_engine_plugins import engine_for, engine_plugin_specs, validate_engine_declaration


@pytest.fixture
def core_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    config = load_config(working_directory=project)
    source = tmp_path / "extension"
    source.mkdir()
    declaration = {"entry": "engine.mjs", "services": [
        {"id": "providers", "title": "Reviewed provider dispatch", "dependencies": [], "methods": ["complete"]},
        {"id": "checks", "title": "Checks", "dependencies": ["tools"], "methods": ["run"]},
    ]}
    manifest = {"id": "engine-test", "name": "Engine test", "version": "1.0.0", "entry": "plugin.mjs",
                "tools": [], "engine": declaration, "config": {"deny": False}}
    (source / "libre-claw-plugin.json").write_text(json.dumps(manifest))
    (source / "plugin.mjs").write_text("export default {name:'engine-test',apply(){}};")
    (source / "engine.mjs").write_text("""
export default {name:'test-core-services', inject:['libreEngine'], apply(ctx, config) {
  for (const [service, method] of [['providers','complete'],['checks','run']]) {
    ctx.effect(() => ctx.libreEngine.register(service, method, (operation, next) => {
      if (Object.keys(operation).sort().join(',') !== 'method,mode,service') throw new Error('Payload leaked');
      if (config.deny) throw new Error('Denied by reviewed engine policy');
      return next();
    }));
  }
}};
""")
    manager = CordisManager(config=config)
    manager.install(source)
    return config, manager, source, project


async def test_core_extensions_require_separate_workspace_grant(core_plugin, tmp_path):
    config, manager, source, project = core_plugin
    manager.enable("engine-test", project, allow_model=True)
    assert engine_plugin_specs(config) == []
    manager.enable("engine-test", project, allow_engine=True)
    assert len(engine_plugin_specs(config)) == 1
    other = tmp_path / "other"
    other.mkdir()
    assert engine_plugin_specs(load_config(working_directory=other)) == []
    manager.disable("engine-test", project)
    assert engine_plugin_specs(config) == []


async def test_registered_cordis_service_replaces_dispatch_and_keeps_host_payload_private(core_plugin):
    config, manager, _, project = core_plugin
    manager.enable("engine-test", project, allow_engine=True)
    private = object()
    async with engine_for(config) as engine:
        snapshot = await engine.inspect()
        providers = next(row for row in snapshot["components"] if row["id"] == "providers")
        assert providers["implementations"]["complete"] == "engine-test"
        assert providers["implementations"]["models"] == "libre-claw"
        assert engine.is_enabled("checks")
        assert await engine.call("providers", "complete", handler=lambda: private) is private
        assert await engine.call("checks", "run", handler=lambda: "verified") == "verified"
        with pytest.raises(CordisEngineError, match="requires"):
            await engine.configure({"tools": False})


async def test_core_policy_can_reject_without_executing_host_operation(core_plugin):
    config, manager, _, project = core_plugin
    manager.configure("engine-test", project, {"deny": True})
    manager.enable("engine-test", project, allow_engine=True)
    async with engine_for(config) as engine:
        with pytest.raises(CordisEngineError, match="engine-test rejected providers.complete"):
            await engine.call("providers", "complete", handler=lambda: pytest.fail("Policy bypassed"))
        assert next(row for row in (await engine.inspect())["components"] if row["id"] == "providers")["failed"] == 1
        assert await engine.call("providers", "models", handler=lambda: "catalog") == "catalog"


async def test_core_revocation_stops_calls_instead_of_returning_to_default_dispatch(core_plugin):
    config, manager, _, project = core_plugin
    manager.enable("engine-test", project, allow_engine=True)
    async with engine_for(config) as engine:
        manager.disable("engine-test", project)
        with pytest.raises(CordisEngineError, match="grants changed"):
            await engine.call("providers", "complete", handler=lambda: pytest.fail("Revoked plugin bypassed"))
        assert not engine.running


async def test_core_revocation_stops_existing_stream_and_joins_cleanup(core_plugin):
    config, manager, _, project = core_plugin
    manager.enable("engine-test", project, allow_engine=True)
    cleaned = asyncio.Event()
    async def values():
        try:
            yield "first"
            yield "second"
        finally:
            cleaned.set()
    async with engine_for(config) as engine:
        stream = engine.stream("providers", "complete", handler=values)
        assert await anext(stream) == "first"
        manager.disable("engine-test", project)
        with pytest.raises(CordisEngineError, match="grants changed"):
            await anext(stream)
        await stream.aclose()
        assert cleaned.is_set()


async def test_core_source_replacement_does_not_inherit_engine_access(core_plugin):
    config, manager, source, project = core_plugin
    manager.enable("engine-test", project, allow_engine=True)
    (source / "engine.mjs").write_text("export default {name:'changed',apply(){}};")
    manager.install(source)
    assert engine_plugin_specs(config) == []


async def test_new_core_grant_waits_for_explicit_restart(core_plugin):
    config, manager, _, project = core_plugin
    async with engine_for(config) as engine:
        manager.enable("engine-test", project, allow_engine=True)
        await engine.verify_plugins()
        assert await engine.call("providers", "complete", handler=lambda: "existing") == "existing"
        assert not engine.is_enabled("checks")
    async with engine_for(config) as replacement:
        assert replacement.is_enabled("checks")


async def test_core_plugin_cannot_execute_after_policy_failure(core_plugin):
    config, manager, source, project = core_plugin
    (source / "engine.mjs").write_text("""
export default {name:'bad-policy',inject:['libreEngine'],apply(ctx) {
  for (const [service,method] of [['providers','complete'],['checks','run']]) {
    ctx.effect(() => ctx.libreEngine.register(service,method,(_operation,next) => {next(); throw Error('later rejection');}));
  }
}};
""")
    manager.install(source)
    manager.enable("engine-test", project, allow_engine=True)
    async with engine_for(config) as engine:
        with pytest.raises(CordisEngineError):
            await engine.call("checks", "run", handler=lambda: pytest.fail("Host ran before policy completed"))


async def test_synchronous_core_policy_loop_is_stopped_by_host_deadline(core_plugin, monkeypatch):
    from libre_claw.core import cordis_engine

    config, manager, source, project = core_plugin
    (source / "engine.mjs").write_text("""
export default {name:'blocked-policy',inject:['libreEngine'],apply(ctx) {
  for (const [service,method] of [['providers','complete'],['checks','run']]) {
    ctx.libreEngine.register(service,method,() => {while (true) {}});
  }
}};
""")
    manager.install(source)
    manager.enable("engine-test", project, allow_engine=True)
    monkeypatch.setattr(cordis_engine, "_DISPATCH_TIMEOUT", 0.1)
    async with engine_for(config) as engine:
        with pytest.raises(CordisEngineError, match="did not dispatch"):
            async with asyncio.timeout(3):
                await engine.call("checks", "run", handler=lambda: pytest.fail("Blocked policy dispatched"))
        await asyncio.wait_for(engine._process.wait(), timeout=2)
        assert not engine.running
        assert not engine.busy


async def test_delayed_registration_retains_its_own_plugin_grants(core_plugin, tmp_path):
    config, manager, source, project = core_plugin
    manifest_path = source / "libre-claw-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine"]["services"] = manifest["engine"]["services"][:1]
    manifest_path.write_text(json.dumps(manifest))
    (source / "engine.mjs").write_text("""
export default {name:'first-policy',inject:['libreEngine'],apply(ctx) {
  let denied = false;
  ctx.libreEngine.register('providers','complete',(_operation,next) => {
    if (!denied) throw Error('Registration borrowed another package grant');
    next();
  });
  setTimeout(() => {
    try { ctx.libreEngine.register('checks','run',(_operation,next) => next()); }
    catch { denied = true; }
  }, 10);
}};
""")
    manager.install(source)
    manager.enable("engine-test", project, allow_engine=True)
    second = tmp_path / "second-extension"
    second.mkdir()
    manifest["id"] = "second-engine"
    manifest["engine"]["services"] = [{"id": "checks", "title": "Checks", "dependencies": [], "methods": ["run"]}]
    (second / "libre-claw-plugin.json").write_text(json.dumps(manifest))
    (second / "plugin.mjs").write_text("export default {name:'second-engine',apply(){}};")
    (second / "engine.mjs").write_text("""
export default {name:'second-policy',inject:['libreEngine'],async apply(ctx) {
  await new Promise(resolve => setTimeout(resolve, 50));
  ctx.libreEngine.register('checks','run',(_operation,next) => next());
}};
""")
    manager.install(second)
    manager.enable("second-engine", project, allow_engine=True)
    async with engine_for(config) as engine:
        assert await engine.call("providers", "complete", handler=lambda: "first") == "first"
        assert await engine.call("checks", "run", handler=lambda: "second") == "second"


@pytest.mark.parametrize("entry", ["../engine.mjs", "/engine.mjs", "a//b.js", "a/./b.js", "a\\b.js"])
def test_engine_entry_cannot_escape_reviewed_package(entry):
    with pytest.raises(ValueError):
        validate_engine_declaration({"entry": entry, "services": [
            {"id": "checks", "title": "Checks", "dependencies": [], "methods": ["run"]}]})
