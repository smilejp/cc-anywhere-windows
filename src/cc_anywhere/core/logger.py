"""Session History Logger for CC-Anywhere."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SessionLogger:
    """Logs session input/output to files for history tracking."""

    def __init__(self, log_dir: Optional[str] = None):
        """Initialize SessionLogger.

        Args:
            log_dir: Directory to store log files (default: ~/.cc-anywhere/logs)
        """
        if log_dir:
            self.log_dir = Path(log_dir).expanduser()
        else:
            self.log_dir = Path.home() / ".cc-anywhere" / "logs"

        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Session logs directory: {self.log_dir}")

    def _get_log_path(self, session_id: str) -> Path:
        """Get log file path for a session."""
        return self.log_dir / f"{session_id}.jsonl"

    def _write_entry(self, session_id: str, entry_type: str, content: str) -> None:
        """Write a log entry to the session log file.

        Args:
            session_id: Session ID
            entry_type: Type of entry (input, output, system)
            content: Content to log
        """
        if not content.strip():
            return

        log_path = self._get_log_path(session_id)

        entry = {
            "ts": datetime.now().isoformat(),
            "type": entry_type,
            "content": content,
        }

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write log entry: {e}")

    def log_input(self, session_id: str, content: str) -> None:
        """Log user input."""
        self._write_entry(session_id, "input", content)

    def log_output(self, session_id: str, content: str) -> None:
        """Log session output."""
        self._write_entry(session_id, "output", content)

    def log_system(self, session_id: str, message: str) -> None:
        """Log system message (session start, stop, etc.)."""
        self._write_entry(session_id, "system", message)

    def get_history(
        self,
        session_id: str,
        limit: Optional[int] = None,
        entry_type: Optional[str] = None,
    ) -> list[dict]:
        """Get session history.

        Args:
            session_id: Session ID
            limit: Maximum number of entries to return (from end)
            entry_type: Filter by type (input, output, system)

        Returns:
            List of log entries
        """
        log_path = self._get_log_path(session_id)

        if not log_path.exists():
            return []

        entries = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry_type is None or entry.get("type") == entry_type:
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to read log file: {e}")
            return []

        if limit:
            entries = entries[-limit:]

        return entries

    def get_all_sessions(self) -> list[dict]:
        """Get list of all sessions with logs.

        Returns:
            List of session info dicts with id, first_ts, last_ts, entry_count
        """
        sessions = []

        for log_file in self.log_dir.glob("*.jsonl"):
            session_id = log_file.stem
            entries = self.get_history(session_id)

            if entries:
                sessions.append({
                    "id": session_id,
                    "first_ts": entries[0].get("ts"),
                    "last_ts": entries[-1].get("ts"),
                    "entry_count": len(entries),
                })

        # Sort by last activity
        sessions.sort(key=lambda x: x.get("last_ts", ""), reverse=True)
        return sessions

    def delete_history(self, session_id: str) -> bool:
        """Delete session history.

        Args:
            session_id: Session ID

        Returns:
            True if deleted, False if not found
        """
        log_path = self._get_log_path(session_id)

        if log_path.exists():
            try:
                log_path.unlink()
                return True
            except Exception as e:
                logger.error(f"Failed to delete log file: {e}")
                return False
        return False

    def get_stats(self, session_id: str) -> dict:
        """Get statistics for a session.

        Args:
            session_id: Session ID

        Returns:
            Dict with input_count, output_count, total_chars, duration
        """
        entries = self.get_history(session_id)

        if not entries:
            return {
                "input_count": 0,
                "output_count": 0,
                "total_input_chars": 0,
                "total_output_chars": 0,
                "duration_seconds": 0,
            }

        input_entries = [e for e in entries if e.get("type") == "input"]
        output_entries = [e for e in entries if e.get("type") == "output"]

        first_ts = datetime.fromisoformat(entries[0]["ts"])
        last_ts = datetime.fromisoformat(entries[-1]["ts"])
        duration = (last_ts - first_ts).total_seconds()

        return {
            "input_count": len(input_entries),
            "output_count": len(output_entries),
            "total_input_chars": sum(len(e.get("content", "")) for e in input_entries),
            "total_output_chars": sum(len(e.get("content", "")) for e in output_entries),
            "duration_seconds": duration,
        }
