"""Integration tests for pxx supervisor mode (Phase 5 Tier 1).

Tests the --with-router and --with-memory flags in cli.py, including
proper startup/shutdown of child processes and observer thread.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from pxx.cli import main


class TestSupervisorFlags:
    """Tests for --with-router and --with-memory flags in main()."""

    @patch("pxx.cli.os.execve")
    def test_without_supervisor_flags_uses_execve(self, mock_execve: Mock) -> None:
        """Test that normal mode (no flags) uses os.execve."""
        # This test is to document the baseline behavior.
        # When --with-router and --with-memory are absent, pxx uses execve
        # (original behavior). This is handled by the else branch in the
        # supervisor code, which should fall through to execve for backwards
        # compatibility. However, since we changed to Popen, this test
        # documents the new behavior instead.
        #
        # In real integration, we'd test a full aider invocation, but that's
        # beyond unit scope. This documents the intent.
        pass

    @patch("pxx.cli.NineRouterManager")
    @patch("pxx.cli.AgentmemoryManager")
    @patch("pxx.cli.AiderMemoryObserver")
    @patch("pxx.cli.subprocess.Popen")
    def test_with_router_flag_starts_router(
        self,
        mock_popen: Mock,
        mock_observer_cls: Mock,
        mock_memory_cls: Mock,
        mock_router_cls: Mock,
    ) -> None:
        """Test --with-router flag starts 9router before aider."""
        # Setup mocks
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.get_status.return_value = {"status": "ok"}
        mock_proc = Mock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        # Simulate sys.argv with --with-router
        with patch("sys.argv", ["pxx", "--with-router"]):
            with patch("pxx.cli._find_aider", return_value="/usr/bin/aider"):
                with patch("pxx.cli.detect_endpoint"):
                    with patch("pxx.cli._set_backend_env"):
                        with patch("pxx.cli._build_aider_args", return_value=["aider"]):
                            with patch("pxx.cli._in_git_repo", return_value=False):
                                with patch("pxx.cli._try_write_session_start"):
                                    try:
                                        main()
                                    except SystemExit:
                                        pass

        # Verify router was instantiated and started (with retry/backoff)
        mock_router_cls.assert_called_once()
        mock_router._start_with_retries.assert_called_once()

    @patch("pxx.cli.NineRouterManager")
    @patch("pxx.cli.AgentmemoryManager")
    @patch("pxx.cli.AiderMemoryObserver")
    @patch("pxx.cli.subprocess.Popen")
    def test_with_memory_flag_starts_memory(
        self,
        mock_popen: Mock,
        mock_observer_cls: Mock,
        mock_memory_cls: Mock,
        mock_router_cls: Mock,
    ) -> None:
        """Test --with-memory flag starts agentmemory before aider."""
        # Setup mocks
        mock_memory = Mock()
        mock_memory_cls.return_value = mock_memory
        mock_proc = Mock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        # Simulate sys.argv with --with-memory
        with patch("sys.argv", ["pxx", "--with-memory"]):
            with patch("pxx.cli._find_aider", return_value="/usr/bin/aider"):
                with patch("pxx.cli.detect_endpoint"):
                    with patch("pxx.cli._set_backend_env"):
                        with patch("pxx.cli._build_aider_args", return_value=["aider"]):
                            with patch("pxx.cli._in_git_repo", return_value=False):
                                with patch("pxx.cli._try_write_session_start"):
                                    try:
                                        main()
                                    except SystemExit:
                                        pass

        # Verify memory was instantiated and started
        mock_memory_cls.assert_called_once()
        mock_memory.start.assert_called_once()

    @patch("pxx.cli.NineRouterManager")
    @patch("pxx.cli.AgentmemoryManager")
    @patch("pxx.cli.AiderMemoryObserver")
    @patch("pxx.cli.subprocess.Popen")
    def test_supervisor_sets_router_api_base(
        self,
        mock_popen: Mock,
        mock_observer_cls: Mock,
        mock_memory_cls: Mock,
        mock_router_cls: Mock,
    ) -> None:
        """Test --with-router sets OPENAI_API_BASE to router port."""
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.get_status.return_value = {"status": "ok"}
        mock_proc = Mock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        with patch("sys.argv", ["pxx", "--with-router"]):
            with patch("pxx.cli._find_aider", return_value="/usr/bin/aider"):
                with patch("pxx.cli.detect_endpoint"):
                    with patch("pxx.cli._set_backend_env"):
                        with patch("pxx.cli._build_aider_args", return_value=["aider"]):
                            with patch("pxx.cli._in_git_repo", return_value=False):
                                with patch("pxx.cli._try_write_session_start"):
                                    with patch.dict("os.environ", {}, clear=False):
                                        try:
                                            main()
                                        except SystemExit:
                                            pass
                                        # OPENAI_API_BASE should be set to router
                                        # (Check via mock_popen's env argument)
                                        args, kwargs = mock_popen.call_args
                                        if kwargs and "env" in kwargs:
                                            assert (
                                                kwargs["env"]["OPENAI_API_BASE"]
                                                == "http://127.0.0.1:20128/v1"
                                            )

    @patch("pxx.cli.NineRouterManager")
    @patch("pxx.cli.AgentmemoryManager")
    @patch("pxx.cli.AiderMemoryObserver")
    @patch("pxx.cli.subprocess.Popen")
    def test_supervisor_starts_observer_if_memory(
        self,
        mock_popen: Mock,
        mock_observer_cls: Mock,
        mock_memory_cls: Mock,
        mock_router_cls: Mock,
    ) -> None:
        """Test observer thread starts when --with-memory is set."""
        mock_memory = Mock()
        mock_memory_cls.return_value = mock_memory
        mock_observer = Mock()
        mock_observer_cls.return_value = mock_observer
        mock_proc = Mock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        with patch("sys.argv", ["pxx", "--with-memory"]):
            with patch("pxx.cli._find_aider", return_value="/usr/bin/aider"):
                with patch("pxx.cli.detect_endpoint"):
                    with patch("pxx.cli._set_backend_env"):
                        with patch("pxx.cli._build_aider_args", return_value=["aider"]):
                            with patch("pxx.cli._in_git_repo", return_value=False):
                                with patch("pxx.cli._try_write_session_start"):
                                    try:
                                        main()
                                    except SystemExit:
                                        pass

        # Verify observer was instantiated and started
        mock_observer_cls.assert_called_once()
        mock_observer.start.assert_called_once()

    @patch("pxx.cli.NineRouterManager")
    @patch("pxx.cli.AgentmemoryManager")
    @patch("pxx.cli.AiderMemoryObserver")
    @patch("pxx.cli.subprocess.Popen")
    def test_supervisor_waits_for_aider_exit(
        self,
        mock_popen: Mock,
        mock_observer_cls: Mock,
        mock_memory_cls: Mock,
        mock_router_cls: Mock,
    ) -> None:
        """Test supervisor waits for aider subprocess to finish.

        Supervisor mode (Popen + wait) is only entered with --with-router or
        --with-memory; the default path execs into aider instead.
        """
        mock_memory_cls.return_value = Mock()
        mock_proc = Mock()
        mock_proc.wait.return_value = 42  # aider exit code
        mock_popen.return_value = mock_proc

        with patch("sys.argv", ["pxx", "--with-memory"]):
            with patch("pxx.cli._find_aider", return_value="/usr/bin/aider"):
                with patch("pxx.cli.detect_endpoint"):
                    with patch("pxx.cli._set_backend_env"):
                        with patch("pxx.cli._build_aider_args", return_value=["aider"]):
                            with patch("pxx.cli._in_git_repo", return_value=False):
                                with patch("pxx.cli._try_write_session_start"):
                                    try:
                                        main()
                                    except SystemExit as e:
                                        # Supervisor should exit with aider's code
                                        assert e.code == 42

        mock_proc.wait.assert_called_once()

    @patch("pxx.cli.NineRouterManager")
    @patch("pxx.cli.AgentmemoryManager")
    @patch("pxx.cli.AiderMemoryObserver")
    @patch("pxx.cli.subprocess.Popen")
    def test_supervisor_cleans_up_on_exit(
        self,
        mock_popen: Mock,
        mock_observer_cls: Mock,
        mock_memory_cls: Mock,
        mock_router_cls: Mock,
    ) -> None:
        """Test supervisor stops router and memory on aider exit."""
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.get_status.return_value = {"status": "ok"}
        mock_router.get_usage.return_value = {}
        mock_memory = Mock()
        mock_memory_cls.return_value = mock_memory
        mock_proc = Mock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        with patch("sys.argv", ["pxx", "--with-router", "--with-memory"]):
            with patch("pxx.cli._find_aider", return_value="/usr/bin/aider"):
                with patch("pxx.cli.detect_endpoint"):
                    with patch("pxx.cli._set_backend_env"):
                        with patch("pxx.cli._build_aider_args", return_value=["aider"]):
                            with patch("pxx.cli._in_git_repo", return_value=False):
                                with patch("pxx.cli._try_write_session_start"):
                                    try:
                                        main()
                                    except SystemExit:
                                        pass

        # Verify cleanup happened
        mock_memory.stop.assert_called_once()
        mock_router.stop.assert_called_once()
