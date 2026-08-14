"""Context Paging Runtime — v0 prototype (NOT shipped in the pxx wheel).

Virtual memory for small-context models: a 4B/8K-window local model completes a real repo
task by assembling a fresh, hard-capped **capsule** per action — no transcript replay — with
host-run verification, earning a receipt. Reference: Camelid's Context Paging Runtime
(timtoole02, 2026-08); this is the pxx-native, receipted evaluation of that idea.

Design contract: ``docs/context-paging-prototype.md`` in the repo root.

This package lives OUTSIDE the packaged ``pxx`` namespace on purpose: it is a v0 prototype,
and the build-native-vs-compose-on-Camelid integration decision is deliberately deferred. It
is NOT part of the shipped wheel (see ``[tool.setuptools] packages`` in pyproject.toml). Its
mechanism is proven by the deterministic negative-control suite in
``tests/test_context_paging.py``; the live 8 GB Neo receipt is earned with ``run_neo.py``.
"""

from __future__ import annotations

from .actions import Action, ActionError, parse_action
from .capsule import Capsule, CapsuleBuilder, CapsuleOverflow
from .executor import Executor
from .ledger import Ledger
from .pages import PageStore, page_hash
from .receipt import Receipt
from .runtime import Runtime, Terminal

__all__ = [
    "Action",
    "ActionError",
    "Capsule",
    "CapsuleBuilder",
    "CapsuleOverflow",
    "Executor",
    "Ledger",
    "PageStore",
    "Receipt",
    "Runtime",
    "Terminal",
    "page_hash",
    "parse_action",
]
