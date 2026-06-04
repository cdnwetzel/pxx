"""Tool call capture from aider sessions.

Extracts observations from aider's tool calls (file edits, searches, etc.)
and stores them in agentmemory for future session context.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def get_git_diff_since(commit_sha: str) -> str:
    """Get git diff from a commit to HEAD."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{commit_sha}..HEAD", "--stat"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except Exception as e:
        logger.warning(f"Failed to get git diff: {e}")
        return ""


def extract_observations_from_diff(diff_stat: str, project_root: Path) -> list[str]:
    """Extract meaningful observations from git diff output.

    Args:
        diff_stat: Output from `git diff --stat`
        project_root: Root directory of the project

    Returns:
        List of observation strings to store
    """
    observations = []

    if not diff_stat.strip():
        return observations

    # Parse diff stat output: "path/to/file.py | 10 +---"
    lines = diff_stat.strip().split("\n")
    for line in lines:
        if "|" not in line:
            continue

        parts = line.split("|")
        if len(parts) != 2:
            continue

        filepath = parts[0].strip()
        changes = parts[1].strip()

        # Skip non-code files
        if filepath.endswith((".md", ".txt", ".yml", ".yaml")):
            continue

        # Extract +/- counts
        tokens = changes.split()
        if len(tokens) >= 1:
            # Parse different formats:
            # Format 1: "10 ++++---" (git diff --stat)
            # Format 2: "10 insertions(+), 5 deletions(-)"
            insertions = 0
            deletions = 0

            # Try to find numeric counts (insertions, deletions)
            if "insertions" in changes:
                # Format: "10 insertions(+), 5 deletions(-)"
                for token in tokens:
                    if token.isdigit():
                        if "insertions" in changes[: changes.index(token)]:
                            insertions = int(token)
                        elif "deletions" in changes[: changes.index(token)]:
                            deletions = int(token)
            else:
                # Format: "10 ++++---" (visual bar)
                # The first token should be the total count
                if tokens and tokens[0].isdigit():
                    total = int(tokens[0])
                    # Count the visual bar
                    plus_minus = "".join(t for t in tokens[1:] if set(t) <= {"+", "-"})
                    if plus_minus:
                        num_plus = plus_minus.count("+")
                        num_minus = plus_minus.count("-")
                        if num_plus + num_minus > 0:
                            # Proportionally distribute total across +/-
                            insertions = (total * num_plus) // (num_plus + num_minus)
                            deletions = total - insertions
                        else:
                            # No +/- found, assume all insertions
                            insertions = total
                    else:
                        # No +/- bar found, assume all insertions
                        insertions = total

            # Create observation
            action = "edited"
            if insertions > 0 and deletions == 0:
                action = "added code to"
            elif deletions > 0 and insertions == 0:
                action = "removed code from"

            obs = f"Aider {action} {filepath} ({insertions}+ {deletions}-)"
            observations.append(obs)

    return observations


def post_observations_to_memory(
    observations: list[str],
    memory_url: str = "http://127.0.0.1:3111",
    project: str = "default",
) -> int:
    """Post observations to agentmemory service.

    Args:
        observations: List of observation strings
        memory_url: Base URL of agentmemory service
        project: Project scope for observations

    Returns:
        Number of observations successfully posted
    """
    if not observations:
        return 0

    posted = 0
    for obs in observations:
        try:
            resp = requests.post(
                f"{memory_url}/observations",
                json={"project": project, "content": obs},
                timeout=5,
            )
            if resp.status_code == 200:
                posted += 1
                logger.debug(f"Posted observation: {obs}")
            else:
                logger.warning(f"Failed to post observation: {resp.status_code}")
        except requests.RequestException as e:
            logger.warning(f"Error posting observation: {e}")

    return posted


def capture_session_tools(
    commit_sha: str,
    project_root: Path,
    project: str = "default",
) -> int:
    """Capture tool calls from an aider session and store as observations.

    Args:
        commit_sha: Git commit SHA before aider started
        project_root: Root directory of the project
        project: Project scope for observations

    Returns:
        Number of observations captured and stored
    """
    try:
        # Get diff since the session started
        diff_stat = get_git_diff_since(commit_sha)
        if not diff_stat:
            logger.debug("No changes to capture")
            return 0

        # Extract observations from the diff
        observations = extract_observations_from_diff(diff_stat, project_root)
        if not observations:
            logger.debug("No tool calls to capture")
            return 0

        # Post to agentmemory
        posted = post_observations_to_memory(observations, project=project)
        logger.info(f"Captured {posted} tool observations from aider session")
        return posted

    except Exception as e:
        logger.error(f"Error capturing tool calls: {e}")
        return 0
