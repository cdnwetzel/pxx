"""Unit tests for pxx.memory_injection (memory context injection into aider)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch


from pxx.memory_injection import MemoryInjector


class TestMemoryInjector:
    """Tests for MemoryInjector retrieval and formatting."""

    def test_injector_init(self) -> None:
        """Test MemoryInjector initialization."""
        injector = MemoryInjector("http://127.0.0.1:3111")
        assert injector.memory_api == "http://127.0.0.1:3111"

    def test_injector_init_default_api(self) -> None:
        """Test MemoryInjector with default API base."""
        injector = MemoryInjector()
        assert injector.memory_api == "http://127.0.0.1:3111"

    @patch("pxx.memory_injection.requests.post")
    def test_retrieve_success(self, mock_post: Mock) -> None:
        """Test retrieve() with successful response."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "results": [
                {
                    "id": "obs-1",
                    "content": "Fixed race condition in auth flow",
                    "score": 0.85,
                }
            ],
            "count": 1,
        }

        injector = MemoryInjector()
        result = injector.retrieve(repo_root="/repo", cwd="/repo/src")

        assert result["observations"]
        assert (
            result["observations"][0]["content"] == "Fixed race condition in auth flow"
        )
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0].endswith("/search")
        assert kwargs["json"]["project"] == "/repo"

    @patch("pxx.memory_injection.requests.post")
    def test_retrieve_empty_result(self, mock_post: Mock) -> None:
        """Test retrieve() with empty observations."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"observations": []}

        injector = MemoryInjector()
        result = injector.retrieve()

        assert result["observations"] == []

    @patch("pxx.memory_injection.requests.post")
    def test_retrieve_timeout(self, mock_post: Mock) -> None:
        """Test retrieve() handles timeout gracefully."""
        import requests

        mock_post.side_effect = requests.Timeout("Connection timeout")

        injector = MemoryInjector()
        result = injector.retrieve()

        assert result == {}

    @patch("pxx.memory_injection.requests.post")
    def test_retrieve_invalid_json(self, mock_post: Mock) -> None:
        """Test retrieve() handles invalid JSON response."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = ValueError("Invalid JSON")

        injector = MemoryInjector()
        result = injector.retrieve()

        assert result == {}

    @patch("pxx.memory_injection.requests.post")
    def test_retrieve_non_200_status(self, mock_post: Mock) -> None:
        """Test retrieve() returns empty dict on non-200 status."""
        mock_post.return_value.status_code = 500

        injector = MemoryInjector()
        result = injector.retrieve()

        assert result == {}

    def test_format_context_empty(self) -> None:
        """Test format_context() with empty observations."""
        injector = MemoryInjector()
        result = injector.format_context([])
        assert result == ""

    def test_format_context_single_observation(self) -> None:
        """Test format_context() with one observation."""
        injector = MemoryInjector()
        observations = [
            {
                "title": "Bug fix",
                "content": "Fixed auth issue",
                "source": "session-1",
                "score": 0.9,
            }
        ]

        result = injector.format_context(observations)

        assert "# Session Memory" in result
        assert "## 1. Bug fix" in result
        assert "Fixed auth issue" in result
        assert "session-1" in result
        assert "0.90" in result

    def test_format_context_multiple_observations(self) -> None:
        """Test format_context() with multiple observations."""
        injector = MemoryInjector()
        observations = [
            {
                "title": "First",
                "content": "Content 1",
                "source": "s1",
                "score": 0.8,
            },
            {
                "title": "Second",
                "content": "Content 2",
                "source": "s2",
                "score": 0.7,
            },
        ]

        result = injector.format_context(observations)

        assert "## 1. First" in result
        assert "## 2. Second" in result
        assert "Content 1" in result
        assert "Content 2" in result

    def test_format_context_missing_fields(self) -> None:
        """Test format_context() handles missing observation fields."""
        injector = MemoryInjector()
        observations = [
            {
                "title": "Observation",
                # content, source, score missing
            }
        ]

        result = injector.format_context(observations)

        assert "## 1. Observation" in result
        assert "# Session Memory" in result

    def test_write_context_file_success(self, tmp_path: Path) -> None:
        """Test write_context_file() creates temp file."""
        injector = MemoryInjector()
        observations = [
            {
                "title": "Test",
                "content": "Test content",
                "source": "test",
                "score": 0.8,
            }
        ]

        result = injector.write_context_file(observations, tmp_path)

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "# Session Memory" in content
        assert "Test content" in content

    def test_write_context_file_empty_observations(self, tmp_path: Path) -> None:
        """Test write_context_file() returns None for empty observations."""
        injector = MemoryInjector()
        result = injector.write_context_file([], tmp_path)
        assert result is None

    def test_write_context_file_invalid_directory(self) -> None:
        """Test write_context_file() returns None on write error."""
        injector = MemoryInjector()
        observations = [{"title": "Test", "content": "Test"}]
        invalid_dir = Path("/nonexistent/invalid/path")

        result = injector.write_context_file(observations, invalid_dir)

        assert result is None

    def test_inject_into_aider_args_no_observations(self) -> None:
        """Test inject_into_aider_args() returns original args when no observations."""
        injector = MemoryInjector()
        original_args = ["aider", "--model", "gpt-4"]

        with patch.object(injector, "retrieve", return_value={"observations": []}):
            result = injector.inject_into_aider_args(original_args)

            assert result == original_args

    @patch("pxx.memory_injection.requests.post")
    def test_inject_into_aider_args_success(
        self, mock_post: Mock, tmp_path: Path
    ) -> None:
        """Test inject_into_aider_args() adds --read flag."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "results": [
                {
                    "id": "obs-1",
                    "content": "Test content",
                    "score": 0.9,
                }
            ]
        }

        injector = MemoryInjector()
        original_args = ["aider", "--model", "gpt-4"]

        result = injector.inject_into_aider_args(
            original_args, repo_root="/repo", cwd="/repo", tmp_dir=tmp_path
        )

        assert len(result) > len(original_args)
        assert "--read" in result
        assert result[0] == "aider"  # Binary still first
        assert result[1] == "--read"  # --read after binary
        assert str(tmp_path / "pxx-memory-context.md") == result[2]

    def test_inject_into_aider_args_empty_args(self, tmp_path: Path) -> None:
        """Test inject_into_aider_args() with empty args list."""
        injector = MemoryInjector()

        with patch.object(
            injector,
            "retrieve",
            return_value={"observations": [{"title": "Test", "content": "Test"}]},
        ):
            with patch.object(
                injector,
                "write_context_file",
                return_value=tmp_path / "context.md",
            ):
                result = injector.inject_into_aider_args([], tmp_dir=tmp_path)

                assert "--read" in result
