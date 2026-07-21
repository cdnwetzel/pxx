"""Integration tests for memory middleware in 9router."""

import pytest
import importlib
import json
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

# Import modules using importlib (due to digit prefix)
_main_mod = importlib.import_module("9router_pkg.main")
_middleware_mod = importlib.import_module("9router_pkg.memory_middleware")

app = _main_mod.app
MemoryMiddleware = _middleware_mod.MemoryMiddleware


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_middleware():
    """Reset middleware before each test."""
    _main_mod.memory_middleware = None
    yield


class TestSlashCommandInterception:
    """Test that slash commands are intercepted and handled."""

    @pytest.mark.asyncio
    async def test_recall_command_intercepted(self, client):
        """POST /recall command is intercepted and returns memory results."""
        # Initialize middleware with mocked search
        _main_mod.memory_middleware = MemoryMiddleware()
        observations = [
            {
                "title": "Previous Fix",
                "content": "Fixed memory leak by changing X to Y",
                "score": 0.95,
            }
        ]
        _main_mod.memory_middleware.memory_client.search = AsyncMock(
            return_value=observations
        )

        # Send request with /recall command
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "devstral:24b",
                "messages": [
                    {"role": "user", "content": '/recall "memory leak"'},
                ],
            },
        )

        # Should return synthetic response
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "Previous Fix" in data["choices"][0]["message"]["content"]
        assert "0.95" in data["choices"][0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_remember_command_intercepted(self, client):
        """POST /remember command is intercepted and returns confirmation."""
        # Initialize middleware with mocked store
        _main_mod.memory_middleware = MemoryMiddleware()
        _main_mod.memory_middleware.memory_client.store_observation = AsyncMock(
            return_value=True
        )

        # Send request with /remember command
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "devstral:24b",
                "messages": [
                    {"role": "user", "content": '/remember "bug title" "fixed it"'},
                ],
            },
        )

        # Should return synthetic response
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert "Saved: bug title" in data["choices"][0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_recall_command_no_results(self, client):
        """POST /recall with no results returns appropriate message."""
        # Initialize middleware with empty search
        _main_mod.memory_middleware = MemoryMiddleware()
        _main_mod.memory_middleware.memory_client.search = AsyncMock(return_value=[])

        # Send request with /recall command
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "devstral:24b",
                "messages": [
                    {"role": "user", "content": '/recall "nonexistent"'},
                ],
            },
        )

        # Should still return 200 with no-results message
        assert response.status_code == 200
        data = response.json()
        assert "No observations found" in data["choices"][0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_normal_request_without_command(self, client):
        """Normal request (no slash command) is handled by router."""
        # Initialize middleware
        _main_mod.memory_middleware = MemoryMiddleware()
        _main_mod.memory_middleware.memory_client.search = AsyncMock(return_value=[])

        # Mock the router to avoid actual network calls
        with patch.object(
            _main_mod.router, "proxy_request", new_callable=AsyncMock
        ) as mock:
            # Return a normal LLM response
            mock.return_value = (
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "id": "llm_123",
                        "object": "chat.completion",
                        "model": "devstral:24b",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Hello"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    }
                ).encode(),
            )

            # Send normal request
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "devstral:24b",
                    "messages": [
                        {"role": "user", "content": "What is 2+2?"},
                    ],
                },
            )

            # Should return router response
            assert response.status_code == 200
            data = response.json()
            assert data["choices"][0]["message"]["content"] == "Hello"
            # Router should have been called
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_injection_in_request(self, client):
        """Memory context is injected into normal requests."""
        # Initialize middleware
        _main_mod.memory_middleware = MemoryMiddleware()
        observations = [
            {
                "title": "Memory Observation",
                "content": "This is important context",
                "score": 0.9,
            }
        ]
        _main_mod.memory_middleware.memory_client.search = AsyncMock(
            return_value=observations
        )

        # Mock the router to capture what request it receives
        with patch.object(
            _main_mod.router, "proxy_request", new_callable=AsyncMock
        ) as mock:
            mock.return_value = (
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "id": "llm_123",
                        "object": "chat.completion",
                        "model": "devstral:24b",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Response"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ).encode(),
            )

            # Send request
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "devstral:24b",
                    "messages": [
                        {"role": "user", "content": "What about the bug?"},
                    ],
                },
            )

            # Check that router was called with modified request
            assert response.status_code == 200
            assert mock.called
            # Get the body that was sent to router
            call_args = mock.call_args
            body_bytes = call_args[1]["body"]
            body = json.loads(body_bytes)

            # Should have injected system message with memory context
            assert len(body["messages"]) >= 2
            assert body["messages"][0]["role"] == "system"
            assert "Relevant Context from Memory" in body["messages"][0]["content"]
            assert "Memory Observation" in body["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_tool_observation_captured(self, client):
        """Tool calls from LLM response are captured as observations."""
        # Initialize middleware
        _main_mod.memory_middleware = MemoryMiddleware()
        _main_mod.memory_middleware.memory_client.search = AsyncMock(return_value=[])
        _main_mod.memory_middleware.memory_client.store_observation = AsyncMock(
            return_value=True
        )

        # Mock the router to return response with tool calls
        with patch.object(
            _main_mod.router, "proxy_request", new_callable=AsyncMock
        ) as mock:
            mock.return_value = (
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "id": "llm_123",
                        "object": "chat.completion",
                        "model": "devstral:24b",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": "call_123",
                                            "function": {
                                                "name": "bash",
                                                "arguments": '{"command": "ls -la"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    }
                ).encode(),
            )

            # Send request
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "devstral:24b",
                    "messages": [
                        {"role": "user", "content": "Run a command"},
                    ],
                },
            )

            # Should have captured tool call as observation
            assert response.status_code == 200
            _main_mod.memory_middleware.memory_client.store_observation.assert_called_once()
            call_args = (
                _main_mod.memory_middleware.memory_client.store_observation.call_args
            )
            obs = call_args[0][0]
            assert obs["title"] == "Tool use: bash"
            assert "bash" in obs["content"]

    @pytest.mark.asyncio
    async def test_middleware_disabled(self, client):
        """When middleware is None, request is forwarded normally."""
        _main_mod.memory_middleware = None

        # Mock the router
        with patch.object(
            _main_mod.router, "proxy_request", new_callable=AsyncMock
        ) as mock:
            mock.return_value = (
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "id": "llm_123",
                        "object": "chat.completion",
                        "model": "devstral:24b",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Hello"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ).encode(),
            )

            # Send request
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "devstral:24b",
                    "messages": [
                        {"role": "user", "content": "Hi"},
                    ],
                },
            )

            # Should forward normally
            assert response.status_code == 200
            assert mock.called

    @pytest.mark.asyncio
    async def test_health_check_unaffected(self, client):
        """Health check endpoint still works."""
        # Initialize middleware
        _main_mod.memory_middleware = MemoryMiddleware()

        # Mock router endpoint check
        with patch.object(
            _main_mod.router, "get_endpoint", new_callable=AsyncMock
        ) as mock:
            mock.return_value = "http://localhost:11434"

            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_models_endpoint_unaffected(self, client):
        """Models endpoint still works."""
        # Initialize middleware
        _main_mod.memory_middleware = MemoryMiddleware()

        # Mock router models
        with patch.object(
            _main_mod.router, "list_models", new_callable=AsyncMock
        ) as mock:
            mock.return_value = {
                "models": [
                    {"name": "devstral:24b", "modified_at": "2026-01-01"},
                ]
            }

            response = client.get("/v1/models")
            assert response.status_code == 200
            data = response.json()
            assert "models" in data
