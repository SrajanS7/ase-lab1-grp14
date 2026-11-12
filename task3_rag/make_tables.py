"""Builds the summary tables of Task 3 from the files in results/ (no LLM calls, no new numbers).

    uv run python task3_rag/make_tables.py

Writes:
  results/table_retrieval.md   configs x categories, context recall@5 and precision@5 (+ held-out, + recall@10)
  results/table_judge.md       judge scores per config and category (if results/judge_*.jsonl exist)
  results/costs_generation.csv tokens, $ and latency per answered question (if results/answers_*.jsonl exist)
  results/table_costs.md       one row per config: indexing time, retrieval latency, tokens and $ per query

Token counts in table_costs.md:
  measured  = reported by the gateway for answers we actually generated (R3, R4)
  estimated = the exact prompt generate.py would send for that config (top-5 whole documents), counted
              locally with tiktoken cl100k_base; no API call. For R3/R4 both are shown, so the estimate
              can be checked against the measurement. Completion tokens/latency for R1/R2/R5 are the
              mean of the measured R3/R4 runs (the answer length does not depend much on the retriever).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
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


def costs_table() -> str:
    from chunking import fixed_size_chunks, load_corpus, n_tokens, recursive_chunks
    from evaluate import load_jsonl
    from generate import PRICE_IN, PRICE_OUT, SYSTEM_PROMPT, build_doc_context, user_prompt
    from indexes import QUERY_INSTRUCTION
    from judge import stratified_sample

    info = json.loads((RESULTS / "run_info.json").read_text())
    size, overlap = info["chosen_chunk_size"], info["overlap_tokens"]
    retrieval_costs = {r["config"].lower(): r for r in csv.DictReader((RESULTS / "costs_retrieval.csv").open())}
    corpus = load_corpus()
    docs_by_id = {d["doc_id"]: d for d in corpus}
    chunk_sets = {"recursive": recursive_chunks(corpus, size, overlap), "fixed": fixed_size_chunks(corpus, size, overlap)}
    dev = load_jsonl(HERE / "data" / "questions_dev.jsonl")
    test = load_jsonl(HERE / "data" / "questions_test.jsonl")
    sample = stratified_sample(dev, 50)
    query_tokens = statistics.mean(n_tokens(QUERY_INSTRUCTION + q["question"]) for q in dev + test)

    measured = {}
    for cfg in CONFIGS:
        path = RESULTS / f"answers_{cfg}_dev_sample50.jsonl"
        if path.exists():
            rows = load_jsonl(path)
            measured[cfg] = {
                "prompt": statistics.mean(r["usage"]["prompt_tokens"] for r in rows),
                "completion": statistics.mean(r["usage"]["completion_tokens"] for r in rows),
                "latency": statistics.median(r["latency_s"] for r in rows),
            }
    completion_fallback = statistics.mean(m["completion"] for m in measured.values())
    latency_fallback = statistics.mean(m["latency"] for m in measured.values())

    lines = [
        "# Cost and latency per configuration",
        "",
        f"Chunk size {size} tokens. Retrieval latency: first (uncached) query-embedding request + local compute, "
        "median / p95 over 248 dev+test queries (2 rate-limited queries excluded). Generation: gpt-6-luna, "
        "top-5 whole documents as context, 50 sampled dev questions. **m** = measured by the gateway, "
        "**e** = estimated (prompt counted locally with tiktoken; completion and generation latency = mean of the "
        f"measured R3/R4 runs). Prices: ${PRICE_IN * 1e6:.2f} / ${PRICE_OUT * 1e6:.2f} per 1M input / output tokens; "
        "embeddings are free.",
        "",
        "| config | indexing time | corpus tokens embedded | retrieval latency median / p95 | query embedding tokens "
        "| generator prompt tokens | completion tokens | $ per query | end-to-end latency (median) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cfg in CONFIGS:
        rc = retrieval_costs[cfg]
        kind = "fixed" if cfg == "r2" else "recursive"
        chunks = chunk_sets[kind]
        chunks_by_id = {c.chunk_id: c for c in chunks}
        retrieval = {r["id"]: r for r in load_jsonl(RESULTS / f"retrieval_{cfg}_dev.jsonl")}

        est_prompt = statistics.mean(
            n_tokens(SYSTEM_PROMPT) + n_tokens(user_prompt(q["question"],
                                                           build_doc_context(retrieval[q["id"]], chunks_by_id, docs_by_id)))
            for q in sample
        )
        index_parts = [f"chunk {float(rc['index_chunk_s']):.2f} s"]
        if cfg != "r1":
            index_parts.append(f"embed {float(rc['index_embed_s']):.1f} s")
        if cfg in ("r1", "r4", "r5"):
            index_parts.append(f"BM25 {float(rc['index_bm25_s']):.2f} s")
        if cfg == "r5":
            index_parts.append(f"cross-encoder load {float(rc['index_crossencoder_load_s']):.1f} s")
        corpus_tokens = "–" if cfg == "r1" else f"{sum(n_tokens(c.text) for c in chunks):,}"
        q_tokens = "0" if cfg == "r1" else f"{query_tokens:.0f}"

        m = measured.get(cfg)
        if m:
            prompt_cell = f"{m['prompt']:.0f} m ({est_prompt:.0f} e)"
            completion, gen_latency, tag = m["completion"], m["latency"], "m"
            prompt_for_cost = m["prompt"]
        else:
            prompt_cell = f"{est_prompt:.0f} e"
            completion, gen_latency, tag = completion_fallback, latency_fallback, "e"
            prompt_for_cost = est_prompt
        cost = prompt_for_cost * PRICE_IN + completion * PRICE_OUT
        retrieval_median = float(rc["latency_median_s"])
        lines.append(
            f"| {NAMES[cfg]} | {', '.join(index_parts)} | {corpus_tokens} | "
            f"{retrieval_median * 1000:.0f} ms / {float(rc['latency_p95_s']) * 1000:.0f} ms | {q_tokens} | "
            f"{prompt_cell} | {completion:.0f} {tag} | ${cost:.6f} {tag} | {retrieval_median + gen_latency:.2f} s {tag} |"
        )
    lines += [
        "",
        f"Query embedding tokens = instruction prefix + question, counted locally (mean over 250 questions). "
        f"R1 sends no request. Retrieval itself uses no LLM tokens in any configuration.",
    ]
    return "\n".join(lines) + "\n"


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
    if (RESULTS / "costs_retrieval.csv").exists():
        table = costs_table()
        (RESULTS / "table_costs.md").write_text(table, encoding="utf-8")
        print(table)


if __name__ == "__main__":
    main()
