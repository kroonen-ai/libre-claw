# Cordis engine

Libre Claw uses a persistent Cordis service graph to dispatch application work.
Python implements the existing agent algorithms, provider adapters, permissions,
storage, and tools. The daemon, standalone TUI, headless runner, reviewers, and
delegated agents bind those implementations to the shared engine.

## Services and execution

| Service | Application work |
| --- | --- |
| Agent | Turn lifecycle, streaming events, cancellation, and delegated work. |
| Providers | Discovery and streaming, goal judging, extraction, finalizers, and brokered plugin model operations. |
| Tools | Execution after Libre Claw's normal permission and plan-mode checks. |
| Sessions | Run/session storage, event history, checkpoints, and exports. |
| Memory | Initialization, retrieval, search, writes, deletion, and extraction. |
| Workflows | Goals, schedules, Git review, and worktree operations across dashboard and terminal. |

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

The standalone Telegram bridge owns the same engine and extension lifecycle.
Daemon-backed clients use the daemon's discovery and execution services. Store
bindings reject new asynchronous methods until they have an explicit service
mapping. Health, read-only recovery views, cancellation, plugin revocation, and
failure-state persistence deliberately remain usable when the engine is down;
they cannot perform model inference or execute agent tools.

Reviewed extensions can register core dispatch methods or add service nodes
with dependencies. This requires a separate `allow_engine` workspace grant.
New registrations load only after an explicit engine restart; changed or
revoked loaded registrations stop the graph. The extension receives service,
method, and operation mode, and can approve dispatch or reject it. Application
payloads and credentials still stay in Python. See
[core service extensions](CORDIS_ENGINE_EXTENSIONS.md) for the contract.

## Dashboard and terminal

The browser mounts its first-party features through a separate Cordis graph.
Its services own API dispatch, static and dynamic event listeners, timers,
animation frames, and disposal. Detached controls lose their effects on
rerender, and handlers check their owner's active state before local changes.
Installed third-party JavaScript is never injected into the administrative
dashboard; plugin settings use validated configuration forms.

Compiled Harness web-client extensions require a separate `allow_client` grant.
They run in an offline Node guest with real React and Cordis. The browser receives
only validated inert element trees and opaque event identifiers. Views support
React state/effects, slots, locale, and package-local chunks. Secret-marked
configuration and recognized credentials are omitted or redacted; provider keys
and the parent conversation are not passed to the guest. Remote
URLs, executable attributes, arbitrary HTML, and active CSS are rejected. Private
Harness application modules or direct browser-DOM dependencies outside this
portable client contract fail explicitly.

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
plugin-owned session events/projections, model discovery/streaming, private
storage, system-prompt contributions, scoped filesystem operations, shell,
jobs, agent delegation, LSP registration, and the PTC runtime. The compatibility
layer vendors the pinned upstream registries rather than substituting empty
service facades. Unchanged production Harness plugins exercise the host bridges
in integration tests.

Compatibility is checked, not assumed. A bundle requiring an application-specific
service outside these APIs fails with its missing dependency. Filesystem and
shell operations require explicit plugin paths and the task's normal approvals.
Writes use observed-version guards. Child agents retain scoped ownership and
budgets. Job processes are terminated and joined on cancellation, revocation,
or disposal. LSP servers are explicitly configured locally; no language server
is automatically downloaded or launched from a model-supplied command.
Language servers always run with read-only project access and isolation from
the host network, even when the plugin has a network grant. Linux uses a private
network namespace, where local socket creation and binding can still succeed;
the regression checks inability to reach a live receiver on the host network.

Imported agent drivers create fresh text assignments with explicit provider/model
routes and bounded child lifetimes. They do not expose arbitrary saved sessions
or parent histories. Use Libre Claw's task recovery and orchestration profiles
for saved work and configurable worker budgets. Filesystem reads, process output,
and directory listings have explicit host limits and return errors/truncation
metadata rather than claiming an unbounded upstream deployment.

PTC executes TypeScript in a separate process and invokes the plugin's actual
tool bindings over bounded JSON messages. Timeouts stop even an infinite loop.
Model-authored programs and shell commands use OS confinement: `sandbox-exec`
on macOS or `bubblewrap` with user namespaces on Linux. Unsupported confinement
fails before execution. PTC always denies network access and inherits no host
environment or credentials. A read-only policy narrows existing write grants.

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
