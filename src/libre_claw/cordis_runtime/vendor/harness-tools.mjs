// Generated from Harness 9291d7f66e87c5b89d4dbe63104734461f4d18a9; see compat/upstream.json and compat/upstream/LICENSE.

// compat/upstream/error.ts
var HarnessError = class extends Error {
  /** Stable machine-routable failure class (e.g. `RATE_LIMIT`); route on this, never by parsing `message`. */
  code;
  constructor(message, code, options) {
    super(message, options);
    this.code = code;
    this.name = new.target.name;
  }
};
var STRUCTURED_CONTEXT_OVERFLOW = new RegExp(
  String.raw`(?:^|[^a-z0-9])context[\s_-](?:length|window)[\s_-]` + String.raw`(?:exceed(?:ed|s)?|overflow(?:ed)?|limit[\s_-]exceeded)(?:$|[^a-z0-9])`,
  "i"
);
var TOO_LARGE_FOR_CONTEXT = new RegExp(
  String.raw`\b(?:request|prompt|input|messages?)\s+(?:is\s+|are\s+)?` + String.raw`too\s+(?:large|long)\s+for\s+(?:(?:this|the)\s+)?` + String.raw`(?:model(?:'s)?\s+)?context(?:\s+window)?\b`,
  "i"
);
var EXCEEDS_MODEL_CONTEXT = new RegExp(
  String.raw`\b(?:input|prompt|request|messages?)\b.{0,40}` + String.raw`\b(?:exceed(?:s|ed)?|overflows?|is\s+larger\s+than)\b.{0,40}` + String.raw`\b(?:the\s+)?(?:model(?:'s)?\s+)?context(?:\s+(?:length|window))?\b`,
  "i"
);

// compat/upstream/values.ts
function assertNever(value, context) {
  const rendered = JSON.stringify(value) ?? String(value);
  throw new Error(`unreachable variant${context ? ` in ${context}` : ""}: ${rendered}`);
}
function hasIntrinsicConstructor(prototype, name) {
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "constructor");
  const constructor = descriptor?.value;
  if (typeof constructor !== "function") return false;
  try {
    return constructor.name === name && constructor.prototype === prototype && Function.prototype.toString.call(constructor) === `function ${name}() { [native code] }`;
  } catch {
    return false;
  }
}
function isIntrinsicObjectPrototype(value) {
  return Object.getPrototypeOf(value) === null && hasIntrinsicConstructor(value, "Object");
}
function hasPlainArrayPrototype(value) {
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(prototype) || !hasIntrinsicConstructor(prototype, "Array")) return false;
  const objectPrototype = Object.getPrototypeOf(prototype);
  return typeof objectPrototype === "object" && objectPrototype !== null && isIntrinsicObjectPrototype(objectPrototype);
}
function hasPlainObjectPrototype(value) {
  const prototype = Object.getPrototypeOf(value);
  return prototype === null || typeof prototype === "object" && isIntrinsicObjectPrototype(prototype);
}
function enumerableStringKeys(value) {
  const keys = Reflect.ownKeys(value);
  if (keys.some((key) => typeof key !== "string" || !Object.prototype.propertyIsEnumerable.call(value, key))) return void 0;
  return keys;
}
function walkJsonValue(value, detach) {
  const ancestors = /* @__PURE__ */ new Set();
  let root;
  const assign = (destination, item) => {
    if (destination === void 0) return;
    if (destination.kind === "root") {
      root = item;
    } else if (destination.kind === "array") {
      destination.target[destination.index] = item;
    } else {
      Object.defineProperty(destination.target, destination.key, {
        value: item,
        enumerable: true,
        configurable: true,
        writable: true
      });
    }
  };
  const tasks = [{
    kind: "visit",
    value,
    ...detach ? { destination: { kind: "root" } } : {}
  }];
  for (let task = tasks.pop(); task !== void 0; task = tasks.pop()) {
    if (task.kind === "leave") {
      ancestors.delete(task.source);
      continue;
    }
    if (task.kind === "array-item") {
      if (!Object.prototype.hasOwnProperty.call(task.source, task.index)) return void 0;
      tasks.push({
        kind: "visit",
        value: task.source[task.index],
        ...task.target === void 0 ? {} : { destination: { kind: "array", target: task.target, index: task.index } }
      });
      continue;
    }
    if (task.kind === "object-property") {
      tasks.push({
        kind: "visit",
        value: task.source[task.key],
        ...task.target === void 0 ? {} : { destination: { kind: "object", target: task.target, key: task.key } }
      });
      continue;
    }
    const current = task.value;
    if (current === null) {
      assign(task.destination, null);
      continue;
    }
    if (typeof current === "boolean" || typeof current === "string") {
      assign(task.destination, current);
      continue;
    }
    if (typeof current === "number") {
      if (!Number.isFinite(current) || Object.is(current, -0)) return void 0;
      assign(task.destination, current);
      continue;
    }
    if (typeof current !== "object") return void 0;
    if (ancestors.has(current)) return void 0;
    if (Array.isArray(current)) {
      if (!hasPlainArrayPrototype(current)) return void 0;
      const length = current.length;
      if (Reflect.ownKeys(current).length !== length + 1) return void 0;
      const target2 = detach ? [] : void 0;
      if (target2 !== void 0) assign(task.destination, target2);
      ancestors.add(current);
      tasks.push({ kind: "leave", source: current });
      for (let index = length - 1; index >= 0; index--) {
        tasks.push({ kind: "array-item", source: current, index, ...target2 === void 0 ? {} : { target: target2 } });
      }
      continue;
    }
    if (!hasPlainObjectPrototype(current)) return void 0;
    const keys = enumerableStringKeys(current);
    if (keys === void 0) return void 0;
    const target = detach ? {} : void 0;
    if (target !== void 0) assign(task.destination, target);
    ancestors.add(current);
    tasks.push({ kind: "leave", source: current });
    for (let index = keys.length - 1; index >= 0; index--) {
      const key = keys[index];
      if (key === void 0) return void 0;
      tasks.push({ kind: "object-property", source: current, key, ...target === void 0 ? {} : { target } });
    }
  }
  return detach ? root : true;
}
function snapshotJsonValue(value) {
  return walkJsonValue(value, true);
}
function isJsonValue(value) {
  return walkJsonValue(value, false) === true;
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

// compat/upstream/json-schema.ts
var JsonSchemaError = class extends HarnessError {
  /** Individual schema violations in walk order. */
  violations;
  constructor(violations) {
    super(`unsupported JSON schema: ${violations.join("; ")}`, "UNSUPPORTED_SCHEMA");
    this.name = "JsonSchemaError";
    this.violations = violations;
  }
};
var CONSTRAINT_KEYWORDS = /* @__PURE__ */ new Set([
  "type",
  "oneOf",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "enum",
  "const"
]);
var ANNOTATION_KEYWORDS = /* @__PURE__ */ new Set(["description", "title", "default", "examples"]);
var SCHEMA_TYPES = ["object", "array", "string", "number", "integer", "boolean", "null"];
function hasIntrinsicConstructor2(prototype, name) {
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "constructor");
  const constructor = descriptor?.value;
  if (typeof constructor !== "function") return false;
  try {
    return constructor.name === name && constructor.prototype === prototype && Function.prototype.toString.call(constructor) === `function ${name}() { [native code] }`;
  } catch {
    return false;
  }
}
function isIntrinsicObjectPrototype2(value) {
  return Object.getPrototypeOf(value) === null && hasIntrinsicConstructor2(value, "Object");
}
function isPlainJsonRecord(value) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  try {
    const prototype = Object.getPrototypeOf(value);
    return prototype === null || typeof prototype === "object" && isIntrinsicObjectPrototype2(prototype);
  } catch {
    return false;
  }
}
function hasPlainArrayPrototype2(value) {
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(prototype) || !hasIntrinsicConstructor2(prototype, "Array")) return false;
  const objectPrototype = Object.getPrototypeOf(prototype);
  return typeof objectPrototype === "object" && objectPrototype !== null && isIntrinsicObjectPrototype2(objectPrototype);
}
function hasOnlyEnumerableStringKeys(value) {
  try {
    return Reflect.ownKeys(value).every((key) => typeof key === "string" && Object.prototype.propertyIsEnumerable.call(value, key));
  } catch {
    return false;
  }
}
function isJsonSchemaRecord(value) {
  return isPlainJsonRecord(value) && hasOnlyEnumerableStringKeys(value);
}
function isPlainJsonArray(value) {
  if (!Array.isArray(value)) return false;
  try {
    if (!hasPlainArrayPrototype2(value) || Reflect.ownKeys(value).length !== value.length + 1) return false;
    for (let index = 0; index < value.length; index++) {
      if (!Object.hasOwn(value, index)) return false;
    }
    return true;
  } catch {
    return false;
  }
}
function isJsonNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && !Object.is(value, -0);
}
function scalarMatches(type, value) {
  switch (type) {
    case "string":
      return typeof value === "string";
    case "number":
      return isJsonNumber(value);
    case "integer":
      return isJsonNumber(value) && Number.isInteger(value);
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    /* v8 ignore next -- JsonSchemaScalarType is closed; this retains compile-time exhaustiveness. */
    default:
      return assertNever(type, "JsonSchemaType");
  }
}
var ONE_OF_SIBLING_KEYWORDS = ["properties", "required", "additionalProperties", "items", "enum", "const"];
function checkObjectSchemaTail(node, path, properties, violations) {
  const hasRequired = Object.hasOwn(node, "required");
  const required = hasRequired ? node.required : void 0;
  if (hasRequired) {
    if (!isPlainJsonArray(required) || required.some((entry) => typeof entry !== "string")) {
      violations.push(`${path}.required must be an array of strings`);
    } else {
      const declared = isJsonSchemaRecord(properties) ? properties : {};
      for (const key of required) {
        if (!Object.hasOwn(declared, key)) violations.push(`${path}.required names "${key}" which is not in properties`);
      }
    }
  }
  if (Object.hasOwn(node, "additionalProperties") && typeof node.additionalProperties !== "boolean") {
    violations.push(`${path}.additionalProperties must be a boolean`);
  }
}
function checkSchemaNode(root, rootPath, violations, seen) {
  const tasks = [{ kind: "enter", node: root, path: rootPath }];
  for (let task = tasks.pop(); task !== void 0; task = tasks.pop()) {
    if (task.kind === "leave") {
      seen.delete(task.node);
      continue;
    }
    if (task.kind === "one-of-tail") {
      for (const key of ONE_OF_SIBLING_KEYWORDS) {
        if (Object.hasOwn(task.node, key)) violations.push(`${task.path}.${key} is not supported beside oneOf`);
      }
      continue;
    }
    if (task.kind === "object-tail") {
      checkObjectSchemaTail(task.node, task.path, task.properties, violations);
      continue;
    }
    const { node, path } = task;
    if (!isJsonSchemaRecord(node)) {
      violations.push(`${path} must be a schema object`);
      continue;
    }
    if (seen.has(node)) {
      violations.push(`${path} is circular`);
      continue;
    }
    seen.add(node);
    tasks.push({ kind: "leave", node });
    for (const key of Object.keys(node)) {
      if (CONSTRAINT_KEYWORDS.has(key)) continue;
      if (ANNOTATION_KEYWORDS.has(key)) {
        try {
          if (!isJsonValue(node[key])) violations.push(`${path}.${key} annotation must be lossless JSON data`);
        } catch {
          violations.push(`${path}.${key} annotation must be lossless JSON data`);
        }
        continue;
      }
      violations.push(`${path}.${key} is not a supported keyword (subset: type/oneOf/properties/required/additionalProperties/items/enum/const + annotations)`);
    }
    if (Object.hasOwn(node, "description") && typeof node.description !== "string") {
      violations.push(`${path}.description must be a string`);
    }
    if (Object.hasOwn(node, "title") && typeof node.title !== "string") {
      violations.push(`${path}.title must be a string`);
    }
    const hasType = Object.hasOwn(node, "type");
    const hasOneOf = Object.hasOwn(node, "oneOf");
    if (hasType && hasOneOf) {
      violations.push(`${path} cannot declare both type and oneOf`);
      continue;
    }
    if (!hasType && !hasOneOf) {
      for (const key of ONE_OF_SIBLING_KEYWORDS) {
        if (Object.hasOwn(node, key)) violations.push(`${path}.${key} requires type or oneOf`);
      }
      continue;
    }
    if (hasOneOf) {
      const oneOf = node.oneOf;
      tasks.push({ kind: "one-of-tail", node, path });
      if (!isPlainJsonArray(oneOf) || oneOf.length < 2) {
        violations.push(`${path}.oneOf must be an array of at least two schemas`);
      } else {
        for (let index = oneOf.length - 1; index >= 0; index--) {
          tasks.push({ kind: "enter", node: oneOf[index], path: `${path}.oneOf[${index}]` });
        }
      }
      continue;
    }
    const type = node.type;
    if (typeof type !== "string" || !SCHEMA_TYPES.includes(type)) {
      violations.push(Array.isArray(type) ? `${path}.type must be a single type string (type arrays are not supported)` : `${path}.type must be one of ${SCHEMA_TYPES.join("/")}`);
      continue;
    }
    const schemaType = type;
    const allowedFor = {
      properties: ["object"],
      required: ["object"],
      additionalProperties: ["object"],
      items: ["array"],
      enum: ["string", "number", "integer", "boolean", "null"],
      const: ["string", "number", "integer", "boolean", "null"]
    };
    for (const [key, types] of Object.entries(allowedFor)) {
      if (Object.hasOwn(node, key) && !types.includes(schemaType)) {
        violations.push(`${path}.${key} is not supported on type "${schemaType}"`);
      }
    }
    switch (schemaType) {
      case "object": {
        const properties = Object.hasOwn(node, "properties") ? node.properties : void 0;
        tasks.push({ kind: "object-tail", node, path, properties });
        if (Object.hasOwn(node, "properties")) {
          if (!isJsonSchemaRecord(properties)) {
            violations.push(`${path}.properties must be an object of schemas`);
          } else {
            const entries = Object.entries(properties);
            for (let index = entries.length - 1; index >= 0; index--) {
              const entry = entries[index];
              if (entry === void 0) continue;
              tasks.push({ kind: "enter", node: entry[1], path: `${path}.properties.${entry[0]}` });
            }
          }
        }
        break;
      }
      case "array": {
        if (Object.hasOwn(node, "items")) tasks.push({ kind: "enter", node: node.items, path: `${path}.items` });
        break;
      }
      case "string":
      case "number":
      case "integer":
      case "boolean":
      case "null": {
        const hasEnum = Object.hasOwn(node, "enum");
        const allowed = hasEnum ? node.enum : void 0;
        const enumValid = isPlainJsonArray(allowed) && allowed.length > 0 && allowed.every((entry) => scalarMatches(schemaType, entry));
        if (hasEnum && !enumValid) {
          violations.push(`${path}.enum must be a non-empty array of ${schemaType} values`);
        }
        const hasConst = Object.hasOwn(node, "const");
        const declaredConst = hasConst ? node.const : void 0;
        const constValid = scalarMatches(schemaType, declaredConst);
        if (hasConst) {
          if (!constValid) {
            violations.push(`${path}.const must be a ${schemaType} value`);
          } else if (enumValid && !allowed.includes(declaredConst)) {
            violations.push(`${path}.const must be one of ${path}.enum when both are declared`);
          }
        }
        break;
      }
      /* v8 ignore next -- schemaType was narrowed from the closed SCHEMA_TYPES table above. */
      default:
        assertNever(schemaType, "JsonSchemaType");
    }
  }
}
function assertSupportedJsonSchema(schema) {
  const violations = [];
  checkSchemaNode(schema, "schema", violations, /* @__PURE__ */ new Set());
  if (violations.length > 0) throw new JsonSchemaError(violations);
}
function assertObjectJsonSchema(schema) {
  const violations = [];
  checkSchemaNode(schema, "schema", violations, /* @__PURE__ */ new Set());
  if (violations.length === 0 && (!isJsonSchemaRecord(schema) || !Object.hasOwn(schema, "type") || schema.type !== "object")) {
    violations.push('schema.type must be "object" (structured output is object-rooted)');
  }
  if (violations.length > 0) throw new JsonSchemaError(violations);
}
function safelyIsJsonValue(value) {
  try {
    return isJsonValue(value);
  } catch {
    return false;
  }
}
function diagnosticPath(path) {
  return path === "" ? "arguments" : path;
}
function propertyPath(path, key) {
  return path === "" ? key : `${path}.${key}`;
}
function losslessValueViolation(path) {
  return [`"${diagnosticPath(path)}" must be a lossless JSON value`];
}
function appendViolations(target, source) {
  for (const violation of source) target.push(violation);
}
function valueFrame(node, value, path) {
  return {
    node,
    value,
    path,
    catches: false,
    phase: "start",
    children: [],
    childIndex: 0,
    violations: [],
    tailViolations: [],
    matches: 0
  };
}
function checkScalarValue(node, value, path) {
  const allowed = Object.hasOwn(node, "enum") ? node.enum : void 0;
  if (allowed !== void 0 && !allowed.includes(value)) {
    return [`"${diagnosticPath(path)}" must be one of ${JSON.stringify(allowed)}`];
  }
  if (Object.hasOwn(node, "const") && value !== node.const) {
    return [`"${diagnosticPath(path)}" must be ${JSON.stringify(node.const)}`];
  }
  return [];
}
function checkValue(schema, value, path) {
  const frames = [valueFrame(schema, value, path)];
  let rootResult;
  const receive = (result) => {
    const parent = frames.at(-1);
    if (parent === void 0) {
      rootResult = result;
      return;
    }
    if (parent.kind === "oneOf") {
      if (result.length === 0) parent.matches++;
    } else {
      appendViolations(parent.violations, result);
    }
  };
  const finish = (result) => {
    frames.pop();
    receive(result);
  };
  while (frames.length > 0) {
    const frame = frames.at(-1);
    if (frame === void 0) break;
    try {
      if (frame.phase === "children") {
        if (frame.childIndex < frame.children.length) {
          const child = frame.children[frame.childIndex];
          if (child === void 0) throw new Error("missing schema-value child frame");
          frame.childIndex++;
          frames.push(valueFrame(child.node, child.value, child.path));
          continue;
        }
        if (frame.kind === "oneOf") {
          finish(frame.matches === 1 ? [] : [`"${diagnosticPath(frame.path)}" must match exactly one oneOf branch (matched ${frame.matches})`]);
          continue;
        }
        appendViolations(frame.violations, frame.tailViolations);
        if (frame.violations.length > 0) {
          finish(frame.violations);
        } else if (frame.kind === "object") {
          finish(safelyIsJsonValue(frame.value) ? [] : [`"${diagnosticPath(frame.path)}" must be a lossless JSON object`]);
        } else {
          finish(safelyIsJsonValue(frame.value) ? [] : [`"${diagnosticPath(frame.path)}" must be a dense lossless JSON array`]);
        }
        continue;
      }
      const nodeType = Object.hasOwn(frame.node, "type") ? frame.node.type : void 0;
      frame.catches = !(nodeType !== void 0 && !SCHEMA_TYPES.includes(nodeType));
      const oneOf = Object.hasOwn(frame.node, "oneOf") ? frame.node.oneOf : void 0;
      if (oneOf !== void 0) {
        frame.kind = "oneOf";
        frame.children = Array.from(oneOf, (branch) => ({ node: branch, value: frame.value, path: frame.path }));
        frame.childIndex = 0;
        frame.matches = 0;
        frame.phase = "children";
        continue;
      }
      if (nodeType === void 0) {
        finish(safelyIsJsonValue(frame.value) ? [] : losslessValueViolation(frame.path));
        continue;
      }
      switch (nodeType) {
        case "object": {
          if (!isPlainJsonRecord(frame.value)) {
            finish([`"${diagnosticPath(frame.path)}" must be an object`]);
            break;
          }
          const properties = Object.hasOwn(frame.node, "properties") ? frame.node.properties ?? {} : {};
          const violations = [];
          const required = Object.hasOwn(frame.node, "required") ? frame.node.required ?? [] : [];
          for (const key of required) {
            if (!Object.hasOwn(frame.value, key) || frame.value[key] === void 0) {
              violations.push(`missing required property "${propertyPath(frame.path, key)}"`);
            }
          }
          const children = [];
          for (const [key, child] of Object.entries(properties)) {
            if (!Object.hasOwn(frame.value, key) || frame.value[key] === void 0) continue;
            children.push({ node: child, value: frame.value[key], path: propertyPath(frame.path, key) });
          }
          const tailViolations = [];
          if (Object.hasOwn(frame.node, "additionalProperties") && frame.node.additionalProperties === false) {
            for (const key of Object.keys(frame.value)) {
              if (!Object.hasOwn(properties, key)) {
                tailViolations.push(`"${propertyPath(frame.path, key)}" is not a declared property (additionalProperties: false)`);
              }
            }
          }
          frame.kind = "object";
          frame.children = children;
          frame.childIndex = 0;
          frame.violations = violations;
          frame.tailViolations = tailViolations;
          frame.phase = "children";
          break;
        }
        case "array": {
          if (!Array.isArray(frame.value)) {
            finish([`"${diagnosticPath(frame.path)}" must be an array`]);
            break;
          }
          const items = Object.hasOwn(frame.node, "items") ? frame.node.items : void 0;
          const children = items === void 0 ? [] : frame.value.flatMap((entry, index) => [{ node: items, value: entry, path: `${frame.path}[${index}]` }]);
          frame.kind = "array";
          frame.children = children;
          frame.childIndex = 0;
          frame.violations = [];
          frame.phase = "children";
          break;
        }
        case "string":
          finish(typeof frame.value === "string" ? checkScalarValue(frame.node, frame.value, frame.path) : [`"${diagnosticPath(frame.path)}" must be a string`]);
          break;
        case "number":
          finish(typeof frame.value !== "number" ? [`"${diagnosticPath(frame.path)}" must be a number`] : !isJsonNumber(frame.value) ? [`"${diagnosticPath(frame.path)}" must be a finite JSON number`] : checkScalarValue(frame.node, frame.value, frame.path));
          break;
        case "integer":
          finish(!isJsonNumber(frame.value) || !Number.isInteger(frame.value) ? [`"${diagnosticPath(frame.path)}" must be an integer`] : checkScalarValue(frame.node, frame.value, frame.path));
          break;
        case "boolean":
          finish(typeof frame.value === "boolean" ? checkScalarValue(frame.node, frame.value, frame.path) : [`"${diagnosticPath(frame.path)}" must be a boolean`]);
          break;
        case "null":
          finish(frame.value === null ? checkScalarValue(frame.node, frame.value, frame.path) : [`"${diagnosticPath(frame.path)}" must be null`]);
          break;
        default:
          finish(assertNever(nodeType, "JsonSchemaType"));
      }
    } catch (error) {
      let failed = frames.pop();
      while (failed !== void 0 && !failed.catches) failed = frames.pop();
      if (failed === void 0) throw error;
      receive(losslessValueViolation(failed.path));
    }
  }
  return rootResult ?? losslessValueViolation(path);
}
function validateJsonSchemaValue(schema, value, path = "value") {
  return checkValue(schema, value, path);
}

// compat/upstream/schema.ts
var ANNOTATION_KEYS = ["description", "title", "default", "examples"];
function authorError(message) {
  throw new JsonSchemaError([message]);
}
function copyAnnotations(source, target) {
  if (Object.hasOwn(source, "description")) target.description = source.description;
  if (Object.hasOwn(source, "title")) target.title = source.title;
  if (Object.hasOwn(source, "default")) target.default = source.default;
  if (Object.hasOwn(source, "examples")) target.examples = source.examples;
}
function assertAuthorKeys(source, path, allowed) {
  for (const key of Object.keys(source)) {
    if (!allowed.includes(key)) authorError(`${path}.${key} is not supported by the value schema DSL`);
  }
}
function assignCompiledNode(destination, node) {
  switch (destination.kind) {
    case "root":
      destination.holder.value = node;
      break;
    case "property":
      Object.defineProperty(destination.target, destination.key, {
        value: node,
        enumerable: true,
        configurable: true,
        writable: true
      });
      break;
    case "item":
      destination.target.items = node;
      break;
    case "one-of":
      destination.target[destination.index] = node;
      break;
  }
}
function assignCompiledPropertyMap(destination, compiled) {
  if (destination.kind === "root") {
    destination.holder.value = compiled;
  } else {
    destination.target.properties = compiled.properties;
  }
}
function runSchemaCompiler(initial) {
  const seen = /* @__PURE__ */ new Set();
  const tasks = [initial];
  for (let task = tasks.pop(); task !== void 0; task = tasks.pop()) {
    if (task.kind === "leave") {
      seen.delete(task.input);
      continue;
    }
    if (task.kind === "property-map-tail") {
      if (task.required.length > 0) {
        task.compiled.required = task.required;
        if (task.destination.kind === "object") task.destination.target.required = task.required;
      }
      continue;
    }
    if (task.kind === "property") {
      if (!isJsonSchemaRecord(task.property)) authorError(`${task.path} must be a value schema object`);
      if (Object.hasOwn(task.property, "required") && task.property.required !== true) {
        authorError(`${task.path}.required must be true when present`);
      }
      if (Object.hasOwn(task.property, "required") && task.property.required === true) task.required.push(task.key);
      tasks.push({
        kind: "value",
        input: task.property,
        path: task.path,
        allowRequired: true,
        destination: { kind: "property", target: task.properties, key: task.key }
      });
      continue;
    }
    if (task.kind === "property-map") {
      if (!isJsonSchemaRecord(task.input)) authorError(`${task.path} must be an object of value schemas`);
      if (seen.has(task.input)) authorError(`${task.path} is circular`);
      seen.add(task.input);
      const compiled = { properties: {} };
      const required = [];
      assignCompiledPropertyMap(task.destination, compiled);
      tasks.push({ kind: "leave", input: task.input });
      tasks.push({ kind: "property-map-tail", compiled, required, destination: task.destination });
      const entries = Object.entries(task.input);
      for (let index = entries.length - 1; index >= 0; index--) {
        const entry = entries[index];
        if (entry === void 0) continue;
        tasks.push({
          kind: "property",
          property: entry[1],
          path: `${task.path}.${entry[0]}`,
          key: entry[0],
          properties: compiled.properties,
          required
        });
      }
      continue;
    }
    const { input, path } = task;
    if (!isJsonSchemaRecord(input)) authorError(`${path} must be a value schema object`);
    if (seen.has(input)) authorError(`${path} is circular`);
    seen.add(input);
    const authorKeys = [...ANNOTATION_KEYS, ...task.allowRequired ? ["required"] : []];
    const node = {};
    assignCompiledNode(task.destination, node);
    tasks.push({ kind: "leave", input });
    if (Object.hasOwn(input, "oneOf")) {
      assertAuthorKeys(input, path, [...authorKeys, "oneOf", "type"]);
      if (Object.hasOwn(input, "type")) authorError(`${path} cannot declare both type and oneOf`);
      if (!isPlainJsonArray(input.oneOf)) authorError(`${path}.oneOf must be an array of at least two value schemas`);
      const branches = [];
      node.oneOf = branches;
      copyAnnotations(input, node);
      for (let index = input.oneOf.length - 1; index >= 0; index--) {
        tasks.push({
          kind: "value",
          input: input.oneOf[index],
          path: `${path}.oneOf[${index}]`,
          allowRequired: false,
          destination: { kind: "one-of", target: branches, index }
        });
      }
      continue;
    }
    const inputType = Object.hasOwn(input, "type") ? input.type : void 0;
    switch (inputType) {
      case "json":
        assertAuthorKeys(input, path, [...authorKeys, "type"]);
        copyAnnotations(input, node);
        break;
      case "object":
        assertAuthorKeys(input, path, [...authorKeys, "type", "properties", "additionalProperties"]);
        if (!Object.hasOwn(input, "additionalProperties") || typeof input.additionalProperties !== "boolean") {
          authorError(`${path}.additionalProperties must be explicitly true or false`);
        }
        node.type = "object";
        copyAnnotations(input, node);
        node.additionalProperties = input.additionalProperties;
        if (Object.hasOwn(input, "properties")) {
          tasks.push({
            kind: "property-map",
            input: input.properties,
            path: `${path}.properties`,
            destination: { kind: "object", target: node }
          });
        }
        break;
      case "array":
        assertAuthorKeys(input, path, [...authorKeys, "type", "items"]);
        node.type = "array";
        copyAnnotations(input, node);
        if (Object.hasOwn(input, "items")) {
          tasks.push({
            kind: "value",
            input: input.items,
            path: `${path}.items`,
            allowRequired: false,
            destination: { kind: "item", target: node }
          });
        }
        break;
      case "string":
      case "number":
      case "integer":
      case "boolean":
      case "null":
        assertAuthorKeys(input, path, [...authorKeys, "type", "enum", "const"]);
        node.type = inputType;
        copyAnnotations(input, node);
        if (Object.hasOwn(input, "enum")) {
          if (!isPlainJsonArray(input.enum)) authorError(`${path}.enum must be a non-empty array of scalar values`);
          node.enum = Array.from(input.enum, (entry) => entry);
        }
        if (Object.hasOwn(input, "const")) node.const = input.const;
        break;
      default:
        authorError(`${path}.type must be string/number/integer/boolean/null/array/object/json, or use oneOf`);
    }
  }
}
function compilePropertyMap(input, path) {
  const holder = {};
  runSchemaCompiler({ kind: "property-map", input, path, destination: { kind: "root", holder } });
  return holder.value ?? authorError(`${path} did not compile`);
}
function compileValueSchema(input, path) {
  const holder = {};
  runSchemaCompiler({ kind: "value", input, path, allowRequired: false, destination: { kind: "root", holder } });
  return holder.value ?? authorError(`${path} did not compile`);
}
function valueSchemaSpecToJsonSchema(spec) {
  const schema = compileValueSchema(spec, "schema");
  assertSupportedJsonSchema(schema);
  return schema;
}
function parameterSchemaSpecToJsonSchema(spec) {
  const compiled = compilePropertyMap(spec, "parameters");
  const schema = {
    type: "object",
    properties: compiled.properties,
    ...compiled.required === void 0 ? {} : { required: compiled.required }
  };
  assertSupportedJsonSchema(schema);
  return schema;
}
var ToolArgsError = class extends HarnessError {
  /** Individual violations in schema-walk order. */
  violations;
  constructor(violations) {
    super(`invalid arguments: ${violations.join("; ")}`, "INVALID_ARGS");
    this.name = "ToolArgsError";
    this.violations = violations;
  }
};
function validateArgs(spec, args) {
  return validateJsonSchemaValue(parameterSchemaSpecToJsonSchema(spec), args, "");
}
function defineTool(options) {
  const userExecute = options.execute;
  const userFinalizeContent = options.finalizeContent;
  const userRender = options.output.render;
  const userPresentationMeta = options.output.presentationMeta;
  const userPresentCall = options.presentCall;
  const userPresentResult = options.presentResult;
  const userIsConcurrencySafe = options.isConcurrencySafe;
  if (options.timeoutMs !== void 0 && (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0)) {
    throw new Error(`defineTool(${options.name}): timeoutMs must be a positive finite number`);
  }
  const parameters = parameterSchemaSpecToJsonSchema(options.parameters);
  const outputSchema = valueSchemaSpecToJsonSchema(options.output.schema);
  const validate = (args) => validateJsonSchemaValue(parameters, args, "");
  const tool = {
    name: options.name,
    description: options.description,
    parameters,
    output: {
      schema: outputSchema,
      render(args, value) {
        return userRender(args, value);
      },
      ...userPresentationMeta !== void 0 ? {
        presentationMeta(args, value) {
          return userPresentationMeta(args, value);
        }
      } : {}
    },
    ...options.timeoutMs !== void 0 ? { timeoutMs: options.timeoutMs } : {},
    async execute(args, exec) {
      const violations = validate(args);
      if (violations.length > 0) throw new ToolArgsError(violations);
      return userExecute(args, exec);
    }
  };
  if (userFinalizeContent) {
    tool.finalizeContent = (exec, result) => userFinalizeContent(exec, result);
  }
  if (userPresentCall) {
    tool.presentCall = (args) => {
      if (validate(args).length > 0) return void 0;
      return userPresentCall(args);
    };
  }
  if (userPresentResult) {
    tool.presentResult = (args, result) => {
      if (validate(args).length > 0) return void 0;
      return userPresentResult(args, result);
    };
  }
  if (userIsConcurrencySafe) {
    tool.isConcurrencySafe = (args) => {
      if (validate(args).length > 0) return false;
      return userIsConcurrencySafe(args);
    };
  }
  return tool;
}
export {
  HarnessError,
  JsonSchemaError,
  ToolArgsError,
  assertObjectJsonSchema,
  assertSupportedJsonSchema,
  deepFreeze,
  defineTool,
  isJsonSchemaRecord,
  isJsonValue,
  isPlainJsonArray,
  isPlainJsonRecord,
  parameterSchemaSpecToJsonSchema,
  snapshotJsonValue,
  validateArgs,
  validateJsonSchemaValue,
  valueSchemaSpecToJsonSchema
};
