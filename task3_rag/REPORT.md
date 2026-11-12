# Task 3 – RAG under the microscope: report

> Every number in this file comes from a file in `results/`. Cite the file next to the number.
> Regenerate all tables with `uv run python task3_rag/make_tables.py`.

## 0. Setup

| | |
|---|---|
| Chat / generator model | `openai.gpt-6-luna` (fallback used? yes/no – which runs) |
| Embedding model | `kiconnect.qwen3-embedding-8b` (4096 dims, query instruction prefix) |
| Cross-encoder (R5) | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Retrieval k | 5 documents (chunks mapped to doc ids, deduplicated) |
| Tuning split | dev questions with odd id number (`tune`), reported on all 150 + even ids (`held-out`) |
| Git commit of the runs | `<hash>` (also in `results/run_info.json`) |

## 1. Configurations

| Config | Chunking | Scoring | Notes |
|---|---|---|---|
| R1 | recursive, `<size>` tok, `<overlap>` tok overlap (same chunks as R3) | BM25Okapi, identifier-aware tokenizer | |
| R2 | fixed-size, `<chars>` chars ≈ `<size>` tok, same overlap | dense cosine | |
| R3 | recursive (headings → paragraphs → lines → sentences → words) | dense cosine | |
| R4 | = R1 + R3 chunks | α·minmax(cos) + (1−α)·minmax(BM25), α = `<α>` | weight justification: §3 |
| R5 | = R4 top-`<N>` chunks | cross-encoder re-ranking | |

## 2. Retrieval results (dev, k = 5)

Source: `results/table_retrieval.md` (generated). Paste it here.

### Chunk size (R3)

Source: `results/table_chunk_size.md`. What changed and why (2–4 sentences):

-

## 3. Hybrid weight

Source: `results/hybrid_weight_sweep.csv` (sweep on the `tune` split only).

- Chosen α:
- Why (curve shape, per-category behaviour, held-out check):

## 4. Cost and latency

Source: `results/costs.csv`. First (uncached) run, measured on `<date>`.

| Config | Indexing time | Latency / query (median, p95) | Tokens / query (retrieval) | Tokens / query (end-to-end) | $ / query |
|---|---|---|---|---|---|

## 5. End-to-end evaluation (judge.py --sample 50)

Source: `results/judge_<cfg>.jsonl`, `results/table_judge.md`.

### 5.1 Ten hand-checked judge decisions

Selection rule: 2 per category, from both configs, including at least 3 judge verdicts of `partial`/`wrong`
and at least 1 abstention. Checked by: `<name>`.

| # | id | cfg | category | judge metric | judge verdict | our verdict | agree? | why we (dis)agree |
|---|---|---|---|---|---|---|---|---|
| 1 | | | | correctness | | | | |
| 2 | | | | correctness | | | | |
| 3 | | | | correctness | | | | |
| 4 | | | | correctness | | | | |
| 5 | | | | correctness | | | | |
| 6 | | | | correctness | | | | |
| 7 | | | | faithfulness (claims) | | | | |
| 8 | | | | faithfulness (claims) | | | | |
| 9 | | | | faithfulness (claims) | | | | |
| 10 | | | | answer relevance | | | | |

Agreement: `x/10`. How much we trust the judge, and on which metric least:

-

## 6. Failure analysis (best config: `<cfg>`)

Diagnosis rule (lecture slide 45): is a gold document in the chunks given to the generator?
No → **retrieval failure**. Yes → **generation failure**.

### Failure 1 – `<id>` (`<category>`)

- **Question:**
- **Ground truth:**
- **Our answer:**
- **Gold doc(s):** `<ids>` – rank in our retrieval: `<rank or "not in top 30">`
- **Top-5 retrieved:** `<ids + titles>`
- **Type:** retrieval / generation
- **Why:**
- **What would fix it:**

### Failure 2 – `<id>` (`<category>`)

(same fields)

### Failure 3 – `<id>` (`<category>`)

### Failure 4 – `<id>` (`<category>`)

### Failure 5 – `<id>` (`<category>`)

Pattern across the five:

-

## 7. Recommendation

- **Deploy:** `<cfg>`
- **Because:** (quality per category, cost, latency, operational complexity)
- **Not chosen:** `<cfg>` because …
- **What we would add next:** (e.g. metadata filter on `version`, multi-hop query decomposition)

## 8. Test predictions

`predictions_test.jsonl`: config `<cfg>`, 100 lines, 5 `retrieved_doc_ids` each, generated `<date>`.
