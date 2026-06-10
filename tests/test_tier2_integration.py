"""End-to-end integration tests for Phase 5 Tier 2 (memory injection + observation capture)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch


from pxx.memory_injection import MemoryInjector
from pxx.observer import AiderMemoryObserver


class TestTier2EndToEnd:
    """Integration tests for memory injection + observation capture."""

    @patch("pxx.observer.requests.post")
    @patch("pxx.memory_injection.requests.post")
    def test_memory_injection_and_observation_capture_flow(
        self, mock_injector_post: Mock, mock_observer_post: Mock
    ) -> None:
        """Test full Tier 2 flow: inject memory then capture observations."""
        # Setup mocks
        mock_injector_post.return_value.status_code = 200
        mock_injector_post.return_value.json.return_value = {
            "observations": [
                {
                    "title": "Previous session observation",
                    "content": "Fixed a bug in auth.py",
                    "source": "previous-session",
                    "score": 0.9,
                }
            ]
        }

        mock_observer_post.return_value.status_code = 200

        # Step 1: Memory injection
        injector = MemoryInjector()
        aider_args = ["aider", "--model", "gpt-4"]

        # Mock the retrieve method to return observations
        with patch.object(
            injector,
            "retrieve",
            return_value={
                "observations": [
                    {
                        "title": "Previous session observation",
                        "content": "Fixed a bug in auth.py",
                        "source": "previous-session",
                        "score": 0.9,
                    }
                ]
            },
        ):
            args_with_memory = injector.inject_into_aider_args(
                aider_args, repo_root="/repo", cwd="/repo"
            )

        # Verify --read flag was added
        assert "--read" in args_with_memory
        read_idx = args_with_memory.index("--read")
        memory_context_file = args_with_memory[read_idx + 1]
        assert Path(memory_context_file).exists()
        content = Path(memory_context_file).read_text()
        assert "Session Memory" in content
        assert "Previous session observation" in content

        # Step 2: Observer captures tool use
        mock_proc = Mock()
        observer = AiderMemoryObserver(mock_proc, repo_root="/repo", cwd="/repo")

        # Simulate tool call and result
        tool_call = {"tool_name": "execute_bash", "arguments": {"cmd": "ls"}}
        observer.last_tool_call = tool_call

        tool_result = "file1.py\nfile2.py"
        obs = observer._format_observation(
            tool_call["tool_name"],
            str(tool_call["arguments"]),
            tool_result,
        )

        # Verify observation is properly formatted
        assert "execute_bash" in obs["title"]
        assert obs["metadata"]["repo_root"] == "/repo"
        assert obs["metadata"]["cwd"] == "/repo"

        # Step 3: Store observation
        observer._store_observation(obs)

        # Verify the store POST was made
        assert mock_observer_post.call_count >= 1
        # Find the /observations call
        store_calls = [
            call
            for call in mock_observer_post.call_args_list
            if "/observations" in str(call)
        ]
        assert len(store_calls) > 0

    def test_memory_context_file_format(self, tmp_path: Path) -> None:
        """Test memory context file has correct markdown format."""
        injector = MemoryInjector()

        observations = [
            {
                "title": "Bug fix",
                "content": "Fixed race condition",
                "source": "session-1",
                "score": 0.95,
            },
            {
                "title": "Feature addition",
                "content": "Added caching layer",
                "source": "session-2",
                "score": 0.85,
            },
        ]

        context_file = injector.write_context_file(observations, tmp_path)
        assert context_file is not None

        content = context_file.read_text()

        # Verify markdown structure
        assert "# Session Memory" in content
        assert "## 1. Bug fix" in content
        assert "## 2. Feature addition" in content
        assert "Fixed race condition" in content
        assert "Added caching layer" in content
        assert "session-1" in content
        assert "session-2" in content

    def test_observation_with_project_context(self) -> None:
        """Test observation includes project context metadata."""
        mock_proc = Mock()
        observer = AiderMemoryObserver(
            mock_proc,
            repo_root="/home/user/project",
            cwd="/home/user/project/src",
        )

        obs = observer._format_observation(
            "read_file",
            "main.py",
            "def main(): pass",
        )

        assert obs["metadata"]["repo_root"] == "/home/user/project"
        assert obs["metadata"]["cwd"] == "/home/user/project/src"
        assert obs["metadata"]["tool"] == "read_file"

    @patch("pxx.memory_injection.requests.post")
    def test_memory_injection_graceful_degradation(self, mock_post: Mock) -> None:
        """Test memory injection gracefully degrades if memory unavailable."""
        mock_post.return_value.status_code = 500  # Simulate error

        injector = MemoryInjector()
        aider_args = ["aider", "--model", "gpt-4"]

        # Should return original args if memory fails
        result = injector.inject_into_aider_args(aider_args)
        assert result == aider_args

    @patch("pxx.observer.requests.post")
    def test_observation_injection_graceful_degradation(self, mock_post: Mock) -> None:
        """Test observation injection doesn't block aider on failure."""
        import requests

        mock_post.side_effect = requests.Timeout()

        mock_proc = Mock()
        observer = AiderMemoryObserver(mock_proc)

        obs = {"title": "Test", "content": "Test"}

        # Should not raise, just log
        observer._store_observation(obs)

        # Verify the store was attempted
        mock_post.assert_called_once()


class TestTier2Integration:
    """Tests for integration between Tier 1 (supervisor) and Tier 2 (memory)."""

    def test_observer_with_router_and_memory(self) -> None:
        """Test observer works with both router and memory active."""
        # This simulates the cli.py scenario where both are running
        mock_proc = Mock()
        observer = AiderMemoryObserver(
            mock_proc,
            memory_api_base="http://127.0.0.1:3111",
            repo_root="/repo",
            cwd="/repo",
        )

        # Router would set OPENAI_API_BASE in env
        # Memory would have observer capturing tool use
        # Together: aider uses compressed tokens + observations recorded

        obs = observer._format_observation("execute_bash", "ls", "file1\nfile2")
        assert obs["title"]
        assert obs["content"]

    def test_memory_injection_doesnt_require_memory_server(self) -> None:
        """Test memory injection works independently of agentmemory server."""
        injector = MemoryInjector()

        # Create fake observations (would come from agentmemory in practice)
        observations = [
            {
                "title": "Previous fix",
                "content": "Fixed X",
                "source": "test",
                "score": 0.9,
            }
        ]

        # Format and write without any HTTP calls
        content = injector.format_context(observations)
        assert "Previous fix" in content
        assert "Fixed X" in content
