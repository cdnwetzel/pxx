"""Tool call capture from aider sessions.

Extracts observations from aider's tool calls (file edits, searches, etc.)
and stores them in agentmemory for future session context.
"""

from __future__ import annotations

import logging
import re
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


def get_unified_diff_since(commit_sha: str) -> str:
    """Get `git diff <commit_sha>..HEAD --unified=0` (zero-context unified diff)."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{commit_sha}..HEAD", "--unified=0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception as e:
        logger.warning(f"Failed to get unified git diff: {e}")
        return ""


def extract_observations_from_diff(
    diff_stat: str, project_root: Path, unified_diff: str = ""
) -> list[dict]:
    """Extract meaningful observations from git diff output.

    Args:
        diff_stat: Output from `git diff <range> --stat`
        project_root: Root directory of the project
        unified_diff: Matching `git diff <range> --unified=0` for the same range,
            used to attach function/class metadata. Empty skips that enrichment.
            Passed in (rather than shelled out internally) so this function is
            pure: its result depends only on its arguments, not the live working
            tree, and the unified diff stays consistent with ``diff_stat``.

    Returns:
        List of observation dictionaries with metadata
    """
    observations: list[dict] = []

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
            insertions = 0
            deletions = 0

            if "insertions" in changes:
                for token in tokens:
                    if token.isdigit():
                        if "insertions" in changes[: changes.index(token)]:
                            insertions = int(token)
                        elif "deletions" in changes[: changes.index(token)]:
                            deletions = int(token)
            else:
                if tokens and tokens[0].isdigit():
                    total = int(tokens[0])
                    plus_minus = "".join(t for t in tokens[1:] if set(t) <= {"+", "-"})
                    if plus_minus:
                        num_plus = plus_minus.count("+")
                        num_minus = plus_minus.count("-")
                        if num_plus + num_minus > 0:
                            insertions = (total * num_plus) // (num_plus + num_minus)
                            deletions = total - insertions
                        else:
                            insertions = total
                    else:
                        insertions = total

        # Create base observation
        action = "edited"
        if insertions > 0 and deletions == 0:
            action = "added code to"
        elif deletions > 0 and insertions == 0:
            action = "removed code from"

        obs: dict[str, any] = {
            "content": f"Aider {action} {filepath} ({insertions}+ {deletions}-)",
            "metadata": {
                "files_changed": [
                    {
                        "path": filepath,
                        "lines_added": insertions,
                        "lines_removed": deletions,
                    }
                ]
            },
        }

        # Parse unified diff for function/class changes. Match the exact
        # `diff --git a/<path>` header (not a bare substring) so a filepath that
        # merely appears inside the diff body can't trip an IndexError on split.
        header = f"diff --git a/{filepath}"
        if header in unified_diff:
            file_diff = unified_diff.split(header, 1)[1].split("diff --git", 1)[0]
            obs["metadata"].update(parse_code_changes(file_diff, filepath))

        # For backward compatibility, also store just the content
        observations.append(
            {"content": obs["content"], "metadata": obs.get("metadata", {})}
        )

    return observations


def parse_code_changes(diff_text: str, filepath: str) -> dict:
    """Parse unified diff to extract function/class changes."""
    metadata = {"functions": [], "classes": []}

    # Regex patterns for Python code
    func_pattern = re.compile(r"^\+.*?def\s+(\w+)\(.*?\)")
    class_pattern = re.compile(r"^\+.*?class\s+(\w+)\s*\(.*?\)")

    lines = diff_text.split("\n")
    current_function = None
    current_class = None

    for i, line in enumerate(lines):
        func_match = func_pattern.match(line)
        class_match = class_pattern.match(line)

        if func_match and not current_class:
            # Found a function definition at the root level
            func_name = func_match.group(1)
            current_function = {
                "name": func_name,
                "line_range": (i + 1, i + 1),
                "change": "add",
            }
            metadata["functions"].append(current_function)

        elif class_match:
            # Found a class definition
            class_name = class_match.group(1)
            if not current_class:
                # New class at root level or nested in another class
                current_class = {
                    "name": class_name,
                    "line_range": (i + 1, i + 1),
                    "change": "add",
                }
                metadata["classes"].append(current_class)

        elif line.startswith("+"):
            # Inside a function or class
            if current_function:
                current_function["line_range"] = (
                    current_function["line_range"][0],
                    i + 1,
                )
            elif current_class:
                current_class["line_range"] = (current_class["line_range"][0], i + 1)

    return metadata


def extract_test_names(output: str) -> dict:
    """Extract test names from test output."""
    tests = {"passed": [], "failed": [], "regressions": []}

    # Simple regex patterns for common test frameworks
    passed_patterns = [
        re.compile(r"passed\s+(\w+)"),
        re.compile(r"\.\s*(\w+)\s+\(.*?\)"),
    ]
    failed_patterns = [
        re.compile(r"failed\s+(\w+)"),
        re.compile(r"F\s*(\w+)\s+\(.*?\)"),
    ]

    for line in output.split("\n"):
        for pattern in passed_patterns:
            match = pattern.search(line)
            if match:
                tests["passed"].append(match.group(1))
        for pattern in failed_patterns:
            match = pattern.search(line)
            if match:
                tests["failed"].append(match.group(1))

    return tests


def post_observations_to_memory(
    observations: list[dict],
    memory_url: str = "http://127.0.0.1:3111",
    project: str = "default",
) -> int:
    """Post observations to agentmemory service.

    Args:
        observations: List of observation dictionaries with metadata
        memory_url: Base URL of agentmemory service
        project: Project scope for observations

    Returns:
        Number of observations successfully posted
    """
    if not observations:
        return 0

    posted = 0
    for obs_data in observations:
        try:
            resp = requests.post(
                f"{memory_url}/observations",
                json={
                    "project": project,
                    "content": obs_data["content"],
                    "metadata": obs_data.get("metadata"),
                },
                timeout=5,
            )
            if resp.status_code == 200:
                posted += 1
                logger.debug(f"Posted observation: {obs_data['content']}")
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
        # Get diff since the session started. Both the --stat summary and the
        # zero-context unified diff come from the same <sha>..HEAD range so the
        # function/class metadata stays consistent with the file stats.
        diff_stat = get_git_diff_since(commit_sha)
        if not diff_stat:
            logger.debug("No changes to capture")
            return 0

        unified_diff = get_unified_diff_since(commit_sha)

        # Extract observations from the diff
        observations = extract_observations_from_diff(
            diff_stat, project_root, unified_diff
        )
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
