"""Capsule builder — a fresh, hard-capped context assembled per action (no transcript replay).

Each action gets a capsule built from scratch:

    fixed agent kernel + task contract (from the ledger) + the EXACT target source verbatim
    + a compact diagnostic (last failure summary + artifact ref) + this phase's tool list
    + dependency pages + recent history

A **hard INPUT-token cap** is enforced with the injected token counter. Priority (never
evicted): kernel, contract, target source, tools, diagnostic — the *floor*. When over budget
the builder evicts in order **history first, then dependency pages** (oldest-touched first),
and **never** the target source.

If the floor alone exceeds the cap, the builder raises :class:`CapsuleOverflow` — the runtime
turns that into a legal, deterministic preflight ``BLOCKED(target_source_exceeds_capsule)``
rather than ever evicting or summarizing the target source. (v1: windowed source pages.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .pages import Page

TokenCounter = Callable[[str], int]


def approx_token_counter(text: str) -> int:
    """A deterministic, tokenizer-free counter for the mechanism tests (~4 chars/token, min 1
    per non-empty string). The LIVE Neo receipt injects the model's REAL tokenizer instead —
    the cap only means something measured against the tokenizer that will actually serve."""
    return max(1, (len(text) + 3) // 4) if text else 0


@dataclass(frozen=True)
class _Section:
    label: str
    text: str
    seq: int = 0  # last-touched action seq; lower == older == evicted first within a tier


@dataclass(frozen=True)
class Capsule:
    """An assembled capsule: the prompt string, its measured token count, and provenance."""

    prompt: str
    input_tokens: int
    cap: int
    included: list[str]
    evicted: list[str]

    @property
    def under_cap(self) -> bool:
        return self.input_tokens <= self.cap


class CapsuleOverflow(Exception):
    """Kernel + contract + target source + tools + diagnostic alone exceed the cap. The target
    source is never dropped or summarized to fit; the host returns a preflight BLOCKED."""


class CapsuleBuilder:
    def __init__(
        self, cap_tokens: int = 5500, count_tokens: TokenCounter = approx_token_counter
    ) -> None:
        self.cap = cap_tokens
        self.count = count_tokens

    def build(
        self,
        *,
        kernel: str,
        contract: str,
        target: Page,
        tools: str,
        diagnostic: str = "",
        dependency_pages: list[Page] | None = None,
        history: list[str] | None = None,
    ) -> Capsule:
        # The floor: never evicted. If it alone busts the cap, that's a preflight BLOCKED.
        floor = [
            _Section("kernel", kernel),
            _Section("contract", contract),
            _Section(f"target:{target.path}", self._render_source(target)),
            _Section("tools", tools),
        ]
        if diagnostic:
            floor.append(_Section("diagnostic", diagnostic))
        # Admission is measured on the RENDERED block (header + separator), which slightly
        # over-estimates the joined prompt (token counts aren't perfectly additive) — so the
        # ACTUAL assembled prompt is always <= the admitted budget <= cap. under_cap can't lie.
        floor_cost = sum(self._cost(s) for s in floor)
        if floor_cost > self.cap:
            raise CapsuleOverflow(
                f"floor {floor_cost} > cap {self.cap} (target {target.path} cannot fit)"
            )

        # Evictable tiers, highest priority first: dependency pages, then history. Within a
        # tier, oldest-touched (lowest seq) is evicted first. We ADD in priority order until
        # the next item would bust the cap; everything not added is 'evicted'.
        dep_sections = [
            _Section(f"dep:{p.path}", self._render_source(p), seq=i)
            for i, p in enumerate(dependency_pages or [])
        ]
        hist_sections = [_Section(f"history[{i}]", h, seq=i) for i, h in enumerate(history or [])]
        # newest-first admission (drop oldest first == the low-seq tail is evicted under pressure)
        candidates = sorted(dep_sections, key=lambda s: -s.seq) + sorted(
            hist_sections, key=lambda s: -s.seq
        )

        included = list(floor)
        used = floor_cost
        evicted: list[str] = []
        for sec in candidates:
            c = self._cost(sec)
            if used + c <= self.cap:
                included.append(sec)
                used += c
            else:
                evicted.append(sec.label)

        prompt = "\n\n".join(f"### {s.label}\n{s.text}" for s in included)
        total = self.count(prompt)  # <= used <= cap, so under_cap is guaranteed
        return Capsule(
            prompt=prompt,
            input_tokens=total,
            cap=self.cap,
            included=[s.label for s in included],
            evicted=evicted,
        )

    def _cost(self, sec: _Section) -> int:
        # the section's cost as it appears in the prompt: its rendered block plus one separator.
        return self.count(f"### {sec.label}\n{sec.text}") + self.count("\n\n")

    @staticmethod
    def _render_source(page: Page) -> str:
        # the target/dependency source is rendered VERBATIM with its authoritative sha so the
        # model's PATCH can carry the exact expected_sha it saw.
        return f"# path: {page.path}\n# sha256: {page.sha}\n{page.text}"


@dataclass
class Diagnostic:
    """A compact last-failure diagnostic: a one-line summary + an artifact ref to INSPECT."""

    summary: str = ""
    ref_id: str = ""
    extras: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.summary and not self.ref_id:
            return ""
        lines = [f"last failure: {self.summary}"] if self.summary else []
        if self.ref_id:
            lines.append(f"artifact: {self.ref_id} (INSPECT for full log)")
        lines.extend(self.extras)
        return "\n".join(lines)
