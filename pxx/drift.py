"""Cross-machine sync/drift detection for pxx (#006).

Detects if the local (Neo) and remote (Studio) pxx repositories have diverged
at the git HEAD level.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from pxx import _git


@dataclass(frozen=True)
class DriftResult:
    """The outcome of a cross-machine sync check.

    If ``error`` is present, ``is_synced`` is always False.
    """

    is_synced: bool
    local_sha: str
    remote_sha: str | None
    local_branch: str | None
    remote_branch: str | None
    error: str | None = None


DEFAULT_SSH_TARGET = "cwetzel@workstation"
DEFAULT_REMOTE_PATH = "/Users/you/ai/code_pro/pxx"
DRIFT_TIMEOUT_SECONDS = 5.0


def check_sync(
    ssh_target: str = DEFAULT_SSH_TARGET,
    remote_path: str = DEFAULT_REMOTE_PATH,
    timeout: float = DRIFT_TIMEOUT_SECONDS,
) -> DriftResult:
    """Compare local HEAD vs remote HEAD over SSH.

    Returns a DriftResult capturing both SHAs and sync status. Always probes
    against the pxx repo root for the local side, regardless of cwd.
    """
    # CF-006: probe pxx repo specifically, not the random cwd.
    local_sha = _get_pxx_local_head()
    if not local_sha:
        return DriftResult(
            is_synced=False,
            local_sha="unknown",
            remote_sha=None,
            local_branch=None,
            remote_branch=None,
            error="Could not determine local pxx HEAD.",
        )

    local_branch = _get_pxx_local_branch()
    remote_sha, remote_branch, error = _get_remote_state(ssh_target, remote_path, timeout)

    if error:
        return DriftResult(
            is_synced=False,
            local_sha=local_sha,
            remote_sha=None,
            local_branch=local_branch,
            remote_branch=None,
            error=error,
        )

    is_synced = local_sha == remote_sha
    return DriftResult(
        is_synced=is_synced,
        local_sha=local_sha,
        remote_sha=remote_sha,
        local_branch=local_branch,
        remote_branch=remote_branch,
    )


def _get_pxx_local_head() -> str | None:
    # CF-006: use repo_root to ensure we probe pxx regardless of cwd.
    root = _git.repo_root()
    if not root:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _get_pxx_local_branch() -> str | None:
    root = _git.repo_root()
    if not root:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _get_remote_state(
    target: str, path: str, timeout: float
) -> tuple[str | None, str | None, str | None]:
    """Probe remote HEAD and branch over SSH.

    Returns (sha, branch, error_message).
    """
    # Combine both probes into one SSH call to minimize latency.
    cmd = f"git -C {path} rev-parse HEAD --abbrev-ref HEAD"
    try:
        result = subprocess.run(
            ["ssh", target, cmd],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or f"exit code {result.returncode}"
            if "not a git repository" in err:
                return None, None, f"Remote path `{path}` is not a git repository."
            if "No such file or directory" in err:
                return None, None, f"Remote path `{path}` not found."
            # P2 finding: treat DNS/resolution failure as "unreachable" (skipped)
            # rather than "error" (✗)
            if "Could not resolve" in err or "Permission denied" in err:
                return None, None, f"Studio unreachable ({err})"
            return None, None, f"SSH command failed: {err}"

        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return None, None, "Remote git output malformed."

        return lines[0], lines[1], None

    except subprocess.TimeoutExpired:
        return None, None, f"Studio unreachable (SSH timeout after {timeout}s)."
    except FileNotFoundError:
        return None, None, "ssh binary not found in PATH."
    except Exception as e:
        return None, None, f"Unexpected error probing remote: {e}"


def print_report(result: DriftResult) -> None:
    """Print the drift report to stderr."""
    if result.error:
        # P2 finding: unreachable cases are informational (skipped)
        if "unreachable" in result.error or "timeout" in result.error:
            print(f"? {result.error}; skipping drift check", file=sys.stderr)
        else:
            print(f"✗ error checking sync: {result.error}", file=sys.stderr)
        return

    if result.is_synced:
        branch_part = f" ({result.local_branch})" if result.local_branch else ""
        print(
            f"✓ Neo and Studio in sync at {result.local_sha[:7]}{branch_part}",
            file=sys.stderr,
        )
        return

    print("✗ drift detected:", file=sys.stderr)
    print(f"    Neo:    {result.local_sha[:7]} {result.local_branch or ''}", file=sys.stderr)

    remote_sha = result.remote_sha[:7] if result.remote_sha else "???????"
    remote_branch = result.remote_branch or ""
    print(f"    Studio: {remote_sha} {remote_branch}", file=sys.stderr)

    print(file=sys.stderr)
    print("  From Neo: git deliver && rsync ...", file=sys.stderr)
    print("  Or from Studio: cd ~/ai/code_pro/pxx && git pull origin main", file=sys.stderr)
