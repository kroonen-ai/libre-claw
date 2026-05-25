# Libre Claw

Libre Claw is a terminal-native AI coding agent harness from Kroonen AI Inc.
(https://kroonen.ai). It runs as a single Python TUI app, streams model output,
uses permissioned tools for coding tasks, supports Telegram, and keeps provider
keys out of project config files.

Version `0.1.0` is the first shippable release. It is early, but functional:
you can launch the TUI, choose Anthropic, OpenAI, OpenRouter, Ollama, or Codex, chat
with the agent, approve tool calls, browse files, save sessions, use memory,
and run the Telegram daemon.

![Libre Claw terminal UI](docs/assets/libre-claw-terminal-screenshot.png)

## What You Get

- Textual terminal UI with streaming Markdown chat.
- Providers: Anthropic, OpenAI, OpenRouter, Ollama, and Codex CLI auth.
- Ollama support for local daemon use, Ollama Cloud, and OpenAI-compatible
  Ollama endpoints.
- Built-in tools: `read_file`, `write_file`, `edit_file`, `list_directory`,
  `glob`, `search_files`, `git_status`, `git_commit`, `think`,
  `browser_navigate`, `browser_read`, `browser_extract`, `browser_execute`,
  `browser_dismiss_cookies`, `browser_click`, `browser_type`, `browser_wait`,
  `browser_download`, `browser_screenshot`, `http_request`, and `bash`.
- `/goal` supervised mode that keeps the agent working for up to a bounded
  number of turns until a separate judge model marks the objective complete.
- Durable local runs with IDs, append-only event logs, run artifacts, and
  `/runs`, `/run <id>`, `/resume <id>`, and `/cancel <id>` controls.
- Human-review cockpit with run timeline replay, Plan/Summary/Verify/Diff/Browser
  artifact panel, blocked approval inbox, and “what changed since I left”
  summaries.
- Background daemon API for daemon-owned runs, event polling, cancel, and
  permission approval.
- Optional TUI daemon mode so the TUI can start and poll daemon-owned runs
  instead of owning execution itself.
- Recurring local automations with `/schedule`, cron-like schedules, daemon
  execution, saved reports, and TUI/Telegram route metadata.
- Browser/computer-use tools with persistent profiles, CSS selector actions,
  page-data extraction, JavaScript execution, cookie-consent dismissal,
  downloads, screenshots, browser artifacts, and domain allow/deny policy.
- OpenRouter growth analytics with app-attribution verification, recommended
  model presets, and persistent `/usage openrouter` rollups by model, run, and
  user surface.
- Competitive polish: one-command install script, first-run `/setup` wizard,
  public roadmap, demo scripts, and a security page.
- MCP stdio integration for explicitly configured and allowlisted external
  tools, surfaced through the normal tool registry and permission system.
- User and project skills loaded from `~/.libre-claw/skills/` and
  `.libre-claw/skills/`, with AgentSkills-style `SKILL.md` discovery.
- Interactive permission prompts for write/edit/shell actions.
- File explorer, hidden on startup, whose root can move up and down with the user.
- SQLite-backed memory, saved sessions, and context compaction.
- Secure API key storage through environment variables, OS keyring, or an
  encrypted fallback file.
- Telegram daemon with allowlist auth.

## Requirements

- Python 3.11 or newer.
- A provider API key if you use a cloud provider.
- Optional: Ollama installed locally if you want local daemon mode.
- Optional: a Telegram bot token if you use the Telegram daemon.
- Optional: Playwright installed if you want headless browser tools.

## Install From This Repo

One-command install:

```bash
curl -fsSL https://raw.githubusercontent.com/kroonen-ai/libre-claw/main/scripts/install.sh | sh
```

For private/self-hosted remotes:

```bash
LIBRE_CLAW_REPO_URL=https://git.kroonen.ai/kroonen-ai/libre-claw.git \
  curl -fsSL https://raw.githubusercontent.com/kroonen-ai/libre-claw/main/scripts/install.sh | sh
```

Use a virtual environment. This avoids macOS/Homebrew's
`externally-managed-environment` pip error.

```bash
git clone https://github.com/kroonen-ai/libre-claw.git
cd libre-claw
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

For browser tools:

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

Run the app:

```bash
libre-claw
```

or:

```bash
python -m libre_claw
```

Build and install a wheel locally:

```bash
python -m build
python -m pip install dist/libre_claw-0.1.0-py3-none-any.whl
```

## First Provider Setup

Libre Claw never needs real API keys in TOML. Use environment variables:

```bash
export ANTHROPIC_API_KEY="..."
export OPENAI_API_KEY="..."
export OPENROUTER_API_KEY="..."
export OLLAMA_API_KEY="..."
```

or store them securely:

```bash
libre-claw auth set-key anthropic
libre-claw auth set-key openai
libre-claw auth set-key openrouter
libre-claw auth set-key ollama
libre-claw auth codex-login
libre-claw auth status
```

Key lookup order is:

1. Environment variable.
2. OS keyring.
3. Encrypted fallback file at `~/.libre-claw/.keys`.

Codex is different: `/codex login` and `libre-claw auth codex-login` use the
supported Codex CLI ChatGPT login flow. Libre Claw does not read or copy Codex
private token files.

## Fast Starts

### OpenRouter

```bash
libre-claw auth set-key openrouter
libre-claw
```

Inside the TUI:

```text
/setup status
/setup provider openrouter
/setup key openrouter
/model openrouter:openrouter/auto
```

Libre Claw always sends OpenRouter app attribution as `Libre Claw` from
`https://kroonen.ai`, including the `cli-agent` category. Users do not configure
those product identity headers.

Useful OpenRouter commands:

```text
/usage openrouter
/usage openrouter attribution
/usage openrouter presets
```

### Ollama Cloud With Kimi K2.6

```bash
libre-claw auth set-key ollama
libre-claw
```

Inside the TUI:

```text
/model ollama:kimi-k2.6:cloud
```

For direct Ollama Cloud API use, your config should point at Ollama's cloud
host:

```toml
[general]
default_provider = "ollama"
default_model = "kimi-k2.6:cloud"

[providers.ollama]
base_url = "https://ollama.com"
default_model = "kimi-k2.6:cloud"
api_format = "ollama"
api_key_env = "OLLAMA_API_KEY"
```

If you want the local Ollama daemon to handle cloud auth instead:

```bash
ollama signin
ollama pull kimi-k2.6:cloud
```

```toml
[general]
default_provider = "ollama"
default_model = "kimi-k2.6:cloud"

[providers.ollama]
base_url = "http://localhost:11434"
default_model = "kimi-k2.6:cloud"
api_format = "ollama"
api_key_env = ""
```

### Anthropic

```bash
libre-claw auth set-key anthropic
libre-claw
```

Default model:

```toml
[general]
default_provider = "anthropic"
default_model = "claude-opus-4-6"
```

### OpenAI

```bash
libre-claw auth set-key openai
libre-claw
```

Default example:

```toml
[general]
default_provider = "openai"
default_model = "gpt-5.5"
```

### Codex / ChatGPT Login

If you want the Open-Claw-style path where users sign in with Codex/ChatGPT
instead of pasting an OpenAI API key, use the Codex provider:

```bash
libre-claw
```

Inside the TUI:

```text
/codex login
/provider codex
/model codex:gpt-5.5 --global
```

Equivalent terminal commands:

```bash
libre-claw auth codex-login
libre-claw auth codex-status
```

This delegates turns to `codex exec` using the user's Codex CLI login. It is a
provider bridge, not API-key storage.

## Model Switching

Use `/model` from inside the TUI:

```text
/model
/model list
/model kimi-k2.6:cloud
/model ollama:kimi-k2.6:cloud
/model openrouter:qwen/qwen3.7-max --global
/model openrouter:openrouter/auto
```

`/model <name>` changes the model for the current provider.
`/model <provider>:<name>` changes provider and model together.
Add `--global` to save the selected provider/model as the default in
`~/.libre-claw/config.toml`.
Press `Tab` after `/model ` to complete known presets.

Use `/provider` when you only want to switch provider:

```text
/provider anthropic
/provider openai
/provider openrouter
/provider ollama
/provider codex
```

Legacy configs that still say `default_provider = "local"` or
`[providers.local]` are accepted and normalized to `ollama`.

## TUI Commands

- `/help`
- `/clear`
- `/cancel`
- `/cost`
- `/usage openrouter|attribution|presets`
- `/model [provider:]<name>|list [--global]`
- `/provider anthropic|openai|openrouter|ollama|codex`
- `/setup status|provider|key|model|openrouter|ollama-cloud|codex`
- `/codex login|status|logout|use [model]`
- `/save [name]`
- `/load <name>`
- `/compact [status|--force] [--keep N]`
- `/goal <objective>|status|stop|max N`
- `/runs [N]`
- `/run <id>`
- `/resume <id>`
- `/artifacts [plan|summary|verify|diff|browser] [id]`
- `/approvals`
- `/changes [id]`
- `/tools list|expand|collapse|toggle <index>`
- `/skills list|show|add|edit|delete`
- `/soul status|show|init|reload`
- `/memory list|add <fact>|forget <id>`
- `/telegram`
- `/exit`

Useful keybindings:

- `Ctrl+B` toggles the file tree.
- The file tree also has a `Hide` button. When hidden, a left-side `Files`
  button brings it back.
- `Ctrl+P` opens the command palette.
- `Ctrl+Shift+C` copies the last assistant response.
- `Esc` or `/cancel` cancels active generation/tool execution.
- `Ctrl+C` exits the app.
- `Tab` completes the first slash-command suggestion.

## Context Tracking

The status bar includes a compact context meter:

```text
ctx [##--------]
```

The meter is an estimated-token view of the current system prompt, soul files,
summary, memory facts, and conversation against `[agent].context_window_tokens`.

Use `/compact status` for details, `/compact` for normal compaction, and
`/compact --force --keep 4` when you want to summarize aggressively while
keeping only the latest four messages.

## Goal Mode

Use `/goal` for bounded autopilot work:

```text
/goal Fix the failing tests and commit the result
```

Libre Claw runs one normal agent turn, asks a separate judge model whether the
goal is done, then reprompts the agent with the judge's next instruction until
the judge returns done or the max-turn limit is reached. The default limit is
20 turns.

Useful controls:

- `/goal status`
- `/goal stop`
- `/goal max 20`

The judge has no tools. It only reads the transcript and returns a strict
done/continue decision, so file and shell permissions still go through the
normal Libre Claw approval flow.

## Token And Cost Tracking

Libre Claw tracks provider-reported token usage cumulatively for the current
session. Use `/cost` in the TUI, or `/cost` in Telegram, to see total input,
output, cached, and reasoning tokens.

For OpenRouter, Libre Claw requests OpenRouter usage accounting on every
OpenRouter call, so `/cost` also shows the provider-reported request cost when
OpenRouter returns it. The status bar updates live with the same cumulative
session total.

Use `/usage openrouter` for persistent usage analytics from durable runs. It
rolls up provider-reported input/output/cached/reasoning tokens and cost by
OpenRouter model, run, and user surface such as `tui:chat`, `tui:goal`,
`daemon`, `telegram:daemon`, or `automation:report`.

OpenRouter-specific helpers:

- `/usage openrouter attribution` verifies Libre Claw is sending
  `HTTP-Referer: https://kroonen.ai`, `X-OpenRouter-Title: Libre Claw`, and
  `X-OpenRouter-Categories: cli-agent`.
- `/usage openrouter presets` prints recommended `/model openrouter:...`
  commands for higher-value coding, goal, and scheduled-check workflows.
- Analytics link: https://openrouter.ai/apps?url=https://kroonen.ai

## First-Run Setup

The TUI includes a first-run setup flow:

```text
/setup status
/setup provider openrouter
/setup key openrouter
/setup model openrouter:qwen/qwen3.7-max --global
```

`/setup key` hides the input and stores the key through the same environment,
keyring, or encrypted fallback path as the CLI `auth set-key` command. It does
not print the key into the transcript.

Use `/setup codex` for Codex/ChatGPT login through the supported Codex CLI
flow.

## Tool Permissions

Read/list/search/status/think/browser-read/browser-wait/screenshot tools run
without prompting. File writes, file edits, git commits, browser navigation,
browser click/type/download actions, and shell commands ask first.

Permission prompts render as an interactive panel with:

- Approve
- Deny
- Always Tool
- Always Command

They also accept `y`, `n`, `a`, and `!` shortcuts. Dangerous sandbox-blocked
bash commands show a warning and only allow one-time approval or denial.

## Durable Runs

Every chat turn and `/goal` objective creates a durable run under:

```bash
~/.libre-claw/runs/<run-id>/
```

Each run stores:

- `meta.json`
- `events.jsonl`
- `plan.md` with the first visible assistant plan before tool use, when one
  was produced.
- `summary.md` with the final assistant output or failure summary.
- `verification.md` with final state, recent tool outcomes, git status, and
  artifact notes.
- `diff.patch` with the tracked-file git diff at finish when Libre Claw is
  running inside a git repository.

Use `/runs` to list recent runs, `/run <id>` to inspect metadata and event
counts, `/resume <id>` to reload a run transcript into the TUI, and
`/cancel <id>` to mark a run cancelled. The diff artifact intentionally does
not embed untracked files; they are listed in `verification.md` through git
status so the user can decide how to handle them.

P1 review controls:

- `/artifacts summary <id>` opens the artifact panel. Switch tabs with the
  Plan, Summary, Verify, Diff, and Browser buttons.
- `/approvals` lists currently blocked run approvals from the durable run log
  plus the active local prompt, if any.
- `/changes <id>` shows new run events since your last review and records the
  current event id as seen.

## Background Daemon

P2 adds the local runner daemon:

```bash
libre-claw daemon
```

By default it listens on:

```text
http://127.0.0.1:8766
```

Useful API endpoints:

- `GET /health`
- `GET /runs?limit=20`
- `POST /runs` with `{"message": "..."}`
- `GET /runs/<run-id>`
- `GET /runs/<run-id>/events?after=<event-id>`
- `POST /runs/<run-id>/cancel`
- `POST /runs/<run-id>/permissions/<tool-call-id>` with
  `{"resolution": "allow_once"}`
- `GET /automations`
- `POST /automations` with `{"name": "...", "schedule": "daily 09:00", "prompt": "..."}`
- `POST /automations/<automation-id>/pause`
- `POST /automations/<automation-id>/resume`
- `DELETE /automations/<automation-id>`

The daemon owns active run tasks, writes to the same durable run store, and can
block a run on tool approval without losing its event history. This is the
backend connection point for TUI and Telegram surfaces to share the same active
run process.

The TUI can also connect to the daemon instead of owning the agent run:

```toml
[tui]
use_daemon = true
```

In daemon mode, normal chat messages become daemon-owned runs. The TUI polls
events into the same transcript, sends approval decisions back through the
daemon API, and can exit without killing the background run. Use `/resume <id>`
to reload and keep polling a running or blocked daemon run.

For security, daemon `POST /runs` requests cannot override `working_directory`.
Set the daemon working directory through config or `--working-directory` when
starting Libre Claw.

Telegram can also route work through the daemon instead of owning the run in
the bot process:

```toml
[telegram]
enabled = true
use_daemon = true
```

For users, the easy path is `libre-claw telegram up`, which starts the local
daemon and Telegram bridge together. Telegram inline approval buttons then
resolve the same daemon run through the local API, so the run can continue even
if another surface is watching it.

## Automations

P5 adds recurring local runs. The daemon watches
`~/.libre-claw/automations/`, starts due schedules as normal durable runs, and
writes saved reports under:

```text
~/.libre-claw/automations/reports/<automation-id>/<run-id>.md
```

The TUI command surface:

```text
/schedule list
/schedule examples
/schedule add daily 09:00 | Daily repo health check | Inspect git status, tests, and risks.
/schedule add weekly mon 10:00 | Weekly dependency review | Review dependency files and CI.
/schedule add every 30 minutes | Morning brief | Summarize active runs and priorities.
/schedule pause <automation-id>
/schedule resume <automation-id>
/schedule delete <automation-id>
```

Supported schedules are `daily HH:MM`, `weekly mon HH:MM`, `every N minutes`,
`hourly`, and a five-field cron subset. Use `--route report`, `--route tui`, or
`--route telegram` on `/schedule add` to record how the result should be
surfaced. `report` writes the saved report, `tui` keeps the run visible through
`/runs` and `/resume`, and `telegram` stores the chat id when created through
Telegram `/schedule`.

The bundled examples cover:

- Daily repo health check.
- Weekly dependency review.
- Morning brief.

## Browser / Computer Use

P6 upgrades browser tools from a single transient page into persistent
Playwright profiles. Install the optional extra first:

```bash
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

Available browser tools:

- `browser_navigate`: open an HTTP(S) URL in a named persistent profile.
- `browser_read`: read visible text from `body` or a CSS selector.
- `browser_extract`: extract image URLs, links, metadata, and JSON-LD
  structured data from the current page without relying on visible text.
- `browser_execute`: run JavaScript in the current page context and return the
  serialized result. This requires approval.
- `browser_dismiss_cookies`: retry common cookie-consent dismissal selectors on
  the current page.
- `browser_click`: click a CSS selector.
- `browser_type`: type/fill text into a CSS selector, optionally pressing Enter.
- `browser_wait`: wait for a selector state or page load state.
- `browser_download`: click a selector that starts a download and save it.
- `browser_screenshot`: capture the full page or a CSS selector.

`browser_navigate` tries to dismiss common cookie banners after page load by
default. Browser state is kept in a process-level pool per profile/config so
the live Playwright page survives registry rebuilds and follow-up tool calls.
The default profile is `default`, and login/session storage lives under:

```text
~/.libre-claw/browser/profiles/<profile>/
```

Screenshots and downloads are saved inside the working directory by default:

```text
.libre-claw/browser/screenshots/
.libre-claw/browser/downloads/
```

When a run finishes, browser screenshots and downloads are summarized in
`browser.md`. Use `/artifacts browser <run-id>` to inspect those paths and
Markdown screenshot previews.

Safe domain policy is configured in TOML:

```toml
[browser]
allowed_domains = []      # empty means allow any HTTP(S) host not denied
denied_domains = []
profile_dir = "~/.libre-claw/browser/profiles"
downloads_dir = ".libre-claw/browser/downloads"
screenshots_dir = ".libre-claw/browser/screenshots"
default_timeout_ms = 30000
headless = true
```

Domain entries match the exact host and subdomains. Use `*.example.com` for a
wildcard-style suffix rule.

## HTTP Request Tool

`http_request` gives the agent a direct HTTP path for APIs, image URLs, and
downloads without going through `bash`.

- Safe `GET` and `HEAD` requests with no body and no output file are
  auto-approved when read tools are auto-approved.
- `POST`, `PUT`, `PATCH`, `DELETE`, request bodies, and downloads require
  approval.
- `output_path` saves the response body inside the configured working directory
  and is checked by the same sandbox path policy as file tools.
- The browser domain allow/deny lists also apply to `http_request`.

## MCP Tools

P4 adds a first MCP bridge for stdio MCP servers. Libre Claw only exposes tools
that are explicitly configured on a server and optionally present in the global
allowlist. Exposed names use the form `mcp__server__tool`.

```toml
[mcp]
enabled = true
allowlist = ["demo.echo"]
permission_level = "ask"
tool_timeout = 30

[mcp.servers.demo]
command = ["python", "/path/to/mcp_server.py"]
tools = ["echo"]
```

Use `/tools list` to see built-ins plus exposed MCP tools. MCP calls go through
the same permission system as other tools, and the model receives only the
configured tool names for the current run.

## Skills

P3 adds lightweight, file-based skills. Skills are Markdown procedures the
agent can pull into its system prompt when the current request matches the
skill title, description, or body.

Libre Claw loads skills from:

- `~/.libre-claw/skills/*.md` for global user skills.
- `<project>/.libre-claw/skills/*.md` for project skills.
- `<project>/.libre-claw/skills/<name>/SKILL.md` and the same layout under the
  user skills directory for AgentSkills-style packages.

Manage them from the TUI:

```text
/skills list
/skills show --project pytest-debug
/skills add --user pytest-debug # Pytest Debug
/skills add --project release-flow # Release Flow
/skills edit --project release-flow # Release Flow Updated
/skills delete --project release-flow
```

The TUI, Telegram bridge, and background daemon all use the same skill loader.
When the agent sees a repeatable workflow that is not covered, the system prompt
asks it to suggest a `/skills add <name> ...` command at the end of the task.

## File Explorer

The file explorer has an `Up` control. Moving the explorer root also updates
the agent working directory, so tools follow the directory you are browsing.

On startup, the TUI shows Libre Claw ASCII art and a collapsed version header.
Click the header to reveal the latest release notes from `RELEASE.md`.

## Telegram

<img src="docs/assets/libre-claw-telegram-screenshot.jpeg" alt="Libre Claw Telegram bot screenshot" width="360">

The simple setup flow stores the bot token securely through keyring or Libre
Claw's encrypted fallback file, enables Telegram, allowlists your Telegram user
ID, and defaults Telegram runs to daemon mode:

```bash
libre-claw telegram setup --user-id 123456789
libre-claw telegram up
```

You can pass the token and model non-interactively:

```bash
libre-claw telegram setup \
  --bot-token "$TELEGRAM_BOT_TOKEN" \
  --user-id 123456789 \
  --provider openrouter \
  --model qwen/qwen3.7-max
libre-claw telegram up
```

Useful Telegram CLI commands:

```bash
libre-claw telegram status
libre-claw telegram allow 123456789  # add another numeric Telegram user ID
libre-claw telegram run   # bot only
libre-claw telegram up    # daemon + bot together
```

Useful bot slash commands:

```text
/start - Check that the bot is ready
/help - Show Telegram slash commands
/new - Start a fresh chat session
/model - Open provider/model buttons
/model <provider>:<name> - Switch model by text
/models - Open provider/model buttons
/provider - Open provider buttons
/cost - Show token and cost usage
/status - Show token and cost usage
/compact - Compact the current context
/schedule examples|list|add ... - Manage recurring runs
/cancel - Cancel the active generation
/stop - Cancel the active generation
```

Libre Claw registers these commands with Telegram on startup, so supported
commands should appear in the Telegram client when you type `/`. Sending `/`
by itself also prints the command list.

`/model`, `/models`, and `/provider` open a Hermes-style inline keyboard for
provider and model selection. The selected model applies immediately to the
running Telegram bridge.

Telegram authorization uses the numeric user ID, not the `@username`. If a user
messages the bot while blocked, Libre Claw replies with the numeric ID and the
exact `libre-claw telegram allow ...` command to run.

Manual config still works in `~/.libre-claw/config.toml`:

```toml
[telegram]
enabled = true
use_daemon = true
allowed_user_ids = [123456789]
```

The bot token is read from the secure key store or `TELEGRAM_BOT_TOKEN`; it is
not stored in TOML.

## Configuration

Bundled defaults:

```bash
libre-claw config defaults
```

User configuration is loaded from:

```text
~/.libre-claw/config.toml
```

Basic settings can be overridden with:

- `LIBRE_CLAW_DEFAULT_PROVIDER`
- `LIBRE_CLAW_DEFAULT_MODEL`
- `LIBRE_CLAW_WORKING_DIRECTORY`
- `LIBRE_CLAW_THEME`
- `LIBRE_CLAW_LOG_LEVEL`

The runtime agent system prompt lives in the `[agent]` config section as
`system_prompt`, with `system_prompt_extra` available for local additions. The
default prompt identifies Libre Claw as a Kroonen AI Inc. agent harness.

Persona customization lives in `soul.md`. Libre Claw loads these files at the
start of each agent turn, in order:

- `~/.libre-claw/soul.md` for your global personal Claw persona
- `<project>/.libre-claw/soul.md` for project-scoped persona/context
- `<project>/soul.md` for a simple repo-root persona file

Use `/soul init --user`, `/soul init --project`, or `/soul init --root` to
create a starter file. Use `/soul show` to see what is being injected and
`/soul reload` after editing. Soul files shape voice, taste, and durable
identity, but they cannot override safety rules, tool permissions, sandbox
settings, provider policies, or direct user instructions.

Sandbox defaults restrict file access to the configured working directory and
block dangerous shell patterns such as root removal, `sudo`, and remote install
pipes.

## Troubleshooting

### `externally-managed-environment`

Create and activate a virtual environment, then install inside it:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### Missing API Key

Run:

```bash
libre-claw auth status
libre-claw auth set-key openrouter
```

Replace `openrouter` with `anthropic`, `openai`, or `ollama`. For Codex, use
`/codex login` in the TUI or `libre-claw auth codex-login`.

### Ollama Cloud 401

Make sure you are not using the model name as the bearer token. Store or export
the real Ollama API key:

```bash
libre-claw auth set-key ollama
```

or:

```bash
export OLLAMA_API_KEY="..."
```

Then use:

```toml
[providers.ollama]
base_url = "https://ollama.com"
api_format = "ollama"
api_key_env = "OLLAMA_API_KEY"
```

## More Docs

- [Getting started in 5 minutes](docs/GETTING_STARTED.md)
- [Security](SECURITY.md)
- [Roadmap](ROADMAP.md)
- [Demo scripts](docs/DEMOS.md)

## Development

```bash
python -m pytest
python -m compileall src tests
git diff --check
```
