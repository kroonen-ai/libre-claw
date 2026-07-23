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
