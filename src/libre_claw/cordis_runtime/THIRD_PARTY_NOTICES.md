# Cordis runtime dependencies

The bundled `vendor/cordis.mjs` contains the actual Cordis framework and
Cosmokit utility library. It is generated without modifications to their
runtime code from these exact npm releases:

| Package | Version | License | Source |
| --- | --- | --- | --- |
| `@deepseek-ai/cordis` | `4.0.2` | MIT | <https://github.com/deepseek-ai/deepseek-harness/tree/master/vendor/cordis> |
| `@deepseek-ai/cosmokit` | `1.8.3` | MIT | <https://github.com/deepseek-ai/deepseek-harness/tree/master/vendor/cosmokit> |

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
