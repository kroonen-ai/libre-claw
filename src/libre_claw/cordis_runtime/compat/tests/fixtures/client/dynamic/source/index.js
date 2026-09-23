// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
import {createElement, useState} from 'react';

const loadPanel = () => import('./panel.js');
function Shell() {
  const [Panel, setPanel] = useState(null);
  return createElement('section', null,
    createElement('button', {onClick: async () => {
      const [first, second] = await Promise.all([loadPanel(), loadPanel()]);
      document.documentElement.dataset.sameChunk = String(first === second);
      setPanel(() => first.Panel);
    }}, 'Load panel'),
    Panel ? createElement(Panel) : createElement('span', null, 'Waiting for chunk'));
}

export default {
  inject: ['slots'],
  apply(ctx) {
    ctx.slots.register({name: 'shell.overlay', id: 'dynamic-fixture'}, Shell);
  },
};
