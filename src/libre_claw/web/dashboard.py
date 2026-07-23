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
    * { box-sizing: border-box; }
    html {
      background: var(--bg);
      overflow-x: clip;
      text-rendering: optimizeLegibility;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      overflow-x: clip;
      font-family: var(--font-ui);
    }
    body::before {
      position: fixed;
      z-index: -1;
      inset: 0;
      content: "";
      background-image:
        linear-gradient(to bottom, color-mix(in srgb, var(--accent) 12%, transparent), transparent 30%),
        radial-gradient(circle at center, var(--grid-dot) 1px, transparent 1px);
      background-size: auto, 26px 26px;
      mask-image: linear-gradient(to bottom, #000 0%, rgba(0, 0, 0, 0.72) 45%, transparent 86%);
      pointer-events: none;
    }
    button, input, textarea, select { font: inherit; }
    button {
      min-height: 38px;
      border: 1px solid var(--line);
      color: var(--text);
      background: rgba(255, 255, 255, 0.04);
      border-radius: 6px;
      padding: 9px 12px;
      cursor: pointer;
      transition: border-color .16s ease, background .16s ease, transform .16s ease;
    }
    button:hover { border-color: color-mix(in srgb, var(--accent) 60%, var(--line)); background: var(--panel-hover); }
    button:active { transform: translateY(1px); }
    button:disabled { cursor: not-allowed; opacity: .48; transform: none; }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; font-weight: 800; }
    button.danger { color: #ffd7da; background: var(--danger-soft); border-color: color-mix(in srgb, var(--danger) 54%, var(--line)); }
    button.ghost { background: transparent; }
    a {
      color: var(--accent-strong);
      text-decoration: none;
    }
    a:hover {
      color: var(--text);
      text-decoration: underline;
      text-decoration-color: var(--accent);
      text-underline-offset: 3px;
    }
    input, textarea, select {
      width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      background: color-mix(in srgb, var(--surface) 88%, white 2%);
      color: var(--text);
      border-radius: 6px;
      padding: 10px 11px;
      outline: none;
    }
    input::placeholder, textarea::placeholder { color: color-mix(in srgb, var(--muted) 70%, transparent); }
    input:focus, textarea:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
    button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    textarea { min-height: 118px; resize: vertical; }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .app {
      display: grid;
      grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
      gap: 18px;
      width: min(1500px, calc(100% - 32px));
      margin: 0 auto;
      padding: 18px 0;
      min-height: 100vh;
    }
    aside {
      align-self: start;
      position: sticky;
      top: 86px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      min-width: 0;
    }
    main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 16px;
      min-width: 0;
    }
    .topbar {
      grid-column: 1 / -1;
      position: sticky;
      top: 10px;
      z-index: 20;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      border: 1px solid var(--line);
      background: color-mix(in srgb, var(--surface) 84%, transparent);
      backdrop-filter: blur(20px);
      border-radius: var(--radius);
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 850;
      letter-spacing: 0;
      min-width: 0;
    }
    .brand-title {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .brand-title span:first-child {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .brand small {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .logo-wrap {
      flex: 0 0 auto;
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      overflow: visible;
      background: transparent;
      border-radius: 0;
      box-shadow: none;
      font-size: 34px;
      line-height: 1;
    }
    .status-dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--muted);
      box-shadow: 0 0 0 5px rgba(255, 255, 255, 0.04);
      flex: 0 0 auto;
    }
    .status-dot.online { background: var(--ok); box-shadow: 0 0 0 5px var(--ok-soft); }
    .status-dot.offline { background: var(--danger); box-shadow: 0 0 0 5px var(--danger-soft); }
    .top-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .status {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .metric, section {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .metric {
      position: relative;
      display: grid;
      gap: 4px;
      padding: 13px;
      min-width: 0;
      overflow: hidden;
    }
    .metric::before {
      position: absolute;
      inset: 0 auto 0 0;
      width: 3px;
      content: "";
      background: var(--accent);
      opacity: .85;
    }
    .metric span, .tiny {
      color: var(--muted);
      font-size: 12px;
    }
    .metric strong {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 18px;
      letter-spacing: 0;
      white-space: nowrap;
    }
    .metric small {
      color: color-mix(in srgb, var(--muted) 76%, transparent);
      font-size: 11px;
    }
    section {
      min-width: 0;
      overflow: hidden;
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.025);
    }
    .section-head h2, .section-head h3 {
      margin: 0;
      font-size: 14px;
      letter-spacing: 0;
    }
    .eyebrow {
      color: var(--accent-strong);
      font-size: 11px;
      font-weight: 850;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .head-copy {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .body { padding: 14px; }
    .stack { display: grid; gap: 10px; }
    .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .row.end { justify-content: flex-end; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .filter-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 126px;
      gap: 8px;
    }
    .runs {
      display: grid;
      gap: 8px;
      max-height: 52vh;
      overflow: auto;
      overflow-x: hidden;
      padding-right: 4px;
    }
    .run-item {
      width: 100%;
      min-width: 0;
      text-align: left;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.025);
      border-radius: 7px;
      padding: 10px;
      overflow: hidden;
    }
    .run-item:hover { background: var(--panel-hover); }
    .run-item.active { border-color: var(--accent); background: var(--accent-soft); }
    .run-title { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .run-meta {
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      max-width: 100%;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .run-foot {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 8px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.03);
      white-space: nowrap;
    }
    .pill.done, .pill.active { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 52%, var(--line)); background: var(--ok-soft); }
    .pill.running, .pill.queued { color: var(--accent-strong); border-color: color-mix(in srgb, var(--accent) 58%, var(--line)); background: var(--accent-soft); }
    .pill.blocked { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 58%, var(--line)); background: var(--warn-soft); }
    .pill.failed, .pill.cancelled, .pill.paused { color: #ffd7da; border-color: color-mix(in srgb, var(--danger) 58%, var(--line)); background: var(--danger-soft); }
    .overview {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .insight {
      min-width: 0;
      border: 1px solid var(--line);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.018)),
        var(--panel);
      border-radius: var(--radius);
      padding: 13px;
      box-shadow: var(--shadow);
    }
    .insight span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }
    .insight strong {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 16px;
    }
    .run-focus {
      border-color: color-mix(in srgb, var(--accent) 44%, var(--line));
      background:
        linear-gradient(120deg, color-mix(in srgb, var(--accent) 18%, transparent), color-mix(in srgb, var(--tool-accent) 10%, transparent) 42%, transparent 70%),
        var(--panel);
    }
    .run-focus .section-head {
      background: transparent;
    }
    .run-focus .body {
      border-top: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.14);
    }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(330px, .85fr);
      gap: 16px;
      min-height: 0;
    }
    .timeline {
      max-height: min(68vh, 860px);
      overflow: auto;
      display: grid;
      gap: 9px;
      padding-right: 4px;
    }
    .event {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px;
      background: rgba(255, 255, 255, 0.025);
      display: grid;
      gap: 8px;
    }
    .event:hover { border-color: var(--line-strong); }
    .event.is-error, .event.event-error { border-color: color-mix(in srgb, var(--danger) 52%, var(--line)); background: var(--danger-soft); }
    .event.event-tool-call, .event.event-tool-result { border-color: color-mix(in srgb, var(--tool-accent) 36%, var(--line)); }
    .event.event-permission-request { border-color: color-mix(in srgb, var(--warn) 52%, var(--line)); background: var(--warn-soft); }
    .event-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
    }
    .event-type {
      color: var(--tool-accent);
      font-size: 12px;
      font-weight: 850;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .event-time {
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
    }
    pre {
      margin: 0;
      max-width: 100%;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--soft);
      background: rgba(0, 0, 0, 0.28);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 6px;
      padding: 10px;
      font-family: var(--font-mono);
      font-size: 12px;
      line-height: 1.5;
    }
    .event pre:empty { display: none; }
    .empty {
      color: var(--muted);
      padding: 18px;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 7px;
      background: rgba(255, 255, 255, 0.02);
    }
    .automation-list { display: grid; gap: 10px; }
    .automation {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.025);
      border-radius: 7px;
      padding: 11px;
      display: grid;
      gap: 9px;
    }
    .automation-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
    }
    .automation strong {
      font-size: 13px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .automation-meta {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .notice {
      border: 1px solid color-mix(in srgb, var(--accent) 46%, var(--line));
      background: var(--accent-soft);
      color: var(--accent-strong);
      padding: 10px;
      border-radius: 7px;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .notice::before {
      content: "Status";
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .notice.error {
      border-color: color-mix(in srgb, var(--danger) 54%, var(--line));
      background: var(--danger-soft);
      color: color-mix(in srgb, var(--danger) 24%, var(--text));
    }
    .approval {
      border: 1px solid color-mix(in srgb, var(--warn) 56%, var(--line));
      background: var(--warn-soft);
      border-radius: 7px;
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .dashboard-footer {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border: 1px solid var(--line);
      background: color-mix(in srgb, var(--surface) 62%, transparent);
      border-radius: var(--radius);
      padding: 12px 14px;
      color: var(--muted);
      font-size: 12px;
      box-shadow: var(--shadow);
    }
    .dashboard-footer nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .dashboard-footer span { color: var(--soft); }
    .theme-picker {
      width: min(220px, 40vw);
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .theme-picker span {
      font-weight: 800;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    .theme-picker select {
      min-height: 38px;
      padding: 8px 32px 8px 10px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel-strong) 92%, white 5%), var(--surface));
      font-weight: 750;
    }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--accent); border-radius: 999px; }
    @media (prefers-color-scheme: light) {
      html:not([data-theme]) {
        --bg: #f7f8fb;
        --surface: #ffffff;
        --surface-2: #f5f7fb;
        --panel: rgba(255, 255, 255, 0.88);
        --panel-strong: #ffffff;
        --panel-hover: rgba(255, 92, 92, 0.06);
        --line: rgba(15, 23, 42, 0.12);
        --line-strong: rgba(15, 23, 42, 0.2);
        --text: #09090b;
        --soft: #27272a;
        --muted: #71717a;
        --accent-strong: #b91c1c;
        --grid-dot: rgba(0, 0, 0, 0.1);
        --shadow: 0 22px 70px rgba(15, 23, 42, 0.1);
      }
      pre { background: rgba(15, 23, 42, 0.045); border-color: rgba(15, 23, 42, 0.08); }
    }
    @media (max-width: 1120px) {
      .app { grid-template-columns: 1fr; }
      aside { position: static; }
      .workspace { grid-template-columns: 1fr; }
      .status, .overview { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }
    @media (max-width: 720px) {
      .app {
        width: min(100%, calc(100% - 20px));
        padding: 10px 0;
        gap: 10px;
      }
      .topbar {
        align-items: flex-start;
        flex-direction: column;
        top: 8px;
      }
      .top-actions,
      .theme-picker,
      .top-actions button,
      .top-actions a,
      .status,
      .overview,
      .grid-2,
      .filter-row {
        width: 100%;
        grid-template-columns: 1fr;
      }
      .section-head {
        align-items: flex-start;
        flex-direction: column;
      }
      .row button { flex: 1 1 140px; }
      .runs { max-height: 38vh; }
      .timeline { max-height: 76vh; }
      .run-title { white-space: normal; }
      textarea { min-height: 96px; }
      .event-head, .automation-head {
        align-items: flex-start;
        flex-direction: column;
      }
      .dashboard-footer {
        align-items: flex-start;
        flex-direction: column;
      }
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
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="logo-wrap" role="img" aria-label="Libre Claw lobster">🦞</span>
        <div>
          <div class="brand-title"><span>Libre Claw Dashboard</span><span id="healthDot" class="status-dot" aria-label="Daemon status"></span></div>
          <small>Local control plane for runs, approvals, schedules, and usage.</small>
        </div>
      </div>
      <div class="top-actions">
        <span class="tiny" id="lastRefresh">Not refreshed yet</span>
        <label class="theme-picker" for="themeSelect">
          <span>Theme</span>
          <select id="themeSelect">
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
        </label>
        <button class="ghost" id="refreshAll" type="button">Refresh</button>
        <button class="primary" id="focusRunInput" type="button">New Run</button>
      </div>
    </header>
    <aside>
      <div class="status">
        <div class="metric"><span>Daemon</span><strong id="daemonStatus">...</strong><small>localhost API</small></div>
        <div class="metric"><span>Active runs</span><strong id="activeRuns">0</strong><small>queued or running</small></div>
        <div class="metric"><span>Tokens</span><strong id="usageTokens">0</strong><small id="usageExact">0 total</small></div>
      </div>
      <section>
        <div class="section-head">
          <div class="head-copy">
            <h2>Start Run</h2>
            <span class="tiny">Kick off an agent task from the browser.</span>
          </div>
        </div>
        <form id="runForm" class="body stack">
          <label>Message<textarea id="runMessage" required placeholder="Ask Libre Claw to do something"></textarea></label>
          <div class="grid-2">
            <label>Provider<select id="runProvider">
              <option value="">default</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI API</option>
              <option value="openrouter">OpenRouter</option>
              <option value="moonshot">Moonshot AI / Kimi</option>
              <option value="ollama">Ollama Cloud/Local</option>
              <option value="codex">OpenAI Codex</option>
            </select></label>
            <label>Model<input id="runModel" placeholder="default"></label>
          </div>
          <button class="primary" type="submit">Start</button>
        </form>
      </section>
      <section>
        <div class="section-head">
          <div class="head-copy">
            <h2>Runs</h2>
            <span class="tiny" id="runCount">0 runs</span>
          </div>
          <button class="ghost" id="refreshRuns" type="button">Refresh</button>
        </div>
        <div class="body stack">
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
        </div>
      </section>
    </aside>
    <main>
      <section class="run-focus">
        <div class="section-head">
          <div class="head-copy">
            <span class="eyebrow">Run cockpit</span>
            <h2 id="selectedTitle">No run selected</h2>
            <span class="tiny" id="selectedMeta">Select a run to inspect its timeline and approvals.</span>
          </div>
          <div class="row">
            <span class="pill" id="selectedState">idle</span>
            <button id="cancelRun" class="danger" type="button" disabled>Cancel</button>
          </div>
        </div>
        <div class="body stack">
          <div id="notice" class="notice">Connected to the local daemon. Start a run or inspect a schedule.</div>
          <div id="permissions" class="stack"></div>
        </div>
      </section>
      <div class="overview" aria-label="Dashboard overview">
        <div class="insight">
          <span>Selected run</span>
          <strong id="selectedInsight">none</strong>
        </div>
        <div class="insight">
          <span>Pending approvals</span>
          <strong id="pendingApprovalCount">0</strong>
        </div>
        <div class="insight">
          <span>Schedules</span>
          <strong id="automationCount">0</strong>
        </div>
        <div class="insight">
          <span>Latest event</span>
          <strong id="lastEventLabel">none</strong>
        </div>
      </div>
      <div class="workspace">
        <section>
          <div class="section-head">
            <div class="head-copy">
              <h2>Timeline</h2>
              <span class="tiny" id="eventCount">0 events</span>
            </div>
            <select id="eventFilter" aria-label="Filter timeline events">
              <option value="">All events</option>
              <option value="message">Messages</option>
              <option value="tool">Tools</option>
              <option value="permission">Approvals</option>
              <option value="error">Errors</option>
              <option value="run">Run state</option>
            </select>
          </div>
          <div class="body timeline" id="timeline"></div>
        </section>
        <section>
          <div class="section-head">
            <div class="head-copy">
              <h2 id="automationFormTitle">Create Schedule</h2>
              <span class="tiny">Recurring checks can write reports or notify Telegram.</span>
            </div>
          </div>
          <div class="body stack">
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
                  <option value="moonshot">Moonshot AI / Kimi</option>
                  <option value="ollama">Ollama Cloud/Local</option>
                  <option value="codex">OpenAI Codex</option>
                </select></label>
              </div>
              <label>Model<input id="automationModel" placeholder="default"></label>
              <div class="row">
                <button id="automationSubmit" type="submit">Create Schedule</button>
                <button id="cancelAutomationEdit" class="ghost" type="button" hidden>Cancel Edit</button>
              </div>
            </form>
            <div id="automations" class="automation-list"></div>
          </div>
        </section>
      </div>
    </main>
    <footer class="dashboard-footer">
      <span>Libre Claw dashboard</span>
      <nav aria-label="Dashboard footer links">
        <a href="https://libreclaw.sh" target="_blank" rel="noreferrer">libreclaw.sh</a>
        <a href="https://github.com/kroonen-ai/libre-claw" target="_blank" rel="noreferrer">GitHub</a>
        <a href="https://git.kroonen.ai/kroonen-ai/libre-claw" target="_blank" rel="noreferrer">GitLab mirror</a>
        <a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">Apache-2.0</a>
        <a href="https://kroonen.ai" target="_blank" rel="noreferrer">Kroonen AI</a>
      </nav>
    </footer>
  </div>
  <script>
    const state = { selectedRunId: "", runs: [], events: [], editingAutomationId: "" };
    const $ = (id) => document.getElementById(id);
    const THEME_KEY = "libre-claw-dashboard-theme";
    const THEMES = new Set([
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

    function setNotice(text, error = false) {
      const box = $("notice");
      box.textContent = text;
      box.className = error ? "notice error" : "notice";
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
      $("daemonStatus").textContent = health.ok ? "online" : "offline";
      $("activeRuns").textContent = health.active_runs ?? 0;
      $("healthDot").className = `status-dot ${health.ok ? "online" : "offline"}`;
    }

    async function refreshUsage() {
      const usage = await request("/usage?limit=250");
      const totalTokens = usage.summary?.total_tokens ?? 0;
      const tokenNode = $("usageTokens");
      tokenNode.textContent = formatCompactNumber(totalTokens);
      tokenNode.title = `${formatExactNumber(totalTokens)} tokens`;
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
      if (!state.selectedRunId && state.runs[0]) await selectRun(state.runs[0].run_id);
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
        button.className = `run-item ${run.run_id === state.selectedRunId ? "active" : ""}`;
        const title = document.createElement("strong");
        title.className = "run-title";
        title.textContent = run.title || "Untitled run";
        const meta = document.createElement("div");
        meta.className = "run-meta";
        meta.textContent = `${run.run_id} | ${run.provider}:${run.model}`;
        const foot = document.createElement("div");
        foot.className = "run-foot";
        const updated = document.createElement("span");
        updated.className = "tiny";
        updated.textContent = formatShortTime(run.updated_at);
        foot.append(pill(run.state), updated);
        button.append(title, meta, foot);
        button.addEventListener("click", () => selectRun(run.run_id));
        container.append(button);
      }
    }

    async function selectRun(runId) {
      state.selectedRunId = runId;
      renderRuns();
      await refreshRunDetail();
    }

    function clearSelectedRun() {
      $("selectedTitle").textContent = "No run selected";
      $("selectedMeta").textContent = "Select a run to inspect its timeline and approvals.";
      $("selectedInsight").textContent = "none";
      $("selectedState").textContent = "idle";
      $("selectedState").className = "pill";
      $("cancelRun").disabled = true;
      state.events = [];
      renderEvents();
      renderPermissions([]);
    }

    async function refreshRunDetail() {
      if (!state.selectedRunId) return;
      const detail = await request(`/runs/${state.selectedRunId}`);
      const run = detail.run;
      $("selectedTitle").textContent = run.title || "Untitled run";
      $("selectedMeta").textContent = `${run.run_id} | ${run.provider}:${run.model} | updated ${formatTime(run.updated_at)}`;
      $("selectedInsight").textContent = run.run_id;
      $("selectedInsight").title = run.run_id;
      $("selectedState").textContent = run.state;
      $("selectedState").className = `pill ${run.state}`;
      $("cancelRun").disabled = !["queued", "running", "blocked"].includes(run.state);
      const events = await request(`/runs/${state.selectedRunId}/events?after=0`);
      state.events = events.events || [];
      renderEvents();
      renderPermissions(detail.pending_permissions || []);
    }

    function renderEvents() {
      const container = $("timeline");
      container.replaceChildren();
      if (!state.events.length) {
        $("eventCount").textContent = "0 events";
        $("lastEventLabel").textContent = "none";
        container.append(empty("No events yet."));
        return;
      }
      const displayEvents = coalescedEvents(state.events);
      const filter = $("eventFilter").value;
      const visible = displayEvents.slice().reverse().filter((event) => eventMatchesFilter(event, filter));
      $("eventCount").textContent = filter
        ? `${visible.length} of ${displayEvents.length} cards`
        : `${displayEvents.length} ${displayEvents.length === 1 ? "card" : "cards"} from ${state.events.length} events`;
      if (!visible.length) {
        $("lastEventLabel").textContent = eventTitle(displayEvents.at(-1));
        container.append(empty("No matching events."));
        return;
      }
      $("lastEventLabel").textContent = eventTitle(displayEvents.at(-1));
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
      $("pendingApprovalCount").textContent = String(pendingIds.length);
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
      $("automationCount").textContent = String(automations.length);
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
        runNow.textContent = "Run now";
        runNow.className = "primary";
        runNow.addEventListener("click", () => runAutomationNow(automation.automation_id, runNow));
        const edit = document.createElement("button");
        edit.textContent = "Edit";
        edit.addEventListener("click", () => editAutomation(automation));
        const toggle = document.createElement("button");
        toggle.textContent = automation.status === "active" ? "Pause" : "Resume";
        toggle.addEventListener("click", () => toggleAutomation(automation));
        const del = document.createElement("button");
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

    $("runForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = {
        message: $("runMessage").value,
        surface: "dashboard",
      };
      if ($("runProvider").value.trim()) body.provider = $("runProvider").value.trim();
      if ($("runModel").value.trim()) body.model = $("runModel").value.trim();
      const payload = await request("/runs", { method: "POST", body: JSON.stringify(body) });
      $("runMessage").value = "";
      setNotice(`Run ${payload.run.run_id} started.`);
      await selectRun(payload.run.run_id);
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

    $("refreshRuns").addEventListener("click", refreshRuns);
    $("refreshAll").addEventListener("click", refreshAll);
    $("runSearch").addEventListener("input", renderRuns);
    $("runStateFilter").addEventListener("change", renderRuns);
    $("eventFilter").addEventListener("change", renderEvents);
    $("focusRunInput").addEventListener("click", () => $("runMessage").focus());
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
        if (state.selectedRunId) await refreshRunDetail();
        $("lastRefresh").textContent = `Updated ${new Date().toLocaleTimeString()}`;
      } catch (error) {
        $("healthDot").className = "status-dot offline";
        setNotice(error.message || String(error), true);
      }
    }

    initTheme();
    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""
