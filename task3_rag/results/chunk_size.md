# Chunk size comparison (dense retrieval, k=5)

| config | chunking | size_tokens | overlap_tokens | n_chunks | tune_recall | tune_precision | tune_recall_identifier | tune_recall_paraphrase | tune_recall_multi-hop | tune_recall_version | heldout_recall | heldout_precision | heldout_recall_identifier | heldout_recall_paraphrase | heldout_recall_multi-hop | heldout_recall_version | all_recall | all_precision | all_recall_identifier | all_recall_paraphrase | all_recall_multi-hop | all_recall_version |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R3 | recursive | 64 | 16 | 3292 | 0.597 | 0.499 | 0.700 | 0.818 | 0.000 | 0.933 | 0.655 | 0.544 | 0.700 | 0.895 | 0.000 | 0.933 | 0.625 | 0.521 | 0.700 | 0.867 | 0.000 | 0.933 |
| R2 | fixed | 64 | 16 | 2782 | 0.605 | 0.491 | 0.700 | 0.818 | 0.031 | 0.933 | 0.612 | 0.533 | 0.500 | 0.947 | 0.036 | 0.800 | 0.608 | 0.511 | 0.633 | 0.900 | 0.033 | 0.867 |
| R3 | recursive | 128 | 16 | 1324 | 0.581 | 0.485 | 0.750 | 0.818 | 0.000 | 0.800 | 0.603 | 0.541 | 0.600 | 0.895 | 0.000 | 0.800 | 0.592 | 0.512 | 0.700 | 0.867 | 0.000 | 0.800 |
| R2 | fixed | 128 | 16 | 1309 | 0.565 | 0.485 | 0.750 | 0.727 | 0.062 | 0.733 | 0.586 | 0.512 | 0.600 | 0.895 | 0.000 | 0.733 | 0.575 | 0.498 | 0.700 | 0.833 | 0.033 | 0.733 |
| R3 | recursive | 256 | 16 | 958 | 0.581 | 0.493 | 0.750 | 0.818 | 0.000 | 0.800 | 0.612 | 0.541 | 0.600 | 0.895 | 0.036 | 0.800 | 0.596 | 0.516 | 0.700 | 0.867 | 0.017 | 0.800 |
| R2 | fixed | 256 | 16 | 962 | 0.532 | 0.489 | 0.750 | 0.818 | 0.000 | 0.600 | 0.586 | 0.534 | 0.600 | 0.895 | 0.000 | 0.733 | 0.558 | 0.511 | 0.700 | 0.867 | 0.000 | 0.667 |
