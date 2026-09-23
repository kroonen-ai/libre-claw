// Generated from Harness 9291d7f66e87c5b89d4dbe63104734461f4d18a9; see compat/upstream.json and compat/upstream/LICENSE.

// compat/upstream/crypto.ts
function randomUUID() {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  const hex = Array.from(bytes, (byte, index) => {
    const pinned = index === 6 ? byte & 15 | 64 : index === 8 ? byte & 63 | 128 : byte;
    return pinned.toString(16).padStart(2, "0");
  }).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// compat/upstream/brand.ts
function brandString(value) {
  return value;
}

// compat/upstream/values.ts
function assertNever(value, context) {
  const rendered = JSON.stringify(value) ?? String(value);
  throw new Error(`unreachable variant${context ? ` in ${context}` : ""}: ${rendered}`);
}
function deepFreeze(value) {
  const seen = /* @__PURE__ */ new WeakSet();
  const pending = [{ kind: "visit", node: value }];
  while (pending.length > 0) {
    const task = pending.pop();
    if (task === void 0) continue;
    if (task.kind === "property") {
      pending.push({ kind: "visit", node: task.source[task.key] });
      continue;
    }
    const node = task.node;
    if (node === null || typeof node !== "object") continue;
    if (node instanceof AbortSignal) continue;
    if (seen.has(node)) continue;
    seen.add(node);
    Object.freeze(node);
    const keys = Object.keys(node);
    for (let index = keys.length - 1; index >= 0; index--) {
      const key = keys[index];
      if (key === void 0) continue;
      pending.push({ kind: "property", source: node, key });
    }
  }
  return value;
}

// compat/upstream/message.ts
var CONTEXT_SUMMARY_MAX_CHARS = 120;
function boundContextSummary(summary) {
  return summary.length <= CONTEXT_SUMMARY_MAX_CHARS ? summary : `${summary.slice(0, CONTEXT_SUMMARY_MAX_CHARS - 1)}\u2026`;
}
function freezeMessage(message) {
  return deepFreeze(structuredClone(message));
}
function createMessage(input) {
  return freezeMessage({
    ...input,
    id: brandString(randomUUID())
  });
}
function createUserMessage(input) {
  return createMessage({
    ...input,
    role: "user"
  });
}
function createAssistantMessage(input) {
  return createMessage({
    role: "assistant",
    content: input.content,
    source: {
      kind: "model",
      ...input.source
    }
  });
}
function createSystemMessage(text, plugin) {
  return createMessage({
    role: "system",
    content: text.length === 0 ? [] : [{ type: "text", text }],
    source: { kind: "plugin", plugin }
  });
}
function createToolResultMessage(input) {
  return createUserMessage({
    source: { kind: "tool", callId: input.callId },
    content: [{
      type: "tool-result",
      toolCallId: input.callId,
      content: input.content,
      isError: input.isError
    }]
  });
}

// compat/upstream/assembler.ts
var BlockAssembler = class {
  partials = /* @__PURE__ */ new Map();
  order = [];
  _usage;
  _finish;
  _replayState;
  /**
   * Feed one chunk into the assembly state.
   * @param chunk - the next raw chunk, in stream order.
   */
  push(chunk) {
    switch (chunk.type) {
      case "block-start": {
        if (!this.partials.has(chunk.index)) {
          this.order.push(chunk.index);
          this.partials.set(chunk.index, {
            blockType: chunk.blockType,
            text: "",
            toolCallArguments: ""
          });
        }
        return;
      }
      case "text-delta":
      case "reasoning-delta": {
        const partial = this.ensure(chunk.index, chunk.type === "text-delta" ? "text" : "reasoning");
        if (partial.block) return;
        partial.text += chunk.text;
        return;
      }
      case "tool-call-delta": {
        const partial = this.ensure(chunk.index, "tool-call");
        if (partial.block) return;
        partial.toolCallId = chunk.id;
        if (chunk.name) partial.toolCallName = chunk.name;
        partial.toolCallArguments += chunk.argumentsDelta;
        return;
      }
      case "block-end": {
        const partial = this.ensure(chunk.index, chunk.block.type);
        if (partial.block) return;
        partial.block = chunk.block;
        return;
      }
      case "usage": {
        this._usage = chunk.usage;
        return;
      }
      case "finish": {
        this._finish = chunk.reason;
        this._replayState = chunk.replayState;
        return;
      }
      default:
        return assertNever(chunk, "BlockAssembler.push");
    }
  }
  ensure(index, blockType) {
    let partial = this.partials.get(index);
    if (!partial) {
      partial = { blockType, text: "", toolCallArguments: "" };
      this.partials.set(index, partial);
      this.order.push(index);
    }
    return partial;
  }
  assemble(partial, index) {
    if (partial.block) return partial.block;
    switch (partial.blockType) {
      case "text":
        return { type: "text", text: partial.text };
      case "reasoning":
        return { type: "reasoning", text: partial.text };
      case "tool-call":
        return {
          type: "tool-call",
          id: partial.toolCallId ?? brandString(`call-${index}`),
          name: partial.toolCallName ?? "",
          arguments: partial.toolCallArguments
        };
      default:
        throw new Error(`cannot assemble incomplete block of type "${partial.blockType}"`);
    }
  }
  /** Invariant accessor: every index in `order` has a partial. */
  mustGet(index) {
    const partial = this.partials.get(index);
    if (!partial) throw new Error(`BlockAssembler invariant violated: no partial for index ${index}`);
    return partial;
  }
  /**
   * The one shared keep/drop decision over all seen blocks: max-token
   * truncation drops tool calls that cannot be executed safely. Emitted blocks
   * and replay metadata both derive from this result, so they cannot disagree.
   */
  assembled() {
    const all = this.order.map((index) => this.assemble(this.mustGet(index), index));
    const kept = this.finish.kind === "max-tokens" ? all.map((block) => block.type !== "tool-call") : void 0;
    const blocks = kept === void 0 ? all : all.filter((_, position) => kept[position]);
    const envelope = this._replayState;
    if (envelope?.blocks === void 0) return { blocks, replay: envelope };
    if (envelope.blocks.length !== all.length) return { blocks, replay: void 0 };
    return {
      blocks,
      replay: kept === void 0 || blocks.length === all.length ? envelope : { response: envelope.response, blocks: envelope.blocks.filter((_, position) => kept[position]) }
    };
  }
  /**
   * Assemble all blocks seen so far, in stream order.
   * @returns one block per seen index, except that max-token truncation drops
   *   tool calls that cannot be executed safely; an open block assembles from
   *   its accumulated deltas (an unknown block type never closed by `block-end` throws).
   */
  blocks() {
    return this.assembled().blocks;
  }
  /**
   * Assemble the prefix an interrupted stream can safely finalize: closed and
   * open text/reasoning blocks with non-whitespace content, in stream order.
   * Tool calls are omitted because interruption precedes dispatch; retaining
   * one would require a fabricated result. Open unknown blocks are also omitted.
   * @returns the kept blocks; empty when nothing streamed before the interruption.
   */
  interruptedBlocks() {
    return this.order.map((index) => {
      const partial = this.mustGet(index);
      const type = partial.block?.type ?? partial.blockType;
      if (type !== "text" && type !== "reasoning") return void 0;
      return this.assemble(partial, index);
    }).filter((block) => (block?.type === "text" || block?.type === "reasoning") && block.text.trim() !== "");
  }
  /** Usage from the `usage` chunk; undefined until one arrives. */
  get usage() {
    return this._usage;
  }
  /** Finish reason from the `finish` chunk; `{kind: 'stop'}` when the stream ended without one. */
  get finish() {
    return this._finish ?? { kind: "stop" };
  }
  /**
   * Replay metadata from the terminal finish chunk, if any, with per-block
   * entries pruned in step with {@link blocks}. Undefined when the envelope's
   * entries do not align with the emitted blocks.
   */
  get replayState() {
    return this.assembled().replay;
  }
  /**
   * The assembled assistant message.
   * @param source - producer attribution for the assembled message.
   * @returns a frozen assistant-role message over `blocks()` (same open-block assembly rules).
   */
  message(source = { kind: "plugin", plugin: "dsh-llm/assembler" }) {
    return createMessage({ role: "assistant", content: this.blocks(), source });
  }
};
export {
  BlockAssembler,
  CONTEXT_SUMMARY_MAX_CHARS,
  boundContextSummary,
  createAssistantMessage,
  createMessage,
  createSystemMessage,
  createToolResultMessage,
  createUserMessage,
  freezeMessage
};
