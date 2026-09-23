// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { CordisRuntime } from '../../runtime.mjs';

async function runtimeFixture(t, source, files = {}, options = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'libre-host-services-'));
  await fs.mkdir(path.join(directory, 'state'));
  await fs.writeFile(path.join(directory, 'plugin.mjs'), source);
  for (const [name, data] of Object.entries(files)) await fs.writeFile(path.join(directory, name), data);
  const runtime = new CordisRuntime({ hostCall: options.hostCall ?? (async () => { throw new Error('Unexpected host call'); }) });
  t.after(async () => { await runtime.unmount(); await fs.rm(directory, { recursive: true, force: true }); });
  await runtime.initialize({ entry: path.join(directory, 'plugin.mjs'), plugin_id: 'test', state_dir: path.join(directory, 'state'),
    harness: { packages: [] }, host_services: options.hostServices ?? ['systemPrompt', 'agents', 'jobs', 'lsp'] });
  return runtime;
}

test('runtime prompt mutations belong only to their exact task scope', async t => {
  const runtime = await runtimeFixture(t, `
import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools','systemPrompt'];export function apply(ctx){
 ctx.systemPrompt.section({name:'global',order:0,text:'Shared plugin guidance'});
 ctx.tools.register(defineTool({name:'customize',description:'Own task guidance',parameters:{text:{type:'string',required:true}},
 output:{schema:{type:'string'},render:(_args,v)=>[{type:'text',text:v}]},execute:async args=>{
 ctx.systemPrompt.section({name:'private',order:1,text:args.text});return 'saved';}}));
}`);
  const context = id => ({ session_id: id, cwd: '/workspace' });
  assert.equal((await runtime.callTool({ name: 'customize', arguments: { text: 'task-a-private' }, context: context('a') })).error, false);
  const a = await runtime.dispatch('harness/prompt', { context: context('a') });
  const b = await runtime.dispatch('harness/prompt', { context: context('b') });
  assert.match(a.text, /task-a-private/);
  assert.equal(b.text, 'Shared plugin guidance');
  assert.equal((await runtime.callTool({ name: 'customize', arguments: { text: 'task-b-private' }, context: context('b') })).error, false);
  assert.doesNotMatch((await runtime.dispatch('harness/prompt', { context: context('a') })).text, /task-b-private/);
});

test('a service request signal cancels its host operation while its parent call remains active', async t => {
  let observedSignal;
  const runtime = await runtimeFixture(t, `
import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools','fs'];export function apply(ctx){
 ctx.tools.register(defineTool({name:'cancel_read',description:'Cancel only this read',parameters:{},
 output:{schema:{type:'boolean'},render:(_args,v)=>[{type:'text',text:String(v)}]},execute:async(_args,exec)=>{
  const request=new AbortController();
  const pending=ctx.fs.resolve('file.txt',{signal:request.signal});
  request.abort(new Error('Cancel just the read'));
  try { await pending; return false; } catch { return !exec.signal.aborted; }
 }}));
}`, {}, {
    hostServices: ['fs'],
    hostCall: async (method, _params, { signal }) => {
      assert.equal(method, 'harness.fs.resolve');
      observedSignal = signal;
      return await new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    },
  });
  const result = await runtime.callTool({ name: 'cancel_read', arguments: {}, context: { session_id: 's', cwd: '/workspace' } });
  assert.equal(observedSignal.aborted, true);
  // Host effects are joined even when the tool catches its own cancellation.
  assert.equal(result.error, true);
});

test('unchanged LSP tool calls a registered provider and preserves normalized locations', async t => {
  const lsp = await fs.readFile(new URL('./fixtures/harness-lsp.mjs', import.meta.url));
  const runtime = await runtimeFixture(t, `
export const inject=['libre','lsp'];export async function apply(ctx){
 ctx.lsp.registerProvider({id:'fixture',extensionToLanguage:{'.ts':'typescript'},async query(request){
  if(request.languageId!=='typescript'||request.position.line!==0)throw Error('Invalid request mapping');
  return {kind:'locations',resolvedWorkspaceUri:'file:///workspace',locations:[{uri:'file:///workspace/definition.ts',range:{start:{line:2,character:0},end:{line:2,character:5}}}]};
 }});
 await ctx.libre.mountHarnessComponents([{id:'lsp',module:'./lsp.mjs'}]);
}`, { 'lsp.mjs': lsp });
  const result = await runtime.callTool({ name: 'lsp', arguments: { operation: 'goToDefinition', file_path: 'source.ts', line: 1, character: 1 },
    context: { session_id: 's', cwd: '/workspace' } });
  assert.equal(result.error, false, result.content);
  assert.match(result.content, /definition.ts:3:1/);
  await runtime.unmount();
  assert.equal(runtime.listTools().tools.length, 0);
});
