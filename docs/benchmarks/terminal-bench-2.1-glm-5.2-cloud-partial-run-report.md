<!-- Copyright 2026 Kroonen AI (https://kroonen.ai) -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Terminal-Bench 2.1 Partial Run Report

## Executive summary

The Libre Claw evaluation using `ollama/glm-5.2:cloud` did not complete. The preserved
job contains 80 materialized trial slots out of 445 planned trials, with 365 still
pending. It covered 79 of the 89 benchmark tasks at least once before provider quota
exhaustion and repeated resume attempts stopped useful progress.

The partial raw reward is **41/80 (51.25%)**. This is not an official Terminal-Bench
score: only 17.98% of the planned trial slots ran, five slots were cancelled while the
job was being stopped, ten local slots ended in provider quota errors, and two trials
never reached Libre Claw because their instructions were parsed as CLI options.

The run exposed several harness problems that should be corrected before another
public evaluation:

1. The Harbor adapter passes the task through a positional shell argument without an
   option terminator. Instructions beginning with `-` can be interpreted as Libre
   Claw CLI options.
2. The benchmark profile exposes 25 tools and the full production system prompt even
   though most tools are irrelevant to Terminal-Bench. This expands every provider
   request and increases the model's decision surface.
3. Completed agent runs consumed 45,985,494 input tokens across only 52 clean process
   completions. Median input usage was 327,063 tokens, and the 90th percentile was
   2,371,715 tokens.
4. The agent has no awareness of the task deadline. It can continue exploration,
   dependency installation, or compilation until Harbor terminates it.
5. ATIF is written only after a normal headless return. All ten timed-out trials lack
   a trajectory, including one trial that passed its verifier before timing out.
6. Uploading every partial resume accumulated transient failures in the public Harbor
   job. Local retry cleanup did not remove already uploaded rows.
7. Four concurrent cloud workers exhausted the available provider quota without a
   run-level circuit breaker.

The local job directory remains preserved for diagnosis and eventual clean resume.
The polluted intermediate public upload was removed so it cannot be mistaken for a
finished benchmark submission.

## Run identity

| Field | Value |
| --- | --- |
| Dataset | `terminal-bench/terminal-bench-2-1` |
| Dataset digest | `sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a` |
| Agent | Libre Claw Harbor adapter |
| Agent source revision | `9bdcb48` |
| Provider and model | `ollama/glm-5.2:cloud` |
| Reasoning effort | `auto` |
| Attempts per task | 5 |
| Planned tasks | 89 |
| Planned trial slots | 445 |
| Active concurrency | 4 workers |
| Timeout policy | Official task-specific Terminal-Bench limits |

No fallback model or provider was used. Using one would invalidate a single-model
benchmark result.

## Snapshot results

| Metric | Value |
| --- | ---: |
| Materialized trial directories | 80 |
| Unique tasks represented | 79 of 89 |
| Pending trial slots | 365 |
| Reward `1.0` | 41 |
| Reward `0.0` | 34 |
| No verifier reward | 5 |
| Raw partial reward | 51.25% |
| Clean process completions | 52 |
| Clean completions with reward `1.0` | 40 |
| ATIF trajectories present | 63 |
| Rewarded trials with ATIF | 40 of 41 |

For diagnosis only, removing the ten quota failures and five interrupted slots gives
41/65, or 63.08%. This adjusted value is not a leaderboard score because it still
mixes normal failures, timeouts, and harness failures. Among the 52 trials that
completed without an exception, 40 passed their verifier (76.92%); this is useful for
comparing future harness revisions but is also not an official score.

## Local failure taxonomy

| Exception | Count | Classification | Required handling |
| --- | ---: | --- | --- |
| `AgentTimeoutError` | 10 | Model and harness efficiency | Analyze and rerun after deadline-aware changes |
| `ApiRateLimitError` | 10 | Provider quota | Preserve completed trials and retry after quota returns |
| `CancelledError` | 5 | Controlled interruption | Rerun the interrupted slots |
| `NonZeroAgentExitCodeError` | 3 | Two harness defects, one provider empty response | Fix and rerun |
| No exception | 52 | Normal completion | Retain |

The intermediate public job showed 208 uploaded rows and 156 errors: 138 quota
errors, 10 timeouts, 5 cancellations, and 3 nonzero exits. This did not mean the
preserved local job had 156 distinct current failures. Each partial public resume
uploaded transient quota failures, while local resume filtering later removed and
recreated those trial directories. The remote upload therefore accumulated obsolete
attempts.

## Timeout analysis

| Task | Deadline | Reward | ATIF |
| --- | ---: | ---: | --- |
| `gcode-to-text` | 900 s | 0 | Missing |
| `gpt2-codegolf` | 900 s | 0 | Missing |
| `large-scale-text-editing` | 1,200 s | 0 | Missing |
| `make-doom-for-mips` | 900 s | 0 | Missing |
| `make-mips-interpreter` | 1,800 s | 0 | Missing |
| `path-tracing` | 1,800 s | 0 | Missing |
| `qemu-startup` | 900 s | 1 | Missing |
| `rstan-to-pystan` | 1,800 s | 0 | Missing |
| `torch-pipeline-parallelism` | 900 s | 0 | Missing |
| `train-fasttext` | 3,600 s | 0 | Missing |

These are the official task deadlines, not a single incorrectly configured timeout.
The `rstan-to-pystan` log is representative: the agent inspected the source and APIs,
installed dependencies, compiled and ran Stan code, and remained active until the
1,800-second deadline. The problem is not simply that the deadline was short. The
agent lacked a remaining-time signal and a strategy for switching from exploration to
verification and submission.

`qemu-startup` is especially important. Its verifier returned reward `1.0`, but Harbor
timed out the agent process before Libre Claw wrote ATIF. It cannot be accepted as a
valid published trajectory and must be rerun.

## Nonzero exits

### Leading-hyphen task instructions

Two `pytorch-model-recovery` trials failed before agent execution with:

```text
Error: No such option '- '.
```

The adapter exports the instruction as `HARBOR_INSTRUCTION` and expands it as the
final positional argument. It does not place `--` before the task text. A task whose
first characters resemble an option can therefore be consumed by Click instead of
being delivered as the message.

The adapter should pass the instruction after an explicit option terminator or over a
dedicated stdin/file channel, and it needs regression tests for leading hyphens,
newlines, quotes, shell substitutions, and empty instructions.

### Empty provider response

The `write-compressor` trial started normally, emitted one short assistant sentence,
then exited because the provider returned neither assistant text nor tool calls. The
harness treated this as terminal immediately. A bounded retry for structurally empty
provider responses would make this failure recoverable without hiding persistent
provider defects.

## Token and context behavior

The 52 clean process completions account for all nonzero recorded usage:

| Usage metric | Input tokens | Output tokens |
| --- | ---: | ---: |
| Total | 45,985,494 | 853,887 |
| Mean per clean completion | 884,336 | 16,421 |
| Median per clean completion | 327,063 | 10,747 |
| 90th percentile | 2,371,715 | 38,970 |
| Maximum | 5,165,462 | 65,596 |

One successful five-minute trial used 352,764 input tokens and 10,877 output tokens.
Its ATIF trajectory contains 27 steps. This indicates that high cumulative input is
not limited to failed or unusually long tasks.

The current agent compacts only when estimated context reaches 80% of a 200,000-token
window, then retains the last eight messages. Provider usage is cumulative across
calls, so repeatedly sending a large history and all tool schemas can produce several
times the nominal context window in total input usage during one trial.

## Tool and prompt surface

Each recorded trajectory exposes 25 tool definitions, including browser automation,
web search, HTTP, schedules, skills discovery, and git commit. Terminal-Bench normally
needs a much smaller coding set: file read/write/edit/list, glob/search, shell, and
possibly git status and a lightweight planning tool.

Disabling memory, skills, Petdex, and automations in the benchmark TOML does not prune
their registered tool schemas. `create_builtin_registry()` instantiates every
registered built-in tool, and the headless runner sends all registry schemas to the
provider.

The benchmark instruction is appended to the default production system prompt. The
result still describes Libre Claw's general user-facing identity and unrelated tools.
A benchmark profile should replace this with a compact task-focused prompt rather
than append to it.

## Trajectory integrity

There are 63 local trajectory files. All parse as `ATIF-v1.7`, all contain at least two
steps, and the median trajectory contains 18 steps. Their maximum is 96 steps.

Trajectory export currently happens only after the asynchronous agent loop exits.
Harbor can terminate the process before that code runs, so timeout and cancellation
paths lose the complete in-memory recording. Usage is also absent from those trial
results, which means the aggregate token count underestimates the true provider load.

The next version should checkpoint an atomic ATIF snapshot after each completed
assistant/tool-result exchange and flush a final interrupted marker from `finally`
and signal handlers. A timeout trajectory must remain valid ATIF and clearly indicate
that the run was interrupted.

## Command execution budget

The benchmark profile fixes Libre Claw's shell command timeout at 180 seconds while
Terminal-Bench task deadlines range from 900 to 3,600 seconds in the observed sample.
Long compiles, model builds, and test suites can therefore be fragmented or killed
inside an otherwise valid task budget.

The command timeout should be derived from the remaining Harbor task time, with a
small reserve for verification and trajectory flushing. This is different from
raising Terminal-Bench's official task timeout and does not change the benchmark
policy.

## Implementation map

The next engineering pass should begin in these modules:

| Area | Current implementation | Why it matters |
| --- | --- | --- |
| Harbor instruction transport | `benchmarks/harbor/libre_claw_agent.py` | Expands the task as a positional shell argument without `--` |
| Benchmark prompt and limits | `benchmarks/harbor/libre_claw_agent.py` | Appends a benchmark note, allows 250 tool calls, and fixes shell commands at 180 seconds |
| Headless registry and export | `src/libre_claw/headless.py` | Builds the full registry and exports ATIF only after the agent loop returns |
| Built-in tool selection | `src/libre_claw/tools_builtin/__init__.py` | Instantiates every registered built-in tool regardless of benchmark needs |
| Context compaction | `src/libre_claw/core/agent.py` | Triggers compaction at the configured context threshold |
| Compaction retention | `src/libre_claw/core/session.py` | Keeps eight recent messages after deterministic summarization |
| ATIF recording and write | `src/libre_claw/core/atif.py` | Retains messages in memory and writes one final atomic artifact |
| Production defaults | `src/libre_claw/default.toml` | Supplies the broad production prompt and 200,000-token context defaults |

## Required harness changes before rerun

### Must fix

1. Add an option terminator or stdin-based instruction transport in the Harbor
   adapter, with hostile-string regression tests.
2. Add a benchmark execution profile with a minimal system prompt and allowlisted
   tool registry.
3. Checkpoint ATIF during execution and flush an interrupted trajectory on timeout,
   cancellation, and process termination.
4. Add a run-level provider quota circuit breaker. Stop scheduling new work after the
   first confirmed quota error and preserve every completed trial.
5. Keep incomplete resumes local. Upload publicly only after final validation.

### Next iteration

1. Inject the remaining task budget into the agent context and switch to a
   verification-first strategy near the deadline.
2. Derive shell command limits from the remaining task budget.
3. Compact earlier for tool-heavy benchmark sessions, summarize large tool outputs,
   and avoid resending irrelevant schemas.
4. Retry structurally empty provider responses with a small bounded backoff.
5. Build or cache a benchmark-ready agent image to reduce repeated installation and
   setup work.

### Measurement work

1. Record per-call prompt, completion, tool-schema, and history token estimates.
2. Record compaction events and before/after context sizes in ATIF metadata.
3. Record the task deadline, remaining time, command timeout, and interruption reason.
4. Compare score, wall time, and token use on a fixed smoke subset before another
   445-trial run.

## Acceptance criteria for the next benchmark version

- Instructions beginning with `-`, multiline instructions, and quoted shell text
  reach the agent byte-for-byte.
- Benchmark trajectories expose only the approved coding tools.
- Every materialized trial has a parseable ATIF artifact, including timeout and
  cancelled trials.
- A quota response prevents new trials from starting within one scheduler cycle.
- Resuming a local job does not create duplicate public rows.
- The smoke subset shows at least a 50% reduction in median cumulative input tokens
  without a reward regression.
- Long shell operations can use the remaining task budget while reserving time for
  verification and ATIF flush.
- Final validation confirms 89 tasks with five trials each, no unresolved provider or
  setup errors, and valid trajectories for every rewarded trial.
- Only the validated final job is uploaded publicly and used for leaderboard
  metadata or a submission pull request.

## Safe resume and publication workflow

Resume quota-failed trials locally after provider access returns:

```bash
harbor job resume \
  --job-path jobs/libre-claw-glm52-tbench21-public-20260718-v2 \
  --filter-error-type ApiRateLimitError \
  -y
```

Do not include `--upload` or `--public` on an incomplete resume. After all 445 trial
slots are present and validation passes, upload the finished job once:

```bash
harbor upload --public \
  jobs/libre-claw-glm52-tbench21-public-20260718-v2
```

The provider key must remain in the process environment or an approved secret store.
It must never be written into this report, the job configuration, trajectories, or
version control.

## Report limitations

This report describes a partial, interrupted evaluation. It is intended to guide
harness engineering and does not claim a final model score, official leaderboard
position, or statistically complete comparison. The preserved local artifacts remain
the source of truth for the figures above.
