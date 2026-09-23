// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// First-party service graph with explicitly approved core-policy extensions.
import { Context, Service } from './vendor/cordis.mjs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const MAX_FRAME = 64 * 1024;
const MAX_OPERATIONS = 128;
const STATES = ['PENDING', 'LOADING', 'ACTIVE', 'FAILED', 'DISPOSED', 'UNLOADING'];
const CATALOG = Object.freeze([
  { id: 'providers', title: 'Providers', dependencies: [], methods: ['complete', 'stream', 'models'] },
  { id: 'tools', title: 'Tools', dependencies: [], methods: ['execute', 'list'] },
  { id: 'sessions', title: 'Sessions', dependencies: [], methods: ['create', 'get', 'load', 'save', 'checkpoint', 'append', 'delete', 'list', 'history', 'export'] },
  { id: 'memory', title: 'Memory', dependencies: ['sessions', 'providers'], methods: ['initialize', 'load', 'retrieve', 'remember', 'search', 'extract', 'delete'] },
  { id: 'agent', title: 'Agent', dependencies: ['providers', 'tools', 'sessions'], methods: ['run'] },
  { id: 'workflows', title: 'Workflows', dependencies: ['agent'], methods: ['run', 'plan', 'review', 'create', 'get', 'list', 'configure', 'delete', 'save'] },
]);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const identifier = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(value);

class EngineError extends Error {}

class CoreEngine {
  #root = new Context();
  #fibers = new Map();
  #enabled = Object.fromEntries(CATALOG.map(component => [component.id, false]));
  #operations = new Map();
  #counts = new Map(CATALOG.map(component => [component.id, { completed: 0, failed: 0, cancelled: 0 }]));
  #initialized = false;
  #closed = false;
  #send;
  #catalog = CATALOG.map(item => ({...item}));
  #extensions = [];
  #handlers = new Map();
  #plugins = [];

  constructor(send) { this.#send = send; }

  inspect() {
    return {
      engine: 'cordis', runtime_version: '4.0.2', state: this.#closed ? 'closed' : 'running',
      privacy: { network: false, inherited_environment: false, payloads: 'opaque-handles', extensions: 'separate-processes' },
      active_operations: this.#operations.size,
      plugins: this.#plugins.map(({plugin_id, digest}) => ({id: plugin_id, digest})),
      components: this.#catalog.map(component => ({
        ...component, enabled: this.#enabled[component.id],
        state: this.#fibers.has(component.id) ? STATES[this.#fibers.get(component.id).state] : 'DISABLED',
        active_operations: [...this.#operations.values()].filter(operation => operation.service === component.id).length,
        ...this.#counts.get(component.id),
        implementations: Object.fromEntries(component.methods.map(method => [method,
          this.#handlers.get(`${component.id}:${method}`)?.owner ?? 'libre-claw'])),
      })),
    };
  }

  async initialize(components, plugins = []) {
    if (this.#initialized || this.#closed || !Array.isArray(plugins) || plugins.length > 16) throw new EngineError('Invalid core plugin initialization.');
    const definitions = new Map(CATALOG.map(item => [item.id, {...item, dependencies: [...item.dependencies], methods: [...item.methods]}]));
    const claims = new Set();
    for (const spec of plugins) {
      if (!object(spec) || !identifier(spec.plugin_id) || typeof spec.root !== 'string'
          || !path.isAbsolute(spec.root) || typeof spec.entry !== 'string' || path.isAbsolute(spec.entry)
          || spec.entry.split(/[\\/]/).some(part => !part || part.startsWith('.'))
          || !Array.isArray(spec.services) || !spec.services.length || spec.services.length > 16) throw new EngineError('Invalid core plugin declaration.');
      for (const row of spec.services) {
        if (!object(row) || !identifier(row.id) || typeof row.title !== 'string'
            || !Array.isArray(row.methods) || !row.methods.length || row.methods.length > 32
            || !row.methods.every(identifier) || new Set(row.methods).size !== row.methods.length
            || !Array.isArray(row.dependencies) || row.dependencies.length > 16
            || !row.dependencies.every(identifier)) throw new EngineError('Invalid core service declaration.');
        for (const method of row.methods) {
          const key = `${row.id}:${method}`;
          if (claims.has(key)) throw new EngineError('Two core plugins claim the same service method.');
          claims.add(key);
        }
        const previous = definitions.get(row.id);
        definitions.set(row.id, {...row,
          methods: [...new Set([...(previous?.methods ?? []), ...row.methods])],
          dependencies: [...new Set([...(previous?.dependencies ?? []), ...row.dependencies])],
        });
      }
    }
    const ordered = [], visiting = new Set(), visited = new Set();
    const visit = id => {
      if (visited.has(id)) return;
      if (visiting.has(id)) throw new EngineError('Cyclic core service dependency.');
      const row = definitions.get(id);
      if (!row) throw new EngineError('Missing core service dependency.');
      visiting.add(id); row.dependencies.forEach(visit); visiting.delete(id);
      visited.add(id); ordered.push(row);
    };
    definitions.forEach(row => visit(row.id));
    this.#catalog = ordered;
    this.#enabled = Object.fromEntries(ordered.map(row => [row.id, false]));
    this.#counts = new Map(ordered.map(row => [row.id, {completed: 0, failed: 0, cancelled: 0}]));
    const graph = this;
    for (const spec of plugins) {
      // A separate service scope binds registration to this package, including
      // delayed callbacks. Another package's initialization cannot lend it rights.
      const scope = this.#root.isolate('libreEngine');
      class EngineRegistration extends Service {
        constructor(ctx) { super(ctx, 'libreEngine'); }
        register(service, method, handler) {
          const key = `${service}:${method}`;
          if (typeof handler !== 'function'
              || !spec.services.some(row => row.id === service && row.methods.includes(method))
              || graph.#handlers.has(key)) throw new EngineError('Undeclared or duplicate core service registration.');
          const entry = {owner: spec.plugin_id, handler};
          return this.ctx.effect(() => {
            graph.#handlers.set(key, entry);
            return () => { if (graph.#handlers.get(key) === entry) graph.#handlers.delete(key); };
          });
        }
      }
      const registry = scope.plugin(EngineRegistration);
      this.#extensions.push(registry); await registry.await();
      const imported = await import(pathToFileURL(path.join(spec.root, spec.entry)).href);
      const plugin = imported.default ?? imported;
      const fiber = scope.plugin(plugin, spec.config ?? {});
      this.#extensions.push(fiber); await fiber.await();
      if (fiber.state !== 2 || spec.services.some(row => row.methods.some(method =>
        this.#handlers.get(`${row.id}:${method}`)?.owner !== spec.plugin_id))) throw new EngineError('Core plugin did not register its declared service methods.');
    }
    this.#plugins = plugins;
    return this.configure(components, true);
  }

  async configure(changes, initialize = false) {
    if (this.#closed) throw new EngineError('The Cordis engine is closed.');
    if (!object(changes)) throw new EngineError('Components must be an object.');
    if (initialize === this.#initialized) throw new EngineError('Invalid engine initialization state.');
    if (this.#operations.size) throw new EngineError('Wait for active engine operations before changing components.');
    const enabled = initialize ? Object.fromEntries(this.#catalog.map(component => [component.id, true])) : { ...this.#enabled };
    for (const [name, value] of Object.entries(changes)) {
      if (!Object.hasOwn(enabled, name) || typeof value !== 'boolean') throw new EngineError('Unknown component or invalid enabled flag.');
      enabled[name] = value;
    }
    for (const component of this.#catalog) {
      if (enabled[component.id] && component.dependencies.some(name => !enabled[name])) {
        throw new EngineError(`${component.title} requires ${component.dependencies.join(', ')}.`);
      }
    }
    // Reverse order disposes dependants before their dependencies. Cordis owns
    // actual service registration and automatically removes it on disposal.
    for (const component of [...this.#catalog].reverse()) {
      if (!enabled[component.id] && this.#fibers.has(component.id)) {
        await this.#fibers.get(component.id).dispose();
        this.#fibers.delete(component.id);
      }
    }
    for (const component of this.#catalog) {
      if (!enabled[component.id] || this.#fibers.has(component.id)) continue;
      const dispatch = operation => this.#delegate(operation);
      class CoreService extends Service {
        static inject = component.dependencies;
        constructor(ctx) {
          super(ctx, component.id);
          this.ctx.effect(() => () => this.cancelPending());
        }
        async invoke(operation) {
          if (!component.methods.includes(operation.method)) throw new EngineError('Unknown component method.');
          const registration = thisEngine.#handlers.get(`${component.id}:${operation.method}`);
          if (!registration) return dispatch(operation);
          let sent = false, active = true, timeout;
          const next = () => {
            if (!active || sent) throw new EngineError('A core plugin can dispatch only its current authorized operation once.');
            sent = true;
          };
          try {
            await Promise.race([
              Promise.resolve().then(() => registration.handler(Object.freeze({service: operation.service, method: operation.method, mode: operation.mode}), next)),
              new Promise((_, reject) => { timeout = setTimeout(() => reject(new EngineError('Core plugin dispatch timed out.')), 5000); }),
            ]);
            if (!sent) throw new EngineError('Core plugin returned without dispatching the operation.');
            return await dispatch(operation);
          } catch {
            throw new EngineError(`Core extension ${registration.owner} rejected ${operation.service}.${operation.method}.`);
          } finally { active = false; clearTimeout(timeout); }
        }
        cancelPending = () => thisEngine.cancelService(component.id);
      }
      const thisEngine = this;
      const fiber = this.#root.plugin(CoreService);
      this.#fibers.set(component.id, fiber);
      await fiber.await();
      if (fiber.state !== 2) throw new EngineError(`${component.title} did not activate.`);
    }
    this.#enabled = enabled;
    this.#initialized = true;
    return this.inspect();
  }

  async #delegate(operation) {
    this.#operations.set(operation.id, operation);
    await this.#send({ type: 'host.call', id: operation.id, service: operation.service, method: operation.method });
  }

  async invoke(request) {
    if (!this.#initialized || this.#closed) throw new EngineError('The Cordis engine is unavailable.');
    if (this.#operations.size >= MAX_OPERATIONS) throw new EngineError('Too many active engine operations.');
    if (this.#operations.has(request.id)) throw new EngineError('Duplicate engine operation.');
    const component = this.#catalog.find(item => item.id === request.service);
    if (!component || !this.#enabled[component.id]) throw new EngineError('The requested engine component is disabled.');
    if (request.mode !== 'call' && request.mode !== 'stream') throw new EngineError('Invalid engine operation mode.');
    try {
      await this.#root[component.id].invoke({
        id: request.id, service: component.id, method: request.method, mode: request.mode, sequence: 0, waiting: false,
      });
    } catch (error) {
      if (!this.#operations.has(request.id)) this.#counts.get(component.id).failed++;
      throw error;
    }
  }

  async receive(request) {
    const operation = this.#operations.get(request.id);
    // A cancelled operation can have an in-flight host reply. It has no power
    // to create a new operation or call a different service.
    if (!operation) return;
    if (request.type === 'host.item') {
      if (operation.mode !== 'stream' || operation.waiting || request.sequence !== operation.sequence) throw new EngineError('Invalid stream sequence.');
      operation.waiting = true;
      await this.#send({ type: 'item', id: operation.id, sequence: operation.sequence });
    } else if (request.type === 'ack') {
      if (!operation.waiting || request.sequence !== operation.sequence) throw new EngineError('Invalid stream acknowledgement.');
      operation.waiting = false;
      await this.#send({ type: 'host.ack', id: operation.id, sequence: operation.sequence++ });
    } else if (request.type === 'host.done' || request.type === 'host.error') {
      if (operation.waiting) throw new EngineError('A stream cannot finish before acknowledgement.');
      this.#operations.delete(operation.id);
      this.#counts.get(operation.service)[request.type === 'host.error' ? 'failed' : 'completed']++;
      await this.#send({ type: 'done', id: operation.id, failed: request.type === 'host.error' });
    } else if (request.type === 'cancel') {
      this.#operations.delete(operation.id);
      this.#counts.get(operation.service).cancelled++;
      await this.#send({ type: 'host.cancel', id: operation.id });
      await this.#send({ type: 'done', id: operation.id, failed: true, error: 'The engine operation was cancelled.' });
    }
  }

  async cancelService(service) {
    for (const operation of [...this.#operations.values()]) {
      if (operation.service === service) await this.receive({ type: 'cancel', id: operation.id });
    }
  }

  async close() {
    this.#closed = true;
    for (const component of [...this.#catalog].reverse()) await this.cancelService(component.id);
    for (const fiber of [...this.#fibers.values()].reverse()) await fiber.dispose();
    for (const fiber of [...this.#extensions].reverse()) await fiber.dispose();
    this.#fibers.clear();
    this.#extensions.length = 0;
    this.#handlers.clear();
    return this.inspect();
  }
}

export async function serve(input = process.stdin, output = process.stdout) {
  let writing = Promise.resolve();
  const send = frame => {
    const encoded = `${JSON.stringify(frame)}\n`;
    if (Buffer.byteLength(encoded) > MAX_FRAME) throw new EngineError('Oversized engine frame.');
    writing = writing.then(() => new Promise((resolve, reject) => output.write(encoded, error => error ? reject(error) : resolve())));
    return writing;
  };
  const engine = new CoreEngine(send);
  let pending = Buffer.alloc(0);
  try {
    for await (const chunk of input) {
      pending = Buffer.concat([pending, chunk]);
      let newline;
      while ((newline = pending.indexOf(10)) !== -1) {
        const line = pending.subarray(0, newline);
        pending = pending.subarray(newline + 1);
        if (line.length > MAX_FRAME) throw new EngineError('Oversized engine frame.');
        const request = JSON.parse(line.toString('utf8'));
        if (!object(request) || !identifier(request.id)) throw new EngineError('Invalid engine frame.');
        try {
          let result;
          if (request.type === 'initialize') result = await engine.initialize(request.components ?? {}, request.plugins ?? []);
          else if (request.type === 'configure') result = await engine.configure(request.components);
          else if (request.type === 'inspect') result = engine.inspect();
          else if (request.type === 'close') result = await engine.close();
          else if (request.type === 'invoke') { await engine.invoke(request); continue; }
          else if (['host.item', 'host.done', 'host.error', 'ack', 'cancel'].includes(request.type)) { await engine.receive(request); continue; }
          else throw new EngineError('Unknown engine request.');
          await send({ type: 'result', id: request.id, result });
          if (request.type === 'close') return;
        } catch (error) {
          const message = error instanceof EngineError ? error.message : 'The engine service could not complete the operation.';
          await send({ type: request.type === 'invoke' ? 'done' : 'result', id: request.id, failed: true, error: message });
        }
      }
      if (pending.length > MAX_FRAME) throw new EngineError('Oversized engine frame.');
    }
  } finally {
    await engine.close();
  }
}

// Only reviewed core extension snapshots; no project imports, remote catalogs,
// or telemetry clients.
await serve();
