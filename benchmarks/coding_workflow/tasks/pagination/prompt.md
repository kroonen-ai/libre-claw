<!-- Copyright 2026 Kroonen AI (https://kroonen.ai); SPDX-License-Identifier: Apache-2.0 -->

Fix `paginate(items, size)` in pagination.py. It must return successive nonempty lists of at most size elements, preserve order, leave the input unchanged, and return [] for empty input. A size less than one must raise ValueError. Preserve the public signature and do not add dependencies or change the tests. Run the unittest suite before finishing.
