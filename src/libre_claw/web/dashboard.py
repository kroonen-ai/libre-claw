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
        <button class="side-foot-row" id="openEngine" type="button" title="Engine" aria-label="Engine">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><path d="M10 10h4v4h-4z"/></svg>
          <span class="grow">Engine</span>
        </button>
        <button class="side-foot-row" id="openPlugins" type="button" title="Plugins" aria-label="Plugins">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3H4v6h2a3 3 0 1 1 0 6H4v6h6v-2a3 3 0 1 1 6 0v2h5v-6h-2a3 3 0 1 1 0-6h2V3h-6v2a3 3 0 1 1-6 0z"/></svg>
          <span class="grow">Plugins</span>
        </button>
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
        <div id="questions" aria-label="Questions awaiting your answer"></div>
        <div id="notice" class="notice" role="status"></div>
        <form id="runForm" class="composer">
          <textarea id="runMessage" aria-label="Message Libre Claw" required rows="1" placeholder="Describe what you want Libre Claw to do"></textarea>
          <div class="composer-controls">
            <select id="runTeam" aria-label="Team for new task" aria-describedby="runTeamSummary"><option value="">No team</option></select>
            <select id="runProvider" aria-label="Provider">
              <option value="">default provider</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI API</option>
              <option value="openrouter">OpenRouter</option>
              <option value="opencode">OpenCode Zen</option>
              <option value="opencode-go">OpenCode Go</option>
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
          <p id="runTeamSummary" class="team-composer-summary" role="status" hidden></p>
        </form>
        <div class="status-strip" id="statusStrip">
          <button id="engineStrip" class="engine-strip" type="button" title="Open engine status">Cordis · checking</button>
          <span class="sep">|</span>
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
        <button id="settingsTabEngine" data-pane="engine" role="tab" aria-controls="paneEngine" aria-selected="false" tabindex="-1" type="button">Engine</button>
        <button id="settingsTabPlugins" data-pane="plugins" role="tab" aria-controls="panePlugins" aria-selected="false" tabindex="-1" type="button">Plugins</button>
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
            <div class="grid-2 model-fields">
              <div class="form-field">
                <label for="configProvider">Provider</label>
                <select id="configProvider" aria-describedby="providerRouteHint">
                  <option value="anthropic">Anthropic</option>
                  <option value="openai">OpenAI API</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="opencode">OpenCode Zen</option>
                  <option value="opencode-go">OpenCode Go</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="moonshot">Kimi Code / Moonshot</option>
                  <option value="ollama">Ollama Cloud/Local</option>
                  <option value="llamacpp">llama.cpp (llama-swap)</option>
                  <option value="codex">OpenAI Codex</option>
                </select>
                <p class="hint" id="providerRouteHint"></p>
                <a id="providerAuthLink" href="https://opencode.ai/auth" target="_blank" rel="noopener noreferrer" hidden>Sign in to OpenCode</a>
              </div>
              <div class="form-field">
                <label for="configModel">Model</label>
                <input id="configModel" placeholder="Select or enter a model ID" required spellcheck="false" autocomplete="off">
              </div>
            </div>
            <div class="model-discovery-row">
              <span id="modelDiscoveryStatus" class="hint" role="status" aria-live="polite">Models load from your provider.</span>
              <div class="model-actions">
                <button id="refreshModels" type="button">Refresh models</button>
                <button class="primary" type="submit">Save default</button>
              </div>
            </div>
          </form>
          <section id="llamacppSettings" class="endpoint-settings" hidden aria-label="llama.cpp connection">
          <div class="setting-row">
            <div class="copy">
              <strong>llama.cpp endpoint</strong>
              <small>Paste your llama-server or llama-swap URL. /ui and /v1 links work too.</small>
            </div>
          </div>
          <form id="llamacppForm" class="stack">
            <div class="endpoint-row">
              <input id="llamacppBaseUrl" placeholder="http://localhost:8080" aria-label="llama.cpp base URL" aria-describedby="llamacppStatus" spellcheck="false">
              <button id="llamacppDiscover" type="button">Discover</button>
              <button id="llamacppSave" class="primary" type="submit">Save endpoint</button>
            </div>
            <p id="llamacppStatus" class="hint" role="status" aria-live="polite">Discover models before choosing one.</p>
            <div id="llamacppDiscovered" class="row"></div>
          </form>
          </section>
        </div>
        <div class="settings-body" id="paneEngine" role="tabpanel" aria-labelledby="settingsTabEngine" tabindex="0" hidden>
          <div class="panel-header"><div><h3>Cordis engine</h3><p class="panel-description">The services behind your workspace.</p></div><button id="refreshEngine" type="button">Refresh</button></div>
          <section class="engine-overview" aria-label="Engine status">
            <div class="engine-overview-top"><div><span class="eyebrow">Local service runtime</span><strong id="engineIdentity">Cordis</strong></div><span id="engineState" class="plugin-state">Checking</span></div>
            <div id="enginePrivacy" class="engine-privacy"></div>
            <p id="engineStatus" class="hint" role="status" aria-live="polite" aria-atomic="true">Connecting to the core runtime…</p>
          </section>
          <div class="engine-section-heading"><h4>Core services</h4><span id="engineCounts" class="tiny">Waiting for runtime</span></div>
          <div id="engineComponents" class="engine-components" aria-label="Core services" aria-busy="true"></div>
          <section class="engine-extensions"><div><h4>Extend your workspace</h4><p class="hint">Add tools and integrations with their own project permissions.</p></div><button id="engineOpenPlugins" type="button">Manage plugins ↗</button></section>
          <div class="engine-recovery"><p id="engineRestartHint" class="hint">Restart is available when no work is running.</p><button id="restartEngine" type="button" aria-describedby="engineRestartHint" disabled>Restart engine</button></div>
        </div>
        <div class="settings-body" id="panePlugins" role="tabpanel" aria-labelledby="settingsTabPlugins" tabindex="0" hidden>
          <div class="panel-header" id="pluginsHeader"><div><h3>Plugins</h3><p class="panel-description">Extensions for this project. <button id="pluginsOpenEngine" class="inline-link" type="button">View core services ↗</button></p></div><div class="plugin-toolbar-actions"><button id="refreshPlugins" type="button">Refresh</button><button id="addPlugin" class="primary" type="button">+ Add plugin</button></div></div>
          <div id="pluginsScopeBlock">
            <div class="plugin-scope"><span class="eyebrow">Project scope</span><strong id="pluginsWorkspace">Current project only</strong><p class="hint">Enabled tools are available on your next message. Disabling a plugin revokes its access.</p></div>
            <p id="pluginsDisabled" class="hint" hidden>Cordis is turned off. Set <code>enabled = true</code> in your <code>[cordis]</code> configuration to enable tools.</p>
            <div id="pluginsPrivacy" class="plugin-privacy" aria-label="Plugin privacy defaults"></div>
          </div>
          <p id="pluginsStatus" class="hint" role="status" aria-live="polite" aria-atomic="true">Load your local plugins.</p>
          <div id="pluginsInventory">
            <input id="pluginSearch" type="search" placeholder="Search plugins or tools" aria-label="Search plugins or tools">
            <section class="plugin-group"><h4 class="plugin-group-title">Installed <span id="pluginsInstalledCount">0</span></h4><div id="pluginsList" class="plugin-list" aria-label="Installed plugins"></div></section>
            <section class="plugin-group" id="pluginsIncludedSection" hidden><h4 class="plugin-group-title">Included <span>Ready to add</span></h4><div id="pluginsIncluded" class="plugin-list" aria-label="Included plugins"></div></section>
          </div>
          <div id="pluginPage" hidden></div>
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
                <option value="opencode">OpenCode Zen</option>
                <option value="opencode-go">OpenCode Go</option>
                <option value="deepseek">DeepSeek</option>
                <option value="moonshot">Kimi Code / Moonshot</option>
                <option value="ollama">Ollama Cloud/Local</option>
              <option value="llamacpp">llama.cpp (llama-swap)</option>
                <option value="codex">OpenAI Codex</option>
              </select></label>
            </div>
            <div class="form-field">
              <label for="automationModel">Model</label>
              <input id="automationModel" placeholder="default">
            </div>
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
          <div class="cache-usage" aria-label="Prompt cache usage">
            <div><span>Input reused</span><strong id="usagePaneCached">0</strong></div>
            <div><span>Cache writes</span><strong id="usagePaneCacheWrites">0</strong></div>
            <div><span>Reported reuse</span><strong id="usagePaneCacheRatio">—</strong></div>
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
    let dashboardUI = null, resolveDashboard, rejectDashboard;
    const dashboardReady = new Promise((resolve, reject) => { resolveDashboard = resolve; rejectDashboard = reject; });
    const uiBindings = Object.fromEntries(["api", "appearance", "models", "tasks", "questions", "plugins", "engine", "workflows"].map(id => [id, []]));
    function bindUI(service, target, event, handler, options) {
      if (dashboardUI) return dashboardUI.bind(service, target, event, handler, options);
      uiBindings[service].push({target, event, handler, options});
    }
    function cancelUI(dispose) { if (typeof dispose === "function") dispose(); }
    function timeoutUI(service, handler, milliseconds, owner) {
      return dashboardUI?.active(service) ? dashboardUI.timeout(service, handler, milliseconds, owner) : () => {};
    }
    function frameUI(service, handler, owner) {
      return dashboardUI?.active(service) ? dashboardUI.frame(service, handler, owner) : () => {};
    }
    function releaseUI(root) { dashboardUI?.release(root); }
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
        const data = await request("/config/theme", {
          method: "PATCH",
          body: JSON.stringify({theme, persist_global: true}),
        });
        setNotice(`Theme saved: ${data.label || theme}`);
      } catch (error) {
        setNotice(`Theme changed locally but could not be saved: ${error.message || error}`, true);
      }
    }

    function initTheme() {
      applyTheme(document.documentElement.dataset.theme || "libre");
      bindUI("appearance", $("themeSelect"), "change", (event) => {
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
      bindUI("appearance", $("railToggle"), "click", () => {
        const rail = $("appFrame").classList.toggle("rail");
        localStorage.setItem(RAIL_KEY, rail ? "1" : "0");
        updateRailLabel();
      });
      bindUI("tasks", $("brandHome"), "click", () => {
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
      bindUI("appearance", $("mobileTasks"), "click", openMobileSidebar);
      bindUI("appearance", $("closeMobileTasks"), "click", () => closeMobileSidebar());
      bindUI("appearance", $("sidebarBackdrop"), "click", () => closeMobileSidebar());
      bindUI("appearance", document, "keydown", handleMobileSidebarKeydown);
      bindUI("appearance", mobileSidebarQuery, "change", () => {
        const sidebarFocused = $("taskSidebar").contains(document.activeElement);
        closeMobileSidebar(false);
        if (mobileSidebarQuery.matches && sidebarFocused) $("mobileTasks").focus();
        else if (!mobileSidebarQuery.matches && [$("closeMobileTasks"), $("mobileTasks")].includes(document.activeElement)) $("brandHome").focus();
      });
    }

    let noticeTimer = 0;
    function setNotice(text, error = false) {
      if (dashboardUI && !dashboardUI.active("appearance")) return;
      const box = $("notice");
      box.textContent = text;
      box.className = `notice visible ${error ? "error" : ""}`;
      const settingsNotice = $("settingsNotice");
      if (!$("settingsOverlay").hidden) {
        settingsNotice.textContent = text;
        settingsNotice.className = `settings-notice ${error ? "error" : ""}`;
        settingsNotice.hidden = false;
      }
      cancelUI(noticeTimer);
      if (!error) noticeTimer = timeoutUI("appearance", () => { box.className = "notice"; settingsNotice.hidden = true; }, 6000, box);
    }

    async function request(path, options = {}) {
      const ui = await dashboardReady;
      return ui.request(path, options);
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
      engineActiveRuns = health.ok ? Number(health.active_runs ?? 0) : null;
      syncEngineControls();
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
        bindUI("tasks", button, "click", () => selectRun(run.run_id));
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
        renderQuestions([]);
        state.events = []; state.streaming = false; cancelUI(streamTimer); resetStreamNode();
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
      cancelUI(streamTimer);
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
      renderQuestions([]);
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
      state.selectedTeam = run.orchestration?.name || run.orchestration?.plugin_id || run.orchestration_plugin || "";
      $("cancelRun").disabled = !["queued", "running", "blocked"].includes(run.state);
      syncComposerMode();
      $("stripMeta").textContent = `${run.run_id} | ${run.provider}:${run.model}`;
      const events = await request(`/runs/${runId}/events?after=0`);
      if (runId !== state.selectedRunId) return;
      state.events = events.events || [];
      scheduleStream(run.state);
      renderEvents();
      renderPermissions(detail.pending_permissions || []);
      renderQuestions(detail.pending_questions || []);
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
      cancelUI(stream.raf);
      stream.node = null;
      stream.text = "";
      stream.shown = 0;
      stream.raf = 0;
    }

    function scheduleStream(runState) {
      cancelUI(streamTimer);
      state.streaming = STREAM_STATES.has(runState);
      if (!state.streaming) {
        resetStreamNode();
        renderEvents();
        return;
      }
      streamTimer = timeoutUI("tasks", () => { void pollRunEvents(); }, 300, $("timeline"));
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
        stream.raf = frameUI("tasks", pumpStream, $("timeline"));
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
      if (!stream.raf) stream.raf = frameUI("tasks", pumpStream, $("timeline"));
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
          if (fresh.some((event) => ["run_finished", "permission_request", "user_question", "user_question_answered"].includes(event.type))) {
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
      streamTimer = timeoutUI("tasks", () => { void pollRunEvents(); }, sawDelta ? 250 : 900, $("timeline"));
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
      bindUI("tasks", copy, "click", async () => {
        try {
          await navigator.clipboard.writeText(code);
          copy.textContent = "Copied";
        } catch (_error) {
          copy.textContent = "Failed";
        }
        timeoutUI("tasks", () => { copy.textContent = "Copy"; }, 1600, copy);
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
        } else if (["permission_request", "permission_result", "user_question", "user_question_answered"].includes(event.type)) {
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
      if (filter === "permission") return event.type.startsWith("permission") || event.type.startsWith("user_question");
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
      if (event.type === "user_question") return "Your input is needed";
      if (event.type === "user_question_answered") return "Answer sent";
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
      if (event.type === "user_question") return (data.questions || []).map(question => question.question || "").join("\n");
      if (event.type === "user_question_answered") return "Your answer was delivered to the running task.";
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

    /* Structured user questions */
    const questionForms = new Map();
    let questionPending = new Set();
    function questionNode(tag, className = "", text) {
      const node = document.createElement(tag); node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    }
    function collectQuestionAnswers(record) {
      return {answers: record.fields.map(field => {
        const selected = field.options.filter(option => option.input.checked).map(option => option.label);
        const custom = field.custom.value.trim();
        if (!selected.length && !custom) throw new Error("Choose an option or write an answer for every question.");
        if (!field.multiple && selected.length > 1) throw new Error("Choose only one option for this question.");
        if (custom.length > 16000) throw new Error("Written answers are limited to 16,000 characters.");
        return {id: field.id, selected, ...(custom ? {custom} : {})};
      })};
    }
    function questionControls(record) {
      for (const control of record.form.querySelectorAll("input, textarea, button")) control.disabled = record.busy || record.sent;
      record.button.textContent = record.sent ? "Answer sent" : record.busy ? "Sending…" : "Send answer";
      record.form.setAttribute("aria-busy", String(record.busy));
    }
    async function submitQuestion(record) {
      if (record.busy || record.sent) return;
      if (!questionPending.has(record.key) || state.selectedRunId !== record.runId || !["queued", "running", "blocked"].includes(state.selectedRunState)) {
        record.status.textContent = "This question is no longer waiting for an answer."; return;
      }
      let answers;
      try { answers = collectQuestionAnswers(record); }
      catch (error) { record.status.textContent = error.message || String(error); return; }
      record.busy = true; record.status.textContent = "Sending your answer…"; questionControls(record);
      try {
        await request(`/runs/${encodeURIComponent(record.runId)}/questions/${encodeURIComponent(record.requestId)}`, {method: "POST", body: JSON.stringify(answers)});
        record.sent = true; record.status.textContent = "Answer sent. The task can continue.";
        if (state.selectedRunId === record.runId) {
          try { await refreshRunDetail(); }
          catch { record.status.textContent = "Answer sent. Refresh the task to see its progress."; }
        }
      } catch (error) { record.status.textContent = `Could not send answer: ${error.message || error}`; }
      finally { record.busy = false; questionControls(record); }
    }
    function renderQuestions(pending) {
      const container = $("questions"), runId = state.selectedRunId;
      const requests = Array.isArray(pending) && ["queued", "running", "blocked"].includes(state.selectedRunState) ? pending : [];
      questionPending = new Set(requests.map(item => JSON.stringify([runId, item.request_id])));
      for (const key of questionForms.keys()) if (!questionPending.has(key)) questionForms.delete(key);
      const forms = [];
      for (const item of requests) {
        if (!Array.isArray(item.questions) || !item.questions.length) continue;
        const key = JSON.stringify([runId, item.request_id]), signature = JSON.stringify(item.questions);
        let record = questionForms.get(key);
        if (!record || record.signature !== signature) {
          const form = questionNode("form", "user-question"); form.setAttribute("aria-label", "Answer the agent's questions");
          record = {key, signature, runId, requestId: String(item.request_id), form, fields: [], busy: false, sent: false};
          form.append(questionNode("h3", "event-type", "Your input is needed"));
          for (const [index, question] of item.questions.entries()) {
            const fieldset = questionNode("fieldset"), legend = questionNode("legend", "", question.question);
            const field = {id: question.id, multiple: question.multiSelect === true, options: []};
            fieldset.append(legend);
            if (question.header) fieldset.append(questionNode("p", "question-header", question.header));
            const options = questionNode("div", "question-options");
            for (const choice of Array.isArray(question.options) ? question.options : []) {
              const label = questionNode("label", "question-option"), input = questionNode("input");
              input.type = field.multiple ? "checkbox" : "radio"; input.name = `question-${item.request_id}-${index}`; input.value = String(choice.label);
              const copy = questionNode("span"); copy.append(questionNode("strong", "", choice.label));
              if (choice.description) copy.append(questionNode("small", "", choice.description));
              label.append(input, copy); options.append(label); field.options.push({label: String(choice.label), input});
            }
            const written = questionNode("label", "question-written", field.options.length ? "Or add a written answer" : "Your answer");
            field.custom = questionNode("textarea"); field.custom.rows = 2; field.custom.maxLength = 16000;
            field.custom.setAttribute("aria-label", `Written answer: ${String(question.question)}`);
            written.append(field.custom); fieldset.append(options, written); form.append(fieldset); record.fields.push(field);
          }
          const actions = questionNode("div", "question-actions");
          record.status = questionNode("p", "hint"); record.status.setAttribute("role", "status"); record.status.setAttribute("aria-live", "polite");
          record.button = questionNode("button", "primary", "Send answer"); record.button.type = "submit";
          actions.append(record.status, record.button); form.append(actions);
          questionForms.set(key, record);
        }
        forms.push(record.form);
      }
      // Preserve the actual input nodes (and focus/drafts) across status polls.
      if (forms.length !== container.children.length || forms.some((form, index) => container.children[index] !== form)) container.replaceChildren(...forms);
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
          bindUI("questions", button, "click", async () => {
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
        bindUI("workflows", runNow, "click", () => { void runAutomationNow(automation.automation_id, runNow).catch(error => setNotice(error.message || String(error), true)); });
        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "Edit";
        bindUI("workflows", edit, "click", () => editAutomation(automation));
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = automation.status === "active" ? "Pause" : "Resume";
        bindUI("workflows", toggle, "click", async () => {
          toggle.disabled = true;
          try { await toggleAutomation(automation); } catch (error) { setNotice(error.message || String(error), true); }
          finally { toggle.disabled = false; }
        });
        const del = document.createElement("button");
        del.type = "button";
        del.textContent = "Delete";
        del.className = "danger";
        bindUI("workflows", del, "click", async () => {
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
        bindUI("tasks", button, "click", () => { $("runMessage").value = prompt; autoGrow(); $("runMessage").focus(); });
        actions.append(button);
      }
      node.append(actions); return node;
    }

    /* Cordis engine */
    let engineSnapshot = null, engineLoading = false, engineRestarting = false, engineActiveRuns = null;
    let engineRendered = "";
    function engineNode(tag, className = "", text) {
      const node = document.createElement(tag); node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    }
    function engineNumber(value) {
      return Number.isSafeInteger(value) && value >= 0 ? value : 0;
    }
    function syncEngineControls() {
      const working = engineNumber(engineSnapshot?.active_operations) > 0 || (engineActiveRuns ?? 0) > 0;
      $("refreshEngine").disabled = engineLoading || engineRestarting;
      $("restartEngine").disabled = engineLoading || engineRestarting || working || engineActiveRuns === null;
      $("restartEngine").textContent = engineRestarting ? "Restarting…" : "Restart engine";
      $("engineRestartHint").textContent = working ? "Wait for active work to finish before restarting."
        : engineActiveRuns === null ? "Connect to the daemon to restart the engine."
        : "Restart the local runtime. Your saved tasks and plugin settings stay in place.";
      $("engineComponents").setAttribute("aria-busy", String(engineLoading || engineRestarting));
    }
    function renderEngine(snapshot) {
      if (snapshot.engine !== "cordis" || !Array.isArray(snapshot.components) || typeof snapshot.state !== "string") {
        throw new Error("Invalid engine status response.");
      }
      engineSnapshot = snapshot;
      const running = snapshot.state === "running", active = engineNumber(snapshot.active_operations);
      const agentReady = snapshot.components.some(component => component.id === "agent" && component.state === "ACTIVE");
      const ready = snapshot.components.filter(component => component.state === "ACTIVE").length;
      $("engineIdentity").textContent = `Cordis ${snapshot.runtime_version || ""}`.trim();
      $("engineState").textContent = running ? "Running" : snapshot.state;
      $("engineState").className = running ? "plugin-state enabled" : "plugin-state danger";
      $("engineStrip").textContent = `Cordis · ${running ? (active ? `${active} working` : agentReady ? "ready" : "paused") : snapshot.state}`;
      $("engineStrip").className = running && agentReady ? "engine-strip ready" : "engine-strip danger";
      $("engineStatus").textContent = active ? `${active} service operation${active === 1 ? "" : "s"} in progress.`
        : running && agentReady ? "Services are ready for your next task."
        : running ? "The agent service is unavailable. Check the core services below." : "The core runtime is not running.";
      $("engineStatus").className = "hint";
      $("engineCounts").textContent = `${ready} / ${snapshot.components.length} active`;
      const privacy = $("enginePrivacy"); privacy.replaceChildren();
      if (snapshot.privacy?.network === false) privacy.append(engineNode("span", "engine-privacy-badge", "Core network blocked"));
      if (snapshot.privacy?.payloads === "opaque-handles") privacy.append(engineNode("span", "engine-privacy-badge", "Task content stays in Python"));
      if (snapshot.privacy?.extensions === "separate-processes") privacy.append(engineNode("span", "engine-privacy-badge", "Extensions run separately"));
      const signature = JSON.stringify(snapshot.components);
      if (signature !== engineRendered) {
        const list = $("engineComponents");
        const expanded = new Set([...list.querySelectorAll("details")].filter(detail => detail.open).map(detail => detail.dataset.component));
        list.replaceChildren();
        for (const component of snapshot.components) {
          const card = engineNode("article", "engine-component"), id = String(component.id || "service");
          card.setAttribute("aria-label", `${component.title || id} service`);
          const heading = engineNode("div", "engine-component-head");
          heading.append(engineNode("h4", "", component.title || id), engineNode("span", component.state === "ACTIVE" ? "engine-service-state active" : "engine-service-state", component.state || "Unknown"));
          const dependencies = Array.isArray(component.dependencies) ? component.dependencies.map(String) : [];
          const count = engineNode("dl", "engine-service-counts");
          for (const [label, value] of [["Working", component.active_operations], ["Completed", component.completed], ["Failed", component.failed], ["Cancelled", component.cancelled]]) {
            const metric = engineNode("div"); metric.append(engineNode("dt", "", label), engineNode("dd", "", engineNumber(value))); count.append(metric);
          }
          card.append(heading, engineNode("p", "engine-dependencies", dependencies.length ? `Uses ${dependencies.join(" · ")}` : "Independent service"), count);
          const implementations = Object.entries(component.implementations || {}).filter(([, plugin]) => typeof plugin === "string" && plugin !== "libre-claw");
          if (implementations.length) {
            const extensions = engineNode("div", "engine-implementations");
            for (const [method, plugin] of implementations) extensions.append(engineNode("p", "hint", `${id}.${method} · ${plugin}`));
            card.append(extensions);
          }
          const details = engineNode("details", "engine-methods"); details.dataset.component = id; details.open = expanded.has(id);
          details.append(engineNode("summary", "", "Service methods"));
          const methods = engineNode("div", "engine-method-list");
          for (const method of Array.isArray(component.methods) ? component.methods : []) methods.append(engineNode("code", "", `${id}.${String(method)}`));
          details.append(methods); card.append(details); list.append(card);
        }
        engineRendered = signature;
      }
    }
    function engineUnavailable(message) {
      engineSnapshot = null; engineRendered = "";
      $("engineState").textContent = "Unavailable"; $("engineState").className = "plugin-state danger";
      $("engineStrip").textContent = "Cordis · unavailable"; $("engineStrip").className = "engine-strip danger";
      $("engineCounts").textContent = "Status unavailable";
      $("engineStatus").textContent = message; $("engineStatus").className = "hint danger";
      $("engineComponents").replaceChildren(); $("enginePrivacy").replaceChildren();
    }
    async function refreshEngine() {
      if (engineLoading || engineRestarting) return;
      engineLoading = true; syncEngineControls();
      try { renderEngine(await request("/engine")); }
      catch (error) { engineUnavailable(`Could not load engine: ${error.message || error}`); }
      finally { engineLoading = false; syncEngineControls(); }
    }
    async function restartCoreEngine() {
      if (engineLoading || engineRestarting || engineActiveRuns === null || engineActiveRuns > 0 || engineNumber(engineSnapshot?.active_operations) > 0) return;
      engineRestarting = true; syncEngineControls();
      $("engineStatus").textContent = "Restarting the local runtime…";
      try { renderEngine(await request("/engine/restart", {method: "POST", body: JSON.stringify({})})); }
      catch (error) { engineUnavailable(`Could not restart engine: ${error.message || error}`); }
      finally { engineRestarting = false; syncEngineControls(); }
    }

    /* Cordis plugins */
    let pluginCatalog = null, pluginLoading = false, pluginPending = "", pluginPendingEnable = false;
    let pluginDetail = null, pluginView = "list", pluginRevision = 0, pluginInstall = null;
    let pluginConfigFields = [], pluginConfigDirty = false, pluginRuntimeStatus = null, pluginConfigDraftBase = null;
    let pluginClient = null, pluginClientRevision = 0;
    let orchestrationEditor = null, orchestrationRevision = 0, orchestrationProviders = null;
    let orchestrationProfiles = [], orchestrationLoading = false, orchestrationLoaded = false, selectedOrchestrationPlugin = "";
    const orchestrationModelCache = new Map();
    function pluginNode(tag, className = "", text) {
      const node = document.createElement(tag);
      node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    }
    function pluginButton(label, action, className = "") {
      const node = pluginNode("button", className, label); node.type = "button";
      bindUI("plugins", node, "click", action); return node;
    }
    async function closePluginClient() {
      pluginClientRevision++;
      const client = pluginClient; pluginClient = null;
      if (client) { try { await client.close(); } catch { /* Server leases also reclaim disconnected guests. */ } }
    }
    async function enablePluginClient(id) {
      if (pluginLoading || pluginPending || pluginConfigDirty || !pluginCatalog?.enabled || !pluginDetail?.requires_client_access
          || pluginDetail.id !== id || pluginDetail.integrity !== "valid") return;
      pluginPending = "client-access"; syncPluginControls();
      try {
        await request(`/plugins/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({enabled: true, allow_client: true})});
        await loadPlugins("Isolated interface access enabled. Plugin code stays outside this browser page.");
        pluginDetail = (await request(`/plugins/${encodeURIComponent(id)}`)).plugin;
        showPluginView("detail"); renderPluginDetail();
      } catch (error) { setPluginStatus(`Could not enable the interface: ${error.message || error}`, true); }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    async function openPluginClient() {
      if (pluginLoading || pluginPending || pluginConfigDirty || !pluginDetail?.enabled || pluginDetail.grants?.allow_client !== true) return;
      pluginPending = "client-open"; syncPluginControls();
      await closePluginClient();
      const revision = ++pluginClientRevision, pluginId = pluginDetail.id;
      const container = document.getElementById("pluginClientView"), status = document.getElementById("pluginClientStatus");
      status.textContent = "Starting the isolated interface…";
      try {
        const [{ClientView}, schema] = await Promise.all([import("/assets/client-view.mjs"), request("/assets/client-schema.json")]);
        const result = await request(`/plugins/${encodeURIComponent(pluginId)}/ui/open`, {method: "POST", body: JSON.stringify(state.selectedRunId ? {run_id: state.selectedRunId} : {})});
        if (revision !== pluginClientRevision || pluginDetail?.id !== pluginId) {
          await request(`/plugins/${encodeURIComponent(pluginId)}/ui/${encodeURIComponent(result.ui_session_id)}`, {method: "DELETE"}); return;
        }
        pluginClient = new ClientView({container, schema, pluginId, sessionId: result.ui_session_id,
          request, bind: (target, event, handler) => bindUI("plugins", target, event, handler),
          timeout: (handler, milliseconds, owner) => timeoutUI("plugins", handler, milliseconds, owner),
          status: (message, error = false) => { if (revision === pluginClientRevision) { status.textContent = message; status.className = error ? "hint danger" : "hint"; } },
        });
        pluginClient.render(result.snapshot); pluginClient.startPolling();
        status.textContent = result.snapshot.warnings?.length ? result.snapshot.warnings.join(" ") : "Interface ready. Execution is isolated and offline.";
      } catch (error) {
        if (revision === pluginClientRevision) {
          await closePluginClient();
          status.textContent = `Could not open the interface: ${error.message || error}`;
        }
      }
      finally { pluginPending = ""; syncPluginControls(); }
    }

    /* Model orchestration editor */
    function orchestrationBind(target, event, handler) {
      return bindUI("plugins", target, event, handler);
    }
    function disposeOrchestrationEditor() {
      orchestrationRevision++;
      if (orchestrationEditor) releaseUI($("pluginPage"));
      orchestrationEditor = null;
    }
    function orchestrationButton(label, action, className = "") {
      const button = pluginNode("button", className, label); button.type = "button";
      orchestrationBind(button, "click", action); return button;
    }
    function orchestrationField(label, value, options = {}) {
      const wrap = pluginNode("label", "orchestration-field"), input = pluginNode(options.multiline ? "textarea" : "input");
      wrap.append(pluginNode("span", "", label)); input.setAttribute("aria-label", options.name || label);
      if (options.multiline) { input.rows = 3; input.maxLength = 16000; }
      else { input.type = options.number ? "number" : "text"; if (options.number) { input.min = String(options.min ?? 1); input.step = "1"; if (options.max) input.max = String(options.max); } }
      input.value = String(value ?? ""); wrap.append(input); return {wrap, input};
    }
    function orchestrationSelect(label, choices, value) {
      const wrap = pluginNode("label", "orchestration-field"), input = pluginNode("select");
      input.setAttribute("aria-label", label); wrap.append(pluginNode("span", "", label));
      for (const [id, title] of choices) { const option = pluginNode("option", "", title); option.value = id; input.append(option); }
      input.value = value; wrap.append(input); return {wrap, input};
    }
    function orchestrationDirty() {
      pluginConfigDirty = true;
      if (orchestrationEditor) {
        orchestrationEditor.save.dataset.blocked = "false"; orchestrationEditor.reset.dataset.blocked = "false";
        orchestrationEditor.check.dataset.blocked = "true";
        orchestrationEditor.routeResults.replaceChildren();
        orchestrationEditor.status.textContent = "Unsaved changes. Save before checking routes or starting a team task.";
      }
      syncPluginControls();
    }
    async function discoverOrchestrationProviders() {
      if (!orchestrationProviders) {
        orchestrationProviders = request("/providers").then(payload => {
          if (!Array.isArray(payload.providers)) throw new Error("Invalid provider directory.");
          return payload;
        }).catch(error => { orchestrationProviders = null; throw error; });
      }
      return orchestrationProviders;
    }
    function orchestrationProvider(row) {
      return row.provider.value || (row.worker ? orchestrationEditor?.orchestrator.provider.value : "") || defaultModelConfig.provider || "";
    }
    function orchestrationEfforts(row) {
      const inherited = row.worker ? orchestrationEditor?.orchestrator.model.value : "";
      const model = row.model.value || inherited || defaultModelConfig.model;
      const metadata = row.models.find(item => item.model === model);
      row.efforts.replaceChildren();
      for (const effort of metadata?.supported_reasoning_efforts || []) {
        const option = pluginNode("option"); option.value = String(effort); row.efforts.append(option);
      }
    }
    async function discoverOrchestrationModels(row, force = false) {
      const editor = orchestrationEditor, revision = ++row.discovery, provider = orchestrationProvider(row);
      if (!editor || !provider) { row.status.textContent = "Choose a provider or keep the inherited route."; return; }
      const inheritedProvider = row.worker ? orchestrationProvider(editor.orchestrator) : defaultModelConfig.provider;
      row.model.placeholder = provider === inheritedProvider ? row.worker ? "Inherit orchestrator model" : "App default model" : "Choose or enter a model for this provider";
      row.status.textContent = "Loading model suggestions…";
      try {
        if (force || !orchestrationModelCache.has(provider)) {
          const pending = request(`/models?${new URLSearchParams({provider, ...(force ? {refresh: "1"} : {})})}`).then(payload => {
            if (!Array.isArray(payload.models)) throw new Error("Invalid model catalog."); return payload.models;
          }).catch(error => { orchestrationModelCache.delete(provider); throw error; });
          orchestrationModelCache.set(provider, pending);
        }
        const models = await orchestrationModelCache.get(provider);
        if (editor !== orchestrationEditor || revision !== row.discovery) return;
        row.models = models; row.modelOptions.replaceChildren();
        for (const model of models) { const option = pluginNode("option", "", model.label || model.model); option.value = String(model.model); row.modelOptions.append(option); }
        orchestrationEfforts(row);
        row.status.textContent = `${models.length} models from ${provider}. A custom model ID is also accepted.`;
      } catch (error) {
        if (editor === orchestrationEditor && revision === row.discovery) row.status.textContent = `Model suggestions unavailable: ${error.message || error}. You can still enter a model ID.`;
      }
    }
    function orchestrationRoute(container, source, name, worker = false) {
      const inheritLabel = worker ? "Inherit orchestrator" : "App default";
      const choices = [["", inheritLabel], ...(source.provider ? [[source.provider, `${source.provider} · saved route`]] : [])];
      const route = pluginNode("div", "orchestration-route"), providerField = orchestrationSelect(`${name} provider`, choices, source.provider || "");
      const modelField = orchestrationField(`${name} model`, source.model || ""); modelField.input.placeholder = worker ? "Inherit orchestrator model" : "App default model";
      const effortField = orchestrationField(`${name} reasoning effort`, source.reasoning_effort || ""); effortField.input.placeholder = "Provider default";
      const modelOptions = pluginNode("datalist"), efforts = pluginNode("datalist");
      const identity = `${orchestrationRevision}-${orchestrationEditor.routeCount++}`;
      modelOptions.id = `team-models-${identity}`; efforts.id = `team-efforts-${identity}`;
      modelField.input.setAttribute("list", modelOptions.id); effortField.input.setAttribute("list", efforts.id);
      modelField.input.autocomplete = "off"; effortField.input.autocomplete = "off";
      const status = pluginNode("p", "hint orchestration-route-status"); status.setAttribute("role", "status");
      const refresh = orchestrationButton("Refresh models", () => { void discoverOrchestrationModels(row, true); }, "orchestration-refresh");
      route.append(providerField.wrap, modelField.wrap); container.append(route, modelOptions, efforts, status);
      const row = {provider: providerField.input, model: modelField.input, effort: effortField.input, effortField: effortField.wrap, refresh, modelOptions, efforts, status, models: [], worker, discovery: 0};
      const editor = orchestrationEditor;
      void discoverOrchestrationProviders().then(payload => {
        if (orchestrationEditor !== editor) return;
        if (editor.limitations) {
          const notes = Array.isArray(payload.limitations) ? payload.limitations : payload.limitations ? [payload.limitations] : [];
          editor.limitations.textContent = notes.map(note => typeof note === "string" ? note : note.message || "").filter(Boolean).join(" ");
          editor.limitations.hidden = !editor.limitations.textContent;
        }
        const saved = row.provider.value;
        row.provider.replaceChildren(); const inherited = pluginNode("option", "", inheritLabel); inherited.value = ""; row.provider.append(inherited);
        for (const provider of payload.providers) {
          const option = pluginNode("option", "", provider.label || provider.name || provider.id); option.value = String(provider.id); row.provider.append(option);
        }
        if (saved && !payload.providers.some(provider => provider.id === saved)) {
          const option = pluginNode("option", "", `${saved} · saved route`); option.value = saved; row.provider.append(option);
        }
        row.provider.value = saved;
        void discoverOrchestrationModels(row);
      }).catch(error => { if (orchestrationEditor === editor) row.status.textContent = `Could not load providers: ${error.message || error}`; });
      orchestrationBind(row.provider, "change", () => {
        orchestrationDirty(); void discoverOrchestrationModels(row);
        if (!worker) for (const child of editor.workers) if (!child.provider.value) void discoverOrchestrationModels(child);
      });
      orchestrationBind(row.model, "input", () => { orchestrationDirty(); orchestrationEfforts(row); });
      orchestrationBind(row.effort, "input", orchestrationDirty);
      return row;
    }
    function orchestrationBudgets(container, row, source, name, worker) {
      const advanced = pluginNode("details", "orchestration-advanced"); advanced.append(pluginNode("summary", "", "Limits & instructions"));
      const grid = pluginNode("div", "orchestration-budget-grid"); row.numbers = {};
      const fields = worker ? [["max_concurrent", "Concurrent workers", 1], ["max_tool_calls", "Tool calls", 20], ["max_seconds", "Time (seconds)", 180], ["context_window_tokens", "Context tokens", 32768], ["max_output_tokens", "Output tokens", 4096]]
        : [["context_window_tokens", "Context tokens", 98304], ["max_output_tokens", "Output tokens", 16384]];
      for (const [key, label, fallback] of fields) {
        const bounds = {max_concurrent: [1, 8], max_tool_calls: [1, 100], max_seconds: [1, 900], context_window_tokens: [1024, 16777216], max_output_tokens: [1, 1048576]}[key];
        const field = orchestrationField(label, source[key] ?? fallback, {number: true, min: bounds[0], max: bounds[1], name: `${name} ${label.toLowerCase()}`});
        row.numbers[key] = field.input; grid.append(field.wrap); orchestrationBind(field.input, "input", orchestrationDirty);
      }
      const prompt = orchestrationField(`${name} instructions`, source.prompt || "", {multiline: true}); row.prompt = prompt.input;
      prompt.input.placeholder = "Optional instructions for this role"; orchestrationBind(prompt.input, "input", orchestrationDirty);
      advanced.append(grid, row.effortField, prompt.wrap, row.refresh); container.append(advanced);
    }
    function syncOrchestrationDefaultWorker() {
      const editor = orchestrationEditor; if (!editor) return;
      const saved = editor.defaultWorker.value || editor.savedDefault;
      editor.defaultWorker.replaceChildren();
      for (const worker of editor.workers) {
        const id = worker.id.value.trim(), option = pluginNode("option", "", worker.name.value.trim() || id || "Unnamed worker"); option.value = id; editor.defaultWorker.append(option);
        worker.remove.dataset.blocked = String(editor.workers.length <= 1);
      }
      editor.defaultWorker.value = editor.workers.some(worker => worker.id.value.trim() === saved) ? saved : editor.workers[0]?.id.value.trim() || "";
      editor.add.dataset.blocked = String(editor.workers.length >= 16);
      syncPluginControls();
    }
    function addOrchestrationWorker(source = {}, dirty = true) {
      const editor = orchestrationEditor; let number = editor.workers.length + 1;
      while (editor.workers.some(worker => worker.id.value === `worker-${number}`)) number++;
      if (editor.workers.length >= 16) { editor.status.textContent = "A team supports up to 16 worker roles."; return; }
      const card = pluginNode("article", "orchestration-worker"), head = pluginNode("div", "orchestration-worker-heading");
      const heading = pluginNode("h5", "", source.name || `Worker ${number}`); head.append(heading); card.append(head);
      const identity = pluginNode("div", "orchestration-identity");
      const id = orchestrationField("Worker ID", source.id || `worker-${number}`), name = orchestrationField("Worker name", source.name || `Worker ${number}`);
      identity.append(id.wrap, name.wrap); card.append(identity);
      const row = orchestrationRoute(card, source, source.name || `Worker ${number}`, true);
      row.id = id.input; row.name = name.input; row.card = card;
      row.id.maxLength = 48; row.name.maxLength = 160;
      const policy = pluginNode("div", "orchestration-policy"), role = orchestrationSelect("Worker role", [["scout", "Scout"], ["worker", "Builder"], ["reviewer", "Reviewer"]], source.role || "worker");
      row.role = role.input;
      const readLabel = pluginNode("label", "orchestration-readonly"), readOnly = pluginNode("input"); readOnly.type = "checkbox"; readOnly.checked = source.read_only !== false;
      readOnly.dataset.lockedByRole = String(row.role.value !== "worker");
      readLabel.append(readOnly, pluginNode("span", "", "Read only")); row.readOnly = readOnly; policy.append(role.wrap, readLabel); card.append(policy);
      orchestrationBudgets(card, row, source, source.name || `Worker ${number}`, true);
      row.remove = orchestrationButton("Remove", () => {
        if (editor !== orchestrationEditor || editor.workers.length <= 1) return;
        editor.workers = editor.workers.filter(worker => worker !== row); card.remove(); orchestrationDirty(); syncOrchestrationDefaultWorker();
      }, "danger orchestration-remove"); head.append(row.remove);
      let previousId = row.id.value;
      orchestrationBind(row.id, "input", () => {
        if (editor.defaultWorker.value === previousId) { editor.savedDefault = row.id.value.trim(); editor.defaultWorker.value = ""; }
        previousId = row.id.value; orchestrationDirty(); syncOrchestrationDefaultWorker();
      });
      orchestrationBind(row.name, "input", () => { heading.textContent = row.name.value.trim() || "Unnamed worker"; orchestrationDirty(); syncOrchestrationDefaultWorker(); });
      orchestrationBind(row.role, "change", () => { row.readOnly.dataset.lockedByRole = String(row.role.value !== "worker"); if (row.role.value !== "worker") row.readOnly.checked = true; orchestrationDirty(); });
      orchestrationBind(row.readOnly, "change", () => {
        if (row.role.value !== "worker") { row.readOnly.checked = true; editor.status.textContent = "Scouts and reviewers stay read only."; }
        orchestrationDirty();
      });
      editor.workers.push(row); editor.workerList.append(card);
      if (dirty) { orchestrationDirty(); syncOrchestrationDefaultWorker(); }
    }
    function collectOrchestrationConfig() {
      const editor = orchestrationEditor;
      const positive = input => {
        const value = Number(input.value), min = Number(input.min || 1), max = Number(input.max || Number.MAX_SAFE_INTEGER);
        if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${input.getAttribute("aria-label") || "Team limit"} must be a whole number between ${min} and ${max}.`); return value;
      };
      const route = row => {
        const result = {provider: row.provider.value.trim(), model: row.model.value.trim(), reasoning_effort: row.effort.value.trim(),
          ...Object.fromEntries(Object.entries(row.numbers).map(([key, input]) => [key, positive(input)])), prompt: row.prompt.value};
        if (result.max_output_tokens >= result.context_window_tokens) throw new Error("Output tokens must be smaller than the working context.");
        return result;
      };
      const seen = new Set(), workers = editor.workers.map(row => {
        const id = row.id.value.trim(), name = row.name.value.trim();
        if (!/^[a-z][a-z0-9_-]{0,47}$/.test(id) || !name || seen.has(id)) throw new Error("Every worker needs a name and a unique lowercase ID."); seen.add(id);
        return {id, name, role: row.role.value, read_only: row.readOnly.checked, ...route(row)};
      });
      if (!workers.length || !seen.has(editor.defaultWorker.value)) throw new Error("Choose a default worker from this team.");
      return {orchestrator: route(editor.orchestrator), workers, default_worker: editor.defaultWorker.value,
        max_concurrent: positive(editor.maxConcurrent), max_total_workers: positive(editor.maxTotal)};
    }
    function renderOrchestrationConfig(container, plugin) {
      pluginConfigFields = []; pluginConfigDirty = false; pluginConfigDraftBase = null;
      const config = plugin.config || {}, form = pluginNode("form", "orchestration-form");
      const editor = orchestrationEditor = {workers: [], routeCount: 0, savedDefault: config.default_worker || ""};
      const introduction = pluginNode("p", "hint", "Choose a lead model and the workers it can delegate to. Empty routes inherit the lead model or app default. A different provider needs its own model ID.");
      editor.limitations = pluginNode("p", "hint orchestration-limitations"); editor.limitations.hidden = true;
      const lead = pluginNode("section", "orchestration-lead"); lead.append(pluginNode("h4", "", "Orchestrator"));
      editor.orchestrator = orchestrationRoute(lead, config.orchestrator || {}, "Orchestrator");
      orchestrationBudgets(lead, editor.orchestrator, config.orchestrator || {}, "Orchestrator", false); form.append(lead);
      const workerHeading = pluginNode("div", "orchestration-worker-heading"); editor.add = orchestrationButton("+ Add worker", () => addOrchestrationWorker()); workerHeading.append(pluginNode("h4", "", "Worker team"), editor.add);
      editor.workerList = pluginNode("div", "orchestration-workers"); form.append(workerHeading, editor.workerList);
      const limits = pluginNode("div", "orchestration-budget-grid"), concurrent = orchestrationField("Team concurrency", config.max_concurrent ?? 3, {number: true, max: 8}), total = orchestrationField("Total worker budget", config.max_total_workers ?? 12, {number: true, max: 32});
      editor.maxConcurrent = concurrent.input; editor.maxTotal = total.input;
      const defaultWorker = orchestrationSelect("Default worker", [], ""); editor.defaultWorker = defaultWorker.input;
      limits.append(concurrent.wrap, total.wrap, defaultWorker.wrap); form.append(limits);
      for (const input of [editor.maxConcurrent, editor.maxTotal, editor.defaultWorker]) {
        orchestrationBind(input, "change", orchestrationDirty); orchestrationBind(input, "input", orchestrationDirty);
      }
      editor.status = pluginNode("p", "hint orchestration-check-status"); editor.status.setAttribute("role", "status");
      editor.routeResults = pluginNode("div", "orchestration-route-results"); editor.routeResults.setAttribute("aria-label", "Checked model routes");
      editor.save = orchestrationButton("Save team", () => { void savePluginConfig(); }, "primary");
      editor.reset = orchestrationButton("Reset changes", renderPluginDetail); editor.check = orchestrationButton("Check routes", () => { void checkOrchestrationRoutes(); });
      editor.save.dataset.blocked = "true"; editor.reset.dataset.blocked = "true";
      const actions = pluginNode("div", "plugin-actions"); actions.append(editor.reset, editor.check, editor.save); form.append(editor.status, editor.routeResults, actions);
      orchestrationBind(form, "submit", event => { event.preventDefault(); void savePluginConfig(); });
      for (const worker of config.workers || []) addOrchestrationWorker(worker, false);
      if (!editor.workers.length) addOrchestrationWorker({id: "worker", name: "Worker", role: "worker"}, false);
      syncOrchestrationDefaultWorker();
      container.append(introduction, editor.limitations, form);
    }
    async function checkOrchestrationRoutes() {
      if (pluginPending || pluginConfigDirty || !orchestrationEditor) return;
      const editor = orchestrationEditor; pluginPending = "routes"; syncPluginControls(); editor.routeResults.replaceChildren(); editor.status.textContent = "Checking saved routes without running a model…";
      try {
        const result = await request("/orchestration/check", {method: "POST", body: JSON.stringify({plugin_id: "orchestration"})});
        if (editor !== orchestrationEditor) return;
        const warnings = Array.isArray(result.warnings) ? result.warnings.map(item => typeof item === "string" ? item : item.message || "Route needs attention") : [];
        editor.status.textContent = result.ready === false || result.error ? `Routes need attention: ${result.error || warnings.join(" · ") || "Review your provider configuration."}`
          : warnings.length ? warnings.join(" · ") : "Routes checked. No model inference was performed.";
        for (const route of Array.isArray(result.routes) ? result.routes : []) {
          const row = pluginNode("div", route.ready ? "orchestration-route-result" : "orchestration-route-result danger");
          row.append(pluginNode("strong", "", route.worker_id || "Model route"), pluginNode("span", "", `${route.provider || ""} / ${route.model || ""}`),
            pluginNode("small", "", route.error || (route.ready ? "Ready" : "Needs attention"))); editor.routeResults.append(row);
        }
      } catch (error) { if (editor === orchestrationEditor) editor.status.textContent = `Could not check routes: ${error.message || error}`; }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    function selectedTeamProfile() { return orchestrationProfiles.find(profile => profile.plugin_id === selectedOrchestrationPlugin); }
    async function refreshOrchestrationProfiles() {
      if (orchestrationLoading) return;
      orchestrationLoading = true;
      try {
        const payload = await request("/orchestration");
        if (!Array.isArray(payload.profiles)) throw new Error("Invalid team directory.");
        orchestrationProfiles = payload.profiles;
        if (!orchestrationLoaded && payload.default_plugin) selectedOrchestrationPlugin = payload.default_plugin;
        orchestrationLoaded = true;
      } catch { orchestrationProfiles = []; }
      finally {
        orchestrationLoading = false;
        const picker = $("runTeam");
        if (picker) {
          picker.replaceChildren(); const none = pluginNode("option", "", "No team"); none.value = ""; picker.append(none);
          for (const profile of orchestrationProfiles.filter(item => item.enabled)) {
            const option = pluginNode("option", "", `${profile.name || profile.plugin_id}${profile.ready ? "" : " · needs setup"}`); option.value = profile.plugin_id; picker.append(option);
          }
          if (selectedOrchestrationPlugin && !orchestrationProfiles.some(item => item.enabled && item.plugin_id === selectedOrchestrationPlugin)) {
            const missing = pluginNode("option", "", "Selected team unavailable"); missing.value = selectedOrchestrationPlugin; picker.append(missing);
          }
          picker.value = selectedOrchestrationPlugin; syncComposerMode();
        }
      }
    }
    async function useOrchestrationForNextTask() {
      if (pluginConfigDirty) { setPluginStatus("Save your team before using it for a task.", true); return; }
      await refreshOrchestrationProfiles();
      const profile = orchestrationProfiles.find(item => item.plugin_id === "orchestration");
      if (!profile?.enabled || !profile.ready) { setPluginStatus(profile?.error || "Enable this team with model access before using it.", true); return; }
      selectedOrchestrationPlugin = "orchestration"; $("runTeam").value = selectedOrchestrationPlugin;
      closeSettingsPanel(); newSession(); syncComposerMode();
    }
    function setPluginStatus(message, error = false) {
      $("pluginsStatus").textContent = message;
      $("pluginsStatus").className = error ? "hint danger" : "hint";
    }
    function syncPluginControls() {
      const busy = pluginLoading || Boolean(pluginPending);
      $("refreshPlugins").disabled = busy || pluginView !== "list";
      $("refreshPlugins").textContent = pluginLoading ? "Refreshing…" : "Refresh";
      $("addPlugin").disabled = busy;
      $("pluginsList").setAttribute("aria-busy", String(busy));
      $("pluginPage").setAttribute("aria-busy", String(busy));
      for (const root of [$("pluginsList"), $("pluginsIncluded"), $("pluginPage")]) {
        for (const button of root.querySelectorAll("button")) {
          button.disabled = (busy && !(pluginPending === "preview" && button.dataset.cancelPreview === "true")) || button.dataset.blocked === "true";
          if (button.dataset.requiresSavedConfig === "true" && pluginConfigDirty) button.disabled = true;
          if (button.dataset.pluginAction !== "toggle") continue;
          const plugin = pluginCatalog?.plugins.find(item => String(item.id) === button.dataset.pluginId);
          button.disabled = busy || !plugin || (!plugin.enabled && (!pluginCatalog.enabled || plugin.integrity !== "valid")) || plugin?.id === "orchestration" && pluginConfigDirty;
          button.textContent = pluginPending === button.dataset.pluginId
            ? (pluginPendingEnable ? "Enabling…" : "Disabling…") : (plugin?.enabled ? "Disable" : plugin?.id === "orchestration" ? "Enable team" : "Enable offline");
        }
      }
      for (const field of $("pluginPage").querySelectorAll("input, select, textarea")) field.disabled = busy || field.dataset.lockedByRole === "true";
      if (orchestrationEditor?.use) orchestrationEditor.use.disabled = busy || pluginConfigDirty || !pluginDetail?.enabled || pluginDetail.grants?.allow_model !== true;
      if (orchestrationEditor?.grant) orchestrationEditor.grant.disabled = busy || pluginConfigDirty || !pluginCatalog?.enabled;
    }
    function pluginToggle(plugin) {
      const name = String(plugin.name || plugin.id);
      const action = plugin.enabled ? "Disable" : plugin.id === "orchestration" ? "Enable team" : "Enable offline";
      const button = pluginButton(action, () => { void togglePlugin(String(plugin.id)); }, plugin.enabled ? "" : "primary");
      button.dataset.pluginId = String(plugin.id); button.dataset.pluginAction = "toggle";
      button.setAttribute("aria-label", `${action}: ${name} for this project`);
      button.setAttribute("aria-describedby", "pluginsStatus"); return button;
    }
    function pluginState(plugin) {
      const changed = plugin.integrity !== "valid";
      const failed = Boolean(plugin.runtime_error || plugin.service?.error);
      return pluginNode("span", changed || failed ? "plugin-state danger" : plugin.enabled ? "plugin-state enabled" : "plugin-state",
        changed ? "Files changed" : failed ? "Runtime failed" : plugin.enabled ? (pluginCatalog?.enabled
          ? (plugin.service?.running || plugin.state === "ACTIVE" ? "Running" : "Enabled") : "Paused") : "Disabled");
    }
    function pluginPermissions(plugin) {
      const grants = plugin.grants || {}, node = pluginNode("dl", "plugin-grants");
      for (const [label, value] of [
        ["Network", grants.allow_network === true ? "Allowed" : "Denied"],
        ["Extra reads", grants.read_paths?.length ? grants.read_paths.join(", ") : "None"],
        ["Extra writes", grants.write_paths?.length ? grants.write_paths.join(", ") : "None"],
        ["Models", grants.allow_model === true ? "Allowed through Libre Claw" : "Denied"],
        ["Core services", grants.allow_engine === true ? "Allowed by an explicit engine grant" : "Denied"],
        ["Interface", grants.allow_client === true ? "Allowed in an isolated offline guest" : "Denied"],
      ]) node.append(pluginNode("dt", "", label), pluginNode("dd", "", value));
      return node;
    }
    function renderPlugins(payload) {
      $("pluginsWorkspace").textContent = payload.workspace ? String(payload.workspace) : "Current project only";
      $("pluginsDisabled").hidden = payload.enabled === true;
      const privacy = payload.privacy || {}, privacyList = $("pluginsPrivacy"); privacyList.replaceChildren();
      for (const [key, label] of [["telemetry", "No telemetry"], ["history_shared", "No automatic chat access"], ["credentials_inherited", "No inherited credentials"]]) {
        if (privacy[key] === false) privacyList.append(pluginNode("span", "plugin-privacy-item", label));
      }
      if (privacy.default_network === "denied") privacyList.append(pluginNode("span", "plugin-privacy-item", "Offline by default"));
      const query = $("pluginSearch").value.trim().toLowerCase();
      const matches = item => [item.id, item.name, item.description, ...(item.tools || [])].join(" ").toLowerCase().includes(query);
      const sorted = items => [...items].sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)) || String(a.id).localeCompare(String(b.id)));
      const list = $("pluginsList"); list.replaceChildren();
      const installed = sorted(payload.plugins.filter(matches));
      $("pluginsInstalledCount").textContent = String(payload.plugins.length);
      if (!installed.length) {
        const empty = pluginNode("div", "plugin-empty");
        empty.append(pluginNode("h4", "", query ? "No matching plugins" : "Make this workspace your own"),
          pluginNode("p", "hint", query ? "Try a different name or tool." : "No plugins installed. Start with an included tool, or add a package you trust."));
        if (!query) empty.append(pluginButton("Add your first plugin", () => { void openPluginInstall(); }, "primary"));
        list.append(empty);
      }
      for (const plugin of installed) {
        const name = String(plugin.name || plugin.id), card = pluginNode("article", "plugin-card"); card.setAttribute("aria-label", name);
        const head = pluginNode("div", "plugin-card-head"), identity = pluginNode("div", "plugin-identity");
        identity.append(pluginNode("h4", "", name), pluginNode("p", "tiny", [plugin.id, plugin.version].filter(Boolean).join(" · ")));
        head.append(identity, pluginState(plugin)); card.append(head);
        if (plugin.description) card.append(pluginNode("p", "plugin-description", plugin.description));
        card.append(pluginPermissions(plugin), pluginNode("p", "plugin-tools", plugin.tools?.length ? `Tools: ${plugin.tools.join(", ")}` : "No tools declared"));
        if (plugin.integrity !== "valid") card.append(pluginNode("p", "hint danger", "Files changed since installation. Reinstall before enabling."));
        const actions = pluginNode("div", "plugin-actions");
        actions.append(pluginButton("Details & configure", () => { void openPluginDetail(String(plugin.id)); }), pluginToggle(plugin));
        card.append(actions); list.append(card);
      }
      const included = $("pluginsIncluded"); included.replaceChildren();
      const available = sorted((payload.catalog || []).filter(item => !payload.plugins.some(installed => installed.id === item.id)).filter(matches));
      $("pluginsIncludedSection").hidden = !available.length;
      for (const item of available) {
        const card = pluginNode("article", "plugin-card included"), identity = pluginNode("div", "plugin-identity");
        identity.append(pluginNode("h4", "", item.name || item.id), pluginNode("p", "tiny", `Included · ${item.version || ""}`));
        card.append(identity, pluginNode("p", "plugin-description", item.description || "An included tool for this workspace."));
        const actions = pluginNode("div", "plugin-actions"); actions.append(pluginButton("Add to workspace", () => { void openPluginInstall(item.source); }));
        card.append(actions); included.append(card);
      }
      syncPluginControls();
    }
    async function loadPlugins(message = "") {
      if (pluginLoading || (pluginPending && !message)) return false;
      pluginLoading = true; syncPluginControls(); setPluginStatus("Loading plugins…");
      try {
        const payload = await request("/plugins");
        if (!Array.isArray(payload.plugins) || typeof payload.enabled !== "boolean") throw new Error("Invalid plugin registry response.");
        pluginCatalog = payload; renderPlugins(payload);
        setPluginStatus(message || `${payload.plugins.length} plugin${payload.plugins.length === 1 ? "" : "s"} installed locally.`); return true;
      } catch (error) {
        pluginCatalog = null; $("pluginsList").replaceChildren(); $("pluginsIncluded").replaceChildren(); $("pluginsPrivacy").replaceChildren();
        setPluginStatus(`${message ? "Change saved, but plugins could not be refreshed" : "Could not load plugins"}: ${error.message || error}`, true); return false;
      } finally { pluginLoading = false; syncPluginControls(); }
    }
    async function togglePlugin(id) {
      if (pluginLoading || pluginPending || !pluginCatalog) return;
      const plugin = pluginCatalog.plugins.find(item => String(item.id) === id);
      if (!plugin || (!plugin.enabled && (!pluginCatalog.enabled || plugin.integrity !== "valid"))) return;
      const enabled = !plugin.enabled, name = String(plugin.name || id);
      if (id === "orchestration" && pluginConfigDirty) { setPluginStatus("Save your team before changing its enabled state.", true); return; }
      const restoreFocus = document.activeElement?.dataset.pluginId === id;
      pluginPending = id; pluginPendingEnable = enabled; syncPluginControls();
      try {
        await request(`/plugins/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({enabled, ...(enabled && id === "orchestration" ? {allow_model: true} : {})})});
        await loadPlugins(enabled ? id === "orchestration" ? `${name} enabled. Select this team for a new task to authorize delegation.` : `${name} enabled offline. Its tools are available on your next message.` : `${name} disabled. Its access has been revoked.`);
        if (id === "orchestration") await refreshOrchestrationProfiles();
        if (pluginDetail?.id === id && pluginView === "detail") {
          pluginDetail = (await request(`/plugins/${encodeURIComponent(id)}`)).plugin; renderPluginDetail();
        }
      } catch (error) {
        const message = `Could not ${enabled ? "enable" : "disable"} ${name}: ${error.message || error}`;
        if (enabled && (plugin.format === "deepseek-harness" || pluginDetail?.id === id && pluginView === "detail")) {
          const staged = pluginDetail?.id === id ? stagedPluginConfig() : [];
          try {
            const details = (await request(`/plugins/${encodeURIComponent(id)}`)).plugin;
            if (details?.id === id) {
              const rootDraft = staged.find(field => field.kind === "root");
              if (rootDraft && !pluginConfigObject(JSON.parse(rootDraft.value))) throw new Error("Keep the existing JSON editor.");
              pluginDetail = details; showPluginView("detail"); renderPluginDetail(); restoreStagedPluginConfig(staged);
            }
          } catch { /* Keep the original activation failure and any existing form. */ }
        }
        setPluginStatus(message, true);
      }
      finally {
        pluginPending = ""; syncPluginControls();
        if (restoreFocus) {
          const root = pluginView === "detail" ? $("pluginPage") : $("pluginsList");
          [...root.querySelectorAll("button[data-plugin-id]")].find(button => button.dataset.pluginId === id)?.focus();
        }
      }
    }
    async function enablePluginModels(id) {
      if (pluginLoading || pluginPending || !pluginCatalog?.enabled) return;
      pluginPending = "model-access"; syncPluginControls();
      try {
        await request(`/plugins/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({enabled: true, allow_model: true})});
        await loadPlugins("Plugin enabled with access to configured models. Provider keys stay in Libre Claw.");
        pluginDetail = (await request(`/plugins/${encodeURIComponent(id)}`)).plugin;
        showPluginView("detail"); renderPluginDetail();
        if (id === "orchestration") await refreshOrchestrationProfiles();
      } catch (error) { setPluginStatus(`Could not enable model access: ${error.message || error}`, true); }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    async function enablePluginEngine(id) {
      if (pluginLoading || pluginPending || !pluginCatalog?.enabled || !pluginDetail?.requires_engine_access
          || pluginDetail.id !== id || pluginDetail.integrity !== "valid") return;
      pluginPending = "engine-access"; syncPluginControls();
      try {
        await request(`/plugins/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({enabled: true, allow_engine: true})});
        await loadPlugins("Core service grant saved. Open Engine and restart the runtime to activate it.");
        pluginDetail = (await request(`/plugins/${encodeURIComponent(id)}`)).plugin;
        showPluginView("detail"); renderPluginDetail();
      } catch (error) { setPluginStatus(`Could not enable core services: ${error.message || error}`, true); }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    function showPluginView(view) {
      pluginView = view; $("pluginsInventory").hidden = view !== "list"; $("pluginPage").hidden = view === "list";
      $("pluginsHeader").hidden = view !== "list"; $("pluginsScopeBlock").hidden = view !== "list";
    }
    async function leavePluginPage() {
      if ((pluginPending && pluginPending !== "preview") || pluginLoading) return;
      const cancelledCheck = pluginPending === "preview";
      if (cancelledCheck) pluginPending = "";
      const token = pluginInstall?.preview?.token;
      void closePluginClient();
      disposeOrchestrationEditor();
      pluginRevision++; pluginInstall = null; pluginDetail = null; pluginConfigFields = []; pluginConfigDirty = false; pluginRuntimeStatus = null;
      $("pluginPage").replaceChildren(); showPluginView("list"); syncPluginControls();
      if (cancelledCheck) setPluginStatus("Package check closed. Nothing was installed.");
      if (token) {
        try { await request(`/plugins/preview/${encodeURIComponent(token)}`, {method: "DELETE"}); }
        catch (error) { setPluginStatus(`Could not discard package preview: ${error.message || error}. It will expire automatically.`, true); }
      }
    }
    function pluginBreadcrumb(title) {
      const row = pluginNode("nav", "plugin-breadcrumb"); row.setAttribute("aria-label", "Plugin navigation");
      const back = pluginButton("← Plugins", () => { void leavePluginPage(); }); back.dataset.cancelPreview = "true";
      row.append(back, pluginNode("span", "", "/"), pluginNode("span", "", title)); return row;
    }
    async function openPluginDetail(id) {
      if (pluginLoading || pluginPending) return;
      const entering = pluginView !== "detail" || pluginDetail?.id !== id;
      pluginPending = "detail"; const revision = ++pluginRevision; syncPluginControls(); setPluginStatus("Loading plugin details…");
      try {
        const payload = await request(`/plugins/${encodeURIComponent(id)}`);
        if (revision !== pluginRevision) return;
        pluginDetail = payload.plugin; showPluginView("detail"); renderPluginDetail(); setPluginStatus("");
        if (entering) $("panePlugins").scrollTop = 0;
      } catch (error) { setPluginStatus(`Could not load plugin: ${error.message || error}`, true); }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    function renderPluginDetail() {
      void closePluginClient();
      disposeOrchestrationEditor();
      const plugin = pluginDetail, page = $("pluginPage"); page.replaceChildren();
      page.append(pluginBreadcrumb(plugin.name || plugin.id));
      const head = pluginNode("div", "plugin-detail-head"), identity = pluginNode("div", "plugin-identity");
      identity.append(pluginNode("h3", "", plugin.name || plugin.id), pluginNode("p", "tiny", `${plugin.id} · ${plugin.version}`));
      head.append(identity, pluginState(plugin)); page.append(head);
      if (plugin.description) page.append(pluginNode("p", "plugin-description", plugin.description));
      if (plugin.runtime_error) page.append(pluginNode("p", "hint danger", plugin.runtime_error));
      if (plugin.service) {
        const service = pluginNode("section", "plugin-section"); service.append(pluginNode("h4", "", "Libre WebUI connection"));
        service.append(pluginNode("p", "hint", plugin.service.running ? "Ready. Use this socket path in Libre WebUI’s native-provider settings." : plugin.service.error || "This service is stopped."));
        if (plugin.service.socket_path) service.append(pluginNode("code", "plugin-digest", plugin.service.socket_path));
        page.append(service);
      }
      if (plugin.format === "deepseek-harness") {
        const components = pluginNode("section", "plugin-section");
        components.append(pluginNode("h4", "", "Harness components"));
        for (const component of plugin.components || []) components.append(pluginNode("p", "hint",
          `${component.id || component.name || "Component"} · ${component.state || (component.enabled === false ? "Disabled" : "Ready to load")}`));
        page.append(components);
      }
      const actions = pluginNode("div", "plugin-actions plugin-detail-actions"), check = pluginButton("Check runtime", () => { void checkPluginRuntime(); });
      check.dataset.blocked = String(!plugin.enabled || !pluginCatalog?.enabled || plugin.integrity !== "valid");
      check.title = "Loads this enabled plugin in its isolated runtime, using its existing grants.";
      actions.append(check, pluginToggle(plugin)); page.append(actions);
      const runtime = pluginRuntimeStatus = pluginNode("p", "hint"); runtime.id = "pluginRuntimeStatus"; runtime.setAttribute("role", "status");
      runtime.textContent = plugin.enabled ? "Check that this plugin loads with its current permissions." : "Enable offline to check its runtime."; page.append(runtime);
      const config = pluginNode("section", "plugin-section"); config.append(pluginNode("h4", "", "Configuration"));
      renderPluginConfig(config, plugin); page.append(config);
      if (plugin.requires_client_access && plugin.client) {
        const section = pluginNode("section", "plugin-section"); section.append(pluginNode("h4", "", "Plugin interface"),
          pluginNode("p", "hint", "This interface runs offline with its public settings. Provider keys and conversation history stay private. A selected task shares only its ID and status."));
        const actions = pluginNode("div", "plugin-actions plugin-detail-actions");
        const open = pluginButton(plugin.grants?.allow_client === true ? "Open interface" : "Enable interface", () => {
          if (plugin.grants?.allow_client === true) void openPluginClient(); else void enablePluginClient(String(plugin.id));
        }, "primary");
        open.dataset.requiresSavedConfig = "true";
        open.dataset.blocked = String(!pluginCatalog?.enabled || plugin.integrity !== "valid" || plugin.grants?.allow_client === true && !plugin.enabled);
        actions.append(open, pluginButton("Close interface", () => { void closePluginClient(); }));
        const status = pluginNode("p", "hint"); status.id = "pluginClientStatus"; status.setAttribute("role", "status");
        status.textContent = plugin.grants?.allow_client === true ? "Open this plugin’s isolated interface." : "A separate interface grant is required.";
        const view = pluginNode("div", "plugin-client-view"); view.id = "pluginClientView"; view.setAttribute("aria-label", "Isolated plugin interface");
        section.append(actions, status, view); page.append(section);
      }
      if (plugin.id === "orchestration") {
        const use = orchestrationEditor.use = orchestrationButton("Use for next task", () => { void useOrchestrationForNextTask(); }, "primary");
        const launch = pluginNode("div", "orchestration-launch");
        launch.append(pluginNode("p", "hint", "Enabling this team allows model access. Selecting it for a task authorizes delegation within the saved worker limits."), use); page.append(launch);
      }
      const permissions = pluginNode("section", "plugin-section"); permissions.append(pluginNode("h4", "", "Permissions"), pluginPermissions(plugin),
        pluginNode("p", "hint", "Private storage belongs to this project. Additional filesystem or network access must be granted explicitly from the CLI.")); page.append(permissions);
      if (plugin.requires_engine_access) {
        const names = Array.isArray(plugin.engine_services) ? plugin.engine_services.map(service => typeof service === "string" ? service : service.title || service.id).filter(Boolean).join(", ") : "declared core services";
        permissions.append(pluginNode("p", "hint", `Core services: ${names}. This extension receives operation names, never prompts or provider keys. Restart the engine after changing its grant.`));
        if (plugin.grants?.allow_engine !== true) {
          const engine = pluginButton("Enable core services", () => { void enablePluginEngine(String(plugin.id)); });
          engine.dataset.blocked = String(!pluginCatalog?.enabled || plugin.integrity !== "valid"); permissions.append(engine);
          engine.dataset.requiresSavedConfig = "true";
        }
        permissions.append(pluginButton("Open engine", () => openSettingsPane("engine")));
      }
      if (plugin.grants?.allow_model !== true && (plugin.id !== "orchestration" || plugin.enabled)) {
        permissions.append(pluginNode("p", "hint", "Model access lets this plugin use your configured providers. It receives model responses, never provider keys or automatic conversation history."));
        const models = plugin.id === "orchestration"
          ? orchestrationEditor.grant = orchestrationButton("Enable team model access", () => { if (!pluginConfigDirty) void enablePluginModels(String(plugin.id)); })
          : pluginButton("Enable with model access", () => { void enablePluginModels(String(plugin.id)); });
        models.dataset.blocked = String(!pluginCatalog?.enabled || plugin.integrity !== "valid"); permissions.append(models);
      }
      const tools = pluginNode("section", "plugin-section"); tools.append(pluginNode("h4", "", "Tools"));
      for (const tool of plugin.tool_definitions || []) {
        const item = pluginNode("details", "plugin-tool-detail"); item.append(pluginNode("summary", "", tool.name || tool.id || "Tool"));
        if (tool.description) item.append(pluginNode("p", "hint", tool.description));
        item.append(pluginNode("pre", "plugin-json", JSON.stringify(tool.parameters || tool.input_schema || tool, null, 2))); tools.append(item);
      }
      if (!plugin.tool_definitions?.length) tools.append(pluginNode("p", "hint", "No tools declared.")); page.append(tools);
      const verification = pluginNode("details", "plugin-tool-detail"); verification.append(pluginNode("summary", "", "Package verification"),
        pluginNode("p", "hint", `Integrity: ${plugin.integrity}`), pluginNode("code", "plugin-digest", plugin.digest || "Digest unavailable")); page.append(verification);
      const removal = pluginNode("section", "plugin-remove"); removal.append(pluginNode("h4", "", "Remove plugin"),
        pluginNode("p", "hint", "Uninstalls this package and deletes its grants, configuration, and private state in every project."),
        pluginButton("Remove plugin…", () => showPluginRemoval(removal), "danger")); page.append(removal); syncPluginControls();
    }
    function pluginConfigObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
    function pluginConfigPointer(path) { return "/" + path.map(key => key.replace(/~/g, "~0").replace(/\//g, "~1")).join("/"); }
    function pluginConfigAt(config, path) {
      let value = config;
      for (const key of path) {
        if (!pluginConfigObject(value) || !Object.hasOwn(value, key)) return {present: false, value: undefined};
        value = value[key];
      }
      return {present: true, value};
    }
    function writePluginConfigPath(config, path, value, remove = false) {
      let parent = config;
      for (const key of path.slice(0, -1)) {
        if (!Object.hasOwn(parent, key) || !pluginConfigObject(parent[key])) {
          if (remove) return;
          Object.defineProperty(parent, key, {value: {}, enumerable: true, writable: true, configurable: true});
        }
        parent = parent[key];
      }
      const key = path[path.length - 1];
      if (remove) delete parent[key];
      else Object.defineProperty(parent, key, {value, enumerable: true, writable: true, configurable: true});
    }
    function renderPluginConfig(container, plugin) {
      if (plugin.id === "orchestration") { renderOrchestrationConfig(container, plugin); return; }
      const schema = plugin.config_schema || {}, properties = schema.properties || {}, config = plugin.config || {};
      pluginConfigFields = []; pluginConfigDirty = false; pluginConfigDraftBase = null;
      const form = pluginNode("form", "plugin-config-form"); form.autocomplete = "off";
      const markDirty = () => { pluginConfigDirty = true; save.dataset.blocked = "false"; reset.dataset.blocked = "false"; syncPluginControls(); };
      const renderFields = (target, parentSchema, prefix = [], depth = 0) => {
        for (const [key, field] of Object.entries(parentSchema.properties || {})) {
          const path = [...prefix, key], pointer = pluginConfigPointer(path), current = pluginConfigAt(config, path);
          const secret = field.writeOnly === true || field.format === "password";
          const required = parentSchema.required?.includes(key) === true;
          const titleText = field.title || key;
          if (!secret && !Array.isArray(field.enum) && field.type === "object" && Object.keys(field.properties || {}).length && depth < 5) {
            const group = pluginNode("fieldset", "plugin-tool-detail plugin-config-field");
            const legend = pluginNode("legend", "", titleText);
            if (required) legend.append(pluginNode("span", "plugin-required", " *"));
            group.append(legend);
            if (field.description) group.append(pluginNode("p", "hint", field.description));
            const fields = pluginNode("div", "plugin-config-form");
            renderFields(fields, field, path, depth + 1); group.append(fields); target.append(group);
            continue;
          }
          const label = pluginNode("label", "plugin-config-field"), title = pluginNode("span", "", titleText);
          if (required) title.append(pluginNode("span", "plugin-required", " *")); label.append(title);
          let input, kind = secret ? "secret" : Array.isArray(field.enum) ? "enum" : field.type;
          if (kind === "enum") {
            input = pluginNode("select"); const empty = pluginNode("option", "", required ? "Choose a value" : "Use default"); empty.value = ""; input.append(empty);
            for (const option of field.enum) { const node = pluginNode("option", "", String(option)); node.value = JSON.stringify(option); input.append(node); }
            input.value = current.present ? JSON.stringify(current.value) : "";
          } else if (kind === "boolean") {
            input = pluginNode("select");
            for (const [value, text] of [["", required ? "Choose a value" : "Use default"], ["true", "On"], ["false", "Off"]]) { const option = pluginNode("option", "", text); option.value = value; input.append(option); }
            input.value = current.present ? String(current.value) : "";
          } else if (["string", "number", "integer", "secret"].includes(kind)) {
            input = pluginNode("input"); input.type = secret ? "password" : ["number", "integer"].includes(kind) ? "number" : "text";
            input.value = !secret && current.present ? String(current.value ?? "") : "";
            if (kind === "integer") input.step = "1"; else if (kind === "number") input.step = "any";
            if (field.minimum !== undefined) input.min = String(field.minimum);
            if (field.maximum !== undefined) input.max = String(field.maximum);
            if (secret) { input.autocomplete = "new-password"; input.placeholder = plugin.configured_secrets?.includes(pointer) ? "Saved · leave blank to keep" : "Not configured"; }
          } else {
            kind = "json"; input = pluginNode("textarea", "plugin-json-input"); input.rows = 5;
            input.value = current.present ? JSON.stringify(current.value, null, 2) : "";
            input.placeholder = plugin.configured_secrets?.some(saved => saved.startsWith(pointer + "/"))
              ? "Saved secret values omitted · leave unchanged to keep" : "JSON · leave blank for default";
          }
          input.setAttribute("aria-label", prefix.length ? `${prefix.join(" / ")} / ${titleText}` : titleText);
          input.setAttribute("aria-required", String(required)); input.dataset.pluginConfigPath = pointer;
          label.append(input);
          if (field.description) label.append(pluginNode("span", "hint", field.description));
          if (kind === "json") label.append(pluginNode("span", "hint", field.type === "array"
            ? "JSON array. Editing replaces the full list; leave unchanged to keep saved values."
            : "Advanced JSON. Saved secret fields are omitted; leaving them out keeps their values."));
          if (secret && field.type && field.type !== "string") label.append(pluginNode("span", "hint", `Enter a JSON ${field.type}. Its value stays concealed.`));
          const entry = {key, path, pointer, kind, input, schema: field, clear: false, touched: false, present: current.present,
            initialValue: String(input.value), secretType: secret ? field.type : null}; pluginConfigFields.push(entry);
          const changed = () => { entry.clear = false; entry.touched = true; markDirty(); };
          bindUI("plugins", input, "input", changed); bindUI("plugins", input, "change", changed);
          if (secret && plugin.configured_secrets?.includes(pointer)) {
            label.append(pluginButton("Clear saved value", () => { input.value = ""; input.placeholder = "Will be cleared on save"; entry.clear = true; entry.touched = true; markDirty(); }, "plugin-clear-secret"));
          }
          target.append(label);
        }
      };
      renderFields(form, schema);
      if (!Object.keys(properties).length) {
        const label = pluginNode("label", "plugin-config-field"); label.append(pluginNode("span", "", "Configuration JSON"));
        const input = pluginNode("textarea", "plugin-json-input"); input.rows = 6; input.value = JSON.stringify(config, null, 2);
        input.setAttribute("aria-label", "Configuration JSON"); bindUI("plugins", input, "input", markDirty); label.append(input);
        form.append(label); pluginConfigFields.push({kind: "root", input});
      }
      const save = pluginButton("Save configuration", () => { void savePluginConfig(); }, "primary");
      const reset = pluginButton("Reset changes", () => { renderPluginDetail(); });
      save.dataset.blocked = "true"; reset.dataset.blocked = "true";
      const actions = pluginNode("div", "plugin-actions"); actions.append(reset, save); form.append(actions);
      bindUI("plugins", form, "submit", event => { event.preventDefault(); void savePluginConfig(); });
      container.append(pluginNode("p", "hint", "Changes stay here until you save. Secrets are stored locally and never returned to this page."), form);
    }
    function collectPluginConfig() {
      if (pluginDetail?.id === "orchestration" && orchestrationEditor) return collectOrchestrationConfig();
      const config = JSON.parse(JSON.stringify(pluginConfigDraftBase || pluginDetail.config || {}));
      for (const field of pluginConfigFields) {
        const raw = String(field.input.value);
        if (field.kind === "root") {
          const value = JSON.parse(raw);
          if (!pluginConfigObject(value)) throw new Error("Configuration must be a JSON object.");
          return value;
        }
        // Preserve exact public values and unrecognized properties unless the
        // user edits this leaf. Password fields are always omitted when blank.
        if (field.kind !== "secret" && !field.touched && raw === field.initialValue) continue;
        writePluginConfigPath(config, field.path, undefined, true);
        let value;
        const label = field.path.join(" / ");
        if (field.kind === "secret") {
          if (field.clear) value = null;
          else if (raw && field.secretType && field.secretType !== "string") {
            try { value = JSON.parse(raw); } catch { throw new Error(`${label} must contain a valid JSON ${field.secretType}.`); }
          } else if (raw) value = raw;
          else continue;
        }
        else if (!raw.trim() && field.kind !== "string") continue;
        else if (["number", "integer"].includes(field.kind)) {
          value = Number(raw); if (!Number.isFinite(value) || (field.kind === "integer" && !Number.isInteger(value))) throw new Error(`${label} must be a valid ${field.kind}.`);
          if (field.schema.minimum !== undefined && value < field.schema.minimum) throw new Error(`${label} must be at least ${field.schema.minimum}.`);
          if (field.schema.maximum !== undefined && value > field.schema.maximum) throw new Error(`${label} must be at most ${field.schema.maximum}.`);
        } else if (["json", "enum", "boolean"].includes(field.kind)) {
          try { value = JSON.parse(raw); } catch { throw new Error(`${label} must contain valid JSON.`); }
        } else value = raw;
        const type = field.schema.type;
        if (!field.clear && ((type === "object" && !pluginConfigObject(value)) || (type === "array" && !Array.isArray(value))
          || (type === "boolean" && typeof value !== "boolean") || (type === "null" && value !== null))) {
          throw new Error(`${label} must contain a valid JSON ${type}.`);
        }
        if (field.kind === "enum" && !field.schema.enum.some(option => JSON.stringify(option) === JSON.stringify(value))) throw new Error(`${label} must be one of its listed choices.`);
        writePluginConfigPath(config, field.path, value);
      }
      return config;
    }
    function stagedPluginConfig() {
      if (!pluginConfigDirty) return [];
      return pluginConfigFields.filter(field => field.touched || field.clear || String(field.input.value) !== field.initialValue)
        .map(field => ({pointer: field.pointer, value: String(field.input.value), clear: field.clear, kind: field.kind}));
    }
    function restoreStagedPluginConfig(staged) {
      let restored = false;
      const rootDraft = staged.find(field => field.kind === "root");
      if (rootDraft && !pluginConfigFields.some(field => field.kind === "root")) {
        pluginConfigDraftBase = JSON.parse(rootDraft.value);
        for (const field of pluginConfigFields) {
          const current = pluginConfigAt(pluginConfigDraftBase, field.path);
          const structured = field.kind === "json" || field.kind === "secret" && field.secretType !== "string";
          field.input.value = !current.present || current.value === null ? "" : structured ? JSON.stringify(current.value, null, 2)
            : ["enum", "boolean"].includes(field.kind) ? JSON.stringify(current.value) : String(current.value);
          field.touched = true; field.clear = field.kind === "secret" && current.present && current.value === null;
        }
        restored = true;
      }
      for (const saved of staged) {
        const field = pluginConfigFields.find(item => item.pointer === saved.pointer && item.kind === saved.kind);
        if (!field) continue;
        field.input.value = saved.value; field.touched = true; field.clear = saved.clear;
        if (field.clear) field.input.placeholder = "Will be cleared on save";
        restored = true;
      }
      if (restored) {
        pluginConfigDirty = true;
        for (const button of $("pluginPage").querySelectorAll("button")) {
          if (["Save configuration", "Reset changes"].includes(button.textContent)) button.dataset.blocked = "false";
        }
      }
    }
    async function savePluginConfig() {
      if (pluginPending || !pluginDetail || !pluginConfigDirty) return;
      let config; try { config = collectPluginConfig(); } catch (error) { setPluginStatus(error.message, true); return; }
      pluginPending = "config"; syncPluginControls(); setPluginStatus("Saving configuration…");
      try {
        pluginDetail = (await request(`/plugins/${encodeURIComponent(pluginDetail.id)}/config`, {method: "PUT", body: JSON.stringify({config})})).plugin;
        renderPluginDetail(); setPluginStatus("Configuration saved for this project. New tool calls use these settings.");
        if (pluginDetail.id === "orchestration") await refreshOrchestrationProfiles();
      } catch (error) { setPluginStatus(`Could not save configuration: ${error.message || error}`, true); }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    async function checkPluginRuntime() {
      if (pluginPending || !pluginDetail?.enabled) return;
      pluginPending = "inspect"; syncPluginControls(); const status = pluginRuntimeStatus; status.textContent = "Checking isolated runtime…";
      try {
        const result = (await request(`/plugins/${encodeURIComponent(pluginDetail.id)}/inspect`, {method: "POST", body: "{}"})).plugin;
        status.textContent = `Runtime ${result.state || "ready"}${result.runtime_version ? ` · Cordis ${result.runtime_version}` : ""}. ${result.runtime_lifetime === "persistent" ? "The isolated worker stays active between calls." : result.service ? "Hosted by Libre Claw over a private local socket." : "Checked with current permissions; the temporary runtime has stopped."}`;
        status.className = "hint";
      } catch (error) { status.textContent = `Runtime check failed: ${error.message || error}`; status.className = "hint danger"; }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    function showPluginRemoval(container) {
      if (pluginPending) return;
      const name = String(pluginDetail.name || pluginDetail.id); container.replaceChildren();
      container.append(pluginNode("h4", "danger", `Remove ${name}?`), pluginNode("p", "hint", "This deletes the package and its grants, configuration, and private state across all projects. This cannot be undone."));
      const actions = pluginNode("div", "plugin-actions"); actions.append(pluginButton("Keep plugin", renderPluginDetail), pluginButton(`Remove ${name}`, () => { void removePlugin(); }, "danger")); container.append(actions);
    }
    async function removePlugin() {
      if (pluginPending || !pluginDetail) return;
      pluginPending = "remove"; syncPluginControls(); const {id, name} = pluginDetail;
      try {
        await request(`/plugins/${encodeURIComponent(id)}`, {method: "DELETE"});
        disposeOrchestrationEditor();
        pluginDetail = null; $("pluginPage").replaceChildren(); showPluginView("list");
        await loadPlugins(`${name || id} removed from all projects.`);
        if (id === "orchestration") await refreshOrchestrationProfiles();
      } catch (error) { setPluginStatus(`Could not remove plugin: ${error.message || error}`, true); }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    async function openPluginInstall(source = "") {
      if (pluginPending || pluginLoading) return;
      await leavePluginPage(); pluginInstall = {source, stage: "source", preview: null}; showPluginView("install"); renderPluginInstall(); setPluginStatus("");
      if (source) await previewPlugin();
    }
    function renderPluginInstall() {
      const page = $("pluginPage"); page.replaceChildren(); page.append(pluginBreadcrumb("Add plugin"));
      page.append(pluginNode("h3", "plugin-page-title", pluginInstall.stage === "done" ? "Ready when you are" : "Add a plugin"));
      if (pluginInstall.stage === "source") {
        page.append(pluginNode("p", "plugin-description", "Bring a trusted Cordis package into your workspace."));
        const form = pluginNode("form", "plugin-install-form"), label = pluginNode("label"); label.append(pluginNode("span", "", "Package source"));
        const input = pluginNode("input"); input.id = "pluginSource"; input.value = pluginInstall.source;
        input.placeholder = "/path/to/plugin or npm:@scope/package@1.0.0"; input.autocomplete = "off"; input.spellcheck = false;
        bindUI("plugins", input, "input", () => { pluginInstall.source = input.value; }); label.append(input); form.append(label);
        const help = pluginNode("details", "plugin-source-help"); help.append(pluginNode("summary", "", "What can I install?"),
          pluginNode("p", "hint", "A compiled Cordis or Harness package from a local folder, .tgz archive, public GitHub repository, or npm. Packages are downloaded only when you check them. Install scripts never run.")); form.append(help);
        const actions = pluginNode("div", "plugin-actions"), cancel = pluginButton("Cancel", () => { void leavePluginPage(); }); cancel.dataset.cancelPreview = "true";
        actions.append(cancel, pluginButton("Check package", () => { void previewPlugin(); }, "primary")); form.append(actions);
        bindUI("plugins", form, "submit", event => { event.preventDefault(); void previewPlugin(); }); page.append(form);
      } else {
        const item = pluginInstall.preview, card = pluginNode("article", "plugin-card");
        card.append(pluginNode("h4", "", item.name || item.id), pluginNode("p", "tiny", `${item.id} · ${item.version}`));
        if (item.description) card.append(pluginNode("p", "plugin-description", item.description));
        const toolCount = item.tool_count ?? item.tools?.length ?? 0;
        card.append(pluginNode("p", "plugin-tools", item.adapter ? "Hosted provider service · Private local connection" : item.format === "deepseek-harness" && !toolCount ? "Tools are discovered when you enable this package" : `${toolCount} tool${toolCount === 1 ? "" : "s"} · Offline by default`));
        const reviewTools = pluginNode("details", "plugin-tool-detail"); reviewTools.append(pluginNode("summary", "", "Review declared tools"));
        for (const tool of item.tool_definitions || (item.tools || []).map(name => ({name}))) {
          const definition = pluginNode("details", "plugin-tool-detail"); definition.append(pluginNode("summary", "", tool.name || tool.id || "Tool"));
          if (tool.description) definition.append(pluginNode("p", "hint", tool.description));
          if (tool.parameters || tool.input_schema) definition.append(pluginNode("pre", "plugin-json", JSON.stringify(tool.parameters || tool.input_schema, null, 2)));
          reviewTools.append(definition);
        }
        if (toolCount) card.append(reviewTools);
        if (pluginInstall.stage === "done") {
          card.append(pluginNode("p", "hint", item.enabled ? "Installed and already enabled for this project. Existing permissions are unchanged." : "Installed and disabled. Enable it for this project when you’re ready."));
        } else {
          card.append(pluginNode("p", "hint", "The package has been checked without executing it. Installation keeps it disabled until you enable it."));
          const verify = pluginNode("details", "plugin-tool-detail"); verify.append(pluginNode("summary", "", "Package fingerprint"), pluginNode("code", "plugin-digest", item.digest || "")); card.append(verify);
        }
        if (pluginInstall.stage !== "done" && pluginCatalog?.plugins.some(plugin => plugin.id === item.id)) {
          card.append(pluginNode("p", "hint danger", "This replaces an installed package. If its files changed, saved grants and configuration will be revoked in every project."));
        }
        const actions = pluginNode("div", "plugin-actions");
        if (pluginInstall.stage === "done") {
          actions.append(pluginButton("Done", () => { void leavePluginPage(); }));
          if (item.enabled) actions.append(pluginButton("Open plugin", () => { pluginInstall = null; void openPluginDetail(String(item.id)); }, "primary"));
          else {
            const enable = item.adapter === "native-provider"
              ? pluginButton("Enable with model access", () => { void enablePluginModels(String(item.id)); }, "primary")
              : pluginButton("Enable now", () => { void enableInstalledPlugin(); }, "primary");
            enable.dataset.blocked = String(!pluginCatalog?.enabled); actions.append(enable);
            if (item.adapter === "native-provider") card.append(pluginNode("p", "hint", "Enabling lets local clients use your configured models. Provider keys remain in Libre Claw."));
          }
        } else actions.append(pluginButton("Back", () => { void backPluginInstall(); }), pluginButton("Install plugin", () => { void installPlugin(); }, "primary"));
        card.append(actions); page.append(card);
      }
      syncPluginControls();
    }
    async function previewPlugin() {
      if (pluginPending || !pluginInstall) return;
      const source = pluginInstall.source.trim(); if (!source) { setPluginStatus("Enter a package source first.", true); return; }
      const revision = ++pluginRevision;
      pluginPending = "preview"; syncPluginControls(); setPluginStatus("Checking package metadata and files…");
      try {
        const preview = await request("/plugins/preview", {method: "POST", body: JSON.stringify({source})});
        if (revision !== pluginRevision) {
          // Discard an abandoned check; the server also expires unclaimed previews.
          try { await request(`/plugins/preview/${encodeURIComponent(preview.token)}`, {method: "DELETE"}); } catch { /* Expiry provides cleanup if the connection was lost. */ }
          return;
        }
        pluginInstall.preview = preview; pluginInstall.stage = "preview"; renderPluginInstall(); setPluginStatus("Package checked. Review it before installing.");
      } catch (error) {
        if (revision === pluginRevision) setPluginStatus(`Could not check package: ${error.message || error}. Edit the source and try again.`, true);
      } finally { if (revision === pluginRevision) pluginPending = ""; syncPluginControls(); }
    }
    async function backPluginInstall() {
      if (pluginPending) return;
      const token = pluginInstall.preview?.token;
      let cleanupError = "";
      if (token) {
        pluginPending = "discard"; syncPluginControls();
        try { await request(`/plugins/preview/${encodeURIComponent(token)}`, {method: "DELETE"}); }
        catch (error) { cleanupError = `Could not discard preview: ${error.message || error}. Unused previews expire automatically.`; }
        finally { pluginPending = ""; }
      }
      pluginInstall.preview = null; pluginInstall.stage = "source"; renderPluginInstall(); setPluginStatus(cleanupError, Boolean(cleanupError));
    }
    async function installPlugin() {
      if (pluginPending || !pluginInstall?.preview?.token) return;
      pluginPending = "install"; syncPluginControls(); setPluginStatus("Installing the checked package…");
      try {
        const result = await request("/plugins/install", {method: "POST", body: JSON.stringify({token: pluginInstall.preview.token})});
        pluginInstall.preview = {...pluginInstall.preview, ...result.plugin, token: null}; pluginInstall.stage = "done";
        await loadPlugins(`${pluginInstall.preview.name || pluginInstall.preview.id} installed. ${pluginInstall.preview.enabled ? "It remains enabled for this project." : "It is disabled."}`); renderPluginInstall();
      } catch (error) {
        pluginInstall.preview = null; pluginInstall.stage = "source"; renderPluginInstall();
        setPluginStatus(`Could not install plugin: ${error.message || error}. Check the package again to retry.`, true);
      }
      finally { pluginPending = ""; syncPluginControls(); }
    }
    async function enableInstalledPlugin() {
      if (pluginPending || pluginInstall?.stage !== "done") return;
      const id = String(pluginInstall.preview.id);
      if (!pluginCatalog?.plugins.some(plugin => plugin.id === id && plugin.enabled)) await togglePlugin(id);
      if (pluginCatalog?.plugins.some(plugin => plugin.id === id && plugin.enabled)) {
        pluginInstall = null; await openPluginDetail(id);
      }
    }

    /* Settings modal */
    const PANES = ["general", "models", "engine", "plugins", "schedules", "usage", "about"];
    let settingsReturnFocus = null;
    function openSettingsPane(pane) {
      if (!PANES.includes(pane)) return;
      if (pane !== "plugins" && pluginPending && pluginPending !== "preview") { setPluginStatus("Wait for the plugin operation to finish before changing sections."); return; }
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
      if (pane === "plugins" && pluginView === "list") void loadPlugins();
      if (pane === "engine") void refreshEngine();
      if (pane !== "plugins" && (!pluginPending || pluginPending === "preview")) void leavePluginPage();
      if (pane === "usage") void loadUsagePane();
    }
    function closeSettingsPanel() {
      if ($("settingsOverlay").hidden) return;
      if (pluginPending && pluginPending !== "preview") { setPluginStatus("Wait for the plugin operation to finish before closing."); return; }
      void leavePluginPage();
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
      tabs.forEach((tab, index) => bindUI("appearance", tab, "keydown", (event) => {
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
      const isOpenCode = provider === "opencode" || provider === "opencode-go";
      $("llamacppSettings").hidden = provider !== "llamacpp";
      $("providerAuthLink").hidden = !isOpenCode;
      $("providerRouteHint").textContent = isOpenCode
        ? `${providerLabel(provider)} uses an OpenCode API key. Connect with /setup ${provider} in the terminal UI, or libre-claw auth set-key ${provider}, then refresh models.`
        : provider === "codex"
        ? "Uses your Codex CLI ChatGPT sign-in. Run libre-claw auth codex-login --browser, then refresh models."
        : provider === "deepseek"
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

    let llamacppGeneration = 0, llamacppPending = false;

    function setLlamacppStatus(message, error = false) {
      $("llamacppStatus").textContent = message;
      $("llamacppStatus").className = error ? "hint danger" : "hint";
    }

    function setLlamacppPending(pending, action = "") {
      llamacppPending = pending;
      $("llamacppDiscover").disabled = pending;
      $("llamacppSave").disabled = pending;
      $("llamacppForm").setAttribute("aria-busy", String(pending));
      $("llamacppDiscover").textContent = pending && action === "discover" ? "Discovering…" : "Discover";
      $("llamacppSave").textContent = pending && action === "save" ? "Saving…" : "Save endpoint";
    }

    bindUI("models", $("llamacppBaseUrl"), "input", () => {
      ++llamacppGeneration;
      $("llamacppDiscovered").replaceChildren();
      setLlamacppStatus("Endpoint changed. Discover models or save this connection.");
    });

    async function loadLlamacppConfig() {
      const generation = llamacppGeneration, value = $("llamacppBaseUrl").value;
      try {
        const payload = await request("/config/llamacpp");
        if (generation !== llamacppGeneration || value !== $("llamacppBaseUrl").value || llamacppPending) return;
        $("llamacppBaseUrl").value = payload.base_url || "";
      } catch (_error) {
        /* endpoint config is optional */
      }
    }

    function renderDiscoveredChips(payload) {
      const box = $("llamacppDiscovered");
      box.replaceChildren();
      for (const item of payload.models || []) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "model-chip";
        chip.textContent = item.label || item.model;
        chip.title = `Use ${item.model}`;
        bindUI("models", chip, "click", () => {
          $("configProvider").value = "llamacpp";
          $("configProvider").dispatchEvent(new Event("change"));
          modelPickers.get("configModel")?.accept(payload, "llamacpp");
          $("configModel").value = item.model;
          $("configModel").dispatchEvent(new Event("input"));
          $("configModel").dispatchEvent(new Event("change"));
        });
        box.append(chip);
      }
    }

    bindUI("models", $("llamacppDiscover"), "click", async () => {
      if (llamacppPending) return;
      const generation = ++llamacppGeneration;
      const base = $("llamacppBaseUrl").value.trim();
      const query = base ? `?base_url=${encodeURIComponent(base)}` : "";
      setLlamacppPending(true, "discover");
      $("llamacppDiscovered").replaceChildren();
      setLlamacppStatus("Connecting and discovering models…");
      try {
        const payload = await request(`/models/llamacpp${query}`);
        if (generation !== llamacppGeneration || base !== $("llamacppBaseUrl").value.trim()) return;
        if (payload.error) throw new Error(payload.error);
        $("llamacppBaseUrl").value = payload.base_url || base;
        renderDiscoveredChips(payload);
        const count = (payload.models || []).length;
        setLlamacppStatus(count
          ? `${count} ${count === 1 ? "model" : "models"} found at ${payload.base_url || base}. Choose a model and save this endpoint to use it.`
          : "Connected, but this endpoint did not report any models. Check the server's loaded models.");
      } catch (error) {
        if (generation !== llamacppGeneration || base !== $("llamacppBaseUrl").value.trim()) return;
        setLlamacppStatus(`Could not discover models: ${error.message || error}`, true);
      } finally {
        setLlamacppPending(false);
      }
    });

    bindUI("models", $("llamacppForm"), "submit", async (event) => {
      event.preventDefault();
      if (llamacppPending) return;
      const generation = ++llamacppGeneration;
      const base = $("llamacppBaseUrl").value.trim();
      setLlamacppPending(true, "save");
      setLlamacppStatus("Saving endpoint…");
      try {
        const payload = await request("/config/llamacpp", {
          method: "PATCH",
          body: JSON.stringify({ base_url: base, persist_global: true }),
        });
        if (payload.error) throw new Error(payload.error);
        for (const [id, picker] of modelPickers) {
          if ($(id).dataset.modelProvider === "llamacpp") void picker.refresh(true);
        }
        if (generation !== llamacppGeneration || base !== $("llamacppBaseUrl").value.trim()) return;
        $("llamacppBaseUrl").value = payload.base_url || base;
        setLlamacppStatus(`Endpoint saved: ${payload.base_url || base}. Choose a model, then Save default.`);
      } catch (error) {
        if (generation !== llamacppGeneration || base !== $("llamacppBaseUrl").value.trim()) return;
        setLlamacppStatus(`Could not save endpoint: ${error.message || error}`, true);
      } finally {
        setLlamacppPending(false);
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
        $("usagePaneCached").textContent = formatCompactNumber(summary.cached_tokens);
        $("usagePaneCacheWrites").textContent = formatCompactNumber(summary.cache_write_tokens);
        const inputTokens = Number(summary.input_tokens) || 0;
        $("usagePaneCacheRatio").textContent = inputTokens > 0
          ? `${(Math.min(1, Math.max(0, Number(summary.cached_tokens) || 0) / inputTokens) * 100).toFixed(1)}%`
          : "—";
        usageTable(
          $("usageByModel"),
          ["Model", "Requests", "Input", "Reused", "Output", "Total", "Cost"],
          (summary.by_model || []).map((group) => [
            group.name || "unknown",
            formatExactNumber(group.requests),
            formatCompactNumber(group.input_tokens),
            formatCompactNumber(group.cached_tokens),
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

    bindUI("models", $("modelForm"), "submit", async (event) => {
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
      bindUI("models", input, "input", showCapabilities);
      bindUI("models", input, "change", showCapabilities);
      showCapabilities();
      const isConfig = input.id === "configModel";
      const applyCatalog = (payload) => {
        models = payload.models || [];
        discoveredDefault = payload.default_model || "";
        list.replaceChildren();
        for (const item of models) {
          const option = document.createElement("option");
          option.value = item.model;
          option.label = item.label || item.model;
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
      };
      const update = async (refresh = false) => {
        const requestId = ++generation;
        const provider = effectiveProvider();
        input.dataset.modelProvider = provider;
        list.replaceChildren(); models = []; discoveredDefault = ""; updatePlaceholder(); showCapabilities();
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
          applyCatalog(payload);
        } catch (_error) {
          if (requestId === generation) {
            input.title = "Discovery unavailable. Enter a model ID.";
            if (isConfig) $("modelDiscoveryStatus").textContent = "Discovery unavailable. Check your provider connection or enter a model ID.";
          }
        } finally {
          if (isConfig && requestId === generation) $("refreshModels").disabled = false;
        }
      };
      bindUI("models", select, "change", () => {
        input.value = "";
        if (input.id === "configModel") {
          ++modelConfigGeneration;
          syncProviderSettings();
        }
        void update();
      });
      bindUI("models", input, "focus", () => { void update(); });
      modelPickers.set(input.id, {
        refresh: update,
        accept: (payload, provider) => {
          if (effectiveProvider() !== provider) return;
          ++generation;
          applyCatalog(payload);
          if (isConfig) $("refreshModels").disabled = false;
        },
      });
    }

    syncModelDatalist($("runProvider"), $("runModel"));
    syncModelDatalist($("configProvider"), $("configModel"));
    syncModelDatalist($("automationProvider"), $("automationModel"));
    bindUI("models", $("configModel"), "input", () => { ++modelConfigGeneration; });
    bindUI("models", $("refreshModels"), "click", () => { void modelPickers.get("configModel").refresh(true); });
    void loadModelConfig();

    const workflow = { review: null, reviewContext: {}, comments: [], reviewGeneration: 0, planGeneration: 0, planSignature: "", workerSignature: "", workers: [], workerDrafts: new Map(), workerPending: new Set(), worktrees: [], transfer: null };

    function workflowButton(label, action, className = "") {
      const button = document.createElement("button");
      button.type = "button"; button.textContent = label; button.className = className;
      bindUI("workflows", button, "click", async () => {
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
      bindUI("workflows", form, "submit", async (event) => {
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
                bindUI("workflows", numberCell, "click", () => commentEditor(block, file, number, side, snapshot, context));
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
        const details = [["Worker", worker.worker_name || worker.name || worker.id], ["Role", worker.role || "worker"], ["Provider / model", `${worker.provider || "Unknown"} / ${worker.model || "Unknown"}`], ["Scope", worker.scope || "Unknown"], ["Access", worker.read_only ? "Read only" : "Writes within declared paths"], ["Tools remaining", controls.tools === null ? "Unknown" : String(Math.floor(controls.tools))], ["Time remaining", controls.seconds === null ? "Unknown" : `${Math.ceil(controls.seconds)} seconds`]];
        if (worker.usage) details.push(["Tokens used", String(worker.usage.total_tokens ?? (Number(worker.usage.input_tokens || 0) + Number(worker.usage.output_tokens || 0)))]);
        if (worker.write_paths?.length) details.push(["Write paths", worker.write_paths.join(", ")]);
        for (const [label, value] of details) { const term = document.createElement("dt"), detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; meta.append(term, detail); }
        card.append(meta);
        if (worker.error) { const error = document.createElement("p"); error.className = "hint worker-error"; error.textContent = worker.error; card.append(error); }
        if (worker.output) { const result = document.createElement("pre"); result.className = "worker-results"; result.setAttribute("aria-label", `Result from worker ${worker.id}`); result.textContent = worker.output; card.append(result); }
        if (["interrupted", "failed", "cancelled"].includes(worker.status) || controls.pending) {
          const input = document.createElement("textarea"); input.rows = 2; input.placeholder = "Optional guidance before resuming"; input.setAttribute("aria-label", `Resume guidance for ${worker.id}`); input.dataset.workerGuidance = key; input.value = workflow.workerDrafts.get(key) || "";
          input.disabled = pending || controls.pending || !controls.canResume;
          bindUI("workflows", input, "input", () => workflow.workerDrafts.set(key, input.value));
          const resume = document.createElement("button"); resume.type = "button"; resume.textContent = controls.pending ? "Resume queued" : pending ? "Requesting resume..." : "Resume worker"; resume.disabled = pending || !controls.canResume;
          bindUI("workflows", resume, "click", () => sendWorkerControl(runId, worker, "agent_resume", input.value));
          card.append(input, resume);
          if (!controls.canResume && !controls.pending) { const reason = document.createElement("p"); reason.className = "hint"; reason.textContent = controls.tools === null || controls.seconds === null ? "Saved budget is unavailable." : "This worker has used its tool or time budget."; card.append(reason); }
          if (key === focusedKey && !input.disabled) restoreFocus = input;
        }
        if (controls.active) {
          const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "danger"; cancel.textContent = pending ? "Cancelling..." : "Cancel worker"; cancel.disabled = pending;
          bindUI("workflows", cancel, "click", () => sendWorkerControl(runId, worker, "agent_cancel")); card.append(cancel);
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
          bindUI("workflows", check, "change", () => sendTaskControl("plan", `${check.checked ? "done" : "pending"} ${index + 1}`));
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
          bindUI("workflows", form, "submit", async (event) => {
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

    bindUI("workflows", $("reviewScope"), "change", () => { $("reviewBase").hidden = $("reviewScope").value !== "branch"; void loadReview(); });
    bindUI("workflows", $("reviewBase"), "change", loadReview);
    bindUI("workflows", $("refreshReview"), "click", loadReview);
    bindUI("workflows", $("analyzeReview"), "click", analyzeReview);
    bindUI("workflows", $("refreshPlan"), "click", () => loadPlan(true));
    bindUI("workflows", $("planMode"), "change", () => sendTaskControl("plan", $("planMode").value === "plan" ? "on" : "off"));
    bindUI("workflows", $("planForm"), "submit", async (event) => { event.preventDefault(); const input = $("planNewStep"); if (await sendTaskControl("plan", `add ${input.value}`)) input.value = ""; });
    bindUI("workflows", $("refreshWorktrees"), "click", loadWorktrees);
    bindUI("workflows", $("worktreeForm"), "submit", async (event) => {
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
    bindUI("workflows", $("applyTransfer"), "click", async () => {
      const preview = workflow.transfer; if (!preview) return; $("applyTransfer").disabled = true;
      try {
        await request(`/worktrees/${preview.worktree.worktree_id}/transfer`, {method:"POST", body:JSON.stringify({revision:preview.review.revision,target_revision:preview.target_revision})});
        workflow.transfer = null; $("transferPanel").hidden = true; setNotice("Changes transferred to the original checkout."); await loadWorktrees();
      } catch (error) { setNotice(error.message || String(error), true); }
      finally { $("applyTransfer").disabled = !workflow.transfer; }
    });
    bindUI("workflows", $("closeTransfer"), "click", () => { workflow.transfer = null; $("transferPanel").hidden = true; });

    /* The composer continues the selected session; New Session starts a thread. */
    function composerMode() {
      if (!state.selectedRunId) return "new";
      if (STREAM_STATES.has(state.selectedRunState)) return "busy";
      return "reply";
    }

    function syncComposerMode() {
      const mode = composerMode();
      const team = mode === "new" && selectedOrchestrationPlugin, profile = selectedTeamProfile();
      $("runTeam").hidden = mode !== "new"; $("runTeam").disabled = mode !== "new" || state.sending;
      $("runProvider").hidden = mode !== "new" || Boolean(team); $("runProvider").disabled = mode !== "new" || Boolean(team);
      $("runModel").hidden = mode !== "new" || Boolean(team); $("runModel").disabled = mode !== "new" || Boolean(team);
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
      $("sendMessage").disabled = state.sending || Boolean(team && (!profile?.enabled || !profile.ready));
      const summary = $("runTeamSummary"); summary.hidden = !team && !state.selectedTeam;
      if (team) summary.textContent = profile?.enabled && profile.ready
        ? `Team: ${profile.name || profile.plugin_id} · ${profile.workers?.length || 0} worker roles · Lead: ${[profile.orchestrator?.provider, profile.orchestrator?.model].filter(Boolean).join(" / ") || "App default"}`
        : "Selected team is unavailable. Open Plugins to enable it or choose No team.";
      else if (mode !== "new" && state.selectedTeam) summary.textContent = `Team: ${state.selectedTeam} · This task keeps its saved team configuration`;
      else summary.hidden = true;
    }

    bindUI("tasks", $("runForm"), "submit", async (event) => {
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
          if (selectedOrchestrationPlugin) {
            const team = selectedTeamProfile();
            if (!team?.enabled || !team.ready) throw new Error("The selected team is unavailable. Enable it or choose No team.");
            body.orchestration_plugin = selectedOrchestrationPlugin;
          } else {
            if ($("runProvider").value.trim()) body.provider = $("runProvider").value.trim();
            if ($("runModel").value.trim()) body.model = $("runModel").value.trim();
          }
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

    bindUI("workflows", $("automationForm"), "submit", async (event) => {
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

    bindUI("tasks", $("runMessage"), "input", autoGrow);
    bindUI("tasks", $("runMessage"), "keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        $("runForm").requestSubmit();
      }
    });
    bindUI("tasks", $("refreshAll"), "click", refreshAll);
    bindUI("tasks", $("runSearch"), "input", renderRuns);
    bindUI("tasks", $("runStateFilter"), "change", renderRuns);
    bindUI("tasks", $("eventFilter"), "change", renderEvents);
    bindUI("tasks", $("tabChat"), "click", () => setView("chat"));
    bindUI("tasks", $("tabTrajectory"), "click", () => setView("trajectory"));
    bindUI("workflows", $("tabPlan"), "click", () => setView("plan"));
    bindUI("workflows", $("tabChanges"), "click", () => setView("changes"));
    bindUI("workflows", $("tabWorktrees"), "click", () => setView("worktrees"));
    bindUI("tasks", $("focusRunInput"), "click", newSession);
    bindUI("appearance", $("openSettings"), "click", () => openSettingsPane("general"));
    bindUI("plugins", $("openPlugins"), "click", () => openSettingsPane("plugins"));
    bindUI("engine", $("openEngine"), "click", () => openSettingsPane("engine"));
    bindUI("engine", $("engineStrip"), "click", () => openSettingsPane("engine"));
    bindUI("engine", $("pluginsOpenEngine"), "click", () => openSettingsPane("engine"));
    bindUI("plugins", $("engineOpenPlugins"), "click", () => openSettingsPane("plugins"));
    bindUI("engine", $("refreshEngine"), "click", () => { void refreshEngine(); });
    bindUI("engine", $("restartEngine"), "click", () => { void restartCoreEngine(); });
    bindUI("appearance", $("closeSettings"), "click", closeSettingsPanel);
    bindUI("appearance", $("settingsMask"), "click", closeSettingsPanel);
    bindUI("appearance", document, "keydown", handleSettingsKeydown);
    document.querySelectorAll(".settings-nav button").forEach((button) => {
      bindUI("appearance", button, "click", () => openSettingsPane(button.dataset.pane));
    });
    bindTabNavigation(".settings-nav button", button => openSettingsPane(button.dataset.pane));
    bindTabNavigation(".view-tab", button => button.click());
    bindUI("tasks", $("messageAction"), "change", syncComposerMode);
    bindUI("workflows", $("runTeam"), "change", () => { orchestrationLoaded = true; selectedOrchestrationPlugin = $("runTeam").value; syncComposerMode(); });
    bindUI("questions", $("questions"), "submit", event => {
      const record = [...questionForms.values()].find(item => item.form === event.target);
      if (!record) return;
      event.preventDefault(); void submitQuestion(record);
    });
    bindUI("workflows", $("automationRoute"), "change", syncAutomationRoute);
    bindUI("tasks", $("refreshUsagePane"), "click", loadUsagePane);
    bindUI("plugins", $("refreshPlugins"), "click", () => { void loadPlugins(); });
    bindUI("plugins", $("addPlugin"), "click", () => { void openPluginInstall(); });
    bindUI("plugins", $("pluginSearch"), "input", () => { if (pluginCatalog) renderPlugins(pluginCatalog); });
    bindUI("workflows", $("refreshSchedules"), "click", () => { void refreshAutomations().catch(error => setNotice(error.message || String(error), true)); });
    bindUI("workflows", $("cancelAutomationEdit"), "click", () => resetAutomationForm($("automationForm")));
    bindUI("tasks", $("cancelRun"), "click", async () => {
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
        const results = await Promise.allSettled([refreshHealth(), refreshUsage(), refreshAutomations(), refreshEngine(), refreshOrchestrationProfiles()]);
        if (results[0].status === "rejected") {
          engineActiveRuns = null; syncEngineControls();
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

    async function mountDashboardServices() {
      try {
        const {mountDashboard} = await import("/assets/cordis-ui.mjs");
        const features = Object.fromEntries(Object.entries(uiBindings).map(([id, bindings]) => [id, {bindings}]));
        features.appearance.setup = () => { initTheme(); initRail(); initMobileSidebar(); };
        features.appearance.dispose = () => { cancelUI(noticeTimer); };
        features.plugins.dispose = () => { void closePluginClient(); };
        features.tasks.intervals = [{handler: refreshAll, milliseconds: 3000}];
        features.tasks.dispose = () => { state.streaming = false; cancelUI(streamTimer); resetStreamNode(); };
        dashboardUI = await mountDashboard({scope: document, features, onError: error => setNotice(error.message || String(error), true)});
        for (const bindings of Object.values(uiBindings)) bindings.length = 0;
        document.documentElement.dataset.uiEngine = "cordis";
        document.documentElement.dataset.uiServices = String(dashboardUI.inspect().components.filter(component => component.state === "ACTIVE").length);
        resolveDashboard(dashboardUI);
        clearSelectedRun();
        void loadWorktrees();
        void refreshAll();
      } catch (error) {
        rejectDashboard(error);
        $("sendMessage").disabled = true;
        setNotice(`Dashboard services could not start: ${error.message || error}. Reload to retry.`, true);
      }
    }
    void mountDashboardServices();
  </script>
</body>
</html>
"""
