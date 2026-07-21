"""pxx package init: version + machine-local env-file loading.

``~/.config/pxx/env`` holds machine-specific settings (your own LLM endpoints,
model ids, drift targets) as KEY=VALUE lines, keeping them out of the repo and
independent of shell profiles (which non-interactive shells don't source).
Loaded here — before any submodule reads its env-derived defaults at import
time. Real environment variables always win over the file.
"""

import os
from pathlib import Path

__version__ = "1.3.3.post1"

_ENV_FILE = Path.home() / ".config" / "pxx" / "env"


def _load_env_file(path: Path = _ENV_FILE) -> None:
    """Apply KEY=VALUE lines from the machine-local config as env defaults."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()
