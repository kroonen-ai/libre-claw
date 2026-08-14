# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from libre_claw.core.themes import dashboard_theme_id


def dashboard_html(theme: str = "lobster") -> str:
    """Return the self-contained local daemon dashboard."""
    fallback_theme = json.dumps(dashboard_theme_id(theme))
    return _DASHBOARD_HTML.replace("__LIBRE_CLAW_DASHBOARD_THEME__", fallback_theme)


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Libre Claw Dashboard</title>
  <link rel="icon" type="image/svg+xml" href="/assets/lobster-icon.svg?v=20260601">
  <script>
    (() => {
      const key = "libre-claw-dashboard-theme";
      const fallback = __LIBRE_CLAW_DASHBOARD_THEME__;
      const aliases = {
        "": "lobster",
        "default": "lobster",
        "dark": "lobster",
        "libre-default": "lobster",
        "clear": "lobster-light",
        "lobster-clear": "lobster-light",
        "codex-lobster-light": "lobster-light",
      };
      const raw = localStorage.getItem(key) || fallback;
      const value = aliases[raw] || raw;
      document.documentElement.dataset.theme = value;
    })();
  </script>
  <style>
    :root {
      color-scheme: dark light;
      --font-ui: "Satoshi", Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Jetbrains Mono", "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #0b1020;
      --surface: #111827;
      --surface-2: #1f2937;
      --panel: rgba(17, 24, 39, 0.86);
      --panel-strong: rgba(31, 41, 55, 0.94);
      --panel-hover: rgba(255, 92, 92, 0.08);
      --line: rgba(255, 255, 255, 0.11);
      --line-strong: rgba(255, 255, 255, 0.18);
      --text: #e4e4e7;
      --soft: #e4e4e7;
      --muted: #a1a1aa;
      --accent: #ff5c5c;
      --accent-soft: rgba(255, 92, 92, 0.15);
      --accent-strong: #fecaca;
      --tool-accent: #3b82f6;
      --tool-soft: rgba(59, 130, 246, 0.14);
      --danger: #ff5c5c;
      --danger-soft: rgba(255, 92, 92, 0.14);
      --ok: #22c55e;
      --ok-soft: rgba(34, 197, 94, 0.13);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.13);
      --grid-dot: rgba(255, 255, 255, 0.12);
      --shadow: 0 26px 80px rgba(0, 0, 0, 0.5);
      --radius: 8px;
    }
    html[data-theme="lobster-light"] {
      color-scheme: light;
      --font-ui: "Satoshi", Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Jetbrains Mono", "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #fdf6e3;
      --surface: #fffaf0;
      --surface-2: #eee8d5;
      --panel: rgba(255, 250, 240, 0.9);
      --panel-strong: #fffaf0;
      --panel-hover: rgba(255, 92, 92, 0.08);
      --line: rgba(101, 123, 131, 0.18);
      --line-strong: rgba(101, 123, 131, 0.3);
      --text: #073642;
      --soft: #586e75;
      --muted: #657b83;
      --accent: #ff5c5c;
      --accent-soft: rgba(255, 92, 92, 0.14);
      --accent-strong: #b91c1c;
      --tool-accent: #268bd2;
      --tool-soft: rgba(38, 139, 210, 0.12);
      --danger: #dc322f;
      --danger-soft: rgba(220, 50, 47, 0.1);
      --ok: #859900;
      --ok-soft: rgba(133, 153, 0, 0.1);
      --warn: #b58900;
      --warn-soft: rgba(181, 137, 0, 0.1);
      --grid-dot: rgba(255, 92, 92, 0.11);
      --shadow: 0 22px 70px rgba(101, 123, 131, 0.14);
    }
    html[data-theme="github-dark"] {
      color-scheme: dark;
      --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      --bg: #0d1117;
      --surface: #0d1117;
      --surface-2: #161b22;
      --panel: rgba(22, 27, 34, 0.88);
      --panel-strong: #161b22;
      --panel-hover: rgba(56, 139, 253, 0.1);
      --line: rgba(139, 148, 158, 0.22);
      --line-strong: rgba(201, 209, 217, 0.32);
      --text: #f0f6fc;
      --soft: #c9d1d9;
      --muted: #8b949e;
      --accent: #2f81f7;
      --accent-soft: rgba(47, 129, 247, 0.16);
      --accent-strong: #79c0ff;
      --tool-accent: #a5d6ff;
      --tool-soft: rgba(165, 214, 255, 0.12);
      --danger: #f85149;
      --danger-soft: rgba(248, 81, 73, 0.14);
      --ok: #3fb950;
      --ok-soft: rgba(63, 185, 80, 0.14);
      --warn: #d29922;
      --warn-soft: rgba(210, 153, 34, 0.14);
      --grid-dot: rgba(121, 192, 255, 0.12);
      --shadow: 0 26px 80px rgba(1, 4, 9, 0.56);
    }
    html[data-theme="github-light"] {
      color-scheme: light;
      --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      --bg: #f6f8fa;
      --surface: #ffffff;
      --surface-2: #f6f8fa;
      --panel: rgba(255, 255, 255, 0.9);
      --panel-strong: #ffffff;
      --panel-hover: rgba(9, 105, 218, 0.08);
      --line: rgba(31, 35, 40, 0.14);
      --line-strong: rgba(31, 35, 40, 0.24);
      --text: #1f2328;
      --soft: #24292f;
      --muted: #656d76;
      --accent: #0969da;
      --accent-soft: rgba(9, 105, 218, 0.12);
      --accent-strong: #0550ae;
      --tool-accent: #8250df;
      --tool-soft: rgba(130, 80, 223, 0.1);
      --danger: #cf222e;
      --danger-soft: rgba(207, 34, 46, 0.1);
      --ok: #1a7f37;
      --ok-soft: rgba(26, 127, 55, 0.1);
      --warn: #9a6700;
      --warn-soft: rgba(154, 103, 0, 0.12);
      --grid-dot: rgba(9, 105, 218, 0.12);
      --shadow: 0 22px 70px rgba(31, 35, 40, 0.12);
    }
    html[data-theme="monokai-pro"] {
      color-scheme: dark;
      --font-ui: "Inter", "Avenir Next", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "JetBrains Mono", "Fira Code", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #19181a;
      --surface: #221f22;
      --surface-2: #2d2a2e;
      --panel: rgba(45, 42, 46, 0.88);
      --panel-strong: #2d2a2e;
      --panel-hover: rgba(255, 97, 136, 0.1);
      --line: rgba(252, 252, 250, 0.12);
      --line-strong: rgba(252, 252, 250, 0.22);
      --text: #fcfcfa;
      --soft: #e5e1dc;
      --muted: #939293;
      --accent: #ff6188;
      --accent-soft: rgba(255, 97, 136, 0.16);
      --accent-strong: #ffd866;
      --tool-accent: #78dce8;
      --tool-soft: rgba(120, 220, 232, 0.14);
      --danger: #ff6188;
      --danger-soft: rgba(255, 97, 136, 0.13);
      --ok: #a9dc76;
      --ok-soft: rgba(169, 220, 118, 0.14);
      --warn: #ffd866;
      --warn-soft: rgba(255, 216, 102, 0.13);
      --grid-dot: rgba(255, 216, 102, 0.13);
      --shadow: 0 28px 86px rgba(0, 0, 0, 0.54);
    }
    html[data-theme="night-owl"] {
      color-scheme: dark;
      --font-ui: "Inter", "Nunito Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Dank Mono", "Operator Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #011627;
      --surface: #061d32;
      --surface-2: #0b2942;
      --panel: rgba(6, 29, 50, 0.88);
      --panel-strong: #0b2942;
      --panel-hover: rgba(130, 170, 255, 0.11);
      --line: rgba(127, 219, 202, 0.16);
      --line-strong: rgba(127, 219, 202, 0.28);
      --text: #d6deeb;
      --soft: #c5e4fd;
      --muted: #637777;
      --accent: #82aaff;
      --accent-soft: rgba(130, 170, 255, 0.16);
      --accent-strong: #addb67;
      --tool-accent: #7fdbca;
      --tool-soft: rgba(127, 219, 202, 0.14);
      --danger: #ef5350;
      --danger-soft: rgba(239, 83, 80, 0.14);
      --ok: #addb67;
      --ok-soft: rgba(173, 219, 103, 0.14);
      --warn: #ecc48d;
      --warn-soft: rgba(236, 196, 141, 0.14);
      --grid-dot: rgba(127, 219, 202, 0.12);
      --shadow: 0 28px 90px rgba(0, 8, 20, 0.62);
    }
    html[data-theme="tokyo-night"] {
      color-scheme: dark;
      --font-ui: "Inter", "IBM Plex Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Cascadia Code", "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #1a1b26;
      --surface: #1f2335;
      --surface-2: #24283b;
      --panel: rgba(36, 40, 59, 0.88);
      --panel-strong: #24283b;
      --panel-hover: rgba(122, 162, 247, 0.11);
      --line: rgba(86, 95, 137, 0.26);
      --line-strong: rgba(169, 177, 214, 0.3);
      --text: #c0caf5;
      --soft: #a9b1d6;
      --muted: #565f89;
      --accent: #7aa2f7;
      --accent-soft: rgba(122, 162, 247, 0.16);
      --accent-strong: #bb9af7;
      --tool-accent: #7dcfff;
      --tool-soft: rgba(125, 207, 255, 0.14);
      --danger: #f7768e;
      --danger-soft: rgba(247, 118, 142, 0.14);
      --ok: #9ece6a;
      --ok-soft: rgba(158, 206, 106, 0.14);
      --warn: #e0af68;
      --warn-soft: rgba(224, 175, 104, 0.14);
      --grid-dot: rgba(122, 162, 247, 0.12);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.52);
    }
    html[data-theme="ayu"] {
      color-scheme: dark;
      --font-ui: "Inter", "Helvetica Neue", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Fira Code", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #0b0e14;
      --surface: #11151c;
      --surface-2: #1f2430;
      --panel: rgba(31, 36, 48, 0.88);
      --panel-strong: #1f2430;
      --panel-hover: rgba(255, 180, 84, 0.1);
      --line: rgba(183, 192, 210, 0.14);
      --line-strong: rgba(183, 192, 210, 0.24);
      --text: #e6e1cf;
      --soft: #d9d7ce;
      --muted: #b3b1ad;
      --accent: #ffb454;
      --accent-soft: rgba(255, 180, 84, 0.16);
      --accent-strong: #ffd580;
      --tool-accent: #59c2ff;
      --tool-soft: rgba(89, 194, 255, 0.13);
      --danger: #f07178;
      --danger-soft: rgba(240, 113, 120, 0.14);
      --ok: #aad94c;
      --ok-soft: rgba(170, 217, 76, 0.14);
      --warn: #ffb454;
      --warn-soft: rgba(255, 180, 84, 0.14);
      --grid-dot: rgba(255, 180, 84, 0.12);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.58);
    }
    html[data-theme="dracula"] {
      color-scheme: dark;
      --font-ui: "Inter", "Nunito Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "JetBrains Mono", "Fira Code", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #282a36;
      --surface: #21222c;
      --surface-2: #282a36;
      --panel: rgba(40, 42, 54, 0.9);
      --panel-strong: #343746;
      --panel-hover: rgba(189, 147, 249, 0.12);
      --line: rgba(248, 248, 242, 0.14);
      --line-strong: rgba(248, 248, 242, 0.26);
      --text: #f8f8f2;
      --soft: #e6e6dc;
      --muted: #b7b7c9;
      --accent: #bd93f9;
      --accent-soft: rgba(189, 147, 249, 0.18);
      --accent-strong: #ff79c6;
      --tool-accent: #8be9fd;
      --tool-soft: rgba(139, 233, 253, 0.14);
      --danger: #ff5555;
      --danger-soft: rgba(255, 85, 85, 0.14);
      --ok: #50fa7b;
      --ok-soft: rgba(80, 250, 123, 0.12);
      --warn: #f1fa8c;
      --warn-soft: rgba(241, 250, 140, 0.12);
      --grid-dot: rgba(189, 147, 249, 0.14);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.5);
    }
    html[data-theme="catppuccin-mocha"] {
      color-scheme: dark;
      --font-ui: "Inter", "Manrope", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #1e1e2e;
      --surface: #181825;
      --surface-2: #1e1e2e;
      --panel: rgba(30, 30, 46, 0.9);
      --panel-strong: #313244;
      --panel-hover: rgba(203, 166, 247, 0.12);
      --line: rgba(205, 214, 244, 0.14);
      --line-strong: rgba(205, 214, 244, 0.26);
      --text: #cdd6f4;
      --soft: #bac2de;
      --muted: #9399b2;
      --accent: #cba6f7;
      --accent-soft: rgba(203, 166, 247, 0.17);
      --accent-strong: #89b4fa;
      --tool-accent: #94e2d5;
      --tool-soft: rgba(148, 226, 213, 0.13);
      --danger: #f38ba8;
      --danger-soft: rgba(243, 139, 168, 0.14);
      --ok: #a6e3a1;
      --ok-soft: rgba(166, 227, 161, 0.13);
      --warn: #f9e2af;
      --warn-soft: rgba(249, 226, 175, 0.12);
      --grid-dot: rgba(203, 166, 247, 0.13);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.54);
    }
    html[data-theme="catppuccin-latte"] {
      color-scheme: light;
      --font-ui: "Inter", "Manrope", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #eff1f5;
      --surface: #ffffff;
      --surface-2: #e6e9ef;
      --panel: rgba(255, 255, 255, 0.9);
      --panel-strong: #ffffff;
      --panel-hover: rgba(136, 57, 239, 0.08);
      --line: rgba(76, 79, 105, 0.15);
      --line-strong: rgba(76, 79, 105, 0.26);
      --text: #4c4f69;
      --soft: #5c5f77;
      --muted: #7c7f93;
      --accent: #8839ef;
      --accent-soft: rgba(136, 57, 239, 0.12);
      --accent-strong: #1e66f5;
      --tool-accent: #179299;
      --tool-soft: rgba(23, 146, 153, 0.1);
      --danger: #d20f39;
      --danger-soft: rgba(210, 15, 57, 0.1);
      --ok: #40a02b;
      --ok-soft: rgba(64, 160, 43, 0.1);
      --warn: #df8e1d;
      --warn-soft: rgba(223, 142, 29, 0.1);
      --grid-dot: rgba(136, 57, 239, 0.1);
      --shadow: 0 22px 70px rgba(76, 79, 105, 0.14);
    }
    html[data-theme="gruvbox-dark"] {
      color-scheme: dark;
      --font-ui: "Inter", "Atkinson Hyperlegible", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Iosevka", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #1d2021;
      --surface: #282828;
      --surface-2: #32302f;
      --panel: rgba(40, 40, 40, 0.9);
      --panel-strong: #3c3836;
      --panel-hover: rgba(250, 189, 47, 0.1);
      --line: rgba(251, 241, 199, 0.14);
      --line-strong: rgba(251, 241, 199, 0.25);
      --text: #fbf1c7;
      --soft: #ebdbb2;
      --muted: #a89984;
      --accent: #fabd2f;
      --accent-soft: rgba(250, 189, 47, 0.16);
      --accent-strong: #fe8019;
      --tool-accent: #83a598;
      --tool-soft: rgba(131, 165, 152, 0.13);
      --danger: #fb4934;
      --danger-soft: rgba(251, 73, 52, 0.14);
      --ok: #b8bb26;
      --ok-soft: rgba(184, 187, 38, 0.13);
      --warn: #fabd2f;
      --warn-soft: rgba(250, 189, 47, 0.13);
      --grid-dot: rgba(250, 189, 47, 0.12);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.52);
    }
    html[data-theme="nord"] {
      color-scheme: dark;
      --font-ui: "Inter", "IBM Plex Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Cascadia Code", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #2e3440;
      --surface: #3b4252;
      --surface-2: #434c5e;
      --panel: rgba(59, 66, 82, 0.9);
      --panel-strong: #434c5e;
      --panel-hover: rgba(136, 192, 208, 0.11);
      --line: rgba(216, 222, 233, 0.16);
      --line-strong: rgba(216, 222, 233, 0.28);
      --text: #eceff4;
      --soft: #d8dee9;
      --muted: #aeb8c4;
      --accent: #88c0d0;
      --accent-soft: rgba(136, 192, 208, 0.16);
      --accent-strong: #8fbcbb;
      --tool-accent: #81a1c1;
      --tool-soft: rgba(129, 161, 193, 0.14);
      --danger: #bf616a;
      --danger-soft: rgba(191, 97, 106, 0.14);
      --ok: #a3be8c;
      --ok-soft: rgba(163, 190, 140, 0.14);
      --warn: #ebcb8b;
      --warn-soft: rgba(235, 203, 139, 0.13);
      --grid-dot: rgba(136, 192, 208, 0.13);
      --shadow: 0 28px 90px rgba(20, 24, 31, 0.55);
    }
    html[data-theme="solarized-dark"] {
      color-scheme: dark;
      --font-ui: "Inter", "Source Sans 3", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Source Code Pro", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #002b36;
      --surface: #073642;
      --surface-2: #0b3a46;
      --panel: rgba(7, 54, 66, 0.9);
      --panel-strong: #073642;
      --panel-hover: rgba(38, 139, 210, 0.12);
      --line: rgba(147, 161, 161, 0.22);
      --line-strong: rgba(238, 232, 213, 0.28);
      --text: #eee8d5;
      --soft: #d7d1bd;
      --muted: #839496;
      --accent: #268bd2;
      --accent-soft: rgba(38, 139, 210, 0.17);
      --accent-strong: #2aa198;
      --tool-accent: #b58900;
      --tool-soft: rgba(181, 137, 0, 0.14);
      --danger: #dc322f;
      --danger-soft: rgba(220, 50, 47, 0.14);
      --ok: #859900;
      --ok-soft: rgba(133, 153, 0, 0.14);
      --warn: #b58900;
      --warn-soft: rgba(181, 137, 0, 0.13);
      --grid-dot: rgba(42, 161, 152, 0.13);
      --shadow: 0 28px 90px rgba(0, 18, 24, 0.6);
    }
    html[data-theme="solarized-light"] {
      color-scheme: light;
      --font-ui: "Inter", "Source Sans 3", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Source Code Pro", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #fdf6e3;
      --surface: #fffaf0;
      --surface-2: #eee8d5;
      --panel: rgba(255, 250, 240, 0.9);
      --panel-strong: #fffaf0;
      --panel-hover: rgba(38, 139, 210, 0.08);
      --line: rgba(101, 123, 131, 0.18);
      --line-strong: rgba(101, 123, 131, 0.3);
      --text: #073642;
      --soft: #586e75;
      --muted: #657b83;
      --accent: #268bd2;
      --accent-soft: rgba(38, 139, 210, 0.12);
      --accent-strong: #2aa198;
      --tool-accent: #b58900;
      --tool-soft: rgba(181, 137, 0, 0.1);
      --danger: #dc322f;
      --danger-soft: rgba(220, 50, 47, 0.1);
      --ok: #859900;
      --ok-soft: rgba(133, 153, 0, 0.1);
      --warn: #b58900;
      --warn-soft: rgba(181, 137, 0, 0.1);
      --grid-dot: rgba(38, 139, 210, 0.1);
      --shadow: 0 22px 70px rgba(101, 123, 131, 0.14);
    }
    html[data-theme="one-dark-pro"] {
      color-scheme: dark;
      --font-ui: "Inter", "Segoe UI", ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: "Cascadia Code", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #21252b;
      --surface: #282c34;
      --surface-2: #2c313a;
      --panel: rgba(40, 44, 52, 0.9);
      --panel-strong: #2c313a;
      --panel-hover: rgba(97, 175, 239, 0.11);
      --line: rgba(171, 178, 191, 0.16);
      --line-strong: rgba(171, 178, 191, 0.28);
      --text: #abb2bf;
      --soft: #d7dae0;
      --muted: #7f848e;
      --accent: #61afef;
      --accent-soft: rgba(97, 175, 239, 0.16);
      --accent-strong: #c678dd;
      --tool-accent: #56b6c2;
      --tool-soft: rgba(86, 182, 194, 0.14);
      --danger: #e06c75;
      --danger-soft: rgba(224, 108, 117, 0.14);
      --ok: #98c379;
      --ok-soft: rgba(152, 195, 121, 0.14);
      --warn: #e5c07b;
      --warn-soft: rgba(229, 192, 123, 0.13);
      --grid-dot: rgba(97, 175, 239, 0.12);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.52);
    }
    html[data-theme="rose-pine"] {
      color-scheme: dark;
      --font-ui: "Inter", "Avenir Next", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "Cartograph CF", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #191724;
      --surface: #1f1d2e;
      --surface-2: #26233a;
      --panel: rgba(31, 29, 46, 0.9);
      --panel-strong: #26233a;
      --panel-hover: rgba(196, 167, 231, 0.11);
      --line: rgba(224, 222, 244, 0.13);
      --line-strong: rgba(224, 222, 244, 0.24);
      --text: #e0def4;
      --soft: #d9d4ee;
      --muted: #908caa;
      --accent: #c4a7e7;
      --accent-soft: rgba(196, 167, 231, 0.16);
      --accent-strong: #ebbcba;
      --tool-accent: #9ccfd8;
      --tool-soft: rgba(156, 207, 216, 0.13);
      --danger: #eb6f92;
      --danger-soft: rgba(235, 111, 146, 0.14);
      --ok: #31748f;
      --ok-soft: rgba(49, 116, 143, 0.16);
      --warn: #f6c177;
      --warn-soft: rgba(246, 193, 119, 0.13);
      --grid-dot: rgba(196, 167, 231, 0.12);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.54);
    }
    html[data-theme="kanagawa"] {
      color-scheme: dark;
      --font-ui: "Inter", "Hiragino Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #1f1f28;
      --surface: #16161d;
      --surface-2: #2a2a37;
      --panel: rgba(31, 31, 40, 0.9);
      --panel-strong: #2a2a37;
      --panel-hover: rgba(126, 156, 216, 0.11);
      --line: rgba(220, 215, 186, 0.14);
      --line-strong: rgba(220, 215, 186, 0.26);
      --text: #dcd7ba;
      --soft: #c8c093;
      --muted: #727169;
      --accent: #7e9cd8;
      --accent-soft: rgba(126, 156, 216, 0.17);
      --accent-strong: #957fb8;
      --tool-accent: #7aa89f;
      --tool-soft: rgba(122, 168, 159, 0.14);
      --danger: #c34043;
      --danger-soft: rgba(195, 64, 67, 0.14);
      --ok: #76946a;
      --ok-soft: rgba(118, 148, 106, 0.14);
      --warn: #c0a36e;
      --warn-soft: rgba(192, 163, 110, 0.13);
      --grid-dot: rgba(126, 156, 216, 0.13);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.55);
    }
    html[data-theme="matrix"] {
      color-scheme: dark;
      --font-ui: "Inter", "IBM Plex Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: "IBM Plex Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
      --bg: #000000;
      --surface: #020604;
      --surface-2: #06100a;
      --panel: rgba(2, 10, 5, 0.9);
      --panel-strong: #07150b;
      --panel-hover: rgba(0, 255, 65, 0.1);
      --line: rgba(0, 255, 65, 0.2);
      --line-strong: rgba(0, 255, 65, 0.34);
      --text: #d7ffe1;
      --soft: #a7ffbd;
      --muted: #48a868;
      --accent: #00ff41;
      --accent-soft: rgba(0, 255, 65, 0.16);
      --accent-strong: #7cff9b;
      --tool-accent: #00d084;
      --tool-soft: rgba(0, 208, 132, 0.14);
      --danger: #ff4757;
      --danger-soft: rgba(255, 71, 87, 0.14);
      --ok: #00ff41;
      --ok-soft: rgba(0, 255, 65, 0.14);
      --warn: #baff39;
      --warn-soft: rgba(186, 255, 57, 0.13);
      --grid-dot: rgba(0, 255, 65, 0.16);
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.68);
    }
    html[data-theme="harness"] {
      color-scheme: dark;
      --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Helvetica Neue", Helvetica, Arial, sans-serif;
      --font-mono: "SF Mono", "JetBrains Mono", "Fira Code", Consolas, Menlo, monospace;
      --bg: #151517;
      --surface: #1b1b1c;
      --surface-2: #232324;
      --panel: #1b1b1c;
      --panel-strong: #2c2c2e;
      --panel-hover: rgba(255, 255, 255, 0.08);
      --line: rgba(255, 255, 255, 0.1);
      --line-strong: rgba(255, 255, 255, 0.16);
      --text: #f9fafb;
      --soft: #cfd3d6;
      --muted: #adb2b8;
      --accent: #5686fe;
      --accent-soft: rgba(86, 134, 254, 0.16);
      --accent-strong: #b7c8fe;
      --tool-accent: #679efe;
      --tool-soft: rgba(103, 158, 254, 0.14);
      --danger: #f25a5a;
      --danger-soft: rgba(242, 90, 90, 0.15);
      --ok: #22c55e;
      --ok-soft: rgba(34, 197, 94, 0.13);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.13);
      --grid-dot: transparent;
      --shadow: 0 0 1px rgba(0, 0, 0, 0.4), 0 12px 32px rgba(0, 0, 0, 0.32);
      --sidebar-fill: #1b1b1c;
      --canvas: #151517;
    }
    html[data-theme="harness-light"] {
      color-scheme: light;
      --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Helvetica Neue", Helvetica, Arial, sans-serif;
      --font-mono: "SF Mono", "JetBrains Mono", "Fira Code", Consolas, Menlo, monospace;
      --bg: #ffffff;
      --surface: #ffffff;
      --surface-2: #f5f6f7;
      --panel: #ffffff;
      --panel-strong: #ffffff;
      --panel-hover: rgba(38, 49, 72, 0.06);
      --line: rgba(0, 0, 0, 0.1);
      --line-strong: rgba(0, 0, 0, 0.16);
      --text: #0f1115;
      --soft: #61666b;
      --muted: #81858c;
      --accent: #4176e6;
      --accent-soft: rgba(65, 118, 230, 0.14);
      --accent-strong: #2f4c8f;
      --tool-accent: #4176e6;
      --tool-soft: rgba(65, 118, 230, 0.12);
      --danger: #ec1313;
      --danger-soft: rgba(236, 19, 19, 0.08);
      --ok: #22c55e;
      --ok-soft: rgba(34, 197, 94, 0.12);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.12);
      --grid-dot: transparent;
      --shadow: 0 0 1px rgba(0, 0, 0, 0.2), 0 12px 32px rgba(0, 0, 0, 0.08);
      --sidebar-fill: #f9fafb;
      --canvas: #ffffff;
    }
    * { box-sizing: border-box; }
    html {
      background: var(--sidebar-fill, var(--bg));
      overflow-x: clip;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }
    body {
      margin: 0;
      background: var(--sidebar-fill, var(--bg));
      color: var(--text);
      min-height: 100vh;
      overflow-x: clip;
      font-size: 14px;
      line-height: 1.5;
      font-family: var(--font-ui);
    }
    button, input, textarea, select { font: inherit; color: inherit; }
    button { cursor: pointer; border: none; background: transparent; padding: 0; }
    button:disabled { cursor: not-allowed; opacity: .48; }
    a { color: var(--accent-strong); text-decoration: none; }
    a:hover { text-decoration: underline; text-underline-offset: 3px; }
    button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 999px; }

    /* ------ App frame: sidebar column + inset conversation panel ------ */
    .app {
      display: grid;
      grid-template-columns: 288px minmax(0, 1fr);
      height: 100vh;
      overflow: clip;
    }
    .app.rail { grid-template-columns: 64px minmax(0, 1fr); }

    /* ------ Sidebar ------ */
    .sidebar {
      display: flex;
      flex-direction: column;
      min-height: 0;
      padding: 6px 12px;
      background: var(--sidebar-fill, var(--bg));
      border-inline-end: 1px solid var(--line);
    }
    .logo-row {
      flex: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      height: 56px;
      padding: 8px 0 8px 4px;
      margin-bottom: 8px;
    }
    .brand {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      font-weight: 650;
      font-size: 15px;
      letter-spacing: -0.01em;
      cursor: pointer;
      color: var(--text);
    }
    .brand .logo-wrap { font-size: 22px; line-height: 1; }
    .brand .harness-tag {
      padding: 2px 6px;
      border-radius: 6px;
      background: var(--text);
      color: var(--sidebar-fill, var(--bg));
      font-family: var(--font-mono);
      font-size: 9px;
      font-weight: 700;
      letter-spacing: .08em;
    }
    .icon-btn {
      flex: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      color: var(--soft);
      transition: background .16s ease, color .16s ease;
    }
    .icon-btn:hover { background: var(--panel-hover); color: var(--text); }
    .icon-btn svg { width: 16px; height: 16px; }
    .new-session {
      flex: none;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      height: 38px;
      margin: 0 2px 8px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-strong);
      color: var(--text);
      font-size: 14px;
      font-weight: 500;
      transition: background .16s ease;
    }
    .new-session:hover { background: var(--panel-hover); }
    .section-label {
      flex: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 28px;
      padding: 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .section-label .filters { display: inline-flex; align-items: center; gap: 4px; }
    .filter-row { flex: none; display: grid; gap: 6px; padding: 0 2px 8px; }
    .filter-row input[type="search"], .filter-row select {
      height: 30px;
      border: none;
      border-radius: 10px;
      background: var(--panel-hover);
      padding: 0 10px;
      font-size: 12px;
      color: var(--text);
      outline: none;
      width: 100%;
      min-width: 0;
    }
    .runs {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 2px;
      margin: 0 -4px;
      padding: 0 4px;
    }
    .run-item {
      flex: none;
      display: flex;
      align-items: center;
      gap: 8px;
      height: 32px;
      padding: 0 8px;
      border-radius: 8px;
      text-align: start;
      color: var(--text);
      transition: background .12s ease;
    }
    .run-item:hover, .run-item.active { background: var(--panel-hover); }
    .run-item .state-dot { flex: none; width: 8px; height: 8px; border-radius: 999px; background: var(--muted); }
    .run-item .state-dot.running, .run-item .state-dot.queued { background: var(--accent); animation: pulse 1.6s ease-in-out infinite; }
    .run-item .state-dot.blocked { background: var(--warn); animation: pulse 1.6s ease-in-out infinite; }
    .run-item .state-dot.done { background: var(--ok); }
    .run-item .state-dot.failed, .run-item .state-dot.cancelled { background: var(--danger); }
    @keyframes pulse { 50% { opacity: .45; } }
    .run-title {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 14px;
    }
    .run-time { flex: none; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .empty { padding: 20px 8px; color: var(--muted); font-size: 13px; text-align: center; }
    .side-foot { flex: none; border-top: 1px solid var(--line); padding-top: 6px; margin-top: 6px; }
    .side-foot-row {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      height: 34px;
      padding: 0 10px;
      border-radius: 12px;
      color: var(--text);
      font-size: 14px;
      text-align: start;
      transition: background .16s ease;
    }
    .side-foot-row:hover { background: var(--panel-hover); }
    .side-foot-row svg { width: 16px; height: 16px; color: var(--soft); flex: none; }
    .side-foot-row .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .side-foot-row .tiny { font-size: 11px; color: var(--muted); }
    .status-dot { width: 8px; height: 8px; border-radius: 999px; background: var(--muted); flex: none; }
    .status-dot.online { background: var(--ok); }
    .status-dot.offline { background: var(--danger); }

    /* Rail (collapsed sidebar) */
    .app.rail .sidebar { padding: 14px 10px 6px; align-items: center; }
    .app.rail .logo-row { height: 36px; padding: 0; margin-bottom: 12px; justify-content: center; }
    .app.rail .brand span:not(.logo-wrap), .app.rail .harness-tag { display: none; }
    .app.rail .logo-row .icon-btn { display: none; }
    .app.rail .new-session { width: 36px; height: 36px; padding: 0; margin: 0 0 12px; border-color: transparent; background: transparent; }
    .app.rail .new-session:hover { background: var(--panel-hover); }
    .app.rail .new-session span { display: none; }
    .app.rail .section-label, .app.rail .filter-row, .app.rail .runs .run-title, .app.rail .runs .run-time { display: none; }
    .app.rail .runs { align-items: center; }
    .app.rail .run-item { width: 36px; justify-content: center; padding: 0; }
    .app.rail .side-foot { display: flex; flex-direction: column; align-items: center; }
    .app.rail .side-foot-row { width: 36px; height: 36px; justify-content: center; padding: 0; border-radius: 999px; }
    .app.rail .side-foot-row .grow, .app.rail .side-foot-row .tiny { display: none; }

    /* ------ Main conversation panel ------ */
    .main {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      margin: 8px 8px 8px 0;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--canvas, var(--surface));
      overflow: clip;
    }
    .main-head {
      flex: none;
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 52px;
      padding: 10px 14px 0;
    }
    .main-head h1 {
      margin: 0;
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 15px;
      font-weight: 600;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 26px;
      padding: 0 10px;
      border-radius: 999px;
      background: var(--panel-hover);
      color: var(--soft);
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
    }
    .pill.running, .pill.queued { background: var(--accent-soft); color: var(--accent-strong); }
    .pill.blocked { background: var(--warn-soft); color: var(--warn); }
    .pill.done, .pill.active { background: var(--ok-soft); color: var(--ok); }
    .pill.failed, .pill.cancelled, .pill.paused { background: var(--danger-soft); color: var(--danger); }
    .pill-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 30px;
      padding: 0 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
      transition: background .16s ease;
    }
    .pill-btn:hover { background: var(--panel-hover); }
    .pill-btn.danger { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 40%, var(--line)); }
    .pill-btn.danger:hover { background: var(--danger-soft); }
    .view-tabs {
      flex: none;
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 4px 16px 0;
      border-bottom: 1px solid var(--line);
    }
    .view-tab {
      position: relative;
      padding: 8px 2px 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 500;
      transition: color .16s ease;
    }
    .view-tab:hover { color: var(--text); }
    .view-tab.active { color: var(--text); }
    .view-tab.active::after {
      content: "";
      position: absolute;
      inset-inline: 0;
      bottom: -1px;
      height: 2px;
      border-radius: 2px;
      background: var(--text);
    }
    .view-tabs .spacer { flex: 1; }
    .view-tabs select {
      height: 26px;
      border: none;
      border-radius: 999px;
      background: var(--panel-hover);
      padding: 0 10px;
      font-size: 12px;
      color: var(--soft);
      outline: none;
      margin-bottom: 6px;
    }
    .view-tabs .tiny { color: var(--muted); font-size: 11px; margin-bottom: 6px; }
    .conversation {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding: 18px clamp(14px, 6vw, 72px) 12px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .msg-user { display: flex; justify-content: flex-end; }
    .msg-user > div {
      max-width: 82%;
      border-radius: 16px 16px 4px 16px;
      background: var(--accent-soft);
      color: var(--text);
      padding: 10px 14px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .msg-assistant { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
    .msg-tool {
      display: flex;
      flex-direction: column;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
      padding: 10px 12px;
    }
    .msg-tool summary, .msg-tool .tool-head {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--soft);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      list-style: none;
    }
    .msg-tool summary::-webkit-details-marker { display: none; }
    .msg-tool .tool-name { font-family: var(--font-mono); color: var(--tool-accent); }
    .msg-tool.is-error .tool-name { color: var(--danger); }
    .msg-tool pre {
      margin: 0;
      padding: 8px 10px;
      border-radius: 8px;
      background: var(--surface-2);
      font-family: var(--font-mono);
      font-size: 11.5px;
      line-height: 1.55;
      overflow-x: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 320px;
      overflow-y: auto;
    }
    .msg-note { color: var(--muted); font-size: 12px; text-align: center; }
    .msg-error {
      border: 1px solid color-mix(in srgb, var(--danger) 40%, var(--line));
      background: var(--danger-soft);
      border-radius: 12px;
      padding: 10px 12px;
      font-size: 13px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .event {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
      padding: 10px 12px;
      display: grid;
      gap: 6px;
    }
    .event.is-error { border-color: color-mix(in srgb, var(--danger) 40%, var(--line)); background: var(--danger-soft); }
    .event-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
    .event-type { font-size: 12px; font-weight: 600; color: var(--soft); }
    .event-time { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .event pre {
      margin: 0;
      font-family: var(--font-mono);
      font-size: 11.5px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 340px;
      overflow-y: auto;
    }
    .composer-zone { flex: none; padding: 4px clamp(14px, 6vw, 72px) 4px; }
    .approval {
      border: 1px solid color-mix(in srgb, var(--warn) 50%, var(--line));
      background: var(--warn-soft);
      border-radius: 14px;
      padding: 12px;
      display: grid;
      gap: 8px;
      margin-bottom: 8px;
    }
    .approval .event-type { color: var(--text); }
    .approval pre {
      margin: 0;
      font-family: var(--font-mono);
      font-size: 11.5px;
      max-height: 160px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .approval .row { display: flex; flex-wrap: wrap; gap: 6px; }
    .approval .row button {
      height: 28px;
      padding: 0 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface);
      font-size: 12px;
      font-weight: 500;
    }
    .approval .row button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .approval .row button.danger { color: var(--danger); }
    .notice { display: none; padding: 0 4px 6px; font-size: 11px; color: var(--muted); }
    .notice.visible { display: block; }
    .notice.error { color: var(--danger); }
    .composer {
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--surface);
      box-shadow: var(--shadow);
      padding: 10px;
      transition: box-shadow .2s ease;
    }
    .composer textarea {
      display: block;
      width: 100%;
      min-height: 34px;
      max-height: 160px;
      border: none;
      background: transparent;
      resize: none;
      outline: none;
      padding: 4px 8px 8px;
      font-size: 14px;
      line-height: 1.55;
      color: var(--text);
    }
    .composer textarea::placeholder { color: var(--muted); }
    .composer-controls { display: flex; align-items: center; gap: 6px; }
    .composer-controls select, .composer-controls input {
      height: 32px;
      border: none;
      border-radius: 999px;
      background: var(--surface-2);
      padding: 0 12px;
      font-size: 12px;
      color: var(--text);
      outline: none;
      min-width: 0;
      transition: background .16s ease;
    }
    .composer-controls select:hover, .composer-controls input:hover { background: var(--panel-hover); }
    .composer-controls input { width: 148px; font-family: var(--font-mono); font-size: 11.5px; }
    .composer-controls .spacer { flex: 1; }
    .send-btn {
      flex: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      transition: filter .16s ease;
    }
    .send-btn:hover { filter: brightness(1.12); }
    .send-btn:disabled { background: var(--accent-soft); color: color-mix(in srgb, #ffffff 70%, transparent); }
    .send-btn svg { width: 16px; height: 16px; }
    .status-strip {
      flex: none;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-wrap: wrap;
      gap: 4px 10px;
      padding: 4px 14px 8px;
      color: var(--muted);
      font-size: 11px;
      font-variant-numeric: tabular-nums;
    }
    .status-strip .sep { opacity: .5; }

    /* ------ Markdown rendering ------ */
    .msg-md { line-height: 1.65; overflow-wrap: anywhere; }
    .msg-md > :first-child { margin-top: 0; }
    .msg-md > :last-child { margin-bottom: 0; }
    .msg-md p { margin: 0 0 10px; }
    .msg-md h3, .msg-md h4, .msg-md h5, .msg-md h6 { margin: 16px 0 8px; font-size: 15px; font-weight: 650; }
    .msg-md h3 { font-size: 16px; }
    .msg-md ul, .msg-md ol { margin: 0 0 10px; padding-inline-start: 22px; }
    .msg-md li { margin: 2px 0; }
    .msg-md a { color: var(--accent-strong); text-decoration: underline; text-underline-offset: 3px; }
    .msg-md a:hover { color: var(--text); }
    .msg-md code {
      padding: 1px 6px;
      border-radius: 6px;
      background: var(--surface-2);
      font-family: var(--font-mono);
      font-size: 12px;
    }
    .msg-md blockquote {
      margin: 0 0 10px;
      padding: 2px 0 2px 12px;
      border-inline-start: 3px solid var(--line-strong);
      color: var(--soft);
    }
    .msg-md hr { border: none; border-top: 1px solid var(--line); margin: 14px 0; }
    .table-wrap { overflow-x: auto; margin: 0 0 10px; }
    .msg-md table, .usage-table { border-collapse: collapse; font-size: 12.5px; min-width: 50%; }
    .msg-md th, .msg-md td, .usage-table th, .usage-table td {
      border: 1px solid var(--line);
      padding: 6px 10px;
      text-align: start;
    }
    .msg-md th, .usage-table th { background: var(--surface-2); font-weight: 600; }
    .code-block {
      margin: 0 0 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow: hidden;
      background: var(--surface);
    }
    .code-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 5px 6px 5px 12px;
      background: var(--surface-2);
      color: var(--muted);
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .code-copy {
      height: 22px;
      padding: 0 10px;
      border-radius: 999px;
      background: var(--panel-hover);
      color: var(--soft);
      font-size: 11px;
      transition: background .16s ease;
    }
    .code-copy:hover { background: var(--line); color: var(--text); }
    .code-block pre {
      margin: 0;
      padding: 10px 12px;
      overflow-x: auto;
      font-family: var(--font-mono);
      font-size: 12px;
      line-height: 1.55;
    }
    .streaming-caret { display: inline-block; width: 7px; height: 15px; margin-inline-start: 2px; vertical-align: -2px; background: var(--accent); border-radius: 2px; animation: pulse 1s ease-in-out infinite; }
    .model-chip {
      height: 28px;
      padding: 0 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface);
      font-family: var(--font-mono);
      font-size: 12px;
      transition: background .16s ease, border-color .16s ease;
    }
    .model-chip:hover { background: var(--accent-soft); border-color: var(--accent); }
    .endpoint-row { display: flex; gap: 8px; align-items: center; }
    .endpoint-row input {
      flex: 1;
      min-width: 0;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--surface);
      padding: 0 10px;
      font-family: var(--font-mono);
      font-size: 12.5px;
      color: var(--text);
      outline: none;
    }
    .endpoint-row input:focus { border-color: var(--accent); }
    .usage-table-wrap { overflow-x: auto; margin-top: 10px; }
    .usage-sub { margin: 16px 0 4px; font-size: 13px; font-weight: 600; color: var(--soft); }

    /* ------ Settings modal (DSH panel) ------ */
    .overlay {
      position: fixed;
      inset: 0;
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
    }
    .overlay.open { display: flex; }
    .overlay .mask { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.4); backdrop-filter: blur(2px); }
    .settings-panel {
      position: relative;
      z-index: 1;
      display: flex;
      width: min(860px, calc(100vw - 32px));
      height: min(640px, calc(100vh - 48px));
      border-radius: 24px;
      overflow: hidden;
      background: var(--panel-strong);
      box-shadow: var(--shadow);
    }
    .settings-nav {
      flex: none;
      display: flex;
      flex-direction: column;
      gap: 4px;
      width: 188px;
      padding: 22px 12px 12px;
    }
    .settings-nav h2 { margin: 0 0 14px; padding: 0 12px; font-size: 16px; font-weight: 600; }
    .settings-nav button {
      display: flex;
      align-items: center;
      gap: 8px;
      height: 40px;
      padding: 0 12px;
      border-radius: 12px;
      color: var(--text);
      font-size: 14px;
      text-align: start;
      transition: background .16s ease;
    }
    .settings-nav button:hover { background: var(--panel-hover); }
    .settings-nav button.active { background: var(--panel-hover); }
    .settings-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
    .settings-head { flex: none; display: flex; justify-content: flex-end; padding: 16px 14px 4px; }
    .settings-body { flex: 1; min-height: 0; overflow-y: auto; padding: 0 24px 24px; }
    .settings-body h3 { margin: 4px 0 2px; font-size: 15px; font-weight: 600; }
    .settings-body .hint { margin: 0 0 12px; color: var(--muted); font-size: 12px; }
    .setting-row {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 14px 0;
      border-bottom: 1px solid var(--line);
    }
    .setting-row:last-child { border-bottom: none; }
    .setting-row .copy { flex: 1; min-width: 0; }
    .setting-row .copy strong { display: block; font-size: 14px; font-weight: 500; }
    .setting-row .copy small { color: var(--muted); font-size: 12px; }
    .setting-row select, .setting-row input {
      height: 34px;
      border: none;
      border-radius: 999px;
      background: var(--surface-2);
      padding: 0 14px;
      font-size: 13px;
      color: var(--text);
      outline: none;
      max-width: 240px;
    }
    .settings-body form.stack, .stack { display: grid; gap: 10px; }
    .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .settings-body label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    .settings-body label input, .settings-body label select, .settings-body label textarea {
      width: 100%;
      max-width: none;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--surface);
      padding: 0 10px;
      font-size: 13px;
      color: var(--text);
      outline: none;
    }
    .settings-body label textarea { height: auto; min-height: 74px; padding: 8px 10px; resize: vertical; }
    .settings-body .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .settings-body .row.end { justify-content: flex-end; }
    .settings-body .row button, .settings-body form button {
      height: 32px;
      padding: 0 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface);
      font-size: 12.5px;
      font-weight: 500;
      transition: background .16s ease;
    }
    .settings-body .row button:hover, .settings-body form button:hover { background: var(--panel-hover); }
    .settings-body button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .settings-body button.danger { color: var(--danger); }
    .automation-list { display: grid; gap: 8px; margin-top: 12px; }
    .automation {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
      padding: 12px;
      display: grid;
      gap: 8px;
    }
    .automation-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .automation-meta, .automation .tiny { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .about-links { display: grid; gap: 8px; padding-top: 8px; }
    .metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; padding-top: 8px; }
    .metric {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
      padding: 12px;
      display: grid;
      gap: 2px;
    }
    .metric span { color: var(--muted); font-size: 11px; }
    .metric strong { font-size: 18px; font-weight: 600; }
    .metric small { color: var(--muted); font-size: 11px; }

    @media (max-width: 860px) {
      .app { grid-template-columns: 64px minmax(0, 1fr); }
      .app:not(.rail) .sidebar { padding: 14px 10px 6px; align-items: center; }
      .app:not(.rail) .logo-row { height: 36px; padding: 0; margin-bottom: 12px; justify-content: center; }
      .app:not(.rail) .brand span:not(.logo-wrap), .app:not(.rail) .harness-tag { display: none; }
      .app:not(.rail) .logo-row .icon-btn { display: none; }
      .app:not(.rail) .new-session { width: 36px; height: 36px; padding: 0; margin: 0 0 12px; border-color: transparent; background: transparent; }
      .app:not(.rail) .new-session span { display: none; }
      .app:not(.rail) .section-label, .app:not(.rail) .filter-row,
      .app:not(.rail) .runs .run-title, .app:not(.rail) .runs .run-time { display: none; }
      .app:not(.rail) .runs { align-items: center; }
      .app:not(.rail) .run-item { width: 36px; justify-content: center; padding: 0; }
      .app:not(.rail) .side-foot { display: flex; flex-direction: column; align-items: center; }
      .app:not(.rail) .side-foot-row { width: 36px; height: 36px; justify-content: center; padding: 0; border-radius: 999px; }
      .app:not(.rail) .side-foot-row .grow, .app:not(.rail) .side-foot-row .tiny { display: none; }
      .conversation, .composer-zone { padding-inline: 12px; }
      .settings-nav { width: 132px; }
      .grid-2, .metric-grid { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
      }
    }
  </style>
</head>
<body>
  <div class="app" id="appFrame">
    <aside class="sidebar">
      <div class="logo-row">
        <button class="brand" id="brandHome" type="button" title="Libre Claw Dashboard">
          <span class="logo-wrap" role="img" aria-label="Libre Claw lobster">🦞</span>
          <span>Libre Claw</span>
          <span class="harness-tag">HARNESS</span>
        </button>
        <button class="icon-btn" id="railToggle" type="button" aria-label="Collapse sidebar" title="Collapse sidebar">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><line x1="9" y1="4" x2="9" y2="20"/></svg>
        </button>
      </div>

      <button class="new-session" id="focusRunInput" type="button">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" width="15" height="15"><path d="M12 5v14M5 12h14"/></svg>
        <span>New Session</span>
      </button>

      <div class="section-label">
        <span>Runs</span>
        <span class="filters"><span id="runCount" class="tiny">0 runs</span></span>
      </div>
      <div class="filter-row" aria-label="Run filters">
        <input id="runSearch" type="search" placeholder="Search runs">
        <select id="runStateFilter" aria-label="Filter runs by state">
          <option value="">All states</option>
          <option value="running">Running</option>
          <option value="blocked">Blocked</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      <div class="runs" id="runs"></div>

      <div class="side-foot">
        <button class="side-foot-row" id="openSettings" type="button">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          <span class="grow">Settings</span>
        </button>
        <div class="side-foot-row" role="status" title="Daemon status">
          <span id="healthDot" class="status-dot" aria-label="Daemon status"></span>
          <span class="grow" id="daemonStatus">...</span>
          <span class="tiny" id="lastRefresh">Not refreshed yet</span>
        </div>
      </div>
    </aside>

    <main class="main">
      <div class="main-head">
        <h1 id="selectedTitle">No run selected</h1>
        <span class="pill" id="selectedState">idle</span>
        <button id="cancelRun" class="pill-btn danger" type="button" disabled>Cancel</button>
        <button id="refreshAll" class="pill-btn" type="button">Refresh</button>
      </div>
      <div class="view-tabs" role="tablist">
        <button class="view-tab active" id="tabChat" role="tab" aria-selected="true" type="button">Chat</button>
        <button class="view-tab" id="tabTrajectory" role="tab" aria-selected="false" type="button">Trajectory</button>
        <span class="spacer"></span>
        <span class="tiny" id="eventCount">0 events</span>
        <select id="eventFilter" aria-label="Filter timeline events" hidden>
          <option value="">All events</option>
          <option value="message">Messages</option>
          <option value="tool">Tools</option>
          <option value="permission">Approvals</option>
          <option value="error">Errors</option>
          <option value="run">Run state</option>
        </select>
      </div>

      <div class="conversation" id="timeline"></div>

      <div class="composer-zone">
        <div id="permissions"></div>
        <div id="notice" class="notice" role="status"></div>
        <form id="runForm" class="composer">
          <textarea id="runMessage" required rows="1" placeholder="Describe what you want Libre Claw to do"></textarea>
          <div class="composer-controls">
            <select id="runProvider" aria-label="Provider">
              <option value="">default provider</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI API</option>
              <option value="openrouter">OpenRouter</option>
              <option value="moonshot">Kimi Code / Moonshot</option>
              <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
              <option value="codex">OpenAI Codex</option>
            </select>
            <input id="runModel" placeholder="default model" aria-label="Model">
            <datalist id="llamacppModels"></datalist>
            <span class="spacer"></span>
            <button class="send-btn" type="submit" aria-label="Start run" title="Start run">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
            </button>
          </div>
        </form>
        <div class="status-strip" id="statusStrip">
          <span id="stripMeta">Each run keeps its own timeline and approvals.</span>
          <span class="sep">|</span>
          <span id="activeRunsLabel"><span id="activeRuns">0</span> active</span>
          <span class="sep">|</span>
          <span id="usageTokensLabel"><span id="usageTokens">0</span> tokens</span>
        </div>
      </div>
    </main>
  </div>

  <div class="overlay" id="settingsOverlay" role="dialog" aria-modal="true" aria-label="Settings">
    <div class="mask" id="settingsMask"></div>
    <div class="settings-panel">
      <nav class="settings-nav">
        <h2>Settings</h2>
        <button class="active" data-pane="general" type="button">General</button>
        <button data-pane="models" type="button">Models</button>
        <button data-pane="schedules" type="button">Schedules</button>
        <button data-pane="usage" type="button">Usage</button>
        <button data-pane="about" type="button">About</button>
      </nav>
      <div class="settings-content">
        <div class="settings-head">
          <button class="icon-btn" id="closeSettings" type="button" aria-label="Close settings">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="settings-body" id="paneGeneral">
          <h3>General</h3>
          <p class="hint">Appearance and dashboard behavior.</p>
          <div class="setting-row">
            <div class="copy">
              <strong>Theme</strong>
              <small>Applies to this browser and persists to the daemon config.</small>
            </div>
            <select id="themeSelect">
              <option value="harness">Harness</option>
              <option value="harness-light">Harness Light</option>
              <option value="lobster">Lobster</option>
              <option value="lobster-light">Lobster Light</option>
              <option value="github-dark">GitHub Dark</option>
              <option value="github-light">GitHub Light</option>
              <option value="monokai-pro">Monokai Pro</option>
              <option value="night-owl">Night Owl</option>
              <option value="tokyo-night">Tokyo Night</option>
              <option value="ayu">Ayu Mirage</option>
              <option value="dracula">Dracula</option>
              <option value="catppuccin-mocha">Catppuccin Mocha</option>
              <option value="catppuccin-latte">Catppuccin Latte</option>
              <option value="gruvbox-dark">Gruvbox Dark</option>
              <option value="nord">Nord</option>
              <option value="solarized-dark">Solarized Dark</option>
              <option value="solarized-light">Solarized Light</option>
              <option value="one-dark-pro">One Dark Pro</option>
              <option value="rose-pine">Rose Pine</option>
              <option value="kanagawa">Kanagawa</option>
              <option value="matrix">Matrix</option>
            </select>
          </div>
          <div class="metric-grid">
            <div class="metric"><span>Daemon</span><strong id="daemonStatusMetric">...</strong><small>localhost API</small></div>
            <div class="metric"><span>Active runs</span><strong id="activeRunsMetric">0</strong><small>queued or running</small></div>
            <div class="metric"><span>Tokens</span><strong id="usageTokensMetric">0</strong><small id="usageExact">0 total</small></div>
          </div>
        </div>
        <div class="settings-body" id="paneModels" hidden>
          <h3>Models</h3>
          <p class="hint">Default provider and model for new runs.</p>
          <form id="modelForm" class="stack">
            <div class="grid-2">
              <label>Provider<select id="configProvider">
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI API</option>
                <option value="openrouter">OpenRouter</option>
                <option value="moonshot">Kimi Code / Moonshot</option>
                <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
                <option value="codex">OpenAI Codex</option>
              </select></label>
              <label>Model<input id="configModel" placeholder="model id"></label>
            </div>
            <div class="row end">
              <span class="tiny" id="modelCurrent" style="margin-inline-end:auto;color:var(--muted);font-size:12px;"></span>
              <button class="primary" type="submit">Save default</button>
            </div>
          </form>
          <div class="setting-row" style="margin-top:8px;">
            <div class="copy">
              <strong>llama.cpp endpoint</strong>
              <small>llama-server or llama-swap base URL; a trailing /v1 is fine.</small>
            </div>
          </div>
          <form id="llamacppForm" class="stack">
            <div class="endpoint-row">
              <input id="llamacppBaseUrl" placeholder="http://localhost:8080" aria-label="llama.cpp base URL" spellcheck="false">
              <button id="llamacppDiscover" type="button">Discover</button>
              <button class="primary" type="submit">Save endpoint</button>
            </div>
            <div id="llamacppDiscovered" class="row"></div>
          </form>
        </div>
        <div class="settings-body" id="paneSchedules" hidden>
          <h3 id="automationFormTitle">Create Schedule</h3>
          <p class="hint">Recurring checks can write reports or notify Telegram.</p>
          <form id="automationForm" class="stack">
            <div class="grid-2">
              <label>Name<input id="automationName" placeholder="HN watch"></label>
              <label>Schedule<input id="automationSchedule" placeholder="every 30 minutes"></label>
            </div>
            <label>Prompt<textarea id="automationPrompt" placeholder="Fetch Hacker News and summarize new notable stories"></textarea></label>
            <div class="grid-2">
              <label>Route<select id="automationRoute"><option value="report">report</option><option value="telegram">telegram</option><option value="tui">tui</option></select></label>
              <label>Telegram chat id<input id="automationChat" inputmode="numeric" placeholder="optional"></label>
            </div>
            <div class="grid-2">
              <label>Status<select id="automationStatus"><option value="active">active</option><option value="paused">paused</option></select></label>
              <label>Provider<select id="automationProvider">
                <option value="">default</option>
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI API</option>
                <option value="openrouter">OpenRouter</option>
                <option value="moonshot">Kimi Code / Moonshot</option>
                <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
                <option value="codex">OpenAI Codex</option>
              </select></label>
            </div>
            <label>Model<input id="automationModel" placeholder="default"></label>
            <div class="row">
              <button id="automationSubmit" class="primary" type="submit">Create Schedule</button>
              <button id="cancelAutomationEdit" type="button" hidden>Cancel Edit</button>
            </div>
          </form>
          <div id="automations" class="automation-list"></div>
        </div>
        <div class="settings-body" id="paneUsage" hidden>
          <h3>Usage</h3>
          <p class="hint">Token consumption and cost across recorded runs.</p>
          <div class="metric-grid">
            <div class="metric"><span>Total tokens</span><strong id="usagePaneTokens">0</strong><small id="usagePaneTokensExact">0</small></div>
            <div class="metric"><span>Requests</span><strong id="usagePaneRequests">0</strong><small id="usagePaneRuns">0 runs</small></div>
            <div class="metric"><span>Cost</span><strong id="usagePaneCost">$0</strong><small>provider-reported</small></div>
          </div>
          <p class="usage-sub">By model</p>
          <div class="usage-table-wrap"><table class="usage-table" id="usageByModel"></table></div>
          <p class="usage-sub">Recent runs</p>
          <div class="usage-table-wrap"><table class="usage-table" id="usageRecent"></table></div>
        </div>
        <div class="settings-body" id="paneAbout" hidden>
          <h3>About</h3>
          <p class="hint">Libre Claw dashboard — local control plane for runs, approvals, schedules, and usage.</p>
          <nav class="about-links" aria-label="Dashboard footer links">
            <a href="https://libreclaw.sh" target="_blank" rel="noreferrer">libreclaw.sh</a>
            <a href="https://github.com/kroonen-ai/libre-claw" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://git.kroonen.ai/kroonen-ai/libre-claw" target="_blank" rel="noreferrer">GitLab mirror</a>
            <a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">Apache-2.0</a>
            <a href="https://kroonen.ai" target="_blank" rel="noreferrer">Kroonen AI</a>
          </nav>
        </div>
      </div>
    </div>
  </div>
  <script>
    const state = { selectedRunId: "", runs: [], events: [], editingAutomationId: "", view: "chat", streaming: false, composing: false };
    const $ = (id) => document.getElementById(id);
    const THEME_KEY = "libre-claw-dashboard-theme";
    const RAIL_KEY = "libre-claw-dashboard-rail";
    const THEMES = new Set([
      "harness",
      "harness-light",
      "lobster",
      "lobster-light",
      "github-dark",
      "github-light",
      "monokai-pro",
      "night-owl",
      "tokyo-night",
      "ayu",
      "dracula",
      "catppuccin-mocha",
      "catppuccin-latte",
      "gruvbox-dark",
      "nord",
      "solarized-dark",
      "solarized-light",
      "one-dark-pro",
      "rose-pine",
      "kanagawa",
      "matrix",
    ]);
    const THEME_ALIASES = new Map([
      ["", "lobster"],
      ["default", "lobster"],
      ["dark", "lobster"],
      ["libre", "lobster"],
      ["libre-dark", "lobster"],
      ["libre-default", "lobster"],
      ["codex-lobster", "lobster"],
      ["clear", "lobster-light"],
      ["lobster-clear", "lobster-light"],
      ["codex-lobster-light", "lobster-light"],
      ["light", "github-light"],
    ]);

    function applyTheme(value) {
      const normalized = THEME_ALIASES.get(String(value || "").toLowerCase()) || value;
      const theme = THEMES.has(normalized) ? normalized : "lobster";
      document.documentElement.dataset.theme = theme;
      localStorage.setItem(THEME_KEY, theme);
      const picker = $("themeSelect");
      if (picker) picker.value = theme;
      return theme;
    }

    async function saveTheme(value) {
      const theme = applyTheme(value);
      try {
        const response = await fetch("/config/theme", {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({theme, persist_global: true}),
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        setNotice(`Theme saved: ${data.label || theme}`);
      } catch (error) {
        setNotice(`Theme changed locally but could not be saved: ${error.message || error}`, true);
      }
    }

    function initTheme() {
      applyTheme(localStorage.getItem(THEME_KEY) || document.documentElement.dataset.theme || "lobster");
      $("themeSelect").addEventListener("change", (event) => {
        void saveTheme(event.target.value);
      });
    }

    function initRail() {
      if (localStorage.getItem(RAIL_KEY) === "1") $("appFrame").classList.add("rail");
      $("railToggle").addEventListener("click", () => {
        const rail = $("appFrame").classList.toggle("rail");
        localStorage.setItem(RAIL_KEY, rail ? "1" : "0");
      });
      $("brandHome").addEventListener("click", () => {
        if ($("appFrame").classList.contains("rail")) {
          $("appFrame").classList.remove("rail");
          localStorage.setItem(RAIL_KEY, "0");
          return;
        }
        newSession();
      });
    }

    let noticeTimer = 0;
    function setNotice(text, error = false) {
      const box = $("notice");
      box.textContent = text;
      box.className = `notice visible ${error ? "error" : ""}`;
      window.clearTimeout(noticeTimer);
      if (!error) noticeTimer = window.setTimeout(() => { box.className = "notice"; }, 6000);
    }

    async function request(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || response.statusText);
      return payload;
    }

    function formatTime(value) {
      if (!value) return "";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
    }

    function scheduleTimezone(schedule) {
      const text = String(schedule || "").trim();
      const explicit = text.match(/\s(?:@|in)\s+([A-Za-z0-9_.\/+-]+)$/);
      if (explicit) return explicit[1];
      const implicit = text.match(/\s([A-Za-z_]+\/[A-Za-z0-9_.\/+-]+)$/);
      return implicit ? implicit[1] : "";
    }

    function formatAutomationNext(automation) {
      const value = automation.next_run_at;
      if (!value) return "";
      const zone = scheduleTimezone(automation.schedule);
      if (!zone) return formatTime(value);
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      try {
        return `${new Intl.DateTimeFormat(undefined, {
          dateStyle: "short",
          timeStyle: "medium",
          timeZone: zone,
          timeZoneName: "short",
        }).format(date)} (${zone})`;
      } catch (_error) {
        return formatTime(value);
      }
    }

    function formatShortTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
    }

    function formatRelativeTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      const seconds = Math.round((Date.now() - date.getTime()) / 1000);
      if (seconds < 60) return "now";
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) return `${minutes}m`;
      const hours = Math.round(minutes / 60);
      if (hours < 24) return `${hours}h`;
      const days = Math.round(hours / 24);
      if (days < 30) return `${days}d`;
      return formatShortTime(value);
    }

    function truncate(value, length = 140) {
      const text = String(value || "");
      return text.length > length ? `${text.slice(0, length - 1)}...` : text;
    }

    function formatCompactNumber(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) return "0";
      return new Intl.NumberFormat(undefined, {
        notation: "compact",
        maximumFractionDigits: number >= 1000000 ? 1 : 0,
      }).format(number);
    }

    function formatExactNumber(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) return "0";
      return new Intl.NumberFormat().format(number);
    }

    function pill(stateValue) {
      const span = document.createElement("span");
      span.className = `pill ${stateValue}`;
      span.textContent = stateValue;
      return span;
    }

    function safeClass(value) {
      return String(value || "event").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    }

    async function refreshHealth() {
      const health = await request("/health");
      const label = health.ok ? "online" : "offline";
      $("daemonStatus").textContent = label;
      $("daemonStatusMetric").textContent = label;
      $("activeRuns").textContent = health.active_runs ?? 0;
      $("activeRunsMetric").textContent = health.active_runs ?? 0;
      $("healthDot").className = `status-dot ${health.ok ? "online" : "offline"}`;
    }

    async function refreshUsage() {
      const usage = await request("/usage?limit=250");
      const totalTokens = usage.summary?.total_tokens ?? 0;
      const compact = formatCompactNumber(totalTokens);
      const tokenNode = $("usageTokens");
      tokenNode.textContent = compact;
      tokenNode.title = `${formatExactNumber(totalTokens)} tokens`;
      $("usageTokensMetric").textContent = compact;
      $("usageExact").textContent = `${formatExactNumber(totalTokens)} provider tokens`;
    }

    async function refreshRuns() {
      const payload = await request("/runs?limit=40");
      state.runs = payload.runs || [];
      renderRuns();
      if (state.selectedRunId && !state.runs.some((run) => run.run_id === state.selectedRunId)) {
        state.selectedRunId = "";
        clearSelectedRun();
      }
      if (!state.selectedRunId && !state.composing && state.runs[0]) await selectRun(state.runs[0].run_id);
    }

    function renderRuns() {
      const container = $("runs");
      container.replaceChildren();
      $("runCount").textContent = `${state.runs.length} ${state.runs.length === 1 ? "run" : "runs"}`;
      if (!state.runs.length) {
        container.append(empty("No runs yet."));
        state.selectedRunId = "";
        clearSelectedRun();
        return;
      }
      const query = $("runSearch").value.trim().toLowerCase();
      const stateFilter = $("runStateFilter").value;
      const filtered = state.runs.filter((run) => {
        const haystack = `${run.title || ""} ${run.run_id || ""} ${run.provider || ""} ${run.model || ""}`.toLowerCase();
        return (!query || haystack.includes(query)) && (!stateFilter || run.state === stateFilter);
      });
      if (!filtered.length) {
        container.append(empty("No matching runs."));
        return;
      }
      for (const run of filtered) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `run-item ${run.run_id === state.selectedRunId ? "active" : ""}`;
        button.title = `${run.run_id} | ${run.provider}:${run.model}`;
        const dot = document.createElement("span");
        dot.className = `state-dot ${safeClass(run.state)}`;
        const title = document.createElement("span");
        title.className = "run-title";
        title.textContent = run.title || "Untitled run";
        const updated = document.createElement("span");
        updated.className = "run-time";
        updated.textContent = formatRelativeTime(run.updated_at);
        button.append(dot, title, updated);
        button.addEventListener("click", () => selectRun(run.run_id));
        container.append(button);
      }
    }

    async function selectRun(runId) {
      state.selectedRunId = runId;
      state.composing = false;
      renderRuns();
      await refreshRunDetail();
    }

    function newSession() {
      state.selectedRunId = "";
      state.composing = true;
      state.streaming = false;
      window.clearTimeout(streamTimer);
      resetStreamNode();
      clearSelectedRun();
      renderRuns();
      $("runMessage").focus();
    }

    function clearSelectedRun() {
      state.selectedRunState = "";
      syncComposerMode();
      $("selectedTitle").textContent = "New session";
      $("selectedState").textContent = "idle";
      $("selectedState").className = "pill";
      $("cancelRun").disabled = true;
      $("stripMeta").textContent = "Each run keeps its own timeline and approvals.";
      state.events = [];
      renderEvents();
      renderPermissions([]);
    }

    async function refreshRunDetail() {
      if (!state.selectedRunId) return;
      const detail = await request(`/runs/${state.selectedRunId}`);
      const run = detail.run;
      $("selectedTitle").textContent = run.title || "Untitled run";
      $("selectedTitle").title = `${run.run_id} | updated ${formatTime(run.updated_at)}`;
      $("selectedState").textContent = run.state;
      $("selectedState").className = `pill ${run.state}`;
      state.selectedRunState = run.state;
      $("cancelRun").disabled = !["queued", "running", "blocked"].includes(run.state);
      syncComposerMode();
      $("stripMeta").textContent = `${run.run_id} | ${run.provider}:${run.model}`;
      const events = await request(`/runs/${state.selectedRunId}/events?after=0`);
      state.events = events.events || [];
      scheduleStream(run.state);
      renderEvents();
      renderPermissions(detail.pending_permissions || []);
    }

    /* Token streaming: while the selected run is live, new events are pulled
       incrementally (`?after=<id>`) on an adaptive loop. Pure-delta batches
       update only the live assistant node, and a requestAnimationFrame
       typewriter smooths each chunk into a per-character reveal instead of a
       block repaint. */
    let streamTimer = 0;
    const STREAM_STATES = new Set(["queued", "running", "blocked"]);
    const stream = { node: null, text: "", shown: 0, raf: 0 };

    function lastNumericEventId() {
      let max = 0;
      for (const event of state.events) {
        const id = Number(event.event_id);
        if (Number.isFinite(id) && id > max) max = id;
      }
      return max;
    }

    function resetStreamNode() {
      window.cancelAnimationFrame(stream.raf);
      stream.node = null;
      stream.text = "";
      stream.shown = 0;
      stream.raf = 0;
    }

    function scheduleStream(runState) {
      window.clearTimeout(streamTimer);
      state.streaming = STREAM_STATES.has(runState);
      if (!state.streaming) {
        resetStreamNode();
        renderEvents();
        return;
      }
      streamTimer = window.setTimeout(() => { void pollRunEvents(); }, 300);
    }

    function nearBottom(container) {
      return container.scrollHeight - container.scrollTop - container.clientHeight < 80;
    }

    function paintStreamNode() {
      const shownText = stream.text.slice(0, stream.shown);
      const replacement = renderMarkdown(shownText);
      const caret = document.createElement("span");
      caret.className = "streaming-caret";
      (replacement.lastElementChild || replacement).append(caret);
      stream.node.replaceWith(replacement);
      stream.node = replacement;
    }

    function pumpStream() {
      stream.raf = 0;
      if (!stream.node || !state.streaming) return;
      const backlog = stream.text.length - stream.shown;
      if (backlog <= 0) return;
      // Catch-up curve: reveal faster the further behind the display is, so
      // bursts stay smooth without ever lagging the model.
      const step = Math.max(2, Math.ceil(backlog / 16));
      stream.shown = Math.min(stream.text.length, stream.shown + step);
      const container = $("timeline");
      const stick = nearBottom(container);
      paintStreamNode();
      if (stick) container.scrollTop = container.scrollHeight;
      if (stream.shown < stream.text.length) {
        stream.raf = requestAnimationFrame(pumpStream);
      }
    }

    /* Append pure assistant-delta batches to the live node; anything else
       falls back to a full re-render. Returns true when handled in place. */
    function tryAppendStream(fresh) {
      if (state.view !== "chat") return false;
      if (!fresh.every((event) => event.type === "assistant_delta" || event.type === "usage")) return false;
      const text = fresh
        .filter((event) => event.type === "assistant_delta")
        .map((event) => event.data?.text || "")
        .join("");
      if (!text) return true;
      const container = $("timeline");
      if (!stream.node || !container.contains(stream.node)) {
        container.querySelector(".empty")?.remove();
        const node = document.createElement("div");
        node.className = "msg-md";
        container.append(node);
        stream.node = node;
        stream.text = "";
        stream.shown = 0;
      }
      stream.text += text;
      if (!stream.raf) stream.raf = requestAnimationFrame(pumpStream);
      return true;
    }

    async function pollRunEvents() {
      const runId = state.selectedRunId;
      if (!runId || !state.streaming) return;
      let sawDelta = false;
      try {
        const payload = await request(`/runs/${runId}/events?after=${lastNumericEventId()}`);
        if (runId !== state.selectedRunId) return;
        const fresh = payload.events || [];
        if (fresh.length) {
          state.events.push(...fresh);
          if (fresh.some((event) => event.type === "run_finished" || event.type === "permission_request")) {
            resetStreamNode();
            await refreshRunDetail();
            await refreshRuns();
            return;
          }
          sawDelta = fresh.some((event) => event.type === "assistant_delta");
          if (!tryAppendStream(fresh)) {
            resetStreamNode();
            renderEvents();
          }
        }
      } catch (_error) {
        /* transient poll errors: keep streaming */
      }
      streamTimer = window.setTimeout(() => { void pollRunEvents(); }, sawDelta ? 250 : 900);
    }

    function setView(view) {
      state.view = view;
      $("tabChat").classList.toggle("active", view === "chat");
      $("tabChat").setAttribute("aria-selected", String(view === "chat"));
      $("tabTrajectory").classList.toggle("active", view === "trajectory");
      $("tabTrajectory").setAttribute("aria-selected", String(view === "trajectory"));
      $("eventFilter").hidden = view !== "trajectory";
      renderEvents();
    }

    function renderEvents() {
      const container = $("timeline");
      const stick = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
      container.replaceChildren();
      if (!state.events.length) {
        $("eventCount").textContent = "0 events";
        container.append(empty(state.selectedRunId ? "No events yet." : "Start a run to see its conversation here."));
        return;
      }
      const displayEvents = coalescedEvents(state.events);
      if (state.view === "chat") {
        renderChat(container, displayEvents);
        $("eventCount").textContent = `${displayEvents.length} ${displayEvents.length === 1 ? "card" : "cards"} from ${state.events.length} events`;
      } else {
        renderTrajectory(container, displayEvents);
      }
      if (stick) container.scrollTop = container.scrollHeight;
    }

    /* Minimal safe markdown renderer: DOM-built (no innerHTML), http(s) links only. */
    function mdInline(target, text) {
      const pattern = /(`[^`]+`)|\[([^\]]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>()]+[^\s<>().,!?;:'"])|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(~~[^~]+~~)/g;
      let last = 0;
      let match;
      while ((match = pattern.exec(text))) {
        if (match.index > last) target.append(text.slice(last, match.index));
        if (match[1]) {
          const code = document.createElement("code");
          code.textContent = match[1].slice(1, -1);
          target.append(code);
        } else if (match[2] !== undefined) {
          if (/^https?:\/\//i.test(match[3])) {
            const link = document.createElement("a");
            link.href = match[3];
            link.target = "_blank";
            link.rel = "noreferrer noopener";
            mdInline(link, match[2]);
            target.append(link);
          } else {
            target.append(match[0]);
          }
        } else if (match[4]) {
          const link = document.createElement("a");
          link.href = match[4];
          link.target = "_blank";
          link.rel = "noreferrer noopener";
          link.textContent = match[4];
          target.append(link);
        } else if (match[5]) {
          const strong = document.createElement("strong");
          mdInline(strong, match[5].slice(2, -2));
          target.append(strong);
        } else if (match[6]) {
          const em = document.createElement("em");
          mdInline(em, match[6].slice(1, -1));
          target.append(em);
        } else if (match[7]) {
          const strike = document.createElement("s");
          strike.textContent = match[7].slice(2, -2);
          target.append(strike);
        }
        last = pattern.lastIndex;
      }
      if (last < text.length) target.append(text.slice(last));
    }

    function codeBlock(code, lang) {
      const wrap = document.createElement("div");
      wrap.className = "code-block";
      const head = document.createElement("div");
      head.className = "code-head";
      const label = document.createElement("span");
      label.textContent = lang || "code";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "code-copy";
      copy.textContent = "Copy";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code);
          copy.textContent = "Copied";
        } catch (_error) {
          copy.textContent = "Failed";
        }
        window.setTimeout(() => { copy.textContent = "Copy"; }, 1600);
      });
      head.append(label, copy);
      const pre = document.createElement("pre");
      const codeNode = document.createElement("code");
      codeNode.textContent = code;
      pre.append(codeNode);
      wrap.append(head, pre);
      return wrap;
    }

    function renderMarkdown(text) {
      const root = document.createElement("div");
      root.className = "msg-md";
      const lines = String(text || "").split("\n");
      const para = [];
      const flushPara = () => {
        if (!para.length) return;
        const p = document.createElement("p");
        mdInline(p, para.join(" "));
        root.append(p);
        para.length = 0;
      };
      const rowCells = (raw) => raw.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((cell) => cell.trim());
      let i = 0;
      while (i < lines.length) {
        const line = lines[i];
        const fence = line.match(/^```(\S*)\s*$/);
        if (fence) {
          flushPara();
          const body = [];
          i += 1;
          while (i < lines.length && !/^```\s*$/.test(lines[i])) { body.push(lines[i]); i += 1; }
          i += 1;
          root.append(codeBlock(body.join("\n"), fence[1] || ""));
          continue;
        }
        if (/^\s*$/.test(line)) { flushPara(); i += 1; continue; }
        const heading = line.match(/^(#{1,4})\s+(.*)$/);
        if (heading) {
          flushPara();
          const level = Math.min(heading[1].length + 2, 6);
          const node = document.createElement(`h${level}`);
          mdInline(node, heading[2]);
          root.append(node);
          i += 1;
          continue;
        }
        if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushPara(); root.append(document.createElement("hr")); i += 1; continue; }
        const quote = line.match(/^>\s?(.*)$/);
        if (quote) {
          flushPara();
          const buffer = [quote[1]];
          i += 1;
          while (i < lines.length) {
            const next = lines[i].match(/^>\s?(.*)$/);
            if (!next) break;
            buffer.push(next[1]);
            i += 1;
          }
          const blockquote = document.createElement("blockquote");
          const p = document.createElement("p");
          mdInline(p, buffer.join(" "));
          blockquote.append(p);
          root.append(blockquote);
          continue;
        }
        const list = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
        if (list) {
          flushPara();
          const node = document.createElement(/^\d/.test(list[1]) ? "ol" : "ul");
          while (i < lines.length) {
            const item = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
            if (!item) break;
            const li = document.createElement("li");
            mdInline(li, item[2]);
            node.append(li);
            i += 1;
          }
          root.append(node);
          continue;
        }
        if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+$/.test(lines[i + 1]) && lines[i + 1].includes("-") && lines[i + 1].includes("|")) {
          flushPara();
          const table = document.createElement("table");
          const thead = document.createElement("thead");
          const headRow = document.createElement("tr");
          for (const cell of rowCells(line)) {
            const th = document.createElement("th");
            mdInline(th, cell);
            headRow.append(th);
          }
          thead.append(headRow);
          table.append(thead);
          const tbody = document.createElement("tbody");
          i += 2;
          while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
            const tr = document.createElement("tr");
            for (const cell of rowCells(lines[i])) {
              const td = document.createElement("td");
              mdInline(td, cell);
              tr.append(td);
            }
            tbody.append(tr);
            i += 1;
          }
          table.append(tbody);
          const wrap = document.createElement("div");
          wrap.className = "table-wrap";
          wrap.append(table);
          root.append(wrap);
          continue;
        }
        para.push(line.trim());
        i += 1;
      }
      flushPara();
      return root;
    }

    function renderChat(container, displayEvents) {
      for (const event of displayEvents) {
        const data = event.data || {};
        if (event.type === "user_message") {
          const wrap = document.createElement("div");
          wrap.className = "msg-user";
          const bubble = document.createElement("div");
          bubble.textContent = data.content || "";
          wrap.append(bubble);
          container.append(wrap);
        } else if (event.type === "assistant_message" || event.type === "assistant_delta") {
          const node = renderMarkdown(data.text || "");
          if (state.streaming && event === displayEvents.at(-1)) {
            const caret = document.createElement("span");
            caret.className = "streaming-caret";
            (node.lastElementChild || node).append(caret);
            // Incremental delta batches continue from this node without a
            // full re-render; the already-visible text never re-animates.
            stream.node = node;
            stream.text = data.text || "";
            stream.shown = stream.text.length;
          }
          container.append(node);
        } else if (event.type === "tool_call" || event.type === "tool_result") {
          const details = document.createElement("details");
          details.className = `msg-tool ${data.is_error ? "is-error" : ""}`;
          const summary = document.createElement("summary");
          const label = document.createElement("span");
          label.textContent = event.type === "tool_call" ? "Tool call" : (data.is_error ? "Tool error" : "Tool result");
          const name = document.createElement("span");
          name.className = "tool-name";
          name.textContent = data.name || "unknown";
          summary.append(label, name);
          const body = document.createElement("pre");
          body.textContent = event.type === "tool_call"
            ? JSON.stringify(data.arguments || {}, null, 2)
            : truncate(data.content, 2200);
          details.append(summary, body);
          container.append(details);
        } else if (event.type === "error" || data.is_error) {
          const node = document.createElement("div");
          node.className = "msg-error";
          node.textContent = data.message || eventText(event);
          container.append(node);
        } else if (event.type === "permission_request" || event.type === "permission_result") {
          const node = document.createElement("div");
          node.className = "msg-note";
          const text = eventText(event);
          node.textContent = text ? `${eventTitle(event)} — ${truncate(text.replaceAll("\n", " · "), 160)}` : eventTitle(event);
          container.append(node);
        }
      }
    }

    function renderTrajectory(container, displayEvents) {
      const filter = $("eventFilter").value;
      const visible = displayEvents.filter((event) => eventMatchesFilter(event, filter));
      $("eventCount").textContent = filter
        ? `${visible.length} of ${displayEvents.length} cards`
        : `${displayEvents.length} ${displayEvents.length === 1 ? "card" : "cards"} from ${state.events.length} events`;
      if (!visible.length) {
        container.append(empty("No matching events."));
        return;
      }
      for (const event of visible) {
        const item = document.createElement("div");
        const data = event.data || {};
        item.className = `event event-${safeClass(event.type)} ${data.is_error ? "is-error" : ""}`;
        const head = document.createElement("div");
        head.className = "event-head";
        const type = document.createElement("div");
        type.className = "event-type";
        type.textContent = eventTitle(event);
        const time = document.createElement("div");
        time.className = "event-time";
        time.textContent = `#${event.event_id} | ${formatShortTime(event.timestamp)}`;
        const body = document.createElement("pre");
        body.textContent = eventText(event);
        head.append(type, time);
        item.append(head, body);
        container.append(item);
      }
    }

    function coalescedEvents(events) {
      const output = [];
      for (const event of events) {
        const text = event.type === "assistant_delta" ? event.data?.text || "" : "";
        const previous = output.at(-1);
        if (text && previous?.type === "assistant_message") {
          previous.data.text += text;
          previous.event_id = `${previous.data.start_event_id}-${event.event_id}`;
          previous.timestamp = event.timestamp;
          continue;
        }
        if (text) {
          output.push({
            ...event,
            type: "assistant_message",
            data: { text, start_event_id: event.event_id },
          });
          continue;
        }
        output.push(event);
      }
      return output;
    }

    function eventMatchesFilter(event, filter) {
      if (!filter) return true;
      if (filter === "message") return ["user_message", "assistant_delta", "assistant_message"].includes(event.type);
      if (filter === "tool") return ["tool_call", "tool_result"].includes(event.type);
      if (filter === "permission") return event.type.startsWith("permission");
      if (filter === "error") return event.type === "error" || event.data?.is_error;
      if (filter === "run") return event.type.startsWith("run_") || event.type === "usage";
      return event.type === filter;
    }

    function eventTitle(event) {
      const data = event.data || {};
      if (event.type === "user_message") return "User message";
      if (event.type === "assistant_delta" || event.type === "assistant_message") return "Assistant";
      if (event.type === "tool_call") return `Tool call: ${data.name || "unknown"}`;
      if (event.type === "tool_result") return `Tool ${data.is_error ? "error" : "result"}: ${data.name || "unknown"}`;
      if (event.type === "permission_request") return `Approval needed: ${data.name || data.tool_call_id || "tool"}`;
      if (event.type === "permission_result") return `Approval: ${data.resolution || "resolved"}`;
      if (event.type === "usage") return "Usage";
      if (event.type === "run_started") return "Run started";
      if (event.type === "run_continued") return "Session continued";
      if (event.type === "run_finished") return `Run finished${data.state ? `: ${data.state}` : ""}`;
      if (event.type === "error") return "Error";
      return event.type.replaceAll("_", " ");
    }

    function eventText(event) {
      const data = event.data || {};
      if (event.type === "assistant_delta" || event.type === "assistant_message") return data.text || "";
      if (event.type === "user_message") return data.content || "";
      if (event.type === "tool_call") return `${data.name}\n${JSON.stringify(data.arguments || {}, null, 2)}`;
      if (event.type === "tool_result") return `${data.name} ${data.is_error ? "error" : "result"}\n${truncate(data.content, 2200)}`;
      if (event.type === "permission_request") return `${data.name}\n${JSON.stringify(data.arguments || {}, null, 2)}`;
      if (event.type === "usage") {
        const input = data.usage?.input_tokens ?? data.input_tokens ?? 0;
        const output = data.usage?.output_tokens ?? data.output_tokens ?? 0;
        const cost = data.cost_usd ?? data.cost ?? 0;
        return `input: ${formatExactNumber(input)}\noutput: ${formatExactNumber(output)}\ncost: $${Number(cost || 0).toFixed(6)}`;
      }
      if (event.type === "run_started") return data.title || data.message || "";
      if (event.type === "run_finished") return data.summary || data.state || "";
      if (event.type === "error") return data.message || "";
      return JSON.stringify(data, null, 2);
    }

    function renderPermissions(pendingIds) {
      const container = $("permissions");
      container.replaceChildren();
      if (!pendingIds.length) return;
      for (const id of pendingIds) {
        const event = state.events.find((item) => item.type === "permission_request" && item.data?.tool_call_id === id);
        const box = document.createElement("div");
        box.className = "approval";
        const title = document.createElement("div");
        title.className = "event-type";
        title.textContent = `Approval needed: ${event?.data?.name || id}`;
        const args = document.createElement("pre");
        args.textContent = JSON.stringify(event?.data?.arguments || {}, null, 2);
        const row = document.createElement("div");
        row.className = "row";
        for (const [label, resolution] of [["Allow once", "allow_once"], ["Always tool", "always_allow_tool"], ["Always call", "always_allow_call"], ["Deny", "deny"]]) {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          if (resolution === "allow_once") button.className = "primary";
          if (resolution === "deny") button.className = "danger";
          button.addEventListener("click", () => resolvePermission(id, resolution));
          row.append(button);
        }
        box.append(title, args, row);
        container.append(box);
      }
    }

    async function resolvePermission(toolCallId, resolution) {
      await request(`/runs/${state.selectedRunId}/permissions/${toolCallId}`, {
        method: "POST",
        body: JSON.stringify({ resolution }),
      });
      setNotice(`Permission ${resolution} sent.`);
      await refreshRunDetail();
    }

    async function refreshAutomations() {
      const payload = await request("/automations?limit=50");
      const container = $("automations");
      container.replaceChildren();
      const automations = payload.automations || [];
      if (!automations.length) {
        container.append(empty("No schedules yet."));
        return;
      }
      for (const automation of automations) {
        const box = document.createElement("div");
        box.className = "automation";
        const head = document.createElement("div");
        head.className = "automation-head";
        const title = document.createElement("strong");
        title.textContent = automation.name;
        head.append(title, pill(automation.status));
        const meta = document.createElement("div");
        meta.className = "automation-meta";
        const model = [automation.provider, automation.model].filter(Boolean).join(":") || "default model";
        meta.textContent = `${automation.schedule} | ${automation.route} | ${model} | next ${formatAutomationNext(automation)}`;
        const prompt = document.createElement("div");
        prompt.className = "tiny";
        prompt.textContent = truncate(automation.prompt || "", 180);
        const row = document.createElement("div");
        row.className = "row end";
        const runNow = document.createElement("button");
        runNow.type = "button";
        runNow.textContent = "Run now";
        runNow.className = "primary";
        runNow.addEventListener("click", () => runAutomationNow(automation.automation_id, runNow));
        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "Edit";
        edit.addEventListener("click", () => editAutomation(automation));
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = automation.status === "active" ? "Pause" : "Resume";
        toggle.addEventListener("click", () => toggleAutomation(automation));
        const del = document.createElement("button");
        del.type = "button";
        del.textContent = "Delete";
        del.className = "danger";
        del.addEventListener("click", () => deleteAutomation(automation.automation_id));
        row.append(runNow, edit, toggle, del);
        box.append(head, meta, prompt, row);
        container.append(box);
      }
    }

    function editAutomation(automation) {
      state.editingAutomationId = automation.automation_id;
      $("automationFormTitle").textContent = "Edit Schedule";
      $("automationSubmit").textContent = "Save Changes";
      $("cancelAutomationEdit").hidden = false;
      $("automationName").value = automation.name || "";
      $("automationSchedule").value = automation.schedule || "";
      $("automationPrompt").value = automation.prompt || "";
      $("automationRoute").value = automation.route || "report";
      $("automationChat").value = automation.telegram_chat_id ?? "";
      $("automationStatus").value = automation.status || "active";
      $("automationProvider").value = automation.provider || "";
      $("automationModel").value = automation.model || "";
      openSettingsPane("schedules");
      $("automationName").focus();
      $("automationForm").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    function resetAutomationForm(form) {
      state.editingAutomationId = "";
      $("automationFormTitle").textContent = "Create Schedule";
      $("automationSubmit").textContent = "Create Schedule";
      $("cancelAutomationEdit").hidden = true;
      form.reset();
      $("automationStatus").value = "active";
    }

    function automationFormPayload() {
      const chat = $("automationChat").value.trim();
      return {
        name: $("automationName").value,
        schedule: $("automationSchedule").value,
        prompt: $("automationPrompt").value,
        route: $("automationRoute").value,
        status: $("automationStatus").value,
        provider: $("automationProvider").value,
        model: $("automationModel").value,
        telegram_chat_id: chat || null,
      };
    }

    async function toggleAutomation(automation) {
      const action = automation.status === "active" ? "pause" : "resume";
      await request(`/automations/${automation.automation_id}/${action}`, { method: "POST" });
      await refreshAutomations();
    }

    async function runAutomationNow(id, button) {
      button.disabled = true;
      const originalLabel = button.textContent;
      button.textContent = "Starting...";
      try {
        const payload = await request(`/automations/${id}/run`, { method: "POST" });
        setNotice(`Schedule run ${payload.run.run_id} started.`);
        closeSettingsPanel();
        await Promise.all([refreshAutomations(), refreshRuns()]);
        await selectRun(payload.run.run_id);
      } finally {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    }

    async function deleteAutomation(id) {
      if (!confirm("Delete this schedule?")) return;
      await request(`/automations/${id}`, { method: "DELETE" });
      await refreshAutomations();
    }

    function empty(text) {
      const node = document.createElement("div");
      node.className = "empty";
      node.textContent = text;
      return node;
    }

    /* Settings modal */
    const PANES = ["general", "models", "schedules", "usage", "about"];
    function openSettingsPane(pane) {
      $("settingsOverlay").classList.add("open");
      for (const id of PANES) {
        const active = id === pane;
        $(`pane${id[0].toUpperCase()}${id.slice(1)}`).hidden = !active;
      }
      document.querySelectorAll(".settings-nav button").forEach((button) => {
        button.classList.toggle("active", button.dataset.pane === pane);
      });
      if (pane === "models") {
        void loadModelConfig();
        void loadLlamacppConfig();
      }
      if (pane === "usage") void loadUsagePane();
    }
    function closeSettingsPanel() {
      $("settingsOverlay").classList.remove("open");
    }

    async function loadModelConfig() {
      try {
        const payload = await request("/config/model");
        if (payload.provider) $("configProvider").value = payload.provider;
        if (payload.model) $("configModel").value = payload.model;
        $("modelCurrent").textContent = `current: ${payload.provider || "?"}:${payload.model || "?"}`;
      } catch (error) {
        $("modelCurrent").textContent = String(error.message || error);
      }
    }

    async function loadLlamacppConfig() {
      try {
        const payload = await request("/config/llamacpp");
        $("llamacppBaseUrl").value = payload.base_url || "";
      } catch (_error) {
        /* endpoint config is optional */
      }
    }

    function renderDiscoveredChips(models) {
      const box = $("llamacppDiscovered");
      box.replaceChildren();
      if (!models.length) {
        box.append(empty("No models reported by this endpoint."));
        return;
      }
      for (const item of models) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "model-chip";
        chip.textContent = item.label;
        chip.title = `Use ${item.model}`;
        chip.addEventListener("click", () => {
          $("configProvider").value = "llamacpp";
          $("configModel").value = item.model;
        });
        box.append(chip);
      }
    }

    $("llamacppDiscover").addEventListener("click", async () => {
      const base = $("llamacppBaseUrl").value.trim();
      const query = base ? `?base_url=${encodeURIComponent(base)}` : "";
      try {
        const payload = await request(`/models/llamacpp${query}`);
        renderDiscoveredChips(payload.models || []);
        const count = (payload.models || []).length;
        setNotice(`${count} ${count === 1 ? "model" : "models"} discovered from ${payload.base_url}.`);
      } catch (error) {
        renderDiscoveredChips([]);
        setNotice(String(error.message || error), true);
      }
    });

    $("llamacppForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const payload = await request("/config/llamacpp", {
          method: "PATCH",
          body: JSON.stringify({ base_url: $("llamacppBaseUrl").value.trim(), persist_global: true }),
        });
        $("llamacppBaseUrl").value = payload.base_url;
        llamacppModelsLoaded = false;
        setNotice(`llama.cpp endpoint saved: ${payload.base_url}`);
      } catch (error) {
        setNotice(String(error.message || error), true);
      }
    });

    function usageTable(table, headers, rows) {
      table.replaceChildren();
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const header of headers) {
        const th = document.createElement("th");
        th.textContent = header;
        headRow.append(th);
      }
      thead.append(headRow);
      table.append(thead);
      const tbody = document.createElement("tbody");
      for (const row of rows) {
        const tr = document.createElement("tr");
        for (const cell of row) {
          const td = document.createElement("td");
          td.textContent = cell;
          tr.append(td);
        }
        tbody.append(tr);
      }
      table.append(tbody);
    }

    async function loadUsagePane() {
      try {
        const payload = await request("/usage?limit=250");
        const summary = payload.summary || {};
        $("usagePaneTokens").textContent = formatCompactNumber(summary.total_tokens);
        $("usagePaneTokensExact").textContent = `${formatExactNumber(summary.total_tokens)} total`;
        $("usagePaneRequests").textContent = formatExactNumber(summary.requests);
        $("usagePaneRuns").textContent = `${formatExactNumber(summary.runs)} runs`;
        $("usagePaneCost").textContent = `$${Number(summary.cost || 0).toFixed(4)}`;
        usageTable(
          $("usageByModel"),
          ["Model", "Requests", "Input", "Output", "Total", "Cost"],
          (summary.by_model || []).map((group) => [
            group.name || "unknown",
            formatExactNumber(group.requests),
            formatCompactNumber(group.input_tokens),
            formatCompactNumber(group.output_tokens),
            formatCompactNumber(group.total_tokens),
            `$${Number(group.cost || 0).toFixed(4)}`,
          ]),
        );
        usageTable(
          $("usageRecent"),
          ["Run", "Model", "Tokens", "Cost", "When"],
          (payload.records || []).slice(0, 12).map((record) => [
            record.title || record.run_id,
            `${record.provider}:${record.model}`,
            formatCompactNumber(record.total_tokens),
            `$${Number(record.cost || 0).toFixed(4)}`,
            formatShortTime(record.timestamp),
          ]),
        );
      } catch (error) {
        setNotice(String(error.message || error), true);
      }
    }

    $("modelForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const payload = await request("/config/model", {
          method: "PATCH",
          body: JSON.stringify({
            provider: $("configProvider").value,
            model: $("configModel").value,
            persist_global: true,
          }),
        });
        $("modelCurrent").textContent = `current: ${payload.provider}:${payload.model}`;
        setNotice(`Default model saved: ${payload.provider}:${payload.model}`);
      } catch (error) {
        setNotice(String(error.message || error), true);
      }
    });

    /* llama.cpp model discovery via llama-swap `/v1/models` */
    let llamacppModelsLoaded = false;
    async function loadLlamacppModels() {
      if (llamacppModelsLoaded) return;
      try {
        const payload = await request("/models/llamacpp");
        const list = $("llamacppModels");
        list.replaceChildren();
        for (const item of payload.models || []) {
          const option = document.createElement("option");
          option.value = item.model;
          option.label = item.label;
          list.append(option);
        }
        llamacppModelsLoaded = true;
        const count = (payload.models || []).length;
        setNotice(count
          ? `${count} llama.cpp ${count === 1 ? "model" : "models"} discovered from ${payload.base_url}.`
          : `No llama.cpp models reported by ${payload.base_url}.`);
      } catch (error) {
        setNotice(String(error.message || error), true);
      }
    }

    function syncModelDatalist(select, input) {
      const update = () => {
        if (select.value === "llamacpp") {
          input.setAttribute("list", "llamacppModels");
          void loadLlamacppModels();
        } else if (input.getAttribute("list") === "llamacppModels") {
          input.removeAttribute("list");
        }
      };
      select.addEventListener("change", update);
      update();
    }

    syncModelDatalist($("runProvider"), $("runModel"));
    syncModelDatalist($("configProvider"), $("configModel"));
    syncModelDatalist($("automationProvider"), $("automationModel"));

    /* The composer continues the selected session; New Session starts a thread. */
    function composerMode() {
      if (!state.selectedRunId) return "new";
      if (STREAM_STATES.has(state.selectedRunState)) return "busy";
      return "reply";
    }

    function syncComposerMode() {
      const mode = composerMode();
      $("runMessage").placeholder = mode === "reply"
        ? "Reply to this session"
        : "Describe what you want Libre Claw to do";
    }

    $("runForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const mode = composerMode();
      if (mode === "busy") {
        setNotice("This session is still working - wait for it to finish or cancel it.", true);
        return;
      }
      const body = {
        message: $("runMessage").value,
        surface: "dashboard",
      };
      if ($("runProvider").value.trim()) body.provider = $("runProvider").value.trim();
      if ($("runModel").value.trim()) body.model = $("runModel").value.trim();
      try {
        const path = mode === "reply" ? `/runs/${state.selectedRunId}/messages` : "/runs";
        const payload = await request(path, { method: "POST", body: JSON.stringify(body) });
        $("runMessage").value = "";
        autoGrow();
        setNotice(mode === "reply" ? "Reply sent." : `Run ${payload.run.run_id} started.`);
        await refreshRuns();
        await selectRun(payload.run.run_id);
      } catch (error) {
        setNotice(String(error.message || error), true);
      }
    });

    $("automationForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = automationFormPayload();
      const editingId = state.editingAutomationId;
      const path = editingId ? `/automations/${editingId}` : "/automations";
      const method = editingId ? "PUT" : "POST";
      const payload = await request(path, { method, body: JSON.stringify(body) });
      setNotice(`Schedule ${payload.automation.automation_id} ${editingId ? "updated" : "created"}.`);
      resetAutomationForm(event.target);
      await refreshAutomations();
    });

    function autoGrow() {
      const area = $("runMessage");
      area.style.height = "auto";
      area.style.height = `${Math.min(area.scrollHeight, 160)}px`;
    }

    $("runMessage").addEventListener("input", autoGrow);
    $("runMessage").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        $("runForm").requestSubmit();
      }
    });
    $("refreshAll").addEventListener("click", refreshAll);
    $("runSearch").addEventListener("input", renderRuns);
    $("runStateFilter").addEventListener("change", renderRuns);
    $("eventFilter").addEventListener("change", renderEvents);
    $("tabChat").addEventListener("click", () => setView("chat"));
    $("tabTrajectory").addEventListener("click", () => setView("trajectory"));
    $("focusRunInput").addEventListener("click", newSession);
    $("openSettings").addEventListener("click", () => openSettingsPane("general"));
    $("closeSettings").addEventListener("click", closeSettingsPanel);
    $("settingsMask").addEventListener("click", closeSettingsPanel);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeSettingsPanel();
    });
    document.querySelectorAll(".settings-nav button").forEach((button) => {
      button.addEventListener("click", () => openSettingsPane(button.dataset.pane));
    });
    $("cancelAutomationEdit").addEventListener("click", () => resetAutomationForm($("automationForm")));
    $("cancelRun").addEventListener("click", async () => {
      if (!state.selectedRunId) return;
      await request(`/runs/${state.selectedRunId}/cancel`, { method: "POST" });
      setNotice("Cancel requested.");
      await refreshRunDetail();
      await refreshRuns();
    });

    async function refreshAll() {
      try {
        await Promise.all([refreshHealth(), refreshUsage(), refreshAutomations()]);
        await refreshRuns();
        // While streaming, the incremental poll owns the conversation pane; a
        // full detail refresh here would repaint mid-token.
        if (state.selectedRunId && !state.streaming) await refreshRunDetail();
        $("lastRefresh").textContent = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date());
      } catch (error) {
        $("healthDot").className = "status-dot offline";
        setNotice(error.message || String(error), true);
      }
    }

    initTheme();
    initRail();
    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""
