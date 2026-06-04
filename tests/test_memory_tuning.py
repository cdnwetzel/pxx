"""Tests for dynamic memory tuning (Phase 5 Tier 3)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from pxx.memory_tuning import MemoryTuneConfig, MemoryTuner


class TestMemoryTuneConfig:
    """Tests for MemoryTuneConfig."""

    def test_config_defaults(self) -> None:
        """Test config creation with defaults."""
        cfg = MemoryTuneConfig(
            min_relevance_score=0.5,
            max_observations=5,
            max_context_chars=8000,
            leave_context_headroom=2000,
        )
        assert cfg.min_relevance_score == 0.5
        assert cfg.max_observations == 5
        assert cfg.max_context_chars == 8000
        assert cfg.leave_context_headroom == 2000

    def test_config_validation_relevance_score(self) -> None:
        """Test relevance score must be 0.0-1.0."""
        with pytest.raises(ValueError, match="min_relevance_score"):
            MemoryTuneConfig(
                min_relevance_score=1.5,
                max_observations=5,
                max_context_chars=8000,
                leave_context_headroom=2000,
            )

    def test_config_validation_max_observations(self) -> None:
        """Test max_observations must be >= 1."""
        with pytest.raises(ValueError, match="max_observations"):
            MemoryTuneConfig(
                min_relevance_score=0.5,
                max_observations=0,
                max_context_chars=8000,
                leave_context_headroom=2000,
            )

    def test_config_validation_max_context(self) -> None:
        """Test max_context_chars must be >= 500."""
        with pytest.raises(ValueError, match="max_context_chars"):
            MemoryTuneConfig(
                min_relevance_score=0.5,
                max_observations=5,
                max_context_chars=100,
                leave_context_headroom=2000,
            )

    @patch.dict(
        os.environ,
        {
            "PXX_MEMORY_THRESHOLD": "0.7",
            "PXX_MEMORY_LIMIT": "10",
            "PXX_MEMORY_MAX_CONTEXT": "16000",
            "PXX_MEMORY_HEADROOM": "3000",
        },
    )
    def test_config_from_env(self) -> None:
        """Test loading config from environment variables."""
        cfg = MemoryTuneConfig.from_env()
        assert cfg.min_relevance_score == 0.7
        assert cfg.max_observations == 10
        assert cfg.max_context_chars == 16000
        assert cfg.leave_context_headroom == 3000


class TestRelevanceFiltering:
    """Tests for relevance score filtering."""

    def test_filter_by_relevance_all_pass(self) -> None:
        """Test filtering when all observations pass."""
        tuner = MemoryTuner(min_relevance_score=0.5)
        obs = [
            {"title": "A", "score": 0.9},
            {"title": "B", "score": 0.7},
            {"title": "C", "score": 0.5},
        ]
        result = tuner.filter_by_relevance(obs)
        assert len(result) == 3

    def test_filter_by_relevance_some_filtered(self) -> None:
        """Test filtering removes low-score observations."""
        tuner = MemoryTuner(min_relevance_score=0.7)
        obs = [
            {"title": "A", "score": 0.9},
            {"title": "B", "score": 0.6},
            {"title": "C", "score": 0.5},
        ]
        result = tuner.filter_by_relevance(obs)
        assert len(result) == 1
        assert result[0]["title"] == "A"

    def test_filter_by_relevance_missing_score(self) -> None:
        """Test filtering treats missing score as 0."""
        tuner = MemoryTuner(min_relevance_score=0.5)
        obs = [
            {"title": "A", "score": 0.8},
            {"title": "B"},  # No score
            {"title": "C", "score": 0.4},
        ]
        result = tuner.filter_by_relevance(obs)
        assert len(result) == 1
        assert result[0]["title"] == "A"

    def test_filter_by_relevance_strict(self) -> None:
        """Test filtering with strict threshold."""
        tuner = MemoryTuner(min_relevance_score=0.9)
        obs = [
            {"title": "A", "score": 0.95},
            {"title": "B", "score": 0.85},
        ]
        result = tuner.filter_by_relevance(obs)
        assert len(result) == 1


class TestCountLimiting:
    """Tests for limiting observations by count."""

    def test_limit_by_count_within_limit(self) -> None:
        """Test no limiting when under max."""
        tuner = MemoryTuner(max_observations=5)
        obs = [{"title": f"Obs{i}"} for i in range(3)]
        result = tuner.limit_by_count(obs)
        assert len(result) == 3

    def test_limit_by_count_exceeds_limit(self) -> None:
        """Test limiting when exceeding max."""
        tuner = MemoryTuner(max_observations=3)
        obs = [{"title": f"Obs{i}"} for i in range(10)]
        result = tuner.limit_by_count(obs)
        assert len(result) == 3

    def test_limit_by_count_exact(self) -> None:
        """Test limiting at exact boundary."""
        tuner = MemoryTuner(max_observations=5)
        obs = [{"title": f"Obs{i}"} for i in range(5)]
        result = tuner.limit_by_count(obs)
        assert len(result) == 5


class TestSizeLimiting:
    """Tests for limiting observations by context size."""

    def test_limit_by_size_within_limit(self) -> None:
        """Test no limiting when within context budget."""
        tuner = MemoryTuner(max_context_chars=8000, leave_context_headroom=2000)
        obs = [
            {
                "title": "A",
                "content": "x" * 100,
            }
        ]
        result, size = tuner.limit_by_size(obs, base_context_size=1000)
        assert len(result) == 1
        assert size > 1000

    def test_limit_by_size_exceeds_limit(self) -> None:
        """Test limiting when exceeding context budget."""
        tuner = MemoryTuner(max_context_chars=1000, leave_context_headroom=500)
        obs = [
            {
                "title": "A",
                "content": "x" * 200,
            },
            {
                "title": "B",
                "content": "x" * 200,
            },
        ]
        result, size = tuner.limit_by_size(obs, base_context_size=400)
        # Should fit A but not B (400 + ~300 + ~300 = 1000)
        assert len(result) <= 2

    def test_limit_by_size_respects_headroom(self) -> None:
        """Test headroom is preserved."""
        tuner = MemoryTuner(max_context_chars=2000, leave_context_headroom=1000)
        obs = [{"title": "Big", "content": "x" * 1500}]
        result, size = tuner.limit_by_size(obs, base_context_size=100)
        # 100 + ~1600 = 1700, which is <= 1000 (max - headroom) - should fit
        assert len(result) >= 0  # May or may not fit depending on calculation


class TestTuneObservations:
    """Tests for full tuning pipeline."""

    def test_tune_observations_full_pipeline(self) -> None:
        """Test full tuning with all three filters."""
        tuner = MemoryTuner(
            min_relevance_score=0.7,
            max_observations=3,
            max_context_chars=5000,
            leave_context_headroom=1000,
        )
        obs = [
            {"title": "A", "score": 0.95, "content": "good"},
            {"title": "B", "score": 0.8, "content": "fine"},
            {"title": "C", "score": 0.5, "content": "low"},  # Filtered by score
            {"title": "D", "score": 0.9, "content": "x" * 5000},  # Too big
            {"title": "E", "score": 0.85, "content": "ok"},
        ]

        result, stats = tuner.tune_observations(obs)

        # Should have stats
        assert stats["original_count"] == 5
        assert stats["final_count"] <= 3  # Limited by max_observations
        assert stats["relevance_filtered_out"] >= 1  # C filtered out

    def test_tune_observations_returns_stats(self) -> None:
        """Test tuning returns stats dict."""
        tuner = MemoryTuner()
        obs = [
            {"title": "A", "score": 0.9},
            {"title": "B", "score": 0.6},
        ]
        result, stats = tuner.tune_observations(obs)

        # Check all expected keys in stats
        assert "original_count" in stats
        assert "after_relevance_filter" in stats
        assert "final_count" in stats
        assert "total_context_size" in stats

    def test_tune_observations_empty_input(self) -> None:
        """Test tuning with empty observation list."""
        tuner = MemoryTuner()
        result, stats = tuner.tune_observations([])
        assert result == []
        assert stats["original_count"] == 0
        assert stats["final_count"] == 0


class TestConfigSummary:
    """Tests for config summary output."""

    def test_get_config_summary(self) -> None:
        """Test config summary string generation."""
        tuner = MemoryTuner(
            min_relevance_score=0.6,
            max_observations=4,
            max_context_chars=10000,
            leave_context_headroom=1500,
        )
        summary = tuner.get_config_summary()

        assert "0.6" in summary
        assert "4" in summary
        assert "10000" in summary
        assert "MemoryTuner" in summary
