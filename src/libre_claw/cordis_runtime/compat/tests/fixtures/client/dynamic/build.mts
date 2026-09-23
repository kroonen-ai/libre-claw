// Copyright 2026 Kroonen AI. SPDX-License-Identifier: Apache-2.0
// Run with the pinned Harness checkout's tsx executable; no checkout is modified.
import {mkdtemp, mkdir, copyFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const checkout = path.resolve(process.argv[2]);
const require = createRequire(path.join(checkout, 'package.json'));
const {build} = await import(pathToFileURL(require.resolve('tsdown')).href);
const {clientBundle} = await import(pathToFileURL(path.join(checkout, 'packages/client/tsdown.client.ts')).href);
const fixture = path.dirname(fileURLToPath(import.meta.url));
// The upstream preset resolves its seed externals from a real workspace package.
const packageName = '@deepseek-ai/dsh-client-ui-conversation';
const temporary = await mkdtemp(path.join(tmpdir(), 'libre-claw-compiled-client-'));
let builds = [];
try {
  await mkdir(path.join(temporary, 'lib/types/client'), {recursive: true});
  await writeFile(path.join(temporary, 'package.json'), JSON.stringify({name: packageName, type: 'module'}));
  for (const name of ['index.js', 'panel.js']) await copyFile(path.join(fixture, 'source', name), path.join(temporary, 'lib/types/client', name));
  const config = clientBundle(packageName, ['lib/types/index.js'])({env: {DSH_BUILD_FACE: 'client'}}).find(item => item.platform === 'browser');
  builds = await build({...config, cwd: temporary, config: false, tsconfig: false,
    write: false, clean: false, exports: false, report: false, logLevel: 'silent'});
  for (const chunk of builds.flatMap(item => item.chunks)) {
    if (chunk.type === 'chunk') {
      const code = chunk.code.replace(/^([ \t]*\/\/#region ).*\/lib\/types\/client\//gm, '$1lib/types/client/');
      await writeFile(path.join(fixture, chunk.fileName), code);
    }
  }
} finally {
  for (const bundle of builds) await bundle[Symbol.asyncDispose]();
  await rm(temporary, {recursive: true, force: true});
}
