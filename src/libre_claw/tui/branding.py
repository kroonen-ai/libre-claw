# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from libre_claw.core.branding import WORDMARK_ROWS
from libre_claw.core.themes import ThemePalette


def pixel_wordmark_text() -> str:
    """Pack two pixel rows per terminal cell to preserve the website proportions."""
    rows = (*WORDMARK_ROWS, "0" * len(WORDMARK_ROWS[0]))
    pixels = {"00": " ", "10": "▀", "01": "▄", "11": "█"}
    return "\n".join(
        "".join(pixels[top + bottom] for top, bottom in zip(rows[index], rows[index + 1])).rstrip()
        for index in range(0, len(rows), 2)
    )


@dataclass(frozen=True)
class PixelWordmark:
    palette: ThemePalette

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        rows = pixel_wordmark_text().splitlines()
        if options.max_width < len(WORDMARK_ROWS[0]):
            yield Text("LIBRE CLAW", style=f"bold {self.palette.accent}")
            return
        for index, row in enumerate(rows):
            if self.palette.is_light:
                color = self.palette.accent if index < 2 else self.palette.accent_strong
            else:
                color = self.palette.text if index == 0 else self.palette.accent_strong if index < 3 else self.palette.accent
            yield Text(row, style=color, no_wrap=True)
