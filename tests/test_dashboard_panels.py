# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from libre_claw.core.themes import THEME_ALIASES, THEME_PALETTES
from libre_claw.web.dashboard import dashboard_html


def run_script(tmp_path, source: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard interactions")
    script = tmp_path / "panels.js"
    script.write_text("const bindUI = (_component, target, event, handler, options) => target.addEventListener(event, handler, options);\n" + source)
    completed = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


class Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if "id" in attributes:
            self.elements[attributes["id"]] = attributes


def test_dashboard_tab_targets_and_accessible_controls() -> None:
    parser = Elements()
    parser.feed(dashboard_html())
    elements = parser.elements
    for element in elements.values():
        for target in (element.get("aria-controls") or "").split():
            assert target in elements
        for label in (element.get("aria-labelledby") or "").split():
            assert label in elements
    assert "hidden" in elements["settingsOverlay"]
    assert elements["settingsOverlay"]["aria-modal"] == "true"
    assert elements["runMessage"]["aria-label"]
    assert elements["runSearch"]["aria-label"]
    assert elements["settingsNotice"]["role"] == "status"


MODAL_DOM = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(id) {
    this.id = id; this.hidden = false; this.disabled = false; this.tabIndex = 0;
    this.dataset = {}; this.attrs = {}; this.listeners = {}; this.isConnected = true;
    this.classes = new Set();
    this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name),
      toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name)};
  }
  setAttribute(name, value) { this.attrs[name] = value; }
  focus() { document.activeElement = this; }
  closest() { return this.hidden ? this : null; }
  getClientRects() { return this.hidden ? [] : [{}]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
const names = ['general', 'models', 'engine', 'plugins', 'schedules', 'usage', 'about'];
const elements = Object.fromEntries(['appFrame', 'settingsOverlay', 'settingsNotice', 'openSettings', 'closeSettings',
  ...names.map(name => 'pane' + name[0].toUpperCase() + name.slice(1))].map(id => [id, new Element(id)]));
elements.settingsOverlay.hidden = true;
const tabs = names.map(pane => { const tab = new Element(pane); tab.dataset.pane = pane; return tab; });
const $ = id => elements[id];
const document = {activeElement: elements.openSettings, querySelectorAll: () => tabs};
elements.settingsOverlay.querySelectorAll = () => [...tabs, elements.closeSettings];
elements.settingsOverlay.contains = element => [...tabs, elements.closeSettings].includes(element);
let modelLoads = 0;
const loadModelConfig = () => { modelLoads++; };
const loadLlamacppConfig = () => {};
const refreshAutomations = async () => {};
const loadUsagePane = () => {};
const refreshEngine = async () => {};
let pluginLoads = 0;
const loadPlugins = async () => { pluginLoads++; };
let pluginView = 'list', pluginPending = '', pluginLeaves = 0, pluginStatus = '';
const leavePluginPage = async () => { pluginLeaves++; pluginPending = ''; pluginView = 'list'; };
const setPluginStatus = message => { pluginStatus = message; };
const setNotice = () => {};
const closeMobileSidebar = () => {};
const keyboard = (key, shiftKey = false) => ({key, shiftKey, prevented: false, preventDefault() { this.prevented = true; }});
"""


def test_settings_traps_focus_restores_trigger_and_supports_keyboard_tabs(tmp_path) -> None:
    html = dashboard_html()
    source = html[html.index("    const PANES ="):html.index("    async function loadModelConfig(")]
    run_script(tmp_path, MODAL_DOM + source + r"""
openSettingsPane('general');
assert.equal(elements.settingsOverlay.hidden, false);
assert.equal(elements.appFrame.inert, true);
assert.equal(document.activeElement, tabs[0]);
assert.equal(elements.paneGeneral.hidden, false);
assert.equal(elements.paneModels.hidden, true);
assert.equal(tabs[0].attrs['aria-selected'], 'true');
assert.equal(tabs[1].tabIndex, -1);
elements.closeSettings.focus();
const forward = keyboard('Tab'); handleSettingsKeydown(forward);
assert.equal(forward.prevented, true); assert.equal(document.activeElement, tabs[0]);
const backward = keyboard('Tab', true); handleSettingsKeydown(backward);
assert.equal(backward.prevented, true); assert.equal(document.activeElement, elements.closeSettings);
bindTabNavigation('.settings-nav button', tab => openSettingsPane(tab.dataset.pane));
tabs[0].listeners.keydown(keyboard('ArrowDown'));
assert.equal(document.activeElement, tabs[1]); assert.equal(elements.paneModels.hidden, false);
assert.equal(elements.paneGeneral.hidden, true); assert.equal(modelLoads, 1);
tabs[1].listeners.keydown(keyboard('ArrowDown'));
assert.equal(document.activeElement, tabs[2]); assert.equal(elements.paneEngine.hidden, false);
tabs[2].listeners.keydown(keyboard('ArrowDown'));
assert.equal(document.activeElement, tabs[3]); assert.equal(elements.panePlugins.hidden, false);
assert.equal(pluginLoads, 1);
tabs[3].listeners.keydown(keyboard('End'));
assert.equal(document.activeElement, tabs.at(-1)); assert.equal(elements.paneAbout.hidden, false);
tabs.at(-1).listeners.keydown(keyboard('Home')); assert.equal(document.activeElement, tabs[0]);
const escape = keyboard('Escape'); handleSettingsKeydown(escape);
assert.equal(escape.prevented, true); assert.equal(elements.appFrame.inert, false);
assert.equal(elements.settingsOverlay.hidden, true); assert.equal(document.activeElement, elements.openSettings);
const closedTab = keyboard('Tab'); handleSettingsKeydown(closedTab); assert.equal(closedTab.prevented, false);
openSettingsPane('invalid'); assert.equal(elements.settingsOverlay.hidden, true);
""")


def test_composer_blocks_duplicate_requests_and_preserves_new_draft(tmp_path) -> None:
    html = dashboard_html()
    start = html.index('    bindUI("tasks", $("runForm"), "submit"')
    end = html.index('    bindUI("workflows", $("automationForm"), "submit"', start)
    run_script(tmp_path, r"""
const assert = require('node:assert/strict');
let submit;
const elements = Object.fromEntries(['runForm', 'runMessage', 'sendMessage', 'messageAction', 'runProvider', 'runModel', 'runMode', 'runWorktree'].map(id => [id, {value: '', addEventListener: (name, listener) => { if (id === 'runForm') submit = listener; }}]));
const $ = id => elements[id];
const state = {sending: false};
let currentMode = 'new';
const composerMode = () => currentMode;
const sendTaskControl = async () => {};
const autoGrow = () => {};
const setNotice = () => {};
const refreshRuns = async () => {};
const selectRun = async () => {};
const syncComposerMode = () => {};
let requests = 0, complete, latestBody;
const request = (path, options) => { requests++; latestBody = JSON.parse(options.body); return new Promise(resolve => { complete = resolve; }); };
""" + html[start:end] + r"""
(async () => {
  elements.runMessage.value = 'Inspect the project';
  elements.runProvider.value = 'deepseek'; elements.runModel.value = 'model-from-discovery';
  elements.messageAction.value = 'message';
  const first = submit({preventDefault() {}});
  assert.equal(elements.sendMessage.disabled, true); assert.equal(state.sending, true);
  assert.equal(latestBody.provider, 'deepseek'); assert.equal(latestBody.model, 'model-from-discovery');
  await submit({preventDefault() {}}); assert.equal(requests, 1);
  elements.runMessage.value = 'A new draft typed while sending';
  complete({run: {run_id: 'created'}}); await first;
  assert.equal(elements.runMessage.value, 'A new draft typed while sending');
  assert.equal(elements.sendMessage.disabled, false); assert.equal(state.sending, false);
  currentMode = 'reply';
  const next = submit({preventDefault() {}});
  assert.equal('provider' in latestBody, false); assert.equal('model' in latestBody, false);
  complete({run: {run_id: 'next'}}); await next;
  assert.equal(elements.runMessage.value, ''); assert.equal(requests, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_saved_task_shows_its_model_and_hides_new_task_overrides(tmp_path) -> None:
    html = dashboard_html()
    source = html[html.index("    function composerMode("):html.index('    bindUI("tasks", $("runForm"), "submit"')]
    run_script(tmp_path, r"""
const assert = require('node:assert/strict');
const elements = Object.fromEntries(['runProvider', 'runModel', 'runModelCapabilities', 'sessionModel', 'messageAction', 'runMode', 'runWorktree', 'runMessage', 'sendMessage'].map(id => [id, {id, textContent: '', value: 'message', options: [{}, {}, {}], setAttribute(name, value) { this[name] = value; }, dispatchEvent() {}}]));
const $ = id => elements[id];
const document = {getElementById: $};
const state = {selectedRunId: 'saved', selectedRunState: 'done', selectedProvider: 'openrouter', selectedModel: 'saved-model'};
const STREAM_STATES = new Set(['queued', 'running', 'blocked']);
""" + source + r"""
syncComposerMode();
assert.equal(elements.runProvider.hidden, true); assert.equal(elements.runProvider.disabled, true);
assert.equal(elements.runModel.hidden, true); assert.equal(elements.sessionModel.hidden, false);
assert.equal(elements.sessionModel.textContent, 'openrouter / saved-model');
assert.equal(elements.sendMessage['aria-label'], 'Send reply');
state.selectedRunState = 'running'; syncComposerMode();
assert.equal(elements.messageAction.value, 'queue'); assert.equal(elements.sendMessage['aria-label'], 'Queue follow-up');
state.selectedRunId = ''; syncComposerMode();
assert.equal(elements.runProvider.hidden, false); assert.equal(elements.runProvider.disabled, false);
assert.equal(elements.runModel.hidden, false); assert.equal(elements.sessionModel.hidden, true);
assert.equal(elements.sendMessage['aria-label'], 'Start task');
""")


def test_mobile_tasks_drawer_focus_escape_and_viewport_changes(tmp_path) -> None:
    html = dashboard_html()
    source = html[html.index("    const mobileSidebarQuery ="):html.index("    let noticeTimer =")]
    run_script(tmp_path, r"""
const assert = require('node:assert/strict');
class Element {
  constructor(id) {
    this.id = id; this.hidden = false; this.disabled = false; this.tabIndex = 0;
    this.attrs = {}; this.listeners = {}; this.isConnected = true; this.children = [];
    this.classes = new Set();
    this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name), contains: name => this.classes.has(name)};
  }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; }
  focus() { document.activeElement = this; }
  contains(element) { return this.children.includes(element); }
  getClientRects() { return this.hidden ? [] : [{}]; }
  querySelectorAll() { return this.children; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}
const elements = Object.fromEntries(['appFrame', 'taskSidebar', 'mainContent', 'sidebarBackdrop', 'mobileTasks', 'closeMobileTasks', 'runSearch', 'brandHome', 'openSettings'].map(id => [id, new Element(id)]));
const $ = id => elements[id];
const document = {activeElement: elements.mobileTasks, addEventListener() {}};
const query = {matches: true, addEventListener(name, handler) { this.listener = handler; }};
const window = {matchMedia: () => query};
elements.taskSidebar.children = [elements.brandHome, elements.closeMobileTasks, elements.runSearch, elements.openSettings];
const keyboard = (key, shiftKey = false) => ({key, shiftKey, prevented: false, preventDefault() { this.prevented = true; }});
""" + source + r"""
initMobileSidebar();
assert.equal(elements.taskSidebar.inert, true); assert.equal(elements.mainContent.inert, false);
elements.mobileTasks.listeners.click();
assert.equal(elements.appFrame.classList.contains('mobile-open'), true);
assert.equal(elements.taskSidebar.inert, false); assert.equal(elements.mainContent.inert, true);
assert.equal(elements.sidebarBackdrop.hidden, false); assert.equal(elements.mobileTasks.attrs['aria-expanded'], 'true');
assert.equal(elements.taskSidebar.attrs['aria-modal'], 'true'); assert.equal(document.activeElement, elements.runSearch);
elements.openSettings.focus(); const forward = keyboard('Tab'); handleMobileSidebarKeydown(forward);
assert.equal(forward.prevented, true); assert.equal(document.activeElement, elements.brandHome);
const backward = keyboard('Tab', true); handleMobileSidebarKeydown(backward);
assert.equal(backward.prevented, true); assert.equal(document.activeElement, elements.openSettings);
handleMobileSidebarKeydown(keyboard('Escape'));
assert.equal(elements.taskSidebar.inert, true); assert.equal(elements.mainContent.inert, false);
assert.equal(elements.sidebarBackdrop.hidden, true); assert.equal(document.activeElement, elements.mobileTasks);
assert.equal(elements.mobileTasks.attrs['aria-expanded'], 'false'); assert.equal('role' in elements.taskSidebar.attrs, false);
elements.mobileTasks.listeners.click(); elements.sidebarBackdrop.listeners.click();
assert.equal(elements.appFrame.classList.contains('mobile-open'), false);
query.matches = false; query.listener();
assert.equal(elements.taskSidebar.inert, false); assert.equal(elements.mainContent.inert, false);
assert.equal(document.activeElement, elements.brandHome);
elements.mobileTasks.listeners.click(); assert.equal(elements.appFrame.classList.contains('mobile-open'), false);
query.matches = true; query.listener();
assert.equal(elements.taskSidebar.inert, true); assert.equal(document.activeElement, elements.mobileTasks);
""")


def test_dashboard_themes_match_shared_registry_and_saved_preferences(tmp_path) -> None:
    html = dashboard_html(theme="libre-light")
    startup = html.split("<script>", 1)[1].split("</script>", 1)[0]
    source = html[html.index("    const THEME_KEY ="):html.index("    async function saveTheme(")]
    picker = html.split('id="themeSelect"', 1)[1].split("</select>", 1)[0]
    for name, palette in THEME_PALETTES.items():
        assert f'<option value="{name}">{palette.label}</option>' in picker
    run_script(tmp_path, r"""
const assert = require('node:assert/strict');
let stored = '', rejectStorage = false;
const document = {documentElement: {dataset: {theme: 'libre-light'}}};
const localStorage = {
  getItem() { if (rejectStorage) throw new Error('Blocked'); return stored; },
  setItem(key, value) { if (rejectStorage) throw new Error('Blocked'); stored = value; },
};
const picker = {value: ''};
const $ = () => picker;
""" + source + "\nconst startup = () => {" + startup + r"""
};
startup(); assert.equal(document.documentElement.dataset.theme, 'libre-light');
stored = 'gruvbox-dark'; startup();
assert.equal(document.documentElement.dataset.theme, 'gruvbox-dark');
stored = 'invalid-theme'; startup();
assert.equal(document.documentElement.dataset.theme, 'libre-light');
stored = ' LIBRE-DARK '; startup();
assert.equal(document.documentElement.dataset.theme, 'libre');
stored = ' LoBsTeR '; startup();
assert.equal(document.documentElement.dataset.theme, 'lobster');
rejectStorage = true; startup();
assert.equal(document.documentElement.dataset.theme, 'libre-light');
assert.equal(applyTheme('libre'), 'libre'); assert.equal(picker.value, 'libre');
rejectStorage = false;
""" + "\nconst expectedAliases = " + json.dumps(THEME_ALIASES) + r""";
for (const [alias, expected] of Object.entries(expectedAliases)) {
  assert.equal(applyTheme(alias), expected);
  assert.equal(picker.value, expected);
  assert.equal(stored, expected);
}
for (const theme of THEMES) assert.equal(applyTheme(theme), theme);
assert.equal(applyTheme('unknown-theme'), 'libre');
""")


def test_all_dashboard_themes_use_shared_colors_and_libre_design() -> None:
    html = dashboard_html()
    assert '<html lang="en" data-theme="libre">' in html
    assert '__LIBRE_CLAW_' not in html
    assert 'id="wordmarkTemplate"' in html
    assert 'class="pixel-wordmark"' in html
    for name, palette in THEME_PALETTES.items():
        assert html.count(f'html[data-theme="{name}"] {{') == 1
        css = html.split(f'html[data-theme="{name}"] {{', 1)[1].split("}", 1)[0]
        for token, color in (("bg", palette.background), ("accent", palette.accent),
                             ("text", palette.text), ("line", palette.line),
                             ("on-accent", palette.on_accent),
                             ("on-accent-strong", palette.on_accent_strong)):
            assert color
            assert f"--{token}: {color};" in css
        assert '--font-ui: var(--font-mono)' in css
        assert '--radius: 0px;' in css
    assert 'html:is([data-theme="libre"], [data-theme="libre-light"])' not in html
    assert '@media (prefers-reduced-motion: reduce)' in html
    assert 'fonts.googleapis.com' not in html


def test_plugin_writes_block_modal_exit_but_package_checks_can_be_left(tmp_path) -> None:
    html = dashboard_html()
    source = html[html.index("    const PANES ="):html.index("    async function loadModelConfig(")]
    run_script(tmp_path, MODAL_DOM + source + r"""
openSettingsPane('plugins');
pluginView = 'install'; pluginPending = 'install';
const initialLeaves = pluginLeaves;
closeSettingsPanel();
assert.equal(elements.settingsOverlay.hidden, false);
assert.equal(pluginLeaves, initialLeaves);
assert.match(pluginStatus, /Wait for the plugin operation/);
openSettingsPane('models');
assert.equal(elements.panePlugins.hidden, false);
assert.equal(modelLoads, 0);
pluginPending = 'preview';
closeSettingsPanel();
assert.equal(elements.settingsOverlay.hidden, true);
assert.equal(pluginLeaves, initialLeaves + 1);
assert.equal(pluginPending, '');
openSettingsPane('plugins');
pluginView = 'detail';
openSettingsPane('general');
assert.equal(elements.paneGeneral.hidden, false);
assert.equal(pluginView, 'list');
assert.equal(pluginLeaves, initialLeaves + 2);
""")
