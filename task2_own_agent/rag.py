"""RAG over the "UPB Agentic SE Web Style Guide".

- load_documents(): reads style_guide/*.md and parses id, title, status, category
- chunk_document(): splits a document by headings and paragraphs into ~CHUNK_TOKENS chunks
- StyleGuideIndex: embeds all chunks once (cached on disk), search(query, k) by cosine similarity
- format_observation(): turns search results into the text the agent sees (<= 1,000 tokens)

Run `python task2_own_agent/rag.py` for a retrieval sanity check (prints the top documents for
a set of queries whose correct answer we know).
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.llm_client import count_tokens  # noqa: E402

STYLE_GUIDE_DIR = Path(__file__).resolve().parent / "style_guide"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"

CHUNK_TOKENS = 120        # target body size; short rule docs stay whole, multi-topic docs (FAQ, changelog) split
OBSERVATION_BUDGET = 1000  # hard limit from the spec: style guide tokens per search observation

# Qwen3-Embedding is trained with an instruction prefix on the query side (documents are embedded as-is).
QUERY_INSTRUCTION = (
    "Instruct: Given a question about building a website, retrieve the style guide rules that answer it\nQuery: "
)


@dataclass
class Document:
    doc_id: str          # e.g. "SG-13"
    title: str           # e.g. "Night mode palette (version 2)"
    status: str          # e.g. "ACTIVE (version 2)" or "DEPRECATED – replaced by SG-13"
    category: str
    body: str            # text without the title / status / category lines
    path: Path

    @property
    def deprecated(self) -> bool:
        return self.status.upper().startswith("DEPRECATED")

    @property
    def replaced_by(self) -> str | None:
        m = re.search(r"replaced by (SG-\d+)", self.status, re.I)
        return m.group(1).upper() if m else None


@dataclass
class Chunk:
    doc_id: str
    title: str
    status: str
    text: str            # chunk body
    chunk_no: int = 0
    score: float = 0.0
    note: str = ""      # e.g. "added because SG-12 is deprecated"

    @property
    def header(self) -> str:
        return f"[{self.doc_id} | {self.title} | Status: {self.status}]"

    @property
    def embed_text(self) -> str:
        # The header is embedded with the body so that "night mode" etc. in the title helps retrieval,
        # and so that a fragment of a long document still knows which rule it belongs to.
        return f"{self.header}\n{self.text}"


# ----------------------------------------------------------------- loading


def load_documents(directory: Path = STYLE_GUIDE_DIR) -> list[Document]:
    docs = []
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        title_line = next((l for l in lines if l.startswith("# ")), path.stem)
        m = re.match(r"#\s*(SG-\d+)\s*:\s*(.*)", title_line)
        doc_id, title = (m.group(1), m.group(2).strip()) if m else (path.stem.split("_")[0], title_line.lstrip("# "))
        status = _meta(lines, "Status") or "UNKNOWN"
        category = _meta(lines, "Category") or ""
        body = "\n".join(
            l for l in lines if l != title_line and not re.match(r"(Status|Category):", l)
        ).strip()
        docs.append(Document(doc_id, title, status, category, body, path))
    return docs


def _meta(lines: list[str], key: str) -> str | None:
    for l in lines:
        if l.startswith(f"{key}:"):
            return l.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------- chunking


def _blocks(body: str) -> list[str]:
    """Paragraph-level blocks. Fenced code blocks and lists stay in one piece."""
    blocks, current, in_code = [], [], False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        # a heading always starts a new block
        if line.startswith("#") and not in_code and current:
            blocks.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _split_long(block: str, max_tokens: int) -> list[str]:
    """Last resort for a single block above the limit: split by sentences / lines."""
    if count_tokens(block) <= max_tokens:
        return [block]
    parts = re.split(r"(?<=[.!?])\s+|\n", block)
    out, cur = [], ""
    for p in parts:
        cand = f"{cur} {p}".strip() if cur else p
        if cur and count_tokens(cand) > max_tokens:
            out.append(cur)
            cur = p
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def chunk_document(doc: Document, max_tokens: int = CHUNK_TOKENS) -> list[Chunk]:
    """Greedy merge of paragraphs up to max_tokens, with one block of overlap between chunks.

    Most style guide documents are shorter than max_tokens and become exactly one chunk, which keeps
    a rule and its exact values together. Only the few long documents (FAQ, changelog, ...) get split.
    """
    blocks = [b for blk in _blocks(doc.body) for b in _split_long(blk, max_tokens)]
    chunks: list[list[str]] = []
    cur: list[str] = []
    for b in blocks:
        if cur and count_tokens("\n\n".join(cur + [b])) > max_tokens:
            overlap = cur[-1] if count_tokens(cur[-1]) <= max_tokens // 3 else None
            # A chunk that consists only of the block we would carry over is fully repeated in the
            # next chunk; keep it attached to its successor instead (slightly oversize chunk).
            if not (len(cur) == 1 and overlap is not None):
                chunks.append(cur)
                cur = [overlap] if overlap else []
        cur.append(b)
    if cur:
        chunks.append(cur)
    return [
        Chunk(doc.doc_id, doc.title, doc.status, "\n\n".join(c), chunk_no=i) for i, c in enumerate(chunks)
    ]


# ------------------------------------------------------------------- index


class StyleGuideIndex:
    """All chunks + their normalised embeddings in a numpy matrix."""

    def __init__(
        self,
        embed_fn: Callable[[list[str]], np.ndarray],
        model_name: str = "unknown",
        directory: Path = STYLE_GUIDE_DIR,
        chunk_tokens: int = CHUNK_TOKENS,
        use_cache: bool = True,
    ) -> None:
        self.embed_fn = embed_fn
        self.docs = {d.doc_id: d for d in load_documents(directory)}
        self.chunks = [c for d in self.docs.values() for c in chunk_document(d, chunk_tokens)]
        self.matrix = self._load_or_embed(model_name, use_cache)

    @classmethod
    def from_llm(cls, llm, **kwargs) -> "StyleGuideIndex":
        return cls(embed_fn=llm.embed, model_name=llm.embedding_model or "unknown", **kwargs)

    def _load_or_embed(self, model_name: str, use_cache: bool) -> np.ndarray:
        key_src = model_name + "\n".join(c.embed_text for c in self.chunks)
        key = hashlib.sha256(key_src.encode()).hexdigest()[:16]
        cache = CACHE_DIR / f"sg_{key}.npz"
        if use_cache and cache.exists():
            return np.load(cache)["matrix"]
        vectors = np.asarray(self.embed_fn([c.embed_text for c in self.chunks]), dtype=np.float32)
        matrix = _normalise(vectors)
        if use_cache:
            CACHE_DIR.mkdir(exist_ok=True)
            np.savez(cache, matrix=matrix)
        return matrix

    def search(self, query: str, k: int = 5, add_replacements: bool = True) -> list[Chunk]:
        """Top-k chunks by cosine similarity.

        If a retrieved chunk comes from a DEPRECATED document and its replacement is not in the
        result, the best chunk of the replacement is appended (marked with a note). The deprecated
        chunk is kept on purpose: the agent should see that a rule changed, not silently miss it.
        """
        q = _normalise(np.asarray(self.embed_fn([QUERY_INSTRUCTION + query]), dtype=np.float32))[0]
        scores = self.matrix @ q
        order = np.argsort(-scores)[:k]
        results = [_with_score(self.chunks[i], float(scores[i])) for i in order]

        if add_replacements:
            present = {c.doc_id for c in results}
            for c in list(results):
                new_id = self.docs[c.doc_id].replaced_by if c.doc_id in self.docs else None
                if new_id and new_id not in present and new_id in self.docs:
                    idx = [i for i, ch in enumerate(self.chunks) if ch.doc_id == new_id]
                    best = max(idx, key=lambda i: scores[i])
                    extra = _with_score(self.chunks[best], float(scores[best]))
                    extra.note = f"added because {c.doc_id} is deprecated"
                    results.append(extra)
                    present.add(new_id)
        return results


def _with_score(chunk: Chunk, score: float) -> Chunk:
    return Chunk(chunk.doc_id, chunk.title, chunk.status, chunk.text, chunk.chunk_no, score)


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(norms, 1e-12)


# ------------------------------------------------------------- observation


def format_observation(chunks: list[Chunk], budget: int = OBSERVATION_BUDGET) -> tuple[str, int]:
    """Render chunks for the model, staying within `budget` tokens for the whole text.

    Replacement chunks for deprecated rules are placed first so they survive the budget cut.
    Chunks that don't fit are dropped (and listed by id, so the model knows they exist).
    Returns (text, tokens_used).
    """
    ordered = [c for c in chunks if c.note] + [c for c in chunks if not c.note]
    parts: list[str] = []
    dropped: list[str] = []
    for c in ordered:
        note = f" ({c.note})" if c.note else ""
        piece = f"{c.header} score={c.score:.2f}{note}\n{c.text}"
        if count_tokens("\n\n".join(parts + [piece])) <= budget:
            parts.append(piece)
        else:
            dropped.append(c.doc_id)
    if not parts:
        return "No style guide text fits the budget.", 0
    text = "\n\n".join(parts)
    used = count_tokens(text)
    if dropped:
        text += f"\n\n(Not shown, over the {budget}-token budget: {', '.join(dropped)}. Search more specifically.)"
    return text, used


# ----------------------------------------------------------- sanity check

SANITY_QUERIES = {
    "night mode background and text color": "SG-13",
    "day mode background color": "SG-11",
    "accent color": "SG-14",
    "how to store the selected theme": "SG-21",
    "theme toggle button": "SG-22",
    "footer text": "SG-40",
    "mobile breakpoint width": "SG-50",
    "font family": "SG-51",
    "page title format": "SG-02",
    "member card markup": "SG-30",
    "hot take badge": "SG-31",
    "animations": "SG-55",
}


def _sanity_check(k: int = 5) -> None:
    from common.llm_client import LLMClient

    llm = LLMClient(log_path=Path(__file__).resolve().parent / "logs" / "rag_sanity.jsonl")
    index = StyleGuideIndex.from_llm(llm)
    sizes = [count_tokens(c.text) for c in index.chunks]
    print(f"{len(index.docs)} documents -> {len(index.chunks)} chunks "
          f"(tokens per chunk: min {min(sizes)}, mean {np.mean(sizes):.0f}, max {max(sizes)})\n")
    hits = 0
    for query, expected in SANITY_QUERIES.items():
        res = index.search(query, k)
        ids = [c.doc_id for c in res]
        ok = expected in ids
        hits += ok
        _, used = format_observation(res)
        print(f"{'OK  ' if ok else 'MISS'} {query!r:45} expected {expected:6} got {ids}  ({used} tok)")
    print(f"\n{hits}/{len(SANITY_QUERIES)} queries return the expected (active) document in the top {k}")
    print(llm.usage_summary())


if __name__ == "__main__":
    _sanity_check()
