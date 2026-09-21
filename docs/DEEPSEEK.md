<!--
Copyright 2026 Kroonen AI (https://kroonen.ai)
SPDX-License-Identifier: Apache-2.0
-->

# DeepSeek

Libre Claw connects directly to DeepSeek's Chat Completions API for streaming
answers, tool calls, and thinking. The same provider works in the TUI, Telegram,
dashboard, headless tasks, goals, fallback routes, and scoped workers.

## Setup

Get a key from the [DeepSeek platform](https://platform.deepseek.com/api_keys), then
store it securely:

```bash
libre-claw auth set-key deepseek
libre-claw auth status
```

Alternatively, set `DEEPSEEK_API_KEY` in your environment. Inside the TUI:

```text
/setup deepseek
/models deepseek --refresh
/model deepseek:<model-id> --global
```

`/setup deepseek` saves your configured DeepSeek default; new installs use
`deepseek-flash`. You can store the key inside the TUI with `/setup key deepseek`.
Telegram and dashboard model pickers include DeepSeek and use the same catalog.
Models come from the authenticated [`/models` endpoint](https://api-docs.deepseek.com/api/list-models/).
Manual IDs remain available if discovery fails or a new model is not listed yet.

## Thinking and output

Edit `~/.libre-claw/config.toml` to customize the provider:

```toml
[providers.deepseek]
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
default_model = "deepseek-flash"
max_tokens = 65536
thinking = "enabled"
reasoning_effort = "high"
```

Use `low`, `high`, or `max` effort, or set `thinking = "disabled"`. Thinking is
enabled with high effort by default. Libre Claw keeps DeepSeek reasoning in
session history and returns it with subsequent tool requests, as required by
[DeepSeek's thinking protocol](https://api-docs.deepseek.com/guides/thinking_mode/).
Reasoning is separate from the visible answer. Temperature is omitted in thinking
mode because DeepSeek ignores it. `max_tokens` limits the whole completion,
including reasoning.

If you switch a conversation from another provider or non-thinking mode,
DeepSeek reasoning may be missing from its history. Start a new session or use
`thinking = "disabled"` to continue that conversation with tools.

## Images, limits, and usage

DeepSeek's model list supplies IDs, without capability or context limits.
Libre Claw keeps unpublished metadata unknown and uses configured limits. Add
overrides for your selected model when needed. For example, DeepSeek documents
[image input for `deepseek-flash`](https://api-docs.deepseek.com/guides/vision/):

```toml
[providers.deepseek.model_capabilities.deepseek-flash]
supports_tools = true
supports_vision = true
supports_reasoning = true
supported_reasoning_efforts = ["low", "high", "max"]
```

Use normal TUI attachments or Telegram photos with a model that supports images.
Confirm support before selecting another model. See
[capability overrides](CODING_WORKFLOWS.md#model-discovery-and-capability-overrides)
for context/output limits and per-model controls.

Usage records include input, output, reasoning, and cached-input tokens when
DeepSeek reports them. Billed cost stays unknown unless the API reports it;
Libre Claw does not hardcode DeepSeek's changing prices. Check
[DeepSeek's pricing page](https://api-docs.deepseek.com/quick_start/pricing/) for
current rates.
