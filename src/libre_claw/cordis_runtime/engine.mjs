// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Trusted first-party service graph. Extension code is never loaded here.
import { Context, Service } from './vendor/cordis.mjs';

const MAX_FRAME = 64 * 1024;
const MAX_OPERATIONS = 128;
const STATES = ['PENDING', 'LOADING', 'ACTIVE', 'FAILED', 'DISPOSED', 'UNLOADING'];
const CATALOG = Object.freeze([
  { id: 'providers', title: 'Providers', dependencies: [], methods: ['complete', 'stream', 'models'] },
  { id: 'tools', title: 'Tools', dependencies: [], methods: ['execute', 'list'] },
  { id: 'sessions', title: 'Sessions', dependencies: [], methods: ['create', 'get', 'load', 'save', 'checkpoint', 'append', 'delete', 'list'] },
  { id: 'memory', title: 'Memory', dependencies: ['sessions', 'providers'], methods: ['load', 'retrieve', 'remember', 'search', 'extract'] },
  { id: 'agent', title: 'Agent', dependencies: ['providers', 'tools', 'sessions'], methods: ['run'] },
  { id: 'workflows', title: 'Workflows', dependencies: ['agent'], methods: ['run', 'plan', 'review'] },
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

  constructor(send) { this.#send = send; }

  inspect() {
    return {
      engine: 'cordis', runtime_version: '4.0.2', state: this.#closed ? 'closed' : 'running',
      privacy: { network: false, inherited_environment: false, payloads: 'opaque-handles', extensions: 'separate-processes' },
      active_operations: this.#operations.size,
      components: CATALOG.map(component => ({
        ...component, enabled: this.#enabled[component.id],
        state: this.#fibers.has(component.id) ? STATES[this.#fibers.get(component.id).state] : 'DISABLED',
        active_operations: [...this.#operations.values()].filter(operation => operation.service === component.id).length,
        ...this.#counts.get(component.id),
      })),
    };
  }

  async configure(changes, initialize = false) {
    if (this.#closed) throw new EngineError('The Cordis engine is closed.');
    if (!object(changes)) throw new EngineError('Components must be an object.');
    if (initialize === this.#initialized) throw new EngineError('Invalid engine initialization state.');
    if (this.#operations.size) throw new EngineError('Wait for active engine operations before changing components.');
    const enabled = initialize ? Object.fromEntries(CATALOG.map(component => [component.id, true])) : { ...this.#enabled };
    for (const [name, value] of Object.entries(changes)) {
      if (!Object.hasOwn(enabled, name) || typeof value !== 'boolean') throw new EngineError('Unknown component or invalid enabled flag.');
      enabled[name] = value;
    }
    for (const component of CATALOG) {
      if (enabled[component.id] && component.dependencies.some(name => !enabled[name])) {
        throw new EngineError(`${component.title} requires ${component.dependencies.join(', ')}.`);
      }
    }
    // Reverse order disposes dependants before their dependencies. Cordis owns
    // actual service registration and automatically removes it on disposal.
    for (const component of [...CATALOG].reverse()) {
      if (!enabled[component.id] && this.#fibers.has(component.id)) {
        await this.#fibers.get(component.id).dispose();
        this.#fibers.delete(component.id);
      }
    }
    for (const component of CATALOG) {
      if (!enabled[component.id] || this.#fibers.has(component.id)) continue;
      const dispatch = operation => this.#delegate(operation);
      class CoreService extends Service {
        static inject = component.dependencies;
        constructor(ctx) {
          super(ctx, component.id);
          this.ctx.effect(() => () => this.cancelPending());
        }
        invoke(operation) {
          if (!component.methods.includes(operation.method)) throw new EngineError('Unknown component method.');
          return dispatch(operation);
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
    const component = CATALOG.find(item => item.id === request.service);
    if (!component || !this.#enabled[component.id]) throw new EngineError('The requested engine component is disabled.');
    if (request.mode !== 'call' && request.mode !== 'stream') throw new EngineError('Invalid engine operation mode.');
    await this.#root[component.id].invoke({
      id: request.id, service: component.id, method: request.method, mode: request.mode, sequence: 0, waiting: false,
    });
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
    for (const component of [...CATALOG].reverse()) await this.cancelService(component.id);
    for (const fiber of [...this.#fibers.values()].reverse()) await fiber.dispose();
    this.#fibers.clear();
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
          if (request.type === 'initialize') result = await engine.configure(request.components ?? {}, true);
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

// No project imports, extension imports, remote catalogs, or telemetry clients.
await serve();
