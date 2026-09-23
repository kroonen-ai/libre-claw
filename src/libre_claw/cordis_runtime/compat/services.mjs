// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Pure package imports; actual host services are installed explicitly by the
// approved runtime. Importing this module neither mounts services nor does I/O.
import { HarnessError } from './tools.mjs';
export { HarnessError } from './tools.mjs';
export * from '../vendor/harness-messages.mjs';

export class UserQuestionError extends HarnessError {}
export class LlmError extends HarnessError {
  constructor(message, code, options) {
    super(message, code, options);
    this.failure = Object.freeze({ message, code,
      ...(options?.status === undefined ? {} : { status: options.status }),
      ...(options?.requestId === undefined ? {} : { requestId: options.requestId }),
    });
  }
}
