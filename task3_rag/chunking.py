"""Chunking for Task 3 (PaderQueue corpus).

Two strategies from the RAG lecture:

- fixed_size_chunks  (R2, lecture slide 33): a fixed window of N characters slides over the text and cuts
  wherever it lands, ignoring words, lines and headings.
- recursive_chunks   (R1/R3, lecture slide 34): try the biggest separator first (headings, then blank lines,
  then lines, then sentences, then words) and only fall back to a smaller one if a piece is still too long.
  The pieces are then merged greedily up to the size limit.

Both work per document (a chunk never spans two documents) and use the same overlap, measured in tokens.
Every chunk gets a metadata header "[title | type | version]" in front of its text (allowed by the README),
so a small piece of a config page still says which option and which version it belongs to.
The header is not counted in the chunk size.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "data" / "corpus.jsonl"


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def n_tokens(text: str) -> int:
    """Approximate token count (cl100k_base, same estimate as common/llm_client.count_tokens)."""
    return len(_encoding().encode(text))


@dataclass
class Chunk:
    chunk_id: str      # "<doc_id>#<n>"
    doc_id: str
    title: str
    type: str
    version: str
    body: str          # the chunk text without header

    @property
    def header(self) -> str:
        return f"[{self.title} | {self.type} | version {self.version or 'n/a'}]"

    @property
    def text(self) -> str:
        """What gets indexed (BM25 and embeddings) and what the generator sees."""
        return f"{self.header}\n{self.body.strip()}"


def load_corpus(path: Path = CORPUS) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _make_chunks(doc: dict, bodies: list[str]) -> list[Chunk]:
    return [
        Chunk(f"{doc['doc_id']}#{i}", doc["doc_id"], doc["title"], doc["type"], doc.get("version", ""), b)
        for i, b in enumerate(b for b in bodies if b.strip())
    ]


# ------------------------------------------------------------------ fixed-size (R2)


def chars_per_token(docs: list[dict]) -> float:
    """Average characters per token in the corpus. Used to give R2 the same chunk size as R3 in tokens."""
    return sum(len(d["text"]) for d in docs) / sum(n_tokens(d["text"]) for d in docs)


def fixed_size_chunks(docs: list[dict], size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    """Character window of size_tokens * chars_per_token characters, step = size - overlap."""
    cpt = chars_per_token(docs)
    size = round(size_tokens * cpt)
    step = size - round(overlap_tokens * cpt)
    chunks = []
    for doc in docs:
        text = doc["text"]
        starts = range(0, max(1, len(text) - round(overlap_tokens * cpt)), step)
        chunks.extend(_make_chunks(doc, [text[s : s + size] for s in starts]))
    return chunks


# ------------------------------------------------------------------- recursive (R1/R3)

# Zero-width split patterns, biggest unit first. Zero-width means the text is kept exactly:
# "".join(pieces) == original text.
SEPARATORS = [
    ("heading", r"(?=\n#{1,6} )"),
    ("paragraph", r"(?<=\n\n)"),
    ("line", r"(?<=\n)"),
    ("sentence", r"(?<=[.!?])(?=\s)"),
    ("word", r"(?<=\s)"),
]


def _split_recursive(text: str, max_tokens: int, level: int = 0) -> list[str]:
    """Split text into pieces of at most max_tokens, using the biggest separator that works."""
    if n_tokens(text) <= max_tokens:
        return [text]
    if level >= len(SEPARATORS):
        # a single "word" longer than the limit (e.g. a long hash): hard cut by tokens
        ids = _encoding().encode(text)
        return [_encoding().decode(ids[i : i + max_tokens]) for i in range(0, len(ids), max_tokens)]
    parts = [p for p in re.split(SEPARATORS[level][1], text) if p]
    if len(parts) == 1:  # separator not present, try the next smaller one
        return _split_recursive(text, max_tokens, level + 1)
    pieces = []
    for part in parts:
        pieces.extend(_split_recursive(part, max_tokens, level + 1) if n_tokens(part) > max_tokens else [part])
    return pieces


def _tail(text: str, overlap_tokens: int) -> str:
    """The last whole words of text that together have about overlap_tokens tokens."""
    if overlap_tokens <= 0:
        return ""
    words = re.split(r"(?<=\s)", text)
    tail = ""
    for word in reversed(words):
        if n_tokens(word + tail) > overlap_tokens:
            break
        tail = word + tail
    return tail


def recursive_chunks(docs: list[dict], size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    """Recursive split, then greedy merge of neighbouring pieces up to size_tokens.

    A new chunk starts with the last ~overlap_tokens tokens (whole words) of the previous chunk.
    """
    chunks = []
    for doc in docs:
        pieces = _split_recursive(doc["text"], size_tokens - overlap_tokens)
        bodies, current = [], ""
        for piece in pieces:
            if current and n_tokens(current + piece) > size_tokens:
                bodies.append(current)
                current = _tail(current, overlap_tokens)
            current += piece
        if current.strip():
            bodies.append(current)
        chunks.extend(_make_chunks(doc, bodies))
    return chunks


def describe(chunks: list[Chunk]) -> dict:
    sizes = sorted(n_tokens(c.body) for c in chunks)
    return {
        "n_chunks": len(chunks),
        "n_docs": len({c.doc_id for c in chunks}),
        "tokens_min": sizes[0],
        "tokens_median": sizes[len(sizes) // 2],
        "tokens_mean": round(sum(sizes) / len(sizes), 1),
        "tokens_max": sizes[-1],
    }


if __name__ == "__main__":
    corpus = load_corpus()
    print(f"chars per token: {chars_per_token(corpus):.2f}")
    for size in (64, 128, 256):
        print(f"recursive {size:>3}:", describe(recursive_chunks(corpus, size, 16)))
        print(f"fixed     {size:>3}:", describe(fixed_size_chunks(corpus, size, 16)))
    example = recursive_chunks([d for d in corpus if d["doc_id"] == "d0132"], 64, 16)
    for c in example:
        print("-" * 60)
        print(c.text)
