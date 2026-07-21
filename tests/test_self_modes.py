"""Tests for pxx.self_modes pure functions.

Covers determine_session_class and extract_self_fix_task from pxx/self_modes.py.
"""

from __future__ import annotations

from pxx.self_modes import determine_session_class, extract_self_fix_task


class TestDetermineSessionClass:
    def test_self_fix_wins_over_all_others(self):
        assert (
            determine_session_class(
                edit_mode=True, dry_run=True, self_improve_mode=True, self_fix_mode=True
            )
            == "self-fix"
        )
        assert (
            determine_session_class(
                edit_mode=False,
                dry_run=True,
                self_improve_mode=True,
                self_fix_mode=True,
            )
            == "self-fix"
        )
        assert (
            determine_session_class(
                edit_mode=True,
                dry_run=False,
                self_improve_mode=False,
                self_fix_mode=True,
            )
            == "self-fix"
        )

    def test_self_improve_wins_over_dry_run_and_edit(self):
        assert (
            determine_session_class(
                edit_mode=True,
                dry_run=True,
                self_improve_mode=True,
                self_fix_mode=False,
            )
            == "self-improve"
        )
        assert (
            determine_session_class(
                edit_mode=True,
                dry_run=False,
                self_improve_mode=True,
                self_fix_mode=False,
            )
            == "self-improve"
        )
        assert (
            determine_session_class(
                edit_mode=False,
                dry_run=True,
                self_improve_mode=True,
                self_fix_mode=False,
            )
            == "self-improve"
        )

    def test_dry_run_only_counts_when_edit_mode_true(self):
        assert (
            determine_session_class(
                edit_mode=True,
                dry_run=True,
                self_improve_mode=False,
                self_fix_mode=False,
            )
            == "dry-run"
        )
        assert (
            determine_session_class(
                edit_mode=False,
                dry_run=True,
                self_improve_mode=False,
                self_fix_mode=False,
            )
            == "ask"
        )

    def test_edit_mode_returns_edit(self):
        assert (
            determine_session_class(
                edit_mode=True,
                dry_run=False,
                self_improve_mode=False,
                self_fix_mode=False,
            )
            == "edit"
        )

    def test_default_returns_ask(self):
        assert (
            determine_session_class(
                edit_mode=False,
                dry_run=False,
                self_improve_mode=False,
                self_fix_mode=False,
            )
            == "ask"
        )


class TestExtractSelfFixTask:
    def test_task_string_following_self_fix_is_extracted_and_removed(self):
        task, rest = extract_self_fix_task(
            ["--self-fix", "fix typo", "--scope", "pxx/cli.py"]
        )
        assert task == "fix typo"
        assert rest == ["--self-fix", "--scope", "pxx/cli.py"]

    def test_argv_without_self_fix_returns_none_with_unchanged_argv(self):
        task, rest = extract_self_fix_task(["--edit", "--scope", "tests/"])
        assert task is None
        assert rest == ["--edit", "--scope", "tests/"]

    def test_self_fix_followed_by_flag_returns_none_with_unchanged_argv(self):
        task, rest = extract_self_fix_task(
            ["--self-fix", "--message", "fix it", "--scope", "x/"]
        )
        assert task is None
        assert rest == ["--self-fix", "--message", "fix it", "--scope", "x/"]

    def test_self_fix_at_end_of_argv_returns_none(self):
        task, rest = extract_self_fix_task(["--edit", "--self-fix"])
        assert task is None
        assert rest == ["--edit", "--self-fix"]
