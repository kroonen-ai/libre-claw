# Changelog

## 0.1.0 - 2026-05-24

Initial shippable Libre Claw release.

### Added

- Textual terminal UI with streaming chat, status bar, file tree, command palette, and permission prompts.
- Anthropic, OpenAI, and local Ollama/OpenAI-compatible providers.
- Native local tool calling and XML tool-call fallback for local models without native support.
- ReAct-style async agent loop with concurrent tool execution.
- Built-in `read_file`, `write_file`, `edit_file`, `list_directory`, and `bash` tools.
- Working-directory sandboxing, blocked shell command patterns, and approval gates for side-effecting tools.
- SQLite memory for facts, sessions, summaries, and file edit logs.
- Telegram daemon with allowlist auth, streaming updates, and inline permission prompts.
- Key storage through environment variables, OS keyring, or encrypted local fallback.
- OAuth 2.0 PKCE and JWT scaffolding for a future dashboard.
- Apache-2.0 licensing with Kroonen AI Inc. source headers.
- Test coverage for config, providers, tools, permissions, memory, Telegram, auth, and TUI helpers.
