# Cordis engine

Libre Claw uses a persistent Cordis service graph to dispatch application work.
Python implements the existing agent algorithms, provider adapters, permissions,
storage, and tools. The daemon, standalone TUI, headless runner, reviewers, and
delegated agents bind those implementations to the shared engine.

## Services and execution

| Service | Application work |
| --- | --- |
| Agent | Turn lifecycle, streaming events, cancellation, and delegated work. |
| Providers | Model streaming and brokered plugin/native-provider model operations. |
| Tools | Execution after Libre Claw's normal permission and plan-mode checks. |
| Sessions | Durable agent checkpoints. |
| Memory | Context retrieval for the current turn. |
| Workflows | Dashboard Git review and worktree operations. |

Cordis manages service dependencies, activation, dispatch, and disposal. A
disabled dependency prevents its dependants from activating. Each operation has
one scoped host callback, and a stream has at most one undelivered item. Nested
agent/provider/tool operations can run concurrently without moving their Python
objects or permission futures into JavaScript.

Only opaque operation IDs and lifecycle messages cross the core runtime's
standard-input/output connection. Prompts, model output, session contents, and
provider credentials remain in the Python host. The core process has a clean
environment, private temporary storage, and no network permission. Failure stops
engine operations; it does not silently switch application runs to another
engine. The Python `Agent` remains independently testable as a service
implementation without launching an application runtime.

## Dashboard and terminal

The browser mounts its first-party features through a separate Cordis graph.
Its services own API dispatch, event listeners, timers, and disposal. Installed
third-party JavaScript is never injected into the administrative dashboard;
plugin settings use validated configuration forms.

**Engine** shows the live backend services, dependencies, methods, active work,
and completed/failed/cancelled operation counts. Restart is rejected while work
is active. **Plugins** manages installed extensions and their component settings.
Structured questions pause the requesting tool until the person answers or
cancels; choices are never submitted automatically.

```sh
libre-claw engine status
libre-claw engine check
```

The TUI supports `/engine` and `/plugins`. Standalone TUI runs share one engine
and their own extension manager; daemon-backed TUI runs use the daemon. Shutdown
joins task cleanup before closing plugin workers and the engine.

The included [Model orchestration plugin](ORCHESTRATION.md) binds an explicitly
selected team to a task. Its Cordis tools broker scoped concurrent workers through
the existing agent service, with fixed model routes, ownership, and budgets.

## Extension workers

An enabled extension runs in its own process for its project. Application-owned
workers remain alive between calls, retaining Cordis services, effects, and
session identities. Install/preview never executes package code. Explicit
activation validates the live tool catalog; configuration changes validate a
replacement before saving it. Replaced code cannot inherit the previous code's
grants or saved configuration. A plugin's private storage belongs to its identity
and survives upgrades; removal deletes it after stopping the worker.

Each worker has bounded traffic, deadlines, and cancellation. Question wait time
pauses the plugin execution deadline while the task's own deadline still applies.
Session events exposed to an extension are that extension's own bounded events,
not the conversation transcript. Background model calls require the separate
model-access grant; task-only callbacks expire when the call finishes.

The manager reconciles changed or revoked grants and closes affected workers.
Host authorization is checked on each broker request. Explicit dashboard changes
reconcile before returning; changes from another process are also checked by the
application's periodic reconciliation. Setting `[cordis].enabled = false` stops
extensions while leaving the first-party application engine available.

## Harness package compatibility

Compiled Cordis functions and Service classes, Harness bundle patches, scoped
groups, Schemastery configuration, and npm dependency graphs can be imported
directly. Supported host APIs include tool definitions and hooks, user questions,
plugin-owned session events/projections, model discovery/streaming, and private
storage. Unchanged production Harness question and todo plugins are exercised in
the integration tests.

Compatibility is checked, not assumed. A bundle requiring a service outside
these APIs fails with its missing dependency. Harness's private React client
slots, full agent/job APIs, PTC, and its filesystem/shell service contracts are
not replaced by empty facades. Libre Claw's own filesystem, shell, agents, and
workflows remain available through the application services and approval system.
Publish compiled packages; arbitrary installation scripts and YAML JavaScript
expressions are not executed. The known `dshHomePath` expression is translated
into a scoped private-state path.

## Libre WebUI

The included **Libre WebUI bridge** adapts the reviewed native-provider 0.1.1
protocol. Install it from **Plugins → Included**, then explicitly choose
**Enable with model access**. Its detail page shows the private Unix socket path
for Libre WebUI's native-provider settings. No provider key is copied to Libre
WebUI or to a JavaScript plugin.

The listener is implemented in Libre Claw's trusted Python host because Node's
network permission also governs Unix sockets. Its parent directory is private
and its socket is owner-only; it exposes no TCP listener. Requests use bounded
JSON/NDJSON, catalogue instance IDs, cancellation, and per-request authorization.
Grant revocation, changed source, or provider configuration changes invalidate
access. The original Harness server process is not needed.

This model-only bridge supports Libre Claw's API-backed providers. Native coding
providers such as Codex retain their normal agent workflow; they are not exposed
as a tools-disabled model endpoint with implicit workspace access. Opaque Harness
file/image attachment handles and unsupported generation options fail explicitly
rather than reading arbitrary local files or silently changing the request.

See [Cordis plugins](CORDIS.md) for package sources, grants, and configuration,
and [the privacy review](CORDIS_PRIVACY_REVIEW.md) for the outbound-network checks.
