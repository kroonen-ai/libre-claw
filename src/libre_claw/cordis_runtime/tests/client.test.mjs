// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClientRuntime } from '../client.mjs';

const nodes = snapshot => snapshot.views.flatMap(view => flatten(view.tree));
const flatten = tree => tree.flatMap(node => [node, ...flatten(node.children)]);
const text = snapshot => nodes(snapshot).filter(node => node.tag === '#text').map(node => node.text).join(' ');

async function source(body, callback, chunks = {}) {
  const root = await mkdtemp(path.join(tmpdir(), 'lc-client-'));
  const entry = path.join(root, 'client.mjs');
  await writeFile(entry, `window.__ModuleLoader__.load({id:'client-fixture',factory(require){const React=require('react');${body}}});`);
  await writeFile(path.join(root, 'package.json'), '{"type":"module"}');
  for (const [name, contents] of Object.entries(chunks)) await writeFile(path.join(root, name), contents);
  const runtime = new ClientRuntime();
  try { await callback(runtime, {plugin_id: 'fixture', package_name: 'client-fixture', entry, component_ids: ['fixture'], config: {},
    chunks: Object.fromEntries(Object.keys(chunks).map(name => [name, path.join(root, name)]))}); }
  finally { await runtime.dispose(); await rm(root, {recursive: true, force: true}); }
}

test('unchanged Harness client registers locale, overlay, two config views and cleanup', async () => {
  const runtime = new ClientRuntime();
  const entry = fileURLToPath(new URL('../compat/tests/fixtures/client/live-client.mjs', import.meta.url));
  const snapshot = await runtime.initialize({plugin_id: 'fixture', package_name: '@fixture/live-client', entry, component_ids: ['fixture-live-client'], config: {}});
  const document = runtime.document;
  assert.equal(snapshot.views.length, 3);
  assert.match(text(snapshot), /Live plugin enabled An example setting Greeting Save/);
  assert.equal(document.documentElement.dataset.liveMounts, '1');
  const form = nodes(snapshot).find(node => node.tag === 'form'), input = nodes(snapshot).find(node => node.tag === 'input');
  const changed = await runtime.event({revision: snapshot.revision, event_id: form.events.submit, target_id: form.id, values: {[input.id]: {value: 'own form input'}}});
  assert.equal(document.documentElement.dataset.liveSaves, '1');
  assert.equal(nodes(changed).find(node => node.id === input.id).props.value, 'own form input');
  await runtime.dispose();
  assert.equal(document.documentElement.dataset.liveDisposals, '1');
  assert.throws(() => runtime.snapshot(), /not active/);
});

test('real React hooks, context, async events and effect cleanup survive the guest boundary', async () => {
  await source(`
    const Value=React.createContext('missing');
    function Counter(){
      const [count,setCount]=React.useState(0);const value=React.useContext(Value);
      React.useEffect(()=>{document.documentElement.dataset.mounted='yes';return()=>{document.documentElement.dataset.cleaned='yes';};},[]);
      return React.createElement('button',{onClick:async()=>{await Promise.resolve();setCount(old=>old+1);}},value+' '+count);
    }
    return {inject:['slots'],apply(ctx){ctx.slots.inject('shell.overlay',()=>ctx.slots.register({name:'shell.overlay',id:'counter'},()=>React.createElement(Value.Provider,{value:'Context'},React.createElement(Counter))));}};
  `, async (runtime, params) => {
    const snapshot = await runtime.initialize(params), document = runtime.document;
    assert.equal(text(snapshot), 'Context 0'); assert.equal(document.documentElement.dataset.mounted, 'yes');
    const button = nodes(snapshot).find(node => node.tag === 'button');
    const updated = await runtime.event({revision: snapshot.revision, event_id: button.events.click, target_id: button.id, values: {}});
    assert.equal(text(updated), 'Context 1'); assert.equal(nodes(updated).find(node => node.tag === 'button').id, button.id);
    await assert.rejects(runtime.event({revision: snapshot.revision, event_id: button.events.click, values: {}}), /changed/);
    await runtime.dispose(); assert.equal(document.documentElement.dataset.cleaned, 'yes');
  });
});

test('controlled inputs receive only their current form values and foreign targets are rejected', async () => {
  await source(`
    function Form(){const [name,setName]=React.useState('start');return React.createElement('form',{onSubmit:e=>{e.preventDefault();document.documentElement.dataset.saved=name;}},React.createElement('input',{name:'name',value:name,onChange:e=>setName(e.target.value)}),React.createElement('span',null,name));}
    return {inject:['slots'],apply(ctx){for(const id of ['one','two'])ctx.slots.inject('shell.overlay',()=>ctx.slots.register({name:'shell.overlay',id},Form));}};
  `, async (runtime, params) => {
    let snapshot = await runtime.initialize(params);
    const inputs = nodes(snapshot).filter(node => node.tag === 'input');
    snapshot = await runtime.event({revision: snapshot.revision, event_id: inputs[0].events.change, target_id: inputs[0].id, values: {[inputs[0].id]: {value: 'updated'}}});
    assert.match(text(snapshot), /updated start/);
    const firstForm = nodes(snapshot).find(node => node.tag === 'form');
    await assert.rejects(runtime.event({revision: snapshot.revision, event_id: firstForm.events.submit, target_id: inputs[1].id, values: {}}), /target/);
    await assert.rejects(runtime.event({revision: snapshot.revision, event_id: firstForm.events.submit, values: {[inputs[1].id]: {value: 'foreign'}}}), /input values/);
    await runtime.event({revision: snapshot.revision, event_id: firstForm.events.submit, values: {}});
    assert.equal(runtime.document.documentElement.dataset.saved, 'updated');
  });
});

test('client-private DOM, unsupported modules, unsafe markup and URL-bearing styles fail closed', async t => {
  t.mock.method(console, 'error', () => {});
  for (const body of [
    `require('react-dom/client');return {apply(){}};`,
    `document.createElement('iframe');return {apply(){}};`,
    `return {inject:['slots'],apply(ctx){ctx.slots.register({name:'shell.overlay',id:'bad'},()=>React.createElement('script',null,'alert(1)'));}};`,
    `return {inject:['slots'],apply(ctx){ctx.slots.register({name:'shell.overlay',id:'bad'},()=>React.createElement('div',{dangerouslySetInnerHTML:{__html:'<img src=https://telemetry.invalid>'}}));}};`,
    `return {inject:['slots'],apply(ctx){ctx.slots.register({name:'shell.overlay',id:'bad'},()=>React.createElement('div',{style:{backgroundColor:'url(https://telemetry.invalid)'}}));}};`,
  ]) await source(body, async (runtime, params) => { await assert.rejects(runtime.initialize(params), /client|DOM|renderer/); });
});

test('configuration slots cannot impersonate another installed package', async () => {
  await source(`return {inject:['slots'],apply(ctx){ctx.slots.register({name:'plugins.row.config',key:'other-package#private'},()=>React.createElement('div',null,'wrong'));}};`,
    async (runtime, params) => { await assert.rejects(runtime.initialize(params), /belong/); });
});

test('actual Harness compiler output lazily materializes a shared chunk once', async () => {
  const runtime = new ClientRuntime();
  const entry = fileURLToPath(new URL('../compat/tests/fixtures/client/dynamic/client.js', import.meta.url));
  try {
    let snapshot = await runtime.initialize({plugin_id: 'fixture', package_name: '@deepseek-ai/dsh-client-ui-conversation', entry,
      component_ids: ['fixture'], chunks: {'client.panel.js': path.join(path.dirname(entry), 'client.panel.js')}});
    assert.equal(runtime.document.documentElement.dataset.chunkMounts, undefined);
    assert.match(text(snapshot), /Waiting for chunk/);
    for (let index = 0; index < 2; index++) {
      const button = nodes(snapshot).find(node => node.tag === 'button');
      snapshot = await runtime.event({revision: snapshot.revision, event_id: button.events.click});
      assert.match(text(snapshot), /Compiled lazy panel/);
      assert.equal(runtime.document.documentElement.dataset.chunkMounts, '1');
      assert.equal(runtime.document.documentElement.dataset.sameChunk, 'true');
    }
  } finally { await runtime.dispose(); }
});

test('async seeds retain identity and only verified local chunk requests resolve', async () => {
  await source(`window.fixtureRequire=require;return {apply(){}};`, async (runtime, params) => {
    await runtime.initialize(params);
    const require = window.fixtureRequire;
    for (const name of ['react', 'react/jsx-runtime', '@deepseek-ai/cordis']) assert.equal(await require.async(name), require(name));
    for (const name of ['../client.panel.js', './sub/client.panel.js', './client.%2e%2e.js', './client.panel.js?x', './client.js',
      './client.unknown.js', '/client.panel.js', 'https://foreign.invalid/client.panel.js', 'other-package/client.panel.js',
      'client-fixture/client.panel.js', './client.panel.js/../client.panel.js', './client.\\panel.js']) {
      await assert.rejects(require.async(name), /reviewed package-local/);
    }
    assert.throws(() => require('./client.panel.js'), /unavailable module/);
    await runtime.dispose();
    await assert.rejects(require.async('react'), /disposed/);
  });
});

test('chunk metadata cannot redirect a verified sibling to another location', async () => {
  for (const mapping of [entry => ({'client.panel.js': path.join(path.dirname(entry), 'other.js')}),
    entry => ({'client.panel.js': path.join(path.dirname(entry), '..', 'client.panel.js')}),
    () => ({'../client.panel.js': '/client.panel.js'})]) {
    await source(`return {apply(){}};`, async (runtime, params) => {
      await assert.rejects(runtime.initialize({...params, chunks: mapping(params.entry)}), /metadata/);
    });
  }
});

test('chunk registration rejects missing, duplicate and foreign owners or names', async () => {
  const good = `window.__ModuleLoader__.load({id:'client-fixture',chunk:'client.panel.js',factory(){return {marker:1};}});`;
  for (const chunk of ['', good + good, good.replace("id:'client-fixture'", "id:'other-package'"),
    good.replace("chunk:'client.panel.js'", "chunk:'client.other.js'"), good.replace("chunk:'client.panel.js',", '')]) {
    await source(`window.fixtureRequire=require;return {apply(){}};`, async (runtime, params) => {
      await runtime.initialize(params);
      const first = window.fixtureRequire.async('./client.panel.js');
      assert.equal(first, window.fixtureRequire.async('./client.panel.js'));
      await assert.rejects(first, /register|registration/);
      assert.equal(first, window.fixtureRequire.async('./client.panel.js'));
    }, {'client.panel.js': chunk});
  }
});

test('disposed in-flight chunks reject immediately and never materialize later', async () => {
  const chunk = `const owner=window;owner.started();await owner.gate;owner.__ModuleLoader__.load({id:'client-fixture',chunk:'client.panel.js',factory(){owner.document.documentElement.dataset.lateFactory='bad';return {};}});`;
  await source(`window.fixtureRequire=require;return {apply(){}};`, async (runtime, params) => {
    await runtime.initialize(params);
    const owner = window, document = runtime.document;
    let release;
    owner.gate = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { owner.started = resolve; });
    const pending = owner.fixtureRequire.async('./client.panel.js');
    const rejected = assert.rejects(pending, /cancelled/);
    await started;
    await runtime.dispose();
    await rejected;
    release();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(document.documentElement.dataset.lateFactory, undefined);
    assert.throws(() => owner.__ModuleLoader__.load({id:'client-fixture',chunk:'client.panel.js',factory(){}}), /registration/);
  }, {'client.panel.js': chunk});
});
