// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Actual Harness registries with host-authorized execution-world providers.
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { Service, getTraceable } from '../vendor/cordis.mjs';
import { snapshotJsonValue } from '../vendor/harness-tools.mjs';

const MAX_BYTES = 256 * 1024;
const value = object => JSON.parse(JSON.stringify(object));
const bindingLifetimes = new WeakMap();

export async function runBoundPtc(runtime, spec, controller) {
  bindingLifetimes.set(spec, controller);
  try { return await runtime.run(spec); }
  finally { controller.abort(); bindingLifetimes.delete(spec); }
}

export async function registerHostServices(root, { enabled, hostData, requireCall, invokeHost, hostCall, executionContext }) {
  const fibers = [], scopes = new Set(), processes = new Set(), agentDisposers = new WeakMap();
  let fsModule, systemPromptModule, scopeModule, agents;
  function track(promise) {
    const call = requireCall();
    const pending = Promise.resolve(promise);
    call.pending = Promise.all([call.pending, pending]).then(() => undefined);
    void call.pending.catch(() => {});
    return pending;
  }
  async function invoke(method, params, signal) {
    signal?.throwIfAborted();
    const result = await track(invokeHost(`harness.${method}`, value(params), signal));
    signal?.throwIfAborted();
    return result;
  }
  async function filesystem(method, params, signal) {
    try { return await invoke(`fs.${method}`, params, signal); }
    catch (error) {
      const code = /\bFS_[A-Z_]+\b/.exec(error.message)?.[0] ?? 'FS_PERMISSION_DENIED';
      throw new fsModule.FsError(error.message, code);
    }
  }
  class FileSystem extends Service {
    constructor(ctx) { super(ctx, 'fs'); }
    get sandboxMode() { return hostData.harness?.writable ? 'workspace-write' : 'read-only'; }
    resolve(file, options = {}) { return filesystem('resolve', { path: file, cwd: options.cwd }, options.signal); }
    processPath(target) { return target.targetKey; }
    processPathFromHostPath() { return undefined; }
    fileUrl(target) { return pathToFileURL(target.targetKey).href; }
    contains(parent, child) {
      const relative = path.relative(parent.targetKey, child.targetKey);
      return relative === '' || (relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
    }
    async stat(target, signal) { return (await filesystem('stat', { target }, signal)) ?? undefined; }
    async lstat(file, options = {}, signal) { return (await filesystem('lstat', { path: file, cwd: options.cwd }, signal)) ?? undefined; }
    readText(target, signal) { return filesystem('readText', { target }, signal); }
    async streamText(target, signal) {
      const text = await this.readText(target, signal);
      return (async function* () { for (let offset = 0; offset < text.length; offset += 8192) { signal?.throwIfAborted(); yield text.slice(offset, offset + 8192); } })();
    }
    async readBytes(target, signal, maxBytes) { return Buffer.from(await filesystem('readBytes', { target, maxBytes: Math.min(maxBytes, MAX_BYTES) }, signal), 'base64'); }
    async readByteRange(target, range, signal) { return Buffer.from(await filesystem('readByteRange', { target, ...range }, signal), 'base64'); }
    listDir(target, signal) { return filesystem('listDir', { target }, signal); }
    writeText(target, content, expected, signal, policy) {
      if (policy?.mode === 'danger-full-access' || policy?.mode === 'read-only') throw new fsModule.FsError('Plugin write exceeds the task policy.', 'FS_SANDBOX_DENIED');
      return filesystem('writeText', { target, content, expected }, signal);
    }
    editText(target, edit, expected, signal, policy) {
      if (policy?.mode === 'danger-full-access' || policy?.mode === 'read-only') throw new fsModule.FsError('Plugin edit exceeds the task policy.', 'FS_SANDBOX_DENIED');
      return filesystem('editText', { target, edit, expected }, signal);
    }
  }
  class SandboxPolicy extends Service {
    constructor(ctx) { super(ctx, 'sandboxPolicy'); }
    resolve(request = {}) {
      const call = requireCall();
      if (request.session && request.session !== call.exec.agent?.session) throw new Error('Cannot select another task policy.');
      if (request.mode !== undefined) throw new Error('Plugin sandbox widening requires changing its reviewed grants.');
      return { mode: hostData.harness?.writable ? 'workspace-write' : 'read-only', workspaceRoot: call.exec.agent?.session.header.cwd };
    }
  }
  class ShellEnv extends Service {
    contributors = new Map();
    constructor(ctx) { super(ctx, 'shellEnv'); }
    register(contribution) {
      if (!contribution?.name || typeof contribution.resolve !== 'function') throw new TypeError('Invalid shell environment contribution.');
      return this.ctx.effect(() => {
        if (this.contributors.has(contribution.name)) throw new Error('Duplicate shell environment contribution.');
        for (const key of Object.keys(contribution.variables)) {
          if (!/^DSH_[A-Z][A-Z0-9_]*$/.test(key) || ['DSH_HOME', 'DSH_SHELL', 'DSH_SESSION_ID'].includes(key)
            || [...this.contributors.values()].some(item => Object.hasOwn(item.variables, key))) throw new Error('Conflicting managed shell environment variable.');
        }
        this.contributors.set(contribution.name, contribution);
        return () => this.contributors.delete(contribution.name);
      });
    }
    list() { return [...this.contributors.values()].flatMap(item => Object.entries(item.variables).map(([key, metadata]) => ({ key, contributor: item.name, ...metadata }))); }
    collect(exec) {
      const call = requireCall();
      if (exec !== call.exec) throw new Error('Shell environment belongs to the current execution.');
      const result = { DSH_SHELL: '/bin/bash', ...(exec.agent ? { DSH_SESSION_ID: exec.agent.session.id } : {}) };
      for (const item of this.contributors.values()) for (const [key, data] of Object.entries(item.resolve(exec))) {
        if (!Object.hasOwn(item.variables, key) || typeof data !== 'string') throw new Error('Undeclared managed environment variable.');
        result[key] = data;
      }
      return result;
    }
  }
  class Shell extends Service {
    constructor(ctx) { super(ctx, 'shell'); }
    get sandboxMode() { return hostData.harness?.writable ? 'workspace-write' : 'read-only'; }
    resolve(request) {
      const call = requireCall();
      const timeoutMs = Math.min(request.timeoutMs ?? 120000, hostData.harness?.commandTimeoutMs ?? 120000);
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError('Invalid shell timeout.');
      return { ...request, workdir: request.workdir ?? call.exec.agent?.session.header.cwd,
        timeoutMs: Math.floor(timeoutMs), stdoutMaxBytes: Math.min(request.stdoutMaxBytes ?? 65536, 65536) };
    }
    run(spec) { const { signal, ...request } = spec; return invoke('shell.run', { spec: request }, signal); }
    async start(spec) {
      const { signal, ...request } = spec;
      const initial = await invoke('shell.start', { spec: request }, signal);
      let current = initial, buffer = '', lossy = false, finished = false;
      const lifetime = new AbortController();
      const poll = async (method, params) => hostCall(`harness.shell.${method}`, { id: initial.id, ...params }, { signal: lifetime.signal });
      const handle = {
        get status() { return current.status; }, get exitCode() { return current.exitCode; }, get signal() { return current.signal; },
        readOutput() { const result = { delta: buffer, lossy }; buffer = ''; lossy = false; return result; },
        kill() { if (finished) return false; void poll('kill', {}).catch(() => {}); return true; },
        done: undefined,
      };
      const stop = () => handle.kill();
      signal?.addEventListener('abort', stop, { once: true });
      handle.done = (async () => {
        try {
          while (true) {
            current = await poll('read', {});
            buffer += current.delta;
            if (Buffer.byteLength(buffer) > 131072) { buffer = buffer.slice(-32768); lossy = true; }
            lossy ||= current.lossy;
            if (current.status !== 'running') break;
            await poll('wait', { timeoutMs: 250 });
          }
        } catch {
          current = { status: 'killed', exitCode: null, signal: null };
        } finally { finished = true; signal?.removeEventListener('abort', stop); processes.delete(handle); }
      })();
      processes.add(handle);
      return handle;
    }
  }
  class PtcRuntime extends Service {
    language = 'typescript';
    isolation = 'process';
    get timeout() { const maximum = Math.min(hostData.harness?.commandTimeoutMs ?? 120000, 120000); return Object.freeze({ defaultMs: maximum, maxMs: maximum }); }
    executionInstructions = 'Use TypeScript with top-level await and return. Only declared asynchronous bindings and explicitly granted files are available. Network access is disabled.';
    constructor(ctx) { super(ctx, 'ptcRuntime'); }
    get sandboxMode() { return hostData.harness?.writable ? 'workspace-write' : 'read-only'; }
    resolve(request) {
      const call = requireCall();
      if (request.timeoutMs === null) throw new Error('Unbounded PTC execution is not permitted.');
      const timeoutMs = Math.min(request.timeoutMs ?? this.timeout.defaultMs, this.timeout.maxMs);
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError('Invalid PTC timeout.');
      if (request.sandboxPolicy?.mode === 'danger-full-access') throw new Error('PTC cannot widen the plugin grants.');
      return { ...request, timeoutMs, cwd: request.cwd ?? call.exec.agent?.session.header.cwd };
    }
    async run(spec) {
      const { signal, bindings, program, cwd, timeoutMs, sandboxPolicy } = spec;
      signal?.throwIfAborted();
      if (!Array.isArray(bindings)) throw new TypeError('PTC bindings must be namespaces.');
      const namespaces = new Map();
      for (const entry of bindings) {
        if (!entry.functions || Object.values(entry.functions).some(fn => typeof fn !== 'function')) throw new TypeError('PTC bindings must be callable.');
        if (namespaces.has(entry.global)) throw new TypeError('Duplicate PTC namespace.');
        namespaces.set(entry.global, entry.functions);
      }
      let id;
      const pending = new Set();
      let settled = false;
      const cancel = () => { if (id) void hostCall('harness.ptc.cancel', { id }).catch(() => {}); };
      try {
        ({ id } = await invoke('ptc.start', { program, cwd, timeoutMs, sandboxPolicy,
          bindings: bindings.map(({ global, functions, errorClass }) => ({ global, members: Object.keys(functions), errorClass })) }, signal));
        signal?.addEventListener('abort', cancel, { once: true });
        while (true) {
          signal?.throwIfAborted();
          const event = await invoke('ptc.read', { id }, signal);
          if (event.type === 'done') { settled = true; bindingLifetimes.get(spec)?.abort(); await Promise.allSettled([...pending]); return event.result; }
          if (event.type === 'pending') continue;
          const methods = namespaces.get(event.namespace);
          if (event.type !== 'call' || !methods || !Object.hasOwn(methods, event.member) || !Array.isArray(event.args)) throw new Error('Invalid PTC callback.');
          if (pending.size >= 32) throw new Error('Too many pending PTC binding calls.');
          const operation = (async () => {
            let reply;
            try {
              const result = await methods[event.member](...event.args);
              const snapshot = snapshotJsonValue(result);
              if (snapshot === undefined) throw new TypeError('PTC bindings must return lossless JSON.');
              reply = { value: snapshot };
            } catch (error) { reply = { error: String(error?.message ?? error).slice(0, 4096) }; }
            if (!settled) await invoke('ptc.respond', { id, call_id: event.id, ...reply }, signal);
          })();
          pending.add(operation);
          void operation.finally(() => pending.delete(operation)).catch(() => {});
        }
      } catch (error) {
        return { logs: [], error: { kind: signal?.aborted ? 'abort' : 'protocol', message: String(error.message).slice(0, 4096) } };
      } finally {
        settled = true;
        bindingLifetimes.get(spec)?.abort();
        signal?.removeEventListener('abort', cancel);
        if (id) await hostCall('harness.ptc.cancel', { id }).catch(() => {});
        await Promise.allSettled([...pending]);
      }
    }
  }
  if (enabled.has('systemPrompt') || enabled.has('jobs') || enabled.has('agents')) {
    scopeModule = await import('./upstream/services/scope.mjs');
  }
  if (enabled.has('systemPrompt')) {
    systemPromptModule = await import('./upstream/services/system-prompt.mjs');
    const Base = systemPromptModule.SystemPrompt;
    const scoped = (service, method, args) => {
      const current = executionContext();
      if (current?.closed) throw new Error('Prompt registrations cannot outlive their active task.');
      const agent = current?.exec.agent;
      const receiver = agent && scopeModule.scopeOf(service.ctx) === undefined ? getTraceable(agent.ctx, service) : service;
      return Reflect.apply(Base.prototype[method], receiver, args);
    };
    class SystemPrompt extends Base {
      section(...args) { return scoped(this, 'section', args); }
      context(...args) { return scoped(this, 'context', args); }
      variable(...args) { return scoped(this, 'variable', args); }
      tools(...args) { return scoped(this, 'tools', args); }
      suppressRuntimeContext(...args) { return scoped(this, 'suppressRuntimeContext', args); }
    }
    fibers.push(await root.plugin(SystemPrompt, { includeHarnessIdentity: false, includeRuntimeContext: true }));
  }
  if (enabled.has('jobs')) {
    const { LocalJobRegistry } = await import('./upstream/services/jobs-local.mjs');
    fibers.push(await root.plugin(LocalJobRegistry, { maxConcurrentJobsPerOwner: 8 }));
  }
  if (enabled.has('agents')) {
    const { AgentRegistry } = await import('./upstream/services/agent.mjs');
    fibers.push(await root.plugin(AgentRegistry));
    agents = root.agents;
    agents.setFactory({
      async createAgent(ownerCtx, options) {
        const parent = requireCall().exec.agent;
        if (!parent || options.parentAgent && options.parentAgent !== parent || options.seed?.length || options.inheritedEventCount) {
          throw new Error('Delegated agents require this active parent and a fresh, unshared history.');
        }
        if (typeof options.sessionId !== 'string' || !options.sessionId || options.sessionId.length > 160) throw new Error('Invalid child session ID.');
        const selected = value(options.agentOptions ?? {});
        if (Object.keys(selected).some(key => !['provider', 'model'].includes(key))) throw new Error('Custom worker reasoning/output limits require a reviewed orchestration profile.');
        const events = [], inbox = [], controller = new AbortController();
        let worker, state = 'idle', activity = Promise.resolve(), disposed = false;
        const session = Object.freeze({ id: options.sessionId,
          header: Object.freeze({ id: options.sessionId, cwd: options.meta?.cwd ?? parent.session.header.cwd }),
          get events() { return Object.freeze([...events]); }, get seq() { return events.length; } });
        const agent = { id: options.sessionId, session, options: Object.freeze(selected),
          get status() { return state; },
          get inbox() { return Object.freeze({ nextTurn: Object.freeze([...inbox]), nextStep: Object.freeze([]) }); },
          whenIdle() { return activity; },
          cancel() { controller.abort(); if (worker) void hostCall('harness.agents.cancel', { id: worker.id }).catch(() => {}); },
          send(message, target, wakeup) {
            if (disposed) throw new Error('The child agent is disposed.');
            if (target !== 'next-turn' && target !== 'next-step') throw new Error('Invalid agent inbox target.');
            const caller = requireCall();
            if (caller.exec.agent !== parent) throw new Error('Only the owning task can drive this child.');
            const text = message.content?.map(block => {
              if (block.type !== 'text' || typeof block.text !== 'string') throw new Error('Delegated prompts currently accept explicit text only.');
              return block.text;
            }).join('\n');
            if (!text?.trim()) throw new Error('A delegated task must have explicit text.');
            if (state !== 'idle') {
              if (!worker) throw new Error('The child is still starting.');
              track(invokeHost('harness.agents.steer', { id: worker.id, text }));
              return;
            }
            if (!wakeup) { inbox.push(message); return; }
            if (worker) throw new Error('Create a fresh child to start another bounded assignment.');
            const task = [...inbox.splice(0), message].map(item => item.content.map(block => block.text).join('\n')).join('\n\n');
            state = 'running';
            const starting = invoke('agents.run', { task, cwd: session.header.cwd, options: selected }, controller.signal);
            activity = (async () => {
              try {
                worker = await starting;
                while (['running', 'blocked'].includes(worker.status)) {
                  worker = await hostCall('harness.agents.wait', { id: worker.id, timeoutMs: 250 });
                }
                events.push(value({ type: 'assistant/message', seq: events.length + 1,
                  data: { id: worker.id, role: 'assistant', content: [{ type: 'text', text: worker.output || worker.error || '' }],
                    source: { kind: 'model', provider: worker.provider, model: worker.model } } }));
              } finally { state = 'idle'; }
            })();
            void activity.catch(() => {});
          },
          followup(message) { this.send(message, 'next-turn', true); },
          inject(message) { this.send(message, 'next-step', true); },
          async runMaintenance(task) {
            if (state !== 'idle' || disposed) throw new Error('The child is not idle.');
            activity = Promise.resolve().then(() => task(controller.signal));
            return activity;
          },
        };
        const scope = scopeModule.createScope(ownerCtx, agent, { parent });
        agent.ctx = scope.ctx; scopes.add(scope);
        try {
          const commit = await options.setup?.(scope.ctx, agent);
          commit?.commit();
          const detach = agents.enter(agent, parent);
          const dispose = async () => {
            if (disposed) return;
            disposed = true; agent.cancel();
            await activity.catch(() => {});
            detach(); await scope.dispose(); scopes.delete(scope);
          };
          agentDisposers.set(agent, dispose);
          scope.ctx.effect(() => () => { agent.cancel(); });
          return { agent: Object.freeze(agent), dispose };
        } catch (error) { await scope.dispose(); scopes.delete(scope); throw error; }
      },
      async resume() { throw new Error('Resume delegated workers through Libre Claw task recovery; arbitrary session IDs do not authorize access.'); },
    });
  }
  if (enabled.has('fs') || enabled.has('shell')) fibers.push(await root.plugin(SandboxPolicy));
  if (enabled.has('fs')) {
    fsModule = await import('./upstream/services/fs.mjs');
    fibers.push(await root.plugin(FileSystem));
  }
  if (enabled.has('shell')) {
    fibers.push(await root.plugin(ShellEnv));
    fibers.push(await root.plugin(Shell));
  }
  if (enabled.has('ptcRuntime')) fibers.push(await root.plugin(PtcRuntime));
  if (enabled.has('lsp')) {
    const { Lsp } = await import('./upstream/services/lsp.mjs');
    fibers.push(await root.plugin(Lsp));
    for (const provider of hostData.lspProviders ?? []) {
      root.lsp.registerProvider({ id: provider.id, extensionToLanguage: provider.extensionToLanguage,
        query: (request, signal) => invoke('lsp.query', { provider: provider.id, request }, signal) });
    }
  }
  return {
    createAgent(id, session) {
      const agent = { id, session, ctx: root, options: {}, get status() { return 'running'; } };
      let scope;
      if (scopeModule) {
        scope = scopeModule.createScope(root, agent);
        agent.ctx = scope.ctx; scopes.add(scope);
      }
      function send(message) {
        const text = message.content.map(block => {
          if (block.type !== 'text' || typeof block.text !== 'string') throw new Error('Plugin notices require explicit text.');
          return block.text;
        }).join('\n');
        if (Buffer.byteLength(text) > 4096) throw new Error('Plugin notice exceeds its bound.');
        const operation = hostCall('harness.agent.notice', { owner: session.id, text });
        const call = executionContext();
        if (call && !call.closed && call.exec.agent === agent) {
          call.pending = Promise.all([call.pending, operation]).then(() => undefined);
          void call.pending.catch(() => {});
        } else void operation.catch(() => {});
      }
      agent.inject = send;
      agent.followup = send;
      const detach = agents?.enter(agent, undefined);
      agentDisposers.set(agent, async () => { detach?.(); if (scope) { await scope.dispose(); scopes.delete(scope); } });
      return Object.freeze(agent);
    },
    withAgent(agent, operation) { return agents && agent ? agents.withInitiator(agent, operation) : operation(); },
    hasActiveJobs(agent) { return enabled.has('jobs') && root.jobs.list(agent).some(job => ['running', 'stopping'].includes(job.status)); },
    releaseAgent(agent) { void agentDisposers.get(agent)?.().catch(() => {}); },
    async prompt(provided) {
      if (!systemPromptModule) return { text: '', sections: [], contexts: [] };
      const assembly = await root.systemPrompt.assemble(provided);
      const text = systemPromptModule.renderPrompt(assembly);
      const contexts = systemPromptModule.renderContextSections(assembly);
      const result = { text, sections: assembly.sections, contexts };
      if (Buffer.byteLength(JSON.stringify(result)) > 65536) throw new Error('Plugin prompt contribution exceeds 64 KiB.');
      return value(result);
    },
    async dispose() {
      for (const process of processes) process.kill();
      await Promise.all([...processes].map(process => process.done));
      for (const scope of scopes) await scope.dispose();
      for (const fiber of fibers.reverse()) await fiber.dispose();
    },
  };
}
