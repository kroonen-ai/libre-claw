# Core service extensions

Cordis core extensions customize service dispatch and dependencies. They receive
operation metadata only; prompts, results, credentials, and Python objects stay
in the host. They can reject a call or authorize its existing scoped callback.
They cannot invent a callback, dispatch another operation, or change its model
or tool arguments. Model/tool permissions still belong to the calling task.

This is a separate capability from ordinary isolated plugin tools. A core
extension shares the core service graph and can stop application work. Review
its source before granting core access. Installation or ordinary enablement does
not grant that access.

## Example package

Create a folder containing these three files:

`libre-claw-plugin.json`:

```json
{
  "id": "provider-policy",
  "name": "Provider policy",
  "version": "1.0.0",
  "entry": "plugin.mjs",
  "tools": [],
  "config": {"paused": false},
  "config_schema": {
    "type": "object",
    "properties": {"paused": {"type": "boolean"}},
    "additionalProperties": false
  },
  "engine": {
    "entry": "engine.mjs",
    "services": [{
      "id": "providers",
      "title": "Providers",
      "dependencies": [],
      "methods": ["complete", "stream"]
    }]
  }
}
```

`plugin.mjs`:

```js
export default { name: 'provider-policy-tools', apply() {} };
```

`engine.mjs`:

```js
export default {
  name: 'provider-policy',
  inject: ['libreEngine'],
  apply(ctx, config) {
    for (const method of ['complete', 'stream']) {
      ctx.effect(() => ctx.libreEngine.register('providers', method, (operation, next) => {
        if (config.paused) throw new Error('Model operations are paused.');
        next();
      }));
    }
  },
};
```

The metadata object contains `service`, `method`, and `mode` (`call` or `stream`).
`next()` records authorization. The host callback starts only after the extension
handler finishes successfully. Calling it twice, omitting it, rejecting after it,
or exceeding the five-second dispatch budget prevents the operation. The return
value of `next()` is not the host operation's result.

## Install and activate

```sh
libre-claw cordis install ./provider-policy
libre-claw cordis enable provider-policy --allow-engine
```

Use the same working directory as the intended task, or pass the global
`--working-directory` option. In the dashboard, **Enable core services** is the
equivalent explicit grant. Then use **Engine → Restart engine** while no tasks
are active. Newly granted extensions do not interrupt the existing graph.

The Engine view reports the plugin implementing each registered method. New
service IDs may declare dependencies and methods. Built-in methods and
dependencies cannot be removed; additional dependencies are validated and
topologically ordered. Two plugins cannot claim the same method.

Changes to loaded code, configuration, or grants stop the graph rather than
falling back to the original implementation. Revoke the plugin or correct its
configuration, then restart. Recovery views, cancellation, and plugin management
remain available. Replacing a package's source removes its previous grants.

Core extensions run offline with a clean environment and read access to their
verified package snapshots. File and network grants for an ordinary plugin do
not carry into the core process.
