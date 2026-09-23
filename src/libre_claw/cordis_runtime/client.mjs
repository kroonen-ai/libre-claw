// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Reviewed client bundles execute here, offline, never in the admin browser.
import * as Cordis from './vendor/cordis.mjs';
import { React, jsxRuntime, Reconciler, ReconcilerConstants } from './vendor/client-react.mjs';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const SCHEMA = JSON.parse(await readFile(new URL('./client-schema.json', import.meta.url), 'utf8'));
const {Context, Service} = Cordis;
const TAGS = new Set(SCHEMA.tags), PROPS = new Set(SCHEMA.props), STYLES = new Set(SCHEMA.styles);
const EVENTS = {onClick: 'click', onInput: 'input', onChange: 'change', onSubmit: 'submit'};
const DATA = new RegExp(SCHEMA.data_attribute_pattern);
const CLIENT_CHUNK = /^client\.[A-Za-z0-9][A-Za-z0-9._-]*\.js$/;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

class ClientError extends Error {}

function json(value, limit = SCHEMA.limits.snapshot_bytes) {
  let encoded;
  try { encoded = JSON.stringify(value); } catch { throw new ClientError('Client data must be bounded JSON.'); }
  if (encoded === undefined || Buffer.byteLength(encoded) > limit) throw new ClientError('Client data exceeds its size limit.');
  return encoded;
}

function style(value) {
  if (!object(value)) throw new ClientError('Client styles must be an object.');
  const result = {};
  for (const [name, entry] of Object.entries(value)) {
    if (!STYLES.has(name) || !['string', 'number'].includes(typeof entry)
        || typeof entry === 'number' && !Number.isFinite(entry)
        || String(entry).length > 128 || /url\s*\(|expression\s*\(|@import/i.test(String(entry))) {
      throw new ClientError('This client style is outside the isolated renderer contract.');
    }
    result[name] = entry;
  }
  return result;
}

function properties(raw, warnings) {
  const result = {};
  for (let [name, value] of Object.entries(raw)) {
    if (name === 'children' || name === 'key' || name === 'ref' || name in EVENTS || value === undefined || value === null) continue;
    if (name === 'className') { warnings.add('Plugin CSS classes stay inside the guest; the interface uses Libre Claw styling.'); continue; }
    if (name === 'defaultValue') name = 'value';
    if (name === 'defaultChecked') name = 'checked';
    if (!PROPS.has(name) && !DATA.test(name)) throw new ClientError('This client property is outside the isolated renderer contract.');
    if (name === 'style') value = style(value);
    else if (!['string', 'boolean', 'number'].includes(typeof value) || typeof value === 'number' && !Number.isFinite(value)) throw new ClientError('Invalid client property value.');
    result[name] = value;
  }
  if (result.type && !SCHEMA.input_types.includes(result.type)) throw new ClientError('This input type is not supported by isolated client interfaces.');
  return result;
}

export class ClientRuntime {
  #context;
  #fibers = [];
  #renderer;
  #views = new Map();
  #nodes = new Map();
  #events = new Map();
  #revision = 0;
  #dirty = true;
  #closed = false;
  #initialized = false;
  #failure = null;
  #warnings = new Set();
  #locale = 'en';
  #dictionaries = new Map();
  #packageName;
  #componentIds = [];
  #publicContext = {};
  #restoreGlobals = [];
  #registrations = new Map();
  #chunks = new Map();
  #chunkLoads = new Map();
  #imports = Promise.resolve();
  #importing = false;
  #expectedChunk;
  #pendingLoads = new Set();
  #moduleScope = randomUUID();
  #styles = new Set();
  document;

  constructor() {
    const runtime = this;
    const append = (parent, child, before) => {
      if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child);
      child.parent = parent;
      const index = before ? parent.children.indexOf(before) : -1;
      if (index < 0) parent.children.push(child); else parent.children.splice(index, 0, child);
    };
    const remove = (parent, child) => { parent.children = parent.children.filter(item => item !== child); child.parent = null; };
    this.#renderer = Reconciler({
      isPrimaryRenderer: true, supportsMutation: true, supportsPersistence: false, supportsHydration: false,
      getRootHostContext: () => null, getChildHostContext: () => null,
      getPublicInstance: node => node,
      prepareForCommit: () => null, resetAfterCommit: () => { runtime.#dirty = true; },
      createInstance(tag, props) {
        if (!TAGS.has(tag)) throw new ClientError('This client element is outside the isolated renderer contract.');
        properties(props, runtime.#warnings);
        const node = {id: randomUUID(), tag, props, children: [], parent: null, fields: {}, eventIds: {}};
        Object.defineProperties(node, {
          value: {get: () => String(node.fields.value ?? node.props.value ?? node.props.defaultValue ?? ''), set: value => { node.fields.value = String(value); runtime.#dirty = true; }},
          checked: {get: () => node.fields.checked ?? node.props.checked ?? node.props.defaultChecked ?? false, set: value => { node.fields.checked = Boolean(value); runtime.#dirty = true; }},
          name: {get: () => node.props.name || ''},
          type: {get: () => node.props.type || ''},
        });
        return node;
      },
      createTextInstance: text => ({id: randomUUID(), tag: '#text', text: String(text), children: [], parent: null}),
      appendInitialChild: append, appendChild: append, appendChildToContainer: append,
      insertBefore: append, insertInContainerBefore: append,
      removeChild: remove, removeChildFromContainer: remove,
      finalizeInitialChildren: () => false, shouldSetTextContent: () => false,
      prepareUpdate: () => true,
      commitUpdate(node, _payload, _tag, previous, next) {
        properties(next, runtime.#warnings); node.props = next;
        if (next.value !== undefined && previous.value !== next.value) delete node.fields.value;
        if (next.checked !== undefined && previous.checked !== next.checked) delete node.fields.checked;
      },
      commitTextUpdate: (node, _old, text) => { node.text = String(text); },
      resetTextContent: node => { node.children = []; },
      clearContainer: container => { container.children = []; },
      hideInstance: node => { node.hidden = true; }, unhideInstance: node => { node.hidden = false; },
      hideTextInstance: node => { node.hidden = true; }, unhideTextInstance: node => { node.hidden = false; },
      scheduleTimeout: setTimeout, cancelTimeout: clearTimeout, noTimeout: -1,
      supportsMicrotasks: true, scheduleMicrotask: queueMicrotask,
      getCurrentEventPriority: () => ReconcilerConstants.DefaultEventPriority,
      getInstanceFromNode: () => null, beforeActiveInstanceBlur() {}, afterActiveInstanceBlur() {},
      preparePortalMount() {}, detachDeletedInstance() {},
    });
  }

  #installGlobal(name, value) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name);
    this.#restoreGlobals.push(() => original ? Object.defineProperty(globalThis, name, original) : delete globalThis[name]);
    Object.defineProperty(globalThis, name, {configurable: true, writable: true, value});
  }

  #window() {
    const window = new EventTarget();
    const dataset = Object.create(null);
    const runtime = this;
    const head = {append(node) {
      if (!runtime.#styles.has(node)) throw new ClientError('Direct document rendering is unavailable; register a React slot.');
      runtime.#warnings.add('Plugin stylesheets are retained offline and are not executed in the dashboard.');
    }, appendChild(node) { this.append(node); return node; }};
    const document = {documentElement: {dataset}, head, createElement(tag) {
      if (String(tag).toLowerCase() !== 'style') throw new ClientError('Direct DOM creation is unavailable; register a React slot.');
      const element = {dataset: Object.create(null), textContent: '', remove() { runtime.#styles.delete(element); }};
      runtime.#styles.add(element); return element;
    }};
    window.document = document;
    window.__ModuleLoader__ = {mode: 'live', load: registration => {
      if (this.#closed || !this.#importing || !object(registration) || registration.id !== this.#packageName
          || registration.chunk !== this.#expectedChunk || this.#registrations.has(registration.chunk)
          || typeof registration.factory !== 'function') {
        throw new ClientError('The client bundle registration does not match its reviewed package.');
      }
      this.#registrations.set(registration.chunk, registration.factory);
    }};
    this.document = document;
    this.#installGlobal('window', window); this.#installGlobal('document', document); this.#installGlobal('self', window);
  }

  #active() {
    if (this.#closed) throw new ClientError('The client interface is disposed; loading was cancelled.');
  }

  #loadBundle(file, chunk) {
    // Serialize registration windows so one reviewed file cannot register a
    // different chunk while a second import is in flight. No paths come from JS.
    const operation = this.#imports.then(async () => {
      this.#active();
      this.#importing = true; this.#expectedChunk = chunk;
      try {
        const url = pathToFileURL(file);
        url.searchParams.set('client-runtime', this.#moduleScope);
        await import(url.href);
        this.#active();
        if (!this.#registrations.has(chunk)) throw new ClientError('The client bundle did not register its expected factory.');
      } finally { this.#importing = false; this.#expectedChunk = undefined; }
    });
    this.#imports = operation.catch(() => {});
    return operation;
  }

  #cancellable(operation) {
    this.#active();
    let cancel;
    const cancelled = new Promise((_resolve, reject) => { cancel = reject; });
    this.#pendingLoads.add(cancel);
    return Promise.race([operation, cancelled]).finally(() => this.#pendingLoads.delete(cancel));
  }

  #require() {
    const modules = new Map([['react', React], ['react/jsx-runtime', jsxRuntime], ['@deepseek-ai/cordis', Cordis]]);
    const require = id => {
      this.#active();
      if (!modules.has(id)) throw new ClientError('This client requires an unavailable module. Supported modules: React, JSX runtime, and Cordis.');
      return modules.get(id);
    };
    require.async = id => {
      try {
        this.#active();
        if (modules.has(id)) return Promise.resolve(modules.get(id));
        if (typeof id !== 'string' || !id.startsWith('./') || !CLIENT_CHUNK.test(id.slice(2)) || !this.#chunks.has(id.slice(2))) {
          throw new ClientError('This client chunk is not a reviewed package-local sibling.');
        }
        const chunk = id.slice(2);
        if (!this.#chunkLoads.has(chunk)) {
          const operation = this.#loadBundle(this.#chunks.get(chunk), chunk).then(() => {
            this.#active();
            const exports = this.#registrations.get(chunk)(require);
            this.#active();
            return exports;
          });
          // Keep successes and failures: a factory is never executed twice.
          this.#chunkLoads.set(chunk, this.#cancellable(operation));
        }
        return this.#chunkLoads.get(chunk);
      } catch (error) { return Promise.reject(error); }
    };
    return require;
  }

  async initialize(params) {
    if (this.#initialized || this.#closed) throw new ClientError('This client runtime cannot be initialized again.');
    if (!object(params) || typeof params.plugin_id !== 'string' || typeof params.package_name !== 'string'
        || typeof params.entry !== 'string' || !path.isAbsolute(params.entry)
        || !Array.isArray(params.component_ids) || params.component_ids.length > 129
        || !params.component_ids.every(id => typeof id === 'string' && id.length <= 160)
        || params.locale !== undefined && (typeof params.locale !== 'string' || params.locale.length > 32)
        || !object(params.chunks ?? {}) || Object.keys(params.chunks ?? {}).length > 256
        || Object.entries(params.chunks ?? {}).some(([name, file]) => !CLIENT_CHUNK.test(name)
          || typeof file !== 'string' || !path.isAbsolute(file) || file !== path.join(path.dirname(params.entry), name)
          || file === params.entry)) {
      throw new ClientError('Invalid client initialization metadata.');
    }
    this.#initialized = true; this.#packageName = params.package_name; this.#componentIds = params.component_ids;
    this.#chunks = new Map(Object.entries(params.chunks ?? {}));
    this.#locale = params.locale || 'en';
    const config = JSON.parse(json(params.config ?? {}, 64 * 1024));
    const context = params.context ?? {};
    if (!object(context) || Object.keys(context).some(key => !['run_id', 'state'].includes(key))
        || Object.values(context).some(value => typeof value !== 'string' || value.length > 160)) throw new ClientError('Invalid client task context.');
    this.#publicContext = Object.freeze({...context});
    this.#window();
    this.#context = new Context();
    const runtime = this, dictionaries = this.#dictionaries;
    class LocaleService extends Service {
      constructor(ctx) { super(ctx, 'locale'); }
      register(namespace, languageOrDictionaries, dictionary) {
        if (typeof namespace !== 'string' || namespace.length > 160) throw new ClientError('Invalid client locale namespace.');
        const values = typeof languageOrDictionaries === 'string' ? {[languageOrDictionaries]: dictionary} : languageOrDictionaries;
        if (!object(values)) throw new ClientError('Invalid client locale dictionary.');
        const copy = JSON.parse(json(values, 64 * 1024));
        if (Object.values(copy).some(value => !object(value) || Object.values(value).some(text => typeof text !== 'string'))) throw new ClientError('Client translations must be strings.');
        return this.ctx.effect(() => {
          if (dictionaries.has(namespace)) throw new ClientError('A client locale namespace is already registered.');
          dictionaries.set(namespace, copy);
          return () => dictionaries.delete(namespace);
        });
      }
      getLocale() { return runtime.#locale; }
    }
    class SlotsService extends Service {
      constructor(ctx) { super(ctx, 'slots'); }
      inject(name, callback) {
        if (!SCHEMA.slots.includes(name) || typeof callback !== 'function') throw new ClientError('This client slot is unavailable in Libre Claw.');
        return this.ctx.effect(callback);
      }
      register(options, component) {
        if (!object(options) || Object.keys(options).some(key => !['name', 'id', 'key', 'locale', 'priority'].includes(key))
            || !SCHEMA.slots.includes(options.name) || !(typeof component === 'function'
              || object(component) && [Symbol.for('react.memo'), Symbol.for('react.forward_ref'), Symbol.for('react.lazy')].includes(component.$$typeof))
            || options.locale !== undefined && typeof options.locale !== 'string'
            || options.priority !== undefined && !Number.isSafeInteger(options.priority)
            || options.name === 'shell.overlay' && (typeof options.id !== 'string' || !options.id || options.id.length > 160)) throw new ClientError('Unsupported client slot declaration.');
        if (options.name === 'plugins.row.config' && !runtime.#componentIds.some(id => options.key === `${runtime.#packageName}#${id}`)) {
          throw new ClientError('Client configuration slots must belong to this plugin.');
        }
        return this.ctx.effect(() => runtime.#register(options, component));
      }
    }
    try {
      for (const service of [LocaleService, SlotsService]) {
        const fiber = this.#context.plugin(service); this.#fibers.push(fiber); await fiber.await();
        this.#active();
      }
      await this.#cancellable(this.#loadBundle(params.entry));
      this.#active();
      const exported = this.#registrations.get(undefined)(this.#require()), plugin = exported?.default ?? exported;
      this.#active();
      const fiber = this.#context.plugin(plugin, config); this.#fibers.push(fiber); await fiber.await();
      this.#active();
      if (fiber.state !== 2) throw new ClientError('The client plugin requires unavailable services.');
      return this.snapshot();
    } catch (error) {
      await this.dispose();
      throw error instanceof ClientError ? error : new ClientError('The client could not start. Its DOM or service requirements may need adaptation.');
    }
  }

  #register(options, component) {
    this.#active();
    const modes = options.name === 'plugins.row.config' ? ['summary', 'page'] : ['panel'];
    const registration = `${options.name}:${options.key ?? options.id}`;
    if ([...this.#views.values()].some(view => view.registration === registration)) throw new ClientError('A client slot entry is already registered.');
    if (this.#views.size + modes.length > SCHEMA.limits.views) throw new ClientError('Too many client interface views.');
    const added = [];
    for (const mode of modes) {
      const view = {id: randomUUID(), registration, priority: options.priority ?? 0, slot: options.name, ...(options.key ? {key: options.key} : {}), mode, children: []};
      const root = this.#renderer.createContainer(view, ReconcilerConstants.LegacyRoot, null, false, null, '', error => {
        this.#failure = error instanceof ClientError ? error : new ClientError('The client interface failed to render.');
      }, null);
      const t = (key, values = {}) => {
        const languages = this.#dictionaries.get(options.locale) ?? {};
        const text = languages[this.#locale]?.[key] ?? languages.en?.[key] ?? String(key);
        return text.replace(/\{([^}]+)\}/g, (whole, name) => Object.hasOwn(values, name) ? String(values[name]) : whole);
      };
      view.root = root; this.#views.set(view.id, view); added.push(view);
      this.#renderer.flushSync(() => this.#renderer.updateContainer(React.createElement(component, {t, view: mode, context: this.#publicContext}), root, null, null));
    }
    return () => {
      for (const view of added) {
        if (!this.#views.has(view.id)) continue;
        this.#renderer.flushSync(() => this.#renderer.updateContainer(null, view.root, null, null));
        this.#views.delete(view.id);
      }
    };
  }

  snapshot() {
    if (!this.#initialized || this.#closed) throw new ClientError('The client interface is not active.');
    for (let pass = 0; pass < 16; pass++) {
      if (!this.#renderer.flushPassiveEffects()) break;
      if (pass === 15) throw new ClientError('The client effects did not settle within their render limit.');
    }
    if (this.#failure) throw this.#failure;
    if (this.#dirty) { this.#revision++; this.#dirty = false; }
    this.#events.clear(); this.#nodes.clear(); let count = 0;
    const serialize = (node, depth) => {
      if (++count > SCHEMA.limits.nodes || depth > SCHEMA.limits.depth) throw new ClientError('The client interface exceeds its node limit.');
      this.#nodes.set(node.id, node);
      if (node.tag === '#text') return {id: node.id, tag: '#text', text: node.hidden ? '' : node.text, props: {}, events: {}, children: []};
      const props = properties(node.props, this.#warnings), events = {};
      if (node.hidden) props.style = {...props.style, display: 'none'};
      if (['input', 'textarea', 'select'].includes(node.tag)) {
        if (node.fields.value !== undefined) props.value = node.fields.value;
        if (node.fields.checked !== undefined) props.checked = node.fields.checked;
      }
      for (const [name, event] of Object.entries(EVENTS)) {
        const callback = node.props[name];
        if (callback === undefined) continue;
        if (typeof callback !== 'function') throw new ClientError('Invalid client event handler.');
        const id = node.eventIds[event] ??= randomUUID(); events[event] = id;
        this.#events.set(id, {node, callback, event});
      }
      return {id: node.id, tag: node.tag, props, events, children: node.children.map(child => serialize(child, depth + 1))};
    };
    const snapshot = {revision: this.#revision, status: 'active', views: [...this.#views.values()].sort((left, right) => left.priority - right.priority).map(view => ({
      id: view.id, slot: view.slot, ...(view.key ? {key: view.key} : {}), mode: view.mode, tree: view.children.map(node => serialize(node, 0)),
    })), warnings: [...this.#warnings]};
    json(snapshot); return snapshot;
  }

  async event(params) {
    // Effects may have committed since the browser last polled. Never execute
    // a callback from an older visible tree against those newer component props.
    this.snapshot();
    json(params, SCHEMA.limits.event_bytes);
    if (!object(params) || Object.keys(params).some(key => !['revision', 'event_id', 'target_id', 'values'].includes(key))
        || params.revision !== this.#revision || typeof params.event_id !== 'string' || !object(params.values ?? {})) throw new ClientError('The client interface changed; refresh before trying this action.');
    const event = this.#events.get(params.event_id);
    if (!event || this.#closed) throw new ClientError('This client action is no longer available.');
    const target = this.#nodes.get(params.target_id ?? event.node.id);
    const within = (node, parent) => {
      while (node) { if (node === parent) return true; node = node.parent; }
      return false;
    };
    if (!target || !within(target, event.node)) throw new ClientError('The client event target is unavailable.');
    let scope = event.node;
    while (scope.parent && scope.tag !== 'form') scope = scope.parent;
    for (const [id, values] of Object.entries(params.values ?? {})) {
      const node = this.#nodes.get(id);
      if (!node || !within(node, scope) || !['input', 'textarea', 'select'].includes(node.tag) || !object(values)
          || Object.keys(values).some(key => !['value', 'checked'].includes(key))
          || values.value !== undefined && (typeof values.value !== 'string' || values.value.length > 16384)
          || values.checked !== undefined && typeof values.checked !== 'boolean') throw new ClientError('Invalid client input values.');
      Object.assign(node.fields, values);
    }
    try {
      let result;
      this.#renderer.flushSync(() => { result = event.callback({type: event.event, target, currentTarget: event.node,
        preventDefault() {}, stopPropagation() {}, nativeEvent: {type: event.event}}); });
      if (result && typeof result.then === 'function') await result;
      this.#renderer.flushSync(() => {});
      this.#dirty = true;
      return this.snapshot();
    } catch (error) { this.#dirty = true; throw error instanceof ClientError ? error : new ClientError('The client action failed.'); }
  }

  async dispose() {
    if (this.#closed) return {disposed: true};
    this.#closed = true;
    for (const cancel of this.#pendingLoads) cancel(new ClientError('The client interface is disposed; loading was cancelled.'));
    this.#pendingLoads.clear(); this.#chunks.clear(); this.#chunkLoads.clear(); this.#registrations.clear();
    try {
      for (const fiber of [...this.#fibers].reverse()) await fiber.dispose();
      for (const view of this.#views.values()) this.#renderer.flushSync(() => this.#renderer.updateContainer(null, view.root, null, null));
      this.#renderer.flushPassiveEffects();
    } finally {
      this.#views.clear(); this.#nodes.clear(); this.#events.clear(); this.#styles.clear(); this.#dictionaries.clear();
      for (const restore of this.#restoreGlobals.reverse()) restore();
    }
    return {disposed: true};
  }
}

export async function serve(input = process.stdin, output = process.stdout) {
  for (const method of ['log', 'info', 'debug', 'warn', 'error']) console[method] = () => {};
  const runtime = new ClientRuntime(); let pending = Buffer.alloc(0);
  const send = async value => {
    const line = json(value, SCHEMA.limits.frame_bytes) + '\n';
    await new Promise((resolve, reject) => output.write(line, error => error ? reject(error) : resolve()));
  };
  try {
    for await (const chunk of input) {
      pending = Buffer.concat([pending, chunk]); let newline;
      while ((newline = pending.indexOf(10)) !== -1) {
        const line = pending.subarray(0, newline); pending = pending.subarray(newline + 1);
        if (line.length > SCHEMA.limits.frame_bytes) throw new ClientError('Oversized client frame.');
        let request;
        try {
          request = JSON.parse(line.toString('utf8'));
          if (!object(request) || !(typeof request.id === 'string' && request.id.length <= 128 || Number.isSafeInteger(request.id))) throw new ClientError('Invalid client request.');
          let result;
          if (request.method === 'initialize') result = await runtime.initialize(request.params);
          else if (request.method === 'snapshot') result = runtime.snapshot();
          else if (request.method === 'event') result = await runtime.event(request.params);
          else if (['dispose', 'shutdown'].includes(request.method)) result = await runtime.dispose();
          else throw new ClientError('Unknown client operation.');
          await send({id: request.id, result});
          if (['dispose', 'shutdown'].includes(request.method)) return;
        } catch (error) {
          await send({id: request?.id ?? null, error: {message: error instanceof ClientError ? error.message : 'The isolated client operation failed.'}});
        }
      }
      if (pending.length > SCHEMA.limits.frame_bytes) throw new ClientError('Oversized client frame.');
    }
  } finally { await runtime.dispose(); }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await serve();
