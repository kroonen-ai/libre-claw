# Libre Claw 🐾

An agentic AI framework by [Kroonen AI Inc.](https://kroonen.ai)

Libre Claw wraps AI backends (Claude Code CLI, Ollama, Anthropic API) into a persistent agent with workspace management, heartbeat autonomy, semantic memory, and a polished terminal UI.

## Features

- **Multiple backends** — Claude Code CLI, Ollama (local), Anthropic API (planned)
- **Workspace system** — Markdown-based context files (SOUL.md, USER.md, AGENTS.md, etc.)
- **Mode-aware context** — Direct mode loads MEMORY.md, heartbeat mode loads HEARTBEAT.md
- **Heartbeat autonomy** — Async heartbeat loop for autonomous task execution
- **Semantic memory** — ChromaDB integration for long-term memory search/storage
- **Rich TUI** — Terminal UI with slash commands, markdown rendering, and spinners
- **HTTP API** — FastAPI server for programmatic access
- **Cost tracking** — Token usage and cost estimation
- **Git sync** — Auto-commit and push workspace changes
- **Daily notes** — Automatic `memory/YYYY-MM-DD.md` journal entries

## Quick Start

```bash
# Clone
git clone https://github.com/kroonen-ai/libre-claw.git
cd libre-claw

# Install
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Initialize workspace
libre-claw --init ~/my-workspace

# Start TUI
libre-claw -w ~/my-workspace

# Or start API server
libre-claw --api -w ~/my-workspace
```

## Workspace Structure

```
my-workspace/
├── SOUL.md              # Agent personality and traits
├── USER.md              # User profile
├── IDENTITY.md          # Agent identity
├── AGENTS.md            # Operating rules (direct vs heartbeat mode)
├── MEMORY.md            # Long-term curated memory
├── HEARTBEAT.md         # Autonomous task checklist
├── HEARTBEAT-AUDIT.md   # Heartbeat run log
├── INFRA.md             # Infrastructure notes
├── TOOLS.md             # Local tool configuration
├── config.yaml          # Framework configuration
├── heartbeat-state.json # Heartbeat state tracking
└── memory/
    └── 2026-02-15.md    # Daily notes
```

## TUI Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation history |
| `/info` | Show session information |
| `/memory <query>` | Search long-term memory |
| `/heartbeat` | Trigger manual heartbeat |
| `/mode [direct\|heartbeat]` | Show/switch mode |
| `/context` | Show loaded context files |
| `/daily <text>` | Append to today's daily note |
| `/files` | List workspace files |
| `/read <file>` | Read a workspace file |
| `/cost` | Show token usage |
| `/quit` | Exit |

## Configuration

```yaml
# config.yaml
backend:
  type: claude_code           # claude_code, ollama, anthropic
  claude_path: /opt/homebrew/bin/claude
  ollama_url: http://localhost:11434
  ollama_model: llama3

workspace:
  path: ~/.libre-claw/workspace

heartbeat:
  enabled: true
  interval_seconds: 30

memory:
  enabled: true
  chromadb_url: http://localhost:8420

git:
  enabled: true
  auto_commit: true
  remote: origin
```

Environment variables override config: `LIBRE_CLAW_BACKEND__TYPE=ollama`

## Backends

### Claude Code CLI (default)
Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed:
```bash
npm install -g @anthropic-ai/claude-code
```

### Ollama (local)
Requires [Ollama](https://ollama.ai) running:
```bash
ollama serve
ollama pull llama3
libre-claw --backend ollama
```

### Anthropic API (planned)
Direct API access — coming in v0.2.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check libre_claw/

# Type check
mypy libre_claw/
```

## License

Apache 2.0 — © 2026 Kroonen AI Inc.
