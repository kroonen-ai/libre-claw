# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from libre_claw.core.themes import THEME_PALETTES

DASHBOARD_CSS = r"""
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    html { background: var(--bg); text-rendering: optimizeLegibility; -webkit-font-smoothing: antialiased; }
    body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.55 var(--font-ui); }
    button, input, textarea, select { font: inherit; color: inherit; }
    button { cursor: pointer; border: 1px solid var(--line); border-radius: var(--radius); padding: 7px 12px; background: var(--surface); transition: background .16s ease, border-color .16s ease, color .16s ease; }
    button:hover { background: var(--panel-hover); border-color: var(--line-strong); }
    button:disabled { opacity: .42; cursor: not-allowed; }
    input, textarea, select { min-width: 0; min-height: 38px; max-width: 100%; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 8px 11px; outline: none; }
    input::placeholder, textarea::placeholder { color: var(--muted); opacity: 1; }
    input:hover, textarea:hover, select:hover { border-color: var(--line-strong); }
    input:focus, textarea:focus, select:focus { border-color: var(--accent); }
    input[type=checkbox] { min-height: 0; width: 16px; height: 16px; accent-color: var(--accent); }
    textarea { resize: vertical; line-height: 1.55; }
    a { color: var(--accent-strong); text-decoration: none; }
    a:hover { text-decoration: underline; text-underline-offset: 3px; }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
    ::selection { background: var(--accent-soft); color: var(--text); }
    ::-webkit-scrollbar { width: 7px; height: 7px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--line-strong); border: 2px solid transparent; background-clip: padding-box; border-radius: var(--radius-small); }
    .app { display: grid; grid-template-columns: 260px minmax(0, 1fr); height: 100vh; height: 100dvh; overflow: hidden; }
    .sidebar { min-height: 0; display: flex; flex-direction: column; background: var(--sidebar-fill, var(--bg)); padding: 16px 12px 10px; border-inline-end: 1px solid var(--line); }
    .logo-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0 4px; min-height: 40px; margin-bottom: 18px; flex: none; }
    .brand { display: inline-flex; gap: 9px; align-items: center; min-width: 0; border: 0; padding: 0; background: none; font-weight: 650; font-size: 15px; color: var(--text); }
    .brand:hover { background: none; }
    .logo-wrap img { display: block; width: 26px; height: 26px; }
    .logo-wrap { display: grid; place-items: center; width: 30px; height: 30px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); font-size: 20px; }
    .harness-tag { display: none; }
    .icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 32px; min-width: 32px; height: 32px; padding: 0; border-color: transparent; border-radius: var(--radius-small); color: var(--muted); }
    .icon-btn svg { width: 16px; height: 16px; }
    .new-session { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; margin: 0 2px 20px; border-color: color-mix(in srgb, var(--accent) 28%, var(--line)); background: var(--accent-soft); color: var(--text); font-weight: 600; flex: none; }
    .new-session svg { color: var(--accent-strong); }
    .new-session:hover { background: color-mix(in srgb, var(--accent) 18%, var(--surface)); }
    .section-label { display: flex; align-items: center; justify-content: space-between; padding: 0 8px 10px; color: var(--muted); font-size: 11px; font-weight: 600; }
    .tiny { font-size: 11px; color: var(--muted); font-weight: 400; }
    .filter-row { display: grid; gap: 6px; padding: 0 2px 12px; }
    .filter-row input, .filter-row select { min-height: 33px; width: 100%; font-size: 12px; background: transparent; padding: 6px 9px; }
    .filter-row select { border-color: transparent; color: var(--muted); }
    .runs { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 3px; padding: 0 2px; }
    .run-item { display: flex; flex: none; align-items: center; gap: 9px; min-height: 48px; width: 100%; padding: 9px 10px; border: 1px solid transparent; border-radius: var(--radius); text-align: start; background: transparent; color: var(--soft); }
    .run-item:hover { background: var(--panel-hover); }
    .run-item.active { background: var(--surface-2); border-color: var(--line); color: var(--text); }
    .run-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; }
    .run-time { flex: none; color: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }
    .state-dot, .status-dot { display: inline-block; flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--muted); }
    .state-dot.running, .state-dot.queued { background: var(--accent); }
    .state-dot.blocked { background: var(--warn); }
    .state-dot.done, .status-dot.online { background: var(--ok); }
    .state-dot.failed, .state-dot.cancelled, .status-dot.offline { background: var(--danger); }
    .side-foot { flex: none; border-top: 1px solid var(--line); padding-top: 9px; margin-top: 12px; }
    .side-foot-row { display: flex; align-items: center; gap: 9px; width: 100%; min-height: 36px; padding: 7px 9px; border: 0; background: none; text-align: start; font-size: 12px; color: var(--soft); }
    .side-foot-row svg { width: 16px; height: 16px; flex: none; color: var(--muted); }
    .grow { flex: 1; min-width: 0; }
    .side-foot-row .tiny { font-size: 10px; }
    .app.rail { grid-template-columns: 64px minmax(0, 1fr); }
    #mobileTasks, .mobile-tasks-close, .sidebar-backdrop { display: none; }
    .app.rail .sidebar { padding: 14px 8px; align-items: center; }
    .app.rail .logo-row { padding: 0; margin-bottom: 16px; }
    .app.rail .brand > span:not(.logo-wrap), .app.rail .logo-row .icon-btn, .app.rail .new-session span, .app.rail .section-label, .app.rail .filter-row, .app.rail .run-title, .app.rail .run-time, .app.rail .side-foot-row .grow, .app.rail .side-foot-row .tiny { display: none; }
    .app.rail .new-session { width: 38px; min-height: 38px; padding: 0; margin-bottom: 14px; }
    .app.rail .runs { width: 100%; align-items: center; }
    .app.rail .run-item { width: 38px; min-height: 38px; justify-content: center; padding: 0; }
    .app.rail .side-foot-row { width: 38px; justify-content: center; padding: 0; }
    .main { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--canvas, var(--surface)); overflow: hidden; }
    .main-head { display: flex; align-items: center; gap: 10px; min-height: 72px; padding: 16px 28px; border-bottom: 1px solid var(--line); flex: none; }
    .main-head h1 { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 0; font-size: 16px; font-weight: 600; }
    .pill { display: inline-flex; align-items: center; gap: 6px; min-height: 24px; padding: 3px 9px; border: 1px solid var(--line); border-radius: var(--radius-small); background: var(--panel-hover); color: var(--muted); font-size: 10px; font-weight: 600; white-space: nowrap; }
    .pill.running, .pill.queued { background: var(--accent-soft); color: var(--accent-strong); border-color: transparent; }
    .pill.blocked { background: var(--warn-soft); color: var(--warn); border-color: transparent; }
    .pill.done, .pill.active { background: var(--ok-soft); color: var(--ok); border-color: transparent; }
    .pill.failed, .pill.cancelled, .pill.paused { background: var(--danger-soft); color: var(--danger); border-color: transparent; }
    .pill-btn { min-height: 32px; padding: 5px 10px; font-size: 11px; }
    .danger { color: var(--danger); }
    button.danger:hover { background: var(--danger-soft); }
    .view-tabs { display: flex; align-items: center; flex: none; gap: 4px; padding: 10px 24px; border-bottom: 1px solid var(--line); overflow-x: auto; }
    .view-tab { position: relative; flex: none; min-height: 34px; border: 0; border-radius: var(--radius-small); padding: 6px 12px; color: var(--muted); background: none; font-size: 12px; white-space: nowrap; }
    .view-tab:hover { color: var(--text); }
    .view-tab.active { color: var(--text); background: var(--surface-2); box-shadow: inset 0 0 0 1px var(--line); }
    .spacer { flex: 1; }
    .view-tabs select { min-height: 30px; padding: 4px 8px; font-size: 11px; }
    .conversation { flex: 1; min-height: 0; overflow-y: auto; padding: 30px max(28px, calc((100% - 880px) / 2)) 24px; display: flex; flex-direction: column; gap: 20px; overscroll-behavior: contain; }
    .msg-user { display: flex; justify-content: flex-end; }
    .msg-user > div { max-width: min(85%, 680px); padding: 13px 17px; border-radius: var(--radius-large) var(--radius-large) var(--radius-small) var(--radius-large); border: 1px solid var(--line); background: var(--surface-2); color: var(--text); white-space: pre-wrap; overflow-wrap: anywhere; }
    .msg-assistant { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.75; }
    .msg-tool, .event { border: 1px solid var(--line); border-radius: var(--radius-panel); background: color-mix(in srgb, var(--surface-2) 45%, var(--surface)); padding: 12px 15px; display: grid; gap: 8px; }
    .msg-tool summary, .msg-tool .tool-head { display: flex; align-items: center; gap: 9px; color: var(--soft); font-size: 12px; cursor: pointer; list-style: none; }
    .msg-tool summary::-webkit-details-marker { display: none; }
    .msg-tool summary::before { content: '›'; color: var(--muted); font-size: 16px; transition: transform .15s; }
    .msg-tool[open] summary::before { transform: rotate(90deg); }
    .msg-tool .tool-name { font-family: var(--font-mono); color: var(--tool-accent); font-size: 11px; }
    .msg-tool.is-error .tool-name { color: var(--danger); }
    .msg-tool pre, .event pre { margin: 0; max-height: 340px; overflow: auto; font: 11.5px/1.7 var(--font-mono); white-space: pre-wrap; overflow-wrap: anywhere; }
    .msg-tool pre { padding: 10px 12px; background: var(--bg); border-radius: var(--radius-small); }
    .msg-note { color: var(--muted); font-size: 11px; text-align: center; }
    .msg-error, .event.is-error { padding: 12px 16px; border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--line)); background: var(--danger-soft); border-radius: var(--radius); color: var(--text); overflow-wrap: anywhere; white-space: pre-wrap; }
    .event-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
    .event-type { font-size: 11px; font-weight: 600; color: var(--soft); }
    .event-time { font-size: 10px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
    .composer-zone { flex: none; padding: 12px max(24px, calc((100% - 940px) / 2)) 10px; background: linear-gradient(transparent, var(--canvas, var(--surface)) 18%); }
    .composer { padding: 14px; border: 1px solid var(--line-strong); border-radius: var(--radius-large); background: var(--surface); box-shadow: 0 8px 25px #0000000b; transition: border-color .18s ease, box-shadow .18s ease; }
    .composer:focus-within { border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); box-shadow: 0 0 0 3px var(--accent-soft); }
    .composer textarea { display: block; width: 100%; min-height: 48px; max-height: 180px; border: 0; padding: 4px 3px 14px; border-radius: 0; background: none; resize: none; outline: none; font-size: 14px; }
    .composer-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; padding-top: 10px; border-top: 1px solid var(--line); }
    .composer-controls input, .composer-controls select { min-height: 32px; height: 32px; padding: 4px 8px; background: var(--surface-2); border-color: transparent; border-radius: var(--radius-small); font-size: 11px; max-width: 210px; }
    .composer-controls input { width: 204px; font-family: var(--font-mono); }
    .composer-controls input:hover, .composer-controls select:hover { border-color: var(--line-strong); }
    .composer-controls .model-metadata { flex-basis: 100%; order: 10; margin: 0; }
    .send-btn { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 34px; height: 34px; min-height: 34px; padding: 0; border: 0; border-radius: var(--radius); background: var(--text); color: var(--bg); }
    .send-btn:hover { background: var(--text); opacity: .88; }
    .send-btn svg { width: 16px; height: 16px; }
    .status-strip { display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 5px 9px; padding: 9px 0 0; font-size: 10px; color: var(--muted); }
    .status-strip .sep { color: var(--line-strong); }
    .notice { display: none; }
    .notice.visible { display: block; margin: 0 0 10px; padding: 9px 12px; background: var(--accent-soft); border: 1px solid var(--line); border-radius: var(--radius-small); color: var(--soft); font-size: 12px; }
    .notice.error { color: var(--danger); background: var(--danger-soft); }
    .approval { display: grid; gap: 10px; margin-bottom: 12px; padding: 16px; border: 1px solid color-mix(in srgb, var(--warn) 40%, var(--line)); border-radius: var(--radius-panel); background: var(--warn-soft); }
    .approval pre { margin: 0; max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 11px/1.6 var(--font-mono); }
    .row, .workflow-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .row.end { justify-content: flex-end; }
    .approval button { font-size: 11px; }
    button.primary { color: var(--bg); background: var(--text); border-color: var(--text); font-weight: 600; }
    button.primary:hover { opacity: .9; }
    .empty { padding: 28px 14px; color: var(--muted); font-size: 12px; text-align: center; border: 1px dashed var(--line); border-radius: var(--radius); }
    .runs .empty { border: 0; font-size: 11px; }
    .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; max-width: 600px; width: 100%; margin: auto; padding: 36px 18px; text-align: center; color: var(--muted); }
    .empty-icon { display: grid; place-items: center; width: 48px; height: 48px; margin-bottom: 4px; border: 1px solid var(--line); border-radius: var(--radius-large); background: var(--surface-2); color: var(--accent-strong); font-size: 22px; }
    .empty-state h2, .empty-state h3, .empty-state > strong { color: var(--text); margin: 0; font-size: 20px; font-weight: 550; letter-spacing: -.3px; }
    .welcome-state > strong { font-size: 26px; }
    .welcome-state { border: 0; }
    .empty-state p { margin: 0; max-width: 44ch; font-size: 13px; line-height: 1.7; }
    .starter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; width: 100%; margin-top: 18px; }
    .starter-action { text-align: start; padding: 12px 14px; font-size: 12px; color: var(--soft); background: transparent; min-height: 52px; }
    .starter-action:hover { border-color: var(--accent); background: var(--accent-soft); }
    .keyboard-hint { font-size: 10px; color: var(--muted); }
    .msg-md { white-space: normal; }
    .msg-md p { margin: 0 0 12px; }
    .msg-md p:last-child { margin-bottom: 0; }
    .msg-md h1, .msg-md h2, .msg-md h3, .msg-md h4 { margin: 20px 0 9px; font-weight: 650; line-height: 1.4; }
    .msg-md h1 { font-size: 22px; } .msg-md h2 { font-size: 19px; } .msg-md h3 { font-size: 16px; } .msg-md h4 { font-size: 14px; }
    .msg-md ul, .msg-md ol { padding-inline-start: 24px; margin: 0 0 12px; }
    .msg-md li { margin: 4px 0; }
    .msg-md code { font: 12px var(--font-mono); background: var(--surface-2); border: 1px solid var(--line); padding: 2px 5px; border-radius: var(--radius-small); }
    .msg-md blockquote { margin: 12px 0; border-inline-start: 2px solid var(--accent); padding-inline-start: 14px; color: var(--muted); }
    .msg-md hr { border: 0; border-top: 1px solid var(--line); margin: 20px 0; }
    .table-wrap, .usage-table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); margin: 12px 0; }
    .msg-md table, .usage-table { border-collapse: collapse; width: 100%; font-size: 12px; }
    .msg-md td, .msg-md th, .usage-table td, .usage-table th { padding: 11px 13px; border-bottom: 1px solid var(--line); text-align: start; }
    .msg-md th, .usage-table th { color: var(--muted); background: var(--surface-2); font-size: 11px; font-weight: 600; white-space: nowrap; }
    .usage-table td { font-variant-numeric: tabular-nums; }
    .usage-table tr:last-child td { border-bottom: 0; }
    .code-block { margin: 14px 0; border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; background: var(--bg); }
    .code-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 7px 10px 7px 14px; border-bottom: 1px solid var(--line); background: var(--surface-2); font: 10px var(--font-mono); color: var(--muted); }
    .code-copy { min-height: 26px; padding: 3px 9px; font-size: 10px; }
    .code-block pre { margin: 0; padding: 15px; overflow-x: auto; font: 12px/1.7 var(--font-mono); }
    .streaming-caret { display: inline-block; width: 6px; height: 14px; background: var(--accent); margin-inline-start: 4px; vertical-align: -2px; animation: pulse 1s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: .35; } }
    .workflow-panel { flex: 1; min-height: 0; overflow: auto; padding: 28px; overscroll-behavior: contain; }
    .panel-header { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 10px 16px; margin: 0 0 22px; }
    .panel-header > div:first-child { flex: 1; min-width: 180px; }
    .panel-header h2, .workflow-panel h2 { margin: 0; color: var(--text); font-size: 21px; font-weight: 600; line-height: 1.35; }
    .panel-description, .hint { color: var(--muted); font-size: 12px; line-height: 1.65; margin: 6px 0 14px; }
    .panel-header .panel-description { margin-bottom: 0; max-width: 65ch; }
    .workflow-panel h3, .settings-section-title { font-size: 13px; font-weight: 600; margin: 22px 0 10px; }
    .section-card, .workflow-card { padding: 18px; margin: 0 0 16px; border: 1px solid var(--line); border-radius: var(--radius-panel); background: color-mix(in srgb, var(--surface) 75%, var(--bg)); min-width: 0; }
    .workflow-card { margin-top: 12px; }
    .section-card h3 { margin-top: 0; }
    .section-card h4 { margin: 0 0 3px; font-size: 13px; font-weight: 600; }
    .workflow-card p, .workflow-card .hint { overflow-wrap: anywhere; }
    .workflow-panel button { min-height: 34px; font-size: 11px; }
    .workflow-panel input, .workflow-panel select, .workflow-panel textarea { font-size: 12px; }
    .workflow-panel textarea { width: 100%; min-height: 74px; }
    .workflow-row h2 { margin-inline-end: auto; }
    .workflow-row label, .field-grid label { display: grid; gap: 6px; min-width: 0; color: var(--muted); font-size: 11px; }
    .field-grid, .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 14px; }
    .field-grid > *, .grid-2 > * { min-width: 0; }
    .stack { display: grid; gap: 15px; }
    .checkbox-label { display: flex !important; align-items: center; gap: 9px; font-size: 12px; color: var(--soft); }
    .plan-steps { list-style-position: outside; padding-inline-start: 26px; margin: 16px 0; }
    .plan-steps li { padding: 10px 0; border-bottom: 1px solid var(--line); }
    .plan-steps li:last-child { border-bottom: 0; }
    .plan-steps .plan-empty { list-style: none; margin-inline-start: -26px; border: 0; }
    .plan-steps input:not([type=checkbox]) { flex: 1; }
    .worker-results { margin-top: 12px; padding: 12px; border-radius: var(--radius-small); background: var(--bg); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 260px; overflow: auto; font: 12px/1.7 var(--font-mono); }
    .worker-meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 7px 14px; margin: 14px 0; font-size: 11px; }
    .worker-meta dt { color: var(--muted); } .worker-meta dd { margin: 0; overflow-wrap: anywhere; }
    .worker-error { color: var(--danger); }
    .diff-patch { overflow: auto; max-height: 380px; margin: 0; padding: 14px; background: var(--bg); font: 12px/1.65 var(--font-mono); white-space: pre; }
    .diff-hunk { margin: 12px 0; border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
    .diff-hunk > .workflow-row { padding: 9px 12px; background: var(--surface-2); border-bottom: 1px solid var(--line); }
    .diff-hunk h4 { margin: 0 auto 0 0; font: 11px var(--font-mono); overflow-wrap: anywhere; }
    .diff-lines { overflow: auto; font: 12px/1.8 var(--font-mono); }
    .diff-line { display: grid; grid-template-columns: 42px 42px minmax(0, 1fr); min-width: 320px; }
    .diff-line.add { background: var(--ok-soft); } .diff-line.remove { background: var(--danger-soft); }
    .diff-line code { white-space: pre; padding: 0 9px; font: inherit; }
    .diff-line .line-number { padding: 0 4px; min-height: 0; border: 0; border-radius: 0; background: transparent; color: var(--muted); font: inherit; }
    .inline-comment { margin: 7px 12px; padding: 10px 13px; border-inline-start: 2px solid var(--accent); background: var(--accent-soft); white-space: pre-wrap; font-size: 12px; overflow-wrap: anywhere; }
    .comment-form { padding: 12px; }
    .overlay { position: fixed; inset: 0; z-index: 100; display: none; align-items: center; justify-content: center; padding: 24px; }
    .overlay.open { display: flex; }
    .mask { position: absolute; inset: 0; background: #00000070; backdrop-filter: blur(5px); }
    .settings-panel { position: relative; z-index: 1; display: flex; width: min(980px, 100%); height: min(760px, calc(100dvh - 48px)); border: 1px solid var(--line-strong); border-radius: var(--radius-large); overflow: hidden; background: var(--surface); box-shadow: 0 24px 100px #00000050; }
    .settings-nav { flex: none; display: flex; flex-direction: column; gap: 5px; width: 190px; padding: 24px 12px; border-inline-end: 1px solid var(--line); background: var(--bg); }
    .settings-nav h2 { margin: 0 0 22px; padding: 0 12px; font-size: 16px; font-weight: 600; }
    .settings-nav button { text-align: start; min-height: 40px; padding: 8px 12px; border-color: transparent; background: transparent; color: var(--muted); font-size: 12px; }
    .settings-nav button:hover { color: var(--text); background: var(--panel-hover); }
    .settings-nav button.active { color: var(--text); background: var(--surface-2); border-color: var(--line); }
    .settings-content { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
    .settings-head { display: flex; align-items: center; justify-content: flex-end; gap: 14px; flex: none; padding: 13px 14px 0; }
    .settings-head > :first-child:not(button) { margin-inline-end: auto; margin-inline-start: 18px; color: var(--muted); font-size: 10px; }
    .settings-body { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 32px 30px; overscroll-behavior: contain; }
    .settings-body > h3 { margin: 0; font-size: 23px; font-weight: 600; letter-spacing: -.4px; }
    .settings-body .panel-header h3 { margin: 0; font-size: 23px; font-weight: 600; letter-spacing: -.4px; }
    .settings-body .panel-header h4 { margin: 0 auto 0 0; font-size: 13px; font-weight: 600; }
    .table-empty { padding: 24px !important; text-align: center !important; color: var(--muted); }
    .settings-body > .hint { margin: 7px 0 23px; max-width: 58ch; }
    .settings-body label { display: grid; align-content: start; align-self: start; gap: 8px; color: var(--muted); font-size: 11px; line-height: 1.5; }
    .settings-body :is(input:not([type=checkbox]), select) { height: 42px; color: var(--text); font-size: 13px; line-height: 1.5; }
    .settings-body label input, .settings-body label select, .settings-body label textarea { width: 100%; }
    .settings-body label textarea { min-height: 90px; color: var(--text); font-size: 13px; line-height: 1.6; }
    .form-field { display: grid; align-content: start; gap: 8px; min-width: 0; }
    .form-field > :is(input, select) { width: 100%; }
    .form-field > :is(.hint, .model-metadata) { margin: 0; font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
    .settings-body form button { min-height: 36px; font-size: 12px; }
    .settings-notice { margin: 8px 24px 12px; padding: 10px 13px; border: 1px solid var(--line); border-radius: var(--radius); color: var(--soft); background: var(--accent-soft); font-size: 12px; }
    .settings-notice.error { color: var(--danger); background: var(--danger-soft); }
    .setting-row { display: flex; align-items: center; gap: 22px; }
    .setting-row:not(.section-card) { padding: 17px 0; border-bottom: 1px solid var(--line); }
    .setting-row:not(.section-card):last-child { border-bottom: 0; }
    .setting-row .copy { flex: 1; min-width: 0; }
    .setting-row strong { display: block; font-size: 13px; font-weight: 600; }
    .setting-row small { display: block; margin-top: 4px; color: var(--muted); font-size: 11px; line-height: 1.6; }
    .setting-row select { max-width: 220px; font-size: 12px; }
    .metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 20px 0; }
    .metric { display: flex; flex-direction: column; gap: 6px; min-width: 0; padding: 17px; border: 1px solid var(--line); border-radius: var(--radius); background: color-mix(in srgb, var(--surface-2) 45%, var(--surface)); }
    .metric span { color: var(--muted); font-size: 10px; }
    .metric strong { color: var(--text); font-size: 24px; font-weight: 550; line-height: 1.25; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    .metric small { color: var(--muted); font-size: 10px; overflow-wrap: anywhere; }
    .model-metadata { color: var(--muted); font-size: 10px; line-height: 1.6; margin: 7px 0 0; }
    .session-model { color: var(--muted); font: 11px/1.5 var(--font-mono); overflow-wrap: anywhere; }
    .empty-icon img { width: 26px; height: 26px; }
    .model-route-card { display: grid; gap: 7px; padding: 16px; border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--line)); border-radius: var(--radius); background: var(--accent-soft); margin: 18px 0 22px; color: var(--text); overflow-wrap: anywhere; }
    .model-route-card .eyebrow { color: var(--muted); font-size: 10px; }
    #modelRoute { font-size: 14px; font-weight: 600; }
    .model-route-card .hint { margin: 0; font-size: 11px; }
    .model-discovery-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 20px; margin-top: 4px; padding-top: 16px; border-top: 1px solid var(--line); }
    .model-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-inline-start: auto; }
    .settings-body :is(.model-actions, .endpoint-row) button { min-height: 42px; white-space: nowrap; }
    .endpoint-settings { margin-top: 24px; border-top: 1px solid var(--line); }
    #modelDiscoveryStatus { flex: 1 1 180px; font-size: 11px; color: var(--muted); margin: 0; overflow-wrap: anywhere; }
    .model-chip { min-height: 32px; padding: 5px 10px; font: 11px var(--font-mono); }
    .model-chip:hover { background: var(--accent-soft); border-color: var(--accent); }
    .endpoint-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: start; gap: 8px; }
    .endpoint-row input { width: 100%; }
    #llamacppStatus { margin: 0; overflow-wrap: anywhere; }
    .plugin-scope { display: grid; gap: 6px; padding: 16px; border: 1px solid var(--line); border-inline-start: 2px solid var(--accent); background: var(--accent-soft); border-radius: var(--radius); }
    .plugin-scope .eyebrow { color: var(--muted); font-size: 10px; }
    .plugin-scope strong { font-size: 12px; font-weight: 500; overflow-wrap: anywhere; }
    .plugin-scope .hint { margin: 0; font-size: 11px; }
    .plugin-privacy { display: flex; flex-wrap: wrap; gap: 7px 14px; margin: 16px 0; }
    .plugin-privacy-item { color: var(--muted); font-size: 10px; line-height: 1.6; }
    .plugin-privacy-item::before { content: '·'; color: var(--accent); margin-inline-end: 6px; }
    #pluginsStatus { min-height: 20px; margin: 14px 0; overflow-wrap: anywhere; }
    .plugin-list { display: grid; gap: 12px; }
    .plugin-card, .plugin-empty { min-width: 0; padding: 18px; border: 1px solid var(--line); border-radius: var(--radius-panel); background: var(--bg); }
    .plugin-card-head { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
    .plugin-identity { min-width: 0; }
    .plugin-card h4, .plugin-empty h4 { margin: 0; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
    .plugin-identity .tiny { margin: 5px 0 0; overflow-wrap: anywhere; }
    .plugin-state { flex: none; font-size: 10px; padding: 3px 7px; border: 1px solid var(--line); border-radius: var(--radius-small); color: var(--muted); }
    .plugin-state.danger { color: var(--danger); border-color: var(--danger); }
    .plugin-grants { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 6px 12px; margin: 18px 0 12px; font-size: 11px; line-height: 1.6; }
    .plugin-grants dt { color: var(--muted); }
    .plugin-grants dd { margin: 0; color: var(--soft); overflow-wrap: anywhere; }
    .plugin-tools { color: var(--muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; margin: 0; }
    .plugin-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); }
    .plugin-actions button { min-height: 38px; font-size: 11px; }
    .plugin-install-command { display: block; color: var(--text); padding: 12px; background: var(--surface-2); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
    .automation-list { display: grid; gap: 12px; margin-top: 22px; }
    .automation { border: 1px solid var(--line); border-radius: var(--radius-panel); background: var(--bg); padding: 17px; display: grid; gap: 10px; }
    .automation-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .automation-meta { display: flex; gap: 5px 12px; flex-wrap: wrap; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
    .automation .tiny { overflow-wrap: anywhere; }
    .usage-sub { margin: 24px 0 8px; font-size: 12px; font-weight: 600; color: var(--soft); }
    .cache-usage { display: flex; flex-wrap: wrap; gap: 16px 28px; padding: 16px 0; border-block: 1px solid var(--line); }
    .cache-usage > div { display: grid; gap: 5px; flex: 1 1 110px; }
    .cache-usage span { color: var(--muted); font-size: 11px; }
    .cache-usage strong { font-size: 17px; font-variant-numeric: tabular-nums; }
    .about-brand { display: flex; gap: 14px; align-items: center; margin: 16px 0; }
    .about-links { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 20px; }
    .about-links a { border: 1px solid var(--line); border-radius: var(--radius); padding: 12px 14px; font-size: 12px; color: var(--soft); }
    .about-links a:hover { color: var(--text); border-color: var(--line-strong); background: var(--panel-hover); }
    @media (max-width: 1100px) {
      .app { grid-template-columns: 228px minmax(0, 1fr); }
      .sidebar { padding-inline: 9px; }
      .main-head { padding-inline: 20px; }
      .view-tabs { padding-inline: 16px; }
      .conversation, .composer-zone { padding-inline: 20px; }
      .workflow-panel { padding: 22px; }
      .settings-body { padding-inline: 24px; }
    }
    @media (max-width: 760px) {
      .app, .app.rail { grid-template-columns: minmax(0, 1fr); }
      .app .sidebar, .app.rail .sidebar { position: fixed; inset: 0 auto 0 0; width: min(300px, 85vw); z-index: 50; padding: 16px 12px 10px; align-items: stretch; transform: translateX(-105%); visibility: hidden; transition: transform .18s ease, visibility .18s; }
      .app.mobile-open .sidebar { transform: translateX(0); visibility: visible; box-shadow: 20px 0 70px #00000040; }
      .sidebar-backdrop { position: fixed; inset: 0; z-index: 40; display: block; border: 0; border-radius: 0; background: #00000070; backdrop-filter: blur(2px); }
      .sidebar-backdrop:hover { background: #00000070; }
      #mobileTasks, .mobile-tasks-close { display: inline-flex; }
      .app .sidebar .logo-row { height: 40px; padding: 0 4px; margin-bottom: 18px; justify-content: space-between; }
      .app .sidebar #railToggle { display: none; }
      .app .sidebar .mobile-tasks-close { display: inline-flex; }
      .app .sidebar .brand > span:not(.logo-wrap), .app .sidebar .new-session span, .app .sidebar .run-title, .app .sidebar .run-time, .app .sidebar .side-foot-row .grow, .app .sidebar .side-foot-row .tiny { display: block; }
      .app .sidebar .brand .harness-tag { display: none; }
      .app .sidebar .section-label { display: flex; }
      .app .sidebar .filter-row { display: grid; }
      .app .sidebar .new-session { width: auto; min-height: 40px; padding: 7px 12px; margin: 0 2px 20px; }
      .app .sidebar .runs { width: auto; align-items: stretch; }
      .app .sidebar .run-item { width: 100%; min-height: 48px; justify-content: flex-start; padding: 9px 10px; }
      .app .sidebar .side-foot { display: block; }
      .app .sidebar .side-foot-row { width: 100%; min-height: 36px; justify-content: flex-start; padding: 7px 9px; }
      .main-head { min-height: 60px; padding: 12px 14px; gap: 7px; }
      .main-head h1 { font-size: 14px; }
      .pill-btn { font-size: 10px; padding: 5px 8px; }
      .view-tabs { padding: 8px 10px; gap: 1px; }
      .view-tab { font-size: 11px; padding-inline: 10px; }
      #eventCount { display: none; }
      .conversation { padding: 20px 15px; }
      .composer-zone { padding: 10px 12px; }
      .composer { padding: 11px; }
      .composer-controls { gap: 5px; }
      .composer-controls input, .composer-controls select { max-width: 170px; font-size: 10px; }
      .workflow-panel { padding: 18px 14px; }
      .settings-panel { height: min(780px, calc(100dvh - 24px)); flex-direction: column; }
      .overlay { padding: 12px; }
      .settings-nav { width: 100%; flex-direction: row; overflow-x: auto; gap: 3px; padding: 12px; border-inline-end: 0; border-bottom: 1px solid var(--line); flex-shrink: 0; }
      .settings-nav h2 { display: none; }
      .settings-nav button { flex: none; min-height: 38px; padding: 7px 10px; font-size: 11px; }
      .settings-head { padding-top: 8px; }
      .settings-body { padding: 0 20px 24px; }
      .settings-body > h3 { font-size: 21px; }
      .settings-notice { margin-inline: 20px; }
      .metric { padding: 12px; }
      .metric strong { font-size: 20px; }
      .metric-grid { gap: 8px; }
    }
    @media (max-width: 600px) {
      .plugin-card-head { flex-wrap: wrap; }
      .plugin-card, .plugin-empty { padding: 14px; }
      .plugin-actions button { width: 100%; }
      .plugin-grants { grid-template-columns: 76px minmax(0, 1fr); gap: 6px 8px; }
      .model-fields { grid-template-columns: 1fr; gap: 20px; }
      .model-actions { width: 100%; }
      .model-actions button { flex: 1; }
      .endpoint-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .endpoint-row input { grid-column: 1 / -1; }
    }
    @media (max-width: 480px) {
      .main-head .pill { display: none; }
      .main-head { flex-wrap: nowrap; }
      .main-head h1 { flex-basis: 0; }
      .composer-controls input { flex: 1; min-width: 100px; }
      .composer-controls .spacer { display: none; }
      .send-btn { margin-inline-start: auto; }
      .composer-controls select { max-width: 100%; }
      .status-strip #stripMeta, #stripMeta + .sep { display: none; }
      .starter-grid, .grid-2, .field-grid, .about-links { grid-template-columns: 1fr; }
      .empty-state { padding: 22px 4px; }
      .empty-state h2 { font-size: 21px; }
      .section-card, .workflow-card { padding: 14px; }
      .setting-row { align-items: flex-start; flex-direction: column; gap: 12px; }
      .setting-row select { width: 100%; max-width: none; }
      .metric-grid { grid-template-columns: 1fr; }
      .metric { flex-direction: row; align-items: center; flex-wrap: wrap; gap: 6px 12px; }
      .metric strong { margin-inline-start: auto; }
      .metric small { flex-basis: 100%; }
      .workflow-row input:not([type=checkbox]) { flex: 1; min-width: 100px; }
      .worker-meta { grid-template-columns: 1fr; gap: 3px; }
      .worker-meta dd { margin-bottom: 7px; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
    }
"""



def _palette_css(theme_id: str, *, selector: str | None = None) -> str:
    palette = THEME_PALETTES[theme_id]
    colors = {
        "bg": palette.background,
        "surface": palette.surface,
        "surface-2": palette.surface_2,
        "panel": palette.panel,
        "panel-strong": palette.panel_strong,
        "panel-hover": palette.surface_2,
        "sidebar-fill": palette.sidebar,
        "canvas": palette.background,
        "text": palette.text,
        "soft": palette.soft,
        "muted": palette.muted,
        "accent": palette.accent,
        "accent-strong": palette.accent_strong,
        "on-accent": palette.on_accent,
        "on-accent-strong": palette.on_accent_strong,
        "tool-accent": palette.tool,
        "danger": palette.danger,
        "ok": palette.ok,
        "warn": palette.warn,
        "line": palette.line,
        "line-strong": palette.line_strong,
        "code": palette.code,
    }
    declarations = [f"--{name}: {value};" for name, value in colors.items()]
    declarations += [
        f"color-scheme: {'light' if palette.is_light else 'dark'};",
        '--font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;',
        "--font-ui: var(--font-mono);",
        "--radius: 0px; --radius-small: 0px; --radius-panel: 0px; --radius-large: 0px;",
        "--accent-soft: color-mix(in srgb, var(--accent) 10%, transparent);",
        "--tool-soft: color-mix(in srgb, var(--tool-accent) 12%, transparent);",
        "--danger-soft: color-mix(in srgb, var(--danger) 12%, transparent);",
        "--ok-soft: color-mix(in srgb, var(--ok) 12%, transparent);",
        "--warn-soft: color-mix(in srgb, var(--warn) 12%, transparent);",
    ]
    target = selector or f'html[data-theme="{theme_id}"]'
    return target + " {\n" + "\n".join(declarations) + "\n}"


DASHBOARD_CSS += _palette_css("libre", selector=":root")
DASHBOARD_CSS += "\n".join(_palette_css(name) for name in THEME_PALETTES)
DASHBOARD_CSS += r"""
    .welcome-wordmark { width: min(380px, 100%); margin: 0 0 22px; }
    .pixel-wordmark { display: block; width: 100%; height: auto; overflow: visible; }
    .wordmark-top { stop-color: color-mix(in srgb, var(--text) 80%, var(--accent)); }
    .wordmark-middle { stop-color: color-mix(in srgb, var(--text) 40%, var(--accent)); }
    .wordmark-bottom { stop-color: var(--accent); }
    .wordmark-depth { fill: color-mix(in srgb, var(--accent) 48%, var(--bg)); }
    .wordmark-shadow { fill: var(--bg); }
    .brand { font-size: 13px; gap: 8px; }
    .brand:hover { color: var(--accent); }
    .logo-wrap { border: 0; background: none; }
    :is(.main-head h1, .panel-header h2, .settings-body h3) { letter-spacing: -.04em; }
    :is(.section-label > span:first-child, .metric > span, .eyebrow) { text-transform: uppercase; letter-spacing: .12em; font-size: 10px; }
    :is(.new-session, button.primary, .send-btn) { color: var(--on-accent); background: var(--accent); border-color: var(--accent); }
    .new-session svg { color: inherit; }
    :is(.new-session, button.primary, .send-btn):hover { color: var(--on-accent-strong); background: var(--accent-strong); border-color: var(--accent-strong); opacity: 1; }
    :is(.view-tab.active, .settings-nav button.active) { color: var(--accent-strong); background: var(--accent-soft); box-shadow: inset 0 -2px var(--accent); border-color: transparent; }
    .run-item.active { border-color: var(--line); box-shadow: inset 2px 0 var(--accent); }
    :is(.state-dot, .status-dot) { border-radius: 0; }
    .composer { position: relative; border-color: var(--line-strong); box-shadow: 3px 3px 0 var(--surface-2); }
    .composer:focus-within { border-color: var(--accent); box-shadow: 3px 3px 0 var(--accent-soft); }
    .composer::before { content: ''; position: absolute; top: -1px; left: -1px; width: 18px; height: 2px; background: var(--accent); }
    .welcome-state { position: relative; padding-block: 42px; }
    .welcome-state > strong { font-size: clamp(19px, 2vw, 25px); font-weight: 600; letter-spacing: -.05em; }
    .welcome-state > p { font-size: 12px; max-width: 44ch; }
    .starter-action { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 11px; }
    .starter-action::after { content: '↗'; color: var(--accent); font-size: 15px; }
    :is(.code-block, .msg-tool pre, .diff-patch, .worker-results) { background: var(--code); }
    .settings-panel { box-shadow: 5px 5px 0 var(--bg), 5px 5px 0 1px var(--line-strong); }
    .about-brand .logo-wrap { width: 40px; height: 40px; }
    @media (max-width: 760px) {
      .welcome-state { padding-block: 22px; }
      .welcome-wordmark { width: min(320px, 100%); margin-bottom: 15px; }
    }
"""
