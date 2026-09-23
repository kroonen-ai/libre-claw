// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { CordisRuntime } from '../runtime.mjs';

test('included orchestration activates without work and forwards only explicit assignments', async t => {
  const state = await fs.mkdtemp(path.join(os.tmpdir(), 'libre-orchestration-'));
  t.after(() => fs.rm(state, { recursive: true, force: true }));
  const entry = fileURLToPath(new URL('../examples/orchestration/plugin.mjs', import.meta.url));
  const calls = [];
  const runtime = new CordisRuntime({ hostCall: async (method, params) => {
    calls.push({ method, params }); return { state: 'ok', method };
  } });
  t.after(() => runtime.unmount());
  await runtime.initialize({ entry, plugin_id: 'orchestration', state_dir: state, config: {} });
  assert.deepEqual(calls, []);
  assert.deepEqual(runtime.listTools().tools.map(tool => tool.name), ['cancel', 'delegate', 'status', 'wait']);
  await assert.rejects(runtime.callTool({ name: 'status', arguments: {} }), /plugin tool failed/);
  assert.deepEqual(calls, []);
  const context = { session_id: 'selected-session', call_id: 'control-call' };
  const tasks = [{ worker: 'scout', task: 'Inspect this explicit assignment', scope: '.' }];
  for (const [name, args] of [
    ['delegate', { tasks }], ['wait', { ids: ['worker-1'], timeout: 1 }],
    ['status', {}], ['cancel', { ids: ['worker-1'] }],
  ]) {
    const result = await runtime.callTool({ name, arguments: args, context });
    assert.equal(result.error, false);
    assert.equal(JSON.parse(result.content).state, 'ok');
  }
  assert.deepEqual(calls, [
    { method: 'orchestration.dispatch', params: { tasks } },
    { method: 'orchestration.wait', params: { ids: ['worker-1'], timeout: 1 } },
    { method: 'orchestration.status', params: {} },
    { method: 'orchestration.cancel', params: { ids: ['worker-1'] } },
  ]);
});

test('orchestration SDK rejects callbacks after the owning tool has returned', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'libre-orchestration-late-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const entry = path.join(directory, 'plugin.mjs');
  await fs.writeFile(entry, `export const inject=['libre'];export function apply(ctx){
    ctx.libre.registerTool({name:'late',description:'Late callback',input_schema:{type:'object'}},()=>{
      setTimeout(async()=>{try{await ctx.libre.orchestration.status();}catch{await ctx.libre.storage.set('blocked',true);}},10);
      return 'returned';
    });
  }`);
  const calls = [];
  const runtime = new CordisRuntime({ hostCall: async (...args) => { calls.push(args); return {}; } });
  t.after(() => runtime.unmount());
  await runtime.initialize({ entry, plugin_id: 'late', state_dir: directory });
  await runtime.callTool({ name: 'late', arguments: {}, context: { session_id: 'old-session' } });
  let blocked = false;
  for (let count = 0; count < 100; count++) {
    try { blocked = JSON.parse(await fs.readFile(path.join(directory, 'blocked.json'), 'utf8')); break; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(blocked, true);
  assert.deepEqual(calls, []);
});
