"""Session Manager for Claude Code sessions using WezTerm (Windows)."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

from .exceptions import (
    SessionAlreadyExistsError,
    SessionLimitError,
    SessionNotFoundError,
    WezTermError,
    WezTermNotFoundError,
)
from .logger import SessionLogger
from .models import Session, SessionOutput, SessionStatus
from . import git_utils

logger = logging.getLogger(__name__)

# ANSI escape sequence pattern
ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[PX^_][^\x1b]*\x1b\\"
)

# Patterns that indicate Claude is waiting for input
INPUT_WAIT_PATTERNS = [
    re.compile(r"\[Y/n\]", re.IGNORECASE),
    re.compile(r"\[y/N\]", re.IGNORECASE),
    re.compile(r"Continue\?", re.IGNORECASE),
    re.compile(r"Proceed\?", re.IGNORECASE),
    re.compile(r"Are you sure\?", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
]


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


class SessionManager:
    """Manages Claude Code sessions using WezTerm."""

    WORKSPACE = "cc-anywhere"

    # Key mapping for special keys (tmux key names -> ANSI escape sequences)
    KEY_MAP = {
        "C-c": "\x03",
        "C-d": "\x04",
        "C-l": "\x0c",
        "C-z": "\x1a",
        "Enter": "\r",
        "Up": "\x1b[A",
        "Down": "\x1b[B",
        "Left": "\x1b[D",
        "Right": "\x1b[C",
        "Escape": "\x1b",
        "Tab": "\t",
        "Backspace": "\x7f",
        "Delete": "\x1b[3~",
        "Home": "\x1b[H",
        "End": "\x1b[F",
        "PageUp": "\x1b[5~",
        "PageDown": "\x1b[6~",
    }

    def __init__(
        self,
        claude_command: str = "claude",
        claude_args: Optional[list[str]] = None,
        default_working_dir: str = "~",
        max_sessions: int = 10,
        session_prefix: str = "cc-",
        log_dir: Optional[str] = None,
    ):
        """Initialize SessionManager.

        Args:
            claude_command: Path to claude CLI
            claude_args: Arguments for claude CLI
            default_working_dir: Default working directory for sessions
            max_sessions: Maximum number of concurrent sessions
            session_prefix: Prefix for session names (unused in WezTerm, kept for compatibility)
            log_dir: Directory for session logs
        """
        self.claude_command = claude_command
        self.claude_args = claude_args or ["--dangerously-skip-permissions"]
        self.default_working_dir = default_working_dir
        self.max_sessions = max_sessions
        self.session_prefix = session_prefix

        self._sessions: dict[str, Session] = {}
        self._output_cache: dict[str, str] = {}

        # Session history logger
        self.logger = SessionLogger(log_dir)

        # Find WezTerm executable
        self._wezterm_path = shutil.which("wezterm")
        if not self._wezterm_path:
            raise WezTermNotFoundError(
                "WezTerm not found. Please install WezTerm: winget install wez.wezterm"
            )

    async def _run_cli(self, *args: str) -> str:
        """Run wezterm cli command.

        Args:
            *args: Command arguments to pass to wezterm cli

        Returns:
            Command stdout as string

        Raises:
            WezTermError: If command fails
        """
        cmd = [self._wezterm_path, "cli", *args]
        logger.debug(f"Running WezTerm CLI: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() or f"Exit code {proc.returncode}"
            raise WezTermError(f"wezterm cli failed: {error_msg}")

        return stdout.decode()

    def _generate_id(self) -> str:
        """Generate unique session ID."""
        return str(uuid.uuid4())[:8]

    def _build_claude_command(self, working_dir: str) -> list[str]:
        """Build the claude command for Windows.

        After Claude Code exits, the shell remains active so the user
        can continue working or restart Claude manually.
        """
        args = " ".join(self.claude_args)
        expanded_dir = str(Path(working_dir).expanduser())
        # For Windows: use cmd /k to keep terminal open after command
        # cd /d handles drive letter changes
        # Quote the path to handle spaces
        return [
            "cmd", "/k",
            f'cd /d "{expanded_dir}" && {self.claude_command} {args}'
        ]

    async def create_session(
        self,
        name: str,
        working_dir: Optional[str] = None,
        create_worktree: bool = False,
        worktree_branch: Optional[str] = None,
        cleanup_worktree: bool = True,
    ) -> Session:
        """Create a new Claude Code session.

        Args:
            name: Human-readable session name
            working_dir: Working directory for the session
            create_worktree: If True, create a Git worktree for the session
            worktree_branch: Branch name for worktree (auto-generated if None)
            cleanup_worktree: If True, remove worktree when session is destroyed

        Returns:
            Created Session object

        Raises:
            SessionAlreadyExistsError: If session with same name exists
            SessionLimitError: If max sessions reached
            WezTermError: If WezTerm operation fails
        """
        # Check for duplicate name
        for session in self._sessions.values():
            if session.name == name:
                raise SessionAlreadyExistsError(f"Session '{name}' already exists")

        # Check session limit
        if len(self._sessions) >= self.max_sessions:
            raise SessionLimitError(f"Maximum sessions ({self.max_sessions}) reached")

        session_id = self._generate_id()
        work_dir = working_dir or self.default_working_dir

        # Worktree handling
        actual_work_dir = work_dir
        actual_worktree_path = None
        actual_worktree_branch = None

        if create_worktree:
            expanded_dir = str(Path(work_dir).expanduser())
            if git_utils.is_git_repo(expanded_dir):
                # Generate branch name if not provided
                if not worktree_branch:
                    worktree_branch = git_utils.generate_branch_name(name)

                # Create worktree
                success, result = git_utils.create_worktree(
                    expanded_dir,
                    worktree_branch,
                )

                if success:
                    actual_worktree_path = result
                    actual_worktree_branch = worktree_branch
                    actual_work_dir = result  # Use worktree as working dir
                    logger.info(f"Created worktree at {result} for branch {worktree_branch}")
                else:
                    logger.warning(f"Failed to create worktree: {result}")
                    # Continue without worktree
            else:
                logger.warning(f"Cannot create worktree: {work_dir} is not a Git repository")

        try:
            # Build command to run
            cmd_parts = self._build_claude_command(actual_work_dir)
            expanded_work_dir = str(Path(actual_work_dir).expanduser())

            # Spawn new pane in our workspace
            # wezterm cli spawn returns the pane_id
            result = await self._run_cli(
                "spawn",
                "--new-window",
                "--workspace", self.WORKSPACE,
                "--cwd", expanded_work_dir,
                "--",
                *cmd_parts,
            )
            pane_id = int(result.strip())

            session = Session(
                id=session_id,
                name=name,
                working_dir=actual_work_dir,
                status=SessionStatus.STARTING,
                wezterm_pane_id=pane_id,
                worktree_path=actual_worktree_path,
                worktree_branch=actual_worktree_branch,
                cleanup_worktree=cleanup_worktree,
            )

            self._sessions[session_id] = session
            self._output_cache[session_id] = ""

            logger.info(f"Created session: {name} ({session_id}) with pane_id={pane_id}")

            # Log session creation
            log_msg = f"Session created: {name} (working_dir: {actual_work_dir})"
            if actual_worktree_branch:
                log_msg += f" (worktree: {actual_worktree_branch})"
            self.logger.log_system(session_id, log_msg)

            # Wait a bit for claude to start
            await asyncio.sleep(1)
            session.status = SessionStatus.ACTIVE

            return session

        except Exception as e:
            # Cleanup worktree on failure
            if actual_worktree_path:
                git_utils.remove_worktree(actual_worktree_path, force=True)
            logger.error(f"Failed to create session: {e}")
            raise WezTermError(f"Failed to create WezTerm pane: {e}") from e

    def get_session(self, session_id: str) -> Session:
        """Get session by ID.

        Args:
            session_id: Session ID

        Returns:
            Session object

        Raises:
            SessionNotFoundError: If session not found
        """
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"Session '{session_id}' not found")
        return self._sessions[session_id]

    def get_session_by_name(self, name: str) -> Optional[Session]:
        """Get session by name.

        Args:
            name: Session name

        Returns:
            Session object or None if not found
        """
        for session in self._sessions.values():
            if session.name == name:
                return session
        return None

    def list_sessions(self) -> list[Session]:
        """List all sessions.

        Returns:
            List of Session objects
        """
        return list(self._sessions.values())

    async def destroy_session(
        self,
        session_id: str,
        cleanup_worktree: Optional[bool] = None,
    ) -> None:
        """Destroy a session.

        Args:
            session_id: Session ID to destroy
            cleanup_worktree: Override session's cleanup_worktree setting

        Raises:
            SessionNotFoundError: If session not found
            WezTermError: If WezTerm operation fails
        """
        session = self.get_session(session_id)

        try:
            # Kill the WezTerm pane
            await self._run_cli(
                "kill-pane",
                "--pane-id", str(session.wezterm_pane_id),
            )

            # Cleanup worktree if requested
            should_cleanup = cleanup_worktree if cleanup_worktree is not None else session.cleanup_worktree
            if should_cleanup and session.worktree_path:
                # Get git root BEFORE removing worktree (needed for branch deletion)
                git_root = git_utils.get_git_root(session.worktree_path)

                success, msg = git_utils.remove_worktree(session.worktree_path, force=True)
                if success:
                    logger.info(f"Cleaned up worktree at {session.worktree_path}")
                    # Also delete the branch
                    if session.worktree_branch and git_root:
                        git_utils.delete_branch(git_root, session.worktree_branch, force=True)
                        logger.info(f"Deleted branch {session.worktree_branch}")
                else:
                    logger.warning(f"Failed to cleanup worktree: {msg}")

            # Log session destruction before removing
            self.logger.log_system(session_id, f"Session destroyed: {session.name}")

            del self._sessions[session_id]
            if session_id in self._output_cache:
                del self._output_cache[session_id]

            logger.info(f"Destroyed session: {session.name} ({session_id})")

        except Exception as e:
            logger.error(f"Failed to destroy session: {e}")
            raise WezTermError(f"Failed to destroy WezTerm pane: {e}") from e

    async def send_input(self, session_id: str, text: str) -> None:
        """Send text input to a session.

        Args:
            session_id: Session ID
            text: Text to send

        Raises:
            SessionNotFoundError: If session not found
            WezTermError: If WezTerm operation fails
        """
        session = self.get_session(session_id)

        try:
            # Use --no-paste to send text directly with newline
            await self._run_cli(
                "send-text",
                "--pane-id", str(session.wezterm_pane_id),
                "--no-paste",
                text + "\r",  # Add carriage return as Enter
            )
            session.update_activity()

            # Log input to history
            self.logger.log_input(session_id, text)

        except Exception as e:
            logger.error(f"Failed to send input: {e}")
            raise WezTermError(f"Failed to send input: {e}") from e

    async def send_key(self, session_id: str, key: str) -> None:
        """Send special key to a session.

        Args:
            session_id: Session ID
            key: Key to send (e.g., 'C-c' for Ctrl+C, 'Enter', 'Up', 'Down')

        Raises:
            SessionNotFoundError: If session not found
            WezTermError: If WezTerm operation fails
        """
        session = self.get_session(session_id)

        try:
            # Map key name to ANSI escape sequence
            key_seq = self.KEY_MAP.get(key, key)

            await self._run_cli(
                "send-text",
                "--pane-id", str(session.wezterm_pane_id),
                "--no-paste",
                key_seq,
            )
            session.update_activity()

        except Exception as e:
            logger.error(f"Failed to send key: {e}")
            raise WezTermError(f"Failed to send key: {e}") from e

    async def cancel_command(self, session_id: str) -> None:
        """Cancel currently running command (send Ctrl+C).

        Args:
            session_id: Session ID
        """
        await self.send_key(session_id, "C-c")
        logger.info(f"Cancelled command in session {session_id}")

    def clear_output_cache(self, session_id: str) -> None:
        """Clear output cache for a session.

        Args:
            session_id: Session ID
        """
        self._output_cache[session_id] = ""

    async def resize_pane(self, session_id: str, cols: int, rows: int) -> None:
        """Resize WezTerm pane to match terminal size.

        Note: WezTerm pane resizing is limited compared to tmux.
        This attempts to resize but may not have full effect.

        Args:
            session_id: Session ID
            cols: Number of columns
            rows: Number of rows
        """
        # WezTerm doesn't have direct pane resize via CLI like tmux
        # The GUI handles resizing. Log a warning for now.
        logger.debug(f"Resize request for session {session_id}: {cols}x{rows} (WezTerm limitations apply)")

    async def read_output(
        self,
        session_id: str,
        lines: int = 100,
    ) -> SessionOutput:
        """Read output from a session.

        Args:
            session_id: Session ID
            lines: Number of lines to capture

        Returns:
            SessionOutput object

        Raises:
            SessionNotFoundError: If session not found
            WezTermError: If WezTerm operation fails
        """
        session = self.get_session(session_id)

        try:
            # Get text from pane with escape sequences preserved
            content = await self._run_cli(
                "get-text",
                "--pane-id", str(session.wezterm_pane_id),
                "--escapes",  # Preserve ANSI escape sequences
                "--start-line", f"-{lines}",  # Negative = lines from bottom
            )

            # Check if waiting for input
            is_waiting = self._check_waiting_input(content)

            if is_waiting:
                session.status = SessionStatus.WAITING_INPUT
            elif session.status == SessionStatus.WAITING_INPUT:
                session.status = SessionStatus.ACTIVE

            return SessionOutput(
                session_id=session_id,
                content=content,
                is_waiting_input=is_waiting,
            )

        except Exception as e:
            logger.error(f"Failed to read output: {e}")
            raise WezTermError(f"Failed to read output: {e}") from e

    def _check_waiting_input(self, content: str) -> bool:
        """Check if the output indicates waiting for user input."""
        # Check last few lines for input prompts
        last_lines = content.split("\n")[-5:]
        last_content = "\n".join(last_lines)

        for pattern in INPUT_WAIT_PATTERNS:
            if pattern.search(last_content):
                return True
        return False

    async def get_new_output(
        self, session_id: str, lines: int = 200, strip_ansi: bool = False
    ) -> str:
        """Get only new output since last read.

        Args:
            session_id: Session ID
            lines: Number of lines to capture
            strip_ansi: If True, strip ANSI codes from output (for Discord etc.)

        Returns:
            New output string (empty if no change)
        """
        output = await self.read_output(session_id, lines)
        full_content = output.content

        # Normalize line endings
        full_content = full_content.replace("\r\n", "\n").replace("\r", "\n")

        # Strip ANSI for comparison only (more reliable hash)
        clean_content = _strip_ansi(full_content)
        content_hash = hashlib.md5(clean_content.encode()).hexdigest()

        # Get cached hash
        cached_hash = self._output_cache.get(session_id)

        # If same hash, no new content
        if cached_hash == content_hash:
            return ""

        # Update cache with new hash
        self._output_cache[session_id] = content_hash

        # Return last portion of content (to avoid duplicates)
        if strip_ansi:
            lines_list = clean_content.strip().split("\n")
        else:
            lines_list = full_content.strip().split("\n")

        # Take last 50 lines as "new" content
        result = "\n".join(lines_list[-50:]) if lines_list else ""
        return result

    async def stream_output(
        self,
        session_id: str,
        interval: float = 0.5,
        idle_timeout: float = 300,
        strip_ansi: bool = False,
    ) -> AsyncIterator[str]:
        """Stream output from a session.

        Args:
            session_id: Session ID
            interval: Poll interval in seconds
            idle_timeout: Stop streaming after this many seconds without new output
            strip_ansi: If True, strip ANSI codes from output

        Yields:
            New output strings
        """
        # Clear cache to start fresh
        self._output_cache.pop(session_id, None)

        last_output_time = asyncio.get_event_loop().time()

        while True:
            try:
                new_output = await self.get_new_output(session_id, strip_ansi=strip_ansi)
                if new_output.strip():
                    yield new_output
                    last_output_time = asyncio.get_event_loop().time()
                else:
                    # Check idle timeout
                    now = asyncio.get_event_loop().time()
                    if now - last_output_time > idle_timeout:
                        break
            except SessionNotFoundError:
                break

            await asyncio.sleep(interval)

    def get_session_status(self, session_id: str) -> SessionStatus:
        """Get session status.

        Args:
            session_id: Session ID

        Returns:
            SessionStatus enum value
        """
        session = self.get_session(session_id)
        return session.status

    async def check_session_alive(self, session_id: str) -> bool:
        """Check if session's WezTerm pane is still alive.

        Args:
            session_id: Session ID

        Returns:
            True if alive, False otherwise
        """
        session = self.get_session(session_id)

        try:
            result = await self._run_cli("list", "--format", "json")
            panes = json.loads(result)

            for pane in panes:
                if pane.get("pane_id") == session.wezterm_pane_id:
                    return True
            return False
        except Exception:
            return False

    async def restart_session(self, session_id: str) -> Session:
        """Restart a session (destroy and recreate).

        Args:
            session_id: Session ID

        Returns:
            New Session object
        """
        session = self.get_session(session_id)
        name = session.name
        working_dir = session.working_dir

        await self.destroy_session(session_id)
        return await self.create_session(name, working_dir)

    async def cleanup_idle_sessions(self, idle_minutes: int = 60) -> list[str]:
        """Clean up sessions that have been idle too long.

        Args:
            idle_minutes: Minutes of inactivity before cleanup

        Returns:
            List of destroyed session IDs
        """
        now = datetime.now()
        destroyed = []

        for session_id, session in list(self._sessions.items()):
            idle_time = (now - session.last_activity).total_seconds() / 60
            if idle_time > idle_minutes:
                try:
                    await self.destroy_session(session_id)
                    destroyed.append(session_id)
                    logger.info(f"Cleaned up idle session: {session.name}")
                except Exception as e:
                    logger.error(f"Failed to cleanup session {session_id}: {e}")

        return destroyed

    async def shutdown(self) -> None:
        """Shutdown SessionManager (keeps WezTerm panes alive)."""
        logger.info("Shutting down SessionManager (WezTerm panes preserved)...")

        # Just clear internal state, don't destroy WezTerm panes
        self._sessions.clear()
        logger.info("SessionManager shutdown complete")

    async def destroy_all_sessions(self) -> int:
        """Destroy all managed sessions.

        Returns:
            Number of destroyed sessions
        """
        count = 0
        for session_id in list(self._sessions.keys()):
            try:
                await self.destroy_session(session_id)
                count += 1
            except Exception as e:
                logger.error(f"Error destroying session {session_id}: {e}")

        logger.info(f"Destroyed {count} sessions")
        return count

    async def discover_wezterm_panes(self) -> list[dict]:
        """Discover all WezTerm panes in our workspace.

        Returns:
            List of pane info dicts from WezTerm
        """
        try:
            result = await self._run_cli("list", "--format", "json")
            panes = json.loads(result)

            # Filter to our workspace
            return [p for p in panes if p.get("workspace") == self.WORKSPACE]

        except Exception as e:
            logger.error(f"Failed to discover WezTerm panes: {e}")
            return []

    async def import_session(
        self,
        pane_id: int,
        name: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Session:
        """Import an existing WezTerm pane into cc-anywhere.

        Args:
            pane_id: WezTerm pane ID to import
            name: Human-readable name (defaults to pane title)
            working_dir: Working directory (will try to detect if not provided)

        Returns:
            Imported Session object

        Raises:
            SessionAlreadyExistsError: If pane already managed
            WezTermError: If pane not found
        """
        # Check if already managed
        for session in self._sessions.values():
            if session.wezterm_pane_id == pane_id:
                raise SessionAlreadyExistsError(
                    f"Pane {pane_id} is already managed as '{session.name}'"
                )

        # Verify pane exists
        try:
            result = await self._run_cli("list", "--format", "json")
            panes = json.loads(result)

            pane_info = None
            for pane in panes:
                if pane.get("pane_id") == pane_id:
                    pane_info = pane
                    break

            if not pane_info:
                raise WezTermError(f"WezTerm pane {pane_id} not found")

            # Use pane info for defaults
            if working_dir is None:
                working_dir = pane_info.get("cwd", "~")

            # Generate ID and create session object
            session_id = self._generate_id()
            session_name = name or pane_info.get("title", f"pane-{pane_id}")

            # Check for duplicate name
            for s in self._sessions.values():
                if s.name == session_name:
                    session_name = f"{session_name}-{session_id[:4]}"
                    break

            session = Session(
                id=session_id,
                name=session_name,
                working_dir=working_dir,
                status=SessionStatus.ACTIVE,
                wezterm_pane_id=pane_id,
            )

            self._sessions[session_id] = session
            self._output_cache[session_id] = ""

            logger.info(f"Imported session: {session_name} ({session_id}) from pane_id={pane_id}")

            return session

        except SessionAlreadyExistsError:
            raise
        except WezTermError:
            raise
        except Exception as e:
            logger.error(f"Failed to import session: {e}")
            raise WezTermError(f"Failed to import WezTerm pane: {e}") from e

    async def import_all_sessions(self) -> list[Session]:
        """Import all unmanaged WezTerm panes in our workspace.

        Returns:
            List of imported Session objects
        """
        imported = []
        panes = await self.discover_wezterm_panes()

        # Get currently managed pane IDs
        managed_pane_ids = {s.wezterm_pane_id for s in self._sessions.values()}

        for pane in panes:
            pane_id = pane.get("pane_id")
            if pane_id and pane_id not in managed_pane_ids:
                try:
                    session = await self.import_session(pane_id)
                    imported.append(session)
                except Exception as e:
                    logger.warning(f"Failed to import pane {pane_id}: {e}")

        return imported
