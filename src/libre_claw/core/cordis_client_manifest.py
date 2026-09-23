# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Declarative client package discovery; importing never executes package code."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


_PACKAGE = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*\Z")


def validate_client_declaration(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"entry", "package_name"}:
        raise ValueError("Client declarations require entry and package_name.")
    entry, package = value["entry"], value["package_name"]
    if (not isinstance(entry, str) or not entry or len(entry) > 512
            or "\\" in entry or "\0" in entry or PurePosixPath(entry).is_absolute()
            or any(not part or part.startswith(".") for part in entry.split("/"))
            or PurePosixPath(entry).suffix not in {".mjs", ".js", ".cjs"}):
        raise ValueError("Client entry must be compiled JavaScript inside the package.")
    if not isinstance(package, str) or len(package) > 214 or not _PACKAGE.fullmatch(package):
        raise ValueError("Client package_name must be an exact npm package identity.")
    return {"entry": entry, "package_name": package}


def detect_client_declaration(package: dict[str, Any], files: dict[str, bytes]) -> dict[str, str] | None:
    dsh = package.get("dsh")
    client = dsh.get("client") if isinstance(dsh, dict) else None
    if client is None:
        return None
    if not isinstance(client, dict) or client.get("platform") != "web":
        raise ValueError("Only declared web client extensions are supported.")
    # Reuse the bounded package exports resolver after its module has loaded.
    from libre_claw.core.cordis_harness import package_entry

    return validate_client_declaration({
        "entry": package_entry(package, files, "./client"), "package_name": package["name"],
    })
