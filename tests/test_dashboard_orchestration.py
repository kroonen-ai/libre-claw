# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess
import json
from pathlib import Path

import pytest

from libre_claw.web.dashboard import dashboard_html
from test_dashboard_plugins import PLUGIN_DOM


TEAM_DOM = r"""
Element.prototype.getAttribute = function(name) { return this.attrs[name] ?? null; };
Element.prototype.append = function(...children) { for (const child of children) { child.parent = this; this.children.push(child); } };
Element.prototype.replaceChildren = function(...children) { this.children = []; this.append(...children); };
Element.prototype.remove = function() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); };
const bindings = [];
const bindUI = (service, target, event, handler) => {
  bindings.push({service,target,event}); target.addEventListener(event, handler);
  return () => { if (target.listeners[event] === handler) delete target.listeners[event]; };
};
for (const id of ['runTeam','runTeamSummary','runProvider','runModel','sessionModel','messageAction','runMode','runWorktree','runMessage','sendMessage','runForm']) elements[id] = new Element();
elements.messageAction.options = [{},{},{}]; elements.messageAction.value = 'message';
elements.runModel.dispatchEvent = () => {};
const state = {selectedRunId:'',selectedRunState:'',sending:false};
const STREAM_STATES = new Set(['queued','running','blocked']);
let defaultModelConfig = {provider:'deepseek',model:'discovered-model'};
let profilesError = '', profileReady = true, providerReply, modelReply;
const providerPayload = {providers:[{id:'deepseek',label:'DeepSeek'},{id:'new-provider',label:'A future provider'}]};
const modelPayload = {models:[{model:'discovered-model',label:'Live model',supported_reasoning_efforts:['standard','intense']}]};
const teamConfig = {orchestrator:{provider:'',model:'',reasoning_effort:'',context_window_tokens:98304,max_output_tokens:16384,prompt:''},
  workers:[{id:'scout',name:'Scout',role:'scout',provider:'',model:'',reasoning_effort:'',read_only:true,max_concurrent:1,max_tool_calls:20,max_seconds:180,context_window_tokens:32768,max_output_tokens:4096,prompt:''}],
  default_worker:'scout',max_concurrent:3,max_total_workers:12};
const teamPlugin = (updates={}) => sample({id:'orchestration',name:'Model orchestration',config:JSON.parse(JSON.stringify(teamConfig)),...updates});
const request = async (path,options) => {
  if (path === '/providers') { calls.push({path,options}); return providerReply ? await providerReply : providerPayload; }
  if (path.startsWith('/models?')) { calls.push({path,options}); return modelReply ? await modelReply : modelPayload; }
  if (path === '/orchestration') {
    calls.push({path,options}); if (profilesError) throw new Error(profilesError);
    return {profiles:catalog.plugins.filter(plugin=>plugin.id==='orchestration').map(plugin=>({plugin_id:plugin.id,name:plugin.name,enabled:plugin.enabled,ready:profileReady && plugin.enabled && plugin.grants?.allow_model===true,orchestrator:plugin.config.orchestrator,workers:plugin.config.workers})),default_plugin:''};
  }
  if (path === '/orchestration/check') { calls.push({path,options}); return {ready:true,warnings:['Custom IDs are accepted; catalog verification may be incomplete.'],routes:[{worker_id:'scout',provider:'deepseek',model:'discovered-model',ready:true}],inference_requests:0}; }
  if (path === '/runs') { calls.push({path,options}); return {run:{run_id:'team-run',orchestration_plugin:JSON.parse(options.body).orchestration_plugin}}; }
  const result=await registryRequest(path,options);
  if (path==='/plugins/orchestration' && options?.method==='PATCH' && JSON.parse(options.body).allow_model===true) result.plugin.grants.allow_model=true;
  return result;
};
let closed=0, newTasks=0;
const closeSettingsPanel=()=>{closed++;};
const newSession=()=>{newTasks++;state.selectedRunId='';};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const setNotice=()=>{};
const autoGrow=()=>{};
const refreshRuns=async()=>{};
const selectRun=async id=>{state.selectedRunId=id;};
const sendTaskControl=async()=>{};
"""


def run_team(tmp_path, assertions: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for dashboard team interactions")
    html = dashboard_html()
    plugins = html[html.index("    /* Cordis plugins */"):html.index("    /* Settings modal */")]
    composer = html[html.index("    function composerMode("):html.index('    bindUI("workflows", $("automationForm"), "submit"')]
    script = tmp_path / "team.cjs"
    script.write_text(PLUGIN_DOM.replace("const request = async", "const registryRequest = async") + TEAM_DOM + plugins + composer + assertions)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_team_editor_discovers_provider_model_and_effort_suggestions_without_inference(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  catalog.plugins=[teamPlugin()]; await loadPlugins(); await openPluginDetail('orchestration'); await flush();
  const editor=orchestrationEditor;
  assert.match(text($('pluginPage')), /Orchestrator/); assert.match(text($('pluginPage')), /Worker team/);
  assert.equal(editor.workers[0].card.querySelectorAll('h5')[0].textContent,'Scout');
  assert.ok(editor.orchestrator.provider.children.some(option=>option.value==='new-provider'));
  assert.equal(editor.orchestrator.modelOptions.children[0].value,'discovered-model');
  assert.deepEqual(editor.orchestrator.efforts.children.map(option=>option.value),['standard','intense']);
  assert.equal(editor.workers[0].readOnly.disabled,true);
  assert.deepEqual(collectPluginConfig(),teamConfig);
  editor.orchestrator.model.value='future/custom-model'; editor.orchestrator.model.listeners.input();
  assert.equal(collectPluginConfig().orchestrator.model,'future/custom-model');
  assert.equal(calls.some(call=>call.path==='/runs'),false);
  assert.ok(bindings.some(binding=>binding.service==='plugins' && binding.target===editor.orchestrator.model));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_team_worker_editing_preserves_default_identity_and_validates_limits(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  catalog.plugins=[teamPlugin()]; await loadPlugins(); await openPluginDetail('orchestration'); await flush();
  const editor=orchestrationEditor; addOrchestrationWorker();
  const worker=editor.workers[1]; editor.defaultWorker.value=worker.id.value;
  worker.id.value='builder';worker.id.listeners.input();worker.name.value='Builder';worker.name.listeners.input();
  assert.equal(worker.card.querySelectorAll('h5')[0].textContent,'Builder');
  assert.equal(editor.defaultWorker.value,'builder');
  worker.role.value='worker';worker.role.listeners.change(); worker.readOnly.checked=false;worker.readOnly.listeners.change();
  worker.provider.value='new-provider';worker.provider.listeners.change();worker.model.value='future/model';worker.model.listeners.input();
  worker.prompt.value='Implement a focused change';worker.prompt.listeners.input();
  const config=collectPluginConfig();assert.equal(config.workers[1].read_only,false);assert.equal(config.default_worker,'builder');
  worker.numbers.max_seconds.value='0';assert.throws(collectPluginConfig,/whole number/);worker.numbers.max_seconds.value='180';
  worker.numbers.max_output_tokens.value='32768';assert.throws(collectPluginConfig,/smaller/);worker.numbers.max_output_tokens.value='4096';
  worker.id.value='scout';assert.throws(collectPluginConfig,/unique lowercase/);worker.id.value='builder';
  worker.remove.listeners.click();assert.equal(editor.workers.length,1);assert.equal(editor.defaultWorker.value,'scout');
  assert.equal(editor.workers[0].remove.disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_opening_team_details_starts_at_top_without_moving_the_editor_while_typing(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  catalog.plugins=[teamPlugin()];await loadPlugins();$('panePlugins').scrollTop=500;
  await openPluginDetail('orchestration');await flush();assert.equal($('panePlugins').scrollTop,0);
  $('panePlugins').scrollTop=240;
  orchestrationEditor.workers[0].name.value='Updated scout';orchestrationEditor.workers[0].name.listeners.input();
  assert.equal($('panePlugins').scrollTop,240);
  renderPluginDetail();assert.equal($('panePlugins').scrollTop,240);
  await leavePluginPage();$('panePlugins').scrollTop=800;
  await openPluginDetail('orchestration');assert.equal($('panePlugins').scrollTop,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_team_save_and_route_check_use_saved_configuration_and_do_not_run_models(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  catalog.plugins=[teamPlugin()]; await loadPlugins(); await openPluginDetail('orchestration'); await flush();
  orchestrationEditor.maxConcurrent.value='4';orchestrationEditor.maxConcurrent.listeners.input();
  await checkOrchestrationRoutes();assert.equal(calls.some(call=>call.path==='/orchestration/check'),false);
  await savePluginConfig();
  const saved=calls.find(call=>call.options?.method==='PUT');assert.equal(saved.path,'/plugins/orchestration/config');
  assert.equal(JSON.parse(saved.options.body).config.max_concurrent,4);
  assert.equal(pluginConfigDirty,false);
  await checkOrchestrationRoutes();
  assert.deepEqual(JSON.parse(calls.find(call=>call.path==='/orchestration/check').options.body),{plugin_id:'orchestration'});
  assert.match(orchestrationEditor.status.textContent,/Custom IDs are accepted/);
  assert.match(text(orchestrationEditor.routeResults),/scout deepseek \/ discovered-model Ready/);
  assert.equal(calls.some(call=>call.path==='/runs'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_included_manifest_configuration_roundtrips_without_losing_role_defaults(tmp_path):
    manifest = Path(__file__).resolve().parents[1] / "src/libre_claw/cordis_runtime/examples/orchestration/libre-claw-plugin.json"
    config = json.loads(manifest.read_text())["config"]
    run_team(tmp_path, "const manifestConfig = " + json.dumps(config) + r""";
(async()=>{
  catalog.plugins=[teamPlugin({config:manifestConfig})];await loadPlugins();await openPluginDetail('orchestration');await flush();
  assert.deepEqual(collectPluginConfig(),manifestConfig);
  assert.equal(orchestrationEditor.workers.find(worker=>worker.id.value==='builder').readOnly.checked,false);
  assert.equal(orchestrationEditor.workers.find(worker=>worker.id.value==='reviewer').readOnly.disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_enabling_a_team_grants_only_model_access_and_next_task_omits_model_overrides(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  catalog.plugins=[teamPlugin()]; await loadPlugins(); await openPluginDetail('orchestration'); await flush();
  await togglePlugin('orchestration');
  assert.deepEqual(JSON.parse(patchCalls()[0].options.body),{enabled:true,allow_model:true});
  await useOrchestrationForNextTask(); assert.equal(closed,1);assert.equal(newTasks,1);
  assert.equal(selectedOrchestrationPlugin,'orchestration');assert.equal($('runProvider').hidden,true);assert.equal($('runModel').disabled,true);
  assert.match($('runTeamSummary').textContent,/Model orchestration/);
  $('runProvider').value='wrong-provider';$('runModel').value='wrong-model';$('runMessage').value='Build the app';
  await $('runForm').listeners.submit({preventDefault(){}});
  const body=JSON.parse(calls.find(call=>call.path==='/runs').options.body);
  assert.equal(body.orchestration_plugin,'orchestration');assert.equal('provider' in body,false);assert.equal('model' in body,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_unavailable_team_cannot_silently_fall_back_to_a_normal_run(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  catalog.plugins=[teamPlugin({enabled:true,grants:{allow_model:true}})];await refreshOrchestrationProfiles();
  selectedOrchestrationPlugin='orchestration';syncComposerMode();assert.equal($('sendMessage').disabled,false);
  profilesError='offline';await refreshOrchestrationProfiles();
  assert.equal(selectedOrchestrationPlugin,'orchestration');assert.equal($('sendMessage').disabled,true);
  $('runMessage').value='Build';await $('runForm').listeners.submit({preventDefault(){}});
  assert.equal(calls.some(call=>call.path==='/runs'),false);
  selectedOrchestrationPlugin='';syncComposerMode();assert.equal($('runProvider').hidden,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


def test_late_provider_discovery_does_not_overwrite_edits_or_a_closed_editor(tmp_path):
    run_team(tmp_path, r"""
(async()=>{
  let resolveProviders;providerReply=new Promise(resolve=>{resolveProviders=resolve;});
  const config=JSON.parse(JSON.stringify(teamConfig));config.orchestrator.provider='deepseek';
  catalog.plugins=[teamPlugin({config})]; await loadPlugins(); await openPluginDetail('orchestration');
  const editor=orchestrationEditor;assert.equal(collectPluginConfig().orchestrator.provider,'deepseek');
  editor.orchestrator.provider.value='';editor.orchestrator.provider.listeners.change();
  resolveProviders(providerPayload);await flush();assert.equal(editor.orchestrator.provider.value,'');
  await leavePluginPage();assert.equal(orchestrationEditor,null);
  assert.equal(editor.orchestrator.provider.listeners.change,undefined);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
