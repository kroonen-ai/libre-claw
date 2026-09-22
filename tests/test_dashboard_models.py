# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from libre_claw.web.dashboard import dashboard_html


def run_model_script(tmp_path: Path, assertions: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for dashboard model interaction verification")
    html = dashboard_html()
    source = html[html.index("    let defaultModelConfig ="):html.index("    async function loadLlamacppConfig(")]
    source += html[html.index('    $("modelForm").addEventListener('):html.index("    const workflow =")]
    script = tmp_path / "models.js"
    script.write_text(MODEL_DOM + source + assertions)
    result = subprocess.run([node, str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


MODEL_DOM = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(id = '') { this.id = id; this.value = ''; this.textContent = ''; this.options = []; this.children = []; this.siblings = []; this.listeners = {}; this.dataset = {}; this.hidden = false; }
  after(child) { this.siblings.push(child); }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  setAttribute(name, value) { this[name] = value; }
  getAttribute(name) { return this[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  async emit(name) { for (const callback of this.listeners[name] || []) await callback({preventDefault() {}}); }
  querySelector() { return submitButton; }
}
const ids = ['configProvider', 'runProvider', 'automationProvider', 'configModel', 'runModel', 'automationModel', 'modelRoute', 'modelCurrent', 'llamacppSettings', 'providerRouteHint', 'modelDiscoveryStatus', 'refreshModels', 'modelForm'];
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
const $ = id => elements[id];
const submitButton = new Element('submit');
const options = [{value:'deepseek', textContent:'DeepSeek'}, {value:'openrouter', textContent:'OpenRouter'}, {value:'llamacpp', textContent:'llama.cpp'}];
for (const id of ['configProvider', 'runProvider', 'automationProvider']) $(id).options = [...(id === 'configProvider' ? [] : [{value: '', textContent: 'default'}]), ...options];
$('configProvider').value = 'openrouter';
const document = {createElement: () => new Element()};
const notices = [], calls = [], pending = [];
const setNotice = message => notices.push(message);
const formatCompactNumber = value => String(value);
const flush = () => new Promise(resolve => setImmediate(resolve));
let currentDefault = {provider:'deepseek', model:'deepseek-flash'};
let holdDiscovery = false, discoveryError = false;
const request = async (path, options) => {
  calls.push({path, options});
  if (path === '/config/model') {
    if (options?.method === 'PATCH') {
      const body = JSON.parse(options.body);
      currentDefault = {provider: body.provider, model: body.model};
    }
    return {...currentDefault};
  }
  if (holdDiscovery) return new Promise(resolve => pending.push({path, resolve}));
  if (discoveryError) throw new Error('Offline');
  const provider = new URL(path, 'http://localhost').searchParams.get('provider');
  return {source:'live', provider, models:[{model: provider === 'deepseek' ? 'deepseek-flash' : 'provider/new-model', label:'Discovered model'}]};
};
"""


def test_default_route_is_visible_on_load_and_after_save(tmp_path: Path) -> None:
    run_model_script(tmp_path, r"""
(async () => {
  await flush();
  assert.equal($('modelRoute').textContent, 'DeepSeek / deepseek-flash');
  assert.equal($('runProvider').options[0].textContent, 'Default · DeepSeek');
  assert.equal($('automationProvider').options[0].textContent, 'Default · DeepSeek');
  assert.equal($('runModel').placeholder, 'Default: deepseek-flash');
  assert.equal($('configModel').value, 'deepseek-flash');
  assert.equal($('llamacppSettings').hidden, true);
  $('runModel').value = 'my/manual-model';
  $('configProvider').value = 'openrouter';
  await $('configProvider').emit('change'); await flush();
  assert.equal($('configModel').value, '');
  $('configModel').value = 'provider/new-model';
  await $('modelForm').emit('submit'); await flush();
  assert.equal($('modelRoute').textContent, 'OpenRouter / provider/new-model');
  assert.equal($('runProvider').options[0].textContent, 'Default · OpenRouter');
  assert.equal($('runModel').placeholder, 'Default: provider/new-model');
  assert.equal($('runModel').value, 'my/manual-model');
  assert.equal(submitButton.disabled, false);
  const saved = JSON.parse(calls.find(call => call.options?.method === 'PATCH').options.body);
  assert.deepEqual(saved, {provider:'openrouter', model:'provider/new-model', persist_global:true});
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_discovery_refresh_rejects_stale_results_and_keeps_manual_ids(tmp_path: Path) -> None:
    run_model_script(tmp_path, r"""
(async () => {
  await flush(); holdDiscovery = true;
  await $('refreshModels').emit('click');
  assert.ok(pending[0].path.includes('refresh=1'));
  assert.equal($('refreshModels').disabled, true);
  $('configProvider').value = 'llamacpp';
  await $('configProvider').emit('change');
  assert.equal($('llamacppSettings').hidden, false);
  $('configModel').value = 'manual-local-model';
  pending[1].resolve({source:'live', models:[{model:'fresh-local-model', label:'Fresh'}]});
  await flush();
  pending[0].resolve({source:'live', models:[{model:'stale-remote-model', label:'Stale'}]});
  await flush();
  const list = $('configModel').siblings.find(node => node.id === 'configModelModels');
  assert.deepEqual(list.children.map(option => option.value), ['fresh-local-model']);
  assert.equal($('configModel').value, 'manual-local-model');
  assert.equal($('refreshModels').disabled, false);
  holdDiscovery = false; discoveryError = true;
  await $('refreshModels').emit('click'); await flush();
  assert.match($('modelDiscoveryStatus').textContent, /Discovery unavailable/);
  assert.equal($('configModel').value, 'manual-local-model');
  assert.equal($('configModel').disabled, undefined);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_model_metadata_keeps_unknown_limits_and_prices_explicit_but_concise(tmp_path: Path) -> None:
    run_model_script(tmp_path, r"""
assert.equal(modelCapabilitySummary({}), 'Capabilities not published by this provider.');
assert.equal(modelCapabilitySummary(null), 'Capabilities not published for this model.');
const partial = modelCapabilitySummary({supports_tools:true, supports_vision:false, context_window_tokens:128000});
assert.match(partial, /Tools/); assert.match(partial, /No images/); assert.match(partial, /128000 context/);
assert.equal(partial.match(/unavailable/g).length, 1);
assert.ok(!partial.includes('$0'));
const known = modelCapabilitySummary({supports_tools:true, supports_vision:false, supports_reasoning:true, context_window_tokens:128000, max_completion_tokens:8192, input_cost_per_token:0, output_cost_per_token:0.000002});
assert.match(known, /\$0\/M in \/ \$2\/M out/);
assert.ok(!known.includes('unavailable'));
""")


def test_capability_detail_visibility_and_accessible_model_names(tmp_path: Path) -> None:
    run_model_script(tmp_path, r"""
(async () => {
  await flush();
  const detail = id => $(id).siblings.find(node => node.id === `${id}Capabilities`);
  assert.equal(detail('runModel').hidden, true);
  assert.equal(detail('automationModel').hidden, true);
  assert.equal(detail('configModel').hidden, false);
  assert.equal($('configModel')['aria-label'], 'Model');
  assert.equal($('automationModel')['aria-label'], 'Schedule model');
  assert.equal($('configModel')['aria-describedby'], 'configModelCapabilities');
  holdDiscovery = true;
  const update = modelPickers.get('runModel').refresh();
  pending[0].resolve({source:'live', models:[{model:'deepseek-flash', supports_tools:true}]});
  await update;
  assert.equal(detail('runModel').hidden, false);
  assert.match(detail('runModel').textContent, /Tools/);
  $('runModel').hidden = true;
  await $('runModel').emit('input');
  assert.equal(detail('runModel').hidden, true);
  $('runModel').hidden = false;
  await $('runModel').emit('input');
  assert.equal(detail('runModel').hidden, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
