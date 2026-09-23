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
    this.listeners = {}; this.textContent = ''; this.hidden = false; this.disabled = false; this.value = '';
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelectorAll(selector = '*') {
    const selectors = selector.split(',').map(part => part.trim());
    return this.children.flatMap(child => [
      ...(selectors.some(part => part === '*' || part === child.tagName
        || part === 'button[data-plugin-id]' && child.tagName === 'button' && child.dataset.pluginId) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
  focus() { document.activeElement = this; }
  set innerHTML(value) { throw new Error('Plugin metadata must use text nodes'); }
}
const elements = Object.fromEntries(['refreshPlugins', 'addPlugin', 'pluginSearch', 'pluginsList', 'pluginsStatus', 'pluginsWorkspace',
  'pluginsPrivacy', 'pluginsDisabled', 'panePlugins', 'settingsOverlay', 'pluginPage', 'pluginsIncluded', 'pluginsInstalledCount',
  'pluginsIncludedSection', 'pluginsInventory', 'pluginsHeader', 'pluginsScopeBlock'].map(id => [id, new Element()]));
const $ = id => elements[id] || Object.values(elements).flatMap(root => root.querySelectorAll()).find(node => node.id === id);
const document = {createElement: tag => new Element(tag), activeElement: null};
let bindUI = (_service, target, event, handler) => {
  target.addEventListener(event, handler);
  return () => { if (target.listeners[event] === handler) delete target.listeners[event]; };
};
const releaseUI = root => { for (const node of [root, ...root.querySelectorAll('*')]) node.listeners = {}; };
const text = node => [node.textContent, ...node.children.map(text)].filter(Boolean).join(' ');
const buttons = () => $('pluginsList').querySelectorAll('button').filter(node => node.dataset.pluginAction === 'toggle');
const pageButton = name => $('pluginPage').querySelectorAll('button').find(node => node.textContent === name);
let catalog = {enabled: true, workspace: '/projects/my-project', plugins: [], catalog: [], privacy: {
  telemetry: false, history_shared: false, credentials_inherited: false, default_network: 'denied',
}};
const sample = (extra = {}) => ({id: 'sample', name: 'Local helper', version: '1.0.0', description: 'A useful local tool.', enabled: false,
  integrity: 'valid', tools: ['summarize'], digest: 'a'.repeat(64), config: {}, config_schema: {type: 'object', properties: {}}, configured_secrets: [],
  tool_definitions: [{name: 'summarize', description: 'Summarize locally', input_schema: {type: 'object', properties: {text: {type: 'string'}}}}],
  grants: {allow_network: false, read_paths: [], write_paths: []}, ...extra});
let loadError = '', patchError = '', holdPatch = false, completePatch, previewError = '', configError = '', installError = '', holdPreview = false, completePreview;
const calls = [];
const request = async (path, options) => {
  calls.push({path, options});
  const method = options?.method || 'GET';
  if (path === '/plugins/preview') {
    if (holdPreview) await new Promise(resolve => { completePreview = resolve; });
    if (previewError) throw new Error(previewError);
    return {token: 'preview-123', id: 'sample', name: 'Local helper', version: '1.0.0', description: 'A useful local tool.', tools: ['summarize'], tool_count: 1, tool_definitions: sample().tool_definitions, digest: 'a'.repeat(64)};
  }
  if (path.startsWith('/plugins/preview/')) { assert.equal(method, 'DELETE'); return {removed: true}; }
  if (path === '/plugins/install') {
    if (installError) throw new Error(installError);
    catalog.plugins = [catalog.plugins.find(plugin => plugin.id === 'sample') || sample()]; return {plugin: catalog.plugins[0]};
  }
  if (path.endsWith('/inspect')) return {plugin: {state: 'ACTIVE', runtime_version: '1.0.0'}};
  if (path.endsWith('/config')) {
    if (configError) throw new Error(configError);
    catalog.plugins[0].config = JSON.parse(options.body).config;
    return {plugin: JSON.parse(JSON.stringify(catalog.plugins[0]))};
  }
  if (method === 'PATCH') {
    if (patchError) throw new Error(patchError);
    if (holdPatch) await new Promise(resolve => { completePatch = resolve; });
    const id = decodeURIComponent(path.slice('/plugins/'.length));
    const change = JSON.parse(options.body), enabled = change.enabled;
    const plugin = catalog.plugins.find(item => item.id === id);
    plugin.enabled = enabled;
    plugin.grants = {allow_network: false, read_paths: [], write_paths: []};
    if (change.allow_engine === true) plugin.grants.allow_engine = true;
    if (change.allow_client === true) plugin.grants.allow_client = true;
    return {plugin};
  }
  if (method === 'DELETE') { catalog.plugins = []; return {removed: true}; }
  if (path !== '/plugins') return {plugin: JSON.parse(JSON.stringify(catalog.plugins.find(plugin => plugin.id === decodeURIComponent(path.slice('/plugins/'.length)))))};
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
  assert.match(text($('pluginsList')), /Add your first plugin/);
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
  assert.match($('pluginsStatus').textContent, /enabled offline. Its tools are available on your next message/);
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
  assert.deepEqual(Object.fromEntries(buttons().map(button => [button.dataset.pluginId, button.disabled])), {active: false, changed: true, sample: true});
  assert.match(text($('pluginsList')), /Files changed since installation. Reinstall before enabling/);
  await togglePlugin('sample'); await togglePlugin('changed');
  assert.equal(patchCalls().length, 0);
  await togglePlugin('active');
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body), {enabled: false});
  catalog.enabled = true;
  await loadPlugins();
  assert.equal($('pluginsDisabled').hidden, true);
  assert.deepEqual(Object.fromEntries(buttons().map(button => [button.dataset.pluginId, button.disabled])), {active: true, changed: true, sample: false});
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


def test_plugin_search_preserves_alphabetic_order_across_toggles(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({id: 'z', name: 'Zebra'}), sample({id: 'a', name: 'Alpha'})];
  catalog.catalog = [{id: 'included', name: 'Text utilities', source: 'builtin:text-utilities', version: '1', description: 'Local text tools'}];
  await loadPlugins();
  assert.deepEqual(buttons().map(button => button.dataset.pluginId), ['a', 'z']);
  await togglePlugin('a');
  assert.deepEqual(buttons().map(button => button.dataset.pluginId), ['a', 'z']);
  assert.equal($('pluginsIncludedSection').hidden, false);
  $('pluginSearch').value = 'zebra'; renderPlugins(pluginCatalog);
  assert.deepEqual(buttons().map(button => button.dataset.pluginId), ['z']);
  assert.equal($('pluginsIncludedSection').hidden, true);
  $('pluginSearch').value = 'absent'; renderPlugins(pluginCatalog);
  assert.match(text($('pluginsList')), /No matching plugins/);
  assert.equal(calls.filter(call => call.options?.method === 'POST').length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugin_install_is_previewed_disabled_and_explicitly_enabled(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  await loadPlugins(); await openPluginInstall();
  pluginInstall.source = 'npm:@local/tool@1.0.0'; await previewPlugin();
  assert.equal(pluginInstall.stage, 'preview');
  assert.equal(catalog.plugins.length, 0);
  assert.match(text($('pluginPage')), /checked without executing/);
  assert.match(text($('pluginPage')), /1 tool · Offline by default/);
  assert.match(text($('pluginPage')), /Review declared tools.*Summarize locally/);
  assert.match(text($('pluginPage')), /"type": "object"/);
  assert.deepEqual(JSON.parse(calls.find(call => call.path === '/plugins/preview').options.body), {source: 'npm:@local/tool@1.0.0'});
  await installPlugin();
  assert.equal(pluginInstall.stage, 'done');
  assert.equal(catalog.plugins[0].enabled, false);
  assert.equal(patchCalls().length, 0);
  assert.deepEqual(JSON.parse(calls.find(call => call.path === '/plugins/install').options.body), {token: 'preview-123'});
  assert.match(text($('pluginPage')), /Installed and disabled/);
  await enableInstalledPlugin();
  assert.equal(catalog.plugins[0].enabled, true);
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body), {enabled: true});
  assert.equal(pluginView, 'detail');
  assert.match(text($('pluginPage')), /Configuration/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugin_install_errors_preserve_source_and_cancel_discards_preview(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  await loadPlugins(); await openPluginInstall();
  pluginInstall.source = '/local/tool.tgz'; previewError = 'Manifest missing';
  await previewPlugin();
  assert.equal(pluginInstall.source, '/local/tool.tgz');
  assert.equal(pluginInstall.stage, 'source');
  assert.match($('pluginsStatus').textContent, /Manifest missing/);
  assert.equal(pageButton('Check package').disabled, false);
  previewError = ''; await previewPlugin();
  installError = 'Disk full'; await installPlugin();
  assert.equal(pluginInstall.stage, 'source');
  assert.equal(pluginInstall.preview, null);
  assert.equal(catalog.plugins.length, 0);
  assert.match($('pluginsStatus').textContent, /Disk full/);
  await previewPlugin(); await backPluginInstall();
  assert.equal(pluginInstall.source, '/local/tool.tgz');
  assert.equal(pluginInstall.stage, 'source');
  assert.equal(calls.filter(call => call.path === '/plugins/preview/preview-123' && call.options?.method === 'DELETE').length, 1);
  await previewPlugin(); await leavePluginPage();
  assert.equal(pluginInstall, null); assert.equal(pluginView, 'list');
  assert.equal(calls.filter(call => call.path === '/plugins/preview/preview-123' && call.options?.method === 'DELETE').length, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugin_configuration_is_staged_validated_and_does_not_prefill_secrets(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({config_schema: {type: 'object', required: ['count'], properties: {
    label: {type: 'string'}, enabled: {type: 'boolean'}, count: {type: 'integer', minimum: 1},
    mode: {type: 'string', enum: ['compact', 'full']}, nested: {type: 'object'}, apiKey: {type: 'string', writeOnly: true, default: 'never-render-this'},
    password: {type: 'string', format: 'password'}
  }}, config: {label: 'Before', enabled: true, count: 2, mode: 'compact', nested: {paths: ['one']}}, configured_secrets: ['/apiKey']})];
  await loadPlugins(); await openPluginDetail('sample');
  const field = name => pluginConfigFields.find(item => item.key === name);
  assert.equal(field('apiKey').input.type, 'password');
  assert.equal(field('apiKey').input.value, '');
  assert.equal(field('password').input.value, '');
  assert.equal(text($('pluginPage')).includes('never-render-this'), false);
  assert.match(field('apiKey').input.placeholder, /leave blank to keep/);
  field('label').input.value = 'Changed'; field('label').input.listeners.input();
  assert.equal(calls.filter(call => call.options?.method === 'PUT').length, 0);
  assert.equal(pageButton('Save configuration').disabled, false);
  field('count').input.value = '1.5'; await savePluginConfig();
  assert.match($('pluginsStatus').textContent, /valid integer/);
  assert.equal(calls.filter(call => call.options?.method === 'PUT').length, 0);
  field('count').input.value = '3'; field('nested').input.value = '{bad'; await savePluginConfig();
  assert.match($('pluginsStatus').textContent, /valid JSON/);
  field('nested').input.value = '{"paths":["two"]}'; await savePluginConfig();
  const saved = JSON.parse(calls.find(call => call.options?.method === 'PUT').options.body).config;
  assert.deepEqual(saved, {label: 'Changed', enabled: true, count: 3, mode: 'compact', nested: {paths: ['two']}});
  assert.equal(Object.hasOwn(saved, 'apiKey'), false);
  assert.equal(Object.hasOwn(saved, 'password'), false);
  assert.equal(pageButton('Save configuration').disabled, true);
  assert.match($('pluginsStatus').textContent, /Configuration saved/);
  pageButton('Clear saved value').listeners.click();
  assert.equal(collectPluginConfig().apiKey, null);
  pageButton('Reset changes').listeners.click();
  assert.equal(Object.hasOwn(collectPluginConfig(), 'apiKey'), false);
  assert.equal(pluginConfigDirty, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugin_configuration_failure_retains_edits_and_leaving_discards_them(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({config_schema: {type: 'object', properties: {label: {type: 'string'}}}, config: {label: 'Original'}})];
  await loadPlugins(); await openPluginDetail('sample');
  pluginConfigFields[0].input.value = 'Unsaved'; pluginConfigFields[0].input.listeners.input();
  configError = 'Configuration rejected'; await savePluginConfig();
  assert.equal(pluginConfigFields[0].input.value, 'Unsaved');
  assert.equal(pluginConfigDirty, true);
  assert.match($('pluginsStatus').textContent, /Could not save configuration: Configuration rejected/);
  await leavePluginPage(); assert.equal(pluginConfigFields.length, 0);
  await openPluginDetail('sample'); assert.equal(pluginConfigFields[0].input.value, 'Original');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugin_removal_requires_named_confirmation_and_runtime_requires_enabled(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample()];
  await loadPlugins(); await openPluginDetail('sample');
  assert.equal(pageButton('Check runtime').disabled, true);
  await checkPluginRuntime(); assert.equal(calls.some(call => call.path.endsWith('/inspect')), false);
  await togglePlugin('sample');
  assert.equal(pageButton('Check runtime').disabled, false);
  await checkPluginRuntime(); assert.match($('pluginRuntimeStatus').textContent, /ACTIVE.*temporary runtime has stopped/);
  pageButton('Remove plugin…').listeners.click();
  assert.equal(calls.some(call => call.options?.method === 'DELETE'), false);
  assert.match(text($('pluginPage')), /Remove Local helper\?/);
  assert.match(text($('pluginPage')), /across all projects/);
  pageButton('Keep plugin').listeners.click();
  assert.equal(catalog.plugins.length, 1);
  pageButton('Remove plugin…').listeners.click(); await removePlugin();
  assert.equal(catalog.plugins.length, 0);
  assert.equal(pluginView, 'list');
  assert.match($('pluginsStatus').textContent, /removed from all projects/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_plugin_replacement_warns_about_all_project_grants_before_install(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample()]; await loadPlugins();
  await openPluginInstall('/replacement');
  assert.match(text($('pluginPage')), /replaces an installed package/);
  assert.match(text($('pluginPage')), /revoked in every project/);
  assert.equal(calls.some(call => call.path === '/plugins/install'), false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_cancelled_package_check_discards_late_preview_without_installing(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  await loadPlugins(); await openPluginInstall();
  pluginInstall.source = '/slow/package'; holdPreview = true;
  const checking = previewPlugin();
  assert.equal(pageButton('Check package').disabled, true);
  assert.equal(pageButton('Cancel').disabled, false);
  await previewPlugin();
  assert.equal(calls.filter(call => call.path === '/plugins/preview').length, 1);
  await leavePluginPage();
  assert.equal(pluginView, 'list'); assert.equal(pluginPending, '');
  completePreview(); await checking;
  assert.equal(pluginView, 'list'); assert.equal(pluginInstall, null);
  assert.equal(calls.some(call => call.path === '/plugins/install'), false);
  assert.equal(calls.filter(call => call.path === '/plugins/preview/preview-123' && call.options?.method === 'DELETE').length, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_identical_reinstall_preserves_enabled_state_without_toggle(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({enabled: true})]; await loadPlugins();
  await openPluginInstall('/same-package'); await installPlugin();
  assert.equal(pluginInstall.stage, 'done');
  assert.match(text($('pluginPage')), /already enabled/);
  assert.equal(pageButton('Enable now'), undefined);
  assert.ok(pageButton('Open plugin'));
  assert.equal(patchCalls().length, 0);
  assert.equal(catalog.plugins[0].enabled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_configuration_prototype_names_and_nonstring_secrets(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  const properties = JSON.parse('{"__proto__":{"type":"string"},"constructor":{"type":"string"},"credentials":{"type":"object","writeOnly":true},"flag":{"type":"boolean","writeOnly":true}}');
  catalog.plugins = [sample({config_schema: {type: 'object', properties}, config: {}})];
  await loadPlugins(); await openPluginDetail('sample');
  const field = key => pluginConfigFields.find(item => item.key === key);
  assert.equal(field('__proto__').input.value, '');
  assert.equal(field('constructor').input.value, '');
  field('__proto__').input.value = 'safe data';
  assert.equal(field('credentials').input.type, 'password');
  field('credentials').input.value = '{"token":"new-value"}';
  field('flag').input.value = 'false';
  const config = collectPluginConfig();
  assert.equal(Object.hasOwn(config, '__proto__'), true);
  assert.equal(config.__proto__, 'safe data');
  assert.deepEqual(config.credentials, {token: 'new-value'});
  assert.equal(config.flag, false);
  assert.equal(Object.getPrototypeOf(config), Object.prototype);
  field('credentials').input.value = 'invalid';
  assert.throws(collectPluginConfig, /valid JSON object/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_saving_another_field_preserves_absent_strings_and_allows_explicit_clear(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({config_schema: {type: 'object', properties: {
    optionalLabel: {type: 'string', minLength: 1}, label: {type: 'string'}, enabled: {type: 'boolean'}
  }}, config: {label: 'Present', enabled: true}})];
  await loadPlugins(); await openPluginDetail('sample');
  const enabled = pluginConfigFields.find(field => field.key === 'enabled');
  enabled.input.value = 'false'; enabled.input.listeners.change();
  await savePluginConfig();
  const saved = JSON.parse(calls.find(call => call.options?.method === 'PUT').options.body).config;
  assert.deepEqual(saved, {label: 'Present', enabled: false});
  const label = pluginConfigFields.find(field => field.key === 'label');
  label.input.value = ''; label.input.listeners.input();
  assert.equal(collectPluginConfig().label, '');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_harness_nested_configuration_uses_fields_and_preserves_unknown_values(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({format: 'deepseek-harness', config_schema: {type: 'object', properties: {
    components: {type: 'object', title: 'Components', properties: {
      todo: {type: 'object', title: 'Task list', properties: {
        enabled: {type: 'boolean', title: 'Enabled'}, config: {type: 'object', title: 'Settings', required: ['allowParallelInProgress'], properties: {
          allowParallelInProgress: {type: 'boolean', title: 'Allow parallel tasks'}, limit: {type: 'integer', minimum: 1, maximum: 20},
          mode: {type: 'string', enum: ['compact', 'full']}, labels: {type: 'array', items: {type: 'string'}},
          optional: {type: 'string'}, nested: {type: 'object', properties: {label: {type: 'string'}}}
        }}
      }}
    }}
  }}, config: {custom: {untouched: true}, components: {
    todo: {enabled: true, extra: 'preserved', config: {allowParallelInProgress: false, limit: 2, mode: 'compact', labels: ['a'], nested: {label: 'Before', unknown: 42}, unrecognized: {a: 1}}},
    future: {enabled: false, config: {data: 'keep'}}
  }}})];
  await loadPlugins(); await openPluginDetail('sample');
  const field = pointer => pluginConfigFields.find(item => item.pointer === pointer);
  assert.equal(field('/components'), undefined);
  assert.ok($('pluginPage').querySelectorAll('fieldset').length >= 3);
  const parallel = field('/components/todo/config/allowParallelInProgress');
  assert.equal(parallel.input.tagName, 'select');
  assert.equal(parallel.input.attrs['aria-required'], 'true');
  assert.equal(parallel.input.value, 'false');
  assert.equal(field('/components/todo/config/labels').kind, 'json');
  const enabled = field('/components/todo/enabled'); enabled.input.value = 'false'; enabled.input.listeners.change();
  const limit = field('/components/todo/config/limit'); limit.input.value = '7'; limit.input.listeners.input();
  const nested = field('/components/todo/config/nested/label'); nested.input.value = 'After'; nested.input.listeners.input();
  assert.equal(patchCalls().length, 0);
  assert.equal(calls.some(call => call.options?.method === 'PUT'), false);
  await savePluginConfig();
  const saved = JSON.parse(calls.find(call => call.options?.method === 'PUT').options.body).config;
  assert.equal(saved.components.todo.enabled, false);
  assert.equal(saved.components.todo.config.limit, 7);
  assert.deepEqual(saved.components.todo.config.nested, {label: 'After', unknown: 42});
  assert.deepEqual(saved.components.todo.config.unrecognized, {a: 1});
  assert.equal(Object.hasOwn(saved.components.todo.config, 'optional'), false);
  assert.equal(saved.components.todo.extra, 'preserved');
  assert.deepEqual(saved.components.future, {enabled: false, config: {data: 'keep'}});
  assert.deepEqual(saved.custom, {untouched: true});
  assert.equal(patchCalls().length, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_failed_harness_enable_loads_required_fields_without_granting_access(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({format: 'deepseek-harness', config: {components: {todo: {enabled: true, config: {}}}}})];
  await loadPlugins();
  // The activation endpoint discovers this schema before rejecting the empty
  // required configuration. The following GET returns that cached metadata.
  catalog.plugins[0].config_schema = {type: 'object', properties: {components: {type: 'object', properties: {
    todo: {type: 'object', properties: {enabled: {type: 'boolean'}, config: {type: 'object', required: ['allowParallelInProgress'],
      properties: {allowParallelInProgress: {type: 'boolean', title: 'Allow parallel tasks'}}}}}
  }}}};
  patchError = 'Harness configuration failed. Review the component settings.';
  await togglePlugin('sample');
  assert.equal(pluginView, 'detail');
  assert.equal(pluginDetail.enabled, false);
  assert.match($('pluginsStatus').textContent, /^Could not enable Local helper: Harness configuration failed/);
  assert.equal($('pluginsStatus').className, 'hint danger');
  const field = pluginConfigFields.find(item => item.pointer === '/components/todo/config/allowParallelInProgress');
  assert.ok(field); assert.equal(field.present, false);
  assert.equal(field.input.attrs['aria-required'], 'true');
  field.input.value = 'false'; field.input.listeners.change();
  await savePluginConfig();
  assert.deepEqual(JSON.parse(calls.find(call => call.options?.method === 'PUT').options.body).config,
    {components: {todo: {enabled: true, config: {allowParallelInProgress: false}}}});
  assert.equal(pluginDetail.enabled, false);
  assert.equal(patchCalls().length, 1);
  patchError = ''; await togglePlugin('sample');
  assert.equal(pluginDetail.enabled, true);
  assert.deepEqual(JSON.parse(patchCalls()[1].options.body), {enabled: true});
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_nested_secrets_use_escaped_paths_and_explicit_clear_only(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({config_schema: {type: 'object', properties: {
    components: {type: 'object', properties: {'row/with~escape': {type: 'object', properties: {
      config: {type: 'object', properties: {label: {type: 'string'}, apiKey: {type: 'string', writeOnly: true, default: 'never-show'},
        credentials: {type: 'object', writeOnly: true}, endpoints: {type: 'array', items: {type: 'object', properties: {token: {type: 'string', writeOnly: true}}}}}}
    }}}}
  }}, config: {components: {'row/with~escape': {config: {label: 'Original'}}}},
  configured_secrets: ['/components/row~1with~0escape/config/apiKey', '/components/row~1with~0escape/config/endpoints/0/token']})];
  await loadPlugins(); await openPluginDetail('sample');
  const field = pointer => pluginConfigFields.find(item => item.pointer === pointer);
  const secret = field('/components/row~1with~0escape/config/apiKey');
  assert.equal(secret.input.type, 'password'); assert.equal(secret.input.value, '');
  assert.match(secret.input.placeholder, /Saved/);
  assert.equal(text($('pluginPage')).includes('never-show'), false);
  const label = field('/components/row~1with~0escape/config/label'); label.input.value = 'Changed'; label.input.listeners.input();
  let saved = collectPluginConfig().components['row/with~escape'].config;
  assert.deepEqual(saved, {label: 'Changed'});
  assert.match(field('/components/row~1with~0escape/config/endpoints').input.placeholder, /leave unchanged to keep/);
  const secretLabel = $('pluginPage').querySelectorAll('label').find(node => node.children.includes(secret.input));
  secretLabel.querySelectorAll('button')[0].listeners.click();
  saved = collectPluginConfig().components['row/with~escape'].config;
  assert.equal(saved.apiKey, null);
  const credentials = field('/components/row~1with~0escape/config/credentials');
  credentials.input.value = '{"token":"new-token"}'; credentials.input.listeners.input();
  assert.deepEqual(collectPluginConfig().components['row/with~escape'].config.credentials, {token: 'new-token'});
  assert.equal(calls.some(call => call.options?.method === 'PUT'), false);
  pageButton('Reset changes').listeners.click();
  assert.equal(Object.hasOwn(collectPluginConfig().components['row/with~escape'].config, 'apiKey'), false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_nested_prototype_names_are_data_and_do_not_mutate_object_prototypes(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  const schema = JSON.parse('{"type":"object","properties":{"__proto__":{"type":"object","properties":{"constructor":{"type":"object","properties":{"prototype":{"type":"boolean"},"newValue":{"type":"string"}}}}},"constructor":{"type":"object","properties":{"prototype":{"type":"object","properties":{"polluted":{"type":"string"}}}}}}}');
  const config = JSON.parse('{"__proto__":{"constructor":{"prototype":false,"unknown":"keep"}}}');
  catalog.plugins = [sample({config_schema: schema, config})];
  await loadPlugins(); await openPluginDetail('sample');
  const field = pointer => pluginConfigFields.find(item => item.pointer === pointer);
  field('/__proto__/constructor/prototype').input.value = 'true';
  field('/constructor/prototype/polluted').input.value = 'stored data';
  const saved = collectPluginConfig();
  assert.equal(Object.hasOwn(saved, '__proto__'), true);
  assert.equal(saved.__proto__.constructor.prototype, true);
  assert.equal(saved.__proto__.constructor.unknown, 'keep');
  assert.equal(Object.hasOwn(saved, 'constructor'), true);
  assert.equal(saved.constructor.prototype.polluted, 'stored data');
  assert.equal({}.polluted, undefined);
  assert.equal(Object.getPrototypeOf(saved), Object.prototype);
  assert.equal(Object.getPrototypeOf(saved.__proto__), Object.prototype);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_nested_forms_bound_depth_and_validate_array_json(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  let deep = {type: 'string'};
  for (let i = 0; i < 8; i++) deep = {type: 'object', properties: {nested: deep}};
  catalog.plugins = [sample({config_schema: {type: 'object', properties: {
    deep, items: {type: 'array', items: {type: 'string'}}, label: {type: 'string'}
  }}, config: {label: 'saved'}})];
  await loadPlugins(); await openPluginDetail('sample');
  assert.equal($('pluginPage').querySelectorAll('fieldset').length, 5);
  assert.ok(pluginConfigFields.find(item => item.kind === 'json' && item.path[0] === 'deep'));
  const items = pluginConfigFields.find(item => item.key === 'items');
  items.input.value = '{}'; items.input.listeners.input();
  assert.throws(collectPluginConfig, /valid JSON array/);
  items.input.value = '["one","two"]';
  assert.deepEqual(collectPluginConfig(), {label: 'saved', items: ['one', 'two']});
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_failed_enable_preserves_existing_staged_fields_and_json_drafts(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  catalog.plugins = [sample({format: 'deepseek-harness', config: {components: {todo: {enabled: true, config: {label: 'saved'}}}, unknown: 'keep'}})];
  await loadPlugins(); await openPluginDetail('sample');
  const root = pluginConfigFields.find(field => field.kind === 'root');
  root.input.value = JSON.stringify({components: {todo: {enabled: false, config: {label: 'staged'}}}, unknown: 'changed'});
  root.input.listeners.input();
  catalog.plugins[0].config_schema = {type: 'object', properties: {components: {type: 'object', properties: {
    todo: {type: 'object', properties: {enabled: {type: 'boolean'}, config: {type: 'object', properties: {label: {type: 'string'}, requiredFlag: {type: 'boolean'}}}}}
  }}}};
  patchError = 'Missing requiredFlag'; await togglePlugin('sample');
  assert.equal(pluginConfigFields.find(field => field.pointer === '/components/todo/config/label').input.value, 'staged');
  assert.equal(pluginConfigDirty, true);
  assert.equal(collectPluginConfig().unknown, 'changed');
  assert.equal(collectPluginConfig().components.todo.enabled, false);
  assert.match($('pluginsStatus').textContent, /Could not enable.*Missing requiredFlag/);
  assert.equal(pageButton('Save configuration').disabled, false);
  pageButton('Reset changes').listeners.click();
  assert.equal(collectPluginConfig().unknown, 'keep');
  assert.equal(collectPluginConfig().components.todo.config.label, 'saved');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_enable_now_never_disables_a_plugin_enabled_since_installation(tmp_path):
    run_plugins(tmp_path, r"""
(async () => {
  await loadPlugins(); await openPluginInstall('/plugin'); await installPlugin();
  catalog.plugins[0].enabled = true; await loadPlugins();
  await enableInstalledPlugin();
  assert.equal(patchCalls().length, 0);
  assert.equal(catalog.plugins[0].enabled, true);
  assert.equal(pluginView, 'detail');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_core_service_grant_requires_a_distinct_explicit_action(tmp_path):
    run_plugins(tmp_path, r"""
(async()=>{
  catalog.plugins=[sample({requires_engine_access:true,engine_services:[{id:'agent',title:'Agent'},{id:'tools'}]})];
  await loadPlugins();await openPluginDetail('sample');
  assert.match(text($('pluginPage')), /Core services: Agent, tools/);
  assert.match(text($('pluginPage')), /never prompts or provider keys/);
  assert.equal(patchCalls().length,0);
  await togglePlugin('sample');
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body),{enabled:true});
  assert.ok(pageButton('Enable core services'));
  await enablePluginEngine('sample');
  assert.deepEqual(JSON.parse(patchCalls()[1].options.body),{enabled:true,allow_engine:true});
  assert.equal(pageButton('Enable core services'),undefined);
  assert.match($('pluginsStatus').textContent,/restart the runtime/);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_client_interface_grant_is_explicit_and_respects_dirty_configuration(tmp_path):
    run_plugins(tmp_path, r"""
(async()=>{
  catalog.plugins=[sample({requires_client_access:true,client:{entry:'client.mjs',package_name:'@fixture/counter'}})];
  await loadPlugins();await openPluginDetail('sample');
  assert.match(text($('pluginPage')), /Provider keys and conversation history stay private/);
  assert.ok(pageButton('Enable interface'));
  assert.equal(patchCalls().length,0);
  await togglePlugin('sample');
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body),{enabled:true});
  assert.ok(pageButton('Enable interface'));
  pluginConfigDirty=true;await enablePluginClient('sample');
  assert.equal(patchCalls().length,1);
  pluginConfigDirty=false;await enablePluginClient('sample');
  assert.deepEqual(JSON.parse(patchCalls()[1].options.body),{enabled:true,allow_client:true});
  assert.equal(pageButton('Enable interface'),undefined);
  assert.ok(pageButton('Open interface'));
  assert.equal(calls.some(call=>call.path.endsWith('/ui/open')),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
