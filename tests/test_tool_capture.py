"""Tests for tool call capture from aider sessions."""

from pathlib import Path

from pxx.tool_capture import extract_observations_from_diff


def test_extract_observations_from_simple_edit():
    """Test extracting observation from a single file edit."""
    diff_stat = "pxx/cli.py | 5 +++--"
    obs = extract_observations_from_diff(diff_stat, Path.cwd())
    assert len(obs) == 1
    assert "cli.py" in obs[0]["content"]
    assert "edited" in obs[0]["content"]
    # Format "5 +++--" has 3 + and 2 -, proportionally maps to 3+ 2- in 5 lines
    assert "3+ 2-" in obs[0]["content"]


def test_extract_observations_additions_only():
    """Test observation when only lines are added."""
    diff_stat = "services/9router/main.py | 10 +++++"
    obs = extract_observations_from_diff(diff_stat, Path.cwd())
    assert len(obs) == 1
    assert "added code to" in obs[0]["content"]
    assert "10+ 0-" in obs[0]["content"]


def test_extract_observations_deletions_only():
    """Test observation when only lines are removed."""
    diff_stat = "pxx/memory.py | 3 ---"
    obs = extract_observations_from_diff(diff_stat, Path.cwd())
    assert len(obs) == 1
    assert "removed code from" in obs[0]["content"]
    assert "0+ 3-" in obs[0]["content"]


def test_skip_docs_and_config():
    """Test that markdown and config files are skipped."""
    diff_stat = """pxx/cli.py | 5 +++--
    config/model-settings.yml | 2 +-
    README.md | 10 ++++++----
    docs/guide.txt | 3 +--"""
    obs = extract_observations_from_diff(diff_stat, Path.cwd())
    assert len(obs) == 1
    assert "cli.py" in obs[0]["content"]


def test_multiple_code_files():
    """Test observation extraction from multiple code file changes."""
    diff_stat = """pxx/cli.py | 5 +++--
    pxx/router.py | 8 +++++---
    services/agentmemory/main.py | 2 +-"""
    obs = extract_observations_from_diff(diff_stat, Path.cwd())
    assert len(obs) == 3
    assert any("cli.py" in o["content"] for o in obs)
    assert any("router.py" in o["content"] for o in obs)
    assert any("agentmemory" in o["content"] for o in obs)


def test_empty_diff():
    """Test handling of empty diff."""
    obs = extract_observations_from_diff("", Path.cwd())
    assert len(obs) == 0


def test_diff_with_statistics_line():
    """Test that the statistics line at the end is skipped."""
    diff_stat = """pxx/cli.py | 5 +++--
    pxx/router.py | 8 +++++---
    2 files changed, 10 insertions(+), 3 deletions(-)"""
    obs = extract_observations_from_diff(diff_stat, Path.cwd())
    assert len(obs) == 2
    # Should not have observations from the stats line
    assert not any("changed" in o for o in obs)


def test_unified_diff_attaches_function_metadata():
    """A matching unified diff enriches the observation with function metadata.

    This path was previously unreachable in tests because the function shelled
    out to the live working tree (empty in CI) instead of taking the diff in.
    """
    diff_stat = "pxx/cli.py | 2 ++"
    unified_diff = (
        "diff --git a/pxx/cli.py b/pxx/cli.py\n"
        "--- a/pxx/cli.py\n"
        "+++ b/pxx/cli.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def new_handler(arg):\n"
        "+    return arg\n"
    )
    obs = extract_observations_from_diff(diff_stat, Path.cwd(), unified_diff)
    assert len(obs) == 1
    functions = obs[0]["metadata"].get("functions", [])
    assert any(f["name"] == "new_handler" for f in functions)


def test_filepath_substring_in_diff_body_does_not_crash():
    """A filepath appearing only in the diff *body* must not trip the split.

    Regression for the IndexError when the code split on a bare substring match
    (`filepath in unified_diff`) instead of the exact `diff --git a/<path>`
    header — exactly what a dirty working tree used to trigger.
    """
    diff_stat = "pxx/cli.py | 2 +-"
    unified_diff = (
        "diff --git a/pxx/other.py b/pxx/other.py\n"
        "--- a/pxx/other.py\n"
        "+++ b/pxx/other.py\n"
        "@@ -1 +1 @@\n"
        "-# references pxx/cli.py in a comment\n"
        "+# still references pxx/cli.py\n"
    )
    obs = extract_observations_from_diff(diff_stat, Path.cwd(), unified_diff)
    assert len(obs) == 1
    # No real header for cli.py, so no function/class metadata is attached.
    assert "functions" not in obs[0]["metadata"]
