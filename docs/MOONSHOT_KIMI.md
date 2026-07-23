# Moonshot AI / Kimi Integration

Libre Claw supports the Moonshot AI Platform as a first-class provider. It
uses the official OpenAI-compatible Chat Completions API while preserving
Kimi-specific reasoning, tool-calling, context, and token behavior.

## Set Up

Create a Moonshot Platform API key, then store it through Libre Claw:

```bash
libre-claw auth set-key moonshot
libre-claw auth status
```

You can use `MOONSHOT_API_KEY` instead:

```bash
export MOONSHOT_API_KEY="..."
```

Select a direct Kimi model from the TUI or Telegram:

```text
/setup moonshot
/model moonshot:kimi-k3 --global
```

`--global` also updates daemon defaults and scheduled automations that follow
the global model.

## Supported Models

| Model | Context | Output setting | Notes |
| --- | ---: | ---: | --- |
| `kimi-k3` | 1,048,576 | Up to 1,048,576 | Flagship reasoning model with native vision. |
| `kimi-k2.7-code` | 262,144 | Up to 32,768 | Coding model; thinking is required. |
| `kimi-k2.7-code-highspeed` | 262,144 | Up to 32,768 | High-speed K2.7 Code endpoint. |
| `kimi-k2.6` | 262,144 | Up to 32,768 | General model with vision and optional non-thinking mode. |

Libre Claw applies these published context limits locally, so `/status`, the
TUI context meter, the daemon, Telegram, and scheduled runs do not need a
metadata request before each run.

## Configuration

The packaged defaults are:

```toml
[providers.moonshot]
api_key_env = "MOONSHOT_API_KEY"
base_url = "https://api.moonshot.ai/v1"
default_model = "kimi-k3"
max_tokens = 131072
reasoning_effort = "max"
thinking = "auto"
auto_context_window = true
```

`reasoning_effort` applies to Kimi K3 and accepts `low`, `high`, or `max`.

`thinking = "disabled"` is supported only by Kimi K2.6. Kimi K3 and Kimi
K2.7 require thinking, and Libre Claw rejects a configuration that attempts to
disable it.

`max_tokens` remains the user-controlled output cap. Libre Claw maps it to
Kimi K3's `max_completion_tokens` field and to the `max_tokens` field used by
Kimi K2.6 and K2.7.

Set `auto_context_window = false` to keep the global
`[agent].context_window_tokens` value instead of the published model limit.

## Reasoning And Tool Calls

Kimi streams private reasoning in `reasoning_content`. Moonshot requires the
complete assistant response, including that reasoning and any `tool_calls`, to
be sent back on the next request in a tool loop.

Libre Claw therefore:

1. Keeps reasoning out of visible TUI, Telegram, and final assistant text.
2. Stores it as an opaque, provider-scoped session block.
3. Sends it back only to Moonshot when continuing the same tool conversation.
4. Removes it before another provider handles the history or a fallback takes
   over.

This preserves Kimi's multi-turn tool behavior without exposing chain-of-thought
as user-facing output or leaking it across providers.

## Vision

Kimi K3, Kimi K2.7 Code, and Kimi K2.6 accept images. TUI image attachments
and Telegram image messages are encoded as base64 `image_url` content for the
Moonshot request.

## Fallbacks And Schedules

Moonshot can be primary, fallback, judge, heartbeat, or scheduled-run provider:

```text
/fallback set 1 moonshot:kimi-k3 --global
/fallback set 2 openrouter:openrouter/auto --global
```

The dashboard provider controls also include Moonshot AI / Kimi. A schedule can
select `moonshot` plus one of the direct model IDs above.

## Platform API Versus Kimi Code

This integration targets the Moonshot Platform API:

```text
https://api.moonshot.ai/v1
```

Moonshot's Kimi Code subscription uses a separate endpoint, credential type,
and model alias. A Kimi Code subscription key is not interchangeable with a
Moonshot Platform API key. Use a Platform API key with the direct model IDs in
this guide.

## Official References

- [Moonshot Platform quickstart](https://platform.kimi.ai/docs/overview)
- [Moonshot model list](https://platform.kimi.ai/docs/models)
- [Kimi K3 guide](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)
- [API errors](https://platform.kimi.ai/docs/api/errors)
- [Rate limits](https://platform.kimi.ai/docs/pricing/limits)
