# Task 3 - Evaluation results

Automatically generated. Configuration selection/tuning uses the development set.

| Run | Category | Recall@5 | Precision@5 |
|---|---|---:|---:|
| R1_s256 | OVERALL | 0.533 | 0.560 |
| R1_s256 | identifier | 0.900 | 0.717 |
| R1_s256 | multi-hop | 0.500 | 0.831 |
| R1_s256 | paraphrase | 0.100 | 0.059 |
| R1_s256 | version | 0.633 | 0.633 |
| R2_s256 | OVERALL | 0.583 | 0.529 |
| R2_s256 | identifier | 0.767 | 0.700 |
| R2_s256 | multi-hop | 0.000 | 0.000 |
| R2_s256 | paraphrase | 0.933 | 0.783 |
| R2_s256 | version | 0.633 | 0.633 |
| R3_s128 | OVERALL | 0.425 | 0.362 |
| R3_s128 | identifier | 0.533 | 0.475 |
| R3_s128 | multi-hop | 0.000 | 0.000 |
| R3_s128 | paraphrase | 0.667 | 0.523 |
| R3_s128 | version | 0.500 | 0.450 |
| R3_s256 | OVERALL | 0.583 | 0.535 |
| R3_s256 | identifier | 0.767 | 0.717 |
| R3_s256 | multi-hop | 0.000 | 0.000 |
| R3_s256 | paraphrase | 0.933 | 0.788 |
| R3_s256 | version | 0.633 | 0.633 |
| R3_s512 | OVERALL | 0.583 | 0.535 |
| R3_s512 | identifier | 0.767 | 0.700 |
| R3_s512 | multi-hop | 0.000 | 0.000 |
| R3_s512 | paraphrase | 0.933 | 0.805 |
| R3_s512 | version | 0.633 | 0.633 |
| R4_s256 | OVERALL | 0.683 | 0.687 |
| R4_s256 | identifier | 1.000 | 0.829 |
| R4_s256 | multi-hop | 0.500 | 0.831 |
| R4_s256 | paraphrase | 0.600 | 0.457 |
| R4_s256 | version | 0.633 | 0.633 |
| R5_s256 | OVERALL | 0.588 | 0.571 |
| R5_s256 | identifier | 0.800 | 0.672 |
| R5_s256 | multi-hop | 0.283 | 0.542 |
| R5_s256 | paraphrase | 0.633 | 0.468 |
| R5_s256 | version | 0.633 | 0.600 |

Unanswerable questions are excluded from retrieval metrics (see evaluate.py).
Costs, latency, cache status and token estimates are recorded in comparison.csv and *.run.json.
Batch mode amortizes query embedding time per question. p95_search_s excludes this time.
Cache hits reflect index reuse. Local GPU/CPU compute costs have no assigned USD value.

## End-to-end (the same stratified 50 development questions)

| Run | Category | n | Faithfulness* | Relevance | Correctness |
|---|---|---:|---:|---:|---:|
| judge_R4_s256 | OVERALL | 50 | 0.982 | 0.603 | 0.760 |
| judge_R4_s256 | identifier | 10 | 1.000 | 0.767 | 1.000 |
| judge_R4_s256 | multi-hop | 10 | - | 0.399 | 0.000 |
| judge_R4_s256 | paraphrase | 10 | 0.938 | 0.647 | 0.800 |
| judge_R4_s256 | unanswerable | 10 | - | 0.405 | 1.000 |
| judge_R4_s256 | version | 10 | 1.000 | 0.795 | 1.000 |
| judge_R5_s256 | OVERALL | 50 | 0.988 | 0.618 | 0.740 |
| judge_R5_s256 | identifier | 10 | 1.000 | 0.700 | 0.800 |
| judge_R5_s256 | multi-hop | 10 | - | 0.428 | 0.000 |
| judge_R5_s256 | paraphrase | 10 | 0.963 | 0.683 | 0.900 |
| judge_R5_s256 | unanswerable | 10 | - | 0.451 | 1.000 |
| judge_R5_s256 | version | 10 | 1.000 | 0.828 | 1.000 |

*Abstentions are excluded from faithfulness scoring.

Complete review_*.jsonl for judge review and failure analysis. Existing review files are preserved.
