"""FastAPI HTTP API for Libre Claw."""

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent import Agent
from .backends import BackendConfig, get_backend
from .config import Config
from .memory import MemoryManager
from .workspace import Workspace


# Request/Response models
class MessageRequest(BaseModel):
    """Request model for sending a message."""

    message: str
    context: Optional[Dict[str, str]] = None
    tools: Optional[List[Dict[str, Any]]] = None


class MessageResponse(BaseModel):
    """Response model for messages."""

    content: str
    session_id: str
    mode: str


class HeartbeatRequest(BaseModel):
    """Request model for heartbeat."""

    prompt: Optional[str] = None


class MemorySearchRequest(BaseModel):
    """Request model for memory search."""

    query: str
    memory_type: Optional[str] = None
    limit: int = 10


class MemoryAddRequest(BaseModel):
    """Request model for adding memory."""

    content: str
    memory_type: str = "general"
    importance: float = 0.5
    tags: Optional[List[str]] = None


class SessionInfo(BaseModel):
    """Session information."""

    session_id: str
    mode: str
    started_at: Optional[str]
    last_activity: Optional[str]
    message_count: int
    backend: str
    workspace: str


# Create FastAPI app
app = FastAPI(
    title="Libre Claw API",
    description="HTTP API for Libre Claw agentic AI framework",
    version="0.1.0",
)

# Global agent instance
_agent: Optional[Agent] = None


def get_agent() -> Agent:
    """Get or create the global agent instance."""
    global _agent
    if _agent is None:
        config = Config.load()
        backend = get_backend(
            config.backend.type,
            BackendConfig(
                claude_path=config.backend.claude_path,
                anthropic_api_key=config.backend.anthropic_api_key,
                ollama_url=config.backend.ollama_url,
                ollama_model=config.backend.ollama_model,
            ),
        )
        workspace = Workspace(config.workspace.path, config)
        memory = None
        if config.memory.enabled:
            memory = MemoryManager(config.memory.chromadb_url)

        _agent = Agent(backend=backend, workspace=workspace, config=config, memory=memory)

    return _agent


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Libre Claw API",
        "version": "0.1.0",
        "description": "HTTP API for Libre Claw agentic AI framework",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    agent = get_agent()
    return {
        "status": "healthy",
        "backend": agent.backend.name,
        "mode": agent.state.mode.value,
    }


@app.post("/message", response_model=MessageResponse)
async def send_message(request: MessageRequest):
    """Send a message to the agent.

    Args:
        request: Message request

    Returns:
        Agent response
    """
    agent = get_agent()

    try:
        response = agent.handle_message(
            message=request.message,
            context=request.context,
            tools=request.tools,
        )

        return MessageResponse(
            content=response,
            session_id=agent.state.session_id,
            mode=agent.state.mode.value,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/heartbeat")
async def trigger_heartbeat(request: HeartbeatRequest = None):
    """Trigger a heartbeat tick.

    Args:
        request: Optional heartbeat request

    Returns:
        Heartbeat response
    """
    agent = get_agent()

    try:
        prompt = request.prompt if request else None
        response = agent.handle_heartbeat(prompt=prompt)

        return {
            "content": response,
            "mode": agent.state.mode.value,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session", response_model=SessionInfo)
async def get_session():
    """Get current session information.

    Returns:
        Session information
    """
    agent = get_agent()
    info = agent.get_session_info()

    return SessionInfo(**info)


@app.post("/session/clear")
async def clear_session():
    """Clear conversation history."""
    agent = get_agent()
    agent.backend.clear_history()

    return {"status": "cleared", "session_id": agent.state.session_id}


@app.post("/memory/search")
async def search_memory(request: MemorySearchRequest):
    """Search long-term memory.

    Args:
        request: Search request

    Returns:
        Search results
    """
    agent = get_agent()

    if not agent.memory:
        raise HTTPException(status_code=503, detail="Memory not enabled")

    try:
        results = agent.search_memory(
            query=request.query,
            memory_type=request.memory_type,
            limit=request.limit,
        )

        return {"results": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/add")
async def add_memory(request: MemoryAddRequest):
    """Add a memory.

    Args:
        request: Memory to add

    Returns:
        Success status
    """
    agent = get_agent()

    if not agent.memory:
        raise HTTPException(status_code=503, detail="Memory not enabled")

    try:
        success = agent.remember(
            content=request.content,
            memory_type=request.memory_type,
            importance=request.importance,
            tags=request.tags,
        )

        if success:
            return {"status": "added"}
        else:
            raise HTTPException(status_code=500, detail="Failed to add memory")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/workspace/files")
async def list_workspace_files(pattern: str = "*.md"):
    """List workspace files.

    Args:
        pattern: File pattern

    Returns:
        List of files
    """
    agent = get_agent()
    files = agent.workspace.list_files(pattern)

    return {
        "files": [str(f.relative_to(agent.workspace.path)) for f in files]
    }


@app.get("/workspace/file/{filename}")
async def read_workspace_file(filename: str):
    """Read a workspace file.

    Args:
        filename: File name

    Returns:
        File contents
    """
    agent = get_agent()
    content = agent.workspace.read(filename)

    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    return {"filename": filename, "content": content}


@app.post("/workspace/file/{filename}")
async def write_workspace_file(filename: str, content: str):
    """Write to a workspace file.

    Args:
        filename: File name
        content: Content to write

    Returns:
        Success status
    """
    agent = get_agent()
    agent.workspace.write(filename, content)

    return {"status": "saved", "filename": filename}


def create_app(config: Optional[Config] = None) -> FastAPI:
    """Create FastAPI app with custom config.

    Args:
        config: Configuration

    Returns:
        FastAPI app
    """
    global _agent

    if config:
        _agent = None  # Force recreation with new config

    return app
