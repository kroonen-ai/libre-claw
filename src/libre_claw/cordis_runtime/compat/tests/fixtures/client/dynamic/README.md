These checked-in JavaScript artifacts are compiled from `source/` with the actual
DeepSeek Harness `clientBundle` tsdown preset at commit
`9291d7f66e87c5b89d4dbe63104734461f4d18a9`. They exercise compiler-generated
`require.async('./client.panel.js')` and package-owned chunk registration.
Tests execute the compiled artifacts unchanged; a separate test covers async
seed-module resolution.
The preset uses the upstream conversation package identity to resolve its public
seed externals, as upstream's own compiler tests do; the fixture source is ours.

Regenerate with a dependency-installed checkout of that commit:

```sh
"$HARNESS_CHECKOUT/node_modules/.bin/tsx" build.mts "$HARNESS_CHECKOUT"
```

The build uses a temporary input directory and does not modify Harness. It
normalizes machine-specific paths in region comments; emitted executable code
is unchanged.
