// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Public metadata only; Schemastery remains authoritative for actual validation.
import { snapshotJsonValue } from '../vendor/harness-tools.mjs';

const SECRET = /password|secret|(?:access|auth|refresh|bearer)[_-]?token|^token$|(?:api|private)[_-]?key|credential|authorization/i;

export function harnessConfigSchema(config) {
  const seen = new Set();
  const warnings = [];
  let remaining = 512;
  const visit = (node, key = '', depth = 0, inheritedSecret = false) => {
    if (!node || typeof node !== 'function' || typeof node.type !== 'string'
      || depth > 16 || --remaining < 0 || seen.has(node)) {
      warnings.push(key || '/'); return undefined;
    }
    seen.add(node);
    try {
      const meta = node.meta ?? {};
      const secret = inheritedSecret || SECRET.test(key.split('/').at(-1)) || ['secret', 'password'].includes(meta.role);
      let schema;
      if (['string', 'number', 'boolean'].includes(node.type)) {
        schema = { type: node.type === 'number' && meta.step === 1 ? 'integer' : node.type };
      } else if (node.type === 'const') {
        const kind = node.value === null ? 'null' : typeof node.value;
        if (!['null', 'string', 'number', 'boolean'].includes(kind)) { warnings.push(key || '/'); return undefined; }
        schema = { type: kind, ...(secret ? {} : { const: node.value }) };
      } else if (node.type === 'union' && node.list?.every(item => item.type === 'const')) {
        const values = node.list.map(item => item.value);
        const kind = values[0] === null ? 'null' : typeof values[0];
        if (!values.length || !['null', 'string', 'number', 'boolean'].includes(kind)
          || values.some(value => (value === null ? 'null' : typeof value) !== kind)) {
          warnings.push(key || '/'); return undefined;
        }
        schema = { type: kind, ...(secret ? {} : { enum: values }) };
      } else if (node.type === 'object') {
        const properties = {};
        const required = [];
        for (const [name, child] of Object.entries(node.dict ?? {})) {
          if (name.length > 160) { warnings.push(key || '/'); continue; }
          const result = visit(child, `${key}/${name}`, depth + 1, secret);
          if (!result) continue;
          Object.defineProperty(properties, name, { value: result, enumerable: true });
          if (child.meta?.required) required.push(name);
        }
        schema = { type: 'object', properties, additionalProperties: true, ...(required.length ? { required } : {}) };
      } else if (node.type === 'array' || node.type === 'dict') {
        const inner = visit(node.inner, `${key}/*`, depth + 1, secret);
        if (!inner) return undefined;
        schema = node.type === 'array' ? { type: 'array', items: inner } : { type: 'object', additionalProperties: inner };
      } else if (node.type === 'intersect') {
        const children = node.list?.map((item, index) => visit(item, `${key}/${index}`, depth + 1, secret));
        if (!children?.length || children.some(item => item?.type !== 'object')) { warnings.push(key || '/'); return undefined; }
        schema = { type: 'object', properties: Object.fromEntries(children.flatMap(item => Object.entries(item.properties ?? {}))), additionalProperties: true };
        const required = [...new Set(children.flatMap(item => item.required ?? []))];
        if (required.length) schema.required = required;
      } else { warnings.push(key || '/'); return undefined; }
      for (const annotation of ['description', 'title']) {
        if (typeof meta[annotation] === 'string') schema[annotation] = meta[annotation].slice(0, 4000);
      }
      if (secret) {
        schema.writeOnly = true;
        if (schema.type === 'string') schema.format = 'password';
      } else if (!['object', 'array'].includes(schema.type) && Object.hasOwn(meta, 'default')) {
        const value = snapshotJsonValue(meta.default);
        if (value !== undefined && Buffer.byteLength(JSON.stringify(value)) < 4096) schema.default = value;
      }
      for (const [from, scalar, text, array] of [['min', 'minimum', 'minLength', 'minItems'], ['max', 'maximum', 'maxLength', 'maxItems']]) {
        if (!Number.isFinite(meta[from])) continue;
        if (['number', 'integer'].includes(schema.type)) schema[scalar] = meta[from];
        if (schema.type === 'string' && Number.isInteger(meta[from]) && meta[from] >= 0) schema[text] = meta[from];
        if (schema.type === 'array' && Number.isInteger(meta[from]) && meta[from] >= 0) schema[array] = meta[from];
      }
      return schema;
    } finally { seen.delete(node); }
  };
  if (config === undefined) return {};
  const schema = visit(config);
  if (schema && Buffer.byteLength(JSON.stringify(schema)) > 32 * 1024) {
    return { config_schema_warnings: ['/'] };
  }
  return {
    ...(schema ? { config_schema: schema } : {}),
    ...(warnings.length ? { config_schema_warnings: [...new Set(warnings)] } : {}),
  };
}
