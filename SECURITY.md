# Security

Libre Claw runs in the user's terminal with access to local files and tools, so
the default security posture is conservative.

## API Keys

- API keys are never required in TOML config files.
- Environment variables take precedence.
- Stored keys use the OS keyring when available.
- If keyring is unavailable, Libre Claw uses an encrypted fallback file at
  `~/.libre-claw/.keys` with file mode `0600`.
- `/setup key <provider>` stores keys through the same secure path and hides
  the input in the TUI.
- Codex/ChatGPT auth is delegated to the Codex CLI. Libre Claw does not read or
  copy private Codex token files.

## Permissions

Read-only tools run without prompting. File writes, file edits, shell commands,
git commits, browser navigation/click/type/download, and external MCP tools ask
for approval unless the user explicitly grants a session override.

Permission choices:

- Approve once.
- Deny.
- Always allow this tool for the session.
- Always allow this identical command for the session.

Dangerous shell commands cannot be promoted to always-allow.

## Sandbox

The command sandbox blocks configured dangerous patterns, including root
removal, `sudo` when disabled, shell bombs, and remote install pipes such as
`curl | sh`.

File tools resolve paths through the configured working directory when
`[sandbox].restrict_to_working_dir = true`.

Daemon `POST /runs` requests cannot override the server working directory.

## Durable Logs

Runs are stored under `~/.libre-claw/runs/<run_id>/` with append-only events and
artifacts. These logs may include prompts, command output, file paths, and tool
results. Treat run directories as sensitive project data.

## Reporting

Report security issues privately to `hello@kroonen.ai`.
