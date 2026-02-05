"""Output summarizer for Claude Code output analysis."""

import re
from dataclasses import dataclass, field
from enum import Enum


class OutputPatternType(Enum):
    """Types of output patterns detected."""

    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"
    FILE_DELETED = "file_deleted"
    FILE_READ = "file_read"
    COMMAND_EXECUTED = "command_executed"
    COMMAND_OUTPUT = "command_output"
    ERROR = "error"
    WARNING = "warning"
    TEST_PASSED = "test_passed"
    TEST_FAILED = "test_failed"
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    GIT_BRANCH = "git_branch"
    THINKING = "thinking"
    COMPLETION = "completion"
    UNKNOWN = "unknown"


@dataclass
class PatternMatch:
    """A matched pattern in the output."""

    pattern_type: OutputPatternType
    content: str
    details: dict = field(default_factory=dict)


@dataclass
class OutputAnalysis:
    """Result of analyzing Claude Code output."""

    # File operations
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)

    # Commands
    commands_executed: list[str] = field(default_factory=list)
    command_outputs: list[str] = field(default_factory=list)

    # Test results
    tests_passed: int = 0
    tests_failed: int = 0
    test_details: list[str] = field(default_factory=list)

    # Git operations
    git_commits: list[str] = field(default_factory=list)
    git_pushes: list[str] = field(default_factory=list)
    git_branches: list[str] = field(default_factory=list)

    # Errors and warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Status
    is_thinking: bool = False
    is_completed: bool = False
    has_error: bool = False

    # Raw patterns
    patterns: list[PatternMatch] = field(default_factory=list)

    def has_changes(self) -> bool:
        """Check if there are any meaningful changes."""
        return bool(
            self.files_created
            or self.files_modified
            or self.files_deleted
            or self.commands_executed
            or self.git_commits
            or self.errors
            or self.tests_passed
            or self.tests_failed
        )


class OutputSummarizer:
    """Summarizes Claude Code output by detecting patterns."""

    # File path pattern - matches paths with extensions or directory separators
    FILE_PATH_PATTERN = r"[\w./\-]+\.[\w]+|[\w]+/[\w./\-]+"

    # Pattern definitions
    PATTERNS = {
        # File operations - Claude Code uses these markers
        # Only match if followed by something that looks like a file path
        OutputPatternType.FILE_CREATED: [
            r"Created\s+([\w./\-]+\.[\w]+)",
            r"Created\s+([\w]+/[\w./\-]+)",
            r"Write\s+([\w./\-]+\.[\w]+)",
            r"Wrote\s+([\w./\-]+\.[\w]+)",
            r"Creating\s+([\w./\-]+\.[\w]+)",
            r"\+\s+([\w./\-]+\.[\w]+)(?:\s+\(created\))",
        ],
        OutputPatternType.FILE_MODIFIED: [
            r"Modified\s+([\w./\-]+\.[\w]+)",
            r"Modified\s+([\w]+/[\w./\-]+)",
            r"Updated\s+([\w./\-]+\.[\w]+)",
            r"Edit\s+([\w./\-]+\.[\w]+)",
            r"Edited\s+([\w./\-]+\.[\w]+)",
            r"~\s+([\w./\-]+\.[\w]+)(?:\s+\(modified\))",
        ],
        OutputPatternType.FILE_DELETED: [
            r"Deleted\s+([\w./\-]+\.[\w]+)",
            r"Removed\s+([\w./\-]+\.[\w]+)",
            r"-\s+([\w./\-]+\.[\w]+)(?:\s+\(deleted\))",
        ],
        OutputPatternType.FILE_READ: [
            r"Read\s+([\w./\-]+\.[\w]+)",
            r"Reading\s+([\w./\-]+\.[\w]+)",
        ],
        # Command execution
        OutputPatternType.COMMAND_EXECUTED: [
            r"^\$\s+(.+?)$",
            r"Running:\s+(.+?)$",
            r"Executing:\s+(.+?)$",
            r"Bash\s*\((.+?)\)",
        ],
        # Errors
        OutputPatternType.ERROR: [
            r"Error:\s*(.+?)$",
            r"ERROR:\s*(.+?)$",
            r"Exception:\s*(.+?)$",
            r"Traceback\s*\(most recent call last\)",
            r"ModuleNotFoundError:\s*(.+?)$",
            r"ImportError:\s*(.+?)$",
            r"SyntaxError:\s*(.+?)$",
            r"TypeError:\s*(.+?)$",
            r"ValueError:\s*(.+?)$",
            r"KeyError:\s*(.+?)$",
            r"AttributeError:\s*(.+?)$",
            r"NameError:\s*(.+?)$",
            r"FileNotFoundError:\s*(.+?)$",
            r"PermissionError:\s*(.+?)$",
        ],
        OutputPatternType.WARNING: [
            r"Warning:\s*(.+?)$",
            r"WARNING:\s*(.+?)$",
            r"WARN:\s*(.+?)$",
        ],
        # Test results - prioritize count patterns over single PASSED/FAILED
        OutputPatternType.TEST_PASSED: [
            r"(\d+)\s+passed",
            r"OK\s*\((\d+)\s+test",
            r"(\d+)\s+tests?\s+passed",
        ],
        OutputPatternType.TEST_FAILED: [
            r"(\d+)\s+failed",
            r"FAIL:\s*(.+?)$",
            r"(\d+)\s+tests?\s+failed",
        ],
        # Git operations
        OutputPatternType.GIT_COMMIT: [
            r"commit\s+([a-f0-9]{7,40})",
            r"\[.+?\s+([a-f0-9]{7,40})\]",
            r"Committed:\s*(.+?)$",
        ],
        OutputPatternType.GIT_PUSH: [
            r"push(?:ed)?\s+(?:to\s+)?(\S+)",
            r"Pushed\s+to\s+(.+?)$",
        ],
        OutputPatternType.GIT_BRANCH: [
            r"branch\s+(.+?)(?:\s|$)",
            r"Switched\s+to\s+branch\s+'(.+?)'",
            r"checked\s+out\s+(.+?)$",
        ],
        # Thinking/Processing
        OutputPatternType.THINKING: [
            r"Thinking\.{3}",
            r"Processing\.{3}",
            r"Analyzing\.{3}",
        ],
        # Completion
        OutputPatternType.COMPLETION: [
            r"Done\.?$",
            r"Completed\.?$",
            r"Finished\.?$",
            r"Success\.?$",
        ],
    }

    def __init__(self):
        """Initialize the summarizer."""
        # Compile patterns for efficiency
        self._compiled_patterns: dict[OutputPatternType, list[re.Pattern]] = {}
        for pattern_type, patterns in self.PATTERNS.items():
            self._compiled_patterns[pattern_type] = [
                re.compile(p, re.MULTILINE | re.IGNORECASE) for p in patterns
            ]

    def analyze(self, output: str) -> OutputAnalysis:
        """Analyze Claude Code output and extract patterns.

        Args:
            output: Raw output from Claude Code

        Returns:
            OutputAnalysis with detected patterns and summary
        """
        analysis = OutputAnalysis()

        if not output:
            return analysis

        # Process each line
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue

            self._analyze_line(line, analysis)

        # Determine overall status
        if analysis.errors:
            analysis.has_error = True

        # Check for completion markers in last few lines
        last_lines = output.strip().split("\n")[-5:]
        for line in last_lines:
            for pattern in self._compiled_patterns.get(OutputPatternType.COMPLETION, []):
                if pattern.search(line):
                    analysis.is_completed = True
                    break

        # Check for thinking markers
        for pattern in self._compiled_patterns.get(OutputPatternType.THINKING, []):
            if pattern.search(output):
                analysis.is_thinking = True
                break

        return analysis

    def _analyze_line(self, line: str, analysis: OutputAnalysis) -> None:
        """Analyze a single line and update analysis."""
        # File operations
        for pattern_type in [
            OutputPatternType.FILE_CREATED,
            OutputPatternType.FILE_MODIFIED,
            OutputPatternType.FILE_DELETED,
            OutputPatternType.FILE_READ,
        ]:
            for pattern in self._compiled_patterns.get(pattern_type, []):
                match = pattern.search(line)
                if match:
                    file_path = match.group(1) if match.groups() else line
                    file_path = self._clean_file_path(file_path)

                    if pattern_type == OutputPatternType.FILE_CREATED:
                        if file_path not in analysis.files_created:
                            analysis.files_created.append(file_path)
                    elif pattern_type == OutputPatternType.FILE_MODIFIED:
                        if file_path not in analysis.files_modified:
                            analysis.files_modified.append(file_path)
                    elif pattern_type == OutputPatternType.FILE_DELETED:
                        if file_path not in analysis.files_deleted:
                            analysis.files_deleted.append(file_path)
                    elif pattern_type == OutputPatternType.FILE_READ:
                        if file_path not in analysis.files_read:
                            analysis.files_read.append(file_path)

                    analysis.patterns.append(
                        PatternMatch(pattern_type, line, {"file": file_path})
                    )
                    break

        # Commands
        for pattern in self._compiled_patterns.get(OutputPatternType.COMMAND_EXECUTED, []):
            match = pattern.search(line)
            if match:
                command = match.group(1) if match.groups() else line
                if command not in analysis.commands_executed:
                    analysis.commands_executed.append(command)
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.COMMAND_EXECUTED, line, {"command": command})
                )
                break

        # Errors
        for pattern in self._compiled_patterns.get(OutputPatternType.ERROR, []):
            match = pattern.search(line)
            if match:
                error_msg = match.group(1) if match.groups() else line
                if error_msg not in analysis.errors:
                    analysis.errors.append(error_msg)
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.ERROR, line, {"error": error_msg})
                )
                break

        # Warnings
        for pattern in self._compiled_patterns.get(OutputPatternType.WARNING, []):
            match = pattern.search(line)
            if match:
                warning_msg = match.group(1) if match.groups() else line
                if warning_msg not in analysis.warnings:
                    analysis.warnings.append(warning_msg)
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.WARNING, line, {"warning": warning_msg})
                )
                break

        # Test results
        for pattern in self._compiled_patterns.get(OutputPatternType.TEST_PASSED, []):
            match = pattern.search(line)
            if match:
                try:
                    groups = match.groups()
                    count = int(match.group(1)) if groups and match.group(1).isdigit() else 1
                    analysis.tests_passed += count
                except (ValueError, AttributeError):
                    analysis.tests_passed += 1
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.TEST_PASSED, line)
                )
                break

        for pattern in self._compiled_patterns.get(OutputPatternType.TEST_FAILED, []):
            match = pattern.search(line)
            if match:
                try:
                    groups = match.groups()
                    count = int(match.group(1)) if groups and match.group(1).isdigit() else 1
                    analysis.tests_failed += count
                except (ValueError, AttributeError):
                    analysis.tests_failed += 1
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.TEST_FAILED, line)
                )
                break

        # Git operations
        for pattern in self._compiled_patterns.get(OutputPatternType.GIT_COMMIT, []):
            match = pattern.search(line)
            if match:
                commit_hash = match.group(1) if match.groups() else ""
                if commit_hash and commit_hash not in analysis.git_commits:
                    analysis.git_commits.append(commit_hash)
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.GIT_COMMIT, line, {"hash": commit_hash})
                )
                break

        for pattern in self._compiled_patterns.get(OutputPatternType.GIT_PUSH, []):
            match = pattern.search(line)
            if match:
                target = match.group(1) if match.groups() else ""
                if target and target not in analysis.git_pushes:
                    analysis.git_pushes.append(target)
                analysis.patterns.append(
                    PatternMatch(OutputPatternType.GIT_PUSH, line, {"target": target})
                )
                break

    def _clean_file_path(self, path: str) -> str:
        """Clean file path from extra characters."""
        # Remove common suffixes and prefixes
        path = path.strip()
        path = path.rstrip(".,;:)")
        path = path.lstrip("(")

        # Remove quotes
        if path.startswith(("'", '"')) and path.endswith(("'", '"')):
            path = path[1:-1]

        return path

    def get_summary_stats(self, analysis: OutputAnalysis) -> dict:
        """Get summary statistics from analysis.

        Args:
            analysis: OutputAnalysis to summarize

        Returns:
            Dictionary with summary stats
        """
        return {
            "files_created": len(analysis.files_created),
            "files_modified": len(analysis.files_modified),
            "files_deleted": len(analysis.files_deleted),
            "commands_executed": len(analysis.commands_executed),
            "tests_passed": analysis.tests_passed,
            "tests_failed": analysis.tests_failed,
            "errors": len(analysis.errors),
            "warnings": len(analysis.warnings),
            "git_commits": len(analysis.git_commits),
            "has_changes": analysis.has_changes(),
            "has_error": analysis.has_error,
            "is_completed": analysis.is_completed,
        }
