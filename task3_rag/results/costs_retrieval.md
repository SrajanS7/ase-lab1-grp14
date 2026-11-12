# Indexing time and retrieval latency per configuration

| config | n_chunks | index_chunk_s | index_embed_s | index_bm25_s | index_crossencoder_load_s | latency_median_s | latency_p95_s | latency_local_median_s | llm_tokens_per_query | n_queries | n_excluded_rate_limited |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 3292 | 0.270 | 0.000 | 0.051 | 0.000 | 0.003 | 0.004 | 0.003 | 0 | 250 | 0 |
| R2 | 2782 | 0.020 | 41.710 | 0.000 | 0.000 | 0.128 | 0.257 | 0.001 | 0 | 248 | 2 |
| R3 | 3292 | 0.270 | 37.000 | 0.000 | 0.000 | 0.128 | 0.257 | 0.001 | 0 | 248 | 2 |
| R4 | 3292 | 0.270 | 37.000 | 0.051 | 0.000 | 0.132 | 0.262 | 0.004 | 0 | 248 | 2 |
| R5 | 3292 | 0.270 | 37.000 | 0.051 | 4.210 | 0.247 | 0.388 | 0.121 | 0 | 248 | 2 |
