"""RAG component for Task 2.

Loads all documents from the style guide, splits them into chunks,
embeds them via the gateway, and provides cosine-similarity search.

Usage:
    from task2_own_agent.rag import RAG, Chunk
    rag = RAG(style_guide_dir, llm)
    chunks = rag.search("color palette day mode", k=5)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from common.llm_client import LLMClient, count_tokens  # noqa: E402


@dataclass
class Chunk:
    text: str
    doc_id: str  # e.g. "SG-13"


class RAG:
    """Embeds style guide chunks and answers similarity queries."""

    def __init__(
        self,
        style_guide_dir: Path | str,
        llm: LLMClient,
        max_chunk_tokens: int = 200,
    ) -> None:
        self._llm = llm
        self.chunks: list[Chunk] = []
        self._vectors: np.ndarray | None = None
        self._load(Path(style_guide_dir), max_chunk_tokens)
        self._build_index()

    # loading

    def _load(self, dir: Path, max_tokens: int) -> None:
        for path in sorted(dir.glob("*.md")):
            doc_id = path.stem.split("_")[0]
            text = path.read_text(encoding="utf-8")
            self.chunks.extend(self._chunk(text, doc_id, max_tokens))

    def _chunk(self, text: str, doc_id: str, max_tokens: int) -> list[Chunk]:
        """Split by double-newline paragraphs, merging short consecutive ones."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[Chunk] = []
        current = ""
        for para in paragraphs:
            candidate = (current + "\n\n" + para).strip() if current else para
            if count_tokens(candidate) <= max_tokens:
                current = candidate
            else:
                if current:
                    chunks.append(Chunk(text=current, doc_id=doc_id))
                current = para  # start fresh (may still exceed if single para is large)
        if current:
            chunks.append(Chunk(text=current, doc_id=doc_id))
        return chunks

    # indexing

    def _build_index(self) -> None:
        """Embed all chunks and store L2-normalised vectors for cosine similarity."""
        texts = [c.text for c in self.chunks]
        vecs = self._llm.embed(texts)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._vectors = (vecs / norms).astype(np.float32)

    # querying

    def search(self, query: str, k: int) -> list[Chunk]:
        """Return the k most similar chunks to query by cosine similarity."""
        assert self._vectors is not None
        q_vec = self._llm.embed([query])[0]
        norm = float(np.linalg.norm(q_vec))
        if norm > 0:
            q_vec = q_vec / norm
        scores: np.ndarray = self._vectors @ q_vec.astype(np.float32)
        top_k_idx = np.argsort(scores)[::-1][:k]
        return [self.chunks[int(i)] for i in top_k_idx]
