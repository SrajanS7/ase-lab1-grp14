# Cost and latency per configuration

Chunk size 64 tokens. Retrieval latency: first (uncached) query-embedding request + local compute, median / p95 over 248 dev+test queries (2 rate-limited queries excluded). Generation: gpt-6-luna, top-5 whole documents as context, 50 sampled dev questions. **m** = measured by the gateway, **e** = estimated (prompt counted locally with tiktoken; completion and generation latency = mean of the measured R3/R4 runs). Prices: $0.10 / $0.50 per 1M input / output tokens; embeddings are free.

| config | indexing time | corpus tokens embedded | retrieval latency median / p95 | query embedding tokens | generator prompt tokens | completion tokens | $ per query | end-to-end latency (median) |
|---|---|---|---|---|---|---|---|---|
| R1 BM25 | chunk 0.27 s, BM25 0.05 s | – | 3 ms / 4 ms | 0 | 961 e | 40 e | $0.000116 e | 2.26 s e |
| R2 fixed+dense | chunk 0.02 s, embed 41.7 s | 199,193 | 128 ms / 257 ms | 42 | 1066 e | 40 e | $0.000126 e | 2.39 s e |
| R3 recursive+dense | chunk 0.27 s, embed 37.0 s | 213,524 | 128 ms / 257 ms | 42 | 1028 m (1022 e) | 38 m | $0.000122 m | 2.27 s m |
| R4 hybrid | chunk 0.27 s, embed 37.0 s, BM25 0.05 s | 213,524 | 132 ms / 262 ms | 42 | 1002 m (995 e) | 41 m | $0.000121 m | 2.51 s m |
| R5 hybrid+rerank | chunk 0.27 s, embed 37.0 s, BM25 0.05 s, cross-encoder load 4.2 s | 213,524 | 247 ms / 388 ms | 42 | 1001 e | 40 e | $0.000120 e | 2.51 s e |

Query embedding tokens = instruction prefix + question, counted locally (mean over 250 questions). R1 sends no request. Retrieval itself uses no LLM tokens in any configuration.
