"""LLM Gateway - Week 3 of the LLMOps platform.

Week 3 adds:
  - POST /v1/generate/stream: Server-Sent Events (token-by-token streaming)
  - time to first token (TTFT) metric
  - in-flight request limit per pod (load shedding: fast 503 instead of piling up)
  - llm_inflight_requests gauge (what is running right now)

Week 2 added production reliability on top of Week 1:
  - configuration from environment (ConfigMap / Secret in Kubernetes)
  - /version endpoint with model, prompt and app versions
  - structured JSON logs with a request ID on every request
  - optional API-key authentication and per-key rate limiting
  - request-size limit
  - shared HTTP client, connect/read timeouts
  - retry ONLY on connection errors (never on slow generations)
  - simple circuit breaker when Ollama is down
  - gateway overhead measured separately from model time
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, Info,
                               generate_latest)
from pydantic import BaseModel, Field

# ---------- Configuration (env vars; set by ConfigMap / Secret) ----------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:3b")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
APP_VERSION = os.getenv("APP_VERSION", "0.3.1")
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "3"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "32768"))
CONNECT_RETRIES = int(os.getenv("CONNECT_RETRIES", "2"))
BREAKER_THRESHOLD = int(os.getenv("BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN_S = float(os.getenv("BREAKER_COOLDOWN_S", "15"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
# Max generations running at once in this pod. Extra requests get a fast 503.
MAX_INFLIGHT = int(os.getenv("MAX_INFLIGHT", "8"))
# Reuse the last Ollama readiness result for this many seconds (fewer probe calls under load)
READY_CACHE_S = float(os.getenv("READY_CACHE_S", "10"))
# Comma-separated keys. Empty = auth disabled (handy for local runs).
API_KEYS = {k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()}

PUBLIC_PATHS = {"/healthz", "/readyz", "/version", "/metrics", "/docs", "/openapi.json"}


# ---------- Structured JSON logging ----------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "extra_fields", {}))
        return json.dumps(entry)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
log = logging.getLogger("gateway")
log.handlers = [handler]
log.setLevel(logging.INFO)
log.propagate = False


def log_event(msg, **fields):
    log.info(msg, extra={"extra_fields": fields})


# ---------- Prometheus metrics ----------
REQUESTS = Counter("llm_requests_total", "Generate requests", ["status"])
LATENCY = Histogram("llm_request_latency_seconds", "End-to-end request latency",
                    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
MODEL_TIME = Histogram("llm_model_seconds", "Time spent inside Ollama",
                       buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
OVERHEAD = Histogram("llm_gateway_overhead_seconds", "Gateway time excluding the model",
                     buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1))
TOKENS_IN = Counter("llm_input_tokens_total", "Prompt tokens")
TOKENS_OUT = Counter("llm_output_tokens_total", "Generated tokens")
TOKENS_PER_SEC = Histogram("llm_tokens_per_second", "Generation throughput per request",
                           buckets=(1, 5, 10, 20, 40, 80, 160))
BREAKER_OPEN = Counter("llm_breaker_rejections_total", "Requests rejected while breaker open")
TTFT = Histogram("llm_time_to_first_token_seconds", "Time until the first streamed token",
                 buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32))
INFLIGHT = Gauge("llm_inflight_requests", "Generations running right now in this pod")
SHED = Counter("llm_load_shed_total", "Requests rejected because the pod was at MAX_INFLIGHT")
BUILD = Info("llm_gateway_build", "Deployed versions")
BUILD.info({"app_version": APP_VERSION, "model": MODEL_NAME, "prompt_version": PROMPT_VERSION})


# ---------- Circuit breaker ----------
class Breaker:
    def __init__(self, threshold, cooldown):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.opened_at = 0, None

    def allow(self):
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown:
            return True  # half-open: let one request test the model server
        return False

    def success(self):
        self.failures, self.opened_at = 0, None

    def failure(self):
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()

    @property
    def state(self):
        if self.opened_at is None:
            return "closed"
        return "half-open" if self.allow() else "open"


breaker = Breaker(BREAKER_THRESHOLD, BREAKER_COOLDOWN_S)

# ---------- Rate limiting (per API key, per pod) ----------
_windows: dict[str, list[float]] = {}


def rate_limited(key: str) -> bool:
    now = time.monotonic()
    window = [t for t in _windows.get(key, []) if now - t < 60]
    if len(window) >= RATE_LIMIT_PER_MIN:
        _windows[key] = window
        return True
    window.append(now)
    _windows[key] = window
    return False


# ---------- In-flight limit (load shedding) ----------
_inflight = 0


def try_acquire() -> bool:
    """Single-threaded event loop, so a plain counter is safe here."""
    global _inflight
    if _inflight >= MAX_INFLIGHT:
        return False
    _inflight += 1
    INFLIGHT.set(_inflight)
    return True


def release():
    global _inflight
    _inflight = max(_inflight - 1, 0)
    INFLIGHT.set(_inflight)


def overloaded():
    SHED.inc()
    REQUESTS.labels(status="overloaded").inc()
    return HTTPException(status_code=503, detail=f"gateway busy ({MAX_INFLIGHT} requests in flight), retry later")


# ---------- App + shared HTTP client ----------
@asynccontextmanager
async def lifespan(app):
    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
    app.state.client = httpx.AsyncClient(base_url=OLLAMA_URL, timeout=timeout)
    log_event("startup", model=MODEL_NAME, ollama_url=OLLAMA_URL,
              auth_enabled=bool(API_KEYS), app_version=APP_VERSION)
    yield
    await app.state.client.aclose()  # graceful shutdown: close connections
    log_event("shutdown")


app = FastAPI(title="LLM Gateway", version=APP_VERSION, lifespan=lifespan)


def error(status, detail, request_id):
    return JSONResponse(status_code=status,
                        content={"detail": detail, "request_id": request_id},
                        headers={"X-Request-ID": request_id})


@app.middleware("http")
async def edge(request: Request, call_next):
    """Request ID, size limit, auth, rate limit and access log."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    start = time.perf_counter()

    size = request.headers.get("content-length")
    if size and int(size) > MAX_BODY_BYTES:
        resp = error(413, f"request body over {MAX_BODY_BYTES} bytes", rid)
    elif API_KEYS and request.url.path not in PUBLIC_PATHS and \
            request.headers.get("X-API-Key") not in API_KEYS:
        resp = error(401, "missing or invalid API key", rid)
    elif API_KEYS and request.url.path not in PUBLIC_PATHS and \
            rate_limited(request.headers["X-API-Key"]):
        resp = error(429, f"rate limit {RATE_LIMIT_PER_MIN}/min exceeded", rid)
    else:
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid

    if request.url.path not in {"/healthz", "/readyz", "/metrics"}:
        log_event("request", request_id=rid, method=request.method, path=request.url.path,
                  status=resp.status_code, latency_ms=round((time.perf_counter() - start) * 1000, 1))
    return resp


# ---------- Schemas ----------
class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    max_tokens: int = Field(256, ge=1, le=2048)
    temperature: float = Field(0.2, ge=0.0, le=2.0)


class GenerateResponse(BaseModel):
    request_id: str
    model: str
    prompt_version: str
    output: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    model_s: float
    tokens_per_s: float


# ---------- Routes ----------
@app.get("/healthz")
async def healthz():
    """Liveness: only the gateway process. Never depends on Ollama."""
    return {"status": "ok"}


_ready = {"ok": False, "at": float("-inf"), "detail": ""}


@app.get("/readyz")
async def readyz(request: Request):
    """Readiness: can we serve right now (Ollama reachable, breaker not open)?

    The Ollama check is cached for READY_CACHE_S seconds so that, under heavy load,
    every probe doesn't add another call to a busy model server.
    """
    if not breaker.allow():
        raise HTTPException(status_code=503, detail="circuit breaker open")
    now = time.monotonic()
    if now - _ready["at"] >= READY_CACHE_S:
        try:
            r = await request.app.state.client.get("/api/tags", timeout=2)
            r.raise_for_status()
            breaker.success()  # model server is back: close the breaker
            _ready.update(ok=True, at=now, detail="")
        except httpx.HTTPError as exc:
            _ready.update(ok=False, at=now, detail=f"model server not ready: {exc}")
    if not _ready["ok"]:
        raise HTTPException(status_code=503, detail=_ready["detail"])
    return {"status": "ready", "model": MODEL_NAME}


@app.get("/version")
async def version():
    return {"app_version": APP_VERSION, "model": MODEL_NAME, "prompt_version": PROMPT_VERSION,
            "breaker": breaker.state, "max_inflight": MAX_INFLIGHT, "inflight": _inflight}


async def call_ollama(client: httpx.AsyncClient, payload: dict, rid: str) -> dict:
    """Retry only when we could not connect. A slow generation is never retried."""
    for attempt in range(CONNECT_RETRIES + 1):
        try:
            r = await client.post("/api/generate", json=payload)
            r.raise_for_status()
            breaker.success()
            return r.json()
        except httpx.ConnectError:
            breaker.failure()
            if attempt == CONNECT_RETRIES:
                raise
            backoff = 0.2 * (2 ** attempt)
            log_event("retry", request_id=rid, attempt=attempt + 1, backoff_s=backoff)
            await asyncio.sleep(backoff)


@app.post("/v1/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, request: Request):
    rid = request.state.request_id
    if not breaker.allow():
        BREAKER_OPEN.inc()
        REQUESTS.labels(status="rejected").inc()
        raise HTTPException(status_code=503, detail="model server unavailable (breaker open)")

    if not try_acquire():
        raise overloaded()
    try:
        return await _generate(req, request, rid)
    finally:
        release()


async def _generate(req: GenerateRequest, request: Request, rid: str):
    payload = {"model": MODEL_NAME, "prompt": req.prompt, "stream": False,
               "options": {"num_predict": req.max_tokens, "temperature": req.temperature}}
    start = time.perf_counter()
    try:
        data = await call_ollama(request.app.state.client, payload, rid)
    except httpx.ConnectError as exc:
        REQUESTS.labels(status="error").inc()
        raise HTTPException(status_code=503, detail=f"cannot reach model server: {exc}")
    except httpx.TimeoutException:
        breaker.failure()
        REQUESTS.labels(status="timeout").inc()
        raise HTTPException(status_code=504, detail=f"model did not answer in {REQUEST_TIMEOUT}s")
    except httpx.HTTPError as exc:
        REQUESTS.labels(status="error").inc()
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}")

    latency = time.perf_counter() - start
    model_s = data.get("total_duration", 0) / 1e9      # Ollama reports nanoseconds
    eval_s = data.get("eval_duration", 0) / 1e9
    in_tokens = int(data.get("prompt_eval_count", 0))
    out_tokens = int(data.get("eval_count", 0))
    tps = out_tokens / eval_s if eval_s > 0 else 0.0

    REQUESTS.labels(status="ok").inc()
    LATENCY.observe(latency)
    MODEL_TIME.observe(model_s)
    OVERHEAD.observe(max(latency - model_s, 0))
    TOKENS_IN.inc(in_tokens)
    TOKENS_OUT.inc(out_tokens)
    TOKENS_PER_SEC.observe(tps)
    log_event("generate", request_id=rid, model=MODEL_NAME, input_tokens=in_tokens,
              output_tokens=out_tokens, model_s=round(model_s, 3), latency_s=round(latency, 3))

    return GenerateResponse(request_id=rid, model=MODEL_NAME, prompt_version=PROMPT_VERSION,
                            output=data.get("response", ""), input_tokens=in_tokens,
                            output_tokens=out_tokens, latency_s=round(latency, 3),
                            model_s=round(model_s, 3), tokens_per_s=round(tps, 2))


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/v1/generate/stream")
async def generate_stream(req: GenerateRequest, request: Request):
    """Stream tokens as Server-Sent Events.

    Events: {"token": "..."} for each chunk, then one final
    {"done": true, "ttft_s": ..., "latency_s": ..., ...}. Errors before the
    stream starts are normal HTTP errors (503/504); errors after it starts
    arrive as {"error": "..."} because the 200 status is already sent.
    """
    rid = request.state.request_id
    if not breaker.allow():
        BREAKER_OPEN.inc()
        REQUESTS.labels(status="rejected").inc()
        raise HTTPException(status_code=503, detail="model server unavailable (breaker open)")
    if not try_acquire():
        raise overloaded()

    client = request.app.state.client
    payload = {"model": MODEL_NAME, "prompt": req.prompt, "stream": True,
               "options": {"num_predict": req.max_tokens, "temperature": req.temperature}}
    start = time.perf_counter()
    upstream = None
    for attempt in range(CONNECT_RETRIES + 1):        # connect-only retries, as in /v1/generate
        try:
            upstream = await client.send(client.build_request("POST", "/api/generate", json=payload),
                                         stream=True)
            upstream.raise_for_status()
            break
        except httpx.ConnectError as exc:
            breaker.failure()
            if attempt == CONNECT_RETRIES:
                release()
                REQUESTS.labels(status="error").inc()
                raise HTTPException(status_code=503, detail=f"cannot reach model server: {exc}")
            await asyncio.sleep(0.2 * (2 ** attempt))
        except httpx.TimeoutException:
            release()
            breaker.failure()
            REQUESTS.labels(status="timeout").inc()
            raise HTTPException(status_code=504, detail="model server timed out")
        except httpx.HTTPError as exc:
            release()
            if upstream is not None:
                await upstream.aclose()
            REQUESTS.labels(status="error").inc()
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}")
    breaker.success()

    async def events():
        ttft = None
        final = {}
        try:
            async for line in upstream.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                token = chunk.get("response", "")
                if token and ttft is None:
                    ttft = time.perf_counter() - start
                    TTFT.observe(ttft)
                if token:
                    yield sse({"token": token})
                if chunk.get("done"):
                    final = chunk
            latency = time.perf_counter() - start
            model_s = final.get("total_duration", 0) / 1e9
            eval_s = final.get("eval_duration", 0) / 1e9
            in_tokens = int(final.get("prompt_eval_count", 0))
            out_tokens = int(final.get("eval_count", 0))
            tps = out_tokens / eval_s if eval_s > 0 else 0.0
            REQUESTS.labels(status="ok").inc()
            LATENCY.observe(latency)
            MODEL_TIME.observe(model_s)
            OVERHEAD.observe(max(latency - model_s, 0))
            TOKENS_IN.inc(in_tokens)
            TOKENS_OUT.inc(out_tokens)
            TOKENS_PER_SEC.observe(tps)
            log_event("generate_stream", request_id=rid, model=MODEL_NAME, input_tokens=in_tokens,
                      output_tokens=out_tokens, ttft_s=round(ttft or 0, 3), latency_s=round(latency, 3))
            yield sse({"done": True, "request_id": rid, "model": MODEL_NAME,
                       "prompt_version": PROMPT_VERSION, "ttft_s": round(ttft or 0, 3),
                       "latency_s": round(latency, 3), "input_tokens": in_tokens,
                       "output_tokens": out_tokens, "tokens_per_s": round(tps, 2)})
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            REQUESTS.labels(status="error").inc()
            log_event("stream_error", request_id=rid, error=str(exc))
            yield sse({"error": "stream interrupted", "request_id": rid})
        finally:
            await upstream.aclose()
            release()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
