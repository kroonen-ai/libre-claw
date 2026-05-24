# Libre Claw

Libre Claw is a terminal-native AI agent harness from Kroonen AI Inc.
(https://kroonen.ai). It provides a Textual TUI, Anthropic and OpenAI streaming
providers, Ollama/OpenAI-compatible local inference, a permissioned local coding
toolset, SQLite-backed memory, Telegram daemon support, key storage, and sandbox
hardening.

Version `0.1.0` is the first shippable CLI/TUI release. It is still early
software, but it is functional end to end: launch the TUI, choose a provider,
chat with the agent, approve tools, persist sessions, and run the Telegram
bridge.

## Install

For development:

```bash
python3 -m pip install -e ".[dev]"
```

For a local wheel build:

```bash
python3 -m build
python3 -m pip install dist/libre_claw-0.1.0-py3-none-any.whl
```

## Quick Start

```bash
libre-claw
```

or:

```bash
python3 -m libre_claw
```

Set provider keys with environment variables:

```bash
export ANTHROPIC_API_KEY="..."
export OPENAI_API_KEY="..."
```

or store them securely with OS keyring, falling back to
`~/.libre-claw/.keys` when keyring is unavailable:

```bash
libre-claw auth set-key anthropic
libre-claw auth set-key openai
libre-claw auth status
```

## Providers

Anthropic is the default provider:

```toml
[general]
default_provider = "anthropic"
default_model = "claude-sonnet-4-6"
```

OpenAI can be selected with:

```toml
[general]
default_provider = "openai"
default_model = "gpt-4o"
```

Local inference defaults to Ollama at `http://localhost:11434`:

```bash
ollama pull qwen3:32b
```

```toml
[general]
default_provider = "local"

[providers.local]
base_url = "http://localhost:11434"
default_model = "qwen3:32b"
api_format = "ollama" # ollama | openai
supports_tools = true
tool_mode = "auto" # auto | native | xml
```

Native local tool calling is used when the model/server supports it. XML
tool-call fallback can be enabled with `tool_mode = "xml"` for local models
without native tool support.

Ollama Cloud is supported through the same `local` provider. There are two
correct modes.

For direct access to `ollama.com`, create an Ollama API key, set
`OLLAMA_API_KEY`, and point the provider at Ollama's cloud host. Use model
names listed by the direct API; Ollama's direct API docs show `gpt-oss:120b`:

```bash
export OLLAMA_API_KEY="..."
```

```toml
[general]
default_provider = "local"
default_model = "gpt-oss:120b"

[providers.local]
base_url = "https://ollama.com"
api_format = "ollama"
api_key_env = "OLLAMA_API_KEY"
```

Libre Claw also accepts a stored local-provider key:

```bash
libre-claw auth set-key local
```

For Ollama's OpenAI-compatible cloud API, use:

```toml
[providers.local]
base_url = "https://ollama.com"
api_format = "openai"
api_key_env = "OLLAMA_API_KEY"
```

If you want Ollama itself to handle cloud authentication, sign in with the
Ollama CLI and keep Libre Claw pointed at the local daemon. In that mode, use
the cloud model name shown by the Ollama model page, such as
`gpt-oss:120b-cloud` or `kimi-k2.6:cloud`:

```bash
ollama signin
ollama pull gpt-oss:120b-cloud
```

```toml
[general]
default_provider = "local"
default_model = "gpt-oss:120b-cloud"

[providers.local]
base_url = "http://localhost:11434"
api_format = "ollama"
api_key_env = ""
```

## TUI Commands

- `/help`
- `/clear`
- `/cancel`
- `/cost`
- `/model <name>`
- `/provider anthropic|openai|local`
- `/save [name]`
- `/load <name>`
- `/compact`
- `/tools expand|collapse|toggle <index>`
- `/memory list|add <fact>|forget <id>`
- `/telegram`
- `/exit`

Permission prompts accept `y`, `n`, `a`, and `!`.

Useful keybindings:

- `Ctrl+B` toggles the file tree
- `Ctrl+P` opens the command palette
- `Ctrl+Shift+C` copies the last assistant response
- `Ctrl+C` or `Esc` cancels active generation/tool execution
- `Tab` completes the first slash-command suggestion

## Tools

The current built-in tools are:

- `read_file`
- `write_file`
- `edit_file`
- `list_directory`
- `bash`

Read/list operations are allowed by default. Write/edit/bash operations require
approval unless the user grants a session override.

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

Bundled defaults can be printed with:

```bash
libre-claw config defaults
```

User configuration is loaded from `~/.libre-claw/config.toml` when present.
Basic general settings can be overridden with:

- `LIBRE_CLAW_DEFAULT_PROVIDER`
- `LIBRE_CLAW_DEFAULT_MODEL`
- `LIBRE_CLAW_WORKING_DIRECTORY`
- `LIBRE_CLAW_THEME`
- `LIBRE_CLAW_LOG_LEVEL`

Sandbox defaults restrict file access to the configured working directory and
block dangerous shell patterns such as root removal, `sudo`, and remote install
pipes.

## Development

```bash
python3 -m pytest
python3 -m compileall src tests
git diff --check
```

GitHub Actions runs the same checks on `main` pushes and pull requests, plus a
wheel/source distribution build.

## License

Libre Claw is licensed under Apache-2.0.
