# Code quality review — September 22, 2026

## Cordis completion review — September 23, 2026

The scan of `071953c` produced 57 additional standard findings. Seventeen
first-party findings were addressed in source or tests: explicit cleanup
explanations, unused imports, a provider mock signature, side effects outside
assertions, asynchronous test observation, and normal attribute lookup with
deferred rejection of unmapped store operations. The original permission and
cancellation assertions remain in place.

Findings 827–842 are analyzer false positives on `await` expressions. Each await
joins work, observes an expected exception, or preserves cleanup ordering; they
were reviewed individually and dismissed without removing the synchronization.

Findings 795–818 concern preserved generated fixtures or pinned dependency code.
They were reviewed against the scanned commit, not changed line numbers after
rebuilding. The rules remain enabled:

| Findings | Disposition |
| --- | --- |
| 809–814 | JavaScript function-scoped `var` declarations are hoisted; values are assigned before consumption in React/Scheduler. |
| 799–803 | Production bundle specialization leaves unreachable development branches. |
| 797–798 | Preserved tsdown `module.exports` expression and a redundant strict-mode directive in an already-strict ES module. |
| 804 | Upstream's generic disposal helper retains an unused async branch for a synchronous instantiation. |
| 795, 805–808, 815–818 | Redundant upstream guards/assignments and extra arguments retained in production-specialized React helpers. |
| 796 | A genuinely unreachable lane-reset branch also exists in React Reconciler 0.29.2's development source. This is accepted pinned-upstream maintenance debt, not a false positive or an application fix. Reassess with a compatible reconciler update. |

Generated dependencies remain reproducible from pinned inputs with their
licenses. They were not hand-edited to conceal analyzer findings.

## Earlier review

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

All eleven saved suggestions were rechecked individually against the current
source on September 23. The nine applicable improvements are implemented. The
two model-name suggestions were factually incorrect; their concern about test
clarity is addressed with explicit local/cloud cases and persistence assertions.

| # | Saved suggestion | Resolution in current code |
| --- | --- | --- |
| 1 | Describe both default TUI input settings in the test name. | [CLI tests](../tests/test_cli.py) use `test_cli_tui_uses_default_mouse_and_inline_settings` and assert both mouse and inline settings. |
| 2 | Replace eight untyped CLI helper suppressions with annotations. | All eight helpers in [CLI tests](../tests/test_cli.py) have parameter and return annotations; no `no-untyped-def` suppressions remain. |
| 3 | Explain the large context/output limits in the runtime-model test. | [Daemon tests](../tests/test_daemon.py) use named model-limit fixture cases and derive assertions from the expected metadata. |
| 4 | Share the different OpenRouter limit examples across tests. | A parameterized fixture in [daemon tests](../tests/test_daemon.py) covers all three context/output pairs across runtime updates, persisted automation updates, and daemon-client requests. |
| 5 | Replace the allegedly invalid Ollama `:cloud` suffix with `:latest`. | Rejected the incorrect replacement. [Daemon tests](../tests/test_daemon.py) explicitly cover the documented cloud tag and verify that the exact tag survives persistence. |
| 6 | Replace Kimi on Ollama with a Llama model because the providers supposedly conflict. | [Daemon tests](../tests/test_daemon.py) cover both local Llama and cloud Kimi routes, keeping the provider and model paired explicitly. Ollama supports both. |
| 7 | Make Petdex's `working` to `running` normalization explicit. | [Petdex tests](../tests/test_petdex.py) parameterize `working-alias` and `running-canonical`, asserting the canonical payload for each. |
| 8 | Validate the installed SVG instead of matching a comment. | [Petdex tests](../tests/test_petdex.py) compare the installed asset with the bundled SVG, parse it, and assert its SVG root and nonempty children. |
| 9 | Rename the generic tool-context helper. | [Tool tests](../tests/test_tools.py) use `create_test_tool_context` at every call site. |
| 10 | Spell out ripgrep in the fallback test name. | [Tool tests](../tests/test_tools.py) use `test_search_files_uses_python_fallback_when_ripgrep_is_unavailable`. |
| 11 | Isolate the fake browser URL per instance. | [Tool tests](../tests/test_tools.py) initialize `FakePage.url` in `__init__`, with no class-level URL state. |

[Ollama lists `kimi-k2.6:cloud` and shows it in its API examples](https://ollama.com/library/kimi-k2.6%3Acloud).
Changing that fixture to an invented `:latest` tag would remove the intended
cloud-routing coverage. The existing fixes landed in `8a55fa3`; the follow-up
fixture consolidation and local/cloud cases strengthen those resolutions.

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
