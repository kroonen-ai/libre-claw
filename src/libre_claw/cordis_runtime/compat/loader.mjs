// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { builtinModules, registerHooks } from 'node:module';
import { readFileSync, realpathSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const aliases = new Map([
  ['@deepseek-ai/cordis', new URL('../vendor/cordis.mjs', import.meta.url).href],
  ['@deepseek-ai/cosmokit', new URL('../vendor/cosmokit.mjs', import.meta.url).href],
  ['@deepseek-ai/schemastery', new URL('../vendor/schemastery.mjs', import.meta.url).href],
  ['zod', new URL('../vendor/zod.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-tools', new URL('./tools.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-tools/types', new URL('./tools.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-tools/presentation', new URL('./tools.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-user-questions', new URL('./services.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-session-projection', new URL('./services.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-llm', new URL('./services.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-llm/types', new URL('./services.mjs', import.meta.url).href],
  ['@deepseek-ai/dsh-util-values', new URL('../vendor/harness-values.mjs', import.meta.url).href],
  ['diff', new URL('../vendor/diff.mjs', import.meta.url).href],
]);
for (const name of ['scope', 'system-prompt', 'agent', 'jobs', 'jobs-local', 'fs', 'shell',
  'sandbox', 'attachment', 'lsp', 'timeout', 'output-retention', 'subprocess', 'http-proxy', 'ptc-runtime']) {
  aliases.set(`@deepseek-ai/dsh-${name}`, new URL(`./upstream/services/${name}.mjs`, import.meta.url).href);
}

function contains(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function packageName(specifier) {
  return specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0];
}

function condition(value, conditions) {
  if (value === null) return null;
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    for (const entry of value) {
      const result = condition(entry, conditions);
      if (typeof result === 'string') return result;
    }
    return null;
  }
  if (value && typeof value === 'object') {
    // Node chooses the first matching condition in package declaration order.
    // The caller's actual resolver conditions distinguish import from require.
    for (const [key, target] of Object.entries(value)) {
      if (key !== 'default' && !conditions.has(key)) continue;
      const result = condition(target, conditions);
      if (result !== undefined) return result;
    }
  }
  return undefined;
}

/** Package resolution is limited to the installed, reviewed package graph. */
export function installHarnessResolver(entry, graph = []) {
  const resolvedEntry = realpathSync(entry);
  const directory = path.dirname(resolvedEntry);
  const records = graph.map(record => {
    const location = path.resolve(directory, record.path);
    if (!contains(directory, location)) throw new Error('Harness package path escapes its installed snapshot.');
    return { ...record, location };
  }).sort((left, right) => right.location.length - left.location.length);
  const byPath = new Map(records.map(record => [record.path, record]));
  const resolve = (specifier, context, nextResolve) => {
    if (aliases.has(specifier)) return { url: aliases.get(specifier), shortCircuit: true };
    if (builtinModules.includes(specifier) || specifier.startsWith('.') || specifier.startsWith('/') || /^[a-zA-Z][a-zA-Z+.-]*:/.test(specifier)) return nextResolve(specifier, context);
    const parent = context.parentURL?.startsWith('file:') ? fileURLToPath(context.parentURL) : undefined;
    const owner = parent && records.find(record => contains(record.location, parent));
    if (!owner) return nextResolve(specifier, context);
    const name = packageName(specifier);
    const dependency = owner.dependencies && Object.hasOwn(owner.dependencies, name) ? owner.dependencies[name] : undefined;
    const record = name === owner.name ? owner : byPath.get(dependency);
    if (!record) throw new Error('The Harness package imports a dependency absent from its reviewed package graph.');
    const manifest = JSON.parse(readFileSync(path.join(record.location, 'package.json'), 'utf8'));
    const subpath = specifier.slice(name.length);
    const conditions = new Set(context.conditions ?? ['node', 'import']);
    let relative;
    if (manifest.exports !== undefined) {
      const key = subpath ? `.${subpath}` : '.';
      const exports = manifest.exports;
      const mapping = typeof exports === 'object' && exports !== null && !Array.isArray(exports)
        && Object.keys(exports).some(item => item.startsWith('.'));
      if (mapping && Object.keys(exports).some(item => !item.startsWith('.'))) throw new Error('Invalid mixed Harness package exports.');
      if (mapping && Object.hasOwn(exports, key)) relative = condition(exports[key], conditions);
      else if (!mapping && key === '.') relative = condition(exports, conditions);
      else if (mapping) {
        const patterns = Object.keys(exports).filter(pattern => pattern.includes('*')).sort((left, right) =>
          right.indexOf('*') - left.indexOf('*') || right.length - left.length);
        for (const pattern of patterns) {
          const [prefix, suffix] = pattern.split('*');
          if (key.startsWith(prefix) && key.endsWith(suffix)) {
            const replacement = key.slice(prefix.length, suffix ? -suffix.length : undefined);
            relative = condition(exports[pattern], conditions)?.replaceAll('*', replacement);
            break;
          }
        }
      }
      if (!relative || !relative.startsWith('./')) throw new Error('The Harness dependency does not export this module.');
    } else relative = subpath ? `.${subpath}` : manifest.main ?? './index.js';
    const target = path.resolve(record.location, relative);
    if (!contains(record.location, target)) throw new Error('A Harness package export escapes its installed directory.');
    return nextResolve(conditions.has('require') ? target : pathToFileURL(target).href, context);
  };
  const hook = registerHooks({ resolve });
  return {
    deregister: () => hook.deregister(),
    component: specifier => resolve(specifier,
      { parentURL: pathToFileURL(resolvedEntry).href }, url => ({ url })).url,
  };
}

export function providedHarnessImports() { return [...aliases.keys()]; }
