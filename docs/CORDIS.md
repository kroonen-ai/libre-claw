# Cordis plugins

Libre Claw embeds the real Cordis framework used by DeepSeek Harness, through a
small local JavaScript runtime. Plugins can register tools, provide services,
declare dependencies, and use Cordis effects for lifecycle cleanup. Python keeps
control of model requests, approvals, and task history.

The runtime bundles `@deepseek-ai/cordis` 4.0.2 and Cosmokit under their MIT
licenses. It does not install or run DeepSeek Harness, connect to DeepSeek
telemetry, or require a DeepSeek account.

## Start with an offline plugin

Use Node.js 22.19 or newer on macOS with `sandbox-exec` available. On Linux,
offline plugins require Node.js 25 or newer with network permission
support. Libre Claw refuses to start an offline plugin when it cannot enforce the
network restriction. Windows is not supported by this integration yet. Nothing
is downloaded when a plugin starts.

From your project directory:

```sh
libre-claw cordis new ./my-plugin
# Review my-plugin/plugin.mjs and libre-claw-plugin.json.
libre-claw cordis install ./my-plugin
libre-claw cordis enable local-word-count
libre-claw cordis inspect local-word-count
```

The example counts words supplied in a tool call. Installation only copies and
validates local files; it does not execute the plugin. Enabling applies only to
the current project. Start a new task to discover `cordis__local-word-count__count_words`.
Its calls use the normal tool approval flow. Cordis tools are treated as mutating
for plan mode, even when a plugin claims otherwise.

The TUI supports `/plugins`, `/plugins inspect <id>`, `/plugins enable <id>`, and
`/plugins disable <id>`. **Settings → Plugins** shows installed versions, integrity,
and grants. Enabling from the TUI or dashboard always selects offline operation
with no additional filesystem grants. Installation and additional grants require
the local CLI.

```sh
libre-claw cordis list
libre-claw cordis disable local-word-count
libre-claw cordis remove local-word-count
```

Disabling prevents subsequent calls, including through an already registered
tool. It does not undo side effects of a call already in progress. Removal also
deletes the plugin's private state. Reinstalling changed code revokes its previous
project grants; review and enable the new version explicitly.

## Privacy boundaries

The [network and privacy review](CORDIS_PRIVACY_REVIEW.md) records the bundled
code inspection, reproducible build check, and outbound network regression tests.

- Each invocation has a separate process and a private state directory scoped to
  the plugin and project. Other plugins and projects do not share its grants.
- The child receives tool arguments, the plugin's own JSON settings, and runtime
  paths. It receives no automatic conversation history, memory, provider keys,
  parent environment, SSH agent sockets, or proxy settings.
- Network access is denied by default. Filesystem access covers only the bundled
  runtime, installed plugin, its private state, and explicitly granted paths.
  Child processes, workers, native addons, WASI, and Node's SQLite API are not
  enabled. Process time, JavaScript heap, and IPC output are bounded.
- There is no plugin telemetry, remote catalog, automatic update check, or npm
  installation at startup. Plugin console output is drained without being saved
  to activity logs. The usual Libre Claw task history can still record tool calls
  and results.
- A plugin cannot self-grant access in its manifest, call Libre Claw's privileged
  tools directly, or silently obtain model credentials. Installed snapshots are
  checked against their recorded digests before execution.

Cordis manages lifecycles; it is not a security sandbox. Node's permission model
provides guardrails for **trusted plugin code**, not containment for a malicious
plugin. macOS additionally enforces offline access at the OS level. Review code
before enabling it and use stronger external isolation for untrusted extensions.
See [Node's permission model](https://nodejs.org/api/permissions.html).

If you explicitly grant network access, the plugin can contact arbitrary hosts.
Anything passed as its arguments or exposed through file grants could be sent
there. Plugin results also become part of the current task and may be sent to
your selected cloud model, like results from other Libre Claw tools.

```sh
libre-claw cordis enable my-plugin --allow-network
libre-claw cordis enable my-plugin --read ./docs --write ./generated
```

Each enable command replaces this project's grants. Private state remains local
and is not encrypted by the Cordis runtime. A plugin can also write directly
inside its allowed state directory; the storage helper's JSON quota is not a
whole-process disk quota.

## Author a plugin

A package contains `libre-claw-plugin.json` and a local `.mjs`, `.js`, or `.cjs`
entry. The manifest declares `id`, `name`, `version`, `entry`, `tools`, and optional
`config`. Each tool declares `name`, `description`, and `input_schema`.
Declared and registered schemas must agree; undeclared tools are not exposed.
Installation excludes hidden files, dependency directories, and common private
key files, and rejects symlinks. Bundle dependencies into your entry beforehand.

An import-free Cordis plugin can use the host's `libre` service:

```js
export default {
  name: "example",
  inject: ["libre"],
  apply(ctx) {
    ctx.libre.registerTool({
      name: "hello",
      description: "Return a local greeting.",
      input_schema: {type: "object", properties: {}, additionalProperties: false}
    }, async () => ({content: "Hello from Cordis."}));
  }
};
```

`ctx.libre.registerTool` registers a reversible Cordis effect. `ctx.provide`,
`ctx.plugin`, `inject`, `ctx.on`, and `ctx.effect` support services, child plugins,
dependencies, events, and cleanup inside the package. `ctx.libre.cordis` exposes
the bundled `Context` and `Service` classes. The storage helper offers async
`get(key)`, `set(key, jsonValue)`, and `delete(key)` with bounded JSON values.
Use it for persistence; in-memory plugin state ends when the invocation exits.

DeepSeek Harness plugins that require its `llm`, `sessions`, or other harness
services need adaptation to Libre Claw's deliberately narrower host API. This is
Cordis framework integration, not unrestricted compatibility with every Harness
plugin. Refer to the [Cordis tutorial](https://github.com/deepseek-ai/deepseek-harness/tree/master/docs/cordis-tutorial)
for the underlying framework.

Set `[cordis].enabled = false` to disable Cordis tool exposure. `tool_timeout`
defaults to 30 seconds. Project configuration cannot choose a plugin directory
or executable: installation and grants are kept in the user's
`~/.libre-claw/cordis` registry.
