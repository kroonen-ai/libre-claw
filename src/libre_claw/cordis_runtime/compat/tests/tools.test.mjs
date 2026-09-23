// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { Context } from '../../vendor/cordis.mjs';
import { z } from '../../vendor/zod.mjs';
import { defineTool, registerHarnessServices } from '../tools.mjs';

// Resolve the original production modules without editing any plugin source.
const aliases = new Map([
  ['@deepseek-ai/dsh-tools', new URL('../tools.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-user-questions', new URL('../tools.mjs', import.meta.url).href],
  ['@deepseek-ai/schemastery', new URL('../../vendor/schemastery.mjs', import.meta.url).href],
  ['zod', new URL('../../vendor/zod.mjs', import.meta.url).href],
]);
registerHooks({ resolve(specifier, context, nextResolve) {
  return aliases.has(specifier) ? { url: aliases.get(specifier), shortCircuit: true } : nextResolve(specifier, context);
} });
const askUser = await import('./fixtures/harness-ask-user.mjs');
const todo = await import('./fixtures/harness-todo.mjs');

async function harness(t, options = {}) {
  const root = new Context();
  const exposed = new Map();
  let context = options.context;
  const dispose = await registerHarnessServices(root, {
    registerTool(schema, handler) {
      exposed.set(schema.name, { schema, handler });
      return () => exposed.delete(schema.name);
    },
    executionContext: () => context,
    ...options,
  });
  t.after(dispose);
  return {
    root, exposed, dispose,
    setContext(value) { context = value; },
    async mount(plugin, config) {
      const fiber = root.plugin(plugin, config);
      await fiber.await();
      t.after(() => fiber.dispose());
      return fiber;
    },
    async call(name, args) { return exposed.get(name).handler(args); },
  };
}

const echo = () => defineTool({
  name: 'echo', description: 'Echo a value',
  parameters: { text: { type: 'string', required: true } },
  output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
  execute: async args => args.text,
});

test('recorded Harness schema and production plugin sources match pinned hashes', async () => {
  const record = JSON.parse(await readFile(new URL('../upstream.json', import.meta.url), 'utf8'));
  for (const file of record.files) {
    const bytes = await readFile(new URL(`../${file.file}`, import.meta.url));
    assert.equal(createHash('sha256').update(bytes).digest('hex'), file.sha256, file.file);
  }
});

test('real Cordis registration validates arguments and disposes native tools', async t => {
  const app = await harness(t);
  const fiber = await app.mount({ inject: ['tools'], apply(ctx) { ctx.tools.register(echo()); } });
  assert.equal(fiber.state, 2);
  assert.deepEqual(app.exposed.get('echo').schema.input_schema, {
    type: 'object', properties: { text: { type: 'string' } }, required: ['text'],
  });
  assert.deepEqual(await app.call('echo', { text: 'hello' }), { content: 'hello', error: false });
  assert.equal((await app.call('echo', { text: 4 })).error, true);
  assert.equal((await app.call('echo', {})).error, true);
  await fiber.dispose();
  assert.equal(app.exposed.size, 0);
  assert.deepEqual(app.dispose.inspect().tools, []);
});

test('canonical output is enforced before rendering and never coerces lossy JSON', async t => {
  const app = await harness(t);
  let rendered = 0;
  await app.mount({ inject: ['tools'], apply(ctx) {
    for (const [name, value] of [['wrong', 9], ['lossy', undefined], ['nonfinite', NaN]]) {
      const definition = echo();
      ctx.tools.register({ ...definition, name, execute: async () => value,
        output: { ...definition.output, render() { rendered++; return [{ type: 'text', text: 'unsafe' }]; } },
      });
    }
  } });
  for (const name of ['wrong', 'lossy', 'nonfinite']) {
    assert.equal((await app.call(name, { text: 'ok' })).error, true);
  }
  assert.equal(rendered, 0);
});

test('DSL enforces nested exact-one unions, required keys, enums, and closed objects', async t => {
  const app = await harness(t);
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register(defineTool({
      name: 'shape', description: 'Validate shape',
      parameters: { value: { required: true, oneOf: [
        { type: 'object', additionalProperties: false, properties: { kind: { type: 'string', enum: ['item'], required: true }, count: { type: 'integer', required: true } } },
        { type: 'null' },
      ] } },
      output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      execute: async args => args.value,
    }));
  } });
  assert.equal((await app.call('shape', { value: { kind: 'item', count: 2 } })).error, false);
  assert.equal((await app.call('shape', { value: null })).content, 'null');
  for (const value of [{ kind: 'other', count: 2 }, { kind: 'item', count: 2.5 }, { kind: 'item' }, { kind: 'item', count: 2, extra: true }]) {
    assert.equal((await app.call('shape', { value })).error, true);
  }
});

test('unchanged Harness ask-user package reaches the host question broker', async t => {
  const requests = [];
  const app = await harness(t, {
    context: { callId: 'call-1', agent: { id: 'agent-1', session: { id: 'session-1' } } },
    async hostCall(method, params, context) {
      requests.push({ method, params, session: context.sessionId });
      return { answers: [{ id: 'choice', selected: ['Proceed'], custom: 'Use local files' }] };
    },
  });
  await app.mount(askUser);
  const result = await app.call('ask_user_question', { questions: [{ id: 'choice', question: 'Continue?', multi_select: true }] });
  assert.deepEqual(requests, [{ method: 'userQuestions.ask', params: { questions: [{ id: 'choice', question: 'Continue?', multiSelect: true }] }, session: 'session-1' }]);
  assert.deepEqual(JSON.parse(result.content), { answers: [{ id: 'choice', selected: ['Proceed'], custom: 'Use local files' }] });
  assert.equal(result.error, false);
});

test('unchanged Harness todo package persists events and folds its real projection', async t => {
  const events = [];
  let state;
  const app = await harness(t, {
    context: { agent: { id: 'agent', session: { id: 'session' } } },
    async hostCall(method, params) { events.push({ method, params }); return { ok: true }; },
  });
  const plugin = await app.mount(todo, { allowParallelInProgress: false });
  await app.mount({ inject: ['sessionProjections'], apply(ctx) {
    ctx.on('tools/result', (exec) => { state = ctx.sessionProjections.stateOf(exec.agent.session, 'todos'); });
  } });
  const result = await app.call('todo_write', { todos: [{ content: '  Add integration  ', status: 'in_progress' }] });
  assert.deepEqual(result, { content: 'Updated todo list: 0 pending, 1 in progress, 0 completed.', error: false });
  assert.deepEqual(events, [{ method: 'session.append', params: { session_id: 'session', type: 'todo/write', data: { todos: [{ content: 'Add integration', status: 'in_progress' }] } } }]);
  assert.deepEqual(state, [{ content: 'Add integration', status: 'in_progress' }]);
  assert.deepEqual(app.dispose.inspect().projections, ['todos']);
  await plugin.dispose();
  assert.deepEqual(app.dispose.inspect().projections, []);
});

test('todo failure cannot report success when the durable host write fails', async t => {
  const app = await harness(t, {
    context: { agent: { id: 'agent', session: { id: 'session' } } },
    async hostCall() { throw new Error('private host token'); },
  });
  await app.mount(todo, { allowParallelInProgress: false });
  const result = await app.call('todo_write', { todos: [{ content: 'Save task', status: 'pending' }] });
  assert.equal(result.error, true);
  assert.equal(result.content.includes('private host token'), false);
});

test('projection state replays only the host-supplied session and stays isolated', async t => {
  const app = await harness(t, { hostCall: async () => ({}) });
  await app.mount({ inject: ['tools', 'sessionProjections'], apply(ctx) {
    ctx.sessionProjections.register({ key: 'count', stateSchema: z.number(), stateVersion: 1,
      init: () => 0, apply: (state, event) => event.type === 'increment' ? state + event.data.amount : state,
    });
    ctx.tools.register({ ...echo(), execute(_args, exec) { return String(ctx.sessionProjections.stateOf(exec.agent.session, 'count')); } });
  } });
  app.setContext({ agent: { id: 'a', session: { id: 'a', events: [{ type: 'increment', data: { amount: 3 } }] } } });
  assert.equal((await app.call('echo', { text: '' })).content, '3');
  app.setContext({ agent: { id: 'b', session: { id: 'b' } } });
  assert.equal((await app.call('echo', { text: '' })).content, '0');
});

test('missing privileged services keep real Cordis consumers pending', async t => {
  const app = await harness(t);
  for (const service of ['llm', 'fs', 'approval', 'agents', 'userQuestions', 'sessionProjections']) {
    const fiber = await app.mount({ inject: [service], apply() { assert.fail('unavailable service must not activate'); } });
    assert.equal(fiber.state, 0, service);
    assert.equal(app.root.get(service), undefined);
  }
});

test('hooks wrap execution, render once, and retain finalizer and metadata', async t => {
  const app = await harness(t);
  const order = [];
  let rendered = 0;
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.on('tools/pre-execute', (_exec, next) => { order.push('pre'); return next(); });
    ctx.on('tools/execute', async (_exec, next) => { order.push('around'); return next(); });
    ctx.on('tools/post-execute', (_exec, _result, next) => { order.push('post'); return next(); });
    ctx.on('tools/result', (_exec, result) => { order.push('result'); assert.equal(Object.isFrozen(result), true); });
    ctx.tools.register({ ...echo(),
      output: { schema: { type: 'string' }, render: (_args, value) => { rendered++; return [{ type: 'text', text: value }]; }, presentationMeta: () => ({ label: 'Echo' }) },
      finalizeContent: (_exec, result) => { order.push('finalize'); return [{ type: 'text', text: `${result.content[0].text}!` }]; },
    });
  } });
  assert.deepEqual(await app.call('echo', { text: 'yes' }), { content: 'yes!', error: false, meta: { label: 'Echo' } });
  assert.deepEqual(order, ['pre', 'around', 'post', 'finalize', 'result']);
  assert.equal(rendered, 1);
});

test('guards and ask decisions cannot manufacture approval', async t => {
  const app = await harness(t);
  let called = 0;
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register({ ...echo(), execute() { called++; return 'bad'; } });
    ctx.tools.guard(() => 'denied');
    ctx.on('tools/pre-execute', () => ({ kind: 'allow' }));
  } });
  assert.equal((await app.call('echo', { text: 'x' })).error, true);
  assert.equal(called, 0);
  const asked = await harness(t);
  await asked.mount({ inject: ['tools'], apply(ctx) { ctx.tools.register(echo()); ctx.on('tools/pre-execute', () => ({ kind: 'ask' })); } });
  assert.match((await asked.call('echo', { text: 'x' })).content, /approval service/);
});

test('cancellation reaches the body even when an around hook replaces its signal', async t => {
  const controller = new AbortController();
  const app = await harness(t, { context: { signal: controller.signal } });
  let entered;
  const body = new Promise(resolve => { entered = resolve; });
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.on('tools/execute', (exec, next) => { exec.signal = new AbortController().signal; return next(); });
    ctx.tools.register({ ...echo(), async execute(_args, exec) {
      entered();
      await new Promise(resolve => exec.signal.addEventListener('abort', resolve, { once: true }));
      return 'late success';
    } });
  } });
  const pending = app.call('echo', { text: 'x' });
  await body;
  controller.abort();
  const result = await pending;
  assert.equal(result.error, true);
  assert.match(result.content, /cancelled/);
});

test('deferred context and concludeTurn survive the tool result', async t => {
  const app = await harness(t);
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register({ ...echo(), async execute(_args, exec) {
      exec.deferContext({ role: 'user', content: [{ type: 'text', text: 'Plugin follow-up' }] });
      exec.concludeTurn();
      return 'done';
    } });
  } });
  const result = await app.call('echo', { text: 'x' });
  assert.equal(result.concludes_turn, true);
  assert.deepEqual(result.additional_contexts, [{ role: 'user', content: [{ type: 'text', text: 'Plugin follow-up' }] }]);
});

test('tool identity survives registration and nested calls keep their owning session', async t => {
  const events = [];
  const app = await harness(t, {
    context: { agent: { id: 'agent', session: { id: 'session' } } },
    hostCall: async (method, params) => { events.push({ method, params }); return {}; },
  });
  const child = { ...echo(), name: 'child', execute(_args, exec) {
    exec.agent.session.append('nested/write', { value: 1 });
    return 'nested';
  } };
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register(child);
    assert.equal(ctx.tools.get('child'), child);
    ctx.tools.register({ ...echo(), async execute(args, exec) {
      const result = await ctx.tools.execute({ name: 'child', arguments: args, agent: exec.agent, signal: exec.signal });
      assert.equal(exec.agent.session.events.length, 1);
      return result.value;
    } });
  } });
  assert.deepEqual(await app.call('echo', { text: 'x' }), { content: 'nested', error: false });
  assert.deepEqual(events, [{ method: 'session.append', params: { session_id: 'session', type: 'nested/write', data: { value: 1 } } }]);
});

test('an explicitly granted model service supports background providers without a tool caller', async t => {
  const calls = [];
  const app = await harness(t, {
    hostServices: ['llm'], hostData: { providers: [{ id: 'local', name: 'Local' }] },
    hostCall: async (method, params) => {
      calls.push({ method, params });
      if (method === 'llm.listModels') return [{ id: 'model' }];
      return [{ type: 'text-delta', index: 0, text: 'generated' }, { type: 'finish', reason: { kind: 'stop' } }];
    },
  });
  let answer;
  await app.mount({ inject: ['llm'], async apply(ctx) {
    assert.deepEqual(ctx.llm.listProviders(), [{ id: 'local', name: 'Local' }]);
    assert.deepEqual(await ctx.llm.listModels('local'), [{ id: 'model' }]);
    answer = [];
    for await (const chunk of ctx.llm.stream({ provider: 'local', model: 'model', messages: [] })) answer.push(chunk);
  } });
  assert.equal(answer[0].text, 'generated');
  assert.deepEqual(calls.map(call => call.method), ['llm.listModels', 'llm.stream']);
  assert.equal(app.root.get('userQuestions'), undefined);
  assert.equal(app.root.get('sessionProjections'), undefined);
});

test('restrictions remove tools from the host catalog and restore them on disposal', async t => {
  const app = await harness(t);
  await app.mount({ inject: ['tools'], apply(ctx) { ctx.tools.register(echo()); } });
  const staleHandler = app.exposed.get('echo').handler;
  const restriction = await app.mount({ inject: ['tools'], apply(ctx) { ctx.tools.restrict({ deny: ['echo'] }); } });
  assert.equal(app.exposed.has('echo'), false);
  assert.equal((await staleHandler({ text: 'denied' })).error, true);
  await restriction.dispose();
  assert.equal(app.exposed.has('echo'), true);
  assert.equal((await app.call('echo', { text: 'restored' })).content, 'restored');
});

test('bounded image content is retained exactly and never fetched or opened', async t => {
  const app = await harness(t);
  const content = [
    { type: 'text', text: 'Generated image' },
    { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'aW1hZ2U=' } },
    { type: 'image', attachment: { attachmentId: 'opaque-ref', mediaType: 'image/png', bytes: 5, width: 1, height: 1 } },
  ];
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register({ ...echo(), output: { schema: { type: 'string' }, render: () => content } });
  } });
  const result = await app.call('echo', { text: 'x' });
  assert.equal(result.content, 'Generated image');
  assert.equal(result.error, false);
  assert.deepEqual(result.content_blocks, content);
});

test('URL images, local file handles, malformed base64, and oversized blocks fail safely', async t => {
  const app = await harness(t);
  const images = [
    { type: 'image', source: { type: 'url', url: 'http://private.invalid/image.png' } },
    { type: 'image', attachment: { attachmentId: '/private/image.png', mediaType: 'image/png', bytes: 5, width: 1, height: 1 } },
    { type: 'image', source: { type: 'base64', media_type: 'image/png', data: '!invalid' } },
    { type: 'image', data: Buffer.alloc(600000).toString('base64'), mediaType: 'image/png' },
  ];
  await app.mount({ inject: ['tools'], apply(ctx) {
    images.forEach((image, index) => ctx.tools.register({ ...echo(), name: `image_${index}`, output: { schema: { type: 'string' }, render: () => [image] } }));
  } });
  for (let index = 0; index < images.length; index++) assert.equal((await app.call(`image_${index}`, { text: 'x' })).error, true);
});

test('session identity is stable across calls, isolated by ID, and bounded by LRU', async t => {
  const app = await harness(t);
  const seen = new WeakMap();
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register({ ...echo(), execute(_args, exec) {
      const count = (seen.get(exec.agent.session) ?? 0) + 1;
      seen.set(exec.agent.session, count);
      return String(count);
    } });
  } });
  const call = async id => {
    app.setContext({ agent: { id, session: { id } } });
    return (await app.call('echo', { text: '' })).content;
  };
  assert.equal(await call('a'), '1');
  assert.equal(await call('b'), '1');
  assert.equal(await call('a'), '2');
  for (let index = 0; index < 31; index++) await call(`session-${index}`);
  assert.equal(app.dispose.inspect().sessions, 32);
  assert.equal(await call('a'), '3');
  assert.equal(await call('b'), '1');
});

test('session cache never evicts an active call to fit another session', async t => {
  const app = await harness(t);
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  let running = 0;
  await app.mount({ inject: ['tools'], apply(ctx) {
    ctx.tools.register({ ...echo(), async execute() { running++; await wait; return 'done'; } });
  } });
  const calls = [];
  for (let index = 0; index < 32; index++) {
    app.setContext({ agent: { id: String(index), session: { id: String(index) } } });
    calls.push(app.call('echo', { text: '' }));
  }
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(running, 32);
  app.setContext({ agent: { id: 'overflow', session: { id: 'overflow' } } });
  assert.equal((await app.call('echo', { text: '' })).error, true);
  assert.equal(running, 32);
  release();
  assert.equal((await Promise.all(calls)).every(result => !result.error), true);
});
