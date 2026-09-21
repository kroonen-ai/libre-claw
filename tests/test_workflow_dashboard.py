# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from libre_claw.web.dashboard import dashboard_html


class DashboardElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        self.ids.extend(value for name, value in attrs if name == "id")


def test_dashboard_control_ids_are_unique_and_all_static_bindings_exist():
    html = dashboard_html()
    elements = DashboardElements()
    elements.feed(html)
    assert len(elements.ids) == len(set(elements.ids))
    bindings = set(re.findall(r'\$\("([^"$]+)"\)', html))
    assert bindings <= set(elements.ids)
    assert {"tabChanges", "tabWorktrees", "tabPlan", "messageAction", "runWorktree", "runMode"} <= set(elements.ids)


def test_dashboard_javascript_is_valid(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard JavaScript verification")
    source = tmp_path / "dashboard.js"
    source.write_text("\n".join(re.findall(r"<script>(.*?)</script>", dashboard_html(), re.S)))
    completed = subprocess.run([node, "--check", str(source)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr


def test_diff_line_numbers_exclude_headers_and_follow_each_hunk(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for line-number verification")
    html = dashboard_html()
    source = html[html.index("    function parseDiffLines("):html.index("    function reviewActions(")]
    patch = "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -4,2 +4,3 @@\n context\n-old\n+new\n+extra\n\\ No newline at end of file\n@@ -20 +21 @@\n-final\n+changed\n"
    source += "\nprocess.stdout.write(JSON.stringify(parseDiffLines(" + json.dumps(patch) + ")));"
    script = tmp_path / "lines.js"; script.write_text(source)
    result = subprocess.run([node, str(script)], check=True, text=True, capture_output=True)
    rows = json.loads(result.stdout)
    assert [(row["kind"], row["oldLine"], row["newLine"]) for row in rows] == [
        ("context", 4, 4), ("remove", 5, None), ("add", None, 5), ("add", None, 6),
        ("remove", 20, None), ("add", None, 21),
    ]
