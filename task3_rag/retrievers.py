"""The five retrieval configurations R1–R5 of Task 3.

Every retriever has the same interface:
    search(query) -> list[Hit]     top CANDIDATES chunks, best first, each with its score
and every search records how long each step took in `self.last_timing` (seconds), for the latency table.

  R1  BM25 on the recursive chunks (same chunks as R3)
  R2  fixed-size chunks + dense embeddings
  R3  recursive chunks + dense embeddings
  R4  hybrid: alpha * minmax(cosine) + (1 - alpha) * minmax(BM25), on the R3 chunks
  R5  R4 + cross-encoder re-ranking of R4's top RERANK_DEPTH chunks

to_doc_ids() maps chunks to their documents in rank order (first occurrence wins), which is what
evaluate.py and the test predictions need.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from chunking import Chunk
from indexes import BM25Index, DenseIndex

CANDIDATES = 30          # chunks returned per search (enough for 20 distinct documents in practice)
RERANK_DEPTH = 30        # R5: how many R4 chunks the cross-encoder re-scores
CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@dataclass
class Hit:
    chunk: Chunk
    score: float


def _top(chunks: list[Chunk], scores: np.ndarray, n: int) -> list[Hit]:
    order = np.argsort(-scores, kind="stable")[:n]
    return [Hit(chunks[i], float(scores[i])) for i in order]


def minmax(scores: np.ndarray) -> np.ndarray:
    """Rescale to [0, 1] per query. BM25 (0..~30) and cosine (~0.1..0.8) have different ranges."""
    lo, hi = float(scores.min()), float(scores.max())
    return np.zeros_like(scores) if hi - lo < 1e-12 else (scores - lo) / (hi - lo)


def to_doc_ids(hits: list[Hit], n: int = 20) -> list[str]:
    seen, ids = set(), []
    for h in hits:
        if h.chunk.doc_id not in seen:
            seen.add(h.chunk.doc_id)
            ids.append(h.chunk.doc_id)
    return ids[:n]


class BM25Retriever:  # R1
    def __init__(self, bm25: BM25Index):
        self.bm25 = bm25
        self.last_timing: dict = {}

    def search(self, query: str) -> list[Hit]:
        t = time.perf_counter()
        hits = _top(self.bm25.chunks, self.bm25.scores(query), CANDIDATES)
        self.last_timing = {"bm25_s": time.perf_counter() - t}
        return hits


class DenseRetriever:  # R2, R3
    def __init__(self, dense: DenseIndex):
        self.dense = dense
        self.last_timing: dict = {}

    def search(self, query: str) -> list[Hit]:
        q = self.dense.embedder.embed_query(query)  # cached after the first time; its latency is recorded there
        t = time.perf_counter()
        hits = _top(self.dense.chunks, self.dense.matrix @ q, CANDIDATES)
        self.last_timing = {"embed_query_s": self.dense.embedder.query_embed_seconds(query),
                            "cosine_s": time.perf_counter() - t}
        return hits


class HybridRetriever:  # R4
    def __init__(self, bm25: BM25Index, dense: DenseIndex, alpha: float):
        assert [c.chunk_id for c in bm25.chunks] == [c.chunk_id for c in dense.chunks], "R1 and R3 need the same chunks"
        self.bm25, self.dense, self.alpha = bm25, dense, alpha
        self.last_timing: dict = {}

    def fused_scores(self, query: str) -> np.ndarray:
        q = self.dense.embedder.embed_query(query)
        t0 = time.perf_counter()
        sparse = self.bm25.scores(query)
        t1 = time.perf_counter()
        cosine = self.dense.matrix @ q
        t2 = time.perf_counter()
        fused = self.alpha * minmax(cosine) + (1 - self.alpha) * minmax(sparse)
        t3 = time.perf_counter()
        self.last_timing = {"embed_query_s": self.dense.embedder.query_embed_seconds(query),
                            "bm25_s": t1 - t0, "cosine_s": t2 - t1, "fusion_s": t3 - t2}
        return fused

    def search(self, query: str) -> list[Hit]:
        return _top(self.dense.chunks, self.fused_scores(query), CANDIDATES)


class RerankRetriever:  # R5
    def __init__(self, hybrid: HybridRetriever, depth: int = RERANK_DEPTH, model_name: str = CROSS_ENCODER):
        from sentence_transformers import CrossEncoder

        self.hybrid = hybrid
        self.depth = depth
        t = time.perf_counter()
        self.model = CrossEncoder(model_name, device="cpu")
        self.load_s = time.perf_counter() - t
        self.last_timing: dict = {}

    def search(self, query: str) -> list[Hit]:
        candidates = _top(self.hybrid.dense.chunks, self.hybrid.fused_scores(query), self.depth)
        timing = dict(self.hybrid.last_timing)
        t = time.perf_counter()
        ce_scores = self.model.predict([(query, h.chunk.text) for h in candidates], batch_size=32,
                                       show_progress_bar=False)
        timing["rerank_s"] = time.perf_counter() - t
        self.last_timing = timing
        order = np.argsort(-np.asarray(ce_scores), kind="stable")
        return [Hit(candidates[i].chunk, float(ce_scores[i])) for i in order]
