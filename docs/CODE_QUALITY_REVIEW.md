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

The September 23 plugin-manager scan added task-wait findings in
`web/plugins_api.py` and `tests/test_cordis_packages.py`, and reissued the
existing daemon and Cordis cleanup waits after line changes. These eight
occurrences were reviewed individually. The API wait retains the transaction
lock until a cancelled write finishes; the tests await errors, staging cleanup,
and a scheduled worker release. The existing waits complete runtime disposal,
memory extraction, and final run persistence. All eight remain necessary. The
same scan's unused import and mutation inside an assertion were corrected.

## Saved AI suggestions

The eleven saved suggestions for CLI, daemon, Petdex, and tool tests were also
reviewed. Nine led to clearer test names, typed helpers, named model-limit
fixtures, explicit state-normalization coverage, SVG validation, or isolated
page-fixture state.

Two suggestions incorrectly claimed that `kimi-k2.6:cloud` was an invalid or
inconsistent Ollama model name. [Ollama lists that exact cloud model](https://ollama.com/library/kimi-k2.6%3Acloud).
The fallback-routing test intentionally retains it; substituting `:latest` or
an unrelated Llama model would remove the cloud-routing example.

## Cordis engine scan — September 23, 2026

The findings below were reviewed against commit `75360d4` and rechecked against
`5af870a`. All 31 reviewed occurrences were dismissed in GitHub on September 23;
the API then reported zero open standard findings. No rules were disabled, and
no runtime or test code was changed to hide these findings.

All twenty `py/ineffectual-statement` reports identify effectful `await`
expressions. Each wait either completes an operation, observes its exception,
or joins cleanup before the following assertions or lifecycle transition.

| Finding | Location in the scanned commit | Required effect |
| --- | --- | --- |
| 769 | `core/agent.py:745` | Finish the shielded file-operation task before propagating cancellation and releasing ownership. |
| 770 | `core/cordis.py:1079` | Join runtime disposal before propagating cancellation. |
| 771 | `daemon.py:1606` | Complete memory extraction before finalizing the run. |
| 772 | `daemon.py:1628` | Join durable finalization before correcting cancellation state. |
| 773 | `web/plugins_api.py:66` | Keep the transaction lock until the cancelled request's filesystem worker finishes. |
| 774 | `tests/test_cordis_engine.py:142` | Observe cancellation and join host cleanup before checking active operations. |
| 775 | `tests/test_cordis_engine.py:347` | Observe cancellation of a dispatched request before checking remote-operation cleanup. |
| 776 | `tests/test_cordis_harness_integration.py:151` | Observe the activation error after a concurrent disable; verify access remains revoked. |
| 777 | `tests/test_cordis_harness_integration.py:253` | Observe task cancellation before checking that the question handler was released. |
| 778 | `tests/test_cordis_harness_integration.py:671` | Wait for cancelled removal to finish worker and private-state cleanup. |
| 779 | `tests/test_cordis_llm.py:78` | Execute each coroutine and assert that authorization fails before provider work. |
| 780 | `tests/test_cordis_llm.py:295` | Finish the delayed request before closing its iterator and checking its detached input. |
| 781 | `tests/test_cordis_llm.py:316` | Observe cancellation before checking provider-stream closure and absence of retries. |
| 782 | `tests/test_cordis_packages.py:531` | Observe the closed-preview error before checking staging and registry state. |
| 783 | `tests/test_cordis_packages.py:535` | Join cancelled preview cleanup before checking that no staging remains. |
| 784 | `tests/test_cordis_packages.py:564` | Join repeatedly cancelled staging and its filesystem worker before cleanup assertions. |
| 785 | `tests/test_cordis_packages.py:592` | Join the scheduled worker-release task; do not leave test work running. |
| 786 | `tests/test_cordis_worker.py:95` | Observe cancellation before checking subprocess, reader, and callback termination. |
| 787 | `tests/test_cordis_worker.py:146` | Observe cancellation before checking model-generator and worker cleanup. |
| 788 | `tests/test_tui_questions.py:145` | Complete the first answer submission before checking that submission tracking was cleared. |

The eleven JavaScript findings were also reviewed against their pinned upstream
sources and generated bundles. They do not identify missing application behavior.
Preserving reproducible generated output is preferable to manually removing
bindings or changing upstream APIs for these reports.

| Findings | Disposition |
| --- | --- |
| 758–760, `vendor/harness-tools.mjs` | Three unused regular-expression constants remain from upstream `error.ts` after their consumers were removed by bundling. The unused-binding observation is accurate; editing the generated bundle is unnecessary. |
| 761–764, `vendor/schemastery.mjs` | Four unused Cosmokit Binary conversion aliases survive property-read initializers. The Binary implementation itself remains required by Schemastery. |
| 765, `vendor/schemastery.mjs` | The Time assignment initializes a generated Cosmokit namespace; it is not an application-state or lifecycle error. |
| 766, `vendor/schemastery.mjs` | The generic `pick` helper's optional `forced` parameter is intentionally omitted by its regular-expression projection caller. |
| 767, `vendor/schemastery.mjs` | Schema is intentionally callable with or without `new`; its factory explicitly returns the constructed schema in both cases. Reference rehydration uses `new`, while cloning uses the ordinary call. |
| 768, `vendor/schemastery.mjs` | The generic filter passes key and value; the key-only predicate deliberately ignores the extra value argument. |

The vendor review exercised both Schema call styles, validation failures,
reference rehydration, translation filtering, and regular-expression projection.
These dispositions apply only to these reviewed occurrences.

## Runtime warning cleanup

The latest CI logs contained 49 Click deprecation warnings in the CLI tests.
Terminal detection now uses the current standard output stream instead of
Click's deprecated `get_text_stream` helper. Output still uses Click and Rich;
TTY detection, redirected output, encoding, and missing stdout have regression
coverage. The targeted tests pass with deprecations treated as errors on both
Click 8.4 and 8.5. CI now treats all Python warnings as errors so new warnings
cannot pass unnoticed.

That stricter check exposed a SQLite acquisition race on Python 3.13 and 3.14.
If a task was cancelled while aiosqlite's worker opened the database, its future
could discard the newly created connection before the context manager took
ownership. Every MemoryStore connection now joins acquisition and closure despite
repeated cancellation, then propagates cancellation or the existing operation
error. Delayed SQLite operations exercise these races without relying on garbage
collection timing.

The AI findings page still displays the eleven June suggestions reviewed above.
Their applicable changes were committed in `8a55fa3`. GitHub currently offers
no dismissal action there and reports that organization AI usage is blocked
until September 30, so it cannot refresh those saved suggestions. No billing
settings or scan rules were changed.
