"""Custom exceptions for CC-Anywhere Windows."""


class CCException(Exception):
    """Base exception for CC-Anywhere."""

    pass


class SessionError(CCException):
    """Session-related errors."""

    pass


class SessionNotFoundError(SessionError):
    """Session not found."""

    pass


class SessionAlreadyExistsError(SessionError):
    """Session with this name already exists."""

    pass


class SessionLimitError(SessionError):
    """Maximum session limit reached."""

    pass


class WezTermError(CCException):
    """WezTerm-related errors."""

    pass


class WezTermNotFoundError(WezTermError):
    """WezTerm is not installed or not found."""

    pass


class WezTermPaneError(WezTermError):
    """Error with WezTerm pane operations."""

    pass
