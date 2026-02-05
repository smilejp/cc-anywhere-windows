"""Web Terminal Dashboard using FastAPI and WebSocket."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core import (
    SessionAlreadyExistsError,
    SessionLimitError,
    SessionManager,
    SessionNotFoundError,
)
from ..core.name_generator import generate_unique_name
from ..core.events import HookEvent
from ..core.event_bus import get_event_bus
from ..core import git_utils

logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="CC-Anywhere Windows", version="0.1.0")

# Session manager instance (will be set by main.py)
session_manager: Optional[SessionManager] = None

# Active WebSocket connections per session
active_connections: dict[str, list[WebSocket]] = {}

# Static files path (in src/cc_anywhere/static)
STATIC_PATH = Path(__file__).parent.parent / "static"


def set_session_manager(manager: SessionManager) -> None:
    """Set the session manager instance."""
    global session_manager
    session_manager = manager


# Mount static files
if STATIC_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


# Pydantic models
class CreateSessionRequest(BaseModel):
    """Request to create a new session."""

    name: str
    working_dir: Optional[str] = None
    create_worktree: bool = False
    worktree_branch: Optional[str] = None
    cleanup_worktree: bool = True


class SendInputRequest(BaseModel):
    """Request to send input to a session."""

    text: str


class SessionResponse(BaseModel):
    """Session response model."""

    id: str
    name: str
    working_dir: str
    status: str
    created_at: str
    last_activity: str


# REST API endpoints
@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard HTML."""
    html_path = STATIC_PATH / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text())
    return HTMLResponse(content="<h1>CC-Anywhere Windows</h1><p>Static files not found.</p>")


@app.get("/api/sessions/random-name")
async def get_random_session_name() -> dict:
    """Generate a random memorable session name."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    # Get existing session names to avoid duplicates
    existing_names = [s.name for s in session_manager.list_sessions()]
    name = generate_unique_name(existing_names)

    return {"name": name}


@app.get("/api/sessions")
async def list_sessions() -> list[dict]:
    """List all sessions."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    sessions = session_manager.list_sessions()
    return [s.to_dict() for s in sessions]


@app.post("/api/sessions")
async def create_session(request: CreateSessionRequest) -> dict:
    """Create a new session."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        session = await session_manager.create_session(
            name=request.name,
            working_dir=request.working_dir,
            create_worktree=request.create_worktree,
            worktree_branch=request.worktree_branch,
            cleanup_worktree=request.cleanup_worktree,
        )
        return session.to_dict()

    except SessionAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SessionLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get session details."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        session = session_manager.get_session(session_id)
        return session.to_dict()
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    """Delete a session."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        await session_manager.destroy_session(session_id)
        return {"status": "deleted", "session_id": session_id}
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sessions")
async def delete_all_sessions() -> dict:
    """Delete all sessions."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        count = await session_manager.destroy_all_sessions()
        return {"status": "deleted", "count": count}
    except Exception as e:
        logger.error(f"Failed to delete all sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_command(session_id: str) -> dict:
    """Cancel running command in session (Ctrl+C)."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        await session_manager.cancel_command(session_id)
        return {"status": "cancelled", "session_id": session_id}
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to cancel command: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/input")
async def send_input(session_id: str, request: SendInputRequest) -> dict:
    """Send input to a session."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        await session_manager.send_input(session_id, request.text)
        return {"status": "sent", "session_id": session_id}
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to send input: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/key")
async def send_key(session_id: str, key: str) -> dict:
    """Send special key to a session."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        await session_manager.send_key(session_id, key)
        return {"status": "sent", "session_id": session_id, "key": key}
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to send key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# WezTerm pane discovery and import
@app.get("/api/wezterm/panes")
async def discover_wezterm_panes() -> list[dict]:
    """Discover all WezTerm panes in our workspace."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    return await session_manager.discover_wezterm_panes()


class ImportSessionRequest(BaseModel):
    """Request to import a WezTerm pane."""

    pane_id: int
    name: Optional[str] = None
    working_dir: Optional[str] = None


class HookEventRequest(BaseModel):
    """Request from Claude Code hook."""

    event_type: str
    payload: dict


@app.post("/api/wezterm/import")
async def import_wezterm_pane(request: ImportSessionRequest) -> dict:
    """Import an external WezTerm pane."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        session = await session_manager.import_session(
            pane_id=request.pane_id,
            name=request.name,
            working_dir=request.working_dir,
        )
        return session.to_dict()

    except SessionAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to import session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wezterm/import-all")
async def import_all_wezterm_panes() -> dict:
    """Import all unmanaged WezTerm panes in our workspace."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    try:
        imported = await session_manager.import_all_sessions()
        return {
            "imported": len(imported),
            "sessions": [s.to_dict() for s in imported],
        }
    except Exception as e:
        logger.error(f"Failed to import sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============ Hook Events API ============


@app.post("/api/hooks")
async def receive_hook_event(request: HookEventRequest) -> dict:
    """Receive hook event from Claude Code.

    This endpoint receives events from the cc-hook.ps1 script which is
    called by Claude Code hooks. Events are published to the EventBus
    for adapters to consume.
    """
    logger.info(f"Received hook event: {request.event_type}")

    # Parse the event
    event = HookEvent.from_payload(request.event_type, request.payload)
    if event is None:
        logger.warning(f"Unknown event type: {request.event_type}")
        return {"status": "ignored", "reason": "unknown event type"}

    # Publish to event bus
    event_bus = get_event_bus()
    await event_bus.publish(event)

    # Broadcast to all WebSocket connections for the session
    await broadcast_hook_event(event)

    return {"status": "ok", "event_type": request.event_type}


async def broadcast_hook_event(event: HookEvent) -> None:
    """Broadcast hook event to WebSocket connections.

    Sends the event to all connected terminals for the session
    and to the monitor WebSocket.
    """
    message = {
        "type": "hook_event",
        "data": event.to_dict(),
    }

    # Broadcast to session connections
    session_id = event.session_id
    if session_id in active_connections:
        for ws in active_connections[session_id]:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to broadcast hook event: {e}")


# WebSocket endpoint for terminal
@app.websocket("/ws/{session_id}")
async def terminal_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for terminal interaction."""
    if session_manager is None:
        await websocket.close(code=1011, reason="Session manager not initialized")
        return

    # Verify session exists
    try:
        session_manager.get_session(session_id)
    except SessionNotFoundError:
        await websocket.close(code=1008, reason="Session not found")
        return

    await websocket.accept()

    # Track connection
    if session_id not in active_connections:
        active_connections[session_id] = []
    active_connections[session_id].append(websocket)

    logger.info(f"WebSocket connected for session {session_id}")

    # Clear output cache for fresh start
    session_manager.clear_output_cache(session_id)

    # Task for streaming output
    output_task = None
    resized = False  # Track if initial resize has been done

    try:
        # Start output streaming (will begin after resize)
        async def stream_output():
            try:
                async for output in session_manager.stream_output(session_id, interval=0.2):
                    if output.strip():
                        await websocket.send_json({
                            "type": "output",
                            "data": output,
                        })
            except Exception as e:
                logger.error(f"Output streaming error: {e}")

        # Handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                if msg_type == "input":
                    # Regular text input (with Enter)
                    text = data.get("data", "")
                    await session_manager.send_input(session_id, text)

                elif msg_type == "key":
                    # Special key (without Enter)
                    key = data.get("data", "")
                    await session_manager.send_key(session_id, key)

                elif msg_type == "ping":
                    # Heartbeat ping - respond with pong
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "resize":
                    # Terminal resize - sync pane size with xterm.js
                    cols = data.get("cols", 80)
                    rows = data.get("rows", 24)
                    await session_manager.resize_pane(session_id, cols, rows)

                    # On first resize, redraw screen and start streaming
                    if not resized:
                        resized = True
                        # Send Ctrl+L to redraw the screen with correct size
                        await session_manager.send_key(session_id, "C-l")
                        await asyncio.sleep(0.1)

                        # Now start output streaming
                        output_task = asyncio.create_task(stream_output())

                        # Send current output
                        try:
                            initial = await session_manager.read_output(session_id)
                            if initial.content.strip():
                                await websocket.send_json({
                                    "type": "output",
                                    "data": initial.content,
                                })
                        except Exception as e:
                            logger.error(f"Failed to send initial output: {e}")

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

            except json.JSONDecodeError:
                logger.warning("Invalid JSON received")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

    finally:
        # Cleanup
        if output_task:
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass

        if session_id in active_connections:
            if websocket in active_connections[session_id]:
                active_connections[session_id].remove(websocket)
            if not active_connections[session_id]:
                del active_connections[session_id]


# WebSocket for monitoring all sessions
@app.websocket("/ws/monitor")
async def monitor_websocket(websocket: WebSocket):
    """WebSocket endpoint for monitoring all sessions."""
    if session_manager is None:
        await websocket.close(code=1011, reason="Session manager not initialized")
        return

    await websocket.accept()
    logger.info("Monitor WebSocket connected")

    try:
        while True:
            # Send session list periodically
            sessions = session_manager.list_sessions()
            await websocket.send_json({
                "type": "sessions",
                "data": [s.to_dict() for s in sessions],
            })

            # Wait for next update or message
            try:
                await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                pass  # Just send next update

    except WebSocketDisconnect:
        logger.info("Monitor WebSocket disconnected")
    except Exception as e:
        logger.error(f"Monitor WebSocket error: {e}")


async def broadcast_to_session(session_id: str, message: dict) -> None:
    """Broadcast message to all connections for a session."""
    if session_id in active_connections:
        for ws in active_connections[session_id]:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to broadcast: {e}")


# ============ History API ============


@app.get("/api/history")
async def list_history_sessions():
    """List all sessions with history logs."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    sessions = session_manager.logger.get_all_sessions()
    return sessions


@app.get("/api/history/{session_id}")
async def get_session_history(
    session_id: str,
    limit: Optional[int] = None,
    entry_type: Optional[str] = None,
):
    """Get history for a specific session.

    Args:
        session_id: Session ID
        limit: Maximum number of entries to return
        entry_type: Filter by type (input, output, system)
    """
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    entries = session_manager.logger.get_history(
        session_id, limit=limit, entry_type=entry_type
    )
    return entries


@app.get("/api/history/{session_id}/stats")
async def get_session_stats(session_id: str):
    """Get statistics for a session."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    stats = session_manager.logger.get_stats(session_id)
    return stats


@app.delete("/api/history/{session_id}")
async def delete_session_history(session_id: str):
    """Delete history for a session."""
    if session_manager is None:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    deleted = session_manager.logger.delete_history(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="History not found")

    return {"status": "deleted"}


# ============ Directory Browser API ============


@app.get("/api/browse")
async def browse_directory(path: Optional[str] = None):
    """Browse directories on the server.

    Args:
        path: Directory path to browse. Defaults to home directory.

    Returns:
        Dictionary with current path, parent path, and list of directories.
    """
    import os

    # Default to home directory
    if not path or path == "~":
        path = os.path.expanduser("~")
    else:
        # Expand ~ in path
        path = os.path.expanduser(path)

    # Resolve to absolute path
    try:
        abs_path = Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Check if path exists and is a directory
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not abs_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    # Get parent path
    parent = str(abs_path.parent) if abs_path != abs_path.parent else None

    # Get home directory for display path conversion
    home = os.path.expanduser("~")

    def to_display_path(p: str) -> str:
        """Convert absolute path to display path with ~."""
        if p.startswith(home):
            return "~" + p[len(home):]
        return p

    # List directories only (not files)
    directories = []
    try:
        for item in sorted(abs_path.iterdir()):
            # Skip hidden files/directories
            if item.name.startswith("."):
                continue
            # Only include directories
            if item.is_dir():
                try:
                    # Check if accessible
                    list(item.iterdir())
                    item_path = str(item)
                    directories.append({
                        "name": item.name,
                        "path": item_path,
                        "display_path": to_display_path(item_path),
                    })
                except PermissionError:
                    # Skip inaccessible directories
                    pass
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Convert home path back to ~ for display
    display_path = to_display_path(str(abs_path))

    return {
        "path": str(abs_path),
        "display_path": display_path,
        "parent": parent,
        "directories": directories,
    }


# ============ Git Info API ============


@app.get("/api/git/info")
async def get_git_info(path: Optional[str] = None):
    """Get Git information for a directory.

    Args:
        path: Directory path to check. Defaults to home directory.

    Returns:
        Dictionary with Git info:
        - is_git_repo: bool
        - root: str (repository root) or null
        - current_branch: str or null
        - worktrees: list of worktree info
    """
    import os

    # Default to home directory
    if not path or path == "~":
        path = os.path.expanduser("~")
    else:
        path = os.path.expanduser(path)

    return git_utils.get_git_info(path)
