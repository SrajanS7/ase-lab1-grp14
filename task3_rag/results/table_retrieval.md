# Retrieval results on dev (k = 5 documents)

R = context recall@5, P = context precision@5 (evaluate.py). Chunk size 64 tokens, overlap 16, alpha = 0.6. n = 120 answerable questions (30 per category); held-out = the 60 answerable questions with even ids, not used for tuning.

| config | identifier R / P | paraphrase R / P | multi-hop R / P | version R / P | **overall R / P** | held-out R / P | overall R@10 |
|---|---|---|---|---|---|---|---|
| R1 BM25 | 0.967 / 0.731 | 0.233 / 0.124 | 0.433 / 0.698 | 0.700 / 0.653 | **0.583 / 0.552** | 0.509 / 0.495 | 0.717 |
| R2 fixed+dense | 0.633 / 0.600 | 0.900 / 0.768 | 0.033 / 0.022 | 0.867 / 0.656 | **0.608 / 0.511** | 0.612 / 0.533 | 0.633 |
| R3 recursive+dense | 0.700 / 0.562 | 0.867 / 0.778 | 0.000 / 0.000 | 0.933 / 0.744 | **0.625 / 0.521** | 0.655 / 0.544 | 0.637 |
| R4 hybrid | 0.933 / 0.732 | 0.667 / 0.477 | 0.350 / 0.577 | 0.767 / 0.626 | **0.679 / 0.603** | 0.647 / 0.602 | 0.792 |
| R5 hybrid+rerank | 0.633 / 0.498 | 0.500 / 0.288 | 0.267 / 0.533 | 1.000 / 0.911 | **0.600 / 0.558** | 0.595 / 0.567 | 0.683 |
