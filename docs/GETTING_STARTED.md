# Getting Started in 5 Minutes

Libre Claw is a terminal-native coding agent harness built by Kroonen AI.

## 1. Install

```bash
curl -fsSL https://raw.githubusercontent.com/kroonen-ai/libre-claw/main/scripts/install.sh | sh
```

Private repository or self-hosted installs can point the same installer at another
HTTPS remote:

```bash
LIBRE_CLAW_REPO_URL=https://git.kroonen.ai/kroonen-ai/libre-claw.git \
  curl -fsSL https://raw.githubusercontent.com/kroonen-ai/libre-claw/main/scripts/install.sh | sh
```

## 2. Launch

```bash
libre-claw
```

If `~/.local/bin` is not on your `PATH`, run:

```bash
~/.local/bin/libre-claw
```

Useful launch surfaces:

```bash
libre-claw tui       # terminal chat UI
libre-claw chat      # alias for the TUI
libre-claw daemon    # local API, dashboard, automations, and daemon-owned runs
```

When the daemon is running, open the local dashboard at
`http://127.0.0.1:8766/dashboard`.

## 3. Set up a provider

Inside the TUI:

```text
/setup status
/setup provider openrouter
/setup key openrouter
/model openrouter:qwen/qwen3.7-max --global
```

Codex/ChatGPT auth is also available:

```text
/setup codex
/provider codex
```

## 4. Run a coding task

```text
Fix the failing tests and commit the result.
```

Libre Claw reads before editing, asks before write/shell/browser actions,
and save a durable run under `~/.libre-claw/runs/<run_id>/`.

## 5. Review the work

```text
/runs
/artifacts summary
/artifacts diff
/usage openrouter
/memory status
```

For longer autonomous work:

```text
/goal Implement the feature, run tests, and stop when verified.
```

Libre Claw automatically keeps local persistent memory. Raw session archives
stay in `~/.libre-claw/sessions/`, durable runs stay in `~/.libre-claw/runs/`,
and searchable memory lives in `~/.libre-claw/memory.db`. Use `/memory list`,
`/memory search <query>`, `/memory add <text>`, and `/memory forget <id>` when
you want to inspect or steer what gets remembered.

## 6. Optional: Telegram

Create a bot with BotFather, then set up Libre Claw with your numeric Telegram
user ID:

```bash
libre-claw telegram setup --user-id 123456789
```

The setup command stores the bot token in the secure key store or encrypted
fallback file, not in TOML. If you do not know your numeric ID yet, message the
bot once; Libre Claw will reply with the exact `libre-claw telegram allow ...`
command to run.

After setup, either start everything with the combined helper:

```bash
libre-claw telegram up
```

or start the normal daemon:

```bash
libre-claw daemon
```

If `[telegram].enabled = true`, `[telegram].use_daemon = true`, and a bot token
is available, the daemon starts and supervises the Telegram bridge
automatically. Telegram approvals, schedules, memory commands, and model
switching all route through the same durable run store.

Telegram also accepts photos and image documents. Captions become the prompt;
uncaptioned images default to "Please inspect the attached image." Uploaded
files stay local in `~/.libre-claw/telegram/uploads/` and are passed to
vision-capable providers.

## Terminal Selection

Libre Claw runs full-screen by default and disables Textual mouse capture. Use
`PageUp` / `PageDown` to scroll the transcript, `Ctrl+Home` / `Ctrl+End` to
jump, and `Ctrl+Shift+C` to copy the current Textual selection. Enable clickable
mouse controls with:

```bash
libre-claw tui --mouse
```

For normal terminal scrollback instead of the full-screen alternate-screen
layout, use:

```bash
libre-claw tui --inline
```

The same defaults can be persisted in `~/.libre-claw/config.toml`:

```toml
[tui]
mouse = false
inline = false
```
