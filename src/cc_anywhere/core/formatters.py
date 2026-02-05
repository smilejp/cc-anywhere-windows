"""Message formatters for messenger adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .summarizer import OutputAnalysis, OutputSummarizer


@dataclass
class FormattedMessage:
    """A formatted message for messenger platforms."""

    text: str
    is_progress: bool = False
    is_completion: bool = False
    is_error: bool = False
    is_acknowledgement: bool = False


class BaseFormatter(ABC):
    """Base class for message formatters."""

    @abstractmethod
    def format_acknowledgement(self, command: str) -> FormattedMessage:
        """Format acknowledgement message when command is received."""
        pass

    @abstractmethod
    def format_progress(
        self, analysis: OutputAnalysis, update_count: int = 0
    ) -> FormattedMessage:
        """Format progress message during execution."""
        pass

    @abstractmethod
    def format_completion(
        self, analysis: OutputAnalysis, elapsed_seconds: Optional[float] = None
    ) -> FormattedMessage:
        """Format completion message."""
        pass

    @abstractmethod
    def format_error(
        self, analysis: OutputAnalysis, error_message: Optional[str] = None
    ) -> FormattedMessage:
        """Format error message."""
        pass


class MessengerFormatter(BaseFormatter):
    """Formatter for Discord, Telegram, and Slack."""

    def __init__(self, platform: str = "generic"):
        """Initialize formatter.

        Args:
            platform: Platform name (discord, telegram, slack, generic)
        """
        self.platform = platform.lower()
        self.summarizer = OutputSummarizer()

    def format_acknowledgement(self, command: str) -> FormattedMessage:
        """Format acknowledgement message when command is received.

        Args:
            command: The command/message received

        Returns:
            Formatted acknowledgement message
        """
        # Truncate command if too long
        display_command = command[:50] + "..." if len(command) > 50 else command

        text = (
            f"Command received\n"
            f"{'=' * 12}\n"
            f"Task: {display_command}\n"
            f"Starting processing..."
        )

        return FormattedMessage(text=text, is_acknowledgement=True)

    def format_progress(
        self, analysis: OutputAnalysis, update_count: int = 0
    ) -> FormattedMessage:
        """Format progress message during execution.

        Args:
            analysis: Output analysis result
            update_count: Number of updates so far

        Returns:
            Formatted progress message (concise)
        """
        # Build progress bar
        progress_bar = self._build_progress_bar(update_count)

        # Build status lines (concise)
        status_lines = []

        # File status
        file_count = (
            len(analysis.files_created)
            + len(analysis.files_modified)
            + len(analysis.files_deleted)
        )
        if file_count > 0:
            parts = []
            if analysis.files_created:
                parts.append(f"{len(analysis.files_created)} created")
            if analysis.files_modified:
                parts.append(f"{len(analysis.files_modified)} modified")
            if analysis.files_deleted:
                parts.append(f"{len(analysis.files_deleted)} deleted")
            status_lines.append(f"Files: {', '.join(parts)}")

        # Command status
        if analysis.commands_executed:
            cmd_count = len(analysis.commands_executed)
            status_lines.append(f"Commands: {cmd_count} running")

        # Test status
        if analysis.tests_passed or analysis.tests_failed:
            test_parts = []
            if analysis.tests_passed:
                test_parts.append(f"{analysis.tests_passed} passed")
            if analysis.tests_failed:
                test_parts.append(f"{analysis.tests_failed} failed")
            status_lines.append(f"Tests: {', '.join(test_parts)}")

        # Git status
        if analysis.git_commits:
            status_lines.append(f"Git: {len(analysis.git_commits)} commits")

        # Build message
        text = f"In progress... {progress_bar}"
        if status_lines:
            text += "\n" + "\n".join(status_lines)

        return FormattedMessage(text=text, is_progress=True)

    def format_completion(
        self, analysis: OutputAnalysis, elapsed_seconds: Optional[float] = None,
        raw_output: Optional[str] = None
    ) -> FormattedMessage:
        """Format completion message.

        Args:
            analysis: Output analysis result
            elapsed_seconds: Time elapsed in seconds
            raw_output: Raw output text for fallback summary

        Returns:
            Formatted completion message (detailed)
        """
        lines = [
            "Task completed",
            "=" * 12,
        ]

        has_content = False

        # File changes
        if analysis.files_created or analysis.files_modified or analysis.files_deleted:
            has_content = True
            lines.append("File changes:")
            for f in analysis.files_created[:5]:
                lines.append(f"  + {self._truncate_path(f)} (created)")
            for f in analysis.files_modified[:5]:
                lines.append(f"  ~ {self._truncate_path(f)} (modified)")
            for f in analysis.files_deleted[:5]:
                lines.append(f"  - {self._truncate_path(f)} (deleted)")

            # Show count if more files
            total = (
                len(analysis.files_created)
                + len(analysis.files_modified)
                + len(analysis.files_deleted)
            )
            if total > 15:
                lines.append(f"  ... and {total - 15} more files")
            lines.append("")

        # Commands executed
        if analysis.commands_executed:
            has_content = True
            lines.append("Commands executed:")
            for cmd in analysis.commands_executed[:5]:
                # Truncate long commands
                display_cmd = cmd[:40] + "..." if len(cmd) > 40 else cmd
                lines.append(f"  {display_cmd}")
            if len(analysis.commands_executed) > 5:
                lines.append(f"  ... and {len(analysis.commands_executed) - 5} more")
            lines.append("")

        # Test results
        if analysis.tests_passed or analysis.tests_failed:
            has_content = True
            lines.append("Test results:")
            if analysis.tests_passed:
                lines.append(f"  {analysis.tests_passed} passed")
            if analysis.tests_failed:
                lines.append(f"  {analysis.tests_failed} failed")
            lines.append("")

        # Git operations
        if analysis.git_commits or analysis.git_pushes:
            has_content = True
            lines.append("Git operations:")
            for commit in analysis.git_commits[:3]:
                lines.append(f"  Commit: {commit[:7]}")
            for push in analysis.git_pushes[:2]:
                lines.append(f"  Push: {push}")
            lines.append("")

        # Warnings (if any)
        if analysis.warnings:
            has_content = True
            lines.append("Warnings:")
            for warn in analysis.warnings[:3]:
                lines.append(f"  {warn[:50]}")
            lines.append("")

        # If no structured content detected, provide a summary from raw output
        if not has_content and raw_output:
            summary = self._extract_summary_from_output(raw_output)
            if summary:
                lines.append("Task summary:")
                for line in summary[:5]:
                    lines.append(f"  {line}")
                lines.append("")

        # Elapsed time
        if elapsed_seconds is not None:
            if elapsed_seconds < 60:
                time_str = f"~{int(elapsed_seconds)}s"
            else:
                minutes = int(elapsed_seconds // 60)
                seconds = int(elapsed_seconds % 60)
                time_str = f"~{minutes}m {seconds}s"
            lines.append(f"Duration: {time_str}")

        text = "\n".join(lines)
        return FormattedMessage(text=text, is_completion=True)

    def _extract_summary_from_output(self, output: str) -> list[str]:
        """Extract meaningful summary lines from raw output.

        Args:
            output: Raw output text

        Returns:
            List of summary lines
        """
        if not output:
            return []

        summary_lines = []
        lines = output.strip().split("\n")

        # Keywords that indicate important information
        important_keywords = [
            "done", "success", "created", "modified", "updated",
            "installed", "passed", "failed", "error", "warning",
            "commit", "push", "pull", "build", "test", "deploy",
        ]

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip very short or very long lines
            if len(line) < 5 or len(line) > 100:
                continue

            # Skip lines that look like prompts or decorations
            if line.startswith(("$", ">", "#", "-", "=", "|")):
                continue

            # Check if line contains important keywords
            line_lower = line.lower()
            if any(kw in line_lower for kw in important_keywords):
                # Truncate if too long
                if len(line) > 60:
                    line = line[:57] + "..."
                summary_lines.append(line)

            if len(summary_lines) >= 5:
                break

        return summary_lines

    def format_error(
        self, analysis: OutputAnalysis, error_message: Optional[str] = None
    ) -> FormattedMessage:
        """Format error message.

        Args:
            analysis: Output analysis result
            error_message: Optional specific error message

        Returns:
            Formatted error message (detailed)
        """
        lines = [
            "Task failed",
            "=" * 12,
        ]

        # Error details
        if error_message:
            lines.append(f"Error: {error_message[:200]}")
        elif analysis.errors:
            lines.append("Errors:")
            for err in analysis.errors[:3]:
                lines.append(f"  {err[:100]}")
        lines.append("")

        # Affected files
        affected = analysis.files_created + analysis.files_modified
        if affected:
            lines.append("Affected files:")
            for f in affected[:5]:
                lines.append(f"  ~ {self._truncate_path(f)}")
            lines.append("")

        # Suggestion based on error type
        suggestion = self._get_error_suggestion(analysis, error_message)
        if suggestion:
            lines.append(f"Suggestion: {suggestion}")

        text = "\n".join(lines)
        return FormattedMessage(text=text, is_error=True)

    def format_raw_output(self, output: str, max_lines: int = 15) -> str:
        """Format raw output for display.

        Args:
            output: Raw output text
            max_lines: Maximum lines to show

        Returns:
            Formatted output string
        """
        if not output.strip():
            return ""

        lines = output.strip().split("\n")
        if len(lines) > max_lines:
            lines = lines[-max_lines:]

        return "\n".join(lines)

    def _build_progress_bar(self, count: int, width: int = 10) -> str:
        """Build a simple progress indicator.

        Args:
            count: Update count
            width: Bar width

        Returns:
            Progress bar string
        """
        # Cycle through positions
        position = count % width
        filled = "=" * position
        pointer = ">"
        empty = " " * (width - position - 1)
        return f"[{filled}{pointer}{empty}]"

    def _truncate_path(self, path: str, max_length: int = 40) -> str:
        """Truncate file path for display.

        Args:
            path: File path
            max_length: Maximum length

        Returns:
            Truncated path
        """
        if len(path) <= max_length:
            return path

        # Keep filename and truncate directory
        parts = path.split("/")
        filename = parts[-1]

        if len(filename) >= max_length - 3:
            return "..." + filename[-(max_length - 3) :]

        remaining = max_length - len(filename) - 4  # for ".../"
        if remaining > 0 and len(parts) > 1:
            prefix = "/".join(parts[:-1])
            if len(prefix) > remaining:
                prefix = "..." + prefix[-(remaining) :]
            return f"{prefix}/{filename}"

        return "..." + path[-(max_length - 3) :]

    def _get_error_suggestion(
        self, analysis: OutputAnalysis, error_message: Optional[str] = None
    ) -> Optional[str]:
        """Get suggestion for error resolution.

        Args:
            analysis: Output analysis
            error_message: Error message

        Returns:
            Suggestion string or None
        """
        error_text = error_message or ""
        if analysis.errors:
            error_text += " " + " ".join(analysis.errors)

        error_text = error_text.lower()

        # Common error patterns and suggestions
        if "modulenotfounderror" in error_text or "no module named" in error_text:
            return "Install the missing package"
        elif "importerror" in error_text:
            return "Check the import path"
        elif "syntaxerror" in error_text:
            return "Fix the syntax error"
        elif "permission" in error_text:
            return "Check file permissions"
        elif "filenotfound" in error_text or "no such file" in error_text:
            return "Check the file path"
        elif "connection" in error_text or "timeout" in error_text:
            return "Check network connection"
        elif "memory" in error_text:
            return "Check memory usage"

        return None


def create_formatter(platform: str = "generic") -> MessengerFormatter:
    """Create a formatter for the specified platform.

    Args:
        platform: Platform name (discord, telegram, slack, generic)

    Returns:
        MessengerFormatter instance
    """
    return MessengerFormatter(platform)
