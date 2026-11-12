"""Search indexes for Task 3: BM25 (keyword) and dense embeddings (meaning), with a disk cache.

- tokenize():     BM25 tokenizer that keeps identifiers like `PQ-E4663` or `spool.buffer_bytes` whole AND
                  adds their parts, so both "PQ-E4663" and "E4663" match.
- BM25Index:      rank_bm25.BM25Okapi over chunk texts; scores(query) returns one score per chunk.
- Embedder:       gateway embedding model with two caches in task3_rag/cache/ (gitignored, regenerable):
                    emb_<hash>.npy      chunk vectors, key = model + every chunk text (any chunking change -> new key)
                    queries_<model>.npz query vectors, key = model + instruction + query text
                  It also records how long the uncached embedding calls took (for the cost/latency table).
- DenseIndex:     cosine similarity of the query vector against all chunk vectors.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from common.llm_client import LLMClient  # noqa: E402

from chunking import Chunk  # noqa: E402

CACHE_DIR = HERE / "cache"
LOG_DIR = HERE / "logs"

# Qwen3-Embedding expects an instruction in front of the query (documents are embedded as they are).
QUERY_INSTRUCTION = (
    "Instruct: Given a question about the PaderQueue message queue, "
    "retrieve documentation passages that answer it\nQuery: "
)

# ---------------------------------------------------------------------- BM25

_TOKEN = re.compile(r"[a-z0-9]+(?:[._\-/][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase; identifiers are kept whole and also split at . _ - /

    "spool.buffer_bytes" -> ["spool.buffer_bytes", "spool", "buffer_bytes", "buffer", "bytes"]
    "PQ-E4663"           -> ["pq-e4663", "pq", "e4663"]
    """
    tokens = []
    for word in _TOKEN.findall(text.lower()):
        tokens.append(word)
        if re.search(r"[._\-/]", word):
            for part in re.split(r"[.\-/]", word):
                if part and part != word:
                    tokens.append(part)
                    if "_" in part:
                        tokens.extend(p for p in part.split("_") if p)
            if "_" in word and not re.search(r"[.\-/]", word):
                tokens.extend(p for p in word.split("_") if p)
    return tokens


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        from rank_bm25 import BM25Okapi

        start = time.perf_counter()
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])
        self.build_s = time.perf_counter() - start

    def scores(self, query: str) -> np.ndarray:
        return np.asarray(self.bm25.get_scores(tokenize(query)), dtype=np.float32)


# -------------------------------------------------------------- embeddings


def _normalise(m: np.ndarray) -> np.ndarray:
    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


class Embedder:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient(log_path=LOG_DIR / "embeddings.jsonl")
        self.model = self.llm.embedding_model
        CACHE_DIR.mkdir(exist_ok=True)
        self.query_cache_path = CACHE_DIR / f"queries_{self.model.replace('/', '_')}.npz"
        self.query_meta_path = CACHE_DIR / f"queries_{self.model.replace('/', '_')}.json"
        self._queries = dict(np.load(self.query_cache_path)) if self.query_cache_path.exists() else {}
        self._query_meta = (
            json.loads(self.query_meta_path.read_text()) if self.query_meta_path.exists() else {}
        )
        self._dirty = False

    # chunks ------------------------------------------------------------
    def embed_chunks(self, chunks: list[Chunk]) -> tuple[np.ndarray, dict]:
        """Normalised chunk matrix (float32) + info {"cached": bool, "embed_s": seconds of the uncached run}."""
        key = _hash(self.model + "\n\x00".join(c.text for c in chunks))
        path = CACHE_DIR / f"emb_{key}.npy"
        meta_path = CACHE_DIR / f"emb_{key}.json"
        if path.exists():
            info = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            return np.load(path).astype(np.float32), {**info, "cached": True}
        start = time.perf_counter()
        vectors = self.llm.embed([c.text for c in chunks])
        elapsed = time.perf_counter() - start
        matrix = _normalise(vectors.astype(np.float32))
        np.save(path, matrix.astype(np.float16))
        info = {"n_chunks": len(chunks), "embed_s": round(elapsed, 2), "model": self.model}
        meta_path.write_text(json.dumps(info))
        return matrix, {**info, "cached": False}

    # queries -----------------------------------------------------------
    def embed_query(self, query: str) -> np.ndarray:
        """One request per query (not batched), so the recorded time is a realistic per-query latency."""
        key = _hash(self.model + QUERY_INSTRUCTION + query)
        if key not in self._queries:
            start = time.perf_counter()
            vector = self.llm.embed([QUERY_INSTRUCTION + query])[0]
            self._query_meta[key] = {"embed_s": round(time.perf_counter() - start, 4), "tokens": None}
            self._queries[key] = _normalise(vector.astype(np.float32))
            self._dirty = True
        return self._queries[key].astype(np.float32)

    def query_embed_seconds(self, query: str) -> float | None:
        meta = self._query_meta.get(_hash(self.model + QUERY_INSTRUCTION + query))
        return meta["embed_s"] if meta else None

    def save(self) -> None:
        if self._dirty:
            np.savez(self.query_cache_path, **self._queries)
            self.query_meta_path.write_text(json.dumps(self._query_meta))
            self._dirty = False


class DenseIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder):
        self.chunks = chunks
        self.embedder = embedder
        self.matrix, self.info = embedder.embed_chunks(chunks)

    def scores(self, query: str) -> np.ndarray:
        return self.matrix @ self.embedder.embed_query(query)


if __name__ == "__main__":
    for example in ["What does PQ-E4663 mean?", "default of `spool.buffer_bytes` in 2.x", "pq topic purge --force"]:
        print(example, "->", tokenize(example))
