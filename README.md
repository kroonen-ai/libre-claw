# Libre Claw

Libre Claw is a terminal-native autonomous coding agent harness built by
[Kroonen AI](https://kroonen.ai). It gives you a serious local TUI,
Telegram control, durable runs, persistent memory, browser tools, scheduled
tasks, and multi-provider model routing in one Python application.

It is built for developers who want an agent that can actually work in a
project, ask before side effects, remember useful context, and keep running
when the terminal closes.

Current release: Version `0.1.0`.

![Libre Claw terminal UI](docs/assets/libre-claw-terminal-screenshot.png)

## Highlights

| Capability | What it means |
| --- | --- |
| Terminal UI | Streaming chat, file explorer, command palette, approvals, run timeline, and artifacts. |
| Telegram bridge | Talk to the same agent from Telegram, approve tools inline, and receive scheduled reports. |
| Durable runs | Every task gets a run ID, JSONL event log, summary, verification notes, and optional diff. |
| Local dashboard | Start, inspect, cancel, and approve daemon-owned runs from a browser on localhost. |
| Memory and skills | Local persistent memory, `SOUL.md` persona files, user/project `SKILL.md` workflows, and optional Vercel Skills discovery. |
| Real tools | File edits, shell, code search, web search, git, HTTP requests, browser actions, screenshots, MCP tools, and more. |
| Provider routing | OpenRouter, Ollama/Ollama Cloud, Anthropic, OpenAI, Codex OAuth, and local-compatible endpoints. |
| Petdex companion | Optional local state updates for the Petdex desktop companion app. |
| Safe defaults | API keys stay out of project config, dangerous commands are blocked, and writes require approval. |

## Install

Recommended local install:

```bash
git clone https://github.com/kroonen-ai/libre-claw.git
cd libre-claw
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

One-command installer:

```bash
curl -fsSL https://raw.githubusercontent.com/kroonen-ai/libre-claw/main/scripts/install.sh | sh
```

For browser tools:

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

For local web search, run a private SearXNG instance:

```bash
libre-claw searx init
libre-claw searx up
libre-claw searx test
```

Libre Claw uses `http://127.0.0.1:8888` by default through the `web_search`
tool. The generated SearXNG settings enable JSON output, which is required for
agent searches. Once `searx init` has written the files, `libre-claw start`
also brings the container up automatically, so manual `searx up` is only needed
for the first setup or explicit control. The implementation walkthrough lives
in [docs/SEARXNG_INTEGRATION.md](docs/SEARXNG_INTEGRATION.md).

Update an installed Git checkout safely:

```bash
libre-claw update
```

The updater fetches `origin/main`, compares commits, writes a backup under
`~/.libre-claw/backups/updates/`, then applies a fast-forward update. It refuses
to pull over uncommitted changes; use `libre-claw update --dry-run` to check
first.

Optional Petdex companion integration:

```toml
[petdex]
enabled = true
base_url = "http://127.0.0.1:7777"
token_path = "~/.petdex/runtime/update-token"
source = "libre-claw"
bubble_prefix = "🦞"
```

When enabled, Libre Claw sends local lifecycle updates to Petdex for daemon,
TUI, Telegram, scheduled runs, tool calls, approvals, success, and errors. Use
`/petdex status` in the TUI or Telegram to verify the token and endpoint. The
TUI also renders the active Petdex sprite from `~/.petdex` when the companion is
enabled and a pet is installed.

## First Run

Start the TUI:

```bash
libre-claw
```

Equivalent entrypoints:

```bash
libre-claw tui
libre-claw chat
python -m libre_claw
```

The TUI runs full-screen by default. Use `PageUp` / `PageDown` to scroll the
transcript, `Ctrl+Home` / `Ctrl+End` to jump, and `Ctrl+Shift+C` to copy the
current Textual selection. If you want normal terminal scrollback instead, launch
with `libre-claw tui --inline`. If you want clickable Textual mouse controls,
launch with `libre-claw tui --mouse` or set `[tui].mouse = true`.

File edits are rendered as compact unified diffs. Press `Ctrl+E` or run
`/review` to open the focused change drawer, then use its Previous/Next controls
to inspect every edit without digging through tool output. Permission prompts
show the exact patch, file contents, or shell command before approval.

You can attach images in the TUI by dragging an image into the terminal or
pasting its local path into a message. Use `/attach <image-path>` to queue an
image for the next prompt, `/attach paste` or `/paste-image` to pull an image
from the OS clipboard, `/attach list` to inspect queued images, and `/attach
clear` to reset them. Libre Claw renders a small terminal preview and sends the
image to vision-capable providers.

Inside the app, run:

```text
/setup status
/setup provider openrouter
/setup key openrouter
/model openrouter:openrouter/auto --global
```

The `--global` flag saves the selected provider/model to
`~/.libre-claw/config.toml`.

## Provider Setup

Libre Claw does not need real API keys in project files. Use the key store:

```bash
libre-claw auth set-key openrouter
libre-claw auth set-key anthropic
libre-claw auth set-key openai
libre-claw auth set-key ollama
libre-claw auth status
```

Or use environment variables:

```bash
export OPENROUTER_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export OPENAI_API_KEY="..."
export OLLAMA_API_KEY="..."
```

Key lookup order:

1. Environment variable.
2. OS keyring.
3. Encrypted local fallback file at `~/.libre-claw/.keys`.

### Common Model Commands

```text
/model list
/model openrouter:qwen/qwen3.7-max --global
/model openrouter:sakana/fugu-ultra --global
/model openrouter:deepseek/deepseek-v4-flash --global
/model openrouter:moonshotai/kimi-k2.7-code --global
/model openrouter:z-ai/glm-5.2 --global
/model openrouter:minimax/minimax-m3 --global
/model openrouter:anthropic/claude-sonnet-5 --global
/model openrouter:nvidia/nemotron-3-ultra-550b-a55b:free --global
/model ollama:glm-5.2:cloud --global
/model ollama:minimax-m3:cloud --global
/model ollama:kimi-k2.6:cloud --global
/model anthropic:claude-sonnet-5 --global
/model anthropic:claude-opus-4-8 --global
/model openai:gpt-5.5 --global
/model codex:gpt-5.5 --global
```

Fallback slots let Libre Claw keep working if the primary provider is rate-limited
or down before it starts streaming. Configure up to three ordered backups:

```text
/fallback list
/fallback set 1 openrouter:openrouter/auto --global
/fallback set 2 ollama:kimi-k2.6:cloud --key-env OLLAMA_BACKUP_API_KEY --global
/fallback set 3 anthropic:claude-sonnet-5 --global
/fallback recheck 3 --global
/fallback clear all --global
```

While running on a fallback, Libre Claw retries the primary after the configured
number of fallback provider calls. The default is `3`.

## Local Web Search

`web_search` is backed by SearXNG so the agent can search the web without
scraping a commercial search page through `bash` or the browser. The default
config is:

```toml
[web_search]
enabled = true
provider = "searxng"
base_url = "http://127.0.0.1:8888"
max_results = 10
```

Useful commands:

```bash
libre-claw searx init      # write compose/settings files
libre-claw searx up        # start local SearXNG with Docker Compose
libre-claw searx status    # show container status
libre-claw searx test      # verify JSON search output
libre-claw searx down      # stop local SearXNG
```

Implementation notes and debugging tips:
[docs/SEARXNG_INTEGRATION.md](docs/SEARXNG_INTEGRATION.md).

Use `/provider` when you only want to switch providers:

```text
/provider openrouter
/provider ollama
/provider anthropic
/provider openai
/provider codex
```

### Codex / ChatGPT Login

Codex uses the supported Codex CLI login flow instead of an OpenAI API key:

```bash
libre-claw auth codex-login
```

Or inside the TUI:

```text
/codex login
/provider codex
/model codex:gpt-5.5 --global
```

## Run Surfaces

### TUI

```bash
libre-claw tui
```

Best for interactive coding, approvals, file browsing, artifacts, and local
work.

### Daemon And Dashboard

```bash
libre-claw start
libre-claw daemon
```

Open:

```text
http://127.0.0.1:8766/dashboard
```

The daemon owns active runs, keeps them alive after the TUI exits, exposes a
local dashboard, supervises schedules, and can start Telegram automatically
when Telegram is enabled and a stored bot token exists.

![Libre Claw dashboard GUI](docs/assets/libre-claw-dashboard-screenshot.png)

Lifecycle helpers:

```bash
libre-claw shutdown
libre-claw restart
```

`restart` reuses the last recorded mode, so a running `telegram up` stack comes
back as `telegram up`. Logs go to `~/.libre-claw/daemon.log`.

Use `libre-claw stop` when you want to cancel the active daemon turn without
shutting down Libre Claw.

### Telegram

```bash
libre-claw telegram setup --user-id 123456789
libre-claw telegram up
```

Telegram uses your numeric Telegram user ID, not your `@username`. If you are
blocked, the bot replies with the exact allow command to run.

Telegram messages can include photos or image documents. Libre Claw stores
uploads locally under `~/.libre-claw/telegram/uploads/`, passes them to
vision-capable providers, and keeps captions as the user prompt.

Useful Telegram commands:

```text
/start
/help
/new
/restart
/model
/models
/provider
/cost
/usage
/status
/daemon
/runs
/run
/compact
/schedule
/heartbeat
/memory
/cancel
/stop
/shutdown
/btw
/steer
```

![Libre Claw Telegram bot](docs/assets/libre-claw-telegram-screenshot.png)

## What The Agent Can Do

Libre Claw ships with production-oriented tools for:

- Reading, writing, editing, searching, and listing project files.
- Running shell commands with timeout, truncation, and permission checks.
- Inspecting git status and creating commits with approval.
- Making direct HTTP requests for APIs and downloads.
- Browsing pages with persistent Playwright profiles.
- Clicking, typing, waiting, extracting page data, dismissing cookie banners,
  taking screenshots, and saving downloads.
- Creating, updating, pausing, resuming, deleting, and listing Libre Claw
  schedules through the same permission system.
- Calling configured MCP tools through the same permission system.

Write/edit/shell/browser action tools ask first. Read-only tools are allowed by
default.

## Core Workflows

### Durable Runs

Every user task gets a run under:

```text
~/.libre-claw/runs/<run-id>/
```

Each run can include:

- `events.jsonl`
- `summary.md`
- `verification.md`
- `diff.patch`
- browser artifacts
- tool and permission events

Useful commands:

```text
/runs
/run <id>
/resume <id>
/cancel <id>
/artifacts summary <id>
/changes <id>
/approvals
```

### Persistent Memory

Libre Claw stores local memory in three layers:

- Raw session archives in `~/.libre-claw/sessions/`.
- Durable run archives in `~/.libre-claw/runs/`.
- Searchable memory items in `~/.libre-claw/memory.db`.

It can automatically extract durable facts, preferences, project decisions, and
workflow summaries after completed runs. Credential-looking data is redacted
before indexing or injection.

Useful commands:

```text
/memory status
/memory list
/memory search <query>
/memory add <text>
/memory forget <id>
/memory summarize
/memory on
/memory off
```

### Skills And Persona

Libre Claw loads reusable skills from:

```text
~/.libre-claw/skills/
<project>/.libre-claw/skills/
```

AgentSkills-style packages with `SKILL.md` are supported. Libre Claw can also
opt into the open Vercel Skills ecosystem by caching
[`vercel-labs/skills`](https://github.com/vercel-labs/skills) and exposing a
read-only `skills_search` tool to the agent.

Enable external skill discovery in `~/.libre-claw/config.toml`:

```toml
[skills]
enabled = true
external_discovery_enabled = true
external_auto_refresh = true
vercel_source_enabled = true
cli_command = "npx -y skills@latest"
```

Persona files are loaded from:

```text
~/.libre-claw/SOUL.md
<project>/.libre-claw/SOUL.md
<project>/SOUL.md
```

Useful commands:

```text
/skills list
/skills sync
/skills show <name>
/skills show --external find-skills
/skills add --project <name>
/soul status
/soul init --project
/soul show
```

See [docs/VERCEL_SKILLS_INTEGRATION.md](docs/VERCEL_SKILLS_INTEGRATION.md) for
the full Vercel Skills setup and maintenance notes.

### Scheduled Work And Heartbeats

Create recurring local runs:

```text
/schedule examples
/schedule add daily 09:00 | Daily repo health check | Inspect git status, tests, and risks.
/schedule add daily 08:00 @ America/Montreal | Morning brief | Send a compact morning report.
/schedule list
/schedule pause <id>
/schedule resume <id>
```

The agent can also create and edit Libre Claw schedules itself with the
`schedule_list` and `schedule` tools. These are daemon automations, not host
cron entries, so they stay portable across TUI, dashboard, and Telegram.
Use an IANA timezone suffix such as `@ America/Montreal` for location-specific
jobs; otherwise schedules use the daemon's local timezone.

Start lightweight periodic check-ins:

```text
/heartbeat status
/heartbeat once
/heartbeat start every 30 minutes
/heartbeat stop
```

### Goal Mode

Use `/goal` for bounded autopilot work:

```text
/goal Fix the failing tests and explain the result
```

Libre Claw runs a normal agent turn, asks a separate judge model whether the
goal is complete, then continues until the judge marks it done or the turn
limit is reached.

## Important Slash Commands

```text
/help
/clear
/cancel
/exit
/cost
/compact
/model [provider:]<model> [--global]
/fallback list|set|clear|recheck
/provider <name>
/setup status
/tools list
/runs
/memory status
/skills list
/workspace status
/telegram
/petdex status
```

Keybindings:

- `Ctrl+B`: toggle file explorer.
- `Ctrl+P`: command palette.
- `Ctrl+Shift+C`: copy last assistant response.
- `Esc`: cancel active generation/tool execution.
- `Ctrl+C`: exit the app.
- `Tab`: accept the first slash-command suggestion.

## Configuration

Main config:

```text
~/.libre-claw/config.toml
```

Show bundled defaults:

```bash
libre-claw config defaults
```

Common environment overrides:

```bash
LIBRE_CLAW_DEFAULT_PROVIDER=openrouter
LIBRE_CLAW_DEFAULT_MODEL=openrouter/auto
LIBRE_CLAW_WORKING_DIRECTORY=/path/to/project
```

Initialize a dedicated workspace:

```bash
libre-claw workspace init
```

By default, this creates:

```text
~/Documents/.workspace/libre-claw
```

## Safety Model

Libre Claw is powerful because it can touch your files, shell, browser, and git
history. The default posture is local-first and permissioned:

- API keys are read from environment variables, keyring, or encrypted local
  fallback storage.
- Provider keys are not written to project config files.
- File writes, edits, shell commands, browser navigation/actions, downloads,
  git commits, and MCP actions ask for approval.
- Dangerous shell patterns are blocked by the sandbox layer.
- File access is restricted to the configured working directory by default.
- Memory redacts credential-looking strings before indexing or prompt
  injection.
- Runs are append-only and inspectable through JSONL logs and artifacts.

## Troubleshooting

### macOS says `externally-managed-environment`

Use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### Missing API key

```bash
libre-claw auth status
libre-claw auth set-key openrouter
```

Replace `openrouter` with `anthropic`, `openai`, or `ollama`. For Codex:

```bash
libre-claw auth codex-login
```

### Ollama Cloud returns 401

Make sure you stored the real Ollama API key, not the model name:

```bash
libre-claw auth set-key ollama
```

For direct Ollama Cloud API use:

```toml
[providers.ollama]
base_url = "https://ollama.com"
api_format = "ollama"
api_key_env = "OLLAMA_API_KEY"
```

For local Ollama daemon use:

```toml
[providers.ollama]
base_url = "http://localhost:11434"
api_format = "ollama"
api_key_env = ""
```

## Documentation

- Website: [libreclaw.sh](https://libreclaw.sh)
- Docs: [libreclaw.sh/docs](https://libreclaw.sh/docs/)
- Getting started: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)
- SearXNG integration: [docs/SEARXNG_INTEGRATION.md](docs/SEARXNG_INTEGRATION.md)
- Vercel Skills integration: [docs/VERCEL_SKILLS_INTEGRATION.md](docs/VERCEL_SKILLS_INTEGRATION.md)
- Security: [SECURITY.md](SECURITY.md)
- Roadmap: [ROADMAP.md](ROADMAP.md)
- Demos: [docs/DEMOS.md](docs/DEMOS.md)

## Development

```bash
python -m pytest
python -m compileall src tests
git diff --check
```

Libre Claw is released under Apache-2.0 by Kroonen AI.
