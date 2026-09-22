# OpenCode Zen and Go

Libre Claw connects directly to OpenCode's gateways using your console API key.
Zen and Go have separate provider identities and endpoints:

| Service | Provider | Endpoint | Credential |
| --- | --- | --- | --- |
| OpenCode Zen | `opencode` | `https://opencode.ai/zen/v1` | `OPENCODE_API_KEY` or a saved Zen key |
| OpenCode Go | `opencode-go` | `https://opencode.ai/zen/go/v1` | `OPENCODE_GO_API_KEY`, a saved Go key, or shared `OPENCODE_API_KEY` |

OpenCode documents console API-key authentication for both services. Sign in at
[OpenCode](https://opencode.ai/auth), select the appropriate account or workspace,
and obtain its key. Go requires an active subscription; Zen uses its own billing
and model access. See the [provider setup guide](https://opencode.ai/docs/providers),
[Zen documentation](https://opencode.ai/docs/zen/), and
[Go documentation](https://opencode.ai/docs/go/).

## Connect securely

Choose one service:

```sh
libre-claw auth connect-opencode opencode
libre-claw auth connect-opencode opencode-go
```

The key is entered without echo and stored through Libre Claw's keyring and
encrypted local fallback. Add `--browser` to open the official sign-in page.
The existing `auth set-key <provider>` command also works. No browser cookies,
OAuth access tokens, or refresh tokens are used for this integration.

In the TUI, use `/setup opencode` or `/setup opencode-go`. Setup accepts the key,
refreshes that service's catalog, and keeps your current provider and model
unchanged. Then choose an actual discovered ID:

```text
/models opencode-go
/model opencode-go:<model-id> --global
```

Both providers are available for tasks, defaults, schedules, and explicit fallback
routes in the dashboard and Telegram. The dashboard links to OpenCode's sign-in
page; enter credentials through the CLI or masked TUI setup. There is no model
release list baked into Libre Claw.

## Import an existing OpenCode login

Credential import is explicit and limited to the selected service:

```sh
libre-claw auth import-opencode opencode
libre-claw auth import-opencode opencode-go
```

These commands read only the selected `type: "api"` entry from OpenCode's
`$XDG_DATA_HOME/opencode/auth.json`, or `~/.local/share/opencode/auth.json` when
XDG data home is unset. Use `--path /path/to/auth.json` for another location.
The original file is not modified. Other providers' keys and OAuth tokens are
not imported. A different already-saved Libre Claw key requires `--replace`.
Import does not change the selected provider or model, or make model requests.

```sh
libre-claw auth status opencode-go
libre-claw auth delete-key opencode-go
```

Status reports the credential source without revealing the key. Deletion removes
Libre Claw's stored key and legacy aliases; environment variables and OpenCode's
own credential file remain independently managed.

Go lookup prefers its configured environment variable and saved Go credential.
With the default setting, a missing Go key falls back to `OPENCODE_API_KEY`.
It never substitutes a saved Zen account. Configuring another `api_key_env`, or
an empty value to use stored credentials only, disables that shared fallback.
The aliases `zen`, `opencode-zen`, and `opencode_zen` identify Zen; `go` and
`opencode_go` identify Go.

## Models and protocols

Availability comes from each gateway's live `/models` endpoint. Protocol and
capability metadata come from [Models.dev](https://models.dev), the public
catalog used by OpenCode. Metadata requests carry no credentials. Responses
stay on your configured gateway; metadata cannot redirect API keys to a new host
or install provider packages.

Libre Claw supports the conversational protocols advertised by the catalog:

- OpenAI Chat Completions
- OpenAI Responses
- Anthropic Messages
- Google GenerateContent

Streaming, tool results, images, provider-specific reasoning, and token usage
use the corresponding protocol. Failed, incomplete, or malformed tool responses
do not release pending tool calls for execution. Go requests include the
required client identity and a stable, opaque session identifier.

If a newly released ID has no routing metadata yet, select its documented
protocol explicitly. This also supports compatible proxy deployments:

```toml
[providers.opencode-go]
default_model = "<model-id>"
api_format = "auto" # or chat, responses, anthropic, google
max_tokens = 16384
# reasoning_effort = "high" # only when supported by the selected model
```

Catalog refresh is available through `/models opencode-go --refresh`. Model
lists are public and do not prove that an account has billing access to every
listed model. Classification-only APIs such as System One are not conversational
agent backends. Unknown protocols fail with configuration guidance.

Go quota or subscription failures stay on Go. Libre Claw never silently switches
to paid Zen. An explicitly configured fallback route can change providers, so
choose those routes according to your own billing preferences.

Requests and tool results are sent to OpenCode and its serving providers. Local
key encryption does not make remote inference private or offline; review the
service's current data policy for the models you select. Libre Claw preserves
reported cache and reasoning usage and leaves cost unknown when not reported.
