# Libre Claw Release Notes

## 0.1.0 - 2026-05-24

First shippable Libre Claw release from Kroonen AI Inc.

### Current State

- Terminal-native Textual TUI with streaming chat, Markdown rendering, command
  palette, slash-command suggestions, `/model` provider/model onboarding,
  startup ASCII art, session status, and click-to-expand startup release notes.
- `/model <provider>:<model> --global` persists the selected provider/model in
  `~/.libre-claw/config.toml`.
- Status bar context meter plus `/compact status`, `/compact --force`, and
  `/compact --keep N` controls for context-window management.
- `/goal <objective>` supervised mode runs bounded multi-turn work, asks a
  separate no-tools judge model whether the goal is complete after each turn,
  and supports `/goal status`, `/goal stop`, and `/goal max N`.
- Durable local runs with unique run IDs, run states, append-only `events.jsonl`
  logs, `summary.md`, `verification.md`, `diff.patch` artifacts, and `/runs`,
  `/run <id>`, `/resume <id>`, and `/cancel <id>` controls.
- Human-review cockpit follow-up with `plan.md`, a TUI Plan/Summary/Verify/Diff
  artifact panel, `/artifacts`, `/approvals`, `/changes`, richer run timeline
  tool cards, and last-seen event tracking.
- Run artifacts capture the launch working directory, final tool-result
  verification notes, artifact sizes in `/run`, and tracked-file git diffs when
  a run finishes inside a git repository.
- Local background daemon API with `libre-claw daemon`, daemon-owned active
  runs, event polling, cancellation, and permission approval endpoints.
- TUI daemon mode via `[tui].use_daemon = true`, allowing the TUI to start,
  poll, approve, cancel, and resume daemon-owned runs without owning execution.
- Telegram can optionally route chat runs and inline approvals through the
  daemon with `[telegram].use_daemon = true`, so approvals resume the same
  durable daemon run.
- Daemon run requests reject per-request `working_directory` overrides; the
  daemon uses the trusted configured root only.
- Recurring local automations with `[automations]` config, `/schedule`
  commands, daemon due-run execution, cron-like schedules, saved Markdown
  reports, and route metadata for TUI, Telegram, or report workflows.
- `/schedule examples` ships ready-made daily repo health check, weekly
  dependency review, and morning brief automation prompts.
- Browser/computer-use upgrade with persistent Playwright profiles,
  `browser_extract`, `browser_execute`, `browser_dismiss_cookies`,
  `browser_click`, `browser_type`, `browser_wait`, and `browser_download`,
  selector-aware reads/screenshots, cookie-consent dismissal, safe `[browser]`
  domain allow/deny rules, saved downloads/screenshots, and `/artifacts
  browser` screenshot previews. Live browser pages are kept in a process-level
  state pool so sessions survive follow-up tool calls and registry rebuilds.
- Direct `http_request` tool for API calls, image/file fetches, sandboxed
  downloads, and HTTP GET/POST-style workflows without shelling out to `bash`.
- MCP stdio bridge with `[mcp]` config, explicit server/tool allowlisting,
  `mcp__server__tool` wrappers, `/tools list` visibility, and normal Libre Claw
  permission policies for external tools.
- Skills system with global `~/.libre-claw/skills/`, project-local
  `.libre-claw/skills/`, AgentSkills-style `SKILL.md` discovery, `/skills`
  management commands, and relevant skill injection across TUI, Telegram, and
  daemon agent runs.
- Soul/persona system with `~/.libre-claw/soul.md`, project
  `.libre-claw/soul.md`, and project-root `soul.md` injection across TUI,
  Telegram, and daemon agent runs, plus `/soul status|show|init|reload`.
- Dedicated runtime workspace support with `libre-claw workspace init`,
  `/workspace status|init|use`, starter `README.md`/`goals.md`/`memory.md`,
  copied `soul.md` and skills Markdown, and persisted
  `[general].working_directory` updates.
- File explorer hidden by default, with parent-directory navigation, agent
  working-directory sync, a visible `Hide` control, a left-side `Files` rail
  for restoring the sidebar, and `Ctrl+B` toggling.
- Provider support for Anthropic, OpenAI, OpenRouter, Ollama, and Codex CLI
  auth. Default model selections are `claude-opus-4-7`, `gpt-5.5`, and
  `qwen3.6:27b`, with Anthropic direct API presets updated to `claude-opus-4-7`,
  `claude-sonnet-4-6`, and `claude-haiku-4-5-20251001`, plus expanded
  Ollama Cloud presets covering the current cloud library aliases such as
  `kimi-k2.6:cloud`, `qwen3.5:cloud`, `gemma4:31b-cloud`,
  `deepseek-v4-flash:cloud`, and `gpt-oss:120b`.
- Codex/ChatGPT login can be started from inside the TUI with `/codex login`,
  then used through `/provider codex` or `/model codex:gpt-5.5`, with Codex
  OAuth picker presets for `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`,
  `gpt-5.3-codex`, `gpt-5.3-codex-spark`, and `gpt-5.2`.
- OpenRouter support with fixed Libre Claw app attribution headers:
  `https://libreclaw.dev`, `Libre Claw`, and the `cli-agent,personal-agent`
  categories, targeting Productivity, Coding Agents, Personal Agents, and CLI
  Agents visibility.
- OpenRouter growth analytics through `/usage openrouter`, attribution
  verification, an analytics link to
  `https://openrouter.ai/apps?url=https://libreclaw.dev`, and a shared
  recommended model preset catalog for TUI, Telegram, and usage reports,
  including `deepseek/deepseek-v4-flash`, `qwen/qwen3.7-max`,
  `moonshotai/kimi-k2.6`, `anthropic/claude-opus-4.7`, and
  `openai/gpt-5.5`.
- Provider fallback routes can fail over to backup provider/model/account
  combinations when the primary provider is unavailable before it starts
  streaming.
- Heartbeat check-ins through TUI and Telegram with `/heartbeat
  status|once|start|stop`, backed by a configurable checklist.
- Explicit `libre-claw tui` and `libre-claw chat` commands open the same TUI as
  the default `libre-claw` launch.
- Competitive polish pass with a one-command installer, first-run `/setup`
  wizard, provider/key setup inside the TUI, quickstart docs, demo scripts,
  public roadmap, and security documentation.
- Cumulative session token tracking in the status bar, TUI `/cost`, and
  Telegram `/cost`. OpenRouter requests usage accounting so provider-reported
  cost, cached tokens, and reasoning tokens appear when OpenRouter returns
  them.
- Ollama covers local daemon use, direct Ollama Cloud access, and
  OpenAI-compatible Ollama endpoints. Native tool calling and XML tool-call
  fallback are both available.
- ReAct-style async agent loop with tool calling, concurrent tool execution,
  interrupt handling, context compaction, and configurable system prompt from
  TOML.
- Built-in `read_file`, `write_file`, `edit_file`, `list_directory`, `glob`,
  `search_files`, `git_status`, `git_commit`, `think`, `browser_navigate`,
  `browser_read`, `browser_extract`, `browser_execute`,
  `browser_dismiss_cookies`, `browser_click`, `browser_type`, `browser_wait`,
  `browser_download`, `browser_screenshot`, `http_request`, and `bash` tools,
  with bounded reads/listing/search, atomic writes/edits, occurrence targeting,
  diffs, git inspection/commit support, persistent browser profiles, browser
  artifact capture with graceful dependency errors, direct HTTP fetches,
  scratchpad thinking, and bounded shell output.
- Interactive TUI permission panel with approve, deny, always allow tool, and
  always allow exact command options. Dangerous sandbox-blocked commands show a
  warning and require one-time approval or denial.
- TUI polish: thin `#0070F3` scrollbars, blue user labels, purple `#8B5CF6`
  Libre Claw assistant labels, dark/light theme support, and cleaner panel
  borders.
- Automatic persistent memory with append-only JSONL session archives,
  durable-run import, searchable SQLite `memory_items`, FTS5/fallback search,
  relevance-based prompt injection, redaction of credential-looking content,
  and `/memory status|on|off|list|search|add|forget|summarize|import-runs`
  commands in TUI and Telegram.
- Existing SQLite facts, sessions, summaries, and file edit logs remain
  compatible; manual facts migrate into first-class memory items.
- Telegram daemon with allowlist auth, streaming updates, per-chat sessions,
  model/provider commands, compact emoji tool notices, per-run daemon event
  cursors to avoid stale tool replay, and inline permission prompts.
- User-friendly Telegram setup with `libre-claw telegram setup`, secure token
  storage, `libre-claw telegram status`, and `libre-claw telegram up` to run
  the bot plus local daemon together.
- Secure key storage through environment variables, OS keyring, or encrypted
  local fallback. API keys are not stored in TOML.
- OAuth 2.0 PKCE and JWT scaffolding for a future dashboard.
- Apache-2.0 licensing with Kroonen AI Inc. source headers.
- GitHub Actions test/build CI and expanded user-facing README documentation.

### Known Limits

- Cost display depends on provider-reported usage. OpenRouter returns cost when
  usage accounting is available; other providers may only return token counts.
- Browser tools require the optional `browser` extra plus Chromium from
  Playwright; without that dependency, Libre Claw returns a friendly tool error
  and keeps running.
- Git PR helpers and richer diff UX are not yet at full parity with larger
  agent harnesses.
- The Codex provider delegates to `codex exec`; richer native Codex event
  rendering and Libre Claw tool unification are still future polish.
- GitLab `main` may remain protected; mirrored updates currently target the
  configured writable branch.

## Release Checklist

Use this checklist before publishing Libre Claw.

1. Update `src/libre_claw/__init__.py` and `pyproject.toml` to the same version.
2. Update `CHANGELOG.md` and this file with the release date and summary.
3. Run:

   ```bash
   python3 -m pytest
   python3 -m compileall src tests
   git diff --check
   python3 -m build
   ```

4. Inspect the wheel contents and confirm `libre_claw/default.toml` and
   `libre_claw/RELEASE.md` are included.
5. Install the wheel into a clean environment and run:

   ```bash
   libre-claw --version
   libre-claw --help
   libre-claw config defaults
   libre-claw auth status
   ```

6. Smoke-test at least one real provider before tagging.
