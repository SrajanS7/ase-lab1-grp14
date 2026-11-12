# End-to-end results (judge.py --sample 50)

faithfulness: mean over answers that make claims (abstentions not scored). correctness: 1 / 0.5 / 0 (correct / partial / wrong).

| config | category | n | abstained | faithfulness | answer relevance | correctness |
|---|---|---|---|---|---|---|
| r3 | paraphrase | 10 | 0.000 | 1.000 | 0.721 | 1.000 |
| r3 | identifier | 10 | 0.400 | 1.000 | 0.687 | 0.600 |
| r3 | version | 10 | 0.000 | 1.000 | 0.777 | 1.000 |
| r3 | unanswerable | 10 | 1.000 | – | 0.433 | 1.000 |
| r3 | multi-hop | 10 | 1.000 | – | 0.372 | 0.000 |
| r3 | **overall** | 50 | 0.480 | 1.000 | 0.598 | 0.720 |
| r3_chunkctx | paraphrase | 10 | 0.000 | 1.000 | 0.706 | 1.000 |
| r3_chunkctx | identifier | 10 | 0.600 | 1.000 | 0.615 | 0.400 |
| r3_chunkctx | version | 10 | 0.100 | 1.000 | 0.737 | 0.900 |
| r3_chunkctx | unanswerable | 10 | 1.000 | – | 0.423 | 1.000 |
| r3_chunkctx | multi-hop | 10 | 1.000 | – | 0.369 | 0.000 |
| r3_chunkctx | **overall** | 50 | 0.540 | 1.000 | 0.570 | 0.660 |
| r4 | paraphrase | 10 | 0.200 | 1.000 | 0.665 | 0.700 |
| r4 | identifier | 10 | 0.000 | 1.000 | 0.706 | 0.900 |
| r4 | version | 10 | 0.000 | 1.000 | 0.752 | 1.000 |
| r4 | unanswerable | 10 | 1.000 | – | 0.433 | 1.000 |
| r4 | multi-hop | 10 | 1.000 | – | 0.370 | 0.000 |
| r4 | **overall** | 50 | 0.440 | 1.000 | 0.585 | 0.720 |
| r4_chunkctx | paraphrase | 10 | 0.200 | 1.000 | 0.637 | 0.700 |
| r4_chunkctx | identifier | 10 | 0.300 | 1.000 | 0.667 | 0.600 |
| r4_chunkctx | version | 10 | 0.300 | 1.000 | 0.684 | 0.700 |
| r4_chunkctx | unanswerable | 10 | 1.000 | – | 0.428 | 1.000 |
| r4_chunkctx | multi-hop | 10 | 1.000 | – | 0.369 | 0.000 |
| r4_chunkctx | **overall** | 50 | 0.560 | 1.000 | 0.557 | 0.600 |
