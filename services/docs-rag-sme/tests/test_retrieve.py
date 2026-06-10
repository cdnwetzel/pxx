"""T2 unit tests: the gate, late-injection ordering, and augment dispatch —
all with a fake retriever so no DB/embedder is needed."""

from __future__ import annotations

from docs_rag_sme.retrieve import (
    augment_messages,
    format_context,
    inject_context,
    is_augmentable,
    last_user_text,
)


def test_gate_fires_on_doc_relevant_text():
    assert is_augmentable("how do I use asyncio.TaskGroup?")
    assert is_augmentable("import httpx and stream a response")
    assert is_augmentable("is `functools.cache` deprecated?")
    assert is_augmentable("what changed in the latest asyncio API")


def test_gate_fires_on_bare_module_names():
    # Regression for the §6 A/B finding: bare module mentions (no dot) must
    # still trigger retrieval.
    assert is_augmentable("how does sqlite3 row_factory work")
    assert is_augmentable("which contextlib decorator makes an async context manager")
    assert is_augmentable("batching with itertools in python")


def test_gate_skips_plain_chatter():
    assert not is_augmentable("hello")
    assert not is_augmentable("thanks, that works")
    assert not is_augmentable("")
    # Common English words that happen to be stdlib modules must NOT fire.
    assert not is_augmentable("what time is the meeting")
    assert not is_augmentable("can you re-run that for me")


def test_last_user_text_handles_parts_form():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "use asyncio.gather"}]},
    ]
    assert last_user_text(msgs) == "use asyncio.gather"


def test_inject_is_late_preserving_prefix():
    msgs = [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "system", "content": "REPO MAP"},
        {"role": "user", "content": "older turn"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "use asyncio.gather"},
    ]
    out = inject_context(msgs, "CONTEXT")
    # The cached prefix (first 2 system msgs) is untouched.
    assert out[0]["content"] == "SYSTEM PROMPT"
    assert out[1]["content"] == "REPO MAP"
    # Context is injected immediately before the final user turn.
    ctx_idx = next(i for i, m in enumerate(out) if m["content"] == "CONTEXT")
    assert out[ctx_idx + 1]["content"] == "use asyncio.gather"
    assert out[ctx_idx]["role"] == "system"


class _FakeRetriever:
    def __init__(self, hits):
        self._hits = hits

    def retrieve(self, query, python_version=None):
        return self._hits


def test_augment_injects_when_gated_in():
    hits = [{"title": "asyncio.gather", "body": "run awaitables", "source_url": "u", "python_version": "3.12"}]
    msgs = [{"role": "user", "content": "how to use asyncio.gather"}]
    out, n = augment_messages(msgs, _FakeRetriever(hits))
    assert n == 1
    assert any("asyncio.gather" in m["content"] for m in out if m["role"] == "system")


def test_augment_noop_when_gated_out():
    msgs = [{"role": "user", "content": "hi there"}]
    out, n = augment_messages(msgs, _FakeRetriever([{"title": "x", "body": "y", "source_url": "u"}]))
    assert n == 0
    assert out == msgs


def test_format_context_includes_version_and_source():
    block = format_context(
        [{"title": "asyncio.gather", "body": "run awaitables", "source_url": "https://d/p", "python_version": "3.12"}]
    )
    assert "asyncio.gather" in block
    assert "3.12" in block
    assert "https://d/p" in block
