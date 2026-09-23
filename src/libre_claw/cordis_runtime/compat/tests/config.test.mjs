// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import z from '../../vendor/schemastery.mjs';
import { harnessConfigSchema } from '../config.mjs';

test('real Schemastery Config exposes editable primitive, required, array, and enum fields', () => {
  const schema = harnessConfigSchema(z.object({
    enabled: z.boolean().required(),
    name: z.string().description('Display name').default('Local'),
    mode: z.union(['fast', 'complete']).default('fast'),
    count: z.number().step(1).min(1).max(5).default(2),
    labels: z.array(z.string()),
    nested: z.object({ active: z.boolean().default(false) }),
  })).config_schema;
  assert.deepEqual(schema.required, ['enabled']);
  assert.deepEqual(schema.properties.name, { type: 'string', description: 'Display name', default: 'Local' });
  assert.deepEqual(schema.properties.mode, { type: 'string', enum: ['fast', 'complete'], default: 'fast' });
  assert.deepEqual(schema.properties.count, { type: 'integer', default: 2, minimum: 1, maximum: 5 });
  assert.deepEqual(schema.properties.labels, { type: 'array', items: { type: 'string' } });
  assert.equal(schema.properties.nested.properties.active.default, false);
});

test('secret defaults and nested container defaults never appear in public metadata', () => {
  const config = z.object({
    apiKey: z.string().default('private-key'),
    phrase: z.string().role('secret').default('private-passphrase'),
    password: z.string().default('private-password'),
    credentials: z.object({ value: z.string().default('private-nested') }).default({ value: 'private-container' }),
    nested: z.object({ visible: z.string().default('public'), access_token: z.string().default('private-token') }),
  }).default({ apiKey: 'private-root' });
  const result = harnessConfigSchema(config);
  assert.equal(JSON.stringify(result).includes('private-'), false);
  assert.equal(result.config_schema.properties.apiKey.format, 'password');
  assert.equal(result.config_schema.properties.phrase.writeOnly, true);
  assert.equal(result.config_schema.properties.credentials.properties.value.format, 'password');
  assert.equal(result.config_schema.properties.nested.properties.visible.default, 'public');
});

test('unsupported fields are reported without inventing validation', () => {
  const result = harnessConfigSchema(z.object({ choice: z.union([z.string(), z.number()]), callback: z.function(), allowed: z.boolean() }));
  assert.deepEqual(result.config_schema_warnings, ['/choice', '/callback']);
  assert.deepEqual(Object.keys(result.config_schema.properties), ['allowed']);
});
