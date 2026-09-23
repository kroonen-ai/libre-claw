# Cordis integration completion

This checklist tracks the requirements from the engine and UI
adaptation. A running service graph and passing unit tests alone do not establish
complete application coverage. Each item requires an exercised application path
and a failure/disposal check before it is marked complete.

## Application execution

- [x] Main agent turns, provider streams, approved tools, and delegated workers.
- [x] Goal judging, automatic memory extraction, and automation finalization.
- [x] Model discovery, memory storage, session/run storage, and scheduling
  operations bound to their Cordis service lifecycle.
- [x] Explicit recovery path for engine failure, with no model/tool fallback
  that bypasses a stopped or disabled service.
- [x] Core service registration and replacement through reviewed plugin
  declarations, with dependency validation and operation authorization.

## Dashboard

- [x] First-party service graph, API dispatch, static bindings, and polling.
- [x] Dynamically created controls and timers owned by disposable Cordis scopes.
- [x] Repeated rendering does not retain detached controls or listeners.
- [x] Disposed or disabled services cannot trigger local actions or requests.

## Imported Harness packages

- [x] Compiled Cordis functions/classes, bundle patches, configuration, tools,
  questions, plugin-owned session events, and model discovery/streaming.
- [x] Reviewed Libre WebUI native-provider 0.1.1 protocol bridge.
- [x] Functional system-prompt, filesystem, shell, and jobs contracts with
  existing approval rules, resource bounds, and revocation cleanup.
- [x] Agent delegation, LSP, and PTC contracts verified against actual packages.
- [x] Client extension contracts reviewed and tested without exposing the
  administrative dashboard or host credentials to extension code.

## Acceptance

- [x] Unchanged reference packages exercise each supported host contract.
- [x] Denial, cancellation, replacement, and shutdown release their resources.
- [x] No telemetry, inherited provider credentials, or implicit network grants.
- [x] Full Python/runtime checks, package installation, and browser verification.

Release verification follows the implementation checks: commit and push, check
CI for that commit, then restart and inspect the managed local daemon. Its result
is recorded in the release/task report rather than predeclared by this checklist.

## Evidence

Local verification on September 23, 2026: 2,037 Python tests passed on Python
3.14 with warnings treated as errors; 60 JavaScript runtime tests passed.
Production JavaScript dependency auditing reported no known vulnerabilities.
The wheel and source distribution verified 228 matching package files. A fresh
wheel installation outside the checkout passed both CLI entrypoints and started
all six offline core services. Browser checks exercised isolated React state and
disposal, displayed core-extension ownership, and completed a concurrent team
task with simulated providers; they made no live model requests.

- `test_cordis_bindings.py`: execution gates, disabled services, engine failure,
  cancellation, and explicit recovery.
- `test_cordis_engine_plugins.py`: real registration/replacement, source/grant
  revocation, independent dispatch deadlines, and cancelled startup cleanup.
- `test_cordis_harness_services.py`: unchanged file/shell/job plugins, actual
  worker delegation, approvals, prompt scoping, and the PTC tool bridge.
- `test_cordis_ptc.py` and `test_cordis_lsp.py`: actual confined processes,
  protocol round trips, timeouts, revocation, and denial of network/credential
  and ungranted file access.
- `test_cordis_client.py`, runtime client tests, `test_client_view.py`, and
  `test_cordis_ui.py`: real React/Cordis guests, safe rendering/events, grant
  revocation, and bounded dynamic-control cleanup.

Python remains the implementation language for host algorithms and credential
storage. Cordis must own their application activation, dispatch, dependencies,
and disposal. This does not mean importing or running the DeepSeek Harness
application or its telemetry services.
