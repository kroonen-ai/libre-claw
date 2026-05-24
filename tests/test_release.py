# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from pathlib import Path

from libre_claw import __version__

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_project_version_matches_package_version() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["version"] == __version__
    assert data["project"]["license"]["text"] == "Apache-2.0"
    assert data["project"]["authors"][0]["name"] == "Kroonen AI Inc."


def test_release_docs_reference_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f"## {__version__}" in changelog
    assert f"Version `{__version__}`" in readme


def test_license_is_apache_with_kroonen_attribution() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Apache License" in license_text
    assert "Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)" in license_text


def test_source_files_have_kroonen_license_header() -> None:
    expected = (
        "# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)\n"
        "# SPDX-License-Identifier: Apache-2.0\n"
    )
    paths = [
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted((ROOT / "tests").rglob("*.py")),
        ROOT / "config" / "default.toml",
        ROOT / "src" / "libre_claw" / "default.toml",
        ROOT / "pyproject.toml",
    ]

    missing = [str(path.relative_to(ROOT)) for path in paths if not path.read_text(encoding="utf-8").startswith(expected)]

    assert missing == []
