"""Tests for slash command execution (Phase 5 Tier 3)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from pxx.memory_commands import SlashCommandHandler


class TestCommandParsing:
    """Tests for parsing slash commands from aider output."""

    def test_parse_recall_command(self) -> None:
        """Test parsing /recall command."""
        handler = SlashCommandHandler()
        result = handler.parse_command("/recall authentication bug")
        assert result == ("recall", "authentication bug")

    def test_parse_remember_command(self) -> None:
        """Test parsing /remember command."""
        handler = SlashCommandHandler()
        result = handler.parse_command('/remember "title" "content"')
        assert result == ("remember", '"title" "content"')

    def test_parse_forget_command(self) -> None:
        """Test parsing /forget command."""
        handler = SlashCommandHandler()
        result = handler.parse_command("/forget obs-123")
        assert result == ("forget", "obs-123")

    def test_parse_unknown_command(self) -> None:
        """Test parsing unknown command returns None."""
        handler = SlashCommandHandler()
        result = handler.parse_command("/unknown arg")
        assert result is None

    def test_parse_non_command_line(self) -> None:
        """Test parsing non-command line returns None."""
        handler = SlashCommandHandler()
        result = handler.parse_command("this is not a command")
        assert result is None

    def test_is_command_line_true(self) -> None:
        """Test is_command_line detects valid commands."""
        handler = SlashCommandHandler()
        assert handler.is_command_line("/recall test")
        assert handler.is_command_line("/remember x")
        assert handler.is_command_line("/forget y")

    def test_is_command_line_false(self) -> None:
        """Test is_command_line rejects non-commands."""
        handler = SlashCommandHandler()
        assert not handler.is_command_line("not a command")
        assert not handler.is_command_line("/unknown test")


class TestRecallCommand:
    """Tests for /recall command execution."""

    @patch("pxx.memory_commands.requests.post")
    def test_recall_success(self, mock_post: Mock) -> None:
        """Test successful /recall execution."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "query": "race condition",
            "results": [
                {
                    "id": "obs-1",
                    "content": "Bug fix: Fixed race condition",
                    "score": 0.95,
                }
            ],
            "count": 1,
        }

        handler = SlashCommandHandler()
        result = handler.execute("recall", "race condition")

        assert result["success"]
        assert "Bug fix: Fixed race condition" in result["response"]
        assert "0.95" in result["response"]
        # Forwarded to the server's /command dispatcher
        args, kwargs = mock_post.call_args
        assert args[0].endswith("/command")
        assert kwargs["json"]["command"] == "recall"
        assert kwargs["json"]["args"]["query"] == "race condition"

    @patch("pxx.memory_commands.requests.post")
    def test_recall_no_results(self, mock_post: Mock) -> None:
        """Test /recall with no results."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"results": [], "count": 0}

        handler = SlashCommandHandler()
        result = handler.execute("recall", "nonexistent query")

        assert result["success"]
        assert "No observations found" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_recall_http_error(self, mock_post: Mock) -> None:
        """Test /recall handles HTTP errors."""
        mock_post.return_value.status_code = 500

        handler = SlashCommandHandler()
        result = handler.execute("recall", "test")

        assert not result["success"]
        assert "500" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_recall_network_error(self, mock_post: Mock) -> None:
        """Test /recall handles network errors."""
        mock_post.side_effect = requests.ConnectionError("timeout")

        handler = SlashCommandHandler()
        result = handler.execute("recall", "test")

        assert not result["success"]
        assert "connection error" in result["response"].lower()

    def test_recall_empty_query(self) -> None:
        """Test /recall with empty query."""
        handler = SlashCommandHandler()
        result = handler.execute("recall", "")

        assert not result["success"]
        assert "Usage" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_recall_with_context(self, mock_post: Mock) -> None:
        """Test /recall scopes the query to the repo via the project field."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"results": [], "count": 0}

        handler = SlashCommandHandler()
        handler.execute("recall", "test", repo_root="/repo", cwd="/repo/src")

        # repo_root maps to the server's per-project scope
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["project"] == "/repo"


class TestRememberCommand:
    """Tests for /remember command execution."""

    @patch("pxx.memory_commands.requests.post")
    def test_remember_success(self, mock_post: Mock) -> None:
        """Test successful /remember execution."""
        mock_post.return_value.status_code = 200

        handler = SlashCommandHandler()
        result = handler.execute("remember", '"My Note" "Important content"')

        assert result["success"]
        assert "Saved" in result["response"]
        assert "My Note" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_remember_save_failure(self, mock_post: Mock) -> None:
        """Test /remember handles save failure."""
        mock_post.return_value.status_code = 500

        handler = SlashCommandHandler()
        result = handler.execute("remember", '"Title" "Content"')

        assert not result["success"]
        assert "500" in result["response"]

    def test_remember_invalid_format(self) -> None:
        """Test /remember with invalid format."""
        handler = SlashCommandHandler()
        result = handler.execute("remember", "no quotes here")

        assert not result["success"]
        assert "Usage" in result["response"]

    def test_remember_empty_args(self) -> None:
        """Test /remember with empty args."""
        handler = SlashCommandHandler()
        result = handler.execute("remember", "")

        assert not result["success"]
        assert "Usage" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_remember_colon_format(self, mock_post: Mock) -> None:
        """Test /remember with colon separator format."""
        mock_post.return_value.status_code = 200

        handler = SlashCommandHandler()
        result = handler.execute("remember", "Title:Some content here")

        assert result["success"]
        # Verify the command was forwarded with parsed title/content
        args, kwargs = mock_post.call_args
        assert args[0].endswith("/command")
        assert kwargs["json"]["command"] == "remember"
        assert kwargs["json"]["args"]["title"] == "Title"
        assert kwargs["json"]["args"]["content"] == "Some content here"

    @patch("pxx.memory_commands.requests.post")
    def test_remember_network_error(self, mock_post: Mock) -> None:
        """Test /remember handles network errors."""
        mock_post.side_effect = requests.Timeout()

        handler = SlashCommandHandler()
        result = handler.execute("remember", '"Title" "Content"')

        assert not result["success"]
        assert "error" in result["response"].lower()


class TestForgetCommand:
    """Tests for /forget command execution."""

    @patch("pxx.memory_commands.requests.post")
    def test_forget_success(self, mock_post: Mock) -> None:
        """Test successful /forget execution."""
        mock_post.return_value.status_code = 200

        handler = SlashCommandHandler()
        result = handler.execute("forget", "obs-123")

        assert result["success"]
        assert "obs-123" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_forget_http_error(self, mock_post: Mock) -> None:
        """Test /forget handles HTTP errors."""
        mock_post.return_value.status_code = 404

        handler = SlashCommandHandler()
        result = handler.execute("forget", "obs-123")

        assert not result["success"]
        assert "404" in result["response"]

    def test_forget_empty_id(self) -> None:
        """Test /forget with empty ID."""
        handler = SlashCommandHandler()
        result = handler.execute("forget", "")

        assert not result["success"]
        assert "Usage" in result["response"]

    @patch("pxx.memory_commands.requests.post")
    def test_forget_network_error(self, mock_post: Mock) -> None:
        """Test /forget handles network errors."""
        mock_post.side_effect = requests.ConnectionError()

        handler = SlashCommandHandler()
        result = handler.execute("forget", "obs-123")

        assert not result["success"]
        assert "error" in result["response"].lower()


class TestCommandExecution:
    """Integration tests for command execution."""

    def test_execute_unknown_command(self) -> None:
        """Test execute with unknown command."""
        handler = SlashCommandHandler()
        result = handler.execute("unknown", "arg")

        assert not result["success"]
        assert "Unknown command" in result["response"]

    def test_execute_with_exception(self) -> None:
        """Test execute handles exceptions gracefully."""
        handler = SlashCommandHandler()
        # Manually set a bad memory API to force an error
        handler.memory_api = "http://invalid:99999"

        result = handler.execute("recall", "test")
        assert not result["success"]
        assert "error" in result["response"].lower()
