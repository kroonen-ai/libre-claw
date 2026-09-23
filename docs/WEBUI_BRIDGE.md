# Libre WebUI embedding bridge

`libre-claw bridge` serves a versioned JSONL protocol over private standard input/output. Libre WebUI supervises this process instead of embedding DeepSeek Harness packages. It runs Libre Claw's actual `Agent` and offline Cordis service graph. It does not connect to your personal daemon, load your user configuration, open a keyring, discover providers, or start telemetry.

Libre WebUI remains responsible for authentication and access checks. Each provider callback belongs to one parent-authorized turn. The parent supplies credentials directly to its existing provider adapter; credentials and account identifiers never enter this subprocess. No listening TCP socket is created.

## Modes

- **Host sessions:** a solo deployment can keep private sessions under its configured session directory. Workspace file tools enforce the selected directory. Read-only sessions expose only read tools; workspace-write sessions require one-shot approval before a write. Cancellation, tool results, reasoning, provider replay metadata and conversation state survive the appropriate turn and restart boundaries. Sessions created for ordinary Chat requests are transient.
- **Work steps:** SQL transcripts, provider selection, sandbox tools, approvals and durable job leases remain owned by WebUI. `Agent.step()` plans one assistant response using Libre Claw's agent/provider services, then returns tool requests before execution. No host filesystem tools, persistent session directory, user configuration or personal workspace are mounted in this mode. The parent sends the full authoritative transcript on every step, including after worker recovery.

The child does not load arbitrary Harness YAML or grant third-party plugins access to WebUI credentials. A separate, explicitly enabled Libre Claw native-provider socket can expose models from an independently configured instance; that is a model-only connection, not a shared agent session.

## Runtime

Python 3.11+ is required. On Linux, the offline engine requires Node.js 25+ with enforced network permissions. `LIBRE_CLAW_NODE` can select that executable separately from WebUI's application Node version. macOS can use Node.js 22 with its system sandbox. Unavailable confinement fails startup rather than allowing network access.

Stdout is reserved for protocol frames; logs go to stderr. The protocol bounds frames, queued callbacks and concurrency. Closing stdin, cancellation and process termination dispose active operations and the Cordis child. The supervising parent must also impose process startup/shutdown timeouts.

## Protocol version 1

Requests use `{ "id": "opaque-id", "method": "...", "params": {} }`. Responses use the same ID and either `result` or `error` (`message` and `code`). Initialize first with `protocolVersion: 1` and `mode: "host"` or `"work"`. Host initialization also supplies `workspacePath`, `sessionStorePath`, `persistence`, `tools` and optional `model`.

Host operations: `session.list`, `session.get`, `session.create`, `session.update`, `session.delete`, `session.approve`, `agent.list`, `tool.list`, `turn.start`, `turn.cancel`. Work uses `work.generate` and cancellation by `turnId`. Both modes support `shutdown`.

Streaming events contain `event: "stream"`, `turnId` and a typed `chunk`. Provider callbacks contain `callbackId`, `method: "provider.stream"` or `"work.provider"`, and turn-bound `params`. The parent returns incremental `event` frames followed by an explicit `done` terminal frame; Work also includes its authoritative response as `result`. A truncated or unsuccessful callback fails the turn. `callback.cancel` asks the parent to abort its provider request.

This is a trusted local embedding protocol, not a public HTTP API. Applications must never forward unauthenticated client frames to it.
