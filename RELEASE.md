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
- Run artifacts capture the launch working directory, final tool-result
  verification notes, artifact sizes in `/run`, and tracked-file git diffs when
  a run finishes inside a git repository.
- Local background daemon API with `libre-claw daemon`, daemon-owned active
  runs, event polling, cancellation, and permission approval endpoints.
- File explorer hidden by default, with parent-directory navigation, agent
  working-directory sync, a visible `Hide` control, a left-side `Files` rail
  for restoring the sidebar, and `Ctrl+B` toggling.
- Provider support for Anthropic, OpenAI, OpenRouter, Ollama, and Codex CLI
  auth. Default model selections are `claude-opus-4-6`, `gpt-5.5`, and
  `qwen3.6:27b`, with Ollama Cloud examples centered on `kimi-k2.6:cloud`.
- Codex/ChatGPT login can be started from inside the TUI with `/codex login`,
  then used through `/provider codex` or `/model codex:gpt-5.5`.
- OpenRouter support with fixed Libre Claw app attribution headers:
  `https://kroonen.ai`, `Libre Claw`, and the `cli-agent` category.
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
  `browser_read`, `browser_screenshot`, and `bash` tools, with bounded
  reads/listing/search, atomic writes/edits, occurrence targeting, diffs,
  git inspection/commit support, browser scaffolding with graceful dependency
  errors, scratchpad thinking, and bounded shell output.
- Interactive TUI permission panel with approve, deny, always allow tool, and
  always allow exact command options. Dangerous sandbox-blocked commands show a
  warning and require one-time approval or denial.
- TUI polish: thin `#0070F3` scrollbars, blue user labels, purple `#8B5CF6`
  Libre Claw assistant labels, dark/light theme support, and cleaner panel
  borders.
- SQLite memory for facts, sessions, summaries, and file edit logs.
- Telegram daemon with allowlist auth, streaming updates, per-chat sessions,
  model/provider commands, and inline permission prompts.
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
