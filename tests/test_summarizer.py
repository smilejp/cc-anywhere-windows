"""Tests for the output summarizer."""

import pytest

from cc_anywhere.core.summarizer import (
    OutputAnalysis,
    OutputPatternType,
    OutputSummarizer,
    PatternMatch,
)
from cc_anywhere.core.formatters import (
    FormattedMessage,
    MessengerFormatter,
    create_formatter,
)


class TestOutputSummarizer:
    """Tests for OutputSummarizer."""

    @pytest.fixture
    def summarizer(self):
        """Create a summarizer instance."""
        return OutputSummarizer()

    def test_empty_output(self, summarizer):
        """Test analyzing empty output."""
        analysis = summarizer.analyze("")
        assert not analysis.has_changes()
        assert not analysis.has_error
        assert not analysis.is_completed

    def test_file_created_detection(self, summarizer):
        """Test detecting file creation."""
        output = """
Created src/new_file.py
Write tests/test_new.py
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.files_created) == 2
        assert "src/new_file.py" in analysis.files_created
        assert "tests/test_new.py" in analysis.files_created

    def test_file_modified_detection(self, summarizer):
        """Test detecting file modification."""
        output = """
Modified src/existing.py
Updated config.json
Edit README.md
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.files_modified) == 3
        assert "src/existing.py" in analysis.files_modified
        assert "config.json" in analysis.files_modified

    def test_file_deleted_detection(self, summarizer):
        """Test detecting file deletion."""
        output = """
Deleted old_file.py
Removed temp.txt
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.files_deleted) == 2
        assert "old_file.py" in analysis.files_deleted
        assert "temp.txt" in analysis.files_deleted

    def test_command_execution_detection(self, summarizer):
        """Test detecting command execution."""
        output = """
$ pytest tests/
Running: npm install
Bash (git status)
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.commands_executed) >= 2

    def test_error_detection(self, summarizer):
        """Test detecting errors."""
        output = """
Error: Something went wrong
ModuleNotFoundError: No module named 'foo'
TypeError: unsupported operand type
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.errors) == 3
        assert analysis.has_error

    def test_warning_detection(self, summarizer):
        """Test detecting warnings."""
        output = """
Warning: Deprecated API
WARNING: This is a warning
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.warnings) == 2

    def test_test_passed_detection(self, summarizer):
        """Test detecting passed tests."""
        output = """
5 passed
OK (3 tests)
"""
        analysis = summarizer.analyze(output)
        assert analysis.tests_passed >= 5

    def test_test_failed_detection(self, summarizer):
        """Test detecting failed tests."""
        output = """
2 failed
FAIL: test_something
"""
        analysis = summarizer.analyze(output)
        assert analysis.tests_failed >= 2

    def test_git_commit_detection(self, summarizer):
        """Test detecting git commits."""
        output = """
commit abc1234
[main abc1234] Add new feature
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.git_commits) >= 1

    def test_git_push_detection(self, summarizer):
        """Test detecting git push."""
        output = """
Pushed to origin/main
push origin
"""
        analysis = summarizer.analyze(output)
        assert len(analysis.git_pushes) >= 1

    def test_completion_detection(self, summarizer):
        """Test detecting completion markers."""
        output = """
Some work...
Done.
"""
        analysis = summarizer.analyze(output)
        assert analysis.is_completed

    def test_has_changes(self, summarizer):
        """Test has_changes method."""
        # No changes
        analysis = OutputAnalysis()
        assert not analysis.has_changes()

        # With file creation
        analysis.files_created.append("test.py")
        assert analysis.has_changes()

    def test_complex_output(self, summarizer):
        """Test analyzing complex output with multiple patterns."""
        output = """
Created src/new_module.py
Modified src/main.py
$ pytest tests/ -v
5 passed, 1 failed
Warning: Deprecated function used
commit a1b2c3d
Done.
"""
        analysis = summarizer.analyze(output)

        assert len(analysis.files_created) == 1
        assert len(analysis.files_modified) == 1
        assert len(analysis.commands_executed) >= 1
        assert analysis.tests_passed == 5
        assert analysis.tests_failed == 1
        assert len(analysis.warnings) == 1
        assert len(analysis.git_commits) == 1
        assert analysis.is_completed
        assert analysis.has_changes()

    def test_summary_stats(self, summarizer):
        """Test getting summary stats."""
        output = """
Created file1.py
Created file2.py
Modified existing.py
$ pytest
3 passed
"""
        analysis = summarizer.analyze(output)
        stats = summarizer.get_summary_stats(analysis)

        assert stats["files_created"] == 2
        assert stats["files_modified"] == 1
        assert stats["files_deleted"] == 0
        assert stats["tests_passed"] == 3
        assert stats["has_changes"] is True


class TestMessengerFormatter:
    """Tests for MessengerFormatter."""

    @pytest.fixture
    def formatter(self):
        """Create a formatter instance."""
        return MessengerFormatter("discord")

    def test_format_acknowledgement(self, formatter):
        """Test formatting acknowledgement message."""
        msg = formatter.format_acknowledgement("Create a new file")

        assert msg.is_acknowledgement
        assert "명령 수신" in msg.text
        assert "Create a new file" in msg.text

    def test_format_acknowledgement_truncation(self, formatter):
        """Test acknowledgement message truncates long commands."""
        long_command = "a" * 100
        msg = formatter.format_acknowledgement(long_command)

        assert len(msg.text) < 200
        assert "..." in msg.text

    def test_format_progress(self, formatter):
        """Test formatting progress message."""
        analysis = OutputAnalysis()
        analysis.files_created = ["file1.py", "file2.py"]
        analysis.commands_executed = ["pytest"]

        msg = formatter.format_progress(analysis, update_count=3)

        assert msg.is_progress
        assert "진행 중" in msg.text
        assert "2개 생성" in msg.text
        assert "1개 실행" in msg.text

    def test_format_completion(self, formatter):
        """Test formatting completion message."""
        analysis = OutputAnalysis()
        analysis.files_created = ["new_file.py"]
        analysis.files_modified = ["existing.py"]
        analysis.commands_executed = ["pytest"]
        analysis.tests_passed = 5

        msg = formatter.format_completion(analysis, elapsed_seconds=30)

        assert msg.is_completion
        assert "작업 완료" in msg.text
        assert "파일 변경" in msg.text
        assert "new_file.py" in msg.text
        assert "소요시간" in msg.text
        assert "30초" in msg.text

    def test_format_completion_long_time(self, formatter):
        """Test formatting completion with minutes."""
        analysis = OutputAnalysis()

        msg = formatter.format_completion(analysis, elapsed_seconds=125)

        assert "2분" in msg.text
        assert "5초" in msg.text

    def test_format_error(self, formatter):
        """Test formatting error message."""
        analysis = OutputAnalysis()
        analysis.errors = ["ModuleNotFoundError: No module named 'foo'"]
        analysis.has_error = True

        msg = formatter.format_error(analysis)

        assert msg.is_error
        assert "작업 실패" in msg.text
        assert "ModuleNotFoundError" in msg.text
        assert "해결 방법" in msg.text
        assert "패키지" in msg.text

    def test_format_error_with_custom_message(self, formatter):
        """Test formatting error with custom message."""
        analysis = OutputAnalysis()

        msg = formatter.format_error(analysis, error_message="Custom error")

        assert "Custom error" in msg.text

    def test_error_suggestions(self, formatter):
        """Test error suggestion generation."""
        test_cases = [
            ("ModuleNotFoundError", "패키지"),
            ("ImportError", "import"),
            ("SyntaxError", "문법"),
            ("PermissionError", "권한"),
            ("FileNotFoundError", "경로"),
            ("timeout", "네트워크"),
        ]

        for error_text, expected_keyword in test_cases:
            analysis = OutputAnalysis()
            analysis.errors = [error_text]
            msg = formatter.format_error(analysis)
            assert expected_keyword in msg.text, f"Expected '{expected_keyword}' for error '{error_text}'"

    def test_create_formatter(self):
        """Test create_formatter factory function."""
        discord_formatter = create_formatter("discord")
        assert discord_formatter.platform == "discord"

        telegram_formatter = create_formatter("telegram")
        assert telegram_formatter.platform == "telegram"

        slack_formatter = create_formatter("slack")
        assert slack_formatter.platform == "slack"

    def test_truncate_path(self, formatter):
        """Test path truncation."""
        short_path = "file.py"
        assert formatter._truncate_path(short_path) == short_path

        long_path = "/very/long/path/to/some/deeply/nested/directory/file.py"
        truncated = formatter._truncate_path(long_path, max_length=30)
        assert len(truncated) <= 30
        assert "file.py" in truncated

    def test_format_raw_output(self, formatter):
        """Test raw output formatting."""
        output = "\n".join([f"Line {i}" for i in range(20)])
        formatted = formatter.format_raw_output(output, max_lines=10)

        lines = formatted.split("\n")
        assert len(lines) == 10
        assert "Line 19" in formatted


class TestPatternMatch:
    """Tests for PatternMatch dataclass."""

    def test_pattern_match_creation(self):
        """Test creating PatternMatch."""
        match = PatternMatch(
            pattern_type=OutputPatternType.FILE_CREATED,
            content="Created file.py",
            details={"file": "file.py"},
        )

        assert match.pattern_type == OutputPatternType.FILE_CREATED
        assert match.content == "Created file.py"
        assert match.details["file"] == "file.py"

    def test_pattern_match_defaults(self):
        """Test PatternMatch default values."""
        match = PatternMatch(
            pattern_type=OutputPatternType.UNKNOWN,
            content="Some content",
        )

        assert match.details == {}


class TestOutputAnalysis:
    """Tests for OutputAnalysis dataclass."""

    def test_output_analysis_defaults(self):
        """Test OutputAnalysis default values."""
        analysis = OutputAnalysis()

        assert analysis.files_created == []
        assert analysis.files_modified == []
        assert analysis.files_deleted == []
        assert analysis.commands_executed == []
        assert analysis.tests_passed == 0
        assert analysis.tests_failed == 0
        assert analysis.errors == []
        assert analysis.warnings == []
        assert analysis.git_commits == []
        assert not analysis.is_thinking
        assert not analysis.is_completed
        assert not analysis.has_error

    def test_has_changes_with_various_fields(self):
        """Test has_changes with different field combinations."""
        test_cases = [
            ("files_created", ["file.py"]),
            ("files_modified", ["file.py"]),
            ("files_deleted", ["file.py"]),
            ("commands_executed", ["ls"]),
            ("git_commits", ["abc123"]),
            ("errors", ["error"]),
        ]

        for field_name, value in test_cases:
            analysis = OutputAnalysis()
            setattr(analysis, field_name, value)
            assert analysis.has_changes(), f"Expected has_changes() for {field_name}"

        # Test with tests_passed
        analysis = OutputAnalysis()
        analysis.tests_passed = 1
        assert analysis.has_changes()

        # Test with tests_failed
        analysis = OutputAnalysis()
        analysis.tests_failed = 1
        assert analysis.has_changes()
