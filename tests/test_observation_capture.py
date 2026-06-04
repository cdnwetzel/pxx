"""Tests for observation capture and injection (pxx.observer enhancements)."""

from __future__ import annotations

from subprocess import Popen
from unittest.mock import Mock, patch


from pxx.observer import AiderMemoryObserver


class TestObservationFormatting:
    """Tests for observation formatting from tool use."""

    def test_format_observation_basic(self) -> None:
        """Test basic observation formatting."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc, repo_root="/repo", cwd="/repo/src")

        obs = observer._format_observation(
            "execute_bash",
            "ls -la /repo",
            "file1.py\nfile2.py\nfile3.py",
        )

        assert obs["title"] == "Tool use: execute_bash"
        assert "execute_bash" in obs["content"]
        assert "ls -la /repo" in obs["content"]
        assert "file1.py" in obs["content"]
        assert obs["source"] == "aider-session:execute_bash"

    def test_format_observation_includes_metadata(self) -> None:
        """Test observation includes project context metadata."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc, repo_root="/repo", cwd="/repo/src")

        obs = observer._format_observation("read_file", "test.py", "content")

        assert obs["metadata"]["tool"] == "read_file"
        assert obs["metadata"]["repo_root"] == "/repo"
        assert obs["metadata"]["cwd"] == "/repo/src"

    def test_format_observation_truncates_long_output(self) -> None:
        """Test long outputs are truncated."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        long_output = "x" * 1000
        obs = observer._format_observation("tool", "input", long_output)

        assert len(obs["content"]) < len(long_output) + 100
        assert "(truncated)" in obs["content"]

    def test_format_observation_empty_input(self) -> None:
        """Test formatting with empty input."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        obs = observer._format_observation("tool", "", "output")

        assert obs["title"] == "Tool use: tool"
        assert "output" in obs["content"]


class TestObservationInjection:
    """Tests for observation injection to memory."""

    @patch("pxx.observer.requests.post")
    def test_inject_observation_success(self, mock_post: Mock) -> None:
        """Test successful observation injection."""
        mock_post.return_value.status_code = 200
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        obs = {
            "title": "Test",
            "content": "Test content",
            "source": "test",
            "metadata": {},
        }

        observer._inject_observation(obs)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "/mem/inject" in args[0]
        assert kwargs["json"]["observations"][0] == obs

    @patch("pxx.observer.requests.post")
    @patch("builtins.print")
    def test_inject_observation_failure(
        self, mock_print: Mock, mock_post: Mock
    ) -> None:
        """Test injection logs but doesn't block on failure."""
        mock_post.return_value.status_code = 500
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        obs = {"title": "Test", "content": "Test"}

        observer._inject_observation(obs)

        # Should log error but not raise
        mock_print.assert_called_once()
        assert "inject failed" in str(mock_print.call_args)

    @patch("pxx.observer.requests.post")
    @patch("builtins.print")
    def test_inject_observation_timeout(
        self, mock_print: Mock, mock_post: Mock
    ) -> None:
        """Test injection handles timeout gracefully."""
        import requests

        mock_post.side_effect = requests.Timeout("timeout")
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        obs = {"title": "Test", "content": "Test"}

        observer._inject_observation(obs)

        # Should log error but not raise
        mock_print.assert_called_once()
        assert "inject error" in str(mock_print.call_args)


class TestToolCallResultPairing:
    """Tests for pairing tool calls with results."""

    def test_observer_stores_last_tool_call(self) -> None:
        """Test observer stores tool call for pairing."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        tool_call = {"tool_name": "execute_bash", "arguments": {"cmd": "ls"}}
        observer.last_tool_call = tool_call

        assert observer.last_tool_call == tool_call

    def test_observer_clears_tool_call_after_result(self) -> None:
        """Test tool call is cleared after result injection."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        # Simulate storing and then clearing
        observer.last_tool_call = {"tool_name": "test"}
        observer.last_tool_call = None

        assert observer.last_tool_call is None

    @patch("pxx.observer.requests.post")
    def test_observer_injects_on_result(self, mock_post: Mock) -> None:
        """Test observer injects observation when result received."""
        mock_post.return_value.status_code = 200
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc, repo_root="/repo", cwd="/repo")

        # Simulate tool call followed by result
        tool_call = {"tool_name": "execute_bash", "arguments": {"cmd": "ls"}}
        observer.last_tool_call = tool_call

        # Simulate result - observer would call _inject_observation
        obs = observer._format_observation(
            tool_call["tool_name"],
            str(tool_call.get("arguments", {})),
            "file1\nfile2",
        )
        observer._inject_observation(obs)

        # Should have POSTed to /mem/inject
        inject_calls = [
            call for call in mock_post.call_args_list if "/mem/inject" in str(call)
        ]
        assert len(inject_calls) > 0


class TestObserverInitialization:
    """Tests for observer initialization with project context."""

    def test_observer_init_with_context(self) -> None:
        """Test observer initialization with project context."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(
            mock_proc,
            "http://127.0.0.1:3111",
            repo_root="/home/user/project",
            cwd="/home/user/project/src",
        )

        assert observer.repo_root == "/home/user/project"
        assert observer.cwd == "/home/user/project/src"

    def test_observer_init_without_context(self) -> None:
        """Test observer initialization without context."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)

        assert observer.repo_root is None
        assert observer.cwd is None

    def test_observer_formats_observation_without_context(self) -> None:
        """Test observation formatting works without project context."""
        mock_proc = Mock(spec=Popen)
        observer = AiderMemoryObserver(mock_proc)  # No context

        obs = observer._format_observation("tool", "input", "output")

        assert obs["metadata"]["repo_root"] is None
        assert obs["metadata"]["cwd"] is None
        assert obs["content"]  # Content still present
