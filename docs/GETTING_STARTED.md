# Getting Started In 5 Minutes

Libre Claw is a terminal-native coding agent harness from Kroonen AI Inc.

## 1. Install

```bash
curl -fsSL https://raw.githubusercontent.com/kroonen-ai/libre-claw/main/scripts/install.sh | sh
```

Private-repo or self-hosted installs can point the same installer at another
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

## 3. Set Up A Provider

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

## 4. Run A Coding Task

```text
Fix the failing tests and commit the result.
```

Libre Claw will read before editing, ask before write/shell/browser actions,
and save a durable run under `~/.libre-claw/runs/<run_id>/`.

## 5. Review The Work

```text
/runs
/artifacts summary
/artifacts diff
/usage openrouter
```

For longer autonomous work:

```text
/goal Implement the feature, run tests, and stop when verified.
```
