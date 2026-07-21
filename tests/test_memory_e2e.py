"""End-to-end test for Phase 5 memory cycle: inject → execute → capture → retrieve.

These are live tests: they spawn real pxx sessions and talk to a running
agentmemory (and, for the full cycle, 9router + an LLM). They are opt-in so the
default unit suite stays deterministic — set PXX_RUN_LIVE=1 with the fleet up.
"""

import os
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PXX_RUN_LIVE") != "1",
    reason="live e2e: set PXX_RUN_LIVE=1 with the pxx fleet (9router + agentmemory + LLM) running",
)


class TestMemoryCycleE2E:
    """Validate complete memory flow: session 1 stores, session 2 retrieves."""

    @pytest.mark.xfail(
        reason="aider's TUI can't run under piped stdout (OSError 22 in asyncio "
        "add_reader); the supervised-observer runtime capture is BLOCKED — see "
        "pxx/observer.py. Needs PTY support before this cycle can pass.",
        strict=False,
    )
    def test_memory_persistence_across_sessions(self, tmp_path, monkeypatch):
        """
        Full memory cycle:
        1. Session 1: aider executes a tool (bash command)
        2. 9router captures the tool call as observation
        3. agentmemory stores it
        4. Session 2: new aider session
        5. 9router queries memory and injects prior context
        6. Verify aider sees the injected context
        """
        # Change to temp directory for clean session isolation
        monkeypatch.chdir(tmp_path)

        # Initialize git repo (pxx requires it)
        subprocess.run(["git", "init"], capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            capture_output=True,
            check=True,
        )

        # Create a simple project file
        (tmp_path / "hello.py").write_text("print('Hello from test')\n")

        # ============ SESSION 1: Generate and store observation ============
        print("\n=== Session 1: Generate observation ===")

        # Start services and run aider with a simple command
        # aider will execute the bash tool (ls) which should be captured
        session1_cmd = [
            "uv",
            "run",
            "pxx",
            "--with-router",
            "--with-memory",
            "--message",
            "show me the files in this directory using bash",
        ]

        # Run session 1 with timeout
        result1 = subprocess.run(
            session1_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_path),
        )

        print(f"Session 1 stdout: {result1.stdout[-500:]}")  # Last 500 chars
        print(f"Session 1 stderr: {result1.stderr[-500:]}")

        # Give agentmemory time to flush
        time.sleep(1)

        # ============ VERIFY: Check agentmemory has stored observation ============
        print("\n=== Verifying observation storage ===")

        # Query agentmemory directly
        with httpx.Client(timeout=5.0) as client:
            search_response = client.post(
                "http://127.0.0.1:3111/search",
                json={
                    "project": str(tmp_path),
                    "query": "files directory bash",
                    "limit": 10,
                },
            )

        assert search_response.status_code == 200, (
            f"Search failed: {search_response.status_code} {search_response.text}"
        )

        search_results = search_response.json()
        observations = search_results.get("results", [])

        print(f"Observations in memory: {len(observations)}")
        for i, obs in enumerate(observations):
            print(
                f"  {i + 1}. {obs.get('title', 'untitled')} "
                f"(score: {obs.get('score', 0):.2f})"
            )
            print(f"     Content: {obs.get('content', '')[:100]}...")

        # Verify at least one observation was captured
        assert len(observations) > 0, (
            "No observations found in memory after Session 1 — memory capture failed"
        )

        # Verify it's about a tool (bash, ls, file)
        all_content = "\n".join([obs.get("content", "") for obs in observations])
        assert any(
            word in all_content.lower()
            for word in ["bash", "ls", "file", "tool", "directory"]
        ), f"Observation content doesn't reference tool execution: {all_content}"

        print("✓ Observation stored successfully")

        # ============ SESSION 2: Verify memory injection ============
        print("\n=== Session 2: Verify memory injection ===")

        # Run a second session that should retrieve and use the prior context
        session2_cmd = [
            "uv",
            "run",
            "pxx",
            "--with-router",
            "--with-memory",
            "--message",
            "what did I ask you about in the previous session?",
        ]

        result2 = subprocess.run(
            session2_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_path),
        )

        print(f"Session 2 stdout: {result2.stdout[-500:]}")
        print(f"Session 2 stderr: {result2.stderr[-500:]}")

        # ============ VALIDATE: Session 2 output references prior context ============
        print("\n=== Validating memory was injected ===")

        session2_output = result2.stdout + result2.stderr

        # Check for indicators that memory was injected and used:
        # - aider should mention tools/bash/files from session 1
        # - 9router should have made /search calls (visible in stderr if verbose)
        # - memory context should appear in aider's response

        # Look for evidence of tool call or file references from Session 1
        memory_indicators = [
            "bash",
            "ls",
            "file",
            "directory",
            "previous",
            "before",
            "last time",
            "earlier",
        ]

        found_indicators = [
            word
            for word in memory_indicators
            if word.lower() in session2_output.lower()
        ]

        print(f"Found memory indicators in Session 2: {found_indicators}")

        # The test passes if:
        # 1. Observations were stored in Session 1
        # 2. Session 2 runs without error
        # 3. Session 2 output references context that could be from memory

        assert len(found_indicators) > 0, (
            f"Session 2 output doesn't reference prior context. "
            f"Memory injection may have failed.\n"
            f"Output: {session2_output[-1000:]}"
        )

        print("✓ Memory injection validated")
        print("✓ E2E memory cycle test PASSED")

    def test_agentmemory_directly_stores_and_retrieves(self):
        """
        Unit-level test: verify agentmemory API works for store → retrieve cycle.
        This is a simpler variant that doesn't involve aider.
        """
        # Allow time for services to start if not already running
        time.sleep(0.5)

        with httpx.Client(timeout=5.0) as client:
            # Store an observation (storage is POST /observations {project, content};
            # /inject is the retrieval endpoint, not storage).
            content = "Test Tool Call\n\nUser ran: ls -la /tmp\nResult: list of files"
            store_resp = client.post(
                "http://127.0.0.1:3111/observations",
                json={"project": "test-e2e", "content": content},
            )

            assert store_resp.status_code == 200, (
                f"Failed to store observation: {store_resp.status_code} "
                f"{store_resp.text}"
            )

            # Search for it immediately
            time.sleep(0.1)  # Give DB time to flush

            search_resp = client.post(
                "http://127.0.0.1:3111/search",
                json={"project": "test-e2e", "query": "ls tmp files", "limit": 5},
            )

            assert search_resp.status_code == 200, (
                f"Search failed: {search_resp.status_code} {search_resp.text}"
            )

            results = search_resp.json()
            observations = results.get("results", [])

            assert len(observations) > 0, "Stored observation not found in search"
            assert "ls -la /tmp" in observations[0]["content"]

            print("✓ agentmemory store → retrieve cycle works")

    def test_9router_forwards_requests_with_memory_middleware(self):
        """Verify 9router successfully proxies requests while middleware is active."""
        time.sleep(0.5)

        # A real 24B inference (prompt processing + generation) easily exceeds a
        # few seconds, so allow a generous read timeout and cap generation — the
        # point is to confirm 9router *proxies* (no 502), not that it's fast.
        with httpx.Client(timeout=60.0) as client:
            # Send a chat completion through 9router
            payload = {
                "model": "devstral:24b",
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                "max_tokens": 16,
            }

            try:
                resp = client.post(
                    "http://127.0.0.1:20128/v1/chat/completions",
                    json=payload,
                )
            except httpx.ReadTimeout:
                pytest.skip(
                    "9router/LLM did not respond within 60s — inference latency "
                    "(e.g. a cold 24B load), not a proxying failure"
                )

            # Should either succeed (200) or be overloaded, but not error (502)
            # 502 indicates proxying failed
            assert resp.status_code != 502, (
                f"9router returned 502 — proxying failed. "
                f"This suggests the middleware broke request forwarding. "
                f"Response: {resp.text}"
            )

            # If 200, verify response is valid
            if resp.status_code == 200:
                data = resp.json()
                assert "choices" in data
                assert len(data["choices"]) > 0
                print("✓ 9router proxying with middleware works")
            else:
                print(f"⚠ 9router returned {resp.status_code} (acceptable)")
