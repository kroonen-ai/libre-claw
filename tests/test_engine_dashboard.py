# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess

import pytest

from libre_claw.web.dashboard import dashboard_html


ENGINE_DOM = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.textContent = ''; this.disabled = false; this.open = false; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tagName === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
  set innerHTML(value) { throw new Error('Engine metadata must not be parsed as HTML'); }
}
const elements = Object.fromEntries(['refreshEngine','restartEngine','engineRestartHint','engineComponents','engineIdentity',
  'engineState','engineStrip','engineStatus','engineCounts','enginePrivacy'].map(id => [id, new Element()]));
const $ = id => elements[id];
const document = {createElement: tag => new Element(tag)};
const text = node => [node.textContent, ...node.children.map(text)].filter(Boolean).join(' ');
let snapshot = {engine:'cordis', runtime_version:'4.0.2', state:'running', active_operations:0,
  privacy:{network:false,payloads:'opaque-handles',extensions:'separate-processes'},
  components:[{id:'agent',title:'Agent',enabled:true,state:'ACTIVE',dependencies:['tools','providers','sessions'],methods:['run'],active_operations:0,completed:4,failed:1,cancelled:2}]};
let error = '', hold = false, complete;
const requests = [];
const request = async (path, options) => {
  requests.push({path,options});
  if (hold) await new Promise(resolve => { complete = resolve; });
  if (error) throw new Error(error);
  return JSON.parse(JSON.stringify(snapshot));
};
"""


def run_engine_ui(tmp_path, assertions: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for dashboard interaction tests")
    html = dashboard_html()
    source = html[html.index("    /* Cordis engine */"):html.index("    /* Cordis plugins */")]
    script = tmp_path / "engine-ui.cjs"
    script.write_text(ENGINE_DOM + source + assertions)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_engine_live_counters_dependencies_and_method_metadata_render_as_text(tmp_path):
    run_engine_ui(tmp_path, r"""
(async () => {
  engineActiveRuns = 0;
  await refreshEngine();
  assert.equal($('engineStrip').textContent, 'Cordis · ready');
  assert.equal($('engineCounts').textContent, '1 / 1 active');
  assert.match(text($('engineComponents')), /Uses tools · providers · sessions/);
  assert.match(text($('engineComponents')), /Working 0 Completed 4 Failed 1 Cancelled 2/);
  assert.match(text($('engineComponents')), /agent.run/);
  assert.match(text($('enginePrivacy')), /Core network blocked/);
  assert.equal($('restartEngine').disabled, false);
  snapshot.components[0].implementations = {run:'custom-agent',inspect:'libre-claw'};
  await refreshEngine();
  assert.match(text($('engineComponents')), /agent.run · custom-agent/);
  assert.doesNotMatch(text($('engineComponents')), /inspect · libre-claw/);
  snapshot.components[0].title = '<img src=x onerror=alert(1)>';
  snapshot.components[0].methods = ['<script>data</script>'];
  await refreshEngine();
  assert.match(text($('engineComponents')), /<img src=x onerror=alert\(1\)>/);
  assert.match(text($('engineComponents')), /agent.<script>data<\/script>/);
  assert.equal(requests.some(request => request.options?.method), false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_engine_restart_is_explicit_bounded_and_refuses_busy_or_unknown_health(tmp_path):
    run_engine_ui(tmp_path, r"""
(async () => {
  await refreshEngine(); await restartCoreEngine();
  assert.equal(requests.length, 1);
  engineActiveRuns = 1; await restartCoreEngine(); assert.equal(requests.length, 1);
  engineActiveRuns = 0; snapshot.active_operations = 2; await refreshEngine();
  assert.equal($('restartEngine').disabled, true);
  await restartCoreEngine(); assert.equal(requests.length, 2);
  snapshot.active_operations = 0; await refreshEngine();
  hold = true;
  const restarting = restartCoreEngine();
  assert.equal($('restartEngine').textContent, 'Restarting…');
  assert.equal($('restartEngine').disabled, true);
  await restartCoreEngine(); await refreshEngine();
  assert.equal(requests.filter(request => request.options?.method === 'POST').length, 1);
  assert.deepEqual(requests.at(-1), {path:'/engine/restart',options:{method:'POST',body:'{}'}});
  complete(); await restarting;
  assert.equal($('restartEngine').disabled, false);
  assert.equal($('engineState').textContent, 'Running');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_engine_errors_clear_stale_health_and_allow_recovery(tmp_path):
    run_engine_ui(tmp_path, r"""
(async () => {
  engineActiveRuns = 0; await refreshEngine();
  error = 'Node.js was not found'; await refreshEngine();
  assert.equal($('engineState').textContent, 'Unavailable');
  assert.equal($('engineStrip').textContent, 'Cordis · unavailable');
  assert.equal($('engineComponents').children.length, 0);
  assert.equal($('enginePrivacy').children.length, 0);
  assert.match($('engineStatus').textContent, /Node.js was not found/);
  assert.equal($('restartEngine').disabled, false);
  await restartCoreEngine();
  assert.match($('engineStatus').textContent, /Could not restart engine/);
  error = ''; await restartCoreEngine();
  assert.equal($('engineState').textContent, 'Running');
  snapshot = {engine:'not-cordis',components:[]}; await refreshEngine();
  assert.match($('engineStatus').textContent, /Invalid engine status/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_engine_poll_preserves_expanded_methods_and_never_claims_disabled_agent_ready(tmp_path):
    run_engine_ui(tmp_path, r"""
(async () => {
  await refreshEngine();
  const detail = $('engineComponents').querySelectorAll('details')[0]; detail.open = true;
  await refreshEngine();
  assert.equal($('engineComponents').querySelectorAll('details')[0], detail);
  snapshot.components[0].completed++; await refreshEngine();
  assert.equal($('engineComponents').querySelectorAll('details')[0].open, true);
  snapshot.components[0].state = 'DISABLED'; await refreshEngine();
  assert.equal($('engineStrip').textContent, 'Cordis · paused');
  assert.match($('engineStatus').textContent, /agent service is unavailable/);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
