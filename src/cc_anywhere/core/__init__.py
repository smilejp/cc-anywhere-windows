"""Core components: session management with WezTerm backend."""

from .exceptions import (
    CCException,
    SessionAlreadyExistsError,
    SessionError,
    SessionLimitError,
    SessionNotFoundError,
    WezTermError,
    WezTermNotFoundError,
    WezTermPaneError,
)
from .logger import SessionLogger
from .models import Session, SessionOutput, SessionStatus
from .session import SessionManager
from .name_generator import generate_session_name, generate_unique_name
from .events import HookEvent, HookEventType, NotificationType
from .event_bus import EventBus, get_event_bus
from . import git_utils
from .summarizer import OutputAnalysis, OutputPatternType, OutputSummarizer, PatternMatch
from .formatters import BaseFormatter, FormattedMessage, MessengerFormatter, create_formatter

__all__ = [
    # Manager
    "SessionManager",
    # Logger
    "SessionLogger",
    # Models
    "Session",
    "SessionOutput",
    "SessionStatus",
    # Name Generator
    "generate_session_name",
    "generate_unique_name",
    # Events
    "HookEvent",
    "HookEventType",
    "NotificationType",
    "EventBus",
    "get_event_bus",
    # Git Utils
    "git_utils",
    # Summarizer
    "OutputSummarizer",
    "OutputAnalysis",
    "OutputPatternType",
    "PatternMatch",
    # Formatters
    "BaseFormatter",
    "FormattedMessage",
    "MessengerFormatter",
    "create_formatter",
    # Exceptions
    "CCException",
    "SessionError",
    "SessionNotFoundError",
    "SessionAlreadyExistsError",
    "SessionLimitError",
    "WezTermError",
    "WezTermNotFoundError",
    "WezTermPaneError",
]
