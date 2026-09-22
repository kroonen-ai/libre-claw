# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from click.testing import CliRunner

from libre_claw.workflow_cli import review_group, workflow_group, worktree_group


def test_every_workflow_action_explains_its_purpose_without_executing(monkeypatch):
    def unexpected_dispatch(*args, **kwargs):
        raise AssertionError("Help must not load or mutate a workspace.")

    monkeypatch.setattr("libre_claw.workflow_cli._dispatch", unexpected_dispatch)
    runner = CliRunner()
    for group_name, group in (("worktree", worktree_group), ("review", review_group)):
        for name, command in group.commands.items():
            assert command.help, f"Missing help for {group_name} {name}"
            result = runner.invoke(workflow_group, [group_name, name, "--help"])
            assert result.exit_code == 0, result.output
            assert command.help in " ".join(result.stdout.split())


def test_worktree_help_distinguishes_preview_apply_and_remove():
    runner = CliRunner()
    descriptions = {}
    for action in ("preview", "apply", "remove"):
        result = runner.invoke(workflow_group, ["worktree", action, "--help"])
        assert result.exit_code == 0, result.output
        descriptions[action] = " ".join(result.stdout.split())
    assert "before transferring" in descriptions["preview"]
    assert "saved preview" in descriptions["apply"]
    assert "--confirm" in descriptions["apply"]
    assert "clean worktree with no active tasks" in descriptions["remove"]
