// Copyright 2026 Kroonen AI (https://kroonen.ai)
// SPDX-License-Identifier: Apache-2.0

export default {
  name: 'text-utilities',
  inject: ['libre'],
  apply(ctx, config) {
    ctx.libre.registerTool({
      name: 'count_text',
      description: 'Count words and lines in supplied text, with an optional Unicode character count. Runs entirely locally.',
      input_schema: {
        type: 'object',
        properties: { text: { type: 'string', description: 'Text to count.' } },
        required: ['text'],
        additionalProperties: false,
      },
    }, ({ text }) => {
      const counts = {
        words: text.match(/\S+/gu)?.length ?? 0,
        lines: text.length === 0 ? 0 : text.split(/\r\n|\r|\n/u).length,
      };
      if (config.include_characters) counts.characters = Array.from(text).length;
      return { content: JSON.stringify(counts) };
    });
  },
};
