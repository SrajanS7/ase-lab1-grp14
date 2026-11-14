# Task 3 – RAG under the microscope: report

> Every number in this file comes from a file in `results/`; the file is named next to the number.
> Regenerate all tables with `uv run python task3_rag/make_tables.py`. Inspect any single question with
> `uv run python task3_rag/show_case.py <id> [--config r3]`.
>
> §5.1, §6 and §7 were drafted with Claude Code from the result files and verified by Cemon, Shrividya and Rachana.

## 0. Setup

| | |
|---|---|
| Chat / generator / judge model | `openai.gpt-6-luna` for all runs (the fallback model was not used) |
| Embedding model | `kiconnect.qwen3-embedding-8b` (4096 dims, query instruction prefix) |
| Cross-encoder (R5) | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local, CPU) |
| Retrieval k | 30 chunks per search, mapped to doc ids and deduplicated; the top 5 documents count |
| Tuning split | chunk size and α chosen on dev questions with odd ids (`tune`); reported on all dev questions and on even ids (`held-out`) |
| Runs | 2026-11-12, on top of commit `b6724be`; the Task 3 code as run is in commit `f0ca7ca` (`results/run_info.json`) |

## 1. Configurations

| Config | Chunking | Scoring | Notes |
|---|---|---|---|
| R1 | recursive, 64 tok, 16 tok overlap (same chunks as R3) | BM25Okapi, identifier-aware tokenizer | `PQ-E4663` is indexed whole and as `pq`, `e4663` |
| R2 | fixed-size, 260 chars ≈ 64 tok, 65 chars ≈ 16 tok overlap | dense cosine | window cuts anywhere, never across documents |
| R3 | recursive (headings → paragraphs → lines → sentences → words), 64 tok, 16 tok overlap | dense cosine | |
| R4 | = R1 + R3 chunks | 0.6·minmax(cos) + 0.4·minmax(BM25) | min-max per query over all chunks; weight: §3 |
| R5 | = R4 top 30 chunks | cross-encoder re-ranking | |

Every chunk starts with a metadata header `[title | type | version]` (allowed by the task README).

## 2. Retrieval results (dev, k = 5)

Source: `results/table_retrieval.md`, `results/metrics_r*_k5.json`. R = context recall@5, P = context
precision@5 (`evaluate.py`), 30 answerable questions per category; held-out = even ids only.

| config | identifier R / P | paraphrase R / P | multi-hop R / P | version R / P | **overall R / P** | held-out R / P | overall R@10 |
|---|---|---|---|---|---|---|---|
| R1 BM25 | 0.967 / 0.731 | 0.233 / 0.124 | 0.433 / 0.698 | 0.700 / 0.653 | **0.583 / 0.552** | 0.509 / 0.495 | 0.717 |
| R2 fixed+dense | 0.633 / 0.600 | 0.900 / 0.768 | 0.033 / 0.022 | 0.867 / 0.656 | **0.608 / 0.511** | 0.612 / 0.533 | 0.633 |
| R3 recursive+dense | 0.700 / 0.562 | 0.867 / 0.778 | 0.000 / 0.000 | 0.933 / 0.744 | **0.625 / 0.521** | 0.655 / 0.544 | 0.637 |
| R4 hybrid | 0.933 / 0.732 | 0.667 / 0.477 | 0.350 / 0.577 | 0.767 / 0.626 | **0.679 / 0.603** | 0.647 / 0.602 | 0.792 |
| R5 hybrid+rerank | 0.633 / 0.498 | 0.500 / 0.288 | 0.267 / 0.533 | 1.000 / 0.911 | **0.600 / 0.558** | 0.595 / 0.567 | 0.683 |

Which approach wins where, and why:

- **Identifier: BM25 (0.967).** Exact codes like `PQ-E4007` are rare tokens with high IDF. Embeddings see
  "PQ-E4007 after upgrade?" and "PQ-E2771 after upgrade?" as nearly the same text.
- **Paraphrase: dense (R2 0.900, R3 0.867).** The questions describe a component ("the part that holds back
  messages until a later point in time") instead of naming it (`scheduler`). BM25 has no shared words to match (0.233).
- **Multi-hop: BM25 (0.433), dense 0.000.** The 120 incident reports are near-identical templates, so their
  embeddings cannot tell `INC-2024-183` from `INC-2024-214`; only an exact keyword match finds the right report.
  No configuration finds the second document (see §6, Failure 4).
- **Version: R5 (1.000), R3 (0.933).** Our interpretation: the cross-encoder reads question and chunk together, so
  "2.x" in the question can match "2.x" in the page title; we did not test this separately.
- **Overall: R4 (0.679 / 0.603).** It keeps most of BM25's identifier strength and most of dense search's paraphrase
  strength. R5 is worse overall: the web-trained cross-encoder prefers chatty forum posts over the exact
  error-catalogue page (identifier 0.933 → 0.633).

### Chunk size (R3)

Source: `results/chunk_size.md`. Dense retrieval, 16-token overlap, recall@5 / precision@5 on all answerable dev questions.

| Config | Size (tokens) | Chunks | Recall | Precision | Version recall | Tune recall |
|---|---|---|---|---|---|---|
| R3 recursive | **64** | 3,292 | **0.625** | 0.521 | **0.933** | **0.597** |
| R3 recursive | 128 | 1,324 | 0.592 | 0.512 | 0.800 | 0.581 |
| R3 recursive | 256 | 958 | 0.596 | 0.516 | 0.800 | 0.581 |
| R2 fixed | 64 | 2,782 | 0.608 | 0.511 | 0.867 | 0.605 |
| R2 fixed | 128 | 1,309 | 0.575 | 0.498 | 0.733 | 0.565 |
| R2 fixed | 256 | 962 | 0.558 | 0.511 | 0.667 | 0.532 |

- The documents are short (about 130 tokens on average; only the 14 changelogs are long), so at 256 tokens almost
  every document is one chunk (958 chunks for 932 documents).
- 64 tokens won on the tune split, mainly through version questions (0.933 vs 0.800). Likely reason (not tested
  separately): a small chunk holding just the defaults table of the 2.x page matches "default in 2.x" better than the
  whole page, whose embedding also carries the generic "when to change it" text.
- The differences are small. On the tune split one question is worth about 0.017, so 0.597 vs 0.581 is one question.
- Recursive beats fixed-size at every size on all dev questions, most clearly at 256 tokens (0.596 vs 0.558),
  where fixed windows cut documents at arbitrary points (lecture slide 33).
- Small chunks have a cost at generation time: the chunk that matched is often not the one with the answer (§5).

## 3. Hybrid weight

Source: `results/hybrid_weight_sweep.csv` (sweep on the `tune` split only). R4 = α·dense + (1−α)·BM25; α = 0 is
pure BM25, α = 1 pure dense.

- **Chosen α: 0.6.** On the tune split, α = 0.5 and 0.6 tie on recall@5 (0.710); 0.6 has the higher precision@5
  (0.604 vs 0.599).
- **Curve shape:** recall rises from 0.653 (α = 0) to a plateau of 0.70–0.71 between α = 0.5 and 0.8, then drops to
  0.597 at α = 1. Both extremes are worse than any mix.
- **Per-category behaviour (tune split):** moving α from 0 to 1 trades categories against each other. Paraphrase rises
  from 0.18 to 0.82 and version from 0.73 to 0.93; identifier falls from 1.00 to 0.70 and multi-hop from 0.47 to 0.00.
  α = 0.6 is the last value that keeps identifier at 1.00 while paraphrase has already reached 0.64.
- **Held-out check:** α = 0.6 gives 0.647 on the held-out split. The held-out optimum is α = 0.8 (0.767), which on the
  tune split scores 0.702, almost the same as 0.6. With about 60 questions per half the optimum is not sharp:
  anything from 0.5 to 0.8 is defensible, and α = 0.6 is a reasonable choice rather than an exact one. We did not
  re-tune on the held-out half, because then it would no longer be held out.

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

Source: `results/table_judge.md`, `results/judge_r4.jsonl`, `results/judge_r3.jsonl` and the `_chunkctx` versions.
R4 and R3 are the two best configurations by overall recall@5. Both answered the same 50 stratified dev questions
(10 per category), in two variants of the generator context:

- **chunks only** (first run, files `*_chunkctx*`): the retrieved 64-token chunks of the top-5 documents.
- **whole documents** ("small-to-big", final): the full text of the top-5 documents; only the long changelogs are
  cut to their retrieved chunks.

| Config | Context | identifier | paraphrase | multi-hop | version | unanswerable | **correctness** | abstained | faithfulness |
|---|---|---|---|---|---|---|---|---|---|
| R4 | chunks only | 0.60 | 0.70 | 0.00 | 0.70 | 1.00 | **0.60** | 56 % | 1.000 |
| R4 | whole documents | 0.90 | 0.70 | 0.00 | 1.00 | 1.00 | **0.72** | 44 % | 1.000 |
| R3 | chunks only | 0.40 | 1.00 | 0.00 | 0.90 | 1.00 | **0.66** | 54 % | 1.000 |
| R3 | whole documents | 0.60 | 1.00 | 0.00 | 1.00 | 1.00 | **0.72** | 48 % | 1.000 |

- Whole documents raised R4 from 0.60 to 0.72: with chunks only, the generator often saw the first chunk of the right
  error page but not its "How to fix" section, and correctly abstained. Example `dev077` (PQ-E2425): the error page
  `d0522` is at rank 3 in both runs. With chunks only, the generator got its first 64-token chunk (238 characters:
  title, log message, severity) and answered "The documentation does not contain this information."; with whole
  documents it got all 567 characters including "How to fix" and answered correctly
  (`results/answers_r4_dev_sample50_chunkctx.jsonl` vs `results/answers_r4_dev_sample50.jsonl`). By the slide-45
  test this is a chunk-level retrieval failure: the right document was in the prompt, the right chunk was not. Small
  chunks are good for finding a document (§2) and bad for answering from it.
- R4 and R3 tie at 0.72 overall, but each wins where its retrieval was strong: R4 on identifiers (0.90 vs 0.60), R3 on
  paraphrases (1.00 vs 0.70). What retrieval misses, the generator cannot answer.
- Faithfulness is 1.000 in all runs, and unanswerable questions are 10/10: the generator never invented a value.
  Every multi-hop question ends in an abstention (0/10).

### 5.1 Ten hand-checked judge decisions

Selection: 2 per category, from both configs (whole-document runs), covering all three metrics; 6 of the 10 answers
are abstentions and 5 have judge verdict `wrong`. Each answer was graded by us before reading the judge's verdict.
Checked with `show_case.py`. Checked by: Cemon, Shrividya and Rachana.

| # | id | cfg | category | judge metric | judge verdict | our verdict | agree? | why we (dis)agree |
|---|---|---|---|---|---|---|---|---|
| 1 | dev068 | R3 | identifier | correctness | wrong (0) | wrong | yes | Answer is the abstention, but the fix is documented (`d0033`). The error page was not in R3's top 20: dense search returned forum posts about other error codes (PQ-E4371, PQ-E2771, PQ-E3249). |
| 2 | dev068 | R4 | identifier | answer relevance | 0.463 | fully relevant, should be high | **no** | The answer is word for word the ground truth, yet it scores the same as R3's useless abstention on the same question (0.467). The judge's generated questions ask about `broker.compression` and never mention PQ-E4007, so they embed far from the original question. |
| 3 | dev004 | R4 | paraphrase | correctness | wrong (0) | wrong | yes | Abstention, but the setting `dlq.max_retries` is documented. Its pages ranked 10 and 12 in R4 (1 and 3 in R3), so the generator never saw them (§6, Failure 1). |
| 4 | dev130 | R4 | paraphrase | faithfulness | 1.0 (1/1 claim supported) | 0: not supported | **no** | The context (1.2 release notes) only says "New error code PQ-E4013 (AUTH_THROTTLED)". It does not say that this code is reported when clients are turned away by the login check. The judge split the answer into the claim "PaderQueue reports PQ-E4013" and dropped the situation, so the unsupported part was never checked. Correctness (wrong) is right. |
| 5 | dev025 | R4 | multi-hop | correctness | wrong (0) | wrong | yes | The fix (raise `metrics.threshold_percent`, add capacity) is documented, so abstaining is wrong. Borderline: the incident report in the context names PQ-E1915, so an answer giving the cause would have been partially correct. |
| 6 | dev042 | R4 | multi-hop | correctness | wrong (0) | wrong | yes | Ground truth `lz4` is documented. Neither the PQ-E2051 error page nor the `compactor.compression` page was retrieved; all 5 contexts are forum posts. Abstaining was the honest reaction, but the answer is wrong. |
| 7 | dev037 | R4 | version | faithfulness | 1.0 (2/2 claims supported) | 1.0 | yes | "Changed in 4.0" is in context 2 (4.x page: "Default changed: in 4.0") and context 3 (4.0 release notes). "Previously 30000" is in context 3 ("now defaults to 10000 (previously 30000)") and context 1 (3.x page, default `30000`). |
| 8 | dev009 | R4 / R3 | version | answer relevance | R4 0.958, R3 0.601 | both fully relevant | **no** | Both answers state the same correct fact (`none`). R3 said only "The default was `none`.", so the judge's questions became generic ("What was the default value?") and the score fell by a third. Relevance measures phrasing, not whether the question was answered. |
| 9 | dev013 | R4 | unanswerable | correctness | correct (1) | correct | yes | `spool.queue_capacity` appears in 0 documents (`grep -c` on the corpus), although 10+ similar settings exist (`acktracker.queue_capacity`, `compactor.queue_capacity`, …). Abstaining is right; the model did not fall for the near match. |
| 10 | dev075 | R4 | unanswerable | correctness | correct (1) | correct | yes | Error code PQ-E4839 appears in 0 documents. It is an invented code that looks like the real ones; abstaining is right. |

**Agreement: 7/10.** By metric: correctness 6/6, faithfulness 1/2, answer relevance 0/2.

How much we trust the judge:

- **Correctness: trustworthy.** It compares the answer with a ground truth, and in all six checks, including the
  abstentions and the unanswerable questions, we reached the same verdict.
- **Faithfulness: too lenient.** The judge splits an answer into claims and may drop the part that is not supported
  (row 4). A score of 1.0 also says nothing about usefulness: `dev062` ("apply the fix from the error catalogue;
  the excerpts don't specify it") is fully faithful and useless.
- **Answer relevance: least trustworthy.** It depends on phrasing (row 8) and on whether the answer repeats the
  identifier from the question (row 2). For abstentions it is meaningless by construction: the relevance prompt
  contains only the answer, so every abstention sends the identical request, the gateway returns the cached reply,
  and the judge invents the same three unrelated questions ("What is the maximum number of users supported?" …).
  Correct and wrong abstentions therefore score alike (R4: 0.433 vs 0.379, `results/judge_r4.jsonl`).
- **Self-evaluation:** generator and judge are the same model (`gpt-6-luna`). We did not test a second judge model,
  so a shared bias cannot be ruled out.

## 6. Failure analysis (best config: R4, whole-document context)

Diagnosis rule (lecture slide 45): is the right chunk in the prompt given to the generator?
No → **retrieval failure**. Yes → **generation failure**.

R4 gets all version and unanswerable questions in the judge sample right, so the failures come from paraphrase,
identifier and multi-hop. Ranks are R4's document ranks from `results/retrieval_r4_dev.jsonl`; only ranks 1–5 reach
the generator.

### Failure 1 – `dev004` (paraphrase)

- **Question:** Which setting controls how many times the mechanism that parks messages nobody could process tries
  again before it gives up?
- **Ground truth:** `dlq.max_retries`
- **Our answer:** The documentation does not contain this information.
- **Gold doc(s):** `d0303` `dlq.max_retries (3.x)` rank 10, `d0032` `dlq.max_retries` rank 12. In R3 (dense only):
  rank 1 and 3.
- **Top-5 retrieved:** PaderQueue 1.1 release notes, `acktracker.max_retries`, `acktracker.timeout_ms`,
  PaderQueue 3.0 release notes, `spool.max_retries`.
- **Type:** retrieval failure.
- **Why:** The question describes the dead-letter queue without naming it. Dense search understood it: the right pages
  have the highest dense score (0.6 × dense = 0.600, best of all chunks). BM25 ranked them 573rd and 656th of 3,292
  chunks, because the only words shared with the question are `before`, `messages`, `that`, `the`: the tokenizer has
  no stemming ("tries" ≠ "retries") and no stopword list. Other retry/timeout pages and long changelogs share more
  filler words, so their BM25 part (0.4 × BM25 up to 0.338 vs 0.192) outweighed the dense lead (+0.07).
- **What would fix it:** a higher α (the held-out split preferred 0.8), stopword removal and stemming in the BM25
  tokenizer, or query rewriting that maps the description to "dead-letter queue (dlq)".

### Failure 2 – `dev130` (paraphrase)

- **Question:** Which error code does PaderQueue report when new clients are turned away by the login check for
  connecting clients?
- **Ground truth:** PQ-E4030 (AUTH_CONNECTION_LIMIT_REACHED)
- **Our answer:** "PaderQueue reports `PQ-E4013` (`AUTH_THROTTLED`)." (wrong)
- **Gold doc(s):** `d0435` `PQ-E4030 AUTH_CONNECTION_LIMIT_REACHED` rank 10. R3: rank 1.
- **Top-5 retrieved:** five changelogs (PaderQueue 1.0, 1.2, 2.0, 2.1, 2.2 release notes).
- **Type:** retrieval failure, which turned into a confident wrong answer instead of an abstention.
- **Why:** The long changelogs mention many components and error codes, so BM25 scores them high on generic words. The
  1.2 release notes contain "New error code PQ-E4013 (AUTH_THROTTLED)", an auth error, but a different one. The
  generator took the closest fact it saw. The judge rated this answer fully faithful (§5.1, row 4).
- **What would fix it:** a higher α; boosting or filtering documents of type `error` when the question asks for an
  error code (metadata filtering, lecture slide 30); a stricter prompt ("answer only if the excerpt describes this
  exact situation").

### Failure 3 – `dev062` (identifier)

- **Question:** What should I do when PaderQueue logs PQ-E3177?
- **Ground truth:** Check for connection leaks in clients; if the load is legitimate, increase
  `scheduler.max_connections`.
- **Our answer:** "Apply the fix recommended in the error catalogue for `PQ-E3177`; the excerpts don't specify what
  that fix is. Add an alert for `PQ-E3177`."
- **Gold doc(s):** `d0227` `PQ-E3177 SCHEDULER_CONNECTION_LIMIT_REACHED` rank 6, one place below the cut-off.
  R1: rank 4; R3: not in top 20.
- **Top-5 retrieved:** forum "PQ-E3177 after upgrade?" and four incident reports (INC-2025-168, -101, -198, -142),
  all mentioning PQ-E3177.
- **Type:** retrieval failure (k cut-off).
- **Why:** Every document that mentions the code competes with the one page that explains it. Four incident reports
  scored higher than the error-catalogue page. The generator answered honestly from what it saw: the incidents only
  say "apply the fix recommended in the error catalogue".
- **What would fix it:** pass more documents to the generator (k = 6–8; documents are short, so this costs about
  130 tokens each), or boost type `error` for "what should I do when … logs …" questions.

### Failure 4 – `dev025` (multi-hop)

- **Question:** Which permanent fix does the documentation recommend for the error that caused incident INC-2024-171?
- **Ground truth:** The incident was caused by PQ-E1915. Fix: add capacity or reduce load; as a last resort, raise
  `metrics.threshold_percent`.
- **Our answer:** The documentation does not contain this information.
- **Gold doc(s):** hop 1 `d0253` Postmortem INC-2024-171 rank 1 (in the context); hop 2 `d0183`
  `PQ-E1915 METRICS_THROTTLED` rank 505 of 932 documents.
- **Top-5 retrieved:** Postmortem INC-2024-171, -136, -160, -154, -217.
- **Type:** retrieval failure (second hop). A minor generation weakness on top: the model could have given the cause.
- **Why:** The question names the incident, not the error. The link to the error page exists only inside the incident
  report ("Apply the fix recommended in the error catalogue for PQ-E1915"), and a single search cannot follow it.
  The generator saw that sentence and refused to guess the fix, which is correct, but by abstaining it also dropped
  the part it could answer (the cause, PQ-E1915), which would probably have earned partial credit.
  This explains all 10 multi-hop questions scoring 0 in every configuration.
- **What would fix it:** iterative retrieval: read the first result, extract the error code, search again for
  "PQ-E1915 how to fix". That turns the fixed pipeline into an agent loop, as in Task 2. A prompt that allows partial
  answers ("give the part you can support and say what is missing").

### Failure 5 – `dev042` (multi-hop)

- **Question:** What is the current default value of the setting that the error catalogue recommends changing for
  PQ-E2051?
- **Ground truth:** `compactor.compression` defaults to `lz4` in 4.x.
- **Our answer:** The documentation does not contain this information.
- **Gold doc(s):** hop 1 `d0738` `PQ-E2051 COMPACTOR_CODEC_MISMATCH` rank 150 of 932 documents (R1: rank 10,
  R3: rank 545); hop 2 `d0564` `compactor.compression` rank 752.
- **Top-5 retrieved:** five forum threads: "PQ-E2051 after upgrade?", "Getting 'compactor: codec mismatch' all the
  time", two "Help: compactor: codec mismatch in production", "PQ-E2154 after upgrade?".
- **Type:** retrieval failure on both hops.
- **Why:** Two problems at once. Hop 1 is crowded out: 11 documents mention PQ-E2051 (forum threads, incidents, a
  changelog), and the forum threads repeat the code and the log message `compactor: codec mismatch`, so they beat the
  one error-catalogue page, which dense search ranks only 545th. Hop 2 cannot be found at all, because the setting
  name `compactor.compression` appears only in the error page's "How to fix" section, never in the question. The
  forum threads suggested the wrong setting (`compactor.log_level`); the generator did not repeat it and abstained.
- **What would fix it:** a boost or filter for type `error` when the question says "error catalogue" (metadata,
  lecture slide 30), which would bring hop 1 into the context; then iterative retrieval to look up the setting the
  error page names (hop 2).

Pattern across the five:

- **Retrieval is the bottleneck, not generation.** In all five the information needed for the answer did not reach the
  generator. The generator never invented an answer from nothing: it abstained, or (Failure 2) used a related fact
  that really was in the context. In Failure 5 it even ignored wrong advice from the forum.
- **Hybrid weighting hurts paraphrases** (Failures 1, 2): dense search alone ranked both gold pages first. This matches
  the held-out split preferring α = 0.8.
- **Crowding:** many documents mention a code, one explains it (Failures 3, 5). The explaining page is the
  error-catalogue page every time, so a document-type boost would help both.
- **Multi-hop needs a second search step** (Failures 4, 5); no change to chunking, α or re-ranking can fix it.

## 7. Recommendation

- **Deploy: R4 (hybrid, α = 0.6) with whole-document context.**
- **Because:**
  - Best retrieval quality: recall@5 0.679 and precision@5 0.603, the highest of all five; 0.792 recall@10.
  - No category collapses: identifier 0.933, paraphrase 0.667, version 0.767, multi-hop 0.350 (the best after BM25).
    R3 fails identifier questions (0.700) and every multi-hop question (0.000); support questions are full of error
    codes and setting names, so that weakness matters in practice.
  - End to end it ties R3 at 0.72 correctness with 1.000 faithfulness and 10/10 unanswerable questions correctly
    refused; it does not invent answers.
  - Cost and latency are no obstacle: about 130 ms retrieval, about 2.5 s end to end, about $0.00012 per question.
    BM25 adds 0.05 s indexing and 3 ms per query on top of dense search.
- **Not chosen:**
  - **R5:** re-ranking costs 120 ms more per query and lowers recall from 0.679 to 0.600; the web-trained cross-encoder
    ranks forum posts above the exact error-catalogue page (identifier 0.933 → 0.633). It wins only version questions.
  - **R3:** ties end to end but is fragile on identifiers and blind to multi-hop.
  - **R1, R2:** each strong in only one or two categories (BM25 paraphrase 0.233; R2 multi-hop 0.033).
- **What we would add next**, in order of effort:
  1. Re-tune α on a fresh split, likely towards 0.7–0.8 (held-out evidence, Failures 1–2).
  2. BM25 tokenizer with stopword removal and stemming (Failure 1).
  3. Pass 6–8 documents instead of 5, and boost type `error` for error-code questions (Failures 2, 3, 5).
  4. Iterative retrieval for multi-hop questions: extract the identifier from the first result and search again
     (Failures 4, 5). This is the only change that addresses the weakest category.
  5. Let the generator give partial answers instead of all-or-nothing abstentions (Failure 4). On the test set, 51 of
     100 answers are abstentions, more than the expected share of unanswerable questions.

## 8. Test predictions

`predictions_test.jsonl`: config R4 with whole-document context, 100 lines, 5 `retrieved_doc_ids` each, generated
2026-11-12 (`results/answers_r4_test.jsonl`). 51 of the 100 answers are abstentions. Cost: $0.0121 for 100
questions (`results/costs_generation.csv`).
