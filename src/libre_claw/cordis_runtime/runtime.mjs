// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// A private JSONL bridge. Process isolation and plugin trust are owned by Python.
import { Context, Service, getTraceable } from './vendor/cordis.mjs';
import { constants } from 'node:fs';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { formatWithOptions } from 'node:util';
import { AsyncLocalStorage } from 'node:async_hooks';
import { registerHarnessServices } from './compat/tools.mjs';
import { installHarnessResolver } from './compat/loader.mjs';
import { harnessConfigSchema } from './compat/config.mjs';

const MAX_FRAME_BYTES = 1024 * 1024;
const MAX_CONTENT_BYTES = 512 * 1024;
const MAX_VALUE_BYTES = 64 * 1024;
const MAX_STORAGE_BYTES = 1024 * 1024;
const MAX_TOOLS = 64;
// Cordis 4.0.2 publishes FiberState as a TypeScript const enum only.
const FIBER_STATES = ['PENDING', 'LOADING', 'ACTIVE', 'FAILED', 'DISPOSED', 'UNLOADING'];
const NAME = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const STORAGE_KEY = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

class PublicError extends Error {}

function pluginOf(module) {
  const plugin = module.default ?? module;
  if (typeof plugin !== 'function' && !(object(plugin) && typeof plugin.apply === 'function')) {
    throw new PublicError('The package does not export a Cordis plugin.');
  }
  return plugin;
}

function privatePaths(value, directory, depth = 0) {
  if (depth > 64) throw new PublicError('Harness configuration is too deeply nested.');
  if (Array.isArray(value)) return value.map(item => privatePaths(item, directory, depth + 1));
  if (!object(value)) return value;
  if (Object.hasOwn(value, '$libreStatePath')) {
    const relative = value.$libreStatePath;
    if (Object.keys(value).length !== 1 || typeof relative !== 'string' || !relative || path.isAbsolute(relative)) {
      throw new PublicError('Invalid private Harness storage path.');
    }
    const result = path.resolve(directory, relative);
    const within = path.relative(directory, result);
    if (within === '..' || within.startsWith(`..${path.sep}`) || path.isAbsolute(within)) {
      throw new PublicError('Harness storage paths must remain inside private plugin storage.');
    }
    return result;
  }
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, privatePaths(item, directory, depth + 1)]));
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function copyJson(value, maximum, message) {
  let encoded;
  try { encoded = JSON.stringify(value); } catch { throw new PublicError(message); }
  if (encoded === undefined || Buffer.byteLength(encoded) > maximum) throw new PublicError(message);
  return JSON.parse(encoded);
}

function deepFreeze(value) {
  if (object(value) || Array.isArray(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

class Storage {
  #directory;
  #pending = Promise.resolve();

  constructor(directory) { this.#directory = directory; }

  #file(key) {
    if (typeof key !== 'string' || !STORAGE_KEY.test(key)) {
      throw new PublicError('Storage keys must contain only letters, numbers, underscores, or hyphens.');
    }
    return path.join(this.#directory, `${key}.json`);
  }

  #serialize(operation) {
    const pending = this.#pending.then(operation);
    this.#pending = pending.catch(() => {});
    return pending;
  }

  async #read(file) {
    let handle;
    try {
      handle = await fs.open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
      const stat = await handle.stat();
      if (!stat.isFile() || stat.size > MAX_VALUE_BYTES) throw new Error('Invalid storage file');
      const text = await handle.readFile({ encoding: 'utf8' });
      if (Buffer.byteLength(text) > MAX_VALUE_BYTES) throw new Error('Oversized storage value');
      return JSON.parse(text);
    } catch (error) {
      if (error?.code === 'ENOENT') return null;
      throw new PublicError('Could not read plugin storage.');
    } finally {
      await handle?.close();
    }
  }

  get(key) {
    const file = this.#file(key);
    return this.#serialize(() => this.#read(file));
  }

  set(key, value) {
    const file = this.#file(key);
    const encoded = JSON.stringify(copyJson(value, MAX_VALUE_BYTES, 'Plugin storage values are limited to 64 KiB of JSON.'));
    return this.#serialize(async () => {
      let temporary;
      try {
        let total = Buffer.byteLength(encoded);
        for (const entry of await fs.readdir(this.#directory, { withFileTypes: true })) {
          if (entry.name === path.basename(file) || !entry.name.endsWith('.json')) continue;
          if (!entry.isFile()) throw new Error('Invalid storage entry');
          total += (await fs.stat(path.join(this.#directory, entry.name))).size;
          if (total > MAX_STORAGE_BYTES) throw new PublicError('Plugin storage is limited to 1 MiB.');
        }
        temporary = path.join(this.#directory, `.write-${randomUUID()}`);
        await fs.writeFile(temporary, encoded, { flag: 'wx', mode: 0o600 });
        await fs.rename(temporary, file);
        return true;
      } catch (error) {
        if (error instanceof PublicError) throw error;
        throw new PublicError('Could not write plugin storage.');
      } finally {
        if (temporary) await fs.unlink(temporary).catch(() => {});
      }
    });
  }

  delete(key) {
    const file = this.#file(key);
    return this.#serialize(async () => {
      try { await fs.unlink(file); return true; }
      catch (error) {
        if (error?.code === 'ENOENT') return false;
        throw new PublicError('Could not delete plugin storage.');
      }
    });
  }

  async drain() { await this.#pending; }
}

export class CordisRuntime {
  #root;
  #fiber;
  #libreFiber;
  #storage;
  #pluginId;
  #initialized = false;
  #tools = new Map();
  #harnessDispose;
  #resolver;
  #components = [];
  #calls = new AsyncLocalStorage();
  #hostCall;

  constructor({ hostCall } = {}) { this.#hostCall = hostCall; }

  inspect() {
    return {
      plugin_id: this.#pluginId ?? null,
      state: this.#fiber ? FIBER_STATES[this.#fiber.state] : 'DISPOSED',
      tools: [...this.#tools.keys()].sort(),
      runtime_version: '4.0.2',
      ...(this.#harnessDispose ? { harness: {
        ...this.#harnessDispose.inspect(),
        components: this.#components.map(({ id, fiber, ctx, disabled, configMetadata, error }) => ({
          id, state: disabled ? 'DISABLED' : fiber ? FIBER_STATES[fiber.state] : 'FAILED',
          missing_services: disabled || !fiber ? [] : Object.keys(fiber.inject).filter(name => ctx.get(name) === undefined),
          ...configMetadata,
          ...(error ? { error } : {}),
        })),
      } } : {}),
    };
  }

  async initialize(params) {
    if (this.#initialized) throw new PublicError('The plugin runtime is already initialized.');
    const { entry, plugin_id: pluginId, state_dir: stateDir, config = {} } = params;
    if (typeof entry !== 'string' || !path.isAbsolute(entry)
      || typeof stateDir !== 'string' || !path.isAbsolute(stateDir)
      || typeof pluginId !== 'string' || !NAME.test(pluginId) || !object(config)) {
      throw new PublicError('Invalid plugin initialization parameters.');
    }
    const safeConfig = copyJson(config, MAX_VALUE_BYTES, 'Plugin configuration is limited to 64 KiB of JSON.');
    try {
      const directory = await fs.lstat(stateDir);
      if (!directory.isDirectory() || directory.isSymbolicLink()) throw new Error('Invalid storage directory');
      const source = await fs.stat(entry);
      if (!source.isFile()) throw new Error('Invalid entry');
    } catch {
      throw new PublicError('The plugin entry and private storage directory must exist.');
    }
    this.#initialized = true;
    this.#pluginId = pluginId;
    this.#storage = new Storage(stateDir);
    this.#root = new Context();
    const tools = this.#tools;
    const storage = this.#storage;
    const runtime = this;
    if (params.harness !== undefined && !object(params.harness)) throw new PublicError('Invalid Harness package metadata.');
    const isHarness = params.harness !== undefined;
    if (isHarness) this.#resolver = installHarnessResolver(entry, params.harness.packages ?? []);

    const mountComponents = async (ctx, components, overrides, parent = '') => {
      if (!Array.isArray(components) || components.length > 128 || !object(overrides)) throw new PublicError('Invalid Harness component configuration.');
      for (const component of components) {
        if (!object(component) || typeof component.id !== 'string' || component.id.length > 160) throw new PublicError('Invalid Harness component.');
        const id = parent ? `${parent}/${component.id}` : component.id;
        if (runtime.#components.some(item => item.id === id)) throw new PublicError('A Harness component id is duplicated.');
        const override = overrides[id] ?? overrides[component.id] ?? {};
        if (!object(override)) throw new PublicError('Invalid Harness component settings.');
        const enabled = override.enabled ?? component.enabled ?? true;
        if (typeof enabled !== 'boolean') throw new PublicError('Harness component enabled must be boolean.');
        if (!enabled) { runtime.#components.push({ id, disabled: true }); continue; }
        let plugin;
        if (component.group === true) {
          plugin = { name: id, async apply(groupCtx) {
            await mountComponents(groupCtx, component.children ?? [], overrides, id);
          } };
        } else {
          if (typeof component.module !== 'string') throw new PublicError('Harness component requires a module.');
          let specifier;
          if (component.module.startsWith('.')) {
            const target = path.resolve(path.dirname(entry), component.module);
            const relative = path.relative(path.dirname(entry), target);
            if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
              throw new PublicError('Harness component paths must stay inside the installed snapshot.');
            }
            specifier = pathToFileURL(target).href;
          } else specifier = runtime.#resolver.component(component.module);
          plugin = pluginOf(await import(specifier));
        }
        const configuration = privatePaths(override.config ?? component.config ?? {}, stateDir);
        const record = { id, ctx, disabled: false, configMetadata: harnessConfigSchema(plugin.Config) };
        runtime.#components.push(record);
        try {
          record.fiber = ctx.plugin(plugin, configuration);
          await record.fiber.await();
        } catch {
          record.error = 'Component configuration or activation failed. Review its settings and required services.';
        }
      }
    };

    class LibreService extends Service {
      constructor(ctx) {
        super(ctx, 'libre');
        this.storage = Object.freeze({
          get: key => storage.get(key),
          set: (key, value) => storage.set(key, value),
          delete: key => storage.delete(key),
        });
        this.cordis = Object.freeze({ Context, Service });
        const orchestration = async (method, parameters) => {
          const call = runtime.#calls.getStore();
          if (!call || call.closed || !call.agent || typeof runtime.#hostCall !== 'function') {
            throw new PublicError('Orchestration requires an active task with an explicitly selected profile.');
          }
          call.signal.throwIfAborted();
          const result = await runtime.#hostCall(`orchestration.${method}`,
            copyJson(parameters, MAX_VALUE_BYTES, 'Orchestration arguments exceed their size limit.'));
          call.signal.throwIfAborted();
          if (call.closed) throw new PublicError('The orchestration task is no longer active.');
          return copyJson(result, 256 * 1024, 'The orchestration report exceeds its size limit.');
        };
        this.orchestration = Object.freeze({
          dispatch: tasks => orchestration('dispatch', { tasks }),
          wait: (ids, timeout = 0) => orchestration('wait', { ...(ids === undefined ? {} : { ids }), timeout }),
          status: () => orchestration('status', {}),
          cancel: ids => orchestration('cancel', ids === undefined ? {} : { ids }),
        });
      }

      registerTool(definition, handler) {
        if (!object(definition) || typeof handler !== 'function'
          || typeof definition.name !== 'string' || !NAME.test(definition.name)
          || typeof definition.description !== 'string' || definition.description.length > 4096
          || !object(definition.input_schema) || definition.input_schema.type !== 'object') {
          throw new PublicError('Invalid plugin tool definition.');
        }
        const schema = deepFreeze(copyJson({
          name: definition.name,
          description: definition.description,
          input_schema: definition.input_schema,
        }, MAX_VALUE_BYTES, 'Plugin tool definitions are limited to 64 KiB of JSON.'));
        if (tools.has(schema.name)) throw new PublicError('A plugin tool name is already registered.');
        if (tools.size >= MAX_TOOLS) throw new PublicError('A plugin may register at most 64 tools.');
        return this.ctx.effect(() => {
          const registration = { schema, handler };
          tools.set(schema.name, registration);
          return () => { if (tools.get(schema.name) === registration) tools.delete(schema.name); };
        });
      }

      mountHarnessComponents(components, overrides = {}) {
        if (!isHarness) throw new PublicError('This plugin is not an installed Harness package.');
        // Service methods carry an origin shadow for their own dependencies.
        // Child plugins must instead inherit the calling plugin's context.
        return mountComponents(getTraceable(this.ctx, this.ctx), components, overrides);
      }
    }

    try {
      this.#libreFiber = await this.#root.plugin(LibreService);
      if (isHarness) {
        this.#harnessDispose = await registerHarnessServices(this.#root, {
          registerTool: (definition, handler) => this.#root.libre.registerTool(definition, handler),
          hostCall: this.#hostCall,
          hostServices: params.host_services ?? [],
          hostData: params.host_data ?? {},
          executionContext: () => this.#calls.getStore(),
        });
      }
      const module = await import(pathToFileURL(entry).href);
      const plugin = pluginOf(module);
      // Retain the handle before awaiting so startup failures can unwind effects.
      this.#fiber = this.#root.plugin(plugin, safeConfig);
      await this.#fiber.await();
      await this.#settle();
      return this.inspect();
    } catch {
      await this.unmount();
      throw new PublicError('The Cordis plugin failed to initialize.');
    }
  }

  async #settle() {
    // A service may activate siblings after its own apply completes. Join every
    // fiber to make tool discovery and lifecycle inspection deterministic.
    for (const runtime of this.#root.registry.values()) {
      for (const fiber of [...runtime.fibers]) {
        try { await fiber.await(); }
        catch {
          // A Harness Config failure is inspectable so the user can edit the
          // actual required fields. Native plugin failures still fail startup.
          const component = this.#components.find(item => item.fiber?.uid === fiber.uid);
          if (!component) throw new PublicError('The Cordis plugin failed to initialize.');
          component.error = 'Component configuration or activation failed. Review its settings and required services.';
        }
      }
    }
  }

  listTools() {
    return { tools: [...this.#tools.values()].map(({ schema }) => schema).sort((a, b) => a.name.localeCompare(b.name)) };
  }

  async callTool(params) {
    const tool = this.#tools.get(params.name);
    if (!tool) throw new PublicError('The requested plugin tool is not available.');
    if (!object(params.arguments)) throw new PublicError('Plugin tool arguments must be a JSON object.');
    const args = deepFreeze(copyJson(params.arguments, MAX_CONTENT_BYTES, 'Plugin tool arguments are too large.'));
    let result;
    try {
      const context = params.context ?? {};
      if (!object(context)) throw new PublicError('Invalid plugin execution context.');
      const supplied = deepFreeze(copyJson(context, MAX_CONTENT_BYTES, 'Plugin execution context is too large.'));
      const call = {
        callId: supplied.callId ?? supplied.call_id ?? randomUUID(),
        signal: new AbortController().signal,
        closed: false,
        ...(supplied.agent ? { agent: supplied.agent } : supplied.session_id ? { agent: {
          id: supplied.agent_id ?? supplied.session_id,
          session: { id: supplied.session_id,
            ...(supplied.cwd ? { cwd: supplied.cwd } : {}),
            ...(supplied.session_events ? { events: supplied.session_events } : {}),
          },
        } } : {}),
      };
      try { result = await this.#calls.run(call, () => tool.handler(args)); }
      finally { call.closed = true; }
    }
    catch { throw new PublicError('The plugin tool failed.'); }
    if (typeof result === 'string') result = { content: result, error: false };
    if (!object(result) || typeof result.content !== 'string'
      || (result.error !== undefined && typeof result.error !== 'boolean')) {
      throw new PublicError('The plugin tool returned an invalid result.');
    }
    if (Buffer.byteLength(result.content) > MAX_CONTENT_BYTES) {
      throw new PublicError('Plugin tool results are limited to 512 KiB.');
    }
    return {
      content: result.content, error: result.error ?? false,
      ...(result.content_blocks === undefined ? {} : { content_blocks: copyJson(result.content_blocks, MAX_CONTENT_BYTES, 'Plugin content blocks are too large.') }),
      ...(result.meta === undefined ? {} : { meta: copyJson(result.meta, MAX_CONTENT_BYTES, 'Plugin presentation metadata is too large.') }),
      ...(result.additional_contexts === undefined ? {} : { additional_contexts: copyJson(result.additional_contexts, MAX_CONTENT_BYTES, 'Plugin deferred context is too large.') }),
      ...(result.concludes_turn === true ? { concludes_turn: true } : {}),
    };
  }

  async unmount() {
    try { await this.#fiber?.dispose(); }
    finally {
      try { await this.#harnessDispose?.(); await this.#libreFiber?.dispose(); }
      finally {
        this.#resolver?.deregister();
        this.#resolver = undefined;
        this.#tools.clear();
        await this.#storage?.drain();
      }
    }
    return { unmounted: true };
  }

  async dispatch(method, params) {
    switch (method) {
      case 'initialize': return this.initialize(params);
      case 'inspect': return this.inspect();
      case 'tools/list': return this.listTools();
      case 'tools/call': return this.callTool(params);
      case 'harness/prompt': {
        if (!this.#harnessDispose?.prompt) throw new PublicError('This plugin does not provide Harness prompts.');
        const supplied = deepFreeze(copyJson(params.context ?? {}, MAX_CONTENT_BYTES, 'Plugin execution context is too large.'));
        return this.#harnessDispose.prompt({ callId: randomUUID(), signal: new AbortController().signal,
          ...(supplied.session_id ? { agent: { id: supplied.agent_id ?? supplied.session_id,
            session: { id: supplied.session_id, cwd: supplied.cwd, events: supplied.session_events } } } : {}) });
      }
      case 'unmount': return this.unmount();
      case 'shutdown': await this.unmount(); return { shutdown: true };
      default: throw new PublicError('Unknown plugin runtime method.');
    }
  }
}

function redirectConsole() {
  for (const level of ['log', 'info', 'warn', 'error', 'debug', 'dir', 'trace']) {
    console[level] = (...values) => {
      let message;
      try { message = formatWithOptions({ depth: 2, maxArrayLength: 20, maxStringLength: 1000 }, ...values); }
      catch { message = '[plugin log unavailable]'; }
      process.stderr.write(`${message.slice(0, 4096)}\n`);
    };
  }
}

export async function serve(input = process.stdin, output = process.stdout) {
  redirectConsole();
  let closed = false;
  let hostSequence = 0;
  const hostRequests = new Map();
  const send = async response => {
    let frame = JSON.stringify(response);
    if (Buffer.byteLength(frame) > MAX_FRAME_BYTES) {
      frame = JSON.stringify({
        ...(response.host_call_id ? { host_call_id: response.host_call_id } : { id: response.id }),
        error: { message: 'Plugin runtime response is too large.' },
      });
    }
    await new Promise((resolve, reject) => output.write(`${frame}\n`, error => error ? reject(error) : resolve()));
  };
  const hostCall = (method, params, options = {}) => {
    if (closed) return Promise.reject(new PublicError('The plugin host connection is closed.'));
    if (hostRequests.size >= 64) return Promise.reject(new PublicError('Too many pending plugin host requests.'));
    if (options.signal?.aborted) return Promise.reject(options.signal.reason);
    const id = ++hostSequence;
    let abort;
    const promise = new Promise((resolve, reject) => {
      const settle = callback => value => { options.signal?.removeEventListener('abort', abort); hostRequests.delete(id); callback(value); };
      hostRequests.set(id, { resolve: settle(resolve), reject: settle(reject) });
      abort = () => {
        hostRequests.get(id)?.reject(options.signal.reason);
        void send({ host_cancel_id: id }).catch(() => {});
      };
      options.signal?.addEventListener('abort', abort, { once: true });
    });
    void send({ host_call_id: id, method, params }).catch(() => {
      hostRequests.get(id)?.reject(new PublicError('Could not contact the plugin host.'));
      hostRequests.delete(id);
    });
    return promise;
  };
  hostCall.stream = async function* (method, params, options = {}) {
    if (closed || hostRequests.size >= 64) throw new PublicError('The plugin host is unavailable.');
    const id = ++hostSequence;
    const queue = [];
    let wake;
    let done = false;
    let failure;
    let received = 0;
    const notify = () => { wake?.(); wake = undefined; };
    const abort = () => { failure = options.signal.reason; done = true; notify(); };
    options.signal?.throwIfAborted();
    options.signal?.addEventListener('abort', abort, { once: true });
    hostRequests.set(id, {
      chunk(value) {
        received += Buffer.byteLength(JSON.stringify(value));
        if (received > 8 * MAX_FRAME_BYTES || queue.length >= 256) {
          failure = new PublicError('The plugin host stream exceeded its limit.');
          done = true;
        } else queue.push(value);
        notify();
      },
      resolve(value) {
        // A bounded array reply is accepted for older, non-streaming brokers.
        if (Array.isArray(value)) for (const chunk of value) this.chunk(chunk);
        done = true; notify();
      },
      reject(error) { failure = error; done = true; notify(); },
    });
    try {
      await send({ host_call_id: id, method, params, stream: true });
      while (!done || queue.length) {
        if (failure) throw failure;
        if (queue.length) yield queue.shift();
        else await new Promise(resolve => { wake = resolve; });
      }
      if (failure) throw failure;
    } finally {
      options.signal?.removeEventListener('abort', abort);
      hostRequests.delete(id);
      if (!done || options.signal?.aborted) await send({ host_cancel_id: id }).catch(() => {});
    }
  };
  const runtime = new CordisRuntime({ hostCall });
  let pending = Buffer.alloc(0);
  let dispatches = Promise.resolve();
  let queued = 0;
  let finish;
  const shutdown = new Promise(resolve => { finish = resolve; });
  const enqueue = request => {
    queued++;
    dispatches = dispatches.then(async () => {
      let id = null;
      try {
        if (!object(request)
          || !(Number.isSafeInteger(request.id) || (typeof request.id === 'string' && request.id.length <= 128))) {
          throw new PublicError('Invalid plugin runtime request.');
        }
        id = request.id;
        if (typeof request.method !== 'string' || (request.params !== undefined && !object(request.params))) {
          throw new PublicError('Invalid plugin runtime request.');
        }
        const result = await runtime.dispatch(request.method, request.params ?? {});
        await send({ id, result });
        if (request.method === 'shutdown') finish();
      } catch (error) {
        await send({ id, error: { message: error instanceof PublicError ? error.message : 'Plugin runtime request failed.' } });
      } finally { queued--; }
    });
  };
  const reader = (async () => {
    for await (const chunk of input) {
      pending = Buffer.concat([pending, Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)]);
      let newline;
      while ((newline = pending.indexOf(10)) !== -1) {
        const line = pending.subarray(0, newline);
        pending = pending.subarray(newline + 1);
        if (line.length > MAX_FRAME_BYTES) {
          await send({ id: null, error: { message: 'Plugin runtime request is too large.' } });
          return;
        }
        let request;
        try { request = JSON.parse(line.toString('utf8')); }
        catch { enqueue(null); continue; }
        if (object(request) && Object.hasOwn(request, 'host_call_id')) {
          const callback = hostRequests.get(request.host_call_id);
          if (!callback) continue;
          if (Object.hasOwn(request, 'chunk') && callback.chunk) callback.chunk(request.chunk);
          else {
            hostRequests.delete(request.host_call_id);
            if (request.error) callback.reject(new PublicError('The plugin host rejected this operation.'));
            else if (Object.hasOwn(request, 'result')) callback.resolve(request.result);
            else callback.reject(new PublicError('Invalid plugin host reply.'));
          }
          continue;
        }
        if (queued >= 256) {
          await send({ id: null, error: { message: 'Too many queued plugin requests.' } });
          return;
        }
        enqueue(request);
      }
      if (pending.length > MAX_FRAME_BYTES) {
        await send({ id: null, error: { message: 'Plugin runtime request is too large.' } });
        return;
      }
    }
    if (pending.length) await send({ id: null, error: { message: 'Plugin runtime requests must end with a newline.' } });
  })();
  try {
    await Promise.race([reader, shutdown]);
  } finally {
    closed = true;
    for (const callback of hostRequests.values()) callback.reject(new PublicError('The plugin host connection closed.'));
    hostRequests.clear();
    await dispatches;
    await runtime.unmount();
    // A shutdown RPC must finish even if the parent keeps its input pipe open.
    input.destroy();
    await reader.catch(() => {});
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  serve().then(() => process.exit(0), () => process.exit(1));
}
