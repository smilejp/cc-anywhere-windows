"""Hook events for Claude Code integration."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class HookEventType(Enum):
    """Types of hook events from Claude Code."""

    STOP = "Stop"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    NOTIFICATION = "Notification"

    @classmethod
    def from_string(cls, value: str) -> Optional["HookEventType"]:
        """Convert string to HookEventType."""
        for event_type in cls:
            if event_type.value == value:
                return event_type
        return None


class NotificationType(Enum):
    """Types of notifications from Claude Code."""

    PERMISSION = "permission"  # Permission request
    IDLE = "idle"  # Idle timeout
    UNKNOWN = "unknown"


@dataclass
class HookEvent:
    """Represents a hook event from Claude Code.

    Claude Code sends different payloads for different events:
    - Stop: {"session_id": "...", "stop_hook_active": bool, "transcript_path": "..."}
    - PostToolUseFailure: {"session_id": "...", "tool_name": "...", "tool_input": {...}, "error": "..."}
    - Notification: {"session_id": "...", "title": "...", "body": "...", "type": "permission"|"idle"}
    """

    event_type: HookEventType
    session_id: str
    timestamp: datetime = field(default_factory=datetime.now)

    # Stop event fields
    stop_hook_active: Optional[bool] = None
    transcript_path: Optional[str] = None

    # PostToolUseFailure event fields
    tool_name: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    # Notification event fields
    title: Optional[str] = None
    body: Optional[str] = None
    notification_type: Optional[NotificationType] = None

    # Raw payload for reference
    raw_payload: Optional[dict[str, Any]] = None

    @classmethod
    def from_payload(
        cls,
        event_type_str: str,
        payload: dict[str, Any],
    ) -> Optional["HookEvent"]:
        """Create HookEvent from Claude Code hook payload.

        Args:
            event_type_str: The event type string (e.g., "Stop", "Notification")
            payload: The JSON payload from Claude Code hook

        Returns:
            HookEvent if valid, None otherwise
        """
        event_type = HookEventType.from_string(event_type_str)
        if not event_type:
            return None

        session_id = payload.get("session_id", "unknown")

        event = cls(
            event_type=event_type,
            session_id=session_id,
            raw_payload=payload,
        )

        if event_type == HookEventType.STOP:
            event.stop_hook_active = payload.get("stop_hook_active")
            event.transcript_path = payload.get("transcript_path")

        elif event_type == HookEventType.POST_TOOL_USE_FAILURE:
            event.tool_name = payload.get("tool_name")
            event.tool_input = payload.get("tool_input")
            event.error = payload.get("error")

        elif event_type == HookEventType.NOTIFICATION:
            event.title = payload.get("title")
            event.body = payload.get("body")
            notif_type = payload.get("type", "unknown")
            event.notification_type = NotificationType(notif_type) if notif_type in [
                e.value for e in NotificationType
            ] else NotificationType.UNKNOWN

        return event

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "event_type": self.event_type.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
        }

        if self.event_type == HookEventType.STOP:
            if self.stop_hook_active is not None:
                result["stop_hook_active"] = self.stop_hook_active
            if self.transcript_path:
                result["transcript_path"] = self.transcript_path

        elif self.event_type == HookEventType.POST_TOOL_USE_FAILURE:
            if self.tool_name:
                result["tool_name"] = self.tool_name
            if self.error:
                result["error"] = self.error

        elif self.event_type == HookEventType.NOTIFICATION:
            if self.title:
                result["title"] = self.title
            if self.body:
                result["body"] = self.body
            if self.notification_type:
                result["notification_type"] = self.notification_type.value

        return result

    def format_message(self) -> str:
        """Format event as a human-readable message."""
        if self.event_type == HookEventType.STOP:
            return f"Task completed (session: {self.session_id})"

        elif self.event_type == HookEventType.POST_TOOL_USE_FAILURE:
            tool = self.tool_name or "unknown"
            error = self.error or "Unknown error"
            return f"Tool execution failed\nTool: {tool}\nError: {error}"

        elif self.event_type == HookEventType.NOTIFICATION:
            title = self.title or "Notification"
            body = self.body or ""
            if self.notification_type == NotificationType.PERMISSION:
                return f"Permission request\n{title}\n{body}"
            elif self.notification_type == NotificationType.IDLE:
                return f"Idle state\n{title}\n{body}"
            else:
                return f"{title}\n{body}"

        return f"Event: {self.event_type.value}"
