# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from libre_claw.core.soul import DEFAULT_SOUL_TEMPLATE, SoulError, SoulStore


def test_soul_store_loads_user_project_and_root_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".libre-claw").mkdir(parents=True)
    (project / ".libre-claw").mkdir(parents=True)
    (home / ".libre-claw" / "soul.md").write_text("# User Soul\n\nBe crisp.", encoding="utf-8")
    (project / ".libre-claw" / "soul.md").write_text("# Project Soul\n\nKnow the repo.", encoding="utf-8")
    (project / "soul.md").write_text("# Root Soul\n\nUse the product voice.", encoding="utf-8")

    store = SoulStore(project, home=home)
    fragments = store.load()

    assert [fragment.scope for fragment in fragments] == ["user", "project", "project-root"]
    assert "Be crisp." in fragments[0].prompt_text
    assert "Know the repo." in fragments[1].prompt_text
    assert "Use the product voice." in fragments[2].prompt_text


def test_soul_store_ignores_missing_and_empty_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".libre-claw").mkdir(parents=True)
    (project / ".libre-claw" / "soul.md").write_text("  \n", encoding="utf-8")

    store = SoulStore(project, home=home)

    assert store.load() == []
    assert "missing" in store.status_text()


def test_soul_store_creates_templates(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    store = SoulStore(project, home=home)

    user_path = store.ensure_template("--user")
    project_path = store.ensure_template("--project")
    root_path = store.ensure_template("--root")

    assert user_path == home / ".libre-claw" / "soul.md"
    assert project_path == project / ".libre-claw" / "soul.md"
    assert root_path == project / "soul.md"
    assert DEFAULT_SOUL_TEMPLATE in user_path.read_text(encoding="utf-8")
    assert DEFAULT_SOUL_TEMPLATE in project_path.read_text(encoding="utf-8")
    assert DEFAULT_SOUL_TEMPLATE in root_path.read_text(encoding="utf-8")


def test_soul_store_rejects_unknown_template_scope(tmp_path: Path) -> None:
    store = SoulStore(tmp_path / "project", home=tmp_path / "home")

    with pytest.raises(SoulError):
        store.ensure_template("--elsewhere")
