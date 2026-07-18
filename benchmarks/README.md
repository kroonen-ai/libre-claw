# Libre Claw Benchmarks

Libre Claw can be evaluated as a real installed agent through
[Harbor](https://www.harborframework.com/), the runner used by Terminal-Bench.
The adapter invokes the same provider, ReAct loop, permissions, and built-in
tools as the TUI through `libre-claw run`; it does not replace the harness with
a direct model client.

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

Always record the Libre Claw commit, Harbor version, dataset version, task IDs,
trial count, and model identifier alongside a score. A small sample is a smoke
evaluation, not a leaderboard-comparable result.
