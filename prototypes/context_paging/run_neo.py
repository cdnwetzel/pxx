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
import math
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .capsule import approx_token_counter
from .model import OpenAICompatibleModel
from .runtime import new_task_runtime


def _swap_used_mb() -> float | None:
    """Current swap 'used' in MB (macOS via ``sysctl vm.swapusage``), else None.

    The delta across a run is the real 8 GB failure signal: if a box swaps, that dominates any
    tok/s number. On the phone (candidate C) this must be read on-device — it is not visible here.
    """
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5
        ).stdout
        # e.g. "total = 2048.00M  used = 512.25M  free = 1535.75M ..."  -> parse the 'used' value
        parts = out.replace("=", " ").split()
        for i, p in enumerate(parts):
            if p == "used" and i + 1 < len(parts):
                return float(parts[i + 1].rstrip("Mm"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def build_performance(
    stats: list[dict],
    wall_clock_s: float,
    swap_delta_mb: float | None,
    transport: str,
    streamed: bool,
) -> dict:
    """Aggregate per-call model samples into a comparable performance block. Pure + testable.

    Rates are computed over the time of the calls that ACTUALLY reported usage (not all calls),
    so a single call missing `usage` can't deflate the throughput. TTFT (stream mode) gives the
    clean prefill/decode split: prefill_tokens_per_s over TTFT, decode over (latency - TTFT).
    """
    calls = [s for s in stats if s.get("latency_s") is not None]
    latencies = [s["latency_s"] for s in calls]
    ttfts = [s["ttft_s"] for s in calls if s.get("ttft_s") is not None]
    n = len(latencies)
    model_time = sum(latencies)

    with_prompt = [s for s in calls if s.get("prompt_tokens") is not None]
    with_comp = [s for s in calls if s.get("completion_tokens") is not None]
    total_prompt = sum(s["prompt_tokens"] for s in with_prompt) if with_prompt else None
    total_comp = sum(s["completion_tokens"] for s in with_comp) if with_comp else None
    tokens_complete = bool(with_prompt) and len(with_prompt) == n and len(with_comp) == n

    def _rate(total, time_s):  # over MATCHED time only, so partial usage never deflates the rate
        return round(total / time_s, 2) if (total and time_s and time_s > 0) else None

    # clean prefill/decode split (stream mode): prefill over TTFT, decode over (latency - TTFT)
    pf = [s for s in with_prompt if s.get("ttft_s") is not None]
    dc = [s for s in with_comp if s.get("ttft_s") is not None]
    prefill_time = sum(s["ttft_s"] for s in pf)
    decode_time = sum(max(0.0, s["latency_s"] - s["ttft_s"]) for s in dc)

    p90 = round(sorted(latencies)[min(n - 1, math.ceil(0.9 * n) - 1)], 3) if n else None
    return {
        "transport": transport,  # "local" (host+model co-located) or "lan" (remote endpoint)
        "streamed": streamed,
        "wall_clock_s": round(wall_clock_s, 3),
        "model_calls": n,
        "model_time_s": round(model_time, 3),
        "latency_median_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p90_s": p90,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_comp,
        "tokens_complete": tokens_complete,  # False => rates are over a subset of calls
        # prefill-inclusive throughput over the matched calls' wall-clock
        "prompt_tokens_per_s": _rate(total_prompt, sum(s["latency_s"] for s in with_prompt)),
        "completion_tokens_per_s": _rate(total_comp, sum(s["latency_s"] for s in with_comp)),
        # the CLEAN split (stream mode only): prefill over TTFT, decode over (latency - TTFT)
        "prefill_tokens_per_s": _rate(
            sum(s["prompt_tokens"] for s in pf) if pf else None, prefill_time
        ),
        "decode_tokens_per_s": _rate(
            sum(s["completion_tokens"] for s in dc) if dc else None, decode_time
        ),
        "ttft_median_s": round(statistics.median(ttfts), 3) if ttfts else None,
        "swap_used_delta_mb": round(swap_delta_mb, 2) if swap_delta_mb is not None else None,
        "notes": "Rates are over the calls that reported usage (tokens_complete flags a subset). "
        "prefill/decode_tokens_per_s (stream mode) are the clean split. swap_used_delta_mb is "
        "measured on the HOST process: it reflects the box under test ONLY when host+model are "
        "co-located (--transport local). For remote/LAN runs (incl. the iPhone) it measures the "
        "control host — read the box-under-test's memory on that box / on-device instead.",
    }


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
    ap.add_argument(
        "--stream",
        action="store_true",
        help="stream deltas to measure time-to-first-token (prefill proxy) — recommended for A/B",
    )
    ap.add_argument(
        "--transport",
        default="local",
        choices=["local", "lan"],
        help="'local' = host+model co-located; 'lan' = remote endpoint (e.g. the iPhone)",
    )
    args = ap.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="paging-neo-"))
    repo = workdir / "repo"
    state = workdir / "state"
    _scratch_repo(repo)
    count, tokenizer_id = _token_counter(args.hf_tokenizer)

    model = OpenAICompatibleModel(args.base_url, args.model, stream=args.stream)
    runtime = new_task_runtime(
        root=repo,
        state_dir=state,
        model=model,
        objective="Fix rated_total so tax is added, not subtracted; the failing test must pass.",
        acceptance_cmd=[sys.executable, "-m", "pytest", "-q", "test_bug.py"],
        target_path="bug.py",
        cap_tokens=args.cap,
        count_tokens=count,
        max_actions=args.max_actions,
        model_id=f"{args.model}@{args.base_url}",
        tokenizer_id=tokenizer_id,
    )

    print(
        f"[run] model={args.model} cap={args.cap} tokenizer={tokenizer_id} "
        f"stream={args.stream} transport={args.transport}\n[run] repo={repo}"
    )
    swap_before = _swap_used_mb()
    wall_start = time.perf_counter()
    terminal, receipt = runtime.run()
    wall_clock_s = time.perf_counter() - wall_start
    swap_after = _swap_used_mb()
    swap_delta = (
        (swap_after - swap_before) if (swap_before is not None and swap_after is not None) else None
    )

    # attach the performance block and RE-SAVE (run() already saved the deterministic receipt)
    receipt.performance = build_performance(
        model.stats, wall_clock_s, swap_delta, args.transport, args.stream
    )
    receipt_path = receipt.save(state)

    print(f"\n=== TERMINAL: {terminal.code}" + (f" ({terminal.reason})" if terminal.reason else ""))
    print(f"=== actions: {len(receipt.actions)}  capsules: {len(receipt.capsules)}")
    print(f"=== negative_controls observed: {receipt.negative_controls}")
    print(f"=== verification: {receipt.verification}")
    p = receipt.performance
    print(
        f"=== perf: wall={p['wall_clock_s']}s  model_time={p['model_time_s']}s  "
        f"calls={p['model_calls']}  prompt_tok/s={p['prompt_tokens_per_s']}  "
        f"ttft_med={p['ttft_median_s']}s  swap_delta_mb={p['swap_used_delta_mb']}"
    )
    print(f"=== receipt written: {receipt_path}")

    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if terminal.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
