# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil

import pytest

from libre_claw.core import cordis
from libre_claw.core.cordis import CordisError, CordisManager, MANIFEST_NAME
from libre_claw.core.cordis_security import CordisSecurityError, prepare_cordis_process
from libre_claw.core.tools import ToolContext


TOOL = {"name": "config", "description": "Read nonsecret configuration", "input_schema": {"type": "object", "properties": {}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "greeting": {"type": "string", "minLength": 1, "maxLength": 20},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        "enabled": {"type": "boolean"},
        "token": {"type": "string", "format": "password", "minLength": 3},
        "nested": {"type": "object", "properties": {"secret": {"type": "string", "writeOnly": True}, "label": {"type": "string"}}, "additionalProperties": False},
    },
}


def package(path, **updates):
    path.mkdir()
    manifest = {"id": "configurable", "name": "Configurable", "version": "1", "description": "A local configurable tool", "entry": "plugin.mjs", "tools": [TOOL], "config": {"greeting": "hello", "limit": 2}, "config_schema": SCHEMA, **updates}
    (path / MANIFEST_NAME).write_text(json.dumps(manifest))
    (path / "plugin.mjs").write_text(f"export default {{inject:['libre'],apply(ctx,config){{ctx.libre.registerTool({json.dumps(TOOL)},()=>JSON.stringify(config));}}}};")
    return path


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = CordisManager(root=tmp_path / "registry")
    return manager, workspace


def test_preview_is_nonexecuting_and_install_refuses_changed_reviewed_bytes(setup, tmp_path):
    manager, workspace = setup
    source = package(tmp_path / "source")
    (source / "plugin.mjs").write_text("throw new Error('must never execute')")
    preview = manager.preview(source)
    assert preview["description"] == "A local configurable tool"
    assert preview["tool_definitions"] == [TOOL]
    assert preview["tool_count"] == 1 and len(preview["digest"]) == 64
    assert not manager.root.exists()
    (source / "plugin.mjs").write_text("throw new Error('changed')")
    with pytest.raises(CordisError, match="changed since its preview"):
        manager.install(source, expected_digest=preview["digest"])
    assert manager.list_plugins(workspace) == []
    installed = manager.install(source, expected_digest=manager.preview(source)["digest"])
    assert installed["enabled"] is False
    assert "config" not in installed
    assert manager.details("configurable", workspace)["tool_definitions"] == [TOOL]


def test_config_is_workspace_scoped_persists_disabled_and_never_grants_permissions(setup, tmp_path):
    manager, workspace = setup
    other = tmp_path / "other"
    other.mkdir()
    manager.install(package(tmp_path / "source"))
    result = manager.configure("configurable", workspace, {"greeting": "custom"})
    assert result["config"] == {"greeting": "custom", "limit": 2}
    assert result["enabled"] is False and result["grants"]["allow_network"] is False
    assert manager.details("configurable", other)["config"]["greeting"] == "hello"
    manager.enable("configurable", workspace)
    manager.disable("configurable", workspace)
    assert manager.details("configurable", workspace)["config"] == result["config"]
    manager.enable("configurable", workspace)
    assert manager.list_plugins(workspace)[0]["grants"] == {"allow_network": False, "read_paths": [], "write_paths": []}
    assert "config" not in manager.list_plugins(workspace)[0]
    assert (manager.root / "registry.json").stat().st_mode & 0o077 == 0
    assert manager.configure("configurable", workspace, {})["config"] == {"greeting": "hello", "limit": 2}


def test_secret_redaction_preservation_and_explicit_clear(setup, tmp_path):
    manager, workspace = setup
    manager.install(package(tmp_path / "source", config={"greeting": "hello", "token": "default-secret"}))
    first = manager.configure("configurable", workspace, {"greeting": "hi", "token": "private-token", "nested": {"secret": "nested-private", "label": "a"}})
    assert first["config"] == {"greeting": "hi", "nested": {"label": "a"}}
    assert set(first["configured_secrets"]) == {"/token", "/nested/secret"}
    assert "private-token" not in json.dumps(first) and "nested-private" not in json.dumps(first)
    preserved = manager.configure("configurable", workspace, {"greeting": "next", "nested": {"label": "b"}})
    assert set(preserved["configured_secrets"]) == set(first["configured_secrets"])
    record = manager._record("configurable")
    saved = next(iter(record["configs"].values()))
    assert saved["token"] == "private-token" and saved["nested"]["secret"] == "nested-private"
    cleared = manager.configure("configurable", workspace, {"token": None, "nested": {"secret": None}})
    assert cleared["configured_secrets"] == []
    assert "token" not in cleared["config"]
    # Subsequent saves cannot accidentally restore a secret from package defaults.
    assert manager.configure("configurable", workspace, {})["configured_secrets"] == []


def test_secret_schema_annotations_and_array_defaults_do_not_leak(setup, tmp_path):
    manager, workspace = setup
    credential = {"type": "string", "writeOnly": True, "default": "private-default", "examples": ["private-example"], "enum": ["private-default", "private-example"]}
    schema = {"type": "object", "properties": {
        "token": credential,
        "accounts": {"type": "array", "items": {"type": "object", "properties": {"token": credential}}, "default": [{"token": "private-default"}], "examples": [[{"token": "private-example"}]], "enum": [[{"token": "private-default"}], [{"token": "private-example"}]]},
    }, "default": {"token": "private-default"}, "examples": [{"token": "private-example"}]}
    manager.install(package(tmp_path / "source", config={"token": "private-default", "accounts": [{"token": "private-default"}]}, config_schema=schema))
    details = manager.details("configurable", workspace)
    assert details["config"] == {}
    assert set(details["configured_secrets"]) == {"/token", "/accounts/0/token"}
    assert "private-default" not in json.dumps(details) and "private-example" not in json.dumps(details)


def test_omitted_secret_array_retains_previous_override(setup, tmp_path):
    manager, workspace = setup
    schema = {"type": "object", "properties": {"accounts": {"type": "array", "items": {"type": "object", "properties": {"token": {"type": "string", "writeOnly": True}, "label": {"type": "string"}}}}}}
    manager.install(package(tmp_path / "source", config={}, config_schema=schema))
    manager.configure("configurable", workspace, {"accounts": [{"token": "private", "label": "one"}]})
    assert manager.configure("configurable", workspace, {})["configured_secrets"] == ["/accounts/0/token"]
    assert manager.configure("configurable", workspace, {"accounts": []})["configured_secrets"] == []


@pytest.mark.parametrize("config", [{"limit": True}, {"limit": 0}, {"limit": 11}, {"limit": 1.5}, {"greeting": ""}, {"greeting": "x" * 21}, {"enabled": "true"}, {"unknown": "field"}, {"token": "a"}, {"nested": {"other": "bad"}}, {"limit": float("nan")}, ["not-object"], {"greeting": object()}])
def test_invalid_config_is_rejected_atomically_without_echoing_values(setup, tmp_path, config):
    manager, workspace = setup
    manager.install(package(tmp_path / "source"))
    before = (manager.root / "registry.json").read_bytes()
    with pytest.raises(CordisError):
        manager.configure("configurable", workspace, config)
    assert (manager.root / "registry.json").read_bytes() == before


@pytest.mark.parametrize("schema", [
    {"type": "object", "allOf": []},
    {"type": "object", "properties": {"name": {"type": "string", "pattern": "(a+)+"}}},
    {"type": "object", "properties": {"name": {"type": "string", "format": "email"}}},
    {"type": "object", "properties": {"x": {"type": ["string", "null"]}}},
    {"type": "object", "properties": {"x": {"$ref": "https://example.invalid/schema"}}},
    {"type": "object", "properties": {"x": {"type": "number", "minimum": "1", "maximum": 2}}},
    {"type": "object", "properties": {"x": {"type": "array"}}},
    {"type": "object", "required": ["not-declared"]},
    {"type": "object", "additionalProperties": "yes"},
    {"type": "object", "minProperties": True},
])
def test_unsupported_or_malformed_schema_rejected_without_execution(setup, tmp_path, schema):
    manager, _ = setup
    with pytest.raises(CordisError):
        manager.preview(package(tmp_path / "source", config={}, config_schema=schema))


def test_schema_supports_enum_arrays_nested_and_numeric_bounds(setup, tmp_path):
    manager, workspace = setup
    schema = {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["brief", "full"]},
        "weights": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 2}, "minItems": 1, "maxItems": 3, "uniqueItems": True},
        "options": {"type": "object", "additionalProperties": {"type": "boolean"}, "maxProperties": 2},
    }, "required": ["mode", "weights"], "additionalProperties": False}
    manager.install(package(tmp_path / "source", config={}, config_schema=schema))
    manager.configure("configurable", workspace, {"mode": "brief", "weights": [0.5, 1], "options": {"detail": False}})
    for config in ({"mode": "other", "weights": [1]}, {"mode": "full", "weights": [1, 1]}, {"mode": "full", "weights": [2]}, {"mode": "full", "weights": [1], "options": {"detail": 1}}):
        with pytest.raises(CordisError):
            manager.configure("configurable", workspace, config)


def test_required_configuration_needed_before_enable_and_secret_clear_is_atomic(setup, tmp_path):
    manager, workspace = setup
    schema = {"type": "object", "properties": {"token": {"type": "string", "writeOnly": True}}, "required": ["token"]}
    manager.install(package(tmp_path / "source", config={}, config_schema=schema))
    assert manager.details("configurable", workspace)["enabled"] is False
    with pytest.raises(CordisError, match="missing required"):
        manager.enable("configurable", workspace)
    manager.configure("configurable", workspace, {"token": "private"})
    manager.enable("configurable", workspace)
    with pytest.raises(CordisError, match="missing required"):
        manager.configure("configurable", workspace, {"token": None})
    assert manager.details("configurable", workspace)["configured_secrets"] == ["/token"]


def test_changed_code_cannot_inherit_config_secrets_or_grants(setup, tmp_path):
    manager, workspace = setup
    source = package(tmp_path / "source")
    manager.install(source)
    manager.configure("configurable", workspace, {"token": "private", "greeting": "custom"})
    manager.enable("configurable", workspace)
    manager.install(source)
    assert manager.details("configurable", workspace)["configured_secrets"] == ["/token"]
    (source / "plugin.mjs").write_text("export default {apply(){}}")
    manager.install(source)
    details = manager.details("configurable", workspace)
    assert not details["enabled"] and details["configured_secrets"] == []
    assert details["config"]["greeting"] == "hello"


def test_old_registry_without_config_schema_or_overrides_remains_supported(setup, tmp_path):
    manager, workspace = setup
    source = package(tmp_path / "source")
    manifest = json.loads((source / MANIFEST_NAME).read_text())
    manifest.pop("config_schema")
    (source / MANIFEST_NAME).write_text(json.dumps(manifest))
    manager.install(source)
    registry = manager._read_registry()
    registry["plugins"]["configurable"].pop("configs")
    manager._write_registry(registry)
    assert manager.details("configurable", workspace)["config_schema"] is None
    manager.configure("configurable", workspace, {"anything": {"nested": [1, None, True]}})
    manager.enable("configurable", workspace)


def test_config_size_and_depth_are_bounded(setup, tmp_path):
    manager, workspace = setup
    manager.install(package(tmp_path / "source"))
    with pytest.raises(CordisError, match="64 KiB"):
        manager.configure("configurable", workspace, {"token": "x" * 65536})
    deep = {}
    for _ in range(40):
        deep = {"nested": deep}
    with pytest.raises(CordisError, match="deeply nested"):
        manager.configure("configurable", workspace, deep)


async def test_real_runtime_receives_effective_config_per_workspace(setup, tmp_path):
    manager, workspace = setup
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    source = package(tmp_path / "source")
    try:
        prepare_cordis_process(node, manager.runtime_path, source, tmp_path / "probe-state")
    except CordisSecurityError as exc:
        pytest.skip(f"Enforced offline runtime unavailable: {exc}")
    manager.node_executable = node
    manager.install(source)
    manager.configure("configurable", workspace, {"greeting": "custom", "limit": 8})
    manager.enable("configurable", workspace)
    tool = manager.create_tools(ToolContext(working_directory=workspace))[0]
    result = await tool.execute()
    assert not result.is_error and json.loads(result.content) == {"greeting": "custom", "limit": 8}
    other = tmp_path / "other"
    other.mkdir()
    manager.enable("configurable", other)
    other_result = await manager.create_tools(ToolContext(working_directory=other))[0].execute()
    assert not other_result.is_error and json.loads(other_result.content) == {"greeting": "hello", "limit": 2}


def test_empty_directory_tree_is_bounded_before_install(setup, tmp_path, monkeypatch):
    manager, workspace = setup
    source = package(tmp_path / "source")
    monkeypatch.setattr(cordis, "MAX_PACKAGE_DIRECTORIES", 4)
    for index in range(4):
        (source / f"empty-{index}").mkdir()
    with pytest.raises(CordisError, match="too many directories"):
        manager.preview(source)
    assert manager.list_plugins(workspace) == []


def test_scan_bounds_directory_enumeration_even_for_excluded_files(setup, tmp_path, monkeypatch):
    manager, _ = setup
    source = package(tmp_path / "source")
    monkeypatch.setattr(cordis, "MAX_PACKAGE_ENTRIES", 4)
    for index in range(5):
        (source / f".ignored-{index}").write_text("private")
    with pytest.raises(CordisError, match="too many directory entries"):
        manager.preview(source)


def test_deep_directories_fail_without_recursing_or_silently_skipping(setup, tmp_path, monkeypatch):
    manager, _ = setup
    source = package(tmp_path / "source")
    monkeypatch.setattr(cordis, "MAX_PACKAGE_DEPTH", 2)
    (source / "one" / "two" / "three").mkdir(parents=True)
    with pytest.raises(CordisError, match="allowed depth"):
        manager.preview(source)


def test_unreadable_plugin_subtree_fails_closed(setup, tmp_path, monkeypatch):
    manager, _ = setup
    source = package(tmp_path / "source")
    unreadable = source / "unreadable"
    unreadable.mkdir()
    original = cordis.os.scandir

    def scan(path):
        if path == unreadable:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(cordis.os, "scandir", scan)
    with pytest.raises(CordisError, match="Cannot safely read plugin directories"):
        manager.preview(source)
    assert not manager.root.exists()


def test_excluded_directories_are_never_enumerated(setup, tmp_path, monkeypatch):
    manager, _ = setup
    source = package(tmp_path / "source")
    for name in ("node_modules", "__pycache__", ".git"):
        (source / name).mkdir()
        (source / name / "private").write_text("unreadable private content")
    original = cordis.os.scandir

    def scan(path):
        if path != source:
            raise AssertionError("Excluded directory must not be enumerated")
        return original(path)

    monkeypatch.setattr(cordis.os, "scandir", scan)
    assert manager.preview(source)["id"] == "configurable"


def test_excluded_dependency_directory_symlink_is_ignored(setup, tmp_path):
    manager, _ = setup
    source = package(tmp_path / "source")
    private = tmp_path / "private-dependencies"
    private.mkdir()
    (private / "private.js").write_text("not part of the plugin")
    (source / "node_modules").symlink_to(private, target_is_directory=True)
    assert manager.preview(source)["id"] == "configurable"
