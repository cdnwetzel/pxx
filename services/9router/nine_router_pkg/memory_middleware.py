"""Memory integration middleware for 9router.

Injects observations into prompts, captures tool calls from responses,
and intercepts slash commands (/recall, /remember, /forget).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import httpx


class AgentmemoryClient:
    """Async client for agentmemory API (port 3111)."""

    def __init__(self, api_base: str = "http://127.0.0.1:3111"):
        self.api_base = api_base
        self.timeout = 2.0

    async def search(
        self,
        query: str,
        limit: int = 3,
        min_score: float = 0.3,
        project_root: str | None = None,
    ) -> list[dict]:
        """Search memory for relevant observations.

        Args:
            query: Search term
            limit: Max results to return
            min_score: Minimum BM25 score threshold
            project_root: Optional project root for scoping observations

        Returns:
            List of observation dicts with title, content, score.
        """
        try:
            payload = {
                "query": query,
                "limit": limit,
                "min_score": min_score,
            }
            if project_root:
                payload["project_root"] = project_root

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.api_base}/search",
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    import logging

                    logging.debug(
                        f"[AgentmemoryClient.search] response keys: {data.keys()}"
                    )
                    # agentmemory returns "results", not "observations"
                    results = data.get("results", [])
                    logging.debug(
                        f"[AgentmemoryClient.search] found {len(results)} results"
                    )
                    return results
        except Exception:
            pass
        return []

    async def store_observation(self, observation: dict) -> bool:
        """Store an observation.

        Args:
            observation: Dict with title, content, metadata.

        Returns:
            True if stored successfully.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.api_base}/inject",
                    json={"observations": [observation]},
                )
                return resp.status_code == 200
        except Exception:
            pass
        return False


class SlashCommandMatcher:
    """Detect and parse slash commands in request text."""

    def detect(self, text: str) -> tuple[str, str] | None:
        """Check if text contains a slash command.

        Args:
            text: Request text (from user message in aider)

        Returns:
            (command, args) tuple or None if no command detected.
        """
        if not text:
            return None

        # Match /recall "query"
        match = re.search(r'^/recall\s+"([^"]+)"', text, re.MULTILINE)
        if match:
            return ("recall", match.group(1))

        # Match /remember "title" "content"
        match = re.search(r'^/remember\s+"([^"]+)"\s+"([^"]+)"', text, re.MULTILINE)
        if match:
            return ("remember", f'"{match.group(1)}" "{match.group(2)}"')

        # Match /forget id
        match = re.search(r"^/forget\s+(\S+)", text, re.MULTILINE)
        if match:
            return ("forget", match.group(1))

        return None

    def extract_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool calls from LLM response.

        Args:
            response: LLM response dict from /v1/chat/completions

        Returns:
            List of tool_call objects from the response choices.
        """
        tool_calls = []
        choices = response.get("choices", [])
        for choice in choices:
            message = choice.get("message", {})
            if "tool_calls" in message:
                tool_calls.extend(message["tool_calls"])
        return tool_calls


class MemoryMiddleware:
    """Main middleware: inject/capture memory around LLM requests."""

    def __init__(self, memory_api_base: str = "http://127.0.0.1:3111"):
        import os

        self.memory_client = AgentmemoryClient(memory_api_base)
        self.command_matcher = SlashCommandMatcher()
        self.enabled = True
        # For per-project scoping; use "default" if not specified
        self.project_root = os.getenv("PXX_PROJECT_ROOT", "default")

    async def on_request(self, request_body: dict) -> dict:
        """Process outgoing request before it reaches the LLM.

        1. Check for slash commands (mark for special handling)
        2. Query memory for context (inject into system prompt)
        3. Return modified request

        Args:
            request_body: Original /v1/chat/completions request

        Returns:
            Modified request_body with memory context injected.
        """
        if not self.enabled or not request_body:
            return request_body

        messages = request_body.get("messages", [])
        if not messages:
            return request_body

        # Check for slash commands in the last user message
        last_msg = messages[-1]
        if last_msg.get("role") == "user":
            content = last_msg.get("content", "")
            cmd_result = self.command_matcher.detect(content)
            if cmd_result:
                # Mark this request as a command for special handling
                request_body["_pxx_slash_command"] = cmd_result
                return request_body

        # Query memory for context injection
        user_query = last_msg.get("content", "") if messages else ""
        if user_query:
            # Use lower min_score for better recall (BM25 scores can be low)
            observations = await self.memory_client.search(
                user_query, limit=3, min_score=0.0, project_root=self.project_root
            )
            import logging

            logger = logging.getLogger(__name__)
            if observations:
                logger.info(
                    f"[on_request] Found {len(observations)} observations to inject"
                )
                # Inject into system prompt
                system_prompt = self._build_memory_injection_prompt(observations)
                # Insert after existing system message if present
                if messages and messages[0].get("role") == "system":
                    existing = messages[0].get("content", "")
                    messages[0]["content"] = f"{existing}\n\n{system_prompt}"
                else:
                    messages.insert(0, {"role": "system", "content": system_prompt})
                logger.info("[on_request] Injected observations into system prompt")
            else:
                logger.debug(
                    f"[on_request] No observations found for query: {user_query[:50]}"
                )

        return request_body

    async def on_response(self, request_body: dict, response_body: dict) -> None:
        """Process incoming response from LLM.

        1. Extract tool calls from response
        2. Format as observations
        3. Store in memory (fire-and-forget)

        Args:
            request_body: Original request
            response_body: LLM response dict
        """
        if not self.enabled:
            return

        try:
            tool_calls = self.command_matcher.extract_tool_calls(response_body)
            import logging

            logger = logging.getLogger(__name__)
            if tool_calls:
                logger.info(f"[on_response] Captured {len(tool_calls)} tool calls")
            for tool_call in tool_calls:
                obs = self._format_tool_observation(tool_call)
                # Fire and forget; don't block on memory store
                await self.memory_client.store_observation(obs)
                logger.info(
                    f"[on_response] Stored observation: {obs.get('title', '?')}"
                )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"[on_response] Error: {e}")
            # Silently ignore errors; don't block response
            pass

    async def handle_slash_command(
        self, command: str, args: str, repo_root: Optional[str] = None
    ) -> dict:
        """Execute a slash command.

        Args:
            command: Command name (recall, remember, forget)
            args: Command arguments
            repo_root: Optional repo context

        Returns:
            Response dict with status and message.
        """
        if command == "recall":
            # Use middleware's project_root if not provided
            project = repo_root or self.project_root
            observations = await self.memory_client.search(
                args, limit=5, project_root=project
            )
            if not observations:
                return {
                    "status": "success",
                    "message": f"No observations found for: {args}",
                }
            lines = [f"### Recall Results for '{args}':\n"]
            for obs in observations:
                score = obs.get("score", 0)
                title = obs.get("title", "Untitled")
                content = obs.get("content", "")
                lines.append(f"**{title}** (relevance: {score:.2f})")
                lines.append(content)
                lines.append("")
            return {"status": "success", "message": "\n".join(lines)}

        elif command == "remember":
            # Parse args as "title" "content"
            match = re.match(r'"([^"]+)"\s+"([^"]+)"', args)
            if not match:
                return {
                    "status": "error",
                    "message": 'Usage: /remember "title" "content"',
                }
            title, content = match.groups()
            obs = {
                "title": title,
                "content": content,
                "source": "user-remember-command",
                "metadata": {"repo_root": repo_root},
            }
            success = await self.memory_client.store_observation(obs)
            if success:
                return {"status": "success", "message": f"Saved: {title}"}
            else:
                return {"status": "error", "message": "Failed to save observation"}

        elif command == "forget":
            # TODO: agentmemory doesn't have /forget yet
            return {
                "status": "error",
                "message": "/forget not yet implemented",
            }

        return {"status": "error", "message": f"Unknown command: {command}"}

    def _build_memory_injection_prompt(self, observations: list[dict]) -> str:
        """Format observations as a system prompt section.

        Args:
            observations: List of observation dicts

        Returns:
            Markdown-formatted system prompt addition.
        """
        lines = ["### Relevant Context from Memory\n"]
        for obs in observations:
            title = obs.get("title", "Untitled")
            content = obs.get("content", "")
            lines.append(f"**{title}**")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)

    def _format_tool_observation(self, tool_call: dict) -> dict:
        """Format a tool_call from LLM response as an observation.

        Args:
            tool_call: Tool call object from LLM response

        Returns:
            Observation dict ready for storage.
        """
        function = tool_call.get("function", {})
        name = function.get("name", "unknown")
        arguments = function.get("arguments", "{}")

        return {
            "title": f"Tool use: {name}",
            "content": f"**Tool:** {name}\n**Arguments:**\n```\n{arguments}\n```",
            "source": "aider-llm-tool-call",
            "metadata": {"tool": name, "timestamp": datetime.now().isoformat()},
        }
