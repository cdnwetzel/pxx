"""A/B eval harness (plan §6).

For every (question, model) it asks twice — once direct to Ollama (no docs) and
once through the docs-rag-sme proxy (docs injected) — and scores each answer by
how many of the question's `expect` tokens it contains. Docs-on minus docs-off
is the objective "does retrieval help this model" signal. Writes a Markdown
report + JSONL.

Usage:
    uv run python eval/run_ab.py --models qwen2.5-coder:7b,qwen2.5:32b-instruct-q4_K_M
Requires: the SME running on --sme (forwarding to the same Ollama as --ollama).
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

import httpx

HERE = Path(__file__).parent


def ask(client: httpx.Client, base: str, model: str, question: str) -> tuple[str, int]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "stream": False,
        "temperature": 0,
    }
    r = client.post(f"{base}/v1/chat/completions", json=payload, timeout=300)
    r.raise_for_status()
    injected = int(r.headers.get("X-Docs-SME-Injected", "0"))
    return r.json()["choices"][0]["message"]["content"], injected


def score(answer: str, expect: list[str]) -> int:
    a = answer.lower()
    return sum(1 for e in expect if e.lower() in a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen2.5-coder:7b,qwen2.5:32b-instruct-q4_K_M")
    ap.add_argument("--sme", default="http://127.0.0.1:8004")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--questions", default=str(HERE / "questions.toml"))
    ap.add_argument("--out", default=str(HERE / "results.md"))
    args = ap.parse_args(argv)

    qs = tomllib.loads(Path(args.questions).read_text())["question"]
    models = [m.strip() for m in args.models.split(",")]
    rows: list[dict] = []

    with httpx.Client() as c:
        for q in qs:
            expect = q.get("expect", [])
            n = len(expect)
            for model in models:
                nd_ans, _ = ask(c, args.ollama, model, q["q"])
                d_ans, inj = ask(c, args.sme, model, q["q"])
                nd, d = score(nd_ans, expect), score(d_ans, expect)
                rows.append({
                    "id": q["id"], "model": model, "n": n, "injected": inj,
                    "nodocs": nd, "docs": d, "nodocs_ans": nd_ans, "docs_ans": d_ans,
                })
                arrow = "↑" if d > nd else ("=" if d == nd else "↓")
                print(f"  {q['id']:24} {model:32} no-docs {nd}/{n}  docs {d}/{n} {arrow}  (inj={inj})")

    # Aggregate
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)

    lines = ["# docs-rag-sme A/B — does retrieval lift answer accuracy?\n",
             "Score = count of expected API tokens present (objective).\n",
             "| model | no-docs | docs | lift |", "|---|---|---|---|"]
    for model, rs in by_model.items():
        tot = sum(r["n"] for r in rs)
        nd = sum(r["nodocs"] for r in rs)
        d = sum(r["docs"] for r in rs)
        lines.append(f"| {model} | {nd}/{tot} ({nd/tot:.0%}) | {d}/{tot} ({d/tot:.0%}) | +{d-nd} |")

    lines.append("\n## Per-question detail\n")
    for r in rows:
        lines.append(f"### {r['id']} — {r['model']}  (injected {r['injected']})")
        lines.append(f"- no-docs {r['nodocs']}/{r['n']}, docs {r['docs']}/{r['n']}")
    Path(args.out).write_text("\n".join(lines))
    Path(args.out).with_suffix(".jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    print("\n=== AGGREGATE ===")
    for model, rs in by_model.items():
        tot = sum(r["n"] for r in rs)
        nd = sum(r["nodocs"] for r in rs)
        d = sum(r["docs"] for r in rs)
        print(f"  {model:32} no-docs {nd}/{tot} ({nd/tot:.0%}) -> docs {d}/{tot} ({d/tot:.0%})  lift +{d-nd}")
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
