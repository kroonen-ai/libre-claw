# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations


def dashboard_html() -> str:
    """Return the self-contained local daemon dashboard."""
    return _DASHBOARD_HTML


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Libre Claw Dashboard</title>
  <link rel="icon" href="/assets/favicon.ico?v=20260527" sizes="any">
  <link rel="shortcut icon" href="/assets/favicon.ico?v=20260527">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png?v=20260527">
  <link rel="icon" type="image/png" sizes="256x256" href="/assets/favicon.png?v=20260527">
  <style>
    :root {
      color-scheme: dark light;
      --bg: #000000;
      --surface: #050505;
      --surface-2: #0a0a0a;
      --panel: rgba(10, 10, 10, 0.86);
      --panel-strong: rgba(16, 16, 16, 0.94);
      --panel-hover: rgba(255, 255, 255, 0.05);
      --line: rgba(255, 255, 255, 0.11);
      --line-strong: rgba(255, 255, 255, 0.18);
      --text: #ffffff;
      --soft: #d4d4d8;
      --muted: #a1a1aa;
      --accent: #0070f3;
      --accent-soft: rgba(0, 112, 243, 0.15);
      --accent-strong: #dbeafe;
      --purple: #8b5cf6;
      --purple-soft: rgba(139, 92, 246, 0.14);
      --danger: #ff4d4f;
      --danger-soft: rgba(255, 77, 79, 0.14);
      --ok: #42d392;
      --ok-soft: rgba(66, 211, 146, 0.13);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.13);
      --grid-dot: rgba(255, 255, 255, 0.12);
      --shadow: 0 26px 80px rgba(0, 0, 0, 0.5);
      --radius: 8px;
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
    }
    body::before {
      position: fixed;
      z-index: -1;
      inset: 0;
      content: "";
      background-image:
        linear-gradient(to bottom, rgba(0, 112, 243, 0.1), transparent 30%),
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
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 0 0 1px var(--line), 0 0 24px var(--accent-soft);
      background: var(--surface-2);
    }
    .logo {
      width: 100%;
      height: 100%;
      display: block;
      object-fit: cover;
    }
    .logo-light { display: none; }
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
        linear-gradient(120deg, rgba(0, 112, 243, 0.16), rgba(139, 92, 246, 0.08) 42%, transparent 70%),
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
    .event.event-tool-call, .event.event-tool-result { border-color: color-mix(in srgb, var(--purple) 36%, var(--line)); }
    .event.event-permission-request { border-color: color-mix(in srgb, var(--warn) 52%, var(--line)); background: var(--warn-soft); }
    .event-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
    }
    .event-type {
      color: var(--purple);
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
      font-family: "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
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
      border-color: #7a1d24;
      background: var(--danger-soft);
      color: #ffc3c7;
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
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--accent); border-radius: 999px; }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f7f8fb;
        --surface: #ffffff;
        --surface-2: #f5f7fb;
        --panel: rgba(255, 255, 255, 0.88);
        --panel-strong: #ffffff;
        --panel-hover: rgba(0, 112, 243, 0.06);
        --line: rgba(15, 23, 42, 0.12);
        --line-strong: rgba(15, 23, 42, 0.2);
        --text: #09090b;
        --soft: #27272a;
        --muted: #71717a;
        --accent-strong: #005cc5;
        --grid-dot: rgba(0, 0, 0, 0.1);
        --shadow: 0 22px 70px rgba(15, 23, 42, 0.1);
      }
      .logo-dark { display: none; }
      .logo-light { display: block; }
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
        <span class="logo-wrap" aria-hidden="true">
          <img class="logo logo-dark" src="/assets/logo-dark.jpg?v=20260527" alt="" width="36" height="36">
          <img class="logo logo-light" src="/assets/logo-light.jpg?v=20260527" alt="" width="36" height="36">
        </span>
        <div>
          <div class="brand-title"><span>Libre Claw Dashboard</span><span id="healthDot" class="status-dot" aria-label="Daemon status"></span></div>
          <small>Local control plane for runs, approvals, schedules, and usage.</small>
        </div>
      </div>
      <div class="top-actions">
        <span class="tiny" id="lastRefresh">Not refreshed yet</span>
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
            <label>Provider<input id="runProvider" placeholder="default"></label>
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
                <label>Provider<input id="automationProvider" placeholder="default"></label>
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
        <a href="https://libreclaw.dev" target="_blank" rel="noreferrer">libreclaw.dev</a>
        <a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">Apache-2.0</a>
        <a href="https://kroonen.ai" target="_blank" rel="noreferrer">Kroonen AI</a>
      </nav>
    </footer>
  </div>
  <script>
    const state = { selectedRunId: "", runs: [], events: [], editingAutomationId: "" };
    const $ = (id) => document.getElementById(id);

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
      const filter = $("eventFilter").value;
      const visible = state.events.slice().reverse().filter((event) => eventMatchesFilter(event, filter));
      $("eventCount").textContent = filter
        ? `${visible.length} of ${state.events.length} events`
        : `${state.events.length} ${state.events.length === 1 ? "event" : "events"}`;
      if (!visible.length) {
        $("lastEventLabel").textContent = eventTitle(state.events.at(-1));
        container.append(empty("No matching events."));
        return;
      }
      $("lastEventLabel").textContent = eventTitle(state.events.at(-1));
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
      if (event.type === "assistant_delta") return data.text || "";
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
        meta.textContent = `${automation.schedule} | ${automation.route} | ${model} | next ${formatTime(automation.next_run_at)}`;
        const prompt = document.createElement("div");
        prompt.className = "tiny";
        prompt.textContent = truncate(automation.prompt || "", 180);
        const row = document.createElement("div");
        row.className = "row end";
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
        row.append(edit, toggle, del);
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

    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""
