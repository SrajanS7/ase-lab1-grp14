"""Runs all retrieval experiments of Task 3 and writes everything into task3_rag/results/.

    uv run python task3_rag/run_retrieval.py

Steps (all numbers end up in results/, nothing is only printed):
  1. Chunk the corpus: recursive and fixed-size, 3 sizes each (64 / 128 / 256 tokens, overlap 16).
     Embed every chunk set (cached in task3_rag/cache/, the first run records the embedding time).
  2. Chunk size: R3 (and R2 for comparison) at the 3 sizes          -> results/chunk_size.csv/.md
  3. Choose the size with the best recall@5 on the TUNE split.
  4. Hybrid weight: R4 with alpha = 0.0 ... 1.0 on that size          -> results/hybrid_weight_sweep.csv/.md
  5. Choose alpha with the best recall@5 on the TUNE split.
  6. Final R1–R5 on dev and test                                      -> results/retrieval_<cfg>_<split>.jsonl
                                                                         results/metrics_<cfg>_k5.json / _k10.json
  7. Indexing time and per-query latency                              -> results/costs_retrieval.csv
  8. All choices, models, git commit                                  -> results/run_info.json

Tuning protocol: dev questions with an odd id number (dev001, dev003, ...) are the TUNE split, even ids are
the HELD-OUT split. Chunk size and alpha are chosen on TUNE only. We report all 150 (as the sheet asks) and
HELD-OUT separately, so you can see whether the choices generalise.
"""

from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from chunking import describe, fixed_size_chunks, load_corpus, recursive_chunks  # noqa: E402
from evaluate import evaluate, load_jsonl  # noqa: E402
from indexes import BM25Index, DenseIndex, Embedder  # noqa: E402
from retrievers import (  # noqa: E402
    CANDIDATES, CROSS_ENCODER, RERANK_DEPTH, BM25Retriever, DenseRetriever, HybridRetriever, RerankRetriever,
    to_doc_ids,
)

RESULTS = HERE / "results"
SIZES = (64, 128, 256)
OVERLAP = 16
ALPHAS = [round(a * 0.1, 1) for a in range(11)]
CATEGORIES = ["identifier", "paraphrase", "multi-hop", "version"]
RATE_LIMITED_S = 10.0  # a single query embedding normally takes < 3 s; longer means a rate-limit retry wait


def split_of(qid: str) -> str:
    return "tune" if int(qid[3:]) % 2 == 1 else "heldout"


def run(retriever, questions: list[dict]) -> list[dict]:
    rows = []
    for q in questions:
        start = time.perf_counter()
        hits = retriever.search(q["question"])
        local_s = time.perf_counter() - start
        timing = {k: (round(v, 5) if v is not None else None) for k, v in retriever.last_timing.items()}
        # The query vector comes from the local cache here, so local_s is pure computation.
        # Total latency = recorded first-run (uncached) embedding request time + local computation.
        timing["local_s"] = round(local_s, 5)
        timing["total_s"] = round((timing.get("embed_query_s") or 0.0) + local_s, 5)
        rows.append({
            "id": q["id"],
            "retrieved_doc_ids": to_doc_ids(hits, 20),
            "chunks": [{"chunk_id": h.chunk.chunk_id, "score": round(h.score, 5)} for h in hits],
            "timing": timing,
        })
    return rows


def metrics(rows: list[dict], questions: list[dict], k: int = 5) -> dict:
    return evaluate(rows, questions, k)["summary"]


def flat(summary: dict) -> dict:
    out = {"recall": round(summary["overall"]["context_recall"], 4),
           "precision": round(summary["overall"]["context_precision"], 4)}
    for cat in CATEGORIES:
        s = summary["per_category"].get(cat)
        out[f"recall_{cat}"] = round(s["context_recall"], 4) if s else None
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_csv_md(stem: str, rows: list[dict], title: str) -> None:
    keys = list(rows[0].keys())
    with (RESULTS / f"{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    lines = [f"# {title}", "", "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    for r in rows:
        lines.append("| " + " | ".join("" if r[k] is None else (f"{r[k]:.3f}" if isinstance(r[k], float) else str(r[k]))
                                       for k in keys) + " |")
    (RESULTS / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def best(rows: list[dict], key_recall: str, key_precision: str, param: str):
    """Highest tune recall@5; ties broken by tune precision@5, then by the earlier (smaller) parameter."""
    return max(rows, key=lambda r: (round(r[key_recall], 4), round(r[key_precision], 4), -rows.index(r)))[param]


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    corpus = load_corpus()
    dev = load_jsonl(HERE / "data" / "questions_dev.jsonl")
    test = load_jsonl(HERE / "data" / "questions_test.jsonl")
    tune = [q for q in dev if split_of(q["id"]) == "tune"]
    heldout = [q for q in dev if split_of(q["id"]) == "heldout"]
    embedder = Embedder()

    # 1. chunk + index ---------------------------------------------------------------------------------
    indexing = {}
    chunk_sets = {}
    for kind, fn in (("recursive", recursive_chunks), ("fixed", fixed_size_chunks)):
        for size in SIZES:
            t = time.perf_counter()
            chunks = fn(corpus, size, OVERLAP)
            chunk_s = time.perf_counter() - t
            dense = DenseIndex(chunks, embedder)
            embedder.save()
            chunk_sets[(kind, size)] = (chunks, dense)
            indexing[(kind, size)] = {"chunk_s": round(chunk_s, 3), **describe(chunks),
                                      "embed_s": dense.info.get("embed_s"), "embed_cached_now": dense.info["cached"]}
            print(f"{kind:>9} {size:>3}: {indexing[(kind, size)]}")

    # warm the query cache: one request per query, the time of the first request is stored.
    # Throttled to ~80 requests/min: the gateway allows 100/min, and a rate-limit retry (up to a minute of
    # waiting) would otherwise be recorded as query latency.
    for q in dev + test:
        if embedder.query_embed_seconds(q["question"]) is None:
            started = time.perf_counter()
            embedder.embed_query(q["question"])
            time.sleep(max(0.0, 0.75 - (time.perf_counter() - started)))
    embedder.save()

    # 2. chunk size -------------------------------------------------------------------------------------
    size_rows = []
    for size in SIZES:
        for name, kind in (("R3", "recursive"), ("R2", "fixed")):
            chunks, dense = chunk_sets[(kind, size)]
            rows = run(DenseRetriever(dense), dev)
            size_rows.append({"config": name, "chunking": kind, "size_tokens": size, "overlap_tokens": OVERLAP,
                              "n_chunks": len(chunks),
                              **{f"tune_{k}": v for k, v in flat(metrics(rows, tune)).items()},
                              **{f"heldout_{k}": v for k, v in flat(metrics(rows, heldout)).items()},
                              **{f"all_{k}": v for k, v in flat(metrics(rows, dev)).items()}})
    write_csv_md("chunk_size", size_rows, "Chunk size comparison (dense retrieval, k=5)")
    size = best([r for r in size_rows if r["config"] == "R3"], "tune_recall", "tune_precision", "size_tokens")
    print(f"chosen chunk size: {size}")

    # 4. hybrid weight ------------------------------------------------------------------------------------
    rec_chunks, rec_dense = chunk_sets[("recursive", size)]
    t = time.perf_counter()
    bm25 = BM25Index(rec_chunks)
    sweep_rows = []
    for alpha in ALPHAS:
        rows = run(HybridRetriever(bm25, rec_dense, alpha), dev)
        sweep_rows.append({"alpha": alpha,
                           **{f"tune_{k}": v for k, v in flat(metrics(rows, tune)).items()},
                           **{f"heldout_{k}": v for k, v in flat(metrics(rows, heldout)).items()}})
    write_csv_md("hybrid_weight_sweep", sweep_rows,
                 "Hybrid weight sweep, R4 = alpha*dense + (1-alpha)*BM25 (k=5). alpha=0 is pure BM25, 1 pure dense")
    alpha = best(sweep_rows, "tune_recall", "tune_precision", "alpha")
    print(f"chosen alpha: {alpha}")

    # 6. final configurations ------------------------------------------------------------------------------
    fix_chunks, fix_dense = chunk_sets[("fixed", size)]
    hybrid = HybridRetriever(bm25, rec_dense, alpha)
    reranker = RerankRetriever(hybrid)
    configs = {
        "r1": BM25Retriever(bm25),
        "r2": DenseRetriever(fix_dense),
        "r3": DenseRetriever(rec_dense),
        "r4": hybrid,
        "r5": reranker,
    }
    final_rows = []
    for cfg, retriever in configs.items():
        dev_rows = run(retriever, dev)
        test_rows = run(retriever, test)
        write_jsonl(RESULTS / f"retrieval_{cfg}_dev.jsonl", dev_rows)
        write_jsonl(RESULTS / f"retrieval_{cfg}_test.jsonl", test_rows)
        for k in (5, 10):
            report = evaluate(dev_rows, dev, k)
            report["heldout_summary"] = evaluate(dev_rows, heldout, k)["summary"]
            (RESULTS / f"metrics_{cfg}_k{k}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

        all_lat = [r["timing"] for r in dev_rows + test_rows]
        # Queries whose embedding request waited for a rate-limit retry are excluded from latency statistics.
        lat = [t for t in all_lat if (t.get("embed_query_s") or 0.0) < RATE_LIMITED_S]
        totals = sorted(t["total_s"] for t in lat)
        local = sorted(t["local_s"] for t in lat)
        kind = "fixed" if cfg == "r2" else "recursive"
        ix = indexing[(kind, size)]
        final_rows.append({
            "config": cfg.upper(),
            "n_chunks": ix["n_chunks"],
            "index_chunk_s": ix["chunk_s"],
            "index_embed_s": ix["embed_s"] if cfg != "r1" else 0.0,
            "index_bm25_s": round(bm25.build_s, 3) if cfg in ("r1", "r4", "r5") else 0.0,
            "index_crossencoder_load_s": round(reranker.load_s, 2) if cfg == "r5" else 0.0,
            "latency_median_s": round(statistics.median(totals), 4),
            "latency_p95_s": round(totals[int(0.95 * (len(totals) - 1))], 4),
            "latency_local_median_s": round(statistics.median(local), 4),
            "llm_tokens_per_query": 0,
            "n_queries": len(totals),
            "n_excluded_rate_limited": len(all_lat) - len(lat),
        })
        print(cfg, flat(metrics(dev_rows, dev)))
    write_csv_md("costs_retrieval", final_rows, "Indexing time and retrieval latency per configuration")

    # 8. run info ------------------------------------------------------------------------------------------
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
    except Exception:
        commit = None
    (RESULTS / "run_info.json").write_text(json.dumps({
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "git_commit": commit,
        "embedding_model": embedder.model,
        "cross_encoder": CROSS_ENCODER,
        "chunk_sizes_compared": SIZES,
        "overlap_tokens": OVERLAP,
        "chosen_chunk_size": size,
        "alphas_compared": ALPHAS,
        "chosen_alpha": alpha,
        "candidates_per_search": CANDIDATES,
        "rerank_depth": RERANK_DEPTH,
        "tuning": "chunk size and alpha chosen by recall@5 (tie: precision@5) on dev questions with odd ids",
        "indexing": {f"{k}_{s}": v for (k, s), v in indexing.items()},
    }, indent=2), encoding="utf-8")
    print(f"done. results in {RESULTS}")


if __name__ == "__main__":
    main()
