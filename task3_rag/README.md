# Task 3 – RAG under the microscope

Read the task description on the lab sheet first. There is no starter code for the retrievers, only the
dataset and the evaluation scripts.

## The dataset: PaderQueue

PaderQueue is a fictional message queue. No model has seen its documentation during training.

`data/corpus.jsonl` – 932 documents, one JSON object per line:

```json
{"doc_id": "d0132", "type": "config", "title": "ingress.interval_s (PaderQueue 2.x)", "version": "2.x", "text": "# `ingress.interval_s` ..."}
```

| type | count | content |
|---|---|---|
| `config` | 170 | configuration reference, one page per option. Some options also have a page for an old major version |
| `error` | 77 | error catalogue (codes like `PQ-E4663`) |
| `cli` | 36 | command line reference |
| `changelog` | 14 | release notes per version (long) |
| `incident` | 120 | postmortems of incidents |
| `forum` | 500 | forum threads. Answers can be wrong or outdated |
| `runbook` | 15 | troubleshooting runbooks |

The `type`, `title` and `version` fields are metadata. You may use them (e.g. put the title into each chunk).

`data/questions_dev.jsonl` – 150 questions with ground truth:

```json
{"id": "dev001", "category": "version", "question": "What was the default value of `ingress.interval_s` in PaderQueue 2.x?",
 "answer": "`300`", "relevant_doc_ids": [["d0132", "d0406"]]}
```

- `relevant_doc_ids` is a list of groups. Each group is one piece of information the answer needs; any
  document in a group provides it. `[["a", "b"], ["c"]]` means: needs (a or b) and c.
- Categories (30 questions each): `identifier`, `paraphrase`, `multi-hop`, `version`, `unanswerable`.
- Unanswerable questions have `relevant_doc_ids: []` and the answer "The documentation does not contain this information."

`data/questions_test.jsonl` – 100 questions, only `id` and `question`.

## Evaluation scripts

### Retrieval: `evaluate.py`

Write your retrieval results as JSONL, one line per question, documents in rank order:

```json
{"id": "dev001", "retrieved_doc_ids": ["d0132", "d0406", "d0017"]}
```

Your retriever returns chunks. Map chunks to their `doc_id` in rank order; duplicates are removed by the script.

```bash
uv run python task3_rag/evaluate.py results/retrieval_r1.jsonl --k 5 --json results/metrics_r1.json
```

Prints context recall and context precision (definitions in the file header), overall and per category.

### End-to-end: `judge.py`

Write your generated answers as JSONL:

```json
{"id": "dev001", "answer": "In PaderQueue 2.x the default was 300.", "contexts": ["chunk text 1", "chunk text 2"]}
```

`contexts` are exactly the chunk texts you gave to the generator.

```bash
uv run python task3_rag/judge.py results/answers_r4.jsonl --sample 50 --out results/judge_r4.jsonl
```

- Scores faithfulness, answer relevance and correctness (definitions in the file header).
- If your system can't answer, it should reply exactly `The documentation does not contain this information.`
  The judge recognizes this sentence as an abstention: it counts it in the `abstained` column and doesn't score its
  faithfulness (an abstention makes no claim about the context). Correctness tells you whether abstaining was right.
- `--sample 50` judges the same stratified 50 questions every time. That's enough for the lab.
- By default the judge uses `$LAB_CHAT_MODEL` and the gateway embedding model. `--local-embeddings` uses a local model for the answer relevance metric instead.

## Test set predictions

Submit `task3_rag/predictions_test.jsonl` with your best configuration, one line per test question:

```json
{"id": "test001", "answer": "...", "retrieved_doc_ids": ["d0001", "d0002", "d0003", "d0004", "d0005"]}
```

We evaluate context recall@5 of `retrieved_doc_ids` and the correctness of `answer` on the hidden ground truth.

## Practical hints

- Embedding models: the gateway embedding model (`$LAB_EMBEDDING_MODEL`, free, send batches of texts per request),
  or locally `sentence-transformers/all-MiniLM-L6-v2` (`uv sync --extra local`, runs fine on a laptop CPU:
  indexing the whole corpus takes well under a minute). The gateway model has 4096 dimensions, so a cache of the whole corpus
  is large: don't submit it.
- Latency: the gateway caches identical requests, so a second run of the same questions is much faster.
  Measure latency on the first run, or report cache hits (`cache_hit` in the log) separately.
- Cache your embeddings (e.g. `np.save`), so you don't re-embed the corpus for every experiment.
- Cross-encoder for re-ranking: `cross-encoder/ms-marco-MiniLM-L-6-v2` (sentence-transformers).
- BM25: `rank_bm25.BM25Okapi`. Think about how you tokenize identifiers like `PQ-E4663` or `spool.buffer_bytes`.
- Hybrid search: BM25 scores and cosine similarities have completely different ranges. Normalize before you combine them.
- Don't tune on the questions you report results on without saying so.

## Reproduce the implementation

Run all commands from the repository root. Requirements: Python 3.11+, `uv`, and UPB gateway access (university network/VPN).

### Setup and tests

```bash
uv sync --extra local
cp -n .env.example .env
```

Set your personal `UPB_AI_GATEWAY_KEY` in `.env`. 
```bash
uv run python -m unittest discover -s task3_rag/tests -v
```

### Experiments

Run these commands in order:

```bash
uv run python task3_rag/experiments.py retrieval
uv run python task3_rag/experiments.py e2e
uv run python task3_rag/experiments.py predict
```

- `retrieval`: evaluates R1–R5 at k=5, including R3 with 128/256/512-word chunks. Defaults: 32-word overlap, hybrid weight 0.5, and 30 candidates for cross-encoder reranking.
- `e2e`: selects two distinct configurations by recall, then precision and latency; generates and judges answers for the same stratified 50 development questions.
- `predict`: selects the configuration with the higher judged correctness and exports all 100 test predictions to `task3_rag/predictions_test.jsonl`.

`rag.py` implements retrieval and generation; `run.py` runs individual configurations; `experiments.py` orchestrates evaluation with the supplied scripts. Use `uv run python task3_rag/run.py --help` for individual runs.

### Results and review

Metrics, answers, timing/cost manifests and comparison tables are saved in `task3_rag/results/`; judge API logs are saved in `logs/`. [ANALYSIS.md](ANALYSIS.md) reports the findings, five failures and deployment recommendation. Ten manual judge checks are recorded in `results/review_judge_R4_s256.jsonl`.

To regenerate tables from existing results:

```bash
uv run python task3_rag/experiments.py report
```

