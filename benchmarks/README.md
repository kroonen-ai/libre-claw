<!-- Copyright 2026 Kroonen AI (https://kroonen.ai); SPDX-License-Identifier: Apache-2.0 -->

# Libre Claw Benchmarks

Libre Claw can be evaluated as a real installed agent through
[Harbor](https://www.harborframework.com/), the runner used by Terminal-Bench.
The adapter invokes the same provider, ReAct loop, permissions, and built-in
tools as the TUI through `libre-claw run`; it does not replace the harness with
a direct model client.

The benchmark profile deliberately keeps only the coding tools needed by
Terminal-Bench, compacts context earlier than an interactive session, allows
long build/test commands, offers validated multi-file patches and managed
process sessions, and writes atomic ATIF checkpoints throughout the run.
Harbor instructions are piped over stdin, so task text beginning with a hyphen
cannot be parsed as a Libre Claw option.

## Terminal-Bench

Install Harbor in an isolated environment and expose the Ollama Cloud key only
to the benchmark process:

```bash
python3.13 -m venv ~/.cache/libre-claw/harbor-py313-venv
~/.cache/libre-claw/harbor-py313-venv/bin/pip install harbor
export OLLAMA_API_KEY="..."
export LIBRE_CLAW_EVAL_REF="$(git rev-parse HEAD)"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

Harbor currently supports Python 3.12 and 3.13. Keep this runner environment
separate if Libre Claw itself is installed under Python 3.14.

Run a local smoke task first:

```bash
~/.cache/libre-claw/harbor-py313-venv/bin/harbor run \
  -p /path/to/harbor/examples/tasks/hello-world \
  -a benchmarks.harbor.libre_claw_agent:LibreClawAgent \
  -m ollama/glm-5.2:cloud
```

Then run Terminal-Bench 2.1, selecting task IDs or a bounded sample before a
full benchmark:

```bash
~/.cache/libre-claw/harbor-py313-venv/bin/harbor run \
  -d terminal-bench/terminal-bench-2-1 \
  -a benchmarks.harbor.libre_claw_agent:LibreClawAgent \
  -m ollama/glm-5.2:cloud
```

Run the balanced three-task smoke sample used by the project:

```bash
~/.cache/libre-claw/harbor-py313-venv/bin/harbor run \
  -d terminal-bench/terminal-bench-2-1 \
  -i terminal-bench/fix-git \
  -i terminal-bench/regex-log \
  -i terminal-bench/fix-code-vulnerability \
  -a benchmarks.harbor.libre_claw_agent:LibreClawAgent \
  -m ollama/glm-5.2:cloud \
  -n 1
```

See [results/terminal-bench-2.1-glm-5.2-cloud-2026-07-18.md](results/terminal-bench-2.1-glm-5.2-cloud-2026-07-18.md)
for the first recorded result.

## Interrupted runs

Keep interrupted or quota-limited jobs local. Resume the same job and rerun
only the matching transient failures so completed trials remain intact:

```bash
export OLLAMA_API_KEY="..."
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

~/.cache/libre-claw/harbor-py313-venv/bin/harbor job resume \
  --job-path jobs/<job-name> \
  --filter-error-type ApiRateLimitError \
  -y
```

Use the corresponding error type for verified transient setup or provider
failures. Do not publish a partial job. After all expected tasks and attempts
are present, inspect rewarded trajectories, confirm their ATIF files are valid,
and only then upload the completed job:

```bash
~/.cache/libre-claw/harbor-py313-venv/bin/harbor upload \
  --job-path jobs/<job-name> \
  --public \
  -y
```

Always record the Libre Claw commit, Harbor version, dataset version, task IDs,
trial count, and model identifier alongside a score. A small sample is a smoke
evaluation, not a leaderboard-comparable result.

## Fixed coding workflow regression suite

Run the small versioned suite before comparing harness changes:

```bash
.venv/bin/python -m benchmarks.coding_workflow.runner --offline
.venv/bin/python -m benchmarks.coding_workflow.runner --provider openrouter --model vendor/model-id
```

The two coding tasks cover pagination boundaries and a change across multiple
modules with nested project instructions. Their starters must fail their tests;
completion requires passing the tests while preserving test and instruction files.
A third task runs the production independent reviewer against two seeded bugs and
a harmless refactor. Findings must identify the changed file and line and explain
the trigger and consequence. The rubric reports precision, recall, and false
positives, including duplicate findings and complaints about the harmless change.
Malformed or ungrounded output fails; the ground truth is kept outside the workspace.

The recovery check interrupts an agent after a tool has changed a ledger but before
its result arrives. It loads the actual `RunStore` snapshot through a fresh store,
verifies runtime, workspace, requirements and decisions, inspects the uncertain
side effect, and completes each phase exactly once. This is a scripted semantic
recovery check, not a measurement of model recovery quality.

Offline mode feeds checked-in solutions through the real headless agent and file
tools. CI runs this as a fixture and harness gate. It does not measure model quality.
Live mode uses the configured provider through the same `run_headless` entry point
and ATIF v1.7 trajectories used by the Harbor adapter; it requires explicit provider
and model IDs and accepts `--config`, `--task`, `--timeout` (at most 180 seconds per
task), and `--output` for a new results directory. It disables fallback so a
score is attributed to the requested model. Codex uses its installed CLI and login;
API providers use the normal configured credentials. Runs approve coding tools only
inside disposable fixture workspaces.

The printed results path contains JSON with task checks, fixture hashes, source
revision, configuration, elapsed time, provider token usage, and cost when reported.
Unknown token usage or cost stays null. Model completion rate includes only live
coding and review tasks; scripted recovery is reported separately. Workspaces and trajectories
remain available beside the report for inspection. These tiny tasks are regression
checks, not a substitute for a larger Harbor evaluation or a leaderboard score.

### Compare providers and models

Create a JSON file with explicit targets (model IDs come from each provider's
current catalog):

```json
{
  "targets": [
    {"id": "target-a", "provider": "codex", "model": "MODEL_ID"},
    {"id": "target-b", "provider": "openrouter", "model": "VENDOR/MODEL_ID"}
  ]
}
```

Each target can include `config`, a TOML path relative to the targets file; leave
credentials in the normal environment or credential store. Run a bounded trial:

```bash
.venv/bin/python -m benchmarks.coding_workflow.comparison \
  --targets targets.json --trials 1 --timeout 180 --output /tmp/workflow-comparison
```

Use `--offline` to validate the comparison without provider calls. The runner accepts
2-8 targets, 1-3 trials, and unique `--task` selections. Every target gets identical
fixture hashes and isolated workspaces; failures remain in the report. Summaries
include completion rate, review precision/recall, runtime, input/output/cached tokens,
and provider-reported cost. A missing cost makes the aggregate unknown, not zero.
The API completion limit is 4,096 tokens per response and the tool budget is 30.
The Codex CLI bridge cannot enforce token or native tool-call limits; its wall-clock
deadline still applies, and the report records this difference.

`comparison.json` uses relative artifact paths and removes local user/workspace
prefixes. It records source revision, dirty state, fixture and source hashes before
and after execution. A changed source tree is explicitly flagged; compare stable
revisions for meaningful harness comparisons. Trial workspaces, final responses,
durable recovery snapshots, and coding ATIF trajectories remain in the output
directory. Keep raw local artifacts private unless they have been inspected.

The [September 21 smoke comparison](results/coding-workflow-comparison-2026-09-21.json)
completed all three tasks on both Codex `gpt-6-astra` and OpenRouter
`z-ai/glm-5.3-flash`. Both reviews found the two seeded bugs without a false positive.
The OpenRouter run reported $0.009624435; Codex cost was unavailable. All six
attempts used a frozen source snapshot with matching source hashes before and
after execution. The snapshot includes the recorded uncommitted changes. These
small, single-trial checks are smoke evidence rather than a provider ranking.
