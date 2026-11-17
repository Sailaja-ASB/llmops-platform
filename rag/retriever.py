"""Hybrid retriever used by the RAG service.

Same method as the Copilot repo (local-agentic-copilot/app/rag/search.py), which scored
47/47 Top-1 on the 47-question benchmark: 500-character chunks with 100 overlap,
all-MiniLM-L6-v2 dense search + BM25, fused with Reciprocal Rank Fusion, optional
cross-encoder reranking.

The corpus here is small (18 files, 36 chunks), so the index lives in memory and is
rebuilt at startup in about a second. A large corpus would use a vector database instead.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from fusion import reciprocal_rank_fusion, tokenize

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
MODES = ("dense", "bm25", "hybrid", "hybrid_rerank")


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        piece = text[start:start + size].strip()
        if piece:
            chunks.append(piece)
        start += size - overlap
    return chunks


def load_chunks(docs_dir: Path) -> list[dict]:
    records = []
    for path in sorted(Path(docs_dir).iterdir()):
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            for i, piece in enumerate(chunk_text(path.read_text(encoding="utf-8"))):
                records.append({"id": f"{path.stem}-chunk-{i}", "source": path.name,
                                "chunk_index": i, "text": piece})
    return records


class HybridRetriever:
    def __init__(self, docs_dir: Path, embedding_model: str, reranker_model: str | None,
                 candidates: int = 10):
        from sentence_transformers import CrossEncoder, SentenceTransformer

        t0 = time.perf_counter()
        self.chunks = load_chunks(docs_dir)
        if not self.chunks:
            raise ValueError(f"no .md/.txt documents found in {docs_dir}")
        self.candidates = candidates
        self.embedding_model = embedding_model
        self.reranker_model = reranker_model
        self.embedder = SentenceTransformer(embedding_model)
        self.reranker = CrossEncoder(reranker_model) if reranker_model else None
        texts = [c["text"] for c in self.chunks]
        self.embeddings = np.asarray(self.embedder.encode(texts, normalize_embeddings=True))
        self.bm25 = BM25Okapi([tokenize(t) for t in texts])
        self.sources = sorted({c["source"] for c in self.chunks})
        self.load_seconds = time.perf_counter() - t0

    def search(self, question: str, top_k: int = 3, mode: str = "hybrid") -> tuple[list[dict], dict]:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == "hybrid_rerank" and self.reranker is None:
            raise ValueError("reranker is not loaded (LOAD_RERANKER=false)")

        n = min(max(self.candidates, top_k), len(self.chunks))
        timings: dict[str, float] = {}
        dense_ids: list[int] = []
        bm25_ids: list[int] = []

        if mode != "bm25":
            t = time.perf_counter()
            q = np.asarray(self.embedder.encode([question], normalize_embeddings=True))[0]
            dense_ids = [int(i) for i in np.argsort(-(self.embeddings @ q))[:n]]
            timings["dense_ms"] = (time.perf_counter() - t) * 1000
        if mode != "dense":
            t = time.perf_counter()
            bm25_ids = [int(i) for i in np.argsort(-self.bm25.get_scores(tokenize(question)))[:n]]
            timings["bm25_ms"] = (time.perf_counter() - t) * 1000

        if mode == "dense":
            ranked = [(i, 1.0 / (r + 1)) for r, i in enumerate(dense_ids)]
        elif mode == "bm25":
            ranked = [(i, 1.0 / (r + 1)) for r, i in enumerate(bm25_ids)]
        else:
            ranked = reciprocal_rank_fusion([dense_ids, bm25_ids])

        if mode == "hybrid_rerank":
            t = time.perf_counter()
            cands = [i for i, _ in ranked[:n]]
            scores = self.reranker.predict([(question, self.chunks[i]["text"]) for i in cands])
            ranked = sorted(zip(cands, (float(s) for s in scores)), key=lambda p: p[1], reverse=True)
            timings["rerank_ms"] = (time.perf_counter() - t) * 1000

        timings["total_ms"] = sum(timings.values())
        dense_rank = {i: r for r, i in enumerate(dense_ids, start=1)}
        bm25_rank = {i: r for r, i in enumerate(bm25_ids, start=1)}
        results = []
        for i, score in ranked[:top_k]:
            c = self.chunks[i]
            results.append({"id": c["id"], "source": c["source"], "chunk_index": c["chunk_index"],
                            "text": c["text"], "score": round(float(score), 5),
                            "dense_rank": dense_rank.get(i), "bm25_rank": bm25_rank.get(i)})
        return results, {k: round(v, 2) for k, v in timings.items()}
