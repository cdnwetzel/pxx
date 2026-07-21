"""Unit tests for memory middleware."""

import pytest
import importlib
from unittest.mock import AsyncMock

# Import modules using importlib (due to digit prefix)
_middleware_mod = importlib.import_module("9router_pkg.memory_middleware")

MemoryMiddleware = _middleware_mod.MemoryMiddleware
SlashCommandMatcher = _middleware_mod.SlashCommandMatcher
AgentmemoryClient = _middleware_mod.AgentmemoryClient


class TestSlashCommandMatcher:
    """Test slash command detection and parsing."""

    def setup_method(self):
        """Initialize matcher for each test."""
        self.matcher = SlashCommandMatcher()

    def test_detect_recall_command(self):
        """Detect /recall "query" command."""
        result = self.matcher.detect('/recall "memory leak fix"')
        assert result == ("recall", "memory leak fix")

    def test_detect_recall_multiline(self):
        """Detect /recall in multiline text."""
        text = 'some context\n/recall "bug fix"\nmore text'
        result = self.matcher.detect(text)
        assert result == ("recall", "bug fix")

    def test_detect_remember_command(self):
        """Detect /remember "title" "content" command."""
        result = self.matcher.detect('/remember "bug title" "fix applied"')
        assert result == ("remember", '"bug title" "fix applied"')

    def test_detect_forget_command(self):
        """Detect /forget id command."""
        result = self.matcher.detect("/forget obs_id_123")
        assert result == ("forget", "obs_id_123")

    def test_no_command_detected(self):
        """Return None for non-command text."""
        result = self.matcher.detect("just a normal message")
        assert result is None

    def test_empty_text(self):
        """Handle empty text."""
        result = self.matcher.detect("")
        assert result is None

    def test_extract_tool_calls_empty_response(self):
        """Extract zero tool calls from response with no choices."""
        response = {"choices": []}
        result = self.matcher.extract_tool_calls(response)
        assert result == []

    def test_extract_tool_calls_no_tool_calls(self):
        """Extract zero tool calls when message has no tool_calls."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "regular text response",
                    }
                }
            ]
        }
        result = self.matcher.extract_tool_calls(response)
        assert result == []

    def test_extract_tool_calls_with_calls(self):
        """Extract tool calls from response."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "function": {
                                    "name": "bash",
                                    "arguments": '{"command": "ls"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        result = self.matcher.extract_tool_calls(response)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "bash"


class TestMemoryMiddleware:
    """Test memory middleware injection and capture."""

    def setup_method(self):
        """Initialize middleware for each test."""
        self.middleware = MemoryMiddleware()

    @pytest.mark.asyncio
    async def test_on_request_no_memory(self):
        """Request passes through when no memory found."""
        # Mock search to return empty
        self.middleware.memory_client.search = AsyncMock(return_value=[])

        request = {
            "model": "devstral:24b",
            "messages": [
                {"role": "user", "content": "help with this"},
            ],
        }

        result = await self.middleware.on_request(request)
        # Should pass through unchanged (no memory injected)
        assert result == request

    @pytest.mark.asyncio
    async def test_on_request_with_memory_injection(self):
        """Memory observations injected into system prompt."""
        observations = [
            {
                "title": "Previous fix",
                "content": "Fixed by changing X to Y",
                "score": 0.95,
            }
        ]
        self.middleware.memory_client.search = AsyncMock(return_value=observations)

        request = {
            "model": "devstral:24b",
            "messages": [
                {"role": "user", "content": "help with this"},
            ],
        }

        result = await self.middleware.on_request(request)

        # Should have injected a system message
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "system"
        assert "Relevant Context from Memory" in result["messages"][0]["content"]
        assert "Previous fix" in result["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_on_request_slash_command_recall(self):
        """Slash command /recall is marked for special handling."""
        request = {
            "model": "devstral:24b",
            "messages": [
                {"role": "user", "content": '/recall "memory test"'},
            ],
        }

        result = await self.middleware.on_request(request)

        # Should be marked as slash command
        assert "_pxx_slash_command" in result
        assert result["_pxx_slash_command"] == ("recall", "memory test")

    @pytest.mark.asyncio
    async def test_on_request_slash_command_remember(self):
        """Slash command /remember is marked for special handling."""
        request = {
            "model": "devstral:24b",
            "messages": [
                {"role": "user", "content": '/remember "title" "content"'},
            ],
        }

        result = await self.middleware.on_request(request)

        assert "_pxx_slash_command" in result
        assert result["_pxx_slash_command"][0] == "remember"

    @pytest.mark.asyncio
    async def test_on_response_no_tool_calls(self):
        """Response with no tool calls is handled gracefully."""
        request = {"model": "devstral:24b"}
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "regular response",
                    }
                }
            ]
        }

        # Should not raise
        await self.middleware.on_response(request, response)

    @pytest.mark.asyncio
    async def test_on_response_captures_tool_calls(self):
        """Tool calls from response are captured as observations."""
        self.middleware.memory_client.store_observation = AsyncMock(return_value=True)

        request = {"model": "devstral:24b"}
        response = {
            "choices": [
                {
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
                    }
                }
            ]
        }

        await self.middleware.on_response(request, response)

        # Should have called store_observation
        self.middleware.memory_client.store_observation.assert_called_once()
        call_args = self.middleware.memory_client.store_observation.call_args[0][0]
        assert call_args["title"] == "Tool use: bash"
        assert "bash" in call_args["content"]

    @pytest.mark.asyncio
    async def test_handle_slash_command_recall_no_results(self):
        """Recall with no results returns appropriate message."""
        self.middleware.memory_client.search = AsyncMock(return_value=[])

        result = await self.middleware.handle_slash_command("recall", "nonexistent")

        assert result["status"] == "success"
        assert "No observations found" in result["message"]

    @pytest.mark.asyncio
    async def test_handle_slash_command_recall_with_results(self):
        """Recall with results formats them properly."""
        observations = [
            {
                "title": "Fix 1",
                "content": "Fixed by doing X",
                "score": 0.95,
            },
            {
                "title": "Fix 2",
                "content": "Fixed by doing Y",
                "score": 0.85,
            },
        ]
        self.middleware.memory_client.search = AsyncMock(return_value=observations)

        result = await self.middleware.handle_slash_command("recall", "fix")

        assert result["status"] == "success"
        assert "Fix 1" in result["message"]
        assert "Fix 2" in result["message"]
        assert "0.95" in result["message"]

    @pytest.mark.asyncio
    async def test_handle_slash_command_remember_success(self):
        """Remember command stores observation."""
        self.middleware.memory_client.store_observation = AsyncMock(return_value=True)

        result = await self.middleware.handle_slash_command(
            "remember", '"Bug Title" "Fix applied"'
        )

        assert result["status"] == "success"
        assert "Saved: Bug Title" in result["message"]
        self.middleware.memory_client.store_observation.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_slash_command_remember_bad_format(self):
        """Remember with bad format returns error."""
        result = await self.middleware.handle_slash_command("remember", "bad format")

        assert result["status"] == "error"
        assert "Usage:" in result["message"]

    @pytest.mark.asyncio
    async def test_handle_slash_command_forget_not_implemented(self):
        """/forget returns not-yet-implemented message."""
        result = await self.middleware.handle_slash_command("forget", "some_id")

        assert result["status"] == "error"
        assert "not yet implemented" in result["message"]

    @pytest.mark.asyncio
    async def test_handle_slash_command_unknown(self):
        """Unknown command returns error."""
        result = await self.middleware.handle_slash_command("unknown", "")

        assert result["status"] == "error"
        assert "Unknown command" in result["message"]

    def test_build_memory_injection_prompt(self):
        """Memory injection prompt is formatted correctly."""
        observations = [
            {
                "title": "Observation 1",
                "content": "Content 1",
            },
            {
                "title": "Observation 2",
                "content": "Content 2",
            },
        ]

        result = self.middleware._build_memory_injection_prompt(observations)

        assert "Relevant Context from Memory" in result
        assert "Observation 1" in result
        assert "Observation 2" in result
        assert "Content 1" in result

    def test_format_tool_observation(self):
        """Tool call is formatted as observation."""
        tool_call = {
            "function": {
                "name": "bash",
                "arguments": '{"command": "ls -la"}',
            }
        }

        result = self.middleware._format_tool_observation(tool_call)

        assert result["title"] == "Tool use: bash"
        assert "bash" in result["content"]
        assert "ls -la" in result["content"]
        assert result["source"] == "aider-llm-tool-call"
        assert result["metadata"]["tool"] == "bash"

    def test_disabled_middleware_skips_injection(self):
        """When disabled, middleware doesn't inject memory."""
        self.middleware.enabled = False

        request = {
            "model": "devstral:24b",
            "messages": [{"role": "user", "content": "test"}],
        }

        # Should return unchanged even with enabled search
        import asyncio

        result = asyncio.run(self.middleware.on_request(request))

        assert result == request


class TestAgentmemoryClient:
    """Test agentmemory API client."""

    def setup_method(self):
        """Initialize client for each test."""
        self.client = AgentmemoryClient()

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Search with empty query returns empty results."""
        result = await self.client.search("")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_network_error(self):
        """Search handles network errors gracefully."""
        # With actual network error (no mock server), should return []
        result = await self.client.search("test", limit=5)
        # In test environment with no actual server, should return empty
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_store_observation_network_error(self):
        """Store handles network errors gracefully."""
        obs = {"title": "Test", "content": "Test"}
        result = await self.client.store_observation(obs)
        # With no mock server, should return False
        assert result is False
