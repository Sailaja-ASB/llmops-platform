# LLMOps Platform

A production-style platform for serving, observing and scaling a self-hosted LLM.
Built step by step: gateway → containers → Kubernetes → autoscaling → RAG → eval-gated CI/CD → dashboards.

**Status:** Week 1 complete (LLM gateway, Docker, metrics, benchmark). Kubernetes is next.

📖 **Write-up:** [LLMOps Platform, Week 1: Building an LLM Gateway with FastAPI, Docker and Prometheus](https://medium.com/@morrennagarisailaja/llmops-platform-week-1-building-an-llm-gateway-with-fastapi-docker-and-prometheus-9e62974db1a3)

## Architecture (Week 1)

```
client ──► FastAPI gateway (Docker, :8000) ──► Ollama model server (:11434, qwen2.5:3b)
                 │
                 ├── /healthz  /readyz   (liveness & readiness probes)
                 └── /metrics            (Prometheus: latency, tokens/sec, request counts)
```

The gateway gives every app one stable API (`/v1/generate`), so the model behind it can be
swapped or scaled without changing clients. It also exposes health probes for Kubernetes
and Prometheus metrics for monitoring.

## Tech stack
Python · FastAPI · httpx · Prometheus client · Docker · Ollama · Qwen2.5-3B

## Benchmark results (Week 1)

Measured with [`benchmark.py`](benchmark.py); the first (warm-up) request is excluded.

**Setup**

| | |
|---|---|
| Hardware | MacBook Air, Apple M1 (2020), 8 GB unified memory |
| Model | `qwen2.5:3b` via Ollama (default 4-bit quantization), running natively on macOS |
| Concurrency | 1 (20 sequential requests, no parallel load) |
| Input | 5 short, one-sentence prompts, rotated |
| Output | `max_tokens=80`, temperature 0.2; mean output 40 tokens (range 19–80) |
| Latency | End-to-end, measured at the client: full non-streaming response, not time to first token |
| Throughput | Generation tokens/s from Ollama's `eval_duration` (excludes prompt processing) |

| Metric | Gateway in Docker | Gateway on host |
|---|---|---|
| p50 latency | 2.54 s | 2.36 s |
| p95 latency | 6.65 s | 7.01 s |
| Mean latency | 3.36 s | 3.31 s |
| Mean throughput | 12.8 tokens/s | 13.0 tokens/s |
| Mean output length | 40 tokens | 40 tokens |
| Cold start (model load) | 5.76 s | 1.65 s* |

\*The model was already loaded in memory from the previous run.

**Findings**
- **Output length drives tail latency.** Every request above 6 s hit the 80-token limit, while
  20-token answers finished in under 2 s. Latency ≈ output tokens ÷ throughput (80 ÷ 12.8 ≈ 6.3 s).
- **Containerizing the gateway adds no meaningful overhead.** The Docker and host numbers are
  within normal run-to-run variation, because generation happens in the model server, not the gateway.
- **Scope.** These are single-user numbers. Concurrent load (Locust) and time to first token
  with streaming are measured in Week 3.
- **Cold starts matter.** The first request after the model is unloaded is noticeably slower,
  so production deployments should keep models warm (readiness checks, minimum replicas).

## Run it yourself

**1. Model server**
```bash
ollama pull qwen2.5:3b
```

**2. Gateway (local)**
```bash
cd gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
MODEL_NAME=qwen2.5:3b uvicorn main:app --port 8000
```

**3. Gateway (Docker)**
```bash
cd gateway
docker build -t llm-gateway:0.1 .
docker run --rm -p 8001:8000 \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  -e MODEL_NAME=qwen2.5:3b llm-gateway:0.1
```

**4. Try it**
```bash
curl localhost:8001/healthz
curl -X POST localhost:8001/v1/generate -H "Content-Type: application/json" \
  -d '{"prompt": "What is Docker in one sentence?", "max_tokens": 60}'
curl -s localhost:8001/metrics | grep llm_
```
Interactive API docs: http://localhost:8001/docs

**5. Benchmark**
```bash
python3 benchmark.py --url http://localhost:8001 --n 20
```

## Roadmap
- [x] Week 1: FastAPI LLM gateway, Prometheus metrics, Docker image, latency/throughput benchmark
- [ ] Week 2: Kubernetes (kind) + Helm chart for gateway and model server
- [ ] Week 3: KEDA autoscaling + load testing with Locust
- [ ] Week 4: Plug in the RAG copilot + guardrails
- [ ] Week 5: Eval-gated CI/CD with GitHub Actions
- [ ] Week 6: Grafana dashboards + write-up

## Author
**Sailaja Morrennagari** · [Portfolio](https://sailaja-morrennagari.github.io) · [LinkedIn](https://www.linkedin.com/in/sailajamorrennagari) · [Medium](https://medium.com/@morrennagarisailaja)
