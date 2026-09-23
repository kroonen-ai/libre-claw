// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// First-party browser services only. Extension packages never enter this page.
import { Context, Service } from './cordis.mjs';

const STATES = ['PENDING', 'LOADING', 'ACTIVE', 'FAILED', 'DISPOSED', 'UNLOADING'];
const CATALOG = [
  { id: 'api', dependencies: [] },
  { id: 'appearance', dependencies: ['api'] },
  { id: 'models', dependencies: ['api'] },
  { id: 'tasks', dependencies: ['api', 'models'] },
  { id: 'questions', dependencies: ['api', 'tasks'] },
  { id: 'plugins', dependencies: ['api'] },
  { id: 'engine', dependencies: ['api'] },
  { id: 'workflows', dependencies: ['api', 'tasks'] },
];
const mounted = new WeakMap();

export class CordisUiError extends Error {}

const connected = target => typeof target?.nodeType !== 'number' || target.isConnected === true;

// One registry is owned by one Cordis fiber. Rerenders add/remove entries here,
// never additional fiber effects, so disposed DOM nodes are not retained.
class DomEffects {
  #scopes = new Map();
  #active;
  #run;
  #timers;
  #changed;
  #closed = false;
  #permanent = {};

  constructor(active, run, timers, changed) {
    this.#active = active; this.#run = run; this.#timers = timers; this.#changed = changed;
  }

  #track(target, kind, cleanup) {
    const owner = target ?? this.#permanent;
    let entries = this.#scopes.get(owner);
    if (!entries) { entries = new Set(); this.#scopes.set(owner, entries); }
    let live = true;
    const record = {kind, dispose: () => {
      if (!live) return;
      live = false; cleanup(); entries.delete(record);
      if (!entries.size) this.#scopes.delete(owner);
    }};
    entries.add(record); this.#changed();
    return {dispose: record.dispose, current: () => live && !this.#closed && this.#active() && connected(owner)};
  }

  bind(target, event, handler, options) {
    if (this.#closed || !target?.addEventListener || typeof handler !== 'function') throw new CordisUiError('Invalid dashboard event binding.');
    let registration;
    const callback = (...args) => {
      if (!registration.current()) { registration.dispose(); return; }
      if (options?.once) registration.dispose();
      return this.#run(handler, ...args);
    };
    registration = this.#track(target, 'binding', () => target.removeEventListener(event, callback, options));
    target.addEventListener(event, callback, options);
    return registration.dispose;
  }

  schedule(kind, handler, delay, owner) {
    if (this.#closed) throw new CordisUiError('The dashboard effects are stopped.');
    const [set, clear] = kind === 'frame' ? ['requestAnimationFrame', 'cancelAnimationFrame']
      : kind === 'interval' ? ['setInterval', 'clearInterval'] : ['setTimeout', 'clearTimeout'];
    if (typeof this.#timers[set] !== 'function' || typeof this.#timers[clear] !== 'function') throw new CordisUiError('This dashboard timer is unavailable.');
    let handle, registration;
    registration = this.#track(owner, 'timer', () => this.#timers[clear](handle));
    handle = this.#timers[set]((...args) => {
      const live = registration.current();
      if (kind !== 'interval' || !live) registration.dispose();
      if (live) return this.#run(handler, ...args);
    }, delay);
    return registration.dispose;
  }

  release(root) {
    for (const [target, entries] of this.#scopes) {
      if (root === target || typeof target?.nodeType === 'number' && root?.contains?.(target)) for (const entry of [...entries]) entry.dispose();
    }
  }

  sweep() {
    for (const [target, entries] of this.#scopes) {
      if (!connected(target)) for (const entry of [...entries]) entry.dispose();
    }
  }

  inspect() {
    const entries = [...this.#scopes.values()].flatMap(scope => [...scope]);
    return {scopes: this.#scopes.size, bindings: entries.filter(entry => entry.kind === 'binding').length, timers: entries.filter(entry => entry.kind === 'timer').length};
  }

  dispose() {
    this.#closed = true;
    for (const entries of this.#scopes.values()) for (const entry of [...entries]) entry.dispose();
  }
}

/**
 * Mount once per page scope. A feature supplies bindings and optional setup,
 * dispose and interval hooks. Cordis owns activation/dependency/disposal.
 * The returned graph exposes request(), bind(), inspect() and dispose().
 */
export function mountDashboard(options = {}) {
  const scope = options.scope ?? globalThis.document;
  if (!scope || typeof scope !== 'object') throw new CordisUiError('A dashboard scope is required.');
  if (mounted.has(scope)) return mounted.get(scope);
  const graph = new DashboardServices(options, () => mounted.delete(scope));
  const pending = graph.start().then(() => graph).catch(async error => {
    await graph.dispose();
    throw error;
  });
  mounted.set(scope, pending);
  return pending;
}

class DashboardServices {
  #root = new Context();
  #fibers = new Map();
  #options;
  #onDisposed;
  #disposed = false;
  #disposing = null;
  #effects = new Map();
  #observer = null;
  #sweepQueued = false;
  #disabled = new Set();

  constructor(options, onDisposed) { this.#options = options; this.#onDisposed = onDisposed; }

  async start() {
    const graph = this;
    const { features = {}, enabled = {}, fetcher = globalThis.fetch?.bind(globalThis), origin = globalThis.location?.origin ?? 'http://localhost' } = this.#options;
    if (typeof fetcher !== 'function') throw new CordisUiError('Dashboard HTTP transport is unavailable.');
    const Observer = this.#options.MutationObserver ?? globalThis.MutationObserver;
    const observationRoot = this.#options.scope ?? globalThis.document;
    if (Observer && observationRoot?.nodeType) {
      this.#observer = new Observer(() => this.#sweep());
      this.#observer.observe(observationRoot, {childList: true, subtree: true});
    }
    for (const component of CATALOG) {
      if (enabled[component.id] === false) continue;
      const feature = features[component.id] ?? {};
      class UiService extends Service {
        static inject = component.dependencies;
        #requests = new Set();
        #domEffects;

        constructor(ctx) {
          super(ctx, component.id);
          ctx.effect(() => () => { for (const controller of this.#requests) controller.abort(); this.#requests.clear(); });
          this.#domEffects = new DomEffects(() => graph.active(component.id), this.run, graph.#options.timers ?? globalThis, () => graph.#scheduleSweep());
          graph.#effects.set(component.id, this.#domEffects);
          ctx.effect(() => () => { this.#domEffects.dispose(); graph.#effects.delete(component.id); });
          if (feature.setup) {
            const cleanup = feature.setup();
            if (typeof cleanup === 'function') ctx.effect(() => cleanup);
          }
          for (const binding of feature.bindings ?? []) this.bind(binding.target, binding.event, binding.handler, binding.options);
          for (const interval of feature.intervals ?? []) {
            this.#domEffects.schedule('interval', interval.handler, interval.milliseconds);
          }
          if (feature.dispose) ctx.effect(() => feature.dispose);
        }

        // Keep the owner's fiber context even when Cordis exposes this service
        // through a consumer proxy. DOM effects must not attach to the root.
        run = (handler, ...args) => {
          if (!graph.active(component.id)) return;
          try {
            const result = handler(...args);
            if (result && typeof result.then === 'function') return result.catch(error => { if (graph.active(component.id)) graph.#options.onError?.(error); });
            return result;
          } catch (error) { if (graph.active(component.id)) graph.#options.onError?.(error); }
        };

        bind = (target, event, handler, options) => {
          return this.#domEffects.bind(target, event, handler, options);
        };

        schedule = (kind, handler, delay, owner) => this.#domEffects.schedule(kind, handler, delay, owner);

        request = async (path, options = {}) => {
          if (!graph.active(component.id)) throw new CordisUiError('The dashboard services are stopped.');
          const controller = new AbortController();
          const abort = () => controller.abort();
          options.signal?.addEventListener('abort', abort, { once: true });
          if (options.signal?.aborted) abort();
          this.#requests.add(controller);
          try {
            if (component.id !== 'api') return await this.ctx.api.request(path, {...options, signal: controller.signal});
            const response = await fetcher(path, {
              ...options, credentials: 'same-origin', redirect: 'error', signal: controller.signal,
              headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
            });
            const payload = await response.json().catch(() => ({}));
            if (!graph.active(component.id) || controller.signal.aborted) throw new CordisUiError('The dashboard services are stopped.');
            if (!response.ok) throw new CordisUiError(payload.error || response.statusText || 'The daemon request failed.');
            return payload;
          } finally {
            options.signal?.removeEventListener('abort', abort);
            this.#requests.delete(controller);
          }
        };
      }
      const fiber = this.#root.plugin(UiService);
      this.#fibers.set(component.id, fiber);
      await fiber.await();
    }
    const lifecycle = this.#options.lifecycle ?? globalThis.window;
    if (lifecycle?.addEventListener && this.#fibers.get('appearance')?.state === 2) {
      this.bind('appearance', lifecycle, 'pagehide', event => { if (!event.persisted) return this.dispose(); });
    }
    this.origin = new URL(origin).origin;
  }

  #service(id) {
    if (!this.active(id)) throw new CordisUiError(`Dashboard ${id} service is unavailable.`);
    return this.#root[id];
  }

  bind(id, target, event, handler, options) { return this.#service(id).bind(target, event, handler, options); }
  active(id) { return !this.#disposed && !this.#disabled.has(id) && this.#fibers.get(id)?.state === 2; }
  run(id, handler, ...args) { return this.#service(id).run(handler, ...args); }
  timeout(id, handler, milliseconds, owner) { return this.#service(id).schedule('timeout', handler, milliseconds, owner); }
  frame(id, handler, owner) { return this.#service(id).schedule('frame', handler, undefined, owner); }
  release(root) { for (const effects of this.#effects.values()) effects.release(root); }

  #scheduleSweep() {
    if (this.#sweepQueued || this.#disposed) return;
    this.#sweepQueued = true;
    queueMicrotask(() => { this.#sweepQueued = false; this.#sweep(); });
  }

  #sweep() {
    if (this.#disposed) return;
    for (const effects of this.#effects.values()) effects.sweep();
  }

  async disable(id) {
    if (this.#disposed || !this.#fibers.has(id)) return;
    this.#disabled.add(id);
    for (const component of CATALOG) {
      if (component.dependencies.some(dependency => this.#disabled.has(dependency))) this.#disabled.add(component.id);
    }
    await this.#fibers.get(id).dispose();
    for (const fiber of this.#fibers.values()) await fiber.await();
  }

  request(path, options = {}) {
    if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || path.includes('\\')) {
      throw new CordisUiError('Dashboard requests must use the local daemon API.');
    }
    const url = new URL(path, this.origin);
    if (url.origin !== this.origin) throw new CordisUiError('Dashboard requests must stay on this origin.');
    const pathname = url.pathname;
    let service = 'api';
    if (/^\/runs\/[^/]+\/questions(?:\/|$)/.test(pathname)) service = 'questions';
    else if (/^\/engine(?:\/|$)/.test(pathname)) service = 'engine';
    else if (/^\/plugins(?:\/|$)/.test(pathname)) service = 'plugins';
    else if (/^\/config\/theme(?:\/|$)/.test(pathname)) service = 'appearance';
    else if (/^\/(models|providers|config)(?:\/|$)/.test(pathname)) service = 'models';
    else if (/^\/(workspace|automations|orchestration)(?:\/|$)/.test(pathname)) service = 'workflows';
    else if (/^\/(runs|usage)(?:\/|$)/.test(pathname)) service = 'tasks';
    return this.#service(service).request(url.pathname + url.search, options);
  }

  inspect() {
    return { engine: 'cordis', runtime_version: '4.0.2', state: this.#disposed ? 'disposed' : 'active',
      components: CATALOG.map(component => ({ ...component, state: this.#fibers.has(component.id) ? STATES[this.#fibers.get(component.id).state] : 'DISABLED',
        effects: this.#effects.get(component.id)?.inspect() ?? {scopes: 0, bindings: 0, timers: 0} })) };
  }

  dispose() {
    if (this.#disposing) return this.#disposing;
    this.#disposed = true;
    this.#observer?.disconnect();
    this.#disposing = (async () => {
      try {
        for (const fiber of [...this.#fibers.values()].reverse()) await fiber.dispose();
      } finally { this.#onDisposed(); }
    })();
    return this.#disposing;
  }
}
