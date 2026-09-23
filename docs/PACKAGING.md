# Build and package Libre Claw

The application is distributed as a Python wheel and source archive. The private
pnpm workspace manages the Cordis build dependencies and exposes the same build
commands used by CI. It includes only `src/libre_claw/cordis_runtime`; plugin
examples, test fixtures, and the separate website are not workspace packages.

## Local workflow

Use Python 3.11 or newer, Node.js 22.19 or newer on macOS (25 or newer on Linux
for offline engine checks), and the exact pnpm version in root `package.json`.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pnpm install --frozen-lockfile
pnpm run build
pnpm run test:runtime
pnpm run test:python
pnpm run package
python scripts/smoke_package.py
```

The install uses the committed lockfile and disables lifecycle scripts.
Esbuild uses its pinned platform package. Builds preserve dependency symlinks so
bundled source labels stay stable across pnpm stores and operating systems.
The application starts from its shipped bundles and needs no pnpm installation.

`pnpm run package` rebuilds the runtime, creates both Python archives, and checks
their contents against the source. `pnpm run package:check` repeats validation
without rebuilding. Start with a clean `dist/` directory when changing versions;
validation rejects ambiguous collections of release archives.

The output contains:

- `libre_claw-<version>-py3-none-any.whl`
- `libre_claw-<version>.tar.gz`
- `SHA256SUMS`
- `package-manifest.json`

The verifier checks metadata, CLI entry points, application files, runtime
bundles, licenses, and source-build manifests. It rejects missing or stale assets
and accidental dependency/cache files. The installation check creates a fresh
virtual environment in a temporary directory, installs the wheel and its Python
dependencies, then runs both command aliases and the offline Cordis engine.

## CI workflow

The workflow runs on pull requests, pushes to `main`, and manual dispatch.

1. The pnpm job installs the locked JavaScript dependencies with scripts disabled,
   rebuilds the runtime, rejects changes to the committed bundles, and runs the
   runtime tests.
2. The Python matrix runs tests with warnings treated as errors on Python
   3.11–3.14, including the offline coding-workflow checks.
3. After both jobs pass, the package job runs `pnpm run package`, repeats the
   bundle comparison, and tests the installed wheel outside the checkout.
4. The job uploads both archives, checksums, and the validation manifest as the
   `libre-claw-dist` artifact.

The workflow has read-only repository permissions and produces downloadable
artifacts. Registry publication is a separate release action; this workspace
does not publish an npm CLI package or upload Python packages to PyPI.

## Dependency updates

Update the runtime manifest and regenerate the root lockfile with pnpm. Rebuild
the bundles and review the changes and license files before committing them with
the manifest and lockfile. For imported Harness helpers, the build also verifies
the source hashes recorded in `compat/upstream.json`.

```sh
pnpm install
pnpm run build
pnpm run test:runtime
pnpm run package
```

Do not add a second npm or pnpm lockfile inside the runtime directory.
