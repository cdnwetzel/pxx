#!/usr/bin/env python3
"""Earn the live 8 GB Neo receipt: drive a REAL 4B/8K local model through the paging runtime.

The deterministic mechanism proof lives in ``tests/test_context_paging.py`` (no hardware). This
script is the LIVE arm: it builds a tiny scratch repo with a real failing test, points the
runtime at a local OpenAI-compatible endpoint (Ollama by default), and lets the model page +
patch + verify its way to COMPLETE — writing a receipt to disk.

Usage (on Neo, with `ollama serve` + `ollama pull qwen3:4b`):

    python -m prototypes.context_paging.run_neo \\
        --base-url http://localhost:11434/v1 --model qwen3:4b --cap 5500

Honest tokenizer note: the capsule cap is only meaningful against the tokenizer that actually
serves. Pass ``--hf-tokenizer <hf-repo>`` to count with the model's real tokenizer (needs
``transformers``); otherwise a documented char/4 approximation is used and recorded in the
receipt's ``tokenizer_id`` so the receipt never overstates its fidelity.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from .capsule import approx_token_counter
from .model import OpenAICompatibleModel
from .runtime import new_task_runtime

# A small, real single-file task: a buggy function whose failing test must pass.
_BUG_SRC = '''\
def rated_total(items, tax_rate):
    """Sum item prices and apply a tax rate. BUG: tax is subtracted, not added."""
    subtotal = sum(items)
    return subtotal - subtotal * tax_rate
'''

_TEST_SRC = """\
from bug import rated_total


def test_rated_total_applies_tax():
    assert rated_total([100.0], 0.1) == 110.0
"""


def _scratch_repo(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "bug.py").write_text(_BUG_SRC)
    (dst / "test_bug.py").write_text(_TEST_SRC)


def _token_counter(hf_tokenizer: str | None):
    """Return (counter, tokenizer_id). Prefer the model's REAL tokenizer when available."""
    if hf_tokenizer:
        try:
            from transformers import AutoTokenizer  # type: ignore

            tok = AutoTokenizer.from_pretrained(hf_tokenizer)
            return (lambda t: len(tok.encode(t))), f"hf:{hf_tokenizer}"
        except Exception as exc:
            print(
                f"[warn] could not load HF tokenizer {hf_tokenizer!r}: {exc}; using approx",
                file=sys.stderr,
            )
    return approx_token_counter, "approx:char-div-4"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live Context Paging Runtime v0 run on Neo.")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default="qwen3:4b")
    ap.add_argument("--cap", type=int, default=5500, help="hard input-token cap per capsule")
    ap.add_argument("--max-actions", type=int, default=40)
    ap.add_argument(
        "--hf-tokenizer", default=None, help="HF repo id for the model's real tokenizer"
    )
    ap.add_argument("--workdir", default=None, help="scratch repo dir (default: a temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch dir after the run")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="paging-neo-"))
    repo = workdir / "repo"
    state = workdir / "state"
    _scratch_repo(repo)
    count, tokenizer_id = _token_counter(args.hf_tokenizer)

    runtime = new_task_runtime(
        root=repo,
        state_dir=state,
        model=OpenAICompatibleModel(args.base_url, args.model),
        objective="Fix rated_total so tax is added, not subtracted; the failing test must pass.",
        acceptance_cmd=[sys.executable, "-m", "pytest", "-q", "test_bug.py"],
        target_path="bug.py",
        cap_tokens=args.cap,
        count_tokens=count,
        max_actions=args.max_actions,
        model_id=f"{args.model}@{args.base_url}",
        tokenizer_id=tokenizer_id,
    )

    print(f"[run] model={args.model} cap={args.cap} tokenizer={tokenizer_id}\n[run] repo={repo}")
    terminal, receipt = runtime.run()
    receipt_path = state / "receipt.json"
    print(f"\n=== TERMINAL: {terminal.code}" + (f" ({terminal.reason})" if terminal.reason else ""))
    print(f"=== actions: {len(receipt.actions)}  capsules: {len(receipt.capsules)}")
    print(f"=== negative_controls observed: {receipt.negative_controls}")
    print(f"=== verification: {receipt.verification}")
    print(f"=== receipt written: {receipt_path}")

    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if terminal.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
