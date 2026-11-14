"""One reproducible retrieval/generation run. Run from the repository root."""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from task3_rag.rag import (CONFIGS, Embedder, Retriever, answer_question, chunk_dicts,
                           fingerprint, load_jsonl, write_jsonl)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", choices=CONFIGS, default="R4")
    p.add_argument("--corpus", type=Path, default=HERE / "data/corpus.jsonl")
    p.add_argument("--questions", type=Path, default=HERE / "data/questions_dev.jsonl")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--chunk-size", type=int, default=256, help="words, excluding metadata")
    p.add_argument("--overlap", type=int, default=32, help="words")
    p.add_argument("--alpha", type=float, default=0.5, help="BM25 weight; dense weight = 1-alpha")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--embedding-backend", choices=["gateway", "local"], default="gateway")
    p.add_argument("--embedding-model")
    p.add_argument("--rerank-model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    p.add_argument("--candidates", type=int, default=30)
    p.add_argument("--cache-dir", type=Path, default=HERE / ".cache")
    p.add_argument("--query-mode", choices=["batch", "live"], default="batch",
                   help="batch: cached batched embeddings; live: one timed API call per question")
    p.add_argument("--generate", action="store_true")
    p.add_argument("--model", help="chat model; default LAB_CHAT_MODEL")
    p.add_argument("--sample", type=int, help="same stratified dev sample as judge.py")
    p.add_argument("--limit", type=int, help="smoke test only; do not use for final predictions")
    return p


def run(args):
    if args.k < 1 or (args.sample is not None and args.sample < 5) or (args.limit is not None and args.limit < 1):
        raise ValueError("Require k/limit > 0 and sample >= 5")
    documents, questions = load_jsonl(args.corpus), load_jsonl(args.questions)
    if len({d["doc_id"] for d in documents}) != len(documents):
        raise ValueError("Duplicate corpus doc_id")
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Duplicate question id")
    if args.sample:
        if any("category" not in q for q in questions):
            raise ValueError("--sample requires dev questions with categories")
        from task3_rag.judge import stratified_sample
        questions = stratified_sample(questions, args.sample)
    if args.limit:
        questions = questions[:args.limit]
    if not questions:
        raise ValueError("No questions found")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # A failed replacement run must not retain the previous success manifest.
    args.out.with_suffix(".run.json").unlink(missing_ok=True)
    embedder = None
    start = time.perf_counter()
    if args.config != "R1":
        embedder = Embedder(args.embedding_backend, args.embedding_model,
                            args.out.with_suffix(".embedding_log.jsonl"))
    embedder_init_seconds = time.perf_counter() - start
    retriever = Retriever(documents, args.config, args.chunk_size, args.overlap, args.alpha,
                          embedder, args.cache_dir, args.rerank_model, args.candidates)
    query_vectors, query_cache_hit, embedding_seconds = None, None, 0.0
    if embedder is not None and args.query_mode == "batch":
        start = time.perf_counter()
        query_vectors, query_cache_hit = embedder.cached([q["question"] for q in questions],
                                                        args.cache_dir, query=True)
        embedding_seconds = time.perf_counter() - start
    llm = None
    if args.generate:
        from common.llm_client import LLMClient
        llm = LLMClient(model=args.model, log_path=args.out.with_suffix(".generation_log.jsonl"))
    rows, search_times, generation_times = [], [], []
    # Flush each row so failed gateway calls leave inspectable partial results.
    with args.out.open("w", encoding="utf-8") as stream:
        for index, question in enumerate(questions):
            start = time.perf_counter()
            vector = query_vectors[index] if query_vectors is not None else None
            chunks = retriever.search(question["question"], args.k, vector)
            search_seconds = time.perf_counter() - start
            search_times.append(search_seconds)
            contexts = [c.text for c in chunks]
            row = {"id": question["id"], "retrieved_doc_ids": [c.doc_id for c in chunks],
                   "contexts": contexts, "chunks": chunk_dicts(chunks),
                   "search_s": search_seconds,
                   "query_embedding_amortized_s": embedding_seconds / len(questions),
                   "approx_context_tokens": sum(max(1, len(c) // 4) for c in contexts),
                   "approx_query_tokens": max(1, len(question["question"]) // 4)}
            if llm is not None:
                start = time.perf_counter()
                row["answer"], row["approx_prompt_tokens"] = answer_question(llm, question["question"], contexts)
                row["generation_s"] = time.perf_counter() - start
                generation_times.append(row["generation_s"])
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            rows.append(row)
            if args.generate or (index + 1) % 25 == 0:
                print(f"{args.config} [{index + 1}/{len(questions)}] {question['id']}", flush=True)
    manifest = {"config": vars(args), "n_questions": len(rows), "n_chunks": len(retriever.chunks),
                "results_sha256": fingerprint(rows),
                "corpus_sha256": fingerprint(documents),
                "questions_sha256": fingerprint([{k: q[k] for k in ("id", "question")} for q in questions]),
                "embedding_model": embedder.model if embedder else None,
                "python_version": platform.python_version(), "numpy_version": np.__version__,
                "index_s": retriever.index_seconds + embedder_init_seconds,
                "index_build_s": retriever.index_seconds, "embedder_init_s": embedder_init_seconds,
                "index_cache_hit": retriever.index_cache_hit,
                "query_cache_hit": query_cache_hit, "query_embedding_batch_s": embedding_seconds,
                "mean_search_s": float(np.mean(search_times)), "p95_search_s": float(np.percentile(search_times, 95)),
                "mean_retrieval_s": float(np.mean(search_times)) + embedding_seconds / len(questions),
                "mean_generation_s": float(np.mean(generation_times)) if generation_times else None,
                "mean_context_tokens_approx": float(np.mean([r["approx_context_tokens"] for r in rows])),
                "mean_query_tokens_approx": float(np.mean([r["approx_query_tokens"] for r in rows])),
                "mean_prompt_tokens_approx": float(np.mean([r["approx_prompt_tokens"] for r in rows])) if llm else None,
                "embedding_cost_usd": embedder.llm.total_cost_usd if embedder and embedder.llm else 0.0,
                "generation_cost_usd": llm.total_cost_usd if llm else 0.0,
                "chat_model": llm.model if llm else None,
                "generation_prompt_tokens": llm.total_prompt_tokens if llm else None,
                "generation_completion_tokens": llm.total_completion_tokens if llm else None,
                "embedding_usage": embedder.llm.usage_summary() if embedder and embedder.llm else None,
                "generation_usage": llm.usage_summary() if llm else None,
                "latency_note": "Batch mode amortizes query embedding time; search excludes batch embedding. "
                                "Live mode includes query embedding in search. Gateway response-cache hits are in logs."}
    args.out.with_suffix(".run.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"Saved {len(rows)} rows to {args.out}")
    return rows, manifest


def main():
    args = parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
