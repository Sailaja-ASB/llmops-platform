"""LLM Gateway - Week 1 of the LLMOps platform.

A FastAPI service that sits in front of a local LLM (Ollama) and adds:
  - a clean /v1/generate API
  - request validation
  - latency + token metrics exposed for Prometheus at /metrics
  - health checks for Kubernetes (/healthz, /readyz)
"""

import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:1.5b")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))

app = FastAPI(title="LLM Gateway", version="0.1.0")

# ---------- Prometheus metrics ----------
REQUESTS = Counter("llm_requests_total", "Total generate requests", ["status"])
LATENCY = Histogram(
    "llm_request_latency_seconds",
    "End-to-end request latency",
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
TOKENS_OUT = Counter("llm_output_tokens_total", "Total tokens generated")
TOKENS_PER_SEC = Histogram(
    "llm_tokens_per_second",
    "Generation throughput per request",
    buckets=(1, 5, 10, 20, 40, 80, 160),
)


# ---------- Schemas ----------
class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    max_tokens: int = Field(256, ge=1, le=2048)
    temperature: float = Field(0.2, ge=0.0, le=2.0)


class GenerateResponse(BaseModel):
    model: str
    output: str
    output_tokens: int
    latency_s: float
    tokens_per_s: float


# ---------- Routes ----------
@app.get("/healthz")
async def healthz():
    """Liveness: the gateway process is up."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: the model server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
        return {"status": "ready", "model": MODEL_NAME}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"model server not ready: {exc}")


@app.post("/v1/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    payload = {
        "model": MODEL_NAME,
        "prompt": req.prompt,
        "stream": False,
        "options": {"num_predict": req.max_tokens, "temperature": req.temperature},
    }
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        REQUESTS.labels(status="error").inc()
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}")

    latency = time.perf_counter() - start
    out_tokens = int(data.get("eval_count", 0))
    # Ollama reports eval_duration in nanoseconds
    eval_s = data.get("eval_duration", 0) / 1e9
    tps = out_tokens / eval_s if eval_s > 0 else 0.0

    REQUESTS.labels(status="ok").inc()
    LATENCY.observe(latency)
    TOKENS_OUT.inc(out_tokens)
    TOKENS_PER_SEC.observe(tps)

    return GenerateResponse(
        model=MODEL_NAME,
        output=data.get("response", ""),
        output_tokens=out_tokens,
        latency_s=round(latency, 3),
        tokens_per_s=round(tps, 2),
    )


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
