# SearXNG Web Search Integration

This tutorial explains how Libre Claw's `web_search` tool was added, how the
local SearXNG helper works, and how to maintain or extend it.

The goal was to give the agent a production-friendly web search path without
asking it to scrape search pages through `bash` or drive a full browser for
simple lookup tasks. SearXNG is self-hostable, supports multiple upstream search
engines, and can return JSON when configured correctly.

## What Was Added

The integration has four pieces:

- A first-class `web_search` tool in
  `src/libre_claw/tools_builtin/web_search.py`.
- Config defaults in `config/default.toml` and the packaged
  `src/libre_claw/default.toml`.
- A local SearXNG Docker helper in `src/libre_claw/core/searxng.py`.
- CLI commands under `libre-claw searx` in `src/libre_claw/cli.py`.

The tool is read-only and has `permission_level = "allow"`, so it can run
without interrupting the user. It returns compact text for the model plus
structured metadata for the harness, Telegram bridge, tests, and future UI
surfaces.

## Configuration

Libre Claw reads the search configuration from `[web_search]`:

```toml
[web_search]
enabled = true
provider = "searxng"
base_url = "http://127.0.0.1:8888"
timeout = 15
max_results = 10
default_language = "auto"
default_safesearch = 0
default_categories = ["general"]
default_engines = []
```

The default points to a private local SearXNG instance on
`http://127.0.0.1:8888`. Users can point `base_url` at another trusted SearXNG
instance if they run one elsewhere.

## Local SearXNG Helper

The CLI helper writes files under `~/.libre-claw/searxng/`:

```bash
libre-claw searx init
```

That creates:

- `docker-compose.yml`
- `settings.yml`
- `.env`

The generated compose file uses the official `searxng/searxng:latest` image and
binds it to `127.0.0.1:8888`. Keeping it on loopback avoids exposing search to
the network by default.

The generated `settings.yml` enables JSON output:

```yaml
search:
  formats:
    - html
    - json
```

This is the important part. Without `json`, SearXNG may respond with HTTP 403
for `/search?format=json`, and Libre Claw will report that JSON output is
disabled.

Start, inspect, test, and stop the local instance with:

```bash
libre-claw searx up
libre-claw searx status
libre-claw searx test
libre-claw searx down
```

`libre-claw searx test` performs a real JSON search against the configured base
URL and verifies that the response includes a `results` list.

### Auto-start on daemon launch

When `web_search.enabled` is true, `web_search.provider` is `searxng`, and
`base_url` points at a loopback address (`127.0.0.1`, `localhost`, `::1`,
`0.0.0.0`), `libre-claw start` and `libre-claw daemon` bring the local SearXNG
container up automatically before the daemon starts listening. This means you do
not need to run `libre-claw searx up` manually after setup — the daemon ensures
the instance is available at `http://127.0.0.1:8888`.

The auto-start is best-effort: if Docker is missing, times out, or returns a
non-zero exit, the daemon prints a skip line and continues normally. Users who
point `base_url` at a remote SearXNG, or who disable web search, are never
touched. The generated compose file uses `restart: unless-stopped`, so once the
container has been started it is also restarted by Docker across host reboots
as long as Docker is running.

## Tool Request Flow

When the model calls `web_search`, the tool:

1. Checks that web search is enabled.
2. Verifies that the provider is `searxng`.
3. Validates the query, page, and result limit.
4. Verifies that `base_url` is an absolute `http` or `https` URL.
5. Builds SearXNG query parameters:
   - `q`
   - `format=json`
   - `pageno`
   - optional categories
   - optional engines
   - optional language
   - optional safesearch
   - optional time range
6. Calls `{base_url}/search` with `httpx.AsyncClient`.
7. Handles network failures, non-2xx responses, JSON-disabled 403s, and malformed
   payloads as `ToolResult(error=...)`.
8. Normalizes result fields into compact dictionaries.
9. Returns both a readable result list and structured metadata.

The formatted content looks like this:

```text
web_search: libre claw
source: http://127.0.0.1:8888
results: 2

1. Libre Claw
   url: https://libreclaw.sh
   snippet: Terminal-native agent harness
   engine: duckduckgo
```

The metadata keeps the normalized result objects:

```json
{
  "artifact_type": "web_search",
  "provider": "searxng",
  "query": "libre claw",
  "returned_results": 1,
  "result_count": 2,
  "results": [
    {
      "title": "Libre Claw",
      "url": "https://libreclaw.sh",
      "snippet": "Terminal-native agent harness",
      "engine": "duckduckgo"
    }
  ]
}
```

That split matters: the model gets a concise answer, while the app still has
machine-readable data for summaries, logs, and UI rendering.

## Agent And Telegram Behavior

The tool is registered like the other built-ins through
`src/libre_claw/tools_builtin/__init__.py`. The tool context copies values from
the loaded config into `ToolContext`, so every run uses the active user config.

The Telegram bridge special-cases `web_search` activity so scheduled jobs do not
spam full raw result payloads into chat. It sends compact notices such as a
search query and result count while keeping the complete results available to
the model and durable run archive.

## Testing Strategy

The integration is tested without a real SearXNG server or network dependency.

`tests/test_tools.py` uses fake async HTTP clients to verify:

- successful result normalization
- result limiting
- category parameter formatting
- JSON-disabled 403 handling
- empty query validation
- max result and page bounds

`tests/test_config.py` verifies default config values.

`tests/test_imports.py` verifies the tool module imports with the rest of the
package.

When changing this integration, run at least:

```bash
.venv/bin/python -m pytest tests/test_tools.py tests/test_config.py tests/test_imports.py
```

For release changes, run the full suite:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall src tests
git diff --check
```

## Debugging

If `web_search` fails with a JSON error:

1. Run `libre-claw searx test`.
2. Check that `settings.yml` contains `json` under `search.formats`.
3. Restart SearXNG with `libre-claw searx down` then `libre-claw searx up`.
4. Check container status with `libre-claw searx status`.

If Docker cannot be found, install Docker Desktop, Docker Engine, or a compatible
local runtime such as Colima. On macOS with Colima, make sure your shell can see
the Docker socket that Colima exposes before running `libre-claw searx up`.

If results are empty, open the SearXNG web UI at `http://127.0.0.1:8888` and run
the same query manually. SearXNG's available engines depend on its configuration
and on upstream availability.

## Extending The Integration

Good follow-up improvements:

- Add a dashboard health card for SearXNG status.
- Add `/web-search status` or a setup wizard check in the TUI.
- Support per-run search categories for scheduled jobs.
- Add optional result caching for repeated automation searches.
- Add a second provider behind the same `web_search` contract if Libre Claw ever
  needs a hosted search backend.

Keep the model-facing tool output compact. Search tools are easy to make noisy,
and large raw result payloads quickly pollute Telegram runs and the context
window.
