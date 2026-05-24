# Libre Claw Release Notes

## 0.1.0 - 2026-05-24

First shippable Libre Claw release from Kroonen AI Inc.

### Current State

- Terminal-native Textual TUI with streaming chat, Markdown rendering, command palette, slash-command suggestions, `/model` provider/model onboarding, file explorer, session status, and startup release notes.
- Provider support for Anthropic, OpenAI, OpenRouter, and Ollama. Ollama covers local daemon use, Ollama Cloud with `kimi-k2.6:cloud`, and Ollama/OpenAI-compatible endpoints.
- ReAct-style async agent loop with tool calling, concurrent tool execution, interrupt handling, context compaction, and configurable system prompt from TOML.
- Built-in `read_file`, `write_file`, `edit_file`, `list_directory`, and `bash` tools.
- Interactive TUI permission panel with approve, deny, always allow tool, and always allow exact command options. Dangerous sandbox-blocked commands show a warning and require one-time approval or denial.
- File explorer can move up to the parent directory, and the agent working directory follows the explorer root.
- TUI polish: thin blue scrollbars, blue user labels, purple Libre Claw assistant labels, dark/light theme support, and clickable file tree.
- SQLite memory for facts, sessions, summaries, and file edit logs.
- Telegram daemon with allowlist auth, streaming updates, and inline permission prompts.
- Key storage through environment variables, OS keyring, or encrypted local fallback.
- OAuth 2.0 PKCE and JWT scaffolding for a future dashboard.
- Apache-2.0 licensing with Kroonen AI Inc. source headers.
- GitHub Actions test/build CI.

### Known Limits

- Cost display is wired but still reports `$0.00`.
- Search, browser, git PR helpers, and richer diff UX are not yet at full parity with larger agent harnesses.
- GitLab `main` may remain protected; mirrored updates currently target the configured writable branch.

## Release Checklist

Use this checklist before publishing Libre Claw.

1. Update `src/libre_claw/__init__.py` and `pyproject.toml` to the same version.
2. Update `CHANGELOG.md` and this file with the release date and summary.
3. Run:

   ```bash
   python3 -m pytest
   python3 -m compileall src tests
   git diff --check
   python3 -m build
   ```

4. Inspect the wheel contents and confirm `libre_claw/default.toml` and `libre_claw/RELEASE.md` are included.
5. Install the wheel into a clean environment and run:

   ```bash
   libre-claw --version
   libre-claw --help
   libre-claw config defaults
   libre-claw auth status
   ```

6. Smoke-test at least one real provider before tagging.
