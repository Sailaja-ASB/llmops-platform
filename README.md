# LLMOps Platform

[![CI](https://github.com/Sailaja-ASB/llmops-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Sailaja-ASB/llmops-platform/actions/workflows/ci.yml)

A production-style platform for serving, scaling, grounding and guarding a self-hosted LLM,
built one week at a time on a MacBook Air (M1, 8 GB) and measured at every step.

**Status:** Weeks 1–4 complete: gateway → Kubernetes + Helm → streaming, load testing and autoscaling →
RAG with verified citations and guardrails. Now: eval-gated CI/CD (GitHub Actions); then observability.

## Highlights

| Result | Number |
|---|---|
| Hybrid retrieval (BM25 + dense, RRF) on a 47-question benchmark | **47/47 Top-1**, vs 43/47 for either method alone |
| Citation accuracy after a prompt change (rag-v1 → rag-v2) | **40/47 → 47/47**, false "I don't know" 4 → 0 |
| Red-team attacks that succeeded end to end | **0 of 12**, with 0 of 8 real questions wrongly blocked |
| Stress-test errors after fixing probes and graceful shutdown | **30% → 0%** |
| Streaming: perceived latency (time to first token, warm) | **3.3 s → 0.2 s** |

Full numbers and test setups: [BENCHMARKS.md](BENCHMARKS.md) · Why it is built this way: [DECISIONS.md](DECISIONS.md) (18 decisions)

## Architecture

```
                      Kubernetes (kind)
             ┌───────────────────────────────────────────────┐
client ──►   │  llm-gateway (FastAPI, 2+ pods, HPA)           │
NodePort     │   • API keys, rate limits, load shedding       │
:8080        │   • circuit breaker, connect-only retries      │
             │   • guardrails: input, context, output, stream │──► Ollama on the Mac
             │   • /v1/generate[/stream], /v1/ask[/stream]    │    qwen2.5:3b
             │   • /metrics (Prometheus), /healthz, /readyz   │
             │           │                                    │
             │           ▼ X-Request-ID                       │
             │  rag-service (FastAPI, ClusterIP :8001)        │
             │   • hybrid search: BM25 + MiniLM, RRF          │
             │   • optional cross-encoder reranker            │
             └───────────────────────────────────────────────┘
```

A question to `/v1/ask` is checked by the input guardrail, sent to rag-service for the top 3 chunks,
answered by the model from numbered sources only, and returned with every `[n]` citation mapped back
to a file and verified.

## Week by week

| Week | Built | Key lesson | Guide |
|---|---|---|---|
| 1 | FastAPI gateway, Prometheus metrics, Docker, p50/p95 benchmark | Output length drives tail latency; Docker added no measurable overhead | [Medium](https://medium.com/@morrennagarisailaja/llmops-platform-week-1-building-an-llm-gateway-with-fastapi-docker-and-prometheus-9e62974db1a3) |
| 2 | Kubernetes (kind) + Helm, probes, ConfigMap/Secret, API keys, rate limits, circuit breaker | Liveness must never depend on the model; gateway overhead ~50 ms | [WEEK2_GUIDE](WEEK2_GUIDE.md) |
| 3 | SSE streaming + TTFT, load shedding, Locust load tests, HPA | One Ollama instance is the bottleneck: HPA doubled pods while TTFT rose 10 s → 52 s | [WEEK3_GUIDE](WEEK3_GUIDE.md) |
| 4 | RAG service, `/v1/ask` with verified citations, layered guardrails, red team | Perfect retrieval ≠ perfect answers: measure each stage separately | [WEEK4_GUIDE](WEEK4_GUIDE.md), [Day 4](WEEK4_DAY4_GUIDE.md) |

## Continuous integration

Every push and pull request runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

| Job | What must pass |
|---|---|
| Lint, unit tests, versions | `ruff` ([ruff.toml](ruff.toml)), 32 guardrail unit tests, code version = Helm appVersion = image tag |
| Helm charts | `helm lint` and rendered manifests validated against the Kubernetes schema (`kubeconform -strict`) |
| Gateway image + smoke test | the real image starts with no model and passes [8 checks](ci/smoke_gateway.sh): health, readiness 503, version, auth 401, injection blocked 400, quoted attack allowed, validation 422, metrics |

Quality gates on retrieval and answers are added next.

## Tech stack

Python · FastAPI · httpx · Pydantic · Prometheus client · Docker · Kubernetes (kind) · Helm ·
Locust · sentence-transformers · rank-bm25 · Ollama · Qwen2.5-3B · pytest · GitHub Actions · ruff

## Repository layout

```
gateway/        FastAPI gateway, guardrails.py, unit tests (32)
rag/            retrieval service, 18-document corpus, eval scripts (retrieval, answers, red team)
helm/           charts: llm-gateway, rag-service
loadtest/       Locust load shapes (steps, spike, stress) + report.py
results/        raw load-test and eval results
ci/             version check + gateway smoke test used by GitHub Actions
BENCHMARKS.md   every measured number, with its setup
DECISIONS.md    D1–D18: what was decided and why
```

## Run it yourself

Requirements: Docker, kind, kubectl, Helm, Ollama with `qwen2.5:3b` pulled.

```bash
# 1. Cluster and images
kind create cluster --config kind-config.yaml
docker build -t llm-gateway:0.5.1 gateway/
docker build -t rag-service:0.1.0 rag/          # first build ~5-10 min (PyTorch + models)
kind load docker-image llm-gateway:0.5.1 --name llmops
kind load docker-image rag-service:0.1.0 --name llmops

# 2. Deploy
helm upgrade --install rag-service helm/rag-service -n llmops --create-namespace
helm upgrade --install llm-gateway helm/llm-gateway -n llmops --set apiKeys="sk-$(openssl rand -hex 12)"
export KEY=$(kubectl -n llmops get secret llm-gateway-secret -o jsonpath='{.data.API_KEYS}' | base64 -d)

# 3. Ask a question
curl -s -X POST localhost:8080/v1/ask -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"Why could a slow but healthy pod get restarted on a CPU-starved node?"}'

# 4. Evaluate
python3 -m pytest -q gateway/tests
kubectl -n llmops port-forward svc/rag-service 8001:8001 &
python3 rag/eval/eval_service.py --min-top1 0.95    # retrieval gate
python3 rag/eval/eval_ask.py                        # answers + citations
python3 rag/eval/eval_guardrails.py                 # red team
```

Step-by-step guides with expected output: [WEEK2](WEEK2_GUIDE.md) · [WEEK3](WEEK3_GUIDE.md) · [WEEK4](WEEK4_GUIDE.md)

## Roadmap

- [x] Week 1: FastAPI LLM gateway, Prometheus metrics, Docker image, latency/throughput benchmark
- [x] Week 2: Kubernetes (kind) + Helm, probes, API keys, rate limiting, circuit breaker
- [x] Week 3: SSE streaming, load shedding, Locust load tests, HPA (and a real probe bug fixed)
- [x] Week 4: RAG service, verified citations, layered guardrails, red-team eval
- [ ] Week 5: Eval-gated CI/CD with GitHub Actions
- [ ] Week 6: Observability: Prometheus + Grafana dashboards, tracing

## Related

The hybrid retrieval was first built and benchmarked in
[local-agentic-copilot](https://github.com/Sailaja-ASB/local-agentic-copilot) (`eval/BENCHMARK.md`).

## Author
**Sailaja Morrennagari** · [Portfolio](https://sailaja-morrennagari.github.io) · [LinkedIn](https://www.linkedin.com/in/sailajamorrennagari) · [Medium](https://medium.com/@morrennagarisailaja)
