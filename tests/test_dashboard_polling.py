# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess

import pytest

from libre_claw.web.dashboard import dashboard_html


POLLING_DOM = r"""
const assert = require('node:assert/strict');
let now = 0, active = true;
Date.now = () => now;
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {hidden: true, textContent: '', title: '', disabled: false});
  return elements.get(id);
};
const document = {hidden: false};
const state = {sending: false, streaming: false, selectedRunId: '', runs: [], events: [], view: 'chat'};
const STREAM_STATES = new Set(['queued', 'running', 'blocked']);
let engineActiveRuns = 0, healthActive = 0, serverRuns = [], streamTimer = 0, streamGeneration = 0;
const stream = {raf: 0};
const dashboardUI = {active: () => active};
const timers = new Set();
const cancelUI = dispose => { if (typeof dispose === 'function') dispose(); };
const timeoutUI = (_service, handler, delay) => {
  const timer = {handler, delay, at: now + delay}; timers.add(timer);
  return () => timers.delete(timer);
};
async function advance(milliseconds) {
  const target = now + milliseconds;
  while (true) {
    const timer = [...timers].sort((a, b) => a.at - b.at)[0];
    if (!timer || timer.at > target) break;
    now = timer.at; timers.delete(timer); await timer.handler();
  }
  now = target;
}
const calls = {health: 0, runs: 0, usage: 0, engine: 0, automations: 0, profiles: 0, detail: 0};
let failHealth = false, failRuns = false;
const refreshHealth = async () => { calls.health++; if (failHealth) throw new Error('Daemon unavailable'); engineActiveRuns = healthActive; };
const refreshRuns = async () => { calls.runs++; if (failRuns) throw new Error('Run list unavailable'); state.runs = serverRuns; };
const refreshAutomations = async () => { calls.automations++; };
const refreshEngine = async () => { calls.engine++; };
const refreshOrchestrationProfiles = async () => { calls.profiles++; };
const refreshRunDetail = async () => { calls.detail++; state.streaming = healthActive > 0; };
const loadPlan = async () => {};
const syncEngineControls = () => {};
const notices = [];
const setNotice = value => notices.push(value);
const formatCompactNumber = value => String(value || 0);
const formatExactNumber = value => String(value || 0);
const formatCost = value => String(value ?? 'unknown');
const formatShortTime = value => String(value);
const usageTable = (table, headers, rows) => { table.rows = rows; };
let holdUsage = false, finishUsage, failUsage = false;
const request = async path => {
  assert.equal(path, '/usage?limit=250'); calls.usage++;
  if (holdUsage) await new Promise(resolve => { finishUsage = resolve; });
  if (failUsage) throw new Error('Usage unavailable');
  return {summary: {total_tokens: calls.usage * 100, runs: 1, requests: 2, input_tokens: 90, cached_tokens: 45}, records: []};
};
"""


def run_polling_ui(tmp_path, assertions: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard interaction tests")
    html = dashboard_html()
    usage = html[html.index("    /* Shared usage snapshot:"):html.index("    async function refreshRuns()")]
    pane = html[html.index("    function renderUsagePane("):html.index('    bindUI("models", $("modelForm")')]
    polling = html[html.index("    /* One adaptive poll"):html.index("    async function mountDashboardServices()")]
    script = tmp_path / "dashboard-polling.cjs"
    script.write_text(POLLING_DOM + usage + pane + polling + "\n(async () => {\n" + assertions + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_idle_refreshes_every_30_seconds_and_active_tasks_every_3(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
await refreshAll({automatic: true});
assert.deepEqual(calls, {health: 1, runs: 1, usage: 1, engine: 1, automations: 1, profiles: 1, detail: 0});
assert.equal(timers.size, 1);
assert.equal([...timers][0].delay, 30000);
await advance(29999);
assert.equal(calls.health, 1);
await advance(1);
assert.equal(calls.health, 2); assert.equal(calls.usage, 2);
healthActive = 1; serverRuns = [{run_id: 'busy', state: 'running'}];
await refreshAll({force: true});
assert.equal([...timers][0].delay, 3000);
const auxiliaryCalls = calls.automations, engineCalls = calls.engine;
await advance(3000);
assert.equal(calls.health, 4); assert.equal(calls.usage, 4);
assert.equal(calls.automations, auxiliaryCalls); assert.equal(calls.engine, engineCalls);
$('settingsOverlay').hidden = false; $('paneEngine').hidden = false;
await advance(3000);
assert.equal(calls.engine, engineCalls + 1);
healthActive = 0; serverRuns = [{run_id: 'busy', state: 'done'}];
await advance(3000);
assert.equal([...timers][0].delay, 30000);
assert.equal(calls.usage, 6);
assert.deepEqual(notices, []);
""")


def test_hidden_tab_stops_requests_and_visibility_resumes_immediately(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
healthActive = 1; serverRuns = [{run_id: 'busy', state: 'running'}];
await refreshAll();
streamTimer = timeoutUI('tasks', () => { throw new Error('Hidden stream should stop'); }, 300);
stream.raf = timeoutUI('tasks', () => { throw new Error('Hidden animation should stop'); }, 16);
document.hidden = true;
handleDashboardVisibility();
assert.equal(timers.size, 0);
const before = {...calls};
await advance(90000);
await refreshAll({automatic: true});
assert.deepEqual(calls, before);
state.streaming = true; state.selectedRunId = 'busy';
document.hidden = false;
await handleDashboardVisibility();
assert.equal(calls.health, before.health + 1);
assert.equal(calls.usage, before.usage + 1);
assert.equal(calls.detail, 1);
assert.equal(timers.size, 1);
assert.equal([...timers][0].delay, 3000);
""")


def test_run_list_failure_does_not_falsely_report_daemon_disconnected(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
$('daemonStatus').textContent = 'Connected';
failRuns = true;
await refreshAll();
assert.equal($('daemonStatus').textContent, 'Connected');
assert.equal(engineActiveRuns, 0);
assert.match(notices.at(-1), /Run list unavailable/);
failRuns = false; failHealth = true;
await refreshAll();
assert.equal($('daemonStatus').textContent, 'Disconnected');
assert.equal(engineActiveRuns, null);
assert.match(notices.at(-1), /Daemon unavailable/);
""")


def test_usage_pane_and_status_share_cache_inflight_and_forced_refresh(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
holdUsage = true;
const overview = refreshUsage();
$('settingsOverlay').hidden = false; $('paneUsage').hidden = false;
const pane = loadUsagePane();
assert.equal(calls.usage, 1);
finishUsage(); await Promise.all([overview, pane]);
assert.equal($('usageTokens').textContent, '100');
assert.equal($('usagePaneTokens').textContent, '100');
assert.equal($('usagePaneCacheRatio').textContent, '50.0%');
holdUsage = false;
await loadUsagePane(); await refreshUsage();
assert.equal(calls.usage, 1);
await loadUsagePane(true);
assert.equal(calls.usage, 2);
assert.equal($('usageTokens').textContent, '200');
assert.equal($('usagePaneTokens').textContent, '200');
now += 30000;
await refreshUsage();
assert.equal(calls.usage, 3);
failUsage = true;
await loadUsagePane(true);
assert.match($('usagePaneStatus').textContent, /Usage unavailable/);
assert.equal($('refreshUsagePane').disabled, false);
failUsage = false;
await loadUsagePane(true);
assert.equal(calls.usage, 5);
assert.equal($('usagePaneTokens').textContent, '500');
""")



def test_completion_during_usage_read_gets_one_fresh_followup(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
holdUsage = true;
const oldRead = refreshUsage();
const completedTurn = refreshUsage(true);
const anotherRefresh = refreshUsage(true);
assert.equal(calls.usage, 1);
finishUsage(); await oldRead;
await Promise.resolve();
assert.equal(calls.usage, 2);
assert.equal($('usageTokens').textContent, '100');
finishUsage(); await Promise.all([completedTurn, anotherRefresh]);
assert.equal(calls.usage, 2);
assert.equal($('usageTokens').textContent, '200');
holdUsage = false;
await refreshUsage();
assert.equal(calls.usage, 2);
""")



def test_queued_usage_followup_stops_when_page_becomes_hidden(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
holdUsage = true;
const pending = refreshUsage(), forced = refreshUsage(true);
document.hidden = true;
handleDashboardVisibility();
finishUsage(); await Promise.all([pending, forced]);
assert.equal(calls.usage, 1);
assert.equal(timers.size, 0);
holdUsage = false;
document.hidden = false;
await handleDashboardVisibility();
assert.equal(calls.usage, 2);
""")


def test_slow_poll_is_coalesced_and_cannot_restart_after_hidden_or_disposal(tmp_path) -> None:
    run_polling_ui(tmp_path, r"""
holdUsage = true;
const first = refreshAll();
await Promise.resolve(); await Promise.resolve();
const duplicate = refreshAll({force: true});
assert.equal(first, duplicate);
assert.equal(calls.health, 1); assert.equal(calls.usage, 1);
document.hidden = true; handleDashboardVisibility();
finishUsage(); await first;
assert.equal(timers.size, 0);
holdUsage = false;
document.hidden = false;
await handleDashboardVisibility();
assert.equal(calls.health, 2); assert.equal(calls.usage, 2);
const pending = refreshAll({force: true});
active = false;
await pending;
assert.equal(timers.size, 0);
""")


def test_run_completion_forces_usage_and_stream_stays_paused_when_hidden(tmp_path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard interaction tests")
    html = dashboard_html()
    poll = html[html.index("    async function pollRunEvents()"):html.index("    function setView(")]
    schedule = html[html.index("    function scheduleStream("):html.index("    function nearBottom(")]
    script = tmp_path / "dashboard-stream.cjs"
    script.write_text(r"""
const assert = require('node:assert/strict');
let state = {selectedRunId: 'run', streaming: true, events: []}, streamTimer = 0, streamGeneration = 0;
const document = {hidden: true}, STREAM_STATES = new Set(['queued', 'running', 'blocked']);
let requests = 0, usage = 0, scheduled = 0;
const request = async () => { requests++; return {events: [{type: 'run_finished'}]}; };
const lastNumericEventId = () => 0;
const resetStreamNode = () => {};
const renderEvents = () => {};
const refreshRunDetail = async () => { scheduleStream('done'); };
const refreshRuns = async () => {};
const refreshUsage = async force => { assert.equal(force, true); usage++; };
const tryAppendStream = () => false;
const cancelUI = () => {};
const scheduleDashboardRefresh = () => {};
const $ = () => ({});
const timeoutUI = () => { scheduled++; };
""" + schedule + poll + r"""
(async () => {
  scheduleStream('running');
  await pollRunEvents();
  assert.equal(requests, 0); assert.equal(scheduled, 0);
  document.hidden = false;
  await pollRunEvents();
  assert.equal(requests, 1); assert.equal(usage, 1);
  assert.equal(state.streaming, false); assert.equal(scheduled, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
