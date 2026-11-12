"""Generation step of Task 3: answer questions from the chunks a retrieval configuration found.

    # the 50 dev questions judge.py --sample 50 will pick (same stratified sample, seed 0)
    uv run python task3_rag/generate.py r4 --split dev --sample 50
    # all 100 test questions + predictions_test.jsonl
    uv run python task3_rag/generate.py r5 --split test --predictions

Input:  results/retrieval_<cfg>_<split>.jsonl (written by run_retrieval.py)
Output: results/answers_<cfg>_<split>[_sample50].jsonl, one line per question:
          {"id", "answer", "contexts", "retrieved_doc_ids", "usage": {...}, "latency_s"}
        "contexts" are exactly the chunk texts that were in the prompt (what judge.py expects).

Context (--context):
  docs    (default, "small-to-big") the FULL text of each of the top-5 documents, in rank order. Retrieval
          searches small 64-token chunks (good for finding the right document), but the chunk that matched
          is often not the one holding the answer (e.g. the "Fix" section of an error page). Documents longer
          than LONG_DOC_TOKENS (only changelogs) contribute their retrieved chunks instead.
  chunks  only the retrieved chunks of the top-5 documents (max MAX_CHUNKS). Our first version; kept so the
          before/after comparison stays reproducible (output files get the suffix _chunkctx).
The model must answer only from the context and use the exact abstention sentence otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from common.llm_client import LLMClient  # noqa: E402

from chunking import fixed_size_chunks, load_corpus, n_tokens, recursive_chunks  # noqa: E402
from evaluate import load_jsonl  # noqa: E402
from judge import ABSTENTION, stratified_sample  # noqa: E402

RESULTS = HERE / "results"
TOP_DOCS = 5
MAX_CHUNKS = 10
LONG_DOC_TOKENS = 400                        # longer documents (the changelogs) are not inserted whole
THREADS = 4                                  # README: stay below ~5 parallel requests per key
PRICE_IN, PRICE_OUT = 0.10 / 1e6, 0.50 / 1e6  # gpt-6-luna, $ per token (README)

SYSTEM_PROMPT = f"""You answer questions about PaderQueue, a message queue, using ONLY the documentation excerpts given.

Rules:
- Use only facts stated in the excerpts. Do not use outside knowledge.
- Answer in 1-3 sentences. Give exact values, setting names, error codes and commands as written, in backticks.
- If the question names a version (e.g. 2.x), use the excerpt for that version. Reference pages (config, error, cli,
  runbook, changelog) beat forum posts; forum answers can be wrong or outdated.
- Some questions need two excerpts combined (e.g. an incident names an error code, the error page gives the fix).
- If the excerpts do not contain the answer, reply with exactly this sentence and nothing else:
{ABSTENTION}"""


def build_context(row: dict, chunks_by_id: dict) -> list[str]:
    """--context chunks: the retrieved chunks that belong to the top-5 documents, in rank order."""
    top_docs = set(row["retrieved_doc_ids"][:TOP_DOCS])
    texts = [chunks_by_id[c["chunk_id"]].text for c in row["chunks"] if chunks_by_id[c["chunk_id"]].doc_id in top_docs]
    return texts[:MAX_CHUNKS]


def build_doc_context(row: dict, chunks_by_id: dict, docs_by_id: dict) -> list[str]:
    """--context docs: one context per top-5 document, the whole document if it is short."""
    contexts = []
    for doc_id in row["retrieved_doc_ids"][:TOP_DOCS]:
        doc = docs_by_id[doc_id]
        header = f"[{doc['title']} | {doc['type']} | version {doc.get('version') or 'n/a'}]"
        if n_tokens(doc["text"]) <= LONG_DOC_TOKENS:
            contexts.append(f"{header}\n{doc['text'].strip()}")
        else:
            parts = [chunks_by_id[c["chunk_id"]].body.strip() for c in row["chunks"]
                     if chunks_by_id[c["chunk_id"]].doc_id == doc_id][:3]
            contexts.append(header + "\n" + "\n[...]\n".join(parts))
    return contexts


def user_prompt(question: str, contexts: list[str]) -> str:
    excerpts = "\n\n".join(f"<excerpt {i}>\n{t}\n</excerpt {i}>" for i, t in enumerate(contexts, start=1))
    return f"Documentation excerpts:\n\n{excerpts}\n\nQuestion: {question}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="r1 ... r5")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--sample", type=int, help="only the stratified sample judge.py --sample N will use")
    parser.add_argument("--predictions", action="store_true", help="also write task3_rag/predictions_test.jsonl")
    parser.add_argument("--model", help="chat model (default $LAB_CHAT_MODEL)")
    parser.add_argument("--context", choices=["docs", "chunks"], default="docs",
                        help="docs: full top-5 documents (default); chunks: only the retrieved chunks")
    args = parser.parse_args()

    info = json.loads((RESULTS / "run_info.json").read_text())
    corpus = load_corpus()
    chunker = fixed_size_chunks if args.config == "r2" else recursive_chunks
    chunks_by_id = {c.chunk_id: c for c in chunker(corpus, info["chosen_chunk_size"], info["overlap_tokens"])}
    docs_by_id = {d["doc_id"]: d for d in corpus}

    questions = load_jsonl(HERE / "data" / f"questions_{args.split}.jsonl")
    if args.sample:
        questions = stratified_sample(questions, args.sample)
    retrieval = {r["id"]: r for r in load_jsonl(RESULTS / f"retrieval_{args.config}_{args.split}.jsonl")}

    suffix = (f"_sample{args.sample}" if args.sample else "") + ("_chunkctx" if args.context == "chunks" else "")
    out_path = RESULTS / f"answers_{args.config}_{args.split}{suffix}.jsonl"
    llm = LLMClient(model=args.model, log_path=HERE / "logs" / f"generate_{args.config}_{args.split}{suffix}.jsonl")
    lock = threading.Lock()

    def answer(q: dict) -> dict:
        row = retrieval[q["id"]]
        contexts = (build_doc_context(row, chunks_by_id, docs_by_id) if args.context == "docs"
                    else build_context(row, chunks_by_id))
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user_prompt(q["question"], contexts)}]
        start = time.perf_counter()
        response = llm._chat_request(messages, None, None)  # full response, so we get the token usage
        latency = time.perf_counter() - start
        usage = response.usage
        details = getattr(usage, "completion_tokens_details", None)
        u = {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
             "reasoning_tokens": getattr(details, "reasoning_tokens", None)}
        u["cost_usd_est"] = round(u["prompt_tokens"] * PRICE_IN + u["completion_tokens"] * PRICE_OUT, 7)
        text = (response.choices[0].message.content or "").strip()
        with lock:
            print(f"{q['id']}: {text[:90]!r}")
        return {"id": q["id"], "answer": text, "contexts": contexts,
                "retrieved_doc_ids": row["retrieved_doc_ids"][:TOP_DOCS], "usage": u,
                "latency_s": round(latency, 3)}

    with ThreadPoolExecutor(THREADS) as pool:
        rows = list(pool.map(answer, questions))

    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {out_path}  ({llm.usage_summary()})")

    if args.predictions:
        pred_path = HERE / "predictions_test.jsonl"
        with pred_path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"id": r["id"], "answer": r["answer"],
                                    "retrieved_doc_ids": r["retrieved_doc_ids"][:TOP_DOCS]}, ensure_ascii=False) + "\n")
        print(f"wrote {pred_path}")


if __name__ == "__main__":
    main()
