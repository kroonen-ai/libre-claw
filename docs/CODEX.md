# Codex / ChatGPT OAuth

Libre Claw's `codex` provider delegates authentication and execution to the
installed Codex CLI. A ChatGPT login supplies the OAuth session; Libre Claw does
not need an OpenAI API key for this route. Codex owns credential storage and
token refresh. Libre Claw does not read or import `auth.json` tokens.

## Connect

Install the CLI using the [official Codex CLI guide](https://learn.chatgpt.com/docs/cli),
then check the existing session:

```bash
libre-claw auth codex-status
```

If needed, start device sign-in:

```bash
libre-claw auth codex-login
```

Enable device sign-in in ChatGPT security settings or ask your workspace
administrator to enable it. Browser sign-in is also supported:

```bash
libre-claw auth codex-login --browser
```

The TUI equivalents are `/codex status`, `/codex login`, and
`/codex login browser`. These call the supported CLI commands documented in
[OpenAI authentication](https://learn.chatgpt.com/docs/auth).

## Choose a model

Discover models for the current CLI session, then select one:

```text
/models codex
/model codex:gpt-6-sol
```

| Model | Libre Claw selection |
| --- | --- |
| GPT-6 Luna | `/model codex:gpt-6-luna` |
| GPT-6 Sol | `/model codex:gpt-6-sol` |
| GPT-6 Astra | `/model codex:gpt-6-astra` |

Append `--global` to save the selection for future launches. In the dashboard,
choose **OpenAI Codex** and select a discovered model or enter its exact ID.
Log in from the terminal on the machine running Libre Claw before refreshing
the dashboard's model list.

The [official model guide](https://learn.chatgpt.com/docs/models) lists these
IDs for Codex CLI. Access depends on rollout, sign-in method, client version,
and workspace permissions. Libre Claw forwards the model ID to `codex exec
--model`; selecting an ID does not grant account access. A successful status
or model-list check does not prove that an inference request will succeed.

## Use a specific CLI installation

Set the executable in Libre Claw's TOML configuration when it is outside `PATH`:

```toml
[providers.codex]
executable = "/absolute/path/to/codex"
```

Login, status, logout, model discovery, and provider requests use this
installation. For a separate configuration file, pass
`libre-claw --config /path/to/config.toml auth codex-status`. The CLI inherits
the process environment, including `CODEX_HOME`, so run authentication and
Libre Claw with the same Codex home.
