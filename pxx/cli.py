"""pxx entry point: detect endpoint, pick model, exec aider."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from pxx.endpoints import Endpoint, detect_endpoint

PKG_DIR = Path(__file__).parent
REPO_ROOT = PKG_DIR.parent
SYSTEM_PROMPT = PKG_DIR / "prompts" / "system.md"
AIDER_CONF = REPO_ROOT / "config" / "aider.conf.yml"
MODEL_SETTINGS = REPO_ROOT / "config" / "model-settings.yml"

STUDIO_DEFAULT = "ollama_chat/devstral:24b"
NEO_DEFAULT = "ollama_chat/qwen3:4b"


def model_for(endpoint: Endpoint) -> str:
    # Only the "neo" endpoint name gets NEO_DEFAULT; every other name
    # (including PXX_OLLAMA_BASE "override") is assumed to be a Studio-class
    # machine and gets STUDIO_DEFAULT. Override the assumption with PXX_MODEL.
    override = os.environ.get("PXX_MODEL")
    if override:
        return override
    return NEO_DEFAULT if endpoint.name == "neo" else STUDIO_DEFAULT


def _find_aider() -> str:
    """Find the aider binary — prefer the one in our own venv."""
    same_venv = Path(sys.executable).parent / "aider"
    if same_venv.exists():
        return str(same_venv)
    found = shutil.which("aider")
    if found:
        return found
    print("pxx: aider not found. Reinstall: uv tool install --editable . --python 3.12", file=sys.stderr)
    sys.exit(1)


def _in_git_repo() -> bool:
    """True if cwd is inside a git work tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, check=False, timeout=2,
        )
        return result.returncode == 0 and result.stdout.strip() == b"true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def main() -> None:
    try:
        endpoint = detect_endpoint()
    except RuntimeError as e:
        print(f"pxx: {e}", file=sys.stderr)
        sys.exit(1)

    os.environ["OLLAMA_API_BASE"] = endpoint.url
    model = model_for(endpoint)
    aider_bin = _find_aider()

    print(f"pxx: endpoint={endpoint.name} ({endpoint.url})  model={model}", file=sys.stderr)

    args = [
        aider_bin,
        "--model", model,
        "--read", str(SYSTEM_PROMPT),
        "--config", str(AIDER_CONF),
        "--model-settings-file", str(MODEL_SETTINGS),
    ]
    if not _in_git_repo():
        print("pxx: no git repo here — auto-commits disabled. Run `git init` to enable.", file=sys.stderr)
        args.append("--no-git")
    args.extend(sys.argv[1:])
    os.execv(aider_bin, args)


if __name__ == "__main__":
    main()
