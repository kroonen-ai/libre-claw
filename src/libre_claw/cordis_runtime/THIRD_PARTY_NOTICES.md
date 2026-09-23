# Cordis runtime dependencies

The bundled `vendor/cordis.mjs` contains the actual Cordis framework and
Cosmokit utility library. It is generated without modifications to their
runtime code from these exact npm releases:

| Package | Version | License | Source |
| --- | --- | --- | --- |
| `@deepseek-ai/cordis` | `4.0.2` | MIT | <https://github.com/deepseek-ai/deepseek-harness/tree/master/vendor/cordis> |
| `@deepseek-ai/cosmokit` | `1.8.3` | MIT | <https://github.com/deepseek-ai/deepseek-harness/tree/master/vendor/cosmokit> |
| `@deepseek-ai/schemastery` | `3.18.2` | MIT | <https://github.com/deepseek-ai/deepseek-harness/tree/9291d7f66e87c5b89d4dbe63104734461f4d18a9/vendor/schemastery> |
| `zod` | `4.4.3` | MIT | <https://github.com/colinhacks/zod> |

The full copyright and permission notices accompany the bundle in
`vendor/CORDIS-LICENSE` and `vendor/COSMOKIT-LICENSE`. Both derive from Shigma's
upstream Cordis and Cosmokit projects. The lockfile records the registry
archive URLs and integrity hashes for reproducible dependency installation.
`@standard-schema/spec` is a type-only dependency and adds no bundled runtime
code. Esbuild is a development tool, not a runtime dependency.

To regenerate the checked-in bundle with Node 22 or newer:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm test
```

Run these commands in this directory. Libre Claw never runs npm when starting
plugins. The Python wheel includes the generated bundle and notices; it does
not need a `node_modules` directory or a network connection.

Cordis manages dependencies and reversible effects. It is not a JavaScript
security sandbox. Libre Claw supplies the separate process restrictions and
requires explicit trust before loading a plugin.

## Harness tool compatibility

`vendor/harness-tools.mjs` contains the actual tool-schema compiler, strict
JSON-schema validator, immutable JSON helpers, and error base from DeepSeek
Harness revision `9291d7f66e87c5b89d4dbe63104734461f4d18a9`. The original source
files are retained byte-for-byte under `compat/upstream/`. The source paths and
SHA-256 checksums are recorded in `compat/upstream.json`; the build checks every
recorded file before bundling. The full MIT notice is `compat/upstream/LICENSE`.

The build aliases the schema helpers' `dsh-llm` import to the isolated upstream
error module. It does not import the Harness model/provider runtime, boot code,
telemetry, network services, or credential services. Libre Claw owns the Cordis
service adapters in `compat/tools.mjs` and brokers host actions through its own
execution boundary.

`vendor/harness-messages.mjs` contains that same revision's immutable message
constructors, UUID/brand helpers, and stream block assembler. These pure value
helpers preserve the provider-neutral Harness message format without loading
its provider adapters. `vendor/cosmokit.mjs` exposes the audited utility exports
to plugins that import the package directly.

`vendor/schemastery.mjs` and `vendor/zod.mjs` retain the real configuration and
projection validators used by compatible plugins. Their copyright notices are
`vendor/SCHEMASTERY-LICENSE` and `vendor/ZOD-LICENSE`. Schemastery also uses the
same pinned Cosmokit release listed above.

The tests retain unchanged compiled production `dsh-tool-ask-user` and
`dsh-tool-todo` modules plus an upstream tool fixture in `compat/tests/fixtures/`,
covered by the same Harness MIT notice and source/checksum record. Test-only
module resolution points their imports at the compatibility services and the
real validators; the fixture source itself is not rewritten.
