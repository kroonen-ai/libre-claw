# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Adapt declarative Harness bundles without executing their modules or YAML."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from libre_claw.core.cordis import MANIFEST_NAME, MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, CordisError

HARNESS_ENTRY = "libre-harness-entry.mjs"
DEPENDENCY_DIRECTORY = "libre-harness-dependencies"
SDK_PACKAGES = frozenset({
    "@deepseek-ai/cordis", "@deepseek-ai/cosmokit", "@deepseek-ai/schemastery", "zod",
    "@deepseek-ai/dsh-tools", "@deepseek-ai/dsh-user-questions",
    "@deepseek-ai/dsh-session-projection", "@deepseek-ai/dsh-llm",
})
NATIVE_PROVIDER_PACKAGE = "@libre-webui/dsh-native-provider"
NATIVE_PROVIDER_VERSION = "0.1.1"
NATIVE_PROVIDER_FILES = {
    "runtime/native-provider-plugin.js": "afafa70d7ae8be58d2590cac4df82e30521b607c290195d3c2d79d213d8d310a",
    "runtime/native-provider-protocol.js": "7cdc099eab10a3d14c4b3c447c659ceaf1c268b5401a8a46fedb9adeb9ee900c",
}
NATIVE_PROVIDER_FINGERPRINT = hashlib.sha256(json.dumps(NATIVE_PROVIDER_FILES, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
_PACKAGE = re.compile(r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")
_STATE_PATH = re.compile(r"\s*(?:ctx\.)?dshHomePath\(\s*(['\"])([^'\"\\\n]+)\1\s*\)\s*\Z")
_NO_EXPORT = object()


def package_manifest(files: dict[str, bytes]) -> dict[str, Any]:
    """Read an npm manifest as bounded JSON data, never import its entry."""
    try:
        package = json.loads(files["package.json"])
    except (KeyError, ValueError, UnicodeError):
        raise CordisError("A Harness or Cordis package requires a valid package.json.") from None
    if not isinstance(package, dict):
        raise CordisError("Plugin package.json must be an object.")
    name, version = package.get("name"), package.get("version")
    if not isinstance(name, str) or len(name) > 214 or not _PACKAGE.fullmatch(name):
        raise CordisError("Plugin package.json requires a valid npm package name.")
    if not isinstance(version, str) or not version.strip() or len(version) > 160:
        raise CordisError("Plugin package.json requires a version.")
    return package


def relative_path(value: Any, label: str) -> str:
    """Validate package paths without filesystem resolution or symlink traversal."""
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value or "\0" in value:
        raise CordisError(f"Invalid Harness {label} path.")
    if value.startswith("./"):
        value = value[2:]
    parts = value.split("/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or any(part in {"", ".", ".."} for part in parts):
        raise CordisError(f"Harness {label} path must remain inside the package.")
    return value


def _export_target(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Node conditional exports are ordered: the first matching condition
        # wins, even when a later condition would also match this runtime.
        for condition, candidate in value.items():
            if condition in {"import", "node", "default"}:
                target = _export_target(candidate)
                if target is not _NO_EXPORT:
                    return target
    if isinstance(value, list):
        for candidate in value:
            if isinstance(target := _export_target(candidate), str):
                return target
        return None
    return _NO_EXPORT


def _subpath_export(exports: dict[str, Any], subpath: str) -> Any:
    if subpath in exports:
        return _export_target(exports[subpath])
    matches = []
    for pattern, target in exports.items():
        if pattern.count("*") != 1:
            continue
        prefix, suffix = pattern.split("*")
        if subpath.startswith(prefix) and subpath.endswith(suffix) and len(subpath) >= len(prefix) + len(suffix):
            matches.append((len(prefix), len(pattern), pattern, target))
    if not matches:
        return _NO_EXPORT
    _, _, pattern, target = max(matches, key=lambda item: item[:2])
    prefix, suffix = pattern.split("*")
    replacement = subpath[len(prefix):len(subpath) - len(suffix) if suffix else None]
    resolved = _export_target(target)
    return resolved.replace("*", replacement) if isinstance(resolved, str) else resolved


def package_entry(package: dict[str, Any], files: dict[str, bytes], subpath: str = ".") -> str:
    """Resolve a compiled Node entry using exports, then main for package roots."""
    exports = package.get("exports")
    target = None
    if exports is not None:
        if isinstance(exports, dict) and any(str(key).startswith(".") for key in exports):
            target = _subpath_export(exports, subpath)
        elif subpath == ".":
            target = _export_target(exports)
        if not isinstance(target, str):
            raise CordisError("Harness package does not export the requested module.")
    elif subpath == ".":
        target = package.get("main", "index.js")
    else:
        target = subpath
    path = relative_path(target, "module")
    if PurePosixPath(path).suffix not in {".js", ".mjs", ".cjs"} or path not in files:
        raise CordisError("Harness packages must include compiled .js, .mjs, or .cjs modules; install scripts are not run.")
    return path


class _BundleLoader(yaml.SafeLoader):
    """Reject executable tags, duplicate mappings, and excessive YAML aliases."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._nodes = 0
        self._depth = 0
        self._aliases = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        self._nodes += 1
        self._depth += 1
        if self.check_event(yaml.AliasEvent):
            self._aliases += 1
        if self._nodes > 10_000 or self._depth > 32 or self._aliases > 64:
            raise CordisError("Harness YAML exceeds its complexity limit.")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise CordisError("Harness YAML object keys must be unique strings.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _state_path(loader: _BundleLoader, node: Any) -> dict[str, str]:
    expression = loader.construct_scalar(node)
    match = _STATE_PATH.fullmatch(expression)
    if match is None:
        raise CordisError("Harness JavaScript YAML expressions are not executed. Only dshHomePath('relative/path') is supported.")
    return {"$libreStatePath": relative_path(match[2], "state")}


_BundleLoader.add_constructor("tag:yaml.org,2002:js", _state_path)
# js-yaml's JSON schema treats yes/no/on/off and dates as strings.
_BundleLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in rules if tag not in {
        "tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool",
    }]
    for key, rules in _BundleLoader.yaml_implicit_resolvers.items()
}
_BundleLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))


def _bounded_json(value: Any) -> None:
    nodes = 0
    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 20_000 or depth > 32:
            raise CordisError("Harness configuration exceeds its complexity limit.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise CordisError("Harness configuration keys must be strings.")
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise CordisError("Harness configuration must contain JSON values.")
    try:
        visit(value, 0)
        if len(json.dumps(value, allow_nan=False).encode()) > 256 * 1024:
            raise CordisError("Harness configuration exceeds its byte limit.")
    except (ValueError, RecursionError, OverflowError):
        raise CordisError("Harness configuration must be bounded JSON data.") from None


def _patch_rows(data: bytes) -> list[dict[str, Any]]:
    if len(data) > 256 * 1024:
        raise CordisError("Harness patch exceeds its byte limit.")
    try:
        patches = yaml.load(data.decode("utf-8"), Loader=_BundleLoader)
    except (yaml.YAMLError, UnicodeError):
        raise CordisError("Harness patch must contain valid safe YAML.") from None
    _bounded_json(patches)
    if not isinstance(patches, list) or len(patches) > 256:
        raise CordisError("Harness patch must be a list of at most 256 operations.")
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    fields = {"id", "name", "config", "disabled", "group"}

    def add(entries: Any, target: list[dict[str, Any]], depth: int = 0) -> None:
        if not isinstance(entries, list) or depth > 16:
            raise CordisError("Harness inserted entries must be a bounded list.")
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - fields:
                raise CordisError("Harness entry contains unsupported loader metadata.")
            identity = entry.get("id")
            if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in index:
                raise CordisError("Harness entries require unique literal IDs.")
            if len(index) >= 128:
                raise CordisError("Harness bundle contains too many components.")
            row = dict(entry)
            if type(row.get("group", False)) is not bool or (row.get("disabled") is not None and type(row["disabled"]) is not bool):
                raise CordisError("Harness group and disabled fields must be literal booleans.")
            index[identity] = row
            if row.get("group"):
                if row.get("name") not in {None, "group", "cordis:group", "@deepseek-ai/cordis-plugin-group"}:
                    raise CordisError("Harness groups must use the standard Cordis group plugin.")
                row["config"] = []
                add(entry.get("config", []), row["config"], depth + 1)
            elif not isinstance(row.get("name"), str):
                raise CordisError("Harness plugin entries require a module name.")
            target.append(row)

    for patch in patches:
        if not isinstance(patch, dict) or set(patch) - fields - {"insert"}:
            raise CordisError("Harness patch contains unsupported loader metadata.")
        if "insert" in patch:
            if set(patch) - {"id", "insert"}:
                raise CordisError("Harness insert patches accept only id and insert.")
            target = rows
            if "id" in patch:
                group = index.get(patch["id"]) if isinstance(patch["id"], str) else None
                if group is None or not group.get("group"):
                    raise CordisError("Harness insert target must name an existing group in this bundle.")
                target = group["config"]
            add(patch["insert"], target)
            continue
        identity = patch.get("id")
        row = index.get(identity) if isinstance(identity, str) else None
        if row is None:
            raise CordisError("Harness patch target must exist in the reviewed bundle.")
        if "name" in patch and patch["name"] != row.get("name"):
            raise CordisError("Harness patch module assertion does not match its target.")
        if "group" in patch and patch["group"] != row.get("group", False):
            raise CordisError("Harness patches cannot change a component into a group.")
        if "disabled" in patch:
            if patch["disabled"] is not None and type(patch["disabled"]) is not bool:
                raise CordisError("Harness disabled fields must be literal booleans.")
            row["disabled"] = patch["disabled"]
        if "config" in patch:
            if row.get("group"):
                raise CordisError("Use insert patches to extend a Harness group.")
            row["config"] = patch["config"]
    return rows


def runtime_dependencies(package: dict[str, Any]) -> dict[str, str]:
    """Return public npm runtime edges, leaving SDK packages to the host resolver."""
    dependencies: dict[str, str] = {}
    for key in ("peerDependencies", "dependencies", "optionalDependencies"):
        values = package.get(key, {})
        if not isinstance(values, dict):
            raise CordisError("Plugin runtime dependencies must be an object.")
        for name, version in values.items():
            if not isinstance(name, str) or not _PACKAGE.fullmatch(name) or not isinstance(version, str) or len(version) > 256:
                raise CordisError("Plugin runtime dependencies require public npm names and version ranges.")
            if name not in SDK_PACKAGES:
                dependencies[name] = version
    return dependencies


def adapt_harness_package(files: dict[str, bytes]) -> dict[str, bytes]:
    """Generate a reviewed manifest/wrapper while leaving original modules unchanged."""
    if MANIFEST_NAME in files:
        return files
    package = package_manifest(files)
    if HARNESS_ENTRY in files or any(name.startswith(DEPENDENCY_DIRECTORY + "/") for name in files):
        raise CordisError("Harness package uses a reserved generated file path.")
    dsh = package.get("dsh", {})
    if not isinstance(dsh, dict):
        raise CordisError("The package dsh field must be an object.")
    bundle = dsh.get("bundle")
    patch_directory = PurePosixPath(".")
    if bundle is not None:
        if not isinstance(bundle, dict) or not isinstance(bundle.get("patch"), str):
            raise CordisError("Harness bundle metadata requires a patch path.")
        patch = relative_path(bundle["patch"], "patch")
        if patch not in files:
            raise CordisError("Harness bundle patch is missing from the package.")
        patch_directory = PurePosixPath(patch).parent
        rows = _patch_rows(files[patch])
    else:
        rows = [{"id": "main", "name": "./" + package_entry(package, files)}]
    declared = set(runtime_dependencies(package)) | SDK_PACKAGES
    defaults: dict[str, Any] = {}

    def component(row: dict[str, Any], parent: str = "") -> dict[str, Any]:
        configuration_id = parent + "/" + row["id"] if parent else row["id"]
        result = {"id": row["id"], "enabled": not bool(row.get("disabled"))}
        if row.get("group"):
            defaults[configuration_id] = {"enabled": result["enabled"]}
            return {**result, "group": True, "children": [component(child, configuration_id) for child in row["config"]]}
        name = row["name"]
        if name.startswith("./"):
            module = relative_path((patch_directory / name).as_posix(), "module")
            if module not in files or PurePosixPath(module).suffix not in {".js", ".mjs", ".cjs"}:
                raise CordisError("Harness module must be compiled JavaScript inside the reviewed package.")
            module = "./" + module
        elif name == package["name"] or name.startswith(package["name"] + "/"):
            subpath = "." + name[len(package["name"]):]
            module = "./" + package_entry(package, files, subpath)
        else:
            parts = name.split("/")
            dependency = "/".join(parts[:2]) if name.startswith("@") else parts[0]
            if dependency not in declared or not _PACKAGE.fullmatch(dependency):
                raise CordisError("Harness components may reference only declared npm dependencies or package-local modules.")
            module = name
        config = row.get("config", {})
        defaults[configuration_id] = {"enabled": result["enabled"], "config": config}
        return {**result, "module": module, "config": config}

    components = [component(row) for row in rows]
    slug = re.sub(r"[^a-z0-9]+", "-", package["name"].lower()).strip("-")
    digest = hashlib.sha256(package["name"].encode()).hexdigest()[:8]
    identity = "h-" + slug[:12].rstrip("-") + "-" + digest
    description = package.get("description", "")
    if not isinstance(description, str) or len(description) > 4000:
        raise CordisError("Harness package description must be a string of at most 4000 characters.")
    manifest = {
        "format": "deepseek-harness", "id": identity, "name": package["name"][:160],
        "version": package["version"], "description": description, "entry": HARNESS_ENTRY,
        "tools": [], "config": {"components": defaults},
        "harness": {"package": package["name"], "components": components,
                    "packages": [{"name": package["name"], "version": package["version"], "path": ".", "dependencies": {}}]},
    }
    if package["name"] == NATIVE_PROVIDER_PACKAGE:
        manifest["name"] = "Libre WebUI bridge"
        manifest["description"] = "Use Libre Claw models in Libre WebUI over a private local connection."
        manifest["harness"].update(adapter="native-provider", adapter_version=1,
                                   adapter_fingerprint=NATIVE_PROVIDER_FINGERPRINT)
        validate_native_adapter(files, manifest)
    literal = json.dumps(components, ensure_ascii=True, separators=(",", ":"))
    wrapper = (
        "// Generated adapter. Original package modules are unchanged.\n"
        "export const name = 'libre-harness-bundle';\n"
        "export const inject = ['libre'];\n"
        f"const components = {literal};\n"
        "export async function apply(ctx, config = {}) {\n"
        "  await ctx.libre.mountHarnessComponents(components, config.components ?? {});\n"
        "}\n"
    ).encode()
    result = {**files, MANIFEST_NAME: json.dumps(manifest, ensure_ascii=True, sort_keys=True).encode(), HARNESS_ENTRY: wrapper}
    check_package_bounds(result)
    return result


def validate_native_adapter(files: dict[str, bytes], manifest: dict[str, Any]) -> None:
    """Allow the host adapter only for the reviewed native-provider implementation."""
    harness = manifest.get("harness", {})
    if "adapter" not in harness:
        return
    package = package_manifest(files)
    components = harness.get("components", [])
    if (harness.get("adapter") != "native-provider" or type(harness.get("adapter_version")) is not int
            or harness.get("adapter_version") != 1 or harness.get("adapter_fingerprint") != NATIVE_PROVIDER_FINGERPRINT
            or package["name"] != NATIVE_PROVIDER_PACKAGE or package["version"] != NATIVE_PROVIDER_VERSION
            or manifest.get("version") != NATIVE_PROVIDER_VERSION or runtime_dependencies(package)
            or len(components) != 1 or components[0].get("module") != "./runtime/native-provider-plugin.js"
            or components[0].get("group") or harness.get("package") != NATIVE_PROVIDER_PACKAGE
            or any(hashlib.sha256(files.get(path, b"")).hexdigest() != digest for path, digest in NATIVE_PROVIDER_FILES.items())):
        raise CordisError("This native-provider build is not the reviewed 0.1.1 implementation; its host adapter cannot be enabled.")


def check_package_bounds(files: dict[str, bytes]) -> None:
    """Apply snapshot limits to generated files and the entire dependency graph."""
    if len(files) > MAX_PACKAGE_FILES or sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
        raise CordisError("Plugin and its dependencies exceed the allowed snapshot size or file count.")
