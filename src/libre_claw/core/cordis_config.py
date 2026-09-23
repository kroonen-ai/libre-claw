# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Bounded, non-executing validation and redaction for plugin configuration.

This deliberately supports a small JSON Schema subset. Unsupported keywords
fail closed instead of silently promising constraints that are not enforced.
Schema defaults are documentation; manifest.config supplies runtime defaults.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any


class CordisConfigError(ValueError):
    """Configuration or its schema cannot be accepted safely."""


_MISSING = object()
_ANNOTATIONS = {"type", "title", "description", "default", "examples", "writeOnly", "format", "enum", "const"}
_KEYWORDS = {
    "object": {"properties", "required", "additionalProperties", "minProperties", "maxProperties"},
    "array": {"items", "minItems", "maxItems", "uniqueItems"},
    "string": {"minLength", "maxLength"},
    "number": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"},
    "integer": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"},
    "boolean": set(),
    "null": set(),
}


def bounded_json(value: Any, *, limit: int = 64 * 1024, label: str = "Plugin config") -> None:
    remaining = 4096

    def visit(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if depth > 32 or remaining < 0:
            raise CordisConfigError(f"{label} is too deeply nested or contains too many values.")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CordisConfigError(f"{label} requires string object keys.")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and type(item) not in (bool, str, int, float):
            raise CordisConfigError(f"{label} must contain only JSON values.")
        elif isinstance(item, float) and not math.isfinite(item):
            raise CordisConfigError(f"{label} must contain only finite numbers.")

    visit(value, 0)
    try:
        size = len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode())
    except (ValueError, OverflowError) as exc:
        raise CordisConfigError(f"{label} contains an invalid JSON value.") from exc
    if size > limit:
        raise CordisConfigError(f"{label} exceeds {limit // 1024} KiB.")


def validate_schema(schema: Any) -> None:
    bounded_json(schema, limit=32 * 1024, label="Plugin config_schema")

    def visit(node: Any, depth: int) -> None:
        if depth > 16 or not isinstance(node, dict) or not isinstance(node.get("type"), str) or node["type"] not in _KEYWORDS:
            raise CordisConfigError("Plugin config_schema requires an explicit supported type at every node.")
        kind = node["type"]
        if set(node) - _ANNOTATIONS - _KEYWORDS[kind]:
            raise CordisConfigError("Plugin config_schema contains unsupported keywords for its type.")
        for name in ("title", "description"):
            if name in node and (not isinstance(node[name], str) or len(node[name]) > 4000):
                raise CordisConfigError("Plugin config_schema annotations must be strings of at most 4000 characters.")
        if "writeOnly" in node and not isinstance(node["writeOnly"], bool):
            raise CordisConfigError("Plugin config_schema writeOnly must be boolean.")
        if "format" in node and (node["format"] != "password" or kind != "string"):
            raise CordisConfigError("Plugin config_schema supports only the password string format.")
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            raise CordisConfigError("Plugin config_schema enum must be a nonempty array.")
        if "examples" in node and not isinstance(node["examples"], list):
            raise CordisConfigError("Plugin config_schema examples must be an array.")
        for name in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
            if name in node and (type(node[name]) is not int or node[name] < 0):
                raise CordisConfigError("Plugin config_schema size bounds must be nonnegative integers.")
        for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if name in node and type(node[name]) not in (int, float):
                raise CordisConfigError("Plugin config_schema numeric bounds must be numbers.")
        for low, high in (("minLength", "maxLength"), ("minItems", "maxItems"), ("minProperties", "maxProperties"), ("minimum", "maximum")):
            if low in node and high in node and node[low] > node[high]:
                raise CordisConfigError("Plugin config_schema has contradictory bounds.")
        if kind == "object":
            properties = node.get("properties", {})
            if not isinstance(properties, dict):
                raise CordisConfigError("Plugin config_schema properties must be an object.")
            required = node.get("required", [])
            if not isinstance(required, list) or not all(isinstance(key, str) and key in properties for key in required) or len(set(required)) != len(required):
                raise CordisConfigError("Plugin config_schema required must name unique declared properties.")
            for child in properties.values():
                visit(child, depth + 1)
            additional = node.get("additionalProperties", True)
            if isinstance(additional, dict):
                visit(additional, depth + 1)
            elif not isinstance(additional, bool):
                raise CordisConfigError("Plugin config_schema additionalProperties must be boolean or a schema.")
        if kind == "array":
            if "items" not in node:
                raise CordisConfigError("Plugin config_schema arrays require an items schema.")
            visit(node["items"], depth + 1)
            if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
                raise CordisConfigError("Plugin config_schema uniqueItems must be boolean.")
        for value in ([node["default"]] if "default" in node else []) + node.get("examples", []):
            _validate(value, node, partial=False, path="config_schema annotation")

    visit(schema, 0)
    if schema["type"] != "object":
        raise CordisConfigError("Plugin config_schema must describe an object.")


def _pointer(path: str, key: str | int) -> str:
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _equal(left: Any, right: Any) -> bool:
    # JSON Schema treats numbers by value but does not equate true with 1.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _validate(value: Any, schema: dict[str, Any], *, partial: bool, path: str) -> None:
    kind = schema["type"]
    valid = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": type(value) is bool,
        "integer": type(value) is int or (type(value) is float and value.is_integer()),
        "number": type(value) in (int, float), "null": value is None,
    }[kind]
    if not valid:
        raise CordisConfigError(f"{path or '/'} must be {kind}.")
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        raise CordisConfigError(f"{path or '/'} is not an allowed value.")
    if "const" in schema and not _equal(value, schema["const"]):
        raise CordisConfigError(f"{path or '/'} does not match its required value.")
    if kind in ("number", "integer"):
        bounds = (("minimum", lambda x: value >= x), ("maximum", lambda x: value <= x), ("exclusiveMinimum", lambda x: value > x), ("exclusiveMaximum", lambda x: value < x))
        if any(name in schema and not check(schema[name]) for name, check in bounds):
            raise CordisConfigError(f"{path or '/'} is outside its numeric bounds.")
    for name in ("minLength", "minItems", "minProperties"):
        if name in schema and len(value) < schema[name]:
            raise CordisConfigError(f"{path or '/'} has too few characters or items.")
    for name in ("maxLength", "maxItems", "maxProperties"):
        if name in schema and len(value) > schema[name]:
            raise CordisConfigError(f"{path or '/'} has too many characters or items.")
    if kind == "object":
        if not partial and any(key not in value for key in schema.get("required", [])):
            raise CordisConfigError(f"{path or '/'} is missing required configuration fields.")
        for key, item in value.items():
            child = schema.get("properties", {}).get(key, schema.get("additionalProperties", True))
            if child is False:
                raise CordisConfigError(f"{path or '/'} contains an undeclared field.")
            if isinstance(child, dict):
                _validate(item, child, partial=partial, path=_pointer(path, key))
    if kind == "array":
        if schema.get("uniqueItems") and any(_equal(item, previous) for index, item in enumerate(value) for previous in value[:index]):
            raise CordisConfigError(f"{path or '/'} requires unique array items.")
        for index, item in enumerate(value):
            _validate(item, schema["items"], partial=partial, path=_pointer(path, index))


def validate_config(value: Any, schema: dict[str, Any] | None, *, partial: bool = False) -> None:
    bounded_json(value)
    if not isinstance(value, dict):
        raise CordisConfigError("Plugin config must be a JSON object.")
    if schema is not None:
        _validate(value, schema, partial=partial, path="")


def _secret(schema: dict[str, Any]) -> bool:
    return schema.get("writeOnly") is True or schema.get("format") == "password"


def _child(schema: dict[str, Any], key: str) -> dict[str, Any]:
    value = schema.get("properties", {}).get(key, schema.get("additionalProperties", {}))
    return value if isinstance(value, dict) else {}


def effective_config(defaults: dict[str, Any], overrides: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    def merge(base: Any, update: Any, node: dict[str, Any]) -> Any:
        if _secret(node) and update is None:
            return _MISSING
        if isinstance(update, dict):
            base = base if isinstance(base, dict) else {}
            result = copy.deepcopy(base)
            for key, value in update.items():
                merged = merge(base.get(key), value, _child(node, key))
                if merged is _MISSING:
                    result.pop(key, None)
                else:
                    result[key] = merged
            return result
        return copy.deepcopy(update)

    return merge(defaults, overrides, schema or {})


def preserve_secrets(submitted: dict[str, Any], previous: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """Replace ordinary overrides, retain omitted secrets; explicit null clears.

    Arrays are replaced as a unit. An omitted array with secret descendants is
    retained as a unit, so redacted arrays can be saved without losing secrets.
    """
    def contains_secret(node: dict[str, Any]) -> bool:
        return _secret(node) or any(contains_secret(child) for child in node.get("properties", {}).values()) or (isinstance(node.get("items"), dict) and contains_secret(node["items"])) or (isinstance(node.get("additionalProperties"), dict) and contains_secret(node["additionalProperties"]))

    def restore(new: Any, old: Any, node: dict[str, Any]) -> Any:
        if new is _MISSING and (_secret(node) or (isinstance(old, list) and contains_secret(node))):
            return copy.deepcopy(old)
        if isinstance(old, dict) and (new is _MISSING or isinstance(new, dict)):
            result = copy.deepcopy(new) if isinstance(new, dict) else {}
            for key, old_value in old.items():
                restored = restore(result.get(key, _MISSING), old_value, _child(node, key))
                if restored is not _MISSING:
                    result[key] = restored
            return result if result or new is not _MISSING else _MISSING
        return new

    return restore(submitted, previous, schema or {})


def public_config(value: Any, schema: dict[str, Any] | None) -> tuple[Any, list[str]]:
    configured: list[str] = []

    def redact(item: Any, node: dict[str, Any], path: str) -> Any:
        if _secret(node):
            configured.append(path)
            return _MISSING
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                clean = redact(child, _child(node, key), _pointer(path, key))
                if clean is not _MISSING:
                    result[key] = clean
            return result
        if isinstance(item, list):
            # Arrays are atomic in the editor; omit any array with secrets.
            before = len(configured)
            result = [redact(child, node.get("items", {}), _pointer(path, index)) for index, child in enumerate(item)]
            return _MISSING if len(configured) > before else result
        return copy.deepcopy(item)

    result = redact(value, schema or {}, "")
    return ({} if result is _MISSING else result), configured


def public_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if schema is None:
        return None

    def clean(node: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(node)
        # Runtime defaults are already represented by redacted effective config.
        # Omitting these annotations avoids nested/array examples leaking values.
        result.pop("default", None)
        result.pop("examples", None)
        # Constraint/default/example values can themselves embed credentials.
        if _secret(node):
            for name in ("enum", "const"):
                result.pop(name, None)
        else:
            for name in ("const",):
                if name in result and isinstance(result[name], (dict, list)):
                    result[name] = public_config(result[name], node)[0]
            for name in ("enum",):
                if name in result:
                    result[name] = [public_config(item, node)[0] if isinstance(item, (dict, list)) else item for item in result[name]]
        if "properties" in result:
            result["properties"] = {key: clean(value) for key, value in node["properties"].items()}
        for name in ("items", "additionalProperties"):
            if isinstance(node.get(name), dict):
                result[name] = clean(node[name])
        return result

    return clean(schema)
