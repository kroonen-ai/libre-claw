# Changelog

## 0.1.0 - 2026-05-24

Initial shippable Libre Claw release.

### Added

- Textual terminal UI with streaming chat, status bar, file tree, command palette, slash-command suggestions, `/model` provider/model onboarding, startup ASCII art, startup release notes, and interactive permission prompts.
- `/model <provider>:<model> --global` persists the selected provider/model in
  `~/.libre-claw/config.toml`.
- Status bar context meter plus `/compact status`, `/compact --force`, and
  `/compact --keep N` controls for context-window management.
- Durable local runs with unique run IDs, run states, append-only event logs,
  run artifacts, and `/runs`, `/run <id>`, `/resume <id>`, and `/cancel <id>`
  controls.
- Human-review cockpit follow-up with `plan.md`, a TUI Plan/Summary/Verify/Diff
  artifact panel, `/artifacts`, `/approvals`, `/changes`, richer run timeline
  tool cards, and last-seen event tracking.
- Run artifacts now include the launch working directory, final tool-result
  verification notes, artifact sizes in `/run`, and tracked-file git diffs when
  a run finishes inside a git repository.
- Local background daemon API with `libre-claw daemon`, daemon-owned active
  runs, event polling, cancellation, and permission approval endpoints.
- Telegram can optionally route chat runs and inline approvals through the
  daemon with `[telegram].use_daemon = true`, so approvals resume the same
  durable daemon run.
- Daemon run requests reject per-request `working_directory` overrides; the
  daemon uses the trusted configured root only.
- Skills system with global `~/.libre-claw/skills/`, project-local
  `.libre-claw/skills/`, AgentSkills-style `SKILL.md` discovery, `/skills`
  management commands, and relevant skill injection across TUI, Telegram, and
  daemon agent runs.
- Anthropic, OpenAI, OpenRouter, and Ollama providers. Defaults are `claude-opus-4-6`, `gpt-5.5`, and `qwen3.6:27b`; Ollama supports local daemon use, Ollama Cloud with `kimi-k2.6:cloud`, and Ollama/OpenAI-compatible endpoints.
- Fixed OpenRouter app attribution for Libre Claw with `https://kroonen.ai`, `Libre Claw`, and `cli-agent` headers.
- Cumulative session token tracking in the status bar, TUI `/cost`, and
  Telegram `/cost`, with OpenRouter usage accounting enabled for
  provider-reported request cost, cached tokens, and reasoning tokens.
- Native Ollama tool calling and XML tool-call fallback for models without native support.
- ReAct-style async agent loop with concurrent tool execution.
- Built-in `read_file`, `write_file`, `edit_file`, `list_directory`, `glob`,
  `search_files`, `git_status`, `git_commit`, `think`, `browser_navigate`,
  `browser_read`, `browser_screenshot`, and `bash` tools.
- Working-directory sandboxing, blocked shell command patterns, and approval gates for side-effecting tools.
- File explorer parent navigation that updates the agent working directory, plus visible hide/show controls and a left-side restore rail.
- TUI polish including thin `#0070F3` scrollbars, blue user labels, purple `#8B5CF6` Libre Claw assistant labels, and cleaner panel borders.
- SQLite memory for facts, sessions, summaries, and file edit logs.
- Telegram daemon with allowlist auth, streaming updates, and inline permission prompts.
- Key storage through environment variables, OS keyring, or encrypted local fallback.
- OAuth 2.0 PKCE and JWT scaffolding for a future dashboard.
- Apache-2.0 licensing with Kroonen AI Inc. source headers.
- GitHub Actions test/build CI and expanded user-facing README documentation.
- Test coverage for config, providers, tools, permissions, memory, Telegram, auth, and TUI helpers.
