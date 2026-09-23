# Cordis network and privacy review

Reviewed on September 23, 2026 against the Cordis integration at `d871ee5`.
This review covers the bundled framework, its local runtime, and Libre Claw's
Python bridge. It is not a guarantee about arbitrary third-party plugins or
other programs on the machine.

## Bundled code

No reference to `harness-telemetry.deepseeksvc.com`, another telemetry collector,
or a telemetry SDK was found in the Cordis runtime or its bundled dependencies.
The vendored framework has no external imports, network client calls, or network
destinations. Its default logger exporter buffers messages in memory.

An offline rebuild reproduced `vendor/cordis.mjs` byte for byte. Esbuild's input
graph contained only `@deepseek-ai/cordis` 4.0.2, `@deepseek-ai/cosmokit` 1.8.3,
and the generated export entry. The output had no external imports. The bundle's
SHA-256 was:

```text
d0cb62c252fc982f3192695871f0f8a75031630ac266000363ff26732fed6f06
```

This verifies the checked-in bundle against the local pinned build inputs; it
is not an independent audit of the npm registry or its publishing accounts.
The full DeepSeek Harness application is not included or launched.

## Runtime restrictions

The Python bridge communicates with the Node process over standard input and
output. It provides an explicit environment without inherited API keys, proxy
settings, Node preload options, or SSH agent sockets. The runtime receives the
tool arguments and plugin settings, not automatic conversation history.

Network access defaults to denied. macOS with Node 22 uses an OS sandbox that
denies network operations. Node 25 and later use Node's network permissions.
Unsupported offline configurations fail before plugin execution. Plugin
installation copies local files; startup does not run npm or fetch code.

The regression suite checks fetch, HTTP, TCP, UDP, and DNS against controlled
local receivers. Each probe succeeds without restrictions first, then must
report denial and deliver no connections or packets under offline restrictions.
The tests passed locally with both Node 22.22.3 and Node 26.9.0. No requests go
to the telemetry host or another public endpoint:

```sh
python -m pytest tests/test_cordis_security.py tests/test_cordis_integration.py
node --test src/libre_claw/cordis_runtime/tests/runtime.test.mjs
```

## Limits and other network traffic

- Explicitly granting `--allow-network` allows the plugin to contact arbitrary
  hosts. The current integration has no per-host network allowlist.
- Node permissions are guardrails for trusted code, not containment for a
  malicious plugin. Review extensions before enabling them.
- Tool arguments and results can appear in local task history. Results can be
  sent to the selected cloud model as part of the normal agent conversation.
- Libre Claw still uses network access for configured providers, Telegram,
  requested web tools, and model discovery. OpenCode model metadata uses
  `models.dev/api.json`; that request does not include conversation content or
  provider authentication. These are separate from the Cordis runtime.

Recheck the bundle and outbound tests whenever the runtime or its dependencies
change. See [Cordis plugins](CORDIS.md) for the complete permission model.

## Guided package installation

The dashboard package manager adds an explicit download path for sources the
user enters as `npm:package@version`. This installation path is separate from
plugin execution: it requests public metadata and an integrity-checked archive
only from `https://registry.npmjs.org`, rejects redirects and external archive
hosts, and sends no inherited credentials or proxy settings. It never runs npm,
install scripts, or the downloaded plugin. Local folders, local archives, and
the included catalog do not require a network request. Installation remains
separate from enabling a plugin and granting it network access.

The source validation, archive limits, preview cancellation, and project-bound
installation tokens are covered by `tests/test_cordis_packages.py` and
`tests/test_plugin_manager_api.py`. The offline execution restrictions and
outbound regression probes remain in place.
