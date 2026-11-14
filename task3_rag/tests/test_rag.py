"""Offline regression tests; no keys, model downloads or network required."""
from __future__ import annotations

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
from task3_rag.rag import (ABSTENTION, BM25, Embedder, Retriever, answer_question,
                           chunk_documents, minmax, split_text, tokenize, unit_vectors)
from task3_rag.evaluate import evaluate


def doc(doc_id, title, text, version="4.x"):
    return {"doc_id": doc_id, "title": title, "text": text, "version": version, "type": "config"}


class FakeEmbedder:
    def cached(self, texts, cache_dir, query=False):
        return unit_vectors([[1, 0] if "old" in text else [0, 1] for text in texts]), False

    def encode(self, texts, query=False):
        return np.asarray([[0, 1] for _ in texts], dtype=np.float32)


class RetrievalTests(unittest.TestCase):
    def test_identifier_exact_match_survives(self):
        tokens = tokenize("PQ-E4663 spool.buffer_bytes --include-internal")
        for term in ("pq-e4663", "e4663", "spool.buffer_bytes", "buffer", "bytes", "include-internal"):
            self.assertIn(term, tokens)
        retriever = Retriever([doc("a", "spool.buffer_bytes", "Default 20"),
                               doc("b", "ingress.interval_s", "Default 10")], config="R1")
        self.assertEqual(retriever.search("spool.buffer_bytes", 1)[0].doc_id, "a")

    def test_chunk_coverage_limits_and_overlap(self):
        text = "# Title\n\n" + " ".join(f"w{i}" for i in range(100)) + ".\n\n## Next\n" + "x " * 40
        for recursive in (False, True):
            chunks = split_text(text, size=30, overlap=5, recursive=recursive)
            self.assertTrue(all(0 < len(c.split()) <= 30 for c in chunks))
            for token in text.split():
                self.assertIn(token, " ".join(chunks).split())
            for left, right in zip(chunks, chunks[1:]):
                count = min(5, len(left.split()))
                self.assertEqual(left.split()[-count:], right.split()[:count])

    def test_recursive_preserves_heading_boundary(self):
        text = "# One\n" + "a " * 12 + "\n\n# Two\n" + "b " * 12
        chunks = split_text(text, size=20, overlap=0)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[1].startswith("# Two"))

    def test_invalid_sizes(self):
        for size, overlap in ((0, 0), (5, 5), (10, -1)):
            with self.assertRaises(ValueError):
                split_text("hello", size, overlap)

    def test_r1_uses_identical_chunks_to_r3(self):
        docs = [doc("a", "old setting", "old " * 100), doc("b", "new setting", "new " * 100)]
        r1 = Retriever(docs, "R1", size=30, overlap=5)
        r3 = Retriever(docs, "R3", size=30, overlap=5, embedder=FakeEmbedder())
        self.assertEqual(r1.chunks, r3.chunks)
        self.assertEqual([c.doc_id for c in r1.search("old", 5)], ["a", "b"])
        self.assertEqual(r3.search("new", 1)[0].doc_id, "b")

    def test_hybrid_weights_and_scale_invariance(self):
        self.assertTrue(np.allclose(minmax([1, 2, 4]), minmax([110, 210, 410])))
        self.assertTrue(np.allclose(minmax([2, 2]), [0, 0]))
        docs = [doc("a", "old", "old"), doc("b", "new", "new")]
        dense = Retriever(docs, "R4", alpha=0, embedder=FakeEmbedder())
        sparse = Retriever(docs, "R4", alpha=1, embedder=FakeEmbedder())
        self.assertEqual(dense.search("old", 1)[0].doc_id, "b")
        self.assertEqual(sparse.search("old", 1)[0].doc_id, "a")

    def test_r5_reranks_and_deduplicates(self):
        class FakeCrossEncoder:
            def __init__(self, name):
                pass

            def predict(self, pairs):
                return np.asarray([10 if "Document: b" in text else 0 for _, text in pairs])
        with patch.dict(sys.modules, {"sentence_transformers": SimpleNamespace(CrossEncoder=FakeCrossEncoder)}):
            retriever = Retriever([doc("a", "old", "old " * 100), doc("b", "new", "new")],
                                  "R5", size=30, overlap=5, alpha=1, embedder=FakeEmbedder(), candidates=1)
            self.assertEqual([c.doc_id for c in retriever.search("old", 2)], ["b", "a"])

    def test_zero_embeddings_are_rejected(self):
        with self.assertRaises(ValueError):
            unit_vectors([[0, 0]])

    def test_cache_separates_model_role_and_text(self):
        embedder = Embedder.__new__(Embedder)
        embedder.backend, embedder.model = "fake", "model1"
        calls = []
        embedder.encode = lambda texts, query=False: (calls.append((texts, query)) or unit_vectors([[1, 2]]))
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(embedder.cached(["a"], folder)[1])
            self.assertTrue(embedder.cached(["a"], folder)[1])
            self.assertFalse(embedder.cached(["b"], folder)[1])
            self.assertFalse(embedder.cached(["a"], folder, query=True)[1])
            embedder.model = "model2"
            self.assertFalse(embedder.cached(["a"], folder)[1])
        self.assertEqual(len(calls), 4)

    def test_generator_gets_exact_context_and_no_ground_truth(self):
        class FakeLLM:
            def chat(self, messages):
                self.messages = messages
                return "Default is 300 [d1]."
        llm = FakeLLM()
        contexts = ["Document: d1\nVersion: 2.x\nDefault: 300"]
        answer, tokens = answer_question(llm, "What is the default?", contexts)
        sent = json.loads(llm.messages[1]["content"])
        self.assertEqual(sent["contexts"], contexts)
        self.assertEqual(set(sent), {"question", "contexts"})
        self.assertIn("300", answer)
        self.assertGreater(tokens, 0)
        self.assertEqual(answer_question(llm, "unknown", []), (ABSTENTION, 0))

    def test_official_evaluator_multihop_deduplication(self):
        questions = [{"id": "q", "category": "multi-hop", "relevant_doc_ids": [["a", "b"], ["c"]]}]
        result = evaluate([{"id": "q", "retrieved_doc_ids": ["a", "a", "c"]}], questions, 2)
        self.assertEqual(result["summary"]["overall"]["context_recall"], 1)


if __name__ == "__main__":
    unittest.main()
