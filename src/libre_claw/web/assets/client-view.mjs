// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Trusted renderer for offline guest output. No plugin script or HTML executes.

export class ClientView {
  #container;
  #schema;
  #request;
  #bind;
  #timeout;
  #status;
  #base;
  #snapshot;
  #nodes = new Map();
  #views = new Map();
  #drafts = new Map();
  #sequence = 0;
  #queue = [];
  #sending = false;
  #closed = false;
  #poll;
  #disposers = new Set();
  #namespace;

  constructor({container, schema, pluginId, sessionId, request, bind, timeout, status = () => {}}) {
    this.#container = container; this.#schema = schema; this.#request = request; this.#bind = bind;
    this.#timeout = timeout; this.#status = status;
    this.#base = `/plugins/${encodeURIComponent(pluginId)}/ui/${encodeURIComponent(sessionId)}`;
    this.#namespace = `client-${sessionId}-`;
    container.setAttribute('aria-busy', 'false');
  }

  #validate(snapshot) {
    if (!snapshot || snapshot.status !== 'active' || !Number.isSafeInteger(snapshot.revision) || snapshot.revision < 0
        || !Array.isArray(snapshot.views) || snapshot.views.length > this.#schema.limits.views
        || new TextEncoder().encode(JSON.stringify(snapshot)).length > this.#schema.limits.snapshot_bytes) throw new Error('Invalid isolated client snapshot.');
    const identities = new Set(), viewIds = new Set(); let count = 0;
    const walk = (node, depth) => {
      if (!node || typeof node.id !== 'string' || node.id.length > 128 || identities.has(node.id)
          || ++count > this.#schema.limits.nodes || depth > this.#schema.limits.depth
          || !Array.isArray(node.children) || !node.props || !node.events) throw new Error('Invalid client element.');
      identities.add(node.id);
      if (node.tag === '#text') {
        if (typeof node.text !== 'string' || node.children.length || Object.keys(node.props).length || Object.keys(node.events).length) throw new Error('Invalid client text.');
      } else if (!this.#schema.tags.includes(node.tag)) throw new Error('Unsupported client element.');
      for (const [name, value] of Object.entries(node.props)) {
        if (!this.#schema.props.includes(name) && !new RegExp(this.#schema.data_attribute_pattern).test(name)) throw new Error('Unsupported client attribute.');
        if (name === 'style') {
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid client styling.');
          for (const [property, entry] of Object.entries(value)) {
            if (!this.#schema.styles.includes(property) || !['string', 'number'].includes(typeof entry)
                || typeof entry === 'number' && !Number.isFinite(entry) || String(entry).length > 128
                || /url\s*\(|expression\s*\(|@import/i.test(String(entry))) throw new Error('Unsupported client styling.');
          }
        } else if (!['string', 'number', 'boolean'].includes(typeof value) || typeof value === 'number' && !Number.isFinite(value)) throw new Error('Invalid client attribute.');
        if (name === 'type' && !this.#schema.input_types.includes(value)) throw new Error('Unsupported input type.');
      }
      for (const [event, id] of Object.entries(node.events)) {
        if (!this.#schema.events.includes(event) || typeof id !== 'string' || id.length > 128) throw new Error('Unsupported client event.');
      }
      for (const child of node.children) walk(child, depth + 1);
    };
    for (const view of snapshot.views) {
      if (!view || typeof view.id !== 'string' || viewIds.has(view.id) || !this.#schema.slots.includes(view.slot)
          || !['panel', 'summary', 'page'].includes(view.mode) || !Array.isArray(view.tree)) throw new Error('Invalid client view.');
      viewIds.add(view.id); for (const node of view.tree) walk(node, 0);
    }
  }

  #listen(target, event, handler, record) {
    const dispose = this.#bind(target, event, handler);
    if (typeof dispose === 'function') { this.#disposers.add(dispose); record.disposers.push(dispose); }
  }

  #drop(record) {
    for (const dispose of record.disposers) { dispose(); this.#disposers.delete(dispose); }
    record.element.remove();
  }

  #reconcile(parent, wanted) {
    for (let index = 0; index < wanted.length; index++) {
      if (parent.childNodes[index] !== wanted[index]) parent.insertBefore(wanted[index], parent.childNodes[index] || null);
    }
    while (parent.childNodes.length > wanted.length) parent.lastChild.remove();
  }

  #values(target, view) {
    const scope = target.closest?.('form') || view;
    const values = Object.create(null);
    for (const element of scope.querySelectorAll('input,textarea,select')) {
      if (!element.dataset.clientNode) continue;
      values[element.dataset.clientNode] = {value: String(element.value), ...(['checkbox', 'radio'].includes(element.type) ? {checked: element.checked} : {})};
    }
    return values;
  }

  #enqueue(record, type, view, event) {
    if (this.#closed || !this.#container.isConnected) return;
    if (type === 'submit') event.preventDefault();
    const id = record.spec.events[type];
    if (!id) return;
    const target = event.target?.closest?.('[data-client-node]');
    const action = {event_id: id, target_id: target?.dataset.clientNode || record.spec.id, values: this.#values(record.element, view), sequence: this.#sequence};
    if (new TextEncoder().encode(JSON.stringify(action)).length > this.#schema.limits.event_bytes) {
      this.#status('This form exceeds the isolated interface input limit.', true); return;
    }
    const previous = this.#queue.at(-1);
    if (['input', 'change'].includes(type) && previous?.event_id === id) this.#queue[this.#queue.length - 1] = action;
    else if (this.#queue.length < 64) this.#queue.push(action);
    else { this.#status('Wait for the current interface actions to finish.', true); return; }
    void this.#drain();
  }

  async #drain() {
    if (this.#sending || this.#closed) return;
    this.#sending = true; this.#container.setAttribute('aria-busy', 'true');
    try {
      while (this.#queue.length && !this.#closed) {
        const {sequence, ...action} = this.#queue.shift();
        const reply = await this.#request(`${this.#base}/events`, {method: 'POST', body: JSON.stringify({...action, revision: this.#snapshot.revision})});
        if (this.#closed) return;
        for (const id of Object.keys(action.values)) {
          const draft = this.#drafts.get(id);
          if (draft && draft.sequence <= sequence) this.#drafts.delete(id);
        }
        this.render(reply.snapshot);
      }
      if (!this.#closed) this.#status('Interface updated.');
    } catch (error) {
      this.#queue = [];
      if (!this.#closed) this.#status(`Interface action failed: ${error.message || error}`, true);
    } finally { this.#sending = false; if (!this.#closed) this.#container.setAttribute('aria-busy', 'false'); }
  }

  #element(spec, view, retained) {
    retained.add(spec.id);
    let record = this.#nodes.get(spec.id);
    if (record && record.spec.tag !== spec.tag) { this.#drop(record); this.#nodes.delete(spec.id); record = null; }
    if (!record) {
      const element = spec.tag === '#text' ? document.createTextNode('') : document.createElement(spec.tag);
      record = {element, spec, previousProps: {}, disposers: []}; this.#nodes.set(spec.id, record);
      if (spec.tag !== '#text') {
        element.dataset.clientNode = spec.id;
        if (['input', 'textarea', 'select'].includes(spec.tag)) {
          this.#listen(element, 'input', event => {
            if (['checkbox', 'radio'].includes(element.type) && !record.spec.events.input && !record.spec.events.change && record.spec.events.click) return;
            this.#drafts.set(spec.id, {sequence: ++this.#sequence});
            const type = record.spec.events.input ? 'input' : record.spec.events.change ? 'change' : null;
            if (type && (type === 'input' || !['checkbox', 'radio'].includes(element.type) && element.tagName !== 'SELECT')) this.#enqueue(record, type, view, event);
          }, record);
          this.#listen(element, 'change', event => {
            if (!['checkbox', 'radio'].includes(element.type) && element.tagName !== 'SELECT') return;
            if (!record.spec.events.change && record.spec.events.click) return;
            this.#drafts.set(spec.id, {sequence: ++this.#sequence});
            this.#enqueue(record, 'change', view, event);
          }, record);
        }
        this.#listen(element, 'click', event => {
          if (['checkbox', 'radio'].includes(element.type) && record.spec.events.click) this.#drafts.set(spec.id, {sequence: ++this.#sequence});
          this.#enqueue(record, 'click', view, event);
        }, record);
        if (spec.tag === 'form') this.#listen(element, 'submit', event => this.#enqueue(record, 'submit', view, event), record);
      }
    }
    record.spec = spec;
    if (spec.tag === '#text') { record.element.textContent = spec.text; return record.element; }
    const element = record.element, props = spec.props;
    for (const name of Object.keys(record.previousProps)) {
      if (name in props) continue;
      if (name === 'style') element.removeAttribute('style');
      else if (['checked', 'disabled', 'readOnly', 'required', 'multiple', 'selected', 'open'].includes(name)) element[name] = false;
      else if (name === 'value') { if (!this.#drafts.has(spec.id)) element.value = ''; }
      else element.removeAttribute(name === 'htmlFor' ? 'for' : name);
    }
    for (const [name, value] of Object.entries(props)) {
      if (name.startsWith('data-client-')) continue;
      if (name === 'value' || name === 'checked') continue;
      if (name === 'style') {
        element.removeAttribute('style');
        for (const [property, entry] of Object.entries(value)) element.style[property] = typeof entry === 'number' && property !== 'fontWeight' ? `${entry}px` : String(entry);
      } else if (name === 'id' || name === 'htmlFor') element.setAttribute(name === 'htmlFor' ? 'for' : name, this.#namespace + String(value));
      else if (name === 'aria-labelledby' || name === 'aria-describedby') element.setAttribute(name, String(value).split(/\s+/).filter(Boolean).map(id => this.#namespace + id).join(' '));
      else if (['disabled', 'readOnly', 'required', 'multiple', 'selected', 'open'].includes(name)) element[name] = Boolean(value);
      else element.setAttribute(name === 'htmlFor' ? 'for' : name, String(value));
    }
    this.#reconcile(element, spec.children.map(child => this.#element(child, view, retained)));
    if (!this.#drafts.has(spec.id)) {
      if ('value' in props && element.value !== String(props.value)) element.value = String(props.value);
      if ('checked' in props) element.checked = props.checked === true;
    }
    record.previousProps = props;
    return element;
  }

  render(snapshot) {
    if (this.#closed) return;
    this.#validate(snapshot);
    if (this.#snapshot && snapshot.revision < this.#snapshot.revision) return;
    this.#snapshot = snapshot;
    const retained = new Set(), active = document.activeElement;
    const selection = active && ['INPUT', 'TEXTAREA'].includes(active.tagName) ? [active.selectionStart, active.selectionEnd] : null;
    const children = [];
    for (const view of snapshot.views) {
      let container = this.#views.get(view.id);
      if (!container) { container = document.createElement('section'); container.className = `client-rendered-view ${view.mode}`; this.#views.set(view.id, container); }
      container.setAttribute('aria-label', view.mode === 'panel' ? 'Plugin panel' : `${view.key || 'Plugin'} ${view.mode}`);
      this.#reconcile(container, view.tree.map(node => this.#element(node, container, retained))); children.push(container);
    }
    this.#reconcile(this.#container, children);
    for (const [id, record] of this.#nodes) if (!retained.has(id)) { this.#drop(record); this.#nodes.delete(id); this.#drafts.delete(id); }
    const views = new Set(snapshot.views.map(view => view.id));
    for (const id of this.#views.keys()) if (!views.has(id)) this.#views.delete(id);
    if (active?.isConnected && this.#container.contains(active) && document.activeElement !== active) {
      active.focus({preventScroll: true});
      if (selection && selection[0] !== null && typeof active.setSelectionRange === 'function') active.setSelectionRange(...selection);
    }
  }

  startPolling() {
    if (this.#closed || this.#poll) return;
    this.#poll = this.#timeout(async () => {
      this.#poll = null;
      if (this.#closed || !this.#container.isConnected) return;
      try {
        if (!this.#sending) {
          const reply = await this.#request(this.#base);
          if (!this.#closed && !this.#sending && reply.snapshot.revision > this.#snapshot.revision) this.render(reply.snapshot);
        }
      } catch (error) {
        if (!this.#closed) {
          this.#status(`Interface unavailable: ${error.message || error}. Open it again to retry.`, true);
          try { await this.close(); } catch { /* The server also expires disconnected guests. */ }
        }
        return;
      }
      this.startPolling();
    }, 1000, this.#container);
  }

  async close() {
    if (this.#closed) return;
    this.#closed = true; this.#queue = [];
    this.#container.setAttribute('aria-busy', 'false');
    this.#poll?.(); this.#poll = null;
    for (const dispose of this.#disposers) dispose(); this.#disposers.clear();
    this.#nodes.clear(); this.#views.clear(); this.#drafts.clear(); this.#container.replaceChildren();
    await this.#request(this.#base, {method: 'DELETE'});
  }
}
