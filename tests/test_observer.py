"""Unit tests for pxx.observer (aider output parsing and memory integration)."""

from __future__ import annotations

from subprocess import Popen
from unittest.mock import Mock, patch

from pxx.observer import AiderMemoryObserver, AiderOutputParser


class TestAiderOutputParser:
    """Tests for AiderOutputParser.parse_stream()."""

    def test_parse_tool_call(self) -> None:
        """Test parsing a tool call from aider output."""
        parser = AiderOutputParser()
        lines = ['{"tool_name": "execute_bash", "arguments": {"command": "ls"}}']

        events = list(parser.parse_stream(lines))

        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "tool_call"
        assert payload["tool_name"] == "execute_bash"

    def test_parse_tool_result(self) -> None:
        """Test parsing a tool result from aider output."""
        parser = AiderOutputParser()
        lines = ["<tool_result>file1.py\nfile2.py</tool_result>"]

        events = list(parser.parse_stream(lines))

        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "tool_result"
        assert payload["output"] == "file1.py\nfile2.py"
        assert payload["success"] is True

    def test_parse_error(self) -> None:
        """Test parsing an error message from aider output."""
        parser = AiderOutputParser()
        lines = ["Error: something went wrong"]

        events = list(parser.parse_stream(lines))

        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "error"
        assert "something went wrong" in payload["message"]

    def test_parse_conversation_start(self) -> None:
        """Test parsing conversation start marker."""
        parser = AiderOutputParser()
        lines = ["Starting session..."]

        events = list(parser.parse_stream(lines))

        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "conversation_start"

    def test_parse_skip_empty_lines(self) -> None:
        """Test that empty lines are skipped."""
        parser = AiderOutputParser()
        lines = ["", "  ", "\n", '{"tool_name": "test"}']

        events = list(parser.parse_stream(lines))

        assert len(events) == 1
        assert events[0][0] == "tool_call"

    def test_parse_invalid_json(self) -> None:
        """Test that invalid JSON is gracefully skipped."""
        parser = AiderOutputParser()
        lines = ['{"tool_name": "test"} INVALID SUFFIX']

        events = list(parser.parse_stream(lines))

        # Invalid JSON should be skipped; no event produced
        assert len(events) == 0

    def test_extract_tag_found(self) -> None:
        """Test _extract_tag() when tag is present."""
        parser = AiderOutputParser()
        text = "prefix <mytag>content here</mytag> suffix"

        result = parser._extract_tag("mytag", text)

        assert result == "content here"

    def test_extract_tag_not_found(self) -> None:
        """Test _extract_tag() when tag is missing."""
        parser = AiderOutputParser()
        text = "no tags here"

        result = parser._extract_tag("mytag", text)

        assert result is None

    def test_extract_tag_mismatched(self) -> None:
        """Test _extract_tag() when opening tag is present but not closing."""
        parser = AiderOutputParser()
        text = "prefix <mytag>content without closing"

        result = parser._extract_tag("mytag", text)

        assert result is None


class TestAiderMemoryObserver:
    """Tests for AiderMemoryObserver."""

    def test_observer_init(self) -> None:
        """Test AiderMemoryObserver initialization."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc, "http://127.0.0.1:3111")

        assert observer.aider == mock_proc
        assert observer.memory_api == "http://127.0.0.1:3111"
        assert observer.thread is None

    def test_observer_start(self) -> None:
        """Test start() spawns observer thread."""
        mock_proc = Mock(spec=Popen)
        # No stdout to read: _run() hits its early-return guard so the daemon
        # thread exits cleanly instead of raising on a Mock stream.
        mock_proc.stdout = None
        observer = AiderMemoryObserver(mock_proc)

        observer.start()

        assert observer.thread is not None
        assert observer.thread.daemon is True

    @patch("pxx.observer.requests.post")
    def test_observer_store_observation_success(self, mock_post: Mock) -> None:
        """Test _store_observation() POSTs to /observations on success."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc, repo_root="/repo")
        mock_post.return_value.status_code = 200

        observer._store_observation({"content": "Tool use: ls"})

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "http://127.0.0.1:3111/observations" in args[0]
        assert kwargs["json"] == {"project": "/repo", "content": "Tool use: ls"}
        assert kwargs["timeout"] == 2

    @patch("pxx.observer.requests.post")
    @patch("builtins.print")
    def test_observer_store_observation_failure(
        self, mock_print: Mock, mock_post: Mock
    ) -> None:
        """Test _store_observation() logs but doesn't block on failure."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)
        mock_post.return_value.status_code = 500

        observer._store_observation({"content": "x"})

        # Should print error but not raise
        mock_print.assert_called_once()
        assert "memory store failed" in str(mock_print.call_args)

    @patch("pxx.observer.requests.post")
    @patch("builtins.print")
    def test_observer_store_observation_timeout(
        self, mock_print: Mock, mock_post: Mock
    ) -> None:
        """Test _store_observation() handles connection errors gracefully."""
        import requests

        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)
        mock_post.side_effect = requests.Timeout("Connection timeout")

        observer._store_observation({"content": "x"})

        # Should print error but not raise
        mock_print.assert_called_once()
        assert "memory store error" in str(mock_print.call_args)
