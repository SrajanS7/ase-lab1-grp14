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

Source: `results/table_costs.md` (built by `make_tables.py` from `results/costs_retrieval.csv`,
`results/answers_*_dev_sample50.jsonl` and the retrieval files). Indexing and latency from the first (uncached)
run on 2026-11-12. **m** = measured by the gateway, **e** = estimated: the exact prompt `generate.py` would
send, counted locally with tiktoken (no API call); completion tokens and generation latency for R1/R2/R5 are
the mean of the measured R3/R4 runs.

| Config | Indexing time | Retrieval latency (median / p95) | Query embedding tokens | Generator prompt tokens | Completion tokens | $ / query | End-to-end latency |
|---|---|---|---|---|---|---|---|
| R1 BM25 | 0.3 s (chunk + BM25) | 3 ms / 4 ms | 0 | 961 e | 40 e | $0.000116 e | 2.26 s e |
| R2 fixed + dense | 41.7 s (embedding 2,782 chunks) | 128 ms / 257 ms | 42 | 1,066 e | 40 e | $0.000126 e | 2.39 s e |
| R3 recursive + dense | 37.3 s (embedding 3,292 chunks) | 128 ms / 257 ms | 42 | 1,028 m (1,022 e) | 38 m | $0.000122 m | 2.27 s m |
| R4 hybrid | 37.3 s (embedding + BM25) | 132 ms / 262 ms | 42 | 1,002 m (995 e) | 41 m | $0.000121 m | 2.51 s m |
| R5 hybrid + rerank | 37.3 s + 4.2 s cross-encoder load | 247 ms / 388 ms | 42 | 1,001 e | 40 e | $0.000120 e | 2.51 s e |

What the numbers say:

- **Indexing:** BM25 indexes the corpus in 0.3 s; anything with embeddings needs about 40 s for 200k tokens
  through the gateway, once (then cached on disk). Embeddings are free on the gateway.
- **Retrieval latency:** BM25 runs locally in 3 ms. Dense search costs about 130 ms, almost all of it the network
  request that embeds the question; the cosine search over 3,292 vectors takes about 1 ms. The cross-encoder adds
  about 120 ms on a laptop CPU.
- **Tokens:** retrieval uses no LLM tokens in any configuration. The generator prompt is about 1,000 tokens for every
  configuration because all pass five short whole documents (R1 lowest at 961, R2 highest at 1,066). Our local
  estimate is within 1 % of the gateway's count for R3/R4, so the estimates for R1/R2/R5 are trustworthy.
- **End to end:** generation (about 2.1–2.4 s) dominates; even R5's slower retrieval adds only about 0.1 s.
  At about $0.00012 per answered question, 10,000 questions would cost about $1.20. Cost does not separate
  the configurations; quality does.

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
