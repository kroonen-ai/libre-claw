# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ThemePalette:
    """Shared theme metadata for dashboard defaults and TUI styling."""

    theme_id: str
    label: str
    is_light: bool
    background: str
    surface: str
    surface_2: str
    panel: str
    sidebar: str
    status_bg: str
    text: str
    muted: str
    accent: str
    accent_strong: str
    tool: str
    danger: str
    ok: str
    warn: str
    line: str = ""
    line_strong: str = ""
    soft: str = ""
    panel_strong: str = ""
    on_accent: str = ""
    code: str = ""
    on_accent_strong: str = ""


THEME_ALIASES: dict[str, str] = {
    "": "libre",
    "default": "libre",
    "dark": "libre",
    "libre-dark": "libre",
    "libre-default": "libre",
    "codex-lobster": "lobster",
    "clear": "lobster-light",
    "lobster-clear": "lobster-light",
    "codex-lobster-light": "lobster-light",
    "light": "libre-light",
    "ayu-mirage": "ayu",
    "rosepine": "rose-pine",
    "rose-pine-moon": "rose-pine",
    "one-dark": "one-dark-pro",
}


THEME_PALETTES: dict[str, ThemePalette] = {
    "libre": ThemePalette(
        theme_id="libre",
        label="Libre Claw",
        is_light=False,
        background="#11110f",
        surface="#191916",
        surface_2="#22221e",
        panel="#191916",
        sidebar="#191916",
        status_bg="#171714",
        text="#e8e4da",
        muted="#a19e94",
        accent="#d98c7c",
        accent_strong="#e8a294",
        tool="#a9c4b7",
        danger="#e1a094",
        ok="#afc69c",
        warn="#d4b57c",
        line="#33332c",
        line_strong="#4a493f",
        soft="#c4c0b5",
        panel_strong="#262621",
        on_accent="#19130f",
        code="#171714",
    ),
    "libre-light": ThemePalette(
        theme_id="libre-light",
        label="Libre Claw Light",
        is_light=True,
        background="#f3f0e7",
        surface="#f9f6ef",
        surface_2="#e9e5da",
        panel="#f9f6ef",
        sidebar="#f9f6ef",
        status_bg="#e9e5da",
        text="#24231f",
        muted="#69665c",
        accent="#973e30",
        accent_strong="#7b2f23",
        tool="#2d6959",
        danger="#973e30",
        ok="#4e6b33",
        warn="#805b1d",
        line="#d6d1c4",
        line_strong="#bcb5a5",
        soft="#504d44",
        panel_strong="#e3dfd3",
        on_accent="#fffaf2",
        code="#f9f6ef",
    ),
    "harness": ThemePalette(
        theme_id="harness",
        label="Harness",
        is_light=False,
        background="#151517",
        surface="#1b1b1c",
        surface_2="#232324",
        panel="#1b1b1c",
        sidebar="#1b1b1c",
        status_bg="#111112",
        text="#f9fafb",
        muted="#adb2b8",
        accent="#5686fe",
        accent_strong="#b7c8fe",
        tool="#679efe",
        danger="#f25a5a",
        ok="#22c55e",
        warn="#f59e0b",
    ),
    "harness-light": ThemePalette(
        theme_id="harness-light",
        label="Harness Light",
        is_light=True,
        background="#ffffff",
        surface="#ffffff",
        surface_2="#f5f6f7",
        panel="#ffffff",
        sidebar="#f9fafb",
        status_bg="#f1f3f5",
        text="#0f1115",
        muted="#81858c",
        accent="#4176e6",
        accent_strong="#2f4c8f",
        tool="#4176e6",
        danger="#ec1313",
        ok="#22c55e",
        warn="#f59e0b",
    ),
    "lobster": ThemePalette(
        theme_id="lobster",
        label="Lobster",
        is_light=False,
        background="#0b1020",
        surface="#111827",
        surface_2="#1f2937",
        panel="#111827",
        sidebar="#0b1020",
        status_bg="#111827",
        text="#e4e4e7",
        muted="#a1a1aa",
        accent="#FF5C5C",
        accent_strong="#fecaca",
        tool="#3b82f6",
        danger="#ff5c5c",
        ok="#22c55e",
        warn="#f59e0b",
    ),
    "lobster-light": ThemePalette(
        theme_id="lobster-light",
        label="Lobster Light",
        is_light=True,
        background="#fdf6e3",
        surface="#fffaf0",
        surface_2="#eee8d5",
        panel="#fff8e8",
        sidebar="#eee8d5",
        status_bg="#eadfca",
        text="#073642",
        muted="#657b83",
        accent="#ff5c5c",
        accent_strong="#b91c1c",
        tool="#268bd2",
        danger="#dc322f",
        ok="#859900",
        warn="#b58900",
    ),
    "github-dark": ThemePalette(
        theme_id="github-dark",
        label="GitHub Dark",
        is_light=False,
        background="#0d1117",
        surface="#161b22",
        surface_2="#0d1117",
        panel="#0d1117",
        sidebar="#161b22",
        status_bg="#010409",
        text="#f0f6fc",
        muted="#8b949e",
        accent="#2f81f7",
        accent_strong="#79c0ff",
        tool="#a5d6ff",
        danger="#f85149",
        ok="#3fb950",
        warn="#d29922",
    ),
    "github-light": ThemePalette(
        theme_id="github-light",
        label="GitHub Light",
        is_light=True,
        background="#f6f8fa",
        surface="#ffffff",
        surface_2="#f6f8fa",
        panel="#edf5ff",
        sidebar="#eef2f6",
        status_bg="#d9e2ec",
        text="#1f2328",
        muted="#656d76",
        accent="#0969da",
        accent_strong="#0550ae",
        tool="#8250df",
        danger="#cf222e",
        ok="#1a7f37",
        warn="#9a6700",
    ),
    "monokai-pro": ThemePalette(
        theme_id="monokai-pro",
        label="Monokai Pro",
        is_light=False,
        background="#19181a",
        surface="#2d2a2e",
        surface_2="#221f22",
        panel="#221f22",
        sidebar="#221f22",
        status_bg="#2d2a2e",
        text="#fcfcfa",
        muted="#939293",
        accent="#ff6188",
        accent_strong="#ffd866",
        tool="#78dce8",
        danger="#ff6188",
        ok="#a9dc76",
        warn="#ffd866",
    ),
    "night-owl": ThemePalette(
        theme_id="night-owl",
        label="Night Owl",
        is_light=False,
        background="#011627",
        surface="#061d32",
        surface_2="#0b2942",
        panel="#061d32",
        sidebar="#061d32",
        status_bg="#0b2942",
        text="#d6deeb",
        muted="#637777",
        accent="#82aaff",
        accent_strong="#addb67",
        tool="#7fdbca",
        danger="#ef5350",
        ok="#addb67",
        warn="#ecc48d",
    ),
    "tokyo-night": ThemePalette(
        theme_id="tokyo-night",
        label="Tokyo Night",
        is_light=False,
        background="#1a1b26",
        surface="#24283b",
        surface_2="#1f2335",
        panel="#1f2335",
        sidebar="#1f2335",
        status_bg="#24283b",
        text="#c0caf5",
        muted="#565f89",
        accent="#7aa2f7",
        accent_strong="#bb9af7",
        tool="#7dcfff",
        danger="#f7768e",
        ok="#9ece6a",
        warn="#e0af68",
    ),
    "ayu": ThemePalette(
        theme_id="ayu",
        label="Ayu Mirage",
        is_light=False,
        background="#0b0e14",
        surface="#1f2430",
        surface_2="#11151c",
        panel="#11151c",
        sidebar="#11151c",
        status_bg="#1f2430",
        text="#e6e1cf",
        muted="#b3b1ad",
        accent="#ffb454",
        accent_strong="#ffd580",
        tool="#59c2ff",
        danger="#f07178",
        ok="#aad94c",
        warn="#ffb454",
    ),
    "dracula": ThemePalette(
        theme_id="dracula",
        label="Dracula",
        is_light=False,
        background="#282a36",
        surface="#343746",
        surface_2="#21222c",
        panel="#21222c",
        sidebar="#21222c",
        status_bg="#343746",
        text="#f8f8f2",
        muted="#b7b7c9",
        accent="#bd93f9",
        accent_strong="#ff79c6",
        tool="#8be9fd",
        danger="#ff5555",
        ok="#50fa7b",
        warn="#f1fa8c",
    ),
    "catppuccin-mocha": ThemePalette(
        theme_id="catppuccin-mocha",
        label="Catppuccin Mocha",
        is_light=False,
        background="#1e1e2e",
        surface="#313244",
        surface_2="#181825",
        panel="#181825",
        sidebar="#181825",
        status_bg="#313244",
        text="#cdd6f4",
        muted="#9399b2",
        accent="#cba6f7",
        accent_strong="#89b4fa",
        tool="#94e2d5",
        danger="#f38ba8",
        ok="#a6e3a1",
        warn="#f9e2af",
    ),
    "catppuccin-latte": ThemePalette(
        theme_id="catppuccin-latte",
        label="Catppuccin Latte",
        is_light=True,
        background="#eff1f5",
        surface="#ffffff",
        surface_2="#e6e9ef",
        panel="#f5f6fb",
        sidebar="#e6e9ef",
        status_bg="#dce0e8",
        text="#4c4f69",
        muted="#7c7f93",
        accent="#8839ef",
        accent_strong="#1e66f5",
        tool="#179299",
        danger="#d20f39",
        ok="#40a02b",
        warn="#df8e1d",
    ),
    "gruvbox-dark": ThemePalette(
        theme_id="gruvbox-dark",
        label="Gruvbox Dark",
        is_light=False,
        background="#1d2021",
        surface="#282828",
        surface_2="#32302f",
        panel="#282828",
        sidebar="#282828",
        status_bg="#3c3836",
        text="#fbf1c7",
        muted="#a89984",
        accent="#fabd2f",
        accent_strong="#fe8019",
        tool="#83a598",
        danger="#fb4934",
        ok="#b8bb26",
        warn="#fabd2f",
    ),
    "nord": ThemePalette(
        theme_id="nord",
        label="Nord",
        is_light=False,
        background="#2e3440",
        surface="#3b4252",
        surface_2="#434c5e",
        panel="#3b4252",
        sidebar="#3b4252",
        status_bg="#434c5e",
        text="#eceff4",
        muted="#aeb8c4",
        accent="#88c0d0",
        accent_strong="#8fbcbb",
        tool="#81a1c1",
        danger="#bf616a",
        ok="#a3be8c",
        warn="#ebcb8b",
    ),
    "solarized-dark": ThemePalette(
        theme_id="solarized-dark",
        label="Solarized Dark",
        is_light=False,
        background="#002b36",
        surface="#073642",
        surface_2="#0b3a46",
        panel="#073642",
        sidebar="#073642",
        status_bg="#00212b",
        text="#eee8d5",
        muted="#839496",
        accent="#268bd2",
        accent_strong="#2aa198",
        tool="#b58900",
        danger="#dc322f",
        ok="#859900",
        warn="#b58900",
    ),
    "solarized-light": ThemePalette(
        theme_id="solarized-light",
        label="Solarized Light",
        is_light=True,
        background="#fdf6e3",
        surface="#fffaf0",
        surface_2="#eee8d5",
        panel="#eee8d5",
        sidebar="#eee8d5",
        status_bg="#e4ddc9",
        text="#073642",
        muted="#657b83",
        accent="#268bd2",
        accent_strong="#2aa198",
        tool="#b58900",
        danger="#dc322f",
        ok="#859900",
        warn="#b58900",
    ),
    "one-dark-pro": ThemePalette(
        theme_id="one-dark-pro",
        label="One Dark Pro",
        is_light=False,
        background="#21252b",
        surface="#282c34",
        surface_2="#2c313a",
        panel="#282c34",
        sidebar="#282c34",
        status_bg="#2c313a",
        text="#abb2bf",
        muted="#7f848e",
        accent="#61afef",
        accent_strong="#c678dd",
        tool="#56b6c2",
        danger="#e06c75",
        ok="#98c379",
        warn="#e5c07b",
    ),
    "rose-pine": ThemePalette(
        theme_id="rose-pine",
        label="Rose Pine",
        is_light=False,
        background="#191724",
        surface="#1f1d2e",
        surface_2="#26233a",
        panel="#1f1d2e",
        sidebar="#1f1d2e",
        status_bg="#26233a",
        text="#e0def4",
        muted="#908caa",
        accent="#c4a7e7",
        accent_strong="#ebbcba",
        tool="#9ccfd8",
        danger="#eb6f92",
        ok="#31748f",
        warn="#f6c177",
    ),
    "kanagawa": ThemePalette(
        theme_id="kanagawa",
        label="Kanagawa",
        is_light=False,
        background="#1f1f28",
        surface="#2a2a37",
        surface_2="#16161d",
        panel="#1f1f28",
        sidebar="#16161d",
        status_bg="#2a2a37",
        text="#dcd7ba",
        muted="#727169",
        accent="#7e9cd8",
        accent_strong="#957fb8",
        tool="#7aa89f",
        danger="#c34043",
        ok="#76946a",
        warn="#c0a36e",
    ),
    "matrix": ThemePalette(
        theme_id="matrix",
        label="Matrix",
        is_light=False,
        background="#000000",
        surface="#06100a",
        surface_2="#020604",
        panel="#020a05",
        sidebar="#020604",
        status_bg="#07150b",
        text="#d7ffe1",
        muted="#48a868",
        accent="#00ff41",
        accent_strong="#7cff9b",
        tool="#00d084",
        danger="#ff4757",
        ok="#00ff41",
        warn="#baff39",
    ),
}


def _blend_color(background: str, foreground: str, amount: float) -> str:
    channels = (
        round(int(background[index:index + 2], 16) * (1 - amount) + int(foreground[index:index + 2], 16) * amount)
        for index in (1, 3, 5)
    )
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _readable_text(color: str, text: str, backgrounds: tuple[str, ...]) -> str:
    """Preserve a palette color unless it needs more contrast on its surfaces."""
    for amount in range(101):
        candidate = color if amount == 0 else _blend_color(color, text, amount / 100)
        if all(_contrast_ratio(candidate, background) >= 4.5 for background in backgrounds):
            return candidate
    return max(("#000000", "#ffffff"), key=lambda candidate: min(_contrast_ratio(candidate, background) for background in backgrounds))


def _control_text(background: str, *preferred: str) -> str:
    for color in (*preferred, "#000000", "#ffffff"):
        if color and _contrast_ratio(color, background) >= 4.5:
            return color
    raise ValueError(f"No readable text color for {background}")


def _complete_palette(palette: ThemePalette) -> ThemePalette:
    """Apply the shared design tokens while retaining each theme's colors."""
    code = palette.code or (palette.surface if palette.is_light else palette.background)
    backgrounds = (palette.background, palette.surface, palette.surface_2, code)
    on_accent = _control_text(palette.accent, palette.on_accent, palette.background, palette.text)
    return replace(
        palette,
        line=palette.line or _blend_color(palette.background, palette.text, 0.16),
        line_strong=palette.line_strong or _blend_color(palette.background, palette.text, 0.26),
        muted=_readable_text(palette.muted, palette.text, backgrounds),
        soft=palette.soft or _readable_text(_blend_color(palette.text, palette.background, 0.18), palette.text, backgrounds),
        panel_strong=palette.panel_strong or palette.surface_2,
        on_accent=on_accent,
        on_accent_strong=_control_text(palette.accent_strong, palette.on_accent_strong, on_accent, palette.background, palette.text),
        code=code,
    )


THEME_PALETTES = {name: _complete_palette(palette) for name, palette in THEME_PALETTES.items()}


def normalize_theme(theme: str | None) -> str:
    """Return a known Libre Claw theme id, accepting legacy aliases."""
    value = (theme or "").strip().lower()
    value = THEME_ALIASES.get(value, value)
    if value in THEME_PALETTES:
        return value
    return "libre"


def dashboard_theme_id(theme: str | None) -> str:
    """Normalize a config value for the dashboard theme selector."""
    return normalize_theme(theme)


def tui_theme_palette(theme: str | None) -> ThemePalette:
    """Return the TUI palette for a config theme value."""
    return THEME_PALETTES[normalize_theme(theme)]
