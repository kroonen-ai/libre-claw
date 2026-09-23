// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { CordisRuntime } from '../../runtime.mjs';

async function fixture(t, components, sources = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'libre-harness-runtime-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const state = path.join(directory, 'state');
  await fs.mkdir(state);
  const entry = path.join(directory, 'plugin.mjs');
  await fs.writeFile(entry, `export const inject=['libre']; export async function apply(ctx,config) {
    await ctx.libre.mountHarnessComponents(${JSON.stringify(components)}, config.components ?? {});
  }`);
  for (const [name, source] of Object.entries(sources)) {
    await fs.mkdir(path.dirname(path.join(directory, name)), { recursive: true });
    await fs.writeFile(path.join(directory, name), source);
  }
  return { directory, entry, plugin_id: 'harness_test', state_dir: state, harness: { packages: [] } };
}

const echo = `import {defineTool} from '@deepseek-ai/dsh-tools';
export const inject=['tools']; export function apply(ctx) {
 ctx.tools.register(defineTool({name:'echo',description:'Echo text',parameters:{text:{type:'string',required:true}},
 output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},execute:async args=>args.text}));
}`;

test('Harness components mount through production runtime and resolve real SDK imports', async t => {
  const init = await fixture(t, [{ id: 'echo', module: './echo.mjs' }], { 'echo.mjs': echo });
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  const status = await runtime.initialize(init);
  assert.deepEqual(status.harness.components, [{ id: 'echo', state: 'ACTIVE', missing_services: [] }]);
  assert.deepEqual(runtime.listTools().tools.map(tool => tool.name), ['echo']);
  assert.deepEqual(await runtime.callTool({ name: 'echo', arguments: { text: 'unchanged API' } }), { content: 'unchanged API', error: false });
  await runtime.unmount();
  assert.equal(runtime.listTools().tools.length, 0);
});

test('group components share real scoped services and dispose their children', async t => {
  const init = await fixture(t, [{ id: 'group', group: true, children: [
    { id: 'consumer', module: './consumer.mjs' }, { id: 'provider', module: './provider.mjs' },
  ] }], {
    'provider.mjs': `import {Service} from '@deepseek-ai/cordis';export default class Greeting extends Service { constructor(ctx){super(ctx,'greeting');this.text='group service';} }`,
    'consumer.mjs': `export const inject=['greeting','libre'];export function apply(ctx){ctx.libre.registerTool({name:'greet',description:'Greeting',input_schema:{type:'object'}},()=>ctx.greeting.text);}`,
  });
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  const status = await runtime.initialize(init);
  assert.equal(status.harness.components.every(item => item.state === 'ACTIVE'), true);
  assert.equal((await runtime.callTool({ name: 'greet', arguments: {} })).content, 'group service');
  await runtime.unmount();
  assert.equal(runtime.inspect().tools.length, 0);
});

test('missing services are reported and disabled components never evaluate their modules', async t => {
  const init = await fixture(t, [
    { id: 'requires_fs', module: './requires.mjs' },
    { id: 'disabled', module: './disabled.mjs', enabled: false },
  ], {
    'requires.mjs': `export const inject=['fs'];export function apply(){throw new Error('must stay pending');}`,
    'disabled.mjs': `throw new Error('disabled module must never be imported');`,
  });
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  const result = await runtime.initialize(init);
  assert.deepEqual(result.harness.components, [
    { id: 'requires_fs', state: 'PENDING', missing_services: ['fs'] },
    { id: 'disabled', state: 'DISABLED', missing_services: [] },
  ]);
});

test('component config uses real Schemastery validation and private state paths', async t => {
  const init = await fixture(t, [{ id: 'configured', module: './configured.mjs', config: { directory: { $libreStatePath: 'documents' } } }], {
    'configured.mjs': `import z from '@deepseek-ai/schemastery';export const inject=['libre'];export const Config=z.object({directory:z.string().required(),label:z.string().default('default label')});
export function apply(ctx,config){ctx.libre.registerTool({name:'config',description:'Config',input_schema:{type:'object'}},()=>JSON.stringify(config));}`,
  });
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.deepEqual(JSON.parse((await runtime.callTool({ name: 'config', arguments: {} })).content), {
    directory: path.join(init.state_dir, 'documents'), label: 'default label',
  });
  const rejected = new CordisRuntime();
  t.after(() => rejected.unmount());
  await assert.rejects(rejected.initialize({ ...init, config: { components: { configured: { config: { directory: { $libreStatePath: '../escape' } } } } } }), /failed to initialize/);
});

test('reviewed package graph resolves bare imports without ambient node_modules', async t => {
  const init = await fixture(t, [{ id: 'package', module: 'tool-package' }], {
    'package.json': JSON.stringify({ name: 'top-package', type: 'module' }),
    'dependencies/tool/package.json': JSON.stringify({ name: 'tool-package', type: 'module', exports: { '.': './index.js' } }),
    'dependencies/tool/index.js': echo.replace("args.text", "args.text + ' package'"),
  });
  init.harness.packages = [
    { name: 'top-package', path: '.', dependencies: { 'tool-package': 'dependencies/tool' } },
    { name: 'tool-package', path: 'dependencies/tool', dependencies: {} },
  ];
  const runtime = new CordisRuntime();
  t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.equal((await runtime.callTool({ name: 'echo', arguments: { text: 'real' } })).content, 'real package');
});

test('package conditions preserve declaration order and distinguish import from require', async t => {
  const init = await fixture(t, [{ id: 'esm', module: './esm.mjs' }, { id: 'cjs', module: './common.cjs' }], {
    'package.json': JSON.stringify({ name: 'root', type: 'module' }),
    'esm.mjs': `import {value} from 'choice';export const inject=['libre'];export function apply(ctx){ctx.libre.registerTool({name:'esm',description:'ESM',input_schema:{type:'object'}},()=>value);}`,
    'common.cjs': `const value=require('choice');module.exports={inject:['libre'],apply(ctx){ctx.libre.registerTool({name:'cjs',description:'CJS',input_schema:{type:'object'}},()=>value);}};`,
    'choice/package.json': JSON.stringify({ name: 'choice', exports: { import: './import.mjs', require: './require.cjs', node: './node.cjs' } }),
    'choice/import.mjs': `export const value='import';`,
    'choice/require.cjs': `module.exports='require';`,
    'choice/node.cjs': `module.exports='wrong-node-branch';`,
  });
  init.harness.packages = [{ name: 'root', path: '.', dependencies: { choice: 'choice' } }, { name: 'choice', path: 'choice', dependencies: {} }];
  const runtime = new CordisRuntime(); t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.equal((await runtime.callTool({ name: 'esm', arguments: {} })).content, 'import');
  assert.equal((await runtime.callTool({ name: 'cjs', arguments: {} })).content, 'require');
});

test('package main takes precedence over bundler-only module metadata', async t => {
  const init = await fixture(t, [{ id: 'legacy', module: 'legacy' }], {
    'package.json': JSON.stringify({ name: 'root', type: 'module' }),
    'legacy/package.json': JSON.stringify({ name: 'legacy', main: './main.cjs', module: './browser.mjs' }),
    'legacy/main.cjs': `module.exports={inject:['libre'],apply(ctx){ctx.libre.registerTool({name:'legacy',description:'Main',input_schema:{type:'object'}},()=> 'main');}};`,
    'legacy/browser.mjs': `throw new Error('Bundler-only entry must not execute in Node.');`,
  });
  init.harness.packages = [{ name: 'root', path: '.', dependencies: { legacy: 'legacy' } }, { name: 'legacy', path: 'legacy', dependencies: {} }];
  const runtime = new CordisRuntime(); t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.equal((await runtime.callTool({ name: 'legacy', arguments: {} })).content, 'main');
});

test('the most specific export pattern wins and a blocked exact path stays blocked', async t => {
  const init = await fixture(t, [{ id: 'pattern', module: './entry.mjs' }], {
    'package.json': JSON.stringify({ name: 'root', type: 'module' }),
    'entry.mjs': `import {value} from 'choice/features/detail/item';export const inject=['libre'];export function apply(ctx){ctx.libre.registerTool({name:'pattern',description:'Pattern',input_schema:{type:'object'}},()=>value);}`,
    'blocked.mjs': `import 'choice/features/detail/blocked';export function apply(){}`,
    'choice/package.json': JSON.stringify({ name: 'choice', type: 'module', exports: {
      './features/*': './broad/*.mjs', './features/detail/*': './specific/*.mjs', './features/detail/blocked': null,
    } }),
    'choice/broad/detail/item.mjs': `export const value='wrong-broad';`,
    'choice/specific/item.mjs': `export const value='specific';`,
    'choice/specific/blocked.mjs': `throw new Error('Must not execute a blocked export.');`,
  });
  init.harness.packages = [{ name: 'root', path: '.', dependencies: { choice: 'choice' } }, { name: 'choice', path: 'choice', dependencies: {} }];
  const runtime = new CordisRuntime(); t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.equal((await runtime.callTool({ name: 'pattern', arguments: {} })).content, 'specific');
  const blocked = new CordisRuntime(); t.after(() => blocked.unmount());
  await assert.rejects(blocked.initialize({ ...init, entry: path.join(init.directory, 'blocked.mjs') }), /failed to initialize/);
});

test('relative component filenames retain literal URL punctuation', async t => {
  const filename = 'component#fragment?query%value.mjs';
  const init = await fixture(t, [{ id: 'literal', module: `./${filename}` }], { [filename]: echo });
  const runtime = new CordisRuntime(); t.after(() => runtime.unmount());
  await runtime.initialize(init);
  assert.equal((await runtime.callTool({ name: 'echo', arguments: { text: 'literal filename' } })).content, 'literal filename');
});

test('failed required Harness config remains inspectable with its real field schema', async t => {
  const init = await fixture(t, [{ id: 'todo', module: './todo.mjs', config: {} }], {
    'todo.mjs': await fs.readFile(new URL('./fixtures/harness-todo.mjs', import.meta.url), 'utf8'),
  });
  init.host_services = ['sessionProjections'];
  const runtime = new CordisRuntime({ hostCall: async () => ({}) });
  t.after(() => runtime.unmount());
  const result = await runtime.initialize(init);
  const component = result.harness.components[0];
  assert.equal(component.state, 'FAILED');
  assert.deepEqual(component.config_schema.properties, { allowParallelInProgress: { type: 'boolean' } });
  assert.deepEqual(component.config_schema.required, ['allowParallelInProgress']);
  assert.match(component.error, /settings/);
  assert.deepEqual(runtime.listTools().tools, []);
});

async function rpcProcess(t, answer) {
  const entry = fileURLToPath(new URL('../../runtime.mjs', import.meta.url));
  const child = spawn(process.execPath, [entry], { stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(() => child.kill());
  const replies = new Map();
  let sequence = 0;
  let received = '';
  child.stdout.setEncoding('utf8');
  const send = frame => child.stdin.write(`${JSON.stringify(frame)}\n`);
  child.stdout.on('data', chunk => {
    received += chunk;
    let offset;
    while ((offset = received.indexOf('\n')) !== -1) {
      const frame = JSON.parse(received.slice(0, offset)); received = received.slice(offset + 1);
      if (frame.host_call_id) {
        Promise.resolve(answer(frame, send)).catch(() => send({ host_call_id: frame.host_call_id, error: { message: 'denied' } }));
      } else {
        const pending = replies.get(frame.id);
        replies.delete(frame.id);
        if (frame.error) pending?.reject(new Error(frame.error.message));
        else pending?.resolve(frame.result);
      }
    }
  });
  const exited = new Promise(resolve => child.on('close', code => {
    for (const pending of replies.values()) pending.reject(new Error('RPC process closed'));
    resolve(code);
  }));
  return {
    request(method, params = {}) {
      const id = ++sequence;
      const response = new Promise((resolve, reject) => replies.set(id, { resolve, reject }));
      send({ id, method, params });
      return response;
    },
    exited,
  };
}

test('duplex JSONL executes unchanged ask-user and todo modules through host responses', { timeout: 10000 }, async t => {
  const init = await fixture(t, [
    { id: 'ask', module: './ask.mjs' },
    { id: 'todo', module: './todo.mjs', config: { allowParallelInProgress: true } },
  ], {
    'ask.mjs': await fs.readFile(new URL('./fixtures/harness-ask-user.mjs', import.meta.url), 'utf8'),
    'todo.mjs': await fs.readFile(new URL('./fixtures/harness-todo.mjs', import.meta.url), 'utf8'),
  });
  init.host_services = ['userQuestions', 'sessionProjections'];
  const calls = [];
  const rpc = await rpcProcess(t, (frame, send) => {
    calls.push(frame);
    send({ host_call_id: frame.host_call_id, result: frame.method === 'userQuestions.ask'
      ? { answers: [{ id: 'q', selected: ['local'] }] } : { ok: true } });
  });
  await rpc.request('initialize', init);
  const context = { session_id: 'session', call_id: 'tool-call' };
  const question = await rpc.request('tools/call', { name: 'ask_user_question', arguments: { questions: [{ id: 'q', question: 'Where?' }] }, context });
  assert.equal(question.error, false);
  assert.deepEqual(JSON.parse(question.content), { answers: [{ id: 'q', selected: ['local'] }] });
  const task = await rpc.request('tools/call', { name: 'todo_write', arguments: { todos: [{ content: 'Test broker', status: 'completed' }] }, context });
  assert.equal(task.error, false);
  assert.deepEqual(calls.map(call => call.method), ['userQuestions.ask', 'session.append']);
  await rpc.request('shutdown');
  assert.equal(await rpc.exited, 0);
});

test('model plugin receives incremental broker stream without provider credentials', { timeout: 10000 }, async t => {
  const init = await fixture(t, [{ id: 'model', module: './model.mjs' }], {
    'model.mjs': `import {defineTool} from '@deepseek-ai/dsh-tools';import {BlockAssembler,createUserMessage} from '@deepseek-ai/dsh-llm';
export const inject=['llm','tools'];export function apply(ctx){ctx.tools.register(defineTool({name:'model',description:'Brokered model',parameters:{},output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},async execute(){const assembler=new BlockAssembler();for await(const chunk of ctx.llm.stream({provider:'test',model:'local',messages:[createUserMessage({source:{kind:'plugin',plugin:'test'},content:[{type:'text',text:'hello'}]})]})){assembler.push(chunk);}return assembler.blocks().map(block=>block.text).join('');}}));}`,
  });
  init.host_services = ['llm'];
  const rpc = await rpcProcess(t, (frame, send) => {
    assert.equal(frame.method, 'llm.stream');
    assert.equal(frame.stream, true);
    assert.equal(frame.params.messages[0].content[0].text, 'hello');
    for (const chunk of [
      { type: 'text-delta', index: 0, text: 'broker ' },
      { type: 'text-delta', index: 0, text: 'result' },
      { type: 'finish', reason: { kind: 'stop' } },
    ]) send({ host_call_id: frame.host_call_id, chunk });
    send({ host_call_id: frame.host_call_id, result: null });
  });
  await rpc.request('initialize', init);
  assert.deepEqual(await rpc.request('tools/call', { name: 'model', arguments: {} }), { content: 'broker result', error: false });
  await rpc.request('shutdown');
  assert.equal(await rpc.exited, 0);
});
