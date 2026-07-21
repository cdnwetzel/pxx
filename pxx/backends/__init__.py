"""Backend registry: mock, native, aider + the get_backend factory."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from ..errors import ConfigError
from .aider import AiderBackend
from .base import AgentBackend, BackendCapabilities, SessionContext
from .mock import MockBackend
from .native import NativeBackend
from .replay import ReplayBackend

if TYPE_CHECKING:
    from ..config import Settings

__all__ = [
    "AgentBackend",
    "AiderBackend",
    "BackendCapabilities",
    "MockBackend",
    "NativeBackend",
    "ReplayBackend",
    "SessionContext",
    "get_backend",
]


def get_backend(name: str | None, settings: Settings) -> AgentBackend:
    """Resolve a backend by name. ``None``/``'auto'`` picks aider when the
    binary is on PATH, else the native loop."""
    if name in (None, "auto"):
        name = "aider" if shutil.which("aider") else "native"
    if name == "native":
        return NativeBackend()
    if name == "aider":
        return AiderBackend()
    if name == "mock":
        return MockBackend()
    raise ConfigError(f"unknown backend: {name!r}")
