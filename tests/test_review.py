"""Tests for pxx.review: parsing, fail-closed policy, NativeReviewer (mocked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from pxx.config import ModelRef
from pxx.review import (
    DEFAULT_REVIEW_PROMPT,
    NativeReviewer,
    ReviewMode,
    ReviewUnavailable,
    Verdict,
    build_healing_prompt,
    parse_review,
    review_changes,
)


class ScriptedReviewer:
    """Protocol-compatible reviewer returning scripted responses."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def review(self, diff: str, task: str) -> str:
        await asyncio.sleep(0)
        self.calls.append((diff, task))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- parse_review ---------------------------------------------------------


def test_parse_approve() -> None:
    verdict, findings = parse_review("Some reasoning.\nVERDICT: APPROVE\n")
    assert verdict is Verdict.APPROVE
    assert findings == []


def test_parse_verdict_case_insensitive() -> None:
    verdict, _ = parse_review("verdict: approve")
    assert verdict is Verdict.APPROVE


def test_parse_revise_with_findings() -> None:
    text = (
        "VERDICT: REVISE\n"
        "F-001 [high] src/main.py:42 missing null check\n"
        "F-002 [low] README.md:7 update the docs\n"
        "F-003 medium src/util.py - handles `[]` input\n"
    )
    verdict, findings = parse_review(text)
    assert verdict is Verdict.REVISE
    assert [f.id for f in findings] == ["F-001", "F-002", "F-003"]
    assert findings[0].severity == "high"
    assert findings[0].file == "src/main.py"
    assert findings[0].line == 42
    assert findings[0].message == "missing null check"
    assert findings[1].line == 7
    assert findings[1].message == "update the docs"
    assert findings[2].severity == "medium"
    assert findings[2].message == "handles `[]` input"


def test_parse_unknown_verdict_is_no_review() -> None:
    verdict, _ = parse_review("VERDICT: MAYBE\nF-001 [low] a.py:1 hmm")
    assert verdict is Verdict.NO_REVIEW


def test_parse_missing_verdict_is_no_review() -> None:
    verdict, findings = parse_review("total nonsense with no structure at all")
    assert verdict is Verdict.NO_REVIEW
    assert findings == []


def test_parse_empty_text() -> None:
    assert parse_review("") == (Verdict.NO_REVIEW, [])


# --- review_changes policy --------------------------------------------------


def test_review_changes_blocking_revise_blocks() -> None:
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [high] a.py:1 boom"])
    result = asyncio.run(review_changes("diff", "task", reviewer, ReviewMode.BLOCKING))
    assert result.blocked
    assert result.verdict is Verdict.REVISE
    assert result.mode is ReviewMode.BLOCKING
    assert len(result.findings) == 1


def test_review_changes_advisory_never_blocks() -> None:
    reviewer = ScriptedReviewer(["VERDICT: REVISE\nF-001 [high] a.py:1 boom"])
    result = asyncio.run(review_changes("diff", "task", reviewer, ReviewMode.ADVISORY))
    assert not result.blocked
    assert result.verdict is Verdict.REVISE


def test_review_changes_approve_never_blocks() -> None:
    reviewer = ScriptedReviewer(["VERDICT: APPROVE"])
    result = asyncio.run(review_changes("diff", "task", reviewer, ReviewMode.BLOCKING))
    assert not result.blocked
    assert result.verdict is Verdict.APPROVE


def test_review_changes_unavailable_fail_closed() -> None:
    reviewer = ScriptedReviewer([ReviewUnavailable("endpoint down")])
    result = asyncio.run(review_changes("d", "t", reviewer, ReviewMode.BLOCKING))
    assert result.blocked
    assert result.verdict is Verdict.NO_REVIEW
    assert result.findings == ()


def test_review_changes_unavailable_advisory_passes() -> None:
    reviewer = ScriptedReviewer([ReviewUnavailable("endpoint down")])
    result = asyncio.run(review_changes("d", "t", reviewer, ReviewMode.ADVISORY))
    assert not result.blocked
    assert result.verdict is Verdict.NO_REVIEW


def test_review_changes_garbage_blocks_in_blocking_mode() -> None:
    reviewer = ScriptedReviewer(["no verdict here"])
    result = asyncio.run(review_changes("d", "t", reviewer, ReviewMode.BLOCKING))
    assert result.verdict is Verdict.NO_REVIEW
    assert result.blocked


# --- build_healing_prompt ---------------------------------------------------


def test_build_healing_prompt() -> None:
    _, findings = parse_review(
        "VERDICT: REVISE\nF-001 [high] src/a.py:7 off by one\nF-002 [low] b.py:3 add docstring"
    )
    prompt = build_healing_prompt("implement the widget", findings, 3)
    assert "implement the widget" in prompt
    assert "round 3" in prompt
    assert "F-001" in prompt and "src/a.py:7" in prompt and "off by one" in prompt
    assert "F-002" in prompt and "b.py" in prompt


# --- NativeReviewer (no network: httpx.MockTransport) ------------------------


def _model() -> ModelRef:
    return ModelRef(
        provider="openai-compatible",
        model="reviewer-x",
        base_url="http://testserver",
        api_key="secret",
    )


def _missing_prompt(tmp_path: Path) -> Path:
    return tmp_path / "no-such-prompt.md"


def test_native_reviewer_success(tmp_path: Path) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "VERDICT: APPROVE"}}]})

    reviewer = NativeReviewer(
        _model(), _missing_prompt(tmp_path), transport=httpx.MockTransport(handler)
    )
    text = asyncio.run(reviewer.review("the-diff", "the-task"))
    assert text == "VERDICT: APPROVE"
    assert seen["url"] == "http://testserver/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    messages = seen["body"]["messages"]
    assert seen["body"]["model"] == "reviewer-x"
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == DEFAULT_REVIEW_PROMPT  # fallback when file missing
    assert "the-task" in messages[1]["content"]
    assert "the-diff" in messages[1]["content"]


def test_native_reviewer_uses_prompt_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "review.md"
    prompt_file.write_text("CUSTOM REVIEW PROMPT")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    reviewer = NativeReviewer(_model(), prompt_file, transport=httpx.MockTransport(handler))
    asyncio.run(reviewer.review("d", "t"))
    assert seen["body"]["messages"][0]["content"] == "CUSTOM REVIEW PROMPT"


def test_native_reviewer_http_error_raises_unavailable(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    reviewer = NativeReviewer(_model(), _missing_prompt(tmp_path), transport=transport)
    with pytest.raises(ReviewUnavailable):
        asyncio.run(reviewer.review("d", "t"))


def test_native_reviewer_network_error_raises_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reviewer = NativeReviewer(
        _model(), _missing_prompt(tmp_path), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ReviewUnavailable):
        asyncio.run(reviewer.review("d", "t"))


def test_native_reviewer_malformed_response_raises_unavailable(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"nope": 1}))
    reviewer = NativeReviewer(_model(), _missing_prompt(tmp_path), transport=transport)
    with pytest.raises(ReviewUnavailable):
        asyncio.run(reviewer.review("d", "t"))


# --- B1.3: evidence-linked findings (generic findings are rejected) -------------


def test_generic_finding_dropped_evidenced_kept() -> None:
    verdict, findings = parse_review(
        "VERDICT: REVISE\n"
        "F-001 [high] src/main.py missing null check\n"  # no line, no anchor
        "F-002 [low] src/util.py:9 rename `tmp`\n"
    )
    assert verdict is Verdict.REVISE
    assert [f.id for f in findings] == ["F-002"]


def test_all_generic_findings_yield_no_review_not_approve() -> None:
    verdict, findings = parse_review(
        "VERDICT: REVISE\n"
        "F-001 [high] src/main.py improve error handling\n"
        "F-002 [low] src/util.py looks wrong\n"
    )
    assert findings == []
    assert verdict is Verdict.NO_REVIEW  # never a vacuous APPROVE


def test_evidence_anchors_accepted() -> None:
    verdict, findings = parse_review(
        "VERDICT: REVISE\n"
        "F-001 [high] src/main.py:42 missing null check\n"  # file+line
        "F-002 [low] src/util.py fails on `total(0)`\n"  # backticked input
        "F-003 [medium] docs note see pxx/loop.py for context\n"  # path in message
    )
    assert verdict is Verdict.REVISE
    assert len(findings) == 3


def test_generic_only_review_blocks_without_vacuous_pass() -> None:
    """A generic-only REVISE must not become a silent approve, and must not
    force healing cycles: it degrades to NO_REVIEW (blocked in BLOCKING)."""

    class GenericReviewer:
        async def review(self, diff: str, task: str) -> str:
            return "VERDICT: REVISE\nF-001 [high] a.py improve things"

    import asyncio

    result = asyncio.run(review_changes("diff", "task", GenericReviewer(), ReviewMode.BLOCKING))
    assert result.verdict is Verdict.NO_REVIEW
    assert result.findings == ()
    assert result.blocked is True
