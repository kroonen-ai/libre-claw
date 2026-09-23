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

  constructor(options, onDisposed) { this.#options = options; this.#onDisposed = onDisposed; }

  async start() {
    const graph = this;
    const { features = {}, enabled = {}, fetcher = globalThis.fetch?.bind(globalThis), origin = globalThis.location?.origin ?? 'http://localhost' } = this.#options;
    if (typeof fetcher !== 'function') throw new CordisUiError('Dashboard HTTP transport is unavailable.');
    for (const component of CATALOG) {
      if (enabled[component.id] === false) continue;
      const feature = features[component.id] ?? {};
      class UiService extends Service {
        static inject = component.dependencies;
        #requests = new Set();

        constructor(ctx) {
          super(ctx, component.id);
          ctx.effect(() => () => { for (const controller of this.#requests) controller.abort(); this.#requests.clear(); });
          if (feature.setup) {
            const cleanup = feature.setup();
            if (typeof cleanup === 'function') ctx.effect(() => cleanup);
          }
          for (const binding of feature.bindings ?? []) this.bind(binding.target, binding.event, binding.handler, binding.options);
          const timers = graph.#options.timers ?? globalThis;
          for (const interval of feature.intervals ?? []) {
            ctx.effect(() => {
              const id = timers.setInterval(() => this.run(interval.handler), interval.milliseconds);
              return () => timers.clearInterval(id);
            });
          }
          if (feature.dispose) ctx.effect(() => feature.dispose);
        }

        // Keep the owner's fiber context even when Cordis exposes this service
        // through a consumer proxy. DOM effects must not attach to the root.
        run = (handler, ...args) => {
          if (graph.#disposed) return;
          try {
            const result = handler(...args);
            if (result && typeof result.then === 'function') return result.catch(error => graph.#options.onError?.(error));
            return result;
          } catch (error) { graph.#options.onError?.(error); }
        };

        bind = (target, event, handler, options) => {
          if (!target?.addEventListener || typeof handler !== 'function') throw new CordisUiError('Invalid dashboard event binding.');
          const callback = (...args) => this.run(handler, ...args);
          return this.ctx.effect(() => {
            target.addEventListener(event, callback, options);
            return () => target.removeEventListener(event, callback, options);
          });
        };

        request = async (path, options = {}) => {
          if (graph.#disposed) throw new CordisUiError('The dashboard services are stopped.');
          if (component.id !== 'api') return this.ctx.api.request(path, options);
          const controller = new AbortController();
          const abort = () => controller.abort();
          options.signal?.addEventListener('abort', abort, { once: true });
          if (options.signal?.aborted) abort();
          this.#requests.add(controller);
          try {
            const response = await fetcher(path, {
              ...options, credentials: 'same-origin', redirect: 'error', signal: controller.signal,
              headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
            });
            const payload = await response.json().catch(() => ({}));
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
    if (this.#disposed || this.#fibers.get(id)?.state !== 2) throw new CordisUiError(`Dashboard ${id} service is unavailable.`);
    return this.#root[id];
  }

  bind(id, target, event, handler, options) { return this.#service(id).bind(target, event, handler, options); }

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
      components: CATALOG.map(component => ({ ...component, state: this.#fibers.has(component.id) ? STATES[this.#fibers.get(component.id).state] : 'DISABLED' })) };
  }

  dispose() {
    if (this.#disposing) return this.#disposing;
    this.#disposed = true;
    this.#disposing = (async () => {
      try {
        for (const fiber of [...this.#fibers.values()].reverse()) await fiber.dispose();
      } finally { this.#onDisposed(); }
    })();
    return this.#disposing;
  }
}
