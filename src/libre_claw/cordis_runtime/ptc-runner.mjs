// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// One program per restricted process. Host functions cross a bounded JSON pipe.
import { createInterface } from 'node:readline';
import { format } from 'node:util';
import * as module from 'node:module';

const MAX = 256 * 1024;
const pending = new Map();
let started = false, finished = false, sequence = 0, outputBytes = 0;
const logs = [];
const write = process.stdout.write.bind(process.stdout);
function send(frame) {
  const data = JSON.stringify(frame) + '\n';
  if (Buffer.byteLength(data) > MAX) throw new Error('PTC transport output exceeds its limit.');
  write(data);
}
function lossless(value, depth = 0, budget = {remaining: 10000}) {
  if (depth > 32 || --budget.remaining < 0) throw new TypeError('JSON value exceeds nesting or item limits.');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value) && !Object.is(value, -0)
      && (!Number.isInteger(value) || Number.isSafeInteger(value))) return;
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index++) {
      if (!Object.hasOwn(value, index)) throw new TypeError('Sparse arrays are not lossless JSON.');
      lossless(value[index], depth + 1, budget);
    }
    return;
  }
  if (typeof value === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== 'string') throw new TypeError('Symbol keys are not lossless JSON.');
      lossless(value[key], depth + 1, budget);
    }
    return;
  }
  throw new TypeError('Completion and binding values must be lossless JSON.');
}
function complete(result) {
  if (finished) return;
  finished = true;
  try { send({type: 'done', result: {...result, logs}}); }
  catch { send({type: 'done', result: {logs: [], error: {kind: 'output-limit', message: 'PTC output exceeded its bounded budget.'}}}); }
  setImmediate(() => process.exit(0));
}
const logger = (...values) => {
  const text = format(...values);
  outputBytes += Buffer.byteLength(text);
  if (outputBytes > 64 * 1024 || logs.length >= 256) {
    complete({error: {kind: 'output-limit', message: 'PTC logs exceeded their budget.'}});
    throw new Error('PTC output limit exceeded.');
  }
  logs.push(text);
};
const capturedConsole = Object.freeze({log: logger, info: logger, warn: logger, error: logger, debug: logger});

async function run(spec) {
  const globals = [], values = [];
  for (const namespace of spec.bindings) {
    const object = Object.create(null);
    let Rejection;
    if (namespace.errorClass) {
      const {name, memberNameProperty} = namespace.errorClass;
      Rejection = class extends Error {
        constructor(message, member) { super(message); this.name = name; Object.defineProperty(this, memberNameProperty, {value: member, enumerable: true}); }
      };
      globals.push(name); values.push(Rejection);
    }
    for (const member of namespace.members) Object.defineProperty(object, member, {enumerable: true, value: async (...args) => {
      if (args.length !== 1) throw new TypeError('PTC bindings take one lossless JSON argument.');
      lossless(args);
      if (pending.size >= 32 || sequence >= 256 || finished) throw new Error('PTC binding budget exhausted.');
      const id = ++sequence;
      const promise = new Promise((resolve, reject) => pending.set(id, {resolve, reject, Rejection, member}));
      try { send({type: 'call', id, namespace: namespace.global, member, args}); }
      catch (error) { pending.delete(id); throw error; }
      return promise;
    }});
    globals.push(namespace.global); values.push(Object.freeze(object));
  }
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    if (typeof module.stripTypeScriptTypes !== 'function') throw new Error('PTC requires Node.js 22.13 or newer with TypeScript transformation.');
    const transformed = module.stripTypeScriptTypes('async function __dsh_main__() {\n' + spec.program + '\n}', {mode: 'transform'});
    const program = new AsyncFunction('console', ...globals, '"use strict";\n' + transformed + '\nreturn await __dsh_main__();');
    const value = await program(capturedConsole, ...values);
    try { if (value !== undefined) lossless(value); }
    catch (error) { complete({error: {kind: 'invalid-output', message: error.message}}); return; }
    complete(value === undefined ? {} : {value});
  } catch (error) {
    complete({error: {kind: 'exception', message: String(error?.message ?? error).slice(0, 8192)}});
  }
}

const reader = createInterface({input: process.stdin, crlfDelay: Infinity});
reader.on('line', line => {
  try {
    if (Buffer.byteLength(line) > MAX) throw new Error('PTC input frame too large.');
    const frame = JSON.parse(line);
    if (!started) { started = true; void run(frame); return; }
    const entry = pending.get(frame.id);
    if (!entry) throw new Error('Unknown PTC binding response.');
    pending.delete(frame.id);
    if (frame.error !== undefined) entry.reject(entry.Rejection ? new entry.Rejection(String(frame.error), entry.member) : new Error(String(frame.error)));
    else { lossless(frame.value); entry.resolve(frame.value); }
  } catch (error) { complete({error: {kind: 'protocol', message: String(error.message).slice(0, 8192)}}); }
});
reader.on('close', () => { if (!finished) process.exit(1); });
