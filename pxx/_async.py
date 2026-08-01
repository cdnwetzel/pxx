"""Small async helpers shared across planes."""

from __future__ import annotations

import inspect
from typing import Any


async def await_if_needed(value: Any) -> Any:
    """Await ``value`` when it is awaitable, else return it unchanged.

    Lets a call site accept either a coroutine (the real async
    :class:`~pxx.memory.store.MemoryStore`, whose ``add``/``search`` are
    ``async def``) or a plain value (the synchronous test doubles) without
    special-casing each one. Guards the "coroutine was never awaited" class of
    bug: a store method that returns a coroutine is always awaited here.
    """
    if inspect.isawaitable(value):
        return await value
    return value
