# Cordis plugins

Libre Claw embeds the real Cordis framework used by DeepSeek Harness, through a
small local JavaScript runtime. Plugins can register tools, provide services,
declare dependencies, and use Cordis effects for lifecycle cleanup. Python keeps
control of model requests, approvals, and task history.

The runtime bundles `@deepseek-ai/cordis` 4.0.2 and Cosmokit under their MIT
licenses. It does not install or run DeepSeek Harness, connect to DeepSeek
telemetry, or require a DeepSeek account.

## Manage plugins in the dashboard

Open **Plugins** in the sidebar, or **Settings → Plugins**. The included **Text
utilities** plugin is a ready-to-use offline example. Choose **Add plugin** to
check a local folder, `.tgz` archive, or public `npm:package@version` package.
Review its name, version, description, and declared tools before installing.
Installation leaves a new plugin disabled; **Enable now** grants offline access
for this project. Tools are available on the next message, including in an
existing TUI conversation. An in-progress turn keeps its existing tool catalog.

Select an installed plugin for its configuration, tool descriptions, permissions,
and **Check runtime**. Configuration edits remain staged until **Save**; **Reset**
discards unsaved edits. A runtime check mounts and disposes the plugin under its
existing grants; it does not run a tool or leave a service running. Disabled
plugins must be enabled before this check.

**Remove** deletes the installation, configuration, and private state for all
projects after confirmation. Disabling only revokes the current project's access
and preserves its settings. Replacing installed code resets grants and settings
for all projects, so new code never inherits existing credentials or permissions.

Package checks and installation never execute JavaScript, npm, or lifecycle
scripts. The reviewed bytes are held in a temporary snapshot bound to the
current project. Approval expires after 15 minutes; expired snapshots are removed
on the next package operation or shutdown. Cancel discards a completed preview;
installation cannot silently switch to a different package after review.

Public npm packages must contain `libre-claw-plugin.json` and self-contained
JavaScript, with no runtime package dependencies. Checking an `npm:` source
contacts only the public npm registry, without inherited authentication or proxy
settings, and verifies the archive integrity. Local and included packages work
without a network request. Git URLs, arbitrary download URLs, and unadapted
DeepSeek Harness bundles are rejected with compatibility guidance.

## Start from the terminal

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
the current project. Your next message can use `cordis__local-word-count__count_words`.
Its calls use the normal tool approval flow. Cordis tools are treated as mutating
for plan mode, even when a plugin claims otherwise.

The TUI supports `/plugins`, `/plugins catalog`, `/plugins install <source>`,
`/plugins details <id>`, `/plugins inspect <id>`, `/plugins enable <id>`, and
`/plugins disable <id>`. Quote paths containing spaces. Enabling from the TUI or
dashboard always selects offline operation with no additional filesystem grants.
Additional filesystem or network grants require the local CLI.

```sh
libre-claw cordis list
libre-claw cordis disable local-word-count
libre-claw cordis remove local-word-count

# Included example; installs disabled.
libre-claw cordis catalog
libre-claw cordis install builtin:text-utilities
libre-claw cordis enable text-utilities

# Inspect package metadata without installing or executing it.
libre-claw cordis preview ./my-plugin

# Show redacted settings, or save project settings from JSON.
libre-claw cordis config text-utilities
libre-claw cordis config text-utilities --file settings.json
```

Disabling prevents subsequent calls, including through an already registered
tool. It does not undo side effects of a call already in progress. Removal also
deletes the plugin's private state. Reinstalling changed code revokes its previous
project grants and settings; review, configure, and enable the new version explicitly.

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
  installation at startup. Explicit `npm:` package checks download metadata and
  package bytes from `registry.npmjs.org`. Plugin console output is drained without being saved
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
`description`, `config`, and `config_schema`. Each tool declares `name`,
`description`, and `input_schema`.
Declared and registered schemas must agree; undeclared tools are not exposed.
Installation excludes hidden files, dependency directories, and common private
key files, and rejects symlinks. Bundle dependencies into your entry beforehand.

`config` supplies runtime defaults. `config_schema` enables dashboard form fields
and validates saved configuration. It supports explicit object, array, string,
boolean, number, integer, and null types; properties, required fields, additional
properties, item schemas, enum/const, size bounds, uniqueness, and numeric bounds.
Unsupported schema keywords are rejected. Schema `default` is an annotation;
put actual runtime defaults in `config`.

Mark private settings with `writeOnly: true` or the string format `password`.
Their values and default/example annotations are omitted from management
responses. Leaving these fields blank preserves saved values; explicitly clearing
them removes the value, subject to required-field validation. Settings are scoped
to the plugin and project and remain local in the owner-only registry. They are
not encrypted. Fields without a private marker are visible in the editor.

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
