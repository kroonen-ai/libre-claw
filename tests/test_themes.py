# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from libre_claw.core.themes import (
    THEME_PALETTES,
    dashboard_theme_id,
    normalize_theme,
    tui_theme_palette,
)


@pytest.mark.parametrize("value", [None, "", "unknown-theme", "default", "dark", "libre-dark", "libre-default"])
def test_default_and_generic_dark_themes_use_libre(value: str | None) -> None:
    assert normalize_theme(value) == "libre"
    assert dashboard_theme_id(value) == "libre"
    assert tui_theme_palette(value).theme_id == "libre"


@pytest.mark.parametrize("value", ["light", "libre-light", " LIBRE-LIGHT "])
def test_generic_light_theme_uses_libre_light(value: str) -> None:
    assert dashboard_theme_id(value) == "libre-light"
    assert tui_theme_palette(value).is_light is True


@pytest.mark.parametrize("theme_id", list(THEME_PALETTES))
def test_named_themes_remain_selectable(theme_id: str) -> None:
    assert normalize_theme(theme_id) == theme_id


@pytest.mark.parametrize("alias,expected", [
    ("clear", "lobster-light"),
    ("lobster-clear", "lobster-light"),
    ("codex-lobster", "lobster"),
    ("codex-lobster-light", "lobster-light"),
    ("ayu-mirage", "ayu"),
    ("rosepine", "rose-pine"),
    ("rose-pine-moon", "rose-pine"),
    ("one-dark", "one-dark-pro"),
])
def test_named_legacy_aliases_are_preserved(alias: str, expected: str) -> None:
    assert normalize_theme(alias) == expected


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[offset:offset + 2], 16) / 255 for offset in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("theme_id", list(THEME_PALETTES))
def test_theme_text_and_controls_have_readable_contrast(theme_id: str) -> None:
    palette = tui_theme_palette(theme_id)
    for background in (palette.background, palette.surface, palette.surface_2, palette.code):
        for foreground in (palette.text, palette.soft, palette.muted):
            assert _contrast_ratio(foreground, background) >= 4.5
    assert _contrast_ratio(palette.on_accent, palette.accent) >= 4.5
    assert _contrast_ratio(palette.on_accent_strong, palette.accent_strong) >= 4.5


@pytest.mark.parametrize("theme_id", list(THEME_PALETTES))
def test_every_theme_has_complete_shared_design_tokens(theme_id: str) -> None:
    palette = tui_theme_palette(theme_id)
    for name in ("line", "line_strong", "soft", "panel_strong", "on_accent", "on_accent_strong", "code"):
        value = getattr(palette, name)
        assert value.startswith("#") and len(value) == 7
        int(value[1:], 16)
    assert palette.line != palette.accent
