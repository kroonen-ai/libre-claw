# Libre Claw

Libre Claw is a terminal-native AI coding agent harness from Kroonen AI Inc.
(https://kroonen.ai). It runs as a single Python TUI app, streams model output,
uses permissioned tools for coding tasks, supports Telegram, and keeps provider
keys out of project config files.

Version `0.1.0` is the first shippable release. It is early, but functional:
you can launch the TUI, choose Anthropic, OpenAI, OpenRouter, or Ollama, chat
with the agent, approve tool calls, browse files, save sessions, use memory,
and run the Telegram daemon.

## What You Get

- Textual terminal UI with streaming Markdown chat.
- Providers: Anthropic, OpenAI, OpenRouter, and Ollama.
- Ollama support for local daemon use, Ollama Cloud, and OpenAI-compatible
  Ollama endpoints.
- Built-in tools: `read_file`, `write_file`, `edit_file`, `list_directory`,
  and `bash`.
- Interactive permission prompts for write/edit/shell actions.
- File explorer whose root can move up and down with the user.
- SQLite-backed memory, saved sessions, and context compaction.
- Secure API key storage through environment variables, OS keyring, or an
  encrypted fallback file.
- Telegram daemon with allowlist auth.

## Requirements

- Python 3.11 or newer.
- A provider API key if you use a cloud provider.
- Optional: Ollama installed locally if you want local daemon mode.
- Optional: a Telegram bot token if you use the Telegram daemon.

## Install From This Repo

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
libre-claw auth status
```

Key lookup order is:

1. Environment variable.
2. OS keyring.
3. Encrypted fallback file at `~/.libre-claw/.keys`.

## Fast Starts

### OpenRouter

```bash
libre-claw auth set-key openrouter
libre-claw
```

Inside the TUI:

```text
/model openrouter:openrouter/auto
```

Libre Claw always sends OpenRouter app attribution as `Libre Claw` from
`https://kroonen.ai`, including the `cli-agent` category. Users do not configure
those product identity headers.

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
default_model = "claude-sonnet-4-6"
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
default_model = "gpt-4o"
```

## Model Switching

Use `/model` from inside the TUI:

```text
/model
/model list
/model kimi-k2.6:cloud
/model ollama:kimi-k2.6:cloud
/model openrouter:openrouter/auto
```

`/model <name>` changes the model for the current provider.
`/model <provider>:<name>` changes provider and model together.
Press `Tab` after `/model ` to complete known presets.

Use `/provider` when you only want to switch provider:

```text
/provider anthropic
/provider openai
/provider openrouter
/provider ollama
```

Legacy configs that still say `default_provider = "local"` or
`[providers.local]` are accepted and normalized to `ollama`.

## TUI Commands

- `/help`
- `/clear`
- `/cancel`
- `/cost`
- `/model [provider:]<name>|list`
- `/provider anthropic|openai|openrouter|ollama`
- `/save [name]`
- `/load <name>`
- `/compact`
- `/tools expand|collapse|toggle <index>`
- `/memory list|add <fact>|forget <id>`
- `/telegram`
- `/exit`

Useful keybindings:

- `Ctrl+B` toggles the file tree.
- `Ctrl+P` opens the command palette.
- `Ctrl+Shift+C` copies the last assistant response.
- `Esc` or `/cancel` cancels active generation/tool execution.
- `Ctrl+C` exits the app.
- `Tab` completes the first slash-command suggestion.

## Tool Permissions

Read/list tools run without prompting. File writes, file edits, and shell
commands ask first.

Permission prompts render as an interactive panel with:

- Approve
- Deny
- Always Tool
- Always Command

They also accept `y`, `n`, `a`, and `!` shortcuts. Dangerous sandbox-blocked
bash commands show a warning and only allow one-time approval or denial.

## File Explorer

The file explorer has an `Up` control. Moving the explorer root also updates
the agent working directory, so tools follow the directory you are browsing.

On startup, the TUI shows Libre Claw ASCII art plus the latest release notes
from `RELEASE.md`.

## Telegram

The standalone Telegram daemon uses the same agent core:

```bash
export TELEGRAM_BOT_TOKEN="..."
libre-claw telegram
```

Configure allowed Telegram user IDs in `~/.libre-claw/config.toml`:

```toml
[telegram]
enabled = true
allowed_user_ids = [123456789]
```

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

Replace `openrouter` with `anthropic`, `openai`, or `ollama`.

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

## Development

```bash
python -m pytest
python -m compileall src tests
git diff --check
```
