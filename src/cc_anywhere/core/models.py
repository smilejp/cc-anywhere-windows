"""Data models for CC-Anywhere Windows."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionStatus(Enum):
    """Session status enumeration."""

    STARTING = "starting"
    ACTIVE = "active"
    IDLE = "idle"
    WAITING_INPUT = "waiting_input"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class Session:
    """Represents a Claude Code session."""

    id: str
    name: str
    working_dir: str
    status: SessionStatus = SessionStatus.STARTING
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    wezterm_pane_id: Optional[int] = None

    # Git worktree fields
    worktree_path: Optional[str] = None
    worktree_branch: Optional[str] = None
    cleanup_worktree: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "name": self.name,
            "working_dir": self.working_dir,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }

        # Include WezTerm pane ID
        if self.wezterm_pane_id is not None:
            result["wezterm_pane_id"] = self.wezterm_pane_id

        # Include worktree info if present
        if self.worktree_path:
            result["worktree_path"] = self.worktree_path
            result["worktree_branch"] = self.worktree_branch

        return result

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now()


@dataclass
class SessionOutput:
    """Output from a session."""

    session_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    is_waiting_input: bool = False
