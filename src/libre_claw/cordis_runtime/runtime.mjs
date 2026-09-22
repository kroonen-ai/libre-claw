// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// A private JSONL bridge. Process isolation and plugin trust are owned by Python.
import { Context, Service } from './vendor/cordis.mjs';
import { constants } from 'node:fs';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { formatWithOptions } from 'node:util';

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

  inspect() {
    return {
      plugin_id: this.#pluginId ?? null,
      state: this.#fiber ? FIBER_STATES[this.#fiber.state] : 'DISPOSED',
      tools: [...this.#tools.keys()].sort(),
      runtime_version: '4.0.2',
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

    class LibreService extends Service {
      constructor(ctx) {
        super(ctx, 'libre');
        this.storage = Object.freeze({
          get: key => storage.get(key),
          set: (key, value) => storage.set(key, value),
          delete: key => storage.delete(key),
        });
        this.cordis = Object.freeze({ Context, Service });
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
    }

    try {
      this.#libreFiber = await this.#root.plugin(LibreService);
      const module = await import(pathToFileURL(entry).href);
      const plugin = module.default ?? module;
      if (typeof plugin !== 'function' && !(object(plugin) && typeof plugin.apply === 'function')) {
        throw new Error('Invalid Cordis plugin');
      }
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
      for (const fiber of [...runtime.fibers]) await fiber.await();
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
    try { result = await tool.handler(args); }
    catch { throw new PublicError('The plugin tool failed.'); }
    if (typeof result === 'string') result = { content: result, error: false };
    if (!object(result) || typeof result.content !== 'string'
      || (result.error !== undefined && typeof result.error !== 'boolean')) {
      throw new PublicError('The plugin tool returned an invalid result.');
    }
    if (Buffer.byteLength(result.content) > MAX_CONTENT_BYTES) {
      throw new PublicError('Plugin tool results are limited to 512 KiB.');
    }
    return { content: result.content, error: result.error ?? false };
  }

  async unmount() {
    try { await this.#fiber?.dispose(); }
    finally {
      try { await this.#libreFiber?.dispose(); }
      finally {
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
  const runtime = new CordisRuntime();
  const send = async response => {
    let frame = JSON.stringify(response);
    if (Buffer.byteLength(frame) > MAX_FRAME_BYTES) {
      frame = JSON.stringify({ id: response.id, error: { message: 'Plugin runtime response is too large.' } });
    }
    await new Promise((resolve, reject) => output.write(`${frame}\n`, error => error ? reject(error) : resolve()));
  };
  let pending = Buffer.alloc(0);
  try {
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
        let id = null;
        try {
          request = JSON.parse(line.toString('utf8'));
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
          if (request.method === 'shutdown') return;
        } catch (error) {
          await send({ id, error: { message: error instanceof PublicError ? error.message : 'Plugin runtime request failed.' } });
        }
      }
      if (pending.length > MAX_FRAME_BYTES) {
        await send({ id: null, error: { message: 'Plugin runtime request is too large.' } });
        return;
      }
    }
    if (pending.length) await send({ id: null, error: { message: 'Plugin runtime requests must end with a newline.' } });
  } finally {
    await runtime.unmount();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  serve().then(() => process.exit(0), () => process.exit(1));
}
