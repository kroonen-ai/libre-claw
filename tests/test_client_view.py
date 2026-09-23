# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


DOM = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ClientView} from './client-view.mjs';
const schema=JSON.parse(readFileSync(new URL('./client-schema.json',import.meta.url),'utf8'));
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.childNodes=[];this.dataset={};this.attributes={};this.listeners={};this.style={};this.value='';this.checked=false;this.selectionStart=0;this.selectionEnd=0;}
  get type(){return this.attributes.type || '';}
  get isConnected(){return this.root===true || this.parentNode?.isConnected===true;}
  get lastChild(){return this.childNodes.at(-1);}
  set textContent(text){this.text=String(text);this.replaceChildren();}
  get textContent(){return (this.text || '')+this.childNodes.map(node=>node.textContent).join('');}
  setAttribute(name,value){this.attributes[name]=value;}
  removeAttribute(name){delete this.attributes[name];if(name==='style')this.style={};}
  insertBefore(node,before){node.remove();const index=before?this.childNodes.indexOf(before):-1;if(index<0)this.childNodes.push(node);else this.childNodes.splice(index,0,node);node.parentNode=this;}
  replaceChildren(...nodes){for(const node of [...this.childNodes])node.remove();for(const node of nodes)this.insertBefore(node,null);}
  remove(){if(this.parentNode){this.parentNode.childNodes=this.parentNode.childNodes.filter(node=>node!==this);this.parentNode=null;}}
  contains(node){return node===this || this.childNodes.some(child=>child.contains(node));}
  closest(selector){if(selector==='form' && this.tagName==='FORM' || selector==='[data-client-node]' && this.dataset.clientNode)return this;return this.parentNode?.closest(selector)||null;}
  querySelectorAll(selector){const tags=selector.split(',').map(tag=>tag.toUpperCase());return this.childNodes.flatMap(node=>[...(tags.includes(node.tagName)?[node]:[]),...node.querySelectorAll(selector)]);}
  focus(){document.activeElement=this;}
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
  emit(type){const event={type,target:this,preventDefault(){this.prevented=true;}};for(const handler of [...(this.listeners[type]||[])])handler(event);return event;}
  set innerHTML(value){throw Error('Untrusted HTML must never execute');}
}
const document={activeElement:null,createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}};
globalThis.document=document;
const root=new Element('div');root.root=true;
const bindings=new Set(),timers=new Set(),calls=[],statuses=[];
const bind=(node,event,handler)=>{(node.listeners[event]??=[]).push(handler);const dispose=()=>{node.listeners[event]=node.listeners[event].filter(item=>item!==handler);bindings.delete(dispose);};bindings.add(dispose);return dispose;};
const timeout=handler=>{const dispose=()=>timers.delete(dispose);dispose.handler=handler;timers.add(dispose);return dispose;};
const node=(id,tag,props={},events={},children=[])=>({id,tag,props,events,children});
const textNode=(id,text)=>({...node(id,'#text'),text});
let revision=1,firstValue='start',secondValue='other',hold=false,release;
const snapshot=()=>({revision,status:'active',views:[{id:'view-one',slot:'shell.overlay',mode:'panel',tree:[
  node('form-one','form',{}, {submit:'submit-one'},[node('input-one','input',{id:'runMessage',name:'one',value:firstValue},{change:'change-one'}),node('button-one','button',{type:'button'},{click:'click-one'},[textNode('button-text','Save')])]),
  node('form-two','form',{}, {},[node('input-two','input',{name:'two',value:secondValue})]),
]}],warnings:[]});
const request=async(path,options={})=>{calls.push({path,options});if(options.method==='DELETE')return{closed:true};
  if(options.method==='POST'){const body=JSON.parse(options.body);assert.equal(body.revision,revision);if(hold)await new Promise(resolve=>release=resolve);if(body.values['input-one'])firstValue=body.values['input-one'].value;revision++;}
  return{ui_session_id:'session',snapshot:snapshot()};};
const view=new ClientView({container:root,schema,pluginId:'plugin /name',sessionId:'session',request,bind,timeout,status:(text,error)=>statuses.push({text,error})});
const all=node=>[node,...node.childNodes.flatMap(all)];
const find=id=>all(root).find(node=>node.dataset.clientNode===id);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
"""


def run_renderer(tmp_path, assertions: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for isolated client renderer tests")
    root = Path(__file__).resolve().parents[1] / "src/libre_claw"
    shutil.copyfile(root / "web/assets/client-view.mjs", tmp_path / "client-view.mjs")
    shutil.copyfile(root / "cordis_runtime/client-schema.json", tmp_path / "client-schema.json")
    script = tmp_path / "renderer.mjs"
    script.write_text(DOM + assertions)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_renderer_preserves_focused_nodes_and_user_drafts_during_serialized_events(tmp_path):
    run_renderer(tmp_path, r"""
view.render(snapshot());const input=find('input-one');input.focus();input.selectionStart=3;input.selectionEnd=3;
assert.equal(input.attributes.id,'client-session-runMessage');
hold=true;input.value='a';input.emit('input');input.value='abc';input.emit('input');
assert.equal(calls.length,1);hold=false;release();await flush();await flush();
assert.equal(calls.length,2);assert.equal(find('input-one'),input);assert.equal(input.value,'abc');assert.equal(document.activeElement,input);
assert.equal(JSON.parse(calls[0].options.body).revision,1);assert.equal(JSON.parse(calls[1].options.body).revision,2);
const other=find('input-two');other.value='unsent other-form draft';other.emit('input');
find('button-one').emit('click');await flush();
assert.equal(other.value,'unsent other-form draft');assert.equal('input-two' in JSON.parse(calls.at(-1).options.body).values,false);
await view.close();
""")


def test_renderer_disposes_removed_controls_polling_and_the_offline_session(tmp_path):
    run_renderer(tmp_path, r"""
view.render(snapshot());view.startPolling();assert.equal(timers.size,1);
const button=find('button-one');const previous=bindings.size;
const empty={revision:++revision,status:'active',views:[],warnings:[]};view.render(empty);
assert.ok(previous>0);assert.equal(bindings.size,0);button.emit('click');await flush();assert.equal(calls.length,0);
await view.close();assert.equal(timers.size,0);assert.equal(calls.at(-1).path,'/plugins/plugin%20%2Fname/ui/session');assert.equal(calls.at(-1).options.method,'DELETE');
await view.close();assert.equal(calls.length,1);
""")


@pytest.mark.parametrize("mutation", [
    "bad.views[0].tree[0].tag='script'",
    "bad.views[0].tree[0].props.onclick='alert(1)'",
    "bad.views[0].tree[0].props.style={backgroundColor:'url(https://telemetry.invalid)'}",
    "bad.views[0].tree[0].props.style={position:'fixed'}",
    "bad.views[0].tree[0].children[0].props.type='file'",
])
def test_renderer_rejects_executable_network_and_private_file_capabilities(tmp_path, mutation):
    run_renderer(tmp_path, "view.render(snapshot());const bad=snapshot();" + mutation + ";assert.throws(()=>view.render(bad));await view.close();")


def test_guest_data_cannot_replace_renderer_owned_node_identifiers(tmp_path):
    run_renderer(tmp_path, r"""
const value=snapshot();value.views[0].tree[0].children[0].props['data-client-node']='forged';view.render(value);
assert.equal(find('input-one').dataset.clientNode,'input-one');
const form=find('form-one');const event=form.emit('submit');assert.equal(event.prevented,true);await flush();
assert.ok(JSON.parse(calls[0].options.body).values['input-one']);assert.equal('forged' in JSON.parse(calls[0].options.body).values,false);
await view.close();
""")


def test_renderer_ignores_an_older_poll_response_after_an_action_has_completed(tmp_path):
    run_renderer(tmp_path, r"""
const before=snapshot();view.render(before);
firstValue='newer server state';revision++;view.render(snapshot());
view.render(before);
assert.equal(find('input-one').value,'newer server state');
find('button-one').emit('click');await flush();
assert.equal(JSON.parse(calls[0].options.body).revision,2);
await view.close();
""")
