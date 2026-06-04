<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# Agent Contributor Guide

This file is for agentic coding models contributing to Libre Claw. Follow it as the local operating manual for this repository.

## Core Principles

- Read before editing. Use `rg` and targeted file reads to understand the existing code path.
- Make small, explicit changes. Do not rewrite whole modules when a surgical change solves the problem.
- Preserve user changes. The worktree may be dirty; never revert edits you did not make unless explicitly asked.
- Keep behavior configurable. Avoid hard-coded provider names, model names, schedule names, user paths, or one-off task rules.
- Prefer existing patterns over new abstractions. Add abstractions only when they remove real duplication or clarify ownership.
- Verify your work. Run focused tests for the touched area and the full suite when the change affects shared behavior.
- Do not push unless asked. A local commit is fine when requested by workflow, but remote pushes need explicit approval.

## Code Style

- Use `from __future__ import annotations` in Python modules.
- Keep function signatures typed. Public and internal APIs should be clear from type hints.
- Keep the core async-first. Avoid blocking I/O on TUI, daemon, Telegram, provider, or agent hot paths.
- Do not use `print()` for app output. Use the TUI, daemon responses, Telegram replies, or structured logging.
- Keep comments sparse and useful. Explain tricky control flow, not obvious assignments.
- Use ASCII text unless a file already uses Unicode or the feature explicitly needs it.
- Add the repository header to source/config files:

```text
# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0
```

For Markdown files, use the same information in an HTML comment.

## Config And Defaults

- The packaged defaults must stay in sync:
  - `config/default.toml`
  - `src/libre_claw/default.toml`
- If you add a config field:
  - add it to the dataclass in `src/libre_claw/config.py`
  - add it to `_load_default_config()`
  - parse it in `_build_config()`
  - update both TOML defaults
  - add or update tests in `tests/test_config.py`
- Do not store API keys or OAuth tokens in committed files.
- Environment variables and keyring-backed secrets should take precedence over TOML secrets.
- User-level config lives under `~/.libre-claw/config.toml`; project workspace config can live under `.libre-claw/`.

## Providers

- Provider implementations must conform to `LLMProvider.complete(...)`.
- Streaming should yield incremental `StreamEvent` objects and handle cancellation cleanly.
- Do not assume a provider supports tools, images, context metadata, or usage accounting unless the provider path proves it.
- OpenRouter app attribution belongs in provider request headers, not public user instructions.
- Model presets belong in provider/model registry code and docs, not in random command handlers.
- If model context can be discovered from the provider, prefer automatic detection over static guesses.

## Agent Loop

- Keep ReAct ordering correct: assistant tool calls must be followed by matching user tool results.
- Preserve Anthropic-compatible content block structure in session history.
- Enforce `max_tool_calls_per_turn` as a safety ceiling, but keep it configurable.
- Stream assistant text without re-rendering the full UI on every token.
- Surface provider errors as agent events. Do not crash the daemon, TUI, or Telegram bridge.
- Use `/goal` and judge flows with bounded prompt context. Do not feed entire raw histories when a focused digest is enough.

## Tools, Permissions, And Sandbox

- Tools inherit from `BaseTool` and return `ToolResult`; tool exceptions should become `ToolResult(error=...)`.
- Read-only tools should generally be `allow`; write, shell, browser navigation, and external side effects should be `ask`.
- Shell commands must go through the sandbox checks and timeout handling.
- Never bypass the sandbox for convenience.
- Scheduled automations may use `automations.auto_approve_tools`, but dangerous commands must still be blocked by the sandbox.
- Do not add task-specific tool hacks. Encode repeatable behavior as skills, config, prompts, or generic tool improvements.
- For file tools, respect the configured working directory restrictions.
- For large command output, read streams incrementally and cap stored metadata as well as displayed text.

## TUI

- Textual UI work should remain terminal-native, dense, and responsive.
- Keep mouse and inline mode configurable.
- Preserve native terminal text selection where possible.
- Slash commands should work consistently across TUI and Telegram when the command makes sense on both surfaces.
- Add tests for command parsing and UI state changes when possible.
- Avoid UI layouts that break when text is long, terminals are narrow, or the file explorer is hidden.

## Telegram

- Telegram is a bridge to the same agent core, not a separate agent implementation.
- Keep messages mobile-friendly and compact.
- Split long messages before sending. Telegram messages must not exceed platform limits.
- Use rendered HTML for final Markdown-like content when possible; keep streaming previews conservative.
- Permission prompts should support allow once, always tool, always call, and deny.
- Slash command registration should match the implemented handlers.
- Keep chat/session continuity unless `/new` explicitly starts fresh.
- Image messages should become user attachments and flow through provider-specific image mapping.

## Daemon, Runs, And Automations

- Daemon runs are durable and append-only. Important state belongs in run events and artifacts.
- Run states are `queued`, `running`, `blocked`, `done`, `failed`, and `cancelled`.
- Runs live under `~/.libre-claw/runs/<run_id>/`.
- Automation reports live under `~/.libre-claw/automations/reports/`.
- Scheduled jobs should run unattended when their tools are explicitly allowed by automation config.
- Scheduled job prompts should demand final mobile-friendly reports and suppress process narration.
- If a scheduled workflow is domain-specific, prefer a skill plus a schedule prompt over hard-coded daemon logic.
- `libre-claw start`, `shutdown`, `restart`, and `stop` have distinct meanings:
  - `start` starts the daemon
  - `shutdown` stops Libre Claw
  - `restart` restarts the daemon or Telegram stack
  - `stop` cancels an active run/turn

## Memory, Soul, And Skills

- Raw run/session archives are the source of truth.
- SQLite memory is the searchable layer and must redact likely secrets.
- Inject only relevant, capped memory into prompts.
- `soul.md` provides persona context and should be loaded without overriding system safety rules.
- Skills live in global and project-local `.libre-claw/skills/` directories.
- Skills should contain reusable operating instructions, not private one-off history.
- If the agent repeatedly performs a workflow, consider proposing or creating a skill.

## Dashboard And Website

- The daemon dashboard is part of the harness and should stay local-first.
- Keep dashboard UI responsive for desktop and mobile.
- Dashboard theme support should come from config and CSS variables, not scattered inline colors.
- The Astro website under `website/` is a separate site project. Touch it only when the task explicitly asks for website changes.
- Public docs should be clean for users. Internal roadmap phases, provider-growth tactics, or private ops notes do not belong in public copy.

## Testing

Use the project virtual environment when available:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests
```

Focused tests are acceptable for narrow changes, but run the full suite before committing shared behavior changes.

Useful test targets:

- Config changes: `tests/test_config.py`
- Permissions/tools: `tests/test_permissions.py`, `tests/test_tools.py`
- Agent loop: `tests/test_agent.py`
- Providers: `tests/test_*provider.py`
- Daemon/runs/automations: `tests/test_daemon.py`
- Telegram: `tests/test_telegram.py`
- TUI/dashboard: `tests/test_tui.py`, dashboard-related daemon tests

## Git Hygiene

- Check `git status --short` before editing and before finishing.
- Do not squash history unless explicitly asked.
- Do not force-push protected branches.
- Keep commits focused and use clear imperative messages.
- If asked to push, push the intended repo only. The app repo and website repo are distinct.
- Never commit secrets, local tokens, generated caches, or private screenshots unless the user explicitly asks for an asset to be committed.

## Final Check Before Hand-Off

- The requested behavior is implemented, not just planned.
- Relevant tests pass.
- The worktree contains only intentional changes.
- Long-running local processes are not left in an unexpected state.
- The final response names what changed, what was verified, and whether anything was not pushed.
