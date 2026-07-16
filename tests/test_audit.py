"""Tests for pxx.audit — session JSONL log (#004)."""

from __future__ import annotations

import gzip
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

from pxx.audit import (
    DEFAULT_RETENTION_DAYS,
    GZIP_AFTER_DAYS,
    is_sensitive_env,
    last_session_head_for,
    log_dir,
    make_session_id,
    now_iso,
    prune_old_logs,
    todays_log_file,
    write_session_start,
    _scrub_url,
)


class TestLogDir:
    def test_uses_xdg_state_home_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert log_dir() == tmp_path / "pxx" / "sessions"

    def test_falls_back_to_dot_local_state(self, monkeypatch):
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert log_dir() == Path.home() / ".local" / "state" / "pxx" / "sessions"

    def test_empty_xdg_state_home_falls_back(self, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", "")
        assert log_dir() == Path.home() / ".local" / "state" / "pxx" / "sessions"


class TestTodaysLogFile:
    def test_uses_iso_date_in_filename(self, tmp_path):
        result = todays_log_file(tmp_path)
        assert result.parent == tmp_path
        # YYYY-MM-DD.jsonl
        assert re.match(r"^\d{4}-\d{2}-\d{2}\.jsonl$", result.name)

    def test_filename_matches_today(self, tmp_path):
        result = todays_log_file(tmp_path)
        expected = datetime.now().strftime("%Y-%m-%d")
        assert result.name == f"{expected}.jsonl"


class TestMakeSessionId:
    def test_format(self):
        sid = make_session_id()
        # YYYYMMDDTHHMMSS-<4 hex chars>
        assert re.match(r"^\d{8}T\d{6}-[0-9a-f]{4}$", sid), sid

    def test_lexical_sort_matches_temporal(self):
        sid1 = make_session_id()
        time.sleep(1.1)
        sid2 = make_session_id()
        assert sid1 < sid2

    def test_two_calls_produce_different_ids(self):
        # Even within the same second, the hex suffix should disambiguate.
        ids = {make_session_id() for _ in range(50)}
        # Realistically 50 ids in <1 second should still mostly differ
        # because of the random suffix; assert at least 40 unique.
        assert len(ids) >= 40


class TestNowIso:
    def test_returns_iso_8601_with_offset(self):
        s = now_iso()
        # e.g. "2026-05-13T10:30:00.123-04:00" or "...+00:00"
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$", s
        ), s


class TestScrubUrl:
    def test_scrubs_basic_auth_credentials(self):
        """URL with user:password basic-auth credentials is scrubbed to scheme + host."""
        result = _scrub_url("http://user:pass@example.com:8080/path")
        assert result == "http://example.com:8080/path"

    def test_no_credentials_unchanged(self):
        """URL without credentials is returned unchanged."""
        result = _scrub_url("https://api.example.com/v1/data")
        assert result == "https://api.example.com/v1/data"

    def test_empty_string_returns_empty(self):
        """Empty string returns empty."""
        result = _scrub_url("")
        assert result == ""

    def test_bare_string_unchanged(self):
        """Bare non-URL string is returned as-is."""
        result = _scrub_url("not-a-url")
        assert result == "not-a-url"

    def test_uppercase_scheme_scrubs(self):
        """Uppercase-scheme URL still scrubs."""
        result = _scrub_url("HTTPS://USER:PASS@EXAMPLE.COM:443/PATH")
        assert result == "HTTPS://EXAMPLE.COM:443/PATH"


class TestIsSensitiveEnv:
    @pytest.mark.parametrize(
        "name",
        [
            "OPENAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "MY_PASSWORD",
            "openai_api_key",  # case-insensitive
            "secret_key_2",
        ],
    )
    def test_matches_sensitive(self, name):
        assert is_sensitive_env(name) is True

    @pytest.mark.parametrize(
        "name",
        ["PATH", "HOME", "USER", "PXX_OLLAMA_BASE", "OLLAMA_API_BASE", "TMPDIR"],
    )
    def test_does_not_match_safe(self, name):
        assert is_sensitive_env(name) is False


class TestWriteSessionStart:
    def test_creates_directory_and_file(self, tmp_path):
        log_path = tmp_path / "sessions" / "2026-05-13.jsonl"
        result = write_session_start({"foo": "bar"}, log_path=log_path)
        assert result == log_path
        assert log_path.exists()

    def test_record_is_valid_json_with_defaults(self, tmp_path):
        log_path = tmp_path / "2026-05-13.jsonl"
        write_session_start({"session_class": "ask"}, log_path=log_path)
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["session_class"] == "ask"
        assert rec["event"] == "session_start"
        assert "ts" in rec
        assert re.match(r"^\d{8}T\d{6}-[0-9a-f]{4}$", rec["session_id"])

    def test_appends_does_not_overwrite(self, tmp_path):
        log_path = tmp_path / "2026-05-13.jsonl"
        write_session_start({"n": 1}, log_path=log_path)
        write_session_start({"n": 2}, log_path=log_path)
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["n"] == 1
        assert json.loads(lines[1])["n"] == 2

    def test_caller_can_override_defaults(self, tmp_path):
        log_path = tmp_path / "2026-05-13.jsonl"
        write_session_start(
            {"event": "custom", "ts": "T", "session_id": "S", "x": 1}, log_path=log_path
        )
        rec = json.loads(log_path.read_text().strip())
        assert rec["event"] == "custom"
        assert rec["ts"] == "T"
        assert rec["session_id"] == "S"
        assert rec["x"] == 1

    def test_json_lines_format_no_pretty_printing(self, tmp_path):
        # Each record should be a single line — never pretty-printed.
        log_path = tmp_path / "2026-05-13.jsonl"
        write_session_start(
            {"session_class": "edit", "scope": ["tests/", "docs/"]}, log_path=log_path
        )
        content = log_path.read_text()
        assert content.count("\n") == 1
        assert "\n  " not in content  # no indented continuation lines


class TestLastSessionHeadFor:
    """#008 M2 lookup: find the previous session's git_head_sha for a given repo."""

    def _record(self, **overrides) -> str:
        rec = {
            "event": "session_start",
            "git_repo_root": "/work/pxx",
            "git_head_sha": "abc1234",
        }
        rec.update(overrides)
        return json.dumps(rec)

    def test_returns_none_when_directory_missing(self, tmp_path):
        assert (
            last_session_head_for("/work/pxx", directory=tmp_path / "missing") is None
        )

    def test_returns_none_when_directory_empty(self, tmp_path):
        assert last_session_head_for("/work/pxx", directory=tmp_path) is None

    def test_returns_sha_from_matching_record(self, tmp_path):
        (tmp_path / "2026-05-15.jsonl").write_text(self._record() + "\n")
        assert last_session_head_for("/work/pxx", directory=tmp_path) == "abc1234"

    def test_returns_most_recent_record_across_files(self, tmp_path):
        # Older log file (lexically smaller name).
        (tmp_path / "2026-05-14.jsonl").write_text(
            self._record(git_head_sha="old111") + "\n"
        )
        # Newer log file.
        (tmp_path / "2026-05-15.jsonl").write_text(
            self._record(git_head_sha="new222") + "\n"
        )
        assert last_session_head_for("/work/pxx", directory=tmp_path) == "new222"

    def test_returns_last_record_in_file(self, tmp_path):
        # Multiple records in one file — take the last (most recent).
        content = (
            self._record(git_head_sha="first111")
            + "\n"
            + self._record(git_head_sha="second2")
            + "\n"
        )
        (tmp_path / "2026-05-15.jsonl").write_text(content)
        assert last_session_head_for("/work/pxx", directory=tmp_path) == "second2"

    def test_ignores_records_for_other_repos(self, tmp_path):
        (tmp_path / "2026-05-15.jsonl").write_text(
            self._record(git_repo_root="/other/repo", git_head_sha="x") + "\n"
        )
        assert last_session_head_for("/work/pxx", directory=tmp_path) is None

    def test_ignores_null_sha(self, tmp_path):
        (tmp_path / "2026-05-15.jsonl").write_text(
            self._record(git_head_sha=None) + "\n"
        )
        assert last_session_head_for("/work/pxx", directory=tmp_path) is None

    def test_ignores_non_session_start_events(self, tmp_path):
        (tmp_path / "2026-05-15.jsonl").write_text(self._record(event="other") + "\n")
        assert last_session_head_for("/work/pxx", directory=tmp_path) is None

    def test_skips_corrupt_lines(self, tmp_path):
        content = "garbage-not-json\n" + self._record(git_head_sha="ok") + "\n"
        (tmp_path / "2026-05-15.jsonl").write_text(content)
        assert last_session_head_for("/work/pxx", directory=tmp_path) == "ok"

    def test_ignores_gz_files(self, tmp_path):
        # Gzipped files are out of scope; only .jsonl is read.
        (tmp_path / "2026-05-14.jsonl.gz").write_bytes(b"\x1f\x8bnot-decoded")
        (tmp_path / "2026-05-15.jsonl").write_text(
            self._record(git_head_sha="live") + "\n"
        )
        assert last_session_head_for("/work/pxx", directory=tmp_path) == "live"


class TestPruneOldLogs:
    def _make_aged_file(self, directory: Path, name: str, age_days: float) -> Path:
        """Create a file with mtime set to N days ago."""
        f = directory / name
        f.write_text("dummy")
        mtime = time.time() - age_days * 86400
        os.utime(f, (mtime, mtime))
        return f

    def test_missing_directory_is_noop(self, tmp_path):
        # Directory doesn't exist; should return (0, 0) without raising.
        missing = tmp_path / "nope"
        assert prune_old_logs(directory=missing) == (0, 0)

    def test_empty_directory(self, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        assert prune_old_logs(directory=tmp_path) == (0, 0)

    def test_recent_files_untouched(self, tmp_path):
        recent = self._make_aged_file(tmp_path, "2026-05-12.jsonl", age_days=2)
        result = prune_old_logs(directory=tmp_path, retention_days=90)
        assert result == (0, 0)
        assert recent.exists()

    def test_gzips_files_older_than_30_days(self, tmp_path):
        old = self._make_aged_file(tmp_path, "2026-04-01.jsonl", age_days=45)
        result = prune_old_logs(directory=tmp_path, retention_days=90)
        assert result == (1, 0)
        assert not old.exists()
        gz = tmp_path / "2026-04-01.jsonl.gz"
        assert gz.exists()
        # Content survived through gzip.
        with gzip.open(gz, "rb") as f:
            assert f.read() == b"dummy"

    def test_deletes_files_older_than_retention(self, tmp_path):
        old = self._make_aged_file(tmp_path, "2025-12-01.jsonl.gz", age_days=120)
        result = prune_old_logs(directory=tmp_path, retention_days=90)
        assert result == (0, 1)
        assert not old.exists()

    def test_mixed_ages(self, tmp_path):
        recent = self._make_aged_file(tmp_path, "today.jsonl", age_days=1)
        mid = self._make_aged_file(tmp_path, "midage.jsonl", age_days=45)
        ancient = self._make_aged_file(tmp_path, "ancient.jsonl.gz", age_days=200)
        gzipped, deleted = prune_old_logs(directory=tmp_path, retention_days=90)
        assert (gzipped, deleted) == (1, 1)
        assert recent.exists()
        assert not mid.exists()
        assert (tmp_path / "midage.jsonl.gz").exists()
        assert not ancient.exists()

    def test_retention_env_var_override(self, tmp_path, monkeypatch):
        f = self._make_aged_file(tmp_path, "x.jsonl", age_days=15)
        monkeypatch.setenv("PXX_LOG_RETENTION_DAYS", "10")
        # File is 15 days old; with retention=10, should be deleted.
        gzipped, deleted = prune_old_logs(directory=tmp_path)
        assert deleted == 1
        assert not f.exists()

    def test_idempotent(self, tmp_path):
        # Re-running on the same state should be a no-op.
        self._make_aged_file(tmp_path, "stale.jsonl", age_days=45)
        first = prune_old_logs(directory=tmp_path, retention_days=90)
        assert first == (1, 0)
        second = prune_old_logs(directory=tmp_path, retention_days=90)
        assert second == (0, 0)

    def test_non_file_entries_skipped(self, tmp_path):
        # A subdirectory should not be touched.
        (tmp_path / "subdir").mkdir()
        self._make_aged_file(tmp_path, "stale.jsonl", age_days=120)
        gzipped, deleted = prune_old_logs(directory=tmp_path, retention_days=90)
        assert (gzipped, deleted) == (0, 1)
        assert (tmp_path / "subdir").exists()


class TestConstants:
    def test_default_retention_is_90_days(self):
        assert DEFAULT_RETENTION_DAYS == 90

    def test_gzip_after_30_days(self):
        assert GZIP_AFTER_DAYS == 30
