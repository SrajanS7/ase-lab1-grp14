"""Show everything about one dev question side by side: for hand-checking the judge and for the failure analysis.

    uv run python task3_rag/show_case.py dev062            # R4 (default)
    uv run python task3_rag/show_case.py dev062 --config r3
    uv run python task3_rag/show_case.py --list            # all judged questions with their verdicts

Reads (no LLM calls):
  data/questions_dev.jsonl                   question, ground truth, relevant documents
  results/answers_<cfg>_dev_sample50.jsonl   the answer the judge graded + the contexts the generator saw
  results/judge_<cfg>.jsonl                  the judge's verdicts
  results/retrieval_<cfg>_dev.jsonl          the rank of each relevant document (for retrieval vs generation failure)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return {r["id"]: r for r in map(json.loads, f) if r}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("id", nargs="?")
    parser.add_argument("--config", default="r4")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    questions = load(HERE / "data" / "questions_dev.jsonl")
    answers = load(HERE / "results" / f"answers_{args.config}_dev_sample50.jsonl")
    judge = load(HERE / "results" / f"judge_{args.config}.jsonl")
    retrieval = load(HERE / "results" / f"retrieval_{args.config}_dev.jsonl")
    docs = {d["doc_id"]: d for d in map(json.loads, (HERE / "data" / "corpus.jsonl").open(encoding="utf-8"))}

    if args.list or not args.id:
        print(f"{'id':<8}{'category':<14}{'correct':>8}{'faith':>7}{'relev':>7}  abstained")
        for qid, j in judge.items():
            faith = "-" if j["faithfulness"] is None else f"{j['faithfulness']:.2f}"
            print(f"{qid:<8}{j['category']:<14}{j['correctness']:>8}{faith:>7}{j['answer_relevance']:>7.2f}  {j['abstained']}")
        return

    q, a, j, r = questions[args.id], answers[args.id], judge[args.id], retrieval[args.id]
    line = "=" * 78
    print(f"{line}\n{args.id}  ({q['category']}, config {args.config.upper()})\n{line}")
    print(f"QUESTION:      {q['question']}")
    print(f"GROUND TRUTH:  {q['answer']}")
    print(f"OUR ANSWER:    {a['answer']}")

    print(f"\n--- judge ---")
    print(f"correctness:      {j['correctness']}   reason: {j['correctness_reason']}")
    print(f"faithfulness:     {j['faithfulness']}   (None = abstention or no claims, not scored)")
    for c in j["claims"]:
        print(f"   [{'supported' if c.get('supported') else 'NOT supported'}] {c.get('claim')}")
    print(f"answer relevance: {j['answer_relevance']:.3f}   questions the judge thinks this answer fits:")
    for g in j["generated_questions"]:
        print(f"   - {g}")

    print(f"\n--- retrieval ---")
    ranked = r["retrieved_doc_ids"]
    in_context = set(a["retrieved_doc_ids"])
    for group in q["relevant_doc_ids"] or []:
        for d in group:
            rank = ranked.index(d) + 1 if d in ranked else None
            where = f"rank {rank}" if rank else "not in top 20"
            print(f"   needed: {d} {docs[d]['title']!r:55} {where}, {'IN' if d in in_context else 'NOT in'} the generator's context")
    if not q["relevant_doc_ids"]:
        print("   unanswerable question: no relevant documents")
    print("   top 5 given to the generator:")
    for d in a["retrieved_doc_ids"]:
        print(f"     {d}  {docs[d]['type']:<9} {docs[d]['title']}")

    print(f"\n--- contexts the generator saw ({len(a['contexts'])}) ---")
    for i, c in enumerate(a["contexts"], start=1):
        print(f"[{i}] {c[:600]}{' ...' if len(c) > 600 else ''}\n")


if __name__ == "__main__":
    main()
