# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
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
  <style>
    :root {
      color-scheme: dark;
      --bg: #000000;
      --surface: #050505;
      --panel: rgba(8, 8, 8, 0.82);
      --panel-strong: #111111;
      --line: rgba(255, 255, 255, 0.11);
      --line-strong: rgba(255, 255, 255, 0.18);
      --text: #ffffff;
      --soft: #d4d4d8;
      --muted: #a1a1aa;
      --accent: #0070f3;
      --accent-soft: rgba(0, 112, 243, 0.14);
      --accent-strong: #dbeafe;
      --purple: #8b5cf6;
      --purple-soft: rgba(139, 92, 246, 0.13);
      --danger: #ff4d4f;
      --danger-soft: rgba(255, 77, 79, 0.14);
      --ok: #42d392;
      --ok-soft: rgba(66, 211, 146, 0.12);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.12);
      --grid-dot: rgba(255, 255, 255, 0.16);
      --shadow: 0 24px 70px rgba(0, 0, 0, 0.48);
      font-family: "Atkinson", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    html {
      background: var(--bg);
      overflow-x: clip;
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
      background-image: radial-gradient(circle at center, var(--grid-dot) 1px, transparent 1px);
      background-size: 24px 24px;
      mask-image: linear-gradient(to bottom, #000 0%, rgba(0, 0, 0, 0.72) 44%, transparent 82%);
      pointer-events: none;
    }
    button, input, textarea, select {
      font: inherit;
    }
    button {
      border: 1px solid var(--line);
      color: var(--text);
      background: rgba(255, 255, 255, 0.04);
      border-radius: 6px;
      padding: 9px 11px;
      cursor: pointer;
      transition: border-color .16s ease, background .16s ease, transform .16s ease;
    }
    button:hover { border-color: color-mix(in srgb, var(--accent) 60%, var(--line)); background: rgba(255, 255, 255, 0.07); }
    button:active { transform: translateY(1px); }
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
      border: 1px solid var(--line);
      background: #050505;
      color: var(--text);
      border-radius: 6px;
      padding: 9px 10px;
      outline: none;
    }
    input:focus, textarea:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
    textarea { min-height: 110px; resize: vertical; }
    label {
      display: grid;
      gap: 6px;
    }
    .app {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 18px;
      width: min(1480px, calc(100% - 32px));
      margin: 0 auto;
      padding: 18px 0;
      min-height: 100vh;
    }
    aside {
      align-self: start;
      position: sticky;
      top: 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      min-width: 0;
    }
    main {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      gap: 16px;
      min-width: 0;
    }
    .topbar {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      border: 1px solid var(--line);
      background: rgba(5, 5, 5, 0.82);
      backdrop-filter: blur(18px);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 800;
      letter-spacing: 0;
      min-width: 0;
    }
    .brand small {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .logo {
      flex: 0 0 auto;
      width: 34px;
      height: 34px;
      border-radius: 7px;
      display: block;
      object-fit: cover;
      box-shadow: 0 0 0 1px var(--line), 0 0 24px var(--accent-soft);
    }
    .top-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .status {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .metric, section {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .metric { padding: 12px; }
    .metric span, label, .tiny {
      color: var(--muted);
      font-size: 12px;
    }
    .metric strong {
      display: block;
      margin-top: 5px;
      font-size: 18px;
      letter-spacing: 0;
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
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.025);
    }
    .section-head h2, .section-head h3 {
      margin: 0;
      font-size: 14px;
      letter-spacing: 0;
    }
    .body { padding: 14px; }
    .stack { display: grid; gap: 10px; }
    .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
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
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.03);
    }
    .pill.done { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 52%, var(--line)); background: var(--ok-soft); }
    .pill.running, .pill.queued { color: var(--accent-strong); border-color: color-mix(in srgb, var(--accent) 58%, var(--line)); background: var(--accent-soft); }
    .pill.blocked { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 58%, var(--line)); background: var(--warn-soft); }
    .pill.failed, .pill.cancelled { color: #ffd7da; border-color: color-mix(in srgb, var(--danger) 58%, var(--line)); background: var(--danger-soft); }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(320px, .8fr);
      gap: 16px;
      min-height: 0;
    }
    .timeline {
      max-height: 58vh;
      overflow: auto;
      display: grid;
      gap: 8px;
      padding-right: 4px;
    }
    .event {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px;
      background: rgba(255, 255, 255, 0.025);
    }
    .event-type {
      color: var(--purple);
      font-size: 12px;
      font-weight: 800;
      margin-bottom: 6px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--soft);
      font-size: 13px;
      line-height: 1.45;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      text-align: center;
    }
    .automation {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.025);
      border-radius: 7px;
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .automation strong { font-size: 13px; }
    .notice {
      border: 1px solid color-mix(in srgb, var(--accent) 46%, var(--line));
      background: var(--accent-soft);
      color: var(--accent-strong);
      padding: 10px;
      border-radius: 7px;
      font-size: 13px;
    }
    .error {
      border-color: #7a1d24;
      background: var(--danger-soft);
      color: #ffc3c7;
    }
    .dashboard-footer {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border: 1px solid var(--line);
      background: rgba(5, 5, 5, 0.58);
      border-radius: 8px;
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
    .dashboard-footer span {
      color: var(--soft);
    }
    ::-webkit-scrollbar { width: 7px; height: 7px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--accent); border-radius: 999px; }
    @media (max-width: 980px) {
      .app { grid-template-columns: 1fr; }
      aside { position: static; }
      .workspace { grid-template-columns: 1fr; }
      .status { grid-template-columns: 1fr; }
    }
    @media (max-width: 680px) {
      .app {
        width: min(100%, calc(100% - 20px));
        padding: 10px 0;
        gap: 10px;
      }
      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }
      .top-actions,
      .top-actions button,
      .top-actions a {
        width: 100%;
      }
      .status,
      .grid-2 {
        grid-template-columns: 1fr;
      }
      .section-head {
        align-items: flex-start;
        flex-direction: column;
      }
      .row button {
        flex: 1 1 140px;
      }
      .runs {
        max-height: 36vh;
      }
      .timeline {
        max-height: 70vh;
      }
      .run-title {
        white-space: normal;
      }
      textarea {
        min-height: 92px;
      }
      .dashboard-footer {
        align-items: flex-start;
        flex-direction: column;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <img class="logo" src="/assets/logo-dark.jpg" alt="" width="34" height="34" aria-hidden="true">
        <div>
          <div>Libre Claw Dashboard</div>
          <small>Local control plane for daemon runs, approvals, schedules, and usage.</small>
        </div>
      </div>
      <div class="top-actions">
        <button class="ghost" id="refreshAll" type="button">Refresh</button>
        <button class="primary" id="focusRunInput" type="button">New Run</button>
      </div>
    </header>
    <aside>
      <div class="status">
        <div class="metric"><span>Daemon</span><strong id="daemonStatus">...</strong></div>
        <div class="metric"><span>Active</span><strong id="activeRuns">0</strong></div>
        <div class="metric"><span>Tokens</span><strong id="usageTokens">0</strong></div>
      </div>
      <section>
        <div class="section-head"><h2>Start Run</h2></div>
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
          <h2>Runs</h2>
          <button class="ghost" id="refreshRuns" type="button">Refresh</button>
        </div>
        <div class="body runs" id="runs"></div>
      </section>
    </aside>
    <main>
      <section>
        <div class="section-head">
          <h2 id="selectedTitle">No run selected</h2>
          <div class="row">
            <span class="pill" id="selectedState">idle</span>
            <button id="cancelRun" class="danger" type="button" disabled>Cancel</button>
          </div>
        </div>
        <div class="body stack">
          <div id="notice" class="notice">Open this page from the local daemon: http://127.0.0.1:8766/dashboard</div>
          <div id="permissions" class="stack"></div>
        </div>
      </section>
      <div class="workspace">
        <section>
          <div class="section-head"><h2>Timeline</h2><span class="tiny" id="eventCount">0 events</span></div>
          <div class="body timeline" id="timeline"></div>
        </section>
        <section>
          <div class="section-head"><h2>Automations</h2></div>
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
              <button type="submit">Create Schedule</button>
            </form>
            <div id="automations" class="stack"></div>
          </div>
        </section>
      </div>
    </main>
    <footer class="dashboard-footer">
      <span>Libre Claw dashboard</span>
      <nav aria-label="Dashboard footer links">
        <a href="https://libreclaw.dev" target="_blank" rel="noreferrer">libreclaw.dev</a>
        <a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer">Apache-2.0</a>
        <a href="https://kroonen.ai" target="_blank" rel="noreferrer">Kroonen AI Inc.</a>
      </nav>
    </footer>
  </div>
  <script>
    const state = { selectedRunId: "", events: [] };
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

    async function refreshHealth() {
      const health = await request("/health");
      $("daemonStatus").textContent = health.ok ? "online" : "offline";
      $("activeRuns").textContent = health.active_runs ?? 0;
    }

    async function refreshUsage() {
      const usage = await request("/usage?limit=250");
      const totalTokens = usage.summary?.total_tokens ?? 0;
      const tokenNode = $("usageTokens");
      tokenNode.textContent = formatCompactNumber(totalTokens);
      tokenNode.title = `${formatExactNumber(totalTokens)} tokens`;
    }

    async function refreshRuns() {
      const payload = await request("/runs?limit=40");
      const container = $("runs");
      container.replaceChildren();
      if (!payload.runs?.length) {
        container.append(empty("No runs yet."));
        return;
      }
      for (const run of payload.runs) {
        const button = document.createElement("button");
        button.className = `run-item ${run.run_id === state.selectedRunId ? "active" : ""}`;
        const title = document.createElement("strong");
        title.className = "run-title";
        title.textContent = run.title;
        const meta = document.createElement("div");
        meta.className = "run-meta";
        meta.textContent = `${run.run_id} | ${run.provider}:${run.model} | ${formatTime(run.updated_at)}`;
        button.append(title, meta, pill(run.state));
        button.addEventListener("click", () => selectRun(run.run_id));
        container.append(button);
      }
      if (!state.selectedRunId && payload.runs[0]) await selectRun(payload.runs[0].run_id);
    }

    async function selectRun(runId) {
      state.selectedRunId = runId;
      await refreshRunDetail();
      await refreshRuns();
    }

    async function refreshRunDetail() {
      if (!state.selectedRunId) return;
      const detail = await request(`/runs/${state.selectedRunId}`);
      const run = detail.run;
      $("selectedTitle").textContent = run.title;
      $("selectedState").textContent = run.state;
      $("selectedState").className = `pill ${run.state}`;
      $("cancelRun").disabled = !["queued", "running", "blocked"].includes(run.state);
      const events = await request(`/runs/${state.selectedRunId}/events?after=0`);
      state.events = events.events || [];
      renderEvents();
      renderPermissions(detail.pending_permissions || []);
    }

    function renderEvents() {
      $("eventCount").textContent = `${state.events.length} events`;
      const container = $("timeline");
      container.replaceChildren();
      if (!state.events.length) {
        container.append(empty("No events yet."));
        return;
      }
      for (const event of state.events.slice().reverse()) {
        const item = document.createElement("div");
        item.className = "event";
        const type = document.createElement("div");
        type.className = "event-type";
        type.textContent = `${event.event_id} / ${event.type} / ${formatTime(event.timestamp)}`;
        const body = document.createElement("pre");
        body.textContent = eventText(event);
        item.append(type, body);
        container.append(item);
      }
    }

    function eventText(event) {
      const data = event.data || {};
      if (event.type === "assistant_delta") return data.text || "";
      if (event.type === "user_message") return data.content || "";
      if (event.type === "tool_call") return `${data.name}\n${JSON.stringify(data.arguments || {}, null, 2)}`;
      if (event.type === "tool_result") return `${data.name} ${data.is_error ? "error" : "result"}\n${truncate(data.content, 2200)}`;
      if (event.type === "permission_request") return `${data.name}\n${JSON.stringify(data.arguments || {}, null, 2)}`;
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
        box.className = "event";
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
      if (!payload.automations?.length) {
        container.append(empty("No schedules yet."));
        return;
      }
      for (const automation of payload.automations) {
        const box = document.createElement("div");
        box.className = "automation";
        const title = document.createElement("strong");
        title.textContent = automation.name;
        const meta = document.createElement("div");
        meta.className = "tiny";
        meta.textContent = `${automation.schedule} | ${automation.route} | next ${formatTime(automation.next_run_at)}`;
        const row = document.createElement("div");
        row.className = "row";
        const toggle = document.createElement("button");
        toggle.textContent = automation.status === "active" ? "Pause" : "Resume";
        toggle.addEventListener("click", () => toggleAutomation(automation));
        const del = document.createElement("button");
        del.textContent = "Delete";
        del.className = "danger";
        del.addEventListener("click", () => deleteAutomation(automation.automation_id));
        row.append(pill(automation.status), toggle, del);
        box.append(title, meta, row);
        container.append(box);
      }
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
      const chat = $("automationChat").value.trim();
      const body = {
        name: $("automationName").value,
        schedule: $("automationSchedule").value,
        prompt: $("automationPrompt").value,
        route: $("automationRoute").value,
      };
      if (chat) body.telegram_chat_id = Number(chat);
      const payload = await request("/automations", { method: "POST", body: JSON.stringify(body) });
      setNotice(`Schedule ${payload.automation.automation_id} created.`);
      event.target.reset();
      await refreshAutomations();
    });

    $("refreshRuns").addEventListener("click", refreshRuns);
    $("refreshAll").addEventListener("click", refreshAll);
    $("focusRunInput").addEventListener("click", () => $("runMessage").focus());
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
      } catch (error) {
        setNotice(error.message || String(error), true);
      }
    }

    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""
