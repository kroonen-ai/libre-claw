# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def run_ui(tmp_path: Path, body: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for Cordis browser service tests")
    source = Path(__file__).resolve().parents[1] / "src/libre_claw"
    shutil.copyfile(source / "web/assets/cordis-ui.mjs", tmp_path / "cordis-ui.mjs")
    shutil.copyfile(source / "cordis_runtime/vendor/cordis.mjs", tmp_path / "cordis.mjs")
    script = tmp_path / "test.mjs"
    script.write_text("""
import assert from 'node:assert/strict';
import {mountDashboard,CordisUiError} from './cordis-ui.mjs';
class Target extends EventTarget {
  listeners = new Set();
  addEventListener(event, callback, options) { this.listeners.add(callback); super.addEventListener(event, callback, options); }
  removeEventListener(event, callback, options) { this.listeners.delete(callback); super.removeEventListener(event, callback, options); }
}
const response = payload => ({ok:true,statusText:'OK',json:async()=>payload});
class NodeTarget extends Target {
  nodeType=1; isConnected=false; children=[];
  connect(value) { this.isConnected=value; for(const child of this.children) child.connect(value); }
  append(child) { this.children.push(child); child.connect(this.isConnected); }
  replaceChildren(...children) { for(const child of this.children) child.connect(false); this.children=[]; for(const child of children) this.append(child); }
  contains(target) { return this===target || this.children.some(child=>child.contains(target)); }
}
class Observer {
  static latest; constructor(callback){this.callback=callback;Observer.latest=this;}
  observe(root){this.root=root;this.observing=true;} disconnect(){this.observing=false;}
  flush(){if(this.observing)this.callback();}
}
""" + body)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_real_cordis_ui_mounts_once_and_disposes_listeners_timers_and_hooks(tmp_path):
    run_ui(tmp_path, """
const scope={}, target=new Target(), lifecycle=new Target(), scheduled=new Map();
let clicks=0, setups=0, cleanups=0, nextTimer=0;
const options={scope,lifecycle,fetcher:async()=>response({}),timers:{
  setInterval(handler){const id=++nextTimer;scheduled.set(id,handler);return id;}, clearInterval(id){scheduled.delete(id);}
},features:{tasks:{setup(){setups++;return()=>cleanups++;},bindings:[{target,event:'click',handler:()=>clicks++}],intervals:[{handler:()=>clicks++,milliseconds:3000}]}}};
const first=mountDashboard(options), second=mountDashboard(options);
assert.equal(first,second);
const ui=await first;
assert.equal(setups,1); assert.equal(target.listeners.size,1); assert.equal(scheduled.size,1);
assert.equal(ui.inspect().components.length,8);
assert.ok(ui.inspect().components.every(component=>component.state==='ACTIVE'));
target.dispatchEvent(new Event('click')); assert.equal(clicks,1);
scheduled.values().next().value(); assert.equal(clicks,2);
await ui.dispose();
assert.equal(cleanups,1); assert.equal(target.listeners.size,0); assert.equal(lifecycle.listeners.size,0); assert.equal(scheduled.size,0);
target.dispatchEvent(new Event('click')); assert.equal(clicks,2);
assert.throws(()=>ui.request('/runs'),CordisUiError);
const replacement=await mountDashboard(options); assert.notEqual(replacement,ui); assert.equal(setups,2);
await replacement.dispose();
""")


def test_missing_cordis_dependency_prevents_feature_effects_and_requests(tmp_path):
    run_ui(tmp_path, """
const target=new Target();let calls=0, clicks=0;
const ui=await mountDashboard({scope:{},enabled:{models:false},fetcher:async()=>{calls++;return response({});},features:{tasks:{bindings:[{target,event:'click',handler:()=>clicks++}]}}});
const states=Object.fromEntries(ui.inspect().components.map(component=>[component.id,component.state]));
assert.equal(states.models,'DISABLED'); assert.equal(states.tasks,'PENDING'); assert.equal(states.questions,'PENDING');
assert.equal(target.listeners.size,0); target.dispatchEvent(new Event('click')); assert.equal(clicks,0);
assert.throws(()=>ui.request('/runs'),/tasks service is unavailable/);
assert.throws(()=>ui.request('/runs/run/questions/question'),/questions service is unavailable/);
await ui.request('/engine'); assert.equal(calls,1);
await ui.dispose();
""")


def test_ui_api_routes_through_active_features_and_blocks_external_destinations(tmp_path):
    run_ui(tmp_path, """
const requests=[];
const ui=await mountDashboard({scope:{},origin:'http://127.0.0.1:8766',enabled:{plugins:false},fetcher:async(path,options)=>{requests.push({path,options});return response({ok:true});}});
assert.deepEqual(await ui.request('/config/theme',{method:'PATCH',body:'{}',credentials:'include'}),{ok:true});
assert.equal(requests[0].path,'/config/theme'); assert.equal(requests[0].options.credentials,'same-origin');
assert.equal(requests[0].options.redirect,'error');
assert.equal(requests[0].options.headers['Content-Type'],'application/json');
assert.throws(()=>ui.request('/plugins'),/plugins service is unavailable/);
assert.throws(()=>ui.request('/runs/../plugins'),/plugins service is unavailable/);
for(const path of ['https://telemetry.invalid/collect','//telemetry.invalid/collect','/\\\\telemetry.invalid/collect']) assert.throws(()=>ui.request(path),/local daemon API/);
assert.equal(requests.length,1); await ui.dispose();
""")


def test_api_failure_preserves_graph_and_disposal_aborts_inflight_fetches(tmp_path):
    run_ui(tmp_path, """
let mode='failure', signal;
const ui=await mountDashboard({scope:{},fetcher:async(path,options)=>{
  if(mode==='failure') return {ok:false,statusText:'Unavailable',json:async()=>({error:'Core engine is offline'})};
  signal=options.signal;
  return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('Aborted')),{once:true}));
}});
await assert.rejects(ui.request('/engine'),/Core engine is offline/);
assert.equal(ui.inspect().state,'active');
mode='pending'; const pending=ui.request('/runs'); const rejection=assert.rejects(pending,/Aborted/);
await ui.dispose(); await rejection; assert.equal(signal.aborted,true);
""")


def test_page_disposal_and_feature_event_errors_are_owned_by_cordis(tmp_path):
    run_ui(tmp_path, """
const lifecycle=new Target(), target=new Target(), errors=[];
const ui=await mountDashboard({scope:{},lifecycle,fetcher:async()=>response({}),onError:error=>errors.push(error.message),features:{questions:{bindings:[{target,event:'submit',handler:async()=>{throw new Error('Could not send answer');}}]}}});
target.dispatchEvent(new Event('submit')); await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(errors,['Could not send answer']);
lifecycle.dispatchEvent(new Event('pagehide')); await ui.dispose();
assert.equal(ui.inspect().state,'disposed'); assert.equal(target.listeners.size,0);
""")


def test_failed_feature_activation_unwinds_existing_effects_and_allows_fresh_boot(tmp_path):
    run_ui(tmp_path, """
const scope={}, target=new Target();
await assert.rejects(mountDashboard({scope,fetcher:async()=>response({}),features:{
  appearance:{bindings:[{target,event:'click',handler:()=>{}}]},
  tasks:{setup(){throw new Error('Component failed');}}
}}),/Component failed/);
assert.equal(target.listeners.size,0);
const ui=await mountDashboard({scope,fetcher:async()=>response({})});
assert.ok(ui.inspect().components.every(component=>component.state==='ACTIVE'));
await ui.dispose();
""")


def test_dynamic_dom_replacements_dispose_old_controls_without_accumulating_effects(tmp_path):
    run_ui(tmp_path, """
const root=new NodeTarget();root.nodeType=9;root.connect(true);
const ui=await mountDashboard({scope:root,MutationObserver:Observer,fetcher:async()=>response({})});
let changes=0, previous;
for(let index=0;index<100;index++){
  const button=new NodeTarget();ui.bind('plugins',button,'click',()=>changes++);
  root.replaceChildren(button);
  if(previous){previous.dispatchEvent(new Event('click'));assert.equal(previous.listeners.size,0);}
  button.dispatchEvent(new Event('click'));
  Observer.latest.flush();await Promise.resolve();
  const effects=ui.inspect().components.find(component=>component.id==='plugins').effects;
  assert.deepEqual(effects,{scopes:1,bindings:1,timers:0});previous=button;
}
assert.equal(changes,100);
root.replaceChildren();Observer.latest.flush();
assert.deepEqual(ui.inspect().components.find(component=>component.id==='plugins').effects,{scopes:0,bindings:0,timers:0});
await ui.dispose();assert.equal(Observer.latest.observing,false);
""")


def test_detached_and_disabled_feature_controls_cannot_mutate_local_state(tmp_path):
    run_ui(tmp_path, """
const root=new NodeTarget();root.nodeType=9;root.connect(true);
const ui=await mountDashboard({scope:root,MutationObserver:Observer,fetcher:async()=>response({})});
let changes=0;const button=new NodeTarget();root.append(button);
ui.bind('tasks',button,'click',()=>changes++);button.dispatchEvent(new Event('click'));assert.equal(changes,1);
const disabling=ui.disable('models');button.dispatchEvent(new Event('click'));assert.equal(changes,1);
await disabling;assert.equal(button.listeners.size,0);assert.equal(ui.active('tasks'),false);
assert.throws(()=>ui.run('tasks',()=>changes++),/unavailable/);
assert.throws(()=>ui.bind('tasks',button,'click',()=>changes++),/unavailable/);
assert.throws(()=>ui.request('/runs'),/unavailable/);
await ui.dispose();button.dispatchEvent(new Event('click'));assert.equal(changes,1);
""")


def test_node_scoped_timers_and_animation_frames_are_released_on_replacement(tmp_path):
    run_ui(tmp_path, """
const root=new NodeTarget();root.nodeType=9;root.connect(true);
const scheduled=new Map();let next=0, changes=0;
const timers={setTimeout(callback){scheduled.set(++next,callback);return next;},clearTimeout(id){scheduled.delete(id);},
requestAnimationFrame(callback){scheduled.set(++next,callback);return next;},cancelAnimationFrame(id){scheduled.delete(id);}};
const ui=await mountDashboard({scope:root,MutationObserver:Observer,timers,fetcher:async()=>response({})});
const button=new NodeTarget();root.append(button);
ui.timeout('tasks',()=>changes++,100,button);ui.frame('tasks',()=>changes++,button);
const late=[...scheduled.values()];root.replaceChildren();Observer.latest.flush();assert.equal(scheduled.size,0);
for(const callback of late)callback();assert.equal(changes,0);
const active=new NodeTarget();root.append(active);
for(let index=0;index<100;index++){ui.timeout('tasks',()=>changes++,10,active);scheduled.values().next().value();}
assert.equal(changes,100);assert.equal(scheduled.size,0);
assert.deepEqual(ui.inspect().components.find(component=>component.id==='tasks').effects,{scopes:0,bindings:0,timers:0});
await ui.dispose();
""")


def test_explicit_scope_release_preserves_unrelated_features_and_rejects_late_response(tmp_path):
    run_ui(tmp_path, """
const root=new NodeTarget();root.nodeType=9;root.connect(true);
let finish;const ui=await mountDashboard({scope:root,MutationObserver:Observer,fetcher:()=>new Promise(resolve=>{finish=resolve;})});
const form=new NodeTarget(),input=new NodeTarget(),unrelated=new Target();root.append(form);form.append(input);
ui.bind('plugins',input,'input',()=>{});ui.bind('appearance',unrelated,'change',()=>{});
ui.release(form);assert.equal(input.listeners.size,0);assert.equal(unrelated.listeners.size,1);
const pending=ui.request('/models');const rejected=assert.rejects(pending,/stopped/);
await ui.disable('models');finish(response({models:['must-not-reach-caller']}));await rejected;
await ui.dispose();assert.equal(unrelated.listeners.size,0);
""")
