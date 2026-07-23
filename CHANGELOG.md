# Changelog

## 0.1.0 - 2026-05-24

Initial shippable Libre Claw release.

### Added

- Textual terminal UI with streaming chat, status bar, file tree, command palette, slash-command suggestions, `/model` provider/model onboarding, startup ASCII art, startup release notes, and interactive permission prompts.
- Focused TUI change review with compact inline diffs, `Ctrl+E` and `/review`
  navigation, exact permission previews, and responsive workspace/composer
  context that remains usable in short terminals.
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
- Local background daemon API with `libre-claw start` / `libre-claw daemon`,
  daemon-owned active runs, event polling, cancellation, and permission
  approval endpoints.
- Process lifecycle commands with `libre-claw shutdown` and
  `libre-claw restart` for shutting down or restarting the daemon/Telegram
  stack from another terminal. `libre-claw stop` now cancels the active daemon
  turn without stopping Libre Claw.
- Safe self-update with `libre-claw update` or `/update` in the TUI and
  authorized Telegram chats. It checks `origin/main`, writes a rollback backup
  under `~/.libre-claw/backups/updates/`, refuses dirty worktrees, and applies
  only fast-forward updates; `--dry-run` checks without changing files.
- Local web dashboard served by the daemon at `/dashboard` for starting runs,
  reviewing timelines, approving blocked tools, managing schedules, and checking
  usage.
- TUI daemon mode via `[tui].use_daemon = true`, allowing the TUI to start,
  poll, approve, cancel, and resume daemon-owned runs without owning execution.
- Telegram can optionally route chat runs and inline approvals through the
  daemon with `[telegram].use_daemon = true`, so approvals resume the same
  durable daemon run.
- Telegram slash command menu now exposes daemon-aware remote commands for
  usage, run history, run inspection, daemon health, and session restart.
- TUI and Telegram support `/btw` and `/steer` for adding steering notes to
  future agent turns without starting a new run.
- Scheduled automations with `route = "telegram"` now deliver the completed
  report back to the stored Telegram chat after each daemon run.
- Daemon run requests reject per-request `working_directory` overrides; the
  daemon uses the trusted configured root only.
- MCP stdio bridge with `[mcp]` config, explicit server/tool allowlisting,
  `mcp__server__tool` wrappers, `/tools list` visibility, and normal Libre Claw
  permission policies for external tools.
- Skills system with global `~/.libre-claw/skills/`, project-local
  `.libre-claw/skills/`, AgentSkills-style `SKILL.md` discovery, `/skills`
  management commands, and relevant skill injection across TUI, Telegram, and
  daemon agent runs.
- Anthropic, OpenAI, OpenRouter, Moonshot AI, Ollama, and Codex providers.
  Direct Moonshot Platform support includes current `kimi-k3`,
  `kimi-k2.7-code`, `kimi-k2.7-code-highspeed`, and `kimi-k2.6` presets,
  Kimi-specific token/thinking parameters, static published context limits,
  vision input, and private reasoning continuity across tool calls.
- Added OpenRouter usage accounting and recommended model presets for TUI,
  Telegram, and usage reports.
- Added the `moonshotai/kimi-k3` OpenRouter preset to the shared TUI,
  Telegram, usage, documentation, and website model catalogues.
- Cumulative session token tracking in the status bar, TUI `/cost`, and
  Telegram `/cost`, with OpenRouter usage accounting enabled for
  provider-reported request cost, cached tokens, and reasoning tokens.
- Native Ollama tool calling and XML tool-call fallback for models without native support.
- ReAct-style async agent loop with concurrent tool execution.
- Built-in `read_file`, `write_file`, `edit_file`, `apply_patch`,
  `list_directory`, `glob`, `search_files`, `view_image`, `git_status`,
  `git_commit`, `think`, `browser_navigate`, `browser_read`,
  `browser_screenshot`, `bash`, and managed `process` tools.
- Tool-heavy runs retain bounded command heads and diagnostic tails, preserve
  partial timeout output and important tool findings through compaction, and
  avoid hanging when descendants inherit a completed shell's output pipes.
- Working-directory sandboxing, blocked shell command patterns, and approval gates for side-effecting tools.
- File explorer parent navigation that updates the agent working directory, plus visible hide/show controls and a left-side restore rail.
- TUI polish including thin `#0070F3` scrollbars, blue user labels, purple `#8B5CF6` Libre Claw assistant labels, and cleaner panel borders.
- SQLite memory for facts, sessions, summaries, and file edit logs.
- Telegram daemon with allowlist auth, streaming updates, and inline permission prompts.
- Key storage through environment variables, OS keyring, or encrypted local fallback.
- OAuth 2.0 PKCE and JWT scaffolding for a future dashboard.
- Apache-2.0 licensing with Kroonen AI source headers.
- GitHub Actions test/build CI and expanded user-facing README documentation.
- Test coverage for config, providers, tools, permissions, memory, Telegram, auth, and TUI helpers.
