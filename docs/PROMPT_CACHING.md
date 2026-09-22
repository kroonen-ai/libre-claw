# Prompt caching

Libre Claw keeps repeated prompt content stable so providers can reuse input
tokens across tool calls. Caching reuses prompt processing; each response is still
generated normally. It does not reuse old answers or skip tools.

## Defaults

| Provider | Libre Claw behavior |
| --- | --- |
| DeepSeek | Preserves exact prefixes for the API's automatic cache. No cache parameter is required. |
| Anthropic | Marks tools and system content as reusable and enables the advancing conversation breakpoint. Uses the default five-minute lifetime. |
| OpenAI | Supplies a stable routing key derived from the model, reusable instructions, and tools. Leaves retention at the provider's default. |
| OpenRouter | Supplies a conversation-derived routing key to keep requests on the same provider when available. Enables automatic caching for explicit Anthropic model routes. |
| Other compatible or local endpoints | Keeps prompts stable without sending unsupported native cache parameters. The server controls prefix reuse. |

Provider caching is best effort. A short prompt, expired cache, changed context,
model switch, or routing change can produce misses. There is no guaranteed hit
rate or fixed cost reduction. See the official guides for
[DeepSeek](https://api-docs.deepseek.com/guides/kv_cache/),
[Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching), and
[OpenRouter](https://openrouter.ai/docs/guides/best-practices/prompt-caching).

## What stays stable

Tool definitions use deterministic ordering and JSON serialization. Durable
instructions come before changing task state. Timed runs keep one fixed deadline
in their prompt instead of rewriting a countdown on every call; the runtime
still enforces the live deadline.

Anthropic requests place reusable instructions and changing task state in separate
blocks. Routing keys stay stable through checkpoint updates; conversation routing
can also survive compaction. Plans, checkpoints, memory, and project instructions
still update when necessary, invalidating the affected suffix. Compaction keeps the
existing recovery behavior and runs only when the context budget requires it;
tool estimates refresh when provider capabilities change.

## Optional controls

Add these settings to the corresponding tables in `~/.libre-claw/config.toml`:

```toml
[providers.anthropic]
prompt_caching = true
prompt_cache_ttl = "5m" # "1h" opts into a longer lifetime and higher write rate

[providers.openai]
prompt_caching = true
# Usually leave this unset so Libre Claw derives a stable key.
# prompt_cache_key = "my-workspace"

[providers.openrouter]
prompt_caching = true
```

Leave `prompt_caching` unset to use endpoint-aware defaults. Anthropic-compatible
custom endpoints require an explicit `true` and support for Anthropic cache
controls. OpenAI/OpenRouter native hints are restricted to their official
endpoints. Setting `false` disables Libre Claw's added hints; it cannot disable
automatic caching performed independently by a provider. No additional retention
is requested by default. Generated routing keys contain hashes, not prompt text
or API keys.

## Verify reuse

Open **Settings → Usage** in the dashboard, or run `/usage <provider>` in the
TUI or Telegram. Reports show cached input and cache writes when the provider
reports them. The dashboard also shows reported reuse as cached input divided by
total input and includes reused tokens for each model.

Input totals include cache reads and writes. These are subsets, so they are not
added again to total tokens. Anthropic reports them separately; Libre Claw
normalizes that accounting. DeepSeek cache misses are ordinary input, not cache
writes. Older records with no cache accounting remain readable and contribute no
reported hits. Costs remain unknown unless the provider supplies them.

Compare a sequence of requests using the same model and tools. The first request
may populate the cache; later tool rounds can reuse it. Avoid manually clearing
or compacting history solely to save tokens: doing so can discard reusable
prefixes and necessary context.
