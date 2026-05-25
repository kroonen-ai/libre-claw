# Libre Claw Roadmap

Libre Claw is built as a competitive, terminal-native agent harness. The first
release focuses on durable local autonomy and provider choice.

## Completed In 0.1.0

- Durable runs with append-only event logs and artifacts.
- Human review cockpit with timeline replay and artifact panels.
- Background daemon API for runs, permissions, Telegram, and automations.
- User/project skills with AgentSkills-style `SKILL.md` support.
- MCP stdio integration with allowlisted external tools.
- Recurring automations and saved reports.
- Browser/computer-use tools with persistent profiles and screenshots.
- OpenRouter usage analytics and app attribution verification.
- First-run setup flow, one-command installer, and security documentation.

## Next Priorities

- Harden daemon authentication for remote deployments.
- Add packaged releases and signed binaries.
- Expand MCP interoperability tests with common local servers.
- Improve browser previews in the TUI artifact panel.
- Add richer OpenRouter analytics exports.
- Publish polished demo videos and GIFs.

## Design Principles

- The agent reads before editing.
- Write, shell, browser, git, and external MCP actions are permissioned.
- Runs survive UI restarts.
- Provider credentials never live in project config.
- OpenRouter app identity is fixed to Libre Claw from Kroonen AI Inc.
