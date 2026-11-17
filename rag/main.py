"""RAG retrieval service – Week 4 of the LLMOps platform.

POST /v1/retrieve  {"question": "...", "top_k": 3, "mode": "hybrid"}
  -> the top_k chunks (source, chunk index, text, score) + time spent in each stage.

The gateway calls this service, then asks the LLM to answer from the returned chunks.
Retrieval runs in its own service so it can be scaled, versioned and evaluated
separately from generation.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, Info, generate_latest
from pydantic import BaseModel, Field

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
DOCS_DIR = Path(os.getenv("DOCS_DIR", "docs"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
LOAD_RERANKER = os.getenv("LOAD_RERANKER", "true").lower() == "true"
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "hybrid")
CANDIDATES = int(os.getenv("RETRIEVAL_CANDIDATES", "10"))
TORCH_THREADS = int(os.getenv("TORCH_THREADS", "2"))  # don't let one pod take every CPU on the node


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
                 "level": record.levelname, "msg": record.getMessage()}
        entry.update(getattr(record, "extra_fields", {}))
        return json.dumps(entry)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
log = logging.getLogger("rag")
log.handlers = [_handler]
log.setLevel(logging.INFO)
log.propagate = False


def log_event(msg, **fields):
    log.info(msg, extra={"extra_fields": fields})


RETRIEVE_SECONDS = Histogram("rag_retrieve_seconds", "Time to retrieve chunks", ["mode"],
                             buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2))
RETRIEVE_REQUESTS = Counter("rag_retrieve_requests_total", "Retrieve requests", ["mode", "status"])
BUILD = Info("rag_service_build", "Deployed versions")


@asynccontextmanager
async def lifespan(app):
    try:
        import torch
        torch.set_num_threads(TORCH_THREADS)
    except ImportError:
        pass
    from retriever import HybridRetriever

    app.state.retriever = HybridRetriever(DOCS_DIR, EMBEDDING_MODEL,
                                          RERANKER_MODEL if LOAD_RERANKER else None, CANDIDATES)
    r = app.state.retriever
    BUILD.info({"app_version": APP_VERSION, "embedding_model": EMBEDDING_MODEL,
                "reranker_model": RERANKER_MODEL if LOAD_RERANKER else "none",
                "default_mode": RETRIEVAL_MODE})
    log_event("startup", app_version=APP_VERSION, files=len(r.sources), chunks=len(r.chunks),
              load_s=round(r.load_seconds, 2), default_mode=RETRIEVAL_MODE)
    yield
    log_event("shutdown")


app = FastAPI(title="RAG Retrieval Service", version=APP_VERSION, lifespan=lifespan)


class RetrieveRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(3, ge=1, le=20)
    mode: Optional[Literal["dense", "bm25", "hybrid", "hybrid_rerank"]] = None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(request: Request):
    # The index is built before the server accepts connections, so being able to answer means ready.
    if getattr(request.app.state, "retriever", None) is None:
        raise HTTPException(status_code=503, detail="index not loaded")
    return {"status": "ready"}


@app.get("/info")
def info(request: Request):
    r = request.app.state.retriever
    return {"app_version": APP_VERSION, "default_mode": RETRIEVAL_MODE, "candidates": CANDIDATES,
            "embedding_model": EMBEDDING_MODEL, "reranker_model": RERANKER_MODEL if LOAD_RERANKER else None,
            "files": len(r.sources), "chunks": len(r.chunks), "index_load_s": round(r.load_seconds, 2),
            "sources": r.sources}


@app.post("/v1/retrieve")
def retrieve(req: RetrieveRequest, request: Request):
    # A plain (non-async) endpoint: FastAPI runs it in a thread pool, so the CPU-heavy
    # embedding work doesn't block health checks.
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    mode = req.mode or RETRIEVAL_MODE
    start = time.perf_counter()
    try:
        chunks, timings = request.app.state.retriever.search(req.question, top_k=req.top_k, mode=mode)
    except ValueError as exc:
        RETRIEVE_REQUESTS.labels(mode=mode, status="bad_request").inc()
        raise HTTPException(status_code=400, detail=str(exc))
    elapsed = time.perf_counter() - start
    RETRIEVE_SECONDS.labels(mode=mode).observe(elapsed)
    RETRIEVE_REQUESTS.labels(mode=mode, status="ok").inc()
    log_event("retrieve", request_id=rid, mode=mode, top_k=req.top_k,
              sources=[c["source"] for c in chunks], ms=round(elapsed * 1000, 1))
    return {"request_id": rid, "mode": mode, "chunks": chunks, "timings_ms": timings}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
