# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from pagination import paginate


class PaginationTests(unittest.TestCase):
    def test_boundaries_and_order(self):
        for count in range(12):
            for size in range(1, 6):
                items = list(range(count))
                result = paginate(items, size)
                self.assertEqual([item for page in result for item in page], items)
                self.assertTrue(all(1 <= len(page) <= size for page in result))
                self.assertEqual(items, list(range(count)))

    def test_invalid_size(self):
        for size in (0, -1, -10):
            with self.assertRaises(ValueError):
                paginate([1, 2], size)

    def test_empty_input(self):
        self.assertEqual(paginate([], 3), [])
