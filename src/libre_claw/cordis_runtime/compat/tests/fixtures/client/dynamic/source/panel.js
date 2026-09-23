// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import {createElement} from 'react';
const counters = document.documentElement.dataset;
counters.chunkMounts = String(Number(counters.chunkMounts ?? 0) + 1);
export function Panel() {
  return createElement('strong', null, 'Compiled lazy panel');
}
