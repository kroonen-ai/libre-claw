# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess

import pytest

from libre_claw.web.dashboard import dashboard_html


PLUGIN_DOM = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {};
    this.listeners = {}; this.textContent = ''; this.hidden = false; this.disabled = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelectorAll() {
    return this.children.flatMap(child => [
      ...(child.tagName === 'button' && child.dataset.pluginId ? [child] : []),
      ...child.querySelectorAll(),
    ]);
  }
  focus() { document.activeElement = this; }
  set innerHTML(value) { throw new Error('Plugin metadata must use text nodes'); }
}
const elements = Object.fromEntries(['refreshPlugins', 'pluginsList', 'pluginsStatus', 'pluginsWorkspace',
  'pluginsPrivacy', 'pluginsDisabled', 'panePlugins', 'settingsOverlay'].map(id => [id, new Element()]));
const $ = id => elements[id];
const document = {createElement: tag => new Element(tag), activeElement: null};
const text = node => [node.textContent, ...node.children.map(text)].filter(Boolean).join(' ');
const buttons = () => $('pluginsList').querySelectorAll();
let catalog = {enabled: true, workspace: '/projects/my-project', plugins: [], privacy: {
  telemetry: false, history_shared: false, credentials_inherited: false, default_network: 'denied',
}};
const sample = (extra = {}) => ({id: 'sample', name: 'Local helper', version: '1.0.0', enabled: false,
  integrity: 'valid', tools: ['summarize'], grants: {allow_network: false, read_paths: [], write_paths: []}, ...extra});
let loadError = '', patchError = '', holdPatch = false, completePatch;
const calls = [];
const request = async (path, options) => {
  calls.push({path, options});
  if (options?.method === 'PATCH') {
    if (patchError) throw new Error(patchError);
    if (holdPatch) await new Promise(resolve => { completePatch = resolve; });
    const id = decodeURIComponent(path.slice('/plugins/'.length));
    const enabled = JSON.parse(options.body).enabled;
    const plugin = catalog.plugins.find(item => item.id === id);
    plugin.enabled = enabled;
    plugin.grants = {allow_network: false, read_paths: [], write_paths: []};
    return {plugin};
  }
  assert.equal(path, '/plugins');
  if (loadError) throw new Error(loadError);
  return JSON.parse(JSON.stringify(catalog));
};
const patchCalls = () => calls.filter(call => call.options?.method === 'PATCH');
"""


def run_plugins(tmp_path, assertions: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard plugin interactions")
    html = dashboard_html()
    source = html[html.index("    /* Cordis plugins */"):html.index("    /* Settings modal */")]
    script = tmp_path / "plugins.cjs"
    script.write_text(PLUGIN_DOM + source + assertions)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_plugins_empty_state_and_metadata_are_read_only_and_safe(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  await loadPlugins();
  assert.match(text($('pluginsList')), /No plugins installed/);
  assert.match(text($('pluginsList')), /libre-claw cordis install <folder>/);
  assert.equal($('pluginsWorkspace').textContent, '/projects/my-project');
  assert.match(text($('pluginsPrivacy')), /No telemetry/);
  assert.match(text($('pluginsPrivacy')), /No automatic chat access/);
  assert.equal($('pluginsStatus').textContent, '0 plugins installed locally.');
  catalog.plugins = [sample({name: '<img src=x onerror=alert(1)>', tools: ['<script>private</script>'],
    grants: {allow_network: true, read_paths: ['/project/reference.md'], write_paths: ['/project/output']}})];
  await loadPlugins();
  assert.match(text($('pluginsList')), /<img src=x onerror=alert\(1\)>/);
  assert.match(text($('pluginsList')), /<script>private<\/script>/);
  assert.match(text($('pluginsList')), /Network Allowed Extra reads \/project\/reference.md Extra writes \/project\/output/);
  assert.equal(buttons()[0].textContent, 'Enable offline');
  assert.equal(buttons()[0].disabled, false);
  assert.equal(patchCalls().length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugins_toggle_is_bounded_offline_and_refreshes_registry(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({id: 'helper /encoded'})];
  await loadPlugins();
  holdPatch = true;
  const action = togglePlugin('helper /encoded');
  assert.equal(buttons()[0].disabled, true);
  assert.equal(buttons()[0].textContent, 'Enabling…');
  assert.equal($('refreshPlugins').disabled, true);
  assert.equal($('pluginsList').attrs['aria-busy'], 'true');
  await togglePlugin('helper /encoded');
  assert.equal(patchCalls().length, 1);
  await loadPlugins();
  assert.equal(calls.filter(call => !call.options).length, 1);
  assert.equal(patchCalls()[0].path, '/plugins/helper%20%2Fencoded');
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body), {enabled: true});
  completePatch(); await action;
  assert.equal(buttons()[0].textContent, 'Disable');
  assert.equal(buttons()[0].disabled, false);
  assert.equal($('refreshPlugins').disabled, false);
  assert.equal($('pluginsList').attrs['aria-busy'], 'false');
  assert.match($('pluginsStatus').textContent, /enabled offline. Start a new task/);
  holdPatch = false;
  await togglePlugin('helper /encoded');
  assert.deepEqual(JSON.parse(patchCalls()[1].options.body), {enabled: false});
  assert.equal(buttons()[0].textContent, 'Enable offline');
  assert.match($('pluginsStatus').textContent, /access has been revoked/);
  assert.equal(calls.filter(call => !call.options).length, 3);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugins_kill_switch_and_changed_integrity_block_enable_but_allow_revocation(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.enabled = false;
  catalog.plugins = [sample(), sample({id: 'changed', integrity: 'changed'}),
    sample({id: 'active', enabled: true, integrity: 'changed'})];
  await loadPlugins();
  assert.equal($('pluginsDisabled').hidden, false);
  assert.deepEqual(buttons().map(button => button.disabled), [true, true, false]);
  assert.match(text($('pluginsList')), /Files changed since installation. Reinstall before enabling/);
  await togglePlugin('sample'); await togglePlugin('changed');
  assert.equal(patchCalls().length, 0);
  await togglePlugin('active');
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body), {enabled: false});
  catalog.enabled = true;
  await loadPlugins();
  assert.equal($('pluginsDisabled').hidden, true);
  assert.deepEqual(buttons().map(button => button.disabled), [false, true, true]);
  await togglePlugin('changed');
  assert.equal(patchCalls().length, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugins_errors_are_visible_and_never_report_a_failed_toggle_as_saved(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample()];
  await loadPlugins();
  patchError = 'Local requests only';
  await togglePlugin('sample');
  assert.match($('pluginsStatus').textContent, /Could not enable Local helper: Local requests only/);
  assert.equal($('pluginsStatus').className, 'hint danger');
  assert.equal(buttons()[0].textContent, 'Enable offline');
  assert.equal(buttons()[0].disabled, false);
  patchError = ''; loadError = 'Registry unavailable';
  await togglePlugin('sample');
  assert.match($('pluginsStatus').textContent, /Change saved, but plugins could not be refreshed: Registry unavailable/);
  assert.equal(buttons().length, 0);
  assert.equal($('refreshPlugins').disabled, false);
  await loadPlugins();
  assert.equal($('pluginsStatus').textContent, 'Could not load plugins: Registry unavailable');
  loadError = '';
  await loadPlugins();
  assert.equal(buttons()[0].textContent, 'Disable');
  assert.equal(buttons()[0].disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
