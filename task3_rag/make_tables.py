"""Builds the summary tables of Task 3 from the files in results/ (no LLM calls, no new numbers).

    uv run python task3_rag/make_tables.py

Writes:
  results/table_retrieval.md   configs x categories, context recall@5 and precision@5 (+ held-out, + recall@10)
  results/table_judge.md       judge scores per config and category (if results/judge_*.jsonl exist)
  results/costs_generation.csv tokens, $ and latency per answered question (if results/answers_*.jsonl exist)
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CONFIGS = ["r1", "r2", "r3", "r4", "r5"]
CATEGORIES = ["identifier", "paraphrase", "multi-hop", "version"]
NAMES = {"r1": "R1 BM25", "r2": "R2 fixed+dense", "r3": "R3 recursive+dense", "r4": "R4 hybrid",
         "r5": "R5 hybrid+rerank"}


def retrieval_table() -> str:
    info = json.loads((RESULTS / "run_info.json").read_text())
    head = ["config"] + [f"{c} R / P" for c in CATEGORIES] + ["**overall R / P**", "held-out R / P", "overall R@10"]
    lines = [
        "# Retrieval results on dev (k = 5 documents)",
        "",
        f"R = context recall@5, P = context precision@5 (evaluate.py). Chunk size {info['chosen_chunk_size']} tokens, "
        f"overlap {info['overlap_tokens']}, alpha = {info['chosen_alpha']}. n = 120 answerable questions "
        "(30 per category); held-out = the 60 answerable questions with even ids, not used for tuning.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    for cfg in CONFIGS:
        path = RESULTS / f"metrics_{cfg}_k5.json"
        if not path.exists():
            continue
        m5 = json.loads(path.read_text())
        m10 = json.loads((RESULTS / f"metrics_{cfg}_k10.json").read_text())
        s, h = m5["summary"], m5["heldout_summary"]
        cells = [NAMES[cfg]]
        for cat in CATEGORIES:
            c = s["per_category"][cat]
            cells.append(f"{c['context_recall']:.3f} / {c['context_precision']:.3f}")
        o = s["overall"]
        cells.append(f"**{o['context_recall']:.3f} / {o['context_precision']:.3f}**")
        cells.append(f"{h['overall']['context_recall']:.3f} / {h['overall']['context_precision']:.3f}")
        cells.append(f"{m10['summary']['overall']['context_recall']:.3f}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def judge_table() -> str | None:
    files = sorted(RESULTS.glob("judge_*.jsonl"))
    if not files:
        return None

    def mean(values):
        values = [v for v in values if v is not None]
        return f"{sum(values) / len(values):.3f}" if values else "–"

    lines = ["# End-to-end results (judge.py --sample 50)", "",
             "faithfulness: mean over answers that make claims (abstentions not scored). "
             "correctness: 1 / 0.5 / 0 (correct / partial / wrong).", "",
             "| config | category | n | abstained | faithfulness | answer relevance | correctness |",
             "|---|---|---|---|---|---|---|"]
    for path in files:
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        groups = defaultdict(list)
        for r in rows:
            groups[r["category"]].append(r)
        groups["**overall**"] = rows
        for cat, items in groups.items():
            lines.append(f"| {path.stem.replace('judge_', '')} | {cat} | {len(items)} | "
                         f"{mean([float(r['abstained']) for r in items])} | {mean([r['faithfulness'] for r in items])} | "
                         f"{mean([r['answer_relevance'] for r in items])} | {mean([r['correctness'] for r in items])} |")
    return "\n".join(lines) + "\n"


def generation_costs() -> list[dict]:
    out = []
    for path in sorted(RESULTS.glob("answers_*.jsonl")):
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        u = [r["usage"] for r in rows]
        out.append({
            "file": path.name,
            "n": len(rows),
            "prompt_tokens_mean": round(statistics.mean(x["prompt_tokens"] for x in u)),
            "completion_tokens_mean": round(statistics.mean(x["completion_tokens"] for x in u)),
            "reasoning_tokens_mean": round(statistics.mean(x["reasoning_tokens"] or 0 for x in u)),
            "cost_usd_per_query": round(statistics.mean(x["cost_usd_est"] for x in u), 6),
            "cost_usd_total": round(sum(x["cost_usd_est"] for x in u), 4),
            "latency_median_s": round(statistics.median(r["latency_s"] for r in rows), 2),
        })
    return out


def main() -> None:
    (RESULTS / "table_retrieval.md").write_text(retrieval_table(), encoding="utf-8")
    print((RESULTS / "table_retrieval.md").read_text())
    jt = judge_table()
    if jt:
        (RESULTS / "table_judge.md").write_text(jt, encoding="utf-8")
        print(jt)
    costs = generation_costs()
    if costs:
        with (RESULTS / "costs_generation.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(costs[0]))
            w.writeheader()
            w.writerows(costs)
        for c in costs:
            print(c)


if __name__ == "__main__":
    main()
