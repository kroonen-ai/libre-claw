<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# Kimi Code and Moonshot

Libre Claw uses Kimi Code by default for direct Kimi access. This is the coding
service included with a Kimi membership, using its official OpenAI-compatible
endpoint and model IDs. Moonshot Platform remains available as an explicit
advanced configuration.

## Kimi Code Setup

Create an API key in the Kimi Code Console, then store it through Libre Claw:

```bash
libre-claw auth set-key moonshot
libre-claw auth status
```

You can use an environment variable instead:

```bash
export KIMI_API_KEY="..."
```

Select a model from the TUI or Telegram:

```text
/setup moonshot
/model moonshot:k3 --global
```

`--global` also updates daemon defaults and scheduled automations that follow
the global model.

## Kimi Code Models

Use `/models moonshot` to fetch the IDs exposed by your configured service.
Add `--refresh` to reload the list, then select an ID with
`/model moonshot:<model-id> --global`. You can also enter an ID directly when
model discovery is unavailable. New provider model IDs need no Libre Claw update.

Legacy Libre Claw aliases remain accepted for existing configurations. Model
availability, context limits, and reasoning options depend on your membership
and the selected model.

## Default Configuration

```toml
[providers.moonshot]
service = "kimi_code"
api_key_env = "KIMI_API_KEY"
base_url = "https://api.kimi.com/coding/v1"
default_model = "k3"
max_tokens = 131072
reasoning_effort = "high"
thinking = "auto"
auto_context_window = true
```

`reasoning_effort` applies to `k3` and accepts `low`, `high`, or `max`.
Kimi Code maps the default to `high`.

Keep `thinking = "auto"` for all three Kimi Code models. Disabling thinking
causes the service to route away from K3/K2.7, so Libre Claw rejects that
combination instead of silently changing the requested model.

`max_tokens` is the user-controlled output cap. Kimi Code receives it through
the OpenAI-compatible `max_tokens` request field.

Set `auto_context_window = false` to keep the global
`[agent].context_window_tokens` value instead of the published model limit.

## Reasoning, Tools, and Vision

Kimi streams private reasoning in `reasoning_content`. Tool conversations need
that opaque reasoning and any `tool_calls` sent back with the next request.
Libre Claw preserves it for the Kimi continuation while keeping it out of
visible TUI, Telegram, and final-answer text. Provider-scoped reasoning is
removed before a fallback provider receives the conversation.

All three Kimi Code model IDs support image input and tool use. TUI attachments
and Telegram images are encoded as OpenAI-compatible image content.

## Fallbacks and Schedules

Kimi Code can be the primary, fallback, judge, heartbeat, or scheduled-run
provider:

```text
/fallback set 1 moonshot:k3 --global
/fallback set 2 openrouter:openrouter/auto --global
```

The dashboard provider selector also exposes the three official Kimi Code IDs.

## Moonshot Platform

Kimi Code membership keys and Moonshot Platform API keys are separate
credentials for separate endpoints. To use Platform intentionally, configure
it explicitly:

```toml
[providers.moonshot]
service = "platform"
api_key_env = "MOONSHOT_API_KEY"
base_url = "https://api.moonshot.ai/v1"
default_model = "<platform-model-id>"
max_tokens = 16384
reasoning_effort = "high"
thinking = "auto"
auto_context_window = false
```

Libre Claw does not rewrite model IDs or custom endpoints when `service` is
`platform`. Use the exact model ID shown in your Platform account.

## Troubleshooting

`Invalid Authentication` usually means the credential and endpoint do not
belong to the same service. Check these pairs first:

- Kimi Code key: `https://api.kimi.com/coding/v1`
- Moonshot Platform key: the Platform endpoint assigned to that account

Use an ID returned by `/models moonshot` or listed in your account. A 401 can
also indicate that the selected model or context size is not included in your
membership tier.

## Official References

- [Kimi Code overview](https://www.kimi.com/code/docs/en/)
- [Kimi Code model configuration](https://www.kimi.com/code/docs/en/kimi-code/models.html)
- [Kimi Code error reference](https://www.kimi.com/code/docs/en/kimi-code/error-reference.html)
- [Moonshot Platform overview](https://platform.kimi.ai/docs/overview)
