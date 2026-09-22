# Code quality review — September 22, 2026

The standard findings were reviewed against the implementation and tests.
Actionable interface, import, assignment, assertion, and error-handling findings
were fixed. The following individual findings were dismissed after review;
their rules remain enabled for future scans.

| Finding | Reason for keeping the code |
| --- | --- |
| `py/ineffectual-statement` on `await` expressions | These statements join tasks, propagate cancellation, finish shielded writes, or reap subprocesses. Removing them can release workspace ownership before work stops or make cleanup assertions race. Cancellation and recovery tests cover these effects. |
| `py/ineffectual-statement` in `KeyringBackend` | Ellipsis bodies declare the methods of a `typing.Protocol`. They describe the keyring interface and are not missing application logic. |
| `py/unused-global-variable` on OpenCode `_METADATA_EXPIRES` | Both successful refreshes and failed refreshes update the deadline. Subsequent calls read it before fetching and again after taking the metadata lock. Removing these assignments disables caching and outage backoff. |
| JavaScript unused bindings and namespace initialization in `cordis_runtime/vendor/cordis.mjs` | These are generated from pinned Cordis 4.0.2 and Cosmokit 1.8.3 dependencies. Preserve the reproducible upstream bundle rather than hand-editing it for style warnings. Runtime and permission tests exercise the bundled framework. |

This disposition applies to the reviewed occurrences, not every future finding
from these rules. In particular, new task-wait findings must be checked for
their lifecycle and cancellation effects before dismissal.
