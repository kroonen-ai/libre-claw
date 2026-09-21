# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from app.catalog import available
from app.client import select


class CatalogTests(unittest.TestCase):
    def test_provider_catalog(self):
        entries = [{"id": " vendor/Future "}, {"id": "next"}, {"id": "vendor/Future"}, {}, {"id": None}, {"id": " "}, None]
        self.assertEqual(available(entries), ["vendor/Future", "next"])
        self.assertEqual(available([]), [])

    def test_selection(self):
        entries = [{"id": "future-2030"}]
        self.assertEqual(select(entries), "future-2030")
        self.assertEqual(select([], " Manual/ID "), "Manual/ID")
        for entries, model in (([], None), ([], ""), ([{"id": "a"}], " ")):
            with self.assertRaises(ValueError):
                select(entries, model)

    def test_project_instructions(self):
        for name in ("catalog.py", "client.py"):
            tree = ast.parse((Path("app") / name).read_text())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    self.assertTrue(ast.get_docstring(node))
                    self.assertNotIn("\n", ast.get_docstring(node))
        self.assertEqual(Path("CHANGELOG.md").read_text().count("Dynamic catalog discovery."), 1)
