# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path

from libre_claw.core.instructions import InstructionLoader, render_instructions, tool_paths


def test_instructions_scope_precedence_and_legacy_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "feature"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    global_file = tmp_path / "global.md"
    global_file.write_text("global rule")
    (tmp_path / "AGENTS.md").write_text("must not load above repository")
    (repo / "AGENT.md").write_text("legacy root")
    (repo / "src" / "AGENT.md").write_text("ignored legacy")
    (repo / "src" / "AGENTS.md").write_text("canonical nested")
    (nested / "AGENTS.md").write_text("most specific")
    instructions = InstructionLoader(nested, global_path=global_file).load()
    assert [item.text for item in instructions] == ["global rule", "legacy root", "canonical nested", "most specific"]
    rendered = render_instructions(instructions)
    assert f"Scope: {nested}" in rendered
    assert f"Source: {global_file}" in rendered
    assert "Direct user instructions" in rendered


def test_instructions_paths_are_scoped_without_sibling_scanning(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "AGENTS.md").write_text("rule a")
    (tmp_path / "b" / "AGENTS.md").write_text("rule b")
    loader = InstructionLoader(tmp_path, global_path=tmp_path / "absent")
    assert loader.load() == []
    assert [item.text for item in loader.load([tmp_path / "a" / "new.py"])] == ["rule a"]
    assert loader.load([tmp_path.parent / "elsewhere.py"]) == []


def test_instruction_reads_skip_links_and_pipes_and_bound_size(tmp_path: Path) -> None:
    global_file = tmp_path / "global.md"
    global_file.write_text("x" * 200)
    (tmp_path / "AGENTS.md").symlink_to(global_file)
    loader = InstructionLoader(tmp_path, global_path=global_file, max_file_bytes=30, max_total_bytes=100)
    instructions = loader.load()
    assert len(instructions) == 1
    assert instructions[0].text.startswith("x" * 30)
    assert "truncated" in instructions[0].text
    (tmp_path / "AGENTS.md").unlink()
    os.mkfifo(tmp_path / "AGENTS.md")
    assert len(loader.load()) == 1


def test_instruction_refresh_reads_changed_contents(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("first")
    loader = InstructionLoader(tmp_path, global_path=tmp_path / "absent")
    first = loader.load()[0]
    path.write_text("second")
    assert loader.load()[0].fingerprint != first.fingerprint


def test_instruction_paths_include_patch_targets(tmp_path: Path) -> None:
    paths = tool_paths({"edits": [{"path": "a/file.py"}, {"path": "b/file.py"}]}, tmp_path)
    assert paths == [tmp_path / "a/file.py", tmp_path / "b/file.py"]


def test_directory_budget_always_keeps_repository_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("root constraint")
    nested = tmp_path / "one" / "two" / "three"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("nested constraint")
    loader = InstructionLoader(nested, global_path=tmp_path / "absent", max_directories=2)
    assert [item.text for item in loader.load()] == ["root constraint", "nested constraint"]
