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
- Provider metadata caches use keyed, process-local identities to separate
  credentials without keeping reusable credential fingerprints.

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

## Dashboard and Daemon API

The daemon binds to localhost by default. Its API is a trusted control surface:
local clients can approve tools and explicitly request worktree setup commands.
Shell execution is intentional and remains subject to the shell permission and
sandbox policies.

The daemon validates the requested host and browser origin, rejects cross-site
API requests, and requires JSON content types for nonempty mutation bodies.
These checks prevent websites from using a visitor's browser to issue commands
to the daemon. They are not user authentication. Binding to a LAN address or
`0.0.0.0` gives reachable native clients access; keep these listeners on trusted
networks. Reverse proxies require explicit authentication and compatible
Host/Origin handling; arbitrary forwarded headers are not trusted.

## Durable Logs

Runs are stored under `~/.libre-claw/runs/<run_id>/` with append-only events and
artifacts. These logs may include prompts, command output, file paths, and tool
results. Treat run directories as sensitive project data.

## Reporting

Report security issues privately to `hello@kroonen.ai`.
