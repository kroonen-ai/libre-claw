// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { CordisRuntime } from '../runtime.mjs';

const runtimeEntry = fileURLToPath(new URL('../runtime.mjs', import.meta.url));

async function fixture(t, source) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'libre-cordis-test-'));
  const entry = path.join(directory, 'plugin.mjs');
  const stateDir = path.join(directory, 'state');
  await fs.mkdir(stateDir, { mode: 0o700 });
  await fs.writeFile(entry, source);
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  return { entry, plugin_id: 'example', config: {}, state_dir: stateDir };
}

test('real Cordis tracks dependencies and disposes plugin-owned tools', async t => {
  const init = await fixture(t, `
export default {
  inject: ['libre'],
  async apply(ctx) {
    const { Service } = ctx.libre.cordis;
    class Greeting extends Service {
      constructor(ctx) { super(ctx, 'greeting'); }
      text() { return 'hello'; }
    }
    const consumer = ctx.plugin({ inject:['greeting','libre'], apply(ctx) {
      ctx.libre.registerTool({name:'greet',description:'Greeting',input_schema:{type:'object'}}, () => ctx.greeting.text());
    }});
    await consumer.await();
    const pending = consumer.state;
    let provider = await ctx.plugin(Greeting);
    await consumer.await();
    ctx.libre.registerTool({name:'control',description:'Lifecycle',input_schema:{type:'object'}}, async args => {
      if (args.drop) await provider.dispose();
      if (args.restore) provider = await ctx.plugin(Greeting);
      await consumer.await();
      return JSON.stringify({pending,state:consumer.state});
    });
    ctx.effect(() => async () => { await ctx.libre.storage.set('cleanup', true); });
  }
};`);
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  assert.equal((await runtime.initialize(init)).state, 'ACTIVE');
  assert.deepEqual(runtime.inspect().tools, ['control', 'greet']);
  assert.deepEqual(await runtime.callTool({ name: 'greet', arguments: {} }), { content: 'hello', error: false });
  const dropped = await runtime.callTool({ name: 'control', arguments: { drop: true } });
  assert.deepEqual(JSON.parse(dropped.content), { pending: 0, state: 0 });
  assert.deepEqual(runtime.inspect().tools, ['control']);
  const restored = await runtime.callTool({ name: 'control', arguments: { restore: true } });
  assert.equal(JSON.parse(restored.content).state, 2);
  assert.deepEqual(runtime.inspect().tools, ['control', 'greet']);
  await runtime.unmount();
  assert.equal(runtime.inspect().state, 'DISPOSED');
  assert.deepEqual(runtime.listTools(), { tools: [] });
  assert.equal(JSON.parse(await fs.readFile(path.join(init.state_dir, 'cleanup.json'), 'utf8')), true);
});

test('missing service dependencies remain pending and expose no tools', async t => {
  const init = await fixture(t, `export default {inject:['missing'],apply(){throw new Error('must not run')}};`);
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  const status = await runtime.initialize(init);
  assert.equal(status.state, 'PENDING');
  assert.deepEqual(status.tools, []);
});

test('tool schemas are copied, scoped, and independent of config inspection', async t => {
  const init = await fixture(t, `export default {inject:['libre'],apply(ctx,config) {
    const schema={type:'object',properties:{text:{type:'string'}}};
    ctx.libre.registerTool({name:'echo',description:'Echo',input_schema:schema}, () => ({content:config.secret,error:false}));
    schema.properties.text.type='number';
  }};`);
  init.config = { secret: 'not-for-inspection' };
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.equal(runtime.listTools().tools[0].input_schema.properties.text.type, 'string');
  assert.equal(JSON.stringify(runtime.inspect()).includes('not-for-inspection'), false);
  assert.equal(JSON.stringify(runtime.listTools()).includes('not-for-inspection'), false);
  assert.deepEqual(Object.keys(runtime.listTools().tools[0]).sort(), ['description', 'input_schema', 'name']);
});

test('private storage persists across runtimes and rejects traversal or oversized values', async t => {
  const init = await fixture(t, `export default {inject:['libre'],apply(ctx) {
    ctx.libre.registerTool({name:'store',description:'Store JSON',input_schema:{type:'object'}}, async args => {
      if (args.action==='set') return String(await ctx.libre.storage.set(args.key,args.value));
      if (args.action==='delete') return String(await ctx.libre.storage.delete(args.key));
      return JSON.stringify(await ctx.libre.storage.get(args.key));
    });
  }};`);
  const first = new CordisRuntime();
  await first.initialize(init);
  await first.callTool({ name: 'store', arguments: { action: 'set', key: 'saved', value: { note: 'local' } } });
  await assert.rejects(first.callTool({ name: 'store', arguments: { action: 'set', key: '../outside', value: 'no' } }), /plugin tool failed/);
  await assert.rejects(first.callTool({ name: 'store', arguments: { action: 'set', key: 'large', value: 'x'.repeat(65536) } }), /plugin tool failed/);
  await first.unmount();
  const second = new CordisRuntime();
  t.after(() => second.unmount());
  await second.initialize(init);
  assert.equal((await second.callTool({ name: 'store', arguments: { key: 'saved' } })).content, '{"note":"local"}');
  assert.equal((await second.callTool({ name: 'store', arguments: { action: 'delete', key: 'saved' } })).content, 'true');
  assert.equal((await second.callTool({ name: 'store', arguments: { key: 'saved' } })).content, 'null');
  assert.deepEqual(await fs.readdir(init.state_dir), []);
});

test('startup failures unwind tool effects and conceal exception content', async t => {
  const init = await fixture(t, `export default {inject:['libre'],apply(ctx) {
    ctx.libre.registerTool({name:'partial',description:'Partial',input_schema:{type:'object'}}, ()=>'partial');
    throw new Error('secret API-key-content');
  }};`);
  const runtime = new CordisRuntime();
  await assert.rejects(runtime.initialize(init), { message: 'The Cordis plugin failed to initialize.' });
  assert.deepEqual(runtime.listTools(), { tools: [] });
});

async function rpc(input) {
  const child = spawn(process.execPath, [runtimeEntry], { stdio: ['pipe', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.setEncoding('utf8').on('data', text => { stdout += text; });
  child.stderr.setEncoding('utf8').on('data', text => { stderr += text; });
  const done = new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('close', code => resolve({ code, stdout, stderr }));
  });
  child.stdin.on('error', () => {});
  child.stdin.end(input);
  return done;
}

test('JSONL RPC has bounded safe errors and sends plugin console output to stderr', async t => {
  const init = await fixture(t, `console.log('plugin log');
export default {inject:['libre'],apply(ctx) {
  ctx.libre.registerTool({name:'fail',description:'Throws',input_schema:{type:'object'}}, () => {throw new Error('private thrown data')});
  ctx.libre.registerTool({name:'echo',description:'Echo',input_schema:{type:'object'}}, args => args.text);
}};`);
  const requests = [
    { id: 1, method: 'initialize', params: init },
    { id: 2, method: 'tools/list' },
    { id: 3, method: 'tools/call', params: { name: 'fail', arguments: {} } },
    { id: 4, method: 'tools/call', params: { name: 'echo', arguments: { text: 'works' } } },
    { id: 5, method: 'unknown' },
    { id: 6, method: 'unmount' },
    { id: 7, method: 'inspect' },
    { id: 8, method: 'shutdown' },
  ];
  const result = await rpc(requests.map(value => JSON.stringify(value) + '\n').join(''));
  assert.equal(result.code, 0);
  assert.match(result.stderr, /plugin log/);
  assert.equal(result.stdout.includes('private thrown data'), false);
  const frames = result.stdout.trim().split('\n').map(JSON.parse);
  assert.equal(frames.length, 8);
  assert.equal(frames[0].result.runtime_version, '4.0.2');
  assert.equal(frames[1].result.tools.length, 2);
  assert.deepEqual(frames[2], { id: 3, error: { message: 'The plugin tool failed.' } });
  assert.deepEqual(frames[3].result, { content: 'works', error: false });
  assert.equal(frames[4].error.message, 'Unknown plugin runtime method.');
  assert.deepEqual(frames[6].result.tools, []);
  assert.equal(frames[6].result.state, 'DISPOSED');
  assert.deepEqual(frames[7].result, { shutdown: true });
});

test('malformed and oversized RPC input does not echo payloads', async () => {
  const malformed = await rpc('not-json\n{"id":2,"method":"shutdown"}\n');
  assert.equal(malformed.stdout.includes('not-json'), false);
  assert.equal(malformed.stdout.trim().split('\n').length, 2);
  const oversized = await rpc('x'.repeat(1024 * 1024 + 1));
  assert.equal(oversized.stdout.trim(), '{"id":null,"error":{"message":"Plugin runtime request is too large."}}');
});

test('oversized tool output is rejected and missing tools never execute', async t => {
  const init = await fixture(t, `export default {inject:['libre'],apply(ctx) {
    ctx.libre.registerTool({name:'echo',description:'Echo',input_schema:{type:'object'}}, args => args.large ? 'x'.repeat(512*1024+1) : args.text);
  }};`);
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  await runtime.initialize(init);
  await assert.rejects(runtime.callTool({ name: 'missing', arguments: {} }), /not available/);
  await assert.rejects(runtime.callTool({ name: 'echo', arguments: [] }), /JSON object/);
  await assert.rejects(runtime.callTool({ name: 'echo', arguments: { large: true } }), /512 KiB/);
  await runtime.unmount();
  await assert.rejects(runtime.callTool({ name: 'echo', arguments: { text: 'no' } }), /not available/);
});

test('storage quota includes all values and refuses symbolic link reads', async t => {
  const init = await fixture(t, `export default {inject:['libre'],apply(ctx) {
    ctx.libre.registerTool({name:'store',description:'Storage',input_schema:{type:'object'}}, async args => {
      if(args.write) return String(await ctx.libre.storage.set(args.key,'x'.repeat(65530)));
      return JSON.stringify(await ctx.libre.storage.get(args.key));
    });
  }};`);
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  await runtime.initialize(init);
  for (let n = 0; n < 16; n++) await runtime.callTool({ name: 'store', arguments: { key: `value${n}`, write: true } });
  await assert.rejects(runtime.callTool({ name: 'store', arguments: { key: 'overflow', write: true } }), /plugin tool failed/);
  assert.equal((await fs.readdir(init.state_dir)).length, 16);
  const outside = path.join(path.dirname(init.state_dir), 'outside.json');
  await fs.writeFile(outside, '"private content"');
  await fs.symlink(outside, path.join(init.state_dir, 'linked.json'));
  await assert.rejects(runtime.callTool({ name: 'store', arguments: { key: 'linked' } }), /plugin tool failed/);
});

test('hyphenated plugin IDs and tool names are supported', async t => {
  const init = await fixture(t, `export default {inject:['libre'],apply(ctx) {
    ctx.libre.registerTool({name:'local-tool',description:'A local tool',input_schema:{type:'object'}}, ()=>'okay');
  }};`);
  init.plugin_id = 'local-plugin';
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  assert.equal((await runtime.initialize(init)).plugin_id, 'local-plugin');
  assert.equal((await runtime.callTool({ name: 'local-tool', arguments: {} })).content, 'okay');
});
