// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Harness's tool-author contract over Libre Claw's approved execution boundary.
import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { Service } from '../vendor/cordis.mjs';
import {
  HarnessError, assertObjectJsonSchema, assertSupportedJsonSchema,
  validateJsonSchemaValue, snapshotJsonValue, deepFreeze,
} from '../vendor/harness-tools.mjs';

export * from '../vendor/harness-tools.mjs';

const NAME = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const MAX_BYTES = 512 * 1024;

function json(value, label = 'Harness plugin value') {
  const copy = snapshotJsonValue(value);
  if (copy === undefined || Buffer.byteLength(JSON.stringify(copy)) > MAX_BYTES) {
    throw new HarnessError(`${label} must be bounded, lossless JSON.`, 'INVALID_JSON');
  }
  return deepFreeze(copy);
}

function failure(code, message) {
  return {
    isError: true, content: [{ type: 'text', text: `Error: ${message}` }],
    error: { message, info: { name: 'HarnessCompatibilityError', code } },
  };
}

function blocks(candidate) {
  const result = json(candidate, 'Tool content');
  const mediaTypes = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
  const keys = (value, allowed) => Object.keys(value).every(key => allowed.includes(key));
  const positive = value => Number.isSafeInteger(value) && value > 0;
  const encoded = (value, mediaType) => typeof value === 'string' && value.length > 0
    && mediaTypes.has(mediaType) && /^[A-Za-z0-9+/]*={0,2}$/.test(value)
    && value.length % 4 === 0 && Buffer.from(value, 'base64').toString('base64') === value;
  const valid = block => {
    if (!block || typeof block !== 'object') return false;
    if (block.type === 'text' || block.type === 'reasoning') return keys(block, ['type', 'text']) && typeof block.text === 'string';
    if (block.type !== 'image') return false;
    if (block.source) return keys(block, ['type', 'source']) && keys(block.source, ['type', 'data', 'media_type'])
      && block.source.type === 'base64' && encoded(block.source.data, block.source.media_type);
    if (Object.hasOwn(block, 'data')) return keys(block, ['type', 'data', 'mediaType', 'mimeType'])
      && (block.mediaType === undefined || block.mimeType === undefined || block.mediaType === block.mimeType)
      && encoded(block.data, block.mediaType ?? block.mimeType);
    const ref = block.attachment;
    return ref && keys(block, ['type', 'attachment', 'offloaded'])
      && keys(ref, ['attachmentId', 'mediaType', 'bytes', 'width', 'height', 'name', 'originalDimensions'])
      && typeof ref.attachmentId === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$/.test(ref.attachmentId)
      && mediaTypes.has(ref.mediaType) && positive(ref.bytes) && positive(ref.width) && positive(ref.height)
      && (ref.name === undefined || (typeof ref.name === 'string' && ref.name.length <= 255 && !/[\\/]/.test(ref.name)))
      && (ref.originalDimensions === undefined || (keys(ref.originalDimensions, ['width', 'height']) && positive(ref.originalDimensions.width) && positive(ref.originalDimensions.height)))
      && (block.offloaded === undefined || block.offloaded === true);
  };
  if (!Array.isArray(result) || result.some(block => !valid(block))) {
    throw new HarnessError('Tool content contains an unsupported or malformed block.', 'UNSUPPORTED_CONTENT');
  }
  return result;
}

function validate(schema, value, code) {
  if (validateJsonSchemaValue(schema, value, '').length) {
    throw new HarnessError(code === 'INVALID_ARGS'
      ? 'Arguments do not match the tool schema.' : 'Tool output does not match its declared schema.', code);
  }
}

function validateProjection(schema, value) {
  const checked = schema.safeParse(value);
  if (!checked.success) throw new HarnessError('Session projection failed schema validation.', 'INVALID_PROJECTION');
  return json(checked.data, 'Session projection');
}

/**
 * Install genuine Cordis services for the supported Harness tool API.
 * All host effects go through hostCall; no filesystem, model, credential,
 * approval, network, or arbitrary session service is implicitly installed.
 * The returned async disposer also exposes inspect() for capability reporting.
 */
export async function registerHarnessServices(root, {
  registerTool, hostCall, executionContext = () => undefined,
  hostServices = ['userQuestions', 'sessionProjections'], hostData = {},
} = {}) {
  if (typeof registerTool !== 'function') throw new TypeError('registerTool is required.');
  const registrations = new Map();
  const guards = new Set();
  const restrictions = new Set();
  const projections = new Map();
  const projectionListeners = new Set();
  const sessions = new WeakSet();
  const sessionCache = new Map();
  const sessionRecords = new WeakMap();
  const active = new AsyncLocalStorage();
  const lifetime = new AbortController();
  const fibers = [];
  let toolsService;
  const enabledServices = new Set(typeof hostCall === 'function' ? hostServices : []);

  function scopeAllowed(scope) {
    return scope === undefined || scope === active.getStore()?.exec.agent;
  }

  function visible(name, scope) {
    if (!scopeAllowed(scope)) return undefined;
    for (const filter of restrictions) {
      if ((filter.allow && !filter.allow.has(name)) || filter.deny?.has(name)) return undefined;
    }
    return registrations.get(name)?.definition;
  }

  function syncExposedTools() {
    for (const [name, registration] of registrations) {
      if (visible(name)) {
        if (!registration.unregister) {
          registration.unregister = registerTool(registration.schema, registration.handler);
          if (typeof registration.unregister !== 'function') throw new TypeError('registerTool must return its disposer.');
        }
      } else {
        registration.unregister?.();
        registration.unregister = undefined;
      }
    }
  }

  function requireCall() {
    const call = active.getStore();
    if (!call || call.closed) throw new HarnessError('This service requires an active tool execution.', 'NO_ACTIVE_EXECUTION');
    call.exec.signal.throwIfAborted();
    return call;
  }

  async function invokeHost(method, params) {
    const call = requireCall();
    if (typeof hostCall !== 'function') throw new HarnessError('The requested host service is unavailable.', 'UNSUPPORTED_SERVICE');
    // The host derives project/run authority from the outer invocation. These
    // identifiers correlate events; they must never grant access on their own.
    const result = await hostCall(method, json(params), {
      signal: call.exec.signal, callId: call.exec.callId,
      agentId: call.exec.agent?.id, sessionId: call.exec.agent?.session.id,
    });
    call.exec.signal.throwIfAborted();
    return result;
  }

  async function invokeModelHost(method, params, signal) {
    const caller = active.getStore()?.exec.signal ?? lifetime.signal;
    const combined = signal ? AbortSignal.any([caller, signal]) : caller;
    combined.throwIfAborted();
    const result = await hostCall(method, json(params), { signal: combined });
    combined.throwIfAborted();
    return json(result, 'Model broker response');
  }

  function drive(session, event) {
    for (const registration of projections.values()) {
      let state = registration.cells.get(session);
      if (state === undefined && !registration.cells.has(session)) {
        state = validateProjection(registration.definition.stateSchema,
          registration.definition.init(session.header, 0));
      }
      state = validateProjection(registration.definition.stateSchema,
        registration.definition.apply(state, event));
      registration.cells.set(session, state);
      if (registration.definition.wire) {
        const value = validateProjection(registration.definition.wire.viewSchema,
          registration.definition.wire.view(state));
        for (const listener of projectionListeners) listener(session, registration.definition.key, value);
      }
    }
    root.emit('session/event', session, event);
  }

  function createSession(source, agentId) {
    if (!source || typeof source.id !== 'string' || !source.id || source.id.length > 256) return undefined;
    const incoming = source.events === undefined ? undefined : json(source.events, 'Session events');
    if (incoming !== undefined && (!Array.isArray(incoming) || incoming.length > 128)) {
      throw new HarnessError('Session history exceeds its bounded window.', 'INVALID_SESSION');
    }
    let record = sessionCache.get(source.id);
    const header = json(source.header ?? { id: source.id, ...(source.cwd ? { cwd: source.cwd } : {}) });
    if (record && (record.agent.id !== agentId || !isDeepStrictEqual(record.session.header, header))) {
      throw new HarnessError('A session cannot change its caller or workspace.', 'INVALID_SESSION');
    }
    const lastIncoming = incoming?.at(-1)?.seq ?? incoming?.length;
    const rewritten = incoming?.some((event, index) => {
      const seq = Number.isSafeInteger(event.seq) ? event.seq : index + 1;
      const previous = record?.events.find(item => item.seq === seq);
      return previous && (previous.type !== event.type || !isDeepStrictEqual(previous.data, event.data));
    });
    if (record && (record.invalid || rewritten || (lastIncoming !== undefined && lastIncoming < record.seq))) {
      if (record.active) throw new HarnessError('Cannot replace a session during an active call.', 'INVALID_SESSION');
      sessionCache.delete(source.id);
      record = undefined;
    }
    if (!record) {
      if (sessionCache.size >= 32) {
        const oldest = [...sessionCache.values()].find(item => item.active === 0);
        if (!oldest) throw new HarnessError('All plugin session slots are active.', 'INVALID_SESSION');
        sessionCache.delete(oldest.session.id);
      }
      record = { events: [], seq: 0, active: 0, invalid: false, session: undefined, agent: undefined };
      const session = {
        id: source.id, header,
        get seq() { return record.seq; },
        get events() { return Object.freeze([...record.events]); },
        append(type, data) {
          const owner = requireCall();
          if (owner.exec.agent?.session !== session) throw new HarnessError('Session writes belong to their active invocation.', 'INVALID_SESSION');
          if (typeof type !== 'string' || !type || type.length > 160) throw new HarnessError('Invalid session event type.', 'INVALID_SESSION_EVENT');
          owner.exec.signal.throwIfAborted();
          const event = json({ type, data, seq: record.seq + 1, time: Date.now() });
          const updated = [...record.events, event].slice(-128);
          if (Buffer.byteLength(JSON.stringify(updated)) > 256 * 1024) throw new HarnessError('Session history exceeds its byte limit.', 'INVALID_SESSION');
          record.events = updated;
          record.seq = event.seq;
          try { drive(session, event); }
          catch (error) { record.invalid = true; throw error; }
          // Harness append is synchronous. Join queued durable writes before
          // succeeding, and discard speculative state if the host rejects it.
          const write = owner.pending.then(() => invokeHost('session.append', {
            session_id: session.id, type, data: event.data,
          })).catch(error => { record.invalid = true; throw error; });
          owner.pending = write;
          void write.catch(() => {});
          return event;
        },
      };
      record.session = Object.freeze(session);
      record.agent = Object.freeze({ id: agentId, session: record.session, ctx: root });
      sessions.add(session);
      sessionRecords.set(session, record);
    }
    // Preserve object identity for plugin WeakMaps while touching LRU order.
    sessionCache.delete(source.id);
    sessionCache.set(source.id, record);
    for (const [index, event] of (incoming ?? []).entries()) {
      const seq = Number.isSafeInteger(event.seq) ? event.seq : index + 1;
      if (seq <= record.seq) continue;
      const copy = json({ ...event, seq }, 'Session event');
      record.events = [...record.events, copy].slice(-128);
      record.seq = seq;
      try { drive(record.session, copy); }
      catch (error) { record.invalid = true; throw error; }
    }
    return record;
  }

  function makeCall(name, args, provided) {
    const signal = provided?.signal ?? new AbortController().signal;
    if (!(signal instanceof AbortSignal)) throw new HarnessError('Invalid tool cancellation signal.', 'INVALID_EXECUTION');
    const call = { pending: Promise.resolve(), additionalContexts: [], concluded: false, closed: false, exec: undefined };
    const sourceAgent = provided?.agent;
    const existingAgent = sourceAgent && sourceAgent === active.getStore()?.exec.agent;
    const record = existingAgent ? sessionRecords.get(sourceAgent.session)
      : sourceAgent?.session ? createSession(sourceAgent.session, sourceAgent.id ?? sourceAgent.session.id) : undefined;
    const agent = record?.agent;
    const callId = provided?.callId ?? randomUUID();
    call.exec = {
      callId, rootCallId: provided?.rootCallId ?? callId,
      name, arguments: args, token: Symbol('libre-harness-tool'), signal,
      ...(provided?.parent ? { parent: provided.parent } : {}),
      ...(agent ? { agent } : {}),
      deferContext(context) { requireCall(); call.additionalContexts.push(json(context, 'Deferred context')); },
      concludeTurn() { requireCall(); call.concluded = true; },
    };
    for (const field of ['callId', 'rootCallId', 'name', 'arguments', 'token', 'agent', 'parent']) {
      if (Object.hasOwn(call.exec, field)) Object.defineProperty(call.exec, field, { writable: false, configurable: false });
    }
    if (record) { record.active++; call.sessionRecord = record; }
    return call;
  }

  async function execute(name, rawArgs, provided) {
    const definition = visible(name) && registrations.get(name).execution;
    if (!definition) return failure('UNKNOWN_TOOL', 'The requested plugin tool is unavailable.');
    let call;
    let result;
    try {
      const args = json(rawArgs, 'Tool arguments');
      validate(definition.parameters, args, 'INVALID_ARGS');
      call = makeCall(name, args, provided);
      result = await active.run(call, async () => {
        const exec = call.exec;
        const callerSignal = exec.signal;
        if (callerSignal.aborted) return failure('ABORTED_BEFORE_DISPATCH', 'Tool call was cancelled before execution.');
        const decision = await root.waterfall(toolsService, 'tools/pre-execute', exec, () => ({ kind: 'allow' }));
        if (decision?.kind === 'cancel' || callerSignal.aborted) return failure('ABORTED_BEFORE_DISPATCH', 'Tool call was cancelled before execution.');
        if (decision?.kind !== 'allow') {
          // A plugin's ask/allow decision can never manufacture host approval.
          return failure('DENIED', decision?.kind === 'ask'
            ? 'This plugin requires a host approval service that is not available.' : 'A plugin policy denied this tool call.');
        }
        for (const guard of guards) {
          if (guard(exec) !== undefined) return failure('DENIED', 'A tool guard denied this call.');
        }
        const canonicalResults = new WeakSet();
        const canonical = (value) => {
          const copy = json(value, 'Tool output');
          validate(definition.output.schema, copy, 'INVALID_TOOL_OUTPUT');
          const result = json({
            isError: false, value: copy, content: blocks(definition.output.render(args, copy)),
            ...(definition.output.presentationMeta ? { meta: json(definition.output.presentationMeta(args, copy)) } : {}),
          });
          canonicalResults.add(result);
          return result;
        };
        let outcome;
        try {
          outcome = await root.waterfall(toolsService, 'tools/execute', exec, async () => {
            exec.signal = AbortSignal.any([callerSignal, exec.signal]);
            exec.signal.throwIfAborted();
            return canonical(await definition.execute(args, exec));
          });
          await call.pending;
          if (outcome?.isError === false) {
            if (!canonicalResults.has(outcome)) outcome = canonical(outcome.value);
          }
          else if (outcome?.isError === true) outcome = { ...outcome, content: blocks(outcome.content) };
          else throw new HarnessError('Tool wrapper returned an invalid outcome.', 'INVALID_TOOL_OUTPUT');
        } catch (error) {
          await call.pending.catch(() => {});
          outcome = errorResult(error, callerSignal);
        }
        const post = await root.waterfall(toolsService, 'tools/post-execute', exec, json(outcome), () => ({ kind: 'accept' }));
        if (post?.kind === 'block') {
          outcome = { ...failure('POST_EXECUTE_BLOCKED', 'A plugin policy blocked the result.'), content: blocks(post.feedback) };
        } else if (post?.kind === 'accept') {
          if (Object.hasOwn(post, 'value')) outcome = canonical(post.value);
          else if (Object.hasOwn(post, 'content')) outcome = { ...outcome, content: blocks(post.content) };
        } else throw new HarnessError('Tool policy returned an invalid decision.', 'INVALID_TOOL_OUTPUT');
        if (post.additionalContexts) call.additionalContexts.push(...json(post.additionalContexts));
        await call.pending;
        if (!outcome.isError && callerSignal.aborted) outcome = failure('ABORTED', 'Tool call was cancelled.');
        if (!outcome.isError && call.concluded) outcome = { ...outcome, concludesTurn: true };
        if (call.additionalContexts.length) outcome = { ...outcome, additionalContexts: json(call.additionalContexts) };
        return outcome;
      });
    } catch (error) {
      result = errorResult(error, provided?.signal);
    }
    if (call) {
      call.closed = true;
      try {
        const content = definition.finalizeContent?.(Object.freeze(call.exec), json(result));
        if (content !== undefined) result = { ...result, content: blocks(content) };
      } catch { result = failure('INVALID_TOOL_OUTPUT', 'The tool content finalizer failed.'); }
      try { active.run(call, () => root.emit(toolsService, 'tools/result', Object.freeze(call.exec), json(result))); }
      catch { /* Observation cannot replace a settled tool result. */ }
      if (call.sessionRecord) call.sessionRecord.active--;
    }
    return json(result);
  }

  function errorResult(error, signal) {
    if (signal?.aborted) return failure('ABORTED', 'Tool call was cancelled.');
    const supported = new Set(['INVALID_ARGS', 'INVALID_TOOL_OUTPUT', 'INVALID_JSON', 'UNSUPPORTED_CONTENT', 'INVALID_PROJECTION', 'NO_ACTIVE_EXECUTION', 'UNSUPPORTED_SERVICE', 'INVALID_SESSION', 'INVALID_SESSION_EVENT']);
    const code = error instanceof HarnessError && supported.has(error.code) ? error.code : 'TOOL_FAILED';
    const messages = {
      INVALID_ARGS: 'Arguments do not match the tool schema.',
      INVALID_TOOL_OUTPUT: 'Tool output does not match its declared schema.',
      UNSUPPORTED_CONTENT: 'Tool content contains an unsupported or malformed block.',
    };
    return failure(code, messages[code] ?? 'The Harness plugin tool failed.');
  }

  class Tools extends Service {
    constructor(ctx) { super(ctx, 'tools'); toolsService = this; }

    register(definition) {
      if (!definition || !NAME.test(definition.name ?? '') || definition.name === 'run_code'
        || typeof definition.description !== 'string' || typeof definition.execute !== 'function'
        || !definition.output || typeof definition.output.render !== 'function') {
        throw new TypeError('A Harness tool requires a name, description, parameters, execute, and output schema/render.');
      }
      assertObjectJsonSchema(definition.parameters);
      assertSupportedJsonSchema(definition.output.schema);
      if (definition.timeoutMs !== undefined && (!Number.isFinite(definition.timeoutMs) || definition.timeoutMs <= 0)) {
        throw new TypeError('Tool timeoutMs must be positive.');
      }
      const normalized = {
        ...definition,
        parameters: json(definition.parameters),
        output: { ...definition.output, schema: json(definition.output.schema) },
      };
      return this.ctx.effect(() => {
        if (registrations.has(definition.name)) throw new TypeError('A tool with that name is already registered.');
        if (registrations.size >= 64) throw new TypeError('A Harness plugin may register at most 64 tools.');
        const registration = {
          definition, execution: normalized,
          schema: { name: definition.name, description: definition.description, input_schema: normalized.parameters },
          async handler(args) {
            const outcome = await execute(definition.name, args, executionContext());
            return {
              content: outcome.content.filter(block => block.type === 'text').map(block => block.text).join('\n'), error: outcome.isError,
              ...(outcome.content.some(block => block.type !== 'text') ? { content_blocks: outcome.content } : {}),
              ...(outcome.meta === undefined ? {} : { meta: outcome.meta }),
              ...(outcome.additionalContexts ? { additional_contexts: outcome.additionalContexts } : {}),
              ...(outcome.concludesTurn ? { concludes_turn: true } : {}),
            };
          },
        };
        registrations.set(definition.name, registration);
        try {
          syncExposedTools();
        } catch (error) { registrations.delete(definition.name); throw error; }
        root.emit('tools/change');
        return () => {
          registration.unregister?.();
          if (registrations.get(definition.name) === registration) registrations.delete(definition.name);
          root.emit('tools/change');
        };
      });
    }

    get(name, scope) { return visible(name, scope); }
    schemas(scope) {
      return [...registrations.keys()].filter(name => visible(name, scope)).map(name => {
        const { description, parameters } = registrations.get(name).execution;
        return { name, description, parameters };
      });
    }
    execute(input) {
      // Nested calls stay inside the current approved plugin, and cannot select
      // another caller or a session that the outer host did not provide.
      const current = requireCall().exec;
      if (input.agent && input.agent !== current.agent) throw new HarnessError('Cannot change the tool caller.', 'INVALID_EXECUTION');
      return execute(input.name, input.arguments, {
        ...current, callId: input.callId ?? randomUUID(), parent: current.token,
        signal: input.signal ? AbortSignal.any([current.signal, input.signal]) : current.signal,
      });
    }
    guard(callback) {
      if (typeof callback !== 'function') throw new TypeError('Tool guard must be callable.');
      return this.ctx.effect(() => { guards.add(callback); return () => guards.delete(callback); });
    }
    restrict(filter) {
      if (!filter || (!Array.isArray(filter.allow) && !Array.isArray(filter.deny))) throw new TypeError('Tool restriction requires allow or deny.');
      const entry = { ...(filter.allow ? { allow: new Set(filter.allow) } : {}), ...(filter.deny ? { deny: new Set(filter.deny) } : {}) };
      for (const name of [...(entry.allow ?? []), ...(entry.deny ?? [])]) {
        if (!registrations.has(name)) throw new TypeError('Tool restriction names an unknown tool.');
      }
      return this.ctx.effect(() => {
        restrictions.add(entry); syncExposedTools(); root.emit('tools/change');
        return () => { restrictions.delete(entry); syncExposedTools(); root.emit('tools/change'); };
      });
    }
    presentAs(mode) {
      if (mode !== 'native') throw new HarnessError('Programmatic tool calling requires a compatible PTC runtime.', 'UNSUPPORTED_SERVICE');
      return this.ctx.effect(() => () => {});
    }
  }

  class UserQuestions extends Service {
    constructor(ctx) { super(ctx, 'userQuestions'); }
    async ask(request) {
      const call = requireCall();
      if (request.agent && request.agent !== call.exec.agent) throw new HarnessError('Cannot select a different question recipient.', 'INVALID_SESSION');
      request.signal?.throwIfAborted();
      const result = await invokeHost('userQuestions.ask', { questions: request.questions });
      request.signal?.throwIfAborted();
      return json(result, 'Question answers');
    }
  }

  class SessionProjections extends Service {
    constructor(ctx) { super(ctx, 'sessionProjections'); }
    register(definition) {
      if (!definition || typeof definition.key !== 'string' || !definition.key
        || typeof definition.init !== 'function' || typeof definition.apply !== 'function'
        || typeof definition.stateSchema?.safeParse !== 'function'
        || !Number.isSafeInteger(definition.stateVersion) || definition.stateVersion < 0
        || (definition.wire && (typeof definition.wire.view !== 'function' || typeof definition.wire.viewSchema?.safeParse !== 'function'))) {
        throw new TypeError('Invalid Harness session projection definition.');
      }
      return this.ctx.effect(() => {
        let registration = projections.get(definition.key);
        if (registration && registration.definition.stateVersion !== definition.stateVersion) throw new TypeError('Session projection version conflict.');
        if (!registration) {
          registration = { definition, cells: new WeakMap(), refs: 0 };
          projections.set(definition.key, registration);
        }
        registration.refs++;
        return () => { if (--registration.refs === 0) projections.delete(definition.key); };
      });
    }
    stateOf(session, key) {
      if (!sessions.has(session)) throw new HarnessError('Session projections require the current tool session.', 'INVALID_SESSION');
      const registration = projections.get(key);
      if (!registration) return undefined;
      if (!registration.cells.has(session)) registration.cells.set(session,
        validateProjection(registration.definition.stateSchema, registration.definition.init(session.header, 0)));
      return registration.cells.get(session);
    }
    onChanged(listener) {
      if (typeof listener !== 'function') throw new TypeError('Projection listener must be callable.');
      return this.ctx.effect(() => { projectionListeners.add(listener); return () => projectionListeners.delete(listener); });
    }
  }

  class Llm extends Service {
    constructor(ctx) { super(ctx, 'llm'); }
    listProviders() { return json(hostData.providers ?? []); }
    listConfigurableProviders() { return json(hostData.configurableProviders ?? []); }
    listModels(provider) { return invokeModelHost('llm.listModels', { provider }); }
    resolveModelInfo(provider, model, signal) { return invokeModelHost('llm.resolveModelInfo', { provider, model }, signal); }
    async *stream(options) {
      const { signal, ...request } = options;
      const caller = active.getStore()?.exec.signal ?? lifetime.signal;
      const combined = signal ? AbortSignal.any([caller, signal]) : caller;
      combined.throwIfAborted();
      const safe = json(request, 'Model request');
      if (typeof hostCall.stream === 'function') {
        for await (const chunk of hostCall.stream('llm.stream', safe, { signal: combined })) {
          combined.throwIfAborted();
          yield json(chunk, 'Model stream chunk');
        }
      } else {
        const chunks = await invokeModelHost('llm.stream', safe, combined);
        if (!Array.isArray(chunks)) throw new HarnessError('Model broker did not return a stream.', 'INVALID_MODEL_STREAM');
        for (const chunk of chunks) {
          combined.throwIfAborted();
          yield json(chunk, 'Model stream chunk');
        }
      }
    }
  }

  try {
    fibers.push(await root.plugin(Tools));
    if (enabledServices.has('userQuestions')) fibers.push(await root.plugin(UserQuestions));
    if (enabledServices.has('sessionProjections')) fibers.push(await root.plugin(SessionProjections));
    if (enabledServices.has('llm')) fibers.push(await root.plugin(Llm));
  } catch (error) {
    for (const fiber of fibers.reverse()) await fiber.dispose();
    throw error;
  }
  const dispose = async () => { lifetime.abort(); for (const fiber of [...fibers].reverse()) await fiber.dispose(); sessionCache.clear(); };
  dispose.inspect = () => ({
    services: ['tools', ...['userQuestions', 'sessionProjections', 'llm'].filter(name => enabledServices.has(name))],
    tools: [...registrations.keys()].sort(), projections: [...projections.keys()].sort(), sessions: sessionCache.size,
  });
  return dispose;
}
