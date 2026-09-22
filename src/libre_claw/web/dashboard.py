# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from html import escape

from libre_claw.core.branding import WORDMARK_ROWS
from libre_claw.core.themes import THEME_ALIASES, THEME_PALETTES, dashboard_theme_id
from libre_claw.web.dashboard_styles import DASHBOARD_CSS


def dashboard_html(theme: str = "libre") -> str:
    """Return the self-contained local daemon dashboard."""
    theme_id = dashboard_theme_id(theme)
    fallback_theme = json.dumps(theme_id)
    theme_options = "\n".join(
        f'<option value="{escape(name)}">{escape(palette.label)}</option>'
        for name, palette in THEME_PALETTES.items()
    )
    return (
        _DASHBOARD_HTML.replace("__LIBRE_CLAW_DASHBOARD_THEME__", fallback_theme)
        .replace("__LIBRE_CLAW_DASHBOARD_THEME_ID__", theme_id)
        .replace("__LIBRE_CLAW_THEME_IDS__", json.dumps(list(THEME_PALETTES)))
        .replace("__LIBRE_CLAW_THEME_ALIASES__", json.dumps(THEME_ALIASES))
        .replace("__LIBRE_CLAW_THEME_OPTIONS__", theme_options)
        .replace("__LIBRE_CLAW_WORDMARK__", _pixel_wordmark())
        .replace("__LIBRE_CLAW_DASHBOARD_CSS__", DASHBOARD_CSS)
    )


def _pixel_wordmark() -> str:
    """Render the shared terminal and website glyphs without a remote font."""
    path = "".join(
        f"M{match.start() + 1} {row + 1}h{len(match[0])}v1h-{len(match[0])}z"
        for row, pixels in enumerate(WORDMARK_ROWS)
        for match in re.finditer("1+", pixels)
    )
    width = len(WORDMARK_ROWS[0]) + 2
    return f'''<svg class="pixel-wordmark" viewBox="0 0 {width} 12" role="img" aria-label="Libre Claw" focusable="false" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="dashboard-wordmark-face" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="wordmark-top"/><stop offset=".5" class="wordmark-middle"/><stop offset="1" class="wordmark-bottom"/>
      </linearGradient></defs>
      <path d="{path}" class="wordmark-shadow" transform="translate(.35 .75)"/>
      <path d="{path}" class="wordmark-depth" transform="translate(.18 .35)"/>
      <path d="{path}" fill="url(#dashboard-wordmark-face)"/>
    </svg>'''


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en" data-theme="__LIBRE_CLAW_DASHBOARD_THEME_ID__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Libre Claw Dashboard</title>
  <link rel="icon" type="image/svg+xml" href="/assets/lobster-icon.svg?v=20260601">
  <script>
    (() => {
      const key = "libre-claw-dashboard-theme";
      const fallback = __LIBRE_CLAW_DASHBOARD_THEME__;
      const aliases = __LIBRE_CLAW_THEME_ALIASES__;
      const themes = new Set(__LIBRE_CLAW_THEME_IDS__);
      let stored = "";
      try { stored = localStorage.getItem(key) || ""; } catch { /* Storage may be disabled. */ }
      const raw = String(stored || fallback).trim().toLowerCase();
      const normalized = aliases[raw] || raw;
      const value = themes.has(normalized) ? normalized : fallback;
      document.documentElement.dataset.theme = value;
    })();
  </script>
  <style>__LIBRE_CLAW_DASHBOARD_CSS__</style>
</head>
<body>
  <template id="wordmarkTemplate">__LIBRE_CLAW_WORDMARK__</template>
  <div class="app" id="appFrame">
    <aside class="sidebar" id="taskSidebar" aria-label="Tasks">
      <div class="logo-row">
        <button class="brand" id="brandHome" type="button" title="Libre Claw Dashboard">
          <span class="logo-wrap" aria-hidden="true"><img src="/assets/lobster-icon.svg" width="26" height="26" alt=""></span>
          <span>Libre Claw</span>
          <span class="harness-tag">LOCAL</span>
        </button>
        <button class="icon-btn" id="railToggle" type="button" aria-label="Collapse sidebar" title="Collapse sidebar">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><line x1="9" y1="4" x2="9" y2="20"/></svg>
        </button>
        <button class="icon-btn mobile-tasks-close" id="closeMobileTasks" type="button" aria-label="Close tasks">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>

      <button class="new-session" id="focusRunInput" type="button">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" width="15" height="15"><path d="M12 5v14M5 12h14"/></svg>
        <span>New task</span>
      </button>

      <div class="section-label">
        <span>Runs</span>
        <span class="filters"><span id="runCount" class="tiny">0 runs</span></span>
      </div>
      <div class="filter-row" aria-label="Run filters">
        <input id="runSearch" type="search" placeholder="Search tasks" aria-label="Search tasks">
        <select id="runStateFilter" aria-label="Filter runs by state">
          <option value="">All states</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="blocked">Blocked</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      <div class="runs" id="runs" aria-label="Recent tasks"></div>

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

    <button class="sidebar-backdrop" id="sidebarBackdrop" type="button" aria-label="Close tasks" tabindex="-1" hidden></button>
    <main class="main" id="mainContent">
      <div class="main-head">
        <button class="icon-btn mobile-tasks-toggle" id="mobileTasks" type="button" aria-label="Open tasks" aria-controls="taskSidebar" aria-expanded="false">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
        </button>
        <h1 id="selectedTitle">Your workspace</h1>
        <span class="pill" id="selectedState">idle</span>
        <button id="cancelRun" class="pill-btn danger" type="button" disabled>Cancel</button>
        <button id="refreshAll" class="pill-btn" type="button">Refresh</button>
      </div>
      <div class="view-tabs" role="tablist" aria-label="Task views">
        <button class="view-tab active" id="tabChat" role="tab" aria-controls="timeline" aria-selected="true" type="button">Chat</button>
        <button class="view-tab" id="tabTrajectory" role="tab" aria-controls="timeline" aria-selected="false" tabindex="-1" type="button">Activity</button>
        <button class="view-tab" id="tabPlan" role="tab" aria-controls="planPanel" aria-selected="false" tabindex="-1" type="button">Plan</button>
        <button class="view-tab" id="tabChanges" role="tab" aria-controls="changesPanel" aria-selected="false" tabindex="-1" type="button">Changes</button>
        <button class="view-tab" id="tabWorktrees" role="tab" aria-controls="worktreesPanel" aria-selected="false" tabindex="-1" type="button">Worktrees</button>
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

      <div class="conversation" id="timeline" role="tabpanel" aria-labelledby="tabChat" tabindex="0"></div>
      <section class="workflow-panel" id="planPanel" role="tabpanel" aria-labelledby="tabPlan" tabindex="0" hidden>
        <div class="panel-header"><div><h2>Task plan</h2><p class="panel-description">Steps, follow-ups, and delegated work.</p></div><div class="workflow-row"><select id="planMode" aria-label="Task execution mode"><option value="default">Build</option><option value="plan">Plan only (read-only)</option></select><button id="refreshPlan" type="button">Refresh</button></div></div>
        <section class="section-card"><h3>Steps</h3><p class="hint" id="planStatus" role="status">Select a task to edit its plan.</p>
        <form id="planForm" class="workflow-row"><input id="planNewStep" aria-label="New plan step" placeholder="Add a step to this task" required><button type="submit">Add step</button></form>
        <ol id="planSteps" class="plan-steps"></ol></section>
        <section class="section-card"><h3>Queued follow-ups</h3><div id="queuedMessages"></div></section>
        <section class="section-card"><h3>Workers</h3><p class="hint" id="workerStatus">Select a task to inspect its workers.</p><div id="taskWorkers" aria-label="Task workers"></div></section>
      </section>
      <section class="workflow-panel" id="changesPanel" role="tabpanel" aria-labelledby="tabChanges" tabindex="0" hidden>
        <div class="panel-header"><div><h2>Changes</h2><p class="panel-description">Review diffs, stage changes, and leave feedback.</p></div><button id="analyzeReview" type="button">Independent review</button></div>
        <div class="workflow-row section-card"><select id="reviewScope" aria-label="Review scope"><option value="unstaged">Unstaged</option><option value="staged">Staged</option><option value="branch">Branch</option><option value="last-turn">Last turn</option></select><input id="reviewBase" aria-label="Base branch or commit" placeholder="Base branch or commit" hidden><button id="refreshReview" type="button">Refresh</button></div>
        <p id="reviewStatus" class="hint" role="status"></p>
        <div id="reviewFiles"></div><div id="reviewAnalysis" class="workflow-card" hidden></div>
      </section>
      <section class="workflow-panel" id="worktreesPanel" role="tabpanel" aria-labelledby="tabWorktrees" tabindex="0" hidden>
        <div class="panel-header"><div><h2>Worktrees</h2><p class="panel-description">Give a task its own Git checkout.</p></div><button id="refreshWorktrees" type="button">Refresh</button></div>
        <form id="worktreeForm" class="section-card stack">
          <h3>Create a worktree</h3>
          <p class="hint">Requires a Git repository in the current workspace.</p>
          <div class="field-grid"><label>Starting reference<input id="worktreeRef" value="HEAD" spellcheck="false" required></label><label>Branch name<input id="worktreeBranch" placeholder="Optional" spellcheck="false"></label></div>
          <label class="checkbox-label"><input id="worktreeInclude" type="checkbox"> Include uncommitted changes</label>
          <label class="checkbox-label"><input id="worktreeAssociate" type="checkbox"> Move the selected task to this worktree</label>
          <div class="row"><button class="primary" type="submit">Create worktree</button></div>
        </form>
        <div id="worktreeList"></div>
        <div id="transferPanel" class="workflow-card" hidden><h3>Transfer to the original checkout</h3><p id="transferTarget" class="hint"></p><pre id="transferPatch" class="diff-patch"></pre><button id="applyTransfer" type="button">Apply reviewed changes</button><button id="closeTransfer" type="button">Close</button></div>
      </section>

      <div class="composer-zone">
        <div id="permissions"></div>
        <div id="notice" class="notice" role="status"></div>
        <form id="runForm" class="composer">
          <textarea id="runMessage" aria-label="Message Libre Claw" required rows="1" placeholder="Describe what you want Libre Claw to do"></textarea>
          <div class="composer-controls">
            <select id="runProvider" aria-label="Provider">
              <option value="">default provider</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI API</option>
              <option value="openrouter">OpenRouter</option>
              <option value="deepseek">DeepSeek</option>
              <option value="moonshot">Kimi Code / Moonshot</option>
              <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
              <option value="codex">OpenAI Codex</option>
            </select>
            <input id="runModel" placeholder="default model" aria-label="Model">
            <span id="sessionModel" class="session-model" hidden></span>
            <select id="runMode" aria-label="New task mode"><option value="default">Build</option><option value="plan">Plan only</option></select>
            <select id="runWorktree" aria-label="Task workspace"><option value="">Current project</option></select>
            <select id="messageAction" aria-label="Message delivery" hidden><option value="message">Send reply</option><option value="steer">Steer active task</option><option value="queue">Queue follow-up</option></select>
            <span class="spacer"></span>
            <button class="send-btn" id="sendMessage" type="submit" aria-label="Start task" title="Start task">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
            </button>
          </div>
        </form>
        <div class="status-strip" id="statusStrip">
          <span id="stripMeta">Ready when you are</span>
          <span class="sep">|</span>
          <span id="activeRunsLabel"><span id="activeRuns">0</span> active</span>
          <span class="sep">|</span>
          <span id="usageTokensLabel"><span id="usageTokens">0</span> tokens</span>
          <span class="keyboard-hint">Enter to send · Shift Enter for a new line</span>
        </div>
      </div>
    </main>
  </div>

  <div class="overlay" id="settingsOverlay" role="dialog" aria-modal="true" aria-label="Settings" hidden>
    <div class="mask" id="settingsMask"></div>
    <div class="settings-panel">
      <nav class="settings-nav" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
        <h2>Settings</h2>
        <button class="active" id="settingsTabGeneral" data-pane="general" role="tab" aria-controls="paneGeneral" aria-selected="true" type="button">General</button>
        <button id="settingsTabModels" data-pane="models" role="tab" aria-controls="paneModels" aria-selected="false" tabindex="-1" type="button">Models</button>
        <button id="settingsTabSchedules" data-pane="schedules" role="tab" aria-controls="paneSchedules" aria-selected="false" tabindex="-1" type="button">Schedules</button>
        <button id="settingsTabUsage" data-pane="usage" role="tab" aria-controls="paneUsage" aria-selected="false" tabindex="-1" type="button">Usage</button>
        <button id="settingsTabAbout" data-pane="about" role="tab" aria-controls="paneAbout" aria-selected="false" tabindex="-1" type="button">About</button>
      </nav>
      <div class="settings-content">
        <div class="settings-head">
          <span class="tiny">Workspace settings</span>
          <button class="icon-btn" id="closeSettings" type="button" aria-label="Close settings">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div id="settingsNotice" class="settings-notice" role="status" hidden></div>
        <div class="settings-body" id="paneGeneral" role="tabpanel" aria-labelledby="settingsTabGeneral" tabindex="0">
          <div class="panel-header"><div><h3>Make it yours</h3><p class="panel-description">Appearance and connection status.</p></div></div>
          <div class="setting-row section-card">
            <div class="copy">
              <strong>Theme</strong>
              <small>Saved for this browser and your local daemon.</small>
            </div>
            <select id="themeSelect" aria-label="Dashboard theme">
              __LIBRE_CLAW_THEME_OPTIONS__
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
          <p class="hint">Choose where your next task runs.</p>
          <div class="model-route-card">
            <span class="eyebrow">Current default</span>
            <strong id="modelRoute">Loading your model…</strong>
            <span class="hint" id="modelCurrent" role="status"></span>
          </div>
          <form id="modelForm" class="stack">
            <div class="grid-2">
              <label>Provider<select id="configProvider">
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI API</option>
                <option value="openrouter">OpenRouter</option>
                <option value="deepseek">DeepSeek</option>
                <option value="moonshot">Kimi Code / Moonshot</option>
                <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
                <option value="codex">OpenAI Codex</option>
              </select></label>
              <label>Model<input id="configModel" placeholder="Select or enter a model ID" required spellcheck="false" autocomplete="off"></label>
            </div>
            <p class="hint" id="providerRouteHint"></p>
            <div class="model-discovery-row">
              <span id="modelDiscoveryStatus" class="hint" role="status" aria-live="polite">Models load from your provider.</span>
              <button id="refreshModels" type="button">Refresh models</button>
            </div>
            <div class="row end">
              <button class="primary" type="submit">Save default</button>
            </div>
          </form>
          <section id="llamacppSettings" class="endpoint-settings" hidden aria-label="llama.cpp connection">
          <div class="setting-row">
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
          </section>
        </div>
        <div class="settings-body" id="paneSchedules" role="tabpanel" aria-labelledby="settingsTabSchedules" tabindex="0" hidden>
          <div class="panel-header"><div><h3>Schedules</h3><p class="panel-description">Keep recurring work on track.</p></div></div>
          <form id="automationForm" class="stack section-card">
            <h4 id="automationFormTitle">Create schedule</h4>
            <div class="grid-2">
              <label>Name<input id="automationName" placeholder="Daily project review" required></label>
              <label>Schedule<input id="automationSchedule" placeholder="every 30 minutes" required></label>
            </div>
            <label>Instructions<textarea id="automationPrompt" placeholder="Review the project and summarize what needs attention." rows="3" required></textarea></label>
            <div class="grid-2">
              <label>Deliver to<select id="automationRoute"><option value="report">Saved report</option><option value="telegram">Telegram</option><option value="tui">Terminal</option></select></label>
              <label id="automationChatField" hidden>Telegram chat ID<input id="automationChat" inputmode="numeric" placeholder="Use configured chat" disabled></label>
            </div>
            <div class="grid-2">
              <label>Status<select id="automationStatus"><option value="active">Active</option><option value="paused">Paused</option></select></label>
              <label>Provider<select id="automationProvider">
                <option value="">default</option>
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI API</option>
                <option value="openrouter">OpenRouter</option>
                <option value="deepseek">DeepSeek</option>
                <option value="moonshot">Kimi Code / Moonshot</option>
                <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
                <option value="codex">OpenAI Codex</option>
              </select></label>
            </div>
            <label>Model<input id="automationModel" placeholder="default"></label>
            <div class="row">
              <button id="automationSubmit" class="primary" type="submit">Create schedule</button>
              <button id="cancelAutomationEdit" type="button" hidden>Cancel edit</button>
            </div>
          </form>
          <div class="panel-header"><h4>Your schedules</h4><button id="refreshSchedules" type="button">Refresh</button></div>
          <div id="automations" class="automation-list"></div>
        </div>
        <div class="settings-body" id="paneUsage" role="tabpanel" aria-labelledby="settingsTabUsage" tabindex="0" hidden>
          <div class="panel-header"><div><h3>Usage</h3><p class="panel-description">Tokens and cost across recorded runs.</p></div><button id="refreshUsagePane" type="button">Refresh</button></div>
          <p id="usagePaneStatus" class="hint" role="status"></p>
          <div class="metric-grid">
            <div class="metric"><span>Total tokens</span><strong id="usagePaneTokens">0</strong><small id="usagePaneTokensExact">0</small></div>
            <div class="metric"><span>Requests</span><strong id="usagePaneRequests">0</strong><small id="usagePaneRuns">0 runs</small></div>
            <div class="metric"><span>Cost</span><strong id="usagePaneCost">—</strong><small>When reported by the provider</small></div>
          </div>
          <p class="usage-sub">By model</p>
          <div class="usage-table-wrap"><table class="usage-table" id="usageByModel" aria-label="Usage by model"></table></div>
          <p class="usage-sub">Recent runs</p>
          <div class="usage-table-wrap"><table class="usage-table" id="usageRecent" aria-label="Usage for recent runs"></table></div>
        </div>
        <div class="settings-body" id="paneAbout" role="tabpanel" aria-labelledby="settingsTabAbout" tabindex="0" hidden>
          <div class="about-brand"><span class="logo-wrap" aria-hidden="true"><img src="/assets/lobster-icon.svg" width="26" height="26" alt=""></span><div><h3>Libre Claw</h3><p class="panel-description">Your workspace. Your models. Your agent.</p></div></div>
          <section class="section-card"><h4>Built to run locally</h4><p class="hint">Manage tasks, review code, and control what your agent can do from one workspace.</p></section>
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
    const state = { selectedRunId: "", runs: [], events: [], editingAutomationId: "", view: "chat", streaming: false, composing: true, sending: false };
    const $ = (id) => document.getElementById(id);
    const THEME_KEY = "libre-claw-dashboard-theme";
    const RAIL_KEY = "libre-claw-dashboard-rail";
    const THEMES = new Set(__LIBRE_CLAW_THEME_IDS__);
    const THEME_ALIASES = new Map(Object.entries(__LIBRE_CLAW_THEME_ALIASES__));

    function applyTheme(value) {
      const raw = String(value || "").trim().toLowerCase();
      const normalized = THEME_ALIASES.get(raw) || raw;
      const theme = THEMES.has(normalized) ? normalized : "libre";
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem(THEME_KEY, theme); } catch { /* Keep the theme usable without storage. */ }
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
      applyTheme(document.documentElement.dataset.theme || "libre");
      $("themeSelect").addEventListener("change", (event) => {
        void saveTheme(event.target.value);
      });
    }

    function initRail() {
      if (localStorage.getItem(RAIL_KEY) === "1") $("appFrame").classList.add("rail");
      const updateRailLabel = () => {
        const collapsed = $("appFrame").classList.contains("rail");
        $("railToggle").setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
        $("railToggle").setAttribute("aria-expanded", String(!collapsed));
      };
      updateRailLabel();
      $("railToggle").addEventListener("click", () => {
        const rail = $("appFrame").classList.toggle("rail");
        localStorage.setItem(RAIL_KEY, rail ? "1" : "0");
        updateRailLabel();
      });
      $("brandHome").addEventListener("click", () => {
        if (mobileSidebarQuery.matches) { newSession(); return; }
        if ($("appFrame").classList.contains("rail")) {
          $("appFrame").classList.remove("rail");
          localStorage.setItem(RAIL_KEY, "0");
          updateRailLabel();
          return;
        }
        newSession();
      });
    }

    const mobileSidebarQuery = window.matchMedia("(max-width: 760px)");
    let mobileSidebarReturnFocus = null;

    function syncMobileSidebar() {
      const open = mobileSidebarQuery.matches && $("appFrame").classList.contains("mobile-open");
      $("taskSidebar").inert = mobileSidebarQuery.matches && !open;
      $("mainContent").inert = open;
      $("sidebarBackdrop").hidden = !open;
      $("mobileTasks").setAttribute("aria-expanded", String(open));
      if (open) {
        $("taskSidebar").setAttribute("role", "dialog"); $("taskSidebar").setAttribute("aria-modal", "true");
      } else {
        $("taskSidebar").removeAttribute("role"); $("taskSidebar").removeAttribute("aria-modal");
      }
    }

    function openMobileSidebar() {
      if (!mobileSidebarQuery.matches) return;
      mobileSidebarReturnFocus = document.activeElement;
      $("appFrame").classList.add("mobile-open"); syncMobileSidebar();
      $("runSearch").focus();
    }

    function closeMobileSidebar(restoreFocus = true) {
      const wasOpen = $("appFrame").classList.contains("mobile-open");
      $("appFrame").classList.remove("mobile-open"); syncMobileSidebar();
      if (wasOpen && restoreFocus && mobileSidebarReturnFocus?.isConnected) mobileSidebarReturnFocus.focus();
      mobileSidebarReturnFocus = null;
    }

    function handleMobileSidebarKeydown(event) {
      if (!mobileSidebarQuery.matches || !$("appFrame").classList.contains("mobile-open")) return;
      if (event.key === "Escape") { event.preventDefault(); closeMobileSidebar(); return; }
      if (event.key !== "Tab") return;
      const controls = [...$("taskSidebar").querySelectorAll('button, input, select, [tabindex]')]
        .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (!first) return;
      if (event.shiftKey && (document.activeElement === first || !$("taskSidebar").contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }

    function initMobileSidebar() {
      syncMobileSidebar();
      $("mobileTasks").addEventListener("click", openMobileSidebar);
      $("closeMobileTasks").addEventListener("click", () => closeMobileSidebar());
      $("sidebarBackdrop").addEventListener("click", () => closeMobileSidebar());
      document.addEventListener("keydown", handleMobileSidebarKeydown);
      mobileSidebarQuery.addEventListener("change", () => {
        const sidebarFocused = $("taskSidebar").contains(document.activeElement);
        closeMobileSidebar(false);
        if (mobileSidebarQuery.matches && sidebarFocused) $("mobileTasks").focus();
        else if (!mobileSidebarQuery.matches && [$("closeMobileTasks"), $("mobileTasks")].includes(document.activeElement)) $("brandHome").focus();
      });
    }

    let noticeTimer = 0;
    function setNotice(text, error = false) {
      const box = $("notice");
      box.textContent = text;
      box.className = `notice visible ${error ? "error" : ""}`;
      const settingsNotice = $("settingsNotice");
      if (!$("settingsOverlay").hidden) {
        settingsNotice.textContent = text;
        settingsNotice.className = `settings-notice ${error ? "error" : ""}`;
        settingsNotice.hidden = false;
      }
      window.clearTimeout(noticeTimer);
      if (!error) noticeTimer = window.setTimeout(() => { box.className = "notice"; settingsNotice.hidden = true; }, 6000);
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

    function formatCost(value, digits = 4) {
      if (value == null || value === "" || !Number.isFinite(Number(value))) return "unknown";
      return `$${Number(value).toFixed(digits)}`;
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
      const query = $("runSearch").value.trim().toLowerCase();
      const stateFilter = $("runStateFilter").value;
      const signature = JSON.stringify([state.selectedRunId, state.runs, query, stateFilter, Math.floor(Date.now() / 60000)]);
      if (signature === state.runSignature) return;
      state.runSignature = signature;
      container.replaceChildren();
      $("runCount").textContent = `${state.runs.length} ${state.runs.length === 1 ? "run" : "runs"}`;
      if (!state.runs.length) {
        container.append(empty("No tasks yet", "Your recent work will appear here."));
        state.selectedRunId = "";
        clearSelectedRun();
        return;
      }
      const filtered = state.runs.filter((run) => {
        const haystack = `${run.title || ""} ${run.run_id || ""} ${run.provider || ""} ${run.model || ""}`.toLowerCase();
        return (!query || haystack.includes(query)) && (!stateFilter || run.state === stateFilter);
      });
      if (!filtered.length) {
        container.append(empty("No matches", "Try another search or status."));
        return;
      }
      for (const run of filtered) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `run-item ${run.run_id === state.selectedRunId ? "active" : ""}`;
        button.setAttribute("aria-current", run.run_id === state.selectedRunId ? "true" : "false");
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
      if (!runId) return;
      closeMobileSidebar();
      const changed = runId !== state.selectedRunId;
      state.selectedRunId = runId;
      state.composing = false;
      renderRuns();
      if (changed) {
        state.events = []; state.streaming = false; window.clearTimeout(streamTimer); resetStreamNode();
        $("timeline").replaceChildren(empty("Loading task…"));
      }
      try {
        await refreshRunDetail();
        if (state.view === "changes") void loadReview();
      } catch (error) {
        if (runId === state.selectedRunId) $("timeline").replaceChildren(empty("Could not load this task", error.message || String(error)));
        setNotice(error.message || String(error), true);
      }
    }

    function newSession() {
      closeMobileSidebar(false);
      state.selectedRunId = "";
      state.composing = true;
      state.streaming = false;
      window.clearTimeout(streamTimer);
      resetStreamNode();
      clearSelectedRun();
      renderRuns();
      setView("chat");
      $("runWorktree").value = "";
      $("runMessage").focus();
    }

    function clearSelectedRun() {
      state.selectedRunState = "";
      syncComposerMode();
      $("selectedTitle").textContent = "New task";
      $("selectedState").textContent = "idle";
      $("selectedState").className = "pill";
      $("cancelRun").disabled = true;
      $("stripMeta").textContent = "Ready when you are";
      state.events = [];
      renderEvents();
      renderPermissions([]);
    }

    async function refreshRunDetail() {
      const runId = state.selectedRunId;
      if (!runId) return;
      const detail = await request(`/runs/${runId}`);
      if (runId !== state.selectedRunId) return;
      const run = detail.run;
      $("selectedTitle").textContent = run.title || "Untitled run";
      $("selectedTitle").title = `${run.run_id} | updated ${formatTime(run.updated_at)}`;
      $("selectedState").textContent = run.state;
      $("selectedState").className = `pill ${run.state}`;
      state.selectedRunState = run.state;
      state.selectedProvider = run.provider || ""; state.selectedModel = run.model || "";
      $("cancelRun").disabled = !["queued", "running", "blocked"].includes(run.state);
      syncComposerMode();
      $("stripMeta").textContent = `${run.run_id} | ${run.provider}:${run.model}`;
      const events = await request(`/runs/${runId}/events?after=0`);
      if (runId !== state.selectedRunId) return;
      state.events = events.events || [];
      scheduleStream(run.state);
      renderEvents();
      renderPermissions(detail.pending_permissions || []);
      if (state.view === "plan") void loadPlan();
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
      for (const [name, id] of [["plan", "Plan"], ["changes", "Changes"], ["worktrees", "Worktrees"]]) {
        $("tab" + id).classList.toggle("active", view === name);
        $("tab" + id).setAttribute("aria-selected", String(view === name));
        $(name === "changes" ? "changesPanel" : name === "plan" ? "planPanel" : "worktreesPanel").hidden = view !== name;
      }
      $("timeline").hidden = !["chat", "trajectory"].includes(view);
      $("timeline").setAttribute("aria-labelledby", view === "trajectory" ? "tabTrajectory" : "tabChat");
      $("timeline").classList.toggle("trajectory", view === "trajectory");
      if (view === "plan") void loadPlan();
      if (view === "changes") void loadReview();
      if (view === "worktrees") void loadWorktrees();
      $("tabChat").classList.toggle("active", view === "chat");
      $("tabChat").setAttribute("aria-selected", String(view === "chat"));
      $("tabTrajectory").classList.toggle("active", view === "trajectory");
      $("tabTrajectory").setAttribute("aria-selected", String(view === "trajectory"));
      document.querySelectorAll(".view-tab").forEach((tab) => { tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1; });
      $("eventFilter").hidden = view !== "trajectory";
      renderEvents();
    }

    function renderEvents() {
      const container = $("timeline");
      const stick = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
      container.replaceChildren();
      if (!state.events.length) {
        $("eventCount").textContent = "0 events";
        if (state.selectedRunId) container.append(empty("Waiting for activity", "Messages and tool activity will appear as this task runs."));
        else if (state.view === "trajectory") container.append(empty("The full picture", "Select a task to inspect its messages, tools, and approvals."));
        else container.append(welcomeState());
        return;
      }
      const displayEvents = coalescedEvents(state.events);
      if (state.view === "chat") {
        renderChat(container, displayEvents);
        $("eventCount").textContent = `${state.events.length} events`;
        if (!container.childElementCount) container.append(empty("Task started", "Libre Claw is preparing a response."));
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
        const cost = data.cost_usd ?? data.cost;
        return `input: ${formatExactNumber(input)}\noutput: ${formatExactNumber(output)}\ncost: ${formatCost(cost, 6)}`;
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
          button.addEventListener("click", async () => {
            row.querySelectorAll("button").forEach(control => { control.disabled = true; });
            try { await resolvePermission(id, resolution); }
            catch (error) { setNotice(error.message || String(error), true); }
            finally { row.querySelectorAll("button").forEach(control => { control.disabled = false; }); }
          });
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
      const automations = payload.automations || [], signature = JSON.stringify(automations);
      if (signature === state.automationSignature) return;
      state.automationSignature = signature;
      const container = $("automations");
      container.replaceChildren();
      if (!automations.length) {
        container.append(empty("Nothing scheduled", "Create a schedule above to run recurring work."));
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
        const next = formatAutomationNext(automation);
        meta.textContent = `${automation.schedule} · ${automation.route} · ${model}${next ? ` · Next ${next}` : ""}`;
        const prompt = document.createElement("div");
        prompt.className = "tiny";
        prompt.textContent = truncate(automation.prompt || "", 180);
        const row = document.createElement("div");
        row.className = "row end";
        const runNow = document.createElement("button");
        runNow.type = "button";
        runNow.textContent = "Run now";
        runNow.className = "primary";
        runNow.addEventListener("click", () => { void runAutomationNow(automation.automation_id, runNow).catch(error => setNotice(error.message || String(error), true)); });
        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "Edit";
        edit.addEventListener("click", () => editAutomation(automation));
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = automation.status === "active" ? "Pause" : "Resume";
        toggle.addEventListener("click", async () => {
          toggle.disabled = true;
          try { await toggleAutomation(automation); } catch (error) { setNotice(error.message || String(error), true); }
          finally { toggle.disabled = false; }
        });
        const del = document.createElement("button");
        del.type = "button";
        del.textContent = "Delete";
        del.className = "danger";
        del.addEventListener("click", async () => {
          del.disabled = true;
          try { await deleteAutomation(automation.automation_id); } catch (error) { setNotice(error.message || String(error), true); }
          finally { del.disabled = false; }
        });
        row.append(runNow, edit, toggle, del);
        box.append(head, meta, prompt, row);
        container.append(box);
      }
    }

    function editAutomation(automation) {
      state.editingAutomationId = automation.automation_id;
      $("automationFormTitle").textContent = "Edit schedule";
      $("automationSubmit").textContent = "Save changes";
      $("cancelAutomationEdit").hidden = false;
      $("automationName").value = automation.name || "";
      $("automationSchedule").value = automation.schedule || "";
      $("automationPrompt").value = automation.prompt || "";
      $("automationRoute").value = automation.route || "report";
      $("automationChat").value = automation.telegram_chat_id ?? "";
      $("automationStatus").value = automation.status || "active";
      $("automationProvider").value = automation.provider || "";
      $("automationModel").value = automation.model || "";
      syncAutomationRoute();
      openSettingsPane("schedules");
      $("automationName").focus();
      $("automationForm").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    function resetAutomationForm(form) {
      state.editingAutomationId = "";
      $("automationFormTitle").textContent = "Create schedule";
      $("automationSubmit").textContent = "Create schedule";
      $("cancelAutomationEdit").hidden = true;
      form.reset();
      $("automationStatus").value = "active";
      syncAutomationRoute();
    }

    function syncAutomationRoute() {
      const telegram = $("automationRoute").value === "telegram";
      $("automationChatField").hidden = !telegram;
      $("automationChat").disabled = !telegram;
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
        telegram_chat_id: $("automationRoute").value === "telegram" ? chat || null : null,
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

    function empty(text, description = "") {
      const node = document.createElement("div");
      node.className = "empty empty-state";
      const title = document.createElement("strong"); title.textContent = text; node.append(title);
      if (description) { const copy = document.createElement("p"); copy.textContent = description; node.append(copy); }
      return node;
    }

    function welcomeState() {
      const node = empty("What are we building?", "Start a task. Keep the work, plan, and changes together.");
      node.classList.add("welcome-state");
      const mark = document.createElement("div"); mark.className = "welcome-wordmark";
      mark.append($("wordmarkTemplate").content.cloneNode(true)); node.prepend(mark);
      const actions = document.createElement("div"); actions.className = "starter-grid";
      for (const [label, prompt] of [["Explore the project", "Explore this project and explain its structure and main entry points."], ["Find a bug", "Review this project for a concrete bug, explain it, and propose a focused fix."], ["Plan a change", "Help me plan a change to this project: "]]) {
        const button = document.createElement("button"); button.type = "button"; button.className = "starter-action"; button.textContent = label;
        button.addEventListener("click", () => { $("runMessage").value = prompt; autoGrow(); $("runMessage").focus(); });
        actions.append(button);
      }
      node.append(actions); return node;
    }

    /* Settings modal */
    const PANES = ["general", "models", "schedules", "usage", "about"];
    let settingsReturnFocus = null;
    function openSettingsPane(pane) {
      if (!PANES.includes(pane)) return;
      closeMobileSidebar();
      const overlay = $("settingsOverlay"), wasOpen = !overlay.hidden;
      if (!wasOpen) { settingsReturnFocus = document.activeElement; $("settingsNotice").hidden = true; }
      overlay.hidden = false; overlay.classList.add("open");
      $("appFrame").inert = true;
      for (const id of PANES) {
        const active = id === pane;
        const panel = $(`pane${id[0].toUpperCase()}${id.slice(1)}`);
        panel.hidden = !active;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", `settingsTab${id[0].toUpperCase()}${id.slice(1)}`);
      }
      document.querySelectorAll(".settings-nav button").forEach((button) => {
        const active = button.dataset.pane === pane;
        button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
        if (active && !wasOpen) button.focus();
      });
      if (pane === "models") {
        void loadModelConfig();
        void loadLlamacppConfig();
      }
      if (pane === "schedules") void refreshAutomations().catch(error => setNotice(error.message || String(error), true));
      if (pane === "usage") void loadUsagePane();
    }
    function closeSettingsPanel() {
      if ($("settingsOverlay").hidden) return;
      $("settingsOverlay").classList.remove("open"); $("settingsOverlay").hidden = true;
      $("appFrame").inert = false;
      if (settingsReturnFocus?.isConnected) settingsReturnFocus.focus();
      settingsReturnFocus = null;
    }

    function handleSettingsKeydown(event) {
      if ($("settingsOverlay").hidden) return;
      if (event.key === "Escape") { event.preventDefault(); closeSettingsPanel(); return; }
      if (event.key !== "Tab") return;
      const controls = [...$("settingsOverlay").querySelectorAll('button, [href], input, select, textarea, [tabindex]')]
        .filter(element => !element.disabled && element.tabIndex >= 0 && !element.closest("[hidden]") && element.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (!first) return;
      if (event.shiftKey && (document.activeElement === first || !$("settingsOverlay").contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }

    function bindTabNavigation(selector, activate) {
      const tabs = [...document.querySelectorAll(selector)];
      tabs.forEach((tab, index) => tab.addEventListener("keydown", (event) => {
        const moves = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1};
        if (!(event.key in moves) && !["Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + moves[event.key] + tabs.length) % tabs.length;
        activate(tabs[next]); tabs[next].focus();
      }));
    }

    let defaultModelConfig = {provider: "", model: ""};
    let modelConfigGeneration = 0;
    const modelPickers = new Map();

    function providerLabel(provider) {
      return Array.from($("configProvider").options).find((option) => option.value === provider)?.textContent || provider;
    }

    function syncProviderSettings() {
      const provider = $("configProvider").value;
      $("llamacppSettings").hidden = provider !== "llamacpp";
      $("providerRouteHint").textContent = provider === "deepseek"
        ? "Connects through your DeepSeek API configuration. Model IDs come directly from DeepSeek."
        : `Models are discovered from your ${providerLabel(provider)} connection. You can also enter a model ID.`;
    }

    function applyDefaultModel(payload, updateForm = false) {
      defaultModelConfig = {provider: payload.provider || "", model: payload.model || ""};
      const label = providerLabel(defaultModelConfig.provider);
      $("modelRoute").textContent = [label, defaultModelConfig.model].filter(Boolean).join(" / ") || "No default selected";
      $("modelCurrent").textContent = "Used for new tasks and schedules that follow the default.";
      for (const [selectId, inputId] of [["runProvider", "runModel"], ["automationProvider", "automationModel"]]) {
        const select = $(selectId);
        const option = Array.from(select.options).find((item) => item.value === "");
        if (option) option.textContent = label ? `Default · ${label}` : "Default provider";
        modelPickers.get(inputId)?.refresh();
      }
      if (updateForm) {
        $("configProvider").value = defaultModelConfig.provider;
        $("configModel").value = defaultModelConfig.model;
        syncProviderSettings();
        modelPickers.get("configModel")?.refresh();
      }
    }

    async function loadModelConfig() {
      const generation = ++modelConfigGeneration;
      try {
        const payload = await request("/config/model");
        if (generation !== modelConfigGeneration) return;
        applyDefaultModel(payload, true);
      } catch (error) {
        if (generation !== modelConfigGeneration) return;
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
          $("configProvider").dispatchEvent(new Event("change"));
          $("configModel").value = item.model;
          $("configModel").dispatchEvent(new Event("input"));
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
        document.querySelectorAll("input[data-model-provider=llamacpp]").forEach((input) => input.dispatchEvent(new Event("focus")));
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
        th.scope = "col";
        th.textContent = header;
        headRow.append(th);
      }
      thead.append(headRow);
      table.append(thead);
      const tbody = document.createElement("tbody");
      if (!rows.length) {
        const row = document.createElement("tr"), cell = document.createElement("td");
        cell.colSpan = headers.length; cell.className = "table-empty"; cell.textContent = "No usage recorded yet.";
        row.append(cell); tbody.append(row);
      }
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
      $("usagePaneStatus").textContent = "Loading usage…";
      $("refreshUsagePane").disabled = true;
      try {
        const payload = await request("/usage?limit=250");
        const summary = payload.summary || {};
        $("usagePaneTokens").textContent = formatCompactNumber(summary.total_tokens);
        $("usagePaneTokensExact").textContent = `${formatExactNumber(summary.total_tokens)} total`;
        $("usagePaneRequests").textContent = formatExactNumber(summary.requests);
        $("usagePaneRuns").textContent = `${formatExactNumber(summary.runs)} runs`;
        $("usagePaneCost").textContent = formatCost(summary.cost);
        usageTable(
          $("usageByModel"),
          ["Model", "Requests", "Input", "Output", "Total", "Cost"],
          (summary.by_model || []).map((group) => [
            group.name || "unknown",
            formatExactNumber(group.requests),
            formatCompactNumber(group.input_tokens),
            formatCompactNumber(group.output_tokens),
            formatCompactNumber(group.total_tokens),
            formatCost(group.cost),
          ]),
        );
        usageTable(
          $("usageRecent"),
          ["Run", "Model", "Tokens", "Cost", "When"],
          (payload.records || []).slice(0, 12).map((record) => [
            record.title || record.run_id,
            `${record.provider}:${record.model}`,
            formatCompactNumber(record.total_tokens),
            formatCost(record.cost),
            formatShortTime(record.timestamp),
          ]),
        );
        $("usagePaneStatus").textContent = "Usage from the latest 250 runs.";
      } catch (error) {
        $("usagePaneStatus").textContent = `Could not load usage: ${error.message || error}`;
        setNotice(String(error.message || error), true);
      } finally { $("refreshUsagePane").disabled = false; }
    }

    $("modelForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const generation = ++modelConfigGeneration;
      const button = $("modelForm").querySelector("button[type=submit]");
      button.disabled = true;
      $("modelCurrent").textContent = "Saving default…";
      try {
        const payload = await request("/config/model", {
          method: "PATCH",
          body: JSON.stringify({
            provider: $("configProvider").value,
            model: $("configModel").value,
            persist_global: true,
          }),
        });
        applyDefaultModel(payload, generation === modelConfigGeneration);
        $("modelCurrent").textContent = "Default saved. Ready for your next task.";
        setNotice(`Default model saved: ${payload.provider}:${payload.model}`);
      } catch (error) {
        $("modelCurrent").textContent = String(error.message || error);
        setNotice(String(error.message || error), true);
      } finally {
        button.disabled = false;
      }
    });

    function modelCapabilitySummary(item) {
      if (!item) return "Capabilities not published for this model.";
      const parts = [];
      let missing = false;
      for (const [key, label] of [["supports_tools", "Tools"], ["supports_vision", "Images"], ["supports_reasoning", "Reasoning"]]) {
        if (typeof item[key] === "boolean") parts.push(item[key] ? label : `No ${label.toLowerCase()}`);
        else missing = true;
      }
      for (const [key, label] of [["context_window_tokens", "context"], ["max_completion_tokens", "max output"]]) {
        if (Number.isFinite(item[key]) && item[key] > 0) parts.push(`${formatCompactNumber(item[key])} ${label}`);
        else missing = true;
      }
      const price = (value) => `$${(value * 1000000).toLocaleString(undefined, {maximumFractionDigits: 4})}/M`;
      if (Number.isFinite(item.input_cost_per_token) && Number.isFinite(item.output_cost_per_token)) {
        parts.push(`${price(item.input_cost_per_token)} in / ${price(item.output_cost_per_token)} out`);
      } else missing = true;
      if (!parts.length) return "Capabilities not published by this provider.";
      if (missing) parts.push("Some details unavailable");
      return parts.join(" · ");
    }

    function syncModelDatalist(select, input) {
      const list = document.createElement("datalist");
      list.id = `${input.id}Models`;
      input.after(list);
      input.setAttribute("list", list.id);
      let generation = 0, models = [], discoveredDefault = "";
      const metadata = document.createElement("p"); metadata.className = "model-metadata"; metadata.id = `${input.id}Capabilities`;
      metadata.setAttribute("aria-live", "polite"); input.after(metadata);
      if (!input.getAttribute("aria-label")) input.setAttribute("aria-label", input.id === "automationModel" ? "Schedule model" : "Model");
      input.setAttribute("aria-describedby", [input.getAttribute("aria-describedby"), metadata.id].filter(Boolean).join(" "));
      const effectiveProvider = () => select.value || defaultModelConfig.provider;
      const defaultModel = () => effectiveProvider() === defaultModelConfig.provider ? defaultModelConfig.model : discoveredDefault;
      const updatePlaceholder = () => {
        if (input.id !== "configModel") input.placeholder = defaultModel() ? `Default: ${defaultModel()}` : "Provider default model";
      };
      const showCapabilities = () => {
        const model = input.value.trim() || (input.id === "configModel" ? "" : defaultModel());
        const item = models.find((item) => item.model === model);
        const hasDetails = item && (
          ["supports_tools", "supports_vision", "supports_reasoning"].some((key) => typeof item[key] === "boolean")
          || ["context_window_tokens", "max_completion_tokens"].some((key) => Number.isFinite(item[key]) && item[key] > 0)
          || (Number.isFinite(item.input_cost_per_token) && Number.isFinite(item.output_cost_per_token))
        );
        metadata.hidden = input.hidden || !model || (input.id !== "configModel" && !hasDetails);
        metadata.textContent = model ? modelCapabilitySummary(item) : "";
      };
      input.addEventListener("input", showCapabilities);
      input.addEventListener("change", showCapabilities);
      showCapabilities();
      const update = async (refresh = false) => {
        const requestId = ++generation;
        const provider = effectiveProvider();
        input.dataset.modelProvider = provider;
        list.replaceChildren(); models = []; discoveredDefault = ""; updatePlaceholder(); showCapabilities();
        const isConfig = input.id === "configModel";
        if (isConfig) {
          $("modelDiscoveryStatus").textContent = `Loading models from ${providerLabel(provider)}…`;
          $("refreshModels").disabled = true;
        }
        try {
          const query = new URLSearchParams();
          if (provider) query.set("provider", provider);
          if (refresh) query.set("refresh", "1");
          const payload = await request(`/models${query.size ? `?${query}` : ""}`);
          if (requestId !== generation) return;
          models = payload.models || [];
          discoveredDefault = payload.default_model || "";
          for (const item of models) {
            const option = document.createElement("option");
            option.value = item.model;
            option.label = item.label;
            list.append(option);
          }
          updatePlaceholder(); showCapabilities();
          input.title = payload.error
            ? "Discovery unavailable. You can still enter a model ID."
            : "Choose a discovered model or enter any model ID.";
          if (isConfig) {
            $("modelDiscoveryStatus").textContent = payload.error
              ? "Discovery unavailable. Check your provider connection or enter a model ID."
              : `${models.length} ${models.length === 1 ? "model" : "models"} ${payload.source === "configured" ? "from configuration" : "available"} · Manual IDs supported`;
          }
        } catch (_error) {
          if (requestId === generation) {
            input.title = "Discovery unavailable. Enter a model ID.";
            if (isConfig) $("modelDiscoveryStatus").textContent = "Discovery unavailable. Check your provider connection or enter a model ID.";
          }
        } finally {
          if (isConfig && requestId === generation) $("refreshModels").disabled = false;
        }
      };
      select.addEventListener("change", () => {
        input.value = "";
        if (input.id === "configModel") {
          ++modelConfigGeneration;
          syncProviderSettings();
        }
        void update();
      });
      input.addEventListener("focus", () => { void update(); });
      modelPickers.set(input.id, {refresh: update});
    }

    syncModelDatalist($("runProvider"), $("runModel"));
    syncModelDatalist($("configProvider"), $("configModel"));
    syncModelDatalist($("automationProvider"), $("automationModel"));
    $("configModel").addEventListener("input", () => { ++modelConfigGeneration; });
    $("refreshModels").addEventListener("click", () => { void modelPickers.get("configModel").refresh(true); });
    void loadModelConfig();

    const workflow = { review: null, reviewContext: {}, comments: [], reviewGeneration: 0, planGeneration: 0, planSignature: "", workerSignature: "", workers: [], workerDrafts: new Map(), workerPending: new Set(), worktrees: [], transfer: null };

    function workflowButton(label, action, className = "") {
      const button = document.createElement("button");
      button.type = "button"; button.textContent = label; button.className = className;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try { await action(); } catch (error) { setNotice(error.message || String(error), true); }
        finally { button.disabled = false; }
      });
      return button;
    }

    function reviewContext() {
      const context = { scope: $("reviewScope").value };
      if (state.selectedRunId) context.run_id = state.selectedRunId;
      if (context.scope === "branch" && $("reviewBase").value.trim()) context.base_ref = $("reviewBase").value.trim();
      return context;
    }

    async function loadReview() {
      const generation = ++workflow.reviewGeneration;
      const context = reviewContext();
      workflow.review = null;
      $("reviewFiles").replaceChildren();
      $("reviewAnalysis").hidden = true;
      $("analyzeReview").disabled = true;
      $("reviewStatus").textContent = "Loading changes…";
      try {
        const [snapshot, feedback] = await Promise.all([
          request(`/workspace/review?${new URLSearchParams(context)}`),
          request(`/workspace/review/comments?${new URLSearchParams(context)}`),
        ]);
        if (generation !== workflow.reviewGeneration) return;
        workflow.review = snapshot; workflow.reviewContext = context; workflow.comments = feedback.comments || [];
        $("reviewStatus").textContent = `${snapshot.repository} · ${snapshot.files.length} changed ${snapshot.files.length === 1 ? "file" : "files"}`;
        $("analyzeReview").disabled = !snapshot.files.length;
        renderReview();
      } catch (error) {
        if (generation !== workflow.reviewGeneration) return;
        const message = error.message || String(error);
        const needsRepository = /not a git repository/i.test(message);
        $("reviewStatus").textContent = "";
        $("reviewFiles").replaceChildren(empty(
          needsRepository ? "No Git repository in this workspace" : "Unable to load changes",
          needsRepository ? "Choose a task in a Git project to review and stage its changes." : message,
        ));
      }
    }

    function parseDiffLines(patch) {
      let oldLine = 0, newLine = 0, inHunk = false;
      const result = [];
      for (const line of String(patch || "").split("\n")) {
        const match = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
        if (match) { oldLine = Number(match[1]); newLine = Number(match[2]); inHunk = true; continue; }
        if (!inHunk || line.startsWith("\\") || ![" ", "+", "-"].includes(line[0])) continue;
        const kind = line[0] === "+" ? "add" : line[0] === "-" ? "remove" : "context";
        result.push({ kind, text: line, oldLine: kind === "add" ? null : oldLine++, newLine: kind === "remove" ? null : newLine++ });
      }
      return result;
    }

    function reviewActions(file, hunk, snapshot, context) {
      const row = document.createElement("div"); row.className = "workflow-row";
      const mutate = async (action) => {
        if (action === "revert" && !window.confirm(`Revert ${hunk ? "this hunk in" : "changes to"} ${file.path}?`)) return;
        await request("/workspace/review/action", { method: "POST", body: JSON.stringify({
          ...context, action, revision: snapshot.revision, path: file.path, ...(hunk ? {hunk_id: hunk.hunk_id} : {}),
        }) });
        setNotice(`${action === "stage" ? "Staged" : action === "unstage" ? "Unstaged" : "Reverted"} ${file.path}.`);
        await loadReview();
      };
      if (snapshot.scope === "unstaged") {
        row.append(workflowButton(hunk ? "Stage hunk" : "Stage file", () => mutate("stage")));
        row.append(workflowButton(hunk ? "Revert hunk" : "Revert file", () => mutate("revert"), "danger"));
      } else if (snapshot.scope === "staged") row.append(workflowButton(hunk ? "Unstage hunk" : "Unstage file", () => mutate("unstage")));
      return row;
    }

    function commentEditor(container, file, line, side, snapshot, context) {
      container.querySelector(".comment-form")?.remove();
      const form = document.createElement("form"); form.className = "comment-form stack";
      const input = document.createElement("textarea"); input.required = true; input.maxLength = 10000;
      input.setAttribute("aria-label", `Comment on ${file.path} ${side} line ${line}`);
      input.placeholder = `Comment on ${side} line ${line}`;
      const actions = document.createElement("div"); actions.className = "workflow-row";
      const save = document.createElement("button"); save.type = "submit"; save.textContent = "Save comment";
      actions.append(save, workflowButton("Cancel", () => form.remove())); form.append(input, actions);
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); save.disabled = true;
        try {
          const payload = await request("/workspace/review/comments", { method: "POST", body: JSON.stringify({
            ...context, path: file.path, line, side, body: input.value, revision: snapshot.revision,
          }) });
          if (workflow.review === snapshot) { workflow.comments.push(payload.comment); renderReview(); }
          setNotice("Comment saved.");
        } catch (error) { setNotice(error.message || String(error), true); save.disabled = false; }
      });
      container.append(form); input.focus();
    }

    function renderReview() {
      const snapshot = workflow.review, context = workflow.reviewContext;
      const container = $("reviewFiles"); container.replaceChildren();
      if (!snapshot?.files.length) { container.append(empty("All clear", "No file changes in this view.")); return; }
      for (const file of snapshot.files) {
        const card = document.createElement("article"); card.className = "workflow-card";
        const head = document.createElement("div"); head.className = "workflow-row";
        const title = document.createElement("h3"); title.textContent = file.path; title.style.marginInlineEnd = "auto";
        head.append(title, reviewActions(file, null, snapshot, context)); card.append(head);
        if (file.binary) card.append(empty("Binary file — file actions are available above."));
        else if (!file.hunks.length) { const patch = document.createElement("pre"); patch.className = "diff-patch"; patch.textContent = file.patch; card.append(patch); }
        for (const hunk of file.hunks) {
          const block = document.createElement("section"); block.className = "diff-hunk";
          const toolbar = document.createElement("div"); toolbar.className = "workflow-row";
          const heading = document.createElement("h4"); heading.textContent = hunk.header;
          toolbar.append(heading, reviewActions(file, hunk, snapshot, context)); block.append(toolbar);
          const lines = document.createElement("div"); lines.className = "diff-lines";
          for (const line of parseDiffLines(hunk.patch)) {
            const row = document.createElement("div"); row.className = `diff-line ${line.kind}`;
            for (const [side, number] of [["left", line.oldLine], ["right", line.newLine]]) {
              const numberCell = document.createElement(number === null ? "span" : "button");
              numberCell.className = "line-number"; numberCell.textContent = number ?? "";
              if (number !== null) {
                numberCell.type = "button"; numberCell.setAttribute("aria-label", `Comment on ${file.path} ${side} line ${number}`);
                numberCell.addEventListener("click", () => commentEditor(block, file, number, side, snapshot, context));
              }
              row.append(numberCell);
            }
            const code = document.createElement("code"); code.textContent = line.text; row.append(code); lines.append(row);
            for (const comment of workflow.comments.filter((item) => item.path === file.path && item.revision === snapshot.revision &&
              ((item.side === "left" && item.line === line.oldLine) || (item.side === "right" && item.line === line.newLine)))) {
              const feedback = document.createElement("p"); feedback.className = "inline-comment";
              feedback.textContent = `${comment.side}:${comment.line} · ${comment.body}`; lines.append(feedback);
            }
          }
          block.append(lines); card.append(block);
        }
        container.append(card);
      }
    }

    async function analyzeReview() {
      const snapshot = workflow.review; if (!snapshot) return;
      const button = $("analyzeReview"); button.disabled = true; button.textContent = "Reviewing…";
      try {
        const result = await request("/workspace/review/analyze", { method: "POST", body: JSON.stringify(workflow.reviewContext) });
        if (workflow.review !== snapshot || result.revision !== snapshot.revision) { setNotice("Changes moved during review. Refresh and review again.", true); return; }
        $("reviewAnalysis").replaceChildren(renderMarkdown(result.text)); $("reviewAnalysis").hidden = false;
      } catch (error) { setNotice(error.message || String(error), true); }
      finally { button.disabled = !workflow.review?.files.length; button.textContent = "Independent review"; }
    }

    async function sendTaskControl(action, text, clearComposer = false) {
      const runId = state.selectedRunId;
      if (!runId) { setNotice("Select a task first.", true); return false; }
      const draft = $("runMessage").value;
      try {
        const payload = await request(`/runs/${runId}/control`, { method: "POST", body: JSON.stringify({action, text}) });
        if (clearComposer && runId === state.selectedRunId && $("runMessage").value === draft) { $("runMessage").value = ""; autoGrow(); }
        setNotice(payload.text || "Task updated.");
        if (runId === state.selectedRunId) await loadPlan(true);
        return true;
      } catch (error) { setNotice(error.message || String(error), true); return false; }
    }

    function workerControls(worker) {
      const remaining = (maximum, used) => typeof maximum === "number" && Number.isFinite(maximum) && typeof used === "number" && Number.isFinite(used) ? Math.max(0, maximum - used) : null;
      const tools = remaining(worker.max_tool_calls, worker.tool_calls), seconds = remaining(worker.max_seconds, worker.elapsed_seconds);
      const active = ["running", "blocked"].includes(worker.status), recoverable = ["interrupted", "failed", "cancelled"].includes(worker.status);
      const pending = Boolean(worker.resume_pending);
      return { tools, seconds, active, pending, canResume: recoverable && !pending && tools !== null && tools > 0 && seconds !== null && seconds > 0 };
    }

    async function sendWorkerControl(runId, worker, action, guidance = "") {
      if (runId !== state.selectedRunId) return;
      const key = `${runId}/${worker.id}`;
      if (workflow.workerPending.has(key)) return;
      workflow.workerPending.add(key); renderWorkers(workflow.workers, true);
      try {
        const text = action === "agent_resume" ? `${worker.id} ${guidance.trim()}`.trim() : worker.id;
        const payload = await request(`/runs/${runId}/control`, { method: "POST", body: JSON.stringify({action, text}) });
        if (runId !== state.selectedRunId) return;
        if (action === "agent_resume") { workflow.workerDrafts.delete(key); worker.resume_pending = true; }
        setNotice(payload.text || "Worker updated.");
        if (Array.isArray(payload.subagents)) workflow.workers = payload.subagents.map(item => action === "agent_resume" && item.id === worker.id && !["running", "blocked", "done"].includes(item.status) ? {...item, resume_pending: true} : item);
        await loadPlan(true);
      } catch (error) { setNotice(error.message || String(error), true); }
      finally { workflow.workerPending.delete(key); if (runId === state.selectedRunId) renderWorkers(workflow.workers, true); }
    }

    function renderWorkers(workers, force = false) {
      const runId = state.selectedRunId, signature = JSON.stringify([runId, workers, [...workflow.workerPending]]);
      if (!force && signature === workflow.workerSignature) return;
      workflow.workerSignature = signature; workflow.workers = workers;
      const focused = document.activeElement, focusedKey = focused?.dataset?.workerGuidance;
      const selection = focusedKey ? [focused.selectionStart, focused.selectionEnd] : null;
      const container = $("taskWorkers"); container.replaceChildren();
      $("workerStatus").textContent = workers.length ? "Resume continues a saved worker with its remaining budget." : "No workers for this task.";
      let restoreFocus = null;
      for (const worker of workers) {
        const key = `${runId}/${worker.id}`, controls = workerControls(worker), pending = workflow.workerPending.has(key);
        const card = document.createElement("article"); card.className = "workflow-card"; card.dataset.workerId = worker.id;
        const head = document.createElement("div"); head.className = "workflow-row";
        const title = document.createElement("strong"); title.textContent = worker.task || "Saved worker";
        const status = document.createElement("span"); status.className = "pill"; status.textContent = controls.pending ? "resume pending" : worker.status || "unknown";
        head.append(title, status); card.append(head);
        const meta = document.createElement("dl"); meta.className = "worker-meta";
        const details = [["Worker", worker.id], ["Provider / model", `${worker.provider || "Unknown"} / ${worker.model || "Unknown"}`], ["Scope", worker.scope || "Unknown"], ["Access", worker.read_only ? "Read only" : "Writes within declared paths"], ["Tools remaining", controls.tools === null ? "Unknown" : String(Math.floor(controls.tools))], ["Time remaining", controls.seconds === null ? "Unknown" : `${Math.ceil(controls.seconds)} seconds`]];
        if (worker.write_paths?.length) details.push(["Write paths", worker.write_paths.join(", ")]);
        for (const [label, value] of details) { const term = document.createElement("dt"), detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; meta.append(term, detail); }
        card.append(meta);
        if (worker.error) { const error = document.createElement("p"); error.className = "hint worker-error"; error.textContent = worker.error; card.append(error); }
        if (worker.output) { const result = document.createElement("pre"); result.className = "worker-results"; result.setAttribute("aria-label", `Result from worker ${worker.id}`); result.textContent = worker.output; card.append(result); }
        if (["interrupted", "failed", "cancelled"].includes(worker.status) || controls.pending) {
          const input = document.createElement("textarea"); input.rows = 2; input.placeholder = "Optional guidance before resuming"; input.setAttribute("aria-label", `Resume guidance for ${worker.id}`); input.dataset.workerGuidance = key; input.value = workflow.workerDrafts.get(key) || "";
          input.disabled = pending || controls.pending || !controls.canResume;
          input.addEventListener("input", () => workflow.workerDrafts.set(key, input.value));
          const resume = document.createElement("button"); resume.type = "button"; resume.textContent = controls.pending ? "Resume queued" : pending ? "Requesting resume..." : "Resume worker"; resume.disabled = pending || !controls.canResume;
          resume.addEventListener("click", () => sendWorkerControl(runId, worker, "agent_resume", input.value));
          card.append(input, resume);
          if (!controls.canResume && !controls.pending) { const reason = document.createElement("p"); reason.className = "hint"; reason.textContent = controls.tools === null || controls.seconds === null ? "Saved budget is unavailable." : "This worker has used its tool or time budget."; card.append(reason); }
          if (key === focusedKey && !input.disabled) restoreFocus = input;
        }
        if (controls.active) {
          const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "danger"; cancel.textContent = pending ? "Cancelling..." : "Cancel worker"; cancel.disabled = pending;
          cancel.addEventListener("click", () => sendWorkerControl(runId, worker, "agent_cancel")); card.append(cancel);
        }
        container.append(card);
      }
      if (restoreFocus) { restoreFocus.focus({preventScroll: true}); if (selection) restoreFocus.setSelectionRange(...selection); }
    }

    async function loadPlan(force = false) {
      const runId = state.selectedRunId, generation = ++workflow.planGeneration;
      $("planForm").hidden = !runId; $("planMode").disabled = !runId;
      if (!runId) { workflow.planSignature = ""; $("planStatus").textContent = "Select a task to edit its plan."; $("planSteps").replaceChildren(); $("queuedMessages").replaceChildren(empty("No task selected")); renderWorkers([], true); $("workerStatus").textContent = "Select a task to inspect its workers."; return; }
      try {
        const payload = await request(`/runs/${runId}/session?controls=1`);
        if (runId !== state.selectedRunId || generation !== workflow.planGeneration) return;
        const session = payload.session || {}, queued = payload.queued || [];
        renderWorkers(Array.isArray(payload.subagents) ? payload.subagents : [], force);
        const signature = JSON.stringify([runId, session.mode, session.plan_steps, queued]);
        if (!force && signature === workflow.planSignature) return;
        if (!force && $("planSteps").contains(document.activeElement)) return;
        workflow.planSignature = signature;
        $("planMode").value = session.mode || "default";
        $("planStatus").textContent = session.mode === "plan" ? "Plan-only mode: tool actions are read-only." : "Build mode: tools follow your approval policy.";
        const list = $("planSteps"); list.replaceChildren();
        for (const [index, step] of (session.plan_steps || []).entries()) {
          const item = document.createElement("li"); const row = document.createElement("div"); row.className = "workflow-row";
          const check = document.createElement("input"); check.type = "checkbox"; check.checked = step.status === "done"; check.setAttribute("aria-label", `Complete step ${index + 1}`);
          check.addEventListener("change", () => sendTaskControl("plan", `${check.checked ? "done" : "pending"} ${index + 1}`));
          const input = document.createElement("input"); input.value = step.text; input.setAttribute("aria-label", `Plan step ${index + 1}`);
          row.append(check, input, workflowButton("Save", () => sendTaskControl("plan", `edit ${index + 1} ${input.value}`)));
          item.append(row); list.append(item);
        }
        if (!(session.plan_steps || []).length) { const item = document.createElement("li"); item.className = "plan-empty"; item.append(empty("A clear next step", "Add a step above, or ask Libre Claw to make a plan.")); list.append(item); }
        $("queuedMessages").replaceChildren();
        for (const item of queued) { const row = document.createElement("p"); row.className = "workflow-card"; row.textContent = item.message; $("queuedMessages").append(row); }
        if (!queued.length) $("queuedMessages").append(empty("Queue is clear", "Choose Queue follow-up in the composer to save the next instruction."));
      } catch (error) { if (generation === workflow.planGeneration) $("planStatus").textContent = error.message || String(error); }
    }

    async function loadWorktrees() {
      try {
        const payload = await request("/worktrees"); workflow.worktrees = payload.worktrees || [];
        const picker = $("runWorktree"), selected = picker.value;
        picker.replaceChildren(new Option("Current project", ""));
        for (const record of workflow.worktrees) picker.append(new Option(record.branch || record.worktree_id.slice(0, 8), record.worktree_id));
        picker.value = workflow.worktrees.some((item) => item.worktree_id === selected) ? selected : "";
        const list = $("worktreeList"); list.replaceChildren();
        for (const record of workflow.worktrees) {
          const card = document.createElement("article"); card.className = "workflow-card";
          const heading = document.createElement("h3"); heading.textContent = record.branch || record.worktree_id.slice(0, 12);
          const path = document.createElement("p"); path.className = "hint"; path.textContent = record.path;
          const actions = document.createElement("div"); actions.className = "workflow-row";
          if (record.run_id) actions.append(workflowButton("Open task", async () => { await selectRun(record.run_id); setView("chat"); }));
          actions.append(workflowButton("Use for new task", () => {
            newSession(); $("runWorktree").value = record.worktree_id;
          }), workflowButton("Review transfer", () => previewTransfer(record)), workflowButton("Remove", async () => {
            if (!window.confirm(`Remove worktree ${record.branch || record.worktree_id}? The server will check for work that has not been transferred.`)) return;
            await request(`/worktrees/${record.worktree_id}`, {method:"DELETE"}); await loadWorktrees(); setNotice("Worktree removed.");
          }, "danger"));
          const setup = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = "Setup commands";
          const form = document.createElement("form"); form.className = "stack";
          const commands = document.createElement("textarea"); commands.placeholder = "One shell command per line"; commands.required = true; commands.setAttribute("aria-label", `Setup commands for ${heading.textContent}`);
          const label = document.createElement("label"); const approve = document.createElement("input"); approve.type = "checkbox"; approve.required = true;
          label.append(approve, " Approve these setup commands");
          const submit = document.createElement("button"); submit.type = "submit"; submit.textContent = "Run setup";
          const output = document.createElement("pre"); output.className = "diff-patch"; output.hidden = true;
          form.append(commands, label, submit); setup.append(summary, form, output);
          form.addEventListener("submit", async (event) => {
            event.preventDefault(); if (!approve.checked) return; submit.disabled = true;
            try { const result = await request(`/worktrees/${record.worktree_id}/setup`, { method:"POST", body:JSON.stringify({commands: commands.value.split("\n").filter((item) => item.trim()), approved:true}) });
              output.textContent = result.results.map((item) => item.content).join("\n"); output.hidden = false; approve.checked = false;
            } catch (error) { setNotice(error.message || String(error), true); }
            finally { submit.disabled = false; }
          });
          card.append(heading, path, actions, setup); list.append(card);
        }
        if (!workflow.worktrees.length) list.append(empty("Room to experiment", "Create a worktree to keep a task's changes in a separate checkout."));
      } catch (error) { $("worktreeList").replaceChildren(empty(error.message || String(error))); }
    }

    async function previewTransfer(record) {
      workflow.transfer = null; $("transferPanel").hidden = true;
      const payload = await request(`/worktrees/${record.worktree_id}/transfer`);
      workflow.transfer = payload;
      $("transferTarget").textContent = `${record.path} → ${record.repository}`;
      $("transferPatch").textContent = payload.review.patch || "No changes to transfer.";
      $("applyTransfer").disabled = !payload.review.patch;
      $("transferPanel").hidden = false;
      $("transferPanel").scrollIntoView({block:"nearest"});
    }

    $("reviewScope").addEventListener("change", () => { $("reviewBase").hidden = $("reviewScope").value !== "branch"; void loadReview(); });
    $("reviewBase").addEventListener("change", loadReview);
    $("refreshReview").addEventListener("click", loadReview);
    $("analyzeReview").addEventListener("click", analyzeReview);
    $("refreshPlan").addEventListener("click", () => loadPlan(true));
    $("planMode").addEventListener("change", () => sendTaskControl("plan", $("planMode").value === "plan" ? "on" : "off"));
    $("planForm").addEventListener("submit", async (event) => { event.preventDefault(); const input = $("planNewStep"); if (await sendTaskControl("plan", `add ${input.value}`)) input.value = ""; });
    $("refreshWorktrees").addEventListener("click", loadWorktrees);
    $("worktreeForm").addEventListener("submit", async (event) => {
      event.preventDefault(); const submit = event.submitter; submit.disabled = true;
      try {
        if ($("worktreeAssociate").checked && !state.selectedRunId) throw new Error("Select a task before moving it to a worktree.");
        const payload = await request("/worktrees", { method:"POST", body:JSON.stringify({ ref:$("worktreeRef").value, branch:$("worktreeBranch").value || undefined,
          include_changes:$("worktreeInclude").checked, ...($("worktreeAssociate").checked ? {run_id:state.selectedRunId} : {}),
        }) });
        await loadWorktrees(); await refreshRuns(); await selectRun(payload.run.run_id); setNotice("Worktree ready.");
      } catch (error) {
        const message = error.message || String(error);
        setNotice(/not a git repository/i.test(message) ? "Worktrees need a Git repository. Choose a Git project as the current workspace first." : message, true);
      }
      finally { submit.disabled = false; }
    });
    $("applyTransfer").addEventListener("click", async () => {
      const preview = workflow.transfer; if (!preview) return; $("applyTransfer").disabled = true;
      try {
        await request(`/worktrees/${preview.worktree.worktree_id}/transfer`, {method:"POST", body:JSON.stringify({revision:preview.review.revision,target_revision:preview.target_revision})});
        workflow.transfer = null; $("transferPanel").hidden = true; setNotice("Changes transferred to the original checkout."); await loadWorktrees();
      } catch (error) { setNotice(error.message || String(error), true); }
      finally { $("applyTransfer").disabled = !workflow.transfer; }
    });
    $("closeTransfer").addEventListener("click", () => { workflow.transfer = null; $("transferPanel").hidden = true; });

    /* The composer continues the selected session; New Session starts a thread. */
    function composerMode() {
      if (!state.selectedRunId) return "new";
      if (STREAM_STATES.has(state.selectedRunState)) return "busy";
      return "reply";
    }

    function syncComposerMode() {
      const mode = composerMode();
      $("runProvider").hidden = mode !== "new"; $("runProvider").disabled = mode !== "new";
      $("runModel").hidden = mode !== "new"; $("runModel").disabled = mode !== "new";
      $("runModel").dispatchEvent(new Event("input"));
      $("sessionModel").hidden = mode === "new";
      $("sessionModel").textContent = [state.selectedProvider, state.selectedModel].filter(Boolean).join(" / ");
      $("sessionModel").title = "This task continues with its saved provider and model. Start a new task to use a different model.";
      $("messageAction").hidden = mode === "new";
      $("runMode").hidden = mode !== "new";
      $("runWorktree").hidden = mode !== "new";
      $("messageAction").options[0].disabled = mode === "busy";
      $("messageAction").options[1].disabled = mode !== "busy";
      if (mode === "busy" && $("messageAction").value === "message") $("messageAction").value = "queue";
      if (mode !== "busy" && $("messageAction").value === "steer") $("messageAction").value = "message";
      $("runMessage").placeholder = mode === "reply"
        ? "Reply to this session"
        : mode === "busy" ? "Guide this task or queue what comes next" : "Describe what you want Libre Claw to do";
      const label = mode === "new" ? "Start task" : $("messageAction").value === "queue" ? "Queue follow-up" : $("messageAction").value === "steer" ? "Steer task" : "Send reply";
      $("sendMessage").setAttribute("aria-label", label); $("sendMessage").title = label;
    }

    $("runForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.sending || !$("runMessage").value.trim()) return;
      state.sending = true; $("sendMessage").disabled = true;
      const draft = $("runMessage").value;
      try {
        const mode = composerMode();
        const delivery = mode === "new" ? "message" : $("messageAction").value;
        if (delivery !== "message") {
          await sendTaskControl(delivery, $("runMessage").value, true);
          return;
        }
        if (mode === "busy") { setNotice("Choose steer or queue while this task is running.", true); return; }
        const body = { message: $("runMessage").value, surface: "dashboard" };
        if (mode === "new") {
          if ($("runProvider").value.trim()) body.provider = $("runProvider").value.trim();
          if ($("runModel").value.trim()) body.model = $("runModel").value.trim();
          body.session = { mode: $("runMode").value };
          if ($("runWorktree").value) body.worktree_id = $("runWorktree").value;
        }
        const path = mode === "reply" ? `/runs/${state.selectedRunId}/messages` : "/runs";
        const payload = await request(path, { method: "POST", body: JSON.stringify(body) });
        if ($("runMessage").value === draft) $("runMessage").value = "";
        autoGrow();
        setNotice(mode === "reply" ? "Reply sent." : `Run ${payload.run.run_id} started.`);
        await refreshRuns();
        await selectRun(payload.run.run_id);
      } catch (error) {
        setNotice(String(error.message || error), true);
      } finally { state.sending = false; $("sendMessage").disabled = false; syncComposerMode(); }
    });

    $("automationForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = $("automationSubmit");
      if (submit.disabled) return;
      submit.disabled = true; const label = submit.textContent; submit.textContent = "Saving…";
      try {
        const body = automationFormPayload();
        const editingId = state.editingAutomationId;
        const path = editingId ? `/automations/${editingId}` : "/automations";
        const method = editingId ? "PUT" : "POST";
        const payload = await request(path, { method, body: JSON.stringify(body) });
        setNotice(`Schedule ${payload.automation.automation_id} ${editingId ? "updated" : "created"}.`);
        resetAutomationForm(event.target);
        await refreshAutomations();
      } catch (error) { setNotice(error.message || String(error), true); submit.textContent = label; }
      finally { submit.disabled = false; }
    });

    function autoGrow() {
      const area = $("runMessage");
      area.style.height = "auto";
      area.style.height = `${Math.min(area.scrollHeight, 160)}px`;
    }

    $("runMessage").addEventListener("input", autoGrow);
    $("runMessage").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
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
    $("tabPlan").addEventListener("click", () => setView("plan"));
    $("tabChanges").addEventListener("click", () => setView("changes"));
    $("tabWorktrees").addEventListener("click", () => setView("worktrees"));
    $("focusRunInput").addEventListener("click", newSession);
    $("openSettings").addEventListener("click", () => openSettingsPane("general"));
    $("closeSettings").addEventListener("click", closeSettingsPanel);
    $("settingsMask").addEventListener("click", closeSettingsPanel);
    document.addEventListener("keydown", handleSettingsKeydown);
    document.querySelectorAll(".settings-nav button").forEach((button) => {
      button.addEventListener("click", () => openSettingsPane(button.dataset.pane));
    });
    bindTabNavigation(".settings-nav button", button => openSettingsPane(button.dataset.pane));
    bindTabNavigation(".view-tab", button => button.click());
    $("messageAction").addEventListener("change", syncComposerMode);
    $("automationRoute").addEventListener("change", syncAutomationRoute);
    $("refreshUsagePane").addEventListener("click", loadUsagePane);
    $("refreshSchedules").addEventListener("click", () => { void refreshAutomations().catch(error => setNotice(error.message || String(error), true)); });
    $("cancelAutomationEdit").addEventListener("click", () => resetAutomationForm($("automationForm")));
    $("cancelRun").addEventListener("click", async () => {
      if (!state.selectedRunId) return;
      $("cancelRun").disabled = true;
      try {
        await request(`/runs/${state.selectedRunId}/cancel`, { method: "POST" });
        setNotice("Cancel requested."); await refreshRunDetail(); await refreshRuns();
      } catch (error) { setNotice(error.message || String(error), true); $("cancelRun").disabled = false; }
    });

    async function refreshAll() {
      if (state.refreshing) return;
      state.refreshing = true; $("refreshAll").disabled = true;
      try {
        const results = await Promise.allSettled([refreshHealth(), refreshUsage(), refreshAutomations()]);
        if (results[0].status === "rejected") {
          $("healthDot").className = "status-dot offline"; $("daemonStatus").textContent = "Disconnected"; $("daemonStatusMetric").textContent = "Disconnected";
          throw results[0].reason;
        }
        const failed = results.find(result => result.status === "rejected");
        if (failed) setNotice(failed.reason?.message || "Some dashboard data could not be refreshed.", true);
        await refreshRuns();
        // While streaming, the incremental poll owns the conversation pane; a
        // full detail refresh here would repaint mid-token.
        if (state.selectedRunId && !state.streaming) await refreshRunDetail();
        else if (state.selectedRunId && state.view === "plan") await loadPlan();
        $("lastRefresh").textContent = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date());
      } catch (error) {
        setNotice(error.message || String(error), true);
      } finally { state.refreshing = false; $("refreshAll").disabled = false; }
    }

    void loadWorktrees();
    initTheme();
    initRail();
    initMobileSidebar();
    clearSelectedRun();
    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""
