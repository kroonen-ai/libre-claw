# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

import json

from click.testing import CliRunner

from libre_claw.cli import main


def test_cordis_enable_keeps_stdout_json_and_sends_guidance_to_stderr(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    payload = {
        "id": "example", "enabled": True,
        "grants": {"allow_network": False, "read_paths": [], "write_paths": []},
    }

    class Manager:
        def enable(self, plugin_id, workspace, **grants):
            assert plugin_id == "example"
            assert workspace == tmp_path.resolve()
            assert grants == {"allow_network": False, "read_paths": (), "write_paths": ()}
            return payload

    monkeypatch.setattr("libre_claw.cordis_cli.manager_for", lambda config: Manager())
    result = CliRunner().invoke(main, ["--working-directory", str(tmp_path), "cordis", "enable", "example"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert "next message" not in result.stdout
    assert "next message" in result.stderr
    assert "normal approval" in result.stderr
