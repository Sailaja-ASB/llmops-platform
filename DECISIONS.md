# Technical decisions

Short records of why the platform is built the way it is. Newest first.

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
