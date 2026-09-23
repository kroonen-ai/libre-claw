# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile

import httpx
import pytest

from libre_claw.core.cordis import CordisError, MANIFEST_NAME
from libre_claw.core.cordis_harness import HARNESS_ENTRY, adapt_harness_package
from libre_claw.core import cordis_packages as packages


MODULE = b"export const inject=['tools']; export function apply(ctx) { throw new Error('must not execute during preview'); }\n"


def bundle(patch=None, **updates):
    package = {"name": "@example/harness-plugin", "version": "1.2.3", "type": "module",
               "main": "lib/index.js", "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}, **updates}
    return {"package.json": json.dumps(package).encode(), "lib/index.js": MODULE,
            "cordis.patch.yml": (patch or "- insert:\n    - id: main\n      name: '@example/harness-plugin'\n").encode()}


def manifest(files):
    return json.loads(files[MANIFEST_NAME])


def archive(files):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as handle:
        for name, content in files.items():
            member = tarfile.TarInfo("package/" + name)
            member.size = len(content)
            handle.addfile(member, io.BytesIO(content))
    return target.getvalue()


def metadata(name, version, data):
    return {"name": name, "version": version, "dist": {
        "tarball": f"https://registry.npmjs.org/{name}/-/{version}.tgz",
        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode(),
    }}


def test_harness_bundle_retains_original_code_and_generates_deterministic_wrapper():
    original = bundle()
    first = adapt_harness_package(original)
    second = adapt_harness_package(dict(reversed(list(original.items()))))
    assert first == second
    assert first["lib/index.js"] == MODULE
    assert first["package.json"] == original["package.json"]
    info = manifest(first)
    assert info["format"] == "deepseek-harness" and info["tools"] == []
    assert info["harness"]["components"] == [{"id": "main", "enabled": True, "module": "./lib/index.js", "config": {}}]
    assert b"mountHarnessComponents(components, config.components" in first[HARNESS_ENTRY]
    assert MANIFEST_NAME not in original


def test_package_exports_choose_import_entry_and_preserve_declarations():
    files = bundle(dsh={}, main="ignored.js", exports={".": {"types": "./types.d.ts", "import": "./lib/index.js"}})
    info = manifest(adapt_harness_package(files))
    assert info["harness"]["components"][0]["module"] == "./lib/index.js"


@pytest.mark.parametrize(("exports", "selected"), [
    ({"node": "./node.mjs", "import": "./import.mjs"}, "./node.mjs"),
    ({"import": "./import.mjs", "node": "./node.mjs"}, "./import.mjs"),
    ({"default": "./default.mjs", "import": "./import.mjs"}, "./default.mjs"),
])
def test_root_exports_follow_node_condition_order(exports, selected):
    files = bundle(dsh={}, exports=exports)
    files.update({"node.mjs": MODULE, "import.mjs": MODULE, "default.mjs": MODULE})
    assert manifest(adapt_harness_package(files))["harness"]["components"][0]["module"] == selected


def test_export_null_blocks_fallback_and_wildcards_choose_most_specific_pattern():
    with pytest.raises(CordisError, match="does not export"):
        adapt_harness_package(bundle(dsh={}, exports={"node": None, "default": "./lib/index.js"}))
    files = bundle("- insert: [{id: main, name: '@example/harness-plugin/tools/test'}]", exports={
        "./*": "./general/*.mjs", "./tools/*": "./tools/*.mjs",
    })
    files.update({"general/tools/test.mjs": MODULE, "tools/test.mjs": MODULE})
    assert manifest(adapt_harness_package(files))["harness"]["components"][0]["module"] == "./tools/test.mjs"
    package = json.loads(files["package.json"])
    package["exports"]["./tools/test"] = None
    files["package.json"] = json.dumps(package).encode()
    with pytest.raises(CordisError, match="does not export"):
        adapt_harness_package(files)


def test_nested_patch_paths_are_anchored_to_patch_directory():
    files = bundle(dsh={"bundle": {"patch": "patches/plugin.yml"}})
    files["patches/plugin.yml"] = b"- insert:\n    - id: nested\n      name: ./plugin.mjs\n"
    files["patches/plugin.mjs"] = MODULE
    info = manifest(adapt_harness_package(files))
    assert info["harness"]["components"][0]["module"] == "./patches/plugin.mjs"


def test_patch_configuration_replaces_whole_value_and_disable_is_preserved():
    files = bundle("""- insert:
    - id: main
      name: '@example/harness-plugin'
      config: {old: true, retained: false}
- id: main
  name: '@example/harness-plugin'
  config: {replacement: 7}
  disabled: true
""")
    info = manifest(adapt_harness_package(files))
    assert info["config"]["components"]["main"] == {"enabled": False, "config": {"replacement": 7}}
    assert info["harness"]["components"][0]["enabled"] is False


def test_groups_keep_their_tree_and_allow_targeted_insertions():
    files = bundle("""- insert:
    - id: group
      group: true
      disabled: true
      config:
        - id: one
          name: '@example/harness-plugin'
- id: group
  insert:
    - id: two
      name: '@example/harness-plugin'
      config: {value: 2}
""")
    group = manifest(adapt_harness_package(files))["harness"]["components"][0]
    assert group["group"] is True and group["enabled"] is False
    assert [row["id"] for row in group["children"]] == ["one", "two"]
    assert group["children"][1]["config"] == {"value": 2}


def test_state_path_expression_is_data_not_executable_javascript():
    files = bundle("""- insert:
    - id: native-provider
      name: '@example/harness-plugin'
      config:
        socketPath: !!js dshHomePath('native/provider.sock')
        feature: on
        date: 2026-09-23
""")
    config = manifest(adapt_harness_package(files))["config"]["components"]["native-provider"]["config"]
    assert config == {"socketPath": {"$libreStatePath": "native/provider.sock"}, "feature": "on", "date": "2026-09-23"}


@pytest.mark.parametrize("patch", [
    "- insert: [{id: main, name: '@example/harness-plugin', config: !!js process.env}]",
    "- insert: [{id: main, name: '@example/harness-plugin', config: !!python/object/apply:os.system ['bad']}]",
    "- insert: [{id: main, name: '@example/harness-plugin', config: !!js dshHomePath('../private')}]",
    "- insert: [{id: main, name: '@example/harness-plugin', config: !!js dshHomePath('/private')}]",
    "- insert: [{id: main, id: duplicate, name: '@example/harness-plugin'}]",
    "- insert: [{id: main, name: '@example/harness-plugin', disabled: 1}]",
    "- insert: [{id: main, name: '@example/harness-plugin', isolate: true}]",
    "- insert: [{id: main, name: '@example/harness-plugin'}, {id: main, name: '@example/harness-plugin'}]",
    "- insert: [{id: main, name: ../escape.js}]",
    "- insert: [{id: main, name: 'undeclared-module'}]",
    "- id: absent\n  disabled: true",
    "- insert: [{id: main, name: '@example/harness-plugin'}]\n- id: main\n  name: 'different-module'",
    "- insert: [{id: main, name: '@example/harness-plugin', config: &cycle [*cycle]}]",
])
def test_unsafe_or_ambiguous_bundle_configuration_is_rejected(patch):
    with pytest.raises(CordisError):
        adapt_harness_package(bundle(patch))


def test_sdk_dependencies_are_provided_without_registry_fetch():
    files = bundle(dependencies={"@deepseek-ai/cordis": "^4.0.2", "@deepseek-ai/dsh-tools": "workspace:^"})
    assert packages.runtime_dependencies(json.loads(files["package.json"])) == {}
    assert manifest(adapt_harness_package(files))["harness"]["package"] == "@example/harness-plugin"


def test_local_folder_and_archive_adapt_to_identical_bytes(tmp_path):
    files = bundle()
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(content)
    assert packages._package_files(tmp_path) == packages._archive_files(archive(files))


async def test_dependencies_are_pinned_integrity_checked_and_resolved_per_parent():
    files = adapt_harness_package(bundle(dependencies={"helper": "^1.0.0", "consumer": "2.0.0"},
                                         scripts={"postinstall": "must-never-run"}))
    helper1 = {"package.json": b'{"name":"helper","version":"1.5.0","main":"index.js"}', "index.js": b"export const value=1;"}
    helper2 = {"package.json": b'{"name":"helper","version":"2.0.0","main":"index.js"}', "index.js": b"export const value=2;"}
    consumer = {"package.json": b'{"name":"consumer","version":"2.0.0","main":"index.js","dependencies":{"helper":"2.0.0"}}',
                "index.js": b"import {value} from 'helper'; export {value};"}
    docs = {}
    for name, version, content in [("helper", "1.5.0", helper1), ("helper", "2.0.0", helper2), ("consumer", "2.0.0", consumer)]:
        data = archive(content)
        entry = metadata(name, version, data)
        docs[f"/{name}/{version}"] = entry
        docs[f"/{name}/-/{version}.tgz"] = data
    docs["/helper"] = {"name": "helper", "versions": {version: docs[f"/helper/{version}"] for version in ["1.5.0", "2.0.0"]}}
    seen = []
    def respond(request):
        seen.append(str(request.url))
        assert "authorization" not in request.headers and "cookie" not in request.headers
        value = docs[request.url.path]
        return httpx.Response(200, content=value) if isinstance(value, bytes) else httpx.Response(200, json=value)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await packages._resolve_dependencies(files, client)
    graph = manifest(result)["harness"]["packages"]
    assert len(graph) == 4
    by_identity = {(node["name"], node["version"]): node for node in graph}
    root = graph[0]
    assert root["dependencies"]["helper"] == by_identity[("helper", "1.5.0")]["path"]
    assert by_identity[("consumer", "2.0.0")]["dependencies"]["helper"] == by_identity[("helper", "2.0.0")]["path"]
    assert result[by_identity[("helper", "2.0.0")]["path"] + "/index.js"] == helper2["index.js"]
    assert not any("node_modules" in path for path in result)
    assert all(url.startswith("https://registry.npmjs.org/") for url in seen)


@pytest.mark.parametrize("dependency", ["file:../private", "git+https://example.test/repo", "https://example.test/p.tgz", "workspace:*", "latest"])
async def test_dependency_sources_cannot_escape_public_version_resolution(dependency):
    files = adapt_harness_package(bundle(dependencies={"helper": dependency}))
    def no_network(request):
        pytest.fail("invalid dependency made a network request")
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
        with pytest.raises(CordisError, match="version ranges"):
            await packages._resolve_dependencies(files, client)


async def test_dependency_integrity_failure_cannot_create_reviewed_snapshot():
    files = adapt_harness_package(bundle(dependencies={"helper": "1.0.0"}))
    content = {"package.json": b'{"name":"helper","version":"1.0.0","main":"index.js"}', "index.js": b"export default 1"}
    data = archive(content)
    meta = metadata("helper", "1.0.0", data)
    meta["dist"]["integrity"] = "sha512-" + base64.b64encode(b"\0" * 64).decode()
    def respond(request):
        return httpx.Response(200, content=data) if request.url.path.endswith("tgz") else httpx.Response(200, json=meta)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(CordisError, match="integrity"):
            await packages._resolve_dependencies(files, client)


def test_generated_and_compiled_entry_paths_cannot_be_overwritten():
    files = bundle()
    files[HARNESS_ENTRY] = b"attacker supplied generated wrapper"
    with pytest.raises(CordisError, match="reserved"):
        adapt_harness_package(files)
    files = bundle(dsh={}, main="src/index.ts")
    files["src/index.ts"] = b"export default {}"
    with pytest.raises(CordisError, match="compiled"):
        adapt_harness_package(files)
