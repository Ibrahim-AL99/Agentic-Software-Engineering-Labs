"""Exercise the complete CLI workflow with deterministic offline model doubles."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from task3_rag import experiments
from task3_rag import run as runner
from task3_rag.rag import load_jsonl, write_jsonl


class OfflineEmbedder:
    model, llm = "offline-test-only", None

    def __init__(self, *args, **kwargs):
        pass

    def cached(self, texts, *args, **kwargs):
        return np.tile(np.asarray([[1, 0]], dtype=np.float32), (len(texts), 1)), False

    def encode(self, texts, **kwargs):
        return self.cached(texts)[0]


class OfflineLLM:
    model = "offline-test-only"
    total_prompt_tokens = total_completion_tokens = total_cost_usd = 0

    def __init__(self, *args, **kwargs):
        pass

    def chat(self, messages):
        supplied = json.loads(messages[1]["content"])
        assert set(supplied) == {"question", "contexts"}
        return "Offline test answer."

    def usage_summary(self):
        return "offline test only"


class PipelineTests(unittest.TestCase):
    def test_full_workflow_exports_reviews_and_100_predictions(self):
        with tempfile.TemporaryDirectory() as folder:
            options = SimpleNamespace(results=Path(folder), configs=["R1", "R3"], size=256,
                                      sizes=[128, 256, 512], overlap=32, alpha=0.5,
                                      embedding_backend="local", embedding_model=None, query_mode="batch",
                                      model=None, best=None, rerank_model="unused", candidates=30)

            def offline_judge(command, **kwargs):
                answers = load_jsonl(command[2])
                by_id = {q["id"]: q for q in load_jsonl(ROOT / "task3_rag/data/questions_dev.jsonl")}
                target = command[command.index("--out") + 1]
                write_jsonl(target, [{"id": a["id"], "category": by_id[a["id"]]["category"],
                                      "correctness": 0.5, "faithfulness": 1.0, "answer_relevance": 0.9}
                                     for a in answers])

            # The real exporter uses HERE/predictions_test.jsonl. Redirect only its
            # final write; all retrieval and answer artifacts stay in the temp folder.
            original_write = experiments.write_jsonl
            export = Path(folder) / "predictions_test.jsonl"
            def safe_write(path, rows):
                if Path(path).name == "predictions_test.jsonl":
                    path = export
                original_write(path, rows)

            with patch.object(runner, "Embedder", OfflineEmbedder), \
                 patch("common.llm_client.LLMClient", OfflineLLM), \
                 patch.object(experiments.subprocess, "run", offline_judge), \
                 patch.object(experiments, "write_jsonl", safe_write), \
                 contextlib.redirect_stdout(io.StringIO()):
                experiments.retrieval(options)
                experiments.e2e(options)
                for name, _, _ in experiments.best_two(options):
                    self.assertEqual(len(load_jsonl(Path(folder) / f"answers_{name}.jsonl")), 50)
                    self.assertEqual(len(load_jsonl(Path(folder) / f"review_judge_{name}.jsonl")), 10)
                    self.assertEqual(len(load_jsonl(Path(folder) / f"review_failures_{name}.jsonl")), 5)
                experiments.predict(options)
            predictions = load_jsonl(export)
            self.assertEqual(len(predictions), 100)
            self.assertEqual(len({row["id"] for row in predictions}), 100)
            self.assertTrue(all(set(row) == {"id", "answer", "retrieved_doc_ids"} for row in predictions))
            self.assertTrue(all(len(set(row["retrieved_doc_ids"])) == 5 for row in predictions))

    def test_failed_run_invalidates_previous_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run.jsonl"
            output.with_suffix(".run.json").write_text("previous success")
            args = runner.parser().parse_args(["--config", "R3", "--out", str(output)])
            with patch.object(runner, "Embedder", side_effect=RuntimeError("offline failure")):
                with self.assertRaises(RuntimeError):
                    runner.run(args)
            self.assertFalse(output.with_suffix(".run.json").exists())


if __name__ == "__main__":
    unittest.main()
