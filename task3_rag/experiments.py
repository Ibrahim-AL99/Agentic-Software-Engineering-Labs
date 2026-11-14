#Reproducible R1--R5 comparisons, end-to-end evaluation and submission export.
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from task3_rag.evaluate import evaluate, format_table
from task3_rag.rag import CONFIGS, fingerprint, load_jsonl, write_jsonl
from task3_rag.run import parser as run_parser, run


def execute_run(options, config, size, output, generate=False, test=False):
    argv = ["--config", config, "--chunk-size", str(size), "--overlap", str(options.overlap),
            "--alpha", str(options.alpha), "--embedding-backend", options.embedding_backend,
            "--query-mode", options.query_mode, "--out", str(output),
            "--rerank-model", options.rerank_model, "--candidates", str(options.candidates)]
    if options.embedding_model:
        argv.extend(["--embedding-model", options.embedding_model])
    if options.model:
        argv.extend(["--model", options.model])
    if generate:
        argv.append("--generate")
        if not test:
            argv.extend(["--sample", "50"])
    if test:
        argv.extend(["--questions", str(HERE / "data/questions_test.jsonl")])
    return run(run_parser().parse_args(argv))


def save_metrics(rows, target):
    report = evaluate(rows, load_jsonl(HERE / "data/questions_dev.jsonl"), 5)
    report["results_sha256"] = fingerprint(rows)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(format_table(report["summary"], target.stem))
    return report


def retrieval(options):
    jobs = [("R1", options.size), ("R2", options.size)]
    jobs += [("R3", size) for size in options.sizes]
    if options.size not in options.sizes:
        jobs.append(("R3", options.size))
    jobs += [("R4", options.size), ("R5", options.size)]
    for config, size in jobs:
        if config not in options.configs:
            continue
        name = f"{config}_s{size}"
        rows, _ = execute_run(options, config, size, options.results / f"{name}.jsonl")
        save_metrics(rows, options.results / f"{name}.metrics.json")
    report(options)


def available(options):
    result = []
    expected_hash = fingerprint(load_jsonl(HERE / "data/corpus.jsonl"))
    questions = load_jsonl(HERE / "data/questions_dev.jsonl")
    expected_questions = fingerprint([{k: q[k] for k in ("id", "question")} for q in questions])
    for path in sorted(options.results.glob("R*_s*.metrics.json")):
        name = path.name.removesuffix(".metrics.json")
        manifest_path = options.results / f"{name}.run.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        if manifest["corpus_sha256"] != expected_hash or manifest["questions_sha256"] != expected_questions:
            raise ValueError(f"Stale/incomplete run: {name}; rerun retrieval")
        metrics = json.loads(path.read_text())
        if metrics.get("results_sha256") != manifest["results_sha256"] or manifest["config"]["k"] != 5:
            raise ValueError(f"Metrics do not match run: {name}; rerun retrieval")
        result.append((name, manifest, metrics))
    return result


def best_two(options):
    runs = available(options)
    # Rank by recall, then precision, then lower measured retrieval latency.
    runs.sort(key=lambda item: (item[2]["summary"]["overall"]["context_recall"],
                                item[2]["summary"]["overall"]["context_precision"],
                                -item[1]["mean_retrieval_s"]), reverse=True)
    # Keep two distinct retrieval families, rather than two R3 chunk sizes.
    selected, seen = [], set()
    for item in runs:
        config = item[1]["config"]["config"]
        if config not in seen:
            selected.append(item)
            seen.add(config)
        if len(selected) == 2:
            break
    if len(selected) < 2:
        raise ValueError("Run retrieval for at least two configurations before e2e")
    return selected


def reproduce_args(options, manifest):
    """Regeneration must use exactly the measured retrieval settings."""
    original = manifest["config"]
    for key in ("overlap", "alpha", "embedding_backend", "embedding_model", "query_mode", "rerank_model", "candidates"):
        setattr(options, key, original[key])


def e2e(options):
    selected = best_two(options)
    for name, manifest, _ in selected:
        reproduce_args(options, manifest)
        execute_run(options, manifest["config"]["config"], manifest["config"]["chunk_size"],
                    options.results / f"answers_{name}.jsonl", generate=True)
        command = [sys.executable, str(HERE / "judge.py"), str(options.results / f"answers_{name}.jsonl"),
                   "--sample", "50", "--out", str(options.results / f"judge_{name}.jsonl")]
        if options.model:
            command.extend(["--model", options.model])
        if options.embedding_backend == "local":
            command.append("--local-embeddings")
        subprocess.run(command, cwd=HERE.parent, check=True)
    report(options)


def predict(options):
    runs = available(options)
    if options.best:
        matches = [item for item in runs if item[0] == options.best]
        if len(matches) != 1:
            raise ValueError("--best must name an evaluated run, e.g. R5_s256")
        chosen = matches[0]
    else:
        scored = []
        for item in best_two(options):
            path = options.results / f"judge_{item[0]}.jsonl"
            if path.exists():
                rows = load_jsonl(path)
                scored.append((sum(r["correctness"] for r in rows) / len(rows), item))
        if len(scored) < 2:
            raise ValueError("Run e2e first or explicitly select an evaluated run with --best")
        chosen = max(scored, key=lambda pair: pair[0])[1]
    name, manifest, _ = chosen
    reproduce_args(options, manifest)
    rows, _ = execute_run(options, manifest["config"]["config"], manifest["config"]["chunk_size"],
                          options.results / f"test_{name}.jsonl", generate=True, test=True)
    expected = [q["id"] for q in load_jsonl(HERE / "data/questions_test.jsonl")]
    if [r["id"] for r in rows] != expected:
        raise ValueError("Test predictions missing or out of order")
    predictions = [{key: row[key] for key in ("id", "answer", "retrieved_doc_ids")} for row in rows]
    if any(not r["answer"].strip() or len(r["retrieved_doc_ids"]) != 5 for r in predictions):
        raise ValueError("Expected a nonempty answer and five document IDs per test question")
    write_jsonl(HERE / "predictions_test.jsonl", predictions)
    print(f"Test export: {HERE / 'predictions_test.jsonl'} ({name})")


def report(options):
    runs = available(options)
    if not runs:
        raise ValueError("No complete retrieval runs found")
    table = []
    for name, manifest, metrics in runs:
        categories = {"OVERALL": metrics["summary"]["overall"], **metrics["summary"]["per_category"]}
        for category, values in categories.items():
            table.append({"run": name, "category": category, **values,
                          "index_s": manifest["index_s"], "index_cache_hit": manifest["index_cache_hit"],
                          "mean_retrieval_s": manifest["mean_retrieval_s"],
                          "p95_search_s": manifest["p95_search_s"],
                          "context_tokens_approx": manifest["mean_context_tokens_approx"],
                          "query_tokens_approx": manifest["mean_query_tokens_approx"],
                          "embedding_cost_usd": manifest["embedding_cost_usd"]})
    with (options.results / "comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    lines = ["# Task 3 - Evaluation results", "", "Automatically generated. Configuration selection/tuning uses the development set.", "",
             "| Run | Category | Recall@5 | Precision@5 |", "|---|---|---:|---:|"]
    lines += [f"| {r['run']} | {r['category']} | {r['context_recall']:.3f} | {r['context_precision']:.3f} |" for r in table]
    lines += ["", "Unanswerable questions are excluded from retrieval metrics (see evaluate.py).",
              "Costs, latency, cache status and token estimates are recorded in comparison.csv and *.run.json.",
              "Batch mode amortizes query embedding time per question. p95_search_s excludes this time.",
              "Cache hits reflect index reuse. Local GPU/CPU compute costs have no assigned USD value.",
              "", "## End-to-end (the same stratified 50 development questions)", "",
              "| Run | Category | n | Faithfulness* | Relevance | Correctness |", "|---|---|---:|---:|---:|---:|"]
    for path in sorted(options.results.glob("judge_R*.jsonl")):
        rows = load_jsonl(path)
        for category in ["OVERALL", *sorted({r["category"] for r in rows})]:
            items = rows if category == "OVERALL" else [r for r in rows if r["category"] == category]
            def mean(key):
                values = [r[key] for r in items if r[key] is not None]
                return f"{sum(values)/len(values):.3f}" if values else "-"
            lines.append(f"| {path.stem} | {category} | {len(items)} | {mean('faithfulness')} | {mean('answer_relevance')} | {mean('correctness')} |")
    lines += ["", "*Abstentions are excluded from faithfulness scoring.",
              "", "Complete review_*.jsonl for judge review and failure analysis. Existing review files are preserved."]
    (options.results / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name, _, metrics in runs:
        judge_path = options.results / f"judge_{name}.jsonl"
        if not judge_path.exists():
            continue
        judges = load_jsonl(judge_path)
        answers = {r["id"]: r for r in load_jsonl(options.results / f"answers_{name}.jsonl")}
        questions = {q["id"]: q for q in load_jsonl(HERE / "data/questions_dev.jsonl")}
        recall = {r["id"]: r["context_recall"] for r in metrics["per_question"]}
        # Two decisions per category, favouring incorrect/partial decisions.
        checks = []
        for category in sorted({j["category"] for j in judges}):
            candidates = sorted((j for j in judges if j["category"] == category),
                                key=lambda j: (j["correctness"], j["id"]))[:2]
            for j in candidates:
                checks.append({"id": j["id"], "question": questions[j["id"]], "answer": answers[j["id"]],
                               "judge": j, "human_agrees": None, "human_reason": ""})
        target = options.results / f"review_judge_{name}.jsonl"
        if not target.exists():
            write_jsonl(target, checks)
        failures = []
        for j in sorted(judges, key=lambda j: (j["correctness"], j["id"])):
            if j["correctness"] >= 1 or len(failures) == 5:
                continue
            qid = j["id"]
            failures.append({"id": qid, "question": questions[qid], "answer": answers[qid], "judge": j,
                             "retrieval_recall_at_5": recall.get(qid),
                             "suggested_failure": "retrieval" if recall.get(qid, 1) < 1 else "generation_or_judge",
                             "human_failure_type": "", "human_explanation": ""})
        target = options.results / f"review_failures_{name}.jsonl"
        if not target.exists():
            write_jsonl(target, failures)
    print(f"Tables and review templates: {options.results}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["retrieval", "e2e", "predict", "report"])
    p.add_argument("--results", type=Path, default=HERE / "results")
    p.add_argument("--configs", nargs="+", choices=CONFIGS, default=list(CONFIGS))
    p.add_argument("--size", type=int, default=256)
    p.add_argument("--sizes", type=int, nargs=3, default=[128, 256, 512])
    p.add_argument("--overlap", type=int, default=32)
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--embedding-backend", choices=["gateway", "local"], default="gateway")
    p.add_argument("--embedding-model")
    p.add_argument("--rerank-model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    p.add_argument("--candidates", type=int, default=30)
    p.add_argument("--query-mode", choices=["batch", "live"], default="batch")
    p.add_argument("--model")
    p.add_argument("--best", help="optional override for test export, e.g. R5_s256")
    options = p.parse_args()
    if len(set(options.sizes)) != 3 or any(size <= options.overlap for size in [options.size, *options.sizes]):
        p.error("Use three different chunk sizes, all larger than the overlap")
    options.results.mkdir(parents=True, exist_ok=True)
    globals()[options.stage](options)


if __name__ == "__main__":
    main()
