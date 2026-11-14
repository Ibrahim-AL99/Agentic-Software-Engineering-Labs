# Task 3 analysis

## 1–3. Retrieval configurations, chunk sizes and results

We evaluated 150 development questions. Retrieval metrics cover the 120 answerable questions. Each run returns five distinct documents. All sizes use words with 32-word overlap and additional source metadata.

R1 uses BM25 on the same recursive chunks as R3. R2 uses fixed windows and dense retrieval. R3 splits recursively at headings, paragraphs, sentences and word boundaries. R4 combines per-query min-max-normalized BM25 and dense scores with equal weights of 0.5, fixed before evaluation to balance exact identifiers and paraphrases. R5 reranks 30 hybrid candidate chunks with `cross-encoder/ms-marco-MiniLM-L-6-v2`.

| Run | Overall recall@5 | Overall precision@5 | Identifier recall | Paraphrase recall | Multi-hop recall | Version recall |
|---|---:|---:|---:|---:|---:|---:|
| R1: BM25 | 0.533 | 0.560 | 0.900 | 0.100 | 0.500 | 0.633 |
| R2: Dense, fixed windows | 0.583 | 0.529 | 0.767 | 0.933 | 0.000 | 0.633 |
| R3: Dense, recursive, 128 words | 0.425 | 0.362 | 0.533 | 0.667 | 0.000 | 0.500 |
| R3: Dense, recursive, 256 words | 0.583 | 0.535 | 0.767 | 0.933 | 0.000 | 0.633 |
| R3: Dense, recursive, 512 words | 0.583 | 0.535 | 0.767 | 0.933 | 0.000 | 0.633 |
| R4: Hybrid | **0.683** | **0.687** | **1.000** | 0.600 | 0.500 | 0.633 |
| R5: Hybrid with cross-encoder | 0.588 | 0.571 | 0.800 | 0.633 | 0.283 | 0.633 |

Full per-category recall and precision are in [comparison.md](results/comparison.md). R4 performs best overall and on identifiers. Dense retrieval with 256-word chunks performs best on paraphrases. R3 at 128 words loses recall, while 512 words provide no overall recall improvement over 256. R1 and R4 cover one of the two required information groups for every multi-hop question.

## 4. End-to-end evaluation and judge review

The two best retrieval configurations, R4 and R5, answered the same 50 stratified development questions, ten per category. Generation and judging used `openai.gpt-6-luna`, with `kiconnect.qwen3-embedding-8b` for embeddings.

| Run | Faithfulness | Relevance | Correctness | Abstentions |
|---|---:|---:|---:|---:|
| R4 | 0.982 | 0.603 | **0.760** | 22 of 50 |
| R5 | 0.988 | 0.618 | 0.740 | 23 of 50 |

R4 has 38 correct answers and R5 has 37. Both abstain on all ten multi-hop questions and all ten unanswerable questions. Faithfulness excludes abstentions and covers 28 R4 answers and 27 R5 answers.



## 5. Cost and latency

| Run | Indexing including initialization (s) | Document vectors | Query vectors | Mean retrieval per query (ms) | Mean context tokens |
|---|---:|---|---|---:|---:|
| R1 | 0.035 | N/A | N/A | 0.071 | 919 |
| R2 | 14.117 | Computed | Computed | 8.442 | 901 |
| R3, 128 | 12.680 | Computed | Cached | 0.233 | 690 |
| R3, 256 | 4.882 | Computed | Cached | 0.116 | 822 |
| R3, 512 | 19.884 | Computed | Cached | 0.107 | 824 |
| R4 | 0.050 | Cached | Cached | 0.163 | 855 |
| R5 | 29.123 | Cached | Cached | 67.422 | 873 |

Mean retrieval includes amortized batch query-embedding time. The table identifies local cache reuse, so the timings reflect different cache conditions. R5 indexing includes cross-encoder initialization. Context tokens are estimated from text length. Mean query length is approximately 20 tokens for all retrieval configurations. Detailed measurements are in `results/*.run.json`.

| Run | Questions | Mean prompt tokens | Mean output tokens | Mean generation (s) | Generation (USD) | Judge (USD) |
|---|---:|---:|---:|---:|---:|---:|
| R4 Dev | 50 | 1,097 | 45 | 1.860 | 0.00763 | 0.01011 |
| R5 Dev | 50 | 1,104 | 44 | 1.881 | 0.00765 | 0.00914 |
| R4 Test | 100 | 1,064 | 55 | 2.092 | 0.01489 | N/A |

Embedding logs report 0 USD. Both development generations, both judge runs and test generation total approximately 0.04942 USD in recorded API costs. Local compute costs are excluded.

## 6. Five failures of the selected configuration

All five R4 answers abstain and receive correctness 0. Each failure originates in retrieval because the decisive source is missing from the supplied context.

| Question | Required answer and evidence | Why retrieval failed |
|---|---|---|
| `dev004` | `dlq.max_retries`, documented in `d0032` or `d0303`. | Both sources are missing. Retrieved retry settings concern other components. |
| `dev011` | `broker.timeout_ms`, documented in `d0530` or `d0911`. | Both broker sources are missing. Retrieved settings include a timeout for the acknowledgement tracker. |
| `dev025` | `d0253` identifies `PQ-E1915`. `d0183` recommends adding capacity or reducing load, with a higher `metrics.threshold_percent` as a last resort. | The incident ranks first, but the referenced error catalogue entry is missing. |
| `dev030` | `d0442` identifies `PQ-E2617`. `d0174` recommends checking logs and increasing `dlq.max_retries` if failures are transient. | The incident ranks first, but the source containing the conditional fix is missing. |
| `dev032` | `d0377` identifies `PQ-E3891`. `d0350` recommends adding worker threads or increasing `compactor.queue_capacity`. | The incident ranks first, but the error catalogue entry containing the fix is missing. |

## 7–8. Recommendation and test predictions

We recommend R4 with 256-word chunks, overlap 32, hybrid weight 0.5 and five documents. It has the highest overall recall, slightly higher answer correctness than R5, and lower retrieval latency. Multi-hop remains its main weakness. A second retrieval step following error codes would be a useful next experiment.

[predictions_test.jsonl](predictions_test.jsonl) contains the 100 R4 test predictions. Selection used development results, which are subject to selection bias. Hidden test correctness is unknown.

## Reproduction and AI usage

See [README.md](README.md#reproduce-the-implementation) for commands. Codex assisted with implementation, tests and result understanding. Experiments ran locally using the UPB gateway. 
