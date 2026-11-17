# Technical decisions

Short records of why the platform is built the way it is. Newest first.

## D9 – Lenient liveness, cached readiness, long grace period (Week 3 fix)
**Decision:** liveness timeout 5 s / 6 failures; readiness timeout 3 s with a 10 s cached Ollama
check; 90 s termination grace, 60 s uvicorn graceful shutdown and a 5 s preStop sleep; HPA waits
3 minutes before scaling down.
**Why:** in the 20-user stress test, 1 s probe timeouts on a CPU-starved Mac made Kubernetes restart
pods that were slow but healthy, and an HPA scale-down killed a pod mid-stream. Both cut active
answers (HTTP 0 / 500). A probe should only restart a pod that is truly stuck, and shutdown must
outlast the longest stream.

## D8 – CPU-based HPA first, KEDA later (Week 3)
**Decision:** start with a CPU HPA (target 50%, 2–5 pods) and measure whether it reacts.
**Why:** it's the standard first step, but an LLM gateway mostly *waits* on the model, so CPU may
stay low under heavy load. If so, scale on in-flight requests or queue depth (KEDA) instead.

## D7 – Load shedding with MAX_INFLIGHT (Week 3)
**Decision:** each pod runs at most MAX_INFLIGHT generations; extra requests get a fast 503.
**Why:** an unbounded queue turns overload into minutes of waiting for everyone. A fast,
clear error lets clients retry or fall back, and keeps latency predictable for accepted requests.

## D6 – Stream tokens with Server-Sent Events (Week 3)
**Decision:** add POST /v1/generate/stream using SSE; keep /v1/generate for simple clients.
**Why:** time to first token is what users feel. SSE is plain HTTP, works through proxies and
is the same pattern most LLM APIs use. Errors after the stream starts are sent as an event,
because the 200 status is already on the wire.

## D5 – Retry only on connection errors (Week 2)
**Decision:** retry up to 2 times with exponential backoff only when the gateway cannot connect to Ollama.
**Why:** retrying a slow generation doubles load exactly when the model server is struggling.

## D4 – Circuit breaker in front of Ollama (Week 2)
**Decision:** after 3 connection failures, reject requests with a fast 503 for 15 s, then let one request test recovery.
**Why:** fail fast instead of making every user wait for timeouts during an outage.

## D3 – /healthz never depends on Ollama (Week 2)
**Decision:** liveness checks only the gateway process; readiness checks Ollama.
**Why:** a model-server outage should stop traffic to the pods, not restart healthy pods in a loop.

## D2 – Ollama runs on the host, outside Kubernetes (Week 2)
**Decision:** only the gateway runs in the kind cluster; pods reach Ollama via host.docker.internal.
**Why:** an 8 GB MacBook Air cannot comfortably run kind, the model and monitoring together. Scaling gateway pods does not scale Ollama, which stays the bottleneck (measured in Week 3).

## D1 – A gateway in front of the model (Week 1)
**Decision:** clients call a FastAPI gateway, never Ollama directly.
**Why:** one stable API, health probes, metrics and a single place for auth, limits and guardrails.
