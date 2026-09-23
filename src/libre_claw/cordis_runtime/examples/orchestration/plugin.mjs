// Copyright 2026 Kroonen AI (https://kroonen.ai)
// SPDX-License-Identifier: Apache-2.0
import manifest from './libre-claw-plugin.json' with { type: 'json' };

const MAX_REPORT_BYTES = 256 * 1024;

function report(value) {
  const content = JSON.stringify(value);
  if (content === undefined || Buffer.byteLength(content) > MAX_REPORT_BYTES) {
    throw new Error('The worker report exceeds its bounded output budget.');
  }
  return { content };
}

export default {
  name: 'orchestration',
  inject: ['libre'],
  apply(ctx) {
    // Activation only registers tools. Model routing and execution remain in
    // the explicitly selected task's Python controller, behind host grants.
    const handlers = {
      delegate: args => ctx.libre.orchestration.dispatch(args.tasks),
      wait: args => ctx.libre.orchestration.wait(args.ids, args.timeout ?? 0),
      status: () => ctx.libre.orchestration.status(),
      cancel: args => ctx.libre.orchestration.cancel(args.ids),
    };
    for (const definition of manifest.tools) {
      ctx.libre.registerTool(definition, async args => report(await handlers[definition.name](args)));
    }
  },
};
