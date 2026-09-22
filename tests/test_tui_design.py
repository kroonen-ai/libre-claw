# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from pygments.token import Token
from rich.console import Console
from rich.color import Color
from textual.widgets import Button, Input

from libre_claw.config import load_config
from libre_claw.core.agent import AgentPermissionRequest
from libre_claw.core.branding import WORDMARK_ROWS
from libre_claw.core.themes import THEME_PALETTES, tui_theme_palette
from libre_claw.core.tools import ToolCall
from libre_claw.tui.app import LibreClawApp, _palette_code_theme, _rich_log_selection_text
from libre_claw.tui.branding import PixelWordmark, pixel_wordmark_text


def test_wordmark_preserves_website_glyphs_and_uses_narrow_fallback() -> None:
    palette = tui_theme_palette("libre")
    output = StringIO()
    console = Console(file=output, width=73, color_system=None)
    console.print(PixelWordmark(palette))
    assert output.getvalue().rstrip() == pixel_wordmark_text()
    assert len(WORDMARK_ROWS) == 9
    assert {len(row) for row in WORDMARK_ROWS} == {73}

    output = StringIO()
    Console(file=output, width=40, color_system=None).print(PixelWordmark(palette))
    assert output.getvalue().strip() == "LIBRE CLAW"


@pytest.mark.parametrize("theme", list(THEME_PALETTES))
def test_syntax_and_diff_colors_follow_the_selected_palette(theme: str) -> None:
    palette = tui_theme_palette(theme)
    syntax = _palette_code_theme(palette)
    assert syntax.background_color == (palette.code or palette.background)
    assert syntax.get_style_for_token(Token.Keyword).color.get_truecolor().hex == palette.accent.lower()
    assert syntax.get_style_for_token(Token.Literal.String).color.get_truecolor().hex == palette.tool.lower()
    assert syntax.get_style_for_token(Token.Generic.Inserted).color.get_truecolor().hex == palette.ok.lower()
    assert syntax.get_style_for_token(Token.Generic.Deleted).color.get_truecolor().hex == palette.danger.lower()


@pytest.fixture
def design_app(monkeypatch, tmp_path: Path) -> LibreClawApp:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(LibreClawApp, "_rebuild_agent", lambda self: None)
    config = load_config()
    config = replace(
        config,
        tui=replace(config.tui, use_daemon=False),
        memory=replace(config.memory, enabled=False),
        heartbeat=replace(config.heartbeat, enabled=False),
        petdex=replace(config.petdex, enabled=False),
    )
    return LibreClawApp(config=config)


async def test_theme_switch_updates_widget_components_and_quiet_borders(design_app: LibreClawApp) -> None:
    app = design_app
    async with app.run_test(size=(120, 36)) as pilot:
        for name in THEME_PALETTES:
            app._set_tui_theme(name)
            await pilot.pause()
            palette = tui_theme_palette(name)
            assert app.query_one("#workspace").styles.border.top[1].hex.lower() == palette.line
            assert app.query_one("#composer").styles.border.top[1].hex.lower() == palette.line
            assert app.query_one("#chat").styles.background.hex.lower() == palette.background
            assert app.query_one("#workspace-bar").styles.background.hex.lower() == palette.background
            assert app.query_one("#composer").styles.background.hex.lower() == palette.surface
            cursor = app.query_one("#input", Input).get_component_rich_style("input--cursor")
            for actual, expected in ((cursor.bgcolor, palette.accent), (cursor.color, palette.on_accent)):
                assert all(abs(a - b) <= 1 for a, b in zip(actual.get_truecolor(), Color.parse(expected).get_truecolor()))
            button_color = app.query_one("#sidebar-show", Button).styles.background.hex
            assert all(abs(a - b) <= 1 for a, b in zip(Color.parse(button_color).get_truecolor(), Color.parse(palette.accent).get_truecolor()))


@pytest.mark.parametrize("preview_lines", [1, 120])
async def test_narrow_resize_reflows_branding_and_keeps_approval_actions_visible(
    design_app: LibreClawApp, monkeypatch, preview_lines: int,
) -> None:
    app = design_app
    async with app.run_test(size=(120, 36)) as pilot:
        assert pixel_wordmark_text().splitlines()[0] in _rich_log_selection_text(app.query_one("#chat").lines)
        resized = asyncio.Event()
        render_transcript = app._render_transcript

        def render_resized_transcript() -> None:
            render_transcript()
            if app.query_one("#chat").content_size.width < 73:
                resized.set()

        monkeypatch.setattr(app, "_render_transcript", render_resized_transcript)
        await pilot.resize_terminal(72, 24)
        await asyncio.wait_for(resized.wait(), timeout=2)
        await pilot.pause()
        chat = app.query_one("#chat")
        assert "LIBRE CLAW" in _rich_log_selection_text(chat.lines)
        assert app.has_class("narrow")
        assert chat.max_scroll_x == 0

        future = asyncio.get_running_loop().create_future()
        request = AgentPermissionRequest(
            call=ToolCall(id="design", name="bash", arguments={
                "command": "\n".join(f"printf line-{index}" for index in range(preview_lines)),
            }),
            future=future,
        )
        app._pending_permission = request
        app._show_permission_prompt(request)
        await pilot.pause()
        panel = app.query_one("#permission-panel")
        buttons = list(app.query("#permission-actions Button"))
        assert len(buttons) == 4
        assert len({button.region.y for button in buttons}) == 2
        assert all(panel.region.contains_region(button.region) for button in buttons)
        main = app.query_one("#main")
        assert all(main.region.contains_region(button.region) for button in buttons)
        assert all(app.screen.region.contains_region(button.region) for button in buttons)
        assert app.query_one("#chat").display is False
        if preview_lines > 1:
            preview = app.query_one("#permission-preview")
            assert preview.max_scroll_y > 0
        deny = app.query_one("#permission-deny")
        target, _ = app.get_widget_at(deny.region.x + deny.region.width // 2, deny.region.y)
        assert target is deny
        assert await pilot.click("#permission-deny", offset=(deny.region.width // 2, 0))
        assert await asyncio.wait_for(asyncio.shield(future), timeout=2) == "deny"
        assert app.query_one("#permission-panel").has_class("hidden")
        assert not app.has_class("approving")
        assert app.query_one("#chat").display is True
